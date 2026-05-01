@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
rem Truncate (>) instead of append (>>) so app.log can't grow unbounded
rem across restarts. Python's RotatingFileHandler manages logs/ghost-jarvis.log.
pythonw main.py > app.log 2>&1
