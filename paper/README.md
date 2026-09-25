# Directory-routing preprint package

The archived evaluation code and data are pinned at
[commit `5759fc9f0d4c84ec33c5e14b4366e3680194825e`](https://github.com/manojbajaj95/fastindex/tree/5759fc9f0d4c84ec33c5e14b4366e3680194825e).

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
[`../output/pdf/arxiv-tex-upload.zip`](../output/pdf/arxiv-tex-upload.zip)
is the preferred arXiv upload. It contains only `main.tex` and
`references.bib`; a [tar.gz alternative](../output/pdf/arxiv-source.tar.gz)
contains the same files.
The PDF is an empirical preprint, not evidence of peer review or arXiv
posting. [`arxiv-metadata.md`](arxiv-metadata.md) contains the draft upload
fields; the author must review the revised title and abstract in arXiv's
preview before submission.
