# freeai-hunter

ایجنت رصد آفرهای رایگان و تریالی هوش مصنوعی، با اعلان در پیوی تلگرام و عضویت
مبتنی بر تایید ادمین. منابع مصرفی رایگان: اجرا روی GitHub Actions، ذخیره‌سازی
در همین ریپو، و لایه استخراج روی فری‌تیر مدل‌های زبانی.

## لایه‌های کشف

- رجیستری ماشینی: دیف لیست مدل‌های رایگان OpenRouter و Hugging Face
- دیف صفحات pricing و free و students و startups حدود ۶۰ شرکت
- دیف کامیت ریپوهای curated مثل free-llm-api-resources
- RSS بلاگ‌های رسمی، خبرگزاری‌ها، AppSumo و Product Hunt
- Google News RSS با کوئری‌های هدفمند
- Reddit و Hacker News و کانال‌های عمومی تلگرام و Bluesky و Devpost

## انتشار عمومی داده

داده‌های تولیدشده در ریپوی خصوصی `freeai-data` می‌مانند، پس هر چیزی که بیرون از
اکشن‌ها باید بخواندشان (صفحهٔ سایت و بات روی Cloudflare Workers) از نسخهٔ عمومی
`docs/` در همین ریپو می‌خواند. `src/publish.py` بعد از هر hunt و recheck این‌ها را
هم می‌نویسد:

- `docs/offers.json` — لیست ساده (بات ورکر اول همین را از
  `raw.githubusercontent.com` می‌خواند، بدون توکن و بدون سقف rate limit)
- `docs/github-pages/offers.json` و `docs/github-pages/sites.json` — همان داده با
  شکل `{"offers": [...]}` که صفحه‌های سایت می‌خوانند
- `docs/sites.json` — همان لیست، برای ورکری که یک فایل می‌خواهد
- `docs/index.md` — جدول مارک‌داون آفرها

فقط فیلدهای عمومی در این کپی‌ها هست؛ `status` داخلی نوشته نمی‌شود (بات نبودنش را
«فعال» می‌خواند). اگر `DATA_REPO` روی ورکر تنظیم نشود، پیش‌فرض همین ریپوی عمومی است.

## صحت‌سنجی

زنده بودن لینک، تایید ادعا در صفحه مقصد، رسمی بودن دامنه، سن دامنه از RDAP،
هم‌پوشانی چند منبع، و بازخورد کاربران.
وضعیت‌ها: verified / likely / unconfirmed / suspicious / dead_link / expired

## هم‌زمانی و ذخیره‌سازی

ورک‌فلوها یک گروه `repo-write` مشترک ندارند:

- `freeai-bot` فقط برای ورک‌فلو `bot` است. بات هرگز به `state.sqlite` دست نمی‌زند،
  پس نه منتظر hunt می‌ماند و نه آن را عقب می‌اندازد.
- `freeai-db` برای `hunt`، `recheck` و `expand` است، چون هر سه همان فایل باینری
  `data/state.sqlite` را می‌نویسند و git نمی‌تواند باینری را merge کند. پس این سه
  ناچارند نوبتی اجرا شوند؛ ولی بات دیگر در این صف نیست و شکار را بلاک نمی‌کند.

نوشتن داده در `scripts/commit-data.sh` انجام می‌شود: rebase و در صورت تعارض، merge
با حفظ نسخهٔ همین اجرا، و در آخر خطای صریح اگر push انجام نشود. فقط فایل‌هایی که هر
ورک‌فلو واقعاً نوشته است stage می‌شوند تا تغییرات هم‌زمان از دست نروند.

`data/state.sqlite` کامل در هر اجرا کامیت می‌شود، بنابراین حجمش باید محدود بماند.
`main.run` هر ۱۲ ساعت (`RETENTION_HOURS`) تاریخ را پاک‌سازی و فایل را vacuum می‌کند:
متن مقاله‌ها بعد از ۷ روز (`BODY_KEEP_DAYS`) و ردیف‌ها و اسنپ‌شات صفحه‌ها بعد از ۴۵ و
۱۴ روز (`ITEM_KEEP_DAYS`، `SNAPSHOT_KEEP_DAYS`). صف استخراج و آفرها هرگز پاک نمی‌شوند.

## راه‌اندازی

1. در Settings > Secrets and variables > Actions مقادیر TELEGRAM_BOT_TOKEN و
   ADMIN_IDS را بگذارید. اختیاری: GEMINI_API_KEY، GROQ_API_KEY،
   CEREBRAS_API_KEY، OPENROUTER_API_KEY
2. در Settings > Actions > General گزینه Workflow permissions را روی
   Read and write بگذارید
3. در تب Actions ورک‌فلو hunt را یک‌بار دستی اجرا کنید

## اجرای محلی

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    python -m src.main
    python -m src.bot.poll
