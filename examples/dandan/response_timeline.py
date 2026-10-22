"""Keep every source pose while spending less time on audited quiet sections.

This timeline changes media time only.  It does not edit, interpolate, crop or
reorder image frames.  The owner's smooth overall RateRamp remains independent.
Audited ranges use the action's *logical* frame indices, never NPZ indices.
"""
from bisect import bisect_right
from math import floor, isfinite


# Half-open logical frame ranges, verified from decoded RGBA movement on Oct 19.
QUIET_RANGES = {
    'head_up': ((90, 123),),
    'side_return': ((0, 105),),
    'sit_up': ((0, 30),),
    'walk_left_enter': ((0, 45),),
    'walk_right_enter': ((0, 45),),
}
QUIET_RATE = 2.2
RAMP_FRAMES = 4.0


def _smoothstep(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _weight(position, ranges, source_frames):
    strength = 0.0
    for start, end in ranges:
        start, end = max(0.0, float(start)), min(float(source_frames), float(end))
        if start >= end or position < start or position >= end:
            continue
        left = 1.0 if start <= 0 else _smoothstep((position - start) / RAMP_FRAMES)
        right = 1.0 if end >= source_frames else _smoothstep((end - position) / RAMP_FRAMES)
        strength = max(strength, min(left, right))
    # Ramp the local rate, not pixel geometry.  Each source-frame interval has
    # strictly positive media duration, including the quiet interval endpoints.
    return 1.0 / (1.0 + (QUIET_RATE - 1.0) * strength)


class QuietTimeline:
    """Invertible local frame/media-time mapping; the completion point is N.

    ``ranges`` are half-open logical frame intervals.  With no ranges this is
    exactly the usual ``frame = seconds * fps`` mapping.  Between integer source
    frames, time is continuous and linear; four-frame smoothstep shoulders make
    the local speed approach/leave quiet sections progressively.
    """
    def __init__(self, clip, ranges=None):
        self.frames = int(clip['frames'])
        self.fps = float(clip['fps'])
        if self.frames <= 0 or not isfinite(self.fps) or self.fps <= 0:
            raise ValueError('Frame count and frame rate must be positive')
        self.ranges = tuple(ranges or ())
        weights = [_weight(i + 0.5, self.ranges, self.frames) for i in range(self.frames)]
        self._set_weights(weights)

    def _set_weights(self, weights):
        if len(weights) != self.frames or any(not isfinite(w) or w <= 0 for w in weights):
            raise ValueError('Each source frame needs a positive finite duration')
        self.weights = tuple(float(w) for w in weights)
        self._times = [0.0]
        for weight in self.weights:
            self._times.append(self._times[-1] + weight / self.fps)
        self.duration = self._times[-1]

    def source_position(self, media_seconds):
        seconds = float(media_seconds)
        if seconds <= 0:
            return 0.0
        if seconds >= self.duration:
            return float(self.frames)
        index = bisect_right(self._times, seconds) - 1
        fraction = (seconds - self._times[index]) / (self._times[index + 1] - self._times[index])
        return index + fraction

    def media_seconds(self, source_frame):
        frame = float(source_frame)
        if frame <= 0:
            return 0.0
        if frame >= self.frames:
            return self.duration
        index = floor(frame)
        return self._times[index] + (frame - index) * (self._times[index + 1] - self._times[index])


def timeline_for(key, clip, urgent):
    """Select quiet timing, including an exact reverse-prefix source mapping.

    Optional metadata for dynamically derived clips:
      timing_source_key: the audited original action key;
      timing_indices: list of original *logical* source indices, one per local
        frame (e.g. [37, 36, ..., 0] for a played-prefix reversal);
      timing_source_frames: original action's total logical frame count.

    As a compact alternative, timing_source_frame_start gives the first original
    logical index and timing_reverse=True means subsequent indices decrease.
    Without a start, reverse begins at timing_source_frames - 1.

    Existing ``frame_start`` / ``reverse`` describe archive lookup only and are
    deliberately ignored: e.g. side_return is already audited in reverse order.
    """
    timeline = QuietTimeline(clip)
    source_key = clip.get('timing_source_key', key)
    ranges = QUIET_RANGES.get(source_key, ()) if urgent else ()
    if not ranges:
        return timeline
    mapped = clip.get('timing_indices')
    if mapped is None and any(name in clip for name in ('timing_source_frame_start', 'timing_reverse')):
        source_frames = int(clip.get('timing_source_frames', timeline.frames))
        reverse = bool(clip.get('timing_reverse', False))
        start = clip.get('timing_source_frame_start', source_frames - 1 if reverse else 0)
        mapped = [float(start) + (-i if reverse else i) for i in range(timeline.frames)]
    if mapped is None:
        return QuietTimeline(clip, ranges)
    if len(mapped) != timeline.frames:
        raise ValueError('timing_indices must contain one source index per frame')
    source_frames = int(clip.get('timing_source_frames', max(max(mapped) + 1, max(end for _, end in ranges))))
    if any(not isfinite(float(i)) or i < 0 or i >= source_frames for i in mapped):
        raise ValueError('timing_indices fall outside the original logical source')
    timeline.ranges = tuple(ranges)
    timeline._set_weights([_weight(float(i) + 0.5, ranges, source_frames) for i in mapped])
    return timeline
