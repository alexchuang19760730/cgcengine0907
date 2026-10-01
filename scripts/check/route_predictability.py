#!/usr/bin/env python3
"""逐步路由的可預測性 —— 從引擎自己的 demand dump 算兩個數。

WHY. 2026-09-28 的量測把「單次提交為什麼快」定位到：該臂在 decode 期**不做任何 fill**
（`gather (ensure) hits=0/0`），池凍結在 prefill 的結果上 ⇒ 提交時每步只有 **57.1%** 的被選中專家
已駐留；而誠實的分段臂（每層提交前 fill）逐步駐留是 **96.3%**（`decode/pool hits=124106/128920`，
另有 ≥4 個獨立場次在 96.2–96.7%）。
差的這 39 個百分點就是「正確的單次提交」必須靠**預測**買回來的量 —— 因為一步之內路由是
逐層串聯的（第 L 層的選取要等第 L−1 層算完），所以不可能在同一趟裡提早得知；
唯一能提早知道的只有**前幾步**的路由。

這支工具回答兩件事，都只用離線重放（不佔 GPU、不受 thermal 影響）：
  A. **k 步重疊** p(k)：第 t 步的需求有多少落在前 k 步的聯集裡（k=1,2,4,8,16）。
     這是「預測器手上最多有多少資訊」的上界。
  B. **填前 k 步** 策略的命中率：模擬「在該步之前，只把前 k 步的需求填進池」，
     池容量用 dump 自己的 `cap <layer> <slots>`，逐層 LRU。
     這是「一次提交」設計真正能拿到的駐留率，可直接與 96.3%（誠實）／57.1%（無 fill）對照。

THE GATE. 同一份 dump 先跑 `honest`（邊用邊填的 LRU）⇒ 它的命中率必須落在引擎實測的
96.2–96.7% 附近。**對不上就拒報其他讀數**：那代表這份 trace 的形狀不是交付 cell，
後面每個數都是從未驗證的模型推出來的 —— 這個 repo 的重複病因。

USAGE
    python3 scripts/check/route_predictability.py <demand_dump.txt>
    python3 scripts/check/route_predictability.py --self-test
"""
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path

# 引擎實測（2026-09-28，Backup/g4miss_2026-09-28/ctl_segmented_default.stderr.log 等 ≥4 場次）
HONEST_BAND = (0.962, 0.967)
# 單次提交臂（同樣的池、零 fill）：提交時已駐留比例 = 1 − 0.429
NOFILL_REF = 1.0 - 0.429


class DemandTrace:
    """`cgc-demand v2` 的逐層需求時序。"""

    def __init__(self):
        self.meta: dict = {}
        self.caps: dict[int, int] = {}
        self.demand: dict[int, list[list[int]]] = {}   # layer -> [step] -> ids
        self.n_steps_seen: dict[int, int] = {}
        self.sites: dict[str, int] = {}

    def add_event(self, site: str, layer: int, ids: list[int], demand_before: int) -> None:
        self.sites[site] = self.sites.get(site, 0) + 1
        if site != "B":
            return
        self.demand.setdefault(layer, []).append(list(ids))

    def layers(self) -> list[int]:
        return sorted(self.demand)


