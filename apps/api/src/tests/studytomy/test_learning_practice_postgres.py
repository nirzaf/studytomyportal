import asyncio
import json
from pathlib import Path
from uuid import uuid4

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
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.routers.ai.ai import router as ai_router
from src.security.auth import get_authenticated_user
from src.services.ai import learning_actions as actions
from src.services.ai import learning_generation as generation
from src.services.ai import learning_practice as practice
from src.services.ai.schemas.learning_surface import LearningSurface, PracticeAction, SubmitAnswer


@pytest.mark.studytomy_postgres
async def test_practice_recovery_rechecks_source_and_owner(postgres_test_rows, monkeypatch):
    rows = postgres_test_rows
    request = Request({"type": "http", "headers": []})
    stamp = "2026-11-16T00:00:00Z"
    other_id = None
    other_org_id = None
    try:
        async with rows.session_factory() as db, db.begin():
            owner = PublicUser.model_validate(await db.get(User, rows.user_id))
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
            assert blocked.value.detail["code"] == "POLICY_NOT_APPROVED"

        async def allow_only_synthetic_orgs(db, viewer, source):
            assert source.org_id in {rows.org_id, other_org_id}
            return practice.PracticePermission(frozenset({"fraction_explorer"}), allow_hints=True)

        monkeypatch.setattr(practice, "require_learning_practice_policy", allow_only_synthetic_orgs)

        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as graded:
                await practice.get_learning_availability(request, db, owner, assignment_activity_uuid)
            assert graded.value.status_code == 404

        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "learning_surface_v1.json"
        sample = json.loads(fixture.read_text(encoding="utf-8"))
        basis = practice.FractionEvaluationBasis(template="equivalent_fractions_v1", numerator=1, denominator=2)
        async with rows.session_factory() as db, db.begin():
            source = await practice._authorize_source(request, db, owner, activity_uuid)
            sample.update(activity_id=activity_uuid, source_identity=source.identity, surface_id=str(uuid4()))
            surface = LearningSurface.model_validate(sample)
            await practice.save_learning_surface(request, db, owner, surface, basis)

        async with rows.session_factory() as db:
            stored = await practice.recover_learning_surface(request, db, owner, activity_uuid, surface.surface_id)
            assert stored.model_dump(mode="json") == sample
            row = await db.get(StudyLearningSurface, str(surface.surface_id))
            assert row.evaluation_basis == basis.model_dump()
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
            if other_id is not None:
                await db.execute(delete(User).where(User.id == other_id))
            if other_org_id is not None:
                await db.execute(delete(Organization).where(Organization.id == other_org_id))


@pytest.mark.studytomy_postgres
async def test_fake_provider_stream_and_replay(postgres_test_rows, monkeypatch):
    rows = postgres_test_rows
    source = practice.PracticeSource(
        activity_uuid=f"activity_{uuid4()}", course_uuid=f"course_{uuid4()}", org_id=rows.org_id,
        identity="a" * 64, approved_visuals=frozenset({"fraction_explorer", "number_line"}),
        allow_hints=True,
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
    monkeypatch.setattr(generation, "model_for_tier", lambda tier: "fake")
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
