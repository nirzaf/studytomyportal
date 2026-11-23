import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import select

from src.core.events.database import get_db_session
from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.assignments import AssignmentUserSubmission
from src.db.courses.courses import Course
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.practice import StudyLearningAction, StudyLearningSurface
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.routers.ai.ai import router as ai_router
from src.security.auth import get_authenticated_user
from src.services.ai import learning_actions as actions
from src.services.ai import learning_generation as generation
from src.services.ai import learning_practice as practice
from src.services.ai.schemas.learning_surface import LearningSurface, PracticeAction, SubmitAnswer
from src.tests.studytomy.test_studytomy_access import _synthetic_policy_body


@pytest.mark.studytomy_postgres
async def test_practice_recovery_rechecks_source_and_owner(postgres_test_rows, monkeypatch):
    rows = postgres_test_rows
    request = Request({"type": "http", "headers": []})
    stamp = "2026-11-16T00:00:00Z"
    other_id = None
    other_org_id = None
    retention_policy_id = None
    try:
        async with rows.session_factory() as db, db.begin():
            owner = PublicUser.model_validate(await db.get(User, rows.user_id))
            organization = await db.get(Organization, rows.org_id)
            retention_policy = StudyPolicyVersion(
                org_id=rows.org_id, policy_key="D-13", scope_kind="organization",
                scope_uuid=organization.org_uuid, version=1, status="draft", body={},
                evidence_reference="synthetic policy foreign-key fixture",
                effective_from=datetime(2026, 11, 1, tzinfo=UTC), created_by_user_id=owner.id,
            )
            db.add(retention_policy)
            await db.flush()
            retention_policy_id = retention_policy.id
            other_user = User(
                username=f"peer_{rows.token}", first_name="Synthetic", last_name="Peer",
                email=f"peer-{rows.token}@example.com", user_uuid=f"user_{uuid4()}",
                creation_date=stamp, update_date=stamp,
            )
            role = Role(
                name="Practice test member", org_id=rows.org_id,
                role_type=RoleTypeEnum.TYPE_ORGANIZATION, role_uuid=f"role_{uuid4()}",
            )
            course = Course(
                name="Synthetic fractions", org_id=rows.org_id, course_uuid=f"course_{uuid4()}",
                open_to_contributors=False, public=True, published=True,
            )
            db.add_all([other_user, role, course])
            db.add(OrganizationConfig(org_id=rows.org_id, config={"features": {"ai": {"enabled": True}}}))
            await db.flush()
            other = PublicUser.model_validate(other_user)
            other_id = other.id
            for viewer in (owner, other):
                db.add(UserOrganization(
                    org_id=rows.org_id, user_id=viewer.id, role_id=role.id,
                    creation_date=stamp, update_date=stamp,
                ))
            lesson = Activity(
                name="Fractions", org_id=rows.org_id, course_id=course.id,
                activity_uuid=f"activity_{uuid4()}", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
                activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, published=True,
                content={"type": "doc", "content": []},
            )
            db.add(lesson)
            assignment_activity_uuid = f"activity_{uuid4()}"
            db.add(Activity(
                name="Synthetic graded assessment", org_id=rows.org_id, course_id=course.id,
                activity_uuid=assignment_activity_uuid, activity_type=ActivityTypeEnum.TYPE_ASSIGNMENT,
                activity_sub_type=ActivitySubTypeEnum.SUBTYPE_ASSIGNMENT_ANY, published=True,
            ))
            await db.flush()
            lesson_id, activity_uuid = lesson.id, lesson.activity_uuid
            other_org = Organization(
                name="Synthetic second organization", slug=f"other-{rows.token}",
                email=f"other-{rows.token}@example.com", org_uuid=f"org_{uuid4()}",
                creation_date=stamp, update_date=stamp,
            )
            db.add(other_org)
            await db.flush()
            other_org_id = other_org.id
            db.add(OrganizationConfig(org_id=other_org_id, config={"features": {"ai": {"enabled": True}}}))
            other_role = Role(
                name="Second organization member", org_id=other_org_id,
                role_type=RoleTypeEnum.TYPE_ORGANIZATION, role_uuid=f"role_{uuid4()}",
            )
            other_course = Course(
                name="Other fractions", org_id=other_org_id, course_uuid=f"course_{uuid4()}",
                open_to_contributors=False, public=True, published=True,
            )
            db.add_all([other_role, other_course])
            await db.flush()
            db.add(UserOrganization(
                org_id=other_org_id, user_id=owner.id, role_id=other_role.id,
                creation_date=stamp, update_date=stamp,
            ))
            other_activity_uuid = f"activity_{uuid4()}"
            db.add(Activity(
                name="Other fractions", org_id=other_org_id, course_id=other_course.id,
                activity_uuid=other_activity_uuid, activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
                activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, published=True,
                content={"type": "doc", "content": []},
            ))

        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as blocked:
                await practice._authorize_source(request, db, owner, activity_uuid)
            assert blocked.value.status_code in (403, 404, 409)

        async def allow_only_synthetic_orgs(db, viewer, source):
            assert source.org_id in {rows.org_id, other_org_id}
            example = practice.ReviewedFractionExample(
                example_id="fraction_half", visual="fraction_explorer", parts=4, selected=2,
                numerator=1, denominator=2, prompt="What fraction of the whole is selected?",
                explanation="Two of four equal parts are selected.", hint="Count selected and total parts.",
                correct_feedback="Two of four is one half.", incorrect_feedback="Compare selected and total parts.",
            )
            return practice.PracticePermission(
                frozenset({"fraction_explorer"}), allow_hints=True, approved_examples=(example,),
                allow_practice=True, approved_model="fake", retention_policy_id=retention_policy_id,
                retention_category="lesson_visual_practice", retention_hours=1,
            )

        monkeypatch.setattr(practice, "require_learning_practice_policy", allow_only_synthetic_orgs)

        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as graded:
                await practice.get_learning_availability(request, db, owner, assignment_activity_uuid)
            assert graded.value.status_code == 404

        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "learning_surface_v1.json"
        sample = json.loads(fixture.read_text(encoding="utf-8"))
        sample["components"][0]["text"] = "Two of four equal parts are selected."
        sample["components"][2]["question"]["prompt"] = "What fraction of the whole is selected?"
        basis = practice.FractionEvaluationBasis(
            template="equivalent_fractions_v1", numerator=1, denominator=2, reviewed_example_id="fraction_half",
        )
        async with rows.session_factory() as db, db.begin():
            source = await practice._authorize_source(request, db, owner, activity_uuid)
            sample.update(activity_id=activity_uuid, source_identity=source.identity, surface_id=str(uuid4()))
            invalid_sample = json.loads(json.dumps(sample))
            invalid_sample["components"][0]["text"] = "An unreviewed explanation must not be persisted."
            invalid_surface = LearningSurface.model_validate(invalid_sample)
            with pytest.raises(HTTPException) as unreviewed:
                await practice.save_learning_surface(request, db, owner, invalid_surface, basis)
            assert unreviewed.value.detail["code"] == "POLICY_NOT_APPROVED"
            surface = LearningSurface.model_validate(sample)
            await practice.save_learning_surface(request, db, owner, surface, basis)

        async with rows.session_factory() as db:
            stored = await practice.recover_learning_surface(request, db, owner, activity_uuid, surface.surface_id)
            assert stored.model_dump(mode="json") == sample
            row = await db.get(StudyLearningSurface, str(surface.surface_id))
            assert row.evaluation_basis == basis.model_dump()
            assert row.retention_policy_id == retention_policy_id
            assert row.retention_expires_at is not None
            with pytest.raises(HTTPException) as hidden:
                await practice.recover_learning_surface(request, db, other, activity_uuid, surface.surface_id)
            assert hidden.value.status_code == 404
            assert (await practice.get_learning_availability(request, db, other, activity_uuid)).surface is None
            with pytest.raises(HTTPException) as wrong_org:
                await practice.recover_learning_surface(request, db, owner, other_activity_uuid, surface.surface_id)
            assert wrong_org.value.status_code == 404
            assert (await db.execute(select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.user_id == owner.id,
            ))).scalars().all() == []

        expired_surface_uuid = str(uuid4())
        expired_at = datetime.now(UTC) - timedelta(days=1)
        async with rows.session_factory() as db, db.begin():
            db.add(StudyLearningSurface(
                surface_uuid=expired_surface_uuid,
                org_id=rows.org_id,
                user_id=owner.id,
                request_uuid=str(uuid4()),
                course_uuid=course.course_uuid,
                activity_uuid=activity_uuid,
                source_identity=surface.source_identity,
                presentation=sample,
                evaluation_basis=basis.model_dump(),
                retention_policy_id=retention_policy_id,
                retention_category="lesson_visual_practice",
                retention_expires_at=expired_at,
                created_at=expired_at - timedelta(days=1),
            ))
        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as expired:
                await practice.recover_learning_surface(request, db, owner, activity_uuid, UUID(expired_surface_uuid))
            assert expired.value.status_code == 410
            assert expired.value.detail["code"] == "PRACTICE_RECORD_EXPIRED"

        committed_answer = SubmitAnswer(
            action="submit_answer", surface_id=surface.surface_id, request_id=uuid4(),
            expected_revision=1, answer="2/4",
        )
        async with rows.session_factory() as db:
            accepted = await actions.apply_learning_action(request, db, owner, activity_uuid, committed_answer)
            assert accepted.revision == 2

        async with rows.session_factory() as db, db.begin():
            await db.execute(update(OrganizationConfig).where(OrganizationConfig.org_id == rows.org_id).values(
                config={"features": {"ai": {"enabled": False}}},
            ))
        async with rows.session_factory() as db:
            for operation in (
                lambda: practice.get_learning_availability(request, db, owner, activity_uuid),
                lambda: practice.recover_learning_surface(request, db, owner, activity_uuid, surface.surface_id),
                lambda: actions.apply_learning_action(request, db, owner, activity_uuid, committed_answer),
                lambda: actions.apply_learning_action(request, db, owner, activity_uuid, PracticeAction(
                    action="request_hint", surface_id=surface.surface_id, request_id=uuid4(), expected_revision=1,
                )),
            ):
                with pytest.raises(HTTPException) as disabled:
                    await operation()
                assert disabled.value.detail["code"] == "PRACTICE_AI_DISABLED"
            assert await db.get(StudyLearningSurface, str(surface.surface_id)) is not None
        async with rows.session_factory() as db, db.begin():
            await db.execute(update(OrganizationConfig).where(OrganizationConfig.org_id == rows.org_id).values(
                config={"features": {"ai": {"enabled": True}}},
            ))

        with pytest.raises(IntegrityError):
            async with rows.session_factory() as db, db.begin():
                duplicate = surface.model_copy(update={"surface_id": uuid4()})
                await practice.save_learning_surface(request, db, owner, duplicate, basis)

        async with rows.session_factory() as db, db.begin():
            lesson = await db.get(Activity, lesson_id)
            lesson.current_version += 1
        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as stale:
                await practice.recover_learning_surface(request, db, owner, activity_uuid, surface.surface_id)
            assert stale.value.detail["code"] == "STALE_VERSION"

        async with rows.session_factory() as db, db.begin():
            await db.execute(delete(UserOrganization).where(
                UserOrganization.org_id == rows.org_id, UserOrganization.user_id == rows.user_id,
            ))
        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as revoked:
                await practice.recover_learning_surface(request, db, owner, activity_uuid, surface.surface_id)
            assert revoked.value.status_code == 403
            with pytest.raises(HTTPException) as replay_denied:
                await actions.apply_learning_action(request, db, owner, activity_uuid, committed_answer)
            assert replay_denied.value.status_code == 403
            recorded = (await db.execute(
                select(StudyLearningAction).where(StudyLearningAction.surface_uuid == str(surface.surface_id))
            )).scalar_one()
            assert recorded.action == "submit_answer"
    finally:
        async with rows.session_factory() as db, db.begin():
            await db.execute(delete(StudyLearningAction).where(StudyLearningAction.surface_uuid.in_(
                select(StudyLearningSurface.surface_uuid).where(StudyLearningSurface.org_id == rows.org_id)
            )))
            await db.execute(delete(StudyLearningSurface).where(StudyLearningSurface.org_id == rows.org_id))
            if retention_policy_id is not None:
                await db.execute(delete(StudyPolicyVersion).where(StudyPolicyVersion.id == retention_policy_id))
            if other_id is not None:
                await db.execute(delete(User).where(User.id == other_id))
            if other_org_id is not None:
                await db.execute(delete(Organization).where(Organization.id == other_org_id))


