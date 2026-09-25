# Directory-routing preprint package

The archived evaluation code and data are pinned at
[commit `50721e1`](https://github.com/manojbajaj95/fastindex/tree/50721e114e37d908648bc5b30a0c63ac718d776e).

The manuscript is [`main.tex`](main.tex), with
[`references.bib`](references.bib). A readable version is
[`manuscript.md`](manuscript.md). The ranked outputs, case ledger,
annotation audit, and per-case scores are in [`data/`](data/README.md).
The original 82-case paired evaluation and its reduced-budget rule are in
[`study-amendment-001.md`](study-amendment-001.md). Review-driven controls
are explicitly post-study and documented in
[`study-amendment-002.md`](study-amendment-002.md). The
[`selector examples`](selector-examples.md) show actual input/output ranges.

From the repository root, `sh paper/reproduce.sh` regenerates the audited
cohort and re-scores archived rankings without model calls. It downloads the
pinned public dataset and source revisions into gitignored local paths.
The full stage commands are in the two amendments.

Build from this directory with `tectonic main.tex --outdir ../output/pdf`.
The rendered PDF is at
[`../output/pdf/evaluating-name-only-directory-routing.pdf`](../output/pdf/evaluating-name-only-directory-routing.pdf);
[`../output/pdf/arxiv-source.tar.gz`](../output/pdf/arxiv-source.tar.gz)
contains only `main.tex` and `references.bib` for an arXiv TeX upload.
The PDF is an empirical preprint, not evidence of peer review or arXiv
posting. [`arxiv-metadata.md`](arxiv-metadata.md) contains the draft upload
fields; the author must review the revised title and abstract in arXiv's
preview before submission.
