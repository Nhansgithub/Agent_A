# Developer gates — the same four checks CI runs, so a red build can be caught before it leaves
# your machine. `make check` is exactly what the pre-push hook runs.
#
# One-time: enable the pre-push hook so `git push` runs these automatically:
#   make hooks        (or: git config core.hooksPath .githooks)
#
# Paths default to the project venv; override on the command line if yours lives elsewhere:
#   make check PY=python3 RUFF=ruff LINT=lint-imports

# NB: use IMPORTS, not LINT — `LINT` is a built-in Make variable (defaults to the `lint` program),
# so `LINT ?= ...` would NOT override it and `make` would run the wrong tool.
PY      ?= .venv/bin/python
RUFF    ?= .venv/bin/ruff
IMPORTS ?= .venv/bin/lint-imports

.PHONY: check lint test format hooks

check: lint test ## Run every CI gate locally (lint + format + contracts + tests)

lint: ## ruff lint, ruff format check, and the import-linter architecture contracts
	$(RUFF) check .
	$(RUFF) format --check .
	$(IMPORTS)

test: ## The offline test suite (no network, no credentials)
	$(PY) -m pytest -q

format: ## Auto-fix: apply ruff formatting and safe lint fixes
	$(RUFF) format .
	$(RUFF) check --fix .

hooks: ## Enable the committed git hooks (pre-push runs `make check`)
	git config core.hooksPath .githooks
	@echo "pre-push hook enabled (core.hooksPath -> .githooks)"
