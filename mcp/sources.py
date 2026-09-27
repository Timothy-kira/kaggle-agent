"""The evidence store: every paper, repo and dataset the search touched, kept and linkable.

Why the tree alone is not enough
-------------------------------
A node records what you concluded and why. It does not record *what you read* to conclude it. So
a review three weeks later can show that n7 was kept for "+0.09, errors shifted from malformed
to genuine dead ends", and nothing about which paper said that, which commit was forked, or
whether that paper had a licence. The reasoning is unreviewable and unreusable, and the next
session re-derives it from scratch.

This module is the missing half. Every source the search reads is stored once, with the parts
that matter for a decision: what it claims, the exact quote that carries the claim, its licence,
and whether anyone has checked it yet. Nodes then link to source ids rather than restating them,
so "what evidence does this decision rest on" is a lookup rather than an archaeology exercise.

Why separate storage, then links
--------------------------------
Because the same paper supports five nodes, and a claim quoted into five node bodies will drift
away from the paper within a couple of edits. One stored record, five references, and the
reverse direction - "what else does this paper support" - becomes answerable for free.

Storage: ``<home>/sources/<id>.json``, one file per source, so concurrent writers to different
papers cannot collide. ``<home>`` is ``KAGGLE_AGENT_HOME`` when set, else ``~/.kaggle-agent``.

``extracts`` is the part that carries the weight. A link with no quote is a link someone asserts
existed; a link with the sentence that supports the claim is a link a reviewer can check.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = 1

# A missing licence is a finding, not a blank field: it decides whether the work can be built on
# at all, so it is part of the record rather than an optional extra.
SOURCE_KINDS = ("paper", "preprint", "code", "dataset", "model", "forum", "doc", "blog")

LICENCE_FAMILIES = (
    "mit", "apache-2.0", "bsd", "gpl", "lgpl", "cc-by", "cc-by-nc", "cc0", "proprietary",
    "unknown", "none", "unspecified",
)

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,95}$", re.I)


def _home() -> str:
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent"
    )


def store_dir() -> str:
    return os.path.join(_home(), "sources")


def path_for(source_id: str) -> str:
    return os.path.join(store_dir(), f"{source_id}.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str, prefix: str = "s") -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    base = base[:70] or "untitled"
    return f"{prefix}-{base}"


def _read(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write(sid: str, data: dict[str, Any]) -> str:
    path = path_for(sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def _new_id(data: dict[str, Any], kind: str) -> str:
    prefix = {"code": "c", "dataset": "d", "model": "m", "forum": "f"}.get(kind, "s")
    n = 1
    while f"{prefix}{n}" in data:
        n += 1
    return f"{prefix}{n}"


def load_all() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    d = store_dir()
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        rec = _read(os.path.join(d, name))
        if rec and rec.get("id"):
            out[str(rec["id"])] = rec
    return out


def get(source_id: str) -> Optional[dict[str, Any]]:
    return _read(path_for(source_id))


def add(kind: str, title: str, url: str = "", authors: str = "", published: str = "",
        venue: str = "", summary: str = "", licence: str = "", source_id: str = "",
        doi: str = "", arxiv: str = "", notes: str = "",
        extracts: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Store one source, or return the existing record if the URL was already stored.

    Storing the same paper twice is the failure mode that quietly rots a ledger: two ids, one
    paper, and a node linked to the stale one. So a URL or arXiv id that is already present
    returns the existing record instead of creating a rival.
    """
    kind = (kind or "paper").strip().lower()
    if kind not in SOURCE_KINDS:
        return {"ok": False, "code": "bad_kind",
                "message": f"kind must be one of {', '.join(SOURCE_KINDS)}, got {kind!r}"}
    if not (title or "").strip():
        return {"ok": False, "code": "no_title", "message": "a source needs a title"}

    existing = load_all()
    for rec in existing.values():
        same_url = url and rec.get("url") and rec["url"].strip() == url.strip()
        same_arxiv = arxiv and rec.get("arxiv") and rec["arxiv"].strip().lower() == arxiv.strip().lower()
        same_doi = doi and rec.get("doi") and rec["doi"].strip().lower() == doi.strip().lower()
        if same_url or same_arxiv or same_doi:
            return {"ok": True, "duplicate": True, "source": rec,
                    "message": f"already stored as {rec['id']}; not creating a rival record",
                    "path": path_for(rec["id"])}

    sid = (source_id or "").strip() or _new_id(existing, kind)
    if not ID_RE.match(sid):
        return {"ok": False, "code": "bad_id", "message": f"id {sid!r} must match {ID_RE.pattern}"}
    if sid in existing:
        return {"ok": False, "code": "duplicate_id", "message": f"id {sid!r} is already used"}

    rec = {
        "schemaVersion": SCHEMA_VERSION,
        "id": sid,
        "kind": kind,
        "title": title.strip(),
        "url": url.strip(),
        "doi": doi.strip(),
        "arxiv": arxiv.strip(),
        "authors": authors.strip(),
        "published": published.strip(),
        "venue": venue.strip(),
        "summary": summary.strip(),
        "licence": (licence or "unknown").strip().lower(),
        "notes": notes.strip(),
        "extracts": _clean_extracts(extracts),
        "addedAt": _now(),
        "revision": 1,
    }
    path = _write(sid, rec)
    return {"ok": True, "source": rec, "path": path}


