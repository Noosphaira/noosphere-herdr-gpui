---
name: git-workflow
description: Branch, commit, user review, pull request and merge procedure for this repository. Use when starting new work, creating a branch, committing, presenting work for user review, opening a pull request to develop or master, or merging.
---

# Git workflow

Rules referenced here are in AGENTS.md: Branches, Commits, Work cycle, Verification, Change size.

## 1. Start work

1. Run `git status`. If the working tree has uncommitted changes, stop and ask the user what to do with them.
2. `git fetch origin`
3. `git switch -c <category>/<description> origin/develop`

## 2. Implement and verify

1. Implement the change. Commit in logical steps, in the commit format from AGENTS.md > Commits.
2. Run the verification from AGENTS.md > Verification.
3. Count changed lines, excluding lockfiles:

   ```bash
   git diff --numstat origin/develop...HEAD \
     | grep -Ev '[[:space:]/](package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|poetry\.lock|uv\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock)$' \
     | awk '{ total += $1 + $2 } END { print total + 0 }'
   ```

   Over 1,000: stop and ask the user before continuing.

## 3. Check readiness

The work is ready for user review when all of these hold:
- A person can use the change in its interface. Examples: a frontend change shows in the running application; a command runs in the terminal; an API endpoint responds to a request.
- The verification from section 2 passed, or each failure is listed with its cause.
- No decision or blocker is open.

When a decision or blocker is open, present it to the user and wait for the answer:
- Decision: the question, the options, and the facts that differ between the options.
- Blocker: what stopped the work, what was tried, and what is needed from the user.

## 4. Present for user review

1. Commit all changes. `git status` shows a clean working tree.
2. Start the change with the command from AGENTS.md > Project > Run locally. Leave it running.
3. Send the user:
   - Branch name and commits (`git log --oneline origin/develop..HEAD`)
   - How to use the change: URL, command, or steps in the interface
   - User-facing changes
   - Verification run, with results, and what was not verified
   - Changed files, one line each: path and change
4. Wait for the user's answer. For requested changes, return to section 2.

Do not push and do not open a pull request before the user approves.

## 5. Open the pull request to develop

1. `git fetch origin`. If `origin/develop` has new commits, run `git merge origin/develop`, resolve conflicts and repeat the verification. If the merge changes behavior the user approved, return to section 4.
2. `git push -u origin HEAD`
3. Get the pull request template. A repository template (`.github/pull_request_template.md`) takes precedence over the organization template:

   ```bash
   gh api -H "Accept: application/vnd.github.raw" \
     "repos/$(gh repo view --json owner --jq .owner.login)/.github/contents/pull_request_template.md"
   ```

4. Write the pull request body to a file outside the repository (`body=$(mktemp)`). Fill every section of the template. Tick exactly one AI disclosure box.
5. `gh pr create --base develop --head "$(git branch --show-current)" --title "<title>" --body-file "$body"`
   Title: imperative mood, at most 72 characters.
6. When the user approved a change over the size limit:
   `gh label create size-exception --force` and `gh pr edit <number> --add-label size-exception`
7. Run the `pr-review-loop` skill with the pull request number.

## 6. After the merge into develop

The `pr-review-loop` skill merges the pull request into `develop` and deletes the branch.

1. `git fetch origin`
2. `gh pr list --base master --head develop --state open --json number,url`
3. No open pull request:
   1. List the changes since the last release: `git log --oneline origin/master..origin/develop`
   2. Write the body from the template. The Summary lists the included pull requests (`#<number>: <title>`).
   3. `gh pr create --base master --head develop --title "Release <YYYY-MM-DD>" --body-file "$body"`
4. Open pull request exists: `gh pr comment <number> --body "Includes #<merged number>: <merged title>"`
5. Send the user the pull request URL. The pull request requires the user's approval on GitHub.
6. Merge into `master` only when the user asks, after the approval: `gh pr merge <number> --merge`. Do not pass `--delete-branch`.
