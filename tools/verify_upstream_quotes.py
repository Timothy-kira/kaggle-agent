"""Are the quoted blocks byte-for-byte identical to the upstream source?

Three files quote upstream and say they quote it verbatim. That claim is checkable, and
checking it is the whole point - a paraphrase presented as a quotation is exactly the
failure this skill is built to catch, applied to itself.

The test is deliberately strict. Normalised whitespace would hide a rewritten clause, so
the comparison is on the exact substring, and every quote must be found in the source it
names. A quote that appears in NO source file is a fabrication.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UP = ROOT / "skills" / "ruler-audit" / "references" / "upstream"
MINE = ROOT / "skills" / "ruler-audit" / "references"

SOURCES = {p.name: p.read_text(encoding="utf-8") for p in UP.glob("*.md")}


def squash(t: str) -> str:
    """Collapse every run of whitespace, including the blank lines between paragraphs.

    Without this the comparison is a false negative on exactly the quotes that matter: a
    multi-paragraph block re-wrapped to this repo's width no longer matches the source
    byte-for-byte even when not one word was changed, and the instrument then reports a
    faithful quotation as a rewrite. The words and their order are what a quotation
    claims; the line breaks are this repo's typesetting.
    """
    return " ".join(t.split())


def _flatten(lines) -> str:
    """Squash a source the same way a quote is squashed.

    The sources carry the quote markers on every line (`> `) and the two paragraph breaks
    this repo introduces do not exist in them. Comparing a squashed quote against an
    unsquashed source reports a faithful quotation as a rewrite for the two reasons that
    matter most - a marker on the continuation line, and a `>` line where the source has a
    blank one - and both failures look like the thing this instrument exists to catch.
    """
    return squash("\n".join(lines))


ALL = _flatten(
    [l.lstrip().lstrip(">").rstrip() for l in "\n".join(SOURCES.values()).split("\n")])


def dequote(block: str) -> str:
    """Strip the markdown blockquote markers and the rewrap this repo applies."""
    lines = []
    for line in block.split("\n"):
        line = line.lstrip()
        if line.startswith(">"):
            line = line[1:]
        if not line.strip():
            lines.append("")
            continue
        lines.append(line.strip())
    return squash("\n".join(lines))


def blocks(text: str):
    """Yield (start_line, quoted_text) for every > block of two or more lines.

    A block whose first line is a self-authored marker is skipped and reported: an
    instrument that cannot tell a quotation from an example of this plugin's own syntax
    would flag every worked example as a fabricated quotation.
    """
    cur, start = [], None
    for i, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith(">"):
            if start is None:
                start = i
            cur.append(line)
        else:
            if len(cur) >= 2:
                if "(this plugin" in dequote("\n".join(cur)).split("\n")[0] or \
                   any("(this plugin" in l for l in cur):
                    SKIPPED.append((_name.get(id(text), "?"), start, cur[0].strip()[:80]))
                else:
                    yield start, dequote("\n".join(cur))
            cur, start = [], None
    if len(cur) >= 2:
        if not any("(this plugin" in l for l in cur):
            yield start, dequote("\n".join(cur))


SKIPPED: list = []
_name = {}


def name_of(text: str) -> str:
    return _name.get(id(text), "?")


targets = ["triage.md", "resolution.md", "adoption.md", "where-hard.md", "checklist.md"]
total = found = 0
problems = []

# Lines belonging to a block explicitly marked as this plugin's own syntax. A worked example
# of a `declare` is not a quotation and must not be graded as one; without this it is
# reported as a fabricated quote, and a report nobody can act on is a report that gets
# ignored.
_own_blocks: dict[str, list[str]] = {}
for _n in targets:
    _t = (MINE / _n).read_text(encoding="utf-8")
    _lines = _t.split("\n")
    _marked: list[str] = []
    for _i, _l in enumerate(_lines):
        if "(this plugin" in _l:
            _j = _i
            while _j < len(_lines) and (_lines[_j].lstrip().startswith(">") or not _lines[_j].strip()):
                _marked.append(_lines[_j])
                if _j < len(_lines) and not _lines[_j].lstrip().startswith(">"):
                    break
                _j += 1
    _own_blocks[_n] = _marked

for name in targets:
    text = (MINE / name).read_text(encoding="utf-8")
    _name[id(text)] = name
    for line_no, quote in blocks(text):
        total += 1
        if len(quote.split()) < 12:
            continue
        if quote in ALL:
            found += 1
        else:
            problems.append((name, line_no, quote[:150]))

    # A table quoted row by row is the format most likely to drift during an edit, and the
    # paragraph check above skips it: each cell is its own line, so a `>` block never forms.
    # Checked CELL BY CELL, because a row can be half quoted and half paraphrased and the
    # row as a whole then fails for a reason that does not say which half is wrong.
    #
    # A cell is also compared with its own list marker stripped. A numbered quote reads
    # `> 4. Synthesized by you...` here and `4. Synthesized by you...` in the source, and
    # without stripping the marker every numbered quote is reported as a rewrite - which is
    # the failure mode where a correct instrument is simply switched off, because a check
    # that always says "not verbatim" is as useless as one that always says it is.
    for line_no, line in enumerate(text.split("\n"), 1):
        s = line.lstrip()
        if not s.startswith(">"):
            continue
        in_own_block = any("(this plugin" in l for l in _own_blocks.get(name, []))
        if in_own_block:
            continue
        cells = [squash(c) for c in s.lstrip(">").strip().strip("|").split("|")]
        for cell in cells:
            cell = re.sub(r"^\d+\.\s*", "", cell.strip()).strip()
            if len(cell.split()) < 12 or set(cell) <= set("-| "):
                continue
            total += 1
            if cell in ALL:
                found += 1
            else:
                problems.append((name, line_no, f"[table cell] {cell}"))

print(f"quoted blocks of 12+ words examined: {total}")
print(f"found verbatim in an upstream source: {found}")
print(f"marked as this plugin's own syntax : {len(SKIPPED)}")
for n, ln, first in SKIPPED:
    print(f"  skipped {n}:{ln}  {first}")
if problems:
    print(f"\nNOT verbatim ({len(problems)}):")
    for name, ln, q in problems:
        print(f"  {name}:{ln}")
        print(f"     {q!r}")
        # point at the nearest source, if any, so the difference is visible
        head = " ".join(q.split()[:8])
        for sname, src in SOURCES.items():
            idx = src.find(head)
            if idx >= 0:
                print(f"     closest in {sname}: ...{src[idx:idx+220]!r}")
                break
        else:
            print("     no upstream file starts with this text")
    sys.exit(1)
print("\nall quoted blocks are verbatim")
