# S2：把 44% 的步從「未知數」變成收入 —— 設計（2026-09-28）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **12.57／12.62 t/s 屬 MTP-on 輸出**，是**另一個輸出函數**，
> **不可與 MTP-off 互比**（含「MTP 加速 X%」「MTP-on ≥ MTP-off」「以 12.57 為基線／錨點／要打敗的數字」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**。

日期：2026-09-28　分支：`demo/sweet-spot-windows-fix`（build 630）　狀態：**設計，本輪零 GPU、零重建**

> **本文件不主張一個新方向，它主張一個已被指定、但從未執行的方向。**
> G1（`docs/G1_ACHIEVABLE_CEILING_2026-09-20.md`）已經把天花板定位到「等 host 寫 leaf」的 44%，
> 並且把候選路徑收成 S2 / S3 / D0 三條，然後說了一句話：**決定 S2 還是 S3 的不是設計品味，
> 是前提 B 的一次量測。** 而**那個量測已經有人做完了**（§2.3）—— 所以這份文件的工作只剩：
> 把 S2 寫成一個能被推翻的實驗，並指出它現在真正的阻塞在哪裡（§5）。

---

## 1. 這條線在佇列裡的位置

`docs/NEXT_ACTIONS_2026-09-27.md` 是**所有 agent 開跑前的第一順位通告**，它排的是
P0（治理 commit：GPU `flock` ＋ 關掉 `--charter none` 後門）→ P1（MTP on 權威成績，根因已定量
166.1 MiB）→ P2（on/off 成文）。**本文件是 P3**，理由是：它依賴 P0 的 `flock` 才有一個乾淨窗口
（09-27 那次兩個 harness 互搶就是沒有鎖的後果），而它自己不需要任何重建 ⇒ **可以排在 P1/P2 的
等待時間裡做 §6 的零成本核查。**

## 2. 已確立的事實（逐條附來源；本節沒有一個數字是本輪量的）

| # | 事實 | 來源 |
|---|---|---|
| 2.1 | **天花板 ×1.702（44% 的步）**：`CGC_SUBMIT_AHEAD=1`（**故意錯**，提交 seg i+1 時 GPU 還讀著舊 remap）把 step 169.21 → 99.39 ms；兩個獨立儀器一致（SUBHEAD_AHEAD 41.2% / 區間儀器 41.9% idle） | G1 §1 |
| 2.2 | **機制式子**：`gap_L = 1.00 × cb_{L−1} + 0.29–0.35 ms`（r +0.94…+0.999，L0 的 gap 恆 0.000）⇒ 兩成分可分辨：斜率 1.00 是 host top-k hook，**常數 0.29–0.35 ms 是每個邊界的 submit+launch 殘留** | G1 §5 |
| 2.3 | ⚠ **前提 B 曾被量，答案是「不成立」——但那個讀數出自一個量具本身不通電的 run（見 2.9）**：delivery decode step（ntok=4）的 consumed churn **42.3%**（bucketed，總數與純量 run 逐位元對上）；42% 的 (step, layer) 對上映射真的動 ⇒ 表內容非常數 ⇒ 發布不冗餘 ⇒ **S3（n_segs 40→1）沒有前提，S2 是可行路徑** | `agent_harness/portal/targets.json` 的 `premise_b`（2026-09-20）——★ 42.3% 來自 **`n_slot_table_consumed_by_ntok`** 那個分桶 teardown 計數器，**不是** 我最初數的 `TABLE-CHURN graph= changed_entries`（那是它自己宣告「無資訊」的 whole-table 量）。詳細更正見 §12 |
| 2.4 | 分段**本身**引入不確定性：S1 真臂在分段下、同 build 同 N 是 **3 輪 3 值**；不分段時塌成單一穩定值 | `portal/targets.json`；G1 §4.1 |
| 2.5 | **重疊旋鈕已進生產**：`CGC_SERVER_OA_ASYNC`（→ 子行程 `CGC_OA_ASYNC`）預設 1，值 1 對 0 是 **+12.6%** | `run_server.sh:125–133, 284–289` |
| 2.6 | ⚠ **`n_main` 是一個陷阱**：`CGC_CB_N_MAIN=32`（ABBA）給 −11.4% step / +12.8% t/s，但**機制被推翻** —— `union` −14.7%、`gap` **+24%**、`busy` −39%、**`busy/union` 1.41 → 1.00**（buffer 間並行消失）。結論原文：「**用段間空窗換段內並行 —— 總時間守恆**」 | `docs/CB_N_MAIN_BUCKETS_2026-09-26.md` §12.1/§12.1b |
| 2.7 | **可引用的量是步內三桶**（`union`/`gap`/`busy`，處理臂兩次復現散佈 0.7%/1.4%），**t/s 不可引用**（控制臂自身漂移 15.5%，遠超 ±9% 噪聲底） | 同上 §12.1/§12.2 |
| 2.8 | **16 GB 上一次跑四臂必 OOM**：每臂 swap 漲 ~8 GB 且不自動回落，`min_free ≈ 14 MiB` 是 macOS 硬底；`purge` 需 sudo（不可自動化） | 同上 §12.3 |
| 2.9 | ★⚠ **S1 的讀回路徑曾整條死掉**：`ffn_moe_topk_remap` 與 `ffn_moe_slot_table` 是 `ggml_new_tensor_2d + ggml_set_output`、**無 producer op** ⇒ 被歸進 `cgraph->leafs`；舊測只走 `nodes` ⇒ **每層都回 false ⇒ 兩張量每步被釘 `nullptr`**，而那正是**唯一能設 `ids_src_valid` 的路徑**。原註解判詞：「That is not a wrong number, it is **NO NUMBER**」。實測 `n_leaf=39 wrote=0 skip_not_in_graph=39 skip_null=0`。**2026-09-27 修**（改走 `cgc_tensor_in_graph`，nodes 先、再 leafs） | `llama-context.cpp:3479–3496`（診斷）、`:4147–4164`（修正） |

**⇒ 2.3 把三條路砍到一條（S2），2.6 給出 S2 必須避開的失敗模式，2.7 給出唯一的判準，2.8 給出協定，
而 2.9 把 S1 從「失敗」改判成「未量」。**

## 3. 假設，寫成可被推翻的式子

**H**：段邊界的等待是可以被移除的，因為 leaf 已由 GPU 計算（S1）⇒ host **沒有東西必須等**。
若 H 成立，則三條**同時**成立（缺一即不成立）：

```
(a) gap 塌向 0                 : gap_sum 中位數 → ~0（不是變小）
(b) wait 的下降量 ≈ Σcb + 被移掉的延遲  : 對不上 ⇒ 吃到的不是那 44%
(c) union 不動                 : 偷來的時間不是從 GPU 工作借的
```

(c) 是新增的，而且是 2.6 的教訓：上次那個「+12.8%」之所以守恆，就是因為它違反了 (c)。

