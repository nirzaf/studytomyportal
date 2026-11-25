# Studytomy

Documentation navigation: [task-oriented index](docs/studytomy/README.md). Read the relevant source and scoped guidance rather than every historical document.

**AI-assisted learning, trusted tutoring, and measurable progress.**

Studytomy Portal is an educator-led learning platform under active development, combining structured courses, tutoring, context-aware AI assistance, assessments, interactive learning, and evidence-based progress tracking. The intended learner offering is the Sri Lankan National School Syllabus (National Institute of Education / Ministry of Education, Grades 6–13, English instruction) for students and their parents/guardians; see the [Sri Lankan decision baseline](docs/studytomy/SRI-LANKA-DECISION-BASELINE.md). That scope is an engineering alignment only: the programme, market and cohort decisions behind it (`D-01`, `D-02`) remain unapproved.

**Studytomy Portal is a public source repository.** The application is in local development. GitHub Actions runs build and repository security checks. For local setup and private vulnerability reporting, see [LOCAL-ONLY.md](LOCAL-ONLY.md) and [SECURITY.md](SECURITY.md).

## Contents

- [Product vision](#product-vision)
- [Scope and delivery status](#scope-and-delivery-status)
- [Target architecture](#target-architecture)
- [Technology choices](#technology-choices)
- [User journeys](#user-journeys)
- [AI and virtual laboratory principles](#ai-and-virtual-laboratory-principles)
- [Repository organization](#repository-organization)
- [Getting started](#getting-started)
- [Migration strategy](#migration-strategy)
- [Security, privacy, and governance](#security-privacy-and-governance)
- [Engineering standards](#engineering-standards)
- [Roadmap and acceptance gates](#roadmap-and-acceptance-gates)
- [Decisions still required](#decisions-still-required)
- [Licensing](#licensing)

## Product vision

The [topic learning loop](docs/studytomy/TOPIC-LEARNING-LOOP.md) is the first end-to-end product target: an educator publishes a lesson with approved material, the learner answers a short question at a topic boundary, and a wrong answer triggers cited reteaching and a distinct equivalent recheck. Scoped staff history shows where the learner struggled and what helped. This is a target, not a claim that the complete flow is available.

Studytomy is intended to help students learn, practise, receive feedback, and improve while giving tutors a controlled way to author educational material, guide students, and review learning evidence. AI assists teachers and learners; it is not the authority for curriculum accuracy, final academic decisions, or scientifically valid experiments.

### Target audiences

| Audience | Intended value |
|---|---|
| **Students** | Curriculum-linked lessons, guided explanations, practice, and understandable feedback. |
| **Tutors and educators** | Course authoring, assessment generation, submissions, marking, and intervention insights. |
| **Parents / guardians** | Authorized learner relationships and sharing; progress and educational acceptance remain subject to verification. |
| **Administrators** | User/content access, tutoring operations, course publication, and platform oversight. |
| **Studytomy operations** | Lead handling, scheduling, reporting, and eventually reviewed marketing intelligence. |

### Product principles

- **Education first**: Explain concepts, support genuine practice, and validate assessed outcomes.
- **Human oversight**: Educators approve reference content and review subjective or uncertain marks.
- **Reuse before rebuilding**: Preserve upstream LMS functionality and implement only demonstrable gaps.
- **One authoritative backend**: Native FastAPI services and PostgreSQL; no Supabase in the target runtime.
- **Small, verifiable releases**: Prove a complete learner workflow before expanding subjects, markets, or AI modalities.
- **Privacy by design**: Explicit permissions for minors, guardians, staff, and AI service providers.

## Scope and delivery status

Use these terms consistently:

- **Upstream OSS capability**: Available in the selected open-source LMS codebase; not evidence that Studytomy has configured or deployed it.
- **Legacy capability**: Present in the previous Studytomy application; must be retained, replaced, or migrated deliberately.
- **Planned**: Studytomy-specific development that requires approval, implementation, and tests.
- **Conditional**: Enabled only if the client chooses it or the required third-party service is configured.

### Foundation capabilities to reuse

| Capability | Source / status | Delivery note |
|---|---|---|
| Course catalogue, chapters, learning activities | Upstream OSS | Configure Studytomy subjects, visibility, and approved content. |
| Rich editor, video/PDF lessons, interactive blocks | Upstream OSS | Editor authoring is documented as desktop-focused. |
| Quizzes, assignments, submissions | Upstream OSS | Includes multiple choice, fill-in-the-blanks, short/numeric answers, code, and file submissions. |
| Supported automatic grading and tutor feedback | Upstream OSS | Code grading needs a configured execution service; file submissions require teacher review. |
| Learning paths, progression, completion records, certificates | Upstream OSS | Completion is not an estimate of concept mastery. |
| Lesson-level contextual AI, AI-assisted course/question drafting | Upstream OSS + configuration | Requires an approved AI provider and validated learning content. |
| Collaborative boards, communities, resource library, playgrounds | Upstream OSS | Interactive playgrounds are not scientifically validated labs. |
| Roles, groups, invitations, authentication, MFA | Upstream OSS | Extend for parental/guardian relationships when approved. |
| Branding, public landing pages, SEO, search | Upstream OSS | Compare against existing website URLs and conversion journeys. |
| Headless APIs, webhooks, scoped integrations, MCP | Upstream OSS + configuration | Enable only the endpoints and permissions needed. |

### Studytomy code currently present

**Source status, 23 November 2026:** the modules listed below are present in `main` at `45178067d44372f508adea16cfce5a193ff783ac`. Their presence is source evidence only; it does not establish educator approval, complete acceptance, production readiness, or deployment. See the [documentation review](docs/studytomy/DOCUMENTATION-REVIEW.md) for scope and limitations.

The working source contains additive Studytomy modules, rather than only a proposed fork. The following are **source-present / acceptance pending**; file presence and targeted tests are not production certification or completion of the 73-task specification.

| Area | Source evidence | Remaining boundary |
|---|---|---|
| Policy approvals, owner/staff scopes, financial delegation and offer readiness | `apps/api/src/routers/studytomy/governance.py`, `apps/api/src/services/studytomy/policies.py`, `readiness.py` | Real owner decisions and launch approvals must be supplied; never fabricate them. |
| Learner profiles, family-authority requests/review, sharing grants and withdrawal | `apps/api/src/routers/studytomy/family.py`, `apps/api/src/services/studytomy/authority.py`, `sharing.py` | Browser, relationship-isolation and full workflow acceptance are separate checks. |
| Evidence-file access, privacy, offers, idempotency and outbox processing | `apps/api/src/routers/studytomy/`, `apps/api/src/services/studytomy/` | Validate storage, worker configuration, concurrency and real PostgreSQL behavior before release. |
| Family workspace, learner context, privacy settings and readiness UI | `apps/web/components/Studytomy/` | Connected UI does not establish educational correctness or production deployment. |
| Adaptive learning and evidence | `apps/api/src/services/studytomy/adaptive_attempts.py`, `adaptive_evidence.py`, `adaptive_mastery.py` | Server-owned scoring and derived mastery are implemented. Real learner activation requires approved curriculum, eligibility, consent, and safeguarding policies. |
| Guided teaching and reviewed reports | `apps/api/src/services/studytomy/adaptive_teaching.py`, `adaptive_reports.py`, `apps/web/components/Objects/Account/subpages/AccountReports.tsx` | Current authority, content/policy approvals and full browser verification remain required. |

Reviewed lesson practice uses authorized examples, keeps scored visuals read-only, and tracks ungraded attempts separately from mastery evidence. Browse approved reference material through Explore Topics. See the [current implementation summary](docs/studytomy/IMPLEMENTATION-STATUS.md).

The remaining product ambitions below are a roadmap, not a claim that all custom functionality is absent or complete. See source, migrations and focused tests for each implementation slice; no live deployment or complete acceptance run is asserted by this README.

### Studytomy modernization requirements

| Feature | Proposed scope | Status |
|---|---|---|
| AI diagnostic assessments | Versioned curriculum, reviewed items, diagnostic attempts and server-owned objective scoring. | Partial: local API/model/service implementation; educational and end-to-end acceptance pending |
| Personalized study plans | Mastery-driven lesson recommendations and guided teaching; richer tutor-editable schedules remain a target. | Partial: local mastery/recommendation/tutor paths; not a completed planning product |
| Sri Lankan senior-secondary (G.C.E. O/L and A/L) preparation | Approved syllabus-specific content, past-paper practice, marking schemes, practice workflows. | Planned |
| AI-generated examination papers | Syllabus-aligned blueprints, controlled question variation, validated answers, tutor approval, export. | Planned |
| Advanced marking | Structured solution steps, rubric-based feedback, uncertainty escalation; handwriting/essays by explicit scope. | Planned |
| Student, guardian and tutor progress views | Authorized progress, reviewed report snapshots, exact-recipient release and withdrawal. | Partial: local progress/report services and learner/staff UI; full browser and real-user acceptance pending |
| Virtual science laboratories | Validated 2D/3D experiments, measurements, mistakes, experiment records, and scoring. | Partial: Pilot 01 (momentum) runs locally with managed assignment submission, release-bound grading, build-integrity checks and live browser specs; validated-model review and educator/publication approvals remain open |
| AI marketing assistant | Draft creatives/copy, campaign recommendations, reviewed publishing workflow. | Planned / optional |
| Subject and grade demand analysis | Evidence-labelled research into subject/grade demand using approved public data and Studytomy enquiry trends. | Planned / optional |

### Public website and legacy workflow separation

The public website (`studytomy.com` / `www.studytomy.com`, [`nirzaf/studytomy-react`](https://github.com/nirzaf/studytomy-react)) is an independent application outside the LMS development and deployment scope, with its own deployment, authentication sessions, and data storage. There is no requirement to migrate the public website's Supabase database or connect its existing business workflows/APIs (lead intake, trial booking, recurring classes, attendance, payments, notifications, scheduling) to the LMS unless separately authorized.

### Out of scope unless separately approved

- A proprietary AI foundation model; Studytomy will integrate approved AI providers.
- Automatic publication or spending on advertising without a designated human approver.
- Claims of guaranteed exam results, accurate AI essay marking, or universal offline functionality.
- Fully autonomous scientific experiments without validated mathematical/scientific models.
- Multi-school tenancy, SSO, SCORM, LTI, integrated live classrooms, private messaging, built-in payments, and advanced LMS analytics unless specifically selected and implemented/licensed.

## Target architecture

**Target architecture (not deployed)**: Studytomy Portal targets `lms.studytomy.com` and uses the existing open-source LMS runtime and database, extended for Studytomy workflows. The repository currently supports local development only. The existing public website (`studytomy.com` / `www.studytomy.com`, [`nirzaf/studytomy-react`](https://github.com/nirzaf/studytomy-react)) remains a separate application.

```mermaid
flowchart TD
  subgraph PublicSite["studytomy.com — Public Website (studytomy-react)"]
    Visitor[Visitors] --> Marketing[Public Website]
  end

  subgraph LMS["Studytomy Portal target — not deployed"]
    Learner[Students / Tutors / Admins] --> Web[Studytomy Portal\nNext.js / React]
    Web --> API[FastAPI Backend]
    Web <-->|Collaborative editing| Collab[Collaboration Service\nHocuspocus / Yjs]
    API --> PG[(PostgreSQL + pgvector)]
    API --> Redis[(Redis)]
    API --> Files[Private file / object storage]
    API --> AI[AI Providers]
    Web --> Lab[Browser-side virtual labs\n2D / 3D simulations]
    Lab -->|Validated event submissions| API
    API -->|Hints and feedback| Web
  end

  Marketing -. Optional navigation link .-> Web
```

**Current operation**: Run and verify the portal locally. A future deployment to `lms.studytomy.com` would require separate operational and business approval. Repository security workflows do not build or deploy the application, and removing repository configuration does not disconnect external GitHub Apps or hosting accounts. The public website remains separate with its own deployment, authentication sessions, and data storage.

### Architecture boundaries

- **Web**: Displays learning flows and gathers actions; never holds AI keys or privileged database credentials.
- **API**: Enforces authentication, learner/tutor/guardian access, data validation, policy and idempotency.
- **Domain services**: Reuse existing course/assignment services; add focused Studytomy modules for real gaps.
- **PostgreSQL**: Source of truth for identities, assignments, learner records, scheduling, evidence and results.
- **Redis**: Supporting cache/background infrastructure; do not treat it as durable grade storage.
- **Labs**: Deterministic calculations and rendering in the browser; report bounded, validated experiment events.
- **AI**: Explains, drafts and proposes; deterministic graders and approved rubrics remain authoritative.

## Technology choices

### Current source stack

This inventory describes the checked-out source and committed dependency declarations, not a live deployment attestation. Versions below come from manifests, lockfiles and Dockerfiles, not registry latest-version claims. Snapshot: 23 November 2026.

| Layer | Technology and version | Evidence |
|---|---|---|
| Portal | Next.js 16.4.6, React / React DOM 19.3.1, TypeScript 6.0.3 | `apps/web/package.json`, `apps/web/bun.lock` |
| Styling and UI | Tailwind CSS 4.3.5, Radix UI components, Tiptap 3.32.3, CodeMirror 6 packages | Web inventory below |
| API | Python 3.14.7, FastAPI 0.162.0, Pydantic 2.14.3, SQLModel 0.0.44, Alembic 1.20.1 | `apps/api/pyproject.toml`, `apps/api/uv.lock` |
| Collaboration | Hocuspocus 4.9.0, Yjs 13.6.33, ioredis 6.1.0 | `apps/collab/package.json`, `apps/collab/bun.lock` |
| AI adapters | Pydantic AI Slim 2.66.0 (OpenAI, Anthropic, Google, Mistral extras), Google GenAI 2.34.0 | API inventory; provider configuration and approval still required |
| Package/build tooling | Bun 1.4.5 in CI and component images. The combined Dockerfile still pins Bun 1.4.4. It uses Node 26.13.0 and PM2 7.1.0. | `.bun-version`, `.github/workflows/ci-build.yml`, root and component Dockerfiles |
| Database and cache templates | Fresh CLI-managed local databases use `pgvector/pgvector:0.8.7-pg19` pinned by multi-architecture digest; local development uses an isolated PG19 profile | `apps/cli/src/constants.ts`, `apps/cli/src/commands/dev.ts`; existing installs retain their selected database image and mount until explicitly migrated |
| Proxy templates | `nginx:1.30-alpine`, `caddy:2.11-alpine`, `alpine/socat:1.8.1.3` | `apps/cli/src/constants.ts`; OS packages installed by `apk` are not exactly pinned |
| Tests | Bun test, pytest 9.2.0, Playwright 1.65.1, CLI Vitest 5.1.1 | Per-component manifests below |
| Documentation | Next.js 16.4.5, Fumadocs Core 16.18.1 / MDX 15.5.0 | `docs/package.json`, `docs/bun.lock` |
| Headless clients | `@learnhouse/sdk`, `@learnhouse/react`, `@learnhouse/vue` 1.4.4 | `packages/*/package.json`; separate from portal runtime |

The CLI's default application image is `ghcr.io/learnhouse/app:latest` (development: `:dev`), an upstream runtime that does not include changes from this repository; it is not a verified Studytomy build. The CLI prints this identity during setup and updates. For source development, build the CLI from this checkout and run its `dev` command as shown in [Quickstart](docs/content/getting-started/quickstart.mdx). The Python pgvector client version (0.5.1) is not the database extension version. Optional integrations and Enterprise-related packages in manifests do not prove that their associated features are licensed, enabled or deployed. No Supabase dependency is declared in these manifests.

### Complete direct dependency inventory

Every direct dependency declaration in the repository's application, documentation and package manifests is included below, including development and peer dependencies. **Declared** preserves the requested range; **Locked** records the matching package entry from that component's lockfile. Peer ranges describe consumer compatibility, not a promise to ship every compatible version. Local workspace dependencies use the workspace package version. These are not independently verified installed/runtime versions. Overrides remain authoritative in each linked manifest and are reflected in lock resolutions.

<details>
<summary>apps/web: 149 direct declarations</summary>

Source: [apps/web/package.json](apps/web/package.json); lock: [apps/web/bun.lock](apps/web/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `@codemirror/autocomplete` | dependencies | `^6.20.4` | `6.20.4` |
| `@codemirror/lang-cpp` | dependencies | `^6.0.3` | `6.0.3` |
| `@codemirror/lang-go` | dependencies | `^6.0.1` | `6.0.1` |
| `@codemirror/lang-java` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-javascript` | dependencies | `^6.2.5` | `6.2.5` |
| `@codemirror/lang-markdown` | dependencies | `^6.5.2` | `6.5.2` |
| `@codemirror/lang-php` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-python` | dependencies | `^6.2.1` | `6.2.1` |
| `@codemirror/lang-rust` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-sass` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-sql` | dependencies | `^6.10.0` | `6.10.0` |
| `@codemirror/lang-wast` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-xml` | dependencies | `^6.1.0` | `6.1.0` |
| `@codemirror/legacy-modes` | dependencies | `^6.5.5` | `6.5.5` |
| `@codemirror/lint` | dependencies | `^6.9.8` | `6.9.8` |
| `@codemirror/state` | dependencies | `^6.7.6` | `6.7.6` |
| `@codemirror/view` | dependencies | `^6.43.15` | `6.43.15` |
| `@emoji-mart/data` | dependencies | `^1.2.1` | `1.2.1` |
| `@emoji-mart/react` | dependencies | `^1.1.1` | `1.1.1` |
| `@hello-pangea/dnd` | dependencies | `^18.0.1` | `18.0.1` |
| `@hocuspocus/provider` | dependencies | `^4.9.0` | `4.9.0` |
| `@icons-pack/react-simple-icons` | dependencies | `^13.16.0` | `13.16.0` |
| `@livekit/components-react` | dependencies | `^2.9.26` | `2.9.26` |
| `@marsidev/react-turnstile` | dependencies | `^1.6.3` | `1.6.3` |
| `@phosphor-icons/react` | dependencies | `^2.1.10` | `2.1.10` |
| `@radix-ui/react-aspect-ratio` | dependencies | `^1.2.0` | `1.2.0` |
| `@radix-ui/react-checkbox` | dependencies | `^1.4.1` | `1.4.1` |
| `@radix-ui/react-dialog` | dependencies | `^1.3.1` | `1.3.1` |
| `@radix-ui/react-direction` | dependencies | `^1.2.0` | `1.2.0` |
| `@radix-ui/react-dropdown-menu` | dependencies | `^2.2.1` | `2.2.1` |
| `@radix-ui/react-form` | dependencies | `^0.3.0` | `0.3.0` |
| `@radix-ui/react-hover-card` | dependencies | `^1.2.1` | `1.2.1` |
| `@radix-ui/react-icons` | dependencies | `^1.3.2` | `1.3.2` |
| `@radix-ui/react-label` | dependencies | `^2.1.17` | `2.1.17` |
| `@radix-ui/react-navigation-menu` | dependencies | `^1.4.1` | `1.4.1` |
| `@radix-ui/react-popover` | dependencies | `^1.3.1` | `1.3.1` |
| `@radix-ui/react-portal` | dependencies | `^1.2.0` | `1.2.0` |
| `@radix-ui/react-select` | dependencies | `^2.4.1` | `2.4.1` |
| `@radix-ui/react-slot` | dependencies | `^1.5.0` | `1.5.0` |
| `@radix-ui/react-switch` | dependencies | `^1.4.1` | `1.4.1` |
| `@radix-ui/react-tabs` | dependencies | `^1.2.1` | `1.2.1` |
| `@radix-ui/react-toggle` | dependencies | `^1.2.1` | `1.2.1` |
| `@radix-ui/react-toggle-group` | dependencies | `^1.2.1` | `1.2.1` |
| `@radix-ui/react-tooltip` | dependencies | `^1.4.1` | `1.4.1` |
| `@react-email/components` | dependencies | `1.0.12` | `1.0.12` |
| `@sentry/nextjs` | dependencies | `^11.16.0` | `11.16.0` |
| `@tanstack/react-query` | dependencies | `^5.108.4` | `5.108.4` |
| `@tanstack/react-query-devtools` | dependencies | `^5.108.4` | `5.108.4` |
| `@tiptap/core` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-bullet-list` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-code-block-lowlight` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-collaboration` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-collaboration-caret` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-heading` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-link` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-list-item` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-ordered-list` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-placeholder` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table-cell` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table-header` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table-row` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-youtube` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/html` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/pm` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/react` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/starter-kit` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/static-renderer` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/suggestion` | dependencies | `3.32.3` | `3.32.3` |
| `@uiw/codemirror-theme-tokyo-night` | dependencies | `^4.25.12` | `4.25.12` |
| `@uiw/react-codemirror` | dependencies | `^4.25.12` | `4.25.12` |
| `@videojs/hlsjs-video` | dependencies | `^10.6.0` | `10.6.0` |
| `@videojs/react` | dependencies | `^10.6.0` | `10.6.0` |
| `avvvatars-react` | dependencies | `^0.4.2` | `0.4.2` |
| `class-variance-authority` | dependencies | `^0.7.1` | `0.7.1` |
| `clsx` | dependencies | `^2.1.1` | `2.1.1` |
| `cmdk` | dependencies | `^1.1.1` | `1.1.1` |
| `cobe` | dependencies | `^2.0.1` | `2.0.1` |
| `currency-codes` | dependencies | `^2.2.0` | `2.2.0` |
| `dayjs` | dependencies | `^1.11.23` | `1.11.23` |
| `dompurify` | dependencies | `^3.5.0` | `3.5.0` |
| `emblor` | dependencies | `^1.4.8` | `1.4.8` |
| `formik` | dependencies | `^2.4.9` | `2.4.9` |
| `get-youtube-id` | dependencies | `^1.0.1` | `1.0.1` |
| `github-markdown-css` | dependencies | `^5.9.0` | `5.9.0` |
| `highlight.js` | dependencies | `^11.12.0` | `11.12.0` |
| `html2canvas` | dependencies | `^1.4.1` | `1.4.1` |
| `html2canvas-pro` | dependencies | `^2.6.4` | `2.6.4` |
| `i18next` | dependencies | `^26.7.2` | `26.7.2` |
| `i18next-browser-languagedetector` | dependencies | `^8.3.1` | `8.3.1` |
| `jspdf` | dependencies | `^4.2.1` | `4.2.1` |
| `jspdf-html2canvas` | dependencies | `^1.7.0` | `1.7.0` |
| `katex` | dependencies | `^0.19.1` | `0.19.1` |
| `livekit-client` | dependencies | `^2.25.0` | `2.25.0` |
| `loops` | dependencies | `^7.5.0` | `7.5.0` |
| `lowlight` | dependencies | `^3.3.0` | `3.3.0` |
| `lucide-react` | dependencies | `^1.78.0` | `1.78.0` |
| `mailchecker` | dependencies | `^6.0.21` | `6.0.21` |
| `motion` | dependencies | `^14.14.0` | `14.14.0` |
| `next` | dependencies | `^16.4.6` | `16.4.6` |
| `nextjs-toploader` | dependencies | `^3.9.17` | `3.9.17` |
| `pdfjs-dist` | dependencies | `^6.5.438` | `6.5.438` |
| `posthog-js` | dependencies | `^1.454.1` | `1.454.1` |
| `prosemirror-state` | dependencies | `^1.4.4` | `1.4.4` |
| `qrcode` | dependencies | `^1.5.4` | `1.5.4` |
| `re-resizable` | dependencies | `^6.11.2` | `6.11.2` |
| `react` | dependencies | `19.3.1` | `19.3.1` |
| `react-confetti` | dependencies | `^6.4.0` | `6.4.0` |
| `react-dom` | dependencies | `19.3.1` | `19.3.1` |
| `react-hot-toast` | dependencies | `^2.6.1` | `2.6.1` |
| `react-i18next` | dependencies | `^17.1.13` | `17.1.13` |
| `react-katex` | dependencies | `^3.1.0` | `3.1.0` |
| `react-markdown` | dependencies | `^10.1.0` | `10.1.0` |
| `react-spinners` | dependencies | `^0.17.1` | `0.17.1` |
| `react-youtube` | dependencies | `^10.1.0` | `10.1.0` |
| `recharts` | dependencies | `^3.11.0` | `3.11.0` |
| `rehype-highlight` | dependencies | `^7.0.2` | `7.0.2` |
| `rehype-katex` | dependencies | `^7.0.1` | `7.0.1` |
| `rehype-raw` | dependencies | `^7.0.0` | `7.0.0` |
| `remark-gfm` | dependencies | `^4.0.1` | `4.0.1` |
| `remark-math` | dependencies | `^6.0.0` | `6.0.0` |
| `require-in-the-middle` | dependencies | `^8.0.1` | `8.0.1` |
| `resend` | dependencies | `^6.39.0` | `6.39.0` |
| `screenfull` | dependencies | `^6.0.2` | `6.0.2` |
| `sharp` | dependencies | `^0.35.5` | `0.35.5` |
| `stripe` | dependencies | `^23.2.0` | `23.2.0` |
| `swr` | dependencies | `^2.5.1` | `2.5.1` |
| `tailwind-merge` | dependencies | `^3.7.1` | `3.7.1` |
| `tailwindcss-animate` | dependencies | `^1.0.7` | `1.0.7` |
| `tippy.js` | dependencies | `^6.3.7` | `6.3.7` |
| `usehooks-ts` | dependencies | `^3.1.1` | `3.1.1` |
| `uuid` | dependencies | `^14.0.3` | `14.0.3` |
| `wavesurfer.js` | dependencies | `^8.1.0` | `8.1.0` |
| `y-prosemirror` | dependencies | `^1.3.7` | `1.3.7` |
| `yjs` | dependencies | `^13.6.33` | `13.6.33` |
| `yup` | dependencies | `^1.7.1` | `1.7.1` |
| `@eslint/js` | devDependencies | `^9.39.5` | `9.39.5` |
| `@tailwindcss/postcss` | devDependencies | `^4.3.5` | `4.3.5` |
| `@types/node` | devDependencies | `26.6.6` | `26.6.6` |
| `@types/qrcode` | devDependencies | `^1.5.6` | `1.5.6` |
| `@types/react` | devDependencies | `19.3.0` | `19.3.0` |
| `@types/react-dom` | devDependencies | `19.3.0` | `19.3.0` |
| `@types/react-katex` | devDependencies | `^3.0.4` | `3.0.4` |
| `eslint` | devDependencies | `^9.39.5` | `9.39.5` |
| `eslint-config-next` | devDependencies | `16.4.5` | `16.4.5` |
| `eslint-plugin-unused-imports` | devDependencies | `^4.4.1` | `4.4.1` |
| `postcss` | devDependencies | `^8.5.33` | `8.5.33` |
| `tailwindcss` | devDependencies | `^4.3.5` | `4.3.5` |
| `typescript` | devDependencies | `6.0.3` | `6.0.3` |

</details>

<details>
<summary>apps/collab: 10 direct declarations</summary>

Source: [apps/collab/package.json](apps/collab/package.json); lock: [apps/collab/bun.lock](apps/collab/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `@hocuspocus/extension-database` | dependencies | `^4.9.0` | `4.9.0` |
| `@hocuspocus/server` | dependencies | `^4.9.0` | `4.9.0` |
| `dotenv` | dependencies | `^18.0.10` | `18.0.10` |
| `ioredis` | dependencies | `^6.1.0` | `6.1.0` |
| `jsonwebtoken` | dependencies | `^9.0.3` | `9.0.3` |
| `yjs` | dependencies | `^13.6.33` | `13.6.33` |
| `@types/jsonwebtoken` | devDependencies | `^9.0.10` | `9.0.10` |
| `@types/node` | devDependencies | `^26.6.6` | `26.6.6` |
| `tsx` | devDependencies | `^4.23.15` | `4.23.15` |
| `typescript` | devDependencies | `^6.0.3` | `6.0.3` |

</details>

<details>
<summary>apps/cli: 9 direct declarations</summary>

Source: [apps/cli/package.json](apps/cli/package.json); lock: [apps/cli/bun.lock](apps/cli/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `@clack/core` | devDependencies | `^1.6.0` | `1.6.0` |
| `@clack/prompts` | devDependencies | `^1.9.0` | `1.9.0` |
| `@types/node` | devDependencies | `^26.6.6` | `26.6.6` |
| `@vitest/coverage-v8` | devDependencies | `^5.1.1` | `5.1.1` |
| `commander` | devDependencies | `^15.1.0` | `15.1.0` |
| `picocolors` | devDependencies | `^1.1.1` | `1.1.1` |
| `tsup` | devDependencies | `^9.0.0` | `9.0.0` |
| `typescript` | devDependencies | `^7.0.2` | `7.0.2` |
| `vitest` | devDependencies | `^5.1.1` | `5.1.1` |

</details>

<details>
<summary>apps/e2e: 3 direct declarations</summary>

Source: [apps/e2e/package.json](apps/e2e/package.json); lock: [apps/e2e/bun.lock](apps/e2e/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `@playwright/test` | devDependencies | `^1.65.1` | `1.65.1` |
| `@types/node` | devDependencies | `^26.6.6` | `26.6.6` |
| `typescript` | devDependencies | `^6.0.3` | `6.0.3` |

</details>

<details>
<summary>docs: 19 direct declarations</summary>

Source: [docs/package.json](docs/package.json); lock: [docs/bun.lock](docs/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `@icons-pack/react-simple-icons` | dependencies | `^13.16.0` | `13.16.0` |
| `@phosphor-icons/react` | dependencies | `^2.1.10` | `2.1.10` |
| `@types/mdx` | dependencies | `^2.0.14` | `2.0.14` |
| `@xyflow/react` | dependencies | `^12.12.1` | `12.12.1` |
| `fumadocs-core` | dependencies | `16.18.1` | `16.18.1` |
| `fumadocs-mdx` | dependencies | `15.5.0` | `15.5.0` |
| `next` | dependencies | `16.4.5` | `16.4.5` |
| `next-sitemap` | dependencies | `^4.2.3` | `4.2.3` |
| `posthog-js` | dependencies | `^1.454.1` | `1.454.1` |
| `react` | dependencies | `^19.3.1` | `19.3.1` |
| `react-dom` | dependencies | `^19.3.1` | `19.3.1` |
| `shiki` | dependencies | `^4.6.0` | `4.6.0` |
| `zod` | dependencies | `^4.6.7` | `4.6.7` |
| `@tailwindcss/postcss` | devDependencies | `^4.3.5` | `4.3.5` |
| `@types/node` | devDependencies | `26.6.6` | `26.6.6` |
| `@types/react` | devDependencies | `19.3.0` | `19.3.0` |
| `pagefind` | devDependencies | `^1.6.0` | `1.6.0` |
| `tailwindcss` | devDependencies | `^4.3.5` | `4.3.5` |
| `typescript` | devDependencies | `6.0.3` | `6.0.3` |

</details>

<details>
<summary>packages: 0 direct declarations</summary>

Source: [packages/package.json](packages/package.json); lock: [packages/bun.lock](packages/bun.lock).

Workspace orchestration only; no direct dependencies declared.

</details>

<details>
<summary>packages/sdk: 4 direct declarations</summary>

Source: [packages/sdk/package.json](packages/sdk/package.json); lock: [packages/bun.lock](packages/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `openapi-typescript` | devDependencies | `7.13.0` | `7.13.0` |
| `tsup` | devDependencies | `^9.0.0` | `9.0.0` |
| `typescript` | devDependencies | `5.9.3` | `5.9.3` |
| `vitest` | devDependencies | `^5.1.0` | `5.1.0` |

</details>

<details>
<summary>packages/react: 93 direct declarations</summary>

Source: [packages/react/package.json](packages/react/package.json); lock: [packages/bun.lock](packages/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `@codemirror/autocomplete` | dependencies | `^6.20.4` | `6.20.4` |
| `@codemirror/lang-cpp` | dependencies | `^6.0.3` | `6.0.3` |
| `@codemirror/lang-go` | dependencies | `^6.0.1` | `6.0.1` |
| `@codemirror/lang-java` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-javascript` | dependencies | `^6.2.5` | `6.2.5` |
| `@codemirror/lang-markdown` | dependencies | `^6.5.2` | `6.5.2` |
| `@codemirror/lang-php` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-python` | dependencies | `^6.2.1` | `6.2.1` |
| `@codemirror/lang-rust` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-sass` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-sql` | dependencies | `^6.10.0` | `6.10.0` |
| `@codemirror/lang-wast` | dependencies | `^6.0.2` | `6.0.2` |
| `@codemirror/lang-xml` | dependencies | `^6.1.0` | `6.1.0` |
| `@codemirror/language` | dependencies | `^6.13.1` | `6.13.1` |
| `@codemirror/legacy-modes` | dependencies | `^6.5.5` | `6.5.5` |
| `@codemirror/lint` | dependencies | `^6.9.7` | `6.9.8` |
| `@codemirror/state` | dependencies | `^6.7.6` | `6.7.6` |
| `@codemirror/view` | dependencies | `^6.43.15` | `6.43.15` |
| `@emoji-mart/react` | dependencies | `^1.1.1` | `1.1.1` |
| `@icons-pack/react-simple-icons` | dependencies | `^13.15.1` | `13.15.1` |
| `@learnhouse/sdk` | dependencies | `1.4.4` | `1.4.4 (workspace)` |
| `@phosphor-icons/react` | dependencies | `^2.1.10` | `2.1.10` |
| `@radix-ui/react-checkbox` | dependencies | `^1.4.1` | `1.4.1` |
| `@radix-ui/react-dialog` | dependencies | `^1.3.1` | `1.3.1` |
| `@radix-ui/react-form` | dependencies | `^0.3.0` | `0.3.0` |
| `@radix-ui/react-hover-card` | dependencies | `^1.2.1` | `1.2.1` |
| `@radix-ui/react-icons` | dependencies | `^1.3.2` | `1.3.2` |
| `@radix-ui/react-label` | dependencies | `^2.1.17` | `2.1.17` |
| `@radix-ui/react-slot` | dependencies | `^1.5.0` | `1.5.0` |
| `@tanstack/react-query` | dependencies | `^5.106.1` | `5.107.1` |
| `@tiptap/core` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-code-block-lowlight` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-heading` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-link` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table-cell` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table-header` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-table-row` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/extension-youtube` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/pm` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/react` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/starter-kit` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/static-renderer` | dependencies | `3.32.3` | `3.32.3` |
| `@tiptap/suggestion` | dependencies | `3.32.3` | `3.32.3` |
| `@types/react` | dependencies | `19.3.0` | `19.3.0` |
| `@types/react-dom` | dependencies | `19.3.0` | `19.3.0` |
| `@uiw/codemirror-theme-tokyo-night` | dependencies | `^4.25.12` | `4.25.12` |
| `@uiw/react-codemirror` | dependencies | `^4.25.12` | `4.25.12` |
| `@videojs/hlsjs-video` | dependencies | `^10.3.0` | `10.3.0` |
| `@videojs/react` | dependencies | `^10.3.0` | `10.3.0` |
| `class-variance-authority` | dependencies | `^0.7.1` | `0.7.1` |
| `clsx` | dependencies | `^2.1.1` | `2.1.1` |
| `dompurify` | dependencies | `^3.4.17` | `3.4.17` |
| `get-youtube-id` | dependencies | `^1.0.1` | `1.0.1` |
| `github-markdown-css` | dependencies | `^5.9.0` | `5.9.0` |
| `highlight.js` | dependencies | `^11.12.0` | `11.12.0` |
| `i18next` | dependencies | `^26.7.2` | `26.7.2` |
| `katex` | dependencies | `^0.19.0` | `0.19.1` |
| `lowlight` | dependencies | `^3.3.0` | `3.3.0` |
| `lucide-react` | dependencies | `^1.69.0` | `1.70.0` |
| `motion` | dependencies | `^14.9.1` | `14.9.1` |
| `pdfjs-dist` | dependencies | `^6.5.438` | `6.5.438` |
| `prosemirror-state` | dependencies | `^1.4.4` | `1.4.4` |
| `re-resizable` | dependencies | `^6.11.2` | `6.11.2` |
| `react-confetti` | dependencies | `^6.4.0` | `6.4.0` |
| `react-hot-toast` | dependencies | `^2.6.1` | `2.6.1` |
| `react-i18next` | dependencies | `^17.1.13` | `17.1.13` |
| `react-katex` | dependencies | `^3.1.0` | `3.1.0` |
| `react-markdown` | dependencies | `^10.1.0` | `10.1.0` |
| `react-youtube` | dependencies | `^10.1.0` | `10.1.0` |
| `rehype-highlight` | dependencies | `^7.0.2` | `7.0.2` |
| `remark-gfm` | dependencies | `^4.0.1` | `4.0.1` |
| `swr` | dependencies | `^2.5.1` | `2.5.1` |
| `tailwind-merge` | dependencies | `^3.7.0` | `3.7.0` |
| `tippy.js` | dependencies | `^6.3.7` | `6.3.7` |
| `typescript` | dependencies | `6.0.3` | `6.0.3` |
| `uuid` | dependencies | `^14.0.3` | `14.0.3` |
| `@tailwindcss/postcss` | devDependencies | `^4.3.3` | `4.3.4` |
| `@testing-library/react` | devDependencies | `^16.3.3` | `16.3.3` |
| `@types/react` | devDependencies | `^19.3.0` | `19.3.0` |
| `@types/react-dom` | devDependencies | `^19.3.0` | `19.3.0` |
| `@vitejs/plugin-react` | devDependencies | `^6.2.1` | `6.2.1` |
| `esbuild` | devDependencies | `^0.28.2` | `0.28.2` |
| `jsdom` | devDependencies | `^30.1.3` | `30.1.3` |
| `postcss` | devDependencies | `^8.5.32` | `8.5.33` |
| `postcss-prefix-selector` | devDependencies | `^2.2.1` | `2.2.1` |
| `tailwindcss` | devDependencies | `^4.3.3` | `4.3.4` |
| `tailwindcss-animate` | devDependencies | `^1.0.7` | `1.0.7` |
| `typescript` | devDependencies | `6.0.3` | `6.0.3` |
| `vite` | devDependencies | `^8.3.5` | `8.3.5` |
| `vitest` | devDependencies | `^5.1.0` | `5.1.0` |
| `react` | peerDependencies | `^18.2.0 &#124;&#124; ^19.0.0` | `19.3.1` |
| `react-dom` | peerDependencies | `^18.2.0 &#124;&#124; ^19.0.0` | `19.3.1` |

</details>

<details>
<summary>packages/vue: 12 direct declarations</summary>

Source: [packages/vue/package.json](packages/vue/package.json); lock: [packages/bun.lock](packages/bun.lock).

| Library | Manifest group | Declared | Locked |
|---|---|---|---|
| `@learnhouse/react` | dependencies | `1.4.4` | `1.4.4 (workspace)` |
| `@learnhouse/sdk` | dependencies | `1.4.4` | `1.4.4 (workspace)` |
| `react` | dependencies | `^19.3.1` | `19.3.1` |
| `react-dom` | dependencies | `^19.3.1` | `19.3.1` |
| `@types/node` | devDependencies | `^26.6.6` | `26.6.6` |
| `@vue/test-utils` | devDependencies | `^2.5.1` | `2.5.1` |
| `jsdom` | devDependencies | `^30.1.3` | `30.1.3` |
| `tsup` | devDependencies | `^9.0.0` | `9.0.0` |
| `typescript` | devDependencies | `6.0.3` | `6.0.3` |
| `vitest` | devDependencies | `^5.1.0` | `5.1.0` |
| `vue` | devDependencies | `^3.5.45` | `3.5.45` |
| `vue` | peerDependencies | `^3.3.0` | `3.5.45` |

</details>

<details>
<summary>apps/api: Python direct dependencies</summary>

Source: [pyproject.toml](apps/api/pyproject.toml), [uv.lock](apps/api/uv.lock). Tests are currently declared in the main dependency list, not a separate development group.

| Library / extras | Declared | Locked |
|---|---|---|
| `aiosqlite` | `==0.22.1` | `0.22.1` |
| `alembic-postgresql-enum` | `==1.11.1` | `1.11.1` |
| `alembic` | `==1.20.1` | `1.20.1` |
| `asyncpg` | `==0.32.0` | `0.32.0` |
| `beautifulsoup4` | `==4.15.0` | `4.15.0` |
| `boto3` | `==1.43.124` | `1.43.124` |
| `botocore` | `==1.43.124` | `1.43.124` |
| `cryptography` | `==50.0.2` | `50.0.2` |
| `defusedxml` | `==0.7.1` | `0.7.1` |
| `dnspython` | `==2.9.2` | `2.9.2` |
| `faker` | `==40.44.0` | `40.44.0` |
| `fakeredis` | `==2.40.0` | `2.40.0` |
| `fastapi` | `==0.162.0` | `0.162.0` |
| `google-genai` | `==2.34.0` | `2.34.0` |
| `httpx` | `==0.28.1` | `0.28.1` |
| `mcp` | `==2.3.0` | `2.3.0` |
| `pgvector` | `==0.5.1` | `0.5.1` |
| `pillow` | `==13.0.0` | `13.0.0` |
| `protobuf` | `==7.36.2` | `7.36.2` |
| `psycopg2-binary` | `==2.9.13` | `2.9.13` |
| `pwdlib[argon2]` | `==0.3.1` | `0.3.1` |
| `pydantic-ai-slim[openai,anthropic,google,mistral]` | `==2.66.0` | `2.66.0` |
| `pydantic[email]` | `==2.14.3` | `2.14.3` |
| `pyjwt[crypto]` | `==2.15.1` | `2.15.1` |
| `pyotp` | `==2.10.0` | `2.10.0` |
| `pypdf` | `==6.21.4` | `6.21.4` |
| `pytest-asyncio` | `==1.4.1.post1` | `1.4.1.post1` |
| `pytest-cov` | `==7.1.0` | `7.1.0` |
| `pytest-xdist` | `==3.8.0` | `3.8.0` |
| `pytest` | `==9.2.0` | `9.2.0` |
| `python-dotenv` | `==1.2.4` | `1.2.4` |
| `python-multipart` | `==0.0.33` | `0.0.33` |
| `pyyaml` | `==6.0.3` | `6.0.3` |
| `redis` | `==8.1.0` | `8.1.0` |
| `regex` | `==2026.9.29` | `2026.9.29` |
| `requests` | `==2.34.2` | `2.34.2` |
| `resend` | `==2.51.0` | `2.51.0` |
| `sentry-sdk[fastapi]` | `==2.74.0` | `2.74.0` |
| `sqlalchemy-utils` | `==0.43.0` | `0.43.0` |
| `sqlmodel` | `==0.0.44` | `0.0.44` |
| `stripe` | `==16.1.0` | `16.1.0` |
| `tiktoken` | `==0.14.0` | `0.14.0` |
| `typer` | `==0.27.3` | `0.27.3` |
| `uvicorn` | `==0.54.0` | `0.54.0` |
| `workos` | `==10.7.0` | `10.7.0` |

</details>

### Transitive libraries and reproducibility

The tables above enumerate direct declarations, not the entire transitive dependency graph. The following lockfiles are the exhaustive version inventories (including transitive and platform-conditional packages); consult them for every indirect library, integrity hash and dependency edge. Do not infer that every locked optional/platform package is installed on every machine.

| Component | Full version inventory | Locked package records |
|---|---|---|
| apps/web | [apps/web/bun.lock](apps/web/bun.lock) | 1171 |
| apps/collab | [apps/collab/bun.lock](apps/collab/bun.lock) | 68 |
| apps/cli | [apps/cli/bun.lock](apps/cli/bun.lock) | 187 |
| apps/e2e | [apps/e2e/bun.lock](apps/e2e/bun.lock) | 6 |
| docs | [docs/bun.lock](docs/bun.lock) | 383 |
| packages | [packages/bun.lock](packages/bun.lock) | 622 |
| API | [apps/api/uv.lock](apps/api/uv.lock) | 119 (includes local project) |

Use frozen lockfile installs (`bun install --frozen-lockfile`, `uv sync --frozen`) instead of silently resolving newer versions. Regenerate this inventory when dependency declarations or locks change. External AI model names, container tag digests and deployment-specific service versions must be recorded separately in a release receipt.

## User journeys

### Learner

1. Sign in and access only authorized courses.
2. Complete a published lesson and request contextual AI help.
3. Attempt an approved question or assignment.
4. Receive supported automatic grading or tutor-reviewed feedback.
5. View completion records and next activities.

**Enhanced journey (planned)**: Complete a baseline diagnostic → receive an approved learning plan → practise targeted objectives → repeat comparable assessments → review verified progress.

### Tutor

- Author or import lessons and reference content.
- Draft quizzes/activities using AI, then verify and publish them.
- Review submissions, especially files or uncertain answers.
- Use permitted student evidence to plan follow-up teaching.

### Guardian (planned)

View only explicitly linked children's approved progress information and communications. Guardians do not inherit access to full student records or other children's data.

### Science laboratory (local pilot)

Select experiment → predict result → manipulate variables → record measurements → explain observation → validated assessment → tutor/AI feedback → save evidence.

Pilot 01 (*Conservation of Linear Momentum*, `apps/labs/momentum-pilot`, Grade 12 G.C.E. A/L Physics Unit 2) implements a bounded version of this journey locally, with managed assignment submission, release-bound grading, build-integrity binding and live browser specs. Its [specification](docs/studytomy/VIRTUAL-LAB-MOMENTUM-PILOT-SPEC.md) is still a draft awaiting qualified educator sign-off and syllabus-edition provenance, so no student launch, NIE endorsement or physical-laboratory equivalence is claimed.

## AI and virtual laboratory principles

### AI tutor and assessment

- Ground explanations and generated questions in versioned, educator-approved material.
- Treat prompts, uploaded documents, model responses, and retrieved web data as untrusted.
- Never write final grades directly from unchecked model output.
- Record the question version, rubric version, answer evidence, evaluator result, and tutor override where applicable.
- Define low-confidence or unsupported answers as *needs review*, not silently correct/incorrect.
- Apply per-user usage limits, retries, timeouts, and model-provider privacy controls.

### Simulation contract (proposed)

Every adopted lab should expose a consistent conceptual boundary:

| Operation | Responsibility |
|---|---|
| **Initialize** | Load a versioned experiment, starting state, and allowed variables. |
| **Apply action** | Validate and apply a student action to the scientific model. |
| **Measure** | Return units, measured values, assumptions, and tolerances. |
| **Record event** | Produce a stable, unique, bounded event for submission/retry safety. |
| **Evaluate** | Score only against approved experimental criteria and reference cases. |
| **Explain** | Pass validated outcomes to the AI tutor for contextual guidance. |
| **Resume** | Reconstruct state from an authorized snapshot or accepted event sequence. |

For initial labs, prefer a scientifically validated 2D Ohm's Law or motion experiment. Expand into 3D only where it improves learning. Chemistry models require domain-specific validation; a 3D physics engine alone does not simulate chemical reactions correctly.

### Content quality and intellectual property

- Identify curriculum owners, reviewers, and the approved source of truth for the Sri Lankan National School Syllabus (NIE / Ministry of Education).
- Do not presume redistribution rights for past examination papers, marking schemes, lab assets, or images.
- Check licenses and asset provenance before integrating third-party simulation code.
- Do not state or imply that generated mock exams are official G.C.E. Ordinary Level or Advanced Level examinations.

## Repository organization

The underlying source has these existing upstream application roots:

```text
apps/
  web/       # Next.js portal and authoring interfaces
  api/       # FastAPI, database models, API routers, services, migrations
  collab/    # Collaborative editing server
  cli/       # Local development, diagnostics and self-hosting tools
```

Studytomy-specific modules follow the existing application structure:

```text
apps/api/src/routers/studytomy/     # Scoped HTTP endpoints
apps/api/src/services/studytomy/    # Use cases and domain rules
apps/api/src/db/studytomy/          # Additive SQLModel entities
apps/api/migrations/versions/       # Existing Alembic location for additive migrations
apps/web/app/                       # Existing routes; new Studytomy pages where appropriate
apps/web/components/                # Reusable Studytomy UI components
```

The `studytomy` backend directories and `apps/web/components/Studytomy` now exist. Keep extensions small, isolated, and compatible with upstream changes. Follow the repository's applicable `AGENTS.md` and local conventions.

## Getting started

### 1. Work from this fork

```bash
git clone https://github.com/nirzaf/studytomyportal.git
cd studytomyportal
# In apps/web, apps/collab or another JS component:
bun install --frozen-lockfile
# In apps/api, with the required Python 3.14.7 available:
uv sync --frozen
```

Run commands in the component directory indicated, not as a single root script. Start required PostgreSQL/Redis services and configure supported environment variables before starting the API. To use the local development CLI from this checkout, build it with `bun --cwd apps/cli run build` and run `node apps/cli/dist/bin/learnhouse.js dev`. `npx learnhouse@latest setup` configures the published upstream runtime image; it does not build or deploy this checkout. The combined Dockerfile and CLI-generated Compose templates support local builds and runtime tests; they do not configure a hosted environment or deploy this project.

### 2. Development commands

- Web (`apps/web`): `bun run dev`, `bun run build`, `bun run test`, `bun run lint:strict`.
- API (`apps/api`): `uv run pytest`; use focused paths under `src/tests` while developing. PostgreSQL-marked tests require an isolated `STUDYTOMY_TEST_DATABASE_URL`; never point them at production.
- Collaboration (`apps/collab`): `bun run dev`, `bun run build`, `bun run test`.
- CLI (`apps/cli`): `bun run build`, `bun run test`.
- Browser acceptance (`apps/e2e`): `bun run test` after configuring its target environment and browsers.
- SDK/renderers (`packages`): `bun run build`, `bun run test`, `bun run check`.

These are available scripts, not a statement that all checks currently pass. The web's ordinary `lint` script runs ESLint across the app and fails on errors only — warnings alone do not fail it; use `lint:strict -- <changed paths>`, adding `--max-warnings=0` when a warning budget is required, for a scoped gate. Do not run concurrent writers or heavyweight suites against another agent's in-progress verification.

### 3. Configuration

Use the checked-in guides as the reference for supported variable names and service setup:

- [Installation](docs/content/self-hosting/installation/index.mdx)
- [AI provider configuration](docs/content/self-hosting/configuration/ai-setup.mdx)
- [Email configuration](docs/content/self-hosting/configuration/email.mdx)
- [Storage configuration](docs/content/self-hosting/configuration/storage.mdx)

Never commit `.env`, provider keys, private certificates, database passwords, or student records. Optional services (such as AI, email, S3-compatible storage, and code execution) require separate configuration and verification.

### 4. Verification commands

From an appropriately configured upstream checkout or self-hosted installation, use its documented lifecycle commands:

```bash
npx learnhouse doctor
npx learnhouse logs
```

For application unit, integration, and end-to-end tests, use the scripts actually present in the pinned fork. Do not invent a single top-level test command. A passing upstream build alone does not validate Studytomy's integrations.

## Migration strategy

**Status**: **Not included by default.** The public website (`studytomy.com`, [`nirzaf/studytomy-react`](https://github.com/nirzaf/studytomy-react)) and Studytomy Portal (`lms.studytomy.com`, [`nirzaf/studytomyportal`](https://github.com/nirzaf/studytomyportal)) operate as independent applications with separate deployments, authentication sessions, and databases. Migrating the public website's Supabase database or connecting its business APIs to the LMS is out of scope unless separately authorized.

If a data migration is explicitly authorized in the future, follow a mapped migration process rather than a direct Supabase-to-PostgreSQL schema copy:

1. **Inventory**: Identify explicitly authorized records to migrate.
2. **Map**: Connect legacy identities and entities to verified LMS models without duplicate or orphaned records.
3. **Extract**: Export legacy data in a controlled, audited, read-only process.
4. **Transform and validate**: Normalize dates/time zones, statuses, record ownership, and cross-record references.
5. **Dry run**: Import into isolated staging and reconcile counts and representative workflows.
6. **Cut over**: Freeze required writes, perform final reconciliation, and retain rollback capability.

Do not assume Supabase password hashes or sessions are compatible with the LMS authentication system.

## Security, privacy, and governance

The platform involves minors and potentially sensitive educational information. Security requirements are release gates, not optional polish.

- **Access boundaries**: Students see their records only; tutors see only assigned students; guardians see only linked children; admins follow least-privilege roles.
- **Authorization**: Backend-enforced permissions for every read, write, document, session, and AI tool action. Never trust browser-supplied student or organization IDs alone.
- **Data protection**: Encrypt transport and storage where supported; protect backups; minimize retained uploads, audio, and prompts; define deletion and retention processes.
- **AI governance**: Approved providers, human oversight, versioned prompts/rubrics, safe errors, transparent limitations, and no secret exposure.
- **Children's safeguards**: Obtain legal/privacy review for Sri Lankan operations, guardian consent rules, cross-border data access, and applicable retention requirements.
- **Operations**: Centralized error logs without leaking learner content; rate limiting; restore drills; dependency updates; alert ownership.
- **Repository security checks.** The `main` ruleset requires Gitleaks and OSV checks from GitHub Actions. See the [security policy](SECURITY.md) for private reporting and the scope of each check.
- **Change approvals.** Obtain maintainer authorization for merges, destructive production operations, critical deletions, and Git history rewrites.

## Engineering standards

- Preserve upstream architecture and use the smallest change that satisfies an approved requirement.
- Keep domain validation, permission checks, transaction boundaries, and concurrency enforcement on the server.
- Use Alembic for PostgreSQL schema changes. No unreviewed destructive production migrations.
- Make imports, submissions, scheduled tasks, grade updates, and integration callbacks idempotent.
- Add targeted tests for changed behavior; do not replace existing tests with speculative test harnesses.
- Keep AI generation separate from final approved educational results.
- Measure user journeys and service behavior; do not infer scalability from framework choice.
- Maintain an upstream-change log and a focused fork-diff review before upgrades.

### Minimum quality gates

- A student cannot read or modify another student's protected data.
- An educator cannot access students outside their permitted relationships.
- A failed/retried submission does not award duplicate marks or progress.
- Known-answer assessments return the expected results; unsupported work reaches human review.
- Educational material and lab calculations pass approved reference-case checks.
- Migration record counts and critical links reconcile against signed-off baselines.
- Backup restore and rollback have been exercised in a non-production environment.
- AI/provider keys are absent from frontend bundles, logs, and exported assets.

## Roadmap and acceptance gates

Prioritize one reviewed PDF-backed topic through authoring, learner check, cited reteaching, recheck and scoped staff history before adding broader media grounding or generated widgets. See the [acceptance contract](docs/studytomy/TOPIC-LEARNING-LOOP.md#acceptance-on-the-same-published-version).

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0 — Foundation** | Pinned upstream fork, development/staging environments, PostgreSQL, roles, storage, AI configuration. | One authenticated course, lesson, assignment and feedback workflow is demonstrated. |
| **1 — Branding and migration** | Studytomy design, public website integration, approved operational record mapping. | Public journeys work and staged data reconciliation passes. |
| **2 — Learning pilot** | One approved National School Syllabus cohort/unit (subject to the open `D-02` pilot-slice decision), lesson-level AI, quizzes, supported grading and progress. | Educators sign off correctness, permissions, and representative learner workflows. |
| **3 — Learning intelligence** | Diagnostics, versioned skill evidence, learning plans, guardian visibility and exam preparation. | Evaluators pass reference cases; educators can inspect evidence and override outcomes. |
| **4 — Interactive science** | First validated lab experiment, measurements, saved evidence and scoring. | Subject-matter expert signs off scientific tests and student interaction acceptance. |
| **5 — Business enhancement** | Approved marketing agent, regional demand research and selected operational integrations. | Research cites sources; campaign actions require documented human approval. |

Phases are proposed, not committed dates or completed implementation milestones. Define the pilot scope and measurable targets in the approved requirements specification.

## Decisions still required

- Which launch model: human-tutor-supported, self-service AI, or both?
- First pilot slice within the National School Syllabus: grades, subjects, syllabus edition and cohort size. `D-01`/`D-02` are unapproved, and the Grade 12 A/L Physics laboratory pilot does not settle this choice.
- Which legacy tutoring workflows must migrate before launch?
- Public site: separate static frontend initially or consolidated Next.js routes?
- Exact AI provider, allowed student data, age safeguards, cost controls, and permitted AI autonomy?
- Which learning outputs need tutor approval, and who owns approved reference content?
- What does the initial parent/guardian experience need to show?
- Which first laboratory experiment, device targets, scoring rubric, and scientific reviewer?
- Which externally hosted services and separately licensed features are actually required?
- Hosting region, identity/permissions model, operational owner, and go-live acceptance criteria?

## Licensing

Studytomy Portal is derived from the [LearnHouse Community Edition](https://github.com/learnhouse/learnhouse), which is licensed under AGPL-3.0. This repository includes AGPL-3.0-covered code and may include separately licensed components. Comply with all applicable license terms, notices, and corresponding-source obligations. Review the license of each component before redistribution; obtain legal review before making closed-source or commercial licensing commitments.

Third-party experiments, curriculum resources, media assets, and examination materials have independent license and distribution terms. Review each before inclusion. Do not incorporate private Enterprise code or bypass its license restrictions.

Studytomy branding, content, and development responsibilities are subject to the client agreement. This README does not convey third-party trademark, educational content, or exclusive intellectual-property rights.

## References

- [Studytomy Virtual Science Lab — Agentuto Integration Proposal](VIRTUAL_SCIENCE_LAB_PROPOSAL.md)
- [Studytomy Portal Navigation & Initial Configuration Guide](PORTAL_GUIDE.md)
- [Studytomy AI Agent Engineering Instructions](AGENTS.md)
- [Self-hosting documentation](docs/content/self-hosting/index.mdx)
- [Enterprise feature boundaries](docs/content/enterprise/index.mdx)
- [AI configuration](docs/content/self-hosting/configuration/ai-setup.mdx)
- [Previous Studytomy website repository](https://github.com/nirzaf/studytomy-react) (access controlled)

**Current delivery state.** GitHub Actions checks builds and repository security. Run the application and its tests locally. Any application release requires separate approval and verified business, educational, privacy, security, and operational gates.
