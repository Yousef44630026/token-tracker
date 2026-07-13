@echo off
setlocal

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."
set "STORE=%~1"
set "PROMPTS=%~2"

if "%STORE%"=="" set "STORE=codex_live.jsonl"
if "%PROMPTS%"=="" set "PROMPTS=SCENARIO_PROMPTS.md"

pushd "%ROOT%" >nul
"%PY%" -m tracker.proxy.cli privacy-audit --store "%STORE%" --prompts "%PROMPTS%"
set "CODE=%ERRORLEVEL%"

popd >nul
exit /b %CODE%