@pytest.mark.studytomy_postgres
async def test_fake_provider_stream_and_replay(postgres_test_rows, monkeypatch):
    rows = postgres_test_rows
    async with rows.session_factory() as db, db.begin():
        org = await db.get(Organization, rows.org_id)
        policy = StudyPolicyVersion(
            org_id=rows.org_id, policy_key="D-13", scope_kind="organization", scope_uuid=org.org_uuid,
            version=1, status="draft", body={}, evidence_reference="synthetic policy foreign-key fixture",
            effective_from=datetime(2026, 11, 1, tzinfo=UTC), created_by_user_id=rows.user_id,
        )
        db.add(policy)
        await db.flush()
        retention_policy_id = policy.id
    examples = (
        practice.ReviewedFractionExample(
            example_id="fraction_half", visual="fraction_explorer", parts=4, selected=2,
            numerator=1, denominator=2, prompt="What fraction of the whole is selected?",
            explanation="Two of four equal parts are selected.", hint="Count selected and total parts.",
            correct_feedback="Two of four is one half.", incorrect_feedback="Compare selected and total parts.",
        ),
        practice.ReviewedNumberLineExample(
            example_id="line_half", visual="number_line", first_mark=0, last_mark=1,
            divisions_per_unit=2, numerator=1, denominator=2,
            prompt="What fraction is the first mark after zero?",
            explanation="The interval is split into two equal parts.", hint="Count equal intervals.",
            correct_feedback="The first interval is one of two equal intervals.",
            incorrect_feedback="Compare the first interval with the whole unit.",
        ),
    )
    source = practice.PracticeSource(
        activity_uuid=f"activity_{uuid4()}", course_uuid=f"course_{uuid4()}", org_id=rows.org_id,
        identity="a" * 64, approved_visuals=frozenset({"fraction_explorer", "number_line"}),
        allow_hints=True, approved_examples=examples, allow_practice=True, allow_explore=True,
        approved_model="fake", retention_policy_id=retention_policy_id,
        retention_category="lesson_visual_practice", retention_hours=1,
    )
    async with rows.session_factory() as db:
        viewer = PublicUser.model_validate(await db.get(User, rows.user_id))

    async def auth(request, db, user, activity_uuid):
        assert user.id == rows.user_id and activity_uuid == source.activity_uuid
        return source

    calls, credits, refunds = [], [], []
    outputs = iter([{"visual": "fraction_explorer"}, {"visual": "number_line"}, {"visual": "iframe"}])

    async def fake_generate(**options):
        calls.append(json.loads(options["user_prompt"]))
        return next(outputs)

    async def fake_reserve(org_id, db):
        credits.append(org_id)
        if len(credits) == 4:
            raise HTTPException(403, "Synthetic allowance limit")

    monkeypatch.setattr(generation, "_authorize_source", auth)
    monkeypatch.setattr(practice, "_authorize_source", auth)
    monkeypatch.setattr(actions, "_authorize_source", auth)
    monkeypatch.setattr(generation, "generate", fake_generate)
    monkeypatch.setattr(generation, "enforce_ai_rate_limit", lambda user_id, org_id: None)
    monkeypatch.setattr(generation, "reserve_ai_credit", fake_reserve)
    monkeypatch.setattr(generation, "refund_ai_credit", refunds.append)

    app = FastAPI()
    app.include_router(ai_router)

    async def session():
        async with rows.session_factory() as db:
            yield db

    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_authenticated_user] = lambda: viewer
    path = "/stream/start/learning_surface"
    payload = {"schema_version": 1, "activity_uuid": source.activity_uuid, "mode": "practice"}
    first = {**payload, "request_id": str(uuid4())}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            availability_path = f"/activity/{source.activity_uuid}/learning_surface"
            empty = await client.get(availability_path)
            assert empty.json() == {"available": True, "surface": None}
            assert empty.headers["cache-control"] == "no-store"
            response = await client.post(path, json=first)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("text/event-stream")
            event = json.loads(response.text.removeprefix("data: "))
            assert event["type"] == "learning_surface"
            assert event["surface"]["components"][1]["kind"] == "fraction_explorer"
            assert event["surface"]["request_id"] == first["request_id"]
            assert "evaluation_basis" not in response.text

            replay = await client.post(path, json=first)
            assert replay.content == response.content
            conflict = await client.post(path, json={**first, "mode": "explore"})
            assert conflict.status_code == 409
            assert calls == [{"mode": "practice", "approved_visuals": ["fraction_explorer", "number_line"]}]

            recovery = await client.get(
                f"/activity/{source.activity_uuid}/learning_surface/{event['surface']['surface_id']}"
            )
            assert recovery.json() == event["surface"]
            assert recovery.headers["cache-control"] == "no-store"

            action_path = f"/activity/{source.activity_uuid}/learning_surface/actions"
            surface_id = event["surface"]["surface_id"]

            def operation(kind, surface, revision, **extra):
                return {
                    "action": kind, "surface_id": surface, "expected_revision": revision,
                    "request_id": str(uuid4()), **extra,
                }

            hint = await client.post(action_path, json=operation("request_hint", surface_id, 1))
            assert hint.status_code == 200 and hint.json()["revision"] == 2
            invalid_answer = await client.post(
                action_path, json=operation("submit_answer", surface_id, 2, answer="1/0"),
            )
            assert invalid_answer.status_code == 422
            answer_request = operation("submit_answer", surface_id, 2, answer="2/4")
            answer, replay_answer = await asyncio.gather(
                client.post(action_path, json=answer_request),
                client.post(action_path, json=answer_request),
            )
            assert answer.status_code == 200 and answer.json() == replay_answer.json()
            feedback = next(item for item in answer.json()["components"] if item["kind"] == "feedback")
            assert feedback["outcome"] == "correct"
            mismatch = await client.post(action_path, json={**answer_request, "answer": "3/4"})
            assert mismatch.status_code == 409
            latest = await client.get(f"/activity/{source.activity_uuid}/learning_surface/{surface_id}")
            assert latest.json() == answer.json()
            reopened = await client.get(availability_path)
            assert reopened.json() == {"available": True, "surface": answer.json()}
            replay_start = await client.post(path, json=first)
            assert replay_start.content == response.content
            advanced = await client.post(action_path, json=operation("next_question", surface_id, 3))
            assert advanced.status_code == 200 and advanced.json()["revision"] == 4
            new_question = next(item for item in advanced.json()["components"] if item["kind"] == "question")
            old_question = next(item for item in event["surface"]["components"] if item["kind"] == "question")
            assert new_question["question"]["id"] != old_question["question"]["id"]

            other = await client.post(path, json={**payload, "request_id": str(uuid4())})
            line = json.loads(other.text.removeprefix("data: "))["surface"]
            assert line["components"][1]["kind"] == "number_line"
            line_id = line["surface_id"]
            race_hint, race_answer = await asyncio.gather(
                client.post(action_path, json=operation("request_hint", line_id, 1)),
                client.post(action_path, json=operation("submit_answer", line_id, 1, answer="3/4")),
            )
            assert sorted([race_hint.status_code, race_answer.status_code]) == [200, 409]
            if race_hint.status_code == 200:
                race_answer = await client.post(
                    action_path, json=operation("submit_answer", line_id, 2, answer="3/4"),
                )
            assert race_answer.status_code == 200
            answered_line = race_answer.json()
            assert answered_line["actions"] == ["retry_practice"]
            retry = await client.post(action_path, json=operation("retry_practice", line_id, answered_line["revision"]))
            assert retry.status_code == 200
            assert retry.json()["components"][1]["divisions_per_unit"] == 2

            malformed = await client.post(path, json={**payload, "request_id": str(uuid4())})
            assert malformed.status_code == 503
            assert malformed.json()["detail"]["code"] == "LEARNING_SURFACE_UNAVAILABLE"
            quota = await client.post(path, json={**payload, "request_id": str(uuid4())})
            assert quota.status_code == 403
            assert quota.json()["detail"]["code"] == "LEARNING_SURFACE_ALLOWANCE"
            assert len(calls) == 3
            assert credits == [source.org_id] * 4
            assert refunds == [source.org_id]

        async with rows.session_factory() as db:
            records = (await db.execute(
                select(StudyLearningAction).where(
                    StudyLearningAction.surface_uuid.in_([surface_id, line_id]),
                    StudyLearningAction.action == "submit_answer",
                )
            )).scalars().all()
            assert len(records) == 2
            assisted_by_surface = {record.surface_uuid: record.assisted for record in records}
            assert assisted_by_surface == {surface_id: True, line_id: race_hint.status_code == 200}
            with pytest.raises(DBAPIError, match="Lesson practice evidence cannot be updated"):
                async with db.begin_nested():
                    await db.execute(update(StudyLearningAction).where(
                        StudyLearningAction.surface_uuid == surface_id,
                        StudyLearningAction.action == "submit_answer",
                    ).values(assisted=False))
            with pytest.raises(DBAPIError, match="Lesson practice evidence cannot be updated"):
                async with db.begin_nested():
                    await db.execute(update(StudyLearningSurface).where(
                        StudyLearningSurface.surface_uuid == surface_id,
                    ).values(source_identity="0" * 64))
            assert (await db.execute(select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.user_id == viewer.id,
            ))).scalars().all() == []
    finally:
        async with rows.session_factory() as db, db.begin():
            await db.execute(delete(StudyLearningAction).where(StudyLearningAction.surface_uuid.in_(
                select(StudyLearningSurface.surface_uuid).where(StudyLearningSurface.org_id == rows.org_id)
            )))
            await db.execute(delete(StudyLearningSurface).where(StudyLearningSurface.org_id == rows.org_id))
            await db.execute(delete(StudyPolicyVersion).where(StudyPolicyVersion.id == retention_policy_id))


