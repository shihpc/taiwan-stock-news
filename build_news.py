#!/usr/bin/env python3
# build_news.py
# ============================================================
#  台股策展新聞 — 每日建置 news.json
#
#  流程：
#   1. 讀 taiwan-stock-radar 公開的 scan_app.csv 取股票池（唯讀，不回寫）
#      池 = 投信連買(trust_days>=2) ∪ 外資連買(foreign_days>=2)，排除 ETF
#   2. 逐檔抓近 N 個交易日的 FinMind TaiwanStockNews（單日單請求）
#   3. 套 news_curation.curate_news 白名單過濾
#   4. 輸出 news.json 給前端 dashboard 讀取
#
#  用法：
#    FINMIND_TOKEN=xxx python build_news.py [--lookback 3] [--max-pool 150]
#        [--hourly-budget 550] [--pool-csv <path or url>]
# ============================================================

import argparse
import io
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd

from news_curation import curate_news, normalize_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_news")

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# taiwan-stock-radar 每日產出的候選清單（公開、唯讀）
DEFAULT_POOL_CSV = "https://raw.githubusercontent.com/shihpc/taiwan-stock-radar/main/scan_app.csv"

OUTPUT_JSON = "news.json"

# ── 分層：權值股（市值大、對大盤影響高，約當 0050 成分）──────────
HEAVYWEIGHTS: frozenset[str] = frozenset({
    "2330", "2317", "2454", "2308", "2382", "2881", "2303", "2882", "2891",
    "3711", "2886", "2412", "2884", "1216", "2885", "3034", "2892", "2357",
    "2890", "5880", "2345", "3231", "2327", "2379", "4938", "2883", "2887",
    "3008", "2002", "1303", "1301", "2880", "2603", "3661", "3017", "2395",
    "3045", "2912", "5876", "1101", "6669", "3037", "2301", "4904", "6505",
    "5871", "2408", "2609", "2615", "6446",
})

# ── 對大盤影響度分類（per 新聞）─────────────────────────────────
#  market：標題點到盤面/類股/大盤層級 → 最該優先看
#  heavy ：權值股個股新聞（會牽動指數）
#  stock ：一般個股新聞
_MARKET_KEYWORDS = (
    "台股", "大盤", "加權", "指數", "類股", "族群", "盤面", "盤後",
    "盤中", "三大法人", "外資買超", "外資賣超", "資金", "權值",
)


def classify_impact(stock_id: str, title: str) -> str:
    t = title or ""
    if any(k in t for k in _MARKET_KEYWORDS):
        return "market"
    if stock_id in HEAVYWEIGHTS:
        return "heavy"
    return "stock"


# ── 節流：一小時內請求數不超過 budget ────────────────────────
class Throttle:
    def __init__(self, budget_per_hour: int):
        self.budget = budget_per_hour
        self.stamps: list[float] = []

    def wait(self) -> None:
        now = time.time()
        self.stamps = [t for t in self.stamps if now - t < 3600]
        if len(self.stamps) >= self.budget:
            sleep_s = 3600 - (now - self.stamps[0]) + 1
            logger.info(f"達每小時 {self.budget} 上限，休息 {sleep_s:.0f}s...")
            time.sleep(sleep_s)
        self.stamps.append(time.time())


def load_pool(pool_csv: str, max_pool: int) -> pd.DataFrame:
    """讀 scan_app.csv → 回傳 (code, name, industry) 池。"""
    if pool_csv.startswith("http"):
        logger.info(f"讀取股票池：{pool_csv}")
        r = requests.get(pool_csv, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), dtype={"code": str})
    else:
        df = pd.read_csv(pool_csv, dtype={"code": str})
    df["code"] = df["code"].str.strip()
    for col in ("trust_days", "foreign_days"):
        if col not in df.columns:
            df[col] = 0
    # industry 的 ETF 有多種寫法（"ETF" / "上市/上櫃指數股票型基金(ETF)"），一律排除
    not_etf = ~df["industry"].astype(str).str.contains("ETF", na=False)
    mask = ((df["trust_days"] >= 2) | (df["foreign_days"] >= 2)) & not_etf
    pool = df[mask].copy()
    # 熱度排序：投信+外資連買天數合計高者優先
    pool["heat"] = pool["trust_days"].fillna(0) + pool["foreign_days"].fillna(0)
    pool = pool.sort_values("heat", ascending=False).head(max_pool)
    logger.info(f"股票池：{len(pool)} 檔（投信連買 ∪ 外資連買，非 ETF，取熱度前 {max_pool}）")
    return pool[["code", "name", "industry"]].reset_index(drop=True)


