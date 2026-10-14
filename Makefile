PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
STAMP  := $(VENV)/.install-stamp

.PHONY: all venv test lint fmt doctor clean logged install-tool release

all: lint test

$(STAMP): pyproject.toml
	@if command -v uv >/dev/null 2>&1; then \
		uv venv --python 3.12 $(VENV); \
		VIRTUAL_ENV=$(VENV) uv pip install --quiet -e ".[dev]"; \
	else \
		$(PYTHON) -m venv $(VENV); \
		$(BIN)/python -m pip install --quiet --upgrade pip; \
		$(BIN)/python -m pip install --quiet -e ".[dev]"; \
	fi
	@touch $(STAMP)

# KYO-193: a long run piped through `tail` reports tail's exit status and, because tail
# buffers, leaves an empty log when the run is interrupted. Three measurements were lost
# that way in one day. With LOG=<file> the whole run goes to the file, its last lines and
# the command's OWN status are printed, and make exits with that status. Without LOG the
# target behaves as it always did. `make logged CMD=... LOG=...` is the same macro exposed
# for tests/test_kyo193_logged_runs.py, which fails if the status stops propagating.
# KYO-297: provisioning, and nothing else. A dispatch worktree needs the venv before a
# worker can run one test file, and until this target existed the only documented way to get
# it was `make test` -- which provisions in under a second and then runs the whole suite for
# ten minutes, inside a worker whose prompt cache lives five. `worktree_setup` in
# kyosei/config.yml names this target; tests/test_kyo143_worktree_tooling.py fails if it goes
# back to one that runs the suite.
venv: $(STAMP)

LOG ?=
define logged
	@if [ -n "$(LOG)" ]; then \
		$(1) > "$(LOG)" 2>&1; s=$$?; \
		tail -n 10 "$(LOG)"; echo "exit=$$s  (whole run in $(LOG))"; exit $$s; \
	else \
		$(1); \
	fi
endef

# The suite runs across every core (pytest-xdist). Measured on an 18-core machine:
# 13m03s serially, 3m25s-3m36s with -n auto over three runs, all green. `make test JOBS=0` runs it
# serially, the way to rule parallelism out when a failure looks order-dependent.
JOBS ?= auto
test: $(STAMP)
	$(call logged,$(BIN)/python -m pytest -n $(JOBS))

lint: $(STAMP)
	$(call logged,$(BIN)/ruff check src tests tools)

logged:
	$(call logged,$(CMD))

fmt: $(STAMP)
	$(BIN)/ruff check --fix src tests tools

# Run the tool's own invariant checks against the project in the current directory.
doctor: $(STAMP)
	$(BIN)/kyo doctor

# KYO-446: installs this checkout as the machine's `kyo`, the one every hook in every
# project, every shell and the desktop app run. It is a non-editable snapshot, re-taken in
# seconds. Run it on `main` after every merge that writes a config key or bumps
# `schema_version`: until then that change blinds every hook in this repository (KYO-335),
# and `kyo doctor` / `kyo rebuild` name this target as the remedy. `env -u VIRTUAL_ENV` is
# KYO-297's lesson: an inherited VIRTUAL_ENV (an orchestrator's venv, an activated shell)
# must not steer the installer. No `-e`: an editable install tracks uncommitted edits
# mid-save (docs/reference/topics/install.md, "Install").
# tests/test_kyo335_path_build_lag.py fails if this recipe loses either property.
# KYO-549: one retry, as a mitigation only. A failed `--force` install can leave the tool
# half-removed (observed once: uv had already removed bin/ when lib/ refused to go,
# "Directory not empty"), so kyo is missing from PATH until an install succeeds. The cause
# -- kyo serve writing into the tool directory mid-removal -- is inferred, not reproduced,
# and nothing here claims the retry fixes it.
install-tool:
	@env -u VIRTUAL_ENV uv tool install --force . || { \
		echo "make install-tool: uv tool install --force failed; retrying once" >&2; \
		env -u VIRTUAL_ENV uv tool install --force . || { \
			echo "make install-tool: uv tool install --force failed twice; kyo may now be missing from PATH -- rerun \`make install-tool\` (or \`env -u VIRTUAL_ENV uv tool install --force .\` here)" >&2; \
			exit 1; }; }

