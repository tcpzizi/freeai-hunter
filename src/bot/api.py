import os, time, httpx

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BASE = "https://api.telegram.org/bot" + TOKEN
_cli = httpx.Client(timeout=45)

class Blocked(Exception):
    pass

class Fatal(Exception):
    pass

def call(method, **payload):
    if not TOKEN:
        raise Fatal("TELEGRAM_BOT_TOKEN is not set")
    last = ""
    for attempt in range(4):
        try:
            r = _cli.post(BASE + "/" + method, json=payload)
        except httpx.RequestError as e:
            last = str(e)[:150]
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json().get("result")
        try:
            err = r.json()
        except Exception:
            err = {"description": r.text[:200]}
        desc = (err.get("description") or "").lower()
        last = str(r.status_code) + " " + desc
        if r.status_code == 429:
            time.sleep(int((err.get("parameters") or {}).get("retry_after", 3)) + 1)
            continue
        if r.status_code == 403 or "bot was blocked" in desc \
           or "user is deactivated" in desc or "chat not found" in desc:
            raise Blocked(desc)
        if r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        raise Fatal(method + ": " + last)
    raise Fatal(method + ": retries exhausted (" + last + ")")

def send(chat_id, text, kb=None, preview=False):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": (not preview)}
    if kb:
        p["reply_markup"] = {"inline_keyboard": kb}
    return call("sendMessage", **p)

def edit(chat_id, mid, text, kb=None):
    return call("editMessageText", chat_id=chat_id, message_id=mid, text=text,
                parse_mode="HTML", disable_web_page_preview=True,
                reply_markup={"inline_keyboard": kb or []})

def answer_cb(cb_id, text="", alert=False):
    try:
        call("answerCallbackQuery", callback_query_id=cb_id, text=text,
             show_alert=alert)
    except Exception:
        pass

def get_updates(offset, timeout=25):
    return call("getUpdates", offset=offset, timeout=timeout, limit=50,
                allowed_updates=["message", "callback_query"]) or []

def delete_webhook():
    try:
        call("deleteWebhook", drop_pending_updates=False)
    except Exception:
        pass

def set_commands():
    try:
        call("setMyCommands", commands=[
            {"command": "start", "description": "عضویت / شروع"},
            {"command": "status", "description": "وضعیت عضویت من"},
            {"command": "latest", "description": "۵ آفر داغ فعلی"},
            {"command": "settings", "description": "تنظیمات اعلان"},
            {"command": "mute", "description": "سکوت موقت مثل /mute 12h"},
            {"command": "stop", "description": "لغو عضویت"},
            {"command": "help", "description": "راهنما"}])
    except Exception as e:
        print("setMyCommands failed: " + str(e)[:120])
