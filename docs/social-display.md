# 社群聲量 tab — 驗收條件

> **2026-09-28 搬到 postmkt**（tab id `social`，驗收條件 `postmkt/docs/move-radar-social.md`）；本站 `social` tab 已移除。本文件仍是該 tab 的畫面規格正本，下文的「本 repo」「`index.html`」路徑指的是搬家前的原站實作。

**寫於** 2026-09-28，動手前定稿。**目標專案**：`/home/user/taiwan-stock-news`，分支 `claude/investment-site-optimization-nac77h`。
**設計示意**：https://claude.ai/artifact/ETr466nR5q9ox7eVY1Jsft （第二分頁）。
**使用者裁決（2026-09-28，兩次，均由使用者本人在主對話直接選定）**：
1. `docs/social-phase1.md` H1「不顯示」解除，社群聲量上畫面。
2. **取代下方 S2／S3 與原「只做描述性顯示」**：態度用紅綠（偏多紅、偏空綠）、偏多比可排序、偏多／偏空排行、每檔標籤
   （偏多比 ≥65% 偏多、≤35% 偏空、判斷篇數 <3 樣本不足）、**情緒＋千張大戶週變化雙確認**的規則式買賣建議、附參考價位。見 §1b。
- **鐵律 8 的紀錄**：標籤、排行與建議屬會影響候選排序的訊號，**目前沒有回測依據**；使用者知情後決定上線。畫面一律標「AI 研判，未經回測，非保證」。
- **誠實原則的明確例外**：CLAUDE.md「狀態詞用中性色、不寫該買該賣」**只在本 tab 例外**，其他 tab 不變。
**同學會**：使用者 2026-09-28 裁決「視同已取得授權」（CMoney 端授權並未取得，條款風險由使用者承擔）。本批仍只有 PTT；同學會管線另案。

## 0. 硬約束
| # | 約束 | 驗法 |
|---|---|---|
| S1 | 只改 `index.html`＋文件＋測試；`build_social.py`、`build_news.py`、`news_curation.py`、workflow、`data/**` 零改動 | `git diff --stat` |
| S2 | 情緒分布**全用中性色**（不用紅綠）；偏多／偏空只以文字與灰階深淺區分 | 讀 CSS |
| S3 | 預設排序＝篇數；情緒欄**不可排序** | 讀碼＋Playwright |
| S4 | tab 頂部固定免責卡：「聲量與作者態度為 AI 判讀的現況描述，非買賣訊號，無回測依據。」並顯示 `llm.model`／`llm.prompt_ver` | 畫面 |
| S5 | 外來字串（標題、股名、作者）一律 `esc()`；文章連結只接受 `https://www.ptt.cc/` 開頭，其餘不出連結 | 注入測試 |
| S6 | 只讀同源 `data/social/*.json`，CSP 不改；首屏不載（切到本 tab 才載） | 首屏請求清單不變 |
| S7 | **排除 `fixture: true` 的日子**（index.json 與單日檔皆檢查） | 測試 |
| S8 | 不改 hash 既有 key 語意；新增 tab 值 `social` 與 key `sd`（日期，`YYYY-MM-DD` 且須在 index.json 非 fixture 清單內），白名單＋非法值靜默退回 | 測試 |

