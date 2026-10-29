"""Credit-card requirement: yes / no / unknown must stay three different answers.
Run from the repo root:  python -m unittest discover -s tests -t . -v
"""
import os, sys, tempfile, types, unittest
from unittest import mock

_fake = types.ModuleType("src.collectors")
for _n in ("model_registry", "pagediff", "github_watch", "rss", "googlenews", "reddit",
           "hackernews", "tgchannel", "bluesky", "devpost"):
    setattr(_fake, _n, types.ModuleType("src.collectors." + _n))
sys.modules.setdefault("src.collectors", _fake)

from src import store, main, recheck  # noqa: E402
from src.bot import render  # noqa: E402

try:
    from src import verify
except ImportError:
    verify = None


@unittest.skipIf(verify is None, "verify dependencies not installed")
class PageSays(unittest.TestCase):
    def test_no_card_is_not_read_as_card_required(self):
        self.assertIs(verify.cc_status("Start free. No credit card required."), False)
        self.assertIs(verify.cc_status("Try it without a credit card"), False)

    def test_card_required(self):
        self.assertIs(verify.cc_status("A credit card is required to start the trial"), True)
        self.assertIs(verify.cc_status("Add a payment method to activate credits"), True)

    def test_not_mentioned_is_unknown(self):
        self.assertIsNone(verify.cc_status("Get $300 in free credits"))

    def test_both_is_unknown(self):
        self.assertIsNone(verify.cc_status("Free: no credit card required. Pro: add a "
                                           "payment method."))


class Tri(unittest.TestCase):
    def test_values(self):
        self.assertEqual([main._tri(x) for x in (True, False, None, "false", "maybe")],
                         [1, 0, None, 0, None])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = self.tmp.name
        self.patch = mock.patch.multiple(store, DB=os.path.join(p, "s.sqlite"),
                                         DELIV_F=os.path.join(p, "d.json"),
                                         FEEDB_F=os.path.join(p, "f.json"))
        self.patch.start()
        self.c = store.conn()

    def tearDown(self):
        self.c.close()
        self.patch.stop()
        self.tmp.cleanup()

    def cc(self, oid=None):
        q = "SELECT requires_cc FROM offers" + (" WHERE id=?" if oid else "")
        self.c.commit()
        return self.c.execute(q, (oid,) if oid else ()).fetchone()[0]


class Stored(Base):
    def hunt(self, llm_cc, page_cc):
        it = {"id": "h:1", "url": "https://example.com/a", "source": "rss:x"}
        o = {"is_offer": True, "offer_type": "free_credits", "value": "$5 in credits",
             "requires_credit_card": llm_cc}
        with mock.patch.object(main.verify, "domain", return_value="example.com"), \
                mock.patch.object(main.verify, "verify",
                                  return_value=("verified", {"cc_required": page_cc})), \
                mock.patch.object(main.score, "score", return_value=30.0):
            main.handle_offer(self.c, {"official": set(), "platforms": {}}, it, o)
        return self.cc()

    def test_unknown_stays_unknown(self):
        self.assertIsNone(self.hunt(None, None))

    def test_page_wins_over_extractor(self):
        self.assertEqual(self.hunt(False, True), 1)
        self.c.execute("DELETE FROM offers")
        self.assertEqual(self.hunt(True, False), 0)

    def test_extractor_used_when_page_is_silent(self):
        self.assertEqual(self.hunt(True, None), 1)

    def test_unknown_does_not_erase_known(self):
        store.upsert_offer(self.c, {"id": "o1", "score": 10, "requires_cc": 1})
        store.upsert_offer(self.c, {"id": "o1", "score": 20, "requires_cc": None})
        self.assertEqual(self.cc("o1"), 1)
        store.upsert_offer(self.c, {"id": "o1", "score": 30, "requires_cc": 0})
        self.assertEqual(self.cc("o1"), 0)

    def test_old_values_reset_once(self):
        store.upsert_offer(self.c, {"id": "o1", "score": 1, "requires_cc": 0})
        self.c.execute("DELETE FROM meta WHERE k='cc_v1'")
        store.migrate_cc_v1(self.c)
        self.assertIsNone(self.cc("o1"))
        store.upsert_offer(self.c, {"id": "o1", "score": 1, "requires_cc": 1})
        store.migrate_cc_v1(self.c)  # already done: keeps the new value
        self.assertEqual(self.cc("o1"), 1)


class Recheck(Base):
    def run_recheck(self, page_cc):
        with mock.patch.object(recheck.verify, "load_vendors", return_value={}), \
                mock.patch.object(recheck.verify, "verify",
                                  return_value=("verified", {"cc_required": page_cc})), \
                mock.patch.object(recheck.store, "conn", side_effect=store.conn), \
                mock.patch.object(recheck.publish, "build"):
            recheck.main()

    def add(self, cc):
        store.upsert_offer(self.c, {"id": "o1", "score": 1, "requires_cc": cc,
                                    "status": "active", "offer_type": "free_credits",
                                    "value": "$5 in credits",
                                    "signup_url": "https://example.com"})
        self.c.commit()

    def test_page_answer_is_saved(self):
        self.add(None)
        self.run_recheck(False)
        self.assertEqual(self.cc("o1"), 0)

    def test_silent_page_keeps_value(self):
        self.add(1)
        self.run_recheck(None)
        self.assertEqual(self.cc("o1"), 1)

    def test_unknown_passed_as_unknown(self):
        self.add(None)
        seen = {}

        def fake_verify(o, vend, corr):
            seen["cc"] = o["requires_credit_card"]
            return "verified", {}
        with mock.patch.object(recheck.verify, "load_vendors", return_value={}), \
                mock.patch.object(recheck.verify, "verify", side_effect=fake_verify), \
                mock.patch.object(recheck.store, "conn", side_effect=store.conn), \
                mock.patch.object(recheck.publish, "build"):
            recheck.main()
        self.assertIsNone(seen["cc"])


class Message(unittest.TestCase):
    def text(self, **o):
        base = {"vendor": "x", "offer_type": "free_credits", "value": "$5", "score": 30}
        base.update(o)
        return render.offer_text(base)

    def test_card_required_warns(self):
        self.assertIn("کارت بانکی لازم", self.text(requires_cc=1))

    def test_no_card_only_when_page_said_so(self):
        self.assertIn("بدون نیاز به کارت", self.text(requires_cc=0, verify={"cc_required": False}))
        self.assertNotIn("بدون نیاز به کارت", self.text(requires_cc=0, verify={}))
        self.assertNotIn("کارت", self.text(requires_cc=None))


if __name__ == "__main__":
    unittest.main()
