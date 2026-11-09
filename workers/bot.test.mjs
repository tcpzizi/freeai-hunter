/* Tests for the Cloudflare Worker bot.
 *
 *   node --test workers/bot.test.mjs
 *
 * Everything external (Telegram, GitHub, LLM, KV) is mocked, so these run
 * offline and assert behaviour, not text formatting details.
 */
import test from "node:test";
import assert from "node:assert/strict";

/* ------------------------------- mocks ------------------------------ */

function makeKV({ maxOps = 1000 } = {}) {
  const store = new Map();
  const stats = { get: 0, put: 0, delete: 0, list: 0, ops: 0, writes: 0 };
  const count = kind => {
    stats[kind]++;
    stats.ops++;
    if (kind === "put" || kind === "delete") stats.writes++;
    if (stats.ops > maxOps) {
      const err = new Error("KV_ERROR: too many operations in one invocation");
      err.name = "KVError";
      throw err;
    }
  };
  return {
    store, stats,
    async get(k, opts) {
      count("get");
      const v = store.get(k);
      if (v && v.expirationTtl && v.expires < Date.now()) { store.delete(k); return null; }
      return v ? v.value : null;
    },
    async put(k, value, opts = {}) {
      count("put");
      store.set(k, { value: String(value), expires: Date.now() + (opts.expirationTtl || 1e9) * 1000 });
    },
    async delete(k) { count("delete"); store.delete(k); },
    async list({ prefix = "", cursor } = {}) {
      count("list");
      const names = [...store.keys()].filter(k => k.startsWith(prefix)).sort();
      return { keys: names.map(name => ({ name })), list_complete: true, cursor: undefined };
    }
  };
}

const ok = (result = true) => ({ ok: true, result });

function makeFetch({ offers = [], telegram } = {}) {
  const calls = { telegram: [], other: [] };
  const mock = async (url, init = {}) => {
    const u = String(url);
    if (u.includes("/contents/offers.json")) {
      return new Response(JSON.stringify(offers), { status: 200 });
    }
    if (u.includes("api.telegram.org")) {
      const payload = JSON.parse(init.body || "{}");
      const method = u.split("/").pop();
      calls.telegram.push({ method, payload });
      const handler = telegram?.handler;
      const res = handler ? await handler(method, payload) : undefined;
      return new Response(JSON.stringify(res || ok()), { status: 200 });
    }
    calls.other.push(u);
    return new Response("<html><body>a free plan with free credits</body></html>", {
      status: 200, headers: { "content-type": "text/html" }
    });
  };
  return { fetch: mock, calls };
}

function env(kv, extra = {}) {
  return {
    TELEGRAM_BOT_TOKEN: "123456:TEST",
    ADMIN_IDS: "42",
    WEBHOOK_SECRET: "hook-secret",
    DATA_REPO: "owner/data",
    KV: kv,
    ...extra
  };
}

const offer = (i, over = {}) => ({
  id: "o" + i, vendor: "vendor" + i, domain: `v${i}.com`, offer_type: "free_credits",
  value: `$${i}0 credits`, score: 20 + i, verify_status: "verified",
  signup_url: `https://v${i}.com/signup`, updated_at: "2026-11-01T00:00:00Z", ...over
});

const approvedUser = (id, extra = {}) => JSON.stringify({
  id, name: "u" + id, username: "u" + id, status: "approved",
  min_score: 20, sent_count: 0, mute_until: 0, _v7: true, ...extra
});

let worker, mod;
async function load() {
  if (!mod) mod = await import("./bot.js");
  return mod.default;
}

/* ------------------------------- tests ------------------------------ */

test("the webhook answers 200 and processes the update through ctx.waitUntil", async () => {
  worker = await load();
  const kv = makeKV();
  const { fetch } = makeFetch();
  globalThis.fetch = fetch;
  let deferred = null;
  const ctx = { waitUntil: p => { deferred = p; } };

  const req = new Request("https://bot.example.workers.dev/tg", {
    method: "POST",
    headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "hook-secret" },
    body: JSON.stringify({ update_id: 1, message: { message_id: 9, from: { id: 42, first_name: "A" }, chat: { id: 42 }, text: "/start" } })
  });
  const res = await worker.fetch(req, env(kv), ctx);
  assert.equal(res.status, 200);
  assert.ok(deferred, "handleUpdate must be handed to ctx.waitUntil");
  await deferred;
  // the admin's own /start was stored and answered
  assert.ok(kv.store.has("user:42"), "user record written");
});

