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


def test_sort_default_by_count_and_sentiment_not_sortable(R):
    assert R["sortDefault"] == ["1101", "2330", "2317", "2454"]   # 篇數降序、次鍵代號
    assert R["sortPush"] == ["2317", "2330", "2454", "1101"]
    # 態度相關鍵不在允許清單 → 退回篇數排序
    assert R["sortPos"] == R["sortDefault"] and R["sortRatio"] == R["sortDefault"]


# ---- 靜態守門 ----

def _css_block() -> str:
    s = _html()
    return "\n".join(l for l in s.splitlines() if ".soc-" in l and "{" in l)


def test_sentiment_css_is_grayscale():
    """S2：soc-* 規則裡的顏色一律灰階（R=G=B 附近），不得出現紅綠系或 .up/.down。"""
    css = _css_block()
    assert css, "找不到 .soc-* CSS"
    for hx in re.findall(r"#([0-9a-fA-F]{6})\b", css):
        r, g, b = (int(hx[i:i + 2], 16) for i in (0, 2, 4))
        assert max(r, g, b) - min(r, g, b) <= 24, f"#{hx} 非灰階"
    s = _html()
    i, j = s.index("// ==== 社群聲量"), s.index("// ---------- URL 狀態")
    block = s[i:j]
    assert 'class="up' not in block and 'class="down' not in block
    assert "#ef5b5b" not in block and "#2fbf71" not in block


def test_disclaimer_and_tab_order():
    s = _html()
    assert "聲量與作者態度為 AI 判讀的現況描述，非買賣訊號，無回測依據。" in s
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
