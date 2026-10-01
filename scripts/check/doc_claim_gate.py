#!/usr/bin/env python3
"""交付文件的數字要有主：`docs/` 與 commit 訊息裡的 t/s，必須對得上一個**通過閘門的產物**。

三條規則（跑前寫死；判準向既有的閘門要，不重寫）
------------------------------------------------
**B 綁定**：同一行**恰好一個小數 `t/s`** ＋ **恰好一個 `.json` 產物** ⇒ 那兩者是同一個主張，必須對得上：
產物存在、它是 bench 產物（有 `rows`）、裡面有一列的 `avg_ts` 在**文件寫出的位數**上等於它，
而且那一列在 `quote_gate` 判 `QUOTABLE`。任一不成立 ⇒ 紅。
 * 整數 `t/s`（`25 t/s`）與非 `.json` 指標（`.log`／`.yaml`）**不綁**：在語料裡它們壓倒性是
   「目標／門檻／原文 log」而不是量測主張。這條是**誤報防護**，不是放寬。
**U 無主**：**交付型文件**（檔名含 `DELIVERY`／`VERDICT`／`REPORT`／`WHITEPAPER`／`CERT`）裡的每一個
 `t/s` 必須（a）被 B 綁定，或（b）所在段落帶「非主張」標記，或（c）登記在凍結帳上。其餘 ⇒ 紅。
**C commit**：訊息逐行套 B；未綁定的登記在帳上（sha 不會變 ⇒ 歷史的凍結是一次性的，**新 commit 立刻紅**）。

範圍分層（不是一刀切）
--------------------
* 交付面（預設，違規 ⇒ rc=1）：檔名符合交付型樣式、且日期 ≥ `--since`（預設 `2026-09-25`＝
  `MEASUREMENT_CONTRACT` 生效日）。更早的 dated 產物是歸檔面 —— 專案慣例「dated 產物不回改」。
* 歸檔面（`--all-docs`）：只稽核、不影響 rc。

為什麼要有凍結帳：`docs/` 有一座 09-25 以前累積的債。把債一次改光要重寫別人的判決書（那些是 dated 產物），
所以帳面**看得見**、**只能往下走**（`--update-ledger` 只加新的、`--prune-ledger` 刪已結清的），
而**沒登記的一律紅** —— 新數字沒有出處就進不了交付文件。

用法
----
    python3 scripts/check/doc_claim_gate.py                  # 交付面（違規 ⇒ rc=1）
    python3 scripts/check/doc_claim_gate.py --all-docs       # 含歸檔面稽核
    python3 scripts/check/doc_claim_gate.py --commits HEAD~1..HEAD      # 推送前（判定）
    python3 scripts/check/doc_claim_gate.py --commits-audit HEAD        # 歷史（只稽核）
    python3 scripts/check/doc_claim_gate.py --update-ledger [--why "…"]
    python3 scripts/check/doc_claim_gate.py --selftest

掛勾（commit 當下就攔；見設計日誌 §77）
--------------------------------------
    python3 scripts/check/doc_claim_gate.py --install-hook     # commit-msg ＋ pre-push（不覆蓋別人的）
    python3 scripts/check/doc_claim_gate.py --hooks-status     # 裝了沒（rc 一律 0；掛勾不進版控）
    python3 scripts/check/doc_claim_gate.py --uninstall-hook   # 移除（有 .chain 會還原）
    python3 scripts/check/doc_claim_gate.py --message-file FILE # git 交過來的那一個訊息檔
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 判準的單一來源：本工具不自己判「可不可引用」，也不自己切段落。
from quote_gate import scan as _qg_scan            # noqa: E402
from void_number_check import expand, paragraph_at  # noqa: E402

DEFAULT_LEDGER = ROOT / "scripts/check/doc_claim_ledger.json"
DEFAULT_SINCE = "2026-09-25"

# 交付型檔名（判斷「這一頁是不是要對外報數字」）
DELIVERY_NAME_RE = re.compile(r"(DELIVERY|VERDICT|REPORT|WHITEPAPER|CERT)", re.I)
DATE_IN_NAME_RE = re.compile(r"(20\d\d)[-_]?(\d\d)[-_]?(\d\d)")

# 小數 t/s：整數不綁（見檔頭誤報防護）。
# `(?:\s*±\s*\d+\.\d+)?`：`tg 10.923 ± 1.62 t/s` 這種寫法的**速率是第一個數**、`1.62` 是它的展幅
#   （單位只寫在最後）。不吃掉這個形式，正則會把 `1.62 t/s` 當成速率、而 10.923 完全看不到 ——
#   2026-09-30 就是這樣量錯的（`K3_PAIR_CERT_V2` 的 `tg 11.02 ± 1.62 t/s`）。
DECL_TS_RE = re.compile(r"(?<![\d.])(\d+\.\d+)(?:\s*±\s*\d+\.\d+)?\s*"
                        r"(?:t/s|tok/s|token/s|tokens/s)")
# 產物指標：只收 .json（.log 是原文、.yaml 是設定）
ARTIFACT_RE = re.compile(r"`?((?:Backup|docs)/[A-Za-z0-9_./\-]+\.json)`?")

# 「非主張」標記：出現這些詞的段落，那個 t/s 不是「我們的讀數」。
# `不可與`：MTP-on／MTP-off 的**輸出函數標籤**（`MTP_CALIBER_REDEFINE_CHARTER` B2 強制的那段
#   「不可與 MTP-off 互比」）——那句話的功能就是宣告這個數字不能拿來比，所以它是非主張標記。
# `非 §5.0`：本 repo 自己的「非認可口徑」寫法（operator 2026-09-28 的認可口徑令）。
NON_CLAIM_LABELS = (
    "不可與", "非 §5.0",
    "目標", "門檻", "需求", "預算", "推算", "推估", "估算", "外推", "模型", "定價",
    "上界", "上限", "下限", "天花板", "作廢", "廢棄", "不可引用", "不得引用", "不主張",
    "非量測", "不是量測", "未驗", "未證", "未認證", "待測", "歷史", "轉引", "假設",
    "DIRTY", "VOID", "POLLUTED", "target", "to beat", "遠低於", "到不了",
)

# 「認賬」標記：句子自己說出「這個數字不可用」。它與上面的**類別**標記（目標／推算／轉引…）不同 ——
# 類別標記說「這根本不是讀數」，認賬標記說「它是讀數，但它不算」。引一個被判死的產物**並且在同一段說出
# 不可引用**，是這個 repo 既有的寫法（`void_number_check` 的作廢標記就是同一件事），所以綁定違規的
# 免紅條件是「說出口」，不是「換一個檔」—— 沒說出口的才紅。
ACK_MARKERS = ("不可引用", "不得引用", "不主張", "作廢", "廢棄", "降級", "排除",
               "DIRTY", "VOID", "非 §5.0", "不可與")

V_OK, V_NA, V_MISS, V_NOMATCH, V_NOTQ, V_NOBACKING = (
    "OK", "N/A", "MISS", "NOMATCH", "NOTQ", "NOBACKING")


# ───────────────────────── 綁定判定 ─────────────────────────

_scan_cache: dict[str, list | None] = {}


def artifact_rows(rel: str):
    """回 `quote_gate.scan()` 的列；產物不存在 ⇒ None。結果快取。"""
    if rel not in _scan_cache:
        p = ROOT / rel
        _scan_cache[rel] = _qg_scan([str(p)]) if p.exists() else None
    return _scan_cache[rel]


def decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0


def judge_binding(num: str, rel: str) -> tuple[str, str]:
    """(verdict, why)。`N/A` ＝ 那個檔不是 bench 產物（結構判定，不算違規但列出來）。"""
    rows = artifact_rows(rel)
    if rows is None:
        return V_MISS, "產物不存在"
    if not rows:
        return V_NA, "不是 bench 產物（沒有 rows）—— 這一條無法用它判，需人工看"
    want = float(num)
    cand = [r for r in rows
            if r.get("metrics") and r["metrics"].get("avg") is not None
            and round(r["metrics"]["avg"], decimals(num)) == want]
    if not cand:
        vals = [round(r["metrics"]["avg"], 3) for r in rows
                if r.get("metrics") and r["metrics"].get("avg") is not None]
        return V_NOMATCH, "產物裡沒有這個數（有 %s）" % (vals[:6] or "—")
    if any(r["verdict"] == "QUOTABLE" for r in cand):
        return V_OK, "對得上且 QUOTABLE"
    return V_NOTQ, "對得上但 %s：%s" % (cand[0]["verdict"], "；".join(cand[0].get("reasons") or []))


SPREAD_PREFIXES = ("± ", "±", "+/-", "±<code>")


def is_spread(text: str, start: int) -> bool:
    """這個數是不是 `±` 後面那個離散值（`tg 11.02 ± 1.62 t/s` 的 1.62 不是速率）。

    實測（2026-09-30）：不擋這一條，`K3_PAIR_CERT_V2_EXECUTABILITY` 的
    「`tg 11.02 ± 1.62 t/s`」會被讀成「速率 1.62 t/s 沒有出處」——純誤報。
    """
    head = text[max(0, start - 4):start]
    return any(head.endswith(pfx) for pfx in SPREAD_PREFIXES) or "+/-" in head


def find_rates(text: str):
    """yield (lineno, num, start) —— 這一行的**速率**（已排除 ± 展幅）。

    `start` 是**全文**的位移（不是行內位移）：`paragraph_at()` 要全文位移才切得對段落。
    （2026-09-30 踩過：傳行內位移 ⇒ 段落切在檔案開頭 ⇒ 標記全部看不到 ⇒ 無主讀數從 11 變 31。）
    """
    base = 0
    for i, ln in enumerate(text.split("\n")):
        for m in DECL_TS_RE.finditer(ln):
            if is_spread(ln, m.start()):
                continue
            yield i + 1, m.group(1), base + m.start()
        base += len(ln) + 1


def scan_lines(text: str):
    """逐行找「一數一檔」的綁定。yield (lineno, num, artifact)。"""
    rates = {}
    for lineno, num, _start in find_rates(text):
        rates.setdefault(lineno, []).append(num)
    for i, ln in enumerate(text.split("\n")):
        nums = rates.get(i + 1, [])
        arts = {a.group(1) for a in ARTIFACT_RE.finditer(ln)}
        if len(nums) != 1 or len(arts) != 1:
            continue                      # 一數一檔才算綁定（一數多檔是密集敘述，綁了會誤判）
        yield i + 1, nums[0], arts.pop()


# ───────────────────────── 帳 ─────────────────────────

def para_key(rel: str, num: str, para: str) -> str:
    h = hashlib.sha1(re.sub(r"\s+", " ", para).strip().encode("utf-8")).hexdigest()[:8]
    return "%s|%s|%s" % (rel, num, h)


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"rule": "", "entries": []}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {"rule": "", "entries": [], "_broken": True}
    if isinstance(d, list):
        d = {"rule": "", "entries": d}
    d.setdefault("entries", [])
    return d


def ledger_keys(ledger: dict) -> set[str]:
    return {str(e.get("key")) for e in ledger.get("entries", []) if isinstance(e, dict)}


def save_ledger(path: Path, ledger: dict) -> None:
    ledger["entries"] = sorted(
        (e for e in ledger.get("entries", []) if isinstance(e, dict)),
        key=lambda e: (str(e.get("kind")), str(e.get("key"))))
    path.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ───────────────────────── 掃描 ─────────────────────────

def name_in_scope(p: Path, since: str) -> bool:
    if not DELIVERY_NAME_RE.search(p.name):
        return False
    m = DATE_IN_NAME_RE.search(p.name)
    if not m:
        return True                       # 沒日期 ⇒ 當成現行件（新檔一律進範圍）
    try:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return True
    return d >= _dt.date(*[int(x) for x in since.split("-")])


def in_scope_docs(since: str, globs=None, all_docs: bool = False) -> tuple[list[Path], list[Path]]:
    pats = globs or ("docs/*.md", "docs/*.html")
    files = [p for p in expand(ROOT, pats) if "archive" not in str(p)]
    if all_docs:
        return files, []
    return [p for p in files if name_in_scope(p, since)], files


def scan_doc(p: Path, keys: set[str]) -> tuple[list, list, list, list]:
    """回 (findings, no_backing, acked, na)。

    * `findings`：綁定違規（有指標但對不上／不可引用，且同一段**沒有**認賬）
    * `no_backing`：無主讀數（沒出處、沒類別標記、也沒登記在帳上）
    * `acked`：綁到被判死的產物，但同一段把「不可引用」說出來了 ⇒ 不判違規，只列出來
    * `na`：綁到不是 bench 的產物（`quote_gate` 無從判）⇒ 不判違規，只列出來（免得「綁一個別名」變成靜默後門）
    """
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], [], [], []
    rel = str(p.relative_to(ROOT))
    findings, noback, acked, na = [], [], [], []
    starts = {ln: st for ln, _n, st in find_rates(text)}
    bound_spans = set()
    for lineno, num, art in scan_lines(text):
        bound_spans.add(lineno)
        v, why = judge_binding(num, art)
        if v == V_OK:
            continue
        if v == V_NA:
            na.append((rel, lineno, num, art, why))
            continue
        para = paragraph_at(text, starts.get(lineno, 0))
        if any(k in para for k in ACK_MARKERS):
            acked.append((rel, lineno, num, art, v, why))
            continue
        findings.append((rel, lineno, num, art, v, why))
    # U：無主讀數（只對交付型檔名，且只算沒被綁定的那些）
    if DELIVERY_NAME_RE.search(p.name):
        for lineno, num, start in find_rates(text):
            if lineno in bound_spans:
                continue
            para = paragraph_at(text, start)
            if any(k in para for k in NON_CLAIM_LABELS):
                continue
            # 已登記的**也收進來**（帳面要看得出「還掛著幾筆」），只是在 run() 裡不算違規。
            noback.append((rel, lineno, num, para_key(rel, num, para), para))
    return findings, noback, acked, na


COMMIT_FMT = "%H%x09%cI%x09%s%x09%b%x1e"

# 閘門上線日：訊息寫在這一天之前的 commit ⇒ 只列不判。
# WHY 這一條存在：閘門上線前的那批訊息已經在歷史裡，改寫歷史不是這支工具的職權（repo 的既有慣例是
# 「dated 產物不回改」；§76 的歷史 228 筆就是只稽核）。上線之後的每一筆都判 —— commit-msg 掛勾是
# 第一道，pre-push 這道只負責把「繞過掛勾（--no-verify）或從別的 worktree 進來」的補起來。
COMMIT_SINCE_DEFAULT = "2026-09-30"

# 掛勾會在安裝的那一刻把「閘門出生時間」寫進 hook 目錄旁的這支檔案，pre-push 掃描用它當底線。
# WHY 要一個戳記而不是只寫日期：這支閘門是 2026-09-30 當天做好的，而當天稍早的 commit 訊息也在歷史裡
# —— 只寫日期會把「今天早上（閘門還不存在時）寫的訊息」也判進去，那就變成對不可能回改的東西開紅單。
# 戳記一旦寫下就**不再前移**（重裝不會把底線往後推），否則每一次重裝都是一次靜默的赦免。
HOOK_SINCE_FILE = "doc_claim_gate.since"

# 工具缺席時掛勾留下的戳記（`<時間> <哪一支掛勾> <worktree 路徑>`）。這是「放行」的證據，
# 不是「判過」的證據：沒有它，缺席那一段就只活在當時那個人的 stderr 裡，
# 而事後看 `--hooks-status` 的人會以為每一次 commit 都被判過。
HOOK_MISSING_FILE = "doc_claim_gate.missing"

# 上線前的 commit 仍要能被列出來（audit），所以回傳裡帶日期。
def read_commits(revspec: str) -> list[tuple[str, str, str]]:
    """回 [(sha, committer_date, message)]。"""
    out = subprocess.run(["git", "log", "--format=" + COMMIT_FMT, *_rev_args(revspec)],
                         cwd=str(ROOT), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("git log 失敗：%s" % (out.stderr.strip()[:200]))
    res = []
    for chunk in out.stdout.split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("\t")
        res.append((parts[0].strip(), parts[1].strip(), "\n".join(parts[2:])))
    return res


def _iso_dt(s: str):
    """ISO 日期或日期時間 → aware datetime（none = 讀不懂）。"""
    t = (s or "").strip()
    if not t:
        return None
    try:
        dt = _dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
    return dt


def commit_since_default() -> str:
    """底線的**單一來源**：掛勾目錄裡的出生戳記（裝了就有），否則退回常數。

    WHY 不是「CLI 用常數、掛勾用戳記」：同一個問題有兩個答案，就會有人拿到比較鬆的那一個。
    手跑 `--commits HEAD --not --remotes` 時看到的 rc 必須跟 push 當下看到的 rc 一樣。
    """
    try:
        stamp = _hooks_dir() / HOOK_SINCE_FILE
        if stamp.is_file():
            return stamp.read_text(encoding="utf-8").strip() or COMMIT_SINCE_DEFAULT
    except SystemExit:
        pass
    return COMMIT_SINCE_DEFAULT


def split_by_cutoff(commits: list[tuple[str, str, str]], cutoff: str) -> tuple[list, list]:
    """(上線後, 上線前)：以 committer date 比對。

    WHY 用 committer date 而不是 author date：閘門管的是「這筆是什麼時候進來的」，amend／rebase 之後
    author date 可以很舊 —— 用 author date 會讓一次改寫把 enforce 悄悄變成 audit。

    WHY 比真的日期而不是比字串：`%cI` 帶時區位移，`2026-09-30T02:45:00+08:00` 與
    `2026-09-29T22:45:00+00:00` 是同一個時刻卻是不同字串 ⇒ 字串比大小會看錯邊。
    讀不懂的 commit 日期 ⇒ 當**上線後**（fail-closed：判它，不靠一個壞欄位放行）。
    """
    dt = _iso_dt(cutoff)
    if dt is None:
        raise SystemExit("--commit-since 讀不懂：%r（要 ISO 日期或日期時間）" % cutoff)
    fresh, old = [], []
    for sha, date, msg in commits:
        c = _iso_dt(date)
        (old if (c is not None and c < dt) else fresh).append((sha, date, msg))
    return fresh, old


def scan_commit_text(sha: str, text: str) -> tuple[list, list, list, list]:
    """commit 訊息逐行套 B；未綁定的整條訊息算一筆無主（比行號穩定，且訊息短）。"""
    findings, noback, acked, na = [], [], [], []
    starts = {ln: st for ln, _n, st in find_rates(text)}
    for lineno, num, art in scan_lines(text):
        v, why = judge_binding(num, art)
        if v == V_OK:
            continue
        tag = "commit:%s" % sha[:8]
        if v == V_NA:
            na.append((tag, lineno, num, art, why))
            continue
        if any(k in paragraph_at(text, starts.get(lineno, 0)) for k in ACK_MARKERS):
            acked.append((tag, lineno, num, art, v, why))
            continue
        findings.append((tag, lineno, num, art, v, why))
    for ln, num, start in find_rates(text):
        if any(ln == f[1] and num == f[2] for f in findings):
            continue
        if any(ln == l and num == n for l, n, _a in scan_lines(text)):
            continue
        para = paragraph_at(text, start)
        if any(k in para for k in NON_CLAIM_LABELS):
            continue
        noback.append(("commit:%s" % sha[:8], ln, num, "commit|%s|%s" % (sha, num), para))
    return findings, noback, acked, na


def _rev_args(revspec: str) -> list[str]:
    """`git log` 的參數：多詞的 revspec（例：`HEAD --not --remotes`）要拆開才成立。

    WHY 用 shlex 而不是自己 split：`origin/dev..HEAD` 是單詞、上面那串是三詞，讓 shell 的規則去切，
    才不會在引號／空白上自己發明一套。pre-push hook 的「全新分支」那一支就是靠它。
    """
    try:
        parts = shlex.split(revspec)
    except ValueError:
        return [revspec]
    return parts or [revspec]


def run_message_file(path: str) -> int:
    """commit-msg hook 模式：判 git 交過來的那個訊息檔。

    WHY 這一條**不看凍結帳**：帳的用途是「上線前既存、且依慣例不回改的 dated 文字」。
    新的 commit 訊息不屬於那個集合 —— 一旦吃帳，被凍結的那幾筆就變成「這句話可以永遠再寫一次」，
    閘門就從「新的不可以不合規」被偷換成「舊的白名單」。所以 hook 模式：零豁免。
    """
    p = Path(path)
    if not p.is_file():
        print("VERDICT: FAIL — commit-msg 的訊息檔不存在：%s" % path)
        return 1
    text = p.read_text(encoding="utf-8", errors="replace")
    findings, nobacks, _acked, _na = scan_commit_text("hook", text)
    if not findings and not nobacks:
        return 0                      # 乾淨就安靜：hook 成功時不該有輸出
    for _rel, lineno, num, art, v, why in findings:
        print("  ✗ 第 %d 行  %s t/s ← %s  [%s] %s" % (lineno, num, art, v, why))
    for _rel, lineno, num, _key, para in nobacks:
        print("  ⛔ 第 %d 行  %s t/s 沒有出處" % (lineno, num))
        print("       …%s…" % re.sub(r"\s+", " ", para)[:100])
    print("VERDICT: FAIL — commit 訊息裡的 t/s 必須指向一個通過 quote_gate 的產物"
          "（同一行寫出產物路徑，或把那句話改成非主張）")
    return 1


def run(args) -> int:
    ledger_path = Path(args.ledger) if args.ledger else DEFAULT_LEDGER
    ledger = load_ledger(ledger_path)
    keys = ledger_keys(ledger)

    since = args.since
    if args.all_docs:
        scoped, allfiles = in_scope_docs(since, args.glob, all_docs=False)
        print("範圍：歸檔面稽核（%d 檔，含非交付型與 09-25 前的 dated 產物；違規只列不判）" % len(allfiles))
        scoped = allfiles
    else:
        scoped, allfiles = in_scope_docs(since, args.glob)
        print("範圍：交付面（%d 檔；檔名含 %s 且日期 ≥ %s）"
              % (len(scoped), DELIVERY_NAME_RE.pattern, since))

    findings, nobacks, acked, na_rows = [], [], [], []
    for p in scoped:
        f, n, a, na = scan_doc(p, keys)
        findings += f
        nobacks += n
        acked += a
        na_rows += na

    # commit 訊息：`--commits RANGE` 判（rc 受影響），`--commits-audit RANGE` 只稽核。
    # WHY 不把歷史 commit 進帳：sha 一固定，228 筆歷史就永遠凍在那裡，帳面反而失去「只能往下走」的意義。
    # commit 的「範圍」本身就是它的帳 —— 推送前掃 `origin/dev..HEAD` 就是它的閘門。
    audit_nobacks = []
    audit_findings = []
    for revspec, enforcing in ((args.commits, True), (args.commits_audit, False)):
        if not revspec:
            continue
        commits = read_commits(revspec)
        cutoff = args.commit_since or commit_since_default()
        fresh, old = split_by_cutoff(commits, cutoff) if enforcing else (commits, [])
        print("commit 範圍：%s（%d 筆；%s）"
              % (revspec, len(commits), "判" if enforcing else "只稽核"))
        if old:
            print("  · 閘門上線（%s）之前的 %d 筆 ⇒ 只列不判：訊息已在歷史裡，改寫不是這支工具的職權（§77）"
                  % (cutoff, len(old)))
        for sha, _date, msg in fresh:
            f, n, a, na = scan_commit_text(sha, msg)
            findings += f
            acked += a
            na_rows += na
            (nobacks if enforcing else audit_nobacks).extend(n)
        for sha, _date, msg in old:
            f, n, a, na = scan_commit_text(sha, msg)
            acked += a
            na_rows += na
            audit_findings += f
            audit_nobacks += n

    print()
    if acked:
        print("=== 綁定已認賬（引了被判死的產物，但同一段說出了「不可引用」⇒ 不判違規）===")
        for rel, lineno, num, art, v, why in acked:
            print("  · %s:%d  %s t/s ← %s  [%s] %s" % (rel, lineno, num, art, v, why))
    if na_rows:
        print("=== 綁定但閘門無從判（不是 bench 產物）===")
        for rel, lineno, num, art, why in na_rows:
            print("  · %s:%d  %s t/s ← %s  %s" % (rel, lineno, num, art, why))

    if findings:
        print("=== 綁定違規（有指標但對不上／不可引用）===")
        for rel, lineno, num, art, v, why in findings:
            print("  ✗ %s:%d  %s t/s ← %s  [%s] %s" % (rel, lineno, num, art, v, why))
    else:
        print("=== 綁定違規：無 ===")

    if audit_nobacks or audit_findings:
        print()
        print("=== commit 訊息稽核（只列不判；歷史不改寫）===")
        for rel, lineno, num, art, v, why in audit_findings:
            print("  · %s  %s t/s ← %s  [%s]" % (rel, num, art, v))
        for rel, lineno, num, _key, _para in audit_nobacks:
            print("  · %s  %s t/s" % (rel, num))

    if nobacks:
        print()
        registered = [n for n in nobacks if n[3] in keys]
        print("=== 無主讀數（沒有出處也沒有非主張標記）===")
        for rel, lineno, num, key, para in nobacks:
            mark = "·" if key in keys else "⛔"
            tail = "（帳上已登記）" if key in keys else ""
            print("  %s %s:%d  %s t/s%s" % (mark, rel, lineno, num, tail))
            if mark == "⛔":
                print("       key=%s" % key)
                print("       …%s…" % re.sub(r"\s+", " ", para)[:110])
        print("  小計：未登記 %d 筆／帳上已登記 %d 筆（已登記的列印出來，不會消失）"
              % (len(nobacks) - len(registered), len(registered)))

    # 帳面：已登記但已不再違規的 ⇒ 可結清
    live = {k for _r, _l, _n, k, _p in nobacks}
    stale = sorted(k for k in keys if k not in live and not k.startswith("commit|")
                   and k.split("|")[0].startswith("docs/"))
    print()
    print("帳：%d 筆（未結清的無主讀數 %d 筆；可結清 %d 筆）"
          % (len(keys), len(nobacks), len(stale)))

    if args.update_ledger:
        why = args.why or "2026-09-30 上線前既存（首次上線凍結，待逐條結清）"
        added = 0
        have = keys
        for rel, lineno, num, key, _p in nobacks:
            if key in have:
                continue
            if str(rel).startswith("commit:"):
                continue                      # commit 的帳是「範圍」，不進凍結帳（見上面的 WHY）
            have.add(key)
            ledger["entries"].append({"kind": "doc", "key": key, "why": why})
            added += 1
        for rel, lineno, num, art, v, why2 in findings:
            k = "bind|%s|%s|%s" % (rel, lineno, num)
            if k in have:
                continue
            have.add(k)
            ledger["entries"].append({"kind": "binding", "key": k, "why": why,
                                      "detail": "%s ← %s [%s] %s" % (num, art, v, why2)})
            added += 1
        ledger["rule"] = ("交付文件與 commit 訊息裡的 t/s 必須對得上一個通過 quote_gate 的產物；"
                          "本帳只凍結上線前既存者，未登記的一律紅。")
        save_ledger(ledger_path, ledger)
        print("帳已更新：+%d 筆 → %s" % (added, ledger_path.relative_to(ROOT)))
        return 0

    if args.prune_ledger:
        before = len(ledger["entries"])
        ledger["entries"] = [e for e in ledger["entries"]
                             if str(e.get("key")) not in set(stale)]
        save_ledger(ledger_path, ledger)
        print("帳已結清 %d 筆（%d → %d）"
              % (before - len(ledger["entries"]), before, len(ledger["entries"])))
        return 0

    # 未登記的才算違規
    hard = [n for n in nobacks if n[3] not in keys]
    hard_f = [f for f in findings if ("bind|%s|%s|%s" % (f[0], f[1], f[2])) not in keys]
    print()
    if hard or hard_f:
        print("VERDICT: FAIL — 未登記的無主讀數 %d 筆、綁定違規 %d 筆"
              % (len(hard), len(hard_f)))
        return 1
    print("VERDICT: PASS — 交付面沒有未登記的無主讀數，綁定全部對得上且可引用")
    return 0


# ───────────────────────── git hooks（commit 當下就攔） ─────────────────────────
#
# WHY 是 commit-msg 而不是只靠一條 CLI：判準早就有了，缺的是「什麼時候跑」。靠人記得跑的命令，
# 遲早會有一次沒跑 —— 而沒跑的那一次，訊息就永久留在歷史裡（sha 不能改，§76 的 C 條就是為此）。
#
# 三件刻意不做的事：
#   * **不覆蓋別人的 hook**：預設遇到既有的 commit-msg／pre-push 就拒裝（rc=1），並印出串接指令；
#     `--chain` 會把原檔保留成 `<name>.chain`，新的入口**先跑它**、再跑我們（別人的判準優先）。
#     pre-push 不支援串接：stdin 只有一份，兩支都讀會互相吃掉。
#   * **工具不在這個 worktree 時警告放行**（不是靜默放行，也不是 fail-closed）：hooks/ 由
#     git worktree **共用** ⇒ 一個 worktree 裝了，另一個（還沒同步到這支工具的）也會被叫到。
#     在那裡拒收＝用別人的工具缺席鎖死別的 worktree。要連缺席都拒收：DOC_CLAIM_STRICT=1。
#   * **hook 不進版控**（本來就進不了）：所以 `--hooks-status` 一律 rc=0、管線不得因它變紅。
#   * **缺席時留痕**：警告放行的那一行 stderr 只活在當時那個終端裡 ⇒ 模板會再寫一份
#     `doc_claim_gate.missing`（時間／哪一支／哪個 worktree），`--hooks-status` 事後印得出來，
#     `--install-hook` 工具回來時清掉。**放行 ≠ 判過**，這兩件事不能在回報面上長得一樣（§78）。

HOOK_MARK = "# doc_claim_gate v1"
HOOK_TEMPLATES = ROOT / "scripts/check/hooks"


def _hooks_dir(explicit: str | None = None) -> Path:
    """git 的 hooks 目錄。worktree 共用 common dir ⇒ 由 git 自己解析，不猜路徑。"""
    if explicit:
        return Path(explicit).resolve()
    out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                         cwd=str(ROOT), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("git rev-parse --git-common-dir 失敗：%s" % out.stderr.strip()[:200])
    p = Path(out.stdout.strip())
    if not p.is_absolute():
        p = Path(ROOT) / p
    return p.resolve() / "hooks"


def _hook_text(name: str, chained: bool) -> str:
    src = HOOK_TEMPLATES / (name + ".sh")
    body = src.read_text(encoding="utf-8")
    if "@@MARK@@" not in body:
        raise SystemExit("%s 沒有 @@MARK@@ 佔位符（模板壞了）" % src)
    body = body.replace("@@MARK@@", HOOK_MARK)
    if chained:
        snippet = ('chain="$(dirname "$0")/%s.chain"\n'
                   'if [ -x "$chain" ]; then "$chain" "$@" || exit $?; fi' % name)
    else:
        snippet = ""
    return body.replace("@@CHAIN@@", snippet)


def install_hooks(hooks: Path, chain: bool) -> int:
    hooks.mkdir(parents=True, exist_ok=True)
    stamp = hooks / HOOK_SINCE_FILE
    if not stamp.exists():
        stamp.write_text(_dt.datetime.now().astimezone().isoformat(timespec="seconds") + "\n",
                         encoding="utf-8")
        print("閘門出生時間：%s（%s；之後的 commit 訊息一律判，之前的只列）"
              % (stamp.read_text(encoding="utf-8").strip(), HOOK_SINCE_FILE))
    miss = hooks / HOOK_MISSING_FILE
    if miss.exists():
        miss.unlink()
        print("清掉工具缺席痕跡：%s（工具回來了 ⇒ 那一段放行不再是待處理狀態）" % HOOK_MISSING_FILE)
    rc = 0
    for name in ("commit-msg", "pre-push"):
        target, kept = hooks / name, hooks / (name + ".chain")
        if target.exists() and HOOK_MARK not in target.read_text(encoding="utf-8", errors="replace"):
            if not chain or name == "pre-push":
                print("拒絕覆蓋既有的 %s（不是這支工具裝的）。" % target)
                if name == "commit-msg":
                    print("  要保留它並串起來：python3 scripts/check/doc_claim_gate.py --install-hook --chain")
                rc = 1
                continue
            if kept.exists():
                print("拒絕：%s 已存在（可能是上次串接留下的）。請先人工確認。" % kept)
                rc = 1
                continue
            target.rename(kept)
            print("原 hook 保留為 %s（會先跑它）" % kept)
        target.write_text(_hook_text(name, kept.exists()), encoding="utf-8")
        target.chmod(0o755)
        print("已安裝 %s%s" % (target, "（串接）" if kept.exists() else ""))
    return rc


def uninstall_hooks(hooks: Path) -> int:
    rc = 0
    for name in ("commit-msg", "pre-push"):
        target, kept = hooks / name, hooks / (name + ".chain")
        if not target.exists():
            print("沒有 %s（略過）" % target)
            continue
        if HOOK_MARK not in target.read_text(encoding="utf-8", errors="replace"):
            print("不動 %s：不是這支工具裝的。" % target)
            rc = 1
            continue
        target.unlink()
        if name == "commit-msg" and (hooks / HOOK_SINCE_FILE).exists():
            (hooks / HOOK_SINCE_FILE).unlink()
            print("已移除 %s" % (hooks / HOOK_SINCE_FILE))
        if kept.exists():
            kept.rename(target)
            print("已移除本工具並還原原 hook：%s" % target)
        else:
            print("已移除 %s" % target)
    return rc


def hooks_status(hooks: Path) -> int:
    """只回報（rc 一律 0）：hook 不進版控 ⇒ 全新 clone 本來就沒有，不能讓它把管線弄紅。"""
    for name in ("commit-msg", "pre-push"):
        p = hooks / name
        if not p.exists():
            print("  · %-10s 未安裝（--install-hook 會裝）" % name)
        elif HOOK_MARK in p.read_text(encoding="utf-8", errors="replace"):
            print("  · %-10s 已安裝（本工具）" % name)
        else:
            print("  · %-10s 存在但不是本工具裝的（未接管）" % name)
    stamp = hooks / HOOK_SINCE_FILE
    if stamp.exists():
        print("  · %-10s %s（這個時間點之後的 commit 訊息 pre-push 會判）" % ("since", stamp.read_text(encoding="utf-8").strip()))
    else:
        print("  · %-10s 缺 ⇒ pre-push 用預設底線 %s（重裝掛勾會補上）" % ("since", COMMIT_SINCE_DEFAULT))
    miss = hooks / HOOK_MISSING_FILE
    if miss.exists():
        first = (miss.read_text(encoding="utf-8", errors="replace").strip().splitlines() or ["?"])[0]
        print("  · %-10s ⚠ 工具曾缺席：%s（那次 commit 是**放行**的，不是判過的）"
              % ("missing", first))
    return 0


# ───────────────────────── selftest ─────────────────────────

# 兩個 row 都是**真的**會被 quote_gate 判 QUOTABLE 的形狀（R1–R7 全過、R0 自洽）：
# 逐 rep 要與回報的 stddev 對得上（quote_gate 的 R0），所以 sd 是照 samples 反算後寫進去的。
GOOD_ARM = {
    "tag": "prod-new", "profile": "prod-new",
    "attribution": {"verdict": "none", "thermal_worst": "NOMINAL"},
    "cell": {"named_cell": "delivery"},
    "rows": [{"avg_ts": 10.923, "stddev_ts": 0.519, "samples_ts": [10.34, 11.34, 11.08],
              "n_prompt": 0, "n_gen": 128, "n_depth": 512},
             {"avg_ts": 11.3404, "stddev_ts": 0.0004, "samples_ts": [11.34, 11.3404, 11.3408],
              "n_prompt": 0, "n_gen": 128, "n_depth": 512}],
}
BAD_ARM = json.loads(json.dumps(GOOD_ARM))
BAD_ARM["attribution"] = {"verdict": "swap", "thermal_worst": "NOMINAL",
                          "why": "swap_growth=1515.07 MiB"}
NOROWS_ARM = {"tag": "prod-new", "profile": "prod-new", "wall_s": 12.0}


def selftest() -> int:
    global ROOT, _scan_cache
    ok: list[tuple[str, bool]] = []

    def chk(name: str, cond: bool) -> None:
        ok.append((name, bool(cond)))

    real_root = ROOT
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "docs").mkdir()
        (d / "Backup").mkdir()
        (d / "Backup/good.json").write_text(json.dumps([GOOD_ARM]), encoding="utf-8")
        (d / "Backup/bad.json").write_text(json.dumps([BAD_ARM]), encoding="utf-8")
        (d / "Backup/norows.json").write_text(json.dumps([NOROWS_ARM]), encoding="utf-8")
        ROOT = d
        _scan_cache = {}

        good = d / "docs/X_DELIVERY_2026-09-30.md"
        good.write_text("讀數 10.923 t/s（`Backup/good.json`）交付 cell。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("綁定：對得上且 QUOTABLE ⇒ 無違規", (f, n) == ([], []))

        good.write_text("讀數 10.923 t/s（`Backup/bad.json`）交付 cell。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("綁定：對得上但不可引用 ⇒ NOTQ",
            len(f) == 1 and f[0][4] == V_NOTQ)

        # 認賬（2026-09-30）：引一個被判死的產物、而且把「不可引用」說出來 ⇒ 不判違規，但要列出來。
        good.write_text("讀數 10.923 t/s（`Backup/bad.json`）——**不可引用**（窗口 swap）。\n",
                        encoding="utf-8")
        f, n, a, _na = scan_doc(good, set())
        chk("認賬：同一段寫出「不可引用」⇒ 不判違規，但列進 acked",
            (f, n) == ([], []) and len(a) == 1 and a[0][4] == V_NOTQ)

        good.write_text("讀數 10.923 t/s（`Backup/bad.json`）。\n", encoding="utf-8")
        f, n, a, _na = scan_doc(good, set())
        chk("認賬：同一顆產物、只差那句話 ⇒ 紅（免紅的條件是說出口）",
            len(f) == 1 and f[0][4] == V_NOTQ and a == [])

        good.write_text("讀數 12.5 t/s（`Backup/good.json`）交付 cell。\n", encoding="utf-8")
        f, _n, _a, _na = scan_doc(good, set())
        chk("綁定：產物裡沒有這個數 ⇒ NOMATCH", len(f) == 1 and f[0][4] == V_NOMATCH)

        good.write_text("讀數 10.923 t/s（`Backup/nope.json`）交付 cell。\n", encoding="utf-8")
        f, _n, _a, _na = scan_doc(good, set())
        chk("綁定：產物不存在 ⇒ MISS", len(f) == 1 and f[0][4] == V_MISS)

        good.write_text("讀數 10.923 t/s（`Backup/norows.json`）交付 cell。\n", encoding="utf-8")
        f, _n, _a, na = scan_doc(good, set())
        chk("綁定：不是 bench 產物 ⇒ N/A（列出但不判違規，且它仍算「有主」）",
            f == [] and _n == [] and len(na) == 1)

        good.write_text("讀數 10.923 t/s 是交付 cell 的錨點。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("無主：交付型文件沒出處 ⇒ 紅", f == [] and len(n) == 1 and n[0][2] == "10.923")

        good.write_text("目標 25.0 t/s 需要 step ≤ 40 ms。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("無主：帶「目標／門檻」標記 ⇒ 放行", (f, n) == ([], []))

        good.write_text("讀數 10.923 t/s 是交付 cell 的錨點。\n", encoding="utf-8")
        key = scan_doc(good, set())[1][0][3]
        f, n, _a, _na = scan_doc(good, {key})
        chk("無主：帳上已登記 ⇒ 掃描仍看得到它（帳面不會消失），但不算違規",
            f == [] and len(n) == 1 and n[0][3] == key)
        good.write_text("讀數 10.923 t/s 是交付 cell 的錨點。但這段話改了。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(good, {key})
        chk("無主：段落一改，帳上的舊鑰匙失效 ⇒ 又紅（不會無限沿用）",
            len(n) == 1 and n[0][3] != key)

        good.write_text("25 t/s 的門檻。10.923 與 11.340 兩筆（`Backup/good.json`）。\n",
                        encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("綁定：一數多檔／多數一檔都不綁（密集敘述不猜）", f == [] and n == [])

        good.write_text("11.34 t/s（`Backup/good.json`）——位數不同但同一個數。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("綁定：位數以文件寫的為準（11.34 ↔ 11.3404）", (f, n) == ([], []))

        good.write_text("11.4 t/s（`Backup/good.json`）——位數對不上就不硬湊。\n", encoding="utf-8")
        f, _n, _a, _na = scan_doc(good, set())
        chk("綁定：四捨五入到文件的位數後仍不等 ⇒ NOMATCH",
            len(f) == 1 and f[0][4] == V_NOMATCH)

        good.write_text("第一支 arm：`tg 10.923 ± 1.62 t/s`（`Backup/good.json`）。\n",
                        encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("± 展幅不是速率（`± 1.62 t/s` 不被當成 1.62 的讀數，也不算兩個數）", (f, n) == ([], []))

        good.write_text("`tg 10.923 ± 1.62 t/s` 沒有出處。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(good, set())
        chk("± 展幅也不製造無主讀數（只有 10.923 要交代）",
            f == [] and [x[2] for x in n] == ["10.923"])

        plain = d / "docs/ARCHIVE_LOG_2026-09-01.md"
        plain.write_text("舊的 10.923 t/s，沒有出處。\n", encoding="utf-8")
        f, n, _a, _na = scan_doc(plain, set())
        chk("範圍：非交付型檔名 ⇒ 不做無主檢查（歸檔面）", (f, n) == ([], []))

        scoped, _all = in_scope_docs("2026-09-25")
        chk("範圍：09-25 前的 dated 交付件歸檔",
            all(name_in_scope(p, "2026-09-25") == (p in scoped) for p in _all)
            and not name_in_scope(Path("docs/OLD_VERDICT_2026-09-20.md"), "2026-09-25")
            and name_in_scope(Path("docs/NEW_VERDICT_2026-09-30.md"), "2026-09-25")
            and name_in_scope(Path("docs/NEW_VERDICT.md"), "2026-09-25"))

        # commit：歷史 sha 不會變 ⇒ 凍結是一次性的；新 commit 才會紅
        f, n, _a, _na = scan_commit_text("a" * 40, "decode 10.923 t/s（`Backup/good.json`）交付錨點")
        chk("commit：有出處且可引用 ⇒ 無違規", (f, n) == ([], []))
        f, n, _a, _na = scan_commit_text("b" * 40, "decode 10.923 t/s（`Backup/bad.json`）")
        chk("commit：出處不可引用 ⇒ 綁定違規", len(f) == 1 and f[0][4] == V_NOTQ)
        f, n, _a, _na = scan_commit_text("c" * 40, "decode 10.923 t/s，看 +9%")
        chk("commit：沒有出處 ⇒ 無主（要嘛補出處、要嘛進帳）",
            f == [] and len(n) == 1 and n[0][3] == "commit|%s|10.923" % ("c" * 40))
        f, n, _a, _na = scan_commit_text("d" * 40, "修正：10.923 t/s 那筆已作廢")
        chk("commit：帶非主張標記 ⇒ 放行", (f, n) == ([], []))

        # hook 模式（commit 當下）：檔案不存在 ⇒ 紅；乾淨 ⇒ 0 且沒有輸出；違規 ⇒ 1。
        def _quiet(fn, *a, **k):
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                rc = fn(*a, **k)
            return rc, buf.getvalue()

        msg = d / "COMMIT_EDITMSG"
        chk("hook：訊息檔不存在 ⇒ rc=1", _quiet(run_message_file, str(d / "nope"))[0] == 1)
        msg.write_text("decode 10.923 t/s（`Backup/good.json`）交付錨點\n", encoding="utf-8")
        rc, out = _quiet(run_message_file, str(msg))
        chk("hook：有出處且可引用 ⇒ 放行，而且完全沒有輸出（hook 乾淨時要安靜）",
            rc == 0 and out == "")
        msg.write_text("decode 10.923 t/s，看 +9%\n", encoding="utf-8")
        rc, out = _quiet(run_message_file, str(msg))
        chk("hook：沒有出處 ⇒ 攔下，且說得出是哪一行哪個數",
            rc == 1 and "沒有出處" in out and "10.923" in out)
        msg.write_text("decode 10.923 t/s（`Backup/bad.json`）\n", encoding="utf-8")
        chk("hook：出處對得上但不可引用 ⇒ 攔下", _quiet(run_message_file, str(msg))[0] == 1)
        msg.write_text("修正：把 9.377 t/s 那筆標成作廢\n", encoding="utf-8")
        chk("hook：帶非主張標記（作廢）⇒ 放行", _quiet(run_message_file, str(msg))[0] == 0)
        msg.write_text("修正：把 9.377 t/s 那筆的判定更新\n", encoding="utf-8")
        chk("hook：只寫「修正」不算非主張 ⇒ 仍攔下（底線：假裝修正的無主讀數也不行）",
            _quiet(run_message_file, str(msg))[0] == 1)

        # 上線前後的分流（pre-push 的 enforce 只打上線之後的）
        _cs = [("a" * 40, "2026-09-30T09:59:59+08:00", "decode 30.0 t/s"),
               ("b" * 40, "2026-09-30T10:00:00+08:00", "decode 10.923 t/s（`Backup/good.json`）"),
               ("c" * 40, "2026-10-01T12:00:00+08:00", "clean")]
        _fresh, _old = split_by_cutoff(_cs, "2026-09-30T10:00:00+08:00")
        chk("上線分流：只判出生時間之後的（同秒算之後），之前的只列不判",
            [c[0][0] for c in _fresh] == ["b", "c"] and [c[0][0] for c in _old] == ["a"])
        # 這一筆是「字串比大小會看錯邊」的形狀：01:00+00:00 是 09:00+08:00（在上線後），
        # 但字串 "2026-09-30T01:00:00+00:00" < "2026-09-30T02:00:00+08:00" 會判成上線前。
        _d1, _d2 = split_by_cutoff([("d" * 40, "2026-09-30T01:00:00+00:00", "x")],
                                   "2026-09-30T02:00:00+08:00")
        chk("上線分流：跨時區要比真的時刻（字串比大小會把 09:00+08:00 誤判成上線前）",
            [c[0][0] for c in _d1] == ["d"] and _d2 == [])
        _u1, _u2 = split_by_cutoff([("e" * 40, "not-a-date", "x")], "2026-09-30")
        chk("上線分流：沒有戳記（這裡不是 git repo）⇒ 底線退回常數",
            commit_since_default() == COMMIT_SINCE_DEFAULT)
        chk("上線分流：commit 日期讀不懂 ⇒ 當上線後（fail-closed，不靠壞欄位放行）",
            [c[0][0] for c in _u1] == ["e"] and _u2 == [])

        # revspec：pre-push 的「全新分支」那一支用多詞 revspec
        chk("revspec：多詞拆得開", _rev_args("HEAD --not --remotes") == ["HEAD", "--not", "--remotes"])
        chk("revspec：單詞不變", _rev_args("origin/dev..HEAD") == ["origin/dev..HEAD"])

        # 安裝器：不覆蓋外來的、串接要能還原
        hd = d / "hooks"
        chk("hook 安裝：空目錄 ⇒ 兩支都裝好、可執行、佔位符都換掉",
            _quiet(install_hooks, hd, False)[0] == 0
            and (hd / "commit-msg").exists() and (hd / "pre-push").exists()
            and os.access(hd / "commit-msg", os.X_OK)
            and HOOK_MARK in (hd / "commit-msg").read_text(encoding="utf-8")
            and "@@MARK@@" not in (hd / "pre-push").read_text(encoding="utf-8")
            and "@@CHAIN@@" not in (hd / "commit-msg").read_text(encoding="utf-8")
            and "--message-file" in (hd / "commit-msg").read_text(encoding="utf-8")
            and "--commits" in (hd / "pre-push").read_text(encoding="utf-8"))
        chk("hook 安裝：重裝自己的 ⇒ 直接覆蓋（rc=0）", _quiet(install_hooks, hd, False)[0] == 0)
        (hd / "commit-msg").write_text("#!/bin/sh\necho 別人的\n", encoding="utf-8")
        chk("hook 安裝：外來的 ⇒ 拒裝（rc=1）且內容不動",
            _quiet(install_hooks, hd, False)[0] == 1
            and "別人的" in (hd / "commit-msg").read_text(encoding="utf-8"))
        chk("hook 安裝：外來的 + --chain ⇒ 原檔留成 .chain、入口先跑它",
            _quiet(install_hooks, hd, True)[0] == 0
            and (hd / "commit-msg.chain").exists()
            and "commit-msg.chain" in (hd / "commit-msg").read_text(encoding="utf-8"))
        chk("hook 卸載：還原原 hook（.chain 回去）",
            _quiet(uninstall_hooks, hd)[0] == 0
            and (hd / "commit-msg").exists() and not (hd / "commit-msg.chain").exists()
            and "別人的" in (hd / "commit-msg").read_text(encoding="utf-8"))
        hd2 = d / "hooks2"
        _quiet(install_hooks, hd2, False)
        chk("hook 卸載：自己裝的 ⇒ 兩支都移除",
            _quiet(uninstall_hooks, hd2)[0] == 0 and not (hd2 / "commit-msg").exists()
            and not (hd2 / "pre-push").exists())
        chk("hook 回報：rc 一律 0（hook 不進版控，不能讓它把管線弄紅）",
            _quiet(hooks_status, hd2)[0] == 0)
        chk("hook 模板：缺席時要留痕（兩支都要寫 doc_claim_gate.missing）",
            all(HOOK_MISSING_FILE in _hook_text(n, False) for n in ("commit-msg", "pre-push")))
        (hd2 / HOOK_MISSING_FILE).write_text("2026-09-30T11:00:00+0800 commit-msg /x\n",
                                             encoding="utf-8")
        chk("hook 回報：工具缺席過的痕跡要看得見（放行 ≠ 判過）",
            _quiet(hooks_status, hd2)[0] == 0 and "缺席" in _quiet(hooks_status, hd2)[1])
        chk("hook 安裝：重裝 ⇒ 清掉缺席痕跡（工具回來＝那段放行結案）",
            _quiet(install_hooks, hd2, False)[0] == 0 and not (hd2 / HOOK_MISSING_FILE).exists())

        # 帳的往返
        led = d / "ledger.json"
        save_ledger(led, {"entries": [{"kind": "doc", "key": "k", "why": "w"}]})
        chk("帳：存讀往返", ledger_keys(load_ledger(led)) == {"k"})
        chk("帳：壞檔不炸（保守當空帳）",
            ledger_keys(load_ledger(d / "nope.json")) == set())

    ROOT = real_root
    _scan_cache = {}
    for name, cond in ok:
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))
    print("selftest %d/%d" % (sum(c for _, c in ok), len(ok)))
    return 0 if all(c for _, c in ok) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--glob", action="append", default=None, help="文件 glob（可多次；預設 docs/*.md|html）")
    ap.add_argument("--since", default=DEFAULT_SINCE, help="交付面的日期底線（YYYY-MM-DD）")
    ap.add_argument("--all-docs", action="store_true", help="含歸檔面稽核（不影響 rc）")
    ap.add_argument("--commits", default=None,
                    help="掃 commit 訊息並判定（推送前用，例：origin/dev..HEAD）")
    ap.add_argument("--commits-audit", default=None,
                    help="只稽核 commit 訊息、不影響 rc（歷史不改寫）")
    ap.add_argument("--commit-since", default=None,
                    help="閘門出生時間：早於此的 commit 訊息只列不判（預設讀掛勾目錄的 %s，沒有才用 %s）"
                         % (HOOK_SINCE_FILE, COMMIT_SINCE_DEFAULT))
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--update-ledger", action="store_true", help="把這次找到的違規／無主登記進帳")
    ap.add_argument("--prune-ledger", action="store_true", help="把已不再違規的帳目結清")
    ap.add_argument("--why", default=None, help="--update-ledger 的理由")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--message-file", default=None,
                    help="commit-msg hook 模式：判一個訊息檔（乾淨時完全沒有輸出；違規 ⇒ rc=1）")
    ap.add_argument("--install-hook", action="store_true",
                    help="裝 commit-msg ＋ pre-push；不覆蓋別人的 hook（rc=1 = 拒裝，--chain 可串接）")
    ap.add_argument("--uninstall-hook", action="store_true", help="移除本工具裝的 hook（有 .chain 會還原）")
    ap.add_argument("--hooks-status", action="store_true", help="只回報 hook 裝了沒（rc 一律 0）")
    ap.add_argument("--chain", action="store_true", help="串接：原 commit-msg 保留為 .chain，新的入口先跑它")
    ap.add_argument("--hooks-dir", default=None, help="覆寫 hooks 目錄（預設由 git 解析；測試用）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.message_file:
        return run_message_file(args.message_file)
    if args.install_hook:
        return install_hooks(_hooks_dir(args.hooks_dir), args.chain)
    if args.uninstall_hook:
        return uninstall_hooks(_hooks_dir(args.hooks_dir))
    if args.hooks_status:
        return hooks_status(_hooks_dir(args.hooks_dir))
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
