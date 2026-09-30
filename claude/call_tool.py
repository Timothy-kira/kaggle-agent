"""mcp/call_tool.py pinned to this copy of the plugin.

Upstream's call_tool locates the server with agent_server.locate(), which searches ~/.minimax/plugins
first, and then starts agent_server.py, which searches again; on a machine that also has the MiniMax
Code install, both would land on that copy. This wrapper points both at this package instead and
changes nothing else, so the upstream file stays untouched:

    python claude/call_tool.py kaggle_competitions_list search=arc
    python claude/call_tool.py --list
"""

import os
import subprocess
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(ROOT, "mcp")
sys.path.insert(0, MCP)

import agent_server  # noqa: E402
import call_tool  # noqa: E402

agent_server.locate = lambda: MCP


def _popen(args, *a, **kw):
    # launch this copy's server itself rather than the self-locating entry
    args = [os.path.join(MCP, agent_server.SERVER_MODULE) if str(x).endswith("agent_server.py") else x for x in args]
    return subprocess.Popen(args, *a, **kw)


shim = types.ModuleType("subprocess")
shim.__dict__.update(subprocess.__dict__)
shim.Popen = _popen
call_tool.subprocess = shim

if __name__ == "__main__":
    sys.exit(call_tool.main(sys.argv[1:]))