**逐邊界的線性預測**（G1 §5）：每移除一個邊界 ⇒ `gap_sum` 中位數 **−0.32 ms**、`total` 同步下降、
`union` 不動。39 個邊界 ⇒ **−12.5 ms**，而 `gap_sum/total ≤ 5%` 在 40 段的步上**不可達**
（地板 12.5 ms > 5% × 139.46 = 6.97 ms）⇒ 可達的重述是 **(a) `cb_sum/total`** 與 **(b) 邊界數**。

## 4. 設計

### E0｜先修量具，而且先算效應量（零 GPU，30 分鐘）

1. **基線讀法**：**步表頭**的 `gap_sum` **中位數**（**不是**逐層中位數的和 —— 同一批 184 步上兩者差
   39%：21.43 vs 34.76 ms）。這是一條容易搞錯、而且會直接改變結論的讀法。
2. **先算 3SE**：用 `union_floor.py` 確認該 run 的 3SE 小於效應量。G1 §5.3 已警告：n≈180 的暖 run
   3SE = **8.0%**，而單層效應 0.32/132 = **0.24%** ⇒ **一次只移一個邊界是量不到的**。
   ⇒ **本設計改成 (i) 一次移一批，或 (ii) 同一 run 內配對（A/B 交替）**，兩者選一，**寫在臂裡**。
3. **閘門量必須全程列印**：`uni=`（不變量）、`busy/union`（2.6 的護欄）、`segs`、
   `zero_mapped_selected`、`n_fast_cold`、`skipped%`（**整臂**閘，不是逐行 —— 見 2026-09-26 §11.2）。

### E1｜臂的構造（一次 ≤2 臂，中間 `purge` 或重啟）

```
ctl   : prod-new p2048 n128 d512 r3   + CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1;CGC_HOOK_SPLIT=1
treat : 同上                          + <S2 開關>
```
- 三桶儀器**兩臂都開** ⇒ 配對公平（沿用 09-26 §2 的做法）。
- 判準是 **§3 的 (a)(b)(c)**，**不是 t/s**。t/s 只有在控制臂自身漂移 ≤ ±9%（2.7）時才附註。
- 起跑前：`compressor QUIET` ＋ 窗口空 ＋ `harness.gpu_window_lock`（NEXT_ACTIONS §1.1）持有。

### E2｜護欄（任一項紅就中止，不進判讀）

| 護欄 | 門檻 | 為什麼 |
|---|---|---|
| bit-identical | M1/M2/M3 ＋ M2 oracle **全過** | G1 §4.1。⚠ 且 2.4 說分段下 S1 真臂是 3 輪 3 值 ⇒ **這道門在 S3 之前是擲骰子**，所以它必須與 §5 的 S1 狀態一起讀 |
| 品質 | `zero_mapped_selected` / `n_fast_cold` **不得上升** | G1 §4.4：round trip 買的就是 routing 新鮮度；分段消失後晚到的 fill 會退化成 ZERO-mapping ⇒ **貢獻被丟棄** |
| 並行 | `busy/union` **不得掉到 ~1.2 以下** | 2.6：掉到 1.00 就是「用並行換空窗」，總時間守恆 |
| 窗口 | 控制臂自身漂移 ≤ ±9% | 2.7 |
| 記憶體 | 每臂後 `swap_used` 不得累積（2.8） | 四臂一腳本必 OOM；`purge` 需 sudo |

### E3｜判讀的順序（先講能不能讀，再講誰贏）

1. 先印 §3 的 (a)(b)(c) **三條逐條**（含 `gap_sum` 步表頭中位數與 `segs`）。
2. 再印 §4 的護欄表。
3. **只有**當護欄全綠且 (a)(b)(c) 同時成立，才把結果寫成一個倍率；否則寫成 null result，
   並註明是三條裡的哪一條沒成立（2.6 就是 (c) 沒成立而被讀成「有收益」的那一次）。

## 5. 真正的阻塞不是「S1 沒過」，是「S1 通過與否**還沒被量過**」

G1 §2 的表寫得很直白：**S2 的擋路石是「圖裡仍有 host 寫入 ⇒ 等的是正確性」，
只有當 leaf 由 GPU 算（S1）才合法。**

記錄停在「S1 仍未過」。**本輪查證改寫了這句話，但方向與期待相反 —— 它不是「失敗」，是「沒有讀數」：**

- **2026-09-24 的註解（`llama-context.cpp:3431–3436`）說 S1 是過的**：同一 build、同一樹，
  `CGC_SLOT_TABLE_GPU=1` **ALONE**（不帶 `CGC_S1_DBG`）通過 gate **M1 9/9 / M2 9/9 / M3 9/9**，
  `zero_mapped_selected=0`。先前那次「S1 aborts」被歸因為**探針自己**：`CGC_S1_DBG` 從被 arena
  重用的懸空指標上讀 `->ne` ⇒ `GGML_ASSERT` ⇒ **探針殺死了它正在量的那個 run**。
- **但 2026-09-27 又修了一條更深的**（2.9）：在該修之前，`rm`／`tb` 兩個 leaf 每步被釘 `nullptr`，
  而那是**唯一能把 `ids_src_valid` 設起來的路徑**。⇒ 那條 09-24 的「9/9 通過」，是在
  **S1 的證據鏈本身還沒接上**的時候讀到的。

**⇒ 誠實的敘述既不是「S1 已過」也不是「S1 未過」，是「S1 的證據還沒被量」。**
這也是 G1 §0 那句話的第二次出現：兩者都是**介於 1.0 與 1.702 之間的未知數**。

### 5.1 一行就能問出答案（零 GPU、零重建、決定 S2 能不能開跑）

`ids_src_valid` 只由那條路徑設。所以：**跑一次 decode 且帶 `CGC_SLOT_TABLE_GPU=1`，看
`ids_src_valid=1` 有沒有出現過。**

- **出現** ⇒ 修正已在 active binary 內、S1 的證據鏈是通的 ⇒ **進 E1**。
- **從未出現** ⇒ **不能**判成「S1 壞了」：要看它的 `CGC-S1: POST … SKIPPED` 是哪一種？
  `capture is not a node of this graph`（＝舊 binary／舊路徑）還是 `not … int32s`（＝byte 測擋的）
  或 `gather=null (leaf path)`。三者的處置完全不同。

**⚠ 但這句話在本文件中被執行時就錯了兩次，實際做法見 §11：**
（a）`ids_src_valid` **只在 `CGC_S1_DBG` 底下才印** ⇒ 「讀 ids_src_valid」與「不開那個探針」
是互斥的（§11.3）；（b）真正的第一步應該是**不帶探針的前導讀數**：
`CGC_MISS_MASK_DBG=1` 的 **`CGC-MM-PUB as_leaf=39 / skip_not_in_graph=0`**。
**結果：已執行，`ids_src_valid=1` × 78、`gather_vs_table=[0]` × 78 ⇒ S1 的證據鏈過（§11.5）。**

### 5.2 binary 有沒有那個修：已核到「一致但不證明」，所以上面的實測才是判準

