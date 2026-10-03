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

---

## 6. 追加（2026-10-03 16:5x）：① 已落地、② 的前提被推翻、③ 窗口不可用

### 6.1 ① 落地：新臂一律 `40-40:140`

`scripts/check/charters/exp-marginal-decomp.yaml` 的臂字串已改（`40-40:16` → `40-40:140`），並把**理由**寫進卡裡
（cap 決定 `min_usable = cap-1` ⇒ `decode width = floor(min_usable/top_k)`；cap<24 ⇒ width<2 ⇒ 任何 ≥2 顆的步被判成 PREFILL 圖）。
09-28/29 的 dated 卡片**留原樣**（歷史對照、不回改）。10-01 的 `e-l254-kaxis-sweep` 本來就已是 `40-40:140`。

### 6.2 ② 的前提被推翻：乾淨窗裡**沒有** draft=0 的輪次

用 `Backup/l254_kaxis_20261001/k*.stderr.log`（10-01、**全臂 `attribution=none`、NOMINAL、swap 0**）逐 k 數引擎自己的 `SPECDBG round`：

| k | mean_draft（= k_eff） | 直方圖 | draft=0 輪次 |
|---|---|---|---|
| 1 | 1.000 | {1:230} | 0 |
| 2 | **1.797** | {1:47, 2:185} | **0** |
| 3 | **2.500** | {1:38, 2:22, 3:136} | **0** |
| 5 | 3.843 | {1:30, 2:15, 3:17, 4:7, 5:109} | **0** |
| 7 | 4.726 | {1:46, 2:23, 3:12, 4:7, 5:5, 6:3, 7:112} | **0** |

⇒ **今天那 13.5% 的 draft=0 是髒盒效應，不是系統性缺陷**（09-26 `KEFF_CAP §4` 記 11/76≈14%，10-01 乾淨窗記 **0/1090**）。
⇒ 因此「修 i=0 值多少」**不能**用今天的數去定價 —— 乾淨窗的短少是另一回事：
  它集中在「只拿到 1 顆」的輪次（k=3：38/196 = 19%），而**不是 0 顆**。
⇒ **本節是對 §3「若把 i=0 碰撞修掉、k_eff 從 2.167 往 3 靠」那句的更正**：那個 2.167 本身是髒盒產物，不能當基準。

### 6.3 ③ 配對量測：**守門已放開**（新），但**窗口不可用**（今日未取得）

**新**：`scripts/check/llama_bench_matrix.py` 的 fail-closed spec 守門原本會拒跑「臂帶 `CGC_SERVER_MTP=1`
而 cell 沒有 `--spec-type`」—— 而 `--spec-type` 是**整趟級**旗標，加了它**每一臂**都投機
⇒ 守門的實際效果是**禁止同 session 的 off/on 配對**，這正是記錄裡「交付 cell MTP 增益 UNRESOLVED（缺配對量測）」的結構成因。
已放開一種情形：臂自己帶**按臂 spec shim** `LLAMA_BENCH_SPEC[=1]`（＋可選 `LLAMA_BENCH_SPEC_DRAFT_N_MAX`，
`llama-bench.cpp:1350-1358`，只在 CLI 沒給 `--spec-type` 時生效）⇒ 那一臂**只有它自己**投機。
守門其餘部分不動（`=0`／空字串仍拒跑）；`--selftest` **11/11 unit ＋ 2/2 end-to-end**。
⚠ 放開時會**大聲印** `!! note: ... PER-ARM spec shim ...`，並要求在產物裡把**實現的深度**讀回來（SPECDBG／ACCEPT），不得從 cell 假設。

**但今日未取得可引用讀數**（`Backup/spec_tree/mtpon_rec/`）：

| 臂 | 結局 | avg_ts | 逐 rep | attribution |
|---|---|---|---|---|
| ON（k=2、cap 140） | `incomplete=False` ✅ | 9.134 | 7.55／9.74／10.11 | **`swap`**（growth 4554 MiB、max_swap 11685 MiB > 實體 9.2 GB） |
| OFF（`prod-new`） | `incomplete=False` | 8.241 | 10.34／**3.88**／10.50 | **`swap`**（growth 890 MiB、max_swap 11910 MiB） |

⇒ 兩臂皆 `attribution=swap`、`max_swap` **超過實體記憶體** ⇒ **逐字不可引用**。
**可引用的收穫只有一條**：**cap 修復在 k=2 上也成立**（`incomplete=False`；`:16` 之下同型臂是 5/5 崩）。
另註：ON 臂 `llama_decode[…] returned -1` = **0**（今天 k=3 那趟是 6）—— 與 §6.2 一致，碰撞是盒子狀態相依的。

### 6.4 「推進可認證紀錄」的前置條件（**唯一缺口**）

需要一個 **乾淨窗口**：起跑 `attribution` 必須是 `none`，即
- `swap` 存量先降回 < 2 GB，且
- 起跑 `pages_available` 越過 `l255_close.SURVIVAL_MB`（7703 MB）——今天量到 6665／9849 兩次都不穩。

而**最大的 hog 是 WorkBuddy 自己**（本輪閘門原文：`WorkBuddy Helper (Renderer) 2723 MiB` ＋ Electron 691＋420 MiB），
⇒ 這一條**要 operator 手動做**（關 app 或重開機），不是本線能排的。
在那之前：**MTP-on 進不了可認證紀錄**——not because the engine is broken, because the box is.
