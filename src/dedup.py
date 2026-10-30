"""Stable offer identity + sanity checks on LLM-extracted offers.

Why this exists: the old key was md5(domain|type|value) where `value` is free
text from the LLM, so the same offer got a new id every time the wording or
the scraped price changed (together.ai showed up 7x with $0..$9). Pricing-page
diffs also leaked token / subscription prices in as "free credits".

v3 (price vs credit): a dollar amount only counts as a freebie when the words
right next to it say so ("$300 in free credits", "credits worth $25"). It is
rejected when it sits next to a billing unit ("$100/mo", "$0.50 / 1M tokens")
or when the credit only comes with a paid plan ("PRO users get $2 of credits").
The old check accepted the amount if "free" appeared anywhere on the same line,
and pricing pages are often one long line, so plan prices slipped through.
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
# words that mean "you get this for free" (kept for other callers)
FREEISH = re.compile(r"\b(free|credits?|grant(ed)?|bonus|complimentary|no[- ]cost|"
                     r"trial|gift|voucher|coupon|promo|giveaway|waived)\b", re.I)

# ---- price vs credit: judged only on the words right next to the amount ----
_PERIOD = r"(?:/|\bper\b|\ba\b|\beach\b|\bevery\b)\s*(?:month|mo|year|yr|day|week)\b\.?"
_CREDIT_ADJ = (r"(?:free|bonus|complimentary|included|monthly|compute|api|usage|"
               r"platform|inference|cloud|gpu|promotional|promo|starter|trial|"
               r"one[- ]time|signup|sign[- ]up)")
# "$300 in free credits", "$30 / month of credits", "$5 credit", "$300 free"
CREDIT_TAIL = re.compile(
    r"^\s*(?:" + _PERIOD + r"\s*)?(?:(?:in|of|worth\s+of)\s+)?"
    r"(?:" + _CREDIT_ADJ + r"\s+)*credits?\b"
    r"|^\s*(?:in\s+)?free\b(?!\s+(?:plan|tier|version|edition))", re.I)
# "free credits worth $25", "credits of up to $", "free $5", "a grant of $"
CREDIT_HEAD = re.compile(
    r"(?:\bcredits?\s+(?:worth|of)\s+(?:up\s+to\s+)?"
    r"|\bfree\s+(?:credits?\s+)?(?:worth\s+)?(?:of\s+)?(?:up\s+to\s+)?"
    r"|\b(?:grants?|bonus|voucher|coupon|gift|giveaway)\s+(?:of\s+|worth\s+)?"
    r"(?:up\s+to\s+)?)$", re.I)
_PAID_TIER = r"(?:pro|plus|premium|team|teams|business|enterprise|paid|paying|ultra|max)"
# a tier name right before the amount ("Pro: $2 of credits"); not "plus"/"max",
# which also mean "and" / "up to" ("$0 / month plus $30 in credits")
_BARE_TIER = r"(?:pro|premium|team|teams|business|enterprise|paid|ultra)"
# a credit that only comes with a paid plan: "PRO users get $2 of credits"
PERK_HEAD = re.compile(
    r"\b" + _PAID_TIER + r"\s+(?:plan\s+|tier\s+)?(?:users?|subscribers?|members?|"
    r"accounts?|customers?|seats?|plans?)\b[^$.]{0,30}$"
    r"|\b" + _BARE_TIER + r"\b\W{0,3}$"
    r"|\b(?:subscribers?|paid\s+plans?)\b[^$.]{0,40}$"
    r"|\b(?:upgrade|subscribe)\s+(?:to|and|for)\b[^$.]{0,40}$"
    # "PRO plan includes $2", "Team gets $20", "Enterprise comes with $"
    r"|\b" + _BARE_TIER + r"\b[^$.]{0,40}\b(?:includes?|included|gets?|receives?|"
    r"unlocks?|comes?\s+with|has|have|with)\b[^$.]{0,25}$", re.I)
PERK_TAIL = re.compile(
    r"^[^$.]{0,50}?\b(?:for|to|with|on)\s+(?:all\s+|the\s+|your\s+)?" + _PAID_TIER + r"\b"
    r"|^[^$.]{0,50}?\b(?:per\s+seat|each\s+seat|included\s+(?:in|with)\s+(?:the\s+|your\s+)?"
    + _PAID_TIER + r")\b", re.I)
# a billing unit right after the amount, or pricing words right before it
PAID_TAIL = re.compile(
    r"^\s*(?:usd\s*)?(?:" + UNIT_PRICE.pattern + r"|billed\b|monthly\b|annually\b|"
    r"yearly\b|a\s+(?:month|year)\b|/\s*(?:user|seat))", re.I)
PAID_HEAD = re.compile(
    r"\b(?:costs?|pay|paying|priced\s+at|price[sd]?\s+at|starting\s+(?:at|from)|"
    r"starts\s+at|from|only|just|upgrade\s+(?:to\s+\w+\s+)?(?:for|at))\s*$", re.I)
TRIAL_WORDS = re.compile(r"\b(trial|days?|weeks?)\b", re.I)

NEEDS_VALUE = {"free_credits", "free_api", "free_trial", "other", "aggregator",
               "lifetime_deal"}
EMPTY_VALUES = {"", "?", "??", "???", "-", "n/a", "na", "none", "null", "unknown",
                "tbd", "not specified"}

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
    return _num(m)


def _num(m):
    try:
        x = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return x * 1000 if m.group(2) else x


def classify_amount(text, start, end):
    """What the $ amount at text[start:end] is: 'free', 'perk', 'paid' or 'unknown'.
    Only the words between this amount and its neighbouring amounts count."""
    text = str(text or "")
    head = text[max(0, start - 60):start].rsplit("$", 1)[-1]
    tail = text[end:end + 60].split("$", 1)[0]
    if CREDIT_TAIL.match(tail) or CREDIT_HEAD.search(head):
        if PERK_HEAD.search(head) or PERK_TAIL.match(tail):
            return "perk"
        return "free"
    if PAID_TAIL.match(tail) or PAID_HEAD.search(head):
        return "paid"
    return "unknown"


def price_verdict(value, context="", use_value=True):
    """How the first $ amount in `value` is used in `context`.
    None: no amount. 'zero': $0. 'paid': shown as a price somewhere (wins, since
    a plan that costs $20 often "includes $20 of usage"). 'perk': a credit that
    needs a paid plan. 'free': shown as something you get. 'unknown': on the page
    but with no clear context. 'absent': not on the page (only if use_value=False;
    otherwise the wording of `value` itself decides)."""
    v = str(value or "")
    m = AMOUNT.search(v)
    if not m:
        return None
    amt = _num(m)
    if amt is None:
        return None
    if amt == 0:
        return "zero"
    ctx = str(context or "")
    kinds = [classify_amount(ctx, mm.start(), mm.end())
             for mm in AMOUNT.finditer(ctx) if _num(mm) == amt]
    for k in ("paid", "perk", "free"):
        if k in kinds:
            return k
    own = classify_amount(v, m.start(), m.end())
    if own in ("paid", "perk"):
        return own
    if kinds:
        return "unknown"
    return own if use_value else "absent"


def is_unit_price(value, context=""):
    """True if `value` is really a list/token/subscription price, not a freebie."""
    return price_verdict(value, context) == "paid"


def needs_value(offer_type):
    return offer_type in NEEDS_VALUE


def has_value(value):
    s = str(value or "").strip().lower()
    return s not in EMPTY_VALUES and bool(re.search(r"\w", s))


def _reject(o, reason):
    o["is_offer"] = False
    o["reject_reason"] = reason
    return o


def sanitize(o, context=""):
    """Mutates and returns o. Rejects prices, paid-plan perks and empty values;
    normalises $0 into a free plan."""
    if not o or not o.get("is_offer"):
        return o
    t = o.get("offer_type")
    val = o.get("value")
    if needs_value(t) and not has_value(val):
        return _reject(o, "no_value")
    # "14-day free trial, then $20/month": the $ is the later price, not the offer
    check_price = not (t == "free_trial" and TRIAL_WORDS.search(str(val or "")))
    verdict = price_verdict(val, context) if check_price else None
    if verdict == "paid":
        return _reject(o, "unit_price")
    if verdict == "perk":
        return _reject(o, "paid_plan_perk")
    if amount_of(val) == 0 and t in ("free_credits", "free_api", "other", None):
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
