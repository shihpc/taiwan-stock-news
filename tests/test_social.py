# tests/test_social.py
# 社群聲量量測班（階段一）守門：免 token／免網路，ptt.cc 走 fixture 目錄、Anthropic 走假 session。
# 涵蓋 docs/social-phase1.md §4 A1 清單：板首頁解析／文章頁解析（含「爆」「X3」）／跨日歸屬／
# 代號與全名抽取（含負例）／聚合不變式／Batches 依 custom_id 對回（亂序）／sync 回退／no-key／
# robots disallow／節流計數與上限／產物無 body 欄／金鑰遮罩。
from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_social as bs  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "social")
TARGET = date(2026, 9, 26)
FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE"


def _read(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def _info() -> dict:
    with open(os.path.join(FIX, "stock_info.json"), encoding="utf-8") as f:
        return bs._info_from_rows(json.load(f))


def _fetcher(**kw) -> bs.PttFetcher:
    return bs.PttFetcher(session=None, fixture_dir=FIX, min_interval=0, sleep=lambda s: None, **kw)


class FakeResp:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAnthropic:
    """假 Anthropic：post 到 batches 回 batch id、get 輪詢回 ended、results 回 JSONL；
    post 到 /v1/messages 回 sync_reply（可為函式）。記錄所有呼叫供斷言。"""

    def __init__(self, jsonl: str | None = None, submit_fail=False, never_end=False,
                 sync_reply=None, sync_fail=False):
        self.jsonl = jsonl
        self.submit_fail = submit_fail
        self.never_end = never_end
        self.sync_reply = sync_reply
        self.sync_fail = sync_fail
        self.calls: list[tuple[str, str]] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url))
        assert headers["x-api-key"] and headers["anthropic-version"] == "2023-06-01"
        if url == bs.URL_BATCHES:
            if self.submit_fail:
                return FakeResp(500, {"type": "error", "error": {"message": "boom " + FAKE_KEY}})
            assert all("thinking" not in r["params"] for r in json["requests"])
            assert all(r["params"]["max_tokens"] == bs.LLM_MAX_TOKENS for r in json["requests"])
            return FakeResp(200, {"id": "msgbatch_1", "processing_status": "in_progress"})
        if url.endswith("/cancel"):
            return FakeResp(200, {"id": "msgbatch_1", "processing_status": "canceling"})
        if url == bs.URL_MESSAGES:
            if self.sync_fail:
                return FakeResp(500, {"type": "error", "error": {"message": "sync down"}})
            reply = self.sync_reply(json) if callable(self.sync_reply) else self.sync_reply
            return FakeResp(200, {"content": [{"type": "text", "text": reply}],
                                  "usage": {"input_tokens": 100, "output_tokens": 10}})
        raise AssertionError(url)

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append(("GET", url))
        if url.endswith("/results"):
            return FakeResp(200, None, text=self.jsonl or "")
        return FakeResp(200, {"id": "msgbatch_1",
                              "processing_status": "in_progress" if self.never_end else "ended",
                              "results_url": bs.URL_BATCHES + "/msgbatch_1/results"})


# ── 解析 ────────────────────────────────────────────────────────────────
def test_parse_board_entries_prev_link_pinned_and_deleted():
    entries, prev = bs.parse_board(_read("index.html"))
    assert prev == "/bbs/Stock/index9999.html"
    by_href = {e["href"]: e for e in entries if e["href"]}
    assert by_href["/bbs/Stock/M.1758900001.A.001.html"]["nrec"] == "爆"
    assert by_href["/bbs/Stock/M.1758900002.A.002.html"]["nrec"] == "X3"
    assert by_href["/bbs/Stock/M.1758900001.A.001.html"]["author"] == "user_a"
    assert by_href["/bbs/Stock/M.1758900001.A.001.html"]["md"] == "9/26"
    assert by_href["/bbs/Stock/M.1758900001.A.001.html"]["title"] == "[標的] 台積電 2330 多"
    deleted = [e for e in entries if "本文已被刪除" in e["title"]]
    assert len(deleted) == 1 and deleted[0]["href"] is None and bs.is_skippable(deleted[0])
    pinned = [e for e in entries if e["pinned"]]
    assert len(pinned) == 1 and pinned[0]["title"].startswith("[公告]")


