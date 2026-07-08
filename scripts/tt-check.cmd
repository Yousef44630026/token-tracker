@echo off
setlocal enabledelayedexpansion

set "PY=C:\Users\yerabhaoui\python-portable\python.exe"
if not exist "%PY%" set "PY=python"

set "ROOT=%~dp0.."
pushd "%ROOT%" >nul

set FAIL=0

echo.
echo === Ruff ===
"%PY%" -m ruff check --no-cache tracker tests api
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_operational_doctor.py ===
"%PY%" tests\test_operational_doctor.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_azure_smoke_harness.py ===
"%PY%" tests\test_azure_smoke_harness.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_azure_openai_adapters.py ===
"%PY%" tests\test_azure_openai_adapters.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_azure_simulated.py ===
"%PY%" tests\test_azure_simulated.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_real_payload_azure.py ===
"%PY%" tests\test_real_payload_azure.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_storage_no_stored_derived_fields.py ===
"%PY%" tests\test_storage_no_stored_derived_fields.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_overlap_trust_axes.py ===
"%PY%" tests\test_overlap_trust_axes.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_trust_report_storage_scale.py ===
"%PY%" tests\test_trust_report_storage_scale.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_proxy_report.py ===
"%PY%" tests\test_proxy_report.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_powerbi_export.py ===
"%PY%" tests\test_powerbi_export.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_api_collector.py ===
"%PY%" tests\test_api_collector.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_core_logic_deep.py ===
"%PY%" tests\test_core_logic_deep.py
if errorlevel 1 set FAIL=1

popd >nul
if "%FAIL%"=="0" (
  echo.
  echo TRACKER CHECK: PASS
  exit /b 0
)

echo.
echo TRACKER CHECK: FAIL
exit /b 1
