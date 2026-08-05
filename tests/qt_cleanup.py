"""Destroy the windows a test built, so the next test does not pay for them.

QMainWindow.close() only hides: the window, and every widget under it, stays
alive in the QApplication for the rest of the process. Each test class here
builds a window per test, so they piled up, and building one grew slower as the
pile grew -- 0.04s for the first, 7.8s for the fortieth. That is quadratic, and
it took this suite from 7 minutes to 40 and then past CI's 30-minute timeout
while the number of tests rose by less than a fifth.

Called at the end of tearDown rather than on each window, so a test that builds
one somewhere other than the fixture is covered too.
"""

import shiboken6
from PySide6.QtWidgets import QApplication


def destroy_all_windows() -> None:
    for widget in QApplication.topLevelWidgets():
        # A widget already destroyed through some other route -- a dialog that
        # deleted itself on close -- leaves a wrapper pointing at nothing.
        if shiboken6.isValid(widget):
            shiboken6.delete(widget)
