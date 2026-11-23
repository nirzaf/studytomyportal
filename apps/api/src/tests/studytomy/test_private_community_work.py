"""Protected learner community work stays private until sharing is approved."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.communities.communities import Community
from src.db.communities.discussion_comments import DiscussionComment
from src.db.communities.discussions import Discussion
from src.db.studytomy.family import StudyLearnerProfile
from src.db.users import User
from src.services.ai.tools import ToolContext
from src.services.ai.tools.communities import _discussion_items
from src.services.ai.tools.discovery import SearchParams, _from_org_search
from src.services.ai.tools.refs import load as load_ai_reference
from src.services.communities.comment_votes import (
    remove_comment_upvote,
    upvote_comment,
)
from src.services.communities.comments import (
    get_comment_count,
    get_comments_by_discussion,
)
from src.services.communities.discussions import (
    get_discussion,
    get_discussions_by_community,
)
from src.services.communities.reactions import get_reactions, toggle_reaction
from src.services.communities.votes import remove_upvote, upvote_discussion
from src.services.search.search import search_across_org


async def _community(db, org):
    community = Community(
        org_id=org.id,
        name="Family learning",
        description="",
        public=True,
        community_uuid="community_private_work",
        creation_date="2026-11-16T00:00:00+00:00",
        update_date="2026-11-16T00:00:00+00:00",
    )
    db.add(community)
    await db.commit()
    await db.refresh(community)
    return community


async def _discussion(db, community, org, author_id, uuid, title):
    discussion = Discussion(
        title=title,
        content=f"Body for {title}",
        label="general",
        community_id=community.id,
        org_id=org.id,
        author_id=author_id,
        discussion_uuid=uuid,
        upvote_count=0,
        is_pinned=False,
        is_locked=False,
        creation_date="2026-11-16T00:00:00+00:00",
        update_date="2026-11-16T00:00:00+00:00",
    )
    db.add(discussion)
    await db.commit()
    await db.refresh(discussion)
    return discussion


async def _comment(db, discussion, author_id, uuid, content):
    comment = DiscussionComment(
        discussion_id=discussion.id,
        author_id=author_id,
        content=content,
        comment_uuid=uuid,
        upvote_count=0,
        creation_date="2026-11-16T00:00:00+00:00",
        update_date="2026-11-16T00:00:00+00:00",
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


@pytest.mark.asyncio
async def test_protected_discussion_is_private_to_its_author(
    db, org, admin_user, regular_user, mock_request
):
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
    )
    community = await _community(db, org)
    private_discussion = await _discussion(
        db, community, org, regular_user.id, "discussion_private", "Private learner work"
    )
    public_discussion = await _discussion(
        db, community, org, admin_user.id, "discussion_ordinary", "Ordinary work"
    )

    with patch(
        "src.services.communities.discussions.check_resource_access",
        new_callable=AsyncMock,
    ):
        stranger_list = await get_discussions_by_community(
            mock_request, community.community_uuid, admin_user, db
        )
        owner_list = await get_discussions_by_community(
            mock_request, community.community_uuid, regular_user, db
        )
        ordinary_detail = await get_discussion(
            mock_request, public_discussion.discussion_uuid, regular_user, db
        )

        with pytest.raises(HTTPException) as hidden_detail:
            await get_discussion(
                mock_request, private_discussion.discussion_uuid, admin_user, db
            )

    assert [row.discussion_uuid for row in stranger_list] == [public_discussion.discussion_uuid]
    assert {row.discussion_uuid for row in owner_list} == {
        private_discussion.discussion_uuid,
        public_discussion.discussion_uuid,
    }
    assert ordinary_detail.content == "Body for Ordinary work"
    assert hidden_detail.value.status_code == 404
    assert hidden_detail.value.detail == "Resource not found"

    ai_context = ToolContext(
        request=mock_request,
        db_session=db,
        user=admin_user,
        org=org,
        org_slug=org.slug,
    )
    ai_items = await _discussion_items(
        ai_context, [private_discussion, public_discussion]
    )
    assert [item["uuid"] for item in ai_items] == [public_discussion.discussion_uuid]

    owner_ai_context = ToolContext(
        request=mock_request,
        db_session=db,
        user=regular_user,
        org=org,
        org_slug=org.slug,
    )
    owner_ai_items = await _discussion_items(
        owner_ai_context, [private_discussion, public_discussion]
    )
    assert [item["uuid"] for item in owner_ai_items] == [public_discussion.discussion_uuid]
    with pytest.raises(HTTPException) as denied_ai_fetch:
        await load_ai_reference(
            owner_ai_context, "discussion", private_discussion.discussion_uuid
        )
    assert denied_ai_fetch.value.status_code == 404


@pytest.mark.asyncio
async def test_search_does_not_return_protected_discussion_text(
    db, org, admin_user, regular_user, mock_request
):
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
    )
    community = await _community(db, org)
    await _discussion(
        db,
        community,
        org,
        regular_user.id,
        "discussion_private_search",
        "Private learner queryneedle",
    )

    with patch(
        "src.services.search.search.search_courses",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "src.services.search.search.is_org_member",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "src.services.search.search.is_org_admin",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "src.services.search.search.can_read_all_communities",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await search_across_org(
            mock_request,
            admin_user,
            org.slug,
            "queryneedle",
            db,
        )

    assert result.discussions == []
    assert result.total_discussions == 0


@pytest.mark.asyncio
async def test_member_search_does_not_enumerate_protected_learner_identity(
    db, org, admin_user, regular_user, mock_request
):
    user_result = await db.execute(
        select(User).where(User.id == regular_user.id)
    )
    learner = user_result.scalars().one()
    learner.username = "protectedsearchneedle"
    learner.bio = "protectedsearchneedle"
    db.add(learner)
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=learner.id,
            created_by_user_id=admin_user.id,
        )
    )
    await db.commit()

    with patch(
        "src.services.search.search.search_courses",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "src.services.search.search.is_org_member",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "src.services.search.search.is_org_admin",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "src.services.search.search.can_read_all_communities",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await search_across_org(
            mock_request,
            admin_user,
            org.slug,
            "protectedsearchneedle",
            db,
        )

    assert result.users == []
    assert result.total_users == 0


@pytest.mark.asyncio
async def test_ai_search_drops_protected_discussion_candidates(
    db, org, admin_user, regular_user, mock_request
):
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
    )
    context = ToolContext(
        request=mock_request,
        db_session=db,
        user=regular_user,
        org=org,
        org_slug=org.slug,
    )
    search_data = {
        "discussions": [
            {
                "discussion_uuid": "discussion_private_search",
                "community_uuid": "community_private_work",
                "title": "Private learner queryneedle",
                "author_id": regular_user.id,
            }
        ]
    }
    with patch(
        "src.services.ai.tools.discovery._org_search",
        new_callable=AsyncMock,
        return_value=search_data,
    ):
        matches = await _from_org_search(
            context,
            SearchParams(query="queryneedle", kinds=["discussion"]),
            {"discussion"},
        )

    assert matches == []


@pytest.mark.asyncio
async def test_protected_comments_are_filtered_from_lists_and_counts(
    db, org, admin_user, regular_user, mock_request
):
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
    )
    community = await _community(db, org)
    discussion = await _discussion(
        db, community, org, admin_user.id, "discussion_with_comments", "Discussion"
    )
    private_comment = await _comment(
        db, discussion, regular_user.id, "comment_private", "Private learner comment"
    )
    ordinary_comment = await _comment(
        db, discussion, admin_user.id, "comment_ordinary", "Ordinary comment"
    )

    with patch(
        "src.services.communities.comments.check_resource_access",
        new_callable=AsyncMock,
    ), patch(
        "src.services.communities.discussions.check_resource_access",
        new_callable=AsyncMock,
    ):
        stranger_comments = await get_comments_by_discussion(
            mock_request, discussion.discussion_uuid, admin_user, db
        )
        owner_comments = await get_comments_by_discussion(
            mock_request, discussion.discussion_uuid, regular_user, db
        )
        stranger_count = await get_comment_count(
            mock_request, discussion.discussion_uuid, admin_user, db
        )
        owner_count = await get_comment_count(
            mock_request, discussion.discussion_uuid, regular_user, db
        )

    assert [row.comment_uuid for row in stranger_comments] == [ordinary_comment.comment_uuid]
    assert {row.comment_uuid for row in owner_comments} == {
        private_comment.comment_uuid,
        ordinary_comment.comment_uuid,
    }
    assert stranger_count == 1
    assert owner_count == 2


@pytest.mark.asyncio
async def test_protected_discussion_comment_count_is_not_enumerable(
    db, org, admin_user, regular_user, mock_request
):
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
    )
    community = await _community(db, org)
    discussion = await _discussion(
        db, community, org, regular_user.id, "discussion_private_count", "Private"
    )
    await _comment(db, discussion, regular_user.id, "comment_private_count", "Private")

    with patch(
        "src.services.communities.comments.check_resource_access",
        new_callable=AsyncMock,
    ):
        with pytest.raises(HTTPException) as stranger_count:
            await get_comment_count(
                mock_request, discussion.discussion_uuid, admin_user, db
            )
        owner_count = await get_comment_count(
            mock_request, discussion.discussion_uuid, regular_user, db
        )

    assert stranger_count.value.status_code == 404
    assert owner_count == 1


@pytest.mark.asyncio
async def test_direct_votes_and_reactions_cannot_reach_protected_work(
    db, org, admin_user, regular_user, mock_request
):
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
    )
    community = await _community(db, org)
    discussion = await _discussion(
        db, community, org, regular_user.id, "discussion_private_actions", "Private"
    )
    comment = await _comment(
        db, discussion, regular_user.id, "comment_private_actions", "Private comment"
    )

    actions = [
        upvote_discussion(mock_request, discussion.discussion_uuid, admin_user, db),
        remove_upvote(mock_request, discussion.discussion_uuid, admin_user, db),
        get_reactions(mock_request, discussion.discussion_uuid, admin_user, db),
        toggle_reaction(mock_request, discussion.discussion_uuid, "👍", admin_user, db),
        upvote_comment(mock_request, comment.comment_uuid, admin_user, db),
        remove_comment_upvote(mock_request, comment.comment_uuid, admin_user, db),
    ]
    for action in actions:
        with pytest.raises(HTTPException) as hidden_action:
            await action
        assert hidden_action.value.status_code == 404
        assert hidden_action.value.detail == "Resource not found"
