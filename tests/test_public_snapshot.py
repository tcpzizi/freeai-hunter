"""The site and the Cloudflare Worker read docs/, in the public repo, because the
generated data lives in a private repo they cannot read.

Run from the repo root:  python -m unittest discover -s tests -t . -v
"""
import json, os, pathlib, tempfile, unittest
from unittest import mock
from src import store, publish


class PublicSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "data").mkdir()
        self.patch = mock.patch.multiple(
            store,
            DB=str(root / "data/state.sqlite"),
            DELIV_F=str(root / "data/deliveries.json"),
            FEEDB_F=str(root / "data/feedback.json"),
        )
        self.patch.start()
        self.old = os.getcwd()
        os.chdir(root)          # publish writes data/ and docs/ relative to cwd
        self.c = store.conn()
        store.put_item(self.c, {"id": "h:1", "canon_url": "https://e.com/1", "url": "https://e.com/1",
                                "title": "t", "body": "b", "source": "rss:x",
                                "source_type": "rss", "prefilter": 5.0})
        self.c.execute(
            "INSERT INTO offers(id,vendor,domain,offer_type,value,score,verify_status,status,"
            "signup_url,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("o1", "acme.com", "acme.com", "free_credits", "$5", 30, "verified", "active",
             "https://acme.com/signup", "2026-11-01T00:00:00Z"),
        )
        self.c.commit()

    def tearDown(self):
        self.c.close()
        os.chdir(self.old)
        self.patch.stop()
        self.tmp.cleanup()

    def test_publish_writes_the_public_copies(self):
        publish.build(self.c)
        root = pathlib.Path(self.tmp.name)
        bare = json.loads((root / "docs/offers.json").read_text(encoding="utf-8"))
        self.assertIsInstance(bare, list)
        self.assertEqual(bare[0]["vendor"], "acme.com")
        # the site reads both shapes, so both must exist
        wrapped = json.loads((root / "docs/github-pages/offers.json").read_text(encoding="utf-8"))
        self.assertEqual(wrapped["offers"][0]["id"], "o1")
        sites = json.loads((root / "docs/github-pages/sites.json").read_text(encoding="utf-8"))
        self.assertEqual(sites["offers"][0]["id"], "o1")
        self.assertEqual(json.loads((root / "docs/sites.json").read_text(encoding="utf-8"))[0]["id"], "o1")

    def test_the_public_copy_carries_no_internal_status(self):
        publish.build(self.c)
        root = pathlib.Path(self.tmp.name)
        for name in ("docs/offers.json", "docs/sites.json"):
            data = json.loads((root / name).read_text(encoding="utf-8"))
            self.assertNotIn("status", data[0], name)
            self.assertIn("verify_status", data[0], name)

    def test_the_worker_default_path_exists_in_the_snapshot(self):
        """The Worker reads docs/offers.json from the public repo; publish must
        keep writing exactly that path."""
        publish.build(self.c)
        self.assertTrue((pathlib.Path(self.tmp.name) / "docs/offers.json").is_file())
