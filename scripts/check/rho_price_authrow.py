#!/usr/bin/env python3
"""ρ 價格的**權威 row** 版：阻塞判詞（BLOCKED／RUNNABLE）＋ 現成命令 ＋ 預註冊價格判詞。

operator 2026-09-30：「把 ρ 的價格場次改到權威 row 上跑（harness bench、每臂同一格、乾淨窗、
臂上無探針），讓 12+ 這種數字有一天真的能入表。」

**`--check` 讀原始碼證明這一條現在跑不了**：ρ 的執行者全部掛在 `CGC_RHO_PROBE` 底下 ——
  ① `src/llama.cpp/src/models/qwen35moe.cpp:218-220`：影子路由節點（`cgc_rho_logits-<il>`）
     只在 `getenv("CGC_RHO_PROBE")` 為真時才建 ⇒ 沒開 probe 就**沒有影子節點**；
  ② `src/llama.cpp/src/llama-context.cpp:4800-4813`：`expert_cache_eval_cb` 裡那一段
     （`cgc_rho_capture(t)` ＋ `ctx->cgc_rho_prefetch(il)`）的閘就是 `cgc_rho_probe`
     ⇒ 沒開 probe 就**沒有 capture、也沒有 prefetch 的呼叫**（唯一「做正事」的那一行在裡面）；
  ③ `src/llama.cpp/src/llama-context.h:288-289`：檔頭自己寫「開關 `CGC_RHO_FILL`；
     需要 `CGC_RHO_PROBE=1`（圖裡要有影子節點）」；
  ④ `scripts/run_server.sh:1780-1791`：兩個旋鈕的語意（PROBE 建影子路由、FILL 把預測變成
     真的非阻塞 prefetch）。
⇒ 「臂上無探針」＝**機制不在跑**：那條設計量出來的 Δ 會是**假的 0**（安靜的 null），
   比量不到更糟。所以在旗標切開之前，這個檔案不發車，只輸出判詞與命令。

四件事，各有單一定義：
  * `--check`：阻塞寫成機器判詞（`BLOCKED`／`RUNNABLE`／`SPLIT_LEAKY`）＋座標（原始碼行）
    ＋交付路徑列印的量具閘門（交付臂的 stderr 必須零個 `CGC-RHO-*`）；
  * `--plan`：兩臂 × 兩趟反序的 exact `harness bench` 命令（cell 旗標向 `cell_contract` 要）；
  * `--judge`：預註冊價格判詞 —— 兩臂**各自**要過 `quote_gate`（R1–R8）、效應必須大於兩臂
    自身散布、兩趟同向才算 `PRICE`（引用面只有一份判準：`quote_gate`）；
  * `--checklist`：套用→建置→oracle（M1 9/9）→四條權威 row 命令的執行清單（補丁 md5 釘住）。

那個清單是給人看的；要**照順序真的跑完**用 `scripts/check/rho_window.py`：它把 S1 套用→S2 建置→
S3 oracle 錨→S4 oracle 交付→S5 四條命令→S6 判詞串成一支，預設只印計畫（`--go` 才動手）、
每一步跑前先掃產物（在就 skip）、跑後當場驗 receipt，失敗就停並印「下一個該看的東西」。
判詞與命令的**單一定義**仍在這個檔案：窗口腳本不重寫判準，只負責餵它四份產物。

全文與預註冊：docs/RHO_PRICE_AUTHROW_2026-09-30.md；立項卡：
scripts/check/charters/e-rho-delivery-flag-2026-09-30.yaml。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# 阻塞座標：(標籤, 相對路徑, 正則)。前三條是「機制在 probe 底下」的三個站點，
# 第四條是 run_server.sh 自己寫下的語意（它同時是「這個 flag 是機制前提」的紀錄）。
BLOCK_PROBES = (
    ("影子路由節點的建立閘", "src/llama.cpp/src/models/qwen35moe.cpp",
     re.compile(r'if\s*\(\s*cgc_rho_probe.*ffn_gate_inp')),
    ("eval hook 裡的 capture＋prefetch 呼叫閘", "src/llama.cpp/src/llama-context.cpp",
     re.compile(r'if\s*\(\s*cgc_rho_probe\s*&&\s*!ask.*cgc_rho_logits')),
    ("capture／prefetch 的呼叫點", "src/llama.cpp/src/llama-context.cpp",
     re.compile(r'ctx->cgc_rho_prefetch\(')),
    ("run_server.sh 的旋鈕語意", "scripts/run_server.sh",
     re.compile(r'CGC_RHO_PROBE=1\s+(builds the per-layer shadow router|the METER)')),
    # 切分後要多的第五條：交付旗標**進得了子行程**嗎（launcher 的 allowlist 陷阱）。
    # 今天掃不到（顯示 None）；它是 oracle 臂 `--env CGC_RHO=1` 真的送得進引擎的前提。
    ("交付旗標的轉送（launcher allowlist）", "scripts/run_server.sh",
     re.compile(r'SERVER_ENV\+=\(CGC_RHO="\$CGC_RHO"\)')),
)
# 交付旗標「已經切出來」的正向證據：任何以 CGC_RHO_ 開頭、但**不是** PROBE／PROBE_LATE
# 的 getenv 出現，就代表有人動了切分（此時 RUNNABLE，看板/人必須回來重判）。
DELIVERY_FLAG_RE = re.compile(r'getenv\("(CGC_RHO[A-Z_]*)"\)')
PROBE_FLAGS = ("CGC_RHO_PROBE", "CGC_RHO_PROBE_LATE")
# 已知**仍以 probe 為前提**的旋鈕：`CGC_RHO_FILL` 不是切分，它自己的閘就在 probe 區塊裡
# （llama-context.cpp:4800-4813）⇒ 它出現在原始碼裡不代表「可以量得到無探針的價格」。
KNOWN_DEPENDENT_FLAGS = {"CGC_RHO_FILL"}

# 交付補丁（不套用、可即刻 `git apply`）—— `--checklist`／selftest 用 md5 釘住，改過就紅。
PATCH_REL = "Backup/rho_split_2026-09-30/rho_delivery_split.patch"
PATCH_MD5 = "37188900d3ba71369ea80478d1a60273"
# 補丁是對這四份內容產生的（base md5）；別人若先改同一檔，清單會自己點名改用 `--3way`。
PATCH_BASES = {
    "src/llama.cpp/src/llama-context.cpp":    "bc65fbb4c8e1f5f2c8c1d97c282334bd",
    "src/llama.cpp/src/llama-context.h":      "87bd9bfb77c93ecee22e733effd30735",
    "src/llama.cpp/src/models/qwen35moe.cpp": "d8b1fa9082a213cc1743df30a09adabf",
    "scripts/run_server.sh":                  "17e3d16fc1a1b8b42f64b974a417e2f3",
}
# 交付路徑上的四個列印：只有 `CGC_RHO=1`（probe 不在）時會經過它們 ⇒ 全部要由
# `cgc_rho_meter()` 守著。收貨條件是交付臂的 stderr `grep -c 'CGC-RHO-'` 為零。
DELIVERY_PRINTS = ("CGC-RHO-CAP", "CGC-RHO-FILL-SKIP", "CGC-RHO-FILL", "CGC-RHO-PHASE-SKIP")
# 記帳區塊的三個列印（MISS／PROBE／SUM）：守衛是同一個大區塊的 `if (cgc_rho_probe && …)`，
# 離列印很遠 ⇒ 掃描視窗放寬。probe 不在時整塊不可達，所以它們不算「洩漏」。
METER_BLOCK_PRINTS = ("CGC-RHO-MISS", "CGC-RHO-PROBE", "CGC-RHO-SUM")
METER_GUARD_FAR = 300   # 交付路徑的守衛用「最近一個縮排更小的行」判（見 `_enclosing_guard`）


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _md5(path: str) -> str:
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def _enclosing_guard(lines: list, i: int) -> tuple:
    """`i` 這一行所在區塊的**開頭那一行**（往上找第一個縮排更小的行）。

    本 repo 的閘門寫法固定是單行 `if (…) {` ⇒ 「前 N 行有沒有出現那個字串」會被**上一個
    區塊**的閘門騙過去（selftest 的 fixture 否證就是抓這個：上一條列印的量具閘在 4 行內，
    第二條拆掉守衛仍然看起來像有守）。
    """
    ind = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        s = lines[j]
        if not s.strip():
            continue
        if len(s) - len(s.lstrip()) < ind:
            return j, s
    return None, ""


def print_guards(root: str = ROOT) -> dict:
    """每一個 `CGC-RHO-*` 列印有沒有被**對的**閘守著（交付路徑 vs 記帳區塊）。

    這是「機制與量具真的分家」的機器版：交付臂（`CGC_RHO=1`、probe 不在）會走
    `cgc_rho_capture`／`cgc_rho_prefetch`／eval hook 的相位分支 —— 那些列印若沒有
    `cgc_rho_meter()`，交付臂的 stderr 就會出現量具的帳 ⇒ 收貨條件（`grep -c` 為零）不成立，
    而「臂上沒有 never-quote 量具」就只是口號。
    """
    p = os.path.join(root, "src/llama.cpp/src/llama-context.cpp")
    if not os.path.exists(p):
        return dict(prints=[], leaks=[], checked=0)
    lines = _read(p).splitlines()
    prints, leaks = [], []
    for i, ln in enumerate(lines):
        m = re.search(r'fprintf\(stderr, "(CGC-RHO-[A-Z-]+)', ln)
        if not m:
            continue
        tag = m.group(1)
        guard, env = "cgc_rho_meter()", ""
        if tag in DELIVERY_PRINTS:
            j, guard_line = _enclosing_guard(lines, i)
            ok = guard in guard_line
            env = (guard_line or "").strip()[:120]
        elif tag in METER_BLOCK_PRINTS:
            guard = "if (cgc_rho_probe &&"
            near = range(max(0, i - METER_GUARD_FAR), i + 1)
            ok = any(guard in lines[j] for j in near)
        else:
            guard, ok = "?", False
        prints.append(dict(line=i + 1, tag=tag, guard=guard, guard_line=env, ok=bool(ok)))
        if not ok:
            leaks.append(dict(line=i + 1, tag=tag, want=guard, guard_line=env))
    return dict(prints=prints, leaks=leaks, checked=len(prints))


def build_state(root: str = ROOT) -> dict:
    """這份切分**編進 build 產物**了沒（不猜：直接找 `libllama*.dylib` 裡的字串）。

    為什麼判得出來：旗標名是 C 字串字面值 —— 原始碼有了、也真的重 build 了，產物裡才會出現
    **精確等於** `CGC_RHO` 的那一行。`CGC_RHO_PROBE` 不算（否則以前的 build 會被誤判為已建置）。
    """
    import subprocess
    pats = sorted(glob.glob(os.path.join(root, "src/llama.cpp/build/bin/libllama*.dylib")))
    out = dict(checked=0, built=False, files={})
    for p in pats:
        try:
            txt = subprocess.run(["strings", p], capture_output=True, text=True).stdout
        except OSError:
            continue
        lines = txt.splitlines()
        rho = sum(1 for l in lines if l == "CGC_RHO")
        probe = sum(1 for l in lines if l == "CGC_RHO_PROBE")
        out["files"][os.path.relpath(p, root)] = dict(rho=rho, probe=probe)
        out["checked"] += 1
        out["built"] = out["built"] or rho >= 1
    return out


def patch_state(root: str = ROOT) -> dict:
    """交付補丁在不在、md5 對不對、這一棵樹的 base 是不是它產生的那一份。"""
    p = os.path.join(root, PATCH_REL)
    st = dict(path=PATCH_REL, present=os.path.exists(p), md5=None, md5_ok=False,
              bases={}, bases_ok=True)
    if not st["present"]:
        return st
    st["md5"] = _md5(p)
    st["md5_ok"] = st["md5"] == PATCH_MD5
    for rel, want in PATCH_BASES.items():
        f = os.path.join(root, rel)
        got = _md5(f) if os.path.exists(f) else None
        st["bases"][rel] = dict(got=got, want=want, ok=got == want)
        st["bases_ok"] = st["bases_ok"] and got == want
    return st


def mechanism_block(root: str = ROOT) -> dict:
    """ρ 的執行者是不是還整套掛在 `CGC_RHO_PROBE` 底下？回判詞 dict。

    `blocked=True` ⇒ 現在的「臂上無探針」設計量不到東西（機制不在跑）。
    `flags` 列出原始碼裡真的存在的 `CGC_RHO*` 旗標名（切分後會多出交付旗標）。
    """
    coords = []
    for label, rel, rx in BLOCK_PROBES:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            coords.append(dict(label=label, file=rel, line=None, snippet="",
                               note="檔案不在（樹的形狀變了 ⇒ 重新確認）"))
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            m = rx.search(line)
            if m:
                coords.append(dict(label=label, file=rel, line=i, snippet=line.strip()))
                break
        else:
            coords.append(dict(label=label, file=rel, line=None, snippet="",
                               note="掃不到（這個座標不見了 ⇒ 可能已切分）"))
    flags = sorted(set(DELIVERY_FLAG_RE.findall(_read(os.path.join(
        root, "src/llama.cpp/src/llama-context.cpp"))))
        | set(DELIVERY_FLAG_RE.findall(_read(os.path.join(
            root, "src/llama.cpp/src/models/qwen35moe.cpp")))))
    delivery = [f for f in flags if f not in PROBE_FLAGS and f not in KNOWN_DEPENDENT_FLAGS]
    # 阻塞＝「影子節點的建立」與「capture/prefetch 的呼叫」兩個站點都還在 probe 底下。
    sites = {c["label"]: c for c in coords}
    shadow = sites.get("影子路由節點的建立閘", {})
    hook = sites.get("eval hook 裡的 capture＋prefetch 呼叫閘", {})
    blocked = bool(shadow.get("line") and hook.get("line"))
    guards = print_guards(root)
    if blocked:
        verdict = "BLOCKED"
        why = ("ρ 的影子節點與唯一執行者都掛在 CGC_RHO_PROBE 底下 ⇒ 「臂上無探針」＝機制不在跑；"
               "先把交付旗標切出來（見 charter e-rho-delivery-flag-2026-09-30）才量得到價格")
    elif guards["leaks"]:
        verdict = "SPLIT_LEAKY"
        why = ("原始碼已有交付旗標（%s），但交付臂的 stderr 仍會印帳：%s ⇒ 這是把旗標改名，不是把"
               "機制與量具分家；`grep -c 'CGC-RHO-'` 不會是零，價格不可引用"
               % ("、".join(delivery or ["?"]),
                  "、".join(":%d %s" % (l["line"], l["tag"]) for l in guards["leaks"])))
    else:
        verdict = "RUNNABLE"
        why = ("原始碼已出現非 probe 的 CGC_RHO* 旗標（%s），且交付路徑的 %d 個列印都由 "
               "cgc_rho_meter() 守著 ⇒ 機制與量具分家，這一格可以發車了"
               % ("、".join(delivery or ["?"]),
                  len([p for p in guards["prints"] if p["tag"] in DELIVERY_PRINTS])))
    return dict(blocked=blocked, coords=coords, flags=flags, delivery_flags=delivery,
                dependent_flags=sorted(f for f in flags if f in KNOWN_DEPENDENT_FLAGS),
                prints=guards["prints"], leaks=guards["leaks"],
                verdict=verdict, why=why)


# ── 現成命令（cell 的旗標向 cell_contract 要，不重寫）──────────────────────────────

def plan(root: str = ROOT, cell: str = "(default)", rho_flag: str = "CGC_RHO",
         charter: str = "scripts/check/charters/e-rho-delivery-flag-2026-09-30.yaml",
         out_dir: str = "Backup/rho_price_2026-09-30") -> list:
    """兩臂 × 兩趟反序的 harness bench 命令（A=基線、B=ρ 交付旗標）。"""
    if rho_flag in PROBE_FLAGS or "PROBE" in rho_flag:
        raise SystemExit("交付旗標不得是探針（%s）：探針是量具，不是機制開關" % rho_flag)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import cell_contract as CC
    contract = CC.load_contract()
    name, cell_block = CC.resolve_cell(contract, None if cell == "(default)" else cell)
    flags, warn = CC.bench_flags(cell_block)
    # ⚠ 預設格**不能**寫成 `--cell (default)`：`cell_contract.resolve_cell()` 的具名格是
    # `cells` 這張表（裡頭沒有 "(default)"）⇒ 具名格會 fail-closed 拒跑。預設格＝**不傳** `--cell`。
    base = ["python3", "scripts/check/harness.py", "bench", "--charter", charter]
    if name != "(default)":
        base += ["--cell", name]
    base += flags
    # 臂的 env 走 `harness bench --arm` 的 spec（`prod-new:CGC_RHO=1`）—— 這是唯一的解析路徑，
    # 不另外發明 `--arm-env`（它不存在；silently ignored 的旗標是這個 repo 的老陷阱）。
    arm_a, arm_b = "prod-new", "prod-new:%s=1" % rho_flag
    cmds = []
    for order, (first, second) in enumerate(((("A", [*base, "--arm", arm_a]),
                                              ("B", [*base, "--arm", arm_b])),
                                             (("B", [*base, "--arm", arm_b]),
                                              ("A", [*base, "--arm", arm_a]))), 1):
        for tag, cmd in (first, second):
            cmd = list(cmd)
            cmd += ["--json", os.path.join(out_dir, "order%d_%s.json" % (order, tag))]
            cmds.append(dict(order=order, arm=tag, arm_spec="prod-new" if tag == "A" else arm_b,
                             cmd=cmd, warn=warn))
    return cmds


# ── 預註冊的價格判詞（跑後）────────────────────────────────────────────────────

def _qg():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import quote_gate
    return quote_gate


def _rows_of(path: str) -> list:
    recs = _qg().scan([path])
    return recs


def _prod_env(path: str) -> dict:
    """產物裡第一支臂的 env（`env`／`extra_env`／`tag` 三個來源併起來看，判準在 quote_gate）。"""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    for prod in (doc if isinstance(doc, list) else [doc]):
        if isinstance(prod, dict):
            return _qg()._arm_env(prod)
    return {}


def _decode_row(recs: list):
    """權威 row 的 decode 行：`p0/n*/d*`（n_prompt=0）。回 (rec, None) 或 (None, 原因)。"""
    dec = [r for r in recs if str(r.get("shape", "")).startswith("p0/n")]
    if len(dec) != 1:
        return None, "decode 行不唯一（%d 條）" % len(dec)
    return dec[0], None


def judge(order_a: str, order_b: str, rho_flag: str = "CGC_RHO") -> dict:
    """預註冊判詞：兩趟反序的權威 row 價格。

    規則（跑前寫死，見 docs/RHO_PRICE_AUTHROW_2026-09-30.md）：
      ① 每一臂的 decode 行必須 `QUOTABLE`（R1–R8 全過）——任一不過 ⇒ `REFUSE`（不可引用就沒有價格）；
      ② 兩臂的臂上 env 差**只有**那顆交付旗標（其餘逐字相同）；
      ③ 每一趟的效應 |Δ| 必須**大於兩臂自身散布**（任一邊不過 ⇒ 該趟不可解）；
      ④ 兩趟都可解且**同向** ⇒ `PRICE`（附 Δ 中位）；兩趟都不可解 ⇒ `NO_EFFECT`；
         兩趟可解但反向 ⇒ `REFUSE`（順序效應）。
    """
    out = {"product": "rho price (authority row)", "rho_flag": rho_flag}
    per = {}
    for tag, path in (("A", order_a), ("B", order_b)):
        recs = _rows_of(path)
        if not recs:
            out.update(verdict="REFUSE", why="%s：產物裡沒有可判的 row（%s）" % (tag, path))
            return out
        rec, why = _decode_row(recs)
        if rec is None:
            out.update(verdict="REFUSE", why="%s：%s" % (tag, why))
            return out
        if rec["verdict"] != "QUOTABLE":
            out.update(verdict="REFUSE",
                       why="%s 的 decode 行不過引用閘門（%s）：%s"
                           % (tag, rec["verdict"], "；".join(rec.get("reasons") or [])))
            return out
        per[tag] = rec
    # ② 兩臂的臂上 env 差**只有**那顆交付旗標（其餘逐字相同）—— 否則量到的是別的差異。
    ea, eb = _prod_env(order_a), _prod_env(order_b)
    diff = {k for k in set(ea) | set(eb) if ea.get(k) != eb.get(k)}
    if diff != {rho_flag}:
        out.update(verdict="REFUSE", why="兩臂的臂上 env 差不是只有 %s：%s"
                   % (rho_flag, sorted(diff) or "（沒有差 ⇒ 機制根本沒開，這是安靜的 null）"))
        return out
    m_a, m_b = per["A"]["metrics"], per["B"]["metrics"]
    spread = max(float(m_a.get("all_spread") or 0.0), float(m_b.get("all_spread") or 0.0),
                 float(m_a.get("kept_spread") or 0.0), float(m_b.get("kept_spread") or 0.0))
    effect = (float(m_b["avg"]) - float(m_a["avg"])) / float(m_a["avg"]) * 100.0
    resolvable = abs(effect) > max(0.0, (spread - 1.0) * 100.0)
    out.update(a=dict(avg=m_a["avg"], spread=spread, file=order_a),
               b=dict(avg=m_b["avg"], file=order_b),
               effect_pct=round(effect, 2), spread_pct=round((spread - 1.0) * 100.0, 2),
               resolvable=resolvable)
    if resolvable:
        out.update(verdict="PRICE", why="效應 %+.2f%% > 兩臂散布 %.2f%% ⇒ 這一趟可解"
                   % (effect, (spread - 1.0) * 100.0))
    else:
        out.update(verdict="NO_EFFECT",
                   why="效應 %+.2f%% ≤ 兩臂散布 %.2f%% ⇒ 這一趟量不出價格（不是「免費」）"
                       % (effect, (spread - 1.0) * 100.0))
    return out


def both_orders(oa: dict, ob: dict) -> dict:
    """兩趟反序合起來的判詞（預註冊的 ④）。"""
    if oa.get("verdict") == "REFUSE" or ob.get("verdict") == "REFUSE":
        return dict(verdict="REFUSE", why="有一趟不可引用：%s / %s"
                    % (oa.get("why"), ob.get("why")), orders=[oa, ob])
    if not (oa.get("resolvable") and ob.get("resolvable")):
        return dict(verdict="NO_EFFECT", why="兩趟都不可解 ⇒ 這套設計量不出 ρ 的價格（不是便宜）",
                    orders=[oa, ob], median_pct=round((oa["effect_pct"] + ob["effect_pct"]) / 2.0, 2))
    if (oa["effect_pct"] > 0) != (ob["effect_pct"] > 0):
        return dict(verdict="REFUSE", why="兩趟可解但反向（%+.2f%% vs %+.2f%%）⇒ 順序效應，不可引用"
                    % (oa["effect_pct"], ob["effect_pct"]), orders=[oa, ob])
    return dict(verdict="PRICE", orders=[oa, ob],
                median_pct=round((oa["effect_pct"] + ob["effect_pct"]) / 2.0, 2),
                why="兩趟同向且都可解 ⇒ ρ 的價格成立")


# ── 執行清單（套用→建置→oracle→四條命令）────────────────────────────────────

def checklist(root: str = ROOT, cell: str = "(default)", rho_flag: str = "CGC_RHO") -> int:
    """印出套用與驗收的完整清單。**不跑任何一條**（只讀樹、印字），回傳 exit code。"""
    ps, st = patch_state(root), mechanism_block(root)
    print("ρ 交付旗標切分：套用與驗收清單（補丁不套用、可即刻 git apply）")
    print()
    print("0) 補丁")
    if not ps["present"]:
        print("   ✗ %s 不在 ⇒ 這張清單沒有東西可套" % PATCH_REL)
        return 1
    print("   %s" % PATCH_REL)
    print("   md5 %s %s" % (ps["md5"], "== 釘住的值" if ps["md5_ok"]
                            else "≠ 釘住的值（%s）⇒ 補丁被改過，先重新驗證" % PATCH_MD5))
    if ps["bases_ok"]:
        print("   四份 base 逐字相同 ⇒ `git apply` 可以直上（不必 --3way）")
    else:
        print("   ⚠ 這一棵樹的 base 已經不是補丁產生的那一份 ⇒ 用 `git apply --3way`：")
        for rel, b in ps["bases"].items():
            if not b["ok"]:
                print("      %s 現在 %s（補丁 base %s）" % (rel, b["got"], b["want"]))
    bs = build_state(root)
    print("   落地狀態（機器推的，不是敘述）")
    print("     套用：%s" % ("是（交付旗標已在樹上）" if not st["blocked"] else "否（機制閘仍是 probe-only）"))
    print("     建置：%s（libllama*.dylib %d 個裡，精確 `CGC_RHO` 字串命中 %d 個）"
          % ("是" if bs["built"] else "否", bs["checked"],
             sum(1 for f in bs["files"].values() if f["rho"])))
    print("     GPU：本清單不發車（套用＋建置後才跑 oracle 與四條命令）")
    print()
    print("1) 套用（在擁有 src/ 的那棵樹）")
    print("   git apply --3way %s" % PATCH_REL)
    print()
    print("2) 建置")
    print("   cmake --build src/llama.cpp/build --target llama-server llama-bench -j 8")
    print()
    print("3) oracle（切分不動數值；控制臂可重用同 build 的既有錨）")
    print("   python3 scripts/check/m123_oracle_gate.py --profile prefill250 --tag rho-split-ctl-<日期>")
    print("   python3 scripts/check/m123_oracle_gate.py --profile prefill250 --tag rho-split-deliver-<日期> \\")
    print("       --env %s=1      # 期望 M1 9/9（exit 0）" % rho_flag)
    print("   # 這一條送得進去，靠的就是補丁在 run_server.sh 加的轉送（allowlist 陷阱）")
    print()
    print("4) 收貨條件（分家是真的；這五條就是 rho_window.py 的 receipt）")
    print("   python3 scripts/check/rho_price_authrow.py --check            # 期望 RUNNABLE")
    print("   # ⚠ 臂上 env：交付臂 summary 的 extra_env 要有 %s=1、對照臂不能有" % rho_flag)
    print("   #   ⇒ Backup/m123_oracle_gate/summary_<tag>.json 的 'extra_env'／'pool_counters'")
    print("   # ⚠ 量具不外洩：看 banner 的 `[log] <path>` 指名的那份**伺服器** log 上")
    print("   #   grep -c 'CGC-RHO-' == 0；不要在 launch_<tag>.log（banner 自己）上 grep —— 那永遠是 0")
    print("   # ρ 的貢獻是對照臂／交付臂兩份伺服器 log 的 prefetch 差分（證據，不是價格）")
    print("   python3 scripts/check/instrument_binding.py --log <deliver arm stderr> \\")
    print("       --arm-env '%s=1' --require prefetch                        # 池的結算行當見證" % rho_flag)
    print()
    print("5) 四條權威 row 命令（同一格、兩臂、兩趟反序）")
    for cc in plan(root, cell=cell, rho_flag=rho_flag):
        print("   [order%d %s] %s" % (cc["order"], cc["arm"], " ".join(cc["cmd"])))
    print()
    print("6) 價格判詞（跑後）")
    print("   python3 scripts/check/rho_price_authrow.py --judge <order1.json> <order2.json>")
    print("   # 期望 PRICE（兩臂 decode 行皆 QUOTABLE、|Δ|>兩臂散布、兩趟同向）")
    print()
    print("—— 或一條命令把 S1→S6 做完（跑前先掃產物、失敗就停並印下一手）——")
    print("   python3 scripts/check/rho_window.py            # 先看計畫（預設不執行任何東西）")
    print("   python3 scripts/check/rho_window.py --go       # 真的跑（可中斷；重跑接續）")
    print()
    print("現況：%s -- %s" % (st["verdict"], st["why"]))
    return 0 if (ps["md5_ok"] and st["verdict"] == "RUNNABLE") else 2


# ── selftest ────────────────────────────────────────────────────────────────

def _fixture_split(td: str, metered: bool) -> None:
    """一棵最小的「切分落地」樹：機制閘＝`cgc_rho_deliver`、四個交付列印由量具閘守著。

    `metered=False` 拿掉 `CGC-RHO-FILL` 的量具閘 ⇒ 交付臂會把帳印出來 ⇒ 判 `SPLIT_LEAKY`。
    這是 `print_guards()` 的否證：沒有它，「量具不外洩」只驗得到正面。
    """
    os.makedirs(os.path.join(td, "src/llama.cpp/src/models"), exist_ok=True)
    os.makedirs(os.path.join(td, "scripts"), exist_ok=True)
    with open(os.path.join(td, "src/llama.cpp/src/models/qwen35moe.cpp"), "w", encoding="utf-8") as fh:
        fh.write('static const bool cgc_rho_deliver = getenv("CGC_RHO") != nullptr || getenv("CGC_RHO_PROBE") != nullptr;\n'
                 'if (cgc_rho_deliver && model.layers[il].ffn_gate_inp != nullptr && !cgc_rho_late) {\n')
    ctx = []
    for tag, guard in (("CGC-RHO-CAP", "cgc_rho_meter() && il == 0"),
                       ("CGC-RHO-FILL-SKIP", "cgc_rho_meter() && il == 0"),
                       ("CGC-RHO-FILL", "cgc_rho_meter() && il == 0"),
                       ("CGC-RHO-PHASE-SKIP", "cgc_rho_meter()")):
        if not metered and tag == "CGC-RHO-FILL":
            guard = "il == 0"
        ctx += ['    if (%s) {' % guard, '        fprintf(stderr, "%s: x\\n");' % tag, '    }']
    ctx += ['    if (cgc_rho_probe && cgc_probe_is_decode) {',
            '        fprintf(stderr, "CGC-RHO-MISS: x\\n");',
            '        fprintf(stderr, "CGC-RHO-PROBE: x\\n");',
            '        fprintf(stderr, "CGC-RHO-SUM: x\\n");',
            '    }',
            '    ctx->cgc_rho_prefetch(il);',
            'static const bool cgc_rho_deliver = getenv("CGC_RHO") != nullptr || cgc_rho_probe;',
            'if (cgc_rho_deliver && !ask && strncmp(t->name, "cgc_rho_logits", 14) == 0) {']
    with open(os.path.join(td, "src/llama.cpp/src/llama-context.cpp"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(ctx) + "\n")
    with open(os.path.join(td, "scripts/run_server.sh"), "w", encoding="utf-8") as fh:
        fh.write('#   CGC_RHO_PROBE=1   the METER: prints the CGC-RHO-* accounting\n'
                 'if [ -n "${CGC_RHO:-}" ]; then\n    SERVER_ENV+=(CGC_RHO="$CGC_RHO")\nfi\n')


def _mk_product(path: str, avg: float, samples: list, spread_hint: float = 0.0,
                attrib: str = "none", rho: str = "0") -> None:
    """一個「權威 row」形狀的產物（(default) cell：pp 行 ＋ tg 行、(0, gen−warm_skip)）。"""
    # 回報的 stddev 要與 samples 反算值相符（quote_gate 的 R0）：三點對稱時樣本標準差
    # 恰好是 (max−min)/2。
    sd = (max(samples) - min(samples)) / 2.0 if samples else 0.0
    prod = {
        "profile": "prod-new", "tag": "prod-new",
        "cell": {"prompt": 2048, "gen": 128, "depths": 512, "reps": 3, "warm_skip": 64,
                 "named_cell": "(default)"},
        "warm_skip_applied": True,
        "contract": {"ok": True, "mismatches": [], "cell": "(default)"},
        "attribution": {"verdict": attrib, "thermal_worst": "NOMINAL"},
        "extra_env": {("CGC_RHO" if rho != "0" else "CGC_RHO_OFF"): "1"} if rho != "0" else {},
        "rows": [
            {"n_prompt": 2048, "n_gen": 0, "n_depth": 512, "avg_ts": 300.0,
             "stddev_ts": 1.0, "samples_ts": [300.0, 301.0, 300.5]},
            {"n_prompt": 0, "n_gen": 64, "n_depth": 512, "avg_ts": avg,
             "stddev_ts": sd, "samples_ts": samples},
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(prod, fh, ensure_ascii=False)


def selftest() -> int:
    import tempfile
    ok = []

    def c(name, good, got=""):
        ok.append((name, bool(good), got))

    # ① --check 在真樹上必須是 BLOCKED／RUNNABLE 之一，且**與樹上的事實一致**。
    #   [2026-10-01 口徑統一線] 切分已套用到工作樹（`--check` 轉 RUNNABLE）⇒ 舊的
    #   「真樹一定 BLOCKED、一定還沒有交付旗標」不再成立。改判**內在一致**（半套仍然紅）：
    #   BLOCKED ⇔ 兩個 probe 站點都在；RUNNABLE ⇔ 站點消失、交付旗標與 launcher 轉送都在。
    st = mechanism_block(ROOT)
    _blocked = st["verdict"] == "BLOCKED"
    _sites = {cc["label"]: cc for cc in st["coords"]}
    _shadow = bool((_sites.get("影子路由節點的建立閘") or {}).get("line"))
    _hook = bool((_sites.get("eval hook 裡的 capture＋prefetch 呼叫閘") or {}).get("line"))
    _fw = [cc for cc in st["coords"] if "轉送" in cc["label"]]
    _fw_ok = bool(_fw) and all(cc.get("line") for cc in _fw)
    c("real tree 是 BLOCKED／RUNNABLE（切分前後兩種一致狀態）",
      st["verdict"] in ("BLOCKED", "RUNNABLE"), st["verdict"])
    c("BLOCKED ⇔ 兩個 probe 站點都在（切分未落地）",
      (_shadow and _hook) == _blocked,
      "shadow=%s hook=%s verdict=%s" % (_shadow, _hook, st["verdict"]))
    c("RUNNABLE ⇔ launcher 轉送落地（BLOCKED ⇒ 還沒）",
      _fw_ok == (st["verdict"] == "RUNNABLE"),
      str([(cc["label"], cc["line"]) for cc in _fw]))
    c("交付旗標與狀態一致（BLOCKED ⇒ 尚未出現；RUNNABLE ⇒ ['CGC_RHO']）",
      (st["delivery_flags"] == ["CGC_RHO"]) == (st["verdict"] == "RUNNABLE"),
      str(st["delivery_flags"]))
    c("CGC_RHO_FILL is a dependent knob, not a split",
      st["dependent_flags"] == ["CGC_RHO_FILL"], str(st["dependent_flags"]))
    c("delivery patch present with pinned md5", patch_state(ROOT)["md5_ok"],
      "md5=%s" % patch_state(ROOT)["md5"])
    # ①b 切分後的形狀（fixture）：機制閘＝cgc_rho_deliver、四個交付列印由量具閘守著。
    with tempfile.TemporaryDirectory() as td:
        _fixture_split(td, metered=True)
        s1 = mechanism_block(td)
        c("split fixture -> RUNNABLE", s1["verdict"] == "RUNNABLE", s1["verdict"])
        c("split fixture: 4/4 delivery prints metered",
          len([p for p in s1["prints"] if p["tag"] in DELIVERY_PRINTS and p["ok"]]) == 4,
          str(s1["leaks"]))
        _fixture_split(td, metered=False)
        s2 = mechanism_block(td)
        c("fixture without the meter guard -> SPLIT_LEAKY", s2["verdict"] == "SPLIT_LEAKY",
          s2["verdict"])
        c("the leak names the print", any(l["tag"] == "CGC-RHO-FILL" for l in s2["leaks"]),
          str(s2["leaks"]))
    # ①c 建置狀態：用兩顆假 dylib 做正／反兩向（不依賴這棵樹真的 build 過）。
    with tempfile.TemporaryDirectory() as td:
        d = os.path.join(td, "src/llama.cpp/build/bin")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "libllama.0.dylib"), "w", encoding="utf-8") as fh:
            fh.write("CGC_RHO_PROBE\nCGC_RHO_FILL\n")
        b0 = build_state(td)
        c("build_state: probe-only artifact -> built=False",
          (b0["checked"], b0["built"]) == (1, False), str(b0))
        with open(os.path.join(d, "libllama.1.dylib"), "w", encoding="utf-8") as fh:
            fh.write("CGC_RHO\nCGC_RHO_PROBE\n")
        b1 = build_state(td)
        c("build_state: a dylib carrying the exact literal -> built=True",
          (b1["checked"], b1["built"]) == (2, True), str(b1))
    # ② 命令：cell 旗標來自 cell_contract（含 --prompt 2048）、兩趟反序、臂 B 帶旗標。
    cmds = plan(ROOT)
    flat = [" ".join(cc["cmd"]) for cc in cmds]
    c("plan has 4 commands (2 orders x 2 arms)", len(cmds) == 4, str(len(cmds)))
    c("plan uses the cell's flags", all("--prompt 2048" in s and "--reps 3" in s for s in flat),
      flat[0][:90])
    c("order2 is reversed", cmds[2]["arm"] == "B" and cmds[3]["arm"] == "A",
      "".join(cc["arm"] for cc in cmds))
    c("probe cannot be the delivery flag",
      _raises(lambda: plan(ROOT, rho_flag="CGC_RHO_PROBE")), "plan() refused")
    # ③ 判詞：可解／不可解／不引用／反向。
    with tempfile.TemporaryDirectory() as td:
        good = [11.20, 11.28, 11.24]           # 散布 0.7%
        fast = [12.10, 12.22, 12.18]           # +8.2%（> 散布）
        mild = [11.26, 11.30, 11.25]
        a1 = os.path.join(td, "o1_A.json"); b1 = os.path.join(td, "o1_B.json")
        a2 = os.path.join(td, "o2_A.json"); b2 = os.path.join(td, "o2_B.json")
        _mk_product(a1, 11.24, good); _mk_product(b1, 12.17, fast, rho="1")
        _mk_product(a2, 11.24, good); _mk_product(b2, 12.15, fast, rho="1")
        v = both_orders(judge(a1, b1), judge(a2, b2))
        c("two same-sign resolvable orders -> PRICE", v["verdict"] == "PRICE",
          "%s %.2f%%" % (v["verdict"], v.get("median_pct", 0)))
        _mk_product(b2, 11.26, mild, rho="1")
        c("a free/no-effect order is NOT a price",
          both_orders(judge(a1, b1), judge(a2, b2))["verdict"] == "NO_EFFECT",
          both_orders(judge(a1, b1), judge(a2, b2))["verdict"])
        _mk_product(b2, 10.30, [10.28, 10.32, 10.30], rho="1")     # −8.4% ⇒ 反向
        c("resolvable but opposite -> REFUSE",
          both_orders(judge(a1, b1), judge(a2, b2))["verdict"] == "REFUSE",
          both_orders(judge(a1, b1), judge(a2, b2))["verdict"])
        _mk_product(b1, 12.17, fast, rho="1", attrib="swap")
        c("a dirty arm is not a price", judge(a1, b1)["verdict"] == "REFUSE", judge(a1, b1)["why"][:60])
        _mk_product(b1, 12.17, fast, rho="1")
        c("clean pair is quotable", judge(a1, b1)["verdict"] in ("PRICE", "NO_EFFECT"),
          judge(a1, b1)["verdict"])
    bad = [t for t in ok if not t[1]]
    for name, good_, got in ok:
        print("  [%s] %s%s" % ("PASS" if good_ else "FAIL", name, "" if good_ else "  -> " + str(got)))
    print("\nselftest: %d/%d PASS" % (len(ok) - len(bad), len(ok)))
    return 0 if not bad else 1


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except SystemExit:
        return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ρ 價格的權威 row 版：阻塞判詞、命令、預註冊判詞")
    ap.add_argument("--check", action="store_true", help="讀原始碼判 BLOCKED／RUNNABLE／SPLIT_LEAKY（預設）")
    ap.add_argument("--plan", action="store_true", help="印兩臂×兩趟反序的 harness bench 命令")
    ap.add_argument("--checklist", action="store_true",
                    help="印套用→建置→oracle（M1 9/9）→四條權威 row 命令的清單")
    ap.add_argument("--judge", nargs=2, metavar=("ORDER1_B_vs_A", "ORDER2_B_vs_A"),
                    help="兩趟反序的配對：每趟給一個 JSON，內含 A 與 B 兩臂的兩份產物路徑")
    ap.add_argument("--rho-flag", default="CGC_RHO")
    ap.add_argument("--cell", default="(default)")
    ap.add_argument("--root", default=ROOT, help="要判的樹（預設＝這份工具的 repo root）")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    root = os.path.abspath(args.root)

    if args.judge:
        out = []
        for spec in args.judge:
            with open(spec, encoding="utf-8") as fh:
                pair = json.load(fh)
            out.append(judge(pair["A"], pair["B"], args.rho_flag))
        res = both_orders(out[0], out[1])
        print(json.dumps(res, ensure_ascii=False, indent=1))
        print("\nVERDICT: %s -- %s" % (res["verdict"], res.get("why", "")))
        if args.json_out:
            json.dump(res, open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return 0

    st = mechanism_block(root)
    if args.checklist:
        return checklist(root, cell=args.cell, rho_flag=args.rho_flag)
    if args.plan:
        if st["blocked"]:
            print("⚠ BLOCKED：%s" % st["why"], file=sys.stderr)
        for cc in plan(root, cell=args.cell, rho_flag=args.rho_flag):
            print("[order%d %s %s] %s" % (cc["order"], cc["arm"], cc["arm_spec"], " ".join(cc["cmd"])))
        return 0 if not st["blocked"] else 1

    # --check（預設）
    for cc in st["coords"]:
        print("  %-28s %s:%s  %s" % (cc["label"], cc["file"], cc.get("line") or "—",
                                      (cc.get("snippet") or cc.get("note") or "")[:100]))
    dp = [p for p in st["prints"] if p["tag"] in DELIVERY_PRINTS]
    print("  交付路徑列印的量具閘門: %d/%d 由 cgc_rho_meter() 守著%s"
          % (sum(1 for p in dp if p["ok"]), len(dp),
             "" if not st["leaks"] else
             "；洩漏 " + "、".join(":%d %s" % (l["line"], l["tag"]) for l in st["leaks"])))
    print("  CGC_RHO* 旗標: %s" % (", ".join(st["flags"]) or "—"))
    print("\nVERDICT: %s -- %s" % (st["verdict"], st["why"]))
    if args.json_out:
        json.dump(st, open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
