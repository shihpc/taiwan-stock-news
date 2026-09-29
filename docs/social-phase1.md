# 社群聲量量測班（階段一）— 驗收條件

**狀態**：階段一＝只收資料、不顯示、不下判準。動機與三階段設計見本檔末「背景」節。
**目標專案**：`/home/user/taiwan-stock-news`，分支 `claude/investment-site-optimization-nac77h`。
**寫於** 2026-09-27，動手前定稿；驗收綁定實作 commit SHA（驗收報告填入）。

## 0. 硬約束（任一違反即不通過）

| # | 約束 | 驗法 |
|---|------|------|
| H1 | ~~**不顯示**~~（**2026-09-28 使用者裁決解除**，上畫面的規格見 `docs/social-display.md`；以下為原條文）：`index.html` 一字不改、`news.json` 格式不改、Worker 不改 | `git diff --stat` 不含 `index.html`／`news.json` |
| H2 | **只做 PTT Stock 板**，不碰 Dcard、不碰其他板 | grep `dcard` 零命中（程式碼） |
| H3 | **不存文章正文**：產物只存標題／作者／時間／推噓數／連結／命中代號／情緒標籤；正文只在記憶體用完即丟 | 產物 schema 檢查＋測試斷言無 `body` 欄 |
| H4 | **抓取節流**：對 ptt.cc 的請求間隔 ≥ `SOCIAL_MIN_INTERVAL`（1.0 秒）、單班總請求 ≤ `SOCIAL_MAX_REQUESTS`（400）、UA 帶專案識別字串 | 測試以假 session 計數＋計時 |
| H5 | **robots 守門**（四值，2026-09-28 依 RFC 9309 §2.3.1 修正）：每班先抓 `https://www.ptt.cc/robots.txt`。200 且 `User-agent: *` 規則擋到 `/`／`/bbs/`／`/bbs/Stock/` → `disallow`、**不抓任何文章**；200 且允許 → `allow`；**HTTP 4xx（含 404）→ `absent`，照抓**（§2.3.1.3「unavailable」＝crawler 可存取任何資源）；5xx／連線例外／逾時 → `unreachable`、不抓（§2.3.1.4 視為全站 disallow）。不抓時仍寫出產物、`articles_n: 0`、`llm.skipped: "robots"`，exit 0 並印 `::warning::` | 測試餵 disallow 文字、404／403／500／503／連線例外 |
| H6 | **金鑰不落地**：`ANTHROPIC_API_KEY`／`FINMIND_TOKEN` 只由環境變數讀、不進 log／產物／例外訊息；例外訊息過遮罩後才印 | grep 產物與測試輸出無 `sk-ant-`／token 字樣 |
| H7 | **缺金鑰不丟資料**：`ANTHROPIC_API_KEY` 缺 → 情緒欄整批 `null`、`llm.skipped="no-key"`，聲量資料照寫、exit 0 | 測試 |
| H8 | **不動既有管線**：`build_news.py`／`news_curation.py`／`build-news.yml` 零改動；Worker dispatch 清單不改（本班只靠 GH cron） | `git diff --stat` |
| H9 | **不下判準**：產物沒有任何「偏多／偏空／建議」欄位，只有計數與分布 | schema 檢查 |

## 1. 交付物

1. `build_social.py`（新檔）：`python build_social.py [--date YYYY-MM-DD] [--from-fixture DIR] [--no-llm] [--stock-info PATH]`
2. `tests/test_social.py` ＋ `tests/fixtures/social/`（PTT 板首頁與文章 HTML 各 ≥2 份、`robots_allow.txt`／`robots_disallow.txt`、`stock_info.json` 小樣本、Batches 回應 JSONL 樣本）
3. `.github/workflows/build-social.yml`：cron `20 15 * * *`（台北 23:20，每日含週末；理由：PTT 討論到晚間才齊，且避開 21:37 的 news 備援班）＋ `workflow_dispatch`；secrets `FINMIND_TOKEN`、`ANTHROPIC_API_KEY`；commit `data/social/**`；push 走 `build-news.yml` 同款 rebase 重試；末步 `./.github/actions/notify-failure`（`if: failure() || cancelled()`、`pipeline: news-social`、job `permissions: issues: write`）；concurrency group `build-social`
4. `data/social/YYYY-MM-DD.json`（每日一檔）＋ `data/social/index.json`（日期清單＋每日 `articles_n`／`via`）
5. `CLAUDE.md` 佈局節加一段（≤15 行）、`README.md` 加「社群聲量量測班（階段一）」節；本檔保留並在末尾補「實作紀錄」
6. `requirements.txt` 不需新增（只用 `requests`＋標準庫；HTML 解析用 `html.parser`，**不加 bs4／lxml**）

## 2. 產物 schema（`data/social/YYYY-MM-DD.json`）

