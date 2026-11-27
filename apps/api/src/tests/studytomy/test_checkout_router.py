import asyncio
import hashlib
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader, PdfWriter
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.events.database import get_db_session
from src.db.courses.certifications import CertificateUser, Certifications
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.capacity import StudyCapacityHold
from src.db.studytomy.collections import (
    StudyChargebackAdjustment,
    StudyChargebackReversal,
    StudyCollectionRemedy,
    StudyManualCollectionEvidence,
    StudyMoneyMovement,
    StudyPaymentAttempt,
    StudyRefundReservation,
    StudyRefundSettlement,
)
from src.db.studytomy.commerce import StudyOffer, StudyOfferTerms, StudyOfferVersion
from src.db.studytomy.education import StudyCourseRelease
from src.db.studytomy.family import (
    StudyEligibilityDecision,
    StudyLearnerProfile,
    StudyRelationship,
)
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyKey,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.studytomy.orders import StudyOrder, StudyOrderAcceptance, StudyOrderLine
from src.db.trail_runs import StatusEnum, TrailRun
from src.db.trails import Trail
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.router import v1_router
from src.routers.studytomy.commerce import router
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.studytomy_access import StudytomyCapabilityName
from src.services.auth.session import mint_session_tokens
from src.services.studytomy import orders as orders_service
from src.services.studytomy.collections import (
    ManualBankEvidence,
    allocate_confirmed_order,
    attest_manual_bank_evidence,
    confirm_manual_collection,
    record_manual_collection_attempt,
)
from src.services.studytomy.offers import _terms_snapshot
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.services.studytomy.refunds import (
    RefundReservationRequest,
    reserve_manual_refund,
)
from src.services.studytomy.releases import load_release_policy_binding
from src.services.utils import upload_content
from src.tests.conftest import USER_RIGHTS
from src.tests.studytomy.test_content_evidence import _seed_postgres_release_sources
from src.tests.studytomy.test_studytomy_access import (
    _grant_role_capabilities,
    _seed_active_owner,
    _seed_approved_policy,
    _seed_staff_scope,
    _synthetic_policy_body,
)


async def test_checkout_rejects_client_price_and_missing_pilot_approval(db, org, regular_user):
    app = FastAPI()
    app.include_router(router)

    async def session():
        yield db

    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_authenticated_non_api_token_user] = lambda: regular_user
    selection = {
        "offer_uuid": str(uuid4()), "offer_version_uuid": str(uuid4()),
        "learner_uuids": [str(uuid4())],
    }
    confirmation = {**selection, "confirmation_sha256": "a" * 64, "acknowledged_overlap": False}
    prefix = f"/orgs/{org.org_uuid}/checkout"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(f"{prefix}/quote", json={**selection, "total_minor": 1})
        assert rejected.status_code == 422
        for path, body in (("quote", selection), ("orders", confirmation)):
            blocked = await client.post(
                f"{prefix}/{path}", json=body, headers={"Idempotency-Key": "synthetic-checkout"},
            )
            assert blocked.status_code == 409
            assert blocked.headers["cache-control"] == "private, no-store"
            assert blocked.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
        absent = await client.post(f"{prefix}/orders/{uuid4()}/accept", json=confirmation)
        assert absent.status_code == 404
        assert absent.json()["detail"]["code"] == "ORDER_DRAFT_UNAVAILABLE"
        purchases = await client.get(f"/orgs/{org.org_uuid}/purchases")
        assert purchases.status_code == 409
        assert purchases.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
        assert purchases.headers["cache-control"] == "private, no-store"
        offers = await client.get(f"{prefix}/offers")
        assert offers.status_code == 409
        assert offers.headers["cache-control"] == "private, no-store"
        assert offers.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
        learners = await client.get(f"{prefix}/learners")
        assert learners.status_code == 200
        assert learners.headers["cache-control"] == "private, no-store"
        assert learners.json() == []
    assert (await db.execute(select(func.count()).select_from(StudyOrder))).scalar_one() == 0


