import json, time, httpx
from ..store import snapshot

UA = {"User-Agent": "freeai-hunter/1.0"}

def openrouter(c):
    out = []
    try:
        data = httpx.get("https://openrouter.ai/api/v1/models",
                         headers=UA, timeout=30).json()["data"]
    except Exception as e:
        print("openrouter failed: " + str(e)[:120])
        return out
    free = {}
    for m in data:
        p = m.get("pricing") or {}
        zero = lambda k: str(p.get(k, "1")) in ("0", "0.0", "-1")
        if (zero("prompt") and zero("completion")) or ":free" in m.get("id", ""):
            free[m["id"]] = {"name": m.get("name"), "ctx": m.get("context_length")}
    changed, old = snapshot(c, "openrouter:free", json.dumps(free, sort_keys=True))
    if changed and old:
        old_d = json.loads(old)
        for mid, meta in free.items():
            if mid in old_d:
                continue
            u = "https://openrouter.ai/" + mid
            out.append({"id": "or:" + mid, "url": u, "canon_url": u,
                "title": "[NEW FREE MODEL] " + str(meta.get("name") or mid) +
                         " - ctx " + str(meta.get("ctx")),
                "body": "OpenRouter: model " + mid + " is now priced at zero. "
                        "Free OpenAI-compatible endpoint, no payment required.",
                "source": "openrouter-registry", "source_type": "registry",
                "published_at": int(time.time()), "prefilter": 9.0,
                "hard_signal": True})
    print("openrouter: " + str(len(free)) + " free models, " + str(len(out)) + " new")
    return out

def huggingface(c):
    out = []
    try:
        r = httpx.get("https://huggingface.co/api/models", headers=UA, timeout=30,
                      params={"inference": "warm", "sort": "createdAt",
                              "direction": -1, "limit": 100})
        ids = sorted(str(m.get("modelId") or m.get("id")) for m in r.json())
    except Exception as e:
        print("hf failed: " + str(e)[:120])
        return out
    changed, old = snapshot(c, "hf:warm", json.dumps(ids))
    if changed and old:
        for mid in sorted(set(ids) - set(json.loads(old))):
            u = "https://huggingface.co/" + mid
            out.append({"id": "hf:" + mid, "url": u, "canon_url": u,
                "title": "[HF serverless] " + mid + " available on free inference",
                "body": "Hugging Face serverless inference free tier: new warm model.",
                "source": "hf-registry", "source_type": "registry",
                "published_at": int(time.time()), "prefilter": 6.0,
                "hard_signal": True})
    print("huggingface: " + str(len(out)) + " new warm models")
    return out
