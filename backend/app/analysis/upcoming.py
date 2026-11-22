"""What to watch next: scheduled events after today that could move the stock.

Company dates come from Yahoo Finance; macro dates from FRED's release calendar
when a key is configured, plus the Federal Reserve's published FOMC schedule.
Facts only, no predictions or advice.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.data import market as mkt
from app.data.http import BudgetExceeded, DataUnavailable

log = logging.getLogger(__name__)

# FOMC decision days (second day of each meeting), federalreserve.gov.
FOMC_DECISIONS = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
]


def _d(x) -> date | None:
    if x is None:
        return None
    if hasattr(x, "date") and callable(x.date):
        return x.date()
    return x if isinstance(x, date) else None


async def upcoming_events(ticker: str, today: date, fred=None, horizon_days: int = 45) -> list[dict]:
    end = today + timedelta(days=horizon_days)
    out: list[dict] = []

    try:
        df = await mkt.earnings_dates(ticker)
        future = df[[ts.date() >= today for ts in df.index]]
        if len(future):
            ts = future.index[0]
            est = future.iloc[0].get("EPS Estimate")
            when = "before the open" if ts.hour < 12 else "after the close" if ts.hour >= 16 else f"{ts:%-I:%M %p} ET"
            out.append({
                "date": ts.date().isoformat(), "kind": "earnings",
                "title": f"{ticker} earnings report",
                "detail": f"Expected {when} (per Yahoo; dates can shift)."
                + (f" Consensus EPS ${float(est):.2f}." if est == est and est is not None else ""),
                "url": f"https://finance.yahoo.com/calendar/earnings?symbol={ticker}",
            })
    except (DataUnavailable, BudgetExceeded) as exc:
        log.info("upcoming earnings unavailable: %s", exc)

    try:
        cal = await mkt.company_calendar(ticker)
        for key, title, detail in (
            ("Ex-Dividend Date", "Ex-dividend date", "Shares bought on or after this date don't get the next dividend."),
            ("Dividend Date", "Dividend payment", "Dividend paid to holders of record."),
        ):
            d = _d(cal.get(key))
            if d and today <= d <= end:
                out.append({"date": d.isoformat(), "kind": "dividend", "title": title, "detail": detail,
                            "url": f"https://finance.yahoo.com/quote/{ticker}/"})
    except (DataUnavailable, BudgetExceeded) as exc:
        log.info("company calendar unavailable: %s", exc)

    for d in FOMC_DECISIONS:
        if today <= d <= end:
            out.append({"date": d.isoformat(), "kind": "macro", "title": "Fed interest-rate decision (FOMC)",
                        "detail": "Statement at 2:00 PM ET; rate-sensitive stocks often react.",
                        "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"})
    if fred is not None and getattr(fred, "available", False):
        try:
            for rel in await fred.releases_between(today, end, include_future=True):
                if rel.name == "FOMC Press Release":
                    continue
                out.append({"date": rel.date.isoformat(), "kind": "macro", "title": rel.name,
                            "detail": f"Released {rel.published_at:%-I:%M %p} ET." if rel.published_at else "",
                            "url": "https://fred.stlouisfed.org/releases/calendar"})
        except (DataUnavailable, BudgetExceeded) as exc:
            log.info("FRED calendar unavailable: %s", exc)

    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: e["date"]):
        k = (e["date"], e["title"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq[:10]
