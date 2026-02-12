"""
HTML generation utilities, refactored from the original pdf_pipeline.py.
These functions are responsible for building the final HTML structure from parsed data.
"""

import html
import re
from typing import Dict, List, Any

def _expand_number_string(number: str) -> List[str]:
    """Expands a number string like '27-30' into ['27', '28', '29', '30']."""
    match = re.match(r"^\s*(\d{1,3})\s*[–-]\s*(\d{1,3})\s*$", number)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        return [str(n) for n in range(start, end + 1)]
    return [number.strip()]

def expand_question_numbers(questions: List[Dict[str, Any]]) -> List[str]:
    """Gets all individual question numbers from a list of question objects."""
    numbers: List[str] = []
    seen = set()
    for q in questions:
        # The question object from Gemini will have a simple 'number' field
        # which might be a range '27-30' or a single '31'.
        q_num_str = q.get("number", "")
        for num in _expand_number_string(q_num_str):
            if num and num not in seen:
                numbers.append(num)
                seen.add(num)
    return numbers

def build_passage_html(paragraphs: List[Dict[str, str]]) -> str:
    """Builds the HTML for the reading passage."""
    parts = []
    for idx, para in enumerate(paragraphs):
        pid = str(para.get("id", "")).strip()
        text = html.escape(para.get("text", ""))
        raw = (para.get("text") or "").strip()
        
        # Check if this is likely a title or subtitle
        is_title = (
            (idx == 0 and len(raw) < 100 and not raw.endswith('.')) or
            (raw.isupper() and len(raw) < 100)
        )

        if pid:
            label = f'<span class="paragraph-label">{html.escape(pid)}</span> '
            parts.append(f'<p id="para-{html.escape(pid)}">{label}{text}</p>')
        elif is_title and "READING PASSAGE" not in raw:
             parts.append(f'<h3 class="passage-title">{html.escape(raw)}</h3>')
        else:
            parts.append(f'<p>{text}</p>')
    return "\n".join(parts)

def build_question_nav(questions: List[Dict[str, Any]]) -> str:
    """Builds the bottom question navigation buttons."""
    numbers = expand_question_numbers(questions)
    return "\n".join(
        f'<button class="q-nav" data-target="q{num}">{num}</button>' for num in numbers
    )

def build_question_html(questions: List[Dict[str, Any]]) -> str:
    """Builds the HTML for the questions block."""
    items = []
    
    # Group questions by their original order and type
    group_anchors = {}
    current_group = []

    def render_group(group):
        if not group:
            return ""
        
        first_q = group[0]
        q_nums = expand_question_numbers(group)
        first_num = q_nums[0] if q_nums else ""
        
        # Anchor for scrolling
        anchor_id = f"q-anchor-{first_num}"
        if first_num:
            group_anchors[first_num] = anchor_id

        # Generate HTML for each question in the group
        group_html = []
        for q in group:
            group_html.append(render_single_question(q))

        return f'<div class="group" id="{anchor_id}">{"".join(group_html)}</div>'

    # This logic is simplified; we assume questions are pre-grouped if they share a type.
    # A more robust version would look at question numbers.
    last_qtype = None
    for q in questions:
        qtype = q.get("qtype")
        if qtype != last_qtype and current_group:
            items.append(render_group(current_group))
            current_group = []
        
        current_group.append(q)
        last_qtype = qtype
    
    if current_group:
         items.append(render_group(current_group))

    return "\n".join(items)

