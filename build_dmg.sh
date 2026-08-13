#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# Arete – build script
# Creates a fully self-contained Arete.dmg:
#   - Python interpreter + stdlib bundled via py2app (semi_standalone=False)
#   - rumps + pyobjc bundled inside the .app
#   - TimeWarrior (timew) binary compiled from source and bundled as a
#     fallback; the app always prefers a separately installed timew on PATH.
#
# Usage:
#   ./build_dmg.sh                        # fully automatic
#   PYTHON=/path/to/python3 ./build_dmg.sh  # force a specific interpreter
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
PYTHON_VERSION="3.11"
TIMEW_VERSION="1.10.0"
TIMEW_SRC_URL="https://github.com/GothenburgBitFactory/timewarrior/releases/download/v${TIMEW_VERSION}/timew-${TIMEW_VERSION}.tar.gz"
TIMEW_BIN="$SCRIPT_DIR/timew"   # bundled binary destination

# ---- helpers ---------------------------------------------------------------

step() { echo; echo "==> $*"; }

die() { echo "ERROR: $*" >&2; exit 1; }

# ---- 1. Find or install Python 3.11+ ---------------------------------------

step "Locating Python 3.11+ interpreter..."

find_python() {
    # Explicit override
    if [ -n "$PYTHON" ]; then
        if "$PYTHON" --version 2>&1 | grep -qE "Python 3\.(1[1-9]|[2-9][0-9])"; then
            echo "$PYTHON"; return
        fi
        die "PYTHON=$PYTHON does not satisfy Python >=3.11"
    fi
    # python.org framework installs
    for v in 3.13 3.12 3.11; do
        p="/Library/Frameworks/Python.framework/Versions/$v/bin/python$v"
        [ -x "$p" ] && echo "$p" && return
    done
    # pyenv — look for any 3.11.x installed version
    for p in "$PYENV_ROOT/versions/"3.11.*/bin/python3; do
        [ -x "$p" ] && echo "$p" && return
    done
    echo ""
}

PYTHON_BIN="$(find_python)"

if [ -z "$PYTHON_BIN" ]; then
    step "No Python 3.11+ found — bootstrapping via pyenv..."

    if [ ! -x "$PYENV_ROOT/bin/pyenv" ]; then
        echo "  Installing pyenv..."
        curl -fsSL https://pyenv.run | bash
    fi

    export PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH"
    eval "$(pyenv init -)"

    pyenv install --skip-existing "$PYTHON_VERSION"
    pyenv local "$PYTHON_VERSION"
    PYTHON_BIN="$(pyenv which python3)"
fi

echo "  Using: $PYTHON_BIN ($("$PYTHON_BIN" --version))"

# ---- 2. Set up venv --------------------------------------------------------

step "Setting up virtual environment..."

# Recreate venv if it was built against a different Python
if [ -f "$VENV/bin/python3" ]; then
    VENV_PY="$("$VENV/bin/python3" --version 2>&1)"
    WANT_PY="$("$PYTHON_BIN" --version 2>&1)"
    if [ "$VENV_PY" != "$WANT_PY" ]; then
        echo "  Replacing stale venv ($VENV_PY → $WANT_PY)..."
        rm -rf "$VENV"
    fi
fi

if [ ! -f "$VENV/bin/python3" ]; then
    "$PYTHON_BIN" -m venv "$VENV"
fi

echo "  Installing build dependencies..."
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install --upgrade py2app cmake rumps pyobjc \
    -r "$SCRIPT_DIR/requirements.txt" --quiet

# ---- 3. Generate Arete.icns -----------------------------------------------

step "Generating Arete.icns..."
"$VENV/bin/python3" "$SCRIPT_DIR/generate_icon.py"

# ---- 4. Build bundled timew binary -----------------------------------------

step "Building bundled timew $TIMEW_VERSION from source..."

BUILD_TMP="$(mktemp -d)"
trap 'rm -rf "$BUILD_TMP"' EXIT

TIMEW_TAR="$BUILD_TMP/timew.tar.gz"
TIMEW_SRC="$BUILD_TMP/timew-${TIMEW_VERSION}"

echo "  Downloading timew $TIMEW_VERSION..."
curl -fsSL "$TIMEW_SRC_URL" -o "$TIMEW_TAR"

echo "  Extracting..."
tar -xzf "$TIMEW_TAR" -C "$BUILD_TMP"

echo "  Configuring..."
CMAKE="$VENV/bin/cmake"
mkdir -p "$TIMEW_SRC/build"
"$CMAKE" -S "$TIMEW_SRC" -B "$TIMEW_SRC/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$TIMEW_SRC/install" \
    -DENABLE_TESTS=OFF \
    -DENABLE_DOCS=OFF \
    > "$BUILD_TMP/cmake.log" 2>&1 || { cat "$BUILD_TMP/cmake.log"; die "cmake configure failed"; }

echo "  Compiling (this may take a minute)..."
make -C "$TIMEW_SRC/build" -j"$(sysctl -n hw.logicalcpu)" timew_executable \
    > "$BUILD_TMP/make.log" 2>&1 || { cat "$BUILD_TMP/make.log"; die "make failed"; }

# Copy the binary directly from the build tree — no install step needed
cp "$TIMEW_SRC/build/src/timew" "$TIMEW_BIN"
echo "  Bundled timew: $("$TIMEW_BIN" --version)"

# ---- 5. Build the .app bundle ----------------------------------------------

step "Building Arete.app with py2app..."
rm -rf "$SCRIPT_DIR/build" "$SCRIPT_DIR/dist"
cd "$SCRIPT_DIR"
"$VENV/bin/python3" setup.py py2app

# ---- 6. Assemble the DMG ---------------------------------------------------

step "Assembling DMG..."
DMG_TMP="$SCRIPT_DIR/dist/dmg_temp"
rm -rf "$DMG_TMP"
mkdir -p "$DMG_TMP"

cp -R "$SCRIPT_DIR/dist/Arete.app" "$DMG_TMP/"
ln -s /Applications "$DMG_TMP/Applications"

rm -f "$SCRIPT_DIR/dist/Arete.dmg"
hdiutil create -volname "Arete" -srcfolder "$DMG_TMP" -ov -format UDZO \
    "$SCRIPT_DIR/dist/Arete.dmg"

rm -rf "$DMG_TMP"

step "Done! Created dist/Arete.dmg"
