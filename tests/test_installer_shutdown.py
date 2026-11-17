# -*- coding: utf-8 -*-
"""安装器：覆盖安装前自动识别并关闭正在运行的 PaperWhale。

这组测试不真的去杀进程（危险且不可控），只验证判定与顺序：
1. 安装流程里「关程序」必须发生在解压之前，且不再只靠 taskkill /IM；
2. 关闭策略是先发 WM_CLOSE，关不掉才强杀；
3. 关不掉时要抛 AppRunningError，让安装器提示用户，而不是装一半失败；
4. 进程列表解析要跳过安装器自己、跳过坏行。
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SETUP_PATH = Path(__file__).resolve().parent.parent / "installer" / "setup.py"
SETUP_SOURCE = SETUP_PATH.read_text(encoding="utf-8")


def load_installer():
    spec = importlib.util.spec_from_file_location("pw_installer_setup", SETUP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.setup = load_installer()

    # ------------------------------------------------------------- 关闭策略
    def test_no_running_instance_is_a_no_op(self):
        with patch.object(self.setup, "list_running_instances", return_value=[]):
            ok, closed = self.setup.close_running_app()
        self.assertTrue(ok)
        self.assertEqual(closed, [])

    def test_graceful_close_is_preferred_over_killing(self):
        instances = [{"pid": 4242, "path": r"D:\论文鲸\PaperWhale.exe"}]
        with patch.object(self.setup, "list_running_instances", return_value=instances), patch.object(
            self.setup, "_pid_alive", return_value=True
        ), patch.object(self.setup, "request_window_close", return_value=True) as graceful, patch.object(
            self.setup, "terminate_process"
        ) as kill, patch.object(self.setup, "time") as fake_time:
            fake_time.sleep.return_value = None
            ok, closed = self.setup.close_running_app()
        self.assertTrue(ok)
        graceful.assert_called_once()
        kill.assert_not_called()
        self.assertEqual(closed[0]["how"], "window-close")

    def test_falls_back_to_terminate_when_window_close_does_not_work(self):
        instances = [{"pid": 4242, "path": r"D:\论文鲸\PaperWhale.exe"}]
        with patch.object(self.setup, "list_running_instances", return_value=instances), patch.object(
            self.setup, "_pid_alive", return_value=True
        ), patch.object(self.setup, "request_window_close", return_value=False), patch.object(
            self.setup, "terminate_process", return_value=True
        ) as kill, patch.object(self.setup, "time") as fake_time:
            fake_time.sleep.return_value = None
            ok, closed = self.setup.close_running_app()
        self.assertTrue(ok)
        kill.assert_called_once_with(4242)
        self.assertEqual(closed[0]["how"], "terminate")

    def test_reports_failure_when_process_survives(self):
        instances = [{"pid": 4242, "path": r"D:\论文鲸\PaperWhale.exe"}]
        with patch.object(self.setup, "list_running_instances", return_value=instances), patch.object(
            self.setup, "_pid_alive", return_value=True
        ), patch.object(self.setup, "request_window_close", return_value=False), patch.object(
            self.setup, "terminate_process", return_value=False
        ), patch.object(self.setup, "time") as fake_time:
            fake_time.sleep.return_value = None
            ok, closed = self.setup.close_running_app()
        self.assertFalse(ok)
        self.assertEqual(len(closed), 0)

    # ------------------------------------------------------------- 进程解析
    def test_process_list_skips_own_pid_and_bad_lines(self):
        import os

        output = (
            "111|D:\\论文鲸\\PaperWhale.exe\n"
            f"{os.getpid()}|D:\\installer\\PaperWhale-Setup.exe\n"
            "not-a-pid|D:\\x\\PaperWhale.exe\n"
            "\n"
            "222|\n"
        )
        with patch.object(self.setup, "_run_powershell", return_value=output):
            pids = [item["pid"] for item in self.setup.list_running_instances()]
        self.assertEqual(pids, [111, 222])

    # --------------------------------------------------------- 安装流程顺序
    def test_install_closes_app_before_extracting_files(self):
        body = SETUP_SOURCE.split("def install(payload: Path", 1)[1]
        close_at = body.find("close_running_app()")
        extract_at = body.find("extractall(target)")
        self.assertGreater(close_at, -1, "install() 必须先自动关闭正在运行的程序")
        self.assertGreater(extract_at, -1)
        self.assertLess(close_at, extract_at, "关程序要发生在写文件之前，否则 exe 被占用照样装不进去")

    def test_install_no_longer_blindly_taskkills_by_image_name(self):
        body = SETUP_SOURCE.split("def install(payload: Path", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("taskkill", body, "安装流程要走自动识别+先礼后兵，不能只按镜像名盲杀")
        # 卸载脚本里按镜像名清理是另一回事，不受影响
        self.assertIn("close_running_app()", body)

    def test_install_raises_when_app_cannot_be_closed(self):
        import tempfile

        payload = Path(__file__).resolve().parent / "fixtures" / "attention.pdf"
        with tempfile.TemporaryDirectory(prefix="pw-installer-") as temp:
            with patch.object(self.setup, "close_running_app", return_value=(False, [{"pid": 4242}])):
                with self.assertRaises(self.setup.AppRunningError):
                    self.setup.install(payload, Path(temp) / "target", "9.9.9", launch=False)

    def test_main_tells_user_how_to_recover(self):
        # 提示文案必须给出可执行的下一步，而不是只说失败
        self.assertIn("自动关闭它失败", SETUP_SOURCE)
        self.assertIn("以管理员身份运行", SETUP_SOURCE)


if __name__ == "__main__":
    unittest.main()
