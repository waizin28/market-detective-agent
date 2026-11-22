"""Where in the trading day did the move happen?

Pure functions over 5-minute bars (index = bar start, ET). Used to judge
whether a story published during the day came before or after the price moved.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd

BAR = timedelta(minutes=5)
OPEN = time(9, 30)


def scaled(bars: pd.Series, daily_close: float) -> pd.Series:
    """Rescale intraday closes so the last bar equals the (adjusted) daily close."""
    return bars * (daily_close / float(bars.iloc[-1]))


def price_at(bars: pd.Series, ts: datetime, prev_close: float) -> float | None:
    """Last completed-bar price at ``ts``; the prior close if before the open;
    None if ``ts`` isn't on this trading day."""
    day = bars.index[0].date()
    ts = ts.astimezone(bars.index.tz)
    if ts.date() > day:
        return None
    if ts.date() < day or ts.time() <= OPEN:
        return prev_close
    done = bars[bars.index + BAR <= ts]
    return float(done.iloc[-1]) if len(done) else prev_close


def share_of_move(price: float, prev_close: float, close: float) -> float | None:
    move = close - prev_close
    if abs(move) < 0.005 * prev_close:  # under 0.5%: shares are meaningless
        return None
    return (price - prev_close) / move


def path_summary(bars: pd.Series, prev_close: float, close: float) -> dict:
    pct = bars / prev_close - 1
    lo, hi = pct.idxmin(), pct.idxmax()
    checkpoints = {}
    for hh, mm in ((10, 0), (12, 0), (14, 0), (15, 0)):
        ts = bars.index[0].replace(hour=hh, minute=mm)
        p = price_at(bars, ts, prev_close)
        if p is not None:
            checkpoints[ts.strftime("%-I:%M %p")] = share_of_move(p, prev_close, close)
    return {
        "open_pct": float(bars.iloc[0] / prev_close - 1),
        "low_pct": float(pct.min()), "low_at": lo.strftime("%-I:%M %p"),
        "high_pct": float(pct.max()), "high_at": hi.strftime("%-I:%M %p"),
        "close_pct": float(close / prev_close - 1),
        "share_by": checkpoints,
    }
