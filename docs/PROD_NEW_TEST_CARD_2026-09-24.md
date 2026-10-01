# prod-new 標準測試卡（2026-09-24）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **MTP-on** 數字屬**另一個輸出函數**，**不可與 MTP-off 互比**
> （含任何「MTP 加速 X%」「MTP-on ≥ MTP-off」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**（dated 產物不回改）。

> **性質**：llama-bench 完整側的統一測試口徑。**所有速度 runner（commit_bench、llama_bench_matrix、A/B）都以此為準**——commit 標題帶的 prefill/decode 成績、以及跨時間比較的數字，都出自這張卡。
> **為什麼需要**：decode 10-14 之間任何跨時間比較都是環境噪音（launch-to-launch ±20%、swap 只累積不回收、thermal 節流），沒有統一形狀 + 歸因行，數字不可比（§EN-473 / ABBA 協議）。

---

## 1. 硬體與模型

| 項 | 值 |
|---|---|
| 機器 | MacBook Air M4 / 16GB（Apple Silicon） |
| 模型 | `models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf`（13030 MiB） |
| 結構 | 40 trunk + 1 MTP head / 256 experts / 8 active / MoE IQ3_XXS + dense IQ4X |
| build 指紋 | server=`054fb22f04a0`、libllama-server-impl=`60fb7910a8bb`（每次測試記實際 digest） |

## 2. llama-bench 命令（shape 完整側）

```
llama-bench -m models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf \
  -ngl 99 --load-mode none -t 8 -expert-cache 8589934592 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  -b 5632 -ub 5632 -p 2048 -n 128 -d 512 -r 3 -o json \
  --warm-skip 64 --fixed-fill-seed 1
```

> **2026-09-30 · `delivery-mmap` 為何是一個 cell 而不是一個 arm 旋鈕（operator 裁定）**
>
> L25-1 的 SECONDARY（`CGC_SERVER_LOAD_MODE=mmap`）09-30 兩臂都被拒：A 臂被壓縮機閘擋（BUSY, rc=2），B 臂過了壓縮機卻被**本塊**擋
> （`load_mode: 實際 mmap ≠ 權威 none` ⇒ fail-closed）。旋鈕本身確認到達（`arm_env_dropped()` 回空、解析後 `none → mmap`），
> 所以卡點不是「旗標沒送進去」，而是 **load-mode 出現在本塊的嚴格維度裡、不在 `runtime_adjustable` 裡** ⇒ 它在結構上不可能是 arm 級增量。
> ⇒ 要量它只能宣告一個 cell，這就是這一格：它與 `delivery` **只差 `load_mode`**（其餘維度逐字相同），預設 cell 與 `delivery`
> **一個字都沒動**，而指名不存在的 cell 仍然 fail-closed（`resolve_cell` 不退回預設）。
>
> 為什麼值得有這一格：`mmap` 下模型頁是 OS 可回收（`budget_preflight.py:59` 把 model resident 視為 0），而本機最常擋跑的預算項是
> 「Metal 駐留」那 7899 MiB（`Backup/p1_rbfeed_2026-09-30` 那兩跑已示範）。它是唯一能在**不動模型、不動量化**的前提下改變「足跡」
> 這一項的 cell 級欄位——而 operator 09-30 已裁定量化不做。
>
> ⚠ **09-30 當日實跑（這一行是量到的，不是推的）**：
> ① **宣告 cell 是必要但不充分**——cell 只宣告**門檻**，值仍要由 `CGC_SERVER_LOAD_MODE=mmap` 供給；
>    只給 `--cell delivery-mmap` 會被本塊擋（`load_mode: 實際 none ≠ 權威 mmap`），兩者一起給才是合規的一趟。
> ② 兩者一起給之後**跑不完**：`--load-mode mmap` ＋ `-expert-cache 8589934592` 在本機（16 GB）
>    **Metal OOM fail-stop**（`command buffer 8 failed with status 5` / `Insufficient Memory`、rc=**−6**、
>    `recovered 0 completed instance`）⇒ 這一格目前在本機**量不到**，不是被否證。
>    要在本機量它，得先宣告一個**更小的 pool**（或換更大的機器）；那是 operator 決策。
> ③ 對照臂（`delivery`、`load_mode=none`）跑通：miss 數/step 中位 **6.0**（IQR 4–12）、
>    有 miss 的層數/step 中位 **6.0**（min 1、max 39）、正規化 **1.92%**；instrument ✅ BOUND
>    （`mm_pub 39/39`、`missmask_step=386`、`missmask_row=64`）。

