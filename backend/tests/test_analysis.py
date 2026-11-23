from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from app.analysis.anomaly import classify_scope, compute_anomaly
from app.analysis.scoring import decide_verdict, score_branch, select_beam
from app.analysis.timing import ET, classify
from app.models import CriticAssessment

EVENT, PREV = date(2026, 2, 26), date(2026, 2, 25)


# ---------------------------------------------------------------- timing
class TestTiming:
    def test_premarket_announcement_is_during(self):
        t = datetime(2026, 2, 26, 8, 0, tzinfo=ET)
        assert classify(t, event_date=EVENT, prev_date=PREV) == "during"

    def test_after_close_announcement_is_after(self):
        t = datetime(2026, 2, 26, 16, 15, tzinfo=ET)
        assert classify(t, event_date=EVENT, prev_date=PREV) == "after"

    def test_exactly_at_close_counts(self):
        t = datetime(2026, 2, 26, 16, 0, tzinfo=ET)
        assert classify(t, event_date=EVENT, prev_date=PREV) == "during"

    def test_prior_day_after_close_earnings_is_during(self):
        # Earnings released after the prior close drive the next day's move.
        t = datetime(2026, 2, 25, 16, 20, tzinfo=ET)
        assert classify(t, event_date=EVENT, prev_date=PREV) == "during"

    def test_older_news_is_before(self):
        t = datetime(2026, 2, 20, 9, 0, tzinfo=ET)
        assert classify(t, event_date=EVENT, prev_date=PREV) == "before"

    def test_missing_and_same_day_date_only_are_unknown(self):
        assert classify(None, event_date=EVENT, prev_date=PREV) == "unknown"
        d = datetime(2026, 2, 26, tzinfo=ET)
        assert classify(d, event_date=EVENT, prev_date=PREV, date_only=True) == "unknown"


# ---------------------------------------------------------------- anomaly
def _frame(returns, start="2026-01-01", volume=1e6):
    closes = 100 * np.cumprod(1 + np.asarray(returns))
    idx = pd.bdate_range(start, periods=len(returns))
    df = pd.DataFrame({"close": closes, "volume": volume}, index=idx)
    df.attrs["adjusted"] = True
    return df


class TestAnomaly:
    def test_detects_known_outlier_and_excludes_event_from_baseline(self):
        rng = np.random.default_rng(0)
        rets = list(rng.normal(0, 0.02, 80)) + [-0.08]
        df = _frame(rets)
        a = compute_anomaly("NVDA", df, {}, lookback=60, z_threshold=2, scan_days=5)
        base = pd.Series(df["close"]).pct_change().iloc[-61:-1]
        expected_z = (-0.08 - base.mean()) / base.std(ddof=1)
        assert a.flagged and a.direction == "down"
        assert a.z_score == pytest.approx(expected_z, rel=1e-9)
        assert a.ret == pytest.approx(-0.08)

    def test_company_specific_vs_sector_wide(self):
        rng = np.random.default_rng(1)
        base = list(rng.normal(0, 0.01, 70))
        stock = _frame(base + [-0.08])
        flat = _frame(list(rng.normal(0, 0.01, 70)) + [-0.002])
        a = compute_anomaly(
            "X", stock, {"SPY": ("market", flat), "SOXX": ("sector", flat)},
            lookback=60, z_threshold=2, scan_days=3,
        )
        assert a.scope == "company_specific"
        assert classify_scope(-0.08, -0.01, -0.07) == "sector_wide"
        assert classify_scope(-0.08, -0.06, -0.07) == "market_wide"

    def test_flags_possible_split_in_unadjusted_data(self):
        rets = [0.001] * 70 + [-0.5]
        df = _frame(rets)
        df.attrs["adjusted"] = False
        a = compute_anomaly("X", df, {}, lookback=60, z_threshold=2, scan_days=3)
        assert any("split" in i for i in a.data_issues)

    def test_requested_date_snaps_to_prior_trading_day(self):
        df = _frame([0.001] * 80)
        sat = df.index[-1] + pd.Timedelta(days=1)
        a = compute_anomaly("X", df, {}, lookback=60, z_threshold=2, scan_days=3,
                            requested_date=sat.date())
        assert a.event_date == df.index[-1].date().isoformat()


