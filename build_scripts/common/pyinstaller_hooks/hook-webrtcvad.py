"""PyInstaller hook for webrtcvad / webrtcvad-wheels.

Overrides the broken contrib hook.
"""
from PyInstaller.utils.hooks import collect_dynamic_libs

hiddenimports = ["webrtcvad"]
binaries = collect_dynamic_libs("webrtcvad")
