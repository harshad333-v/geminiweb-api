# Capturing Gemini Web Nano Banana network traffic

This is a one-time protocol-discovery step for the native Gemini Web backend. It does
not ask for a Gemini Developer API key.

## Capture one successful generation

1. Open https://gemini.google.com in the browser profile that is already signed in.
2. Open DevTools → **Network**. Enable **Preserve log** and clear the current entries.
3. Select the image-generation mode/model in Gemini Web and generate one ordinary test image.
4. Wait until the image is visibly complete.
5. In Network, inspect both **Fetch/XHR** and **WS**. If a WebSocket appears, inspect its Messages/Frames panel too.
6. Export the Network log as HAR with content. Chrome may not include WebSocket frame payloads in HAR; if not, record the relevant frame direction and JSON shape separately. Do not copy cookie values.

## Produce a sanitized structural report

Save the HAR locally as gemini-image-generation.har, then run from the repository root:

    python scripts/inspect_gemini_image_trace.py gemini-image-generation.har --output gemini-image-trace-report.json

The report deliberately omits:
- Cookie and authorization header values
- Query parameter values (including XSRF/session tokens)
- Request body values and prompt text
- Full URLs and image resource tokens
- Response scalar values

It retains method, host, a redacted path, query parameter names, header names, JSON structure, response MIME type, and image-resource host hints.

## Share only the report

Review gemini-image-trace-report.json before sharing it. Do not share the original HAR, screenshots showing cookies, copied request headers, or raw WebSocket frames. If the report is empty, provide the request method + host + redacted path for the successful generation request, and say whether it appeared under Fetch/XHR or WS.

## Why this is required

The existing native adapter calls the text-oriented StreamGenerate endpoint and expects a response shape that was not present in the observed live response. Implementing another guessed field index or forcing a WebSocket client would repeat the same failure. The report lets us identify the actual browser request sequence, model/mode routing, transport, and image result representation before writing the provider adapter.

This inspection utility is diagnostic only. It does not replay requests, automate the Gemini UI, bypass account controls, or extract authentication credentials.
