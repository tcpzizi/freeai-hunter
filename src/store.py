import sqlite3, os, time, pathlib, hashlib, json

DB = os.getenv("DB_PATH", "data/state.sqlite")
DELIV_F = os.getenv("DELIV_FILE", "data/deliveries.json")
FEEDB_F = os.getenv("FEEDBACK_FILE", "data/feedback.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY, canon_url TEXT, url TEXT, title TEXT, body TEXT,
  source TEXT, source_type TEXT, published_at INTEGER,
  first_seen INTEGER, last_seen INTEGER, prefilter REAL
);
CREATE TABLE IF NOT EXISTS offers(
  id TEXT PRIMARY KEY, item_id TEXT, vendor TEXT, domain TEXT,
  offer_type TEXT, target TEXT, value TEXT, limits TEXT,
  requires_cc INTEGER, requires_edu INTEGER, region TEXT,
  signup_url TEXT, source_url TEXT, expires_at TEXT,
  model_class TEXT, why TEXT, score REAL,
  verify_status TEXT, verify TEXT, corroborations INTEGER DEFAULT 1,
  created_at INTEGER, updated_at INTEGER, status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS snapshots(
  key TEXT PRIMARY KEY, hash TEXT, payload TEXT, fetched_at INTEGER
);
CREATE TABLE IF NOT EXISTS source_health(
  source TEXT PRIMARY KEY, fails INTEGER DEFAULT 0, last_ok INTEGER,
  last_err TEXT, yield_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS candidate_sources(
  url TEXT PRIMARY KEY, kind TEXT, found_via TEXT, added_at INTEGER
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
-- candidates waiting for the LLM. An item leaves this table only once it was
-- actually judged; overflow and failures stay here for the next run.
CREATE TABLE IF NOT EXISTS extract_queue(
  item_id TEXT PRIMARY KEY, payload TEXT, priority REAL,
  hard_signal INTEGER DEFAULT 0, tries INTEGER DEFAULT 0, last_error TEXT,
  queued_at INTEGER, last_try INTEGER
);
CREATE INDEX IF NOT EXISTS ix_offers_domain ON offers(domain);
CREATE INDEX IF NOT EXISTS ix_offers_status ON offers(status);
"""


def _ensure_columns(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(offers)")}
    if "sources" not in cols:
        c.execute("ALTER TABLE offers ADD COLUMN sources TEXT")


def conn():
    pathlib.Path(DB).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    _ensure_columns(c)
    migrate_dedup_v2(c)
    migrate_cc_v1(c)
    migrate_edu_v1(c)
    migrate_zero_v1(c)
    return c


def seen(c, item_id):
    return c.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is not None


def put_item(c, it):
    now = int(time.time())
    c.execute("""INSERT INTO items(id,canon_url,url,title,body,source,source_type,
                 published_at,first_seen,last_seen,prefilter)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen""",
              (it["id"], it["canon_url"], it["url"], it["title"],
               (it.get("body") or "")[:8000], it["source"], it["source_type"],
               int(it.get("published_at") or now), now, now,
               float(it.get("prefilter") or 0)))


def snapshot(c, key, payload):
    h = hashlib.sha256(payload.encode("utf-8", "ignore")).hexdigest()
    row = c.execute("SELECT hash,payload FROM snapshots WHERE key=?", (key,)).fetchone()
    c.execute("""INSERT INTO snapshots(key,hash,payload,fetched_at) VALUES(?,?,?,?)
                 ON CONFLICT(key) DO UPDATE SET hash=excluded.hash,
                 payload=excluded.payload, fetched_at=excluded.fetched_at""",
              (key, h, payload, int(time.time())))
    if row is None:
        return False, None
    return (row["hash"] != h), row["payload"]


def meta_get(c, k):
    r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else None


def meta_set(c, k, v):
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))


# ------------------------------------------------------------ extract queue
QUEUE_MAX_AGE_DAYS = 7
_PAYLOAD_KEYS = ("id", "url", "canon_url", "title", "source", "source_type",
                 "source_url", "published_at", "prefilter", "hard_signal")


def enqueue(c, it):
    """Add a candidate (keeps the first entry if it is already queued)."""
    payload = {k: it.get(k) for k in _PAYLOAD_KEYS if it.get(k) is not None}
    payload["body"] = (it.get("body") or "")[:8000]
    c.execute("""INSERT INTO extract_queue(item_id,payload,priority,hard_signal,tries,
                 queued_at) VALUES(?,?,?,?,0,?) ON CONFLICT(item_id) DO NOTHING""",
              (it["id"], json.dumps(payload, ensure_ascii=False, default=str),
               float(it.get("prefilter") or 0), 1 if it.get("hard_signal") else 0,
               int(time.time())))


def next_batch(c, limit):
    """Best candidates first: hard signals, then fewest failures, then score."""
    out = []
    rows = c.execute("SELECT item_id, payload FROM extract_queue ORDER BY hard_signal DESC, "
                     "tries ASC, priority DESC, queued_at ASC LIMIT ?", (int(limit),))
    for r in list(rows):
        try:
            out.append(json.loads(r["payload"]))
        except Exception:
            dequeue(c, r["item_id"])  # unreadable entry would block the queue
    return out


def dequeue(c, item_id):
    c.execute("DELETE FROM extract_queue WHERE item_id=?", (item_id,))


def retry_later(c, item_id, error="", count=True):
    """Keep the item for the next run. count=False for outages that are not the
    item's fault (no LLM answered), so they do not use up its tries."""
    c.execute("UPDATE extract_queue SET tries=tries+?, last_error=?, last_try=? "
              "WHERE item_id=?", (1 if count else 0, str(error)[:200],
                                  int(time.time()), item_id))


def prune_queue(c, max_tries, max_age_days=QUEUE_MAX_AGE_DAYS):
    """Drop entries that failed too often or waited too long. Returns the count."""
    old = int(time.time()) - int(max_age_days * 86400)
    cur = c.execute("DELETE FROM extract_queue WHERE tries>=? OR queued_at<?",
                    (int(max_tries), old))
    return cur.rowcount


def queue_size(c):
    return c.execute("SELECT COUNT(*) FROM extract_queue").fetchone()[0]


def backfill_queue(c, threshold, days=3, cap=300):
    """One-off recovery: before the queue existed, candidates past the per-run
    limit were saved as 'seen' and never extracted. Re-queue recent ones that
    passed the prefilter and never produced an offer."""
    since = int(time.time()) - int(days * 86400)
    rows = list(c.execute(
        "SELECT i.* FROM items i WHERE i.prefilter>=? AND i.first_seen>=? "
        "AND NOT EXISTS (SELECT 1 FROM offers o WHERE o.item_id=i.id) "
        "AND NOT EXISTS (SELECT 1 FROM extract_queue q WHERE q.item_id=i.id) "
        "ORDER BY i.prefilter DESC, i.first_seen DESC LIMIT ?",
        (float(threshold), since, int(cap))))
    for r in rows:
        enqueue(c, dict(r))
    return len(rows)


OFFER_COLS = ["id","item_id","vendor","domain","offer_type","target","value","limits",
              "requires_cc","requires_edu","region","signup_url","source_url",
              "expires_at","model_class","why","score","verify_status","verify",
              "corroborations","created_at","updated_at","status","sources"]
# descriptive fields follow the best-scoring sighting; in SQLite every RHS below
# sees the OLD row, so the CASE compares against the pre-update score.
_BETTER = "excluded.score >= COALESCE(offers.score,0)"
_FOLLOW = ["item_id", "vendor", "target", "value", "limits", "signup_url",
           "source_url", "model_class", "why", "region"]


def upsert_offer(c, row):
    now = int(time.time())
    row.setdefault("created_at", now)
    row["updated_at"] = now
    row.setdefault("status", "active")
    vals = [row.get(k) for k in OFFER_COLS]
    ph = ",".join("?" * len(OFFER_COLS))
    upd = ["score=max(COALESCE(score,0),excluded.score)",
           "verify_status=excluded.verify_status", "verify=excluded.verify",
           "corroborations=excluded.corroborations", "sources=excluded.sources",
           "updated_at=excluded.updated_at", "status=excluded.status",
           "expires_at=COALESCE(excluded.expires_at,expires_at)",
           # unknown (NULL) never replaces a known yes/no
           "requires_cc=CASE WHEN " + _BETTER + " THEN COALESCE(excluded.requires_cc,"
           "requires_cc) ELSE COALESCE(requires_cc,excluded.requires_cc) END",
           "requires_edu=CASE WHEN " + _BETTER + " THEN COALESCE(excluded.requires_edu,"
           "requires_edu) ELSE COALESCE(requires_edu,excluded.requires_edu) END"]
    upd += [k + "=CASE WHEN " + _BETTER + " THEN excluded." + k + " ELSE " + k + " END"
            for k in _FOLLOW]
    c.execute("INSERT INTO offers(" + ",".join(OFFER_COLS) + ") VALUES(" + ph + ") "
              "ON CONFLICT(id) DO UPDATE SET " + ", ".join(upd), vals)


# ---------------------------------------------------------------- migration
def _merge(dst, src):
    if isinstance(dst, list) and isinstance(src, list):
        return dst + [x for x in src if x not in dst]
    if isinstance(dst, dict) and isinstance(src, dict):
        for k, v in src.items():
            dst[k] = _merge(dst[k], v) if k in dst else v
        return dst
    return dst


def _remap_json(path, remap):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return 0
    if not isinstance(d, dict):
        return 0
    n = 0
    for old, new in remap.items():
        if old in d:
            v = d.pop(old)
            d[new] = _merge(d[new], v) if new in d else v
            n += 1
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    return n


def migrate_dedup_v2(c):
    """One-off: re-key offers with the stable v2 key, merge duplicates, drop
    unit-price junk, and carry Telegram deliveries/feedback over to the new ids
    so nobody gets the same offer twice."""
    if c.execute("SELECT 1 FROM meta WHERE k='dedup_v2'").fetchone():
        return
    from . import dedup
    now = int(time.time())
    rows = list(c.execute("SELECT o.*, i.body AS item_body, i.title AS item_title, "
                          "i.source AS item_source FROM offers o "
                          "LEFT JOIN items i ON i.id=o.item_id"))
    groups, junk = {}, []
    for r in rows:
        o = {"is_offer": True, "offer_type": r["offer_type"], "value": r["value"]}
        dedup.sanitize(o, (r["item_title"] or "") + "\n" + (r["item_body"] or ""))
        if not o["is_offer"]:
            junk.append(r["id"])
            continue
        nid = dedup.offer_key(o, r["domain"] or "", r["signup_url"] or "")
        groups.setdefault(nid, []).append((r, o))
    for oid in junk:
        c.execute("UPDATE offers SET status='archived', verify_status='not_offer', "
                  "updated_at=? WHERE id=?", (now, oid))
    remap, merged = {}, 0
    for nid, members in groups.items():
        members.sort(key=lambda m: (m[0]["status"] == "active", m[0]["score"] or 0),
                     reverse=True)
        best, bo = members[0]
        srcs = sorted({dedup.source_family(m[0]["item_source"]) for m in members
                       if m[0]["item_source"]})
        row = {k: best[k] for k in OFFER_COLS if k in best.keys()}
        row.update({"id": nid, "offer_type": bo["offer_type"], "value": bo["value"],
                    "sources": json.dumps(srcs),
                    "corroborations": max(1, len(srcs)),
                    "created_at": min((m[0]["created_at"] or now) for m in members),
                    "score": max((m[0]["score"] or 0) for m in members),
                    "status": "active" if any(m[0]["status"] == "active"
                                               for m in members) else best["status"]})
        for m in members:
            c.execute("DELETE FROM offers WHERE id=?", (m[0]["id"],))
            if m[0]["id"] != nid:
                remap[m[0]["id"]] = nid
        cols = [k for k in OFFER_COLS if k in row]
        c.execute("INSERT OR REPLACE INTO offers(" + ",".join(cols) + ") VALUES(" +
                  ",".join("?" * len(cols)) + ")", [row[k] for k in cols])
        merged += len(members) - 1
    nd = _remap_json(DELIV_F, remap)
    nf = _remap_json(FEEDB_F, remap)
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('dedup_v2',?)", (str(now),))
    c.commit()
    print("migrate dedup_v2: " + str(len(rows)) + " offers -> " + str(len(groups)) +
          " (merged " + str(merged) + ", junk " + str(len(junk)) + "), remapped " +
          str(nd) + " deliveries / " + str(nf) + " feedback")


def migrate_cc_v1(c):
    """One-off: stored card values could not be trusted (unknown was saved as 0,
    and "No credit card required" pages as 1). Reset them to unknown; hunt and
    recheck fill them again from the vendor pages."""
    if c.execute("SELECT 1 FROM meta WHERE k='cc_v1'").fetchone():
        return
    n = c.execute("UPDATE offers SET requires_cc=NULL WHERE requires_cc IS NOT NULL").rowcount
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('cc_v1',?)", (str(int(time.time())),))
    c.commit()
    print("migrate cc_v1: reset card requirement on " + str(n) + " offers")


def migrate_edu_v1(c):
    """One-off: a stored 0 meant either "no school email needed" or "not stated".
    Reset those to unknown (1 came from an explicit answer and is kept)."""
    if c.execute("SELECT 1 FROM meta WHERE k='edu_v1'").fetchone():
        return
    n = c.execute("UPDATE offers SET requires_edu=NULL WHERE requires_edu=0").rowcount
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('edu_v1',?)", (str(int(time.time())),))
    c.commit()
    print("migrate edu_v1: reset school-email requirement on " + str(n) + " offers")


def migrate_zero_v1(c):
    """One-off: old rows saved as free_credits / free_api / other with a $0 value
    are really free plans. Archive them when the same site already has a
    permanent_free_tier row, otherwise turn them into one."""
    if c.execute("SELECT 1 FROM meta WHERE k='zero_v1'").fetchone():
        return
    from . import dedup
    now = int(time.time())
    rows = list(c.execute("SELECT id, domain, value FROM offers WHERE status='active' "
                          "AND offer_type IN ('free_credits','free_api','other')"))
    archived = converted = 0
    for r in rows:
        if dedup.amount_of(r["value"]) != 0:
            continue
        twin = c.execute("SELECT 1 FROM offers WHERE domain=? AND status='active' AND "
                         "offer_type='permanent_free_tier' AND id<>?",
                         (r["domain"], r["id"])).fetchone()
        if twin:
            c.execute("UPDATE offers SET status='archived', verify_status='not_offer', "
                      "updated_at=? WHERE id=?", (now, r["id"]))
            archived += 1
        else:
            c.execute("UPDATE offers SET offer_type='permanent_free_tier', "
                      "value='Free plan ($0)', updated_at=? WHERE id=?", (now, r["id"]))
            converted += 1
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('zero_v1',?)", (str(now),))
    c.commit()
    print("migrate zero_v1: " + str(archived) + " archived, " + str(converted) + " converted")