```json
{
  "schema": 1,
  "date": "2026-09-27",              // 台北日曆日（文章發文時間落在該日 00:00–23:59 台北）
  "generated_at": "2026-09-27T23:2x:xx+08:00",
  "source": "ptt-stock",
  "robots": "allow" | "disallow" | "absent" | "unreachable",   // absent＝HTTP 4xx、照抓（2026-09-28 補）
  "robots_crawl_delay": null | 1.0,   // robots.txt 對 User-agent: * 宣告的 Crawl-delay（秒），null＝未宣告；
                                      // 生效值＝max(它, SOCIAL_MIN_INTERVAL)（2026-09-28 驗收後補）
  "robots_detail": { "status": 200 | null, "err": null | "ConnectionError: …（遮罩後，≤200 字）",
                     "final_url": null | "https://www.ptt.cc/…", "attempts": 1 },   // 2026-09-28 補：status 有值時 err 為 null；
                                      // final_url＝實際回應 URL（看有沒有被 302 到 over18／擋牆頁）；fixture 模式 final_url 為 null
  "fetch_errors": [ { "path": "/bbs/Stock/index.html", "status": 503 | null, "err": null | "…" } ],  // 失敗的每次嘗試，最多前 10 筆、path 不含 query
  "pages_fetched": 3, "articles_n": 187, "articles_fetched_n": 180, "failed_n": 7,
  "requests_n": 184, "elapsed_s": 212.3,
  "llm": { "model": "claude-opus-5", "prompt_ver": 1, "via": "batch"|"sync"|"mixed"|null,
           "skipped": null | "no-key" | "robots" | "no-articles" | "no-llm" | "pending" | "error",
           "classified_n": 180, "failed_n": 0,
           "usage": {"input_tokens": 0, "output_tokens": 0} },
  "stocks": { "2330": { "name": "台積電", "n": 41, "push": 913, "boo": 120,
                        "pos": 18, "neg": 9, "neu": 12, "unk": 2 } },
  "articles": [ { "aid": "M.1758980000.A.1B2", "title": "[標的] 台積電 多", "author": "xxx",
                  "ts": "2026-09-27 21:03", "push": 35, "boo": 2, "cat": "標的",
                  "url": "https://www.ptt.cc/bbs/Stock/M.1758980000.A.1B2.html",
                  "codes": ["2330"], "sent": {"2330": "pos"} } ],
  "teardown": { "since": "2026-09-27", "age_days": 0, "due": false },
  "fixture": true                     // 只有 --from-fixture 產的樣本才有這個頂層鍵（線上產物沒有）
}
```

- `llm.skipped`（2026-09-28 驗收後補）：`no-llm`＝`--no-llm`；`pending`＝進 LLM 之前先落地的那一版
  （分類完成會被覆寫，磁碟上還看得到它＝那班 LLM 路徑沒走完）；`error`＝`classify()` 整段拋例外，
  代號全記 `unk`、exit 仍 0。
- `sent` 的值除 `pos|neg|neu` 外**可為 `"unk"`**（LLM 未回／回了不在三類／同步回退預算耗盡）；
  LLM 整批跳過時 `sent` 才是 `null`。

- `n`＝提及該代號的篇數；`push`／`boo`＝那些篇的推／噓數加總；`pos+neg+neu+unk == n`（`unk`＝LLM 未回或回了不在三類）。
- `sent` 每篇每代號一個標籤 ∈ `pos|neg|neu|unk`；LLM 跳過時 `sent` 為 `null`、`unk == n`。
- **`prompt_ver` 與 `model` 每檔必帶**——改 prompt 就把 `SOCIAL_PROMPT_VER` +1，舊檔不可比。

## 3. 功能規格

### 3.1 抓取（`fetch_board`／`fetch_article`）
- 板首頁 `https://www.ptt.cc/bbs/Stock/index.html` 起向前翻頁（`index{N}.html`），直到整頁文章都早於目標日為止，且最多 `SOCIAL_MAX_PAGES`（15）頁。cookie `over18=1`。
- 每篇：解析 `.r-ent`（標題、作者、日期、推文數；「爆」→100、「X{n}」→ −n×10、「XX」→ −100，寫在註解）、去掉「(本文已被刪除)」與公告；標題以 `[分類]` 開頭者記 `cat`。
- 文章頁只抓當日 `SOCIAL_MAX_ARTICLES`（300）篇，依推文數高者優先；解析發文時間（`.article-meta-value` 第 4 個，`%a %b %d %H:%M:%S %Y`）、正文（`#main-content` 去 meta 與推文區）、推噓（`.push-tag` 計數）。
- 台北日歸屬用文章頁的發文時間；板首頁只有「月/日」，用來決定要不要抓文章頁。
- robots 四值：`allow`／`disallow`（200 且規則）、`absent`（4xx，照抓、`robots_crawl_delay` null、間隔維持
  `SOCIAL_MIN_INTERVAL`）、`unreachable`（5xx／例外／逾時，不抓）。`Disallow` 含 `*`／結尾 `$` 依萬用字元比對，任一規則命中 `/`、`/bbs/`、`/bbs/Stock/` 或板首頁即
  disallow；`Crawl-delay` 取 `max(它, SOCIAL_MIN_INTERVAL)` 生效並寫進 `robots_crawl_delay`（2026-09-28 補）。
- 節流：H4；失敗（非 200／逾時／例外）退避一次再失敗即計入 `failed_n`，**不寫 `err` 到產物以外的地方**。
- `--from-fixture DIR`：用目錄裡的 HTML 取代網路（測試與沙箱用；本沙箱連不到 ptt.cc）。

