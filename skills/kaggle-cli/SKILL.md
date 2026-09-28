---
name: kaggle-cli
description: Use when working with Kaggle from this machine - signing in, listing, checking, pushing or downloading notebooks, and searching competitions; or when a Kaggle call fails with 401/403 and the active account or credential source needs identifying. Tools are prefixed kaggle_ (kaggle_auth_status, kaggle_config_view, kaggle_kernels_list, kaggle_kernels_status, kaggle_kernels_push, kaggle_kernels_output, kaggle_kernels_logs, kaggle_competitions_list).
---

# Kaggle Agent

Kaggle is driven through MCP tools, not through a shell. Each tool runs the real Kaggle CLI in a
subprocess, so the behaviour and the output match what a human sees typing the same command.

## Signing in (once per user)

The plugin stores credentials per user, so this works on any machine for any account.

**The normal path is a tool call, not a terminal.** Ask the user for their access token and
save it directly:

```
kaggle_accounts
  action   = "add"
  name     = <account name>
  token    = <ACCESS_TOKEN>
  username = <optional Kaggle username label>
```

A token is created at <https://www.kaggle.com/settings/account>.

If the user would rather not paste a token into the conversation, the command line writes to
the same store and is offered as an alternative, not as a requirement:

```
kaggle-cli login --as <name> <ACCESS_TOKEN> [kaggle-username]
kaggle-cli whoami           # which account is active, and where the token came from
kaggle-cli logout           # forget the stored token
```

On macOS/Linux the entry point is `python3 bin/kaggle-cli.sh`; on Windows it is
`bin/kaggle-cli.cmd`. Both run the same code, so the commands and output are identical.
`bin/kaggle-cli.sh` is a Python file with a shebang, not a shell script, and it is
committed without the executable bit on purpose: a package that depends on the mode bit
survives only the checkout it was built on. Calling it through the interpreter also means
one line works whether or not the bit was kept.

Credentials are resolved in this order, so an existing setup keeps working:

1. `KAGGLE_API_TOKEN` environment variable
2. this plugin's store, `~/.kaggle-agent/accounts.json` (a store at the previous
   `~/.kaggle-cli/accounts.json` is copied across verbatim on first read, and left in place)
3. `KAGGLE_KEY` environment variable

`KAGGLE_AGENT_HOME` moves the store, so a probe or a second install does not read the real one.

The token lives in one of those places only. It is never written into the plugin package and never
printed by a tool. A token pasted into the conversation is still saved - that is what the user
asked for - but it is also now in the transcript, so say so once and offer rotation as a known
option, not a lecture.

**Requirement:** Python 3 with the CLI installed (`pip install kaggle`), reachable under the name
`python` - that is the name `servers.mcp.json` launches, and a machine that ships `python3` only
has no `python`. The symptom of that is an install that succeeds and then exposes **no tools at
all**; `python --version` is the check, and the fix is a `python` on PATH rather than an edit to
the installed manifest, which the next update overwrites. If that interpreter has no `kaggle`,
the server reports it rather than failing silently.

The host launches the server through the bootstrap in `servers.mcp.json`, which finds this package
under any known plugin root - `~/.minimax/plugins/kaggle-agent` for a local install, or
`~/.minimax/v2/plugin-cache/official/sha256-tree-<hash>` for a Marketplace one - and runs
`mcp/agent_server.py`, which resolves this server from there.

## When no tool answers at all

A skill loads from the package on disk, not from the running server, so this section is readable
even when the server never started - which is exactly when it is needed.

Find out which of the two things is missing before proposing anything. The host prints a startup
error naming the interpreter it tried to run, and that name is the answer.

| What you see | What it means | What to do |
|---|---|---|
| Tool list empty, and the host mentions `python` not found | `servers.mcp.json` launches the server as `python`; this machine only has `python3` | macOS/Linux: `mkdir -p ~/.local/bin && ln -sf "$(command -v python3)" ~/.local/bin/python`, then start a new session. Windows: nothing to do, a normal Python install provides `python`. |
| Server starts, every call returns 127 | The interpreter was found and the Kaggle CLI is not installed for it | `kaggle_sources action="doctor"`; if the CLI is what is missing, ask the user before installing it |
| `kaggle_auth_status` answers | Nothing is wrong | - |

