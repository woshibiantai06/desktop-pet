"""Focused native-reader and time-controlled activity tests; no real input sent."""
import unittest

from input_activity import ActivityDetector, KeyboardActivity, WindowsKeyboardReader, TYPING_KEYS


class DetectorTests(unittest.TestCase):
    def test_burst_and_idle_edges(self):
        detector = ActivityDetector()
        self.assertFalse(detector.update(True, 0.0))
        self.assertFalse(detector.update(False, 0.1))
        self.assertTrue(detector.update(True, 0.2))
        self.assertTrue(detector.started)
        self.assertTrue(detector.update(False, 0.3))
        self.assertFalse(detector.started)
        self.assertTrue(detector.update(False, 2.19))
        self.assertFalse(detector.update(False, 2.21))
        self.assertTrue(detector.stopped)
        self.assertFalse(detector.update(False, 2.3))
        self.assertFalse(detector.stopped)

    def test_sparse_taps_do_not_start(self):
        detector = ActivityDetector()
        for start in (0.0, 1.0, 2.0):
            self.assertFalse(detector.update(True, start))
            self.assertFalse(detector.update(False, start + 0.05))

    def test_held_key_keeps_activity_without_repeat_edges(self):
        detector = ActivityDetector()
        self.assertFalse(detector.update(True, 0.0, 1))
        self.assertFalse(detector.update(True, 0.3, 0))
        self.assertTrue(detector.update(True, 0.36, 0))
        self.assertTrue(detector.started)
        self.assertTrue(detector.update(True, 20.0, 0))
        self.assertFalse(detector.started)
        self.assertTrue(detector.update(False, 20.1, 0))
        self.assertFalse(detector.update(False, 22.01, 0))

    def test_simultaneous_press_counts_and_cooldown(self):
        detector = ActivityDetector(cooldown=1.0)
        self.assertTrue(detector.update(2, 0.0, 2))
        self.assertFalse(detector.update(0, 2.1, 0))
        self.assertFalse(detector.update(2, 2.2, 2))
        self.assertFalse(detector.update(2, 2.8, 0))
        self.assertTrue(detector.update(2, 3.11, 0))

    def test_reset_clears_pending_burst(self):
        detector = ActivityDetector()
        detector.update(True, 0.0)
        detector.reset()
        self.assertFalse(detector.update(True, 0.2))
        self.assertFalse(detector.started)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.down = set()
        self.reader = WindowsKeyboardReader(lambda key: -32768 if key in self.down else 0,
                                             platform='win32')

    def test_modifiers_mouse_and_shortcuts_excluded(self):
        self.assertFalse(set((1, 2, 4, 5, 6, 0x10, 0x11, 0x12, 0x5B, 0x5C)) & set(TYPING_KEYS))
        self.down.update((1, 2, 0x10, 0xA0, 0xA1))
        self.assertEqual(self.reader.read().down_count, 0)
        self.down.add(0x41)  # Shift+A is ordinary typing.
        self.assertEqual(self.reader.read().transitions, 1)
        self.down.add(0x11)  # Ctrl+A is a shortcut.
        self.assertEqual(self.reader.read().down_count, 0)

    def test_replacement_key_and_hold(self):
        self.down.add(0x41)
        self.assertEqual(self.reader.read().transitions, 1)
        self.assertEqual(self.reader.read().transitions, 0)
        self.down = {0x42}  # Equal aggregate held count, but one actual new press.
        sample = self.reader.read()
        self.assertEqual((sample.down_count, sample.transitions), (1, 1))

    def test_low_bit_is_not_reliable_activity(self):
        reader = WindowsKeyboardReader(lambda key: 1, platform='win32')
        self.assertEqual(reader.read().down_count, 0)
        self.assertEqual(reader.read().transitions, 0)

    def test_non_windows_disabled(self):
        reader = WindowsKeyboardReader(lambda key: self.fail('should not call Windows API'),
                                        platform='linux')
        keyboard = KeyboardActivity(reader=reader)
        self.assertFalse(keyboard.enabled)
        self.assertFalse(keyboard.poll(0.0))
        self.assertFalse(keyboard.started)

    def test_api_failure_stops_once_and_disables(self):
        keyboard = KeyboardActivity(reader=self.reader)
        self.down.update((0x41, 0x42))
        self.assertTrue(keyboard.poll(0.0))
        self.assertTrue(keyboard.started)
        self.reader._get_key_state = lambda key: (_ for _ in ()).throw(OSError('unavailable'))
        self.assertFalse(keyboard.poll(0.1))
        self.assertFalse(keyboard.enabled)
        self.assertTrue(keyboard.stopped)
        self.assertFalse(keyboard.poll(0.2))
        self.assertFalse(keyboard.stopped)


if __name__ == '__main__':
    unittest.main(verbosity=2)
