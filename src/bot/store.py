import json, os, pathlib, tempfile, time

USERS_F = os.getenv("USERS_FILE", "data/users.json")
DELIV_F = os.getenv("DELIV_FILE", "data/deliveries.json")
STATE_F = os.getenv("BOTSTATE_FILE", "data/bot_state.json")
FEEDB_F = os.getenv("FEEDBACK_FILE", "data/feedback.json")
OFFERS_F = os.getenv("OFFERS_FILE", "data/offers.json")

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)

def load_users():
    db = load_json(USERS_F, {"users": {}})
    db.setdefault("users", {})
    return db

def save_users(db):
    save_json(USERS_F, db)

def load_deliv():
    return load_json(DELIV_F, {})

def save_deliv(d):
    save_json(DELIV_F, d)

def load_state():
    return load_json(STATE_F, {"offset": 0})

def save_state(s):
    save_json(STATE_F, s)

def load_offers():
    data = load_json(OFFERS_F, [])
    return data if isinstance(data, list) else []

DEFAULTS = {"status": "pending", "min_score": 45.0, "types": None,
            "mode": "instant", "mute_until": 0, "sent_count": 0,
            "last_sent": 0, "note": ""}

def get_or_create(db, uid, tg_user):
    key = str(uid)
    u = db["users"].get(key)
    if u is None:
        u = dict(DEFAULTS)
        u["requested_at"] = int(time.time())
        u["username"] = tg_user.get("username") or ""
        u["first_name"] = tg_user.get("first_name") or ""
        u["lang"] = tg_user.get("language_code") or "fa"
        db["users"][key] = u
        return u, True
    for k, v in DEFAULTS.items():
        u.setdefault(k, v)
    if tg_user.get("username"):
        u["username"] = tg_user["username"]
    if tg_user.get("first_name"):
        u["first_name"] = tg_user["first_name"]
    return u, False

def prune_deliveries(d, keep_ids):
    for k in list(d.keys()):
        if k not in keep_ids:
            d.pop(k, None)
    return d