### 3.2 標的抽取（`extract_codes`）
- 代號：`(?<![0-9A-Z])\d{4}[A-Z]?(?![0-9A-Z])` 且必須存在於股票清單。
- 股名：股票清單的完整 `stock_name`（≥2 字）整詞命中；**只認全名、不建暱稱字典**（寧可漏）。
- **像西元年的代號（1990–2039，2026-09-28 補）**：只有同篇也出現該股股名（含「2025千興」「千興(2025)」這種緊鄰寫法）才算；後接「年」或日期格式（`2025/09/28`、`2025-09`、`2025.9`）的那次出現一律不算。見 §6.7。
- **常用詞股名停用清單 `NAME_STOPWORDS`（2026-09-28 補）**：清單內的名字（如「大量」）不以股名命中，只能靠代號命中（「3167大量」「大量(3167)」的代號本身就會命中）。見 §6.7。
- 股票清單：FinMind `TaiwanStockInfo`（限 twse／tpex），同日快取 `data/cache/social_info_<YYYYMMDD>.json`（不進 git，沿用 `.gitignore` 的 `data/cache`）；`--stock-info PATH` 讀本機 JSON。缺 token 且無快取 → 只認代號、`stocks[].name` 為 `null`，印 `::warning::`。
- 每篇最多記前 `SOCIAL_MAX_CODES_PER_ARTICLE`（8）個代號（依出現順序）。

### 3.3 情緒分類（`classify`）
- 輸入：標題＋正文前 `SOCIAL_BODY_CHARS`（600）字＋該篇命中代號清單。輸出：JSON 物件 `{代號: "pos"|"neg"|"neu"}`，只對輸入的代號作答。
- System prompt 存常數 `SOCIAL_SYS`，明寫：只判**作者對該檔的態度**，不判市場、不預測、不加解釋；無法判斷回 `neu`。
- 走 Anthropic Message Batches（`POST /v1/messages/batches`，raw `requests`，比照 `postmkt/build_summary.py` 的 `call_claude_batch`／`batch_deadline` 移植但**不共用**）：期限 `SOCIAL_BATCH_DEADLINE_SEC`（40 分）；逾時或整包失敗 → 逐篇同步回退，`via` 記實際路徑（全 batch＝`batch`、全 sync＝`sync`、混＝`mixed`）。
- `max_tokens` 256、無 thinking 參數（分類任務）。回應非合法 JSON 或缺代號 → 該代號 `unk`，不重試超過一次。
- **同步回退總預算 `SOCIAL_SYNC_BUDGET_SEC`（15 分，2026-09-28 驗收後補）**：從第一次同步呼叫起算，
  超過即停止回退、剩餘篇計入 `llm.failed_n`、代號記 `unk`、印 `::warning::`。與 batch 40 分合計 55 分，
  留餘裕給 70 分 job。**進 LLM 之前先寫一版 `llm.skipped="pending"` 的產物**，分類完成再覆寫。
- 模型常數 `SOCIAL_MODEL = "claude-opus-5"`（依本 session 載入的 claude-api skill 預設；要換成 `claude-haiku-4-5` 省成本屬使用者裁決，改常數即可，`model` 欄會如實記錄）。
- **usage 必記**：從每筆回應的 `usage` 加總 `input_tokens`／`output_tokens` 寫入 `llm.usage`，供估成本。

### 3.4 拆除提醒
- `SOCIAL_SAMPLE_SINCE = "2026-09-27"`、`SOCIAL_TEARDOWN_DUE_DAYS = 45`（日曆日）；`due` 時每班印 `::warning::社群量測班已滿 N 日，該回頭看樣本決定收窄／移除`，**不 issue、不 dispatch**。

## 4. 驗收清單（fresh-context 驗收者逐條打勾，綁 commit SHA）

- [ ] A1 `python -m pytest tests/ -q` 全綠（含既有四支）；`tests/test_social.py` ≥ 12 個案例，涵蓋：板首頁解析／文章頁解析（含「爆」「X3」）／跨日歸屬／代號與全名抽取（含負例：`00631L`、`2330A` 不誤中 `2330`、常見四位數如年份 `2026` 不在清單即不命中）／聲量聚合 `pos+neg+neu+unk==n`／Batches 結果依 `custom_id` 對回（亂序）／sync 回退／no-key 路徑／robots disallow 路徑／節流計數與上限／產物無 `body` 欄／金鑰遮罩
- [ ] A2 `python build_social.py --from-fixture tests/fixtures/social --stock-info tests/fixtures/social/stock_info.json --no-llm --date <fixture 日>` 實跑成功、產物符合 §2 schema、`index.json` 更新
- [ ] A3 `ruff check .`（若 repo 有設定）或 `python -m pyflakes build_social.py` 零錯誤
- [ ] A4 H1–H9 逐條檢查（見表內驗法）
- [ ] A5 workflow YAML `yaml.safe_load` 可解析；cron、secrets、notify-failure、concurrency、rebase 重試皆在；`permissions` 含 `contents: write` 與 `issues: write`
- [ ] A6 文件：`CLAUDE.md`／`README.md`／本檔「實作紀錄」節就位，且**不宣稱線上已跑過**
- [ ] A7 commit message 不含模型識別字串；push 前 `git fetch` 確認遠端分支狀態

