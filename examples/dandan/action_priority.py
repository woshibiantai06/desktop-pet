"""Explicit owner-request labels, separate from ordinary animation scheduling.

Tags describe what an asset is; they do not speed it up on their own.  A live
``urgency_context`` is required before a posture transition is an urgent route.
This keeps the same sit/stand animation calm during autonomous daily behavior.
No renderer, input hook, or GUI dependency is needed to inspect this policy.
"""
from collections.abc import Mapping, MutableMapping


SPECIAL_ACTIONS = {
    'typing': {
        'label': '跟主人敲键盘', 'trigger': 'keyboard_activity',
        'priority': 60, 'destination': 'typing',
    },
    'walk_left': {
        'label': '向左拖动', 'trigger': 'drag_left',
        'priority': 80, 'destination': 'walk_left',
    },
    'walk_right': {
        'label': '向右拖动', 'trigger': 'drag_right',
        'priority': 80, 'destination': 'walk_right',
    },
    'pet': {
        'label': '双击头部摸头', 'trigger': 'double_click_head',
        'priority': 90, 'destination': 'current_posture',
    },
    'manual_pose': {
        'label': '手动指定姿态', 'trigger': 'radial_pose_button',
        'priority': 95, 'destination': 'requested_posture',
    },
    'exit': {
        'label': '退出桌宠', 'trigger': 'exit_requested',
        'priority': 100, 'destination': 'offscreen',
    },
}

# Planning budgets, not deadlines.  A long or unusual authored transition may
# need longer so the renderer can retain its complete motion and speed limits.
_ROUTE_SECONDS = {
    'typing': 3.2,
    'walk_left': 2.6,
    'walk_right': 2.6,
    'pet': 1.2,
    'manual_pose': 1.4,
    'exit': 2.5,
}
_STREAM_KINDS = ('typing', 'walk_left', 'walk_right')
_PET_KEYS = frozenset(('pet_stand', 'pet_sit', 'pet_paws'))
_PROTECTED_KEYS = frozenset(('entry_effect', 'entry_approach', 'exit_effect'))
_INACTIVE_PHASES = frozenset(('active', 'reached', 'complete', 'completed', 'cancelled'))


def _asset_request_kind(key):
    for kind in _STREAM_KINDS:
        if key in (kind + '_enter', kind + '_loop', kind + '_exit'):
            return kind
    if key in _PET_KEYS:
        return 'pet'
    return None


def install_action_tags(assets):
    """Add policy metadata in place and return ``assets`` for convenience.

    Owner specials receive ``action_class='owner_special'`` and their request's
    trigger/priority.  Natural posture changes remain ``posture_transition``
    with priority zero, even when they may later be reused in an urgent route.
    Existing geometry, timing, state edges, and other manifest fields survive.
    Calling this more than once produces identical metadata.
    """
    if not isinstance(assets, MutableMapping):
        raise TypeError('assets must be a mutable mapping')
    for key, clip in assets.items():
        if not isinstance(clip, MutableMapping):
            raise TypeError(f'asset {key!r} must be a mutable mapping')
        kind = _asset_request_kind(key)
        if kind is not None:
            request = SPECIAL_ACTIONS[kind]
            clip.update(action_class='owner_special', trigger=request['trigger'],
                        response_priority=request['priority'], request_kind=kind)
        elif key.startswith('entry'):
            clip.update(action_class='system_lifecycle', trigger='startup',
                        response_priority=0, request_kind=None)
        elif key in ('turn_exit', 'exit_effect'):
            clip.update(action_class='system_lifecycle', trigger='exit_requested',
                        response_priority=SPECIAL_ACTIONS['exit']['priority'],
                        request_kind='exit')
        else:
            clip.update(action_class=('posture_transition' if clip.get('start') != clip.get('end')
                                      else 'ambient'),
                        trigger='scheduler', response_priority=0, request_kind=None)
    return assets


def target_route_seconds(kind):
    """Return a soft route-time budget, or ``None`` for an unknown request."""
    return _ROUTE_SECONDS.get(kind)


def is_urgent_route(key, clip, urgency_context):
    """Whether this clip can use the requested fast *transition* timing.

    ``urgency_context`` is ``{'kind': <SPECIAL_ACTIONS key>, 'target': <state>}``.
    The caller owns its lifetime: clear it on reaching the target or cancelling
    the request.  Optional ``phase`` and ``completed`` fields can also disable
    it.  The target is deliberately not inferred from a clip's tags: a typing
    request can legitimately require a paw-to-lie, lie-to-sit, sit-to-stand
    route, or even leaving another interaction first.

    This is only permission for a continuous timing profile, not permission to
    skip frames, bypass a state edge, or interrupt a loop/pet halfway through.
    The intro and final disappearance retain their ordinary authored timing.
    """
    if not isinstance(urgency_context, Mapping):
        return False
    if urgency_context.get('kind') not in SPECIAL_ACTIONS:
        return False
    if urgency_context.get('completed') or urgency_context.get('phase') in _INACTIVE_PHASES:
        return False
    if not isinstance(clip, Mapping):
        return False
    if key in _PROTECTED_KEYS or key.startswith('entry'):
        return False
    role = clip.get('interaction_role')
    if role in ('loop', 'pet') or key in _PET_KEYS:
        return False
    # An owner interaction's entered/exited motion is meaningful even when a
    # synthetic fixture omits state fields.  Ordinary still/idle actions are
    # not route segments and should never be rushed by a stale request.
    if role in ('enter', 'exit'):
        return True
    start, end = clip.get('start'), clip.get('end')
    return start is not None and end is not None and start != end
