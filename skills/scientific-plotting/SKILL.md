---
name: "scientific-plotting"
description: "Plot the experiment tree's results and any other figure honestly, accessibly, and ready to publish. Step 0 checks the plotting backend and installs it on the user's word before anything is drawn, Step 1 writes the figure contract - conclusion, archetype, panel map, evidence hierarchy, statistics, reviewer risk - before any plotting code, then the six charts the tree earns via action='analyze', and the craft rules - honest encoding, redundant colour, explicit uncertainty and missing data, inspected exports - applied to any figure."
license: "MIT. Vendored from K-Dense Scientific Agent Skills (c) 2025 K-Dense Inc., https://github.com/K-Dense-AI/claude-scientific-skills/tree/main/skills/scientific-visualization. Adapted here: flat frontmatter, the install step, and the tree entry point."
---

# Scientific plotting

Build figures that preserve scientific meaning before optimising appearance. For a competition
run that means one thing above all: **the figure is the analysis**, not a picture of it.

## Step 0 — the backend, before any figure

Figures are drawn with **numpy and matplotlib**. That is the engine, not an upgrade to it.

```
kaggle_sources action="doctor"
```

Read the answer before drawing anything:

- `plottingReady: true` → go to step 1.
- `plottingReady: false` → the `nextStep` field already carries the command. **Show it to the
  user and get an answer before running it.** Installing changes their Python environment, so
  it is their decision, not a step to take silently. If they decline, stop: no figure can be
  produced, and saying so is the honest answer. Do not fall back to drawing something by hand.

```
kaggle_sources action="install" packages='["matplotlib", "numpy"]'
```

`packages` is a JSON array in one string — the host's tool layer empties a real array argument
before the plugin sees it.

Draw only after `doctor` reports ready. A chart call made before that returns
`backend_missing` with the same command in it; that is the designed failure, not a bug to work
around.

## Step 1 — write the figure contract, before any plotting code

Before the first figure, write six lines. Not a template to fill in afterwards — the value is
that they are written **first**, while the claim is still small enough to be wrong.

| Line | The question it forces |
|---|---|
| **Conclusion** | one sentence: what this figure shows that a number alone does not |
| **Archetype** | which form it takes — trend, comparison, distribution, relationship, composition |
| **Panel map** | which panel carries which part of the argument, and what each is *not* for |
| **Evidence hierarchy** | which number is load-bearing, and which is context |
| **Statistics** | what has to be on it for the conclusion to hold — n, error, baseline |
| **Reviewer risk** | the one question a hostile reader asks, and the honest answer |

Two of those earn their place by failing loudly.

**The conclusion line is a filter, not a caption.** If you cannot write it in one sentence, the
figure has no claim to make and `action="analyze"` will draw something anyway — six charts from a
tree that supports them. That is the default worth resisting: a figure nobody needed is not neutral,
it is an invitation to read a pattern into noise. The line that says "is this delta real, or inside
the noise?" is what makes the `band` chart necessary rather than decorative.

**The reviewer-risk line is where over-reading gets caught.** Name the question before you can see
the chart, because afterwards the chart will argue for itself. "Is the gain inside the noise?" has
an answer the figure can show — a band. "Does this generalise beyond this split?" does not, and
writing that down is how you find out you need a second split rather than a bigger axis range.

This costs four minutes and it is the only step in this skill that is a *thinking* step rather than
a mechanical one. Nothing here can be checked by a gate: a contract can be present and still be
worthless. That is a limitation, not a reason to skip it, and it is the same split the claim audit
enforces elsewhere — a deterministic pass can refuse, and cannot acquit.

## Step 2 — the figures this tree has earned

```
kaggle_experiment_tree action="analyze"
```

One call reads the tree, draws every figure the data actually supports, and attaches each path
to the nodes it came from. It also reports what it **could not** draw and why, which matters
more than it sounds: a figure silently missing is read as "nothing there to see".

| Chart | The question it answers that a number cannot |
|---|---|
| `line` | is the metric actually going anywhere, or did we plateau? |
| `band` | **is this delta real, or inside the noise?** |
| `bar` | which operator or family actually produced the gain? |
| `scatter` | what did quality cost per unit of compute? |
| `pareto` | what was worth its cost — which points are on the non-dominated frontier? |
| `forest` | which criterion collapsed while the composite stayed stable? |

`band` is the one that prevents over-reading. A `+0.02` drawn as a bare number is
indistinguishable from a real gain; drawn as a mean **inside its own band** it is visibly not
one. `select` already flags `withinNoise` when `|delta| < 2·std`, and the figure makes it
impossible to forget. A node with no recorded `samples` gets **no band** — drawing a band
through a single measurement is the exact fabrication the band exists to prevent.

