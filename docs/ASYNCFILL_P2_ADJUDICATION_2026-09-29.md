# async fill ＋ per-expert 重算 —— P2 判定（M1 可行性 ＋ 成本上界）

- 日期：2026-09-29 00:5x
- 線別：線 A（S1／段邊界）
- 成本：**0 build、0 GPU、0 `src/`**。純靜態源碼判定。
- 卡：`scripts/check/charters/e-asyncfill-recompute-2026-09-28.yaml`（新增 `p2_adjudication` 節）
- 關聯：`ASYNCFILL_RECOMPUTE_CHARTER_2026-09-28.md`、`docs/ASYNCFILL_P1_G3_NOOP_20260929_0035.html`

---

## 0. 一句話結論

**D1 在原理上可以 bit-identical（不判死），但「能不能做」的關卡不是 IEEE、也不是寫回機制，
而是「decode 的圖只建一次，而 miss 集合逐 step 變」—— 這把成本從「隨 miss 縮放」
變成「每層常備 k 個 acc 節點的固定成本」。**

同時：**上一顆 commit（P0）的整段論證作廢** —— 它裁的 `sum_rows` 根本不參與 expert 聚合。

---

## 1. P0 論證作廢（本輪第一個發現）

| | P0 的記載（09-28） | 2026-09-29 查證 |
|---|---|---|
| combine 是哪個 op | `sum_rows`（`sum_rows(weights * experts)`） | ❌ **不是** |
| `ggml_sum_rows` 在 MoE 的實際用途 | — | 只有 `llama-graph.cpp:2589` 的 `weights_sum`（`norm_w` 權重歸一化），**不參與聚合** |
| 真正的聚合 | — | `llama-graph.cpp:2863-2941` 的**左結合 add 鏈** |

全 repo 的 `ggml_sum_rows` 使用點（確認沒有別處做 expert 聚合）：

```
llama-graph.cpp:2086  group_scores（專家分組打分）
llama-graph.cpp:2589  weights_sum（norm_w 歸一化）   ← MoE 裡唯一一處
models/delta-net-base.cpp:348,369 / deepseek32.cpp:345 / deepseek4.cpp:328,335
models/rwkv7-base.cpp:127 / gemma3n.cpp:314,389
```

⇒ P0 verdict 的 (a)(b)(c)（蝴蝶樹、樹形由 `(ne00, ntg.x)` 決定、09-19 反證不適用）
針對的是一個**不參與 combine 的 kernel**，整段作廢。所幸 P0 的**結論方向**（D1 不判死）
仍然成立，但理由完全不同 —— 真正的理由見 §2。

---

## 2. J1 —— M1 生／死

### 2.1 預註冊判準（讀源碼之前寫死）

逐位元相同 **iff** 三條同時成立，任一不成立 ⇒ 判死：

- **(i)** combine 的每一項 `c_i` 與「同批帶了哪些 expert」無關（**列獨立**）；
- **(ii)** C 的值能在 combine 執行前寫回它在原張量裡的**原始偏移**；
- **(iii)** 該寫入精確（不是「把真值加到一個不為 0 的殘值上」）。

### 2.2 判定：**✅ M1 原理上成立（D1 不判死）**，三條全過

**(i) 列獨立 —— 過**

`kernel_mul_mm_id`（`ggml-metal.metal:10697`）：

```metal
const int   im  = tgpig.z;                        // :10723 註釋原話 "// expert"
const int32_t neh1 = tpe_u32[im];                 // :10730
const int   id = ids_i32[im*args.ne21 + r1 + lr1]; // :10748
// NR0 = 64; NR1 = 32; NK = 32 —— :10716-10721 皆為編譯期常量
```

單一 expert 的算術只由 `(ne0, neh1[im], id, tile 座標)` 決定，
**與這次呼叫帶了幾個 id 無關** ⇒ 拆成兩次呼叫，逐 expert 值不變。
`swiglu` 與 `ggml_mul(experts, weights)`（`:2852`）逐元素、逐列 ⇒ 也不跨界。
⇒ **C 組遲到不改動 H 組任何一列的值。**

**(ii) 原位寫回機制存在 —— 過**

