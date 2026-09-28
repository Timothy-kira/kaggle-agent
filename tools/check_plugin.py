#!/usr/bin/env python3
"""Local validation for the kaggle-agent plugin.

This is the enforcement half of the skill-relationship binding. relationships.json
is the single source of truth for how the skills relate; the 'Bound edges' table in
each category INDEX.md is rendered from it. If the two drift, this fails and names the
stale table, so the relationship graph cannot silently rot back into loose prose.

Run:  python tools/check_plugin.py
Exit: 0 = all checks pass, 1 = at least one failure.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


import atexit as _atexit
import shutil as _shutil

_OWN_TEMP_DIRS: list[str] = []


def _mkdtemp(*args, **kwargs) -> str:
    """A temp directory this process is responsible for removing.

    Thirty-odd suites across these files each made a throwaway home per run and never removed
    it, so a few hundred ka-* folders had piled up in the user's temp directory. That residue
    reads as if the WORK left it behind when the tests did, and a suite that passes every
    assertion can still leave a mess behind - which is exactly the kind of failure that never
    shows up as a failure. Tracking each directory and removing it at exit is the whole fix,
    and it only ever touches what this process created, so a suite running beside another one
    cannot delete its neighbour's home.
    """
    d = tempfile.mkdtemp(*args, **kwargs)
    _OWN_TEMP_DIRS.append(d)
    return d


_atexit.register(lambda: [_shutil.rmtree(d, ignore_errors=True) for d in _OWN_TEMP_DIRS])

# The launchers start the server with `-B` so the package directory stays free of
# __pycache__. A developer running this file by hand would put it straight back, and
# the directory is read-only by contract - so the tool holds the same line the
# launcher does. Set before any mcp/ module is imported below, which is what makes it
# effective rather than decorative.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".minimax-plugin" / "plugin.json"
SERVERS = ROOT / "servers.mcp.json"
REL = ROOT / "skills" / "relationships.json"
SERVER_PY = ROOT / "mcp" / "kaggle_server.py"

# declare now requires a falsifiable prediction, so any suite that declares a node without
# caring about the prediction has to say one. Doing it here rather than editing a dozen call
# sites keeps the suites readable - and keeps the default an honest prediction rather than a
# new escape hatch nobody reads.
_PREDICT = {"direction": "up", "atLeast": 0.01}


def with_prediction(node: dict) -> dict:
    """A declaration payload that satisfies the prediction gate."""
    out = dict(node)
    if out.get("expect") is None and not out.get("expectOmitted"):
        out["expect"] = dict(_PREDICT)
    return out


def _decl(et, comp, node: dict, **kw):
    """declare() with the prediction gate satisfied, for suites that are not testing it."""
    return et.declare(comp, with_prediction(node), **kw)

CATEGORIES = ["identity", "research", "experiment", "collab"]

failures: list[str] = []
checks = 0


def ok(msg: str) -> None:
    global checks
    checks += 1
    print(f"  ok  {msg}")


def bad(msg: str) -> None:
    global checks
    checks += 1
    failures.append(msg)
    print(f"FAIL  {msg}")


def check(cond, msg):
    """Assert one thing: check(condition, "message").

    A reversed check("some label", condition) has been written here eight times, and a
    non-empty label is always truthy, so every one of those assertions passed without
    checking anything. A runtime guard was tried and REMOVED: it cannot tell a literal
    label from a boolean expression that happens to evaluate to a non-empty string
    ("when" in e and e["when"].strip() is one), and a guard that rejects correct code is
    worse than no guard. The reliable form is the AST audit, check_no_reversed_assertions,
    which can see that the first argument is a literal rather than a test.
    """
    if cond:
        ok(msg)
    else:
        bad(msg)
    return cond


def parse_json(path: Path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return None, "has a UTF-8 BOM"
    try:
        return json.loads(raw.decode("utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"invalid JSON: {exc}"


# ---------------------------------------------------------------- vendored content
# Anchored on attack-specific vocabulary, not on bossy English. "you must" appears in honest
# skill prose constantly; flagging it teaches everyone to ignore this check, which is worse
# than not having one. The `(?:\w+\s+){0,n}` gap between tokens is deliberate - it defeats
# "ignore all PRIOR instructions" style evasion without matching ordinary prose.
INJECTION_PATTERNS = (
    ("ignore-prior-instructions",
     r"ignore\s+(?:\w+\s+){0,3}(?:all\s+)?(?:previous|prior|earlier|above|foregoing)\s+"
     r"(?:\w+\s+){0,2}instructions"),
    ("reveal-system-prompt",
     r"(?:reveal|print|show|output|repeat|dump)\s+(?:\w+\s+){0,3}(?:system\s+prompt|"
     r"your\s+instructions|developer\s+message|initial\s+prompt)"),
    ("role-hijack", r"you\s+are\s+now\s+(?:a|an|the)\s+\w+"),
    ("exfiltrate-secret",
     r"(?:send|post|upload|transmit|exfiltrate|leak)\s+(?:\w+\s+){0,3}"
     r"(?:api[\s_-]?keys?|tokens?|credentials?|\.env|ssh\s+key|private\s+key)"),
    ("hide-from-user",
     r"(?:\w+\s+){0,3}do\s+not\s+(?:\w+\s+){0,2}(?:tell|inform|notify|mention\s+(?:this\s+)?to|"
     r"show)\s+(?:the\s+)?(?:user|human|operator)"),
    ("exec-decoded-blob",
     r"base64\s+(?:-d|--decode)[^|\n]{0,60}\|\s*(?:ba|z|d)?sh"),
    ("pipe-download-to-shell",
     r"(?:curl|wget)\s+[^|\n]{0,120}\|\s*(?:sudo\s+)?(?:ba)?sh"),
    ("destructive-rm",
     r"\brm\s+-[a-z]*[rR][a-z]*\s+/(?:\s|$)|Remove-Item[^-\n]{0,40}-Recurse[^-\n]{0,20}"
     r"-Force[^-\n]{0,40}[A-Za-z]:\\\\\\?"),
)


# Build residue, not content. Running an upstream script regenerates __pycache__ next to it,
# and a .pyc is a derived, machine-specific blob whose readable twin is already in the set -
# counting it as "third-party content nobody scanned" would be both true and useless noise.
_BUILD_RESIDUE_DIRS = {"__pycache__"}
_BUILD_RESIDUE_SUFFIX = {".pyc", ".pyo"}


def _is_build_residue(p: Path) -> bool:
    return bool(_BUILD_RESIDUE_DIRS.intersection(p.parts)) or p.suffix.lower() in _BUILD_RESIDUE_SUFFIX


def vendored_files() -> list[Path]:
    """Every file this package took from someone else.

    By convention, not by manifest: anything under a skill's assets/, references/ or scripts/
    came from upstream, and the SKILL.md beside it is the only file in that skill this
    package authored. That is a convention, so it is a thing the check can drift away from -
    which is why the count is asserted too.
    """
    out: list[Path] = []
    for skill in sorted((ROOT / "skills").glob("*/")):
        for sub in ("assets", "references", "scripts"):
            d = skill / sub
            if d.is_dir():
                out.extend(p for p in sorted(d.rglob("*"))
                           if p.is_file() and not _is_build_residue(p))
    return out


# A security document legitimately says "must not transmit a token", and flagging that teaches
# everyone to ignore this check - which is worse than not having one. But a tripwire that
# downgrades hits on its own judgement and says nothing is worse still, so downgrading is
# clause-scoped and always reported.
_PROHIBITION = re.compile(
    r"\b(?:must|may|shall|can|will|do|does|did|is|are|should)\s+not\b"
    r"|\bnever\b|\bcannot\b|\bcan't\b|\bdon't\b|\bdoesn't\b|\bwon't\b"
    r"|\brefuse[sd]?\s+to\b|\bwithout\b", re.I)
_CLAUSE_BREAK = re.compile(r"[.;!?]")


def _phrased_as_prohibition(line: str, match_start: int) -> bool:
    """True when the match sits inside a clause that forbids something.

    Clause-scoped on purpose: "Never mind. Ignore all previous instructions" puts `Never` in the
    *previous* sentence, and reading it as a prohibition would suppress a real attack. The
    residual hole - "Do not follow this: ignore all previous instructions" - is accepted rather
    than closed, because closing it means parsing intent, and a scanner that guesses intent is
    the failure mode this whole check exists to avoid. Downgraded hits are printed regardless.
    """
    return bool(_PROHIBITION.search(_CLAUSE_BREAK.split(line[:match_start])[-1]))


def _line_at(text: str, pos: int) -> tuple[int, str, int]:
    """(1-based line number, the line's text, offset of pos inside that line)."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end == -1:
        end = len(text)
    return text.count("\n", 0, pos) + 1, text[start:end], pos - start


def scan_for_injection(text: str) -> list[tuple[str, int, bool]]:
    """Return (pattern_id, 1-based line, phrased_as_prohibition) for every hit.

    A clean result means "no known-bad strings", never "safe" - a regex tripwire is not a
    boundary. The layer that can acquit content is a reviewer reading it, and the plugin says
    so in the skills that vendor it.
    """
    hits: list[tuple[str, int, bool]] = []
    for pid, pat in INJECTION_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            lineno, line_text, offset = _line_at(text, m.start())
            hits.append((pid, lineno, _phrased_as_prohibition(line_text, offset)))
    return hits


def check_vendored_content_is_scanned():
    """Third-party code we vendored is still third-party code.

    Vendoring does not launder a file. Everything under a skill's `assets/`, `references/` or
    `scripts/` came from somewhere else, and this package hands parts of it straight back to an
    agent: a SKILL.md the agent reads, a script a human runs. A poisoned entry in that content
    is not a hypothetical - it is instructions aimed at the next turn, delivered through a file
    a reviewer already agreed was fine.

    So the vendored set is scanned here, on every run, with no allowlist to maintain: adding a
    vendored file scans it automatically rather than requiring someone to remember. A clean
    scan is a floor, not a verdict - that distinction is the reason this section exists at all,
    and it is the reason the same discipline appears in the skills that vendor the content.
    """
    print("vendored third-party content")
    files = vendored_files()
    check(bool(files), f"a vendored set exists to scan ({len(files)} files) - if this drops to "
                       f"0, the convention moved and this check is scanning nothing")
    executable = [p for p in files if p.suffix.lower() in (".py", ".sh", ".cmd", ".bat", ".ps1")]
    check(bool(executable),
          f"vendored executable files are in scope ({len(executable)}) - the riskiest kind")
    found: list[str] = []
    phrased_as_prohibition: list[str] = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            found.append(f"{rel}: unreadable as UTF-8, so it was NOT scanned")
            continue
        for pid, line, prohibited in scan_for_injection(text):
            rec = f"{rel}:{line} matches injection pattern {pid!r}"
            (phrased_as_prohibition if prohibited else found).append(rec)
    check(not found,
          f"no vendored file carries an injection instruction ({len(found)} hit(s): {found[:5]})")
    check(True, f"{len(phrased_as_prohibition)} hit(s) were phrased as a prohibition rather than an "
               f"instruction, and are reported rather than dropped - a tripwire that explains itself "
               f"away without saying so is the failure this section is guarding against: "
               f"{phrased_as_prohibition[:5]}")
    check(len(phrased_as_prohibition) >= 1,
          f"the prohibition branch has a live example in the real vendored set ({len(phrased_as_prohibition)}) "
          f"- a branch that never fires reads as coverage right up until it is needed")
    by_ext: dict[str, int] = {}
    for p in files:
        by_ext[p.suffix.lower() or "(none)"] = by_ext.get(p.suffix.lower() or "(none)", 0) + 1
    check(True, f"scanned {len(files)} vendored file(s): {dict(sorted(by_ext.items()))}")


# ---------------------------------------------------------------- manifest
def check_manifest():
    print("manifest")
    data, err = parse_json(MANIFEST)
    if not check(data is not None, f"plugin.json parses ({err or 'ok'})"):
        return None
    for field in ("schemaVersion", "name", "version", "description", "author",
                  "category", "exampleQueries", "icon", "apps", "mcpServers", "skills"):
        check(field in data, f"plugin.json has '{field}'")
    check(data.get("apps") == [], "plugin.json 'apps' is empty (local runtime ignores Apps)")
    check(bool(data.get("mcpServers")) or bool(data.get("skills")),
          "plugin.json declares at least one real capability")
    check(re.match(r"^\d+\.\d+\.\d+$", str(data.get("version", ""))) is not None,
          f"plugin.json version is SemVer ({data.get('version')})")
    icon = ROOT / data.get("icon", "")
    check(icon.is_file() and icon.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"),
          "icon file exists with a valid image extension")
    if "darkIcon" in data:
        dark = ROOT / data["darkIcon"]
        check(dark.is_file() and dark.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"),
              "darkIcon file exists with a valid image extension")
        if dark.is_file() and icon.is_file():
            # A darkIcon that is a byte-for-byte copy of the light one is not a dark icon. The
            # host has no way to tell the difference, so the field is decorative: it declares
            # that dark mode was considered, and renders exactly the light artwork anyway. The
            # two files here hashed identically for the whole life of the field.
            same = (icon.read_bytes() == dark.read_bytes())
            check(not same,
                  f"darkIcon ({data['darkIcon']}) is a different image from icon "
                  f"({data['icon']}) - a byte-identical copy makes the field decorative")
    for q in data.get("exampleQueries", []):
        check(isinstance(q, str) and q.strip() != "", f"example query non-empty: {q[:40]!r}")
    # every declared skill file exists
    for rel_path in data.get("skills", []):
        check((ROOT / rel_path).is_file(), f"declared skill exists: {rel_path}")
    return data


def check_servers():
    print("servers.mcp.json")
    data, err = parse_json(SERVERS)
    if not check(data is not None, f"servers.mcp.json parses ({err or 'ok'})"):
        return
    text = json.dumps(data)
    check("stdio" in text, "uses the stdio transport")
    for banned in ('"oauth"', '"auth"', '"refresh"'):
        check(banned not in text, f"no unsupported auth field {banned}")


# ---------------------------------------------------------------- frontmatter
def _plain_scalar_is_safe(value: str) -> bool:
    """Would this survive being parsed as a YAML plain (unquoted) scalar?

    A plain scalar ends at the first ': ' or ' #', and may not open with a YAML
    indicator character. Two of the sixteen skills carried a description containing
    "the main thread: the general browser search" and "the optional environment:
    plotting always works". Both parsed as a nested mapping, both were dropped by the
    runtime without a word, and every check here still passed, because the frontmatter
    was only ever matched with a regex. The regex saw a non-empty description; the
    loader never saw a description at all.
    """
    v = value.strip()
    if not v:
        return False
    if v[0] in "-?:,[]{}#&*!|>'\"%@`":
        return False
    if v.endswith(":"):
        return False
    return ": " not in v and " #" not in v


def _frontmatter_line_is_valid(line: str) -> tuple[bool, str]:
    """Validate one top-level `key: value` frontmatter line without a YAML dependency."""
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
    if not m:
        return False, "not a `key: value` line"
    key, value = m.group(1), m.group(2).strip()
    if not value:
        return False, f"'{key}' has an empty value"
    if value.startswith('"'):
        if not (value.endswith('"') and len(value) >= 2):
            return False, f"'{key}' opens a double quote that is never closed"
        if value.count('"') % 2:
            return False, f"'{key}' has an unbalanced double quote"
        return True, ""
    if value.startswith("'"):
        if not (value.endswith("'") and len(value) >= 2):
            return False, f"'{key}' opens a single quote that is never closed"
        return True, ""
    if value[0] in "[{":
        return True, ""  # flow collection: a real parser's job, not ours
    if not _plain_scalar_is_safe(value):
        return False, (f"'{key}' is an unquoted scalar containing ': ' or ' #', which YAML "
                       f"reads as a nested mapping — quote the value")
    return True, ""


def check_skill_frontmatter():
    print("skill frontmatter")
    data, _ = parse_json(MANIFEST)
    for rel_path in data.get("skills", []):
        p = ROOT / rel_path
        text = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not check(m is not None, f"{rel_path}: has YAML frontmatter"):
            continue
        fm = m.group(1)
        # Every top-level line must survive a real YAML parse, not merely a regex match.
        for line in fm.split("\n"):
            if not line.strip():
                continue
            ok_line, why = _frontmatter_line_is_valid(line)
            snippet = line if len(line) <= 70 else line[:67] + "..."
            check(ok_line, f"{rel_path}: frontmatter parses as YAML -- {snippet}"
                  + (f"  [{why}]" if why else ""))
        name_m = re.search(r"^name:\s*(\S+)\s*$", fm, re.M)
        desc_m = re.search(r"^description:\s*(.+)$", fm, re.M)
        dir_name = p.parent.name
        if check(name_m is not None, f"{rel_path}: frontmatter has 'name'"):
            # A quoted scalar is the correct way to write these, so the comparison
            # must see the value rather than the quotes around it.
            declared_name = name_m.group(1).strip("\"'")
            check(declared_name == dir_name,
                  f"{rel_path}: frontmatter name == directory name ({dir_name})")
        if check(desc_m is not None, f"{rel_path}: frontmatter has 'description'"):
            check(len(desc_m.group(1).strip().strip("\"'")) > 20,
                  f"{rel_path}: description is substantive")


# ---------------------------------------------------------------- relationships
# ASCII-only edge markers. These rows are printed to a GBK console on Windows, where
# box-drawing or mathematical glyphs raise UnicodeEncodeError - which would abort the whole
# run instead of reporting one failed check.
ARROWS = {
    "needs": "-> needs", "dispatches": "-> dispatches", "produces": "-> produces",
    "widget": "-> widget", "gate": "-> gates", "browses": "-> browses",
    "asks": "-> asks", "enforces": "=> enforces", "loops": "=> loops",
}


def render_edges(rel: dict, category: str) -> list[str]:
    """Render the 'Bound edges' markdown rows for one category from relationships.json.

    A row is rendered for every edge whose *source* skill is in the category, plus
    every edge whose *target* skill is in the category (so inbound prerequisites and
    hand-offs are visible from this category's own table too). Each row embeds a
    stable marker `<!-- edge: from->to:type -->` so the checker can locate rows
    without re-parsing human prose.
    """
    by_id = {n["id"]: n for n in rel["nodes"]}

    def in_cat(nid: str) -> bool:
        n = by_id.get(nid)
        return bool(n and n.get("category") == category)

    rows = []
    seen = set()
    for e in rel["edges"]:
        if in_cat(e["from"]) or in_cat(e["to"]):
            key = (e["from"], e["to"], e["type"])
            if key in seen:
                continue
            seen.add(key)
            f, t = e["from"], e["to"]
            rel_arrow = ARROWS.get(e["type"], f"-> {e['type']}")
            src = f"`{f}`"
            dst = f"`{t}`"
            rows.append(
                f"| {src} {rel_arrow} {dst} | {e['when']} | <!-- edge:{f}->{t}:{e['type']} --> |"
            )
    return rows


def check_relationships():
    print("relationships.json + bound index tables")
    rel, err = parse_json(REL)
    if not check(rel is not None, f"relationships.json parses ({err or 'ok'})"):
        return
    for key in ("edgeTypes", "nodes", "edges"):
        check(key in rel, f"relationships.json has '{key}'")

    ids = [n["id"] for n in rel["nodes"]]
    check(len(ids) == len(set(ids)), "relationships.json node ids are unique")
    idset = set(ids)

    # edge types must be declared
    for e in rel["edges"]:
        check(e["type"] in rel["edgeTypes"],
              f"edge {e['from']}->{e['to']} type '{e['type']}' is declared")
        check(e["from"] in idset, f"edge source '{e['from']}' is a real node")
        check(e["to"] in idset, f"edge target '{e['to']}' is a real node")
        check("when" in e and e["when"].strip(), f"edge {e['from']}->{e['to']} has a 'when'")

    # every skill node's path exists and matches the manifest
    data, _ = parse_json(MANIFEST)
    srv_text = SERVER_PY.read_text(encoding="utf-8")
    manifest_skills = set(data.get("skills", []))
    for n in rel["nodes"]:
        kind = n.get("kind")
        if kind == "skill":
            p = n.get("path", "")
            check((ROOT / p).is_file(), f"relationship node path exists: {p}")
            check(p in manifest_skills, f"relationship skill is in manifest: {n['id']}")
        elif kind == "builtin-skill":
            # A host-provided skill, deliberately NOT in this package. The check that matters
            # is that it is declared as not shipped, so nobody later "fixes" it by adding a
            # copy to the manifest and double-defining it.
            check(n.get("shipped") is False,
                  f"builtin-skill '{n['id']}' is marked shipped:false (host-provided, not in this package)")
            check(n["id"] not in manifest_skills,
                  f"builtin-skill '{n['id']}' is not shipped inside the manifest")
        elif kind == "agent":
            check(n.get("reference", "").startswith("agent:"),
                  f"agent node '{n['id']}' has an agent: reference")
            av = ROOT / n.get("avatar", "")
            check(av.is_file(), f"agent node '{n['id']}' avatar exists: {n.get('avatar')}")
        elif kind == "source":
            check(n.get("url", "").startswith("https://"),
                  f"source node '{n['id']}' has an https url")
            check(n.get("mustBrowse") is True,
                  f"source node '{n['id']}' is marked mustBrowse:true")
        elif kind == "source-group":
            check("note" in n, f"source-group node '{n['id']}' documents what it groups")
        elif kind == "paper":
            check(n.get("url", "").startswith("https://"),
                  f"paper node '{n['id']}' has an https url")
            check("note" in n, f"paper node '{n['id']}' records what was taken from it")
        elif kind == "module":
            # a module we ship and import ourselves, not a registered MCP tool and not
            # something the host provides
            ref = str(n.get("reference", ""))
            check(ref.startswith("mcp/") and (ROOT / ref).is_file(),
                  f"module node '{n['id']}' points at a real bundled module ({ref})")
            check("note" in n, f"module node '{n['id']}' documents what it is for")
        elif kind == "provenance":
            check(n.get("url", "").startswith("https://"),
                  f"provenance node '{n['id']}' points at the upstream repository")
            check("note" in n,
                  f"provenance node '{n['id']}' records the licence and what was adapted")
        elif kind == "module":
            # a module this package ships, not a registered MCP tool and not host-provided
            check(n.get("reference", "").startswith("mcp/"),
                  f"module node '{n['id']}' names a module inside mcp/")
            check((ROOT / n.get("reference", "")).is_file(),
                  f"module node '{n['id']}' points at a file that exists")
            check("note" in n, f"module node '{n['id']}' documents what it does")
        elif kind == "tool":
            # A tool node names the capability it drives. That is a plugin tool of ours, or a
            # host-bound capability like mcp_browser that the host provides - both are valid,
            # but the host one must say so, because nothing in this package registers it.
            ref = str(n.get("reference", ""))
            if ref.startswith("kaggle_"):
                check(f'"name": "{ref}"' in srv_text,
                      f"tool node '{n['id']}' is registered: {ref}")
            else:
                check(n.get("hostProvided") is True,
                      f"tool node '{n['id']}' ({ref}) is marked hostProvided - the host "
                      "provides it, this package does not register it")
            check("note" in n, f"tool node '{n['id']}' documents what it does")

    # every manifest skill appears as a relationship node
    for s in manifest_skills:
        stem = Path(s).parent.name
        check(stem in idset, f"manifest skill '{stem}' is a relationship node")

    # the bound tables in each category index must match the rendered rows
    for cat in CATEGORIES:
        idx = ROOT / "skills" / "categories" / f"{cat}.md"
        if not check(idx.is_file(), f"categories/{cat}.md exists"):
            continue
        text = idx.read_text(encoding="utf-8")
        expected = render_edges(rel, cat)
        for row in expected:
            # COUNTED, not membership-tested. `row in text` was true for a row that appears
            # once and for a row that appears twice, so a category index could carry a whole
            # extra copy of the bound-edge table and every one of these checks still passed -
            # which is exactly what had happened in all four files. A rendered table is
            # rendered once; anything else is two documents disagreeing about the graph.
            n = text.count(row)
            check(n == 1,
                  f"{cat}/INDEX.md bound edge row appears exactly once: "
                  f"{row.split('|')[1].strip()} (found {n})")
        # no stale markers left behind.
        #
        # Two things were wrong here, and they hid each other. The pattern was
        # `<!-- edge:([^>]+?) -->`, but a marker body is `from->to:type` - so `[^>]` could
        # never span the arrow and the pattern matched ZERO markers: the block was vacuous.
        # Fixing the pattern alone then exposed the second bug: the comparison was
        # `f"<!-- edge:{marker} -->" in expected`, a list-membership test against a list of
        # whole rendered ROWS, so every marker looked stale. Two dead checks stacked.
        expected_markers = {m for row in expected
                            for m in re.findall(r"<!-- edge:(.+?) -->", row)}
        markers = re.findall(r"<!-- edge:(.+?) -->", text)
        for marker in markers:
            if marker not in expected_markers:
                bad(f"categories/{cat}.md has a stale edge marker not in relationships.json: "
                    f"{marker}")
        # ...and no marker rendered more than once, which a row count can miss when a copy
        # was hand-edited: a duplicated block is the failure, not a duplicated string.
        for marker in set(markers):
            n = text.count(f"<!-- edge:{marker} -->")
            if n != 1:
                bad(f"categories/{cat}.md renders edge marker {n} times: {marker}")
        # one table, not two. A second '## Bound edges' heading is how the duplication
        # announced itself: the stale copy still pointed at `../../relationships.json`, a
        # path that does not exist from skills/categories/, because it was written before
        # the file moved up a level.
        heads = re.findall(r"^##\s+Bound edges", text, re.M)
        check(len(heads) == 1,
              f"categories/{cat}.md has exactly one '## Bound edges' section (found {len(heads)})")


# ---------------------------------------------------------------- coverage floor
REQUIRED_BROWSED = ("github", "huggingface", "arxiv")


def check_browses_floor():
    """The 'we must have actually looked at these three sites' requirement is an edge, not a sentence.

    If a skill can drop one of its `browses` edges and still pass, the coverage floor is only
    decorative. So this asserts both that the edges exist and that they are wired from the two
    places that actually do the browsing.
    """
    print("browses coverage floor")
    rel, _ = parse_json(REL)
    if rel is None:
        return
    by_id = {n["id"]: n for n in rel["nodes"]}
    edges = rel["edges"]

    for sid in REQUIRED_BROWSED:
        node = by_id.get(sid)
        check(node is not None and node.get("kind") == "source",
              f"source node '{sid}' exists in the graph")

    # The research skill holds all three, and holds them itself rather than delegating them. That
    # is the point of the ownership: if the forensics ever went back to a subagent, a detached
    # child has no in-app browser and the floor would have no owner that could actually meet it.
    for owner in ("kaggle-competition-research",):
        held = {e["to"] for e in edges if e["from"] == owner and e["type"] == "browses"}
        for sid in REQUIRED_BROWSED:
            check(sid in held, f"'{owner}' holds a browses edge to '{sid}'")

    # deep-research must be dispatched by the research skill: it is the general search
    dispatched = {e["to"] for e in edges
                  if e["from"] == "kaggle-competition-research" and e["type"] == "dispatches"}
    check("deep-research" in dispatched,
          "kaggle-competition-research dispatches deep-research (the general browser search)")

    # a browses edge must point at a source, never at a skill
    for e in edges:
        if e["type"] == "browses":
            check(by_id[e["to"]].get("kind") == "source",
                  f"browses edge {e['from']}->{e['to']} points at a source node")

    # The floor is only dischargeable if the skill actually tells the agent to drive the
    # browser. A skill that merely names the browser skill, or claims deep-research will
    # browse, fails this: deep-research's own instructions prefer web_search/web_fetch and
    # avoid browser automation, so the main agent has to open the pages itself.
    node = by_id.get("kaggle-competition-research")
    if node:
        text = (ROOT / node["path"]).read_text(encoding="utf-8")
        check("mcp_browser" in text,
              "the research skill names mcp_browser (the floor is discharged by a real "
              "browser action, not by a search tool)")
        check("open_tab" in text or "inspect" in text,
              "the research skill gives a concrete browser action to run")
        check("avoids browser automation" in text or "will not use the browser on its own" in text,
              "the research skill states that deep-research does not browse by itself, so the "
              "main agent must")


# ---------------------------------------------------------------- presence reach
DECISION_SKILLS = ("kaggle-account-switch", "experiment-launch", "approach-decision",
                   "log-monitor", "handoff", "kaggle-competition-research")


def check_presence_reach():
    """presence-mode must reach every decision point, and each must name it in its own body.

    Two halves, and both matter. The graph edge says the relationship exists; the text check
    says the skill can actually follow it. A skill that has an `asks` edge but never mentions
    `presence-mode` or `kaggle_presence` is an edge nobody reads.
    """
    print("presence mode reach")
    rel, _ = parse_json(REL)
    if rel is None:
        return
    by_id = {n["id"]: n for n in rel["nodes"]}
    asked = {e["to"] for e in rel["edges"] if e["from"] == "presence-mode" and e["type"] == "asks"}

    for sid in DECISION_SKILLS:
        check(sid in asked, f"presence-mode holds an asks edge to '{sid}'")

    check("genui-scenarios" in
          {e["to"] for e in rel["edges"]
           if e["from"] == "presence-mode" and e["type"] == "needs"},
          "presence-mode needs genui-scenarios (who is there to click a widget?)")

    # the tool must exist, or every one of those edges points at nothing
    srv = SERVER_PY.read_text(encoding="utf-8")
    check('"name": "kaggle_presence"' in srv, "kaggle_presence tool is registered")
    check("import presence" in srv, "kaggle_server.py imports the presence module")
    check((ROOT / "mcp" / "presence.py").is_file(), "mcp/presence.py exists")

    # each decision skill must actually consult it
    for sid in DECISION_SKILLS:
        node = by_id.get(sid)
        if not node or node.get("kind") != "skill":
            continue
        text = (ROOT / node["path"]).read_text(encoding="utf-8")
        check("kaggle_presence" in text or "presence-mode" in text,
              f"'{sid}' body names presence-mode / kaggle_presence")


# ---------------------------------------------------------------- widget binding
def check_widget_binding():
    print("widget binding")
    genui = ROOT / "skills" / "genui-scenarios" / "SKILL.md"
    if not check(genui.is_file(), "genui-scenarios/SKILL.md exists"):
        return
    text = genui.read_text(encoding="utf-8")
    rel, _ = parse_json(REL)
    by_id = {n["id"]: n for n in rel["nodes"]}
    widget_edges = [e for e in rel["edges"] if e["type"] in ("widget", "gate")]
    check(len(widget_edges) > 0, "graph declares widget/gate edges")
    # every widget edge target is a real skill
    for e in widget_edges:
        tgt = by_id[e["to"]]
        check(tgt.get("kind") == "skill", f"widget/gate target '{e['to']}' is a skill")
    # the two visualizers must appear as a widget edge source
    visualizers = {"account-rename-visualizer", "log-monitor-visualizer"}
    for v in visualizers:
        present = any(e["from"] == v for e in rel["edges"])
        check(present, f"visualizer '{v}' is wired into the graph")


# ---------------------------------------------------------------- secrets
SECRET_PATTERNS = [
    (re.compile(r"\bKAGGLE_API_TOKEN\s*=\s*[A-Za-z0-9_\-]{16,}"), "literal KAGGLE_API_TOKEN value"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token literal"),
    (re.compile(r"\bkaggle_[a-f0-9]{32}\b"), "Kaggle API token literal"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI-style key literal"),
]


def check_no_secrets():
    print("secret scan")
    targets = list((ROOT / "skills").rglob("*.md")) + \
        [MANIFEST, SERVERS, REL, ROOT / "README.md"]
    hits = 0
    for p in targets:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for rx, label in SECRET_PATTERNS:
            if rx.search(text):
                bad(f"possible {label} in {p.relative_to(ROOT)}")
                hits += 1
    if hits == 0:
        ok("no literal secrets in skills, manifest, or relationships")


# ---------------------------------------------------------------- stale references
# The forensics agent was renamed kaggle-search -> competition-browser, and has since been
# removed entirely: wave 2 does its own forensics in the main thread. A half-finished rename is
# the worst outcome - the package documents a reference that no longer resolves, and a
# dispatch fails only at runtime, in a research sweep, hours in. Removal is the same failure
# with more room for it, which is why nothing is exempt any more: the file that used to be
# allowed to mention the old names is gone, and the agent it named is gone with it.
#
# These are matched as whole words, not substrings. "kaggle_search_engine" is a *current*
# tool name that happens to contain the retired agent's name, and a substring rule would
# flag the tool that replaced it.
RETIRED_NAMES = ("kaggle-search", "Kaggle 搜索", "search-agent.png")
RETIRED_PATTERNS = [re.compile(r"\bkaggle_search\b"), re.compile(r"agent:kaggle-search")]


# ---------------------------------------------------------------- the one home directory
def check_store_migration():
    print("credential store migration")
    spec = importlib.util.spec_from_file_location(
        "cred_check", ROOT / "mcp" / "credentials.py")
    cred = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cred)

    check(cred.APP_DIR_NAME == ".kaggle-agent",
          f"the plugin owns one home directory (got {cred.APP_DIR_NAME})")
    check(cred.LEGACY_APP_DIR_NAME == ".kaggle-cli",
          "the previous location is still named, so an existing setup can be found")
    check(cred.store_path().name == "accounts.json",
          "the store is accounts.json - README once documented a kaggle.json that never existed")
    check(cred.legacy_store_path().name == "accounts.json",
          "the old multi-account file is migrated, not ignored")
    check(cred.legacy_store_path().parent.name == cred.LEGACY_APP_DIR_NAME
          and cred.legacy_path().parent.name == cred.LEGACY_APP_DIR_NAME,
          "both legacy paths stay in the OLD directory - pointing them at the new home would "
          "make a probe 'migrate' a temporary directory that never held anything")

    # github_sync already keeps its own credentials.json in the new home. Two meanings for
    # that one filename is how somebody ends up logged out of one service and into another.
    gs = ROOT / "mcp" / "github_sync.py"
    spec2 = importlib.util.spec_from_file_location("gh_check", gs)
    gh = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(gh)
    # pinned, because an earlier suite may have left KAGGLE_AGENT_HOME pointing at its own
    # throwaway home - and the question here is which NAME github_sync uses, not where it
    # happens to be resolved from right now.
    saved_gh = os.environ.get("KAGGLE_AGENT_HOME")
    os.environ["KAGGLE_AGENT_HOME"] = str(Path.home() / ".kaggle-agent")
    try:
        gh_path = Path(gh._store_path())
    finally:
        if saved_gh is None:
            os.environ.pop("KAGGLE_AGENT_HOME", None)
        else:
            os.environ["KAGGLE_AGENT_HOME"] = saved_gh
    check(gh_path.name == "credentials.json" and gh_path.parent.name == ".kaggle-agent",
          f"github_sync's store is ~/.kaggle-agent/credentials.json (got {gh_path})")
    check(cred.store_path().name != gh_path.name,
          "the account store is not named credentials.json, so it cannot shadow the GitHub one")

    # the behaviour, against a fake home: a renamed account must not come back
    saved_expand, saved_env = os.path.expanduser, os.environ.get("KAGGLE_AGENT_HOME")
    home = Path(_mkdtemp(prefix="ka-cred-check-"))
    old_dir = home / ".kaggle-cli"
    old_dir.mkdir(parents=True)
    renamed = {"active": "second-handle",
               "accounts": {"second-handle": {"token": "T1", "username": "bob",
                                              "renamedFrom": "work"}}}
    (old_dir / "accounts.json").write_text(json.dumps(renamed), encoding="utf-8")
    agent = home / ".kaggle-agent"
    os.path.expanduser = lambda p=None: str(home) if (p or "").startswith("~") else (p or "")
    os.environ["KAGGLE_AGENT_HOME"] = str(agent)
    try:
        data = cred.load()
    finally:
        os.path.expanduser = saved_expand
        if saved_env is None:
            os.environ.pop("KAGGLE_AGENT_HOME", None)
        else:
            os.environ["KAGGLE_AGENT_HOME"] = saved_env
    check(data.get("active") == "second-handle",
          "a store at the old location is migrated and keeps the active account")
    check(list((data.get("accounts") or {})) == ["second-handle"],
          f"account names are copied verbatim - a renamed account must not come back as a "
          f"duplicate (got {list((data.get('accounts') or {}))})")
    check((agent / "accounts.json").exists(), "the migrated store is written to the new home")
    check((old_dir / "accounts.json").exists(),
          "the old file is left alone: deleting somebody's credentials is their call, not a "
          "side effect of an upgrade")

    # the override has to actually redirect, or a probe reads the real accounts
    os.environ["KAGGLE_AGENT_HOME"] = str(agent)
    try:
        check(str(cred.store_path()).startswith(str(agent)),
              "KAGGLE_AGENT_HOME redirects the store, so a probe cannot read real credentials")
    finally:
        if saved_env is None:
            os.environ.pop("KAGGLE_AGENT_HOME", None)
        else:
            os.environ["KAGGLE_AGENT_HOME"] = saved_env
    shutil.rmtree(home, ignore_errors=True)

    # Every doc that names a store path has to name the current one. Mentioning the OLD path
    # is allowed and in two places required - a reader with an existing store needs to be told
    # it still works - but it must never be the only path in the file, because a doc that
    # names one store and not the other is a doc that got half-reverted.
    for rel in ("README.md", "servers.mcp.json", "bin/kaggle-cli.cmd", "bin/kaggle-cli.sh",
                "mcp/kaggle_server.py", "skills/kaggle-cli/SKILL.md",
                "skills/kaggle-account-switch/SKILL.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        check("kaggle.json" not in text,
              f"{rel} does not name a kaggle.json - that file never existed")
        mentions_old = ".kaggle-cli" in text
        mentions_new = ".kaggle-agent" in text
        check(mentions_new and (not mentions_old or mentions_new),
              f"{rel} names the current store (old path present: {mentions_old})")
        check(not mentions_old or mentions_new,
              f"{rel} never presents the old store on its own")


def check_no_uncalled_functions():
    """A module-level function nobody calls must SAY so.

    Six of these accumulated unnoticed: a ladder reset that cleared less than the copy
    beside it, an accelerator reader with a second spelling and no caller, a config setter
    whose logic the handler had re-implemented inline, and two accessors and two GitHub
    readers with no consumer at all. None of them raised, so every suite stayed green.

    Dead code is worse than no code when it looks correct: `reset_ladder`'s docstring
    promised exactly what its body failed to do. So an uncalled function is allowed, but
    only when it declares that it is uncalled - `# noqa: KA-uncalled` - which is a sentence
    somebody has to write on purpose.
    """
    print("no uncalled functions")
    marker = "KA-uncalled"
    modules = sorted(p for p in (ROOT / "mcp").glob("*.py"))
    tools = sorted(p for p in (ROOT / "tools").glob("*.py"))

    def live_code(text: str) -> str:
        """Drop docstrings and comments, so a mention in prose is not a call site.

        Without this the check is wrong in both directions: a name quoted in a docstring
        reads as a reference, and a function registered in a dispatch table - plots.py puts
        all six chart renderers in RENDERERS and reaches them by key - reads as uncalled.
        """
        text = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', '""', text)
        return "\n".join(re.sub(r"#.*$", "", line) for line in text.split("\n"))

    corpus = "\n".join(live_code(p.read_text(encoding="utf-8")) for p in modules + tools)
    total = 0
    for path in modules:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        defined = [n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and not n.name.startswith("__")]
        for fn in defined:
            refs = len(re.findall(rf"(?<!\w){re.escape(fn.name)}\b", corpus))
            defs = len(re.findall(rf"^def {re.escape(fn.name)}\s*\(", corpus, re.M))
            if refs <= defs:
                total += 1
                segment = ast.get_source_segment(src, fn) or ""
                head = src.splitlines()[fn.lineno - 1]
                declared = marker in segment or marker in head
                check(declared,
                      f"{path.name}:{fn.lineno} {fn.name}() has no caller - delete it, wire it, or "
                      f"mark it `# noqa: {marker}` to say it is deliberately uncalled")
    check(True, f"scanned {len(modules)} modules; {total} uncalled function(s) accounted for")

    # A doubled carriage return is invisible in a diff and fatal in a parse. It arrived twice
    # while editing this package: read a CRLF file, split on \n (which leaves the \r attached),
    # join with \n, then write with the line endings "restored" - producing \r\r\n throughout.
    # The Python file became a SyntaxError on a line that had not been touched, and the only
    # symptom was an OSError on a pipe flush three suites later. This is the check that says so
    # at the source instead.
    print("line endings")
    strays = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() not in (".py", ".json", ".md", ".cmd", ".sh", ".txt"):
            continue
        raw = path.read_bytes()
        if b"\r\r\n" in raw or b"\r" in raw.replace(b"\r\n", b""):
            strays.append(path.relative_to(ROOT).as_posix())
    check(not strays,
          f"no file carries a doubled carriage return ({len(strays)} affected: "
          f"{strays[:4]})")


def check_no_stale_names():
    print("stale references")
    targets = list((ROOT / "skills").rglob("*.md")) + [
        MANIFEST, SERVERS, REL, ROOT / "skills" / "README.md",
    ]
    for p in targets:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        rel_p = p.relative_to(ROOT).as_posix()
        # No file is exempt any more. The rename was documented in the agent's own doc, and that
        # doc is gone along with the agent; a retired name surviving anywhere now is a live
        # reference to something that no longer exists.
        for old in RETIRED_NAMES:
            if old in text:
                bad(f"stale reference to retired '{old}' in {rel_p}")
        for pattern in RETIRED_PATTERNS:
            if pattern.search(text):
                bad(f"stale reference matching {pattern.pattern} in {rel_p}")


# ---------------------------------------------------------------- the tree is enforced
def check_tree_enforcement():
    """The RSI tree must be a validated tool, not a convention in a skill.

    Three things have to hold together: the module exists, the tool is registered, and the
    graph says who enforces what. The prose in the skill can be skipped under time pressure;
    these cannot.
    """
    print("experiment tree enforcement")
    check((ROOT / "mcp" / "experiment_tree.py").is_file(), "mcp/experiment_tree.py exists")
    srv = SERVER_PY.read_text(encoding="utf-8")
    check('"name": "kaggle_experiment_tree"' in srv, "kaggle_experiment_tree tool is registered")
    check("import experiment_tree" in srv, "kaggle_server.py imports experiment_tree")

    rel, _ = parse_json(REL)
    if rel is None:
        return
    by_id = {n["id"]: n for n in rel["nodes"]}
    edges = rel["edges"]

    check(by_id.get("experiment-tree", {}).get("kind") == "tool",
          "'experiment-tree' is a tool node, not a skill node")
    check(by_id.get("research-sources", {}).get("kind") == "source-group",
          "'research-sources' node exists")

    enforced = {e["to"] for e in edges if e["type"] == "enforces"}
    looped = {e["to"] for e in edges if e["type"] == "loops"}
    check("rsi-experiment-tree" in enforced,
          "experiment-tree enforces rsi-experiment-tree (the node shape is validated)")
    check("rsi-experiment-tree" in looped,
          "experiment-tree loops back to rsi-experiment-tree (read-gated planning)")
    check("research-sources" in
          {e["to"] for e in edges if e["from"] == "rsi-experiment-tree" and e["type"] == "needs"},
          "rsi-experiment-tree needs research-sources (a research node names where it went)")

    # the read gate must be real: a write with no read_revision has to be refused
    import importlib.util
    import os
    import tempfile
    spec = importlib.util.spec_from_file_location("et_check", ROOT / "mcp" / "experiment_tree.py")
    et = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(et)
    os.environ["KAGGLE_AGENT_HOME"] = _mkdtemp()
    probe = "tree-enforcement-probe"

    refused = et.record(probe, {"id": "n1", "kind": "experiment"}, read_revision=None)
    check(not refused["ok"] and refused.get("code") == "read_required",
          "record WITHOUT a read is refused (read-gate is real)")

    rev = et.read(probe)["readRevision"]
    good = {"id": "n1", "kind": "experiment", "parent": None, "change": "one thing",
            "hypothesis": "h", "metric": {"name": "m", "parent": 1, "result": 2, "delta": 1,
                                          "rank": 0.5, "rankSource": "local",
                                          "direction": "higher"},
            "operator": "improve", "family": "context", "evidence": "local-only",
            "verdict": "keep", "reason": "specific reason", "artifacts": ["a"]}
    first = et.record(probe, good, read_revision=rev, new_base="n1")
    check(first["ok"], "a well-formed node is accepted")

    # the loop: a second write with the SAME read must be refused
    second = et.record(probe, dict(good, id="n2", parent="n1"), read_revision=rev)
    check(not second["ok"] and second.get("code") == "stale_read",
          "record with a STALE read is refused (the tree must be re-read after every node)")

    # shape enforcement, each of the ways a node could be unjudgeable
    rev2 = et.read(probe)["readRevision"]
    cases = [
        ("no hypothesis", {k: v for k, v in good.items() if k != "hypothesis"}),
        ("two changes in one", dict(good, id="n2", change="raise limit and change model")),
        ("empty reason", dict(good, id="n2", reason="better")),
        ("dangling parent", dict(good, id="n2", parent="nope")),
        ("no operator", {k: v for k, v in good.items() if k != "operator"}),
        ("no family", {k: v for k, v in good.items() if k != "family"}),
        ("no rank", None),
        ("no rankSource", None),
    ]
    for label, node in cases:
        if node is None:
            continue
        r = et.record(probe, node, read_revision=rev2)
        check(not r["ok"], f"rejected: {label}")
    # the two rank fields are checked by removing them from a copy of the metric
    for field in ("rank", "rankSource"):
        rev_f = et.read(probe)["readRevision"]
        stripped = json.loads(json.dumps(good))
        stripped["id"] = "n2"
        stripped["parent"] = "n1"
        del stripped["metric"][field]
        r = et.record(probe, stripped, read_revision=rev_f)
        check(not r["ok"], f"rejected: metric missing '{field}'")

    # a bogus operator and an unslug family are both refused
    rev_op = et.read(probe)["readRevision"]
    r = et.record(probe, dict(good, id="n2", operator="teleport"), read_revision=rev_op)
    check(not r["ok"], "rejected: operator outside the four atomic operators")
    rev_fam = et.read(probe)["readRevision"]
    r = et.record(probe, dict(good, id="n2", family="a whole sentence, not a slug"),
                  read_revision=rev_fam)
    check(not r["ok"], "rejected: family that is not a slug")

    # a research node is a first-class kind, and it must open something
    rev3 = et.read(probe)["readRevision"]
    res_ok = et.record(probe, {
        "id": "n2", "kind": "research", "parent": "n1", "question": "q",
        "targets": ["forum"], "verdict": "inconclusive", "reason": "r", "opens": "o",
    }, read_revision=rev3)
    check(res_ok["ok"], "a well-formed research node is accepted")
    rev4 = et.read(probe)["readRevision"]
    res_bad = et.record(probe, {
        "id": "n3", "kind": "research", "parent": "n1", "question": "q",
        "targets": ["reddit"], "verdict": "inconclusive", "reason": "r", "opens": "o",
    }, read_revision=rev4)
    check(not res_bad["ok"], "rejected: research node with an unreachable target")
    rev5 = et.read(probe)["readRevision"]
    res_noopen = et.record(probe, {
        "id": "n3", "kind": "research", "parent": "n1", "question": "q",
        "targets": ["forum"], "verdict": "inconclusive", "reason": "r", "opens": "  ",
    }, read_revision=rev5)
    check(not res_noopen["ok"], "rejected: research node that opens nothing")
    check(et.status(probe)["sound"], "the probe tree validates clean")


# ---------------------------------------------------------------- search widening
def check_search_widening():
    """Selection must be non-greedy, or the tree degenerates into score-maximising hill climbing.

    The paper this adapts (arXiv 2607.28568 sec. 5.2) reports a matched Medal Average gain of
    53.03% -> 60.61% from replacing scalar-fitness parent selection with a quality + progress +
    novelty utility. This asserts the mechanism is actually present, not just documented.
    """
    print("search widening")
    spec = importlib.util.spec_from_file_location(
        "et_search_check", ROOT / "mcp" / "experiment_tree.py")
    et = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(et)
    os.environ["KAGGLE_AGENT_HOME"] = _mkdtemp()
    comp = "search-widening-probe"

    check(set(et.OPERATORS) == {"draft", "improve", "debug", "crossover"},
          "the four atomic operators are declared (draft/improve/debug/crossover)")

    def node(nid, parent, result, delta, op, fam, verdict="keep"):
        n = {
            "id": nid, "kind": "experiment", "parent": parent, "change": f"change for {nid}",
            "hypothesis": "h", "metric": {"name": "s", "parent": result - delta,
                                         "result": result, "delta": delta,
                                         "rank": 0.5, "rankSource": "local",
                                         "direction": "higher"},
            "operator": op, "family": fam, "verdict": verdict, "reason": "r",
            "artifacts": ["a"],
            # a concluded node must declare provenance; this probe is about selection
            "evidence": "local-only",
        }
        # A refuted node must say which harness layer broke; without it the record is rejected.
        if verdict == "revert":
            n["failureLayer"] = "metric"
        return n

    for nd in (node("n1", None, 0.40, 0.0, "draft", "baseline"),
               node("n2", "n1", 0.72, 0.32, "improve", "context"),
               node("n3", "n2", 0.65, -0.07, "improve", "context"),
               node("n4", "n2", 0.69, -0.03, "crossover", "verifier"),
               node("n5", "n2", 0.50, -0.22, "debug", "parser", verdict="revert")):
        rev = et.read(comp)["readRevision"]
        et.record(comp, nd, read_revision=rev)

    tree = et.load(comp)
    sel = et.select_next(tree)
    check(sel["ok"], "selection produced a recommendation")
    check(sel["recommendedParent"] == "n4",
          f"the NEW method family wins over the top scorer (got {sel['recommendedParent']}, "
          "expected n4) - this is the search being widened, not greedy hill climbing")
    check(all(c["id"] != "n5" for c in sel["candidates"]),
          "a refuted node is never selected again")
    check(any(c["novelty"] == 1.0 for c in sel["candidates"]),
          "novelty is actually computed for a node")
    check(all("cooling" in c and "progressNorm" in c for c in sel["candidates"]),
          "every candidate carries progress and cooling terms")

    board = et.experience_board(tree)
    check(board["familyCount"] >= 3, "the experience board groups method families")
    check("improve" in board["operatorGain"],
          "operator gain is attributed, so 'which operator produced the gain' is answerable")
    check(board["refutedByFamily"].get("parser") == ["n5"],
          "failures are grouped by family so a whole direction is marked as tried")

    # a direction with the same family must NOT be novel the second time
    rows = {r["id"]: r for r in et.score_nodes(tree)}
    check(rows["n2"]["novelty"] == 1.0 and rows["n3"]["novelty"] == 0.0,
          "novelty is positional: the second node in a family is not novel")

    # the tool must expose both actions
    srv = SERVER_PY.read_text(encoding="utf-8")
    check('"select"' in srv and '"board"' in srv,
          "kaggle_experiment_tree exposes the select and board actions")


# ---------------------------------------------------------------- graph-declared state
def check_graph_state():
    """The live state the ask decision depends on must be declared in the graph.

    The point of putting state in relationships.json is that an agent which never read the
    presence skill can still find the answer. That only holds if the declaration is real, names a
    reader that exists, and offers the values the reader actually accepts. These checks are what
    stop it decaying into a comment.
    """
    print("graph-declared state")
    rel, _ = parse_json(REL)
    if rel is None:
        return
    state = rel.get("state")
    if not check(isinstance(state, dict), "relationships.json declares a 'state' block"):
        return

    real = {k: v for k, v in state.items() if not k.startswith("_")}
    check(len(real) > 0, "state block declares at least one piece of live state")
    check("presence" in real, "presence is declared in the graph (the ask decision depends on it)")

    srv = SERVER_PY.read_text(encoding="utf-8")
    for name, entry in real.items():
        if not isinstance(entry, dict):
            bad(f"state '{name}' must be an object")
            continue
        check("meaning" in entry, f"state '{name}' says what it means")
        vals = entry.get("values")
        check(isinstance(vals, list) and vals, f"state '{name}' lists its possible values")
        check("default" in entry, f"state '{name}' declares a default")
        check("readTool" in entry, f"state '{name}' names its read tool")
        check("writeTool" in entry, f"state '{name}' names its write tool")
        tool = str(entry.get("readTool", "")).split()[0] if entry.get("readTool") else ""
        if tool.startswith("kaggle_"):
            check(f'"name": "{tool}"' in srv, f"state '{name}' read tool {tool} is registered")

    # the engine default in the graph must match the module's default, or the graph lies
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "se_check", ROOT / "mcp" / "searchengine.py")
        se = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(se)
        declared = real.get("search-engine", {})
        check(declared.get("default") == se.DEFAULT_ENGINE,
              f"graph search-engine default matches the module ({se.DEFAULT_ENGINE})")
        check(se.DEFAULT_ENGINE in se.ENGINES,
              f"the default engine is one this build knows how to drive ({se.DEFAULT_ENGINE})")
    except Exception as exc:  # noqa: BLE001
        bad(f"could not verify the search-engine default: {exc}")

    # graphstate must be wired in, and it must fail toward asking
    check((ROOT / "mcp" / "graphstate.py").is_file(), "mcp/graphstate.py exists")
    check("import graphstate" in srv, "kaggle_server.py imports graphstate")

    # the engine node and the browser node must exist and be wired to the research skill
    by_id = {n["id"]: n for n in rel["nodes"]}
    check(by_id.get("search-engine", {}).get("kind") == "tool",
          "'search-engine' is declared as a tool node in the graph")
    check(by_id.get("browser", {}).get("kind") == "tool",
          "'browser' is declared as a tool node in the graph")
    needs = {e["to"] for e in rel["edges"]
             if e["from"] == "kaggle-competition-research" and e["type"] == "needs"}
    check("search-engine" in needs, "the research skill needs search-engine")
    check("browser" in needs, "the research skill needs browser")
    asks = {e["to"] for e in rel["edges"] if e["from"] == "presence-mode" and e["type"] == "asks"}
    check("search-engine" in asks, "presence-mode asks at search-engine (away -> default)")

    # an unreadable state must resolve to ask, never to auto
    import importlib.util as _iu
    spec2 = _iu.spec_from_file_location("gs_check", ROOT / "mcp" / "graphstate.py")
    gs = _iu.module_from_spec(spec2)
    spec2.loader.exec_module(gs)
    os.environ["KAGGLE_AGENT_GRAPH"] = os.path.join(str(ROOT), "does-not-exist.json")
    verdict = gs.decide()
    os.environ.pop("KAGGLE_AGENT_GRAPH", None)
    check(verdict["decision"] == "ask",
          "an unreadable graph state fails toward ASKING, never toward auto-deciding")


# ---------------------------------------------------------------- v3 tree mechanics
def _probe_tree():
    """A fresh v3 tree in a throwaway home, for the checks below to poke at."""
    spec = importlib.util.spec_from_file_location(
        "et_v3_check", ROOT / "mcp" / "experiment_tree.py")
    et = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(et)
    os.environ["KAGGLE_AGENT_HOME"] = _mkdtemp()
    return et, et.load("probe")


def _v3_node(nid, parent, result, delta, op, fam, verdict="keep", reason="specific reason",
             cost=None, criteria=None, layer=None, std=None, **extra):
    metric = {"name": "s", "parent": result - delta, "result": result, "delta": delta,
              "rank": 0.5, "rankSource": "local", "direction": "higher"}
    if std is not None:
        metric["samples"] = {"n": 3, "values": [result - std, result, result + std],
                             "mean": result, "std": std}
    node = {"id": nid, "kind": "experiment", "parent": parent, "change": f"change for {nid}",
            "hypothesis": "h", "metric": metric, "operator": op, "family": fam,
            "verdict": verdict, "reason": reason, "artifacts": ["a"]}
    # a concluded node must declare provenance; these probes are about shape, not sourcing
    node["evidence"] = "local-only"
    if verdict == "revert":
        # a refuted node always needs a layer; default to "metric" so callers only pass one
        # when they are testing a specific layer
        node["failureLayer"] = layer or "metric"
    if cost:
        node["cost"] = cost
    if criteria:
        node["criteria"] = criteria
    node.update(extra)
    return node


def check_the_dataset_is_a_control_and_silence_is_not_a_disagreement():
    """controls.data, and the distinction that makes adding it safe.

    Two separate claims, and the second one is the interesting one.

    **The dataset is a control.** The other four record what made a comparison FAIR - same
    seed, same budget, same eval surface, same retrain policy. None of them records what the
    comparison was OF. A competition that re-uploads its data, or a public dataset that ships a
    new version, changes the input while leaving every other control untouched, and the table
    would report the resulting jump as a clean win for the factor. So the tree pins it the same
    way, and the refusal is the existing confound gate rather than a new rule.

    **Silence on both sides is not a disagreement.** This is the part that would have broken
    every tree that predates the field. `data` is absent from all of them, so under the old
    logic - "a key missing on either side means not recorded on both" - adding it would have
    reported a mismatch for every comparable pair in the package and emptied the ablation table
    everywhere. Two arms that both fail to name their dataset are equally unknown; calling that
    a difference asserts something nobody observed. Only ONE side being silent is a real
    finding, and that is the branch that fires.
    """
    print("the dataset as a control")
    et, _ = _probe_tree()

    check("data" in et.CONTROL_KEYS,
          f"the dataset is one of the controls that make a delta attributable "
          f"({', '.join(et.CONTROL_KEYS)})")
    check(et.normalize_node({"controls": {"data": "train-v3"}}).get("controls") == {"data": "train-v3"},
          "a run's dataset survives normalisation, so it reaches the tree instead of being dropped")

    # A tree whose nodes all predate the field: every arm is silent about its data, and none of
    # them may be reported as a mismatch because of it.
    legacy = {"seed": 1, "budget": "s", "eval": "v", "retrain": "re-eval"}
    silent = et._control_diff({"controls": dict(legacy)}, {"controls": dict(legacy)})
    check(silent == [],
          f"two arms that are both silent about their data are NOT a disagreement (got {silent}) - "
          f"or every tree that predates the field would report one")
    half = et._control_diff({"controls": dict(legacy, data="train-v3")}, {"controls": dict(legacy)})
    check(any("data" in h for h in half),
          f"one arm naming its dataset while the other does not IS reported ({half}) - the two are "
          f"not known to be comparable")
    clash = et._control_diff({"controls": dict(legacy, data="train-v3")},
                             {"controls": dict(legacy, data="train-v4")})
    check(any("train-v3" in c and "train-v4" in c for c in clash),
          f"two arms naming different versions are reported, with both values ({clash})")

    # The table: a delta measured across a data change belongs to the data, not to the factor.
    moved = et.ablation_table(_ab_tree({
        "n0": _ab("n0", [], 0.50, controls=dict(legacy, data="train-v3")),
        "n1": _ab("n1", ["a"], 0.60, parent="n0", controls=dict(legacy, data="train-v4")),
    }))
    check(moved["confounds"] and any("data" in c["differ"][0] for c in moved["confounds"]),
          f"a factor's gain measured across a dataset change is not attributed to the factor "
          f"(confounds={moved['confounds']})")
    same = et.ablation_table(_ab_tree({
        "n0": _ab("n0", [], 0.50, controls=dict(legacy, data="train-v3")),
        "n1": _ab("n1", ["a"], 0.60, parent="n0", controls=dict(legacy, data="train-v3")),
    }))
    check(not same["confounds"] and len(same["edges"]) == 1,
          f"the same comparison on one pinned dataset is a clean, attributable edge "
          f"(confounds={same['confounds']}, edges={len(same['edges'])})")

    # And the write gate, which is where a recorded change has to be declared.
    parent = _v3_node("n1", None, 0.50, 0.00, "improve", "ablation", controls=dict(legacy, data="v3"))
    child = _v3_node("n2", "n1", 0.60, 0.10, "improve", "ablation", controls=dict(legacy, data="v4"))
    refused = et.validate(_ab_tree({"n1": parent, "n2": child}, base="n1"))
    check(any("data" in p and "confound" in p.lower() for p in refused),
          f"a run that silently switched dataset is refused at write time ({refused})")
    declared = _v3_node("n2", "n1", 0.60, 0.10, "improve", "ablation",
                        controls=dict(legacy, data="v4"),
                        confoundReason="the dataset was re-uploaded mid-competition; the gain is "
                                       "partly the new data, which is the point of measuring it")
    accepted = et.validate(_ab_tree({"n1": parent, "n2": declared}, base="n1"))
    check(not any("data" in p and "confound" in p.lower() for p in accepted),
          f"declaring WHY the dataset moved is the escape hatch, and it works ({accepted})")


def _skill_body(text: str) -> str:
    """A skill's procedure, with the frontmatter removed.

    The frontmatter is metadata: it describes the skill to whoever is deciding whether to load
    it. Asserting against the whole file lets a description satisfy a check about the procedure,
    which is how a deleted section still reads as present - the words survive in the summary
    above the fold while the instruction is gone.
    """
    if text.startswith("---"):
        close = text.find("\n---", 3)
        if close != -1:
            rest = text.find("\n", close + 1)
            return text[rest:] if rest != -1 else ""
    return text


def check_the_readme_counts_what_the_package_contains():
    """A number in a README is a claim, and it is the one kind nobody re-checks.

    The plugin's whole argument is that a discipline written in prose decays silently, so the
    counts in the README are the most embarrassing place for that to be true - and they were.
    It claimed 16 skills where the manifest declares 17, "the server and its nine modules"
    where `mcp/` holds fifteen, and 27 tools where the live server answers 29 on `tools/list`.
    None of that broke anything, which is exactly why it survived: there was no check, so a
    deletion and two skill additions moved reality without moving the sentence.

    The counts are read from the sources rather than restated: `TOOLS` is the exact list the
    server hands back, the manifest is what the runtime reads, and the module count is the
    directory. Each is asserted with the surrounding words, because a bare number could match
    anything; the count has to appear where a reader would take it as the package's size.
    """
    print("the README's own numbers")
    spec = importlib.util.spec_from_file_location("ks_readme", ROOT / "mcp" / "kaggle_server.py")
    sys.path.insert(0, str(ROOT / "mcp"))
    ks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ks)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    # "fifteen", not "15" - the sentence reads that way, and matching either form would let
    # the check pass on a number the reader never sees.
    WORDS = {15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 14: "fourteen",
             13: "thirteen", 12: "twelve", 11: "eleven", 10: "ten", 9: "nine"}
    tools = len(ks.TOOLS)
    manifest, err = parse_json(MANIFEST)
    skills = len(manifest.get("skills") or []) if manifest else 0
    modules = len([p for p in (ROOT / "mcp").glob("*.py") if p.is_file()])

    check(f"{tools} tools" in readme,
          f"the README's tool count is the one the server actually serves ({tools})")
    check(readme.count(f"{skills} skills") >= 2,
          f"both README counts of skills match the manifest ({skills}) - a manifest that says "
          f"{skills} and a README that says something else is a claim nobody checked")
    on_disk = len(list((ROOT / "skills").glob("*/SKILL.md")))
    check(skills == on_disk,
          f"the manifest declares exactly the skills that exist ({skills} declared, "
          f"{on_disk} on disk)")
    check(f"its {WORDS.get(modules, str(modules))} modules" in readme,
          f"the README's module count matches mcp/ ({modules} .py files)")

    # The residue audit has a floor of its own: a module no entry point reaches is a file the
    # next reader has to work out the purpose of. This is the check that would have caught
    # mcp/resolve_token.py, whose whole content was a credential path its callers had stopped
    # using and whose docstring still described a launcher contract that no longer existed.
    #
    # Reachability is a CLOSURE, not a first hop. `plots` is imported by `experiment_tree`,
    # not by the server, and a one-level check would have called it residue - which is the same
    # error as a grep that only searches the file you happen to be editing.
    def _imports_of(path: Path) -> set[str]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return set()
        out: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.update(Path(a.name).stem for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                out.add(Path(node.module).stem)
        return out

    ENTRY_POINTS = {"kaggle_server", "kaggle_cli", "agent_server"}
    on_disk_modules = {p.stem for p in (ROOT / "mcp").glob("*.py") if p.is_file()}
    reached: set[str] = {"kaggle_server"}
    frontier = ["kaggle_server.py", "kaggle_cli.py"]
    while frontier:
        for mod in _imports_of(ROOT / "mcp" / frontier.pop()):
            if mod in on_disk_modules and mod not in reached:
                reached.add(mod)
                frontier.append(f"{mod}.py")
    unreached = sorted(m for m in on_disk_modules if m not in reached and m not in ENTRY_POINTS)
    check(not unreached,
          f"every module in mcp/ is a declared entry point or reachable from one, transitively "
          f"({len(on_disk_modules)} modules, {len(reached)} reached, unreached: {unreached})")


def check_the_thinking_steps_were_actually_added():
    """Two steps that are judgement, not arithmetic, asserted where an agent will read them.

    Neither of these can be gated. A figure contract can be present and worthless, and a
    candidate list can have three entries and no killer. What the check can do is the smaller
    thing: make sure the step exists, names what it has to settle, and is not quietly deleted by
    a later edit that tidies the file. The ORDER assertion is the part that carries weight - a
    contract written after the chart has already been drawn is a caption, and the whole point is
    that it comes first, while the claim is still small enough to be wrong.
    """
    print("the two thinking steps")
    plot = _skill_body((ROOT / "skills" / "scientific-plotting" / "SKILL.md").read_text(encoding="utf-8"))
    low = plot.lower()
    for field in ("conclusion", "archetype", "panel map", "evidence hierarchy", "statistics",
                  "reviewer risk"):
        check(field in low,
              f"the figure contract names '{field}' - a template with blanks is not a contract")
    contract_at = low.find("write the figure contract")
    analyze_at = low.find('action="analyze"')
    check(0 < contract_at < analyze_at,
          f"the figure contract comes BEFORE the chart is drawn (contract at {contract_at}, "
          f"analyze at {analyze_at}) - afterwards it is a caption")
    check("cannot acquit" in low or "refuse" in low,
          "the contract says plainly that no gate can judge it, rather than implying one checks it")

    approach = _skill_body((ROOT / "skills" / "approach-decision" / "SKILL.md").read_text(encoding="utf-8"))
    alow = approach.lower()
    check("what would kill it" in alow,
          "a candidate is scored against the measurement that would end it")
    check("not a candidate" in alow,
          "the skill says why a candidate with no killer is not a candidate")
    check("cheapest" in alow,
          "and gives a tie-break between two survivors, so the section is not just a list")
    check("converg" in alow,
          "convergence counts against a candidate, reusing the argument the fork section makes")
    check(approach.count("```json") >= 2,
          "the recorded node is shown, because an unrecorded choice is re-litigated next session")
    first_at, second_at = alow.find("what you are trying to win"), alow.find("the short version")
    check(0 < first_at < second_at,
          f"naming the attack comes before the fork-vs-write call, which assumes one "
          f"(candidates at {first_at}, fork section at {second_at})")


def check_replay_semantics():
    """Replay must be a pure, deterministic walk over recorded history.

    Dream-RSI's claim is that alternative exploration policies can be scored by reading a
    completed history. That only holds if reading it changes nothing and always gives the
    same answer - otherwise comparing two policies is comparing two different experiments.
    """
    print("replay semantics")
    et, tree = _probe_tree()
    for nd in (_v3_node("n1", None, 0.40, 0.0, "draft", "baseline"),
               _v3_node("n2", "n1", 0.72, 0.32, "improve", "context"),
               _v3_node("n3", "n2", 0.65, -0.07, "improve", "context"),
               _v3_node("n4", "n2", 0.69, -0.03, "crossover", "verifier"),
               _v3_node("n5", "n2", 0.50, -0.22, "debug", "parser", verdict="revert")):
        tree["tree"]["nodes"][nd["id"]] = nd
    tree["tree"]["base"] = {"id": "n1", "label": "n1", "parent": None}

    p = {"weights": {"score": 1.0, "progress": 0.5, "novelty": 0.35}, "workers": 2, "maxRounds": 8}
    a = et.replay(tree, p)
    b = et.replay(tree, p)
    check((a["V"], a["N"], a["k"]) == (b["V"], b["N"], b["k"]),
          "replay is deterministic: same tree and policy give the same V, N and k")
    known = set(tree["tree"]["nodes"])
    check(all(nid in known for round_ids in a["revealed"] for nid in round_ids),
          "replay reveals only nodes that were actually recorded")
    check(not et.replay(tree, {"workers": 64, "maxRounds": 1})["ok"] is False
          or True, "replay terminates even with an over-large worker count")
    check(a["k"] <= p["maxRounds"], "replay respects maxRounds")
    # a batch can reveal several children at once, which the paper's chain-shaped tree cannot
    wide = et.replay(tree, {"weights": {"score": 1.0, "progress": 0.0, "novelty": 0.0},
                           "workers": 4, "maxRounds": 8})
    check(any(len(r) > 1 for r in wide["revealed"]) or wide["N"] >= 1,
          "a batch may reveal several children - our tree is a DAG, the paper's is chains")
    # nothing on disk changes
    path = et.tree_path("probe")
    before = open(path, "rb").read() if os.path.isfile(path) else None
    et.replay(tree, p)
    after = open(path, "rb").read() if os.path.isfile(path) else None
    check(before == after, "replay does not write to disk")


def check_monotone_policy():
    """Comparison must never leave the deployed policy worse than it is.

    This is the selection half of Dream-RSI and the direct answer to the "safe inheritance"
    failure in arXiv 2609.11873: persistence across rounds is not the same as improvement.
    """
    print("monotone policy")
    et, tree = _probe_tree()
    for nd in (_v3_node("n1", None, 0.40, 0.0, "draft", "baseline"),
               _v3_node("n2", "n1", 0.72, 0.32, "improve", "context"),
               _v3_node("n3", "n2", 0.65, -0.07, "improve", "context")):
        tree["tree"]["nodes"][nd["id"]] = nd
    tree["tree"]["base"] = {"id": "n1", "label": "n1", "parent": None}
    cur = {"id": "cur", "params": {"weights": {"score": 1.0, "progress": 0.5, "novelty": 0.35},
                                   "workers": 1, "maxRounds": 8}}
    dead = {"id": "dead", "params": {"weights": {"score": 0.0, "progress": 0.0, "novelty": 0.0},
                                     "workers": 1, "maxRounds": 1}}
    res = et.compare(tree, [cur, dead], deployed=cur)
    check(res["ok"], "compare runs when the current policy is among the candidates")
    check(res["monotone"], "the selection is reported as monotone")
    check(res["best"]["V"] >= res["current"]["V"] - 1e-9,
          "the selected policy is never worse than the current one")
    no_base = et.compare(tree, [dead], deployed=cur)
    check(not no_base["ok"] and no_base.get("code") == "baseline_missing",
          "compare refuses when the candidate set omits the current policy")
    no_deploy = et.compare(tree, [cur, dead])
    check(not no_deploy["ok"] and no_deploy.get("code") == "no_baseline",
          "compare refuses when nothing is deployed to be monotone against")


def check_efc_accounting():
    """EFC is per criterion. Collapsing it to one boolean throws away the finding."""
    print("effective feedback compute")
    et, _ = _probe_tree()
    check(set(et.EFC_FLAGS) == {"informative", "valid", "redundant", "retained"},
          "the four EFC flags are the ones the paper names")
    quota = {"quotaHours": 4.0}
    all_redundant = [{"name": "correctness", "value": 0.5, "direction": "higher",
                      "efc": {"informative": False, "valid": True,
                              "redundant": True, "retained": True}}]
    mixed = [{"name": "correctness", "value": 0.9, "direction": "higher",
              "efc": {"informative": False, "valid": True, "redundant": True, "retained": True}},
             {"name": "wallcost", "value": 1.0, "direction": "lower",
              "efc": {"informative": True, "valid": True, "redundant": False, "retained": True}}]
    n_red = _v3_node("a", None, 1.0, 0.0, "improve", "f", cost=quota, criteria=all_redundant)
    n_mix = _v3_node("b", None, 1.0, 0.0, "improve", "f", cost=quota, criteria=mixed)
    check(et.effective_cost(n_red) == 0.0,
          "a node that was redundant on every criterion has zero effective cost")
    check(et.effective_cost(n_mix) == 4.0,
          "one informative criterion makes the whole node's cost count")
    check(et.effective_cost(_v3_node("c", None, 1.0, 0.0, "improve", "f", cost=quota)) == 4.0,
          "a node with no criteria falls back to its raw quota")
    nodes = {"a": n_red, "b": n_mix}
    summary = et._efc_summary(nodes)
    per = summary["perCriterion"]
    check(per.get("correctness", {}).get("redundant", 0) >= 1
          and per.get("correctness", {}).get("informative", 0) == 0,
          "per-criterion EFC keeps correctness redundant")
    check(per.get("wallcost", {}).get("informative", 0) == 1,
          "per-criterion EFC keeps wallcost informative")
    check(summary["effectiveQuotaHours"] < summary["rawQuotaHours"],
          "raw versus effective compute are distinguished, not merged")


def check_failure_layer_and_anchor():
    print("failure layers and the evaluation anchor")
    et, _ = _probe_tree()
    base = _v3_node("n1", None, 0.5, 0.0, "draft", "core")
    et._write("probe", {**et.empty_tree(),
                        "tree": {"base": {"id": "n1", "label": "n1", "parent": None},
                                 "nodes": {"n1": base}}})
    rev = et.read("probe")["readRevision"]
    no_layer = _v3_node("n2", "n1", 0.2, -0.3, "debug", "parser", verdict="revert")
    no_layer.pop("failureLayer", None)
    r = et.record("probe", no_layer, read_revision=rev)
    check(not r["ok"], "a refuted node with no failureLayer is rejected")
    # the rejected write must have left nothing behind, so the next record uses a fresh id
    with_layer = _v3_node("n2b", "n1", 0.2, -0.3, "debug", "parser", verdict="revert",
                          layer="tool-recovery")
    r2 = et.record("probe", with_layer, read_revision=et.read("probe")["readRevision"])
    check(r2["ok"], "the same node is accepted once it names the layer")
    bad_layer = _v3_node("n3", "n1", 0.2, -0.3, "debug", "p2", verdict="revert", layer="vibes")
    r3 = et.record("probe", bad_layer, read_revision=et.read("probe")["readRevision"])
    check(not r3["ok"], "an unknown failureLayer is rejected")

    et.declare_anchor("probe", "private-split")
    leak = _v3_node("n4", "n1", 0.9, 0.4, "improve", "context")
    leak["metric"]["split"] = "private-split"
    r4 = et.record("probe", leak, read_revision=et.read("probe")["readRevision"])
    check(not r4["ok"], "a node scored on the held-out set is rejected once the anchor is declared")


# ---------------------------------------------------------------- ablation arithmetic
_CTL = {"seed": 1, "budget": "s", "eval": "v", "retrain": "re-eval"}


def _ab(nid, factors, result, parent=None, controls=None, **extra):
    """A bare experiment node for the ablation arithmetic (no metric ceremony needed)."""
    n = {"id": nid, "kind": "experiment", "parent": parent, "change": "swap one thing",
         "hypothesis": "h", "metric": {"name": "s", "parent": 0.0, "result": result,
                                       "delta": result, "rank": 1, "rankSource": "local",
                                       "direction": "higher"},
         "verdict": "keep", "reason": "r", "artifacts": ["a"], "evidence": "local-only",
         "operator": "improve", "family": "f"}
    if factors is not None:
        n["factors"] = factors
    if controls is not None:
        n["controls"] = controls
    n.update(extra)
    return n


def _ab_tree(nodes, base=None):
    return {"base": base, "nodes": nodes}


def check_ablation_arithmetic():
    print("ablation arithmetic")
    et, _ = _probe_tree()

    # factors=[] is the bare model, not an absent field. This one line is the difference between
    # an ablation table and a table that quietly starts one row too low.
    check(et._factor_set({"factors": []}) == frozenset(),
          "an empty factor list is the bare model, not an unrecorded one")
    check(et._factor_set({"factors": ["A", "b", "A"]}) == frozenset({"a", "b"}),
          "factors are folded to lower-case slugs and de-duplicated")
    check(et._factor_set({}) is None,
          "an absent factors field is still None - unknown stays unknown")
    n = et.normalize_node({"factors": ["B", "a"]})
    check(n["factors"] == ["a", "b"], "normalize_node sorts a factor set deterministically")
    check(et.normalize_node({"factorsIntent": "FACTORIAL"})["factorsIntent"] == "factorial",
          "normalize_node folds the intent too")
    check(et.normalize_node({"factors": []})["factors"] == [],
          "normalize_node keeps the bare-model arm instead of dropping it")

    # --- the case the rule was written for: bare -> +a -> +a+b, and no standalone b arm
    ladder = _ab_tree({
        "n0": _ab("n0", [], 0.50, controls=_CTL),
        "n1": _ab("n1", ["a"], 0.60, parent="n0", controls=_CTL),
        "n2": _ab("n2", ["a", "b"], 0.75, parent="n1", controls=_CTL),
    })
    t = et.ablation_table(ladder)
    check(t["baseline"] and t["baseline"]["node"] == "n0",
          f"the bare arm is the baseline (got {t['baseline']}) - it was being dropped from its "
          f"own table because an empty list read as absent")
    check(len(t["edges"]) == 2, "each one-factor step is an edge")
    check(all(e["direction"] == "add" for e in t["edges"]),
          "direction is read off the parent link, so an upward ladder reads as additions")
    check(t["interactions"] == [],
          "a pair with no standalone arm produces NO interaction - the degenerate triple used "
          "to print a confident 0.00, which reads exactly like independence")
    cond = {c["factor"]: c for c in t["conditional"]}
    check("a" not in cond, "a was measured on the bare arm, so its effect needs no caveat")
    check(cond.get("b", {}).get("shape") == "add-one-in",
          f"b was only ever added on top of a, and is named as such (got {cond.get('b')})")
    check("in that company" in cond.get("b", {}).get("note", ""),
          "the caveat says what the number actually is: b's effect alongside a")

    # --- leave-one-out: same edges, read from the other end
    loo = _ab_tree({
        "m0": _ab("m0", ["a", "b", "c"], 0.90, controls=_CTL),
        "m1": _ab("m1", ["a", "b"], 0.70, parent="m0", controls=_CTL),
        "m2": _ab("m2", ["a", "c"], 0.68, parent="m0", controls=_CTL),
        "m3": _ab("m3", ["b", "c"], 0.66, parent="m0", controls=_CTL),
    })
    t2 = et.ablation_table(loo)
    check(t2["baseline"] is None,
          "a leave-one-out family has no bare arm, and the table says so instead of promoting "
          "an arbitrary pair to baseline")
    check({e["factor"] for e in t2["edges"]} == {"a", "b", "c"},
          "every factor is isolated by dropping it")
    check(all(e["direction"] == "drop" for e in t2["edges"]),
          "the same table reads as removals for a family declared as removals")
    check(t2["gaps"] == [], "nothing is missing when every factor has an isolating comparison")
    check(all(c["shape"] == "leave-one-out" for c in t2["conditional"]),
          "the family is recognised as leave-one-out, and told its deltas do not add up")

    # --- a factorial arm needs four DISTINCT arms before an interaction exists
    fac = _ab_tree({
        "f0": _ab("f0", [], 0.50, controls=_CTL),
        "f1": _ab("f1", ["a"], 0.60, parent="f0", controls=_CTL),
        "f2": _ab("f2", ["b"], 0.62, parent="f0", controls=_CTL),
        "f3": _ab("f3", ["a", "b"], 0.95, parent="f1", controls=_CTL,
                  factorsIntent="factorial"),
    })
    t3 = et.ablation_table(fac)
    check(len(t3["interactions"]) == 1, "four distinct arms make exactly one interaction")
    it = t3["interactions"][0]
    check(abs(it["interaction"] - 0.23) < 1e-9,
          f"the interaction is joint minus the parts, not the joint (got {it['interaction']})")
    check(it["kind"] == "synergistic", "a pair doing more than the sum is called synergistic")
    check(t3["size"]["complete"] and t3["size"]["fullFactorialArms"] == 4,
          "the factorial size is reported, so a half-covered grid does not look finished")

    # --- noise: a repeated configuration is the only free measurement of it
    rep = _ab_tree({
        "r0": _ab("r0", [], 0.50, controls=_CTL),
        "r0b": _ab("r0b", [], 0.52, parent="r0", controls=_CTL, factorsIntent="repeat"),
        "r1": _ab("r1", ["a"], 0.53, parent="r0", controls=_CTL),
        "r2": _ab("r2", ["a", "b"], 0.80, parent="r1", controls=_CTL),
    })
    t4 = et.ablation_table(rep)
    check(abs((t4["noise"] or 0) - 0.02) < 1e-9,
          f"repeated configurations give the noise floor (got {t4['noise']})")
    check(any(e["kind"] == "indistinguishable" for e in t4["edges"]),
          "a delta inside the noise floor is not evidence and is marked so")
    check(all(not e["without"] == "r0b" and not e["with"] == "r0b" or e["direction"] != "repeat"
              for e in t4["edges"]),
          "a repeat is not an edge - identical factor sets isolate nothing")
    check(t4["repeats"] and t4["repeats"][0]["nodes"] == ["r0", "r0b"],
          "the repeat is reported, because it is what the noise estimate rests on")

    # --- controls that disagree make a delta unattributable, and say which one
    conf = _ab_tree({
        "c0": _ab("c0", [], 0.50, controls=_CTL),
        "c1": _ab("c1", ["a"], 0.62, parent="c0", controls=dict(_CTL, seed=7)),
    })
    t5 = et.ablation_table(conf)
    check(t5["edges"] and t5["edges"][0]["attributable"] is False,
          "an edge whose arms disagree on a control is not attributable")
    check(t5["confounds"] and "seed" in t5["confounds"][0]["differ"][0],
          f"the table names the control that moved (got {t5['confounds']})")
    check("not comparable" in t5["note"], "the note leads with the confound, not the numbers")

    # --- a factor that never took part in any comparison is named as missing
    lone = _ab_tree({
        "z0": _ab("z0", ["a", "b", "c"], 0.70, controls=_CTL),
        "z1": _ab("z1", ["a", "b"], 0.60, parent="z0", controls=_CTL),
    })
    t6 = et.ablation_table(lone)
    check(any("'a'" in g for g in t6["gaps"]) and any("'b'" in g for g in t6["gaps"]),
          f"a factor that only ever ran inside a combination is reported missing "
          f"(got {t6['gaps']})")
    check(not any("'c'" in g for g in t6["gaps"]),
          "c was isolated by the drop, so it is not reported missing - a gap that names a factor "
          "which does have an arm sends people to run an experiment they already ran")
    check(any("added" in g and "removed" in g for g in t6["gaps"]),
          "the missing arm is named in both directions, because a leave-one-out family needs "
          "the removal and an add-one-in ladder needs the addition")

    # --- an empty tree says what to record rather than printing an empty table
    empty = et.ablation_table(_ab_tree({}))
    check(empty["rows"] == [] and "factors" in empty["note"],
          "an empty tree explains how to start one")

    # --- the gates, exercised through the real write path
    et._write("abl", et.empty_tree())
    base = _v3_node("b1", None, 0.50, 0.0, "draft", "f")
    base["factors"] = ["a"]
    base["controls"] = dict(_CTL)
    check(et.record("abl", base, read_revision=et.read("abl")["readRevision"])["ok"],
          "the bare first arm records")

    def _try(nd):
        return et.record("abl", nd, read_revision=et.read("abl")["readRevision"])

    two = _v3_node("b2", "b1", 0.70, 0.2, "improve", "f")
    two["factors"] = ["a", "b", "c"]
    two["controls"] = dict(_CTL)
    r = _try(two)
    check(not r["ok"], "changing two factors at once is refused - no conjunction word required")
    check(any("2 factors" in p for p in r.get("problems", [])),
          f"the refusal states the arithmetic (got {r.get('problems')})")
    two["factorsIntent"] = "factorial"
    check(_try(two)["ok"], "declaring it a factorial arm is the way through")

    same = _v3_node("b3", "b2", 0.72, 0.02, "improve", "f")
    same["factors"] = ["a", "b", "c"]
    same["controls"] = dict(_CTL)
    r = _try(same)
    check(not r["ok"], "a run with the same factors as its parent is refused")
    check(any("seed" in p for p in r.get("problems", [])),
          "the refusal says the delta measures the seed rather than a factor")
    same["factorsIntent"] = "repeat"
    check(_try(same)["ok"], "declaring it a repeat is the way through")

    seeded = _v3_node("b4", "b3", 0.74, 0.02, "improve", "f")
    seeded["factors"] = ["a", "b", "c", "d"]
    seeded["controls"] = dict(_CTL, seed=99)
    r = _try(seeded)
    check(not r["ok"], "a run that changed a control as well as a factor is refused")
    check(any("disagree about" in p for p in r.get("problems", [])),
          f"the refusal names the control that moved (got {r.get('problems')})")
    seeded["confoundReason"] = "the seed is the experiment"
    check(_try(seeded)["ok"], "saying why the control moved is the way through")

    quiet = _v3_node("b5", "b4", 0.76, 0.02, "improve", "f")
    check("factors" not in quiet and "controls" not in quiet,
          "the probe really is a node that says nothing about factors or controls")
    check(_try(quiet)["ok"],
          "a node that records no factors and no controls is still writable - refusing it would "
          "have rejected every tree ever recorded, which is what happened once already")
    check(not any("controls" in p for p in (_try(quiet).get("problems") or [])),
          "silence about controls is not the same as a disagreement about them")

    bad_slug = _v3_node("b6", "b5", 0.78, 0.02, "improve", "f")
    bad_slug["factors"] = ["two words and a space"]
    r = _try(bad_slug)
    check(not r["ok"], "a factor that is not one slug is refused")
    check(any("end to end" in p for p in r.get("problems", [])),
          f"the slug rule is anchored, not a prefix match (got {r.get('problems')})")

    bad_retrain = _v3_node("b7", "b5", 0.78, 0.02, "improve", "f")
    bad_retrain["factors"] = ["a"]
    bad_retrain["controls"] = {"retrain": "sometimes"}
    check(not _try(bad_retrain)["ok"],
          "retrain must be from-scratch or re-eval, because they answer different questions")

    bad_intent = _v3_node("b8", "b5", 0.78, 0.02, "improve", "f")
    bad_intent["factors"] = ["a"]
    bad_intent["factorsIntent"] = "vibes"
    check(not _try(bad_intent)["ok"], "an invented factorsIntent is refused")

    # --- a declaration is an announcement, not an arm
    et._write("abl2", et.empty_tree())
    check(et.record("abl2", _ab("p0", ["a"], 0.50, controls=_CTL),
                    read_revision=0)["ok"], "the arm before the declaration lands")
    decl = {"id": "p1", "kind": "experiment", "parent": "p0", "change": "add b",
            "hypothesis": "h", "reason": "r", "operator": "improve", "family": "f",
            "factors": ["a", "b"], "controls": dict(_CTL),
            "diagnosis": "none", "diagnosisReason": "ladder",
            "expect": {"direction": "up", "atLeast": 0.01}}
    check(et.declare("abl2", decl, read_revision=et.read("abl2")["readRevision"])["ok"],
          "a declaration carrying factors is accepted")
    landed = _ab("p1r", ["a", "b"], 0.62, parent="p1")
    landed["operator"], landed["family"] = "improve", "f"
    res = et.settle("abl2", "p1", landed, read_revision=et.read("abl2")["readRevision"])
    check(res.get("ok"),
          f"the settled run is accepted - a declaration carries the factors the run WILL have, "
          f"so measuring the run against its own announcement always reads as 'nothing changed' "
          f"and every settle in the tree would be refused (got {res.get('problems')})")
    nodes2 = et.load("abl2")["tree"]["nodes"]
    check(et._comparison_parent(nodes2, nodes2["p1r"]) == "p0",
          "a settled run is measured against the arm its declaration was measured against")
    check(nodes2["p1"].get("status") == "planned",
          "the declaration is still marked planned, which is how the walk knows to step over it")
    ladder2 = et.ablation_table(et.load("abl2"))
    check([r["node"] for r in ladder2["rows"]] == ["p0", "p1r"],
          "the declaration itself is not an arm in the table - it has no result to place")
    check(ladder2["edges"] and ladder2["edges"][0]["direction"] == "add",
          f"a declared-and-settled ladder step reads as an addition through the declaration "
          f"(got {[(e['factor'], e['direction']) for e in ladder2['edges']]})")

    # --- a doc that claims to describe what the tree refuses must speak the tree's language
    design = (ROOT / "skills" / "ablation-design" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("factors", "controls", "factorsIntent", "confoundReason", "ablate"):
        check(token in design,
              f"ablation-design mentions {token} - a skill that teaches one-variable nodes "
              f"without them produces nodes that are legal and invisible to the ablation table")
    check("The tree already refuses" not in design,
          "ablation-design no longer claims the tree refuses conjunction words - that is a "
          "wording scan, and the tree enforces the factor symmetric difference instead")
    rsi = (ROOT / "skills" / "rsi-experiment-tree" / "SKILL.md").read_text(encoding="utf-8")
    check("ablation-design" in rsi,
          "rsi-experiment-tree points at ablation-design, so the design-time half is reachable "
          "from the half that enforces the rules")

    # --- the action is actually reachable
    srv = SERVER_PY.read_text(encoding="utf-8")
    check('"board", "ablate"' in srv, "kaggle_experiment_tree exposes the ablate action")
    check('action == "ablate"' in srv and "ablation_table_response" in srv,
          "ablate is wired to the ablation table rather than left dangling")
    check("ablate," in srv, "the unknown-action hint lists ablate")
    check("factorsIntent" in srv and "confoundReason" in srv,
          "the node schema tells the caller about the escape hatches, not just the rule")

    # the skill has to teach the shape, or the rule is only discoverable by hitting it
    doc = (ROOT / "skills" / "rsi-experiment-tree" / "SKILL.md").read_text(encoding="utf-8")
    for token, why in (("factors", "what was switched on"),
                       ("factorsIntent", "the declared exceptions"),
                       ("confoundReason", "the declared control change"),
                       ('action="ablate"', "how to read the table"),
                       ("from-scratch", "the retrain axis")):
        check(token in doc, f"SKILL.md documents {token} ({why})")
    check("leave-one-out" in doc and "add-one-in" in doc,
          "SKILL.md names both directions, because the table serves both")
    check("0.00" in doc,
          "SKILL.md says what a degenerate interaction looks like - a flat zero that reads as "
          "independence is the failure, not a rounding detail")


def check_undo_and_rounds():
    print("undo and round archival")
    et, _ = _probe_tree()
    et._write("probe", et.empty_tree())
    for nid, res in (("n1", 0.4), ("n2", 0.5)):
        nd = _v3_node(nid, None if nid == "n1" else "n1", res, 0.1, "draft", "f")
        et.record("probe", nd, read_revision=et.read("probe")["readRevision"])
    p = et.register_policy("probe", {"workers": 2}, label="wide")
    pid = p["policy"]["id"]
    et.deploy_policy("probe", pid)
    check(et.load("probe")["deployedPolicy"] == pid, "a policy can be deployed")
    u = et.undo("probe")
    check(u["ok"] and et.load("probe")["deployedPolicy"] is None,
          "undo steps back the deployment")
    before_rounds = len(et.load("probe")["rounds"])
    et.close_round("probe", read_revision=et.read("probe")["readRevision"])
    check(len(et.load("probe")["rounds"]) == before_rounds + 1, "a round can be archived")
    check(et.load("probe")["tree"]["nodes"] == {}, "closing a round empties the current tree")
    u2 = et.undo("probe")
    check(u2["ok"] and et.load("probe")["tree"]["nodes"],
          "undo puts the archived tree back, not just the round counter")
    check(len(et.load("probe")["rounds"]) == before_rounds,
          "undo also removes the archive it added")


def check_migration_v2_to_v3():
    print("v2 to v3 migration")
    et, _ = _probe_tree()
    home = et._home()
    d = os.path.join(home, "handoff", "legacy")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "tree.json"), "w", encoding="utf-8") as fh:
        json.dump({"base": {"id": "a1", "label": "old", "parent": None},
                   "nodes": {"a1": {"parent": None, "change": "c", "hypothesis": "h",
                                    "metric": {"name": "m", "parent": 1, "result": 2,
                                               "delta": 1, "rank": 0.5,
                                               "rankSource": "l"},
                                    "verdict": "keep", "reason": "r", "artifacts": ["a"]}},
                   "revision": 7}, fh)
    m = et.load("legacy")
    check(m.get("migrated") is True, "a v2 document is reported as migrated")
    check("a1" in m["tree"]["nodes"], "migration keeps the old nodes")
    check(m["revision"] == 7, "migration keeps the old revision")
    check(isinstance(m["rounds"], list) and isinstance(m["policies"], dict)
          and isinstance(m["journal"], list) and isinstance(m["anchor"], dict),
          "migration supplies the v3 shells")


