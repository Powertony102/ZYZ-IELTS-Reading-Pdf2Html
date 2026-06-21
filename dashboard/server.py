#!/usr/bin/env python3
"""
Dashboard server for IELTS Reading practice navigation.
Uses only Python stdlib (http.server + sqlite3). No pip dependencies.

Endpoints:
  GET  /api/tests              — list all discovered test HTML files
  GET  /api/records            — list all practice records from SQLite
  POST /api/records            — upsert a practice record (JSON body)
  POST /api/records/reset/{id} — reset a record to not_started
  GET  /api/stats              — aggregate statistics

All other paths are served as static files from --root.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import urllib.parse
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# IELTS Reading band score conversion (40 questions total)
# ---------------------------------------------------------------------------
BAND_TABLE: List[Tuple[int, int, float]] = [
    (39, 40, 9.0),
    (37, 38, 8.5),
    (35, 36, 8.0),
    (33, 34, 7.5),
    (30, 32, 7.0),
    (27, 29, 6.5),
    (23, 26, 6.0),
    (20, 22, 5.5),
    (16, 19, 5.0),
    (13, 15, 4.5),
    (10, 12, 4.0),
    (6, 9, 3.5),
    (4, 5, 3.0),
    (3, 3, 2.5),
    (2, 2, 2.0),
    (1, 1, 1.0),
    (0, 0, 0.0),
]


def raw_to_band(raw: int, total: int = 40) -> float:
    """Convert raw IELTS reading score to band score."""
    if total != 40:
        # Scale to 40-question equivalent
        raw = round(raw * 40 / total) if total > 0 else 0
    for lo, hi, band in BAND_TABLE:
        if lo <= raw <= hi:
            return band
    return 0.0


def band_display(band: float) -> str:
    """Format band score for display."""
    if band == int(band):
        return f"{int(band)}.0"
    return str(band)


# ---------------------------------------------------------------------------
# Test discovery
# ---------------------------------------------------------------------------
# Pattern: "102. P1 - Katherine Mansfield 新西兰作家【高】"
_NAME_RE = re.compile(
    r"^(\d+)\.\s+P([123])\s*-\s*(.+?)\s*【(高|次)】\s*$"
)
# Background articles pattern
_BG_RE = re.compile(
    r"^(\d+)\.\s+P([123])\s*-\s*(.+)$"
)


def _make_id(stem: str) -> str:
    """Create a stable ID from filename stem."""
    # Remove special chars, keep alphanumeric and hyphens
    s = stem.strip()
    s = re.sub(r"[^\w\s\-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s[:120]


def discover_tests(root: Path) -> List[Dict[str, Any]]:
    """Scan server_build/html/ for test HTML files."""
    html_root = root / "server_build" / "html"
    if not html_root.exists():
        return []

    tests: List[Dict[str, Any]] = []
    category_map = {
        "P1（32高+13次）": "P1",
        "P2（33高+11次）": "P2",
        "P3（41高+9次）": "P3",
    }

    for cat_dir_name, passage_type in category_map.items():
        cat_dir = html_root / cat_dir_name
        if not cat_dir.is_dir():
            continue
        # Sub-directories: "1. 高频", "2. 次高频"
        for sub in sorted(cat_dir.iterdir()):
            if not sub.is_dir():
                continue
            if "高频" in sub.name and "次" in sub.name:
                frequency = "次高频"
            elif "高频" in sub.name:
                frequency = "高频"
            else:
                frequency = ""

            for test_dir in sorted(sub.iterdir()):
                if not test_dir.is_dir():
                    continue
                # Find the HTML file inside
                html_files = list(test_dir.glob("*.html"))
                if not html_files:
                    continue
                html_file = html_files[0]
                stem = html_file.stem  # e.g. "102. P1 - Katherine Mansfield 新西兰作家【高】"

                # Parse the name
                m = _NAME_RE.match(stem)
                if m:
                    num, ptype, title, freq_mark = m.groups()
                else:
                    m2 = _BG_RE.match(stem)
                    if m2:
                        num, ptype, title = m2.groups()
                        freq_mark = ""
                    else:
                        num = ""
                        ptype = passage_type
                        title = stem
                        freq_mark = ""

                # Relative path from root for URL linking
                rel_path = html_file.relative_to(root).as_posix()

                tests.append({
                    "id": _make_id(stem),
                    "number": int(num) if num.isdigit() else 0,
                    "passage_type": f"P{ptype}" if ptype else passage_type,
                    "frequency": frequency,
                    "freq_mark": freq_mark,
                    "title": title.strip(),
                    "full_title": stem,
                    "path": rel_path,
                })

    # Also scan 高频背景（非原文）
    bg_dir = html_root / "高频背景（非原文）"
    if bg_dir.is_dir():
        for sub in sorted(bg_dir.iterdir()):
            if not sub.is_dir():
                continue
            # Determine passage type from subfolder name like "P1（5篇）"
            ptype_match = re.search(r"P([123])", sub.name)
            ptype = f"P{ptype_match.group(1)}" if ptype_match else "P?"
            for test_dir in sorted(sub.iterdir()):
                if not test_dir.is_dir():
                    continue
                html_files = list(test_dir.glob("*.html"))
                if not html_files:
                    continue
                html_file = html_files[0]
                stem = html_file.stem
                rel_path = html_file.relative_to(root).as_posix()
                tests.append({
                    "id": _make_id(stem),
                    "number": 0,
                    "passage_type": ptype,
                    "frequency": "背景",
                    "freq_mark": "",
                    "title": stem,
                    "full_title": stem,
                    "path": rel_path,
                })

    return tests


# ---------------------------------------------------------------------------
# SQLite database
# ---------------------------------------------------------------------------
DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_records (
    id             TEXT PRIMARY KEY,
    test_path      TEXT NOT NULL,
    passage_type   TEXT NOT NULL DEFAULT '',
    frequency      TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'not_started',
    time_seconds   INTEGER NOT NULL DEFAULT 0,
    raw_score      INTEGER,
    total_questions INTEGER,
    band_score     REAL,
    updated_at     TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(DB_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def get_all_records(self) -> List[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM practice_records ORDER BY passage_type, title"
        )
        return [dict(row) for row in cur.fetchall()]

    def get_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM practice_records WHERE id = ?", (record_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def upsert_record(self, data: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        record_id = data["id"]

        # Compute band score if raw_score and total_questions provided
        band = None
        if data.get("raw_score") is not None and data.get("total_questions"):
            band = raw_to_band(data["raw_score"], data["total_questions"])

        existing = self.get_record(record_id)
        if existing:
            # Update
            self.conn.execute(
                """UPDATE practice_records SET
                    status = COALESCE(?, status),
                    time_seconds = COALESCE(?, time_seconds),
                    raw_score = COALESCE(?, raw_score),
                    total_questions = COALESCE(?, total_questions),
                    band_score = COALESCE(?, band_score),
                    updated_at = ?
                WHERE id = ?""",
                (
                    data.get("status"),
                    data.get("time_seconds"),
                    data.get("raw_score"),
                    data.get("total_questions"),
                    band,
                    now,
                    record_id,
                ),
            )
        else:
            # Insert
            self.conn.execute(
                """INSERT INTO practice_records
                    (id, test_path, passage_type, frequency, title,
                     status, time_seconds, raw_score, total_questions, band_score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    data.get("test_path", ""),
                    data.get("passage_type", ""),
                    data.get("frequency", ""),
                    data.get("title", ""),
                    data.get("status", "not_started"),
                    data.get("time_seconds", 0),
                    data.get("raw_score"),
                    data.get("total_questions"),
                    band,
                    now,
                ),
            )
        self.conn.commit()
        return self.get_record(record_id)

    def reset_record(self, record_id: str) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            """UPDATE practice_records SET
                status = 'not_started', time_seconds = 0,
                raw_score = NULL, total_questions = NULL,
                band_score = NULL, updated_at = ?
            WHERE id = ?""",
            (now, record_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_stats(self) -> Dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) FROM practice_records").fetchone()[0]
        completed = self.conn.execute(
            "SELECT COUNT(*) FROM practice_records WHERE status = 'completed'"
        ).fetchone()[0]
        in_progress = self.conn.execute(
            "SELECT COUNT(*) FROM practice_records WHERE status = 'in_progress'"
        ).fetchone()[0]
        avg_band_row = self.conn.execute(
            "SELECT AVG(band_score) FROM practice_records WHERE band_score IS NOT NULL"
        ).fetchone()[0]
        avg_time_row = self.conn.execute(
            "SELECT AVG(time_seconds) FROM practice_records WHERE status = 'completed' AND time_seconds > 0"
        ).fetchone()[0]

        return {
            "total_records": total,
            "completed": completed,
            "in_progress": in_progress,
            "not_started": total - completed - in_progress,
            "avg_band": round(avg_band_row, 1) if avg_band_row else None,
            "avg_time_seconds": int(avg_time_row) if avg_time_row else 0,
        }


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves static files and handles API endpoints."""

    server_version = "IELTSDashboard/1.0"

    def __init__(self, *args, db: Database = None, root: str = None, **kwargs):
        self.db = db
        super().__init__(*args, directory=root, **kwargs)

    def _send_json(self, code: int, payload: Any):
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _parse_path(self) -> Tuple[str, Dict[str, str]]:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query or "")
        params = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}
        return parsed.path, params

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path, params = self._parse_path()

        if path == "/api/tests":
            tests = discover_tests(Path(self.directory or os.getcwd()))
            return self._send_json(200, tests)

        if path == "/api/records":
            records = self.db.get_all_records()
            return self._send_json(200, records)

        if path == "/api/stats":
            stats = self.db.get_stats()
            return self._send_json(200, stats)

        # Serve static files
        return super().do_GET()

    def do_POST(self):
        path, params = self._parse_path()

        if path == "/api/records":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
            except Exception as e:
                return self._send_json(400, {"error": f"Invalid JSON: {e}"})
            if not body.get("id"):
                return self._send_json(400, {"error": "Missing 'id' field"})
            record = self.db.upsert_record(body)
            return self._send_json(200, record)

        # Reset endpoint: POST /api/records/reset/{id}
        if path.startswith("/api/records/reset/"):
            record_id = path.split("/api/records/reset/", 1)[1]
            if not record_id:
                return self._send_json(400, {"error": "Missing record ID"})
            success = self.db.reset_record(urllib.parse.unquote(record_id))
            if success:
                return self._send_json(200, {"ok": True})
            return self._send_json(404, {"error": "Record not found"})

        return self._send_json(404, {"error": "Unknown endpoint"})

    def do_DELETE(self):
        path, params = self._parse_path()

        if path.startswith("/api/records/"):
            record_id = path.split("/api/records/", 1)[1]
            if not record_id:
                return self._send_json(400, {"error": "Missing record ID"})
            success = self.db.reset_record(urllib.parse.unquote(record_id))
            if success:
                return self._send_json(200, {"ok": True})
            return self._send_json(404, {"error": "Record not found"})

        return self._send_json(404, {"error": "Unknown endpoint"})

    def log_message(self, fmt, *args):
        # Concise logging
        return super().log_message(fmt, *args)


def make_handler(db: Database, root: str):
    """Create a handler class with the database instance bound."""
    def handler(*args, **kwargs):
        return DashboardHandler(*args, db=db, root=root, **kwargs)
    return handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="IELTS Reading Dashboard — serves navigation UI and practice records API."
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(),
        help="Root directory to serve (project root)."
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="SQLite database path. Defaults to {root}/dashboard/ielts_dashboard.db"
    )
    parser.add_argument(
        "--port", type=int, default=7777,
        help="Port to bind (default: 7777)."
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.exists():
        print(f"[!] Root does not exist: {root}")
        return 2

    db_path = args.db or (root / "dashboard" / "ielts_dashboard.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = Database(db_path)
    print(f"[+] Database: {db_path}")

    # Pre-scan tests
    tests = discover_tests(root)
    print(f"[+] Discovered {len(tests)} test files")

    handler = make_handler(db, str(root))
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"[+] Serving {root}")
    print(f"[+] Dashboard: http://127.0.0.1:{args.port}/dashboard/index.html")
    print(f"[+] Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