## 1. 功能
新 tab `social`「社群聲量」，排在「個股追蹤」之後（成為第 6 個 tab）。
1. **日期選單**：讀 `data/social/index.json`，列最近 7 個非 fixture 日子，預設最新一天。
2. **狀態列**（逐字依產物欄位）：資料日｜抓取 `pages_fetched` 頁、`articles_n` 篇（失敗 `failed_n`）｜robots 判定｜情緒分類 `llm.via`（`null` 時依 `llm.skipped` 顯示中文：`no-key`＝未設金鑰、`no-llm`＝未分類、`robots`＝站方限制未抓、`no-articles`＝無文章、`pending`＝分類中、`error`＝分類失敗）。產物 `generated_at` 早於資料日隔日 12:00 以外的正常情況不另判級（不新增紅黃綠）。
3. **空狀態**：`articles_n == 0` 時顯示原因（依 robots 與 skipped），不顯示空表。
4. **個股表**（`stocks`）：代號｜股名（`name` 為 null 顯代號）｜篇數 `n`｜推 `push`｜噓 `boo`｜作者態度分布（單條堆疊：偏多／中性／偏空／無法判斷，灰階）｜偏多比 `pos/(pos+neg+neu)`（分母 0 顯「—」）。預設篇數降序、次鍵代號。表格包 `<div class="tblwrap">`。
5. **點個股展開**：列出該檔的文章（標題＋時間＋推噓＋該篇對此檔的態度），來源為 `articles[]` 中 `codes` 含該代號者；連結依 S5。
6. **來源維度**：頂部來源 chip 由產物 `source` 值決定；只有一種來源時只顯示一個不可點的 chip「PTT Stock」。

## 1b. 紅綠、排行、建議、價位（使用者裁決，2026-09-28）
- 常數（`index.html` social-pure 區）：`SOCIAL_RANK_MIN_N`＝3、`SOCIAL_BULL_TH`＝0.65、`SOCIAL_BEAR_TH`＝0.35，旁註「未經回測」。
- 標籤 `socialTag`：判斷篇數（偏多＋中性＋偏空）<3 → 樣本不足；偏多比 ≥0.65 → 偏多；≤0.35 → 偏空；其餘分歧。
- 排序 `socialSortRows(stocks,key,llm)`：`n`／`push`／`boo`／`ratio`，ratio 的 null 排最後，次鍵篇數降序、代號升序。
  排行 `socialRankRows`：只納入判斷篇數 ≥3；偏多排行依偏多比降序、偏空排行升序。
- 建議 `socialAdvice(tag, diagRow)`：偏多＋`hdw>0` → 買進；偏空＋`hdw<0` → 賣出；其餘（含未涵蓋、持平、分歧、樣本不足）→ 觀望。
  展開區固定三行：「依據（事實）」「研判（推論）」＋（買進／賣出時）「參考價位」。
- 參考價位 `socialLevels`：由 postmkt 素材庫「現價距 MA%」反推 MA＝c÷(1＋dist/100)（`src/build_diag.py` 的 ma5/20/60 定義）。
  買進：進場參考＝min(現價, MA20)、停損參考＝MA60（僅在低於現價時列）；賣出：減碼參考＝max(現價, MA20)。標「近似值，未經回測」。
- 籌碼來源：同源 `../postmkt/data/diag/diag.json`，本 tab 首次開啟載一次；讀不到 → 建議一律觀望並顯示原因。只涵蓋素材庫約 1,200 檔。
- 免責卡改為「AI 研判，未經回測，非保證。」＋模型／prompt 版本＋門檻說明（取代 S4 原文「非買賣訊號」，因頁面已有買賣建議，舊句自相矛盾）。

## 2. 驗收清單（fresh-context 驗收者逐條，綁 commit）
- [ ] B1 S1–S8 逐條
- [ ] B2 `python -m pytest tests/ -q` 全綠（既有 83 個不得變紅）
- [ ] B3 新增前端純函式測試（node 或以 Python 抽 JS 皆可，沿用本 repo 慣例）：偏多比分母 0、`sent=null` 全計「無法判斷」、fixture 排除、hash `sd` 白名單、連結白名單
- [ ] B4 Playwright（本機 http.server；以 `tests/fixtures/social` 產一份非 fixture 的樣本檔放到暫存副本或以 `page.route` 餵）：6 個 tab 逐一點擊 console 零 error；日期切換、個股展開、空狀態、`skipped` 各值文案正確
- [ ] B5 375／390／1280 三寬度 `scrollWidth <= innerWidth`（本 repo 既有慣例）
- [ ] B6 注入測試：標題／股名／作者／URL 各放 `<img onerror>` 與 `javascript:` 樣本，零觸發
- [ ] B7 CLAUDE.md（5→6 tab、hash 節、社群段改「顯示已上線、描述性」）、README、`docs/social-phase1.md` H1 附註使用者裁決，三處同步
