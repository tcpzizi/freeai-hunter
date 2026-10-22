import re, time, datetime as dt, httpx, tldextract

_EX = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)
UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}

SCAM = re.compile(r"(free[- ]?(gift|key|token)[- ]?generator|100% working|"
                  r"unlimited chatgpt plus free|linkvertise|adf\.ly|cutt\.ly|"
                  r"shorte\.st)", re.I)
CLAIM = re.compile(r"(free|trial|credits?|no credit card|\$\s?\d+|students?|quota|"
                   r"rate limit|sign ?up|get started)", re.I)
CC_REQ = re.compile(r"(credit card required|card details required|"
                    r"billing information required|add a payment method)", re.I)
GONE = re.compile(r"(offer (has )?(expired|ended)|no longer available|"
                  r"promotion .{0,20}ended|this domain is for sale|"
                  r"waitlist is closed)", re.I)

def domain(url):
    e = _EX(url or "")
    return ".".join(p for p in [e.domain, e.suffix] if p)

def domain_age_days(d):
    try:
        r = httpx.get("https://rdap.org/domain/" + d, timeout=15, follow_redirects=True)
        for ev in r.json().get("events", []):
            if ev.get("eventAction") == "registration":
                reg = dt.datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
                return (dt.datetime.now(dt.timezone.utc) - reg).days
    except Exception:
        pass
    return None

def verify(offer, official_domains, corroborations=1):
    url = offer.get("signup_url") or offer.get("url")
    v = {"http": None, "claim_found": False, "cc_required": None, "domain": None,
         "domain_age_days": None, "official_domain": False, "scam_flag": False,
         "corroborations": corroborations, "checked_at": int(time.time())}
    if not url:
        return "unverifiable", v
    d = domain(url)
    v["domain"] = d
    v["official_domain"] = d in official_domains
    v["scam_flag"] = bool(SCAM.search(url))
    try:
        r = httpx.get(url, headers=UA, timeout=25, follow_redirects=True)
        v["http"] = r.status_code
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)[:200000]
        v["claim_found"] = bool(CLAIM.search(text))
        v["cc_required"] = bool(CC_REQ.search(text))
        v["scam_flag"] = v["scam_flag"] or bool(SCAM.search(text))
        if GONE.search(text):
            return "expired", v
    except Exception as e:
        v["error"] = str(e)[:200]
    if not v["official_domain"]:
        v["domain_age_days"] = domain_age_days(d)
    if v["scam_flag"]:
        return "suspicious", v
    if v["http"] and v["http"] >= 400:
        return "dead_link", v
    if v["official_domain"] and v["claim_found"]:
        return "verified", v
    if v["claim_found"] and (v["domain_age_days"] or 9999) > 180:
        return "likely", v
    if corroborations >= 2 and v["claim_found"]:
        return "likely", v
    if v["domain_age_days"] is not None and v["domain_age_days"] < 45:
        return "suspicious", v
    return "unconfirmed", v
