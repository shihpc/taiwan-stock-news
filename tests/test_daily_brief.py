#!/usr/bin/env python3
# tests/test_daily_brief.py
# 每日晨報產製物守門：對 repo 內現行 daily-brief.html 與 daily-brief-card.json
# 驗 CLAUDE.md「每日晨報產製規範」的四條硬規範（純 Python、免網路、免 token）。
#   a. card.json 的 quote：≤120 字、≤3 句（全形句讀 。；！？）、單行純文字、必要欄位齊全
#   b. daily-brief.html 當期正文（<section class="archive"> 之前）漢字 ≤5,000
#   c. 歷史存檔恰 7 期（archive 區內的 <details> 數）
#   d. </body> 前的 postMessage 自動高度 script 存在，且訊息契約與 index.html 接收端一致
#      （接收端只認 {t:"brief-h", h:<數字>}；見 index.html 的 "brief-h" 監聽器）
# 另以字串 fixture（不落檔）驗證四條規範在超標時確實會紅。
#
# 用法：python -m pytest tests/ -q

from __future__ import annotations

import json
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BRIEF_HTML = os.path.join(ROOT, "daily-brief.html")
CARD_JSON = os.path.join(ROOT, "daily-brief-card.json")

QUOTE_MAX_CHARS = 120
QUOTE_MAX_SENTENCES = 3
BODY_MAX_CJK = 5000
ARCHIVE_EDITIONS = 7
CARD_REQUIRED_KEYS = ("schema", "date", "edition", "generated_at", "quote")

ARCHIVE_MARK = '<section class="archive">'
# CJK 統一表意文字（基本區＋擴充 A＋相容區）；不計標點、數字、英文
CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
SENTENCE_SPLIT_RE = re.compile(r"[。；！？]")
HTML_TAG_RE = re.compile(r"<[^>]+>")


# ---------- 檢查函式（回傳問題清單；空＝通過） ----------

def check_quote(card: dict) -> list[str]:
    problems = []
    for k in CARD_REQUIRED_KEYS:
        if k not in card:
            problems.append(f"card.json 缺必要欄位 {k!r}")
    q = card.get("quote")
    if not isinstance(q, str) or not q.strip():
        problems.append("quote 必須是非空字串")
        return problems
    q = q.strip()
    if "\n" in q or "\r" in q:
        problems.append("quote 必須單行（含換行）")
    if HTML_TAG_RE.search(q):
        problems.append("quote 必須是純文字（含 HTML 標籤）")
    if len(q) > QUOTE_MAX_CHARS:
        problems.append(f"quote {len(q)} 字 > {QUOTE_MAX_CHARS}")
    sentences = [s for s in SENTENCE_SPLIT_RE.split(q) if s.strip()]
    if len(sentences) > QUOTE_MAX_SENTENCES:
        problems.append(f"quote {len(sentences)} 句 > {QUOTE_MAX_SENTENCES}")
    return problems


def split_brief(html: str) -> tuple[str, str]:
    """回 (當期正文, 歷史存檔區)。當期正文＝<body 起到 archive 標記前。"""
    i = html.find(ARCHIVE_MARK)
    assert i >= 0, f"daily-brief.html 找不到歷史存檔分界標記 {ARCHIVE_MARK}"
    b = html.find("<body")
    assert b >= 0 and b < i, "daily-brief.html 找不到 <body> 或它位於存檔區之後"
    tail = html[i:]
    end = tail.find("</section>")
    assert end >= 0, "歷史存檔 <section> 沒有閉合"
    return html[b:i], tail[: end + len("</section>")]


def count_cjk(text: str) -> int:
    return len(CJK_RE.findall(text))


def check_body_budget(html: str) -> list[str]:
    body, _ = split_brief(html)
    n = count_cjk(body)
    return [f"當期正文 {n} 漢字 > {BODY_MAX_CJK}"] if n > BODY_MAX_CJK else []


def count_archive_editions(html: str) -> int:
    _, archive = split_brief(html)
    return len(re.findall(r"<details(?=[\s>])", archive))


def check_archive(html: str) -> list[str]:
    n = count_archive_editions(html)
    return [] if n == ARCHIVE_EDITIONS else [f"歷史存檔 {n} 期 ≠ {ARCHIVE_EDITIONS}"]


# 契約（與 index.html 接收端一致）：postMessage 第一參數為物件字面量，含 t:"brief-h"
# 與 h:<數值>（數字或 *Height 屬性）；第二參數 targetOrigin 為同 origin 值或 "*"。
# 現行產製物用 "*"——接收端以 e.origin 守門、訊息只帶高度數字，"*" 不放寬安全邊界；
# 規範要求該 script「原樣保留」，故 "*" 與 location.origin 兩者皆接受。
POST_RE = re.compile(
    r"postMessage\(\s*\{(?P<obj>[^{}]*)\}\s*,\s*"
    r"(?P<origin>\"\*\"|'\*'|(?:window\.)?location\.origin)\s*\)"
)
T_RE = re.compile(r"""(?:^|[,{\s])(?:t|"t"|'t')\s*:\s*(?:"brief-h"|'brief-h')""")
H_RE = re.compile(r"""(?:^|[,{\s])(?:h|"h"|'h')\s*:\s*(?:\d+(?:\.\d+)?|[\w.]*(?:scrollHeight|offsetHeight))""")


