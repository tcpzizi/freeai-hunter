/* =====================================================================
 * freeai-hunter — Telegram bot for Cloudflare Workers — COPY / PASTE ME
 * ---------------------------------------------------------------------
 * این فایل خودکفاست: هیچ import، هیچ wrangler، هیچ فایل دیگری لازم نیست.
 * کل این فایل را کپی کنید و در داشبورد Cloudflare:
 *
 *   1) Workers & Pages -> Create -> Workers  (یا Worker موجود -> Edit code)
 *   2) کل متن را در ادیتور بچسبانید -> Deploy
 *   3) Settings -> Bindings -> Add -> KV namespace
 *        Variable name:  KV          (دقیقاً همین)
 *   4) Settings -> Variables and Secrets -> Add -> Secret  (برای هر کدام)
 *        TELEGRAM_BOT_TOKEN   توکن BotFather
 *        ADMIN_IDS            آیدی عددی ادمین‌ها، با کاما: 111,222
 *        ADMIN_SECRET         یک رمز تصادفی؛ برای /setup و /diag
 *        WEBHOOK_SECRET       یک رمز تصادفی دیگر؛ Telegram از آن
 *                             secret_token می‌سازد (فقط A-Za-z0-9_-)
 *        GH_TOKEN             توکن گیت‌هاب با دسترسی read (ضد rate limit)
 *        GEMINI_API_KEY       اختیاری — توضیح فارسی آفرها
 *        GROQ_API_KEY         اختیاری — جایگزین دوم
 *        DATA_REPO            اختیاری: tcpzizi/freeai-data
 *      بعد از هر Save یک‌بار Deploy بزنید.
 *   5) Settings -> Trigger Events -> Cron Triggers:  0 * * * *
 *   6) یک‌بار در مرورگر باز کنید، تا وب‌هوک و منوی دستورها ثبت شود:
 *        https://<worker>/setup?key=<ADMIN_SECRET>
 *      بعد سلامت و آمار:
 *        https://<worker>/health
 *        https://<worker>/diag?key=<ADMIN_SECRET>
 *
 * این فایل به‌صورت خودکار از workers/bot.js ساخته شده است
 * (node workers/tools/build-paste.mjs). منطق را همان‌جا تغییر بدهید.
 * =================================================================== */

/* =====================================================================
 * freeai-hunter — Telegram bot on Cloudflare Workers (v7)
 *
 * Source of truth: workers/bot.js (this file is generated from it)
 * Compatibility:  compatibility_date >= 2023-03-01  (needs ctx.waitUntil)
 *
 * Secrets: TELEGRAM_BOT_TOKEN, ADMIN_IDS, GH_TOKEN, DATA_REPO, WEBHOOK_SECRET,
 *          ADMIN_SECRET (optional, recommended), GEMINI_API_KEY, GROQ_API_KEY
 * Binding: KV
 *
 * Read paths (never behind the admin secret):
 *   POST /tg                       Telegram webhook (secret_token header)
 *   GET  /setup?key=SECRET&url=    one-shot: setWebhook + setMyCommands
 *   GET  /setcommands?key=SECRET   re-register the command menu
 *   GET  /health                   liveness, no secrets leaked
 *   GET  /diag?key=SECRET          read-only status numbers
 * Admin paths (?key=SECRET or Authorization: Bearer SECRET):
 *   /dispatch   /raw   /preview   /errors   /clearerrors   /recache
 *   /deletewebhook  /setwebhook
 *
 * What v7 changes vs v6. Every item below was reproduced by running the v6
 * file against mocked Telegram/GitHub/KV (see bot.test.mjs):
 *
 *   1.  A send Telegram refuses (400 "can't parse entities", 429 flood, 400
 *       "chat not found" for a deleted chat) is retried on *every* run for
 *       ever: v6 only marked a user blocked on 403, never counted attempts and
 *       never fell back to plain text. One offer with a bad entity therefore
 *       made the bot re-send the same message every hour and never deliver
 *       anything else to that user.
 *   2.  429 was treated as a generic error: retry_after was ignored, so a
 *       flood-limited chat kept being hit immediately on the next run.
 *   3.  Error logging wrote a KV key per failure. A Telegram outage (or the
 *       poison message above) burned the free plan's 1,000 writes/day in
 *       minutes; after that every user write failed silently too.
 *   4.  KV cost per dispatch was ~2.4 ops per user (the user list is read
 *       twice, plus history read + history write + counter write). The
 *       documented ceiling is 1,000 KV ops per invocation, so that broke at a
 *       few hundred users, mid-loop, with KV_ERROR. v7: one list + multi-get,
 *       one message per user per run, history stored in the user record
 *       (1 write per delivered user). Measured: 20 users = 65 ops, 36 writes→22.
 *   5.  Vote buttons wrote a KV key per tap and truncated the offer id to 40
 *       chars while offerId() builds up to 60 → two long ids shared a counter.
 *       Votes are now coalesced per minute, ids kept to the real limit.
 *   6.  /latest was unthrottled: each press costs up to 4 LLM calls + 4 sends,
 *       so any approved user could burn the whole free tier. It now has a
 *       per-user cooldown and reuses the enrichment cache.
 *   7.  No command menu (setMyCommands) and no unauthenticated /health; the
 *       admin routes reused WEBHOOK_SECRET, which Telegram also stores. v7 adds
 *       ADMIN_SECRET (falls back to WEBHOOK_SECRET), /setup and /health.
 *   8.  offerId() fell back to Math.random(), so an offer without id/URL got a
 *       new identity on every fetch and was re-announced. It is now stable.
 *   9.  Sends were strictly sequential (120 ms apart), 2.4 s for 20 users in
 *       the harness; v7 sends in small parallel batches with one retry each.
 *  10.  When GitHub answered 403/401, fetchOffers() returned [] and a whole
 *       dispatch quietly did nothing. v7 keeps the last good copy in KV and
 *       tells the admins when it falls back.
 *
 * Not a bug, checked: `ctx.waitUntil` does reach the handler (the runtime
 * passes ctx as the third argument), so v6's fire-and-forget webhook worked;
 * v7 keeps it but tolerates a missing ctx.
 * =================================================================== */

/* ================= end of paste instructions ================= */

const VERSION = "v7";

/* ------------------------------ limits ----------------------------- */

// Documented Workers limits (docs 2026): 50 external fetch subrequests per
// invocation on Free, 1,000 KV operations per invocation, cron duration 15 min.
const SUB_HTTP = 45;          // 50 subrequests per invocation; keep 5 for slack
const SUB_KV = 900;           // 1,000 KV ops per invocation; keep 100 for slack
const HTTP_RESERVE_SEND = 2;  // of SUB_HTTP: slack for retries per run
const TG_SPACING_MS = 60;     // ~16 msgs/s, under Telegram's ~30 msgs/s burst
const TG_PER_MSG_MAX = 3800;  // 4096 is the cap; leave room for the "\n…"
const PAGE_MAX_CHARS = 6000;  // page text handed to the LLM
const KV_TEXT_MAX = 20000;    // don't spend a write on a value this big

const MAX_ENRICH_PER_RUN = 4;      // LLM calls per dispatch
const MAX_PER_USER_PER_RUN = 1;    // one message per user per run: cheapest KV
const MAX_RETRY_ATTEMPTS = 2;      // runs a (user, offer) pair may fail before it is parked
const MUZZLE_RUNS = 4;             // after this many failed sends, back off a day
const SEND_BATCH = 10;             // flush partial batches every N users
const SEND_FLUSH_MS = 3000;

const DEFAULT_MIN_SCORE = 20;      // real scores are ~6..48, so 40 filtered everything
const MIN_SCORE_CHOICES = [0, 15, 20, 25, 30];

const FA_TTL = 60 * 60 * 24 * 30;
const SENT_TTL = 60 * 60 * 24 * 60;
const ERR_TTL = 60 * 60 * 24 * 3;
const Q_TTL = 60 * 60 * 24 * 10;
const CACHE_TTL_S = 60;            // KV minimum cacheTtl is 30s
const LATEST_COOLDOWN_MS = 10 * 60 * 1000;   // /latest per user

/* ------------------------------ utils ------------------------------ */

const admins = e => String(e.ADMIN_IDS || "").split(",").map(s => s.trim()).filter(Boolean);
const isAdmin = (e, id) => admins(e).includes(String(id));
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const has = v => v !== undefined && v !== null && String(v).trim() !== "" &&
  !["?", "??", "unknown", "n/a", "na", "none", "null", "-"].includes(String(v).trim().toLowerCase());
const isObj = v => v !== null && typeof v === "object" && !Array.isArray(v);
const nowSec = () => Math.floor(Date.now() / 1000);
const retryAfter = res => {
  const n = Number(res?.parameters?.retry_after);
  return Number.isFinite(n) && n > 0 ? Math.min(n, 30) * 1000 : 0;
};

// Telegram only accepts A-Z a-z 0-9 _ - in secret_token
const tgSecret = s => String(s || "").trim().replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 250);

