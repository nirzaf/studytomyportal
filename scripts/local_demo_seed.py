"""Provision synthetic native accounts only in an explicit disposable local database.

Run with apps/api/.venv/bin/python from an isolated archive. No courses, policies,
reviews, releases, grants, guardian authority or external notifications are created.
Credentials are generated per run, saved outside the checkout with mode 0600,
and never printed. Reset means replacing the owned disposable database, not
rewriting account or immutable learning history.
"""
import argparse
import asyncio
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import re
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def validate_target(raw):
    from sqlalchemy.engine import make_url
    url = make_url(raw)
    if (url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not re.fullmatch(r"studytomy_demo_[a-z0-9_]+_test", url.database or "")
        or url.query):
        raise ValueError("Require loopback PostgreSQL, studytomy_demo_<name>_test and no routing query")
    return url


async def seed(args):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from src.db.organizations import Organization, OrganizationCreate
    from src.db.users import User
    from src.db.roles import Role
    from src.db.user_organizations import UserOrganization
    from src.security.security import security_hash_password
    from src.services.setup.setup import install_default_elements, install_create_organization

    url = validate_target(os.environ["LEARNHOUSE_SQL_CONNECTION_STRING"])
    output = args.credentials.resolve()
    if output.is_relative_to(ROOT) or not output.parent.is_dir() or output.is_symlink():
        raise ValueError("Credential file must be outside checkout in an existing private directory")
    if output.exists():
        raise ValueError("Credential file already exists; refusing overwrite. Use it to resume, not reseed.")
    engine = create_async_engine(url.set(drivername="postgresql+asyncpg"), echo=False, hide_parameters=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if (await db.exec(select(Organization))).first() or (await db.exec(select(User))).first():
                raise ValueError("Database is not empty; refusing to modify existing accounts")
            # Existing supported native setup helpers. No application authorization is disabled.
            await install_default_elements(db)
            org = await install_create_organization(OrganizationCreate(
                name="Synthetic local demo", slug="default", email="",
                description="Disposable synthetic account rehearsal only",
            ), db)
            now = datetime.now(UTC).isoformat()
            accounts = []
            fixture_passwords = {
                "admin": "Admin@123",
                "teacher": "Teacher@123",
                "learner": "Student@123",
            }
            # Reviewer is intentionally an Instructor account without release-review
            # business scope. A separate approved scenario must provision that scope.
            for label, role_id in (("admin", 1), ("teacher", 3), ("reviewer", 3), ("learner", 4)):
                role = await db.get(Role, role_id)
                if role is None:
                    raise ValueError("Native role bootstrap incomplete")
                password = fixture_passwords.get(label, secrets.token_urlsafe(24) + "Aa1!")
                user = User(username=f"demo-{label}", email=f"demo-{label}@example.com",
                            first_name="Synthetic", last_name=label.title(),
                            user_uuid=f"user_{uuid4()}", password=security_hash_password(password),
                            email_verified=True, is_superadmin=False,
                            creation_date=now, update_date=now)
                db.add(user)
                await db.flush()
                db.add(UserOrganization(user_id=user.id, org_id=org.id, role_id=role_id,
                                        creation_date=now, update_date=now))
                accounts.append({"label": label, "email": user.email, "password": password,
                                 "user_id": user.id, "role": role.name, "role_id": role_id})
            # Fail if the private output cannot be created; no account transaction committed yet.
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"scenario": "native-accounts-v1", "database": url.database,
                           "org_slug": org.slug, "org_id": org.id, "accounts": accounts}, f)
            await db.commit()
            print("Created four synthetic native accounts; credentials saved privately.")
            print("No content published, approval scopes, guardian grants or external messages created.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", required=True, type=Path)
    args = parser.parse_args()
    # Validate before loading application configuration or opening any connection.
    validate_target(os.environ["LEARNHOUSE_SQL_CONNECTION_STRING"])
    asyncio.run(seed(args))
