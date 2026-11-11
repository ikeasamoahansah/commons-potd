"""Fetch the Wikimedia Commons Picture of the Day.

Most days the POTD is a single image; occasionally a day consists of two or
more. Everything transcluded on the POTD template page is inspected,
non-picture files are filtered out, and the remaining candidates are
returned in page order.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

import requests

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "commons-potd/1.0 (desktop wallpaper setter; "
    "https://github.com/ikeasamoahansah/commons-potd)"
)

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff",
    ".svg", ".bmp", ".avif", ".xcf", ".djvu",
}

# Boilerplate files that occasionally appear on the POTD template page but
# are never the picture itself.
_SKIP_RE = re.compile(
    r"(?i)(padlock|commons-logo|question[_ ]book|ambox|edit-protect|"
    r"symbol_|_icon|icon[_ -]|nuvola|crystal)"
)

# Wikimedia production only generates thumbnails in these widths (see
# https://www.mediawiki.org/wiki/Common_thumbnail_sizes). Requests for
# other sizes are rejected (HTTP 400/429). Always snap to a step ≥ target.
THUMBNAIL_STEPS = (20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840)

# Formats whose rendering scales to any size (so thumbs exist even when
# the requested width exceeds the "original" raster dimensions).
SCALABLE_FORMATS = {".svg", ".tif", ".tiff", ".xcf", ".djvu"}

_TIMEOUT = 30  # seconds


class PotdError(Exception):
    """Raised when the Picture of the Day cannot be fetched."""


class NetworkError(PotdError):
    """A :class:`PotdError` caused by a connectivity failure.

    Raised on timeout / connection / HTTP-transport problems so callers
    can wait for the network instead of giving up. It is a *subclass* of
    ``PotdError``, so code that only catches ``PotdError`` keeps working.
    """


@dataclass(frozen=True)
class PotdImage:
    """One candidate image for the day."""

    title: str                 # e.g. "File:Something.jpg"
    description_url: str       # Commons file page
    original_url: str
    original_width: int
    original_height: int
    thumb_url: str             # ~screen-size thumbnail for the wallpaper
    thumb_width: int
    thumb_height: int

    @property
    def name(self) -> str:
        """File name without the ``File:`` prefix."""
        return self.title[len("File:"):]

    def preview_url(self, width: int) -> str:
        """Thumbnail URL for on-screen previewing at ≈ ``width`` pixels.

        Snapped up to an allowed Wikimedia thumbnail width. Files smaller
        than that are returned at full size instead of being upscaled
        (raster formats cannot be upscaled by Commons).
        """
        target = min(snap_width(width), THUMBNAIL_STEPS[-1])
        scalable = PurePosixPath(self.title).suffix.lower() in SCALABLE_FORMATS
        if not scalable and target >= self.original_width > 0:
            return self.original_url or self.thumb_url
        return self._rewidth_url(target)

    def _rewidth_url(self, width: int) -> str:
        """Change the pixel width of a Commons thumbnail URL.

        The width is the only free parameter; ``width`` must already be
        one of the allowed ``THUMBNAIL_STEPS``.
        """
        head, _, last = self.thumb_url.rpartition("/")
        # The width qualifier always matches "\d+px-"; the segment may start
        # with a modifier such as "lossy-page1-" for multi-page sources.
        match = re.search(r"\d+px-", last)
        if match:
            last = f"{last[:match.start()]}{width}px-{last[match.end():]}"
        return f"{head}/{last}"

    @property
    def extension(self) -> str:
        """File extension of the downloadable thumbnail."""
        return PurePosixPath(_clean_url(self.thumb_url)).suffix.lower()


def snap_width(width: int) -> int:
    """Smallest allowed Wikimedia thumbnail width ≥ ``width``."""
    for step in THUMBNAIL_STEPS:
        if step >= width:
            return step
    return THUMBNAIL_STEPS[-1]


def _clean_url(url: str) -> str:
    """Strip tracking query strings (e.g. ``?utm_source=…``)."""
    return url.split("?", 1)[0]


def _get(params: dict, session: Optional[requests.Session] = None) -> dict:
    http = session or requests
    params = {"format": "json", "formatversion": 2, **params}
    try:
        response = http.get(
            API_URL, params=params, headers={"User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise NetworkError(f"Could not reach Wikimedia Commons: {exc}") from exc


def potd_templates(date: dt.date) -> tuple[str, ...]:
    """Possible page titles of the POTD template for ``date``."""
    iso = f"{date:%Y-%m-%d}"
    return (f"Template:Potd/{iso}", f"Template:POTD/{iso}")


def _template_images(page_title: str, session) -> list[str]:
    """File titles transcluded on a POTD template page (up to 50)."""
    data = _get({
        "action": "query",
        "titles": page_title,
        "prop": "images",
        "imlimit": 100,
    }, session)
    pages = data.get("query", {}).get("pages", [])
    if not pages or "missing" in pages[0]:
        return []
    return [
        img["title"]
        for img in pages[0].get("images", [])
        if img["title"].startswith("File:")
        and PurePosixPath(img["title"]).suffix.lower() in IMAGE_EXTENSIONS
        and not _SKIP_RE.search(img["title"])
    ]


def _info(candidates: list[str], screen_width: int, session) -> list[PotdImage]:
    """Fetch URLs, dimensions and a screen-sized thumbnail for file titles."""
    if not candidates:
        return []
    data = _get({
        "action": "query",
        "titles": "|".join(candidates[:50]),
        "prop": "imageinfo",
        "iiprop": "url|size",
        "iiurlwidth": snap_width(max(1, min(int(screen_width), 3840))),
    }, session)
    images: list[PotdImage] = []
    for page in data.get("query", {}).get("pages", []):
        if "missing" in page or not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        thumb = info.get("thumburl")
        if not thumb:
            continue
        images.append(PotdImage(
            title=page["title"],
            description_url=info.get("descriptionurl", ""),
            original_url=_clean_url(info["url"]),
            original_width=info["width"],
            original_height=info["height"],
            thumb_url=_clean_url(thumb),
            thumb_width=info.get("thumbwidth", info["width"]),
            thumb_height=info.get("thumbheight", info["height"]),
        ))
    return images


def fetch_potd_images(
    date: dt.date,
    *,
    screen_width: int = 1920,
    session: Optional[requests.Session] = None,
) -> list[PotdImage]:
    """Return the Picture(s) of the Day for ``date``.

    Primary source: the Commons ``Potd/`` template page (covers far-ahead
    dates and multi-image days). Fallback: the Wikimedia featured feed,
    which knows the current day even before its template page is created.
    Usually one image; some days two.
    """
    candidates: list[str] = []
    for title in potd_templates(date):
        candidates = _template_images(title, session)
        if candidates:
            break
    images = _info(candidates, screen_width, session)
    if images:
        return images

    # Fallback: featured feed (also covers "today" before its template
    # page exists and dates the Commons-wiki template may never carry).
    images = fetch_potd_from_feed(date, screen_width, session)
    if images:
        return images
    raise PotdError(f"No Picture of the Day found for {date:%Y-%m-%d}.")


def fetch_potd_from_feed(
    date: dt.date, screen_width: int = 1920,
    session: Optional[requests.Session] = None,
) -> list[PotdImage]:
    """POTD via the Wikimedia featured feed (en.wikipedia)."""
    http = session or requests
    url = (f"https://api.wikimedia.org/feed/v1/wikipedia/en/featured/"
           f"{date:%Y/%m/%d}")
    try:
        response = http.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=_TIMEOUT)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        image = response.json().get("image") or {}
    except requests.RequestException as exc:
        raise NetworkError(f"Could not reach the Wikimedia feed: {exc}") from exc
    title = image.get("title", "")
    if not title:
        return []

    original = image.get("image") or {}
    thumbnail = image.get("thumbnail") or {}
    file_page = image.get("file_page", "")
    # Query the Commons API for a monitor-sized thumb of this one file.
    return _info([title], screen_width, session) or [PotdImage(
        title=title,
        description_url=file_page,
        original_url=_clean_url(original.get("source", "")),
        original_width=original.get("width", 0),
        original_height=original.get("height", 0),
        thumb_url=_clean_url(thumbnail.get("source", "")),
        thumb_width=thumbnail.get("width", 0),
        thumb_height=thumbnail.get("height", 0),
    )]


def download(url: str, path, session: Optional[requests.Session] = None) -> None:
    """Download ``url`` to ``path``."""
    http = session or requests
    try:
        with http.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT,
            stream=True,
        ) as response:
            response.raise_for_status()
            with open(path, "wb") as fh:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
    except requests.RequestException as exc:
        raise NetworkError(f"Could not download {url}: {exc}") from exc
    except OSError as exc:  # e.g. disk full / permission denied, not network
        raise PotdError(f"Could not download {url}: {exc}") from exc