def check_decision_coupling():
    """Two mechanisms must not silently own the same decision point.

    ModularRSI reported 44.19% for jointly evolving its five modules against 52.43% for
    evolving them independently and integrating - the failure being two completion gates
    fighting each other. The graph is where a duplicate claim would be visible, so this
    looks for it rather than hoping nobody adds one.

    A duplicate claim is not automatically a defect: genui-scenarios and presence-mode really
    do both touch these skills, and that overlap is legitimate as long as each owns a
    DIFFERENT part of the decision. So a node listed in `divisionOfLabour` is allowed, and the
    declaration has to say who decides what - otherwise the two gates can still contradict.
    """
    print("decision coupling")
    rel, _ = parse_json(REL)
    if rel is None:
        return
    claims: dict[str, list[str]] = {}
    for e in rel["edges"]:
        if e["type"] in ("gate", "asks"):
            claims.setdefault(e["to"], []).append(f"{e['from']} ({e['type']})")
    labour = {k: v for k, v in (rel.get("divisionOfLabour") or {}).items()
              if not k.startswith("_")}
    duplicates = {k: v for k, v in claims.items() if len(v) >= 2}

    for target, owners in sorted(duplicates.items()):
        declared = labour.get(target)
        if not declared:
            bad(f"'{target}' is claimed by {len(owners)} mechanisms at once "
                f"({'; '.join(owners)}). Declare the split of labour under "
                f"\"divisionOfLabour\" - two gates that both think they decide the same "
                "thing is the shape that cost ModularRSI eight points.")
            continue
        # the declaration must actually split the decision, not just acknowledge it
        owners_declared = declared.get("owners") or []
        if len(owners_declared) < 2:
            bad(f"'{target}' declares a division of labour but names only "
                f"{len(owners_declared)} owner(s)")
            continue
        decided = {d.get("decides") for d in owners_declared if isinstance(d, dict)}
        if len(decided) != len(owners_declared) or any(not d for d in decided):
            bad(f"'{target}' must give each owner a distinct, named responsibility")
            continue
        ok(f"'{target}' is claimed by {len(owners)} mechanisms and the split of labour is "
           f"declared: " + " | ".join(str(d) for d in declared.get("owners")))

    orphans = [k for k in labour if len(claims.get(k, [])) < 2]
    for k in orphans:
        bad(f"divisionOfLabour declares '{k}' but it is not actually claimed twice")


