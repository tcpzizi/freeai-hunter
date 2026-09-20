import os, re, time
from . import api, store, render

ADMINS = set()
for _x in (os.getenv("ADMIN_IDS", "") or "").replace(" ", "").split(","):
    if _x.isdigit():
        ADMINS.add(int(_x))

WELCOME_PENDING = ("سلام {name} 👋\n\n"
 "این بات آفرهای <b>رایگان و تریالی هوش مصنوعی</b> را رصد می‌کند: "
 "API رایگان، کردیت هدیه، فری‌تیر دائمی، پلن دانشجویی و استارتاپی.\n\n"
 "⏳ درخواست عضویت شما ثبت و برای <b>تایید ادمین</b> ارسال شد.\n"
 "به‌محض تایید، همین‌جا خبردار می‌شوید.")
WELCOME_OK = ("🎉 عضویت شما <b>تایید</b> شد!\n\n"
 "از این پس هر آفر جدید مستقیم به پیویتان می‌آید.\n"
 "• /settings تنظیم حداقل امتیاز و نوع آفر\n"
 "• /latest آفرهای داغ فعلی\n"
 "• /mute 12h سکوت موقت\n"
 "• /stop لغو عضویت")
STATUS_FA = {"pending": "⏳ در انتظار تایید ادمین", "approved": "✅ فعال",
             "rejected": "❌ رد شده", "blocked": "🚫 مسدود", "left": "خارج شده"}

def is_admin(uid):
    return uid in ADMINS

def review_kb(uid):
    return [[{"text": "✅ تایید", "callback_data": "ap:" + str(uid)},
             {"text": "❌ رد", "callback_data": "rj:" + str(uid)},
             {"text": "🚫 بلاک", "callback_data": "bl:" + str(uid)}]]

def notify_admins_new(u, uid):
    uname = ("@" + u["username"]) if u.get("username") else "—"
    txt = ("🆕 <b>درخواست عضویت جدید</b>\nنام: " + str(u.get("first_name", "")) +
           "\nیوزرنیم: " + uname + "\nآیدی: <code>" + str(uid) + "</code>\nزمان: " +
           time.strftime("%Y-%m-%d %H:%M", time.gmtime(u.get("requested_at", 0))) + " UTC")
    if not ADMINS:
        print("WARNING: ADMIN_IDS is empty, nobody to notify")
    for a in ADMINS:
        try:
            api.send(a, txt, review_kb(uid))
        except Exception as e:
            print("admin notify failed " + str(a) + ": " + str(e)[:100])

def active_offers():
    out = []
    for o in store.load_offers():
        if o.get("status") and o["status"] != "active":
            continue
        if o.get("verify_status") in ("suspicious", "dead_link", "expired"):
            continue
        out.append(o)
    out.sort(key=lambda x: (-(x.get("score") or 0), -(x.get("updated_at") or 0)))
    return out

def send_top(uid, n=5):
    for o in active_offers()[:n]:
        try:
            api.send(uid, render.offer_text(o), render.offer_kb(o))
        except api.Blocked:
            raise
        except Exception as e:
            print("send_top failed: " + str(e)[:100])
        time.sleep(0.2)

def cmd_start(db, uid, msg):
    u, is_new = store.get_or_create(db, uid, msg.get("from", {}))
    if u["status"] == "approved":
        return api.send(uid, "✅ شما قبلاً تایید شده‌اید. /latest را بزنید.")
    if u["status"] == "blocked":
        return api.send(uid, "دسترسی شما توسط ادمین مسدود شده است.")
    if u["status"] in ("rejected", "left"):
        u["status"] = "pending"
        u["requested_at"] = int(time.time())
        is_new = True
    api.send(uid, WELCOME_PENDING.format(name=u.get("first_name") or ""))
    if is_new:
        notify_admins_new(u, uid)

