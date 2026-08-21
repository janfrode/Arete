import os
import sys

# ---------------------------------------------------------------------------
# Auto re-exec under the project venv if invoked with the wrong Python.
# This lets you run `python3 setup.py py2app` directly without activating
# the venv first.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(_HERE, ".venv", "bin", "python3")
if os.path.abspath(sys.executable) != os.path.abspath(_VENV_PYTHON):
    if os.path.exists(_VENV_PYTHON):
        os.execv(_VENV_PYTHON, [_VENV_PYTHON] + sys.argv)
    else:
        print(
            "ERROR: .venv not found. Run ./build_dmg.sh once to set it up,\n"
            "       or activate the venv manually before running setup.py.",
            file=sys.stderr,
        )
        sys.exit(1)

from setuptools import setup

def _read_version():
    with open(os.path.join(_HERE, "version"), encoding="utf-8") as _f:
        v = _f.read().strip()
    if not v:
        raise RuntimeError("version file is empty")
    return v

VERSION = _read_version()

APP = ['arete.py']
DATA_FILES = [
    # timereport.py is loaded in-process by arete.py; ship it alongside.
    ('', ['timereport.py']),
    # Changes.md is read by the "What's New" window.
    ('', ['Changes.md']),
    # version is the single source of truth for the app version number.
    ('', ['version']),
]
# Bundle the timew binary built by build_dmg.sh so the app works without a
# separate TimeWarrior installation.  arete.py will use it as a fallback.
if os.path.exists('timew'):
    DATA_FILES.append(('', ['timew']))
OPTIONS = {
    'argv_emulation': False,
    # Bundle the Python interpreter and stdlib so the .app works on any Mac
    # regardless of what Python (if any) is installed on the target system.
    'semi_standalone': False,
    'plist': {
        'LSUIElement': True,
        'CFBundleDisplayName': 'Arête',
    },
    # rumps is not auto-detected by py2app's import scanner; list it explicitly.
    # pyobjc (AppKit, Foundation, objc) is picked up automatically via imports.
    'packages': ['rumps'],
    'iconfile': 'Arete.icns',
}

setup(
    name="Arete",
    version=VERSION,
    author="Jan-Frode Myklebust",
    author_email="janfrode@tanso.net",
    url="https://github.com/janfrode/Arete",
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