test("a wrong webhook secret is rejected and does nothing", async () => {
  worker = await load();
  const kv = makeKV();
  const { fetch, calls } = makeFetch();
  globalThis.fetch = fetch;
  const req = new Request("https://x/tg", {
    method: "POST",
    headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "nope" },
    body: "{}"
  });
  const res = await worker.fetch(req, env(kv), { waitUntil: () => {} });
  assert.equal(res.status, 401);
  assert.equal(calls.telegram.length, 0);
});

test("v6 bug: admin commands were unreachable; /users works now", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:42", { value: approvedUser(42) });
  kv.store.set("user:7", { value: approvedUser(7) });
  kv.store.set("user:8", { value: JSON.stringify({ id: 8, status: "pending" }), expires: 0 });
  const { fetch, calls } = makeFetch({ offers: [offer(1)] });
  globalThis.fetch = fetch;
  const ctx = { waitUntil: () => {} };
  await worker.fetch(new Request("https://x/tg", {
    method: "POST",
    headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "hook-secret" },
    body: JSON.stringify({ message: { message_id: 1, from: { id: 42, first_name: "A" }, chat: { id: 42 }, text: "/users" } })
  }), env(kv), ctx);
  await new Promise(r => setTimeout(r, 30));
  const answer = calls.telegram.find(c => c.method === "sendMessage");
  assert.ok(answer, "an admin command must produce a reply");
  assert.match(answer.payload.text, /مجموع/, answer.payload.text);
});

test("dispatcher: a failed send is not counted, and is retried next run", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  let failOnce = true;
  const { fetch } = makeFetch({
    offers: [offer(1)],
    telegram: {
      handler: method => {
        if (method === "sendMessage" && failOnce) {
          failOnce = false;
          return { ok: false, error_code: 502, description: "Bad Gateway" };
        }
        return ok({ message_id: 1 });
      }
    }
  });
  globalThis.fetch = fetch;
  const res = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const body = await res.json();
  assert.equal(body.sent, 0, "a refused send must not count as sent");
  assert.equal(body.failed, 1);
  const user = JSON.parse(kv.store.get("user:7").value);
  assert.equal(user.retry.id, "o1", "the offer is remembered for a retry");
  assert.equal(user.sent, undefined, "nothing is added to the sent history");

  // next run (Telegram healthy again) delivers the same offer
  globalThis.fetch = makeFetch({ offers: [offer(1)] }).fetch;
  const res2 = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const body2 = await res2.json();
  assert.equal(body2.sent, 1);
  const after = JSON.parse(kv.store.get("user:7").value);
  assert.equal(after.retry, null);
  assert.deepEqual(after.sent, ["o1"]);
  assert.equal(after.sent_count, 1);
});

test("dispatcher: 20 users are served inside the 1000 KV ops budget", async () => {
  worker = await load();
  const kv = makeKV({ maxOps: 1000 });
  for (let i = 0; i < 20; i++) kv.store.set("user:" + (100 + i), { value: approvedUser(100 + i) });
  const { fetch } = makeFetch({ offers: [offer(1), offer(2)], telegram: { handler: () => ok({ message_id: 1 }) } });
  globalThis.fetch = fetch;
  const res = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const body = await res.json();
  assert.equal(body.sent, 20);
  assert.ok(kv.stats.ops <= 1000, `used ${kv.stats.ops} KV ops`);
  // 20 user records + the offers cache + one fa cache entry
  assert.ok(kv.stats.writes <= 24, `used ${kv.stats.writes} writes`);
});

test("dispatcher: beyond the subrequest budget it degrades instead of throwing", async () => {
  worker = await load();
  const kv = makeKV({ maxOps: 1000 });
  for (let i = 0; i < 50; i++) kv.store.set("user:" + (200 + i), { value: approvedUser(200 + i) });
  const { fetch } = makeFetch({ offers: [offer(1)], telegram: { handler: () => ok({ message_id: 1 }) } });
  globalThis.fetch = fetch;
  const res = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  assert.equal(res.status, 200, "the remaining users are planned, not crashed");
  const body = await res.json();
  assert.ok(body.sent > 0 && body.sent < 50, `sent ${body.sent}`);
  // whatever did not fit is simply picked up by the next run
  const leftovers = [...kv.store.keys()].filter(k => k.startsWith("user:2") &&
    JSON.parse(kv.store.get(k).value).status === "approved");
  assert.ok(leftovers.length > 0);
});

