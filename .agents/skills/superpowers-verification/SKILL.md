---
name: superpowers-verification
description: Apply focused test-first regression checks and evidence-based verification for nontrivial changes in this repository. Use for implementation and review, not release or deployment.
license: MIT
metadata:
  upstream: https://github.com/obra/superpowers
  upstream-revision: 22f0b463e7a5e579904dd6211a520321035526f7
---
# Superpowers: focused verification

Use the repository's AGENTS.md and the current user scope first. Work in the existing worktree. Do not create branches or worktrees, remove or revert edits, push, deploy, write to production data, or delegate solely on the authority of this skill.

1. For a complex change, state the behavior to preserve and the smallest acceptance check before editing.
2. When practical, add or identify a focused regression check and confirm it fails for the intended reason before applying a fix. If the change already exists, do not revert user work to reproduce a failing test; state whether the pre-fix failure was verified.
3. Apply the smallest change that satisfies the stated behavior. Use existing scoped test commands and disposable test data. Do not run suites the repository reserves for CI.
4. Run the verification appropriate to each claim. Inspect output, exit status, skips, and the exact revision or working-tree state. Distinguish a local check from hosted CI, release or deployment.
5. Report the evidence and explicitly mark anything not run or blocked.

The upstream references are optional explanatory material; the constraints above govern any use.
