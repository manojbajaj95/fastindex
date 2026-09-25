#!/bin/sh
# Re-score archived rankings against pinned public source without model calls.
set -eu
cd "$(dirname "$0")/.."

uv sync --extra evals
mkdir -p evals/fixtures/external/contextbench evals/results
PARQUET=evals/fixtures/external/contextbench/full.parquet
if [ ! -f "$PARQUET" ]; then
  curl -fL 'https://huggingface.co/datasets/Contextbench/ContextBench/resolve/c2855792b006af41c67202d33883fb9d46362853/data/full.parquet?download=true' \
    --output "$PARQUET"
fi
printf '%s  %s\n' '2f56535bdc73eb8a68bf4ebb49789d8e9cd4f219ea60df6290b85278aee61ca8' "$PARQUET" \
  | shasum -a 256 -c -

uv run --with pyarrow python -m evals.contextbench_holdout \
  --manifest evals/fixtures/queries/contextbench_paper.json \
  --out evals/results/contextbench-paper-holdout.json
uv run python -m evals.contextbench_snapshots \
  --holdout evals/results/contextbench-paper-holdout.json \
  --out evals/results/contextbench-paper-snapshots.json
uv run python -m evals.contextbench_audit \
  --holdout evals/results/contextbench-paper-holdout.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --out evals/results/contextbench-paper-audited.json
printf '%s  %s\n' '28b0f8961bf191da6e504b82df13bdabacca006565a901f52cedd40bb94e330e' \
  'evals/results/contextbench-paper-audited.json' | shasum -a 256 -c -
uv run python -m evals.contextbench_study \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --tree paper/data/contextbench-paper-tree.json \
  --fts5 paper/data/contextbench-paper-fts5.json \
  --rg paper/data/contextbench-paper-rg.json \
  --gold-audit paper/data/contextbench-paper-gold-audit.json \
  --out evals/results/contextbench-paper-rescored.json
uv run --with pyarrow python -m evals.contextbench_annotation_audit \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --out evals/results/contextbench-annotation-audit-rescored.json
uv run python -m evals.contextbench_poststudy \
  --holdout evals/results/contextbench-paper-audited.json \
  --snapshots evals/results/contextbench-paper-snapshots.json \
  --path-fts5 paper/data/contextbench-paper-path-fts5.json \
  --flat-jev paper/data/contextbench-paper-flat-paths.json \
  --rg-feedback paper/data/contextbench-paper-rg-feedback.json \
  --out evals/results/contextbench-poststudy-rescored.json
uv run python - <<'PY'
import json
from pathlib import Path


def check(archive, reproduced, ignored=()):
    expected = json.loads(Path(archive).read_text())
    actual = json.loads(Path(reproduced).read_text())
    for key in ignored:
        expected.pop(key, None)
        actual.pop(key, None)
    if expected != actual:
        raise SystemExit(f"Archived and reproduced results differ: {archive}")
    print(f"Verified {archive}")


check("paper/data/contextbench-paper-study.json",
      "evals/results/contextbench-paper-rescored.json", ("config",))
check("paper/data/contextbench-annotation-audit.json",
      "evals/results/contextbench-annotation-audit-rescored.json")
check("paper/data/contextbench-paper-poststudy.json",
      "evals/results/contextbench-poststudy-rescored.json", ("input_sha256",))
PY
