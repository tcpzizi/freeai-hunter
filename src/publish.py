import json, time, pathlib

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
    copied = snapshot(c)
    print("published: " + str(len(data)) + " offers, " + str(copied) + " in docs/")
