---
name: pr-review-loop
description: Review cycle for an open pull request to develop. Waits for automated reviews, assesses each review comment, fixes or declines it, replies, resolves the thread, repeats until no thread is open, then squash-merges. Use after opening a pull request to develop, or when asked to address review comments on a pull request.
---

# Pull request review loop

Input: the pull request number. Shell variables used below:

```bash
N=<pull request number>
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
OWNER=${REPO%/*}
NAME=${REPO#*/}
```

Run rounds until the merge conditions hold. Rounds are numbered from 1.

## Round

### 1. Wait

Wait 60 minutes after the pull request was opened or after the last push.
- Claude Code: create a one-time scheduled task due in 60 minutes with the prompt `Run the pr-review-loop skill for pull request #<N>, round <r>`, then end the turn. The task fires only while the session is open.
- Other agents: use the agent's scheduled or delayed task. Without one, tell the user the time the round is due and stop.

### 2. Collect

1. Pending reviews: `gh pr view $N --json reviewRequests` and `gh pr checks $N`. If an automated reviewer is still requested or a review check is still running, wait 15 minutes and repeat this step.
2. Failed checks: `gh pr checks $N`. Read the log with `gh run view <run id> --log-failed`. Each failed check is handled as an accepted comment.
3. Unresolved review threads:

   ```bash
   gh api graphql -f query='
   query($owner: String!, $name: String!, $n: Int!) {
     repository(owner: $owner, name: $name) {
       pullRequest(number: $n) {
         reviewThreads(first: 100) {
           nodes {
             id isResolved isOutdated path line
             comments(first: 50) { nodes { author { login } body url } }
           }
         }
       }
     }
   }' -f owner="$OWNER" -f name="$NAME" -F n="$N" \
     --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved | not)'
   ```

   Outdated threads (`isOutdated: true`) are included and handled like the others.
4. Review summaries and conversation comments added since the previous round: `gh pr view $N --json reviews,comments`

### 3. Assess

Check each comment's claim against the code, then classify the comment.

| Class | Criteria | Action |
|---|---|---|
| Accept | The comment identifies a defect, a violation of AGENTS.md, or an improvement within the pull request's scope, and the fix keeps the behavior the user approved | Fix it |
| Decline | The comment is incorrect, duplicates another comment, is outside the pull request's scope, or contradicts AGENTS.md | Reply with the reason |
| Decision | The fix changes behavior the user approved, or needs a product or architecture choice | Present it to the user (git-workflow section 3) and wait for the answer |

### 4. Fix

1. Implement the accepted fixes.
2. Run the verification from AGENTS.md > Verification.
3. Commit. Subject: `Address review comments (round <r>)`. Body: one line per fixed comment.
4. `git push`. Do not force-push.

### 5. Reply and resolve

After the push, for each thread:
- Accepted: reply `Fixed in <short SHA>.`, then resolve the thread.
- Declined: reply with the reason in one to three sentences, citing the code or the rule, then resolve the thread.

Reply:

```bash
gh api graphql -f query='
mutation($thread: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) { comment { url } }
}' -f thread="<thread id>" -f body="<reply>"
```

Resolve:

```bash
gh api graphql -f query='
mutation($thread: ID!) {
  resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } }
}' -f thread="<thread id>"
```

Review summaries and conversation comments have no thread to resolve. Answer them in one pull request comment that quotes each comment and gives the reply: `gh pr comment $N --body-file <file>`

### 6. Next step

- Commits were pushed in this round: start the next round.
- No commits were pushed: check the merge conditions.

## Merge

Merge conditions:
- Every required check in `gh pr checks $N` passes.
- The thread query from round step 2.3 returns no thread.
- No decision is waiting for the user.

Steps:
1. Write the squash commit body to a file outside the repository: a summary of the change, a blank line, and the `Assisted-by:` trailer.
2. `gh pr merge $N --squash --subject "<pull request title> (#$N)" --body-file <file> --delete-branch`
3. Continue with git-workflow section 6.

## Round limit

After round 5, if new comments still arrive, stop and present a blocker to the user: the number of rounds, the open threads, and the comments that recur.