def parse_demand_dump(path: Path) -> DemandTrace:
    """只吃 `cgc-demand v2`（v1 的 P 事件 payload 不同，靜默接受會重放一條從未跑過的規則）。"""
    t = DemandTrace()
    for ln, raw in enumerate(path.open(errors="replace"), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            p = line[1:].split()
            if p and p[0] == "cgc-demand":
                if len(p) < 2 or p[1] != "v2":
                    raise ValueError(f"{path}:{ln}: 需要 'cgc-demand v2'，讀到 {line!r}")
                for kv in p[2:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        t.meta[k] = int(v) if v.lstrip("-").isdigit() else v
            elif p and p[0] == "cap":
                t.caps[int(p[1])] = int(p[2])
            continue
        f = line.split()
        if len(f) < 4:
            raise ValueError(f"{path}:{ln}: 事件行太短 {line!r}")
        seq, site, layer, n = int(f[0]), f[1], int(f[2]), int(f[3])
        ids = [int(x) for x in f[4:]]
        if len(ids) != n:
            raise ValueError(f"{path}:{ln}: n={n} 但 payload 有 {len(ids)} 個 id")
        t.add_event(site, layer, ids, seq)
    if not t.demand:
        raise ValueError(f"{path}: 沒有任何 B（demand）事件")
    return t


def kmax_overlap(steps: list[list[int]], ks=(1, 2, 4, 8, 16)) -> dict[int, tuple[float, int]]:
    """(命中比例, 樣本步數)：第 t 步的需求有多少落在前 k 步的聯集裡。"""
    out: dict[int, tuple[float, int]] = {}
    for k in ks:
        hit = tot = 0
        for i in range(k, len(steps)):
            hist: set[int] = set()
            for j in range(i - k, i):
                hist.update(steps[j])
            cur = set(steps[i])
            hit += len(cur & hist)
            tot += len(cur)
        out[k] = (hit / tot if tot else 0.0, tot)
    return out


def simulate(steps: list[list[int]], cap: int, ahead: int) -> tuple[float, float]:
    """(命中率, 每步平均缺口)。

    ahead=0 : 誠實臂 —— 邊用邊填（該步的缺就在該步進來）。
    ahead=k : 單次提交臂的正確版 —— 該步之前只把**前 k 步**的需求填進池，該步本身不填。
    """
    lru: OrderedDict[int, None] = OrderedDict()

    def touch(e: int) -> None:
        if e in lru:
            lru.move_to_end(e)
        else:
            lru[e] = None
            while len(lru) > cap:
                lru.popitem(last=False)

    hit = tot = 0
    for i, cur in enumerate(steps):
        if ahead:
            # 誠實版會在每一步把該步用到的都帶進來；正確的單次提交沒有這個機會。
            if i >= 1:
                hist: set[int] = set()
                for j in range(max(0, i - ahead), i):
                    hist.update(steps[j])
                for e in sorted(hist):
                    touch(e)
        s = set(cur)
        present = len(s & set(lru))
        hit += present
        tot += len(s)
        if not ahead:
            for e in sorted(s):
                touch(e)
    return (hit / tot if tot else 0.0, (tot - hit) / len(steps) if steps else 0.0)


def report(path: Path) -> int:
    t = parse_demand_dump(path)
    layers = t.layers()
    print(f"trace: {path}")
    print(f"  header: {t.meta}")
    print(f"  層數={len(layers)}  每層步數={ {len(t.demand[l]) for l in layers} }")
    if len({len(t.demand[l]) for l in layers}) != 1:
        print("  ⚠ 各層步數不一致；重疊統計仍逐層獨立計算，但總平均會混到不同的 t 範圍")
    tot_ids = sum(len(s) for l in layers for s in t.demand[l])
    print(f"  demand 事件 = {sum(len(t.demand[l]) for l in layers)}；平均 |S_t| = "
          f"{tot_ids / sum(len(t.demand[l]) for l in layers):.1f}")
    print(f"  site 計數 = {t.sites}")

    # ── 閘門：先驗模型 ──
    caps = [t.caps.get(l, t.meta.get("n_slots", 0)) for l in layers]
    if len(set(caps)) != 1 or caps[0] == 0:
        print(f"  ⚠ 各層 capacity 不一致或缺席: {sorted(set(caps))}")
    cap = caps[0]
    h_honest = []
    for l in layers:
        r, _ = simulate(t.demand[l], t.caps.get(l, cap), ahead=0)
        h_honest.append(r)
    h = sum(h_honest) / len(h_honest)
    ok = HONEST_BAND[0] <= h <= HONEST_BAND[1]
    print(f"\n[GATE] honest LRU @ cap={cap} 命中率 = {h*100:.1f}%"
          f"  （引擎實測 {HONEST_BAND[0]*100:.1f}–{HONEST_BAND[1]*100:.1f}%）"
          f"  -> {'PASS（模型可用）' if ok else 'FAIL（拒報其餘讀數）'}")
    if not ok:
        print("  這份 trace 的形狀不是交付 cell（或 cap 不對）⇒ 後面的數不成立。")
        print(f"  參考：無 fill 臂（提交時已駐留）實測 {NOFILL_REF*100:.1f}%")
        return 1

    print("\n[A] k 步重疊（前 k 步的聯集覆蓋第 t 步需求的比例）")
    for k, (p, tot) in kmax_overlap(t.demand[layers[0]]).items():
        per = [kmax_overlap(t.demand[l], ks=(k,))[k][0] for l in layers]
        print(f"  k={k:>2}  逐層平均 {sum(per)/len(per)*100:6.1f}%")

    print("\n[B] 填前 k 步的策略（單次提交臂的正確版會拿到的駐留率）")
    base_h, base_m = simulate(t.demand[layers[0]], cap, ahead=0)
    per0 = [simulate(t.demand[l], t.caps.get(l, cap), ahead=0) for l in layers]
    print(f"  ahead=0（誠實，邊用邊填）  命中 {sum(p[0] for p in per0)/len(per0)*100:6.1f}%"
          f"   缺口 {sum(p[1] for p in per0)/len(per0):5.2f}/步")
    ref = []
    for k in (1, 2, 4, 8, 16, 32):
        per = [simulate(t.demand[l], t.caps.get(l, cap), ahead=k) for l in layers]
        hk = sum(p[0] for p in per) / len(per)
        mk = sum(p[1] for p in per) / len(per)
        ref.append((k, hk, mk))
        print(f"  ahead={k:<2}（只用前 {k} 步）      命中 {hk*100:6.1f}%   缺口 {mk:5.2f}/步")
    print(f"\n  對照：無 fill 臂實測（提交時已駐留）{NOFILL_REF*100:.1f}%")
    print(f"  判讀：ahead=1 的命中率就是「正確的單次提交」在只有前一步資訊時的駐留率；")
    print(f"        它與 honest 的差距 = 必須用重算補上的量。")
    return 0


def self_test() -> int:
    import tempfile
    fails = []

    def expect(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.txt"
        # 兩層，cap=2；層 0 的需求是周期性的（可預測），層 1 每步都是全新的（不可預測）
        lines = ["# cgc-demand v2 layers=2 experts=8 n_slots=2 zero_slot=0",
                 "# cap 0 2", "# cap 1 2"]
        seq = 1
        for step in range(6):
            a = [1, 2] if step % 2 == 0 else [2, 1]
            lines.append(f"{seq} B 0 {len(a)} " + " ".join(map(str, a))); seq += 1
        for step in range(6):
            b = [step % 8, (step + 4) % 8]
            lines.append(f"{seq} B 1 {len(b)} " + " ".join(map(str, b))); seq += 1
        lines.append(f"{seq} X 0 1 2")
        p.write_text("\n".join(lines) + "\n")

        t = parse_demand_dump(p)
        expect("parse: layers", t.layers(), [0, 1])
        expect("parse: steps per layer", [len(t.demand[l]) for l in t.layers()], [6, 6])
        expect("parse: caps", t.caps, {0: 2, 1: 2})
        expect("parse: sites", t.sites, {"B": 12, "X": 1})

        # 層 0：需求固定在 {1,2}，cap=2。第一步本身必然是 compulsory（池是空的），
        # 之後 5 步全中 ⇒ 10/12。ahead=1 同理（第一步沒有「前一步」）。
        h0, m0 = simulate(t.demand[0], 2, ahead=0)
        expect("layer0 honest hit", round(h0, 6), round(10 / 12, 6))
        h0a, m0a = simulate(t.demand[0], 2, ahead=1)
        expect("layer0 ahead1 hit", round(h0a, 6), round(10 / 12, 6))
        # 層 1：每步 2 個全新 id、cap=2 ⇒ honest 也命中不了（前一步的兩個已被換掉）
        h1, _ = simulate(t.demand[1], 2, ahead=0)
        expect("layer1 honest < 0.2 (no reuse)", h1 < 0.2, True)
        h1a, _ = simulate(t.demand[1], 2, ahead=1)
        expect("layer1 ahead1 < layer1 honest", h1a <= h1, True)

        # 重疊：層 0 的 k=1 應為 100%（集合相同），層 1 為 0%
        o0 = kmax_overlap(t.demand[0], ks=(1,))[1][0]
        expect("layer0 k=1 overlap", round(o0, 6), 1.0)
        o1 = kmax_overlap(t.demand[1], ks=(1,))[1][0]
        expect("layer1 k=1 overlap", round(o1, 6), 0.0)

        # 格式防護：v1 必須被拒
        bad = Path(d) / "bad.txt"
        bad.write_text("# cgc-demand v1 layers=1\n1 B 0 1 5\n")
        try:
            parse_demand_dump(bad)
            fails.append("v1 dump should have been rejected")
        except ValueError:
            pass
        # 格式防護：n 與 payload 不符要拒
        bad2 = Path(d) / "bad2.txt"
        bad2.write_text("# cgc-demand v2 layers=1\n1 B 0 3 5 6\n")
        try:
            parse_demand_dump(bad2)
            fails.append("n/payload mismatch should have been rejected")
        except ValueError:
            pass

    if fails:
        print("SELFTEST FAIL:")
        for f in fails:
            print("  ✗", f)
        return 1
    print("SELFTEST OK（11 條）")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?", help="LLAMA_EXPERT_CACHE_DEMAND_DUMP 產物（cgc-demand v2）")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.dump:
        ap.error("需要一個 dump 路徑（或 --self-test）")
    return report(Path(a.dump))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
