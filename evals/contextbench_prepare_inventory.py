"""Estimate fresh/forced bottom-up preparation work without model calls."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from evals import RESULTS
from evals.contextbench_snapshots import HOLDOUT
from fastindex.bundle import RESERVED, SKIP_DIRS
from fastindex.utils.prepare import GROUP_SIZE, _text_chunks, _validate_text

SNAPSHOTS = RESULTS / "contextbench-snapshots.json"


def _reduction_calls(parts: int) -> int:
    calls = 0
    while parts > 1:
        parts = (parts + GROUP_SIZE - 1) // GROUP_SIZE
        calls += parts
    return calls


def _inventory(root: Path) -> dict[str, int]:
    directories = files = skipped = segments = reductions = characters = bytes_read = 0
    existing_indexes = 0
    for dirpath, child_dirs, names in os.walk(root, followlinks=False):
        child_dirs[:] = sorted(name for name in child_dirs
                               if name not in SKIP_DIRS
                               and not (Path(dirpath) / name).is_symlink())
        directories += 1
        existing_indexes += (Path(dirpath) / "index.md").is_file()
        for name in names:
            path = Path(dirpath) / name
            if name.startswith(".") or name in RESERVED or path.is_symlink() or not path.is_file():
                continue
            try:
                _validate_text(path)
                chunks = list(_text_chunks(path))
            except (UnicodeError, OSError):
                skipped += 1
                continue
            files += 1
            count = len(chunks)
            segments += count
            reductions += _reduction_calls(count)
            characters += sum(map(len, chunks))
            bytes_read += path.stat().st_size
    # In a fresh/forced build, each child index is summarized at least once by
    # its parent. Long indexes and retries can only increase this call count.
    child_index_calls = max(0, directories - 1)
    return {"directories": directories, "source_files": files,
            "skipped_files": skipped, "existing_indexes": existing_indexes,
            "source_characters": characters, "source_bytes": bytes_read,
            "source_segment_calls": segments, "source_reduction_calls": reductions,
            "child_index_calls_lower_bound": child_index_calls,
            "model_calls_lower_bound": segments + reductions + child_index_calls}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path,
                        default=RESULTS / "contextbench-prepare-inventory.json")
    args = parser.parse_args()
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
    snapshots = {row["id"]: row for row in json.loads(
        args.snapshots.read_text(encoding="utf-8"))["cases"]}
    selected = cases[:args.limit] if args.limit else cases
    results = []
    for case in selected:
        root = Path(snapshots[case["id"]]["root"])
        actual = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        if actual != case["base_commit"]:
            raise ValueError(f"Snapshot commit changed: {root}")
        started = time.perf_counter()
        counts = _inventory(root)
        results.append({"id": case["id"], "repo": case["repo"],
                        "base_commit": actual, "elapsed_ms":
                        (time.perf_counter() - started) * 1000, **counts})
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_suffix(args.out.suffix + ".tmp")
        temporary.write_text(json.dumps({"results": results}, indent=2), encoding="utf-8")
        temporary.replace(args.out)
        print(f"{len(results)}/{len(selected)} {case['repo']} "
              f"{counts['model_calls_lower_bound']} minimum calls", flush=True)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
