#!/usr/bin/env sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${AI_TOKEN_TRACKER_PYTHON:-python3}
status=0

run_test() {
    printf '\n=== %s ===\n' "$1"
    "$PYTHON_BIN" "$1" || status=1
}

cd "$ROOT"
run_test tests/test_trust_reporting.py
run_test tests/test_reconciliation_audit.py
run_test tests/test_real_payload_azure.py
run_test tests/test_operational_metrics.py
run_test tests/test_powerbi_export.py
run_test tests/test_csv_excel_export.py
run_test tests/test_export_totals_match_model.py
run_test tests/test_azure_openai_adapters.py
run_test tests/test_bedrock_converse_adapter.py

printf '\nProvider validation matrix:\n'
"$PYTHON_BIN" -m tracker.proxy.cli provider-matrix || status=1

if [ "$status" -eq 0 ]; then
    printf '\nTRUSTED VERIFICATION: PASS\n'
else
    printf '\nTRUSTED VERIFICATION: FAIL\n'
fi
exit "$status"