def render_single_question(q: Dict[str, Any]) -> str:
    """Renders a single question article."""
    q_num = q.get("number", "")
    q_type = q.get("qtype", "text")
    stem = q.get("stem", "")
    options = q.get("options", [])
    
    # HTML escape content
    stem_html = html.escape(stem).replace("\n", "<br>")
    
    # Generate anchors for each individual number in a range
    q_numbers = _expand_number_string(q_num)
    anchors = "".join(f'<span class="q-anchor" id="q{num}"></span>' for num in q_numbers)

    # Header section for the question
    header_html = f"""
    <div class="q-group-header">
        <h4>Questions {q_num}</h4>
        <p class="q-group-hint">{q.get("instruction", "")}</p>
    </div>
    """

    # Main content of the question
    content_html = ""

    if q_type == "summary" or q_type == "gap":
        # For gap-fill, we need to insert input boxes or dropzones
        summary_text = stem_html
        
        # Replace placeholders like (31) _____ or just __31__
        for num in q_numbers:
            placeholder = f"__BLANK_{num}__"
            # Regex to find the blank associated with a number
            # e.g., "31 ____" or "(31)"
            replaced = False
            patterns = [
                re.compile(rf"\b{num}\b\s*[_＿]{{2,}}"),
                re.compile(r"[\\(\\[]\s*" + re.escape(str(num)) + r"\s*[\\)\\]]"),
            ]
            for pattern in patterns:
                if pattern.search(summary_text):
                    summary_text = pattern.sub(placeholder, summary_text, count=1)
                    replaced = True
                    break
            if not replaced: # If no pattern found, just append it
                 summary_text += f" {placeholder}"

        # If options are provided (bank of words), create dropzones
        if options:
            for num in q_numbers:
                dropzone_html = f'<strong>{num}</strong> <span class="dropzone" data-target="q{num}"></span>'
                summary_text = summary_text.replace(f"__BLANK_{num}__", dropzone_html)
            
            cards = "".join(
                f'<div class="card" draggable="true" data-value="{html.escape(opt.get("label", ""))}">{html.escape(opt.get("label", ""))} {html.escape(opt.get("text", ""))}</div>'
                for opt in options
            )
            option_pool = f'<div class="option-pool">{cards}</div>'
            content_html = f'<div class="q-stem">{summary_text}</div>{option_pool}'
        else: # Otherwise, create simple text inputs
            for num in q_numbers:
                input_html = f'<strong>{num}</strong> <input type="text" name="q{num}" class="text-input inline-input" />'
                summary_text = summary_text.replace(f"__BLANK_{num}__", input_html)
            content_html = f'<div class="q-stem">{summary_text}</div>'

    elif q_type == "mcq":
        options_html = "\n".join(
            f'<label><input type="radio" name="q{q_num}" value="{html.escape(opt.get("label", ""))}"/> {html.escape(opt.get("label", ""))}. {html.escape(opt.get("text", ""))}</label>'
            for opt in options
        )
        content_html = f'<div class="q-block"><p>{stem_html}</p><div class="options">{options_html}</div></div>'
    
    elif q_type in {"tfng", "ynng"}:
        opts = ["TRUE", "FALSE", "NOT GIVEN"] if q_type == "tfng" else ["YES", "NO", "NOT GIVEN"]
        options_html = "\n".join(
            f'<label><input type="radio" name="q{q_num}" value="{opt.lower().replace(" ", "")}"> {opt}</label>'
            for opt in opts
        )
        content_html = f'<div class="q-block"><p><strong>{q_num}</strong> {stem_html}</p><div class="options inline">{options_html}</div></div>'

    else: # Default to a simple text input
        content_html = f'<div class="q-block"><p><strong>{q_num}</strong> {stem_html}</p><input type="text" name="q{q_num}" class="text-input" placeholder="Type answer" /></div>'
    
    # For questions with a shared instruction but individual inputs (like TF/NG), group them visually
    if q_type in {"tfng", "ynng", "mcq"} and "-" not in q_num:
         # This is a single question that is part of a logical group. The header is handled by the grouping logic.
         return f'<article class="question" id="q{q_num}">{anchors}{content_html}</article>'
    else:
        # This is a standalone question group (like a summary)
        return f'<article class="question" id="q{q_numbers[0] if q_numbers else ""}">{anchors}{header_html}{content_html}</article>'

def render_html(template: str, context: Dict[str, str]) -> str:
    """Safely substitutes variables into a string template."""
    # Using string.Template is safer but the original used f-strings.
    # We will use simple string replacement for this task.
    for key, value in context.items():
        template = template.replace(f'${key}', value)
    return template

def storage_key_from_title(title: str) -> str:
    """Creates a browser-safe key for localStorage."""
    safe = re.sub(r"\\W+", "_", title.lower()).strip("_")
    return f"ielts_practice_{safe or 'session'}"
