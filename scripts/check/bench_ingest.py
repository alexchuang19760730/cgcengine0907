#!/usr/bin/env python3
"""bench_ingest.py -- 把「直跑」的量測補成 harness 門口徑的可入閘產物，並拒絕假裝。

WHY THIS EXISTS
---------------
`harness bench` 的產物契約 = raw matrix JSON + harness 注入的
`base_check` / `cell` / `box_gate` / `charter` / `sys_before` / `sys_after` / `sys_rate`。
2026-09-27 11:15 之後那批 prod-new off 量測跑在 `llama_bench_matrix.py` 上（`/tmp/mtp_std/off*.json`）：
cell 校驗、reps、thermal/swap 讀數都是真的，但少了那一層注入 ⇒
`mtp_promotion_gate.py` 的規則 1（cell 一致）拿不到 cell、報告也指不出 artifact，
於是那批數字只能靠人手複述，而人手複述正是這一輪在修的那個失效模式。

本工具照**閘自己的定義**補欄位，並把「這是補的、不是量的」寫死在產物裡：

  * `base_check` / `cell`：用 harness 的謂詞重算（`import harness`，不複寫規則）
  * `box_gate` / `charter` / `sys_*`：直跑裡**不存在** ⇒ 標 `synthesized` 或 null + 原因，
    不偽造成「閘跑過」。窗級宣稱要用這一層的人，看得到它不在。
  * 每個 sample 的 verdict（accepted / variant / rejected）與理由都進產物；
    `--strict` 只要有 rejected 就非 0（fail-closed），預設只記錄、不擋。

拒絕規則（全部可申報、可重算，沒有一條是「看起來怪」）
-----------------------------------------------------
  r_rows      沒有 rows，或缺 pp 列（n_prompt>0）／tg 列（n_prompt==0, n_gen>0）
  r_contract  matrix 自己的 cell 口徑校驗不是 ok
  r_incomplete / r_error   arm 半途死掉（OOM/abort）
  r_head      engine_build != --head（不同 build 的數字不是同一個盒子）
  r_thermal   thermal.worst.label != NOMINAL（降頻的數字不是窗級樣本）
  r_cell      15 維 cell 與 baseline 不一致（維度對不上就不是同一個 cell）
  r_wall      wall_s > --wall-s-max（窗級崩壞：這個 cell 正常 77-111 s，今天 14:12 那次 997 s）
  r_floor     任一軸低於 frozen anchor 的 --sanity-floor 倍（預設 0.6）⇒ 盒況降級樣本
  variant     extra_env 非空 ⇒ 不是 canonical off 樣本（照記錄、不進 canonical 統計）

用法
----
    python3 scripts/check/bench_ingest.py \
        --product /tmp/mtp_std/off.json \
        --product /tmp/mtp_std/off_dirty.json \
        --product /tmp/mtp_std/off_after_on.json \
        --out Backup/mtp_off_corridor/off_corridor_20260927.json \
        --summary-out Backup/mtp_off_corridor/off_corridor_20260927.summary.json

    # 產品規格 PATH[:runner[:session]]；runner ∈ auto|matrix-direct|harness-bench，預設 auto
    # auto = 有 base_check+box_gate+cell 三件套就當 harness-bench，否則 matrix-direct
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import harness as _h  # noqa: E402  閘的謂詞只准有一個實作：注入用它算，不複寫。

BASELINE_DEFAULT = HERE / "mtp_off_baseline.json"

SANITY_FLOOR = 0.6      # frozen anchor 的比例地板；今天被擋下的那個樣本是 0.17x/0.31x
WALL_S_MAX = 240.0      # 這個 cell 正常 77-111 s；997 s 那次是窗級崩壞
RUNNERS = ("auto", "matrix-direct", "harness-bench")

# --- 健康判準（citation-grade；樣本 verdict 管「是不是同一種窗」，這裡管「能不能拿來宣稱」）---
# 今天量到的分佈（9 個同 cell run、27 個 rep）：
#   * 同臂三個 rep 的 spread：乾淨態 0.5-2.4%（兩個 rep 互差 ≤1%），混合態 3.0-4.7%（一個 rep 掉 5-8%）
#   * thermal sampler 覆蓋率 thermal.n*interval/wall：正常 0.91-0.97、冷窗 0.74、崩壞 0.12
#   * 連續跑（無冷卻）第 3 支起 thermal 出現 MODERATE/HEAVY，pp 單調掉 14.8%
REP_SPREAD_CLEAN = 0.025    # <= 2.5% = tight（三個 rep 一致）
OUTLIER_GAP_MIN = 0.03      # 其中一個 rep 比同伴中位低 >= 3% = bimodal（那一次跑被撞到）；否則 spread
COVERAGE_MIN = 0.90         # 監測執行緒被餓死＝整個盒況壞掉，不只是數字差
WALL_FACTOR_MAX = 1.3       # 對同批健康樣本的 wall 中位數的倍率
SEGMENT_GAP_MIN = 10.0      # 間隔 <= 10 分鐘算同一個窗（今天：<=7 分鐘內 run 中位數 1σ≈1.4%）
HOT_LABELS = ("MODERATE", "HEAVY")


# --------------------------------------------------------------------------- 讀檔
def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _load_json(path: Path):
    return json.loads(path.read_text())


def _rows(entry: dict) -> list[dict]:
    return [r for r in (entry.get("rows") or []) if isinstance(r, dict)]


def pp_tg(entry: dict) -> tuple[dict | None, dict | None]:
    pp = next((r for r in _rows(entry) if (r.get("n_prompt") or 0) > 0), None)
    tg = next((r for r in _rows(entry)
               if (r.get("n_prompt") or 0) == 0 and (r.get("n_gen") or 0) > 0), None)
    return pp, tg


def detect_runner(entry: dict) -> str:
    """harness bench 的注入痕跡：base_check + box_gate + cell 同時在。"""
    return "harness-bench" if all(k in entry for k in ("base_check", "box_gate", "cell")) \
        else "matrix-direct"


def session_of(entry: dict, mtime: float) -> tuple[str, str]:
    """session stamp。矩陣產物沒有 harness 的時間戳，拿 rows[].test_time (UTC) 換算 +0800。"""
    times = [r.get("test_time") for r in _rows(entry) if r.get("test_time")]
    fallback = dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M+0800")
    if not times:
        return fallback, "file mtime (product has no row test_time)"
    try:
        t = min(dt.datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ") for x in times)
    except ValueError:
        return fallback, "file mtime (unparsable row test_time)"
    return (t + dt.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M+0800"), \
        "rows[].test_time (UTC) +0800"


# --------------------------------------------------------------------------- cell
def derive_cell15(entry: dict, pp: dict | None, tg: dict | None) -> tuple[dict, dict, list[str]]:
    """把矩陣產物能證明的 15 維還原出來；每一維都附出處，推不出來的就拒收。"""
    scalars = entry.get("scalars") or {}
    src: dict[str, str] = {}
    problems: list[str] = []

    def put(key, value, source):
        if value is None:
            problems.append(f"{key}: 推不出來（{source}）")
            return
        src[key] = source
        return value

    model = scalars.get("MODEL") or (pp or {}).get("model_filename") or (tg or {}).get("model_filename")
    rel = None
    if model:
        try:
            rel = str(Path(model).resolve().relative_to(ROOT))
        except ValueError:
            rel = model
    ref = pp or tg or {}
    samples = ref.get("samples_ts") or ref.get("samples_ns") or []
    cell = {
        "model": put("model", rel, "scalars.MODEL（相對 repo 根）"),
        "ngl": put("ngl", int(scalars["NGL"]) if scalars.get("NGL") not in (None, "") else None,
                   "scalars.NGL"),
        "load_mode": put("load_mode", scalars.get("LOAD_MODE") or ref.get("load_mode"),
                         "scalars.LOAD_MODE / row.load_mode"),
        "threads": put("threads", ref.get("n_threads"), "row.n_threads"),
        "batch": put("batch", ref.get("n_batch"), "row.n_batch"),
        "ubatch": put("ubatch", ref.get("n_ubatch"), "row.n_ubatch"),
        "prompt": put("prompt", (pp or {}).get("n_prompt"), "pp row.n_prompt"),
        "gen": put("gen", _h._BENCH_DEFAULTS["gen"],
                   "harness._BENCH_DEFAULTS.gen（矩陣的 tg 列 n_gen 是深度切分後的 64，"
                   "CLI 的 --gen 不進產物 ⇒ 這一維是宣告值，不是讀數）"),
        "depths": put("depths", int((tg or {}).get("n_depth")) if (tg or {}).get("n_depth") else None,
                      "tg row.n_depth（baseline 的 cell.depths 是 int，harness 的 cell 是 str——"
                      "同一個維度兩種形狀，這裡統一成 int 比對）"),
        "reps": put("reps", len(samples) or None, "len(row.samples_ts)"),
        "warm_skip": put("warm_skip", entry.get("warm_skip"), "entry.warm_skip"),
        "ctx_size": entry.get("ctx_size") if entry.get("ctx_size") is not None else 0,
        "fixed_fill_seed": put("fixed_fill_seed", entry.get("fixed_fill_seed"),
                               "entry.fixed_fill_seed"),
    }
    cell["ctx_size"] = put("ctx_size", cell.get("ctx_size"), "entry.ctx_size（None ⇒ 0）")
    return {k: v for k, v in cell.items() if v is not None}, src, problems


def harness_cell_shape(entry: dict, cell15: dict) -> dict:
    """harness `_cell(args)` 的形狀（11 鍵），由同一支函式產生 ⇒ 不複寫 shape。"""
    ns = argparse.Namespace(
        prompt=cell15.get("prompt"), gen=cell15.get("gen"), depths=str(cell15.get("depths")),
        reps=cell15.get("reps"), warm_skip=cell15.get("warm_skip"),
        ctx_size=cell15.get("ctx_size"), fixed_fill_seed=cell15.get("fixed_fill_seed"),
        prompt_file="", warmup=True, spec_type=entry.get("spec_type") or "",
        spec_draft_n_max=entry.get("spec_draft_n_max"))
    return _h._cell(ns)


def recompute_base_check(entry: dict) -> dict:
    profile = entry.get("profile") or "prod-new"
    matrix = _h._load("matrix", "llama_bench_matrix.py")
    arm = matrix.resolve(profile, dict(entry.get("extra_env") or {}))
    ok, diffs, ovr = _h._base_gate(profile, arm, set())
    armed, _missing = _h._swap_arm_gate(profile, arm.get("env", {}), set())
    return {
        "profile": profile, "pass": bool(ok and armed), "overrides": ovr, "diffs": diffs,
        "swap_arms": {k: arm.get("env", {}).get(k) for k in _h._SWAP_ARM_KEYS},
        "recomputed_by": "bench_ingest.py -> harness._base_gate/_swap_arm_gate；"
                         "矩陣直跑沒記下 arm 的 ! 宣告 ⇒ overrides 只含實驗開關白名單",
    }


# --------------------------------------------------------------------------- 判定
CONTRACT_KEYS = ("base_check", "cell", "box_gate", "charter", "sys_before", "sys_after", "sys_rate")
GAP_HARD = ("base_check", "cell", "box_gate")   # 缺這三件就沒有可比性，只能拒收


def _gap_fill(key: str, entry: dict, cell15: dict) -> object:
    """佔位不是讀數：補進來的每個欄位都自己說「我不是那次跑量的」。"""
    if key == "base_check":
        bc = recompute_base_check(entry)
        bc["recomputed_by"] += "（產物原本缺 base_check ⇒ 這是補件，不是那次跑的注入）"
        return bc
    if key == "cell":
        c = harness_cell_shape(entry, cell15)
        return {**c, "_gap": "產物原本缺 cell；由矩陣產物的欄位重建"}
    if key == "box_gate":
        return {"synthesized": True, "synthesized_by": "bench_ingest.py",
                "why": "產物裡沒有 box_gate ⇒ 起跑時的盒況閘沒有紀錄",
                "compressor": {"quiet": None, "reason": "not recorded"},
                "swap_stock_label": None}
    if key == "charter":
        return {"gate": "not_recorded",
                "why": "產物裡沒有 charter 區塊（harness 版本較舊或路徑不同）；不等於 waived 或 passed"}
    return None


def _norm(v):
    """'512' 與 512 是同一個 cell 維度；形狀不同不是漂移。"""
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return v


def judge(entry: dict, runner: str, head: str, baseline: dict,
          wall_max: float, floor: float) -> tuple[dict, dict]:
    """回 (shaped_entry, sample)。所有規則只看產物自己的欄位。"""
    reasons: list[str] = []
    pp, tg = pp_tg(entry)
    if pp is None or tg is None:
        reasons.append("r_rows: 缺 pp 列（n_prompt>0）或 tg 列（n_prompt==0, n_gen>0）")
    if (entry.get("contract") or {}).get("ok") is not True:
        reasons.append(f"r_contract: cell 口徑校驗沒過 {json.dumps(entry.get('contract'), ensure_ascii=False)}")
    if entry.get("incomplete"):
        reasons.append("r_incomplete: arm 沒跑完（矩陣標 incomplete）")
    if entry.get("error"):
        reasons.append(f"r_error: {entry['error']}")
    if entry.get("engine_build") != head:
        reasons.append(f"r_head: engine_build={entry.get('engine_build')!r} != {head!r}")
    th_worst = ((entry.get("thermal") or {}).get("worst") or {}).get("label")
    if th_worst != "NOMINAL":
        reasons.append(f"r_thermal: thermal.worst={th_worst!r} != 'NOMINAL'")

    cell15, cell_src, cell_problems = derive_cell15(entry, pp, tg)
    if cell_problems:
        reasons.append("r_cell: " + "; ".join(cell_problems))
    else:
        for k, want in baseline["cell"].items():
            if _norm(cell15.get(k)) != _norm(want):
                reasons.append(f"r_cell: {k} got={cell15.get(k)!r} want={want!r}")

    wall = entry.get("wall_s")
    if wall is None or wall > wall_max:
        reasons.append(f"r_wall: wall_s={wall} > {wall_max} s（窗級崩壞，不是同一種窗）")

    pp_ts = (pp or {}).get("avg_ts")
    tg_ts = (tg or {}).get("avg_ts")
    pp_floor = floor * baseline["measured"]["pp_t_s"]
    tg_floor = floor * baseline["measured"]["tg_t_s"]
    if pp_ts is not None and pp_ts < pp_floor:
        reasons.append(f"r_floor: pp {pp_ts:.2f} < {pp_floor:.2f} t/s "
                       f"({floor:.0%} of frozen {baseline['measured']['pp_t_s']})")
    if tg_ts is not None and tg_ts < tg_floor:
        reasons.append(f"r_floor: tg {tg_ts:.2f} < {tg_floor:.2f} t/s "
                       f"({floor:.0%} of frozen {baseline['measured']['tg_t_s']})")

    variant = bool(entry.get("extra_env"))
    session, session_src = session_of(entry, 0)
    shaped = dict(entry)
    if runner == "matrix-direct":
        shaped["base_check"] = recompute_base_check(entry)
        shaped["cell"] = harness_cell_shape(entry, cell15)
        shaped["box_gate"] = {
            "synthesized": True,
            "synthesized_by": "bench_ingest.py",
            "why": "matrix-direct 跑：起跑時的 thermal 冷卻/壓縮機安靜度**沒有**被閘取樣，"
                   "這裡只有那次跑自己的 thermal/memory 讀數。要引 window 級的宣稱，"
                   "用 harness.py bench 重跑，不要用這個欄位。",
            "thermal": (entry.get("thermal") or {}).get("launch"),
            "thermal_worst": (entry.get("thermal") or {}).get("worst"),
            "compressor": {"quiet": None,
                           "reason": "not sampled：矩陣直跑的起跑閘沒被這條路徑取樣"},
            "swap_stock_label": None,
        }
        shaped["charter"] = {"gate": "not_applied", "runner": "matrix-direct",
                             "why": "矩陣直跑沒有跑前立項閘；不要把它讀成 waived 或 passed"}
        shaped["sys_before"] = None
        shaped["sys_after"] = None
        shaped["sys_rate"] = None
    # 產物契約是「每臂都有」，不是「應該有」：缺件先補佔位並記下來，
    # 而 base_check/cell/box_gate 這三件缺了就等於無法比對 ⇒ 直接拒收（fail-closed）。
    missing = [k for k in CONTRACT_KEYS if k not in shaped]
    for k in missing:
        shaped[k] = _gap_fill(k, entry, cell15)
    hard = [k for k in missing if k in GAP_HARD]
    if hard:
        reasons.append(f"r_shape: 產物缺 {', '.join(hard)}（補的是佔位，不是讀數）")
    shaped["cell_gate"] = cell15
    shaped["cell_gate_source"] = cell_src

    verdict = "rejected" if reasons else ("variant" if variant else "accepted")
    shaped["ingest"] = {
        "by": "scripts/check/bench_ingest.py",
        "runner": runner,
        "sample_verdict": verdict,
        "reject_reasons": reasons,
        "contract_gaps": missing,
        "session": session,
        "session_source": session_src,
    }

    mem = entry.get("memory") or {}
    sample = {
        "session": session,
        "session_source": session_src,
        "runner": runner,
        "verdict": verdict,
        "reasons": reasons,
        "tag": entry.get("tag"),
        "build": entry.get("engine_build"),
        "wall_s": wall,
        "pp_t_s": pp_ts, "pp_sd": (pp or {}).get("stddev_ts"),
        "tg_t_s": tg_ts, "tg_sd": (tg or {}).get("stddev_ts"),
        "reps": len((tg or {}).get("samples_ts") or []),
        "samples_ts": (tg or {}).get("samples_ts"),
        "thermal_worst": th_worst,
        "extra_env": entry.get("extra_env") or {},
        "swap_launch_mib": (mem.get("launch") or {}).get("swap_used_mb"),
        "swap_worst_mib": (mem.get("worst") or {}).get("max_swap_mb"),
        "artifact": None,          # 由呼叫者填（來源產品路徑）
        "health": health_of(entry, verdict),
    }
    shaped["health"] = sample["health"]
    return shaped, sample


# --------------------------------------------------------------------------- 統計
def rep_shape(entry: dict) -> dict:
    """同一個 arm 的三個 rep 是什麼形狀 —— 今天主因就在這一維。"""
    _pp, tg = pp_tg(entry)
    reps = [float(x) for x in ((tg or {}).get("samples_ts") or [])]
    if len(reps) < 2:
        return {"shape": "unknown", "spread_pct": None, "reps": reps, "median": None,
                "low_rep": None, "low_rep_gap_pct": None, "outlier_gap_pct": None,
                "agreeing_pair_spread_pct": None, "note": "不到兩個 rep，沒得比"}
    med = st.median(reps)
    spread = (max(reps) - min(reps)) / med if med else float("inf")
    low = min(reps)
    peers = sorted(r for r in reps if r != low) or [med]
    peer_med = st.median(peers)
    # 三種形狀：tight（三個一致）/ bimodal（一個 rep 明顯掉下去）/ spread（均勻散開，沒有單一元兇）
    outlier = (peer_med - low) / peer_med if peer_med else 0.0
    if spread <= REP_SPREAD_CLEAN:
        shape, note = "tight", "三個 rep 一致"
    elif outlier >= OUTLIER_GAP_MIN:
        shape = "bimodal"
        note = (f"兩個 rep 互差 {abs(reps[0]-reps[2])/med*100:.1f}% 而其中一個掉 "
                f"{outlier*100:.1f}% ⇒ 那一次跑裡有一個 rep 被撞到，不是整窗變慢")
    else:
        shape, note = "spread", "三個 rep 均勻散開（沒有單一被撞到的 rep），中位數是平均值而非模態"
    agree = (max(peers) - min(peers)) / med if len(peers) > 1 else None
    return {"shape": shape, "spread_pct": round(spread * 100, 2), "reps": reps,
            "median": round(med, 3), "low_rep": low,
            "low_rep_gap_pct": round((med - low) / med * 100, 2),
            "outlier_gap_pct": round(outlier * 100, 2),
            "agreeing_pair_spread_pct": round(agree * 100, 2) if agree is not None else None,
            "note": note}


def health_of(entry: dict, sample_verdict: str) -> dict:
    """宣告式健康判準；每個檢查都回 ok/warn/fail 與它的讀數。"""
    th = entry.get("thermal") or {}
    hist = th.get("hist") or {}
    hot = sum(int(v) for k, v in hist.items() if k in HOT_LABELS)
    th_worst = (th.get("worst") or {}).get("label")
    wall = entry.get("wall_s") or 0
    n = th.get("n")
    iv = th.get("interval_s")
    cov = (n * (iv or 0.5) / wall) if (n and wall) else None
    rs = rep_shape(entry)
    checks = {
        "identity": {"sev": "ok" if sample_verdict in ("accepted", "variant") else "fail",
                     "read": sample_verdict,
                     "why": "head/cell/收尾規則都過了才算同一個盒子"},
        "thermal": {"sev": "fail" if th_worst not in (None, "NOMINAL") else
                            ("warn" if hot else "ok"),
                    "read": {"worst": th_worst, "hot_samples": hot,
                             "hist": hist},
                    "why": "worst 是啟動前讀的標籤；跑內熱起來要看 hist（連續跑到第 3 支就會出現 HEAVY）"},
        "coverage": {"sev": ("fail" if cov < COVERAGE_MIN else "ok") if cov is not None else "warn",
                     "read": round(cov, 3) if cov is not None else None,
                     "threshold": COVERAGE_MIN,
                     "why": ("thermal.n*interval/wall；取樣執行緒被餓死 ⇒ 整台機器在 stall，"
                             "不只是這一臂慢") if cov is not None else
                            "產物沒有 sampler 讀數（缺件，不當 0；舊格式產物會落在這裡）"},
        "rep_shape": {"sev": "ok" if rs["shape"] == "tight" else ("fail" if rs["shape"] == "unknown" else "warn"),
                      "read": {k: rs[k] for k in ("shape", "spread_pct", "outlier_gap_pct",
                                                  "agreeing_pair_spread_pct")},
                      "threshold_pct": REP_SPREAD_CLEAN * 100,
                      "why": rs["note"]},
        "wall": {"sev": "pending", "read": wall,
                 "threshold_factor": WALL_FACTOR_MAX,
                 "why": "同批健康樣本的中位 wall 映出的倍率；要用到它得等統計跑完（見 --out 產物）"},
    }
    return {"checks": checks, "verdict": _health_verdict(checks)}


def _health_verdict(checks: dict) -> str:
    sevs = [c["sev"] for c in checks.values()]
    if "fail" in sevs:
        return "sick"
    return "mixed" if "warn" in sevs else "clean"


def segments(samples: list[dict], gap_min: float = SEGMENT_GAP_MIN) -> list[dict]:
    """把樣本按時間分成「窗」。同一個窗＝相鄰樣本間隔 <= gap_min。

    今天是這兩個尺度：<=7 分鐘背對背 4 支（run 中位數 1σ≈1.4%）、跨 95 分鐘 5 支（3.6%）。
    """
    def t_of(s):
        try:
            return dt.datetime.strptime(s["session"], "%Y-%m-%dT%H:%M%z")
        except ValueError:
            return None
    pts = [s for s in samples if t_of(s)]
    pts.sort(key=t_of)
    out, cur = [], []
    for s in pts:
        if cur and (t_of(s) - t_of(cur[-1])).total_seconds() > gap_min * 60:
            out.append(cur); cur = []
        cur.append(s)
    if cur:
        out.append(cur)
    segs = []
    for g in out:
        acc = [s for s in g if s["verdict"] == "accepted"]
        tg = [s["tg_t_s"] for s in acc if s.get("tg_t_s")]
        reps = [s["tg_sd"] for s in acc if s.get("tg_sd")]
        segs.append({
            "start": min(s["session"] for s in g), "end": max(s["session"] for s in g),
            "span_min": round((t_of(g[-1]) - t_of(g[0])).total_seconds() / 60, 1),
            "n": len(g), "n_accepted": len(acc),
            "tg_median_t_s": round(st.median(tg), 3) if tg else None,
            "tg_spread_pct": round((max(tg) - min(tg)) / st.median(tg) * 100, 2) if len(tg) > 1 else None,
            "rep_spread_pct_median": (round(st.median(rs), 2) if (rs := [s["health"]["checks"]["rep_shape"]
                                                                        ["read"]["spread_pct"] for s in g
                                                                    if s.get("health") and
                                                                    s["health"]["checks"]["rep_shape"]
                                                                    ["read"]["spread_pct"] is not None]) else None),
            "sessions": [s["session"] for s in g],
            "verdicts": [s["verdict"] for s in g],
        })
    return segs


def stats_of(samples: list[dict]) -> dict:
    acc = [s for s in samples if s["verdict"] == "accepted"]
    if not acc:
        return {"n_accepted": 0}

    def axis(key, sd_key=None):
        vals = [s[key] for s in acc if s.get(key) is not None]
        if not vals:
            return {}
        med = st.median(vals)
        out = {"median": round(med, 3), "min": round(min(vals), 3), "max": round(max(vals), 3),
               "spread_pct": round((max(vals) - min(vals)) / med * 100, 2),
               "n": len(vals)}
        if len(vals) > 1:
            out["sd"] = round(st.stdev(vals), 3)
        if sd_key:
            sds = [s[sd_key] for s in acc if s.get(sd_key) is not None]
            if sds:
                out["mean_within_run_sd"] = round(st.mean(sds), 3)
        return out

    return {"n_accepted": len(acc), "tg": axis("tg_t_s", "tg_sd"), "pp": axis("pp_t_s", "pp_sd"),
            "wall_s": axis("wall_s")}


# --------------------------------------------------------------------------- selftest
def _synth_entry(baseline: dict, **kw):
    model = str(ROOT / baseline["cell"]["model"])
    row_common = {"n_batch": 5632, "n_ubatch": 5632, "n_threads": 8, "n_gpu_layers": 99,
                  "type_k": "q8_0", "type_v": "q8_0", "load_mode": "none",
                  "model_filename": model, "test_time": "2026-09-27T03:15:56Z",
                  "samples_ts": [292.2, 294.1, 292.2], "n_kept": 2}
    entry = {
        "tag": "prod-new", "profile": "prod-new", "extra_env": {},
        "batch": "5632", "ubatch": "5632", "wall_s": 76.6,
        "scalars": {"PROFILE": "prod-new", "MODEL": model, "CTX": "8192",
                    "BUDGET": "8589934592", "NGL": "99", "LOAD_MODE": "none",
                    "BATCH": "5632", "UBATCH": "5632"},
        "contract": {"ok": True, "mismatches": [], "declared": []},
        "incomplete": False, "error": None,
        "thermal": {"launch": {"label": "NOMINAL", "t": "11:15:48"},
                    "worst": {"label": "NOMINAL", "t": ""},
                    "hist": {"NOMINAL": 148}, "n": 148, "interval_s": 0.5},
        "spec_type": None, "warm_skip": 64, "warm_skip_applied": True, "fixed_fill_seed": 1,
        "engine_build": "e5d1c0f14", "ctx_size": None, "spec_draft_n_max": None,
        "memory": {"launch": {"swap_used_mb": 8267.0}, "end": {"swap_used_mb": 9012.0},
                   "worst": {"max_swap_mb": 9100.0}},
        "rows": [{**row_common, "n_prompt": 2048, "n_gen": 0, "n_depth": 512, "avg_ts": 292.85,
                  "stddev_ts": 1.1, "samples_ts": [292.2, 294.1, 292.2], "test_time": "2026-09-27T03:15:56Z"},
                 {**row_common, "n_prompt": 0, "n_gen": 64, "n_depth": 512, "avg_ts": 12.019,
                  "stddev_ts": 0.29, "samples_ts": [11.99, 12.02, 11.98], "test_time": "2026-09-27T03:16:29Z"}],
    }
    entry.update(kw)
    return entry


def selftest() -> int:
    baseline = _load_json(BASELINE_DEFAULT)
    cases = []

    def run(name, entry, want, runner="matrix-direct"):
        shaped, sample = judge(entry, runner, "e5d1c0f14", baseline, WALL_S_MAX, SANITY_FLOOR)
        got = sample["verdict"]
        extra_ok = True
        if runner == "matrix-direct" and want == "accepted":
            extra_ok = (shaped["base_check"]["pass"] is True
                        and shaped["cell_gate"] == baseline["cell"]
                        and shaped["box_gate"]["synthesized"] is True
                        and shaped["sys_before"] is None)
        elif runner == "harness-bench" and want == "accepted":
            extra_ok = shaped["ingest"]["contract_gaps"] == ["charter", "sys_before",
                                                              "sys_after", "sys_rate"]
        cases.append((name, got == want and extra_ok, f"got={got} want={want} extra_ok={extra_ok}",
                      sample))

    run("canonical matrix-direct sample", _synth_entry(baseline), "accepted")
    run("thermal HEAVY", _synth_entry(baseline, thermal={"launch": {"label": "HEAVY"},
                                                         "worst": {"label": "HEAVY"}}), "rejected")
    run("degraded window (997 s, pp 47, tg 3.6)",
        _synth_entry(baseline, wall_s=997.0, rows=[{**_synth_entry(baseline)["rows"][0],
                                                    "avg_ts": 47.03, "stddev_ts": 58.8},
                                                   {**_synth_entry(baseline)["rows"][1],
                                                    "avg_ts": 3.617, "stddev_ts": 4.87}]),
        "rejected")
    run("wrong build", _synth_entry(baseline, engine_build="640e56aa8"), "rejected")
    run("variant arm env", _synth_entry(baseline, extra_env={"CGC_SERVER_MTP": "0"}), "variant")
    hb = _synth_entry(baseline, base_check={"profile": "prod-new", "pass": True},
                      box_gate={"thermal_gate": True}, cell={"prompt": 2048})
    run("harness-bench passthrough (軟缺口照記錄)", hb, "accepted", runner="harness-bench")
    run("harness product missing box_gate (硬缺口)",
        {k: v for k, v in hb.items() if k != "box_gate"}, "rejected", runner="harness-bench")
    run("no rows (empty ON arm)", {**_synth_entry(baseline), "rows": []}, "rejected")

    # health 層（citation-grade）與樣本 verdict 是兩件事：能不能引用 vs 是不是同一種窗
    def with_thermal(entry, hist, worst="NOMINAL", n=150):
        return {**entry, "thermal": {"launch": {"label": "NOMINAL"}, "worst": {"label": worst},
                                     "hist": hist, "n": n, "interval_s": 0.5}}

    def health_case(name, entry, want_h, want_v="accepted"):
        shaped, sample = judge(entry, "matrix-direct", "e5d1c0f14", baseline, WALL_S_MAX, SANITY_FLOOR)
        got = (sample["verdict"], sample["health"]["verdict"])
        want = (want_v, want_h)
        cases.append((name, got == want, f"got={got} want={want}", sample))

    bimodal = _synth_entry(baseline)
    bimodal["rows"][1]["samples_ts"] = [11.98, 11.08, 11.92]
    bimodal["rows"][1]["avg_ts"] = 11.66
    health_case("rep bimodal（2 個一致 + 1 個掉 7.6%）", bimodal, "mixed")
    scatter = _synth_entry(baseline)
    scatter["rows"][1]["samples_ts"] = [11.79, 11.48, 11.56]   # 均勻散開，沒有單一元兇
    health_case("rep spread（均勻散開 2.7%）", scatter, "mixed")
    health_case("thermal hist 有 HEAVY/MODERATE（連續跑）",
                with_thermal(_synth_entry(baseline),
                             {"NOMINAL": 80, "MODERATE": 39, "HEAVY": 32}), "mixed")
    health_case("sampler 被餓死（coverage 0.13）",
                with_thermal(_synth_entry(baseline), {"NOMINAL": 20}, n=20), "sick")

    bad = 0
    for name, ok, info, _sample in cases:
        if not ok:
            bad += 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:42s} {info}")
    acc = stats_of([c[3] for c in cases if c[3]["verdict"] == "accepted"])
    print(f"  stats over accepted: {json.dumps(acc, ensure_ascii=False)}")
    print(f"selftest: {'PASS' if bad == 0 else f'{bad} FAIL'} ({len(cases)} cases)")
    return 0 if bad == 0 else 1


# --------------------------------------------------------------------------- main
def _band_check(baseline: dict, stat: dict) -> dict | None:
    """走廊有沒有整段落在 window-class 錨裡？這是形狀檢查，不是精度宣稱。"""
    tg = stat.get("tg") or {}
    band = baseline.get("band") or {}
    if tg.get("min") is None or not band:
        return None
    ref = baseline["measured"]["tg_t_s"]
    lo, hi = ref * band["low"], ref * band["high"]
    return {"low": round(lo, 2), "high": round(hi, 2), "corridor_min": tg["min"],
            "corridor_max": tg["max"], "corridor_span_pct": tg.get("spread_pct"),
            "covers_corridor": bool(lo <= tg["min"] and tg["max"] <= hi),
            "why": "band 是窗級錨（不是比較精度）：這裡只回答走廊是否整段落在錨內"}


def parse_product(spec: str) -> tuple[Path, str | None, str | None]:
    parts = spec.split(":")
    path = Path(parts[0])
    runner = parts[1] or None if len(parts) > 1 else None
    session = parts[2] or None if len(parts) > 2 else None
    if runner and runner not in RUNNERS:
        raise SystemExit(f"bad runner in --product {spec!r}: want one of {', '.join(RUNNERS)}")
    return path, runner, session


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--product", action="append", default=[], metavar="PATH[:runner[:session]]")
    ap.add_argument("--out", help="合併後的門形產物（list of arm entries）")
    ap.add_argument("--summary-out", dest="summary_out")
    ap.add_argument("--baseline", default=str(BASELINE_DEFAULT))
    ap.add_argument("--head", default="e5d1c0f14", help="釘住的 engine_build（head）")
    ap.add_argument("--wall-s-max", type=float, default=WALL_S_MAX)
    ap.add_argument("--sanity-floor", type=float, default=SANITY_FLOOR,
                    help="相對 frozen anchor 的地板倍率（預設 0.6）")
    ap.add_argument("--purpose", default="off corridor for the authoritative bench cell")
    ap.add_argument("--print", dest="do_print", action="store_true", default=True)
    ap.add_argument("--strict", action="store_true", help="有任何 rejected 就非 0")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.product:
        print("need --product <file> (repeatable) or --selftest", file=sys.stderr)
        return 2

    baseline = _load_json(Path(a.baseline))
    base_md5 = hashlib.md5(Path(a.baseline).read_bytes()).hexdigest()
    shaped_all: list[dict] = []
    samples: list[dict] = []
    products: list[dict] = []

    for spec in a.product:
        path, runner_override, session_override = parse_product(spec)
        if not path.exists():
            print(f"!! 找不到產品：{path}")
            products.append({"path": str(path), "missing": True})
            continue
        raw = _load_json(path)
        entries = raw if isinstance(raw, list) else [raw]
        rec = {"path": str(path), "md5": _md5(path), "entries": len(entries)}
        runners = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            runner = runner_override or detect_runner(entry)
            runners.add(runner)
            shaped, sample = judge(entry, runner, a.head, baseline, a.wall_s_max, a.sanity_floor)
            if session_override:
                shaped["ingest"]["session"] = session_override
                shaped["ingest"]["session_source"] = "--product spec (caller declared)"
                sample["session"], sample["session_source"] = session_override, "--product spec (caller declared)"
            shaped["ingest"]["source_product"] = str(path)
            shaped["ingest"]["source_md5"] = rec["md5"]
            shaped["ingest"]["baseline_md5"] = base_md5
            sample["artifact"] = str(path)
            samples.append(sample)
            shaped_all.append(shaped)
        rec["runners"] = sorted(runners)
        products.append(rec)

    # 健康層：wall 要跟同批的健康同伴比，所以它是第二遍（第一遍先收齊樣本）
    wall_pool = [s["wall_s"] for s in samples
                 if s.get("wall_s") and s["health"]["checks"]["thermal"]["sev"] == "ok"]
    wall_ref = st.median(wall_pool) if wall_pool else None
    for s in samples:
        h = s["health"]
        w = s.get("wall_s")
        cw = h["checks"]["wall"]
        if wall_ref:
            cw["ref_s"] = round(wall_ref, 1)
            cw["sev"] = "ok" if (w and w <= wall_ref * WALL_FACTOR_MAX) else "warn"
        else:
            cw["sev"] = "ok"
        cw["why"] = (f"vs 同批健康樣本 wall 中位 {cw.get('ref_s')} s x {WALL_FACTOR_MAX}；"
                      "窗級崩壞（今天 996.5 s）不只是一臂慢，是整個盒況換了")
        h["verdict"] = _health_verdict(h["checks"])

    # 窗的分段：同一個窗 = 相鄰樣本間隔 <= SEGMENT_GAP_MIN 分鐘
    segs = segments(samples)
    seg_of = {sess: sg["start"] for sg in segs for sess in sg["sessions"]}
    for s in samples:
        s["segment"] = seg_of.get(s["session"])

    # 統計只吃 accepted（variant/rejected 照記錄，不進 canonical 分布）
    stat = stats_of(samples)

    if a.do_print:
        print(f"{'session':17s} {'runner':14s} {'verdict':9s} {'health':7s} {'reps':>5s} {'tg t/s':>8s} "
              f"{'pp t/s':>8s} {'wall s':>7s} thermal     artifact")
        for s in sorted(samples, key=lambda x: x["session"]):
            tg = f"{s['tg_t_s']:.2f}" if s["tg_t_s"] is not None else "  -  "
            pp = f"{s['pp_t_s']:.2f}" if s["pp_t_s"] is not None else "  -  "
            wall = f"{s['wall_s']:.1f}" if s["wall_s"] is not None else "   -  "
            rs = s["health"]["checks"]["rep_shape"]["read"]
            sp = f"{rs['spread_pct']:.1f}%" if rs.get("spread_pct") is not None else "-"
            print(f"{s['session']:17s} {s['runner']:14s} {s['verdict']:9s} {s['health']['verdict']:7s} "
                  f"{sp:>5s} {tg:>8s} {pp:>8s} {wall:>7s} {str(s['thermal_worst']):11s} "
                  f"{Path(s['artifact']).name}")
            for r in s["reasons"]:
                print(f"      ! {r}")
            for k, c in s["health"]["checks"].items():
                if c["sev"] in ("warn", "fail"):
                    print(f"      ~ health.{k}: {c['sev']} ({json.dumps(c['read'], ensure_ascii=False)})")
        if segs:
            print("\n窗（相鄰樣本間隔 <= %.0f 分鐘）：" % SEGMENT_GAP_MIN)
            for g in segs:
                print(f"  {g['start'][5:16]} → {g['end'][5:16]}  span {g['span_min']:5.1f} min  "
                      f"n={g['n']} acc={g['n_accepted']}  tg_med={g['tg_median_t_s']}  "
                      f"窗內 spread={g['tg_spread_pct']}%  rep spread 中位={g['rep_spread_pct_median']}%")
        spread = stat.get("tg", {}).get("spread_pct")
        print(f"\ncanonical corridor: n={stat.get('n_accepted')} "
              f"tg median={stat.get('tg', {}).get('median')} "
              f"[{stat.get('tg', {}).get('min')}, {stat.get('tg', {}).get('max')}] "
              f"spread={spread}%  frozen anchor={baseline['measured']['tg_t_s']}")

    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(shaped_all, indent=2, ensure_ascii=False))
        print(f"\n門形產物 -> {out}（{len(shaped_all)} 臂；補欄位的臂標了 ingest.runner=matrix-direct）")
    if a.summary_out:
        sobj = {
            "schema": "bench-corridor/1",
            "purpose": a.purpose,
            "generated_by": "scripts/check/bench_ingest.py",
            "generated_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0800"),
            "profile": baseline.get("profile"),
            "cell": baseline["cell"],
            "head": a.head,
            "baseline": {"path": a.baseline, "md5": base_md5,
                         "tg_t_s": baseline["measured"]["tg_t_s"],
                         "pp_t_s": baseline["measured"]["pp_t_s"], "frozen": baseline.get("frozen"),
                         "role": "走廊只驗證「哪些單點屬於同一種窗」；門檻仍讀 baseline 的 measured/band"},
            "health": {
                "thresholds": {"rep_spread_clean_pct": REP_SPREAD_CLEAN * 100,
                               "coverage_min": COVERAGE_MIN,
                               "wall_factor_max": WALL_FACTOR_MAX,
                               "segment_gap_min": SEGMENT_GAP_MIN},
                "wall_ref_s": round(wall_ref, 1) if wall_ref else None,
                "segments": segs,
                "citation_rule": (
                    "可引用的窗 = 同 head、同 cell、health=clean（rep_shape tight 且 thermal hist "
                    "無 MODERATE/HEAVY 且 coverage>=0.90）且與同伴樣本間隔 <=10 分鐘。今天的兩個尺度："
                    "<=7 分鐘背對背 4 支 run 中位數 1σ≈1.4%；跨 95 分鐘 5 支 1σ≈3.6%。"
                    "單讀對單讀的最小可辨差 ≈10%（95%）；<=3% 的宣稱只能是同窗配對"
                    "（ABBA／交錯、兩側各 >=2 reps、兩側 spread<=3%）。"
                    "另：同臂三個 rep 的 spread 今天雙模（乾淨 0.5-2.4%、混合 3.0-4.7%），"
                    "所以 rep_shape 不是裝飾，它決定那個中位數是不是混合值。"),
            },
            "rules": {"head": a.head, "wall_s_max": a.wall_s_max, "sanity_floor": a.sanity_floor,
                      "health": {"rep_spread_clean_pct": REP_SPREAD_CLEAN * 100,
                                 "coverage_min": COVERAGE_MIN,
                                 "wall_factor_max": WALL_FACTOR_MAX,
                                 "segment_gap_min": SEGMENT_GAP_MIN},
                      "reject_reasons": ["r_rows", "r_contract", "r_incomplete", "r_error",
                                         "r_head", "r_thermal", "r_cell", "r_wall", "r_floor",
                                         "variant(extra_env)"]},
            "band_check": _band_check(baseline, stat),
            "products": products,
            "samples": samples,
            "stats": stat,
            "shape": {"corridor_artifact": a.out,
                      "note": "本檔的 samples 是走廊；不是配對。3% 的宣稱仍然只活在配對自己的重複上"
                              "（mtp_promotion_gate.py v2），這個檔只回答「哪些單點屬於同一種窗」。"},
        }
        sout = Path(a.summary_out)
        sout.parent.mkdir(parents=True, exist_ok=True)
        sout.write_text(json.dumps(sobj, indent=2, ensure_ascii=False))
        print(f"走廊摘要   -> {sout}")

    n_rej = sum(1 for s in samples if s["verdict"] == "rejected")
    n_var = sum(1 for s in samples if s["verdict"] == "variant")
    print(f"\nverdicts: accepted={stat.get('n_accepted')} variant={n_var} rejected={n_rej}")
    return 1 if (a.strict and n_rej) else 0


if __name__ == "__main__":
    sys.exit(main())
