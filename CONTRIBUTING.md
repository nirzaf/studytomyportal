# Contributing to Studytomy Portal

This repository is public. Follow [AGENTS.md](AGENTS.md) and [LOCAL-ONLY.md](LOCAL-ONLY.md) for the project conventions and local setup.

## Getting Started

To use the local development CLI from this checkout, build it locally:

```bash
git clone https://github.com/nirzaf/studytomyportal.git
cd studytomyportal
bun --cwd apps/cli install --frozen-lockfile
bun --cwd apps/cli run build
node apps/cli/dist/bin/learnhouse.js dev
```

This starts the local development services using this checkout. The published `npx learnhouse` package and its default application image are upstream artifacts and do not contain changes from this repository. See the [CLI documentation](apps/cli/README.md) for details.

## Dependencies and lockfiles

Local and Docker image installs use frozen lockfiles, so a manifest change
that skips the lockfile fails with `lockfile had changes, but lockfile is
frozen`. The repository security workflow does not replace application test/build verification. After touching a
`package.json`, `apps/api/pyproject.toml` or a version number, run:

```bash
scripts/lockfiles.sh          # regenerate every lockfile
scripts/lockfiles.sh --check  # verify locally without rewriting lockfiles
```

## Submitting Contributions

### Security, privacy, and licensing

- Never commit API keys, passwords, private certificates, local `.env` files, database dumps, real learner records, or private educational documents.
- Use synthetic fixtures and keep local secrets in ignored environment files. Check `git status --ignored` before staging configuration files.
- Report vulnerabilities through GitHub's private reporting form. Keep vulnerability details, credentials, and scanner reports out of public issues and pull requests. See [SECURITY.md](SECURITY.md).
- Include source and license information for third-party code, fonts, icons, audio, images, and educational material. Do not contribute proprietary Enterprise code or content without redistribution rights.
- Run the relevant component tests before opening a pull request. GitHub Actions runs the required secret and dependency scans for `main`. Contact a maintainer privately if a scan reports a possible secret or a disputed finding.

This project follows [GitHub's standard forking model](https://guides.github.com/activities/forking/). Please fork the project to submit pull requests.

### Submitting a bug/fix

- Start an issue [here](https://github.com/nirzaf/studytomyportal/issues/new) to report the bug.
- Please include a detailed description of the bug and how it can be reproduced.
- Someone from the team will review the issue and will give you a go ahead.

### Submitting a feature / idea

- Start a discussion or issue in the [Studytomy Portal repository](https://github.com/nirzaf/studytomyportal/issues) to propose your idea and how it should be implemented.
- Someone from the team will review your idea and will give you a go ahead.
- Start an issue & link the discussion to it.
- Clone your fork locally
- Create a new branch and make your commits
- Push your commits to your forked repo
- Make a Pull request
- Code will be added after review
