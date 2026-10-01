#!/usr/bin/env python3
"""機器判定：這一輪的**量具有沒有真的綁上圖**（不是「旗標有沒有設」）。

WHY THIS EXISTS
---------------
2026-09-29 凌晨，交付 cell 的「單次提交補齊臂」連跑四場都沒有產物。事後讀 stderr 才看到：
`CGC-MM-PUB n_leaf=0 wrote=0`、`CGC-MISSMASK-STEP 0`、`CGC-RB-FEED 0` —— 三個計數器**全部是 0**，
也就是「武裝了、卻沒有綁上圖」。而同一天早些時候，預設 cell 的同一支臂是
`n_leaf=39 wrote=39`、`feeds=14000`、`step=390`：同一顆 binary、同一個 arm 字串。

這和 `claim_instrument_check.py` 處理的那族病（**數字比量具新**）是同一族，方向相反：那次是
「量具當時還不存在」，這次是「量具在、旗標也設了，但它**沒接上去**」。後者更陰險，因為
開關白名單、符號、建置產物、產物存在**四項檢查全過**，只有讀 stderr 才看得到那個 0。

`llama-context.cpp:3943-3945` 自己就寫過這條判準——
「A run that prints only n_leaf=0 rows is now proof the map never filled」。
本檔把那句註解變成一個布林，讓產線自己判，而不是等人事後在幾十萬行日誌裡發現。

WHAT IT CHECKS（每個探針三態）
------------------------------
* `BOUND`      至少一次正值 ⇒ 真的動過。
* `ZERO-ONLY`  印了，但每次都是 0 ⇒ **武裝了、沒接上**（本檔存在的理由）。
* `NO-MARKER`  一行都沒印 ⇒ 沒武裝，或武裝了但路徑根本沒走到。

「要驗哪些探針」**由臂自己的 env 推**（不是由人記得），但只推那些「開關在，輸出就**應該**在」的：
`CGC_MISS_MASK_DBG` ⇒ `mm_pub_n_leaf`／`mm_pub_wrote` 必須為正；`CGC_SPAC_DBG` ⇒ `spac` 必須為正。
一個受檢量具都沒武裝 ⇒ `N/A`。

**不由 env 推的**（要驗就得明文宣告 `binding:` 或 `--require-instrument`）：那些「輸出本身要原始碼補丁
 才存在」的探針。`rb_feed` 就是一個——它是 2026-09-29 才加的餵料線，所以
「一個 09-28 的臂武裝了 `CGC_SEG_BATCH`」推不出「它應該印 `rb_feed`」：
把「當時還不存在的量具」讀成「量具沒動」是本 repo 另一支檢查器的主敵
（`claim_instrument_check`：開關首進白名單晚於量測日 ⇒ 那個數字不可能是它產生的）；
這裡會犯同族的錯，只是方向相反，所以這條路徑上不推。

這條不對稱是刻意的，也是本檔的核心立場：
**武裝一個量具就是對它許下承諾**（它會產生我接下來要引用的數字），所以武裝了而值是 0 = 不通過；
沒有武裝 = 沒有承諾 = N/A。`ZERO-ONLY` **比 `NO-MARKER` 嚴重**：前者是「旗標設了、事沒發生」，
後者常常只是這條路本來就沒開。

UNVERIFIABLE 是第四態，只在掃產物時出現（見 `--scan`）：臂武裝了量具，但產物旁邊**沒有並存的
stderr log**。那是「沒有證據」而不是「證據說沒綁」——兩者要分開，否則一次政策改動就會
靜默改寫整份語料。`CGC_INSTRUMENT_STRICT=1` 可以把它拉成不通過（fail-closed 的選項留給人開）。

USAGE
-----
    python3 scripts/check/instrument_binding.py --log X.stderr.log --arm-env CGC_MISS_MASK_DBG=1
    python3 scripts/check/instrument_binding.py --scan 'Backup/**/*.json'
    python3 scripts/check/instrument_binding.py --self-test

rc: 0 = 沒有 UNBOUND；1 = 至少一個 UNBOUND；2 = 用法錯。
"""
import argparse
import glob as _glob
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── verdict（報告層）──────────────────────────────────────────────────────────
BOUND = "BOUND"
UNBOUND = "UNBOUND"
NA = "N/A"
UNVERIFIABLE = "UNVERIFIABLE"
BOUND_VERDICTS = (BOUND, NA)

# ── state（單一探針）─────────────────────────────────────────────────────────
POSITIVE = "positive"
ZERO_ONLY = "zero-only"
NO_MARKER = "no-marker"

# 探針：每個都是 stderr 上的一行計數器，值抓在第 1 個 group。
# 這裡只放**有「做了事」語意的計數器**：`misses=0` 這種「可以是 0 的結果」不能當探針，
# 一個乾淨的步本來就可能 0 miss。所以選的是「必須遞增才能說這條路在跑」的量。
PROBES = {
    "mm_pub_n_leaf": dict(
        re=re.compile(r"CGC-MM-PUB n_leaf=(\d+)"),
        what="mask 回讀認出的圖葉節點數",
        means="這張圖有可發佈的葉（全部是 0 ⇒ 地圖從沒填起來，後面所有欄位無意義）",
        origin="llama-context.cpp:3948"),
    "mm_pub_wrote": dict(
        re=re.compile(r"CGC-MM-PUB .*?wrote=(\d+)"),
        what="mask 真的寫進圖的 placeholder 數",
        means="placeholder 真的寫進去了（n_leaf>0 而 wrote 全 0 ⇒ 每個葉都被 skip，看同一行的 skip_*）",
        origin="llama-context.cpp:3948"),
    "rb_feed": dict(
        re=re.compile(r"CGC-RB-FEED: feeds=(\d+)"),
        what="回讀迴圈餵給 SpAc／union 的次數",
        means="不經 hook 的餵料在跑（單次提交臂的唯一餵料來源）",
        origin="llama-context.cpp:4058"),
    "missmask_step": dict(
        re=re.compile(r"CGC-MISSMASK-STEP: step=(\d+)"),
        what="走過 mask 判定的步數",
        means="逐步 miss 統計真的有在累加（值不是 miss 數，是步數）",
        origin="llama-context.cpp:4096"),
    "missmask_row": dict(
        re=re.compile(r"(?m)^MISSMASK il=\d+ step=\d+ nsel=\d+ misses=(\d+)"),
        what="逐層 miss 列數（`misses>0` 才印）",
        means="地圖真的填起來過（沒有 placeholder 寫進圖就不可能有 miss 列）",
        origin="llama-context.cpp:4090"),
    "spac": dict(
        re=re.compile(r"CGC-SPAC: feeds=(\d+)"),
        what="SpAc 效用 EMA 的刷新次數",
        means="池的重定中心有在跑",
        origin="llama-context.cpp:2188"),
    "prefetch": dict(
        re=re.compile(r"prefetch=(\d+)/\d+"),
        what="預取發出的次數（最終統計行）",
        means="預取器真的下過單（0 = 它接到空集合，或根本沒開）",
        origin="llama-expert-cache.cpp 最終統計"),
    # 【2026-09-30 S3-b】ρ 的**收貨端**。端點①的預註冊明文說它是「計數器、髒窗可引用」，
    # 而那正是「不靠牆鐘」的定義 ⇒ 它應該是一個受檢量具，而不是一段散文。
    # 值用 `layers=`（**累計**層×步事件數）：它只會增（每步 ≥ n_layer），所以是「有做事」語意的
    # 計數器——不像 `misses` 可以是 0。停滯/缺席的形狀：整行不存在（收貨端沒跑）或恆 0。
    "rho_capture": dict(
        re=re.compile(r"CGC-RHO-SUM: steps=\d+ layers=(\d+)"),
        what="ρ 影子路由累計捕捉的（層 × 步）事件數",
        means="影子分支在 decode 步上真的收貨（沒有這一行 ⇒ 收貨端沒跑；有列印但停在 0 ⇒ 沒量到）",
        origin="llama-context.cpp:6844"),
    # 【2026-09-30 · S2-c】單段提交臂的**投遞**見證。上面的 `rho_capture` 綁的是
    # `CGC-RHO-SUM`，那一行印在 per-layer hook 裡（:6844）—— 而 S2-c 處理的正是「hook 不跑」
    # 的那條路 ⇒ 少了這一支，事後就分不開「沒送達」與「送達但沒記帳」（S2-b 的單段臂就是前者）。
    # 只在補丁套用後的 build 上會出現（llama-context.cpp:4337，見
    # Backup/s2c_2026-09-30/s2c_rho_capture.patch）。
    "rho_s2c": dict(
        re=re.compile(r"CGC-RHO-S2C: steps=\d+ layers=(\d+)"),
        what="S2-c 在單段臂上投遞的（層 × 步）事件數（累計）",
        means="影子 capture 在單段提交臂上真的送達（沒有這一行 ⇒ 補丁沒生效或臂沒帶 CGC_SEG_BATCH；"
              "有列印而 layers/step < n_layer ⇒ 影子節點不在這張圖裡）",
        origin="llama-context.cpp:4337（S2-c 補丁）"),
}


