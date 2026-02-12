#!/usr/bin/env python3
"""
Tiny local HTTP server that serves static HTML and persists per-page state to disk.

Why: browsers can't write to local files from file:// pages. This server provides
same-origin /api/state endpoints so the practice UI can save/load progress as
JSON files next to the HTML, making it portable across machines.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple


def _safe_resolve(root: Path, url_path: str) -> Optional[Path]:
    # url_path is like "/foo/bar.html". Prevent traversal and ensure within root.
    raw = urllib.parse.unquote(url_path)
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    raw = posixpath.normpath(raw)
    raw = raw.lstrip("/")
    if raw.startswith(".."):
        return None
    cand = (root / raw).resolve()
    try:
        cand.relative_to(root.resolve())
    except Exception:
        return None
    return cand


def _state_path_for_doc(doc_path: Path) -> Path:
    # Store alongside the html file: foo.html -> foo.state.json
    if doc_path.suffix.lower() == ".html":
        return doc_path.with_suffix(".state.json")
    return doc_path.with_suffix(doc_path.suffix + ".state.json")


class Handler(SimpleHTTPRequestHandler):
    server_version = "IELTSStateServer/0.1"

    def _send_json(self, code: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _doc_param(self) -> Optional[str]:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query or "")
        doc = (qs.get("doc") or [None])[0]
        return doc

    def _resolve_doc(self) -> Tuple[Optional[Path], Optional[Path], Optional[str]]:
        root = Path(self.directory or os.getcwd()).resolve()
        doc = self._doc_param()
        if not doc:
            return None, None, "Missing 'doc' query parameter."
        doc_path = _safe_resolve(root, doc)
        if not doc_path:
            return None, None, "Invalid doc path."
        return doc_path, _state_path_for_doc(doc_path), None

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/state"):
            doc_path, state_path, err = self._resolve_doc()
            if err:
                return self._send_json(400, {"ok": False, "error": err})
            if not state_path.exists():
                return self._send_json(404, {"ok": False, "error": "No state file.", "state": None})
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return self._send_json(500, {"ok": False, "error": f"Bad state JSON: {exc}"})
            return self._send_json(200, {"ok": True, "state": state})
        if self.path.startswith("/api/answers"):
            doc_path, _state_path, err = self._resolve_doc()
            if err:
                return self._send_json(400, {"ok": False, "error": err})
            answers_path = doc_path.with_suffix(".answers.json")
            if not answers_path.exists():
                return self._send_json(404, {"ok": False, "error": "No answers file.", "answers": None})
            try:
                answers = json.loads(answers_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return self._send_json(500, {"ok": False, "error": f"Bad answers JSON: {exc}"})
            return self._send_json(200, {"ok": True, "answers": answers})
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/api/state"):
            doc_path, state_path, err = self._resolve_doc()
            if err:
                return self._send_json(400, {"ok": False, "error": err})
            try:
                body = self._read_body()
                state = json.loads(body.decode("utf-8") or "{}")
            except Exception as exc:
                return self._send_json(400, {"ok": False, "error": f"Invalid JSON: {exc}"})
            try:
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                return self._send_json(500, {"ok": False, "error": f"Write failed: {exc}"})
            return self._send_json(200, {"ok": True, "path": str(state_path)})
        return self._send_json(404, {"ok": False, "error": "Unknown endpoint"})

    def do_DELETE(self):  # noqa: N802
        if self.path.startswith("/api/state"):
            doc_path, state_path, err = self._resolve_doc()
            if err:
                return self._send_json(400, {"ok": False, "error": err})
            try:
                if state_path.exists():
                    state_path.unlink()
            except Exception as exc:
                return self._send_json(500, {"ok": False, "error": f"Delete failed: {exc}"})
            return self._send_json(200, {"ok": True})
        return self._send_json(404, {"ok": False, "error": "Unknown endpoint"})

    def log_message(self, fmt: str, *args):  # noqa: D401
        # Keep logs concise.
        return super().log_message(fmt, *args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve HTML and persist practice state to disk.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Root directory to serve.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.exists():
        print(f"[!] Root does not exist: {root}")
        return 2

    os.chdir(root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), lambda *a, **kw: Handler(*a, directory=str(root), **kw))
    print(f"[+] Serving {root}")
    print(f"[+] Open: http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