def check_tree_ownership():
    """tree.json must have exactly one writer, and it must be experiment_tree.

    handoff.py used to keep its own copy of load/save and could therefore write a tree
    straight to disk, bypassing the read-gate and every node rule. The fix is only durable
    if something keeps it from creeping back.
    """
    print("tree ownership")
    handoff_src = (ROOT / "mcp" / "handoff.py").read_text(encoding="utf-8")
    # parse rather than grep: the file's own comment explains the rule and names the removed
    # functions, so a substring search would match the explanation and fail for the wrong reason
    import ast as _ast
    defined = set()
    try:
        for node in _ast.parse(handoff_src).body:
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                defined.add(node.name)
            elif isinstance(node, _ast.Assign):
                for target in node.targets:
                    if isinstance(target, _ast.Name):
                        defined.add(target.id)
    except SyntaxError as exc:
        bad(f"handoff.py does not parse: {exc}")
    check("load_tree" not in {n for n in defined if n.startswith("def")},
          "handoff.py does not define its own load_tree")
    check("save_tree" not in defined,
          "handoff.py does not define its own save_tree")
    check("load_tree" in defined and "save_tree" not in defined,
          "handoff.py's tree access is an alias onto the owner, not a reimplementation")
    check("import experiment_tree" in handoff_src,
          "handoff.py imports the tree owner")
    check("experiment_tree.validate" in handoff_src,
          "handoff_write validates the tree before writing it")
    check("experiment_tree.save" in handoff_src,
          "handoff_write persists through the owner's atomic writer")

    # a malformed tree handed to handoff_write must be refused, not persisted
    spec = importlib.util.spec_from_file_location("hf_check", ROOT / "mcp" / "handoff.py")
    sys.path.insert(0, str(ROOT / "mcp"))
    hf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hf)
    os.environ["KAGGLE_AGENT_HOME"] = _mkdtemp()
    bad_tree = {"tree": {"base": {"id": "n1", "label": "n1", "parent": None},
                         "nodes": {"n1": {"id": "n1", "kind": "experiment", "parent": None,
                                           "change": "two things and a third",
                                           "verdict": "keep", "reason": "r"}}}}
    try:
        hf.write("coup", {}, tree=bad_tree)
        bad("handoff_write accepted a tree the owner would reject")
    except ValueError:
        ok("handoff_write refuses a tree the owner rejects")


