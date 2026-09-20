import time, urllib.parse, httpx

UA = {"User-Agent": "freeai-hunter/1.0 (by github freeai-hunter)"}

def _get(cli, url):
    for _ in range(2):
        try:
            r = cli.get(url)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 403):
                time.sleep(5)
        except Exception:
            time.sleep(2)
    return {}

def reddit(subs, queries, limit=40):
    items = []
    cli = httpx.Client(headers=UA, timeout=25, follow_redirects=True)
    urls = ["https://www.reddit.com/r/" + s + "/new.json?limit=" + str(limit)
            for s in subs]
    urls += ["https://www.reddit.com/search.json?q=" + urllib.parse.quote(q) +
             "&sort=new&t=week&limit=" + str(limit) for q in queries]
    for u in urls:
        data = _get(cli, u)
        for ch in (data.get("data") or {}).get("children", []):
            d = ch.get("data") or {}
            if not d.get("id"):
                continue
            perma = "https://reddit.com" + (d.get("permalink") or "")
            link = d.get("url_overridden_by_dest") or perma
            meta = ("\n[r/" + str(d.get("subreddit")) + " score=" +
                    str(d.get("score")) + " comments=" + str(d.get("num_comments")) + "]")
            items.append({"id": "rd:" + d["id"], "url": link, "canon_url": link,
                "title": (d.get("title") or "")[:300],
                "body": ((d.get("selftext") or "") + meta)[:4000],
                "source": "reddit/" + str(d.get("subreddit")), "source_type": "forum",
                "published_at": int(d.get("created_utc") or time.time()),
                "source_url": perma})
        time.sleep(2)
    cli.close()
    print("reddit: " + str(len(items)) + " items")
    return items
