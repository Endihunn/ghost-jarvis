"""PyInstaller hook for torchaudio.

Ensures backend shared libraries (sox, ffmpeg, etc.) are bundled.
"""
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

binaries = collect_dynamic_libs("torchaudio")
datas = collect_data_files("torchaudio")