| 檢查 | 結果 |
|---|---|
| 原始碼 mtime | `llama-context.cpp` = **09-27 12:53:21** |
| link artifact | `libllama.0.0.630.dylib` = **09-27 12:53:30**（比原始碼晚 9 秒）⇒ **一致** |
| active 指向 | `libllama.0.dylib → libllama.0.0.630.dylib` ✅ |
| 符號表 | ⛔ **無效**：`nm -a` 有 8624 行、89 個 `cgc*` 符號，但兩個 static 都不在裡面 ⇒ **「不含」不成立，是量具盲掉**（連確定存在的 `cgc_node_in_graph` 也查不到） |
| 字串 | `CGC-MM-PUB … as_leaf=%d` 在 binary 內 ⇒ **09-26 的 provenance v2 確定在**；但 09-27 的修改**沒有新增字串** ⇒ 無法用字串分辨 |

**⇒ mtime 只能說「一致」；那 9 秒的差距不是證明。** 判準因此回到 5.1 的那一次實測。

### 5.3 前提 B（2.3）要重測，理由是它的**來源 run 正是 2.9 那個失效**

42.3% 是 **2026-09-20** 的讀數；而 2.9 的失效（leaf 全被釘 `nullptr`、下游讀回 arena 殘值）
被同一批註解判為「**這整個練習產出的那個 100% miss**」的來源。⇒ **churn 讀數與 miss 讀數共用那條死掉的證據鏈**，
所以在一個含 09-27 修的 run 上重讀之前，**2.3 不能承重**。這反過來讓 **S3 重獲一個（未量測的）前提**。

### 5.4 S1 × S2 的相互作用

S1 把 leaf 的計算搬到 GPU，但那條路徑**自己**有 host 側準備工作（table 發布、snapshot 比對、byte 測）。
⇒ §3 的 (b) 式（`wait` 下降量 ≈ Σ`cb` + 被移掉的延遲）必須在 **S1 開啟**的前提下重做一次，
**不能用 baseline 的 `cb`**。

### 5.5 殘留限制（原註解自己聲明的，沿用）

`cgc_is_i32_n` 讓讀取**安全**，不讓它**可歸屬**：一個剛好落在長度相符的 live I32 張量上的懸空指標
仍會通過，而那行輸出描述的是那個張量、不是 S1 的 gather。收掉它需要**逐 build 的 generation stamp**
（在 capture 點記下「哪一次 build 寫的」）——**那是後續工作，不是這次**。⇒ 在那之前，
每一行 POST 都必須與它的 `ids_src_valid` 標籤一起讀。

## 6. 零成本核查清單（本輪已做 / 待做）

| 項目 | 狀態 | 證據 |
|---|---|---|
| 旋鈕可從**生產入口**武裝 | ✅ 已核 | 逐一數 `SERVER_ENV+=(…)` 的顯式加入：`CGC_CB_N_MAIN` 1、`CGC_HOOK_SPLIT` 1、`CGC_SUBMIT_AHEAD` 1、`CGC_GPU_TIMING` 1、`CGC_SLOT_TABLE_GPU` 1、`CGC_S1_DBG` 1、`CGC_DECODE_PROFILE` **2**。⚠ **不要用 `grep -c 變數名` 當成員判定**：那會把註解行一起算進去（同一組變數在單行 grep 下是 3/3/6/3/8）。 |
| `CGC_OA_ASYNC` 的入口是**另一個名字**，而 allowlist 是多行的 | ✅ 已核 | `CGC_SERVER_OA_ASYNC` → `SERVER_OA_ASYNC` → 子行程 `CGC_OA_ASYNC`（`run_server.sh:133`、`1489`）。⚠ 真正的陷阱是：**`CGC_OA_ASYNC` 在單行 `SERVER_ENV+=(.*CGC_OA_ASYNC` 下數到 0**，因為它在一個**跨行**的 `SERVER_ENV+=( … )` 區塊裡（`:1489`，`:1492` 收 `)`）⇒ 任何人用單行正則審 allowlist 都會把它誤判成「沒接上」 |
| `n_main` 的讀取點與地板 | ✅ 已核 | `ggml-metal-context.m:1153`（註解）＋ `:1162–1164`（`cgc_cb_nmain` 有值則用、否則 `MAX(64, 0.1*gf->n_nodes)`）；分段後 N≈99 ⇒ 地板接管 ⇒ **65% 節點由呼叫執行緒序列編碼**（09-26 §1） |
| 三桶／相位儀器存在且可跑 | ✅ 已核 | `scripts/check/phase_split_ab.py`（115 KB，含 `arm_order`／窗口等待／launch／probe／`parse_banner`／`segments`／`attribution`） |
| binary 是否含 09-27 的 leaf 修 | ✅ **已答（用行為，不是用檔案）** | §11.1：`as_leaf=39 skip_not_in_graph=0` ⇒ 確定在。mtime 只能給「一致」；符號測**無效**（量具盲） |
| S1（`CGC_SLOT_TABLE_GPU`）現狀 | ✅ **已答：證據鏈過** | §11.5：`ids_src_valid=1` × 78、`gather_vs_table=[0]` × 78（需 `CGC_S1_DBG=1`） |
| 關機路徑是否會漏 GPU 記憶體 | 🟡 **已量到會** | §11.4：連**單一** SIGTERM 都走 `ggml-metal-device.m:657 GGML_ASSERT([rsets->data count] == 0)` in `exit` |
| 前提 B 在 630 上是否仍 42.3% | ⛔ **待查，且已降級** | §5.3：04-20 那個讀數的來源 run 正是 2.9 的失效 |
| 這一輪的 `run_server.sh` 旋鈕全部**真的會傳到子行程**嗎 | ⛔ **未核** | 只核了 allowlist 成員，**沒有**核「子行程實際 `getenv` 到的值」（09-26 的 `ENV.CGC_OA_ASYNC: ref='0' now='1'` 就是這一類） |

## 7. 明確不做（避免重踩）

- **S3**（n_segs 40→1）：前提 B 曾被否（2.3），但那個讀數出自 2.9 那個失效的證據鏈 ⇒
  **除非 §5.3 重測出 churn ≈ 0，否則不碰**（重測是 S2 之前該做的事，不是 S2 的替代）。
- **D0 submit-ahead**：故意錯，只是上限量具（2.1）。
- **一次移一個邊界**：效應 0.32 ms vs 3SE 8%（2.2 / G1 §5.3）⇒ 量不到。
- **一支腳本跑四臂**：必 OOM（2.8）。
- **拿 `n_main` 當 gap 槓桿**：已證守恆（2.6）。
- **用 t/s 當判準**：漂移 15.5% 不可比（2.7）。判準是 (a)(b)(c)。

## 8. 這份文件不能支持的

1. **本輪沒有跑任何東西**（零 GPU、零重建）。§2 的每個數字都是**引用**，來源逐條標出；
   §3 的三條式子與 §4 的門檻是**設計**，尚未量測。
