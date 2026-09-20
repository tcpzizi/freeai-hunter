import time, hashlib, feedparser, httpx
import concurrent.futures as cf

UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}

def one(url):
    items = []
    try:
        r = httpx.get(url, headers=UA, timeout=25, follow_redirects=True)
        d = feedparser.parse(r.content)
    except Exception as e:
        print("rss failed " + url + ": " + str(e)[:80])
        return items
    host = url.split("/")[2] if "//" in url else url
    for e in d.entries[:30]:
        link = getattr(e, "link", None)
        if not link:
            continue
        ts = None
        for k in ("published_parsed", "updated_parsed"):
            if getattr(e, k, None):
                ts = int(time.mktime(getattr(e, k)))
                break
        body = getattr(e, "summary", "") or ""
        if getattr(e, "content", None):
            try:
                body = e.content[0].value
            except Exception:
                pass
        items.append({"id": "rss:" + hashlib.md5(link.encode()).hexdigest()[:16],
            "url": link, "canon_url": link,
            "title": (getattr(e, "title", "") or "")[:300], "body": body[:4000],
            "source": "rss/" + host, "source_type": "feed",
            "published_at": ts or int(time.time())})
    return items

def many(urls):
    out = []
    with cf.ThreadPoolExecutor(10) as ex:
        for f in cf.as_completed([ex.submit(one, u) for u in urls]):
            try:
                out += f.result()
            except Exception:
                pass
    print("rss: " + str(len(out)) + " items from " + str(len(urls)) + " feeds")
    return out
