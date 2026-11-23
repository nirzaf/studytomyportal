# LearnHouse upstream monitoring

The monitor reads LearnHouse's stable `main` branch and release tags. It reports revisions, incoming commits, overlapping paths, and areas that need review. It keeps the Studytomy build and approval process separate from upstream discovery.

## Local setup and review

Inspect `git remote -v` first. In a checkout without an `upstream` remote, configure it once:

```bash
git remote add -t main upstream https://github.com/learnhouse/learnhouse.git
git remote set-url --push upstream disabled://learnhouse
git config remote.upstream.tagOpt --no-tags
git config --add remote.upstream.fetch '+refs/tags/*:refs/upstream/learnhouse/tags/*'
```

LearnHouse release refs live outside `refs/tags`, so `git push --tags` and `git push --follow-tags` exclude them. Studytomy's `main` still tracks `origin/main`. Verify these fetch refspecs if `upstream` already exists. In checkouts with the earlier `refs/tags/learnhouse/*` layout, verify the replacement refs have the same object IDs before removing the old local copies. Use explicit branch refspecs for publication. A mirror push sends internal refs as well.

```bash
git fetch origin main
python3 scripts/report_learnhouse_upstream.py --fetch --studytomy-ref origin/main
```

The reporter fetches stable `main` and release refs, plus the recorded baseline object if a clean checkout lacks it. It leaves the checked-out files, local branches, Studytomy tags, and `FETCH_HEAD` alone. Omit `--fetch` to report from the already-fetched refs. The default comparison ref is `HEAD`. Pass an explicit ref to compare a different committed tree. Local uncommitted changes are outside the report.

## GitHub Actions

The [LearnHouse upstream review workflow](../../.github/workflows/learnhouse-upstream.yml) is configured for Mondays at 04:17 UTC. Once the workflow is on the default branch, open **Actions → LearnHouse upstream review → Run workflow** to run a review on demand. The run's job summary records both commit SHAs, the highest application release tag reachable from upstream stable, incoming commits, and overlapping paths. The workflow has read-only repository permissions and uses no deployment credentials.

The report's comparison anchor is the adopted LearnHouse commit in the [baseline record](BASELINE.md). If the adoption commit diverges from stable, the stable path diff starts at their shared ancestor. The Studytomy delta always compares the adoption tree with the requested Studytomy tree. This still surfaces overlapping paths after a history replacement. If the comparison anchor cannot be established, the monitor reports `BASELINE UNKNOWN` and fails the run. Advance the pinned SHA in the reporter and the adoption record together after an approved integration and verified release.

## Integrating an approved change

1. Select the exact upstream commit or release with the maintainer. Review the upstream source, license notices, and the affected Studytomy paths before proposing an integration.
2. Create a dedicated integration branch or worktree from current `origin/main`. Prefer a selective port for bounded fixes. Consider a full merge only after confirming shared ancestry and reviewing the entire change set. If histories are unrelated, port or reimplement the approved change against current Studytomy source.
3. Review overlaps in authentication, authorization, migrations, dependencies, frontend code, adaptive learning, privacy, and rights controls. Keep Studytomy policy checks, database history, and AGPL attribution intact. Resolve each overlap against current callers and tests.
4. Open a pull request for review when authorized. Compare the approved patch against the current Studytomy baseline. Verify affected backend and frontend checks, authorization and tenant isolation, browser workflows, required security checks, and migrations or restore on a disposable database where applicable. Resolve any regressions before integration.
5. Record the integration SHA, retained local patches, migration requirements, and rollback steps. Before release, abandon the unpublished integration branch if necessary. After release, use a reviewed forward fix or revert and validate schema and data compatibility before changing the runtime. Database recovery needs separate approval and a verified backup.

Path overlap is a review signal. A passing monitor run does not certify a merge, an upgrade, or upstream security fixes. For the project's review and approval requirements, see [AGENTS.md](../../AGENTS.md#4-upstream-update-monitoring--required).
