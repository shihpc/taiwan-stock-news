# 社群聲量量測班（階段一）— 驗收條件

**狀態**：階段一＝只收資料、不顯示、不下判準。動機與三階段設計見本檔末「背景」節。
**目標專案**：`/home/user/taiwan-stock-news`，分支 `claude/investment-site-optimization-nac77h`。
**寫於** 2026-09-27，動手前定稿；驗收綁定實作 commit SHA（驗收報告填入）。

## 0. 硬約束（任一違反即不通過）

| # | 約束 | 驗法 |
|---|------|------|
| H1 | **不顯示**：`index.html` 一字不改、`news.json` 格式不改、Worker 不改 | `git diff --stat` 不含 `index.html`／`news.json` |
| H2 | **只做 PTT Stock 板**，不碰 Dcard、不碰其他板 | grep `dcard` 零命中（程式碼） |
| H3 | **不存文章正文**：產物只存標題／作者／時間／推噓數／連結／命中代號／情緒標籤；正文只在記憶體用完即丟 | 產物 schema 檢查＋測試斷言無 `body` 欄 |
| H4 | **抓取節流**：對 ptt.cc 的請求間隔 ≥ `SOCIAL_MIN_INTERVAL`（1.0 秒）、單班總請求 ≤ `SOCIAL_MAX_REQUESTS`（400）、UA 帶專案識別字串 | 測試以假 session 計數＋計時 |
| H5 | **robots 守門**：每班先抓 `https://www.ptt.cc/robots.txt`，若 `User-agent: *` 對 `/bbs/` 或 `/` 為 `Disallow` → **不抓任何文章**，仍寫出產物並記 `robots: "disallow"`、`articles_n: 0`，exit 0 並印 `::warning::` | 測試餵 disallow 的 robots 文字 |
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
  "robots": "allow" | "disallow" | "unreachable",
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
- robots `Disallow` 含 `*`／結尾 `$` 依萬用字元比對，任一規則命中 `/`、`/bbs/`、`/bbs/Stock/` 或板首頁即
  disallow；`Crawl-delay` 取 `max(它, SOCIAL_MIN_INTERVAL)` 生效並寫進 `robots_crawl_delay`（2026-09-28 補）。
- 節流：H4；失敗（非 200／逾時／例外）退避一次再失敗即計入 `failed_n`，**不寫 `err` 到產物以外的地方**。
- `--from-fixture DIR`：用目錄裡的 HTML 取代網路（測試與沙箱用；本沙箱連不到 ptt.cc）。

### 3.2 標的抽取（`extract_codes`）
- 代號：`(?<![0-9A-Z])\d{4}[A-Z]?(?![0-9A-Z])` 且必須存在於股票清單。
- 股名：股票清單的完整 `stock_name`（≥2 字）整詞命中；**只認全名、不建暱稱字典**（寧可漏）。
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