# KYO-447: one command for a PyPI release, which refuses every precondition it can check rather than trusting a checklist:
#
#   make release TOKEN_FILE=path/to/pypi-token   # tag, build, verify, publish
#   make release DRY_RUN=1                       # the same, stopping before publish
#
# The order is: refuse (no version, pyproject/__version__ disagree, dirty tree, tag
# exists, no token file) -> tag v<version> -> build the WHEEL only (`uv build` alone
# makes an sdist, which the build backend refuses) with VIRTUAL_ENV unset (KYO-297) ->
# read the stamp out of the wheel and refuse unless it names HEAD, clean, a release, this
# version -> publish. The tag is taken BEFORE the build so the stamp records `release`
# (KYO-445), and it is deleted on every exit that published nothing -- a failed build, a
# stamp refusal, a failed publish, and a dry run, whose tag would otherwise make the next
# real release refuse "already tagged". The token is read from TOKEN_FILE into
# UV_PUBLISH_TOKEN, so it is on no command line and in no output; nothing is pushed.
# RELEASE_BUILD and PUBLISH_URL exist so tests/test_kyo447_release.py can run this in a
# throwaway repository against a fake build and a local index; the default publishes to PyPI.
TOKEN_FILE    ?=
DRY_RUN       ?=
PUBLISH_URL   ?=
RELEASE_DIST  ?= dist
RELEASE_BUILD ?= uv build --wheel --out-dir

release:
	@refuse() { echo "make release: refused -- $$*" >&2; exit 1; }; \
	v=$$(sed -n 's/^version = "\(.*\)"$$/\1/p' pyproject.toml | head -n 1); \
	pv=$$(sed -n 's/^__version__ = "\(.*\)"$$/\1/p' src/kyosei/__init__.py | head -n 1); \
	[ -n "$$v" ] || refuse "pyproject.toml sets no [project] version, so there is nothing to tag"; \
	[ "$$v" = "$$pv" ] || refuse "pyproject.toml's version is $$v but kyosei.__version__ (src/kyosei/__init__.py) is $$pv; the tag would be v$$v while the stamp's release check reads $$pv -- set both to the release number and commit"; \
	[ -z "$$(git status --porcelain)" ] || refuse "the working tree is not clean (git status --porcelain is not empty); a release is built from a committed tree -- commit or stash, then rerun"; \
	tag=v$$v; \
	if git rev-parse -q --verify "refs/tags/$$tag" >/dev/null; then \
		refuse "$$tag is already tagged (at $$(git rev-parse --short "$$tag^{commit}")); a version is released once -- set the next version in pyproject.toml and src/kyosei/__init__.py"; \
	fi; \
	if [ -z "$(DRY_RUN)" ]; then \
		[ -n "$(TOKEN_FILE)" ] || refuse "no TOKEN_FILE given; pass the path of a file holding the PyPI token (make release TOKEN_FILE=...), or DRY_RUN=1 to stop before publishing"; \
		[ -s "$(TOKEN_FILE)" ] || refuse "TOKEN_FILE $(TOKEN_FILE) is missing, unreadable or empty"; \
	fi; \
	head=$$(git rev-parse HEAD); \
	wheel="$(RELEASE_DIST)/kyosei-$$v-py3-none-any.whl"; \
	rm -f "$$wheel"; \
	git tag "$$tag" || refuse "could not create tag $$tag"; \
	echo "make release: tagged $$tag at $$head"; \
	untag() { git tag -d "$$tag" >/dev/null; echo "make release: refused -- $$*; deleted tag $$tag, nothing was published" >&2; exit 1; }; \
	env -u VIRTUAL_ENV $(RELEASE_BUILD) "$(RELEASE_DIST)" || untag "the wheel build failed"; \
	[ -f "$$wheel" ] || untag "the build produced no $$wheel"; \
	facts=$$(unzip -p "$$wheel" kyosei/_build_stamp.json | $(PYTHON) -c 'import json, sys; s = json.load(sys.stdin); print(s.get("commit"), s.get("dirty"), s.get("release"), s.get("version"))') \
		|| untag "$$wheel carries no readable kyosei/_build_stamp.json"; \
	set -- $$facts; \
	[ "$$1" = "$$head" ] || untag "the built stamp names commit $$1, not HEAD $$head"; \
	[ "$$2" = "False" ] || untag "the built stamp says dirty=$$2; the tree changed during the build"; \
	[ "$$4" = "$$v" ] || untag "the built stamp says version $$4, not $$v"; \
	[ "$$3" = "True" ] || untag "the built stamp says release=$$3, though HEAD carries $$tag"; \
	echo "make release: built $$wheel; stamp verified (commit $$head, clean, release, version $$v)"; \
	if [ -n "$(DRY_RUN)" ]; then \
		git tag -d "$$tag" >/dev/null; \
		echo "make release: DRY_RUN -- stopped before publishing; deleted tag $$tag; the wheel stays at $$wheel"; \
		exit 0; \
	fi; \
	UV_PUBLISH_TOKEN="$$(cat "$(TOKEN_FILE)")" env -u VIRTUAL_ENV uv publish $(if $(PUBLISH_URL),--publish-url "$(PUBLISH_URL)") "$$wheel" \
		|| untag "uv publish failed"; \
	echo "make release: published $$wheel; tag $$tag stays, local -- push it where the release is recorded"

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
