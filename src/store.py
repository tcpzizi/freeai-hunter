import sqlite3, os, time, pathlib, hashlib

DB = os.getenv("DB_PATH", "data/state.sqlite")

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
CREATE INDEX IF NOT EXISTS ix_offers_domain ON offers(domain);
CREATE INDEX IF NOT EXISTS ix_offers_status ON offers(status);
"""

def conn():
    pathlib.Path(DB).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
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
              "corroborations","created_at","updated_at","status"]

def upsert_offer(c, row):
    now = int(time.time())
    row.setdefault("created_at", now)
    row["updated_at"] = now
    row.setdefault("status", "active")
    vals = [row.get(k) for k in OFFER_COLS]
    ph = ",".join("?" * len(OFFER_COLS))
    upd = ("score=max(COALESCE(score,0),excluded.score), "
           "verify_status=excluded.verify_status, verify=excluded.verify, "
           "corroborations=excluded.corroborations, updated_at=excluded.updated_at, "
           "status=excluded.status, expires_at=COALESCE(excluded.expires_at,expires_at)")
    c.execute("INSERT INTO offers(" + ",".join(OFFER_COLS) + ") VALUES(" + ph + ") "
              "ON CONFLICT(id) DO UPDATE SET " + upd, vals)
