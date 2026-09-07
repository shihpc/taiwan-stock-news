#!/usr/bin/env python3
# tests/test_incremental.py
# 增量抓取正確性測試：**增量結果必須與全量結果完全一致**。
#
# 增量的風險不在省請求，而在「省錯了」——漏掉台北日跨 UTC 切片的新聞、或把快取
# 區段與新抓區段接壞（重複計入 / 中間破一個洞）。這支測試用假語料把 FinMind 換掉，
# 對同一份語料跑三種情境並比對輸出：
#   1. 全量（--full）
#   2. 增量（讀前一輪 news.json 當快取）
#   3. 增量 + 池新增個股（新進池的檔必須全窗補抓）
# 另外檢查請求數真的下降了。
#
# 用法：python tests/test_incremental.py     （不需要 FINMIND_TOKEN / 網路）

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_news as bn  # noqa: E402

TDAYS = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
POOL_BASE = ["2330", "2317", "2454"]
NEW_CODE = "1234"

# 假語料：key=(code, UTC 切片)，value=[(台北時間字串, source, title)]
# 刻意涵蓋「台北日的新聞落在前一個 UTC 切片」這個關鍵情形：
#   台北 07-24 03:20 的新聞，其 UTC 時間是 07-23 19:20 → 落在切片 07-23。
CORPUS = {
    ("2330", "2026-07-19"): [("2026-07-19 10:00:00", "經濟日報", "窗外新聞不該入選")],
    ("2330", "2026-07-20"): [("2026-07-20 09:00:00", "經濟日報", "台積電法說")],
    ("2330", "2026-07-22"): [("2026-07-22 14:00:00", "工商時報", "先進封裝擴產")],
    ("2330", "2026-07-23"): [("2026-07-23 11:00:00", "鉅亨網", "外資買超"),
                             ("2026-07-24 03:20:00", "MoneyDJ", "台北清晨新聞落在前一切片")],
    ("2330", "2026-07-24"): [("2026-07-24 13:00:00", "UDN", "尾盤拉抬")],
    # 同一篇文章在相鄰切片都被 FinMind 回傳（實測全量模式 kept ~730 但實際只輸出
    # ~479，就是這種跨切片重複造成的）。全量會拿到兩份、增量從快取只拿到一份 →
    # 若 total_news 用去重前的 len(kept)，兩種模式就會給出不同數字。
    ("2317", "2026-07-21"): [("2026-07-21 10:30:00", "中央社", "鴻海股東會")],
    ("2317", "2026-07-22"): [("2026-07-21 10:30:00", "中央社", "鴻海股東會")],
    ("2317", "2026-07-24"): [("2026-07-24 09:30:00", "自由財經", "鴻海新廠")],
    # 台北 07-23 清晨（06:30）的新聞，UTC 是 07-22 22:30 → 落在切片 07-22。
    # 這一則是用來釘住 cache_cutoff 的：cutoff 若提前一天（refresh_slices[0] 而非 [1]），
    # 它既不在快取段（快取止於 cutoff）也不在重抓的切片裡 → 會被漏掉。
    # 沒有這則語料的話，把 cutoff 改錯測試照樣全綠。
    ("2454", "2026-07-22"): [("2026-07-22 16:00:00", "今周刊", "聯發科新晶片"),
                             ("2026-07-23 06:30:00", "經濟日報", "台北07-23清晨新聞在切片07-22")],
    (NEW_CODE, "2026-07-21"): [("2026-07-21 08:30:00", "經濟日報", "新進池個股舊新聞")],
    (NEW_CODE, "2026-07-24"): [("2026-07-24 10:00:00", "工商時報", "新進池個股今日新聞")],
}
# 第 4 元素＝明確 link（省略時 stub 用 https://x/<title>）。以下三則同一篇文章（連結只差追蹤參數／
# 大小寫／尾斜線 → 同 aid）：2330 出現兩次（不同標題與日期）→ 同股內須合併成一則（date 取最早、
# 標題取最長）；2454 也有 → 跨股保留、雙方互附 related。
#   2330 的兩份分別落在切片 07-22（<cache_cutoff，增量走快取）與 07-23（增量重抓）——正是
#   「快取段已合併 + 新抓段再合併」要與全量一致的那條路。
DUP_LINKS = ("https://udn.com/news/story/1/999?from=udn-ch1_breaknews",
             "https://UDN.com/news/story/1/999/",
             "https://udn.com/news/story/1/999")
DUP_TITLE_SHORT, DUP_TITLE_LONG = "同篇短題", "同篇短題 加長版標題"
CORPUS[("2330", "2026-07-22")].append(("2026-07-22 09:10:00", "經濟日報", DUP_TITLE_SHORT, DUP_LINKS[0]))
CORPUS[("2330", "2026-07-23")].append(("2026-07-23 10:00:00", "經濟日報", DUP_TITLE_LONG, DUP_LINKS[1]))
CORPUS[("2454", "2026-07-23")] = [("2026-07-23 10:00:00", "經濟日報", DUP_TITLE_LONG, DUP_LINKS[2])]

