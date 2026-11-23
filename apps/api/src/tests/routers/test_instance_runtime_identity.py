import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from src.core.events.database import get_db_session
from src.routers import instance


class RuntimeIdentityConnection:
    dialect = SimpleNamespace(name="postgresql")

    async def run_sync(self, callback):
        return callback(None)

    async def exec_driver_sql(self, statement):
        assert statement == "SELECT current_database()"
        return SimpleNamespace(scalar_one=lambda: "studytomy_test")


class RuntimeIdentitySession:
    def __init__(self, url="postgresql+asyncpg://user:secret@localhost/studytomy_test"):
        self.url = make_url(url)
        self.connection_value = RuntimeIdentityConnection()

    async def connection(self):
        return self.connection_value

    def get_bind(self):
        return SimpleNamespace(url=self.url)


class UnavailableRuntimeIdentitySession(RuntimeIdentitySession):
    async def connection(self):
        raise OSError("database connection unavailable")


def test_runtime_database_identity_is_credential_free_and_rejects_unsafe_targets():
    url = make_url("postgresql+asyncpg://user:secret@localhost/studytomy_test")
    expected = "5cefd14c3a81e924b9e6e36e08b483e9c6c45d022994e0a9f5169cdc979afa1b"
    assert hashlib.sha256(b"localhost\x005432\x00studytomy_test").hexdigest() == expected
    assert instance._runtime_database_identity_sha256(url, "studytomy_test") == expected
    assert instance._runtime_database_identity_sha256(
        make_url("postgresql+asyncpg://other:password@localhost:5432/studytomy_test"),
        "studytomy_test",
    ) == expected

    for unsafe in (
        "postgresql+asyncpg://user:secret@db.example.org/studytomy_test",
        "postgresql+asyncpg://user:secret@localhost/studytomy_test?host=db.example.org",
        "postgresql+asyncpg://user:secret@localhost/studytomy_test?hostaddr=203.0.113.1",
        "postgresql+asyncpg://user:secret@localhost/studytomy_test#unexpected",
    ):
        with pytest.raises(ValueError):
            instance._runtime_database_identity_sha256(make_url(unsafe), "studytomy_test")
    with pytest.raises(ValueError):
        instance._runtime_database_identity_sha256(url, "another_test")


@pytest.mark.asyncio
async def test_runtime_identity_is_hidden_without_local_test_opt_in(monkeypatch):
    monkeypatch.delenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", raising=False)

    with pytest.raises(HTTPException) as error:
        await instance.get_runtime_identity(Response(), current_user=None, db_session=RuntimeIdentitySession())

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_runtime_identity_requires_complete_build_metadata(monkeypatch):
    monkeypatch.setenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", "1")
    monkeypatch.setenv("STUDYTOMY_BUILD_SHA", "not-a-commit")
    monkeypatch.setenv("STUDYTOMY_BUILD_TREE_SHA256", "not-a-digest")

    with pytest.raises(HTTPException) as error:
        await instance.get_runtime_identity(Response(), current_user=None, db_session=RuntimeIdentitySession())

    assert error.value.status_code == 503
    assert error.value.detail == "Runtime source identity is not configured."


@pytest.mark.asyncio
async def test_runtime_identity_reports_database_heads(monkeypatch):
    monkeypatch.setenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", "1")
    monkeypatch.setenv("STUDYTOMY_BUILD_SHA", "a" * 40)
    monkeypatch.setenv("STUDYTOMY_BUILD_TREE_SHA256", "b" * 64)
    monkeypatch.setattr(instance, "head_revisions", lambda: ["head-1"])
    monkeypatch.setattr(instance, "current_revisions", lambda _connection: ["head-1"])

    result = await instance.get_runtime_identity(
        Response(), current_user=None, db_session=RuntimeIdentitySession()
    )

    assert result == {
        "build_sha": "a" * 40,
        "source_tree_sha256": "b" * 64,
        "source_migration_heads": ["head-1"],
        "database_migration_heads": ["head-1"],
        "database_identity_sha256": hashlib.sha256(b"localhost\x005432\x00studytomy_test").hexdigest(),
    }


@pytest.mark.asyncio
async def test_runtime_identity_rejects_database_host_query_override(monkeypatch):
    monkeypatch.setenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", "1")
    monkeypatch.setenv("STUDYTOMY_BUILD_SHA", "a" * 40)
    monkeypatch.setenv("STUDYTOMY_BUILD_TREE_SHA256", "b" * 64)
    monkeypatch.setattr(instance, "head_revisions", lambda: ["head-1"])
    monkeypatch.setattr(instance, "current_revisions", lambda _connection: ["head-1"])

    with pytest.raises(HTTPException) as error:
        await instance.get_runtime_identity(
            Response(),
            current_user=None,
            db_session=RuntimeIdentitySession(
                "postgresql+asyncpg://user:secret@localhost/studytomy_test?host=db.example.org"
            ),
        )

    assert error.value.status_code == 503
    assert error.value.detail == "Runtime database identity or revision is unavailable."


@pytest.mark.asyncio
async def test_runtime_identity_fails_closed_when_database_connection_is_unavailable(monkeypatch):
    monkeypatch.setenv("STUDYTOMY_TEST_RUNTIME_IDENTITY", "1")
    monkeypatch.setenv("STUDYTOMY_BUILD_SHA", "a" * 40)
    monkeypatch.setenv("STUDYTOMY_BUILD_TREE_SHA256", "b" * 64)

    with pytest.raises(HTTPException) as error:
        await instance.get_runtime_identity(
            Response(),
            current_user=None,
            db_session=UnavailableRuntimeIdentitySession(),
        )

    assert error.value.status_code == 503
    assert error.value.detail == "Runtime database identity or revision is unavailable."


@pytest.mark.asyncio
async def test_runtime_identity_route_rejects_unauthenticated_http_requests():
    app = FastAPI()
    app.include_router(instance.router, prefix="/api/v1/instance")
    app.dependency_overrides[get_db_session] = RuntimeIdentitySession

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/instance/runtime-identity")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
