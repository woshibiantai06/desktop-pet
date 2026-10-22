"""Free-play mode regressions without native input, windows, or frame decoding."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, Qt

from pet_alpha import Pet
from radial_menu import RadialMenu
from test_interaction_logic import FakePlayer as RoutingPlayer


class FakeAction:
    def __init__(self):
        self.checked = False
        self.text = ''

    def setChecked(self, value):
        self.checked = bool(value)

    def setText(self, value):
        self.text = value


class FakePlayer(RoutingPlayer):
    """Keep the production FSM and gesture handlers; replace only Qt I/O."""
    switch_mode = Pet.switch_mode
    request_exit = Pet.request_exit

    def __init__(self):
        super().__init__()
        self.free_play = False
        self.urgency_context = None
        self.radial = SimpleNamespace(isVisible=lambda: False, dismiss=lambda: None,
                                      update=lambda: None)
        self.free_action = self.free_play_action = FakeAction()
        self.keyboard_action = FakeAction()
        self.tray = SimpleNamespace(setToolTip=lambda text: None)
        self.drag_last_move = 0.
        self._position = QPoint(200, 300)
        self.moves = []
        self.keyboard_polls = 0
        original_poll = self.keyboard_activity.poll

        def counted_poll(now):
            self.keyboard_polls += 1
            return original_poll(now)

        self.keyboard_activity.poll = counted_poll

    def pos(self):
        return QPoint(self._position)

    def move(self, point):
        self._position = QPoint(point)
        self.moves.append(QPoint(point))


def mouse_event(point, button=Qt.MouseButton.LeftButton,
                buttons=Qt.MouseButton.LeftButton):
    return SimpleNamespace(button=lambda: button, buttons=lambda: buttons,
                           globalPosition=lambda: QPointF(point))


class FreePlayTests(unittest.TestCase):
    def test_toggle_updates_mode_and_button_label(self):
        pet = FakePlayer()
        self.assertEqual(pet.free_play_label(), '自由活动')
        pet.toggle_free_play()
        self.assertTrue(pet.free_play)
        self.assertEqual(pet.free_play_label(), '取消自由活动')
        self.assertTrue(pet.free_play_action.checked)
        self.assertEqual(pet.free_play_action.text, '取消自由活动')
        pet.toggle_free_play()
        self.assertFalse(pet.free_play)
        self.assertEqual(pet.free_play_label(), '自由活动')
        self.assertFalse(pet.free_play_action.checked)
        self.assertEqual(pet.free_play_action.text, '自由活动')

    def test_repeated_set_is_idempotent(self):
        pet = FakePlayer()
        pet.set_free_play(True)
        snapshot = (pet.key, pet.pending, pet.state, pet.waiting,
                    deepcopy(pet.queue), pet.keyboard_activity.resets)
        pet.set_free_play(True)
        self.assertTrue(pet.free_play)
        self.assertEqual((pet.key, pet.pending, pet.state, pet.waiting,
                          pet.queue, pet.keyboard_activity.resets), snapshot)
        pet.set_free_play(False)
        snapshot = (pet.key, pet.pending, pet.state, pet.waiting,
                    deepcopy(pet.queue), pet.keyboard_activity.resets)
        pet.set_free_play(False)
        self.assertFalse(pet.free_play)
        self.assertEqual((pet.key, pet.pending, pet.state, pet.waiting,
                          pet.queue, pet.keyboard_activity.resets), snapshot)

    def test_keeps_user_keyboard_link_preference(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                pet = FakePlayer()
                pet.keyboard_enabled = enabled
                pet.set_free_play(True)
                self.assertEqual(pet.keyboard_enabled, enabled)
                pet.set_free_play(False)
                self.assertEqual(pet.keyboard_enabled, enabled)

    def test_free_play_never_polls_keyboard_or_starts_typing(self):
        pet = FakePlayer()
        pet.set_free_play(True)
        pet.keyboard_activity.active = True
        pet.keyboard_wanted = True
        for _ in range(4):
            pet.poll_interactions()
        self.assertEqual(pet.keyboard_polls, 0)
        self.assertFalse(pet.keyboard_wanted)
        self.assertIsNone(pet.interaction)
        self.assertGreater(pet.keyboard_activity.resets, 0)

    def test_direct_special_stream_calls_are_blocked(self):
        for kind in ('typing', 'walk_left', 'walk_right'):
            with self.subTest(kind=kind):
                pet = FakePlayer()
                pet.set_free_play(True)
                before = (pet.key, pet.pending, deepcopy(pet.queue), pet.state)
                self.assertFalse(pet.start_stream(kind))
                self.assertIsNone(pet.interaction)
                self.assertEqual((pet.key, pet.pending, pet.queue, pet.state), before)

    def test_free_play_ignores_head_pet_for_all_idle_postures(self):
        for pose, key in (('stand_front', 'stand_idle_rest'),
                          ('sit_front', 'sit_rest'), ('lie_front', 'lie_quiet'),
                          ('lie_paws', 'paws_idle_rest')):
            with self.subTest(pose=pose):
                pet = FakePlayer()
                pet.state, pet.key = pose, key
                pet.set_free_play(True)
                with patch.object(pet, 'head_hit', return_value=True):
                    self.assertFalse(pet.pet_head(QPointF(50, 25)))
                self.assertIsNone(pet.interaction)

    def test_cancelling_free_play_does_not_reset_pose_or_animation(self):
        pet = FakePlayer()
        pet.state, pet.key, pet.pending = 'sit_front', 'sit_rest', 'sit_rest'
        pet.index = 17
        pet.set_free_play(True)
        before = (pet.state, pet.key, pet.pending, pet.index, pet.waiting,
                  deepcopy(pet.queue))
        pet.set_free_play(False)
        self.assertEqual((pet.state, pet.key, pet.pending, pet.index,
                          pet.waiting, pet.queue), before)

    def test_enabling_during_typing_loop_exits_through_legal_route(self):
        pet = FakePlayer()
        pet.state, pet.key = 'typing', 'typing_loop'
        pet.interaction = dict(kind='typing', phase='loop', wanted=True)
        pet.set_free_play(True)
        self.assertTrue(pet.free_play)
        self.assertFalse(pet.interaction['wanted'])
        self.assertEqual(pet.key, 'typing_loop')
        pet.complete_active()
        self.assertEqual(pet.pending, 'typing_exit')
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')
        pet.complete_active()
        self.assertEqual(pet.state, 'stand_front')
        self.assertIsNone(pet.interaction)
        self.assertTrue(pet.free_play)

    def test_enabling_during_walk_loop_exits_through_legal_route(self):
        for kind in ('walk_left', 'walk_right'):
            with self.subTest(kind=kind):
                pet = FakePlayer()
                pet.state, pet.key = kind, kind + '_loop'
                pet.interaction = dict(kind=kind, phase='loop', wanted=True)
                pet.set_free_play(True)
                pet.complete_active()
                self.assertEqual(pet.pending, kind + '_exit')
                self.assertEqual(pet.assert_queue_safe(), 'stand_front')
                pet.complete_active()
                self.assertEqual(pet.state, 'stand_front')
                self.assertIsNone(pet.interaction)

    def test_unneeded_typing_approach_stops_at_reached_posture(self):
        pet = FakePlayer()
        pet.state, pet.key = 'lie_paws', 'paws_idle_rest'
        pet.start_stream('typing')
        pet.activate_pending()
        self.assertEqual(pet.key, 'head_up')
        pet.set_free_play(True)
        pet.complete_active()
        self.assertEqual(pet.state, 'lie_front')
        self.assertIsNone(pet.interaction)
        self.assertNotIn('typing_enter', [pet.pending] + pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'lie_front')

    def test_enabling_cancels_unplayed_manual_route_without_teleport(self):
        pet = FakePlayer()
        pet.command_pose('lie_front')
        self.assertTrue(pet.waiting)
        self.assertEqual(pet.pending, 'sit_down')
        pet.set_free_play(True)
        self.assertIsNone(pet.command_target)
        self.assertEqual(pet.state, 'stand_front')
        self.assertNotIn('lie_down', [pet.pending] + pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_enabling_during_posture_transition_finishes_current_segment(self):
        pet = FakePlayer()
        pet.command_pose('lie_front')
        pet.activate_pending()
        pet.index = 20
        self.assertEqual(pet.key, 'sit_down')
        pet.set_free_play(True)
        self.assertEqual(pet.key, 'sit_down')
        self.assertFalse(pet.waiting)
        self.assertEqual(pet.index, 20)
        self.assertIsNone(pet.command_target)
        self.assertNotIn('lie_down', pet.queue)
        pet.complete_active()
        self.assertEqual(pet.state, 'sit_front')
        self.assertEqual(pet.pending, 'sit_rest')
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_nonstanding_free_play_bouts_naturally_return_to_standing(self):
        # Include the normal idle poses and authored intermediate directions;
        # lifecycle offscreen and live owner-stream states use separate logic.
        cases = {
            'sit_front': 'sit_rest',
            'lie_front': 'lie_quiet',
            'lie_paws': 'paws_idle_rest',
            'stand_side_screen_left': 'side_idle_rest',
            'sit_look_screen_left': 'sit_left_idle_rest',
            'sit_look_screen_right': 'sit_right_idle_rest',
            'stand_threequarter': 'turn_exit_return',
            'stand_front_small': 'entry_approach',
            'stand_look_screen_left': 'stand_look',
            'stand_threequarter_far': 'quarter_return_alt',
        }
        for pose, expected_first in cases.items():
            with self.subTest(pose=pose):
                pet = FakePlayer()
                pet.free_play = True
                pet.state = pose
                pet.queue = []
                pet.choose()
                self.assertTrue(pet.queue)
                self.assertEqual(pet.queue[0], expected_first)
                reached = pose
                for key in pet.queue:
                    clip = pet.assets[key]
                    self.assertEqual(clip['start'], reached, (pose, key))
                    self.assertNotEqual(clip.get('action_class'), 'owner_special')
                    reached = clip['end']
                self.assertEqual(reached, 'stand_front')
                self.assertEqual(pet.queue[-1], 'stand_idle_rest')
                self.assertIsNone(pet.urgency_context)

    def test_enabling_preserves_pending_manual_return_prefix(self):
        pet = FakePlayer()
        pet.key, pet.state, pet.index = 'sit_down', 'stand_front', 12
        pet.command_pose('stand_front')
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertTrue(pet.waiting)
        prefix = deepcopy(pet.assets[pet.pending])
        partial_state = pet.state
        pet.set_free_play(True)
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertTrue(pet.waiting)
        self.assertEqual(pet.state, partial_state)
        self.assertEqual(pet.assets[pet.pending], prefix)
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')
        pet.complete_active()
        self.assertEqual(pet.state, 'stand_front')
        self.assertEqual(pet.assets[pet.pending]['start'], 'stand_front')

    def test_enabling_preserves_pending_owner_return_prefix_until_safe_pose(self):
        pet = FakePlayer()
        pet.key, pet.state, pet.index = 'walk_left_enter', 'stand_front', 12
        pet.interaction = dict(kind='walk_left', phase='approach', wanted=True)
        pet.start_stream('walk_right')
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertTrue(pet.waiting)
        prefix = deepcopy(pet.assets[pet.pending])
        pet.set_free_play(True)
        self.assertEqual(pet.pending, 'command_return_prefix')
        self.assertTrue(pet.waiting)
        self.assertEqual(pet.assets[pet.pending], prefix)
        pet.complete_active()
        self.assertEqual(pet.state, 'stand_front')
        self.assertIsNone(pet.interaction)
        self.assertNotIn('walk_right_enter', [pet.pending] + pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'stand_front')

    def test_pending_startup_preserves_complete_entry_pair(self):
        pet = FakePlayer()
        pet.state, pet.key = 'offscreen', ''
        pet.pending, pet.waiting = 'entry_effect', True
        pet.queue = ['entry_approach', 'stand_quiet']
        pet.set_free_play(True)
        self.assertEqual(pet.state, 'offscreen')
        self.assertEqual(pet.pending, 'entry_effect')
        self.assertEqual(pet.queue[0], 'entry_approach')

    def test_active_startup_preserves_remaining_entry_pair(self):
        pet = FakePlayer()
        pet.state, pet.key = 'offscreen', 'entry_effect'
        pet.pending = 'entry_effect'
        pet.queue = ['entry_approach', 'stand_quiet']
        pet.set_free_play(True)
        self.assertEqual(pet.key, 'entry_effect')
        self.assertFalse(pet.waiting)
        self.assertEqual(pet.queue[0], 'entry_approach')

    def test_manual_pose_explicitly_cancels_free_play(self):
        pet = FakePlayer()
        pet.set_free_play(True)
        pet.command_pose('sit_front')
        self.assertFalse(pet.free_play)
        self.assertEqual(pet.command_target, 'sit_front')
        self.assertEqual(pet.pending, 'sit_down')

    def test_demo_command_explicitly_cancels_free_play(self):
        pet = FakePlayer()
        pet.set_free_play(True)
        pet.switch_mode(True)
        self.assertFalse(pet.free_play)
        self.assertFalse(pet.random_mode)
        self.assertTrue(pet.queue)

    def test_exit_always_works_in_free_play(self):
        pet = FakePlayer()
        pet.set_free_play(True)
        pet.request_exit()
        self.assertTrue(pet.exit_requested)
        self.assertFalse(pet.random_mode)
        self.assertIn('turn_exit', [pet.pending] + pet.queue)
        self.assertIn('exit_effect', [pet.pending] + pet.queue)
        self.assertEqual(pet.assert_queue_safe(), 'offscreen')

    def test_drag_in_free_play_moves_window_without_special_animation(self):
        pet = FakePlayer()
        pet.set_free_play(True)
        start = QPoint(250, 325)
        finish = QPoint(320, 325)
        pet.interaction_mouse_press(mouse_event(start))
        with patch('interaction_runtime.QApplication',
                   SimpleNamespace(startDragDistance=lambda: 10)):
            pet.interaction_mouse_move(mouse_event(finish))
        self.assertTrue(pet.dragging)
        self.assertEqual(pet.pos(), QPoint(270, 300))
        self.assertIsNone(pet.interaction)
        self.assertTrue(pet.free_play)
        self.assertFalse(any(e == 'interaction_requested' for e, _ in pet.logs))
        pet.interaction_mouse_release(mouse_event(finish, buttons=Qt.MouseButton.NoButton))
        self.assertFalse(pet.dragging)
        self.assertIsNone(pet.interaction)
        self.assertTrue(pet.free_play)

    def test_radial_menu_resolves_free_play_label_dynamically(self):
        pet = FakePlayer()
        menu_stub = SimpleNamespace(owner=pet)
        self.assertEqual(RadialMenu.label_for(menu_stub, 'free', '自由活动'), '自由活动')
        pet.set_free_play(True)
        self.assertEqual(RadialMenu.label_for(menu_stub, 'free', '自由活动'), '取消自由活动')
        self.assertEqual(RadialMenu.label_for(menu_stub, 'exit', '退出'), '退出')

    def test_radial_free_button_dispatches_toggle_not_old_mode_reset(self):
        pet = FakePlayer()
        dismissed = []
        menu_stub = SimpleNamespace(owner=pet, closing=False, ITEMS=RadialMenu.ITEMS,
                                    hit=lambda point: 3,
                                    dismiss=lambda: dismissed.append(True))
        event = SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
                                position=lambda: QPointF(20, 20))
        RadialMenu.mousePressEvent(menu_stub, event)
        self.assertTrue(pet.free_play)
        RadialMenu.mousePressEvent(menu_stub, event)
        self.assertFalse(pet.free_play)
        self.assertEqual(len(dismissed), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
