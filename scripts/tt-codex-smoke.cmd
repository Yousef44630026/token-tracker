@echo off
setlocal

set "PROXY=C:\Users\yerabhaoui\python-portable\Scripts\ai-token-tracker-proxy.exe"
set "CODEX_BIN=c:\Users\yerabhaoui\.vscode\extensions\openai.chatgpt-26.623.31921-win32-x64\bin\windows-x86_64\codex.exe"
set "STORE=codex_live.jsonl"
set "PROMPTS=CODEX_VARIED_TESTS.md"
set "BUDGET=50000"

if not exist "%PROXY%" (
  echo ai-token-tracker-proxy.exe not found at:
  echo %PROXY%
  exit /b 1
)

if not exist "%CODEX_BIN%" (
  echo codex.exe not found at:
  echo %CODEX_BIN%
  exit /b 1
)

"%PROXY%" codex-suite --store "%STORE%" --prompts "%PROMPTS%" --limit 1 --live-budget-tokens %BUDGET% --suppress-output --codex-bin "%CODEX_BIN%"
