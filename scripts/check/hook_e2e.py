#!/usr/bin/env python3
"""hook_e2e.py — 交付面掛勾的端到端測試：**真的做一個 commit、真的推一次**。

WHY 這一支存在（而不是只在 `--selftest` 裡測函式）：`--selftest` 測的是**判準**（該不該紅），
掛勾的成敗卻取決於**git 真的叫了它、它的 rc 真的讓 commit／push 停下來**。兩者之間隔著一層
只有真跑才會出現的東西：git 的 hooks lookup、訊息檔的實際內容、pre-push 的 stdin refs 格式、
`--not --remotes` 這種多詞 revspec、以及 git 對非零 rc 的反應。§77 的六條實測就是這樣做出來的
—— 這支把那六條變成常駐測試（在系統暫存區做一個 scratch repo，不碰本樹）。

用法：
    python3 scripts/check/hook_e2e.py [--keep]

兩段：
  * **synthetic**（`Backup/green.json`／`Backup/dirty.json`）：判準的形狀，hermetic、不依賴任何真實產物。
  * **真產物**（`Backup/l253_anchor_20260930/`、09-30 02:1x 的 prod-new ＋ harness bench ＋ 交付 cell）：
    同一支臂三場，逐 rep 離散不同 ⇒ 一場可引用、兩場不可。這一格才回答「掛勾對**真的 harness 產物**
    會不會動、分不分得出是哪一場」。產物不在這台機器上時明說 SKIP（不假裝 PASS）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
TOOL = "scripts/check/doc_claim_gate.py"
DEPS = ("scripts/check/quote_gate.py", "scripts/check/void_number_check.py")

# 產物 fixture：形狀照 doc_claim_gate --selftest 的 GOOD_ARM（逐 rep 與 stddev 自洽 ⇒ R0 過），
# KEEP_CLEAN 必須真的是 QUOTABLE，否則「放行」那一條測不到東西。
GREEN = [{
    "tag": "prod-new", "profile": "prod-new",
    "attribution": {"verdict": "none", "thermal_worst": "NOMINAL"},
    "cell": {"named_cell": "delivery"},
    "rows": [{"avg_ts": 10.923, "stddev_ts": 0.519, "samples_ts": [10.34, 11.34, 11.08],
              "n_prompt": 0, "n_gen": 128, "n_depth": 512}],
}]
DIRTY = json.loads(json.dumps(GREEN))
DIRTY[0]["attribution"] = {"verdict": "swap", "thermal_worst": "NOMINAL",
                           "why": "swap_growth=1515.07 MiB"}

# ── 真產物（09-30 02:1x 那一夜的三場、同一支臂：prod-new ＋ harness bench ＋ 交付 cell、MTP off）──
# WHY 不只用 synthetic：fixture 證明的是**判準**會動，證明不了「它對真的 harness 產物也會動」。
# 三場的差別只有逐 rep 離散（1.097／3.036／2.877）⇒ 這一格順便測「它分得出是哪一場」。
REAL_DIR = "Backup/l253_anchor_20260930"
REAL_GOOD = REAL_DIR + "/anchor_delivery_3.json"      # QUOTABLE：10.923、全 rep 1.097、kept 1.023
REAL_UNSTABLE = REAL_DIR + "/anchor_delivery_2.json"  # 同夜同形：8.486、全 rep 2.877 ⇒ UNSTABLE
REAL_RATE = "10.923"

BAD_MSG = "decode 13.9 t/s"
GOOD_MSG = "decode 10.923 t/s（`Backup/green.json`）交付錨點"
NOTQ_MSG = "decode 10.923 t/s（`Backup/dirty.json`）"


class Runner:
    def __init__(self, repo: Path, hooks: Path, remote: Path):
        self.repo, self.hooks, self.remote = repo, hooks, remote
        self.results: list[tuple[str, bool, str]] = []

    def git(self, *args, cwd=None, env=None, strict=False):
        e = dict(os.environ)
        e["GIT_CONFIG_GLOBAL"] = os.devnull   # 不讓這台機器的 git 設定（簽章／hooksPath／身分）滲進來
        e["GIT_CONFIG_SYSTEM"] = os.devnull
        e["GIT_TERMINAL_PROMPT"] = "0"
        e.update(env or {})
        cmd = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost",
               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=%s" % self.hooks]
        r = subprocess.run(cmd + list(args), cwd=str(cwd or self.repo), env=e,
                           capture_output=True, text=True)
        if strict and r.returncode != 0:
            raise SystemExit("git %s 失敗：%s%s" % (" ".join(args), r.stdout, r.stderr))
        return r

    def check(self, name: str, cond: bool, why: str = "") -> None:
        self.results.append((name, bool(cond), why))

    def tool_real(self, *args):
        """跑**本樹**那一份工具：scratch repo 裡的那份在這一刻是缺席狀態，而 hooks 目錄是共用的。"""
        return subprocess.run([sys.executable, str(ROOT / TOOL), *args],
                              cwd=str(ROOT), capture_output=True, text=True)

    def tool(self, *args, strict=False):
        r = subprocess.run([sys.executable, str(self.repo / TOOL), *args],
                           cwd=str(self.repo), capture_output=True, text=True)
        if strict and r.returncode != 0:
            raise SystemExit("工具 %s 失敗：%s%s" % (" ".join(args), r.stdout, r.stderr))
        return r


def build(tmp: Path) -> Runner:
    repo, hooks, remote = tmp / "repo", tmp / "hooks", tmp / "remote.git"
    (repo / "scripts/check").mkdir(parents=True)
    (repo / "Backup").mkdir()
    for rel in (TOOL,) + DEPS:
        shutil.copy2(ROOT / rel, repo / rel)
    shutil.copytree(ROOT / "scripts/check/hooks", repo / "scripts/check/hooks")
    (repo / "Backup/green.json").write_text(json.dumps(GREEN), encoding="utf-8")
    (repo / "Backup/dirty.json").write_text(json.dumps(DIRTY), encoding="utf-8")
    src = ROOT / REAL_DIR
    real = (src / "anchor_delivery_2.json").exists() and (src / "anchor_delivery_3.json").exists()
    if real:
        shutil.copytree(src, repo / REAL_DIR)
    r = Runner(repo, hooks, remote)
    r.real = real
    r.git("init", "-q", "-b", "main", ".", strict=True)
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    r.git("init", "-q", "-b", "main", "--bare", str(remote), strict=True)
    r.git("remote", "add", "origin", str(remote), strict=True)
    r.check("安裝：--install-hook（含出生戳記）", r.tool("--install-hook", "--hooks-dir", str(hooks), strict=True).returncode == 0
            and (hooks / "commit-msg").exists() and (hooks / "pre-push").exists()
            and (hooks / "doc_claim_gate.since").exists())
    return r


def run_cases(r: Runner) -> None:
    r.git("add", "f.txt", strict=True)
    # 1) commit-msg：無出處 ⇒ commit 停住
    c = r.git("commit", "-m", BAD_MSG)
    r.check("commit：無出處的 t/s ⇒ 擋", c.returncode != 0 and "沒有出處" in (c.stdout + c.stderr),
            "rc=%d" % c.returncode)
    # 2) 補上出處 ⇒ commit 成立，而且乾淨時沒有輸出（乾淨的掛勾不吵）
    c = r.git("commit", "-m", GOOD_MSG)
    r.check("commit：有出處且可引用 ⇒ 放行（且無輸出）",
            c.returncode == 0 and c.stderr.strip() == "", "rc=%d" % c.returncode)
    # 真產物那一段要在**乾淨的分支點**上跑：上面那些刻意壞掉的 commit（工具缺席時被放行的）
    # 還在本機歷史裡，任何 `--not --remotes` 的掃描都會把它們一起撈進來。
    r.base = (r.git("rev-parse", "HEAD").stdout or "").strip()
    c = r.git("commit", "--allow-empty", "-m", NOTQ_MSG)
    r.check("commit：出處對得上但視窗髒（swap）⇒ 擋",
            c.returncode != 0 and "NOTQ" in (c.stdout + c.stderr), "rc=%d" % c.returncode)
    # 3) 繞過 commit-msg 的壞訊息 ⇒ 由 pre-push 補刀
    r.git("commit", "--no-verify", "-q", "--allow-empty", "-m", BAD_MSG, strict=True)
    p = r.git("push", "origin", "main")
    r.check("push：--no-verify 硬塞的壞訊息 ⇒ 擋",
            p.returncode != 0 and "13.9" in (p.stdout + p.stderr), "rc=%d" % p.returncode)
    # 4) 同一筆 amend 成有出處 ⇒ 推得出去
    r.git("commit", "--no-verify", "-q", "--allow-empty", "--amend", "-m", GOOD_MSG, strict=True)
    p = r.git("push", "origin", "main")
    r.check("push：補了出處 ⇒ 放行（新分支）", p.returncode == 0, "rc=%d" % p.returncode)
    # 5) 工具缺席 ⇒ 警告放行，而且**留痕**（讓後面的 --hooks-status 看得見這次放行）
    tool = r.repo / TOOL
    keep = tool.read_text(encoding="utf-8")
    tool.unlink()
    c = r.git("commit", "--allow-empty", "-m", BAD_MSG)
    miss = r.hooks / "doc_claim_gate.missing"
    r.check("工具缺席：警告放行 ＋ 留下痕跡", c.returncode == 0 and "skipped" in c.stderr and miss.exists(),
            "rc=%d" % c.returncode)
    # 由**另一個 worktree** 那份工具來問（工具在、hooks 目錄共用）——
    # 這正是留痕要解決的場景：當時那台終端早就關了。
    s = r.tool_real("--hooks-status", "--hooks-dir", str(r.hooks))
    r.check("--hooks-status：把那次放行列出來（不再只活在 stderr 裡）",
            s.returncode == 0 and "缺席" in s.stdout, s.stdout.strip().splitlines()[-1] if s.stdout else "")
    # 6) DOC_CLAIM_STRICT=1 ⇒ 缺席即拒收
    c = r.git("commit", "--allow-empty", "-m", "clean message", env={"DOC_CLAIM_STRICT": "1"})
    r.check("工具缺席 ＋ DOC_CLAIM_STRICT=1 ⇒ 拒收", c.returncode != 0, "rc=%d" % c.returncode)
    tool.write_text(keep, encoding="utf-8")
    tool.chmod(0o755)
    # 7) 重裝把痕跡清掉（工具回來了 ⇒ 上一次放行不再是待處理狀態）
    r.tool("--install-hook", "--hooks-dir", str(r.hooks), strict=True)
    r.check("重裝：清掉缺席痕跡", not miss.exists())


def run_real_cases(r: Runner) -> None:
    """真產物那一段：同一支臂三場（prod-new ＋ harness bench ＋ 交付 cell），差別只有逐 rep 離散。"""
    if not getattr(r, "real", False):
        print("  [SKIP] 真產物那一段：%s 不在這棵樹（synthetic 段仍會跑；不假裝 PASS）" % REAL_DIR)
        return
    r.git("checkout", "-q", "-b", "real", getattr(r, "base", "HEAD"), strict=True)
    r.git("add", "-A", strict=True)
    r.git("commit", "-q", "-m", "把那一夜的 harness 產物放進來", strict=True)
    # 1) 無出處（數字本身是真的：10.923 就是那支臂的讀數）
    c = r.git("commit", "--allow-empty", "-m", "decode %s t/s" % REAL_RATE)
    r.check("真產物：無出處 ⇒ 擋（prod-new ＋ harness bench，不是 synthetic fixture）",
            c.returncode != 0 and "沒有出處" in (c.stdout + c.stderr), "rc=%d" % c.returncode)
    # 2) 指名可引用的那一場 ⇒ 放行，而且乾淨時沒有輸出
    good = "decode %s t/s（`%s`）交付錨點" % (REAL_RATE, REAL_GOOD)
    c = r.git("commit", "--allow-empty", "-m", good)
    r.check("真產物：指名 anchor_delivery_3（QUOTABLE）⇒ 放行（且無輸出）",
            c.returncode == 0 and c.stderr.strip() == "", "rc=%d" % c.returncode)
    # 3) 同一夜、同一格、出處也對得上，但那一場逐 rep 2.877 ⇒ 不可引用
    c = r.git("commit", "--allow-empty", "-m", "decode 8.486 t/s（`%s`）" % REAL_UNSTABLE)
    out = c.stdout + c.stderr
    r.check("真產物：同夜的 UNSTABLE 那一場 ⇒ 擋，且點名是哪一場（2.877）",
            c.returncode != 0 and "NOTQ" in out and "2.877" in out, "rc=%d" % c.returncode)
    # 4) 繞過 commit-msg 也繞不過 pre-push（這裡點名的數字是真的）
    r.git("commit", "--no-verify", "-q", "--allow-empty", "-m", "decode %s t/s" % REAL_RATE,
          strict=True)
    pp = r.git("push", "origin", "real")
    r.check("真產物：--no-verify 硬塞 ⇒ pre-push 點名那個 commit 的 %s t/s" % REAL_RATE,
            pp.returncode != 0 and REAL_RATE in (pp.stdout + pp.stderr), "rc=%d" % pp.returncode)
    # 5) 同一筆 amend 成有出處 ⇒ 推得出去
    r.git("commit", "--no-verify", "-q", "--allow-empty", "--amend", "-m", good, strict=True)
    pp = r.git("push", "origin", "real")
    r.check("真產物：amend 成有出處 ⇒ 推得出去", pp.returncode == 0, "rc=%d" % pp.returncode)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true", help="留下 scratch repo（除錯用）")
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="docgate_e2e_"))
    try:
        r = build(tmp)
        run_cases(r)
        run_real_cases(r)
    finally:
        if a.keep:
            print("scratch 留在 %s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    for name, ok, why in r.results:
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  (%s)" % why) if why else ""))
    bad = [n for n, ok, _ in r.results if not ok]
    print("hook e2e %d/%d" % (len(r.results) - len(bad), len(r.results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
