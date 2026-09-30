# kaggle-agent plugin: host mapping for Claude Code

The kaggle-agent skills were written for MiniMax Code. Their instructions still apply here; only
the host's own tools and paths have different names. Read the skills with this mapping:

- **Tools.** The skills name tools bare (`kaggle_quota`, `kaggle_accounts`, `kaggle_experiment_tree`,
  ...). Here they come from this plugin's MCP server `kaggle`, so their full names end in those
  names (for example `mcp__plugin_kaggle-agent_kaggle__kaggle_quota`). If they are deferred, load
  them with ToolSearch before calling them.
- **`ask_user`** is `AskUserQuestion`: at most 4 questions per call, 2-4 options each, your
  recommendation first and marked "(Recommended)", no "Other" option of your own. Where a skill
  says to ask "as text" (for example a free-form metric or slug), ask it plainly in your reply.
  A skill's "one call, two steps" form becomes one AskUserQuestion call with up to 4 questions.
- **`task` subagents and waves** are the `Agent` tool (`subagent_type: "general-purpose"`). A
  wave is several Agent calls in the same assistant message; `run_in_background` is available.
  Subagents may not get this plugin's MCP tools; the workaround the skills describe
  (`mcp/call_tool.py`) is, for this copy, `python -B "<PLUGIN_ROOT>/claude/call_tool.py" <tool> key=value ...`
  (or `--json @payload.json`, `--list`); give subagents that exact command in their brief.
- **genui widgets** (`<mavis-widget>`, `mavis.widget.v1`, `window.mavis.sendPrompt`,
  `window.__mavisData`) are `mcp__visualize__show_widget` when it is available (load its guide
  with `mcp__visualize__read_me` first): the same HTML/CSS/JS inside, the global `sendPrompt(text)`
  in place of `window.mavis.sendPrompt`, data inlined in the script. Without it, give a short
  markdown table and the choices as text. The skills' rules on *when* a widget is warranted
  (genui-scenarios) still decide whether to render one at all.
- **The `kaggle` shell alias** (`~/.minimax/bin/kaggle`, `bin/kaggle-cli.cmd`) is
  `python -B "<PLUGIN_ROOT>/mcp/kaggle_cli.py" ...` here. Credentials are the shared store in
  `~/.kaggle-agent/`, the same accounts MiniMax Code uses.
- **Presence mode.** "Present" is a user answering in this conversation. "Away" is a
  run the user has asked you to continue unattended (a /loop, a scheduled task, or an explicit
  "keep going without me").
- **Not applicable here:** the built-in `mavis` skill, the MiniMax plugin marketplace and
  `mcode plugin add`, `~/.minimax/...` plugin paths. This copy lives at `<PLUGIN_ROOT>`; do not
  edit the MiniMax Code install under `~/.minimax/plugins/kaggle-agent`.