def fetch_market_value_weights() -> dict[str, float]:
    """抓全市場市值權重（TaiwanStockMarketValueWeight，不帶 data_id 一次拿全市場）。
    此 dataset 必須帶 start_date（否則 API 回 400），近期任一日即可取得最新一期權重。
    失敗時回傳空 dict，呼叫端 fallback 為排序時 weight_per 皆視為 0。"""
    start = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        r = requests.get(FINMIND_URL, params={
            "dataset": "TaiwanStockMarketValueWeight",
            "start_date": start,
            "token": FINMIND_TOKEN,
        }, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
        # 視窗內可能橫跨多個發布日，同檔股票只取日期最新的一筆
        latest_date: dict[str, str] = {}
        weights: dict[str, float] = {}
        for rec in data:
            sid = str(rec.get("stock_id", "")).strip()
            d = str(rec.get("date", ""))
            if not sid:
                continue
            if sid in latest_date and d < latest_date[sid]:
                continue
            try:
                w = float(rec.get("weight_per", 0) or 0)
            except (TypeError, ValueError):
                w = 0.0
            latest_date[sid] = d
            weights[sid] = w
        logger.info(f"市值權重：取得 {len(weights)} 檔（基準日 {max(latest_date.values(), default='?')}）")
        return weights
    except Exception as e:
        logger.warning(f"市值權重 API 失敗，排序將退化為依新聞數：{e}")
        return {}


def recent_trading_days(n: int) -> list[str]:
    """近 n 個交易日（以 FinMind TaiwanStockTradingDate 為準，退回平日近似）。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=n * 2 + 20)
    try:
        r = requests.get(FINMIND_URL, params={
            "dataset": "TaiwanStockTradingDate",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "token": FINMIND_TOKEN,
        }, timeout=30)
        data = r.json().get("data", [])
        dates = sorted({str(d["date"])[:10] for d in data})
        if dates:
            return dates[-n:]
    except Exception as e:
        logger.warning(f"交易日 API 失敗，改用平日近似：{e}")
    out: list[str] = []
    d = datetime.now(timezone.utc).date()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return sorted(out)


def fetch_news_one(stock_id: str, date: str, throttle: Throttle) -> list[dict]:
    """抓單檔單日新聞（TaiwanStockNews 單日單請求）。"""
    throttle.wait()
    try:
        r = requests.get(FINMIND_URL, params={
            "dataset": "TaiwanStockNews",
            "data_id": stock_id,
            "start_date": date,
            "end_date": date,
            "token": FINMIND_TOKEN,
        }, timeout=30)
        if r.status_code != 200:
            return []
        data = r.json().get("data", [])
        time.sleep(0.05)
        return data
    except Exception as e:
        logger.warning(f"[{stock_id} {date}] 抓取失敗：{e}")
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=3, help="近 N 個交易日（預設 3）")
    ap.add_argument("--max-pool", type=int, default=150, help="股票池上限（預設 150）")
    ap.add_argument("--hourly-budget", type=int, default=550)
    ap.add_argument("--pool-csv", default=DEFAULT_POOL_CSV)
    args = ap.parse_args()

    if not FINMIND_TOKEN:
        logger.error("未設定 FINMIND_TOKEN 環境變數")
        raise SystemExit(1)

    pool = load_pool(args.pool_csv, args.max_pool)
    weight_map = fetch_market_value_weights()
    dates = recent_trading_days(args.lookback)
    logger.info(f"時間範圍：{dates[0]} ~ {dates[-1]}（{len(dates)} 個交易日）")
    throttle = Throttle(args.hourly_budget)

    name_map = dict(zip(pool["code"], pool["name"]))
    ind_map = dict(zip(pool["code"], pool["industry"]))

    raw: list[dict] = []
    for i, code in enumerate(pool["code"], 1):
        cnt = 0
        for d in dates:
            for rec in fetch_news_one(code, d, throttle):
                raw.append({
                    "date": str(rec.get("date", "")),
                    "stock_id": str(rec.get("stock_id", code)),
                    "source": str(rec.get("source", "")).strip(),
                    "title": str(rec.get("title", "")).strip(),
                    "link": str(rec.get("link", "")),
                })
                cnt += 1
        if i % 20 == 0 or i == len(pool):
            logger.info(f"進度 {i}/{len(pool)}（最新 {code} {name_map.get(code,'')}: {cnt} 則）")

    logger.info(f"抓取原始 {len(raw)} 則，套用白名單過濾...")
    kept = curate_news(raw)
    logger.info(f"過濾後保留 {len(kept)} 則")

    # 依股票分組
    by_stock: dict[str, list[dict]] = {}
    seen_per_stock: dict[str, set[tuple[str, str]]] = {}
    for rec in kept:
        sid = rec["stock_id"]
        key = (rec["title"], rec["link"])
        seen = seen_per_stock.setdefault(sid, set())
        if key in seen:
            continue  # 同一檔股票裡，跨查詢日期抓到重複文章（FinMind 單日查詢邊界會重疊）
        seen.add(key)
        by_stock.setdefault(sid, []).append(rec)

    stocks = []
    for code, items in by_stock.items():
        items.sort(key=lambda r: r["date"], reverse=True)
        news = [{
            "date": r["date"],
            "source": normalize_source(r["source"]),
            "title": r["title"],
            "link": r["link"],
            "impact": classify_impact(code, r["title"]),
        } for r in items]
        stocks.append({
            "stock_id": code,
            "name": name_map.get(code, ""),
            "industry": ind_map.get(code, "") or "其他",
            "heavyweight": code in HEAVYWEIGHTS,
            "weight_per": weight_map.get(code, 0.0),
            "count": len(news),
            "market_count": sum(1 for n in news if n["impact"] == "market"),
            "news": news,
        })
    # 市值權重高者優先（抓不到權重視為 0，排最後）；同權重時新聞多者優先，再股號
    stocks.sort(key=lambda s: (-(s["weight_per"] or 0.0), -s["count"], s["stock_id"]))

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lookback_days": args.lookback,
        "trading_days": dates,
        "pool_size": int(len(pool)),
        "stocks_with_news": len(stocks),
        "total_news": len(kept),
        "stocks": stocks,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    logger.info(f"已輸出 {OUTPUT_JSON}：{len(stocks)} 檔有新聞、共 {len(kept)} 則")


if __name__ == "__main__":
    main()
