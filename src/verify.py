import re, time, datetime as dt, urllib.parse as up
import httpx, tldextract, yaml
from . import dedup

_EX = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)
UA = {"User-Agent": "Mozilla/5.0 (compatible; freeai-hunter/1.0)"}
SCAM = re.compile(r"(free[- ]?(gift|key|token)[- ]?generator|100% working|"
                  r"unlimited chatgpt plus free|linkvertise|adf\.ly|cutt\.ly|"
                  r"shorte\.st)", re.I)
# Only phrases that actually promise something free. The old regex matched
# "sign up" / "get started" / bare "free", i.e. every SaaS page on earth.
CLAIM = re.compile(
    r"(free (credits?|tier|plan|trial|access|usage|quota|api|tokens?|requests?|"
    r"compute|gpu|inference|for (students|startups|developers|everyone|life))|"
    r"no credit card|without a credit card|start (for )?free|try (it )?(for )?free|"
    r"get started (for )?free|sign up (for )?free|"
    r"\$\s?\d[\d,]*\s*(k\s*)?(in\s*)?(free\s*)?credits?|credits? (worth|of) (up to )?\$|"
    r"\d[\d,]*\s*(free\s*)?(requests?|tokens?|credits?|minutes?|hours?|messages?)\s*"
    r"(per|/|a|each)\s*(day|month|minute|week)|"
    r"\bfree\b[^.\n]{0,40}\b(students?|startups?|educators?)\b|"
    r"startups? program|for startups|student (plan|pack|discount|offer|program)|"
    r"\$0(?![.,]?\d)|\bfree forever\b)", re.I)
CC_REQ = re.compile(r"(credit card (is )?required|card details (are )?required|"
                    r"billing information (is )?required|add a payment method|"
                    r"payment method (is )?required|requires? a (valid )?(credit|debit) card)",
                    re.I)
# "No credit card required" contains "credit card required": strip these first
NO_CC = re.compile(r"(no (credit |debit |payment )?cards? (is |are )?(required|needed|necessary)|"
                   r"no credit card|without (a |any )?(credit |debit )?card|"
                   r"(credit |debit )?card (is )?not required|"
                   r"no payment (method|details|info(rmation)?) (is )?(required|needed))", re.I)
GONE = re.compile(r"(offer (has )?(expired|ended)|no longer available|"
                  r"promotion .{0,20}ended|this domain is for sale|"
                  r"waitlist is closed)", re.I)
BLOCKED = {401, 403, 429, 503}  # bot walls / rate limits, not dead links
DEAD = {404, 410}
THIN_WORDS = 80  # fewer words = JS shell, can't judge claim


def domain(url):
    e = _EX(url or "")
    return ".".join(p for p in [e.domain, e.suffix] if p)


def load_vendors(path="config/vendors.yaml"):
    cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
    plat = {}
    for host, prefixes in (cfg.get("platform_hosts") or {}).items():
        plat[str(host).lower()] = [str(p).lower() for p in (prefixes or [])]
    return {"official": set(cfg.get("official_domains") or []), "platforms": plat}


def _vend(v):
    if isinstance(v, dict) and "official" in v:
        return v
    return {"official": set(v or []), "platforms": {}}


def platform_rule(url, vend):
    """Return the official-path list if url is on a user-content platform, else None."""
    host = dedup._host(url)
    plat = vend["platforms"]
    if host in plat:
        return plat[host]
    for ph, prefixes in plat.items():
        if ph.startswith("*.") and (host == ph[2:] or host.endswith(ph[1:])):
            return prefixes
    return None


def official_status(url, vend):
    """(is_official, is_platform)"""
    rule = platform_rule(url, vend)
    if rule is not None:
        path = (up.urlsplit(url).path or "/").lower()
        return any(path == p or path.startswith(p.rstrip("/") + "/") for p in rule), True
    return domain(url) in vend["official"], False


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


def page_text(html):
    text = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)[:200000]


EDU_REQ = re.compile(r"(\.edu (email|e-mail|address|account)|"
                     r"(school|university|college|student|academic) (email|e-mail|address) "
                     r"(is )?(required|needed)|verify (your )?(student|academic) status|"
                     r"student verification|proof of (enrollment|student status)|"
                     r"requires? (a |an )?(valid )?(student|school|university|\.edu) "
                     r"(email|e-mail|id)|github student developer pack)", re.I)
# "No .edu email required" contains ".edu email": strip these first
NO_EDU = re.compile(r"(no (\.edu|student|school|university|academic) (email|e-mail|address|"
                    r"verification|id) (is )?(required|needed|necessary)|"
                    r"without (a |an )?(\.edu|student|school|university) (email|e-mail|address)|"
                    r"(\.edu|student|school|university) (email|e-mail) (is )?not required|"
                    r"(any|personal) email (address )?works)", re.I)


