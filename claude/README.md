# kaggle-agent for Claude Code

The same plugin as the MiniMax Code one (`~/.minimax/plugins/kaggle-agent`), packaged for Claude
Code. Everything Claude-specific is an added file; no upstream file is changed, so the branch
merges `main` without conflicts and the MiniMax Code install is never touched.

| File | What it does |
|---|---|
| `.claude-plugin/plugin.json` | the Claude Code manifest; skills are found in `skills/` as they are. The MCP server is declared inline here, not in a `.mcp.json`, because upstream's `tools/check_plugin.py` requires every `*.mcp.json` to be in the MiniMax manifest |
| `.claude-plugin/plugin.json` → `mcpServers` | starts `mcp/kaggle_server.py` of *this* copy directly (the self-locating `agent_server.py` searches `~/.minimax/plugins` first and would run the MiniMax copy) |
| `hooks/hooks.json` + `claude/host_context.py` | SessionStart: puts `claude/HOST.md` in the session, the mapping from the skills' MiniMax wording (`ask_user`, `task`, genui widgets, the `kaggle` alias) to Claude Code |
| `claude/call_tool.py` | `mcp/call_tool.py` pinned to this copy, for subagents that do not get the MCP tools |

## Install (done once on this machine)

- Local marketplace `akira-local` at `~/claude-plugins/.claude-plugin/marketplace.json`, listing
  `./kaggle-agent`.
- `~/.claude/settings.json`: `extraKnownMarketplaces.akira-local` (a directory source pointing at
  `~/claude-plugins`) and `enabledPlugins["kaggle-agent@akira-local"] = true`.
- Restart Claude Code (or open a new session) after changing either.

## Coexisting with MiniMax Code

- Accounts and tokens are shared: both read and write `~/.kaggle-agent/`.
- Handoffs, the experiment tree and local runs also live under `~/.kaggle-agent/`, so work started
  in one host can be picked up in the other.
- The MiniMax install, its git checkout and `~/.minimax/bin/kaggle.cmd` are not modified.

## Updating from upstream

```bash
git -C ~/claude-plugins/kaggle-agent fetch origin
git -C ~/claude-plugins/kaggle-agent merge origin/main
```

Then bump `version` in `.claude-plugin/plugin.json` to the new `.minimax-plugin/plugin.json`
version, and restart Claude Code so it reloads the plugin.
