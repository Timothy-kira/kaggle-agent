"""The index of published scientific-agent skills, and the three ways it is read.

There is no index in this package. That is deliberate. A bundled index is a snapshot of someone
else's repository taken on one day, it grows stale the moment upstream renames a skill, and -
worst of all - a stale answer to "is there published method for this?" is worse than no answer,
because it reads as a search that found nothing.

So the index is scraped from where it is actually written down. Upstream keeps
``docs/skills.md``: one file, one request, every skill with its name, its path, and a summary
that mirrors its own description. Reading that page is both cheaper and more current than
fetching 166 skill files and trusting the join.

Whatever a scrape produces is cached under ``~/.kaggle-agent/skill-cache/<sha>/`` keyed by the
commit it came from, so the second run costs nothing and a new upstream version lands beside the
old one instead of over it.

Three reads, and which one you call decides whether the network is touched at all:

* :func:`recommend` - local only. Reads the cache and never opens a socket. This is the one the
  experiment loop uses, because a network call there either fails the loop or taxes every node.
* :func:`probe` - one small authenticated call that says whether the cache is current.
* :func:`fetch_index` - scrape the page and write the cache.

Failures are reported as what was observed - the HTTP status, the transport's own error text,
and the offline capability probe - and not as a conclusion. Which of those is actionable, and
what to ask the user about it, is the caller's judgement: a taxonomy written here would be wrong
the moment upstream produced a failure mode this file had never seen.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
from typing import Any, Optional

# How much of a skill's description has to overlap the query before it is offered, and how many
# distinct words must actually be shared.
#
# The floor exists because the tree's own overlap divides by the SMALLER set: three query words
# that all appear in a long description score 1.0, and a large library hands out perfect scores
# to almost anything. Three shared words is the smallest overlap that is evidence rather than
# coincidence, and it is checked against the raw intersection rather than the ratio so a long
# description cannot buy a high score with two lucky words.
SKILL_SCORE_MIN = 0.30
SKILL_SHARED_MIN = 3

REPO = "K-Dense-AI/scientific-agent-skills"
# The page the index is read from. One request, and it is the page upstream maintains for
# exactly this purpose - it is a catalogue, not a place descriptions happen to be mentioned.
INDEX_PAGE = "docs/skills.md"
CACHE_DIRNAME = "skill-cache"

_ENTRY = re.compile(
    r"^-\s*\*\*\[(?P<label>[^\]]*)\]\((?P<href>[^)]+)\)\*\*\s*[-\u2013]?\s*(?P<text>.*)$")


# --------------------------------------------------------------------------- where things live


def _home() -> str:
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent")


def _package_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cache_root() -> str:
    return os.path.join(_home(), CACHE_DIRNAME)


def cache_dir(commit: str) -> str:
    """Cache is keyed by commit so a new upstream version lands beside the old one.

    Keyed by anything else - a name, a "latest" file, a single directory overwritten in place -
    and a refresh silently mixes two versions of the world, which is the one state a pinned
    upstream exists to prevent.
    """
    sha = (commit or "unknown").strip()
    return os.path.join(cache_root(), re.sub(r"[^0-9a-fA-F]", "", sha)[:40] or "unknown")


def index_path(commit: str) -> str:
    return os.path.join(cache_dir(commit), "index.json")


# --------------------------------------------------------------------------- reading, locally


def _read_index(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("skills"), list):
        return None
    return doc


def _cache_commits() -> list[str]:
    root = cache_root()
    if not os.path.isdir(root):
        return []
    return sorted((e for e in os.listdir(root)
                   if os.path.isdir(os.path.join(root, e))), reverse=True)


def local_index() -> dict[str, Any]:
    """The freshest index on disk. Never touches the network.

    Empty is a real answer, not a fallback to be papered over: it means the page has not been
    read yet on this machine, and saying so is what lets the caller go and read it. Inventing a
    bundled copy to avoid that sentence is the failure this module exists to not have.
    """
    for sha in _cache_commits():
        doc = _read_index(os.path.join(cache_root(), sha, "index.json"))
        if doc and doc.get("skills"):
            doc = dict(doc)
            doc["source"] = "cache"
            return doc
    return {"skills": [], "source": "none", "count": 0,
            "note": "no index on this machine yet; read the upstream page first"}


# --------------------------------------------------------------------------- matching

_SUFFIXES = ("ingly", "edly", "ings", "ing", "ions", "ion", "ies", "ied", "es", "ed",
             "er", "ers", "able", "ible", "al", "ly", "s")

# Words that appear in most of the index and mean nothing about which skill is meant. Without
# this, "use a bigger batch size on the gpu" reaches experimental-design on the strength of
# "use", "the" and "size" - a real false friend, because that skill really does discuss batch
# effects, which is exactly why the surrounding filler has to go before the real words are
# counted. Kept separate from the tree's own tokeniser on purpose: the tree matches nodes it
# wrote, this one matches prose written by strangers.
_STOPWORDS = frozenset("""
the and for with from that this these those use using used uses when what which whose while
will would can could should shall may might must not no nor but or if then than there here
their they them its it he she his her you your we our us been being have has had do does did
also into about over under more most some any all each both other such only own same very
one two three four five six seven eight nine ten first second next last new old good best
""".split())


def _stem(word: str) -> str:
    if len(word) <= 4:
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            base = word[: -len(suf)]
            if suf == "s" and base.endswith(("s", "u", "i", "a", "e")):
                continue
            return base
    return word


def tokens(*values: Any) -> set[str]:
    """The word set a query and a description are compared on.

    Same rule as the tree matches nodes on - lowercase, split on non-alphanumerics, drop
    anything of two characters or fewer - plus the stem and the stopword list above, so a
    description written in one form of a word and a query written in another still meet, and so
    the filler that every English sentence carries does not count as overlap.
    """
    words: set[str] = set()
    for v in values:
        for tok in re.findall(r"[a-z0-9]+", str(v or "").lower()):
            if len(tok) > 2 and tok not in _STOPWORDS:
                words.add(_stem(tok))
    return words


# Words that appear in most of the index are worth almost nothing when deciding whether one
# description is about what the query is about. "before" opens one skill and "test" runs through
# many; "rival" and "explanations" pick out exactly one.
#
# So the score is the share of the query's WEIGHT that the description covers, where a word's
# weight grows as it gets rarer in the index. Counting words equally is the same mistake in the
# opposite direction from the floor above: a query is a whole sentence - "generate competing
# rival explanations before choosing a test" - and the signal is two words inside it.
_DF_CACHE: dict[int, dict[str, int]] = {}
_DF_N: int = 0


def _document_frequency(index: dict[str, Any]) -> dict[str, int]:
    """How many descriptions contain each word. Cached per index identity.

    Built once per process because it reads every description in the index, and
    :func:`recommend` runs inside the experiment loop.
    """
    global _DF_N
    key = id(index)
    cached = _DF_CACHE.get(key)
    if cached is not None:
        return cached
    df: dict[str, int] = {}
    n = 0
    for entry in index.get("skills") or []:
        if not isinstance(entry, dict):
            continue
        n += 1
        for w in tokens(entry.get("description"), entry.get("name")):
            df[w] = df.get(w, 0) + 1
    _DF_CACHE[key] = df
    _DF_N = n
    return df


def _weight(word: str, df: dict[str, int], n: int) -> float:
    # log(N / df): a word in every description scores 0, a word in one scores log(N).
    if n <= 0:
        return 0.0
    return math.log(n / float(max(1, df.get(word, 0))))


def match(index: dict[str, Any], query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Rank the index against one query. Pure: no IO, no network, no clock."""
    return rank(index, query, limit)["hits"]


