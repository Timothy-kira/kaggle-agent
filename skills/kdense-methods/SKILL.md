---
name: kdense-methods
description: Use when a competition experiment is about to be declared, when the tree has stalled, when a branch is opening on different data, or during the research sweep before the general search - and the question is whether published experimental method already answers the thing being planned. Covers the three steps in order (search, then confirm the index, then download), why the index is scraped from the page upstream keeps rather than shipped here, which of the four moments is allowed to reach the network, the four gates a download passes, and what to do when a download fails. Not for running an experiment, and not for judging whether a measurement can resolve a change - that is `ruler-audit`, and it comes first.
---

# Published method, before the experiment

## Why this exists

An experiment tree records what *you* tried. It cannot record that the method you are about to
invent was published in 2021, with a procedure, a failure mode and a way to allocate runs that
you would otherwise have had to derive from scratch. The tree's own refuted list is the right
place to notice a repetition; it is the wrong place to notice a reinvention, because a
reinvention is not in the tree at all.

So this is a lookup that happens at four fixed moments, and the moments are the design. Between
them, this skill does almost nothing on its own — it hands the agent a shortlist and the words
that matched, and the agent decides.

## The three steps, in this order

The order is the whole point, and running them out of order produces the two failures this
exists to prevent.

### 1. Search

`kaggle_methods action="search"` with a `change` and a `hypothesis`.

**This step opens no socket.** It reads the index on disk and ranks it. That is not an
optimisation: `search` runs on every declaration, and a network round trip in the experiment
loop is a dependency the loop should not carry.

Matching is lexical and it is weighted by rarity. A word that appears in most of the index
counts for little; a word that appears in one counts for a lot. That is what lets a whole
sentence be the query while the signal inside it still lands — the query *"generate competing
rival explanations before choosing a test"* is mostly filler, and `rival` and `explanations` are
the two words that mean something.

A match must clear two bars: at least three distinct words in common, and a weighted share of
the query's weight. Both are needed. The count is what stops a three-word query from scoring a
perfect match against a long description; the weighted share is what stops a long query from
being carried by its own filler.

### 2. Confirm the index

`kaggle_methods action="refresh"`, or `action="probe"` to ask without re-reading.

The index is not shipped in this package. It is scraped from `docs/skills.md` in the upstream
repository — the page upstream keeps for exactly this list, one request, every skill with its
name and description. A bundled index would be a snapshot of somebody else's repository taken
on one day, and a stale answer to *"is there published method for this?"* is worse than no
answer, because it reads as a search that found nothing.

So freshness is a question with an answer, not an assumption: `probe` compares the cached
commit against upstream's current `main` and says whether the index may be out of date. What to
do about that is `presence-mode`'s call, not this skill's.

### 3. Download

`kaggle_methods action="fetch"` with a `name` and a `commit`.

This is the only step that puts a third party's words in front of an agent, and a downloaded
`SKILL.md` is **instructions**, not data. It passes four gates in this order, and the order is
the reason it is safe enough to do at all:

1. **Allowlist.** Only the names `action="allowed"` prints. A name appearing in the index is not
   permission.
2. **Pin.** A commit, never a branch. `main` moves, and a method that changed between the plan
   being written and the plan being followed is a plan nobody read.
3. **Cache first.** Already on disk means no socket at all, keyed by commit so a new version
   lands beside the old one instead of over it.
4. **Scan.** The same `scan_for_injection` the package's own check runs, on the same definition.
   A clean scan means no known-bad string matched — a floor, not a clearance, and the tool says
   so in its own output.

Downloads land under `~/.kaggle-agent/skill-cache/<sha>/`, outside the package and outside git.

## The four moments, and which may reach the network

| | When | Network |
|---|---|---|
| **Research** | after wave 1, before wave 2 | yes — all three steps |
| **Every declaration** | before each `declare` | no — search only |
| **Stall** | after two or three flat rounds | no — search only |
| **New branch** | when a branch opens on different data | yes — all three steps |

The two that must not touch the network are the two that sit inside the experiment loop. That
is the whole reason the split exists, and it is why a failed download in the middle of a run is
a bug rather than a slow path.

## The two that come after the ruler

At a declaration and at a stall, `ruler-audit` goes first: stage 2 before every declaration,
stage 4 when the tree stops moving. **Ask whether the metric can resolve the change at all
before asking whether a published method exists for it.** A method cannot rescue a measurement
that cannot see the result — the finding would be real, legible, and unreadable. The reverse
order is just as silly: hunting for a method and then discovering the ruler cannot see it.

## A new branch is where the old answer stops applying

Opening a branch means the conditions changed — a different feature set, a different snapshot,
a different reading of the same objective. A refutation earned on the old one is a statement
about a measurement taken under conditions that no longer hold, so `consider` will not carry it
across: it names the branch the refutation came from, keeps the node visible in `matches`, and
leaves the call to the agent. Re-run all three steps when a branch opens.

## When a download fails, stop and ask

A failure here is not something to absorb. If the method is needed and cannot be fetched, the
tree is about to make a decision on incomplete information, and nobody would know why.

The tool reports **what it observed** — the HTTP status, the transport's own error text, and an
offline capability probe — and deliberately does not map those to a remedy. Which of these is
the user's to fix, which is a stale pin this package should update, and which is a rate limit
that will pass, are three different jobs, and a table written here would be wrong about at least
one of them the first time upstream did something new.

What to do with that, concretely: hand the report to `presence-mode`. Present means ask;
away means `action="record"` the decision to continue on cache and mark it for when the user
returns. A deferred notice is not a skipped one.

## What this skill does not do

- **It does not judge relevance for you.** It ranks and it says which words matched. Which
  candidate to read is a judgement, and it stays with the agent.
- **It does not fetch on its own initiative during a run.** If a search recommends something not
  on disk, it comes back marked `not downloaded`. Asking for it is a separate, explicit call.
- **It does not record anything by itself.** A skill you read gets a `kind="research"` node with
  `targets: ["skill"]`, the name, the upstream commit, and whether you read it or only saw it
  recommended. That is `evidence-sources`' relation, not a new vocabulary.

## Also worth knowing

- The upstream catalogue is MIT-licensed and lives at
  `K-Dense-AI/scientific-agent-skills` (formerly `K-Dense-AI/claude-scientific-skills`, which
  still resolves). The pin this package was taken at is
  `065b734670d7d990627dbc06a05b5a99be33f1f1`; name that commit rather than a branch, because a
  branch moves under the fetch.
- Eight bodies from that catalogue are already vendored whole under four host skills'
  `references/kdense/` — `hypothesis-generation`, `scientific-critical-thinking`, `seaborn`,
  `scientific-visualization`, `scientific-writing`, `scientific-slides`,
  `scientific-brainstorming`, `experimental-design` — each with a per-file sha256 manifest. A
  body already in the package needs no download, and `kaggle_methods action="fetch"` will still
  put a second copy in the cache; read the vendored one instead.
- The catalogue is organised by scientific *domain* — biology, chemistry, databases, and so on
  — not by machine-learning practice. A ML competition will match `experimental-design` and
  `statistical-power` and match almost nothing else, and that is the true shape of the library
  rather than a gap in the index.
- `scientific-visualization` is already vendored in full as `scientific-plotting`, and
  `experimental-design` is adapted into `ablation-design`; the index will list both, and fetching
  either would duplicate what is already in the package.
