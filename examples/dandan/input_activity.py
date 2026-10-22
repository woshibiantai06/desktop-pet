"""Aggregate owner-typing activity, with no hooks, text decoding, or key log.

Integration (the caller owns its Qt timer, preferably every 40--50 ms)::

    keyboard = KeyboardActivity()
    active = keyboard.poll(time.monotonic())
    if keyboard.started:
        # Request the keyboard animation through the pet's normal pose routing.
        ...
    if keyboard.stopped:
        # Finish that interaction and return through a safe transition.
        ...

``poll`` returns the debounced activity level. ``started`` / ``stopped`` are
one-poll edges. Call ``reset`` when suspending interactions (exit, dragging,
manual commands) so old activity does not trigger on resume. Native polling
reads only current physical down states. A transient bit mask detects new
presses; it is replaced on each poll and never exposed, logged, or persisted.
The public sample contains only counts. There is no input interception,
foreground-window inspection, text conversion, disk I/O, or network activity.

Polling may miss very brief taps between timer ticks. A held ordinary key
counts as ongoing activity; modifiers, mouse buttons and Ctrl/Alt/Win shortcuts
do not. Windows can return zero when the desktop is inaccessible, which is
indistinguishable from no keys held and is treated as idle. Secure desktops
are not monitored. GetAsyncKeyState's unreliable low bit is never used:
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate
"""

from collections import deque
from dataclasses import dataclass
import ctypes
import math
import sys
import time


# Letters, digits, editing/navigation keys, numpad, and ordinary punctuation.
# No mouse buttons, modifiers, lock/system/media keys, Escape, or function keys.
TYPING_KEYS = (
    tuple(range(0x30, 0x3A))
    + tuple(range(0x41, 0x5B))
    + (0x08, 0x09, 0x0D, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E)
    + tuple(range(0x60, 0x70))
    + (0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE, 0xE2)
)
_SHORTCUT_MODIFIERS = (0x11, 0x12, 0x5B, 0x5C)  # Ctrl, Alt, either Windows key.


@dataclass(frozen=True)
class ActivitySample:
    """Counts only; no key identifiers or characters leave the native reader."""

    down_count: int = 0
    transitions: int = 0


class ActivityDetector:
    """Pure, monotonic-time debounce for aggregate keyboard observations.

    ``update(pressed, now, transitions=None)`` accepts a bool or aggregate held
    count. If explicit new-down transitions are unavailable, increases of the
    aggregate count are the conservative fallback. Two presses in 0.8 seconds
    or a continuous 0.35-second hold start activity. An active interaction ends
    after 2 seconds without any key held or observed new press. A 1-second
    restart cooldown keeps isolated later taps from rapidly toggling the pet.
    All thresholds are configurable, with no dependence on Qt or Windows.
    """

    def __init__(self, min_presses=2, burst_window=0.8, hold_time=0.35,
                 idle_timeout=2.0, cooldown=1.0):
        if not isinstance(min_presses, int) or isinstance(min_presses, bool) or min_presses < 1:
            raise ValueError('min_presses must be a positive integer')
        values = (burst_window, hold_time, idle_timeout, cooldown)
        if any(not math.isfinite(v) or v < 0 for v in values) or burst_window == 0 or idle_timeout == 0:
            raise ValueError('timeouts must be finite and nonnegative; burst/idle must be positive')
        self.min_presses = min_presses
        self.burst_window = float(burst_window)
        self.hold_time = float(hold_time)
        self.idle_timeout = float(idle_timeout)
        self.cooldown = float(cooldown)
        self.reset()

    def reset(self):
        """Clear activity, pending bursts and cooldown; emits no callback/edge."""
        self.active = self.started = self.stopped = False
        self._presses = deque(maxlen=self.min_presses)
        self._held_since = self._last_activity = self._last_time = None
        self._last_count = 0
        self._next_start = float('-inf')

    def update(self, pressed, now, transitions=None):
        now = float(now)
        if not math.isfinite(now):
            raise ValueError('now must be finite monotonic seconds')
        count = max(0, int(pressed))
        self.started = self.stopped = False
        if self._last_time is not None and now < self._last_time:
            was_active = self.active
            self.reset()
            self.stopped = was_active
        self._last_time = now
        presses = max(0, count - self._last_count) if transitions is None else max(0, int(transitions))
        self._last_count = count
        while self._presses and now - self._presses[0] > self.burst_window:
            self._presses.popleft()
        for _ in range(min(presses, self.min_presses)):
            self._presses.append(now)
        if count:
            if self._held_since is None:
                self._held_since = now
            self._last_activity = now
        else:
            self._held_since = None
        if presses:
            self._last_activity = now
        if self.active:
            if self._last_activity is not None and now - self._last_activity >= self.idle_timeout:
                self.active = False
                self.stopped = True
                self._next_start = now + self.cooldown
                self._presses.clear()
        else:
            held = self._held_since is not None and now - self._held_since >= self.hold_time
            burst = len(self._presses) >= self.min_presses
            if now >= self._next_start and (held or burst):
                self.active = self.started = True
                self._presses.clear()
        return self.active


