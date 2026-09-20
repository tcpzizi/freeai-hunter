import time, httpx

UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}

def hackathons(url):
    out = []
    try:
        data = httpx.get(url, headers=UA, timeout=25, follow_redirects=True).json()
    except Exception as e:
        print("devpost failed: " + str(e)[:100])
        return out
    for h in (data.get("hackathons") or [])[:40]:
        title = h.get("title") or ""
        themes = " ".join(t.get("name", "") for t in (h.get("themes") or []))
        blob = (title + " " + themes).lower()
        if "ai" not in blob and "machine learning" not in blob:
            continue
        link = h.get("url") or ""
        if not link:
            continue
        out.append({"id": "dp:" + str(h.get("id")), "url": link, "canon_url": link,
            "title": ("[hackathon] " + title)[:200],
            "body": "AI hackathon. Themes: " + themes + ". Prize: " +
                    str(h.get("prize_amount") or "") +
                    ". Sponsors usually give free API credits to participants. "
                    "Dates: " + str(h.get("submission_period_dates")),
            "source": "devpost", "source_type": "hackathon",
            "published_at": int(time.time()), "prefilter": 3.2})
    print("devpost: " + str(len(out)) + " AI hackathons")
    return out
