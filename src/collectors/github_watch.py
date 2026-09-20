import os, time, httpx
from ..store import snapshot

def _h():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "freeai-hunter/1.0"}
    t = os.getenv("GITHUB_TOKEN")
    if t:
        h["Authorization"] = "Bearer " + t
    return h

def run(c, cfg):
    out = []
    for repo in cfg.get("repos", []):
        try:
            r = httpx.get("https://api.github.com/repos/" + repo + "/commits",
                          headers=_h(), params={"per_page": 5}, timeout=25)
            if r.status_code != 200:
                continue
            commits = r.json()
        except Exception:
            continue
        if not commits:
            continue
        changed, old = snapshot(c, "gh:" + repo, commits[0]["sha"])
        if not (changed and old):
            continue
        for cm in commits[:3]:
            if cm["sha"] == old:
                break
            msg = (cm["commit"]["message"] or "").split("\n")[0]
            u = cm["html_url"]
            out.append({"id": "gh:" + cm["sha"][:12], "url": u, "canon_url": u,
                "title": "[" + repo + "] " + msg[:120],
                "body": "Curated free-AI list updated. Commit: " + msg[:500],
                "source": "github/" + repo, "source_type": "curated_repo",
                "published_at": int(time.time()), "prefilter": 3.5})
    week = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    for q in cfg.get("search_queries", []):
        try:
            r = httpx.get("https://api.github.com/search/repositories", headers=_h(),
                          params={"q": q + " pushed:>" + week, "sort": "updated",
                                  "per_page": 15}, timeout=25)
            if r.status_code != 200:
                continue
            for it in r.json().get("items", []):
                desc = it.get("description") or ""
                out.append({"id": "ghs:" + str(it["id"]), "url": it["html_url"],
                    "canon_url": it["html_url"],
                    "title": ("[repo] " + it["full_name"] + ": " + desc)[:200],
                    "body": desc + "\nstars=" + str(it.get("stargazers_count")),
                    "source": "github-search", "source_type": "curated_repo",
                    "published_at": int(time.time()), "prefilter": 2.0})
        except Exception:
            continue
    print("github_watch: " + str(len(out)) + " items")
    return out
