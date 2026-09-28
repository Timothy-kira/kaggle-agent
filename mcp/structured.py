"""Read a structured tool argument that may arrive as JSON text.

Why this module exists
----------------------
A tool call made through the desktop host's own tool layer loses the *contents* of an
object-typed argument: the plugin receives `{}`, so `node` arrives with an empty id and the
whole experiment-tree workflow - declare, record, settle - is unreachable. Every scalar
argument in the same call arrives intact, and the identical payload sent over plain MCP
stdio reaches this plugin whole, so the loss is in the transport, not here.

That rules out "the contract is wrong". A contract the host cannot carry is still a broken
path to the same experiment, so the data has to travel over a channel that does survive:
a STRING of JSON. Which means every structured argument is published as a string and parsed
on arrival, while a real object is still accepted so that an ordinary MCP client - a script,
a test, another host - can keep sending one.

The cost is that a caller must quote JSON correctly, so the error messages here are written
for a model that got the quoting wrong: they name the field, say what shape was expected,
and show the example. A vague "invalid argument" is what makes this unusable in practice.

What this is NOT
----------------
Not a repair of mangled data. `normalize_node` in :mod:`experiment_tree` repairs a node whose
lists collapsed or whose parent was stringified on the way in. This module is the front door:
it decides whether the argument ever arrived in a form worth repairing.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

__all__ = ["StructuredError", "structured", "argv", "scrub", "MAX_ARG_CHARS"]

# Long enough for any node this plugin writes, short enough that a call carrying it is still
# an ordinary-sized message. Beyond this the payload is a mistake worth naming, not a node.
MAX_JSON_CHARS = 20000

# A single argument larger than this. The transport fails a very long argument BEFORE this
# process can answer - the client sees a dead connection and no error at all - so a cap here
# cannot prevent that, only make the supported ceiling explicit and give long data a home
# (a file, and a path).
MAX_ARG_CHARS = 20000

# Anything outside the BMP - an emoji, and several CJK extension blocks - is two UTF-16 code
# units. A transport that slices a string between them can leave two outcomes, and they are
# not the same: a PAIR that is merely split apart is repairable, and a lone HALF means the
# character is already gone and cannot be recovered by any amount of guessing.
_HIGH = re.compile("[\ud800-\udbff]")
_LOW = re.compile("[\udc00-\udfff]")
_ANY_SURROGATE = re.compile("[\ud800-\udfff]")
_LONE = re.compile("(?:[\ud800-\udbff](?![\udc00-\udfff]))|(?:(?<![\ud800-\udbff])[\udc00-\udfff])")


class StructuredError(ValueError):
    """A structured argument could not be read. The message is the tool's user guidance."""


def _shape(want: type) -> str:
    return "a JSON object like {...}" if want is dict else "a JSON array like [...]"


def structured(value: Any, field: str, want: type = dict,
               example: str = "") -> Optional[Any]:
    """Return ``value`` as real data, accepting JSON text or the value itself.

    ``want`` is :class:`dict` or :class:`list``. ``None`` and the empty string mean "not
    supplied" and return ``None``; a supplied argument that cannot be read raises
    :class:`StructuredError` naming the field and showing ``example``.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, want):
        return value
    if isinstance(value, (dict, list)):
        # The wrong shape, but real data: say which one instead of pretending it is JSON text.
        raise StructuredError(
            f"{field} must be {_shape(want)}, not a JSON "
            f"{'string' if isinstance(value, str) else 'array'}. {example}".strip()
        )
    if not isinstance(value, str):
        raise StructuredError(
            f"{field} must be a JSON string in one piece, not "
            f"{type(value).__name__}. {example}".strip()
        )
    text = value.strip()
    if len(text) > MAX_JSON_CHARS:
        raise StructuredError(
            f"{field} is {len(text)} characters; the limit is {MAX_JSON_CHARS}. "
            f"Send the node's fields directly rather than a document. {example}".strip()
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredError(
            f"{field} is not valid JSON ({exc.msg} at position {exc.pos}). It has to be one "
            f"string holding the whole value - single quotes and bare keys are not JSON. "
            f"{example}".strip()
        ) from None
    if not isinstance(parsed, want):
        raise StructuredError(
            f"{field} parsed as a JSON {type(parsed).__name__}, but {_shape(want)} was "
            f"expected. {example}".strip()
        )
    return parsed


def argv(value: Any, field: str = "command") -> Optional[list]:
    """Read a command line, accepting a JSON array, a real list, or plain text.

    Plain text is split on whitespace, which is what a caller writing
    ``python train.py --epochs 3`` means. It is a convenience, not a shell: no quoting, no
    redirection, no pipes. A real argument that contains spaces must be sent as an array so
    the boundary between one token and the next stays where the caller put it.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, list):
        return [str(c) for c in value]
    if not isinstance(value, str):
        raise StructuredError(f"{field} must be a list of argv tokens.")
    text = value.strip()
    if text.startswith("["):
        parsed = structured(text, field, list, 'e.g. ["python","train.py","--epochs","3"]')
        return [str(c) for c in parsed]
    return text.split()


def _join_split_surrogates(text: str) -> str:
    """Re-join a surrogate pair the transport split but did not lose."""
    if not _ANY_SURROGATE.search(text):
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if _HIGH.match(ch) and i + 1 < n and _LOW.match(text[i + 1]):
            out.append(chr(0x10000 + ((ord(ch) - 0xD800) << 10) + (ord(text[i + 1]) - 0xDC00)))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _first_lone(text: str) -> str:
    m = _LONE.search(text)
    return m.group(0) if m else ""


def scrub(value: Any, field: str = "arguments") -> Any:
    """Check every string on the way in: repair what is repairable, refuse what is not.

    A tool argument travels through a transport that can corrupt it two ways, and both
    failures look the same from the outside unless something checks on arrival:

    - a character outside the BMP is two code units, and a split that keeps both can be
      rejoined here. Doing it is not cosmetic: the alternative is an un-encodable string that
      raises deep inside a file write, long after the caller was told the call succeeded.
    - a split that keeps only one half has already destroyed the character. No repair is
      possible, and a replacement char or a "?" would write damage into a file that exists to
      be a record. So the call is refused, the field is named, and the guidance is ASCII.

    ``scrub`` also caps a single string at :data:`MAX_ARG_CHARS`, naming the field and
    pointing at a file, because a payload that the transport drops on the way in never
    reaches this message at all.
    """
    if isinstance(value, str):
        if len(value) > MAX_ARG_CHARS:
            raise StructuredError(
                f"{field} is {len(value)} characters; the supported maximum is "
                f"{MAX_ARG_CHARS}. An argument this large is dropped by the transport before "
                f"it arrives, and a dropped argument fails silently. Put the content in a "
                f"file and pass its path instead."
            )
        joined = _join_split_surrogates(value)
        lone = _first_lone(joined)
        if lone:
            raise StructuredError(
                f"{field} contains a half of a character ({lone!r}): the transport split a "
                f"non-ASCII character and dropped the rest, so it cannot be recovered. "
                f"Send this field in ASCII, or put the non-ASCII text in a file and pass the "
                f"path. Everything else about the call is fine."
            )
        return joined
    if isinstance(value, dict):
        return {k: scrub(v, f"{field}.{k}" if field else str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, f"{field}[{i}]") for i, v in enumerate(value)]
    return value
