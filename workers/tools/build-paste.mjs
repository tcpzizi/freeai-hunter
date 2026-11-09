#!/usr/bin/env node
/* Builds workers/bot.paste.js — the single file you paste into the Cloudflare
 * dashboard editor — from workers/bot.js, which stays the source of truth.
 *
 *   node workers/tools/build-paste.mjs          # (re)write bot.paste.js
 *   node workers/tools/build-paste.mjs --check  # exit 1 if it is out of date
 *
 * The generated file is the whole bot: no imports, no wrangler.toml, nothing to
 * download. The only things it cannot carry are the bindings and secrets, which
 * live in the dashboard; the header lists them.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const SRC = join(root, "bot.js");
const OUT = join(root, "bot.paste.js");

const header = `/* =====================================================================
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

`;

const src = readFileSync(SRC, "utf8");
// The source file opens with its own banner; keep it, but drop the "deployed
// with wrangler" line, which does not apply to a paste-only file.
const body = src.replace(
  " * Deployed with:  wrangler deploy        (see wrangler.toml / README)",
  " * Source of truth: workers/bot.js (this file is generated from it)"
);

const out = header + body;
const banner = "/* ================= end of paste instructions ================= */\n\n";
const final = out.replace(
  "\nconst VERSION = ",
  "\n" + banner + "const VERSION = "
);

if (process.argv.includes("--check")) {
  let current = "";
  try { current = readFileSync(OUT, "utf8"); } catch (_) {}
  if (current !== final) {
    console.error("bot.paste.js is out of date: run node workers/tools/build-paste.mjs");
    process.exit(1);
  }
  console.log("bot.paste.js is up to date");
} else {
  writeFileSync(OUT, final);
  const kb = (Buffer.byteLength(final) / 1024).toFixed(1);
  console.log(`wrote bot.paste.js (${kb} KB)`);
}
