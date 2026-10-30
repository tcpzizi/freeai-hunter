"""Remaining fixes: paid-plan perks phrased with "includes", and legacy $0 rows.
Run from the repo root:  python -m unittest discover -s tests -t . -v
"""
import os, tempfile, unittest
from unittest import mock
from src import dedup, store


def offer(value, t="free_credits"):
    return {"is_offer": True, "offer_type": t, "value": value}


class PaidPerks(unittest.TestCase):
    def rejected(self, ctx, value="$2"):
        o = dedup.sanitize(offer(value), ctx)
        self.assertFalse(o["is_offer"], ctx)
        self.assertEqual(o["reject_reason"], "paid_plan_perk")

    def test_includes_phrasing(self):
        self.rejected("PRO plan includes $2 of monthly credits")
        self.rejected("Team gets $20 in credits for each member", "$20")
        self.rejected("Enterprise comes with $50 of usage credits", "$50")

    def test_free_users_line_does_not_poison_a_real_credit(self):
        ctx = "Pro $20/mo with more limits. Free users get $5 in free credits."
        self.assertTrue(dedup.sanitize(offer("$5"), ctx)["is_offer"])

    def test_plain_credit_still_kept(self):
        self.assertTrue(dedup.sanitize(offer("$300"), "New accounts get $300 in free credits")["is_offer"])


class ZeroRows(unittest.TestCase):
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

    def row(self, oid):
        self.c.commit()
        return self.c.execute("SELECT offer_type, value, status, verify_status FROM offers "
                              "WHERE id=?", (oid,)).fetchone()

    def test_archive_when_twin_exists_else_convert(self):
        store.upsert_offer(self.c, {"id": "a", "score": 1, "domain": "together.ai",
                                    "offer_type": "free_credits", "value": "$0"})
        store.upsert_offer(self.c, {"id": "b", "score": 1, "domain": "together.ai",
                                    "offer_type": "permanent_free_tier",
                                    "value": "Free plan ($0)"})
        store.upsert_offer(self.c, {"id": "c", "score": 1, "domain": "solo.ai",
                                    "offer_type": "free_credits", "value": "$0"})
        store.upsert_offer(self.c, {"id": "d", "score": 1, "domain": "x.ai",
                                    "offer_type": "free_credits", "value": "$5 in credits"})
        self.c.execute("DELETE FROM meta WHERE k='zero_v1'")
        store.migrate_zero_v1(self.c)
        self.assertEqual(self.row("a")["status"], "archived")
        self.assertEqual(self.row("a")["verify_status"], "not_offer")
        self.assertEqual(self.row("b")["status"], "active")
        self.assertEqual(tuple(self.row("c"))[:2], ("permanent_free_tier", "Free plan ($0)"))
        self.assertEqual(self.row("d")["offer_type"], "free_credits")  # real credit untouched

    def test_runs_once(self):
        store.upsert_offer(self.c, {"id": "a", "score": 1, "domain": "x.ai",
                                    "offer_type": "free_credits", "value": "$0"})
        self.c.execute("DELETE FROM meta WHERE k='zero_v1'")
        store.migrate_zero_v1(self.c)
        store.upsert_offer(self.c, {"id": "e", "score": 1, "domain": "y.ai",
                                    "offer_type": "free_credits", "value": "$0"})
        store.migrate_zero_v1(self.c)  # already done
        self.assertEqual(self.row("e")["offer_type"], "free_credits")


if __name__ == "__main__":
    unittest.main()