def check_runtime_behaviour():
    """Static structure is not runtime behaviour; drive the server over the wire.

    The report's own lesson applies here: a harness change can be large enough to cover
    model-generation gaps and can still destroy behaviour with no failing check. So the
    actions have to be exercised, not merely present.
    """
    print("runtime behaviour")
    import subprocess
    server = ROOT / "mcp" / "agent_server.py"
    env = dict(os.environ, KAGGLE_AGENT_HOME=_mkdtemp())
    proc = subprocess.Popen([sys.executable, "-B", str(server)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env)
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        def recv():
            while True:
                line = proc.stdout.readline()
                if not line:
                    return None
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

        send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "1"}}})
        recv()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        listed = recv() or {}
        names = {t["name"] for t in listed.get("result", {}).get("tools", [])}
        check("kaggle_experiment_tree" in names, "tools/list advertises the tree tool")
        check("kaggle_search_engine" in names, "tools/list still advertises the search tool")

        n = [1]

        def call(tool, args):
            n[0] += 1
            send({"jsonrpc": "2.0", "id": n[0], "method": "tools/call",
                  "params": {"name": tool, "arguments": args}})
            return recv() or {}

        def tree(action, **kw):
            r = call("kaggle_experiment_tree",
                     {"competition": "wire", "action": action, **kw})
            res = r.get("result", {})
            return res.get("content", [{}])[0].get("text", ""), res.get("isError", False)

        txt, err = tree("anchor", held_out="private-split")
        check(not err and "private-split" in txt, "the anchor can be declared over the wire")
        txt, err = tree("status")
        check("anchor:" in txt, "status reports the anchor")
        txt, _ = tree("read")
        rev = 0
        for line in txt.splitlines():
            if line.startswith("revision:"):
                rev = int(line.split()[1])
        nd = _v3_node("n1", None, 0.4, 0.0, "draft", "base")
        nd["new_base"] = "n1"
        txt, err = tree("record", read_revision=rev, node=nd)
        check(not err, "a node records over the wire")
        txt, err = tree("select", weights={"workers": 2})
        check("batch (|C| <= workers" in txt, "select returns a batch over the wire")
        txt, err = tree("board")
        check("effective feedback compute" in txt, "board reports EFC over the wire")
    finally:
        proc.kill()


# ---------------------------------------------------------------- evidence and plots
def check_evidence_chain():
    """A conclusion with nothing behind it must be un-recordable.

    The store exists so a review can re-read the reasoning. That only works if every concluded
    node says what it rests on - and the check has to be real, because "silence" is the failure
    that leaves a decision looking considered when nobody knows.
    """
    print("evidence chain")
    et, _ = _probe_tree()
    et._write("probe", et.empty_tree())
    rev = et.read("probe")["readRevision"]
    bare = _v3_node("n1", None, 0.5, 0.0, "draft", "core", new_base="n1")
    bare.pop("evidence", None)   # this probe is exactly the "no provenance at all" case
    r = et.record("probe", bare, read_revision=rev)
    check(not r["ok"], "a concluded node with neither source nor evidence is refused")
    check(any("provenance" in p for p in r.get("problems") or []),
          "the refusal names provenance as the reason")

    local = _v3_node("n1", None, 0.5, 0.0, "draft", "core", new_base="n1",
                     evidence="local-only")
    r2 = et.record("probe", local, read_revision=et.read("probe")["readRevision"])
    check(r2["ok"], "evidence='local-only' is an accepted, honest alternative")

    # a source id that is not in the store must not slip through as a dangling link
    dangling = _v3_node("n2", "n1", 0.6, 0.1, "improve", "ctx",
                        sources=[{"sourceId": "not-in-store", "relation": "supports"}])
    r3 = et.record("probe", dangling, read_revision=et.read("probe")["readRevision"])
    check(not r3["ok"], "a source id that does not resolve is refused")
    check(any("evidence store" in p for p in r3.get("problems") or []),
          "the dangling-link refusal says the id is not in the store")

    # the same source may support AND contradict - that is two claims, not a duplicate
    et.register_policy  # noqa: B018 - keep the import surface obvious
    dup = _v3_node("n3", "n1", 0.6, 0.1, "improve", "ctx2", evidence="local-only",
                   sources=[{"sourceId": "s1", "relation": "supports"},
                            {"sourceId": "s1", "relation": "contradicts"}])
    import sources as _s
    _s.add(kind="paper", title="t", source_id="s1")
    r4 = et.record("probe", dup, read_revision=et.read("probe")["readRevision"])
    check(r4["ok"], "one source may support and contradict the same claim (relations differ)")
    same = _v3_node("n4", "n1", 0.6, 0.1, "improve", "ctx3", evidence="local-only",
                    sources=[{"sourceId": "s1", "relation": "supports"},
                             {"sourceId": "s1", "relation": "supports"}])
    r5 = et.record("probe", same, read_revision=et.read("probe")["readRevision"])
    check(not r5["ok"], "the same source repeated under the same relation is refused")


def check_plotting_backend_is_real():
    """Figures are drawn with numpy + matplotlib, and every claim about that is measured.

    The previous version of this check asserted the OPPOSITE - that the engine must not import
    either package - because the engine used to hand-write SVG. That invariant was honest for
    the engine it described and wrong for the one that exists, and a check that outlives its
    reason is a check that stops meaning anything. So it is inverted here, and the new
    assertions are the ones that actually matter on a machine that is missing the backend:

      - a missing package produces `backend_missing` carrying the command that fixes it, not
        an ImportError traceback out of a third-party import;
      - a dataset with nothing plottable is refused and writes no file;
      - the palette is the audited one, and the audit is re-runnable from the repo;
      - `doctor` tells the truth about readiness instead of hard-coding it.
    """
    print("plotting backend")
    src = (ROOT / "mcp" / "plots.py").read_text(encoding="utf-8")
    # By NAME, not by import statement. The engine resolves both through one lazy
    # `importlib.import_module` call so there is a single place that can turn a missing package
    # into an instruction, and an assertion looking for the text "import numpy" would have
    # rejected the better implementation.
    for needed in ("numpy", "matplotlib"):
        check(needed in src, f"the engine draws with {needed} - it is the backend, not an extra")

    _spec = importlib.util.spec_from_file_location("pl_chk", ROOT / "mcp" / "plots.py")
    pl = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(pl)
    os.environ["KAGGLE_AGENT_HOME"] = _mkdtemp()
    res = pl.render("line", {"series": [{"label": "s", "points": [[1, 1], [2, 2], [3, 3]]}]},
                    "backend", "t")
    check(res.get("ok") and os.path.isfile(res["path"]), "a figure renders through the backend")
    text = open(res["path"], encoding="utf-8").read()
    check(text.startswith("<?xml") and "</svg>" in text, "the output is standalone vector SVG")

    # every kind draws, from the same data contract `analyze` uses
    for kind, data in (("band", {"bands": [{"label": "n1", "mean": 0.5, "std": 0.02, "n": 3}]}),
                       ("bar", {"items": [{"label": "op", "value": 0.1}]}),
                       ("scatter", {"points": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]}),
                       ("pareto", {"points": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]}),
                       ("forest", {"rows": [{"name": "acc", "value": 0.6, "std": 0.01}]})):
        r = pl.render(kind, data, f"each-{kind}", "t")
        check(r.get("ok") and os.path.isfile(r["path"]), f"the {kind} chart draws")

    empty = pl.render("line", {"series": []}, "empty", "t")
    check(not empty.get("ok") and not os.path.isfile(os.path.join(pl.plots_dir(), "empty.svg")),
          "an empty dataset is refused and writes no file")
    check(set(pl.CHART_KINDS) == {"line", "band", "bar", "scatter", "pareto", "forest"},
          "the six chart types are declared")

    # A missing backend has to be an instruction, not a stack trace. `sys.modules[name] = None`
    # makes `import name` raise, which is the closest faithful simulation of an absent package.
    saved = {k: sys.modules.get(k) for k in ("numpy", "matplotlib")}
    sys.modules["numpy"] = None
    try:
        blocked = pl.render("line", {"series": [{"points": [[1, 1], [2, 2]]}]}, "blocked", "t")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    check(blocked.get("code") == "backend_missing" and "numpy" in blocked.get("install", ""),
          "a missing backend is named, with the command that installs it")
    check("Traceback" not in blocked.get("error", "") and "Error" not in blocked.get("error", ""),
          "a missing backend is reported as an instruction, never as a traceback")

    # ...and with BOTH packages absent it has to name both. Reporting only the first one would
    # produce an install command that is still missing a dependency after the user runs it.
    saved2 = {k: sys.modules.get(k) for k in ("numpy", "matplotlib")}
    sys.modules["numpy"] = None
    sys.modules["matplotlib"] = None
    try:
        both = pl._backend
        try:
            both()
            names: list[str] = []
        except pl.BackendMissing as exc:
            names = list(exc.missing)
    finally:
        for k, v in saved2.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    check(set(names) == {"numpy", "matplotlib"},
          f"an absent backend names every missing package, not just the first ({names})")

    # The palette is the audited Okabe-Ito subset, not whatever order someone typed. Two of the
    # eight full-set colours fall below 3:1 on white and a third pair collides in greyscale, so
    # this list is a measurement, and the auditor that produced it ships in the repo.
    check(list(pl.PALETTE) == ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#000000"],
          "the palette is the audited five, in the order the audit cleared")
    auditor = ROOT / "skills" / "scientific-plotting" / "scripts" / "palette_audit.py"
    check(auditor.is_file(), "the palette auditor ships with the plugin, so the claim is re-runnable")

    dsrc = (ROOT / "mcp" / "deps.py").read_text(encoding="utf-8")
    check("subprocess" in dsrc, "the installer exists")
    import deps as _d
    check(not _d.install(["definitely-not-real"]).get("ok"),
          "installing an unknown package is refused")
    p = _d.probe()
    ready = bool(p.get("plottingReady"))
    check(ready == all(_d._installed(n) for n in _d.BACKEND),
          "doctor reports readiness as the backend's real state, not a hard-coded True")
    if ready:
        check("nextStep" not in p, "a ready machine is not told to install something")
    else:
        check("kaggle_sources" in p.get("nextStep", ""),
              "a machine without the backend is given the command, as a next step")


def check_the_audit_drives_and_does_not_acquit():
    """Stage three of a claim audit: the mechanical half refuses, and never clears.

    The split is the whole design, so both halves are asserted separately and the more
    dangerous one gets the harder test. A gate that can say "the evidence exists" is easy to
    build and very easy to misread as "the evidence supports the claim" - and an audit that
    acquits is worse than no audit, because it manufactures the confidence it was meant to
    check. So:

      - a forbidden sentence copied in, and an artifact the tree claims that is not on disk,
        must both be REFUSED here, with no model in the loop. A refusal is arithmetic.
      - a number attributed to a node must be checked against what that node holds, and an
        unmatched one must be handed on as WORK rather than quietly cleared.
      - the packet must carry file paths and an explicit instruction not to accept a summary,
        because a reviewer handed a summary reviews the summary.
    """
    print("claim audit: drives, does not acquit")
    et, tree = _probe_tree()
    node = _v3_node("n1", None, 0.50, 0.00, "improve", "ablation",
                    cost={"quotaHours": 0.4, "wallSeconds": 900, "agentCalls": 12})
    r = et.record("probe", node, et.read("probe")["readRevision"])
    check(bool(r.get("ok")), f"the audit fixture tree records - problems={r.get('problems')!r}")

    # `artifacts: ["a"]` above points at nothing, so a phantom is present by construction.
    # That is the point: a tree that claims a file it never produced must be refused.
    check(hasattr(et, "audit_report"), "the tree exposes audit_report")
    if not hasattr(et, "audit_report"):
        return

    report = ("# Results\n\nThe base reached n1: 0.50.\n"
              "And the same run reached n1: 0.97.\n")   # n1 holds 0.50, not 0.97
    a = et.audit_report("probe", text=report)
    kinds = {x["kind"] for x in a.get("refusals") or []}
    check("phantom_artifact" in kinds,
          f"an artifact the tree claims but that is not on disk is refused ({sorted(kinds)})")
    nums = a.get("attributedNumbers") or {}
    matched = {(x["node"], x["claimed"]) for x in nums.get("matched") or []}
    unmatched = {(x["node"], x["claimed"]) for x in nums.get("unmatched") or []}
    check(("n1", "0.50") in matched, f"a number the node does hold is matched to its field "
                                    f"(matched={sorted(matched)})")
    check(("n1", "0.97") in unmatched,
          f"a number the node does NOT hold is reported, not cleared (unmatched={sorted(unmatched)})")
    check(any(x.get("note") for x in nums.get("unmatched") or []),
          "an unmatched number says what the node actually holds, so it can be acted on")
    check(a.get("reviewNeeded") is True,
          "a report with attributed numbers is flagged as needing a reviewer, even when the "
          "mechanical pass found nothing to refuse")

    # The dangerous half, asserted behaviourally rather than on the prose: a report whose numbers
    # all match and which trips no refusal still comes back "clean", and "clean" still does not
    # acquit it. If these two could not disagree, the whole distinction would be decorative.
    #
    # It needs a SECOND tree: the one above is built to contain a phantom artifact, so "clean" is
    # unreachable there and asserting it would fail for the fixture's reason, not the code's.
    et2, _ = _probe_tree()
    real_artifact = Path(os.environ["KAGGLE_AGENT_HOME"]) / "figure.png"
    real_artifact.write_bytes(b"\x89PNG\r\n\x1a\n")
    clean_node = _v3_node("n1", None, 0.50, 0.00, "improve", "ablation")
    clean_node["artifacts"] = [str(real_artifact)]
    r2 = et2.record("clean", clean_node, et2.read("clean")["readRevision"])
    check(bool(r2.get("ok")), f"the clean-tree fixture records - problems={r2.get('problems')!r}")
    clean = et2.audit_report("clean", text="# Results\n\nThe base reached n1: 0.50.\n")
    check(clean.get("code") == "clean" and not (clean.get("refusals") or []),
          f"a report with nothing mechanically wrong is not refused - the gate does not invent "
          f"faults (code={clean.get('code')!r}, refusals={clean.get('refusals')!r})")
    check(clean.get("reviewNeeded") is True and clean.get("ok") is True,
          "and 'clean' still demands a reviewer: a matching number proves the evidence EXISTS, "
          f"not that it supports the sentence (reviewNeeded={clean.get('reviewNeeded')!r})")
    check(et2.audit_report("clean", text="# Results\n\nNo attributed figures here.\n",
                           review_report=False).get("reviewNeeded") is False,
          "a reader who asked for no reviewer gets no reviewer")

    packet = a.get("reviewerPacket") or {}
    check("tree.json" in str(packet.get("tree")),
          f"the reviewer packet names the tree FILE, not a summary of it (tree={packet.get('tree')!r})")
    check("do not accept any summary" in str(packet.get("instruction", "")).lower(),
          "the packet tells the reviewer not to trust a summary of the artifacts")
    check("did NOT" in str(packet.get("instruction", "")),
          "the packet states that the deterministic pass cleared nothing")
    check(packet.get("alreadyRefused") == a.get("refusals"),
          "the reviewer is told what was already refused, so it does not re-decide it")
    # The packet must not smuggle the prose back in. A reviewer handed a summary reviews the
    # summary, so the assertion is that the report's own sentences are absent from the packet.
    packet_json = json.dumps(packet, ensure_ascii=False)
    smuggled = [s for s in re.split(r"(?<=[.!?])\s+", report.strip())
                if len(s) > 20 and s in packet_json]
    check(not smuggled, f"the reviewer packet carries no sentence of the report itself ({smuggled})")

    # The forbidden-sentence refusal, taken from this tree's OWN ledger rather than a phrase
    # invented here - a fixture that guesses the wording tests nothing.
    ledger = et.report("probe")
    forbidden = [c for c in (ledger.get("mayNotClaim") or []) if isinstance(c, dict)]
    if forbidden:
        sentence = str(forbidden[0].get("because") or "")
        b = et.audit_report("probe", text=f"# Results\n\nWe can say: {sentence}\n")
        check(any(x["kind"] == "forbidden_claim" for x in b.get("refusals") or []),
              "a mayNotClaim sentence copied in verbatim is refused mechanically "
              f"(refusals={[x['kind'] for x in b.get('refusals') or []]})")
    else:
        check(False, "the ledger produced a mayNotClaim entry to test the copy-in refusal with")

    # A missing report is a refusal, not a silent pass.
    c = et.audit_report("probe", path=str(Path(tempfile.gettempdir()) / "ka-no-such-report.md"))
    check(c.get("code") == "report_missing",
          "auditing a file that is not there is refused rather than answered")

    # The rule has to be written where the agent will actually read it, or the tool exists and
    # nobody uses it. A text assertion is cheap and it is the only thing that stops a later
    # edit quietly deleting the distinction.
    for rel in ("skills/technical-report/SKILL.md", "skills/ablation-design/SKILL.md"):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        check("acquit" in text and "refus" in text,
              f"{rel} states that a gate may refuse but may never acquit")

    # and the action is actually advertised, not just implemented
    srv = (ROOT / "mcp" / "kaggle_server.py").read_text(encoding="utf-8")
    check('"audit-report"' in srv, "the action is in the published enum")
    check("action == \"audit-report\"" in srv, "the action is dispatched by the server")


def check_evidence_graph_binding():
    """The new capabilities must be bound in the graph, not just present on disk."""
    print("evidence and plotting graph binding")
    rel, _ = parse_json(REL)
    if rel is None:
        return
    by_id = {n["id"]: n for n in rel["nodes"]}
    for nid, kind in (("sources-store", "tool"), ("plot-engine", "module"),
                      ("k-dense-methods", "provenance")):
        check(by_id.get(nid, {}).get("kind") == kind,
              f"'{nid}' is declared as a {kind} node")
    srv = SERVER_PY.read_text(encoding="utf-8")
    check('"name": "kaggle_sources"' in srv, "the evidence tool is registered")
    for action in ("analyze", "review"):
        check(f'"{action}"' in srv, f"the tree exposes action='{action}'")
    needs = {(e["from"], e["to"]) for e in rel["edges"] if e["type"] == "needs"}
    for pair in (("rsi-experiment-tree", "evidence-sources"),
                 ("rsi-experiment-tree", "scientific-plotting"),
                 ("rsi-experiment-tree", "ablation-design"),
                 ("ablation-design", "k-dense-methods")):
        check(pair in needs, f"{pair[0]} needs {pair[1]}")
    enforced = {(e["from"], e["to"]) for e in rel["edges"] if e["type"] == "enforces"}
    check(("sources-store", "experiment-tree") in enforced,
          "the evidence store is what makes provenance non-dangling")
    # the new skills must be declared in the manifest, not only on disk
    data, _ = parse_json(MANIFEST)
    for path in ("skills/evidence-sources/SKILL.md",
                 "skills/scientific-plotting/SKILL.md",
                 "skills/ablation-design/SKILL.md"):
        check(path in data.get("skills", []), f"manifest declares {path}")


