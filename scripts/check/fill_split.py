#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_split.py — 交付 cell 上「每 miss 的等」逐桶分解（2026-09-30）

問題：交付 cell 上每 miss 的 ~0.75 ms「等」（docs/FILL_TERM_DELIVERY_2026-09-30.md），
是**開檔**、**解碼**（pread 本身）還是**喚醒路徑**？三者的修法完全不同（前者＝別 open、
中者＝少讀／早讀／讀大塊、後者＝換同步機制），所以單一個和數無法排序要先修哪一個。

儀器：引擎的 `CGC_FILL_SPLIT=1`（`llama-expert-cache.cpp`，**預設關**；`run_server.sh` 有
轉送，漏轉會被 `llama_bench_matrix.arm_env_dropped()` 抓成 DROPPED），在
`fill_segments_pool` 裡記五個桶，**全部由呼叫端線程累計**（可與牆鐘相比）：

  madvise_us   提交前、呼叫端：P1 discard（prod 預設不開 ⇒ 記到的是空迴圈成本）
  build_us     提交前、呼叫端：排序／run-merge／推 job（持 `pool_m`）
  advise_us    提交**後**、呼叫端：`fcntl F_RDADVISE` 提示 ⇒ **與 worker 重疊**，本檔
               永遠不加進 wait（加了就會把同一段時間算兩次）
  wake_in_us   submit → 第一個 worker 拿到 job（線程喚醒 ＋ 排在既有工作後面）
  span_us      first_dequeue → 最後一個完成（pread 在飛的窗口）
  wake_out_us  最後完成 → 呼叫端重新跑
  wait_us      = wake_in_us + span_us + wake_out_us  ← 恆等式，逐臂現算（閘 3）

「開檔」這一桶刻意**不存在**：這條路上沒有 per-fill 的 `open()`（`cache->files` 是 init 時
開好的 `FILE*`，worker 只做 `pread(fileno(f),...)`）。唯一的 per-fill `open()` 在
`cgc_exact_cache_verify_post_fill()`，閘 6 用 arm env 直接證明它沒被武裝。

結構閘（任一不過 ⇒ REFUSE；fail-closed，看板紅）：
 1 arm 清單必須來自 matrix json：`contract.cell == delivery`、`contract.ok == true`
 2 `incomplete == 0`（有批次的戳記沒落地 ⇒ 不可判，不是 0）
 3 恆等式：`|wake_in+span+wake_out-wait| <= max(1, 0.5%*wait)` μs
 4 `jobs <= segs`（run-merge 只能把 job 數往下壓）
 5 參考臂的桶必須有量（wait>0）；NOFILL 臂必須**全 0** 且 `read_mib == 0`（陰性對照）
 6 arm env 不含 `CGC_EXACT_CACHE_VERIFY`（那條路徑每 fill 一次 open+pread）
 7 `segs == 3 x misses`（本模型一個 miss = gate/up/down 三段）

⚠ 這一支只分解**呼叫端看得到的等**；它不決定那一段能不能真的回收（回收要另一個機制）。
⚠ NOFILL 臂是 timing-only（引擎明文：輸出是垃圾）⇒ 它的 t/s 不是能力數，只用來當上界。

用法：
    python3 scripts/check/fill_split.py --dir Backup/fill_split_delivery_2026-09-30
    python3 scripts/check/fill_split.py --selftest
"""

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FS_RE = re.compile(
    r"CGC-FILLSPLIT: batches=(\d+) incomplete=(\d+) misses=(\d+) segs=(\d+) jobs=(\d+) "
    r"madvise_us=(\d+) build_us=(\d+) advise_us=(\d+) wake_in_us=(\d+) span_us=(\d+) "
    r"wake_out_us=(\d+) wait_us=(\d+) wait_max_us=(\d+)")
FINAL_RE = re.compile(
    r"final stats: runtime requests=(\d+) hits=(\d+) misses=(\d+) \(hit rate ([\d.]+)%\).*?"
    r"file_reads=(\d+) pread_usec=(\d+)")
SHAPE_RE = re.compile(r"CGC-SHAPE v=1 phase=final .*?read_mib=([\d.]+)")
EB_RE = re.compile(r"CGC-EBTIMER: step_usec=(\d+) calls=(\d+) miss=(\d+) n_sum=(\d+) seg=(\w+)")

DELIVERY = "delivery"
SEGS_PER_MISS = 3          # gate/up/down：交付 cell 上每個 (layer, expert) 三段
IDENT_TOL = 0.005          # 閘 3：相對容忍
BUCKETS = ("madvise_us", "build_us", "advise_us", "wake_in_us", "span_us", "wake_out_us",
           "wait_us", "wait_max_us")


def parse_fill_split(path):
    """-> dict(buckets) or None（沒有那一行 ⇒ None，不是 0）。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    m = FS_RE.search(txt)
    if not m:
        return None
    keys = ("batches", "incomplete", "misses", "segs", "jobs") + BUCKETS
    return {k: int(v) for k, v in zip(keys, m.groups())}