2. **它不能承諾收益，而且本輪把未知數換了位置而不是消掉**。G1 §0 的原文是「在前提 B 被量出來之前，
   正確答案是**介於 1.0 與 1.702 之間的一個未知數**」。本文件的淨變化是：**S1 從「失敗」改判成「未量」
   （§5）而且前提 B 被降級（§5.3）⇒ 兩個未知數都不比舊的那個更接近答案，只是更便宜**
   （各一次 run，見 §6）。**不要把它讀成「S2 更有希望」**：它也同樣可能是「S2 的合法化條件不成立」。
3. **2.3 的前提 B 讀數來自一個 MTP-off 形狀以外的 bucket 分解**（ntok=4 = delivery decode step），
   而同一份記錄說「同一個 signature 在 43 個舊 run 之間橫跨 37.2%–84.4%」⇒ **42.3% 是一個 run 的
   直接讀數，不是那個分布**。引用時必須連這句一起引。

---

## 9. 判準 0：先證明量具活著（本輪最重要的發現）

本設計的**最大風險不是 G1 的 44% 拿不到，是拿到了一個假數字就收工。**
同一個失效族在本 repo 已經有**五次**記錄，而且**每一次的表現都是「一個看起來正常的數字」**：

| # | 事件 | 印出來的 | 真相 | 出處 |
|---|---|---|---|---|
| 1 | prerouter | `precision 0.0%` | `scored=0`：**一次都沒做過預測**（`pred_total=0`） | `engine_loop/README.md` §12（同日） |
| 2 | prerouter | `nodata=0` | 它測的是「vector 有沒有配到 256 格」，**不是**「有沒有被填」 | 同上 |
| 3 | S1 探針 | run `GGML_ASSERT` abort | **探針**讀快取重用的懸空指標 ⇒ 殺死了它正在量的 run | `llama-context.cpp:3431–3436` |
| 4 | leaf 成員測試 | 「100% miss」 | 39 個 leaf **全部**被釘 `nullptr`，讀回的是 arena 殘值 | `llama-context.cpp:4147–4164`（2.9） |
| 5 | 本文件的 §5.2 符號測 | 「binary 不含那個修」 | dylib **沒被 strip**（8624 行、89 個 `cgc*` 符號），是 **static 不在 symtab** ⇒ 量具盲了 | 本輪，未落任何程式碼 |

**#5 是發生在本次「查證」裡的**，這就是為什麼它必須寫成一條判準而不是一段感想：
**每一次查證都得先回答「如果事實相反，這個量具印出來的會不一樣嗎？」**
（#5 的答案是「不會」，所以它無效；§5.2 表就是依這個問題逐列標的。）

### 9.1 因此 E0 的第一件事具體化成一個斷言

| 要問的 | 只問一次的讀數 | 什麼會使它無效 |
|---|---|---|
| S1 的證據鏈通不通 | `ids_src_valid=1` 是否出現 | 若「從未出現」，必須同時讀到是哪一種 `SKIPPED`；**否則不能區分「修沒進 binary」與「S1 真的壞」** |
| 發布端在不在動 | `CGC-MM-PUB` 的 `n_leaf=39 wrote>0 skip_not_in_graph=0` | `wrote=0` ⇒ 該 run 的 **churn 讀數不可引用，不管它印幾 %** |
| 分段的邊界真的存在 | `segs` 與 `gap_sum`（步表頭） | 若 `segs` 顯示未分段，則整條 S2 的前提消失（那會是 S3 的世界） |

**⇒ 一條臂的 `wrote=0` 或「無 `ids_src_valid`」必須讓那個 run 進入 `UNQUOTABLE`，而不是進表格。**

## 10. 執行順序（一次一件事，每件都能停在原地）

```
0.  無 GPU：本文件 §6 的三個 ⛔（子行程實際 env、churn 重讀的儀器是否還在樹上、S1 的 SKIPPED 分類）
1.  1 run（不用 S2、不用 rebuild）：CGC_SLOT_TABLE_GPU=1 的 decode，讀 ids_src_valid 與 MM-PUB
      ├─ 不通電  → 停，先修證據鏈（這一輪就到這裡為止，不要跑任何臂）
      └─ 通電    → 進 2
2.  1 run：前提 B 重讀（bucket 分解）。churn ≈ 0 ⇒ 改成做 S3；churn 仍高 ⇒ 進 3
3.  E0：把 (a)(b)(c) 與護欄寫成一個腳本，先拿 baseline 跑一次證明它會印
4.  E1：ctl vs S2，同 run 配對，判準是 (a)(b)(c)，t/s 只當附註
```

**每一步都可以停**，而停在 1 或 2 的收穫（S1 的證據鏈通不通、churn 是多少）
本身就比「又一個 t/s 差異」有價值。

---

## 11. 第二步的執行結果（2026-09-28 12:46，build 630）

**執行口徑**：`CGC_SERVER_PROFILE=prod-new` ＋ `CGC_SLOT_TABLE_GPU=1` ＋ `CGC_MISS_MASK=1`
＋ `CGC_MISS_MASK_DBG=1`，**刻意不開 `CGC_S1_DBG`**（見下），`CGC_THERMAL_GATE=off`。
log：`Backup/cgc_logs/llama_server_20260928_124618.log`。

### 11.1 結果：發布端是活的，舊的失效模式（2.9）已消失

```
CGC-MM-PUB n_leaf=39 wrote=39 as_leaf=39 skip_null=0 skip_not_in_graph=0 skip_shape=0 st_null=0
             | first_leaf: nn=256 nexp=256 slots=143 had_st=1 nres=143 | resident 5577/9984
CGC-S1: SELSHAPE il=1 sel_ne=[8,1] sel_nb=[4,1024] ops=38 src0_op=70   （il=1..4）
```

- **`as_leaf=39` 是關鍵那格**：39 個 leaf **全部經由 leafs 分支**被接受；`skip_not_in_graph=0`。
  對照 2.9 的修前讀數 `n_leaf=39 wrote=0 skip_not_in_graph=39` ⇒ **這是它的鏡像**。
  ⇒ **`cgc_tensor_in_graph`（09-27 修）確實在 active binary 內**（§5.2 那個無法用
  mtime／符號／字串回答的問題，改用**行為**回答了 —— 而且回答儀器本身沒有可疑之處）。
- `CGC-S1: SELSHAPE` 只被一個計數器 gate（`cgc_sel_shape_lines++ < 4`），且位於
  `llama-graph.cpp:2342` 的 **`CGC_SLOT_TABLE_GPU` 分支內** ⇒ 它會印代表 **S1 分支真的在執行**。
- ⇒ Σ₁ 那條「唯一能設 `ids_src_valid` 的路徑」**現在會執行**（以前是 NO NUMBER）。

### 11.2 ★ 但「量具活著」不等於「device 收到了資料」—— 這是我這輪差點讀錯的地方

同一份原始碼（`llama-graph.cpp:2326–2336`，就在 SELSHAPE 上面）記錄了一個**量到的反例**：
**一個由 host 寫入、再被 `mul_mat_id` 消費的張量，值不會被送到 device** ——
實測 `gather_vs_table=[15]`，即 device 讀到 **ZEROS**（而 host 鏡像裡號碼是對的）：

