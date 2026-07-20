#!/usr/bin/env python3
# build_news.py
# ============================================================
#  台股策展新聞 — 每日建置 news.json
#
#  流程：
#   1. 自建股票池（build_pool_from_finmind：FinMind TaiwanStockInfo + 近3日投信/外資買賣超；
#      taiwan-stock-radar repo 已刪除，不再依賴外部 repo 的 scan_app.csv）
#      池 = 投信連買(trust_days>=2) ∪ 外資連買(foreign_days>=2)，排除 ETF
#   2. 逐檔抓 FinMind TaiwanStockNews（單日單請求）；日期範圍＝涵蓋近 N 個
#      交易日的「日曆日」區間（第 N 個交易日前一天起到今天，含夾雜與尾隨的
#      週末/假日），週末排程執行時也能收到週六日發布的新聞。
#      FinMind 的 date 是 UTC，寫入前轉台北時間（finmind_news_date_to_taipei）
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

from news_curation import curate_news, normalize_source, strip_title_tail, _loose

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_news")

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# 台北時區（台灣無夏令時間，固定 UTC+8）
TAIPEI_TZ = timezone(timedelta(hours=8))


def finmind_news_date_to_taipei(s: str) -> str:
    """FinMind TaiwanStockNews 的 date 欄位是「UTC 時間戳」的 naive 字串
    （2026-07-13 抽樣驗證：鉅亨網/FTNN/工商時報/MoneyDJ/UDN 等主要來源
    +8 小時後與文章頁的台北發布時間分秒吻合；自由時報頁內 publishAt 更直接
    帶 Z 後綴證實為 UTC）。此函式把它轉成台北時間，輸出格式維持
    'YYYY-MM-DD HH:MM:SS' 不變（前端 fmtDate 切字串顯示，無需調整）。
    解析失敗時原樣回傳（防禦：格式異常寧可顯示原值也不要丟資料）。"""
    try:
        dt = datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(s)
    return dt.replace(tzinfo=timezone.utc).astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def taipei_today():
    """台北時間的今天。所有「今天/回看 N 日」判定一律走這裡，避免用 UTC 日期
    在台北清晨 8 點前（UTC 仍是前一天）把今天誤判成昨天，導致清晨的班天生慢一天。"""
    return datetime.now(TAIPEI_TZ).date()

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


def build_pool_from_finmind(max_pool: int) -> pd.DataFrame:
    """自建股票池（taiwan-stock-radar repo 已刪除，不再依賴外部 repo 的 scan_app.csv）。
    邏輯與原 scan_app.csv 一致：TaiwanStockInfo 取名稱/產業分類，近3個交易日
    TaiwanStockInstitutionalInvestorsBuySell 算投信/外資連買天數，篩選
    (trust_days>=2 or foreign_days>=2) 且非ETF，依熱度(兩者天數合計)排序取前N。"""
    r = requests.get(FINMIND_URL, params={"dataset": "TaiwanStockInfo", "token": FINMIND_TOKEN}, timeout=30)
    r.raise_for_status()
    name_map, ind_map, type_map = {}, {}, {}
    for row in r.json().get("data", []):
        sid = str(row.get("stock_id", ""))
        if not sid or sid in name_map:  # 同代號可能重複列（雙產業分類等），取第一筆
            continue
        name_map[sid] = row.get("stock_name", "")
        ind_map[sid] = row.get("industry_category", "")
        type_map[sid] = row.get("type", "")
    keep = {sid for sid, t in type_map.items()
            if t in ("twse", "tpex") and sid[:1].isdigit() and "ETF" not in (ind_map.get(sid) or "")}

    trust_days: dict[str, int] = {}
    foreign_days: dict[str, int] = {}
    for ds in recent_trading_days(3):
        r = requests.get(FINMIND_URL, params={
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
            "start_date": ds, "end_date": ds, "token": FINMIND_TOKEN,
        }, timeout=30)
        for rec in r.json().get("data", []):
            sid = str(rec.get("stock_id", ""))
            if sid not in keep:
                continue
            name = rec.get("name")
            if name not in ("Investment_Trust", "Foreign_Investor"):
                continue
            net = (rec.get("buy") or 0) - (rec.get("sell") or 0)
            if net > 0:
                d = trust_days if name == "Investment_Trust" else foreign_days
                d[sid] = d.get(sid, 0) + 1

    rows = [{"code": sid, "name": name_map.get(sid, ""), "industry": ind_map.get(sid, ""),
             "heat": trust_days.get(sid, 0) + foreign_days.get(sid, 0)}
            for sid in keep if trust_days.get(sid, 0) >= 2 or foreign_days.get(sid, 0) >= 2]
    df = pd.DataFrame(rows)
    if df.empty:
        logger.warning("股票池（FinMind自建）：0 檔，近3日投信/外資買賣超資料可能尚未settle")
        return df.assign(code=[], name=[], industry=[]) if rows else pd.DataFrame(columns=["code", "name", "industry"])
    df = df.sort_values("heat", ascending=False).head(max_pool)
    logger.info(f"股票池（FinMind自建）：{len(df)} 檔（投信連買∪外資連買，非ETF，取熱度前{max_pool}）")
    return df[["code", "name", "industry"]].reset_index(drop=True)


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
    end = datetime.now(TAIPEI_TZ)
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
    d = taipei_today()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return sorted(out)


