"""School / .edu email requirement: yes / no / unknown must stay three answers.
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

try:
    from src import verify
except ImportError:
    verify = None


@unittest.skipIf(verify is None, "verify dependencies not installed")
class PageSays(unittest.TestCase):
    def test_required(self):
        self.assertIs(verify.edu_status("Sign up with your .edu email address"), True)
        self.assertIs(verify.edu_status("Verify your student status to unlock Pro"), True)

    def test_not_required(self):
        self.assertIs(verify.edu_status("No .edu email required, any email works"), False)
        self.assertIs(verify.edu_status("Join without a school email"), False)

    def test_not_mentioned_is_unknown(self):
        self.assertIsNone(verify.edu_status("Get $300 in free credits"))

    def test_verify_reports_it(self):
        class R:
            status_code, url = 200, "https://example.com/students"
            text = "Students get Pro free. Verify your student status." + " lorem" * 100
        with mock.patch.object(verify.httpx, "get", return_value=R()):
            _, v = verify.verify({"signup_url": R.url, "offer_type": "student",
                                  "value": "Pro free"},
                                 {"official": {"example.com"}, "platforms": {}})
        self.assertIs(v["edu_required"], True)


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

    def edu(self, oid=None):
        self.c.commit()
        q = "SELECT requires_edu FROM offers" + (" WHERE id=?" if oid else "")
        return self.c.execute(q, (oid,) if oid else ()).fetchone()[0]


class Stored(Base):
    def hunt(self, llm_edu, page_edu):
        it = {"id": "h:1", "url": "https://example.com/a", "source": "rss:x"}
        o = {"is_offer": True, "offer_type": "student", "value": "Pro free",
             "requires_edu": llm_edu}
        with mock.patch.object(main.verify, "domain", return_value="example.com"), \
                mock.patch.object(main.verify, "verify",
                                  return_value=("verified", {"edu_required": page_edu})), \
                mock.patch.object(main.score, "score", return_value=30.0):
            main.handle_offer(self.c, {"official": set(), "platforms": {}}, it, o)
        return self.edu()

    def test_unknown_stays_unknown(self):
        self.assertIsNone(self.hunt(None, None))

    def test_page_wins_over_extractor(self):
        self.assertEqual(self.hunt(False, True), 1)
        self.c.execute("DELETE FROM offers")
        self.assertEqual(self.hunt(True, False), 0)

    def test_extractor_used_when_page_is_silent(self):
        self.assertEqual(self.hunt("true", None), 1)

    def test_unknown_does_not_erase_known(self):
        store.upsert_offer(self.c, {"id": "o1", "score": 10, "requires_edu": 1})
        store.upsert_offer(self.c, {"id": "o1", "score": 20, "requires_edu": None})
        self.assertEqual(self.edu("o1"), 1)

    def test_migration_resets_only_zero(self):
        store.upsert_offer(self.c, {"id": "o0", "score": 1, "requires_edu": 0})
        store.upsert_offer(self.c, {"id": "o1", "score": 1, "requires_edu": 1})
        self.c.execute("DELETE FROM meta WHERE k='edu_v1'")
        store.migrate_edu_v1(self.c)
        self.assertIsNone(self.edu("o0"))
        self.assertEqual(self.edu("o1"), 1)


class Recheck(Base):
    def run_recheck(self, edu, page_edu):
        store.upsert_offer(self.c, {"id": "o1", "score": 1, "requires_edu": edu,
                                    "status": "active", "offer_type": "student",
                                    "value": "Pro free", "signup_url": "https://example.com"})
        self.c.commit()
        seen = {}

        def fake_verify(o, vend, corr):
            seen["edu"] = o["requires_edu"]
            return "verified", {"edu_required": page_edu}
        with mock.patch.object(recheck.verify, "load_vendors", return_value={}), \
                mock.patch.object(recheck.verify, "verify", side_effect=fake_verify), \
                mock.patch.object(recheck.store, "conn", side_effect=store.conn), \
                mock.patch.object(recheck.publish, "build"):
            recheck.main()
        return seen["edu"], self.edu("o1")

    def test_unknown_passed_as_unknown_and_page_saved(self):
        self.assertEqual(self.run_recheck(None, False), (None, 0))

    def test_silent_page_keeps_value(self):
        self.assertEqual(self.run_recheck(1, None), (True, 1))


if __name__ == "__main__":
    unittest.main()
