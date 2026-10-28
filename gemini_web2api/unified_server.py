"""Unified API extensions layered over the legacy-compatible handler.

The original handler remains responsible for existing chat/Responses/Google routes.
"""
import base64
import collections
import hashlib
import json
import os
import re
import socket
import struct
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .capabilities import public_capabilities
from .config import CONFIG
from .documents import DocumentError, extract_document
from .gemini import generate, log
from .models import MODELS, resolve_model
from .image_provider import ImageProviderError, edit_image, generate_image
from .security import decode_file_content, safe_filename, save_upload
from .server import GeminiHandler, ThreadedServer

MAX_BODY_DEFAULT = 10 * 1024 * 1024
_UPLOAD_ROOT = Path(os.environ.get("GEMINI_WEB2API_UPLOAD_DIR", "./data/uploads"))
_JOBS = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = threading.BoundedSemaphore(4)
_REQUEST_TIMES = collections.defaultdict(collections.deque)
_USAGE_TOTALS = collections.Counter()
_USAGE_LOCK = threading.Lock()


def _error(message, code="invalid_request_error"):
    return {"error": {"message": str(message), "type": code}}


class UnifiedGeminiHandler(GeminiHandler):
    """Adds security defaults, health, file/document endpoints and WebSocket chat."""

    def _identity(self):
        """Return a non-reversible tenant identifier for files and jobs."""
        auth = self.headers.get("Authorization", "")
        key = auth[7:] if auth.startswith("Bearer ") else ""
        key = key or self.headers.get("x-api-key", "") or self.headers.get("x-goog-api-key", "")
        if not key and "?" in self.path:
            for pair in self.path.split("?", 1)[1].split("&"):
                if pair.startswith("key="):
                    key = pair[4:]
                    break
        if key:
            return hashlib.sha256(key.encode("utf-8")).hexdigest()
        ip = self.client_address[0] if self.client_address else "local"
        return "local:" + ip

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        allowed = CONFIG.get("cors_origins", ["http://localhost", "http://127.0.0.1"])
        if origin and ("*" in allowed or origin in allowed):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        keys = CONFIG.get("api_keys") or []
        if keys:
            return super()._authorized()
        if CONFIG.get("allow_unauthenticated_remote", False):
            return True
        # No configured keys is permitted for local development only.
        try:
            return self.client_address[0] in ("127.0.0.1", "::1")
        except (AttributeError, IndexError):
            return False

    def _read_request_body(self):
        max_bytes = int(CONFIG.get("max_request_bytes", MAX_BODY_DEFAULT))
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" not in transfer_encoding:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > max_bytes:
                raise ValueError("request body exceeds configured limit")
            return self.rfile.read(length) if length else b""

        chunks = []
        total = 0
        while True:
            line = self.rfile.readline(8192)
            if not line:
                raise ValueError("incomplete chunked request body")
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError as exc:
                raise ValueError("invalid chunked request body") from exc
            if size < 0:
                raise ValueError("invalid chunk size")
            if size == 0:
                while True:
                    trailer = self.rfile.readline(8192)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                break
            total += size
            if total > max_bytes:
                raise ValueError("request body exceeds configured limit")
            chunk = self.rfile.read(size)
            if len(chunk) != size:
                raise ValueError("incomplete chunked request body")
            chunks.append(chunk)
            ending = self.rfile.read(2)
            if ending != b"\r\n":
                raise ValueError("invalid chunk terminator")
        return b"".join(chunks)

    def _json_request(self):
        raw = self._read_request_body()
        req = self._parse_body(raw)
        if not isinstance(req, dict):
            self.send_json(_error("request body must be a valid JSON object"), 400)
            return None
        return req

    def _rate_limited(self):
        """Simple per-identity sliding-window limiter for a single process."""
        identity = self._identity()
        now = time.time()
        limit = max(1, int(CONFIG.get("max_requests_per_minute", 60)))
        with _USAGE_LOCK:
            events = _REQUEST_TIMES[identity]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= limit:
                return True
            events.append(now)
            _USAGE_TOTALS[identity] += 1
            return False

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        allowed = CONFIG.get("cors_origins", ["http://localhost", "http://127.0.0.1"])
        self.send_response(204)
        if origin and ("*" in allowed or origin in allowed):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key, X-Goog-Api-Key")
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self.send_json({"status": "ok", "version": __import__("gemini_web2api").__version__})
            return
        if path == "/ready":
            self.send_json({"status": "ready", "provider": "gemini-web", "model_count": len(MODELS)})
            return
        if path.startswith("/v1/") and path != "/v1/ws" and not self._rate_limited():
            pass
        elif path.startswith("/v1/") and path != "/v1/ws":
            self.send_json(_error("rate limit exceeded", "rate_limit_error"), 429)
            return
        if path == "/v1/usage":
            if not self._authorized():
                self.send_json(_error("invalid api key", "authentication_error"), 401)
                return
            identity = self._identity()
            with _USAGE_LOCK:
                self.send_json({"object": "usage", "requests_total": _USAGE_TOTALS[identity],
                                "requests_last_minute": len(_REQUEST_TIMES[identity]),
                                "limit_per_minute": int(CONFIG.get("max_requests_per_minute", 60))})
            return
        if path == "/v1/capabilities":
            if not self._authorized():
                self.send_json(_error("invalid api key", "authentication_error"), 401)
                return
            self.send_json({"object": "capability.list", "data": public_capabilities()})
            return
        if path.startswith("/v1/jobs/"):
            if not self._authorized():
                self.send_json(_error("invalid api key", "authentication_error"), 401)
                return
            parts = path.strip("/").split("/")
            result_only = len(parts) == 4 and parts[-1] == "result"
            job_id = parts[-2] if result_only else parts[-1]
            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
                snapshot = dict(job) if job and job.get("owner_id") == self._identity() else None
            if not snapshot:
                self.send_json(_error("job not found", "not_found_error"), 404)
            else:
                snapshot.pop("owner_key", None)
                snapshot.pop("owner_id", None)
                if result_only:
                    if snapshot.get("status") != "succeeded":
                        self.send_json(_error("job result is not available yet", "conflict_error"), 409)
                    else:
                        self.send_json({"id": job_id, "status": snapshot["status"], "result": snapshot.get("result")})
                else:
                    self.send_json(snapshot)
            return
        if path == "/v1/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_websocket()
            return
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        api_path = path.startswith("/v1")
        if api_path and self._rate_limited():
            self.send_json(_error("rate limit exceeded", "rate_limit_error"), 429)
            return
        if api_path and not self._authorized():
            self.send_json(_error("invalid api key", "authentication_error"), 401)
            return
        try:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                if int(raw_length) > int(CONFIG.get("max_request_bytes", MAX_BODY_DEFAULT)):
                    self.send_json(_error("request body exceeds configured limit"), 413)
                    return
            except ValueError:
                self.send_json(_error("invalid Content-Length"), 400)
                return
            body = self._read_request_body()
        except ValueError as exc:
            status = 413 if "exceeds" in str(exc) else 400
            self.send_json(_error(str(exc)), status)
            return
        try:
            if path == "/v1/files":
                req = self._parse_body(body)
                if not isinstance(req, dict):
                    self.send_json(_error("request body must be a valid JSON object"), 400)
                else:
                    self._create_file(req)
            elif path == "/v1/extractions":
                req = self._parse_body(body)
                if not isinstance(req, dict):
                    self.send_json(_error("request body must be a valid JSON object"), 400)
                else:
                    self._create_extraction(req)
            elif path == "/v1/images/generations":
                req = self._parse_body(body)
                if not isinstance(req, dict):
                    self.send_json(_error("request body must be a valid JSON object"), 400)
                else:
                    self.send_json(generate_image(req))
            elif path == "/v1/images/edits":
                req = self._parse_body(body)
                if not isinstance(req, dict):
                    self.send_json(_error("request body must be a valid JSON object"), 400)
                else:
                    self.send_json(edit_image(req))
            elif path == "/v1/chat/completions":
                self._handle_chat(body)
            elif path == "/v1/responses":
                self._handle_responses(body)
            elif ":streamGenerateContent" in path:
                self._handle_google_generate(body, stream=True)
            elif ":generateContent" in path:
                self._handle_google_generate(body, stream=False)
            else:
                self.send_json(_error("not found", "not_found_error"), 404)
        except ImageProviderError as exc:
            self.send_json(_error(str(exc), "provider_error"), exc.status)
        except (ValueError, DocumentError) as exc:
            self.send_json(_error(str(exc)), 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            log("Unified POST error: " + type(exc).__name__ + ": " + str(exc))
            self.send_json(_error("internal server error", "server_error"), 500)

    def do_DELETE(self):
        path = urlsplit(self.path).path
        if self._rate_limited():
            self.send_json(_error("rate limit exceeded", "rate_limit_error"), 429)
            return
        if not path.startswith("/v1/jobs/"):
            self.send_json(_error("not found", "not_found_error"), 404)
            return
        if not self._authorized():
            self.send_json(_error("invalid api key", "authentication_error"), 401)
            return
        job_id = path.rsplit("/", 1)[-1]
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if not job or job.get("owner_id") != self._identity():
                self.send_json(_error("job not found", "not_found_error"), 404)
                return
            if job.get("status") in ("succeeded", "failed", "cancelled"):
                self.send_json(_error("job has already completed"), 409)
                return
            job["cancel_requested"] = True
            job["status"] = "cancelled"
        self.send_json({"id": job_id, "status": "cancelled"})

    def _create_file(self, req):
        filename = safe_filename(req.get("filename", "upload.txt"))
        data = decode_file_content(req)
        max_bytes = int(CONFIG.get("max_upload_bytes", MAX_BODY_DEFAULT))
        if len(data) > max_bytes:
            raise ValueError("file exceeds configured upload limit")
        record = save_upload(_UPLOAD_ROOT, data, filename)
        record.pop("path", None)
        file_path = _UPLOAD_ROOT / (record["id"] + ".bin")
        metadata = {"id": record["id"], "filename": filename, "bytes": len(data),
                    "created_at": int(time.time()), "path": str(file_path), "owner_id": self._identity()}
        (_UPLOAD_ROOT / (record["id"] + ".meta.json")).write_text(
            json.dumps(metadata), encoding="utf-8")
        self.send_json({"id": record["id"], "object": "file", "filename": filename,
                        "bytes": len(data), "purpose": req.get("purpose", "extraction")}, 201)

    def _load_file(self, file_id):
        if not isinstance(file_id, str) or not re.fullmatch(r"file_[A-Za-z0-9_-]{1,64}", file_id):
            raise ValueError("invalid file_id")
        meta_path = _UPLOAD_ROOT / (file_id + ".meta.json")
        data_path = _UPLOAD_ROOT / (file_id + ".bin")
        if not meta_path.is_file() or not data_path.is_file():
            raise ValueError("file not found")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("owner_id") != self._identity():
            raise ValueError("file not found")
        data = data_path.read_bytes()
        if len(data) > int(CONFIG.get("max_upload_bytes", MAX_BODY_DEFAULT)):
            raise ValueError("stored file exceeds configured upload limit")
        return meta, data

    def _create_extraction(self, req):
        if req.get("async") is True:
            if not _MAX_JOBS.acquire(blocking=False):
                self.send_json(_error("too many active jobs; retry later", "rate_limit_error"), 429)
                return
            job_id = "job_" + uuid.uuid4().hex
            job = {"id": job_id, "object": "job", "status": "queued",
                   "created_at": int(time.time()), "result": None, "error": None,
                   "cancel_requested": False, "owner_id": self._identity()}
            with _JOBS_LOCK:
                _JOBS[job_id] = job
            thread = threading.Thread(target=self._run_extraction_job, args=(job_id, dict(req)), daemon=True)
            thread.start()
            self.send_json({"id": job_id, "object": "job", "status": "queued",
                            "status_url": "/v1/jobs/" + job_id}, 202)
            return
        result = self._extract_request(req)
        self.send_json({"object": "extraction", "status": "succeeded", "result": result})

    def _extract_request(self, req):
        if req.get("file_id"):
            meta, data = self._load_file(req["file_id"])
            filename = meta["filename"]
        else:
            filename = safe_filename(req.get("filename", "document.txt"))
            data = decode_file_content(req)
        result = extract_document(filename, data, int(CONFIG.get("max_upload_bytes", MAX_BODY_DEFAULT)))
        if req.get("schema") is not None:
            if not isinstance(req["schema"], dict):
                raise ValueError("schema must be a JSON object")
            # Schema is returned alongside deterministic extraction; no claim of JSON-Schema validation.
            result["requested_schema"] = req["schema"]
        return result

    def _run_extraction_job(self, job_id, req):
        try:
            with _JOBS_LOCK:
                if _JOBS[job_id].get("cancel_requested"):
                    return
                _JOBS[job_id]["status"] = "running"
            result = self._extract_request(req)
            with _JOBS_LOCK:
                if not _JOBS[job_id].get("cancel_requested"):
                    _JOBS[job_id]["result"] = result
                    _JOBS[job_id]["status"] = "succeeded"
        except Exception as exc:
            with _JOBS_LOCK:
                _JOBS[job_id]["error"] = str(exc)
                _JOBS[job_id]["status"] = "failed"
        finally:
            _MAX_JOBS.release()

    def _handle_websocket(self):
        if self._rate_limited():
            self.send_error(429, "Rate limit exceeded")
            return
        if not self._authorized():
            self.send_error(401, "Unauthorized")
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400, "Missing WebSocket key")
            return
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                opcode, payload = self._read_ws_frame()
                if opcode == 8:
                    self._write_ws_frame(8, payload[:125])
                    return
                if opcode == 9:
                    self._write_ws_frame(10, payload[:125])
                    continue
                if opcode != 1:
                    self._write_ws_frame(8, b"\x03\xeaUnsupported frame")
                    return
                try:
                    message = json.loads(payload.decode("utf-8"))
                    if message.get("type") in ("ping", "heartbeat"):
                        response = {"type": "pong", "request_id": message.get("request_id")}
                    elif message.get("type") in ("chat.completion", "chat.completions"):
                        model_name, model_id, think_mode, err, extra = resolve_model(
                            message.get("model", CONFIG["default_model"]))
                        if err:
                            response = {"type": "error", "request_id": message.get("request_id"),
                                        "error": {"message": err}}
                        else:
                            prompt, images = __import__("gemini_web2api.tools", fromlist=["messages_to_prompt"]).messages_to_prompt(
                                message.get("messages", []))
                            if images:
                                response = {"type": "error", "request_id": message.get("request_id"),
                                            "error": {"message": "WebSocket image input is not supported; use HTTP chat endpoint"}}
                            else:
                                answer = generate(prompt, model_id, think_mode, None, extra)
                                response = {"type": "chat.completion", "request_id": message.get("request_id"),
                                            "model": model_name, "choices": [{"message": {"role": "assistant",
                                            "content": answer}, "finish_reason": "stop"}]}
                    else:
                        response = {"type": "error", "request_id": message.get("request_id"),
                                    "error": {"message": "unsupported event type"}}
                except Exception as exc:
                    response = {"type": "error", "error": {"message": str(exc)}}
                self._write_ws_frame(1, json.dumps(response, ensure_ascii=False).encode("utf-8"))
        except (ConnectionError, OSError, ValueError, struct.error):
            return

    def _read_ws_frame(self):
        head = self.rfile.read(2)
        if len(head) != 2:
            raise ConnectionError("WebSocket disconnected")
        b1, b2 = head
        if not (b1 & 0x80):
            raise ValueError("fragmented WebSocket frames are not supported")
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        if not masked:
            raise ValueError("client WebSocket frames must be masked")
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.rfile.read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.rfile.read(8))[0]
        if length > int(CONFIG.get("max_websocket_message_bytes", 1024 * 1024)):
            raise ValueError("WebSocket message exceeds configured limit")
        mask = self.rfile.read(4) if masked else b""
        payload = self.rfile.read(length)
        if len(payload) != length:
            raise ConnectionError("incomplete WebSocket frame")
        if masked:
            payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        return opcode, payload

    def _write_ws_frame(self, opcode, payload):
        length = len(payload)
        head = bytes([0x80 | opcode])
        if length < 126:
            head += bytes([length])
        elif length < 65536:
            head += bytes([126]) + struct.pack("!H", length)
        else:
            head += bytes([127]) + struct.pack("!Q", length)
        self.wfile.write(head + payload)
        self.wfile.flush()
