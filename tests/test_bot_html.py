"""Regression tests for Telegram's HTML parse mode."""
import html
import unittest
from unittest import mock

from src.bot import handlers, render


class TelegramHtmlTests(unittest.TestCase):
    def test_welcome_escapes_user_name(self):
        name = 'A&B <admin>'
        message = handlers.WELCOME_PENDING.format(name=html.escape(name))
        self.assertIn('A&amp;B &lt;admin&gt;', message)
        self.assertNotIn('A&B <admin>', message)

    def test_offer_text_escapes_untrusted_fields(self):
        message = render.offer_text({
            "vendor": "<b>spoof</b>",
            "offer_type": "free_credits",
            "value": "$5 & unlimited",
            "score": 35,
            "why": "hello <script>alert(1)</script>",
        })
        self.assertIn("&lt;b&gt;spoof&lt;/b&gt;", message)
        self.assertIn("$5 &amp; unlimited", message)
        self.assertIn("&lt;script&gt;", message)
        self.assertNotIn("<script>", message)

    def test_admin_pending_escapes_user_fields(self):
        db = {"users": {"123456": {
            "status": "pending",
            "first_name": "<b>name</b>",
            "username": "x&y",
        }}}
        with mock.patch.object(handlers, "ADMINS", {99}), \
             mock.patch.object(handlers.api, "send") as send:
            handlers.cmd_pending(db, 99, {})
        text = send.call_args.args[1]
        self.assertIn("&lt;b&gt;name&lt;/b&gt;", text)
        self.assertIn("x&amp;y", text)


if __name__ == "__main__":
    unittest.main()
