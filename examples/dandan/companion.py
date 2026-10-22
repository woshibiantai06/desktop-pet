"""Posture-led bouts; short idle units keep exit latency bounded."""
import random

def install_idle_assets(assets):
    for key in ['stand_idle','sit_left_idle','sit_right_idle','side_idle','paws_idle']:
        base=assets[key]
        assets[key+'_rest']={**base,'pingpong_frames':base['frames'],'frames':2*base['frames']-1}
    # Quiet sitting uses the supplied first neutral frame, not a repeated sit-down.
    if 'sit_idle' in assets:
        assets['sit_rest']={**assets['sit_idle'],'derived':'authored breathing sitting idle, forward playback'}
    else:
        assets['sit_rest']={**assets['sit_down'],'start':'sit_front','end':'sit_front',
                            'hold_index':-1,'frames':90,'derived':'3-second still sitting'}

def bouts(rng=random):
    stand_actions=[['lick'],['tilt'],['bark'],['stand_scratch'],['look_left','stand_look'],['stand_idle_legacy']]
    rng.shuffle(stand_actions)
    stand=['stand_idle_rest']
    for action in stand_actions:stand+=action+['stand_idle_rest']
    sit_actions=[['sit_tilt'],['sit_scratch'],['sit_scratch_alt'],['sit_look'],['sit_look_alt'],['sit_bark'],
        ['sit_to_left']+['sit_left_idle_rest']*rng.randint(2,3)+['sit_from_left'],
        ['sit_to_right']+['sit_right_idle_rest']*rng.randint(2,3)+['sit_from_right']]
    rng.shuffle(sit_actions)
    sit=['sit_down','sit_rest']
    for action in sit_actions:sit+=action+['sit_rest']
    sit+=['sit_up']
    side=['side_turn']+['side_idle_rest']*rng.randint(1,2)+['side_sniff','side_idle_rest','side_lookup','side_return',
        'stand_idle_rest','side_turn_alt','side_idle_rest','side_return_alt','quarter_turn_alt','quarter_return_alt']
    lie=['sit_down','lie_down','head_down']+['paws_idle_rest']*rng.randint(3,5)+['head_up','lie_up','sit_up']
    result=[stand,sit,side,lie];rng.shuffle(result)
    return result
