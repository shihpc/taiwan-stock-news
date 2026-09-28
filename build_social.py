#!/usr/bin/env python3
# build_social.py
# ============================================================
#  社群聲量量測班（階段一）— 每日產 data/social/YYYY-MM-DD.json
#
#  定位：**只收資料、不顯示、不下判準**（規格與驗收條件正本：docs/social-phase1.md）。
#  來源只有 PTT Stock 板（H2）；產物只存標題／作者／時間／推噓數／連結／命中代號／
#  情緒標籤，正文只在記憶體用完即丟（H3）；沒有任何「偏多／偏空／建議」欄位（H9）。
#
#  流程：
#   1. robots.txt 守門（H5）：User-agent: * 對 / 或 /bbs/ Disallow → 不抓任何文章，
#      仍寫產物（robots:"disallow"、articles_n:0）並 exit 0。抓不到 robots 也視同不抓
#     （robots:"unreachable"，保守立場：量測班寧可缺一天、不可在未取得同意的狀態下抓）。
#   2. 板首頁 index.html 向前翻頁（index{N}.html），最多 SOCIAL_MAX_PAGES 頁，
#      直到整頁文章都早於目標日；只挑「月/日」＝目標日的列。
#   3. 文章頁依推文數高者優先、最多 SOCIAL_MAX_ARTICLES 篇；台北日歸屬以文章頁的
#      發文時間為準（板首頁只有月/日，用來決定要不要抓文章頁）。
#   4. 標的抽取：代號 regex ＋ 股票清單全名整詞命中（只認全名、不建暱稱字典）。
#   5. 情緒分類：Anthropic Message Batches（半價）→ 逾時或失敗逐篇同步回退；
#      缺 ANTHROPIC_API_KEY → 情緒整批 null、llm.skipped="no-key"，聲量資料照寫（H7）。
#   6. 聚合 stocks{}、寫日檔＋index.json；拆除提醒（滿 SOCIAL_TEARDOWN_DUE_DAYS 日印 warning）。
#
#  節流（H4）：對 ptt.cc 請求間隔 ≥ SOCIAL_MIN_INTERVAL、單班總請求 ≤ SOCIAL_MAX_REQUESTS，
#  UA 帶專案識別字串。金鑰只由環境變數讀，例外訊息過 mask_secret() 才印（H6）。
#
#  用法：
#    python build_social.py [--date YYYY-MM-DD] [--from-fixture DIR] [--no-llm] [--stock-info PATH]
#  exit code：0＝完成（含預期跳過：--no-llm／no-key／robots disallow／unreachable／no-articles）；
#             2＝聲量產物已落地但情緒分類失效（classify() 例外 → llm.skipped="error"，或有目標篇卻
#               全數失敗 failed_n == 目標篇數）。workflow 走「延後紅燈」：build 步驟不失敗、commit 照跑、
#               末段依 exit code 讓 job 紅並 notify-failure。
#
#  ⚠ PTT DOM 結構（r-ent／nrec／article-metaline／push）依記憶實作、沙箱連不到 ptt.cc，
#    線上首跑要對照實際頁面；解析器一律寬鬆——找不到元素回 None／空值、不拋例外。
# ============================================================
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

import requests

# ── 常數 ────────────────────────────────────────────────────────────────
PTT_BASE = "https://www.ptt.cc"
PTT_BOARD = "Stock"
PTT_INDEX_PATH = f"/bbs/{PTT_BOARD}/index.html"
PTT_ROBOTS_PATH = "/robots.txt"
SOCIAL_UA = "taiwan-stock-news-social/1 (+https://github.com/shihpc/taiwan-stock-news; measurement only)"

SOCIAL_MIN_INTERVAL = 1.0          # 對 ptt.cc 兩次請求最小間隔（秒）
SOCIAL_MAX_REQUESTS = 400          # 單班對 ptt.cc 總請求上限（含 robots／板首頁／文章頁）
SOCIAL_MAX_PAGES = 15              # 板首頁最多翻幾頁
SOCIAL_MAX_ARTICLES = 300          # 每日最多抓幾篇文章頁（推文數高者優先）
SOCIAL_MAX_CODES_PER_ARTICLE = 8   # 每篇最多記幾個代號（依出現順序）
SOCIAL_BODY_CHARS = 600            # 送 LLM 的正文前 N 字
SOCIAL_BATCH_DEADLINE_SEC = 40 * 60
SOCIAL_BATCH_POLL_SEC = 20
# 同步回退的總時間預算：與 batch 40 分合計 55 分，仍留餘裕給 build-social.yml 的 70 分 job
#（抓取上限 400 請求×≥1 秒≈7 分＋commit）。超過預算就停止回退：剩餘篇數計入 llm.failed_n、
# 代號記 unk——API 異常時寧可情緒缺、不可讓 job 被砍掉連聲量資料一起丟（H7 精神）。
SOCIAL_SYNC_BUDGET_SEC = 15 * 60
SOCIAL_MODEL = "claude-opus-5"     # 要換模型改這裡即可，產物 llm.model 會如實記錄
SOCIAL_PROMPT_VER = 1              # 改 SOCIAL_SYS 就 +1，舊檔不可比
SOCIAL_SAMPLE_SINCE = "2026-09-27"
SOCIAL_TEARDOWN_DUE_DAYS = 45      # 日曆日；到期只印 warning，不 issue、不 dispatch
FETCH_RETRY_SLEEP = 2.0            # ptt.cc 失敗退避一次
LLM_RETRY_SLEEP = 5.0              # 同步回退失敗退避一次
LLM_MAX_TOKENS = 256

