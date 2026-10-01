#!/usr/bin/env python3
"""L20-10 判詞：格子判別（「量測起點」 vs batch）—— **v2：可滿足的起點格**。

歷史：09-30 20:20–20:23 的兩趟（`delivery-ws256`＝B1、`default-b512`＝B3）判 `INVALID_CELL`
（B1 的格不可滿足：`warm_skip=256 ≥ gen=128` ⇒ 計時段 −128 ⇒ 起點假設**未被測**；B3 被窗口擋）。
那一輪的判詞留在 `verdict.json` 的 `history` 與 `docs/L2010_B1B3_2026-09-30.md`。
這一版把格換成**可滿足**的形狀，判的是新的一對：

    A（對照）＝ `delivery`            ：`gen 128 / warm_skip 64`  ⇒ 計時段 64（權威 row `p0/n64`）
    B（起點）＝ `delivery-ws192`      ：`gen 256 / warm_skip 192` ⇒ 計時段**一樣是 64**，
                                       只有**起點**不同（每 rep 先 warm 192 個不計時 token）

「計時段保住 64」是這一版的重點：兩臂的權威 row 逐字相同（`p0/n64`）⇒ 唯一差異是起點，
不會把「起點」和「樣本量／離散」混在一起（測試卡 §2.5.4b）。

2026-10-01 第二輪：同一隊改用 §2.5.4c 的 **reps 孿生**（`delivery-reps7`／`delivery-ws192-reps7`：
與上面兩格**只差 `reps` 3→7**）—— 第一輪在乾淨窗裡兩臂首次都可引用，但 **A4** 判 `REFUSE`
（`|Δstep| 3.989 ≤ 兩臂散布 7.515 ms`：效應小於解析度）。判別句與門檻**一字未改**；
這一輪要解的是**散布**，不是把引用閘門放寬。

跑前寫死（改這裡＝改判別句，留痕在 git）：
  A1 **可滿足性**：`warm_skip < gen`（否則判 `INVALID_CELL`，不是「髒」）；
  A2 **引用**：兩臂的 tg 行都要 `QUOTABLE`（`quote_gate` R1–R8）；
  A3 **判別句**：B 落在 `11.5`（(default) 錨 11.3–11.7 的中值）±5% ⇒ 判 `START_POINT`
     （起點是主因）；落在 `10.5 ± 5%`（≈ 對照臂的 10.2–10.3）⇒ 判 `SAME_BAND`
     （falsify：**起點不是主因**）；B 落在 9.7 附近 ⇒ `BATCH_LIKE`（與 B3 的假設同向，怪）；
     其餘 ⇒ `INCONCLUSIVE`。
  A4 **可辨識性**：B 與 A 的差必須大於兩臂自身在 ms 上的散布，否則判 `REFUSE`（不可辨識）。
  A5 兩個候選落點（11.5 vs 10.3 的對照帶）相距 > 10% ⇒ 判別句本身分得開。

用法：
  python3 scripts/check/l2010_verdict.py --check
  python3 scripts/check/l2010_verdict.py --write
  python3 scripts/check/l2010_verdict.py --selftest
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

PRODUCT_DIR = "Backup/l2010_b1b3_2026-09-30"
VERDICT_REL = PRODUCT_DIR + "/verdict.json"

# 兩對產物各有自己的名字：\`dirty\` ＝ 一對在**髒窗**下跑的（診斷級，已跑）；
# \`clean\` ＝ 清窗後要跑的那一對 —— 名字不同是故意的，否則舊的髒產物會被當成答案
# （看板的 \`needed\` spec 就是掃 \`clean_ws192_*.json\`）。
PAIR_FILES = {
    "dirty": dict(A="ws192_A.json", B="ws192_B.json", verdict="verdict.json"),
    "clean": dict(A="clean_ws192_A.json", B="clean_ws192_B.json",
                  verdict="clean_ws192_verdict.json"),
}


def arms_for_pair(pair: str) -> dict:
    """把 ARMS 的產物檔名換成那一對的（判別句、門檻、格宣告都不動）。"""
    f = PAIR_FILES[pair]
    out = {}
    for name, spec in ARMS.items():
        s = dict(spec)
        s["file"] = PRODUCT_DIR + "/" + f[name]
        out[name] = s
    return out

# ── 跑前寫死的常數（改這裡＝改判別句）────────────────────────────────────────
ANCHOR_DEFAULT_TPS = 11.5     # (default) cell 的錨（11.3–11.7 的中值）
NEAR_PCT = 0.05               # A3：「落在」＝與落點的相對差 ≤5%
SAME_BAND_TPS = 10.5          # A3：對照帶（≈ delivery 的 10.2–10.3）
SAME_BAND_PCT = 0.05
BATCH_LIKE_TPS = 9.7          # B3 那一族的落點（不該出現在這一對，出現就先懷疑）

# ── REFUSE 時要說的話（單一落點：兩條 REFUSE 路徑共用，免得一邊改了一邊沒改）─────
# 2026-10-01 實測修正：原本這裡寫「清窗＝重開機」——**那句話是錯的**，而且方向相反：
# 重開機後 swap 從 0 起跑 ⇒ 成長必為正，而 `attribution()` 的第一條是 `growth > 512 MiB`
# ⇒ 重開機保證第一趟判 swap（實測 +1534.81 MiB）。重開機後的**第二趟**窗過了
# （growth 257.75、attribution=none），卻換成 **離散** 擋下（逐 rep [11.06, 4.03, 11.82]）。
# 兩個失敗模式同一個根：池子 8192 ＋ Metal 駐留 ~10 814 ＞ 本機上限 11 453 MiB（超 ~8 577 MiB）
# ⇒ 這一格的 pool 一定有一部分住在 swap 檔裡（harness 自己的峰值閘門就把它印成 REFUSE）。
WINDOW_NEXT = [
    "⚠ 重開機**不是**解（2026-10-01 實測：重開機後第一趟 swap 成長 +1534.81 MiB > 512 的門檻 ⇒ "
    "反而保證判 swap；第二趟窗過了（attribution=none）卻被離散擋下：逐 rep [11.06, 4.03, 11.82]）",
    "⚠ 也**不是**「池子放不下」：這句在 2026-10-01 被 operator 當場更正 —— L4 pool 的區域是 "
    "`regions adopted from expert tensors`（`src/llama.cpp/src/llama-expert-cache.cpp:4152`），"
    "它的頁面**就在**量到的駐留裡；引擎自報 `resident=6197.04 MiB`（143 槽 × 40 層，**靜態**量）"
    "⇒ 池子預算不可以再加一次（16 GB 的算術也不允許：10 814 ＋ 7 954 ＞ 16 GB 的那一趟不可能跑完）",
    "真正的讀值：上限 11 453 −（峰值 Metal 駐留 10 814 ＋ 保留 1 024）＝ **−385 MiB** ⇒ 這一格在**邊緣**"
    "上跑；成敗取決於那 ~4.6 GiB 的池子硬碟填充落在**窗內還是窗外**"
    "（`docs/PROD_NEW_BASELINE_REPRO_2026-09-25.md`：“差的不是填充量，是填充**落點**”）",
    "⇒ 這一格的下一步（2026-10-01 第二輪）：兩臂換成測試卡 §2.5.4c 的 **reps 孿生**"
    "（`delivery-reps7`／`delivery-ws192-reps7`，只差 `reps` 3→7；其餘逐字相同、計時段仍是 64）；"
    "判別句與 A4 門檻**一字未改** —— 要解的是**解析度**，不是把引用閘門放寬",
    "⇒ 2026-10-01 實測（n=7、同一窗）：A 全 rep **1.1197** ⇒ `UNUSABLE`（**引用閘門**先擋，"
    "A4 沒輪到）、B 1.0818 ⇒ `QUOTABLE`；兩臂中位數 11.7941／11.9281 ⇒ 診斷級 Δ 只剩 +1.14%"
    "（n=3 讀 +4.8%）⇒ operator **明文否證式結案（排除）**：結成「起點在 n=7 下不再可辨」"
    "（n=3 的 +4.8% 是第一 rep 冷啟對 n=3 中位數的拉扯）—— 判詞**維持 `REFUSE`**，"
    "結案是 operator 的處置，不是把 `REFUSE` 改寫成否証；這一格不再重跑",
    "⚠ A4／引用閘門的統計量改革是**另一個前瞻案**（已立卡："
    "`scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml`，法源 `docs/QUOTE_CALIBER_PAIRED_PREREG_2026-10-01.md`；"
    "配對 per-pair 比率 ＋ 兩趟反序；預註冊、前瞻、0 回填），**不回填**這一格的判詞——不得用它默認放寬",
    "逐 rep 與 attribution 都在 Backup/l2010_b1b3_2026-09-30/"
    "（失敗那幾趟在 clean_logs/archive/）；結案文件：docs/L2010_CLEAN_WS192_20261001.md",
]
CTRL_BAND = (9.9, 11.1)       # 對照臂的預期帶（只是記錄；不由它判詞）

ARMS = {
    "A": dict(cell="delivery-reps7", file=PRODUCT_DIR + "/ws192_A.json",
              hypothesis="對照（warm 64）", target=None,
              declared=dict(gen=128, warm_skip=64, prompt=0, batch=512, reps=7),
              why="對照臂：§2.5.4c 的孿生（delivery ＋ reps 3→7）的權威 row（p0/n64）；"
                  "它的水準就是「起點較冷」那一側"),
    "B": dict(cell="delivery-ws192-reps7", file=PRODUCT_DIR + "/ws192_B.json",
              hypothesis="起點（warm 192）", target=ANCHOR_DEFAULT_TPS,
              declared=dict(gen=256, warm_skip=192, prompt=0, batch=512, reps=7),
              why="判別句：起點是主因 ⇒ 本格升到 ~11.5（±5%）；n=7 是為了讓 |Δstep| 大於兩臂散布（A4）"),
}


def _qg():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import quote_gate
    return quote_gate


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    prods = doc if isinstance(doc, list) else [doc]
    return [p for p in prods if isinstance(p, dict)]


def _tg_samples(prod: dict) -> list:
    for row in prod.get("rows") or []:
        if row.get("n_prompt") == 0 and row.get("avg_ts") is not None:
            return list(row.get("samples_ts") or [])
    return []


def feasible(declared: dict) -> tuple:
    """A1：這一格的形狀可不可能存在。回 (ok, why)。"""
    gen, ws = int(declared.get("gen", 0)), int(declared.get("warm_skip", 0))
    kept = gen - ws
    if ws and kept <= 0:
        return False, ("warm_skip=%d ≥ gen=%d ⇒ 計時段 %d ≤ 0：這一格定義上不存在"
                       "（引擎語意是「先跑 N 個不計時 token，計時的是 gen−N」）" % (ws, gen, kept))
    return True, "warm_skip=%d < gen=%d（計時段 %d）" % (ws, gen, kept)


def judge_arm(root: str, name: str, spec: dict) -> dict:
    path = os.path.join(root, spec["file"])
    out = dict(arm=name, cell=spec["cell"], hypothesis=spec["hypothesis"], path=spec["file"],
               declared=spec["declared"], why=spec["why"], exists=os.path.exists(path),
               target=spec["target"])
    ok, why = feasible(spec["declared"])
    out["feasible"], out["feasible_why"] = ok, why
    if not out["exists"]:
        out.update(verdict="MISSING", reasons=["產物不在：%s（還沒跑或路徑不同）" % spec["file"]])
        return out
    prods = _load(path)
    if not prods:
        out.update(verdict="REFUSE", reasons=["產物裡沒有可判的臂"])
        return out
    prod = prods[0]
    samples = _tg_samples(prod)
    med = sorted(samples)[len(samples) // 2] if samples else None
    out.update(decode_tps_median=med, samples=samples,
               warm_skip=prod.get("warm_skip"), warm_skip_applied=prod.get("warm_skip_applied"),
               cell_named=(prod.get("contract") or {}).get("cell"),
               attribution=(prod.get("attribution") or {}).get("verdict"))
    recs = _qg().scan([path])
    dec = [r for r in recs if str(r.get("shape", "")).startswith("p0/n")]
    out["quote"] = (dict(verdict=dec[0]["verdict"], reasons=dec[0]["reasons"]) if dec
                    else dict(verdict="REFUSE", reasons=["沒有 decode 行（p0/n*）"]))
    if dec:
        out["spread"] = (dec[0].get("metrics") or {}).get("all_spread")
    if med is not None:
        out["delta_vs_anchor_pct"] = (med - ANCHOR_DEFAULT_TPS) / ANCHOR_DEFAULT_TPS * 100.0
        out["near_anchor"] = abs(med - ANCHOR_DEFAULT_TPS) <= NEAR_PCT * ANCHOR_DEFAULT_TPS
        out["in_same_band"] = abs(med - SAME_BAND_TPS) <= SAME_BAND_PCT * SAME_BAND_TPS
        out["batch_like"] = abs(med - BATCH_LIKE_TPS) <= NEAR_PCT * BATCH_LIKE_TPS
        out["ms_per_step"] = 1000.0 / med
    if not ok:
        out["verdict"] = "INVALID_CELL"
        out["reasons"] = [why]
    elif out["quote"]["verdict"] != "QUOTABLE":
        out["verdict"] = "UNUSABLE"
        out["reasons"] = ["引用閘門判 %s：%s" % (out["quote"]["verdict"], "；".join(out["quote"]["reasons"]))]
    elif med is None:
        out["verdict"] = "REFUSE"
        out["reasons"] = ["讀不出 tg 的逐 rep 樣本"]
    elif spec["target"] is None:
        out["verdict"] = "CONTROL"
        out["reasons"] = ["對照臂（沒有判別句）：水準 %.3f t/s（%.1f ms/step）"
                          % (med, out["ms_per_step"])]
    elif out["near_anchor"]:
        out["verdict"] = "DISCRIMINATED"
        out["reasons"] = ["tg=%.3f 落在 %.1f（相對差 %.1f%% ≤ %.0f%%）"
                          % (med, ANCHOR_DEFAULT_TPS, abs(out["delta_vs_anchor_pct"]), NEAR_PCT * 100)]
    else:
        out["verdict"] = "NOT_NEAR"
        out["reasons"] = ["tg=%.3f 不落在 %.1f（相對差 %.1f%% > %.0f%%）"
                          % (med, ANCHOR_DEFAULT_TPS, abs(out["delta_vs_anchor_pct"]), NEAR_PCT * 100)]
    return out


def judge(root: str = ROOT, arms_spec: dict = None) -> dict:
    spec_all = arms_spec or ARMS
    arms = {n: judge_arm(root, n, s) for n, s in spec_all.items()}
    a, b = arms.get("A", {}), arms.get("B", {})
    out = dict(product="L20-10 格子判別（reps 孿生：delivery-reps7 vs delivery-ws192-reps7；n=3→7）",
               at=time.strftime("%F %T %z"), root=root,
               constants=dict(anchor_default_tps=ANCHOR_DEFAULT_TPS, near_pct=NEAR_PCT,
                              same_band_tps=SAME_BAND_TPS, same_band_pct=SAME_BAND_PCT,
                              batch_like_tps=BATCH_LIKE_TPS, ctrl_band=CTRL_BAND),
               arms=arms, decode_tps_median={n: x.get("decode_tps_median") for n, x in arms.items()},
               cell_note="A＝delivery-reps7（warm 64）；B＝delivery-ws192-reps7（gen 256／ws 192 ⇒ "
                         "計時段一樣 64）⇒ 唯一差異是起點；n=7（§2.5.4c 的孿生，只差 reps 3→7）")
    # 前置：兩臂都要可滿足、可引用、讀得到
    bad = [x for x in (a, b) if x.get("verdict") not in ("DISCRIMINATED", "NOT_NEAR", "CONTROL")]
    if bad:
        out.update(verdict="REFUSE", why="有一臂不成立（%s）：%s"
                   % ("、".join("%s=%s" % (x["arm"], x["verdict"]) for x in bad),
                      "；".join(r for x in bad for r in x.get("reasons", []))),
                   accept_met=False, falsify_triggered=False)
        # 診斷級（不是判決）：兩臂的**中位數差**仍記下來 —— 2026-10-01 的 n=7 輪，A 沒過引用
        # 閘門（全 rep 1.1197>1.10）但兩臂中位數只差 ~1%（n=3 時它讀 +4.8%）：沒有這個欄位，
        # 「效應是不是還在」就得靠人重算。標籤寫明它是診斷級，不可當判決。
        if a.get("decode_tps_median") and b.get("decode_tps_median"):
            dd = b["decode_tps_median"] - a["decode_tps_median"]
            out["diagnostic"] = dict(
                note="兩臂中位數差（**未過引用閘門時僅供診斷**，不是判決）",
                delta_tps=dd, delta_pct=dd / a["decode_tps_median"] * 100.0,
                spreads={n: x.get("spread") for n, x in arms.items()},
                samples={n: x.get("samples") for n, x in arms.items()}, reps={
                    n: len(x.get("samples") or []) for n, x in arms.items()})
        out["next"] = list(WINDOW_NEXT)
        out["history"] = _history(root)
        return out
    delta_tps = (b["decode_tps_median"] or 0) - (a["decode_tps_median"] or 0)
    delta_pct = delta_tps / (a["decode_tps_median"] or 1) * 100.0
    spread_ms = max(abs((x.get("spread") or 1.0) - 1.0) for x in (a, b)) * max(
        a.get("ms_per_step") or 0, b.get("ms_per_step") or 0)
    delta_ms = (a.get("ms_per_step") or 0) - (b.get("ms_per_step") or 0)
    out.update(delta_tps=delta_tps, delta_pct=delta_pct, delta_step_ms=delta_ms, spread_ms=spread_ms)
    if abs(delta_ms) <= spread_ms:
        out.update(verdict="REFUSE", why="|Δstep|=%.3f ms ≤ 兩臂散布 %.3f ms ⇒ 起點效應不可辨識（A4）"
                   % (abs(delta_ms), spread_ms), accept_met=False, falsify_triggered=False)
    elif b.get("near_anchor"):
        out.update(verdict="START_POINT",
                   why="B 落在 %.1f（起點是主因；Δ=%+.2f%% vs 對照）" % (ANCHOR_DEFAULT_TPS, delta_pct),
                   accept_met=True, falsify_triggered=False)
    elif b.get("in_same_band"):
        out.update(verdict="SAME_BAND",
                   why="B 與對照同帶（%.3f vs %.3f）⇒ falsify：**起點不是主因**"
                   % (b["decode_tps_median"], a["decode_tps_median"]),
                   accept_met=False, falsify_triggered=True)
    elif b.get("batch_like"):
        out.update(verdict="BATCH_LIKE",
                   why="B 落在 %.1f（batch 那一族的落點）—— 這一對裡出現它 = 這一輪量到別的東西，先查"
                   % BATCH_LIKE_TPS, accept_met=False, falsify_triggered=False)
    else:
        out.update(verdict="INCONCLUSIVE", why="B 兩邊都不是（%.3f）⇒ 未分離"
                   % (b["decode_tps_median"] or 0), accept_met=False, falsify_triggered=False)
    out["next"] = _next_of(out)
    out["history"] = _history(root)
    return out


def _next_of(v: dict) -> list:
    if v["verdict"] == "START_POINT":
        return ["起點是主因 ⇒ 把 delivery 的讀數一律標成「起點冷」；量測口徑寫進看板 L20-10 的結案",
                "重跑一次成對確認（同一格、乾淨窗）⇒ 判詞不變才結案"]
    if v["verdict"] == "SAME_BAND":
        return ["falsify 觸發：起點與 batch（B3 的 10.89 診斷級）都不是主因 ⇒ 看板 L20-10 註記"
                "「已試、兩者皆非主因」，力氣回 §56 的第三個候選（工作集／pool 行為）"]
    if v["verdict"] == "REFUSE":
        return list(WINDOW_NEXT)
    return ["判詞沒落地 ⇒ 檢查 %s" % v.get("why", "")]


def _history(root: str) -> dict:
    """前一輪（不可滿足的那一對）的判詞照抄進來，避免「換了格就忘了舊判決」。"""
    p = os.path.join(root, PRODUCT_DIR, "verdict_b1b3_2026-09-30.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                old = json.load(fh)
            return dict(at=old.get("at"), verdict=old.get("verdict"), why=old.get("why"),
                        decode_tps_median=old.get("decode_tps_median"))
        except Exception:  # noqa: BLE001
            pass
    return dict(verdict="INVALID_CELL（B1 的格不可滿足；B3 被窗口擋）",
                note="舊判詞見 docs/L2010_B1B3_2026-09-30.md")


def report(v: dict) -> None:
    print("L20-10 判詞 v2（可滿足的起點格）")
    print("  判別句：B 落在 %.1f（±%.0f%%）⇒ START_POINT ｜ 落在 %.1f±%.0f%% ⇒ SAME_BAND（falsify）"
          % (ANCHOR_DEFAULT_TPS, NEAR_PCT * 100, SAME_BAND_TPS, SAME_BAND_PCT * 100))
    print("  兩臂的權威 row 逐字相同（p0/n64）⇒ 唯一差異是起點（%s）" % v.get("cell_note", ""))
    print()
    print("  %-3s %-16s %-16s %-10s %-9s %-10s %s" %
          ("arm", "cell", "shape", "tg median", "ms/step", "引用", "verdict"))
    for n, x in v["arms"].items():
        d = x["declared"]
        print("  %-3s %-16s gen=%-3s ws=%-4s %-10s %-9s %-10s %s"
              % (n, x["cell"], d.get("gen"), d.get("warm_skip"),
                 ("%.3f" % x["decode_tps_median"]) if x.get("decode_tps_median") else "—",
                 ("%.1f" % x["ms_per_step"]) if x.get("ms_per_step") else "—",
                 x.get("quote", {}).get("verdict", "—"), x["verdict"]))
        for r in x.get("reasons", []):
            print("        ↳ %s" % r)
    if v.get("delta_pct") is not None:
        print("\n  Δ = %+.2f%%（%.2f ms/step；散布 %.2f ms；%s）"
              % (v["delta_pct"], v.get("delta_step_ms", 0), v.get("spread_ms", 0),
                 "可辨識" if abs(v.get("delta_step_ms", 0)) > v.get("spread_ms", 0) else "不可辨識"))
    if v.get("diagnostic"):
        d = v["diagnostic"]
        print("\n  診斷級（不可當判決）：兩臂中位數差 = %+.2f%%；散布 A/B = %s／%s（reps %s）"
              % (d["delta_pct"], d["spreads"].get("A"), d["spreads"].get("B"), d.get("reps")))
    print("\nVERDICT: %s -- %s" % (v["verdict"], v["why"]))
    print("  accept 成立：%s ｜ falsify 觸發：%s" % (v["accept_met"], v["falsify_triggered"]))
    if v.get("history"):
        print("  前一輪（不可滿足的那一對）：%s -- %s" % (v["history"].get("verdict"), v["history"].get("at")))
    print("  下一步：")
    for x in v.get("next", []):
        print("    · %s" % x)


def selftest() -> int:
    import statistics
    import tempfile

    ok = [0, 0]

    def case(name, cond, detail=""):
        ok[1] += 1
        ok[0] += 1 if cond else 0
        print("  %-62s -> %s%s" % (name, "PASS" if cond else "FAIL", "" if cond else "  " + str(detail)))

    case("ARMS 指向 §2.5.4c 的 reps 孿生（A／B 格名與 declared.reps=7）",
         ARMS["A"]["cell"] == "delivery-reps7" and ARMS["B"]["cell"] == "delivery-ws192-reps7"
         and ARMS["A"]["declared"].get("reps") == 7 and ARMS["B"]["declared"].get("reps") == 7)
    case("A1: ws=192/gen=256 ⇒ 可滿足（計時段 64）", feasible(dict(gen=256, warm_skip=192))[0] is True)
    case("A1: ws=256/gen=128 ⇒ 不可滿足（舊格，計時段 −128）",
         feasible(dict(gen=128, warm_skip=256))[0] is False)

    def _mk(root, name, tps, samples=None, attrib="none", cell="delivery", ws=64, applied=True,
            gen=128, n_gen=64):
        # 注意：cell.gen／cell.warm_skip 要真的跟格一致 —— R8b 會用 `gen − warm_skip` 算
        # 「這一格宣告的行」，寫錯就會被自己的引用閘門擋下（fixture 也要誠實）。
        samples = samples or [tps] * 3
        prod = dict(tag="prod-new", profile="prod-new", warm_skip=ws, warm_skip_applied=applied,
                    cell=dict(named_cell=cell, prompt=0, gen=gen, depths=512, reps=3, warm_skip=ws),
                    attribution=dict(verdict=attrib, thermal_worst="NOMINAL"),
                    contract=dict(ok=True, cell=cell),
                    rows=[dict(n_prompt=0, n_gen=n_gen, avg_ts=tps, stddev_ts=statistics.pstdev(samples),
                               samples_ts=samples)])
        os.makedirs(os.path.join(root, PRODUCT_DIR), exist_ok=True)
        p = os.path.join(root, PRODUCT_DIR, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump([prod], fh)
        return p

    with tempfile.TemporaryDirectory() as td:
        # 真形狀：A（delivery）10.23、B（ws192）11.55 且窗口乾淨 ⇒ START_POINT
        _mk(td, "ws192_A.json", 10.23, [10.20, 10.23, 10.26])
        _mk(td, "ws192_B.json", 11.55, [11.52, 11.55, 11.58], cell="delivery-ws192", ws=192, gen=256)
        v = judge(td)
        case("B 落在 11.5、兩臂可引、Δ 可辨識 ⇒ START_POINT（accept 成立）",
             v["verdict"] == "START_POINT" and v["accept_met"] is True, (v["verdict"], v.get("delta_pct")))
        case("Δ 印得出來且為正（起點較熱 ⇒ 較快）", v.get("delta_pct", 0) > 10.0, v.get("delta_pct"))
        case("history 會帶上舊判詞（不可滿足那一對）", v.get("history", {}).get("verdict") is not None)

        # B 與對照同帶 ⇒ SAME_BAND（falsify）
        _mk(td, "ws192_B.json", 10.30, [10.28, 10.30, 10.32], cell="delivery-ws192", ws=192, gen=256)
        v = judge(td)
        case("B 落在 10.5±5%（≈ 對照）⇒ SAME_BAND、falsify 觸發",
             v["verdict"] == "SAME_BAND" and v["falsify_triggered"] is True, v["verdict"])
        case("SAME_BAND 的 next 指向「兩者皆非主因」",
             any("皆非主因" in x for x in v.get("next", [])), v.get("next"))

        # B 落 9.7 ⇒ BATCH_LIKE（先查）
        _mk(td, "ws192_B.json", 9.70, [9.68, 9.70, 9.72], cell="delivery-ws192", ws=192, gen=256)
        case("B 落在 9.7 ⇒ BATCH_LIKE（量到別的東西，先查）", judge(td)["verdict"] == "BATCH_LIKE")

        # 效應小於散布 ⇒ REFUSE（不可辨識）
        _mk(td, "ws192_A.json", 10.50, [10.20, 10.50, 10.80])
        _mk(td, "ws192_B.json", 10.60, [10.58, 10.60, 10.62], cell="delivery-ws192", ws=192, gen=256)
        case("|Δstep| ≤ 兩臂散布 ⇒ REFUSE（不可辨識，不是「不是主因」）",
             judge(td)["verdict"] == "REFUSE", judge(td)["why"])

        # 窗口不乾淨 ⇒ REFUSE
        _mk(td, "ws192_A.json", 10.23, [10.20, 10.23, 10.26])
        _mk(td, "ws192_B.json", 11.55, [11.52, 11.55, 11.58], attrib="thermal", cell="delivery-ws192", ws=192,
            gen=256)
        v = judge(td)
        case("B 臂窗口 thermal ⇒ REFUSE（不判 START_POINT／SAME_BAND）",
             v["verdict"] == "REFUSE" and "U" in v["why"], v["why"][:60])

        # 窗口不乾淨 ⇒ 即使兩臂的 level 已經在帶內，也只能 REFUSE（且必須印下一步）
        _mk(td, "ws192_A.json", 10.86, [10.19, 11.05, 10.86])
        _mk(td, "ws192_B.json", 10.57, [10.36, 10.86, 10.57], attrib="swap", cell="delivery-ws192",
            ws=192, gen=256)
        v = judge(td)
        case("兩臂同帶但窗口 swap ⇒ REFUSE（不是 SAME_BAND）且 next 給出結構性出路",
             v["verdict"] == "REFUSE" and v.get("next"), v.get("next"))
        case("有臂不成立時仍記下診斷級中位數差（不冒充判決）",
             v.get("diagnostic", {}).get("delta_pct") is not None
             and "診斷" in v["diagnostic"]["note"], v.get("diagnostic"))
        case("REFUSE 的 next 不再叫人「重開機」（2026-10-01 實測把那一條否證掉）",
             not any("清窗＝重開機" in x or x.startswith("先讓窗口乾淨") for x in v.get("next", []))
             and any("重開機**不是**解" in x for x in v.get("next", [])),
             v.get("next"))
        case("REFUSE 的 next 不再把池子當加項（operator 2026-10-01 更正的那一條）",
             not any("池子 8192 ＋ Metal 駐留" in x for x in v.get("next", []))
             and any("不可以再加一次" in x for x in v.get("next", [])),
             v.get("next"))
        case("REFUSE 的 next 記著 operator 的否證式結案（2026-10-01 明文，排除）",
             any("否證式結案" in x for x in v.get("next", []))
             and any("不是把 `REFUSE` 改寫成否証" in x for x in v.get("next", [])),
             v.get("next"))
        case("REFUSE 的 next 不再叫人改閘門救這一格（改革另立前瞻案）",
             not any("下一手是 operator 的選擇" in x for x in v.get("next", []))
             and any("另一個前瞻案" in x for x in v.get("next", [])),
             v.get("next"))

        # 不可滿足的格 ⇒ INVALID_CELL（護欄還在）
        _mk(td, "ws192_B.json", 11.55, [11.52, 11.55, 11.58], cell="delivery-ws256", ws=256, applied=False,
            n_gen=128)
        spec = {k: dict(x) for k, x in ARMS.items()}
        spec["B"]["declared"] = dict(gen=128, warm_skip=256, prompt=0, batch=512)
        case("B 的格不可滿足 ⇒ INVALID_CELL（A1 護欄）",
             judge(td, spec)["arms"]["B"]["verdict"] == "INVALID_CELL")

    print("== selftest %d/%d ==" % (ok[0], ok[1]))
    return 0 if ok[0] == ok[1] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L20-10 判詞（可滿足的起點格）")
    ap.add_argument("--check", action="store_true", help="只印判詞（預設）")
    ap.add_argument("--write", action="store_true", help="寫 verdict 檔（依 --pair 決定檔名）")
    ap.add_argument("--pair", choices=("dirty", "clean"), default="dirty",
                    help="dirty＝已跑的髒窗那一對；clean＝清窗後那一對（檔名不同，不混用）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    spec = arms_for_pair(args.pair)
    v = judge(args.root, spec)
    v["pair"] = args.pair
    report(v)
    if args.write:
        rel = PRODUCT_DIR + "/" + PAIR_FILES[args.pair]["verdict"]
        p = os.path.join(args.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(v, fh, ensure_ascii=False, indent=1)
        print("\n判詞落檔：%s" % rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