| 參數 | 值 | 意義 / 依據 |
|---|---|---|
| `-p 2048` | prefill 2048 token | 完整側 prefill 維 |
| `-n 128` | decode 128 token | 完整側 decode 維 |
| `-d 512` | depth 512 | 已入 context 的 token 數 |
| `-r 3` | 3 reps | 每臂 3 次（中位數口徑） |
| `-b / -ub` | 5632 | 最大存活 chunk（6144 OOM 0/5，見 prefill250 段） |
| `-ngl 99 --load-mode none` | 全層 GPU、不 mmap | |
| `-expert-cache 8589934592` | 8 GiB pool | 縮 pool 確定性降速（8G 12.06 → 6G 10.48 → 4G 7.67），故不縮 |
| `--warm-skip 64` | 時鐘在池預熱後才起算 | 沒它就是「冷啟動含在內」的另一個 cell |
| `--fixed-fill-seed 1` | 每個 rep 重播同一條填充流 | `docs/DECODE_STEADY_BASELINE_2026-09-19.md:45`：σ 3.46→1.09。設 0 ⇒ 每 rep 換一條 ⇒量到 cold/steady 混樣（run3 = 4.13/11.85/11.67），那個混樣會被讀成「噪音」 |

### 2.5 machine-readable CELL（driver 唯一讀取處：人讀 §2 命令、程式讀本塊）

> 下面 JSON 是所有 driver（harness bench / commit_bench / llama_bench_matrix）組命令的**唯一齣處**。
> driver 實際組出的命令與本塊不一致 ⇒ **fail-closed 拒跑**。臂專用開關（§4）在 `switches` 的 default 之上覆寫、並把覆寫記錄在產物；`provenance_required` 欄位必須出現（值可為 null，但不能缺席）。

