@echo off
setlocal

set "PROXY=C:\Users\yerabhaoui\python-portable\Scripts\ai-token-tracker-proxy.exe"
set "CODEX_BIN=c:\Users\yerabhaoui\.vscode\extensions\openai.chatgpt-26.623.31921-win32-x64\bin\windows-x86_64\codex.exe"
set "STORE=codex_interactive_live.jsonl"
set "BUDGET=50000"
set "POLL_SECONDS=2"

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

"%PROXY%" codex --store "%STORE%" --live-budget-tokens %BUDGET% --poll-interval %POLL_SECONDS% --codex-bin "%CODEX_BIN%" -- --no-alt-screen %*
