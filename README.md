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

## صحت‌سنجی

زنده بودن لینک، تایید ادعا در صفحه مقصد، رسمی بودن دامنه، سن دامنه از RDAP،
هم‌پوشانی چند منبع، و بازخورد کاربران.
وضعیت‌ها: verified / likely / unconfirmed / suspicious / dead_link / expired

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
