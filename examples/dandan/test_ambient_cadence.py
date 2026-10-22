"""Natural-cadence checks: no QApplication, windows, native input or decoding.

The old recipe is retained only as a timing reference.  A playback clip lasts
N / fps (QuietTimeline's completion point), not (N - 1) / fps.
"""
from copy import deepcopy
import random
import statistics
import unittest
from unittest.mock import patch

from companion import bouts, install_idle_assets
from test_interaction_logic import FakePlayer


REST_KEYS = frozenset({
    'stand_idle_rest', 'sit_rest', 'sit_left_idle_rest',
    'sit_right_idle_rest', 'side_idle_rest', 'paws_idle_rest',
})
DAILY_SOURCES = (set(range(1, 29)) - {15, 16, 17, 18}) | {29, 30, 36, 37, 38, 39}
DAILY_ACTIONS = frozenset({
    'lick', 'tilt', 'bark', 'stand_scratch', 'look_left', 'stand_look',
    'stand_idle_legacy', 'sit_tilt', 'sit_scratch', 'sit_scratch_alt',
    'sit_look', 'sit_look_alt', 'sit_bark', 'sit_to_left', 'sit_from_left',
    'sit_to_right', 'sit_from_right', 'side_turn', 'side_sniff',
    'side_lookup', 'side_return', 'side_turn_alt', 'side_return_alt',
    'quarter_turn_alt', 'quarter_return_alt', 'sit_down', 'sit_up',
    'lie_down', 'lie_up', 'head_down', 'head_up',
})


def old_bouts(rng):
    """Unmodified 0.3.1 repeat counts, without touching production assets."""
    stand_actions = [['lick'], ['tilt'], ['bark'], ['stand_scratch'],
                     ['look_left', 'stand_look'], ['stand_idle_legacy']]
    rng.shuffle(stand_actions)
    stand = ['stand_idle_rest']
    for action in stand_actions:
        stand += action + ['stand_idle_rest'] * rng.randint(1, 2)
    sit_actions = [
        ['sit_tilt'], ['sit_scratch'], ['sit_scratch_alt'], ['sit_look'],
        ['sit_look_alt'], ['sit_bark'],
        ['sit_to_left'] + ['sit_left_idle_rest'] * rng.randint(3, 5) + ['sit_from_left'],
        ['sit_to_right'] + ['sit_right_idle_rest'] * rng.randint(3, 5) + ['sit_from_right'],
    ]
    rng.shuffle(sit_actions)
    sit = ['sit_down', 'sit_rest']
    for action in sit_actions:
        sit += action + ['sit_rest']
    sit += ['sit_up']
    side = ['side_turn'] + ['side_idle_rest'] * rng.randint(2, 3) + [
        'side_sniff', 'side_idle_rest', 'side_lookup', 'side_return',
        'stand_idle_rest', 'side_turn_alt', 'side_idle_rest', 'side_return_alt',
        'quarter_turn_alt', 'quarter_return_alt',
    ]
    lie = ['sit_down', 'lie_down', 'head_down'] + ['paws_idle_rest'] * rng.randint(4, 7) + [
        'head_up', 'lie_up', 'sit_up',
    ]
    result = [stand, sit, side, lie]
    rng.shuffle(result)
    return result


def group_name(group):
    if group[0] == 'stand_idle_rest':
        return 'stand'
    if group[0] == 'side_turn':
        return 'side'
    if group[:3] == ['sit_down', 'lie_down', 'head_down']:
        return 'lie'
    return 'sit'


def duration(assets, sequence):
    return sum(assets[key]['frames'] / assets[key]['fps'] for key in sequence)


