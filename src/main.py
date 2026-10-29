import time, hashlib, json, yaml
import urllib.parse as up
import concurrent.futures as cf
from . import store, filters, extract, verify, score, publish, dedup
from .collectors import (model_registry, pagediff, github_watch, rss,
                         googlenews, reddit as rd, hackernews as hnc,
                         tgchannel, bluesky, devpost)

DROP_Q = ("utm_", "ref", "fbclid", "gclid", "aff", "mc_cid", "mc_eid")
# an item that keeps failing (bad page, LLM error on it) is dropped after this many tries
MAX_TRIES = 3


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


def _tri(x):
    """True/False/None -> 1/0/None, so "unknown" is not stored as "no card"."""
    if isinstance(x, str):
        x = {"true": True, "yes": True, "false": False, "no": False}.get(x.strip().lower())
    return None if x is None else (1 if x else 0)


def handle_offer(c, vend, it, o):
    """Verify, score and store one extracted offer. Returns the verify status."""
    o["signup_url"] = o.get("signup_url") or it["url"]
    dom = verify.domain(o["signup_url"])
    if not dom:
        return "no_domain"
    oid = dedup.offer_key(o, dom, o["signup_url"])
    prev = c.execute("SELECT created_at,sources FROM offers WHERE id=?",
                     (oid,)).fetchone()
    # corroboration = distinct source families, not "seen again next hour"
    srcs = dedup.load_sources(prev["sources"]) if prev else set()
    srcs.add(dedup.source_family(it.get("source")))
    corr = max(1, len(srcs))
    try:
        status, v = verify.verify(o, vend, corr)
    except Exception as e:
        status, v = "unverifiable", {"error": str(e)[:150], "corroborations": corr}
    # the vendor page wins when it says so; otherwise keep the extractor's answer
    if v.get("cc_required") is not None:
        o["requires_credit_card"] = v["cc_required"]
    o["requires_credit_card"] = (None if _tri(o.get("requires_credit_card")) is None
                                 else bool(_tri(o.get("requires_credit_card"))))
    # same rule for a school / .edu email requirement
    if v.get("edu_required") is not None:
        o["requires_edu"] = v["edu_required"]
    o["requires_edu"] = (None if _tri(o.get("requires_edu")) is None
                         else bool(_tri(o.get("requires_edu"))))
    sc = score.score(o, status, it.get("hard_signal"))
    store.upsert_offer(c, {
        "id": oid, "item_id": it["id"], "vendor": o.get("vendor") or dom,
        "domain": dom, "offer_type": o.get("offer_type"),
        "target": o.get("target"), "value": o.get("value"),
        "limits": o.get("limits"),
        "requires_cc": _tri(o.get("requires_credit_card")),
        "requires_edu": _tri(o.get("requires_edu")),
        "region": o.get("region"), "signup_url": o["signup_url"],
        "source_url": it.get("source_url") or it["url"],
        "expires_at": o.get("expires_at"), "model_class": o.get("model_class"),
        "why": o.get("why"), "score": sc, "verify_status": status,
        "verify": json.dumps(v, ensure_ascii=False), "corroborations": corr,
        "sources": json.dumps(sorted(srcs)),
        "created_at": prev["created_at"] if prev else int(time.time()),
        "status": "archived" if status == "not_offer" else "active"})
    print(" offer[" + status + " " + str(sc) + "] " +
          str(o.get("vendor") or dom) + " - " + str(o.get("value"))[:40])
    return status


def process(c, vend, batch):
    """Extract a batch from the queue. An item leaves the queue only once it was
    judged; on errors, or when no LLM answered, it stays for the next run.
    Returns (offers, rejected, retried)."""
    made, rejected, retried = 0, 0, 0
    for it in batch:
        try:
            o = extract.extract(it)
        except Exception as e:
            print("extract failed: " + str(e)[:120])
            store.retry_later(c, it["id"], "extract: " + str(e))
            retried += 1
            c.commit()
            continue
        if not o or not o.get("is_offer"):
            if o and o.get("extracted_by") == "regex":
                # no LLM answered: the rule-based fallback is not a real verdict
                store.retry_later(c, it["id"], "no_llm", count=False)
                retried += 1
            else:
                if o and o.get("reject_reason"):
                    rejected += 1
                store.dequeue(c, it["id"])
            c.commit()
            continue
        try:
            status = handle_offer(c, vend, it, o)
        except Exception as e:
            print("offer failed: " + str(e)[:120])
            store.retry_later(c, it["id"], "offer: " + str(e))
            retried += 1
            c.commit()
            continue
        store.dequeue(c, it["id"])
        if status in ("not_offer", "no_domain"):
            rejected += 1
        else:
            made += 1
        c.commit()  # per item, so a timeout keeps the work already done
    return made, rejected, retried


def run(limit_extract=40):
    cfg = yaml.safe_load(open("config/sources.yaml", encoding="utf-8"))
    vend = verify.load_vendors("config/vendors.yaml")
    c = store.conn()
    t0 = time.time()
    if not store.meta_get(c, "queue_backfill_v1"):
        n = store.backfill_queue(c, filters.THRESHOLD)
        store.meta_set(c, "queue_backfill_v1", int(time.time()))
        c.commit()
        print("queue backfill: " + str(n) + " earlier candidates re-queued")
    raw = collect(cfg, c)
    seen_now, queued = set(), 0
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
            store.enqueue(c, it)
            queued += 1
    dropped = store.prune_queue(c, MAX_TRIES)
    c.commit()
    batch = store.next_batch(c, limit_extract)
    print("collected=" + str(len(raw)) + " new=" + str(len(seen_now)) +
          " queued=" + str(queued) + " backlog=" + str(store.queue_size(c)) +
          " batch=" + str(len(batch)) + " dropped=" + str(dropped))
    made, rejected, retried = process(c, vend, batch)
    publish.build(c)
    c.commit()
    left = store.queue_size(c)
    c.close()
    print("done in " + str(round(time.time() - t0)) + "s, offers touched=" + str(made) +
          ", rejected=" + str(rejected) + ", retry later=" + str(retried) +
          ", still queued=" + str(left))


if __name__ == "__main__":
    run()
