# 壓縮機閘門 + 每行程歸屬（2026-09-26）

**兩件事**：(1) 把窗口閘從「swap 存量 < 2048 MiB」換成「壓縮機安靜度」；(2) 把一臂 18–28 GB 的
壓縮流量歸屬到具體行程。兩者都在同一顆引擎（digest `1ca685490`）上完成，產物都帶 box 標籤。

---

## 一、窗口閘：stock → flow

### 為什麼（校準，同一天同機器）

| 盒況 | swap 存量 | Compressions | Pageouts |
|---|---:|---:|---:|
| 閒置（12.6 s 取樣，本日 12:35） | **7993 MiB** | **0.00 MiB/s** | **0.00 MiB/s** |
| 同一臂（prod-new，r=3） | ~7862 MiB | **245–540 MiB/s** | 21–61 MiB/s |

四個數量級分離，而**存量完全分不出這兩種盒況**。舊閘（`swap_used < 2048`）在兩種情況下都會判錯：
它拒跑「8 GB 陳年 swap + 壓縮機安靜」（今天 12:59 那一臂就是這種），又放行「小 swap + 壓縮機忙」
（09-26 那次 8.31 t/s 的讀數就是這種：起跑 swap 只有 4.5 GB）。

### 實作（都預設 fail-closed；讀不到 ≠ 安靜）

| 檔案 | 內容 |
|---|---|
| `scripts/check/compressor_pressure.py` | **新增，單一出處**。`measure/quiet/require` + CLI + 自測。重用 `mem_oversub_probe.vm_snapshot/delta` 與 `memory_pressure._page_size_kb`（16 KiB，不硬編 4096）。門檻 1.0 MiB/s（對忙碌側 ~150–540），「unknown」（取樣 < 2、計數器缺席、dt=0）自成一種判決，絕不當 quiet |
| `mem_oversub_probe.py` | 修兩個缺陷：`PAGE = 4096` → 問 kernel（本機 16384，4× 單位錯）；`delta()` 對**缺席的計數鍵**原本回 0（＝安靜），現在回 None（＝unknown）。各補一個自測 |
| `server_window.py` | 新增 `compressor()` 並成為 `quiet()` 的第四個 term（`require/record/require_first` 全體繼承，`provenance()` 因此多一欄），`decision()` 的 `harness_terms` 多 `compressor` |
| `lane_watchdog.py` | `gate` 的 decider 換成壓縮機；`LAUNCH_SWAP_KILL` 降為標籤（`stress_note` 用），`--max-swap-mb` 只印不比 |
| `arm_two_pass.py` | `gate_environment`：`swap_level` → `compressor_quiet`，另加 `swap_label`（pass=True 的標籤欄） |
| `restart_rerun.py` | `window_state` 的 veto 換成壓縮機，`evidence` 明寫 `swap_is_veto: False` |
| `gate_consistency.py` | 規則 1 改為「三支閘均須走 `compressor_pressure`」，新增規則 1b「swap 存量不得再當閘」（靜態回歸防線） |

### 驗收

| 檢查 | 結果 |
|---|---|
| `compressor_pressure --selftest` | **10/10**；突變（`quiet = True`）⇒ 3 個 fixture 變紅 |
| 其中最關鍵的 fixture | 「**存量 7993 MiB + 流量 0 = QUIET**」——這條在舊閘下必紅 |
| `mem_oversub_probe --selftest` | 11 項（含新增的「缺席 → None，不是 0」） |
| `restart_rerun selftest` | OK（6 個拒跑條件 + 新 fixture：**存量 4096 > 舊線 2048 + 壓縮機安靜 ⇒ OPEN**） |
| `server_window selftest` | PASS |
| `gate_consistency --selftest` | **9/9**（含三個新的必須紅：閘沒走共享模組／`swap_level` 回歸／`> max_swap_mb` 回歸） |
| `gate_consistency`（真實檔） | 新規則 1、1b 皆 PASS；**3 項既有 FAIL 未動**（`--reps` 預設 1、看門狗命名、`budget_gate`） |
| 真實閘 | `PREFLIGHT PASS thermal=NOMINAL free=6086 compressor=quiet (0.00, 0.00 MiB/s) [swap stock 7833 MiB is a label, --max-swap-mb 1024 no longer decides]` |

