# Native Gemini Web Image Generation (Nano Banana) — Implementation Plan

## Goal

Expose Gemini Web's own image-generation capability through this repository's OpenAI-compatible API, authenticated with a user-configured Gemini Web session. Do not add a separate Gemini API key or external image-generation provider for this path.

This document records the current diagnosis and the evidence-led implementation plan. It is a plan, not a claim that live image generation is working.

## Current status

- Working branch: `feature/gemini-web-native-image-generation`
- Local API server starts on `127.0.0.1:8081`.
- `GET /v1/models` returned HTTP 200.
- `GEMINI_WEB2API_IMAGE_BACKEND=gemini-web` is enabled in the test shell.
- `config.json` points `cookie_file` to a local `cookie.txt`; startup reports `Cookie: yes`.
- Automated tests: 35 passed.
- Live `POST /v1/images/generations` currently returns HTTP 502 with `Gemini Web returned no generated-image URLs`.
- A live diagnostic call to the existing `StreamGenerate` request returned a raw response (17,684 characters), and the current frame parser found 7 `wrb.fr` envelopes but 0 generated image URLs.
- A separate safe diagnostic counted 9 URL-like strings, all hosted on `fonts.gstatic.com`; none appeared to be generated-image URLs.
- The existing `_generated_image_urls()` parser expects a particular nested candidate/rich-content layout. The live response inspected so far did not match that expected image-result structure.
- The current `gemini.py` implementation sends a form-encoded POST to Gemini Web's `StreamGenerate` endpoint. We have not yet established that this is the correct native image-generation flow or that WebSocket is used for image generation.
- The model registry maps `gemini-3.6-flash` to mode 1 / thinking mode 4, but this alone does not prove Nano Banana/image-generation capability or correct model routing.

## Main problems and planned solutions

### P0 — Identify the actual Gemini Web image-generation flow

**Problem:** The current adapter invokes the existing text-oriented `StreamGenerate` flow and then searches its response for image URLs. The live response does not expose the expected image-result structure.

**Plan:**
1. Generate one image manually in Gemini Web using the intended account and image model.
2. In browser DevTools Network, inspect the request sequence associated with that successful generation.
3. Inspect Fetch/XHR requests and any WebSocket connections/frames. Do not assume WebSocket is required; determine the actual transport from observed traffic.
4. Record sanitized request paths, operation names, payload field names/types, response envelope shape, completion events, and image-result metadata.
5. Do not capture or commit cookie values, authorization headers, session tokens, private prompts, or full authenticated URLs.

**Acceptance criteria:** A successful browser generation has been associated with a specific request flow and the necessary request/response structure is documented without secrets.

### P0 — Determine transport and protocol lifecycle

**Problem:** The user wants to access Gemini Web through its native website protocol, including WebSocket if that is what the site actually uses. Current repository code uses an HTTP POST to `StreamGenerate`; there is no evidence yet that image generation uses WebSocket.

**Plan:**
- Determine whether image generation uses HTTP, streaming HTTP, WebSocket, or a sequence of multiple requests.
- If WebSocket is involved, document connection setup, message types, completion/failure events, reconnection behavior, and how image results are delivered.
- If the actual generation flow is HTTP-based, implement that flow instead of adding an unnecessary WebSocket layer.
- Keep protocol-specific code behind the image-provider boundary.

**Acceptance criteria:** Transport and lifecycle are based on captured, sanitized evidence and reproduced in a controlled test.

### P1 — Verify model and image capability routing

**Problem:** Existing model aliases do not prove that the selected model/account supports the desired native image-generation feature. Unknown model names currently fall back to a default model, which can hide routing mistakes.

**Plan:**
- Record the model and image-generation mode selected in the Gemini Web UI during a successful image generation.
- Identify the corresponding model identifier/flags in the observed request.
- Map only verified model behavior in the adapter.
- Return an explicit unsupported-model/capability error rather than silently implying image generation is available.

**Acceptance criteria:** The API routes to a verified supported model/mode, or returns a clear actionable error.

### P1 — Update response parsing based on real response evidence

**Problem:** `_generated_image_urls()` expects nested JSON at a fixed candidate/rich-content path. Live diagnostics have not found that structure or a generated-image URL.