**線上驗證（實作者與驗收者都做不到，留給使用者）**：
- [ ] L1 repo Settings → Secrets 已有 `ANTHROPIC_API_KEY`（`build-news.yml` 只用 `FINMIND_TOKEN`，本 repo 是否已有此 secret **未知**）
- [ ] L2 在分支上 `workflow_dispatch` 一次，run 綠、`data/social/<今日>.json` 落地、`robots` 欄為 `allow`（若為 `disallow`，階段一到此為止、改議）
- [ ] L3 看 `llm.usage` 估日成本，決定是否改 `SOCIAL_MODEL`

## 5. 背景（為什麼只量不判）

貼文宣稱「爬 PTT／Dcard 聲量→AI 情緒→表格」績效勝過投顧，屬單一樣本、無基準。家族鐵律 8：會影響投資方向的新訊號先問回測依據。聲量／情緒有三個未驗的問題：同期指標而非領先指標的機率高；可被帶風向操縱；LLM 分數跨版本不可比。故分三階段：**一、只收 30+ 個交易日資料（本檔）；二、在 taiwan-backtest 走 walkforward 驗資訊係數、與「昨日漲跌幅」基準比；三、有結果才談呈現，且只做描述性顯示**。任一階段結論為「無用」即收掉、資料留檔。

## 6. 實作紀錄（2026-09-27，實作者填；commit SHA 由驗收報告補）

- **交付物**：`build_social.py`、`tests/test_social.py`（25 案例）＋`tests/fixtures/social/`
  （板首頁 3 份 `index.html`／`index9999.html`／`index9998.html`、文章頁 4 份、
  `robots_allow.txt`／`robots_disallow.txt`、`stock_info.json`、`batch_results.jsonl`）、
  `.github/workflows/build-social.yml`、`CLAUDE.md` 佈局節一段、`README.md`「社群聲量量測班」節、
  `data/social/2026-09-26.json`＋`index.json`（fixture 產的 schema 樣本，檔內 `"fixture": true`）。
- **本機實跑**：`python -m pytest tests/ -q` 62 passed（含既有四支）；
  `python build_social.py --from-fixture tests/fixtures/social --stock-info tests/fixtures/social/stock_info.json --no-llm --date 2026-09-26`
  → 3 頁、候選 5 篇、抓到 4、失敗 1（fixture 刻意缺檔）、跨日 1 篇排除、4 檔標的；`pyflakes`／`ruff check` 零錯誤；
  workflow `yaml.safe_load` 通過。**線上未跑過**（L1–L3 留給使用者）。
- **規格外的決定（標明）**：
  1. `robots` 為 `unreachable` 時**也不抓文章**（§0 H5 只規定 disallow）——保守立場：未取得
     robots 就不抓，寧可缺一天。要改成「unreachable 視同 allow」只需動 `build()` 一個條件。
  2. 工作流目標日＝「起跑時刻減 6 小時」的台北日，而非直接取今天：GitHub cron 常態延遲 1~2 小時，
     23:20 延遲跨午夜會讓 `taipei_today()` 滾成隔日、當日文章一篇也抓不到。副作用：白天手動
     dispatch 不帶 `date` 會算到前一天（README 已寫明）。
  3. 「未達推文數上限但請求預算耗盡」的篇數計入 `failed_n`（另印 warning），不另立欄位。
  4. 板首頁 `div.r-list-sep` 之後的置底公告一律不抓；`[公告]` 開頭與「(本文已被刪除)」跳過。
  5. `articles_n`＝板首頁挑出的當日候選數（去重後、取前 300）；`articles_fetched_n`＝文章頁抓成功數；
     文章頁時間不落在目標日者（跨日）不進 `articles[]` 也不計 failed。
  6. 股名整詞命中時，同一位置有較長股名命中則較短的前綴名不算（避免「中華」吃掉「中華電」），
     仍只認清單全名、不建暱稱字典。
- **已知不確定點**：PTT DOM（`div.r-ent`／`div.nrec > span.hl`／`div.title > a`／`div.meta`／
  `div#main-content` 的 `div.article-metaline` × 3＋`article-metaline-right`／`div.push > span.push-tag`／
  `a.btn.wide` 上頁連結）**依記憶實作**，沙箱連不到 ptt.cc；解析器寬鬆不拋例外，但若實際結構不同，
  症狀會是 `articles_n: 0` 或 `failed_n` 很高——線上首跑要對照實際頁面。`robots.txt` 實際內容同樣未查證。
- **移植來源**：Batches 提交／輪詢／cancel／結果依 `custom_id` 對回的流程移植自 postmkt
  `build_summary.py` 的 `call_claude_batch`，刻意不共用模組；差異＝無 thinking 參數、`max_tokens` 256、
  期限固定 40 分（無牌鐘）、回應解析改為 JSON 物件而非長文。

### 6.1 驗收退回修正（2026-09-28，於 `e3c7f1e` 之後同分支補）

