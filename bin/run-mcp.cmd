@echo off
REM ---------------------------------------------------------------------------
REM run-mcp.cmd - start the kaggle MCP server by hand (stdio JSON-RPC 2.0).
REM
REM NOT what the host uses. servers.mcp.json launches the server with its own inlined
REM bootstrap (mcp/agent_server.py), which finds the package by marker file and therefore
REM works from any directory and on any platform. This file is the fallback for the case
REM that bootstrap does not cover: the host's default `python` may not have the kaggle CLI
REM installed, and the interpreter search below picks the first one that does. Launch it
REM yourself when you are debugging the server outside MiniMax Code.
REM
REM This batch file only locates a Python interpreter and starts
REM mcp\kaggle_server.py. Credentials are NOT resolved here: the server resolves
REM them itself through mcp\credentials.py (env var, then this user's store, then
REM KAGGLE_KEY), which keeps the same package working for any user without a
REM per-machine path baked into the package.
REM
REM stdout belongs to the MCP protocol from here on, so all diagnostics go to stderr.
REM ---------------------------------------------------------------------------
setlocal DisableDelayedExpansion

set "MCP_DIR=%~dp0..\mcp"

REM The MCP server runs the kaggle CLI in-process, so it needs an interpreter that can
REM import kaggle. Probe candidates and keep the first that can. Each candidate is guarded
REM by "if exist" and is either derived from the environment or from a common install
REM location: this package is installed on other people's machines, so no path into one
REM particular user's profile may appear here.
set "PY="
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY for /f "usebackq delims=" %%p in (`where python 2^>nul`) do call :try "%%p"
if not defined PY (
  >&2 echo [kaggle-cli] no Python with the kaggle CLI was found. Install it with:
  >&2 echo   pip install kaggle
  exit /b 127
)

REM -B keeps the package free of __pycache__ build artifacts: the plugin directory is
REM read-only by contract, and stray .pyc files would otherwise accumulate in it.
"%PY%" -B "%MCP_DIR%\kaggle_server.py"
exit /b %ERRORLEVEL%

:try
if defined PY goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import kaggle" >nul 2>&1 || goto :eof
set "PY=%~1"
goto :eof