test("dispatcher: one message per user per run (KV write quota)", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  const offers = [1, 2, 3, 4, 5].map(i => offer(i));
  const { fetch, calls } = makeFetch({ offers, telegram: { handler: () => ok({ message_id: 1 }) } });
  globalThis.fetch = fetch;
  const res = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  assert.equal((await res.json()).sent, 1);
  assert.equal(calls.telegram.filter(c => c.method === "sendMessage").length, 1);
});

test("a blocked user is marked blocked, not retried forever", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  const { fetch } = makeFetch({
    offers: [offer(1)],
    telegram: { handler: m => m === "sendMessage" ? { ok: false, error_code: 403, description: "Forbidden: bot was blocked by the user" } : ok() }
  });
  globalThis.fetch = fetch;
  await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  assert.equal(JSON.parse(kv.store.get("user:7").value).status, "blocked");
});

test("dead offers are filtered even though offers.json has no status field", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  const { fetch, calls } = makeFetch({
    offers: [offer(1, { verify_status: "dead_link" }), offer(2)],
    telegram: { handler: () => ok({ message_id: 1 }) }
  });
  globalThis.fetch = fetch;
  await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const sent = calls.telegram.filter(c => c.method === "sendMessage");
  assert.equal(sent.length, 1);
  assert.match(sent[0].payload.text, /vendor2/);
  // /raw is the raw GitHub list (both offers) ...
  const raw = await (await worker.fetch(new Request("https://x/raw?key=hook-secret"), env(kv), { waitUntil: () => {} })).json();
  assert.equal(raw.length, 2);
  // ... while /preview goes through liveOffers() and drops the dead one
  const preview = await (await worker.fetch(new Request("https://x/preview?key=hook-secret"), env(kv), { waitUntil: () => {} })).json();
  assert.equal(preview.length, 1);
  assert.equal(preview[0].title.includes("vendor2"), true);
});

test("a broken HTML entity is re-sent as plain text instead of being lost", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  let first = true;
  const seen = [];
  const { fetch } = makeFetch({
    offers: [offer(1)],
    telegram: {
      handler: (method, payload) => {
        if (method !== "sendMessage") return ok();
        seen.push(payload);
        if (first) { first = false; return { ok: false, error_code: 400, description: "Bad Request: can't parse entities" }; }
        return ok({ message_id: 1 });
      }
    }
  });
  globalThis.fetch = fetch;
  const res = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  assert.equal((await res.json()).sent, 1);
  assert.equal(seen.length, 2);
  assert.equal(seen[0].parse_mode, "HTML");
  assert.equal(seen[1].parse_mode, undefined, "the retry drops parse_mode");
});

test("votes are coalesced: two taps in a minute cost one KV write", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  kv.store.set("user:9", { value: approvedUser(9) });
  const { fetch, calls } = makeFetch({ offers: [offer(1)] });
  globalThis.fetch = fetch;
  const ctx = { waitUntil: () => {} };
  const vote = (uid, oid) => new Request("https://x/tg", {
    method: "POST",
    headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "hook-secret" },
    body: JSON.stringify({
      callback_query: {
        id: "cb" + uid, from: { id: uid, first_name: "u" },
        data: "fb:ok:" + oid,
        message: { message_id: 5, chat: { id: uid }, text: "offer text" }
      }
    })
  });
  const before = kv.stats.writes;
  await worker.fetch(vote(7, "o1"), env(kv), ctx);
  await new Promise(r => setTimeout(r, 20));
  await worker.fetch(vote(9, "o1"), env(kv), ctx);
  await new Promise(r => setTimeout(r, 20));
  const writes = kv.stats.writes - before;
  assert.ok(writes <= 2, `expected at most 2 writes, got ${writes}`);
  assert.ok(calls.telegram.some(c => c.method === "answerCallbackQuery"));
});

test("callback_data stays inside Telegram's 64-byte limit for long ids", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  const longId = "a".repeat(60);
  const { fetch, calls } = makeFetch({
    offers: [offer(1, { id: longId })],
    telegram: { handler: () => ok({ message_id: 1 }) }
  });
  globalThis.fetch = fetch;
  await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const msg = calls.telegram.find(c => c.method === "sendMessage");
  for (const btn of msg.payload.reply_markup.inline_keyboard[0]) {
    assert.ok(Buffer.byteLength(btn.callback_data) <= 64, btn.callback_data);
  }
});