def _on(env: dict, key: str) -> bool:
    """值才是判準：`KEY=0` 是誠實的關（同 `llama_bench_matrix.mtp_without_spec` 的教訓）。"""
    v = env.get(key)
    if v is None:
        return False
    return str(v).strip() not in ("", "0")


def infer_requirements(env) -> list:
    """臂武裝了哪些開關 ⇒ 它對哪些探針許下了承諾。順序固定（輸出可比對）。"""
    env = {str(k): v for k, v in (env or {}).items()}
    req = []
    if _on(env, "CGC_MISS_MASK_DBG"):
        req += ["mm_pub_n_leaf", "mm_pub_wrote"]
    if _on(env, "CGC_SPAC_DBG"):
        req.append("spac")
    # 刻意**不**推 `rb_feed`（見檔頭）：它是 2026-09-29 才進原始碼的餵料線，
    # 所以「舊 build 的臂武裝了 CGC_SEG_BATCH」不可能產生它——那是「當時不存在」，不是「沒動」。
    return req


# ── 跑前可判的那一半：**武裝了一個量具，卻沒開它的前提** ────────────────────────────
#
# 這一族在「不跑」的情況下就已經矛盾了，所以它可以（也应该）在花 GPU 時間之前就拒掉。
# 每一條的出處都是引擎自己：`CGC_MISS_MASK_DBG=1` 而 `CGC_MISS_MASK=1` 不在時，引擎會印
# `CGC-MISSMASK: CGC_MISS_MASK_DBG=1 without CGC_MISS_MASK=1 -- no mask`（llama-context.cpp:4001）；
# `CGC-SPAC:` 印在 `if (spac_on)` 裡面（llama-context.cpp:2181）；成本計數器印在回讀區塊裡（:4113）。
KNOB_PREREQS = {
    "CGC_MISS_MASK_DBG": (["CGC_MISS_MASK"], "回讀讀的是 mask 節點；沒有 mask 就沒有東西可讀"),
    # 【2026-09-30】`CGC_RHO_FILL=1` 需要**一個**機制旗標：`CGC_RHO_PROBE`（舊語意）**或**
    # `CGC_RHO`（2026-09-30 切分後的交付面；它自己就把 fill 打開，引擎那一行是
    # `cgc_rho_deliver || getenv("CGC_RHO_FILL")`）。兩個都不在 ⇒ fill 不發 IO，與
    # `CGC_SPAC_DBG` 同一型（武裝了計數器，卻沒開它的前提）。
    "CGC_RHO_FILL": (["CGC_RHO|CGC_RHO_PROBE"],
                     "fill 要有機制旗標（CGC_RHO 或 CGC_RHO_PROBE）才會發 IO；沒有就沒有節點可填"),
    "CGC_MISS_MASK_COST": (["CGC_MISS_MASK_DBG"], "成本計數器就印在回讀區塊裡面"),
    "CGC_SPAC_DBG": (["CGC_SPAC"], "`CGC-SPAC:` 印在 `if (spac_on)` 裡面"),
}

# 明文要求一個探針時，它需要哪些開關同時在。空 list ＝ **跑前判不了**（不是「不需要」）：
# `prefetch` 只有在「有人被選中而沒駐留」時才会有單，而那要跑才知道 ⇒ 交給跑後那一半。
PROBE_REQUIRES = {
    "mm_pub_n_leaf": ["CGC_MISS_MASK_DBG", "CGC_MISS_MASK"],
    "mm_pub_wrote": ["CGC_MISS_MASK_DBG", "CGC_MISS_MASK"],
    "missmask_step": ["CGC_MISS_MASK_DBG", "CGC_MISS_MASK"],
    "rb_feed": ["CGC_SEG_BATCH", "CGC_MISS_MASK_DBG", "CGC_MISS_MASK"],
    "spac": ["CGC_SPAC_DBG", "CGC_SPAC"],
    "prefetch": [],
    # ρ 的兩段都要 `CGC_RHO_PROBE`（影子分支有開才有節點、才有收貨）；`CGC_RHO_FILL` 的列印
    # 在 `if (cgc_rho_probe)` 之前就擋住了（llama-context.cpp:4800-4801）。
    "rho_capture": ["CGC_RHO_PROBE"],
    # S2-c 的投遞點只在 `cgc_rb_seg_batch` 上生效 ⇒ 兩個前提都要在，否則那一趟印不出東西。
    "rho_s2c": ["CGC_RHO_PROBE", "CGC_SEG_BATCH"],
}


def _need_off(env, need) -> list:
    """need 清單是 **AND**；元素內用 `|` 分隔者是 **OR**（至少一個在）。回沒開的那些。

    OR 是 2026-09-30 的 ρ 切分要的：`CGC_RHO_FILL` 的前提不再是只有 `CGC_RHO_PROBE` ——
    交付旗標 `CGC_RHO` 自己就會把 fill 打開。清單留在 AND 一層，是為了不動其他旋鈕的語意。
    """
    off = []
    for k in need:
        alts = [a.strip() for a in str(k).split("|") if a.strip()]
        if not any(_on(env, a) for a in alts):
            off.append(str(k).replace("|", " 或 "))
    return off


def static_precheck(env, require=None) -> tuple:
    """(ok, reasons, warnings) —— 跑前就能判的矛盾。`ok=False` 就別跑（拒跑比拒寫便宜）。

    只查「開關→前提」與「探針→開關」兩類別。查不到的絕不推測：
    * 沒武裝任何受檢量具 ⇒ `ok=True`（沒有承諾）
    * 要求的探針跑前判不了（例：`prefetch`）⇒ 進 `warnings`，交給跑後那一半判
    """
    env = {str(k): v for k, v in (env or {}).items()}
    reasons, warnings = [], []
    for knob, (need, why) in sorted(KNOB_PREREQS.items()):
        if not _on(env, knob):
            continue
        off = _need_off(env, need)
        if off:
            reasons.append("武裝了 %s，但前提 %s 沒開（%s）⇒ 這個量具不可能動"
                           % (knob, "、".join(off), why))
    for probe in (require or []):
        if probe not in PROBES:
            reasons.append("要求了一個不存在的探針 %r（拼錯不得放行）" % probe)
            continue
        need = PROBE_REQUIRES.get(probe, [])
        if not need:
            warnings.append("探針 %s 跑前判不了（它需要在跑的時候真的有事件發生）⇒ 交給跑後那一半"
                            % probe)
            continue
        off = [k for k in need if not _on(env, k)]
        if off:
            reasons.append("要求探針 %s，但 %s 沒開 ⇒ 它不可能出現" % (probe, "、".join(off)))
    return (not reasons), reasons, warnings


def runs_unbound(arms, *, missing_is_bad=True) -> tuple:
    """(ok, details) —— 跑後那一半：一份產物裡每一臂的量具綁定判定。

    只看產物**自己記的** `instrument`（`llama_bench_matrix` 寫的），不在此重推（那屬於寫入端）。
    缺那個欄位 ⇒ `missing_is_bad=True` 時算不過（拿不出證明就是拿不出），理由要寫成
    「產物沒有 instrument 欄位」而不是「量具沒綁上」—— 兩者的下一步完全不同。
    """
    details = []
    for arm in (arms if isinstance(arms, list) else [arms]):
        if not isinstance(arm, dict):
            continue
        tag = arm.get("tag") or arm.get("profile") or "?"
        if arm.get("dry_run"):
            continue
        inst = arm.get("instrument")
        if not isinstance(inst, dict) or not inst.get("verdict"):
            ok = not missing_is_bad
            details.append({"tag": tag, "ok": ok, "verdict": "NO-FIELD",
                            "why": "產物沒有 instrument 欄位（舊的 matrix？）⇒ 拿不出證明"})
            continue
        ok, why = is_bound(inst)
        # `UNVERIFIABLE`（舊產物沒有並存的 log）對「剛跑完的這一場」不適用：新產物一定有欄位。
        details.append({"tag": tag, "ok": ok, "verdict": inst.get("verdict"), "why": why})
    return all(d["ok"] for d in details), details