req_log: list[tuple[str, str]] = []
FAIL_PAIRS: set[tuple[str, str]] = set()   # 要模擬抓取失敗的 (代號, 切片)


def taipei_to_utc_slice(tpe: str) -> str:
    """把假語料的台北時間換回 FinMind 儲存的 UTC 字串（build_news 會再轉回台北）。"""
    dt = bn.datetime.strptime(tpe, "%Y-%m-%d %H:%M:%S") - timedelta(hours=8)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def install_stubs(pool_codes: list[str]) -> None:
    bn.FINMIND_TOKEN = "stub"
    bn.POOL_CACHE_DIR = None      # 股票池同日快取停用：本測試每個情境都換池（stub），快取命中會蓋掉 stub
    bn.recent_trading_days = lambda n: TDAYS[-n:]
    bn.fetch_market_value_weights = lambda: {c: 1.0 for c in pool_codes}
    bn.build_pool_from_finmind = lambda mx: pd.DataFrame(
        {"code": pool_codes, "name": [f"股{c}" for c in pool_codes],
         "industry": ["半導體"] * len(pool_codes)})
    # 讓「今天」固定在 07-24，news_calendar_days 才是決定性的
    bn.taipei_today = lambda: bn.datetime.strptime("2026-07-24", "%Y-%m-%d").date()

    def fake_fetch(stock_id, date, throttle):
        req_log.append((stock_id, date))
        if (stock_id, date) in FAIL_PAIRS:      # 模擬 HTTP 非 200 / 連線例外
            return [], False
        return ([{"date": taipei_to_utc_slice(rec[0]), "stock_id": stock_id, "source": rec[1],
                  "title": rec[2], "link": rec[3] if len(rec) > 3 else f"https://x/{rec[2]}"}
                 for rec in CORPUS.get((stock_id, date), [])], True)
    bn.fetch_news_one = fake_fetch


def run(argv: list[str]) -> tuple[dict, int]:
    req_log.clear()
    bn.main.__wrapped__(argv) if hasattr(bn.main, "__wrapped__") else _call_main(argv)
    with open(bn.OUTPUT_JSON, encoding="utf-8") as f:
        return json.load(f), len(req_log)


def _call_main(argv: list[str]) -> None:
    old = sys.argv
    sys.argv = ["build_news.py"] + argv
    try:
        bn.main()
    finally:
        sys.argv = old


def comparable(payload: dict) -> dict:
    """去掉每次執行都會變的欄位，只留內容本身。"""
    out = {k: v for k, v in payload.items() if k != "generated_at"}
    for s in out["stocks"]:
        s["news"] = sorted(s["news"], key=lambda n: (n["date"], n["title"]))
    out["stocks"] = sorted(out["stocks"], key=lambda s: s["stock_id"])
    return out


def news_index(payload: dict) -> set[tuple[str, str, str]]:
    return {(s["stock_id"], n["date"], n["title"])
            for s in payload["stocks"] for n in s["news"]}


