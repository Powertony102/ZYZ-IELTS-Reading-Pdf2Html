#!/usr/bin/env python3
"""
PDF -> HTML converter using OpenAI multimodal models (ChatGPT key).
"""

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent / "Script"))

try:
    import pdfplumber
    from PIL import Image
except ImportError:
    print("Error: Required libraries missing. Please install pdfplumber and Pillow.")
    sys.exit(1)

from html_utils import (
    build_passage_html,
    build_question_html,
    build_question_nav,
    expand_question_numbers,
    render_html,
    storage_key_from_title,
)

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_system_prompt() -> str:
    return """
You are an expert AI assistant specializing in analyzing educational materials. Your task is to process content from an IELTS reading test PDF and convert it into a structured JSON format.

You will be provided with images of each page of the PDF.
You must follow these rules:
1. Analyze all text and images in the PDF. Look for the passage text, question blocks, and the answer key.
2. Output MUST be a single, valid JSON object. Do not include markdown fences.
3. JSON schema:
{
  "title": "The title of the reading passage",
  "passage": [
    { "id": "A", "text": "Paragraph text" }
  ],
  "questions": [
    {
      "number": "27",
      "qtype": "mcq",
      "instruction": "Group instruction text",
      "stem": "Question text (for summary use numbered placeholders like (31))",
      "options": [ { "label": "A", "text": "Option text" } ]
    }
  ],
  "answers": { "q27": "A", "q31": "H" }
}
Question types:
- mcq: options provided.
- tfng/ynng: include the correct instruction.
- summary/gap: if a word bank exists, list in options and include numbered placeholders in stem.
""".strip()


def _openai_request(payload: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    import urllib.request
    import urllib.error

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI API error: {e.code} {e.reason}\n{detail}") from e


def call_openai_api(pdf_path: Path, model: str, max_pages: Optional[int]) -> Dict[str, Any]:
    _load_dotenv(Path(__file__).parent / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set.")

    contents: List[Dict[str, Any]] = []
    contents.append({"type": "text", "text": "Read the following pages and return the JSON object."})

    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages[: max_pages or len(pdf.pages)]
        for i, page in enumerate(pages):
            pix = page.to_image(resolution=150).original
            img_byte_arr = io.BytesIO()
            pix.save(img_byte_arr, format="PNG")
            img_data = img_byte_arr.getvalue()
            b64 = base64.b64encode(img_data).decode("ascii")
            contents.append({"type": "text", "text": f"--- Page {i + 1} ---"})
            contents.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": contents},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    response = _openai_request(payload, api_key)
    text = response["choices"][0]["message"]["content"].strip()
    if text.startswith("```json"):
        text = text[7:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"DEBUG: Raw response from OpenAI:\n{text}")
        raise ValueError(f"Failed to parse JSON from OpenAI response: {e}") from e


def process_pdf(pdf_path: Path, args: argparse.Namespace) -> None:
    print(f"[+] Processing {pdf_path.name}...")
    try:
        data = call_openai_api(pdf_path, args.model, args.max_pages)
    except Exception as e:
        print(f"[!] ERROR: {e}")
        return

    try:
        template = Path(args.template).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"[!] ERROR: Template not found at {args.template}")
        return

    title = data.get("title", pdf_path.stem)
    questions = data.get("questions", [])

    meta_rows: List[Dict[str, str]] = []
    for q in questions:
        q_num = q.get("number", "")
        q_type = q.get("qtype", "text")
        import re
        match = re.match(r"^\s*(\d+)\s*[–-]\s*(\d+)\s*$", q_num)
        if match:
            for n in range(int(match.group(1)), int(match.group(2)) + 1):
                meta_rows.append({"id": f"q{n}", "number": str(n), "type": q_type})
        else:
            meta_rows.append({"id": f"q{q_num}", "number": q_num, "type": q_type})

    context = {
        "TITLE": title,
        "PASSAGE_HTML": build_passage_html(data.get("passage", [])),
        "QUESTION_NAV": build_question_nav(questions),
        "QUESTION_HTML": build_question_html(questions),
        "ANSWERS_JSON": json.dumps(data.get("answers", {}), indent=2, ensure_ascii=False),
        "QUESTION_META_JSON": json.dumps(meta_rows, ensure_ascii=False),
        "STORAGE_KEY": storage_key_from_title(title),
    }

    final_html = render_html(template, context)
    output_dir = args.output_dir or pdf_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_html_path = output_dir / f"{pdf_path.stem}.html"
    output_html_path.write_text(final_html, encoding="utf-8")

    try:
        rel_path = output_html_path.resolve().relative_to(Path.cwd().resolve())
        print(f"    -> Successfully created {rel_path}")
    except ValueError:
        print(f"    -> Successfully created {output_html_path}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PDF -> Interactive HTML via OpenAI")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--template", type=Path, default=Path(__file__).parent / "Script" / "templates" / "base.html")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--model", type=str, default="gpt-4o-mini")
    args = parser.parse_args(argv)

    if args.input.is_file():
        pdfs = [args.input]
    else:
        pdfs = sorted(args.input.rglob("*.pdf"))

    if args.limit:
        pdfs = pdfs[: args.limit]

    for pdf_path in pdfs:
        process_pdf(pdf_path, args)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