def requirement_reason(env, probe: str) -> str:
    """這個探針為什麼被要求（引用時要一起寫的那一半）。"""
    env = {str(k): v for k, v in (env or {}).items()}
    why = {
        "mm_pub_n_leaf": "CGC_MISS_MASK_DBG=%s 武裝了 mask 回讀" % env.get("CGC_MISS_MASK_DBG"),
        "mm_pub_wrote": "CGC_MISS_MASK_DBG=%s 武裝了 mask 回讀" % env.get("CGC_MISS_MASK_DBG"),
        "rb_feed": ("明文宣告的探針：不經 hook 的餵料（CGC_SEG_BATCH=%s 時 hook 不跑，"
                    "它是唯一的餵料來源；此線需 2026-09-29 的補丁）" % env.get("CGC_SEG_BATCH")),
        "spac": "CGC_SPAC_DBG=%s 武裝了 SpAc 計數器" % env.get("CGC_SPAC_DBG"),
        "rho_capture": ("CGC_RHO_PROBE=%s 武裝了影子路由（每步印 `CGC-RHO-SUM`）；"
                        "L20-7 的端點①就是它，判詞由 scripts/check/rho_instrument_ab.py 持有"
                        % env.get("CGC_RHO_PROBE")),
        "rho_s2c": ("CGC_SEG_BATCH=1 讓 hook 不跑 ⇒ ρ 的投遞由 S2-c 補丁在讀回點接手"
                    "（每步印 `CGC-RHO-S2C`）；它判的是「單段臂上到底有沒有送達」，不是 M1"),
    }
    return why.get(probe, "由 --require 明文要求")


def observe(text: str) -> dict:
    """把一份 stderr 文字變成 {probe: 觀測}。純函式，方便 selftest 注入。"""
    out = {}
    for name, spec in PROBES.items():
        vals = [int(m.group(1)) for m in spec["re"].finditer(text or "")]
        if not vals:
            state, mx, last = NO_MARKER, None, None
        else:
            mx, last = max(vals), vals[-1]
            state = POSITIVE if mx > 0 else ZERO_ONLY
        out[name] = {"state": state, "n": len(vals), "max": mx, "last": last,
                     "what": spec["what"], "means": spec["means"], "origin": spec["origin"]}
    return out


def window_escape(probes: dict) -> str:
    """`CGC-MM-PUB` 的取樣視窗**只涵蓋前 8 個 compute**（`llama-context.cpp:3943-3945`：前 5 個是
    mask-less 的 prefill／warmup，第 6 個才是第一個 decode step）。所以「8 列全部 n_leaf=0」在
    某些 cell 上可以只是「那個視窗一個帶葉的步都沒照到」，不是地圖沒填。

    分辨法用**同一條路的另一個證據**：`MISSMASK il=` 列只在 `misses>0` 時印，而沒有 placeholder
    寫進圖就不可能有 miss 列 ⇒ 有列就證明地圖**真的填起來過**（2026-09-29 實測：fillahead 的那一輪
    第一列在 `step=6`，正好是視窗的第 6 個 compute）。

    回非空字串 ⇒ 視窗退路成立（該探針不判死），字串是給人讀的理由。
    """
    if probes.get("missmask_row", {}).get("state") == POSITIVE:
        return ("CGC-MM-PUB 的視窗只涵蓋前 8 個 compute（llama-context.cpp:3943-3945），"
                "但 `MISSMASK il=` 有 %d 列（第一列 step≈%s 之後）⇒ 地圖確實填起來過，"
                "判定以逐層列為準" % (probes["missmask_row"]["n"], "?"))
    return ""


def binding_report(text: str, env=None, require=None) -> dict:
    """這一輪的綁定報告。`env` = 臂的 env（用來推承諾），`require` = 額外明文要求的探針。"""
    probes = observe(text)
    req = list(dict.fromkeys(list(infer_requirements(env)) + list(require or [])))
    # 視窗退路只對 mask 回讀的兩個探針生效：它們的出處是一個**有上限的取樣視窗**。
    esc = window_escape(probes)
    escaped = []
    unmet = []

    def _detail(n):
        pr = probes.get(n)
        if pr is None:
            # 未知的探針名（例：`--require` 拼錯）⇒ 不通過。靜默放行就是下一場空白資料的來源。
            return "%s=unknown-probe（不在 PROBES 裡，拼錯不得放行）" % n
        return "%s=%s（%s）" % (n, pr["state"], requirement_reason(env, n))

    for n in req:
        if probes.get(n, {}).get("state") == POSITIVE:
            continue
        if esc and n in ("mm_pub_n_leaf", "mm_pub_wrote"):
            escaped.append(n)
            continue
        unmet.append(n)
    # 沒被要求、卻印了全 0 的探針：不判死，但**要看得見**（否則下一次還是要讀日誌才知道）。
    incidental_zero = [n for n, p in probes.items() if p["state"] == ZERO_ONLY and n not in req]

    if not req:
        verdict = NA
        why = "本臂未武裝任何受檢量具 ⇒ 沒有東西可綁（不是乾淨、也不是不乾淨）"
        if incidental_zero:
            why += "；但 %s 印了全 0（未被要求，只記錄）" % "、".join(sorted(incidental_zero))
    elif unmet:
        verdict = UNBOUND
        why = "武裝了 %s，但 %s" % ("、".join(req), "；".join(_detail(n) for n in unmet))
    else:
        verdict = BOUND
        moved = [n for n in req if probes.get(n, {}).get("state") == POSITIVE]
        why = "受檢量具都動過：%s" % "、".join("%s=%s" % (n, probes[n]["max"]) for n in moved)
    if escaped:
        why += "；%s 走視窗退路：%s" % ("、".join(escaped), esc)

    return {"verdict": verdict, "why": why, "required": req,
            "required_reason": {n: requirement_reason(env, n) for n in req},
            "probes": probes, "incidental_zero": sorted(incidental_zero),
            "escaped": escaped, "escape_why": esc if escaped else "",
            "armed": sorted(k for k in ("CGC_MISS_MASK_DBG", "CGC_SEG_BATCH", "CGC_SPAC_DBG")
                            if _on({str(k2): v for k2, v in (env or {}).items()}, k))}


def is_bound(report) -> tuple:
    """(bound, why) —— 「這一輪的量具綁上了嗎」的**唯一定義**。

    寫入端（`experiment_sync`）與檢查端（`mindmap_void_check`）都呼叫這一個，理由同
    `memory_pressure.is_clean()`：兩邊各寫一遍「什麼叫綁上」，漂移的那天就是這種病回來的時候。

    缺席不是綁上（fail-closed）。`N/A` 算通過：沒有武裝就沒有承諾（見檔頭的不對稱）。
    `UNVERIFIABLE` 不算通過 —— 它是「沒有證據」，要不要把它當 VOID 由呼叫端決定，
    但**不能**被當成通過。
    """
    if not isinstance(report, dict):
        return False, "沒有 instrument 判定（缺席不是綁上）"
    v = str(report.get("verdict") or "").strip()
    if v in BOUND_VERDICTS:
        return True, "instrument.verdict=%s（%s）" % (v, report.get("why") or "")
    return False, "instrument.verdict=%s（%s）" % (v or "空", report.get("why") or "")


def strict_mode() -> bool:
    """`CGC_INSTRUMENT_STRICT=1`：把 UNVERIFIABLE（沒有並存的 log）也當不通過。

    預設關。理由：那不是「證據說沒綁」，是「沒有證據」；一次政策改動不該靜默改寫既有語料。
    要 fail-closed 的人把這個開關打開，而那筆決定會留在環境裡，看得見。
    """
    v = os.environ.get("CGC_INSTRUMENT_STRICT")
    return v is not None and str(v).strip() not in ("", "0")


# ── 產物層：從一支產物檔推出 (env, 並存的 log) ────────────────────────────────
def artifact_env(d) -> dict:
    """產物的 env：`env`（profile 解出的）＋ `extra_env`（臂宣告的）合併。

    兩種欄位在舊產物上可能是 dict（新）或 list of "K=V"（更舊），兩種都要吃得下 ——
    讀不出 env 就讀不出「對誰許過承諾」，那就會退回 N/A 而漏掉整個機制。
    """
    out = {}
    for field in ("env", "extra_env"):
        v = (d or {}).get(field)
        if isinstance(v, dict):
            out.update({str(k): val for k, val in v.items()})
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str) and "=" in item:
                    k, _, val = item.partition("=")
                    out[k] = val
    return out


def sibling_log(artifact_path: str) -> str:
    """產物 JSON 旁邊的 stderr log。命名慣例：`X.json` ↔ `X.stderr.log`。

    （`llama_bench_matrix` 把 stderr 寫成 `<workdir>/llama_bench_<tag>_<shape>.stderr.log`；
    harness 收產物時會把它與 JSON 一起搬進同一個目錄並改成同名。所以這是慣例，不是猜。）
    """
    p = str(artifact_path)
    for suf in (".json",):
        if p.endswith(suf):
            return p[: -len(suf)] + ".stderr.log"
    return p + ".stderr.log"


