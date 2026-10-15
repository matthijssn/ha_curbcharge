---
name: issue-to-pr
description: "Use when implementing a GitHub issue end-to-end and opening a pull request: validate scope, work on a dedicated issue branch (never the default branch), implement and test, update issue checklists, commit, push, and verify the PR."
argument-hint: "GitHub issue number or URL"
---

# Issue to PR

Use this workflow when asked to implement a GitHub issue and open a pull request. A request to summarize or review an issue alone is not permission to edit or submit changes.

## Validate the issue

1. Identify the repository from the workspace and confirm the issue belongs to it.
2. Use `gh issue view` to read the issue, state, labels, comments, acceptance criteria, and implementation tasks. Check for an existing PR covering the work.
3. Read the relevant implementation, callers, tests, and documentation. Confirm the issue is actionable and identify the smallest complete change.
4. If the issue has no acceptance criteria or implementation tasks, add concise `## Acceptance criteria` and `## Implementation tasks` checklists based only on its stated scope.
5. If the issue is closed, already implemented, or materially ambiguous, stop before making changes and report the specific conflict or ask one focused question.

## Create an issue branch before editing

1. Inspect the worktree, current branch, repository default branch, and remotes.
2. Work on a dedicated branch named for the issue, such as `fix/issue-123-short-description`, `feat/issue-123-short-description`, or `docs/issue-123-short-description`.
3. Never implement or commit issue changes on `main` or another default branch. If already on the default branch, create the issue branch before editing and preserve any existing work.
4. Do not discard, overwrite, or include unrelated user changes. Stage only files needed for this issue. If existing changes or commits cannot be safely separated, stop and explain the blocker.

## Implement and validate

1. Follow repository patterns and make the smallest change that fully satisfies the issue.
2. Run the smallest relevant tests, lint, or build checks. Verify the actual acceptance criteria, not just a proxy. Fix failures and rerun the affected checks; report checks that were not run.
3. Mark issue checklist items `[x]` only after the corresponding implementation or validation is complete. Leave incomplete items unchecked.

## Commit and open the PR

1. Review the scoped diff and run `git diff --check`. Commit only issue-related changes on the dedicated branch.
2. Push the branch without force-pushing and open a non-draft PR against the repository default branch, unless the user or repository specifies another target.
3. Use an imperative PR title and a concise body describing the change and validation. Include `Closes #<issue-number>` so the issue closes when the PR is merged.
4. Verify the PR's head branch, base branch, title, body, and URL with `gh pr view`. Leave the issue open until merge; do not claim a PR exists unless GitHub confirms it.
5. If authentication, permissions, or a remote blocks pushing or PR creation, preserve the branch and report the exact blocker and remaining step.