URL_MESSAGES = "https://api.anthropic.com/v1/messages"
URL_BATCHES = "https://api.anthropic.com/v1/messages/batches"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

OUT_DIR = "data/social"
CACHE_DIR = "data/cache"           # .gitignore 已排除

TAIPEI_TZ = timezone(timedelta(hours=8))

SENT_LABELS = ("pos", "neg", "neu")

SOCIAL_SYS = (
    "你是文字標註員。給你一篇 PTT Stock 板文章的標題、正文節錄，以及文中提到的台股代號清單。"
    "任務：對清單中的**每一個代號**，只判斷**作者本人對該檔股票的態度**，回 pos（看好／偏多）、"
    "neg（看壞／偏空）或 neu（中性／純資訊／無法判斷）。"
    "規則：不判斷市場或大盤、不預測走勢、不加任何解釋或建議；作者態度不明或文章只是轉貼新聞一律 neu。"
    "輸出格式：只輸出一個 JSON 物件，鍵＝代號（與輸入完全相同）、值＝pos|neg|neu，"
    "不得有多餘文字、不得輸出清單以外的代號。"
)

CODE_RE = re.compile(r"(?<![0-9A-Z])\d{4}[A-Z]?(?![0-9A-Z])")
AID_RE = re.compile(r"(M\.\d+\.A\.[0-9A-F]+)")
CAT_RE = re.compile(r"^\s*(?:Re:\s*|Fw:\s*)*\[([^\]]{1,6})\]")
SECRET_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ)


def taipei_today() -> date:
    return taipei_now().date()


# ── 金鑰遮罩（H6）────────────────────────────────────────────────────────
def mask_secret(msg, *secrets: str) -> str:
    """例外訊息在印出前一律過這裡：把 sk-ant-… 形狀與傳入的 secret 字面量都換成 ***。
    secret 為空或過短時跳過字面量替換（否則 str.replace("") 會把訊息炸開）。"""
    s = str(msg)
    for sec in secrets:
        if sec and len(sec) >= 8:
            s = s.replace(sec, "***")
    s = SECRET_RE.sub("sk-ant-***", s)
    s = re.sub(r"(token=)[^&\s'\"]+", r"\1***", s)
    return s


def warn(msg: str) -> None:
    print(f"::warning::{msg}", flush=True)


# ── robots.txt（H5）─────────────────────────────────────────────────────
ROBOTS_CHECK_PATHS = ("/", "/bbs/", f"/bbs/{PTT_BOARD}/", f"/bbs/{PTT_BOARD}/index.html")


def _robots_rule_re(pattern: str) -> re.Pattern:
    """Disallow 路徑 → regex：`*` 任意串、結尾 `$` 精確結尾、其餘為前綴比對。"""
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    rx = "^" + ".*".join(re.escape(part) for part in core.split("*"))
    rx += "$" if anchored else ".*"
    return re.compile(rx)


def parse_robots(text: str | None) -> dict:
    """回 {verdict: allow|disallow|unreachable, crawl_delay: float|None}。只看 User-agent: * 那組
    （含多個 UA 共用一組的寫法）；**任一** Disallow 規則命中 ROBOTS_CHECK_PATHS 任一路徑
    （`/`、`/bbs/`、`/bbs/Stock/`、板首頁）即 disallow——含 `*`／`$` 萬用字元；Allow 不解、寧可多擋。
    Crawl-delay 只取 * 那組、解析失敗視同未宣告。"""
    if text is None:
        return {"verdict": "unreachable", "crawl_delay": None}
    applies = False
    seen_ua = False
    verdict = "allow"
    crawl_delay = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            # 連續多行 User-agent 屬同一組；遇到規則行後再出現 User-agent 才是新組
            if not seen_ua:
                applies = False
            applies = applies or (v == "*")
            seen_ua = True
            continue
        seen_ua = False
        if not applies:
            continue
        if k == "disallow" and v:
            rx = _robots_rule_re(v)
            if any(rx.match(p) for p in ROBOTS_CHECK_PATHS):
                verdict = "disallow"
        elif k == "crawl-delay":
            try:
                crawl_delay = max(crawl_delay or 0.0, float(v))
            except ValueError:
                pass
    return {"verdict": verdict, "crawl_delay": crawl_delay}


def robots_verdict(text: str | None) -> str:
    return parse_robots(text)["verdict"]


# ── 抓取（H4：節流＋上限＋UA）────────────────────────────────────────────
FETCH_ERRORS_MAX = 10     # 產物 fetch_errors 最多留前 N 筆