def rank(index: dict[str, Any], query: str, limit: int = 5) -> dict[str, Any]:
    """Rank the index, and say so when nothing cleared the bar.

    Returns the matching text rather than a summary of it, because the caller's next decision -
    read it, or ignore it - needs the words that matched and not a paraphrase. The words that
    carried the match travel with the result for the same reason.

    When the hit list is empty the closest entries come back anyway, under ``closest``, marked
    as below the bar. Matching is lexical and a query is written in somebody's words while a
    description is written in the author's: "without adding facts I did not measure" shares
    exactly two words with the skill about evidence provenance, and no threshold gets that
    right. An empty list says "nothing found" and reads as certainty; a shortlist marked
    "nothing found, these were nearest" says the true thing and lets the caller judge.
    """
    proposed = tokens(query)
    empty = {"hits": [], "closest": [], "note": None, "indexSize": 0,
             "queryWords": sorted(proposed)}
    if not proposed:
        empty["note"] = "the query had no usable words in it"
        return empty

    df = _document_frequency(index)
    n = _DF_N or 1
    query_weight = sum(_weight(w, df, n) for w in proposed)
    if query_weight <= 0:
        empty["note"] = "every word in the query appears throughout the index, so none of them points anywhere"
        return empty

    scored: list[dict[str, Any]] = []
    for entry in index.get("skills") or []:
        if not isinstance(entry, dict):
            continue
        hay = tokens(entry.get("description"), entry.get("name"))
        inter = proposed & hay
        if not inter:
            continue
        score = sum(_weight(w, df, n) for w in inter) / query_weight
        scored.append({
            "name": entry.get("name"), "path": entry.get("path"),
            "score": round(score, 3), "shared": len(inter),
            "matchedOn": sorted(inter, key=lambda w: -_weight(w, df, n))[:6],
            "description": entry.get("description") or "",
        })
    scored.sort(key=lambda m: -m["score"])

    hits = [m for m in scored[:limit] if m["shared"] >= SKILL_SHARED_MIN
            and m["score"] >= SKILL_SCORE_MIN]
    out = {"hits": hits, "closest": [], "note": None,
           "indexSize": len(index.get("skills") or []), "queryWords": sorted(proposed)}
    if not hits and scored:
        out["closest"] = scored[:3]
        top = scored[0]
        out["note"] = ("nothing cleared the bar; these were nearest, below it "
                       "(best shared %d of %d words at score %.2f, bar is %d at %.2f)"
                       % (top["shared"], len(proposed), top["score"],
                          SKILL_SHARED_MIN, SKILL_SCORE_MIN))
    elif not hits:
        out["note"] = "nothing in the index shares a word with this query"
    return out


