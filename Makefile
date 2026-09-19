# OrchestraOS — install / doctor / run. `make help` lists targets.
SHELL := /bin/bash
ORCHESTRA := ./bin/orchestra
PREFIX ?= $(HOME)/.local

.PHONY: help init doctor up up-detached down status test install

help:
	@echo "make init      create data dir + orchestra.toml + venv + npm installs + builds (idempotent)"
	@echo "make doctor    check CLIs+auth, ports, tmux, config keys, builds, rotation beat (exit 1 on MISSING)"
	@echo "make up        run gateway + api + dashboard + arturo + beats under one supervisor (foreground)"
	@echo "make up-detached / make down / make status"
	@echo "make test      python test suites (per package, like CI)"
	@echo "make install   symlink bin/orchestra into $(PREFIX)/bin"

init:
	$(ORCHESTRA) init

doctor:
	$(ORCHESTRA) doctor

up:
	$(ORCHESTRA) up

up-detached:
	$(ORCHESTRA) up --detach

down:
	$(ORCHESTRA) down

status:
	$(ORCHESTRA) status

# `orchestra init` creates .venv and pip-installs requirements.txt (pytest lives THERE, not
# in system python). Use the venv when it exists so `make test` works on a bare box after
# `make init`; fall back to python3 so CI, which installs pytest system-wide, is unchanged.
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

test:
	$(PY) -m pytest -q orchestra_cli/tests
	$(PY) -m pytest -q test_router_prefixes.py test_msg_threading.py --ignore=scripts
	$(PY) -m pytest -q services/arturo
	$(PY) -m pytest -q scripts --ignore=scripts/lineage_daemon --ignore=scripts/identity_store --ignore=scripts/focus_registry
# cpu_measure_test is deselected from the DEFAULT suite and lives in `make test-perf` (below).
# Why (gm-g73 ruling, 2026-09-19): its RELATIVE guard — "one-scan fanout must be materially cheaper
# than the per-agent scan" (ms/tick ratio) — asserts unconditionally; its own docstring calls the
# relative/structural guards "load-invariant", but on a busy host the ratio collapsed (0.55 vs 0.80
# ms/tick) and it failed 3 of 5 runs in isolation at load 31 while passing everywhere quiet. A ratio
# of milliseconds under uncontrolled load tests the HOST, not the product. `_high_load()` gates only
# the absolute <1% CPU budget. Post-flip fix (do not touch the test before the tag): gate the relative
# assertion on `_high_load()` too, or widen its tolerance / report instead of assert.
	$(PY) -m pytest -q scripts/lineage_daemon --deselect scripts/lineage_daemon/realtime/cpu_measure_test.py::test_steady_state_cpu_under_one_percent_on_real_proc
	$(PY) -m pytest -q scripts/identity_store
	$(PY) -m pytest -q scripts/focus_registry
	$(PY) -m pytest -q contract

# Perf-ratio tests: they assume a QUIET host (see the comment in `test`). Run them deliberately.
test-perf:
	$(PY) -m pytest -q scripts/lineage_daemon/realtime/cpu_measure_test.py

install:
	mkdir -p $(PREFIX)/bin && ln -sf $(CURDIR)/bin/orchestra $(PREFIX)/bin/orchestra && echo "installed $(PREFIX)/bin/orchestra"
	@case ":$$PATH:" in *":$(PREFIX)/bin:"*) ;; *) \
	  echo ""; \
	  echo "NOTE: $(PREFIX)/bin is not on your PATH in this shell, so \`orchestra\` will not be found yet."; \
	  echo "      (Ubuntu adds it at login only if it already existed, and it did not.) Either run:"; \
	  echo ""; \
	  echo '        export PATH="$$HOME/.local/bin:$$PATH"     # this shell; add to ~/.bashrc to keep it'; \
	  echo ""; \
	  echo "      or use ./bin/orchestra from the checkout instead."; \
	  echo "";; \
	esac
