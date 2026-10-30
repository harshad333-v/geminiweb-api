import json
import unittest
from urllib.parse import quote

from scripts.inspect_gemini_image_trace import inspect_har


class GeminiImageTraceInspectorTests(unittest.TestCase):
    def test_extracts_request_structure_without_secrets_or_prompt(self):
        har = {
            "log": {
                "entries": [{
                    "request": {
                        "method": "POST",
                        "url": "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/Generate?at=SECRET_TOKEN&rpcids=SECRET_RPC",
                        "headers": [
                            {"name": "Cookie", "value": "SID=SECRET_COOKIE"},
                            {"name": "Authorization", "value": "Bearer SECRET"},
                            {"name": "Content-Type", "value": "application/x-www-form-urlencoded"},
                        ],
                        "postData": {"text": "f.req=" + quote('[["image operation", "PRIVATE PROMPT"]]')},
                    },
                    "response": {
                        "status": 200,
                        "content": {
                            "mimeType": "application/json",
                            "text": '[{"result":{"image":"https://lh3.googleusercontent.com/private-token"}}]',
                        },
                    },
                }]
            }
        }
        report = inspect_har(har)
        rendered = json.dumps(report)
        self.assertEqual(report["candidate_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["method"], "POST")
        self.assertIn("at", candidate["query_parameter_names"])
        self.assertNotIn("SECRET_TOKEN", rendered)
        self.assertNotIn("SECRET_COOKIE", rendered)
        self.assertNotIn("PRIVATE PROMPT", rendered)
        self.assertNotIn("private-token", rendered)
        self.assertTrue(candidate["response_resource_hints"])

    def test_includes_websocket_frame_shapes_without_frame_values(self):
        har = {
            "log": {
                "entries": [{
                    "request": {
                        "method": "GET",
                        "url": "wss://gemini.google.com/ws",
                        "headers": [{"name": "Cookie", "value": "SECRET_COOKIE"}],
                    },
                    "response": {"status": 101, "content": {"mimeType": "application/json"}},
                    "_webSocketMessages": [
                        {"type": "send", "opcode": 1, "data": '{"prompt":"DO_NOT_PRINT"}'},
                        {"type": "receive", "opcode": 1, "data": '{"image":"https://lh3.googleusercontent.com/secret"}'},
                    ],
                }]
            }
        }
        report = inspect_har(har)
        rendered = json.dumps(report)
        self.assertEqual(report["candidates"][0]["transport"], "websocket")
        self.assertEqual(len(report["candidates"][0]["websocket_frames"]), 2)
        self.assertNotIn("SECRET_COOKIE", rendered)
        self.assertNotIn("DO_NOT_PRINT", rendered)
        self.assertNotIn("googleusercontent.com/secret", rendered)


if __name__ == "__main__":
    unittest.main()