- **必修 1（LLM 路徑掛住不得拖垮聲量資料）**：
  a. `build(out_dir=…)` 在 `classify()` 之前先 `write_outputs()` 一版 `sent` 全 null／`llm.skipped="pending"`
     的完整產物（`stocks` 已聚合、`index.json` 已更新），分類完成後由 `main()` 覆寫；`classify()` 拋例外
     則記 `llm.skipped="error"`、代號全 `unk`、exit 0（`::warning::` 帶遮罩後的例外訊息）。
     守門測試：`test_pending_product_written_before_classify_and_survives_exception`（monkeypatch `classify`
     拋例外，斷言拋出當下磁碟已有 pending 版且不變式成立、最終版 `skipped="error"`）、
     `test_build_without_out_dir_writes_nothing`。
  b. `SOCIAL_SYNC_BUDGET_SEC = 15*60`：`classify()` 從第一次同步呼叫起算，超過即不再呼叫 `/v1/messages`
     （含「非法 JSON 再試一次」那次），沒拿到結果的篇計 `failed_n`、代號 `unk`，印 `::warning::`。
     守門測試：`test_sync_budget_exhausted_stops_fallback_keeps_invariant`（假鐘每次呼叫耗 400 秒、預算 500
     → 恰呼叫 2 次、第 3 篇 unk、`pos+neg+neu+unk==n`、pending 檔存在、並斷言 15+40 分 < 70 分）。
- **必修 2（robots 解析）**：`parse_robots()` 取代 `robots_verdict()` 內部實作（後者保留為包裝）。
  `Disallow` 值含 `*` 轉 `.*`、結尾 `$` 精確結尾、其餘前綴比對；任一規則命中 `ROBOTS_CHECK_PATHS`
  （`/`、`/bbs/`、`/bbs/Stock/`、`/bbs/Stock/index.html`）即 disallow。`Crawl-delay`（只取 `*` 那組）
  → `fetcher.min_interval = max(既有, Crawl-delay)`，產物新增 `robots_crawl_delay`（null＝未宣告）。
  守門測試：`test_robots_wildcard_and_dollar_rules`、`test_robots_crawl_delay_parsed_and_applied_to_fetcher`。
- fixture 模式的 `PttFetcher` 改注入 `sleep=lambda s: None`（fixture 的 `robots_allow.txt` 宣告
  `Crawl-delay: 1`，否則本機實跑會真的睡 10 秒）；線上路徑仍是 `time.sleep`。
- §2 schema 同批回寫：`llm.skipped` 補 `no-llm`／`pending`／`error`、`sent` 值可為 `unk`、頂層 `fixture` 鍵、
  新欄 `robots_crawl_delay`。`tests/test_social.py` 由 25 案例增為 30。

### 6.2 classify() 例外不得靜默成綠燈（2026-09-28 第二次退回修正）

- `build_social.py` 新增 `EXIT_LLM_ERROR = 2` 與 `llm_exit_code(out)`：`llm.skipped == "error"`（classify()
  例外）或「有目標篇（有代號的文章 >0）卻 `failed_n` ≥ 目標篇數」（全數失敗）→ `main()` **exit 2**，
  產物照樣落地並印 `::error::`；預期跳過（`--no-llm`／no-key／robots disallow／unreachable／no-articles）
  與部分失敗仍 exit 0。
- `build-social.yml` 走家族「延後紅燈」模式（同 taiwan-flow-live-v2 `intraday.yml`）：Build 步驟
  `id: build`、以 `if python …; then ok=1; else ok=0; fi` 寫 `$GITHUB_OUTPUT`（步驟本身不失敗、任何非 0 exit
  都記 ok=0）→ Commit 照跑 → 「Fail on LLM error」`if: steps.build.outputs.ok == '0'` → `exit 1` →
  notify-failure（末步 `if: failure() || cancelled()`）。**未用 `continue-on-error`**。
- 守門測試：`test_main_exit_2_on_classify_exception_but_product_written`、
  `test_main_exit_2_when_all_articles_fail_classification`、`test_main_exit_0_on_expected_skips`。
  `tests/test_social.py` 30 → 33 案例。

### 6.3 線上首跑 `robots: unreachable` 的診斷欄位（2026-09-28）

- **線上事實**（run 36376947260，main）：產物 `robots: "unreachable"`、`requests_n: 2`、`elapsed_s: 2.4`、
  `articles_n: 0`——只知道 robots.txt 兩次都沒拿到，**分不出是被擋（403／302 到擋牆頁）還是連不到**。
- **補的欄位**：`robots_detail` `{status, err, final_url, attempts}`（取 robots 那次 `PttFetcher.get()` 的
  `last_detail`；`err`＝遮罩後的例外類別名＋訊息前 200 字，`status` 有值時為 null；`final_url`＝
  `requests` 回應的 `.url`）與 `fetch_errors`（每次失敗嘗試 `{path, status, err}`，最多前
  `FETCH_ERRORS_MAX`＝10 筆、path 去 query、err 過 `mask_secret`）。log 另印一行
  `robots fetch: verdict=… status=… err=… final_url=… attempts=…`。
- 守門測試：`test_robots_detail_distinguishes_blocked_from_unreachable`（403＋302 URL vs 連線例外含假 token；
  斷言欄位、遮罩、截斷與 log 行）、`test_fetch_errors_capped_and_path_without_query`；schema 測試釘 fixture 的
  `robots_detail`／`fetch_errors` 形狀。`tests/test_social.py` 33 → 35 案例。
- **仍未知**：真正的失敗種類要看下一班的 `robots_detail`；本沙箱連不到 ptt.cc，無法在本機重現。

### 6.4 robots 判定改四值（2026-09-28，依 RFC 9309）

