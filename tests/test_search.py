from debt_analysis.edgar import EdgarClient, EdgarError
from debt_analysis.web.app import create_app

TICKERS = {str(i): {"cik_str": c, "ticker": t, "title": n} for i, (c, t, n) in enumerate([
    (320193, "AAPL", "Apple Inc."),
    (1318605, "TSLA", "Tesla, Inc."),
    (1045810, "NVDA", "NVIDIA CORP"),
    (1000, "APLE", "Apple Hospitality REIT, Inc."),
])}


def client(tmp_path, monkeypatch):
    c = EdgarClient(cache_dir=tmp_path)
    monkeypatch.setattr(c, "_get_json", lambda url, name: TICKERS)
    return c


def test_search_by_ticker_name_and_cik(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    assert c.search_companies("aapl")[0]["cik"] == 320193
    assert [r["ticker"] for r in c.search_companies("apple")] == ["AAPL", "APLE"]
    assert c.search_companies("hospitality")[0]["ticker"] == "APLE"
    assert c.search_companies("1318605")[0]["ticker"] == "TSLA"
    assert c.search_companies("zzzz") == [] and c.search_companies("") == []


def test_search_includes_known_delisted(tmp_path, monkeypatch):
    assert client(tmp_path, monkeypatch).search_companies("bed bath")[0]["cik"] == 886158


def test_resolve_by_name(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    assert c.resolve_cik("tesla") == 1318605
    try:
        c.resolve_cik("nonexistent co")
    except EdgarError:
        pass
    else:
        raise AssertionError


def test_search_endpoint(tmp_path, monkeypatch):
    class Svc:
        client = client(tmp_path, monkeypatch)
    r = create_app(Svc()).test_client().get("/api/search?q=nvid")
    assert r.get_json()["results"][0]["name"] == "NVIDIA CORP"