class PttFetcher:
    """對 ptt.cc 的唯一出口。get(path) 回 HTML 字串或 None（失敗／預算耗盡）。
    fixture_dir 非 None 時改讀本機檔（測試與沙箱用），仍計 requests_n。
    診斷（2026-09-28 線上首跑 robots=unreachable 後補）：每次 get() 後 `last_detail`＝
    {status, err, final_url, attempts}（status 有值時 err 為 None；err＝遮罩後的例外類別名＋訊息前 200 字；
    final_url＝實際回應 URL，看有沒有被 302 到 over18／擋牆頁）；每個失敗的嘗試收進 `errors`
    （最多 FETCH_ERRORS_MAX 筆，path 不含 query）。"""

    def __init__(self, session=None, min_interval: float = SOCIAL_MIN_INTERVAL,
                 max_requests: int = SOCIAL_MAX_REQUESTS, fixture_dir: str | None = None,
                 sleep=time.sleep, clock=time.monotonic, retry_sleep: float = FETCH_RETRY_SLEEP):
        self.session = session
        self.min_interval = min_interval
        self.max_requests = max_requests
        self.fixture_dir = fixture_dir
        self._sleep = sleep
        self._clock = clock
        self.retry_sleep = retry_sleep
        self.requests_n = 0
        self.last_at: float | None = None
        self.exhausted = False
        self.errors: list[dict] = []
        self.errors_total = 0
        self.last_detail: dict = {"status": None, "err": None, "final_url": None, "attempts": 0}

    def _throttle(self) -> None:
        if self.last_at is not None and self.min_interval > 0:
            gap = self._clock() - self.last_at
            if gap < self.min_interval:
                self._sleep(self.min_interval - gap)
        self.last_at = self._clock()

    def _fixture_path(self, path: str) -> str:
        name = os.path.basename(path)
        if path == PTT_ROBOTS_PATH:
            for cand in ("robots.txt", "robots_allow.txt"):
                p = os.path.join(self.fixture_dir, cand)
                if os.path.exists(p):
                    return p
            return os.path.join(self.fixture_dir, "robots.txt")
        return os.path.join(self.fixture_dir, name)

    def _one(self, path: str) -> tuple[str | None, int | None, str | None]:
        """回 (html|None, status|None, final_url|None)；例外由呼叫端接。"""
        if self.fixture_dir is not None:
            p = self._fixture_path(path)
            if not os.path.exists(p):
                return None, None, None
            with open(p, encoding="utf-8") as f:
                return f.read(), 200, None
        r = self.session.get(PTT_BASE + path, headers={"User-Agent": SOCIAL_UA},
                             cookies={"over18": "1"}, timeout=(10, 30))
        final_url = getattr(r, "url", None)
        if r.status_code != 200:
            return None, r.status_code, final_url
        return r.text, r.status_code, final_url

    def _record_error(self, path: str, status: int | None, err: str | None) -> None:
        self.errors_total += 1
        if len(self.errors) < FETCH_ERRORS_MAX:
            self.errors.append({"path": path.split("?", 1)[0], "status": status, "err": err})

    def get(self, path: str) -> str | None:
        """兩次機會（失敗退避 retry_sleep 再試一次），每次都計入 requests_n 與節流。"""
        detail = {"status": None, "err": None, "final_url": None, "attempts": 0}
        self.last_detail = detail
        for attempt in (1, 2):
            if self.requests_n >= self.max_requests:
                self.exhausted = True
                return None
            self._throttle()
            self.requests_n += 1
            detail["attempts"] = attempt
            try:
                html, status, final_url = self._one(path)
                detail.update(status=status, err=None, final_url=final_url)
                if html is None and status is None and self.fixture_dir is not None:
                    detail["err"] = "FixtureMissing: no such file"
            except Exception as e:
                html = None
                detail.update(status=None, final_url=None,
                              err=mask_secret(f"{type(e).__name__}: {e}")[:200])
                print(f"  fetch {path} 第{attempt}次例外：{detail['err']}", flush=True)
            if html is not None:
                return html
            self._record_error(path, detail["status"], detail["err"])
            if attempt == 1:
                self._sleep(self.retry_sleep)
        return None


# ── 板首頁解析 ──────────────────────────────────────────────────────────
class _StackParser(HTMLParser):
    """共用：維護 (tag, classes, id) 堆疊，子類用 in_() 判斷目前落在哪個容器裡。"""

    VOID = {"br", "img", "hr", "meta", "link", "input"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str], str, dict]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = set((a.get("class") or "").split())
        if tag not in self.VOID:
            self.stack.append((tag, cls, a.get("id") or "", a))
        self.on_start(tag, cls, a)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        self.on_end(tag)

    def in_(self, tag: str | None = None, cls: str | None = None, id_: str | None = None) -> bool:
        for t, c, i, _ in self.stack:
            if (tag is None or t == tag) and (cls is None or cls in c) and (id_ is None or i == id_):
                return True
        return False

    def on_start(self, tag, cls, attrs):  # pragma: no cover - overridden
        pass

    def on_end(self, tag):  # pragma: no cover - overridden
        pass


class BoardParser(_StackParser):
    """板首頁：每個 div.r-ent → {nrec, title, href, author, md}；div.r-list-sep 之後是置底公告
    （pinned=True）；a.btn.wide 文字含「上頁」→ prev_href。"""

    def __init__(self):
        super().__init__()
        self.entries: list[dict] = []
        self.prev_href: str | None = None
        self.pinned = False
        self._cur: dict | None = None
        self._a_attrs: dict | None = None
        self._a_text = ""

    def on_start(self, tag, cls, attrs):
        if tag == "div" and "r-list-sep" in cls:
            self.pinned = True
        if tag == "div" and "r-ent" in cls:
            self._cur = {"nrec": "", "title": "", "href": None, "author": "", "md": "",
                         "pinned": self.pinned}
        if tag == "a":
            self._a_attrs = attrs
            self._a_text = ""
            if self._cur is not None and self.in_("div", "title"):
                self._cur["href"] = attrs.get("href")

    def handle_data(self, data):
        if self._a_attrs is not None:
            self._a_text += data
        if self._cur is None:
            return
        if self.in_("div", "nrec"):
            self._cur["nrec"] += data.strip()
        elif self.in_("div", "title"):
            self._cur["title"] += data
        elif self.in_("div", "author"):
            self._cur["author"] += data.strip()
        elif self.in_("div", "date"):
            self._cur["md"] += data.strip()

    def on_end(self, tag):
        if tag == "a" and self._a_attrs is not None:
            cls = set((self._a_attrs.get("class") or "").split())
            if "btn" in cls and "wide" in cls and "上頁" in self._a_text:
                self.prev_href = self._a_attrs.get("href")
            self._a_attrs = None
        if tag == "div" and self._cur is not None and not self.in_("div", "r-ent"):
            self._cur["title"] = " ".join(self._cur["title"].split())
            self.entries.append(self._cur)
            self._cur = None


