"""SessionStart hook: print claude/HOST.md (with this copy's root filled in) as session context."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

with open(os.path.join(HERE, "HOST.md"), encoding="utf-8") as f:
    text = f.read().replace("<PLUGIN_ROOT>", ROOT)
sys.stdout.reconfigure(encoding="utf-8")
sys.stdout.write(text)
