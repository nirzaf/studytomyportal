import hashlib
import ipaddress
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.engine import URL
from src.db.organizations import Organization
from src.core.events.database import get_db_session
from src.core.migrations import current_revisions, head_revisions
from src.core.deployment_mode import get_deployment_mode
from src.services.orgs.cache import get_cached_instance_info, set_cached_instance_info
from config.config import get_learnhouse_config
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.db.users import PublicUser

router = APIRouter()


def _runtime_database_identity_sha256(database_url: URL, actual_database: str) -> str:
    """Return a credential-free identity only for a direct local PostgreSQL test DB."""
    host = (database_url.host or "").lower().strip("[]")
    database = database_url.database or ""
    if database_url.get_backend_name() != "postgresql" or database_url.query:
        raise ValueError("Runtime database URL has an unsupported driver or query override.")
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("Runtime database host is not loopback.")
        except ValueError as error:
            raise ValueError("Runtime database host is not loopback.") from error
    if not re.fullmatch(r"[A-Za-z0-9_-]+_test", database, re.IGNORECASE) or actual_database != database:
        raise ValueError("Runtime database is not the configured _test database.")
    port = database_url.port or 5432
    return hashlib.sha256(f"{host}\0{port}\0{actual_database}".encode("utf-8")).hexdigest()


async def _active_runtime_database_identity_sha256(db_session: AsyncSession, connection) -> str:
    if connection.dialect.name != "postgresql":
        raise ValueError("Runtime database is not PostgreSQL.")
    result = await connection.exec_driver_sql("SELECT current_database()")
    actual_database = result.scalar_one()
    return _runtime_database_identity_sha256(db_session.get_bind().url, actual_database)


@router.get("/runtime-identity", include_in_schema=False)
async def get_runtime_identity(
    response: Response,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Expose exact local test identity only when the operator opts in."""
    del current_user  # Authentication is required; identity contains no user data.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    if os.getenv("STUDYTOMY_TEST_RUNTIME_IDENTITY") != "1":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    build_sha = os.getenv("STUDYTOMY_BUILD_SHA", "")
    source_tree_sha256 = os.getenv("STUDYTOMY_BUILD_TREE_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{40}", build_sha) or not re.fullmatch(
        r"[0-9a-f]{64}", source_tree_sha256
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runtime source identity is not configured.",
        )

    try:
        connection = await db_session.connection()
        database_migration_heads = await connection.run_sync(current_revisions)
        database_identity_sha256 = await _active_runtime_database_identity_sha256(db_session, connection)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runtime database identity or revision is unavailable.",
        ) from error

    return {
        "build_sha": build_sha,
        "source_tree_sha256": source_tree_sha256,
        "source_migration_heads": head_revisions(),
        "database_migration_heads": database_migration_heads,
        "database_identity_sha256": database_identity_sha256,
    }


def _strip_port(domain: str) -> str:
    """Strip port from a domain string (e.g. 'localhost:3000' -> 'localhost')."""
    return domain.split(":")[0] if ":" in domain else domain


def _live_fields(tenancy: str) -> dict:
    """The license-derived part of the payload, never cached.

    Resolves the mode once. is_multi_org_allowed() calls get_deployment_mode()
    internally, so asking for both separately doubles the work on a public
    endpoint that runs this per request.
    """
    mode = get_deployment_mode()
    return {
        "mode": mode,
        # Deprecated: prefer `tenancy`. Will be removed in a future release.
        "multi_org_enabled": tenancy == "multi" and mode in ("ee", "saas"),
    }


@router.get(
    "/info",
    summary="Get instance info",
    description=(
        "Public endpoint returning instance configuration (deployment mode, default org slug, "
        "frontend domain, and multi-org flag). Result is cached for performance."
    ),
    responses={
        200: {"description": "Instance configuration for the current deployment."},
    },
)
async def get_instance_info(db_session: AsyncSession = Depends(get_db_session)):
    """Public endpoint returning instance configuration."""
    # Only the DB lookup is cached. `mode` and `multi_org_enabled` are derived
    # from live license state and are recomputed on every request: the license
    # can flip mid-runtime (a heartbeat recovering, a revocation landing), and
    # baking those into a 600s blob meant an operator who fixed a bad license
    # key watched /instance/info keep reporting mode "oss" for ten minutes and
    # concluded the fix had not worked.
    #
    # This is not free: get_deployment_mode() resolves the whole config, which
    # is why config.yaml parsing is memoised. Measured at ~0.2ms per request
    # against ~26ms before that memoisation.
    cached = get_cached_instance_info()
    if cached is not None:
        return {**cached, **_live_fields(cached.get("tenancy", "single"))}

    default_org_slug = "default"
    try:
        statement = select(Organization).where(Organization.slug == "default")
        default_org = (await db_session.execute(statement)).scalars().first()
        if not default_org:
            statement = select(Organization).order_by(Organization.id).limit(1)
            default_org = (await db_session.execute(statement)).scalars().first()
        if default_org:
            default_org_slug = default_org.slug
    except Exception:
        pass

    config = get_learnhouse_config()
    frontend_domain = config.hosting_config.frontend_domain
    top_domain = _strip_port(frontend_domain)
    tenancy = config.hosting_config.tenancy

    cacheable = {
        "tenancy": tenancy,
        "default_org_slug": default_org_slug,
        "frontend_domain": frontend_domain,
        "top_domain": top_domain,
    }
    set_cached_instance_info(cacheable)
    return {**cacheable, **_live_fields(tenancy)}