> 「A host-written root consumed by a GPU kernel is therefore **not delivered to the device**
> (the table and the remap leaf are consumed by `mul_mat_id`, not by a device GET_ROWS),
> so **that design cannot work no matter how the host fills it**.」

⇒ **`wrote=39` 是必要條件，不是充分條件。** 所以 §4 E2 的護欄要補一列：
**`wrote>0` 不得被讀成「S1 成立」**，必須配 `ids_src_valid=1` **且** `gather_vs_table=[0]`。

### 11.3 為什麼沒量到 `ids_src_valid`：它只在 `CGC_S1_DBG` 底下才印

```
src/llama.cpp/src/llama-context.cpp:4102:  static const bool cgc_s1_post = getenv("CGC_S1_DBG") != nullptr;
4103:  if (cgc_s1_post) {            ← 整個 POST 區塊，含 ids_src_valid 的 fprintf
```

⇒ **我原本提議的第一步（「讀 `ids_src_valid`」）在設計上就是要先武裝那把記錄裡
「殺死它正在量的 run」的槍。** 這是本文件內同一個教訓的第二次：**先問那個讀數需要什麼，
再決定要不要跑。**

### 11.4 現場狀況：第二階段被機器的記憶體狀態擋住

- 第一支臂活到了 decode（有 MM-PUB 與 SELSHAPE），但我那條啟動指令被工具的 120 s 逾時殺掉，
  **工具送的是 SIGINT + SIGTERM 兩發** ⇒ 觸發 repo 自己警告的那條路：
  `ggml-metal-device.m:657 GGML_ASSERT([rsets->data count] == 0)`在 `signal_handler → exit`。
  **這不是 inference 的錯，是關機路徑的錯**，且它會**洩漏 GPU 記憶體**。
- 第二支臂（加 `CGC_S1_DBG=1`）在 t≈7.3 s、RSS 5055 MiB 時消失：**`Pages free` = 3894≈61 MiB、
  swap 9705/10240 MiB、kernel 正在 jetsam 殺行程**（`killing_idle_process` 連七筆）。
  ⇒ 13 GB 模型灌不進去，**不是旋鈕的錯**。

**⇒ 協定修正（給 §7 與 2.8）**：跑臂的指令**不可**讓工具逾時殺它
（殺出來的 SIGINT+SIGTERM 會走關機 assert 並洩漏 GPU 記憶體 ⇒ **污染下一支臂**）。
要嘛 detached 啟動後輪詢，要嘛先確認 swap 存量，再用手上的 `curl /shutdown` 或**單一** SIGINT 收工。

**⇒ 修正：那次失敗不是旋鈕的錯，而是記憶體。** 等 `free` 自己回到 7 GB 後重跑**成功**，
而且 11.5 就是它量到的東西。

**⇒ 協定修正二（比第一條更硬）**：**連「單一 SIGTERM 的優雅關機」路徑本身都會 assert。**
第二次 run 的 log 只有**一發** `[CGC] Received SIGTERM — initiating graceful shutdown`（沒有
second interrupt），然後就是同一條 `GGML_ASSERT([rsets->data count] == 0)` in `exit`。
引擎日誌承諾的那條「會把 expert cache ＋ Metal buffers 釋放掉」的路徑**在這個 build 上走不通**，
而 `ggml-metal-device.m:657` 那個 assert 發生在 `ggml_metal_device_free` ⇒ **GPU 記憶體就是在那裡漏的。**
⇒ 任何「跑一支臂、收工、再跑下一支」的協定都必須把它算進去（它會與 2.8 的 OOM 疊加）。

### 11.5 ★ 回答：`ids_src_valid=1` × **78**、`gather_vs_table=[0]` × **78**

log：`Backup/cgc_logs/llama_server_20260928_125503.log`（`CGC_S1_DBG=1`）。

```
423:  CGC-S1: POST il=1  ntok=2 n_expert=256 gather=[...] leaf=[n/a] idx=[...] ... ids_src_valid=1 ...
 …   （il=1..39 共 39 行）
751:  CGC-S1: POST il=39 ntok=2 n_expert=256 ...
```

- **78 = 2 次 decode 呼叫 × 39 層**（探針的 `cgc_s1_post_n < 6` 限次，這次用了 2 次）。
- **78/78 全部 `ids_src_valid=1`**、**78/78 全部 `gather_vs_table=[0]`**。
  `[0]` 是「全部位置相符」；原始碼的反例是 `gather_vs_table=[15]`（device 讀到 ZEROS）。
- 同一 run 的 `n_leaf=39 wrote=39 as_leaf=39 skip_not_in_graph=0`、`SELSHAPE il=1..4`、
  `ntok=2 n_expert=256`。

**⇒ S1 的證據鏈是活的，而且資料一致。** §5.1 那個一行就能問的問題有了答案：
**不是「S1 壞了」，也不是「修沒進 binary」，而是兩者都不是 —— 以前只是沒人去讀。**
⇒ §5 那句「S1 的證據還沒被量」現在結案：**已量，過。**

### 11.6 但不要把 11.5 讀成「S2 可行」

11.5 證明的是「一個 device 與 host 一致的索引向量存在且讀得到」。它**不**證明：
（i）主機不再有東西必須等（S2 的全部命題），（ii）§11.2 那個「host 寫的 leaf 被 `mul_mat_id`
消費則送不到 device」的限制不適用於 S2 要動的那幾張張量。
⇒ **下一步不變：E0 的三條式（a)(b)(c) 與護欄，才是判 S2 的那把尺。**

### 11.7 本輪**未能**歸屬的一件事

我發出的兩個 `/v1/chat/completions` 請求：第一個回應**不是 JSON**，第二個 `http=000`（連接失敗，
伺服已關）。而 78 行 POST 全部出現在**我用 grep 找得到的請求處理痕跡之前／之間**，而 log 裡
我試的幾個請求標記（`prompt eval` / `slot release` / `stop processing`）**一個都沒命中**。
⇒ **這 78 行屬於 decode 形狀的步進，但我無法把它們歸給我的請求**（可能是啟動暖機那兩步）。
**這不影響 11.5 的結論**（POST 只存在於 decode graph），但它影響「這次請求有被服務」這個宣稱 ——
**那個我沒有證成，所以不宣稱。**

---

## 12. 重讀前提 B：三個更正，與一個我自己的誤比較（2026-09-28 13:0x）

### 12.1 ⚠ 更正一：出處路徑一直是錯的

本文件典 §2.3 引的 `portal/targets.json` **不存在**。真實路徑是
**`agent_harness/portal/targets.json`**（`:100` 起是 `premise_b`）。
本文件已修正。**一個從來沒被驗證過的引用路徑，跟一個沒被驗證的讀數一樣危險。**

### 12.2 ⚠ 更正二：42.3% 不是我第一次數的那個計數器（這是我自己犯的誤比較）

