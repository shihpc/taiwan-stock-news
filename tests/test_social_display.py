#!/usr/bin/env python3
# tests/test_social_display.py
# 「社群聲量」tab 前端純函式守門（驗收條件正本 docs/social-display.md §2 B3）。
# 做法：從 index.html 抽出 `social-pure:begin` ～ `social-pure:end` 之間的純函式，
# 丟進 node 沙箱跑一組案例、回傳 JSON，由 Python 斷言（免網路、免 token）。
# 另以靜態檢查守 S2（灰階、無紅綠）／S3（情緒欄不可排序）／S6（首屏不載）／S8（hash 白名單）。
#
# 用法：python -m pytest tests/ -q

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INDEX = os.path.join(ROOT, "index.html")
BEGIN = "// ---- social-pure:begin"
END = "// ---- social-pure:end ----"


def _html() -> str:
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def _pure_js() -> str:
    s = _html()
    assert s.count(BEGIN) == 1 and s.count(END) == 1, "social-pure 標記須各唯一命中"
    i = s.index(BEGIN)
    return s[i:s.index(END, i)]


HARNESS = r"""
const R = {};
R.ratio0 = socialPosRatio({pos:0, neg:0, neu:0, unk:5});
R.ratioOnlyUnk = socialPosRatio(socialDist({n:3, pos:0, neg:0, neu:0, unk:3}, {via:"batch"}));
R.ratio = socialPosRatio({pos:2, neg:1, neu:1, unk:9});
R.distSkipped = socialDist({n:4, pos:1, neg:1, neu:1, unk:1}, {via:null, skipped:"no-key"});
R.distMissing = socialDist({n:5, pos:2}, {via:"sync"});
R.distNormal = socialDist({n:4, pos:1, neg:1, neu:1, unk:1}, {via:"batch"});
R.sentNull = socialArticleSent({sent:null}, "2330");
R.sentMissing = socialArticleSent({sent:{"2317":"pos"}}, "2330");
R.sentBad = socialArticleSent({sent:{"2330":"bullish"}}, "2330");
R.sentOk = socialArticleSent({sent:{"2330":"neg"}}, "2330");
R.days = socialDays({days:[
  {date:"2026-09-26", fixture:true}, {date:"2026-09-28"}, {date:"2026-09-27"},
  {date:"bad"}, {date:"2026-09-28"}, null, {date:"2026-09-20", fixture:"yes"},
  {date:"2026-09-01"},{date:"2026-09-02"},{date:"2026-09-03"},{date:"2026-09-04"},{date:"2026-09-05"},{date:"2026-09-06"}]});
R.daysEmpty = socialDays(null);
R.fixFile = socialIsFixture({fixture:true});
R.fixNo = socialIsFixture({date:"2026-09-28"});
const D = ["2026-09-28", "2026-09-27"];
R.sd = [socialPickSd("2026-09-27", D), socialPickSd("2026-09-26", D), socialPickSd("2026-9-27", D),
        socialPickSd("<img>", D), socialPickSd("", D), socialPickSd(null, D), socialPickSd(" 2026-09-28 ", D)];
R.url = [socialSafeUrl("https://www.ptt.cc/bbs/Stock/M.1.A.1.html"),
         socialSafeUrl("javascript:alert(1)"), socialSafeUrl("http://www.ptt.cc/bbs/x.html"),
         socialSafeUrl("https://www.ptt.cc.evil.com/x"), socialSafeUrl("https://www.ptt.cc/\" onmouseover=\"x"),
         socialSafeUrl("https://www.ptt.cc/a b"), socialSafeUrl(null), socialSafeUrl(" https://www.ptt.cc/x"),
         socialSafeUrl("https://evil.com/?https://www.ptt.cc/")];
const st = {"2454":{n:2,push:1,boo:9,pos:0,neg:2,neu:0},"2330":{n:5,push:3,boo:0,pos:5,neg:0,neu:0},
            "2317":{n:2,push:7,boo:0,pos:0,neg:0,neu:2},"1101":{n:5,push:0,boo:0,pos:0,neg:5,neu:0}};
R.sortDefault = socialSortRows(st).map(r => r.code);
R.sortPush = socialSortRows(st, "push").map(r => r.code);
R.sortPos = socialSortRows(st, "pos").map(r => r.code);
R.sortRatio = socialSortRows(st, "ratio").map(r => r.code);
// 標籤門檻邊界（使用者 2026-09-28 裁決：0.65／0.35、判斷篇數 ≥3）
R.tags = [socialTag({pos:13,neu:7,neg:0,unk:0}), socialTag({pos:12,neu:8,neg:0,unk:0}),
          socialTag({pos:7,neu:13,neg:0,unk:0}), socialTag({pos:8,neu:12,neg:0,unk:0}),
          socialTag({pos:2,neu:0,neg:0,unk:9}), socialTag({pos:3,neu:0,neg:0,unk:0}),
          socialTag({pos:0,neu:0,neg:0,unk:5})];
const st2 = {"A1":{n:6,pos:4,neg:1,neu:1},"B2":{n:9,pos:1,neg:6,neu:2},"C3":{n:2,pos:2,neg:0,neu:0},
             "D4":{n:4,pos:3,neg:0,neu:1},"E5":{n:5,pos:3,neg:0,neu:1}};
const base = socialSortRows(st2, "n");
R.rankBull = socialRankRows(base, "bull").map(r => r.code);
R.rankBear = socialRankRows(base, "bear").map(r => r.code);
R.rankAll = socialRankRows(base, "all").map(r => r.code);
R.adv = [socialAdvice("bull",{hdw:0.5}), socialAdvice("bull",{hdw:-0.5}), socialAdvice("bull",{hdw:0}),
         socialAdvice("bull",null), socialAdvice("bear",{hdw:-1}), socialAdvice("bear",{hdw:1}),
         socialAdvice("mixed",{hdw:2}), socialAdvice("few",{hdw:-2}), socialAdvice("bull",{hdw:null}),
         socialAdvice("bull",{hdw:"x"})];
R.ma = [socialMaPrice(110, 10), socialMaPrice(90, -10), socialMaPrice(100, null), socialMaPrice(0, 5), socialMaPrice(100, -100)];
R.lvBuy = socialLevels("buy", {c:110, ma20:10, ma60:22.2222222});
R.lvBuyNoStop = socialLevels("buy", {c:90, ma20:-10, ma60:-10});
R.lvSell = socialLevels("sell", {c:90, ma20:-10});
R.lvHold = socialLevels("hold", {c:100, ma20:1});
R.lvNoDiag = socialLevels("buy", null);
process.stdout.write(JSON.stringify(R));
"""


