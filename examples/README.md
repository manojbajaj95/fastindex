# Examples

CLI demos use `examples/sample-bundle`, a symlink to the shared lab wiki at
`evals/fixtures/sample-bundle`.

Gold queries stay under `evals/fixtures/queries/`. If demo content and
measurement gold ever need to diverge, replace the symlink with a slim
standalone demo bundle.

## Demo GIF

`tree_reason_gif.py` renders the tree-reason walk animation used in the
README (`docs/assets/tree-reason.gif`):

```bash
uv run --with matplotlib --with networkx --with pillow \
  python examples/tree_reason_gif.py
cp examples/tree-reason.gif docs/assets/tree-reason.gif
```