`pareto` is how the next experiment gets chosen: a candidate is worth its cost only if nothing
cheaper is at least as good, which turns "what should we try next" from a preference into a
reading. `forest` exists because a composite can look perfectly steady while one criterion
quietly falls over.

## Non-negotiable guardrails

- Never alter, hide, invent, or selectively enhance data to improve a figure.
- Do not silently connect missing observations, suppress inconvenient points, or tune axes to
  exaggerate a conclusion. A gap in the record is not a measurement.
- Do not claim that a palette, a DPI value, or an automated report makes a figure accessible.
- Do not infer journal requirements. Identify the journal, article type and submission phase,
  and verify its live official guidance.

## Choose an honest encoding

- **Bars/areas** normally include zero, because length is measured from a baseline.
- **Points/lines** may use non-zero limits; show context and disclose any break.
- **Uncertainty** names what it is — SD, SE, CI, percentile — and states `n`.
- **Missing data** is distinguished from zero, from censored, and from excluded.
- **Area/volume** scales area, not radius. No decorative 3D.
- **Log axes** label the base and say how zero and negatives are handled.
- **Normalisation** states its formula and keeps limits consistent across compared panels.
- **Dual axes** are a last resort; if unavoidable, justify the units and do not engineer an
  apparent correlation.

## Design accessibility in, not after

Use **colour plus marker, line style, hatching, direct label, or panel separation** — colour is
never the only cue. The bundled palette is the Okabe-Ito set filtered to five colours that pass
WCAG 3:1 against white; four of its ten pairs still sit close in greyscale, which is exactly why
every series also carries its own marker and dash. Audit a change before believing it:

```bash
python skills/scientific-plotting/scripts/palette_audit.py \
  --palette okabe_ito_on_white --background FFFFFF --role graphical
```

## Bundled helpers

All deterministic, network-free, and they refuse to overwrite unless you pass `--force`.

```bash
# export with journal-correct resolution, atomic write, and a provenance manifest
python skills/scientific-plotting/scripts/figure_export.py --demo outputs/export-smoke --manifest

# inspect a delivered file: dimensions, effective DPI, alpha, page size, embedded fonts
python skills/scientific-plotting/scripts/image_metadata.py figure.tiff --format tiff --min-dpi 300

# screen a publisher's requirements (dated snapshots, not compliance rules)
python skills/scientific-plotting/scripts/export_plan.py --publisher nature --figure-type combination --width single --phase final

# inspect and preview the bundled styles
python skills/scientific-plotting/scripts/style_presets.py --list
python skills/scientific-plotting/scripts/style_presets.py --show nature
```

Assets: `assets/publication.mplstyle` (general print), `assets/nature.mplstyle` (a dated visual
starting point, not a compliance preset), `assets/presentation.mplstyle`,
`assets/color_palettes.py`, `assets/publisher_profiles.json`.

References: `references/publication_guidelines.md` (integrity and deceptive encodings),
`references/color_palettes.md`, `references/journal_requirements.md`,
`references/matplotlib_examples.md`, `references/sources.md`.

## Reading order after a run

1. `analyze` — get the figures.
2. `select` — the batch to expand next, with `regressedCriteria` next to the scores.
3. `board` — gain per operator, per-criterion EFC, refutations by harness layer.
4. `record` — the new node, with its samples, its cost and its provenance.
5. `round_close` when the round is done, which turns the tree into a replayable history.

## Final review checklist

- [ ] Missing values, exclusions, bins, normalisation and uncertainty are explicit.
- [ ] Baselines, scales and limits are honest.
- [ ] Colour is redundant and the rendered contrast was reviewed.
- [ ] Physical dimensions, DPI, format and file size were inspected after export.
- [ ] No automated report is presented as an accessibility or compliance certification.

## Cross-references

- `rsi-experiment-tree` — produces the data every chart here draws; `analyze` reads from it.
- `ablation-design` — decides which arms exist to be plotted at all.
- `technical-report` — embeds these figures and states the ones that could not be drawn.
- `evidence-sources` — supplies the provenance a node is required to carry.

## Attribution

`assets/`, `references/` and `scripts/` are vendored from **Scientific Agent Skills** by
K-Dense Inc. (MIT). If they materially contributed to a manuscript, report, or code release,
cite:

> Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). Scientific Agent
> Skills: A Library of Procedural Knowledge for Research Agents. arXiv:2609.00065.
> https://doi.org/10.48550/arXiv.2609.00065
