# Repository setup

Fastindex is a public Python 3.11+ research-preview library and CLI. This setup pass
keeps that scope explicit rather than treating it as a production search service.

## Applied

- README with target user, problem statement, installation, five-minute quickstart,
  architecture, examples, retrieval benchmarks, limitations, contribution path, and
  license.
- MIT license and focused contribution guide retained.
- Code of conduct added.
- Minimal CI added for Ruff, pytest, sample-bundle lint, and package build.
- GitHub description and retrieval-focused topics refreshed.
- Repository social-preview artwork added at `docs/assets/social-preview.jpg`.
- `main` configured to require a pull request and the `quality` CI check; force pushes
  and deletion are blocked.

## Deliberately deferred

- PyPI publishing, release automation, and changelog automation: the project is still a
  research preview and no publication workflow was requested.
- A separate static type checker: the project had no existing mypy or Pyright baseline;
  adding a new dependency was outside this repository-presentation pass.
- Mandatory external approval: this is currently a single-maintainer repository; CI and
  pull-request review remain required without creating an approval deadlock.
- Production or asymptotic performance claims: the recorded evidence is one benchmark
  on one repository.

## Validation

```bash
uv run pytest -q
uv run ruff check src tests evals
uv run fastindex lint examples/sample-bundle
uv build
```
