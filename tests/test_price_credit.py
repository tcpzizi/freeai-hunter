"""Price vs free-credit regression tests.
Run from the repo root:  python -m unittest discover -s tests -t . -v
"""
import unittest
from unittest import mock
from src import dedup, extract

try:
    from src import verify
except ImportError:  # httpx / tldextract / yaml not installed
    verify = None

# pricing pages are often collapsed into one long line, with "free" somewhere on it
ONE_LINE_PRICING = ("Pricing Hobby Free Includes limited Agent requests, limited Tab "
                    "completions, free trial of Pro Pro $20 /mo Extended limits on "
                    "Agent Super $100 /mo Everything in Pro, plus more usage")
PERK_PAGE = ("Monthly credits. Free users: none. PRO users get $2.00 of included "
             "monthly credits. Team and Enterprise organizations get $2 of credits "
             "for each seat.")


def offer(value, t="free_credits"):
    return {"is_offer": True, "offer_type": t, "value": value}


class PriceVsCredit(unittest.TestCase):
    def rejected(self, o, reason):
        self.assertFalse(o["is_offer"])
        self.assertEqual(o.get("reject_reason"), reason)

    def test_plan_price_on_one_line_page(self):
        self.rejected(dedup.sanitize(offer("$100"), ONE_LINE_PRICING), "unit_price")

    def test_llm_calls_a_plan_price_credits(self):
        self.rejected(dedup.sanitize(offer("$100 credits"), ONE_LINE_PRICING), "unit_price")

    def test_marketplace_paid_tier(self):
        ctx = ("Mem0 is available on all plans: a free plan with usage limits, "
               "or the $20 per month plan for higher volume.")
        self.rejected(dedup.sanitize(offer("$20", "free_api"), ctx), "unit_price")

    def test_credit_that_needs_a_paid_plan(self):
        self.rejected(dedup.sanitize(offer("$2"), PERK_PAGE), "paid_plan_perk")
        self.rejected(dedup.sanitize(offer("$2"), "Pro: $2 of credits each month"),
                      "paid_plan_perk")

    def test_token_price(self):
        self.rejected(dedup.sanitize(offer("$0.50"), "Llama 70B $0.50 / 1M tokens"),
                      "unit_price")

    def test_price_after_trial_is_a_price(self):
        self.rejected(dedup.sanitize(offer("$49", "free_trial"),
                                     "3-day trial, then $49 per year"), "unit_price")

    def test_missing_value(self):
        self.rejected(dedup.sanitize(offer("?"), "free credits"), "no_value")
        self.rejected(dedup.sanitize(offer("", "free_api"), ""), "no_value")

    def test_real_credits_kept(self):
        cases = [("New accounts get $300 in free credits.", "$300"),
                 ("Claim free credits worth $25 today.", "$25"),
                 ("Startups receive up to $100k in credits.", "$100k credits"),
                 ("Starter $0 / month plus $30 / month in free credits for compute.",
                  "$30 / month in free credits")]
        for ctx, val in cases:
            with self.subTest(ctx=ctx):
                self.assertTrue(dedup.sanitize(offer(val), ctx)["is_offer"])

    def test_trial_with_later_price_kept(self):
        o = dedup.sanitize(offer("14-day free trial, then $20/month", "free_trial"),
                           "Try free for 14 days, then $20/month")
        self.assertTrue(o["is_offer"])

    def test_quota_without_dollars_kept(self):
        o = dedup.sanitize(offer("1,000 requests/day", "free_api"),
                           "1,000 requests/day free")
        self.assertTrue(o["is_offer"])

    def test_zero_becomes_free_tier(self):
        o = dedup.sanitize(offer("$0"), "Free $0 forever")
        self.assertEqual(o["offer_type"], "permanent_free_tier")

    def test_amount_missing_from_page(self):
        self.assertEqual(dedup.price_verdict("$300 credits", "no number here",
                                             use_value=False), "absent")
        self.assertEqual(dedup.price_verdict("$300 credits", "no number here"), "free")


class RegexFallback(unittest.TestCase):
    def run_extract(self, title):
        item = {"title": title, "body": "", "url": "https://example.com", "source": "t"}
        with mock.patch.object(extract, "llm_json", return_value=None):
            return extract.extract(item)

    def test_bare_price_is_not_an_offer(self):
        self.assertFalse(self.run_extract("Pro plan now $20/month")["is_offer"])

    def test_explicit_credit_phrase(self):
        o = self.run_extract("Get $50 in free credits for new devs")
        self.assertTrue(o["is_offer"])
        self.assertEqual(o["value"], "$50 in free credits")

    def test_paid_plan_credit_phrase(self):
        o = self.run_extract("PRO users get $2 in credits every month")
        self.assertFalse(o["is_offer"])
        self.assertEqual(o["reject_reason"], "paid_plan_perk")


class FakeResp:
    def __init__(self, text, url, code=200):
        self.text, self.url, self.status_code = text, url, code


@unittest.skipIf(verify is None, "verify dependencies not installed")
class VerifyPage(unittest.TestCase):
    VEND = {"official": {"cursor.com", "example.com"}, "platforms": {}}

    def run_verify(self, page, value, url="https://cursor.com/pricing"):
        filler = " lorem" * 100  # stay above the thin-page limit
        with mock.patch.object(verify.httpx, "get",
                               return_value=FakeResp(page + filler, url)):
            return verify.verify({"signup_url": url, "offer_type": "free_credits",
                                  "value": value}, self.VEND)

    def test_plan_price_is_not_offer(self):
        status, v = self.run_verify(ONE_LINE_PRICING, "$100")
        self.assertEqual(status, "not_offer")
        self.assertEqual(v["reject_reason"], "unit_price")

    def test_paid_plan_perk_is_not_offer(self):
        status, v = self.run_verify(PERK_PAGE, "$2", "https://example.com/pricing")
        self.assertEqual(status, "not_offer")
        self.assertEqual(v["reject_reason"], "paid_plan_perk")

    def test_real_credit_verified(self):
        status, _ = self.run_verify("Sign up and get $300 in free credits. No credit "
                                    "card required.", "$300", "https://example.com/start")
        self.assertEqual(status, "verified")

    def test_amount_not_on_page_is_only_likely(self):
        status, _ = self.run_verify("Start for free with generous free credits.", "$300",
                                    "https://example.com/start")
        self.assertEqual(status, "likely")

    def test_unknown_value_is_not_offer(self):
        status, _ = self.run_verify("free credits", "?")
        self.assertEqual(status, "not_offer")


if __name__ == "__main__":
    unittest.main()