def test_nrec_to_int():
    assert bs.nrec_to_int("爆") == 100
    assert bs.nrec_to_int("X3") == -30
    assert bs.nrec_to_int("XX") == -100
    assert bs.nrec_to_int("12") == 12
    assert bs.nrec_to_int("") == 0 and bs.nrec_to_int(None) == 0


def test_parse_article_meta_time_push_boo_and_body():
    a = bs.parse_article(_read("M.1758900001.A.001.html"))
    assert a["author"].startswith("user_a")
    assert a["title"] == "[標的] 台積電 2330 多"
    assert a["ts"].strftime("%Y-%m-%d %H:%M:%S") == "2026-09-26 21:03:15"
    assert (a["push"], a["boo"]) == (3, 1)          # 推 3、噓 1、→ 1 不算
    assert "先進製程滿載" in a["body"]
    assert "發信站" not in a["body"] and "push-content" not in a["body"] and ": 推" not in a["body"]
    assert bs.parse_article("<html><body>nothing</body></html>") is None
    assert bs.parse_article_ts("garbage") is None


def test_board_md_to_date_and_year_rollover():
    assert bs.md_to_date(" 9/26", TARGET) == TARGET
    assert bs.md_to_date("12/31", date(2027, 1, 2)) == date(2026, 12, 31)
    assert bs.md_to_date("2/30", TARGET) is None
    assert bs.md_to_date("", TARGET) is None


def test_collect_day_cross_day_attribution_and_failed_count():
    f = _fetcher()
    col = bs.collect_day(f, TARGET, _info())
    aids = [a["aid"] for a in col["articles"]]
    assert "M.1758900003.A.003" not in aids          # 板首頁 9/26、文章頁 9/27 00:05 → 不屬 9/26
    assert "M.1758990000.A.010" not in aids          # 9/27 列不抓
    assert "M.1700000000.A.AAA" not in aids          # 置底公告不抓
    assert set(aids) == {"M.1758900001.A.001", "M.1758900002.A.002", "M.1758900004.A.004"}
    assert col["pages_fetched"] == 3 and col["articles_n"] == 5
    assert col["articles_fetched_n"] == 4 and col["failed_n"] == 1   # 005 沒有 fixture
    assert f.requests_n == 3 + 5 + 1                  # 3 頁 + 5 篇 + 缺檔那篇的重試 1 次
    assert col["bodies"]["M.1758900001.A.001"]       # 正文只在記憶體


# ── 標的抽取 ────────────────────────────────────────────────────────────
def test_extract_codes_positive_and_negative():
    info = _info()
    assert "9999" not in info                        # type 非 twse/tpex 不進清單
    t = "2026 年看 00631L 與 2330A，台積電 2330 與鴻海；穩懋 3105 也提一下"
    assert bs.extract_codes(t, info) == ["2330", "2317", "3105"]
    assert bs.extract_codes("2026 年 2330A 00631L 1234", info) == []       # 年份／假代號／不在清單
    assert bs.extract_codes("中華電 5G", {"2412": "中華電", "0000": "中華"}) == ["2412"]  # 長名優先
    assert bs.extract_codes("", info) == []
    many = {str(1000 + i): None for i in range(20)}
    assert len(bs.extract_codes(" ".join(many), many)) == bs.SOCIAL_MAX_CODES_PER_ARTICLE


# ── 聚合 ────────────────────────────────────────────────────────────────
def test_aggregate_invariant_pos_neg_neu_unk_equals_n():
    arts = [
        {"codes": ["2330", "2317"], "push": 5, "boo": 1, "sent": {"2330": "pos", "2317": "weird"}},
        {"codes": ["2330"], "push": 2, "boo": 0, "sent": None},
        {"codes": ["2330"], "push": 1, "boo": 1, "sent": {"2330": "neg"}},
    ]
    s = bs.aggregate(arts, {"2330": "台積電"})
    assert s["2330"] == {"name": "台積電", "n": 3, "push": 8, "boo": 2, "pos": 1, "neg": 1, "neu": 0, "unk": 1}
    assert s["2317"]["unk"] == 1 and s["2317"]["name"] is None
    for v in s.values():
        assert v["pos"] + v["neg"] + v["neu"] + v["unk"] == v["n"]


# ── LLM：Batches 亂序對回／sync 回退／no-key ────────────────────────────
def _articles_with_bodies():
    col = bs.collect_day(_fetcher(), TARGET, _info())
    return col["articles"], col["bodies"]


