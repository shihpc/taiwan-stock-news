# tests/test_pipeline_resilience.py
# build_news.py 管線韌性守門（2026-09-06 批次二 #4；免 token／免網路，requests 全 mock）：
#   1. 交易日查詢 memoize：build_pool（n=3）與 main（n=lookback）共用一次 API
#   2. fetch_news_one 失敗退避重試一次：例外→200 成功；非 200 兩次→回報失敗；重試計入 Throttle
#   3. 股票池／市值權重／交易日的同日快取：寫→讀 roundtrip、舊日檔清除、參數不合忽略、
#      main() 同日第二班不再重建池、--no-cache／--full 繞過
from __future__ import annotations

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_news as bn  # noqa: E402


class FakeResp:
    def __init__(self, status: int, payload=None, bad_json: bool = False):
        self.status_code = status
        self._payload = payload if payload is not None else {"data": []}
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """依 dataset 派發回應；`script` 是一串 FakeResp 或 Exception，按呼叫順序吐出。"""

    def __init__(self, script=None, by_dataset=None):
        self.script = list(script or [])
        self.by_dataset = by_dataset or {}
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        ds = (params or {}).get("dataset")
        if ds in self.by_dataset:
            return self.by_dataset[ds]
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(bn, "FINMIND_TOKEN", "stub")
    monkeypatch.setattr(bn, "RETRY_SLEEP", 0)
    monkeypatch.setattr(bn, "FETCH_PAUSE", 0)
    monkeypatch.setattr(bn, "POOL_CACHE_DIR", str(tmp_path / "cache"))
    bn._fetch_trading_dates.cache_clear()
    yield
    bn._fetch_trading_dates.cache_clear()


# ── 1. memoize ──────────────────────────────────────────────────────────
def test_trading_days_api_called_once(monkeypatch):
    dates = [f"2026-08-{d:02d}" for d in range(3, 30)]
    sess = FakeSession(by_dataset={"TaiwanStockTradingDate": FakeResp(200, {"data": [{"date": d} for d in dates]})})
    monkeypatch.setattr(bn, "_SESSION", sess)
    a = bn.recent_trading_days(3)
    b = bn.recent_trading_days(5)
    assert a == dates[-3:] and b == dates[-5:]
    assert sum(1 for c in sess.calls if c.get("dataset") == "TaiwanStockTradingDate") == 1, "交易日 API 只該打一次"


def test_trading_days_exception_not_cached(monkeypatch):
    sess = FakeSession(script=[RuntimeError("boom"),
                               FakeResp(200, {"data": [{"date": "2026-08-27"}, {"date": "2026-08-28"}]})])
    monkeypatch.setattr(bn, "_SESSION", sess)
    fallback = bn.recent_trading_days(2)       # 例外 → 平日近似
    assert len(fallback) == 2
    assert bn.recent_trading_days(2) == ["2026-08-27", "2026-08-28"], "例外不該被 lru_cache 記住"
    assert len(sess.calls) == 2


# ── 2. 重試 ──────────────────────────────────────────────────────────────
def test_fetch_news_retries_once_then_succeeds(monkeypatch):
    rec = {"date": "2026-09-05 01:00:00", "stock_id": "2330", "source": "經濟日報", "title": "t", "link": "l"}
    sess = FakeSession(script=[ConnectionError("reset"), FakeResp(200, {"data": [rec]})])
    monkeypatch.setattr(bn, "_SESSION", sess)
    th = bn.Throttle(100)
    data, ok = bn.fetch_news_one("2330", "2026-09-05", th)
    assert ok is True and data == [rec]
    assert len(sess.calls) == 2
    assert len(th.stamps) == 2, "重試也要計入每小時額度"


def test_fetch_news_non200_then_200(monkeypatch):
    sess = FakeSession(script=[FakeResp(502), FakeResp(200, {"data": []})])
    monkeypatch.setattr(bn, "_SESSION", sess)
    data, ok = bn.fetch_news_one("2330", "2026-09-05", bn.Throttle(100))
    assert ok is True and data == [] and len(sess.calls) == 2


def test_fetch_news_fails_after_two_attempts(monkeypatch):
    sess = FakeSession(script=[FakeResp(500), FakeResp(200, bad_json=True)])
    monkeypatch.setattr(bn, "_SESSION", sess)
    slept = []
    monkeypatch.setattr(bn.time, "sleep", lambda s: slept.append(s))
    data, ok = bn.fetch_news_one("2330", "2026-09-05", bn.Throttle(100))
    assert ok is False and data == []
    assert len(sess.calls) == 2, "只重試一次，不無限重試"
    assert len(slept) == 2, "退避一次 + 失敗路徑也 sleep 一次"


