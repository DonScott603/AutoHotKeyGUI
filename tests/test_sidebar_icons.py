import os
import unittest

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import _NAV_ICON_SIZE, _NAV_ITEMS, _THEME_COLORS, nav_icon

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


def _ink(icon: QIcon, mode: QIcon.Mode) -> list[tuple[int, int, int]]:
    """RGB of the drawn pixels, most common first."""
    image = icon.pixmap(_NAV_ICON_SIZE, _NAV_ICON_SIZE, mode).toImage()
    counts: dict[tuple[int, int, int], int] = {}
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 40:
                key = (pixel.red(), pixel.green(), pixel.blue())
                counts[key] = counts.get(key, 0) + 1
    return [rgb for rgb, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def _assert_close(
    case: unittest.TestCase, actual: tuple[int, int, int], expected_hex: str
) -> None:
    """Compare against a colour, allowing for antialiasing.

    The dominant pixel lands a channel or two off the pen depending on how the
    platform rasterises text -- offscreen Qt differs from the Windows one -- so
    an exact match would be flaky rather than meaningful.
    """
    expected = tuple(int(expected_hex[i : i + 2], 16) for i in (1, 3, 5))
    drift = max(abs(a - b) for a, b in zip(actual, expected))
    case.assertLessEqual(
        drift, 8, f"drew {actual}, expected about {expected} ({expected_hex})"
    )


class SidebarIconTests(unittest.TestCase):
    """Every nav glyph has to be drawn in the pen colour.

    A plain keyboard or gear symbol resolves to the colour emoji font, whose
    glyphs are bitmaps: the pen does not reach them, so they kept one fixed
    colour in both themes and stayed that colour on the selected row while the
    label beside them turned white. The icon font is used instead.
    """

    def test_every_glyph_renders_something(self) -> None:
        for code_point, fallback, label in _NAV_ITEMS:
            with self.subTest(label=label):
                icon = nav_icon(code_point, fallback, "dark")

                self.assertTrue(_ink(icon, QIcon.Mode.Normal), "nothing drawn")

    def test_glyphs_take_the_theme_text_colour(self) -> None:
        for theme in ("light", "dark"):
            expected = _THEME_COLORS[theme]["text"]
            for code_point, fallback, label in _NAV_ITEMS:
                with self.subTest(theme=theme, label=label):
                    icon = nav_icon(code_point, fallback, theme)

                    _assert_close(self, _ink(icon, QIcon.Mode.Normal)[0], expected)

    def test_glyphs_take_the_selected_colour_on_the_current_row(self) -> None:
        for theme in ("light", "dark"):
            expected = _THEME_COLORS[theme]["sidebar_sel_text"]
            for code_point, fallback, label in _NAV_ITEMS:
                with self.subTest(theme=theme, label=label):
                    icon = nav_icon(code_point, fallback, theme)

                    _assert_close(self, _ink(icon, QIcon.Mode.Selected)[0], expected)

    def test_labels_carry_no_inline_glyph(self) -> None:
        # The glyph living in the label text was what made every row's text
        # start at a different x.
        for _code_point, _fallback, label in _NAV_ITEMS:
            with self.subTest(label=label):
                self.assertEqual(label, label.strip())
                self.assertTrue(label.isascii())


if __name__ == "__main__":
    unittest.main()
