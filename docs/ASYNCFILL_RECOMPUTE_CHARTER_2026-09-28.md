# async fill ＋ per-expert 重算 kernel：立項卡（2026-09-28 00:1x）

> **一句話**：這一項**不是「還沒動工」，也不是「已判死」**——
> 09-19 判死它的**證明前提**已被 09-20 推翻，本輪讀源碼確認了這一點。
> 所以本卡的第一個產物是「把判決改回未定」，並把真正的關卡從
> **浮點數（不可能）** 移到 **圖建構（成本未知）**。

立項卡：`scripts/check/charters/e-asyncfill-recompute-2026-09-28.yaml`（本檔是它的理由書）。

---

## 0. 為什麼要開這張卡

09-26 結案時把 async fill／重算 kernel 列為「唯一既未被判死、又還沒動工的項」。
**開卡前查證發現這個描述不成立，而且錯在兩個方向。**

### (1) 它實際上被判死過（09-19）

`DECODE_25TPS_TWO_ALGORITHMS_2026-09-19.md:259-272`：

> 「拆成 H/C 兩段再 `concat`，gate/up 的輸出**可以**與單一次 MMID 逐位元相同。
> **但歸約發生在 combine**：今天 `sum_rows(weights * experts)` 對 k 個位置
> **依序**相加；同項不同加法順序 ⇒ IEEE 下不同數。**填 0.0 也救不了**
> （把真值移到後面加）。⇒ 在 M1/M2/M3 的約束下，沒有位元一致且量級足夠的實作。」

### (2) 但那個證明的機制描述是錯的（09-20 已推翻，本輪讀源碼確認）

09-20 §(1)「★ 計畫改變的發現」：`sum_rows` 在 Metal 上是 `simd_sum` **蝴蝶樹**，不是序列相加。

本輪讀 `src/llama.cpp/ggml/src/ggml-metal/ggml-metal.metal:1721-1778` 全本，實際是三層：

| 層 | 行號 | 行為 |
|---|---|---|
| ① 執行緒內 | `:1748-1750` | **跨步**累加 `for (i0 = tpitg.x; i0 < args.ne00; i0 += ntg.x)` |
| ② simdgroup 內 | `:1752` | `simd_sum(sumf)`（蝴蝶，32 lane） |
| ③ 跨 simdgroup | `:1756-1763` | lane0 寫 `shmem_t[sgitg]` → barrier → `simd_sum` |

**⇒ 沒有一層是 09-19 說的「對 k 個位置依序相加」。**

---

## 1. 裁決：兩個設計的位元一致性狀態相反

同一個名字底下其實有兩個設計，長期被混為一談：

| | **D1**：matmul 分段、**combine 保留單一次** | **D2**：combine 也拆（帶 0 歸約、事後增量補算） |
|---|---|---|
| 歸約樹 | `ne00 = k` 不變 ⇒ **同形** | 寬度／序變 ⇒ **不同形** |
| M1 | **可能**逐位元一致 | **不可能** |
| 依據 | 樹形由 `(ne00, ntg.x)` 決定；09-19:262 自認 matmul 逐 expert 位置獨立 | 09-19:263-267 已證（真值被移到後面加） |
| 本卡 | ✅ 範圍內 | ⛔ 排除 |

**⇒ 09-19 的「位元一致性擋死」對 D1 是過強的結論。** 它被證的是 D2。

---

## 2. 真正的關卡被換掉了：不是 IEEE，是圖建構

D1 要成立，只差一件事：

> **C 組（cold）的 matmul 輸出，能否寫回它在 routed order 的原始偏移？**

- 若能（同一 tensor、原偏移）⇒ `src0` 逐位元相同 ⇒ combine 逐位元相同 ⇒ **M1 可達成**。
- 若只能 `concat` 成 `[H | C]` ⇒ 位置序變（routing 是交錯的，不是分組的）⇒ **退回 D2 ⇒ 判死**。

⛔ 而 ggml 的 op 自備 dst，要寫進既有 tensor 的偏移需要 view／scatter，
**這份成本從來沒估過**——它是 D1 的第一個未知數，也是唯一該先回答的。

已知「位元一致又安全」的子集仍然只有 09-19 指出的那一條：
**同層內本來就獨立的工作**（shared expert `ffn_*_shexp`，約該層 FFN 20–25%）⇒ 只蓋 **~1/4 的 cb**。

---

## 3. 位元一致性怎麼驗（合約）

