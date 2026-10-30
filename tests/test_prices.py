import pandas as pd
import pytest

from debt_analysis import prices as px
from debt_analysis.service import AnalysisService
from debt_analysis.web.app import create_app


def series(vals, start="2020-01-01"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def test_quarterly_stats_return_and_drawdown():
    close = pd.Series([10.0, 20.0, 5.0], index=pd.to_datetime(["2020-03-31", "2020-06-30", "2020-09-30"]))
    st = px.quarterly_price_stats(close, close.index)
    assert st["price_close"].tolist() == [10, 20, 5]
    assert st["price_qtr_return"].iloc[1] == pytest.approx(1.0)
    assert st["price_drawdown"].iloc[2] == pytest.approx(-0.75)


def test_stale_quarter_end_price_is_missing():
    close = pd.Series([10.0], index=pd.to_datetime(["2020-01-02"]))
    st = px.quarterly_price_stats(close, pd.DatetimeIndex(["2020-06-30"]))
    assert pd.isna(st["price_close"].iloc[0])


def test_event_reaction_uses_prior_close_to_next_day_close():
    close = pd.Series([100.0, 100.0, 80.0, 70.0],
                      index=pd.to_datetime(["2020-03-02", "2020-03-03", "2020-03-04", "2020-03-05"]))
    ev = [{"filed": "2020-03-04", "relevance": "critical"}]
    r = px.event_reactions(close, ev)
    assert r[0]["price"] == 80 and r[0]["move_1d"] == pytest.approx(-0.30)  # 70/100 - 1
    s = px.summarize_reactions(ev, r)
    assert s[0]["n"] == 1 and s[0]["pct_negative"] == 1


def test_event_without_prices_is_none():
    close = series([1, 2, 3], "2021-01-04")
    assert px.event_reactions(close, [{"filed": "2015-01-05"}])[0]["move_1d"] is None


def test_overlap_check_rejects_reassigned_ticker():
    close = series([1, 2, 3], "2026-07-01")
    with pytest.raises(px.PriceError, match="reassigned"):
        px.check_overlap(close, pd.Timestamp("2009-01-01"), pd.Timestamp("2023-02-25"))
    px.check_overlap(close, pd.Timestamp("2020-01-01"), pd.Timestamp("2026-08-01"))


def test_correlation_needs_enough_observations_and_detects_sign():
    idx = pd.date_range("2018-03-31", periods=12, freq="QE")
    stats = pd.DataFrame({"price_qtr_return": range(12)}, index=idx, dtype=float)
    m = pd.DataFrame({"z_double_prime": [-v for v in range(12)]}, index=idx, dtype=float)
    row = px.price_correlations(stats, m)[0]
    assert row["level_vs_next"] == pytest.approx(-1.0)
    short = px.price_correlations(stats.iloc[:5], m.iloc[:5])[0]
    assert short["level_vs_next"] is None


def test_prices_endpoint_unavailable_is_not_an_error():
    class A:
        panel = pd.DataFrame(index=pd.to_datetime(["2020-03-31"]))
        submissions = {"tickers": []}
        cik = 1

    class Svc(AnalysisService):
        def __init__(self): pass
        def get(self, q): return A()
    r = create_app(Svc()).test_client().get("/api/prices?q=123")
    assert r.status_code == 200 and r.get_json()["available"] is False


def test_news_filters_to_ticker_sorts_and_caches(tmp_path):
    raw = [
        {"title": "old", "link": "http://x/1", "providerPublishTime": 1000, "relatedTickers": ["ORCL"], "publisher": "A"},
        {"title": "new", "link": "http://x/2", "providerPublishTime": 2000, "relatedTickers": ["ORCL", "MSFT"], "publisher": "B"},
        {"title": "other co", "link": "http://x/3", "providerPublishTime": 3000, "relatedTickers": ["XYZ"]},
        {"title": "no link", "providerPublishTime": 4000, "relatedTickers": ["ORCL"]},
    ]
    calls = []

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"news": raw}

    c = px.PriceClient(cache_dir=tmp_path)
    c.session.get = lambda *a, **k: calls.append(1) or Resp()
    items = c.news("orcl")
    assert [i["title"] for i in items] == ["new", "old"]
    c.news("orcl")
    assert len(calls) == 1  # second call served from cache
