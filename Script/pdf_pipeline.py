#!/usr/bin/env python3
"""
Lightweight PDF → HTML pipeline for IELTS-style reading sets.

Workflow (matching the user requirements):
  Step A  Detect whether the PDF looks text-based vs scanned by sampling early pages.
  Step B  Locate Passage / Questions / Answers blocks via keyword + page-range heuristics.
  Step C  Break the passage into paragraph units (A/B/C... or synthetic P1/P2...).
  Step D  Parse questions with a basic type recogniser (MCQ, TF/NG, YN/NG, gap/text).
  Step E  Parse the answer key.
  Step F  Run consistency checks and flag NEEDS_FIX when parsing confidence is low.
  Step G  Render a single-file HTML using the shared template, next to the PDF (same-name folder).

This script is intentionally dependency-light. If pdfplumber/pypdf are missing the script
will emit a clear message instead of crashing. See README for usage.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Optional dependencies: pdfplumber > pypdf (for layout-friendly extraction)
try:
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pdfplumber = None

try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None


@dataclass
class Question:
    number: str
    stem: str
    qtype: str = "text"  # mcq | tfng | ynng | gap | text
    options: List[Tuple[str, str]] = field(default_factory=list)
    raw: str = ""

    @property
    def html_name(self) -> str:
        return f"q{self.number}"


@dataclass
class ParseOutcome:
    paragraphs: List[Dict[str, str]]
    questions: List[Question]
    answers: Dict[str, str]
    status: str
    notes: List[str]
    suspected_scan: bool
    char_count_sample: int
    ascii_ratio_sample: float
    total_pages: int


def extract_pages_text(pdf_path: Path, max_pages: Optional[int] = None) -> Tuple[List[str], int]:
    """Extract text for up to max_pages (None = all pages)."""
    if pdfplumber:
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages[: max_pages or len(pdf.pages)]
            texts = [(page.extract_text() or "") for page in pages]
            return texts, len(pdf.pages)
    if PdfReader:
        reader = PdfReader(str(pdf_path))
        texts = []
        for idx, page in enumerate(reader.pages):
            if max_pages is not None and idx >= max_pages:
                break
            texts.append(page.extract_text() or "")
        return texts, len(reader.pages)
    raise RuntimeError(
        "Missing PDF backend. Please install `pdfplumber` (preferred) or `pypdf` before running."
    )


def detect_scan(sample_texts: List[str]) -> Tuple[int, float, bool]:
    """Return (char_count, ascii_ratio, suspected_scan) using the early-page sample."""
    joined = "\n".join(sample_texts)
    char_count = len(joined)
    ascii_chars = sum(1 for c in joined if c.isascii() and not c.isspace())
    ascii_ratio = ascii_chars / char_count if char_count else 0.0
    suspected_scan = char_count < 300 or ascii_ratio < 0.55
    return char_count, ascii_ratio, suspected_scan


def locate_blocks(page_texts: List[str]) -> Tuple[str, str, str, List[str]]:
    """Locate Passage/Questions/Answers via keyword scanning (with fallback heuristics)."""
    question_markers = [
        r"\bQuestions?\s+\d",
        r"\bReading\s+Passage\s+\d",
        r"\bQuestions\b",
    ]
    answer_markers = [
        r"\bAnswer\s*Key\b",
        r"\bAnswer\s+Key\b",
        r"\bAnswers\b",
        r"\b答案\b",
        r"\b参考答案\b",
        r"\b答案解析\b",
    ]

    def find_first(patterns: List[str]) -> Optional[int]:
        for idx, text in enumerate(page_texts):
            for pat in patterns:
                if re.search(pat, text, flags=re.IGNORECASE):
                    return idx
        return None

    def question_line_count(text: str) -> int:
        pattern = re.compile(r"(?m)^\s*\d{1,3}\s+[A-Za-z].+")
        return len(pattern.findall(text))

    question_section_hints = [
        r"\bChoose the correct letter\b",
        r"\bDo the following statements\b",
        r"\bComplete the summary\b",
        r"\bComplete the table\b",
        r"\bWhich paragraph\b",
        r"\bMatch\b",
    ]

    def is_question_page(text: str) -> bool:
        if question_line_count(text) >= 3:
            return True
        return any(re.search(pat, text, flags=re.IGNORECASE) for pat in question_section_hints)

    q_start = None
    for idx, text in enumerate(page_texts):
        if any(re.search(pat, text, flags=re.IGNORECASE) for pat in question_markers) and is_question_page(text):
            q_start = idx
            break

    ans_start = find_first(answer_markers)

    def answer_line_count(text: str) -> int:
        pattern = re.compile(
            r"(?m)^\s*\d{1,3}\s*[:\.\-]?\s*(?:[A-Za-z]{1,6}|TRUE|FALSE|YES|NO|NG|NOT GIVEN|[ivxlcdm]+)\s*$"
        )
        return len(pattern.findall(text))

    notes = []
    if ans_start is not None and page_texts:
        if answer_line_count(page_texts[ans_start]) < 2:
            notes.append("Answer marker looked false-positive; ignored.")
            ans_start = None
    if ans_start is None and page_texts:
        for idx in range(len(page_texts) - 1, -1, -1):
            if answer_line_count(page_texts[idx]) >= 4:
                ans_start = idx
                notes.append("Answer Key marker not found; guessed answer section from patterns.")
                break

    if q_start is None and page_texts:
        for idx, text in enumerate(page_texts):
            if question_line_count(text) >= 3:
                q_start = idx
                notes.append("Questions marker not found; guessed question section from patterns.")
                break

    passage_end = q_start if q_start is not None else (ans_start if ans_start is not None else len(page_texts))
    questions_end = ans_start if ans_start is not None else len(page_texts)

    passage_text = "\n".join(page_texts[:passage_end]).strip()
    questions_text = "\n".join(page_texts[passage_end:questions_end]).strip()
    answers_text = "\n".join(page_texts[questions_end:]).strip()
    if q_start is None:
        notes.append("Questions marker not found; treated as contiguous after passage.")
    if ans_start is None:
        notes.append("Answer Key marker not found; answers parsed from tail of document.")

    return passage_text, questions_text, answers_text, notes


def parse_paragraphs(passage_text: str) -> List[Dict[str, str]]:
    """Split passage into paragraph units (A/B/C... or synthetic P1...)."""
    cleaned = passage_text.replace("\r", "\n")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", cleaned) if b.strip()]
    paragraphs = []
    label_pat = re.compile(r"^(?P<label>[A-Z])[\.\)]\s+")

    current_id = None
    for block in blocks:
        label_match = label_pat.match(block)
        if label_match:
            current_id = label_match.group("label")
            body = label_pat.sub("", block, count=1).strip()
        else:
            current_id = f"P{len(paragraphs) + 1}"
            body = block
        paragraphs.append({"id": current_id, "text": body})
    return paragraphs


def _option_lines(lines: List[str]) -> List[Tuple[str, str]]:
    def line_match(line: str) -> Optional[Tuple[str, str]]:
        match = re.match(r"^\s*\(?([A-L]|[ivxlcdm]+)\)?[\.\)]?\s+(.*)", line, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip(), match.group(2).strip()

    def split_inline(line: str) -> List[Tuple[str, str]]:
        pat = re.compile(r"(?:(?<=^)|(?<=\s))\(?([A-L]|[ivxlcdm]+)\)?[\.\)]?\s+", flags=re.IGNORECASE)
        matches = list(pat.finditer(line))
        if len(matches) < 2:
            return []
        items: List[Tuple[str, str]] = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
            label = match.group(1).strip()
            text = line[start:end].strip(" .;:-")
            if text:
                items.append((label, text))
        return items

    opts: List[Tuple[str, str]] = []
    seen = set()
    for ln in lines:
        inline_opts = split_inline(ln)
        if inline_opts:
            for label, text in inline_opts:
                key = label.upper()
                if key not in seen:
                    opts.append((label, text))
                    seen.add(key)
            continue
        matched = line_match(ln)
        if matched:
            label, text = matched
            key = label.upper()
            if key not in seen:
                opts.append((label, text))
                seen.add(key)
    return opts


def _strip_option_lines(lines: List[str]) -> List[str]:
    stripped = []
    for ln in lines:
        inline_opts = _option_lines([ln])
        if inline_opts and len(inline_opts) >= 2:
            continue
        if re.match(r"^\s*\(?([A-L]|[ivxlcdm]+)\)?[\.\)]?\s+.+", ln, flags=re.IGNORECASE):
            continue
        stripped.append(ln)
    return stripped


def _parse_numbered_questions(text: str, default_qtype: Optional[str] = None) -> List[Question]:
    markers = list(re.finditer(r"(?m)^\s*(\d{1,3})[\.\)]?\s+", text))
    spans = []
    for idx, match in enumerate(markers):
        start = match.start()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(text)
        spans.append((match.group(1), text[start:end]))

    questions: List[Question] = []
    for num, block in spans:
        cleaned = re.sub(r"(?m)^\s*\d{1,3}[\.\)]?\s*", "", block, count=1).strip()
        lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
        options = _option_lines(lines)
        if len(options) < 2:
            options = []
        stem_lines = _strip_option_lines(lines) if options else lines

        qtype = "text"
        # Check for gap-fill patterns first
        if re.search(r"_{3,}|\(\d+\)|\[\d+\]", block):
            qtype = "gap"
        # Check for TFNG/YNNG only if no MCQ-style options (A/B/C/D)
        elif not options:
            if re.search(r"\b(true|false|not given)\b", block, flags=re.IGNORECASE):
                qtype = "tfng"
            elif re.search(r"\b(yes|no|not given)\b", block, flags=re.IGNORECASE):
                qtype = "ynng"
            elif default_qtype in {"tfng", "ynng"}:
                qtype = default_qtype
        # If we have MCQ-style options, it's definitely MCQ
        elif options:
            qtype = "mcq"

        questions.append(
            Question(
                number=num,
                stem=" ".join(stem_lines).strip(),
                qtype=qtype,
                options=options,
                raw=block.strip(),
            )
        )
    return questions


def _extract_range(text: str) -> Optional[Tuple[int, int]]:
    match = re.search(r"Questions?\s+(\d{1,3})\s*[–-]\s*(\d{1,3})", text, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _parse_summary_section(section: str) -> Optional[Question]:
    range_info = _extract_range(section)
    if not range_info:
        return None
    start, end = range_info
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    options = _option_lines(lines)
    stem_lines = _strip_option_lines(lines) if options else lines
    stem = " ".join(stem_lines).strip()
    return Question(
        number=f"{start}-{end}",
        stem=stem,
        qtype="summary",
        options=options,
        raw=section.strip(),
    )


def _strip_tail_notice(text: str) -> str:
    markers = [
        r"Disclaimer",
        r"Compiled, formatted",
        r"All copyright",
        r"No affiliation",
        r"Available free of charge",
        r"Resale or any paid distribution",
    ]
    cutoff = None
    for marker in markers:
        match = re.search(marker, text, flags=re.IGNORECASE)
        if match:
            cutoff = match.start() if cutoff is None else min(cutoff, match.start())
    return text[:cutoff].strip() if cutoff is not None else text


def _detect_section_qtype(text: str) -> Optional[str]:
    lowered = text.lower()
    if "true" in lowered and "false" in lowered and "not given" in lowered:
        return "tfng"
    if "yes" in lowered and "no" in lowered and "not given" in lowered:
        return "ynng"
    return None


def parse_questions(question_text: str) -> List[Question]:
    """Parse questions into a structured list with light type detection."""
    question_text = _strip_tail_notice(question_text)
    if not question_text.strip():
        return []

    summary_hints = (
        "complete the summary",
        "complete the notes",
        "complete the table",
        "complete the flow-chart",
        "complete the flow chart",
        "complete the diagram",
        "complete the sentences",
    )
    headings = list(re.finditer(r"(?im)^\s*Questions?\s+\d{1,3}\s*[–-]\s*\d{1,3}", question_text))
    if not headings:
        return _parse_numbered_questions(question_text)

    questions: List[Question] = []
    if headings[0].start() > 0:
        questions.extend(_parse_numbered_questions(question_text[: headings[0].start()]))

    for idx, match in enumerate(headings):
        start = match.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(question_text)
        section = question_text[start:end]
        section_lower = section.lower()
        if any(hint in section_lower for hint in summary_hints):
            summary_q = _parse_summary_section(section)
            if summary_q:
                questions.append(summary_q)
            else:
                questions.extend(_parse_numbered_questions(section))
        else:
            default_qtype = _detect_section_qtype(section)
            questions.extend(_parse_numbered_questions(section, default_qtype=default_qtype))
    return questions


def normalize_answer(ans: str) -> str:
    lookup = {
        "t": "TRUE",
        "f": "FALSE",
        "ng": "NOT GIVEN",
        "n": "NO",
        "y": "YES",
    }
    cleaned = ans.strip()
    key = cleaned.lower()
    if key in lookup:
        return lookup[key]
    return cleaned.upper()


def parse_answers(answer_text: str) -> Dict[str, str]:
    """Parse answers of the form '1 A' or '14 ii'."""
    answers: Dict[str, str] = {}
    if not answer_text.strip():
        return answers
    pattern = re.compile(
        r"(?mi)^\s*(\d{1,3})\s*[:\.\-]?\s*([A-Za-z]{1,6}|TRUE|FALSE|YES|NO|NG|NOT GIVEN|[ivxlcdm]+)\b"
    )
    for match in pattern.finditer(answer_text):
        qnum, raw_ans = match.group(1), match.group(2)
        answers[f"q{qnum}"] = normalize_answer(raw_ans)
    if len(answers) < 3:
        inline_pattern = re.compile(
            r"(?i)(?<!\d)(\d{1,3})\s*[:\.\-]?\s*(TRUE|FALSE|YES|NO|NG|NOT GIVEN|[A-Za-z]{1,6}|[ivxlcdm]+)\b"
        )
        for match in inline_pattern.finditer(answer_text):
            qnum, raw_ans = match.group(1), match.group(2)
            key = f"q{qnum}"
            if key not in answers:
                answers[key] = normalize_answer(raw_ans)
    return answers


def _expand_number_string(number: str) -> List[str]:
    match = re.match(r"^\s*(\d{1,3})\s*[–-]\s*(\d{1,3})\s*$", number)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        return [str(n) for n in range(start, end + 1)]
    return [number.strip()]


def expand_question_numbers(questions: List[Question]) -> List[str]:
    numbers: List[str] = []
    seen = set()
    for q in questions:
        for num in _expand_number_string(q.number):
            if num and num not in seen:
                numbers.append(num)
                seen.add(num)
    return numbers


def validate(questions: List[Question], answers: Dict[str, str], suspected_scan: bool) -> Tuple[str, List[str]]:
    notes: List[str] = []
    if not questions:
        notes.append("No questions parsed.")
    if not answers:
        notes.append("No answers parsed.")
    q_numbers = expand_question_numbers(questions)
    missing_answers = [f"q{num}" for num in q_numbers if f"q{num}" not in answers]
    if missing_answers:
        notes.append(f"Missing answers for: {', '.join(missing_answers[:6])}")
    if suspected_scan:
        notes.append("Suspected scan/PDF with weak text extraction.")
    if answers and q_numbers and abs(len(answers) - len(q_numbers)) > 3:
        notes.append(f"Answer count {len(answers)} vs question count {len(q_numbers)} mismatch.")
    status = "READY" if not notes else "NEEDS_FIX"
    return status, notes


def build_passage_html(paragraphs: List[Dict[str, str]]) -> str:
    parts = []
    for para in paragraphs:
        pid = html.escape(str(para.get("id", "")))
        text = html.escape(para.get("text", ""))
        label = f'<span class="paragraph-label">{pid}</span> ' if pid else ""
        parts.append(f'<p id="para-{pid}">{label}{text}</p>')
    return "\n".join(parts)


def build_question_nav(questions: List[Question]) -> str:
    numbers = expand_question_numbers(questions)
    return "\n".join(
        f'<button class="q-nav" data-target="q{num}">{num}</button>' for num in numbers
    )


def build_question_html(questions: List[Question]) -> str:
    items = []
    for q in questions:
        stem_html = html.escape(q.stem or "").replace("\n", "<br>")
        controls = ""
        if q.qtype == "summary":
            range_numbers = _expand_number_string(q.number)
            if range_numbers:
                anchors = "".join(f'<span class="q-anchor" id="q{num}"></span>' for num in range_numbers)
            else:
                anchors = ""
            placeholder_map = {}
            stem_src = q.stem or ""
            for num in range_numbers:
                placeholder = f"__BLANK_{num}__"
                placeholder_map[num] = placeholder
                replaced = False
                patterns = [
                    re.compile(rf"\b{num}\b\s*_{2,}"),
                    re.compile(r"[\(\[]\s*" + re.escape(str(num)) + r"\s*[\)\]]"),
                ]
                for pattern in patterns:
                    if pattern.search(stem_src):
                        stem_src = pattern.sub(placeholder, stem_src, count=1)
                        replaced = True
                        break
                if not replaced:
                    stem_src += f" {placeholder}"
            stem_safe = html.escape(stem_src)
            options_html = ""
            if q.options:
                choices = "".join(
                    f'<option value="{html.escape(label.upper())}">{html.escape(label.upper())}</option>'
                    for label, _ in q.options
                )
                for num in range_numbers:
                    select_html = (
                        f'<select class="select-input" name="q{num}">'
                        f'<option value="">--</option>{choices}</select>'
                    )
                    stem_safe = stem_safe.replace(placeholder_map[num], f"{num} {select_html}")
                option_pills = "".join(
                    f'<span class="option-pill"><strong>{html.escape(label.upper())}</strong> {html.escape(text)}</span>'
                    for label, text in q.options
                )
                options_html = f'<div class="option-grid">{option_pills}</div>'
            else:
                for num in range_numbers:
                    input_html = f'<input type="text" name="q{num}" class="text-input inline-input" />'
                    stem_safe = stem_safe.replace(placeholder_map[num], f"{num} {input_html}")
            items.append(
                f"""<article class="question" id="q{range_numbers[0] if range_numbers else q.number}">
  {anchors}
  <div class="q-header"><span class="q-number">{q.number}</span><div class="q-meta">SUMMARY</div></div>
  <div class="q-stem">{stem_safe}</div>
  {options_html}
