#!/usr/bin/env python3
"""Sign in to Kaggle and run the Kaggle CLI (macOS / Linux entry point).

The Windows entry point is ``bin/kaggle-cli.cmd``, which forwards to the same
``mcp/kaggle_cli.py``. This script is the equivalent for a shell, so both platforms
share one implementation of credential resolution and one set of commands:

    ./bin/kaggle-cli.sh login            # prompt for a token, input hidden
    ./bin/kaggle-cli.sh login <TOKEN>    # or pass it directly
    ./bin/kaggle-cli.sh whoami
    ./bin/kaggle-cli.sh logout
    ./bin/kaggle-cli.sh kernels list --mine

Requires Python 3 and ``pip install kaggle``. The token is stored under
``~/.kaggle-cli/credentials.json`` and is never printed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp"))

import kaggle_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(kaggle_cli.main(sys.argv[1:]))