- **第二班實測證據**（main 上的 `data/social/2026-09-28.json`）：`robots_detail = {status: 404, err: null,
  final_url: "https://www.ptt.cc/robots.txt", attempts: 2}`——ptt.cc **沒有 robots.txt**、runner 連得到，
  原本「非 200 一律 `unreachable`」把它判成不抓，過度保守。
- **依據**：RFC 9309 §2.3.1.3「Unavailable」——伺服器回 4xx 時 crawler MAY 存取任何資源；§2.3.1.4
  「Unreachable」——5xx 或連線失敗時 crawler MUST 假設全站 disallow。
- **實作**：`parse_robots(text, status)`／`robots_verdict(text, status)`——text 為 None 時 4xx → `absent`、其餘
  → `unreachable`；`ROBOTS_FETCH_OK = ("allow", "absent")` 走同一條抓取路徑；`absent` 印一行
  「依 RFC 9309 §2.3.1.3 視為無限制」，`robots_crawl_delay` 為 null、fetcher 間隔維持 `SOCIAL_MIN_INTERVAL`；
  `llm.skipped="robots"` 只在 `disallow`／`unreachable` 出現。
- 守門測試：`test_robots_4xx_is_absent_and_fetch_proceeds[404／403]`（照抓 3 頁 5 篇、`skipped=no-key`
  非 `robots`、間隔 1.0）、`test_robots_5xx_is_unreachable_and_fetch_skipped[500／503]`（只打 2 次、不抓）、
  `test_robots_connection_error_is_unreachable`；既有 disallow／unreachable 測試維持，
  `test_robots_detail_distinguishes_blocked_from_unreachable` 的 403 案例改斷言 `absent`。
  `tests/test_social.py` 35 → 40 案例。

### 6.5 線上實測：GitHub runner 被 ptt.cc 拒絕（403）（2026-09-28，只記錄、不改程式）

- **三班實測序列**（皆 `main`、`build-social.yml` 手動／排程 run）：
  1. 第一班（run 36376947260）：`robots: "unreachable"`、`requests_n: 2`、`elapsed_s: 2.4`——只知道 robots.txt
     兩次沒拿到，產物分不出種類 → 補 `robots_detail`／`fetch_errors`（§6.3）。
  2. 第二班：`robots_detail = {status: 404, err: null, final_url: "https://www.ptt.cc/robots.txt", attempts: 2}`
     → ptt.cc **沒有 robots.txt**、runner 連得到 → 4xx 改判 `absent` 照抓（§6.4）。
  3. 第三班（run 36377888325，commit `6e35102`，產物 `data/social/2026-09-28.json`）：`robots: "absent"`（404），
     但 `/bbs/Stock/index.html` **兩次都 HTTP 403**（`fetch_errors` 第 3、4 筆 `{path: "/bbs/Stock/index.html",
     status: 403, err: null}`），`pages_fetched: 0`、`articles_n: 0`、`llm.skipped: "no-articles"`。
- **結論**：robots 沒有規則，但站方對本 runner 的板首頁請求以 **403 明確拒絕**（請求帶 UA＝本班識別字串
  `SOCIAL_UA`、cookie `over18=1`）。程式路徑本身走通了（robots → absent → 翻頁），卡在站方拒絕。
- **未知、且本班刻意不測**：403 是依 **IP 段**（GitHub Actions／雲端出口）還是依 **UA**。**不以偽裝瀏覽器 UA
  試探**——H5 的立場是未取得同意不抓，403 是站方的明確拒絕，換 UA 繞過等於違反同一條。
- **待使用者裁決的兩條路**：
  1. **改由自有機器跑同一支程式**（`python build_social.py --date …`，需 `FINMIND_TOKEN`／`ANTHROPIC_API_KEY`
     環境變數），驗證是否純粹 IP 因素；程式一字不改。若自有機器拿得到 200，再決定排程要不要搬（GH cron
     這條路對 ptt.cc 就是死的）。
  2. **階段一到此為止**：留下程式、測試、fixture 樣本與本節紀錄，`build-social.yml` 停用或刪除。
- **在裁決前**：cron 每晚仍會跑、每班產一份 `pages_fetched: 0` 的產物並 commit（exit 0、不紅燈——
  「抓不到」不是分類失效）。這是刻意不擋的：多累積幾班 403 樣本本身也是證據；若不想累積，先把
  `build-social.yml` 的 `schedule` 註解掉。

### 6.6 執行環境搬到 Hetzner（2026-09-28，使用者裁定：GitHub 只當資料倉）

- **Hetzner 實測**（2026-09-28 台北中午，commit `2477cf5`，`--no-llm`、兩個 token 清空）：robots 404 → `absent`、
  板首頁 **200**、`pages_fetched: 3`、`articles_n: 14`、`articles_fetched_n: 14`、`failed_n: 0`、
  `requests_n: 19`、`elapsed_s: 19.8`。**結論**：§6.5 的 403 是 GitHub Actions 出口 IP 被 ptt.cc 擋；
  依記憶實作的 DOM 解析器與節流在真實頁面上正確（14 篇全抓到、零失敗）。
