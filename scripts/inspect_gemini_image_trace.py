#!/usr/bin/env python3
"""Inspect a Chrome DevTools HAR export for Gemini image-generation traffic.

Only request/response structure is reported. Cookie/header values, request bodies,
prompt text, full URLs, query values, and opaque resource identifiers are omitted.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

SECRET_HEADER_NAMES = {"authorization", "cookie", "set-cookie", "x-goog-authuser", "x-xsrf-token"}
IMAGE_HOST_HINTS = ("googleusercontent.com", "ggpht.com", "googleapis.com")
OPERATION_HINTS = ("image", "imagen", "generate", "media", "upload", "streamgenerate", "bard", "assistant")
SAFE_PATH_SEGMENTS = {
    "api", "v1", "v1beta", "image", "images", "media", "upload", "generate",
    "generation", "download", "bardchatui", "data", "assistant", "streamgenerate",
    "ws", "rpc", "models",
}
URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")


def _json_values(text):
    if not isinstance(text, str) or not text:
        return
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        while pos < len(text) and (text[pos].isspace() or text[pos].isdigit()):
            pos += 1
        if pos >= len(text):
            break
        try:
            value, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            pos += 1
            continue
        yield value
        pos = end


def _shape(value, depth=0):
    if depth > 12:
        return "<max-depth>"
    if isinstance(value, dict):
        return {str(key): _shape(item, depth + 1) for key, item in list(value.items())[:80]}
    if isinstance(value, list):
        return [] if not value else [_shape(value[0], depth + 1), {"additional_items": max(0, len(value) - 1)}]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _safe_path_hint(path):
    result = []
    for segment in [part for part in (path or "").split("/") if part][:8]:
        normalized = segment.lower()
        if normalized in SAFE_PATH_SEGMENTS or re.fullmatch(r"[^/]{1,32}\.(?:json|js|css)", segment, re.I):
            result.append(segment[:40])
        else:
            result.append("<redacted>")
    return "/" + "/".join(result) if result else "/"


def _walk(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, path + "." + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value[:100]):
            yield from _walk(item, path + "[" + str(index) + "]")
    elif isinstance(value, str):
        for match in URL_RE.findall(value):
            parsed = urlsplit(match.rstrip("),.;"))
            host = (parsed.hostname or "").lower()
            if host:
                yield {"field": path, "host": host, "path_hint": _safe_path_hint(parsed.path)}
        if len(value) > 512 and re.fullmatch(r"[A-Za-z0-9_+/=-]+", value):
            yield {"field": path, "kind": "long_base64_like_string", "characters": len(value)}


def _parse_body_shape(entry):
    text = ((entry.get("request") or {}).get("postData") or {}).get("text")
    if not isinstance(text, str) or not text:
        return None
    for value in _json_values(text):
        return _shape(value)
    fields = {}
    for key, value in parse_qsl(text, keep_blank_values=True):
        nested = next(_json_values(value), None)
        fields[key] = _shape(nested) if nested is not None else "string"
    return fields or {"body": "non-json-text", "characters": len(text)}


def _parse_response_shape(entry):
    content = ((entry.get("response") or {}).get("content") or {})
    text = content.get("text")
    if not isinstance(text, str) or not text:
        return None, []
    values = list(_json_values(text))
    shapes = [_shape(value) for value in values[:4]]
    findings = [item for index, value in enumerate(values[:4])
                for item in _walk(value, "$response[" + str(index) + "]")]
    for match in URL_RE.findall(text):
        parsed = urlsplit(match.rstrip("),.;"))
        host = (parsed.hostname or "").lower()
        if host and not any(item.get("host") == host for item in findings):
            findings.append({"field": "$response-text", "host": host,
                             "path_hint": _safe_path_hint(parsed.path)})
    return shapes or {"body": "non-json-text", "characters": len(text)}, findings


def _is_candidate(entry, findings):
    request = entry.get("request") or {}
    parsed = urlsplit(request.get("url", ""))
    path = (parsed.path or "").lower()
    mime = ((entry.get("response") or {}).get("content") or {}).get("mimeType", "").lower()
    query_names = " ".join(key for key, _ in parse_qsl(parsed.query))
    ws = entry.get("_webSocketMessages") or entry.get("webSocketMessages") or []
    return (
        bool(ws)
        or any(word in path + " " + query_names for word in OPERATION_HINTS)
        or mime.startswith("image/")
        or any(any(hint in item.get("host", "") for hint in IMAGE_HOST_HINTS) for item in findings)
        or ("json" in mime and any(word in path for word in ("image", "media", "upload", "generate")))
    )


def inspect_har(document):
    entries = (document.get("log") or {}).get("entries") or []
    report = {
        "format": "gemini-image-trace-report-v1",
        "entry_count": len(entries),
        "candidate_count": 0,
        "candidates": [],
        "safety": {
            "values_omitted": [
                "cookies", "authorization headers", "query parameter values",
                "request bodies", "prompt text", "full URLs", "response scalar values",
                "opaque URL path segments",
            ],
            "note": "Review this report before sharing it. HAR files themselves contain secrets.",
        },
    }
    for index, entry in enumerate(entries):
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        parsed = urlsplit(request.get("url", ""))
        response_shape, findings = _parse_response_shape(entry)
        ws_messages = entry.get("_webSocketMessages") or entry.get("webSocketMessages") or []
        if not _is_candidate(entry, findings):
            continue
        report["candidate_count"] += 1
        candidate = {
            "entry_index": index,
            "transport": "websocket" if ws_messages else "http",
            "method": request.get("method", "UNKNOWN"),
            "host": parsed.hostname or "<unknown>",
            "path": _safe_path_hint(parsed.path),
            "query_parameter_names": sorted({key for key, _ in parse_qsl(parsed.query)}),
            "request_header_names": sorted({
                str(item.get("name", "")).lower() for item in request.get("headers", [])
                if item.get("name", "").lower() not in SECRET_HEADER_NAMES
            }),
            "request_body_shape": _parse_body_shape(entry),
            "status": response.get("status"),
            "response_mime_type": (response.get("content") or {}).get("mimeType"),
            "response_shape": response_shape,
            "response_resource_hints": findings[:40],
        }
        if ws_messages:
            candidate["websocket_frames"] = []
            for frame_index, frame in enumerate(ws_messages[:100]):
                data = frame.get("data", "")
                values = list(_json_values(data)) if isinstance(data, str) else []
                candidate["websocket_frames"].append({
                    "index": frame_index,
                    "direction": frame.get("type", "unknown"),
                    "opcode": frame.get("opcode"),
                    "payload_shape": [_shape(value) for value in values[:2]] or
                                     ({"characters": len(data)} if isinstance(data, str) else None),
                    "resource_hints": [item for value in values[:2]
                                       for item in _walk(value, "$websocket")][:20],
                })
        report["candidates"].append(candidate)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har_file", type=Path, help="Chrome DevTools HAR export")
    parser.add_argument("--output", type=Path, help="write sanitized JSON report to this path")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.har_file.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("log"), dict):
            raise ValueError("input does not look like a HAR 1.2 document")
        report = inspect_har(document)
        rendered = json.dumps(report, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
            print("Sanitized report written to " + str(args.output))
        else:
            print(rendered)
        print("Candidates: " + str(report["candidate_count"]), file=sys.stderr)
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print("HAR inspection failed: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