def parse_board(html: str) -> tuple[list[dict], str | None]:
    p = BoardParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    if p._cur is not None:
        p.entries.append(p._cur)
    return p.entries, p.prev_href


def nrec_to_int(s: str) -> int:
    """推文數欄：「爆」→100（PTT 對 ≥100 推顯示爆）、「X{n}」→ −n×10（噓 ≥ n×10）、
    「XX」→ −100、純數字→int、空→0。"""
    s = (s or "").strip()
    if not s:
        return 0
    if s == "爆":
        return 100
    if s == "XX":
        return -100
    if s.startswith("X") and s[1:].isdigit():
        return -int(s[1:]) * 10
    if s.isdigit():
        return int(s)
    return 0


def md_to_date(md: str, target: date) -> date | None:
    """板首頁「 9/27」→ date。年份取目標日的年；若得出的日期比目標日晚 30 天以上，視為前一年
    （跨年翻頁：目標 1 月、列表出現 12 月）。"""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})\s*$", md or "")
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    try:
        cand = date(target.year, mo, d)
    except ValueError:
        return None
    if (cand - target).days > 30:
        try:
            cand = date(target.year - 1, mo, d)
        except ValueError:
            return None
    return cand


def is_skippable(e: dict) -> bool:
    t = e.get("title") or ""
    return (not e.get("href")) or ("本文已被刪除" in t) or t.startswith("[公告]") or t.startswith("Fw: [公告]")


def title_cat(title: str) -> str | None:
    m = CAT_RE.match(title or "")
    return m.group(1) if m else None


# ── 文章頁解析 ──────────────────────────────────────────────────────────
class ArticleParser(_StackParser):
    """div#main-content：div.article-metaline（作者／標題／時間）與 article-metaline-right（看板）
    的 span.article-meta-tag / span.article-meta-value；正文＝main-content 直屬文字（去 meta、
    推文、span.f2 發信站列）；div.push > span.push-tag 計推噓。"""

    def __init__(self):
        super().__init__()
        self.meta: list[tuple[str, str]] = []
        self._tag = ""
        self._val = ""
        self._in_meta = False
        self.body_parts: list[str] = []
        self.push = 0
        self.boo = 0
        self._pushtag = ""

    def on_start(self, tag, cls, attrs):
        if tag == "div" and ("article-metaline" in cls or "article-metaline-right" in cls):
            self._in_meta = True
            self._tag, self._val = "", ""
        if tag == "span" and "push-tag" in cls:
            self._pushtag = ""

    def handle_data(self, data):
        if not self.in_("div", id_="main-content"):
            return
        if self._in_meta:
            if self.in_("span", "article-meta-tag"):
                self._tag += data
            elif self.in_("span", "article-meta-value"):
                self._val += data
            return
        if self.in_("span", "push-tag"):
            self._pushtag += data
            return
        if self.in_("div", "push") or self.in_("span", "f2") or self.in_("div", "richcontent"):
            return
        self.body_parts.append(data)

    def on_end(self, tag):
        if tag == "div" and self._in_meta and not self.in_("div", "article-metaline") \
                and not self.in_("div", "article-metaline-right"):
            self.meta.append((self._tag.strip(), self._val.strip()))
            self._in_meta = False
        if tag == "span" and self._pushtag:
            t = self._pushtag.strip()
            if t.startswith("推"):
                self.push += 1
            elif t.startswith("噓"):
                self.boo += 1
            self._pushtag = ""


