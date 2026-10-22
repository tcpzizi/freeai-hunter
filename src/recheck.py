import json, time
from . import store, verify, score, publish

MAX_AGE_DAYS = 90

def main():
    vend = verify.load_vendors("config/vendors.yaml")
    try:
        fb = json.load(open("data/feedback.json", encoding="utf-8"))
    except Exception:
        fb = {}
    c = store.conn()
    now = int(time.time())
    rows = list(c.execute("SELECT * FROM offers WHERE status='active' "
                          "ORDER BY updated_at ASC LIMIT 120"))
    changed = 0
    for r in rows:
        o = {"signup_url": r["signup_url"], "offer_type": r["offer_type"],
             "value": r["value"], "model_class": r["model_class"],
             "requires_credit_card": bool(r["requires_cc"]),
             "requires_edu": bool(r["requires_edu"]), "confidence": 0.7}
        dead_votes = len((fb.get(r["id"]) or {}).get("dead", []))
        status, v = verify.verify(o, vend, r["corroborations"] or 1)
        if dead_votes >= 2 and status not in ("verified",):
            status = "dead_link"
        v["dead_votes"] = dead_votes
        new_status = "active"
        if status in ("dead_link", "expired", "suspicious"):
            new_status = "archived"
        if r["expires_at"]:
            try:
                exp = time.mktime(time.strptime(str(r["expires_at"])[:10], "%Y-%m-%d"))
                if exp < now:
                    new_status, status = "archived", "expired"
            except Exception:
                pass
        # permanent free tiers do not expire by age
        if (r["offer_type"] != "permanent_free_tier" and
                (now - (r["created_at"] or now)) > MAX_AGE_DAYS * 86400):
            new_status = "archived"
        sc = score.score(o, status)
        if status != r["verify_status"] or new_status != r["status"]:
            changed += 1
            print("  " + str(r["vendor"]) + ": " + str(r["verify_status"]) +
                  " -> " + status + " (" + new_status + ")")
        c.execute("UPDATE offers SET verify_status=?, verify=?, score=?, status=?, "
                  "updated_at=? WHERE id=?",
                  (status, json.dumps(v, ensure_ascii=False), sc, new_status,
                   now, r["id"]))
    c.commit()
    publish.build(c)
    c.commit()
    c.close()
    print("rechecked " + str(len(rows)) + " offers, " + str(changed) + " changed")

if __name__ == "__main__":
    main()
