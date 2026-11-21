"""Deterministic anomaly statistics (Week 2, step 1).

All numbers in the case file come from here, not from the LLM.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.models import Anomaly, PeerMove, PricePoint

SPLIT_RATIOS = [1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 2, 3, 4, 5, 10, 20, 2 / 3, 3 / 2]


def daily_returns(df: pd.DataFrame) -> pd.Series:
    return df["close"].pct_change().dropna()


def zscores(returns: pd.Series, lookback: int) -> pd.DataFrame:
    """z-score of each day's return against the *prior* ``lookback`` returns
    (the event day itself is excluded from its own baseline)."""
    prior = returns.shift(1).rolling(lookback, min_periods=lookback)
    out = pd.DataFrame({"ret": returns, "mean": prior.mean(), "std": prior.std(ddof=1)})
    out["z"] = (out["ret"] - out["mean"]) / out["std"]
    return out.dropna()


def pick_event_date(z: pd.DataFrame, scan_days: int, requested: date | None) -> pd.Timestamp:
    if requested is not None:
        eligible = z.index[z.index <= pd.Timestamp(requested)]
        if len(eligible) == 0:
            raise ValueError(f"Not enough history before {requested} to compute a baseline")
        return eligible[-1]
    recent = z.tail(scan_days)
    return recent["z"].abs().idxmax()


def compute_anomaly(
    ticker: str,
    df: pd.DataFrame,
    benchmarks: dict[str, tuple[str, pd.DataFrame | None]],
    *,
    lookback: int,
    z_threshold: float,
    scan_days: int,
    requested_date: date | None = None,
) -> Anomaly:
    """``benchmarks`` maps ticker -> (role, history) with role in market/sector/peer."""
    issues: list[str] = []
    r = daily_returns(df)
    z = zscores(r, lookback)
    if z.empty:
        raise ValueError(f"Need at least {lookback + 2} trading days of history for {ticker}")
    t = pick_event_date(z, scan_days, requested_date)
    if requested_date and t.date() != requested_date:
        issues.append(
            f"{requested_date} was not a trading day with data; using {t.date()} instead."
        )
    row = z.loc[t]
    pos = df.index.get_loc(t)
    prev_t = df.index[pos - 1]
    close, prev_close = float(df["close"].iloc[pos]), float(df["close"].iloc[pos - 1])

    # ---- data validation (Week 6 guardrail) ---------------------------------
    if not df.attrs.get("adjusted", True):
        ratio = close / prev_close
        if any(abs(ratio - s) / s < 0.02 for s in SPLIT_RATIOS) and abs(ratio - 1) > 0.3:
            issues.append(
                f"Price ratio {ratio:.3f} on {t.date()} matches a stock-split ratio; "
                "the source is not split-adjusted, so the move may be a corporate action."
            )
    window_idx = df.index[max(0, pos - lookback) : pos + 1]
    gaps = pd.Series(window_idx).diff().dt.days.dropna()
    if (gaps > 5).any():
        issues.append(
            f"Price history has {int((gaps > 5).sum())} gap(s) longer than 5 days in the baseline window."
        )

    # ---- volume -------------------------------------------------------------
    vol = float(df["volume"].iloc[pos]) if "volume" in df and not pd.isna(df["volume"].iloc[pos]) else None
    avg_vol = df["volume"].iloc[max(0, pos - 20) : pos].mean() if "volume" in df else None
    vol_ratio = (vol / avg_vol) if vol and avg_vol and avg_vol > 0 else None

    # ---- benchmarks --------------------------------------------------------
    moves: list[PeerMove] = []
    market_ret = sector_ret = None
    market_r = None
    for bt, (role, bdf) in benchmarks.items():
        if bdf is None or bdf.empty:
            moves.append(PeerMove(ticker=bt, role=role, ret=None, available=False))
            issues.append(f"No price data for benchmark {bt}.")
            continue
        br = daily_returns(bdf)
        bret = float(br.loc[t]) if t in br.index else None
        if bret is None:
            issues.append(f"Benchmark {bt} has no data on {t.date()}.")
        moves.append(PeerMove(ticker=bt, role=role, ret=bret, available=bret is not None))
        if role == "market":
            market_ret, market_r = bret, br
        elif role == "sector" and sector_ret is None:
            sector_ret = bret

    # ---- market model (beta-adjusted abnormal return) -------------------------
    beta = abnormal = abnormal_z = None
    if market_r is not None and market_ret is not None:
        joined = pd.concat([r, market_r], axis=1, keys=["s", "m"]).dropna()
        base = joined.loc[joined.index < t].tail(lookback)
        if len(base) >= max(20, lookback // 2):
            m_var = float(np.var(base["m"], ddof=1))
            if m_var > 0:
                beta = float(np.cov(base["s"], base["m"], ddof=1)[0, 1] / m_var)
                alpha = float(base["s"].mean() - beta * base["m"].mean())
                resid = base["s"] - (alpha + beta * base["m"])
                abnormal = float(row["ret"] - (alpha + beta * market_ret))
                rs = float(resid.std(ddof=1))
                abnormal_z = abnormal / rs if rs > 0 else None

    ret = float(row["ret"])
    return Anomaly(
        ticker=ticker,
        event_date=t.date().isoformat(),
        prev_date=prev_t.date().isoformat(),
        close=close,
        prev_close=prev_close,
        ret=ret,
        mean_ret=float(row["mean"]),
        std_ret=float(row["std"]),
        z_score=float(row["z"]),
        volume=vol,
        volume_ratio=vol_ratio,
        lookback_days=lookback,
        flagged=abs(float(row["z"])) >= z_threshold,
        direction="up" if ret >= 0 else "down",
        benchmarks=moves,
        excess_vs_market=(ret - market_ret) if market_ret is not None else None,
        excess_vs_sector=(ret - sector_ret) if sector_ret is not None else None,
        beta=beta,
        abnormal_ret=abnormal,
        abnormal_z=abnormal_z,
        scope=classify_scope(ret, market_ret, sector_ret),
        data_issues=issues,
    )


def classify_scope(ret: float, market_ret: float | None, sector_ret: float | None) -> str:
    """How much of the move is shared with the market / sector on the same day."""
    if ret == 0:
        return "mixed"

    def share(b: float | None) -> float | None:
        return None if b is None else b / ret  # >0 means same direction

    m, s = share(market_ret), share(sector_ret)
    if m is not None and m >= 0.6:
        return "market_wide"
    if s is not None and s >= 0.6:
        return "sector_wide"
    known = [x for x in (m, s) if x is not None]
    if known and all(x < 0.3 for x in known):
        return "company_specific"
    return "mixed"


def price_points(df: pd.DataFrame, tail: int = 260) -> list[PricePoint]:
    d = df.tail(tail)
    return [
        PricePoint(date=i.date().isoformat(), close=round(float(c), 4), volume=None if pd.isna(v) else float(v))
        for i, c, v in zip(d.index, d["close"], d.get("volume", pd.Series([None] * len(d))))
    ]
