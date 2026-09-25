"""Materialize pinned ContextBench repositories and check gold line bounds."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from evals import RESULTS

HOLDOUT = RESULTS / "contextbench-holdout.json"
ROOT = Path(__file__).parent / "fixtures/external/contextbench"
REMOTES = {
    "django/django": "https://github.com/django/django.git",
    "sveltejs/svelte": "https://github.com/sveltejs/svelte.git",
    "huggingface/transformers": "https://github.com/huggingface/transformers.git",
    "sympy/sympy": "https://github.com/sympy/sympy.git",
    "ansible/ansible": "https://github.com/ansible/ansible.git",
    "mui/material-ui": "https://github.com/mui/material-ui.git",
    "microsoft/vscode": "https://github.com/microsoft/vscode.git",
    "serverless/serverless": "https://github.com/serverless/serverless.git",
    "NodeBB/NodeBB": "https://github.com/NodeBB/NodeBB.git",
    "cli/cli": "https://github.com/cli/cli.git",
    "flipt-io/flipt": "https://github.com/flipt-io/flipt.git",
    "ponylang/ponyc": "https://github.com/ponylang/ponyc.git",
    "clap-rs/clap": "https://github.com/clap-rs/clap.git",
}


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def _snapshot(root: Path, repo: str, commit: str) -> tuple[Path, bool]:
    if repo not in REMOTES or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"Unexpected repository or commit: {repo}@{commit}")
    name = repo.replace("/", "__")
    bare = root / "git" / f"{name}.git"
    tree = root / "worktrees" / name / commit
    bare.parent.mkdir(parents=True, exist_ok=True)
    tree.parent.mkdir(parents=True, exist_ok=True)
    if not bare.exists():
        _git("init", "--bare", str(bare))
        _git("--git-dir", str(bare), "remote", "add", "origin", REMOTES[repo])
    elif (_git("--git-dir", str(bare), "remote", "get-url", "origin")
          .stdout.strip() != REMOTES[repo]):
        raise ValueError(f"Unexpected remote for {bare}")
    if tree.exists():
        actual = _git("-C", str(tree), "rev-parse", "HEAD").stdout.strip()
        if actual != commit:
            raise ValueError(f"Existing worktree has wrong commit: {tree}")
        return tree, True
    if _git("--git-dir", str(bare), "cat-file", "-e", f"{commit}^{{commit}}",
            check=False).returncode != 0:
        _git("--git-dir", str(bare), "fetch", "--filter=blob:none", "--depth=1",
             "origin", commit)
    _git("--git-dir", str(bare), "worktree", "add", "--quiet", "--detach",
         str(tree), commit)
    return tree, False


def _check_gold(tree: Path, gold: list[dict]) -> tuple[list[str], list[str]]:
    errors, clipped = [], []
    lines_by_path: dict[str, list[str]] = {}
    for span in gold:
        path = span["path"]
        source = (tree / path).resolve()
        if not source.is_relative_to(tree.resolve()) or not source.is_file():
            errors.append(f"Missing or unsafe gold file: {path}")
            continue
        if path not in lines_by_path:
            try:
                lines_by_path[path] = source.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError) as exc:
                errors.append(f"Unreadable gold file {path}: {exc}")
                continue
        line_count = len(lines_by_path[path])
        if span["start_line"] > line_count:
            errors.append(f"Gold start {span['start_line']} exceeds {path}"
                          f" ({line_count} lines)")
        elif span["end_line"] > line_count:
            # Match ContextBench's line_to_byte behavior: an overlong end clips at EOF.
            clipped.append(f"Gold end {span['end_line']} clips to {line_count} in {path}")
    return errors, clipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=RESULTS / "contextbench-snapshots.json")
    args = parser.parse_args()
    cases = json.loads(args.holdout.read_text(encoding="utf-8"))["cases"]
    if args.limit:
        cases = cases[:args.limit]
    rows = []
    for case in cases:
        started = time.perf_counter()
        tree, warm = _snapshot(args.root.resolve(), case["repo"], case["base_commit"])
        setup_ms = (time.perf_counter() - started) * 1000
        errors, clipped = _check_gold(tree, case["gold"])
        rows.append({
            "id": case["id"], "repo": case["repo"], "base_commit": case["base_commit"],
            "root": str(tree), "warm": warm, "setup_ms": setup_ms,
            "gold_files": len({span["path"] for span in case["gold"]}),
            "gold_spans": len(case["gold"]), "clipped": clipped, "errors": errors,
        })
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_suffix(args.out.suffix + ".tmp")
        temporary.write_text(json.dumps({"cases": rows}, indent=2), encoding="utf-8")
        temporary.replace(args.out)
        print(f"{len(rows)}/{len(cases)} {case['repo']} {case['id']}: "
              f"{'verified' if not errors else f'{len(errors)} errors'}", flush=True)
    return int(any(row["errors"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
