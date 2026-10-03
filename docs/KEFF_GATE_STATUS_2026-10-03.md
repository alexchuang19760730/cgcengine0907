# k_eff 的下一個閘門：**具名的那個已經修好了**；剩的是已揭露的 i=0 殘餘

**日期** 2026-10-03 · 載體 `Nail-Qwen3.6-35B-A3B-MTP IQ3_XXS-denseIQ4X` · prof `prod-new` · cell `delivery`（`p0_n128_d512_r3 ws64`）

## 0. 一句話

有人說「`k_eff` 仍 ~1.6 ⇒ 下一個閘門是 `is_mem_shared` 的 draft 位置碰撞」。
查證結果：**那個碰撞已在 HEAD 修好並提交**（`9d72e3751` 的 `one_position_drafts`），而 **i=0 的 KV 尾巴守衛**
（`speculative.cpp:1528`）也在 HEAD~2 就有。
本輪把它量成事實：**k=3、width=8 之下 `k_eff = 2.167`**（不是 1.6），draft 深度直方圖的眾數在 **3（58.1%）**。
剩下的唯一殘餘是 `KEFF_CAP §4` 早就揭露的那一類：**13.5% 的輪次 draft=0，全部是 `llama_decode[0]` 失敗且 `X == Y`**。

## 1. 查證（一手，全部可複查）

| 問 | 證據 | 結論 |
|---|---|---|
| 09-26 的 `one_position_drafts` 修法在嗎？ | `grep -c one_position_drafts src/llama.cpp/common/speculative.cpp` = **4**；`git show HEAD~2:…` 也 = **4**；`git log -S` → **`9d72e3751`** | **已在 HEAD** |
| 它的支撐函式在嗎？ | `llama_model_is_assistant_block` 在 `src/llama-ext.h:166` ＋ `src/llama-model.cpp:3015`，兩檔 `git status` **乾淨** | **已在 HEAD** |
| i=0 的 KV 尾巴守衛在嗎？ | `speculative.cpp:1528` `if (pos_max >= N) llama_memory_seq_rm(...)`；`git show HEAD~2:… \| grep -c 'pos_max >= N'` = **1** | **已在 HEAD~2** |
| 本輪對 `speculative.cpp` 動了什麼？ | `git diff HEAD~2 HEAD -- …/speculative.cpp` ⇒ 只有 `alt`（**+30/−0**），不碰任何位置語意 | 與此議題無關 |

⇒ **`docs/MTP_KAXIS_RECOMPUTE_2026-09-30.md` §4.2b 那句「卡點換人了…是 `is_mem_shared` 的 draft 位置碰撞」是過期的。**
（那句指向的正是 09-26 已修並已提交的缺陷。）

## 2. 本輪量測（`Backup/spec_tree/keff_gate*`）

臂：`prod-new:CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:140;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;LLAMA_BENCH_SPEC_DBG=1`
＋ `--spec-type draft-mtp --spec-draft-n-max 3`。相位：`CGC-PHASE-SPLIT: routable=139 → width=8`。
`incomplete=False`（跑完）。`avg_ts = 12.268`（reps 19.571／8.949／8.284，**`attribution=both`**）
<span style="color:#b45309">⇒ 該 t/s **不可引用**（本節只用計數與直方圖）</span>。

### 2.1 draft 深度直方圖（引擎自己的 `SPECDBG round`，222 輪）

| draft | 輪數 | 佔比 |
|---|---|---|
| 0 | 30 | 13.5% |
| 1 | 32 | 14.4% |
| 2 | 31 | 14.0% |
| **3** | **129** | **58.1%** |

**`mean_draft = k_eff = 2.167`**（k 設 3）。

### 2.2 殘餘失敗：**只有 i=0，且全是 `X == Y`**

```
spec draft: llama_decode[0] returned -1     × 6   （llama_decode[1] 的次數：0）
M-RoPE X=Y: 514/514, 517/517, 580/580, 543/543 …   × 30 行，全部 X == Y
```

⇒ 與 `docs/KEFF_CAP_2026-09-26.md` §4 記的「剩 11/76（14%）是 `llama_decode[0]`、`X == Y`」**同形同量級**
（本輪 13.5%）。這是**已揭露的既存殘餘**，不是新發現。

## 3. 這對 k_eff 的意義

- **`k_eff = 2.167` @ k=3** ⇒ 09-30 的「k_eff 仍 ~1.6」不能再當成 k=3 的常態；
  那 1.59 是 **k=7** 那一支的量，而它在 `docs/L254_K_AXIS_ECON_2026-10-01.md` 的 k 掃描裡本來就是 −51% 的點。
- **殘餘的代價是可算的**：13.5% 的輪次 draft=0 ⇒ 那些輪的 E=1。
  若把 i=0 碰撞修掉、讓它們也拿到 draft=3，粗估 `k_eff` 從 2.167 往 3 靠
  （**上界 3，不是無限**）。這是「還差多少」的算術，不是承諾。
- **修它的方向**（來自 §2.2 的形態，未實作）：draft 的**第一步**重錨在 target 剛寫過的位址。
  守衛 `:1528` 只在「新一代開始」時丟尾巴；輪內的 i=0 重錨沒有對應處置。
  因為 KV 是**共享**的，直接 `seq_rm` 會動到 target 的 cell ⇒ 這是一個 **shared-KV 語意** 的改動，
  不是一行 guard，且必須以 oracle 判等價（動它會改變 draft 的輸入）。

## 4. 誠實邊界

- 本輪**沒有**修任何程式碼；所有結論都是既有碼 ＋ 一次量測。
- `k_eff` 是**引擎自己的計數**（`SPECDBG round` 的 draft 欄），不是 t/s；它不受窗口髒影響。
- `avg_ts` 那一趟 `attribution=both` ⇒ **不可引用**，也不參與任何判詞。
- **未跑**：`KEFF_CAP §3(iii)` 的 k-sweep（`--warm-skip 64`、多 k）—— 要把「修 i=0 值多少」變成可引用的數字，
  那一趟仍然欠著。
- 09-30 的 `k_eff 1.59`（k=7）未被本輪重跑；本輪用的是 k=3，兩者**不同臂，不可互相歸因**。

## 5. 產物

- runner：`Backup/spec_tree/keff_gate.sh`
- 產物：`Backup/spec_tree/keff_gate{,.json,.logs}`
- 來源文件：`docs/KEFF_CAP_2026-09-26.md`（根因與修法）、`docs/MTP_KAXIS_RECOMPUTE_2026-09-30.md` §4.2b（本輪更正其判詞）
