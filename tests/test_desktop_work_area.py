"""Borderless maximization uses the current monitor's taskbar-aware bounds."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import desktop


class WorkAreaTests(unittest.TestCase):
    def test_windows_maximize_sets_working_area_on_ui_thread(self):
        area = object()
        form = SimpleNamespace(MaximizedBounds=None, WindowState='normal')
        form.Invoke = MagicMock(side_effect=lambda callback: callback())
        screen = SimpleNamespace(FromControl=MagicMock(return_value=SimpleNamespace(WorkingArea=area)))
        modules = {
            'System': SimpleNamespace(Action=lambda callback: callback),
            'System.Windows.Forms': SimpleNamespace(Screen=screen, FormWindowState=SimpleNamespace(Maximized='maximized')),
            'webview.platforms.winforms': SimpleNamespace(BrowserView=SimpleNamespace(instances={'test':form})),
        }
        with patch.dict(sys.modules, modules), patch.object(desktop.sys, 'platform', 'win32'):
            desktop._maximize_in_work_area(SimpleNamespace(uid='test'))
        self.assertIs(form.MaximizedBounds, area)
        self.assertEqual(form.WindowState, 'maximized')
        screen.FromControl.assert_called_once_with(form)
        form.Invoke.assert_called_once()

    def test_other_platforms_use_native_maximize(self):
        window = MagicMock()
        with patch.object(desktop.sys, 'platform', 'linux'):
            desktop._maximize_in_work_area(window)
        window.maximize.assert_called_once()
