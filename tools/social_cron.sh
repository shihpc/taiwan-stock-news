#!/usr/bin/env bash
# tools/social_cron.sh — 社群聲量量測班（階段一）的 Hetzner cron 入口（2026-09-28）
#
# 為什麼不在 GitHub Actions 跑：實測 GitHub runner 出口 IP 對 ptt.cc 板首頁一律 403，
# Hetzner 同一支程式拿 200（docs/social-phase1.md §6.5／§6.6）。GitHub 只當資料倉。
#
# 用法：bash tools/social_cron.sh [--dry-run] [--no-llm]
#   REPO_DIR   repo 位置（預設 /root/projects/taiwan-stock-news）
#   ENV_FILE   只含 FINMIND_TOKEN=／ANTHROPIC_API_KEY= 的環境檔，須存在且權限 0600（否則 exit 3）
#   SOCIAL_EXTRA_ARGS  透傳給 build_social.py 的額外參數（測試用：--from-fixture … --stock-info …）
#   --dry-run  不 commit、不 push，只印會做的事；--no-llm 透傳給 build_social.py
# exit code：0 完成；2＝build_social.py 回報情緒分類失效（產物已 commit，延後紅燈精神）；
#            3＝ENV_FILE 不合格；4＝工作樹不乾淨或 pull --ff-only 失敗；其他＝git 步驟失敗
# 全程只印流程與 git 輸出，**絕不 echo 任何環境變數值**。
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/projects/taiwan-stock-news}"
ENV_FILE="${ENV_FILE:-/root/.config/taiwan-stock-news.env}"
DRY_RUN=0
NO_LLM=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-llm)  NO_LLM="--no-llm" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

# ── ENV_FILE 守門：存在且 0600（金鑰檔不得對群組／其他人可讀）──────────────
if [ ! -f "$ENV_FILE" ]; then
  log "ENV_FILE 不存在：$ENV_FILE（exit 3）"; exit 3
fi
perm="$(stat -c '%a' "$ENV_FILE")"
if [ "$perm" != "600" ]; then
  log "ENV_FILE 權限為 $perm、須為 600：$ENV_FILE（exit 3）"; exit 3
fi

cd "$REPO_DIR"
log "repo=$REPO_DIR dry_run=$DRY_RUN no_llm=${NO_LLM:-0}"

# ── 工作樹必須乾淨（不 stash：那可能是這台機器上還沒推的工作）──────────────
if [ -n "$(git status --porcelain)" ]; then
  log "工作樹不乾淨，拒跑（exit 4）："; git status --short; exit 4
fi
git fetch origin main
git checkout -q main
if ! git pull --ff-only origin main; then
  log "pull --ff-only 失敗（本地 main 與遠端分歧，要人來看；exit 4）"; exit 4
fi

# ── 載入金鑰（只進本 process 環境，不印）──────────────────────────────────
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# 目標日：與 build-social.yml 同一口徑——起跑時刻減 6 小時的台北日，延遲 ≤6 小時不滾日
DAY="$(TZ=Asia/Taipei date -d '-6 hours' +%F)"
log "target day=$DAY"

# ── 跑管線：不因非 0 中斷（延後紅燈精神：產物先落地、exit code 最後才退）──────
build_rc=0
# shellcheck disable=SC2086
python3 build_social.py --date "$DAY" $NO_LLM ${SOCIAL_EXTRA_ARGS:-} || build_rc=$?
log "build_social.py exit=$build_rc"

# ── commit／push data/social ─────────────────────────────────────────────
git add data/social
if git diff --cached --quiet; then
  log "data/social 無變化，跳過 commit"
  exit "$build_rc"
fi
if [ "$DRY_RUN" = "1" ]; then
  log "dry-run：會 commit 以下檔案並 push origin main（實際未做）："
  git diff --cached --name-only
  git reset -q -- data/social
  exit "$build_rc"
fi
git config user.name  >/dev/null 2>&1 || git config user.name  "hetzner-cron"
git config user.email >/dev/null 2>&1 || git config user.email "hetzner-cron@localhost"
git commit -q -m "social update $DAY ($(date -u '+%Y-%m-%d %H:%M UTC'), hetzner)"
log "已 commit $(git rev-parse --short HEAD)"

# push 走 build-social.yml 同款 pull --rebase 重試（最多 5 次、間隔 4 秒）；
# 本班只動 data/social/**，衝突必在該目錄，以本班版本為準。
# ⚠ rebase 期間 --ours/--theirs 語意反轉：rebase 是把本班 commit replay 到 origin 最新
# commit 之上，「ours」＝origin 那邊、「theirs」才是本班 commit——所以「取本班版本」用 --theirs。
pushed=0
for i in 1 2 3 4 5; do
  if git pull --rebase origin main; then
    if git push origin HEAD:main; then pushed=1; break; fi
  else
    if git checkout --theirs -- data/social && git add data/social; then
      GIT_EDITOR=true git rebase --continue \
        || git rebase --skip \
        || git rebase --abort || true
    else
      git rebase --abort || true
    fi
  fi
  log "push 第 $i 次未成功，4 秒後重試"
  sleep 4
done
if [ "$pushed" != "1" ]; then
  log "push failed after 5 retries（exit 1）"; exit 1
fi
log "已 push origin main：$(git rev-parse --short HEAD)"
exit "$build_rc"