def test_batch_results_matched_by_custom_id_out_of_order_and_errored_falls_to_sync():
    arts, bodies = _articles_with_bodies()
    fake = FakeAnthropic(jsonl=_read("batch_results.jsonl"),
                         sync_reply=lambda j: json.dumps({"2412": "neu", "2330": "neu"}))
    llm = bs.classify(arts, bodies, FAKE_KEY, fake, sleep=lambda s: None, poll_sec=0)
    by = {a["aid"]: a for a in arts}
    assert by["M.1758900001.A.001"]["sent"] == {"2330": "pos"}          # JSONL 第 3 行、含 ```json 圍欄
    assert by["M.1758900002.A.002"]["sent"] == {"2317": "neu", "2454": "neg"}
    assert by["M.1758900004.A.004"]["sent"] == {"2412": "neu", "2330": "neu"}  # errored → sync
    assert llm["via"] == "mixed" and llm["classified_n"] == 3 and llm["failed_n"] == 0
    assert llm["usage"] == {"input_tokens": 700 + 100, "output_tokens": 35 + 10}
    assert llm["model"] == bs.SOCIAL_MODEL and llm["prompt_ver"] == bs.SOCIAL_PROMPT_VER
    assert sum(1 for m, u in fake.calls if u == bs.URL_MESSAGES) == 1


def test_batch_submit_failure_falls_back_to_sync_for_all():
    arts, bodies = _articles_with_bodies()
    fake = FakeAnthropic(submit_fail=True,
                         sync_reply=lambda j: json.dumps({c: "neu" for c in json.loads(
                             j["messages"][0]["content"].split("代號清單：")[1].split("\n")[0])}))
    llm = bs.classify(arts, bodies, FAKE_KEY, fake, sleep=lambda s: None, poll_sec=0)
    assert llm["via"] == "sync" and llm["classified_n"] == 3
    assert all(set(a["sent"].values()) == {"neu"} for a in arts)


def test_batch_deadline_timeout_cancels_and_falls_back():
    arts, bodies = _articles_with_bodies()
    clock = iter(range(0, 10_000, 100))
    fake = FakeAnthropic(never_end=True, sync_reply='{"2330":"pos","2317":"neg","2454":"neu","2412":"neu"}')
    llm = bs.classify(arts, bodies, FAKE_KEY, fake, deadline_sec=250, sleep=lambda s: None,
                      clock=lambda: next(clock), poll_sec=0)
    assert ("POST", bs.URL_BATCHES + "/msgbatch_1/cancel") in fake.calls
    assert llm["via"] == "sync"


def test_invalid_json_reply_retries_once_then_unk():
    arts, bodies = _articles_with_bodies()
    fake = FakeAnthropic(submit_fail=True, sync_reply="我覺得會漲")
    llm = bs.classify(arts, bodies, FAKE_KEY, fake, sleep=lambda s: None, poll_sec=0)
    assert all(set(a["sent"].values()) == {"unk"} for a in arts)
    assert sum(1 for m, u in fake.calls if u == bs.URL_MESSAGES) == 2 * len(arts)
    s = bs.aggregate(arts, None)
    assert all(v["unk"] == v["n"] for v in s.values())
    assert llm["classified_n"] == 3


