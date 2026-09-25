# arXiv submission metadata

Use these draft fields when Manoj Bajaj uploads the TeX source package.
The author approved the earlier title and abstract on September 24, 2026;
the revision below incorporates the post-review experiment and needs the
author's final check in arXiv's preview. No arXiv upload or submission has
been made.

- Title: Evaluating Name-Only Directory Routing for One-Shot Code Search
- Author: Manoj Bajaj
- Affiliation: none; omit the affiliation field
- Public contact address: not provided; omit any optional public contact field
- Primary category: cs.SE (Software Engineering)
- Distribution license: Creative Commons Attribution 4.0 International (CC BY 4.0)
- Comments: 9 pages, 5 tables, 3 figures; code and data at the URL in the paper
- Journal reference and DOI: leave blank (unpublished preprint)
- Upload: `output/pdf/arxiv-tex-upload.zip` (TeX source and bibliography)

Abstract (plain-text metadata):

> Coding agents must find a small set of relevant files in large
> repositories. Lexical tools are fast but may rank incidental word matches
> above the files needed to understand an issue. Repository folders and file
> names provide a different signal: a model can navigate this hierarchy and
> nominate candidate files without an embedding index. We study when
> model-guided directory routing retrieves annotated code files that
> fixed-query lexical rankings miss, and whether their combination improves
> evidence delivered under a fixed context budget. We compare path-name tree
> routing with actual rg, FTS5, and equal-budget fusion on 82 audited issues
> from 11 repositories at pinned pre-fix commits. At eight files, tree
> routing recovered 0.465 of annotated gold files on average, versus 0.352
> for FTS5 and 0.245 for a fixed full-issue rg query. The paired tree--FTS5
> difference was 0.113 (95% repository-cluster bootstrap interval
> 0.053--0.168). Tree found 61 gold-file occurrences absent from FTS5's top
> eight across 40 issues. Fusion improved recall over FTS5 to 0.491, but its
> 0.026 gain over tree alone had an interval crossing zero; at 16 files,
> fusion recovered fewer gold files than tree. Under a shared 16K-token
> context selector, tree delivered 0.443 of annotated lines versus 0.246 for
> FTS5 on the 55 cases whose annotation text aligns with pre-fix source. A
> post-review flat path tournament using the same model reached 0.572 file
> recall@8, above tree, but used 24.6 versus 8.9 model calls per issue. Tree
> routing averaged 8.9 seconds and 12.2K input tokens per issue, versus a
> 0.9-second FTS5 build and 7-millisecond query. At equal candidate budgets,
> the evaluated tree router thus finds more annotated files and aligned lines
> than the selected one-shot lexical baselines while using less model
> computation than the higher-recall flat path control. The comparison does
> not isolate a quality effect of hierarchy or establish superiority over
> an adaptive coding agent.

The author must confirm the final title, abstract, category, and license in
arXiv's upload preview. Endorsement, if required for the account or category,
is separate from manuscript quality.