def parse_article_ts(s: str) -> datetime | None:
    """`Sat Sep 27 21:03:15 2026` → naive datetime（PTT 顯示即台北時間）。"""
    try:
        return datetime.strptime((s or "").strip(), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None


def parse_article(html: str) -> dict | None:
    """回 {author, title, ts(datetime|None), body, push, boo}；沒有 main-content 回 None。"""
    p = ArticleParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    if not p.meta and not p.body_parts:
        return None
    by_tag = {}
    for k, v in p.meta:
        by_tag.setdefault(k, v)
    values = [v for _, v in p.meta]
    ts_raw = by_tag.get("時間") or (values[3] if len(values) >= 4 else "")
    body = "".join(p.body_parts)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return {"author": by_tag.get("作者", "") or (values[0] if values else ""),
            "title": by_tag.get("標題", "") or (values[2] if len(values) >= 3 else ""),
            "ts": parse_article_ts(ts_raw), "body": body, "push": p.push, "boo": p.boo}


# ── 標的抽取 ────────────────────────────────────────────────────────────
def extract_codes(text: str, info: dict[str, str | None],
                  limit: int = SOCIAL_MAX_CODES_PER_ARTICLE) -> list[str]:
    """代號：regex 命中且存在於 info；股名：info 的完整 stock_name（≥2 字）整詞命中
    （同一位置有較長股名命中時，較短的前綴名不算——避免「中華」吃掉「中華電」）。
    依首次出現位置排序，最多 limit 個。"""
    if not text:
        return []
    hits: dict[str, int] = {}
    for m in CODE_RE.finditer(text):
        c = m.group(0)
        if c in info and c not in hits:
            hits[c] = m.start()
    name_hits: list[tuple[int, int, str]] = []  # (pos, len, code)
    for code, name in info.items():
        if not name or len(name) < 2:
            continue
        pos = text.find(name)
        if pos >= 0:
            name_hits.append((pos, len(name), code))
    name_hits.sort(key=lambda x: (x[0], -x[1]))
    covered: list[tuple[int, int]] = []
    for pos, ln, code in name_hits:
        if any(a <= pos < b and pos + ln <= b and (b - a) > ln for a, b in covered):
            continue
        covered.append((pos, pos + ln))
        if code not in hits or pos < hits[code]:
            hits[code] = pos
    ordered = sorted(hits.items(), key=lambda kv: kv[1])
    return [c for c, _ in ordered][:limit]


def load_stock_info(path: str | None, day8: str, session, finmind_token: str) -> dict[str, str | None] | None:
    """{code: name}（限 twse／tpex）。順序：--stock-info → 當日快取 → FinMind（成功即寫快取）。
    全部落空回 None（呼叫端只認代號、name 為 null）。"""
    if path:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        return _info_from_rows(j) if isinstance(j, list) else {str(k): v for k, v in j.items()}
    cache = os.path.join(CACHE_DIR, f"social_info_{day8}.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    if not finmind_token:
        warn("FINMIND_TOKEN 缺且無當日快取：只認代號、stocks[].name 為 null")
        return None
    try:
        r = session.get(FINMIND_URL, params={"dataset": "TaiwanStockInfo", "token": finmind_token}, timeout=30)
        r.raise_for_status()
        info = _info_from_rows(r.json().get("data", []))
    except Exception as e:
        warn(f"TaiwanStockInfo 取得失敗（{mask_secret(e, finmind_token)}）：只認代號、name 為 null")
        return None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        for old in glob.glob(os.path.join(CACHE_DIR, "social_info_*.json")):
            if not old.endswith(f"{day8}.json"):
                os.remove(old)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
    except Exception as e:
        print(f"  快取寫入失敗（不影響本班）：{mask_secret(e, finmind_token)}", flush=True)
    return info


def _info_from_rows(rows: list) -> dict[str, str | None]:
    info: dict[str, str | None] = {}
    for row in rows:
        sid = str(row.get("stock_id", "") or "")
        if not sid or sid in info:
            continue
        if row.get("type") not in ("twse", "tpex"):
            continue
        info[sid] = row.get("stock_name") or None
    return info


# ── 情緒分類（Batches → 同步回退）──────────────────────────────────────
def _anth_headers(key: str) -> dict:
    return {"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}


def msg_params(model: str, user_msg: str) -> dict:
    return {"model": model, "max_tokens": LLM_MAX_TOKENS, "system": SOCIAL_SYS,
            "messages": [{"role": "user", "content": user_msg}]}


def user_prompt(title: str, body: str, codes: list[str]) -> str:
    return (f"標題：{title}\n代號清單：{json.dumps(codes, ensure_ascii=False)}\n"
            f"正文節錄：\n{(body or '')[:SOCIAL_BODY_CHARS]}")


def parse_sent(text: str, codes: list[str]) -> dict[str, str] | None:
    """模型輸出 → {code: pos|neg|neu}；不是合法 JSON 物件回 None；缺的代號或不在三類 → 不收
    （呼叫端補 unk）。"""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(j, dict):
        return None
    out = {}
    for c in codes:
        v = j.get(c)
        if isinstance(v, str) and v.strip().lower() in SENT_LABELS:
            out[c] = v.strip().lower()
    return out


def _message_text_usage(msg: dict) -> tuple[str, dict]:
    text = "\n".join(b.get("text", "") for b in (msg.get("content") or []) if b.get("type") == "text")
    u = msg.get("usage") or {}
    return text, {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0)}


def call_batch(reqs: dict[str, str], key: str, session, deadline_sec: int, model: str = SOCIAL_MODEL,
               sleep=time.sleep, clock=time.monotonic, poll_sec: float = SOCIAL_BATCH_POLL_SEC) -> dict:
    """reqs = {custom_id: user_msg}。回 {custom_id: (text, usage) 或 None}；None＝該筆需同步回退。
    提交失敗／逾時 cancel／結果下載失敗 → 全部 None。結果 JSONL 亂序，一律依 custom_id 對回。"""
    none_all = {cid: None for cid in reqs}
    if deadline_sec <= 0 or not reqs:
        return none_all
    try:
        r = session.post(URL_BATCHES, headers=_anth_headers(key),
                         json={"requests": [{"custom_id": cid, "params": msg_params(model, um)}
                                            for cid, um in reqs.items()]}, timeout=(15, 120))
        j = r.json()
        if not r.ok or j.get("type") == "error":
            raise RuntimeError((j.get("error") or {}).get("message") or f"HTTP {r.status_code}")
        bid, status = j["id"], j.get("processing_status")
        print(f"  batch 已提交 {bid}（{len(reqs)} 筆，期限 {deadline_sec // 60} 分）", flush=True)
    except Exception as e:
        print(f"  batch 提交失敗（{mask_secret(e, key)}）→ 全數同步回退", flush=True)
        return none_all
    t0 = clock()
    results_url = None
    while status != "ended":
        if clock() - t0 > deadline_sec:
            print(f"  batch 超過期限仍未 ended（{status}）→ cancel 並全數同步回退", flush=True)
            try:
                session.post(f"{URL_BATCHES}/{bid}/cancel", headers=_anth_headers(key), timeout=(15, 60))
            except Exception:
                pass
            return none_all
        sleep(poll_sec)
        try:
            jj = session.get(f"{URL_BATCHES}/{bid}", headers=_anth_headers(key), timeout=(15, 60)).json()
            status = jj.get("processing_status") or status
            results_url = jj.get("results_url") or results_url
        except Exception as e:
            print(f"  batch 輪詢暫時失敗（{mask_secret(e, key)}），續等", flush=True)
    try:
        rr = session.get(results_url or f"{URL_BATCHES}/{bid}/results", headers=_anth_headers(key), timeout=(15, 300))
        rr.raise_for_status()
        out = dict(none_all)
        for line in rr.text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid, res = row.get("custom_id"), row.get("result") or {}
            if cid not in out:
                continue
            if res.get("type") == "succeeded":
                out[cid] = _message_text_usage(res.get("message") or {})
            else:
                print(f"  batch {cid} 結果 {res.get('type')} → 該筆同步回退", flush=True)
        print(f"  batch 完成（{int(clock() - t0)}s，{sum(1 for v in out.values() if v)}/{len(out)} 筆成功）", flush=True)
        return out
    except Exception as e:
        print(f"  batch 結果下載失敗（{mask_secret(e, key)}）→ 全數同步回退", flush=True)
        return none_all


def call_sync(user_msg: str, key: str, session, model: str = SOCIAL_MODEL, sleep=time.sleep) -> tuple[str, dict] | None:
    """單筆同步，失敗退避一次；兩次皆敗回 None。"""
    for attempt in (1, 2):
        try:
            r = session.post(URL_MESSAGES, headers=_anth_headers(key), json=msg_params(model, user_msg), timeout=(15, 120))
            j = r.json()
            if not r.ok or j.get("type") == "error":
                raise RuntimeError((j.get("error") or {}).get("message") or f"HTTP {r.status_code}")
            return _message_text_usage(j)
        except Exception as e:
            print(f"  sync 第{attempt}次失敗：{mask_secret(e, key)}", flush=True)
            if attempt == 1:
                sleep(LLM_RETRY_SLEEP)
    return None


def classify(articles: list[dict], bodies: dict[str, str], key: str, session, model: str = SOCIAL_MODEL,
             deadline_sec: int = SOCIAL_BATCH_DEADLINE_SEC, sleep=time.sleep, clock=time.monotonic,
             poll_sec: float = SOCIAL_BATCH_POLL_SEC, sync_budget_sec: float = SOCIAL_SYNC_BUDGET_SEC) -> dict:
    """就地把 sent 寫進每篇（有代號者）；回 llm 區塊。via：全 batch＝batch、全 sync＝sync、混＝mixed。
    同步回退累計超過 sync_budget_sec（從第一次同步呼叫起算）即停止：沒拿到任何結果的篇數計入 failed_n、
    代號記 unk，並印 ::warning::。"""
    todo = [a for a in articles if a["codes"]]
    llm = {"model": model, "prompt_ver": SOCIAL_PROMPT_VER, "via": None, "skipped": None,
           "classified_n": 0, "failed_n": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}
    if not todo:
        llm["skipped"] = "no-articles"
        return llm
    reqs = {a["aid"]: user_prompt(a["title"], bodies.get(a["aid"], ""), a["codes"]) for a in todo}
    got = call_batch(reqs, key, session, deadline_sec, model=model, sleep=sleep, clock=clock, poll_sec=poll_sec)
    n_batch = n_sync = 0
    sync_t0: float | None = None
    budget_hit = 0

    def sync_allowed() -> bool:
        nonlocal sync_t0
        if sync_t0 is None:
            sync_t0 = clock()
            return True
        return clock() - sync_t0 <= sync_budget_sec

    for a in todo:
        res = got.get(a["aid"])
        path = "batch"
        if res is None:
            if sync_allowed():
                res = call_sync(reqs[a["aid"]], key, session, model=model, sleep=sleep)
            else:
                budget_hit += 1
            path = "sync"
        if res is None:
            llm["failed_n"] += 1
            a["sent"] = {c: "unk" for c in a["codes"]}
            continue
        text, usage = res
        llm["usage"]["input_tokens"] += usage["input_tokens"]
        llm["usage"]["output_tokens"] += usage["output_tokens"]
        parsed = parse_sent(text, a["codes"])
        if parsed is None:
            # 回應不是合法 JSON：再試一次（同步，仍受預算約束），仍不合法 → 該篇全 unk
            res2 = call_sync(reqs[a["aid"]], key, session, model=model, sleep=sleep) if sync_allowed() else None
            path = "sync" if path == "sync" else "mixed"
            if res2 is not None:
                text2, usage2 = res2
                llm["usage"]["input_tokens"] += usage2["input_tokens"]
                llm["usage"]["output_tokens"] += usage2["output_tokens"]
                parsed = parse_sent(text2, a["codes"])
            parsed = parsed or {}
        a["sent"] = {c: parsed.get(c, "unk") for c in a["codes"]}
        llm["classified_n"] += 1
        if path == "batch":
            n_batch += 1
        elif path == "sync":
            n_sync += 1
        else:
            n_batch += 1
            n_sync += 1
    if budget_hit:
        warn(f"同步回退超過預算 {int(sync_budget_sec)} 秒，{budget_hit} 篇未分類（計入 llm.failed_n、代號記 unk）")
    if n_batch and n_sync:
        llm["via"] = "mixed"
    elif n_batch:
        llm["via"] = "batch"
    elif n_sync:
        llm["via"] = "sync"
    return llm


# ── 聚合與產物 ──────────────────────────────────────────────────────────
def aggregate(articles: list[dict], info: dict[str, str | None] | None) -> dict:
    """stocks{code: {name, n, push, boo, pos, neg, neu, unk}}；pos+neg+neu+unk == n 恆成立。"""
    stocks: dict[str, dict] = {}
    for a in articles:
        for c in a["codes"]:
            s = stocks.setdefault(c, {"name": (info or {}).get(c), "n": 0, "push": 0, "boo": 0,
                                      "pos": 0, "neg": 0, "neu": 0, "unk": 0})
            s["n"] += 1
            s["push"] += a["push"]
            s["boo"] += a["boo"]
            lab = (a.get("sent") or {}).get(c) if a.get("sent") else None
            s[lab if lab in SENT_LABELS else "unk"] += 1
    return dict(sorted(stocks.items(), key=lambda kv: (-kv[1]["n"], kv[0])))


def teardown_info(target: date) -> dict:
    since = date.fromisoformat(SOCIAL_SAMPLE_SINCE)
    age = (target - since).days
    return {"since": SOCIAL_SAMPLE_SINCE, "age_days": age, "due": age >= SOCIAL_TEARDOWN_DUE_DAYS}


def write_outputs(out: dict, out_dir: str = OUT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{out['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    idx_path = os.path.join(out_dir, "index.json")
    idx = {"schema": 1, "days": []}
    if os.path.exists(idx_path):
        try:
            with open(idx_path, encoding="utf-8") as f:
                idx = json.load(f)
        except Exception:
            pass
    entry = {"date": out["date"], "articles_n": out["articles_n"], "robots": out["robots"],
             "via": out["llm"]["via"], "classified_n": out["llm"]["classified_n"]}
    if out.get("fixture"):
        entry["fixture"] = True
    days = [d for d in idx.get("days", []) if d.get("date") != out["date"]] + [entry]
    idx = {"schema": 1, "updated_at": out["generated_at"], "days": sorted(days, key=lambda d: d["date"])}
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    return path


# ── 主流程 ──────────────────────────────────────────────────────────────
def collect_day(fetcher: PttFetcher, target: date, info: dict[str, str | None] | None,
                max_pages: int = SOCIAL_MAX_PAGES, max_articles: int = SOCIAL_MAX_ARTICLES) -> dict:
    """翻板首頁 → 挑目標日候選 → 抓文章頁 → 抽代號。回 {articles, bodies, pages_fetched,
    articles_n, articles_fetched_n, failed_n}。bodies 只活在記憶體（H3）。"""
    cands: list[dict] = []
    path = PTT_INDEX_PATH
    pages = 0
    while path and pages < max_pages:
        html = fetcher.get(path)
        if html is None:
            break
        pages += 1
        entries, prev = parse_board(html)
        dated = [(e, md_to_date(e["md"], target)) for e in entries if not e["pinned"]]
        for e, d in dated:
            if d == target and not is_skippable(e):
                cands.append(e)
        known = [d for _, d in dated if d is not None]
        if known and all(d < target for d in known):
            break
        path = prev
    # 同一 aid 只留一筆
    seen = set()
    uniq = []
    for e in cands:
        m = AID_RE.search(e["href"] or "")
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        e["aid"] = m.group(1)
        uniq.append(e)
    uniq.sort(key=lambda e: -nrec_to_int(e["nrec"]))
    picked = uniq[:max_articles]
    articles, bodies = [], {}
    failed = 0
    for e in picked:
        html = fetcher.get(f"/bbs/{PTT_BOARD}/{e['aid']}.html")
        art = parse_article(html) if html else None
        if art is None:
            failed += 1
            continue
        ts = art["ts"]
        if ts is None or ts.date() != target:
            # 台北日歸屬以文章頁為準：板首頁月/日與文章頁不一致（跨日／改標）→ 不算該日
            continue
        title = art["title"] or e["title"]
        codes = extract_codes(f"{title}\n{art['body']}", info or {})
        articles.append({"aid": e["aid"], "title": title, "author": art["author"] or e["author"],
                         "ts": ts.strftime("%Y-%m-%d %H:%M"), "push": art["push"], "boo": art["boo"],
                         "cat": title_cat(title), "url": f"{PTT_BASE}/bbs/{PTT_BOARD}/{e['aid']}.html",
                         "codes": codes, "sent": None})
        bodies[e["aid"]] = art["body"]
    articles.sort(key=lambda a: a["ts"])
    return {"articles": articles, "bodies": bodies, "pages_fetched": pages, "articles_n": len(picked),
            "articles_fetched_n": len(picked) - failed, "failed_n": failed}


def _finalize(out: dict, fetcher: PttFetcher, info, t0: float) -> dict:
    out["stocks"] = aggregate(out["articles"], info)
    out["requests_n"] = fetcher.requests_n
    out["fetch_errors"] = [dict(e) for e in fetcher.errors]
    out["elapsed_s"] = round(time.monotonic() - t0, 1)
    out["generated_at"] = taipei_now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return out


def build(target: date, fetcher: PttFetcher, info: dict[str, str | None] | None, key: str,
          llm_session=None, no_llm: bool = False, fixture: bool = False, out_dir: str | None = None,
          **llm_kw) -> dict:
    """out_dir 非 None 時：進 LLM 之前先落一版 sent 全 null／llm.skipped="pending" 的產物，分類完成
    再由呼叫端覆寫——LLM 路徑掛住或 job 被砍時，該日聲量資料已在磁碟上（H7 精神）。"""
    t0 = time.monotonic()
    rb = parse_robots(fetcher.get(PTT_ROBOTS_PATH))
    robots = rb["verdict"]
    robots_detail = dict(fetcher.last_detail)
    print(f"robots fetch: verdict={robots} status={robots_detail['status']} err={robots_detail['err']} "
          f"final_url={robots_detail['final_url']} attempts={robots_detail['attempts']}", flush=True)
    if rb["crawl_delay"]:
        fetcher.min_interval = max(fetcher.min_interval, rb["crawl_delay"])
    out = {"schema": 1, "date": target.isoformat(), "generated_at": None, "source": "ptt-stock",
           "robots": robots, "robots_crawl_delay": rb["crawl_delay"], "robots_detail": robots_detail,
           "fetch_errors": [], "pages_fetched": 0, "articles_n": 0, "articles_fetched_n": 0, "failed_n": 0,
           "requests_n": 0, "elapsed_s": 0.0,
           "llm": {"model": SOCIAL_MODEL, "prompt_ver": SOCIAL_PROMPT_VER, "via": None, "skipped": None,
                   "classified_n": 0, "failed_n": 0, "usage": {"input_tokens": 0, "output_tokens": 0}},
           "stocks": {}, "articles": [], "teardown": teardown_info(target)}
    if fixture:
        out["fixture"] = True
    if robots != "allow":
        warn(f"robots.txt 判定 {robots}：本班不抓任何文章（H5）")
        out["llm"]["skipped"] = "robots"
    else:
        col = collect_day(fetcher, target, info)
        bodies = col.pop("bodies")
        out.update(col)
        if fetcher.exhausted:
            warn(f"對 ptt.cc 請求達上限 {fetcher.max_requests}，後續文章未抓（計入 failed_n）")
        if no_llm:
            out["llm"]["skipped"] = "no-llm"
        elif not key:
            warn("ANTHROPIC_API_KEY 缺：情緒欄整批 null、llm.skipped=no-key（聲量資料照寫）")
            out["llm"]["skipped"] = "no-key"
        else:
            if out_dir:
                out["llm"]["skipped"] = "pending"
                write_outputs(_finalize(out, fetcher, info, t0), out_dir)
                out["llm"]["skipped"] = None
            try:
                out["llm"] = classify(out["articles"], bodies, key, llm_session or requests.Session(), **llm_kw)
            except Exception as e:
                # 分類整段炸掉也不讓聲量資料陪葬：記 skipped="error"、全部 unk，exit 仍為 0
                warn(f"情緒分類例外（{mask_secret(e, key)}）：llm.skipped=error、代號全記 unk")
                for a in out["articles"]:
                    a["sent"] = {c: "unk" for c in a["codes"]} if a["codes"] else None
                out["llm"].update({"skipped": "error", "failed_n": sum(1 for a in out["articles"] if a["codes"])})
        bodies.clear()  # H3：正文用完即丟
    _finalize(out, fetcher, info, t0)
    if out["teardown"]["due"]:
        warn(f"社群量測班已滿 {out['teardown']['age_days']} 日，該回頭看樣本決定收窄／移除")
    return out


EXIT_LLM_ERROR = 2


def llm_exit_code(out: dict) -> int:
    """情緒分類失效 → EXIT_LLM_ERROR，否則 0。失效＝classify() 例外（skipped="error"）或有目標篇
    （有代號的文章 >0）卻全數 failed；預期跳過（no-llm／no-key／robots／no-articles／pending）不算失效。"""
    llm = out.get("llm") or {}
    if llm.get("skipped") == "error":
        return EXIT_LLM_ERROR
    target_n = sum(1 for a in out.get("articles", []) if a.get("codes"))
    if llm.get("skipped") is None and target_n > 0 and llm.get("failed_n", 0) >= target_n:
        return EXIT_LLM_ERROR
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="社群聲量量測班（階段一）：PTT Stock 板 → data/social/")
    ap.add_argument("--date", help="目標台北日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--from-fixture", metavar="DIR", help="以目錄內 HTML 取代網路（測試／沙箱）")
    ap.add_argument("--no-llm", action="store_true", help="跳過情緒分類")
    ap.add_argument("--stock-info", metavar="PATH", help="本機股票清單 JSON（取代 FinMind）")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args(argv)

    target = date.fromisoformat(args.date) if args.date else taipei_today()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    fm_token = os.environ.get("FINMIND_TOKEN", "")
    http = requests.Session()
    info = load_stock_info(args.stock_info, target.strftime("%Y%m%d"), http, fm_token)
    fetcher = PttFetcher(session=http, fixture_dir=args.from_fixture,
                         min_interval=0 if args.from_fixture else SOCIAL_MIN_INTERVAL,
                         sleep=(lambda s: None) if args.from_fixture else time.sleep)
    out = build(target, fetcher, info, key, llm_session=http, no_llm=args.no_llm,
                fixture=bool(args.from_fixture), out_dir=args.out_dir)
    path = write_outputs(out, args.out_dir)
    print(f"寫出 {path}：robots={out['robots']} pages={out['pages_fetched']} articles={out['articles_n']} "
          f"fetched={out['articles_fetched_n']} failed={out['failed_n']} requests={out['requests_n']} "
          f"stocks={len(out['stocks'])} llm.via={out['llm']['via']} skipped={out['llm']['skipped']} "
          f"usage={out['llm']['usage']}", flush=True)
    rc = llm_exit_code(out)
    if rc:
        print(f"::error::情緒分類失效（skipped={out['llm']['skipped']} failed_n={out['llm']['failed_n']}），"
              f"聲量產物已寫出、exit {rc}（延後紅燈由 workflow 末段發）", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
