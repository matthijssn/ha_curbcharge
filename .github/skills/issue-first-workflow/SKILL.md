---
name: issue-first-workflow
description: "Use for CurbCharge feature or bug-fix implementation requests when no GitHub issue is supplied. Find or create an issue before editing, branch from latest origin/main, then validate, commit, push, and verify one PR."
argument-hint: "Describe the requested CurbCharge change"
---

# Issue-First Workflow

Use this workflow for implementation requests that will modify repository files when the user has not supplied an issue number or PR. Carry the work from issue creation through a verified pull request. Requests for review, explanation, summary, or planning alone do not authorize issue creation or file changes.

## Find or Create an Issue

1. Confirm the target repository from the workspace and read its contribution instructions.
2. Search the repository's open and closed issues and pull requests for matching work. Read the relevant code to confirm whether the requested behavior is already implemented.
3. Reuse an open issue that matches the request. If no matching open issue exists and the work is not already implemented, create an issue before editing with the repository's appropriate template, a concise problem statement, expected behavior, acceptance criteria, and relevant labels.
4. Verify the issue URL, number, and body through GitHub. Read its full description, comments, labels, and linked dependencies. If GitHub access or permissions prevent issue creation or verification, stop before editing and report the blocker.
5. If the scope is materially ambiguous, ask one focused question. If a prerequisite issue is incomplete, stop and identify it. Do not recursively implement dependencies or bundle unrelated issues into one PR.
6. If acceptance criteria are missing, add a short checklist based only on the requested scope. If an open PR already covers the issue, update that PR rather than creating a duplicate.

## Branch Before Editing

- Inspect the worktree, current branch, remotes, and existing diff. Preserve user changes and do not include unrelated work. If changes cannot be safely separated, stop and report the blocker.
- Fetch the latest `origin/main` and create the issue branch from it before the first file edit. Never develop or commit directly on `main`.
- Use `feature/<issue>-<slug>` for features, `fix/<issue>-<slug>` for bug fixes, `docs/<issue>-<slug>` for documentation, `test/<issue>-<slug>` for tests, and `chore/<issue>-<slug>` for maintenance.
- If issue-related changes are already present on `main`, inspect the diff and carry only those changes onto the new issue branch. Never reset, revert, or force-push to discard work.

## Implement and Validate

- Read the owning implementation, callers, tests, documentation, and repository instructions before editing. Make the smallest complete change and add or update focused tests.
- Run the repository's required tests, lint, formatting, and relevant integration validators. Do not disable checks. If the local environment cannot run one, report that and rely on the corresponding CI check.
- Verify each acceptance criterion. Mark issue checklist items complete only after implementation and validation pass.

## Commit and Open the PR

- Review the issue-scoped diff and run `git diff --check`. Stage only issue-related files and create a scoped commit on the issue branch.
- Push the branch without force-pushing. Open one PR targeting `main`; use `Closes #<number>` only when it fully resolves the issue, otherwise use `Refs #<number>`.
- Follow the repository's PR template. Verify the PR's head, base, title, body, URL, and required check status. Fix failing checks and push corrections. Clearly report any pending check; do not claim readiness until required checks pass.
- Do not merge unless explicitly asked. Do not close the issue before the PR is merged.

This skill guides agent behavior but cannot technically block edits on `main`. A deterministic branch guard requires a repository hook and a separately configured tool restriction.