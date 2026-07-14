"""PyInstaller hook for faster-whisper / ctranslate2.

Ensures the compiled ctranslate2 shared libraries and model tokenizer files
are bundled correctly.
"""
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

# Bundle ctranslate2 shared libs (.dll / .so / .dylib)
binaries = collect_dynamic_libs("ctranslate2")

# Bundle tokenizer configs and other data files
datas = collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")
