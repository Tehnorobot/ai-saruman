"""Offline tests: real local HTTP, fake Telegram, no messages or paid APIs."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import client
from gateway import Publisher, Server, Settings, Telegram, TelegramError, read_env

KEY = "test-key-" + "a" * 40
TOKEN = "123456789:" + "b" * 35


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.can_post = True
        self.started = threading.Event()
        self.release = None

    def call(self, method, payload):
        self.calls.append((method, payload))
        if method == "sendMessage":
            self.started.set()
            if self.release:
                self.release.wait(5)
            if self.failure:
                raise self.failure
            return {"message_id": 42}
        return {
            "getMe": {"id": 123, "username": "saruman_test_bot"},
            "getChat": {"id": -100123456, "type": "channel", "title": "Test"},
            "getChatMember": {"status": "administrator", "can_post_messages": self.can_post},
        }[method]


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(TOKEN, "-100123456", KEY, Path(self.temp.name) / "gateway.sqlite3")
        self.telegram = FakeTelegram()
        self.publisher = Publisher(self.settings, self.telegram)

    def post(self, text="Тест: дерево выращено 🌳", id="test-1"):
        return self.publisher.publish({"publication_id": id, "text": text})

    def test_publish_and_duplicate_survive_restart(self):
        code, result = self.post()
        self.assertEqual((code, result["status"], result["message_id"]), (200, "sent", 42))
        self.publisher = Publisher(self.settings, self.telegram)
        self.publisher.recover_interrupted()
        self.assertEqual(self.post(), (code, result))
        self.assertEqual(len(self.telegram.calls), 1)
        self.assertEqual(self.telegram.calls[0][1]["chat_id"], self.settings.channel_id)

    def test_same_id_different_text_conflicts(self):
        self.post()
        self.assertEqual(self.post("Changed")[0], 409)
        self.assertEqual(len(self.telegram.calls), 1)

    def test_channel_override_is_rejected(self):
        with self.assertRaises(ValueError):
            self.publisher.publish({"publication_id": "test", "text": "Hi", "chat_id": "@other"})
        self.assertFalse(self.telegram.calls)

    def test_validation(self):
        for payload in [None, [], {}, {"publication_id": "../bad", "text": "Hi"},
                        {"publication_id": 1, "text": "Hi"}, {"publication_id": "ok", "text": " "},
                        {"publication_id": "ok", "text": "a" * 4097},
                        {"publication_id": "ok", "text": "🌳" * 2049},
                        {"publication_id": "ok", "text": "\ud800"}]:
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(ValueError):
                    self.publisher.publish(payload)
        self.assertFalse(self.telegram.calls)

    def test_concurrent_duplicates_send_once(self):
        self.telegram.release = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.post)
            self.assertTrue(self.telegram.started.wait(3))
            try:
                self.assertEqual(self.post()[0], 202)
            finally:
                self.telegram.release.set()
            self.assertEqual(first.result()[0], 200)
        self.assertEqual(len(self.telegram.calls), 1)

    def test_unknown_delivery_is_not_resent(self):
        self.telegram.failure = TelegramError("telegram_connection_error", uncertain=True)
        self.assertEqual(self.post()[1]["status"], "unknown")
        self.assertEqual(self.post()[0], 409)
        self.assertEqual(len(self.telegram.calls), 1)

    def test_rate_limit_is_recorded_without_hidden_retry(self):
        self.telegram.failure = TelegramError("telegram_http_429", retry_after=15)
        code, result = self.post()
        self.assertEqual((code, result["status"], result["retry_after"]), (502, "failed", 15))
        self.post()
        self.assertEqual(len(self.telegram.calls), 1)

    def test_interrupted_send_remains_in_history(self):
        with self.publisher.connect() as db:
            db.execute("INSERT INTO publications (publication_id,text,digest,status,created_at,updated_at) "
                       "VALUES ('old','original text','digest','sending','created','updated')")
        self.publisher.recover_interrupted()
        self.assertEqual(self.publisher.get("old")["status"], "unknown")
        with self.publisher.connect() as db:
            self.assertEqual(db.execute("SELECT text FROM publications WHERE publication_id='old'").fetchone()[0],
                             "original text")

    def test_check_posts_nothing_and_reports_permissions(self):
        self.assertTrue(self.publisher.check()["can_post"])
        self.telegram.can_post = False
        self.assertFalse(self.publisher.check()["can_post"])
        self.assertFalse(any(method == "sendMessage" for method, _ in self.telegram.calls))

    def test_malformed_permission_response_is_an_explicit_error(self):
        with patch.object(self.telegram, "call", return_value=[]):
            with self.assertRaisesRegex(TelegramError, "invalid_bot_response"):
                self.publisher.check()

    def test_success_without_message_id_is_unknown(self):
        with patch.object(self.telegram, "call", return_value={}):
            code, result = self.post()
        self.assertEqual((code, result["status"]), (409, "unknown"))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.telegram = FakeTelegram()
        settings = Settings(TOKEN, "-100123456", KEY, Path(cls.temp.name) / "db.sqlite3")
        cls.server = Server(("127.0.0.1", 0), Publisher(settings, cls.telegram))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(3)
        cls.temp.cleanup()

    def test_health_without_key(self):
        code, result = client.call(self.url, "", "GET", "/healthz")
        self.assertEqual((code, result["ok"]), (200, True))

    def test_authentication(self):
        for key in ("", "wrong"):
            self.assertEqual(client.call(self.url, key, "GET", "/v1/check")[0], 401)
            self.assertEqual(client.call(self.url, key, "POST", "/v1/posts",
                                         {"publication_id": "unauthorized", "text": "No"})[0], 401)
        self.assertIsNone(self.server.publisher.get("unauthorized"))

    def test_publish_status_and_replay_over_http(self):
        payload = {"publication_id": "http-test", "text": "Проверка 🌳"}
        first = client.call(self.url, KEY, "POST", "/v1/posts", payload)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]["status"], "sent")
        self.assertEqual(client.call(self.url, KEY, "POST", "/v1/posts", payload), first)
        self.assertEqual(client.call(self.url, KEY, "GET", "/v1/posts/http-test"), first)

    def test_bad_payload_returns_json(self):
        code, result = client.call(self.url, KEY, "POST", "/v1/posts", {"text": "Missing ID"})
        self.assertEqual((code, result["error"]), (400, "invalid_request"))

    def test_missing_post(self):
        self.assertEqual(client.call(self.url, KEY, "GET", "/v1/posts/missing")[0], 404)


class ConfigurationTests(unittest.TestCase):
    def test_client_requires_encryption_for_remote_hosts(self):
        for url in ("http://example.com", "https://user:password@example.com", "https://example.com/?key=x",
                    "https://example.com/api", "file:///tmp/foo", "http://127.0.0.1.evil.example"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                client.validate_url(url)
        self.assertEqual(client.validate_url("https://example.com/"), "https://example.com")
        self.assertEqual(client.validate_url("http://127.0.0.1:8787"), "http://127.0.0.1:8787")

    def test_env_is_literal_and_process_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# comment\nVALUE="$(not-executed)"\nOTHER=file\n', encoding="utf-8-sig")
            with patch.dict(os.environ, {"OTHER": "process"}, clear=True):
                self.assertEqual(read_env(path), {"VALUE": "$(not-executed)", "OTHER": "process"})

    def test_server_requires_explicit_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
                Settings.load(path)
            path.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHANNEL_ID=-100123456\n"
                            f"GATEWAY_API_KEY={KEY}\n", encoding="utf-8")
            self.assertEqual(Settings.load(path).database, Path(directory) / "data/gateway.sqlite3")

    def test_no_redirects_with_credentials(self):
        from gateway import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))


class TransportTests(unittest.TestCase):
    def test_interrupted_response_is_unknown(self):
        from http.client import IncompleteRead
        telegram = Telegram(TOKEN)
        with patch.object(telegram.opener, "open", side_effect=IncompleteRead(b"partial", 100)):
            with self.assertRaises(TelegramError) as caught:
                telegram.call("sendMessage", {})
        self.assertTrue(caught.exception.uncertain)

    def test_network_errors_are_unknown_without_exposing_token(self):
        telegram = Telegram(TOKEN)
        with patch.object(telegram.opener, "open", side_effect=URLError("secret " + TOKEN)):
            with self.assertRaises(TelegramError) as caught:
                telegram.call("sendMessage", {})
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_http_429_preserves_retry_after(self):
        import io
        telegram = Telegram(TOKEN)
        error = HTTPError("https://example.invalid", 429, "Rate limited", {},
                          io.BytesIO(b'{"ok":false,"parameters":{"retry_after":12}}'))
        with patch.object(telegram.opener, "open", side_effect=error):
            with self.assertRaises(TelegramError) as caught:
                telegram.call("sendMessage", {})
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(caught.exception.retry_after, 12)

    def test_http_502_is_unknown(self):
        telegram = Telegram(TOKEN)
        with patch.object(telegram.opener, "open", side_effect=HTTPError("", 502, "", {}, None)):
            with self.assertRaises(TelegramError) as caught:
                telegram.call("sendMessage", {})
        self.assertTrue(caught.exception.uncertain)


if __name__ == "__main__":
    unittest.main()
