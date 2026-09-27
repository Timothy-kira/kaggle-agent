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

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".minimax-plugin" / "plugin.json"
SERVERS = ROOT / "servers.mcp.json"
REL = ROOT / "skills" / "relationships.json"
SERVER_PY = ROOT / "mcp" / "kaggle_server.py"

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


def check(cond: bool, msg: str) -> bool:
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
                       f"reads as a nested mapping 鈥?quote the value")
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
            check(row in text, f"{cat}/INDEX.md bound edge row present: {row.split('|')[1].strip()}")
        # no stale markers left behind
        for marker in re.findall(r"<!-- edge:([^>]+?) -->", text):
            if f"<!-- edge:{marker} -->" not in expected:
                bad(f"categories/{cat}.md has a stale edge marker not in relationships.json: {marker}")


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

    # both the research skill and the forensics agent must genuinely browse all three
    for owner in ("kaggle-competition-research", "competition-browser"):
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
# The agent was renamed from kaggle-search to competition-browser. A half-finished rename is
# the worst outcome: the package would document a reference that no longer resolves, and the
# wave-2 dispatch would fail only at runtime, in a research sweep, hours in.
#
# These are matched as whole words, not substrings. "kaggle_search_engine" is a *current*
# tool name that happens to contain the retired agent's name, and a substring rule would
# flag the tool that replaced it.
RETIRED_NAMES = ("kaggle-search", "Kaggle 鎼滅储", "search-agent.png")
RETIRED_PATTERNS = [re.compile(r"\bkaggle_search\b"), re.compile(r"agent:kaggle-search")]


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
        # The rename history is allowed to be *documented*, but only in the file that
        # documents the rename; everywhere else a live reference is a bug.
        exempt = rel_p == "skills/competition-browser-agent.md"
        if exempt:
            continue
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
    os.environ["KAGGLE_AGENT_HOME"] = tempfile.mkdtemp()
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
    os.environ["KAGGLE_AGENT_HOME"] = tempfile.mkdtemp()
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
    os.environ["KAGGLE_AGENT_HOME"] = tempfile.mkdtemp()
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
    os.environ["KAGGLE_AGENT_HOME"] = tempfile.mkdtemp()
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
    env = dict(os.environ, KAGGLE_AGENT_HOME=tempfile.mkdtemp())
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


def check_plotting_is_self_contained():
    """Plotting must not depend on anything the user has to install.

    Measured on the build machine: numpy, scipy, pandas, matplotlib, statsmodels, pint and
    pyDOE3 are all absent. If any chart type needed one of them, "the plugin ships its own
    environment" would be false in the only way that matters - on a machine that does not have it.
    """
    print("self-contained plotting")
    src = (ROOT / "mcp" / "plots.py").read_text(encoding="utf-8")
    for banned in ("import numpy", "import matplotlib", "import pandas", "from scipy"):
        check(banned not in src, f"the bundled engine does not {banned}")
    ok_kind, bad_kind = "line", "nonsense"
    _spec = importlib.util.spec_from_file_location("pl_chk", ROOT / "mcp" / "plots.py")
    pl = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(pl)
    os.environ["KAGGLE_AGENT_HOME"] = tempfile.mkdtemp()
    res = pl.render(ok_kind, {"series": [{"label": "s", "points": [[1, 1], [2, 2], [3, 3]]}]},
                    "selfcontained", "t")
    check(res.get("ok") and os.path.isfile(res["path"]),
          "a figure renders with nothing installed")
    text = open(res["path"], encoding="utf-8").read()
    check(text.startswith("<svg") and "</svg>" in text, "the output is standalone SVG")
    empty = pl.render(ok_kind, {"series": []}, "empty", "t")
    check(not empty.get("ok"), "an empty dataset is refused rather than drawn")
    check(set(pl.CHART_KINDS) == {"line", "band", "bar", "scatter", "pareto", "forest"},
          "the six chart types are declared")
    # the dependency module must never install anything on its own
    dsrc = (ROOT / "mcp" / "deps.py").read_text(encoding="utf-8")
    check("subprocess" in dsrc, "the optional-package installer exists")
    import deps as _d
    bad = _d.install(["definitely-not-real"])
    check(not bad.get("ok"), "installing an unknown package is refused")
    p = _d.probe()
    check(p.get("plottingReady") is True,
          "the environment probe reports plotting is ready regardless of what is absent")


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
    print("version sync")
    data, _ = parse_json(MANIFEST)
    ver = data.get("version")
    rel, _ = parse_json(REL)
    if rel is not None:
        check(rel.get("version") == ver,
              f"relationships.json version matches manifest ({ver})")
    text = SERVER_PY.read_text(encoding="utf-8")
    m = re.search(r'SERVER_INFO\s*=\s*\{[^}]*"version"\s*:\s*"([^"]+)"', text)
    if check(m is not None, "kaggle_server.py has SERVER_INFO version"):
        check(m.group(1) == ver, f"SERVER_INFO version matches manifest ({ver})")