- **改動**：
  - 新增 `tools/social_cron.sh`（bash，`set -euo pipefail`）：`REPO_DIR`（預設 `/root/projects/taiwan-stock-news`）
    ／`ENV_FILE`（預設 `/root/.config/taiwan-stock-news.env`，須存在且 **0600**，否則 exit 3）／`--dry-run`
    （不 commit 不 push）／`--no-llm` 透傳／`SOCIAL_EXTRA_ARGS`（測試透傳 fixture 參數）。流程：工作樹不乾淨
    exit 4（不 stash）→ `fetch` → `checkout main` → `pull --ff-only`（失敗 exit 4）→ `set -a; . ENV_FILE` →
    目標日＝台北時刻減 6 小時（與 workflow 同口徑）→ `python3 build_social.py`（exit code 記下、不中斷）→
    `git add data/social` → 有變化才 commit（`social update <日> (<UTC>, hetzner)`）→ `pull --rebase` 重試 5 次、
    衝突以本班 `data/social` 為準（`--theirs`）→ 以 build 的 exit code 退出（2＝LLM 失效）。全程不 echo
    任何環境變數值。
  - `build-social.yml` 移除 `schedule`、原位留註解；只剩 `workflow_dispatch` 供診斷（預期 403）。
  - 守門測試 `tests/test_social_cron.py`（6 案例，臨時 bare repo＋clone）：`--dry-run` 產物寫出但不 push
    不 commit、log 不含金鑰值；假 `build_social.py` 回 2 → 腳本 exit 2；ENV_FILE 0644／不存在 → exit 3；
    工作樹髒 → exit 4；未知參數 exit 2；`bash -n`。
- **安裝**（詳見 claude-harness `Harness/server-hetzner.md`「每日社群班」）：
  1. `ENV_FILE`（`/root/.config/taiwan-stock-news.env`，`chmod 600`），內容只有兩行、鍵名如下（值不得出現在任何文件）：
     ```
     FINMIND_TOKEN=
     ANTHROPIC_API_KEY=
     ```
  2. `crontab -e`：`20 15 * * * cd /root/projects/taiwan-stock-news && bash tools/social_cron.sh >> /var/log/social_cron.log 2>&1`
     （UTC 15:20＝台北 23:20，每日含週末；用 PATH 的 `python3`，不硬編路徑）。先手動 `bash tools/social_cron.sh --dry-run --no-llm` 一次。
- **已知限制**：Hetzner cron 失敗**不會開 issue**——只有 exit code 與 `/var/log/social_cron.log`，與 claude-harness
  `tools/sync_machine.py` 同一個盲區（Actions 上的 `notify-failure` 看不到機器本地）。exit 2（LLM 失效）同樣只在 log。
  要主動告警得在該機放憑證，屬另案。§6.5 寫的「cron 每晚仍會跑並 commit 空產物」自本節起不再成立（GH schedule 已移除）。

### 6.7 首班實跑兩個 bug：batch custom_id 被拒、年份與常用詞誤判代號（2026-09-28）

**證據**（Hetzner 首班，產物 `data/social/2026-09-28.json`＝commit `25c9400`，**該檔不重跑、不覆寫**，修正自下一班生效）：

1. **batch 整包被拒**：log `batch 提交失敗（requests.0.custom_id: String should match pattern '^[a-zA-Z0-9_-]{1,64}$'）→ 全數同步回退`。
   原因是 `classify()` 直接拿 `aid` 當 `custom_id`，PTT 文章 id 形如 `M.1790528726.A.991`，含 `.`。
   產物 `llm.via: "sync"`、`classified_n: 19`——每班都付原價、半價路徑從未走通。
   既有測試沒擋到：假 Anthropic 不驗 custom_id 格式，fixture JSONL 也直接用 aid。
2. **代號誤判**：`stocks` 前幾名 `2025 千興 n=6`、`2023 燁輝 n=4`、`2024 志聯 n=4`、`3167 大量 n=4`。
   2023／2024 的 `push`／`boo` 完全相同（377／57）＝同幾篇文章同時命中兩個「年份」；「大量」是常用詞被股名整詞命中。
   25 篇共 72 次代號命中中，有 **40 次**落在「像年份的代號」或「停用清單股名」上
   （2025×6、3167×4、2023×4、2024×4、5347×3、2027×3、9921×2、2022×2、5287×2、2002×2、8923／6435／2015／2020／2455／8440／2028／2030 各 1）。
   這些數字直接進 postmkt「社群聲量」tab 的排行與建議，屬資料正確性 bug。

**修正**（`build_social.py`，分類 prompt／產物 schema／`stocks` 欄位一律不動）：

- `batch_custom_ids(todo)` 產 `a0`、`a1`…（todo 內序號），`classify()` 以 cid 建請求並對回原篇；
  重複 aid 也不撞；同步回退路徑行為不變（只是 key 從 aid 換成 cid）。fixture `batch_results.jsonl` 的 custom_id 同步改成 `a0`～`a2`（仍亂序）。
