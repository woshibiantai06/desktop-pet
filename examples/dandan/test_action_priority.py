"""Pure policy checks: no Qt, decoding, or desktop input required."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from action_priority import (
    SPECIAL_ACTIONS, install_action_tags, is_urgent_route, target_route_seconds,
)


class ActionTagTests(unittest.TestCase):
    def setUp(self):
        self.assets = json.loads((Path(__file__).parent / 'config.json').read_text(
            encoding='utf-8'))['manifest']

    def test_all_stream_segments_are_explicit_owner_specials(self):
        install_action_tags(self.assets)
        for kind in ('typing', 'walk_left', 'walk_right'):
            for role in ('enter', 'loop', 'exit'):
                with self.subTest(kind=kind, role=role):
                    clip = self.assets[f'{kind}_{role}']
                    self.assertEqual(clip['action_class'], 'owner_special')
                    self.assertEqual(clip['request_kind'], kind)
                    self.assertEqual(clip['trigger'], SPECIAL_ACTIONS[kind]['trigger'])
                    self.assertEqual(clip['response_priority'], SPECIAL_ACTIONS[kind]['priority'])

    def test_all_three_petting_poses_are_owner_specials(self):
        install_action_tags(self.assets)
        for key in ('pet_stand', 'pet_sit', 'pet_paws'):
            self.assertEqual(self.assets[key]['action_class'], 'owner_special')
            self.assertEqual(self.assets[key]['trigger'], 'double_click_head')

    def test_postures_are_not_globally_promoted_to_special(self):
        install_action_tags(self.assets)
        for key in ('head_up', 'lie_up', 'sit_up', 'sit_down', 'lie_down'):
            with self.subTest(key=key):
                self.assertEqual(self.assets[key]['action_class'], 'posture_transition')
                self.assertEqual(self.assets[key]['response_priority'], 0)
                self.assertIsNone(self.assets[key]['request_kind'])

    def test_rest_and_micro_actions_remain_ambient(self):
        install_action_tags(self.assets)
        for key in ('stand_idle', 'sit_idle', 'lick', 'tilt', 'bark'):
            self.assertEqual(self.assets[key]['action_class'], 'ambient')
            self.assertEqual(self.assets[key]['response_priority'], 0)

    def test_lifecycle_is_not_owner_special(self):
        install_action_tags(self.assets)
        for key in ('entry_effect', 'entry_approach', 'turn_exit', 'exit_effect'):
            self.assertEqual(self.assets[key]['action_class'], 'system_lifecycle')

    def test_tags_preserve_all_existing_asset_content(self):
        original = deepcopy(self.assets)
        self.assertIs(install_action_tags(self.assets), self.assets)
        tag_fields = {'action_class', 'trigger', 'response_priority', 'request_kind'}
        for key, clip in original.items():
            for field, value in clip.items():
                if field not in tag_fields:
                    self.assertEqual(self.assets[key][field], value)

    def test_install_is_idempotent(self):
        install_action_tags(self.assets)
        once = deepcopy(self.assets)
        install_action_tags(self.assets)
        self.assertEqual(self.assets, once)

    def test_registry_destinations_and_priority_order(self):
        self.assertEqual(SPECIAL_ACTIONS['typing']['destination'], 'typing')
        self.assertEqual(SPECIAL_ACTIONS['walk_left']['destination'], 'walk_left')
        self.assertEqual(SPECIAL_ACTIONS['walk_right']['destination'], 'walk_right')
        self.assertGreater(SPECIAL_ACTIONS['walk_left']['priority'], SPECIAL_ACTIONS['typing']['priority'])
        self.assertGreater(SPECIAL_ACTIONS['manual_pose']['priority'], SPECIAL_ACTIONS['typing']['priority'])
        self.assertEqual(max(x['priority'] for x in SPECIAL_ACTIONS.values()),
                         SPECIAL_ACTIONS['exit']['priority'])


class UrgentRouteTests(unittest.TestCase):
    transition = {'start': 'lie_paws', 'end': 'lie_front'}
    context = {'kind': 'typing', 'target': 'typing'}

    def test_no_request_never_accelerates(self):
        self.assertFalse(is_urgent_route('head_up', self.transition, None))
        self.assertFalse(is_urgent_route('head_up', self.transition, {}))

    def test_unknown_request_is_not_urgent(self):
        self.assertFalse(is_urgent_route('head_up', self.transition, {'kind': 'ambient'}))

    def test_posture_route_is_urgent_only_with_explicit_context(self):
        for kind in SPECIAL_ACTIONS:
            self.assertTrue(is_urgent_route('head_up', self.transition, {'kind': kind}))

    def test_tags_alone_do_not_accelerate(self):
        tagged = {**self.transition, 'action_class': 'owner_special', 'response_priority': 100}
        self.assertFalse(is_urgent_route('head_up', tagged, None))

    def test_stream_enter_and_exit_can_respond_quickly(self):
        for role in ('enter', 'exit'):
            self.assertTrue(is_urgent_route('typing_' + role,
                {'interaction_role': role}, self.context))

    def test_stream_loop_always_uses_normal_speed(self):
        self.assertFalse(is_urgent_route('typing_loop',
            {'start': 'typing', 'end': 'typing', 'interaction_role': 'loop'}, self.context))

    def test_all_petting_clips_use_normal_speed(self):
        for key in ('pet_stand', 'pet_sit', 'pet_paws'):
            self.assertFalse(is_urgent_route(key, {'interaction_role': 'pet'}, self.context))

    def test_intro_and_disappearance_are_protected(self):
        for key in ('entry_effect', 'entry_approach', 'exit_effect'):
            self.assertFalse(is_urgent_route(key, self.transition, self.context))

    def test_exit_turn_can_be_a_fast_route_without_rushing_disappearance(self):
        self.assertTrue(is_urgent_route('turn_exit',
            {'start': 'stand_front', 'end': 'stand_threequarter'}, {'kind': 'exit'}))

    def test_still_idle_does_not_accelerate_even_with_request(self):
        self.assertFalse(is_urgent_route('stand_idle',
            {'start': 'stand_front', 'end': 'stand_front'}, self.context))

    def test_completed_or_cancelled_context_is_inactive(self):
        for phase in ('active', 'reached', 'complete', 'completed', 'cancelled'):
            self.assertFalse(is_urgent_route('head_up', self.transition,
                {**self.context, 'phase': phase}))
        self.assertFalse(is_urgent_route('head_up', self.transition,
            {**self.context, 'completed': True}))

    def test_partial_retrace_is_eligible_without_skipping_a_state_edge(self):
        self.assertTrue(is_urgent_route('command_return_prefix',
            {'start': 'partial:lie_down', 'end': 'sit_front', 'command_prefix': True}, self.context))

    def test_budget_is_guidance_not_global_playback_rate(self):
        expected = {'typing': 3.2, 'walk_left': 2.6, 'walk_right': 2.6,
                    'pet': 1.2, 'manual_pose': 1.4, 'exit': 2.5}
        for kind, seconds in expected.items():
            self.assertEqual(target_route_seconds(kind), seconds)
        self.assertIsNone(target_route_seconds('ambient'))


if __name__ == '__main__':
    unittest.main()
