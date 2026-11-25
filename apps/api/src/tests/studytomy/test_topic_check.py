from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.services.ai.learning_practice import PracticePermission, ReviewedFractionExample, ReviewedTopicCheck, _PracticeReview
from src.services.courses.activities.activities import keep_server_owned_metadata
from src.services.studytomy import topic_check as check

RELEASE_UUID = f"course_release_{uuid4()}"


def _example(example_id, parts, selected):
    return ReviewedFractionExample(
        example_id=example_id, visual="fraction_explorer", parts=parts, selected=selected,
        numerator=selected, denominator=parts, prompt="What fraction of the whole is selected?",
        explanation="Identify the equal parts.", correct_feedback="Correct.", incorrect_feedback="Check the whole.",
    )


@pytest.fixture
def fixture():
    first, second = _example("first", 4, 2), _example("second", 6, 2)
    review = ReviewedTopicCheck(
        release_snapshot_sha256="d" * 64, pdf_sha256="a" * 64, pdf_page=3,
        initial_example_id="first", recheck_example_id="second",
        reteaching="Count the selected parts first, then count every equal-sized part.", show_visual=True,
    )
    return check._Binding(
        "b" * 64, PracticePermission(
            approved_visuals=frozenset({"fraction_explorer"}), allow_explore=True,
            allow_practice=True, approved_examples=(first, second), topic_check=review,
            activity_digest="c" * 64, concept_uuid="concept", curriculum_revision_uuid="curriculum",
            review_policy_id=19, retention_policy_id=1, retention_category="lesson_visual_practice", retention_hours=24,
        ), review, first, second, 7, 19,
    )


def test_wrong_first_answer_provides_scoped_explanation_and_different_recheck(fixture):
    episode = uuid4()
    first = check._initial(fixture, episode)
    assert first.question.visual.parts == 4 and first.reteaching is None
    second = check._answer(fixture, first, "1/3")
    assert (second.phase, second.revision, second.outcome, second.assisted) == ("recheck", 2, "incorrect", True)
    assert second.episode_uuid == episode and second.pdf_page == 3
    assert second.reteaching == fixture.review.reteaching and second.teaching_visual == first.question.visual
    assert second.question.visual.parts == 6
    done = check._answer(fixture, check.TopicCheckView.model_validate_json(second.model_dump_json()), "2/6")
    assert (done.phase, done.revision, done.outcome, done.assisted) == ("complete", 3, "correct", True)
    assert done.question is None and done.reteaching is None
    not_complete = check._answer(fixture, second, "1/2")
    assert (not_complete.phase, not_complete.outcome, not_complete.question) == ("needs_review", "incorrect", None)


def test_first_correct_is_unassisted_and_invalid_input_does_not_grade(fixture):
    first = check._initial(fixture)
    done = check._answer(fixture, first, "3/6")
    assert (done.phase, done.revision, done.outcome, done.assisted) == ("complete", 2, "correct", False)
    with pytest.raises(HTTPException) as invalid:
        check._answer(fixture, first, "1/0")
    assert invalid.value.status_code == 422


def test_review_rejects_rescaled_copy_of_answer_and_feedback(fixture):
    review = dict(
        curriculum_revision_uuid="curriculum", curriculum_digest="c" * 64,
        concept_uuid="concept", course_uuid="course", activity_uuid="activity",
        activity_version=7, activity_digest="c" * 64, rights_reference="rights",
        evidence_reference="evidence", review_reference="reviewed", reviewer_user_uuid="reviewer",
        examples=[fixture.initial, fixture.recheck], topic_check=fixture.review,
    )
    _PracticeReview.model_validate(review)
    with pytest.raises(ValidationError):
        _PracticeReview.model_validate({**review, "examples": [fixture.initial, _example("second", 6, 3)]})
    with pytest.raises(ValidationError):
        _PracticeReview.model_validate({
            **review, "topic_check": fixture.review.model_copy(update={"reteaching": "Check the whole."}),
        })


def test_creating_user_attribution_is_not_client_writable():
    key = "studytomy_pdf_creator_uuid"
    assert key not in keep_server_owned_metadata({key: "forged"}, None)
    assert keep_server_owned_metadata({key: "forged"}, {key: "original"})[key] == "original"