@pytest.mark.studytomy_postgres
async def test_practice_retention_worker_honors_approved_expiry_and_holds(postgres_test_rows):
    rows = postgres_test_rows
    from src.services.studytomy.learning_practice_retention import (
        expire_practice_batch,
        remove_expired_practice_surface,
    )

    now = datetime.now(UTC)
    expired_uuid = str(uuid4())
    unheld_expired_uuid = str(uuid4())
    future_uuid = str(uuid4())
    hold_uuid = str(uuid4())
    action_uuid = str(uuid4())
    async with rows.session_factory() as db, db.begin():
        org = await db.get(Organization, rows.org_id)
        role = Role(
            name=f"Retention policy approver {rows.token}",
            org_id=rows.org_id,
            role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            role_uuid=f"retention-role-{rows.token}",
        )
        db.add(role)
        await db.flush()
        db.add(UserOrganization(
            org_id=rows.org_id,
            user_id=rows.user_id,
            role_id=role.id,
            creation_date=now.isoformat(),
            update_date=now.isoformat(),
        ))
        owner = StudyOwnerAssignment(
            org_id=rows.org_id,
            user_id=rows.user_id,
            responsibility=StudyOwnerFunction.BUSINESS.value,
            scope_kind="organization",
            scope_uuid=org.org_uuid,
            status="active",
            evidence_reference="synthetic D-13 owner fixture",
            effective_from=now - timedelta(days=1),
            assigned_by_user_id=rows.user_id,
        )
        db.add(owner)
        await db.flush()
        specified = {
            "retention": {
                "lesson_visual_practice": {
                    "retention_hours": 24,
                    "retention_category": "lesson_visual_practice",
                    "hold_reason_codes": ["LEGAL_HOLD"],
                    "hold_release_reason_codes": ["REVIEW_COMPLETE"],
                    "removal_reason_codes": ["AUTHORIZED_REMOVAL"],
                    "max_hold_review_days": 30,
                }
            },
            "removal": {
                "lesson_visual_practice": {
                    "automatic_expiry_enabled": True,
                    "authorized_removal_enabled": True,
                }
            },
        }
        policy = StudyPolicyVersion(
            org_id=rows.org_id,
            policy_key="D-13",
            scope_kind="organization",
            scope_uuid=org.org_uuid,
            version=1,
            status=StudyPolicyStatus.APPROVED.value,
            body=_synthetic_policy_body(
                StudyPolicyKey.D_13,
                specified,
            ).model_dump(mode="json"),
            evidence_reference="synthetic approved D-13 fixture",
            effective_from=now - timedelta(days=1),
            effective_to=now + timedelta(days=30),
            created_by_user_id=rows.user_id,
            approved_by_user_id=rows.user_id,
            approved_at=now,
        )
        db.add(policy)
        await db.flush()
        db.add(StudyPolicyApproval(
            org_id=rows.org_id,
            policy_version_id=policy.id,
            owner_assignment_id=owner.id,
            responsibility=StudyOwnerFunction.BUSINESS.value,
            approver_user_id=rows.user_id,
            evidence_reference="synthetic named D-13 approval",
            rationale="Isolated test fixture only.",
            approved_at=now,
        ))
        db.add(StudyLearningSurface(
            surface_uuid=expired_uuid,
            org_id=rows.org_id,
            user_id=rows.user_id,
            request_uuid=str(uuid4()),
            course_uuid=f"course_{rows.token}",
            activity_uuid=f"activity_{rows.token}",
            source_identity="a" * 64,
            presentation={"schema_version": 1, "components": []},
            retention_policy_id=policy.id,
            retention_category="lesson_visual_practice",
            created_at=now - timedelta(days=3),
            retention_expires_at=now - timedelta(days=2),
        ))
        db.add(StudyLearningSurface(
            surface_uuid=unheld_expired_uuid,
            org_id=rows.org_id,
            user_id=rows.user_id,
            request_uuid=str(uuid4()),
            course_uuid=f"course_{rows.token}",
            activity_uuid=f"activity_{rows.token}",
            source_identity="d" * 64,
            presentation={"schema_version": 1, "components": []},
            retention_policy_id=policy.id,
            retention_category="lesson_visual_practice",
            created_at=now - timedelta(days=3),
            retention_expires_at=now - timedelta(days=2),
        ))
        await db.flush()
        future_surface = StudyLearningSurface(
            surface_uuid=future_uuid,
            org_id=rows.org_id,
            user_id=rows.user_id,
            request_uuid=str(uuid4()),
            course_uuid=f"course_{rows.token}",
            activity_uuid=f"activity_{rows.token}",
            source_identity="c" * 64,
            presentation={"schema_version": 1, "components": []},
            retention_policy_id=policy.id,
            retention_category="lesson_visual_practice",
            created_at=now,
            retention_expires_at=now + timedelta(days=1),
        )
        db.add(future_surface)
        await db.flush()
        db.add(StudyLearningAction(
            surface_uuid=expired_uuid,
            request_uuid=action_uuid,
            request_hash="b" * 64,
            revision=2,
            action="request_hint",
            presentation={"schema_version": 1, "components": []},
            retention_policy_id=policy.id,
            retention_category="lesson_visual_practice",
            retention_expires_at=now - timedelta(days=2),
        ))
        db.add(StudyBusinessEvent(
            org_id=rows.org_id,
            event_uuid=hold_uuid,
            subject_uuid=f"user_{rows.token}",
            resource_kind="learning_practice_surface",
            resource_uuid=expired_uuid,
            action="studytomy.learning_practice.retention_hold_placed",
            operation_uuid=hold_uuid,
            dedupe_key=f"synthetic-hold-{rows.token}",
            reason="LEGAL_HOLD",
            policy_refs=[policy.id],
        ))
        await db.flush()

        with pytest.raises(HTTPException) as premature:
            await remove_expired_practice_surface(db, org, future_surface, now=now)
        assert premature.value.status_code == 409
        assert premature.value.detail["code"] == "PRACTICE_RETENTION_NOT_EXPIRED"

        # A held record survives, while later eligible records are not starved by it.
        assert await expire_practice_batch(db, org) == 1
        assert await db.get(StudyLearningSurface, expired_uuid) is not None
        assert await db.get(StudyLearningSurface, unheld_expired_uuid) is None

        db.add(StudyBusinessEvent(
            org_id=rows.org_id,
            subject_uuid=f"user_{rows.token}",
            resource_kind="learning_practice_surface",
            resource_uuid=expired_uuid,
            action="studytomy.learning_practice.retention_hold_released",
            operation_uuid=hold_uuid,
            dedupe_key=f"synthetic-release-{rows.token}",
            reason="REVIEW_COMPLETE",
            policy_refs=[policy.id],
        ))
        await db.flush()
        assert await expire_practice_batch(db, org) == 1
        await db.flush()
        assert await db.get(StudyLearningSurface, expired_uuid) is None
        actions = (await db.execute(select(StudyLearningAction).where(
            StudyLearningAction.surface_uuid == expired_uuid,
        ))).scalars().all()
        assert actions == []
        assert await db.get(StudyLearningSurface, future_uuid) is not None
        receipt = await db.scalar(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == rows.org_id,
            StudyBusinessEvent.resource_uuid == expired_uuid,
            StudyBusinessEvent.action == "studytomy.learning_practice.retention_removed",
        ))
        assert receipt is not None
        await db.rollback()