@pytest.fixture(scope="module")
def R():
    node = shutil.which("node")
    if not node:
        pytest.fail("找不到 node：本測試需要 node 執行 index.html 抽出的純函式")
    out = subprocess.run([node, "-e", _pure_js() + "\n" + HARNESS],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_pos_ratio_zero_denominator(R):
    assert R["ratio0"] is None
    assert R["ratioOnlyUnk"] is None
    assert R["ratio"] == pytest.approx(0.5)


def test_sent_null_counts_as_unknown(R):
    assert R["distSkipped"] == {"pos": 0, "neu": 0, "neg": 0, "unk": 4}
    assert R["distMissing"] == {"pos": 2, "neu": 0, "neg": 0, "unk": 3}
    assert R["distNormal"] == {"pos": 1, "neu": 1, "neg": 1, "unk": 1}
    assert R["sentNull"] == "unk" and R["sentMissing"] == "unk" and R["sentBad"] == "unk"
    assert R["sentOk"] == "neg"


def test_fixture_excluded(R):
    assert "2026-09-26" not in R["days"] and "2026-09-20" not in R["days"]
    assert R["days"][:2] == ["2026-09-28", "2026-09-27"]
    assert len(R["days"]) == 7 and len(set(R["days"])) == 7
    assert R["days"] == sorted(R["days"], reverse=True)
    assert R["daysEmpty"] == []
    assert R["fixFile"] is True and R["fixNo"] is False


def test_hash_sd_whitelist(R):
    assert R["sd"] == ["2026-09-27", None, None, None, None, None, "2026-09-28"]


def test_link_whitelist(R):
    assert R["url"][0] == "https://www.ptt.cc/bbs/Stock/M.1.A.1.html"
    assert R["url"][1:] == [None] * 8


def test_sort_default_count_and_ratio_sortable(R):
    """使用者 2026-09-28 裁決：偏多比可排序（null 排最後、次鍵篇數降序再代號）；其他態度鍵仍不接受。"""
    assert R["sortDefault"] == ["1101", "2330", "2317", "2454"]   # 篇數降序、次鍵代號
    assert R["sortPush"] == ["2317", "2330", "2454", "1101"]
    assert R["sortRatio"] == ["2330", "1101", "2317", "2454"]
    assert R["sortPos"] == R["sortDefault"]


def test_tag_thresholds(R):
    # 13/20=0.65 偏多、12/20=0.60 分歧、7/20=0.35 偏空、8/20=0.40 分歧、判斷 2 篇 → 樣本不足、剛好 3 篇 → 可判、全無法判斷 → 樣本不足
    assert R["tags"] == ["bull", "mixed", "bear", "mixed", "few", "bull", "few"]


def test_rank_filter_and_order(R):
    # 偏多比：A1 4/6、B2 1/9、D4 3/4、E5 3/4（D4／E5 同分 → 篇數降序 E5 在前）；C3 判斷篇數 2 < 3 不進排行
    assert R["rankBull"] == ["E5", "D4", "A1", "B2"]
    assert R["rankBear"] == ["B2", "A1", "E5", "D4"]
    assert R["rankAll"] == ["B2", "A1", "E5", "D4", "C3"]


def test_advice_double_confirmation(R):
    assert R["adv"] == ["buy", "hold", "hold", "hold", "sell", "hold", "hold", "hold", "hold", "hold"]


def test_reference_levels(R):
    assert R["ma"][0] == pytest.approx(100) and R["ma"][1] == pytest.approx(100)
    assert R["ma"][2:] == [None, None, None]
    assert R["lvBuy"]["entry"] == pytest.approx(100) and R["lvBuy"]["stop"] == pytest.approx(90, abs=1e-4)
    assert R["lvBuyNoStop"]["entry"] == pytest.approx(90) and R["lvBuyNoStop"]["stop"] is None
    assert R["lvSell"]["exit"] == pytest.approx(100)
    assert R["lvHold"] is None and R["lvNoDiag"] is None


# ---- 靜態守門 ----

def _css_block() -> str:
    s = _html()
    return "\n".join(l for l in s.splitlines() if ".soc-" in l and "{" in l)


def test_sentiment_css_red_green():
    """使用者 2026-09-28 裁決改紅綠（台股慣例）：偏多＝紅 #ef5b5b、偏空＝綠 #2fbf71。"""
    css = _css_block()
    assert re.search(r"\.soc-pos\{background:#ef5b5b\}", css)
    assert re.search(r"\.soc-neg\{background:#2fbf71\}", css)


def test_disclaimer_and_tab_order():
    s = _html()
    assert "AI 研判，未經回測，非保證。" in s
    assert "非買賣訊號" not in s[s.index("// ==== 社群聲量"):s.index("// ---------- URL 狀態")]
    # 本 tab 副標題（TAB_SUBS.social，在 URL 狀態段之後）也不得再寫舊句，須帶免責措辭
    m = re.search(r'^\s*social: "([^"]*)",', s, re.M)
    assert m, "找不到 TAB_SUBS.social"
    assert "非買賣訊號" not in m.group(1) and "只作現況描述" not in m.group(1)
    assert "AI 研判，未經回測，非保證" in m.group(1)
    tabs = re.findall(r'<div class="tab[^"]*" data-tab="([a-z]+)"', s)
    assert tabs == ["news", "daily", "morning", "insight", "track", "social"]
    assert re.search(r'const HASH_TABS = \[[^\]]*"social"\]', s)


def test_social_not_loaded_on_first_screen():
    """S6：data/social 只在 loadSocial() 內抓，且 loadSocial 只由 showTab('social') 呼叫。"""
    s = _html()
    assert len(re.findall(r'fetch\w*\("data/social/', s)) == 2
    # 定義 1 處；呼叫只有 showTab 1 處＋日期切換 1 處
    assert s.count("async function loadSocial()") == 1
    assert s.count("loadSocial();") == 2
    assert 'if (tab === "social") loadSocial();' in s
    # CSP 未新增來源
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', s).group(1)
    assert "connect-src 'self' https://raw.githubusercontent.com https://taiwan-flow-v2.shihpc.workers.dev https://api.anthropic.com https://api.github.com" in csp
