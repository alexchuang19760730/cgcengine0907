#!/usr/bin/env python3
"""公式推論審計（250/25）—— 每一條定量主張都要有量測或數學支撐。

緣起：operator 2026-09-25 下令「公式推論一定要基於量測或數學，不能憑空推論；
若缺乏支撐，就要丟到廢棄」。本檔把 `docs/MTP_AMORTIZATION_RECHECK_2026-09-25.*`
與 mindmap M 軸裡的每一條定量主張登錄成表，機械判定它的支撐等級。

六個等級（前兩個可引用，其餘一律廢棄）：
  M   MEASURED      直接來自實測計數器／時間，有產物檔可回溯
  D   IDENTITY      由定義或量綱恆等推出，不含經驗假設（但要標適用範圍）
  X   EXTRAPOLATION 依賴模型形式外推到「未量測」的參數點
  A   ASSUMPTION    假設某兩個量互相獨立（未量測）
  S   SPECULATION   推測因果
  U   UNSOURCED     數字沒有出處

機檢三件事：
  1. 每條 M 類主張都能從產物 JSON 重算出來（容差內）
  2. 每條 D 類主張都能用數值驗算（恆等式／微分／極限）
  3. 每條 X/A/S/U 類主張的**字面串**不得出現在報告的「結論區」
     （結論區＝檔內第一個「⛔ 廢棄」錨點之前；錨點之後是廢棄區，允許出現）

用法：
    python3 scripts/check/formula_audit.py            # 全跑（驗算＋掃描）
    python3 scripts/check/formula_audit.py --selftest # 內建正負例
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "Backup/phase_decomp/spec_cost_en-mtp-repro-250925.json"

# 被掃描的「結論載體」：這些檔案在廢棄錨點之前不得出現廢棄斷言
SCAN_TARGETS = [
    ROOT / "docs/MTP_AMORTIZATION_RECHECK_2026-09-25.md",
    ROOT / "docs/MTP_AMORTIZATION_RECHECK_2026-09-25.html",
    ROOT / "docs/FORMULA_AUDIT_MTP_2026-09-25.md",
    ROOT / "docs/FORMULA_AUDIT_MTP_2026-09-25.html",
]
VOID_ANCHOR = "⛔ 廢棄"
MINDMAP = ROOT / "docs/mindmap/mindmap.json"

M, D, X, A, S, U = "M", "D", "X", "A", "S", "U"
KEEP, VOID = "KEEP", "VOID"


# ──────────────────────────────────────────────────────────────
# 審計表：id / 主張 / 等級 / 支撐 / 判定 / 備註
# ──────────────────────────────────────────────────────────────
def audit_rows() -> list[dict]:
    return [
        dict(id="F01", claim="k_eff = 1（請求 3、模組截到 1）", cls=M,
             support="SPECDBG round: ... draft=1 逐輪皆是（/tmp/spec_cost_k3_r*.stderr.log）",
             verdict=KEEP),
        dict(id="F02", claim="輪內 S = 0.914 / 0.682 / 1.050（中位 0.914）", cls=M,
             support="spec_cost_en-mtp-repro-250925.json 三輪 tps_tg 比值",
             verdict=KEEP),
        dict(id="F03", claim="E = 1.707 / 1.455 / 1.620（中位 1.620）", cls=M,
             support="同上，mean_len 欄", verdict=KEEP),
        dict(id="F04", claim="a = E−1（k_eff=1）⇒ 中位 0.620", cls=M,
             support="E 的派生；a 定義為「每輪接受數 ÷ k_eff」", verdict=KEEP),
        dict(id="F05", claim="m = cost−1 = 0.868", cls=M,
             support="cost = ms_on/ms_off 的派生；**僅在 k_eff=1 成立**",
             verdict=KEEP, note="不得外推到別的 k"),
        dict(id="F06", claim="MiB/step 19.9 → 49.9", cls=M,
             support="read_mib_per_step；實測範圍 19.87~20.25 → 49.87~53.70",
             verdict=KEEP, note="應報範圍，不報單點"),
        dict(id="F07", claim="worst layer distinct 114 → 187（slots 143）", cls=M,
             support="實測範圍 111~130 → 168~205", verdict=KEEP, note="應報範圍"),
        dict(id="F08", claim="layers_over_slots 0 → 4", cls=M,
             support="實測 0 → 2~4（整數計數）", verdict=KEEP, note="應報範圍"),
        dict(id="F09", claim="池 hit% 95.0 → 92.3", cls=M,
             support="實測 94.9~95.0 → 91.9~92.3", verdict=KEEP),
        dict(id="F10", claim="S = E / cost", cls=D,
             support="量綱恆等：S=(E/step_on)/(1/step_off)", verdict=KEEP),
        dict(id="F11", claim="E = 1 + a·k_eff", cls=D,
             support="a 的定義式；不是經驗規律", verdict=KEEP),
        dict(id="F12", claim="k_eff=1 時 2× 不可達（E≤2 ⇒ 需 m≤0）", cls=D,
             support="S=E/(1+m)=2 ⇒ E=2(1+m)；a≤1 ⇒ E≤2 ⇒ m≤0",
             verdict=KEEP, note="**僅在 k_eff=1 下成立**"),
        dict(id="F13", claim="dlnS/dlna = a/(1+a)、dlnS/dlnm = −m/(1+m)", cls=D,
             support="k=1 處對 S=(1+a)/(1+m) 微分；中位 +0.383 / −0.465",
             verdict=KEEP, note="局部值；k 變了就變，不是「m 永遠比 a 重要」"),
        dict(id="F14", claim="原 html 的 E=2.31 高估 35%~59%", cls=D,
             support="2.31 ÷ 三輪 E（1.707/1.455/1.620）", verdict=KEEP),
        dict(id="F15", claim="原 html 的 m=0.19 低估 2.9~6.0×", cls=D,
             support="三輪 m（0.543/0.868/1.134）÷ 0.19", verdict=KEEP),
        dict(id="F24", claim="09-24 的 m 隨 k 變化：1.176 / 0.840 / 0.961 / 0.920", cls=M,
             support="MTP_AMORTIZATION_2026-09-25.html 的 step 實測（48.2/104.87/129.16/187.19/225.61）",
             verdict=KEEP, note="⇒ 線性模型在他批資料上就不成立"),

        # ── 以下缺乏支撐 ⇒ 廢棄 ────────────────────────────────
        dict(id="F16", claim="k=3 時 S = 0.794", cls=X,
             support="本批實測只有 k∈{0,1}；k=3 從未量測", verdict=VOID,
             note="依賴 cost 線性於 k 的外推", void_marks=["0.794"]),
        dict(id="F17", claim="k→∞ 上界 a/m = 0.714", cls=X,
             support="極限存在，但線性模型本身未被量測支撐", verdict=VOID,
             void_marks=["0.714"]),
        dict(id="F18", claim="沿 k 軸加到多大都虧", cls=X,
             support="同 F17 的外推", verdict=VOID),
        dict(id="F19", claim="a 拉滿到 1.0 ⇒ S = 1.071（只有 +7%）", cls=A,
             support="假設 cost 與 a 獨立（未量測；本批無 a 變化對照臂）",
             verdict=VOID, void_marks=["1.071"]),
        dict(id="F20", claim="m≈0.87 是穩定讀數，兩批差 0.5%", cls=U,
             support="量綱錯配：42.03 是 d(step)/dT（ms/verify-token），"
                     "48.2 是「每產出 token 成本」；同義口徑應是 1.176 vs 0.868（差 35%）",
             verdict=VOID, void_marks=["差 0.5%", "0.872"]),
        dict(id="F21", claim="draft 前向只佔一輪 ~7.5%", cls=U,
             support="本輪 stderr 無 CGC-MTP-PERF 輸出；7.5% 全庫無出處",
             verdict=VOID, void_marks=["7.5%"]),
        dict(id="F22", claim="池 hit%「k_eff=1 才沒崩到 57%」", cls=U,
             support="57% 全庫無出處", verdict=VOID, void_marks=["沒崩到 57%"]),
        dict(id="F23", claim="原 html 的 S=1.19 來自 13.78/11.49 兩台儀器相除", cls=S,
             support="13.78/11.49=1.199 是算術，但因果是推測", verdict=VOID,
             note="算術可留，因果句廢棄"),
        dict(id="F25", claim="a=0.44 是 ctx_other regression 的產物", cls=A,
             support="D5 FAIL 未歸因（65c76b8c7 的 commit msg 明寫）", verdict=VOID,
             note="改標「相關但未證明」"),
    ]


def void_marks() -> list[str]:
    out: list[str] = []
    for r in audit_rows():
        out.extend(r.get("void_marks", []))
    return out


# ──────────────────────────────────────────────────────────────
# 1. M 類：從產物重算
# ──────────────────────────────────────────────────────────────
def recompute(product: Path = PRODUCT) -> dict:
    d = json.loads(product.read_text(encoding="utf-8"))
    by: dict[int, dict[int, dict]] = {}
    for x in d["runs"]:
        by.setdefault(x["round"], {})[x["k"]] = x
    rows = []
    for r, g in sorted(by.items()):
        off, on = g[0], g[3]
        cost = on["ms_per_step"] / off["ms_per_step"]
        rows.append(dict(
            round=r,
            S=on["tps_tg"] / off["tps_tg"],
            E=on["E"], k_eff=on["mean_draft"],
            a=on["E"] - 1.0, m=cost - 1.0, cost=cost,
            mib_off=off["read_mib_per_step"], mib_on=on["read_mib_per_step"],
            worst_off=off["worst_layer_distinct"], worst_on=on["worst_layer_distinct"],
            over_off=off["layers_over_slots"], over_on=on["layers_over_slots"],
            hit_off=off["pool_hit_pct"], hit_on=on["pool_hit_pct"],
        ))
    return dict(rows=rows, slots=d["runs"][0]["per_layer_slots"])


def check_measured(rc: dict) -> list[str]:
    """M 類：聲明值必須能從產物重算（容差 5e-3）。"""
    errs: list[str] = []
    rows = rc["rows"]
    med = lambda xs: sorted(xs)[len(xs) // 2]

    def close(got, want, tol=5e-3, what=""):
        if abs(got - want) > tol:
            errs.append(f"{what}: 重算 {got:.4f} ≠ 聲明 {want:.4f}")

    close(med([r["S"] for r in rows]), 0.914, what="F02 S 中位")
    close(med([r["E"] for r in rows]), 1.620, what="F03 E 中位")
    close(med([r["a"] for r in rows]), 0.620, what="F04 a 中位")
    close(med([r["m"] for r in rows]), 0.868, what="F05 m 中位")
    if not all(abs(r["k_eff"] - 1.0) < 1e-9 for r in rows):
        errs.append("F01: k_eff 不是每輪都等於 1")
    if min(r["mib_on"] for r in rows) < 49.0 or max(r["mib_off"] for r in rows) > 21.0:
        errs.append("F06: MiB/step 範圍與聲明不符")
    if min(r["worst_on"] for r in rows) < 160 or max(r["worst_off"] for r in rows) > 135:
        errs.append("F07: worst distinct 範圍與聲明不符")
    if min(r["over_on"] for r in rows) < 2 or any(r["over_off"] != 0 for r in rows):
        errs.append("F08: layers_over_slots 與聲明不符")
    if min(r["hit_on"] for r in rows) > 92.4 or max(r["hit_off"] for r in rows) < 94.9:
        errs.append("F09: hit% 與聲明不符")
    return errs


# ──────────────────────────────────────────────────────────────
# 2. D 類：數值驗算
# ──────────────────────────────────────────────────────────────
def check_identity(rc: dict) -> list[str]:
    errs: list[str] = []
    rows = rc["rows"]
    med = lambda xs: sorted(xs)[len(xs) // 2]

    # F10：S = E/cost
    for r in rows:
        if abs(r["S"] - r["E"] / r["cost"]) > 1e-3:
            errs.append(f"F10: r{r['round']} S≠E/cost（{r['S']:.4f} vs {r['E']/r['cost']:.4f}）")

    # F11：E = 1 + a·k_eff（a := E−1，k_eff=1）
    for r in rows:
        if abs(r["E"] - (1 + r["a"] * r["k_eff"])) > 1e-9:
            errs.append(f"F11: r{r['round']} E≠1+a·k_eff")

    # F12：k_eff=1 且 a≤1 ⇒ S=2 需 m≤0
    for r in rows:
        if abs(r["k_eff"] - 1.0) < 1e-9 and r["a"] <= 1.0:
            need_m = 2.0 / 2.0 - 1.0  # E=2 ⇒ 2 = 2(1+m) ⇒ m=0
            if r["m"] > need_m:  # 實測 m>0 ⇒ 2× 不可達
                continue
            errs.append(f"F12: r{r['round']} m={r['m']:.4f} 竟然 ≤0，聲明要复查")

    # F13：彈性（k=1）
    a, m = med([r["a"] for r in rows]), med([r["m"] for r in rows])
    ea, em = a / (1 + a), -m / (1 + m)
    if abs(ea - 0.383) > 5e-3 or abs(em + 0.465) > 5e-3:
        errs.append(f"F13: 彈性重算 {ea:.4f}/{em:.4f} ≠ 聲明 +0.383/−0.465")

    # F14/F15：對原 html 的倍數（報範圍）
    Es = sorted(r["E"] for r in rows)
    ms = sorted(r["m"] for r in rows)
    hi = [2.31 / e for e in Es]
    lo = [mm / 0.19 for mm in ms]
    if not (1.35 <= min(hi) and max(hi) <= 1.60):
        errs.append(f"F14: 高估倍數範圍 {min(hi):.2f}~{max(hi):.2f} 與聲明 1.35~1.59 不符")
    if not (2.8 <= min(lo) and max(lo) <= 6.1):
        errs.append(f"F15: 低估倍數範圍 {min(lo):.2f}~{max(lo):.2f} 與聲明 2.9~6.0 不符")

    # F24：09-24 的 m 隨 k 變化（線性模型在他批資料上就不成立）
    step = {0: 48.20, 1: 104.87, 2: 129.16, 3: 187.19, 4: 225.61}
    mk = [(step[k] - step[0]) / step[0] / k for k in (1, 2, 3, 4)]
    if not (abs(mk[0] - 1.176) < 5e-3 and abs(mk[1] - 0.840) < 5e-3
            and abs(mk[2] - 0.961) < 5e-3 and abs(mk[3] - 0.920) < 5e-3):
        errs.append(f"F24: 09-24 的 m(k) 重算 {[round(x,3) for x in mk]} 與聲明不符")
    spread = max(mk) / min(mk)
    if spread < 1.15:
        errs.append(f"F24: m(k) 散佈只有 {spread:.2f}×，不足以判非線性")
    return errs


# ──────────────────────────────────────────────────────────────
# 3. X/A/S/U：廢棄斷言不得出現在結論區
# ──────────────────────────────────────────────────────────────
def scan_void() -> list[str]:
    errs: list[str] = []
    marks = void_marks()
    for f in SCAN_TARGETS:
        if not f.exists():
            errs.append(f"掃描目標不存在：{f.name}")
            continue
        text = f.read_text(encoding="utf-8")
        anchor = text.find(VOID_ANCHOR)
        head = text if anchor < 0 else text[:anchor]
        for mk in marks:
            if mk in head:
                errs.append(f"廢棄斷言「{mk}」出現在 {f.name} 的結論區（應移入廢棄區）")
    return errs


def scan_mindmap() -> list[str]:
    """mindmap 是結論載體，沒有「廢棄區」概念 ⇒ 廢棄斷言一律不得出現。"""
    errs: list[str] = []
    if not MINDMAP.exists():
        return ["mindmap.json 不存在"]
    d = json.loads(MINDMAP.read_text(encoding="utf-8"))
    marks = void_marks()

    def walk(node, path):
        if isinstance(node, str):
            for mk in marks:
                if mk in node:
                    errs.append(f"廢棄斷言「{mk}」出現在 mindmap.json {path}")
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(d.get("subgoal_briefs", {}), "subgoal_briefs")
    walk(d.get("meta", {}), "meta")
    return errs


def run() -> int:
    rc = recompute()
    errs = check_measured(rc) + check_identity(rc) + scan_void() + scan_mindmap()
    rows = audit_rows()
    nk = sum(1 for r in rows if r["verdict"] == KEEP)
    nv = sum(1 for r in rows if r["verdict"] == VOID)
    print(f"審計條目 {len(rows)}：KEEP {nk}／VOID {nv}")
    for r in rows:
        flag = "✓" if r["verdict"] == KEEP else "⛔"
        print(f"  {flag} {r['id']} [{r['cls']}] {r['claim']}")
    if errs:
        print("\n".join("FAIL " + e for e in errs))
        return 1
    print("OK: 實測可重算、恆等式成立、廢棄斷言未回潮")
    return 0


# ──────────────────────────────────────────────────────────────
def selftest() -> int:
    ok = 0
    total = 0

    def t(name, cond):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print(("  ok  " if cond else "  FAIL ") + name)
        return cond

    rows = audit_rows()
    ids = [r["id"] for r in rows]
    t("條目 id 唯一", len(ids) == len(set(ids)))
    t("每條都有等級與判定", all(r.get("cls") in (M, D, X, A, S, U) and r.get("verdict") in (KEEP, VOID) for r in rows))
    t("M／D 類全部 KEEP", all(r["verdict"] == KEEP for r in rows if r["cls"] in (M, D)))
    t("X／A／S／U 類全部 VOID", all(r["verdict"] == VOID for r in rows if r["cls"] in (X, A, S, U)))
    t("每個等級都有條目", {r["cls"] for r in rows} == {M, D, X, A, S, U})
    t("廢棄條目至少有一條帶字面串", len(void_marks()) >= 5)
    t("mindmap 掃描器可載入", MINDMAP.exists())

    rc = recompute()
    t("產物三輪可重算", len(rc["rows"]) == 3)
    t("實測項無誤", not check_measured(rc))
    t("恆等式無誤", not check_identity(rc))

    # 掃描器的負例：把廢棄斷言塞進結論區要能被抓到
    tmp = Path("/tmp/_formula_audit_probe.md")
    tmp.write_text("結論：m≈0.87 是穩定讀數，差 0.5%，draft 只佔 7.5%，k=3 時 S=0.794\n"
                   + VOID_ANCHOR + "\n以下 OK\n", encoding="utf-8")
    orig = SCAN_TARGETS[:]
    SCAN_TARGETS.clear()
    SCAN_TARGETS.append(tmp)
    t("掃描器能抓到結論區裡的廢棄斷言", len(scan_void()) >= 3)
    tmp.write_text("結論：只有有支撐的項目\n" + VOID_ANCHOR + "\n差 0.5%（廢棄）\n", encoding="utf-8")
    t("廢棄區裡的同樣字串不算違規", not scan_void())
    t("mindmap M 軸目前無廢棄斷言", not scan_mindmap())
    SCAN_TARGETS.clear()
    SCAN_TARGETS.extend(orig)
    tmp.unlink(missing_ok=True)

    print(f"selftest: {ok}/{total}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(run())
