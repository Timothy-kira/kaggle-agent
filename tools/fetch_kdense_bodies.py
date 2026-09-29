"""Re-fetch the K-Dense bodies this package vendors, at a chosen pin.

Run from the package root:

    python tools/fetch_kdense_bodies.py <commit>

It writes each named skill directory upstream into the host skill that reads it, verbatim,
with one deliberate exception: the entry file is renamed from ``SKILL.md`` to ``<name>.md``.

The rename is not cosmetic. ``check_plugin`` collects plugin capabilities by filename, and it
collects them with ``rglob("SKILL.md")`` rather than a one-level glob, because a nested
``SKILL.md`` is a declared capability however deep it sits. A vendored copy left under that
name is therefore collected as a capability no manifest declares, and the build fails with
UNREFERENCED_CAPABILITY. Everything else keeps upstream's own layout, so a path printed in
the body still resolves.

The copy is byte-for-byte. Nothing is edited here on purpose: an adaptation belongs in the
host skill, where it is visible and arguable, not silently inside a file that claims to be
upstream's.

This script is not imported by the plugin. It is the record of where the vendored trees came
from and how to get them again at a different commit.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "K-Dense-AI/scientific-agent-skills"
UPSTREAM_LICENSE = "LICENSE.md"

# name -> the host skill whose references/ directory receives it, and why that host.
HOSTS: dict[str, tuple[str, str]] = {
    "hypothesis-generation": (
        "ruler-audit",
        "the rival-explanation and falsification step a declaration is missing"),
    "scientific-critical-thinking": (
        "ruler-audit",
        "severity grading and evidence quality for a verdict review"),
    "seaborn": (
        "scientific-plotting",
        "the 0.12 to 0.13 signature changes our engine's examples do not carry"),
    "scientific-visualization": (
        "scientific-plotting",
        "the body behind the assets, references and scripts already vendored here"),
    "scientific-writing": (
        "technical-report",
        "the claim/evidence id scheme that makes 'every sentence traces to a row' mechanical"),
    "scientific-slides": (
        "technical-report",
        "the talk structure the report skill does not cover"),
    "scientific-brainstorming": (
        "kaggle-competition-research",
        "independent generation and adversarial review, after the sweep has been synthesised"),
    # experimental-design was vendored here and then removed: it is a laboratory protocol
    # (three mice, plate edges, reagent ageing) and its only competitive content - blocking,
    # and what counts as a true independent replicate - is already carried by ablation-design
    # and ruler-audit. Re-adding it would buy a pyDOE3 dependency and nothing else.
}


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "mcp", rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# This machine reaches GitHub through a local proxy, and the proxy drops a large response
# mid-stream often enough to matter: an SSL EOF here is a transport event, not a 404, and
# treating the two alike is how a fetch reports "upstream is gone" when upstream is fine.
# So a transport failure is retried, a real status is not, and the count of retries is printed
# rather than swallowed - a run that needed 40 retries and one that needed 0 should not read
# the same in the log.
RETRIES = 4
retried = 0


def _get(gh, path: str, commit: str):
    global retried
    url = "/repos/%s/contents/%s?ref=%s" % (REPO, path, commit)
    for attempt in range(RETRIES):
        data, err, status = gh.api("GET", url)
        if status != 0:                      # a real answer, even a 404
            return data, err, status
        retried += 1
        if attempt < RETRIES - 1:
            time.sleep(1.5 * (attempt + 1))
    return data, err, status


def _write(outdir: str, rel_parts: list[str], payload: bytes) -> None:
    target = os.path.join(outdir, *rel_parts)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(payload)


MANIFEST_NAME = "MANIFEST.sha256"
# The manifest records what was copied, and `check_plugin` recomputes it. Byte totals, which is
# what the Anthropic copies are checked by, cannot tell a reworded heading from the original; a
# per-file digest can, and this is the only place the "shipped unedited" claim is mechanical
# rather than a promise in a README.
def _write_manifests(commit: str) -> None:
    for host in sorted({h for h, _ in HOSTS.values()}):
        base = os.path.join(ROOT, "skills", host, "references", "kdense")
        if not os.path.isdir(base):
            continue
        rows = []
        for dirpath, _dirs, names in os.walk(base):
            for fn in sorted(names):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, base).replace("\\", "/")
                if rel in (MANIFEST_NAME, "LICENSE.md"):
                    continue
                with open(full, "rb") as fh:
                    rows.append("%s  %s" % (hashlib.sha256(fh.read()).hexdigest(), rel))
        header = ("# vendored from %s at %s\n"
                  "# sha256 of every file under this directory except this manifest and "
                  "LICENSE.md\n" % (REPO, commit))
        with open(os.path.join(base, MANIFEST_NAME), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(header + "\n".join(sorted(rows, key=lambda r: r.split("  ", 1)[1])) + "\n")
        print("   manifest %-28s %d file(s)" % (host, len(rows)))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    commit = argv[1].strip()
    gh = _load("ks_fetch_gh", "github_sync.py")

    def get(path: str):
        return _get(gh, path, commit)

    lic, err, status = get(UPSTREAM_LICENSE)
    if status != 200 or not isinstance(lic, dict):
        print("FAIL reading upstream %s: status=%s %s" % (UPSTREAM_LICENSE, status, err))
        return 1
    licence_bytes = base64.b64decode(lic.get("content") or "")

    written = failed = 0
    for name, (host, _why) in sorted(HOSTS.items()):
        outdir = os.path.join(ROOT, "skills", host, "references", "kdense", name)
        listing, err, status = get("skills/%s" % name)
        if status != 200 or not isinstance(listing, list):
            print("FAIL list %s: status=%s %s" % (name, status, err))
            failed += 1
            continue
        queue: list[tuple[str, dict]] = []
        for item in listing:
            if item.get("type") == "dir":
                sub, err, status = get(item["path"])
                if status != 200 or not isinstance(sub, list):
                    print("FAIL list %s: status=%s %s" % (item["path"], status, err))
                    failed += 1
                    continue
                queue.extend((item["path"], e) for e in sub)
            else:
                queue.append(("skills/%s" % name, item))
        for _prefix, e in queue:
            if e.get("type") != "file":
                continue
            rel = e["path"].split("skills/%s/" % name, 1)[-1]
            got, err, status = get(e["path"])
            if status != 200 or not isinstance(got, dict):
                print("FAIL %s: status=%s %s" % (e["path"], status, err))
                failed += 1
                continue
            parts = rel.split("/")
            if parts == ["SKILL.md"]:
                parts = ["%s.md" % name]
            _write(outdir, parts, base64.b64decode(got.get("content") or ""))
            written += 1
        # The licence travels with what it covers. One copy per host, named for the repo it is.
        _write(os.path.join(ROOT, "skills", host, "references", "kdense"),
               ["LICENSE.md"], licence_bytes)
        print("ok %-30s -> %s" % (name, os.path.relpath(outdir, ROOT).replace("\\", "/")))
    _write_manifests(commit)
    print("wrote %d file(s), %d failure(s) at pin %s" % (written, failed, commit))
    print("transport retries: %d" % retried)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
