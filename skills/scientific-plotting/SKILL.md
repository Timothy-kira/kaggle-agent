---
name: "scientific-plotting"
description: "Use after a run has produced a result - plot it, because a number alone does not say whether the delta is real, which family the gain came from, or what the next experiment should be. Covers the six chart types the bundled engine draws, when each one answers a question a table cannot, how noise bands and Pareto frontiers change a reading, and the honest report of a figure that could not be drawn. Also covers the optional environment: plotting always works, and richer chart types are only installed if the user agrees."
---

# Scientific plotting: the figure is the analysis

## Plot after every run, not at the end

```
kaggle_experiment_tree action="analyze"
```

One call reads the tree, draws every figure the data actually supports, and attaches the paths to
the nodes they came from. It also reports what it **could not** draw and why, which matters more
than it sounds: a figure silently missing is read as "nothing there to see".

## No installation, ever

The bundled engine is **pure standard library** and writes SVG by hand. It is not a fallback that
needs something else present first — it is the whole implementation.

That is a deliberate constraint, measured on the machine this was built on: `numpy`, `scipy`,
`pandas`, `statsmodels`, `matplotlib`, `pint` and `pyDOE3` are all absent, and every figure in
these skills still renders. A plugin that tells a user to `pip install` something is a plugin
half the time unusable, and one that silently installs on first run is a plugin nobody can audit.

```
kaggle_sources action="doctor"
```

reports the environment, what is present, and what each absent package would buy. It installs
nothing. If the user *wants* the extra chart types:

```
kaggle_sources action="install" packages='["matplotlib"]'
```

`packages` is a JSON array in one string — the host's tool layer empties a real array
argument before the plugin sees it.

— **only after they have said yes.** Until then, everything below works.

## The six charts, and the question each one answers

| Chart | The question it answers that a number cannot |
|---|---|
| `line` | is the metric actually going anywhere, or did we plateau? |
| `band` | **is this delta real, or inside the noise?** |
| `bar` | which operator or family actually produced the gain? |
| `scatter` | what did quality cost per unit of compute? |
| `pareto` | what was worth its cost — which points are on the non-dominated frontier? |
| `forest` | which criterion collapsed while the composite stayed stable? |

### `band` is the one that prevents over-reading

A mean with its spread. This is the whole reason repeated measurements exist: a `+0.02` drawn as a
bare number is indistinguishable from a real gain, and drawn as a mean **inside its own band** it
is visibly not one. `select` already flags `withinNoise` when `|delta| < 2·std`; the figure makes it
impossible to forget.

If a node has no recorded `samples`, it gets **no band** — and `analyze` says so, because drawing a
band point through a single measurement is the exact fabrication the band exists to prevent.

### `pareto` is how you choose the next experiment

Quality against effective cost, with the non-dominated frontier marked. A candidate is worth its
cost only if nothing cheaper is at least as good. That turns "what should we try next" from a
preference into a reading.

Needs at least two nodes with a recorded `cost.quotaHours`; with fewer, `analyze` reports the
shortfall instead of drawing a one-point frontier.

### `forest` is how a stable average hides a collapse

One row per criterion, with the direction that counts and the spread. It exists because a
composite can look perfectly steady while one criterion quietly falls over, and the reader needs to
see that rather than infer it.

## What the engine is, concretely

- **Vector SVG**, so a figure stays sharp in a paper and its axis values are readable as text
  rather than traced back from pixels.
- **Colour *and* dash/marker**, so a figure survives greyscale printing and colour-vision
  deficiency instead of relying on hue alone.
- **A missing point breaks a line** rather than interpolating across it. A gap in the record is not
  a measurement, and a line drawn through it invents one.
- **It refuses.** A chart with no plottable points returns an error, never an empty figure that
  looks like a result.

## Reading order after a run

1. `analyze` — get the figures.
2. `select` — the batch to expand next, with `regressedCriteria` next to the scores.
3. `board` — gain per operator, per-criterion EFC, refutations by harness layer.
4. `record` — the new node, with its samples, its cost and its provenance.
5. `round_close` when the round is done, which turns the tree into a replayable history.

## Cross-references

- `rsi-experiment-tree` — produces the data every chart here draws.
- `evidence-sources` — supplies the provenance a node is required to carry.
- `../relationships.json` — the `enforces` edge from `experiment-tree` to the tree skill, and the
  edge from that skill to this one.
