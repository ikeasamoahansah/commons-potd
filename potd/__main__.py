"""Command-line interface: fetch the POTD, preview it, set the wallpaper.

Run directly with ``python -m potd`` or (after installing) via the ``potd``
command.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path, PurePosixPath

from . import __version__
from .api import (NetworkError, PotdImage, PotdError, download,
                  fetch_potd_images)
from .preview import PreviewItem, show_chooser
from .wallpaper import WallpaperError, set_wallpaper

_CACHE_DIR = Path.home() / ".cache" / "commons-potd"
_BACKGROUNDS_DIR = Path.home() / ".local" / "share" / "backgrounds"
_PREVIEW_PADDING = 96
_RETRY_INTERVAL = 60 * 60  # seconds between offline retries
_OFFLINE_MESSAGE = "Wallpaper could not be loaded due to internet connectivity."
_RETRY_MESSAGE = "Retrying in an hour — press Ctrl-C to stop waiting."


def _screen_size(default: tuple[int, int] = (1920, 1080)) -> tuple[int, int]:
    """Best-effort size of the primary monitor, in pixels."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        return max(800, size[0]), max(600, size[1])
    except Exception:
        return default


def _preview_size(n: int, screen_w: int, screen_h: int) -> tuple[int, int]:
    """The pixel box each preview card may occupy (same layout as preview.py)."""
    box_w = max(280, min(640, (screen_w - _PREVIEW_PADDING) // max(n, 1)))
    box_h = max(220, min(720, screen_h - 320))
    return box_w, box_h


def _fetch_images(date: dt.date, screen_width: int) -> list[PotdImage]:
    """Fetch the day's images, retrying hourly while offline.

    Connectivity failures print a message and wait one hour between
    attempts, forever (Ctrl-C cancels). Any other :class:`PotdError` —
    e.g. a day with no picture — propagates immediately.
    """
    while True:
        try:
            return fetch_potd_images(date, screen_width=screen_width)
        except NetworkError:
            print(_OFFLINE_MESSAGE, file=sys.stderr)
            print(_RETRY_MESSAGE, file=sys.stderr)
            time.sleep(_RETRY_INTERVAL)


def _build_preview_item(image: PotdImage, index: int, n: int,
                        date: dt.date) -> PreviewItem:
    """Download a small PNG preview of ``image`` and wrap it for the chooser."""
    screen_w, screen_h = _screen_size()
    box_w, box_h = _preview_size(n, screen_w, screen_h)

    aspect = image.original_height / image.original_width
    width = max(120, min(box_w, int(box_h / aspect)))

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = PurePosixPath(image.preview_url(width)).suffix.lower() or ".jpg"
    path = _CACHE_DIR / f"{date:%Y%m%d}-{index + 1}{suffix}"
    if not path.is_file() or path.stat().st_size == 0:
        download(image.preview_url(width), path)

    return PreviewItem(
        title=image.name,
        detail=f"{image.original_width} × {image.original_height}",
        path=path,
        source_url=image.description_url,
    )


def _download_wallpaper_size(image: PotdImage, index: int,
                             date: dt.date) -> Path:
    _BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    path = _BACKGROUNDS_DIR / (
        f"commons-potd-{date:%Y%m%d}-{index + 1}{image.extension}")
    print(f"Downloading {image.name} …")
    download(image.thumb_url, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="potd",
        description="Set your desktop wallpaper to the Wikimedia Commons "
                    "Picture of the Day, shown in a preview window so you "
                    "can confirm (or pick, when a day has more than one).",
    )
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=None,
        metavar="YYYY-MM-DD",
        help="which day's picture to use (default: today, UTC)",
    )
    parser.add_argument(
        "--index", type=int, default=None, metavar="N",
        help="use the N-th image (1-based) without showing the preview",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="never show the preview; use the first image",
    )
    parser.add_argument(
        "--width", type=int, default=None, metavar="PX",
        help="override the detected monitor width used for downloads "
             "(e.g. 3840 for a 4K screen with fractional scaling)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="print the day's images and exit without changing anything",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    date = args.date or dt.datetime.now(dt.timezone.utc).date()
    screen_width = args.width or _screen_size()[0]

    try:
        if args.list:
            # A listing shouldn't sit around waiting for the network.
            images = fetch_potd_images(date, screen_width=screen_width)
        else:
            images = _fetch_images(date, screen_width)
    except PotdError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.list:
        for i, image in enumerate(images, 1):
            dims = f"{image.original_width}×{image.original_height}"
            print(f"{i}. {image.name}  ({dims})")
            print(f"   {image.description_url}")
        return 0

    # ---- choose which image to use -------------------------------------
    if args.index is not None:
        if not 1 <= args.index <= len(images):
            print(f"Error: --index must be between 1 and {len(images)}",
                  file=sys.stderr)
            return 1
        chosen = args.index - 1
    elif args.auto:
        chosen = 0
    else:
        # Always show the preview — even for a single daily image — so you
        # can confirm (or cancel) before the wallpaper changes.
        try:
            print("Opening the preview window — confirm the picture there.")
            items = [_build_preview_item(image, i, len(images), date)
                     for i, image in enumerate(images)]
            chosen = show_chooser(items, f"{date:%Y-%m-%d}")
        except Exception as exc:
            print(f"Error: could not open the preview window ({exc}).\n"
                  "Use --auto or --index N instead.", file=sys.stderr)
            return 1
        if chosen is None:
            print("Cancelled — wallpaper unchanged.")
            return 0

    image = images[chosen]
    print(f"Selected: {image.name}")

    # ---- download at wallpaper size and apply ---------------------------
    try:
        path = _download_wallpaper_size(image, chosen, date)
        method = set_wallpaper(path)
    except (PotdError, WallpaperError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wallpaper set via {method}: {path}")
    print(f"Source: {image.description_url}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
