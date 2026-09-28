#!/usr/bin/env python3
"""Sign in to Kaggle and run the Kaggle CLI (macOS / Linux entry point).

The Windows entry point is ``bin/kaggle-cli.cmd``, which forwards to the same
``mcp/kaggle_cli.py``. This script is the equivalent for a shell, so both platforms
share one implementation of credential resolution and one set of commands.

It is a Python file with a shebang rather than a shell script, and it carries no
executable bit: a packaged plugin must not depend on a file mode surviving a
checkout. Invoke it through the interpreter, which is why the examples below name
``python3`` explicitly instead of running the file directly:

    python3 bin/kaggle-cli.sh login            # prompt for a token, input hidden
    python3 bin/kaggle-cli.sh login <TOKEN>    # or pass it directly
    python3 bin/kaggle-cli.sh whoami
    python3 bin/kaggle-cli.sh logout
    python3 bin/kaggle-cli.sh kernels list --mine

Requires Python 3 and ``pip install kaggle``. The token is stored under
``~/.kaggle-agent/accounts.json`` and is never printed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp"))

import kaggle_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(kaggle_cli.main(sys.argv[1:]))