</article>"""
            )
            continue
        if q.qtype == "mcq" and q.options:
            options_html = "\n".join(
                f'<label><input type="radio" name="q{q.number}" value="{html.escape(label.upper())}"> {html.escape(label.upper())}. {html.escape(text)}</label>'
                for label, text in q.options
            )
            controls = f'<div class="options">{options_html}</div>'
        elif q.qtype in {"tfng", "ynng"}:
            opts = ["YES", "NO", "NOT GIVEN"] if q.qtype == "ynng" else ["TRUE", "FALSE", "NOT GIVEN"]
            options_html = "\n".join(
                f'<label><input type="radio" name="q{q.number}" value="{opt.lower()}"> {opt}</label>'
                for opt in opts
            )
            controls = f'<div class="options inline">{options_html}</div>'
        else:
            controls = f'<input type="text" name="q{q.number}" class="text-input" placeholder="Type answer" />'
        items.append(
            f"""<article class="question" id="q{q.number}">
  <div class="q-header"><span class="q-number">{q.number}</span><div class="q-meta">{q.qtype.upper()}</div></div>
  <div class="q-stem">{stem_html}</div>
  {controls}
</article>"""
        )
    return "\n".join(items)


def load_template(template_path: Path) -> str:
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def render_html(template: str, context: Dict[str, str]) -> str:
    from string import Template

    tpl = Template(template)
    return tpl.safe_substitute(context)


def storage_key_from_title(title: str) -> str:
    safe = re.sub(r"\W+", "_", title.lower()).strip("_")
    return f"ielts_practice_{safe or 'session'}"


def process_pdf(pdf_path: Path, args: argparse.Namespace) -> ParseOutcome:
    all_texts, total_pages = extract_pages_text(pdf_path, max_pages=None)
    sample_texts = all_texts[: min(2, len(all_texts))]
    char_count, ascii_ratio, suspected_scan = detect_scan(sample_texts)
    passage_text, questions_text, answers_text, notes = locate_blocks(all_texts)

    paragraphs = parse_paragraphs(passage_text)
    questions = parse_questions(questions_text)
    if not questions:
        full_text = "\n".join(all_texts)
        match = re.search(r"(?i)Questions?\s+\d{1,3}", full_text)
        fallback_text = full_text[match.start():] if match else full_text
        questions = parse_questions(fallback_text)
    answers = parse_answers(answers_text)
    status, validation_notes = validate(questions, answers, suspected_scan)
    notes.extend(validation_notes)

    outcome = ParseOutcome(
        paragraphs=paragraphs,
        questions=questions,
        answers=answers,
        status=status,
        notes=notes,
        suspected_scan=suspected_scan,
        char_count_sample=char_count,
        ascii_ratio_sample=ascii_ratio,
        total_pages=total_pages,
    )

    should_render = status == "READY" or args.force_html or bool(questions) or bool(paragraphs)
    if should_render:
        # Check if PDF is already in a folder with the same name
        if pdf_path.parent.name == pdf_path.stem:
            # PDF is already in a folder with same name, use that folder
            out_dir = pdf_path.parent
        else:
            # Create a new folder with the same name as PDF
            out_dir = args.output_dir / pdf_path.stem
            out_dir.mkdir(parents=True, exist_ok=True)
        if args.bundle_pdf:
            target_pdf = out_dir / pdf_path.name
            if not target_pdf.exists():
                shutil.copy2(pdf_path, target_pdf)
        template = load_template(args.template)
        title = pdf_path.stem
        question_numbers = expand_question_numbers(questions)
        filtered_answers = {f"q{num}": answers.get(f"q{num}", "") for num in question_numbers}
        meta_rows = []
        for q in questions:
            for num in _expand_number_string(q.number):
                meta_rows.append({"id": f"q{num}", "number": num, "type": q.qtype})
        html_content = render_html(
            template,
            {
                "TITLE": html.escape(title),
                "PASSAGE_HTML": build_passage_html(paragraphs),
                "QUESTION_NAV": build_question_nav(questions),
                "QUESTION_HTML": build_question_html(questions),
                "ANSWERS_JSON": json.dumps(
                    filtered_answers,
                    ensure_ascii=False,
                    indent=2,
                ),
                "QUESTION_META_JSON": json.dumps(
                    meta_rows,
                    ensure_ascii=False,
                ),
                "STORAGE_KEY": storage_key_from_title(title),
            },
        )
        output_html_path = out_dir / f"{pdf_path.stem}.html"
        output_html_path.write_text(html_content, encoding="utf-8")
    return outcome


def gather_pdfs(input_path: Path) -> List[Path]:
    if input_path.is_file() and input_path.suffix.lower() == ".pdf":
        return [input_path]
    return sorted(input_path.rglob("*.pdf"))


def write_report_rows(outcomes: List[Tuple[Path, ParseOutcome]], report_path: Path):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "file",
        "pages",
        "chars_first_pages",
        "ascii_ratio",
        "suspected_scan",
        "questions",
        "answers",
        "status",
        "notes",
    ]
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for pdf_path, outcome in outcomes:
            writer.writerow(
                [
                    pdf_path.name,
                    outcome.total_pages,
                    outcome.char_count_sample,
                    f"{outcome.ascii_ratio_sample:.2f}",
                    outcome.suspected_scan,
                    len(expand_question_numbers(outcome.questions)),
                    len(outcome.answers),
                    outcome.status,
                    "; ".join(outcome.notes),
                ]
            )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch PDF → HTML converter for IELTS-style sets.")
    parser.add_argument("input", type=Path, help="PDF file or directory containing PDFs.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Root output directory. Default: alongside the input PDF or inside the input folder.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).parent / "templates" / "base.html",
        help="HTML template path.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path.cwd() / "report.csv",
        help="CSV report output path.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of PDFs (for testing).")
    parser.add_argument(
        "--bundle-pdf",
        action="store_true",
        help="Copy the source PDF into the output folder (same stem).",
    )
    parser.add_argument(
        "--force-html",
        action="store_true",
        help="Render HTML even when status is NEEDS_FIX.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.output_dir is None:
        args.output_dir = args.input.parent if args.input.is_file() else args.input
    pdfs = gather_pdfs(args.input)
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print("No PDFs found.", file=sys.stderr)
        return 1

    outcomes: List[Tuple[Path, ParseOutcome]] = []
    for pdf_path in pdfs:
        print(f"[+] Processing {pdf_path}")
        try:
            outcome = process_pdf(pdf_path, args)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[!] Failed {pdf_path.name}: {exc}", file=sys.stderr)
            continue
        outcomes.append((pdf_path, outcome))
    write_report_rows(outcomes, args.report)
    print(f"Report written to {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
