# AGENTS.md — Studytomy Portal

Guidelines and operating instructions for AI coding agents working on the **Studytomy Portal** repository (`https://github.com/nirzaf/studytomyportal.git`), forked from [LearnHouse](https://github.com/learnhouse/learnhouse.git) Community Edition.

---

## 1. Project Overview & Architecture

Studytomy is an Australian curriculum-focused learning platform combining structured courses, educator-led tutoring, context-aware AI assistance, assessments, interactive learning, and progress tracking.

- **Frontend (`apps/web`)**: Next.js 16 (App Router, Turbopack, `output: 'standalone'`), React, TypeScript, Tailwind CSS v4. Also see [apps/web/AGENTS.md](apps/web/AGENTS.md) for Next.js version-specific guidance.
- **Backend (`apps/api`)**: Python 3.14, FastAPI, SQLModel, Alembic, PostgreSQL (`pgvector`), Redis.
- **Collaboration (`apps/collab`)**: Hocuspocus / Yjs WebSocket server (Node.js / TypeScript / `tsx`).
- **CLI (`apps/cli`)**: Official setup, dev, and diagnostics CLI (`tsup` / TypeScript).
- **Authoritative Backend Rule**: FastAPI + PostgreSQL is the single source of truth. **Do not** introduce Supabase SDKs, auth, storage, or parallel identity stores.

### Proposed Studytomy Extension Boundaries

Keep Studytomy-specific additions isolated and additive so upstream updates remain easy to review and merge:

- `apps/api/src/routers/studytomy/` — Scoped HTTP endpoints
- `apps/api/src/services/studytomy/` — Domain use cases and business rules
- `apps/api/src/db/studytomy/` — Additive SQLModel entities
- `apps/api/migrations/versions/` — Additive Alembic migrations only
- `apps/web/app/` & `apps/web/components/` — Studytomy UI routes and components

---

## 2. Branding, Theme & Rebrand Guardrails

This fork has been rebranded to **Studytomy**:

- **Logos & Icons**:
  - `apps/web/public/studytomy-mini.png` (`87×87` centered icon mark)
  - `apps/web/public/studytomy-logo.png` (`639×428` cropped full logo)
  - `apps/web/app/icon.png` (Next.js App Router favicon)
- **Theme Colors (`apps/web/styles/globals.css` & `apps/web/app/layout.tsx`)**:
  - `themeColor`: `#003049`
  - Primary Navy: `#003049` (`201 100% 14%`)
  - Accent Orange: `#F77F00` (`31 100% 48%`)
  - Warm Gold: `#FCBF49` (`40 97% 64%`)
  - Destructive Crimson: `#D62828` (`0 69% 50%`)
  - Warm Cream: `#EAE2B7` (`50 56% 82%`)
- **Removed Branding / Links**:
  - `apps/web/components/Objects/Watermark.tsx` intentionally returns `null` (no floating "Made with" badge).
  - External `learnhouse.app`, `docs.learnhouse.app`, `learnhouse.to/discord`, and `learnhouse.io` UI links have been removed or replaced with `https://www.studytomy.com/`.
- **Do NOT Rename Internals**:
  - Preserve internal symbol names (`LearnHouseEmail`, `LearnHouseSpinner`, `LearnHouseCourseImport`, `useShowLearnHouseBranding`, etc.), `LEARNHOUSE_*` environment variables, API routes, and JSON translation keys in `apps/web/locales/*.json` (update only string *values*, never keys).

---

## 3. Local Development, Build & Test Commands

### Infrastructure (Docker)

```bash
docker compose -f .learnhouse/docker-compose.dev.yml -p learnhouse-dev up -d --wait
```

Starts `learnhouse-db-dev` (`pgvector/pgvector:pg16` on `127.0.0.1:5432`) and `learnhouse-redis-dev` (`redis:8.6.1-alpine` on `127.0.0.1:6379`).

### Build & Run

- **Web (`apps/web`)**:
  - Install & build: `cd apps/web && bun install --frozen-lockfile && bun run build`
  - Copy static assets for standalone mode: `cp -r .next/static .next/standalone/.next/static`
  - Run standalone server: `cd apps/web/.next/standalone && NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL=http://localhost:1338/ HOSTNAME=0.0.0.0 PORT=3000 bun server-wrapper.js`
  - Unit tests: `cd apps/web && bun run test`
- **API (`apps/api`)**:
  - Install: `cd apps/api && uv sync --frozen`
  - Apple Silicon (`arm64`) note: if `greenlet` is skipped by the `uv.lock` marker, run `uv pip install greenlet==3.5.6`.
  - Run: `cd apps/api && LEARNHOUSE_DEVELOPMENT_MODE=true LEARNHOUSE_DISABLE_EE=1 uv run python app.py` (port `1338`)
  - Tests: `cd apps/api && uv run pytest <test_path>`
- **Collab (`apps/collab`)**:
  - Install & build: `cd apps/collab && bun install --frozen-lockfile && bun run build`
  - Run: `cd apps/collab && bun run start` (port `4000`)
- **CLI (`apps/cli`)**:
  - Install & build: `cd apps/cli && bun install --frozen-lockfile && bun run build`
  - Tests: `cd apps/cli && bun run test`

Never commit `.env`, `.env.local`, `.demo-secrets`, `.learnhouse/`, private keys, or student data.

---

## 4. Security, Privacy & Engineering Standards

- **Access Boundaries**: Enforce permissions on the backend for every read, write, document, session, and AI tool action. Students see only their own records; tutors see only assigned students; guardians see only linked children.
- **AI & Assessment Safety**: Ground AI in educator-approved material, treat prompts/uploads/model outputs as untrusted, and **never** write final grades directly from unchecked model output.
- **Database & Idempotency**: Use Alembic for all PostgreSQL schema changes; keep imports, submissions, grade updates, and callbacks idempotent.
- **Licensing**: Upstream core is AGPL-3.0. Do not incorporate private Enterprise (`ee/`) code or bypass license restrictions.

---

## 5. Upstream Monitoring & Sync Workflow (Mandatory)

The upstream repository is configured as remote `origin` (`https://github.com/learnhouse/learnhouse.git`), while this fork's remote is `studytomy` (`https://github.com/nirzaf/studytomyportal.git`).

Whenever asked to check for upstream updates—or before major feature/maintenance work—follow this process:

1. **Fetch Upstream**:
   ```bash
   git fetch origin --prune
   ```
2. **Inspect New Commits**:
   Compare `origin/dev` (and `origin/main` / tags) against our fork's base commit (`144c843b`, or the latest merged upstream SHA):
   ```bash
   git log --oneline <last_synced_upstream_sha>..origin/dev
   ```
3. **Prepare a Benefits & Risk Report (Before Any Code Changes)**:
   If new commits or releases are found, **do not merge or cherry-pick automatically**. Present a structured report to the user covering:
   - **Commit SHAs & Titles** (grouped by Security, Bug Fixes, Performance, Features).
   - **Benefits for Studytomy** (why each update is useful—e.g., vulnerability fix, editor improvement, API stability, performance gain).
   - **Conflict & Rebrand Risk** (whether changes touch `apps/web/locales/`, branding/logos, `Watermark.tsx`, `OrgMenu.tsx`, `DashLeftMenu.tsx`, `globals.css`, or Alembic migrations).
   - **Recommended Action** (cherry-pick specific commits vs. merge vs. skip).
4. **Implement Only After Explicit Approval**:
   - Wait for the user to approve which updates to apply.
   - Apply the approved commits cleanly (resolving any conflicts while preserving all Studytomy branding, logos, theme variables, and removed LearnHouse links/watermarks).
   - Re-run `apps/web` tests (`bun run test`), `apps/web` build (`bun run build`), and relevant `apps/api` tests (`uv run pytest`) before committing and pushing to `studytomy`.
