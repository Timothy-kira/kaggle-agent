"""End-to-end: naming an account for one call must not change who the next call runs as.

This is the mechanism the two-account case depends on. The account that may READ a
competition's files and the account that has GPU quota left are routinely different accounts,
and the second one is often the one that never entered the competition. `kaggle_kernel_launch`
used to reach its named account through `credentials.use_account`, which rewrites the stored
active account -- so one launch charged to B silently left every later competition-scoped call
running as B, which cannot read the files at all. The failure looks like a permissions problem
somewhere downstream, not like a launch.

So this drives the real `run_kaggle` -- the same token resolution, the same command detection,
the same environment construction -- and swaps only the outermost `subprocess.run`, which is the
one step that would need a network. What is asserted is the thing that actually broke: the
store afterwards.

Usage:  python tools/probe_account_scope.py
"""

import atexit as _atexit
import json
import os
import shutil as _shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_OWN_TEMP_DIRS: list[str] = []


def _mkdtemp(*args, **kwargs) -> str:
    """A temp directory this process is responsible for removing.

    Same helper every probe in this package carries, for the same reason: a throwaway home per
    run that is never removed piles up as residue that reads as if the work left it behind.
    """
    d = tempfile.mkdtemp(*args, **kwargs)
    _OWN_TEMP_DIRS.append(d)
    return d


_atexit.register(lambda: [_shutil.rmtree(d, ignore_errors=True) for d in _OWN_TEMP_DIRS])

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import credentials  # noqa: E402
import kaggle_server as ks  # noqa: E402
import kaggle_cli  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print("  ok   %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL %s" % label)


class _FakeProc:
    returncode = 0
    stdout = "ok"
    stderr = ""


def main() -> int:
    home = _mkdtemp(prefix="ka-acct-")
    os.environ["KAGGLE_AGENT_HOME"] = str(home)
    # The env token wins over the store in resolve_token, so it has to be out of the way or
    # every case below would pass without ever reading the account file.
    os.environ.pop("KAGGLE_API_TOKEN", None)
    os.environ.pop("KAGGLE_KEY", None)

    credentials.add_account("alpha", "TOKEN-ALPHA", "alpha_user")
    credentials.add_account("beta", "TOKEN-BETA", "beta_user")
    credentials.use_account("alpha")

    seen: list[dict] = []
    real_run = subprocess.run

    def spy(cmd, **kw):
        seen.append({"cmd": cmd, "token": (kw.get("env") or {}).get("KAGGLE_API_TOKEN")})
        return _FakeProc()

    ks.subprocess.run = spy
    try:
        print("account scope")

        # 1. The named account is the one that pays, for this call.
        ks.run_kaggle(["kernels", "list", "--mine"], account="beta")
        check(seen[-1]["token"] == "TOKEN-BETA",
              "a call naming beta runs with beta's token")

        # 2. THE regression. The store must not have moved.
        store = json.loads((Path(home) / "accounts.json").read_text(encoding="utf-8"))
        check(store.get("active") == "alpha",
              "naming beta for one call left the active account as alpha")
        check(set(store.get("accounts") or {}) == {"alpha", "beta"},
              "the account store kept both accounts")

        # 3. A later call with no name still runs as the account that was already active --
        #    this is the one that breaks in production, when that account is the only one
        #    entitled to the competition's files.
        ks.run_kaggle(["competitions", "files", "-c", "some-slug"])
        check(seen[-1]["token"] == "TOKEN-ALPHA",
              "the next unnamed call still runs as alpha, not as the account just used")

        # 4. An unsaved name is refused rather than silently falling back.
        seen.clear()                      # count only this call, not the ones before it
        code, _out, err = ks.run_kaggle(["kernels", "list"], account="gamma")
        check(code == 2 and "gamma" in (err or ""),
              "an unsaved account name is refused by name, not run as the active one")
        check(len(seen) == 0, "the refused call never reached the CLI at all")
        store = json.loads((Path(home) / "accounts.json").read_text(encoding="utf-8"))
        check(store.get("active") == "alpha", "the refused call left the active account alone")

        # 5. The tool layer reads the name off the arguments.
        check(ks._acct({"account": " beta "}) == "beta", "the tool layer trims the named account")
        check(ks._acct({}) == "" and ks._acct({"account": ""}) == "",
              "an unnamed call resolves to no account")

        # 6. The MCP tools that need it actually expose it, since a handler that reads an
        #    argument the schema does not declare cannot be called at all.
        by_name = {t["name"]: t for t in ks.TOOLS}
        expected = ["kaggle_quota", "kaggle_accelerators", "kaggle_kernel_launch",
                    "kaggle_kernel_pull", "kaggle_kernels_list", "kaggle_kernels_status",
                    "kaggle_kernels_push", "kaggle_kernels_output", "kaggle_kernels_logs",
                    "kaggle_competitions_list", "kaggle_competitions_forums",
                    "kaggle_competitions_leaderboard", "kaggle_datasets_publish"]
        missing = [n for n in expected
                   if "account" not in ((by_name.get(n, {}).get("inputSchema", {}) or {})
                                        .get("properties") or {})]
        check(not missing, "every account-aware tool declares account in its schema%s"
              % ("" if not missing else " (missing: %s)" % ", ".join(missing)))

        # 7. The launch path must not reach for use_account any more. Scoped deliberately: the
        #    kaggle_accounts tool is SUPPOSED to switch accounts, because the user asked it to,
        #    so a blanket "the word never appears" would forbid the one legitimate switch. What
        #    must not exist is an implicit switch on a call that only meant to spend quota.
        #    Comment lines are dropped first: the comment documenting this very fix mentions
        #    the old call by name, and a scan that reads its own explanation is not a test.
        src = (ROOT / "mcp" / "kaggle_server.py").read_text(encoding="utf-8")
        code_only = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        # Offsets have to be taken on the SAME text that is being sliced, or the slice is
        # silently taken from the wrong place -- two coordinate systems, one index.
        accounts_branch = code_only.find('if name == "kaggle_accounts"')
        launch_branch = code_only.find('if name == "kaggle_kernel_launch"')
        check(accounts_branch > 0 and launch_branch > 0,
              "the launch and accounts branches are both present to compare")
        lo, hi = sorted((launch_branch, accounts_branch))
        check("use_account" not in code_only[lo:hi],
              "no branch between the launch and the accounts tool switches the active account")
        check("use_account" in code_only[hi:],
              "the kaggle_accounts tool can still switch accounts on purpose")

        # 8. The CLI can do the same one-shot thing, so the shell route is not the workaround
        #    for switching globally.
        code = kaggle_cli.main(["--as", "beta", "config", "view"])
        check(code == 0 and seen[-1]["token"] == "TOKEN-BETA",
              "kaggle-cli --as beta runs one command as beta")
        check(json.loads((Path(home) / "accounts.json")
                         .read_text(encoding="utf-8")).get("active") == "alpha",
              "kaggle-cli --as beta left the active account as alpha")
        code = kaggle_cli.main(["--as", "gamma", "config", "view"])
        check(code == 2, "kaggle-cli --as with an unsaved name exits non-zero")
    finally:
        ks.subprocess.run = real_run

    print()
    if FAILURES:
        print("PROBE_ACCOUNT_SCOPE FAILED: %d" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("PROBE_ACCOUNT_SCOPE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