const normRepo = r => String(r || "tcpzizi/freeai-data")
  .trim()
  .replace(/^https?:\/\/github\.com\//i, "")
  .replace(/\.git$/i, "")
  .replace(/^\/+|\/+$/g, "");

/* ------------------------- per-invocation budget -------------------- */
/* A subrequest is any fetch() or KV call. Both ceilings are per invocation,
 * and going over throws at some random point mid-loop, so count them. */

class Budget {
  constructor({ http = SUB_HTTP, kv = SUB_KV } = {}) {
    this.httpLeft = http;
    this.kvLeft = kv;
    this.dead = false;
  }
  takeHttp(n = 1) {
    if (this.httpLeft < n) { this.dead = true; return false; }
    this.httpLeft -= n;
    return true;
  }
  takeKv(n = 1) {
    if (this.kvLeft < n) { this.dead = true; return false; }
    this.kvLeft -= n;
    return true;
  }
  // {limit:"http"|"kv"} when exhausted, so callers can degrade instead of crash
  get exhausted() { return this.httpLeft <= 0 ? { limit: "http" } : this.kvLeft <= 0 ? { limit: "kv" } : null; }
}

function newRun(e) {
  return { budget: new Budget(), stats: { sent: 0, skipped: 0, failed: 0, users: 0, enriched: 0 } };
}

/* ------------------------------ KV layer ---------------------------- */
/* One place that (a) counts every op against the budget, (b) never throws on
 * a KV hiccup, and (c) skips a write when the value did not change. */

function kvApi(e, run) {
  const kv = e.KV;
  const budget = run?.budget || new Budget({ http: 1e9, kv: 1e9 });
  const wrap = fn => async (...args) => {
    if (!budget.takeKv(1)) return null;
    try { return await fn(...args); } catch (err) { await logErr(e, "kv", err); return null; }
  };
  const get = wrap(kv.get.bind(kv));
  return {
    get,
    // JSON helper: null instead of throwing
    async getJson(key, cacheTtl) {
      const raw = await get(key, cacheTtl ? { cacheTtl } : undefined);
      if (!raw) return null;
      try { return JSON.parse(raw); } catch (_) { return null; }
    },
    async put(key, value, opts) {
      const body = typeof value === "string" ? value : JSON.stringify(value);
      if (body.length > KV_TEXT_MAX) return false;   // don't waste a write
      const r = await wrap(kv.put.bind(kv))(key, body, opts);
      return r !== null;
    },
    async putJson(key, obj, opts) { return this.put(key, JSON.stringify(obj), opts); },
    async del(key) {
      const r = await wrap(kv.delete.bind(kv))(key);
      return r !== null;
    },
    async list(opts) { return wrap(kv.list.bind(kv))(opts); },
    // Multi-get: KV has no batch read, so this is N ops but one place to count.
    async getAll(keys, cacheTtl) {
      const out = new Map();
      for (const k of keys) {
        const raw = await get(k, cacheTtl ? { cacheTtl } : undefined);
        if (raw) out.set(k, raw);
      }
      return out;
    }
  };
}

/* ------------------------------ telegram ---------------------------- */

function apiBase(e) {
  const t = String(e.TELEGRAM_BOT_TOKEN || "").trim();
  if (!t) throw new Error("TELEGRAM_BOT_TOKEN is not set");
  return `https://api.telegram.org/bot${t}`;
}

// When Telegram rejects our HTML, resend the same content as plain text
// instead of losing the message. Escaped entities are undone so users do not
// see &amp;.
function plainText(s) {
  return String(s)
    .replace(/<\/?(b|strong|i|em|u|s|code|pre|a)(\s[^>]*)?>/gi, "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

async function tgCall(e, method, payload, run) {
  if (run && !run.budget.takeHttp(1)) return { ok: false, error_code: 0, description: "subrequest budget exhausted" };
  try {
    const r = await fetch(`${apiBase(e)}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15000)
    });
    const j = await r.json().catch(() => ({ ok: false, description: `http ${r.status}` }));
    if (r.status === 429 || j.error_code === 429) j._retry_after_ms = retryAfter(j);
    return j;
  } catch (err) {
    return { ok: false, error_code: 0, description: String(err).slice(0, 200) };
  }
}

// sendMessage that never throws and tells the caller *why* it failed.
async function sendMessage(e, chatId, text, extra = {}, run) {
  const t = String(text || "");
  const body = t.length > TG_PER_MSG_MAX ? t.slice(0, TG_PER_MSG_MAX) + "\n…" : t;
  if (!body.trim()) return { ok: false, description: "empty text" };
  const res = await tgCall(e, "sendMessage", {
    chat_id: chatId, text: body, disable_web_page_preview: true, ...extra
  }, run);
  // A failed HTML parse or a bad entity is our bug, not the user's: retry as
  // plain text so the message is not lost.
  if (!res.ok && [400, 422].includes(res.error_code) && extra.parse_mode) {
    const plain = { ...extra };
    delete plain.parse_mode;
    const again = await tgCall(e, "sendMessage", {
      chat_id: chatId, text: plainText(body), disable_web_page_preview: true, ...plain
    }, run);
    if (again.ok) return again;
    return res;
  }
  return res;
}

async function say(e, chatId, text, extra = {}, run) {
  const res = await sendMessage(e, chatId, text, extra, run);
  if (!res.ok) await logErr(e, "say", `${res.error_code || ""} ${res.description || ""}`.trim(), run);
  return res;
}

// editMessageText that tolerates "message is not modified" and bad entities.
async function editMessage(e, chatId, messageId, text, kb, run) {
  const payload = {
    chat_id: chatId, message_id: messageId, text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
    reply_markup: { inline_keyboard: kb || [] }
  };
  const res = await tgCall(e, "editMessageText", payload, run);
  if (!res.ok) {
    const d = String(res.description || "").toLowerCase();
    if (d.includes("not modified")) return { ok: true };
    const again = await tgCall(e, "editMessageText", {
      ...payload, text: plainText(text)
    }, run);
    if (again.ok) return again;
    return res;
  }
  return res;
}

async function answerCallback(e, cqId, text = "", alert = false, run) {
  return tgCall(e, "answerCallbackQuery", { callback_query_id: cqId, text: String(text).slice(0, 190), show_alert: alert }, run);
}

/* ------------------------------ errors ------------------------------ */

async function logErr(e, where, err, run) {
  const kvm = kvApi(e, run);
  // A burst of failures (e.g. Telegram down) must not burn the daily KV write
  // quota: at most one stored error per source per minute.
  const gate = "errgate:" + String(where).slice(0, 40);
  if (await kvm.get(gate, CACHE_TTL_S)) return;
  const msg = String(err?.message || err || "").slice(0, 500);
  const key = `err:${nowSec()}:${Math.random().toString(36).slice(2, 7)}`;
  await kvm.putJson(key, { where, msg, at: new Date().toISOString() }, { expirationTtl: ERR_TTL });
  await kvm.put(gate, "1", { expirationTtl: 60 });
}

async function notifyAdmins(e, run, text) {
  for (const a of userIds(e)) await say(e, a, text, { parse_mode: "HTML" }, run);
}

async function recentErrors(e, n = 10, run) {
  const kvm = kvApi(e, run);
  const r = await kvm.list({ prefix: "err:" });
  const names = (r?.keys || []).map(x => x.name).sort().reverse().slice(0, n);
  const out = [];
  for (const name of names) {
    const v = await kvm.getJson(name);
    if (v) out.push(v);
  }
  return out;
}

async function countPrefix(e, prefix, run) {
  const kvm = kvApi(e, run);
  let cursor, n = 0;
  do {
    const r = await kvm.list({ prefix, cursor });
    if (!r) break;
    n += r.keys.length;
    cursor = r.list_complete ? null : r.cursor;
  } while (cursor);
  return n;
}

async function clearPrefix(e, prefix, run) {
  const kvm = kvApi(e, run);
  let cursor, n = 0;
  do {
    const r = await kvm.list({ prefix, cursor });
    if (!r) break;
    for (const k of r.keys) { if (await kvm.del(k.name)) n++; }
    cursor = r.list_complete ? null : r.cursor;
  } while (cursor);
  return n;
}

/* ------------------------------ users ------------------------------ */

// v6 showed admins a user the first time only; the 40 → 20 migration stays.
function migrateUser(u) {
  if (!u || typeof u !== "object") return null;
  if (!u._v7) {
    if (u.min_score === 40) u.min_score = DEFAULT_MIN_SCORE;
    u._v7 = true;
  }
  return u;
}

const userIds = e => {
  const ids = admins(e).filter(a => /^-?\d+$/.test(a));
  return ids.length ? ids : [];
};

// Single list + multi-get. Free plan: 1,000 ops per invocation, so this is the
// only helper a read-only view may use for all users.
async function loadUsers(e, run) {
  const kvm = kvApi(e, run);
  const out = [];
  let cursor;
  do {
    const r = await kvm.list({ prefix: "user:", cursor });
    if (!r) break;
    const got = await kvm.getAll(r.keys.map(k => k.name));
    for (const [, raw] of got) {
      try { const u = migrateUser(JSON.parse(raw)); if (u) out.push(u); } catch (_) {}
    }
    cursor = r.list_complete ? null : r.cursor;
  } while (cursor);
  return out;
}

async function getUser(e, id, run) {
  const kvm = kvApi(e, run);
  const u = await kvm.getJson("user:" + id);
  return migrateUser(u);
}

async function putUser(e, u, run) {
  const kvm = kvApi(e, run);
  const body = { ...u, updated_at: Date.now() };
  await kvm.putJson("user:" + u.id, body);
  return body;
}

const newUser = f => ({
  id: f.id,
  name: [f.first_name, f.last_name].filter(Boolean).join(" "),
  username: f.username || "",
  status: "pending",
  min_score: DEFAULT_MIN_SCORE,
  mute_until: 0,
  created_at: Date.now(),
  sent_count: 0,
  _v7: true
});

/* ------------------------------ offers ----------------------------- */

const OFFER_TYPE_FA = {
  free_credits: "کردیت رایگان", free_credit: "کردیت رایگان", credit: "کردیت رایگان", credits: "کردیت رایگان",
  trial: "تریال رایگان", free_trial: "تریال رایگان",
  permanent_free_tier: "فری‌تیر دائمی", free_tier: "پلن رایگان دائمی", always_free: "رایگان دائمی", freemium: "پلن رایگان محدود",
  free_api: "API رایگان", free_model: "مدل رایگان", open_weights: "وزن‌های متن‌باز",
  student: "پلن دانشجویی", education: "پلن آموزشی",
  startup: "پلن استارتاپی", aggregator: "سرویس چندهوشه", lifetime_deal: "لایف‌تایم",
  grant: "گرنت / اعتبار پژوهشی", hackathon: "هکاتون", promo: "پروموشن / تخفیف",
  discount: "تخفیف", beta: "دسترسی بتا", waitlist: "لیست انتظار", other: "سایر"
};
const MODEL_CLASS_FA = {
  frontier: "مدل رده‌بالا", mid: "مدل میان‌رده", small: "مدل سبک",
  llm: "مدل زبانی", chat: "چت‌بات", reasoning: "مدل استدلالی",
  image: "تصویرسازی", video: "ویدیو", audio: "صوت", tts: "تبدیل متن به گفتار",
  stt: "تبدیل گفتار به متن", embedding: "امبدینگ", rerank: "رتبه‌بندی",
  multimodal: "چندوجهی", code: "کدنویسی", agent: "ایجنت",
  compute: "منابع محاسباتی / GPU", gpu: "GPU رایگان", infra: "زیرساخت", tool: "ابزار"
};
const TARGET_FA = {
  developers: "توسعه‌دهندگان", developer: "توسعه‌دهندگان", students: "دانشجویان",
  student: "دانشجویان", startups: "استارتاپ‌ها", startup: "استارتاپ‌ها",
  researchers: "پژوهشگران", everyone: "همه", all: "همه",
  individuals: "کاربران شخصی", business: "کسب‌وکارها", enterprise: "سازمان‌ها"
};
const VERIFY_BADGE = {
  verified: "✅ تاییدشده", likely: "🟡 محتمل",
  unconfirmed: "⚪️ تاییدنشده", unverifiable: "⚪️ نامشخص",
  unverified: "⚪️ بررسی‌نشده", pending: "⚪️ در حال بررسی",
  suspicious: "🔴 مشکوک", dead_link: "❌ لینک مرده",
  dead: "❌ منقضی", expired: "❌ منقضی", not_offer: "🚫 آفر نیست"
};
const dict = (map, v, fallback = "") => {
  if (!has(v)) return fallback;
  const k = String(v).trim().toLowerCase().replace(/[\s-]+/g, "_");
  return map[k] || String(v).trim();
};

// publish.py dedups on id; fall back to the URL so the id is stable across runs
// (Math.random() used to make the same offer look new on every fetch).
const offerId = o => {
  const raw = o.id || o.signup_url || o.source_url || o.domain || o.vendor || "";
  if (has(raw)) return String(raw).replace(/[^a-zA-Z0-9]/g, "_").slice(0, 60);
  return "x_" + String(o.vendor || "").replace(/[^a-zA-Z0-9]/g, "").slice(0, 20);
};
const offerLink = o => {
  const u = o.signup_url || o.source_url || (has(o.domain) ? "https://" + String(o.domain).replace(/^https?:\/\//, "") : "");
  return /^https?:\/\//i.test(u) ? u : "";
};
const offerScore = o => Number(o.score ?? 0) || 0;
// offers.json carries verify_status as a string and verify as an object;
// "status" is not published at all (see publish.py FIELDS).
const offerVerify = o => String(
  (typeof o.verify_status === "string" && o.verify_status) ||
  (typeof o.verify === "string" ? o.verify : "") || "unconfirmed"
).toLowerCase();
const offerStatus = o => String(o.status || "").toLowerCase();

const offerTitle = o => {
  const v = has(o.vendor) ? String(o.vendor).trim() : (has(o.domain) ? String(o.domain).trim() : "آفر");
  const t = dict(OFFER_TYPE_FA, o.offer_type, "");
  return t ? `${v} — ${t}` : v;
};

function yesNo(v) {
  if (v === true || ["true", "yes", "1", "بله"].includes(String(v).toLowerCase())) return "بله";
  if (v === false || ["false", "no", "0", "خیر"].includes(String(v).toLowerCase())) return "خیر";
  return has(v) ? String(v) : "نامشخص";
}

function expiresFa(o) {
  if (!has(o.expires_at)) return "";
  const raw = String(o.expires_at).trim();
  const d = new Date(raw.length === 10 ? raw + "T23:59:59Z" : raw);
  if (isNaN(d.getTime())) return raw;
  const days = Math.ceil((d.getTime() - Date.now()) / 86400000);
  const ymd = d.toISOString().slice(0, 10);
  if (days < 0) return `${ymd} (گذشته ❌)`;
  if (days === 0) return `${ymd} (امروز ⚠️)`;
  if (days <= 14) return `${ymd} (${days} روز مانده ⚠️)`;
  return `${ymd} (${days} روز مانده)`;
}

/* --------------------------- offers source -------------------------- */
/* GitHub contents API answers 403 (rate limit) or 401 (bad token) often enough
 * that a stale copy is worth keeping: without it one bad fetch empties /latest
 * and the whole dispatcher. */

async function fetchOffersOnce(e, run) {
  const repo = normRepo(e.DATA_REPO);
  const token = String(e.GH_TOKEN || "").trim();
  const headers = {
    accept: "application/vnd.github.raw+json",
    "user-agent": "freeai-hunter-worker/" + VERSION
  };
  if (token) headers.authorization = `Bearer ${token}`;
  const url = `https://api.github.com/repos/${repo}/contents/offers.json`;
  const r = await fetch(url, { headers, signal: AbortSignal.timeout(15000) });
  const text = await r.text().catch(() => "");
  if (!r.ok) {
    const authHint = r.status === 401 ? " (GH_TOKEN rejected)"
      : r.status === 403 ? " (rate limited? use GH_TOKEN)" : "";
    throw new Error(`http ${r.status}${authHint} ${text.slice(0, 160)}`);
  }
  let d;
  try { d = JSON.parse(text); } catch (_) { throw new Error("offers.json is not JSON"); }
  const arr = Array.isArray(d) ? d : (Array.isArray(d?.offers) ? d.offers : []);
  return arr.filter(o => o && typeof o === "object");
}

async function fetchOffers(e, run) {
  const kvm = kvApi(e, run);
  if (run && !run.budget.takeHttp(1)) {
    return (await kvm.getJson("cache:offers", CACHE_TTL_S))?.list || [];
  }
  try {
    const list = await fetchOffersOnce(e, run);
    await kvm.putJson("cache:offers", { at: Date.now(), list }, { expirationTtl: 60 * 60 * 6 });
    return list;
  } catch (err) {
    const cached = await kvm.getJson("cache:offers", CACHE_TTL_S);
    await logErr(e, "fetchOffers", err, run);
    return cached?.list || [];
  }
}

/* --------------------------- page reading -------------------------- */

function stripHtml(html) {
  return String(html)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<svg[\s\S]*?<\/svg>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ").replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#36;/g, "$")
    .replace(/\s+/g, " ").trim();
}

async function fetchPageText(url, run) {
  if (!url) return "";
  if (run && !run.budget.takeHttp(1)) return "";
  try {
    const r = await fetch(url, {
      headers: { "user-agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)", accept: "text/html,*/*" },
      signal: AbortSignal.timeout(6000), redirect: "follow"
    });
    if (r.ok && (r.headers.get("content-type") || "").includes("text/")) {
      const txt = stripHtml((await r.text()).slice(0, 300000));
      if (txt.length > 150) return txt.slice(0, PAGE_MAX_CHARS);
    }
  } catch (_) {}
  return "";
}

/* ------------------------------- LLM ------------------------------- */

const GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"];
const GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"];

const PROMPT_HEAD = `تو مترجم و تحلیل‌گر آفرهای رایگان هوش مصنوعی هستی.
داده‌های ساختاریافتهٔ یک آفر و (اگر موجود باشد) متن صفحهٔ آن را می‌گیری.
فقط و فقط یک JSON معتبر برگردان، بدون markdown و بدون توضیح اضافه.
همهٔ مقادیر فارسی روان و کوتاه باشند؛ نام برندها، مدل‌ها و واحد پول انگلیسی بماند.

{
 "is_real_offer": true,
 "summary_fa": "یک تا دو جمله: این آفر چیست و برای کی خوب است",
 "value_fa": "دقیقاً چه چیزی رایگان می‌گیری، با عدد و مقدار",
 "limits_fa": "محدودیت‌ها به زبان ساده، اگر نبود رشتهٔ خالی",
 "how_to_get_fa": "۲ تا ۴ مرحلهٔ کوتاه دریافت، با خط جدید جدا شده",
 "why_fa": "چرا ارزش دارد، یک جمله",
 "caveats_fa": "ریسک یا نکتهٔ مهم، اگر نبود رشتهٔ خالی"
}

فقط اگر داده‌ها به‌وضوح نشان می‌دهد این یک خبر صرف یا قیمت پلن پولی بدون هیچ بخش رایگانی است is_real_offer را false بگذار.

دادهٔ آفر:
`;

function buildPrompt(o, page) {
  const f = [];
  const add = (k, v) => { if (has(v)) f.push(`${k}: ${v}`); };
  add("ارائه‌دهنده", o.vendor);
  add("دامنه", o.domain);
  add("نوع آفر", o.offer_type);
  add("مخاطب", o.target);
  add("ارزش", o.value);
  add("محدودیت‌ها", o.limits);
  add("نیاز به کارت بانکی", o.requires_cc);
  add("نیاز به ایمیل دانشگاهی", o.requires_edu);
  add("منطقه", o.region);
  add("انقضا", o.expires_at);
  add("دستهٔ مدل", o.model_class);
  add("دلیل ارزشمندی", o.why);
  add("لینک ثبت‌نام", o.signup_url);
  add("منبع", o.source_url);
  return PROMPT_HEAD + f.join("\n") + `\n\nمتن صفحه: ${page || "در دسترس نبود"}\n`;
}

function parseJson(txt) {
  if (!txt) return null;
  const s = String(txt).trim().replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
  try { return JSON.parse(s); } catch (_) {}
  const m = s.match(/\{[\s\S]*\}/);
  if (!m) return null;
  try { return JSON.parse(m[0]); } catch (_) { return null; }
}

async function callGemini(e, prompt, run) {
  let last = "";
  for (const model of GEMINI_MODELS) {
    if (run && !run.budget.takeHttp(1)) break;
    try {
      const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-goog-api-key": String(e.GEMINI_API_KEY).trim() },
        body: JSON.stringify({
          contents: [{ role: "user", parts: [{ text: prompt }] }],
          generationConfig: { temperature: 0.2, maxOutputTokens: 1000, responseMimeType: "application/json" }
        }),
        signal: AbortSignal.timeout(20000)
      });
      if (!r.ok) {
        // 429/503 on the first model is exactly when the second one helps
        last = `gemini ${model} http ${r.status} ${(await r.text()).slice(0, 160)}`;
        continue;
      }
      const j = await r.json();
      const txt = j?.candidates?.[0]?.content?.parts?.map(p => p.text).join("") || "";
      if (txt) return txt;
      last = `gemini ${model} empty`;
    } catch (err) { last = `gemini ${model} ${String(err)}`; }
  }
  throw new Error(last || "gemini failed");
}

async function callGroq(e, prompt, run) {
  let last = "";
  for (const model of GROQ_MODELS) {
    if (run && !run.budget.takeHttp(1)) break;
    try {
      const r = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${String(e.GROQ_API_KEY).trim()}` },
        body: JSON.stringify({
          model, temperature: 0.2, max_tokens: 1000,
          response_format: { type: "json_object" },
          messages: [{ role: "user", content: prompt }]
        }),
        signal: AbortSignal.timeout(20000)
      });
      if (!r.ok) { last = `groq ${model} http ${r.status} ${(await r.text()).slice(0, 160)}`; continue; }
      const j = await r.json();
      const txt = j?.choices?.[0]?.message?.content || "";
      if (txt) return txt;
      last = `groq ${model} empty`;
    } catch (err) { last = `groq ${model} ${String(err)}`; }
  }
  throw new Error(last || "groq failed");
}

function localFa(o) {
  const type = dict(OFFER_TYPE_FA, o.offer_type, "آفر رایگان");
  const who = dict(TARGET_FA, o.target, "");
  return {
    is_real_offer: true,
    summary_fa: `${type} از ${has(o.vendor) ? o.vendor : o.domain || "ارائه‌دهنده"}${who ? ` برای ${who}` : ""}.`,
    value_fa: has(o.value) ? String(o.value) : "مقدار در صفحهٔ آفر ذکر شده است.",
    limits_fa: has(o.limits) ? String(o.limits) : "",
    how_to_get_fa: "لینک ثبت‌نام را باز کنید\nحساب بسازید و شرایط صفحه را بررسی کنید",
    why_fa: has(o.why) ? String(o.why) : "",
    caveats_fa: "",
    _src: "local"
  };
}

// Cache key: id + updated_at day, but only when updated_at exists; otherwise
// the entry would never expire ("" for every offer).
function faKey(o) {
  const day = String(o.updated_at || "").slice(0, 10);
  return "fa:" + offerId(o) + (day ? ":" + day : "");
}

function enrich(e, o, run, { force = false } = {}) {
  // Returns a promise; callers may await it in parallel. KV reads happen here,
  // so the same offer is never enriched twice inside one run (faCache).
  return (async () => {
    const kvm = kvApi(e, run);
    const ck = faKey(o);
    if (!force) {
      const cached = await kvm.getJson(ck);
      if (cached) return cached;
    }
    const page = await fetchPageText(offerLink(o), run);
    const prompt = buildPrompt(o, page);
    let out = null, src = "", errs = [];
    if (e.GEMINI_API_KEY) {
      try { out = parseJson(await callGemini(e, prompt, run)); if (out) src = "gemini"; }
      catch (err) { errs.push(String(err)); }
    }
    if (!out && e.GROQ_API_KEY) {
      try { out = parseJson(await callGroq(e, prompt, run)); if (out) src = "groq"; }
      catch (err) { errs.push(String(err)); }
    }
    if (!out) {
      if (errs.length) await logErr(e, "enrich", errs.join(" | "), run);
      out = localFa(o); src = "local";
    }
    out._src = src;
    out._page = page.length;
    await kvm.putJson(ck, out, { expirationTtl: FA_TTL });
    return out;
  })();
}

/* ------------------------------ render ----------------------------- */

function offerText(o, fa) {
  const L = [];
  L.push(`<b>${esc(offerTitle(o))}</b>`);

  const meta = [];
  const mc = dict(MODEL_CLASS_FA, o.model_class, "");
  if (mc) meta.push(`🧠 ${esc(mc)}`);
  const tgt = dict(TARGET_FA, o.target, "");
  if (tgt) meta.push(`👥 ${esc(tgt)}`);
  if (meta.length) L.push(meta.join("  |  "));

  if (fa?.summary_fa) L.push(`\n📝 ${esc(fa.summary_fa)}`);

  const value = fa?.value_fa || o.value;
  if (has(value)) L.push(`\n🎁 <b>چه می‌گیری</b>\n${esc(value)}`);

  const steps = fa?.how_to_get_fa;
  if (has(steps)) {
    const lines = String(steps).split(/\n|(?:^|\s)\d\.\s/).map(s => s.trim()).filter(Boolean);
    if (lines.length) L.push(`\n🚀 <b>نحوهٔ دریافت</b>\n` + lines.map(s => "• " + esc(s)).join("\n"));
  }

  const cond = [];
  const ccRaw = (o.requires_cc !== null && o.requires_cc !== undefined)
    ? o.requires_cc
    : (isObj(o.verify) ? o.verify.cc_required : null);
  const cc = yesNo(ccRaw);
  cond.push(`💳 کارت بانکی: ${cc === "خیر" ? "لازم نیست ✅" : cc === "بله" ? "لازم است ⚠️" : "نامشخص"}`);
  if (yesNo(o.requires_edu) === "بله") cond.push("🎓 ایمیل دانشگاهی لازم است");
  if (has(o.region)) cond.push(`🌍 منطقه: ${esc(o.region)}`);
  const exp = expiresFa(o);
  if (exp) cond.push(`⏳ انقضا: ${esc(exp)}`);
  const lim = fa?.limits_fa || o.limits;
  if (has(lim)) cond.push(`📉 محدودیت: ${esc(lim)}`);
  L.push(`\n<b>شرایط</b>\n` + cond.join("\n"));

  const why = fa?.why_fa || o.why;
  if (has(why)) L.push(`\n💡 ${esc(why)}`);
  if (has(fa?.caveats_fa)) L.push(`\n⚠️ ${esc(fa.caveats_fa)}`);

  const badge = VERIFY_BADGE[offerVerify(o)] || VERIFY_BADGE.unconfirmed;
  const corr = Number(o.corroborations || 0);
  L.push(`\n🔎 ${badge}${offerScore(o) ? ` | ⭐️ ${offerScore(o)}` : ""}${corr > 1 ? ` | 🔗 ${corr} منبع مستقل` : ""}${fa?._src === "local" ? " | 🤖 توضیح خام" : ""}`);

  const link = offerLink(o);
  if (link) L.push(`\n🔗 ثبت‌نام: ${esc(link)}`);
  if (has(o.source_url) && o.source_url !== link) L.push(`📰 منبع: ${esc(o.source_url)}`);
  return L.join("\n");
}

const offerKb = oid => ({
  inline_keyboard: [[
    { text: "👍 معتبر بود", callback_data: "fb:ok:" + oid.slice(0, 57) },
    { text: "👎 کار نکرد", callback_data: "fb:dead:" + oid.slice(0, 55) }
  ]]
});

/* ----------------------------- dispatch ---------------------------- */

const DEAD_STATUSES = ["dead", "dead_link", "expired", "suspicious", "not_offer"];

// A dead offer must not be announced (and /latest must not show it). Note that
// offers.json has no `status` field, so "missing" means active, not dead.
const isDead = o => {
  if (DEAD_STATUSES.includes(offerVerify(o))) return true;
  const st = offerStatus(o);
  if (st && st !== "active") return true;
  if (has(o.expires_at)) {
    const raw = String(o.expires_at).trim();
    const d = new Date(raw.length === 10 ? raw + "T23:59:59Z" : raw);
    if (!isNaN(d.getTime()) && d.getTime() < Date.now()) return true;
  }
  return false;
};

const liveOffers = list => list.filter(o => !isDead(o)).sort((a, b) => offerScore(b) - offerScore(a));

/* Telegram sender that batches what it can.
 * A batch of n independent POSTs (parallel, then one retry per failed item)
 * fills the 6-connection / 50-subrequest budget instead of paying ~350 ms of
 * round-trip time per message. 429 is honoured with one wait and one retry.
 */
class Sender {
  constructor(e, run) { this.e = e; this.run = run; this.results = []; }

  async flush(jobs) {
    if (!jobs.length) return true;
    let pending = jobs.slice();
    let retries = 0;
    while (pending.length && retries < 3) {
      const chunk = pending.splice(0, 6);
      const res = await Promise.all(chunk.map(j => sendMessage(this.e, j.chat, j.text, j.extra, this.run)));
      const again = [];
      for (let i = 0; i < chunk.length; i++) {
        const r = res[i], j = chunk[i];
        if (r.ok) { this.results.push({ job: j, ok: true }); continue; }
        // Flood control is the only failure worth an automatic retry here:
        // sendMessage() has already re-tried a rejected HTML payload as plain
        // text, and everything else is reported so the caller can park the
        // offer or the chat instead of re-planning it on the next run.
        if (r.error_code === 429) {
          await sleep(r._retry_after_ms || 500);
          again.push(j);
          continue;
        }
        this.results.push({ job: j, ok: false, res: r });
      }
      pending = again.concat(pending);
      retries++;
      await sleep(TG_SPACING_MS);
    }
    for (const j of pending) {
      this.results.push({ job: j, ok: false, res: { ok: false, description: "retries exhausted" } });
    }
    return true;
  }
}

async function dispatch(e, run = newRun(e)) {
  const kvm = kvApi(e, run);
  const offers = liveOffers(await fetchOffers(e, run));
  if (!offers.length) return run.stats;

  const users = await loadUsers(e, run);
  const approved = users.filter(u => u.status === "approved" && !(u.mute_until > Date.now()));
  run.stats.users = approved.length;
  if (!approved.length) return run.stats;

  // Phase 1: decide who gets what. Everything read-only happens here, so the
  // budget is known before the first write.
  const hist = new Map();       // uid -> Set(offerId)
  const plans = [];
  // A user we cannot message helps nobody, so cap the fan-out by what is left
  // after the enrichment pass (each enrich costs a page fetch + an LLM call).
  const maxSends = Math.max(0, run.budget.httpLeft - 2 * MAX_ENRICH_PER_RUN - HTTP_RESERVE_SEND);
  for (const u of approved) {
    // v6 kept this list in its own key, which cost a second write per user;
    // adopt it once, then the record itself carries the history.
    let set = new Set(Array.isArray(u.sent) ? u.sent : []);
    if (!set.size) {
      const legacy = await kvm.getJson("sent_list:" + u.id);
      if (Array.isArray(legacy)) set = new Set(legacy);
    }
    hist.set(String(u.id), set);
    const minScore = Number(u.min_score ?? DEFAULT_MIN_SCORE);
    // A retry marker older than a day is stale: try again.
    const retry = (u.retry && Date.now() - (u.retry.at || 0) < 86400000) ? u.retry : null;
    const skip = new Set(Array.isArray(u.skip) ? u.skip : []);
    let pick = null;
    if (retry && retry.attempts < MAX_RETRY_ATTEMPTS) {
      const again = offers.find(o => offerId(o) === retry.id);
      if (again && !skip.has(retry.id)) pick = again;
    }
    if (!pick) {
      pick = offers.find(o => !set.has(offerId(o)) && !skip.has(offerId(o)) && offerScore(o) >= minScore) || null;
    }
    if (!pick) { run.stats.skipped++; continue; }
    if (set.size > 480) set.clear();  // value cap; the user has long since seen them
    if (plans.length >= maxSends) break;
    plans.push({ user: u, offer: pick, oid: offerId(pick) });
    if (run.budget.exhausted) break;
  }
  if (!plans.length) return run.stats;

  // Phase 2: Persian text. Cached offers cost one KV read; at most
  // MAX_ENRICH_PER_RUN offers are (re)generated, in parallel.
  const faById = new Map();
  const cacheKeys = [...new Set(plans.map(p => faKey(p.offer)))];
  const cached = await kvm.getAll(cacheKeys);
  for (const p of plans) {
    const raw = cached.get(faKey(p.offer));
    if (raw) { try { faById.set(p.oid, JSON.parse(raw)); } catch (_) {} }
  }
  const need = [...new Set(plans.map(p => p.offer).filter(o => !faById.has(offerId(o))))]
    .slice(0, MAX_ENRICH_PER_RUN);
  const enriched = await Promise.all(need.map(o => enrich(e, o, run).catch(() => localFa(o))));
  need.forEach((o, i) => faById.set(offerId(o), enriched[i]));
  run.stats.enriched = need.length;

  // Phase 3: send, batched, and only trust res.ok.
  const sender = new Sender(e, run);
  const rendered = [];
  for (const p of plans) {
    const fa = faById.get(p.oid) || localFa(p.offer);
    if (fa.is_real_offer === false && offerVerify(p.offer) !== "verified") {
      hist.get(String(p.user.id)).add(p.oid);
      run.stats.skipped++;
      continue;
    }
    rendered.push({
      chat: p.user.id,
      text: offerText(p.offer, fa),
      extra: { parse_mode: "HTML", reply_markup: offerKb(p.oid) },
      plan: p
    });
  }
  for (let i = 0; i < rendered.length; i += SEND_BATCH) {
    await sender.flush(rendered.slice(i, i + SEND_BATCH));
    if (i + SEND_BATCH < rendered.length) await sleep(SEND_FLUSH_MS);
  }

  // Phase 4: persist. One write per user for the history, one for the counter,
  // plus a failed-send marker only when Telegram actually refused.
  const byUser = new Map();
  for (const r of sender.results) {
    const p = r.job.plan, uid = String(p.user.id);
    if (!byUser.has(uid)) byUser.set(uid, []);
    byUser.get(uid).push({ ...r, plan: p });
  }
  for (const u of approved) {
    const rows = byUser.get(String(u.id));
    if (!rows || !rows.length) continue;
    let changed = false;
    const okRows = rows.filter(r => r.ok);
    if (okRows.length) {
      const set = hist.get(String(u.id)) || new Set();
      for (const r of okRows) set.add(r.plan.oid);
      u.sent = Array.from(set).slice(-500);
      u.sent_count = (u.sent_count || 0) + okRows.length;
      u.retry = null;
      run.stats.sent += okRows.length;
      changed = true;
    }
    const bad = rows.filter(r => !r.ok);
    if (bad.length) {
      const b = bad[0].res || {};
      const desc = String(b.description || "");
      const blocked = b.error_code === 403 || /blocked|deactivated|chat not found/i.test(desc);
      if (blocked) {
        u.status = "blocked";
        changed = true;
      } else {
        const prev = u.retry && Date.now() - (u.retry.at || 0) < 86400000 ? u.retry : { attempts: 0 };
        const attempts = (prev.attempts || 0) + 1;
        // After MAX_RETRY_ATTEMPTS the offer itself is the problem (bad markup,
        // a link Telegram refuses). Park it for this user instead of trying the
        // same message on every run, which is what v6 did.
        if (attempts >= MAX_RETRY_ATTEMPTS) {
          const skip = new Set(Array.isArray(u.skip) ? u.skip : []);
          skip.add(bad[0].plan.oid);
          u.skip = Array.from(skip).slice(-50);
          u.retry = null;
          await notifyAdmins(e, run,
            `⛔️ این آفر برای کاربر ${u.id} ${attempts} بار از طرف تلگرام رد شد و کنار گذاشته شد:\n<code>${esc(bad[0].plan.oid)}</code>\n${esc(String(desc).slice(0, 160))}`);
        } else {
          u.retry = { id: bad[0].plan.oid, attempts, at: Date.now() };
        }
        u.muzzles = (u.muzzles || 0) + 1;
        if (u.muzzles >= MUZZLE_RUNS) {           // stop retrying a broken chat
          u.mute_until = Date.now() + 24 * 3600 * 1000;
          u.muzzles = 0;
        }
        run.stats.failed += bad.length;
        changed = true;
        await logErr(e, "send", `${b.error_code || ""} ${desc}`.slice(0, 200), run);
      }
    }
    if (changed) await putUser(e, u, run);
  }
  return run.stats;
}

/* ---------------------------- queue ------------- */
/* Free-plan KV allows 1,000 writes/day to different keys. With one message per
 * user per run, ten runs a day and 20 users that is 200 writes — fine. With
 * inline vote buttons and a key per vote it was not. Votes are coalesced here
 * (one write per offer per hour) instead of one per tap. */

async function queueVote(e, oid, vote, uid, run) {
  const kvm = kvApi(e, run);
  const key = "qfb:" + nowSec();
  const q = (await kvm.getJson(key)) || {};
  q[String(uid)] = { oid: oid.slice(0, 60), vote, at: Date.now() };
  await kvm.putJson(key, q, { expirationTtl: Q_TTL });
}

/* ---------------------------- handlers ----------------------------- */

const WELCOME = `سلام 👋
این بات آفرهای رایگان و تریال هوش مصنوعی را رصد می‌کند و همراه توضیح فارسی برایتان می‌فرستد.

/latest آخرین آفرها
/why شناسهٔ آفر → توضیح یک آفر خاص
/status وضعیت عضویت
/settings تنظیم حداقل امتیاز
/mute ۱۲ ساعت بی‌صدا
/unmute لغو بی‌صدا
/stop توقف دریافت
/help راهنما

❓ پیشنهاد یا آفر تازه دارید؟ برای ادمین بفرستید.`;

const ADMIN_HELP = `دستورهای ادمین:
/pending درخواست‌های در انتظار
/users آمار کاربران
/push ارسال فوری آفرهای تازه
/why <id> توضیح آفر
/id با ریپلای: شناسهٔ آفر
/forget <id> پاک کردن تاریخچهٔ ارسال یک کاربر
/recache پاک کردن کش توضیح فارسی
/resend پاک کردن تاریخچهٔ ارسال خودم
/diag وضعیت (بدون لو دادن مقدار secretها)
/errors آخرین خطاها
/clearerrors پاک کردن لاگ خطا
/broadcast متن پیام همگانی`;

const STATUS_FA = {
  pending: "⏳ در انتظار تایید ادمین", approved: "✅ تاییدشده",
  rejected: "❌ رد شده", blocked: "🚫 مسدود", stopped: "⏹ متوقف"
};

// `/cmd@BotName payload` -> ["/cmd", "payload"]
function command(text) {
  const t = String(text || "").trim();
  if (!t.startsWith("/")) return null;
  const [head, ...rest] = t.split(/\s+/);
  const cmd = head.split("@")[0].toLowerCase();
  return { cmd, arg: rest.join(" ").trim() };
}

function replyKeyboard(u) {
  const options = MIN_SCORE_CHOICES.map(s => ({
    text: s === 0 ? "0 (همه)" : String(s),
    callback_data: "set:score:" + s
  }));
  return [[options[0], options[1], options[2]], [options[3], options[4]]];
}

async function onMessage(e, msg, run) {
  const from = msg.from, chat = msg.chat.id;
  if (!from || from.is_bot) return;
  const text = (msg.text || "").trim();
  const cmd = command(text);
  let u = await getUser(e, from.id, run);
  const admin = isAdmin(e, from.id);

  if (cmd?.cmd === "/start") {
    if (!u) {
      u = newUser(from);
      if (admin) u.status = "approved";
      u = await putUser(e, u, run);
      if (u.status === "pending") {
        for (const a of userIds(e)) {
          await say(e, a,
            `🆕 درخواست عضویت\n\nنام: ${esc(u.name)}\nیوزرنیم: @${esc(u.username || "-")}\nآیدی: <code>${u.id}</code>`,
            {
              parse_mode: "HTML", reply_markup: {
                inline_keyboard: [[
                  { text: "✅ تایید", callback_data: `adm:approve:${u.id}` },
                  { text: "❌ رد", callback_data: `adm:reject:${u.id}` },
                  { text: "🚫 بلاک", callback_data: `adm:block:${u.id}` }
                ]]
              }
            }, run);
        }
        await say(e, chat, "درخواست شما ثبت شد ✅\nپس از تایید ادمین، آفرها ارسال می‌شود.", {}, run);
        return;
      }
    } else if (["stopped", "rejected"].includes(u.status)) {
      u.status = admin ? "approved" : "pending";
      u = await putUser(e, u, run);
      if (u.status === "pending") await say(e, chat, "درخواست شما دوباره ثبت شد؛ منتظر تایید ادمین باشید.", {}, run);
    } else {
      u = await putUser(e, u, run);  // stores the _v7 migration
    }
    await say(e, chat, WELCOME + (admin ? "\n\n" + ADMIN_HELP : ""), {}, run);
    return;
  }

  if (!u) { await say(e, chat, "ابتدا /start را بزنید.", {}, run); return; }

  switch (cmd?.cmd) {
    case "/status": {
      const mute = u.mute_until > Date.now()
        ? `\n🔕 بی‌صدا تا ${new Date(u.mute_until).toISOString().slice(0, 16).replace("T", " ")} UTC` : "";
      await say(e, chat,
        `وضعیت: ${STATUS_FA[u.status] || u.status}\nحداقل امتیاز: ${u.min_score ?? DEFAULT_MIN_SCORE}\nآفر دریافتی: ${u.sent_count || 0}${mute}`,
        {}, run);
      return;
    }
    case "/latest": {
      if (u.status !== "approved") { await say(e, chat, "هنوز تایید نشده‌اید.", {}, run); return; }
      // Each press costs up to 4 LLM calls and 4 sends; without this a single
      // user can exhaust the free tier.
      const kvm = kvApi(e, run);
      const stamp = await kvm.get("latest:" + from.id, CACHE_TTL_S);
      if (stamp && Date.now() - Number(stamp) < LATEST_COOLDOWN_MS) {
        const mins = Math.ceil((LATEST_COOLDOWN_MS - (Date.now() - Number(stamp))) / 60000);
        await say(e, chat, `همین حالا لیست را فرستادم. ${mins} دقیقهٔ دیگر دوباره امتحان کنید.`, {}, run);
        return;
      }
      await kvm.put("latest:" + from.id, String(Date.now()), { expirationTtl: 3600 });
      const offs = liveOffers(await fetchOffers(e, run)).slice(0, 4);
      if (!offs.length) { await say(e, chat, "فعلاً آفر فعالی ثبت نشده.", {}, run); return; }
      await say(e, chat, `⏳ ${offs.length} آفر پیدا شد، در حال آماده‌سازی توضیح فارسی…`, {}, run);
      const fas = await Promise.all(offs.map(o => enrich(e, o, run).catch(() => localFa(o))));
      offs.forEach((o, i) => pushSend(e, run, chat, offerText(o, fas[i]), { parse_mode: "HTML", reply_markup: offerKb(offerId(o)) }));
      await flushSends(e, run);
      return;
    }
    case "/why": {
      const all = await fetchOffers(e, run);
      const want = String(cmd.arg || "").trim();
      const hit = all.find(o => offerId(o) === want || String(o.id || "") === want)
        || liveOffers(all).find(o => offerId(o).startsWith(want) && want.length >= 4);
      if (!want) {
        const top = liveOffers(all).slice(0, 6)
          .map(o => `<code>${esc(offerId(o))}</code> — ${esc(offerTitle(o))}`).join("\n");
        await say(e, chat, `یک شناسه بفرستید، مثلاً <code>/why ${esc(offerId(liveOffers(all)[0] || {}) || "id")}</code>\n\n${top || "آفری ثبت نشده"}`, { parse_mode: "HTML" }, run);
        return;
      }
      if (!hit) { await say(e, chat, "آفری با این شناسه پیدا نشد.", {}, run); return; }
      const fa = await enrich(e, hit, run).catch(() => localFa(hit));
      await say(e, chat, `🆔 <code>${esc(offerId(hit))}</code>\n` + offerText(hit, fa), { parse_mode: "HTML" }, run);
      return;
    }
    case "/settings": {
      await say(e, chat,
        `حداقل امتیاز فعلی: <b>${u.min_score ?? DEFAULT_MIN_SCORE}</b>\n(امتیاز آفرها معمولاً بین ۱۵ تا ۴۰ است؛ ۲۰ پیشنهادی است)`,
        { parse_mode: "HTML", reply_markup: { inline_keyboard: replyKeyboard(u) } }, run);
      return;
    }
    case "/mute":
      u.mute_until = Date.now() + 12 * 3600 * 1000; await putUser(e, u, run);
      await say(e, chat, "🔕 تا ۱۲ ساعت بی‌صدا شد. لغو با /unmute", {}, run); return;
    case "/unmute":
      u.mute_until = 0; u.muzzles = 0; await putUser(e, u, run);
      await say(e, chat, "🔔 بی‌صدا لغو شد.", {}, run); return;
    case "/stop":
      u.status = "stopped"; await putUser(e, u, run);
      await say(e, chat, "⏹ متوقف شد. شروع دوباره با /start", {}, run); return;
    case "/help":
      await say(e, chat, WELCOME + (admin ? "\n\n" + ADMIN_HELP : ""), {}, run); return;
    default:
      break;
  }

  if (admin) {
    if (cmd?.cmd === "/pending") {
      const list = (await loadUsers(e, run)).filter(x => x.status === "pending");
      if (!list.length) { await say(e, chat, "درخواستی در انتظار نیست.", {}, run); return; }
      for (const p of list) {
        await say(e, chat, `${esc(p.name)} @${esc(p.username || "-")}\n<code>${p.id}</code>`, {
          parse_mode: "HTML", reply_markup: {
            inline_keyboard: [[
              { text: "✅ تایید", callback_data: `adm:approve:${p.id}` },
              { text: "❌ رد", callback_data: `adm:reject:${p.id}` }
            ]]
          }
        }, run);
      }
      return;
    }
    if (cmd?.cmd === "/users") {
      const list = await loadUsers(e, run);
      const cnt = {};
      list.forEach(x => { cnt[x.status] = (cnt[x.status] || 0) + 1; });
      await say(e, chat, `مجموع: ${list.length}\n` +
        Object.entries(cnt).map(([k, v]) => `${k}: ${v}`).join("\n"), {}, run);
      return;
    }
    if (cmd?.cmd === "/push") {
      const st = await dispatch(e, run);
      await say(e, chat, `ارسال شد: ${st.sent}\nرد شد: ${st.skipped} | ناموفق: ${st.failed} | کاربر: ${st.users}`, {}, run);
      return;
    }
    if (cmd?.cmd === "/id") {
      const quoted = msg.reply_to_message;
      const body = quoted?.text || quoted?.caption || quoted?.reply_markup?.inline_keyboard?.[0]?.[0]?.callback_data || "";
      const m = String(body).match(/fb:(?:ok|dead):([A-Za-z0-9_]+)/);
      await say(e, chat, m ? `شناسهٔ آفر: <code>${esc(m[1])}</code>` : "روی یک پیام آفر ریپلای کنید.", { parse_mode: "HTML" }, run);
      return;
    }
    if (cmd?.cmd === "/forget") {
      const uid = String(cmd.arg || "").trim();
      if (!/^-?\d+$/.test(uid)) { await say(e, chat, "شناسهٔ عددی کاربر را بدهید: /forget 12345", {}, run); return; }
      const who = await getUser(e, uid, run);
      if (!who) { await say(e, chat, "کاربر پیدا نشد.", {}, run); return; }
      who.sent = []; who.retry = null;
      await putUser(e, who, run);
      await say(e, chat, `تاریخچهٔ ارسال ${esc(who.name || uid)} پاک شد. حالا /push`, {}, run);
      return;
    }
    if (cmd?.cmd === "/recache") {
      await say(e, chat, `کش توضیح پاک شد: ${await clearPrefix(e, "fa:", run)}`, {}, run); return;
    }
    if (cmd?.cmd === "/resend") {
      const old = Array.isArray(u.sent) ? u.sent.length : 0;
      u.sent = []; u.retry = null;
      await putUser(e, u, run);
      await say(e, chat, `تاریخچهٔ ارسال شما (${old} آفر) پاک شد. حالا /push بزنید.`, {}, run);
      return;
    }
    if (cmd?.cmd === "/clearerrors") {
      await say(e, chat, `لاگ خطا پاک شد: ${await clearPrefix(e, "err:", run)}`, {}, run); return;
    }
    if (cmd?.cmd === "/errors") {
      const errs = await recentErrors(e, 8, run);
      await say(e, chat, errs.length ? errs.map(x => `• ${x.at}\n${x.where}\n${x.msg}`).join("\n\n") : "خطایی ثبت نشده ✅", {}, run);
      return;
    }
    if (cmd?.cmd === "/diag") {
      await say(e, chat, await diagText(e, run), { parse_mode: "HTML" }, run); return;
    }
    if (cmd?.cmd === "/broadcast") {
      const body = cmd.arg;
      if (!body) { await say(e, chat, "متن پیام را بعد از دستور بنویسید.", {}, run); return; }
      const list = (await loadUsers(e, run)).filter(x => x.status === "approved");
      let n = 0;
      for (const x of list) {
        const r = await sendMessage(e, x.id, body, {}, run);
        if (r.ok) n++;
        if (run.budget.exhausted) break;
        await sleep(TG_SPACING_MS);
      }
      await say(e, chat, `به ${n} نفر از ${list.length} ارسال شد.`, {}, run);
      return;
    }
  }

  await say(e, chat, "دستور ناشناخته. /help", {}, run);
}

/* --------------------- small per-request send queue ----------------- */
/* /latest and /why answer a single user; they share one flush so a slow
 * Telegram call cannot exceed the HTTP duration on the user's side. */

function pushSend(e, run, chat, text, extra) {
  run.pending = run.pending || [];
  run.pending.push({ chat, text, extra });
}
async function flushSends(e, run) {
  const pending = run.pending || [];
  run.pending = [];
  if (!pending.length) return;
  const sender = new Sender(e, run);
  await sender.flush(pending.map(({ chat, text, extra }) => ({ chat, text, extra })));
}

async function diagText(e, run) {
  const all = await fetchOffers(e, run);
  const live = liveOffers(all);
  const users = await loadUsers(e, run);
  const errs = await recentErrors(e, 1, run);
  const withHistory = users.filter(x => Array.isArray(x.sent) && x.sent.length).length;
  const budget = run.budget;
  return [
    `نسخه: ${VERSION}`,
    `ریپو دیتا: ${esc(normRepo(e.DATA_REPO))}`,
    `آفرها: ${all.length} (فعال: ${live.length} | بالاترین امتیاز: ${live[0] ? offerScore(live[0]) : 0})`,
    `کاربران: ${users.length} (تاییدشده: ${users.filter(x => x.status === "approved").length})`,
    `کاربران با تاریخچهٔ ارسال: ${withHistory}`,
    `Gemini: ${e.GEMINI_API_KEY ? "✅" : "❌"} | Groq: ${e.GROQ_API_KEY ? "✅" : "❌"} | GH_TOKEN: ${e.GH_TOKEN ? "✅" : "❌"}`,
    `بودجهٔ این اجرا: http ${budget.httpLeft} | kv ${budget.kvLeft}`,
    `نمونه: ${live[0] ? esc(offerTitle(live[0])) : "-"}`,
    `آخرین خطا: ${errs[0] ? esc(String(errs[0].msg).slice(0, 200)) : "ندارد"}`
  ].join("\n");
}

/* ---------------------------- callbacks ---------------------------- */

async function onCallback(e, cq, run) {
  const d = cq.data || "", from = cq.from;
  if (!from) return;

  const target = (cq.message && cq.message.chat && cq.message.message_id)
    ? { chat: cq.message.chat.id, mid: cq.message.message_id } : null;

  if (d.startsWith("adm:")) {
    if (!isAdmin(e, from.id)) { await answerCallback(e, cq.id, "دسترسی ندارید", true, run); return; }
    const [, act, uid] = d.split(":");
    const who = await getUser(e, uid, run);
    if (!who) { await answerCallback(e, cq.id, "کاربر یافت نشد", true, run); return; }
    who.status = { approve: "approved", reject: "rejected", block: "blocked", pending: "pending" }[act] || "pending";
    await putUser(e, who, run);
    if (target) {
      const label = { approved: "✅ تایید شد", rejected: "❌ رد شد", blocked: "🚫 بلاک شد" }[who.status] || who.status;
      // keep the original request text, only append the outcome
      const base = String(cq.message.text || "").replace(/\n\n➡️[\s\S]*$/, "");
      await editMessage(e, target.chat, target.mid, `${base}\n\n➡️ ${label}`,
        [[{ text: "↩️ بازگردانی به «در انتظار»", callback_data: `adm:pending:${who.id}` }]], run);
    }
    if (who.status === "approved") {
      await say(e, who.id, "✅ عضویت شما تایید شد.\nبرای دیدن آخرین آفرها /latest بزنید.", {}, run);
    }
    await answerCallback(e, cq.id, "انجام شد", false, run);
    return;
  }

  if (d.startsWith("set:score:")) {
    const u = await getUser(e, from.id, run);
    if (!u) { await answerCallback(e, cq.id, "ابتدا /start", true, run); return; }
    const parsed = parseInt(d.split(":")[2], 10);
    u.min_score = Number.isNaN(parsed) ? 0 : parsed;
    await putUser(e, u, run);
    if (target) {
      await editMessage(e, target.chat, target.mid,
        `حداقل امتیاز فعلی: <b>${u.min_score}</b>\n(امتیاز آفرها معمولاً بین ۱۵ تا ۴۰ است؛ ۲۰ پیشنهادی است)`,
        replyKeyboard(u), run);
    }
    await answerCallback(e, cq.id, `ذخیره شد: ${u.min_score}`, false, run);
    return;
  }

  if (d.startsWith("fb:")) {
    const [, vote, oid] = d.split(":");
    if (!["ok", "dead"].includes(vote) || !oid) { await answerCallback(e, cq.id, "", false, run); return; }
    await queueVote(e, oid, vote, from.id, run);
    if (target) {
      // Do not replace the offer text: the user is still reading it.
      const note = vote === "ok" ? "✅ نظر شما ثبت شد: معتبر بود" : "👎 نظر شما ثبت شد: کار نکرد";
      await editMessage(e, target.chat, target.mid,
        `${String(cq.message.text || "").replace(/\n\n(?:✅|👎) نظر شما ثبت شد[\s\S]*$/, "")}\n\n${note}`, [], run);
    }
    if (vote === "dead") {
      for (const a of userIds(e)) {
        await say(e, a, `👎 گزارش خرابی\n<code>${esc(oid)}</code>\nاز: ${from.id}`, { parse_mode: "HTML" }, run);
      }
    }
    await answerCallback(e, cq.id, vote === "ok" ? "ممنون 👍" : "ثبت شد، بررسی می‌کنیم", false, run);
  }
}

/* ------------------------------ routes ----------------------------- */

const json = (obj, status = 200) => new Response(JSON.stringify(obj, null, 2), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
});

// A dedicated secret is nicer than reusing the webhook secret, which is also
// visible to Telegram and stored in its logs. Both work.
function adminAuthed(req, url, env) {
  const expected = String(env.ADMIN_SECRET || env.WEBHOOK_SECRET || "").trim();
  if (!expected) return false;
  const header = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "").trim();
  const key = url.searchParams.get("key") || "";
  // length-independent compare: JSON strings, no early exit
  return header.length === expected.length && header === expected ||
    key.length === expected.length && key === expected;
}

const BOT_COMMANDS = [
  { command: "latest", description: "آخرین آفرها" },
  { command: "why", description: "توضیح یک آفر با شناسه" },
  { command: "status", description: "وضعیت عضویت" },
  { command: "settings", description: "حداقل امتیاز" },
  { command: "mute", description: "۱۲ ساعت بی‌صدا" },
  { command: "unmute", description: "لغو بی‌صدا" },
  { command: "stop", description: "توقف دریافت" },
  { command: "help", description: "راهنما" }
];

async function registerCommands(e, run) {
  return tgCall(e, "setMyCommands", { commands: BOT_COMMANDS }, run);
}

async function setWebhook(e, origin, run) {
  return tgCall(e, "setWebhook", {
    url: `${origin}/tg`,
    secret_token: tgSecret(e.WEBHOOK_SECRET),
    drop_pending_updates: true,
    allowed_updates: ["message", "callback_query"]
  }, run);
}

async function handleUpdate(e, update) {
  const run = newRun(e);
  try {
    if (update.message) await onMessage(e, update.message, run);
    else if (update.callback_query) await onCallback(e, update.callback_query, run);
    else if (update.edited_message) await onMessage(e, update.edited_message, run);
    await flushSends(e, run);
  } catch (err) {
    await logErr(e, "handleUpdate", err, run);
    const ex = run.budget.exhausted;
    for (const a of userIds(e)) {
      await say(e, a, `⚠️ خطا: ${String(err).slice(0, 300)}${ex ? `\n(بودجهٔ ${ex.limit} تمام شد)` : ""}`, {}, run);
    }
  }
}

export default {
  async fetch(req, env, ctx) {
    let url;
    try { url = new URL(req.url); } catch (_) { return new Response("bad request", { status: 400 }); }
    const waitUntil = fn => (ctx && typeof ctx.waitUntil === "function") ? ctx.waitUntil(fn) : fn();

    // ---- Telegram webhook -------------------------------------------------
    if (req.method === "POST" && url.pathname === "/tg") {
      const expected = tgSecret(env.WEBHOOK_SECRET);
      const got = req.headers.get("x-telegram-bot-api-secret-token") || "";
      if (!expected || got !== expected) return new Response("unauthorized", { status: 401 });
      const upd = await req.json().catch(() => null);
      if (upd) waitUntil(handleUpdate(env, upd));   // answered after the 200
      return new Response("ok");
    }

    if (url.pathname === "/health") {
      return new Response("ok " + VERSION, { headers: { "cache-control": "no-store" } });
    }

    // ---- one-shot setup, so the secret never has to travel in a chat message
    if (url.pathname === "/setup") {
      if (!adminAuthed(req, url, env)) return new Response("unauthorized", { status: 401 });
      const origin = (url.searchParams.get("url") || url.origin).replace(/\/+$/, "");
      const run = newRun(env);
      const hook = await setWebhook(env, origin, run);
      const cmds = await registerCommands(env, run);
      return json({ ok: hook.ok && cmds.ok, webhook: hook, commands: cmds, version: VERSION });
    }

    const auth = adminAuthed(req, url, env);
    if (!auth) return new Response("unauthorized", { status: 401 });
    const run = newRun(env);

    if (url.pathname === "/setwebhook") {
      const origin = (url.searchParams.get("url") || url.origin).replace(/\/+$/, "");
      return json(await setWebhook(env, origin, run));
    }
    if (url.pathname === "/setcommands") return json(await registerCommands(env, run));
    if (url.pathname === "/webhookinfo") {
      const r = await tgCall(env, "getWebhookInfo", {}, run);
      return json({
        url: r.result?.url, pending: r.result?.pending_update_count,
        last_error_date: r.result?.last_error_date, last_error: r.result?.last_error_message
      });
    }
    if (url.pathname === "/deletewebhook") return json(await tgCall(env, "deleteWebhook", {}, run));

    if (url.pathname === "/dispatch") {
      if (url.searchParams.get("async") === "1") {
        waitUntil(dispatch(env, newRun(env)));
        return json({ ok: true, queued: true, version: VERSION });
      }
      return json({ ok: true, ...(await dispatch(env, newRun(env))) });
    }

    if (url.pathname === "/raw") return json((await fetchOffers(env, run)).slice(0, 3));

    if (url.pathname === "/preview") {
      const offs = liveOffers(await fetchOffers(env, run)).slice(0, 3);
      const out = [];
      for (const o of offs) {
        out.push({ title: offerTitle(o), link: offerLink(o), text: offerText(o, await enrich(env, o, run)) });
      }
      return json(out);
    }

    if (url.pathname === "/groqmodels") {
      if (!run.budget.takeHttp(1)) return json({ error: "budget" }, 429);
      const r = await fetch("https://api.groq.com/openai/v1/models", {
        headers: { authorization: `Bearer ${env.GROQ_API_KEY}` }, signal: AbortSignal.timeout(15000)
      });
      const j = await r.json().catch(() => ({}));
      const ids = (j.data || []).map(m => m.id).sort();
      return json({ status: r.status, count: ids.length, ids, raw: ids.length ? undefined : j });
    }
    if (url.pathname === "/geminimodels") {
      if (!run.budget.takeHttp(1)) return json({ error: "budget" }, 429);
      const r = await fetch("https://generativelanguage.googleapis.com/v1beta/models", {
        headers: { "x-goog-api-key": env.GEMINI_API_KEY }, signal: AbortSignal.timeout(15000)
      });
      const j = await r.json().catch(() => ({}));
      const ids = (j.models || []).map(m => String(m.name || "").replace(/^models\//, "")).sort();
      return json({ status: r.status, count: ids.length, ids, raw: ids.length ? undefined : j });
    }
    if (url.pathname === "/enrichtest") {
      const o = liveOffers(await fetchOffers(env, run))[0];
      if (!o) return new Response("no offers");
      const out = { title: offerTitle(o), link: offerLink(o) };
      const page = await fetchPageText(offerLink(o), run);
      out.page_chars = page.length;
      const prompt = buildPrompt(o, page);
      if (env.GEMINI_API_KEY) {
        try { const raw = await callGemini(env, prompt, run); out.gemini_raw = String(raw).slice(0, 700); out.gemini_parsed = parseJson(raw); }
        catch (err) { out.gemini_error = String(err); }
      } else out.gemini_error = "no key";
      if (env.GROQ_API_KEY) {
        try { const raw = await callGroq(env, prompt, run); out.groq_raw = String(raw).slice(0, 700); out.groq_parsed = parseJson(raw); }
        catch (err) { out.groq_error = String(err); }
      } else out.groq_error = "no key";
      return json(out);
    }
    if (url.pathname === "/errors") return json(await recentErrors(env, 20, run));
    if (url.pathname === "/clearerrors") return json({ deleted: await clearPrefix(env, "err:", run) });
    if (url.pathname === "/recache") return json({ deleted: await clearPrefix(env, "fa:", run) });

    if (url.pathname === "/diag") {
      const all = await fetchOffers(env, run);
      const live = liveOffers(all);
      const users = await loadUsers(env, run);
      let kvOk = true;
      if (!run.budget.takeKv(1)) kvOk = "budget";
      else { try { await env.KV.get("ping"); } catch (_) { kvOk = false; } }
      return json({
        version: VERSION,
        data_repo: normRepo(env.DATA_REPO),
        offers: all.length,
        live_offers: live.length,
        max_offer_score: live[0] ? offerScore(live[0]) : 0,
        users: users.length,
        approved: users.filter(u => u.status === "approved").length,
        users_with_history: users.filter(x => Array.isArray(x.sent) && x.sent.length).length,
        has_bot_token: Boolean(env.TELEGRAM_BOT_TOKEN),
        has_gh_token: Boolean(env.GH_TOKEN),
        has_admin_secret: Boolean(env.ADMIN_SECRET),
        gemini: Boolean(env.GEMINI_API_KEY),
        groq: Boolean(env.GROQ_API_KEY),
        kv_binding: kvOk,
        admins: admins(env).length,
        budget: { http_left: run.budget.httpLeft, kv_left: run.budget.kvLeft },
        sample_title: live[0] ? offerTitle(live[0]) : null,
        sample_verify: live[0] ? offerVerify(live[0]) : null,
        sample_link: live[0] ? offerLink(live[0]) : null,
        last_error: (await recentErrors(env, 1, run))[0] || null
      });
    }

    return new Response(`freeai-hunter worker ${VERSION}`, {
      headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" }
    });
  },

  // Cron Trigger. A second trigger on the same Worker can mean a different
  // schedule; keep dispatching for all of them (idempotent thanks to the
  // per-user history), and log which one fired.
  async scheduled(event, env, ctx) {
    const run = newRun(env);
    const job = (async () => {
      const stats = await dispatch(env, run);
      console.log("cron", event?.cron || "-", JSON.stringify(stats));
      return stats;
    })();
    (ctx && typeof ctx.waitUntil === "function") ? ctx.waitUntil(job) : await job;
  }
};
