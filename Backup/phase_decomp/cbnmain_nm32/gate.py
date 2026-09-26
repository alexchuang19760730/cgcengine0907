#!/usr/bin/env python3
# [2026-09-26 15:0x] fail-closed thermal 預檢。
#
# 為什麼需要它（實測教訓，2026-09-26 14:58）：
#   harness 內部的 thermal gate 是 **fail-OPEN** —— 它等 `--cool-max-s`（預設 420s），
#   超時仍**照跑**，只把讀數標成「NOT quotable」（`thermal_pressure.py:225-232`）。
#   那等於把「拿不到有效窗口」轉成「這裡有個數字」。
#
#   當天實況：14:58 我啟動時 `thermalpressurelevel=1`，harness 開始等；但**別的 session
#   正在跑 paired MTP 實驗**（`/tmp/armed_fc.sh`：14:57:41 完成 off 臂、接著跑 on 臂），
#   所以 thermal 反而從 1 **升到 2（HEAVY）** ⇒ 等 420s 也不可能 NOMINAL，而且過程是
#   擠在一個已經被佔用的窗口裡。
#
#   本閘 = 借用 `/tmp/armed_fc.sh` 的設計：**非 NOMINAL 就拒跑**（exit 3），
#   不是「照跑但標記」。
#
# 用法：gate.py [timeout_s]   （預設 600）
#   exit 0 = NOMINAL，可以跑
#   exit 3 = 等不到 NOMINAL ⇒ 本輪拒跑（呼籲者必須**不寫 DONE**，改寫 REFUSED_THERMAL）
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "scripts", "check"))
import thermal_pressure as th

timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
r = th.wait_nominal(timeout_s=timeout, poll_s=15.0)
print(f"[thermal gate] {r['label']} waited={r['waited_s']:.0f}s readings={r['n_readings']} "
      f"ok={r['ok']}", flush=True)
if not r["ok"]:
    print("[thermal gate] REFUSED —— 非 NOMINAL ⇒ 本輪拒跑（不產出『不可引用』的數字）", flush=True)
sys.exit(0 if r["ok"] else 3)
