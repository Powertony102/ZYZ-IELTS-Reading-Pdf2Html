#!/usr/bin/env python3
"""
PDF -> HTML converter using a Multimodal LLM (Gemini) for robust parsing.
"""

import argparse
import base64
import json
import os
import sys
import io
from pathlib import Path

# Add Script directory to path to import html_utils
sys.path.insert(0, str(Path(__file__).parent / "Script"))

try:
    import pdfplumber
    from PIL import Image
    import google.generativeai as genai
except ImportError:
    print("Error: Required libraries missing. Please install pdfplumber, google-generativeai, and Pillow.")
    sys.exit(1)

# Import our refactored HTML generation functions
from html_utils import (
    build_passage_html,
    build_question_html,
    build_question_nav,
    expand_question_numbers,
    render_html,
    storage_key_from_title,
)

# --- LLM PROMPT AND API INTERACTION ---

def get_system_prompt() -> str:
    return """
You are an expert AI assistant specializing in analyzing educational materials. Your task is to process content from an IELTS reading test PDF and convert it into a structured JSON format.

You will be provided with images of each page of the PDF.
You must meticulously follow these rules:
1.  Analyze all text and images in the PDF. Look for the passage text, question blocks, and the answer key (which might be at the end, sometimes as an image or a table).
2.  Your output MUST be a single, valid JSON object. Do not include any text like \"```json\" before or after the JSON object. Just the raw JSON.
3.  The JSON object must adhere to the following schema:

{
  "title": "The title of the reading passage",
  "passage": [
    {
      "id": "A", // Use the paragraph label (A, B, C...) if present; otherwise ""
      "text": "The full text of the paragraph."
    }
  ],
  "questions": [
    {
      "number": "27", // Single number or range like "31-35"
      "qtype": "mcq", // "mcq", "tfng", "ynng", "summary", "gap", "text"
      "instruction": "The instruction for this group (e.g., 'Choose the correct letter, A, B, C or D.')",
      "stem": "The question text. For summaries, include the full text with (31) style placeholders.",
      "options": [
        { "label": "A", "text": "Option text" }
      ] // Only for MCQ or Summary with word bank
    }
  ],
  "answers": {
    "q27": "A",
    "q31": "H"
    // Extract these from the 'Answer Key' section in the document.
  }
}

Important for Question Types:
- MCQ: Populated options.
- TFNG/YNNG: Instruction must clearly state if it's TRUE/FALSE or YES/NO.
- Summary/Gap: If there's a list of words (A-J), put them in 'options'. Use (number) as placeholders in 'stem'.
"""

def call_gemini_api(pdf_path: Path) -> dict:
    """
    Calls Gemini API with PDF pages as images.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable not set.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3-pro')

    print(f"    -> Extracting pages from PDF...")
    contents = [get_system_prompt()]
    
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            # Convert page to image for multimodal analysis
            # This is better than text extraction for complex layouts and image-based answers
            pix = page.to_image(resolution=150).original
            img_byte_arr = io.BytesIO()
            pix.save(img_byte_arr, format='PNG')
            img_data = img_byte_arr.getvalue()
            
            contents.append(f"--- Page {i+1} ---")
            contents.append({
                "mime_type": "image/png",
                "data": base64.b64encode(img_data).decode()
            })

    print(f"    -> Calling Gemini API (this may take a minute)...")
    response = model.generate_content(contents)
    
    # Clean the response text (sometimes models add markdown blocks)
    text = response.text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"DEBUG: Raw response from Gemini:\n{text}")
        raise ValueError(f"Failed to parse JSON from Gemini response: {e}")

def process_pdf(pdf_path: Path, args: argparse.Namespace):
    print(f"[+] Processing {pdf_path.name}...")

    try:
        data = call_gemini_api(pdf_path)
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
    question_numbers = expand_question_numbers(questions)
    
    # Prepare metadata for JS
    meta_rows = []
    for q in questions:
        q_num = q.get("number", "")
        q_type = q.get("qtype", "text")
        # Expand ranges for meta
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
        "STORAGE_KEY": storage_key_from_title(title)
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

def main(argv: list = None):
    parser = argparse.ArgumentParser(description="PDF -> Interactive HTML via Gemini")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--template", type=Path, default=Path(__file__).parent / "Script" / "templates" / "base.html")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    if args.input.is_file():
        pdfs = [args.input]
    else:
        pdfs = sorted(args.input.rglob("*.pdf"))

    if args.limit:
        pdfs = pdfs[:args.limit]
    
    for pdf_path in pdfs:
        process_pdf(pdf_path, args)
    print("\nDone.")

if __name__ == "__main__":
    sys.exit(main())