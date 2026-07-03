@echo off
setlocal enabledelayedexpansion

set "PY=C:\Users\yerabhaoui\python-portable\python.exe"
if not exist "%PY%" set "PY=python"

set "ROOT=%~dp0.."
pushd "%ROOT%" >nul

set FAIL=0

call :run tests\test_trust_reporting.py
call :run tests\test_reconciliation_audit.py
call :run tests\test_real_payload_azure.py
call :run tests\test_operational_metrics.py
call :run tests\test_powerbi_export.py
call :run tests\test_csv_excel_export.py
call :run tests\test_export_totals_match_model.py
call :run tests\test_azure_openai_adapters.py
call :run tests\test_bedrock_converse_adapter.py

echo.
echo Provider validation matrix:
"%PY%" -m tracker.proxy.cli provider-matrix
if errorlevel 1 set FAIL=1

popd >nul
if "%FAIL%"=="0" (
  echo.
  echo TRUSTED VERIFICATION: PASS
  exit /b 0
)

echo.
echo TRUSTED VERIFICATION: FAIL
exit /b 1

:run
echo.
echo === %~1 ===
"%PY%" "%~1"
if errorlevel 1 set FAIL=1
exit /b 0
