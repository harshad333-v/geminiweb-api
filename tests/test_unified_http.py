"""HTTP smoke tests for the unified server without contacting Gemini upstream."""
import json
import os
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from gemini_web2api.config import CONFIG
from gemini_web2api.unified_server import UnifiedGeminiHandler, UnifiedThreadedServer


class UnifiedHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.old_db_path = os.environ.get("GEMINI_WEB2API_DB_PATH")
        os.environ["GEMINI_WEB2API_DB_PATH"] = os.path.join(cls.tempdir.name, "sessions.sqlite3")
        cls.old_api_keys = CONFIG.get("api_keys")
        CONFIG["api_keys"] = []
        cls.server = UnifiedThreadedServer(("127.0.0.1", 0), UnifiedGeminiHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:{}/".format(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        CONFIG["api_keys"] = cls.old_api_keys
        if cls.old_db_path is None:
            os.environ.pop("GEMINI_WEB2API_DB_PATH", None)
        else:
            os.environ["GEMINI_WEB2API_DB_PATH"] = cls.old_db_path
        cls.tempdir.cleanup()

    def request_json(self, path, method="GET", payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path.lstrip("/"),
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_readiness(self):
        status, health = self.request_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        status, ready = self.request_json("/ready")
        self.assertEqual(status, 200)
        self.assertEqual(ready["status"], "ready")

    def test_create_and_list_session(self):
        status, created = self.request_json("/v1/sessions", "POST", {"title": "smoke test"})
        self.assertEqual(status, 201)
        self.assertTrue(created["id"].startswith("sess_"))
        status, sessions = self.request_json("/v1/sessions")
        self.assertEqual(status, 200)
        self.assertIn(created["id"], [item["id"] for item in sessions["data"]])

    def test_capabilities_endpoint(self):
        status, response = self.request_json("/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(response["object"], "capability.list")
        self.assertIsInstance(response["data"], dict)
        self.assertIn("chat.completions", response["data"])


if __name__ == "__main__":
    unittest.main()
