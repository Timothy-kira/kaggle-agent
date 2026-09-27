"""Print the resolved Kaggle access token to stdout for a parent process to capture.

The launcher (`bin/kaggle-cli.cmd`, `bin/run-mcp.cmd`) runs this and assigns the output to
`KAGGLE_API_TOKEN` in its own environment, so child Kaggle calls authenticate. The value is
consumed by the parent and never displayed to a user, written to a log, or sent over the MCP
protocol. Prints an empty line when no credential is configured.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import credentials  # noqa: E402

if __name__ == "__main__":
    print(credentials.resolve_token()[0] or "")
