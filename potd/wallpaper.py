"""Set the desktop wallpaper, supporting the common Linux desktops."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class WallpaperError(Exception):
    """Raised when no supported desktop wallpaper mechanism is found."""


def _run(args: list[str]) -> None:
    try:
        subprocess.run(args, check=True)
    except subprocess.CalledProcessError as exc:
        raise WallpaperError(
            f"Command failed ({' '.join(args)}): "
            f"{exc.stderr.strip() if exc.stderr else exc.returncode}"
        ) from exc
    except FileNotFoundError as exc:
        raise WallpaperError(str(exc)) from exc


def _copy_into_backgrounds(path: Path) -> Path:
    """Copy the image somewhere GNOME/KDE profiles will keep it."""
    backgrounds = Path.home() / ".local" / "share" / "backgrounds"
    backgrounds.mkdir(parents=True, exist_ok=True)
    destination = backgrounds / path.name
    if destination.exists() and destination.stem == path.stem:
        try:
            if destination.samefile(path):
                return destination
        except OSError:
            pass
    shutil.copyfile(path, destination)
    return destination


def set_wallpaper(path: Path) -> str:
    """Set ``path`` as wallpaper. Returns a human-readable method name."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise WallpaperError(f"Wallpaper file not found: {path}")

    if shutil.which("gsettings"):  # GNOME / Ubuntu / Budgie / Cinnamon
        file_uri = _copy_into_backgrounds(path).as_uri()
        for key in ("picture-uri", "picture-uri-dark"):
            _run(["gsettings", "set", "org.gnome.desktop.background", key, file_uri])
        _run(["gsettings", "set", "org.gnome.desktop.background",
              "picture-options", "zoom"])
        return "GNOME (gsettings)"

    if shutil.which("plasma-apply-wallpaperimage"):  # KDE Plasma
        _run(["plasma-apply-wallpaperimage", str(path)])
        return "KDE Plasma"

    if shutil.which("xfconf-query"):  # XFCE
        out = subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-l"],
            capture_output=True, text=True, check=True,
        ).stdout
        props = [line.strip() for line in out.splitlines()
                 if line.strip().endswith("last-image")]
        if not props:
            raise WallpaperError("No XFCE backdrop property found to set.")
        for prop in props:
            _run(["xfconf-query", "-c", "xfce4-desktop", "-p", prop,
                  "-s", str(path)])
        return "XFCE"

    if shutil.which("feh"):  # i3 / window managers
        _run(["feh", "--bg-fill", "--no-fehbg", str(path)])
        return "feh"

    if shutil.which("swaybg"):  # Sway
        _run(["swaybg", "--mode", "fill", "-i", str(path)])
        return "sway"

    raise WallpaperError(
        "No supported wallpaper mechanism found. Install gsettings (GNOME), "
        "plasma-apply-wallpaperimage (KDE), xfconf-query (XFCE), "
        "or feh/swaybg for window managers."
    )
