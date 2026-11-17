"""Regression tests for default-private Studytomy learner projections."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.studytomy.family import StudyLearnerProfile
from src.db.users import PublicUser, User
from src.services.studytomy.privacy import (
    can_read_public_profile,
    project_org_member_user,
    project_public_author,
    project_public_user,
)
from src.services.users.users import (
    read_user_by_id,
    read_user_by_username,
    read_user_by_uuid,
)


async def _protect_learner(db, org, admin_user, learner_user):
    result = await db.execute(select(User).where(User.id == learner_user.id))
    learner = result.scalars().one()
    learner.bio = "private learner biography"
    learner.details = {"support_need": "private support detail"}
    learner.profile = {"private_note": "private profile note"}
    learner.extra_metadata = {"guardian_contact": "private contact"}
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=learner.id,
            created_by_user_id=admin_user.id,
        )
    )
    await db.commit()
    await db.refresh(learner)
    return learner


class TestProtectedLearnerProfileLookup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("lookup", "argument", "attribute"),
        [
            (read_user_by_id, "user_id", "id"),
            (read_user_by_uuid, "user_uuid", "user_uuid"),
            (read_user_by_username, "username", "username"),
        ],
    )
    async def test_other_org_member_gets_generic_not_found(
        self, lookup, argument, attribute, db, org, admin_user, regular_user, mock_request
    ):
        learner = await _protect_learner(db, org, admin_user, regular_user)

        with pytest.raises(HTTPException) as exc_info:
            await lookup(
                mock_request,
                db,
                admin_user,
                **{argument: getattr(learner, attribute)},
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Resource not found"

    @pytest.mark.asyncio
    async def test_superadmin_role_does_not_turn_public_lookup_into_authority(
        self, db, org, admin_user, regular_user
    ):
        learner = await _protect_learner(db, org, admin_user, regular_user)
        superadmin_viewer = PublicUser(
            id=99,
            username="platform-admin",
            first_name="Platform",
            last_name="Admin",
            email="platform-admin@example.test",
            user_uuid="user_platform_admin",
            is_superadmin=True,
        )

        assert not await can_read_public_profile(db, superadmin_viewer, learner)

    @pytest.mark.asyncio
    async def test_learner_can_read_their_own_profile(self, db, org, admin_user, regular_user):
        learner = await _protect_learner(db, org, admin_user, regular_user)

        assert await can_read_public_profile(db, regular_user, learner)

    @pytest.mark.asyncio
    async def test_anonymous_lookup_cannot_enumerate_protected_profile(
        self, db, org, admin_user, regular_user, anonymous_user, mock_request
    ):
        learner = await _protect_learner(db, org, admin_user, regular_user)

        with pytest.raises(HTTPException) as exc_info:
            await read_user_by_uuid(
                mock_request,
                db,
                anonymous_user,
                user_uuid=learner.user_uuid,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Resource not found"

    @pytest.mark.asyncio
    async def test_ordinary_profile_lookup_keeps_legacy_public_fields(
        self, db, org, admin_user, regular_user, mock_request
    ):
        user_result = await db.execute(select(User).where(User.id == regular_user.id))
        user = user_result.scalars().one()
        user.bio = "ordinary adult biography"
        user.details = {"website": "https://example.test"}
        user.profile = {"display_theme": "blue"}
        db.add(user)
        await db.commit()

        projection = await read_user_by_id(
            mock_request,
            db,
            admin_user,
            user_id=user.id,
        )

        assert projection.bio == "ordinary adult biography"
        assert projection.details == {"website": "https://example.test"}
        assert projection.profile == {"display_theme": "blue"}
        assert "email" not in projection.model_dump()


class TestProtectedLearnerProjections:
    @pytest.mark.asyncio
    async def test_public_user_projection_strips_free_form_learner_fields(
        self, db, org, admin_user, regular_user
    ):
        learner = await _protect_learner(db, org, admin_user, regular_user)

        projection = await project_public_user(db, learner)

        assert projection.bio == ""
        assert projection.details is None
        assert projection.profile is None
        assert "email" not in projection.model_dump()

    @pytest.mark.asyncio
    async def test_org_member_projection_strips_contact_and_free_form_fields(
        self, db, org, admin_user, regular_user
    ):
        learner = await _protect_learner(db, org, admin_user, regular_user)

        projection = await project_org_member_user(
            db,
            learner,
            allowed_extra_metadata_keys={"guardian_contact"},
        )

        assert projection.email == ""
        assert projection.bio == ""
        assert projection.details is None
        assert projection.profile is None
        assert projection.extra_metadata is None

    @pytest.mark.asyncio
    async def test_public_author_projection_is_pseudonymous_per_resource(
        self, db, org, admin_user, regular_user
    ):
        learner = await _protect_learner(db, org, admin_user, regular_user)

        first = await project_public_author(db, learner, scope_uuid="course_1")
        same_resource = await project_public_author(db, learner, scope_uuid="course_1")
        other_resource = await project_public_author(db, learner, scope_uuid="course_2")

        assert first.id == 0
        assert first.user_uuid.startswith("user_private_")
        assert first.username == "learner"
        assert first.first_name == "Learner"
        assert first.last_name == ""
        assert first.avatar_image == ""
        assert learner.user_uuid not in first.user_uuid
        assert same_resource == first
        assert other_resource.user_uuid != first.user_uuid
        assert "email" not in first.model_dump()
