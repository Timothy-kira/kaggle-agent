"""Draw the three architecture figures as SVG, from code, so they can be regenerated.

A generated-figure convention rather than pasted diagrams, for three reasons that have each
already cost something here: an image cannot be diffed or reviewed in a pull request, a
hand-placed arrow goes stale the moment a file is renamed, and the checks below cannot assert
about a picture. Generating them means the prose and the figure move together, and
`check_plugin` can require that the files exist and say what the text claims they say.

Usage:  python tools/draw_architecture.py [output_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent

# The audited Okabe-Ito subset already in mcp/plots.py, so a figure and a chart read as one
# system rather than two palettes. Every fill is paired with a text label, so hue is never the
# only channel carrying meaning.
BLUE = "#0072B2"
ORANGE = "#D55E00"
GREEN = "#009E73"
PINK = "#CC79A7"
INK = "#000000"
MUTED = "#5A5A5A"
PAPER = "#FFFFFF"
WASH = "#F2F5F7"
FONT = ("ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Figure:
    def __init__(self, width: int, height: int, title: str, subtitle: str = "") -> None:
        self.w, self.h = width, height
        self.parts: list[str] = []
        self.parts.append(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{esc(title)}">'
        )
        self.parts.append(f'<rect width="{width}" height="{height}" fill="{PAPER}"/>')
        self.text(40, 46, title, size=27, weight=650, fill=INK)
        if subtitle:
            self.text(40, 76, subtitle, size=15, fill=MUTED)

    # ---------------------------------------------------------------- primitives
    def text(self, x: float, y: float, body: str, *, size: int = 15, weight: int = 400,
             fill: str = INK, family: str = FONT, anchor: str = "start",
             opacity: float = 1.0) -> None:
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
            f'opacity="{opacity}">{esc(body)}</text>'
        )

    def box(self, x: float, y: float, w: float, h: float, *, fill: str = WASH,
            stroke: str = INK, width: float = 1.4, rx: float = 8, dash: str = "") -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{d}/>'
        )

    def arrow(self, x1: float, y1: float, x2: float, y2: float, *, color: str = MUTED,
              width: float = 1.6, head: float = 7.0) -> None:
        self.parts.append(
            f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="{head}" '
            f'markerHeight="{head}" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker></defs>'
        )
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" '
            f'stroke-width="{width}" marker-end="url(#ah)"/>'
        )

    def rule(self, x: float, y: float, w: float, *, color: str = "#D8DEE3") -> None:
        self.parts.append(
            f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + w:.1f}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="1"/>'
        )

    def save(self, path: Path) -> None:
        # The closing tag is appended here, not in __init__: everything drawn after construction
        # lands at the end of the list, so a "</svg>" written up front puts the whole figure
        # outside the document and the file renders as an XML error page.
        self.parts.append("</svg>")
        path.write_text("\n".join(self.parts) + "\n", encoding="utf-8")
        # Parse it back. "Wrote N kB" says nothing about whether a browser can render it, and
        # the first version of this file was exactly that: written, sized, and unrenderable.
        root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
        overflow = self._overflow(root)
        if overflow:
            raise SystemExit(f"{path.name}: drawn outside the canvas -> {overflow}")
        print(f"wrote {path.relative_to(ROOT)}  ({path.stat().st_size // 1024} kB, "
              f"parses as XML, nothing off-canvas)")

    def _overflow(self, root: ElementTree.Element) -> list[str]:
        """Anything drawn past the viewBox, which renders as content the reader cannot scroll to.

        A figure that runs off its own canvas looks fine in a wide window and silently loses its
        right-hand column in a narrow one, so this is measured rather than eyeballed.
        """
        found: list[str] = []
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "rect":
                x, y = float(element.get("x", 0)), float(element.get("y", 0))
                w, h = float(element.get("width", 0)), float(element.get("height", 0))
                right, bottom = x + w, y + h
            elif tag == "text":
                x, y = float(element.get("x", 0)), float(element.get("y", 0))
                # 0.56em per character is a deliberately generous average for a UI sans face;
                # a false alarm here is cheap, a missed overflow is not.
                w = len(element.text or "") * float(element.get("font-size", 12)) * 0.56
                right, bottom = x + w, y + float(element.get("font-size", 12))
                if element.get("text-anchor") == "middle":
                    right = x + w / 2
                elif element.get("text-anchor") == "end":
                    right = x
            elif tag == "line":
                x, y = float(element.get("x1", 0)), float(element.get("y1", 0))
                right = max(x, float(element.get("x2", 0)))
                bottom = max(y, float(element.get("y2", 0)))
            else:
                continue
            if right > self.w + 2 or bottom > self.h + 2:
                found.append(f"{tag} at ({x:.0f},{y:.0f}) reaches {right:.0f}x{bottom:.0f} "
                             f"on a {self.w}x{self.h} canvas")
        return found


def node(fig: Figure, x: float, y: float, w: float, title: str, body: Iterable[str],
         *, accent: str, tint: str = WASH) -> float:
    """Draw one step and return the height it took.

    The height follows the number of lines rather than being passed in. A fixed height is a
    figure that renders correctly until somebody edits a sentence, and then silently spills its
    last line over the arrow below it.
    """
    lines = list(body)
    h = 50 + len(lines) * 19 + 8
    fig.box(x, y, w, h, fill=tint, stroke=accent, width=1.6)
    fig.parts.append(
        f'<rect x="{x:.1f}" y="{y:.1f}" width="5" height="{h:.1f}" fill="{accent}"/>'
    )
    fig.text(x + 18, y + 27, title, size=16, weight=620, fill=INK)
    for i, line in enumerate(lines):
        fig.text(x + 18, y + 50 + i * 19, line, size=13.5, fill=MUTED)
    return h


# --------------------------------------------------------------------- figure 1
def launch_path(out: Path) -> None:
    x, w = 40, 620
    steps = [
        ("MiniMax Code host", [
            "Launches the MCP server over stdio, speaking JSON-RPC on stdout.",
            "servers.mcp.json: command \"python\", args [-B, -c, <inline bootstrap>]"],
         INK, WASH),
        ("Inline bootstrap  (in the manifest)", [
            "Finds the package two levels under any known plugin root, identifies it by the",
            "name in its own .minimax-plugin/plugin.json, then exec()s the entry with an",
            "explicit __name__ - runpy.run_path(run_name=\"__main__\") cannot resolve __main__",
            "under python -c, which is how the old one failed before a tool was registered."],
         BLUE, "#EEF5FA"),
        ("mcp/agent_server.py  -  self-locating entry", [
            "Two install shapes, one answer: a local plugin is <root>/kaggle-agent; a",
            "Marketplace plugin is cached under a content hash, further down. Neither is",
            "matched on a directory name, because a hash directory is not a name."],
         GREEN, "#EDF7F3"),
        ("mcp/kaggle_server.py  -  protocol and dispatch", [
            "29 tools over stdio JSON-RPC. Every Kaggle tool resolves credentials, then asks",
            "for the CLI command - a value cached per process, so it is probed once, not once",
            "per call, and dropped only when an install makes the old answer false."],
         ORANGE, "#FDF1EC"),
    ]
    heights = [50 + len(body) * 19 + 8 for _, body, _, _ in steps]
    height = 108 + sum(heights) + 34 * (len(steps) - 1) + 20
    fig = Figure(1120, height, "How a call reaches the tools",
                 "One stdio process, located at runtime: the host does not expand "
                 "${PLUGIN_ROOT}, and does not promise a working directory.")

    y = 108
    for (title, body, accent, tint), h in zip(steps, heights):
        node(fig, x, y, w, title, body, accent=accent, tint=tint)
        y += h + 34
        if y < height - 20:
            fig.arrow(x + w / 2, y - 30, x + w / 2, y - 6, color=accent)

    px = 700
    fig.text(px, 128, "Why any of it is checked", size=17, weight=650, fill=INK)
    fig.rule(px, 140, 380)
    notes = [
        "tools/check_plugin.py",
        "1537 assertions over the manifest, the skill",
        "graph and its rendered index, the tree",
        "mechanics, the evidence chain, plotting,",
        "the secrets scan, and a live drive of this",
        "server over the wire.",
        "",
        "tools/probe_marketplace_layout.py",
        "Starts this exact bootstrap against a",
        "Marketplace-shaped layout and asks the",
        "server to answer initialize. Five kinds of",
        "break are proven to turn it red.",
        "",
        "tools/probe_transport.py",
        "One process per case, and a case that gets",
        "no reply fails the suite rather than",
        "printing SERVER DIED and exiting 0.",
    ]
    for i, line in enumerate(notes):
        bold = line.endswith(".py")
        fig.text(px, 172 + i * 22, line, size=13.5,
                 weight=600 if bold else 400, family=MONO if bold else FONT,
                 fill=INK if bold else MUTED)
    fig.save(out / "architecture-launch-path.svg")


# --------------------------------------------------------------------- figure 2
def rsi_tree(out: Path) -> None:
    fig = Figure(1120, 760, "RSI for Science: the experiment tree",
                 "Recursive self-improvement for science: an experiment is a node, and the tree "
                 "is the memory.")

    fig.box(160, 196, 440, 330, fill="#F7FAFB", stroke=BLUE, width=1.8, rx=14)
    fig.text(180, 226, "experiment tree  -  a validated DAG", size=16, weight=640, fill=INK)

    # Every edge below is horizontal or vertical on purpose. An earlier version drew them with
    # one shared routine that assumed a parent above its child, so the sibling links came out as
    # diagonals straight through the node boxes.
    nodes = {
        "A": (190, 250, "drop 3 features", GREEN),
        "B": (320, 250, "lr 3e-4 -> 1e-4", GREEN),
        "C": (450, 250, "+ blend, - val", GREEN),
        "D": (190, 350, "0.812 -> 0.838", BLUE),
        "E": (320, 350, "0.838 -> 0.802", ORANGE),
        "G": (450, 350, "0.838 -> 0.811", ORANGE),
        "F": (190, 450, "0.802 -> 0.836", BLUE),
    }
    W, H = 105, 52
    for tag, (x, y, label, accent) in nodes.items():
        fig.box(x, y, W, H, fill=PAPER, stroke=accent, width=1.6, rx=7)
        fig.text(x + 12, y + 22, tag, size=15, weight=700, fill=accent)
        fig.text(x + 12, y + 40, label, size=11.5, fill=MUTED)

    def vlink(a: str, b: str, label: str = "", color: str = "#B9C3CA") -> None:
        ax, ay = nodes[a][0] + W / 2, nodes[a][1] + H
        bx, by = nodes[b][0] + W / 2, nodes[b][1]
        fig.arrow(ax, ay, bx, by, color=color, width=1.4, head=6)
        if label:
            fig.text(ax + 9, (ay + by) / 2, label, size=11, fill=color)

    vlink("A", "D", "kept")
    vlink("B", "E", "revert", ORANGE)
    vlink("C", "G", "revert", ORANGE)
    vlink("D", "F", "kept")

    fig.text(180, 546, "blue  = kept      orange  = reverted, and the revert has to name the layer",
             size=12, fill=MUTED)
    fig.box(180, 566, 380, 56, fill="#F2F8F5", stroke=GREEN, width=1.3, rx=7, dash="4 3")
    fig.text(194, 590, "one variable per node", size=12.5, weight=600, fill=GREEN)
    fig.text(194, 610, "a node changes one thing, so its score means something", size=11.5,
             fill=MUTED)

    rules = [
        ("Read-gated", "read() hands back a revision and record() refuses a stale or absent one. "
                       "Prose saying \"check the state first\" can be skipped; a number cannot."),
        ("Failure layer", "reverting a node has to name where it failed, so \"that did not help\" "
                          "becomes a claim somebody can check."),
        ("Replay", "archived rounds are a simulator: your own history grades a candidate policy, "
                   "and compare always includes the running one."),
        ("Non-greedy selection", "score plus progress plus novelty, with a visit cooldown, so a "
                                 "locally good branch cannot starve the search."),
        ("Per-criterion cost", "four cost flags are kept apart rather than flattened, which is what "
                               "makes a gain that spends another budget visible."),
        ("Forced provenance", "a node with no source has to say evidence: \"local-only\" out loud."),
        ("Mechanical audit", "the audit is arithmetic. It drives and never acquits: a figure on disk "
                             "proves evidence exists, never that it supports the sentence."),
    ]
    x0 = 650
    fig.text(x0, 176, "What makes it more than a log", size=17, weight=650, fill=INK)
    fig.rule(x0, 188, 430)
    for i, (head, body) in enumerate(rules):
        y = 220 + i * 72
        fig.box(x0, y - 16, 430, 62, fill=WASH, stroke="#D8DEE3", width=1.1, rx=6)
        fig.text(x0 + 14, y + 2, head, size=13.5, weight=640, fill=BLUE)
        words, line, lines = body.split(" "), "", []
        for word in words:
            if len(line) + len(word) + 1 > 70:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        lines.append(line)
        for j, text in enumerate(lines[:3]):
            fig.text(x0 + 14, y + 21 + j * 16, text, size=11.5, fill=MUTED)
    fig.save(out / "architecture-rsi-tree.svg")


# --------------------------------------------------------------------- figure 3
def skill_layers(out: Path) -> None:
    layers = [
        ("identity", ["Who am I acting as,", "and which quota pays?"], ORANGE,
         ["kaggle-cli", "kaggle-account-switch", "account-rename-visualizer"], "quota pays for"),
        ("research", ["What is this competition,", "and what should I build?"], GREEN,
         ["kaggle-competition-research", "approach-decision"], "plan becomes a run"),
        ("experiment", ["How do I run it, watch it,", "and learn from it?"], BLUE,
         ["experiment-launch", "log-monitor", "log-monitor-visualizer", "rsi-experiment-tree",
          "scientific-plotting", "ablation-design"], "tree becomes the doc"),
        ("collab", ["How does this survive", "into the next session?"], PINK,
         ["handoff", "github-auth", "presence-mode", "evidence-sources", "genui-scenarios",
          "technical-report"], "run becomes a result"),
    ]
    LEFT, SKILL_X, RIGHT = 40, 268, 1080
    ROW_H, GAP = 34, 9

    def sw(skill: str) -> float:
        # Per-skill, not one width for the lot: a fixed width taken from the longest name made
        # `kaggle-cli` as wide as `kaggle-competition-research` and threw the wrapping off.
        return 13 + 7.4 * len(skill)

    def rows(skills: list[str]) -> list[list[str]]:
        out_rows: list[list[str]] = [[]]
        for skill in skills:
            used = sum(sw(s) for s in out_rows[-1]) + 10 * max(0, len(out_rows[-1]) - 1)
            if out_rows[-1] and used + sw(skill) > RIGHT - SKILL_X:
                out_rows.append([])
            out_rows[-1].append(skill)
        return out_rows

    laid = [(name, question, accent, rows(skills), yields)
            for name, question, accent, skills, yields in layers]
    heights = [max(len(r) * (ROW_H + GAP) + 30, 30 + len(q) * 17 + 52)
              for _, q, _, r, _ in laid]
    # The trailing gap is counted once per layer, because the loop below adds it every time -
    # sizing off len(layers) - 1 left the last section hanging 9px off the bottom.
    height = 112 + sum(heights) + 22 * len(laid) + 150

    fig = Figure(1120, height, "Seventeen skills, four layers",
                 "A category is a grouping you read; an edge is a dependency the checker enforces.")

    y = 112
    for (name, question, accent, r, yields), h in zip(laid, heights):
        fig.box(LEFT, y, 1040, h, fill=WASH, stroke=accent, width=1.6, rx=9)
        fig.parts.append(f'<rect x="{LEFT}" y="{y}" width="6" height="{h}" fill="{accent}"/>')
        fig.text(LEFT + 22, y + 30, name, size=18, weight=660, fill=accent)
        for i, line in enumerate(question):
            fig.text(LEFT + 22, y + 54 + i * 17, line, size=12.5, fill=MUTED)
        fig.text(LEFT + 22, y + h - 16, f"yields: {yields}", size=12, fill=MUTED)
        block = len(r) * (ROW_H + GAP) - GAP
        for row_index, row in enumerate(r):
            x = SKILL_X
            ry = y + (h - block) / 2 + row_index * (ROW_H + GAP)
            for skill in row:
                w = sw(skill)
                fig.box(x, ry, w, ROW_H, fill=PAPER, stroke=accent, width=1.1, rx=6)
                fig.text(x + w / 2, ry + 22, skill, size=12, fill=INK, anchor="middle")
                x += w + 10
        y += h + 22
        if name != "collab":
            fig.arrow(LEFT + 22, y - 22, LEFT + 22, y - 4, color=accent)

    fig.text(LEFT, y + 26, "Enforced, not described", size=17, weight=650, fill=INK)
    fig.rule(LEFT, y + 38, 1040)
    facts = [
        ("relationships.json", "every skill, every edge, and the live state those edges gate on"),
        ("categories/*.md", "tables rendered from that file - check_plugin fails when they drift"),
        ("1537 assertions", "an edge described only in prose can rot; one declared and checked cannot"),
    ]
    for i, (head, body) in enumerate(facts):
        yy = y + 68 + i * 28
        fig.text(LEFT, yy, head, size=13, weight=640, family=MONO, fill=BLUE)
        fig.text(280, yy, body, size=12.5, fill=MUTED)
    fig.save(out / "architecture-skill-layers.svg")


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "docs"
    out.mkdir(parents=True, exist_ok=True)
    launch_path(out)
    rsi_tree(out)
    skill_layers(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