- `ggml.h:935-943` `ggml_acc` 註釋原話：`dst = a; view(dst, nb1..nb3, offset) += b`
  ⇒ **byte offset ＋ 累加**，正是「把 C 那一列加回它在 `experts` 的原始偏移」要的東西。
- `ggml_view_2d/3d/4d` 末參是 byte `offset`。
- Metal 有 kernel：`ggml-metal-ops.cpp:343` ＋ `ggml-metal-device.m:1194` supports 表。
  限制 `contiguous_rows` 且 `src0->type == F32`；本路徑 `experts` 為 F32、
  decode 時 `n_tokens=1` ⇒ 單列連續 ⇒ **限制滿足**。
- ⚠ `grep ggml_acc(` 在 `src/llama.cpp/src/` **0 命中** ⇒ 是新路徑，無既有範例可抄。

**(iii) 寫入精確 —— 過**

`ggml_acc` 是 `+=` ⇒ 遲到列必須先是**精確 0**。兩條來源：
(a) 顯式 zero-fill；(b) zero slot（G3，`llama-context.cpp:3745-3815`）讓缺席 expert 的
down 輸出 = 0，`ggml_mul(weights)` 後仍 = 0。IEEE：`+0 + c = c` 對所有有限 c 精確。

⚠ 已知邊界：`+0 + (-0) = +0` ⇒ 若 `c_i` 恰為 `-0`，符號位與原式不同
（數值相等，且 add 鏈下游 `x+(-0) == x+(+0)`）⇒ **非 blocker**。

### 2.3 ⛔ 前提：只在未融合路徑成立

`CGC_DOWN_COMBINE=1` 時 combine 被折進 `kernel_mul_mv_id_down_combine_q3_K_f32`
（`ggml-metal.metal:11886`）內部，該 kernel 自己累加 k 個 expert
⇒ 拆它就**退回 09-19 的論證 ⇒ 判死**。

✅ **resolve-only 實測**：prod-new 的 `CGC_DOWN_COMBINE` = **`<unset>`**（未設）
⇒ 交付臂走未融合路徑（`:2838-2941`）⇒ **J1 適用於交付臂**。

---

## 3. J2 —— 成本上界

### 3.1 預註冊判準

拆成「頻寬項」與「派發項」各給上界，**都標推算**；預期主導項是派發，不是頻寬。

### 3.2 判定

| 項 | 量級 | 結論 |
|---|---|---|
| 頻寬項（j2-a） | ≈ 5 MB/step ⇒ ~0.7 ms @7 GB/s | 可忽略 |
| **派發／靜態圖項（j2-b）** | **312 op/step × 5–10 µs ⇒ 1.6–3.1 ms/step** | **主導，約 1.8%–3.6%**（step ≈ 86 ms @ 11.61 t/s） |

**j2-b 是本輪第二個發現，也比 J1 更決定可行性：**

decode 的圖**建一次、重複用**，而 miss 集合**逐 step 變**
⇒ 不可能「這 step 2 個 miss 就建 2 個 acc 節點」。
唯一靜態可行的形狀：**每層常備 k 個 acc 節點（每列一個）**，
缺席那幾列的 `b` 填真值、其餘填 0（`+=0` 精確、無副作用）。

⇒ **成本由固定的 `k × n_layer` 主導，與 miss 率無關**（k=8、39 層 ⇒ 312 op/step）。

**j2-c：卡上既有的「~12 op/step（0.3 miss/層）」停用。**
`targets.miss_rate_measured` = [0.047, 0.430] ⇒ 每 step 15–134 個 miss；
而靜態圖要求的是**固定 312**。原數字沒有來源，且方向錯（把成本說成隨 miss 縮放）。

⚠ **k 未實測**：源碼兩處註釋互相矛盾 ——
`:2746`「8 down GEMVs + 7 adds」⇒ k=8；`:2929`「n_expert_used-1 = 6」⇒ k=7。
J1 不依賴 k；J2 成本與 k 線性相關。

⛔ 全部是推算：未計 `ggml-alloc` 為 C 子圖新增的 buffer，也未計 zero-fill。

---

## 4. 結論對卡的改動