```json
{
  "schema": "prod-new-cell/v1",
  "model": "models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf",
  "cell": {
    "ngl": 99,
    "load_mode": "none",
    "threads": 8,
    "batch": 5632,
    "ubatch": 5632,
    "prompt": 2048,
    "gen": 128,
    "depths": 512,
    "reps": 3,
    "warm_skip": 64,
    "ctx_size": 0,
    "expert_cache_bytes": 8589934592,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
    "fixed_fill_seed": 1
  },

  "cells": {
    "delivery": {
      "ngl": 99,
      "load_mode": "none",
      "threads": 8,
      "batch": 512,
      "ubatch": 512,
      "prompt": 0,
      "gen": 128,
      "depths": 512,
      "reps": 3,
      "warm_skip": 64,
      "ctx_size": 4096,
      "expert_cache_bytes": 8589934592,
      "cache_type_k": "q8_0",
      "cache_type_v": "q8_0",
      "fixed_fill_seed": null
    },
    "delivery-mmap": {
      "ngl": 99,
      "load_mode": "mmap",
      "threads": 8,
      "batch": 512,
      "ubatch": 512,
      "prompt": 0,
      "gen": 128,
      "depths": 512,
      "reps": 3,
      "warm_skip": 64,
      "ctx_size": 4096,
      "expert_cache_bytes": 8589934592,
      "cache_type_k": "q8_0",
      "cache_type_v": "q8_0",
      "fixed_fill_seed": null
    },
    "delivery-ws256": {
  "ngl": 99,
  "load_mode": "none",
  "threads": 8,
  "batch": 512,
  "ubatch": 512,
  "prompt": 0,
  "gen": 128,
  "depths": 512,
  "reps": 3,
  "warm_skip": 256,
  "ctx_size": 4096,
  "expert_cache_bytes": 8589934592,
  "cache_type_k": "q8_0",
  "cache_type_v": "q8_0",
  "fixed_fill_seed": null
 },
 "delivery-ws192": {
  "ngl": 99,
  "load_mode": "none",
  "threads": 8,
  "batch": 512,
  "ubatch": 512,
  "prompt": 0,
  "gen": 256,
  "depths": 512,
  "reps": 3,
  "warm_skip": 192,
  "ctx_size": 4096,
  "expert_cache_bytes": 8589934592,
  "cache_type_k": "q8_0",
  "cache_type_v": "q8_0",
  "fixed_fill_seed": null
 },
 "delivery-reps7": {
  "twin_of": "delivery",
  "ngl": 99,
  "load_mode": "none",
  "threads": 8,
  "batch": 512,
  "ubatch": 512,
  "prompt": 0,
  "gen": 128,
  "depths": 512,
  "reps": 7,
  "warm_skip": 64,
  "ctx_size": 4096,
  "expert_cache_bytes": 8589934592,
  "cache_type_k": "q8_0",
  "cache_type_v": "q8_0",
  "fixed_fill_seed": null
 },
 "delivery-ws192-reps7": {
  "twin_of": "delivery-ws192",
  "ngl": 99,
  "load_mode": "none",
  "threads": 8,
  "batch": 512,
  "ubatch": 512,
  "prompt": 0,
  "gen": 256,
  "depths": 512,
  "reps": 7,
  "warm_skip": 192,
  "ctx_size": 4096,
  "expert_cache_bytes": 8589934592,
  "cache_type_k": "q8_0",
  "cache_type_v": "q8_0",
  "fixed_fill_seed": null
 },
 "default-b512": {
  "ngl": 99,
  "load_mode": "none",
  "threads": 8,
  "batch": 512,
  "ubatch": 512,
  "prompt": 2048,
  "gen": 128,
  "depths": 512,
  "reps": 3,
  "warm_skip": 64,
  "ctx_size": 4096,
  "expert_cache_bytes": 8589934592,
  "cache_type_k": "q8_0",
  "cache_type_v": "q8_0",
  "fixed_fill_seed": null
 },
 "delivery-mmap-p6": {
  "ngl": 99,
  "load_mode": "mmap",
  "threads": 8,
  "batch": 512,
  "ubatch": 512,
  "prompt": 0,
  "gen": 128,
  "depths": 512,
  "reps": 3,
  "warm_skip": 64,
  "ctx_size": 4096,
  "expert_cache_bytes": 6442450944,
  "cache_type_k": "q8_0",
  "cache_type_v": "q8_0",
  "fixed_fill_seed": null
 },
 "delivery-repsplit": {
      "ngl": 99,
      "load_mode": "none",
      "threads": 8,
      "batch": 512,
      "ubatch": 512,
      "prompt": 0,
      "gen": 128,
      "depths": 512,
      "reps": 1,
      "warm_skip": 64,
      "ctx_size": 4096,
      "expert_cache_bytes": 8589934592,
      "cache_type_k": "q8_0",
      "cache_type_v": "q8_0",
      "fixed_fill_seed": null,
      "rep_split": { "of": "delivery", "launches": 3, "cool_to": "NOMINAL", "cool_max_s": 420 }
    }
  },

  "switches": {
    "LLAMA_EXPERT_CACHE_ALLOW_NGL": {
      "default": 1,
      "values": [0, 1],
      "role": "ngl>0 下 expert skip-load / L4 pool 的硬使能：expert_cache_skip_load = (ngl<=0 || ALLOW_NGL || L3_NGL) && !NOGATHER",
      "prerequisite_for": ["CGC_EXPERT_SKIP_READRAW"]
    },
    "CGC_EXPERT_SKIP_READRAW": {
      "stage": "P0",
      "default": 1,
      "enforced": "run_server.sh cgc_swap_guard：未武裝且未用 CGC_SWAP_GUARD=off 宣告 ⇒ 拒跑（exit 2）",
      "values": [0, 1],
      "requires": "LLAMA_EXPERT_CACHE_ALLOW_NGL=1",
      "effect": "skip-load expert 不 read_raw，省 ~10.9 GiB 匿名駐留、swap 增量 -92%（5679→449 MiB）"
    },
    "CGC_POOL_MADVISE": {
      "default": 2,
      "enforced": "同上（cgc_swap_guard，同一道閘）",
      "values": [0, 1, 2],
      "stage_map": {
        "1": "P1：fill（pread）前 madvise(DONTNEED) 丟將被覆蓋頁",
        "2": "P1+P2：fill 前 + evict 時都丟"
      },
      "effect": "減少駐留與重讀"
    },
    "CGC_B_SCHEME": {
      "default": 1,
      "enforced": "同上（cgc_swap_guard；run_server.sh SERVER_ENV 白名單已接，沒列會被靜默丟）",
      "values": [0, 1],
      "effect": "熱門優先替換：不踢錯熱專家，讓 miss 收斂"
    }
  },
  "runtime_adjustable": ["ctx_size", "fixed_fill_seed"],
  "provenance_required": ["engine_md5", "swap_before", "swap_after", "thermal_launch", "thermal_worst", "fixed_fill_seed"],
  "derived": {
    "warm_skip_applied": "產物 n_gen 應 == cell.gen - cell.warm_skip（=64）；不符即「名義有、實際沒有」，fail-closed"
  }
}
```

#### 2.5.4 `cells.delivery-ws256` / `cells.default-b512` — L20-10 的兩個互斥判別格（2026-09-30 線A 新增）