我最初在 `Backup/cgc_logs/` 裡找到 `CGC-S1: TABLE-CHURN graph=… changed_entries=…`，
把 1637 個 graph 匯總成 **中位 0.8%、均值 1.4%、最大 19.2%、總體 1.31%**，
然後把它跟 42.3% 對比 —— **那個比較不成立，兩者是不同的量：**

| | 那個 | 42.3% 的那個 |
|---|---|---|
| 計數器 | `n_slot_table_changed`（每 per-graph 行 `changed_entries`） | **`n_slot_table_consumed_changed_by_ntok`** |
| 分母 | 39×256 個表項 | **只算 consumer 讀過的那些 id** |
| 何時印 | 每個 graph | **只在 teardown，且按 `ntok` 分桶** |
| 定義出處 | `premise_b` (a) 自己說「whole-table count 是 ~44% by construction …**carries no information**」 | 同上 |

⇒ `premise_b` 的原文就寫著：**whole-table 的那個按構造約 44%、不携帶資訊**。
所以「1.31% vs 42.3%」是拿一個它自己宣告無資訊的量去對一個有資訊的量。**已廢。**

### 12.3 ✅ 更正三：那個「一行修」現在已經套用了

`premise_b` (c) 說 `cgc_is_decode_graph(n_tokens, cgc_pool_max_tokens())` 是
「**PREPARED AND SYNTAX-VERIFIED, NOT APPLIED**」。**本輪核到它已經在原始碼裡**
（`llama-context.cpp:5502`），而且 `CGC_S1_TABLE_CHURN` 在
`run_server.sh:1530` 的 pass-through 清單裡（**跨行**，所以單行正則會誤報成 0）。
⇒ 計量器與它的 gate 都就緒，不必重建。

### 12.4 因此重讀的正確口徑（已備好，未執行）

```
臂      : scripts/check/decode_sweep.py:209 的既有臂 p25-s1-churn
          = {CGC_GPU_TIMING=1, CGC_DECODE_PROFILE=1, CGC_SLOT_TABLE_GPU=1, CGC_S1_TABLE_CHURN=1}
profile : CGC_SERVER_PROFILE=prod25   ★ 必須是 MTP ON（delivery 的 ntok=4 只在 MTP ON 下存在；
          prod-new 是 MTP off、decode 是 ntok=2 ⇒ 那個桶根本不會出現）
讀數    : teardown 的 "S1 churn by ntok (consumed subset): ntok=… x/y=z%"
          （llama-expert-cache.cpp:3100–3118），以及它下面那行 entry-rate（§EN-317）
基準    : ntok=4 3449/8160 = 42.3%（2026-09-20、build d91e432636d1、prod25）
```

**★ 而這個重讀的價值不在「S2 還是 S3」——那個已經由記錄定了。** `premise_b` 自己的結論：
「**42.3% is not 0** … so S3 (collapse n_segs 40 → 1) **HAS NO PREMISE** and S2 is the viable route」。
重讀真正的價值是兩件它自己標為未完成的事：
- **(e) 分布**：同一個 signature 在 43 個舊 run 之間横跨 **37.2%–84.4%** ⇒ 42.3% 是「一個 run 的
  直接讀數」而不是它的分布。在 630 上重讀是把這個分布從一個 build 接到另一個。
- **(f) 沒被建立的那件事**：「**the knob that moves churn across that range. It is known to exist
  and is not recorded.**」⇒ 找那個旋鈕是這個量具下一個真正的工作。

### 12.5 而另一個計數器上有一個未記錄的事實

`TABLE-CHURN` 的**新格式**（帶 `ntok=` 與 `SEL-DRIFT`）在 5 個 log 裡出現過（全 09-20，ntok=8）：

```
TABLE-CHURN graph=1 ntok=8 publishes=40 changed_entries=1286  SEL-DRIFT layers=0 entries=0 max_per_layer=0
（5 個 log 的每一個 graph 都是 SEL-DRIFT 0）
```

按原始碼自己的定義（`:5536–5545`），**`SEL-DRIFT = 0` 表示池在「publish → 消費」那個窗口內
沒有移動** ⇒ 消費者讀到的就是當時發布的那一份。這與 12.2 的那個 42.3% **不矛盾**：churn 大
（發布不冗餘）與 drift=0（發布及時）是两件事。**但它仍然是 ntok=8（chunked prefill），
不是 delivery 桶**，所以不能拿它回答 12.4 的問題。

### 12.6 ⛔ 未完成：機器被另一個 session 占著

本輪三次嘗試都**沒有**拿到那個讀數，而原因不是旋鈕：

1. **launcher 自己的 preflight 拒跑**（正確行為）：「`error: 仍有 1 支 llama 行程，繼續啟動極可能
   GPU OOM (ret=-3)`」，它上面的註解還自己記著 2026-09-18 那次「**把別條 session 正在量的 server
   SIGTERM 掉了**」的错。
2. 另一支 run 在 **t≈30 s** 就被 **SIGTERM＋SIGINT（外部的，不是我發的）** 終止，
   teardown 還沒跑（`log:501–502`）。
3. 有一個帶著 `CGC_DETACHED` 標記的 `python3 -c` detach 包裝器在跑 —— **那是別條 session 的工具**。

⇒ 這是 `NEXT_ACTIONS_2026-09-27.md` 的 **P0 `harness.gpu_window_lock`（flock）** 不存在所直接造成的。
**在本文件這個實驗真的可做之前，先把那把鎖立起來。**

### 12.7 兩個協定教训（本輪新增，給所有跑臂的人）

- **不要把臂放在一個「工具可能殺掉」的前景指令裡。** 本輪與 11.4 各一次：指令一結束，
  server 就收到 SIGTERM＋SIGINT ⇒ 走關機 assert、洩漏 GPU 記憶體、污染下一支臂。
  可行的做法是**在單一個指令裡跑完整條鏈**（啟動 → 輪詢就緒 → 請求 → 單一 SIGTERM → 讀 teardown），
  而不要跨指令保留 server。
- **⚠ 我的第一個守門寫錯了**：`( pgrep … && echo … && exit 1 )` 裡的 `exit` 只退出**子 shell**，
  主 shell 照樣往下啟動。**守門必須在當前 shell 退出，或者直接 `|| exit`**。
  它下一個版本要寫成 `pgrep -f "llama-server -m" >/dev/null && { echo …; exit 1; }`（不加子 shell）。

---

## 13. 那個獎品換成 decode t/s 是什麼數字

**換算口徑**（門戶自己的公式，`AGENT_HARNESS_PORTAL.html` / `fleet_export.json`）：

```
t/s = 1000 / step_ms × mean_len      ，mean_len = 2.40 （接受的 token / 步，MTP ON 交付形狀）
```

⇒ `2.40 / 0.09939 = 24.15`（門戶引的 t/s 上界）、`2.40 / 0.13946 = 17.21`（同一家族現況）。

