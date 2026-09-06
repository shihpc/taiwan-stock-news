# CLAUDE.md — taiwan-stock-news 接手速覽

<!-- CANON:BEGIN v1 -->
<!-- 唯一事實來源＝shihpc/claude-harness 的 CANON.md。以下區塊在五個 repo 的 CLAUDE.md 頂端
     有 byte-identical 逐字副本，由各 repo 的 .github/workflows/canon.yml 守門（比對 sha256）。
     改動流程：先改 claude-harness/CANON.md → 跑 tools/sync_canon.py 同步五份 → 更新守門 hash。
     不要只改單一 repo，CI 會擋下來。 -->

## 通用工作鐵律（五個 repo 逐字相同，勿單獨修改）

1. **機密**：token／金鑰只存在不受版控的本機設定或受控 secrets（`.env`／Actions secret／
   `wrangler secret`），絕不寫進會 commit 的檔案、log 或對話輸出。commit 前掃 staged 內容，
   **只回報檔名／行號／類型、不把可疑字串原文印出來**；`sk-ant-`／`ghp_`／`eyJ` 是線索不是全集。
2. **指揮官不下場**：掃 repo、通讀 >300 行的檔、一次讀 >3 個檔、查網頁研究、批次改檔、
   驗收改過的東西——這六類一律派 subagent，主對話只收結論＋`檔案:行號`；但 subagent 回報
   **不等於事實**，主對話為核對結論可直接查原始證據。雲端 session 的 subagent 派工（含第 3 條
   驗收）已獲常備授權，需要時直接派，不需逐次詢問。
3. **先寫驗收條件再動手**：動手前先寫下目標專案完整路徑＋怎樣算完成＋怎麼驗。修改者先自測，
   再派 fresh-context subagent 驗收——**改東西的 agent（含主對話自己）不得擔任驗收者**。
   驗收要綁**確切 commit／產物版本**；驗收後又改動，受影響部分要重驗。
4. **不確定不亂說**：陳述事實（尤其技術細節、數字、外部服務的限制與行為）要嘛附佐證（官方
   文件、實測、`檔案:行號`），要嘛明說「這點我不確定，需要查證」，不可憑印象當確定講。區分
   「已驗證事實」與「推測」，推測要標明；**`檔案:行號` 只證明程式這樣寫，不證明線上這樣跑**。
5. **一次只做一件事**：聚焦一個明確目標，完成該目標必要的修改、測試與整合；不擅自加入無關
   重構或延伸功能。範圍外問題簡短記錄、不自行擴張任務。
6. **完成的定義**：驗收條件逐條打勾＋fresh-context subagent 驗過＋產物在使用者拿得到的位置，
   並明示已完成與未完成；**可執行的東西沒實跑過不算完成**（純文件交付以內容與結構檢查為準）。
   涉及部署者另需 push＋部署 workflow 成功＋在**實際服務的位置**驗證本次變更（線上頁面／API／
   資料時戳）——**raw URL 只證明原始碼進了 repo，不證明線上跑的是該版本**，200 也不等於功能正確。
7. **push 前**：先確認目前分支與推送目標，`git fetch` 後檢查遠端是否領先，非空必須先看內容
   （訊息／時間戳／diff）。一般 push → rebase 整合（本專案既定政策），嚴禁直接覆蓋；force push
   前若遠端領先的 commit 是真實新工作 → 停下來問，且一律用 `--force-with-lease=<ref>:<預期 SHA>`；
   授權「這次 force push」不等於授權蓋掉遠端所有領先 commit。
8. **新指標／訊號若會影響投資方向、候選排序、進出場或風險判定，先問有沒有回測依據**，沒有就
   先驗證再上線；純描述性顯示（欄位、日期、圖示）只需驗算式正確。市場內容可做情境判讀與多空
   因素分析，可研判市場與大眾情緒對該數值或新聞的可能反應，並可提供具體個股／標的的買賣建議
   與進出點位；以上均須附依據、區分事實與推論，並標明屬 AI 研判而非保證。
9. **語言**：對話與文件用繁體中文；程式碼註解可中文，identifier 用英文；外部原文、API 名稱、
   指令與錯誤訊息保留原樣。

> 判準細則、派工模板、教訓簿見 `shihpc/claude-harness`（private）。雲端 session 需 add_repo 才讀得到。
<!-- CANON:END v1 -->

