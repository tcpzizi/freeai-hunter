import re, time, difflib, httpx
import concurrent.futures as cf
from selectolax.parser import HTMLParser
from ..store import snapshot

FREE_RE = re.compile(r"(free|no credit card|trial|credits?|students?|startup|"
                     r"\$\d+|rate limit|quota|per (day|minute|month))", re.I)
UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}

def relevant_lines(html):
    tree = HTMLParser(html)
    for t in tree.css("script,style,noscript,svg,footer,nav"):
        t.decompose()
    body = tree.body
    if body is None:
        return []
    text = re.sub(r"[ \t]+", " ", body.text(separator="\n"))
    lines = [l.strip() for l in text.split("\n") if 3 < len(l.strip()) < 220]
    return [l for l in lines if FREE_RE.search(l)][:400]

def fetch(url):
    try:
        r = httpx.get(url, headers=UA, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            return url, None
        if "text/html" not in r.headers.get("content-type", ""):
            return url, None
        return url, relevant_lines(r.text)
    except Exception:
        return url, None

def watch(c, domains, paths, max_pages=260):
    urls = [("https://" + d.strip("/") + p) for d in domains for p in paths][:max_pages]
    results = []
    with cf.ThreadPoolExecutor(12) as ex:
        for f in cf.as_completed([ex.submit(fetch, u) for u in urls]):
            results.append(f.result())
    out, ok = [], 0
    for url, lines in results:
        if not lines:
            continue
        ok += 1
        changed, old = snapshot(c, "page:" + url, "\n".join(lines))
        if not (changed and old):
            continue
        added = [l[1:].strip() for l in difflib.unified_diff(old.split("\n"), lines, n=0)
                 if l.startswith("+") and not l.startswith("+++")][:12]
        if not added:
            continue
        host = url.split("/")[2]
        out.append({"id": "pd:" + url + ":" + str(int(time.time()) // 3600),
            "url": url, "canon_url": url,
            "title": "[PRICING CHANGE] " + host + " - " + added[0][:110],
            "body": "New free/credit related lines on this page:\n" + "\n".join(added),
            "source": "pagediff:" + host, "source_type": "pagediff",
            "published_at": int(time.time()), "prefilter": 5.0, "hard_signal": True})
    print("pagediff: " + str(ok) + " pages ok, " + str(len(out)) + " changes")
    return out