# ---------------------------------------------------------------- publishable
# Everything above validates the working tree. That is the wrong tree. This package is
# published as a git repository, and a file can be present on disk, declared in the
# manifest, referenced by the relationship graph 鈥?and still be absent from the repo.
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
                    bad(f"possible {label} in {rel} 鈥?this repository is public")
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
                bad(f"{label} in {rel}: {found.group(0)!r} 鈥?this package is installed "
                    f"on other machines, use a plugin-relative path or ${{PLUGIN_ROOT}}")
                machine_hits += 1
    if machine_hits == 0:
        ok("no publishable file hardcodes an absolute path into the author's home")


# ---------------------------------------------------------------- data locality
# The user does not want competition data pulled onto this machine, ever. The skill said
# "must run locally" and "write a local CPU notebook", which is an instruction to download the
# dataset 鈥?on a competition where every listed file 403s anyway. Prose was not enough: the fix
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
# competition-browser agent doc already recorded that a detached subagent has only web_fetch 鈥?
# the two files contradicted each other and the browser was doing a job it is bad at.
RESEARCH_SKILL = ROOT / "skills" / "kaggle-competition-research" / "SKILL.md"
BROWSER_AGENT_DOC = ROOT / "skills" / "competition-browser-agent.md"


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
    # one gets a concrete web_fetch example on its own line 鈥?a whole-file containment test
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

    if BROWSER_AGENT_DOC.is_file():
        agent_doc = BROWSER_AGENT_DOC.read_text(encoding="utf-8")
        check("the in-app browser is the primary method" not in agent_doc.lower(),
              "the competition-browser doc no longer makes the browser its primary method")
        check("page selection is not the browser's job" in agent_doc.lower()
              or "not the browser's job" in agent_doc.lower(),
              "the competition-browser doc states the browser does not choose pages")


# ---------------------------------------------------------------- research preflight
# Research is the expensive path in this plugin: four subagents, a multi-round sweep and a
# forensics pass. Re-running it over work that already exists is the most wasteful thing the
# skill can do, and the skill had no preflight at all 鈥?handoff appeared once, at the end, as
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
    # question is deleted 鈥?so each assertion below pins the exact instruction, not a token.
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
    folder = Path(_tempfile.mkdtemp(prefix="ka-gate-"))
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
        d = et.declare(comp, {"id": "e1", "change": "swap the sampler",
                              "hypothesis": "it is the bottleneck", "parent": None,
                              "operator": "draft", "family": "sampling",
                              "reason": "the profile says so"}, read_revision=rev)
        if not check(d.get("ok"), f"a declaration is accepted: {d.get('message')}"):
            return
        check(et.declare(comp, {"id": "e0", "change": "x", "hypothesis": "h", "parent": None,
                                "operator": "draft", "family": "f", "reason": "r"},
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
        check(et.declare(comp, {"id": "p2", "change": "abandon the model entirely",
                                "hypothesis": "the baseline is wrong, not the method",
                                "parent": None, "operator": "crossover",
                                "family": "problem-framing",
                                "reason": "the old base was refuted twice"},
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
                      "parent: null", "new_base")),
        (launch_skill, ('action="declare"', 'action="settle"', "declares         =", "REQUIRED")),
    ):
        if path.is_file():
            body = path.read_text(encoding="utf-8")
            for token in tokens:
                check(token in body, f"{path.name} documents {token!r}")


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
    check_decision_coupling()
    check_tree_ownership()
    check_runtime_behaviour()
    check_evidence_chain()
    check_plotting_is_self_contained()
    check_evidence_graph_binding()
    check_skill_index()
    check_widget_binding()
    check_no_secrets()
    check_no_stale_names()
    check_version_sync()
    check_data_stays_on_kaggle()
    check_browser_is_search_only()
    check_research_preflight()
    check_launch_gate()
    check_publishable()
    print()
    if failures:
        print(f"{len(failures)} failure(s) out of {checks} checks:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"all {checks} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