「新聞晨報」站：抓 FinMind `TaiwanStockNews` → 來源白名單過濾 → 產 `news.json`。
線上 https://shihpc.github.io/taiwan-stock-news/ 。5 個 tab：新聞／每日晨報／昨日市場
（原「晨報」tab 更名）／摘要分析／個股追蹤（`index.html` tabs 區）。「每日晨報」tab 以
iframe 載入同 repo 的 `daily-brief.html`——該檔由雲端排程 session 每日台北 07:30 產製並
push 到 main，**常態勿手動編輯**（手改當期內容會被隔日產製覆蓋）；**改版例外**——版式／
規範本身的變更本來就得手改該檔（前例 `f09d406`／`b41e5f7`／`0bb433f`），這種手改**必須同步
更新下方「每日晨報產製規範」節**，否則隔日產製會照舊規範寫回去、改版被沖掉。
純靜態前端（單檔 `index.html`）＋ Python 管線。

## 佈局

- `build_news.py`：主管線，流程註解在 :6-15 —— 自建股票池 → 市值權重 → 算窗 →
  增量規劃（:355-386）→ 逐檔逐日抓 → `news_curation.py` 白名單過濾 → 依股分組去重 → 寫 `news.json`
  - 韌性（2026-09-06）：模組級 `_session()`、交易日 `_fetch_trading_dates` memoize、
    `fetch_news_one` 退避重試一次、股票池／市值權重／交易日同日快取 `data/cache/pool_<YYYYMMDD>.json`
    （`--full` 重建覆寫、`--no-cache` 繞過；不進 git，CI 走 `build-news.yml` actions/cache）。
    細節見 README「管線韌性」節
- `news_curation.py`：來源白名單／標題正規化／去重（Python 端事實來源）
- `index.html`、`news.json`、`data/`、`tests/`
  - `index.html` 資料抓取一律走 `fetchFresh()`（`cache:"no-cache"` 條件式驗證）或 `bust()`，
    回退開關 `CACHE_BUST`（script 頂端「快取策略」註解；細節與 CDN `max-age` 殘留見 README
    「前端快取策略」節，2026-09-06）
  - 手機適配（2026-09-06）：`.mtable` 全表 `nowrap`，**新增表格一律包 `<div class="tblwrap">`**
    （`overflow-x:auto`，同 `daily-brief.html` 的 `.poswrap`）；`.tabs` 已 `flex-wrap`，
    `@media (max-width:640px)` 收縮外距與字級。驗收慣例：Playwright 375／390／1280 三寬度
    5 tab（含個股追蹤三分頁）`scrollWidth <= innerWidth`
- `.github/workflows/`：`build-news.yml` ＋ `test.yml` ＋ `canon.yml`
  （後者只守 CLAUDE.md 頂端的 CANON 區塊，不碰資料管線）

## 股票池自建

`build_pool_from_finmind()`（`build_news.py:138-184`）：`TaiwanStockInfo` 限 twse/tpex、
代號開頭為數字、排除 ETF；以近 3 交易日法人資料算投信/外資連買天數，
取 `trust_days>=2 or foreign_days>=2`，再取前 `--max-pool`（預設 150）。
**原依賴已刪除的 taiwan-stock-radar `scan_app.csv`，2026-07-10 改為自建**，不再依賴外部 repo。

## 不可破壞的約定

1. **備援 cron 排 21:37 不排 22:37**（理由原樣寫在 `build-news.yml:18-27`）：主力觸發是
   外部 Worker dispatch（每日含週末台北 06:07–22:07 每小時 :07），此 repo 只留一班備援
   cron `37 13 * * *`＝台北 21:37。GitHub cron 常態延遲 1~2 小時，若排 22:37 延遲後會
   **跨台北午夜把 `generated_at` 滾成隔日、覆蓋 Worker 22:07 已寫好的當日晚班，
   害下游 pm 彙總閘門永久 skip**（2026-07-16 實際事故）。改排程前先讀那段註解。
2. **前端絕不碰 FinMind token**：個股追蹤三分頁全打
   `USW_WORKER = https://taiwan-flow-v2.shihpc.workers.dev`（`index.html:530`）——
   `/fundamentals?ids=`（批次）、`/chips?id=`（逐股 lazy）、`/technical?id=`（7 指標全在
   Worker 端算）。token 由 Worker 持有。
3. **三站同步函式**：`index.html:641` 明寫「postmkt / taiwan-flow-live-v2 /
   taiwan-stock-news，修改請三站同步」（另見 :729-730、:846），與 postmkt CLAUDE.md
   第 2 條是同一組約定。**第五組（2026-08-27 起）**：費用估算 `insightCostText`／
   `INSIGHT_PRICES`／`USD_TWD`（`index.html:858-880`）亦為三站逐字副本。
   另有一組**四站同步但非逐字**的 `loadSiteVer()`＋footer `#siteVer`（`index.html:197`、
   `:1508`）：本站 sessionStorage key `news_site_ver`、時間走內嵌 `toLocaleString("sv-SE")`
   （postmkt 走 `fmtGenTaipei`），各站打自己 repo 的
   `api.github.com/repos/shihpc/<repo>/commits/main`（免金鑰、限 60 req/hr/IP，失敗靜默隱藏）。
   改行為四站一起改，但不強求逐字。清單正本在 `postmkt/CLAUDE.md` 第 2 條。
