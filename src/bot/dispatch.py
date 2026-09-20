import time
from . import api, store, render

def eligible(u, o):
    if u.get("status") != "approved":
        return False
    if (u.get("mute_until") or 0) > time.time():
        return False
    if (o.get("score") or 0) < float(u.get("min_score") or 0):
        return False
    if o.get("status") and o["status"] != "active":
        return False
    if o.get("verify_status") in ("suspicious", "dead_link", "expired"):
        return False
    if u.get("types"):
        allowed = [t.strip() for t in str(u["types"]).split(",")]
        if o.get("offer_type") not in allowed:
            return False
    return True

def dispatch(mode="instant", max_per_user=8):
    db = store.load_users()
    deliv = store.load_deliv()
    offers = [o for o in store.load_offers()
              if not o.get("status") or o["status"] == "active"]
    offers.sort(key=lambda x: -(x.get("score") or 0))
    counter = {}
    for o in offers:
        oid = str(o.get("id"))
        already = set(deliv.get(oid, []))
        for key, u in db["users"].items():
            try:
                uid = int(key)
            except ValueError:
                continue
            if uid in already:
                continue
            if u.get("mode", "instant") != mode:
                continue
            if not eligible(u, o):
                continue
            if counter.get(uid, 0) >= max_per_user:
                continue
            try:
                api.send(uid, render.offer_text(o), render.offer_kb(o))
            except api.Blocked:
                u["status"] = "blocked"
                continue
            except Exception as e:
                print("send failed " + str(uid) + ": " + str(e)[:100])
                continue
            deliv.setdefault(oid, []).append(uid)
            u["sent_count"] = u.get("sent_count", 0) + 1
            u["last_sent"] = int(time.time())
            counter[uid] = counter.get(uid, 0) + 1
            time.sleep(0.08)
    store.prune_deliveries(deliv, set(str(o.get("id")) for o in offers))
    store.save_deliv(deliv)
    store.save_users(db)
    print("dispatch[" + mode + "]: " + str(sum(counter.values())) +
          " messages to " + str(len(counter)) + " users")

if __name__ == "__main__":
    import sys
    dispatch(sys.argv[1] if len(sys.argv) > 1 else "instant")
