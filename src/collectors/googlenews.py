import time, hashlib, urllib.parse, feedparser, httpx

UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}

def google_news(queries, hl="en-US", gl="US"):
    out = []
    for q in queries:
        u = ("https://news.google.com/rss/search?q=" +
             urllib.parse.quote(q + " when:2d") +
             "&hl=" + hl + "&gl=" + gl + "&ceid=" + gl + ":en")
        try:
            d = feedparser.parse(httpx.get(u, headers=UA, timeout=25).content)
        except Exception:
            continue
        for e in d.entries[:25]:
            link = getattr(e, "link", "")
            if not link:
                continue
            ts = int(time.mktime(e.published_parsed)) \
                 if getattr(e, "published_parsed", None) else int(time.time())
            out.append({"id": "gn:" + hashlib.md5(link.encode()).hexdigest()[:16],
                "url": link, "canon_url": link,
                "title": (getattr(e, "title", "") or "")[:300],
                "body": (getattr(e, "summary", "") or "")[:2000],
                "source": "googlenews", "source_type": "news", "published_at": ts})
        time.sleep(0.5)
    print("googlenews: " + str(len(out)) + " items")
    return out