def cmd_status(db, uid, msg):
    u = db["users"].get(str(uid))
    if not u:
        return api.send(uid, "هنوز عضو نشده‌اید. /start را بزنید.")
    t = ["وضعیت: <b>" + STATUS_FA.get(u["status"], u["status"]) + "</b>",
         "حداقل امتیاز: <code>" + str(u["min_score"]) + "</code>",
         "نوع آفرها: " + str(u["types"] or "همه"),
         "حالت: " + ("فوری" if u.get("mode") == "instant" else "خلاصه روزانه"),
         "آفرهای ارسال‌شده: " + str(u.get("sent_count", 0))]
    if (u.get("mute_until") or 0) > time.time():
        t.append("🔇 ساکت تا " +
                 time.strftime("%m-%d %H:%M", time.gmtime(u["mute_until"])) + " UTC")
    api.send(uid, "\n".join(t))

def cmd_latest(db, uid, msg):
    u = db["users"].get(str(uid))
    if not u or u["status"] != "approved":
        return api.send(uid, "این دستور فقط برای اعضای تاییدشده است.")
    if not active_offers():
        return api.send(uid, "فعلاً آفر فعالی در لیست نیست.")
    send_top(uid, 5)

SET_KB = [[{"text": "۳۰ (همه‌چیز)", "callback_data": "set:score:30"},
           {"text": "۴۵ (متعادل)", "callback_data": "set:score:45"},
           {"text": "۶۰ (فقط مهم‌ها)", "callback_data": "set:score:60"}],
          [{"text": "فقط API رایگان",
            "callback_data": "set:types:free_api,permanent_free_tier"},
           {"text": "فقط کردیت/تریال",
            "callback_data": "set:types:free_credits,free_trial"}],
          [{"text": "همه‌ی انواع", "callback_data": "set:types:all"}],
          [{"text": "⚡️ فوری", "callback_data": "set:mode:instant"},
           {"text": "🗓 خلاصه روزانه", "callback_data": "set:mode:digest"}]]

def cmd_settings(db, uid, msg):
    api.send(uid, "⚙️ تنظیمات اعلان‌ها:", SET_KB)

def cmd_mute(db, uid, msg):
    u = db["users"].get(str(uid))
    if not u:
        return
    m = re.search(r"(\d+)\s*([hdهر])?", msg.get("text", ""))
    hours = int(m.group(1)) if m else 12
    if m and (m.group(2) or "") in ("d", "ر"):
        hours *= 24
    hours = max(1, min(hours, 720))
    u["mute_until"] = int(time.time()) + hours * 3600
    api.send(uid, "🔇 تا " + str(hours) + " ساعت آینده پیامی نمی‌فرستم.")

def cmd_stop(db, uid, msg):
    u = db["users"].get(str(uid))
    if u:
        u["status"] = "left"
    api.send(uid, "عضویت شما لغو شد. هر وقت خواستید /start بزنید. 👋")

def cmd_help(db, uid, msg):
    txt = ("دستورها:\n/start عضویت\n/status وضعیت\n/latest آفرهای داغ\n"
           "/settings تنظیمات\n/mute 12h سکوت موقت\n/stop لغو عضویت")
    if is_admin(uid):
        txt += ("\n\n<b>ادمین</b>:\n/pending صف تایید\n/users آمار\n"
                "/approve ID\n/broadcast متن")
    api.send(uid, txt)

def cmd_pending(db, uid, msg):
    if not is_admin(uid):
        return
    p = [(k, v) for k, v in db["users"].items() if v.get("status") == "pending"]
    if not p:
        return api.send(uid, "صف تایید خالی است ✅")
    for k, u in p[:20]:
        api.send(uid, "⏳ " + str(u.get("first_name", "")) + " (@" +
                 str(u.get("username") or "—") + ") <code>" + k + "</code>",
                 review_kb(k))

def cmd_users(db, uid, msg):
    if not is_admin(uid):
        return
    cnt = {}
    for u in db["users"].values():
        cnt[u.get("status")] = cnt.get(u.get("status"), 0) + 1
    api.send(uid, "👥 " + " · ".join(str(k) + ": " + str(v) for k, v in cnt.items()) +
             "\nجمع: " + str(len(db["users"])))

def cmd_approve(db, uid, msg):
    if not is_admin(uid):
        return
    ids = re.findall(r"\d{5,}", msg.get("text", ""))
    if not ids:
        return api.send(uid, "استفاده: /approve 123456789")
    for t in ids:
        if decide(db, t, "approved", uid):
            api.send(uid, "✅ " + t + " تایید شد.")
        else:
            api.send(uid, "کاربر " + t + " پیدا نشد.")

