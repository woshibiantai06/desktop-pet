"""Headless timing tests: continuous source positions, never remove a pose."""
import unittest

from response_timeline import QuietTimeline, QUIET_RANGES, QUIET_RATE, timeline_for


class QuietTimelineTests(unittest.TestCase):
    def test_identity_including_fractional_frames(self):
        timeline = QuietTimeline({'frames':123, 'fps':30})
        self.assertAlmostEqual(timeline.duration, 4.1)
        for i in range(493):
            frame = i/4
            self.assertAlmostEqual(timeline.media_seconds(frame), frame/30, places=12)
            self.assertAlmostEqual(timeline.source_position(frame/30), frame, places=10)

    def test_all_configured_actions_compress_but_keep_every_frame_interval(self):
        counts={'head_up':123,'side_return':242,'sit_up':122,'walk_left_enter':114,'walk_right_enter':125}
        for key, frames in counts.items():
            with self.subTest(key=key):
                timeline=timeline_for(key,{'frames':frames,'fps':30},True)
                self.assertLess(timeline.duration, frames/30)
                self.assertGreater(timeline.duration, frames/(30*QUIET_RATE))
                times=[timeline.media_seconds(i) for i in range(frames+1)]
                self.assertTrue(all(a<b for a,b in zip(times,times[1:])))
                for frame in range(frames+1):
                    self.assertAlmostEqual(timeline.source_position(times[frame]),frame,places=10)

    def test_mutual_inverse_and_monotonicity_inside_frame_intervals(self):
        timeline=timeline_for('head_up',{'frames':123,'fps':30},True)
        positions=[]
        for i in range(2001):
            seconds=timeline.duration*i/2000
            frame=timeline.source_position(seconds)
            self.assertAlmostEqual(timeline.media_seconds(frame),seconds,places=12)
            positions.append(frame)
        self.assertTrue(all(a<b for a,b in zip(positions,positions[1:])))

    def test_endpoints_clamp_without_skipping_zero_or_the_final_frame(self):
        timeline=QuietTimeline({'frames':123,'fps':30},[(0,30),(90,123)])
        self.assertEqual(timeline.source_position(-1),0)
        self.assertEqual(timeline.source_position(0),0)
        self.assertEqual(timeline.source_position(timeline.duration),123)
        self.assertEqual(timeline.source_position(100),123)
        self.assertLess(timeline.source_position(timeline.duration-1e-8),123)
        self.assertGreater(timeline.source_position(timeline.duration-1e-8),122)
        self.assertGreater(timeline.media_seconds(1),0)
        self.assertLess(timeline.media_seconds(122),timeline.duration)

    def test_four_frame_smoothstep_shoulder_and_quiet_plateau(self):
        timeline=QuietTimeline({'frames':90,'fps':30},[(20,60)])
        rates=[1/w for w in timeline.weights]
        self.assertEqual(rates[19],1)
        self.assertEqual(rates[60],1)
        self.assertTrue(all(a<b for a,b in zip(rates[20:24],rates[21:25])))
        self.assertTrue(all(a>b for a,b in zip(rates[55:59],rates[56:60])))
        self.assertAlmostEqual(rates[30],QUIET_RATE)
        self.assertLess(rates[20]-1,0.06)
        self.assertLess(QUIET_RATE-rates[23],0.06)

    def test_nonurgent_and_unknown_assets_are_identity(self):
        clip={'frames':123,'fps':30}
        for key,urgent in [('head_up',False),('typing_enter',True),('pet_stand',True)]:
            timeline=timeline_for(key,clip,urgent)
            self.assertAlmostEqual(timeline.duration,4.1)
            self.assertTrue(all(w==1 for w in timeline.weights))

    def test_logical_action_ranges_do_not_accidentally_reverse_archive_metadata(self):
        timeline=timeline_for('side_return',{'frames':242,'fps':30,'frame_start':300,'reverse':True},True)
        self.assertAlmostEqual(timeline.weights[0],1/QUIET_RATE)
        self.assertEqual(timeline.weights[120],1)

    def test_reverse_prefix_uses_original_logical_indices(self):
        clip={'frames':61,'fps':30,'reverse':True,'frame_start':700,
              'timing_source_key':'sit_up','timing_source_frames':122,
              'timing_indices':list(range(60,-1,-1))}
        timeline=timeline_for('command_return_prefix',clip,True)
        original=timeline_for('sit_up',{'frames':122,'fps':30},True)
        self.assertEqual(timeline.weights,tuple(reversed(original.weights[:61])))
        self.assertEqual(timeline.weights[0],1)
        self.assertAlmostEqual(timeline.weights[-1],1/QUIET_RATE)
        for i in range(61*3+1):
            frame=i/3
            self.assertAlmostEqual(timeline.source_position(timeline.media_seconds(frame)),frame,places=10)

    def test_compact_reverse_prefix_and_explicit_indices_are_identical(self):
        base={'frames':61,'fps':30,'timing_source_key':'sit_up','timing_source_frames':122}
        compact=timeline_for('prefix',dict(base,timing_source_frame_start=60,timing_reverse=True),True)
        explicit=timeline_for('prefix',dict(base,timing_indices=list(range(60,-1,-1))),True)
        self.assertEqual(compact.weights,explicit.weights)

    def test_empty_or_invalid_metadata_is_rejected(self):
        for clip in ({'frames':0,'fps':30},{'frames':123,'fps':0},{'frames':1,'fps':float('inf')}):
            with self.assertRaises(ValueError):QuietTimeline(clip)
        with self.assertRaises(ValueError):
            timeline_for('prefix',{'frames':2,'fps':30,'timing_source_key':'sit_up','timing_indices':[0]},True)
        with self.assertRaises(ValueError):
            timeline_for('prefix',{'frames':2,'fps':30,'timing_source_key':'sit_up','timing_source_frames':122,'timing_indices':[0,122]},True)

    def test_paws_to_typing_budget_at_separate_three_point_five_rate(self):
        route=[('head_up',123),('lie_up',123),('sit_up',122),('typing_enter',126)]
        media_total=sum(timeline_for(key,{'frames':frames,'fps':30},True).duration for key,frames in route)
        wall_total=media_total/3.5
        # Overall smooth acceleration / loading belongs to the player.  This
        # budget is comfortably below 5 s, without any removed source interval.
        self.assertGreater(wall_total,3.5)
        self.assertLess(wall_total,4.7)


if __name__=='__main__':unittest.main()
