#!/usr/bin/env python3
"""Extract IELTS answer keys from OCR-first PDFs using PaddleOCR.

This script is designed for PDFs whose answer pages are image-based and
therefore unreadable by the normal text pipeline. It submits the PDF to the
PaddleOCR document API, downloads the OCR JSONL result, gathers markdown text,
and tries to build a sidecar ``*.answers.json`` file that the existing HTML
pipeline can already consume.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Script.pdf_pipeline import _expand_number_string, parse_questions

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"
POLL_SECONDS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use PaddleOCR to extract answer keys into *.answers.json sidecars."
    )
    parser.add_argument("pdf", type=Path, help="Source PDF path.")
    parser.add_argument(
        "--token",
        default=os.environ.get("PADDLE_OCR_TOKEN"),
        help="PaddleOCR API token. Defaults to $PADDLE_OCR_TOKEN.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"PaddleOCR model name. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output answers JSON path. Default: <pdf>.answers.json",
    )
    parser.add_argument(
        "--dump-jsonl",
        type=Path,
        default=None,
        help="Optional path to save the raw OCR JSONL payload for debugging.",
    )
    parser.add_argument(
        "--max-polls",
        type=int,
        default=60,
        help="Maximum polling attempts before timing out. Default: 60",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print OCR snippets and parsing diagnostics.",
    )
    return parser.parse_args()


def normalize_answer(raw: str, qtype: str) -> str:
    text = raw.strip().upper()
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"\s+", " ", text)
    if qtype == "tfng":
        synonyms = {
            "T": "TRUE",
            "F": "FALSE",
            "NG": "NOT GIVEN",
            "N G": "NOT GIVEN",
        }
        return synonyms.get(text, text)
    if qtype == "ynng":
        synonyms = {
            "Y": "YES",
            "N": "NO",
            "NG": "NOT GIVEN",
            "N G": "NOT GIVEN",
        }
        return synonyms.get(text, text)
    synonyms = {
        "N G": "NOT GIVEN",
    }
    return synonyms.get(text, text)


def clean_markdown_text(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", "\t", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"[|]+", " ", text)
    text = re.sub(r"`+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_question_specs_from_pdf(pdf_path: Path) -> Tuple[List[int], Dict[int, str]]:
    from Script.pdf_pipeline import extract_pages_text, locate_blocks_with_indices

    pages, _ = extract_pages_text(pdf_path, max_pages=None)
    _, questions_text, _, _, _, _ = locate_blocks_with_indices(pages)
    questions = parse_questions(questions_text)
    numbers: List[int] = []
    qtypes: Dict[int, str] = {}
    for question in questions:
        for num in _expand_number_string(question.number):
            qnum = int(num)
            numbers.append(qnum)
            qtypes[qnum] = question.qtype
    return numbers, qtypes


def submit_job(pdf_path: Path, token: str, model: str) -> str:
    headers = {"Authorization": f"bearer {token}"}
    data = {
        "model": model,
        "optionalPayload": json.dumps(
            {
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": False,
            }
        ),
    }
    with pdf_path.open("rb") as handle:
        response = requests.post(
            JOB_URL,
            headers=headers,
            data=data,
            files={"file": handle},
            timeout=180,
        )
    response.raise_for_status()
    payload = response.json()
    return payload["data"]["jobId"]


def poll_until_done(job_id: str, token: str, max_polls: int) -> str:
    headers = {"Authorization": f"bearer {token}"}
    for _ in range(max_polls):
        response = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=60)
        response.raise_for_status()
        payload = response.json()["data"]
        state = payload.get("state")
        if state == "done":
            return payload["resultUrl"]["jsonUrl"]
        if state == "failed":
            raise RuntimeError(payload.get("errorMsg") or "PaddleOCR job failed.")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"PaddleOCR job {job_id} did not finish in time.")


def download_jsonl(json_url: str, dump_path: Optional[Path]) -> str:
    response = requests.get(json_url, timeout=180)
    response.raise_for_status()
    text = response.text
    if dump_path:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(text, encoding="utf-8")
    return text


def iter_markdown_blocks(jsonl_text: str) -> Iterable[Tuple[int, str]]:
    page_num = 0
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        result = obj.get("result", {})
        for layout in result.get("layoutParsingResults", []):
            markdown = layout.get("markdown", {}).get("text", "")
            yield page_num, clean_markdown_text(markdown)
            page_num += 1


def parse_answers_from_text(text: str, expected_numbers: List[int], qtypes: Dict[int, str]) -> Dict[str, str]:
    answers: Dict[str, str] = {}
    normalized = text.upper()

    # Common OCR noise: "10° FALSE" or "10. FALSE"
    pattern = re.compile(
        r"(?<!\d)(\d{1,3})\s*[\.\-:°]?\s*(TRUE|FALSE|NOT GIVEN|YES|NO|[A-Z]{1,4}|[IVX]{1,6})(?![A-Z])"
    )
    for match in pattern.finditer(normalized):
        qnum = int(match.group(1))
        if qnum not in expected_numbers:
            continue
        ans = normalize_answer(match.group(2), qtypes.get(qnum, "text"))
        if ans:
            answers[f"q{qnum}"] = ans

    # Table rows such as "1 I ..." are often the cleanest OCR units.
    for line in normalized.splitlines():
        line = line.strip()
        m = re.match(
            r"^(\d{1,3})\s+((?:NOT GIVEN)|(?:TRUE)|(?:FALSE)|(?:YES)|(?:NO)|(?:[A-Z]))(?:\s|$)",
            line,
        )
        if not m:
            continue
        qnum = int(m.group(1))
        if qnum not in expected_numbers:
            continue
        answers[f"q{qnum}"] = normalize_answer(m.group(2), qtypes.get(qnum, "text"))

    return answers


def choose_best_answer_block(
    blocks: List[Tuple[int, str]], expected_numbers: List[int], qtypes: Dict[int, str]
) -> Tuple[str, Dict[str, str]]:
    best_text = ""
    best_answers: Dict[str, str] = {}
    for _page_num, text in blocks:
        parsed = parse_answers_from_text(text, expected_numbers, qtypes)
        if len(parsed) > len(best_answers):
            best_text = text
            best_answers = parsed
    if not best_answers:
        combined = "\n\n".join(text for _page_num, text in blocks)
        best_text = combined
        best_answers = parse_answers_from_text(combined, expected_numbers, qtypes)
    return best_text, best_answers


def main() -> int:
    args = parse_args()
    if not args.token:
        print("Missing PaddleOCR token. Pass --token or set PADDLE_OCR_TOKEN.", file=sys.stderr)
        return 2
    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    expected_numbers, qtypes = extract_question_specs_from_pdf(args.pdf)
    if not expected_numbers:
        print("No question numbers detected from the PDF; aborting.", file=sys.stderr)
        return 2

    job_id = submit_job(args.pdf, args.token, args.model)
    json_url = poll_until_done(job_id, args.token, args.max_polls)
    jsonl_text = download_jsonl(json_url, args.dump_jsonl)
    blocks = list(iter_markdown_blocks(jsonl_text))
    best_text, answers = choose_best_answer_block(blocks, expected_numbers, qtypes)

    missing = [f"q{num}" for num in expected_numbers if f"q{num}" not in answers]
    output_path = args.output or args.pdf.with_suffix(".answers.json")

    if args.verbose:
        print(f"job_id={job_id}")
        print(f"json_url={json_url}")
        print("expected_numbers=", expected_numbers)
        print("best_text_preview=")
        print(best_text[:3000])
        print("parsed_answers=", json.dumps(answers, ensure_ascii=False, indent=2))
        print("missing=", missing)

    if not answers:
        print("OCR completed but no answers were parsed.", file=sys.stderr)
        return 1

    output_path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(answers)} answers to {output_path}")
    if missing:
        print("Missing:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