- `extract_codes()`：
  - 代號落在 `YEARLIKE_MIN`～`YEARLIKE_MAX`（1990～2039）時，需同篇出現該股股名才算；後接「年」或日期（`DATE_AFTER_RE`）的那次出現直接跳過
    （股名另處命中時仍以股名位置計入，所以此規則只影響排序與截斷）。「緊鄰股名」是「同篇有股名」的特例，同一條件涵蓋。
  - `NAME_STOPWORDS`（16 個）：`大量 世界 數字 巨大 時報 綠電 全新 大中`（09-28 產物實際命中、且在文中多半是普通詞或子字串誤切，
    例如「擴大中」含「大中」、「工商時報」含「時報」）＋ `聯合 國產 中華 三星 新興 綠能 物聯 全國`（高頻常用詞或外部實體名：
    聯合報／國產車／中華民國／韓國三星／新興市場／綠能產業／物聯網／全國）。停用名字只能靠代號命中。
  - **代價（刻意接受）**：只寫「世界」指世界先進、只寫「大量」指大量科技的文章會漏；本站原則「寧可漏」（§3.2）。
  - **全稱別名 `NAME_ALIASES`（2026-09-29 使用者裁決）**：FinMind 把 5347 的股名記成「世界」，停用後連全名「世界先進」也會漏，故加一條別名「世界先進」→5347；**只在 info 裡 5347 的股名確實是「世界」時生效**（代號對應變了就自動失效）。單寫「世界」仍不算。其餘候選停用詞（統一、大同、光明…）使用者裁決**先不加**，看幾天產物再說。
  - **待使用者裁決、未收入的候選**（皆為 2 字股名且是常見詞，但沒有產物實證）：統一、大同、全家、精華、台南、光明、地球、安心、幸福、
    介面、精確、互動、相互、開展、無敵、傳奇、有益、樂意、典範、是方、大樹、合一、信義、優美、櫻花、中天、上品、新建、大成。
- **驗證限制**：產物不存正文（H3），09-28 的 25 個標題本身只命中 `2903`（遠百）與 `3481`（群創），改動前後完全相同；
  上面 40 次命中都來自正文，無法逐篇重算「改後會剩哪些」。依規則推論：年份型代號在正文未出現股名時消失、停用清單 8 檔（3167／5347／5287／9921／8923／6435／2455／8440）
  除非正文有該代號字面才保留。fixture 收進當日的三篇文章（第 4 頁屬 9/27、不收）改動前後代號清單完全相同（驗收以新舊兩版 `collect_day` 對跑確認）。
- 測試 `tests/test_social.py` 新增 4 案例（年份佐證、停用清單、custom_id 格式與唯一、嚴格假 API 下重複 aid＋亂序對回且零同步回退）；
  變異測試：custom_id 改回 aid、custom_id 撞號、拿掉年份檢查、拿掉日期跳過、拿掉停用清單、年份下界改 2000，皆有測試轉紅。

## 7. 其他社群來源評估紀錄（2026-09-28，結論：目前只用 PTT）

留給之後的人，**不要重走一次**。

### 7.1 CMoney 股市爆料同學會：停在 401
- robots.txt：`User-agent: *` 為 `Allow: /`，禁止清單只有 `/cashflow/`、`/member/`、`/identity/` 等，`/forum` 與 `/api/` 未被禁止（Hetzner 實測）。
- 條款（`/member/tos.aspx` 關鍵字比對，非逐字讀完）：未見明文禁止爬蟲或自動化；禁止「未經他人同意而擅自複製他人資訊轉售、轉載」與「未經 CMoney 事前授權的商業行為」。
- 個股頁 `/forum/stock/<代號>` 伺服器輸出的 HTML 與 `__NUXT__`（Nuxt 2 函式形式）**都沒有文章串**；ld+json 只有 BreadcrumbList／WebPage／Corporation。另有 `windSnapshots`（CMoney 自己的「風向」情緒分數），屬其衍生產品，不採用。
- 文章串由前端 `POST https://www.cmoney.tw/api/mach/api/Article/GetChannelsArticleByWeight?count=N`，body `{"items":["Popular-Stock.<代號>"]}` 載入（依權重排序的熱門文，非完整時間序）。
- **不帶憑證打該 API 回 HTTP 401**（Hetzner 實測）。取得憑證只能用使用者帳號或模仿前端取訪客 token，兩者都是繞過站方存取控制，**不做**。使用者 2026-09-28 的「視同授權」只決定我方承擔條款風險，不能替 CMoney 開放鎖住的介面。唯一合規路徑：向 CMoney 申請資料授權。

### 7.2 Threads：使用者 2026-09-28 決定放棄
- 官方 `keyword_search` 需 `threads_keyword_search` 權限；**審核前只搜得到授權帳號自己的貼文**，測試者亦同。
- 進階權限須連結已完成 **Business Verification** 的商家帳號，個人無行號／稅籍多半過不了（未查證到確定答案）。
- 他人貼文的讚數／回覆數很可能拿不到（未實測）。
- 查證限制：Meta 官方頁在研究沙箱被擋，以上依 GitHub 逐字轉貼的官方原文與搜尋摘要。

### 7.3 其他已排除
Dcard（Cloudflare 擋、無官方 API）、Facebook 社團（Groups API 2024 關閉）、LINE 社群（無讀取介面）、X（免費層取消、按則計價）、Mobile01／玩股網／鉅亨討論區（無 API）。
仍可考慮但未做：YouTube 財經頻道留言（官方 API，訊號偏弱）、Telegram 公開頻道（官方 API，屬意見領袖單向發文）。
交易行為類情緒指標（期交所 Put/Call 比、臺指 VIX、小台散戶多空比）不屬社群文本，另案。