def test_sync_budget_exhausted_stops_fallback_keeps_invariant(tmp_path, capsys):
    """必修 1b：同步回退超過 SOCIAL_SYNC_BUDGET_SEC 即停止，剩餘篇計 failed_n、代號 unk，產物照寫。"""
    t = {"now": 0.0}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += s

    calls = {"n": 0}

    def slow_reply(j):
        calls["n"] += 1
        t["now"] += 400.0                              # 每次同步呼叫耗 400 秒
        return json.dumps({c: "neu" for c in json.loads(
            j["messages"][0]["content"].split("代號清單：")[1].split("\n")[0])})

    class Sess(FakeAnthropic):
        def post(self, url, headers=None, json=None, timeout=None):
            if url == bs.URL_MESSAGES:
                self.calls.append(("POST", url))
                return FakeResp(200, {"content": [{"type": "text", "text": slow_reply(json)}],
                                      "usage": {"input_tokens": 1, "output_tokens": 1}})
            return super().post(url, headers=headers, json=json, timeout=timeout)

    fake = Sess(submit_fail=True)
    f = _fetcher()
    out = bs.build(TARGET, f, _info(), key=FAKE_KEY, llm_session=fake, out_dir=str(tmp_path),
                   sleep=sleep, clock=clock, poll_sec=0, sync_budget_sec=500)
    # 第 1 篇：t=0 起算 → 呼叫（t=400）；第 2 篇：400 ≤ 500 → 呼叫（t=800）；第 3 篇：800 > 500 → 停
    assert calls["n"] == 2
    assert out["llm"]["classified_n"] == 2 and out["llm"]["failed_n"] == 1 and out["llm"]["via"] == "sync"
    unk_arts = [a for a in out["articles"] if set(a["sent"].values()) == {"unk"}]
    assert len(unk_arts) == 1
    for v in out["stocks"].values():
        assert v["pos"] + v["neg"] + v["neu"] + v["unk"] == v["n"]
    assert "同步回退超過預算" in capsys.readouterr().out
    assert (tmp_path / "2026-09-26.json").exists()   # pending 版已先落地
    assert bs.SOCIAL_SYNC_BUDGET_SEC + bs.SOCIAL_BATCH_DEADLINE_SEC < 70 * 60


def test_pending_product_written_before_classify_and_survives_exception(tmp_path, monkeypatch):
    """必修 1a：classify() 之前磁碟上已有 sent 全 null／skipped=pending 的產物；classify 拋例外時
    該版仍在、最終版記 skipped=error、聲量資料不丟。"""
    snapshots = []

    def boom(*a, **k):
        p = tmp_path / "2026-09-26.json"
        assert p.exists()
        snapshots.append(json.load(open(p, encoding="utf-8")))
        raise RuntimeError("api exploded " + FAKE_KEY)

    monkeypatch.setattr(bs, "classify", boom)
    out = bs.build(TARGET, _fetcher(), _info(), key=FAKE_KEY, llm_session=object(), out_dir=str(tmp_path))
    pend = snapshots[0]
    assert pend["llm"]["skipped"] == "pending" and all(a["sent"] is None for a in pend["articles"])
    assert pend["articles_n"] == 5 and len(pend["articles"]) == 3 and pend["stocks"]["2330"]["unk"] == 2
    assert all(v["pos"] + v["neg"] + v["neu"] + v["unk"] == v["n"] for v in pend["stocks"].values())
    idx = json.load(open(tmp_path / "index.json", encoding="utf-8"))
    assert idx["days"][0]["date"] == "2026-09-26"
    assert out["llm"]["skipped"] == "error" and out["llm"]["failed_n"] == 3
    assert all(set(a["sent"].values()) == {"unk"} for a in out["articles"])
    final = bs.write_outputs(out, str(tmp_path))
    assert json.load(open(final, encoding="utf-8"))["llm"]["skipped"] == "error"


def test_build_without_out_dir_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bs.build(TARGET, _fetcher(), _info(), key="")
    assert not os.path.exists("data")


def test_sync_total_failure_counts_failed_and_unk():
    arts, bodies = _articles_with_bodies()
    fake = FakeAnthropic(submit_fail=True, sync_fail=True)
    llm = bs.classify(arts, bodies, FAKE_KEY, fake, sleep=lambda s: None, poll_sec=0)
    assert llm["failed_n"] == 3 and llm["classified_n"] == 0 and llm["via"] is None


def test_no_key_keeps_volume_data(capsys):
    out = bs.build(TARGET, _fetcher(), _info(), key="", llm_session=None)
    assert out["llm"]["skipped"] == "no-key" and out["llm"]["via"] is None
    assert out["articles_n"] == 5 and len(out["articles"]) == 3
    assert all(a["sent"] is None for a in out["articles"])
    assert all(v["unk"] == v["n"] for v in out["stocks"].values())
    assert "::warning::" in capsys.readouterr().out


# ── robots ─────────────────────────────────────────────────────────────
def test_robots_verdict_parsing():
    assert bs.robots_verdict(_read("robots_allow.txt")) == "allow"
    assert bs.robots_verdict(_read("robots_disallow.txt")) == "disallow"
    assert bs.robots_verdict("User-agent: *\nDisallow: /\n") == "disallow"
    assert bs.robots_verdict("User-agent: Googlebot\nDisallow: /\n") == "allow"
    assert bs.robots_verdict("User-agent: foo\nUser-agent: *\nDisallow: /bbs\n") == "disallow"
    assert bs.robots_verdict("") == "allow"
    assert bs.robots_verdict(None) == "unreachable"