| 項 | 值 |
|---|---|
| 閘門 | `scripts/check/m123_oracle_gate.py` |
| 參考基線 | `Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v7_20260926.jsonl` |
| M1 | 數值一致＝ `row_fnv1a64` 相同（逐位元） |
| M2 | 決策一致＝ `argmax_token` 相同 |
| 通過規則 | `ok == (M1 rate == 1.0 AND M2 rate == 1.0)`，三者永不合併 |
| 可比性 | `config_diffs == []` 才判；≠ [] ⇒ **exit 2（INVALID COMPARISON）** |

三條會把人帶錯的坑，已寫進卡裡：

1. **exit 2 ≠ M1 不過。** 本項勢必新增 env；若新 env 進了 numerics-determining 子集，
   每次都會 exit 2，那不是回歸。開跑前先確認。
2. **基準是「移動」來的，不是修好的。** 09-27 D5 重新基線 v6→v7（commit `825ec07d5`），
   默認 M1 9/9 M2 9/9 M3 9/9；判據是引擎可重現（兩次 dump 位元組相同）。
   ⇒ 若本項需要 `--write-ref`，那是基準移動，須經 operator 並登記。
3. **鍵碰撞時 M1 不是判決。** gate 用 `(step, token_idx, ctx_type)` 當鍵；
   09-28 已踩過「M1 1/14、coverage 8%」其實是鍵碰撞。引 M1 前必先看 coverage。

另需遵守 09-20 §(1) 的逐後端規則：Metal 是蝴蝶樹、CPU 是序列 ⇒
新 op 必須**複刻它所取代的那條鏈的同一棵樹**（照寫 `simd_sum` 即可）。
M1 比的是 GPU-vs-GPU，所以這約束可達成。

---

## 4. 階段（Q1 沒過不得進下一階段）

| 階段 | 內容 | 出口 |
|---|---|---|
| **P0** | 裁決 09-19 前提（本輪已完成） | ✅ 已裁：進 P1 |
| **P1** | **第一個 commit 不是 kernel**：缺席 expert 的貢獻從 `e % ns`（**別人的權重**）換成 0 | M1 9/9 M2 9/9 |
| **P2** | D1 kernel：matmul 分段 ＋ 單一 combine ＋ C 寫回原偏移 | M1 9/9 M2 9/9 |
| **P3** | 測速（交付 cell、MTP off、配對 11.61） | tg ≥ 13.6 |

**P1 為什麼不能跳**：`CGC_B_SCHEME`（`llama-context.cpp:3607-3634`）把缺席 expert
指到 `e % ns` ＝**另一個 expert 的權重**，不是 0 ⇒ 「缺席那一項 ≈ 0」這個補算前提
**現在不成立**。要先換成 zero slot（`llama-expert-cache.cpp:277-282`／memset `:292-316`／
publish `:371-408`）或走 `llama-graph.cpp:2718` 的 `CGC_ZERO_MISS` 塊。
⚠ `zero_slot_enabled()`（`:302-316`）只在 `CGC_VERIFY_DECODE`／`CGC_DRAFT_DECODE`／
`CGC_ZERO_SLOT` 為真 ⇒ gate 要放寬。

**P2 兩個必踩的坑**：`ids` 要用 **slot index** 不是 expert id；有 `CGC_DOWN_COMBINE`
融合快路徑（`llama-graph.cpp:2678-2765`，:2764 直接 return）⇒ 補算必須考慮它，
否則只在沒開融合時正確。可複用最小單位是 `build_lora_mm_id`（`:1567`）；
「單獨建一個 expert FFN」的現成函式**沒有**。

---

## 5. 凍結判準（沿用 09-26 §EN-150，不改）

1. 同 prompt greedy 逐 rep 相同 ＋ `answer_md5` 等於分段臂（不過就退回）
2. 落點 **13.6~14.2 t/s**，**< 13.0 退回**
3. 穩態 miss 率閘門（平衡點 66.1%~114.3%，實測 4.7%~43.0%）
4. mask 成本用**結構判據**：`segs=41` 不變、生產路徑無新增 synchronize；
   不是 ±0.2 ms 的數字（那在噪音底內）

⛔ **14.1~19.9 t/s 是推算不是實測**，三條推算互相印證只增加量級信心。
Q1 通過前不得當收益引用。

★ **fill 是「正確性」前提不是「速度」前提**（`MISS_GATE_STEP3_2026-09-24.md` §0）：
不 fill，缺席 expert 的權重永遠不出現 ⇒ 輸出永遠是 garbage。
⇒ 背景 fill 必做，它決定拿 14.1 還是 19.9，**不決定正負**。

