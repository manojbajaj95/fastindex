# arXiv handoff checklist

The intended primary category is **cs.SE**. The paper's author is
**Manoj Bajaj**. This checklist is for preparing a preprint, not a claim
of peer review or acceptance.

## Scientific gate

- [x] Complete the 82-case FTS5 and actual-ripgrep rankings paired with
  the already measured tree results; publish paired results and repository
  uncertainty, including losses and run failures.
- [x] Report the reduced-cohort audit (87 selected; five excluded),
  text-alignment sensitivity (55/82 fully aligned), and top-k ceilings.
- [x] Document the selector algorithm, no-gold-leakage boundary, actual
  input/output examples, Choice prompt/schema, branch ordering, retries,
  and model limitations in the PDF and code.
- [x] Add the 82-case ID/commit/query-hash ledger, 51-span mechanical
  mismatch audit, macro and micro aggregation, table captions, method
  diagram, repository plot, and Codex assistance disclosure.
- [x] Finish and archive the post-study flat Jev, path-only, basename, and
  two-search `rg` controls, with their costs and losses. Keep the frozen
  82-case primary comparison separate from this extension.
- [x] Report the measured tree calls/tokens/estimated cost, FTS5 build
  and query time, and actual `rg` search time. Do not claim end-to-end
  fusion latency, which is not measured in this reduced study.
- [x] Include repository/path-hint/cross-directory breakdowns and representative
  failures selected after aggregate analysis.
- [x] State the exact bounded claim: file-candidate complementarity to a
  fixed-query lexical ranking, not superiority over adaptive `grep` agents
  or demonstrated issue-resolution gains.
- [x] Reconstruct the reduced cohort and re-score the archived rankings
  without model calls; confirm all non-provenance study fields match.
- [x] Cross-check the tables, paired intervals, subgroup counts, operation
  means, cost calculation, and illustrative file ranks against the frozen
  JSON artifacts. The 57-in-37 three-way overlap is labeled descriptive.
- [x] Preflight the code/data release contents: `paper/data/` has no machine-local
  absolute paths, the arXiv source tar contains only TeX and bibliography,
  and `setup.md`, `.env`, external source snapshots, and `evals/results/`
  are untracked and gitignored. This does not replace review of a staged
  diff before publishing.
- [x] Deposit the completed code, selected-case manifest, protocol,
  analysis source, `paper/data/` rank outputs, and reproduction commands
  at a stable public commit on the `paper` branch before posting the paper.
  Do not publish API keys or private data; the gitignored source
  snapshots need not be uploaded.

## Submission package

- [x] Finalize the revised abstract and all tables from the archived
  original and post-study artifacts. No results placeholders may remain.
- [x] Build a portable LaTeX source package with the bibliography; compile
  it locally, render every page, and inspect text, tables, references,
  page breaks, and file names. The revised nine-page PDF was rendered and
  inspected; the two-file source tar also compiled independently.
  arXiv prefers TeX source and does not accept a PDF generated from TeX
  as a PDF-only submission.
- [x] Record the author's choice of CC BY 4.0 and no affiliation. No public
  contact address was provided, so omit the optional field. Manoj Bajaj
  approved the prior title, abstract, and cs.SE category on 2026-09-24; the revised upload
  fields are in
  [`arxiv-metadata.md`](arxiv-metadata.md).
- [ ] Verify the title, abstract, category, and metadata in arXiv's submission
  preview after a future upload. The local metadata approval does not replace
  this preview.
- [ ] Have the author submit through their own arXiv account. Registration
  is required; endorsement may be required for a new author/category.
  Preview arXiv's compiled PDF before final submission.

Official guidance: [arXiv Submission Overview](https://info.arxiv.org/help/submit/index.html)
and [Submit TeX/LaTeX](https://info.arxiv.org/help/submit_tex.html).
