"""
Change-point detection on short series (one value per timeline bucket).

Binary segmentation with a squared-error cost and a BIC-style penalty scaled by
a robust noise estimate. The series have at most a few thousand points, so this
small numpy implementation is exact, deterministic and fast; no extra library
is needed.
"""

from __future__ import annotations

import math

import numpy as np


def noise_sigma(x: np.ndarray) -> float:
    """Robust noise level: MAD of first differences (insensitive to the level shifts we look for)."""
    if len(x) < 3:
        return 0.0
    d = np.diff(x)
    mad = float(np.median(np.abs(d - np.median(d))))
    return mad / 0.6745 / math.sqrt(2)


def change_points(x, min_size: int = 3, max_changes: int = 5, penalty_factor: float = 3.0) -> list[int]:
    """
    Indices where a new segment starts (x[i] is the first value after the change).
    A split is kept only when it lowers the squared error by more than
    penalty_factor · 2σ² · ln(n).
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2 * min_size:
        return []
    sigma = noise_sigma(x)
    scale = max(sigma, 1e-6 * (abs(float(np.mean(x))) + 1))
    penalty = penalty_factor * 2 * scale ** 2 * math.log(n)
    cs = np.concatenate([[0.0], np.cumsum(x)])
    cs2 = np.concatenate([[0.0], np.cumsum(x * x)])

    def cost(i: int, j: int) -> float:
        m = j - i
        s = cs[j] - cs[i]
        return float(cs2[j] - cs2[i] - s * s / m)

    def best_split(i: int, j: int) -> tuple[float, int]:
        if j - i < 2 * min_size:
            return 0.0, -1
        whole = cost(i, j)
        gains = [(whole - cost(i, k) - cost(k, j), k) for k in range(i + min_size, j - min_size + 1)]
        return max(gains)

    segments = [(0, n)]
    cps: list[int] = []
    while len(cps) < max_changes:
        options = [(best_split(i, j), (i, j)) for i, j in segments]
        (gain, k), (i, j) = max(options, key=lambda o: o[0][0])
        if k < 0 or gain <= penalty:
            break
        cps.append(k)
        segments.remove((i, j))
        segments += [(i, k), (k, j)]
    return sorted(cps)


def segment_levels(x, cps: list[int]) -> list[tuple[int, int, float]]:
    """(start, end, median) for each segment between change points."""
    x = np.asarray(x, dtype=float)
    bounds = [0] + list(cps) + [len(x)]
    return [(a, b, float(np.median(x[a:b]))) for a, b in zip(bounds, bounds[1:])]