**Plan:**
- Inspect the sanitized structure of a successful image-generation response from the browser.
- Determine whether the output is an image URL, resource/reference ID, image bytes, or a later follow-up request.
- Implement extraction based on the verified schema, not guessed array indices.
- Handle multiple frames, anti-XSSI prefix, length-prefixed frames, partial responses, duplicate results, and non-image/text-only responses.
- Do not treat unrelated resource URLs (for example, font assets) as generated images.

**Acceptance criteria:** Parser extracts an actual generated image result from a captured sanitized fixture and rejects text-only responses with a clear diagnostic.

### P1 — Integrate the native provider with the public API

**Problem:** The API route is reachable but live generation fails with HTTP 502.

**Plan:**
- Keep the native provider behind `GEMINI_WEB2API_IMAGE_BACKEND=gemini-web`.
- Integrate the verified request flow with `POST /v1/images/generations`.
- Support image edits only if the native Web flow is verified to support them.
- Return the documented OpenAI-compatible response shape for successful output.
- Preserve clear status codes for missing/expired authentication, unsupported capability, upstream rejection, timeout, and parse failure.
- Do not claim support for `b64_json` unless the implementation can actually return valid base64 image data.

**Acceptance criteria:** A real request through the API produces a real image result and a valid API response without an external image-provider API key.

### P1 — Session, privacy, and service security

**Problem:** Gemini Web cookies are sensitive session credentials, can expire, and generated image URLs may be authenticated or private.

**Plan:**
- Keep the cookie file local and out of Git; verify ignore rules before any commit.
- Never print cookie contents, authorization headers, SAPISID/PSID values, or full secret-bearing request URLs.
- Redact secrets from exceptions and debug logs.
- Provide actionable authentication-expired errors.
- Keep the service loopback-only by default. Before remote exposure, require API authentication, HTTPS, rate limiting, and appropriate access controls.
- Validate whether returned image URLs are accessible to the intended client; do not promise permanent/public URLs without testing.

**Acceptance criteria:** No secrets appear in test output, logs, documentation, or commits; remote access remains protected.

### P2 — Tests and regression coverage

**Problem:** The existing 35 tests pass, but they do not establish that real Gemini Web image generation works.

**Plan:**
- Add sanitized fixtures captured from the verified successful request/response flow.
- Unit-test frame decoding, response classification, image-result extraction, duplicate handling, and text-only/upstream-error behavior.
- Mock network calls for deterministic tests.
- Run the complete test suite.
- Run a separate live smoke test with the user's local authenticated session; never make live credentials part of automated CI.
- Record live test outcome separately from unit-test success.

**Acceptance criteria:** Unit/regression tests pass, and a separate live smoke test generates an actual image through `/v1/images/generations`.

## Ordered execution checklist

- [ ] 1. Capture a successful manual Gemini Web image-generation network flow.
- [ ] 2. Determine the actual transport (WebSocket vs HTTP/streaming/multi-request flow).
- [ ] 3. Document sanitized request and response structure.
- [ ] 4. Verify model/mode routing and account capability.
- [ ] 5. Add sanitized regression fixtures and tests.
- [ ] 6. Implement the verified transport and response parser.
- [ ] 7. Integrate with `/v1/images/generations`; add edits only if verified.
- [ ] 8. Run unit tests and inspect logs for secret leakage.
- [ ] 9. Run a live image-generation smoke test and verify the returned result.
- [ ] 10. Update API documentation with verified behavior and limitations.

## Non-goals / constraints

- Do not use a separate Gemini API key or external image-generation provider for the native Gemini Web backend.
- Do not assume that a text-generation prompt automatically activates Nano Banana.
- Do not assume WebSocket is the image-generation transport until observed in the browser network trace.
- Do not scrape, commit, or share session cookies or other authentication secrets.
- Do not claim success based only on mocks, model listing, or HTTP 200 from unrelated endpoints.
- Gemini Web's internal protocol may change; compatibility must be validated against live behavior and applicable service terms/account limits.

## Current blocker

**The immediate blocker is protocol discovery:** we need the sanitized network request/response sequence from a successful image generation in Gemini Web. Until that is captured, further parser changes would be guesswork. Once available, implement the actual flow on this branch and verify it with a live end-to-end test.