def test_robots_wildcard_and_dollar_rules():
    assert bs.robots_verdict("User-agent: *\nDisallow: /bbs/*/\n") == "disallow"       # 萬用字元
    assert bs.robots_verdict("User-agent: *\nDisallow: /*/Stock/\n") == "disallow"
    assert bs.robots_verdict("User-agent: *\nDisallow: /bbs/Stock/index*\n") == "disallow"
    assert bs.robots_verdict("User-agent: *\nDisallow: /bbs/$\n") == "disallow"        # 精確結尾命中 /bbs/
    assert bs.robots_verdict("User-agent: *\nDisallow: /bbs/Other/*\n") == "allow"
    assert bs.robots_verdict("User-agent: *\nDisallow: /*.png$\n") == "allow"
    assert bs.robots_verdict("User-agent: Bad\nDisallow: /bbs/*/\nUser-agent: *\nDisallow: /ask/\n") == "allow"


def test_robots_crawl_delay_parsed_and_applied_to_fetcher(tmp_path):
    rb = bs.parse_robots("User-agent: *\nCrawl-delay: 3\nDisallow: /ask/\nUser-agent: X\nCrawl-delay: 99\n")
    assert rb == {"verdict": "allow", "crawl_delay": 3.0}
    assert bs.parse_robots("User-agent: *\nCrawl-delay: abc\n")["crawl_delay"] is None
    assert bs.parse_robots("User-agent: *\nDisallow: /ask/\n")["crawl_delay"] is None
    d = tmp_path / "fix"
    d.mkdir()
    for n in os.listdir(FIX):
        (d / n).write_bytes(open(os.path.join(FIX, n), "rb").read())
    (d / "robots.txt").write_text("User-agent: *\nCrawl-delay: 3\n", encoding="utf-8")
    f = bs.PttFetcher(session=None, fixture_dir=str(d), min_interval=1.0, sleep=lambda s: None)
    out = bs.build(TARGET, f, _info(), key="")
    assert f.min_interval == 3.0 and out["robots_crawl_delay"] == 3.0 and out["robots"] == "allow"
    f2 = bs.PttFetcher(session=None, fixture_dir=str(d), min_interval=5.0, sleep=lambda s: None)
    bs.build(TARGET, f2, _info(), key="")
    assert f2.min_interval == 5.0                    # 取 max(Crawl-delay, 既有間隔)
    out0 = bs.build(TARGET, _fetcher(), _info(), key="")
    assert out0["robots_crawl_delay"] == 1.0         # robots_allow.txt


def test_robots_disallow_fetches_nothing(tmp_path, capsys):
    d = tmp_path / "fix"
    d.mkdir()
    for n in os.listdir(FIX):
        (d / n).write_bytes(open(os.path.join(FIX, n), "rb").read())
    (d / "robots.txt").write_text(_read("robots_disallow.txt"), encoding="utf-8")
    f = bs.PttFetcher(session=None, fixture_dir=str(d), min_interval=0, sleep=lambda s: None)
    out = bs.build(TARGET, f, _info(), key=FAKE_KEY, llm_session=FakeAnthropic(submit_fail=True))
    assert out["robots"] == "disallow" and out["articles_n"] == 0 and out["articles"] == []
    assert f.requests_n == 1                          # 只打了 robots
    assert out["llm"]["skipped"] == "robots" and out["stocks"] == {}
    assert "::warning::" in capsys.readouterr().out


