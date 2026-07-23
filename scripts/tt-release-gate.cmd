@echo off
setlocal

set "ROOT=%~dp0.."
set "PY=%~dp0_python.cmd"
if not defined TRACKER_STORE set "TRACKER_STORE=C:\ai-token-tracker-data\collector_events.jsonl"
if not defined TRACKER_DASHBOARD_EVIDENCE set "TRACKER_DASHBOARD_EVIDENCE=C:\ai-token-tracker-data\health\dashboard-refresh.json"
if not defined TRACKER_SCALE_EVIDENCE set "TRACKER_SCALE_EVIDENCE=C:\ai-token-tracker-data\health\scale-probe.json"
if not defined TRACKER_COLLECTOR_SOAK_EVIDENCE set "TRACKER_COLLECTOR_SOAK_EVIDENCE=C:\ai-token-tracker-data\evidence\collector-soak\summary.json"
if not defined TRACKER_RECOVERY_EVIDENCE set "TRACKER_RECOVERY_EVIDENCE=C:\ai-token-tracker-data\evidence\recovery-drill.json"
if not defined TRACKER_BILLING_EVIDENCE set "TRACKER_BILLING_EVIDENCE=C:\ai-token-tracker-data\evidence\billing-reconciliation.json"
if not defined TRACKER_PROVIDER_PROOF_DIR set "TRACKER_PROVIDER_PROOF_DIR=C:\ai-token-tracker-data\proofs\approved"
if not defined TRACKER_PROOF_CAPTURE_KEY_FILE set "TRACKER_PROOF_CAPTURE_KEY_FILE=C:\ai-token-tracker-data\proofs\keys\capture.key"
if not defined TRACKER_PROOF_REVIEW_KEY_FILE set "TRACKER_PROOF_REVIEW_KEY_FILE=C:\ai-token-tracker-data\proofs\keys\review.key"
set "FAIL=0"

pushd "%ROOT%" >nul

call scripts\tt-check.cmd
if errorlevel 1 set "FAIL=1"

call scripts\tt-doctor.cmd --store "%TRACKER_STORE%" --strict-warnings
if errorlevel 1 set "FAIL=1"

call "%PY%" -m tracker.ops.release_readiness ^
  --dashboard-evidence "%TRACKER_DASHBOARD_EVIDENCE%" ^
  --scale-evidence "%TRACKER_SCALE_EVIDENCE%" ^
  --provider-proof-dir "%TRACKER_PROVIDER_PROOF_DIR%" ^
  --max-provider-proof-age-seconds 2592000 ^
  --provider-proof-capture-key-file "%TRACKER_PROOF_CAPTURE_KEY_FILE%" ^
  --provider-proof-review-key-file "%TRACKER_PROOF_REVIEW_KEY_FILE%" ^
  --max-scale-age-seconds 604800 ^
  --min-scale-events 50000 ^
  --collector-soak-evidence "%TRACKER_COLLECTOR_SOAK_EVIDENCE%" ^
  --recovery-evidence "%TRACKER_RECOVERY_EVIDENCE%" ^
  --billing-evidence "%TRACKER_BILLING_EVIDENCE%" ^
  --max-operational-evidence-age-seconds 2592000 ^
  --min-collector-soak-seconds 259200 ^
  --max-dashboard-age-seconds 7200 ^
  --min-pricing-coverage 0.95 ^
  --min-instrumented-latency-coverage 0.95 ^
  --require-quality-status clean ^
  --strict-warnings ^
  --require-proven azure_openai:chat_completions:usage ^
  --require-proven azure_openai:chat_completions:stream ^
  --require-proven azure_openai:responses:usage ^
  --require-proven azure_openai:responses:stream ^
  --require-proven azure_openai:embeddings:usage ^
  --require-proven vertex_ai:generate_content:usage ^
  --require-proven vertex_ai:generate_content:stream ^
  --require-proven vertex_ai:generate_content:cache ^
  --require-proven vertex_ai:embeddings:usage ^
  --require-proven bedrock:converse:usage ^
  --require-proven bedrock:converse:stream ^
  --require-proven bedrock:converse:cache ^
  --require-proven bedrock:invoke_model:usage ^
  --require-proven bedrock:invoke_model:stream ^
  --require-proven bedrock:embeddings:usage
if errorlevel 1 set "FAIL=1"

popd >nul
if "%FAIL%"=="0" (
  echo.
  echo MULTI-CLOUD RELEASE GATE: PASS
  exit /b 0
)

echo.
echo MULTI-CLOUD RELEASE GATE: FAIL
exit /b 1