- **關卡改判**：從「圖建構（能否寫回原位）」→「**靜態圖 vs 逐 step 變化的 miss 集合**」。
- `designs.D1.risk` 的「scatter 成本從沒估過」由 J2 回答：機制存在（`ggml_acc`），上界 1.8%–3.6%（推算）。
- ⛔ **本項未判活**：J1 只證明原理可行。P2 的 exit（`M1 9/9 M2 9/9`）仍需動 `src/`。
- P0 status 改為「結論方向對、論證作廢」。

## 5. 給 owner 的工單（本線不實作）

owner 仍未明文（`owner:` 欄自開卡起未決）。工單內容已齊：

1. 要動的檔：`llama-graph.cpp`（`:2623` gate_up、`:2838` down、`:2863-2941` add 鏈）
   ＋ 新增 acc 節點；`ggml-metal-ops.cpp` 只需既有 `GGML_OP_ACC`（已有 kernel）。
2. 成本上界：固定 `k × n_layer` 個 op/step（推算 1.8%–3.6%）。
3. M1 判據：`scripts/check/m123_oracle_gate.py`，ref v7，`config_diffs == []`，
   ok = (M1 rate == 1.0 AND M2 rate == 1.0)。
4. ⛔ 前提：`CGC_DOWN_COMBINE` 必須關閉；`experts` 必須是 F32／單列連續。

## 5b. 給線 I 的工單：修 EBTIMER 口徑（本線不動 `src/`）

§7.3 證明「fill 占多少 ms/step」沒有任何可信實測，而 EBTIMER 是唯一包住它的計時器
⇒ 修它的價值 = 把 §7.4 那張表的「有效吞吐」從未知變成實測（跨 12.9→19.4 t/s）。

要改的都在 `src/llama.cpp/src/llama-expert-cache.cpp`（`cgc_eb_timer`，1159-1195）：

1. **`calls` 欄位不要印 `nl`**（現在 `fprintf` 第 4 個參數是 `nl`，恆 41）⇒
   改成印**本次窗口實際累積的呼叫數**，或直接改名 `nl=` 免得被當成觀測值。
2. **窗口邊界改成 step 邊界**：現在 `calls % nl == 0` 只是「數到 41 次就吐」，
   prefill 與 decode 共用同一個累加器 ⇒ 窗口跨段（`n_sum/41` 8.00 vs 18.3）。
   建議 `ensure_batch` 帶一個 step id（或由呼叫端在 step 末顯式 flush），
   並在見證行加一個 `seg=` 欄位（prefill／decode）。
3. **`pread_us` 改名**（它不測 fill，見 §7.3）⇒ 否則下一個人也會誤用。

⚠ 見證行格式被 `scripts/check/{fill_onpath_ab,pair_ab}.py` 的 `EB_RE` 消費
⇒ 加欄位要同步改那兩支腳本。

**驗收判據（可機檢）**：一個 decode 步恰好一條行、`n_sum ≡ 328`、
`step_usec < 86 ms`（否則仍是跨段）。達不到這三條就別引用 `step_usec`。

## 6. 「能優化成多少」—— 落點區間不可用於決策（01:0x 追加查證）

用現行錨點 11.61 t/s（86.13 ms/step）重算，並扣掉 J2 成本：

| 情境 | 淨收益 | 落點 |
|---|---:|---:|
| 最壞（零 fill、0.169 ms/次、328 op/step） | 34.5 − 22.43 = 12.07 ms；再 −3.28 ms | **12.93 t/s（+11.4%）** |
| 最好（穩態 fill、0.126 ms/次、328 op/step） | 34.5 − 1.89 = 32.61 ms；再 −1.64 ms | **18.13 t/s（+56.2%）** |

⛔ **但這個區間的兩個輸入都不牢，現在不能拿去做 go/no-go：**

**(a) 上界端是已知異常讀數。**「省下 34.5 ms」的 B 端 20.66 t/s 出自
`CACHE_SIZE_AB_2026-09-23.md:135`，而同文 `:137` 原話：
「單支啟動**自己**的三個 rep 就能差 2.7 倍，而 20.66 t/s **超出這台機器歷史上任何同形狀讀數的上緣**」。
另一個 44.5 ms（⇒ 23.68 t/s）更極端。⇒ **B 端不是穩態，是離群值。**

**(b) 下界端也無法用實測 A/B 複核。** `Backup/nofill_ab/`（build **578**、`n_batch 512`、
**非交付 cell** 的 5632；`n_kept=2`）：

