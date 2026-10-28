import base64
import os
import tempfile
import unittest

from gemini_web2api.documents import DocumentError, extract_document
from gemini_web2api.security import decode_file_content, safe_filename, save_upload


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

    def test_upload_uses_generated_id(self):
        with tempfile.TemporaryDirectory() as root:
            result = save_upload(root, b"hello", "hello.txt")
            self.assertTrue(result["id"].startswith("file_"))
            self.assertTrue(os.path.isfile(result["path"]))
            self.assertEqual(open(result["path"], "rb").read(), b"hello")


if __name__ == "__main__":
    unittest.main()