# ── 節流（H4）────────────────────────────────────────────────────────────
def test_throttle_interval_ua_cookie_and_request_cap():
    class Sess:
        def __init__(self):
            self.n = 0
            self.headers = []

        def get(self, url, headers=None, cookies=None, timeout=None):
            self.n += 1
            self.headers.append((headers, cookies))
            assert url.startswith(bs.PTT_BASE)
            return FakeResp(200, None, text="<html></html>")

    t = {"now": 0.0}
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        t["now"] += s

    def clock():
        t["now"] += 0.1        # 每次看鐘過 0.1 秒（模擬處理時間）
        return t["now"]

    sess = Sess()
    f = bs.PttFetcher(session=sess, min_interval=1.0, max_requests=5, sleep=sleep, clock=clock)
    for i in range(7):
        f.get(f"/bbs/Stock/index{i}.html")
    assert sess.n == 5 and f.requests_n == 5 and f.exhausted
    assert len(sleeps) == 4 and all(s > 0 for s in sleeps)   # 前 5 次之間 4 個間隔都補睡到 ≥1 秒
    assert all(h["User-Agent"] == bs.SOCIAL_UA and "taiwan-stock-news" in h["User-Agent"]
               and c == {"over18": "1"} for h, c in sess.headers)
    assert bs.SOCIAL_MIN_INTERVAL >= 1.0 and bs.SOCIAL_MAX_REQUESTS <= 400


def test_fetch_retries_once_on_non_200():
    class Sess:
        def __init__(self):
            self.codes = iter([503, 200])

        def get(self, url, **kw):
            return FakeResp(next(self.codes), None, text="<html>ok</html>")

    slept = []
    f = bs.PttFetcher(session=Sess(), min_interval=0, sleep=slept.append, retry_sleep=7)
    assert f.get("/bbs/Stock/index.html") == "<html>ok</html>"
    assert f.requests_n == 2 and slept == [7]


# ── 產物（H3／H9）與金鑰（H6）────────────────────────────────────────────
def test_output_schema_no_body_no_judgement_and_index(tmp_path):
    out = bs.build(TARGET, _fetcher(), _info(), key="", fixture=True)
    path = bs.write_outputs(out, str(tmp_path))
    raw = open(path, encoding="utf-8").read()
    j = json.loads(raw)
    assert "body" not in raw and "先進製程滿載" not in raw
    for a in j["articles"]:
        assert set(a) == {"aid", "title", "author", "ts", "push", "boo", "cat", "url", "codes", "sent"}
    for k in ("偏多", "偏空", "建議", "signal", "score", "bias"):
        assert k not in raw
    assert set(j) == {"schema", "date", "generated_at", "source", "robots", "pages_fetched", "articles_n",
                      "articles_fetched_n", "failed_n", "requests_n", "elapsed_s", "llm", "stocks",
                      "articles", "teardown", "fixture", "robots_crawl_delay"}
    assert j["robots_crawl_delay"] == 1.0            # robots_allow.txt 宣告 Crawl-delay: 1
    assert set(j["llm"]) == {"model", "prompt_ver", "via", "skipped", "classified_n", "failed_n", "usage"}
    assert j["fixture"] is True and j["teardown"]["since"] == bs.SOCIAL_SAMPLE_SINCE
    idx = json.load(open(tmp_path / "index.json", encoding="utf-8"))
    assert [d["date"] for d in idx["days"]] == ["2026-09-26"] and idx["days"][0]["fixture"] is True
    # 同日重寫不重複、跨日排序
    out2 = dict(out, date="2026-09-25")
    bs.write_outputs(out2, str(tmp_path))
    bs.write_outputs(out, str(tmp_path))
    idx = json.load(open(tmp_path / "index.json", encoding="utf-8"))
    assert [d["date"] for d in idx["days"]] == ["2026-09-25", "2026-09-26"]


def test_mask_secret_and_no_key_in_logs(capsys):
    assert "FAKE" not in bs.mask_secret(f"401 unauthorized key={FAKE_KEY}", FAKE_KEY)
    assert bs.mask_secret("x sk-ant-abc_123 y") == "x sk-ant-*** y"
    assert bs.mask_secret("token=abcdef&x=1", "") == "token=***&x=1"
    assert bs.mask_secret("short", "ab") == "short"
    arts, bodies = _articles_with_bodies()
    bs.classify(arts, bodies, FAKE_KEY, FakeAnthropic(submit_fail=True, sync_fail=True), sleep=lambda s: None)
    assert FAKE_KEY not in capsys.readouterr().out


def test_teardown_due_warning(capsys):
    since = date.fromisoformat(bs.SOCIAL_SAMPLE_SINCE)
    assert bs.teardown_info(since)["due"] is False
    t = bs.teardown_info(date.fromordinal(since.toordinal() + bs.SOCIAL_TEARDOWN_DUE_DAYS))
    assert t["due"] is True and t["age_days"] == bs.SOCIAL_TEARDOWN_DUE_DAYS
    due_day = date.fromordinal(since.toordinal() + bs.SOCIAL_TEARDOWN_DUE_DAYS)
    out = bs.build(due_day, _fetcher(), _info(), key="", fixture=True)   # fixture 無該日文章也照提醒
    assert out["teardown"]["due"] is True
    assert "社群量測班已滿" in capsys.readouterr().out


