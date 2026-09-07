# tests/test_curation.py
# news_curation 文章識別守門（2026-09-07 批次三；免 token 免網路）：
#   canonical_url：追蹤參數移除（utm_*／fbclid／from…）、文章識別參數保留（MoneyDJ ?a=、?id=）、
#   host 大小寫、fragment、尾斜線、query 排序；article_id：同篇不同寫法同碼、不同文章不同碼、
#   長度與字元集穩定、空值回空。
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from news_curation import article_id, canonical_url  # noqa: E402

BASE = "https://udn.com/news/story/7240/9736532"


@pytest.mark.parametrize("url", [
    BASE,
    BASE + "?from=udn-ch1_breaknews",
    BASE + "?utm_source=line&utm_medium=share&utm_campaign=x",
    BASE + "?fbclid=IwAR123&gclid=abc",
    BASE + "/",
    BASE + "#top",
    BASE + "/?ref=home#section-2",
    "HTTPS://UDN.COM/news/story/7240/9736532",
])
def test_canonical_strips_tracking_case_fragment_slash(url):
    assert canonical_url(url) == BASE


def test_canonical_keeps_article_identifying_params():
    mdj = "https://www.moneydj.com/kmdj/news/newsviewer.aspx?a=abc123"
    assert canonical_url(mdj + "&utm_source=x") == mdj
    assert canonical_url("https://x.tw/n?id=42&Type=1&Page=2") == "https://x.tw/n?Page=2&Type=1&id=42"
    # 不在移除清單的 key 一律保留（連 google news 的 oc 也保留）
    assert canonical_url("https://news.google.com/rss/articles/CBM?oc=5") == "https://news.google.com/rss/articles/CBM?oc=5"


def test_canonical_query_order_insensitive():
    assert canonical_url("https://x.tw/n?b=2&a=1") == canonical_url("https://x.tw/n?a=1&b=2")


def test_canonical_root_and_empty():
    assert canonical_url("https://x.tw/") == "https://x.tw"
    assert canonical_url("https://x.tw") == "https://x.tw"
    assert canonical_url("") == ""
    assert canonical_url(None) == ""
    assert canonical_url("  not-a-url ") == "not-a-url"       # 無 host：原樣 strip，不炸


def test_canonical_does_not_touch_path_case():
    # 只有 scheme／host 轉小寫，path 大小寫是有意義的
    assert canonical_url("https://X.tw/News/Story/ABC") == "https://x.tw/News/Story/ABC"


def test_article_id_stable_across_variants():
    ids = {article_id(u) for u in (BASE, BASE + "?from=x", BASE + "/#top", "HTTPS://UDN.com/news/story/7240/9736532")}
    assert len(ids) == 1
    aid = ids.pop()
    assert len(aid) == 12 and all(c in "0123456789abcdef" for c in aid)


def test_article_id_differs_for_different_articles():
    assert article_id(BASE) != article_id("https://udn.com/news/story/7240/9736533")
    a = "https://www.moneydj.com/kmdj/news/newsviewer.aspx?a="
    assert article_id(a + "one") != article_id(a + "two")


def test_article_id_empty_for_no_link():
    assert article_id("") == ""
    assert article_id(None) == ""


def test_article_id_deterministic_value():
    # 釘住演算法（sha1 前 12 碼）：改雜湊或截長會讓既有 news.json 的 aid 全變，屬破壞性變更
    import hashlib
    assert article_id(BASE) == hashlib.sha1(BASE.encode("utf-8")).hexdigest()[:12]
