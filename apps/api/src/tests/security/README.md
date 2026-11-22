# API security regression tests

This directory tests Studytomy Portal-derived and Studytomy security boundaries. It is not a certification that every route is secure, and a test file's presence is not a passing result.

## Find the relevant checks

| Concern | Representative tests |
|---|---|
| Authentication, revocation and cookies | `test_auth.py`, `test_auth_runtime.py`, `test_session_revocation.py`, `test_csrf.py` |
| Roles, org scope and tokens | `test_rbac.py`, `test_rbac_cross_org.py`, `test_api_token_access.py`, `test_mcp_auth.py` |
| Courses, assignments and media | `test_content_access.py`, `test_assignment_content_gates.py`, `test_media_file_access.py`, `test_submission_file_access.py` |
| AI retrieval and limits | `test_rag_access_scope.py`, `test_ai_cross_org_credit_drain.py`, `test_feature_resolve.py` |
| Input and resource boundaries | `test_file_validation.py`, `test_resource_exhaustion_limits.py`, `test_custom_domain_live_dns.py` |

Use filename search and the current service callers to select additional tests; this is deliberately not an exhaustive copied inventory. `test_security_all.py` is a legacy combined test module, not a substitute for collecting this directory. Studytomy-specific authority, immutable evidence and PostgreSQL races also live in `../studytomy` in the development worktree.

## Run locally

From the repository root, after installing locked API dependencies:

```bash
cd apps/api
uv run --no-sync pytest src/tests/security/ --collect-only -q
uv run --no-sync pytest src/tests/security/test_rag_access_scope.py -q
# Broader suite, after reviewing fixture side effects:
uv run --no-sync pytest src/tests/security/ -q
```

The shared `../conftest.py` sets `TESTING=true`, forces OSS and disables the demo. It supplies in-memory SQLite fixtures as well as mocks; do not claim all tests are mock-only or establish PostgreSQL locking behavior. Check each test and fixture for its dependencies, skips and platform restrictions. Use the explicit disposable PostgreSQL fixtures for database concurrency acceptance, never runtime data. Record the exact command, checkout and result. Repository CI/CD is disabled by local-only project policy; run these checks locally.

Preserve real authorization checks in regression cases. Do not weaken guards to make a fixture pass or treat approved synthetic policy fixtures as permission for real learners.
