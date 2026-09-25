"""Fetch pinned full files for ARB trace2code span diagnostics.

Files and the verification manifest are local and gitignored. A fetched file is
accepted only if it matches the released file chunk and covers its gold lines
when it is a gold file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from urllib.request import Request, urlopen

from evals import RESULTS
from evals.arb_trace_ablation import DEFAULT_RELEASE, _files

DEFAULT_SOURCE_ROOT = DEFAULT_RELEASE.parent / "full-source-gold"
TRUNCATION = "\n...[truncated]"


def _candidate_paths(row: dict, source: str) -> list[str]:
    if source == "rrf":
        return row["rrf_paths"]
    if source == "tree":
        return row["paths"]
    if source in {"literal", "bm25"}:
        return row["rankings"][source]
    raise ValueError(f"Unknown candidate source: {source}")


def _safe_parts(repo: str, commit: str, path: str) -> tuple[str, ...]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError(f"Unsafe repository: {repo}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"Unsafe commit: {commit}")
    rel = PurePosixPath(path)
    if not rel.parts or rel.is_absolute() or "\\" in path or any(
        part in {".", ".."} for part in rel.parts
    ):
        raise ValueError(f"Unsafe source path: {path}")
    return rel.parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--fusion", "--rankings", dest="rankings", type=Path,
                        help="Fetch frozen candidate files instead of all gold files")
    parser.add_argument("--candidate-source", choices=("rrf", "tree", "literal", "bm25"),
                        default="rrf")
    parser.add_argument("--candidate-k", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="Smoke-test the first N distinct files")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.candidate_k < 1 or args.candidate_k > 16:
        parser.error("--candidate-k must be between 1 and 16")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.candidate_source != "rrf" and not args.rankings:
        parser.error("--candidate-source requires --rankings")
    if args.out is None:
        name = (f"arb-full-source-{args.candidate_source}{args.candidate_k}.json"
                if args.rankings
                else "arb-full-source-gold.json")
        args.out = RESULTS / name

    samples = [json.loads(line) for line in (
        args.release / "benchmark/v2_trace2code/samples.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    gold_ends: dict[tuple[str, str, str], int] = defaultdict(int)
    for sample in samples:
        for span in sample["gold_spans"]:
            key = sample["repo"], sample["base_commit"], span["path"]
            gold_ends[key] = max(gold_ends[key], span["end_line"])
    if args.rankings:
        by_id = {sample["id"]: sample for sample in samples}
        rankings = json.loads(args.rankings.read_text(encoding="utf-8"))["results"]
        if {row["id"] for row in rankings} != set(by_id):
            raise ValueError("Candidate ranking and release have different query IDs")
        needed = {
            (by_id[row["id"]]["repo"], by_id[row["id"]]["base_commit"], path):
            gold_ends.get((by_id[row["id"]]["repo"],
                           by_id[row["id"]]["base_commit"], path), 0)
            for row in rankings for path in _candidate_paths(
                row, args.candidate_source
            )[:args.candidate_k]
        }
    else:
        needed = gold_ends
    keys = sorted(needed)
    if args.limit is not None:
        keys = keys[: args.limit]

    chunk_cache = {
        (repo, commit): _files(args.release, repo, commit)
        for repo, commit in {(repo, commit) for repo, commit, _ in keys}
    }

    def fetch(key: tuple[str, str, str]) -> dict:
        repo, commit, path = key
        parts = _safe_parts(repo, commit, path)
        release_files = chunk_cache[repo, commit]
        if path not in release_files:
            raise ValueError(f"Selected file absent from release: {repo}@{commit}:{path}")
        root = args.source_root / repo.replace("/", "__") / commit
        dest = root.joinpath(*parts)
        if not dest.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Source path escapes snapshot: {path}")
        url = (
            f"https://raw.githubusercontent.com/{repo}/{commit}/"
            + "/".join(quote(part, safe="") for part in parts)
        )
        try:
            if dest.exists():
                payload = dest.read_bytes()
            else:
                request = Request(url, headers={"User-Agent": "fastindex-arb-study/1"})
                with urlopen(request, timeout=30) as response:
                    payload = response.read()
            text = payload.decode("utf-8")
            released = release_files[path]
            prefix = released[:-len(TRUNCATION)] if released.endswith(TRUNCATION) else released
            # Some released non-gold chunks omit leading blank lines.
            leading_newlines = len(text) - len(text.lstrip("\r\n"))
            if not (text.startswith(prefix) or text[leading_newlines:].startswith(prefix)):
                raise ValueError("Full source does not match released file-chunk prefix")
            lines = len(text.splitlines())
            if lines < needed[repo, commit, path]:
                raise ValueError(f"Full source has {lines} lines; gold ends later")
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(payload)
            row = {
                "repo": repo, "commit": commit, "path": path,
                "bytes": len(payload), "lines": lines,
                "gold_end_line": needed[repo, commit, path],
                "prefix_leading_newlines": leading_newlines,
                "sha256": hashlib.sha256(payload).hexdigest(), "url": url,
                "status": "verified",
            }
        except (OSError, UnicodeError, ValueError) as exc:
            row = {"repo": repo, "commit": commit, "path": path,
                   "url": url, "status": "error", "error": str(exc)}
        return row

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(fetch, keys):
            rows.append(row)
            print(f"{len(rows)}/{len(keys)} {row['status']} "
                  f"{row['repo']}:{row['path']}", flush=True)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({
                "release": str(args.release), "source_root": str(args.source_root),
                "selection": (f"{args.candidate_source}@{args.candidate_k}"
                              if args.rankings else "gold"),
                "fusion": (str(args.rankings) if args.rankings
                           and args.candidate_source == "rrf" else None),
                "rankings": str(args.rankings) if args.rankings else None,
                "requested": len(keys), "verified": sum(
                    item["status"] == "verified" for item in rows
                ), "results": rows,
            }, indent=2))
    return 0 if all(row["status"] == "verified" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