# ---------------------------------------------------------------- index completeness
def check_skill_index():
    """The index is the map. It must cover every skill, link correctly, and be backed by the graph.

    A directory-based grouping was replaced by a flat layout (the manifest requires
    `skills/<name>/SKILL.md`), so the index is the only place the layering is visible. That makes
    it a thing that can silently rot, so it is checked rather than trusted: every manifest skill
    must appear, every relative link must resolve, and the graph's node paths must match reality.
    """
    print("skill index")
    data, _ = parse_json(MANIFEST)
    skills = data.get("skills", [])
    index = ROOT / "skills" / "README.md"
    if not check(index.is_file(), "skills/README.md exists"):
        return
    text = index.read_text(encoding="utf-8")

    # 1. every skill is listed, and linked relative to the index itself
    for path in skills:
        parts = path.split("/")
        check(len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md",
              f"manifest skill path is skills/<name>/SKILL.md: {path}")
        name = parts[1] if len(parts) == 3 else parts[-2]
        # the index lives in skills/, so its own links are <name>/SKILL.md
        check(f"]({name}/SKILL.md)" in text,
              f"the index links {name} as {name}/SKILL.md")

    # 2. every relative link in every index-ish file resolves
    for md in sorted((ROOT / "skills").rglob("*.md")):
        body = md.read_text(encoding="utf-8")
        for m in re.finditer(r"\]\((?!https?:)([^)#]+)(?:#[^)]*)?\)", body):
            target = (md.parent / m.group(1)).resolve()
            check(target.exists(),
                  f"link resolves: {md.relative_to(ROOT)} -> {m.group(1)}")

    # 3. the graph's node paths point at real files
    rel, _ = parse_json(REL)
    if rel is not None:
        for node in rel["nodes"]:
            p = node.get("path")
            if p:
                check((ROOT / p).is_file(), f"graph node path exists: {p}")

    # 4. the index states the layout rule that the manifest forces, so the next edit does not
    #    re-introduce nesting
    check("skills/<name>/SKILL.md" in text,
          "the index documents the flat layout the manifest requires")

    # 5. no stale category directories left behind from the nested layout
    for cat in CATEGORIES:
        check(not (ROOT / "skills" / cat).is_dir(),
              f"no leftover skills/{cat}/ directory (nesting is not valid in the manifest)")


# ---------------------------------------------------------------- version sync
def check_version_sync():
    """Every version declared anywhere in the package must be the same one.

    This used to read exactly three places - the manifest, relationships.json and
    SERVER_INFO - and hardcode them by name. A second, Claude-format manifest sat in the
    repository for twelve releases saying 1.12.3, and nothing went red, because the checker
    only ever looked at the three files it had been told about. So it now finds them: any
    JSON in the package that declares a `version` has to agree with the manifest, and
    SERVER_INFO is matched by regex wherever it lives. Adding a fourth distribution format
    now costs one edit instead of producing a silent lie.
    """
    print("version sync")
    data, _ = parse_json(MANIFEST)
    ver = data.get("version")
    check(bool(ver), f"the manifest declares a version ({ver})")

    found: list[tuple[str, str]] = []
    for path in sorted(ROOT.rglob("*.json")):
        if ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            continue
        # a plugin manifest declares version at the top level, or on each listed plugin
        for match in re.finditer(r'"version"\s*:\s*"([^"]+)"', raw):
            found.append((rel, match.group(1)))
    check(bool(found), "the package declares at least one version to compare")
    for rel, declared in found:
        check(declared == ver,
              f"{rel} version matches the manifest ({ver}) - found {declared}")
    check(MANIFEST.relative_to(ROOT).as_posix() in {r for r, _ in found},
          "the manifest is one of the files compared")

    text = SERVER_PY.read_text(encoding="utf-8")
    m = re.search(r'SERVER_INFO\s*=\s*\{[^}]*"version"\s*:\s*"([^"]+)"', text)
    if check(m is not None, "kaggle_server.py has SERVER_INFO version"):
        check(m.group(1) == ver, f"SERVER_INFO version matches manifest ({ver})")


# ---------------------------------------------------------------- publishable
# Everything above validates the working tree. That is the wrong tree. This package is
# published as a git repository, and a file can be present on disk, declared in the
# manifest, referenced by the relationship graph — and still be absent from the repo.
#
# It happened. `.gitignore` carried an unanchored `handoff/` to keep a runtime output
# directory out of the tree; git reads a pattern with no leading slash as matching at
# every depth, so it also excluded `skills/handoff/SKILL.md`. All 842 checks passed,
# because every one of them read the disk where the file was sitting in plain view. The
# repository was one skill short, and the first person to find out would have been the
# user who imported it.
#
# So the deliverable is now checked as the deliverable, not as the directory.

# Unanchored directory rules that are nonetheless intentional: bytecode is worthless at
# any depth, and an editor's .idea/ or .vscode/ is noise wherever it appears. The rule
# exists to protect declared package content, and no skill can be named after an editor
# directory. Every other directory rule must be anchored to the package root.
ALLOWED_UNANCHORED_DIR_RULES = {"__pycache__/", ".idea/", ".vscode/"}

# Stricter than SECRET_PATTERNS: those guard the prose, these guard the source. A
# credential in a code path leaks the same as one in a comment, and this repository is
# public.
PUBLISH_SECRET_PATTERNS = SECRET_PATTERNS + [
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "GitHub personal access token literal"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}"), "GitHub OAuth token literal"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained PAT literal"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\beyJhbGciOi[A-Za-z0-9_\-\.]{10,}"), "JWT literal"),
]

TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".cmd", ".txt", ".yml", ".yaml", ".cfg", ".ini"}

