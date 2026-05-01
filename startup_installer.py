"""Install/uninstall Ghost Jarvis from Windows startup folder and desktop."""
import os
import sys
from pathlib import Path


def _app_label() -> str:
    """Return the user-visible app name based on agent_name config."""
    try:
        from config import APP_CONFIG
        name = APP_CONFIG.agent_name.strip()
        if name:
            return f"{name} Jarvis"
    except Exception:
        pass
    return "Ghost Jarvis"


def get_startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def get_desktop_folder() -> Path:
    desktop = os.environ.get("USERPROFILE")
    return Path(desktop) / "Desktop"


def _create_shortcut(lnk_path: Path, target: Path, arguments: str = "", work_dir: str = "", icon: str = ""):
    import win32com.client
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
    label = _app_label()
    lnk = startup / f"{label}.lnk"
    target, args, work_dir, icon = _resolve_target()
    _create_shortcut(lnk, target, args, work_dir, icon)
    return lnk


def remove_startup():
    label = _app_label()
    lnk = get_startup_folder() / f"{label}.lnk"
    if lnk.exists():
        lnk.unlink()
    # Also clean up legacy name if present
    legacy = get_startup_folder() / "Ghost Jarvis.lnk"
    if legacy.exists():
        legacy.unlink()


def install_desktop():
    desktop = get_desktop_folder()
    desktop.mkdir(parents=True, exist_ok=True)
    label = _app_label()
    lnk = desktop / f"{label}.lnk"
    target, args, work_dir, icon = _resolve_target()
    _create_shortcut(lnk, target, args, work_dir, icon)
    return lnk


def remove_desktop():
    label = _app_label()
    lnk = get_desktop_folder() / f"{label}.lnk"
    if lnk.exists():
        lnk.unlink()
    # Also clean up legacy name if present
    legacy = get_desktop_folder() / "Ghost Jarvis.lnk"
    if legacy.exists():
        legacy.unlink()


if __name__ == "__main__":
    install_startup()
    print(f"{_app_label()} agregado al inicio de Windows.")
