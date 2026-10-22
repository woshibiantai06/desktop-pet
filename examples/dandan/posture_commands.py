import random
import heapq
from itertools import count

TRANSITIONS=['sit_down','sit_up','lie_down','lie_up','head_down','head_up','stand_look',
             'sit_from_left','sit_from_right','sit_to_left','sit_to_right','side_return','quarter_return_alt','turn_exit_return','entry_approach']

def route_to(assets,start,target,cost=None):
    serial=count();queue=[(0.,next(serial),start,[])];best={start:0.}
    while queue:
        elapsed,_,state,route=heapq.heappop(queue)
        if elapsed>best[state]:continue
        if state==target:return route
        keys=TRANSITIONS+[k for k,a in assets.items() if a.get('routing_transition') and k not in TRANSITIONS]
        for key in keys:
            if key not in assets:continue
            a=assets[key]
            if a['start']==state:
                duration=cost(key,a) if cost else a['frames']/a['fps']
                total=elapsed+max(.001,duration)
                if total<best.get(a['end'],float('inf')):
                    best[a['end']]=total;heapq.heappush(queue,(total,next(serial),a['end'],route+[key]))
    raise ValueError(f'No safe posture route: {start} -> {target}')

def after_command(target):
    if target=='stand_front':return ['stand_idle_rest']
    if target=='sit_front':
        return ['sit_rest',random.choice(['sit_tilt','sit_look','sit_scratch']),'sit_rest','sit_up','stand_idle_rest']
    return ['lie_quiet','head_down','paws_idle_rest','head_up','lie_up','sit_up','stand_idle_rest']
