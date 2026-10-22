"""Urgent owner requests: legal, prompt routes without windows or real input."""
from types import SimpleNamespace
import unittest

import numpy as np

from interaction_runtime import plan_route
from pet_alpha import Pet, frame_at
from test_interaction_logic import FakePlayer


class UrgentPlayer(FakePlayer):
    request_exit = Pet.request_exit

    def __init__(self):
        super().__init__()
        self.urgency_context = None
        self.tray = SimpleNamespace(setToolTip=lambda text: None)
        self.radial.dismiss = lambda: None


class UrgentRoutingTests(unittest.TestCase):
    def test_waiting_unplayed_lower_head_can_be_replanned_from_actual_pose(self):
        pet = UrgentPlayer()
        pet.state, pet.key = 'lie_front', 'lie_quiet'
        pet.pending, pet.waiting = 'head_down', True
        pet.start_stream('typing')
        self.assertEqual(pet.pending, 'lie_up')
        self.assertNotIn('head_down', pet.queue)
        self.assertNotIn('head_up', pet.queue)
        self.assertIn('typing_enter', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'typing')

    def test_waiting_unplayed_sit_down_does_not_delay_walk(self):
        pet = UrgentPlayer()
        pet.pending, pet.waiting = 'sit_down', True
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'walk_right_enter')
        self.assertNotIn('sit_up', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'walk_right')

    def test_pending_direction_change_does_not_play_stale_turn(self):
        pet = UrgentPlayer()
        pet.start_stream('walk_left')
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'walk_right_enter')
        self.assertNotIn('walk_left_enter', pet.queue)
        self.assertNotIn('walk_left_exit', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'walk_right')

    def test_pending_partial_reverse_retains_displayed_first_frame(self):
        pet = UrgentPlayer()
        pet.key, pet.state, pet.index = 'walk_left_enter', 'stand_front', 12
        pet.interaction = dict(kind='walk_left', wanted=True, phase='approach')
        visible = frame_at(np.arange(90), pet.assets[pet.key], pet.index)
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertEqual(frame_at(np.arange(90), pet.assets[pet.pending], 0), visible)
        pet.stop_stream()
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertEqual(frame_at(np.arange(90), pet.assets[pet.pending], 0), visible)
        self.assertIn(pet.assert_queue_safe(), ('stand_front', 'walk_right'))
        pet.complete_active()
        self.assertEqual(pet.pending, 'stand_idle_rest')
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_plan_for_pending_transition_starts_at_real_state(self):
        pet = UrgentPlayer()
        pet.pending, pet.waiting = 'sit_down', True
        route, source, immediate = plan_route(pet, 'stand_front')
        self.assertEqual(source, 'stand_front')
        self.assertEqual(route, [])
        self.assertTrue(immediate)

    def test_new_typing_request_during_pending_reverse_keeps_prefix_and_changes_tail(self):
        pet = UrgentPlayer()
        pet.key, pet.state, pet.index = 'walk_left_enter', 'stand_front', 12
        pet.interaction = dict(kind='walk_left', wanted=True, phase='approach')
        visible = frame_at(np.arange(90), pet.assets[pet.key], pet.index)
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'command_return_prefix')
        pet.start_stream('typing')
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertEqual(frame_at(np.arange(90), pet.assets[pet.pending], 0), visible)
        self.assertNotIn('walk_right_enter', pet.queue)
        self.assertIn('typing_enter', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'typing')

    def test_exit_interrupts_standing_idle_without_waiting_for_its_end(self):
        pet = UrgentPlayer()
        pet.index = 1
        pet.request_exit()
        self.assertTrue(pet.exit_requested)
        self.assertTrue(pet.waiting)
        self.assertEqual(pet.pending, 'turn_exit')
        self.assertEqual(pet.queue, ['exit_effect'])
        self.assertEqual(pet.assert_queue_safe(), 'offscreen')

    def test_exit_from_paws_idle_uses_complete_safe_posture_route_immediately(self):
        pet = UrgentPlayer()
        pet.state, pet.key = 'lie_paws', 'paws_idle_rest'
        pet.index = 1
        pet.request_exit()
        self.assertTrue(pet.waiting)
        self.assertEqual(pet.pending, 'head_up')
        self.assertEqual(pet.queue, ['lie_up', 'sit_up', 'turn_exit', 'exit_effect'])
        self.assertEqual(pet.assert_queue_safe(), 'offscreen')

    def test_typing_stop_before_first_transition_starts_keeps_original_pose(self):
        pet = UrgentPlayer()
        pet.state, pet.key = 'lie_paws', 'paws_idle_rest'
        pet.start_stream('typing')
        pet.stop_stream()  # Debounced typing ended before the dog stood up.
        self.assertIsNone(pet.interaction)
        self.assertEqual(pet.state, 'lie_paws')
        self.assertEqual(pet.pending, 'paws_idle_rest')
        self.assertNotIn('typing_enter', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'lie_paws')

    def test_typing_stop_mid_head_up_finishes_only_that_stage_and_holds_lie(self):
        pet = UrgentPlayer()
        pet.state, pet.key = 'lie_paws', 'paws_idle_rest'
        pet.start_stream('typing')
        pet.activate_pending()
        self.assertEqual(pet.key, 'head_up')
        pet.index = 50
        pet.stop_stream()
        self.assertEqual(pet.key, 'head_up')
        pet.complete_active()
        self.assertEqual(pet.state, 'lie_front')
        self.assertIsNone(pet.interaction)
        self.assertNotIn('lie_up', [pet.pending] + pet.queue)
        self.assertNotIn('sit_up', [pet.pending] + pet.queue)
        self.assertNotIn('typing_enter', [pet.pending] + pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'lie_front')

    def test_typing_stop_mid_lie_up_finishes_only_that_stage_and_holds_sit(self):
        pet = UrgentPlayer()
        pet.state, pet.key = 'lie_front', 'lie_quiet'
        pet.start_stream('typing')
        pet.activate_pending()
        self.assertEqual(pet.key, 'lie_up')
        pet.index = 50
        pet.stop_stream()
        pet.complete_active()
        self.assertEqual(pet.state, 'sit_front')
        self.assertIsNone(pet.interaction)
        self.assertNotIn('sit_up', [pet.pending] + pet.queue)
        self.assertNotIn('typing_enter', [pet.pending] + pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'sit_front')

    def test_typing_stop_after_actual_loop_returns_through_keyboard_exit(self):
        pet = UrgentPlayer()
        pet.start_stream('typing')
        pet.complete_active()
        self.assertEqual(pet.pending, 'typing_loop')
        pet.activate_pending()
        pet.index = 30
        pet.stop_stream()
        pet.complete_active()
        self.assertEqual(pet.pending, 'typing_exit')
        pet.complete_active()
        self.assertIsNone(pet.interaction)
        self.assertEqual(pet.pending, 'stand_idle_rest')

    def test_resumed_input_before_current_stage_ends_cancels_pending_stop(self):
        pet = UrgentPlayer()
        pet.state, pet.key = 'lie_paws', 'paws_idle_rest'
        pet.start_stream('typing')
        pet.activate_pending()
        pet.index = 50
        pet.stop_stream()
        self.assertTrue(pet.interaction['cancel_approach'])
        self.assertEqual(pet.queue, [])
        pet.keyboard_activity.active = True
        pet.poll_interactions()
        self.assertFalse(pet.interaction.get('cancel_approach', False))
        self.assertTrue(pet.interaction['wanted'])
        self.assertIn('typing_enter', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'typing')
        pet.complete_active()
        self.assertEqual(pet.pending, 'lie_up')

    def test_resumed_input_after_pose_hold_continues_from_reached_pose(self):
        for initial, idle, reached, next_key in (
            ('lie_paws', 'paws_idle_rest', 'lie_front', 'lie_up'),
            ('lie_front', 'lie_quiet', 'sit_front', 'sit_up'),
        ):
            with self.subTest(reached=reached):
                pet = UrgentPlayer()
                pet.state, pet.key = initial, idle
                pet.start_stream('typing')
                pet.activate_pending()
                pet.index = 50
                pet.stop_stream()
                pet.complete_active()
                self.assertEqual(pet.state, reached)
                self.assertIsNone(pet.interaction)
                pet.keyboard_activity.active = True
                pet.poll_interactions()
                self.assertEqual(pet.pending, next_key)
                self.assertIn('typing_enter', pet.queue)
                self.assertEqual(pet.assert_queue_safe(), 'typing')

    def test_keyboard_idle_threshold_is_short_only_while_approaching(self):
        pet = UrgentPlayer()
        pet.keyboard_activity.detector = SimpleNamespace(idle_timeout=2., cooldown=1.)
        pet.start_stream('typing')
        pet.poll_interactions()
        self.assertEqual(pet.keyboard_activity.detector.idle_timeout, .55)
        self.assertEqual(pet.keyboard_activity.detector.cooldown, .2)
        pet.interaction = dict(kind='typing', phase='loop', wanted=True)
        pet.state, pet.key, pet.pending, pet.waiting = 'typing', 'typing_loop', 'typing_loop', False
        pet.poll_interactions()
        self.assertEqual(pet.keyboard_activity.detector.idle_timeout, 2.)
        self.assertEqual(pet.keyboard_activity.detector.cooldown, 1.)
        pet.interaction = None
        pet.state, pet.key, pet.pending = 'stand_front', 'stand_idle_rest', 'stand_idle_rest'
        pet.poll_interactions()
        self.assertEqual(pet.keyboard_activity.detector.idle_timeout, 2.)
        self.assertEqual(pet.keyboard_activity.detector.cooldown, 1.)

    def test_manual_posture_request_can_cancel_approaching_typing(self):
        pet = UrgentPlayer()
        pet.state, pet.key = 'lie_paws', 'paws_idle_rest'
        pet.start_stream('typing')
        pet.command_pose('sit_front')
        self.assertIsNone(pet.interaction)
        self.assertNotIn('typing_enter', [pet.pending] + pet.queue)
        self.assertNotIn('typing_loop', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_urgent_return_context_does_not_depend_on_live_interaction(self):
        pet = UrgentPlayer()
        pet.set_urgency('walk_right', target_state='stand_front')
        pet.interaction = None
        pet.key, pet.state = 'walk_right_exit', 'walk_right'
        self.assertIsNotNone(pet.urgency_context)
        self.assertGreater(pet.interaction_rate(), 1.)

    def test_urgent_target_activity_loop_or_petting_is_not_globally_sped_up(self):
        pet = UrgentPlayer()
        for kind in ('typing', 'walk_left', 'walk_right'):
            with self.subTest(kind=kind):
                pet.set_urgency(kind, target_key=kind + '_loop')
                pet.key, pet.state = kind + '_loop', kind
                pet.interaction = dict(kind=kind, wanted=True, phase='loop')
                self.assertEqual(pet.interaction_rate(), 1.)
        pet.set_urgency('pet', target_key='pet_sit')
        pet.key, pet.state = 'pet_sit', 'sit_front'
        pet.interaction = dict(kind='pet', clip='pet_sit', wanted=True, phase='pet')
        self.assertEqual(pet.interaction_rate(), 1.)


if __name__ == '__main__':
    unittest.main(verbosity=2)