# ── 3. 同日快取 ─────────────────────────────────────────────────────────
POOL = pd.DataFrame({"code": ["2330", "2317"], "name": ["台積電", "鴻海"], "industry": ["半導體", "電子"]})
W = {"2330": 30.1, "2317": 3.2}
TD = ["2026-09-02", "2026-09-03", "2026-09-04"]


def test_pool_cache_roundtrip_and_cleanup():
    os.makedirs(bn.POOL_CACHE_DIR, exist_ok=True)
    stale = os.path.join(bn.POOL_CACHE_DIR, "pool_20260905.json")
    open(stale, "w").write("{}")
    bn.save_pool_cache("20260906", 150, 3, POOL, W, TD)
    assert not os.path.exists(stale), "舊日快取要自動清"
    c = bn.load_pool_cache("20260906", 150, 3)
    assert c is not None
    assert c["pool"].to_dict(orient="records") == POOL.to_dict(orient="records")
    assert c["weights"] == W and c["tdays"] == TD
    assert bn.load_pool_cache("20260906", 200, 3) is None, "max_pool 不同不可命中"
    assert bn.load_pool_cache("20260907", 150, 3) is None, "隔日不可命中"


def test_pool_cache_skips_empty_and_disabled(monkeypatch):
    bn.save_pool_cache("20260906", 150, 3, POOL.iloc[0:0], W, TD)
    assert not os.path.exists(bn.pool_cache_path("20260906")), "空池不寫"
    bn.save_pool_cache("20260906", 150, 3, POOL, {}, TD)
    assert not os.path.exists(bn.pool_cache_path("20260906")), "權重空不寫"
    monkeypatch.setattr(bn, "POOL_CACHE_DIR", None)
    bn.save_pool_cache("20260906", 150, 3, POOL, W, TD)
    assert bn.load_pool_cache("20260906", 150, 3) is None


def test_pool_cache_lookback_change_refetches_tdays(monkeypatch):
    bn.save_pool_cache("20260906", 150, 3, POOL, W, TD)
    monkeypatch.setattr(bn, "recent_trading_days", lambda n: [f"D{i}" for i in range(n)])
    c = bn.load_pool_cache("20260906", 150, 5)
    assert c["tdays"] == ["D0", "D1", "D2", "D3", "D4"]


def _run_main(monkeypatch, tmp_path, argv, counters):
    monkeypatch.setattr(bn, "OUTPUT_JSON", str(tmp_path / "news.json"))
    monkeypatch.setattr(bn, "taipei_today", lambda: bn.datetime(2026, 9, 6).date())
    monkeypatch.setattr(bn, "recent_trading_days", lambda n: TD[-n:])

    def build_pool(mx):
        counters["pool"] += 1
        return POOL.copy()
    monkeypatch.setattr(bn, "build_pool_from_finmind", build_pool)
    monkeypatch.setattr(bn, "fetch_market_value_weights", lambda: W)
    monkeypatch.setattr(bn, "fetch_news_one", lambda sid, d, th: ([], True))
    monkeypatch.setattr(sys, "argv", ["build_news.py"] + argv)
    bn.main()
    with open(bn.OUTPUT_JSON, encoding="utf-8") as f:
        return json.load(f)


def test_main_second_run_reads_cache(monkeypatch, tmp_path):
    counters = {"pool": 0}
    p1 = _run_main(monkeypatch, tmp_path, ["--lookback", "3"], counters)
    assert counters["pool"] == 1 and os.path.exists(bn.pool_cache_path("20260906"))
    p2 = _run_main(monkeypatch, tmp_path, ["--lookback", "3"], counters)
    assert counters["pool"] == 1, "同日第二班要讀快取、不重建池"
    assert p2["pool_size"] == p1["pool_size"] == 2 and p2["trading_days"] == TD
    _run_main(monkeypatch, tmp_path, ["--lookback", "3", "--no-cache"], counters)
    assert counters["pool"] == 2, "--no-cache 要繞過快取"
    _run_main(monkeypatch, tmp_path, ["--lookback", "3", "--full"], counters)
    assert counters["pool"] == 3, "--full 要重建（並覆寫快取）"
    _run_main(monkeypatch, tmp_path, ["--lookback", "3"], counters)
    assert counters["pool"] == 3, "--full 覆寫後的快取可被下一班命中"
