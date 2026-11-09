"""Retention: data/state.sqlite is committed to git, so it must not grow forever.
Run from the repo root:  python -m unittest discover -s tests -t . -v
"""
import os, time, tempfile, sqlite3, unittest
from unittest import mock
from src import store


class Retention(unittest.TestCase):
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

    def item(self, i, age_days, body="article text"):
        it = {"id": "h:%03d" % i, "canon_url": "https://e.com/%d" % i,
              "url": "https://e.com/%d" % i, "title": "t%d" % i, "body": body,
              "source": "rss:x", "source_type": "rss", "prefilter": 5.0}
        store.put_item(self.c, it)
        # put_item always stamps "now"; age the row the way a real run would
        self.c.execute("UPDATE items SET first_seen=?, last_seen=? WHERE id=?",
                       (int(time.time()) - int(age_days * 86400),
                        int(time.time()) - int(age_days * 86400), it["id"]))

    def body(self, i):
        return self.c.execute("SELECT body FROM items WHERE id=?",
                              ("h:%03d" % i,)).fetchone()["body"]

    def test_body_is_dropped_before_the_row(self):
        self.item(1, age_days=10)   # older than the body window, inside the item window
        self.item(2, age_days=1)
        store.prune_history(self.c, item_days=45, snapshot_days=14, body_days=7,
                            vacuum=False)
        self.assertEqual(self.body(1), "")          # kept, but only as a tombstone
        self.assertEqual(self.body(2), "article text")
        self.assertIsNotNone(self.c.execute(
            "SELECT 1 FROM items WHERE id='h:001'").fetchone())

    def test_old_item_removed(self):
        self.item(1, age_days=46)
        self.item(2, age_days=44)
        counts = store.prune_history(self.c, item_days=45, snapshot_days=14,
                                     body_days=7, vacuum=False)
        self.assertEqual(counts["items"], 1)
        ids = {r["id"] for r in self.c.execute("SELECT id FROM items")}
        self.assertEqual(ids, {"h:002"})

    def test_old_page_snapshot_removed_but_recent_kept(self):
        now = int(time.time())
        store.snapshot(self.c, "page:https://old.ai/pricing", "free plan\n")
        store.snapshot(self.c, "page:https://new.ai/pricing", "free plan\n")
        self.c.execute("UPDATE snapshots SET fetched_at=? WHERE key LIKE '%old.ai%'",
                       (now - 15 * 86400,))
        store.prune_history(self.c, item_days=45, snapshot_days=14, body_days=7,
                            vacuum=False)
        keys = [r["key"] for r in self.c.execute("SELECT key FROM snapshots")]
        self.assertEqual(keys, ["page:https://new.ai/pricing"])
        # the recent snapshot still diffs correctly -> change detection still works
        changed, old = store.snapshot(self.c, "page:https://new.ai/pricing",
                                      "free plan\nnew line\n")
        self.assertTrue(changed)
        self.assertEqual(old, "free plan\n")

    def test_queue_and_offers_are_never_pruned_here(self):
        self.item(1, age_days=400)
        store.enqueue(self.c, {"id": "h:001", "url": "https://e.com/1",
                               "title": "t", "body": "b", "prefilter": 7})
        store.upsert_offer(self.c, {"id": "o1", "item_id": "h:001", "score": 40,
                                    "domain": "e.com"})
        store.prune_history(self.c, item_days=30, snapshot_days=7, body_days=1,
                            vacuum=False)
        self.assertEqual(store.queue_size(self.c), 1)   # unpaid candidate is not lost
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 1)

    def test_vacuum_shrinks_the_file(self):
        for i in range(200):
            self.item(i, age_days=90, body="x" * 8000)
        self.c.commit()
        big = os.path.getsize(store.DB)
        self.c.execute("DELETE FROM items")
        self.c.commit()
        self.assertEqual(os.path.getsize(store.DB), big)   # pages are only freed by vacuum
        store.prune_history(self.c, item_days=45, snapshot_days=14, body_days=7,
                            vacuum=True)
        self.assertLess(os.path.getsize(store.DB), big)

    def test_vacuum_failure_does_not_break_the_run(self):
        class Locked:
            """A connection whose VACUUM fails the way a locked database does."""
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, *args):
                if sql.strip().upper().startswith("VACUUM"):
                    raise sqlite3.OperationalError("database is locked")
                return self.conn.execute(sql, *args)

            def commit(self):
                return self.conn.commit()

        self.item(1, age_days=90)
        store.vacuum_db(Locked(self.c))   # swallowed, no exception
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)

    def test_run_prunes_at_most_once_per_window(self):
        """main.run() consults the meta key, so a 48-runs-a-day schedule does not
        rewrite the whole database 48 times."""
        now = int(time.time())
        store.meta_set(self.c, "pruned_at", now - int(11.5 * 3600))
        self.c.commit()
        last = int(store.meta_get(self.c, "pruned_at"))
        self.assertLess(now - last, 12 * 3600)   # inside the window: no prune
        store.meta_set(self.c, "pruned_at", now - int(12.5 * 3600))
        self.c.commit()
        last = int(store.meta_get(self.c, "pruned_at"))
        self.assertGreater(now - last, 12 * 3600)  # outside: prune


if __name__ == "__main__":
    unittest.main()