---

## 6. on_fail：判死的話**不准放寬護欄**

若 P2 證明只能 `concat`（⇒ D1 退回 D2）⇒ **判死**，然後：

> **不得放寬 M1/M2/M3 護欄** —— 那是口徑移動不是修好。

判據很清楚：MTP 那次之所以能走「口徑重定義」，是因為 `n_rs_seq` 讓
**計算份數本身改變**（`gdn` 30→60、`conv_input` 30→150）＝不同的輸出函數。
本項若只是**加法順序不同**，那純粹是實作選擇，**不構成放寬理由**。

⇒ 判死後寫 deadend 結案，改回主線 gap/wait 桶。

---

## 7. 未決事項

- **kernel 的 owner 未明文**（09-26 §EN-150 已記，至今未決）。本線（線 A）只出卡。
- P2 的 scatter 成本完全沒估過 —— 它才是決定 D1 值不值做的第一個數字。

---

## 8. 2026-09-29 追加：P1 驗收實測 —— G3 在交付臂上是 no-op

開卡時把 P1 寫成「待做」，且引用的行號有誤。查證後有兩點更正。

### (1) G3 已落地，但不在本路徑上觸發

`CGC_ZERO_SLOT=1`（專用旋鈕，`scripts/run_server.sh:1752-1753`）已於 09-26 進 allowlist，
`zero_slot_enabled()`（`llama-expert-cache.cpp:311-313`）已含它 ⇒ **不必動線 I 的檔**
（推翻開卡時「gate 要放寬」的記載）。binary 已含：
`strings -a libllama.0.dylib | grep -c CGC-G3-ZEROSLOT` = **2**（build 630）⇒ 0 build 即可跑。

實測 `prod-new:CGC_ZERO_SLOT=1`（0 build、交付 cell、`--warm-skip 64 -r 3`、起跑 NOMINAL）：

| 觀察 | 值 |
|---|---|
| env 送達 | `CGC_B_SCHEME=1` ✅、`CGC_ZERO_SLOT=1` ✅ |
| `CGC-G3-ZEROSLOT-TOTAL` | `zero_slot=0 placeholder=0`（印 6 次） |
| `CGC-G3-ZEROSLOT: il=..` 逐層行 | **0 次** |
| tg | 10.31（worst=**HEAVY**、swap growth +2059 MiB）⛔ **不可引用** |

逐層行 **0 次**而 TOTAL 印了 6 次 ⇒ **那段 for 迴圈一次都沒進**（TOTAL 在迴圈外）。
⇒ 不是「沒有 miss」，是 **writer 根本沒運行**。

### (2) 原因（源碼級）

- `cache_slot_table_tensors` 只在 **DECODE graph** 由 `build_moe_ffn` 填
  （`llama-context.cpp:3448-3450`）；
- slot table leaf 是 **if/else ⇒ 預設不建**（`llama-graph.cpp:2491` 原話
  "the leaf is NOT built when the table is"）⇒ prod-new 交付臂**沒有這個 leaf**
  ⇒ map 為空 ⇒ 每一層都被 `continue` 掉。

### (3) P1 的 exit 原本寫錯

源碼註釋（`llama-context.cpp:3768-3772`）原話：「That is **NOT** bit-identical to the
segmented arm, and **it is not meant to be**」—— G3 是**故意**不 bit-identical：
把 unbounded/arbitrary error（別人的權重）換成 bounded/deterministic/interpretable 的 0。
⇒ 要求它 `M1 9/9` 是**原理上不可能達成**的門檻（原文另寫 "A zero slot does NOT make G1 pass"）。
已改為見證行判據（`zero_slot>=0` 且 `placeholder==0`）。

### (4) 下一步（未做，且本輪不該做）

G3 只在 slot table leaf 真的被建的路徑上生效 —— 即 S1 那一組開關
（`MEMORY_S1.md:729`：`CGC_SEG_BATCH;CGC_B_SCHEME;CGC_SLOT_TABLE_GPU;CGC_MISS_MASK*;
CGC_ZERO_SLOT`，那趟 rc=0）。

⛔ 本輪**沒有再跑第二趟**：跑完 swap 由 6266 → 9111 MiB（盒子不歸還），下一趟必然更髒；
而正確的下一趟要帶 S1 整組開關，屬 **S1 線的實驗設計**，不應在本卡內臨時起意。
⇒ 建議由 S1 線在乾淨窗排一次「S1 組 ＋/− `CGC_ZERO_SLOT`」的 A/B。
