# -*- mode: python ; coding: utf-8 -*-
"""FAST test spec for Ghost Jarvis (excludes torch/whisper to validate structure)."""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent.parent
sys.path.insert(0, str(ROOT))

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
    ],
    hiddenimports=[
        "pyaudio",
        "webrtcvad",
        "edge_tts",
        "pyttsx3.drivers",
        "pyttsx3.drivers.sapi5",
        "cryptography",
        "platformdirs",
        "onboarding",
        "onboarding.pages",
        "onboarding.detector",
        "secure_store",
        "secure_store.windows",
        "secure_store.unix",
    ],
    hookspath=[str(ROOT / "build_scripts" / "common" / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchaudio",
        "faster_whisper",
        "ctranslate2",
        "pytest",
        "unittest",
        "pydoc",
        "tkinter",
        "matplotlib",
        "pandas",
        "tensorboard",
        "triton",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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
    icon=str(ROOT / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GhostJarvis",
)