# ── 產物 ↔ stderr log 的成對規則（**唯一住處**）───────────────────────────────
# 2026-09-29：stderr log 是唯一能證明「量具真的動了」的東西，而它從前只落在 `--workdir`
# （預設 `/tmp` ⇒ 遲早被清掉），產物本身卻被歸檔到 `Backup/` ⇒ 歸檔完成的那一刻就永遠驗不了
# （`UNVERIFIABLE`）。實例：`g4miss2_default.json`、`g4miss_delivery.json` —— 42.9%／41.8% 那兩個
# 數的出處，兩支產物旁邊什麼都沒有。所以成對不是「記得順手 cp 一下」，而是寫入端的責任。
#
# 寫入端在產物旁邊落地兩份（先 log、後產物：中斷只會留下孤兒 log，不會留下無 log 的產物）：
#   <dir>/<stem>.logs/<safe_tag>.stderr.log   每一臂一份（完整、唯一、可單獨引用）
#   <dir>/<stem>.stderr.log                   合併版（`X.json` ↔ `X.stderr.log`，sibling 慣例）
PAIR_HEADER_PREFIX = "# cgc-pair:"


def safe_tag(tag: str) -> str:
    """臂名 → 檔名安全版。與 `llama_bench_matrix` 同一個換法（兩邊不同就會漂移）。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(tag or "arm"))[:80]


def _stem(artifact_path: str) -> str:
    p = str(artifact_path)
    return p[: -len(".json")] if p.endswith(".json") else p


def pair_arm_log(artifact_path: str, tag: str) -> str:
    return os.path.join(_stem(artifact_path) + ".logs", safe_tag(tag) + ".stderr.log")


def pair_canonical(artifact_path: str) -> str:
    return _stem(artifact_path) + ".stderr.log"


def _pair_header(artifact_path: str, tag: str, shape=None) -> str:
    parts = [PAIR_HEADER_PREFIX, "artifact=" + os.path.basename(str(artifact_path)),
             "arm=" + str(tag)]
    if shape:
        parts.append("shape=" + str(shape))
    parts.append("written=" + time.strftime("%Y-%m-%dT%H:%M:%S"))
    return " ".join(parts) + "\n"


def write_pair_arm(artifact_path: str, tag: str, text: str, shape=None) -> str:
    """一臂一份 log：唯一檔名 ⇒ 不會被同目錄的其他臂／其他輪覆寫（矩陣 09-16 的那個坑）。"""
    p = pair_arm_log(artifact_path, tag)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", errors="replace") as f:
        f.write(_pair_header(artifact_path, tag, shape))
        f.write(text or "")
    return p


def write_pair_bundle(artifact_path: str, entries) -> str:
    """合併成 `<stem>.stderr.log`（＝產物的 sibling）。由**知道這一輪邊界**的人呼叫一次，
    而不是由每一臂各自 append —— append 會讓重跑混進舊輪的殘留。

    `entries` 是 `[(tag, shape, text), ...]`。產物寫出前呼叫（先 log、後產物）。
    """
    p = pair_canonical(artifact_path)
    os.makedirs(os.path.dirname(os.path.abspath(p)) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8", errors="replace") as f:
        f.write(_pair_header(artifact_path, "(%d arm(s))" % len(list(entries)), None))
        for tag, shape, text in entries:
            f.write("\n===== arm %s%s =====\n" % (tag, ("  [%s]" % shape) if shape else ""))
            f.write(text or "")
    return p


def pair_status(artifact_path: str) -> dict:
    """這一支產物有沒有成對的 log —— 檢查端唯一的判準（缺 ⇒ **不可引用**）。

    「不可引用」不是「數值錯了」：產物可能是真的，只是拿不出「量具真的動過」的證明。
    """
    if not artifact_path:
        return {"paired": False, "canonical": None, "arm_logs": [],
                "why": "沒有給產物路徑 ⇒ 不知道要去哪裡找成對的 log"}
    canon = pair_canonical(artifact_path)
    arm_dir = os.path.join(_stem(artifact_path) + ".logs")
    arms = sorted(_glob.glob(os.path.join(arm_dir, "*.stderr.log")))
    size = os.path.getsize(canon) if os.path.exists(canon) else None
    paired = bool(size) and size > 0
    if paired:
        why = "成對：%s（%d bytes）%s" % (os.path.basename(canon), size,
                                          "（另有 %d 份 per-arm log）" % len(arms) if arms else "")
        return {"paired": True, "canonical": canon, "bytes": size, "source": "sibling",
                "arm_logs": [os.path.basename(a) for a in arms], "why": why}
    # 證據可能就在同一個目錄裡，只是名字是**矩陣從前寫的**那個（`llama_bench_<tag>_<shape>.stderr.log`）。
    # 那個名字是由臂名與形狀**推出來的**，不是在猜：所以不再是「沒有證據」，而是「證據在、但那一手
    # 人工搬檔沒做」。仍然要說清楚它比 sibling 弱在哪（同一組 tag+shape 的別輪就覆寫過它）。
    wd = _workdir_name_matches(artifact_path)
    if wd:
        why = ("成對（矩陣命名，弱於 sibling）：%s ⇒ 同一組 tag+shape 的別輪會覆寫它，"
               "要當引用依據就 `--pair-backfill` 登記成 %s"
               % (", ".join(os.path.basename(w) for w in wd[:3]), os.path.basename(canon)))
        return {"paired": True, "canonical": wd[0], "bytes": os.path.getsize(wd[0]),
                "source": "workdir-name", "arm_logs": [os.path.basename(a) for a in arms],
                "why": why}
    hl = _harness_log_matches(artifact_path)
    if hl:
        why = ("成對（驅動紀錄）：%s ⇒ <code>&lt;stem&gt;.harness.log</code> 記著這一次啟動的"
               " --workdir（同一條命令列），歸檔後那一份就在產物旁邊的同名子目錄；"
               "要當引用依據就 `--pair-backfill` 登記成 %s"
               % (", ".join(os.path.basename(h) for h in hl[:3]), os.path.basename(canon)))
        return {"paired": True, "canonical": hl[0], "bytes": os.path.getsize(hl[0]),
                "source": "harness-log", "arm_logs": [os.path.basename(a) for a in arms],
                "why": why}
    why = ("缺成對的 stderr log（找不到 %s%s）⇒ 這一場的量具無法查證 ⇒ 不可引用"
           % (os.path.basename(canon),
              "，per-arm 目錄也沒有任何 log" if not arms else "，但 per-arm 目錄有 %d 份" % len(arms)))
    return {"paired": False, "canonical": canon, "bytes": size, "source": None,
            "arm_logs": [os.path.basename(a) for a in arms], "why": why}


def _workdir_name_matches(artifact_path: str) -> list:
    """同一目錄下、名字是矩陣從前那個寫法、且**由這支產物的臂名與形狀推得出來**的 stderr log。

    名字裡的形狀是**這次呼叫的參數**，不是量到的長度：`-p 2048 -n 128 --warm-skip 64` 的臂，
    它的 tg row 會是 `n_gen=64`（= gen − warm-skip，見產物的 `warm_skip_applied`）。所以形狀要從
    產物**反推回呼叫參數**：`p`＝最大的 `n_prompt`（交付 cell 是 0，沒有 pp row）、
    `n`＝最大的 `n_gen` ＋ `warm_skip`、`d`＝`n_depth`；`reps` 不落在 row 上，那一段用 `*`。
    反推不出來（沒有 rows）⇒ 回空清單：宁可說找不到，也不要把隔壁那一場的 log 當成這一場的。
    """
    is_run, data, _why = load_run_artifact(artifact_path)
    if not is_run:
        return []
    d = os.path.dirname(os.path.abspath(artifact_path))
    hits = []
    for arm in data:
        tag = safe_tag(arm.get("tag"))
        rows = [r for r in (arm.get("rows") or []) if isinstance(r, dict)]
        if not rows:
            continue
        ws = int(arm.get("warm_skip") or 0)
        p = max((r.get("n_prompt") or 0) for r in rows)
        n = max((r.get("n_gen") or 0) for r in rows) + ws
        dd = max((r.get("n_depth") or 0) for r in rows)
        hits += sorted(_glob.glob(os.path.join(
            d, "llama_bench_%s_p%s_n%s_d%s_r*.stderr.log" % (tag, p, n, dd))))
    return list(dict.fromkeys(hits))


def _harness_log_matches(artifact_path: str) -> list:
    """**驅動紀錄**指向的那一份 stderr log（`<stem>.harness.log` → `--workdir` → 歸檔後的位置）。

    為什麼這一條不是猜的：`harness bench` 每一次啟動都會把**同一條命令列**寫進
    `<stem>.harness.log`（`--workdir <dir> … --json <stem>.json`）。也就是說「哪一份 log 屬於這一份
    產物」是**驅動自己記下來的**，不是由檔名推的。

    而歸檔（`k3_pair_cert.sh` 之類）把 `/tmp/kb/logs/<arm_id>/launches/L01` 搬成
    `<artifact_dir>/launches/L01` ⇒ 用 `logs/<arm_id>/` **之後的尾段**對到產物旁邊的同名子目錄。
    尾段對不上、或那個目錄裡沒有 `*.stderr.log` ⇒ 回空清單（寧可說找不到，也不要把隔壁那一場的
    log 當成這一場的）。
    """
    hl = _stem(artifact_path) + ".harness.log"
    if not os.path.exists(hl):
        return []
    try:
        with io.open(hl, encoding="utf-8", errors="ignore") as fh:
            txt = fh.read(400000)
    except OSError:
        return []
    base = os.path.dirname(os.path.abspath(artifact_path))
    outs = []
    for m in re.finditer(r"--workdir\s+(\S+)", txt):
        parts = m.group(1).strip().split("/")
        if "logs" in parts:
            i = len(parts) - 1 - parts[::-1].index("logs")
            tail = parts[i + 2:]          # 跳過 `logs` 與 `<arm_id>`
        else:
            tail = parts[-1:]
        if not tail:
            continue
        outs += sorted(_glob.glob(os.path.join(base, *tail, "*.stderr.log")))
    return list(dict.fromkeys(outs))


def is_run_artifact(d) -> bool:
    """這支 JSON 是不是「跑出來的產物」（而不是文件／mindmap／登記表）。

    判準用結構，不用檔名：`llama_bench_matrix` 的產物是 list[arm]，arm 有 `tag` 且（有 `rows`
    或 `refused_preflight`／`dry_run`）。這樣「文件類產物豁免」是機械判定的，不是靠人記得豁免誰。
    """
    arms = d if isinstance(d, list) else (d.get("arms") if isinstance(d, dict) else None)
    if not isinstance(arms, list) or not arms:
        return False
    for a in arms:
        if not isinstance(a, dict) or "tag" not in a:
            return False
        if not any(k in a for k in ("rows", "refused_preflight", "dry_run", "state_gate")):
            return False
    return True


def load_run_artifact(path: str):
    """讀一支 JSON 並判它是不是跑的產物：(is_run, data|None, why)。讀不出來 ⇒ (False, None, why)。"""
    try:
        with open(path, "r", errors="replace") as f:
            d = json.load(f)
    except Exception as e:  # noqa: BLE001
        return False, None, "讀不出 JSON：%s" % e
    return is_run_artifact(d), d, ""


def pairs_audit(patterns, repo_root=None) -> list:
    """唯讀：列出「跑出來的產物」裡成對 log 缺席的那些（附「哪裡還找得到同一場的 log」）。

    它是「缺 log 自動不可引用」的偵察端：先看得見，才談得上修。
    """
    base = repo_root or ROOT
    rows, seen = [], set()
    for pat in patterns:
        full = pat if os.path.isabs(pat) else os.path.join(base, pat)
        for p in sorted(_glob.glob(full, recursive=True)):
            if not p.endswith(".json") or p in seen:
                continue
            seen.add(p)
            is_run, d, _why = load_run_artifact(p)
            if not is_run:
                continue
            st = pair_status(p)
            rows.append({"artifact": os.path.relpath(p, base), "paired": st["paired"],
                         "why": st["why"], "arms": [a.get("tag") for a in d],
                         "workdir_log_candidates": workdir_log_hits(p, d, base)})
    return rows


def workdir_log_hits(artifact_path: str, data, repo_root=None) -> list:
    """「同一場的 log 還在哪裡」——只回**真的存在**的候選，不猜路徑。

    矩陣從前把 log 寫在 `<workdir>/llama_bench_<safe_tag>_<shape>.stderr.log`；`/tmp` 版會消失，
    但有人把它們搬進 `Backup/` 就還在。回傳候選是為了讓人（或 `--pair-backfill --from`）能修，
    **本函式不搬檔**：搬檔是改語料，要人明說。
    """
    base = repo_root or ROOT
    hits = []

    def _rel(p):
        return os.path.relpath(p, base) if os.path.abspath(p).startswith(base) else p

    # ① 產物自己那個目錄裡的任何 log（人工搬過去的痕跡；`fillahead_2026-09-28/fed_default.stderr.log`
    #    就是這樣存在的）
    own_dir = os.path.dirname(os.path.abspath(artifact_path))
    if os.path.isdir(own_dir):
        hits += [_rel(os.path.join(own_dir, x)) for x in sorted(os.listdir(own_dir))
                 if x.endswith(".stderr.log")]
    # ② 用臂名去找矩陣從前寫在 workdir 的那個檔名（`/tmp` 版會被清掉，搬到 Backup 的還在）
    for arm in (data or []):
        if not isinstance(arm, dict):
            continue
        tag = safe_tag(arm.get("tag"))[:40]
        for pat in (os.path.join(base, "Backup", "**", "*%s*.stderr.log" % tag),
                    os.path.join("/tmp", "**", "*%s*.stderr.log" % tag)):
            hits += [_rel(h) for h in sorted(_glob.glob(pat, recursive=True))]
    # 這只是「去哪裡找得回來」的線索，不是同一場的證明 —— 證明靠人比对 mtime/tag/shape 後
    # 明說 `--pair-backfill --from`（那一手會在 log 開頭留下 `backfilled-from=` 一行）。
    return list(dict.fromkeys(hits))[:6]


def pair_backfill(artifact_path: str, log_path: str, note: str = "") -> dict:
    """把一份**已存在**的 log 登記成某產物的成對 log（不複製、不發明：可能是 symlink，也可能是複製）。

    這是修舊語料的唯一入口，且必須明說來源：新的 log 檔開頭會被寫上 `paired-from=` 一行，
    所以「這份 log 是這一場的嗎」永遠留有可查的痕跡，而不是變成一份說不清來源的新證據。
    """
    st = pair_status(artifact_path)
    if st["paired"]:
        return {"ok": False, "why": "已經成對（%s）⇒ 不覆寫" % os.path.basename(st["canonical"])}
    if not os.path.exists(log_path):
        return {"ok": False, "why": "來源 log 不存在：%s" % log_path}
    canon = pair_canonical(artifact_path)
    with open(log_path, "r", errors="replace") as f:
        src = f.read()
    with open(canon, "w", encoding="utf-8", errors="replace") as f:
        f.write("# cgc-pair: artifact=%s backfilled-from=%s written=%s %s\n"
                % (os.path.basename(artifact_path), log_path,
                   time.strftime("%Y-%m-%dT%H:%M:%S"), note))
        f.write(src)
    return {"ok": True, "canonical": canon, "bytes": os.path.getsize(canon)}


def report_for_artifact(d, artifact_path=None, repo_root=None, env=None,
                        require=None) -> dict:
    """一支產物檔 → 綁定報告。優先用產物**自己記的** `instrument`（新產物），
    否則由 env 推承諾、讀並存的 log 算（舊產物）。

    讀不到 log ⇒ `UNVERIFIABLE`（**不是** UNBOUND）：沒有證據 ≠ 證據說沒綁。
    """
    rec = (d or {}).get("instrument")
    if isinstance(rec, dict) and rec.get("verdict"):
        return rec
    env = dict(artifact_env(d)) if env is None else dict(env)
    req = infer_requirements(env)
    if require:
        req = list(dict.fromkeys(req + list(require)))
    base = repo_root or ROOT
    text, log_rel, err = None, None, None
    if req == []:
        # 沒有承諾就沒必要讀 log —— 3000 支產物的掃描裡，這條省掉絕大多數 IO。
        return {"verdict": NA, "why": "產物未武裝任何受檢量具（arm env 裡沒有受檢開關）",
                "required": [], "probes": {}, "incidental_zero": [], "armed": [],
                "source": "derived"}
    if artifact_path:
        log_rel = sibling_log(artifact_path)
        p = log_rel if os.path.isabs(log_rel) else os.path.join(base, log_rel)
        if not os.path.exists(p) and not os.path.isabs(log_rel):
            # 有人用相對於 cwd 的路徑呼叫（手動診斷）。不補這一手的話，一個真的存在的 log
            # 會被報成 UNVERIFIABLE —— 假陰性會把「量具沒動」這種事藏起來。
            alt = os.path.abspath(log_rel)
            if os.path.exists(alt):
                p = alt
        if os.path.exists(p):
            try:
                with open(p, "r", errors="replace") as f:
                    text = f.read()
            except Exception as e:  # noqa: BLE001
                err = "%s：%s" % (type(e).__name__, e)
        else:
            err = "找不到並存的 stderr log：%s" % os.path.relpath(p, base)
    else:
        err = "沒有給產物路徑 ⇒ 不知道要去哪裡找 stderr log"
    if text is None:
        return {"verdict": UNVERIFIABLE, "why": "武裝了 %s，但 %s"
                % ("、".join(req), err), "required": req, "probes": {},
                "incidental_zero": [], "armed": [],
                "log": log_rel, "source": "derived"}
    rep = binding_report(text, env=env, require=require)
    rep["log"] = log_rel
    rep["source"] = "derived"
    return rep


def scan(patterns, repo_root=None, limit=None) -> list:
    """掃產物：每支有武裝受檢量具的產物回一列（唯讀，不會改任何東西）。"""
    base = repo_root or ROOT
    rows = []
    seen = set()
    for pat in patterns:
        full = pat if os.path.isabs(pat) else os.path.join(base, pat)
        for p in sorted(_glob.glob(full, recursive=True)):
            if not p.endswith(".json") or p in seen:
                continue
            seen.add(p)
            try:
                with open(p, "r", errors="replace") as f:
                    d = json.load(f)
            except Exception:  # noqa: BLE001  不是產物就不是本工具的對象
                continue
            for arm in (d if isinstance(d, list) else [d]):
                if not isinstance(arm, dict):
                    continue
                rel = os.path.relpath(p, base)
                rep = report_for_artifact(arm, artifact_path=p, repo_root=base)
                if rep.get("verdict") == NA:
                    continue
                rows.append({"artifact": rel, "tag": arm.get("tag"),
                             "verdict": rep["verdict"], "why": rep.get("why"),
                             "required": rep.get("required")})
                if limit and len(rows) >= limit:
                    return rows
    return rows


# ───────────────────────────────── selftest ──────────────────────────────────

def self_test() -> int:
    import tempfile
    import shutil

    ok = True

    def expect(tag, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print("  [%s] %s: %r" % ("OK" if good else "FAIL", tag, got)
              + ("" if good else "   (want %r)" % (want,)))

    def expect_true(tag, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print("  [%s] %s%s" % ("OK" if cond else "FAIL", tag,
                               ("   %s" % detail) if detail else ""))

    print("instrument_binding --self-test")

    # 1) 真的綁上（預設 cell 的形狀）
    fed = ("CGC-MM-PUB n_leaf=39 wrote=0 skip_not_in_graph=39\n"
           "CGC-MM-PUB n_leaf=39 wrote=39\n"
           "CGC-RB-FEED: feeds=14000 il=3 n=312 (spac_update without the hook)\n"
           "CGC-MISSMASK-STEP: step=390 misses=22 layers=15\n"
           "CGC-SPAC: feeds=1560 queued=6 n_prefetch=2129 dropped=0\n"
           "llama_expert_cache: final stats: ... prefetch=2854/0\n")
    r = binding_report(fed, env={"CGC_MISS_MASK_DBG": "1", "CGC_SEG_BATCH": "1",
                                 "CGC_SPAC_DBG": "1"})
    expect("綁上的臂 → BOUND", r["verdict"], BOUND)
    # `rb_feed` 不由 env 推（它需要 2026-09-29 的補丁，見檔頭）；要驗得明文宣告。
    expect("要求由 env 推的三個探針", r["required"],
           ["mm_pub_n_leaf", "mm_pub_wrote", "spac"])
    expect_true("is_bound(BOUND)", is_bound(r)[0])
    r1b = binding_report(fed, env={"CGC_SEG_BATCH": "1", "CGC_MISS_MASK_DBG": "1"},
                         require=["rb_feed"])
    expect("... 明文宣告 rb_feed 時才驗它", r1b["verdict"], BOUND)

    # 2) 武裝了、值是 0（2026-09-29 交付 cell 的形狀）—— 這條是本檔存在的理由
    dead = ("CGC-MM-PUB n_leaf=0 wrote=0 as_leaf=0 skip_null=0\n"
            "CGC-MISSMASK-STEP: step=0 misses=0 layers=0\n"
            "CGC-RB-FEED: feeds=0 il=0 n=0\n")
    r2 = binding_report(dead, env={"CGC_MISS_MASK_DBG": "1", "CGC_SEG_BATCH": "1"})
    expect("印了全 0 → UNBOUND", r2["verdict"], UNBOUND)
    expect_true("is_bound(UNBOUND) 為假", not is_bound(r2)[0])
    expect_true("why 要把沒動過的探針點名",
                all(n in r2["why"] for n in ("mm_pub_n_leaf", "mm_pub_wrote")), r2["why"][:60])
    # 舊 build 的單次提交臂：武裝了 SEG_BATCH，但餵料線當時還不存在 ⇒ 不得因此判死（
    # 2026-09-28 的 g4miss 兩支臂就是這個形狀）
    r2d = binding_report("CGC-MM-PUB n_leaf=39 wrote=39\n",
                         env={"CGC_MISS_MASK_DBG": "1", "CGC_SEG_BATCH": "1"})
    expect("舊臂沒有 rb_feed 不得被判死（不由 env 推）", r2d["verdict"], BOUND)

    # 2b) 視窗退路：8 列全部 n_leaf=0，但逐層 miss 列存在 ⇒ 地圖確實填起來過（只是視窗沒照到）
    win = ("CGC-MM-PUB n_leaf=0 wrote=0\n" * 8
           + "MISSMASK il=1 step=6 nsel=8 misses=6\n"
             "MISSMASK il=2 step=6 nsel=8 misses=4\n")
    r2b = binding_report(win, env={"CGC_MISS_MASK_DBG": "1"})
    expect("全 0 但有逐層 miss 列 ⇒ BOUND（視窗退路）", r2b["verdict"], BOUND)
    expect_true("... 且退路要寫出理由", "視窗" in r2b["why"] and r2b["escaped"] == ["mm_pub_n_leaf",
                                                                        "mm_pub_wrote"])
    # 2c) 反向：同一個 cell、9 列全 0 且**沒有**逐層 miss 列 ⇒ 地圖真的沒填（ctl 的形狀）
    r2c = binding_report("CGC-MM-PUB n_leaf=0 wrote=0\n" * 9
                         + "CGC-MISSMASK-STEP: step=390 misses=0 layers=0\n",
                         env={"CGC_MISS_MASK_DBG": "1"})
    expect("全 0 且無 miss 列 ⇒ UNBOUND", r2c["verdict"], UNBOUND)
    expect_true("... 且不得誤用退路", r2c["escaped"] == [])

    # 3) 沒武裝 ⇒ N/A（沒有承諾），即使日誌裡有別的探針是 0
    r3 = binding_report("prefetch=0/0\n", env={"CGC_SEG_BATCH": "1"})
    expect("沒武裝受檢量具 → N/A", r3["verdict"], NA)
    expect_true("N/A 算通過（沒有承諾）", is_bound(r3)[0])
    expect_true("但 incidental 的 0 要被記錄下來", "prefetch" in r3["incidental_zero"])

    # 4) `KEY=0` 是誠實的關，不是武裝（fail-closed 只對真的開了的開關）
    r4 = binding_report("", env={"CGC_MISS_MASK_DBG": "0"})
    expect("CGC_MISS_MASK_DBG=0 → 不算武裝", r4["verdict"], NA)

    # 5) MISS_MASK_DBG 開了但 MISS_MASK 沒開 ⇒ 那條路自己說「沒有 mask」⇒ 不算綁上
    r5 = binding_report("CGC-MISSMASK: CGC_MISS_MASK_DBG=1 without CGC_MISS_MASK=1 -- no mask\n",
                        env={"CGC_MISS_MASK_DBG": "1"})
    expect("有 debug 沒有 mask ⇒ UNBOUND", r5["verdict"], UNBOUND)

    # 6) 明文要求（--require）：用來把「池的重定中心」這類沒有專屬開關的探針釘住
    r6 = binding_report(fed, env={}, require=["prefetch"])
    expect("--require 讓沒武裝的臂也要驗", r6["required"], ["prefetch"])
    expect("... 且值為正 ⇒ BOUND", r6["verdict"], BOUND)
    r6b = binding_report("prefetch=0/0\n", env={}, require=["prefetch"])
    expect("... 值為 0 ⇒ UNBOUND", r6b["verdict"], UNBOUND)

    # 6b) 跑前可判的那一半：武裝了卻沒開前提 ⇒ 拒跑（不花 GPU 時間）
    ok_, rs_, ws_ = static_precheck({"CGC_MISS_MASK_DBG": "1"})
    expect("跑前：MISS_MASK_DBG 開了但 MISS_MASK 沒開 ⇒ 不通過", ok_, False)
    expect_true("... 而且理由指名前綢", "CGC_MISS_MASK" in rs_[0] and "不可能動" in rs_[0])
    ok_, rs_, ws_ = static_precheck({"CGC_MISS_MASK": "1", "CGC_MISS_MASK_DBG": "1",
                                     "CGC_MISS_MASK_COST": "1", "CGC_SEG_BATCH": "1",
                                     "CGC_SPAC": "1", "CGC_SPAC_DBG": "1"},
                                    require=["mm_pub_n_leaf", "rb_feed", "spac"])
    expect("跑前：prod-new 段子的那套都齊 ⇒ 通過", (ok_, rs_, ws_), (True, [], []))
    ok_, rs_, ws_ = static_precheck({"CGC_MISS_MASK": "1", "CGC_MISS_MASK_DBG": "1"},
                                    require=["rb_feed"])
    expect("跑前：要求 rb_feed 但 SEG_BATCH 沒開 ⇒ 不通過", ok_, False)
    ok_, rs_, ws_ = static_precheck({"CGC_MISS_MASK": "1", "CGC_MISS_MASK_DBG": "1"},
                                    require=["prefetch"])
    expect("跑前：prefetch 跑前判不了 ⇒ 通過但要警告（不是静默放行）", (ok_, bool(ws_)), (True, True))
    # 6c) OR 前提（2026-09-30 ρ 切分）：`CGC_RHO_FILL` 可以由交付旗標或探針任一個帶著。
    ok_, rs_, ws_ = static_precheck({"CGC_RHO_FILL": "1"})
    expect("跑前：RHO_FILL 開了但沒有機制旗標 ⇒ 拒跑", (ok_, "CGC_RHO" in " ".join(rs_)), (False, True))
    expect("跑前：RHO_FILL ＋ 交付旗標 ⇒ 通過（切分後的合法臂）",
           static_precheck({"CGC_RHO_FILL": "1", "CGC_RHO": "1"})[0], True)
    expect("跑前：RHO_FILL ＋ 探針 ⇒ 照舊通過（舊語意不變）",
           static_precheck({"CGC_RHO_FILL": "1", "CGC_RHO_PROBE": "1"})[0], True)
    expect("跑前：沒武裝任何受檢量具 ⇒ 通過（沒有承諾）", static_precheck({})[0], True)
    expect("跑前：要求不存在的探針 ⇒ 不通過（拼錯不得放行）",
           static_precheck({}, require=["nope_probe"])[0], False)

    # 6c) 跑後：整份產物的每一臂都要有綁定證明
    ok_, det = runs_unbound([{"tag": "a", "instrument": {"verdict": BOUND}},
                             {"tag": "b", "instrument": {"verdict": NA}}])
    expect("跑後：BOUND 與 N/A 都算通過", ok_, True)
    ok_, det = runs_unbound([{"tag": "a", "instrument": {"verdict": BOUND}},
                             {"tag": "b", "instrument": {"verdict": UNBOUND, "why": "全 0"}}])
    expect("跑後：有一臂 UNBOUND ⇒ 整份不通過（不是平均值）", ok_, False)
    expect_true("... 且指出是哪一臂", det[1]["tag"] == "b" and not det[1]["ok"])
    ok_, det = runs_unbound([{"tag": "a"}])
    expect("跑後：缺 instrument 欄位 ⇒ 不通過，且理由不是「量具沒綁上」",
           (ok_, det[0]["verdict"]), (False, "NO-FIELD"))
    ok_, det = runs_unbound([{"tag": "a", "dry_run": True}])
    expect("跑後：dry-run 臂不算（它沒有量到東西）", len(det), 0)

    # 7) observation 的三態
    o = observe("CGC-MM-PUB n_leaf=0 wrote=0\nCGC-MM-PUB n_leaf=39 wrote=39\n")
    expect("n_leaf 看 max 不看最後一筆", o["mm_pub_n_leaf"]["state"], POSITIVE)
    expect("wrote 也一樣", o["mm_pub_wrote"]["max"], 39)
    expect("沒印過的探針 = no-marker", o["spac"]["state"], NO_MARKER)

    # 8) 產物層：優先用產物自己記的判定；沒有才由 env+log 推
    d = tempfile.mkdtemp(prefix="ib_")
    try:
        with open(os.path.join(d, "a.json"), "w") as f:
            json.dump({"tag": "t", "extra_env": {"CGC_MISS_MASK_DBG": "1"}}, f)
        with open(os.path.join(d, "a.stderr.log"), "w") as f:
            f.write(dead)
        rep = report_for_artifact(json.load(open(os.path.join(d, "a.json"))),
                                 artifact_path=os.path.join(d, "a.json"), repo_root=d)
        expect("舊產物：由 env+並存的 log 推出 UNBOUND", rep["verdict"], UNBOUND)
        # 並存的 log 不見了 ⇒ UNVERIFIABLE（沒有證據 ≠ 證據說沒綁）
        os.remove(os.path.join(d, "a.stderr.log"))
        rep2 = report_for_artifact(json.load(open(os.path.join(d, "a.json"))),
                                   artifact_path=os.path.join(d, "a.json"), repo_root=d)
        expect("log 不在 ⇒ UNVERIFIABLE（不是 UNBOUND）", rep2["verdict"], UNVERIFIABLE)
        expect_true("... 且 is_bound 不把它當通過", not is_bound(rep2)[0])
        # 新產物自己記的判定優先
        rec = {"instrument": {"verdict": BOUND, "why": "產物自己記的"}}
        expect("產物自記的判定優先", report_for_artifact(rec)["verdict"], BOUND)
        # env 是 list 形式的舊產物也要吃得下
        old = {"extra_env": ["CGC_MISS_MASK_DBG=1"]}
        expect_true("env 為 list 形式也要讀得出來",
                    infer_requirements(artifact_env(old)) == ["mm_pub_n_leaf", "mm_pub_wrote"])
        # 掃描：只有武裝過受檢量具的列會被列出（唯讀）
        with open(os.path.join(d, "b.json"), "w") as f:
            json.dump({"tag": "plain", "extra_env": {"CGC_SEG_BATCH": "1"}}, f)
        rows = scan([os.path.join(d, "*.json")], repo_root=d)
        expect("scan 只列武裝過的（b.json 被跳過）", [r["artifact"].split("/")[-1] for r in rows],
               ["a.json"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # 9) 成對規則（產物 ↔ stderr log）：log 是唯一能證明「量具真的動了」的東西，
    #    而它從前只落在 workdir（/tmp）⇒ 歸檔完成的那一刻就永遠驗不了。
    pd = tempfile.mkdtemp(prefix="ib_pair_")
    try:
        art = os.path.join(pd, "m.json")
        with open(art, "w") as f:
            json.dump([{"tag": "prod-new:CGC_X=1", "rows": [{"n_prompt": 0, "n_gen": 64}]}], f)
        arm_log = write_pair_arm(art, "prod-new:CGC_X=1", "CGC-SPAC: feeds=7\n", shape="p0_n64")
        expect_true("per-arm log 落在 <stem>.logs/ 且帶來源標頭",
                    arm_log.endswith("m.logs/prod-new_CGC_X_1.stderr.log")
                    and open(arm_log).read().startswith(PAIR_HEADER_PREFIX))
        st = pair_status(art)
        expect("合併版還沒寫 ⇒ 尚未成對（但看得到 per-arm 有 log）",
               (st["paired"], len(st["arm_logs"])), (False, 1))
        write_pair_bundle(art, [("a", "p0_n64", "AAA\n"), ("b", None, "BBB\n")])
        st = pair_status(art)
        expect("合併版寫完 ⇒ 成對，且說得出 size", (st["paired"], st["bytes"] > 0), (True, True))
        body = open(pair_canonical(art)).read()
        expect_true("合併版含每一臂的區段＋來源標頭（重跑不會混進舊輪）",
                    body.startswith(PAIR_HEADER_PREFIX) and "===== arm a" in body
                    and "===== arm b" in body)
        expect("已成對 ⇒ backfill 拒絕覆寫",
               pair_backfill(art, arm_log)["ok"], False)
        # 驅動紀錄這一條（2026-09-30）：`k3_pair_cert` 家族的產物旁邊沒有 sibling，也沒有
        # `<stem>.logs/`，但 `<stem>.harness.log` 記著 `--workdir …/launches/L01`，而歸檔把那個
        # 子目錄原樣搬到產物旁邊 ⇒ 這一條要能把**真的那一份**找回來（而不是把整族判成不可引用）。
        # ⚠ 用自己的臨時目錄：`pd` 是後面 `pairs_audit` 那一組案例的 fixture 集合，往裡面丟檔案
        #   會讓「只列跑的產物」那條案例多出兩支（第一版就是這樣紅的）。
        hd = tempfile.mkdtemp(prefix="ib_hlog_")
        h_art = os.path.join(hd, "h.json")
        with open(h_art, "w") as f:
            json.dump([{"tag": "prod25:!CGC_PREFILL_STREAM=1", "rows": [{"n_prompt": 0, "n_gen": 64}]}], f)
        os.makedirs(os.path.join(hd, "launches", "L01"))
        with open(os.path.join(hd, "launches", "L01", "x.stderr.log"), "w") as f:
            f.write("CGC-SPAC: feeds=3\n")
        with open(os.path.join(hd, "h.harness.log"), "w") as f:
            f.write("$ harness.py bench --arm 'prod25:!CGC_PREFILL_STREAM=1' "
                    "--workdir /tmp/kb/logs/r4_b_k2/launches/L01 --json /tmp/kb/logs/r4_b_k2/h.json\n")
        st = pair_status(h_art)
        expect("驅動紀錄：--workdir 的尾段對上歸檔子目錄 ⇒ 成對", (st["paired"], st["source"]),
               (True, "harness-log"))
        # 尾段對不上（別的臂／別的場）⇒ 仍然不可引用：宁可說找不到
        with open(os.path.join(hd, "h.harness.log"), "w") as f:
            f.write("$ harness.py bench --workdir /tmp/kb/logs/rX_a_k9/launches/L77 --json x.json\n")
        expect("驅動紀錄：尾段對不上 ⇒ 仍然不成對", pair_status(h_art)["paired"], False)
        shutil.rmtree(hd, ignore_errors=True)
        # 未成對的產物 + 真的存在的來源 log ⇒ 可以修，但會在 log 開頭留下來源
        n_art = os.path.join(pd, "n.json")
        with open(n_art, "w") as f:
            json.dump([{"tag": "prod-new", "rows": [{"n_prompt": 0, "n_gen": 128}]}], f)
        expect("backfill：來源不存在 ⇒ 拒（不發明證據）",
               pair_backfill(n_art, os.path.join(pd, "nope.log"))["ok"], False)
        # 來源不能就叫 canonical 名（那就已經成對了，backfill 會正確地拒絕做多餘的事）
        src = os.path.join(pd, "n_from_workdir.stderr.log")
        with open(src, "w") as f:
            f.write("CGC-RB-FEED: feeds=12\n")
        expect("backfill：來源存在 ⇒ 修好", pair_backfill(n_art, src, "人工確認同一場")["ok"], True)
        expect_true("... 且 canonical 開頭記下 backfilled-from（可查）",
                    "backfilled-from=" in open(pair_canonical(n_art)).read())
        expect("backfill 後再驗 ⇒ 成對", pair_status(n_art)["paired"], True)
        # 「跑出來的產物」用結構判，不用檔名（文件類產物不能因為沒 log 被誤殺）
        expect_true("is_run_artifact：matrix 產物（list[arm] 有 tag+rows）⇒ True",
                    is_run_artifact(json.load(open(art))))
        expect("is_run_artifact：文件／mindmap ⇒ False",
               (is_run_artifact({"nodes": [{"id": "x"}]}), is_run_artifact([{"id": "x"}])),
               (False, False))
        # 稽核：缺的那一支要被點名，修好之後就不再點
        un = os.path.join(pd, "u.json")
        with open(un, "w") as f:
            json.dump([{"tag": "prod-new", "rows": []}], f)
        rows = pairs_audit([os.path.join(pd, "*.json")], repo_root=pd)
        expect("pairs_audit：只列跑的產物，且點名未成對的那支",
               (len(rows), sorted(r["artifact"] for r in rows if not r["paired"])),
               (3, ["u.json"]))
        os.remove(un)
        expect("修完 ⇒ 稽核不再點名",
               [r["artifact"] for r in pairs_audit([os.path.join(pd, "*.json")], repo_root=pd)
                if not r["paired"]], [])
    finally:
        shutil.rmtree(pd, ignore_errors=True)

    print("  -> %s" % ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


# ─────────────────────────────────── CLI ────────────────────────────────────

def _parse_env(s: str) -> dict:
    """`K=V;K2=V2` 或 `K=V,K2=V2`（harness 的 arm 語法用 `;`，人也常寫 `,`）。"""
    out = {}
    if not s:
        return out
    for part in re.split(r"[;,]", s):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def fmt_human(rep: dict) -> str:
    mark = {"BOUND": "✅", "N/A": "·", "UNVERIFIABLE": "⚠"}.get(rep["verdict"], "⛔")
    out = ["%s %s  %s" % (mark, rep["verdict"], rep.get("why") or "")]
    for name in rep.get("required") or []:
        p = (rep.get("probes") or {}).get(name) or {}
        out.append("      · %-14s %-10s max=%s n=%s   ← %s（%s）"
                   % (name, p.get("state"), p.get("max"), p.get("n"),
                      rep["required_reason"].get(name, ""), p.get("origin")))
    for name in rep.get("incidental_zero") or []:
        out.append("      · %-14s 印了全 0（未被要求，只記錄）" % name)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", help="要判定的 stderr log")
    ap.add_argument("--arm-env", default="", help="臂的 env：`K=V;K2=V2`（用來推「對誰許過承諾」）")
    ap.add_argument("--require", default="",
                    help="額外明文要求的探針（逗號分隔）：%s" % "、".join(sorted(PROBES)))
    ap.add_argument("--scan", action="append", default=None, metavar="GLOB",
                    help="掃產物 JSON（可多次）；只列武裝過受檢量具的，唯讀")
    ap.add_argument("--pairs", action="append", default=None, metavar="GLOB",
                    help="唯讀：哪些「跑出來的產物」缺成對的 stderr log（缺 ⇒ 那一場不可引用）。"
                         "rc=1 表示有缺（可當閘門）")
    ap.add_argument("--pair-backfill", metavar="ARTIFACT.json",
                    help="把一份**已存在**的 log 登記成這支產物的成對 log（修舊語料的唯一入口；"
                         "需 --from，且不會覆寫已存在的成對 log）")
    ap.add_argument("--from", dest="pair_from", metavar="SOURCE.stderr.log",
                    help="--pair-backfill 的來源 log（明說來源；不會被發明出來）")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    require = [x.strip() for x in a.require.split(",") if x.strip()]
    for r in require:
        if r not in PROBES:
            print("!! 未知探針 %r（已知：%s）" % (r, "、".join(sorted(PROBES))), file=sys.stderr)
            return 2

    if a.scan:
        rows = scan(a.scan)
        env_knobs = [k for k in ("CGC_MISS_MASK_DBG", "CGC_SEG_BATCH", "CGC_SPAC_DBG")]
        print("instrument_binding --scan（受檢開關：%s；strict=%s）"
              % ("、".join(env_knobs), strict_mode()))
        if not rows:
            print("  沒有任何產物武裝過受檢量具 ⇒ 沒有東西可綁（N/A，不是通過）")
        for row in rows:
            mark = {"BOUND": "✅", "N/A": "·", "UNVERIFIABLE": "⚠"}.get(row["verdict"], "⛔")
            print("  %s %-12s %-46s %s" % (mark, row["verdict"], row["artifact"],
                                           (row["why"] or "")[:80]))
        bad = [r for r in rows if r["verdict"] == UNBOUND]
        unver = [r for r in rows if r["verdict"] == UNVERIFIABLE]
        print("  小計：%d 列（BOUND=%d、UNBOUND=%d、UNVERIFIABLE=%d）"
              % (len(rows), sum(1 for r in rows if r["verdict"] == BOUND), len(bad), len(unver)))
        if unver and not strict_mode():
            print("  ⚠ UNVERIFIABLE = 沒有並存的 stderr log（沒有證據，不是證據說沒綁）。"
                  "要讓它也擋，設 CGC_INSTRUMENT_STRICT=1。")
        if a.json_out:
            with open(a.json_out, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            print("  json -> %s" % a.json_out)
        return 1 if bad else 0

    if a.pairs:
        rows = pairs_audit(a.pairs)
        missing = [r for r in rows if not r["paired"]]
        print("instrument_binding --pairs（唯讀：每場量測的產物與 stderr log 成對保存）")
        if not rows:
            print("  沒有「跑出來的產物」命中這些 glob ⇒ 沒有東西可查（不是通過）")
        for r in rows:
            mark = "✅" if r["paired"] else "⛔"
            print("  %s %-52s %s" % (mark, r["artifact"], r["why"]))
            for h in r.get("workdir_log_candidates") or []:
                print("        · 可能是同一場的 log（需人工比對）：%s" % h)
        print("  小計：%d 支跑的產物，%d 支缺成對 log" % (len(rows), len(missing)))
        if missing:
            print("  ⛔ 缺 log ⇒ 那一場的計數器與結論**不可引用**（不是「數值錯了」，是拿不出證明）。")
            print("     修舊語料：python3 scripts/check/instrument_binding.py "
                  "--pair-backfill <artifact.json> --from <那份 log>")
        if a.json_out:
            with open(a.json_out, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            print("  json -> %s" % a.json_out)
        return 1 if missing else 0

    if a.pair_backfill:
        if not a.pair_from:
            print("!! --pair-backfill 需要 --from <來源 log>（來源必須明說，不得靠猜）", file=sys.stderr)
            return 2
        res = pair_backfill(a.pair_backfill, a.pair_from)
        print(("✅ " if res["ok"] else "⛔ ") + (res.get("why") or
              "成對 log 已寫入 %s（%d bytes，開頭記下來源）" % (res["canonical"], res["bytes"])))
        return 0 if res["ok"] else 1

    if not a.log:
        print("!! 需要 --log、--scan、--pairs、--pair-backfill（或 --self-test）", file=sys.stderr)
        return 2
    if not os.path.exists(a.log):
        print("!! 找不到 log %s" % a.log, file=sys.stderr)
        return 2
    with open(a.log, "r", errors="replace") as f:
        text = f.read()
    env = _parse_env(a.arm_env)
    rep = binding_report(text, env=env, require=require)
    print("instrument_binding  %s" % a.log)
    print(fmt_human(rep))
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("json -> %s" % a.json_out)
    return 1 if rep["verdict"] == UNBOUND else 0


if __name__ == "__main__":
    sys.exit(main())
