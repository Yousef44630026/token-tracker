@echo off
setlocal

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."
if not defined CODEX_BIN set "CODEX_BIN=codex"
set "STORE=codex_interactive_live.jsonl"
set "BUDGET=50000"
set "POLL_SECONDS=2"

pushd "%ROOT%" >nul
"%PY%" -m tracker.proxy.cli codex --store "%STORE%" --live-budget-tokens %BUDGET% --poll-interval %POLL_SECONDS% --codex-bin "%CODEX_BIN%" -- --no-alt-screen %*
set "CODE=%ERRORLEVEL%"

popd >nul
exit /b %CODE%
