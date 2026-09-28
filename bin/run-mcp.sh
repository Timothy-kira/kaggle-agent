"""Start the Kaggle Agent MCP server by hand (macOS / Linux entry point).

The Windows entry point is ``bin/run-mcp.cmd``. This is the same thing for a shell: it runs the
MCP server over stdio, so a JSON-RPC frame can be typed at it and watched, which is the way to see
what a tool actually did rather than what a transcript claims it did.

    python3 bin/run-mcp.sh
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 bin/run-mcp.sh

It is a Python file with a shebang and no executable bit, on the same reasoning as
``bin/kaggle-cli.sh``: a packaged plugin should not depend on a file mode surviving a checkout.
Because of that it runs on Windows too, invoked through the interpreter.

The package locates itself, so the working directory does not have to be the package root.
"""
from __future__ import annotations

import os
import sys

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PACKAGE, "mcp"))

import agent_server  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(agent_server.serve())