- 由來：L20-10 要分離「量測起點」與「batch」兩個候選，而 `warm_skip`／`batch` 都是**嚴格維度**
  （實測：`--cell delivery --warm-skip 256` 被 contract 拒，只列 `warm_skip: 實際 256 ≠ 權威 64`）
  ⇒ 只能**宣告新格**，不能用 CLI 對齊。
- `delivery-ws256`（B1）：delivery 的一切不變，只把 `warm_skip` 64 → 256。
  判別句：**升到 ~11.5 且 reps 變平 ⇒ 「量測起點」是主因**。
- `default-b512`（B3）：(default) 的一切不變，只把 `batch`/`ubatch` 5632 → 512（保留 `prompt 2048`）。
  判別句：**掉到 ~9.7 ⇒ batch 是主因**。
- 兩者互斥且都只改一個維度 ⇒ 任一格單獨達標即分離成功；都不達標 ⇒ 主因在別處（不是 11.703 vs 8.26 的差）。

#### 2.5.4b `cells.delivery-ws192` — L20-10 的**可滿足**起點格（2026-09-30 線A 新增，取代 `delivery-ws256`）

- 由來（實測，`scripts/check/l2010_verdict.py`）：`delivery-ws256` **不可滿足** —— 它的 shape 是
  `gen=128 / warm_skip=256` ⇒ 計時段 `gen − warm_skip = −128`。引擎語意是「每個 rep 先跑 N 個
  **不計時**的 token，計時的段落是 `gen−N`」（`src/llama.cpp/tools/llama-bench/llama-bench.cpp:474`），
  所以那一格**定義上不存在**：產物自報 `warm_skip_applied=false` 不是「引擎沒套用」，是這條算式的
  必然結果。⇒ 照它跑的 B1 只證明「格不成立」，起點假設**未被測**。
- `delivery-ws192`：delivery 的一切不變，`gen` 128 → 256、`warm_skip` 64 → 192 ⇒
  **計時段仍是 64**（與 `delivery` 逐字相同的權威 row `p0/n64`），只有**起點**不同（每 rep 先warm
  192 個 token 再計時，對照臂只 warm 64）。
- 判別句（跑前寫死，與 `delivery-ws256` 同一家族）：
  **起點是主因 ⇒ 本格升到 ~11.5**（(default) 錨 11.3–11.7 的中值，±5% 內）；
  **本格落在 `10.5 ± 5%`（≈ 對照臂的 10.2–10.3）⇒ 起點不是主因**。
- 為何不改成「`gen=128 / ws=112`」：那會把計時段壓到 16 個 token（樣本變少、離散變大），
  與對照臂不同形 ⇒ 分不開「起點」與「樣本量」。保住 64 就保住「唯一差異＝起點」。

#### 2.5.4c `cells.delivery-reps7` / `cells.delivery-ws192-reps7` — L20-10 的**解析度**孿生（2026-10-01 線A 新增）

- 由來（實測）：§2.5.4b 的一對在乾淨窗裡**兩臂首次都可引用**
  （A `[11.1961, 11.5185, 12.1653]` median 11.5185／B `[11.669, 12.0732, 12.2068]` median 12.0732、
  `attribution=none`、swap 成長 0.0 MiB、thermal NOMINAL），但判詞仍是 `REFUSE`——**A4 可辨識性**：
  `|Δstep| 3.989 ms ≤ 兩臂散布 7.515 ms`。效應（+4.8%）不是不存在，是 **n=3 的逐 rep 散布
  （~1.05–1.09）比它大** ⇒ 在這一對裡量不到那個差。
- 所以宣告兩個**同形孿生**：各自與其基底**只差 `reps`**（3 → 7），其餘維度逐字相同、計時段不變
  （A：`gen 128 − ws 64 = 64`；B：`gen 256 − ws 192 = 64` ⇒ 權威 row 仍是逐字相同的 `p0/n64`）。
  孿生由 `twin_of` 宣告，`cell_contract.validate_reps_twins()` 逐項驗（差一個別維度就 fail-closed；
  `reps` 只能加大 —— 孿生不可用來量比較少的 rep）。
- 判別句（跑前寫死）：**與 §2.5.4b 逐字相同**（B 落在 11.5±5% ⇒ 起點是主因；落 10.5±5% ⇒ falsify），
  加的是**前置**：兩臂的逐 rep 散布要小到讓 `|Δstep| > 散布`（A4）——否則維持 `REFUSE`
  （不可辨識 ≠ 否證）。
- 為什麼是「加 n」而不是動別的維度：`reps` 是兩格的**嚴格維度**，孿生機制只開放
  `reps`／`rep_split` ⇒ 要 n↑ 只能宣告孿生（不能用 CLI 對齊）。
