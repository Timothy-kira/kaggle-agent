@echo off
REM ---------------------------------------------------------------------------
REM kaggle-cli.cmd - sign in to Kaggle and run the Kaggle CLI.
REM
REM This batch file only locates a Python interpreter and forwards to
REM mcp\kaggle_cli.py, which does the real work. All credential handling lives in
REM Python on purpose: resolving a token inside for /f backticks is fragile when the
REM path contains backslashes, and one implementation is easier to keep correct.
REM
REM Subcommands (run from your own terminal, so a token never passes through a
REM model conversation):
REM   kaggle-cli.cmd login [ACCESS_TOKEN]   store a token (prompted, hidden, if omitted)
REM   kaggle-cli.cmd whoami                 which account is active, and token source
REM   kaggle-cli.cmd logout                 forget the stored token
REM   kaggle-cli.cmd <kaggle args...>       run the Kaggle CLI, authenticated
REM
REM Credentials resolve in this order: KAGGLE_API_TOKEN, then this user's store at
REM %USERPROFILE%\.kaggle-cli\credentials.json, then KAGGLE_KEY. The token is never
REM written to this file, to the plugin package, or to stdout.
REM ---------------------------------------------------------------------------
setlocal DisableDelayedExpansion

set "MCP_DIR=%~dp0..\mcp"

REM Pick an interpreter that can actually import kaggle. Each candidate is probed, so this
REM works whether the CLI lives in a venv, in the user's Python, or in the system Python.
REM The sandbox venv is listed first because that is where the CLI was installed here, but a
REM different user with a normal "pip install kaggle" will match on the system Python instead.
set "PY="
if exist "C:\Users\Akira\.minimax\agents\arc26\venv\Scripts\python.exe" call :try "C:\Users\Akira\.minimax\agents\arc26\venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY for /f "usebackq delims=" %%p in (`where python 2^>nul`) do call :try "%%p"
if not defined PY (
  >&2 echo [kaggle-cli] no Python with the kaggle CLI was found. Install it with:
  >&2 echo   pip install kaggle
  exit /b 127
)

REM -B keeps the package free of __pycache__ build artifacts.
"%PY%" -B "%MCP_DIR%\kaggle_cli.py" %*
exit /b %ERRORLEVEL%

:try
rem %1 is a candidate interpreter; keep the first one that imports kaggle.
if defined PY goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import kaggle" >nul 2>&1 || goto :eof
set "PY=%~1"
goto :eof
