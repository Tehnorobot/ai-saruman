"""Telegram UI routes operate on isolated SQLite; never use credentials or publish."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_lab import Fixture, FakeProvider
from saruman.web import Application, make_server
from unittest.mock import Mock
from test_runtime import FakeGame


class TelegramHttpTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app = Application(self.root, provider=FakeProvider())
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.app.runtime.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def post(self, path, value, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Saruman-Token"] = self.app.token
        with urlopen(Request(self.base + path, json.dumps(value).encode(), headers)) as response:
            return json.load(response)

    def snapshot(self):
        with urlopen(self.base + "/api/telegram") as response:
            return json.load(response)

    def test_settings_require_csrf_and_validate_values(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/telegram/settings", {"enabled": True}, token=False)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        for value in ({"enabled": "true"}, {"interval_seconds": 0}, {"api_key": "secret"}):
            with self.assertRaises(HTTPError) as error:
                self.post("/api/telegram/settings", value)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        self.assertFalse(self.snapshot()["settings"]["enabled"])

    def test_runtime_routes_start_stop_and_reject_untrusted_controls(self):
        self.app.runtime.game=FakeGame()
        self.app.director.start=Mock()  # No model/network worker in an HTTP fixture.
        with self.assertRaises(HTTPError) as error:
            self.post("/api/runtime/start",{"minecraft":False},token=False)
        self.assertEqual(error.exception.code,403)
        error.exception.close()
        started=self.post("/api/runtime/start",{"minecraft":False})
        self.assertTrue(started["active"])
        self.assertFalse(started["options"]["minecraft"])
        with urlopen(self.base+"/api/runtime") as response:
            self.assertTrue(json.load(response)["active"])
        stopped=self.post("/api/runtime/stop",{})
        self.assertFalse(stopped["active"])
        with self.assertRaises(HTTPError) as error:
            self.post("/api/runtime/start",{"executable":"cmd.exe"})
        self.assertEqual(error.exception.code,400)
        error.exception.close()

    def test_filter_checkbox_setting_is_authenticated_persisted_and_requires_stop(self):
        self.app.runtime.game=FakeGame();self.app.director.start=Mock()
        with self.assertRaises(HTTPError) as error:
            self.post('/api/runtime/start',{'minecraft':False,'filter_enabled':False},token=False)
        self.assertEqual(error.exception.code,403);error.exception.close()
        started=self.post('/api/runtime/start',{'minecraft':False,'filter_enabled':False})
        self.assertFalse(started['options']['filter_enabled'])
        self.assertFalse(self.app.runtime.game.filter_enabled)
        with self.assertRaises(HTTPError) as error:
            self.post('/api/runtime/start',{'filter_enabled':True})
        self.assertEqual(error.exception.code,409);error.exception.close()
        self.post('/api/runtime/stop',{})
        self.assertFalse(self.post('/api/runtime/start',{})['options']['filter_enabled'])
        self.post('/api/runtime/stop',{})
        self.assertTrue(self.post('/api/runtime/start',{'filter_enabled':True})['options']['filter_enabled'])

    def test_second_lab_cannot_listen_on_the_same_port(self):
        duplicate=None
        try:
            with self.assertRaises(OSError):duplicate=make_server(self.app,self.server.server_port)
        finally:
            if duplicate:duplicate.server_close()
        with urlopen(self.base+'/api/runtime') as response:
            self.assertFalse(json.load(response)['active'])

    def test_manual_draft_queue_cancel_and_persistence(self):
        identifier = self.post("/api/telegram/draft", {"text": "Проверка UI"})["id"]
        self.assertEqual(self.snapshot()["posts"][0]["status"], "draft")
        self.post("/api/telegram/action", {"id": identifier, "action": "queue"})
        self.assertEqual(self.snapshot()["posts"][0]["status"], "queued")
        self.post("/api/telegram/action", {"id": identifier, "action": "cancel"})
        app = Application(self.root, provider=FakeProvider())
        self.assertEqual(app.telegram.store.snapshot()["posts"][0]["status"], "cancelled")

    def test_page_has_no_keys_and_lists_all_history(self):
        for i in range(35):
            self.app.telegram.store.event(str(i), "tree_grown", {"fact": "Tree grew"})
        first = self.snapshot()
        with urlopen(self.base + "/api/telegram?page=1") as response:
            second = json.load(response)
        self.assertEqual(len(first["events"]) + len(second["events"]), 35)
        self.assertNotIn("GATEWAY_API_KEY", json.dumps(first))

    def test_private_publish_requires_csrf_and_tracks_actual_delivery(self):
        identifier=self.app.telegram.store.manual('Ready post')
        command={'text':'опубликуй пост в канале','request_id':'publish-ui'}
        with self.assertRaises(HTTPError) as error:self.post('/api/admin',command,token=False)
        self.assertEqual(error.exception.code,403);error.exception.close()
        self.assertEqual(self.snapshot()['posts'][0]['status'],'draft')
        result=self.post('/api/admin',command)
        self.assertEqual(result['status'],'waiting_start')
        with self.app.telegram.store.db() as db:
            db.execute("UPDATE posts SET status='sent',message_id=7 WHERE id=?",(identifier,))
        with urlopen(self.base+'/api/admin') as response:history=json.load(response)['messages']
        self.assertEqual(history[0]['status'],'sent')
        self.assertEqual(json.loads(history[0]['result'])['message_id'],7)
