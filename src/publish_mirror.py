"""Publish the public JSON copies to the public repo, after the data commit.

The generated data lives in a private repo. The site and the Cloudflare Worker
can only read public files, so the same JSON is pushed to
`docs/{offers,sites}.json` (and the two `docs/github-pages/` shapes the site
accepts) in the public repo.

Why an API push and not a commit from the workflow: `actions/checkout` on this
repo gives the job a read-only `contents` token, so committing `docs/` from
`hunt`/`recheck` could never work - it never pushed anything.

Run from the repo root, after the data is committed:

    PUBLIC_MIRROR_TOKEN=<token with contents:write on the public repo> \\
    python -m src.publish_mirror

Exit codes: 0 = published, 1 = nothing written (the workflow step fails so the
public copy cannot silently go stale), 2 = no token (reported, not a failure -
the data repo copy still exists for a Worker that has a token).
"""
import json
import os
import sqlite3
import sys

from . import publish

DB = os.getenv("DB_PATH", "data/state.sqlite")


def load_rows():
    if not os.path.exists(DB):
        return []
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        return publish.rows(c)
    finally:
        c.close()


def public_rows(rows):
    return [{k: r.get(k) for k in publish.PUBLIC_FIELDS} for r in rows]


def main():
    rows = public_rows(load_rows())
    if not publish.MIRROR_TOKEN:
        print("mirror: PUBLIC_MIRROR_TOKEN is not set; nothing pushed "
              "(the Worker can still read offers.json out of DATA_REPO with GH_TOKEN)")
        return 2
    written = publish.mirror_public(rows)
    print("mirror: " + str(len(written)) + " file(s) written to "
          + publish.MIRROR_REPO + "@" + publish.MIRROR_BRANCH
          + (" -> " + ", ".join(written) if written else ""))
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
