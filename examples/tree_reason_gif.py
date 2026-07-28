#!/usr/bin/env python3
"""Generate a GIF demo of tree-reason: gated descent, prune, span collection.

No top_k / ranking — just the walk (including parallel branch opens). Run:

  uv run --with matplotlib --with networkx --with pillow \\
    python examples/tree_reason_gif.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from matplotlib.patches import Circle
from PIL import Image

OUT = Path(__file__).with_name("tree-reason.gif")
BEAT_MS = 1100

NODES = [
    "root",
    "data",
    "ops",
    "wh",
    "tables",
    "datasets",
    "orders",
    "customers",
    "pos",
    "kitchen",
    "vendors",
    "recipes",
    "espresso",
    "alpine",
]

EDGES = [
    ("root", "data"),
    ("root", "ops"),
    ("data", "wh"),
    ("wh", "tables"),
    ("wh", "datasets"),
    ("tables", "orders"),
    ("tables", "customers"),
    ("datasets", "pos"),
    ("ops", "kitchen"),
    ("ops", "vendors"),
    ("kitchen", "recipes"),
    ("recipes", "espresso"),
    ("vendors", "alpine"),
]

COLORS = {
    "idle": "#C5C9D1",
    "reviewing": "#EBCB8B",
    "opened": "#5E9BB5",
    "pruned": "#D8DEE9",
    "span": "#7FA86A",
}
EDGE_IDLE = "#9AA3B2"
EDGE_ACTIVE = "#5E9BB5"
EDGE_PRUNED = "#D0D5DE"
BG = "#FFFFFF"
MUTED = "#4C566A"
RADIUS = 0.26

# Both root children stay active so the walk stays balanced L/R.
BEATS: list[dict] = [
    {
        "caption": "Start",
        "reviewing": set(),
        "opened": set(),
        "pruned": set(),
        "spans": set(),
    },
    {
        "caption": "Gate at root",
        "reviewing": {"root"},
        "opened": set(),
        "pruned": set(),
        "spans": set(),
    },
    {
        "caption": "Parallel open — both sides",
        "reviewing": {"root"},
        "opened": {"data", "ops"},
        "pruned": set(),
        "spans": set(),
    },
    {
        "caption": "Parallel gates on both sides",
        "reviewing": {"data", "ops"},
        "opened": {"data", "ops"},
        "pruned": set(),
        "spans": set(),
    },
    {
        "caption": "Descend both sides",
        "reviewing": {"data", "ops"},
        "opened": {"data", "ops", "wh", "kitchen", "vendors"},
        "pruned": set(),
        "spans": set(),
    },
    {
        "caption": "Parallel gates — deeper",
        "reviewing": {"wh", "kitchen", "vendors"},
        "opened": {"data", "ops", "wh", "kitchen", "vendors"},
        "pruned": set(),
        "spans": set(),
    },
    {
        "caption": "Open promising children · prune siblings",
        "reviewing": {"wh", "kitchen"},
        "opened": {"data", "ops", "wh", "kitchen", "tables", "recipes"},
        "pruned": {"datasets", "pos", "vendors", "alpine"},
        "spans": set(),
    },
    {
        "caption": "Parallel gates on both paths",
        "reviewing": {"tables", "recipes"},
        "opened": {"data", "ops", "wh", "kitchen", "tables", "recipes"},
        "pruned": {"datasets", "pos", "vendors", "alpine"},
        "spans": set(),
    },
    {
        "caption": "Open leaves on both paths · prune a sibling",
        "reviewing": {"tables", "recipes"},
        "opened": {
            "data",
            "ops",
            "wh",
            "kitchen",
            "tables",
            "recipes",
            "orders",
            "espresso",
        },
        "pruned": {"datasets", "pos", "vendors", "alpine", "customers"},
        "spans": set(),
    },
    {
        "caption": "Parallel section gates",
        "reviewing": {"orders", "espresso"},
        "opened": {
            "data",
            "ops",
            "wh",
            "kitchen",
            "tables",
            "recipes",
            "orders",
            "espresso",
        },
        "pruned": {"datasets", "pos", "vendors", "alpine", "customers"},
        "spans": set(),
    },
    {
        "caption": "Spans from both sides",
        "reviewing": set(),
        "opened": {
            "data",
            "ops",
            "wh",
            "kitchen",
            "tables",
            "recipes",
            "orders",
            "espresso",
        },
        "pruned": {"datasets", "pos", "vendors", "alpine", "customers"},
        "spans": {"orders", "espresso"},
    },
    {
        "caption": "Context found without reading the full tree",
        "reviewing": set(),
        "opened": {
            "data",
            "ops",
            "wh",
            "kitchen",
            "tables",
            "recipes",
            "orders",
            "espresso",
        },
        "pruned": {"datasets", "pos", "vendors", "alpine", "customers"},
        "spans": {"orders", "espresso"},
    },
]

LEGEND = [
    ("reviewing", "reviewing"),
    ("opened", "opened"),
    ("pruned", "pruned"),
    ("span", "span"),
]


def _as_set(value: object) -> set:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return set(value)


def _layered_pos(g: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Tidy tree layout: children centered under parents, siblings spaced wide."""
    levels: dict[str, int] = {}
    for node in nx.topological_sort(g):
        preds = list(g.predecessors(node))
        levels[node] = 0 if not preds else max(levels[p] for p in preds) + 1

    order = {n: i for i, n in enumerate(NODES)}
    gap = 2.4  # minimum horizontal gap between sibling subtrees

    def subtree_width(nid: str) -> float:
        kids = sorted(g.successors(nid), key=lambda n: order[n])
        if not kids:
            return gap
        return max(gap, sum(subtree_width(k) for k in kids))

    pos: dict[str, tuple[float, float]] = {}

    def place(nid: str, left: float) -> float:
        """Place nid's subtree starting at left; return right edge."""
        w = subtree_width(nid)
        kids = sorted(g.successors(nid), key=lambda n: order[n])
        if not kids:
            x = left + w / 2
            pos[nid] = (x, -levels[nid] * 1.05)
            return left + w
        cursor = left
        child_xs: list[float] = []
        for k in kids:
            kw = subtree_width(k)
            place(k, cursor)
            child_xs.append(pos[k][0])
            cursor += kw
        x = sum(child_xs) / len(child_xs)
        pos[nid] = (x, -levels[nid] * 1.05)
        return left + w

    place("root", 0.0)
    # Center around x=0, then stretch horizontally for a wider silhouette.
    mid = (min(x for x, _ in pos.values()) + max(x for x, _ in pos.values())) / 2
    return {n: ((x - mid) * 1.15, y) for n, (x, y) in pos.items()}


