# 社群聲量 tab — 驗收條件

**寫於** 2026-09-28，動手前定稿。**目標專案**：`/home/user/taiwan-stock-news`，分支 `claude/investment-site-optimization-nac77h`。
**設計示意**：https://claude.ai/artifact/ETr466nR5q9ox7eVY1Jsft （第二分頁）。
**使用者裁決（2026-09-28）**：`docs/social-phase1.md` H1「不顯示」改為「只做描述性顯示」。依鐵律 8：情緒屬 AI 判讀，須標明；**不依情緒排序、不產生任何偏多／偏空訊號或建議**；本站誠實原則「狀態詞用中性色」適用。
**同學會**：未授權、**不抓取**。來源維度做成資料驅動，本批只會有 PTT。

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

## 2. 驗收清單（fresh-context 驗收者逐條，綁 commit）
- [ ] B1 S1–S8 逐條
- [ ] B2 `python -m pytest tests/ -q` 全綠（既有 83 個不得變紅）
- [ ] B3 新增前端純函式測試（node 或以 Python 抽 JS 皆可，沿用本 repo 慣例）：偏多比分母 0、`sent=null` 全計「無法判斷」、fixture 排除、hash `sd` 白名單、連結白名單
- [ ] B4 Playwright（本機 http.server；以 `tests/fixtures/social` 產一份非 fixture 的樣本檔放到暫存副本或以 `page.route` 餵）：6 個 tab 逐一點擊 console 零 error；日期切換、個股展開、空狀態、`skipped` 各值文案正確
- [ ] B5 375／390／1280 三寬度 `scrollWidth <= innerWidth`（本 repo 既有慣例）
- [ ] B6 注入測試：標題／股名／作者／URL 各放 `<img onerror>` 與 `javascript:` 樣本，零觸發
- [ ] B7 CLAUDE.md（5→6 tab、hash 節、社群段改「顯示已上線、描述性」）、README、`docs/social-phase1.md` H1 附註使用者裁決，三處同步
