#!/usr/bin/env python3
"""experiment_sync — 把「跑前立項 charter → mindmap 實驗節點 → harness bench 產物 → 節點更新」自動化。

為什麼需要它
    兩條開發線各自跑實驗，常見三個問題：實驗沒在 mindmap 上掛節點（看不到進展）、繞過生產級
    腳本自拼參數（口徑不一致）、跑完結果不回寫（子目標永遠卡在 doing）。本工具把流程釘死成：

        ① 寫一張 charter（scripts/check/charters/_TEMPLATE.yaml：現狀/目標/假設/驗收）
        ② python3 experiment_sync.py init --charter <x.yaml> --sub S
               → 在 mindmap 建一個 tier=3a 的「實驗階段」節點（含初始子目標）
        ③ 用生產級腳本跑：harness.py bench --charter <x.yaml> --json Backup/.../x.json
        ④ python3 experiment_sync.py sync --artifact Backup/.../x.json
               → 把運行設置／可點 log／結果／thermal／swap 追加進節點，更新結果與子目標

    init／sync 完成後都會自動重建 mindmap 總圖（index.html）＋ 全部逐條白皮書 briefs（html/md）。

用法
    python3 experiment_sync.py init  --charter scripts/check/charters/x.yaml --sub S
    python3 experiment_sync.py sync  --artifact Backup/.../x.json [--artifact ...]
    python3 experiment_sync.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mindmap_build as MB          # noqa: E402  (總圖構建／校驗)
import mindmap_brief_build as MBB   # noqa: E402  (逐條白皮書)

MM_JSON = ROOT / "docs/mindmap/mindmap.json"
EXP_BACKUP = ROOT / "Backup/exp_runs"
SUBS = ("S", "M", "both", "C", "na")


# ───────────────────────── 純邏輯（不碰檔案，selftest 測這一層）─────

def resolve_sub(charter: dict, sub: str | None) -> str:
    """實驗節點要掛在哪個軸：--sub 優先，其次 charter.axis；都沒有就 fail-closed。"""
    s = sub or charter.get("axis")
    if s not in SUBS:
        raise ValueError(f"必須指定軸 --sub（{list(SUBS)}），或在 charter 加 axis 欄位")
    return s


def short_name(question: str, fallback: str, n: int = 34) -> str:
    q = " ".join(str(question).split())
    if not q:
        return fallback
    return q[:n] + "…" if len(q) > n else q


def build_entry(charter: dict, sub: str, name: str | None = None) -> dict:
    """charter → tier=3a 實驗節點（純 dict）。"""
    cid = str(charter.get("id", "")).strip()
    if not cid:
        raise ValueError("charter 缺 id")
    base = charter.get("baseline") or {}
    hyp = charter.get("hypothesis") or {}
    acc = charter.get("acceptance") or {}
    raw_targets = charter.get("targets")
    targets = {k: v for k, v in (raw_targets.items() if isinstance(raw_targets, dict) else [])
               if k in ("decode_tps", "prefill_tps") and isinstance(v, (int, float))}
    charter_rel = None
    cp = charter.get("_path")
    if cp and _is_relative_to(Path(cp), ROOT):
        charter_rel = os.path.relpath(cp, ROOT)
    return {
        "id": cid,
        "name": name or short_name(charter.get("question", ""), cid),
        "theme": "實驗",
        "tier": "3a",
        "goal": str(charter.get("question", "")).strip(),
        "crit": str(acc.get("success", "—")),
        "res": "實驗進行中（尚無讀數）",
        "evid": str(base.get("source", "—")),
        "sub": sub,
        "note": f'假設：{str(hyp.get("mechanism", "—"))}',
        "runs": [],
        "targets": targets,
        "best": None,
        "target_gap": target_gap(targets, None),
        "subtasks": [
            {"id": "st-charter", "text": "跑前立項（現狀/目標/假設/驗收）", "status": "done",
             **({"log": charter_rel} if charter_rel else {})},
            {"id": "st-run", "text": "用生產級腳本 prod-new ＋ 自己 option 跑實驗臂，留存 log",
             "status": "doing"},
            {"id": "st-accept", "text": f'驗收：{str(acc.get("success", "—"))}', "status": "todo"},
            {"id": "st-falsify", "text": f'否證條件：{str(acc.get("falsify", "—"))}', "status": "todo"},
        ],
    }


def charter_id(arm: dict) -> str | None:
    """從產物 arm 取出它隸屬的 charter id（無立項／豁免 → None）。"""
    ch = arm.get("charter")
    if isinstance(ch, dict):
        return None if ch.get("waived") else ch.get("id")
    if isinstance(ch, str) and ch not in ("waived", "none", ""):
        return ch
    return None


def row_ts(rows: list, kind: str):
    """從 rows 抓 prefill（pp，n_prompt>0）或 decode（tg，n_gen>0 且無 prompt）的 avg_ts。"""
    for r in rows:
        if kind == "pp" and r.get("n_prompt", 0) > 0:
            return r.get("avg_ts")
        if kind == "tg" and r.get("n_prompt", 0) == 0 and r.get("n_gen", 0) > 0:
            return r.get("avg_ts")
    return None


def _mb(v):
    return f"{v:.0f} MiB" if isinstance(v, (int, float)) else "?"


def build_run(arm: dict, log_rel: str, when: str) -> dict:
    """產物 arm → 一條 run（運行設置＋log＋結果＋thermal＋swap）。"""
    rows = arm.get("rows") or []
    sb, sa = arm.get("sys_before") or {}, arm.get("sys_after") or {}
    pp, tg = row_ts(rows, "pp"), row_ts(rows, "tg")
    th = arm.get("thermal") or {}
    att = arm.get("attribution") or {}
    thermal = ((sa.get("thermal") or {}).get("label")) or th.get("worst") or "?"
    r0 = rows[0] if rows else {}
    pp_row = next((r for r in rows if r.get("n_prompt", 0) > 0), None)
    tg_row = next((r for r in rows if r.get("n_prompt", 0) == 0 and r.get("n_gen", 0) > 0), None)
    n_prompt = (pp_row or r0).get("n_prompt", "?")
    n_gen = tg_row.get("n_gen") if tg_row else r0.get("n_gen", "?")
    cmd_desc = (f'llama-bench（{arm.get("profile", "?")}）-p {n_prompt} '
                f'-n {n_gen} --warm-skip {arm.get("warm_skip", "?")}')
    result = {}
    if pp is not None:
        result["pp"] = round(pp, 2)
    if tg is not None:
        result["tg"] = round(tg, 2)
    return {
        "when": when,
        "arm": str(arm.get("tag") or arm.get("profile") or "?"),
        "profile": arm.get("profile"),
        "log": log_rel,
        "cmd": cmd_desc + "（由產物參數重建）",
        "result": result,
        "thermal": thermal,
        "swap": {"before": _mb(sb.get("swap_used_mb")), "after": _mb(sa.get("swap_used_mb"))},
        "verdict": f'{att.get("verdict", "?")}：{att.get("why", "")}'.rstrip("："),
    }


def merge_run(entry: dict, run: dict) -> bool:
    """把 run 併進 entry（同 log 去重）；有 decode 結果就更新 res、把 st-run 標 done。回是否新增。"""
    entry.setdefault("runs", [])
    if any(r.get("log") == run["log"] for r in entry["runs"]):
        return False
    entry["runs"].append(run)
    tg = run.get("result", {}).get("tg")
    if tg is not None:
        tag = run.get("verdict", "").split("：", 1)[0]
        entry["res"] = f"decode {tg:.2f} t/s" + (f"（{tag}）" if tag and tag != "?" else "")
    for t in entry.setdefault("subtasks", []):
        if t.get("id") == "st-run" and run.get("result"):
            t["status"] = "done"
    entry["best"] = compute_best(entry["runs"])
    entry["target_gap"] = target_gap(entry.get("targets") or {}, entry["best"])
    return True


def compute_best(runs: list) -> dict | None:
    """從 runs 挑成績最高者：以 decode(tg) 為第一指標；無任何 tg 才退而以 prefill(pp) 為準。"""
    def tg_of(r):
        return (r.get("result") or {}).get("tg")

    def pp_of(r):
        return (r.get("result") or {}).get("pp")

    tg_cands = [r for r in runs if isinstance(tg_of(r), (int, float))]
    if tg_cands:
        r = max(tg_cands, key=tg_of)
        return {"metric": "tg", "tg": tg_of(r), "pp": pp_of(r),
                "arm": r.get("arm"), "log": r.get("log"), "when": r.get("when")}
    pp_cands = [r for r in runs if isinstance(pp_of(r), (int, float))]
    if pp_cands:
        r = max(pp_cands, key=pp_of)
        return {"metric": "pp", "tg": None, "pp": pp_of(r),
                "arm": r.get("arm"), "log": r.get("log"), "when": r.get("when")}
    return None


def target_gap(targets: dict, best: dict | None) -> dict:
    """量化目標 vs 當前最佳：回每個目標的 current/gap/進度%（tg→decode_tps、pp→prefill_tps）。"""
    cur_map = {"decode_tps": (best or {}).get("tg"), "prefill_tps": (best or {}).get("pp")}
    out: dict = {}
    for key in ("decode_tps", "prefill_tps"):
        tgt = targets.get(key) if isinstance(targets, dict) else None
        if not isinstance(tgt, (int, float)):
            continue
        cur = cur_map[key]
        if isinstance(cur, (int, float)):
            out[key] = {"current": round(cur, 3), "target": tgt,
                        "gap": round(cur - tgt, 3), "pct": round(cur / tgt * 100, 1)}
        else:
            out[key] = {"current": None, "target": tgt, "gap": None, "pct": None}
    return out


# ───────────────────────── 檔案 IO ────────────────────────────────

def _is_relative_to(p: Path, base: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def load_charter(path: str) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        raise SystemExit(f"charter 不存在: {path}（相對於 repo root 解析）")
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 PyYAML（pip install pyyaml）")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("charter 頂層必須是 mapping")
    data["_path"] = str(p)
    return data


def load_mm() -> dict:
    return json.loads(MM_JSON.read_text(encoding="utf-8"))


def save_mm(data: dict) -> None:
    MM_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_log_in_repo(artifact: Path, cid: str, stamp: str) -> str:
    """產物若在 repo 內，回其相對路徑；否則複製進 Backup/exp_runs/（保證 brief 的連結點得到）。"""
    if _is_relative_to(artifact, ROOT):
        return os.path.relpath(artifact.resolve(), ROOT)
    EXP_BACKUP.mkdir(parents=True, exist_ok=True)
    dest = EXP_BACKUP / f"{cid}_{stamp}.json"
    shutil.copy2(artifact, dest)
    return str(dest.relative_to(ROOT))


def rebuild() -> None:
    """重建 mindmap 總圖（權威流程 MB.main）＋ 全部逐條白皮書 briefs。"""
    if MB.main([]) != 0:
        raise SystemExit("mindmap 總圖構建失敗（見上）")
    data, mapping = MBB.load()
    problems = MBB.build(data, mapping)
    if problems:
        print("!! briefs 構建問題：", problems[:5])
        raise SystemExit(2)


# ───────────────────────── 子命令 ────────────────────────────────

def cmd_init(args) -> int:
    charter = load_charter(args.charter)
    cid = str(charter.get("id", "")).strip()
    sub = resolve_sub(charter, args.sub)
    data = load_mm()
    byid = {e["id"]: e for e in data["entries"]}
    if cid in byid and not args.update:
        raise SystemExit(f"已存在節點 {cid}（加 --update 可刷新描述、保留歷史 runs）")
    entry = build_entry(charter, sub, args.name)
    if cid in byid:
        old = byid[cid]
        for k in ("name", "theme", "tier", "goal", "crit", "res", "evid", "sub", "note", "targets"):
            old[k] = entry[k]
        old.setdefault("runs", [])
        old.setdefault("subtasks", entry["subtasks"])
        old["best"] = compute_best(old["runs"])
        old["target_gap"] = target_gap(old.get("targets") or {}, old["best"])
        print(f"已更新實驗節點 {cid}（軸 {sub}，歷史 runs 保留）")
    else:
        data["entries"].append(entry)
        print(f"已建立實驗節點 {cid}（軸 {sub}）")
    save_mm(data)
    rebuild()
    return 0


def cmd_sync(args) -> int:
    data = load_mm()
    byid = {e["id"]: e for e in data["entries"]}
    now = dt.datetime.now()
    stamp, when = now.strftime("%Y%m%d_%H%M%S"), now.strftime("%Y-%m-%d %H:%M")
    touched: list[str] = []
    for artifact in args.artifact:
        p = Path(artifact)
        if not p.exists():
            print(f"  跳過：產物不存在 {artifact}")
            continue
        arms = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(arms, dict):
            arms = [arms]
        for arm in arms:
            cid = charter_id(arm) or args.entry
            if not cid:
                print(f"  跳過：{p.name} 的 arm '{arm.get('tag')}' 無 charter（用 --entry 指定）")
                continue
            entry = byid.get(cid)
            if not entry:
                print(f"  跳過：找不到節點 {cid}（先 init）")
                continue
            log_rel = ensure_log_in_repo(p, cid, stamp)
            run = build_run(arm, log_rel, when)
            if merge_run(entry, run):
                touched.append(cid)
                print(f"  {cid}：追加 run（{run.get('result') or '無結果'}）")
    if not touched:
        print("沒有任何節點被更新")
        return 1
    save_mm(data)
    rebuild()
    print("已更新節點：", sorted(set(touched)))
    return 0


# ───────────────────────── selftest ──────────────────────────────

def selftest() -> bool:
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    charter = {
        "id": "exp-demo", "owner": "agent-x",
        "question": "把 X 並行化之後 decode 能否提升超過 3%？",
        "baseline": {"metric": "decode t/s", "cell": "prod-new 穩態", "source": "docs/abc.md"},
        "hypothesis": {"mechanism": "X 與 GPU 重疊", "expected": "+8%", "basis": "分解"},
        "acceptance": {"success": "decode ≥ 13.2 t/s", "falsify": "配對中位 < +3%",
                       "on_fail": "收攤"},
        "arms": ["prod-new"],
    }
    entry = build_entry(charter, "S")
    chk("build_entry：tier=3a、軸 S", entry["tier"] == "3a" and entry["sub"] == "S")
    chk("build_entry：goal=question、crit=success",
        entry["goal"].startswith("把 X") and entry["crit"] == "decode ≥ 13.2 t/s")
    chk("build_entry：4 條初始 subtasks、首條 done",
        len(entry["subtasks"]) == 4 and entry["subtasks"][0]["status"] == "done")
    chk("build_entry：runs 初始為空", entry["runs"] == [])

    try:
        resolve_sub(charter, None)
        chk("resolve_sub：無軸應拒", False)
    except ValueError:
        chk("resolve_sub：無軸應拒", True)
    chk("resolve_sub：charter.axis 可用", resolve_sub({**charter, "axis": "M"}, None) == "M")
    chk("short_name：過長截斷", short_name("q" * 50, "fb").endswith("…"))

    arm = {
        "tag": "prod-new", "profile": "prod-new", "warm_skip": 64,
        "charter": {"id": "exp-demo"},
        "rows": [
            {"n_prompt": 512, "n_gen": 0, "avg_ts": 280.1},
            {"n_prompt": 0, "n_gen": 128, "avg_ts": 12.3},
        ],
        "sys_before": {"swap_used_mb": 100.0, "thermal": {"label": "NOMINAL"}},
        "sys_after": {"swap_used_mb": 130.0, "thermal": {"label": "NOMINAL"}},
        "thermal": {"worst": "NOMINAL"},
        "attribution": {"verdict": "clean", "why": "thermal=NOMINAL"},
    }
    chk("charter_id：dict 取 id", charter_id(arm) == "exp-demo")
    chk("charter_id：waived→None", charter_id({"charter": {"waived": True}}) is None)
    chk("row_ts：pp=280.1", row_ts(arm["rows"], "pp") == 280.1)
    chk("row_ts：tg=12.3", row_ts(arm["rows"], "tg") == 12.3)

    run = build_run(arm, "Backup/x.json", "2026-09-27 12:00")
    chk("build_run：result 含 pp/tg", run["result"] == {"pp": 280.1, "tg": 12.3})
    chk("build_run：swap before→after",
        run["swap"]["before"] == "100 MiB" and run["swap"]["after"] == "130 MiB")
    chk("build_run：thermal 與 verdict",
        run["thermal"] == "NOMINAL" and run["verdict"].startswith("clean"))

    fresh = build_entry(charter, "S")
    chk("merge_run：首次新增、res 更新",
        merge_run(fresh, run) and fresh["res"].startswith("decode 12.30"))
    st_run = next(t for t in fresh["subtasks"] if t["id"] == "st-run")
    chk("merge_run：st-run 標 done", st_run["status"] == "done")
    chk("merge_run：同 log 去重（不重複）", merge_run(fresh, run) is False)

    # ── A＋B：targets 解析 / best-config / 目標差距 ─────────────────────
    ch_t = {**charter, "targets": {"decode_tps": 15.0, "prefill_tps": 250.0}}
    e_t = build_entry(ch_t, "S")
    chk("build_entry：targets 解析、best 初始 None",
        e_t["targets"] == {"decode_tps": 15.0, "prefill_tps": 250.0} and e_t["best"] is None)
    chk("build_entry：target_gap 初始 current 空、target 在",
        e_t["target_gap"]["decode_tps"]["current"] is None
        and e_t["target_gap"]["decode_tps"]["target"] == 15.0)

    r1 = {"result": {"tg": 11.0, "pp": 270.0}, "arm": "a1", "log": "l1", "when": "t1"}
    r2 = {"result": {"tg": 12.8, "pp": 260.0}, "arm": "a2", "log": "l2", "when": "t2"}
    r3 = {"result": {"tg": 12.1, "pp": 290.0}, "arm": "a3", "log": "l3", "when": "t3"}
    b = compute_best([r1, r2, r3])
    chk("compute_best：取 tg 最高 r2、帶 arm/log/pp",
        b["tg"] == 12.8 and b["pp"] == 260.0 and b["arm"] == "a2" and b["log"] == "l2")
    chk("compute_best：空 runs → None", compute_best([]) is None)
    bpp = compute_best([{"result": {"pp": 280.0}, "arm": "p", "log": "lp", "when": "tp"}])
    chk("compute_best：僅 pp → metric=pp、tg None",
        bpp["metric"] == "pp" and bpp["pp"] == 280.0 and bpp["tg"] is None)

    g = target_gap({"decode_tps": 15.0, "prefill_tps": 250.0}, b)
    chk("target_gap：decode gap=-2.2 pct=85.3",
        g["decode_tps"]["current"] == 12.8 and g["decode_tps"]["gap"] == -2.2
        and g["decode_tps"]["pct"] == 85.3)
    chk("target_gap：prefill 達標 gap=10.0 pct=104.0",
        g["prefill_tps"]["current"] == 260.0 and g["prefill_tps"]["gap"] == 10.0
        and g["prefill_tps"]["pct"] == 104.0)

    e2 = build_entry(ch_t, "S")
    merge_run(e2, {"result": {"tg": 11.0}, "arm": "a1", "log": "l1", "when": "t1",
                  "thermal": "NOMINAL", "swap": {}, "verdict": ""})
    merge_run(e2, {"result": {"tg": 12.8, "pp": 260.0}, "arm": "a2", "log": "l2",
              "when": "t2", "thermal": "NOMINAL", "swap": {}, "verdict": ""})
    chk("merge_run：best 取最高 12.8、target_gap 同步 pct=85.3",
        e2["best"]["tg"] == 12.8 and e2["best"]["arm"] == "a2"
        and e2["target_gap"]["decode_tps"]["pct"] == 85.3)
    return ok


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="charter ↔ mindmap 實驗節點同步")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="從 charter 建實驗節點")
    p_init.add_argument("--charter", required=True)
    p_init.add_argument("--sub", choices=SUBS)
    p_init.add_argument("--name")
    p_init.add_argument("--update", action="store_true")

    p_sync = sub.add_parser("sync", help="從 harness bench 產物更新節點")
    p_sync.add_argument("--artifact", required=True, nargs="+")
    p_sync.add_argument("--entry", help="產物無 charter 時，指定要寫入的節點 id")

    args = ap.parse_args(argv)
    if args.selftest:
        print("════ experiment_sync selftest ════")
        ok = selftest()
        print(f"\n{'SELFTEST OK' if ok else 'SELFTEST FAIL'}")
        return 0 if ok else 1
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "sync":
        return cmd_sync(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
