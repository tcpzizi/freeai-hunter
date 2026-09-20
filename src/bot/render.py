import html

EMO = {"free_api": "🔑", "permanent_free_tier": "♾", "free_credits": "💰",
       "free_trial": "⏳", "student": "🎓", "startup": "🚀", "hackathon": "🏁",
       "aggregator": "🎁", "lifetime_deal": "🏷", "other": "•"}
BADGE = {"verified": "✅ تاییدشده", "likely": "🟡 محتمل",
         "unconfirmed": "⚪️ تاییدنشده", "unverifiable": "⚪️ نامشخص",
         "suspicious": "🔴 مشکوک", "dead_link": "⚰️ لینک مرده",
         "expired": "⌛️ منقضی"}
TYPE_FA = {"free_api": "API رایگان", "permanent_free_tier": "فری‌تیر دائمی",
           "free_credits": "کردیت رایگان", "free_trial": "تریال رایگان",
           "student": "دانشجویی", "startup": "استارتاپی", "hackathon": "هکاتون",
           "aggregator": "سرویس چندهوشه", "lifetime_deal": "لایف‌تایم",
           "other": "سایر"}
CLASS_FA = {"frontier": "مدل رده‌بالا", "mid": "مدل میان‌رده",
            "small": "مدل کوچک", "unknown": ""}

def offer_text(o):
    e = html.escape
    v = o.get("verify") or {}
    if not isinstance(v, dict):
        v = {}
    head = str(o.get("vendor") or o.get("domain") or o.get("target") or "?")
    L = [EMO.get(o.get("offer_type"), "•") + " <b>" + e(head) + "</b>   <code>" +
         str(o.get("score")) + "</code>"]
    row2 = "🏷 " + TYPE_FA.get(o.get("offer_type"), "—")
    cf = CLASS_FA.get(o.get("model_class") or "unknown", "")
    if cf:
        row2 += " · " + cf
    L.append(row2)
    if o.get("target"):
        L.append("🎯 " + e(str(o["target"]))[:200])
    L.append("🎁 <b>" + e(str(o.get("value") or "?"))[:150] + "</b>")
    if o.get("limits"):
        L.append("📊 " + e(str(o["limits"]))[:220])
    if o.get("expires_at"):
        L.append("⌛️ انقضا: " + e(str(o["expires_at"])))
    warn = []
    if o.get("requires_cc"):
        warn.append("کارت بانکی لازم")
    if o.get("requires_edu"):
        warn.append("ایمیل دانشگاهی")
    if o.get("region"):
        warn.append("منطقه: " + str(o["region"]))
    if warn:
        L.append("⚠️ " + e(" · ".join(warn)))
    meta = [BADGE.get(o.get("verify_status"), "")]
    if (o.get("corroborations") or 1) > 1:
        meta.append(str(o["corroborations"]) + " منبع مستقل")
    if v.get("domain_age_days"):
        meta.append("عمر دامنه " + str(v["domain_age_days"]) + " روز")
    L.append("  ·  ".join(x for x in meta if x))
    if o.get("why"):
        L.append("💬 " + e(str(o["why"]))[:300])
    return "\n".join(L)

def offer_kb(o):
    url = o.get("signup_url") or o.get("source_url") or ""
    row = []
    if url:
        row.append({"text": "🔗 دریافت آفر", "url": url})
    src = o.get("source_url")
    if src and src != url:
        row.append({"text": "📰 منبع", "url": src})
    kb = []
    if row:
        kb.append(row)
    kb.append([{"text": "👍", "callback_data": "fb:up:" + str(o.get("id"))},
               {"text": "👎", "callback_data": "fb:down:" + str(o.get("id"))},
               {"text": "🚫 مرده/جعلی", "callback_data": "fb:dead:" + str(o.get("id"))}])
    return kb
