"""No-network checks of the real Lab routing, shared SQLite and provider protocol."""
import io
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_lab import Fixture
from saruman.provider import CodexProvider
from saruman.roles import ModelSettings, api_complete, protocol, settings
from saruman.web import Application, make_server
import threading


class ModelTests(Fixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {"SARUMAN_ROLE_DB": str(self.root / "roles.sqlite"),
                                          "MINIMAX_API_KEY": "test-key-never-disclose"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.models = ModelSettings(self.root)

    def select(self, role="lab_chat", **changes):
        profile = self.models.store.profiles()[role]
        revision = profile.pop("revision")
        profile.update(changes)
        self.models.save({"role": role, "revision": revision, "profile": profile})
        return self.models.store.profiles()[role]

    def minimax(self, role="lab_chat"):
        return self.select(role, provider="minimax", model="MiniMax-M3.1-Flash", reasoning="low", max_output=32768)

    def test_shared_profiles_persist_and_telegram_does_not_follow_persona_edits(self):
        self.minimax("telegram")
        self.select("persona", model="gpt-6.1-terra")
        saved = ModelSettings(self.root).snapshot()
        self.assertEqual(saved["profiles"]["telegram"]["provider"], "minimax")
        self.assertEqual(saved["profiles"]["persona"]["model"], "gpt-6.1-terra")
        self.assertEqual(saved["profiles"]["action"]["provider"], "codex")
        self.assertNotIn("test-key-never-disclose", json.dumps(saved))

    def test_old_telegram_profile_is_migrated_once(self):
        path = self.root / "old.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE profiles (role TEXT PRIMARY KEY, revision INTEGER, body TEXT)")
            db.execute("INSERT INTO profiles VALUES ('persona',7,?)", (json.dumps({**settings.default_profile(), "model":"gpt-6.1-sol"}),))
        store = settings.RoleStore(path)
        self.assertEqual(store.profiles()["telegram"]["revision"], 7)
        p = store.profiles()["persona"]
        p.pop("revision")
        store.save("persona", {**p, "model":"gpt-6.1-terra"}, paused=True)
        self.assertEqual(settings.RoleStore(path).profiles()["telegram"]["model"], "gpt-6.1-sol")

    def test_live_request_and_stale_editor_cannot_replace_profile(self):
        p = self.models.store.profiles()["action"]
        identifier = self.models.store.begin("action", "test-live", p, 10)
        with self.assertRaisesRegex(ValueError, "Ещё выполняется"):
            self.select("action", model="gpt-6.1-terra")
        self.minimax("telegram")  # Another role remains editable.
        self.models.store.finish(identifier, {"total_tokens": 5})
        old = self.models.store.profiles()["action"]
        self.select("action", model="gpt-6.1-terra")
        with self.assertRaisesRegex(ValueError, "другом окне"):
            self.models.save({"role":"action", "revision":old.pop("revision"), "profile":old})
        with self.assertRaisesRegex(RuntimeError, "изменились"):
            self.models.store.begin("action", "stale-read", {**old, "revision":0}, 10)

    def test_cached_codex_models_and_actual_reasoning_are_validated(self):
        self.models.store.update_catalogue([{"id":"account-model", "reasoning":["low","high"]}])
        self.select("action", model="account-model", reasoning="high")
        self.assertEqual(self.models.snapshot()["catalogue"]["codex"]["account-model"], ["default","low","high"])
        with self.assertRaises(ValueError):
            self.select("action", reasoning="ultra")

    def test_real_chat_routes_to_minimax_and_preserves_usage(self):
        self.minimax()
        app = Application(self.root)
        session = app.store.create_session()
        result = {"reply":"Ответ", "memories":[]}
        usage = {"input_tokens":120,"output_tokens":7,"total_tokens":127,"cached_input_tokens":80,"cache_write_tokens":10}
        with patch("saruman.roles.api_complete", return_value=(result,usage)) as api, patch.object(app.codex,"complete") as codex:
            app.chat({"session_id":session["id"], "model":"wrong-client-model", "text":"Привет"})
        self.assertEqual(api.call_args.args[0]["model"], "MiniMax-M3.1-Flash")
        self.assertEqual(api.call_args.args[0]["reasoning"], "low")
        codex.assert_not_called()
        run = app.store.runs()[0]
        self.assertEqual((run["provider"],run["role"],run["model"]),("minimax","lab_chat","MiniMax-M3.1-Flash"))
        self.assertEqual((run["input_tokens"],run["total_tokens"],run["cache_write_tokens"]),(120,127,10))
        self.assertEqual(app.models.store.history()[0]["usage"], usage)

    def test_missing_key_fails_explicitly_without_codex_fallback(self):
        profile = self.minimax()
        app = Application(self.root)
        session = app.store.create_session()
        with patch.dict(os.environ, MINIMAX_API_KEY=""), patch.object(app.codex,"complete") as codex:
            self.assertFalse(app.models.snapshot()["providers"]["minimax"]["configured"])
            with self.assertRaisesRegex(RuntimeError, "MINIMAX_API_KEY"):
                app.chat({"session_id":session["id"], "text":"Привет"})
        codex.assert_not_called()
        self.assertEqual(app.models.store.history()[0]["state"], "error")
        self.assertEqual(profile["provider"], "minimax")

    def test_partial_usage_retained_for_truncated_output(self):
        self.minimax()
        app=Application(self.root)
        session=app.store.create_session()
        with patch("saruman.roles.api_complete", side_effect=protocol.ProviderOutputError({"input_tokens":5,"output_tokens":9,"total_tokens":14})):
            with self.assertRaises(RuntimeError):
                app.chat({"session_id":session["id"],"text":"Привет"})
        self.assertEqual(app.store.runs()[0]["total_tokens"],14)
        self.assertEqual(app.models.store.history()[0]["usage"]["total_tokens"],14)

    def test_failed_codex_keeps_observed_usage_in_shared_history(self):
        app=Application(self.root)
        session=app.store.create_session()
        def fail(instructions,prompt,model,trace,**kwargs):
            trace["usage"]={"inputTokens":8,"totalTokens":8}
            raise RuntimeError("Quota reached")
        with patch.object(app.codex,"complete",side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,"Quota"):
                app.chat({"session_id":session["id"],"text":"Привет"})
        self.assertEqual(app.models.store.history()[0]["usage"]["total_tokens"],8)
        self.assertEqual(app.store.runs()[0]["total_tokens"],8)

    def test_minimax_wire_thinking_efforts_images_and_cache(self):
        profile=self.minimax()
        for effort in ("default","low","medium","high","xhigh","max"):
            profile["reasoning"]=effort
            url, headers, body=protocol.build_request(profile,"instructions","input",{"type":"object"},[{"url":"data:image/png;base64,YQ=="}])
            self.assertEqual(url,"https://api.minimax.io/anthropic/v1/messages")
            self.assertEqual(headers["Authorization"],"Bearer test-key-never-disclose")
            self.assertNotIn("x-api-key",headers)
            self.assertEqual(body["thinking"],{"type":"adaptive"})
            self.assertEqual(body.get("output_config"),None if effort=="default" else {"effort":effort})
            self.assertEqual(body["messages"][0]["content"][1]["source"]["media_type"],"image/png")
        usage=protocol.normalize_usage("minimax",{"input_tokens":10,"output_tokens":5,"cache_read_input_tokens":20,"cache_creation_input_tokens":3})
        self.assertEqual(usage,{"input_tokens":33,"output_tokens":5,"total_tokens":38,"cached_input_tokens":20,"cache_write_tokens":3})
        for bad in ("https://evil.test/anthropic","http://api.minimax.io/anthropic","https://api.minimax.io/anthropic?key=oops"):
            with patch.dict(os.environ,MINIMAX_BASE_URL=bad),self.assertRaises(ValueError):
                protocol.endpoint("minimax")

    def test_api_http_failure_hides_remote_text_and_does_not_follow_redirect(self):
        profile=self.minimax()
        error=HTTPError("https://api.minimax.io/anthropic/v1/messages",401,"secret remote body",{},io.BytesIO(b"test-key-never-disclose"))
        with patch("saruman.roles.build_opener") as opener:
            opener.return_value.open.side_effect=error
            with self.assertRaisesRegex(RuntimeError,"HTTP 401") as caught:
                api_complete(profile,"system","prompt",{})
        self.assertNotIn("test-key",str(caught.exception))
        error.close()

    def test_model_settings_http_csrf_validation_and_static_ui(self):
        app=Application(self.root)
        server=make_server(app,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base="http://127.0.0.1:"+str(server.server_port)
        try:
            payload={"role":"telegram","revision":0,"profile":{**settings.default_profile(),"provider":"minimax","model":"MiniMax-M3.1-Flash","reasoning":"low"}}
            request=Request(base+"/api/models",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
            with self.assertRaises(HTTPError) as denied:
                urlopen(request)
            self.assertEqual(denied.exception.code,403)
            denied.exception.close()
            request.add_header("X-Saruman-Token",app.token)
            with urlopen(request) as response:
                self.assertEqual(json.load(response)["profiles"]["telegram"]["provider"],"minimax")
            for path in ("/roles.js","/app.js","/"):
                with urlopen(base+path) as response:
                    text=response.read().decode()
                self.assertNotIn("????",text)
            self.assertEqual(app.models.store.history(),[])  # Saving/opening never runs LLM.
        finally:
            server.shutdown();server.server_close();thread.join()