def news_calendar_days(trading_days: list[str]) -> list[str]:
    """涵蓋近 N 個交易日的「日曆日」區間：從 trading_days[0] 的「前一天」（含）
    到今天（含）的每一個日曆日（交易日＋夾雜與尾隨的週末/假日）。
    例：週日執行、lookback=3（週三四五）→ 回傳 週二~週日 共 6 天，
    讓週末發布的新聞也抓得到。「今天」以台北時間為準（見 taipei_today()）——
    若用 UTC 判定，台北清晨 8 點前 UTC 仍是前一天，會把今天誤判成昨天，
    導致清晨的班天生慢一天。

    為何往前多含一天：FinMind 的單日切片以其儲存的 UTC 日為準，而新聞 date
    轉台北(+8)後，台北 D 日清晨 00:00~07:59 的新聞落在 FinMind 的 D-1(UTC)
    切片裡；不多抓一天會漏掉首個交易日清晨的新聞。多抓那天裡台北時間仍在
    trading_days[0] 之前的新聞，會在 main() 轉換後依台北日過濾掉。
    尾端不用多抓：執行時刻之前發布的新聞，其台北日必 <= 今天(台北)。"""
    start = datetime.strptime(trading_days[0], "%Y-%m-%d").date() - timedelta(days=1)
    end = taipei_today()
    out: list[str] = []
    d = start
    while d <= end:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


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
    ap.add_argument("--pool-csv", default=None,
                     help="指定舊格式CSV(path或url)覆蓋預設；不指定則用FinMind自建股票池")
    args = ap.parse_args()

    if not FINMIND_TOKEN:
        logger.error("未設定 FINMIND_TOKEN 環境變數")
        raise SystemExit(1)

    pool = load_pool(args.pool_csv, args.max_pool) if args.pool_csv else build_pool_from_finmind(args.max_pool)
    weight_map = fetch_market_value_weights()
    tdays = recent_trading_days(args.lookback)          # 交易日（trading_days 欄位、前端 TDAYS 依賴）
    dates = news_calendar_days(tdays)                   # 抓新聞用日曆日（含週末/假日）
    logger.info(f"時間範圍：{dates[0]} ~ {dates[-1]}（{len(tdays)} 個交易日、共 {len(dates)} 個日曆日）")
    throttle = Throttle(args.hourly_budget)

    name_map = dict(zip(pool["code"], pool["name"]))
    ind_map = dict(zip(pool["code"], pool["industry"]))

    raw: list[dict] = []
    for i, code in enumerate(pool["code"], 1):
        cnt = 0
        for d in dates:
            for rec in fetch_news_one(code, d, throttle):
                # FinMind date 是 UTC → 轉台北（見 finmind_news_date_to_taipei 註解）
                date_tpe = finmind_news_date_to_taipei(rec.get("date", ""))
                # 視窗以「台北日」為準：多抓的前一個 UTC 日切片裡，台北時間
                # 仍早於 trading_days[0] 的新聞不在視窗內，這裡丟掉
                if date_tpe[:10] < tdays[0]:
                    continue
                raw.append({
                    "date": date_tpe,
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

    # 依股票分組＋去重。key 用「去尾巴(- 媒體名)+寬鬆正規化」的標題（沿用
    # news_curation 既有的 strip_title_tail/_loose）：同一篇文章常見同來源
    # 集團旗下不同站台轉載（自由財經 vs 自由時報、UDN vs udn 大小寫），
    # 標題尾巴的媒體名不同、連結也不同，純標題或標題+連結比對都攔不住。
    # 先依日期新到舊排序再去重 → 同篇保留最新一筆。
    kept.sort(key=lambda r: r["date"], reverse=True)
    by_stock: dict[str, list[dict]] = {}
    seen_per_stock: dict[str, set[str]] = {}
    for rec in kept:
        sid = rec["stock_id"]
        seen = seen_per_stock.setdefault(sid, set())
        key = _loose(strip_title_tail(rec["title"]))
        if key and key in seen:
            continue
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
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
        "lookback_days": args.lookback,
        "trading_days": tdays,
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