@pytest.mark.asyncio
async def test_source_scope_rechecks_revocation_pdf_format_and_sha(fixture, monkeypatch):
    scope = check.TopicScope(course_uuid="course", course_release_uuid=RELEASE_UUID, activity_uuid="activity")
    release = SimpleNamespace(uuid=scope.course_release_uuid, snapshot_sha256="d" * 64, rights_policy_id=19)
    auth = SimpleNamespace(release=release, progression_policy=object())
    resolver = AsyncMock(return_value=SimpleNamespace(
        concept_uuids=("concept",), authorization=auth, source_binding_digest="e" * 64,
        curriculum=SimpleNamespace(uuid="curriculum"), activity_digest="c" * 64,
        activity_version=7, activity_snapshot={"id": 19},
    ))
    monkeypatch.setattr(check, "resolve_managed_release_lesson_source", resolver)
    monkeypatch.setattr(check, "require_learning_practice_policy", AsyncMock(return_value=fixture.policy))
    monkeypatch.setattr(check, "_current_policies", AsyncMock(return_value=[SimpleNamespace(id=11)]))
    monkeypatch.setattr(check, "_assessed_pass_delivery", AsyncMock(return_value=(True, None)))
    media = {"sha256": fixture.review.pdf_sha256}
    monkeypatch.setattr(check, "_project_authorized_media", lambda *_a, **_kw: ("lesson", media, "application/pdf"))
    org = SimpleNamespace(id=1)
    actor = SimpleNamespace(id=2)
    assert (await check._bind(None, org, actor, scope)).activity_version == 7
    with pytest.raises(HTTPException):
        await check._bind(None, org, actor, scope.model_copy(update={"course_release_uuid": f"course_release_{uuid4()}"}))
    release.snapshot_sha256 = "f" * 64
    with pytest.raises(HTTPException) as different_snapshot:
        await check._bind(None, org, actor, scope)
    assert different_snapshot.value.status_code == 404
    release.snapshot_sha256 = "d" * 64
    release.rights_policy_id = 20
    with pytest.raises(HTTPException) as superseded_policy:
        await check._bind(None, org, actor, scope)
    assert superseded_policy.value.status_code == 404
    release.rights_policy_id = 19
    media["sha256"] = "0" * 64
    with pytest.raises(HTTPException):
        await check._bind(None, org, actor, scope)
    media["sha256"] = fixture.review.pdf_sha256
    resolver.side_effect = HTTPException(404, "Revoked")
    with pytest.raises(HTTPException):
        await check._bind(None, org, actor, scope)


def test_visual_help_requires_its_own_approval(fixture):
    no_visual = replace(fixture, policy=replace(fixture.policy, allow_explore=False))
    result = check._answer(no_visual, check._initial(no_visual), "1/3")
    assert result.reteaching and result.teaching_visual is None


