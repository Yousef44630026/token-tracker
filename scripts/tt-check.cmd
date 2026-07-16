@echo off
setlocal enabledelayedexpansion

set "PY=%~dp0_python.cmd"

set "ROOT=%~dp0.."
pushd "%ROOT%" >nul

set FAIL=0

echo.
echo === Ruff ===
call "%PY%" -m ruff check --no-cache tracker tests api
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_operational_doctor.py ===
call "%PY%" tests\test_operational_doctor.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_azure_smoke_harness.py ===
call "%PY%" tests\test_azure_smoke_harness.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_azure_openai_adapters.py ===
call "%PY%" tests\test_azure_openai_adapters.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_azure_simulated.py ===
call "%PY%" tests\test_azure_simulated.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_real_payload_azure.py ===
call "%PY%" tests\test_real_payload_azure.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_storage_no_stored_derived_fields.py ===
call "%PY%" tests\test_storage_no_stored_derived_fields.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_additivity_no_double_count.py ===
call "%PY%" tests\test_additivity_no_double_count.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_event_grain_no_double_count.py ===
call "%PY%" tests\test_event_grain_no_double_count.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_stream_supersession_no_double_count.py ===
call "%PY%" tests\test_stream_supersession_no_double_count.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_export_totals_match_model.py ===
call "%PY%" tests\test_export_totals_match_model.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_context_propagation_async.py ===
call "%PY%" tests\test_context_propagation_async.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_repository_tolerates_invalid_row.py ===
call "%PY%" tests\test_repository_tolerates_invalid_row.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_overlap_trust_axes.py ===
call "%PY%" tests\test_overlap_trust_axes.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_independent_subtotal_contradiction.py ===
call "%PY%" tests\test_independent_subtotal_contradiction.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_context_core.py ===
call "%PY%" tests\test_context_core.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_normalizer_more.py ===
call "%PY%" tests\test_normalizer_more.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_stream_tracker.py ===
call "%PY%" tests\test_stream_tracker.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_stream_timeout_keeps_known_input.py ===
call "%PY%" tests\test_stream_timeout_keeps_known_input.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_proxy_truncated_stream.py ===
call "%PY%" tests\test_proxy_truncated_stream.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_trust_report_storage_scale.py ===
call "%PY%" tests\test_trust_report_storage_scale.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_partitioned_repository_index.py ===
call "%PY%" tests\test_partitioned_repository_index.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_cross_process_storage.py ===
call "%PY%" tests\test_cross_process_storage.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_proxy_report.py ===
call "%PY%" tests\test_proxy_report.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_real_call_proxy.py ===
call "%PY%" tests\test_real_call_proxy.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_proxy_operational_config.py ===
call "%PY%" tests\test_proxy_operational_config.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_powerbi_export.py ===
call "%PY%" tests\test_powerbi_export.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_excel_dashboard_reporting.py ===
call "%PY%" tests\test_excel_dashboard_reporting.py
if errorlevel 1 set FAIL=1

echo === tests\test_dashboard_task_plan.py ===
call "%PY%" tests\test_dashboard_task_plan.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_powerbi_dedup_event_id.py ===
call "%PY%" tests\test_powerbi_dedup_event_id.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_api_collector.py ===
call "%PY%" tests\test_api_collector.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_collector_operational_config.py ===
call "%PY%" tests\test_collector_operational_config.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_collector_task_plan.py ===
call "%PY%" tests\test_collector_task_plan.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_collector_monitor.py ===
call "%PY%" tests\test_collector_monitor.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_collector_monitor_task_plan.py ===
call "%PY%" tests\test_collector_monitor_task_plan.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_collector_soak.py ===
call "%PY%" tests\test_collector_soak.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_api_server_errors.py ===
call "%PY%" tests\test_api_server_errors.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_collector_rejects_surfaced.py ===
call "%PY%" tests\test_collector_rejects_surfaced.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_privacy_audit.py ===
call "%PY%" tests\test_privacy_audit.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_privacy_audit_severity_regression.py ===
call "%PY%" tests\test_privacy_audit_severity_regression.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_html_report_ui_regression.py ===
call "%PY%" tests\test_html_report_ui_regression.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_static_html_operational_safety.py ===
call "%PY%" tests\test_static_html_operational_safety.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_supersession_collision_regression.py ===
call "%PY%" tests\test_supersession_collision_regression.py
if errorlevel 1 set FAIL=1

echo.
echo === tests\test_core_logic_deep.py ===
call "%PY%" tests\test_core_logic_deep.py
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