| 版本 | step_ms | t/s | 性質 | 出處 |
|---|---:|---:|---|---|
| 探針天花板（D0 `SUBMIT_AHEAD`） | 99.39 | **24.15** | 故意錯、不可交付；**仍差 25 的 3.5%** | G1 §1 / 門戶 |
| 「S2 完美」`total → union`（所有邊界） | 104.87 | 22.9 | 推算，**從未達成** | §2.3 |
| **★ S2 誠實天花板（只有 31.9% 邊界可免等）** | 128.42 | **≈ 18.7** | **推算**（本格就是答案） | §2.3 |
| 現況（同一家族） | 139.46 | 17.2 | 同推算 | §2.3 |
| 交付口徑生產基準 | — | 12.0–12.9（中位 12.62） | portal；⚠ `http_duo` 的讀數自己標「t/s 不可引用」 | portal |

**⇒ 把力氣轉向重疊，對 decode t/s 的誠實兌現是 17.2 → ≈ 18.7（引擎內部口徑）＝ ×1.087。**

**三個必須一起講的限定：**

1. **可免等的邊界只有 31.9%**（`by_m.0.n = 1251/3921`）。零 miss 的層才真的閒（`cb` 中位 0.01 ms），
  而 `cb_share.pct_from_layers_with_miss = 99.775%`——**68.1% 的邊界仍有真工作在等**，不能免費穿過。
   上面那個 34.59 ms（`total → union`）是**全部**邊界的帳，不是可拿的帳。
2. **交付口徑的數字未知**：「兩台儀器之間沒有可用的校準」，而按同一個比值只會**更小**。
3. **25 t/s 的門檻不是靠 S2 過的**。18.7 離 25 很遠。門戶自己的路徑是：25 需要
   `mean_len ≥ 2.485`（**+3.5% 的 accept 數**），而探針天花板 24.15 就只差那 3.5%。

**⚠ 一個對本文件先前說法的更正**：§11 之前我把這條路的獎品寫成「約 9–15% 的步 ⇒ ×1.09–1.15」，
那是拿 `gap_sum` 與 39×0.32 ms 的地板推的，**沒有把 31.9% 這個可免等比例算進去**。
本節的 18.7（×1.087）才是記錄裡誠實的那格，而且它比記錄一直在引的最保守格 19.0 還低。

---

## 14. 「到 25 t/s 的最後 3.5% acceptance」這條路是錯的 —— 真門票是 `f` 20% → 8%

### 14.1 那個 3.5% 是跨 run 組合商數的產物，而記錄已經禁掉了它

`門戶` 的「跳過 25 只需 mean_len ≥ 2.485（+3.5%）」是拿 **`mean_len 2.40`（一處）** 配
**`step 99.39 ms`（D0 探針，故意錯、不可交付）** 算出來的。
而 `docs/DECODE25_CEILING_2026-09-20.md`（**定案**）的開頭就寫：「下面全部用**同一輪、同臂、
哨兵認可窗口**的讀數，**不再跨 run 組合商數**」。它的算術（同一 run 反推 `S0` 與 `m`）：

```
step_round(3) = 207.8 ms   ,  C = 1 + m·k   ,  m = 0.474
S0 = 207.8 / 2.422 = 85.8 ms
t/s(k) = 1000 · mean_len(k) / (S0 · (1 + m·k))   ,  mean_len(k) ≤ k+1
```

| k | 現況 a=0.537 | 完美接受 a=1.0 |
|---:|---:|---:|
| **3（交付形狀）** | **12.57** | **19.25** |
| ∞（極限） | — | **24.59 < 25** |

**三條判死線**：① k=3 即使完美接受（`mean_len` 撞硬上界 4）也只有 **19.25**；要 25 需
`mean_len ≥ 207.8/40 = 5.20 > 4` ⇒ **無解**。② 加深 k 只從 12.16 走到 13.16（**+8%**）。
③ 極限 `1000/(S0·m) = 24.59` ⇒ 仍差 **1.7%**。

⇒ **「+3.5% acceptance」要動的東西不存在**：在交付形狀（k=3）上，它需要 `mean_len` 超過
自己的硬上界。而這與 §13 的 18.7 也對不上 —— 因為 18.7 用的是 `step 128.42`、DECODE25 用的是
`step_round 207.8`。**三個步時（99.39 / 128.42 / 207.8）來自三種不同口徑，不能混用。**

### 14.2 ★ 真門票（DECODE25 §7，物理頻寬帳）

同一份定案文件的 §7 把判決改寫了，而且那段是可驗的算術：

```
機型 Mac16,12（Air M4），規格頻寬 120 GB/s；模型 12.72 GiB / 3.12 bpw；A3B ⇒ 每 token 必讀 1.171 GB
物理上限（100% 命中 RAM）= 9.76 ms/token = 102.5 t/s
t = bpt·[(1−f)/BW_ram + f/BW_ssd]   ，f = 每 token 讀取量落到 SSD 的比例

f = 20.0%  ⇒ 85.9 ms / 11.64 t/s   ← 與實測 S0 = 85.8 ms / 11.66 t/s 吻合到小數第一位
25 t/s（40 ms）⇒ f ≤ 7.9%      16 t/s ⇒ f ≤ 13.9%
```

**⇒ 25 t/s 的門票是 `f`：從 20.0% 降到 ≤7.9%。** 而且 20% 只貢獻 0.0667/0.0733 = **91% 的時間** ——
兩成的位元組吃了九成的時間。（順帶：20% × 1.171 GB × 11.66 t/s = **2.73 GB/s**，
正好是 Air NVMe 的 ~3 GB/s 上限 ⇒ **decode 期間 SSD 是飽和的**。）

**兩個門，而同一個靶：**

| 路 | 需要 | 現況 | 幅度 |
|---|---|---|---|
| 降 `m`（每 draft token 的攤薄係數） | k=3 需 `m ≤ 0.073`；k=8 需 `m ≤ 0.184` | 0.474 | **降 61–85%** |
| 降 `S0`（無 MTP 單 token 成本） | `S0 ≤ 43.1 ms` | 85.8 ms | **×0.50** |

而 `m` 的機械解釋（「每多一個 draft token 約 46–59 ms 固定代價，**pool budget 的主項**」）
指向同一件事：**25 的門票不在 dispatcher、也不在 k，而在 expert cache pool 的每 token 成本。**

### 14.3 因此最小實驗不是「提高 acceptance」，是「量出 `f` 是真的住在哪」

§7 自己把 `f` 標成**上界解釋**：「**20% 是上界解釋** —— 真實的 SSD miss 率可能更低，
剩下的算在 dequant／kernel 效率上」。⇒ 在花任何工程力氣之前，先把那 20% 拆開：

**E-A｜量實際的每 token SSD 位元組（唯一入口，零重建）**

```
臂   : prod25 一支，現有計數器：hit% / misses / us-miss / io MiB/s / read_mib / pread_us / n_gen
讀數 : ssd_bytes_per_token = read_mib × 1.048576 GB/MiB ÷ n_gen
       f_actual = ssd_bytes_per_token ÷ 1.171 GB
判準 : f_actual ≈ 0.20 ⇒ §7 擬合成立 ⇒ 靶是「把 4.72 GiB 從 SSD 拉回 RAM」
       f_actual ≪ 0.20 ⇒ 那 20% 大部分不是 SSD，而是 dequant／kernel ⇒ 靶完全換人
```

