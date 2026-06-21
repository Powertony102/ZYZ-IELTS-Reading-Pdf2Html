#!/usr/bin/env bash
set -euo pipefail

# Batch-convert a whole IELTS PDF library into practice HTML.
# This version uses a Python venv instead of conda.
#
# Usage:
#   bash batch_convert_all_venv.sh [INPUT_ROOT] [OUTPUT_ROOT] [VENV_DIR]
#
# Example:
#   bash batch_convert_all_venv.sh \
#     "ZYZ 1月高频（139篇+29背景）" \
#     "server_build/html" \
#     ".venv"

INPUT_ROOT="${1:-ZYZ 1月高频（139篇+29背景）}"
OUTPUT_ROOT="${2:-server_build/html}"
VENV_DIR="${3:-.venv}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
PIPELINE="$PROJECT_ROOT/Script/pdf_pipeline.py"

if [[ ! -d "$INPUT_ROOT" ]]; then
  echo "[!] Input directory does not exist: $INPUT_ROOT" >&2
  exit 2
fi

if [[ ! -f "$PIPELINE" ]]; then
  echo "[!] Pipeline script not found: $PIPELINE" >&2
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

mkdir -p "$OUTPUT_ROOT"

MASTER_REPORT="$OUTPUT_ROOT/report.csv"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

printf 'file,pages,chars_first_pages,ascii_ratio,suspected_scan,questions,answers,status,notes\n' > "$MASTER_REPORT"

count_total=0
count_ok=0
count_fail=0

# Activate venv
source "$VENV_DIR/bin/activate"

echo "[+] Using venv: $VENV_DIR"
echo "[+] Python: $(which python)"
echo "[+] Version: $(python --version)"
echo

while IFS= read -r -d '' pdf_path; do
  count_total=$((count_total + 1))

  rel_path="${pdf_path#"$INPUT_ROOT"/}"
  rel_dir="$(dirname "$rel_path")"
  per_output_dir="$OUTPUT_ROOT/$rel_dir"
  per_report="$TMP_DIR/report_$count_total.csv"

  mkdir -p "$per_output_dir"

  echo "[+] ($count_total) Converting: $pdf_path"

  if python "$PIPELINE" \
    "$pdf_path" \
    --output-dir "$per_output_dir" \
    --report "$per_report" \
    --limit 1 \
    --force-html \
    --bundle-pdf
  then
    if [[ -f "$per_report" ]]; then
      tail -n +2 "$per_report" >> "$MASTER_REPORT"
    fi
    count_ok=$((count_ok + 1))
  else
    echo "[!] Failed: $pdf_path" >&2
    count_fail=$((count_fail + 1))
  fi
done < <(
  find "$INPUT_ROOT" -type f -name '*.pdf' ! -name '._*' -print0 | sort -z
)

echo
echo "[+] Done."
echo "[+] Total PDFs:   $count_total"
echo "[+] Successful:   $count_ok"
echo "[+] Failed:       $count_fail"
echo "[+] Output root:  $OUTPUT_ROOT"
echo "[+] Report CSV:   $MASTER_REPORT"
