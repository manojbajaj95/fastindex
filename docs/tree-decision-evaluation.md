# Typed decision-model evaluation

`tree-decision` uses categorical probabilities to prune a prepared tree and
choose one evidence section per selected page. The provider and model are
configuration, rather than separate retrieval strategies. It generates no text
at query time. `prepare` still uses an LLM to create missing indexes.

## September 20–21, 2026 results

These are single cold-track runs over the ten queries in
`evals/fixtures/queries/sample.jsonl`, with `top_k=2`. Every comparison within
an index group used the same bundle and span-level gold. Values are means;
calls and query cost are totals. Preparation cost is excluded.

| Prepared indexes | Strategy | Span recall | Span F1 | Line F1 | Latency | Calls | Query cost |
|---|---|---:|---:|---:|---:|---:|---:|
| Purpose-oriented generated | `tree-reason` / `gpt-5.6-luna` | 0.95 | 0.933 | 0.831 | 9.14 s | 52 | $0.0076 estimated |
| Purpose-oriented generated | `tree-decision` / classifier Jev | 0.90 | 0.883 | 0.718 | 2.34 s | 38 | $0.0000 |
| Compact fixture | `tree-decision` / classifier Jev | 0.90 | 0.883 | 0.674 | 2.42 s | 38 | $0.0000 |
| Compact fixture | `tree-decision` / classifier Laya | 0.90 | 0.633 | 0.502 | 5.62 s | 47 | $0.0000 |
| Routing-prompt generated | `tree-decision` / TypeSafe Jev 1.13 | **0.95** | **0.933** | 0.759 | 4.19 s | 38 | $0.00082 estimated |
| Routing-prompt generated | `tree-decision` / classifier.dev Jev 1.13 | 0.90 | 0.917 | **0.762** | 2.39 s | 36 | $0.0000 |
| Routing-prompt generated | `tree-reason` / `gpt-5.6-luna` | 0.80 | 0.833 | 0.734 | 12.58 s | 48 | $0.0066 estimated |

Direct TypeSafe Jev reached 0.95 span recall and 0.933 span F1, matching the
strongest observed LLM result in this fixture. On the same routing indexes it
beat `tree-reason` quality, ran 3.0 times faster, and cost about 8.0 times less.
The free classifier.dev route was faster still, but lost half of one additional
multi-page query. These measurements are too small and unreplicated for a
general speed or quality claim.

## Experiments

### Index generation

The routing prompt asks for the questions each item directly answers and exact
entities, while excluding incidental linked topics.
Compared with the earlier purpose-oriented prompt, Jev's span F1 rose from
0.883 to 0.917 and line F1 rose from 0.718 to 0.762. Recall stayed at 0.90.
The implementation also retries one empty model response and bounds a summary
deterministically when a model ignores the second length instruction.

An additional prompt that preserved every explicit join and SKU relationship
did not raise recall and lowered span F1 back to 0.883, so it was not retained.
The two remaining sample misses each require two source pages. For “How do
orders join to customers?”, Jev strongly preferred `orders.md` and pruned
`customers.md`. For the espresso-tonic SKU question, it followed a plausible
POS overview instead of assembling the recipe and orders pages. Preserving
relationships in the labels did not overcome the categorical branch choice;
the walk still does not perform link traversal.

### Classification and pruning

The classifier.dev instruction is “Choose the source with direct evidence to
answer the user question.” More explicit variants about exact evidence and
cross-page questions did not improve both misses. A separate query-level
classifier for “one page” versus “multiple pages” also left aggregate recall
and F1 unchanged while adding ten calls.

TypeSafe's direct Choice API initially over-selected `none_of_these` because a
directory summary is a route to evidence rather than the evidence itself. Its
provider adapter now states that directory options summarize descendants and
reserves none for queries unrelated to every option. Including the actual
frontmatter resource URL in metadata choices recovered resource-location
questions. Together these changes raised the direct TypeSafe run from 0.65
span recall and 0.667 span F1 to 0.95 and 0.933.

Opening branches more widely (`decision_min_probability=0.04`,
`decision_relative_probability=0.05`) raised span recall from 0.90 to 0.95,
but lowered span F1 from 0.883 to 0.850 through extra false-positive spans.
This is useful when recall is the priority; the defaults retain the better F1.

### Laya and multi-hop limit

Laya completed a standalone run after retrying hosted 429/503 responses, but
its 0.633 span F1 trailed Jev's 0.883 on the same compact indexes. This does not
rule out an on-device Laya deployment; the hosted trial includes queueing and
its model behavior is only one configuration.

Jev reached 0.417 span recall and 0.550 span F1 on the six dedicated multi-hop
queries. All six require following relationships between pages. The current
tree walk only descends the directory hierarchy, so prompt tuning cannot fully
solve that benchmark. Link traversal or another retrieval stage is required.

## Cost and service caveats

[classifier.dev](https://classifier.dev/) currently exposes a free Jev route
and a limited Laya trial. The recorded `$0.0000` is the bill for these runs,
not a production price forecast. classifier.dev does not expose compatible
token usage, so `input_tokens` is a rough character-based estimate. LLM cost
uses LiteLLM's pricing table.

The official [TypeSafe API](https://docs.typesafe.ai/api) is supported as
`typesafe/jev-latest`. It returns exact token usage; fastindex estimates query
cost using the current [Jev model price](https://docs.typesafe.ai/models). The
ten-query run used 19,512 input tokens and 1,842 output tokens. TypeSafe bills
input tokens, producing the $0.00082 estimate above. It requires
`TYPESAFE_API_KEY`.

The public [JevBench repository](https://github.com/fstandhartinger/jevbench)
is a broader independent decision-model benchmark. The linked
[jevbench.dev leaderboard](https://jevbench.dev/leaderboard) contains a small
set of application smoke checks and is not comparable with span retrieval.

## Reproduce

```bash
FASTINDEX_DECISION_MODEL=classifier/jev \
  uv run python -m evals.bench --strategies tree-reason,tree-decision
FASTINDEX_DECISION_MODEL=typesafe/jev-latest \
  uv run python -m evals.bench --strategies tree-decision
uv run python -m evals.bench --strategies tree-decision \
  --fixtures evals/fixtures/queries/multi_hop.jsonl
```

Raw runs are under gitignored `evals/results/`. They are deliberately not
committed. The reported generated bundles are also local experiment artifacts.
