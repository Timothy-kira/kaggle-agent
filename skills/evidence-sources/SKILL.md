---
name: evidence-sources
description: Use when a conclusion, a plan or a node decision rests on something that was read - a paper, a repository, a dataset, a model card or a forum thread - and that source needs to be stored and linked so the reasoning can be reviewed later. Covers adding a source, capturing the sentence that carries a claim, linking it to a tree node with an explicit relation, reverse lookup of what a paper supports, and reassembling a full evidence chain at review time. Also covers what to do when the evidence was a local run rather than a citation, and why an unextracted link is not a citation.
---

# Evidence sources: keeping the reasoning reviewable

## Why this exists

A tree node records what you concluded and why. On its own it does not record **what you read** to
conclude it. So a review three weeks later can see that n7 was kept for "+0.09, errors shifted
from malformed to genuine dead ends" — and nothing about which paper said that, which commit was
forked, or whether that paper was licensed for reuse. The reasoning is unreviewable, and the next
session re-derives it from scratch.

This is the missing half: the source is stored once, and nodes link to it.

## Store it once, link it many times

The same paper supports five nodes. A claim quoted into five node bodies drifts away from the paper
within a couple of edits, and nobody notices. So:

```
kaggle_sources action="add" kind="paper" title="..." url="..." arxiv="2609.14858" licence="arXiv perpetual" summary="what it actually claims"
```

A source already stored under the same URL, arXiv id or DOI returns the **existing** record rather
than a second one. Two ids for one paper is how a ledger quietly rots: a node linked to the stale
one, and no way to tell.

**The licence is a finding, not a field.** A repository with no licence cannot legally be built
on, and finding that out late is expensive. Record `licence` honestly; `unknown` is an acceptable
recorded answer, silence is not.

## Capture the sentence, not just the link

```
kaggle_sources action="extract" source_id="s1" \
  quote="accumulated discovery history can serve as a replay simulator" \
  claim="why replay costs nothing" locator="abstract"
```

**A link with no stored quote is a claim someone asserted existed.** A link with the sentence
behind it is something a reviewer can actually check. The tool refuses an empty quote for exactly
this reason.

## Attach it to the node, with a relation

Sources ride along on the node at record time, so the node and its evidence are created together:

```json
{"id": "n7", "kind": "experiment", "parent": "n6",
 "change": "add a verifier pass before submission",
 "metric": {..., "rank": 0.77, "rankSource": "leaderboard percentile 0.77"},
 "operator": "crossover", "family": "verifier", "verdict": "keep",
 "reason": "+0.09, errors shifted from malformed to genuine dead ends",
 "sources": [{"sourceId": "s3", "relation": "supports",
              "quote": "..."}]}
```

Later, when a new source turns up, attach it to an existing node:

```
kaggle_sources action="link" competition="<c>" node_id="n7" source_id="s9" relation="contradicts"
```

**The relation is not decoration.** `supports`, `motivates`, `contradicts` and `supersedes` are
different claims. A tree that flattens them cannot later answer which evidence justified a kept
node versus which evidence merely suggested the next one. The same source may legitimately appear
twice on a node with different relations — one paper can both support and contradict your reading.

A `sourceId` that is not in the store is **rejected at record time**, so a typo cannot leave a link
that silently resolves to nothing.

## When there is no paper

Most experiments do not come from a paper. A hyperparameter nudged because the previous run was
slow has no literature behind it, and claiming otherwise would be worse. So every concluded node
must take one of two paths:

- attach at least one source, or
- set `evidence: "local-only"` to say plainly that the evidence was a local run.

A node with **neither is rejected**. That is not bureaucracy: unstated provenance is precisely what
makes a review impossible, because the reader cannot tell a considered decision from an unconsidered
one that happened to work.

## Review: put the chain back together

```
kaggle_experiment_tree action="review"
kaggle_sources action="coverage" competition="<c>"
```

`review` reassembles the tree with its evidence attached — each claim next to the sentence behind
it, failures grouped by harness layer, the coverage ratio, and any link that still has no stored
quote. It ends with the **next questions the tree itself raises**, which are openings rather than
instructions:

- conclusions with nothing behind them
- a harness layer that keeps absorbing the refutations
- research nodes that say the picture was insufficient, and whether anyone acted on them

That last one is how the loop continues. A `research` node records that you went back to a source;
`review` tells you whether reading it actually changed anything, and the coverage and backlink
views make the re-read cheap instead of re-doing the search.

### The direction a ledger usually misses

```
kaggle_sources action="backlink" source_id="s1"
```

Which nodes across **every** tree cite this source, and how. A paper that supported three decisions
in a row is a much stronger reason to trust it than one mentioned once.

### The dangerous kind of stored source

```
kaggle_sources action="search" needs_extract=true
```

Sources nobody has quoted yet. These are the ones a review cannot use, and they are worth
surfacing on purpose rather than discovering later.

## Also worth knowing

- `kaggle_sources action="stats"` reports the store's shape, including how many sources have no
  licence and how many have no extract. Both numbers are quality signals, not just counts.
- The store is one JSON file per source under the plugin's state directory, so two agents writing
  different papers cannot collide.
- This skill is bound to `rsi-experiment-tree` in the relationship graph: the tree requires
  provenance, and this skill is how provenance is supplied. Neither half works alone.

## Cross-references

- `rsi-experiment-tree` — the tree that consumes this evidence and demands provenance.
- `kaggle-competition-research` — where sources are first stored, and where a `research` node
  goes back to them.
- `scientific-plotting` — the figures that make a reviewed result legible.
- `../relationships.json` — the `needs` edge between this skill and the tree.