def edu_status(text):
    """Same three answers as cc_status, for a school / .edu email requirement."""
    text = str(text or "")
    no = bool(NO_EDU.search(text))
    yes = bool(EDU_REQ.search(NO_EDU.sub(" ", text)))
    if yes != no:
        return yes
    return None


def cc_status(text):
    """True: the page says a card is needed. False: it says no card is needed.
    None: it says neither, or both (e.g. one plan each). The old check returned
    True/False only, so "not mentioned" was stored as "no card needed", and
    "No credit card required" itself matched "credit card required"."""
    text = str(text or "")
    no = bool(NO_CC.search(text))
    yes = bool(CC_REQ.search(NO_CC.sub(" ", text)))
    if yes != no:
        return yes
    return None


def value_on_page(value, text):
    """None if value has no $ amount (or is $0). True only when the page shows the
    amount as something you get; False when it is missing or not clearly free.
    (The old check accepted the amount anywhere, so a $100/mo plan price
    "verified" a $100 credit claim.)"""
    verdict = dedup.price_verdict(value, text, use_value=False)
    if verdict in (None, "zero"):
        return None
    return verdict == "free"


def verify(offer, vendors, corroborations=1):
    vend = _vend(vendors)
    url = offer.get("signup_url") or offer.get("url")
    v = {"http": None, "claim_found": False, "value_found": None, "thin_page": False,
         "cc_required": None, "edu_required": None, "domain": None, "final_url": None,
         "domain_age_days": None, "official_domain": False, "platform": False,
         "scam_flag": False, "price_verdict": None, "corroborations": corroborations,
         "checked_at": int(time.time())}
    if dedup.needs_value(offer.get("offer_type")) and not dedup.has_value(offer.get("value")):
        v["reject_reason"] = "no_value"
        return "not_offer", v
    if not url:
        return "unverifiable", v
    v["scam_flag"] = bool(SCAM.search(url))
    final = url
    try:
        r = httpx.get(url, headers=UA, timeout=25, follow_redirects=True)
        v["http"] = r.status_code
        final = str(r.url)
        text = page_text(r.text)
        v["thin_page"] = len(text.split()) < THIN_WORDS
        v["claim_found"] = bool(CLAIM.search(text))
        v["price_verdict"] = dedup.price_verdict(offer.get("value"), text, use_value=False)
        v["value_found"] = (None if v["price_verdict"] in (None, "zero")
                            else v["price_verdict"] == "free")
        v["cc_required"] = cc_status(text)
        v["edu_required"] = edu_status(text)
        v["scam_flag"] = v["scam_flag"] or bool(SCAM.search(text))
        if r.status_code < 400 and GONE.search(text):
            v["final_url"] = final
            return "expired", v
        # the page shows this amount as a price, or as a credit that needs a paid plan
        if r.status_code < 400 and v["price_verdict"] in ("paid", "perk"):
            v["final_url"] = final
            v["reject_reason"] = ("unit_price" if v["price_verdict"] == "paid"
                                  else "paid_plan_perk")
            return "not_offer", v
    except httpx.ConnectError as e:
        v["error"] = "connect: " + str(e)[:180]
    except Exception as e:
        v["error"] = str(e)[:200]
    # judge the page we actually landed on, not the link we were given
    v["final_url"] = final
    v["domain"] = domain(final)
    v["official_domain"], v["platform"] = official_status(final, vend)
    if not v["official_domain"] and not v["platform"]:
        v["domain_age_days"] = domain_age_days(v["domain"])
    age = v["domain_age_days"]
    if v["scam_flag"]:
        return "suspicious", v
    if v["http"] in DEAD or (v["http"] and v["http"] >= 500 and v["http"] not in BLOCKED):
        return "dead_link", v
    if str(v.get("error", "")).startswith("connect:"):
        return "dead_link", v
    if v["http"] in BLOCKED or v["http"] is None or v["thin_page"]:
        # couldn't read the page: only independent sources can lift it
        return ("likely" if corroborations >= 2 else "unverifiable"), v
    claim_ok = v["claim_found"] and v["value_found"] is not False
    if v["official_domain"] and claim_ok:
        return "verified", v
    if v["official_domain"] and v["claim_found"]:
        return "likely", v  # official page, but the $ amount isn't shown as free
    if corroborations >= 2 and v["claim_found"]:
        return "likely", v
    if not v["platform"] and claim_ok and age is not None and age > 180:
        return "likely", v
    if age is not None and age < 45:
        return "suspicious", v
    return "unconfirmed", v
