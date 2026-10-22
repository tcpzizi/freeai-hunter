"""Stable offer identity + sanity checks on LLM-extracted offers.

Why this exists: the old key was md5(domain|type|value) where `value` is free
text from the LLM, so the same offer got a new id every time the wording or
the scraped price changed (together.ai showed up 7x with $0..$9). Pricing-page
diffs also leaked token / subscription prices in as "free credits".
"""
import re, json, hashlib, urllib.parse as up

AMOUNT = re.compile(r"(?:\$|usd\s?|us\$)\s?(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", re.I)

# something like "/ 1M tokens", "per hour", "/mo", "per seat" right after a price
UNIT_PRICE = re.compile(
    r"(?:/|\bper\b|\beach\b)\s*(?:1\s?|one\s)?(?:m\b|mtok|million|k\b|1k|thousand|"
    r"tokens?|toks?|chars?|characters?|requests?|reqs?|calls?|images?|imgs?|"
    r"seconds?|secs?|s\b|minutes?|mins?|hours?|hrs?|h\b|gpu|months?|mo\b|years?|"
    r"yr\b|seats?|users?|members?|editors?|queries|query|pages?|steps?|runs?|"
    r"generations?|videos?|songs?|words?)", re.I)

# words that mean "you get this for free", not "you pay this"
FREEISH = re.compile(r"\b(free|credits?|grant(ed)?|bonus|complimentary|no[- ]cost|"
                     r"trial|gift|voucher|coupon|promo|giveaway|waived)\b", re.I)

LOCALES = {"en", "en-us", "en-gb", "fa", "de", "fr", "es", "it", "pt", "pt-br",
           "ja", "ko", "zh", "zh-cn", "zh-tw", "ru", "tr", "ar", "nl", "pl", "id"}

# hosts where anyone can publish: path (and query) identify the offer
PLATFORM_KEY_HOSTS = {"github.com", "news.ycombinator.com", "news.google.com",
                      "reddit.com", "old.reddit.com", "x.com", "twitter.com",
                      "bsky.app", "t.me", "medium.com", "huggingface.co",
                      "producthunt.com", "appsumo.com", "youtube.com",
                      "apps.apple.com", "play.google.com", "devpost.com"}
DROP_Q = ("utm_", "ref", "fbclid", "gclid", "aff", "mc_cid", "mc_eid", "oc")


def amount_of(value):
    """First $ amount in value as float (k suffix handled), or None."""
    m = AMOUNT.search(str(value or ""))
    if not m:
        return None
    try:
        x = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return x * 1000 if m.group(2) else x


def _num(m):
    try:
        x = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return x * 1000 if m.group(2) else x


def is_unit_price(value, context=""):
    """True if `value` is really a list/token/subscription price, not a freebie."""
    v = str(value or "")
    if FREEISH.search(v):
        return False
    m = AMOUNT.search(v)
    if not m:
        return False
    if UNIT_PRICE.search(v[m.end():m.end() + 40]):
        return True
    amt = _num(m)
    if amt is None:
        return False
    hits = 0
    for line in str(context or "").split("\n"):
        for mm in AMOUNT.finditer(line):
            if _num(mm) != amt:
                continue
            hits += 1
            if FREEISH.search(line):
                return False
            if UNIT_PRICE.search(line[mm.end():mm.end() + 40]):
                return True
    return False


def sanitize(o, context=""):
    """Mutates and returns o. Kills unit prices, normalises $0 into a free plan."""
    if not o or not o.get("is_offer"):
        return o
    t = o.get("offer_type")
    if t in ("free_credits", "free_api", "free_trial", "other", "aggregator",
             "lifetime_deal") and is_unit_price(o.get("value"), context):
        o["is_offer"] = False
        o["reject_reason"] = "unit_price"
        return o
    if amount_of(o.get("value")) == 0:
        if t in ("free_credits", "free_api", "other", None):
            o["offer_type"] = "permanent_free_tier"
        o["value"] = "Free plan ($0)"
    return o


def _host(url):
    h = up.urlsplit(url or "").netloc.lower().split("@")[-1].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def norm_target(url):
    """host + normalised path; query kept only on platform hosts."""
    p = up.urlsplit(url or "")
    host = _host(url)
    segs = [s for s in (p.path or "").lower().split("/") if s]
    if segs and segs[0] in LOCALES:
        segs = segs[1:]
    if segs:
        segs[-1] = re.sub(r"\.(html?|php|aspx?)$", "", segs[-1])
    path = "/" + "/".join(segs)
    plat = host in PLATFORM_KEY_HOSTS or host.endswith(".devpost.com")
    q = ""
    if plat and p.query:
        keep = sorted((k, v) for k, v in up.parse_qsl(p.query)
                      if not k.lower().startswith(DROP_Q))
        q = "?" + up.urlencode(keep) if keep else ""
    return host + path + q


def offer_key(o, dom, signup_url=None):
    raw = "v2|" + str(dom or "").lower() + "|" + str(o.get("offer_type")) + "|" + \
          norm_target(signup_url or o.get("signup_url") or "")
    return hashlib.md5(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def source_family(src):
    """'pagediff:anthropic.com' -> 'pagediff', 'github/foo' -> 'github'."""
    s = str(src or "").lower().strip()
    return re.split(r"[:/]", s)[0] or "unknown"


def load_sources(raw):
    try:
        x = json.loads(raw or "[]")
        return set(x) if isinstance(x, list) else set()
    except Exception:
        return set()