def _clean_extracts(raw: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out = []
    for e in raw or []:
        if not isinstance(e, dict):
            continue
        quote = str(e.get("quote") or "").strip()
        if not quote:
            continue
        out.append({
            "quote": quote[:2000],
            "claim": str(e.get("claim") or "").strip()[:400],
            "locator": str(e.get("locator") or "").strip()[:200],
        })
    return out


def add_extract(source_id: str, quote: str, claim: str = "", locator: str = "") -> dict[str, Any]:
    """Attach the sentence that carries a claim.

    This is the whole reason the store exists. A link without the supporting sentence is a claim
    that cannot be checked, and an uncheckable claim is exactly what a review cannot use.
    """
    rec = get(source_id)
    if not rec:
        return {"ok": False, "code": "unknown_source", "message": f"no source {source_id!r}"}
    quote = (quote or "").strip()
    if not quote:
        return {"ok": False, "code": "no_quote", "message": "an extract without a quote proves nothing"}
    rec.setdefault("extracts", []).append(
        {"quote": quote[:2000], "claim": claim.strip()[:400], "locator": locator.strip()[:200]})
    rec["revision"] = int(rec.get("revision", 1)) + 1
    path = _write(source_id, rec)
    return {"ok": True, "sourceId": source_id, "extracts": len(rec["extracts"]),
            "added": rec["extracts"][-1], "path": path}


def link_to_node(competition: str, node_id: str, source_id: str,
                 relation: str = "supports", quote: str = "") -> dict[str, Any]:
    """Attach a source to a tree node, recording the relation and optionally the quote.

    ``relation`` is not decoration. "supports", "motivates", "contradicts" and "supersedes" are
    different claims, and a tree that flattens them all to "references" cannot later answer
    which evidence actually justified a kept node and which evidence merely suggested the next one.
    """
    import experiment_tree as et
    src = get(source_id)
    if not src:
        return {"ok": False, "code": "unknown_source", "message": f"no source {source_id!r}"}
    tree = et.load(competition)
    inner = et._current(tree)
    node = (inner.get("nodes") or {}).get(node_id)
    if not node:
        return {"ok": False, "code": "unknown_node", "message": f"no node {node_id!r} in {competition!r}"}
    if quote:
        add_extract(source_id, quote, claim=f"cited by node {node_id}")

    refs = node.setdefault("sources", [])
    for r in refs:
        if r.get("sourceId") == source_id and r.get("relation") == relation:
            return {"ok": True, "duplicate": True, "nodeId": node_id, "sourceId": source_id,
                    "relation": relation, "links": len(refs),
                    "message": f"node {node_id} already {relation}s {source_id}"}
    refs.append({"sourceId": source_id, "relation": relation,
                 "quote": quote.strip()[:2000] or "", "linkedAt": _now()})
    inner["nodes"][node_id] = node
    tree["revision"] = int(tree["revision"]) + 1
    path = et.save(competition, tree)
    return {"ok": True, "nodeId": node_id, "sourceId": source_id, "relation": relation,
            "sourceTitle": src.get("title"), "links": len(refs), "path": path}


def unlink(competition: str, node_id: str, source_id: str) -> dict[str, Any]:
    import experiment_tree as et
    tree = et.load(competition)
    inner = et._current(tree)
    node = (inner.get("nodes") or {}).get(node_id)
    if not node:
        return {"ok": False, "code": "unknown_node", "message": f"no node {node_id!r}"}
    before = len(node.get("sources") or [])
    node["sources"] = [r for r in (node.get("sources") or [])
                       if r.get("sourceId") != source_id]
    inner["nodes"][node_id] = node
    tree["revision"] = int(tree["revision"]) + 1
    et.save(competition, tree)
    return {"ok": True, "nodeId": node_id, "sourceId": source_id,
            "removed": before - len(node["sources"])}


def search(query: str = "", kind: str = "", licence: str = "",
           needs_extract: bool = False) -> list[dict[str, Any]]:
    """Find stored sources. `needs_extract` surfaces the ones nobody has quoted yet.

    Those are the dangerous ones: a source linked into a decision with no supporting sentence
    cannot be reviewed, so they are worth surfacing on purpose.
    """
    q = (query or "").strip().lower()
    k = (kind or "").strip().lower()
    lic = (licence or "").strip().lower()
    out = []
    for rec in load_all().values():
        if k and rec.get("kind") != k:
            continue
        if lic and rec.get("licence") != lic:
            continue
        if needs_extract and not rec.get("extracts"):
            continue
        if q:
            hay = " ".join(str(rec.get(f, "")) for f in
                           ("title", "summary", "authors", "url", "venue", "arxiv", "notes")).lower()
            hay += " " + " ".join(str(e.get("quote", "")).lower()
                                  for e in rec.get("extracts") or [])
            if q not in hay:
                continue
        out.append({
            "id": rec.get("id"), "kind": rec.get("kind"), "title": rec.get("title"),
            "url": rec.get("url"), "arxiv": rec.get("arxiv"), "licence": rec.get("licence"),
            "published": rec.get("published"),
            "extracts": len(rec.get("extracts") or []),
            "summary": (rec.get("summary") or "")[:220],
        })
    return sorted(out, key=lambda r: (r["id"] or ""))


def backlink(source_id: str) -> dict[str, Any]:
    """Which nodes across every tree reference this source, and how.

    The direction a ledger is usually missing. A paper that supported three decisions in a row
    is a much stronger reason to trust it than one that was mentioned once.
    """
    import experiment_tree as et
    rec = get(source_id)
    if not rec:
        return {"ok": False, "code": "unknown_source", "message": f"no source {source_id!r}"}
    hits = []
    handoff_root = os.path.join(_home(), "handoff")
    if os.path.isdir(handoff_root):
        for comp in sorted(os.listdir(handoff_root)):
            tree = et.load(comp)
            for nid, node in (et._current(tree).get("nodes") or {}).items():
                for r in node.get("sources") or []:
                    if r.get("sourceId") == source_id:
                        hits.append({
                            "competition": comp, "nodeId": nid,
                            "relation": r.get("relation"),
                            "change": node.get("change") or node.get("question"),
                            "verdict": node.get("verdict"),
                        })
    return {"ok": True, "sourceId": source_id, "title": rec.get("title"),
            "citedBy": len(hits), "hits": hits}


def coverage(competition: str) -> dict[str, Any]:
    """How much of this tree's reasoning is actually backed by a stored, quotable source.

    A number, because "are we citing things" is a question that otherwise only gets answered by
    reading every node.
    """
    import experiment_tree as et
    tree = et.load(competition)
    nodes = et._current(tree).get("nodes") or {}
    scored = [n for n in nodes.values() if isinstance(n, dict) and n.get("verdict")]
    with_src = [n for n in scored if n.get("sources")]
    local = [n for n in scored if n.get("evidence") == "local-only"]
    unsupported = [n.get("id") for n in scored if not n.get("sources") and n.get("evidence") != "local-only"]
    quoted = 0
    for n in with_src:
        if any(r.get("quote") for r in n.get("sources") or []):
            quoted += 1
    return {
        "competition": competition,
        "scoredNodes": len(scored),
        "withSources": len(with_src),
        "declaredLocalOnly": len(local),
        "unsupported": len(unsupported),
        "unsupportedNodeIds": unsupported,
        "withInlineQuote": quoted,
        "ratio": (round(len(with_src) / len(scored), 3) if scored else None),
        "note": (
            "a node with no source and no 'local-only' note is a conclusion with nothing behind "
            "it. Either link what you read, or say explicitly that the evidence was a local run."
        ),
    }


def stats() -> dict[str, Any]:
    recs = load_all()
    by_kind: dict[str, int] = {}
    by_licence: dict[str, int] = {}
    no_licence = 0
    no_extract = 0
    for r in recs.values():
        by_kind[r.get("kind", "?")] = by_kind.get(r.get("kind", "?"), 0) + 1
        lic = r.get("licence", "unknown")
        by_licence[lic] = by_licence.get(lic, 0) + 1
        if lic in ("unknown", "unspecified", "none"):
            no_licence += 1
        if not r.get("extracts"):
            no_extract += 1
    return {
        "count": len(recs),
        "byKind": by_kind,
        "byLicence": by_licence,
        "missingLicence": no_licence,
        "withoutExtract": no_extract,
        "path": store_dir(),
        "note": (
            "missing licence and missing extract are both findings, not blanks: the first decides "
            "whether the work can be built on, the second decides whether the claim can be checked."
        ),
    }
