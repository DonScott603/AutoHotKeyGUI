import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ahk_manager import (
    AppSettings,
    Expansion,
    ExpansionStore,
    _atomic_write_text,
    generate_ahk,
)


class AtomicWriteTests(unittest.TestCase):
    """Saving must never leave a half-written file behind.

    The store is rewritten on every edit now, so a failure part-way through a
    write would land on the only copy of the user's data.
    """

    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.dir = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_replaces_content_and_leaves_no_temp_file(self) -> None:
        path = self.dir / "thing.json"
        path.write_text("old", encoding="utf-8")

        _atomic_write_text(path, "new content\n")

        self.assertEqual(path.read_text(encoding="utf-8"), "new content\n")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_line_endings_match_the_previous_write_text_behaviour(self) -> None:
        # Changing the on-disk newline format would show up as a whole-file
        # diff in every consumer of these files.
        expected = (self.dir / "expected.txt")
        expected.write_text("a\nb\n", encoding="utf-8")
        actual = self.dir / "actual.txt"

        _atomic_write_text(actual, "a\nb\n")

        self.assertEqual(actual.read_bytes(), expected.read_bytes())

    def test_a_failed_write_leaves_the_original_intact(self) -> None:
        path = self.dir / "thing.json"
        path.write_text("original\n", encoding="utf-8")

        with mock.patch("ahk_manager.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                _atomic_write_text(path, "replacement\n")

        self.assertEqual(path.read_text(encoding="utf-8"), "original\n")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_store_save_survives_a_failed_write(self) -> None:
        path = self.dir / "expansions.json"
        store = ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";a", "first")]
        )
        store.save(path)
        original = path.read_bytes()

        store.expansions.append(Expansion("Work", ";b", "second"))
        with mock.patch("ahk_manager.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.save(path)

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(ExpansionStore.load(path).expansions[0].trigger, ";a")

    def test_generated_ahk_survives_a_failed_write(self) -> None:
        path = self.dir / "text_expansions.ahk"
        store = ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";a", "first")]
        )
        generate_ahk(store, path, backup=False)
        original = path.read_bytes()

        store.expansions.append(Expansion("Work", ";b", "second"))
        with mock.patch("ahk_manager.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                generate_ahk(store, path, backup=False)

        self.assertEqual(path.read_bytes(), original)

    def test_settings_save_round_trips(self) -> None:
        path = self.dir / "settings.json"
        AppSettings("C:/somewhere/text_expansions.ahk").save(path)

        self.assertEqual(
            AppSettings.load(path, Path("unused")).generated_ahk_path,
            "C:/somewhere/text_expansions.ahk",
        )


if __name__ == "__main__":
    unittest.main()
