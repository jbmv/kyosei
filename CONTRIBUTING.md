# Contributing to Kyosei

Contributions are made by pull request from a fork. A small fix can go straight to a pull
request. For a larger change, please open an issue first, because the store's behaviour is
governed by the laws in [docs/design/charter.md](docs/design/charter.md) and a change that
conflicts with one will need discussion.

Kyosei has one maintainer, who also commits directly to `main`. A branch may therefore need
rebasing before it can merge. The history of `main` is not rewritten.

## Setting up and testing

Kyosei needs Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```sh
make venv    # provision .venv and run nothing else
make lint    # ruff
make test    # the Python suite, in parallel across all cores
```

Provision with `make venv` rather than installing by hand. [AGENTS.md](AGENTS.md) explains why,
and is the fuller guide for a person or an agent working on this repository.

The desktop app under `apps/desktop/` is a separate Electron project with its own
[README](apps/desktop/README.md) and checks. It has been run only on macOS.

## What a change needs

- `make lint` and `make test` pass.
- A new mechanism comes with a test that fails if the mechanism is removed.
- An error message that names a remedy comes with a test showing that the remedy clears the
  error.
- A new stored field comes with the code that reads it.
- A schema change bumps `schema_version` through the compatibility code and adds rebuild
  defaults for the prior export format. `tests/test_schema_compat.py` fails otherwise.
- A change to the command-line interface regenerates the command reference:
  `.venv/bin/python -m kyosei reference --output docs/reference/cli.md`.

Please keep each pull request to one change, and describe in it what changes and why.

## Record citations

Source comments, tests and documents cite records as `KYO-n`, sometimes with a suffix such as
`.c2`. These are ids in the private tracker in which Kyosei's own development is recorded. A
contributor needs no access to it: the tests build their own temporary projects, and
`kyo changelog --all` lists the completed records with their titles.

Please do not invent a `KYO-n` for new work. State the reason for the code in the comment
itself. Proposals and other planning documents are kept in the tracker, and a test fails if
anything is tracked under `docs/proposals/`.

## License

Kyosei is released under the [Apache License 2.0](LICENSE). Under section 5 of that license, a
contribution submitted for inclusion is licensed on the same terms unless you state otherwise.
