import os
import sys

# ---------------------------------------------------------------------------
# Auto re-exec under the project venv (same pattern as setup.py).
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(_HERE, ".venv", "bin", "python3")
if os.path.abspath(sys.executable) != os.path.abspath(_VENV_PYTHON):
    if os.path.exists(_VENV_PYTHON):
        os.execv(_VENV_PYTHON, [_VENV_PYTHON] + sys.argv)
    else:
        print(
            "ERROR: .venv not found. Run 'make' once to set it up.",
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

APP = ['timereport.py']
DATA_FILES = [
    ('', ['Changes.md']),
    ('', ['version']),
]
if os.path.exists('timew'):
    DATA_FILES.append(('', ['timew']))

OPTIONS = {
    'argv_emulation': False,
    'semi_standalone': False,
    'plist': {
        'LSUIElement': False,          # shows in Dock
        'CFBundleName': 'Arête Logbook',
        'CFBundleDisplayName': 'Arête Logbook',
        'CFBundleIdentifier': 'net.tanso.arete.logbook',
    },
    'packages': [],
    'iconfile': 'Arete.icns',
}

setup(
    name="AreteLogbook",
    version=VERSION,
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
