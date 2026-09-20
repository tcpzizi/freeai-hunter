import time, httpx, yaml, re
from . import store

UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}
PROBE = ["/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
         "/blog/rss.xml", "/changelog/feed"]

def main():
    c = store.conn()
    cfg = yaml.safe_load(open("config/sources.yaml", encoding="utf-8"))
    known = set(cfg["page_watch"]["domains"])
    doms = [r["domain"] for r in c.execute(
        "SELECT domain, COUNT(*) n FROM offers WHERE status='active' "
        "AND verify_status IN ('verified','likely') GROUP BY domain "
        "ORDER BY n DESC LIMIT 60") if r["domain"]]
    added_feeds, added_domains = 0, 0
    for d in doms:
        if d not in known:
            cfg["page_watch"]["domains"].append(d)
            known.add(d)
            added_domains += 1
        for p in PROBE:
            url = "https://" + d + p
            row = c.execute("SELECT 1 FROM candidate_sources WHERE url=?",
                            (url,)).fetchone()
            if row:
                continue
            try:
                r = httpx.get(url, headers=UA, timeout=12, follow_redirects=True)
            except Exception:
                continue
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and re.search(r"(xml|rss|atom)", ct, re.I):
                c.execute("INSERT OR IGNORE INTO candidate_sources"
                          "(url,kind,found_via,added_at) VALUES(?,?,?,?)",
                          (url, "rss", d, int(time.time())))
                if url not in cfg["rss"]["official_blogs"]:
                    cfg["rss"]["official_blogs"].append(url)
                    added_feeds += 1
                break
    c.commit()
    c.close()
    if added_feeds or added_domains:
        with open("config/sources.yaml", "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, width=100)
    print("expand: +" + str(added_domains) + " domains, +" +
          str(added_feeds) + " feeds")

if __name__ == "__main__":
    main()
