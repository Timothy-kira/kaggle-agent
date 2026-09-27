---
name: github-auth
description: Use when handoff sync to a remote repo is wanted but this machine cannot authenticate to GitHub - covering the browser device flow that needs no pasted secret, the fine-grained PAT fallback, and the cases where no GitHub setup is needed at all because git is already authenticated or the handoff is only staying local. Covers why an OAuth client_id belongs to the user and is never shipped in the plugin, and what to do when sync is genuinely unavailable.
---

# Getting GitHub access for handoff sync

`github_auth` reports what this machine can do and changes it. Start with `action=status`:
it makes **no network call**, so it is always safe to run and always the right first step.

```
github_auth action=status
```

Read the four lines that matter: `git installed`, `token available`, `repo`, and
`selected transport`.

## Do not set up GitHub unless it is needed

The common case for a handoff is **the same machine, a different agent**. Claude Code and
MiniMax Code share the filesystem, so `handoff_read` already works with nothing configured.
Offering a GitHub setup there is busywork with a credential attached.

Set up a remote only when the next agent is on a **different machine**, or when the user
explicitly wants the work mirrored. When in doubt, say the local file is enough and let
them decide.

## git is often missing, and that is not a failure

A clean Windows or macOS box frequently has no `git` at all. Check before promising a git
transport, and install it only if the user agrees — it is a real system change:

```
winget install --id Git.Git -e --source winget        # Windows
gh auth login --hostname github.com --git-protocol https --web   # then the browser login
```

Note that **the API transport does not need git at all.** `push_file` uses the GitHub Contents
API, so a machine with no git can still sync a handful of small text files with only a token.
git is required for pushing a whole package or a directory of many files in one commit, because
the Contents API commits one file at a time.

## Three ways to authenticate, and which to reach for

**GitHub CLI browser login — the shortest path, and usually the one to try first.**

If the user already uses `gh`, or is willing to install it, this is a browser login that needs no
app registration and no pasted secret, because the CLI carries GitHub's own client id:

```
gh auth login --hostname github.com --git-protocol https --web
```

It prints a **one-time code** and a URL, and the user approves in the browser. The code is
single-use and expires; it is not a credential, and it is fine to show.

This plugin then **reads that login directly** — `resolve_token()` looks in the CLI's own
`hosts.yml` before falling back, so the token never has to be copied out by hand into a shell
history or a chat transcript. After `gh auth login` succeeds, `github_auth action=status` reports
`token available: True` with source `gh-cli (browser login)` and no token has been pasted
anywhere.

That is the recommended route, because it is the only one that is both browser-based **and**
registration-free.

**Browser device flow through this plugin — no secret to paste.** The plugin asks GitHub for a
short code, you open `github.com/login/device`, type the code and approve, and the plugin collects
the token. The requested scopes are visible on the approval page, so you can see exactly what
you are granting.

```
github_auth action=begin      # returns a code and the URL
# approve it in the browser
github_auth action=finish     # call again after a few seconds until it succeeds
```

This needs a `client_id`, and that is the one real prerequisite:

- GitHub → Settings → Developer settings → OAuth Apps → New OAuth App
- **tick "Enable Device Flow"** - it is off by default, and the flow fails without it
- copy the Client ID: `github_auth action=configure client_id=Iv1.xxxx`

Reach for this when the user will not install the CLI. Otherwise prefer the two above.

**Fine-grained PAT — when a token already exists.** Give it `repo` scope on just the handoff
repo, and put it in `GITHUB_TOKEN` in the environment rather than in a file. No token is ever
written to the plugin's config; the store and the environment are the only two places one lives.

Token precedence is `GITHUB_TOKEN` → `GH_TOKEN` → the CLI's own store → the plugin's store. An
explicit environment variable wins because it is the most local and most deliberate; a CLI
login is honoured next because the user created it deliberately in a browser; the plugin's own
store is the last resort.

## The client_id belongs to the user

A published plugin cannot ship an OAuth `client_id`. That identifier belongs to whoever
registered the app, and baking one in would mean every user of the package was silently
authorising against someone else's registration. So the plugin asks for it and never
bundles one. If the user asks why there is no client_id pre-filled, that is the reason.

The same logic is why `github_auth` never asks the user to paste a token into a chat. Not a
policy technicality: chat history, session logs and transcripts all retain it, and the
device flow exists precisely so it never has to be pasted.

## When sync is unavailable, say so plainly

`handoff_sync` fails with a specific reason when there is no transport. The correct
response is to report the handoff as **local, not lost**:

- the document is on disk and readable by any agent on this machine;
- cross-machine pickup needs either a token or `git`;
- nothing was uploaded, so nothing was exposed.

Never describe a local-only handoff as synced, and never imply the work is lost when the
push fails. A sync failure is a missing convenience, not a lost artifact.

To enable it, in order of least effort for the user:

1. `gh auth login --web` — browser login, no registration, and this plugin reads the result
   directly, so nothing is pasted;
2. `GITHUB_TOKEN` in the environment, if they already have a PAT;
3. `github_auth action=begin` — the browser flow through this plugin, needs a `client_id`;
4. install `git` and let its own credential helper handle auth.

## Scopes and repos

A handoff sync needs read and write on **one** repository - the handoff repo. It does not
need issues, PRs, Actions, packages, or org scope. If a user is asked for more than that,
something is wrong; ask them to narrow it.

Keep the handoff repo `private`. It contains experiment trees, quota history, and often
notes about which approaches did not work - which is competitive information, and the
reason the whole thing is private by default.

## A note on the official GitHub MCP server

GitHub ships an official MCP server at `https://api.githubcopilot.com/mcp/`, free for all
GitHub users, with OAuth and toolsets. If a user wants full GitHub capability - issues,
PRs, Actions, code search - that is the better tool, and they can register it as their own
MCP server.

This plugin does not wrap it on purpose. Its built-in transport exists for the one thing
handoff actually needs - committing two small text files - and it does that with no Docker,
no OAuth app registration and no third-party dependency. When someone needs real GitHub
surface area, point them at the official server rather than growing this one.
