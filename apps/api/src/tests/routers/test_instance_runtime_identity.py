import pytest
from fastapi import HTTPException, Response

from src.routers import instance


@pytest.mark.asyncio
async def test_runtime_identity_is_hidden_without_local_test_opt_in(db, monkeypatch):
    monkeypatch.delenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", raising=False)

    with pytest.raises(HTTPException) as error:
        await instance.get_runtime_identity(Response(), current_user=None, db_session=db)

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_runtime_identity_requires_complete_build_metadata(db, monkeypatch):
    monkeypatch.setenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", "1")
    monkeypatch.setenv("STUDYTOMY_BUILD_SHA", "not-a-commit")
    monkeypatch.setenv("STUDYTOMY_BUILD_TREE_SHA256", "not-a-digest")

    with pytest.raises(HTTPException) as error:
        await instance.get_runtime_identity(Response(), current_user=None, db_session=db)

    assert error.value.status_code == 503
    assert error.value.detail == "Runtime source identity is not configured."


@pytest.mark.asyncio
async def test_runtime_identity_reports_database_heads(db, monkeypatch):
    monkeypatch.setenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", "1")
    monkeypatch.setenv("STUDYTOMY_BUILD_SHA", "a" * 40)
    monkeypatch.setenv("STUDYTOMY_BUILD_TREE_SHA256", "b" * 64)
    monkeypatch.setattr(instance, "head_revisions", lambda: ["head-1"])
    monkeypatch.setattr(instance, "current_revisions", lambda _connection: ["head-1"])

    result = await instance.get_runtime_identity(Response(), current_user=None, db_session=db)

    assert result == {
        "build_sha": "a" * 40,
        "source_tree_sha256": "b" * 64,
        "source_migration_heads": ["head-1"],
        "database_migration_heads": ["head-1"],
    }
