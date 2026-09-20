import re

TYPE_W = {"free_api": 1.0, "permanent_free_tier": 1.0, "free_credits": 0.9,
          "aggregator": 0.85, "startup": 0.8, "student": 0.75, "hackathon": 0.7,
          "free_trial": 0.65, "lifetime_deal": 0.5, "other": 0.35}
CLASS_W = {"frontier": 1.0, "mid": 0.7, "small": 0.4, "unknown": 0.55}
VER_W = {"verified": 1.0, "likely": 0.8, "unconfirmed": 0.5, "unverifiable": 0.35,
         "suspicious": 0.05, "dead_link": 0.0, "expired": 0.0}
NOTIFY_AT = 38.0

def money(v):
    m = re.search(r"\$\s?([\d,]+)", v or "")
    if not m:
        return 0.4
    try:
        x = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.4
    return min(1.0, 0.3 + (min(x, 5000) / 500) ** 0.5 * 0.7)

def score(o, ver_status, hard_signal=False):
    s = 100.0 * TYPE_W.get(o.get("offer_type"), 0.35) \
              * CLASS_W.get(o.get("model_class"), 0.55) \
              * VER_W.get(ver_status, 0.4)
    s *= 0.6 + 0.4 * money(o.get("value"))
    try:
        conf = float(o.get("confidence") or 0.5)
    except (TypeError, ValueError):
        conf = 0.5
    s *= 0.85 + 0.15 * max(0.0, min(1.0, conf))
    if o.get("requires_credit_card"):
        s *= 0.75
    if o.get("requires_edu"):
        s *= 0.80
    if hard_signal:
        s *= 1.25
    return round(min(s, 100.0), 1)
