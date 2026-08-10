"""Stopping the previous script, and listing the ones that are not ours.

Generate & Run used to stop only processes running the script it was about to
launch. Point the app at a new path and the script at the old path kept
running beside the new one -- two copies of every trigger, so every expansion
fired twice. The app's own child process was not stopped either; it survived
only because the path match happened to catch it.

Ownership is now the rule: every path this app has generated to during the
session is its own and is stopped without asking. Anything else running
AutoHotkey belongs to the user and is only listed.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app as app_module
from ahk_manager import Expansion, ExpansionStore
from app import ExpansionApp, RunningScript, classify_running_scripts, normalized_path_for_compare
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])

AHK_EXE = r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"


def _process(pid: int, script: str, name: str = "AutoHotkey64.exe") -> dict[str, object]:
    return {"ProcessId": pid, "Name": name, "CommandLine": f'"{AHK_EXE}" "{script}"'}


class ClassificationTests(unittest.TestCase):
    """The rule that decides what may be stopped without being asked."""

    def _classify(self, processes: list[dict[str, object]], *owned: str) -> list[RunningScript]:
        return classify_running_scripts(
            processes, {normalized_path_for_compare(path) for path in owned}, current_pid=999
        )

    def test_a_script_at_an_owned_path_is_ours(self) -> None:
        found = self._classify([_process(1, r"C:\lib\text_expansions.ahk")], r"C:\lib\text_expansions.ahk")

        self.assertEqual([item.owned for item in found], [True])

    def test_a_script_at_a_previously_owned_path_is_still_ours(self) -> None:
        # The whole point: the path setting has moved on, but the script the
        # app started at the old path is still running and still ours.
        found = self._classify(
            [_process(1, r"C:\old\text_expansions.ahk"), _process(2, r"C:\new\text_expansions.ahk")],
            r"C:\old\text_expansions.ahk",
            r"C:\new\text_expansions.ahk",
        )

        self.assertEqual([item.owned for item in found], [True, True])

    def test_someone_elses_script_is_not_ours(self) -> None:
        found = self._classify(
            [_process(1, r"C:\Users\me\my_own_macros.ahk")], r"C:\lib\text_expansions.ahk"
        )

        self.assertEqual([item.owned for item in found], [False])
        self.assertEqual(found[0].scripts, [r"C:\Users\me\my_own_macros.ahk"])

    def test_a_non_autohotkey_process_is_ignored(self) -> None:
        found = self._classify([_process(1, r"C:\lib\a.ahk", name="notepad.exe")])

        self.assertEqual(found, [])

    def test_this_app_is_never_listed(self) -> None:
        processes = [{"ProcessId": 999, "Name": "AutoHotkey64.exe", "CommandLine": "x.ahk"}]

        self.assertEqual(self._classify(processes), [])

    def test_a_process_without_a_usable_pid_is_ignored(self) -> None:
        self.assertEqual(self._classify([{"Name": "AutoHotkey64.exe", "CommandLine": "x.ahk"}]), [])

    def test_ours_are_listed_first(self) -> None:
        found = self._classify(
            [_process(5, r"C:\other\theirs.ahk"), _process(9, r"C:\lib\ours.ahk")],
            r"C:\lib\ours.ahk",
        )

        self.assertEqual([item.pid for item in found], [9, 5])

    def test_a_process_with_no_script_path_is_listed_as_not_ours(self) -> None:
        found = self._classify([{"ProcessId": 3, "Name": "AutoHotkey.exe", "CommandLine": AHK_EXE}])

        self.assertEqual([(item.pid, item.owned, item.scripts) for item in found], [(3, False, [])])
        self.assertIn("no script path found", found[0].label())


class OwnershipTrackingTests(unittest.TestCase):
    """Which paths the window counts as its own as the setting moves."""

    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        self.root = root
        ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";one", "first")]
        ).save(root / "expansions.json")
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        )
        app_module.JSON_PATH = root / "expansions.json"
        app_module.SETTINGS_PATH = root / "settings.json"
        app_module.UI_PREFS_PATH = root / "ui_prefs.json"
        app_module.AHK_PATH = root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = root / "backups"
        self.app = ExpansionApp()

    def tearDown(self) -> None:
        self.app.close()
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        ) = self._saved_paths
        self._temp.cleanup()

    def _owns(self, path: Path) -> bool:
        return normalized_path_for_compare(path) in self.app._owned_ahk_paths

    def test_the_configured_path_is_owned_at_startup(self) -> None:
        self.assertTrue(self._owns(self.app.current_ahk_path()))

    def test_changing_the_path_keeps_the_old_one_owned(self) -> None:
        old_path = self.app.current_ahk_path()
        new_path = self.root / "elsewhere" / "moved.ahk"

        self.app.ahk_path_edit.setText(str(new_path))
        self.app.save_settings(announce=False)

        self.assertTrue(self._owns(old_path), "the previous script path stopped being ours")
        self.assertTrue(self._owns(new_path))

    def test_a_stale_script_at_the_old_path_is_stopped_by_a_run(self) -> None:
        # The reported bug, end to end: change the path, generate, and the
        # process still running the old script must be among those stopped.
        old_path = self.app.current_ahk_path()
        new_path = self.root / "moved.ahk"
        self.app.ahk_path_edit.setText(str(new_path))
        self.app.save_settings(announce=False)

        stopped: list[int] = []
        with mock.patch.object(
            ExpansionApp,
            "_running_autohotkey_processes",
            return_value=[
                _process(11, str(old_path)),
                _process(12, str(new_path)),
                _process(13, str(self.root / "someone_elses.ahk")),
            ],
        ), mock.patch.object(
            ExpansionApp, "_stop_process", side_effect=lambda pid: stopped.append(pid) or 1
        ):
            terminated = self.app._terminate_matching_ahk_processes(new_path)

        self.assertEqual(sorted(stopped), [11, 12])
        self.assertEqual(terminated, 2)

    def test_a_foreign_script_is_never_stopped_by_a_run(self) -> None:
        stopped: list[int] = []
        with mock.patch.object(
            ExpansionApp,
            "_running_autohotkey_processes",
            return_value=[_process(21, str(self.root / "my_own_macros.ahk"))],
        ), mock.patch.object(
            ExpansionApp, "_stop_process", side_effect=lambda pid: stopped.append(pid) or 1
        ):
            terminated = self.app._terminate_matching_ahk_processes(self.app.current_ahk_path())

        self.assertEqual(stopped, [])
        self.assertEqual(terminated, 0)

    def test_the_apps_own_process_is_stopped_by_handle(self) -> None:
        # Even with nothing matching by path -- the query may not return a
        # command line at all -- the child this app started must not survive.
        process = mock.Mock()
        process.poll.return_value = None
        process.pid = 31
        self.app.ahk_process = process

        with mock.patch.object(ExpansionApp, "_running_autohotkey_processes", return_value=[]):
            terminated = self.app._terminate_matching_ahk_processes(self.app.current_ahk_path())

        process.terminate.assert_called_once()
        self.assertEqual(terminated, 1)
        self.assertIsNone(self.app.ahk_process)

    def test_an_already_exited_process_is_not_stopped_again(self) -> None:
        process = mock.Mock()
        process.poll.return_value = 0
        self.app.ahk_process = process

        with mock.patch.object(ExpansionApp, "_running_autohotkey_processes", return_value=[]):
            terminated = self.app._terminate_matching_ahk_processes(self.app.current_ahk_path())

        process.terminate.assert_not_called()
        self.assertEqual(terminated, 0)

    def test_a_process_that_ignores_terminate_is_killed(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = app_module.subprocess.TimeoutExpired("ahk", 5)
        self.app.ahk_process = process

        self.assertEqual(self.app._stop_own_ahk_process(), 1)
        process.kill.assert_called_once()


class RunningScriptsDialogTests(unittest.TestCase):
    """The list the user acts on, and what it hands back."""

    def setUp(self) -> None:
        destroy_all_windows()
        self.scripts = [
            RunningScript(11, "AutoHotkey64.exe", [r"C:\lib\ours.ahk"], owned=True),
            RunningScript(12, "AutoHotkey64.exe", [r"C:\mine\theirs.ahk"], owned=False),
        ]

    def test_nothing_is_ticked_to_begin_with(self) -> None:
        dialog = app_module.RunningScriptsDialog(None, self.scripts)

        self.assertEqual(dialog._checked_pids(), [])
        dialog.deleteLater()

    def test_stopping_returns_only_the_ticked_rows(self) -> None:
        dialog = app_module.RunningScriptsDialog(None, self.scripts)
        dialog._list.item(1).setCheckState(app_module.Qt.CheckState.Checked)

        with mock.patch.object(app_module, "confirm", return_value=True):
            dialog._stop_selected()

        self.assertEqual(dialog.chosen_pids, [12])
        dialog.deleteLater()

    def test_declining_the_confirmation_stops_nothing(self) -> None:
        dialog = app_module.RunningScriptsDialog(None, self.scripts)
        dialog._list.item(0).setCheckState(app_module.Qt.CheckState.Checked)

        with mock.patch.object(app_module, "confirm", return_value=False):
            dialog._stop_selected()

        self.assertEqual(dialog.chosen_pids, [])
        dialog.deleteLater()

    def test_stopping_with_nothing_ticked_says_so(self) -> None:
        dialog = app_module.RunningScriptsDialog(None, self.scripts)

        with mock.patch.object(app_module, "show_info") as info:
            dialog._stop_selected()

        info.assert_called_once()
        self.assertEqual(dialog.chosen_pids, [])
        dialog.deleteLater()

    def test_ours_is_marked_in_the_label(self) -> None:
        self.assertIn("this app's script", self.scripts[0].label())
        self.assertNotIn("this app's script", self.scripts[1].label())


if __name__ == "__main__":
    unittest.main()