@pytest.mark.studytomy_postgres
@pytest.mark.parametrize(
    "price_minor, free_approved, delay_remedy_policy, overlap_before_settlement, correction_scenario",
    [(125, False, False, False, None), (125, False, False, True, None), (0, False, False, False, None),
     (0, True, False, False, None), (0, True, True, False, None), (0, True, False, False, "retry"),
     (0, True, False, False, "race"), (0, True, False, False, "receipt_scope_absent"),
     (125, False, False, False, "settled_refund"), (0, True, False, False, "reentry"),
     (0, True, False, False, "media_delivery"), (0, True, False, False, "stream_revocation")],
    ids=["paid", "paid-chargeback-first", "free-unapproved", "free-approved", "free-rollback-and-retry",
         "free-correction", "free-correction-first-open-race", "free-correction-receipt-scope-absent",
         "paid-correction-settled-refund", "free-reentry", "free-release-media", "free-release-stream-revocation"],
)
async def test_signed_checkout_accepts_only_retained_approved_terms(
    postgres_session_factory, price_minor, free_approved, delay_remedy_policy, overlap_before_settlement, correction_scenario,
    tmp_path, monkeypatch,
):
    media_scenario = correction_scenario in ("media_delivery", "stream_revocation")
    correct_before_first_use = correction_scenario not in (None, "reentry")
    token = uuid4().hex
    offer_uuid, version_uuid = str(uuid4()), str(uuid4())
    org = Organization(
        name="Synthetic checkout", slug=token, org_uuid=f"org_{token}", email=f"{token}@example.invalid",
    )
    actors = [
        User(
            username=f"{token}-{name}", first_name=name, last_name="Fixture",
            email=f"{token}-{name}@example.invalid", user_uuid=f"user_{uuid4()}",
        )
        for name in ("Purchaser", "Owner", "SecondApprover", "SettlementVerifier")
    ]
    purchaser, owner, second_approver, verifier = actors
    async with postgres_session_factory() as db:
        db.add_all([org, *actors])
        await db.flush()
        role = Role(
            name="Fixture", org_id=org.id, role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            role_uuid=f"role_{uuid4()}", rights=USER_RIGHTS.model_dump(),
        )
        db.add(role)
        await db.flush()
        db.add_all([
            UserOrganization(org_id=org.id, user_id=actor.id, role_id=role.id, creation_date="", update_date="")
            for actor in actors
        ])
        profile = StudyLearnerProfile(
            org_id=org.id, user_id=purchaser.id, created_by_user_id=owner.id,
            status="active", control_mode="independent",
        )
        db.add(profile)
        activity_uuid = f"activity_{uuid4()}"
        assessment_uuid = f"activity_{uuid4()}"
        private_draft_uuid = f"activity_{uuid4()}"
        media_activity_uuid = f"activity_{uuid4()}"
        video_activity_uuid = f"activity_{uuid4()}"
        unsupported_media_uuid = f"activity_{uuid4()}"
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=420)
        writer.add_blank_page(width=420, height=300)
        if correction_scenario == "stream_revocation":
            writer.add_metadata({"/Subject": "x" * (64 * 1024)})
        with BytesIO() as pdf:
            writer.write(pdf)
            release_media_bytes = pdf.getvalue()
        release_video_bytes = b"synthetic approved video bytes"
        media_source_reference = (
            f"courses/course_synthetic/activities/{media_activity_uuid}/documentpdf/lesson.pdf"
        )
        media_storage_reference = f"studytomy/releases/{token}/lesson.bin"
        video_source_reference = (
            f"courses/course_synthetic/activities/{video_activity_uuid}/video/video.mp4"
        )
        video_storage_reference = f"studytomy/releases/{token}/video.bin"
        caption_source_reference = video_source_reference.rsplit("/", 1)[0] + "/captions/en.vtt"
        caption_storage_reference = f"studytomy/releases/{token}/caption.bin"
        caption_bytes = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nCaption\n"
        release_media_manifest = {
            media_source_reference: {
                "sha256": hashlib.sha256(release_media_bytes).hexdigest(),
                "size_bytes": len(release_media_bytes),
                "media_type": "application/pdf",
                "storage_reference": media_storage_reference,
            },
            video_source_reference: {
                "sha256": hashlib.sha256(release_video_bytes).hexdigest(),
                "size_bytes": len(release_video_bytes),
                "media_type": "video/mp4",
                "storage_reference": video_storage_reference,
            },
            caption_source_reference: {
                "sha256": hashlib.sha256(caption_bytes).hexdigest(),
                "size_bytes": len(caption_bytes),
                "media_type": "text/vtt",
                "storage_reference": caption_storage_reference,
            },
        }
        retained_activities = [
            {
                "id": 1, "activity_uuid": activity_uuid, "published": True,
                "activity_type": "TYPE_DYNAMIC", "activity_sub_type": "SUBTYPE_DYNAMIC_PAGE",
                "name": "Reviewed lesson", "lock_type": "public",
                "content": {"type": "doc", "content": [{
                    "type": "paragraph", "content": [{"type": "text", "text": "Retained lesson text."}],
                }]},
            }, {
                "id": 2, "activity_uuid": assessment_uuid, "published": True,
                "activity_type": "TYPE_ASSIGNMENT", "activity_sub_type": "SUBTYPE_ASSIGNMENT_ANY",
                "name": "Private assessment", "lock_type": "public",
                "content": {"answer": "synthetic answer key"},
            }, {
                "id": 6, "activity_uuid": private_draft_uuid, "published": False,
                "activity_type": "TYPE_DYNAMIC", "activity_sub_type": "SUBTYPE_DYNAMIC_PAGE",
                "name": "Private managed draft", "lock_type": "public",
                "content": {"answer": "synthetic private draft content"},
            },
        ]
        if media_scenario:
            retained_activities.append({
                "id": 3, "activity_uuid": media_activity_uuid, "published": True,
                "activity_type": "TYPE_DOCUMENT", "activity_sub_type": "SUBTYPE_DOCUMENT_PDF",
                "name": "Reviewed PDF", "lock_type": "public", "content": {"filename": "lesson.pdf"},
            })
            retained_activities.extend([
                {
                    "id": 4, "activity_uuid": video_activity_uuid, "published": True,
                    "activity_type": "TYPE_VIDEO", "activity_sub_type": "SUBTYPE_VIDEO_HOSTED",
                    "name": "Reviewed video", "lock_type": "public", "content": {"filename": "video.mp4"},
                    "extra_metadata": {"captions": {"enabled": True, "status": "ready", "languages": [
                        {"code": "en", "label": "English", "status": "ready"},
                    ]}},
                },
                {
                    "id": 5, "activity_uuid": unsupported_media_uuid, "published": True,
                    "activity_type": "TYPE_AUDIO", "activity_sub_type": "SUBTYPE_AUDIO",
                    "name": "Unsupported audio", "lock_type": "public", "content": {"filename": "audio.mp3"},
                },
            ])
        course = Course(
            org_id=org.id, course_uuid="course_synthetic", name="Synthetic course",
            public=False, published=False, open_to_contributors=False, studytomy_managed=True,
        )
        db.add(course)
        await db.flush()
        retained = {
            "schema": "course_content_evidence_v4",
            "course": {"name": course.name, "course_uuid": course.course_uuid, "id": course.id, "org_id": org.id},
            "activities": retained_activities,
            "chapters": [{"id": 1, "lock_type": "public"}],
            "chapter_order": [[1, 1]],
            "activity_order": [
                [1, 1, 1], [1, 2, 2],
                *([[1, 3, 3], [1, 4, 4], [1, 5, 5]] if media_scenario else []),
                [1, 6, 6],
            ],
        }
        review, snapshot, release_policies = await _seed_postgres_release_sources(
            db, org, owner.id, token, snapshot=retained,
        )
        release = StudyCourseRelease(
            org_id=org.id, course_uuid=course.course_uuid, review_id=review.id,
            snapshot_sha256=review.revision_sha256, snapshot=snapshot,
            media_manifest=release_media_manifest if media_scenario else {},
            assessment_policy_id=release_policies[0].id, progression_policy_id=release_policies[1].id,
            retention_policy_id=release_policies[2].id, rights_policy_id=release_policies[3].id,
            retention_category="synthetic",
        )
        first_use = delay_remedy_policy or correct_before_first_use
        terms = StudyOfferTerms.model_validate({
            "title": "Synthetic course", "goals": ["Practice"], "audience": "Synthetic fixture",
            "formats": ["text"], "prerequisites": [], "accessibility_notes": [],
            "effort_minutes_min": 5, "effort_minutes_max": 10, "included_services": [],
            "capacity_basis": "Fixture", "capacity_is_limited": True, "capacity_limit": 1,
            "access": {
                "clock": "after_first_access" if first_use else "after_purchase", "duration_days": 1,
            }, "assessment": {"method": "none"},
            "award": {"kind": "none"}, "customer_terms": "Synthetic customer terms.",
            "price": {"currency": "AUD", "total_minor": price_minor,
                      "lines": [{"kind": "charge", "label": "Synthetic", "amount_minor": price_minor}]},
            "resources": [{
                "kind": "course", "resource_uuid": course.course_uuid, "title_snapshot": course.name,
                "release_uuid": release.uuid, "release_sha256": release.snapshot_sha256,
            }],
        })
        assert not terms.completion_blockers()
        data, digest = _terms_snapshot(terms, "course")
        offer = StudyOffer(org_id=org.id, offer_uuid=offer_uuid, offer_kind="course", created_by_user_id=owner.id)
        db.add_all([course, offer])
        await db.flush()
        db.add(StudyOfferVersion(
            offer_id=offer.id, version_no=1, version_uuid=version_uuid,
            terms=data, content_sha256=digest, created_by_user_id=owner.id,
        ))
        retention = release_policies[2]
        retention_rules = {"course_release_retention_category": "synthetic"}
        record_access = {"fixture": True}
        if correction_scenario == "reentry":
            retention_rules["course_completion_days"] = 7
            retention_rules["record_request_evidence_retention_category"] = "synthetic"
            retention_rules["case_evidence_holds"] = {
                "reason_codes": ["synthetic_review"], "max_review_days": 5,
                "release_reason_codes": ["synthetic_review_complete"],
            }
            retention_rules["case_evidence_retention_days"] = 5
            record_access["self_course_completion"] = True
            privacy_owner = await _seed_active_owner(db, org, owner.id, purchaser.id, StudyOwnerFunction.PRIVACY)
            record_access["self_learning_record_request"] = {
                "due_days": 7, "privacy_owner_assignment_uuid": privacy_owner.owner_uuid,
                "learner_status_enabled": True,
                "evidence_upload_enabled": True,
                "completion_delivery_days": 2,
                "decision_reason_codes": {
                    "approved_for_preparation": ["verified_self_request"], "denied": ["identity_unverified"],
                },
            }
        retention.body = _synthetic_policy_body(StudyPolicyKey.D_13, {
            "retention": retention_rules,
            "record_access": record_access,
            **({"removal": {"case_evidence_approval_reason_codes": ["synthetic_expired"]}}
               if correction_scenario == "reentry" else {}),
        }).model_dump(mode="json")
        for row in release_policies:
            if row is not retention:
                specified = {"release_schedule": {
                    "ungated_text_activities": {
                        course.course_uuid: [activity_uuid, assessment_uuid, private_draft_uuid],
                    },
                    **({"ungated_media_activities": {
                        course.course_uuid: [media_activity_uuid, video_activity_uuid, unsupported_media_uuid],
                    }} if media_scenario else {}),
                }} if row.policy_key == StudyPolicyKey.D_09.value else None
                row.body = _synthetic_policy_body(StudyPolicyKey(row.policy_key), specified).model_dump(mode="json")
            row.effective_from = datetime.now(UTC)
        decisions = {
            StudyPolicyKey.D_01: {"independent_participation": {"fixture": True}},
            StudyPolicyKey.D_02: {},
            StudyPolicyKey.D_03: {"adult_transition": {"fixture": True}, "guardian_authority": {"fixture": True}},
            StudyPolicyKey.D_11: {},
            StudyPolicyKey.D_12: {
                "parent_report_scope": ["purchase_allocation"],
                "information_uses": {
                    "learning_terms": ["course_release"],
                    "purchase_receipt": (["order_summary"] if correction_scenario == "receipt_scope_absent"
                                         else ["order_summary", "allocation_correction_status"]),
                    "allocation_correction": ["first_content_delivery"],
                    "learning_access_history": ["access_window", "course_completion"],
                    "record_request": ["self_learning_records", "self_learning_request_status", "staff_review_evidence",
                                       "self_course_completion_delivery"],
                },
                "consent": {
                    "notice_versions": {"learning_terms": "fixture-v1"},
                    "notice_texts": {"learning_terms": "Synthetic course release consent."},
                },
            },
            StudyPolicyKey.D_20: {},
            StudyPolicyKey.D_04: {"staffing": {"offer_place_limits": {offer_uuid: 1}}},
            StudyPolicyKey.D_05: {
                "prices": {offer_uuid: data["price"]},
                "learner_allocation": {
                    "substantive_use_rule": "first_content_delivery",
                    "correction_reason_codes": ["wrong_learner", "other_reviewed_reason"],
                    "offer_hold_minutes": {offer_uuid: 5},
                    "order_line_offer_versions": {offer_uuid: version_uuid},
                },
                "access_periods": {offer_uuid: data["access"]},
            },
            StudyPolicyKey.D_07: {"refunds": {
                "manual_reservation_reasons": ["synthetic_refund"],
                "manual_settlement_access_effect": "end_fully_returned_order_access",
            }, "disputes": {
                "manual_chargeback_access_effect": "preserve",
                "manual_chargeback_reversal_access_effect": "preserve",
                "manual_overlap_acknowledgement_enabled": True,
            }, "withdrawal_remedies": {
                "manual_collection_outcomes": ["returned_externally"],
            }},
            StudyPolicyKey.D_17: {"pilot_size": {
                "order_acceptance_offer_versions": {offer_uuid: version_uuid},
                "offer_place_limits": {offer_uuid: 1},
            }},
            StudyPolicyKey.D_18: {
                "customer_terms": {offer_uuid: terms.customer_terms},
                "selected_market_requirements": {
                    "fixture": True, "case_evidence_holds_enabled": correction_scenario == "reentry",
                    "case_evidence_dry_run_enabled": correction_scenario == "reentry",
                    "case_evidence_hold_release_enabled": correction_scenario == "reentry",
                    "case_evidence_removal_approval_enabled": correction_scenario == "reentry",
                    "case_evidence_local_execution_enabled": correction_scenario == "reentry",
                    "case_evidence_local_restore_replay_enabled": correction_scenario == "reentry",
                },
            },
            StudyPolicyKey.D_19: {"refund_limits": {"max_amount_minor": 125, "currency": "AUD"}, "staff_authority": {
                "manual_collection_enabled": True, "manual_collection_limits_minor": {"AUD": 200},
                "manual_remedy_resolution_enabled": True,
                "manual_refund_reservation_enabled": True,
                "refund_dual_approval_required": True,
                "manual_refund_settlement_enabled": True,
                "manual_refund_settlement_independent_verifier": True,
                "manual_chargeback_attestation_enabled": True,
                "manual_chargeback_reversal_enabled": True,
            }},
        }
        policies = {StudyPolicyKey(row.policy_key): row for row in release_policies}
        track_delivery = not free_approved or first_use
        history_approved = not free_approved or not delay_remedy_policy
        if not track_delivery:
            del decisions[StudyPolicyKey.D_12]["information_uses"]["allocation_correction"]
        if not history_approved:
            del decisions[StudyPolicyKey.D_12]["information_uses"]["learning_access_history"]
        if free_approved:
            decisions[StudyPolicyKey.D_05]["learner_allocation"]["free_order_offer_versions"] = {
                offer_uuid: version_uuid,
            }
        if correction_scenario == "reentry":
            decisions[StudyPolicyKey.D_15] = {
                "supported_formats": {"max_bytes": 1024, "media_types": ["application/pdf"]},
            }
        elif media_scenario:
            decisions[StudyPolicyKey.D_15] = {
                "supported_formats": {
                    "max_bytes": 128 * 1024 if correction_scenario == "stream_revocation" else 1024,
                    "media_types": ["application/pdf", "video/mp4", "text/vtt"],
                },
            }
        if price_minor > 0:
            support_lead = await _seed_active_owner(db, org, owner.id, purchaser.id, StudyOwnerFunction.SUPPORT)
            support_backup = await _seed_active_owner(
                db, org, verifier.id, purchaser.id, StudyOwnerFunction.SUPPORT,
            )
            decisions[StudyPolicyKey.D_04]["staffing"]["support"] = {
                "lead_owner_assignment_uuid": support_lead.owner_uuid,
                "backup_owner_assignment_uuid": support_backup.owner_uuid,
            }
            decisions[StudyPolicyKey.D_04]["support_routes"] = {"public_support": {
                "categories": ["Orders"], "contact_instructions": "Synthetic support contact",
                "service_limits": "Synthetic support limits",
            }}
            decisions[StudyPolicyKey.D_04]["monitored_hours"] = {"public_support": "Synthetic hours"}
            decisions[StudyPolicyKey.D_04]["response_targets"] = {"public_support": "Synthetic review expectations"}
            decisions.setdefault(StudyPolicyKey.D_15, {})
        for key, specified in decisions.items():
            if delay_remedy_policy and key == StudyPolicyKey.D_07:
                continue
            policies[key] = await _seed_approved_policy(db, org, owner.id, key, specified=specified)
        owners = {}
        if price_minor > 0:
            owners[StudyOwnerFunction.SUPPORT] = support_lead
        for key, policy in policies.items():
            for function in POLICY_REQUIRED_RESPONSIBILITIES[key]:
                if function not in owners:
                    delegation = {
                        "financial_limit_minor": 125, "financial_limit_currency": "AUD",
                        "delegation_evidence_reference": "SYNTHETIC",
                        "delegation_policy_version_id": policies[StudyPolicyKey.D_19].id,
                    } if function == StudyOwnerFunction.COMMERCIAL else {}
                    owners[function] = await _seed_active_owner(db, org, owner.id, purchaser.id, function, **delegation)
                db.add(StudyPolicyApproval(
                    org_id=org.id, policy_version_id=policy.id, responsibility=function.value,
                    owner_assignment_id=owners[function].id, approver_user_id=owner.id,
                    evidence_reference="SYNTHETIC", rationale="Synthetic fixture", approved_at=datetime.now(UTC),
                ))
        now = datetime.now(UTC)
        for kind, keys in (
            ("learning", (StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_12)),
            ("independent_participation", (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_12)),
            ("purchase", (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_05, StudyPolicyKey.D_12)),
        ):
            db.add(StudyEligibilityDecision(
                org_id=org.id, learner_profile_id=profile.id, eligibility_kind=kind, outcome="eligible",
                policy_version_ids=[policies[key].id for key in keys], evidence_reference="SYNTHETIC",
                effective_from=now, reviewer_user_id=owner.id,
                owner_assignment_id=owners[StudyOwnerFunction.EDUCATION].id,
            ))
        await db.flush()
        release.policy_binding = (await load_release_policy_binding(db, org, now)).model_dump(mode="json")
        db.add(release)
        if correction_scenario == "reentry":
            previous_end = now - timedelta(days=1)
            previous_completion = (now - timedelta(days=2)).isoformat()
            trail = Trail(org_id=org.id, user_id=purchaser.id)
            db.add(trail)
            await db.flush()
            run = TrailRun(
                org_id=org.id, user_id=purchaser.id, course_id=course.id, trail_id=trail.id,
                status=StatusEnum.STATUS_COMPLETED, creation_date=previous_completion, update_date=previous_completion,
            )
            previous_grant = StudyCourseAccessGrant(
                org_id=org.id, course_uuid=course.course_uuid, release_id=release.id, learner_profile_id=profile.id,
                source_kind="free", source_uuid=f"earlier-{token}", start_basis="fixed_period",
                starts_at=now - timedelta(days=3), ends_at=previous_end, created_at=now - timedelta(days=3),
            )
            db.add_all([run, previous_grant])
        await db.commit()

    async def session():
        async with postgres_session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(v1_router)
    app.dependency_overrides[get_db_session] = session
    access = mint_session_tokens(purchaser.email, amr="mfa", org_id=org.id).access_token
    headers = {"Authorization": f"Bearer {access}", "Idempotency-Key": "synthetic-checkout"}
    selection = {"offer_uuid": offer_uuid, "offer_version_uuid": version_uuid, "learner_uuids": [profile.learner_uuid]}
    prefix = f"/api/v1/studytomy/orgs/{org.org_uuid}/checkout"
    purchase_path = f"/api/v1/studytomy/orgs/{org.org_uuid}/purchases"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        checkout_offers = await client.get(f"{prefix}/offers")
        assert checkout_offers.status_code == 200, checkout_offers.text
        assert checkout_offers.headers["cache-control"] == "private, no-store"
        # The exact D-17 allowlist is insufficient without current F07 readiness.
        assert checkout_offers.json() == []

        async def synthetic_ready_course_offer(_db, _org, _terms):
            # Later assertions exercise settlement mechanics under an explicitly
            # simulated readiness result; the real predicate was checked above.
            return True

        monkeypatch.setattr(orders_service, "_course_offer_ready_for_sale", synthetic_ready_course_offer)
        checkout_learners = await client.get(f"{prefix}/learners")
        assert checkout_learners.status_code == 200, checkout_learners.text
        assert checkout_learners.headers["cache-control"] == "private, no-store"
        assert checkout_learners.json() == [{
            "learner_uuid": profile.learner_uuid,
            "label": "Purchaser Fixture",
        }]
        quote = await client.post(f"{prefix}/quote", json=selection)
        if price_minor == 0 and not free_approved:
            assert quote.status_code == 409, quote.text
            assert quote.json()["detail"]["code"] == "FREE_ORDER_NOT_APPROVED"
            assert (await client.get(purchase_path)).json() == {"orders": [], "next_cursor": None}
            return
        assert quote.status_code == 200, quote.text
        assert quote.json()["total_minor"] == price_minor
        if correction_scenario == "reentry":
            assert quote.json()["learners_with_overlap"] == []
        confirmation = {**selection, "confirmation_sha256": quote.json()["confirmation_sha256"],
                        "acknowledged_overlap": False}
        draft = await client.post(f"{prefix}/orders", json=confirmation)
        assert draft.status_code == 201, draft.text
        ref = draft.json()["customer_ref"]
        assert (await client.get(purchase_path)).json() == {"orders": [], "next_cursor": None}
        if delay_remedy_policy:
            failed = await client.post(f"{prefix}/orders/{ref}/accept", json=confirmation)
            assert failed.status_code == 409, failed.text
            assert failed.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
            assert (await client.get(purchase_path)).json() == {"orders": [], "next_cursor": None}
            async with postgres_session_factory() as db:
                retained_draft = (await db.execute(select(StudyOrder).where(
                    StudyOrder.customer_ref == ref,
                ))).scalar_one()
                assert await db.get(StudyOrderAcceptance, retained_draft.id) is None
                assert (await db.execute(select(StudyCapacityHold.id).where(
                    StudyCapacityHold.request_uuid == ref,
                ))).first() is None
                assert (await db.execute(select(StudyCourseAccessGrant.id).where(
                    StudyCourseAccessGrant.org_id == org.id,
                ))).first() is None
                key = StudyPolicyKey.D_07
                remedy = await _seed_approved_policy(db, org, owner.id, key, specified=decisions[key])
                for function in POLICY_REQUIRED_RESPONSIBILITIES[key]:
                    assignment = owners.get(function)
                    if assignment is None:
                        assignment = await _seed_active_owner(db, org, owner.id, purchaser.id, function)
                    db.add(StudyPolicyApproval(
                        org_id=org.id, policy_version_id=remedy.id, responsibility=function.value,
                        owner_assignment_id=assignment.id, approver_user_id=owner.id,
                        evidence_reference="SYNTHETIC", rationale="Synthetic fixture", approved_at=datetime.now(UTC),
                    ))
                await db.commit()
        if correction_scenario == "reentry":
            org_path = f"/api/v1/studytomy/orgs/{org.org_uuid}"
            release_path = f"{org_path}/learning/courses/{course.course_uuid}/releases/{release.uuid}"
            reference = str(uuid4())
            record_path = f"{org_path}/learners/me/record-requests/{reference}"
            direct = mint_session_tokens(purchaser.email, amr="password", org_id=org.id).access_token
            direct_headers = {"Authorization": f"Bearer {direct}"}
            availability = f"{org_path}/learners/me/record-requests/availability"
            allowed = await client.get(availability, headers=direct_headers)
            assert allowed.status_code == 204 and not allowed.content
            assert allowed.headers["cache-control"] == "private, no-store"
            async with postgres_session_factory() as db:
                assert (await db.execute(select(StudyBusinessEvent.id).where(
                    StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == "studytomy.record_request.submitted",
                ))).first() is None
            other = mint_session_tokens(owner.email, amr="password", org_id=org.id).access_token
            outsider = await client.put(record_path, headers={"Authorization": f"Bearer {other}"})
            assert outsider.status_code == 404
            dispatched = await asyncio.gather(*[client.put(record_path, headers=direct_headers) for _ in range(2)])
            first, repeat = dispatched
            assert first.status_code == repeat.status_code == 200, (first.text, repeat.text)
            assert first.json() == repeat.json() and first.json()["request_reference"] == reference
            assert first.headers["cache-control"] == "private, no-store"
            assert set(first.json()) == {"request_reference", "received_at", "due_at"}
            pending_status = await client.get(record_path, headers=direct_headers)
            assert pending_status.status_code == 200
            assert pending_status.json() == first.json() | {"status": "received", "decided_at": None}
            assert (await client.get(record_path, headers={"Authorization": f"Bearer {other}"})).status_code == 404
            delegated = mint_session_tokens(purchaser.email, amr="api_token", org_id=org.id).access_token
            refused = await client.put(record_path, headers={"Authorization": f"Bearer {delegated}"})
            assert refused.status_code == 403
            assert (await client.get(availability, headers={"Authorization": f"Bearer {delegated}"})).status_code == 403
            async with postgres_session_factory() as db:
                record_request = (await db.execute(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.resource_uuid == reference,
                    StudyBusinessEvent.action == "studytomy.record_request.submitted",
                ))).scalar_one()
                assert record_request.subject_uuid == profile.learner_uuid
                assert record_request.after_state["owner_assignment_id"] == privacy_owner.id
                different_subject = StudyLearnerProfile(
                    org_id=org.id, user_id=second_approver.id, created_by_user_id=owner.id,
                    status="active", control_mode="independent",
                )
                db.add(different_subject)
                await db.flush()
                file_data = {
                    "org_id": org.id, "record_request_event_uuid": record_request.event_uuid,
                    "purpose": "record_request_evidence", "storage_reference": "synthetic-private-storage",
                    "content_sha256": "a" * 64, "media_type": "application/pdf", "size_bytes": 12,
                    "retention_category": "synthetic", "upload_status": "ready", "uploaded_by_user_id": owner.id,
                }
                with pytest.raises(DBAPIError, match="requires its exact intake and learner"):
                    async with db.begin_nested():
                        db.add(StudyEvidenceFile(
                            **file_data, evidence_uuid=f"ev_{uuid4().hex}", learner_profile_id=different_subject.id,
                        ))
                        await db.flush()
                evidence_file = StudyEvidenceFile(**file_data, evidence_uuid=f"ev_{uuid4().hex}", learner_profile_id=profile.id)
                db.add(evidence_file)
                await db.flush()
                with pytest.raises(DBAPIError, match="scope is immutable"):
                    async with db.begin_nested():
                        await db.execute(update(StudyEvidenceFile).where(StudyEvidenceFile.id == evidence_file.id).values(
                            record_request_event_uuid=str(uuid4()),
                        ))
                await _grant_role_capabilities(db, org.id, owner.id, privacy_retention_review=True)
            review_path = f"{org_path}/record-requests/{reference}"
            queue_path = f"{org_path}/record-requests"
            review_headers = {"Authorization": f"Bearer {other}"}
            unassigned = await client.get(review_path, headers=review_headers)
            assert unassigned.status_code == 404, unassigned.text
            assert (await client.get(f"{review_path}/decision", headers=review_headers)).status_code == 404
            empty_queue = await client.get(queue_path, headers=review_headers)
            assert empty_queue.status_code == 200 and empty_queue.json() == []
            assert (await client.get(queue_path, headers=direct_headers)).status_code == 404
            async with postgres_session_factory() as db:
                await _seed_staff_scope(
                    db, org.id, privacy_owner.id, StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW,
                    "case", reference, purchaser.id,
                )
            reviewed = await client.get(review_path, headers=review_headers)
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.headers["cache-control"] == "private, no-store" and reviewed.json() == first.json()
            hold_reference = str(uuid4())
            hold_path = f"{review_path}/evidence-holds/{hold_reference}"
            dry_run_path = f"{review_path}/evidence-retention"
            same_cutoff = {"as_of": datetime.now(UTC).isoformat()}
            initial_dry_run = await client.get(dry_run_path, params=same_cutoff, headers=review_headers)
            assert initial_dry_run.status_code == 200 and initial_dry_run.headers["cache-control"] == "private, no-store"
            assert initial_dry_run.json()["files"] == [{
                "evidence_reference": evidence_file.evidence_uuid, "disposition": "case_open", "eligible_at": None,
            }]
            assert (await client.get(dry_run_path, params=same_cutoff, headers=review_headers)).json() == initial_dry_run.json()
            hold_command = {"reason_code": "synthetic_review", "review_due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat()}
            assert (await client.put(hold_path, json=hold_command, headers=direct_headers)).status_code in (403, 404)
            unapproved_hold = await client.put(hold_path, json=hold_command | {"reason_code": "unapproved"}, headers=review_headers)
            assert unapproved_hold.status_code == 409 and unapproved_hold.json()["detail"]["code"] == "RETENTION_HOLD_NOT_APPROVED"
            held, replayed_hold = await asyncio.gather(*[
                client.put(hold_path, json=hold_command, headers=review_headers) for _ in range(2)
            ])
            assert held.status_code == replayed_hold.status_code == 200, (held.text, replayed_hold.text)
            assert held.json() == replayed_hold.json() and held.json()["hold_reference"] == hold_reference
            held_manifest = await client.get(dry_run_path, params=same_cutoff, headers=review_headers)
            assert held_manifest.status_code == 200 and held_manifest.json()["hold_references"] == [hold_reference]
            assert held_manifest.json()["files"][0]["disposition"] == "held"
            assert held_manifest.json()["snapshot_sha256"] != initial_dry_run.json()["snapshot_sha256"]
            conflicting_hold = await client.put(hold_path, json=hold_command | {
                "review_due_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            }, headers=review_headers)
            assert conflicting_hold.status_code == 409
            assert conflicting_hold.json()["detail"]["code"] == "RETENTION_HOLD_REFERENCE_CONFLICT"
            async with postgres_session_factory() as db:
                hold = (await db.execute(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.resource_uuid == hold_reference,
                    StudyBusinessEvent.action == "studytomy.retention.case_evidence_held",
                ))).scalar_one()
                assert hold.operation_uuid == record_request.event_uuid and hold.after_state["decision_code"] == "synthetic_review"
            lift_path = f"{hold_path}/release"
            lift_command = {"reason_code": "synthetic_review_complete"}
            assert (await client.put(lift_path, json=lift_command, headers=direct_headers)).status_code in (403, 404)
            lifted, lift_replay = await asyncio.gather(*[
                client.put(lift_path, json=lift_command, headers=review_headers) for _ in range(2)
            ])
            assert lifted.status_code == lift_replay.status_code == 200, (lifted.text, lift_replay.text)
            assert lifted.json() == lift_replay.json()
            after_lift = await client.get(dry_run_path, params=same_cutoff, headers=review_headers)
            assert after_lift.status_code == 200 and after_lift.json()["hold_references"] == []
            assert after_lift.json()["files"] == initial_dry_run.json()["files"]
            assert after_lift.json()["snapshot_sha256"] != initial_dry_run.json()["snapshot_sha256"]
            queued = await client.get(queue_path, headers=review_headers)
            assert queued.status_code == 200 and queued.json() == [first.json()]
            assert queued.headers["cache-control"] == "private, no-store"
            decision_path = f"{review_path}/decision"
            staff_export = f"{review_path}/completion-export"
            own_export = f"{record_path}/completion-export"
            assert (await client.get(staff_export, headers=review_headers)).status_code == 409
            assert (await client.post(own_export, headers=direct_headers)).status_code == 404
            decision_options = await client.get(decision_path, headers=review_headers)
            assert decision_options.status_code == 200 and decision_options.headers["cache-control"] == "private, no-store"
            assert decision_options.json() == {
                "approved_for_preparation": ["verified_self_request"], "denied": ["identity_unverified"],
            }
            decision = {"outcome": "approved_for_preparation", "reason_code": "verified_self_request",
                        "evidence_reference": "ev_synthetic_identity_review"}
            unapproved = await client.put(decision_path, json=decision | {"reason_code": "unapproved"}, headers=review_headers)
            assert unapproved.status_code == 409
            assert unapproved.json()["detail"]["code"] == "RECORD_REQUEST_DECISION_NOT_APPROVED"
            unverified = await client.put(decision_path, json=decision, headers=review_headers)
            assert unverified.status_code == 409
            assert unverified.json()["detail"]["code"] == "RECORD_REQUEST_EVIDENCE_UNAVAILABLE"
            monkeypatch.chdir(tmp_path)
            config = SimpleNamespace(hosting_config=SimpleNamespace(content_delivery=SimpleNamespace(type="filesystem")))
            monkeypatch.setattr(upload_content, "get_learnhouse_config", lambda: config)
            monkeypatch.setattr("src.services.studytomy.retention_execution.get_learnhouse_config", lambda: config)
            evidence_path = f"{review_path}/evidence"
            upload_headers = {**review_headers, "Idempotency-Key": "synthetic-record-evidence"}
            attachment = {"file": ("synthetic.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")}
            uploaded = await client.post(evidence_path, files=attachment, headers=upload_headers)
            assert uploaded.status_code == 201, uploaded.text
            assert uploaded.headers["cache-control"] == "private, no-store"
            assert (await client.post(evidence_path, files=attachment, headers=upload_headers)).json() == uploaded.json()
            decision["evidence_reference"] = uploaded.json()["evidence_reference"]
            stored = tmp_path / "content" / "orgs" / org.org_uuid / "studytomy" / "evidence" / f"{decision['evidence_reference']}.pdf"
            stored.write_bytes(b"altered bytes")
            corrupt = await client.put(decision_path, json=decision, headers=review_headers)
            assert corrupt.status_code == 503
            assert (await client.get(decision_path, headers=review_headers)).json() == decision_options.json()
            stored.write_bytes(attachment["file"][1])
            decided, repeated = await asyncio.gather(*[
                client.put(decision_path, json=decision, headers=review_headers) for _ in range(2)
            ])
            assert decided.status_code == repeated.status_code == 200, (decided.text, repeated.text)
            assert decided.json() == repeated.json()
            closed_options = await client.get(decision_path, headers=review_headers)
            assert closed_options.status_code == 200 and closed_options.json() == {}
            assert set(decided.json()) == {"request_reference", "outcome", "decided_at"}
            learner_status = await client.get(record_path, headers=direct_headers)
            assert learner_status.status_code == 200 and learner_status.headers["cache-control"] == "private, no-store"
            assert learner_status.json() == first.json() | {
                "status": "approved_for_preparation", "decided_at": decided.json()["decided_at"],
            }
            conflicting = await client.put(decision_path, json={
                **decision, "outcome": "denied", "reason_code": "identity_unverified",
            }, headers=review_headers)
            assert conflicting.status_code == 409
            assert conflicting.json()["detail"]["code"] == "RECORD_REQUEST_ALREADY_DECIDED"
            closed_queue = await client.get(queue_path, headers=review_headers)
            assert closed_queue.status_code == 200 and closed_queue.json() == []
            async with postgres_session_factory() as db:
                review_event = (await db.execute(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.resource_uuid == reference,
                    StudyBusinessEvent.action == "studytomy.record_request.decided",
                ))).scalar_one()
                assert review_event.operation_uuid == record_request.event_uuid
                assert review_event.actor_user_id == owner.id
                assert review_event.after_state["evidence_sha256"] == uploaded.json()["content_sha256"]
            preview = await client.get(staff_export, headers=review_headers)
            assert preview.status_code == 200, preview.text
            native_completion = {
                "course_uuid": course.course_uuid, "recorded_at": previous_completion.replace("+00:00", "Z"),
            }
            assert preview.json()["completions"] == [native_completion]
            before_release_completion = preview.json()["snapshot_sha256"]
            completed_at = datetime.fromisoformat(previous_completion) + timedelta(hours=1)
            async with postgres_session_factory() as db:
                event = {
                    "org_id": org.id, "resource_kind": "course_release",
                    "resource_uuid": release.uuid, "action": "studytomy.course.completion_recorded",
                    "policy_refs": [release.progression_policy_id], "created_at": completed_at,
                }
                db.add_all([
                    StudyBusinessEvent(**event, actor_user_id=purchaser.id,
                                       after_state={"source_snapshot_digest": release.snapshot_sha256}),
                    StudyBusinessEvent(**event, actor_user_id=purchaser.id,
                                       after_state={"source_snapshot_digest": "0" * 64}),
                    StudyBusinessEvent(**event, actor_user_id=owner.id,
                                       after_state={"source_snapshot_digest": release.snapshot_sha256}),
                ])
                await db.commit()
            stale = await client.put(staff_export, json={"snapshot_sha256": before_release_completion}, headers=review_headers)
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "RECORD_DELIVERY_SNAPSHOT_CHANGED"
            preview = await client.get(staff_export, headers=review_headers)
            assert preview.status_code == 200, preview.text
            assert preview.json()["completions"] == [native_completion, {
                "course_uuid": course.course_uuid, "release_uuid": release.uuid,
                "recorded_at": completed_at.isoformat().replace("+00:00", "Z"),
            }]
            wrong_snapshot = await client.put(staff_export, json={"snapshot_sha256": "f" * 64}, headers=review_headers)
            assert wrong_snapshot.status_code == 409
            command = {"snapshot_sha256": preview.json()["snapshot_sha256"]}
            released, replayed = await asyncio.gather(*[
                client.put(staff_export, json=command, headers=review_headers) for _ in range(2)
            ])
            assert released.status_code == replayed.status_code == 200, (released.text, replayed.text)
            assert released.json() == replayed.json()
            download, repeated_download = await asyncio.gather(*[
                client.post(own_export, headers=direct_headers) for _ in range(2)
            ])
            assert download.status_code == 200, download.text
            assert download.headers["cache-control"] == "private, no-store"
            assert download.headers["content-disposition"].startswith("attachment;")
            assert download.json() == {"completions": preview.json()["completions"]}
            assert repeated_download.status_code == 200 and repeated_download.json() == download.json()
            handoff_receipt = download.headers["x-studytomy-delivery-receipt"]
            assert repeated_download.headers["x-studytomy-delivery-receipt"] == handoff_receipt
            outsider_session = mint_session_tokens(second_approver.email, amr="password", org_id=org.id).access_token
            assert (await client.post(own_export, headers={"Authorization": f"Bearer {outsider_session}"})).status_code == 404
            async with postgres_session_factory() as db:
                await db.execute(update(StudyLearnerProfile).where(StudyLearnerProfile.id == profile.id).values(
                    control_mode="dependent",
                ))
                await db.commit()
            assert (await client.get(staff_export, headers=review_headers)).status_code == 404
            assert (await client.post(own_export, headers=direct_headers)).status_code == 404
            async with postgres_session_factory() as db:
                await db.execute(update(StudyLearnerProfile).where(StudyLearnerProfile.id == profile.id).values(
                    control_mode="independent",
                ))
                await db.execute(update(TrailRun).where(TrailRun.id == run.id).values(update_date=""))
                await db.commit()
            changed = await client.post(own_export, headers=direct_headers)
            assert changed.status_code == 409 and changed.json()["detail"]["code"] == "RECORD_DELIVERY_SNAPSHOT_CHANGED"
            async with postgres_session_factory() as db:
                handoff = (await db.execute(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.resource_uuid == reference,
                    StudyBusinessEvent.action == "studytomy.record_request.delivery_handed_off",
                ))).scalar_one()
                assert handoff.event_uuid == handoff_receipt and handoff.actor_user_id == purchaser.id
                assert handoff.after_state == {"source_snapshot_digest": command["snapshot_sha256"]}
                await db.execute(update(TrailRun).where(TrailRun.id == run.id).values(update_date=previous_completion))
                await db.commit()
            removal_cutoff = datetime.now(UTC) + timedelta(days=6)

            class RetentionClock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return removal_cutoff.astimezone(tz)

            with monkeypatch.context() as clock:
                clock.setattr("src.services.studytomy.retention.datetime", RetentionClock)
                params = {"as_of": removal_cutoff.isoformat()}
                manifest = await client.get(dry_run_path, params=params, headers=review_headers)
                assert manifest.status_code == 200, manifest.text
                eligible_files = [item for item in manifest.json()["files"] if item["disposition"] == "candidate"]
                assert [item["evidence_reference"] for item in eligible_files] == [uploaded.json()["evidence_reference"]]
                approval_ref = str(uuid4())
                approval_path = f"{dry_run_path}/approvals/{approval_ref}"
                approval = params | {"snapshot_sha256": manifest.json()["snapshot_sha256"], "reason_code": "synthetic_expired"}
                approved, approval_replay = await asyncio.gather(*[
                    client.put(approval_path, json=approval, headers=review_headers) for _ in range(2)
                ])
                assert approved.status_code == approval_replay.status_code == 200, (approved.text, approval_replay.text)
                assert approved.json() == approval_replay.json() and approved.json()["candidate_count"] == 1
                renewed_hold_path = f"{review_path}/evidence-holds/{uuid4()}"
                renewed_hold = await client.put(renewed_hold_path, json={
                    "reason_code": "synthetic_review", "review_due_at": (removal_cutoff + timedelta(days=1)).isoformat(),
                }, headers=review_headers)
                assert renewed_hold.status_code == 200, renewed_hold.text
                stale = await client.put(approval_path, json=approval, headers=review_headers)
                assert stale.status_code == 409 and stale.json()["detail"]["code"] == "RETENTION_MANIFEST_CHANGED"
                old_execution_path = f"{approval_path}/files/{decision['evidence_reference']}"
                blocked_execution = await client.put(old_execution_path, headers=review_headers)
                assert blocked_execution.status_code == 409
                assert blocked_execution.json()["detail"]["code"] == "RETENTION_MANIFEST_CHANGED"
                assert stored.read_bytes() == attachment["file"][1]
                assert (await client.put(f"{renewed_hold_path}/release", json=lift_command, headers=review_headers)).status_code == 200
                new_manifest = await client.get(dry_run_path, params=params, headers=review_headers)
                assert new_manifest.status_code == 200
                new_approval_path = f"{dry_run_path}/approvals/{uuid4()}"
                new_approval = await client.put(new_approval_path, json=approval | {
                    "snapshot_sha256": new_manifest.json()["snapshot_sha256"],
                }, headers=review_headers)
                assert new_approval.status_code == 200, new_approval.text
                execution_path = f"{new_approval_path}/files/{decision['evidence_reference']}"
                original_unlink = type(stored).unlink

                def fail_evidence_unlink(path, *args, **kwargs):
                    if path == stored:
                        raise PermissionError("Synthetic storage failure")
                    return original_unlink(path, *args, **kwargs)

                with monkeypatch.context() as failure:
                    failure.setattr(type(stored), "unlink", fail_evidence_unlink)
                    refused_removal = await client.put(execution_path, headers=review_headers)
                    assert refused_removal.status_code == 503, refused_removal.text
                assert stored.read_bytes() == attachment["file"][1]
                removed, removal_retry = await asyncio.gather(*[
                    client.put(execution_path, headers=review_headers) for _ in range(2)
                ])
                assert removed.status_code == removal_retry.status_code == 200, (removed.text, removal_retry.text)
                assert removed.json() == removal_retry.json()
                assert not stored.exists()
                finished_manifest = await client.get(dry_run_path, params=params, headers=review_headers)
                assert finished_manifest.status_code == 200
                assert finished_manifest.json()["snapshot_sha256"] == new_manifest.json()["snapshot_sha256"]
                assert finished_manifest.json()["completed_removals"] == {
                    decision["evidence_reference"]: removed.json()["removal_receipt"],
                }
                assert finished_manifest.json()["completed_removal_approvals"] == {
                    decision["evidence_reference"]: new_approval.json()["approval_reference"],
                }
                original_approval_retry = await client.put(new_approval_path, json=approval | {
                    "snapshot_sha256": finished_manifest.json()["snapshot_sha256"],
                }, headers=review_headers)
                assert original_approval_retry.status_code == 200 and original_approval_retry.json() == new_approval.json()
                duplicate_approval = await client.put(f"{dry_run_path}/approvals/{uuid4()}", json=approval | {
                    "snapshot_sha256": finished_manifest.json()["snapshot_sha256"],
                }, headers=review_headers)
                assert duplicate_approval.status_code == 409
                assert duplicate_approval.json()["detail"]["code"] == "RETENTION_NO_CANDIDATES"
                restore_replay = f"{execution_path}/restore-replays/{uuid4()}"
                stored.write_bytes(b"unexpected restored bytes")
                unexpected = await client.put(restore_replay, headers=review_headers)
                assert unexpected.status_code == 503 and stored.read_bytes() == b"unexpected restored bytes"
                stored.write_bytes(attachment["file"][1])
                reconciled, reconciled_retry = await asyncio.gather(*[
                    client.put(restore_replay, headers=review_headers) for _ in range(2)
                ])
                assert reconciled.status_code == reconciled_retry.status_code == 200, (reconciled.text, reconciled_retry.text)
                assert reconciled.json() == reconciled_retry.json() and not stored.exists()
                async with postgres_session_factory() as db:
                    replay = (await db.execute(select(StudyBusinessEvent).where(
                        StudyBusinessEvent.org_id == org.id,
                        StudyBusinessEvent.event_uuid == reconciled.json()["removal_receipt"],
                    ))).scalar_one()
                    assert replay.action == "studytomy.retention.case_evidence_restore_replayed"
                    assert replay.operation_uuid == removed.json()["removal_receipt"]
            consent = await client.post(f"{org_path}/learners/me/consent-events", json={
                "purpose": "learning_terms", "choice": "accept", "notice_version": "fixture-v1",
                "scope_codes": ["course_release"],
            }, headers={**headers, "Idempotency-Key": "synthetic-reentry-consent"})
            assert consent.status_code == 201, consent.text
            before = await client.get(f"{release_path}/access-history")
            assert before.status_code == 200 and before.json()["status"] == "expired"
            assert before.json()["completion_before_window"] is None
            assert (await client.get(f"{release_path}/lessons/{activity_uuid}")).status_code == 404
            path = f"{prefix}/orders/{ref}/accept"
            attempts = await asyncio.gather(*[client.post(path, json=confirmation) for _ in range(2)])
            assert all(attempt.status_code == 200 and attempt.json() == {"customer_ref": ref} for attempt in attempts)
            current_history = await client.get(f"{release_path}/access-history")
            assert current_history.status_code == 200 and current_history.json()["status"] == "active"
            assert current_history.json()["completion_before_window"] is True
            async with postgres_session_factory() as db:
                original, current = (await db.execute(select(StudyCourseAccessGrant).where(
                    StudyCourseAccessGrant.org_id == org.id,
                ).order_by(StudyCourseAccessGrant.id))).scalars().all()
                assert original.id == previous_grant.id and original.ends_at == previous_end and original.revoked_at is None
                assert current.source_kind == "order_line" and current.starts_at > previous_end
                assert current.ends_at - current.starts_at == timedelta(days=1)
                line = await db.get(StudyOrderLine, int(current.source_uuid))
                order = await db.get(StudyOrder, line.order_id)
                assert order.customer_ref == ref and line.learner_profile_id == profile.id
                preserved = (await db.execute(select(TrailRun).where(TrailRun.org_id == org.id))).scalar_one()
                assert preserved.id == run.id and preserved.status == StatusEnum.STATUS_COMPLETED
                assert preserved.creation_date == preserved.update_date == previous_completion
                assert (await db.execute(select(StudyBusinessEvent.id).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.dedupe_key == f"studytomy:course_grant:{org.id}:{current.id}",
                ))).scalar_one()
                await db.execute(update(TrailRun).where(TrailRun.id == run.id).values(
                    update_date=current.starts_at.isoformat(),
                ))
                await db.commit()
            event_history = await client.get(f"{release_path}/access-history")
            assert event_history.status_code == 200 and event_history.json()["completion_before_window"] is True
            async with postgres_session_factory() as db:
                await db.execute(update(TrailRun).where(TrailRun.id == run.id).values(update_date=previous_completion))
                await db.commit()
            return
        accepted = await client.post(f"{prefix}/orders/{ref}/accept", json=confirmation)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json() == {"customer_ref": ref}
        receipt_path = f"{prefix}/orders/{ref}/receipt"
        options_path = f"{prefix}/orders/{ref}/refund-options"
        assert (await client.get(options_path)).status_code == 404
        awaiting = await client.get(receipt_path)
        assert awaiting.status_code == 200, awaiting.text
        assert awaiting.json()["collection_status"] == ("not_required" if free_approved else "awaiting_collection")
        assert awaiting.json()["confirmed_minor"] == 0
        assert awaiting.json()["allocations"][0]["correction_status"] is None
        purchases = await client.get(purchase_path, params={"limit": 1})
        assert purchases.status_code == 200, purchases.text
        assert purchases.headers["cache-control"] == "private, no-store"
        assert purchases.json() == {"orders": [{
            "customer_ref": ref, "accepted_at": awaiting.json()["accepted_at"],
            "currency": "AUD", "order_total_minor": price_minor,
        }], "next_cursor": None}
        assert (await client.get(purchase_path, params={"after": ref})).json() == {
            "orders": [], "next_cursor": None,
        }
        if free_approved:
            replay = await client.post(f"{prefix}/orders/{ref}/accept", json=confirmation)
            assert replay.status_code == 200, replay.text
            assert replay.json() == accepted.json()
            org_path = f"/api/v1/studytomy/orgs/{org.org_uuid}"
            release_path = f"{org_path}/learning/courses/{course.course_uuid}/releases/{release.uuid}"
            lesson_path = f"{release_path}/lessons/{activity_uuid}"
            if first_use:
                async with postgres_session_factory() as db:
                    await _grant_role_capabilities(db, org.id, owner.id, family_authority_review=True)
                    await _seed_staff_scope(
                        db, org.id, owners[StudyOwnerFunction.FAMILY_SERVICES].id,
                        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW, "learner", profile.learner_uuid, purchaser.id,
                    )
                reviewer_access = mint_session_tokens(owner.email, amr="mfa", org_id=org.id).access_token
                reviewer_headers = {"Authorization": f"Bearer {reviewer_access}"}
                correction_review = f"{org_path}/learners/{profile.learner_uuid}/allocations/{ref}/correction-review"
                unstarted = await client.get(correction_review, headers=reviewer_headers)
                assert unstarted.status_code == 200 and unstarted.json() == {"status": "unstarted", "reason_codes": ["wrong_learner", "other_reviewed_reason"]}
            consent = await client.post(
                f"{org_path}/learners/me/consent-events",
                json={"purpose": "learning_terms", "choice": "accept", "notice_version": "fixture-v1",
                      "scope_codes": ["course_release"]},
                headers={**headers, "Idempotency-Key": "synthetic-free-course-consent"},
            )
            assert consent.status_code == 201, consent.text
            if media_scenario:
                overview_path = release_path
                finance_headers = {
                    "Authorization": f"Bearer {mint_session_tokens(verifier.email, amr='mfa', org_id=org.id).access_token}"
                }
                async with postgres_session_factory() as db:
                    grant = (await db.execute(select(StudyCourseAccessGrant).where(
                        StudyCourseAccessGrant.org_id == org.id,
                    ))).scalar_one()
                index = await client.get(f"{overview_path}/lessons", headers=headers)
                assert index.status_code == 200, index.text
                assert index.headers["cache-control"] == "private, no-store"
                assert index.json() == {
                    "requires_first_use": True,
                    "lessons": [
                        {
                            "activity_uuid": activity_uuid,
                            "name": "Reviewed lesson",
                            "position": 1,
                            "format": "text",
                            "media_type": None,
                            "available": True,
                            "unavailable_reason": None,
                        },
                        {
                            "activity_uuid": media_activity_uuid,
                            "name": "Reviewed PDF",
                            "position": 2,
                            "format": "pdf",
                            "media_type": "application/pdf",
                            "available": True,
                            "unavailable_reason": None,
                        },
                        {
                            "activity_uuid": video_activity_uuid,
                            "name": "Reviewed video",
                            "position": 3,
                            "format": "video",
                            "media_type": "video/mp4",
                            "available": True,
                            "caption_codes": ["en"],
                            "unavailable_reason": None,
                        },
                        {
                            "activity_uuid": unsupported_media_uuid,
                            "name": "Unsupported audio",
                            "position": 4,
                            "format": "unsupported",
                            "media_type": None,
                            "available": False,
                            "unavailable_reason": "unsupported_release_format",
                        },
                    ],
                }
                assert (await client.get(f"{overview_path}/lessons", headers=finance_headers)).status_code == 404
                media_path = (
                    f"{overview_path}/activities/{media_activity_uuid}/media"
                )
                storage_reads = []
                storage_bytes_read = []

                content_root = tmp_path / "content"
                retained_pdf = content_root / "orgs" / org.org_uuid / media_storage_reference
                retained_pdf.parent.mkdir(parents=True)
                retained_pdf.write_bytes(release_media_bytes)
                (content_root / "orgs" / org.org_uuid / caption_storage_reference).write_bytes(caption_bytes)
                (content_root / "orgs" / org.org_uuid / video_storage_reference).write_bytes(release_video_bytes)
                config = SimpleNamespace(hosting_config=SimpleNamespace(
                    content_delivery=SimpleNamespace(type="filesystem"),
                ))
                monkeypatch.setattr(upload_content, "_CONTENT_ROOT", str(content_root))
                monkeypatch.setattr(upload_content, "get_learnhouse_config", lambda: config)

                async def open_fixture_media(**kwargs):
                    storage_reads.append(kwargs)
                    stream = await upload_content.open_content_stream(**kwargs)

                    async def chunks():
                        try:
                            async for chunk in stream:
                                storage_bytes_read.append(len(chunk))
                                yield chunk
                        finally:
                            await stream.aclose()

                    return chunks()

                monkeypatch.setattr(
                    "src.services.studytomy.access_policy.open_content_stream",
                    open_fixture_media,
                )
                assert (await client.get(media_path, headers=finance_headers)).status_code == 404
                assert (await client.get(
                    media_path.replace(course.course_uuid, f"{course.course_uuid}-other"),
                )).status_code == 404
                assert (await client.get(
                    media_path.replace(media_activity_uuid, f"activity_{uuid4()}"),
                )).status_code == 404
                assert (await client.get(
                    media_path.replace(org.org_uuid, f"org_{uuid4()}"),
                )).status_code == 404
                video_path = media_path.replace(media_activity_uuid, video_activity_uuid)
                caption_path = f"{video_path}/captions/en"
                assert (await client.get(caption_path, headers=headers)).status_code == 404
                assert storage_reads == []

                invalid_range = await client.post(
                    f"{media_path}/open", headers={**headers, "Range": f"bytes={len(release_media_bytes)}-"},
                )
                assert invalid_range.status_code == 416
                assert invalid_range.headers["content-range"] == f"bytes */{len(release_media_bytes)}"
                assert storage_reads == []
                async with postgres_session_factory() as db:
                    unstarted = await db.get(StudyCourseAccessGrant, grant.id)
                    assert unstarted.starts_at is None and unstarted.ends_at is None

                # A real missing filesystem object must not consume first use.
                retained_pdf.unlink()
                missing_pdf = await client.post(f"{media_path}/open", headers=headers)
                assert missing_pdf.status_code == 503
                assert missing_pdf.json()["detail"]["code"] == "RELEASE_STORAGE_INVALID"
                async with postgres_session_factory() as db:
                    unstarted = await db.get(StudyCourseAccessGrant, grant.id)
                    assert unstarted.starts_at is None and unstarted.ends_at is None
                retained_pdf.write_bytes(release_media_bytes)
                prior_reads = len(storage_reads)

                opened, replayed_open = await asyncio.gather(*[
                    client.post(f"{media_path}/open", headers=headers) for _ in range(2)
                ])
                for response in (opened, replayed_open):
                    assert response.status_code == 200, response.text
                    assert response.content == release_media_bytes
                    assert response.headers["content-type"] == "application/pdf"
                    assert response.headers["cache-control"] == "private, no-store"
                    assert response.headers["x-content-type-options"] == "nosniff"
                    assert response.headers["accept-ranges"] == "bytes"
                assert len(storage_reads) == prior_reads + 2
                with BytesIO(opened.content) as pdf:
                    pages = PdfReader(pdf, strict=True).pages
                    assert [tuple(page.mediabox)[2:] for page in pages] == [(300, 420), (420, 300)]
                async with postgres_session_factory() as db:
                    started = await db.get(StudyCourseAccessGrant, grant.id)
                    assert started.starts_at is not None and started.ends_at is not None
                    delivered = (await db.execute(select(StudyBusinessEvent).where(
                        StudyBusinessEvent.org_id == org.id,
                        StudyBusinessEvent.resource_uuid == str(grant.id),
                        StudyBusinessEvent.action == "studytomy.course_grant.content_delivered",
                    ))).scalars().all()
                    assert len(delivered) == 1

                caption = await client.get(caption_path, headers=headers)
                video = await client.get(video_path, headers={**headers, "Range": "bytes=1-4"})
                assert video.status_code == 206
                assert video.content == release_video_bytes[1:5]
                assert caption.status_code == 200
                assert caption.content == caption_bytes
                assert caption.headers["content-type"].startswith("text/vtt")
                assert caption.headers["cache-control"] == "private, no-store"
                assert (await client.get(caption_path, headers=finance_headers)).status_code == 404
                assert (await client.get(f"{video_path}/captions/fr", headers=headers)).status_code == 404
                assert (await client.get(
                    caption_path.replace(course.course_uuid, f"{course.course_uuid}-other"), headers=headers,
                )).status_code == 404

                ranged = await client.get(media_path, headers={**headers, "Range": "bytes=1-4"})
                assert ranged.status_code == 206
                assert ranged.content == release_media_bytes[1:5]
                assert ranged.headers["content-range"] == f"bytes 1-4/{len(release_media_bytes)}"
                prior_reads = len(storage_reads)
                prior_bytes_read = sum(storage_bytes_read)
                repeated_ranges = await asyncio.gather(*[
                    client.get(media_path, headers={**headers, "Range": "bytes=1-4"})
                    for _ in range(2)
                ])
                assert all(response.status_code == 206 for response in repeated_ranges)
                assert len(storage_reads) == prior_reads + 2
                assert sum(storage_bytes_read) == prior_bytes_read + 2 * len(release_media_bytes)

                async def open_tampered_media(**kwargs):
                    async def chunks():
                        yield b"tampered" * 6

                    return chunks()

                monkeypatch.setattr(
                    "src.services.studytomy.access_policy.open_content_stream",
                    open_tampered_media,
                )
                tampered = await client.get(media_path, headers=headers)
                assert tampered.status_code == 503
                assert tampered.json()["detail"]["code"] == "RELEASE_STORAGE_INVALID"
                assert b"tampered" not in tampered.content

                from src.services.studytomy import access_policy

                original_temporary_file = access_policy.TemporaryFile
                staged_files = []

                def track_temporary_file(*args, **kwargs):
                    staged_file = original_temporary_file(*args, **kwargs)
                    staged_files.append(staged_file)
                    return staged_file

                monkeypatch.setattr(access_policy, "TemporaryFile", track_temporary_file)
                original_current_policies = access_policy._current_policies
                d15_policy_reads = 0

                async def changed_d15_policy(db, organization, policy_keys, now):
                    nonlocal d15_policy_reads
                    policies = await original_current_policies(db, organization, policy_keys, now)
                    if tuple(policy_keys) == (StudyPolicyKey.D_15,):
                        d15_policy_reads += 1
                        if d15_policy_reads == 2:
                            changed_policy = policies[0].model_copy(
                                update={"id": int(policies[0].id) + 1}
                            )
                            return [changed_policy]
                    return policies

                monkeypatch.setattr(access_policy, "_current_policies", changed_d15_policy)
                policy_staging_paused = asyncio.Event()
                resume_policy_staging = asyncio.Event()

                async def open_policy_change_media(**kwargs):
                    async def chunks():
                        yield release_media_bytes
                        policy_staging_paused.set()
                        await resume_policy_staging.wait()

                    return chunks()

                monkeypatch.setattr(
                    "src.services.studytomy.access_policy.open_content_stream",
                    open_policy_change_media,
                )
                pending_policy_delivery = asyncio.create_task(client.get(media_path, headers=headers))
                await asyncio.wait_for(policy_staging_paused.wait(), timeout=5)
                resume_policy_staging.set()
                changed_policy_response = await pending_policy_delivery
                assert changed_policy_response.status_code == 409
                assert changed_policy_response.json()["detail"]["code"] == "POLICY_CHANGED"
                assert d15_policy_reads == 2
                assert staged_files and staged_files[-1].closed
                monkeypatch.setattr(access_policy, "_current_policies", original_current_policies)

                async def change_grant_during_staging(change: str) -> None:
                    staging_paused = asyncio.Event()
                    resume_staging = asyncio.Event()

                    async def open_paused_media(**kwargs):
                        storage_reads.append(kwargs)

                        async def chunks():
                            yield release_media_bytes
                            storage_bytes_read.append(len(release_media_bytes))
                            staging_paused.set()
                            await resume_staging.wait()

                        return chunks()

                    monkeypatch.setattr(
                        "src.services.studytomy.access_policy.open_content_stream",
                        open_paused_media,
                    )
                    pending_delivery = asyncio.create_task(client.get(media_path, headers=headers))
                    await asyncio.wait_for(staging_paused.wait(), timeout=5)
                    governance_lock_acquired = False
                    try:
                        async with postgres_session_factory() as db:
                            try:
                                await asyncio.wait_for(
                                    db.execute(
                                        select(Organization.id)
                                        .where(Organization.id == org.id)
                                        .with_for_update()
                                    ),
                                    timeout=2,
                                )
                                governance_lock_acquired = True
                            except TimeoutError:
                                await db.rollback()

                            changed_grant = await db.get(StudyCourseAccessGrant, grant.id)
                            now = datetime.now(UTC)
                            if change == "expired":
                                expires_at = changed_grant.ends_at

                                class ExpiredClock(datetime):
                                    @classmethod
                                    def now(cls, tz=None):
                                        return expires_at + timedelta(milliseconds=1)

                                monkeypatch.setattr(access_policy, "datetime", ExpiredClock)
                            else:
                                changed_grant.revoked_at = now
                                await db.commit()
                    finally:
                        resume_staging.set()

                    response = await pending_delivery
                    assert governance_lock_acquired, "Storage staging still held the organization governance lock."
                    assert response.status_code == 404
                    assert staged_files and all(staged_file.closed for staged_file in staged_files)

                await change_grant_during_staging("expired")
                monkeypatch.setattr(access_policy, "datetime", datetime)
                if correction_scenario == "stream_revocation":
                    first_chunk_sent = asyncio.Event()
                    resume_stream = asyncio.Event()
                    original_stream = access_policy.ManagedActivityMedia.stream

                    async def pause_after_first_chunk(media):
                        async with aclosing(original_stream(media)) as chunks:
                            yield await anext(chunks)
                            first_chunk_sent.set()
                            await resume_stream.wait()
                            async for chunk in chunks:
                                yield chunk

                    monkeypatch.setattr(access_policy.ManagedActivityMedia, "stream", pause_after_first_chunk)
                    monkeypatch.setattr(access_policy, "open_content_stream", open_fixture_media)
                    delivery = asyncio.create_task(client.get(media_path, headers=headers))
                    try:
                        await asyncio.wait_for(first_chunk_sent.wait(), timeout=5)
                        async with postgres_session_factory() as revoker:
                            record = await revoker.get(StudyCourseAccessGrant, grant.id)
                            record.revoked_at = datetime.now(UTC)
                            await revoker.commit()
                    finally:
                        resume_stream.set()
                        response = await asyncio.wait_for(delivery, timeout=5)
                    assert response.status_code == 200
                    assert response.content == release_media_bytes[:64 * 1024]
                    assert int(response.headers["content-length"]) > len(response.content)
                    assert staged_files and all(staged_file.closed for staged_file in staged_files)
                    assert (await client.get(media_path, headers=headers)).status_code == 404
                    return

                await change_grant_during_staging("revoked")
                reads_before_revoked = len(storage_reads)
                assert (await client.get(media_path, headers=headers)).status_code == 404
                assert (await client.get(caption_path, headers=headers)).status_code == 404
                assert len(storage_reads) == reads_before_revoked
                return
            if correct_before_first_use:
                async with postgres_session_factory() as db:
                    replacement = StudyLearnerProfile(
                        org_id=org.id, user_id=second_approver.id, created_by_user_id=owner.id,
                        status="active", control_mode="dependent",
                    )
                    db.add(replacement)
                    await db.flush()
                    policy_ids = [policies[key].id for key in (StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13)]
                    now = datetime.now(UTC)
                    purchaser_authority = StudyRelationship(
                        org_id=org.id, learner_profile_id=replacement.id, adult_user_id=purchaser.id,
                        requested_by_user_id=purchaser.id, requested_scopes=["purchase_allocation"],
                        approved_scopes=["purchase_allocation"], status="accepted",
                        request_evidence_reference="SYNTHETIC", request_policy_version_ids=policy_ids,
                        decision_policy_version_ids=policy_ids, valid_from=now, valid_to=now+timedelta(days=1),
                        decided_by_user_id=owner.id, decision_owner_assignment_id=owners[StudyOwnerFunction.FAMILY_SERVICES].id,
                        decision_evidence_reference="SYNTHETIC", decision_reason_code="VERIFIED", decided_at=now,
                    )
                    db.add(StudyEligibilityDecision(
                        org_id=org.id, learner_profile_id=replacement.id, eligibility_kind="learning", outcome="eligible",
                        policy_version_ids=[policies[key].id for key in (
                            StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_12,
                        )],
                        evidence_reference="SYNTHETIC", effective_from=now, reviewer_user_id=owner.id,
                        owner_assignment_id=owners[StudyOwnerFunction.EDUCATION].id,
                    ))
                    await db.commit()
                correction_path = correction_review.removesuffix("-review")
                correction = {"replacement_learner_uuid": replacement.learner_uuid, "reason_code": "wrong_learner"}
                denied = await client.put(correction_path, json=correction, headers=reviewer_headers)
                assert denied.status_code == 404
                async with postgres_session_factory() as db:
                    await _seed_staff_scope(
                        db, org.id, owners[StudyOwnerFunction.FAMILY_SERVICES].id,
                        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW, "learner", replacement.learner_uuid, purchaser.id,
                    )
                wrong_learner = await client.put(correction_path, json=correction, headers=reviewer_headers)
                assert wrong_learner.status_code == 404
                assert wrong_learner.json()["detail"]["code"] == "ALLOCATION_LEARNER_UNAVAILABLE"
                async with postgres_session_factory() as db:
                    grant = (await db.execute(select(StudyCourseAccessGrant).where(
                        StudyCourseAccessGrant.org_id == org.id,
                    ))).scalar_one()
                    assert grant.learner_profile_id == profile.id and grant.revoked_at is None
                    assert (await db.execute(select(StudyBusinessEvent.id).where(
                        StudyBusinessEvent.org_id == org.id,
                        StudyBusinessEvent.action == "studytomy.allocation.corrected",
                    ))).first() is None
                    db.add(purchaser_authority)
                    await db.commit()
                if correction_scenario == "race":
                    first, opened = await asyncio.gather(
                        client.put(correction_path, json=correction, headers=reviewer_headers),
                        client.post(f"{lesson_path}/open", headers=headers),
                    )
                    assert (first.status_code, opened.status_code) in ((200, 404), (409, 200)), (
                        first.text, opened.text,
                    )
                    if opened.status_code == 200:
                        assert opened.json()["paragraphs"] == ["Retained lesson text."]
                        assert first.json()["detail"]["code"] == "ALLOCATION_EXCEPTION_REVIEW_REQUIRED"
                        used = await client.get(correction_review, headers=reviewer_headers)
                        assert used.status_code == 200 and used.json() == {"status": "used", "reason_codes": []}
                        async with postgres_session_factory() as db:
                            grant = (await db.execute(select(StudyCourseAccessGrant).where(
                                StudyCourseAccessGrant.org_id == org.id,
                            ))).scalar_one()
                            assert grant.learner_profile_id == profile.id
                            assert grant.starts_at is not None and grant.revoked_at is None
                        return
                else:
                    first, repeat = await asyncio.gather(*[
                        client.put(correction_path, json=correction, headers=reviewer_headers) for _ in range(2)
                    ])
                    assert repeat.status_code == 200 and first.json() == repeat.json(), repeat.text
                assert first.status_code == 200 and first.json()["status"] == "corrected", first.text
                corrected_receipt = await client.get(receipt_path, headers=headers)
                assert corrected_receipt.status_code == 200, corrected_receipt.text
                assert corrected_receipt.headers["cache-control"] == "private, no-store"
                corrected_line = corrected_receipt.json()["allocations"][0]
                expected_status = None if correction_scenario == "receipt_scope_absent" else "corrected"
                assert corrected_line == awaiting.json()["allocations"][0] | {"correction_status": expected_status}
                assert (await client.get(receipt_path, headers=reviewer_headers)).status_code == 404
                different = await client.put(correction_path, json=correction | {
                    "reason_code": "other_reviewed_reason",
                }, headers=reviewer_headers)
                assert different.status_code == 409 and different.json()["detail"]["code"] == "ALLOCATION_ALREADY_CORRECTED"
                assert (await client.post(f"{lesson_path}/open")).status_code == 404
                async with postgres_session_factory() as db:
                    grants = (await db.execute(select(StudyCourseAccessGrant).where(
                        StudyCourseAccessGrant.org_id == org.id,
                    ).order_by(StudyCourseAccessGrant.id))).scalars().all()
                    original, installed = grants
                    assert original.learner_profile_id == profile.id and original.revoked_at is not None
                    assert installed.learner_profile_id == replacement.id and installed.starts_at is None
                    assert original.source_uuid == installed.source_uuid and installed.release_id == release.id
                    line = await db.get(StudyOrderLine, int(original.source_uuid))
                    assert line.learner_profile_id == profile.id
                    lineage = (await db.execute(select(StudyBusinessEvent).where(
                        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.operation_uuid == first.json()["correction_uuid"],
                    ))).scalar_one()
                    assert lineage.after_state["predecessor_grant_id"] == original.id
                    assert lineage.resource_uuid == str(installed.id)
                return
            if delay_remedy_policy:
                assert (await client.get(lesson_path)).status_code == 404
                assert (await client.get(correction_review, headers=reviewer_headers)).json() == {"status": "unstarted", "reason_codes": ["wrong_learner", "other_reviewed_reason"]}
            opened = await client.post(f"{lesson_path}/open")
            assert opened.status_code == 200, opened.text
            read = await client.get(lesson_path)
            assert read.status_code == 200 and read.json() == opened.json()
            assert read.json()["paragraphs"] == ["Retained lesson text."]
            if delay_remedy_policy:
                used = await client.get(correction_review, headers=reviewer_headers)
                assert used.status_code == 200 and used.json() == {"status": "used", "reason_codes": []}
            for path in (f"{org_path}/learning/access-history", f"{release_path}/access-history"):
                history = await client.get(path)
                assert history.status_code == (200 if history_approved else 404)
                assert history.headers["cache-control"] == "private, no-store"
            async with postgres_session_factory() as db:
                grant = (await db.execute(select(StudyCourseAccessGrant).where(
                    StudyCourseAccessGrant.org_id == org.id,
                ))).scalar_one()
                assert (grant.learner_profile_id, grant.release_id) == (profile.id, release.id)
                accepted_at = datetime.fromisoformat(awaiting.json()["accepted_at"])
                if delay_remedy_policy:
                    assert grant.start_basis == "after_first_access" and grant.starts_at >= accepted_at
                else:
                    assert grant.starts_at == accepted_at
                assert grant.ends_at - grant.starts_at == timedelta(days=1)
                issued = (await db.execute(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.dedupe_key == f"studytomy:course_grant:{org.id}:{grant.id}",
                ))).scalar_one()
                assert (policies[StudyPolicyKey.D_12].id in issued.policy_refs) == track_delivery
                delivered = (await db.execute(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.dedupe_key == f"studytomy:course_grant:delivered:{grant.id}",
                ))).scalar_one_or_none()
                if track_delivery:
                    assert delivered is not None
                    assert delivered.policy_refs == [policies[StudyPolicyKey.D_12].id]
                else:
                    assert delivered is None
                hold = (await db.execute(select(StudyCapacityHold).where(
                    StudyCapacityHold.request_uuid == ref,
                ))).scalar_one()
                assert hold.consumed_at is not None
                for ledger in (StudyPaymentAttempt, StudyMoneyMovement):
                    assert (await db.execute(select(ledger.id).where(ledger.org_id == org.id))).first() is None
            return
    async with postgres_session_factory() as db:
        order = (await db.execute(select(StudyOrder).where(StudyOrder.customer_ref == ref))).scalar_one()
        accepted = await db.get(StudyOrderAcceptance, order.id)
        assert accepted is not None and accepted.confirmation_sha256 == order.confirmation_sha256
        assert not (await db.execute(select(StudyPaymentAttempt.id).where(
            StudyPaymentAttempt.order_id == order.id,
        ))).first()
        await _grant_role_capabilities(
            db, org.id, owner.id, financial_reconciliation=True, refund_approval=True, family_authority_review=True,
        )
        await _seed_staff_scope(
            db, org.id, owners[StudyOwnerFunction.COMMERCIAL].id,
            StudytomyCapabilityName.FINANCIAL_RECONCILIATION, "collection", ref, purchaser.id,
        )
        await _seed_staff_scope(
            db, org.id, owners[StudyOwnerFunction.COMMERCIAL].id,
            StudytomyCapabilityName.REFUND_APPROVAL, "order", ref, purchaser.id,
        )
        await _seed_staff_scope(
            db, org.id, owners[StudyOwnerFunction.FAMILY_SERVICES].id,
            StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW, "learner", profile.learner_uuid, purchaser.id,
        )
        for delegate in (second_approver, verifier):
            await _grant_role_capabilities(db, org.id, delegate.id, refund_approval=True)
            assignment = await _seed_active_owner(
                db, org, delegate.id, owner.id, StudyOwnerFunction.COMMERCIAL,
                financial_limit_minor=125, financial_limit_currency="AUD",
                delegation_evidence_reference="SYNTHETIC", delegation_policy_version_id=policies[StudyPolicyKey.D_19].id,
            )
            await _seed_staff_scope(
                db, org.id, assignment.id, StudytomyCapabilityName.REFUND_APPROVAL, "order", ref, owner.id,
            )
        operator = PublicUser.model_validate(owner)
        attempt_refs = (uuid4(), uuid4(), uuid4())
        attempts = [
            await record_manual_collection_attempt(
                db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_ref,
            )
            for attempt_ref in attempt_refs
        ]
        assert (await record_manual_collection_attempt(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[0],
        )).id == attempts[0].id
        await db.flush()
        assert not (await db.execute(select(StudyMoneyMovement.id).where(
            StudyMoneyMovement.order_id == order.id,
        ))).first()

        def movement(attempt):
            return StudyMoneyMovement(
                org_id=org.id, order_id=order.id, attempt_id=attempt.id,
                amount_minor=125, currency="AUD", external_reference=f"bank-{token}",
                evidence_reference=f"synthetic-{token}", confirmed_by_user_id=owner.id,
            )

        confirmed = movement(attempts[0])
        with pytest.raises(DBAPIError, match="requires matching verified bank evidence"):
            async with db.begin_nested():
                db.add(confirmed)
                await db.flush()

        def bank_evidence(attempt):
            return StudyManualCollectionEvidence(
                org_id=org.id, order_id=order.id, attempt_id=attempt.id,
                amount_minor=125, currency="AUD", external_reference=f"bank-{token}",
                evidence_reference=f"synthetic-{token}", record_sha256="a" * 64,
                settled_at=datetime.now(UTC), verified_by_user_id=owner.id,
            )

        command = ManualBankEvidence(
            amount_minor=125, currency="AUD", external_reference=f"bank-{token}",
            evidence_reference=f"synthetic-{token}", record_sha256="a" * 64,
            settled_at=datetime.now(UTC),
        )
        evidence = await attest_manual_bank_evidence(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[0], command=command,
        )
        replay = await attest_manual_bank_evidence(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[0], command=command,
        )
        assert evidence.attempt_id == replay.attempt_id == attempts[0].id
        with pytest.raises(HTTPException) as unassigned:
            await attest_manual_bank_evidence(
                db, org, PublicUser.model_validate(purchaser),
                customer_ref=UUID(ref), attempt_reference=attempt_refs[1], command=command,
            )
        assert unassigned.value.status_code in (403, 404)
        assert not (await db.execute(select(StudyMoneyMovement.id).where(
            StudyMoneyMovement.order_id == order.id,
        ))).first()
        with pytest.raises(IntegrityError, match="uq_st_manual_evidence_external_reference"):
            async with db.begin_nested():
                db.add(bank_evidence(attempts[1]))
                await db.flush()
        await attest_manual_bank_evidence(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[1],
            command=command.model_copy(update={"external_reference": f"mismatch-{token}", "amount_minor": 1}),
        )
        mismatch = await confirm_manual_collection(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[1],
        )
        assert isinstance(mismatch, StudyCollectionRemedy) and mismatch.reason == "amount_or_currency_mismatch"
        assert (await confirm_manual_collection(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[1],
        )).attempt_id == mismatch.attempt_id
        hold = (await db.execute(select(StudyCapacityHold).where(
            StudyCapacityHold.request_uuid == ref,
        ))).scalar_one()
        await attest_manual_bank_evidence(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[2],
            command=command.model_copy(update={
                "external_reference": f"out-of-window-{token}",
                "settled_at": hold.created_at - timedelta(seconds=1),
            }),
        )
        out_of_window = await confirm_manual_collection(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[2],
        )
        assert isinstance(out_of_window, StudyCollectionRemedy)
        assert out_of_window.reason == "capacity_or_sale_unavailable"
        with pytest.raises(DBAPIError, match="Collection attempt requires remedy review"):
            async with db.begin_nested():
                blocked_movement = movement(attempts[1])
                blocked_movement.external_reference = f"mismatch-{token}"
                blocked_movement.amount_minor = 1
                db.add(blocked_movement)
                await db.flush()
        confirmed = await confirm_manual_collection(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[0],
        )
        assert isinstance(confirmed, StudyMoneyMovement)
        await db.refresh(hold)
        assert hold.consumed_at is not None
        assert confirmed.amount_minor == 125 and confirmed.currency == "AUD"
        assert (await confirm_manual_collection(
            db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[0],
        )).id == confirmed.id
        refund_values = {
            "org_id": org.id, "order_id": order.id, "movement_id": confirmed.id,
            "refund_reference": str(uuid4()), "amount_minor": 25, "currency": confirmed.currency,
            "reason_code": "synthetic_refund", "requested_by_user_id": purchaser.id,
            "approved_by_user_id": owner.id, "remedy_policy_id": policies[StudyPolicyKey.D_07].id,
            "finance_policy_id": policies[StudyPolicyKey.D_19].id,
        }
        with pytest.raises(IntegrityError, match="fk_st_refund_reservation_collection"):
            async with db.begin_nested():
                db.add(StudyRefundReservation(**(refund_values | {"currency": "NZD"})))
                await db.flush()
        async with db.begin_nested() as storage_fixture:
            reservation = StudyRefundReservation(**refund_values)
            db.add(reservation)
            await db.flush()
            with pytest.raises(IntegrityError, match="uq_st_refund_reservation_reference"):
                async with db.begin_nested():
                    db.add(StudyRefundReservation(**refund_values))
                    await db.flush()
            for operation in (
                update(StudyRefundReservation).values(amount_minor=1),
                delete(StudyRefundReservation),
            ):
                with pytest.raises(DBAPIError, match="Refund reservation history is immutable"):
                    async with db.begin_nested():
                        await db.execute(operation.where(StudyRefundReservation.id == reservation.id))
            await storage_fixture.rollback()
        with pytest.raises(HTTPException) as no_collection:
            await allocate_confirmed_order(
                db, org, operator, customer_ref=UUID(ref), attempt_reference=attempt_refs[1],
            )
        assert no_collection.value.detail["code"] == "COLLECTION_CONFIRMATION_REQUIRED"
        with pytest.raises(DBAPIError, match="Payment evidence history is immutable"):
            async with db.begin_nested():
                await db.execute(update(StudyMoneyMovement).where(
                    StudyMoneyMovement.id == confirmed.id,
                ).values(amount_minor=1))
        await db.commit()

    new_attempt = str(uuid4())
    collection_path = f"/api/v1/studytomy/orgs/{org.org_uuid}/collections/{ref}/manual-attempts/{new_attempt}"
    finance_access = mint_session_tokens(owner.email, amr="mfa", org_id=org.id).access_token
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.put(collection_path, headers=headers)
        assert denied.status_code in (403, 404)
        finance_headers = {"Authorization": f"Bearer {finance_access}"}
        foreign_receipt = await client.get(receipt_path, headers=finance_headers)
        assert foreign_receipt.status_code == 404
        assert foreign_receipt.headers["cache-control"] == "private, no-store"
        assert (await client.get(purchase_path, headers=finance_headers)).json() == {
            "orders": [], "next_cursor": None,
        }
        assert (await client.get(purchase_path, params={"after": ref}, headers=finance_headers)).status_code == 404
        paid = await client.get(receipt_path, headers=headers)
        assert paid.status_code == 200, paid.text
        assert paid.headers["cache-control"] == "private, no-store"
        assert paid.json()["collection_status"] == "confirmed"
        assert paid.json()["confirmed_minor"] == 125 and paid.json()["finance_review_required"] is True
        assert paid.json()["allocations"][0]["courses"] == ["Synthetic course"]
        assert profile.learner_uuid not in paid.text and f"bank-{token}" not in paid.text
        opened = await client.put(collection_path, headers=finance_headers)
        assert opened.status_code == 200, opened.text
        assert opened.json() == {"attempt_reference": new_attempt, "status": "pending"}
        statement = command.model_dump(mode="json")
        statement["external_reference"] = f"additional-{token}"
        reviewed = await client.put(collection_path + "/bank-evidence", json=statement, headers=finance_headers)
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json() == {"attempt_reference": new_attempt, "status": "evidence_attested"}
        remedy = await client.put(collection_path + "/confirmation", headers=finance_headers)
        assert remedy.status_code == 202, remedy.text
        assert remedy.headers["cache-control"] == "private, no-store"
        assert remedy.json() == {"attempt_reference": new_attempt, "status": "remedy_review"}
        queue = f"/api/v1/studytomy/orgs/{org.org_uuid}/collections/reviews"
        staff_page = await client.get(queue, params={"limit": 1}, headers=finance_headers)
        assert staff_page.status_code == 200, staff_page.text
        assert staff_page.headers["cache-control"] == "private, no-store"
        assert len(staff_page.json()["cases"]) == 1
        assert staff_page.json()["next_cursor"] == staff_page.json()["cases"][0]["attempt_reference"]
        next_page = await client.get(queue, params={"after": staff_page.json()["next_cursor"]}, headers=finance_headers)
        assert len(next_page.json()["cases"]) == 2
        assert (await client.get(queue, headers=headers)).json() == {"cases": [], "next_cursor": None}
        resolution = {
            "outcome": "returned_externally", "evidence_reference": f"synthetic-return-{token}",
            "record_sha256": "b" * 64,
        }
        unapproved = await client.put(collection_path + "/resolution", json={
            **resolution, "outcome": "settlement_not_received",
        }, headers=finance_headers)
        assert unapproved.status_code == 409
        assert unapproved.json()["detail"]["code"] == "COLLECTION_REMEDY_NOT_APPROVED"
        for _ in range(2):
            resolved = await client.put(collection_path + "/resolution", json=resolution, headers=finance_headers)
            assert resolved.status_code == 200, resolved.text
            assert resolved.json() == {"attempt_reference": new_attempt, "status": "resolved"}
        conflict = await client.put(collection_path + "/resolution", json={
            **resolution, "record_sha256": "c" * 64,
        }, headers=finance_headers)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "COLLECTION_RESOLUTION_CONFLICT"
        status = await client.put(collection_path + "/confirmation", headers=finance_headers)
        assert status.json()["status"] == "resolved"
        remaining = await client.get(queue, headers=finance_headers)
        assert {case["attempt_reference"] for case in remaining.json()["cases"]} == {
            str(attempt_refs[1]), str(attempt_refs[2]),
        }
        confirmed_path = collection_path.replace(new_attempt, str(attempt_refs[0]))
        refund_ids = [str(uuid4()) for _ in range(2)]
        refund_paths = [f"{confirmed_path}/refund-reservations/{refund_id}" for refund_id in refund_ids]
        request_paths = [f"{prefix}/orders/{ref}/refund-requests/{refund_id}" for refund_id in refund_ids]
        request_list_path = f"{prefix}/orders/{ref}/refund-requests"
        assert (await client.get(request_list_path, headers=headers)).json() == {"requests": [], "next_cursor": None}
        review_paths = [f"{queue.rsplit('/', 1)[0]}/{ref}/refund-requests/{refund_id}" for refund_id in refund_ids]
        refund = {"amount_minor": 65, "currency": "AUD", "reason_code": "synthetic_refund"}
        options = await client.get(options_path, headers=headers)
        assert options.status_code == 200 and options.headers["cache-control"] == "private, no-store"
        assert options.json() == {"currency": "AUD", "maximum_minor": 125, "reason_codes": ["synthetic_refund"]}
        assert (await client.get(options_path, headers=finance_headers)).status_code == 404
        known_denial = await client.put(refund_paths[0], json=refund, headers=headers)
        unknown_denial = await client.put(refund_paths[0].replace(ref, str(uuid4())), json=refund, headers=headers)
        assert known_denial.status_code == unknown_denial.status_code == 403
        assert known_denial.json() == unknown_denial.json()
        assert known_denial.headers["cache-control"] == "private, no-store"
        unassigned = await client.put(
            refund_paths[0].replace(ref, str(uuid4())), json=refund, headers=finance_headers,
        )
        assert unassigned.status_code == 404 and unassigned.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"
        unrequested = await client.put(refund_paths[0], json=refund, headers=finance_headers)
        assert unrequested.status_code == 409 and unrequested.json()["detail"]["code"] == "REFUND_REQUEST_REQUIRED"
        assert (await client.put(request_paths[0], json=refund, headers=finance_headers)).status_code == 404
        for request_path, refund_id in zip(request_paths, refund_ids, strict=True):
            requested = await client.put(request_path, json=refund, headers=headers)
            assert requested.status_code == 200 and requested.json() == {
                "refund_reference": refund_id, "status": "requested",
            }
        review = await client.get(review_paths[0], headers=finance_headers)
        assert review.status_code == 200 and review.headers["cache-control"] == "private, no-store"
        assert review.json() == refund | {
            "refund_reference": refund_ids[0], "attempt_reference": str(attempt_refs[0]), "status": "requested",
        }
        assert (await client.get(review_paths[0], headers=headers)).status_code == 403
        assert (await client.get(review_paths[0].replace(ref, str(uuid4())), headers=finance_headers)).status_code == 404
        assert (await client.put(request_paths[0], json=refund, headers=headers)).json() == {
            "refund_reference": refund_ids[0], "status": "requested",
        }
        changed = await client.put(refund_paths[0], json=refund | {"amount_minor": 59}, headers=finance_headers)
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "REFUND_REQUEST_REQUIRED"
        destination = await client.put(refund_paths[0], json=refund | {"destination": "other"}, headers=finance_headers)
        assert destination.status_code == 422
        for path in [*refund_paths, refund_paths[0]]:
            endorsement = await client.put(path, json=refund, headers=finance_headers)
            assert endorsement.status_code == 200, endorsement.text
            assert endorsement.json()["status"] == "awaiting_second_approval"
        async with postgres_session_factory() as db:
            count = (await db.execute(select(func.count()).select_from(StudyRefundReservation).where(
                StudyRefundReservation.org_id == org.id,
            ))).scalar_one()
            assert count == 0
            movement = (await db.execute(select(StudyMoneyMovement).where(
                StudyMoneyMovement.org_id == org.id,
            ))).scalar_one()
            adjustment = StudyChargebackAdjustment(
                org_id=org.id, order_id=movement.order_id, movement_id=movement.id,
                currency="AUD", amount_minor=61, external_reference=f"synthetic-debit-{token}",
                case_reference=f"synthetic-case-{token}", record_sha256="a" * 64, effective_at=datetime.now(UTC),
                verified_by_user_id=verifier.id, remedy_policy_id=policies[StudyPolicyKey.D_07].id,
                finance_policy_id=policies[StudyPolicyKey.D_19].id,
            )
            db.add(adjustment)
            await db.flush()
            with pytest.raises(HTTPException) as insufficient:
                await reserve_manual_refund(
                    db, org, PublicUser.model_validate(second_approver), customer_ref=UUID(ref),
                    attempt_reference=attempt_refs[0], refund_reference=UUID(refund_ids[0]),
                    command=RefundReservationRequest.model_validate(refund),
                )
            assert insufficient.value.detail["code"] == "REFUND_BALANCE_EXCEEDED"
            for statement in (
                update(StudyChargebackAdjustment).values(amount_minor=1),
                delete(StudyChargebackAdjustment),
            ):
                with pytest.raises(DBAPIError, match="Chargeback adjustment history is immutable"):
                    async with db.begin_nested():
                        await db.execute(statement.where(StudyChargebackAdjustment.id == adjustment.id))
            await db.rollback()
        second_access = mint_session_tokens(second_approver.email, amr="mfa", org_id=org.id).access_token
        second_headers = {"Authorization": f"Bearer {second_access}"}
        contested = await asyncio.gather(*[
            client.put(path, json=refund, headers=second_headers) for path in refund_paths
        ])
        assert sorted(result.status_code for result in contested) == [200, 409], [result.text for result in contested]
        winning_path, winning = next(
            (path, result) for path, result in zip(refund_paths, contested, strict=True) if result.status_code == 200
        )
        assert winning.json()["status"] == "reserved"
        winning_review_path = review_paths[refund_paths.index(winning_path)]
        pending_review = await client.get(winning_review_path, headers=finance_headers)
        assert pending_review.status_code == 200 and pending_review.json()["status"] == "reserved"
        async with postgres_session_factory() as db:
            audit_events = (await db.execute(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_uuid == winning.json()["refund_reference"],
                StudyBusinessEvent.action.in_(("studytomy.refund.first_approval", "studytomy.refund.reserved")),
            ))).scalars().all()
            events = {event.action: event for event in audit_events}
            assert len(audit_events) == 2
            first = events["studytomy.refund.first_approval"]
            reserved_event = events["studytomy.refund.reserved"]
            assert (first.actor_user_id, reserved_event.actor_user_id) == (owner.id, second_approver.id)
            assert reserved_event.after_state["refund_first_approval_uuid"] == first.event_uuid
            assert reserved_event.policy_refs == first.policy_refs
            reservation = (await db.execute(select(StudyRefundReservation).where(
                StudyRefundReservation.org_id == org.id,
                StudyRefundReservation.refund_reference == winning.json()["refund_reference"],
            ))).scalar_one()
            settlement_values = {
                "reservation_id": reservation.id, "org_id": org.id,
                "external_reference": f"synthetic-settlement-{token}", "evidence_reference": "synthetic-bank-evidence",
                "record_sha256": "d" * 64, "settled_at": datetime.now(UTC), "verified_by_user_id": owner.id,
                "remedy_policy_id": reservation.remedy_policy_id, "finance_policy_id": reservation.finance_policy_id,
            }
            with pytest.raises(DBAPIError, match="cannot precede reservation approval"):
                async with db.begin_nested():
                    db.add(StudyRefundSettlement(**(settlement_values | {
                        "settled_at": reservation.approved_at - timedelta(seconds=1),
                    })))
                    await db.flush()
        settle_path = f"{winning_path}/settlement"
        evidence = {
            key: settlement_values[key]
            for key in ("external_reference", "evidence_reference", "record_sha256", "settled_at")
        }
        evidence["settled_at"] = evidence["settled_at"].isoformat()
        evidence.update({"amount_minor": 65, "currency": "AUD"})
        verifier_access = mint_session_tokens(verifier.email, amr="mfa", org_id=org.id).access_token
        verifier_headers = {"Authorization": f"Bearer {verifier_access}"}
        mismatch = await client.put(settle_path, json=evidence | {"amount_minor": 59}, headers=verifier_headers)
        assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "REFUND_SETTLEMENT_AMOUNT_MISMATCH"
        assert (await client.put(settle_path, json=evidence, headers=headers)).status_code == 403
        for approver_headers in (finance_headers, second_headers):
            conflict_of_interest = await client.put(settle_path, json=evidence, headers=approver_headers)
            assert conflict_of_interest.status_code == 403
            assert conflict_of_interest.json()["detail"]["code"] == "REFUND_SETTLEMENT_CONFLICT_OF_INTEREST"
        if overlap_before_settlement:
            allocated = await client.put(confirmed_path + "/allocation", headers=finance_headers)
            assert allocated.status_code == 200, allocated.text
            early_debit = {
                "amount_minor": 70, "currency": "AUD", "external_reference": f"early-debit-{token}",
                "case_reference": f"early-case-{token}", "record_sha256": "b" * 64,
                "effective_at": datetime.now(UTC).isoformat(),
            }
            debit = await client.put(f"{confirmed_path}/chargeback", json=early_debit, headers=finance_headers)
            assert debit.status_code == 200 and debit.json()["review_required"] is True
        first_settlement, replayed_settlement = await asyncio.gather(*[
            client.put(settle_path, json=evidence, headers=verifier_headers) for _ in range(2)
        ])
        assert first_settlement.status_code == replayed_settlement.status_code == 200, first_settlement.text
        assert first_settlement.json() == replayed_settlement.json() == {
            "refund_reference": winning.json()["refund_reference"], "status": "settled",
            "review_required": overlap_before_settlement,
        }
        assert first_settlement.headers["cache-control"] == "private, no-store"
        if correction_scenario == "settled_refund":
            allocated = await client.put(confirmed_path + "/allocation", headers=finance_headers)
            assert allocated.status_code == 200, allocated.text
            async with postgres_session_factory() as db:
                replacement = StudyLearnerProfile(
                    org_id=org.id, user_id=second_approver.id, created_by_user_id=owner.id,
                    status="active", control_mode="dependent",
                )
                db.add(replacement)
                await db.flush()
                now = datetime.now(UTC)
                relationship_policies = [policies[key].id for key in (
                    StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13,
                )]
                db.add_all([
                    StudyRelationship(
                        org_id=org.id, learner_profile_id=replacement.id, adult_user_id=purchaser.id,
                        requested_by_user_id=purchaser.id, requested_scopes=["purchase_allocation"],
                        approved_scopes=["purchase_allocation"], status="accepted",
                        request_evidence_reference="SYNTHETIC", request_policy_version_ids=relationship_policies,
                        decision_policy_version_ids=relationship_policies, valid_from=now, valid_to=now + timedelta(days=1),
                        decided_by_user_id=owner.id, decision_owner_assignment_id=owners[StudyOwnerFunction.FAMILY_SERVICES].id,
                        decision_evidence_reference="SYNTHETIC", decision_reason_code="VERIFIED", decided_at=now,
                    ),
                    StudyEligibilityDecision(
                        org_id=org.id, learner_profile_id=replacement.id, eligibility_kind="learning", outcome="eligible",
                        policy_version_ids=[policies[key].id for key in (StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_12)],
                        evidence_reference="SYNTHETIC", effective_from=now, reviewer_user_id=owner.id,
                        owner_assignment_id=owners[StudyOwnerFunction.EDUCATION].id,
                    ),
                ])
                await _seed_staff_scope(
                    db, org.id, owners[StudyOwnerFunction.FAMILY_SERVICES].id,
                    StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW, "learner", replacement.learner_uuid, purchaser.id,
                )
            correction_path = f"/api/v1/studytomy/orgs/{org.org_uuid}/learners/{profile.learner_uuid}/allocations/{ref}/correction"
            preflight = await client.get(correction_path + "-review", headers=finance_headers)
            assert preflight.status_code == 200 and preflight.json()["status"] == "unstarted"
            denied = await client.put(correction_path, json={
                "replacement_learner_uuid": replacement.learner_uuid, "reason_code": "wrong_learner",
            }, headers=finance_headers)
            assert denied.status_code == 409 and denied.json()["detail"]["code"] == "ALLOCATION_FINANCE_REVIEW_REQUIRED"
            async with postgres_session_factory() as db:
                grant = (await db.execute(select(StudyCourseAccessGrant).where(
                    StudyCourseAccessGrant.org_id == org.id,
                ))).scalar_one()
                assert grant.learner_profile_id == profile.id and grant.revoked_at is None and grant.starts_at is None
                assert (await db.execute(select(StudyBusinessEvent.id).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.action == "studytomy.allocation.corrected",
                ))).first() is None
            return
        different = await client.put(settle_path, json=evidence | {"record_sha256": "f" * 64}, headers=verifier_headers)
        assert different.status_code == 409 and different.json()["detail"]["code"] == "REFUND_SETTLEMENT_CONFLICT"
        async with postgres_session_factory() as db:
            settlement = await db.get(StudyRefundSettlement, reservation.id)
            assert settlement.verified_by_user_id == verifier.id
            assert settlement.record_sha256 == evidence["record_sha256"]
            for statement in (update(StudyRefundSettlement).values(record_sha256="e" * 64), delete(StudyRefundSettlement)):
                with pytest.raises(DBAPIError, match="Refund settlement history is immutable"):
                    async with db.begin_nested():
                        await db.execute(statement.where(StudyRefundSettlement.reservation_id == reservation.id))
            if overlap_before_settlement:
                payment_event = (await db.execute(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == "studytomy.refund.settled",
                ))).scalar_one()
                assert payment_event.after_state["refund_access_effect"] == "review_required"
                retained_grant = (await db.execute(select(StudyCourseAccessGrant).where(
                    StudyCourseAccessGrant.org_id == org.id,
                ))).scalar_one()
                assert retained_grant.revoked_at is None
        if overlap_before_settlement:
            next_id = str(uuid4())
            next_refund = refund | {"amount_minor": 60}
            next_reservation = f"{confirmed_path}/refund-reservations/{next_id}"
            request = await client.put(f"{request_list_path}/{next_id}", json=next_refund, headers=headers)
            assert request.status_code == 200
            unavailable = await client.put(next_reservation, json=next_refund, headers=finance_headers)
            assert unavailable.status_code == 409 and unavailable.json()["detail"]["code"] == "REFUND_BALANCE_EXCEEDED"
            reversed_debit = await client.put(f"{confirmed_path}/chargeback-reversal", json=early_debit | {
                "debit_reference": early_debit["external_reference"],
                "external_reference": f"early-reversal-{token}", "effective_at": datetime.now(UTC).isoformat(),
            }, headers=finance_headers)
            assert reversed_debit.status_code == 200, reversed_debit.text
            first = await client.put(next_reservation, json=next_refund, headers=finance_headers)
            assert first.status_code == 200 and first.json()["status"] == "awaiting_second_approval"
            second = await client.put(next_reservation, json=next_refund, headers=second_headers)
            assert second.status_code == 200 and second.json()["status"] == "reserved"
            excess_id = str(uuid4())
            excess = refund | {"amount_minor": 1}
            assert (await client.put(f"{request_list_path}/{excess_id}", json=excess, headers=headers)).status_code == 200
            extra = await client.put(
                f"{confirmed_path}/refund-reservations/{excess_id}", json=excess, headers=finance_headers,
            )
            assert extra.status_code == 409 and extra.json()["detail"]["code"] == "REFUND_BALANCE_EXCEEDED"
            states = await client.get(request_list_path, headers=headers)
            assert {"refund_reference": next_id, "status": "reserved"} in states.json()["requests"]
            assert {"refund_reference": winning.json()["refund_reference"], "status": "settled"} in states.json()["requests"]
            return
        status = await client.get(request_list_path, params={"limit": 1}, headers=headers)
        assert status.status_code == 200 and status.headers["cache-control"] == "private, no-store"
        assert status.json()["next_cursor"] == refund_ids[1]
        next_status = await client.get(request_list_path, params={"after": refund_ids[1]}, headers=headers)
        assert next_status.status_code == 200 and next_status.json()["next_cursor"] is None
        assert status.json()["requests"] + next_status.json()["requests"] == [
            {"refund_reference": refund_id, "status": "settled" if refund_id == winning.json()["refund_reference"] else "requested"}
            for refund_id in reversed(refund_ids)
        ]
        assert (await client.get(request_list_path, headers=finance_headers)).status_code == 404
        assert (await client.get(request_list_path, params={"after": str(uuid4())}, headers=headers)).status_code == 404
        reviewed = await client.get(winning_review_path, headers=finance_headers)
        assert reviewed.status_code == 200 and reviewed.json()["status"] == "settled"
        assert winning.headers["cache-control"] == "private, no-store"
        assert next(result for result in contested if result.status_code == 409).json()["detail"]["code"] == "REFUND_BALANCE_EXCEEDED"
        retry = await client.put(winning_path, json=refund, headers=finance_headers)
        assert retry.json() == winning.json()
        conflict = await client.put(winning_path, json=refund | {"amount_minor": 59}, headers=finance_headers)
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "REFUND_REFERENCE_CONFLICT"
        replay = await client.put(confirmed_path + "/confirmation", headers=finance_headers)
        assert replay.status_code == 200
        assert replay.json() == {"attempt_reference": str(attempt_refs[0]), "status": "confirmed"}
        assert (await client.put(confirmed_path + "/allocation", headers=headers)).status_code in (403, 404)
        unconfirmed = await client.put(collection_path + "/allocation", headers=finance_headers)
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["detail"]["code"] == "COLLECTION_CONFIRMATION_REQUIRED"
        allocations = await asyncio.gather(*[
            client.put(confirmed_path + "/allocation", headers=finance_headers)
            for _ in range(2)
        ])
        for allocated in allocations:
            assert allocated.status_code == 200, allocated.text
            assert allocated.headers["cache-control"] == "private, no-store"
            assert allocated.json() == {"attempt_reference": str(attempt_refs[0]), "grant_count": 1}
        async with postgres_session_factory() as db:
            grant = (await db.execute(select(StudyCourseAccessGrant).where(
                StudyCourseAccessGrant.org_id == org.id,
            ))).scalar_one()
            assert (grant.learner_profile_id, grant.release_id) == (profile.id, release.id)
            if grant.start_basis == "after_first_access":
                assert grant.starts_at is None and grant.ends_at is None
            else:
                assert (grant.starts_at, grant.ends_at) == (
                    command.settled_at, command.settled_at + timedelta(days=1),
                )
        org_path = f"/api/v1/studytomy/orgs/{org.org_uuid}"
        overview_path = f"{org_path}/learning/courses/{course.course_uuid}/releases/{release.uuid}"
        catalog_path = f"{org_path}/learning/releases"
        consent_path = f"{org_path}/learners/me/consent-events"
        assert (await client.get(overview_path, headers=headers)).status_code == 404
        assert (await client.get(catalog_path, headers=headers)).status_code == 404
        consent = await client.post(consent_path, json={
            "purpose": "learning_terms", "choice": "accept", "notice_version": "fixture-v1",
            "scope_codes": ["course_release"],
        }, headers={**headers, "Idempotency-Key": "synthetic-course-consent"})
        assert consent.status_code == 201, consent.text
        overview = await client.get(overview_path, headers=headers)
        assert overview.status_code == 200, overview.text
        assert overview.json() == {
            "course_uuid": course.course_uuid, "release_uuid": release.uuid, "name": "Synthetic course",
        }
        assert overview.headers["cache-control"] == "private, no-store"
        catalog = await client.get(catalog_path, headers=headers)
        assert catalog.json() == {"releases": [overview.json()], "next_cursor": None}
        assert catalog.headers["cache-control"] == "private, no-store"
        after = await client.get(catalog_path, headers=headers, params={"after": release.uuid, "limit": 1})
        assert after.json() == {"releases": [], "next_cursor": None}
        assert (await client.get(catalog_path, headers=finance_headers)).status_code == 404
        lesson_path = f"{overview_path}/lessons/{activity_uuid}"
        correction_review = f"{org_path}/learners/{profile.learner_uuid}/allocations/{ref}/correction-review"
        before_use = await client.get(correction_review, headers=finance_headers)
        assert before_use.status_code == 200 and before_use.json() == {"status": "evidence_unavailable", "reason_codes": []}
        assert before_use.headers["cache-control"] == "private, no-store"
        assert (await client.get(correction_review, headers=headers)).status_code == 403
        assert (await client.get(correction_review.replace(profile.learner_uuid, str(uuid4())), headers=finance_headers)).status_code == 404
        history_path = f"{overview_path}/access-history"
        history = await client.get(history_path, headers=headers)
        assert history.status_code == 200 and history.headers["cache-control"] == "private, no-store"
        assert history.json() == overview.json() | {
            "status": "active", "start_basis": "after_purchase", "duration_days": None, "completion_before_window": None,
            "starts_at": command.settled_at.isoformat().replace("+00:00", "Z"),
            "ends_at": (command.settled_at + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        }
        assert (await client.get(history_path, headers=finance_headers)).status_code == 404
        delivery = select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.action == "studytomy.course_grant.content_delivered",
        )
        assert (await client.get(f"{overview_path}/lessons/{assessment_uuid}", headers=headers)).status_code == 404
        async with postgres_session_factory() as db:
            assert (await db.execute(delivery)).first() is None
        lesson, replayed_lesson = await asyncio.gather(
            client.get(lesson_path, headers=headers), client.get(lesson_path, headers=headers),
        )
        assert lesson.status_code == 200, lesson.text
        assert replayed_lesson.status_code == 200 and replayed_lesson.json() == lesson.json()
        after_use = await client.get(correction_review, headers=finance_headers)
        assert after_use.status_code == 200 and after_use.json() == {"status": "used", "reason_codes": []}
        async with postgres_session_factory() as db:
            delivered = (await db.execute(delivery)).scalar_one()
            assert delivered.resource_uuid == str(grant.id)
            assert delivered.actor_user_id == purchaser.id
            assert delivered.after_state is None
            issued = (await db.execute(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.dedupe_key == f"studytomy:course_grant:{org.id}:{grant.id}",
            ))).scalar_one()
            assert delivered.policy_refs == [policies[StudyPolicyKey.D_12].id]
            assert delivered.policy_refs[0] in issued.policy_refs
        assert lesson.json() == {
            "course_uuid": course.course_uuid, "release_uuid": release.uuid,
            "activity_uuid": activity_uuid, "name": "Reviewed lesson", "paragraphs": ["Retained lesson text."],
        }
        lesson_index = await client.get(f"{overview_path}/lessons", headers=headers)
        assert lesson_index.status_code == 200
        assert lesson_index.json() == {
            "requires_first_use": False,
            "lessons": [{
                "activity_uuid": activity_uuid,
                "name": "Reviewed lesson",
                "position": 1,
                "format": "text",
                "media_type": None,
                "available": True,
                "unavailable_reason": None,
            }],
        }
        assert (await client.get(overview_path, headers=finance_headers)).status_code == 404
        async with postgres_session_factory() as db:
            assert (await db.get(StudyCourseAccessGrant, grant.id)).revoked_at is None
            now = datetime.now(UTC)
            other_grant = StudyCourseAccessGrant(
                org_id=org.id, course_uuid=course.course_uuid, release_id=release.id,
                learner_profile_id=profile.id, source_kind="free", source_uuid=f"independent-{token}",
                start_basis="fixed_period", starts_at=now, ends_at=now + timedelta(hours=1),
            )
            db.add(other_grant)
            await db.commit()
        remainder_id = str(uuid4())
        remainder = refund | {"amount_minor": 60}
        remainder_request = await client.put(
            f"{request_list_path}/{remainder_id}", json=remainder, headers=headers,
        )
        assert remainder_request.status_code == 200, remainder_request.text
        remainder_path = f"{confirmed_path}/refund-reservations/{remainder_id}"
        assert (await client.put(remainder_path, json=remainder, headers=finance_headers)).json()["status"] == "awaiting_second_approval"
        assert (await client.put(remainder_path, json=remainder, headers=second_headers)).json()["status"] == "reserved"
        full_return = await client.put(f"{remainder_path}/settlement", json=evidence | {
            "amount_minor": 60, "external_reference": f"remainder-{token}", "settled_at": datetime.now(UTC).isoformat(),
        }, headers=verifier_headers)
        assert full_return.status_code == 200 and full_return.json()["status"] == "settled", full_return.text
        for attempt_ref in attempt_refs[1:]:
            resolution_path = f"{collection_path.replace(new_attempt, str(attempt_ref))}/resolution"
            cleared = await client.put(resolution_path, json=resolution | {
                "evidence_reference": f"synthetic-closed-{attempt_ref}",
            }, headers=finance_headers)
            assert cleared.status_code == 200, cleared.text
        before_overlap = await client.get(receipt_path, headers=headers)
        assert before_overlap.status_code == 200 and before_overlap.json()["finance_review_required"] is False
        overlap_path = f"{confirmed_path}/overlap-review"
        balanced = await client.get(overlap_path, headers=finance_headers)
        assert balanced.status_code == 200 and balanced.json()["review_required"] is False
        assert (await client.get(overlap_path, headers=headers)).status_code in (403, 404)
        chargeback_path = f"{confirmed_path}/chargeback"
        debit = {
            "amount_minor": 20, "currency": "AUD", "external_reference": f"external-debit-{token}",
            "case_reference": f"debit-case-{token}", "record_sha256": "b" * 64,
            "effective_at": datetime.now(UTC).isoformat(),
        }
        unauthorized_debit = await client.put(chargeback_path, json=debit, headers=headers)
        assert unauthorized_debit.status_code in (403, 404)
        confirmed_debits = await asyncio.gather(*[
            client.put(chargeback_path, json=debit, headers=finance_headers) for _ in range(2)
        ])
        for debit_response in confirmed_debits:
            assert debit_response.status_code == 200, debit_response.text
            assert debit_response.json() == {"status": "recorded", "review_required": True}
            assert debit_response.headers["cache-control"] == "private, no-store"
        altered_debit = await client.put(chargeback_path, json=debit | {"amount_minor": 21}, headers=finance_headers)
        assert altered_debit.status_code == 409
        assert altered_debit.json()["detail"]["code"] == "CHARGEBACK_REFERENCE_CONFLICT"
        overlap = await client.get(overlap_path, headers=finance_headers)
        assert overlap.status_code == 200 and overlap.headers["cache-control"] == "private, no-store"
        snapshot = overlap.json()["snapshot_sha256"]
        assert overlap.json() == {
            "currency": "AUD", "confirmed_minor": 125, "reserved_minor": 0, "settled_minor": 125,
            "chargeback_minor": 20, "review_required": True, "acknowledged": False, "snapshot_sha256": snapshot,
        }
        stale_review = await client.put(overlap_path, json={
            "snapshot_sha256": balanced.json()["snapshot_sha256"],
        }, headers=finance_headers)
        assert stale_review.status_code == 409 and stale_review.json()["detail"]["code"] == "OVERLAP_REVIEW_STALE"
        acknowledgements = await asyncio.gather(*[
            client.put(overlap_path, json={"snapshot_sha256": snapshot}, headers=finance_headers)
            for _ in range(2)
        ])
        assert all(result.status_code == 200 for result in acknowledgements)
        assert all(result.json() == overlap.json() | {"acknowledged": True} for result in acknowledgements)
        after_overlap = await client.get(receipt_path, headers=headers)
        assert after_overlap.status_code == 200
        assert after_overlap.json() == before_overlap.json() | {"finance_review_required": True}
        reversal_path = f"{confirmed_path}/chargeback-reversal"
        reversal = debit | {
            "debit_reference": debit["external_reference"], "external_reference": f"reversal-{token}",
            "effective_at": datetime.now(UTC).isoformat(),
        }
        unmatched = await client.put(reversal_path, json=reversal | {"amount_minor": 19}, headers=finance_headers)
        assert unmatched.status_code == 409 and unmatched.json()["detail"]["code"] == "CHARGEBACK_REVERSAL_MISMATCH"
        replays = await asyncio.gather(*[
            client.put(reversal_path, json=reversal, headers=finance_headers) for _ in range(2)
        ])
        assert all(result.status_code == 200 and result.json() == {"status": "reversed"} for result in replays)
        cleared_overlap = await client.get(overlap_path, headers=finance_headers)
        assert cleared_overlap.json()["review_required"] is False
        assert cleared_overlap.json()["chargeback_minor"] == 0
        assert cleared_overlap.json()["snapshot_sha256"] not in (snapshot, balanced.json()["snapshot_sha256"])
        assert (await client.get(receipt_path, headers=headers)).json() == before_overlap.json()
        denied_reissue = await client.put(confirmed_path + "/allocation", headers=finance_headers)
        assert denied_reissue.status_code == 409 and denied_reissue.json()["detail"]["code"] == "ORDER_ACCESS_ENDED"
        assert (await client.get(lesson_path, headers=headers)).status_code == 200
        async with postgres_session_factory() as db:
            reviewed = (await db.execute(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.action == "studytomy.chargeback.review_acknowledged",
            ))).scalar_one()
            assert reviewed.after_state["source_snapshot_digest"] == snapshot
            saved_debit = (await db.execute(select(StudyChargebackAdjustment).where(
                StudyChargebackAdjustment.org_id == org.id,
            ))).scalar_one()
            assert saved_debit.amount_minor == 20 and saved_debit.case_reference == debit["case_reference"]
            saved_reversal = await db.get(StudyChargebackReversal, saved_debit.id)
            assert saved_reversal is not None and saved_reversal.external_reference == reversal["external_reference"]
            from src.services.studytomy.metrics import confirmed_cash_by_currency

            end = datetime.now(UTC) + timedelta(seconds=1)
            period = {"start": end - timedelta(days=30), "end": end}
            expected_cash = [{
                "currency": "AUD", "collected_minor": 125, "refunded_minor": 125,
                "chargeback_minor": 20, "reversed_chargeback_minor": 20, "net_minor": 0,
                "collection_count": 1, "settled_refund_count": 2,
                "chargeback_count": 1, "chargeback_reversal_count": 1,
            }]
            assert await confirmed_cash_by_currency(db, org_id=org.id, **period) == expected_cash
            assert await confirmed_cash_by_currency(
                db, org_id=org.id, offer_uuid=offer_uuid, **period,
            ) == expected_cash
            assert await confirmed_cash_by_currency(
                db, org_id=org.id, offer_uuid=str(uuid4()), **period,
            ) == []
            report_path = f"{org_path}/pilot/offers/{offer_uuid}/versions/{version_uuid}/confirmed-cash"
            options_path = f"{org_path}/pilot/cash-report-offers"
            report_period = {"start": period["start"].isoformat(), "end": datetime.now(UTC).isoformat()}
            denied_report = await client.get(report_path, params=report_period, headers=finance_headers)
            assert denied_report.status_code == 403
            assert (await client.get(options_path, headers=finance_headers)).json() == []
            await _seed_staff_scope(
                db, org.id, owners[StudyOwnerFunction.COMMERCIAL].id,
                StudytomyCapabilityName.FINANCIAL_RECONCILIATION, "offer", offer_uuid, purchaser.id,
            )
            report = await client.get(report_path, params=report_period, headers=finance_headers)
            options = await client.get(options_path, headers=finance_headers)
            assert options.status_code == 200
            assert options.headers["cache-control"] == "private, no-store"
            assert options.json() == [{
                "offer_uuid": offer_uuid, "offer_version_uuid": version_uuid,
                "title": terms.title, "version": 1,
            }]
            assert (await client.get(options_path, headers=headers)).json() == []
            assert report.status_code == 200, report.text
            assert report.headers["cache-control"] == "private, no-store"
            assert report.json()["confirmed_cash"] == expected_cash
            assert report.json()["settled_refunds_by_reason"] == [{
                "currency": "AUD", "reason_code": "synthetic_refund",
                "settlement_count": 2, "refunded_minor": 125,
            }]
            assert report.json()["offer_version_uuid"] == version_uuid
            assert (await client.get(report_path, params=report_period, headers=headers)).status_code == 403
            wrong_version = await client.get(
                report_path.replace(version_uuid, str(uuid4())), params=report_period, headers=finance_headers,
            )
            assert wrong_version.status_code == 409
            assert wrong_version.json()["detail"]["code"] == "OFFER_SALES_PAUSED"
            assert await confirmed_cash_by_currency(
                db, org_id=org.id, start=saved_reversal.effective_at, end=end,
            ) == [{
                "currency": "AUD", "collected_minor": 0, "refunded_minor": 0,
                "chargeback_minor": 0, "reversed_chargeback_minor": 20, "net_minor": 20,
                "collection_count": 0, "settled_refund_count": 0,
                "chargeback_count": 0, "chargeback_reversal_count": 1,
            }]
            assert await confirmed_cash_by_currency(
                db, org_id=org.id, start=end - timedelta(days=30), end=saved_reversal.effective_at,
            ) == [{
                "currency": "AUD", "collected_minor": 125, "refunded_minor": 125,
                "chargeback_minor": 20, "reversed_chargeback_minor": 0, "net_minor": -20,
                "collection_count": 1, "settled_refund_count": 2,
                "chargeback_count": 1, "chargeback_reversal_count": 0,
            }]
            empty_org = Organization(
                name="Synthetic empty ledger", slug=f"empty-{token}",
                org_uuid=f"org_empty_{token}", email=f"empty-{token}@example.invalid",
            )
            db.add(empty_org)
            await db.flush()
            assert await confirmed_cash_by_currency(
                db, org_id=empty_org.id, start=end - timedelta(days=30), end=end,
            ) == []
            assert await confirmed_cash_by_currency(
                db, org_id=empty_org.id, offer_uuid=offer_uuid, **period,
            ) == []
            assert (await db.get(StudyCourseAccessGrant, grant.id)).revoked_at is not None
            retained = await db.get(StudyCourseAccessGrant, other_grant.id)
            assert retained.revoked_at is None
            retained.revoked_at = datetime.now(UTC)
            pending = StudyCourseAccessGrant(
                org_id=org.id, course_uuid=course.course_uuid, release_id=release.id,
                learner_profile_id=profile.id, source_kind="free", source_uuid=f"fixture-{token}",
                start_basis="after_first_access", duration_days=1,
            )
            db.add(pending)
            await db.commit()
        assert (await client.get(lesson_path, headers=headers)).status_code == 404
        assert (await client.get(catalog_path, headers=headers)).json()["releases"] == [overview.json()]
        before_open = await client.get(f"{overview_path}/lessons", headers=headers)
        assert before_open.json()["requires_first_use"] is True
        invalid = await client.post(f"{overview_path}/lessons/{assessment_uuid}/open", headers=headers)
        assert invalid.status_code == 404
        async with postgres_session_factory() as db:
            assert (await db.get(StudyCourseAccessGrant, pending.id)).starts_at is None
        opened = await asyncio.gather(*[
            client.post(f"{lesson_path}/open", headers=headers) for _ in range(2)
        ])
        assert [result.status_code for result in opened] == [200, 200]
        assert all(result.json() == lesson.json() for result in opened)
        async with postgres_session_factory() as db:
            claimed = await db.get(StudyCourseAccessGrant, pending.id)
            assert claimed.starts_at is not None and claimed.ends_at - claimed.starts_at == timedelta(days=1)
        assert (await client.get(lesson_path, headers=headers)).status_code == 200
        assert (await client.get(f"{overview_path}/lessons", headers=headers)).json()["requires_first_use"] is False
        award_uuid = f"award_{token}"
        async with postgres_session_factory() as db:
            certificate = Certifications(course_id=course.id, certification_uuid=f"cert_{token}")
            db.add(certificate)
            await db.flush()
            db.add(CertificateUser(
                user_id=profile.user_id, certification_id=certificate.id,
                user_certification_uuid=award_uuid,
            ))
            await db.commit()
        awards_path = "/api/v1/certifications/user/all"
        own_awards = await client.get(awards_path, params={"org_id": org.id}, headers=headers)
        assert own_awards.status_code == 200, own_awards.text
        assert [entry["certificate_user"]["user_certification_uuid"] for entry in own_awards.json()] == [award_uuid]
        async with postgres_session_factory() as db:
            now = datetime.now(UTC)
            await db.execute(update(StudyCourseAccessGrant).where(
                StudyCourseAccessGrant.id == pending.id,
            ).values(revoked_at=now))
            db.add(StudyCourseAccessGrant(
                org_id=org.id, course_uuid=course.course_uuid, release_id=release.id,
                learner_profile_id=profile.id, source_kind="provisional", source_uuid=f"expired-{token}",
                start_basis="fixed_period", starts_at=now-timedelta(days=2),
                ends_at=now-timedelta(days=1), created_at=now-timedelta(days=2),
            ))
            await db.commit()
        expired = await client.get(history_path, headers=headers)
        assert expired.status_code == 200 and expired.json()["status"] == "expired"
        assert expired.json()["start_basis"] == "fixed_period"
        assert (await client.get(lesson_path, headers=headers)).status_code == 404
        retained_awards = await client.get(awards_path, params={"org_id": org.id}, headers=headers)
        assert retained_awards.status_code == 200, retained_awards.text
        assert retained_awards.json() == own_awards.json()
        monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_SUPPORT_INTAKE_ENABLED", "true")
        support_path = f"{org_path}/support/cases"
        support_case = await client.post(
            support_path, headers=headers, json={"topic": "Orders", "request_id": str(uuid4())},
        )
        assert support_case.status_code == 201, support_case.text
        receipt = support_case.json()
        assert receipt == {"case_uuid": receipt["case_uuid"], "topic": "Orders", "status": "assigned"}
        assert (await client.get(f"{support_path}/{receipt['case_uuid']}", headers=headers)).json() == receipt
        assert (await client.get(lesson_path, headers=headers)).status_code == 404
        history_list = f"{org_path}/learning/access-history"
        retained = await client.get(history_list, headers=headers)
        assert retained.status_code == 200 and retained.headers["cache-control"] == "private, no-store"
        assert retained.json() == {"releases": [overview.json()], "next_cursor": None}
        assert (await client.get(history_list, params={"after": release.uuid}, headers=headers)).json() == {
            "releases": [], "next_cursor": None,
        }
        assert (await client.get(catalog_path, headers=headers)).json() == {"releases": [], "next_cursor": None}
        assert (await client.get(history_list, headers=finance_headers)).status_code == 404
        withdrawn = await client.post(
            f"{consent_path}/{consent.json()['event_uuid']}/withdraw",
            headers={**headers, "Idempotency-Key": "synthetic-course-withdraw"},
        )
        assert withdrawn.status_code == 201, withdrawn.text
        assert (await client.get(history_path, headers=headers)).status_code == 404
        assert (await client.get(history_list, headers=headers)).status_code == 404
        assert (await client.get(overview_path, headers=headers)).status_code == 404
        assert (await client.get(lesson_path, headers=headers)).status_code == 404
        assert (await client.post(f"{lesson_path}/open", headers=headers)).status_code == 404
        assert (await client.get(f"{overview_path}/lessons", headers=headers)).status_code == 404
        assert (await client.get(catalog_path, headers=headers)).status_code == 404
