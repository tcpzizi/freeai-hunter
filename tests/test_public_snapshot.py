"""The site and the Cloudflare Worker read docs/, in the public repo, because the
generated data lives in a private repo they cannot read.

Run from the repo root:  python -m unittest discover -s tests -t . -v
"""
import json, os, pathlib, sqlite3, tempfile, unittest
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
        with mock.patch.object(publish, "MIRROR_TOKEN", ""):
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
        with mock.patch.object(publish, "MIRROR_TOKEN", ""):
            publish.build(self.c)
        root = pathlib.Path(self.tmp.name)
        for name in ("docs/offers.json", "docs/sites.json"):
            data = json.loads((root / name).read_text(encoding="utf-8"))
            self.assertNotIn("status", data[0], name)
            self.assertIn("verify_status", data[0], name)

    def test_the_worker_default_path_exists_in_the_snapshot(self):
        """The Worker reads docs/offers.json from the public repo; publish must
        keep writing exactly that path."""
        with mock.patch.object(publish, "MIRROR_TOKEN", ""):
            publish.build(self.c)
        self.assertTrue((pathlib.Path(self.tmp.name) / "docs/offers.json").is_file())


class Mirror(unittest.TestCase):
    """publish.py pushes the public JSON through the contents API: a workflow's
    plain checkout of the public repo is read-only for contents, so a git commit
    of docs/ from there could never work."""

    def setUp(self):
        self.calls = []

    class R:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
            self.text = str(body)

        def json(self):
            return self._body


    def test_no_token_means_no_push_and_no_crash(self):
        with mock.patch.object(publish, "MIRROR_TOKEN", ""):
            self.assertEqual(publish.mirror_public([{"id": "o1"}]), [])

    def test_a_new_file_is_created_and_an_existing_one_updated(self):
        gets, puts_ = [], []

        def fake_get(url, **kw):
            gets.append((url, kw))
            return self.R(200, {"sha": "abc123"})

        def fake_put(url, **kw):
            puts_.append((url, kw))
            return self.R(201, {})

        with mock.patch.object(publish, "MIRROR_TOKEN", "tok"), \
             mock.patch.object(publish.httpx, "get", fake_get), \
             mock.patch.object(publish.httpx, "put", fake_put):
            written = publish.mirror_public([{"id": "o1"}])
        self.assertEqual(len(written), 4)
        puts = [("PUT", u, kw.get("json"), kw.get("headers")) for u, kw in puts_]
        self.assertTrue(puts)
        # the update carries the sha it read, and the token travels in the header
        self.assertEqual(puts[0][2]["sha"], "abc123")
        self.assertIn("Bearer tok", puts[0][3]["authorization"])
        import base64, json
        body = json.loads(base64.b64decode(puts[0][2]["content"]).decode("utf-8"))
        self.assertEqual(body[0]["id"], "o1", "docs/offers.json is the bare list")

    def test_a_rejected_push_is_reported_not_swallowed(self):
        def fake_get(url, **kw):
            return self.R(404, {})

        def fake_put(url, **kw):
            return self.R(403, {"message": "no"})

        with mock.patch.object(publish, "MIRROR_TOKEN", "tok"), \
             mock.patch.object(publish.httpx, "get", fake_get), \
             mock.patch.object(publish.httpx, "put", fake_put):
            self.assertEqual(publish.mirror_public([{"id": "o1"}]), [])

    def test_a_missing_token_is_reported_but_not_a_failure(self):
        from src import publish_mirror
        with mock.patch.object(publish, "MIRROR_TOKEN", ""):
            self.assertEqual(publish_mirror.main(), 2)

    def test_a_rejected_push_fails_the_step(self):
        from src import publish_mirror

        def fake_get(url, **kw):
            return self.R(404, {})

        def fake_put(url, **kw):
            return self.R(403, {"message": "no"})

        with mock.patch.object(publish, "MIRROR_TOKEN", "tok"), \
             mock.patch.object(publish_mirror, "load_rows", return_value=[]), \
             mock.patch.object(publish.httpx, "get", fake_get), \
             mock.patch.object(publish.httpx, "put", fake_put):
            self.assertEqual(publish_mirror.main(), 1, "a stale public copy must turn the step red")

    def test_a_good_push_reports_what_it_wrote(self):
        from src import publish_mirror

        def fake_get(url, **kw):
            return self.R(404, {})

        def fake_put(url, **kw):
            return self.R(201, {})

        with mock.patch.object(publish, "MIRROR_TOKEN", "tok"), \
             mock.patch.object(publish_mirror, "load_rows", return_value=[{"id": "o1", "vendor": "v"}]), \
             mock.patch.object(publish.httpx, "get", fake_get), \
             mock.patch.object(publish.httpx, "put", fake_put):
            self.assertEqual(publish_mirror.main(), 0)
