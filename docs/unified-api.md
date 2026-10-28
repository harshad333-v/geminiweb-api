# Unified API: endpoints, configuration, and limits

This document describes the V1 feature branch. Existing endpoints remain served by the legacy handler; new endpoints are implemented by `UnifiedGeminiHandler`.

## Configuration

Use a JSON config file for core settings and environment variables for secrets:

- `GEMINI_WEB2API_API_KEYS`: comma-separated public API keys; takes precedence over the JSON list.
- `GEMINI_WEB2API_HOST`, `GEMINI_WEB2API_PORT`: bind host and port.
- `GEMINI_WEB2API_UPLOAD_DIR`: upload storage directory (default `./data/uploads`).
- `GEMINI_WEB2API_IMAGE_BASE_URL`: root URL of an OpenAI-compatible image provider, e.g. `https://provider.example` or `https://provider.example/v1`.
- `GEMINI_WEB2API_IMAGE_API_KEY`: optional provider credential.
- `GEMINI_WEB2API_IMAGE_TIMEOUT_SEC`: image provider timeout, clamped to 1–300 seconds.

For public deployment, configure API keys, HTTPS termination, a trusted CORS allowlist, and upstream provider limits. Do not set `allow_unauthenticated_remote=true` on an internet-facing instance. With no configured API keys, only loopback clients are authorized for API routes.

## Endpoint summary

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Process liveness |
| GET | `/ready` | Basic readiness metadata; does not prove upstream Gemini availability |
| GET | `/v1/models` | Existing model listing |
| GET | `/v1/capabilities` | Capability metadata |
| GET | `/v1/usage` | Per-identity request counts for this process |
| GET | `/v1/sessions` | List the caller's saved sessions |
| POST | `/v1/sessions` | Create a saved conversation session |
| GET | `/v1/sessions/{id}` | Read a caller-owned session |
| DELETE | `/v1/sessions/{id}` | Delete a caller-owned session |
| POST | `/v1/chat/completions` | Existing OpenAI-compatible chat |
| POST | `/v1/responses` | Existing Responses API |
| POST | `/v1/files` | Store UTF-8 text or base64 file content |
| POST | `/v1/extractions` | Extract supported document content |
| GET | `/v1/jobs/{id}` | Read extraction job status/result |\n| GET | `/v1/jobs/{id}/result` | Read a completed extraction result |
| DELETE | `/v1/jobs/{id}` | Cancel a queued/running extraction job |
| POST | `/v1/images/generations` | Forward an OpenAI-compatible generation request when configured |
| POST | `/v1/images/edits` | Forward a JSON base64 image-edit request when configured |
| WS | `/v1/ws` | Application-level JSON heartbeat/chat protocol |

Existing Google-native routes remain available. WebSocket is not a Gemini upstream protocol and does not replace SSE.

## File API

Upload text:

```json
{
  "filename": "notes.txt",
  "content": "Meeting notes"
}
```

Upload binary content:

```json
{
  "filename": "table.xlsx",
  "content_base64": "UEsDB..."
}
```

The response returns a generated `file_...` ID. Use that ID in an extraction request:

```json
{
  "file_id": "file_...",
  "async": true
}
```

For inline extraction, provide `filename` plus either `content` or `content_base64`. Supported formats are TXT, Markdown, CSV, JSON, PDF, DOCX, and XLSX. PDF/XLSX support requires optional dependencies: `pip install '.[documents]'`. DOCX parsing uses Python's standard library. Scanned-PDF OCR and XLSX-to-CSV conversion are not included in this initial implementation.

Extraction `schema` is currently returned as request metadata; it is **not** a JSON Schema validator. Do not treat the output as schema-validated until a validator is added.

## Image API

Set `GEMINI_WEB2API_IMAGE_BASE_URL` to an OpenAI-compatible image API provider root, and optionally set `GEMINI_WEB2API_IMAGE_API_KEY`. Generation forwards supported fields: `model`, `prompt`, `n`, `size`, `quality`, `style`, `response_format`, and `user`.

For edits, this API accepts JSON with `prompt`, `image_base64`, optional `filename`, and supported provider fields, then converts the image to multipart form data upstream. This is a convenience extension; it is not identical to OpenAI's multipart client request format. Provider-specific support and billing apply.

## Persistent chat sessions

Create a session with `POST /v1/sessions` and optional JSON `{"title":"Project notes"}`. Use the returned `sess_...` identifier on a non-streaming `POST /v1/chat/completions` request via the additional `session_id` field. Send only the new turn's `messages`; the server prepends saved history and stores the new messages plus assistant response after a successful completion. Streaming session requests are currently rejected. Sessions are stored in SQLite at `./data/gemini-web2api.sqlite3` by default; override with `GEMINI_WEB2API_DB_PATH`. Every session is scoped to the API key identity that created it.

## WebSocket protocol

Connect to `/v1/ws` with the same API key in the `Authorization: Bearer ...` header.

Heartbeat:

```json
{"type":"ping","request_id":"req-1"}
```

Chat request:

```json
{
  "type": "chat.completion",
  "request_id": "req-2",
  "model": "gemini-3.6-flash",
  "messages": [{"role":"user","content":"Hello"}]
}
```

The server responds with a JSON `pong`, `chat.completion`, or `error` event. This V1 protocol handles one request at a time per connection; it does not stream token deltas. Use the existing SSE chat endpoint for streaming. The low-level WebSocket implementation supports unfragmented text frames and basic ping/close frames; use a reverse proxy with sensible connection timeouts.

## Operational limitations

- Uploaded files persist on local disk and include per-key ownership metadata; define retention/cleanup for production.
- Job state, request quotas, and usage counters are in process memory; they reset on restart and are not coordinated across replicas. No admin dashboard, OCR worker, or multi-replica job broker is included yet.
- Image generation/editing require an independently configured provider; Gemini Web itself is not assumed to support these operations.
- The API uses a reverse-engineered Gemini Web integration. Upstream changes, account restrictions, and quotas can affect availability.
- The upstream image URL fetch path now rejects non-global IP destinations, validates redirects, and caps response size. DNS rebinding and proxy-specific routing still require deployment-level controls; do not treat this as a substitute for an egress firewall.