def _node_state(nid: str, beat: dict) -> str:
    reviewing = _as_set(beat["reviewing"])
    if nid in beat["spans"]:
        return "span"
    if nid in reviewing:
        return "reviewing"
    if nid in beat["pruned"]:
        return "pruned"
    if nid in beat["opened"]:
        return "opened"
    return "idle"


def _edge_style(u: str, v: str, beat: dict) -> tuple[str, float]:
    reviewing = _as_set(beat["reviewing"])
    if v in beat["pruned"] or u in beat["pruned"]:
        return EDGE_PRUNED, 0.7
    if v in beat["opened"] or v in beat["spans"] or v in reviewing:
        return EDGE_ACTIVE, 1.8
    return EDGE_IDLE, 1.0


def main() -> None:
    g = nx.DiGraph()
    g.add_nodes_from(NODES)
    g.add_edges_from(EDGES)
    pos = _layered_pos(g)
    ys = [y for _, y in pos.values()]
    leaf_y, top_y = min(ys), max(ys)

    fig = plt.figure(figsize=(11.0, 8.0), dpi=120)
    fig.patch.set_facecolor(BG)
    # Tree-only axes; caption + legend sit in the figure margin below.
    ax = fig.add_axes((0.04, 0.18, 0.92, 0.78))
    ax.set_facecolor(BG)
    caption_artist = fig.text(
        0.5,
        0.11,
        "",
        ha="center",
        va="center",
        color=MUTED,
        fontsize=12,
        fontfamily="sans-serif",
    )
    leg = fig.add_axes((0.12, 0.025, 0.76, 0.055))
    leg.set_facecolor(BG)
    leg.set_xlim(0, 4)
    leg.set_ylim(0, 1)
    leg.axis("off")
    for i, (key, name) in enumerate(LEGEND):
        leg.scatter([i + 0.2], [0.5], s=40, c=COLORS[key], zorder=4)
        leg.text(
            i + 0.35,
            0.5,
            name,
            va="center",
            color=MUTED,
            fontsize=9,
            fontfamily="sans-serif",
        )

    def draw(beat: dict) -> Image.Image:
        ax.clear()
        ax.set_facecolor(BG)
        xs = [x for x, _ in pos.values()]
        ax.set_xlim(min(xs) - 0.6, max(xs) + 0.6)
        pad = RADIUS * 2.8
        ax.set_ylim(leaf_y - pad, top_y + pad)
        ax.set_aspect("equal")
        ax.axis("off")

        # Edges run center-to-center; nodes draw on top.
        for u, v in g.edges():
            color, lw = _edge_style(u, v, beat)
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            ax.plot(
                [x0, x1],
                [y0, y1],
                color=color,
                lw=lw,
                zorder=1,
                solid_capstyle="round",
            )

        for nid in NODES:
            state = _node_state(nid, beat)
            x, y = pos[nid]
            alpha = 0.55 if state == "pruned" else 1.0
            edge_c = "#2E3440" if state == "reviewing" else "#FFFFFF"
            lw = 2.0 if state == "reviewing" else 0.8
            ax.add_patch(
                Circle(
                    (x, y),
                    RADIUS,
                    facecolor=COLORS[state],
                    edgecolor=edge_c,
                    linewidth=lw,
                    alpha=alpha,
                    zorder=2,
                )
            )

        caption_artist.set_text(beat["caption"])
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())
        return Image.fromarray(buf).convert("P", palette=Image.Palette.ADAPTIVE)

    images = [draw(beat) for beat in BEATS]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        OUT,
        save_all=True,
        append_images=images[1:],
        duration=BEAT_MS,
        loop=0,
        optimize=False,
    )
    plt.close(fig)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB, {len(images)} beats @ {BEAT_MS}ms)")


if __name__ == "__main__":
    main()
