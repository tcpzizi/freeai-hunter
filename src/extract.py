import os, json, re, httpx

SCHEMA_HINT = """You extract AI freebie offers. Reply with ONLY a JSON object:
{"is_offer":bool,
 "offer_type":"free_api|free_credits|free_trial|student|startup|hackathon|permanent_free_tier|aggregator|lifetime_deal|other",
 "vendor":str,"target":str,"value":str,"limits":str,
 "requires_credit_card":true|false|null,"requires_edu":true|false|null,
 "region":str|null,"signup_url":str|null,"expires_at":"YYYY-MM-DD"|null,
 "model_class":"frontier|mid|small|unknown","confidence":0.0-1.0,
 "why":"one short sentence in Persian"}
Set is_offer=true ONLY if a user can actually get something free / trial / credits.
Pure news, opinions, tutorials and job posts must be is_offer=false."""

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
            print("  llm[" + name + "] failed: " + str(e)[:120])
    return None

def regex_fallback(item):
    t = str(item.get("title", "")) + " " + str(item.get("body", ""))
    val = re.search(r"\$\s?\d{1,5}(\s*(in\s*)?credits?)?", t, re.I)
    kind = "free_api" if re.search(r"\bapi\b", t, re.I) else "other"
    if re.search(r"\bstudents?\b", t, re.I):
        kind = "student"
    elif re.search(r"\bcredits?\b", t, re.I):
        kind = "free_credits"
    elif re.search(r"\btrial\b", t, re.I):
        kind = "free_trial"
    return {"is_offer": True, "offer_type": kind, "vendor": "",
            "target": item["title"][:90], "value": val.group(0) if val else "?",
            "limits": "", "requires_credit_card": None, "requires_edu": None,
            "region": None, "signup_url": item["url"], "expires_at": None,
            "model_class": "unknown", "confidence": 0.35,
            "why": "استخراج قاعده محور (LLM در دسترس نبود)"}

def extract(item):
    prompt = (SCHEMA_HINT + "\n\nSOURCE: " + str(item.get("source")) +
              "\nURL: " + str(item.get("url")) +
              "\nTITLE: " + str(item.get("title")) +
              "\nBODY:\n" + str(item.get("body") or "")[:6000])
    return llm_json(prompt) or regex_fallback(item)
