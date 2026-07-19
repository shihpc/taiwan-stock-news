# 台股新聞（taiwan-stock-news）

股市雷達 Hub 的子專案。每日抓取 FinMind `TaiwanStockNews`，套**來源白名單**過濾
（排除論壇、內容農場、綜合社會媒體），輸出 `news.json` 給前端 dashboard 呈現。

線上：https://shihpc.github.io/taiwan-stock-news/

`news.json` 的 `generated_at`（台北 +08:00）／`trading_days`／新聞 `date` 等日期欄語意，
以及跨站產出檔的日期對照，見 postmkt repo 的 `docs/date-semantics.md`（五 repo 統一對照表）。

## 個股追蹤（第一批：基本面，2026-07-19）

`index.html` 第四個 tab「個股追蹤」（純前端插入式，`#trackView`＋`loadTrack`/`renderTrack` 等
`trk*` 函式群，三站同步函式零改動）。**兩組來源、視覺區分**：

- **自選股**：localStorage key `news_watchlist`（代號陣列），輸入框可增/刪（格式驗證，存在性由
  Worker 回傳判定，查不到顯示提示不炸）。
- **持股診斷股**：讀同 origin 的 `pm_holdings`（postmkt 存，格式 `[{c,sh,...}]`），**本站唯讀**
  （不可增刪，管理權留 postmkt），標註「來自持股診斷」。

點選任一檔 → 下方三區塊（基本面）：① 每日新聞追蹤（讀本站 `news.json`，僅 ~150 檔法人熱門股，
不在池內誠實降級標註）② 每月營收追蹤（最新月＋MoM＋YoY＋近 12 月柱狀＋近 14 日公布標「新公布」，
億元）③ 每季財報追蹤（EPS/營收/毛利率/營益率/淨利率/稅後淨利 × 本季/上季QoQ/去年同季YoY＋近 8 季
迷你趨勢，紅增綠減）。

**資料架構**：月營收/季財報走 v2 Worker `/fundamentals?ids=`（`USW_WORKER` 常數，即時代理 FinMind，
任何台股皆可）；前端**絕不碰 FinMind token**。QoQ/YoY 由 Worker 算並附回。詳端點規格見
taiwan-flow-live-v2 `PROJECT_SUMMARY.md`「快速接手」。**後續批次**：籌碼面／技術面（技術面採描述性、
留待回測驗證，比照 postmkt 持股診斷）另分期，tab 地基已可直接加區塊。

## 架構
```
build_news.py        ← 每日 pipeline：讀股票池 → 抓新聞 → 過濾 → 產 news.json
news_curation.py     ← 來源白名單過濾邏輯（純函式，附白名單/正規化對照）
index.html           ← dashboard 前端（讀 news.json）
news.json            ← 每日由 GitHub Actions 產出並 commit
.github/workflows/build-news.yml
```

## 資料流
1. 股票池：`build_pool_from_finmind()` 自建（FinMind `TaiwanStockInfo` 取名稱/產業＋
   近 3 個交易日投信/外資買賣超，排除 ETF）。原依賴 `taiwan-stock-radar` 的 `scan_app.csv`
   已於 2026-07-10 隨該 repo 刪除而改為自建（`--pool-csv` 參數仍可指定外部 CSV 覆蓋）。
2. 逐檔抓 `TaiwanStockNews`（單日單請求，含 550/hr 節流）；日期範圍為涵蓋近 N 個
   交易日（預設 3）的**日曆日**區間（含夾雜與尾隨的週末/假日），排程每天三班
   06:30/15:00/22:37 台北跑（含週末，2026-07-12 起；晚班 2026-07-14 由 22:30 改
   22:37 錯開 GitHub cron 壅塞），週六日發布的新聞也收得到。
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

## 快速接手（2026-07-12）

- 晨報「昨日資金流向」段（2026-07-18 第八期）：跨 repo 讀 taiwan-flow-live-v2
  `data/daysummary/latest.json`（`flowSumHtml()`，插在籌碼卡與美股段之間；讀不到/解析失敗
  整段隱藏不擋晨報，卡片標「資料日 YYYY-MM-DD」）。上游為該 repo `daysummary.yml`（平日
  14:05 台北產出，口徑同其收盤總結卡）。
- `index.html` 現有 3 個 tab：新聞、晨報（跨 repo 讀 taiwan-flow-live-v2 `data/morning.json`）、
  **摘要分析**（2026-07-12 新增）。摘要分析為前端直呼 Claude，框架與 postmkt 逐字同源
  （callClaude/mdToHtml/Opus 4.8-Sonnet 5 模型切換）；localStorage key
  `anthropic_key`/`insight_model` 與 postmkt、taiwan-flow-live-v2 同 origin 共用（設一次三站通用）。
- insightGatherContext 彙整：大盤財金焦點新聞（impact=market 去重前12）、個股新聞熱度前15、
  晨報籌碼（gap/法人/投信連買賣/主動ETF；MORNING 未載入會先 `await loadMorning()` 再判空略過）、
  隔夜美股（各族群前3）。SYS prompt 為「新聞×籌碼共振」語境。
- 個股外連＋雲端儲存（2026-07-12）：insight 渲染中個股代號自動變連結，外開 Yahoo 技術分析頁
  （`linkifyStocks(html, knownSet)`，三站逐字一致、改動需三站同步）。分析結果自動存
  **postmkt repo** `data/analyses/insight-news-YYYYMMDD.json`（當日陣列、單日上限10筆、
  保留近3日），寫入用 localStorage `gh_token`（GitHub Fine-grained PAT，三站同 origin 共用、
  未設靜默跳過）；tab 內「雲端歷史（近3日）」免 token 列本站檔、點擊展開（raw CDN 約 5 分快取）。
  PAT 建法與維護細節見 postmkt `README.md`。
- 已知觀察項（輕微、未修）：晨報籌碼段資料日標 `MORNING.generated_at`，
  但法人數字實為前一交易日（晨報本質即彙整昨日籌碼），更嚴謹可改標 `chips.inst.date`。