def main() -> None:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ✓ " if cond else "  ✗ ") + msg)
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        bn.OUTPUT_JSON = os.path.join(td, "news.json")

        print("[1] 全量抓取（基準）")
        install_stubs(POOL_BASE)
        full, n_full = run(["--lookback", "5", "--full"])
        print(f"      請求 {n_full} 次、{full['total_news']} 則新聞")
        check(full["total_news"] > 0, "全量有抓到新聞")
        check(("2330", "2026-07-24 03:20:00", "台北清晨新聞落在前一切片") in news_index(full),
              "全量收到「台北日落在前一 UTC 切片」的新聞")
        check(all(t != "窗外新聞不該入選" for _, _, t in news_index(full)),
              "全量正確排除視窗外新聞")

        check(full["total_news"] == len(news_index(full)),
              f"total_news 等於實際輸出則數（{full['total_news']}）")

        print("[2] 增量抓取（讀上一輪 news.json 當快取）")
        incr, n_incr = run(["--lookback", "5"])
        print(f"      請求 {n_incr} 次、{incr['total_news']} 則新聞")
        check(comparable(incr) == comparable(full), "增量輸出與全量輸出完全一致")
        check(n_incr < n_full, f"請求數下降（{n_full} → {n_incr}）")
        # total_news 曾經是 len(kept)（去重前），而去重掉多少取決於抓了幾個切片
        # → 增量與全量會給出差三成的數字，前端「N 則」看起來像掉了一大截。
        check(incr["total_news"] == full["total_news"],
              f"total_news 不受抓取模式影響（全量 {full['total_news']} vs 增量 {incr['total_news']}）")

        print("[3] 增量 + 池新增個股（新進池必須全窗補抓）")
        install_stubs(POOL_BASE + [NEW_CODE])
        incr2, n_incr2 = run(["--lookback", "5"])
        idx2 = news_index(incr2)
        print(f"      請求 {n_incr2} 次、{incr2['total_news']} 則新聞")
        check((NEW_CODE, "2026-07-21 08:30:00", "新進池個股舊新聞") in idx2,
              "新進池個股的「舊日期」新聞有補抓到")
        check((NEW_CODE, "2026-07-24 10:00:00", "新進池個股今日新聞") in idx2,
              "新進池個股的今日新聞也在")
        check(news_index(full) <= idx2, "原有個股的新聞未因增量而遺失")

        print("[4] 全量重跑一次，確認與 [3] 的增量結果一致")
        full2, _ = run(["--lookback", "5", "--full"])
        check(comparable(full2) == comparable(incr2), "增量(含新進池) == 全量")

        print("[5] 重複值檢查")
        dup = [s["stock_id"] for s in incr2["stocks"]
               if len({(n["date"], n["title"]) for n in s["news"]}) != len(s["news"])]
        check(not dup, f"無重複新聞（快取段與新抓段沒接壞）{dup}")

        # 抓取失敗（HTTP 非 200／連線例外）回空清單，與「這天真的沒新聞」形狀相同。
        # 若失敗仍被寫進 coverage，下一班就會從快取拿這個「沒有」，那天的新聞永久
        # 遺失（只有 --full 沖得掉）。全量重抓時代不會有這問題——失敗只影響當班。
        print("[6] 抓取失敗不得毒化 coverage（增量最大的資料遺失風險）")
        install_stubs(POOL_BASE)
        run(["--lookback", "5", "--full"])                       # 先建立乾淨快取
        FAIL_PAIRS.add(("2330", "2026-07-24"))                   # 這班某檔某切片失敗
        broken, _ = run(["--lookback", "5"])
        check("2330" not in broken["coverage"]["codes"],
              "失敗的檔已被排出 coverage")
        FAIL_PAIRS.clear()
        healed, _ = run(["--lookback", "5"])                     # 下一班（仍是增量）
        idx_h = news_index(healed)
        check(("2330", "2026-07-24 13:00:00", "尾盤拉抬") in idx_h,
              "下一班增量即自動補回失敗那天的新聞（不必等 --full）")
        check("2330" in healed["coverage"]["codes"], "補回後重新計入 coverage")

        # 同一篇文章（同 aid）在同一檔股票內只留一則，但跨股票是合法關聯、不得刪；
        # 跨股折疊交給前端（related 欄）。增量與全量都要得到同樣的合併結果。
        print("[7] 同股重複去、跨股保留（aid 去重＋related）")
        install_stubs(POOL_BASE)
        full3, _ = run(["--lookback", "5", "--full"])
        incr3, _ = run(["--lookback", "5"])
        check(comparable(incr3) == comparable(full3), "aid 合併後增量輸出仍與全量一致")
        for label, out in (("全量", full3), ("增量", incr3)):
            by = {s["stock_id"]: s for s in out["stocks"]}
            aid = bn.article_id(DUP_LINKS[0])
            check(all(bn.article_id(u) == aid for u in DUP_LINKS), f"[{label}] 三種寫法的連結同一 aid")
            hits_2330 = [n for n in by["2330"]["news"] if n["aid"] == aid]
            hits_2454 = [n for n in by["2454"]["news"] if n["aid"] == aid]
            check(len(hits_2330) == 1, f"[{label}] 2330 同 aid 只留一則（{len(hits_2330)}）")
            check(hits_2330 and hits_2330[0]["date"] == "2026-07-22 09:10:00"
                  and hits_2330[0]["title"] == DUP_TITLE_LONG,
                  f"[{label}] 合併後 date 取最早、標題取最長")
            check(len(hits_2454) == 1, f"[{label}] 2454 的同篇文章未被跨股刪除")
            check(hits_2330 and hits_2330[0].get("related") == ["2454"]
                  and hits_2454 and hits_2454[0].get("related") == ["2330"],
                  f"[{label}] 雙方互附 related")
            check(all("related" not in n for s in out["stocks"] for n in s["news"] if n["aid"] != aid),
                  f"[{label}] 非跨股的則不帶 related 鍵")
            check(all(n.get("aid") for s in out["stocks"] for n in s["news"]), f"[{label}] 每則都有 aid")
            st = out.get("stats", {})
            check(st.get("n_raw") == out["total_news"] and st.get("n_unique_articles") == out["total_news"] - 1,
                  f"[{label}] stats n_raw={st.get('n_raw')}／n_unique_articles={st.get('n_unique_articles')}"
                  f"（唯一跨股連結使兩者差 1）")
            check(out["coverage"]["codes"] == POOL_BASE and out["coverage"]["dates"],
                  f"[{label}] coverage 不變式未被動到")

    print()
    if failures:
        print(f"✗ {len(failures)} 項失敗")
        sys.exit(1)
    print("✓ 增量抓取與全量一致")


if __name__ == "__main__":
    main()
