# news_curation.py
# ============================================================
#  晨報策展新聞 — 來源白名單過濾邏輯
#
#  依 output/news_source_findings.md 定案的白名單規格實作。
#  純函式、無 I/O、無外部相依（僅標準庫），可被 morning.py 直接 import：
#
#      from engine.news_curation import curate_news
#      kept = curate_news(news_records)   # news_records: list[dict]
#
#  每筆 news record 需含（FinMind TaiwanStockNews schema）：
#      date, stock_id, source, title   （link 等其他欄位原樣保留）
#
#  過濾管線（每檔股票、每個交易日獨立處理）：
#    1. source.strip() 正規化 → 合併同媒體的多種寫法
#    2. 命中核心白名單 → 保留
#    3. CMoney 次級過濾：標題含「股市爆料同學會」→ 排除（即使 source=CMoney）
#    4. 跨來源去重：Yahoo 標題去尾巴後，若與同股同日其他已保留來源重複 → 丟 Yahoo
#    5. 若 1-4 後該股當日 0 篇 → 改用 Fallback Pool 重跑（仍排除「永遠排除」，
#       不需步驟 3-4）
#    6. 仍 0 篇 → 回傳空清單（呼叫端顯示「無相關新聞」）
#
#  未知來源（不在任何清單）預設排除：白名單前提是「未列名 = 不信任」。
# ============================================================

import re

# ── 1. source 正規化對照（raw source → canonical）─────────────
#  只列出有多種寫法的媒體；未列出者 canonical == 原字串。
#  對照表沿用 news_source_findings.md 第 6 節。
_NORMALIZE: dict[str, str] = {
    # UDN
    "UDN": "UDN", "udn": "UDN", "udn.com": "UDN",
    # Yahoo（四變體合併，去重見步驟 4）
    "Yahoo股市": "Yahoo", "Yahoo新聞": "Yahoo",
    "Yahoo 財經": "Yahoo", "Yahoo - 汽機車": "Yahoo",
    # 鉅亨
    "news.cnyes.com": "鉅亨(cnyes)", "cnyes.com": "鉅亨(cnyes)",
    "鉅亨": "鉅亨(cnyes)", "鉅亨網": "鉅亨(cnyes)", "鉅亨號": "鉅亨(cnyes)",
    # FTNN
    "FTNN 新聞網": "FTNN新聞", "FTNN 新聞": "FTNN新聞",
    # 三立
    "三立新聞網SETN.com": "三立新聞", "三立新聞": "三立新聞",
    "三立娛樂星聞": "三立新聞",
    # 富聯網
    "富聯網": "富聯網", "ww2.money-link.com.tw": "富聯網",
    # 自由時報系
    "自由時報": "自由時報系", "自由財經": "自由時報系",
    "stock.ltn.com.tw": "自由時報系",
    # 鏡週刊 / 鏡報系
    "鏡週刊Mirror Media": "鏡週刊/鏡報系", "鏡報": "鏡週刊/鏡報系",
    "鏡新聞": "鏡週刊/鏡報系", "mirrormedia.mg": "鏡週刊/鏡報系",
    "mirrordaily.news": "鏡週刊/鏡報系",
    # 民視
    "民視財經網": "民視", "民視新聞網": "民視",
    "民視運動網": "民視", "ftvnews.com.tw": "民視",
    # 台視
    "台視全球資訊網": "台視", "台視新聞網": "台視",
    # 東森
    "東森電視": "東森", "東森新聞": "東森",
    # ETtoday
    "ETtoday財經雲": "ETtoday", "ETtoday房產雲": "ETtoday",
    # 中央社
    "中央社": "中央社", "中央社 CNA": "中央社",
    # Top1 Markets（排除清單，仍正規化以利辨識）
    "Top1 Markets": "Top1 Markets", "top1markets.com": "Top1 Markets",
}

# ── 2. 核心白名單（canonical，一律通過）──────────────────────
# 註：2026-07 依實跑量能檢討，移除高量低訊號來源（富聯網、CMoney、
#     CMoney投資網誌、sinotrade、Yahoo、財訊）以降低每日新聞量。
#     2026-07-10 使用者指示再移除：信傳媒、優分析UAnalyze。
CORE_WHITELIST: frozenset[str] = frozenset({
    "UDN", "經濟日報", "工商時報", "自由時報系", "中央社", "今周刊",
    "遠見雜誌", "商周財富網", "理財周刊",
    "MoneyDJ", "TechNews 科技新報", "鉅亨(cnyes)",
    "FTNN新聞", "富果直送",
})

