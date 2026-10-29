"""Extract-queue tests: overflow, failures and LLM outages must not lose candidates.
Run from the repo root:  python -m unittest discover -s tests -t . -v
"""
import os, sys, tempfile, time, types, unittest
from unittest import mock

# main.py imports every collector (network libraries); these tests do not need them
_fake = types.ModuleType("src.collectors")
for _n in ("model_registry", "pagediff", "github_watch", "rss", "googlenews", "reddit",
           "hackernews", "tgchannel", "bluesky", "devpost"):
    setattr(_fake, _n, types.ModuleType("src.collectors." + _n))
sys.modules.setdefault("src.collectors", _fake)

from src import store, main  # noqa: E402

VEND = {"official": set(), "platforms": {}}


def item(i, pre=5.0, **kw):
    d = {"id": "h:%03d" % i, "url": "https://example.com/%d" % i,
         "canon_url": "https://example.com/%d" % i, "title": "t%d" % i, "body": "b",
         "source": "rss:x", "source_type": "rss", "prefilter": pre}
    d.update(kw)
    return d


class QueueBase(unittest.TestCase):
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

    def queued(self):
        return {r[0]: r[1] for r in self.c.execute("SELECT item_id, tries FROM extract_queue")}


class Queue(QueueBase):
    def test_overflow_waits_for_next_run(self):
        for i in range(50):
            store.enqueue(self.c, item(i, pre=3 + i / 100))
        first = store.next_batch(self.c, 40)
        self.assertEqual(len(first), 40)
        self.assertEqual(first[0]["id"], "h:049")  # best prefilter first
        for it in first:
            store.dequeue(self.c, it["id"])
        self.assertEqual(len(store.next_batch(self.c, 40)), 10)

    def test_hard_signal_first(self):
        store.enqueue(self.c, item(1, pre=9))
        store.enqueue(self.c, item(2, pre=1, hard_signal=True))
        self.assertEqual(store.next_batch(self.c, 1)[0]["id"], "h:002")

    def test_payload_keeps_what_main_needs(self):
        store.enqueue(self.c, item(1, source_url="https://news.example/1", hard_signal=True))
        it = store.next_batch(self.c, 1)[0]
        for k in ("id", "url", "title", "body", "source", "source_url", "hard_signal"):
            self.assertIn(k, it)

    def test_failures_dropped_after_max_tries(self):
        store.enqueue(self.c, item(1))
        for _ in range(main.MAX_TRIES):
            store.retry_later(self.c, "h:001", "boom")
        self.assertEqual(store.prune_queue(self.c, main.MAX_TRIES), 1)

    def test_llm_outage_does_not_use_tries(self):
        store.enqueue(self.c, item(1))
        for _ in range(10):
            store.retry_later(self.c, "h:001", "no_llm", count=False)
        self.assertEqual(store.prune_queue(self.c, main.MAX_TRIES), 0)

    def test_old_entries_expire(self):
        store.enqueue(self.c, item(1))
        self.c.execute("UPDATE extract_queue SET queued_at=?", (int(time.time()) - 8 * 86400,))
        self.assertEqual(store.prune_queue(self.c, main.MAX_TRIES), 1)

    def test_backfill_recovers_lost_candidates(self):
        for i, pre in ((1, 5.0), (2, 1.0), (3, 6.0)):
            store.put_item(self.c, item(i, pre=pre))
        store.upsert_offer(self.c, {"id": "o1", "item_id": "h:003", "score": 1})
        self.assertEqual(store.backfill_queue(self.c, 3.0), 1)  # h:001 only
        self.assertEqual(store.next_batch(self.c, 5)[0]["id"], "h:001")
        self.assertEqual(store.backfill_queue(self.c, 3.0), 0)  # already queued


class Process(QueueBase):
    def run_process(self, results, verdict="verified"):
        """results: item id -> extract result (dict) or an exception to raise"""
        for iid in results:
            store.enqueue(self.c, item(int(iid[2:])))

        def fake_extract(it):
            r = results[it["id"]]
            if isinstance(r, Exception):
                raise r
            return dict(r)
        with mock.patch.object(main.extract, "extract", side_effect=fake_extract), \
                mock.patch.object(main.verify, "domain", return_value="example.com"), \
                mock.patch.object(main.verify, "verify", return_value=(verdict, {})), \
                mock.patch.object(main.score, "score", return_value=30.0):
            return main.process(self.c, VEND, store.next_batch(self.c, 40))

    def test_each_outcome(self):
        made, rejected, retried = self.run_process({
            "h:001": RuntimeError("timeout"),
            "h:002": {"is_offer": False, "reject_reason": "no_llm_no_credit_phrase",
                      "extracted_by": "regex"},
            "h:003": {"is_offer": False, "reject_reason": "unit_price",
                      "extracted_by": "llm"},
            "h:004": {"is_offer": True, "offer_type": "free_credits",
                      "value": "$5 in credits", "extracted_by": "llm"}})
        self.assertEqual((made, rejected, retried), (1, 1, 2))
        # error: retried with one try used; LLM outage: retried, no try used
        self.assertEqual(self.queued(), {"h:001": 1, "h:002": 0})
        self.assertEqual(self.c.execute("SELECT status FROM offers").fetchone()["status"],
                         "active")

    def test_not_offer_is_archived(self):
        made, rejected, _ = self.run_process(
            {"h:005": {"is_offer": True, "offer_type": "free_credits", "value": "$100",
                       "extracted_by": "llm"}}, verdict="not_offer")
        self.assertEqual((made, rejected), (0, 1))
        self.assertEqual(self.c.execute("SELECT status FROM offers").fetchone()["status"],
                         "archived")
        self.assertEqual(self.queued(), {})


class ExtractedBy(unittest.TestCase):
    def test_marks_where_the_verdict_came_from(self):
        it = {"title": "Pro $20/month", "body": "", "url": "https://x.com", "source": "t"}
        with mock.patch.object(main.extract, "llm_json", return_value=None):
            self.assertEqual(main.extract.extract(it)["extracted_by"], "regex")
        with mock.patch.object(main.extract, "llm_json", return_value={"is_offer": False}):
            self.assertEqual(main.extract.extract(it)["extracted_by"], "llm")


if __name__ == "__main__":
    unittest.main()