test("GFM: /why explains an offer and /id reads a forwarded message", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:42", { value: approvedUser(42) });
  const { fetch, calls } = makeFetch({ offers: [offer(1)] });
  globalThis.fetch = fetch;
  const ctx = { waitUntil: () => {} };
  const send = text => worker.fetch(new Request("https://x/tg", {
    method: "POST",
    headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "hook-secret" },
    body: JSON.stringify({ message: { message_id: 2, from: { id: 42, first_name: "A" }, chat: { id: 42 }, text } })
  }), env(kv), ctx);

  await send("/why o1");
  await new Promise(r => setTimeout(r, 30));
  const why = calls.telegram.filter(c => c.method === "sendMessage").pop();
  assert.match(why.payload.text, /🆔 <code>o1<\/code>/);
});

test("/diag never prints secret values", async () => {
  worker = await load();
  const kv = makeKV();
  const { fetch } = makeFetch({ offers: [offer(1)] });
  globalThis.fetch = fetch;
  const text = await (await worker.fetch(
    new Request("https://x/diag?key=hook-secret"),
    env(kv, { GH_TOKEN: "ghp_SECRETVALUE", GEMINI_API_KEY: "AIzaSECRET" }),
    { waitUntil: () => {} }
  )).text();
  assert.ok(!text.includes("SECRET"), text);
  assert.match(text, /has_gh_token/);
});

test("admin routes require the secret; /health does not and leaks nothing", async () => {
  worker = await load();
  const kv = makeKV();
  globalThis.fetch = makeFetch({ offers: [] }).fetch;
  const unauth = await worker.fetch(new Request("https://x/diag"), env(kv), { waitUntil: () => {} });
  assert.equal(unauth.status, 401);
  const health = await worker.fetch(new Request("https://x/health"), env(kv), { waitUntil: () => {} });
  assert.equal(health.status, 200);
  const body = await health.text();
  assert.match(body, /ok v7/);
  assert.ok(!body.includes("123456"));
});

test("fetch works when ctx is missing (no waitUntil)", async () => {
  worker = await load();
  const kv = makeKV();
  globalThis.fetch = makeFetch({ offers: [offer(1)] }).fetch;
  const res = await worker.fetch(new Request("https://x/health"), env(kv));
  assert.equal(res.status, 200);
});

test("GitHub failure falls back to the cached copy", async () => {
  worker = await load();
  const kv = makeKV();
  globalThis.fetch = makeFetch({ offers: [offer(1)] }).fetch;
  await worker.fetch(new Request("https://x/raw?key=hook-secret"), env(kv), { waitUntil: () => {} });
  globalThis.fetch = async () => new Response("rate limited", { status: 403 });
  const again = await (await worker.fetch(new Request("https://x/raw?key=hook-secret"), env(kv), { waitUntil: () => {} })).json();
  assert.equal(again.length, 1, "the cached offer list is used when GitHub says no");
});

test("/setup registers the webhook and the command menu", async () => {
  worker = await load();
  const kv = makeKV();
  const { fetch, calls } = makeFetch({});
  globalThis.fetch = fetch;
  const res = await worker.fetch(new Request("https://x/setup?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const body = await res.json();
  assert.equal(body.ok, true);
  const setHook = calls.telegram.find(c => c.method === "setWebhook");
  assert.equal(setHook.payload.url, "https://x/tg");
  assert.equal(setHook.payload.secret_token, "hook-secret");
  assert.ok(calls.telegram.some(c => c.method === "setMyCommands"));
});

test("the cron handler dispatches and survives a slow Telegram", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  globalThis.fetch = makeFetch({ offers: [offer(1)], telegram: { handler: () => ok({ message_id: 1 }) } }).fetch;
  let done = null;
  await worker.scheduled({ cron: "0 * * * *" }, env(kv), { waitUntil: p => { done = p; } });
  assert.ok(done);
  const stats = await done;
  assert.equal(stats.sent, 1);
});

test("poison send: a 400 is retried a bounded number of times, then the offer is dropped", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  kv.store.set("user:8", { value: approvedUser(8) });
  let attempts = 0;
  const MAX_ATTEMPTS_ALLOWED = 4;   // 2 runs × (HTML + plain-text fallback)
  const { fetch } = makeFetch({
    offers: [offer(1)],
    telegram: {
      handler: (method, payload) => {
        if (method !== "sendMessage") return ok();
        // chat 7 always fails, chat 8 is fine
        if (payload.chat_id === 7) { attempts++; return { ok: false, error_code: 400, description: "Bad Request: can't parse entities" }; }
        return ok({ message_id: 1 });
      }
    }
  });
  globalThis.fetch = fetch;
  const ctx = { waitUntil: () => {} };
  for (let run = 0; run < 6; run++) {
    await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), ctx);
  }
  // one failed attempt costs two Telegram calls: HTML, then the plain-text retry
  const perAttempt = 2;
  assert.ok(attempts <= MAX_ATTEMPTS_ALLOWED, `v6 retried for ever; v7 made ${attempts} calls`);
  const bad = JSON.parse(kv.store.get("user:7").value);
  assert.deepEqual(bad.skip, ["o1"], "the offer is parked for this user instead of being retried for ever");
  assert.equal(attempts <= perAttempt * 2, true);
  // and the healthy user keeps getting served while the bad chat is parked
  const healthy = JSON.parse(kv.store.get("user:8").value);
  assert.ok(Array.isArray(healthy.sent) && healthy.sent.length >= 1);
});