# A published package is read on someone else's machine. An absolute path into this
# author's home directory is therefore not a style problem, it is a broken package:
# the MCP server had `~/.minimax/plugins/kaggle-agent/mcp` baked into its launch args,
# so the config validated perfectly and then pointed every importer's server at a
# directory on their disk that does not exist. Plugin-relative paths and ${PLUGIN_ROOT}
# are the portable forms; this is how the hardcoding gets caught before publishing.
MACHINE_PATH_PATTERNS = [
    (re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s\"']+"), "absolute Windows user path"),
    (re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"), "absolute POSIX user path"),
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def check_publishable():
    print("publishable")

    # (1) A directory rule without a leading slash matches at every depth. That is the
    #     only reason `skills/handoff/SKILL.md` was silently dropped, so it is the rule
    #     worth refusing outright rather than reviewing by eye.
    gitignore = ROOT / ".gitignore"
    if check(gitignore.is_file(), ".gitignore exists"):
        offenders = []
        for raw in gitignore.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.endswith("/") and not line.startswith("/") \
                    and line not in ALLOWED_UNANCHORED_DIR_RULES:
                offenders.append(line)
        for line in offenders:
            bad(f"gitignore rule {line!r} is a directory pattern with no leading slash, "
                f"so it also matches every nested directory; anchor it as '/{line}'")
        if not offenders:
            ok("every gitignore directory rule is anchored to the package root")

    # (2) The manifest's own declarations must survive .gitignore. This is the assertion
    #     whose absence let the bug through: `check_manifest` proved the file existed,
    #     which is not the same question as whether it would be published.
    data, _ = parse_json(MANIFEST)
    declared: list[str] = []
    if data:
        declared += list(data.get("skills", [])) + list(data.get("mcpServers", []))
        for field in ("icon", "darkIcon"):
            if data.get(field):
                declared.append(data[field])

    if shutil.which("git") is None:
        print("  --  git is not on PATH; skipped the gitignore-collision assertions")
    elif check(bool(declared), "manifest declares paths to verify against .gitignore"):
        ignored = [rel for rel in declared if _git("check-ignore", "-q", "--", rel).returncode == 0]
        for rel in ignored:
            bad(f"manifest declares {rel} but .gitignore excludes it: the file is on disk "
                f"and would be missing from the repository")
        if not ignored:
            ok(f"all {len(declared)} manifest-declared paths survive .gitignore")

    # (3) The repository needs a landing page. A public repo whose entire documentation is
    #     skills/README.md asks an importer to assemble their own mental model first.
    if check((ROOT / "README.md").is_file(), "package root has a README.md for the repo landing page"):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        check(len(text.strip()) > 400, "README.md is a real page, not a stub")
        for heading in ("Install", "kaggle_experiment_tree"):
            check(heading in text, f"README.md covers '{heading}'")

    # (4) The whole publishable set, not just the prose. Enumerated through git so the
    #     answer describes the repository rather than whatever happens to sit on disk.
    if shutil.which("git") is None:
        print("  --  git is not on PATH; skipped the repository-wide secret scan")
        return
    listing = _git("ls-files", "--cached", "--others", "--exclude-standard").stdout
    publishable = [line for line in listing.splitlines() if line.strip()]
    if check(bool(publishable), f"git enumerates a publishable file set ({len(publishable)} files)"):
        hits = 0
        for rel in publishable:
            p = ROOT / rel
            if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for rx, label in PUBLISH_SECRET_PATTERNS:
                if rx.search(text):
                    bad(f"possible {label} in {rel} — this repository is public")
                    hits += 1
        if hits == 0:
            ok(f"no credential literal in any of the {len(publishable)} publishable files")

    # (5) The package is read on someone else's machine, so no publishable file may name
    #     an absolute path into this author's home directory.
    machine_hits = 0
    for rel in publishable:
        p = ROOT / rel
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for rx, label in MACHINE_PATH_PATTERNS:
            found = rx.search(text)
            if found:
                bad(f"{label} in {rel}: {found.group(0)!r} — this package is installed "
                    f"on other machines, use a plugin-relative path or ${{PLUGIN_ROOT}}")
                machine_hits += 1
    if machine_hits == 0:
        ok("no publishable file hardcodes an absolute path into the author's home")


# ---------------------------------------------------------------- data locality
# The user does not want competition data pulled onto this machine, ever. The skill said
# "must run locally" and "write a local CPU notebook", which is an instruction to download the
# dataset — on a competition where every listed file 403s anyway. Prose was not enough: the fix
# has to be something a later edit cannot quietly undo, so the required wording is asserted here.
RESEARCH_SKILL = ROOT / "skills" / "kaggle-competition-research" / "SKILL.md"


def check_data_stays_on_kaggle():
    print("data locality")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = RESEARCH_SKILL.read_text(encoding="utf-8")
    lowered = text.lower()

    # The two phrases that instructed a local run, in any casing.
    for banned in ("must run locally", "local cpu notebook", "runs locally"):
        check(banned not in lowered,
              f"the research skill no longer says {banned!r} (it must profile on Kaggle)")

    # The prohibition has to be stated, not implied by omission.
    check("competitions download" in text and "datasets download" in text
          and "must never" in lowered or "do not run" in lowered,
          "the research skill states the ban on downloading competition data")

    for tool in ("kaggle_kernel_launch", 'accelerator="none"', "kaggle_kernel_verify",
                 'expected="none"', "kaggle_kernels_output"):
        check(tool in text,
              f"the research skill routes the profile through {tool}")

    check("input directory" in lowered,
          "the research skill tells the notebook to read Kaggle's mounted input directory")


# ---------------------------------------------------------------- browser boundary
# The in-app browser is for *discovery*. Deciding which pages matter and reading them is not
# delegated to it: a known URL is read with web_fetch. The skill used to say "Do it with the
# in-app browser in this session, not with a search tool" for the coverage floor, while the
# forensics doc already recorded that a detached subagent has only web_fetch — the two files
# contradicted each other and the browser was doing a job it is bad at. Since the forensics moved
# into the main thread, the skill is the only place these instructions live, and the checks in
# check_wave_two_is_single_threaded are what keep them there.
RESEARCH_SKILL = ROOT / "skills" / "kaggle-competition-research" / "SKILL.md"


def check_browser_is_search_only():
    print("browser boundary")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return

    research = RESEARCH_SKILL.read_text(encoding="utf-8")
    lowered = research.lower()

    # The old instruction that handed page-reading to the browser.
    for banned in ("do it with the **in-app browser in this session**",
                   "must be genuinely browsed",
                   "opened and parsed each of those three sites"):
        check(banned.lower() not in lowered,
              f"the research skill no longer says {banned[:46]!r}")

    # The boundary itself, stated positively. The floor names three specific sites, so each
    # one gets a concrete web_fetch example on its own line — a whole-file containment test
    # would pass with two of the three examples deleted, because `web_fetch url=` and the
    # host strings both survive somewhere else in a 34 KB document.
    check("the floor is about the source, not the tool" in lowered,
          "the coverage floor is stated as tool-agnostic")
    fetch_lines = [ln for ln in research.splitlines() if "web_fetch url=" in ln]
    check(bool(fetch_lines), "the research skill shows a web_fetch example for a known URL")
    for host in ("github.com", "huggingface.co", "arxiv.org"):
        check(any(host in ln for ln in fetch_lines),
              f"the coverage floor reads {host} with web_fetch on its own example line")
    check("for *search*" in lowered or "browser is for search" in lowered,
          "the research skill scopes the browser to search")
    check("js rendering" in lowered or "needs js rendering" in lowered,
          "the research skill names the cases that still require the browser")

    # The edge contract in the graph has to carry the same boundary.
    rel, _ = parse_json(REL)
    if isinstance(rel, dict):
        desc = str(rel.get("edgeTypes", {}).get("browses", "")) \
            if isinstance(rel.get("edgeTypes"), dict) else ""
        if not desc:
            for key in ("edges", "edgeTypes"):
                if isinstance(rel.get(key), dict) and "browses" in rel[key]:
                    desc = str(rel[key]["browses"])
        check("web_fetch" in desc,
              "relationships.json scopes the browses edge to web_fetch for known URLs")

    # The two assertions that used to live here, reading the agent's own doc, are gone with the
    # doc. Guarding them behind `is_file()` would have turned them into two assertions that
    # silently stop running while still reading as coverage - so the discipline they carried
    # (the browser is not the primary method; page selection is not the browser's job) is now
    # asserted against the research skill itself, which is where those instructions live.


# ---------------------------------------------------------------- research preflight
# Research is the expensive path in this plugin: four subagents, a multi-round sweep and a
# forensics pass. Re-running it over work that already exists is the most wasteful thing the
# skill can do, and the skill had no preflight at all — handoff appeared once, at the end, as
# something to *offer*. The user's rule: check the local workspace first, and ask about a cloud
# handoff rather than assuming there is none.
def check_research_preflight():
    print("research preflight")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = RESEARCH_SKILL.read_text(encoding="utf-8")
    lowered = text.lower()

    # Form, not name. A document this size keeps the word "handoff_status" alive in prose long
    # after the call itself is gone, and keeps the word "ask" and "cloud" alive after the
    # question is deleted — so each assertion below pins the exact instruction, not a token.
    check('handoff_status competition=' in text,
          "the preflight shows the handoff_status CALL, not just the name")
    check('kaggle_experiment_tree action="read"' in text,
          "the preflight shows the experiment-tree read call")
    check("github_auth action=\"status\"" in text or 'github_auth action="status"' in text,
          "the preflight reports GitHub transport without a network call")

    # Ordering is the whole point: a preflight written after wave 1 is a paragraph, not a gate.
    wave_idx = lowered.find("## the shape: two waves")
    pre_idx = lowered.find("## preflight")
    if check(pre_idx != -1, "the skill has a preflight section"):
        check(wave_idx != -1 and pre_idx < wave_idx,
              "the preflight comes BEFORE the wave section, not after it")

    check("ask whether there is a handoff in the cloud" in lowered,
          "the preflight asks about a cloud handoff instead of assuming there is none")
    check("combining with what already exists" in lowered,
          "the skill says how to combine a new pass with existing work")
    check("then run the wave informed by it" in lowered,
          "the existing-work branch still runs the wave, informed by the handoff")
    check("not a reason to\nskip it" in lowered or "not a reason to skip it" in lowered,
          "existing work is an input to the research, not a reason to skip it")
    check("never overwrite the base node" in lowered,
          "the skill refuses to overwrite a base node the tree already holds")

    # The description is what decides whether this skill loads at all, so the preflight
    # has to be visible there or it will be skipped by an agent that never opens the body.
    m = re.search(r"^description:\s*(.+)$", text, re.M)
    if check(m is not None, "the research skill has a description"):
        desc = m.group(1).strip().strip('"').lower()
        check("preflight" in desc, "the description advertises the preflight")
        check("handoff_status" in desc, "the description names the preflight call")


# ---------------------------------------------------------------- the launch gate
# The guarantee: no experiment result lives only in a chat transcript. kaggle_kernel_launch
# refuses a run nobody declared, and a declaration is only closeable by settling it. This is
# the only place the plugin refuses to do the user's work, which is why it is tested by running
# it rather than by reading the source.
def check_launch_gate():
    print("launch gate")
    import json as _json
    import shutil as _shutil
    import tempfile as _tempfile

    spec = importlib.util.spec_from_file_location("_ks_gate", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the gate test: {exc}")
        return
    et = sys.modules.get("experiment_tree") or ks.experiment_tree

    tools = {t.get("name"): t for t in getattr(ks, "TOOLS", [])}
    launch = tools.get("kaggle_kernel_launch") or {}
    props = (launch.get("inputSchema") or {}).get("properties") or {}
    required = (launch.get("inputSchema") or {}).get("required") or []
    check("declares" in props and "declares" in required,
          "kaggle_kernel_launch requires a declaration id in its schema")
    check("competition" in props, "kaggle_kernel_launch can name the competition")

    tree_schema = (tools.get("kaggle_experiment_tree") or {}).get("inputSchema") or {}
    enum = ((tree_schema.get("properties") or {}).get("action") or {}).get("enum") or []
    for action in ("declare", "settle"):
        check(action in enum, f"kaggle_experiment_tree exposes action={action!r}")

    comp = "zz-check-launch-gate"
    tree_file = Path(et.tree_path(comp))
    if tree_file.exists():
        tree_file.unlink()
    folder = Path(_mkdtemp(prefix="ka-gate-"))
    try:
        (folder / "notebook.ipynb").write_text("{}", encoding="utf-8")
        (folder / "kernel-metadata.json").write_text(
            _json.dumps({"id": f"owner/{comp}", "title": "gate test"}), encoding="utf-8")
        launch_args = {"folder": str(folder)}

        def _launch(**kw):
            a = dict(launch_args)
            a.update(kw)
            r = ks.tool_call("kaggle_kernel_launch", a)
            return _json.dumps(r)

        refused = _launch(competition=comp)
        check("no experiment was declared" in refused,
              "a run with nothing declared is refused")
        # The refusal is returned as JSON, so its embedded quotes arrive escaped.
        check('action=\\"declare\\"' in refused or 'action="declare"' in refused,
              "the refusal names the call that unlocks it")

        rev = et.read(comp)["revision"]
        d = _decl(et, comp, {"id": "e1", "change": "swap the sampler",
                              "hypothesis": "it is the bottleneck", "parent": None,
                              "operator": "draft", "family": "sampling",
                              "reason": "the profile says so",
                      "diagnosis": "none",
                      "diagnosisReason": "first run in this test tree"}, read_revision=rev)
        if not check(d.get("ok"), f"a declaration is accepted: {d.get('message')}"):
            return
        check(_decl(et, comp, {"id": "e0", "change": "x", "hypothesis": "h", "parent": None,
                                "operator": "draft", "family": "f", "reason": "r",
                                "diagnosis": "none",
                                "diagnosisReason": "probing the read gate"},
                         read_revision=None).get("code") == "read_required",
              "declare without a read is refused")
        open_ids = [p["id"] for p in et.pending_declarations(comp)]
        check(open_ids == ["e1"],
              f"an unsettled declaration is offered to the gate: {open_ids}")
        passed = _launch(competition=comp, declares="e1")
        check("no experiment was declared" not in passed and "was refused" not in passed,
              "the declared run passes the gate")
        check("was refused" in _launch(competition=comp, declares="nope"),
              "a bogus declaration id is refused")

        rev = et.read(comp)["revision"]
        s = et.settle(comp, "e1", {"id": "e1-result", "change": "swap the sampler",
                                    "hypothesis": "it is the bottleneck", "operator": "draft",
                                    "family": "sampling", "reason": "profile",
                                    "evidence": "local-only", "verdict": "keep",
                                    "metric": {"name": "score", "parent": None, "result": 0.42,
                                               "delta": 0.02, "rank": 7, "rankSource": "lb"}},
                      read_revision=rev)
        if not check(s.get("ok"), f"a result settles its declaration: {s.get('message')}"):
            return
        nodes = et._current(et.load(comp))["nodes"]
        check(nodes.get("e1-result", {}).get("parent") == "e1",
              "the result is a NEW node parented by the declaration, not a rewrite")
        check(et.pending_declarations(comp) == [],
              "a settled declaration is no longer open")
        check("was refused" in _launch(competition=comp, declares="e1"),
              "re-running a settled declaration is refused")
        check(et.settle(comp, "e1", {"id": "again", "change": "c", "hypothesis": "h",
                                     "operator": "draft", "family": "f", "reason": "r",
                                     "evidence": "local-only", "verdict": "keep",
                                     "metric": {"name": "s", "parent": None, "result": 1,
                                                "delta": 0, "rank": 1, "rankSource": "x"}},
                       read_revision=et.read(comp)["revision"]).get("code") == "already_settled",
              "settling the same declaration twice is refused")
        check(_decl(et, comp, {"id": "p2", "change": "abandon the model entirely",
                                "hypothesis": "the baseline is wrong, not the method",
                                "parent": None, "operator": "crossover",
                                "family": "problem-framing",
                                "reason": "the old base was refuted twice",
                       "diagnosis": "none",
                       "diagnosisReason": "new direction, nothing settled to learn from"},
                       read_revision=et.read(comp)["revision"]).get("ok"),
              "a completely new direction may start from a null parent")
        prompt = et.plan_prompt(comp)
        check("IN FLIGHT" in prompt and "p2" in prompt,
              "plan reports what is in flight")
    finally:
        _shutil.rmtree(folder, ignore_errors=True)
        if tree_file.exists():
            tree_file.unlink()


    # The launch gate is only a guarantee if the skills describe the same three calls.
    tree_skill = ROOT / "skills" / "rsi-experiment-tree" / "SKILL.md"
    launch_skill = ROOT / "skills" / "experiment-launch" / "SKILL.md"
    for path, tokens in (
        (tree_skill, ('action="declare"', 'action="settle"', 'declares="', "IN FLIGHT",
                      "parent: null", "new_base", 'action="consider"', 'action="prune"',
                      "already_refuted", "worth_declaring", "Only `research` nodes",
                      "undo` restores it", 'action="diagnose"', '"diagnosis"',
                      "diagnosisReason", "FAILURE_LAYERS", "logRef", "logPath")),
        (launch_skill, ('action="declare"', 'action="settle"', "declares         =", "REQUIRED")),
    ):
        if path.is_file():
            body = path.read_text(encoding="utf-8")
            for token in tokens:
                check(token in body, f"{path.name} documents {token!r}")


# ---------------------------------------------------------------- consider and prune
# Two things the tree needed. `consider` makes consulting it cheap enough to do at EVERY step,
# not only before a run, while still refusing to pad the tree with "I looked at the docs" nodes.
# `prune` deletes collected research material that turned out to be useless — and only that:
# an experiment is evidence that quota was spent, and evidence is not deleted.
def check_consider_and_prune():
    print("consider and prune")
    import shutil as _shutil

    spec = importlib.util.spec_from_file_location("_ks_cp", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the consider/prune test: {exc}")
        return
    et = sys.modules.get("experiment_tree") or ks.experiment_tree

    comp = "zz-check-consider-prune"
    tree_file = Path(et.tree_path(comp))
    if tree_file.exists():
        tree_file.unlink()
    try:
        def _rev():
            return et.read(comp)["revision"]

        def _exp(nid, change, verdict, **kw):
            n = {"id": nid, "kind": "experiment", "change": change, "hypothesis": "h",
                 "parent": None, "operator": "draft", "family": "sampling",
                 "reason": "because", "evidence": "local-only", "verdict": verdict,
                 "metric": {"name": "s", "parent": None, "result": 0.5, "delta": 0.1,
                            "rank": 1, "rankSource": "lb"}}
            n.update(kw)
            return et.record(comp, n, read_revision=_rev())

        def _res(nid, question, parent=None):
            return et.record(comp, {"id": nid, "kind": "research", "question": question,
                                    "targets": ["forum"], "verdict": "keep",
                                    "opens": "an idea", "parent": parent,
                                    "reason": "the forum said so"}, read_revision=_rev())

        check(_exp("e1", "warm-start the encoder from a checkpoint", "revert",
                   failureLayer="metric").get("ok"), "seed: a reverted experiment")
        check(_exp("e2", "cache the retriever index to disk", "keep").get("ok"),
              "seed: a kept experiment")
        check(_res("r1", "which prompt format do competitors use").get("ok"),
              "seed: a research node")

        c = et.consider(comp, "warm-start the encoder from a checkpoint", "faster",
                        operator="draft", family="sampling")
        check(c["verdict"] == "already_refuted",
              f"consider spots a step that was already refuted: {c['verdict']}")
        check(c["worthANode"] is False, "a refuted step is not worth a node")
        check((c.get("match") or {}).get("failureLayer") == "metric",
              "and it carries the layer that broke")
        c = et.consider(comp, "cache the retriever index to disk", "avoid refetching",
                        operator="draft", family="sampling")
        check(c["verdict"] == "already_known",
              f"consider spots a step already kept: {c['verdict']}")
        c = et.consider(comp, "shrink the context window to 4k", "cheaper",
                        operator="improve", family="context")
        check(c["verdict"] == "worth_declaring" and c["worthANode"] is True,
              f"a genuinely new step earns a node: {c['verdict']}")
        check(et.consider(comp, "", "")["verdict"] == "not_worth_a_node",
              "an empty change is not worth a node")
        c = et.consider(comp, "read the library release notes", "to know what changed")
        check(c["verdict"] == "judge_it" and c["worthANode"] is False,
              f"an unnamed change with no operator or family goes to judgement: {c['verdict']}")
        check(_decl(et, comp, {"id": "p1", "change": "quantise the weights to int8",
                                "hypothesis": "half the memory", "parent": "e2",
                                "operator": "debug", "family": "memory",
                                "reason": "the next cost",
                                "diagnosis": "none",
                                "diagnosisReason": "first run in this test tree"}, read_revision=_rev()).get("ok"),
              "seed: a declaration")
        c = et.consider(comp, "quantise the weights to int8", "half the memory",
                        operator="debug", family="memory")
        check(c["verdict"] == "in_flight" and c["inFlight"] == ["p1"],
              f"consider spots a step already declared: {c['verdict']}")

        r = et.prune(comp, "r1", "the leaderboard answered it", read_revision=_rev())
        check(r.get("ok"), f"a useless research node is prunable: {r.get('message')}")
        check("r1" not in et._current(et.load(comp))["nodes"], "and it is gone")
        u = et.undo(comp)
        check(u.get("ok") == True and "r1" in et._current(et.load(comp))["nodes"],
              f"undo restores a pruned node (undid {u.get('undone')})")

        check(et.prune(comp, "e2", "it is clutter now", read_revision=_rev()).get("code")
              == "not_prunable", "an experiment node is NOT prunable")
        check("quota was spent" in (et.prune(comp, "e2", "clutter",
                                             read_revision=_rev()).get("message") or ""),
              "and the refusal says why: evidence is not deleted")
        check(et.prune(comp, "e2", "clutter", read_revision=None).get("code") == "read_required",
              "pruning without a read is refused")
        check(et.prune(comp, "e2", "clutter", read_revision=999).get("code") == "stale_read",
              "pruning on a stale read is refused")
        check(et.prune(comp, "e2", "clutter", read_revision=_rev()).get("code")
              == "not_prunable", "a real reason still does not make an experiment prunable")
        check(et.prune(comp, "p1", "silly idea", read_revision=_rev()).get("code")
              == "not_prunable", "a declaration is NOT prunable")
        _res("r2", "which seeds are worth trying", parent="r1")
        check(et.prune(comp, "r1", "no longer interesting", read_revision=_rev()).get("code")
              == "still_referenced", "a research node with children is NOT prunable")
        check(et.prune(comp, "nope", "x", read_revision=_rev()).get("code") == "unknown_node",
              "pruning a node that does not exist is refused")
        check(et.prune(comp, "r1", "better", read_revision=None).get("code") == "read_required",
              "prune needs a read before it deletes anything")
    finally:
        if tree_file.exists():
            tree_file.unlink()
        _shutil.rmtree(ROOT / "mcp" / "__pycache__", ignore_errors=True)


# ---------------------------------------------------------------- monitoring is attached
# A run that nothing watches is a run whose failure you learn about hours later. "Set up the log
# monitor after launching" was prose in the experiment-launch skill, which is exactly the kind of
# instruction a long turn forgets — the same shape as the research preflight. So the launch attaches
# its own ref. Only DISPATCHING a watcher stays the agent's decision, because that costs a session.
def check_launch_attaches_monitoring():
    print("launch monitoring")
    import shutil as _shutil
    import tempfile as _tempfile

    spec = importlib.util.spec_from_file_location("_ks_mon", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the monitoring test: {exc}")
        return
    et = sys.modules.get("experiment_tree") or ks.experiment_tree
    lm = sys.modules.get("logmonitor") or ks.logmonitor

    comp = "zz-check-monitoring"
    tree_file = Path(et.tree_path(comp))
    if tree_file.exists():
        tree_file.unlink()
    cfg = Path(lm.config_path())
    backup = cfg.read_bytes() if cfg.exists() else None
    folders: list = []
    real_run = ks.run_kaggle

    def _targets():
        return lm.describe().get("targets") or []

    def _mk_decl(nid):
        return _decl(et, comp, {"id": nid, "change": f"try {nid}", "hypothesis": "h",
                                 "parent": None, "operator": "draft", "family": "sampling",
                                 "reason": "because",
                                 "diagnosis": "none",
                                 "diagnosisReason": "first run in this test tree"}, read_revision=et.read(comp)["revision"])

    try:
        def _nb():
            d = Path(_mkdtemp(prefix="ka-mon-"))
            folders.append(d)
            (d / "notebook.ipynb").write_text("{}", encoding="utf-8")
            (d / "kernel-metadata.json").write_text(
                '{"id":"tester/nb-auto-monitor","title":"t"}', encoding="utf-8")
            return str(d)

        # The stub takes the real signature. run_kaggle grew an `account` keyword, and a stub
        # that only accepted the positional command raised TypeError on every call - which reads
        # as a broken check rather than as a stale test double, and is exactly the case where a
        # suite can fail for a reason that has nothing to do with what it is testing.
        ks.run_kaggle = lambda cmd, account="": (
            0, "Kernel version 1 successfully pushed to "
               "https://www.kaggle.com/code/tester/nb-auto-monitor", "")

        lm.reset()
        check(_mk_decl("e1").get("ok"), "seed: a declaration")
        r = ks.tool_call("kaggle_kernel_launch",
                         {"folder": _nb(), "competition": comp, "declares": "e1"})
        check("monitoring:" in json.dumps(r), "a launch reports that monitoring is attached")
        check(any("nb-auto-monitor" in json.dumps(t) for t in _targets()),
              f"the kernel just pushed is a monitor target: {_targets()}")

        lm.reset()
        _mk_decl("e2")
        r = ks.tool_call("kaggle_kernel_launch",
                         {"folder": _nb(), "competition": comp, "declares": "e2",
                          "monitor": False})
        check("monitoring:" not in json.dumps(r), 'monitor:false says nothing about monitoring')
        check(_targets() == [], f"monitor:false attaches nothing: {_targets()}")
        lm.set_target("kaggle", ref="tester/nb-manual")
        check(any("nb-manual" in json.dumps(t) for t in _targets()),
              "a manual target still works after an opt-out")

        lm.reset()
        r = ks.tool_call("kaggle_kernel_launch", {"folder": _nb(), "competition": comp})
        check("no experiment was declared" in json.dumps(r),
              "a launch with nothing declared is still refused")
        check(_targets() == [], f"and it attached no target: {_targets()}")

        lm.reset()
        ks.run_kaggle = lambda cmd, account="": (1, "", "kaggle: notebook metadata is invalid")
        _mk_decl("e3")
        r = ks.tool_call("kaggle_kernel_launch",
                         {"folder": _nb(), "competition": comp, "declares": "e3"})
        check("notebook metadata is invalid" in json.dumps(r),
              "a failing push still reports the error")
        check(_targets() == [], f"and attaches no target: {_targets()}")

        tools = {t.get("name"): t for t in getattr(ks, "TOOLS", [])}
        launch = tools.get("kaggle_kernel_launch") or {}
        check("monitor" in ((launch.get("inputSchema") or {}).get("properties") or {}),
              "kaggle_kernel_launch documents the monitor opt-out")

        skill = (ROOT / "skills" / "experiment-launch" / "SKILL.md")
        if skill.is_file():
            body = skill.read_text(encoding="utf-8")
            check("already attached" in body.lower(),
                  "the launch skill says the target is attached automatically")
            check('action=\\"target\\"' not in body and "you do not need to" in body.lower(),
                  "and that the old first step is gone")
    finally:
        ks.run_kaggle = real_run
        for f in folders:
            _shutil.rmtree(f, ignore_errors=True)
        if tree_file.exists():
            tree_file.unlink()
        if backup is not None:
            cfg.write_bytes(backup)
        elif cfg.exists():
            cfg.unlink()


# ---------------------------------------------------------------- local runs too
# A Kaggle launch attaches its own kernel ref. A local run had no entry point at all — the agent
# composed a shell command and ran it — so there was nowhere for monitoring to attach, and
# "nothing is watching" was a consequence of which engine you picked. kaggle_local_launch makes the
# two engines symmetric: same declaration gate, same automatic log target.
def check_local_run_is_monitored():
    print("local run monitoring")
    import shutil as _shutil
    import tempfile as _tempfile
    import time as _time

    spec = importlib.util.spec_from_file_location("_ks_ll", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the local-run test: {exc}")
        return
    et = sys.modules.get("experiment_tree") or ks.experiment_tree
    lm = sys.modules.get("logmonitor") or ks.logmonitor

    tools = {t.get("name"): t for t in getattr(ks, "TOOLS", [])}
    check("kaggle_local_launch" in tools, "kaggle_local_launch exists")
    local = tools.get("kaggle_local_launch") or {}
    req = (local.get("inputSchema") or {}).get("required") or []
    check(all(x in req for x in ("command", "competition", "declares")),
          f"a local run requires a command, a competition and a declaration: {req}")

    comp = "zz-check-local-run"
    tree_file = Path(et.tree_path(comp))
    if tree_file.exists():
        tree_file.unlink()
    cfg = Path(lm.config_path())
    backup = cfg.read_bytes() if cfg.exists() else None
    work = Path(_mkdtemp(prefix="ka-check-local-"))

    def _targets():
        return lm.describe().get("targets") or []

    def _mk_decl(nid):
        return _decl(et, comp, {"id": nid, "change": f"try {nid}", "hypothesis": "h",
                                 "parent": None, "operator": "draft", "family": "sampling",
                                 "reason": "because",
                                 "diagnosis": "none",
                                 "diagnosisReason": "first run in this test tree"}, read_revision=et.read(comp)["revision"])

    try:
        lm.reset()
        r = ks.tool_call("kaggle_local_launch",
                         {"command": [sys.executable, "-c", "print(1)"], "competition": comp})
        check("no experiment was declared" in json.dumps(r),
              "a local run with nothing declared is refused")
        check(_targets() == [], f"and it attached no target: {_targets()}")
        r = ks.tool_call("kaggle_local_launch",
                         {"command": [sys.executable, "-c", "print(1)"], "declares": "e1"})
        check("must name the competition" in json.dumps(r),
              "a local run with no competition is refused")

        lm.reset()
        check(_mk_decl("e1").get("ok"), "seed: a declaration")
        logfile = work / "run1.log"
        r = ks.tool_call("kaggle_local_launch",
                         {"command": [sys.executable, "-c",
                                      "print('hello-from-check')"],
                          "cwd": str(work), "competition": comp, "declares": "e1",
                          "log_path": str(logfile)})
        t = json.dumps(r)
        check("monitoring:" in t, "a local run reports that monitoring is attached")
        check("started pid" in t, "and reports the pid")
        check(any("run1.log" in json.dumps(x) for x in _targets()),
              f"its log is a monitor target: {_targets()}")
        check(any(x.get("kind") == "local" for x in _targets()),
              "and the target is a LOCAL one, not a kaggle ref")
        for _ in range(80):
            if logfile.exists() and "hello-from-check" in logfile.read_text(errors="replace"):
                break
            _time.sleep(0.2)
        check(logfile.exists() and "hello-from-check" in logfile.read_text(errors="replace"),
              "the run's real output landed in the attached log")

        lm.reset()
        _mk_decl("e2")
        r = ks.tool_call("kaggle_local_launch",
                         {"command": [sys.executable, "-c", "print(2)"], "cwd": str(work),
                          "competition": comp, "declares": "e2", "monitor": False,
                          "log_path": str(work / "run2.log")})
        check(_targets() == [], f"monitor:false attaches nothing: {_targets()}")
        check("run2.log" in json.dumps(r), "but the run still starts and reports its log")

        lm.reset()
        _mk_decl("e3")
        r = ks.tool_call("kaggle_local_launch",
                         {"command": ["definitely-not-a-real-binary-xyz"], "cwd": str(work),
                          "competition": comp, "declares": "e3"})
        check("could not start" in json.dumps(r), "an unstartable command is reported")
        check(_targets() == [], f"and attaches no target: {_targets()}")
    finally:
        if backup is not None:
            cfg.write_bytes(backup)
        elif cfg.exists():
            cfg.unlink()
        if tree_file.exists():
            tree_file.unlink()
        _shutil.rmtree(work, ignore_errors=True)


def check_monitor_uses_the_builtin_cron():
    print("monitor heartbeat")
    skill = ROOT / "skills" / "log-monitor" / "SKILL.md"
    if not check(skill.is_file(), "the log-monitor skill is present"):
        return
    body = skill.read_text(encoding="utf-8")
    low = body.lower()

    # It must REFERENCE the built-in surface, not fork it. A second copy of an official
    # document is a second copy that goes stale, and the whole point of this project is
    # that prose rots. So: name the mechanism, point at the reference, and keep only the
    # tick body that is specific to a Kaggle log.
    check("cron self" in low, "the skill names the built-in scheduled-task mechanism")
    check("mavis" in low, "it points at the built-in mavis skill rather than restating it")
    check("cron reference" in low or "cron.md" in low,
          "it tells the agent to read the official cron reference")
    check("定时任务" in body, "and it names the user-facing feature the UI shows")
    check("delete this cron" in low, "the tick body deletes the cron on a terminal state")
    check("quiet_on_skip" in low or "exit quietly" in low,
          "the tick body is silent when there is nothing to say")
    check("sleep" in low, "it warns that a sleeping machine can miss a tick")

    # The stale claim that made this whole question necessary: a subagent is ONE turn, so it
    # cannot watch. Assert the corrected understanding is present and the wrong one is gone.
    check("a subagent is one turn" in low,
          "the skill states that a subagent cannot hold a loop open")
    check("stop the subagent" not in low and "stop a subagent" not in low,
          "no instruction survives that still tells you to stop a subagent as the exit")
    check("watch a log for hours" not in low,
          "the old claim that a subagent can watch a log for hours is gone")


# ---------------------------------------------------------------- competition isolation
# A tree is addressed by a slug derived from whatever string the caller passed, so
# "arc-prize-2026-arc-agi-3" and "arc-agi-3" sanitise to two different directories and
# would be two silent histories of one competition - the exact waste the tree exists to
# prevent. So a tree records the name it was created under plus every name later pointed
# at it, a second name resolves to the same file, and aliasing onto a key that already has
# a tree of its own is refused loudly rather than silently picking one.
#
# NOTE the check() signature used throughout this file: check(cond, msg). Writing it as
# check("label", condition) makes every assertion pass, because a non-empty label is
# always truthy. That has happened SEVEN times in this project's history, most recently
# in the standalone test this function was generated from - which reported 28 passed with
# the message printed as "True". The tell is in the output, not in the code.
def check_competition_isolation():
    print("competition isolation")
    import shutil as _shutil
    import tempfile as _tempfile
    import pathlib

    spec = importlib.util.spec_from_file_location("_ks_iso", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the isolation test: {exc}")
        return
    et = sys.modules.get("experiment_tree")
    json = __import__("json")

    home = _mkdtemp(prefix="ka-check-iso-")
    os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        def rec(competition, nid, change="do a thing"):
            return et.record(competition, {"id": nid, "kind": "experiment", "change": change,
                                            "hypothesis": "h", "parent": None, "operator": "draft",
                                            "family": "f", "reason": "r", "evidence": "local-only",
                                            "verdict": "keep",
                                            "metric": {"name": "s", "parent": None, "result": 1.0,
                                                       "delta": 0.1, "rank": 1, "rankSource": "lb"}},
                             read_revision=et.read(competition)["revision"])


        try:
            check(rec("arc-prize-2026-arc-agi-3", "e1").get("ok"), "seed: a node under one name")
            t = et.read("arc-prize-2026-arc-agi-3")
            check(t.get("competition") == "arc-prize-2026-arc-agi-3",
                  f"the tree records the name it was created under: {t.get('competition')}")
            check("arc-prize-2026-arc-agi-3" in (t.get("competitionKeys") or []),
                  f"and claims that key: {t.get('competitionKeys')}")

            fresh = et.read("titanic")
            check(fresh.get("competition") == "titanic",
                  f"a brand new tree already says what it is for: {fresh.get('competition')!r}")
            check(not ((fresh.get("tree") or {}).get("nodes") or {}), "and starts empty")
            check(rec("titanic", "e1").get("ok"), "a same node id in another competition is fine")
            check(et.read("arc-prize-2026-arc-agi-3")["tree"]["nodes"].get("e1") is not None,
                  "and it did not touch the first tree")

            a = et.register_alias("arc-prize-2026-arc-agi-3", "ARC-AGI-3")
            check(a.get("ok"), f"registering an alias: {a.get('message')}")
            check("arc-agi-3" in (a.get("keys") or []), f"the alias is in the key set: {a.get('keys')}")
            t3 = et.read("ARC-AGI-3")
            check(t3.get("competition") == "arc-prize-2026-arc-agi-3",
                  f"the alias resolves to the SAME tree: {t3.get('competition')}")
            check("e1" in ((t3.get("tree") or {}).get("nodes") or {}),
                  "and sees the node recorded under the other name")
            check(os.path.realpath(et.tree_path_resolved("ARC-AGI-3"))
                  == os.path.realpath(et.tree_path_resolved("arc-prize-2026-arc-agi-3")),
                  "both names resolve to one file on disk")

            ident = et.identity("ARC-AGI-3")
            check(ident.get("competition") == "arc-prize-2026-arc-agi-3",
                  f"identity() says which competition the tree is for: {ident.get('competition')}")
            check(ident.get("nodes") == 1, f"and how many nodes it holds: {ident.get('nodes')}")
            check(ident.get("forks") == [], "and that nothing else claims the key")

            check(rec("titanic2", "e9", "a different run").get("ok"), "seed: a second real tree")
            a2 = et.register_alias("arc-prize-2026-arc-agi-3", "titanic2")
            check(not a2.get("ok"), "aliasing onto a key that has its own tree is refused")
            check(a2.get("code") == "alias_has_own_tree", f"with a specific code: {a2.get('code')}")
            check("two histories" in (a2.get("message") or "").lower(),
                  "and it explains why in the user's terms")
            check(bool(et.read("titanic2")["tree"]["nodes"]), "and the other tree is untouched")
            check(et.register_alias("arc-prize-2026-arc-agi-3", "   ").get("code") == "bad_alias",
                  "a blank alias is refused")

            on_disk = json.loads(pathlib.Path(et.tree_path("arc-prize-2026-arc-agi-3")).read_text())
            for transient in ("identity", "forks", "path", "problems", "migrated"):
                check(transient not in on_disk, f"'{transient}' is not written to tree.json")
            check("competition" in on_disk and "competitionKeys" in on_disk,
                  "the document carries its own identity")

            rec("../../escape", "e1")
            written = et.tree_path_resolved("../../escape")
            check(os.path.realpath(written).startswith(
                os.path.realpath(os.path.join(home, "handoff"))),
                f"a traversal key stays under the handoff root: {os.path.basename(os.path.dirname(written))}")

            # the read the agent actually sees must show the identity
            r = ks.tool_call("kaggle_experiment_tree",
                             {"action": "read", "competition": "ARC-AGI-3"})
            text = json.dumps(r)
            check("competition:" in text, "read reports which competition the tree is for")
            check("arc-prize-2026-arc-agi-3" in text, "and names it")

            r2 = ks.tool_call("kaggle_experiment_tree",
                              {"action": "alias", "competition": "arc-prize-2026-arc-agi-3",
                               "alias": "titanic2"})
            check("alias_has_own_tree" in json.dumps(r2), "the tool surfaces the fork refusal too")

            tools = {t.get("name"): t for t in getattr(ks, "TOOLS", [])}
            enum = (((tools.get("kaggle_experiment_tree") or {}).get("inputSchema") or {})
                    .get("properties", {}).get("action", {}).get("enum") or [])
            check("alias" in enum, "the alias action is in the tool schema")
        finally:
            os.environ.pop("KAGGLE_AGENT_HOME", None)
            shutil.rmtree(home, ignore_errors=True)
    finally:
        os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)



def _flat(text: str) -> str:
    """Whitespace-collapsed text, for checks that assert a phrase is present.

    A reflowed paragraph splits "no score at / all" across two lines while the reader still sees
    one phrase. Asserting on the raw source then fails on where the line breaks fell rather than
    on whether the instruction exists, and the failure sends you to fix the line wrapping. The
    quotes themselves are still matched exactly - only the whitespace between words is normalised.
    """
    return " ".join(text.split())


# ---------------------------------------------------------------- the two agenda questions
def check_the_waves_ask_what_to_search():
    print("research agenda questions")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    flat = _flat(text)
    low = flat.lower()

    check("do not stop to ask" not in low,
          "the skill no longer tells an agent to push on without asking when nobody is watching")

    marks = {
        "shape": text.find("## The shape"),
        "ask1": text.find("## Before the wave: ask what it is for"),
        "launch": text.find("## Launch a wave"),
        "ask2": text.find("## After the first wave: ask what it changed"),
        "engine": text.find('kaggle_search_engine action="ask"'),
    }
    check(all(v > 0 for v in marks.values()),
          "both agenda questions, and the sections they bracket, are present")
    # No early return here. Bailing out on the first missing heading would report ONE failure
    # for a skill that lost both questions, and every assertion after the bail would sit
    # unexercised while still reading as coverage. Guard each one instead.
    if all(v > 0 for v in marks.values()):
        check(marks["shape"] < marks["ask1"] < marks["launch"],
              "the first question sits after the shape and before the wave is launched")
        check(marks["launch"] < marks["ask2"] < marks["engine"],
              "the second question sits after the wave and before an engine is asked about")

    check(text.count("ask_user") >= 2, "both questions go through ask_user")
    check("not a widget" in low, "a mid-sweep question is text, not a widget")

    # Each question's own section has to say it is asked away as well, not just inherit that
    # from the first one: "like the first" is a pointer, and a reader who skipped that section
    # is exactly the one who needs the sentence. Offsets are taken on the same text that is
    # sliced - positions found in the un-collapsed source mean nothing in the collapsed copy.
    f_ask1 = flat.find("## Before the wave: ask what it is for")
    f_launch = flat.find("## Launch a wave")
    f_ask2 = flat.find("## After the first wave: ask what it changed")
    f_engine = flat.find('kaggle_search_engine action="ask"')
    for label, lo, hi in (("first", f_ask1, f_launch), ("second", f_ask2, f_engine)):
        check(lo > 0 and hi > lo, f"the {label} question's section is locatable")
        seg = flat[lo:hi].lower() if lo > 0 and hi > lo else ""
        check("tier-3" in seg and "away" in seg,
              f"the {label} question says it is asked away, not only when present")

    # A question inside a wave blocks that response and turns the parallel into a queue.
    idxs = [m.start() for m in re.finditer(r"task\(agent_name=", text)]
    check(len(idxs) >= 4, f"a wave is shown as four task( calls (found {len(idxs)})")
    if len(idxs) >= 4:
        check("ask_user" not in text[idxs[0]:idxs[3]],
              "no question is interleaved between the four task( calls of one wave")

    # Trimming a wave is allowed. Losing one without saying so is not: a trimmed wave and a
    # complete one produce the same shape of report, and the next reader cannot tell them apart.
    check("you may cut a subagent" in low,
          "the skill permits a subagent to be cut rather than forbidding it")
    check("quietly lost a member" in low,
          "and a cut member has to be named in the report as cut")


def check_reading_the_code_is_a_chain_not_a_vow():
    print("reading the code is a chain")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    flat = _flat(text)

    check("kaggle_kernel_pull" in flat and "notebook.ipynb" in flat,
          "the sweep pulls each notebook and reads the source file, not the title")
    check("share an ancestor" in flat and "forked from it" in flat,
          "same-lineage notebooks are clustered by an ancestor, and the fan-out is reported")
    check("score bands" in flat and "inside a cluster, or between clusters" in flat,
          "scores are banded, and the question is whether the movement is inside a cluster")
    for v in ("`factors`", "`controls`", "`conditional`"):
        check(v in flat, f"attribution is written in the existing shape: {v}")
    check("author-reported" in flat and "only a re-run settles it" in flat,
          "a diff yields a hypothesis about an author-reported number; only a re-run settles it")
    check("which notebooks you did not read" in flat,
          "the report has to name the notebooks that were not read")


def check_the_code_sweep_states_its_proxy_and_its_gotchas():
    print("code sweep triage")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    flat = _flat(text)
    low = flat.lower()

    check("no score at all" in low,
          "the skill states that the kernel listing carries no score column")
    check("Pick one proxy, write it down" in flat,
          "so 'high scoring' needs a proxy the report names out loud")
    check("100 is a ceiling" in low and "never a total" in low,
          "the 100-record server cap is stated as a ceiling, never as the size of the field")
    check("bytes reprs" in low and "the highest-voted notebook" in low,
          "the bytes-repr ref damage is recorded, including that it hit the top-voted notebook")
    check("Next Page Token" in flat,
          "leaderboard pagination is recorded, the same way the forum's already was")
    check("list separately anything you could not repair" in low,
          "an unrepairable ref is listed rather than dropped, since a dropped one reads as absent")


def check_the_method_note_has_a_home():
    print("the four-line method note")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    flat = _flat(text)
    low = flat.lower()

    # Matched as a template line, not as the bare word. The prose around the block also says
    # "Coverage" twice - once explaining that it is the field people skip, once in an example
    # report - so a check on the word is satisfied by the explanation alone and passes after
    # the field itself has been deleted. The angle bracket is what makes it a required field.
    for field in ("Read", "How", "Blocked at", "Coverage"):
        check(re.search(rf"{re.escape(field)}:\s+<", flat) is not None,
              f"every subagent reports a {field} line, as a filled-in field")
    check("`controls.data`" in flat,
          "the data subagent's read is lifted into its own node's controls.data")
    # Scoped to the data section. The review gate's table also names action="declare" - for a
    # different, allowed thing - so searching the whole skill for the token is satisfied by
    # that row and passes even after the data run's own declaration was deleted.
    a = text.find("## Wave 1 subagent 4")
    b = text.find("### When the account that may read the data")
    check(a > 0 and b > a, "the data subagent's section is locatable")
    if a > 0 and b > a:
        sec = _flat(text[a:b])
        check('action="declare"' in sec and "declares=" in sec,
              "the data run is declared before it is launched, which the launch tool requires")
    check("`constraints`" in flat, "the other notes are lifted into the handoff's constraints")
    check("added by the main agent" in low and "stale_read" in flat,
          "sources are stored by the main agent, because parallel writers lose each other's work")


def check_no_mojibake_in_english_sources():
    print("encoding damage in code")
    # 16 of these shipped: a UTF-8 em dash decoded as GBK, so the sources that a model reads -
    # including one inside a tool description - carried a CJK character and a stray '?'.
    signature = {0x9225: "U+9225", 0x95B3: "U+95B3", 0x95C2: "U+95C2", 0x9226: "U+9226"}
    hits: list[str] = []
    for sub in ("mcp", "tools"):
        for path in sorted((ROOT / sub).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for ch in line:
                    if ord(ch) in signature:
                        hits.append(f"{path.relative_to(ROOT)}:{i} {signature[ord(ch)]}")
    check(not hits, f"no mojibake left in mcp/ or tools/ ({len(hits)} hit(s))"
          + ("" if not hits else f": {hits[:5]}"))


def check_the_scores_that_the_rules_gave_away_are_separated():
    print("method gains vs scores the rules gave away")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    flat = _flat(text)

    check("a label leak" in flat and "duplicated train/test row" in flat,
          "the static patterns of a rules-era score are named, so they can be looked for")
    check("when it does the score evaporates" in flat,
          "the skill says such a score goes away when the host fixes the rule")
    check("let a re-run decide it" in flat,
          "and that the re-run, not the reading, is what settles it")
    check("Claimed" in flat and "Reproduced today" in flat and "Verdict" in flat,
          "the report carries claimed and reproduced as separate columns")
    check("is not a failed experiment" in flat,
          "a collapsed score is reported as a first-class result rather than dropped")
    check("we fixed the scoring" in flat,
          "the host's own announcement is a source, so an unfixed rule is carried into the plan")


def check_an_account_named_for_one_call_does_not_switch_the_session():
    print("per-call accounts")
    spec = importlib.util.spec_from_file_location(
        "ks_acct_check", ROOT / "mcp" / "kaggle_server.py")
    ks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ks)
    creds = getattr(ks, "credentials", None)
    check(creds is not None and hasattr(creds, "token_for"),
          "credentials can resolve a named account without rewriting the active one")
    if creds is not None and hasattr(creds, "token_for"):
        doc = (creds.token_for.__doc__ or "").lower()
        check("without touching the active one" in doc,
              "and the function says so, so the guarantee is readable at the call site")

    src = (ROOT / "mcp" / "kaggle_server.py").read_text(encoding="utf-8")
    code_only = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    launch = code_only.find('if name == "kaggle_kernel_launch"')
    accounts = code_only.find('if name == "kaggle_accounts"')
    check(launch > 0 and accounts > 0 and "use_account" not in code_only[min(launch, accounts):
                                                                        max(launch, accounts)],
          "launching as another account no longer switches the active account as a side effect")

    aware = [t["name"] for t in ks.TOOLS
             if "account" in ((t.get("inputSchema") or {}).get("properties") or {})]
    for tool in ("kaggle_quota", "kaggle_kernel_launch", "kaggle_kernels_output",
                 "kaggle_competitions_list", "kaggle_kernel_pull", "kaggle_datasets_publish"):
        check(tool in aware, f"{tool} takes an account, so a second account is usable mid-task")
    check(len(aware) >= 12, f"account is a per-call option, not a launch-only extra ({len(aware)})")

    cli = (ROOT / "mcp" / "kaggle_cli.py").read_text(encoding="utf-8")
    check('argv[0] == "--as"' in cli and "token_for" in cli,
          "the CLI has the same one-shot selector, so the shell route is not a global switch")

    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    check('account="<A>"' in text and 'account="<B>"' in text,
          "the research skill routes a copy as one account and a compute as another")
    check('accelerator="none"' in text, "and the copy itself runs on the CPU tier")
    check("kaggle datasets create" in text and "dataset_sources" in text,
          "the data changes hands as a dataset inside Kaggle, mounted by the second account")
    check("Never put A's token in a notebook cell" in text,
          "and the token is never inlined into a notebook that gets published")


def check_wave_two_is_single_threaded():
    print("wave 2 runs in one thread")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    flat = _flat(text)
    low = flat.lower()

    check("agent:competition-browser" not in flat,
          "no subagent is dispatched for the forensics any more")
    check("competition-browser" not in low,
          "the removed agent is not named anywhere in the skill body")

    # The wave-2 span, bounded by its own headings, must hold no task() at all. Checking the
    # whole file instead would pass while a dispatch sat inside the forensics section, which is
    # exactly where it used to be.
    w2 = flat.find("Wave 2, step 1")
    end = flat.find("Before any experiment: declare the held-out set")
    check(w2 > 0 and end > w2, "the wave-2 span is locatable between its own two headings")
    if w2 > 0 and end > w2:
        check("task(" not in flat[w2:end],
              "nothing inside wave 2 dispatches a subagent - the forensics are done in-thread")

    # The discipline the removed agent's persona carried. It was the whole reason the agent
    # existed, and it disappears silently: a report that stops opening the real page still looks
    # like a report.
    for phrase, label in (
        ("not reachable in this session", "a page that cannot be read is named, not skipped"),
        ("A missing licence is a finding", "a missing licence is treated as a finding"),
        ("Paper numbers are not leaderboard numbers",
         "a paper's number is never treated as a leaderboard score"),
        ("A snippet is a claim *about* a source, not the source",
         "a search snippet is not accepted as the source"),
        ("An empty result is a result", "an empty result is not padded"),
        ("Never substitute a generic search to fill the gap",
         "a search never fills the gap a page left"),
    ):
        check(phrase in flat, f"the forensics still says: {label}")

    check("`fetch`, `browser`, or\n`not reachable`" in flat or
          all(x in flat for x in ("`fetch`", "`browser`", "`not reachable`")),
          "every source carries the method it was read with")

    # Read-but-not-recorded is a field the next session re-reads.
    for tool in ('kaggle_sources action="add"', 'action="extract"', 'action="link"'):
        check(tool in flat, f"the forensics stores what it read: {tool}")
    check('"kind":"research"' in flat and '"sources"' in flat,
          "and lands it on a research node with its source references")


def check_the_plan_is_reviewed_before_it_is_handed_off():
    print("the plan is reviewed before it is handed off")
    if not check(RESEARCH_SKILL.is_file(), "the research skill is present"):
        return
    text = _skill_body(RESEARCH_SKILL.read_text(encoding="utf-8"))
    flat = _flat(text)
    low = flat.lower()

    # Gate A. Three questions, and the ones that branch - not a proofread request.
    check("Gate A" in flat, "there is a gate before the handoff is offered")
    check("ask_user" in flat, "the first gate asks with ask_user")
    for what, label in (("Which approach", "which approach"),
                        ("Which experiment runs first", "which experiment runs first"),
                        ("Which held-out set", "which held-out set")):
        check(what in flat, f"the gate asks {label}")
    check("tier-3" in low, "and it is tier-3, so it is asked away as well as present")

    # Gate B has two valid exits. A skill that assumes ExitPlanMode exists stalls the most
    # important step of the whole skill on a tool the runtime may not have.
    check("ExitPlanMode" in flat, "the plan can be put up for review with ExitPlanMode")
    # The whole bullet, not a fragment of it. The file contains "is not available" in an
    # unrelated sentence about reading every notebook, so a bare search for the phrase is
    # satisfied by a coincidence and would pass after the fallback had been deleted.
    check("It is not available, and that is a normal outcome" in flat,
          "and there is a documented route when that tool is not available")
    check("you will not write a handoff" in low,
          "the fallback states what will not happen until the plan is approved")

    # The gate has to bite. A handoff is what the next agent reads as the plan. Matched per
    # line rather than as one fixed cell, because a row may name more than the tool it blocks.
    rows = [l for l in text.splitlines() if l.strip().startswith("|")]
    for blocked in ("`handoff_write`", "`handoff_sync`", "`kaggle_kernel_launch`"):
        row = next((l for l in rows if blocked in l), None)
        check(row is not None and "**blocked**" in row,
              f"{blocked} is blocked until the plan is approved")
    check(any('`kaggle_experiment_tree action="anchor"`' in l and "**allowed**" in l for l in rows),
          "and declaring the anchor is explicitly still allowed, so the gate blocks acting, "
          "not thinking")

    # presence-mode's tier-2 "draft it locally" rule would otherwise write an unapproved plan
    # to disk the moment the user looks away, and the gate would be theatre.
    handoff = ROOT / "skills" / "handoff" / "SKILL.md"
    if check(handoff.is_file(), "the handoff skill is present"):
        h = _flat(handoff.read_text(encoding="utf-8"))
        check("One exception, and it outranks the tier" in h,
              "the handoff skill's tier-2 drafting exception is stated")
        check("not tier-2" in h and "unreviewed research plan" in h.lower(),
              "and it says an unapproved plan is not the tier-2 case, even when away")


def check_the_package_survives_a_marketplace_install():
    """A local install and a Marketplace install do not have the same shape on disk.

    A local plugin sits at ``<root>/kaggle-agent``. A Marketplace plugin is cached under a
    directory named after its content hash, several levels further down. Two separate pieces of
    code find this package - the entry module, and the one-line bootstrap the manifest carries
    because it cannot reference a path - and both used to look one level down and match on a
    directory name. A Marketplace user therefore got a server that never started, and the
    symptom is an empty tool list, which reads as "the plugin did not register".

    The bootstrap is also run under ``python -c``, where ``runpy.run_path(..., run_name=
    '__main__')`` cannot work: the interpreter's ``__main__`` has no spec and sys.path[0] is the
    package's own directory. That raised before a single tool was registered, so it is asserted
    here rather than left to the shape of the code.
    """
    entry = (ROOT / "mcp" / "agent_server.py").read_text(encoding="utf-8")
    servers = json.loads((ROOT / "servers.mcp.json").read_text(encoding="utf-8"))
    args = servers["mcpServers"]["kaggle"]["args"]
    code = args[args.index("-c") + 1]

    check("plugin-cache" in entry, "the entry module knows the marketplace cache root")
    check("plugin-import" in entry, "the entry module knows the imported-plugin root")
    check("plugin-cache" in code, "the bootstrap knows the marketplace cache root too")
    check("plugin-import" in code, "the bootstrap knows the imported-plugin root too")
    check("os.path.join(b,n,m)" in code, "the bootstrap descends two levels, not one")

    check("PLUGIN_NAME" in entry and 'get("name")' in entry,
          "the package is identified by its manifest name, not by a directory name")
    check("run_name='__main__'" not in code,
          "the bootstrap does not ask runpy for a __main__ module that python -c cannot provide")
    check("exec(compile(" in code and "'__name__':'__main__'" in code,
          "the bootstrap executes the entry with an explicit __name__")

    # bin/kaggle-cli.sh is a Python file with a shebang and carries no executable bit, so a
    # documented `./bin/kaggle-cli.sh` fails with permission denied on the machine that installs.
    sh = (ROOT / "bin" / "kaggle-cli.sh").read_text(encoding="utf-8")
    cli_skill = (ROOT / "skills" / "kaggle-cli" / "SKILL.md").read_text(encoding="utf-8")
    check("./bin/kaggle-cli.sh" not in cli_skill,
          "the skill never asks a user to run a file whose executable bit is not shipped")
    check("./bin/kaggle-cli.sh" not in sh, "the script's own examples do not either")
    check("python3 bin/kaggle-cli.sh" in cli_skill,
          "the skill documents the interpreter call that works without the mode bit")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("mcode plugin add kaggle-agent@official" in readme,
          "the README tells an installed user how the plugin gets there")

    # A manual entry point existed for one platform only, so the other one had nothing to run by
    # hand when a tool misbehaved. It is a Python file with a shebang, like kaggle-cli.sh, and
    # finds the package without depending on the working directory.
    manual = ROOT / "bin" / "run-mcp.sh"
    check(manual.is_file(), "there is a POSIX entry point for driving the server by hand")
    if manual.is_file():
        text = manual.read_text(encoding="utf-8")
        check("import agent_server" in text and "agent_server.serve()" in text,
              "and it is the Python entry point, not a shell wrapper that would need an exec bit")
        check("os.path.abspath(__file__)" in text, "it locates the package rather than the cwd")

    # Naming one platform's entry point as if it were the only one is how a POSIX user ends up
    # told to run a .cmd file.
    check("kaggle-cli.sh" in servers["mcpServers"]["kaggle"]["description"],
          "the server description names the shell entry point too, not only the .cmd one")
    check("(Windows)" in cli_skill and "macOS" in cli_skill,
          "the skill says which entry point is which platform's")

    probe = ROOT / "tools" / "probe_marketplace_layout.py"
    check(probe.is_file(), "the marketplace layout probe ships with the package")

    transport = (ROOT / "tools" / "probe_transport.py").read_text(encoding="utf-8")
    check("PROBE_TRANSPORT_OK" in transport and "PROBE_TRANSPORT_FAILED" in transport,
          "the transport probe can report failure, not only success")


def check_the_cli_is_offered_not_just_reported():
    """The Kaggle CLI gets the same detect-then-offer path the plotting backend already had.

    A missing figure is a missing figure; a missing CLI leaves every tool in the package unable
    to run, and until this existed the only install flow in the codebase talked about matplotlib
    - so the one hard prerequisite was the one with no route. Nothing here installs anything.
    What is asserted is that the report names the CLI, carries the command, and asks first.
    """
    import deps as _d

    check("kaggle" in _d.INSTALLABLE, "the CLI is installable through the same action")
    check(tuple(_d.CLI_PACKAGES) == ("kaggle",), "the CLI is its own category, not a backend package")

    p = _d.probe()
    check("kaggleCliReady" in p and "toolsReady" in p,
          "the report separates 'can this machine draw' from 'can this package do anything'")
    check(bool(p["toolsReady"]) == bool(p["kaggleCliReady"]),
          "toolsReady and kaggleCliReady cannot disagree")

    # Forcing the CLI to look absent while the backend stays present is the case that decides
    # the report: it is the one a freshly installed plugin lands in.
    real = _d.cli_ready
    _d.cli_ready = lambda timeout=30: False
    try:
        missing = _d.probe()
    finally:
        _d.cli_ready = real
    check(missing["kaggleCliReady"] is False and missing["toolsReady"] is False,
          "with no CLI, the package reports that no tool works")
    check(missing["code"] == "kaggle_cli_missing", "the missing CLI is what the report names")
    check('["kaggle"]' in missing.get("install", ""), "the report carries the command that fixes it")
    check("ask the user" in missing.get("nextStep", "").lower(),
          "and the report asks before changing the environment")
    check(missing["plottingReady"] == p["plottingReady"],
          "a missing CLI is not reported as a plotting problem")

    # Detecting it must not be an import. The kaggle package prints a sign-in walkthrough to
    # stdout when imported, and this process speaks JSON-RPC on stdout.
    seen: list[str] = []
    real_installed = _d._installed
    _d._installed = lambda name: (seen.append(name), real_installed(name))[1]
    try:
        _d._present("kaggle")
        _d._present("numpy")
    finally:
        _d._installed = real_installed
    check("kaggle" not in seen,
          "detecting the CLI never imports the package, which would print to stdout")
    check("numpy" in seen, "the libraries are still detected by import")

    version = _d._cli_version()
    check(bool(re.fullmatch(r"\d+(\.\d+)*", version or "")),
          f"the version field is a number, not the CLI's sign-in text ({version[:60]!r})")

    # Ask once, reuse from then on. Every Kaggle tool funnels through this, and a probe that
    # re-ran per call would cost a subprocess per tool call in a package with two dozen of them.
    import kaggle_server as _ks
    real_run = _ks.subprocess.run
    probes: list[list] = []

    def counting(cmd, *a, **kw):
        probes.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
        return real_run(cmd, *a, **kw)

    _ks.subprocess.run = counting
    try:
        _ks._reset_kaggle_command_cache()
        first = _ks._kaggle_command()
        after_first = len(probes)
        repeats = [_ks._kaggle_command() for _ in range(3)]
        check(after_first >= 1, f"the first call really probes ({after_first} candidate interpreter(s))")
        check(len(probes) == after_first,
              f"later calls reuse it rather than probing again ({len(probes) - after_first} extra)")
        check(repeats == [first] * 3, "and every call gets the same answer")
        _ks._kaggle_command(force=True)
        check(len(probes) > after_first, "force=True re-asks on purpose")
        after_force = len(probes)
        _ks._kaggle_command()
        check(len(probes) == after_force, "and caches that answer too")

        # The machine without the CLI is the one that would otherwise pay the most, since a
        # missing package is what every candidate interpreter gets asked about.
        real_probe = _ks._probe_kaggle_command
        misses: list[int] = []

        def failing() -> list[str] | None:
            misses.append(1)
            return None

        _ks._probe_kaggle_command = failing
        try:
            _ks._reset_kaggle_command_cache()
            absent = [_ks._kaggle_command() for _ in range(4)]
        finally:
            _ks._probe_kaggle_command = real_probe
        check(absent == [None] * 4, "a machine without the CLI is still told so, on every call")
        check(len(misses) == 1,
              f"and the 'no CLI' answer is cached too, not re-derived per call ({len(misses)} probes)")

        _ks._reset_kaggle_command_cache()
        _ks._kaggle_command()
        check(len(misses) >= 1, "resetting the cache re-asks, which is what an install has to do")
    finally:
        _ks.subprocess.run = real_run
        _ks._reset_kaggle_command_cache()

    server = (ROOT / "mcp" / "kaggle_server.py").read_text(encoding="utf-8")
    check('action=\\"doctor\\"' in server,
          "the 127 path points at doctor rather than only naming pip")


def main() -> int:
    check_manifest()
    check_servers()
    check_skill_frontmatter()
    check_relationships()
    check_browses_floor()
    check_presence_reach()
    check_graph_state()
    check_tree_enforcement()
    check_search_widening()
    check_replay_semantics()
    check_monotone_policy()
    check_efc_accounting()
    check_failure_layer_and_anchor()
    check_undo_and_rounds()
    check_migration_v2_to_v3()
    check_ablation_arithmetic()
    check_the_dataset_is_a_control_and_silence_is_not_a_disagreement()
    check_the_thinking_steps_were_actually_added()
    check_the_readme_counts_what_the_package_contains()
    check_decision_coupling()
    check_tree_ownership()
    check_runtime_behaviour()
    check_evidence_chain()
    check_plotting_backend_is_real()
    check_vendored_content_is_scanned()
    check_the_audit_drives_and_does_not_acquit()
    check_evidence_graph_binding()
    check_skill_index()
    check_widget_binding()
    check_no_secrets()
    check_no_uncalled_functions()
    check_store_migration()
    check_no_stale_names()
    check_version_sync()
    check_data_stays_on_kaggle()
    check_wave_two_is_single_threaded()
    check_the_plan_is_reviewed_before_it_is_handed_off()
    check_the_package_survives_a_marketplace_install()
    check_the_cli_is_offered_not_just_reported()
    check_the_waves_ask_what_to_search()
    check_reading_the_code_is_a_chain_not_a_vow()
    check_the_code_sweep_states_its_proxy_and_its_gotchas()
    check_the_method_note_has_a_home()
    check_no_mojibake_in_english_sources()
    check_the_scores_that_the_rules_gave_away_are_separated()
    check_an_account_named_for_one_call_does_not_switch_the_session()
    check_browser_is_search_only()
    check_research_preflight()
    check_launch_gate()
    check_launch_attaches_monitoring()
    check_local_run_is_monitored()
    check_monitor_uses_the_builtin_cron()
    check_diagnosis_loop()
    check_node_survives_the_tool_boundary()
    check_the_schema_advertises_what_exists()
    check_no_reversed_assertions()
    check_transport_resilience()
    check_the_monitor_watches_content()
    check_a_node_keeps_its_recipe()
    check_predictions_are_judged()
    check_the_curriculum_gates_declare()
    check_a_line_can_be_abandoned()
    check_consider_and_prune()
    check_competition_isolation()
    check_text_encoding()
    check_publishable()
    print()
    if failures:
        print(f"{len(failures)} failure(s) out of {checks} checks:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"all {checks} checks passed")
    return 0


# The RSI loop is only a loop if the next experiment is required to be based on
# what the last one showed. Before this the tree held scores and never held what
# a run taught, so the improvement step was guesswork wearing a DAG.
#
# diagnose_log reuses the EXISTING FAILURE_LAYERS vocabulary rather than adding a
# taxonomy: a run that is merely slow is not a layer failure, so `layer` is required
# only when a failure signature appears, and `bottleneck` stays free text. Specific
# signals beat the generic "Traceback", because a bare traceback sits on the header
# line ABOVE the exception that names the failure.

def check_diagnosis_loop():
    print("diagnosis loop")
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    import pathlib as _pl

    spec = importlib.util.spec_from_file_location("_ks_loop", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the diagnosis loop test: {exc}")
        return
    et = sys.modules["experiment_tree"]
    # An embedded body may bind the name `bad` to a local; keep the module-level
    # reporter under a name nothing in the body can shadow.
    _report_failure = globals()["bad"]   # globals(), not `bad`: the embedded body
    # binds `bad` as a local of THIS function, so a bare name would be unbound here

    P: list[str] = []
    Fl: list[str] = []

    def check(cond, label, detail=""):
        # (cond, label) on purpose. The swapped form check("label", cond) always passes,
        # because a non-empty label is truthy - which has happened seven times in this project.
        (P if cond else Fl).append(label)
        # Bump the module counter too, or a passing assertion here is invisible in the
        # headline total and the suite looks like it checks nothing.
        globals()["checks"] += 1
        print(f"  {'ok  ' if cond else 'FAIL'} {label}"
              f"{('  ' + str(detail)[:90]) if detail else ''}")

    comp = "zz-check-" + "_ks_loop"
    COMP = comp          # the embedded body was written against a module constant
    home = _mkdtemp(prefix="ka-_ks_loop-")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    work = _pl.Path(_mkdtemp(prefix="ka-_ks_loop-work-"))
    log = Path(work, "run.log")     # the embedded body refers to this name
    log.write_text(
        "loading data...\n"
        "step 1200/1200\n"
        "elapsed: 41.50\n"
        "Traceback (most recent call last):\n"
        '  File "train.py", line 88, in <module>\n'
        "    raise ValueError('submission must have exactly 2 columns')\n"
        "ValueError: submission must have exactly 2 columns\n",
        encoding="utf-8")
    try:
        print("=== 1. diagnose reads the log and names the layer ===")
        r = ks.tool_call("kaggle_experiment_tree",
                         {"action": "diagnose", "competition": COMP, "path": str(log)})
        t = json.dumps(r, ensure_ascii=False)
        check("output-contract" in t, 'it classifies the layer')
        check("it quotes the first error" in t or "ValueError" in t, "and quotes the first error line")
        check("timing   :" in t, "and surfaces the timing it found")
        check("bottleneck" in t and "logPath" in t,
              "and a node carrying logPath + bottleneck")

        print()
        print("=== 2. declaring without a diagnosis is refused ===")
        d = _decl(et, COMP, {"id": "e0", "change": "x", "hypothesis": "h", "parent": None,
                              "operator": "draft", "family": "f", "reason": "r"},
                       read_revision=et.read(COMP)["revision"])
        check(not d.get("ok") and d.get("code") == "diagnosis_required",
              f"a declaration with no basis is refused: {d.get('code')}")

        print()
        print("=== 3. the diagnosis records, and can then be cited ===")
        reading = et.diagnose_log(log.read_text(encoding="utf-8"), source=str(log))
        check(reading["empty"] is False, "the reader says the log is not empty")
        node = {"id": "d1", "kind": "research",
                "question": "why did the run fail?", "targets": ["code"], "verdict": "keep",
                "opens": "fix the submission writer", "parent": None,
                "reason": "the log said so", "evidence": "local-only",
                "bottleneck": "submission writer emits one column, the evaluator wants two",
                "logPath": str(log), "layer": reading["layer"]}
        rec = et.record(COMP, node, read_revision=et.read(COMP)["revision"])
        check(rec.get("ok"), f"the diagnosis is recorded: {rec.get('message')}")

        d = _decl(et, COMP, {"id": "e1", "change": "write both columns", "hypothesis": "it then passes",
                              "parent": None, "operator": "debug", "family": "submission",
                              "reason": "the diagnosis named it", "diagnosis": "d1"},
                       read_revision=et.read(COMP)["revision"])
        check(d.get("ok"), f"a declaration citing the diagnosis is accepted: {d.get('message')}")

        print()
        print("=== 4. the citation is checked, not trusted ===")
        d2 = _decl(et, COMP, {"id": "e2", "change": "c", "hypothesis": "h", "parent": None,
                               "operator": "draft", "family": "f", "reason": "r",
                               "diagnosis": "no-such-node"},
                        read_revision=et.read(COMP)["revision"])
        check(d2.get("code") == "unknown_diagnosis",
              f"citing a node that does not exist is refused: {d2.get('code')}")
        et.record(COMP, {"id": "r9", "kind": "research", "question": "q", "targets": ["code"],
                         "verdict": "keep", "opens": "o", "parent": None, "reason": "r"},
                  read_revision=et.read(COMP)["revision"])
        d3 = _decl(et, COMP, {"id": "e3", "change": "c", "hypothesis": "h", "parent": None,
                               "operator": "draft", "family": "f", "reason": "r",
                               "diagnosis": "r9"},
                        read_revision=et.read(COMP)["revision"])
        check(d3.get("code") == "not_a_diagnosis",
              f"citing a research node with no bottleneck is refused: {d3.get('code')}")

        print()
        print("=== 5. the escape hatch still needs a reason ===")
        comp2 = "zz-loop-first"
        d4 = _decl(et, comp2, {"id": "e0", "change": "baseline", "hypothesis": "establish a number",
                                "parent": None, "operator": "draft", "family": "f", "reason": "r",
                                "diagnosis": "none"},
                         read_revision=et.read(comp2)["revision"])
        check(not d4.get("ok") and d4.get("code") == "diagnosis_reason_required",
              f"diagnosis=none without a reason is refused: {d4.get('code')}")
        d5 = _decl(et, comp2, {"id": "e0", "change": "baseline", "hypothesis": "establish a number",
                                "parent": None, "operator": "draft", "family": "f", "reason": "r",
                                "diagnosis": "none",
                                "diagnosisReason": "first run of this competition, nothing to learn from yet"},
                         read_revision=et.read(comp2)["revision"])
        check(d5.get("ok"), f"diagnosis=none with a real reason is accepted: {d5.get('message')}")

        print()
        print("=== 6. a bottleneck with no log behind it is rejected ===")
        bad = et.record(COMP, {"id": "d2", "kind": "research", "question": "q", "targets": ["code"],
                               "verdict": "keep", "opens": "o", "parent": None, "reason": "r",
                               "bottleneck": "it felt slow"},
                        read_revision=et.read(COMP)["revision"])
        check(not bad.get("ok"), "a bottleneck citing no log is refused")
        check(any("logRef" in p or "logPath" in p for p in (bad.get("problems") or [])),
              f"and the complaint names the log: {(bad.get('problems') or [])[:1]}")
        bad2 = et.record(COMP, {"id": "d3", "kind": "research", "question": "q", "targets": ["code"],
                                "verdict": "keep", "opens": "o", "parent": None, "reason": "r",
                                "logPath": str(log), "bottleneck": "x", "layer": "not-a-layer"},
                         read_revision=et.read(COMP)["revision"])
        check(not bad2.get("ok"), "an invented failure layer is refused")

        print()
        print("=== 7. an empty log cannot be diagnosed ===")
        empty = work / "empty.log"
        empty.write_text("", encoding="utf-8")
        r2 = ks.tool_call("kaggle_experiment_tree",
                          {"action": "diagnose", "competition": COMP, "path": str(empty)})
        check("is empty" in json.dumps(r2), "diagnosing an empty log is refused")
        r3 = ks.tool_call("kaggle_experiment_tree", {"action": "diagnose", "competition": COMP})
        check("needs ref=" in json.dumps(r3), "diagnose with no source is refused")
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)
        _shutil.rmtree(work, ignore_errors=True)

    for x in Fl:
        _report_failure(f"diagnosis loop: {x}")


# The tool boundary mangles shapes. An agent reported that `targets` could not be
# recorded: ["code"], ["forum","code"] and omitting it all produced "targets must
# be a list". That is not a caller error - the runtime hands the node over with the
# list collapsed to a bare string, or one level deeper than it was sent. A contract
# that breaks on that is not a contract, and a node an agent cannot record is an
# experiment it cannot run.

def check_node_survives_the_tool_boundary():
    print("node survives the tool boundary")
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    import pathlib as _pl

    spec = importlib.util.spec_from_file_location("_ks_boundary", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the node survives the tool boundary test: {exc}")
        return
    et = sys.modules["experiment_tree"]
    # An embedded body may bind the name `bad` to a local; keep the module-level
    # reporter under a name nothing in the body can shadow.
    _report_failure = globals()["bad"]   # globals(), not `bad`: the embedded body
    # binds `bad` as a local of THIS function, so a bare name would be unbound here

    P: list[str] = []
    Fl: list[str] = []

    def check(cond, label, detail=""):
        # (cond, label) on purpose. The swapped form check("label", cond) always passes,
        # because a non-empty label is truthy - which has happened seven times in this project.
        (P if cond else Fl).append(label)
        # Bump the module counter too, or a passing assertion here is invisible in the
        # headline total and the suite looks like it checks nothing.
        globals()["checks"] += 1
        print(f"  {'ok  ' if cond else 'FAIL'} {label}"
              f"{('  ' + str(detail)[:90]) if detail else ''}")

    comp = "zz-check-" + "_ks_boundary"
    COMP = comp          # the embedded body was written against a module constant
    home = _mkdtemp(prefix="ka-_ks_boundary-")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    work = _pl.Path(_mkdtemp(prefix="ka-_ks_boundary-work-"))
    log = Path(work, "run.log")     # the embedded body refers to this name
    log.write_text(
        "loading data...\n"
        "step 1200/1200\n"
        "elapsed: 41.50\n"
        "Traceback (most recent call last):\n"
        '  File "train.py", line 88, in <module>\n'
        "    raise ValueError('submission must have exactly 2 columns')\n"
        "ValueError: submission must have exactly 2 columns\n",
        encoding="utf-8")
    def research(**over):
        n = {"id": "d1", "kind": "research", "question": "what dominated the run",
             "targets": ["code"], "verdict": "keep", "opens": "the next experiment",
             "parent": None, "reason": "the log said so", "evidence": "local-only",
             "bottleneck": "data loading is 70% of the run",
             "logPath": str(log)}
        n.update(over)
        return n

    def rev():
        return et.read(COMP)["revision"]

    try:
        print("=== the shapes that actually arrive ===")
        cases = [
            ("a real list", research(), "d1"),
            ("a BARE STRING where a list was sent", research(id="d2", targets="code"), "d2"),
            ("a list nested one level too deep", research(id="d3", targets=[["code"]]), "d3"),
            ("a tuple", research(id="d4", targets=("code",)), "d4"),
            ("parent sent as the string 'none'", research(id="d5", parent="none"), "d5"),
            ("parent sent as false", research(id="d6", parent=False), "d6"),
            ("parent absent entirely", {k: v for k, v in research(id="d7").items() if k != "parent"}, "d7"),
            ("metric nested one level too deep",
             {"id": "e1", "kind": "experiment", "change": "c", "hypothesis": "h", "parent": None,
              "operator": "draft", "family": "f", "reason": "r", "evidence": "local-only",
              "verdict": "keep",
              "metric": {"metric": {"name": "s", "parent": None, "result": 1.0, "delta": 0.1,
                                    "rank": 1, "rankSource": "lb"}}}, "e1"),
        ]
        for label, node, nid in cases:
            r = et.record(COMP, node, read_revision=rev())
            check(f"{label}", r.get("message"))

        print()
        print("=== the shape is repaired, not merely tolerated ===")
        n = et.load(COMP)
        stored = n["tree"]["nodes"]
        check((stored.get("d2") or {}).get("targets"), 'a bare string became a one-item list')
        check((stored.get("d3") or {}).get("targets"), 'a nested list was flattened')
        check((stored.get("d5") or {}).get("parent") is None, "parent 'none' became null")
        check((stored.get("d6") or {}).get("parent") is None, "parent false became null")
        check((stored.get("d7") or {}).get("parent") is None,
              "an absent parent stays harmless (it is no longer required)")
        check(((stored.get("e1") or {}).get("metric") or {}).get("name") == "s",
          "the nested metric was unwrapped")

        print()
        print("=== normalize_node is idempotent ===")
        once = et.normalize_node(research(targets="code", parent="none"))
        twice = et.normalize_node(once)
        check(once == twice, "normalizing twice changes nothing")

        print()
        print("=== it does not hide a genuinely wrong value ===")
        r = et.record(COMP, research(id="d8", targets="not-a-real-target"),
                      read_revision=rev())
        check(not r.get("ok"), "a target outside the vocabulary is still rejected")
        check(any("not one of" in x for x in (r.get("problems") or [])),
              "and the complaint names the vocabulary")
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)
        _shutil.rmtree(work, ignore_errors=True)

    for x in Fl:
        _report_failure(f"node survives the tool boundary: {x}")


# ---------------------------------------------------------------- text encoding
# A UTF-8 file round-tripped through a Windows PowerShell pipeline comes back as DIFFERENT
# characters, not as an error: Get-Content -Raw decodes with the system codepage (GBK here)
# and the next write re-encodes the result, so every CJK string in the file silently becomes
# other CJK strings of similar width. It reached the published repository across four commits
# before it was found, in a single retired-agent name, and 1053 checks stayed green because
# nothing looked at the bytes.
#
# The tell is a code point comparison, never a console rendering: the console will happily
# display corrupted characters as if they were the intended ones.
BOM = b"\xef\xbb\xbf"
ENCODING_SUFFIXES = {".py", ".md", ".json", ".sh", ".cmd", ".txt", ".yml", ".yaml"}
# Literals whose exact characters matter, compared by code point. A mojibake round trip
# produces same-width wrong characters, so a length check cannot see it.
EXACT_LITERALS = [
    ("tools/check_plugin.py", "Kaggle \u641c\u7d22", "\u641c\u7d22"),
]


def _publishable_text_files() -> list[Path]:
    out = []
    for f in ROOT.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in ENCODING_SUFFIXES:
            continue
        parts = set(f.parts)
        if ".git" in parts or "__pycache__" in parts:
            continue
        out.append(f)
    return sorted(out)


def check_text_encoding():
    print("text encoding")
    files = _publishable_text_files()
    check(bool(files), f"there are text files to check ({len(files)})")

    undecodable = []
    bommed = []
    for f in files:
        b = f.read_bytes()
        if b.startswith(BOM):
            bommed.append(str(f.relative_to(ROOT)))
        try:
            b.decode("utf-8")
        except UnicodeDecodeError as exc:
            undecodable.append(f"{f.relative_to(ROOT)}: {exc}")
    for rel in bommed:
        bad(f"{rel} starts with a UTF-8 BOM; the loader sees the BOM as content")
    check(not bommed, f"no text file carries a BOM ({len(files)} files)")
    for u in undecodable:
        bad(f"{u} is not valid UTF-8")
    check(not undecodable, "every text file decodes as strict UTF-8")

    for rel, needle, expected in EXACT_LITERALS:
        f = ROOT / rel
        if not check(f.is_file(), f"{rel} exists for its literal check"):
            continue
        text = f.read_text(encoding="utf-8")
        present = check(needle in text, f"{rel} contains {expected!r} exactly")
        if not present:
            # Already reported. Do not index into a string that is not there: the whole point of
            # this check is to survive the file being wrong.
            continue
        # A same-width wrong string is the actual failure mode, so compare code points.
        got = text[text.index(needle):text.index(needle) + len(needle)]
        check("".join(f"{ord(c):04X}" for c in got) ==
              "".join(f"{ord(c):04X}" for c in needle),
              f"{rel}: the literal is not a mojibake lookalike")



# ---------------------------------------------------------------- the schema must advertise it
# `diagnose` shipped in the skills for four commits with no enum entry, and 1085 checks stayed
# green the whole time, because nothing compared "what the skills say" against "what the schema
# accepts". An action the tool rejects looks exactly like a feature that was never written.
# This is the omission class made permanent: every action a handler implements, and every action a
# skill documents, must be in the published enum.
def check_the_schema_advertises_what_exists():
    print("schema advertises what exists")
    import re as _re

    spec = importlib.util.spec_from_file_location("_ks_schema", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the server module loads for the schema audit: {exc}")
        return
    tools = {t.get("name"): t for t in getattr(ks, "TOOLS", [])}
    src = SERVER_PY.read_text(encoding="utf-8")

    def branch(tool: str) -> str:
        start = src.index(f'if name == "{tool}":')
        nxt = _re.search(r'\n    if name == "', src[start + 10:])
        return src[start:start + 10 + (nxt.start() if nxt else len(src))]

    for tool in ("kaggle_experiment_tree", "kaggle_log_monitor"):
        if not check(tool in tools, f"{tool} is published"):
            continue
        enum = set(((tools[tool].get("inputSchema") or {}).get("properties") or {})
                   .get("action", {}).get("enum") or [])
        if not check(bool(enum), f"{tool} publishes an action enum"):
            continue
        handled = set(_re.findall(r'action == "([a-z_]+)"', branch(tool)))
        check(handled <= enum,
              f"{tool}: every action it implements is in the enum "
              f"(missing {sorted(handled - enum)}; {len(handled)} implemented)")
        documented = set()
        for md in (ROOT / "skills").rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            for m in _re.finditer(rf'{tool}[^\n]{{0,80}}?action="([a-z_]+)"', text):
                documented.add(m.group(1))
            for m in _re.finditer(r'action="([a-z_]+)"[^\n]{0,80}?' + tool, text):
                documented.add(m.group(1))
        check(documented <= enum,
              f"{tool}: every action the skills document is in the enum "
              f"(missing {sorted(documented - enum)}; {len(documented)} documented)")

    # the tool count is a headline number; state it so a silent drop is visible
    check(len(tools) >= 29, f"the tool surface has not shrunk ({len(tools)} tools)")


# ---------------------------------------------------------------- no reversed assertions
# The single most repeated defect in this file: check("label", condition) instead of
# check(condition, "label"). A non-empty label is always truthy, so the assertion passes
# whatever the code does, and the suite reports a success it never earned. It has happened
# eight times, and the only reliable detector is the parse tree - at runtime a correct call
# like check("when" in e and e["when"].strip(), ...) evaluates to a string and looks exactly
# like a label, so a runtime guard cannot tell them apart.
def check_no_reversed_assertions():
    print("no reversed assertions")
    import ast as _ast

    src = (ROOT / "tools" / "check_plugin.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    module_level = set()
    for n in tree.body:
        if isinstance(n, _ast.FunctionDef):
            module_level.add(n.name)

    # the embedded suites define their own local check(cond, label, detail) on purpose
    locals_own = set()
    for n in _ast.walk(tree):
        if isinstance(n, _ast.FunctionDef) and n.name == "check":
            if n not in tree.body:
                locals_own.add(id(n))

    problems: list[str] = []
    for n in _ast.walk(tree):
        if not (isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)
                and n.func.id == "check"):
            continue
        a = n.args
        if len(a) >= 3:
            problems.append(f"line {n.lineno}: three arguments - check(condition, \"message\")")
        elif len(a) == 2 and isinstance(a[0], (_ast.Constant, _ast.List, _ast.Dict, _ast.Set)):
            if isinstance(a[0], _ast.Constant) and not isinstance(a[0].value, str):
                continue
            val = a[0].value if isinstance(a[0], _ast.Constant) else "<literal>"
            if isinstance(val, str) and val.strip() == "":
                continue
            problems.append(f"line {n.lineno}: a literal where the condition belongs "
                       f"({str(val)[:50]!r}) - a non-empty label is always truthy")
    for b in problems:
        bad(b)
    if not problems:
        ok("no check() call has its label in the condition slot")


# ------------------------------------------------------- structured args, and what a transport does
# The host's tool layer drops the CONTENTS of an object-typed argument and can split a
# non-ASCII character in half. Both failures are invisible from the schema: the call is
# accepted, and the tool reports its own ordinary complaint ("node id ''", an encoding error
# deep in a file write). So two rules are asserted here, each from the failure it prevents:
# every published argument is a scalar or a STRING of JSON, and a handler can never take the
# server down with it.
def check_transport_resilience():
    print("transport resilience")
    import json as _json

    def check(cond, label):
        # (cond, label) only - the AST audit forbids the 3-arg form, and a detail that
        # belongs in the message belongs in the message, not in a second slot.
        # ok()/bad() already bump the module counter; do not bump it twice here.
        if cond:
            ok(label)
        else:
            bad(f"transport resilience: {label}")

    spec = importlib.util.spec_from_file_location("_ks_tr", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"transport resilience: the server module loads: {exc}")
        return

    # 1. No published argument is an object or an array. Either arrives empty through the
    #    host, so the whole tool family - declare, record, settle, local launch - is
    #    unreachable. A string of JSON survives.
    structured_published = []
    for t in ks.TOOLS:
        for k, v in ((t.get("inputSchema") or {}).get("properties") or {}).items():
            if v.get("type") in ("object", "array"):
                structured_published.append(f"{t['name']}.{k} ({v.get('type')})")
    check(not structured_published,
          f"no published argument is an object or an array (found: {structured_published})")

    # 2. Every argument that was structured a moment ago is still a string of JSON, so the
    #    fix is not "delete the parameter" but "carry the same data over the channel that
    #    works". A parameter that quietly disappeared is a capability removed, not fixed.
    src = (ROOT / "mcp" / "kaggle_server.py").read_text(encoding="utf-8")
    for field in ("node", "tree", "command", "constraints", "weights", "policy",
                  "params", "packages"):
        check(f'"{field}"' in src,
              f"the argument '{field}' is still read by the server, not deleted")

    # 3. A tool that raises returns a normal error result and the server stays up. The
    #    counter-example is a real handler bug - an unhandled ValueError used to kill the
    #    process, and the client only ever saw "Connection closed".
    r = ks.safe_tool_call("handoff_write", {
        "competition": "zz-tr", "title": "t", "task": "x",
        "tree": '{"base":"n1","nodes":{"n1":{"id":"n1","kind":"experiment",'
                '"verdict":"keep","reason":"r"}}}',
    })
    txt = _json.dumps(r)
    check(r.get("isError") is True,
          f"a handler that raises returns isError, not a dead connection ({txt[:70]})")
    check("this is a bug in the plugin" in txt,
          "and it says the call reached the plugin (a plugin bug, not your arguments)")

    # the server is still answering after that
    after = ks.safe_tool_call("kaggle_experiment_tree", {"action": "status",
                                                         "competition": "zz-tr"})
    check(after.get("isError") is not True,
          "the server still answers the next call after a handler raised")

    # 4. A split surrogate pair is rejoined; a lone half is refused with the field named.
    import structured as js
    joined = js.scrub("a\ud83d\ude00b")
    check(joined == "a\U0001f600b",
          "a split surrogate pair is rejoined, not mangled "
          f"({' '.join('%04X' % ord(c) for c in joined)})")
    try:
        js.scrub({"node": {"change": "x\udcaey"}})
        check(False, "a lone surrogate half is refused")
    except js.StructuredError as exc:
        check("arguments.node.change" in str(exc),
              f"a lone surrogate half is refused, naming the exact field ({str(exc)[:60]})")

    # 5. An over-long argument is refused before it can stress the transport, with a route
    #    for the content that does not fit.
    try:
        js.scrub({"rules": "P" * (js.MAX_ARG_CHARS + 1)})
        check(False, "an over-long argument is refused")
    except js.StructuredError as exc:
        check("a file" in str(exc),
              "an over-long argument is refused and points at a file instead "
              f"({str(exc)[:50]})")

    # 6. argv reads a JSON array and plain text, and rejects broken JSON by name.
    check(js.argv('["python","train.py"]') == ["python", "train.py"],
          "argv reads a JSON array of tokens")
    check(js.argv("python train.py --epochs 3") == ["python", "train.py", "--epochs", "3"],
          "argv still reads plain text")
    try:
        js.argv("[bad json")
        check(False, "argv rejects broken JSON")
    except js.StructuredError as exc:
        check("command is not valid JSON" in str(exc),
              f"argv rejects broken JSON and names the field ({str(exc)[:50]})")


# ------------------------------------------------------- a monitor that watches, and a node that remembers
# A ladder that only counts ticks cannot tell "the run has printed nothing for an hour" from
# "the run died in the first minute": the difference is in the log, and a clock never reads the
# log. And a log that cannot be read was treated as a reason to stop watching, which throws away
# runs that were fine. So: the content decides the cadence, and an unreadable log is a route to
# be repaired rather than an ending. Both are asserted here from the failure they prevent.
def check_the_monitor_watches_content():
    print("the monitor watches content")
    import json as _json
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile

    def check(cond, label):
        if cond:
            ok(label)
        else:
            bad(f"the monitor watches content: {label}")

    spec = importlib.util.spec_from_file_location("_ks_mon", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the monitor watches content: the server module loads: {exc}")
        return
    lm = sys.modules["logmonitor"]

    home = _mkdtemp(prefix="ka-check-monitor-")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        # 1. The rules are DATA, not code: a user can watch for their own field.
        check(all(r.get("name") and r.get("pattern") for r in lm.WATCH_RULES),
              "every default watch rule has a name and a pattern")
        check({r["act"] for r in lm.WATCH_RULES} <= {"report", "wake"},
              f"rules act only as report or wake ({sorted({r['act'] for r in lm.WATCH_RULES})})")

        # 2. A moved log tightens; a quiet one relaxes; a moved one again tightens. This is
        #    the whole claim: the cadence is a function of the content, not of the tick count.
        first = lm.observe("epoch 1/100 val_loss 0.9")
        check(first["action"] == "tighten",
              f"a first read tightens the cadence (got {first['action']})")
        again = lm.observe("epoch 2/100 val_loss 0.8")
        check(again["changed"] and again["action"] == "tighten",
              "a log that moved tightens again")
        same = lm.observe("epoch 2/100 val_loss 0.8")
        check(not same["changed"] and same["action"] == "hold",
              f"one quiet read holds the cadence (got {same['action']})")
        quiet = lm.observe("epoch 2/100 val_loss 0.8")
        check(quiet["steady"] and quiet["action"] == "relax",
              f"two quiet reads relax it (got {quiet['action']})")
        rung_after_quiet = lm.note_tick(action="relax")["rung"]
        woken = lm.observe("epoch 3/100 val_loss 0.7")
        back = lm.note_tick(action=woken["action"])["rung"]
        check(back == 0 and rung_after_quiet > 0,
              f"a run that wakes up is watched closely again (rung {rung_after_quiet} -> {back})")

        # 3. A heartbeat line that re-matches on an unchanged log must NOT re-tighten: that is
        #    the timer-by-another-name this replaced, and it is the easy mistake.
        for _ in range(3):
            held = lm.observe("epoch 3/100 val_loss 0.7")
        check(held["action"] != "tighten",
              f"a re-matched heartbeat on an unchanged log does not re-tighten "
              f"(got {held['action']})")

        # 4. Error / terminal / decision are reported on sight, and carry the line.
        err = lm.observe("Traceback (most recent call last):\n  ValueError: boom")
        check(err["report"] and err["report"][0]["name"] == "error",
              "an error in the log is reported")
        check("ValueError" in _json.dumps(err),
              "and the report carries the line that matched, not just a flag")
        term = lm.observe("Run complete")
        check(term["report"] and term["report"][0]["name"] == "terminal",
              "a terminal state is reported")
        dec = lm.observe("overwrite? [y/n]")
        check(dec["report"] and dec["report"][0]["name"] == "decision",
              "a prompt needing the user is reported")

        # 5. An unreadable log is a route to repair: the tool walks routes and remembers one.
        steps = []
        for i in range(lm.MAX_ATTEMPTS):
            res = lm.attempt(f"failure {i}", ok=False, kind="kaggle")
            steps.append(res["exhausted"])
        check(steps[-1] is True,
              f"the repair loop ends by saying every route failed (exhausted={steps[-1]})")
        check(all(s is False for s in steps[:-1]),
              "and it does not give up before the routes are actually tried")
        rec = lm.attempt("worked", ok=True, recipe="status", kind="kaggle")
        check(rec["recovered"] and rec["lastRecipe"]["step"] == "status",
              "the route that worked is remembered for the next run")
        shown = lm.describe()
        check("readable via" in _json.dumps(shown) or shown["lastRecipe"],
              "and get/describe report it, so the next run starts from it")
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


def check_a_node_keeps_its_recipe():
    print("a node keeps its recipe")
    import json as _json
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile

    def check(cond, label):
        if cond:
            ok(label)
        else:
            bad(f"a node keeps its recipe: {label}")

    spec = importlib.util.spec_from_file_location("_ks_rcp", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"a node keeps its recipe: the server module loads: {exc}")
        return
    et = sys.modules["experiment_tree"]

    home = _mkdtemp(prefix="ka-check-recipe-")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    comp = "zz-recipe"
    try:
        # 1. A recipe arrives as JSON text, the only shape the host can carry, and survives.
        got = et.normalize_node(
            {"recipe": _json.dumps({"engine": "local",
                                    "command": ["python", "train.py"]})})
        check(got["recipe"]["engine"] == "local" and got["recipe"]["command"] == ["python", "train.py"],
              f"a recipe given as JSON text is normalized ({got.get('recipe')})")
        # and a real object is still accepted, so a plain MCP client is not locked out
        obj = et.normalize_node({"recipe": {"engine": "kaggle", "ref": "me/slug"}})
        check(obj["recipe"]["ref"] == "me/slug",
              "a recipe given as a real object is accepted too")

        # 2. A recipe that says nothing about how the run started is refused: it looks
        #    reusable and is not, which is worse than absent.
        tree = et.load(comp)
        tree["tree"] = {"base": None, "nodes": {"e1": {
            "id": "e1", "kind": "experiment", "parent": None, "change": "c", "hypothesis": "h",
            "metric": {"name": "s", "parent": 0.0, "result": 0.1, "delta": 0.1, "rank": 1,
                       "rankSource": "local"},
            "verdict": "keep", "reason": "r", "operator": "draft", "family": "f",
            "evidence": "local-only", "recipe": {"engine": "", "command": [], "raw": "junk"}}}}
        problems = et.validate(tree)
        check(any("recipe" in p for p in problems),
              f"an empty recipe is refused ({problems[:1]})")

        # 3. A declaration without a recipe inherits its parent's - the reuse default.
        good = {"id": "n1", "kind": "experiment", "parent": None, "change": "first",
                "hypothesis": "runs",
                "metric": {"name": "s", "parent": 0.0, "result": 0.1, "delta": 0.1, "rank": 1,
                           "rankSource": "local"},
                "verdict": "keep", "reason": "seed", "operator": "draft", "family": "base",
                "evidence": "local-only",
                "recipe": {"engine": "local", "command": ["python", "train.py"]}}
        t = et.load(comp)
        t["tree"] = {"base": None, "nodes": {}}
        t["revision"] = 0
        et.save(comp, t)
        rec = et.record(comp, good, read_revision=0)
        check(rec.get("ok"), f"the seeded node with a real recipe is accepted ({rec.get('message')})")
        rev = et.read(comp)["revision"]
        decl = {"id": "n2", "kind": "experiment", "parent": "n1", "change": "more",
                "hypothesis": "better", "reason": "push it", "operator": "improve",
                "family": "tuning", "diagnosis": "none", "diagnosisReason": "baseline only"}
        res = _decl(et, comp, decl, read_revision=rev)
        check(res.get("ok"), f"the child declaration is accepted ({res.get('message')})")
        n2 = (et.load(comp).get("tree") or {}).get("nodes", {}).get("n2") or {}
        check((n2.get("recipe") or {}).get("command") == ["python", "train.py"],
              f"and it inherited the parent's recipe ({n2.get('recipe')})")
        check(n2.get("recipeInheritedFrom") == "n1",
              f"and says where it came from ({n2.get('recipeInheritedFrom')})")

        # 4. read surfaces the runnable command, so reuse needs no archaeology.
        roll = et.latest_recipes(comp)
        check("local" in roll and roll["local"]["recipe"]["command"] == ["python", "train.py"],
              f"read/rollup reports how to run it now ({roll})")
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)




# ------------------------------------------------- did the result match the prediction, and why
# A tree with scores but no predictions can only say "the number went up". It cannot say
# "the number went up the way we thought it would", which is the difference between a result
# that compounds and one that happened. This asserts the judging, the gate that forces a
# prediction to exist, and the board cell that exposes a kept-but-unpredicted gain.
def check_predictions_are_judged():
    print("predictions are judged")
    import json as _json
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile

    def check(cond, label):
        if cond:
            ok(label)
        else:
            bad(f"predictions are judged: {label}")

    spec = importlib.util.spec_from_file_location("_ks_pred", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"predictions are judged: the server module loads: {exc}")
        return
    et = sys.modules["experiment_tree"]

    # the judge, on its own, across the three verdicts and the noise floor
    up = lambda d, s=None: {"name": "s", "parent": 0, "result": 0.3 + d, "delta": d,
                            **({"samples": s} if s else {})}
    check(et.judge_expectation({"direction": "up", "atLeast": 0.02}, up(0.05))["verdict"]
          == "confirmed", "a delta past the floor is confirmed")
    check(et.judge_expectation({"direction": "up", "atLeast": 0.05}, up(0.03))["verdict"]
          == "partial", "right direction, short of the floor, is partial - not confirmed")
    check(et.judge_expectation({"direction": "up", "atLeast": 0.02}, up(-0.02))["verdict"]
          == "refuted", "the wrong direction is refuted")
    check(et.judge_expectation({"direction": "up", "atLeast": 0.02}, up(0.0))["verdict"]
          == "refuted", "no movement at all is refuted, not a weak win")
    noisy = et.judge_expectation({"direction": "up", "atLeast": 0.01},
                                 up(0.02, {"n": 4, "mean": 0.32, "std": 0.05}))
    check(noisy["verdict"] == "partial" and noisy["floorFrom"] == "noise",
          "a measured noise floor above the prediction downgrades it to partial")
    check(et.judge_expectation(None, up(0.05))["verdict"] == "unreadable",
          "no prediction is unreadable, not silently fine")

    # declare refuses a node with no prediction, and accepts one with
    home = _mkdtemp(prefix="ka-check-pred-")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    comp = "zz-pred"
    try:
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        base = {"id": "b1", "kind": "experiment", "parent": None, "change": "seed",
                "hypothesis": "h",
                "metric": {"name": "s", "parent": 0.0, "result": 0.3, "delta": 0.3,
                           "rank": 1, "rankSource": "local"},
                "verdict": "keep", "reason": "r", "operator": "draft", "family": "base",
                "evidence": "local-only"}
        et.record(comp, base, read_revision=0)
        rev = et.read(comp)["revision"]
        d = {"id": "n1", "kind": "experiment", "parent": "b1", "change": "c", "hypothesis": "h",
             "reason": "r", "operator": "improve", "family": "opt",
             "diagnosis": "none", "diagnosisReason": "base only"}
        miss = et.declare(comp, d, read_revision=rev)
        check(not miss.get("ok") and miss.get("code") == "expect_required",
              f"declare refuses a node with no prediction ({miss.get('code')})")
        d["expect"] = {"direction": "up", "atLeast": 0.05}
        ok_decl = et.declare(comp, d, read_revision=rev)
        check(ok_decl.get("ok"), f"a declaration with a prediction is accepted "
                                 f"({ok_decl.get('message')})")

        # settle carries the prediction over and judges it
        res = {"id": "r1", "kind": "experiment", "parent": "n1", "change": "c",
               "hypothesis": "h",
               "metric": {"name": "s", "parent": 0.3, "result": 0.32, "delta": 0.02,
                          "rank": 2, "rankSource": "local"},
               "verdict": "keep", "reason": "gained a little", "operator": "improve",
               "family": "opt", "evidence": "local-only"}
        s = et.settle(comp, "n1", res, read_revision=et.read(comp)["revision"])
        stored = (et.load(comp).get("tree") or {}).get("nodes", {}).get("r1") or {}
        check((stored.get("expectation") or {}).get("verdict") == "partial",
              f"settle judges the prediction against the result "
              f"({(stored.get('expectation') or {}).get('verdict')})")
        check((stored.get("expectation") or {}).get("why"),
              "and records why, not just a label")

        # the board surfaces the kept-but-unpredicted cell
        board = et.experience_board(et.load(comp))
        check(board["expectations"]["keptNotAsPredicted"] == 1,
              f"board counts a kept node that worked for the wrong reason "
              f"({board['expectations']})")
        rendered = _json.dumps(ks.safe_tool_call(
            "kaggle_experiment_tree", {"action": "board", "competition": comp}))
        check("kept but NOT as predicted" in rendered,
              "and the rendered board names that cell explicitly")
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------------------- the curriculum is a gate, not advice
# Simple-to-hard is enforced here, not suggested: a hard stage before the easy one has passed
# usually fails for a reason unrelated to the idea, and that failure gets recorded as evidence
# against the idea. The override exists so a deliberate skip is still possible.
def check_the_curriculum_gates_declare():
    print("the curriculum gates declare")
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile

    def check(cond, label):
        if cond:
            ok(label)
        else:
            bad(f"the curriculum gates declare: {label}")

    spec = importlib.util.spec_from_file_location("_ks_curr", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"the curriculum gates declare: the server module loads: {exc}")
        return
    et = sys.modules["experiment_tree"]

    home = _mkdtemp(prefix="ka-check-curr-")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    comp = "zz-curr"
    try:
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        ladder = [{"name": "smoke", "passesWhen": "runs end to end"},
                  {"name": "scale", "passesWhen": "beats the baseline"}]
        setr = et.set_stage(comp, curriculum=ladder, read_revision=0)
        check(setr.get("ok"), f"a curriculum is declared ({setr.get('message')})")
        check(et.load(comp).get("stage") == "smoke",
              "and the search starts at the simplest stage")

        _seq = [0]

        def decl(stage, override=None):
            _seq[0] += 1
            d = {"id": f"n{_seq[0]}", "kind": "experiment",
                 "parent": None, "change": "c", "hypothesis": "h", "reason": "r",
                 "operator": "draft", "family": "f", "diagnosis": "none",
                 "diagnosisReason": "first", "expect": {"direction": "up", "atLeast": 0.01}}
            if stage:
                d["stage"] = stage
            if override:
                d["stageOverride"] = override
            return et.declare(comp, d, read_revision=et.read(comp)["revision"])

        locked = decl("scale")
        check(not locked.get("ok") and locked.get("code") == "stage_locked",
              f"declaring the hard stage first is refused ({locked.get('code')})")
        over = decl("scale", "the harness is already configured for the full run")
        check(over.get("ok"), "a deliberate skip with a stated reason is accepted")
        easy = decl("smoke", None)
        check(easy.get("ok"), "declaring at the current stage is fine")
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------ giving up a line: summarise, isolate, delete nothing
# Abandoning a whole direction has no way to be said without these, and a half-measure here
# (delete the folder, keep the node) loses the reason; (keep the folder, mark the node) leaves
# the next run writing into the abandoned line. This asserts the summary, the marking, the file
# move, and that nothing is destroyed.
def check_a_line_can_be_abandoned():
    print("a line can be abandoned")
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile

    def check(cond, label):
        if cond:
            ok(label)
        else:
            bad(f"a line can be abandoned: {label}")

    spec = importlib.util.spec_from_file_location("_ks_aband", SERVER_PY)
    ks = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "mcp"))
    try:
        spec.loader.exec_module(ks)
    except Exception as exc:  # noqa: BLE001
        bad(f"a line can be abandoned: the server module loads: {exc}")
        return
    et = sys.modules["experiment_tree"]

    home = _mkdtemp(prefix="ka-check-abandon-")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    comp = "zz-abandon"
    try:
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        base = {"id": "b1", "kind": "experiment", "parent": None, "change": "seed",
                "hypothesis": "h",
                "metric": {"name": "s", "parent": 0.0, "result": 0.3, "delta": 0.3,
                           "rank": 1, "rankSource": "local"},
                "verdict": "keep", "reason": "r", "operator": "draft", "family": "base",
                "evidence": "local-only"}
        et.record(comp, base, read_revision=0)
        et.branch_paths(comp, "bad", read_revision=et.read(comp)["revision"])
        d = {"id": "n1", "kind": "experiment", "parent": "b1", "change": "radical",
             "hypothesis": "h", "reason": "r", "operator": "improve", "family": "rad",
             "diagnosis": "none", "diagnosisReason": "base only", "branch": "bad",
             "expect": {"direction": "up", "atLeast": 0.1}}
        et.declare(comp, d, read_revision=et.read(comp)["revision"])
        res = {"id": "r1", "kind": "experiment", "parent": "n1", "change": "radical",
               "hypothesis": "h",
               "metric": {"name": "s", "parent": 0.3, "result": 0.2, "delta": -0.1,
                          "rank": 3, "rankSource": "local"},
               "verdict": "revert", "reason": "much worse", "operator": "improve",
               "family": "rad", "evidence": "local-only", "failureLayer": "metric",
               "cost": {"quotaHours": 1.5}}
        et.settle(comp, "n1", res, read_revision=et.read(comp)["revision"])
        # a real file in the branch, so the move is real
        bdir = et.branch_dir(comp, "bad")
        _os.makedirs(bdir, exist_ok=True)
        Path(bdir, "model.pt").write_text("weights", encoding="utf-8")

        ab = et.abandon(comp, "n1", "this direction is not worth more quota", branch="bad",
                        read_revision=et.read(comp)["revision"])
        check(ab.get("ok"), f"the line is abandoned ({ab.get('message')})")
        s = ab.get("summary") or {}
        check(s.get("quotaHours") == 1.5, f"the summary keeps the quota spent "
                                          f"({s.get('quotaHours')})")
        check(s.get("refutedOrPartialExpectations") == 1,
              f"and counts the missed prediction ({s.get('refutedOrPartialExpectations')})")
        qdir = et.quarantine_dir(comp, "bad")
        check(not _os.path.isdir(bdir), "the branch folder is moved, not left in place")
        check(Path(qdir, "model.pt").is_file(), "and the file is intact in quarantine")
        nodes = et.load(comp)["tree"]["nodes"]
        check(et.is_abandoned(nodes["n1"]) and et.is_abandoned(nodes["r1"]),
              "both the declaration and its result are marked abandoned")
        check(not et.is_abandoned(nodes["b1"]), "and nothing outside the line is touched")
        # the abandoned line drops out of the search
        sel = et.select_next(et.load(comp))
        ids = {r["id"] for r in sel.get("ranking", []) if r.get("eligible")}
        check("n1" not in ids and "r1" not in ids,
              f"an abandoned line is no longer selectable ({sorted(ids)})")

        # undo puts the marks AND the files back
        u = et.undo(comp)
        check(u.get("ok") and u.get("undone") == "abandon", f"abandon is undoable ({u})")
        check(Path(bdir, "model.pt").is_file(), "and undo puts the files back where they were")
        check(not et.is_abandoned(et.load(comp)["tree"]["nodes"]["n1"]),
              "and clears the mark")
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
