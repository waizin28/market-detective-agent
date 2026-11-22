"""Market data: daily OHLCV history and a lightweight company profile.

Alpha Vantage is the primary source from the design docs. Its free tier is
limited (25 calls/day, ~100 days of history), so the provider falls back to
yfinance when no key is configured, when the quota is hit, or when more
history is needed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from app.data.http import CachedHttp, DataUnavailable, current_budget

log = logging.getLogger(__name__)

AV_URL = "https://www.alphavantage.co/query"


@dataclass
class Profile:
    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    currency: str | None = None
    quote_type: str | None = None


class MarketData:
    def __init__(self, http: CachedHttp, alpha_vantage_key: str | None, provider: str = "auto"):
        self._http = http
        self._av_key = alpha_vantage_key
        self._provider = provider

    # ------------------------------------------------------------------ history
    async def history(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Daily bars indexed by date with columns close, volume, adjusted (bool attr).

        ``df.attrs['adjusted']`` tells callers whether prices are split-adjusted, which
        determines whether the split heuristic in anomaly detection must run.
        """
        ticker = ticker.upper()
        use_av = self._provider in ("auto", "alpha_vantage") and self._av_key
        if use_av:
            try:
                df = await self._alpha_vantage(ticker)
                df = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
                if len(df) and df.index.min() <= pd.Timestamp(start) + pd.Timedelta(days=5):
                    return df
                log.info("Alpha Vantage history too short for %s; using yfinance", ticker)
            except DataUnavailable as exc:
                if self._provider == "alpha_vantage":
                    raise
                log.info("Alpha Vantage unavailable for %s (%s); using yfinance", ticker, exc)
        return await self._yfinance(ticker, start, end)

    async def _alpha_vantage(self, ticker: str) -> pd.DataFrame:
        data = await self._http.get_json(
            AV_URL,
            source="alpha_vantage",
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker,
                "outputsize": "compact",
                "apikey": self._av_key,
            },
            ttl=3 * 3600,
        )
        series = data.get("Time Series (Daily)") if isinstance(data, dict) else None
        if not series:
            msg = (data or {}).get("Note") or (data or {}).get("Information") or "no data"
            raise DataUnavailable(f"Alpha Vantage: {str(msg)[:160]}")
        rows = [
            {"date": pd.Timestamp(d), "close": float(v["4. close"]), "volume": float(v["5. volume"])}
            for d, v in series.items()
        ]
        df = pd.DataFrame(rows).set_index("date").sort_index()
        df.attrs["adjusted"] = False  # TIME_SERIES_DAILY is not split-adjusted
        df.attrs["source"] = "Alpha Vantage"
        return df

    async def _yfinance(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        budget = current_budget.get()
        if budget is not None:
            budget.charge("yfinance")

        def _fetch() -> pd.DataFrame:
            import yfinance as yf

            h = yf.Ticker(ticker).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), auto_adjust=True
            )
            return h

        h = await asyncio.to_thread(_fetch)
        if h is None or h.empty:
            raise DataUnavailable(f"No price history for {ticker}")
        df = pd.DataFrame(
            {"close": h["Close"].astype(float), "volume": h["Volume"].astype(float)},
        )
        df.index = pd.DatetimeIndex([pd.Timestamp(d.date()) for d in h.index], name="date")
        df.attrs["adjusted"] = True
        df.attrs["source"] = "Yahoo Finance (yfinance)"
        return df

    # ------------------------------------------------------------------ profile
    async def profile(self, ticker: str) -> Profile:
        budget = current_budget.get()
        if budget is not None:
            budget.charge("yfinance")

        def _fetch() -> dict:
            import yfinance as yf

            try:
                return yf.Ticker(ticker).info or {}
            except Exception:  # yfinance raises a variety of errors for bad symbols
                return {}

        info = await asyncio.to_thread(_fetch)
        return Profile(
            ticker=ticker.upper(),
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            market_cap=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            week52_high=info.get("fiftyTwoWeekHigh"),
            week52_low=info.get("fiftyTwoWeekLow"),
            currency=info.get("currency"),
            quote_type=info.get("quoteType"),
        )