- ⚠ 為什麼不直接改既有兩格：`delivery`／`delivery-ws192` 是**已量測的權威格**（跨時間比較的入口）；
  改它們的 `reps` 等於換格，會讓既有讀數失去可比的格。
- **2026-10-01 07:22 結果（兩格都已實跑，同一個乾淨窗）**：n↑ **沒有**解掉 A4，而且方向意外——
  同一支臂的全 rep max/min 由 **1.0866（n=3）升到 1.1197（n=7）**（B 也由 1.046 → 1.0818）：
  `max/min` 是**極值統計**，樣本愈多愈容易碰到極端值 ⇒ A4 的分母不是 n 能壓的；而**效應本身也消失**：
  n=3 的 +4.8%（A 11.5185／B 12.0732）在 n=7 只剩 **+1.14%**（A 11.7941／B 11.9281），兩臂都落在
  11.5±5% ⇒ 起點在 n=7 下**不再可辨**（W0 的 A 也読 11.804，兩趟一致）。⇒ 這一對的孿生**不是**
  「更多樣本 ⇒ 可判定」的路；要嘛換 A4 的解析度量（中位數差的標準誤／配對 per-rep 比率），
  要嘛以這兩輪為證據否證式結案——**兩者都是 operator 的決定**（看板 L20-10 的 `precondition.block`）。
- **2026-10-01 結案（operator 明文，排除）**：判詞**維持 `REFUSE`**（A 不可引用），結案是 operator 的明文處置——結成
  「**起點在 n=7 下不再可辨**」（n=3 的 +4.8% 是第一 rep 冷啟對 n=3 中位數的拉扯）。以**配對 per-rep 比率**（B/A）現算：
  中位數 `1.0114`、方向 5/7、符號檢定 `p=0.227`、t=1.23 ⇒ 就算換成 A4 的解析度量，這份 n=7 資料也不會變成可判定；
  A4／引用閘門的統計量改革**另立前瞻 charter**（不回填這一格）。看板：L20-10 已 `settled`（`precondition.rerun=duplicate`
  指 `docs/L2010_CLEAN_WS192_20261001.md` 與判詞檔）。
- **前瞻改革已立卡（2026-10-01）**：`scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml`
  ＋ 預註冊全文 `docs/QUOTE_CALIBER_PAIRED_PREREG_2026-10-01.md`（**paired-v1**：配對 per-pair 比率＋兩趟反序＋
  house precision rule；唯讀重播 0 翻轉、A/A 空對照無號、L20-10 兩輪仍拒為採納前提；前瞻生效、0 回填）。
  原始數據：`Backup/l2010_b1b3_2026-09-30/clean_ws192_{A,B}.json`、`clean_ws192_verdict.json`（含
  `diagnostic`）、`docs/L2010_CLEAN_WS192_20261001.md`。

#### 2.5.5 `cells.delivery-mmap-p6` — L25-1 的 B 臂（2026-09-30 線A 新增）

- 由來：`delivery-mmap`（pool 8 GiB）在這台 16 GB 盒子上 **Metal OOM（rc=−6）**，
  而「pool 大小」是格子級欄位（`expert_cache_bytes`）⇒ 宣告一個 **6 GiB** 的同形格子來量。
- 判別句（§8 兩量，計數器端點）：`miss/step` 與「有 miss 的層數」比 A 臂（`delivery`、
  已量 6.0／6.0、正規化 1.92%）下降 ⇒ 引擎側還有槓桿；**否證 ⇒ 25 的引擎側候選窮盡**（量化已被 operator 禁止）。
- ⚠ 這不是放寬口徑：load_mode 與 pool 都寫死在格子裡，contract 照樣 fail-closed。

#### 2.5.1 `cells.delivery` — 交付 cell（2026-09-28 新增）

**為什麼會有第二個 cell。** 在 2026-09-28 之前這裡只宣告**一個** cell，而
`prod_profile.py`（量交付 decode 的入口）用的形狀是 `--prompt 0 --batch 512 --ctx-size 4096`
⇒ `cell contract` 對它 **fail-closed**，於是**交付 cell 自合約生效起就量不到**。
發現它的人是 k=2 vs k=3 的認證（見 `docs/K3_PAIR_CERT_V2_EXECUTABILITY_2026-09-28.md` 的 D8）。

**它不是放寬，是補宣告。** 兩個 cell 的**機器不變量逐項相同**（`ngl 99 / load_mode none /
threads 8 / expert_cache 8589934592 / cache_type_k = cache_type_v = q8_0`，用 `resolve()` 對過），
差別只在量測維度：

