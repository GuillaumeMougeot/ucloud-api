# Contributing

```bash
git clone https://github.com/GuillaumeMougeot/ucloud-api
cd ucloud-api
uv sync                  # venv + dependencies
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # strict type checking
uv run --group docs mkdocs serve   # docs at http://127.0.0.1:8000
pre-commit install       # optional: run checks on every commit
```

All of these run in CI on every push; `mkdocs build --strict` fails the build
on any broken internal link.

## The one thing you can't guess

**The test suite fakes the UCloud API.** Every test runs offline against
recorded shapes, so a green suite proves the code is internally consistent —
it does *not* prove the API contract still holds. The endpoints, payloads and
quirks in this codebase (the `WEBSOCKET_V2` upload framing, the `Project`
header semantics, 400-instead-of-404 on missing parents, `SUCCESS` on
terminated batch jobs, …) were established by running against the live
deployment at `cloud.sdu.dk`.

Practically:

- If you change anything that touches a request or response shape, verify it
  against a live deployment once. The cheapest jobs cost fractions of a
  core-hour (`cpu-amd-zen5-1-vcpu` with a `run = "echo ok"` batch spec).
- If the live API disagrees with the code, trust the API and update the code
  *and* the fakes together.

## Ground rules

- Never commit credentials: `.env`, `credentials.json`, `token_cache.json` are
  git-ignored — keep them that way, and check `git status` before committing.
- New behavior needs a test that fails without the change.
- The docs follow [Diátaxis](https://diataxis.fr/): tutorials teach, guides
  solve tasks, the CLI reference describes, How-it-works explains. Put new
  pages where they belong in that split.
