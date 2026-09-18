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

test:
	python3 -m pytest -q orchestra_cli/tests
	python3 -m pytest -q test_router_prefixes.py test_msg_threading.py --ignore=scripts
	python3 -m pytest -q services/arturo
	python3 -m pytest -q scripts --ignore=scripts/lineage_daemon --ignore=scripts/identity_store --ignore=scripts/focus_registry
	python3 -m pytest -q scripts/lineage_daemon
	python3 -m pytest -q scripts/identity_store
	python3 -m pytest -q scripts/focus_registry
	python3 -m pytest -q contract

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
