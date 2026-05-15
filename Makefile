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
# Daemon (twmux watch) \
DAEMON: ## ############################################################

# All watch-* targets work under both startup modes:
#   * launchd  — when ~/Library/LaunchAgents/$(WATCH_LABEL).plist exists AND
#                the service is currently registered. Native launchctl is used.
#   * fallback — plain `twmux watch …` commands, suitable for tmux `run-shell`
#                or ad-hoc foreground runs.
# Detection happens inside each recipe so a user can install/remove the plist
# without re-editing the Makefile.

WATCH_LABEL  := dev.sysid.twmux-watch
WATCH_DOMAIN := gui/$(shell id -u)
WATCH_PLIST  := $(HOME)/Library/LaunchAgents/$(WATCH_LABEL).plist
WATCH_LOG    := $(HOME)/.cache/twmux/watch.log
WATCH_PID    := $(HOME)/.cache/twmux/watch.pid

.PHONY: watch-start
watch-start:  ## Start watch daemon (launchd if plist installed, else foreground)
	@if [ -f $(WATCH_PLIST) ]; then \
	    echo ">> launchctl bootstrap $(WATCH_DOMAIN) $(WATCH_PLIST)"; \
	    launchctl bootstrap $(WATCH_DOMAIN) $(WATCH_PLIST) 2>&1 || \
	      echo "  (already loaded — use 'make watch-restart' to pick up changes)"; \
	  else \
	    echo ">> twmux watch daemon --ensure-running &"; \
	    twmux watch daemon --ensure-running >/dev/null 2>&1 & \
	  fi

.PHONY: watch-stop
watch-stop:  ## Stop watch daemon (launchctl bootout under launchd, else SIGTERM)
	@if [ -f $(WATCH_PLIST) ] && launchctl print $(WATCH_DOMAIN)/$(WATCH_LABEL) >/dev/null 2>&1; then \
	    echo ">> launchctl bootout $(WATCH_DOMAIN)/$(WATCH_LABEL)"; \
	    launchctl bootout $(WATCH_DOMAIN)/$(WATCH_LABEL); \
	  else \
	    echo ">> twmux watch stop"; \
	    twmux watch stop; \
	  fi

.PHONY: watch-restart
watch-restart:  ## Restart watch daemon (fast — picks up source edits)
	@if [ -f $(WATCH_PLIST) ] && launchctl print $(WATCH_DOMAIN)/$(WATCH_LABEL) >/dev/null 2>&1; then \
	    echo ">> launchctl kickstart -k $(WATCH_DOMAIN)/$(WATCH_LABEL)"; \
	    launchctl kickstart -k $(WATCH_DOMAIN)/$(WATCH_LABEL); \
	  else \
	    echo ">> twmux watch stop && twmux watch daemon --ensure-running &"; \
	    twmux watch stop >/dev/null 2>&1 || true; \
	    twmux watch daemon --ensure-running >/dev/null 2>&1 & \
	  fi

.PHONY: watch-status
watch-status:  ## Show whether the daemon is running (process, launchd, pid file)
	@echo "[process]"
	@pgrep -lf 'twmux watch daemon' | sed 's/^/  /' || echo "  no process"
	@echo "[launchd]"
	@launchctl list 2>/dev/null | grep $(WATCH_LABEL) | sed 's/^/  /' || echo "  not registered"
	@echo "[pid file]"
	@if [ -f $(WATCH_PID) ]; then echo "  $(WATCH_PID): $$(cat $(WATCH_PID))"; else echo "  none"; fi
	@echo "[tsv]"
	@if [ -f $(HOME)/.cache/twmux/agents.tsv ]; then \
	    echo "  rows: $$(wc -l < $(HOME)/.cache/twmux/agents.tsv | tr -d ' ')"; \
	  else echo "  none"; fi

.PHONY: watch-logs
watch-logs:  ## Tail the daemon log (Ctrl-C to exit)
	@tail -F $(WATCH_LOG)

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