4. **誠實原則（專案鐵律，逐字保留，`README.md:71`）**：分頁頂部固定免責卡
   「技術指標為現況描述、非買賣訊號，僅供參考」；狀態詞用中性色、不寫該買該賣、不做預測。

## CSP 與注入面（2026-09-06）

`index.html:11` 的 `<meta http-equiv="Content-Security-Policy">`（比照 postmkt `index.html:10`，另加
`frame-src 'self'` 供晨報 iframe）。**新增資料源時 `connect-src` 要同步加**，否則 fetch 被靜默擋下
（console 出現 `Refused to connect`）。盤點（2026-09-06 grep 實查，行號為當時）：

| 面向 | 現況 | CSP 對應 |
|------|------|---------|
| 內嵌 script | 只有 1 個 `<script>` 區塊（`index.html:222` 起，含四站同步的 `loadSiteVer`） | `script-src 'self' 'unsafe-inline'`（不為 CSP 重構、不搬外部檔） |
| 內嵌事件屬性 | **零**（`on*=` 屬性 grep 無命中；事件全走 `addEventListener`／`el.onclick=` 指派） | — |
| `javascript:`／`<img>`／`<link>`／`@import`／`url()`／`<object>`／`<form>` | 皆無 | `img-src 'self' data:`（預留）、`object-src 'none'`、`base-uri 'none'` |
| `style=` 屬性 | 85 處（卡片內距等） | `style-src 'self' 'unsafe-inline'` |
| iframe | `#dailyFrame` 載同源 `daily-brief.html`；`postMessage` 高度回報有 `e.origin === location.origin` 檢查 | `frame-src 'self'`；**該文件不受本 meta 管轄**（自身無 CSP，由產製 session 產出，外連只有新聞來源 `<a>`，`:459` 一個內嵌 script） |
| fetch 目標 origin | 同源 `news.json`／`daily-brief.html`；`raw.githubusercontent.com`（taiwan-flow-live-v2 morning／us／daysummary、postmkt analyses）；`taiwan-flow-v2.shihpc.workers.dev`（/fundamentals /chips /technical /uswatch /usersync）；`api.anthropic.com`（callClaude）；`api.github.com`（ghSaveAnalysis 寫 postmkt、loadSiteVer） | `connect-src` 白名單恰為此 5 個 |
| 純導覽外連 | Yahoo 技術分析（`linkifyStocks`／新聞連結）、GitHub、Hub；皆 `target="_blank" rel="noopener"` | 不受 CSP 限制（無 `navigate-to`） |

**`innerHTML` 拼字串（16 處）逃逸稽核**：不可信輸入全部過 `esc()`——新聞標題／來源／連結
（`newsItem`）、來源面板（`renderSrcPanel`）、美股表（`usHtml`／`loadUsWatch`）、晨報籌碼名單
（`chipsHtml`）、個股追蹤各表（`trackChip`／`trackNewsHtml`／`trackHoldHtml`／`techState` 等）、雲端
歷史 meta。LLM 輸出走三站同步的 `mdToHtml`（`esc2` 逃 `&<>`，只放進元素內容、不進屬性）＋
`linkifyStocks`（href 只由 regex 命中的數字代號組成）。**未逃逸但可接受**：①`newsItem` 的
`class="badge ${n.impact}"`——值來自自家 `build_news.classify_impact`，值域固定 market/heavy/stock；
②`gapHtml`／`chipsHtml`／`flowSumHtml`／`trackFinHtml` 的數值欄（`g.gap`、`x.div`、`toFixed`）——
來自自家 repo 的 JSON、非字串路徑；③`renderStats` 的 `TDAYS`（自家 `news.json` 的 `YYYY-MM-DD`）。
這三類的信任邊界是「自家管線產出」，若日後改讀第三方 JSON 要補 `esc()`。

## 每日晨報產製規範（產製 session 必讀）

`daily-brief.html`／`daily-brief-card.json` 由雲端排程 session 每晨產製。以下規範
**優先於排程 prompt 中較舊的敘述**——prompt 與本節衝突時，以本節為準。