**E-B｜拆 `m`：MTP on/off 在「其他完全一樣」下比 `ssd_bytes_per_token`**

因為 `m` 被描述成「pool budget 的主項」，所以如果 draft／verify 的流量就是把池預算打爆的東西，
則**同一 run 形狀下 MTP off 的 `ssd_bytes_per_token` 應該明顯更低**。這是唯一能分辨
「池太小」與「MTP 把池喫掉」的一支臂。（參考點：prod-new（MTP off）實測 13–14 t/s。）

**E-C｜推翻判決的免費測試（§6 已經給了）**

> 「若要推翻本判決，唯一的方法是量到「**同一 run 內 t/s ≥ 12.57 且 step_round ≤ 160 ms**」」

交付記錄已經有 12.57，缺的是**同一 run 的 `step_round ≤ 160 ms`**。這是一支臂就能問的。

### 14.4 對本文件（以及對整個 G1 家族）的意義

**S2 與 25 t/s 是兩個不同的門，而 S2 解不了 25。** S2 的誠實天花板 18.7（§13）；25 需要在那之上
再拿 **+34%** 的 `mean_len`（2.40→3.21 @ step 128.42），而那正是 DECODE25 §3 證明會在 k=3 撞牆的東西。
⇒ **如果目標真的是 25，S2 不該排第一；`f`（或 `m`）才是。**

但 S2 仍然有它自己的價值（§11 之前已述）：把被序列化喫掉的 8.7% 拿回來、並且**把 44% 那個懸了一週的
未知數結案**。它只是**不是通往 25 的門**。

---

## 15. ★ E-A 執行結果：`f` 不是 20%，是 **~2%** —— decode 不是 SSD-bound

**執行**：2026-09-28 13:12，`CGC_SERVER_PROFILE=prod25`（MTP ON）、一支臂、一個請求。
log `Backup/cgc_logs/llama_server_20260928_131256.log`。

### 15.1 兩個獨立儀器都指向同一個量

| 儀器 | 讀數 | 範圍 |
|---|---|---|
| **OS**（`vm_stat Pageins` 差值×16384） | **398,052 頁 = 6219.5 MiB = 6.22 GiB** | 請求窗（47.9 s） |
| **引擎**（`phase=final read_mib` / read-shape `total_bytes`） | **9.02 GiB**（`jobs=24912`） | 整個 process |

閒置基準 604 頁/10 s ⇒ 47.9 s 內背景約 45 MiB，**可忽略**。兩個儀器差一個量級以內 ⇒ 互相背書。

### 15.2 換算：`f_actual` ≈ 1.5%（上界）

```
請求 usage : prompt 220 + completion 156 = 376 tokens（finish=stop）
模型應讀量 : 376 × 1117 MiB = 419,992 MiB = 410 GiB
實陸 SSD   : 6.22 GiB（OS 窗）  ／  9.02 GiB（引擎全程）

⇒ f_actual = 6220 / 419992 = 1.48%      （引擎全程口徑： 2.20%）
```

**而請求窗還包含了 listen 之後的任何預熱 pagein ⇒ 6.22 GiB 是解碼 SSD 流量的「上界」。
結論只會更強。**

### 15.3 把 `f_actual` 代回 §14 的恆等式 —— 它不再吻合

```
t = bpt·[(1−f)/120 + f/3]

f = 20%（§7 的擬合） ⇒ 85.9 ms   ← 與實測 S0 = 85.8 ms 吻合到小數第一位
f = 2%（本輪實測）   ⇒ 17.4 ms   ← 只有實測的 1/5
```

⇒ **§7 那個「吻合到小數第一位」不是唯一的解釋**。把 `f` 換成量到的值，85.8 ms 就解釋不了。
而 §7 自己已經標了那個可能：「**20% 是上界解釋** —— 真實的 SSD miss 率可能更低，剩下的算在
dequest／kernel 效率上」。**本輪就是把那個「真實值」量出來的：2%。**

### 15.4 而且讀取形狀說它不是帶寬受限

```
read shape: jobs=24912  bytes=9688858624 (0.37 MiB/job)  us/job=1856  effective_rate=210 MiB/s
pool      : pool_cap_slots=143 slots_layer=143 union=64
miss     : compulsory=6458 capacity=2240 (74.2% / 25.8%) evict=8586  hit_pct=81.62
critical : fill_wait_us=101314（= 0.101 s）  而請求總時 47.9 s
MTP      : fast calls=4366 union=81428  verify calls=4061 union=78988  cold(ZERO)=0
```

1. **`0.37 MiB/job` 取 1.86 ms**。若真是 3 GB/s 的 SSD，這個大小的讀取應該只要 **0.12 ms** ——
   量到的 1.86 ms 是它的 15 倍。⇒ **受限的是每次讀取的固定開銷（issue／queue 延遲），不是帶寬。**
2. **臨界路徑上只等了 0.101 s**（`fill_wait_us`，且 header 擔保它與 wall time 可比）／ 47.9 s ⇒
   **請求執行緒幾乎沒有被 I/O 阻塞。**
3. `capacity=2240`（佔 miss 25.8%）⇒ 有淘汰但不大；**不是 thrashing 主導**。

### 15.5 所以結論（以及它取消了什麼）

**⇒ 這台機在 decode 時不是 SSD-bound。權重讀取的 ~98.5% 來自 RAM，不在磁碟上。**

**它取消的是 §14.2 表下面那句「把 4.72 GiB 從 SSD 拉回 RAM」** —— **那 4.72 GiB 本來就在 RAM 裡**
（無論是 pool 還是 page cache）。⇒ **降 `m` / 降 `S0` 的靶不是池容量，是「0.37 MiB 一次、1.86 ms 一次」
的那 24912 次讀取的發行方式，以及那個 11.4% 的 RAM 帶寬效率本身。**

### 15.6 這個結果不能支持的事（樣本很小，明寫）

1. **一支臂、一個請求、一個 prompt。** 不是分布。
2. **本輪的絕對 t/s 不可引用**：156 token / 47.9 s ≈ 3–4 t/s，而交付基準是 12.6。
   這個盒子今天一直在記憶體壓力下（swap 一度 10.2/11.2 GiB）—— 所以「慢」不是本輪的量，
   但「慢不是 SSD 造成的」不受影響。
3. `vm_stat Pageins` 是**全系統**的（背景基準已量、可忽略），而 `read_mib` 只在 teardown 印，
   所以 engine 口徑無法切出「只有請求那段」。兩個口徑都給了。
4. **沒法分辨「真的一個 pagein 都沒發生」與「發生在 mmap 路徑上而 pageins 沒計到」** ——
   後者要靠 `footprint` / `vmmap` 審計，本輪沒有做。
5. 本輪 `MTP fast calls=4366` 而 `draft calls=305` ⇒ 這支臂的 draft 實際幾乎沒在用，
   所以它對 `m`（每 draft token 的攤薄係數）**沒有代表性**。E-B 還是要做。
