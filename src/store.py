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

OFFER_COLS = ["id","item_id","vendor","domain","offer_type","target","value","limits",
              "requires_cc","requires_edu","region","signup_url","source_url",
              "expires_at","model_class","why","score","verify_status","verify",
              "corroborations","created_at","updated_at","status","sources"]

# descriptive fields follow the best-scoring sighting; in SQLite every RHS below
# sees the OLD row, so the CASE compares against the pre-update score.
_BETTER = "excluded.score >= COALESCE(offers.score,0)"
_FOLLOW = ["item_id", "vendor", "target", "value", "limits", "signup_url",
           "source_url", "model_class", "why", "requires_cc", "requires_edu", "region"]

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
           "expires_at=COALESCE(excluded.expires_at,expires_at)"]
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