| | （預設）prod-new host-prefill | `delivery` |
|---|---|---|
| batch / ubatch | 5632 / 5632 | **512 / 512** |
| prompt | 2048 | **0** |
| ctx_size | 0 | **4096** |
| fixed_fill_seed | 1 | **null** |

`delivery` 的臂是 `prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256`
（＝ registry 的 `prod25-stream`），形狀與 2026-09-20 讀到 **12.57 t/s** 的那次逐字相同
（`Backup/prod_profile/prod_profile_20260920_1230.json` 的 `decode-delivery-anchor`），
且它通過 `derived.warm_skip_applied`：`gen 128 − warm_skip 64 = 64` ✔

**怎麼用**：

```sh
python3 scripts/check/harness.py bench \
    --arm "prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256" \
    --cell delivery --prompt 0 --batch 512 --ctx-size 4096 \
    --warm-skip 64 --fixed-fill-seed 0 --spec-type draft-mtp \
    --charter <charter.yaml> --json <out.json>
```

**兩個不能忘的性質：**

1. **不指名 cell 就是預設 cell** —— 拿交付形狀去跑而沒帶 `--cell delivery`，仍會被拒
   （2026-09-28 已驗證）；閘門沒有變鬆。指名一個**不存在**的 cell 也是 fail-closed，
   **不退回預設**（退回會讓一次命名錯誤變成另一個 cell 的數字）。
2. **兩個 cell 的數字不可互比。** 產物現在會記 `named_cell`（`_cell()`）與
   `contract.cell`，但紀錄不會替你判斷。

**為什麼這件事對 k=2 vs k=3 重要**：預設 cell 的 `-ub 5632` prefill 在 16 GB 盒子上會撞
**Metal OOM**（`CGC-METAL-FAIL: kIOGPUCommandBufferCallbackErrorOutOfMemory`，2026-09-28
可重現兩次），而它建議的逃生口（降 `-ub`／expert cache）正好指向 CELL 裡的**嚴格維度**
⇒ 那個 cell 動不了。`-b 512` 的交付 cell 沒有這個問題。

## 3. prod-new profile env（默認，顯式 env 永遠贏）

| env | 值 | 角色 |
|---|---|---|
| `CGC_SERVER_MTP` | **0** | decode 支柱（MTP off：省 draft 鏈 + verify batch，同模型實測 13-14 t/s；MTP on 走 prod25-stream 血統） |
| `CGC_SERVER_DENSE_IQ4X` | 1 | dense 走 IQ4X |
| `CGC_SERVER_OA_ASYNC` | 1 | 異步 |
| `CGC_SPAC` / `CGC_SPAC_ALPHA` | 1 / 0.75 | 專家緩存替換策略 |
| `CGC_MM_BITIDENT` | 1 | bit-identical 支柱 |
| `CGC_SERVER_NO_SEQ_RM_PROBE` | 1 | |
| `CGC_SERVER_PREFIX_REUSE_CKPT` | 1 | prefix reuse（MTP off 下 no-op，保留為顯式一致） |
| `LLAMA_EXPERT_CACHE_ALLOW_NGL` | **1** | **通用 SERVER_ENV（run_server.sh:1429，所有 profile 都帶）——L4/skip-load 使能條件**：`expert_cache_skip_load = (ngl<=0 || ALLOW_NGL || cgc_l3_ngl) && !no_gather`。**P0（skip-readraw）要生效，這條必須=1**（值語意，`0` 是關，讀者解析值不測存在） |
| `CGC_EXPERT_SKIP_READRAW` | **1** | P0（**預設開**；砍 ~10.9 GiB 匿名副本＝超訂根因，開了之後 8 GiB pool 才裝得進 16 GB。要跑關掉的 A/B：`CGC_SWAP_GUARD=off CGC_EXPERT_SKIP_READRAW=0`） |
| `CGC_POOL_MADVISE` | **2** | P1+P2（**預設開**） |
| `CGC_B_SCHEME` | **1** | 熱門優先替換（**預設開**；`SERVER_ENV` 白名單 2026-09-25 才接上——沒接之前設了會被靜默丟） |
| `CGC_PREFILL_STREAM` | 1 | prefill250 支柱（大 chunk 走 whole-layer slab） |
| `CGC_GATHER_SLAB_CAP` | 256 | slab 裝得下全部 256 experts |
| `CGC_SERVER_MTP_N_MAX` | 3 | MTP on 時 n_max 顯式釘 3 |
| `SERVER_BATCH` / `SERVER_UBATCH` | 5632 | |
| `CTX` | 8192 | |
| `CGC_EXPERT_CACHE_BYTES`（內部 `BUDGET`） | 8589934592 | 8 GiB pool |
| server 參數 | `-t 8 --temp 0.4` | |