一句話：**同一個盒子在舊閘下（7833 > 1024）是拒跑的，現在它會跑。** 這正是你要的
「swap 一直長也要能跑」。

---

## 二、18–28 GB/臂的壓縮流量是誰的

### 方法

新工具 `scripts/check/arm_pressure_attrib.py`（自測 11/11；閘＝壓縮機安靜 + 無對手 llama 行程，
其餘標籤；產物寫 engine digest / cell / window provenance）。

它跑**一臂**，並在臂中對**真正在做事的行程**（RSS 最大者，不是 python 父行程 —— 09-26 那次取到
父行程 14.5 MB 的錯）取 4 次 `footprint --swapped`（同一瞬間也對**最大的外來行程**取一次，
作為「不是鄰居」的對照），並全程記 `vm_stat`。

### 讀數（prod-new, r=3, digest `1ca685490`；pp 277.98 / tg 11.46）

| capture | 我方（llama-bench） | 鄰居（Freebuff） |
|---|---|---|
| 0 | footprint 6,644 MiB dirty, **(Swapped) 495 MiB**（`untagged VM_ALLOCATE` 158 + MALLOC 系列 ~110） | footprint 1,319 MiB, **(Swapped) 666 MiB** @250 MB RSS |
| 1 | footprint 17,818 MiB, **(Swapped) 1,319 MiB**（VM_ALLOCATE 426 + MALLOC_LARGE 161） | footprint 1,794 MiB, **(Swapped) 1,026 MiB** @256 MB RSS |
| 2 | footprint 16,284 MiB, **(Swapped) 1,013 MiB**（VM_ALLOCATE 393） | footprint 1,786 MiB, **(Swapped) 271 MiB** @806 MB RSS |

```text
compressions      245–540 MiB/s
decompressions    238–483 MiB/s      <- 與 compressions 幾乎相等
Pages stored in compressor   ~ 0     <- 庫存不漲
```

### 判決

1. **是 churn，不是累積**：壓縮與解壓同速、壓縮機庫存淨變化 ≈ 0 ⇒ macOS 反覆壓同一批頁再解回來。
2. **兩邊都有，但流量是這一臂造成的**：閒置時 0.00 MiB/s，只有臂在跑時才有 245–540 MiB/s。
   我方背 0.25–1.32 GiB，鄰居（RSS 只有 0.25–0.8 GB）背 0.27–1.03 GiB —— **外來行程被壓掉的比它的
   RSS 還多**，那是「我們把它們推去壓縮」的形狀，不是「它們本來就在壓」。
3. **讀取路徑不是槓桿**（與 F_NOCACHE 的結果一致：`pageins` 腰斬、`compressions` 不動）。真正的自
   變數是**我們自己的足跡**：8–9 GiB dirty 匿名 + 臂中最高 12.3 GB wired，這個壓力讓壓縮機去處理
   全機（包含桌面 app 的頁）。所以下一步應該問的是「**哪個旋鈕能縮 wired/匿名足跡**」，而不是
   「fill 怎麼讀」。
4. 反面（可證偽）：若 idle 也有 ~100 MiB/s 的流量，或鄰居的 swapped 在臂前後不變，則「流量由臂
   造成」就不成立。今天 idle 是 0.00，且鄰居的 stock 在臂中隨時間變動（666→1026→271）。

**順帶一個對照**：這一臂的起跑 swap 是 **7862 MiB（舊線 3.8×）**，量到 tg **11.46**；而 09-26 那次
被舊閘放行、起跑 swap 只有 4.5 GB 的臂量到 **8.31**。存量大 ≠ 慢，壓縮機忙才是。

