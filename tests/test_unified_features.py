import base64
import json
import os
import tempfile
import unittest

from gemini_web2api.documents import DocumentError, extract_document
from gemini_web2api.security import decode_file_content, safe_filename, save_upload
from gemini_web2api.multimodal import _validate_public_image_url
from gemini_web2api.image_provider import ImageProviderError, generate_image
from gemini_web2api import image_provider
from gemini_web2api.storage import append_messages, create_session, delete_session, get_session, list_sessions


class DocumentExtractionTests(unittest.TestCase):
    def test_text_extraction(self):
        result = extract_document("notes.txt", b"hello\nworld")
        self.assertEqual(result["text"], "hello\nworld")
        self.assertEqual(result["format"], "txt")

    def test_csv_extraction(self):
        result = extract_document("people.csv", b"name,age\nAda,36\n")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["rows"][0]["name"], "Ada")

    def test_json_extraction(self):
        result = extract_document("data.json", b'{"ok": true}')
        self.assertEqual(result["data"], {"ok": True})

    def test_unsupported_extension_is_rejected(self):
        with self.assertRaises(DocumentError):
            extract_document("payload.exe", b"no")

    def test_upload_limit_is_enforced(self):
        with self.assertRaises(DocumentError):
            extract_document("notes.txt", b"1234", max_bytes=3)


class SecurityHelperTests(unittest.TestCase):
    def test_base64_content(self):
        self.assertEqual(decode_file_content({"content_base64": base64.b64encode(b"hi").decode()}), b"hi")

    def test_invalid_base64_rejected(self):
        with self.assertRaises(ValueError):
            decode_file_content({"content_base64": "%%%"})

    def test_filename_drops_paths(self):
        self.assertEqual(safe_filename("../../hello.txt"), "hello.txt")

    def test_private_image_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            _validate_public_image_url("http://127.0.0.1/private.png")


    def test_gemini_web_generated_image_url_parser(self):
        image_url = "https://lh3.googleusercontent.com/generated-image"
        image_item = [[None, None, None, [None, None, None, image_url]]]
        rich_content = [None] * 8
        rich_content[7] = [[image_item]]
        candidate = [None] * 13
        candidate[0] = "candidate-id"
        candidate[12] = rich_content
        response = [None] * 5
        response[4] = [candidate]
        raw = json.dumps([["wrb.fr", None, json.dumps(response)]])
        self.assertEqual(image_provider._generated_image_urls(raw), [image_url])

    def test_image_generation_requires_configured_provider(self):
        import os
        previous = os.environ.pop("GEMINI_WEB2API_IMAGE_BASE_URL", None)
        try:
            with self.assertRaises(ImageProviderError) as error:
                generate_image({"prompt": "test"})
            self.assertEqual(error.exception.status, 501)
        finally:
            if previous is not None:
                os.environ["GEMINI_WEB2API_IMAGE_BASE_URL"] = previous

    def test_upload_uses_generated_id(self):
        with tempfile.TemporaryDirectory() as root:
            result = save_upload(root, b"hello", "hello.txt")
            self.assertTrue(result["id"].startswith("file_"))
            self.assertTrue(os.path.isfile(result["path"]))
            with open(result["path"], "rb") as handle:
                self.assertEqual(handle.read(), b"hello")


class SessionStorageTests(unittest.TestCase):
    def test_session_round_trip_is_owner_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            old_path = os.environ.get("GEMINI_WEB2API_DB_PATH")
            os.environ["GEMINI_WEB2API_DB_PATH"] = os.path.join(root, "sessions.sqlite3")
            try:
                created = create_session("owner-a", "Test session")
                self.assertTrue(created["id"].startswith("sess_"))
                self.assertTrue(append_messages("owner-a", created["id"], [{"role": "user", "content": "hello"}]))
                loaded = get_session("owner-a", created["id"])
                self.assertEqual(loaded["messages"][0]["content"], "hello")
                self.assertIsNone(get_session("owner-b", created["id"]))
                self.assertEqual(len(list_sessions("owner-a")), 1)
                self.assertFalse(delete_session("owner-b", created["id"]))
                self.assertTrue(delete_session("owner-a", created["id"]))
            finally:
                if old_path is None:
                    os.environ.pop("GEMINI_WEB2API_DB_PATH", None)
                else:
                    os.environ["GEMINI_WEB2API_DB_PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
