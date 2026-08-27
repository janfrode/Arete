# ---------------------------------------------------------------------------
# Arete — incremental build
#
# Targets:
#   all                → dist/Arete.dmg  (default, contains both apps)
#   dist/Arete.dmg     → combined DMG (Arete + Logbook)
#   run                → build Arete.app if needed, then launch it
#   run-logbook        → build Logbook.app if needed, then launch it
#   timew              → build bundled timew binary only
#   clean              → remove build/, dist/, timew, .timew-*, Arete.icns, icon.png
#
# Override the Python interpreter:
#   make PYTHON=/path/to/python3
# ---------------------------------------------------------------------------

TIMEW_VERSION := 1.10.0
TIMEW_URL     := https://github.com/GothenburgBitFactory/timewarrior/releases/download/v$(TIMEW_VERSION)/timew-$(TIMEW_VERSION).tar.gz
PYENV_ROOT    ?= $(HOME)/.pyenv

VENV        := .venv
VENV_PYTHON := $(VENV)/bin/python3
VENV_PIP    := $(VENV)/bin/pip
CMAKE       := $(VENV)/bin/cmake

# Locate a Python 3.11+ interpreter at parse time (only runs once).
PYTHON_BIN := $(or \
    $(PYTHON), \
    $(shell for v in 3.13 3.12 3.11; do \
        p="/Library/Frameworks/Python.framework/Versions/$$v/bin/python$$v"; \
        [ -x "$$p" ] && echo "$$p" && break; \
    done), \
    $(shell for p in \
            $(PYENV_ROOT)/versions/3.13.*/bin/python3 \
            $(PYENV_ROOT)/versions/3.12.*/bin/python3 \
            $(PYENV_ROOT)/versions/3.11.*/bin/python3; do \
        [ -x "$$p" ] && echo "$$p" && break; \
    done) \
)

PY_SOURCES := arete.py timereport.py setup.py setup_logbook.py

# --------------------------------------------------------------------------
.PHONY: all run run-logbook timew clean
# timew is a convenience alias only — not a build input itself

all: dist/Arete.dmg

# --------------------------------------------------------------------------
# run — build the app bundle then launch it directly (skips DMG)
# --------------------------------------------------------------------------
run: dist/Arete.app/Contents/MacOS/Arete
	@echo "==> Launching Arete..."
	dist/Arete.app/Contents/MacOS/Arete

# --------------------------------------------------------------------------
# run-logbook — build the logbook app then launch it directly
# --------------------------------------------------------------------------
run-logbook: .logbook-built
	@echo "==> Launching Arête Logbook..."
	"dist/Arête Logbook.app/Contents/MacOS/Arête Logbook"

# --------------------------------------------------------------------------
# dist/Arete.dmg — combined DMG with both apps
# --------------------------------------------------------------------------
dist/Arete.dmg: dist/Arete.app/Contents/MacOS/Arete .logbook-built
	@echo "==> Assembling DMG..."
	rm -rf dist/dmg_temp
	mkdir -p dist/dmg_temp
	cp -R dist/Arete.app dist/dmg_temp/
	cp -R "dist/Arête Logbook.app" "dist/dmg_temp/Arête Logbook.app"
	ln -s /Applications dist/dmg_temp/Applications
	rm -f dist/Arete.dmg
	hdiutil create -volname "Arete" -srcfolder dist/dmg_temp -ov -format UDZO \
	    dist/Arete.dmg
	rm -rf dist/dmg_temp
	@echo "==> Done! dist/Arete.dmg"

# --------------------------------------------------------------------------
# dist/Arete.app — py2app bundle
# py2app always writes into dist/, so we only rm build/ not dist/ before
# rebuilding — that way dist/Arete.dmg's mtime is preserved and make won't
# redundantly re-assemble the DMG if only the app changed.
# --------------------------------------------------------------------------
dist/Arete.app/Contents/MacOS/Arete: $(VENV_PYTHON) Arete.icns .timew-$(TIMEW_VERSION) $(PY_SOURCES) version Changes.md
	@[ -s version ] || { echo "ERROR: version file is empty"; exit 1; }
	@echo "==> Building Arete.app with py2app..."
	rm -rf build dist/Arete.app
	$(VENV_PYTHON) setup.py py2app

# --------------------------------------------------------------------------
# dist/Arête Logbook.app — standalone logbook, py2app bundle
# Uses a stamp file (.logbook-built) as the make target because the real
# executable path contains an accent and a space, both of which break make
# target parsing.
# --------------------------------------------------------------------------
.logbook-built: $(VENV_PYTHON) Arete.icns .timew-$(TIMEW_VERSION) $(PY_SOURCES) version Changes.md
	@[ -s version ] || { echo "ERROR: version file is empty"; exit 1; }
	@echo "==> Building Arête Logbook.app with py2app..."
	rm -rf build "dist/Arête Logbook.app"
	$(VENV_PYTHON) setup_logbook.py py2app
	touch .logbook-built

