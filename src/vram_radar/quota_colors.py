"""One quota colour rule for every provider and surface.

The Codex gradient anchors (berry -> coral -> amber -> jade -> ocean blue,
low -> full) are joined by a smooth Catmull-Rom curve in OKLab and walked at
constant perceptual speed (arc length), so each percent moves the colour by
the same visible amount -- no tiers, no stalls at anchors.  The result is then
lightness-adjusted (hue kept) until it reaches WCAG 4.5:1 against the real
surface behind the text, so it stays readable on light and dark taskbars and
all background styles.
"""
from __future__ import annotations

import math
from functools import lru_cache

# Text on a dark surface / on a light surface (unchanged Codex anchors).
ANCHORS = {False: ((227, 143, 163), (230, 166, 135), (218, 190, 119), (105, 195, 173), (112, 184, 220)),
           True: ((160, 62, 96), (184, 108, 84), (158, 130, 67), (51, 139, 120), (55, 125, 163))}
NEUTRAL = {False: (160, 168, 173), True: (112, 120, 125)}
REFERENCE_SURFACE = {False: (32, 32, 32), True: (243, 243, 243)}
MIN_CONTRAST = 4.5
SAMPLES_PER_SEGMENT = 64
WAIT_MIDPOINT = 64800   # seconds; legacy "waiting" scale (18 h midpoint)


def _linear(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _encode(c):
    c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return max(0, min(255, round(c * 255)))


def to_oklab(rgb):
    r, g, b = (_linear(v) for v in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _oklab_linear(lab):
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def from_oklab(lab):
    """sRGB bytes; out-of-gamut colours lose chroma (hue and lightness kept)."""
    L, a, b = lab
    lo, hi = 0.0, 1.0
    rgb = _oklab_linear((L, a, b))
    if all(-1e-6 <= v <= 1 + 1e-6 for v in rgb):
        return tuple(_encode(min(1, max(0, v))) for v in rgb)
    for _ in range(24):
        mid = (lo + hi) / 2
        test = _oklab_linear((L, a * mid, b * mid))
        if all(-1e-6 <= v <= 1 + 1e-6 for v in test):
            lo = mid
        else:
            hi = mid
    return tuple(_encode(min(1, max(0, v))) for v in _oklab_linear((L, a * lo, b * lo)))


def luminance(rgb):
    r, g, b = (_linear(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = luminance(fg), luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


@lru_cache(maxsize=2)
def _curve(bright: bool):
    """Dense OKLab samples of the anchor curve plus cumulative arc length."""
    points = [to_oklab(c) for c in ANCHORS[bright]]
    ext = [points[0], *points, points[-1]]
    samples = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for k in range(SAMPLES_PER_SEGMENT):
            t = k / SAMPLES_PER_SEGMENT
            t2, t3 = t * t, t * t * t
            samples.append(tuple(0.5 * (2 * b1 + (-a0 + c2) * t + (2 * a0 - 5 * b1 + 4 * c2 - d3) * t2
                                        + (-a0 + 3 * b1 - 3 * c2 + d3) * t3)
                                 for a0, b1, c2, d3 in zip(p0, p1, p2, p3)))
    samples.append(points[-1])
    lengths = [0.0]
    for p, q in zip(samples, samples[1:]):
        lengths.append(lengths[-1] + math.dist(p, q))
    return samples, lengths


def curve_lab(fraction: float, bright: bool):
    """OKLab colour at ``fraction`` (0 = empty, 1 = full) of equal-speed curve."""
    samples, lengths = _curve(bright)
    target = max(0.0, min(1.0, fraction)) * lengths[-1]
    lo, hi = 0, len(lengths) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if lengths[mid] <= target:
            lo = mid
        else:
            hi = mid
    span = lengths[hi] - lengths[lo]
    t = (target - lengths[lo]) / span if span else 0.0
    return tuple(a + (b - a) * t for a, b in zip(samples[lo], samples[hi]))


def readable(lab, surface, minimum=MIN_CONTRAST):
    """Move lightness away from ``surface`` (hue kept) until ``minimum``
    contrast, or as far as sRGB allows."""
    darker = luminance(surface) > 0.18
    L, a, b = lab
    rgb = from_oklab((L, a, b))
    if contrast(rgb, surface) >= minimum:
        return rgb
    # Smallest lightness shift that reaches the target (bisection keeps the
    # adjusted colours continuous instead of stepping).
    near, far = L, (0.0 if darker else 1.0)
    # Mid-tone surfaces (some accent styles) cannot reach 4.5:1 with any hue;
    # then settle for 80 % of the best possible contrast and keep the hue.
    minimum = min(minimum, 0.8 * contrast(from_oklab((far, a, b)), surface))
    if contrast(rgb, surface) >= minimum:
        return rgb
    for _ in range(30):
        mid = (near + far) / 2
        if contrast(from_oklab((mid, a, b)), surface) >= minimum:
            far = mid
        else:
            near = mid
    return from_oklab((far, a, b))


@lru_cache(maxsize=4096)
def _color(fraction_key, bright, surface):
    if fraction_key is None:
        return readable(to_oklab(NEUTRAL[bright]), surface)
    return readable(curve_lab(fraction_key / 1000, bright), surface)


def usage_color(value, *, bright=False, waiting=False, surface=None):
    """Remaining percent (0-100) -> colour; None/NaN -> neutral grey."""
    surface = tuple(int(v) for v in surface) if surface is not None else REFERENCE_SURFACE[bool(bright)]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return _color(None, bool(bright), surface)
    value = max(0.0, float(value))
    fraction = 1 - WAIT_MIDPOINT / (value + WAIT_MIDPOINT) if waiting else min(1.0, value / 100)
    return _color(round(fraction * 1000), bool(bright), surface)


def quota_color(percent=None, *, low=False, warning=False, known=False, bright=False, surface=None):
    """The single rule for every provider's quota and reset text:

    * stale / error / sign-in needed -> neutral grey (the value is not current);
    * a remaining percentage -> the gradient at that percentage;
    * used up / empty balance (no percentage) -> the 0 % end;
    * any other known amount (balance, "available", reset only) -> the 100 % end;
    * no quota information at all (bare status) -> neutral grey.
    """
    if warning:
        return usage_color(None, bright=bright, surface=surface)
    if isinstance(percent, (int, float)) and not isinstance(percent, bool) and math.isfinite(percent):
        return usage_color(percent, bright=bright, surface=surface)
    if low:
        return usage_color(0, bright=bright, surface=surface)
    if known:
        return usage_color(100, bright=bright, surface=surface)
    return usage_color(None, bright=bright, surface=surface)


def js_table(steps: int = 100) -> dict:
    """Gradient for the web UI (CSS light-dark()): reference surfaces."""
    return {"light": [usage_color(p, bright=True) for p in range(steps + 1)],
            "dark": [usage_color(p, bright=False) for p in range(steps + 1)]}