def recommend(*query: str, limit: int = 5) -> dict[str, Any]:
    """Local-only recommendation. Never opens a socket - see the module docstring."""
    index = local_index()
    r = rank(index, " ".join(q for q in query if q), limit=limit)
    return {
        "ok": True,
        "network": "not used",
        "source": index.get("source"),
        "upstreamCommit": index.get("upstreamCommit"),
        "indexSize": index.get("count", len(index.get("skills") or [])),
        "indexMayBeStale": index.get("source") != "cache",
        "note": index.get("note"),
        "matches": r["hits"],
        "closest": r["closest"],
        "whyNoMatch": r["note"],
        "queryWords": r["queryWords"],
    }


# --------------------------------------------------------------------------- the transport


def _load_github_sync():
    """The plugin's own transport, loaded by path.

    Imported rather than duplicated so that the token precedence, the transport fallback and
    the capability probe are the same code here as in handoff sync. A second implementation
    would drift within a release, and the drift would show up as "the plugin can authenticate
    but the index cannot".
    """
    sys_path = os.path.join(_package_root(), "mcp")
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    spec = importlib.util.spec_from_file_location(
        "ks_kdense_gh", os.path.join(sys_path, "github_sync.py"))
    gh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gh)
    return gh


def _report(outcome: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Wrap a network outcome with the local capability probe and the raw observation.

    The probe is local and offline, which is the point: when the network is the thing that
    broke, a diagnostic that needs the network cannot help. Nothing here decides what the
    failure means; it reports what was seen and lets the caller work out whether it is the
    user's configuration, the pin, or a rate limit.
    """
    out = dict(detail)
    out["outcome"] = outcome
    out["status"] = detail.get("httpStatus")
    out["transportError"] = detail.get("transportError")
    if outcome != "ok":
        out["capabilities"] = _load_github_sync().capabilities()
    return out


# --------------------------------------------------------------------------- scraping the page


def parse_index_page(text: str) -> list[dict[str, str]]:
    """Pull ``name, path, description`` out of the catalogue page.

    The page is a bulleted list, one entry per skill:

        - **[DepMap](../skills/depmap/SKILL.md)** - Query the Cancer Dependency Map ...

    The link is the authority for the name and the path; the visible label is a display title
    and is not always the same string ("U.S. Treasury Fiscal Data" for ``usfiscaldata``), so
    matching on the label would file the skill under a name its directory does not have.
    """
    out: list[dict[str, str]] = []
    for line in (text or "").splitlines():
        m = _ENTRY.match(line.strip())
        if not m:
            continue
        href = m.group("href").split("#", 1)[0]
        want = "/skills/"
        if want not in href or not href.endswith("SKILL.md"):
            continue
        rel = href.split(want, 1)[1][: -len("/SKILL.md")]
        if not rel or "/" in rel:
            continue                       # nested or section links, not a skill entry
        out.append({
            "name": rel,
            "path": "skills/%s/SKILL.md" % rel,
            "label": m.group("label").strip(),
            "description": re.sub(r"\s+", " ", m.group("text") or "").strip(),
        })
    return out


def fetch_index(commit: str) -> dict[str, Any]:
    """Read the catalogue page at one commit and cache what it says.

    ``commit`` is the pin, never a branch. The page and the skills it points at have to come
    from the same revision, or the index describes a set of skills that no longer exist.
    """
    if not str(commit or "").strip():
        return _report("no_pin", {"detail": "no commit was named; a branch would move under the read"})
    gh = _load_github_sync()
    data, err, status = gh.api(
        "GET", "/repos/%s/contents/%s?ref=%s" % (REPO, INDEX_PAGE, commit))
    if status != 200 or not isinstance(data, dict):
        return _report("fetch_failed", {
            "httpStatus": status, "transportError": err or None,
            "page": INDEX_PAGE, "commit": commit,
        })
    import base64
    try:
        text = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
    except Exception as exc:                                   # noqa: BLE001
        return _report("fetch_failed", {
            "httpStatus": status, "page": INDEX_PAGE, "commit": commit,
            "transportError": "%s: %s" % (type(exc).__name__, exc),
        })

    skills = parse_index_page(text)
    if not skills:
        # An empty parse is a page that changed shape, not a repository with no skills. Saying
        # "none" here would write an empty index over a good one.
        return _report("parse_failed", {
            "httpStatus": status, "page": INDEX_PAGE, "commit": commit,
            "detail": "the page was read but no skill entries were found in it; "
                      "the layout it uses has probably changed",
        })

    doc = {
        "upstream": REPO,
        "upstreamCommit": commit,
        "indexPage": INDEX_PAGE,
        "scrapedFrom": "docs/skills.md",
        "count": len(skills),
        "skills": skills,
    }
    target = index_path(commit)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
    _DF_CACHE.clear()
    return _report("ok", {"httpStatus": status, "commit": commit, "page": INDEX_PAGE,
                          "count": len(skills), "cached": target})


def probe() -> dict[str, Any]:
    """One call that says whether the cache is current. Never raises.

    A 403 here means the request was refused or rate-limited, and a 404 means the repository
    is not there under that name. They are reported as the statuses they are: a refused request
    is not evidence that anything is missing, and turning it into "upstream is gone" would
    silently retire a working index with no trace.
    """
    gh = _load_github_sync()
    data, err, status = gh.api("GET", "/repos/%s/commits/main" % REPO)
    sha = data.get("sha") if isinstance(data, dict) else None
    if status != 200 or not sha:
        return _report("probe_failed", {
            "httpStatus": status, "transportError": err or None,
            "note": "the index was not re-read; treat what is on disk as possibly stale",
        })
    local = local_index()
    current = str(local.get("upstreamCommit") or "")
    return _report("ok", {
        "httpStatus": status, "upstreamCommit": sha,
        "indexCommit": current or None,
        "indexIsCurrent": bool(current) and current == sha,
        "indexMayBeStale": not (current and current == sha),
    })


def fetch_skill(commit: str, path: str) -> dict[str, Any]:
    """Download one file as it stood at one commit. The caller gates and stores it.

    ``contents/{path}?ref={commit}`` and not ``git/blobs/{commit}:{path}``: the tree endpoint
    takes a blob's own SHA, and a commit is not one, so the "obvious" spelling returns 404 for
    every path and looks exactly like a missing file. The contents endpoint takes a ref, which
    is what a commit actually is here.
    """
    gh = _load_github_sync()
    data, err, status = gh.api("GET", "/repos/%s/contents/%s?ref=%s" % (REPO, path, commit))
    if status != 200 or not isinstance(data, dict):
        return _report("fetch_failed", {
            "httpStatus": status, "transportError": err or None,
            "path": path, "commit": commit,
        })
    import base64
    try:
        text = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
    except Exception as exc:                                   # noqa: BLE001
        return _report("fetch_failed", {
            "httpStatus": status, "path": path, "commit": commit,
            "transportError": "%s: %s" % (type(exc).__name__, exc),
        })
    return {"ok": True, "outcome": "ok", "httpStatus": status, "path": path,
            "commit": commit, "text": text}


def cached_skill(commit: str, path: str) -> Optional[str]:
    """The cached copy of a file, or None. Local read only."""
    target = os.path.join(cache_dir(commit), *path.split("/"))
    try:
        with open(target, "r", encoding="utf-8") as fh:
            return fh.read()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None


def store_skill(commit: str, path: str, text: str) -> str:
    target = os.path.join(cache_dir(commit), *path.split("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return target
