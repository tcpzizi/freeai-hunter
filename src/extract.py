import os, json, re, httpx
from . import dedup

SCHEMA_HINT = """You extract AI freebie offers. Reply with ONLY a JSON object:
{"is_offer":bool,
"offer_type":"free_api|free_credits|free_trial|student|startup|hackathon|permanent_free_tier|aggregator|lifetime_deal|other",
"vendor":str,"target":str,"value":str,"limits":str,
"requires_credit_card":true|false|null,"requires_edu":true|false|null,
"region":str|null,"signup_url":str|null,"expires_at":"YYYY-MM-DD"|null,
"model_class":"frontier|mid|small|unknown","confidence":0.0-1.0,
"why":"one short sentence in Persian"}
Rules:
- is_offer=true ONLY if a user can actually get something free / trial / credits.
  Pure news, opinions, tutorials, launches, benchmarks and job posts are is_offer=false.
- "value" is ONLY what the user gets for free, e.g. "$300 credits",
  "1,000 requests/day", "Pro free for 12 months". NEVER put a paid price there:
  token prices ("$0.50 / 1M tokens"), subscription prices ("$20/month") and
  per-seat prices are NOT offers. A pricing page that only lists paid prices
  is is_offer=false.
- Credits or allowances that come ONLY with a paid plan are NOT free offers
  ("PRO users get $2/month of credits", "Team plan includes $20 of usage",
  "$100/mo plan with more requests"): is_offer=false.
- A dollar amount in "value" must be written in the text as something the user
  receives (credit, grant, voucher, "in free credits"). If the text only shows
  that number as a plan or usage price, is_offer=false.
- Never answer "?" or an empty "value". If you cannot say what the user gets,
  is_offer=false.
- A free plan with no dollar amount: offer_type "permanent_free_tier" and
  describe the quota in "value" / "limits".
- For "[PRICING CHANGE]" items look ONLY at the added lines. Report an offer
  only if they introduce or change something free (free tier, free credits,
  trial, student / startup program). Price increases or cuts are not offers.
- "requires_credit_card" and "requires_edu": true or false ONLY when the text
  says so; otherwise null. Do not guess.
- "signup_url" is the vendor page where the user claims the offer if it appears
  in the text (not a news article or aggregator); otherwise null.
- "vendor" is the company giving the offer, not the site that reported it."""

PROVIDERS = [("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY"),
             ("cerebras", "CEREBRAS_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")]


def _oai(base, key, model, prompt):
    r = httpx.post(base + "/chat/completions",
                   headers={"Authorization": "Bearer " + key},
                   json={"model": model, "temperature": 0,
                         "response_format": {"type": "json_object"},
                         "messages": [{"role": "user", "content": prompt}]}, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(key, prompt):
    r = httpx.post("https://generativelanguage.googleapis.com/v1beta/models/"
                   "gemini-2.5-flash:generateContent",
                   headers={"x-goog-api-key": key},
                   json={"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0,
                                              "responseMimeType": "application/json"}},
                   timeout=60)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def llm_json(prompt):
    for name, env in PROVIDERS:
        key = os.getenv(env)
        if not key:
            continue
        try:
            if name == "gemini":
                txt = _gemini(key, prompt)
            elif name == "groq":
                txt = _oai("https://api.groq.com/openai/v1", key,
                           "llama-3.3-70b-versatile", prompt)
            elif name == "cerebras":
                txt = _oai("https://api.cerebras.ai/v1", key, "llama-3.3-70b", prompt)
            else:
                txt = _oai("https://openrouter.ai/api/v1", key,
                           "meta-llama/llama-3.3-70b-instruct:free", prompt)
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                return json.loads(m.group(0))
        except Exception as e:
            print(" llm[" + name + "] failed: " + str(e)[:120])
    return None


# only an explicit credit phrase, never a bare price
FREE_CREDIT = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:k\b)?\s*(?:in\s+|of\s+)?(?:free\s+|bonus\s+)?credits?\b"
    r"|\bfree\s+credits?\s+(?:worth|of)\s+(?:up\s+to\s+)?\$\s?\d[\d,]*(?:\.\d+)?\s*(?:k\b)?",
    re.I)


def regex_fallback(item):
    """Used only when no LLM answered. Conservative on purpose: the old version
    took the first $ amount in the text (often a plan price) and stored "?"
    when there was none, which filled the list with fake "free credits"."""
    t = str(item.get("title", "")) + " " + str(item.get("body", ""))
    m = FREE_CREDIT.search(t)
    if not m:
        return {"is_offer": False, "reject_reason": "no_llm_no_credit_phrase"}
    kind = "student" if re.search(r"\bstudents?\b", t, re.I) else "free_credits"
    return {"is_offer": True, "offer_type": kind, "vendor": "",
            "target": str(item.get("title") or "")[:90], "value": m.group(0).strip(),
            "limits": "", "requires_credit_card": None, "requires_edu": None,
            "region": None, "signup_url": item.get("url"), "expires_at": None,
            "model_class": "unknown", "confidence": 0.35,
            "why": "استخراج قاعده محور (LLM در دسترس نبود)"}


def extract(item):
    prompt = (SCHEMA_HINT + "\n\nSOURCE: " + str(item.get("source")) +
              "\nURL: " + str(item.get("url")) +
              "\nTITLE: " + str(item.get("title")) +
              "\nBODY:\n" + str(item.get("body") or "")[:6000])
    o, by = llm_json(prompt), "llm"
    if not isinstance(o, dict):
        o, by = regex_fallback(item), "regex"
    ctx = str(item.get("title") or "") + "\n" + str(item.get("body") or "")
    o = dedup.sanitize(o, ctx)
    o["extracted_by"] = by  # main.py re-queues "regex" rejects for the next run
    return o
