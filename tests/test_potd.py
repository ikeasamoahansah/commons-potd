"""Offline unit tests — no network required."""

import unittest
import unittest.mock
from pathlib import PurePosixPath

from potd import __main__ as cli
from potd.api import (
    IMAGE_EXTENSIONS,
    THUMBNAIL_STEPS,
    _SKIP_RE,
    NetworkError,
    PotdError,
    PotdImage,
    potd_templates,
    snap_width,
)
from potd.__main__ import _preview_size


def make_image(thumb_url: str, **overrides) -> PotdImage:
    defaults = dict(
        title="File:Example.jpg",
        description_url="https://commons.wikimedia.org/wiki/File:Example.jpg",
        original_url="https://upload.wikimedia.org/example.jpg",
        original_width=6000,
        original_height=4000,
        thumb_url=thumb_url,
        thumb_width=1920,
        thumb_height=1280,
    )
    defaults.update(overrides)
    return PotdImage(**defaults)


class TemplateNameTests(unittest.TestCase):
    def test_date_formatting(self):
        import datetime as dt
        self.assertEqual(
            potd_templates(dt.date(2026, 11, 7)),
            ("Template:Potd/2026-11-07", "Template:POTD/2026-11-07"),
        )


class ThumbWidthTests(unittest.TestCase):
    def test_snaps_up_to_allowed_steps(self):
        for requested, expected in ((1, 20), (800, 960), (1920, 1920),
                                    (2000, 3840), (9999, 3840)):
            self.assertEqual(snap_width(requested), expected)
        for step in THUMBNAIL_STEPS:
            self.assertEqual(snap_width(step), step)

    def test_preview_snaps_up(self):
        img = make_image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "6/62/Example.jpg/1920px-Example.jpg")
        url = img.preview_url(800)
        self.assertIn("/960px-", url)

    def test_no_upscale_for_raster(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/x/ab/Small.jpg"
        img = make_image(url, original_url=url,
                         original_width=500, original_height=300)
        self.assertEqual(img.preview_url(800), url)

    def test_scalable_formats_always_get_a_thumb(self):
        img = make_image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "1/13/Big.svg/1920px-Big.svg.png",
            title="File:Big.svg", original_width=100, original_height=100)
        self.assertIn("960px-Big.svg.png", img.preview_url(800))

    def test_utm_query_not_part_of_extension(self):
        img = make_image(
            "https://thumb.wikimedia.org/wikipedia/commons/thumb/0/0c/"
            "Example.jpg/1920px-Example.jpg?utm_source=x")
        self.assertEqual(img.extension, ".jpg")


class ThumbnailUrlTests(unittest.TestCase):
    def test_rewidth_raster(self):
        img = make_image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "6/62/Example.jpg/1920px-Example.jpg")
        self.assertEqual(
            img._rewidth_url(960),
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "6/62/Example.jpg/960px-Example.jpg")

    def test_rewidth_tiff(self):
        img = make_image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "a/ab/Example.tif/lossy-page1-1920px-Example.tif.jpg")
        self.assertEqual(
            img._rewidth_url(960),
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "a/ab/Example.tif/lossy-page1-960px-Example.tif.jpg")

    def test_png_conversion_for_jpeg(self):
        img = make_image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "6/62/Example.jpg/1920px-Example.jpg")
        url = img.preview_url(1920)
        # Portrait/narrow cases keep the ended file format; JPEG previews
        # are converted locally with Pillow, never by the CDN.
        self.assertTrue(url.startswith(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "6/62/Example.jpg/"))
        self.assertIn("1920px-Example.jpg", url)

    def test_already_png_keeps_png_and_rewidths(self):
        img = make_image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/"
            "1/13/Example.svg/1920px-Example.svg.png")
        url = img.preview_url(960)
        self.assertTrue(url.endswith("960px-Example.svg.png"))

    def test_extension(self):
        img = make_image("https://example.org/thumb/x/y/Foo.svg/800px-Foo.svg.png")
        self.assertEqual(img.extension, ".png")
        self.assertEqual(PurePosixPath(img.thumb_url).suffix,
                         ".png")


class NameTests(unittest.TestCase):
    def test_name_strips_prefix(self):
        img = make_image("https://example.org/800px-Foo.jpg",
                         title="File:Foo bar.jpg")
        self.assertEqual(img.name, "Foo bar.jpg")


class SkipFilterTests(unittest.TestCase):
    def test_boilerplate_skipped(self):
        for title in ("File:Padlock.svg", "File:Commons-logo.svg",
                      "File:Ambox_question_book.svg"):
            self.assertTrue(_SKIP_RE.search(title), title)

    def test_real_pictures_not_skipped(self):
        for title in ("File:Sunset over the bay.jpg",):
            self.assertFalse(_SKIP_RE.search(title), title)

    def test_extensions(self):
        self.assertIn(".jpg", IMAGE_EXTENSIONS)
        self.assertIn(".svg", IMAGE_EXTENSIONS)
        self.assertNotIn(".ogg", IMAGE_EXTENSIONS)
        self.assertNotIn(".pdf", IMAGE_EXTENSIONS)


class RetryTests(unittest.TestCase):
    """Offline behaviour when the machine has no internet connection."""

    def _run(self, fake_fetch):
        """Call ``cli._fetch_images`` with ``fake_fetch`` and a stubbed sleep."""
        sleeps = []
        with unittest.mock.patch.object(cli, "fetch_potd_images", fake_fetch), \
                unittest.mock.patch.object(cli.time, "sleep", sleeps.append):
            result = cli._fetch_images("someday", 1920)
        return result, sleeps

    def test_retries_hourly_until_success(self):
        calls = []

        def fake_fetch(date, *, screen_width):
            calls.append(screen_width)
            if len(calls) < 3:
                raise NetworkError("Could not reach Wikimedia Commons: down")
            return ["image"]

        result, sleeps = self._run(fake_fetch)
        self.assertEqual(result, ["image"])
        self.assertEqual(calls, [1920, 1920, 1920])
        # One sleep between each pair of attempts, exactly an hour apart.
        self.assertEqual(sleeps, [cli._RETRY_INTERVAL, cli._RETRY_INTERVAL])
        self.assertEqual(cli._RETRY_INTERVAL, 60 * 60)

    def test_other_potd_errors_do_not_wait(self):
        def fake_fetch(date, *, screen_width):
            raise PotdError("No Picture of the Day found for someday.")

        with self.assertRaises(PotdError):
            self._run(fake_fetch)

    def test_network_error_is_a_potd_error(self):
        # Callers that catch PotdError keep working unchanged.
        self.assertTrue(issubclass(NetworkError, PotdError))


class PreviewSizeTests(unittest.TestCase):
    def test_two_cards_wide_screen(self):
        w, h = _preview_size(2, 1920, 1080)
        # 912 would fit, but the chooser clamps card width at 640.
        self.assertEqual(w, 640)
        self.assertLessEqual(h, 720)

    def test_sane_minimums(self):
        w, h = _preview_size(3, 800, 600)
        self.assertGreaterEqual(w, 280)
        self.assertGreaterEqual(h, 220)


if __name__ == "__main__":
    unittest.main()
