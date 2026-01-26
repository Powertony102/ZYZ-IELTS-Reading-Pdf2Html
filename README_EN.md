# Overview

Convert IELTS-style reading PDFs into offline-friendly HTML: passage on the left, questions on the right, bottom navigation, a bottom-right timer, and answers that only appear after submission. Everything (answers, notes, highlights) is kept in browser localStorage so progress can be restored.

## Layout
- `Script/pdf_pipeline.py`: main Python pipeline (Steps A–G: type detection, block locating, paragraph split, question parsing, answer parsing, validation, HTML rendering).
- `Script/templates/base.html`: shared HTML template (timer, navigation, save/reset, highlight, notes, grading).
- `Petrol power an eco-revolution.html`: refreshed sample built from the template.
- `ZYZ 1月高频（139篇+29背景）/`: user data; ignored by git.

## Requirements
- Python 3.9+.
- PDF extraction libraries (install manually if offline): `pip install pdfplumber pypdf`.

## Usage
1) Install deps  
```bash
pip install pdfplumber pypdf
```
2) Try on a single PDF  
```bash
python Script/pdf_pipeline.py "ZYZ 1月高频（139篇+29背景）/P3（41高+9次）/1. 高频/187. P3 - Petrol power an eco-revolution 交通的革命【高】.pdf" \
  --output-dir "ZYZ 1月高频（139篇+29背景）/P3（41高+9次）/1. 高频" \
  --bundle-pdf \
  --limit 1
```
Key options:
- `--output-dir`: where per-PDF folders + HTML live (default: beside input). `--bundle-pdf` copies the source PDF alongside the HTML.
- `--report`: path for `report.csv` (pages, char sample, scan suspicion, parse status, notes).
- `--force-html`: still render HTML even when status is `NEEDS_FIX` (good for manual/Gemini fixes).
- `--limit`: process only N PDFs (debug).

## Pipeline (A–G)
- A: sample first pages, measure char count and ASCII ratio, flag likely scans.
- B: find Passage / Questions / Answer Key by keyword + page ranges.
- C: paragraphs: use A/B/C labels when present, otherwise auto P1/P2… by blank lines.
- D: question parsing: MCQ with choices, TF/NG, YN/NG, gaps; keep raw text for recovery.
- E: answer parsing: handles “1 C / 14 ii / TRUE / YES / NG”.
- F: validation: question/answer count sanity, scan hint; mark `NEEDS_FIX` when low confidence.
- G: HTML generation: template-driven, timer at bottom-right, answers revealed after submit, localStorage autosave.

## Template features
- Bottom-right timer (pause/resume) + bottom nav for quick jumps.
- Autosave: answers, notes, highlights, and grading results go to localStorage. `Save` is explicit; `Reset` has double confirmation.
- Highlight + notes: select text to highlight/remove; notes panel with copy.
- Score + answer list only after submit. Minimal, non-flashy styling; responsive on mobile.

## Gemini fallback
- Rows in `report.csv` marked `NEEDS_FIX` can be rendered with `--force-html`, then patched manually or by sending the raw blocks to Gemini CLI for structuring before writing back to JSON/HTML.

## Troubleshooting

### Blank HTML Page
**Cause**: Missing PDF libraries or bugs in previous versions.

**Solution**:
1. Ensure dependencies are installed:
   ```bash
   pip install pdfplumber pypdf
   ```

2. Delete old HTML files and regenerate:
   ```bash
   python Script/pdf_pipeline.py --force-html --bundle-pdf "path/to/your.pdf"
   ```

3. Check `report.csv` for parse status

### Question Type Misidentification
- Check if PDF format is standard
- Some MCQ questions might be misidentified as TF/NG due to keywords like "no"/"yes" in options
- Use `--force-html` to generate and manually adjust

### Missing Answers
- Some PDFs don't include answer keys (e.g., sample 187), which is normal
- Generated HTML still works for practice, just won't show reference answers
- You can provide separate answer files or manually add to HTML

## Notes
- `ZYZ 1月高频（139篇+29背景）/` is git-ignored to keep user PDFs and answers out of the repo.
- Clearing browser storage removes saved answers/highlights/notes; each HTML uses its own `STORAGE_KEY`.
- Reset uses a two-step confirm to avoid accidents.

## Known limitations
- PDF libs are not preinstalled in this environment—install before running the script.
```bash
conda install -c conda-forge pdfplumber pypdf
# or use pip
pip install pdfplumber pypdf

```
- The rule-based question detector may misclassify edge cases; `NEEDS_FIX` indicates items to review with Gemini/human passes.
