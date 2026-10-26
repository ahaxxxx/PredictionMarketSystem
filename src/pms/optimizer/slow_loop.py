"""M3 — slow-loop validator.

Full backtest of a candidate composition: unit-share binary-market trading
with explicit fees and slippage, evaluated over walk-forward time windows so
every promotion decision carries a genuine out-of-sample figure.

Trading model (deliberately simple and deterministic):

* At each replay point with a live ``yes_price``, trade only when the edge
  ``|forecast - price|`` clears ``edge_threshold``.
* Edge up (forecast above price) buys YES; edge down buys NO.  NO is priced
  as ``1 - yes_price``.
* Effective price = price adjusted by ``slippage_bps`` against the trader,
  plus a ``fee_bps`` charge on notional.  One unit share is traded, so cost
  drag is visible per trade instead of being hidden in position sizing.
* At resolution a YES share pays ``actual``; a NO share pays ``1 - actual``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pms.optimizer.replay import OutcomeStore, ReplayPoint, replay_composition
from pms.optimizer.schemas import CandidateSpec, SlowScore, WindowScore
from pms.research.cache import FactorPanel

__all__ = ("SlowLoopValidator",)


@dataclass(frozen=True, slots=True)
class SlowLoopValidator:
    """Walk-forward backtester with fees and slippage."""

    fee_bps: float = 25.0
    slippage_bps: float = 10.0
    n_windows: int = 4
    # Fraction of windows (counted from the front) treated as in-sample.
    # The fast loop scores on the in-sample span; the rest is out-of-sample.
    in_sample_fraction: float = 0.5
    min_trades_per_window: int = 1

    def validate(
        self,
        candidate: CandidateSpec,
        panel: FactorPanel,
        outcomes: OutcomeStore,
    ) -> SlowScore:
        points = replay_composition(candidate, panel, outcomes)
        windows = self._split_windows(points)
        n_in_sample = int(round(len(windows) * self.in_sample_fraction))
        window_scores: list[WindowScore] = []
        for index, window_points in enumerate(windows):
            window_scores.append(
                self._score_window(
                    window_index=index,
                    points=window_points,
                    edge_threshold=candidate.edge_threshold,
                    in_sample=index < n_in_sample,
                )
            )
        total_pnl = sum(window.pnl for window in window_scores)
        total_trades = sum(window.n_trades for window in window_scores)
        return SlowScore(
            windows=tuple(window_scores),
            total_pnl=total_pnl,
            max_drawdown=_max_drawdown([w.pnl for w in window_scores]),
            pnl_per_trade=total_pnl / max(1, total_trades),
            fee_bps=self.fee_bps,
            slippage_bps=self.slippage_bps,
        )

    def passes_gate(self, score: SlowScore) -> bool:
        oos = score.out_of_sample
        if not oos:
            return False
        if any(w.n_trades < self.min_trades_per_window for w in oos):
            return False
        return score.oos_pnl() > 0.0

    # ---- internals --------------------------------------------------------

    def _split_windows(
        self, points: tuple[ReplayPoint, ...]
    ) -> list[tuple[ReplayPoint, ...]]:
        if not points:
            return []
        ordered = sorted(points, key=lambda point: point.ts)
        n = min(self.n_windows, len(ordered))
        windows: list[tuple[ReplayPoint, ...]] = []
        per_window = len(ordered) // n
        start = 0
        for index in range(n):
            end = len(ordered) if index == n - 1 else start + per_window
            windows.append(tuple(ordered[start:end]))
            start = end
        return windows

    def _score_window(
        self,
        *,
        window_index: int,
        points: tuple[ReplayPoint, ...],
        edge_threshold: float,
        in_sample: bool,
    ) -> WindowScore:
        pnl = 0.0
        trades = 0
        hits = 0
        brier_total = 0.0
        for point in points:
            brier_total += point.brier()
            trade = self._simulate_trade(point, edge_threshold)
            if trade is None:
                continue
            trade_pnl, won = trade
            pnl += trade_pnl
            trades += 1
            hits += 1 if won else 0
        return WindowScore(
            window_index=window_index,
            ts_start=points[0].ts.isoformat() if points else "",
            ts_end=points[-1].ts.isoformat() if points else "",
            in_sample=in_sample,
            n_trades=trades,
            pnl=pnl,
            hit_rate=hits / trades if trades else 0.0,
            brier=brier_total / len(points) if points else 1.0,
        )

    def _simulate_trade(
        self,
        point: ReplayPoint,
        edge_threshold: float,
    ) -> tuple[float, bool] | None:
        price = point.yes_price
        if price is None or not 0.0 < price < 1.0:
            return None
        edge = point.forecast - price
        buy_yes = True
        if abs(edge) < edge_threshold:
            return None
        if edge < 0.0:
            buy_yes = False
        entry = price if buy_yes else 1.0 - price
        slip = self.slippage_bps / 10_000.0
        fee = self.fee_bps / 10_000.0
        effective_entry = entry * (1.0 + slip) + fee
        payoff = point.actual if buy_yes else 1.0 - point.actual
        trade_pnl = payoff - effective_entry
        return trade_pnl, trade_pnl > 0.0


def _max_drawdown(pnls: list[float]) -> float:
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst
