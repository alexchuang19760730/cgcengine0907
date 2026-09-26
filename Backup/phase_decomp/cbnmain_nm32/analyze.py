#!/usr/bin/env python3
# [2026-09-26 14:5x] nm32 union/gap 拆分解析器
#
# 讀 Backup/phase_decomp/cbnmain_nm32/{tag}/*.stderr.log 的 `CGC-GPUTIME:` 行。
# 口徑（src/llama.cpp/ggml/src/ggml-backend.cpp:2141-2172, 3035-3050）：
#   wait   = Σ 每段「CPU 輪詢等該段完成」的 wall（ms）
#   busy   = Σ 每段「所有 buffer 的 (GPUEndTime-GPUStartTime)」之和（可 >100% wait，段內 buffer 會重疊）
#   union  = Σ 每段「buffer 的 GPU 執行聯集 span」（同工作量下 GPU 真忙）
#   gap    = Σ (本段最早 start − 上段最晚 end)，只累正數 = 段間空窗（每步重置，不跨步）
#   skipped= 無可用時間戳的 buffer 數 ⇒ 必須為 0，否則整行無意義
#
# 可信度閘（乾跑 sweep3 得到的事實，不是猜的）：
#   nm128 ⇒ skip 67%、且殘餘行報 busy=2872ms / wait=94.7ms（3034%，物理不可能）
#   機制（ggml-metal-context.m:1140-1175）：n_main 大 ⇒ n_nodes_1 小 ⇒ 平行執行緒分不到節點
#   ⇒ 空 buffer ⇒ 無時間戳。所以 skip% 是「n_main 是否偏離安全方向」的直接讀數。
#   nm32 < 默認 64 ⇒ n_nodes_1 更大 ⇒ 不應有空 buffer ⇒ 本臂 skip% 應 ≈ 0（nm16 實測 2/97）。
#
# 判讀：
#   gap   ↓ = 命中目標函數（operator 要消的就是這個）
#   union ↓ = 同工作量下 GPU 更有效率
#   busy  ↓ = 真工作量變了（不該發生；n_main 只改「誰編碼」不改內容 ⇒ 若變了要查）
import json, os, re, statistics as st, sys

WD = os.path.dirname(os.path.abspath(__file__))
ARMS = ["ctl64a", "nm32a", "nm32b", "ctl64b"]
# 可覆寫：analyze.py <wd> <tag1,tag2,...>
# （用途：拿 sweep3 的既有產物乾跑驗證解析器，不再花 GPU）
if len(sys.argv) > 1:
    WD = os.path.abspath(sys.argv[1])
if len(sys.argv) > 2:
    ARMS = sys.argv[2].split(",")

SKIP_MAX = 20.0   # skip% 上限；超過 ⇒ 整臂時間戳不可信
GPU = re.compile(
    r"^CGC-GPUTIME: step=(\d+) segs=(\d+) bufs=(\d+) skipped=(\d+) "
    r"wait=([\d.]+) gpu_busy_sum=([\d.]+) \([\d.]+%\) "
    r"gpu_union=([\d.]+) \([\d.]+%\) gap=([\d.]+) \([\d.]+%\) ms")
DP = re.compile(r"^CGC-DECPROF: step=(\d+) segs=(\d+) layers=(\d+) total=([\d.]+) ms \| "
                r"wait=([\d.]+) \([\d.]+%\) cb=([\d.]+) \([\d.]+%\) submit=([\d.]+) \([\d.]+%\) "
                r"ntok=1 \| layer gpu_sum=([\d.]+) union_sum=([\d.]+) gap_sum=([\d.]+) ms")
# HOOKSPLIT（`llama-context.cpp:7746-7761`）：逐層 hook 的時間拆解。`n` 是**累計**調用數，
# 每 160 次打一行，四欄全部是**累計平均**：
#   pre    = 取 ids 等前置
#   ensure = `ensure_batch`（阻塞填池）⇒ ★ 這就是 operator 要的 **`fill`**
#   drain  = 清理未開始的 prefetch    tail = 收尾
# ⚠ 因為是累計平均，**最後一行的值被 prefill 拉高** ⇒ 取最後兩個窗口的**增量**才是穩態。
HS = re.compile(r"^CGC-HOOKSPLIT: n=(\d+)\s+pre=([\d.]+)\s+ensure=([\d.]+)\s+drain=([\d.]+)\s+"
                r"tail=([\d.]+) us/call \(total ([\d.]+)\)")


def med(xs):
    return st.median(xs) if xs else float("nan")


def collect(tag):
    d = os.path.join(WD, tag)
    lines = []
    for fn in os.listdir(d) if os.path.isdir(d) else []:
        if fn.endswith(".stderr.log"):
            with open(os.path.join(d, fn), "r", errors="replace") as f:
                lines += f.read().splitlines()
    ok, dirty = [], 0
    for ln in lines:
        m = GPU.match(ln)
        if not m:
            continue
        step, segs, bufs, skip, w, b, u, g = m.groups()
        if int(skip) != 0:
            dirty += 1
            continue
        ok.append(dict(step=int(step), segs=int(segs), bufs=int(bufs),
                       wait=float(w), busy=float(b), union=float(u), gap=float(g)))
    total = len(ok) + dirty
    skip_pct = 100.0 * dirty / total if total else 100.0
    # 熱機剔除：step <= 8（第一批圖含 prefill 溢出，sweep3 已見 step=4 髒）
    warm = [r for r in ok if r["step"] <= 8]
    ok = [r for r in ok if r["step"] > 8]
    h = len(ok) // 2
    front, back = med([r["wait"] for r in ok[:h]]), med([r["wait"] for r in ok[h:]])

    dp = []
    for ln in lines:
        m = DP.match(ln)
        if m:
            dp.append(dict(step=int(m.group(1)), total=float(m.group(4)),
                           wait=float(m.group(5)), cb=float(m.group(6)),
                           submit=float(m.group(7)), gpu=float(m.group(8)),
                           union=float(m.group(9)), gap=float(m.group(10))))
    dp = [r for r in dp if r["step"] > 8]

    # HOOKSPLIT：累計平均 ⇒ 取最後兩行的增量窗口，才是穩態的 decode 值
    hs = []
    for ln in lines:
        m = HS.match(ln)
        if m:
            hs.append(dict(n=int(m.group(1)), pre=float(m.group(2)), ensure=float(m.group(3)),
                           drain=float(m.group(4)), tail=float(m.group(5)), total=float(m.group(6))))
    hs_n = hs[-1]["n"] if hs else 0
    hs_fill = hs_pre = hs_drain = hs_total = float("nan")
    if len(hs) >= 2:
        a, b = hs[-2], hs[-1]
        dn = b["n"] - a["n"]
        if dn > 0:
            hs_fill  = (b["ensure"] * b["n"] - a["ensure"] * a["n"]) / dn
            hs_pre   = (b["pre"]    * b["n"] - a["pre"]    * a["n"]) / dn
            hs_drain = (b["drain"]  * b["n"] - a["drain"]  * a["n"]) / dn
            hs_total = (b["total"]  * b["n"] - a["total"]  * a["n"]) / dn

    tg = pp = float("nan")
    lg = os.path.join(WD, tag + ".log")
    if os.path.exists(lg):
        with open(lg, "r", errors="replace") as f:
            t = f.read()
        mm = re.search(r"tg p=0\s+n=\d+\s+d=512\s+->\s+([\d.]+)", t)
        if mm: tg = float(mm.group(1))
        mm = re.search(r"pp p=2048\s+n=0\s+d=512\s+->\s+([\d.]+)", t)
        if mm: pp = float(mm.group(1))

    return dict(tag=tag, n=len(ok), dirty=dirty, warm=len(warm), total=total,
                skip_pct=skip_pct, trusted=(skip_pct <= SKIP_MAX),
                hs_n=hs_n, hs_fill=hs_fill, hs_pre=hs_pre, hs_drain=hs_drain, hs_total=hs_total,
                wait=med([r["wait"] for r in ok]), busy=med([r["busy"] for r in ok]),
                union=med([r["union"] for r in ok]), gap=med([r["gap"] for r in ok]),
                segs=med([r["segs"] for r in ok]), bufs=med([r["bufs"] for r in ok]),
                front=front, back=back,
                dp_n=len(dp), dp_total=med([r["total"] for r in dp]),
                dp_wait=med([r["wait"] for r in dp]), dp_cb=med([r["cb"] for r in dp]),
                dp_sub=med([r["submit"] for r in dp]),
                dp_union=med([r["union"] for r in dp]), dp_gap=med([r["gap"] for r in dp]),
                tg=tg, pp=pp)


res = [collect(a) for a in ARMS]

print("=" * 122)
print("  ★ 完整時間拆解 —— n_main=32（on）vs 64（off）")
print("    tpot = 步總時間(ms) │ CPU 三桶 wait/cb/submit │ GPU 三桶 union/gap/busy │ fill = 填池(us/call)")
print("=" * 122)
print(f"{'arm':<8} {'clean/all':>10} {'skip%':>7} {'可信':>5} "
      f"{'tpot':>7} {'wait':>7} {'cb':>6} {'submit':>7} | "
      f"{'union':>7} {'gap':>7} {'busy':>7} | {'fill':>7} {'tg':>7} {'pp':>7}")
print("-" * 122)
for r in res:
    print(f"{r['tag']:<8} {str(r['n'])+'/'+str(r['total']):>10} {r['skip_pct']:>6.1f}% "
          f"{'OK' if r['trusted'] else 'STALE':>5} "
          f"{r['dp_total']:>7.2f} {r['dp_wait']:>7.2f} {r['dp_cb']:>6.2f} {r['dp_sub']:>7.2f} | "
          f"{r['union']:>7.2f} {r['gap']:>7.2f} {r['busy']:>7.2f} | "
          f"{r['hs_fill']:>7.1f} {r['tg']:>7.2f} {r['pp']:>7.2f}")
print("-" * 122)
print("  （tpot/cb/submit/union/gap/busy 單位 ms，median；fill = ensure_batch us/call，取最後窗口增量）")
print("   （tpot 與 wait 來自 DECPROF 的 ntok=1 decode 行；union/gap/busy 來自 CGC-GPUTIME）")
print("   （clean/all = 乾淨行/全部行；skip% > 20% ⇒ **union/gap/busy 不可讀**（Metal 時間戳不可信），")
print("     但 tpot/cb/submit/fill 仍有效 —— 後四者來自 DECPROF/HOOKSPLIT，不依賴 Metal 時間戳）")

trusted = [r for r in res if r["trusted"]]
# ★ 篩選用「有沒有 DECPROF 數據」而不是「整臂可信」：tpot/cb/submit/fill 不依賴 Metal 時間戳，
#   即使某臂 union/gap/busy 因 skip% 過高而不可讀，它的 tpot/tg 仍然是有效的漂移證據。
#   （2026-09-26 22:2x 實例：ctl64a skip 23.7% ⇒ 舊版直接放棄算漂移界，丟掉了 ctl64a 的 tg 10.17）
has_dp = lambda r: r["dp_total"] == r["dp_total"]   # NaN-safe
# 臂名兼容兩種命名：cbnmain_nm32 用 ctl64a/nm32a…；cbnmain_pair 用 off1/on1/on2/off2
ctl = [r for r in res if r["tag"].startswith(("ctl", "off")) and has_dp(r)]
nm = [r for r in res if r["tag"].startswith(("nm", "on")) and has_dp(r)]
if len(ctl) < 2:
    print("\n⚠ 控制臂樣本 < 2 ⇒ 無法算漂移界，判讀不成立。")
if len(nm) < 1:
    print("⚠ 處理臂全部無數據 ⇒ 本趟無效，需查 n_main 的 skip 機制。")
if any(not r["trusted"] for r in ctl + nm):
    bad = [r["tag"] for r in ctl + nm if not r["trusted"]]
    print(f"⚠ 有臂 STALE {bad} ⇒ 其 **union/gap/busy 不可比**；"
          f"tpot/tg/cb/submit/fill 仍可比（不依賴 Metal 時間戳）")

if len(ctl) == 2:
    print()
    print("--- 控制臂漂移界（同臂頭尾 = 量測噪聲底；Δ 必須超過它才算訊號）---")
    drift = {}
    print(f"{'量':<8} {'ctl64a':>9} {'ctl64b':>9} {'Δ%':>8}")
    for k in ("wait", "union", "gap", "busy", "tg"):
        a, b = ctl[0][k], ctl[1][k]
        pct = (b - a) / a * 100 if a else float("nan")
        drift[k] = abs(pct)
        print(f"{k:<8} {a:>9.2f} {b:>9.2f} {pct:>+7.1f}%")

    nmlab = "+".join(r["tag"] for r in nm) or "-"
    if len(nm) >= 1:
        def avg(rs, k):
            v = [r[k] for r in rs]
            return sum(v) / len(v) if v else float("nan")
        print()
        print(f"--- 處理臂 vs 控制臂均（門檻 = max(控制漂移, 3%)；處理臂 [{nmlab}] n={len(nm)}）---")
        print(f"{'量':<8} {'ctl64均':>9} {nmlab+'均':>9} {'Δ%':>8} {'門檻%':>7}  判讀")
        for k in ("wait", "union", "gap", "busy", "tg"):
            a, b = avg(ctl, k), avg(nm, k)
            pct = (b - a) / a * 100 if a else float("nan")
            thr = max(drift.get(k, 0.0), 3.0)
            sig = abs(pct) > thr
            if not sig:
                verdict = f"在噪聲底內（±{thr:.1f}%）"
            elif k == "gap":
                verdict = "★ 命中目標（消段間空窗）" if pct < 0 else "gap 變大（反向）"
            elif k == "union":
                verdict = "GPU 更有效率" if pct < 0 else "GPU 有效時間變多"
            elif k == "busy":
                verdict = "⚠ 真工作量變了，要查" if abs(pct) > 5 else "輕微波動"
            elif k == "wait":
                verdict = "CPU 等待下降" if pct < 0 else "CPU 等待上升"
            else:
                verdict = "★ 交付速度上升" if pct > 0 else "交付速度下降"
            print(f"{k:<8} {a:>9.2f} {b:>9.2f} {pct:>+7.1f}% {thr:>6.1f}%  {verdict}")

    if len(nm) == 2:
        print()
        print(f"--- 處理臂內部噪聲（{nm[0]['tag']} vs {nm[1]['tag']}；單臂噪音底參考 ≈ ±27%）---")
        print(f"{'量':<8} {nm[0]['tag']:>9} {nm[1]['tag']:>9} {'Δ%':>8}")
        for k in ("wait", "union", "gap", "busy", "tg"):
            a, b = nm[0][k], nm[1][k]
            pct = (b - a) / a * 100 if a else float("nan")
            print(f"{k:<8} {a:>9.2f} {b:>9.2f} {pct:>+7.1f}%")

    print()
    print("--- 步內漂移檢查（前半 vs 後半 wait，median）---")
    for r in res:
        print(f"  {r['tag']:<8} front={r['front']:>7.2f}  back={r['back']:>7.2f}  "
              f"Δ={r['back']-r['front']:>+7.2f} ({(r['back']-r['front'])/r['front']*100 if r['front'] else 0:>+5.1f}%)")

    print()
    print("--- DECPROF 交叉驗證（ntok=1 decode 行；wait 應等於 GPUTIME.wait —— 同源）---")
    print(f"{'arm':<8} {'dp_n':>5} {'total':>8} {'wait':>8} {'cb':>7} {'submit':>7} {'uni_sum':>8} {'gap_sum':>8}")
    for r in res:
        print(f"{r['tag']:<8} {r['dp_n']:>5} {r['dp_total']:>8.2f} {r['dp_wait']:>8.2f} "
              f"{r['dp_cb']:>7.2f} {r['dp_sub']:>7.2f} {r['dp_union']:>8.2f} {r['dp_gap']:>8.2f}")
