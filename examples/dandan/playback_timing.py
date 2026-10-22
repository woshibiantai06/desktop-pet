"""Continuous playback speed, independent of the wall clock used for blending."""


class RateRamp:
    def __init__(self, value=1.0, duration=.36):
        if value <= 0 or duration <= 0:
            raise ValueError('Playback speed and ramp duration must be positive')
        self.origin = self.target = float(value)
        self.started = 0.0
        self.duration = float(duration)

    def value(self, now):
        p = min(1., max(0., (now-self.started)/self.duration))
        return self.origin+(self.target-self.origin)*p*p*(3-2*p)

    def retarget(self, target, now):
        if target <= 0:
            raise ValueError('Playback speed must be positive')
        if target == self.target:
            return False
        self.origin = self.value(now)
        self.target = float(target)
        self.started = float(now)
        return True

    def _primitive(self, now):
        # Integral of smoothstep(p): p^3 - p^4 / 2, including its flat tails.
        x = (now-self.started)/self.duration
        if x <= 0:
            return self.origin*(now-self.started)
        if x >= 1:
            return self.duration*(self.origin+self.target)/2+self.target*(now-self.started-self.duration)
        return self.duration*(self.origin*x+(self.target-self.origin)*(x**3-x**4/2))

    def integral(self, start, end):
        return self._primitive(end)-self._primitive(start)