# ---------------------------------------------------------------- scoring
def _ev(i, kind, timing):
    return {"id": i, "kind": kind, "timing": timing, "source": "s", "title": i}


def _assess(**kw):
    base = dict(branch_id="B1", event_documented=True, event_evidence_ids=["E1"],
                price_fit=True, independent_support=True,
                supporting_evidence_ids=["E1", "E2"], contradicting_evidence_ids=[],
                decisive_contradiction=False, rationale="r")
    base.update(kw)
    return CriticAssessment(**base)


ANOM = {"scope": "company_specific"}


class TestScoring:
    def test_full_marks(self):
        ev = {"E1": _ev("E1", "sec_filing", "during"), "E2": _ev("E2", "market_data", "during")}
        r = score_branch(_assess(), category="earnings_guidance", evidence=ev, anomaly=ANOM)
        assert r.total == 4

    def test_regression_announcement_moved_to_after_close_fails_timing(self):
        """Week 4/6 regression: moving only the timestamp to 4:15 pm must fail timing,
        regardless of how relevant or credible the evidence is."""
        ev = {"E1": _ev("E1", "sec_filing", "after"), "E2": _ev("E2", "market_data", "during")}
        r = score_branch(_assess(), category="earnings_guidance", evidence=ev, anomaly=ANOM)
        assert r.timing is False and r.total == 3
        b = {"B1": {"id": "B1", "order": 1, "status": "finalist", "challenge_completed": True,
                    "score": r.as_dict()}}
        assert decide_verdict(b, min_score=3)["status"] == "unresolved"

    def test_duplicate_news_is_not_independent(self):
        ev = {"E1": _ev("E1", "news", "during"), "E2": _ev("E2", "news", "during")}
        r = score_branch(_assess(), category="company_specific", evidence=ev, anomaly=ANOM)
        assert r.independent is False

    def test_hallucinated_ids_are_dropped(self):
        ev = {"E2": _ev("E2", "market_data", "during")}
        r = score_branch(_assess(event_evidence_ids=["E99"]), category="other", evidence=ev, anomaly=ANOM)
        assert r.event is False and "E99" in r.unknown_ids

    def test_company_cause_cannot_fit_sector_wide_move(self):
        ev = {"E1": _ev("E1", "sec_filing", "during"), "E2": _ev("E2", "market_data", "during")}
        r = score_branch(_assess(), category="earnings_guidance", evidence=ev,
                         anomaly={"scope": "sector_wide"})
        assert r.price_fit is False

    def test_beam_prunes_low_scores_and_keeps_width(self):
        def br(i, total, order, decisive=False):
            return {"id": i, "order": order, "status": "active",
                    "score": {"total": total, "primary_sources": 0, "decisive_contradiction": decisive}}
        branches = {b["id"]: b for b in [br("A", 3, 1), br("B", 1, 2), br("C", 3, 3), br("D", 4, 4, True), br("E", 2, 5)]}
        keep, pruned = select_beam(branches, round_no=1, beam_width=2, prune_below=2)
        assert keep == ["A", "C"]
        assert pruned["D"] == "ruled_out" and "below" in pruned["B"] and "beam" in pruned["E"]

    def test_unfinished_challenge_blocks_leading_and_ties_share_rank(self):
        s = {"total": 4, "event": True, "timing": True, "primary_sources": 1}
        b = {"A": {"id": "A", "order": 1, "status": "finalist", "challenge_completed": True, "score": s},
             "B": {"id": "B", "order": 2, "status": "finalist", "challenge_completed": True, "score": s},
             "C": {"id": "C", "order": 3, "status": "finalist", "challenge_completed": False, "score": s}}
        v = decide_verdict(b, min_score=3)
        assert v["leading_ids"] == ["A", "B"]
        assert v["ranks"]["A"] == v["ranks"]["B"]
        assert "final challenge not completed" in v["blockers"]["C"]