def check_post_message(html: str) -> list[str]:
    body_end = html.rfind("</body>")
    if body_end < 0:
        return ["找不到 </body>"]
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html[:body_end], flags=re.S)
    if not scripts:
        return ["</body> 前沒有任何 <script>"]
    last = scripts[-1]
    m = POST_RE.search(last)
    if not m:
        return ["</body> 前最後一個 <script> 沒有 postMessage({...}, location.origin|\"*\")"]
    obj = m.group("obj")
    problems = []
    if not T_RE.search(obj):
        problems.append('postMessage 物件缺 t:"brief-h"')
    if not H_RE.search(obj):
        problems.append("postMessage 物件缺 h:<數字>")
    return problems


# ---------- 現行產製物 ----------

@pytest.fixture(scope="module")
def brief_html() -> str:
    with open(BRIEF_HTML, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def card() -> dict:
    with open(CARD_JSON, encoding="utf-8") as f:
        return json.load(f)


def test_card_quote(card):
    assert check_quote(card) == []


def test_body_cjk_budget(brief_html):
    assert check_body_budget(brief_html) == []


def test_archive_seven_editions(brief_html):
    assert check_archive(brief_html) == []


def test_post_message_contract(brief_html):
    assert check_post_message(brief_html) == []


# ---------- fixture：超標必須紅 ----------

def _brief(body_cjk: int, editions: int, script: str) -> str:
    body = "<p>" + ("漢" * body_cjk) + "</p>"
    archive = "".join(
        f"<details><summary>2026-09-0{i} · 第 {i} 期</summary><ul><li>x</li></ul></details>"
        for i in range(editions)
    )
    return (
        "<!doctype html><html><head><meta charset=utf-8></head><body><div>"
        + body
        + f'{ARCHIVE_MARK}<h2>歷史存檔</h2>{archive}</section></div>'
        + script
        + "</body></html>"
    )


GOOD_SCRIPT = (
    "<script>(function(){var post=function(){"
    'parent.postMessage({t:"brief-h",h:document.documentElement.scrollHeight},"*");};'
    'addEventListener("load",post);})();</script>'
)


def test_fixture_quote_130_chars_fails():
    bad = {k: "x" for k in CARD_REQUIRED_KEYS}
    bad["quote"] = "字" * 130
    assert any("字 >" in p for p in check_quote(bad))


def test_fixture_quote_ok_passes():
    ok = {k: "x" for k in CARD_REQUIRED_KEYS}
    ok["quote"] = "第一句。第二句；第三句！"
    assert check_quote(ok) == []


def test_fixture_quote_four_sentences_fails():
    bad = {k: "x" for k in CARD_REQUIRED_KEYS}
    bad["quote"] = "一。二。三。四。"
    assert any("句 >" in p for p in check_quote(bad))


def test_fixture_quote_multiline_or_html_fails():
    bad = {k: "x" for k in CARD_REQUIRED_KEYS}
    bad["quote"] = "第一行\n第二行"
    assert any("單行" in p for p in check_quote(bad))
    bad["quote"] = "含 <b>標籤</b>"
    assert any("純文字" in p for p in check_quote(bad))


def test_fixture_quote_missing_key_fails():
    bad = {"quote": "好。"}
    assert any("缺必要欄位 'date'" in p for p in check_quote(bad))


def test_fixture_body_5100_cjk_fails():
    html = _brief(5100, ARCHIVE_EDITIONS, GOOD_SCRIPT)
    assert check_body_budget(html) != []
    assert check_body_budget(_brief(5000, ARCHIVE_EDITIONS, GOOD_SCRIPT)) == []


def test_fixture_archive_8_editions_fails():
    assert check_archive(_brief(10, 8, GOOD_SCRIPT)) != []
    assert check_archive(_brief(10, 6, GOOD_SCRIPT)) != []
    assert check_archive(_brief(10, 7, GOOD_SCRIPT)) == []


def test_fixture_missing_post_message_fails():
    assert check_post_message(_brief(10, 7, "")) != []
    # 有 script 但訊息型別錯／欄位錯／targetOrigin 不合契約，也要紅
    wrong_t = GOOD_SCRIPT.replace('t:"brief-h"', 't:"height"')
    assert check_post_message(_brief(10, 7, wrong_t)) != []
    wrong_h = GOOD_SCRIPT.replace("h:document.documentElement.scrollHeight", 'h:"tall"')
    assert check_post_message(_brief(10, 7, wrong_h)) != []
    wrong_origin = GOOD_SCRIPT.replace('},"*")', '},"https://evil.example")')
    assert check_post_message(_brief(10, 7, wrong_origin)) != []
    same_origin = GOOD_SCRIPT.replace('},"*")', "},location.origin)")
    assert check_post_message(_brief(10, 7, same_origin)) == []
    assert check_post_message(_brief(10, 7, GOOD_SCRIPT)) == []
