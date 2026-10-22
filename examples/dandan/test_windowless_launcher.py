"""Exercise the windowless launcher without opening Qt or another desktop pet."""
import importlib.machinery
import importlib.util
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().with_name('launch_pet.pyw')
LOADER = importlib.machinery.SourceFileLoader('dogpet_launcher_under_test', str(SOURCE))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
launcher = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(launcher)


class WindowlessLauncherTests(unittest.TestCase):
    def setUp(self):
        self.previous_cwd = os.getcwd()
        self.previous_argv = sys.argv[:]
        self.previous_path = sys.path[:]
        self.temporary = tempfile.TemporaryDirectory(prefix='dogpet_launcher_test_')
        self.root = Path(self.temporary.name) / '中文 空格目录'
        self.root.mkdir()
        self.log_path = self.root / 'logs' / 'launcher.log'
        self.popups = []
        self.captured = {}
        self.root_patch = patch.object(launcher, 'ROOT', self.root)
        self.log_patch = patch.object(launcher, 'LOG_PATH', self.log_path)
        self.popup_patch = patch.object(launcher, 'show_error', self.popups.append)
        for item in (self.root_patch, self.log_patch, self.popup_patch):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.restore_process_context)

    def restore_process_context(self):
        os.chdir(self.previous_cwd)
        sys.argv = self.previous_argv
        sys.path[:] = self.previous_path
        self.temporary.cleanup()

    def run_stub(self, behavior, argv=None, none_streams=False):
        def fake_run(path, run_name):
            self.captured.update(
                path=path, run_name=run_name, argv=sys.argv[:],
                cwd=os.getcwd(), import_path=sys.path[0],
            )
            print('中文日志：站姿摸头', flush=True)
            print('错误输出也可见', file=sys.stderr, flush=True)
            return behavior()

        with patch.object(launcher.runpy, 'run_path', fake_run):
            if none_streams:
                with patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
                    return launcher.main(argv)
            return launcher.main(argv)

    def log_text(self):
        return self.log_path.read_text(encoding='utf-8')

    def assert_launch_context(self, args):
        self.assertEqual(self.captured['path'], str(self.root / 'pet_alpha.py'))
        self.assertEqual(self.captured['run_name'], '__main__')
        self.assertEqual(self.captured['argv'], [str(self.root / 'pet_alpha.py'), *args])
        self.assertEqual(self.captured['cwd'], str(self.root))
        self.assertEqual(self.captured['import_path'], str(self.root))
        self.assertIn('中文日志：站姿摸头', self.log_text())
        self.assertIn('错误输出也可见', self.log_text())

    def test_forwards_original_arguments_with_unicode_and_spaces(self):
        args = ['--demo', '--report', str(self.root / '报告 空格.json')]
        sys.argv = ['launch_pet.pyw', *args]
        self.assertEqual(self.run_stub(lambda: None), 0)
        self.assert_launch_context(args)
        self.assertEqual(self.popups, [])

    def test_pythonw_none_streams_and_explicit_arguments(self):
        args = ['--report', str(self.root / '中文 报告.json')]
        self.assertEqual(self.run_stub(lambda: None, args, none_streams=True), 0)
        self.assert_launch_context(args)
        self.assertEqual(self.popups, [])

    def test_normal_system_exit_never_shows_error(self):
        for code in (None, 0):
            with self.subTest(code=code):
                def finish():
                    raise SystemExit(code)
                self.assertEqual(self.run_stub(finish, []), 0)
        self.assertEqual(self.popups, [])

    def test_nonzero_system_exit_logs_and_notifies(self):
        for code, expected in ((2, 2), ('invalid mode', 1)):
            with self.subTest(code=code):
                def finish():
                    raise SystemExit(code)
                self.assertEqual(self.run_stub(finish, []), expected)
        self.assertEqual(len(self.popups), 2)
        self.assertIn('application_exit code=2', self.log_text())
        self.assertIn('detail=invalid mode', self.log_text())
        self.assertTrue(all(str(self.log_path) in message for message in self.popups))

    def test_application_exception_has_traceback_and_error_message(self):
        def fail():
            raise RuntimeError('模拟中文异常')
        self.assertEqual(self.run_stub(fail, []), 1)
        self.assertEqual(len(self.popups), 1)
        self.assertIn('windowless_launch_failed', self.log_text())
        self.assertIn('RuntimeError: 模拟中文异常', self.log_text())

    def test_unwritable_log_directory_does_not_fail_silently(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError('directory denied')):
            with patch.object(launcher.runpy, 'run_path') as run_app:
                self.assertEqual(launcher.main([]), 1)
                run_app.assert_not_called()
        self.assertEqual(len(self.popups), 1)
        self.assertIn(str(self.log_path), self.popups[0])

    def test_unwritable_log_file_does_not_fail_silently(self):
        with patch.object(launcher, 'RotatingFileHandler', side_effect=PermissionError('file denied')):
            with patch.object(launcher.runpy, 'run_path') as run_app:
                self.assertEqual(launcher.main([]), 1)
                run_app.assert_not_called()
        self.assertEqual(len(self.popups), 1)

    def test_output_log_rotates_with_bounded_backups(self):
        requested = {}

        def compact_handler(path, **kwargs):
            requested.update(kwargs)
            return RotatingFileHandler(path, maxBytes=512, backupCount=2, encoding='utf-8')

        def write_many_lines():
            for index in range(100):
                print(f'轮换日志 {index:03d} ' + 'x' * 64, flush=True)

        with patch.object(launcher, 'RotatingFileHandler', compact_handler):
            self.assertEqual(self.run_stub(write_many_lines, []), 0)
        self.assertEqual(requested['maxBytes'], 2 * 1024 * 1024)
        self.assertEqual(requested['backupCount'], 2)
        self.assertEqual(requested['encoding'], 'utf-8')
        logs = list(self.log_path.parent.glob('launcher.log*'))
        self.assertEqual(len(logs), 3)
        self.assertTrue(all(path.stat().st_size <= 512 for path in logs))
        self.assertIn('轮换日志 099', self.log_text())
        self.assertEqual(logging.getLogger('dogpet.windowless').handlers, [])


if __name__ == '__main__':
    unittest.main()
