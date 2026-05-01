# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Ghost Jarvis.

Builds a one-folder bundle with all assets and the Whisper model included.
Usage:
    pyinstaller build_scripts/common/ghost_jarvis.spec --clean
"""
import sys
from pathlib import Path

# Ensure the project root is on the path so imports resolve during Analysis
ROOT = Path(SPECPATH).parent.parent
sys.path.insert(0, str(ROOT))

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "models"), "models"),
    ],
    hiddenimports=[
        # Audio / STT / TTS
        "pyaudio",
        "webrtcvad",
        "faster_whisper",
        "ctranslate2",
        "torch",
        "torchaudio",
        "edge_tts",
        "pyttsx3.drivers",
        "pyttsx3.drivers.sapi5",
        "pyttsx3.drivers.dummy",
        "pyttsx3.drivers.espeak",
        "pyttsx3.drivers.nsss",
        # Security / utils
        "cryptography",
        "cryptography.hazmat.primitives.kdf.pbkdf2",
        "platformdirs",
        # Our packages
        "onboarding",
        "onboarding.pages",
        "onboarding.detector",
        "secure_store",
        "secure_store.windows",
        "secure_store.unix",
        # GPU utils may import these conditionally
        "torch.cuda",
    ],
    hookspath=[str(ROOT / "build_scripts" / "common" / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test suites and docs to reduce bundle size
        "pytest",
        "unittest",
        "pydoc",
        "tkinter",
        "matplotlib",
        "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=1,
)

# Strip large debug symbols if any
pyz = PYZ(a.pure, a.zipped_data)

# One-file bundle for simpler installer packaging.
# The single .exe contains all binaries, datas, and Python libs.

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name="GhostJarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico") if sys.platform == "win32" else None,
)
