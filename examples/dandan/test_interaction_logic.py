"""Interaction routing regressions without windows, decoding, or real input."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt

from companion import install_idle_assets
from interaction_runtime import InteractionMixin
from pet_alpha import Pet, validate_edge, frame_at
from posture_commands import route_to


class FakeKeyboard:
    def __init__(self, active=False):
        self.active = active
        self.stopped = False
        self.resets = 0

    def poll(self, now):
        return self.active

    def reset(self):
        self.resets += 1


class FakePlayer(InteractionMixin):
    """Uses real queue/boundary methods, but completes clips explicitly."""

    begin_next = Pet.begin_next
    choose = Pet.choose
    command_pose = Pet.command_pose
    menu_closed = Pet.menu_closed

    def __init__(self):
        self.config = json.loads((Path(__file__).parent / 'config.json').read_text(encoding='utf-8'))
        self.assets = self.config['manifest']
        install_idle_assets(self.assets)
        for kind in ('typing', 'walk_left', 'walk_right'):
            for role, start, end in (('enter', 'stand_front', kind), ('loop', kind, kind),
                                     ('exit', kind, 'stand_front')):
                self.assets[kind + '_' + role] = dict(start=start, end=end, frames=90, fps=30,
                    interaction_role=role, continuous_group=kind, routing_transition=role == 'exit')
        for key, state in (('pet_sit', 'sit_front'), ('pet_paws', 'lie_paws')):
            self.assets[key] = dict(start=state, end=state, frames=90, fps=30, interaction_role='pet')
        self.state = 'stand_front'
        self.key = self.pending = 'stand_idle_rest'
        self.index = 0
        self.waiting = self.quitting = self.exit_requested = False
        self.exit_route_started = False
        self.command_target = None
        self.interaction = None
        self.interactions_ready = self.keyboard_enabled = self.random_mode = True
        self.input_cooldown = 0
        self.dragging = False
        self.drag = self.drag_press = self.drag_last = None
        self.walk_direction = None
        self.keyboard_wanted = False
        self.keyboard_activity = FakeKeyboard()
        self.radial = SimpleNamespace(isVisible=lambda: False)
        self.stand_bag = self.queue = []
        self.logs = []
        self.current = np.zeros((100, 100, 4), dtype=np.uint8)
        self.current[10:90, 30:70, 3] = 255
        self.canvas_size = 100
        self.menu_restore_through = False

    def width(self): return 100
    def height(self): return 100
    def request(self, key): self.last_requested = key
    def log(self, event, **kwargs): self.logs.append((event, kwargs))
    def force_close(self): raise AssertionError('Unexpected force close')
    def set_through(self, on): self.through = on

    def activate_pending(self):
        self.key = self.pending
        self.waiting = False
        self.index = 0

    def complete_active(self):
        if self.waiting:
            self.activate_pending()
        self.state = self.assets[self.key]['end']
        self.index = self.assets[self.key]['frames'] - 1
        self.begin_next()

    def assert_queue_safe(self):
        state = self.assets[self.pending]['end'] if self.waiting else self.assets[self.key]['end']
        for key in self.queue:
            validate_edge(self.assets, state, key)
            state = self.assets[key]['end']
        return state


class InteractionTests(unittest.TestCase):
    def test_typing_does_not_preempt_explicit_posture_command(self):
        pet = FakePlayer()
        pet.command_target = 'lie_front'
        pet.keyboard_activity.active = True
        pet.poll_interactions()
        self.assertEqual(pet.command_target, 'lie_front')
        self.assertIsNone(pet.interaction)

    def test_typing_stopping_during_posture_route_drops_enter(self):
        pet = FakePlayer()
        pet.state, pet.key = 'lie_paws', 'paws_idle_rest'
        pet.start_stream('typing')
        pet.activate_pending()  # head_up begins, before typing_enter.
        pet.interaction['wanted'] = False
        pet.complete_active()
        self.assertIsNone(pet.interaction)
        self.assertNotIn('typing_enter', [pet.pending] + pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'lie_front')

    def test_release_before_pending_enter_does_not_show_new_enter(self):
        pet = FakePlayer()
        pet.start_stream('walk_left')
        self.assertTrue(pet.waiting)
        self.assertEqual(pet.pending, 'walk_left_enter')
        pet.stop_stream()
        self.assertNotEqual(pet.pending, 'walk_left_enter')
        self.assertIsNone(pet.interaction)
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_typing_stops_mid_intro_retraces_same_first_frame(self):
        pet = FakePlayer()
        pet.key, pet.state, pet.index = 'typing_enter', 'stand_front', 15
        pet.interaction = dict(kind='typing', wanted=True, phase='approach')
        pet.keyboard_activity.active = False
        pet.keyboard_activity.stopped = True
        expected = frame_at(np.arange(90), pet.assets['typing_enter'], pet.index)
        pet.poll_interactions()
        self.assertIsNone(pet.interaction)
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertEqual(frame_at(np.arange(90), pet.assets[pet.pending], 0), expected)
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_typing_resumes_during_exit_reenters_before_loop(self):
        pet = FakePlayer()
        pet.key, pet.state, pet.index = 'typing_exit', 'typing', 30
        pet.interaction = dict(kind='typing', wanted=False, phase='exit')
        pet.keyboard_activity.active = True
        pet.poll_interactions()
        self.assertTrue(pet.interaction['wanted'])
        pet.complete_active()
        self.assertEqual(pet.pending, 'typing_enter')
        self.assertIsNotNone(pet.interaction)
        pet.complete_active()
        self.assertEqual(pet.pending, 'typing_loop')

    def test_drag_resumes_same_direction_during_exit(self):
        pet = FakePlayer()
        pet.key, pet.state, pet.index = 'walk_right_exit', 'walk_right', 30
        pet.interaction = dict(kind='walk_right', wanted=False, phase='exit')
        pet.start_stream('walk_right')
        self.assertTrue(pet.interaction['wanted'])
        pet.complete_active()
        self.assertEqual(pet.pending, 'walk_right_enter')

    def test_release_before_pending_loop_chooses_exit_without_extra_loop(self):
        pet = FakePlayer()
        pet.key, pet.state = 'walk_right_enter', 'walk_right'
        pet.pending, pet.waiting = 'walk_right_loop', True
        pet.interaction = dict(kind='walk_right', wanted=True, phase='loop')
        pet.stop_stream()
        self.assertEqual(pet.pending, 'walk_right_exit')
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_walk_direction_change_then_release_uses_real_exit_routes(self):
        pet = FakePlayer()
        pet.state, pet.key = 'walk_left', 'walk_left_loop'
        pet.interaction = dict(kind='walk_left', wanted=True, phase='loop')
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'walk_left_exit')
        pet.complete_active()
        self.assertEqual(pet.pending, 'walk_right_enter')
        pet.complete_active()
        self.assertEqual(pet.pending, 'walk_right_loop')
        pet.activate_pending()
        pet.stop_stream()
        pet.complete_active()
        self.assertEqual(pet.pending, 'walk_right_exit')
        pet.complete_active()
        self.assertIsNone(pet.interaction)
        self.assertEqual(pet.pending, 'stand_idle_rest')

    def test_direction_change_before_intro_activation_uses_latest_direction(self):
        pet = FakePlayer()
        pet.start_stream('walk_left')
        self.assertTrue(pet.waiting)
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'walk_right_enter')
        self.assertNotIn('walk_left_exit', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'walk_right')

    def test_release_while_reverse_prefix_is_loading_keeps_legal_route(self):
        pet = FakePlayer()
        pet.key, pet.state, pet.index = 'walk_left_enter', 'stand_front', 12
        pet.interaction = dict(kind='walk_left', wanted=True, phase='approach')
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertTrue(pet.waiting)
        pet.stop_stream()
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertIn(pet.assert_queue_safe(), ('stand_front', 'walk_right'))
        pet.complete_active()
        self.assertIsNone(pet.interaction)
        self.assertEqual(pet.pending, 'stand_idle_rest')

    def test_standing_head_pet_falls_back_to_sit_and_restores_stand(self):
        pet = FakePlayer()
        pet.assets.pop('pet_stand',None)
        self.assertTrue(pet.pet_head(QPointF(50, 25)))
        self.assertEqual(pet.pending, 'sit_down')
        self.assertIn('pet_sit', pet.queue)
        self.assertIn('sit_up', pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_standing_head_pet_uses_standing_export_when_available(self):
        pet=FakePlayer()
        pet.assets['pet_stand']=dict(start='stand_front',end='stand_front',frames=90,fps=30,interaction_role='pet')
        self.assertTrue(pet.pet_head(QPointF(50,25)))
        self.assertEqual(pet.pending,'pet_stand')
        self.assertNotIn('sit_down',pet.queue)
        self.assertNotIn('pet_sit',pet.queue)
        self.assertEqual(pet.assert_queue_safe(),'stand_front')

    def test_special_approach_is_brisk_but_loop_and_petting_are_normal(self):
        pet=FakePlayer()
        for kind in ('typing','walk_left','walk_right'):
            pet.interaction=dict(kind=kind,wanted=True,phase='approach')
            pet.set_urgency(kind,target_key=kind+'_loop')
            for key in ('head_up','lie_up','sit_up',kind+'_enter',kind+'_exit'):
                pet.key=key
                self.assertEqual(pet.interaction_rate(),3.5)
            pet.key=kind+'_loop'
            self.assertEqual(pet.interaction_rate(),1.)
        pet.interaction=dict(kind='pet');pet.key='pet_sit'
        self.assertEqual(pet.interaction_rate(),1.)
        pet.interaction=None;pet.urgency_context=None;pet.key='stand_idle_rest'
        self.assertEqual(pet.interaction_rate(),1.)

    def test_body_and_transparent_clicks_are_not_head_pet(self):
        pet = FakePlayer()
        self.assertFalse(pet.pet_head(QPointF(50, 65)))
        self.assertFalse(pet.pet_head(QPointF(5, 25)))
        self.assertIsNone(pet.interaction)

    def test_non_idle_action_double_click_is_ignored(self):
        pet = FakePlayer()
        pet.key = 'bark'
        self.assertFalse(pet.pet_head(QPointF(50, 25)))
        self.assertIsNone(pet.interaction)

    def test_manual_command_cancels_typing_and_routes_out(self):
        pet = FakePlayer()
        pet.state, pet.key = 'typing', 'typing_loop'
        pet.interaction = dict(kind='typing', wanted=True, phase='loop')
        pet.command_pose('sit_front')
        self.assertIsNone(pet.interaction)
        self.assertEqual(pet.pending, 'typing_exit')
        self.assertEqual(pet.command_target, 'sit_front')
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_menu_close_preserves_active_drag(self):
        pet = FakePlayer()
        pet.dragging = True
        pet.drag = QPoint(12, 15)
        Pet.menu_closed(pet)
        self.assertEqual(pet.drag, QPoint(12, 15))

    def test_menu_through_restore_waits_until_drag_release(self):
        pet = FakePlayer()
        pet.dragging, pet.menu_restore_through = True, True
        pet.drag, pet.through = QPoint(12, 15), False
        pet.menu_closed()
        self.assertFalse(pet.through)
        event = SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton)
        pet.interaction_mouse_release(event)
        self.assertTrue(pet.through)
        self.assertFalse(pet.menu_restore_through)


if __name__ == '__main__':
    unittest.main(verbosity=2)