def parse_side(path):
    """final stats + read_mib + EBTIMER 累計（同一支 log 的其他事實）。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    out = {}
    if (m := FINAL_RE.search(txt)):
        out.update(misses=int(m.group(3)), hit_pct=float(m.group(4)),
                   file_reads=int(m.group(5)), pread_usec=int(m.group(6)))
    if (m := SHAPE_RE.search(txt)):
        out["read_mib"] = float(m.group(1))
    eb = [(int(m.group(1)), m.group(5)) for m in EB_RE.finditer(txt)]
    out["eb_lines"] = len(eb)
    out["eb_us"] = sum(x for x, _ in eb)
    out["eb_decode_lines"] = sum(1 for _, s in eb if s == "decode")
    return out


def load_matrix(dirpath):
    """吃一個矩陣目錄：ab.json + ab.logs/*.stderr.log -> [arm]。"""
    jpath = os.path.join(dirpath, "ab.json")
    arms = []
    jd = json.load(open(jpath, encoding="utf-8")) if os.path.exists(jpath) else []
    for blk in jd:
        tag = blk.get("tag") or ""
        arms.append({
            "tag": tag,
            "env": dict(blk.get("env") or blk.get("extra_env") or {}),
            "contract": blk.get("contract") or {},
            "attribution": (blk.get("attribution") or {}).get("verdict"),
            "ts": (blk.get("rows") or [{}])[0].get("avg_ts"),
            "ts_sd": (blk.get("rows") or [{}])[0].get("stddev_ts"),
            "fs": None, "side": {},
        })
    for a in arms:
        if not a["tag"]:
            continue
        # 檔名＝tag 的 `:` `;` `=` 換成 `_`（矩陣把 arm tag 直接當檔名）
        want = a["tag"].replace(":", "_").replace(";", "_").replace("=", "_")
        cands = glob.glob(os.path.join(dirpath, "ab.logs", want + ".stderr.log"))
        if not cands:
            cands = [p for p in glob.glob(os.path.join(dirpath, "ab.logs", "*.stderr.log"))
                     if want in os.path.basename(p)]
        if cands:
            a["fs"] = parse_fill_split(cands[0])
            a["side"] = parse_side(cands[0])
            a["log"] = os.path.basename(cands[0])
    return arms


# ─────────────────────────────────────────────────────────────────────────────
# 判詞
# ─────────────────────────────────────────────────────────────────────────────
def _is_nofill(a):
    return "NOFILL" in (a["tag"] or "").upper() or "CGC_EB_NOFILL" in (a.get("env") or {})


def judge(arms, ref_substr=None):
    """-> (verdict, ref, nofill, [problem])；verdict ∈ {SPLIT, REFUSE}。"""
    probs = []
    ref = nofill = None
    for a in arms:
        c = a.get("contract") or {}
        if c.get("cell") != DELIVERY:
            probs.append(f"閘1 {a['tag'] or '?'}: contract.cell={c.get('cell')!r} 不是 {DELIVERY}"
                         "（跨 cell 不可比）")
        if not c.get("ok"):
            probs.append(f"閘1 {a['tag'] or '?'}: contract.ok 不是 true（口徑校驗沒過）")
        if a.get("fs") is None:
            probs.append(f"閘1 {a['tag'] or '?'}: 沒有 CGC-FILLSPLIT 行"
                         "（CGC_FILL_SPLIT=1 沒到引擎，或這一臂不是讀取路徑）")

    live = [a for a in arms if a.get("fs")]
    if ref_substr:
        for a in live:
            if ref_substr in a["tag"] and not _is_nofill(a):
                ref = a
                break
    if ref is None:
        for a in live:
            if not _is_nofill(a):
                ref = a
                break
    for a in live:
        if _is_nofill(a):
            nofill = a
            break
    if ref is None:
        probs.append("閘1 找不到參考臂（非 NOFILL 且在交付 cell 上）")
        return "REFUSE", None, nofill, probs

    for a in live:
        fs, tag = a["fs"], a["tag"]
        if fs["incomplete"]:
            probs.append(f"閘2 {tag}: incomplete={fs['incomplete']} > 0"
                         "（戳記沒落地的批次 ⇒ 不可判，不是 0）")
        ident = fs["wake_in_us"] + fs["span_us"] + fs["wake_out_us"]
        tol = max(1.0, IDENT_TOL * fs["wait_us"])
        if abs(ident - fs["wait_us"]) > tol:
            probs.append(f"閘3 {tag}: 恆等式破了 wake_in+span+wake_out={ident} vs wait={fs['wait_us']}"
                         f"（差 {ident - fs['wait_us']} > 容忍 {tol:.0f}）")
        if fs["jobs"] > fs["segs"]:
            probs.append(f"閘4 {tag}: jobs={fs['jobs']} > segs={fs['segs']}"
                         "（run-merge 只能減少 job 數）")
        # 閘 7 只在「這一臂真的走過 fill」時成立：NOFILL 臂在函式頭就返回（batches=0/segs=0），
        # 它的 misses 來自 ensure_batch 的計數，不是被填的段。
        if fs["batches"] and fs["misses"] and fs["segs"] != SEGS_PER_MISS * fs["misses"]:
            probs.append(f"閘7 {tag}: segs={fs['segs']} != {SEGS_PER_MISS} x misses={fs['misses']}"
                         "（不是本模型的每 miss 三段）")
        if "CGC_EXACT_CACHE_VERIFY" in (a.get("env") or {}):
            probs.append(f"閘6 {tag}: env 帶 CGC_EXACT_CACHE_VERIFY"
                         "（每 fill 一次 open+pread ⇒ 開檔不再是 0）")

    fs = ref["fs"]
    if fs["wait_us"] == 0 or fs["batches"] == 0:
        probs.append(f"閘5 {ref['tag']}: 參考臂沒有量（batches={fs['batches']} wait={fs['wait_us']}）")
    if nofill is not None:
        nfs = nofill["fs"]
        nz = [k for k in BUCKETS if nfs[k]] + (["batches"] if nfs["batches"] else [])
        if nz:
            probs.append(f"閘5 {nofill['tag']}: NOFILL 臂的桶不是 0（{','.join(nz)}）"
                         "⇒ 陰性對照失敗：有東西在閘的下游被計時")
        if nofill.get("side", {}).get("read_mib", 0) != 0.0:
            probs.append(f"閘5 {nofill['tag']}: read_mib={nofill['side'].get('read_mib')} != 0"
                         "（NOFILL 說「不讀位元組」）")
    else:
        probs.append("閘5 沒有 NOFILL 臂 ⇒ 沒有陰性對照（拿不到 fill 的上界）")
    return ("REFUSE" if probs else "SPLIT"), ref, nofill, probs


def per_miss(fs, key):
    return fs[key] / fs["misses"] if fs["misses"] else float("nan")


def render(arms, ref, nofill):
    L = []
    L.append("=" * 108)
    L.append("  fill-split（CGC_FILL_SPLIT=1）· cell=delivery · 呼叫端線程累計")
    L.append("=" * 108)
    hdr = (f"{'arm':52} {'t/s':>6} {'miss':>6} {'bat':>5} {'job':>6} | "
           f"{'wake_in':>8} {'span':>9} {'wake_out':>8} | {'wait':>9} {'µs/miss':>8}")
    L.append(hdr)
    L.append("-" * 108)
    for a in arms:
        fs = a.get("fs")
        if not fs:
            L.append(f"{a['tag'][:52]:52} {'-':>6}      -     -      - | "
                     f"{'-':>8} {'-':>9} {'-':>8} | {'-':>9} {'-':>8}")
            continue
        ts = f"{a['ts']:.2f}" if a.get("ts") else "-"
        L.append(f"{a['tag'][:52]:52} {ts:>6} {fs['misses']:>6} {fs['batches']:>5} {fs['jobs']:>6} | "
                 f"{fs['wake_in_us']:>8} {fs['span_us']:>9} {fs['wake_out_us']:>8} | "
                 f"{fs['wait_us']:>9} {per_miss(fs, 'wait_us'):>8.1f}")
    L.append("")
    if ref:
        fs, side = ref["fs"], ref.get("side") or {}
        wait_pm = per_miss(fs, "wait_us")
        L.append(f"參考臂 {ref['tag']}")
        L.append(f"  batches={fs['batches']}  misses={fs['misses']}  segs={fs['segs']} "
                 f"jobs={fs['jobs']}（merge 省掉 {fs['segs'] - fs['jobs']} 個 job）")
        L.append("")
        L.append("  桶（μs）              總計        每 miss    每 miss 占比      在不在 wait 裡")
        rows = [
            ("開檔 open()",        None,        None,       "路上沒有 per-fill open（閘 6 已證）"),
            ("madvise（P1）",       fs["madvise_us"],   per_miss(fs, "madvise_us"),   "提交前，呼叫端"),
            ("build（sort/merge）", fs["build_us"],     per_miss(fs, "build_us"),     "提交前，呼叫端"),
            ("advise（rdadvise）",  fs["advise_us"],    per_miss(fs, "advise_us"),    "提交後，**與 worker 重疊**"),
            ("wake_in（喚醒）",     fs["wake_in_us"],   per_miss(fs, "wake_in_us"),   "wait 內"),
            ("span（pread 在飛）",  fs["span_us"],      per_miss(fs, "span_us"),      "wait 內"),
            ("wake_out（喚醒）",    fs["wake_out_us"],  per_miss(fs, "wake_out_us"),  "wait 內"),
        ]
        for name, tot, pm, note in rows:
            if tot is None:
                L.append(f"  {name:20} {'0':>10} {0.0:>11.1f} {'0.0%':>15}   {note}")
                continue
            share = (tot / fs["wait_us"] * 100.0) if note.startswith("wait") else None
            sh = f"{share:>14.1f}%" if share is not None else f"{'—':>14}"
            L.append(f"  {name:20} {tot:>10} {pm:>11.1f} {sh}   {note}")
        L.append(f"  {'wait（=三者之和）':20} {fs['wait_us']:>10} {wait_pm:>11.1f} "
                 f"{100.0:>14.1f}%   wait 總和（恆等式已驗）")
        L.append(f"  {'wait_max（單批最久）':20} {fs['wait_max_us']:>10} "
                 f"{'—':>11} {'—':>15}   最慢的一批")
        L.append("")
        L.append(f"  每批次：{fs['wait_us'] / max(fs['batches'], 1):.0f} μs 的等 / "
                 f"{fs['misses'] / max(fs['batches'], 1):.2f} miss / "
                 f"{fs['jobs'] / max(fs['batches'], 1):.2f} job（worker 數是 arm env 決定的）")
        if side.get("eb_us"):
            L.append(f"  EBTIMER（包住整個 ensure_batch）總計 {side['eb_us']} μs"
                     f"（{side.get('eb_lines', 0)} 行）⇒ fill 的等佔 {fs['wait_us'] / side['eb_us'] * 100:.1f}%")
        if side.get("read_mib") is not None:
            per_read = 0.0
            L.append(f"  read_mib={side['read_mib']} MiB / file_reads={side.get('file_reads')} "
                     f"/ pread_usec={side.get('pread_usec')} μs（**含 prefill slab 串流**，不是純 decode）")
        if nofill and nofill.get("ts") and ref.get("ts"):
            step_a, step_n = 1000.0 / ref["ts"], 1000.0 / nofill["ts"]
            L.append("")
            L.append(f"  上界（對 NOFILL 臂的牆鐘差）：{step_a:.2f} → {step_n:.2f} ms/step "
                     f"⇒ fill 全部 {step_a - step_n:.2f} ms/step"
                     f"（= 每 miss {1000 * (step_a - step_n) / (fs['misses'] / 384.0):.2f} μs）")
            L.append(f"  ⚠ NOFILL 是 timing-only（輸出是垃圾）⇒ 它的 {nofill['ts']:.2f} t/s 不是能力讀數，只用差。")
    return L


def run(dirpath, ref_substr=None, quiet=False):
    arms = load_matrix(dirpath)
    verdict, ref, nofill, probs = judge(arms, ref_substr)
    if not quiet:
        for line in render(arms, ref, nofill):
            print(line)
        print()
        if probs:
            for p in probs:
                print("  ✗ " + p)
            print(f"VERDICT: REFUSE（{len(probs)} 條結構閘沒過；桶分解不可引用）")
        else:
            print("VERDICT: SPLIT（恆等式與七條結構閘全過；桶分解可引用）")
    return verdict, probs, ref, nofill


# ─────────────────────────────────────────────────────────────────────────────
# 自測
# ─────────────────────────────────────────────────────────────────────────────
def _fake_arm(tag, fs=None, cell=DELIVERY, ok=True, ts=10.6, read_mib=4639.0, env=None):
    return {"tag": tag, "env": env or {"CGC_FILL_SPLIT": "1"}, "contract": {"cell": cell, "ok": ok},
            "attribution": "none", "ts": ts, "ts_sd": 0.5, "fs": fs, "side": {"read_mib": read_mib}}


def _fs(**kw):
    base = dict(batches=3189, incomplete=0, misses=4287, segs=12861, jobs=12816,
                madvise_us=2080, build_us=22355, advise_us=487182, wake_in_us=31265,
                span_us=3896301, wake_out_us=8740, wait_us=3936306, wait_max_us=10550)
    base.update(kw)
    return base


def selftest():
    cases = []

    def chk(name, arms, expect, ref_substr=None):
        verdict, ref, nofill, probs = judge(arms, ref_substr)
        ok = verdict == expect
        cases.append((ok, name, verdict, expect, probs[:2]))

    good = [_fake_arm("prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1", fs=_fs()),
            _fake_arm("prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1;CGC_EB_NOFILL=1",
                      fs=_fs(batches=0, misses=3909, segs=0, jobs=0, madvise_us=0, build_us=0,
                             advise_us=0, wake_in_us=0, span_us=0, wake_out_us=0, wait_us=0,
                             wait_max_us=0), ts=13.75, read_mib=0.0,
                      env={"CGC_FILL_SPLIT": "1", "CGC_EB_NOFILL": "1"})]
    chk("健康的一對（A + NOFILL）", good, "SPLIT")

    bad = [_fake_arm(good[0]["tag"], fs=_fs(wait_us=int(3936306 * 1.12))), good[1]]
    chk("恆等式破（wait 對不上三個桶）", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"], fs=_fs(incomplete=2)), good[1]]
    chk("incomplete > 0 ⇒ 不可判", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"]), _fake_arm(good[1]["tag"], fs=_fs(batches=0, wait_us=0),
                                                read_mib=0.0, env={"CGC_EB_NOFILL": "1"})]
    chk("NOFILL 臂的桶不是 0（陰性對照失敗）", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"]), _fake_arm(good[1]["tag"], fs=_fs(read_mib=4639.0),
                                                read_mib=4639.0, env={"CGC_EB_NOFILL": "1"})]
    chk("NOFILL 臂 read_mib != 0", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"], fs=_fs(jobs=13000)), good[1]]
    chk("jobs > segs（merge 只能減少）", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"], fs=_fs(segs=9999)), good[1]]
    chk("segs != 3 x misses", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"], cell="matrix"), good[1]]
    chk("不是交付 cell", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"], env={"CGC_FILL_SPLIT": "1", "CGC_EXACT_CACHE_VERIFY": "1"}), good[1]]
    chk("arm 帶 CGC_EXACT_CACHE_VERIFY（開檔不再是 0）", bad, "REFUSE")

    bad = [_fake_arm(good[0]["tag"], fs=None), good[1]]
    chk("參考臂沒有 CGC-FILLSPLIT 行", bad, "REFUSE")

    bad = [good[0]]
    chk("沒有 NOFILL 臂 ⇒ 沒有上界", bad, "REFUSE")

    npass = sum(1 for ok, *_ in cases if ok)
    for ok, name, got, want, probs in cases:
        print(f"  {'✓' if ok else '✗'} {name}: {got}（期望 {want}）"
              + ("" if ok else f"  ← {probs}"))
    print(f"\n自測 {npass}/{len(cases)}")
    return 0 if npass == len(cases) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="矩陣目錄（含 ab.json 與 ab.logs/）")
    ap.add_argument("--arm", default=None, help="參考臂的 tag 子字串（預設第一個非 NOFILL 臂）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if not a.dir:
        ap.error("--dir 或 --selftest 擇一")
    verdict, probs, _, _ = run(a.dir, a.arm)
    sys.exit(0 if verdict == "SPLIT" else 1)


if __name__ == "__main__":
    main()
