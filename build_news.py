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
#        [--hourly-budget 550] [--pool-csv <path or url>] [--full] [--no-cache]
#
#  韌性（2026-09-06 批次二 #4）：
#   - 所有 FinMind 請求共用模組級 requests.Session（連線重用）
#   - 交易日查詢 memoize（_fetch_trading_dates），一班只打一次
#   - fetch_news_one 非 200／例外退避 RETRY_SLEEP 秒重試一次，失敗路徑也 sleep
#   - 股票池／市值權重／交易日以「台北日」為粒度落盤快取 data/cache/pool_<YYYYMMDD>.json，
#     同日第二班起直接讀（--full 重建並覆寫、--no-cache 不讀不寫）；舊日檔自動清
# ============================================================

import argparse
import glob
import io
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests
import pandas as pd

from news_curation import article_id, curate_news, normalize_source, strip_title_tail, _loose

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_news")

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# ── HTTP：模組級 Session（連線重用）＋重試參數（測試可改 0）──────────────
_SESSION: requests.Session | None = None
RETRY_SLEEP = 2.0     # fetch_news_one 失敗後退避秒數（只重試一次）
FETCH_PAUSE = 0.05    # 每個請求之後的小停頓（成功／失敗路徑皆 sleep）


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


# ── 股票池／市值權重／交易日：台北日粒度落盤快取 ─────────────────────────
# 每小時一班（一天 16 班）但這三樣一天內幾乎不變；快取後同日第二班起省掉
# TaiwanStockInfo（全市場）＋3 個交易日法人＋市值權重＋交易日共 6 個請求。
# 取捨（刻意）：FinMind 當日法人資料約 17:00 後才入庫，快取讓池在 21:37 的 --full
# 備援班（會重建並覆寫快取）之前維持早班版本；新進池的檔會在該班全窗補抓，不會漏。
# None＝停用（tests/test_incremental.py 用），CI 走 actions/cache 跨 run 帶（build-news.yml）。
POOL_CACHE_DIR: str | None = "data/cache"
POOL_CACHE_VERSION = 1

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
        r = _session().get(pool_csv, timeout=30)
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
    r = _session().get(FINMIND_URL, params={"dataset": "TaiwanStockInfo", "token": FINMIND_TOKEN}, timeout=30)
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
        r = _session().get(FINMIND_URL, params={
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
        r = _session().get(FINMIND_URL, params={
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


TRADING_DATE_WINDOW_DAYS = 45   # 交易日查詢的固定回看窗（n ≤ 12 都夠），固定才能讓 memoize 命中


@lru_cache(maxsize=None)
def _fetch_trading_dates(start: str, end: str) -> tuple[str, ...]:
    """TaiwanStockTradingDate [start, end] 的交易日（升序）。lru_cache：同一班內
    build_pool_from_finmind（n=3）與 main（n=lookback）都會查交易日，原本各打一次 API，
    現在共用固定視窗、只打一次。例外不會被快取（下次呼叫會再試）。"""
    r = _session().get(FINMIND_URL, params={
        "dataset": "TaiwanStockTradingDate",
        "start_date": start,
        "end_date": end,
        "token": FINMIND_TOKEN,
    }, timeout=30)
    data = r.json().get("data", [])
    return tuple(sorted({str(d["date"])[:10] for d in data}))


def recent_trading_days(n: int) -> list[str]:
    """近 n 個交易日（以 FinMind TaiwanStockTradingDate 為準，退回平日近似）。"""
    end = datetime.now(TAIPEI_TZ)
    start = end - timedelta(days=max(TRADING_DATE_WINDOW_DAYS, n * 2 + 20))
    try:
        dates = _fetch_trading_dates(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if dates:
            return list(dates[-n:])
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


def load_cache(path: str) -> dict:
    """讀既有 news.json 當快取。回 {} 表示不可用（首次執行 / 格式舊 / 損壞）。

    快取裡的 news 已是「過濾後」的結果（source 已 normalize、另帶 impact），
    重用時不再送進 curate_news——normalize_source 對 canonical 值雖然是 idempotent，
    但沒必要重跑；直接與新抓那批 curate 完的結果合併即可。
    """
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.info(f"無可用快取（{type(e).__name__}）→ 全量抓取")
        return {}
    if not isinstance(prev.get("coverage"), dict):
        # 舊版 news.json 沒有 coverage，無法判斷「某 (檔,日) 是真的沒新聞還是沒抓過」
        logger.info("既有 news.json 無 coverage 區塊（舊版）→ 本次全量抓取並補上")
        return {}
    return prev


def cached_records(prev: dict, codes: set[str], lo: str, hi_exclusive: str) -> list[dict]:
    """從快取取出 code ∈ codes、台北日在 [lo, hi_exclusive) 的新聞（附回 stock_id）。"""
    out: list[dict] = []
    for s in prev.get("stocks", []):
        sid = str(s.get("stock_id", ""))
        if sid not in codes:
            continue
        for n in s.get("news", []):
            day = str(n.get("date", ""))[:10]
            if lo <= day < hi_exclusive:
                out.append({"date": n["date"], "stock_id": sid,
                            "source": n.get("source", ""), "title": n.get("title", ""),
                            "link": n.get("link", "")})
    return out


def fetch_news_one(stock_id: str, date: str, throttle: Throttle) -> tuple[list[dict], bool]:
    """抓單檔單日新聞（TaiwanStockNews 單日單請求）。

    回傳 `(records, ok)`。**必須把「抓取失敗」與「這天真的沒新聞」分開**：
    兩者都回空清單的話，失敗會被 coverage 記成「已涵蓋且沒新聞」，之後每一班
    增量都從快取拿這個「沒有」，那天的新聞就永久遺失（只有 --full 沖得掉）。
    改成全量重抓之前，失敗只影響當班、下一班會重抓，是自癒的；增量把它變成
    沾黏的，所以這裡一定要回報失敗。
    """
    params = {
        "dataset": "TaiwanStockNews",
        "data_id": stock_id,
        "start_date": date,
        "end_date": date,
        "token": FINMIND_TOKEN,
    }
    last = ""
    # 非 200／連線例外／JSON 解析失敗 → 退避 RETRY_SLEEP 秒重試一次（重試也計入 Throttle）；
    # 兩次都失敗才回報失敗。失敗路徑同樣 sleep FETCH_PAUSE，避免連續失敗時緊接著猛打。
    for attempt in range(2):
        throttle.wait()
        try:
            r = _session().get(FINMIND_URL, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json().get("data", [])
                time.sleep(FETCH_PAUSE)
                return data, True
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt == 0:
            logger.warning(f"[{stock_id} {date}] {last}，{RETRY_SLEEP:g}s 後重試一次")
            time.sleep(RETRY_SLEEP)
    logger.warning(f"[{stock_id} {date}] 重試後仍失敗（{last}），視為抓取失敗")
    time.sleep(FETCH_PAUSE)
    return [], False


def pool_cache_path(day8: str) -> str:
    return os.path.join(POOL_CACHE_DIR or "", f"pool_{day8}.json")


def load_pool_cache(day8: str, max_pool: int, lookback: int) -> dict | None:
    """讀當日股票池快取。回 None 表示不可用（無檔／版本或參數不合／損壞）。
    回 {"pool": DataFrame, "weights": dict, "tdays": list}。"""
    if not POOL_CACHE_DIR:
        return None
    path = pool_cache_path(day8)
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if (c.get("version") != POOL_CACHE_VERSION or c.get("day") != day8
            or c.get("max_pool") != max_pool or not c.get("pool")):
        logger.info(f"股票池快取 {path} 版本／參數不合，忽略")
        return None
    tdays = c.get("tdays") or []
    if c.get("lookback") != lookback or len(tdays) != lookback:
        tdays = recent_trading_days(lookback)      # 只有交易日要補查（1 個請求）
    pool = pd.DataFrame(c["pool"], columns=["code", "name", "industry"])
    pool["code"] = pool["code"].astype(str)
    return {"pool": pool, "weights": {str(k): float(v) for k, v in (c.get("weights") or {}).items()},
            "tdays": list(tdays)}


def save_pool_cache(day8: str, max_pool: int, lookback: int,
                    pool: pd.DataFrame, weights: dict[str, float], tdays: list[str]) -> None:
    """寫當日快取並清掉其他日期的舊檔。池或權重為空（FinMind 未 settle／API 失敗）時
    不寫——否則整天都會鎖在壞結果上。"""
    if not POOL_CACHE_DIR:
        return
    if pool.empty or not weights:
        logger.info("股票池或市值權重為空，不寫快取（下一班重建）")
        return
    os.makedirs(POOL_CACHE_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(POOL_CACHE_DIR, "pool_*.json")):
        if os.path.basename(old) != f"pool_{day8}.json":
            try:
                os.remove(old)
            except OSError:
                pass
    payload = {
        "version": POOL_CACHE_VERSION, "day": day8, "max_pool": max_pool, "lookback": lookback,
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
        "pool": pool[["code", "name", "industry"]].astype(str).to_dict(orient="records"),
        "weights": weights, "tdays": list(tdays),
    }
    with open(pool_cache_path(day8), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    logger.info(f"股票池快取已寫 {pool_cache_path(day8)}（{len(pool)} 檔、權重 {len(weights)} 檔）")


def dedup_records(kept: list[dict]) -> tuple[dict[str, list[dict]], dict[str, set[str]]]:
    """依股票分組＋兩層去重（2026-09-07 批次三改為兩層，順序不可反）。

    輸入：curate_news 過濾後的紀錄（date／stock_id／source／title／link）。
    回傳：(by_stock, aid_codes)——by_stock[code] 為該檔去重後的紀錄（每則已附 `aid`、新到舊）；
          aid_codes[aid] 為該文章出現的股票代號集合（供 related 欄）。

    第一層 aid：同一檔股票內同 article_id（連結正規化後的識別碼，見 news_curation.article_id）
       合併成一則——date 取最早、title 取最長（同長取字典序小者）、其餘欄位隨最長標題那筆。
       同一篇文章常帶不同追蹤參數／在相鄰 UTC 切片重複回傳，這層先把「同連結」收攏。
       合併是 min／argmax 的可交換結合運算，快取段（已合併）與新抓段再合併結果不變，
       增量輸出==全量輸出（tests/test_incremental.py 守著）。無 link 的則（aid 空）不併。
    第二層標題：key 用「去尾巴(- 媒體名)+寬鬆正規化」的標題（沿用 news_curation 既有的
       strip_title_tail/_loose）：同一篇文章常見同來源集團旗下不同站台轉載（自由財經 vs
       自由時報、UDN vs udn 大小寫），標題尾巴的媒體名不同、連結也不同，aid 攔不住。
       依日期新到舊排序再去重 → 同篇保留最新一筆。
    **跨股票不刪**：同一 aid 出現在多檔是合法關聯（一篇提到多家公司），各檔都保留，
    另由呼叫端依 aid_codes 在每則附 `related`（同 aid 的其他股票代號）供前端全站列表折疊。
    """
    merged: dict[tuple[str, str], dict] = {}
    no_aid: list[dict] = []
    for rec in kept:
        aid = article_id(rec.get("link", ""))
        rec = dict(rec, aid=aid)
        if not aid:
            no_aid.append(rec)
            continue
        k = (rec["stock_id"], aid)
        cur = merged.get(k)
        if cur is None:
            merged[k] = rec
            continue
        # 標題取最長（同長取字典序小者）；date 取最早
        if (-len(rec["title"]), rec["title"]) < (-len(cur["title"]), cur["title"]):
            rec, cur = cur, rec           # cur ← 最長標題那筆
            merged[k] = cur
        cur["date"] = min(cur["date"], rec["date"])
    kept = list(merged.values()) + no_aid

    # 新到舊；同時刻以 aid／link／title 作次鍵，讓第二層去重的取捨不依賴輸入順序
    #（全量與增量的 kept 拼接順序不同，沒有次鍵時同日同標題鍵的取捨會漂）
    kept.sort(key=lambda r: (r["date"], r["aid"], r["link"], r["title"]), reverse=True)
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

    # 跨股關聯：aid → 出現的股票代號集合
    aid_codes: dict[str, set[str]] = {}
    for sid, items in by_stock.items():
        for r in items:
            if r["aid"]:
                aid_codes.setdefault(r["aid"], set()).add(sid)
    return by_stock, aid_codes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=3, help="近 N 個交易日（預設 3）")
    ap.add_argument("--max-pool", type=int, default=150, help="股票池上限（預設 150）")
    ap.add_argument("--hourly-budget", type=int, default=550)
    ap.add_argument("--pool-csv", default=None,
                     help="指定舊格式CSV(path或url)覆蓋預設；不指定則用FinMind自建股票池")
    # 下限 1：台北日 D 的新聞散落在 UTC 切片 D-1 與 D，refresh_days=0 只會抓到
    # 一個切片，cache_cutoff 退化成「今天」，於是台北今天 00:00~07:59 的新聞
    # 既不在快取（快取止於今天之前）也沒抓到 → 每天無聲漏掉清晨那批。
    ap.add_argument("--refresh-days", type=int, default=1, choices=range(1, 8),
                    metavar="{1..7}",
                    help="增量模式下要重抓的「台北日」數（預設 1＝只重抓今天；"
                         "更早的日子沿用既有 news.json）")
    ap.add_argument("--full", action="store_true",
                    help="忽略快取、全窗重抓（換過濾規則/池邏輯後跑一次）；股票池快取也重建並覆寫")
    ap.add_argument("--no-cache", action="store_true",
                    help="股票池／市值權重／交易日不讀不寫當日快取（data/cache/pool_<YYYYMMDD>.json）")
    args = ap.parse_args()

    if not FINMIND_TOKEN:
        logger.error("未設定 FINMIND_TOKEN 環境變數")
        raise SystemExit(1)

    # ── 股票池／市值權重／交易日：同日快取（--full 重建並覆寫、--no-cache 不讀不寫、--pool-csv 不快取）──
    day8 = taipei_today().strftime("%Y%m%d")
    cached = None
    if not args.full and not args.no_cache and not args.pool_csv:
        cached = load_pool_cache(day8, args.max_pool, args.lookback)
    if cached:
        pool, weight_map, tdays = cached["pool"], cached["weights"], cached["tdays"]
        logger.info(f"股票池／市值權重／交易日：讀同日快取 {pool_cache_path(day8)}（{len(pool)} 檔）")
    else:
        pool = load_pool(args.pool_csv, args.max_pool) if args.pool_csv else build_pool_from_finmind(args.max_pool)
        weight_map = fetch_market_value_weights()
        tdays = recent_trading_days(args.lookback)      # 交易日（trading_days 欄位、前端 TDAYS 依賴）
        if not args.no_cache and not args.pool_csv:
            save_pool_cache(day8, args.max_pool, args.lookback, pool, weight_map, tdays)
    dates = news_calendar_days(tdays)                   # 抓新聞用日曆日（含週末/假日）
    logger.info(f"時間範圍：{dates[0]} ~ {dates[-1]}（{len(tdays)} 個交易日、共 {len(dates)} 個日曆日）")
    throttle = Throttle(args.hourly_budget)

    name_map = dict(zip(pool["code"], pool["name"]))
    ind_map = dict(zip(pool["code"], pool["industry"]))
    pool_codes = [str(c) for c in pool["code"]]

    # ── 增量規劃 ────────────────────────────────────────────────────
    # 這支排程每小時跑一次（Worker dispatch，一天 16 班），但視窗裡只有「今天」會變：
    # 實測 5 日視窗 724 則新聞，今天只佔 73 則（~10%）。原本每班都把 池150 × 日曆日7~10
    # ＝1000~1500 個 (檔,日) 請求全部重打，逼出 --hourly-budget 1400 + timeout 90 分
    # + Throttle 睡到整點。改成只重抓最近幾個切片、其餘沿用既有 news.json。
    #
    # 切片與台北日的關係（關鍵）：FinMind 的單日切片是 UTC 日，台北 = UTC+8，所以
    # UTC 切片 s 涵蓋台北 s 08:00 ~ (s+1) 07:59 → 台北 D 日的新聞散落在切片 D-1 與 D。
    # 要「完整」重抓 R 個台北日，就必須抓 R+1 個切片。
    prev = {} if args.full else load_cache(OUTPUT_JSON)
    prev_dates = set(prev.get("coverage", {}).get("dates", []))
    prev_codes = set(prev.get("coverage", {}).get("codes", []))

    refresh_slices = dates[-(args.refresh_days + 1):]
    # 被 refresh 完整覆蓋的最早台北日（其前一個切片也在 refresh 範圍內才算完整）
    cache_cutoff = refresh_slices[1] if len(refresh_slices) > 1 else refresh_slices[0]

    # 視窗新增了「不在 refresh 範圍、又沒抓過」的舊切片（通常是 --lookback 變大）
    # → 增量拼不出完整視窗，退回全量
    stale = (set(dates) - prev_dates) - set(refresh_slices)
    if prev and stale:
        logger.info(f"視窗新增 {len(stale)} 個未抓過的舊切片（lookback 變大？）→ 本次全量重抓")
        prev, prev_codes = {}, set()

    incr_codes = [c for c in pool_codes if c in prev_codes]      # 有快取 → 只抓 refresh 切片
    fresh_codes = [c for c in pool_codes if c not in prev_codes]  # 新進池 → 全窗補抓
    plan = {c: (refresh_slices if c in prev_codes else dates) for c in pool_codes}
    n_req = sum(len(v) for v in plan.values())
    if prev:
        logger.info(f"增量模式：重抓 {len(refresh_slices)} 個切片 {refresh_slices[0]}~{refresh_slices[-1]}"
                    f"（完整覆蓋台北日 >= {cache_cutoff}）；"
                    f"{len(incr_codes)} 檔沿用快取 + {len(fresh_codes)} 檔新進池全窗補抓"
                    f" → 共 {n_req} 個請求（全量需 {len(pool_codes)*len(dates)}）")
    else:
        logger.info(f"全量模式：{len(pool_codes)} 檔 × {len(dates)} 日 → 共 {n_req} 個請求")

    raw: list[dict] = []
    failed_codes: set[str] = set()   # 本班有任一請求失敗的檔 → 不寫進 coverage
    for i, code in enumerate(pool_codes, 1):
        cnt = 0
        # 有快取的檔只接受 refresh 完整覆蓋的台北日（更早的由快取供應，避免重複）；
        # 新進池的檔沒有快取可用，整個視窗都收
        lo = cache_cutoff if (prev and code in prev_codes) else tdays[0]
        for d in plan[code]:
            recs, ok = fetch_news_one(code, d, throttle)
            if not ok:
                failed_codes.add(code)   # 這檔本班不算「已涵蓋」，見下方 coverage
            for rec in recs:
                # FinMind date 是 UTC → 轉台北（見 finmind_news_date_to_taipei 註解）
                date_tpe = finmind_news_date_to_taipei(rec.get("date", ""))
                # 視窗以「台北日」為準：多抓的前一個 UTC 日切片裡，台北時間
                # 仍早於 lo 的新聞不在本次負責範圍內，這裡丟掉
                if date_tpe[:10] < lo:
                    continue
                raw.append({
                    "date": date_tpe,
                    "stock_id": str(rec.get("stock_id", code)),
                    "source": str(rec.get("source", "")).strip(),
                    "title": str(rec.get("title", "")).strip(),
                    "link": str(rec.get("link", "")),
                })
                cnt += 1
        if i % 20 == 0 or i == len(pool_codes):
            logger.info(f"進度 {i}/{len(pool_codes)}（最新 {code} {name_map.get(code,'')}: {cnt} 則）")

    logger.info(f"抓取原始 {len(raw)} 則，套用白名單過濾...")
    kept = curate_news(raw)
    logger.info(f"過濾後保留 {len(kept)} 則")

    # 快取供應的區段：台北日 [tdays[0], cache_cutoff)，僅限有快取覆蓋的檔
    if prev:
        reused = cached_records(prev, set(incr_codes), tdays[0], cache_cutoff)
        logger.info(f"沿用快取 {len(reused)} 則（台北日 {tdays[0]} ~ {cache_cutoff} 之前）")
        kept = kept + reused

    # 依股票分組＋兩層去重（aid 合併 → 標題鍵）＋跨股關聯；邏輯抽在 dedup_records()
    # 讓離線統計／測試能對既有 news.json 重跑同一套規則（不需 token）。
    by_stock, aid_codes = dedup_records(kept)

    stocks = []
    for code, items in by_stock.items():
        items.sort(key=lambda r: (r["date"], r["aid"], r["title"]), reverse=True)
        news = []
        for r in items:
            n = {
                "date": r["date"],
                "source": normalize_source(r["source"]),
                "title": r["title"],
                "link": r["link"],
                "impact": classify_impact(code, r["title"]),
                "aid": r["aid"],
            }
            related = sorted(aid_codes.get(r["aid"], set()) - {code}) if r["aid"] else []
            if related:
                n["related"] = related
            news.append(n)
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

    # total_news 必須是「實際輸出的則數」（去重後、前端「N 則」顯示的那個數字），
    # 不能用 len(kept)：kept 是**去重前**的計數，而去重掉多少完全取決於抓了幾個切片
    #   - 全量（7 切片）：kept ~730 → 實際輸出 ~479（FinMind 同一篇在相鄰切片重複出現）
    #   - 增量（2 切片 + 快取）：kept ~491 → 實際輸出 ~475（快取那段早已去重過）
    # 兩者實際輸出一致，但 len(kept) 會讓前端的「則數」看起來掉了三成。
    delivered = sum(len(s["news"]) for s in stocks)
    # 去重後「篇」數：distinct aid ＋ 無 link 的則各算一篇。與 delivered 一樣只看實際輸出，
    # 不受抓取模式（全量／增量）影響。
    n_unique_articles = len({n["aid"] for s in stocks for n in s["news"] if n["aid"]}) \
        + sum(1 for s in stocks for n in s["news"] if not n["aid"])
    covered_codes = [c for c in pool_codes if c not in failed_codes]
    if failed_codes:
        logger.warning(f"⚠ {len(failed_codes)} 檔本班有抓取失敗，已排出 coverage："
                       f"{sorted(failed_codes)[:10]}{' …' if len(failed_codes) > 10 else ''}"
                       f"；下一班會對這些檔全窗重抓補回")
    payload = {
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
        "lookback_days": args.lookback,
        "trading_days": tdays,
        "pool_size": int(len(pool)),
        "stocks_with_news": len(stocks),
        "total_news": delivered,
        # n_raw＝各股 news 條目總數（同 total_news；同一篇跨股計多次）、
        # n_unique_articles＝distinct 文章數（aid 去重）。前端說明列「原始 N 則／去重後 M 篇」。
        "stats": {"n_raw": delivered, "n_unique_articles": n_unique_articles},
        # 下一班的增量依據：本檔已涵蓋這些 (日曆日切片 × 股票代號) 的組合。
        # 「有涵蓋但 stocks 裡沒出現」＝該檔該日確實沒新聞（而非沒抓過）——少了這個
        # 區塊就無法區分兩者，只能全量重抓。~1.5KB，且內容穩定、幾乎不增加 git churn。
        #
        # **本班抓取失敗的檔一律排除**：這個不變式的前提是「沒出現＝真的沒有」，
        # 而失敗的請求回空清單，會讓「暫時抓不到」被寫成「那天沒新聞」而沾黏下去
        # （之後每班都從快取拿這個「沒有」，只有 --full 沖得掉）。排除後該檔下一班
        # 不在 prev_codes 裡 → 被當成新進池、全窗重抓 → 自動補回，不必等 --full。
        "coverage": {"dates": dates, "codes": covered_codes},
        "stocks": stocks,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    logger.info(f"已輸出 {OUTPUT_JSON}：{len(stocks)} 檔有新聞、共 {delivered} 則"
                f"（去重前 {len(kept)} 則）")


if __name__ == "__main__":
    main()
