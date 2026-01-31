.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

VERSION   = $(shell cat VERSION 2>/dev/null || echo "0.1.0")
pkg_src   = src/twmux
tests_src = tests

.PHONY: all
all: clean build publish  ## Build and publish

################################################################################
# Development \
DEVELOPMENT:  ## ############################################################

.PHONY: install-dep
install-dep:  ## Install dependencies
	uv sync --extra dev

################################################################################
# Testing \
TESTING:  ## ############################################################

.PHONY: test
test:  ## Run tests
	uv run pytest

################################################################################
# Code Quality \
QUALITY: ## ############################################################

.PHONY: lint
lint:  ## Check style with ruff
	uv run ruff check $(pkg_src) $(tests_src)
	uv run ruff format --check $(pkg_src) $(tests_src)

.PHONY: lint-fix
lint-fix:  ## Autofix linter findings
	uv run ruff check --fix $(pkg_src) $(tests_src)

.PHONY: format
format:  ## Format code with ruff
	uv run ruff format $(pkg_src) $(tests_src)

.PHONY: ty
ty:  ## Check type hints
	@uvx ty check $(pkg_src)

.PHONY: static-analysis
static-analysis: lint-fix format ty  ## Run all static analysis

.PHONY: check
check: lint test  ## Run lint and test

################################################################################
# Building, Deploying \
BUILDING:  ## ############################################################

.PHONY: build
build: clean  ## Build package
	uv run python -m build

.PHONY: publish
publish:  ## Upload to PyPI
	uv run twine upload --verbose dist/*

.PHONY: install
install: uninstall  ## Install via uv tool
	uv tool install -e .
	twmux --install-completion bash

.PHONY: uninstall
uninstall:  ## Uninstall via uv tool
	-uv tool uninstall twmux

.PHONY: bump-patch
bump-patch: check-github-token  ## Bump patch, tag, push, release
	bump-my-version bump --commit --tag patch
	git push && git push --tags
	@$(MAKE) create-release

.PHONY: bump-minor
bump-minor: check-github-token  ## Bump minor, tag, push, release
	bump-my-version bump --commit --tag minor
	git push && git push --tags
	@$(MAKE) create-release

.PHONY: bump-major
bump-major: check-github-token  ## Bump major, tag, push, release
	bump-my-version bump --commit --tag major
	git push && git push --tags
	@$(MAKE) create-release

.PHONY: create-release
create-release: check-github-token  ## Create GitHub release
	gh release create "v$(VERSION)" --generate-notes

.PHONY: check-github-token
check-github-token:
	@if [ -z "$$GITHUB_TOKEN" ]; then echo "GITHUB_TOKEN not set"; exit 1; fi

################################################################################
# Clean \
CLEAN:  ## ############################################################

.PHONY: clean
clean: clean-build clean-pyc  ## Remove all build artifacts

.PHONY: clean-build
clean-build:  ## Remove build artifacts
	rm -rf build/ dist/ .eggs/
	find . \( -path ./env -o -path ./venv -o -path ./.env -o -path ./.venv \) -prune -o -name '*.egg-info' -exec rm -rf {} +

.PHONY: clean-pyc
clean-pyc:  ## Remove Python file artifacts
	rm -rf .pytest_cache .ruff_cache .coverage
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -rf {} +

################################################################################
# Help \
HELP:  ## ############################################################

define PRINT_HELP_PYSCRIPT
import re, sys
for line in sys.stdin:
	match = re.match(r'^([a-zA-Z0-9_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("\033[36m%-20s\033[0m %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT

.PHONY: help
help:
	@python -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)