# ── 3. Fallback Pool（canonical，僅核心 0 篇時啟用）───────────
# 註：2026-07 移除 LINE TODAY、玩股網、台視、三立新聞、MSN、中時新聞網，
#     再移除 旺得富理財網、東森、TVBS新聞網。
FALLBACK_POOL: frozenset[str] = frozenset({
    # 生技專業站
    "genetinfo.com", "環球生技月刊", "GeneOnline News",
    "ETtoday", "民視",
    "鏡週刊/鏡報系",
})

# ── 特殊規則常數 ─────────────────────────────────────────────
CMONEY_SOURCE = "CMoney"
CMONEY_FORUM_MARKER = "股市爆料同學會"   # 標題含此字串即為論壇 UGC，永遠排除
YAHOO_SOURCE = "Yahoo"

# 標題尾巴「 - 媒體名」：尾巴不含分隔符，確保抓到最後一段
_TAIL_RE = re.compile(r"^(?P<body>.+?)\s+[-—–|]\s+(?P<tail>[^-—–|]{1,20})$")
_LOOSE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


# ── 工具函式 ─────────────────────────────────────────────────

def normalize_source(source: str) -> str:
    """source.strip() 後套正規化對照；未列名者回傳自身。"""
    s = (source or "").strip()
    return _NORMALIZE.get(s, s)


def strip_title_tail(title: str) -> str:
    """去掉標題結尾的「 - 媒體名」，回傳本文；無尾巴時回傳原標題。"""
    t = (title or "").strip()
    m = _TAIL_RE.match(t)
    return m.group("body").strip() if m else t


def _loose(s: str) -> str:
    """寬鬆比對鍵：去所有空白與標點、轉小寫。"""
    return _LOOSE_RE.sub("", (s or "")).lower()


def _is_cmoney_forum(canonical: str, title: str) -> bool:
    return canonical == CMONEY_SOURCE and CMONEY_FORUM_MARKER in (title or "")


def _day(rec: dict) -> str:
    return str(rec.get("date", ""))[:10]


# ── 主邏輯（單一 stock_id + 單一交易日）──────────────────────

def curate_stock_day(records: list[dict], *, dedup_yahoo: bool = True) -> list[dict]:
    """
    對「同一檔股票、同一交易日」的新聞清單套用完整過濾管線。
    回傳保留的 record（原 dict 物件，順序穩定）。
    """
    # 步驟 1-3：核心白名單 + CMoney 論壇次級過濾
    core_kept: list[dict] = []
    for rec in records:
        canonical = normalize_source(rec.get("source", ""))
        if canonical not in CORE_WHITELIST:
            continue
        if _is_cmoney_forum(canonical, rec.get("title", "")):
            continue                      # 論壇 UGC，永遠排除
        core_kept.append(rec)

    # 步驟 4：Yahoo 跨來源去重
    if dedup_yahoo:
        other_keys = {
            _loose(strip_title_tail(r.get("title", "")))
            for r in core_kept
            if normalize_source(r.get("source", "")) != YAHOO_SOURCE
        }
        deduped: list[dict] = []
        for rec in core_kept:
            if normalize_source(rec.get("source", "")) == YAHOO_SOURCE:
                key = _loose(strip_title_tail(rec.get("title", "")))
                if key and key in other_keys:
                    continue              # 與其他已保留來源重複，丟 Yahoo 這筆
            deduped.append(rec)
        core_kept = deduped

    if core_kept:
        return core_kept

    # 步驟 5：核心 0 篇 → Fallback Pool（不需步驟 3-4）
    fallback_kept = [
        rec for rec in records
        if normalize_source(rec.get("source", "")) in FALLBACK_POOL
    ]
    # 步驟 6：仍 0 篇 → 空清單（呼叫端顯示「無相關新聞」）
    return fallback_kept


# ── 公開入口（多股多日）──────────────────────────────────────

def curate_news(records: list[dict], *, dedup_yahoo: bool = True) -> list[dict]:
    """
    對任意新聞清單（可含多檔、多日）套用過濾。
    依 (stock_id, 交易日) 分組後各自處理，回傳合併後的保留清單，
    順序依首次出現的分組穩定排列。
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    for rec in records:
        key = (str(rec.get("stock_id", "")), _day(rec))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)

    out: list[dict] = []
    for key in order:
        out.extend(curate_stock_day(groups[key], dedup_yahoo=dedup_yahoo))
    return out
