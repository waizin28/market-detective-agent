"""FRED: Treasury yields / volatility moves and scheduled macro release dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.data.http import CachedHttp, DataUnavailable

ET = ZoneInfo("America/New_York")
OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"

SERIES = {
    "DGS10": ("10-Year Treasury yield", "pct"),
    "DGS2": ("2-Year Treasury yield", "pct"),
    "VIXCLS": ("CBOE VIX", "index"),
    "DFF": ("Effective federal funds rate", "pct"),
}

# Major scheduled releases and their customary publication time (ET). The time
# lets the timing check classify same-day releases instead of marking them unknown.
MAJOR_RELEASES = {
    "Consumer Price Index": time(8, 30),
    "Employment Situation": time(8, 30),
    "Gross Domestic Product": time(8, 30),
    "Producer Price Index": time(8, 30),
    "Personal Income and Outlays": time(8, 30),
    "Advance Monthly Sales for Retail and Food Services": time(8, 30),
    "Job Openings and Labor Turnover Survey": time(10, 0),
    "FOMC Press Release": time(14, 0),
}


@dataclass
class SeriesMove:
    series_id: str
    label: str
    unit: str
    date: str
    value: float
    prev_value: float
    change: float


@dataclass
class Release:
    name: str
    date: date
    published_at: datetime | None  # None when no customary time is known


class Fred:
    def __init__(self, http: CachedHttp, api_key: str | None):
        self._http = http
        self._key = api_key

    @property
    def available(self) -> bool:
        return bool(self._key)

    async def series_move(self, series_id: str, on: date) -> SeriesMove:
        if not self._key:
            raise DataUnavailable("FRED_API_KEY not configured")
        data = await self._http.get_json(
            OBS_URL,
            source="fred",
            params={
                "series_id": series_id,
                "api_key": self._key,
                "file_type": "json",
                "observation_start": (on - timedelta(days=10)).isoformat(),
                "observation_end": on.isoformat(),
            },
            ttl=6 * 3600,
        )
        obs = [o for o in data.get("observations", []) if o.get("value") not in (".", None)]
        if len(obs) < 2 or obs[-1]["date"] != on.isoformat():
            raise DataUnavailable(f"FRED {series_id}: no observation on {on}")
        cur, prev = float(obs[-1]["value"]), float(obs[-2]["value"])
        label, unit = SERIES.get(series_id, (series_id, ""))
        return SeriesMove(series_id, label, unit, obs[-1]["date"], cur, prev, cur - prev)

    async def releases_between(self, start: date, end: date, include_future: bool = False) -> list[Release]:
        if not self._key:
            raise DataUnavailable("FRED_API_KEY not configured")
        data = await self._http.get_json(
            RELEASE_DATES_URL,
            source="fred",
            params={
                "api_key": self._key,
                "file_type": "json",
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
                "include_release_dates_with_no_data": "true" if include_future else "false",
                "limit": 1000,
            },
            ttl=12 * 3600,
        )
        out = []
        for r in data.get("release_dates", []):
            name = r.get("release_name", "")
            if name not in MAJOR_RELEASES:
                continue
            d = date.fromisoformat(r["date"])
            t = MAJOR_RELEASES[name]
            out.append(Release(name=name, date=d, published_at=datetime.combine(d, t, tzinfo=ET)))
        return out
