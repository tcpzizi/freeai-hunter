import time, hashlib, httpx
from selectolax.parser import HTMLParser

UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}

def telegram_channels(names):
    out = []
    for ch in names:
        try:
            r = httpx.get("https://t.me/s/" + ch, headers=UA, timeout=20,
                          follow_redirects=True)
            if r.status_code != 200:
                continue
            tree = HTMLParser(r.text)
        except Exception:
            continue
        for m in tree.css("div.tgme_widget_message")[-25:]:
            node = m.css_first("div.tgme_widget_message_text")
            txt = node.text(separator=" ") if node else ""
            if len(txt) < 20:
                continue
            pid = m.attributes.get("data-post") or \
                  (ch + "/" + hashlib.md5(txt.encode()).hexdigest()[:10])
            u = "https://t.me/" + pid
            out.append({"id": "tg:" + pid, "url": u, "canon_url": u,
                "title": txt[:140], "body": txt[:4000],
                "source": "telegram/" + ch, "source_type": "channel",
                "published_at": int(time.time())})
    print("telegram: " + str(len(out)) + " items")
    return out