---

## 三、第七個「儀器說謊」：`harness.py` 的 swap 行（同一族，順手一起修）

**症狀**（14 支臂全部）：`swap: launch=None end=None worst=None growth=+0 MiB`，而同一支臂的
`attribution.swap_growth_mb` 寫著 2137.75 —— 兩個數字互相矛盾，而前者讀起來像判決（「沒有換頁問題」）。

**根因**：它讀 `m.get("launch_swap")`／`"end_swap"`／`"worst_swap"`，但 `memory` 的實際結構是
`launch.swap_used_mb`／`end.swap_used_mb`／`worst.max_swap_mb` ⇒ 三個全 None ⇒ growth = 0 − 0。
這行**從來沒印過真值**。

**修法（兩行一起）**

| # | 內容 |
|---|---|
| 1 | `_swap_line(memory)`：讀真實鍵；**只有兩端都讀到才算 growth**；缺席印 `growth=n/a (unpopulated — use attribution.swap_growth_mb)`，**永不印 `+0`**（半讀也一樣） |
| 2 | `_swap_stock_line(advice)`：`[swap] verdict=advise …` → `[swap-stock] 標籤（不是起跑閘）…`；`memory_pressure.swap_advice` 的 docstring/措辭同步（它判的是 stock，閘是 flow），並寫進「存量大本身不降速（實測 7862 MiB 存量 + 0.00 MiB/s 流量 ⇒ tg 11.46）」 |
| 3 | **真正的閘**接上：`_box_gate` 多印 `[compressor] … <- 起跑閘`，busy ⇒ 拒跑（`CGC_WINDOW_OVERRIDE=1` 可改為照跑但把狀態記進產物） |

**驗收**

- 實測同一支臂：現在印 `swap: launch=7862.44 end=8762.75 worst=9060.81 growth=+900 MiB`，與
  `attribution.swap_growth_mb = 900.31` 一致。
- 新增 8 個 fixture，含三個**必須紅**：「缺席不得印 `+0`」「半讀不得印 0」「stock 行不得出現 `verdict=`」。
- 實跑 `_box_gate`：`[swap-stock] 標籤…存量大本身不降速…` + `[compressor] quiet (0.00, 0.00 MiB/s) <- 起跑閘`，
  `refused=None` —— 同一個盒子（存量 8029 MiB）在舊措辭下會被讀成「已飽和」。
- `harness.py selftest` 綠。它原本紅的原因不是這兩行，而是**兩支他線未登錄的啟動器**
  （`cap_oomsweep.py`、`s1_ksweep.py`，都隨 `65c76b8c7` 併入）：照註冊表自己的慣例補上立場
  （owner 留空），不預設它們已 gated。

**同族但未動（他線檔案）**：`spec_cost_curve.py:420,1038` 仍以 `SWAP_START_LIMIT_MB`（2048）做警告語。
它在那裡只是警告（「E 是計數比、不受影響」），但要不要一併改成標籤措辭屬於該線的判斷。

---

## 四、出處與界線

- 產物：`/tmp/attrib3/{attrib.json, summary.json, footprint_*.txt, footprint_foreign_*.txt, vmmap_*.txt}`；
  前一輪（修 sampler 迴圈後、加鄰居對照前）在 `/tmp/attrib2/`。
- 引擎：`build_commit = 1ca685490`，`spec_type = null`（MTP off，正確），`warm_skip 64` 已生效。
- 盒況標籤：window provenance `class = clean`（reclaimable 9316 MB、壓縮機安靜）；臂中 thermal 到
  HEAVY、swap 7862 → 9061 MiB、wired 最高 12.3 GB、`min_free 42 MB`。**這是超訂 regime 的讀數**，
  不是乾淨窗口的讀數；但閘門層面它是「該跑的」，因為起跑時壓縮機是安靜的。
- 未 commit。`gate_consistency` 那 3 項既有 FAIL 不是我這輪引入、也沒去動。