Do not edit the installed `servers.mcp.json` to make the error go away. The next update rewrites
it. The fix that survives is the one on the machine.

`python3 -B mcp/agent_server.py` starts the server by hand and prints whatever the startup error
is, which beats reading it out of a panel.


## Tools

| Tool | Use it for |
|---|---|
| `kaggle_auth_status` | Is Kaggle signed in, from where, as whom. **Start here on any 401/403.** |
| `kaggle_accounts` | Manage accounts: `action` is list / add / use / remove / status. |
| `kaggle_config_view` | The CLI's resolved config: username, auth method, path. |
| `kaggle_quota` | Remaining GPU and TPU hours, and the refresh date. Check before a long run. |
| `kaggle_accelerators` | Which accelerators can be requested, and what the quota allows. |
| `kaggle_kernel_launch` | Push a run with an explicit time limit, accelerator and account. |
| `kaggle_kernel_verify` | Did it actually get the accelerator it asked for? Run right after a launch. |
| `kaggle_kernel_retire` | Back up, delete and confirm deletion of a kernel that keeps burning quota. |
| `kaggle_kernels_list` | See notebooks. `mine=true` lists only this account's. |
| `kaggle_kernels_status` | Is a run idle / running / complete / error? Ref is `username/slug`. |
| `kaggle_kernels_push` | Create or update a notebook from a folder. **Starts a run.** |
| `kaggle_kernels_output` | Download the output files of a finished run. |
| `kaggle_kernels_logs` | Download the logs of a run. |
| `kaggle_competitions_list` | Search competitions. |
| `kaggle_competitions_forums` | Read a competition's discussion topics and messages. |
| `kaggle_competitions_leaderboard` | Read a competition's leaderboard. |

The handoff tools (`handoff_status`, `handoff_write`, `handoff_read`, `handoff_sync`,
`github_auth`) are documented in the `handoff` and `github-auth` skills.

## Adding an account from inside the conversation

`kaggle_accounts` with `action: "add"` takes a name and a token directly, so signing in
never requires leaving the conversation:

```
kaggle_accounts action="add" name="work" token="<ACCESS_TOKEN>" username="<kaggle username>"
```

Offer the terminal form as an alternative, not as a correction:

```
kaggle-cli login --as <name> <ACCESS_TOKEN> [username]
```

Both reach the same store. The choice is the user's: past it in the conversation and it
stays in the transcript, or run it in a terminal and it does not. If a token does arrive in
chat, save it - that is what was asked for - and mention once that it is now in the
transcript, so rotating it later is a known option rather than a surprise.

## The normal loop

1. `kaggle_auth_status` — signed in and as whom.
2. `kaggle_kernels_status` — is it running, or did it already finish?
3. Running → wait, then ask again. Do not push a new version while a run is live.
4. Complete → `kaggle_kernels_output` into a local folder, then read the result files.
5. Error → `kaggle_kernels_logs`, read the traceback, fix, push again.

## Pushing a notebook

The folder must contain **both** `notebook.ipynb` and `kernel-metadata.json`:

```
build/api/
  notebook.ipynb
  kernel-metadata.json     <- holds "id": "username/slug", gpu/internet flags
```

The `id` in `kernel-metadata.json` decides which notebook is written. It must match the account that
`kaggle_auth_status` reports, or the push fails. The folder is uploaded as-is, so stage any code the
notebook needs into it before pushing.

## Notes

- Output is truncated. If a listing looks cut off, narrow it (`mine=true`, a `search` string, or a
  specific `ref`) rather than re-running the same broad query.
- A push that is accepted still has to be started; confirm with `kaggle_kernels_status`.
- `kaggle auth login` in the upstream CLI is browser-based and interactive; this plugin's
  `kaggle-cli.cmd login` (Windows) and `python3 bin/kaggle-cli.sh login` (macOS, Linux)
  are the non-interactive paths for a token.
