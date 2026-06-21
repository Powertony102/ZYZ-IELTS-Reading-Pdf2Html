#!/usr/bin/env bash
set -euo pipefail

# Generate *.answers.json sidecars for PDFs whose answer pages are image-based.
# This version uses a Python venv instead of conda.
#
# Usage:
#   bash batch_generate_ocr_answers.sh [INPUT_ROOT] [VENV_DIR]
#
# Required environment:
#   PADDLE_OCR_TOKEN=...
#
# Example:
#   PADDLE_OCR_TOKEN=your_token \
#   bash batch_generate_ocr_answers.sh "ZYZ 1月高频（139篇+29背景）" ".venv"

INPUT_ROOT="${1:-ZYZ 1月高频（139篇+29背景）}"
VENV_DIR="${2:-.venv}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
OCR_SCRIPT="$SCRIPT_DIR/Script/paddle_ocr_answers.py"

if [[ -z "${PADDLE_OCR_TOKEN:-}" ]]; then
  echo "[!] Missing PADDLE_OCR_TOKEN environment variable." >&2
  exit 2
fi

if [[ ! -d "$INPUT_ROOT" ]]; then
  echo "[!] Input directory does not exist: $INPUT_ROOT" >&2
  exit 2
fi

if [[ ! -f "$OCR_SCRIPT" ]]; then
  echo "[!] OCR helper not found: $OCR_SCRIPT" >&2
  exit 2
fi

# Resolve venv path relative to project root
if [[ ! -d "$VENV_DIR" ]]; then
  VENV_DIR="$PROJECT_ROOT/$VENV_DIR"
fi
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  echo "[!] Virtual environment not found: $VENV_DIR" >&2
  echo "    Please create it first, e.g.: python3 -m venv $VENV_DIR" >&2
  exit 2
fi

count_total=0
count_done=0
count_skip=0
count_fail=0

# Activate venv
source "$VENV_DIR/bin/activate"

echo "[+] Using venv: $VENV_DIR"
echo "[+] Python: $(which python)"
echo "[+] Version: $(python --version)"
echo

while IFS= read -r -d '' pdf_path; do
  count_total=$((count_total + 1))
  answers_path="${pdf_path%.pdf}.answers.json"

  if [[ -f "$answers_path" ]]; then
    echo "[=] ($count_total) Skip existing: $answers_path"
    count_skip=$((count_skip + 1))
    continue
  fi

  echo "[+] ($count_total) OCR answers: $pdf_path"

  if PADDLE_OCR_TOKEN="$PADDLE_OCR_TOKEN" python "$OCR_SCRIPT" "$pdf_path"
  then
    count_done=$((count_done + 1))
  else
    echo "[!] Failed: $pdf_path" >&2
    count_fail=$((count_fail + 1))
  fi
done < <(
  find "$INPUT_ROOT" -type f -name '*.pdf' ! -name '._*' -print0 | sort -z
)

echo
echo "[+] OCR answer generation finished."
echo "[+] Total PDFs:   $count_total"
echo "[+] Generated:    $count_done"
echo "[+] Skipped:      $count_skip"
echo "[+] Failed:       $count_fail"
