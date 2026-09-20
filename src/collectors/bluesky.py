import time, httpx

UA = {"User-Agent": "freeai-hunter/1.0"}
EP = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"

def search(queries, limit=25):
    out = []
    for q in queries:
        try:
            r = httpx.get(EP, headers=UA, timeout=25,
                          params={"q": q, "limit": limit, "sort": "latest"})
            if r.status_code != 200:
                continue
            posts = r.json().get("posts", [])
        except Exception:
            continue
        for p in posts:
            rec = p.get("record") or {}
            txt = rec.get("text") or ""
            if len(txt) < 15:
                continue
            handle = (p.get("author") or {}).get("handle", "")
            rkey = (p.get("uri") or "").split("/")[-1]
            u = "https://bsky.app/profile/" + handle + "/post/" + rkey
            out.append({"id": "bsky:" + rkey, "url": u, "canon_url": u,
                "title": txt[:140], "body": txt[:2000], "source": "bluesky",
                "source_type": "social", "published_at": int(time.time())})
    print("bluesky: " + str(len(out)) + " items")
    return out