def test_load_stock_info_from_path_and_missing_token(tmp_path, capsys, monkeypatch):
    info = bs.load_stock_info(os.path.join(FIX, "stock_info.json"), "20260926", None, "")
    assert info["2330"] == "台積電" and "9999" not in info
    monkeypatch.setattr(bs, "CACHE_DIR", str(tmp_path))
    assert bs.load_stock_info(None, "20260926", None, "") is None
    assert "::warning::" in capsys.readouterr().out


def test_main_from_fixture_writes_files(tmp_path):
    rc = bs.main(["--from-fixture", FIX, "--stock-info", os.path.join(FIX, "stock_info.json"),
                  "--no-llm", "--date", "2026-09-26", "--out-dir", str(tmp_path)])
    assert rc == 0
    j = json.load(open(tmp_path / "2026-09-26.json", encoding="utf-8"))
    assert j["llm"]["skipped"] == "no-llm" and j["fixture"] is True and j["stocks"]["2330"]["n"] == 2


def test_main_exit_2_on_classify_exception_but_product_written(tmp_path, monkeypatch):
    """延後紅燈：classify() 例外 → 產物仍落地（skipped=error）、main() 回 2。"""
    def boom(*a, **k):
        raise RuntimeError("api exploded")

    monkeypatch.setattr(bs, "classify", boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    rc = bs.main(["--from-fixture", FIX, "--stock-info", os.path.join(FIX, "stock_info.json"),
                  "--date", "2026-09-26", "--out-dir", str(tmp_path)])
    assert rc == 2
    j = json.load(open(tmp_path / "2026-09-26.json", encoding="utf-8"))
    assert j["llm"]["skipped"] == "error" and j["articles_n"] == 5 and len(j["articles"]) == 3
    assert all(v["unk"] == v["n"] for v in j["stocks"].values())


def test_main_exit_2_when_all_articles_fail_classification(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(bs.requests, "Session", lambda: FakeAnthropic(submit_fail=True, sync_fail=True))
    monkeypatch.setattr(bs, "LLM_RETRY_SLEEP", 0)
    rc = bs.main(["--from-fixture", FIX, "--stock-info", os.path.join(FIX, "stock_info.json"),
                  "--date", "2026-09-26", "--out-dir", str(tmp_path)])
    assert rc == 2
    j = json.load(open(tmp_path / "2026-09-26.json", encoding="utf-8"))
    assert j["llm"]["skipped"] is None and j["llm"]["failed_n"] == 3 and j["llm"]["classified_n"] == 0


def test_main_exit_0_on_expected_skips(tmp_path, monkeypatch):
    base = ["--from-fixture", FIX, "--stock-info", os.path.join(FIX, "stock_info.json"), "--date", "2026-09-26"]
    assert bs.main(base + ["--no-llm", "--out-dir", str(tmp_path / "a")]) == 0
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert bs.main(base + ["--out-dir", str(tmp_path / "b")]) == 0          # no-key
    d = tmp_path / "fix"
    d.mkdir()
    for n in os.listdir(FIX):
        (d / n).write_bytes(open(os.path.join(FIX, n), "rb").read())
    (d / "robots.txt").write_text(_read("robots_disallow.txt"), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    assert bs.main(["--from-fixture", str(d), "--stock-info", os.path.join(FIX, "stock_info.json"),
                    "--date", "2026-09-26", "--out-dir", str(tmp_path / "c")]) == 0   # robots disallow
    assert bs.llm_exit_code({"llm": {"skipped": None, "failed_n": 1}, "articles": [{"codes": ["2330"]}, {"codes": ["2317"]}]}) == 0  # 部分失敗不算
    assert bs.llm_exit_code({"llm": {"skipped": "no-articles", "failed_n": 0}, "articles": []}) == 0


@pytest.mark.parametrize("title,cat", [("[標的] 台積電 多", "標的"), ("Re: [新聞] x", "新聞"), ("無分類", None)])
def test_title_cat(title, cat):
    assert bs.title_cat(title) == cat
