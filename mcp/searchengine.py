"""Which search engine the browser uses — and when that is a question worth asking.

Why the engine is a decision and not a constant
----------------------------------------------
Discovery search and direct navigation are different acts, and conflating them is how a
workflow ends up asking a question that has no answer.

**Discovery search** is when the target is not yet known: "who is working on ARC-AGI-3 and
what did they publish". You need a search engine, and different engines genuinely differ —
Bing's result mix and Google's are not the same page, region routing changes the language, and
one may render where the other does not. That is a real choice, so it is asked.

**Direct navigation** is when the URL is already known. Opening ``github.com/topics/arc-prize``
or ``arxiv.org/list/cs.AI/recent`` is not a search; there is no engine to choose, and asking
"which engine?" before opening a URL you already have would be noise pretending to be a
question. The three coverage sources are reached this way, so they never ask.

This module therefore records the engine for *discovery* search, and the skill is explicit that
direct navigation is exempt. Getting that wrong in either direction is bad: ask every time and
the workflow interrogates the user before opening a URL it already holds; never ask and the
discovery engine is picked silently by whoever ran last.

Measured behaviour, not assumed behaviour
-----------------------------------------
Both engines were driven through the real in-app browser on this machine, and the details that
matter were measured rather than assumed:

- **Bing** renders quickly and its results parse from ``#b_results``. **Its language follows the
  machine's region** — one query came back with a Japanese title and Japanese snippets, so
  ``&setlang=en&cc=us`` is appended to pin the language instead of inheriting the host locale.

- **Google** redirects to a regional host (``www.google.com.hk`` from this machine) and **renders
  more slowly than the navigation call reports**. Immediately after ``open_tab`` returns, the
  viewport can still be blank while the DOM is already populated. It is not broken, and it is not
  an empty result — it just needs a moment. Both were confirmed on the same query: blank at first
  frame, fully rendered a few seconds later, with the text query returning results in between.

The practical rule that falls out of this: **judge a search by its text, never by whether the
page looks painted, and give a slow-rendering engine a `wait` before believing an empty screen.**
Screenshotting once and concluding "Google does not work here" is exactly the mistake this note
exists to prevent.

That is also why the engine choice is worth surfacing. The two engines index differently and
render at different speeds, so which one you picked changes both what you find and how you have
to read it.

Storage: ``<home>/search-engine.json``, beside the other per-user state. ``<home>`` is
``KAGGLE_AGENT_HOME`` when set, else ``~/.kaggle-agent``. Configuration only — no credential,
no token, no search history.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote_plus

SCHEMA_VERSION = 1
CONFIG_FILENAME = "search-engine.json"

# Google is the default. It was chosen deliberately, not as a fallback: it was measured
# working through the real browser on this machine, and it is the engine to use when nobody is
# available to be asked. Bing remains available and is better when you want the page to paint
# fast, or when you need a pinned `setlang`/`cc` on the query URL rather than Google's regional
# redirect.
DEFAULT_ENGINE = "google"

ENGINES = {
    "bing": {
        "label": "Bing",
        "note": "renders visibly; language follows the host region unless setlang is pinned",
        "search_url": "https://www.bing.com/search?q={q}&setlang=en&cc=us&count=30",
        # The container Bing's organic results live in. Parsing the whole body also works but
        # drags in the sidebar, the answer box and the ad slots.
        "results_selector": "#b_results",
    },
    "google": {
        "label": "Google",
        "note": "redirects to a regional host and renders slowly - wait before judging the screen",
        "search_url": "https://www.google.com/search?q={q}&hl=en&num=20",
        "results_selector": "#search, #r, body",
    },
}


def _home() -> str:
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent"
    )


def config_path() -> str:
    return os.path.join(_home(), CONFIG_FILENAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def defaults() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "engine": DEFAULT_ENGINE,
        "language": "en",
        "changedAt": None,
        "source": "default (never chosen explicitly)",
    }


def load() -> dict[str, Any]:
    """Read the engine choice. Never cached, so a change lands on the next search.

    A missing or malformed file yields the default rather than an error: an unconfigured
    discovery search should still run, on Bing, rather than refusing to start.
    """
    data = defaults()
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return data
    if not isinstance(stored, dict):
        return data
    if stored.get("engine") in ENGINES:
        data["engine"] = stored["engine"]
    if isinstance(stored.get("language"), str) and stored["language"]:
        data["language"] = stored["language"]
    for key in ("changedAt", "source"):
        if isinstance(stored.get(key), str):
            data[key] = stored[key]
    return data


def _write(data: dict[str, Any]) -> str:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def set_engine(engine: str, language: Optional[str] = None,
               source: str = "user") -> dict[str, Any]:
    key = (engine or "").strip().lower()
    if key not in ENGINES:
        raise ValueError(
            f"engine must be one of {', '.join(sorted(ENGINES))}, got {engine!r}"
        )
    data = load()
    before = data["engine"]
    data["engine"] = key
    data["source"] = source
    data["changedAt"] = _now()
    if language:
        data["language"] = language.strip() or "en"
    _write(data)
    return {
        "ok": True,
        "previousEngine": before,
        "engine": key,
        "label": ENGINES[key]["label"],
        "changed": key != before,
        "updatedAt": data["changedAt"],
        "path": config_path(),
    }


def search_url(query: str, engine: Optional[str] = None) -> dict[str, Any]:
    """Build the discovery-search URL for a query. Read-only; changes nothing.

    Returning the URL rather than performing the fetch is deliberate: the browser lives in the
    host, not in this process, so this module's job is to hand the main agent a URL that is
    known to work, not to pretend it can browse.
    """
    text = re.sub(r"\s+", " ", (query or "").strip())
    if not text:
        raise ValueError("query must not be empty")

    data = load()
    key = (engine or data["engine"] or DEFAULT_ENGINE).strip().lower()
    if key not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; known: {', '.join(sorted(ENGINES))}")

    spec = ENGINES[key]
    lang = data.get("language") or "en"
    url = spec["search_url"].format(q=quote_plus(text)).replace("&setlang=en", f"&setlang={lang}")
    if key == "google":
        url = url.replace("&hl=en", f"&hl={lang}")

    return {
        "ok": True,
        "engine": key,
        "label": spec["label"],
        "query": text,
        "url": url,
        "resultsSelector": spec["results_selector"],
        "note": spec["note"],
        "readingRule": (
            "read with query(kind=\"text\", selector=<resultsSelector>). If a screenshot looks "
            "empty, the page may still be rendering: wait, then re-read. Never conclude a search "
            "returned nothing from a blank frame."
        ),
    }


def describe() -> dict[str, Any]:
    data = load()
    return {
        "engine": data["engine"],
        "label": ENGINES[data["engine"]]["label"],
        "note": ENGINES[data["engine"]]["note"],
        "language": data.get("language"),
        "source": data.get("source"),
        "changedAt": data.get("changedAt"),
        "path": config_path(),
        "exists": os.path.isfile(config_path()),
        "available": sorted(ENGINES),
    }