| 臂 | avg_ts |
|---|---:|
| fill（r1/r4/r5） | 10.445 / 7.462 / 10.868 |
| nofill（r2/r3/r6） | 10.318 / 15.166 / 16.350 |

nofill 臂**自身** spread **59%**、stddev 最高 **5.29** ⇒ 量不出差異。

⇒ **14.1~19.9（換錨點後 12.9~18.1）是「推算的推算」**，其最大輸入（省下多少）
一端是標記過的離群值、一端量不出來。

### 6.1 順帶解掉 `k_unresolved`

`CGC-EBTIMER: step_usec=.. calls=41 miss=146 n_sum=328`
⇒ `calls = 41`（n_layers）、`n_sum/calls = 328/41 = 8.000` ⇒ **n_layer = 41、k = 8**
（miss 146/328 = 44.5%，落在 `miss_rate_measured` 上緣）。k=8 與 `:2746`「8 down GEMVs + 7 adds」一致，
`:2929`「n_expert_used-1 = 6」作廢。⇒ **J2 成本定死 = 41×8 = 328 op/step**（先前用 39 層推的 312 作廢）。

### 6.2 收窄它最便宜的一步：不是 J2，是 fill 吞吐

「背景 fill 供不供得上」決定落 13 還是 18（跨度 ~24 ms/step），
而 J2 只值 1.64–3.28 ms ⇒ **不到 1/8**。

✅ 儀器**已經存在**（推翻 `MISS_GATE_STEP3` §4「得新加一個計時器」——那是 09-24 的記載）：
`CGC_EB_TIMER=1`（`llama-expert-cache.cpp:1159-1195`，09-25 加，RAII），
見證行 `CGC-EBTIMER: step_usec=<us> calls=<n> miss=<m> n_sum=<s>`，
已被 `scripts/check/{fill_onpath_ab,pair_ab}.py` 的 `EB_RE` 消費（勿改格式）；binary 有。

⛔ **但口徑未坐實**：每趟只有 **1 條** `n_sum=328`（decode）行，且 fill 臂讀到
**170–199 ms/step**，與 decode step 預算 **86 ms** **矛盾** ⇒ flush 與「一步」沒對齊，
數字現在不能用。⚠ 修它要動 `llama-expert-cache.cpp` ⇒ **屬線 I，本線不動 `src/`**。

## 7. fill 預算閉合 —— 繞開壞儀器，用 pool 統計行算（01:1x 追加）

EBTIMER 口徑壞掉 ⇒ 改用**趟末 pool 統計行**（`llama-expert-cache.cpp:1121` 那行），
它與 flush 窗口無關，是整趟累計 ⇒ 不受 §6 的對齊問題影響。

### 7.1 閉合數字（6 臂，build 578／`p0_n128_d512`）

| 臂 | req | steps(=req/328) | miss | hit% | read_mib | MiB/miss | MiB/step | miss/step |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| r1_fill | 178055 | 542.9 | 8694 | 95.12 | 9419.3 | 1.083 | 17.35 | 16.02 |
| r4_fill | 192654 | 587.4 | 8178 | 95.76 | 8842.7 | 1.081 | 15.05 | 13.92 |
| r5_fill | 163635 | 498.9 | 9769 | 94.03 | 10628.0 | 1.088 | 21.30 | 19.58 |
| r2_nofill | 178523 | 544.3 | 6728 | 96.23 | 0.0 | — | 0 | 12.36 |
| r3_nofill | 135419 | 412.9 | 6158 | 95.45 | 0.0 | — | 0 | 14.92 |
| r6_nofill | 186350 | 568.1 | 5845 | 96.86 | 0.0 | — | 0 | 10.29 |

兩條一致性支撐閉合：**MiB/miss = 1.081–1.088（±0.3%）** ⇒ expert 大小就是 1.083 MiB；
**hit% 94.03–96.86** 四臂一致 ⇒ 穩態 miss 率是 **3.1–6.0%**。

⇒ **穩態 fill 需求 = 15.0–21.3 MiB/step**。以錨點 86.13 ms/step 計
⇒ **持續頻寬需求 175–250 MB/s**。

### 7.2 供得上嗎：供得上，且有一個量級的餘量

