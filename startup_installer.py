"""Install/uninstall Ghost Jarvis from Windows startup folder and desktop."""
import os
import sys
from pathlib import Path

import win32com.client


def get_startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def get_desktop_folder() -> Path:
    desktop = os.environ.get("USERPROFILE")
    return Path(desktop) / "Desktop"


def _create_shortcut(lnk_path: Path, target: Path, arguments: str = "", work_dir: str = "", icon: str = ""):
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(lnk_path))
    shortcut.TargetPath = str(target)
    if arguments:
        shortcut.Arguments = arguments
    if work_dir:
        shortcut.WorkingDirectory = work_dir
    if icon:
        shortcut.IconLocation = icon
    shortcut.save()
    return lnk_path


def _resolve_target():
    """Return (target_path, arguments, work_dir, icon) for the app launcher."""
    base = Path(__file__).parent
    vbs = base / "launch.vbs"
    bat = base / "launch.bat"
    main_py = base / "main.py"
    pythonw = Path(sys.executable).parent / "pythonw.exe"

    if vbs.exists():
        return (vbs, "", str(base), str(vbs))
    elif bat.exists():
        return (bat, "", str(base), str(bat))
    else:
        # Fallback to pythonw + main.py
        return (pythonw, f'"{main_py}"', str(base), str(main_py))


def install_startup():
    startup = get_startup_folder()
    startup.mkdir(parents=True, exist_ok=True)
    lnk = startup / "Ghost Jarvis.lnk"
    target, args, work_dir, icon = _resolve_target()
    _create_shortcut(lnk, target, args, work_dir, icon)
    return lnk


def remove_startup():
    lnk = get_startup_folder() / "Ghost Jarvis.lnk"
    if lnk.exists():
        lnk.unlink()


def install_desktop():
    desktop = get_desktop_folder()
    desktop.mkdir(parents=True, exist_ok=True)
    lnk = desktop / "Ghost Jarvis.lnk"
    target, args, work_dir, icon = _resolve_target()
    _create_shortcut(lnk, target, args, work_dir, icon)
    return lnk


def remove_desktop():
    lnk = get_desktop_folder() / "Ghost Jarvis.lnk"
    if lnk.exists():
        lnk.unlink()


if __name__ == "__main__":
    install_startup()
    print("Ghost Jarvis agregado al inicio de Windows.")
