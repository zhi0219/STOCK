from __future__ import annotations

from typing import Iterable, List, Tuple


def compute_polyline(
    points: Iterable[float], width: int, height: int, padding: int = 8
) -> List[Tuple[float, float]]:
    values = [float(value) for value in points]
    if not values:
        return []
    canvas_width = max(int(width), 1)
    canvas_height = max(int(height), 1)
    pad = max(int(padding), 0)
    plot_width = max(canvas_width - pad * 2, 1)
    plot_height = max(canvas_height - pad * 2, 1)
    lo = min(values)
    hi = max(values)
    if hi == lo:
        hi = lo + 1.0
    count = len(values)
    polyline: List[Tuple[float, float]] = []
    for idx, value in enumerate(values):
        if count == 1:
            x = pad + plot_width / 2
        else:
            x = pad + (plot_width * (idx / (count - 1)))
        ratio = (value - lo) / (hi - lo)
        y = pad + (1 - ratio) * plot_height
        x = min(max(x, 0.0), float(canvas_width))
        y = min(max(y, 0.0), float(canvas_height))
        polyline.append((x, y))
    return polyline