即使保守取 1 GB/s 有效讀速，供給／需求 = **4–6×**。
⇒ **「背景 fill 供不供得上」的答案是「帶寬上供得上」**；剩下的是**能否重疊**（§EN-463：重疊=0）
與**實際有效吞吐是多少**（未知，見 7.4）。

### 7.3 ⛔⛔ 三個計數器都不能拿來算 fill（誤用會得到錯一個量級的結論）

| 計數器 | 為什麼不能用 |
|---|---|
| `pread_us` | **nofill 臂 `read_mib=0` 卻 `pread_us`=375–556 s** ⇒ 它計的根本不是 fill，是別的 pread（模型權重／mmap 頁入）。誤用會得「每次 miss 52–95 ms」，比真值大兩個量級。 |
| `fill_wait_us` | 只覆蓋 `ensure_slot`（同步單 slot）路徑；本 cell 走 `ensure_batch`(L3 Option A) ⇒ r1/r5 為 **0**、r4 整趟僅 154 ms ⇒ 不是 fill 總耗時。 |
| `CGC-EBTIMER step_usec` | 窗口跨段（見 §6）：同 log 裡 `n_sum/41` 既有 **8.00**（純 decode）也有 **14.9–21.3**（混 prefill）⇒ 一行既可能是一步也可能是一段。 |

⇒ **「fill 占多少 ms/step」至今沒有任何可信實測。** 這是本線目前最大的未量量。

### 7.4 「34.5 ms」的物理含義（新解，但不構成救回）

34.5 ms ≈ **17.3 MiB @ ~0.5 GB/s**，與「同步 fill、有效吞吐 ~500 MB/s」自洽
（不是 SSD 峰值：pool 頁在 swap、隨機讀、單執行緒 pread）。
⇒ 落點區間的跨度其實來自**「fill 實際有效吞吐」這個未知數**：

| 有效吞吐 | fill 占 step | 扣 J2(1.6–3.3 ms) 後 | 落點 |
|---|---:|---:|---:|
| 0.5 GB/s | 34.6 ms | 50.0–51.6 ms | **19.4–20.0 t/s** |
| 1.0 GB/s | 17.3 ms | 66.8–68.5 ms | **14.6–15.0 t/s** |
| 2.0 GB/s | 8.6 ms | 75.9–77.6 ms | **12.9–13.2 t/s** |

⛔⛔ **不可比性（本輪最重要的限制）**：上表全部基於 `Backup/nofill_ab/`
＝ **p0（無 prefill）／n_batch 512／build 578**；交付 cell 是
**p2048／n_batch 5632／build 630**。prefill 2048 會把 pool 預熱得更熱
⇒ 交付 cell 的穩態 miss 率**可能更低** ⇒ fill 成本更低 ⇒ **收益更小**。
⇒ 這些數字只能定**數量級**，不能做定點估計。
⛔ 也不要把上表的 19.4–20.0 當成上界救回 —— §6(a) 的離群值問題仍然獨立成立。

### 7.5 k=8 的論證修正（method_culpa #2）

上一節我用 `328/41 = 8.000` 解出 k —— **除法本身對，理由寫錯了**：
源碼 `fprintf(stderr, "... step_usec=%llu calls=%llu ...", step_us, nl, ...)`
⇒ **`calls` 恆等於 `nl`（=41），不是觀測值**。所以「328/41」裡的 41 不是「這趟數到 41 次呼叫」。

修正後的論證（不依賴 `calls` 欄位）：
1. flush 條件是 `calls % nl == 0` ⇒ **每個 flush 窗口確實含 nl 次 `ensure_batch`**（真前提）。
2. 其它窗口的 `n_sum/41 = 14.9–21.3` ⇒ prefill 段每次呼叫的 n ≈ **18**。
3. 若 328 窗口混了 x 次 decode(n=8) 與 41−x 次 prefill(n=18)：`8x+18(41−x)=328 ⇒ x=41`
   ⇒ **該窗口是純 decode、每次 n=8** ⇒ **k=8**。
4. `n_layer=41` 由統計行獨立證實 ⇒ J2 = 41×8 = **328 op/step** 不變。
   ⛔ **本行已被 §8.3 作廢**：`e-fillbudget-m-decomp` 的 P1 見證行實測
   `n_sum=320` ⇒ **n_layer=40**（不是 41）⇒ **J2 = 40×8 = 320 op/step**。

