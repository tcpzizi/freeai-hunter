import re

POS = {
 r"\bfree (credits?|tier|trial|access|plan|api|quota|for students)\b": 3.0,
 r"\bno credit card\b": 3.0,
 r"\$\s?\d{2,5}\s*(in\s*)?(free\s*)?credits?\b": 3.0,
 r"\b(giving away|giveaway|redeem|promo ?code|coupon|voucher)\b": 2.0,
 r"\b(free|complimentary)\s+(gpu|inference|tokens?)\b": 2.5,
 r"\b(unlimited|generous)\s+free\b": 2.0,
 r"\b(student|academic|hackathon|research grant)\b": 1.5,
 r"\b(early access|beta|waitlist|preview)\b[^.]{0,60}\bfree\b": 2.0,
 r"\b(limited time|expires|until \w+ \d{1,2})\b": 1.2,
 r"\b(api key|endpoint|openai[- ]compatible)\b": 1.0,
 r"\b(pro|plus|premium)\b[^.]{0,40}\b(free|\d+ months?)\b": 1.8,
}
NEG = {
 r"\b(cracked?|nulled|keygen|generator|mod apk|bypass paywall)\b": -6.0,
 r"\b(nsfw|casino|betting|onlyfans)\b": -6.0,
 r"\b(hiring|job opening|we are looking for)\b": -2.5,
 r"\bhow to (get|use) chatgpt\b": -1.5,
 r"\b(tier list|my opinion|review of)\b": -1.0,
}
AI_CTX = re.compile(r"\b(ai|llm|gpt|claude|gemini|llama|qwen|deepseek|mistral|grok|"
                    r"model|inference|tokens?|agent|copilot|diffusion|tts|stt|"
                    r"embedding)\b", re.I)

THRESHOLD = 3.0

def prefilter(title, body=""):
    t = (str(title) + "\n" + str(body or ""))[:6000]
    s = 0.0
    for p, w in POS.items():
        if re.search(p, t, re.I):
            s += w
    for p, w in NEG.items():
        if re.search(p, t, re.I):
            s += w
    if not AI_CTX.search(t):
        s -= 3.0
    return round(s, 2)
