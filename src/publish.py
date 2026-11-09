import base64, json, os, time, pathlib

import httpx

# Everything except `status`; status is not published (the Worker treats a
# missing status as "active"), which is also why it is dropped here.
PUBLIC_FIELDS = ["id", "vendor", "domain", "offer_type", "target", "value", "limits",
                 "requires_cc", "requires_edu", "region", "signup_url", "source_url",
                 "expires_at", "model_class", "why", "score", "verify_status",
                 "corroborations", "created_at", "updated_at"]

FIELDS = ["id", "vendor", "domain", "offer_type", "target", "value", "limits",
          "requires_cc", "requires_edu", "region", "signup_url", "source_url",
          "expires_at", "model_class", "why", "score", "verify_status",
          "corroborations", "created_at", "updated_at", "status"]


# The generated data lives in a private repo, so the site and the Worker cannot
# read it. If a token with access to the public repo is available, the same
# JSON is pushed there over the contents API - that works no matter how the
# public repo was checked out (a plain `actions/checkout` gives read-only
# contents, which is why the earlier attempt to commit docs/ from a workflow
# could never work).
MIRROR_REPO = os.getenv("PUBLIC_MIRROR_REPO", "tcpzizi/freeai-hunter")
MIRROR_TOKEN = os.getenv("PUBLIC_MIRROR_TOKEN", "")
MIRROR_BRANCH = os.getenv("PUBLIC_MIRROR_BRANCH", "main")
MIRROR_FILES = (
    ("docs/offers.json", "bare"),
    ("docs/github-pages/offers.json", "wrapped"),
    ("docs/github-pages/sites.json", "wrapped"),
    ("docs/sites.json", "bare"),
)


def _mirror_put(path, payload, token, repo):
    """Create or update one file; returns True when GitHub accepted it."""
    url = "https://api.github.com/repos/" + repo + "/contents/" + path
    headers = {"authorization": "Bearer " + token, "accept": "application/vnd.github+json",
               "user-agent": "freeai-hunter-publish/1.0"}
    body = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    sha = None
    try:
        r = httpx.get(url, headers=headers, params={"ref": MIRROR_BRANCH}, timeout=20)
        if r.status_code == 200:
            sha = (r.json() or {}).get("sha")
    except Exception:
        pass                    # a missing file is fine: create it below
    data = {"message": "publish: " + path + " (" + str(len(payload)) + " records)",
            "content": base64.b64encode(body).decode("ascii"), "branch": MIRROR_BRANCH}
    if sha:
        data["sha"] = sha
    try:
        r = httpx.put(url, headers=headers, json=data, timeout=25)
    except Exception as e:
        print("mirror: " + path + " failed: " + str(e))
        return False
    if r.status_code in (200, 201):
        return True
    print("mirror: " + path + " -> " + str(r.status_code) + " " + str(r.text)[:200])
    return False


def mirror_public(data):
    """Copy the public JSON into the public repo. Returns the paths written."""
    if not MIRROR_TOKEN:
        print("mirror: no PUBLIC_MIRROR_TOKEN (the Worker can still read data/ from DATA_REPO)")
        return []
    written = []
    for path, shape in MIRROR_FILES:
        payload = data if shape == "bare" else {"offers": data, "updated_at": int(time.time())}
        if _mirror_put(path, payload, MIRROR_TOKEN, MIRROR_REPO):
            written.append(path)
    return written


def snapshot(c):
    """Copy what the site and the Worker read into docs/.

    The generated data lives in the private data repo, which the Cloudflare
    Worker and a GitHub Pages site cannot read without a token. docs/ is in the
    public repo, so the same files are written there too, in both shapes:

      docs/offers.json           bare list (what a static page fetches)
      docs/github-pages/offers.json   {"offers": [...]} (what pages/offers.html reads)
      docs/github-pages/sites.json    {"offers": [...]} (the same tools, for the page)
      docs/sites.json            list of tools, for a Worker that prefers one file

    Both copies are trimmed: the public repo is public, so only the fields the
    site renders are included.
    """
    tools = []
    for d in rows(c):
        tools.append({k: d.get(k) for k in PUBLIC_FIELDS})
    out = []
    for target, payload in (
        ("docs/offers.json", tools),
        ("docs/github-pages/offers.json", {"offers": tools}),
        ("docs/github-pages/sites.json", {"offers": tools}),
        ("docs/sites.json", tools),
    ):
        p = pathlib.Path(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    return len(tools)


def rows(c):
    out = []
    q = ("SELECT * FROM offers WHERE status='active' AND "
         "verify_status NOT IN ('dead_link','expired','suspicious','not_offer') "
         "ORDER BY score DESC, updated_at DESC LIMIT 400")
    for r in c.execute(q):
        d = {k: r[k] for k in FIELDS}
        try:
            d["verify"] = json.loads(r["verify"] or "{}")
        except Exception:
            d["verify"] = {}
        out.append(d)
    return out


def build(c):
    data = rows(c)
    pathlib.Path("data").mkdir(exist_ok=True)
    with open("data/offers.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    stats = {"total": len(data), "generated_at": int(time.time()),
             "verified": sum(1 for d in data if d["verify_status"] == "verified"),
             "likely": sum(1 for d in data if d["verify_status"] == "likely")}
    with open("data/stats.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    pathlib.Path("docs").mkdir(exist_ok=True)
    lines = ["# Free AI Offers", "",
             "Updated: " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()) +
             " | total: " + str(len(data)), "",
             "| Score | Vendor | Type | Value | Verify | Link |",
             "|---|---|---|---|---|---|"]
    for d in data[:120]:
        cells = [str(d["score"]), str(d["vendor"] or "")[:28],
                 str(d["offer_type"] or ""), str(d["value"] or "")[:40],
                 str(d["verify_status"] or ""),
                 "[open](" + str(d["signup_url"] or "") + ")"]
        lines.append("| " + " | ".join(x.replace("|", "/") for x in cells) + " |")
    with open("docs/index.md", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    mirrored = mirror_public(data)
    copied = snapshot(c)
    print("published: " + str(len(data)) + " offers, " + str(copied) + " in docs/, "
          + str(len(mirrored)) + " mirrored to the public repo")

    # The public mirror is a hard requirement, not a nicety: the site and the
    # Cloudflare Worker have nothing else to read (the data repo is private).
    # When GitHub refuses, this raises, hunt goes red, and src/main.py keeps the
    # previous docs/ files instead of publishing an empty list.
    # A configured mirror that silently does nothing is the failure that made
    # the Worker blind in the first place, so the workflows set
    # PUBLIC_MIRROR_REQUIRED=1 and a rejected push turns the run red. Local runs
    # (and the test suite) stay quiet.
    if not mirrored and os.getenv("PUBLIC_MIRROR_REQUIRED") == "1":
        raise RuntimeError("public mirror failed: nothing could be written to " + MIRROR_REPO
                           + (" (PUBLIC_MIRROR_TOKEN is not set)" if not MIRROR_TOKEN else ""))