class AmbientCadenceTests(unittest.TestCase):
    def test_1000_seed_cycles_keep_all_daily_actions_and_posture_edges(self):
        pet = FakePlayer()
        original_assets = deepcopy(pet.assets)
        for seed in range(1000):
            groups = bouts(random.Random(seed))
            pet.state, pet.stand_bag, pet.queue = 'stand_front', [], []
            covered, sources = set(), set()
            # Exercise the real Pet.choose, including its outer standing rest.
            with patch('pet_alpha.bouts', return_value=deepcopy(groups)):
                for _ in range(4):
                    pet.choose()
                    self.assertTrue(pet.queue, seed)
                    for key in pet.queue:
                        clip = pet.assets[key]
                        self.assertEqual(clip['start'], pet.state, (seed, key))
                        self.assertNotEqual(clip.get('action_class'), 'owner_special')
                        pet.state = clip['end']
                        covered.add(key)
                        sources.add(clip.get('source_id'))
                    self.assertEqual(pet.state, 'stand_front', seed)
                    pet.queue = []
            self.assertFalse(pet.stand_bag, seed)
            self.assertTrue(DAILY_ACTIONS <= covered, seed)
            self.assertEqual(sources, DAILY_SOURCES, seed)
        self.assertEqual(pet.assets, original_assets)

    def test_waiting_repeat_limits_keep_complete_idle_units(self):
        for seed in range(1000):
            groups = {group_name(g): g for g in bouts(random.Random(seed))}
            stand = groups['stand']
            # One initial rest and exactly one after each of the six actions.
            self.assertEqual(stand.count('stand_idle_rest'), 7, seed)
            self.assertFalse(any(a == b == 'stand_idle_rest'
                                 for a, b in zip(stand, stand[1:])), seed)
            sit = groups['sit']
            for key in ('sit_left_idle_rest', 'sit_right_idle_rest'):
                self.assertIn(sit.count(key), (2, 3), (seed, key))
            self.assertEqual(sit.count('sit_rest'), 9, seed)
            side = groups['side']
            first_sniff = side.index('side_sniff')
            self.assertIn(first_sniff - 1, (1, 2), seed)
            self.assertEqual(set(side[1:first_sniff]), {'side_idle_rest'}, seed)
            self.assertIn(groups['lie'].count('paws_idle_rest'), (3, 4, 5), seed)

    def test_mean_cycle_is_about_15_percent_shorter_than_old_recipe(self):
        assets = FakePlayer().assets
        before, after = [], []
        by_pose = {key: [] for key in ('stand', 'sit', 'side', 'lie')}
        for seed in range(1000):
            before.append(sum(duration(assets, g + ['stand_idle_rest'])
                              for g in old_bouts(random.Random(seed))))
            groups = bouts(random.Random(seed))
            after.append(sum(duration(assets, g + ['stand_idle_rest']) for g in groups))
            for group in groups:
                by_pose[group_name(group)].append(duration(assets, group + ['stand_idle_rest']))
        ratio = statistics.mean(after) / statistics.mean(before)
        self.assertGreater(ratio, .82)
        self.assertLess(ratio, .86)
        # Statistical comparison, not a seed-by-seed randomness guarantee.
        print('Natural cadence: old mean {:.3f}s; new mean {:.3f}s; '
              'new range {:.3f}..{:.3f}s; reduction {:.2%}'.format(
                  statistics.mean(before), statistics.mean(after), min(after), max(after), 1 - ratio))
        print('New posture means (including return and outer rest):',
              {key: round(statistics.mean(values), 3) for key, values in by_pose.items()})

    def test_idle_derivation_keeps_original_animation_metadata(self):
        pet = FakePlayer()
        # Run derivation a second time to check that base metadata is unchanged.
        originals = deepcopy({k: v for k, v in pet.assets.items() if k not in REST_KEYS})
        install_idle_assets(pet.assets)
        self.assertEqual({k: v for k, v in pet.assets.items() if k not in REST_KEYS}, originals)
        for key in ('stand_idle', 'sit_left_idle', 'sit_right_idle', 'side_idle', 'paws_idle'):
            base, rest = pet.assets[key], pet.assets[key + '_rest']
            self.assertEqual(rest['frames'], 2 * base['frames'] - 1)
            self.assertEqual(rest['pingpong_frames'], base['frames'])
            for field in ('fps', 'file', 'start', 'end', 'frame_start', 'reverse'):
                self.assertEqual(base.get(field), rest.get(field), (key, field))
        self.assertEqual(pet.assets['sit_rest']['frames'], pet.assets['sit_idle']['frames'])
        self.assertEqual(pet.assets['sit_rest']['fps'], pet.assets['sit_idle']['fps'])

    def test_free_play_nonstanding_rest_repeats_are_one_or_two(self):
        poses = {'lie_paws': 'paws_idle_rest', 'stand_side_screen_left': 'side_idle_rest',
                 'sit_look_screen_left': 'sit_left_idle_rest',
                 'sit_look_screen_right': 'sit_right_idle_rest'}
        pet = FakePlayer()
        for seed in range(1000):
            for state, idle in poses.items():
                pet.state, pet.queue, pet.free_play = state, [], True
                with patch('pet_alpha.random.randint', side_effect=random.Random(seed).randint):
                    pet.choose()
                repetitions = 0
                for key in pet.queue:
                    if key != idle:
                        break
                    repetitions += 1
                self.assertIn(repetitions, (1, 2), (seed, state))
                self.assertEqual(pet.queue[-1], 'stand_idle_rest')
                reached = state
                for key in pet.queue:
                    self.assertEqual(pet.assets[key]['start'], reached, (state, key))
                    reached = pet.assets[key]['end']
                self.assertEqual(reached, 'stand_front')


if __name__ == '__main__':
    unittest.main()
