import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("только Windows")

from ipwidget import taskbar
from ipwidget.uia import Item

RECT = (0, 1984, 3120, 2080)          # панель 3120x96, как на реальной машине (200 %)


def item(cls, left, right, top=1984, bottom=2080, off=False):
    return Item(cls, "", left, top, right, bottom, off)


class OccupiedTest(unittest.TestCase):
    def test_containers_are_dropped(self):
        items = [item("Taskbar.TaskbarFrameAutomationPeer", 0, 3120),
                 item("Windows.UI.Input.InputSite.WindowClass", 0, 1560, 1984, 2032),
                 item("ToggleButton", 943, 1033),
                 item("Taskbar.TaskListButtonAutomationPeer", 1033, 1121)]
        self.assertEqual(taskbar.occupied_from_items(items, RECT), [(943, 1121)])

    def test_own_windows_are_ignored(self):
        items = [item("ToggleButton", 943, 1033),
                 item("IPWidget2PlateWindow", 429, 923),
                 item("IPWidget2TipWindow-dev", 429, 1045, 1576, 1976),
                 item("IPWidgetWindowClass", 20, 500)]          # плашка прежней версии — занята
        self.assertEqual(taskbar.occupied_from_items(items, RECT), [(20, 500), (943, 1033)])

    def test_offscreen_and_empty_and_foreign_rows(self):
        items = [item("X", 100, 200, off=True), item("X", 300, 300), item("X", 400, 500, 100, 150),
                 item("X", 600, 700)]
        self.assertEqual(taskbar.occupied_from_items(items, RECT), [(600, 700)])

    def test_adjacent_items_merge(self):
        items = [item("X", 100, 200), item("X", 200, 300), item("X", 301, 400)]
        self.assertEqual(taskbar.occupied_from_items(items, RECT), [(100, 300), (301, 400)])


class ThemeHysteresisTest(unittest.TestCase):
    def test_holds_value_between_thresholds(self):
        tracker = taskbar.ThemeTracker()
        tracker.dark = True
        mid = (taskbar.ThemeTracker.LOW + taskbar.ThemeTracker.HIGH) / 2
        from unittest import mock
        info = taskbar.TaskbarInfo(1, RECT, (0, 0, 3120, 2080), 192)
        with mock.patch.object(taskbar.system, "accent_on_taskbar", return_value=True), \
                mock.patch.object(taskbar, "_sample_taskbar_luma", return_value=mid):
            self.assertTrue(tracker.update(info))
        with mock.patch.object(taskbar.system, "accent_on_taskbar", return_value=True), \
                mock.patch.object(taskbar, "_sample_taskbar_luma", return_value=200.0):
            self.assertFalse(tracker.update(info))
        with mock.patch.object(taskbar.system, "accent_on_taskbar", return_value=True), \
                mock.patch.object(taskbar, "_sample_taskbar_luma", return_value=mid):
            self.assertFalse(tracker.update(info))          # не «моргает» обратно
        with mock.patch.object(taskbar.system, "accent_on_taskbar", return_value=True), \
                mock.patch.object(taskbar, "_sample_taskbar_luma", return_value=20.0):
            self.assertTrue(tracker.update(info))

    def test_registry_is_used_without_accent(self):
        from unittest import mock
        tracker = taskbar.ThemeTracker()
        with mock.patch.object(taskbar.system, "accent_on_taskbar", return_value=False), \
                mock.patch.object(taskbar.system, "system_uses_light", return_value=True):
            self.assertFalse(tracker.update(None))
        with mock.patch.object(taskbar.system, "accent_on_taskbar", return_value=False), \
                mock.patch.object(taskbar.system, "system_uses_light", return_value=False):
            self.assertTrue(tracker.update(None))


if __name__ == "__main__":
    unittest.main()