def cmd_broadcast(db, uid, msg):
    if not is_admin(uid):
        return
    parts = msg.get("text", "").split(" ", 1)
    if len(parts) < 2:
        return api.send(uid, "استفاده: /broadcast متن پیام")
    ok = 0
    for key, u in db["users"].items():
        if u.get("status") != "approved":
            continue
        try:
            api.send(int(key), "📢 " + parts[1])
            ok += 1
            time.sleep(0.08)
        except api.Blocked:
            u["status"] = "blocked"
        except Exception:
            pass
    api.send(uid, "ارسال شد به " + str(ok) + " کاربر.")

def decide(db, key, status, by):
    u = db["users"].get(str(key))
    if not u:
        return None
    u["status"] = status
    u["decided_at"] = int(time.time())
    u["decided_by"] = by
    if status == "approved":
        try:
            api.send(int(key), WELCOME_OK)
            send_top(int(key), 5)
        except api.Blocked:
            u["status"] = "blocked"
        except Exception as e:
            print("welcome failed: " + str(e)[:120])
    elif status == "rejected":
        try:
            api.send(int(key), "متاسفانه درخواست عضویت شما تایید نشد.")
        except Exception:
            pass
    return u

CMDS = {"start": cmd_start, "status": cmd_status, "latest": cmd_latest,
        "settings": cmd_settings, "mute": cmd_mute, "stop": cmd_stop,
        "help": cmd_help, "pending": cmd_pending, "users": cmd_users,
        "approve": cmd_approve, "broadcast": cmd_broadcast}

def handle_message(db, msg):
    frm = msg.get("from") or {}
    if not frm or frm.get("is_bot"):
        return
    if (msg.get("chat") or {}).get("type") != "private":
        return
    uid = frm["id"]
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        u = db["users"].get(str(uid))
        if u and u.get("status") == "pending":
            api.send(uid, "⏳ درخواست شما در صف تایید ادمین است.")
        elif u and u.get("status") == "approved":
            api.send(uid, "برای دیدن دستورها /help را بزنید.")
        else:
            api.send(uid, "برای عضویت /start را بزنید.")
        return
    cmd = re.split(r"[\s@]", text[1:])[0].lower()
    fn = CMDS.get(cmd)
    if fn:
        fn(db, uid, msg)
    else:
        api.send(uid, "دستور ناشناس. /help")

def handle_callback(db, cb):
    uid = (cb.get("from") or {}).get("id")
    data = cb.get("data") or ""
    m = cb.get("message") or {}
    mid = m.get("message_id")
    chat = (m.get("chat") or {}).get("id")
    if data[:3] in ("ap:", "rj:", "bl:"):
        if not is_admin(uid):
            return api.answer_cb(cb["id"], "فقط ادمین.", True)
        key = data.split(":", 1)[1]
        new = {"ap": "approved", "rj": "rejected", "bl": "blocked"}[data[:2]]
        u = decide(db, key, new, uid)
        if not u:
            return api.answer_cb(cb["id"], "کاربر پیدا نشد.", True)
        fa = {"approved": "✅ تایید", "rejected": "❌ رد", "blocked": "🚫 بلاک"}[new]
        api.answer_cb(cb["id"], fa)
        try:
            api.edit(chat, mid, (m.get("text") or "") + "\n\n<b>" + fa +
                     "</b> توسط <code>" + str(uid) + "</code>")
        except Exception:
            pass
        return
    if data.startswith("set:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        field, val = parts[1], parts[2]
        u = db["users"].get(str(uid))
        if not u:
            return api.answer_cb(cb["id"], "ابتدا /start", True)
        if field == "score":
            u["min_score"] = float(val)
        elif field == "types":
            u["types"] = None if val == "all" else val
        elif field == "mode":
            u["mode"] = val
        return api.answer_cb(cb["id"], "ذخیره شد ✅")
    if data.startswith("fb:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        kind, oid = parts[1], parts[2]
        fb = store.load_json(store.FEEDB_F, {})
        fb.setdefault(oid, {}).setdefault(kind, [])
        if uid not in fb[oid][kind]:
            fb[oid][kind].append(uid)
        store.save_json(store.FEEDB_F, fb)
        return api.answer_cb(cb["id"], "ممنون! ثبت شد 🙏")