### 3b. 其餘通用 SERVER_ENV 默認（run_server.sh SERVER_ENV 段，prod-new 未覆寫）

| env | 值 | 角色 |
|---|---|---|
| `LLAMA_EXPERT_CACHE_L4_SKIP_LAYER0` | 0 | layer0 在 pool（40 層全 pool；=1 是**數值變更**，需自帶 M1/M2/M3 參考） |
| `LLAMA_EXPERT_CACHE_WORKERS` | 8 | fill 並行度（唯一 concurrency knob；57363 preads×1.73ms 的綁定項） |
| `CGC_WAKE_POLL_US` | 15 | 固定常數 |
| `CGC_EVICTED_RING` | 0 | prefetch 策略固定（step 默認；hist 已移除，opt-in 用 CGC_PREFETCH_SRC=hist） |
| `CGC_N_CB` | 8 | command buffer 深度（§8.93 cb8 sweet spot） |
| `CGC_SERVER_AUTO_ANCHOR` | 0 | 默認關（oracle 實測 anchor ON→echo loop） |
| `CGC_SERVER_DEFAULT_MARKER_STOPS` | 1 | client 沒給 stop 時補 ChatML marker stops |

> 未帶（prod-new 默認不設）：`CGC_FORCE_TEMP0`、`CGC_DOWN_COMBINE`、`CGC_DC_MULTITOK`、`CGC_HOOK_PROFILE` —— 全是 opt-in（if 條件才加）。
> 校準方法：`CGC_DUMP_ENV=1` 的解析輸出是唯一權威——測試卡任何一行與之不符，以 dump 為準。

> 顯式 `0` 不是「不設」：讓 P0/P1/P2 開關在 profile 裡**有名字**。注意下游白名單只傳非 0 值，`CGC_DUMP_ENV=1` 在關閉時看不到這兩行——那是「關」的正常表現。
>
> 2026-09-25：P0/P1/P2 由「顯式 0＝預設關」翻成「**預設開＋沒開就拒跑**」。理由不是 A/B 結果（那一輪量測本身不可比，見 `docs/P0_P1P2_ATTRIBUTION_2026-09-25.md`），
> 而是預設值本身：超訂 4838 MiB 的根因是那 10.9 GiB 匿名副本，而 8 GiB pool 是 decode 的硬需求（8G 12.06 → 6G 10.48 → 4G 7.67，縮 pool 確定性降速）。
> ⇒ 強制閘（`cgc_swap_guard`）與 `test_run_server_swap_guard.py`（三態＋順序）成對存在；閘在 `CGC_DUMP_ENV` **之後**，否則會把量測入口自己的解析路徑擋死。

## 4. 臂差異開關（A/B 用）

| env | 值 | 效果 |
|---|---|---|
| `CGC_SPAC_HOT=1` | B 真臂 | 踢 `spac_count`（累計路由次數，永不衰減）最低者 → 熱門優先替換（命中 143 槽可覆蓋 98.9% 路由） |
| `CGC_SERVER_MTP=1` | MTP on | 對照 decode 支柱（走 prod25-stream） |
| `CGC_EXPERT_SKIP_READRAW=0`（+`CGC_SWAP_GUARD=off`） | P0 對照 | P0 自 2026-09-25 起是**預設**，所以 A/B 的差異臂是「關掉它」；經 `harness bench` 則寫成 `--arm 'prod-new:!CGC_EXPERT_SKIP_READRAW=0'`（`!` = 已宣告，進產物） |

## 5. 量測紀律（強制，所有測試者含兩個 Agent 與 MainAgent）

### 5.0 每一臂必須**同時測並同時報 prefill + decode**

- llama-bench 完整側天然出兩行：`pp p=`（prefill）與 `tg p=`（decode）。
- **禁止只報 tg**：任何報告 / commit 標題 / 結論必須同時給 `prefill=X / decode=Y`。
- 只報 decode 的讀數視為**不完整產物**，不可引用（活例：2026-09-24 早上的單段 ABBA 只抓了 tg，prefill 行被 grep 丟掉）。

### 5.1 每一個報出的數字必須帶 thermal state + swap state 標注

- 格式（每個 prefill/decode 數字旁必須出現，缺標注 = 不可引用）：

