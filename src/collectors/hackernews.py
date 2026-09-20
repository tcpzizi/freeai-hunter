import re, time, httpx

def hn(queries, hours=48):
    since = int(time.time()) - hours * 3600
    out = []
    for q in queries:
        try:
            r = httpx.get("https://hn.algolia.com/api/v1/search_by_date",
                params={"query": q, "tags": "(story,comment)",
                        "numericFilters": "created_at_i>" + str(since),
                        "hitsPerPage": 40}, timeout=25)
            hits = r.json().get("hits", [])
        except Exception:
            continue
        for h in hits:
            hn_url = "https://news.ycombinator.com/item?id=" + str(h["objectID"])
            url = h.get("url") or hn_url
            title = h.get("title") or ((h.get("story_title") or "") + " (comment)")
            body = re.sub(r"<[^>]+>", " ",
                          h.get("comment_text") or h.get("story_text") or "")
            out.append({"id": "hn:" + str(h["objectID"]), "url": url,
                "canon_url": url, "title": title[:300], "body": body[:4000],
                "source": "hackernews", "source_type": "forum",
                "published_at": h.get("created_at_i") or int(time.time()),
                "source_url": hn_url})
    print("hackernews: " + str(len(out)) + " items")
    return out
