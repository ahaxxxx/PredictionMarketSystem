"""Alpha-style information-coefficient metrics (AlphaBench / Grinold & Kahn).

These follow the standard formulaic alpha-factor mining definitions:

* **IC** — Pearson correlation between the predicted edge and the realized
  return of acting on it.
* **RankIC** — the same correlation on rank-transformed values, robust to
  outliers.
* **ICIR** — mean per-market IC divided by its standard deviation, a
  stability measure (analogous to a t-statistic of the factor).

Pure standard-library implementations; inputs are the flat replay points
so M2 and any future evaluator share one definition.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from pms.optimizer.replay import ReplayPoint

__all__ = ("ic_series", "pearson", "rank_ic", "spearman")


def edge_return_pairs(
    points: tuple[ReplayPoint, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(edge, realized return) pairs; points without a live price are dropped."""
    edges: list[float] = []
    realized: list[float] = []
    for point in points:
        if point.yes_price is None or not 0.0 < point.yes_price < 1.0:
            continue
        edges.append(point.forecast - point.yes_price)
        realized.append(point.actual - point.yes_price)
    return tuple(edges), tuple(realized)


def pearson(xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    """Pearson correlation; 0.0 when variance is degenerate."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0.0 or var_y <= 0.0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


def spearman(xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    """Spearman rank correlation (average ranks for ties)."""
    if len(xs) < 2 or len(xs) != len(ys):
        return 0.0
    return pearson(_ranks(xs), _ranks(ys))


def ic_series(
    points: tuple[ReplayPoint, ...],
) -> Mapping[str, float]:
    """IC across all points plus per-market ICs for the ICIR stability term."""
    edges, realized = edge_return_pairs(points)
    per_market: dict[str, list[tuple[float, float]]] = {}
    for point in points:
        if point.yes_price is None or not 0.0 < point.yes_price < 1.0:
            continue
        per_market.setdefault(point.market_id, []).append(
            (point.forecast - point.yes_price, point.actual - point.yes_price)
        )
    market_ics = [
        pearson(
            tuple(pair[0] for pair in pairs),
            tuple(pair[1] for pair in pairs),
        )
        for pairs in per_market.values()
        if len(pairs) >= 3
    ]
    return {
        "ic": pearson(edges, realized),
        "rank_ic": spearman(edges, realized),
        "icir": _icir(tuple(market_ics)),
        "n_ic_markets": float(len(market_ics)),
    }


def _icir(market_ics: tuple[float, ...]) -> float:
    if len(market_ics) < 2:
        return 0.0
    mean = sum(market_ics) / len(market_ics)
    variance = sum((value - mean) ** 2 for value in market_ics) / (len(market_ics) - 1)
    std = math.sqrt(variance)
    if std <= 0.0:
        return 0.0
    return mean / std


def _ranks(values: tuple[float, ...]) -> tuple[float, ...]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index
        while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[index][1]:
            end += 1
        average_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[indexed[position][0]] = average_rank
        index = end + 1
    return tuple(ranks)