1. **速讀版硬預算（2026-08-30 第 24 期起，使用者裁定，取代 08-29 前的長文版式）**：
   當期正文（歷史存檔前）**≤5,000 漢字**；每則新聞＝h3 標題(≤30字)＋一句 why(≤50字)＋
   來源 meta，細節收 `<details class="more">`；重點判讀全刊限 3 則（各 ≤150 字）；
   行情數字只在 `table.pos` 完整出現一次；`<b>` 每則至多 1 處；生活四區塊收合於
   `<details class="lifeblock">`。完整結構順序見 `daily-brief.html` 頂端
   「速讀版組稿規範」HTML 註解（與排程 prompt v4 同源）；**`</body>` 前的
   postMessage 自動高度 script 必須原樣保留**（站內 iframe 依它調整高度）。
2. **「今日一句話」（2026-08-30 更新，取代 08-29 分段版）**：≤100 字、至多 2 段，
   名副其實地收束、不重複前文數據。
3. **歷史存檔只保留最近 7 期（近一週，2026-08-30 使用者裁定，取代原 14 期）**。
4. **`daily-brief-card.json` 的 `quote` 欄**＝今日一句話的**濃縮版**：≤120 字、
   至多 3 句、單行純文字——不是網頁版全文照抄（LINE 長圖空間有限，渲染端會依句
   分行，過長會被截斷）。**自動守門（2026-09-06 起）**：`tests/test_daily_brief.py` 驗
   quote 字數／句數／單行純文字／必要欄位，並連帶驗第 1 條正文 ≤5,000 漢字、第 3 條存檔
   恰 7 期、postMessage script 契約；`test.yml` 的 paths 已納入 `daily-brief.html`／
   `daily-brief-card.json`，**產製 session push 後 CI 就會跑這支測試，紅了要回頭改產製物**
   （本機可先 `python -m pytest tests/ -q`）。下游 taiwan-flow-live-v2 Worker 另有防禦層 `export function fxSplitQuote`
   ＋`export const FX_QUOTE_MAX`（360 字）：超標會截到最近句尾並補「（全文見網頁版晨報）」，
   **那是保險絲不是額度**，寫到 360 字仍然違規。

## 跨 repo 依賴

- 讀 taiwan-flow-live-v2 的 `morning.json` / `us.json` / `daysummary/latest.json`
  （讀不到時該整段隱藏）
- 摘要分析結果**寫入 postmkt** `data/analyses/insight-news-YYYYMMDD.json`
- 持股診斷股讀同 origin localStorage `pm_holdings`（postmkt 寫入，本站唯讀）

## 已知坑

1. **FinMind 單日切片是 UTC 日**，台北＝UTC+8，切片 s 涵蓋台北 s 08:00～(s+1) 07:59；
   要完整重抓 R 個台北日必須抓 **R+1** 個切片（`build_news.py:383`），快取邊界
   `cache_cutoff` 必須取 `refresh_slices[1]`＝「被完整覆蓋的最早台北日」（`:385`）。
   取 `[0]` 會漏掉該日 00:00～07:59（那批在前一個切片裡），且**現象無聲**。
2. **抓取失敗不可寫進 `coverage`**（2026-07-27 修）：`fetch_news_one` 回 `(records, ok)`，
   失敗的檔收進 `failed_codes` 並排除在 `coverage.codes` 之外（`:485` 附近）。
   `coverage` 的不變式是「有涵蓋但 stocks 沒出現＝那天真的沒新聞」；若把失敗也記成
   已涵蓋，下一班增量會從快取拿這個「沒有」並一路沿用，**只有 `--full` 沖得掉**。
   全量時代失敗只影響當班（自癒），增量會把它變成沾黏的——改這段前先想清楚。
3. **JS 的 `\W` 是 ASCII-only**（即使加 u 旗標），會把中文當非字元砍掉，與 Python
   `re.UNICODE` 語意不同；改用 `[^\p{L}\p{N}]`（`index.html:326`）。
4. 晨報籌碼段法人數字實為**前一交易日**，原本沒標日期而視覺上繼承 `MORNING.generated_at`
   （建置時間）。2026-07-31 已修：改標 payload 既有的 `chips.inst.date`（`index.html:470`）。
   其他消費者（postmkt、Worker 圖卡）本來就正確解析該欄，本站渲染是唯一漏網的。

## 驗證方式

```bash
python tests/test_incremental.py   # 免 token 免網路，驗「增量輸出 == 全量輸出」六情境
                                   # （含抓取失敗不毒化 coverage、失敗後下一班自動補回）
python -m pytest tests/ -q         # 晨報產製物守門 test_daily_brief.py＋管線韌性 test_pipeline_resilience.py（免 token 免網路）
python -m http.server 8000         # 前端本機驗證，5 個 tab 逐一點擊 console 零 error
```
