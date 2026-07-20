@echo off
setlocal

set "ROOT=%~dp0.."
set "PY=%~dp0_python.cmd"
if not defined TRACKER_STORE set "TRACKER_STORE=C:\ai-token-tracker-data\collector_events.jsonl"
if not defined TRACKER_DASHBOARD_EVIDENCE set "TRACKER_DASHBOARD_EVIDENCE=C:\ai-token-tracker-data\health\dashboard-refresh.json"
set "FAIL=0"

pushd "%ROOT%" >nul

call scripts\tt-check.cmd
if errorlevel 1 set "FAIL=1"

call scripts\tt-doctor.cmd --store "%TRACKER_STORE%" --strict-warnings
if errorlevel 1 set "FAIL=1"

call "%PY%" -m tracker.ops.release_readiness ^
  --dashboard-evidence "%TRACKER_DASHBOARD_EVIDENCE%" ^
  --max-dashboard-age-seconds 7200 ^
  --min-pricing-coverage 0.95 ^
  --min-latency-coverage 0.95 ^
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