# --------------------------------------------------------------------------
# Arete.icns — regenerated when generate_icon.py changes
# --------------------------------------------------------------------------
Arete.icns: generate_icon.py $(VENV_PYTHON)
	@echo "==> Generating Arete.icns..."
	$(VENV_PYTHON) generate_icon.py

# --------------------------------------------------------------------------
# timew — stamp-file guards the expensive compile; only runs once per version
# --------------------------------------------------------------------------
# 'make timew' is a convenience target — the app depends on the stamp file directly
timew: .timew-$(TIMEW_VERSION)

.timew-$(TIMEW_VERSION): $(VENV_PYTHON)
	@echo "==> Building timew $(TIMEW_VERSION) from source..."
	@set -e; \
	BUILD_TMP=$$(mktemp -d); \
	trap 'rm -rf "$$BUILD_TMP"' EXIT; \
	echo "  Downloading..."; \
	curl -fsSL "$(TIMEW_URL)" -o "$$BUILD_TMP/timew.tar.gz"; \
	echo "  Extracting..."; \
	tar -xzf "$$BUILD_TMP/timew.tar.gz" -C "$$BUILD_TMP"; \
	echo "  Configuring..."; \
	mkdir -p "$$BUILD_TMP/timew-$(TIMEW_VERSION)/build"; \
	$(CMAKE) -S "$$BUILD_TMP/timew-$(TIMEW_VERSION)" \
	         -B "$$BUILD_TMP/timew-$(TIMEW_VERSION)/build" \
	         -DCMAKE_BUILD_TYPE=Release \
	         -DENABLE_TESTS=OFF \
	         -DENABLE_DOCS=OFF \
	         > "$$BUILD_TMP/cmake.log" 2>&1 \
	    || { cat "$$BUILD_TMP/cmake.log"; exit 1; }; \
	echo "  Compiling (this may take a minute)..."; \
	$(MAKE) -C "$$BUILD_TMP/timew-$(TIMEW_VERSION)/build" \
	    -j$$(sysctl -n hw.logicalcpu) timew_executable \
	    > "$$BUILD_TMP/make.log" 2>&1 \
	    || { cat "$$BUILD_TMP/make.log"; exit 1; }; \
	cp "$$BUILD_TMP/timew-$(TIMEW_VERSION)/build/src/timew" timew; \
	echo "  timew: $$(./timew --version)"; \
	touch .timew-$(TIMEW_VERSION)

# --------------------------------------------------------------------------
# .venv — set up once; re-run only when requirements.txt changes
# --------------------------------------------------------------------------
$(VENV_PYTHON): requirements.txt
	@echo "==> Setting up virtual environment..."
	@if [ -z "$(PYTHON_BIN)" ]; then \
	    echo "  No Python 3.11+ found — bootstrapping via pyenv..."; \
	    PYENV_ROOT="$(PYENV_ROOT)"; \
	    if [ ! -x "$$PYENV_ROOT/bin/pyenv" ]; then \
	        echo "  Installing pyenv..."; \
	        curl -fsSL https://pyenv.run | bash; \
	    fi; \
	    export PATH="$$PYENV_ROOT/bin:$$PYENV_ROOT/shims:$$PATH"; \
	    eval "$$(pyenv init -)"; \
	    pyenv install --skip-existing 3.11; \
	    pyenv local 3.11; \
	    PYTHON_BIN="$$(pyenv which python3)"; \
	else \
	    PYTHON_BIN="$(PYTHON_BIN)"; \
	fi; \
	echo "  Using: $$PYTHON_BIN ($$($$PYTHON_BIN --version))"; \
	if [ -f "$(VENV_PYTHON)" ]; then \
	    cur="$$($(VENV_PYTHON) --version 2>&1)"; \
	    want="$$($$PYTHON_BIN --version 2>&1)"; \
	    if [ "$$cur" != "$$want" ]; then \
	        echo "  Replacing stale venv ($$cur → $$want)..."; \
	        rm -rf "$(VENV)"; \
	    fi; \
	fi; \
	[ -f "$(VENV_PYTHON)" ] || $$PYTHON_BIN -m venv "$(VENV)"; \
	$(VENV_PIP) install --upgrade pip --quiet; \
	$(VENV_PIP) install --upgrade py2app cmake rumps pyobjc \
	    -r requirements.txt --quiet; \
	touch $(VENV_PYTHON)
# --------------------------------------------------------------------------
# clean
# --------------------------------------------------------------------------
clean:
	@echo "==> Cleaning..."
	rm -rf build dist
	rm -f timew .timew-* .logbook-built
	rm -f Arete.icns icon.png
	@echo "  Done. (.venv preserved — rm -rf .venv to also wipe it)"

# --------------------------------------------------------------------------
# justfile shims — 'just build' and 'just run' still work
# --------------------------------------------------------------------------
