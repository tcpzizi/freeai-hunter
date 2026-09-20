import time, hashlib, json, yaml
import urllib.parse as up
import concurrent.futures as cf
from . import store, filters, extract, verify, score, publish
from .collectors import (model_registry, pagediff, github_watch, rss,
                         googlenews, reddit as rd, hackernews as hnc,
                         tgchannel, bluesky, devpost)

DROP_Q = ("utm_", "ref", "fbclid", "gclid", "aff", "mc_cid", "mc_eid")

def canon(u):
    try:
        p = up.urlsplit(u)
        q = [(k, v) for k, v in up.parse_qsl(p.query)
             if not k.lower().startswith(DROP_Q)]
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return up.urlunsplit(("https", host, p.path.rstrip("/"), up.urlencode(q), ""))
    except Exception:
        return u

def offer_key(o, dom):
    raw = (dom + "|" + str(o.get("offer_type")) + "|" +
           str(o.get("value")).lower().strip())
    return hashlib.md5(raw.encode("utf-8", "ignore")).hexdigest()[:16]

def collect(cfg, c):
    raw = []
    for fn in (lambda: model_registry.openrouter(c),
               lambda: model_registry.huggingface(c),
               lambda: pagediff.watch(c, cfg["page_watch"]["domains"],
                                      cfg["page_watch"]["paths"],
                                      cfg["page_watch"].get("max_pages_per_run", 260)),
               lambda: github_watch.run(c, cfg["github_watch"])):
        try:
            raw += fn() or []
        except Exception as e:
            print("db-collector failed: " + str(e)[:150])
    feeds = []
    for k, v in cfg["rss"].items():
        if isinstance(v, list) and k != "google_news_queries":
            feeds += v
    tasks = [lambda: rss.many(feeds),
             lambda: googlenews.google_news(cfg["rss"]["google_news_queries"]),
             lambda: rd.reddit(cfg["reddit"]["subreddits"],
                               cfg["reddit"]["search_queries"]),
             lambda: hnc.hn(cfg["hackernews"]["queries"]),
             lambda: tgchannel.telegram_channels(cfg["telegram_channels"]),
             lambda: bluesky.search(cfg["bluesky"]["queries"]),
             lambda: devpost.hackathons(cfg["devpost"])]
    with cf.ThreadPoolExecutor(7) as ex:
        for f in cf.as_completed([ex.submit(t) for t in tasks]):
            try:
                raw += f.result() or []
            except Exception as e:
                print("collector failed: " + str(e)[:150])
    return raw

def run(limit_extract=40):
    cfg = yaml.safe_load(open("config/sources.yaml", encoding="utf-8"))
    vend = set(yaml.safe_load(open("config/vendors.yaml",
                                   encoding="utf-8"))["official_domains"])
    c = store.conn()
    t0 = time.time()
    raw = collect(cfg, c)

    fresh, seen_now = [], set()
    for it in raw:
        try:
            it["canon_url"] = canon(it.get("canon_url") or it["url"])
            it["id"] = it.get("id") or \
                "h:" + hashlib.md5(it["canon_url"].encode()).hexdigest()[:16]
        except Exception:
            continue
        if it["id"] in seen_now or store.seen(c, it["id"]):
            continue
        seen_now.add(it["id"])
        it["prefilter"] = max(float(it.get("prefilter") or 0),
                              filters.prefilter(it.get("title", ""), it.get("body", "")))
        store.put_item(c, it)
        if it["prefilter"] >= filters.THRESHOLD or it.get("hard_signal"):
            fresh.append(it)
    c.commit()
    fresh.sort(key=lambda x: -x["prefilter"])
    fresh = fresh[:limit_extract]
    print("collected=" + str(len(raw)) + " new=" + str(len(seen_now)) +
          " candidates=" + str(len(fresh)))

    made = 0
    for it in fresh:
        try:
            o = extract.extract(it)
        except Exception as e:
            print("extract failed: " + str(e)[:120])
            continue
        if not o or not o.get("is_offer"):
            continue
        o["signup_url"] = o.get("signup_url") or it["url"]
        dom = verify.domain(o["signup_url"])
        if not dom:
            continue
        oid = offer_key(o, dom)
        prev = c.execute("SELECT corroborations,created_at FROM offers WHERE id=?",
                         (oid,)).fetchone()
        corr = (prev["corroborations"] + 1) if prev else 1
        try:
            status, v = verify.verify(o, vend, corr)
        except Exception as e:
            status, v = "unverifiable", {"error": str(e)[:150], "corroborations": corr}
        if o.get("requires_credit_card") is None and v.get("cc_required") is not None:
            o["requires_credit_card"] = v["cc_required"]
        sc = score.score(o, status, it.get("hard_signal"))
        store.upsert_offer(c, {
            "id": oid, "item_id": it["id"], "vendor": o.get("vendor") or dom,
            "domain": dom, "offer_type": o.get("offer_type"),
            "target": o.get("target"), "value": o.get("value"),
            "limits": o.get("limits"),
            "requires_cc": 1 if o.get("requires_credit_card") else 0,
            "requires_edu": 1 if o.get("requires_edu") else 0,
            "region": o.get("region"), "signup_url": o["signup_url"],
            "source_url": it.get("source_url") or it["url"],
            "expires_at": o.get("expires_at"), "model_class": o.get("model_class"),
            "why": o.get("why"), "score": sc, "verify_status": status,
            "verify": json.dumps(v, ensure_ascii=False), "corroborations": corr,
            "created_at": prev["created_at"] if prev else int(time.time()),
            "status": "active"})
        made += 1
        print("  offer[" + status + " " + str(sc) + "] " +
              str(o.get("vendor") or dom) + " - " + str(o.get("value"))[:40])
        c.commit()

    publish.build(c)
    c.commit()
    c.close()
    print("done in " + str(round(time.time() - t0)) + "s, offers touched=" + str(made))

if __name__ == "__main__":
    run()