@pytest.mark.studytomy_postgres
async def test_durable_episode_resume_replay_and_revocation(postgres_test_rows, fixture, monkeypatch):
    from datetime import UTC, datetime

    from sqlalchemy import delete
    from sqlmodel import select

    from src.db.organizations import Organization
    from src.db.studytomy.governance import StudyPolicyVersion
    from src.db.studytomy.practice import StudyLearningAction, StudyLearningSurface
    from src.db.users import PublicUser, User

    rows = postgres_test_rows
    scope = check.TopicScope(
        course_uuid="synthetic_course", course_release_uuid=RELEASE_UUID,
        activity_uuid="synthetic_activity",
    )
    async with rows.session_factory() as db:
        org = await db.get(Organization, rows.org_id)
        learner = PublicUser.model_validate(await db.get(User, rows.user_id))
        policy = StudyPolicyVersion(
            org_id=rows.org_id, policy_key="D-13", scope_kind="organization", scope_uuid=org.org_uuid,
            version=1, status="draft", body={}, evidence_reference="synthetic retention fixture",
            effective_from=datetime.now(UTC), created_by_user_id=rows.user_id,
        )
        db.add(policy)
        await db.commit()
        policy_id = policy.id
    bound = replace(fixture, policy=replace(fixture.policy, retention_policy_id=policy_id))

    revoked = False

    async def bind(session, org, _actor, _scope):
        await check.lock_organization(session, org.id)
        if revoked:
            raise HTTPException(404, "Source revoked")
        return bound

    monkeypatch.setattr(check, "_bind", bind)
    initial = check.TopicSubmission(**scope.model_dump(), operation_uuid=uuid4(), binding_id=bound.identity, expected_revision=1, answer="1/3")
    final = check.TopicSubmission(**scope.model_dump(), operation_uuid=uuid4(), binding_id=bound.identity, expected_revision=2, answer="2/6")
    try:
        async with rows.session_factory() as db:
            first = await check.read_topic_check(db, org, learner, scope)
            assert first.phase == "initial" and first.episode_uuid is None
        async with rows.session_factory() as db:
            wrong = await check.submit_topic_check(db, org, learner, initial)
            assert wrong.phase == "recheck"
        async with rows.session_factory() as db:
            assert (await check.read_topic_check(db, org, learner, scope)) == wrong
        async with rows.session_factory() as db:
            assert (await check.submit_topic_check(db, org, learner, initial)) == wrong
        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as reuse:
                await check.submit_topic_check(db, org, learner, initial.model_copy(update={"answer": "1/2"}))
            assert reuse.value.status_code == 409
        async with rows.session_factory() as db:
            done = await check.submit_topic_check(db, org, learner, final)
            assert done.episode_uuid == wrong.episode_uuid and done.phase == "complete"
        async with rows.session_factory() as db:
            assert (await check.read_topic_check(db, org, learner, scope)) == done
            saved = (await db.execute(
                select(StudyLearningAction).where(StudyLearningAction.surface_uuid == str(done.episode_uuid))
                .order_by(StudyLearningAction.revision)
            )).scalars().all()
            assert [(action.revision, action.assisted) for action in saved] == [(2, False), (3, True)]
        revoked = True
        async with rows.session_factory() as db:
            with pytest.raises(HTTPException) as gone:
                await check.submit_topic_check(db, org, learner, final)
            assert gone.value.status_code == 404
    finally:
        async with rows.session_factory() as db, db.begin():
            own_surfaces = select(StudyLearningSurface.surface_uuid).where(StudyLearningSurface.org_id == rows.org_id)
            await db.execute(delete(StudyLearningAction).where(StudyLearningAction.surface_uuid.in_(own_surfaces)))
            await db.execute(delete(StudyLearningSurface).where(StudyLearningSurface.org_id == rows.org_id))
            await db.execute(delete(StudyPolicyVersion).where(StudyPolicyVersion.id == policy_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("role_id, creator, target_id", [(999, "lesson-creator", 12), (1, "staff", 1)])
async def test_staff_rejects_unrelated_teacher_and_own_learner_record(fixture, monkeypatch, role_id, creator, target_id):
    def result(value):
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        result(SimpleNamespace(role_id=role_id)),
        result(SimpleNamespace(id=19, extra_metadata={"studytomy_pdf_creator_uuid": creator})),
        result(SimpleNamespace(id=target_id)),
    ]))
    org = SimpleNamespace(id=5)
    scope = check.TopicScope(
        course_uuid="course", course_release_uuid=RELEASE_UUID, activity_uuid="activity",
    )
    bind = AsyncMock()
    monkeypatch.setattr(check, "verify_org_admin", AsyncMock(side_effect=HTTPException(403)))
    monkeypatch.setattr(check, "lock_organization", AsyncMock(return_value=org))
    monkeypatch.setattr(check, "check_resource_access", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    monkeypatch.setattr(check, "_bind", bind)
    with pytest.raises(HTTPException) as denied:
        await check.read_topic_timeline(None, db, org, SimpleNamespace(id=1, user_uuid="staff"), "learner", scope)
    assert denied.value.status_code == 404
    bind.assert_not_awaited()


@pytest.mark.asyncio
async def test_replaced_binding_cannot_grade_an_answer_from_old_screen(fixture, monkeypatch):
    monkeypatch.setattr(check, "_bind", AsyncMock(return_value=fixture))
    record = AsyncMock()
    monkeypatch.setattr(check, "_record", record)
    body = check.TopicSubmission(
        course_uuid="course", course_release_uuid=RELEASE_UUID,
        activity_uuid="activity", binding_id="c" * 64,
        operation_uuid=uuid4(), expected_revision=1, answer="1/2",
    )
    with pytest.raises(HTTPException) as conflict:
        await check.submit_topic_check(None, None, None, body)
    assert conflict.value.status_code == 409
    record.assert_not_awaited()


@pytest.mark.parametrize(("role", "author", "allowed"), [
    ("admin", False, True),
    ("teacher", True, True),
    ("teacher", False, False),
])
async def test_staff_timeline_requires_author_or_admin_and_current_scope(
    fixture, monkeypatch, role, author, allowed,
):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import Mock

    from src.db.users import PublicUser

    org = SimpleNamespace(id=1)
    staff = SimpleNamespace(id=10, user_uuid="staff")
    learner = PublicUser(
        id=11, user_uuid="learner", username="learner", email="learner@example.invalid",
        first_name="Synthetic", last_name="Learner",
    )
    membership = SimpleNamespace(
        role_id=997 if role == "admin" else 999,
    )
    activity = SimpleNamespace(id=fixture.lesson_id, extra_metadata={
        "studytomy_pdf_creator_uuid": staff.user_uuid if author else "other_teacher",
    })
    scope = check.TopicScope(
        course_uuid="course", course_release_uuid=RELEASE_UUID, activity_uuid="lesson",
    )
    response = check._answer(fixture, check._initial(fixture), "1/3")
    created = datetime.now(UTC)
    action = SimpleNamespace(
        presentation=response.model_dump(mode="json"), assisted=False, created_at=created, answer="private answer",
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        Mock(scalar_one_or_none=Mock(return_value=membership)),
        Mock(scalar_one_or_none=Mock(return_value=activity)),
        Mock(scalar_one_or_none=Mock(return_value=learner)),
        Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[action])))),
    ]))
    admin_permission = AsyncMock(side_effect=None if role == "admin" else HTTPException(403))
    monkeypatch.setattr(check, "verify_org_admin", admin_permission)
    monkeypatch.setattr(check, "lock_organization", AsyncMock(return_value=org))
    monkeypatch.setattr(check, "check_resource_access", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    monkeypatch.setattr(check, "_bind", AsyncMock(return_value=fixture))
    assert_scope = AsyncMock()
    monkeypatch.setattr(check, "read_staff_mastery", assert_scope)
    monkeypatch.setattr(check, "_record", AsyncMock(return_value=SimpleNamespace(
        surface_uuid=str(uuid4()), retention_expires_at=created + timedelta(hours=1),
    )))

    if not allowed:
        with pytest.raises(HTTPException) as denied:
            await check.read_topic_timeline(None, db, org, staff, "learner", scope)
        assert denied.value.status_code == 404
        assert_scope.assert_not_awaited()
        return

    result = await check.read_topic_timeline(None, db, org, staff, "learner", scope)
    if role == "admin":
        admin_permission.assert_awaited_once_with(staff.id, org.id, db)
    else:
        admin_permission.assert_not_awaited()
    assert_scope.assert_awaited_once_with(None, db, org, staff, "learner", "curriculum", 0, 1)
    assert not result.complete and result.pdf_page == 3
    assert result.events[0].reteaching == fixture.review.reteaching
    assert result.events[0].prompt == fixture.initial.prompt
    assert "private answer" not in result.model_dump_json()
    assert_scope.side_effect = HTTPException(403)
    db.execute.reset_mock(side_effect=True)
    db.execute.side_effect = [
        Mock(scalar_one_or_none=Mock(return_value=row)) for row in (membership, activity, learner)
    ]
    with pytest.raises(HTTPException) as denied:
        await check.read_topic_timeline(None, db, org, staff, "learner", scope)
    assert denied.value.status_code == 404


@pytest.mark.studytomy_postgres
async def test_pdf_authoring_records_managed_creator_not_client_metadata(postgres_test_rows, monkeypatch):
    from datetime import UTC, datetime
    from io import BytesIO

    from fastapi import Request, UploadFile
    from starlette.datastructures import Headers

    from src.db.courses.activities import ActivityUpdate
    from src.db.courses.chapters import Chapter
    from src.db.courses.course_chapters import CourseChapter
    from src.db.courses.courses import Course
    from src.db.users import PublicUser, User
    from src.services.courses.activities import activities, pdf

    rows = postgres_test_rows
    stamp = datetime.now(UTC).isoformat()
    actor_request = Request({"type": "http", "headers": []})
    key = "studytomy_pdf_creator_uuid"
    monkeypatch.setattr(pdf, "check_resource_access", AsyncMock())
    monkeypatch.setattr(pdf, "upload_pdf", AsyncMock(return_value="synthetic.pdf"))
    monkeypatch.setattr(pdf, "index_activity", lambda _id: None)
    monkeypatch.setattr(pdf, "track_activity_created", AsyncMock())
    monkeypatch.setattr(activities, "check_resource_access", AsyncMock())
    monkeypatch.setattr(activities, "index_activity", lambda _id: None)
    monkeypatch.setattr(activities, "notify_ee_course_content_changed", lambda _id: None)

    async with rows.session_factory() as db:
        maker = PublicUser.model_validate(await db.get(User, rows.user_id))
        course = Course(
            name="Synthetic PDF pilot", course_uuid=f"course_{uuid4()}", org_id=rows.org_id,
            open_to_contributors=False, public=False, published=False, studytomy_managed=True,
        )
        db.add(course)
        await db.flush()
        chapter = Chapter(name="Fractions", course_id=course.id, org_id=rows.org_id, chapter_uuid=f"chapter_{uuid4()}")
        db.add(chapter)
        await db.flush()
        db.add(CourseChapter(
            order=1, course_id=course.id, chapter_id=chapter.id, org_id=rows.org_id,
            creation_date=stamp, update_date=stamp,
        ))
        await db.commit()

        upload = UploadFile(
            filename="synthetic.pdf", file=BytesIO(b"%PDF-1.4 synthetic upload stub"),
            headers=Headers({"content-type": "application/pdf"}),
        )
        created = await pdf.create_documentpdf_activity(
            actor_request, "Fractions", chapter.id, maker, db, upload, {key: "spoofed", "display": "synthetic"},
        )
        assert created.extra_metadata == {key: maker.user_uuid, "display": "synthetic"}
        updated = await activities.update_activity(
            actor_request, ActivityUpdate(extra_metadata={key: "another", "display": "changed"}),
            created.activity_uuid, maker, db,
        )
        assert updated.extra_metadata == {key: maker.user_uuid, "display": "changed"}
