# 台股新聞（taiwan-stock-news）

股市雷達 Hub 的子專案。每日抓取 FinMind `TaiwanStockNews`，套**來源白名單**過濾
（排除論壇、內容農場、綜合社會媒體），輸出 `news.json` 給前端 dashboard 呈現。

線上：https://shihpc.github.io/taiwan-stock-news/

## 架構
```
build_news.py        ← 每日 pipeline：讀股票池 → 抓新聞 → 過濾 → 產 news.json
news_curation.py     ← 來源白名單過濾邏輯（純函式，附白名單/正規化對照）
index.html           ← dashboard 前端（讀 news.json）
news.json            ← 每日由 GitHub Actions 產出並 commit
.github/workflows/build-news.yml
```

## 資料流
1. 股票池：讀 `taiwan-stock-radar` 公開的 `scan_app.csv`（唯讀，不回寫），
   取投信連買(`trust_days>=2`) ∪ 外資連買(`foreign_days>=2`)、排除 ETF。
2. 逐檔抓近 N 個交易日（預設 3）的 `TaiwanStockNews`（單日單請求，含 550/hr 節流）。
3. `news_curation.curate_news` 白名單過濾：
   - source 正規化 → 核心白名單 → CMoney「股市爆料同學會」論壇次級過濾
   - Yahoo 跨來源標題去重 →（核心 0 篇時）fallback pool → 仍 0 則留空
4. 輸出 `news.json`，前端 `index.html` 讀取呈現。

## 首次設定（一次性）
1. **Secrets → Actions** 新增 `FINMIND_TOKEN`（FinMind API token）。
2. **Settings → Pages** 來源選 `Deploy from a branch` → `main` / `/ (root)`。
3. 到 **Actions → 每日建置台股新聞 → Run workflow** 手動跑一次產生 `news.json`。

## 手動更新
```bash
FINMIND_TOKEN=xxx python build_news.py --lookback 3
```

白名單規格與盤點依據見 `taiwan-stock-radar` 的 `output/news_source_findings.md`。
