#!/usr/bin/env python3
"""rho_window.py — ρ 切分的窗口**宣告**：S1 套用→S2 建置→S3 oracle 錨→S4 oracle 交付→
S5 四條權威 row 命令→S6 判詞。流程本身住在 `window_runner.py`（單一定義），這裡只宣告
「每一步要跑什麼、跑完收什麼、失敗時要看哪裡」—— 別的子目標要用同一套 skip／fail-fast／
可接續，照著宣告一份自己的 steps 即可（`window_runner` 的 docstring 有最小範例）。

為什麼要有這一支（operator 2026-09-30）：落地要動的東西分散在一份補丁、五支工具與四個產物
目錄裡。窗口一開（`src/` 從別線釋放）時，最貴的不是算力，是「人記得下一步是哪一步、跑完要看
哪個數字」。

收貨條件（跑前寫死；與 charter `e-rho-delivery-flag` 的 acceptance 同一份）：
  ① `rho_price_authrow.py --check` 轉 `RUNNABLE`（機制與量具分家）；
  ② oracle **對照臂與交付臂都 M1 9/9**（切分不動數值；對照臂同時是「這支 build 的錨」）；
  ③ 交付臂 `extra_env['CGC_RHO']=='1'`（**旗標真的進了子行程**）+ 伺服器 log **零** `CGC-RHO-*`；
  ④ 池結算行 `prefetch=N/M`：ρ 的貢獻是**配對差分（B−A）**——`n_prefetch` 是所有 prefetch 路徑
     共用的計數器（`llama-expert-cache.cpp:1798/1852`）⇒ N≥1 只證明「池在預取」；
  ⑤ 四條命令的 decode 行過 `quote_gate`，且 A 臂無／B 臂有 `CGC_RHO`；`--judge` 判 **`PRICE`**
     或 **`NO_EFFECT`**（後者是合法結局：不可辨識；`REFUS` 不是結局，任何情況都不得寫成「ρ 免費」）。

⚠ 三個容易踩的事實（實測，見 docs/RHO_PRICE_AUTHROW_2026-09-30.md §6）：
  * oracle 的 `launch_<tag>.log` **只是 launcher 的 banner**，不含伺服器 stderr ⇒ 在那份檔上
    `grep -c 'CGC-RHO-'` 永遠是 0（一個**假的綠**）。真的伺服器 log 由 banner 的 `[log] <path>`
    指名（`m123_oracle_gate.pool_counters_for_run()` 讀的也是同一份）。
  * 「零 `CGC-RHO-*`」是**必要不是充分**（探針臂 teardown 沒跑到時也是 0 行）⇒ 旗標進子行程的
    證據看 summary 的 `extra_env`。
  * 影子建置（2026-09-30，零 GPU）證明：這棵樹的 Mac configure 必須帶
    `-DCMAKE_CXX_FLAGS=-DMTP_SUPPORT`（`scripts/build_fork_llama.sh:215`）。少了它，
    `llama-bench.cpp` 直接編不過（它呼叫的 `release_context()` 在 `#ifdef MTP_SUPPORT` 裡）。

用法：
    python3 scripts/check/rho_window.py                 # --status（預設）：計畫＋現況，不跑任何東西
    python3 scripts/check/rho_window.py --go            # 真的跑（fail-fast；可中斷後重跑接續）
    python3 scripts/check/rho_window.py --go --only S1,S2   # 只做這兩步（例：先套用＋建置）
    python3 scripts/check/rho_window.py --go --reuse-ctl Backup/m123_oracle_gate/summary_xxx.json
    python3 scripts/check/rho_window.py --list-steps     # 六步與各自的收貨來源
    python3 scripts/check/rho_window.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import instrument_binding as IB  # noqa: E402  （產物↔stderr log 的成對規則住在它裡面，不重寫）
import rho_price_authrow as RPA  # noqa: E402  （判詞、補丁狀態、建置狀態、四條命令的單一定義）
import window_runner as WR       # noqa: E402  （流程：skip／fail-fast／可接續，只有那一份）

STEP_IDS = ("S1", "S2", "S3", "S4", "S5", "S6")
STEP_NAMES = {
    "S1": "套用補丁（切分進樹）",
    "S2": "建置（llama-server ＋ llama-bench）",
    "S3": "oracle 對照臂",
    "S4": "oracle 交付臂（CGC_RHO=1）",
    "S5": "四條權威 row 命令（兩臂 × 兩趟反序）",
    "S6": "判詞（PRICE／NO_EFFECT／REFUSE）",
}
JOURNAL_REL = "Backup/rho_split_2026-09-30/window.jsonl"
LOG_DIR_REL = "Backup/rho_split_2026-09-30/window_logs"
# 影子驗（--shadow）：在 src/ 的**複本**上把 S1/S2 先驗綠（零 GPU）。為什麼要這一套：套用與
# 建置是最貴的兩步，而且它們的失敗跟「機制對不對」無關（例如 configure 少了 -DMTP_SUPPORT）——
# 這種石頭不該在 GPU 窗口裡踩。重現指令**不手抄**：把 scripts/build_fork_llama.sh 複製進影子再跑，
# 它的 FORK_DIR 是從腳本自身位置推的 ⇒ ＝對影子 configure，旗標清單與 profile 戳記都是同一份。
SHADOW_DIR_REL = "Backup/rho_split_2026-09-30/shadow"
SHADOW_JOURNAL_REL = "Backup/rho_split_2026-09-30/shadow_journal.jsonl"
SHADOW_LOG_DIR_REL = "Backup/rho_split_2026-09-30/shadow_logs"
SHADOW_EVIDENCE_REL = "Backup/rho_split_2026-09-30/shadow.json"
SHADOW_MIN_FREE_GB = 3.0          # 複本 0.3G ＋（從零）建置 0.2–0.5G ⇒ 留足餘裕，否則拒跑
SHADOW_TITLE = ("ρ 影子驗（零 GPU）：複製 src/ → 套用補丁 → 照建置脚本 configure＋建置 → 只驗 S1/S2")
ORACLE_DIR = "Backup/m123_oracle_gate"
PRICE_DIR = "Backup/rho_price_2026-09-30"
TITLE = "ρ 切分窗口：S1 套用 → S2 建置 → S3 oracle 錨 → S4 oracle 交付 → S5 四條命令 → S6 判詞"

# 每一步失敗時要印的「下一個該看的東西」。放這裡（而不是散在流程裡）是因為：**這張表就是這支
# 腳本存在的理由** —— 窗口裡最貴的是「失敗之後要看哪裡」。
NEXT_ON_FAIL = {
    "S1": [
        "python3 scripts/check/rho_price_authrow.py --check        # 哪一個座標沒翻（四條閘＋列印）",
        "git apply -R Backup/rho_split_2026-09-30/rho_delivery_split.patch   # 套歪了就整張退回去（--check 必須回到 BLOCKED）",
        "docs/RHO_PRICE_AUTHROW_2026-09-30.md §6                   # 補丁的四個靶與各自的驗證",
    ],
    "S2": [
        "git -C src/llama.cpp status --porcelain | head           # 別線是不是還在改同一棵 src/",
        "scripts/build_fork_llama.sh:215                          # 這個 repo 的 Mac configure 要 -DCMAKE_CXX_FLAGS=-DMTP_SUPPORT（少了它 llama-bench.cpp 編不過）",
        "python3 scripts/check/rho_price_authrow.py --checklist     # 建置狀態：libllama*.dylib 內精確 CGC_RHO 字串",
    ],
    "S3": [
        "python3 scripts/check/r6_witness.py --help                # 錨的規則：M1=M2=M3=n 才算綠（錨壞了什麼都不能歸因）",
        "docs/RHO_PRICE_AUTHROW_2026-09-30.md §6                   # 對照臂可重用同 build 的既有錨（--reuse-ctl）",
    ],
    "S4": [
        "python3 scripts/check/rho_price_authrow.py --check        # RUNNABLE 才算分家（SPLIT_LEAKY 會點名是哪一條列印）",
        "python3 -c \"import json;d=json.load(open('Backup/m123_oracle_gate/summary_<tag>.json'));print(d['extra_env'], d['pool_counters'])\"   # 旗標到底有沒有進子行程",
        "scripts/run_server.sh:1799                                # 補丁的第五個座標：CGC_RHO 進得了子行程嗎",
    ],
    "S5": [
        "python3 scripts/check/quote_gate.py check Backup/rho_price_2026-09-30/order1_B.json   # 為何不可引用（R1–R8）",
        "docs/S3B_RHO_COST_2026-09-30.md §8                        # R8：輪級聚合不可入表；同 cell／同 row／逐 rep",
        "Backup/rho_price_2026-09-30/*.stderr.log                  # CGC-RHO- 應為 0；prefetch 差分才是 ρ 的貢獻",
    ],
    "S6": [
        "python3 scripts/check/rho_price_authrow.py --judge Backup/rho_price_2026-09-30/order1.json Backup/rho_price_2026-09-30/order2.json",
        "docs/RHO_PRICE_AUTHROW_2026-09-30.md §3                   # 四條規則：兩臂 QUOTABLE／只有一顆旗標差／|Δ|>散布／兩趟同向",
        "docs/S3B_RHO_COST_2026-09-30.md §7.6                      # 舊價格 REFUSE 的形狀（散布蓋過效應）",
    ],
}


# ── 小工具：解析 receipts（全部是純函式，selftest 直接餵字串／檔）────────────────

def _rate(s) -> tuple:
    """`"9/9"` → `(9, 9)`；缺欄位或格式不合 ⇒ `None`（不要猜）。"""
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)\s*$", str(s or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _short(p: str) -> str:
    """訊息裡的路徑：在 repo 裡就寫相對於 repo 的，外面的（暫時 fixture）就寫 basename。"""
    p = str(p)
    return os.path.relpath(p, ROOT) if p.startswith(ROOT + os.sep) else os.path.basename(p)


def read_text(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def summary_state(path: str, anchor: bool = False, want_env: dict = None) -> dict:
    """m123 summary 的判讀。`anchor=True` 時要求 M1=M2=M3=n（r6_witness 的錨規則）。

    `want_env` 判「旗標真的進到子行程了嗎」—— 讀的是 `extra_env`（`run_server.sh
    CGC_DUMP_ENV=1` 解析出來的生效覆蓋集）：`{"CGC_RHO": "1"}` ⇒ 必須有且等於 1；
    `{"CGC_RHO": None}` ⇒ 必須**沒有**（對照臂不得帶交付旗標）。
    """
    if not os.path.exists(path):
        return dict(present=False, ok=False, why="summary 不在：%s" % _short(path), m1=None)
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return dict(present=True, ok=False, why="summary 讀不動：%s" % e, m1=None)
    keys = ("m1_numeric_identity", "m2_decision_agreement", "m3_topk_set_agreement")
    rates = [_rate(d.get(k)) for k in keys]
    bad = []
    if not bool(d.get("comparable")):
        bad.append("comparable=%r（不是可比趟次 ⇒ 這份 summary 只能看，不能判）" % d.get("comparable"))
    if rates[0] is None:
        bad.append("m1_numeric_identity 不是 k/n 形（%r）" % d.get("m1_numeric_identity"))
    elif rates[0][0] != rates[0][1]:
        bad.append("M1 %s（要 %d/%d）" % (d.get("m1_numeric_identity"), rates[0][1], rates[0][1]))
    if anchor:
        for k, r in zip(keys, rates):
            if r is None or r[0] != r[1]:
                bad.append("錨的 %s = %r（錨必須 M1=M2=M3=n）" % (k, d.get(k)))
    env = d.get("extra_env") or {}
    for k, v in (want_env or {}).items():
        if v is None:
            if k in env:
                bad.append("extra_env 帶了 %s=%r（這一臂不該帶）" % (k, env[k]))
        elif str(env.get(k)) != str(v):
            bad.append("extra_env[%s]=%r（要 %r）⇒ 旗標沒進子行程" % (k, env.get(k), v))
    ok = not bad
    why = ("；".join(bad) if bad else
           "M1 %s、M2 %s、M3 %s、comparable=True%s" % (d.get("m1_numeric_identity"),
                                                       d.get("m2_decision_agreement"),
                                                       d.get("m3_topk_set_agreement"),
                                                       "、extra_env 收貨" if want_env else ""))
    return dict(present=True, ok=ok, why=why, m1=rates[0], rates=rates, doc=d,
                env=env, pool=d.get("pool_counters"))


def log_receipts(text: str) -> dict:
    """一份**伺服器** stderr log 的兩個收貨數字：`CGC-RHO-` 行數、池結算行的 `prefetch=N/M`。

    ⚠ `prefetch` 是**池**的計數器：N≥1 只證明池在預取（其他 prefetch 路徑也會加它），
    ρ 的貢獻是 B−A 差分 —— 這一行註解就是收貨條件 ④ 的出處。
    ⚠ 餵進來的必須是 banner 指名的那份伺服器 log（`server_log_of()`）：banner 自己永遠是 0。
    """
    rho = sum(1 for l in (text or "").splitlines() if "CGC-RHO-" in l)
    prefetch = None
    for m in re.finditer(r"prefetch=(\d+)/(\d+)", text or ""):
        prefetch = (int(m.group(1)), int(m.group(2)))
    return dict(rho_lines=rho, prefetch=prefetch, has_text=bool(text))


def server_log_of(root: str, tag: str) -> str:
    """oracle 那條線的**伺服器** log：由 `launch_<tag>.log` 的 `[log] <path>` 指名。

    不猜、也不 glob：另一個 run 的計數器會是一個安靜的錯答案（`m123_oracle_gate` 自己也是
    這個立場）。找不到就回空字串，讓上層寫出「讀不到」而不是「零」。
    """
    banner = read_text(os.path.join(root, ORACLE_DIR, "launch_%s.log" % tag))
    m = re.search(r"\[log\]\s+([^\s（(]+)", banner)
    if not m:
        return ""
    p = m.group(1)
    return p if os.path.exists(p) else ""


def ctl_tag_of(reuse: str, ctl_tag: str) -> str:
    """對照臂的 tag。重用外部錨時從那份 summary 的檔名反推（`summary_<tag>.json`）；
    反推不出來就回空字串 —— 宁可不說，也不要把別的 run 的 log 當這一場的。"""
    if not reuse:
        return ctl_tag
    b = os.path.basename(reuse)
    if b.startswith("summary_") and b.endswith(".json"):
        return b[len("summary_"):-len(".json")]
    return ""


def pool_diff(root: str, ctl_tag: str, arm_tag: str) -> str:
    """兩份伺服器 log 的 `prefetch` 差分（同一 binary、同一份 prompt、只差一顆 `CGC_RHO`）。

    這是**證據**，不是價格：`n_prefetch` 是所有 prefetch 路徑共用的計數器，所以差分只回答
    「交付旗標有沒有多發預取」。價格要 S5 的權威 row（同 cell、同 row、逐 rep）。
    """
    if not ctl_tag:
        return "池差分不算（重用外部錨，它的 tag 反推不出來 ⇒ 不把別的 run 當這一場）"
    a = log_receipts(read_text(server_log_of(root, ctl_tag)))["prefetch"]
    b = log_receipts(read_text(server_log_of(root, arm_tag)))["prefetch"]
    if not (a and b):
        return "池差分讀不到（兩份伺服器 log 沒有結算行）"
    return "池 prefetch 差分 B−A = %+d（%d→%d；證據，不是價格）" % (b[0] - a[0], a[0], b[0])


def oracle_receipts(root: str, tag: str, role: str, reuse: str = None) -> tuple:
    """oracle 那條線的收貨（plan 與 receipt **共用同一份**，所以計畫不會和驗收漂移）。

    `role='ctl'`：錨（M1=M2=M3=n）＋ `extra_env` **沒有** `CGC_RHO`（對照臂不得帶交付旗標）。
    `role='deliver'`：M1=n/n ＋ `extra_env['CGC_RHO']=='1'`（補丁第五個座標的收貨）＋
       banner 指名的伺服器 log 上 `CGC-RHO-*` ＝ 0（量具不外洩）。
    兩者都回報該次啟動的池計數（`prefetch=N/M`），差分在 S4 時一起印。

    ⚠ 誠實邊界：「伺服器 log 零 CGC-RHO-*」是**必要條件不是充分條件**（探針臂的 teardown 沒跑到
    時也是 0 行，實測 `s2b-arm-segbatch-v9`：`CGC_RHO_PROBE=1` 而 rho=0）⇒ 旗標進子行程的證據是
    `extra_env`；「機制真的做了事」的證據在 S5（bench 會正常收工 ⇒ 池結算行與帳都在）。
    """
    if role == "ctl":
        path = os.path.join(root, reuse or os.path.join(ORACLE_DIR, "summary_%s.json" % tag))
        st = summary_state(path, anchor=True, want_env={"CGC_RHO": None})
        src = "reuse-ctl" if reuse else "新跑"
    else:
        st = summary_state(os.path.join(root, ORACLE_DIR, "summary_%s.json" % tag),
                           want_env={"CGC_RHO": "1"})
        src = "交付臂"
    slog = server_log_of(root, tag)
    lr = log_receipts(read_text(slog))
    why = "%s：%s" % (src, st["why"])
    if role == "deliver":
        if not slog:
            return False, why + "；讀不到伺服器 log（banner 沒有 `[log] <path>` ⇒ 量具無法查證）", st
        if lr["rho_lines"]:
            return False, why + "；伺服器 log 有 %d 行 CGC-RHO-*（量具外洩）" % lr["rho_lines"], st
        why += "；伺服器 log 零 CGC-RHO-*（%s）" % os.path.basename(slog)
    why += "；prefetch=%s" % (str(lr["prefetch"]) if lr["prefetch"] else "讀不到")
    return st["ok"], why, st


def _price_rows(root: str, prods: list) -> tuple:
    """四份產物的 decode 行是不是都可引用、以及各自的 log receipts。回 (ok, why)。

    ρ 的貢獻不是「prefetch ≥ 1」而是 **B−A 差分**（`n_prefetch` 是所有 prefetch 路徑共用的
    計數器）⇒ 這裡把兩臂的 N 一起回報，讓 S5 的收貨條件可由**產物**重算，不是由敘述。
    """
    notes, all_ok, seen = [], True, []
    for p in prods:
        rel = os.path.relpath(p, root)
        if not os.path.exists(p):
            all_ok = False
            notes.append("%s 不在" % rel)
            continue
        rec, why = RPA._decode_row(RPA._rows_of(p))
        if rec is None or rec.get("verdict") != "QUOTABLE":
            all_ok = False
            notes.append("%s：%s（%s）" % (rel, (rec or {}).get("verdict", "無 row"),
                                          "；".join((rec or {}).get("reasons") or [why or ""])))
            continue
        log = _prod_log(root, p)
        if not log:
            all_ok = False
            notes.append("%s QUOTABLE 但缺成對的 stderr log（量具無法查證）" % rel)
            continue
        # 臂對不對：A 不該帶 CGC_RHO、B 必須帶（與 `judge()` 的規則②同一份 env 判讀）。
        base = os.path.basename(rel)
        arm = "B" if base.endswith("_B.json") else ("A" if base.endswith("_A.json") else "")
        try:
            env = RPA._prod_env(p)
        except Exception as e:  # noqa: BLE001
            all_ok = False
            notes.append("%s 的臂上 env 讀不到（%s）⇒ 不知道它是不是對的那一臂" % (rel, e))
            continue
        if arm == "B" and str(env.get("CGC_RHO", "")) != "1":
            all_ok = False
            notes.append("%s：B 臂的 env 沒有 CGC_RHO=1（%s）⇒ 這不是交付臂" % (rel, env))
            continue
        if arm == "A" and "CGC_RHO" in env:
            all_ok = False
            notes.append("%s：A 臂帶了 CGC_RHO（%s）⇒ 這不是對照臂" % (rel, env))
            continue
        lr = log_receipts(read_text(log))
        if lr["rho_lines"]:
            all_ok = False
            notes.append("%s 的 stderr 有 %d 行 CGC-RHO-*（量具外洩）" % (rel, lr["rho_lines"]))
            continue
        seen.append((os.path.basename(rel), lr["prefetch"]))
        notes.append("%s QUOTABLE（prefetch=%s、零 CGC-RHO-*）" % (rel, lr["prefetch"]))
    if len(seen) == 4:
        diffs = []
        for order in (1, 2):
            a = [n for f, n in seen if f.startswith("order%d_A" % order)]
            b = [n for f, n in seen if f.startswith("order%d_B" % order)]
            if a and b and a[0] and b[0]:
                diffs.append("order%d B−A = %+d" % (order, b[0][0] - a[0][0]))
        notes.append("ρ 的貢獻看差分（%s；n_prefetch 是所有 prefetch 路徑共用）"
                     % ("、".join(diffs) if diffs else "兩臂的結算行讀不到 ⇒ 差分不可算"))
    return all_ok, "；".join(notes) if notes else "四份產物都不在"


def _prod_log(root: str, path: str) -> str:
    """產物的成對 stderr log —— 判準住 `instrument_binding.pair_status()`（唯一住處）。"""
    st = IB.pair_status(path)
    return st["canonical"] if st.get("paired") and st.get("canonical") else ""


def _verdict_state(path: str) -> tuple:
    if not os.path.exists(path):
        return False, "判詞還沒寫（%s）" % _short(path)
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, "判詞讀不動：%s" % e
    v = d.get("verdict")
    return v in ("PRICE", "NO_EFFECT"), "%s -- %s" % (v, d.get("why", ""))


# ── 前置（S0）：補丁、這棵樹、盒子閒不閒 ──────────────────────────────────────

def _llama_jobs() -> list:
    """有沒有別人在用盒子（llama-server／llama-bench）。只讀，不殺任何東西。"""
    try:
        p = subprocess.run(["pgrep", "-fl", "build/bin/llama-(server|bench)"],
                           capture_output=True, text=True)
    except OSError:
        return []
    jobs = []
    for l in (p.stdout or "").splitlines():
        if "pgrep" in l or "rho_window" in l:
            continue
        jobs.append(l.strip())
    return jobs


def _src_dirty(root: str) -> list:
    """`src/llama.cpp` 這一棵子樹有幾個未提交的檔（「別線還在佔著 src/」的訊號）。

    **不是** blocker：補丁的 base md5 已經對上了 ⇒ `git apply` 套得上去，而且 git apply 是原子的
    （衝突就當場失敗）。但建置產物會混用「他們改到一半的樹」⇒ 那件事必須讓人看見，不能靜靜跑。
    ⚠ 只認**這一棵樹自己的** repo：影子樹住在 repo 裡（不是 repo）時會往上找到母樹 ⇒ 那不是
    這一棵樹的 dirty 清單，回空（否則影子樹會被誤報成「別線在動它」）。
    """
    sub = os.path.join(root, "src/llama.cpp")
    try:
        top = subprocess.run(["git", "-C", sub, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    if top.returncode != 0:
        return []
    tl = (top.stdout or "").strip()
    if not (tl == root or tl.startswith(root + os.sep)):
        return []
    try:
        p = subprocess.run(["git", "-C", sub, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [l for l in (p.stdout or "").splitlines() if l.strip()] if p.returncode == 0 else []


def preflight(root: str, ctx: dict) -> dict:
    ps = RPA.patch_state(root)
    reasons = []
    if not ps["present"]:
        reasons.append("補丁不在：%s" % RPA.PATCH_REL)
    elif not ps["md5_ok"]:
        reasons.append("補丁 md5 ≠ 釘住的值 ⇒ 先重新驗證（別套一份沒驗過的補丁）")
    jobs = _llama_jobs()
    if jobs:
        reasons.append("盒子不閒（%d 個 llama 行程）⇒ 先等它收工：%s" % (len(jobs), jobs[0]))
    notes = []
    dirty = _src_dirty(root)
    if dirty:
        notes.append("⚠ src/llama.cpp 有 %d 個未提交的檔 ⇒ 這一棵樹還在別線手上：套用與建置會和"
                     "他們共用同一棵樹" % len(dirty))
        notes.append("  （git apply 是原子的 ⇒ 衝突會當場失敗；但**建置產物會混用半成品** ⇒ "
                     "窗口是不是你的，人決定）")
    return dict(ok=not reasons, reasons=reasons, notes=notes,
                header=["補丁 md5 %s %s" % (ps["md5"], "（釘住的值）" if ps["md5_ok"]
                                            else "（≠ 釘住的值）")])


# ── 六步的宣告（流程由 window_runner 跑）───────────────────────────────────────

def steps(ctx: dict) -> list:
    """ρ 的窗口宣告。`ctx` 帶 `date`（tag 的日期）與 `reuse_ctl`（重用既有錨的路徑）。"""
    root, date = ctx["root"], ctx["date"]
    reuse = ctx.get("reuse_ctl")
    ctl_tag, arm_tag = "rho-split-ctl-%s" % date, "rho-split-deliver-%s" % date
    mech = RPA.mechanism_block(root)
    built = RPA.build_state(root)
    ps = RPA.patch_state(root)
    cmds = RPA.plan(root)          # 四條命令與它們的產物路徑（cell 旗標向 cell_contract 要）
    prods = [os.path.join(root, _json_of(c)) for c in cmds]
    verdict_path = os.path.join(root, PRICE_DIR, "verdict.json")

    def done_mech(r, c):
        m = RPA.mechanism_block(r)
        return ((not m["blocked"]) and not m["leaks"],
                "機制與量具已分家（--check 會判 RUNNABLE）" if not m["blocked"]
                else "機制閘仍是 probe-only（%s）" % m["verdict"])

    def done_build(r, c):
        b = RPA.build_state(r)
        hit = sum(1 for f in b["files"].values() if f["rho"])
        return (b["built"], ("build 產物裡有精確 `CGC_RHO`（%d/%d 顆 libllama*.dylib）"
                             % (hit, b["checked"])) if b["built"] else
                "build 產物裡沒有 `CGC_RHO`（%d 顆掃過）⇒ 切分還沒編進去" % b["checked"])

    def done_ctl(r, c):
        ok, why, _ = oracle_receipts(r, ctl_tag, "ctl", reuse=reuse)
        return ok, why

    def done_arm(r, c):
        ok, why, _ = oracle_receipts(r, arm_tag, "deliver")
        return ok, why

    def done_price(r, c):
        return _price_rows(r, prods)

    def done_verdict(r, c):
        return _verdict_state(verdict_path)

    def ctl_inprocess(r, c):        # --reuse-ctl：不發車，只認領並當場驗那顆錨
        ok, why, _ = oracle_receipts(r, ctl_tag, "ctl", reuse=reuse)
        return (0 if ok else 1), "", ok, why

    def judge_inprocess(r, c):      # S6：判詞在 rho_price_authrow 裡（預註冊的那一份）
        by = {(x["order"], x["arm"]): os.path.join(r, _json_of(x)) for x in RPA.plan(r)}
        for order in (1, 2):
            p = os.path.join(r, PRICE_DIR, "order%d.json" % order)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"A": by[(order, "A")], "B": by[(order, "B")]}, fh,
                          ensure_ascii=False, indent=1)
        res = RPA.both_orders(RPA.judge(by[(1, "A")], by[(1, "B")]),
                              RPA.judge(by[(2, "A")], by[(2, "B")]))
        out = os.path.join(r, PRICE_DIR, "verdict.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        ok = res.get("verdict") in ("PRICE", "NO_EFFECT")
        return 0, "", ok, "%s -- %s（%s）" % (res.get("verdict"), res.get("why", ""),
                                            os.path.relpath(out, r))

    s3 = (dict(id="S3", name=STEP_NAMES["S3"], commands=[],
               inprocess=ctl_inprocess, done=done_ctl, receipt=done_ctl,
               artifacts=[os.path.join(ORACLE_DIR, "summary_%s.json" % ctl_tag)],
               next_on_fail=NEXT_ON_FAIL["S3"])
          if reuse else
          dict(id="S3", name=STEP_NAMES["S3"],
               commands=[["python3", "scripts/check/m123_oracle_gate.py", "--profile", "prefill250",
                          "--tag", ctl_tag]],
               done=done_ctl, receipt=done_ctl,
               artifacts=[os.path.join(ORACLE_DIR, "summary_%s.json" % ctl_tag)],
               next_on_fail=NEXT_ON_FAIL["S3"]))

    # S5：四條命令**逐條** skip（該份產物已在就不重跑；重跑一份已量到的讀數是浪費窗口）。
    def _price_cmd(c):
        p = os.path.join(root, _json_of(c))
        return dict(cmd=c["cmd"],
                    skip_if=lambda r, cc, p=p: (os.path.exists(p), "%s 已在" % _short(p)),
                    log=safe_rel(IB.pair_canonical(p), root))

    return [
        dict(id="S1", name=STEP_NAMES["S1"],
             commands=[["git", "apply"] + ([] if ps["bases_ok"] else ["--3way"]) + [RPA.PATCH_REL]],
             done=done_mech, receipt=done_mech,
             artifacts=[RPA.PATCH_REL], next_on_fail=NEXT_ON_FAIL["S1"]),
        dict(id="S2", name=STEP_NAMES["S2"],
             commands=[["cmake", "--build", "src/llama.cpp/build", "--target",
                        "llama-server", "llama-bench", "-j", "8"]],
             done=done_build, receipt=done_build,
             artifacts=["src/llama.cpp/build/bin/libllama*.dylib"], next_on_fail=NEXT_ON_FAIL["S2"]),
        s3,
        dict(id="S4", name=STEP_NAMES["S4"],
             commands=[["python3", "scripts/check/m123_oracle_gate.py", "--profile", "prefill250",
                        "--tag", arm_tag, "--env", "CGC_RHO=1"]],
             done=done_arm, receipt=done_arm,
             artifacts=[os.path.join(ORACLE_DIR, "summary_%s.json" % arm_tag),
                        os.path.join(ORACLE_DIR, "launch_%s.log" % arm_tag)],
             next_on_fail=NEXT_ON_FAIL["S4"]),
        dict(id="S5", name=STEP_NAMES["S5"],
             commands=[_price_cmd(c) for c in cmds],
             done=done_price, receipt=done_price,
             artifacts=[safe_rel(p) for p in prods], next_on_fail=NEXT_ON_FAIL["S5"]),
        dict(id="S6", name=STEP_NAMES["S6"], commands=[],
             inprocess=judge_inprocess, done=done_verdict, receipt=done_verdict,
             artifacts=[safe_rel(verdict_path, root)], next_on_fail=NEXT_ON_FAIL["S6"]),
    ]


# ── 影子驗（--shadow）：零 GPU 地把 S1/S2 先驗綠 ──────────────────────────────

def _out(cmd: list, cwd: str = None) -> tuple:
    """唯讀查詢用：回 (rc, stdout)。不印、不改任何東西。"""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
        return 1, str(e)
    return p.returncode, (p.stdout or "")


def _dir_mb(p: str) -> int:
    rc, out = _out(["du", "-sm", p])
    return int(out.split()[0]) if rc == 0 and out.split() else 0


def _disk_free_gb(p: str) -> float:
    rc, out = _out(["df", "-k", p])
    if rc != 0 or len(out.splitlines()) < 2:
        return -1.0
    parts = out.splitlines()[1].split()
    return round(int(parts[3]) / 1024.0 / 1024.0, 1) if len(parts) > 3 else -1.0


def _newest_mtime(p: str) -> int:
    """這一棵裡最新的 mtime（「影子是不是比實樹舊」的便宜指標，不是完整 digest）。"""
    rc, out = _out(["bash", "-c",
                    "find %s -type f -not -path '*/build/*' -print0 2>/dev/null "
                    "| xargs -0 stat -f '%%m' 2>/dev/null | sort -n | tail -1" % _q(p)])
    return int(out.strip() or 0) if rc == 0 else 0


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def _rel_or_die(root: str, p: str) -> str:
    """給 `git apply --directory` 用的 repo 相對路徑（它不接受絕對）。影子在 repo 外 ⇒ 當場拒收。"""
    rel = os.path.relpath(p, root)
    if rel.startswith(".."):
        raise SystemExit("影子必須在 repo 裡：`git apply --directory` 不接受絕對路徑（%s）" % p)
    return rel


def _shadow_paths(root: str, ctx: dict = None) -> dict:
    shadow = os.path.join(root, (ctx or {}).get("shadow_dir") or SHADOW_DIR_REL)
    build = os.path.join(shadow, "src/llama.cpp/build")
    return dict(shadow=shadow, evidence=os.path.join(root, SHADOW_EVIDENCE_REL),
                models_real=os.path.join(root, "models"), models_link=os.path.join(shadow, "models"),
                build_script=os.path.join(shadow, "scripts", "build_fork_llama.sh"),
                stamp=os.path.join(build, "CGC_BUILD_PROFILE.txt"),
                patch=os.path.join(shadow, RPA.PATCH_REL))


def _shadow_prepared(root: str, ctx: dict) -> tuple:
    """影子「已經備好了嗎」（跑前掃描）：補丁在、md5 對、base 要嘛逐字相同（還沒套）要嘛已經套上
    （`--check` RUNNABLE）、建置脚本是同一份、models 連得上、而且**複本不比實樹舊**。

    為什麼不比「實樹的 mtime」而比「複本自己的 mtime」：`rsync -a` 會帶 mtime ⇒ 複本裡最新的那個
    時間就是「它從實樹的哪個狀態複來的」。實樹更新 ⇒ 比它大 ⇒ 重做一次**乾淨**的複本（保留 build/，
    因為那是最花時間的東西）；沒更新就 skip（窗口裡最常見的情況）。
    """
    p = _shadow_paths(root, ctx)
    if not os.path.isdir(p["shadow"]):
        return False, "影子不在：%s" % safe_rel(p["shadow"], root)
    ps = RPA.patch_state(p["shadow"])
    # base 檢查要接受**兩種**狀態：還沒套（逐字等於補丁的 base）與已經套了（base 當然不再相等，
    # 但那時收貨是「--check 判 RUNNABLE」）。只認前者會讓影子套用完就永遠 pending ⇒ 又去 rsync
    # 蓋掉補丁，然後重套一次 —— 假的工作、真的浪費。
    applied, _ = _shadow_applied(root, ctx)
    if not (ps["present"] and ps["md5_ok"] and (ps["bases_ok"] or applied)):
        return False, "影子的補丁／base 不對（present=%s md5_ok=%s bases_ok=%s 已套=%s）" % (
            ps["present"], ps["md5_ok"], ps["bases_ok"], applied)
    want = os.path.join(root, "scripts", "build_fork_llama.sh")
    if not (os.path.exists(p["build_script"]) and os.path.exists(want)
            and open(p["build_script"], "rb").read() == open(want, "rb").read()):
        return False, "影子的建置脚本不是同一份 ⇒ 重新複製"
    if not (os.path.islink(p["models_link"]) and os.path.realpath(p["models_link"])
            == os.path.realpath(p["models_real"])):
        return False, "models 沒連到實樹（%s）" % safe_rel(p["models_link"], root)
    real_new = _newest_mtime(os.path.join(root, "src", "llama.cpp"))
    sh_new = _newest_mtime(os.path.join(p["shadow"], "src", "llama.cpp"))
    if sh_new < real_new:
        return False, "複本比實樹舊（複本 %s < 實樹 %s）⇒ 重做一次乾淨複本" % (sh_new, real_new)
    return True, ("影子已備妥（補丁 md5 對、base %s、建置脚本同份、models 連著、複本 mtime %s ≥ 實樹 %s）"
                  % ("逐字相同" if ps["bases_ok"] else "已套用", sh_new, real_new))


def _shadow_applied(root: str, ctx: dict) -> tuple:
    p = _shadow_paths(root, ctx)
    st = RPA.mechanism_block(p["shadow"])
    ok = (not st["blocked"]) and not st["leaks"]
    return ok, "--check 判 %s；交付列印洩漏 %d 條" % (st["verdict"], len(st["leaks"]))


def _shadow_built(root: str, ctx: dict) -> tuple:
    p = _shadow_paths(root, ctx)
    bs = RPA.build_state(p["shadow"])
    hit = sum(1 for f in bs["files"].values() if f["rho"])
    bins = [os.path.join(p["shadow"], "src/llama.cpp/build/bin", b)
            for b in ("llama-server", "llama-bench")]
    missing = [os.path.basename(b) for b in bins if not os.path.exists(b)]
    if missing:
        return False, "建置產物不齊：缺 %s" % "、".join(missing)
    return bs["built"], ("精確 `CGC_RHO` 命中 %d/%d 顆 libllama*.dylib；llama-server／llama-bench 都在"
                         % (hit, bs["checked"]))


def _shadow_stamp(root: str, ctx: dict) -> str:
    """影子 build 裡的 `CGC_BUILD_PROFILE.txt`：**建置脚本跑到最後**才會寫。它是「照建置脚本建置」
    這個主張的憑據 —— 沒有它，收貨只證明「兩顆目標在」，不證明「是那份脚本建的」。"""
    return read_text(_shadow_paths(root, ctx)["stamp"]).strip()


def _shadow_build_done(root: str, ctx: dict) -> tuple:
    """P3 的 done：收貨成立 **且** 有 profile 戳記。只有前者時 P3 不算完成 ⇒ 下一次 `--go` 會用同一份
    脚本、`REBUILD=0` 接續建置（分幾輪跑得完；每一輪都從已經建好的地方繼續）。"""
    ok, msg = _shadow_built(root, ctx)
    if not ok:
        return False, msg
    stamp = _shadow_stamp(root, ctx)
    if not stamp:
        return False, msg + "（建置脚本未跑完：無 profile 戳記 ⇒ 可接續）"
    return True, msg + "；profile 戳記：%s" % stamp


def _shadow_evidence_done(root: str, ctx: dict) -> tuple:
    """P4 的 done：證據在、且它記的正是**現在**的收貨與戳記（建置脚本跑完後戳記會變 ⇒ 證據自動重寫）。"""
    p = _shadow_paths(root, ctx)
    ev = _read_json(p["evidence"]) or {}
    fresh = (os.path.exists(p["evidence"])
             and ev.get("receipts", {}).get("s2") == _shadow_built(root, ctx)[1]
             and ev.get("build", {}).get("profile_stamp", "") == _shadow_stamp(root, ctx)
             and ev.get("prepare", {}).get("src_newest_mtime")
             == _newest_mtime(os.path.join(root, "src", "llama.cpp")))
    return fresh, "證據：%s" % ("在（最新）" if fresh else ("在（過期）" if os.path.exists(p["evidence"]) else "不在"))


def _shadow_evidence(root: str, ctx: dict) -> tuple:
    """寫 `shadow.json`：足跡、磁碟、旗標（從**影子自己的** CMakeCache 讀，不是從意圖）、
    建置產物（大小＋md5）、S1/S2 的收貨。這一步就是「證據落檔」。"""
    p = _shadow_paths(root, ctx)
    bs = RPA.build_state(p["shadow"])
    cache = read_text(os.path.join(p["shadow"], "src/llama.cpp/build/CMakeCache.txt"))
    flags = {}
    for key in ("CMAKE_BUILD_TYPE", "CMAKE_CXX_FLAGS", "GGML_METAL", "LLAMA_BUILD_SERVER",
                "LLAMA_BUILD_TESTS", "GGML_BLAS", "GGML_ACCELERATE", "GGML_CPU_REPACK"):
        m = re.search(r"^%s:[A-Z]+=(.*)$" % key, cache, re.M)
        if m:
            flags[key] = m.group(1).strip()
    stamp = read_text(p["stamp"]).strip()
    # 脚本自己的 log：它除了 C++ 建置還會做 UI 段（npm／下載）——那個在這台機器上是壞的（node 18 對
    # 不上 storybook 的 >=20），但它**不擋** C++ 產物，腳本也會繼續走到戳記。照實記，不假裝全綠。
    bscript_log = read_text(os.path.join(root, SHADOW_LOG_DIR_REL, "P3_build.log"))
    log_note = dict(
        bytes=len(bscript_log),
        reached_100pct=("Built target llama-server" in bscript_log),
        npm_errors=bscript_log.count("npm error"),
        ui_download_failed="UI: download dist.tar.gz" in bscript_log
        and 'failed: "HTTP response code said error"' in bscript_log,
        warning_llama_simple="WARNING: llama-simple not found" in bscript_log,
        cmd="CGC_BUILD_PROFILE=devserver REBUILD=0 JOBS=%s bash %s" % (
            os.environ.get("CGC_SHADOW_JOBS", "6"), safe_rel(p["build_script"], root)))
    binds = {}
    for b in ("llama-server", "llama-bench"):
        f = os.path.join(p["shadow"], "src/llama.cpp/build/bin", b)
        if os.path.exists(f):
            binds[b] = dict(bytes=os.path.getsize(f), md5=_md5(f))
    libs = {}
    for f, d in sorted(bs["files"].items()):
        if d["rho"]:
            libs[os.path.basename(f)] = dict(md5=_md5(os.path.join(p["shadow"], f)), rho=d["rho"])
    ev = _read_json(p["evidence"]) or {}
    ev.update(dict(
        product="ρ 切分的影子驗（零 GPU）", shadow=safe_rel(p["shadow"], root),
        at=time.strftime("%F %T %z"),
        prepare=dict(patch_md5=RPA.patch_state(p["shadow"])["md5"],
                     patch_md5_pinned=RPA.PATCH_MD5,
                     src_newest_mtime=_newest_mtime(os.path.join(root, "src", "llama.cpp")),
                     build_script_md5=_md5(os.path.join(root, "scripts", "build_fork_llama.sh")),
                     models_link=safe_rel(p["models_link"], root),
                     models_real=safe_rel(p["models_real"], root)),
        apply=dict(check=_shadow_applied(root, ctx)[1]),
        build=dict(profile_stamp=stamp, flags=flags, built=bs["built"],
                   dylibs_with_rho=libs, binaries=binds, script_log=log_note),
        footprint=dict(shadow_mb=_dir_mb(p["shadow"]),
                       build_mb=_dir_mb(os.path.join(p["shadow"], "src/llama.cpp/build")),
                       disk_free_gb=_disk_free_gb(root)),
        box=dict(ncpu=(_out(["sysctl", "-n", "hw.ncpu"])[1] or "").strip(),
                 load=(_out(["uptime"])[1] or "").strip()),
        receipts=dict(s1=_shadow_applied(root, ctx)[1], s2=_shadow_built(root, ctx)[1]),
    ))
    with open(p["evidence"], "w", encoding="utf-8") as fh:
        json.dump(ev, fh, ensure_ascii=False, indent=1)
    summary = ("證據落檔 %s（影子 %d MB／build %d MB／磁碟剩 %.1f GB；標籤 %s；脚本 100%%=%s）"
               % (safe_rel(p["evidence"], root), ev["footprint"]["shadow_mb"],
                  ev["footprint"]["build_mb"], ev["footprint"]["disk_free_gb"],
                  safe_rel(p["shadow"], root), log_note["reached_100pct"]))
    # in-process 步的合約是 (rc, txt, ok, why)：txt 進 P4.log（可接續時看得見上一輪做了什麼）。
    return 0, summary + "\n", True, summary


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _md5(path: str) -> str:
    import hashlib
    try:
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    except OSError:
        return ""


def _shadow_done(root: str, ctx: dict) -> tuple:
    """`--shadow` 的完成條件＝S1/S2 在影子上都綠**且**證據落檔（避免「做了但沒留下讀數」）。"""
    a = _shadow_applied(root, ctx)
    b = _shadow_built(root, ctx)
    ev = _shadow_paths(root, ctx)["evidence"]
    ok = a[0] and b[0] and os.path.exists(ev)
    return ok, "S1: %s；S2: %s；證據：%s" % (a[1], b[1],
                                            "在" if os.path.exists(ev) else "不在")


def shadow_steps(ctx: dict) -> list:
    """影子驗的四步（同一套 skip／fail-fast／可接續）。"""
    root = ctx["root"]
    p = _shadow_paths(root, ctx)
    shadow_rel = safe_rel(p["shadow"], root)

    def skip_models(r, c):
        return ((os.path.islink(p["models_link"]) and os.path.realpath(p["models_link"])
                 == os.path.realpath(p["models_real"])), "models 已連到實樹")

    def skip_script(r, c):
        return ((os.path.exists(p["build_script"]) and os.path.exists(os.path.join(root, "scripts", "build_fork_llama.sh"))
                 and open(p["build_script"], "rb").read()
                 == open(os.path.join(root, "scripts", "build_fork_llama.sh"), "rb").read()),
                "建置脚本已是同一份")

    def skip_applied(r, c):
        return _shadow_applied(r, c)[0], "影子已是切分後的原始碼（--check RUNNABLE）"

    def skip_built(r, c):
        if c.get("shadow_rebuild"):
            return False, ""      # 明確要求重建 ⇒ 連脚本自己的「已完成」也一起重來
        return _shadow_build_done(r, c)

    return [
        dict(id="P1", name="複製 src/（乾淨複本；預設不清 build/）＋補丁、scripts、models 連線", commands=[
            ["bash", "-c", "mkdir -p %s %s %s" % (
                _q(os.path.join(p["shadow"], "src")), _q(os.path.join(p["shadow"], "scripts")),
                _q(os.path.join(p["shadow"], os.path.dirname(RPA.PATCH_REL))))],
            # 乾淨複本：清掉來源檔（保留 build/ —— 那是最花時間的產物，也是增量建置的依據）。
            ["bash", "-c", "find %s -mindepth 1 -maxdepth 1 -not -name build -exec rm -rf {} +"
             % _q(os.path.join(p["shadow"], "src", "llama.cpp"))],
            ["rsync", "-a", "--exclude", "build/", "--exclude", ".git*",
             "src/llama.cpp/", os.path.join(p["shadow"], "src/llama.cpp") + "/"],
            ["rsync", "-a", "scripts/", os.path.join(p["shadow"], "scripts") + "/"],
            ["bash", "-c", "cp -f %s %s" % (_q(os.path.join(root, RPA.PATCH_REL)), _q(p["patch"]))],
            dict(cmd=["ln", "-sfn", p["models_real"], p["models_link"]], skip_if=skip_models,
                 log="%s/P1_models.log" % SHADOW_LOG_DIR_REL),
        ], done=_shadow_prepared, receipt=_shadow_prepared,
            artifacts=[shadow_rel], next_on_fail=[
                "df -h .                                   # 影子要 ~0.5 GB（複本）＋（從零）~0.5 GB（建置）",
                "ls -l %s/models                     # 應指向實樹的 models（30G，只讀不複製）" % shadow_rel,
                "python3 scripts/check/rho_price_authrow.py --checklist   # 補丁 md5／base 的原始出處"]),
        # ⚠ `git apply --directory` **不接受絕對路徑**（實測：`error: invalid path '<abs>'`）⇒ 兩個
        # 路徑都寫成 root 相對（runner 用 cwd=root 叫它）。影子若在 repo 外，就在宣告時拒收而不是
        # 留一個 rc≠0 的謎。
        dict(id="P2", name="套用補丁（進影子，不進實樹）", commands=[
            dict(cmd=["git", "apply", "-p1", "--directory=" + _rel_or_die(root, p["shadow"]),
                      RPA.PATCH_REL], skip_if=skip_applied,
                 log="%s/P2_apply.log" % SHADOW_LOG_DIR_REL)],
            done=_shadow_applied, receipt=_shadow_applied, artifacts=[shadow_rel],
            next_on_fail=[
                "python3 scripts/check/rho_price_authrow.py --check --root %s   # 哪一個座標沒翻" % shadow_rel,
                "git apply -p1 --directory=%s <patch> --check              # 衝突時會是原子的失敗" % shadow_rel]),
        dict(id="P3", name="建置（跑影子自己的 build_fork_llama.sh）", commands=[
            # env 直接寫進命令：CGC_BUILD_PROFILE=devserver（實樹的 live build 就是這一份）、
            # REBUILD 預設 0（增量；--shadow-rebuild ⇒ 1）、JOBS 留 4 核給別人。
            dict(cmd=["bash", "-c", "CGC_BUILD_PROFILE=devserver REBUILD=%d JOBS=%s bash %s" % (
                    1 if ctx.get("shadow_rebuild") else 0,
                    os.environ.get("CGC_SHADOW_JOBS", "6"), _q(p["build_script"]))],
                 skip_if=skip_built, log="%s/P3_build.log" % SHADOW_LOG_DIR_REL)],
            done=_shadow_build_done, receipt=_shadow_built, artifacts=[shadow_rel], next_on_fail=[
                "grep -n 'error' %s/P3_build.log | head   # 建置錯的第一手" % SHADOW_LOG_DIR_REL,
                "scripts/build_fork_llama.sh:215            # Mac 的 configure 必須帶 -DCMAKE_CXX_FLAGS=-DMTP_SUPPORT",
                "CGC_BUILD_PROFILE=devserver bash %s/scripts/build_fork_llama.sh   # 手動重跑（同一份腳本）" % shadow_rel]),
        dict(id="P4", name="足跡與證據落檔", commands=[], inprocess=_shadow_evidence,
            done=_shadow_evidence_done,
            receipt=_shadow_done, artifacts=[safe_rel(p["evidence"], root)], next_on_fail=[
                "cat %s       # 足跡／旗標／建置產物 md5／S1+S2 收貨" % safe_rel(p["evidence"], root),
                "du -sh %s                           # 影子佔用" % shadow_rel]),
    ]


def shadow_preflight(root: str, ctx: dict) -> dict:
    """影子驗的前置：補丁可上（實樹 base 沒漂）、磁碟夠、盒子沒有 llama 在跑（建置會搶 CPU）。"""
    reasons, notes = [], []
    ps = RPA.patch_state(root)
    if not ps["md5_ok"]:
        reasons.append("補丁 md5 ≠ 釘住的值 ⇒ 先重新驗證（影子會用同一份）")
    if not ps["bases_ok"]:
        reasons.append("實樹的 base 已經不是補丁的那一份 ⇒ 先解決（影子會複製成一份上不去的樹）")
    free = _disk_free_gb(root)
    if 0 <= free < SHADOW_MIN_FREE_GB:
        reasons.append("磁碟只剩 %.1f GB（< %.1f GB）⇒ 影子驗不做" % (free, SHADOW_MIN_FREE_GB))
    jobs = _llama_jobs()
    if jobs:
        reasons.append("盒子不閒（%d 個 llama 行程）⇒ 建置會和他們搶 CPU：%s" % (len(jobs), jobs[0]))
    p = _shadow_paths(root, ctx)
    notes.append("影子目錄：%s（目前 %d MB；磁碟剩 %.1f GB）" % (safe_rel(p["shadow"], root),
                                                                 _dir_mb(p["shadow"]), free))
    notes.append("零 GPU：只複製、套用、建置（影子自己的 build_fork_llama.sh）與收貨；不發 server／bench。")
    notes.append("驗綠之後：同一個窗口可以 `--root %s --go --only S3,S4` 在**隔離的**影子上跑 oracle。"
                 % safe_rel(p["shadow"], root))
    return dict(ok=not reasons, reasons=reasons, notes=notes,
                header=["補丁 md5 %s %s" % (ps["md5"], "（釘住的值）" if ps["md5_ok"] else "（≠ 釘住的值）")])


def shadow_main(argv=None) -> int:
    return WR.main(shadow_steps, preflight=shadow_preflight, journal=SHADOW_JOURNAL_REL,
                   log_dir=SHADOW_LOG_DIR_REL, title=SHADOW_TITLE,
                   ctx_factory=shadow_ctx_factory,
                   argv=sys.argv[1:] if argv is None else argv)


def shadow_ctx_factory(args, rest) -> dict:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--shadow-dir", default=SHADOW_DIR_REL)
    ap.add_argument("--shadow-rebuild", action="store_true",
                    help="建置前先清空影子的 build（預設增量）")
    ns, unknown = ap.parse_known_args(rest)
    if unknown:      # fail-closed：不認得的旗標拒收，不靜默忽略（打錯一個字就變成「驗了別的東西」）
        raise SystemExit("不懂的旗標：%s（--shadow 模式只認 --shadow-dir／--shadow-rebuild；\n"
                         "核心的 --status／--go／--only／--root／--list-steps 也可以。見 usage）"
                         % " ".join(unknown))
    return dict(shadow_dir=ns.shadow_dir, shadow_rebuild=ns.shadow_rebuild)


def _json_of(cmd: dict) -> str:
    c = cmd["cmd"]
    return c[c.index("--json") + 1]


def safe_rel(p: str, root: str = ROOT) -> str:
    """顯示／log 用的相對路徑（影子樹這種外面來的路徑也給得出人看得懂的字串）。"""
    return os.path.relpath(p, root) if p.startswith(root + os.sep) else p


# ── CLI ─────────────────────────────────────────────────────────────────────

def ctx_factory(args, rest) -> dict:
    """ρ 自己的旗標：`--date`（tag 的日期）與 `--reuse-ctl`（重用既有錨）。"""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--date", default=None)
    ap.add_argument("--reuse-ctl", default=None)
    ns, unknown = ap.parse_known_args(rest)
    if unknown:
        raise SystemExit("不懂的旗標：%s（見 rho_window.py 的 usage）" % " ".join(unknown))
    return dict(date=ns.date or time.strftime("%Y%m%d"), reuse_ctl=ns.reuse_ctl)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--shadow" in argv:                      # 影子驗是這支腳本的另一個模式（同一套流程）
        return shadow_main([a for a in argv if a != "--shadow"])
    return WR.main(steps, preflight=preflight, journal=JOURNAL_REL, log_dir=LOG_DIR_REL,
                   title=TITLE, ctx_factory=ctx_factory, argv=argv)


# ── selftest ────────────────────────────────────────────────────────────────

def selftest() -> int:
    """ρ 這一半的體檢（流程那一半在 window_runner --selftest）。最後一段是**整段彩排**。"""
    import contextlib
    import io
    import tempfile

    ok = [0, 0]

    def case(name, cond, detail=""):
        ok[1] += 1
        ok[0] += 1 if cond else 0
        print("  %-58s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                   "" if cond else "  " + str(detail)))

    def quiet(fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **kw)
        return rc, buf.getvalue()

    print("rho_window --selftest（宣告 ＋ receipts；流程在 window_runner）")
    case("_rate: 9/9 -> (9,9)", _rate("9/9") == (9, 9))
    case("_rate: 4/9 -> (4,9)", _rate("4/9") == (4, 9))
    case("_rate: 壞字串 -> None", _rate("M1 ok") is None and _rate(None) is None)
    r1 = log_receipts("noise\nCGC-RHO-SUM: steps=1\nllama_expert_cache: final stats: prefetch=54/7\n")
    case("log_receipts: 1 行 CGC-RHO- 與 prefetch=54/7",
         (r1["rho_lines"], r1["prefetch"]) == (1, (54, 7)), r1)
    case("log_receipts: prefetch=0/0 照實回（不是『沒開』也不是『免費』）",
         log_receipts("prefetch=0/0\n")["prefetch"] == (0, 0))
    case("log_receipts: 空 log -> has_text=False", log_receipts("")["has_text"] is False)
    case("ctl_tag_of: 從 summary 檔名反推 tag",
         ctl_tag_of("Backup/x/summary_anchor-0919.json", "t") == "anchor-0919"
         and ctl_tag_of("", "t") == "t" and ctl_tag_of("weird.json", "t") == "")
    case("六步的 id 與名字表對得上", STEP_IDS == tuple(STEP_NAMES) and len(STEP_IDS) == 6)
    case("每一步都有 next_on_fail（失敗時才知道看哪裡）",
         all(NEXT_ON_FAIL.get(sid) for sid in STEP_IDS))

    with tempfile.TemporaryDirectory() as td:
        def _summ(name, **over):
            d = dict(tag="t", profile="prefill250", comparable=True, ok=True,
                     m1_numeric_identity="9/9", m2_decision_agreement="9/9",
                     m3_topk_set_agreement="9/9", extra_env={"CGC_SERVER_BATCH": "6144"},
                     pool_counters={"found": True})
            d.update(over)
            p = os.path.join(td, name)
            json.dump(d, open(p, "w", encoding="utf-8"))
            return p
        case("summary: 9/9 且可比 -> ok", summary_state(_summ("a.json"))["ok"] is True)
        st = summary_state(_summ("b.json", m1_numeric_identity="4/9"))
        case("summary: M1 4/9 -> 不過且點名 M1", (st["ok"] is False) and "M1" in st["why"], st["why"])
        case("summary: comparable=False -> 不過",
             summary_state(_summ("c.json", comparable=False))["ok"] is False)
        st = summary_state(_summ("d.json", m2_decision_agreement="3/9"), anchor=True)
        case("anchor: M2 掉 -> 不過（錨必須 M1=M2=M3=n）", st["ok"] is False, st["why"])
        case("summary: 不在 -> 不過（fail-closed）",
             summary_state(os.path.join(td, "nope.json"))["ok"] is False)

        # oracle 收貨：banner 指名的伺服器 log 才是要讀的那一份（banner 自己永遠是 0）。
        od = os.path.join(td, "tree", ORACLE_DIR)
        os.makedirs(od, exist_ok=True)
        slog = os.path.join(td, "srv.log")
        with open(slog, "w", encoding="utf-8") as fh:
            fh.write("[CGC] hello\nllama_expert_cache: final stats: prefetch=116/20\n")
        with open(os.path.join(od, "launch_arm.log"), "w", encoding="utf-8") as fh:
            fh.write("banner\n[log]   %s（tail -f 同路徑）\n" % slog)
        case("server_log_of: 從 banner 的 [log] 行拿到真的伺服器 log",
             server_log_of(os.path.join(td, "tree"), "arm") == slog)
        case("server_log_of: banner 不在 ⇒ 空字串（不 glob、不猜）",
             server_log_of(os.path.join(td, "tree"), "nope") == "")
        case("log_receipts: banner 自己 → 0 行（這就是那個假綠）",
             log_receipts(read_text(os.path.join(od, "launch_arm.log")))["rho_lines"] == 0)
        st = summary_state(_summ("e1.json", extra_env={"CGC_RHO": "1"}), want_env={"CGC_RHO": "1"})
        case("env 收貨: extra_env 帶 CGC_RHO=1 -> 過（旗標進了子行程）", st["ok"] is True, st["why"])
        st = summary_state(_summ("e2.json"), want_env={"CGC_RHO": "1"})
        case("env 收貨: 沒帶 -> 不過（旗標沒進子行程）",
             st["ok"] is False and "沒進子行程" in st["why"], st["why"])
        st = summary_state(_summ("e3.json", extra_env={"CGC_RHO": "1"}), want_env={"CGC_RHO": None})
        case("env 收貨: 對照臂帶了 -> 不過（對照臂不得帶交付旗標）",
             st["ok"] is False and "不該帶" in st["why"], st["why"])

        # 交付臂：summary 綠＋伺服器 log 零 CGC-RHO-* ⇒ 過；有一行 ⇒ 不過（量具外洩）。
        th = os.path.join(td, "tree2")
        os.makedirs(os.path.join(th, ORACLE_DIR), exist_ok=True)
        _summ(os.path.join("tree2", ORACLE_DIR, "summary_rho-split-deliver-20260101.json"),
              extra_env={"CGC_RHO": "1"})
        with open(os.path.join(th, ORACLE_DIR, "launch_rho-split-deliver-20260101.log"),
                  "w", encoding="utf-8") as fh:
            fh.write("[log]   %s\n" % slog)
        ok_d, why_d, _ = oracle_receipts(th, "rho-split-deliver-20260101", "deliver")
        case("oracle_receipts(deliver): 全綠 ⇒ 過且零 CGC-RHO-*", ok_d is True, why_d)
        bad_log = os.path.join(td, "srv_bad.log")
        with open(bad_log, "w", encoding="utf-8") as fh:
            fh.write("[CGC-RHO-SUM] steps=1 layers=2\n")
        with open(os.path.join(th, ORACLE_DIR, "launch_rho-split-deliver-20260101.log"),
                  "w", encoding="utf-8") as fh:
            fh.write("[log]   %s\n" % bad_log)
        ok_d, why_d, _ = oracle_receipts(th, "rho-split-deliver-20260101", "deliver")
        case("oracle_receipts(deliver): 伺服器 log 有一行 CGC-RHO-* ⇒ 不過（量具外洩）",
             ok_d is False and "外洩" in why_d, why_d)

        # 池差分 + 影子樹的 dirty 判定（不把母樹的 dirty 記到影子樹頭上）。
        sl_b = os.path.join(td, "srv_b.log")
        with open(sl_b, "w", encoding="utf-8") as fh:
            fh.write("final stats: prefetch=168/20\n")
        os.makedirs(os.path.join(td, "tree3", ORACLE_DIR), exist_ok=True)
        for tg, lp in (("c", slog), ("b", sl_b)):
            with open(os.path.join(td, "tree3", ORACLE_DIR, "launch_%s.log" % tg),
                      "w", encoding="utf-8") as fh:
                fh.write("[log]   %s\n" % lp)
        case("pool_diff: 116→168 ⇒ B−A = +52",
             "+52" in pool_diff(os.path.join(td, "tree3"), "c", "b"))
        case("pool_diff: 重用外部錨但 tag 反推不出來 ⇒ 不算（不拿別的 run 當這一場）",
             "不算" in pool_diff(os.path.join(td, "tree3"), "", "b"))
        case("_src_dirty: 不是 git repo ⇒ 空清單（影子樹不繼承母樹的 dirty）",
             _src_dirty(td) == [])
        case("_src_dirty: 實樹看得到別線未提交的檔", len(_src_dirty(ROOT)) > 0, len(_src_dirty(ROOT)))

        # 影子模式：P3 的完成條件不是「有沒有產物」，而是「**照建置脚本**建完了沒」——憑據是脚本自己
        # 在最後寫下的 profile 戳記。只有產物、沒有戳記 ⇒ 未完成 ⇒ 下一次 --go 用同一份脚本接續。
        sh = os.path.join(td, "shadow")
        RPA._fixture_split(sh, metered=True)      # 影子裡的原始碼（切分已落地）——證據要讀它
        os.makedirs(os.path.join(sh, "src/llama.cpp/build/bin"), exist_ok=True)
        sctx = dict(root=td, shadow_dir=sh)
        for b in ("llama-server", "llama-bench"):
            with open(os.path.join(sh, "src/llama.cpp/build/bin", b), "w", encoding="utf-8") as fh:
                fh.write("x\n")
        with open(os.path.join(sh, "src/llama.cpp/build/bin/libllama.9.dylib"),
                  "w", encoding="utf-8") as fh:
            fh.write("CGC_RHO\n")
        case("影子: 兩顆目標在且 dylib 帶 CGC_RHO ⇒ S2 收貨成立",
             _shadow_built(td, sctx)[0] is True, _shadow_built(td, sctx)[1])
        case("影子: 收貨成立但無 profile 戳記 ⇒ P3 不算完成（可接續）",
             _shadow_build_done(td, sctx)[0] is False and "戳記" in _shadow_build_done(td, sctx)[1],
             _shadow_build_done(td, sctx)[1])
        with open(_shadow_paths(td, sctx)["stamp"], "w", encoding="utf-8") as fh:
            fh.write("profile=devserver\nbuilt_at=1\n")
        case("影子: 戳記出現（＝脚本跑到最後）⇒ P3 完成且點名 profile",
             _shadow_build_done(td, sctx)[0] is True and "devserver" in _shadow_build_done(td, sctx)[1],
             _shadow_build_done(td, sctx)[1])
        def _raises(fn):
            try:
                fn()
                return False
            except SystemExit:
                return True

        case("影子: 不懂的旗標拒收（不靜默忽略）",
             _raises(lambda: shadow_ctx_factory(None, ["--nope"])))
        case("影子: --shadow-rebuild 解析得到、預設 False",
             shadow_ctx_factory(None, ["--shadow-rebuild"])["shadow_rebuild"] is True
             and shadow_ctx_factory(None, [])["shadow_rebuild"] is False)
        st3 = [s for s in shadow_steps(dict(root=td, shadow_dir=sh, shadow_rebuild=True))
               if s["id"] == "P3"][0]
        case("--shadow-rebuild: P3 的 skip_if 直接讓路（不因已完成而跳過）",
             st3["commands"][0]["skip_if"](td, dict(shadow_rebuild=True))[0] is False)
        case("--shadow-rebuild: 命令裡的 REBUILD 是 1",
             "REBUILD=1" in " ".join(st3["commands"][0]["cmd"]),
             st3["commands"][0]["cmd"])
        logp = os.path.join(td, SHADOW_LOG_DIR_REL, "P3_build.log")
        os.makedirs(os.path.dirname(logp), exist_ok=True)
        with open(logp, "w", encoding="utf-8") as fh:
            fh.write("[100%] Built target llama-server\nnpm error code EBADENGINE\n"
                     "UI: download dist.tar.gz from b695 failed: \"HTTP response code said error\"\n"
                     "WARNING: llama-simple not found\n")
        os.makedirs(os.path.dirname(_shadow_paths(td, sctx)["evidence"]), exist_ok=True)
        _rslt = _shadow_evidence(td, sctx)
        case("影子: 證據落檔 ⇒ in-process 合約 (rc,txt,ok,why) 與「最新」都對",
             isinstance(_rslt, tuple) and len(_rslt) == 4 and _rslt[0] == 0 and _rslt[2] is True
             and _shadow_evidence_done(td, sctx)[0] is True,
             _shadow_evidence_done(td, sctx)[1])
        _ev = _read_json(_shadow_paths(td, sctx)["evidence"])
        case("影子: 證據照實記下脚本 log（100%／npm 壞掉／UI 下載失敗）——不假裝全綠",
             _ev["build"]["script_log"]["reached_100pct"] is True
             and _ev["build"]["script_log"]["npm_errors"] == 1
             and _ev["build"]["script_log"]["ui_download_failed"] is True
             and "devserver" in _ev["build"]["script_log"]["cmd"], _ev["build"]["script_log"])
        with open(_shadow_paths(td, sctx)["stamp"], "w", encoding="utf-8") as fh:
            fh.write("profile=devserver\nbuilt_at=2\n")
        case("影子: 戳記變了 ⇒ 證據被判過期（下次 --go 自動重寫）",
             _shadow_evidence_done(td, sctx)[0] is False, _shadow_evidence_done(td, sctx)[1])

        # S5 的收貨：四份產物要 QUOTABLE、成對、零 CGC-RHO-*、而且**臂是對的**。
        pd = os.path.join(td, "price")
        os.makedirs(pd, exist_ok=True)

        def _mk_quad(rho_a="0", log_a="final stats: prefetch=116/20\n",
                     log_b="final stats: prefetch=168/20\n"):
            prods = []
            for order in (1, 2):
                for arm, s, rho in (("A", [12.0, 12.2, 12.1], rho_a),
                                    ("B", [12.4, 12.5, 12.45], "1")):
                    p = os.path.join(pd, "order%d_%s.json" % (order, arm))
                    RPA._mk_product(p, avg=(12.2 if arm == "A" else 12.5), samples=s, rho=rho)
                    with open(p[:-len(".json")] + ".stderr.log", "w", encoding="utf-8") as fh:
                        fh.write(log_a if arm == "A" else log_b)
                    prods.append(p)
            return prods
        ok5, why5 = _price_rows(td, _mk_quad())
        case("S5 收貨: 四份都可引用、成對、兩臂 env 對 ⇒ 過且印出池差分",
             ok5 is True and "+52" in why5, why5)
        ok5, why5 = _price_rows(td, _mk_quad(rho_a="1"))
        case("S5 收貨: A 臂也帶 CGC_RHO ⇒ 不過（這不是對照臂）",
             ok5 is False and "不是對照臂" in why5, why5)
        ok5, why5 = _price_rows(td, _mk_quad(log_b="[CGC-RHO-CAP] x\n"))
        case("S5 收貨: B 臂 stderr 有 CGC-RHO-* ⇒ 不過（量具外洩）",
             ok5 is False and "外洩" in why5, why5)
        os.remove(os.path.join(pd, "order2_B.stderr.log"))
        ok5, why5 = _price_rows(td, [os.path.join(pd, f) for f in sorted(os.listdir(pd))
                                     if f.endswith(".json")])
        case("S5 收貨: 缺成對 stderr log ⇒ 不過（不可引用）",
             ok5 is False and "成對" in why5, why5)

        # 計畫：切分已落地的樹 ⇒ S1/S2 done、其餘 pending；每個 pending 都印得出命令。
        fx = os.path.join(td, "tree")
        RPA._fixture_split(fx, metered=True)
        os.makedirs(os.path.join(fx, "src/llama.cpp/build/bin"), exist_ok=True)
        with open(os.path.join(fx, "src/llama.cpp/build/bin/libllama.9.dylib"), "w") as fh:
            fh.write("CGC_RHO\nCGC_RHO_PROBE\n")
        ctx = dict(root=fx, date="20260101", reuse_ctl=None)
        st = WR.plan(steps(ctx), root=fx, ctx=ctx)
        case("plan: 六步且順序固定", [s["id"] for s in st] == list(STEP_IDS))
        case("plan(fixture): S1/S2 done、S3–S6 pending",
             [s["state"] for s in st] == ["done", "done", "pending", "pending", "pending", "pending"],
             [s["state"] for s in st])
        case("plan(fixture): S5 四條命令都印得出來（計畫＝會跑什麼）",
             len(st[4]["commands"]) == 4 and all("harness.py bench" in " ".join(c)
                                                 for c in st[4]["commands"]))
        # --reuse-ctl：S3 不發車，改成「認領＋當場驗」那顆錨（inprocess，沒有命令）。
        ctx_r = dict(root=fx, date="20260101", reuse_ctl="Backup/x/summary_anchor-0919.json")
        st_r = WR.plan(steps(ctx_r), root=fx, ctx=ctx_r)
        case("--reuse-ctl: S3 沒有命令、改成 inprocess 驗那顆錨",
             st_r[2]["commands"] == [] and "pending" == st_r[2]["state"], st_r[2])

        # 整段彩排：fixture 的 S1/S2 ＋ 假 runner 寫出真形狀的 S3–S6 產物 ⇒ 六步跑完。
        def fake_all(cmd, cwd=None, log=None):
            joined = " ".join(cmd)
            if "m123_oracle_gate.py" in joined:
                tag = cmd[cmd.index("--tag") + 1]
                deliver = "CGC_RHO=1" in joined
                od2 = os.path.join(fx, ORACLE_DIR)
                os.makedirs(od2, exist_ok=True)
                env = {"CGC_SERVER_BATCH": "6144"}
                if deliver:
                    env["CGC_RHO"] = "1"
                with open(os.path.join(od2, "summary_%s.json" % tag), "w", encoding="utf-8") as fh:
                    json.dump(dict(tag=tag, profile="prefill250", comparable=True, ok=True,
                                   m1_numeric_identity="9/9", m2_decision_agreement="9/9",
                                   m3_topk_set_agreement="9/9", extra_env=env,
                                   pool_counters={"found": True}), fh)
                srv = os.path.join(fx, "srv_%s.log" % tag)
                with open(srv, "w", encoding="utf-8") as fh:
                    fh.write("final stats: prefetch=%d/20\n" % (168 if deliver else 116))
                with open(os.path.join(od2, "launch_%s.log" % tag), "w", encoding="utf-8") as fh:
                    fh.write("[log]   %s（tail -f 同路徑）\n" % srv)
                return 0, "fake m123 %s\n" % tag
            if "--json" in cmd:
                j = os.path.join(cwd or ROOT, cmd[cmd.index("--json") + 1])
                b_arm = "CGC_RHO=1" in joined
                RPA._mk_product(j, avg=(12.5 if b_arm else 12.2),
                                samples=([12.4, 12.5, 12.45] if b_arm else [12.0, 12.2, 12.1]),
                                rho=("1" if b_arm else "0"))
                with open(IB.pair_canonical(j), "w", encoding="utf-8") as fh:
                    fh.write("final stats: prefetch=%d/20\n" % (168 if b_arm else 116))
                return 0, "fake bench %s\n" % ("B" if b_arm else "A")
            return 0, "noop\n"

        def fake_runner_fail(cmd, cwd=None, log=None):
            return 1, "boom\n"

        class _OpenPreflight:
            """preflight 只擋「還沒輪到你」；彩排要驗的是發車之後的流程 ⇒ 把它的來源換成綠的。"""

            def __enter__(self):
                self._old = (RPA.patch_state, globals()["_llama_jobs"], globals()["_src_dirty"])
                RPA.patch_state = lambda root=ROOT: dict(path=RPA.PATCH_REL, present=True, md5="x",
                                                         md5_ok=True, bases={}, bases_ok=True)
                globals()["_llama_jobs"] = lambda: []
                globals()["_src_dirty"] = lambda root: []

            def __exit__(self, *a):
                RPA.patch_state, globals()["_llama_jobs"], globals()["_src_dirty"] = self._old

        calls = []

        def fake_any(cmd, cwd=None, log=None):
            calls.append(cmd)
            return fake_all(cmd, cwd=cwd, log=log)

        with _OpenPreflight():
            rc, out = quiet(WR.run, steps(dict(root=fx, date="20260101")), root=fx, go=False,
                            runner=fake_any, journal=os.path.join(td, "w1.jsonl"),
                            title=TITLE, preflight=preflight)
        case("--status 不呼叫 runner（彩排前）", calls == [] and rc == 2, (rc, len(calls)))
        with _OpenPreflight():
            rc, out = quiet(WR.run, steps(dict(root=fx, date="20260101")), root=fx, go=True,
                            runner=fake_runner_fail, journal=os.path.join(td, "w2.jsonl"),
                            title=TITLE, preflight=preflight)
        case("--go：失敗就停（S1/S2 已在 ⇒ 第一次失敗在 S3）且印 hints",
             rc == 1 and "下一個該看的東西" in out, out[-200:])
        w2 = [json.loads(l) for l in open(os.path.join(td, "w2.jsonl"), encoding="utf-8")]
        case("--go：journal 記到 fail（可接續）",
             any(r["step"] == "S3" and r["status"] == "fail" for r in w2), w2)
        with _OpenPreflight():
            rc, out = quiet(WR.run, steps(dict(root=fx, date="20260101")), root=fx, go=True,
                            runner=fake_all, journal=os.path.join(td, "w3.jsonl"),
                            title=TITLE, preflight=preflight)
        case("彩排: S1→S6 一次跑完（rc=0、「全部完成」）", rc == 0 and "全部完成" in out, out[-260:])
        st6 = WR.plan(steps(dict(root=fx, date="20260101")), root=fx,
                      ctx=dict(root=fx, date="20260101"))
        case("彩排: 六步全部 done", [s["state"] for s in st6] == ["done"] * 6,
             [(s["id"], s["state"]) for s in st6])
        vp = os.path.join(fx, PRICE_DIR, "verdict.json")
        case("彩排: verdict.json = PRICE",
             os.path.exists(vp) and json.load(open(vp, encoding="utf-8"))["verdict"] == "PRICE")
        w3 = os.path.join(td, "w3.jsonl")
        recs3 = [json.loads(l) for l in open(w3, encoding="utf-8")] if os.path.exists(w3) else []
        case("彩排: journal 六步都有記錄（可接續）",
             {r["step"] for r in recs3} == set(STEP_IDS), sorted({r.get("step") for r in recs3}))

    print("== selftest %d/%d ==" % (ok[0], ok[1]))
    return 0 if ok[0] == ok[1] else 1


def _raises(fn) -> bool:
    try:
        fn()
    except BaseException:  # noqa: BLE001
        return True
    return False


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())
