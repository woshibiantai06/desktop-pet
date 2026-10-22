"""Deterministic speed-ramp checks; never opens a window or sends input."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from playback_timing import RateRamp


class RateRampTests(unittest.TestCase):
    def test_constant_initial_rate_and_default_target_are_stable(self):
        ramp = RateRamp()
        for now in (0.0, 0.1, 3.0, 100000.0):
            self.assertEqual(ramp.value(now), 1.0)
        self.assertAlmostEqual(ramp.integral(2.0, 4.0), 2.0)
        self.assertFalse(ramp.retarget(1.0, 4.0))

    def test_acceleration_is_continuous_monotonic_and_bounded(self):
        ramp = RateRamp(value=1.0, duration=0.36)
        self.assertTrue(ramp.retarget(1.85, 10.0))
        rates = [ramp.value(10.0 + n * 0.0036) for n in range(101)]
        self.assertEqual(rates[0], 1.0)
        self.assertAlmostEqual(rates[-1], 1.85)
        self.assertAlmostEqual(ramp.value(10.18), 1.425)
        self.assertTrue(all(1.0 <= rate <= 1.85 for rate in rates))
        self.assertTrue(all(left <= right for left, right in zip(rates, rates[1:])))
        # Smoothstep starts and ends with a near-zero acceleration.
        self.assertLess(ramp.value(10.0001) - 1.0, 0.000001)
        self.assertLess(1.85 - ramp.value(10.3599), 0.000001)

    def test_deceleration_retains_the_current_speed(self):
        ramp = RateRamp(value=1.0, duration=0.36)
        ramp.retarget(1.85, 1.0)
        before = ramp.value(1.14)
        self.assertTrue(ramp.retarget(1.0, 1.14))
        self.assertAlmostEqual(ramp.value(1.14), before)
        rates = [ramp.value(1.14 + n * 0.0036) for n in range(101)]
        self.assertTrue(all(1.0 <= rate <= before for rate in rates))
        self.assertTrue(all(left >= right for left, right in zip(rates, rates[1:])))
        self.assertAlmostEqual(rates[-1], 1.0)

    def test_retargeting_the_same_goal_does_not_restart_the_ramp(self):
        ramp = RateRamp(duration=0.36)
        ramp.retarget(1.85, 5.0)
        for now in (5.01, 5.10, 5.20, 5.35):
            self.assertFalse(ramp.retarget(1.85, now))
        self.assertAlmostEqual(ramp.value(5.36), 1.85)
        self.assertAlmostEqual(ramp.integral(5.0, 5.36), 0.36 * 1.425)

    def test_full_smoothstep_integral_has_the_symmetric_mean_rate(self):
        ramp = RateRamp(value=1.0, duration=0.36)
        ramp.retarget(1.85, 2.0)
        self.assertAlmostEqual(ramp.integral(2.0, 2.36), 0.36 * (1.0 + 1.85) / 2)
        self.assertAlmostEqual(ramp.integral(1.75, 2.76), 0.25 + 0.36 * 1.425 + 0.40 * 1.85)

    def test_exact_integral_matches_independent_numeric_quadrature(self):
        ramp = RateRamp(value=1.85, duration=0.36)
        ramp.retarget(1.0, 7.0)
        start, end, count = 6.91, 7.63, 20000
        step = (end - start) / count
        numeric = sum(ramp.value(start + (n + 0.5) * step) for n in range(count)) * step
        self.assertAlmostEqual(ramp.integral(start, end), numeric, places=9)

    def test_integral_is_additive_across_both_ramp_boundaries(self):
        ramp = RateRamp(duration=0.36)
        ramp.retarget(1.85, 3.0)
        boundaries = (2.8, 3.0, 3.07, 3.18, 3.36, 3.9)
        parts = sum(ramp.integral(left, right) for left, right in zip(boundaries, boundaries[1:]))
        self.assertAlmostEqual(parts, ramp.integral(boundaries[0], boundaries[-1]))
        self.assertEqual(ramp.integral(3.15, 3.15), 0.0)

    def test_settled_speed_and_integral_do_not_drift_over_long_sessions(self):
        ramp = RateRamp(duration=0.36)
        ramp.retarget(1.85, 123456.0)
        self.assertEqual(ramp.value(123500.0), 1.85)
        self.assertEqual(ramp.value(999999.0), 1.85)
        self.assertAlmostEqual(ramp.integral(123500.0, 133500.0), 18500.0)

    def test_media_clock_never_rewinds_when_goal_changes_mid_ramp(self):
        ramp = RateRamp(duration=0.36)
        ramp.retarget(1.85, 0.0)
        media = 0.0
        previous_time = 0.0
        for frame in range(1, 121):
            now = frame / 120.0
            advanced = ramp.integral(previous_time, now)
            self.assertGreater(advanced, 0.0)
            self.assertLessEqual(advanced, (now - previous_time) * 1.85 + 1e-12)
            media += advanced
            if frame == 20:
                before = ramp.value(now)
                ramp.retarget(1.0, now)
                self.assertAlmostEqual(ramp.value(now), before)
            if frame == 35:
                before = ramp.value(now)
                ramp.retarget(1.85, now)
                self.assertAlmostEqual(ramp.value(now), before)
            previous_time = now
        self.assertGreater(media, 1.0)
        self.assertLess(media, 1.85)


class RealTickTimingTests(unittest.TestCase):
    """Exercise the real renderer's clock math with no QApplication/window."""

    def make_player(self):
        from pet_alpha import Pet
        raw = np.zeros((1, 420, 420, 4), dtype=np.uint8)
        raw[0, :, :, :] = (80, 40, 20, 255)
        player = SimpleNamespace(
            quitting=False, exit_bridging=False, fading=False, waiting=False,
            assets={'sample': dict(start='stand_front', end='stand_front',
                                   fps=30, frames=300, hold_index=0)},
            key='sample', clip_start=0.0, activated_at=0.0, rate_tick=0.0,
            playback_rate=1.0, rate_ramp=RateRamp(), exit_requested=False,
            command_target=None, desired=1.85, dragging=False,
            data=raw, index=-1, skipped=0, pad=0,
            offset=np.zeros(2, dtype=int), shape_pivot=None,
            previous=np.zeros((420, 420, 4), dtype=np.uint8),
            blend_seconds=0.28,
        )
        player.interaction_rate = lambda: player.desired
        player.log = lambda *_args, **_kwargs: None
        player.update = lambda: None
        player.tick = lambda: Pet.tick(player)
        return player

    def tick_at(self, player, now):
        with patch('pet_alpha.time.perf_counter', return_value=now):
            player.tick()

    def test_actual_tick_uses_exact_media_integral_and_wall_time_blend(self):
        player = self.make_player()
        self.tick_at(player, 0.0)
        self.tick_at(player, 0.18)
        expected_media = player.rate_ramp.integral(0.0, 0.18)
        self.assertEqual(player.index, int(expected_media * 30))
        self.assertAlmostEqual(player.playback_rate, 1.425)
        self.assertAlmostEqual((0.18 - player.clip_start) * player.playback_rate, expected_media)
        progress = 0.18 / 0.28
        progress = progress * progress * (3 - 2 * progress)
        self.assertEqual(int(player.current[0, 0, 0]), int(80 * progress))

    def test_target_change_at_same_instant_does_not_jump_source_frames(self):
        player = self.make_player()
        self.tick_at(player, 0.0)
        self.tick_at(player, 0.18)
        frame_before = player.index
        source_before = (0.18 - player.clip_start) * player.playback_rate
        player.desired = 1.0
        self.tick_at(player, 0.18)
        self.assertEqual(player.index, frame_before)
        self.assertAlmostEqual(player.playback_rate, 1.425)
        self.assertAlmostEqual((0.18 - player.clip_start) * player.playback_rate, source_before)
        self.tick_at(player, 0.54)
        expected_media = source_before + 0.36 * (1.425 + 1.0) / 2
        self.assertEqual(player.index, int(expected_media * 30))
        self.assertAlmostEqual(player.playback_rate, 1.0)
        self.assertEqual(int(player.current[0, 0, 0]), 80)


if __name__ == '__main__':
    unittest.main()
