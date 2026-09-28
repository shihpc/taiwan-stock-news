# tests/test_social_cron.py
# tools/social_cron.sh 守門（免 token 免網路）：臨時 bare repo＋clone，走 --dry-run --no-llm 與
# SOCIAL_EXTRA_ARGS 透傳 fixture 模式。斷言：不 push、產物寫出、build_social.py 的 exit code 原樣傳遞、
# ENV_FILE 權限守門、工作樹不乾淨拒跑、log 不含金鑰值。
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPT = os.path.join(ROOT, "tools", "social_cron.sh")
FIX = os.path.join(ROOT, "tests", "fixtures", "social")
FAKE_TOKEN = "fake-finmind-token-ABCDEFGHIJ"
FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKEFAKE"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """bare origin ＋ clone（main 上有 build_social.py），回 (clone, bare)。"""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(bare), str(clone))
    _git(clone, "config", "user.name", "t")
    _git(clone, "config", "user.email", "t@x")
    shutil.copy(os.path.join(ROOT, "build_social.py"), clone / "build_social.py")
    _git(clone, "add", "build_social.py")
    _git(clone, "commit", "-q", "-m", "init")
    _git(clone, "push", "-q", "origin", "main")
    return clone, bare


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / "social.env"
    p.write_text(f"FINMIND_TOKEN={FAKE_TOKEN}\nANTHROPIC_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    p.chmod(0o600)
    return p


def _run(clone, env_file, *args, extra_env=None):
    env = {**os.environ, "REPO_DIR": str(clone), "ENV_FILE": str(env_file),
           "SOCIAL_EXTRA_ARGS": f"--from-fixture {FIX} --stock-info {os.path.join(FIX, 'stock_info.json')}"}
    env.pop("FINMIND_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.update(extra_env or {})
    return subprocess.run(["bash", SCRIPT, *args], env=env, capture_output=True, text=True)


def test_dry_run_writes_product_but_does_not_push(repo, env_file):
    clone, bare = repo
    before = _git(bare, "rev-parse", "main")
    r = _run(clone, env_file, "--dry-run", "--no-llm")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry-run" in r.stdout and "target day=" in r.stdout
    out_dir = clone / "data" / "social"
    assert (out_dir / "index.json").exists() and any(p.suffix == ".json" for p in out_dir.iterdir())
    assert _git(bare, "rev-parse", "main") == before                 # 沒 push
    assert _git(clone, "log", "--oneline") .count("\n") == 0         # 沒 commit
    assert FAKE_TOKEN not in r.stdout + r.stderr and FAKE_KEY not in r.stdout + r.stderr


def test_exit_code_from_build_is_propagated(repo, env_file):
    clone, bare = repo
    fake = ("import os,sys\nos.makedirs('data/social',exist_ok=True)\n"
            "open('data/social/x.json','w').write('{}')\nprint('fake build')\nsys.exit(2)\n")
    (clone / "build_social.py").write_text(fake, encoding="utf-8")
    _git(clone, "commit", "-qam", "fake build")
    _git(clone, "push", "-q", "origin", "main")
    r = _run(clone, env_file, "--dry-run", "--no-llm")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "build_social.py exit=2" in r.stdout and (clone / "data" / "social" / "x.json").exists()


def test_env_file_permission_gate(repo, env_file):
    clone, _ = repo
    env_file.chmod(0o644)
    r = _run(clone, env_file, "--dry-run", "--no-llm")
    assert r.returncode == 3 and "600" in r.stdout
    r = _run(clone, env_file.parent / "missing.env", "--dry-run", "--no-llm")
    assert r.returncode == 3


def test_dirty_worktree_refused(repo, env_file):
    clone, _ = repo
    (clone / "build_social.py").write_text("# dirty\n", encoding="utf-8")
    r = _run(clone, env_file, "--dry-run", "--no-llm")
    assert r.returncode == 4 and "工作樹不乾淨" in r.stdout


def test_unknown_argument_rejected(repo, env_file):
    clone, _ = repo
    r = _run(clone, env_file, "--bogus")
    assert r.returncode == 2


def test_bash_syntax():
    subprocess.run(["bash", "-n", SCRIPT], check=True)
    assert sys.version_info >= (3, 11)
