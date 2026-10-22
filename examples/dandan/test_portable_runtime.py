"""Portable frozen-branch checks without creating Qt windows or real pets."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import app_paths


SOURCE = Path(__file__).resolve().with_name('launch_pet.pyw')
LOADER = importlib.machinery.SourceFileLoader('dogpet_frozen_launcher_test', str(SOURCE))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
launcher = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(launcher)


class FrozenLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='dogpet-frozen-test-')
        self.root = Path(self.temporary.name)
        self.resources = self.root / '只读程序 中文 空格' / '_internal'
        self.resources.mkdir(parents=True)
        self.log = self.root / '其他用户 AppData' / 'logs' / 'launcher.log'
        self.old_cwd, self.old_argv, self.old_path = os.getcwd(), sys.argv[:], sys.path[:]
        self.stub = types.ModuleType('pet_alpha')
        self.stub.main = Mock(return_value=0)
        self.popups = []
        self.lock = Mock(return_value=(1234, False))
        self.unlock = Mock()
        for item in (patch.object(launcher, 'FROZEN', True), patch.object(launcher, 'ROOT', self.resources),
                     patch.object(launcher, 'LOG_PATH', self.log), patch.object(launcher, 'show_error', self.popups.append),
                     patch.object(launcher, 'acquire_instance_lock', self.lock),
                     patch.object(launcher, 'release_instance_lock', self.unlock),
                     patch.object(launcher.runpy, 'run_path', side_effect=AssertionError('Frozen app must not require .py source')),
                     patch.dict(sys.modules, {'pet_alpha': self.stub})):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.restore)

    def restore(self):
        os.chdir(self.old_cwd)
        sys.argv = self.old_argv
        sys.path[:] = self.old_path
        self.temporary.cleanup()

    def test_imported_main_runs_without_pet_alpha_py_source(self):
        self.assertFalse((self.resources / 'pet_alpha.py').exists())
        self.assertEqual(launcher.main(['--demo', '--duration', '1']), 0)
        self.stub.main.assert_called_once_with()
        self.assertEqual(sys.argv[1:], ['--demo', '--duration', '1'])
        self.lock.assert_called_once_with()
        self.unlock.assert_called_once_with(1234)
        self.assertEqual(self.popups, [])
        self.assertTrue(self.log.exists())

    def test_diagnostic_allow_multiple_is_removed_before_app_parser(self):
        self.assertEqual(launcher.main(['--allow-multiple', '--demo']), 0)
        self.assertEqual(sys.argv[1:], ['--demo'])
        self.lock.assert_not_called()
        self.stub.main.assert_called_once_with()

    def test_second_instance_returns_without_imported_app_start(self):
        self.lock.return_value = (5678, True)
        self.assertEqual(launcher.main([]), 0)
        self.stub.main.assert_not_called()
        self.unlock.assert_called_once_with(5678)
        self.assertIn('already_running', self.log.read_text(encoding='utf-8'))

    def test_pythonw_none_streams_are_redirected_and_closed(self):
        def emit():
            print('冻结程序 中文正常输出')
            print('冻结程序错误输出', file=sys.stderr)
            return 0
        self.stub.main.side_effect = emit
        with patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
            self.assertEqual(launcher.main([]), 0)
        text = self.log.read_text(encoding='utf-8')
        self.assertIn('冻结程序 中文正常输出', text)
        self.assertIn('冻结程序错误输出', text)

    def test_imported_main_failure_logs_and_releases_lock(self):
        self.stub.main.side_effect = RuntimeError('模拟素材缺失')
        self.assertEqual(launcher.main([]), 1)
        self.assertIn('模拟素材缺失', self.log.read_text(encoding='utf-8'))
        self.assertEqual(len(self.popups), 1)
        self.unlock.assert_called_once_with(1234)

    def test_nonzero_imported_main_return_is_not_silently_successful(self):
        self.stub.main.return_value = 2
        self.assertEqual(launcher.main([]), 2)
        self.assertEqual(len(self.popups), 1)
        self.assertIn('application_exit code=2', self.log.read_text(encoding='utf-8'))


class FrozenOutputPathTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows launcher notifications')
    def test_diagnostics_suppress_error_dialog_but_normal_launch_can_notify(self):
        with patch.object(launcher.ctypes.windll.user32, 'MessageBoxW') as message_box:
            with patch.dict(os.environ, {'DOGPET_DIAGNOSTIC_NO_DIALOG': '1'}):
                launcher.show_error('诊断失败不打扰用户')
            message_box.assert_not_called()
            with patch.dict(os.environ, {'DOGPET_DIAGNOSTIC_NO_DIALOG': '0'}):
                launcher.show_error('正常启动失败提示')
            message_box.assert_called_once()

    def test_local_appdata_not_program_directory_is_writable_target(self):
        with tempfile.TemporaryDirectory(prefix='dogpet-user-paths-') as folder:
            base = Path(folder) / '其他用户 中文 空格'
            denied = Path(folder) / '程序不可写且不存在'
            with patch.object(app_paths, 'FROZEN', True), patch.object(app_paths, 'RESOURCE_ROOT', denied):
                with patch.dict(os.environ, {'LOCALAPPDATA': str(base)}, clear=True):
                    output = app_paths.writable_output('desktop_pet.png')
                    self.assertEqual(output, base / 'DandanDesktopPet' / 'desktop_pet.png')
                    self.assertTrue(output.parent.is_dir())
                    self.assertFalse(denied.exists())

    def test_explicit_diagnostic_data_override_is_respected(self):
        with tempfile.TemporaryDirectory(prefix='dogpet-user-paths-') as folder:
            override = Path(folder) / '隔离测试'
            with patch.object(app_paths, 'FROZEN', True), patch.dict(os.environ, {'DOGPET_DATA_DIR': str(override)}):
                self.assertEqual(app_paths.writable_output('report.json'), override / 'report.json')


if __name__ == '__main__':
    unittest.main()
