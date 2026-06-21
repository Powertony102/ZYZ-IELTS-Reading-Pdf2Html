#!/usr/bin/env bash
set -euo pipefail

# Generate *.answers.json sidecars for PDFs whose answer pages are image-based.
#
# Usage:
#   bash batch_generate_ocr_answers.sh [INPUT_ROOT] [CONDA_ENV]
#
# Required environment:
#   PADDLE_OCR_TOKEN=...
#
# Example:
#   PADDLE_OCR_TOKEN=your_token \
#   bash batch_generate_ocr_answers.sh "ZYZ 1月高频（139篇+29背景）" "ielts"

INPUT_ROOT="${1:-ZYZ 1月高频（139篇+29背景）}"
CONDA_ENV="${2:-ielts}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

count_total=0
count_done=0
count_skip=0
count_fail=0

while IFS= read -r -d '' pdf_path; do
  count_total=$((count_total + 1))
  answers_path="${pdf_path%.pdf}.answers.json"

  if [[ -f "$answers_path" ]]; then
    echo "[=] ($count_total) Skip existing: $answers_path"
    count_skip=$((count_skip + 1))
    continue
  fi

  echo "[+] ($count_total) OCR answers: $pdf_path"

  if PADDLE_OCR_TOKEN="$PADDLE_OCR_TOKEN" conda run -n "$CONDA_ENV" \
    python "$OCR_SCRIPT" "$pdf_path"
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
