# shellcheck shell=bash
# Shared definitions for the hooks in .githooks.
# Maintained in the rules repository and synced. Project hooks go in .githooks/local/.

# Variables below are read by the hooks that source this file.
# shellcheck disable=SC2034
PROTECTED_BRANCHES="develop master"
BRANCH_CATEGORIES="feature|bugfix|documentation|refactor|test|chore"
BRANCH_PATTERN="^(${BRANCH_CATEGORIES})/[a-z0-9]+(-[a-z0-9]+)*$"
ASSISTED_BY_PATTERN='^Assisted-by: [A-Za-z0-9._-]+:[A-Za-z0-9._-]+( \[[A-Za-z0-9._ -]+\])*$'
AI_IDENTITY_PATTERN='(claude|anthropic|copilot|openai|codex|chatgpt|gemini|cursor|devin)'
# Largest file a commit may add or change, in bytes (5 MiB).
MAX_FILE_BYTES=5242880

# Prints each argument on its own line to stderr and exits with status 1.
hook_fail() {
  printf '%s\n' "$@" >&2
  exit 1
}

# Returns 0 when $1 is a protected branch name.
is_protected_branch() {
  case " $PROTECTED_BRANCHES " in
    *" $1 "*) return 0 ;;
  esac
  return 1
}

# Prints the branch naming rule to stderr.
print_branch_rule() {
  printf '%s\n' \
    "Branch name format: <category>/<description>" \
    "Categories: ${BRANCH_CATEGORIES//|/, }" \
    "Description: lowercase letters and digits, words separated by hyphens. Example: feature/csv-export" >&2
}

# Runs .githooks/local/<hook> with the given arguments when the file exists and is executable.
run_local_hook() {
  local hook="$1"
  shift
  local path
  path="$(git rev-parse --show-toplevel)/.githooks/local/$hook"
  if [ -x "$path" ]; then
    "$path" "$@" || exit $?
  fi
}