class WindowsKeyboardReader:
    """Read aggregate current-down state; gracefully disabled off Windows.

    ``get_key_state`` and ``platform`` are optional injection seams for tests.
    API exceptions disable the reader for its remaining lifetime. A zero result
    is valid idle input, not a detectable API error. The low bit is ignored.
    """

    def __init__(self, get_key_state=None, platform=None):
        self.enabled = False
        self.disabled_reason = ''
        self._previous_mask = 0
        self._get_key_state = None
        if (sys.platform if platform is None else platform) != 'win32':
            self.disabled_reason = 'Keyboard activity is available only on Windows.'
            return
        try:
            if get_key_state is None:
                get_key_state = ctypes.WinDLL('user32', use_last_error=True).GetAsyncKeyState
                get_key_state.argtypes = (ctypes.c_int,)
                get_key_state.restype = ctypes.c_short
            self._get_key_state = get_key_state
            self.enabled = True
        except Exception:
            self.disabled_reason = 'Windows keyboard activity API is unavailable.'

    def reset(self):
        self._previous_mask = 0

    def read(self):
        if not self.enabled:
            return ActivitySample()
        try:
            if any(self._get_key_state(key) & 0x8000 for key in _SHORTCUT_MODIFIERS):
                self._previous_mask = 0
                return ActivitySample()
            mask = 0
            for bit, key in enumerate(TYPING_KEYS):
                if self._get_key_state(key) & 0x8000:
                    mask |= 1 << bit
            sample = ActivitySample(mask.bit_count(), (mask & ~self._previous_mask).bit_count())
            self._previous_mask = mask
            return sample
        except Exception:
            self.enabled = False
            self.disabled_reason = 'Windows keyboard activity API stopped responding.'
            self.reset()
            return ActivitySample()


class KeyboardActivity:
    """Timer-friendly facade returning a bool plus started/stopped edge flags."""

    def __init__(self, detector=None, reader=None):
        self.detector = detector if detector is not None else ActivityDetector()
        self.reader = reader if reader is not None else WindowsKeyboardReader()
        self.active = self.started = self.stopped = False

    @property
    def enabled(self):
        return self.reader.enabled

    @property
    def disabled_reason(self):
        return self.reader.disabled_reason

    def reset(self):
        self.detector.reset()
        self.reader.reset()
        self.active = self.started = self.stopped = False

    def poll(self, now=None):
        sample = self.reader.read()
        if not self.reader.enabled:
            was_active = self.active
            self.detector.reset()
            self.active = self.started = False
            self.stopped = was_active
            return False
        self.active = self.detector.update(sample.down_count,
                                           time.monotonic() if now is None else now,
                                           sample.transitions)
        self.started, self.stopped = self.detector.started, self.detector.stopped
        return self.active
