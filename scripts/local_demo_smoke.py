"""Verify synthetic seed credentials through real loopback authentication, offline from providers."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--credentials", required=True, type=Path)
parser.add_argument("--api", required=True, help="Loopback API origin, for example http://127.0.0.1:1342")
args = parser.parse_args()
url = urlsplit(args.api)
if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
    or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}):
    raise SystemExit("Refusing non-loopback API origin")
fixture = json.loads(args.credentials.read_text())
if fixture.get("scenario") != "native-accounts-v1":
    raise SystemExit("Wrong synthetic credential manifest")
for account in fixture["accounts"]:
    with httpx.Client(base_url=args.api, timeout=15, follow_redirects=False, trust_env=False) as client:
        assert client.get("/api/v1/auth/me").status_code == 401
        response = client.post("/api/v1/auth/login", data={
            "username": account["email"], "password": account["password"],
            "org_slug": fixture["org_slug"],
        })
        assert response.status_code == 200, "Synthetic sign-in failed"
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "demo-" + account["label"]
        session = client.get("/api/v1/users/session")
        assert session.status_code == 200
        data = session.json()
        assert data["user"]["id"] == account["user_id"]
        assert data["user"]["is_superadmin"] is False
        assert len(data["roles"]) == 1
        assert data["roles"][0]["role"]["id"] == account["role_id"]
        assert data["roles"][0]["org"]["id"] == fixture["org_id"]
        print(account["label"], "PASS: login, session, exact role, anonymous denial")