# --------------------------------------------------------------------------
# Sector benchmark selection
# --------------------------------------------------------------------------
INDUSTRY_ETFS: dict[str, str] = {
    "semiconductors": "SOXX",
    "semiconductor equipment & materials": "SOXX",
    "software—infrastructure": "IGV",
    "software—application": "IGV",
    "software - infrastructure": "IGV",
    "software - application": "IGV",
    "biotechnology": "XBI",
    "banks—regional": "KRE",
    "banks - regional": "KRE",
    "banks—diversified": "KBE",
    "banks - diversified": "KBE",
    "oil & gas e&p": "XOP",
    "oil & gas integrated": "XLE",
    "internet retail": "XRT",
    "specialty retail": "XRT",
    "airlines": "JETS",
    "aerospace & defense": "ITA",
    "gold": "GDX",
    "solar": "TAN",
    "auto manufacturers": "CARZ",
    "drug manufacturers—general": "XPH",
    "drug manufacturers - general": "XPH",
    "medical devices": "IHI",
    "reit—residential": "XLRE",
}

SECTOR_ETFS: dict[str, str] = {
    "technology": "XLK",
    "communication services": "XLC",
    "consumer cyclical": "XLY",
    "consumer defensive": "XLP",
    "energy": "XLE",
    "financial services": "XLF",
    "healthcare": "XLV",
    "industrials": "XLI",
    "basic materials": "XLB",
    "real estate": "XLRE",
    "utilities": "XLU",
}


def sector_etf_for(profile: Profile) -> str | None:
    if profile.industry and profile.industry.lower() in INDUSTRY_ETFS:
        return INDUSTRY_ETFS[profile.industry.lower()]
    if profile.sector and profile.sector.lower() in SECTOR_ETFS:
        return SECTOR_ETFS[profile.sector.lower()]
    return None


# ---------------------------------------------------------------------------
# Company calendar data (earnings, analyst actions) and intraday bars. All from
# yfinance, free and keyless. Every call is charged to the run's budget.
# ---------------------------------------------------------------------------
def _charge() -> None:
    budget = current_budget.get()
    if budget is not None:
        budget.charge("yfinance")


async def _yf(fn):
    try:
        return await asyncio.to_thread(fn)
    except DataUnavailable:
        raise
    except Exception as exc:  # yfinance raises many error types for gaps
        raise DataUnavailable(f"yfinance: {exc}") from exc


async def earnings_dates(ticker: str, limit: int = 12) -> pd.DataFrame:
    """Past and scheduled earnings: index = report time (ET), columns
    'EPS Estimate', 'Reported EPS', 'Surprise(%)'."""
    _charge()

    def _f():
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if df is None or df.empty:
            raise DataUnavailable(f"No earnings dates for {ticker}")
        return df.sort_index()

    return await _yf(_f)


async def analyst_actions(ticker: str) -> pd.DataFrame:
    """Upgrades, downgrades and price-target changes; index = grade time (UTC)."""
    _charge()

    def _f():
        import yfinance as yf

        df = yf.Ticker(ticker).upgrades_downgrades
        if df is None or df.empty:
            raise DataUnavailable(f"No analyst actions for {ticker}")
        return df.sort_index()

    return await _yf(_f)


async def company_calendar(ticker: str) -> dict:
    """Next dividend / ex-dividend dates and earnings estimates."""
    _charge()

    def _f():
        import yfinance as yf

        return yf.Ticker(ticker).calendar or {}

    return await _yf(_f)


async def intraday(ticker: str, day: date, interval: str = "5m") -> pd.Series:
    """Regular-session closes for one day, indexed by bar START time (ET).
    Yahoo keeps 5-minute bars for about 60 days only."""
    from zoneinfo import ZoneInfo

    if (date.today() - day).days > 58:
        raise DataUnavailable(f"Intraday bars are only kept for ~60 days ({day} is older)")
    _charge()

    def _f():
        import yfinance as yf

        h = yf.Ticker(ticker).history(
            start=day.isoformat(), end=(day + timedelta(days=1)).isoformat(), interval=interval, prepost=False
        )
        if h is None or h.empty:
            raise DataUnavailable(f"No intraday bars for {ticker} on {day}")
        s = h["Close"].astype(float)
        s.index = s.index.tz_convert(ZoneInfo("America/New_York"))
        return s[s.index.date == day]

    s = await _yf(_f)
    if s.empty:
        raise DataUnavailable(f"No intraday bars for {ticker} on {day}")
    return s