⇒ **結論保住、論證換掉**（J1 不依賴 k，故 J1 不受影響）。

## 8. 結案（2026-09-29 02:1x）—— 建議不做

### 8.1 判決

**🔴 CLOSED — 建議不做。** 不是「判死」，是「判不值得」：M1 沒破產，是**收益低於可量測門檻**。

依 `e-fillbudget-m-decomp.result_p1`（交付 cell，1 趟，EBTIMER 修好後實測）：

| 量 | 值 |
|---|---|
| fill | p10 1.87 / **p50 5.41** / p90 12.72 ms/step（step 預算 86.13） |
| 落點 | 1000/(86.13 − 5.41 + J2) = **11.92–12.15 t/s（+2.6%~+4.6%）** |
| MDD | **8.5%** |

⇒ **收益 < MDD** ⇒ 在 16 GB 盒子上**量不出來**。

### 8.2 為什麼不「反正做一下」

它需要動 `src/` 且**碰 M1/M2/M3 護欄**（改 combine 的加法順序／寫回位置）。
「動護欄去換一個量不出來的 2.6–4.6%」是本專案明確禁止的交換
（參照 MTP B 卡前例：護欄只能在口徑被證明**必須**移動時才動，不是為了換速度）。

補一層方向性保險：本趟是髒窗（thermal HEAVY、swap +3261 MiB）⇒ 讓 fill **偏慢**
⇒ 乾淨窗的 fill 只會更小、收益更小 ⇒ **方向穩健，不是窗的問題**。

### 8.3 留下什麼 / 作廢什麼

✅ **留下（知識，不是工單）**
- **J1 原理可行**：未融合路徑（`CGC_DOWN_COMBINE` `<unset>`）下 D1 的 M1 原理成立。
- **J2 成本模型**：靜態圖 ⇒ 固定 `k×n_layer` op/step，與 miss 率無關。
- **EBTIMER `seg=` 欄位**（已落地）：本線留下的**永久資產**，與本卡結案無關。
- **fill 預算閉合**（§7）：穩態 15–21 MiB/step ⇒ 175–250 MB/s ⇒ 帶寬上有 4–6× 餘量。

⛔ **作廢**
- P0 的整段論證（裁的 `sum_rows` 不參與 combine）。
- 「~12 op/step」、「14.1~19.9 t/s」、「12.9~19.4 t/s」。
- **J2 的 328 op/step**（n_layer 實測是 **40** 不是 41 ⇒ **320 op/step**）。

### 8.4 重開條件（兩條同時成立才可）

1. 盒子換成 **≥32 GB**（pool 不再有 ~4.4 GiB 落在 swap ⇒ `cb` 桶消失 ⇒ fill 與 step 預算的相對關係改變）；
2. 重新實測 **fill ms/step ≥ 8.5% × step 預算**。

⛔ 單獨「髒窗讓數字難看」**不構成**重開理由。

### 8.5 owner

owner 從未明文指派 ⇒ 本線依 `on_fail` 自行結案：**不實作、不開工單**。

## 9. 本輪沒做的幾件事（避免下次重踩）

1. ~~沒跑任何 cell（0 GPU）~~ —— J1/J2 是靜態判定，但 §8 的落點來自**另卡 1 趟實測**。
2. ~~沒動 `src/`~~ ⇒ EBTIMER `seg=` 那一次動了（env-gated、不進數值路徑），見 §5b。
3. 沒解決 owner ⇒ §8.5 依 `on_fail` 自行結案。
4. ~~沒實測 k~~ ⇒ 已解，但**改過兩次**：先用 `328/41`（理由錯：`calls` 印的是常數 `nl`），
   再解 `k=8`；最後由 `e-fillbudget-m-decomp` 的 P1 見證行定死 **n_layer=40、k=8**
   ⇒ §7.5 的 41 與本節上方「J2=328」**均作廢**。
5. 沒把「每 op 派發 5–10 µs」坐實 —— 那是推算。⚠ 因已結案（§8.1 收益 < MDD），
   **不必再坐實**：就算它是 5 µs 或 20 µs，落點都過不了 MDD。
6. ~~沒修 EBTIMER 口徑~~ ⇒ 已修（`seg=` 欄位），見 `e-fillbudget-m-decomp`。