```
prefill=X t/s / decode=Y t/s
thermal: launch=MODERATE worst=HEAVY hist={NOMINAL:n, MODERATE:n, HEAVY:n}
swap:    launch=5712 MiB end=6320 MiB worst=8318 MiB growth=+608 MiB
attribution: thermal / swap / both / none        # memory_pressure.py 判定
```

- **HEAVY 或 swap growth > 500 MiB 的讀數標記為污染**，只能當診斷價，不可進錨點 / commit 標題。

### 5.2 機器內嵌（產物契約）

- matrix 產物 json 已內嵌 `thermal` / `memory` / `attribution` / `rows`（含 pp+tg 兩行）——**不得人肉重打**。
- 引用任何產物時，標注必須**取自該產物欄位**，不許手寫或跨產物借用。

### 5.3 既有規則（保留）

- 任何「慢了 X%」的結論，**先過 thermal/swap 歸因門**再下（memory_pressure.py，selftest 14/14）。
- 交錯協議：A/B 用 ABBA 交錯（A1 B1 A2 B2 A3 B3）+ 配對 per-rep 比率中位，**不允許先跑完 A 再跑 B**。
- 啟動前窗口守門：無殘留 llama-server、swap 水位、thermal NOMINAL、budget 預檢。

## 6. 成績錨點（本卡口徑，llama-bench 完整側）

| 臂 | prefill t/s | decode t/s | hit% | attribution | 來源 |
|---|---:|---:|---:|---:|---|
| ctrl（prod-new 冷機） | 259.91 ± 7.12 | 11.90 ± 0.15 | 96.4 | — | /tmp/abba_p0b_ctrl |
| p0+B 單臂 | 218.53 ± 26.79 | **11.37 ± 0.24** | 96.2 | both（swap+1079 MiB、thermal HEAVY 8） | /tmp/commit_bench_p0hot3 |
| ctrl（同場首臂，髒前） | 162.5 | 10.73 | — | — | commit f2effe569 |

**解讀規則**：
- decode 在同帶內（±0.8 內）即可複現；跨環境直接比絕對值沒有意義。
- prefill 跨時間不可比（環境差 60%），必須同場交錯才有結論。
- 目標：prefill 250+（冷機可達）、decode 25+（現 ~48%，缺 CPU/GPU 重疊 + MoE gather 頻寬）。

## 7. 使用入口

> **唯一對外門（2026-09-25 裁定）**：通用測量走 `harness.py bench`、commit 前走 `commit_bench.py`。
> `llama_bench_matrix.py`／`prod_matrix.py` 是 **internal** 驅動：直跑它們、cell 與 §2.5 不符會被合約
> 當場拒跑；即使跑通，未帶完整 §2.5 口徑標注的數字＝**「口徑不明」**，不可寫進 commit 標題或跨時間比較。

2026-09-25 新增：**cell 與三支柱都有強制閘，兩條路各司其職**。

| 閘 | 在哪 | 沒過會怎樣 | 合法繞道 |
|---|---|---|---|
| cell 合約（`cell_contract.py`） | `llama_bench_matrix.run_arm` 拼完命令、執行前 | 拒跑（fail-closed），逐項列出與 §2.5 的差 | 走 `harness.py bench` / `commit_bench.py` 的預設 |
| swap 支柱（P0/P1/P2＋B_SCHEME） | `run_server.sh cgc_swap_guard`（**在 `CGC_DUMP_ENV` 之後**，否則會擋死量測入口自己的解析路徑） | 拒跑（exit 2） | `CGC_SWAP_GUARD=off`（大聲警告），A/B 對照臂就靠它 |
| thermal 冷卻（軟） | `run_server.sh cgc_box_preflight` / `harness.py bench` | 不拒跑：等 NOMINAL 最多 420 s，逾時只警告（那輪不可引用） | `CGC_THERMAL_GATE=off` / `--no-thermal-gate` |
| swap 佔用者提醒 | 同上（`memory_pressure.py --advice`） | 不拒跑：印出超過起跑門檻的 swap 與佔用它的大戶 | —（提醒是重點，不擋啟動） |

```sh
# 單臂（commit_bench 同款）
python3 scripts/check/llama_bench_matrix.py --arms "prod-new:CGC_SPAC_HOT=1" \
  --prompt 2048 --gen 128 --depths 512 --reps 3 --ctx-size 0 \
  --warm-skip 64 --fixed-fill-seed 1 \
  --workdir /tmp/xxx --json /tmp/xxx/result.json

# A/B 對比（同場）
python3 scripts/check/llama_bench_matrix.py --arms "prod-new,prod-new:CGC_SERVER_MTP=1"

# dry-run 看實際命令
python3 scripts/check/llama_bench_matrix.py --arms prod-new --dry-run
```
