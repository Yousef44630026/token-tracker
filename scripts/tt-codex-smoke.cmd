@echo off
setlocal

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."
if not defined CODEX_BIN set "CODEX_BIN=codex"
set "STORE=codex_live.jsonl"
set "PROMPTS=SCENARIO_PROMPTS.md"
set "BUDGET=50000"

pushd "%ROOT%" >nul
"%PY%" -m tracker.proxy.cli codex-suite --store "%STORE%" --prompts "%PROMPTS%" --limit 1 --live-budget-tokens %BUDGET% --suppress-output --codex-bin "%CODEX_BIN%"
set "CODE=%ERRORLEVEL%"

popd >nul
exit /b %CODE%