test("429 flood control: retry_after is honoured, not ignored", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  let first = true;
  const { fetch } = makeFetch({
    offers: [offer(1)],
    telegram: {
      handler: method => {
        if (method !== "sendMessage") return ok();
        if (first) { first = false; return { ok: false, error_code: 429, description: "Too Many Requests", parameters: { retry_after: 1 } }; }
        return ok({ message_id: 1 });
      }
    }
  });
  globalThis.fetch = fetch;
  const t0 = Date.now();
  const res = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const body = await res.json();
  assert.equal(body.sent, 1, "the message is delivered after the flood window");
  assert.ok(Date.now() - t0 >= 900, "it actually waited");
});

test("/latest has a per-user cooldown instead of burning the LLM quota", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  const { fetch, calls } = makeFetch({ offers: [offer(1)], telegram: { handler: () => ok({ message_id: 1 }) } });
  globalThis.fetch = fetch;
  const ctx = { waitUntil: () => {} };
  const press = () => worker.fetch(new Request("https://x/tg", {
    method: "POST",
    headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "hook-secret" },
    body: JSON.stringify({ message: { message_id: 1, from: { id: 7 }, chat: { id: 7 }, text: "/latest" } })
  }), env(kv), ctx);

  await press();
  await new Promise(r => setTimeout(r, 40));
  const firstRound = calls.telegram.filter(c => c.method === "sendMessage").length;
  assert.ok(firstRound >= 2, "first press: a progress line + the offers");

  calls.telegram.length = 0;
  await press();
  await new Promise(r => setTimeout(r, 40));
  const second = calls.telegram.filter(c => c.method === "sendMessage");
  assert.equal(second.length, 1, "second press inside the window gets one notice");
  assert.match(second[0].payload.text, /دقیقهٔ دیگر/);
});

test("an offer with no id or URL keeps a stable identity across fetches", async () => {
  worker = await load();
  const kv = makeKV();
  kv.store.set("user:7", { value: approvedUser(7) });
  const bare = { vendor: "NoId GmbH", offer_type: "free_trial", score: 25, verify_status: "verified" };
  globalThis.fetch = makeFetch({ offers: [bare], telegram: { handler: () => ok({ message_id: 1 }) } }).fetch;
  await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  const firstId = JSON.parse(kv.store.get("user:7").value).sent[0];
  // a later fetch of the same offer must not look like a new one
  globalThis.fetch = makeFetch({ offers: [{ ...bare }], telegram: { handler: () => ok({ message_id: 1 }) } }).fetch;
  const res = await worker.fetch(new Request("https://x/dispatch?key=hook-secret"), env(kv), { waitUntil: () => {} });
  assert.equal((await res.json()).sent, 0, "not re-announced (v6 used Math.random())");
  assert.equal(JSON.parse(kv.store.get("user:7").value).sent[0], firstId);
});

test("bot.paste.js is the current bot.js (dashboard copy stays in sync)", async () => {
  const { readFileSync } = await import("node:fs");
  const { execFileSync } = await import("node:child_process");
  const paste = readFileSync(new URL("./bot.paste.js", import.meta.url), "utf8");
  // dashboard files must be self-contained
  assert.ok(!/^\s*import\s/m.test(paste), "no import statements");
  assert.ok(!/^\s*export\s/m.test(paste.replace(/export default \{[\s\S]*$/, "")), "only the default export");
  assert.match(paste, /const VERSION = "v7"/);
  assert.match(paste, /export default \{/);
  try {
    execFileSync(process.execPath, [new URL("./tools/build-paste.mjs", import.meta.url).pathname, "--check"], { stdio: "pipe" });
  } catch (err) {
    assert.fail("bot.paste.js is stale: run node workers/tools/build-paste.mjs\n" + String(err.stdout || err.message));
  }
});
