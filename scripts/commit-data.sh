#!/usr/bin/env bash
# Commit and push a data repo without losing the run's work.
#
#   scripts/commit-data.sh <dir> "<commit message>" <path> [<path> ...]
#
# Two workflows (or two cron runs of the same workflow) can finish at the same
# moment, so a bare `git pull --rebase && git push` is not enough:
#
#   * a rebase that hits a conflict leaves the checkout mid-rebase. The old
#     retry loop then failed on *every* following attempt and still exited 0,
#     so the run's data was thrown away silently.
#   * data/state.sqlite is binary: git can never merge it. The rebase has to be
#     abandoned and the two sides joined with a plain merge that keeps our copy
#     of the conflicting files.
#
# The script therefore retries with backoff and fails loudly (exit 1) if the
# push never lands, so a lost write shows up as a red workflow instead of a
# quiet gap in the data.
set -uo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <dir> <commit message> <path> [<path> ...]" >&2
  exit 2
fi

dir=$1
msg=$2
shift 2
paths=("$@")

cd "$dir" || { echo "::error::no such directory: $dir"; exit 1; }
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "::error::$dir is not a git checkout (did the checkout step fail?)"
  exit 1
fi

git config user.name "${GIT_AUTHOR_NAME:-freeai-hunter}"
git config user.email "${GIT_AUTHOR_EMAIL:-bot@users.noreply.github.com}"

# Only paths that exist: `git add` fails on a missing pathspec.
existing=()
for p in "${paths[@]}"; do
  [ -e "$p" ] && existing+=("$p")
done
if [ "${#existing[@]}" -eq 0 ]; then
  echo "nothing to commit: none of ${paths[*]} exist"
  exit 0
fi

git add -A -- "${existing[@]}"
if git diff --quiet --cached; then
  echo "nothing to commit: ${existing[*]} unchanged"
  exit 0
fi
git commit -q -m "$msg" || { echo "::error::commit failed"; exit 1; }
commit=$(git rev-parse --short HEAD)

branch=$(git rev-parse --abbrev-ref HEAD)
attempts=${PUSH_ATTEMPTS:-5}

for i in $(seq 1 "$attempts"); do
  # Fast path: replay our commit on top of whatever landed meanwhile.
  if git pull --rebase --autostash -q >/dev/null 2>&1; then
    if git push -q >/dev/null 2>&1; then
      echo "pushed $commit to $dir ($branch) on attempt $i"
      exit 0
    fi
  else
    echo "::warning::rebase onto origin/$branch failed on attempt $i; merging instead"
    git rebase --abort >/dev/null 2>&1 || true
    # -X ours keeps this run's copy of files that both sides touched. That is
    # the only possible answer for the binary sqlite file, and it is safe for
    # the JSON state files because the concurrency groups serialise writers.
    if git pull --no-rebase --no-edit -X ours -q >/dev/null 2>&1; then
      if git push -q >/dev/null 2>&1; then
        echo "::warning::$dir: pushed $commit with a merge (kept local copy of conflicting files)"
        exit 0
      fi
    fi
    git merge --abort >/dev/null 2>&1 || true
  fi
  echo "push attempt $i/$attempts failed, retrying"
  sleep $((i * 5))
done

echo "::error::$dir: could not push $commit after $attempts attempts; this run's data is not saved"
exit 1
