# ---------------------------------------------------------------------------
# Arete — incremental build
#
# Targets:
#   all            → dist/Arete.dmg  (default)
#   dist/Arete.dmg → full DMG
#   run            → build app if needed, then launch it
#   timew          → build bundled timew binary only
#   clean          → remove build/, dist/, timew, .timew-*, Arete.icns, icon.png
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
    $(shell for p in $(PYENV_ROOT)/versions/3.11.*/bin/python3; do \
        [ -x "$$p" ] && echo "$$p" && break; \
    done) \
)

PY_SOURCES := arete.py timereport.py setup.py

# --------------------------------------------------------------------------
.PHONY: all run timew clean
# timew is a convenience alias only — not a build input itself

all: dist/Arete.dmg

# --------------------------------------------------------------------------
# run — build the app bundle then launch it directly (skips DMG)
# --------------------------------------------------------------------------
run: dist/Arete.app/Contents/MacOS/Arete
	@echo "==> Launching Arete..."
	dist/Arete.app/Contents/MacOS/Arete

# --------------------------------------------------------------------------
# dist/Arete.dmg
# --------------------------------------------------------------------------
dist/Arete.dmg: dist/Arete.app/Contents/MacOS/Arete
	@echo "==> Assembling DMG..."
	rm -rf dist/dmg_temp
	mkdir -p dist/dmg_temp
	cp -R dist/Arete.app dist/dmg_temp/
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
dist/Arete.app/Contents/MacOS/Arete: $(VENV_PYTHON) Arete.icns .timew-$(TIMEW_VERSION) $(PY_SOURCES)
	@echo "==> Building Arete.app with py2app..."
	rm -rf build dist/Arete.app
	$(VENV_PYTHON) setup.py py2app

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
	    echo "ERROR: No Python 3.11+ found. Set PYTHON=/path/to/python3." >&2; \
	    exit 1; \
	fi
	@echo "  Using: $(PYTHON_BIN)"
	@# Replace venv if built against a different Python
	@if [ -f "$(VENV_PYTHON)" ]; then \
	    cur="$$($(VENV_PYTHON) --version 2>&1)"; \
	    want="$$($(PYTHON_BIN) --version 2>&1)"; \
	    if [ "$$cur" != "$$want" ]; then \
	        echo "  Replacing stale venv ($$cur → $$want)..."; \
	        rm -rf "$(VENV)"; \
	    fi; \
	fi
	@[ -f "$(VENV_PYTHON)" ] || $(PYTHON_BIN) -m venv "$(VENV)"
	$(VENV_PIP) install --upgrade pip --quiet
	$(VENV_PIP) install --upgrade py2app cmake rumps pyobjc \
	    -r requirements.txt --quiet
	touch $(VENV_PYTHON)

# --------------------------------------------------------------------------
# clean
# --------------------------------------------------------------------------
clean:
	@echo "==> Cleaning..."
	rm -rf build dist
	rm -f timew .timew-*
	rm -f Arete.icns icon.png
	@echo "  Done. (.venv preserved — rm -rf .venv to also wipe it)"
