# Portal 的「覆蓋痕跡」：一個已修，五個補標記，五個只清點（2026-09-20，零 GPU）

**起因**：`G1.premise_b` 自己打自己 —— (c) 寫著 `THE FIX IS ONE LINE, PREPARED AND SYNTAX-VERIFIED,
NOT APPLIED`，而**同一個欄位的尾巴**記錄著那份量測，而那份量測**沒有套用那個補丁就不可能存在**。
用 `Bash` 讀那一欄的人會得到 03:12 之前的答案。

**這份文件做三件事**：① 修 `G1.premise_b`；② 把**同型**的欄位找出來（方法可重跑）；③ 對其中
**需要值或組合決策、不是加標記就能修**的五筆，只清點、不動，並寫下為什麼。

**全部改動未 commit。** 檔案：`agent_harness/portal/targets.json`（6 個欄位）、
新增 `Backup/patch_g1_premise_b_unify_20260920.py`（dry-run 預設、8 個錨點各自唯一性守衛、
改完必須仍是合法 JSON、且**只有預期的欄位改變**才寫檔）、本文件。

---

## 0. 為什麼是腳本，而不是手改（兩個都是今天踩過的）

1. **這個檔不能 round-trip。** `targets.json` 帶刻意的空行分隔，`json.dumps` 重生會把 70 KB
   全改寫 ⇒ 在一個**共編**檔案上製造看不完的 diff。所以每筆改動都是**在 JSON-escaped 域裡的
   錨定子字串替換**，不是重序列化。實測：`json.load` → `json.dumps(indent=2)` 與原檔**不相等**
   （69649 → 71985 bytes，第一個差異在 720：`],` 後面少一個空行）。
2. **本輪的編輯工具根目錄是主 clone，不是這個 worktree。** `flashkv-devserver` 是
   `flashkv0516/.git/worktrees/flashkv-devserver`（主 clone 在 `dev`，這裡在
   `demo/sweet-spot-windows-fix`）。相對路徑會落到**另一個 worktree、另一條分支**。本輪第一次
   `write_file Backup/…` 就是這樣跑到 `flashkv0516/Backup/` 去的（已搬回）。**所有路徑請用絕對路徑。**

---

## 1. 已修：`G1.premise_b`（3 筆錨點，同一個欄位）

| 錨點 | 改了什麼 |
|---|---|
| 欄位開頭 | 新增 **ANSWER FIRST** 段：前提 B **不成立**（交付 verify 步 ntok=4 是 3449/8160 = 42.3%），⇒ S3 無前提、S2 是活路；並**指名**下面哪兩句被這次量測取代 |
| (b) 尾句 | `a prior, not an answer` 加上 `[SUPERSEDED THE SAME DAY]`：那句是關於**那 45 筆舊讀數**的陳述，不是關於儀器能力 |
| (c) 全段 | `NOT APPLIED` → **APPLIED**，附上**產物見證**而不是計畫：commit `bffbfeeeb`、補丁兩 hunk、`src 03:07 → libllama.0.dylib 03:08`、build rc=0 且 warning 剖面不變（7 = 7）、新 predicate 的字串在出貨的 dylib 裡而舊的字面 gate **命中 0 次**、逐 graph 行出現 `{1,2,4,8}` 的 ntok 分布（**ntok=4 的列在 `== 1` 下不可能出現**）⇒ 尾巴那段是**交付配置的量測**，不是「將如何量測」的描述 |

**為什麼要留著舊句而不是刪掉**：這個 repo 的價值有一半在「錯誤的方向也被記錄」。刪掉它，下一個
人就會重踩；標記它，錯誤變成教材。所以三段都是**保留原文 + 就地標記**，只有 (c) 的「未套用」
這個**事實陳述**被改成已套用（它不是觀點，是狀態）。

## 2. 同輪補標記的 5 個欄位（只加指向，不動任何數值或結論）

| 欄位 | 原本的病灶 | 補了什麼 |
|---|---|---|
| `G1.candidate_family` | `has its premise FALSIFIED IN DIRECTION BUT NOT IN MAGNITUDE` —— 而**magnitude 已經量到了** | 改成「方向否定**且**量值已量到（42.3%，見 premise_b）」，並補上 draft 步 0/612 那個不對稱 |
| `G4.necessity` | `NOT REQUIRED UNLESS G6 STALLS`，理由建立在 G1 的 ×1.702 天花板與 `24.15 t/s` 上 | 前置 **SUPERSEDED IN PREMISE**：×1.702 只在故意錯的 `CGC_SUBMIT_AHEAD=1` 探針下存在（全 NaN dump、殺 MTP），而決定這個閘門自身前提的量測留下 S2 這條**未實作未量測**的路 ⇒ 「不需要 G4」建立在一個**不存在的可交付讀數**上。原句整段保留 |
| `G4.resolution` | 開頭判 `NOT MEASURABLE`，而**同一物件的 `measurement_floor` 說那用了錯的統計量、其實可量** | 前置 **READ `measurement_floor` FIRST** 指向 |
| `G4.why_not_a_constant` | 門檻在 mean_len 2.40 下推導、且假設 `total ~ union/0.95` | 附 **REGIME CAVEAT**：交付那輪的 mean_len 是 3.375，而且 `union_sum` **超過自己的步**（union/total 1.13–1.15；union+gap = 1.14 × total，`G1_ACHIEVABLE_CEILING` §7 標為**未解**）⇒ 0.95 這個換算係數在那個 regime 不成立，**不能把 union 的削減直接讀成步的削減** |
| `G6.to` | 兩個分支的條件式，第一個分支（G1 落地 ⇒ 2.485）**已經死了** | 補 **WHICH BRANCH GOVERNS**：**第二個**，因為 G1 未落地且其天花板不可交付 ⇒ 有效門檻 3.479；實測 3.375，差約 3% |

## 3. 只清點、未動的 5 筆（需要決策，不是加標記）

| 欄位 | 為什麼算覆蓋痕跡 | 需要的決策 |
|---|---|---|
| `G3.stage_a` | 尾巴留著 `so G4 may not be needed at all` —— 與 `G4.necessity` **同一個** ×1.702 前提，而它已被 `G1.premise_b` 取代 | 這句是「G4 的組合決策」，屬線 I 的組合決定 |
| `G6.evidence` | `G6 is therefore the CHEAPEST remaining move: +3.5% … if G1 lands`，前件已死 | 是否改寫成「在 G1 未落地的分支下最便宜的是什麼」——那是 G6 的策略，不是文字 |
| `G1.from` | `25.0%`。同一天 `to` 被改寫成「5% 不可達、可達的是 (a) cb 或 (b) 邊界數」⇒ 這個**數值屬於哪個成分**沒有任何欄位說 | 要選：它記的是舊 metric，還是要重新表述成兩個成分各一個 `from` |
| `G1.evidence` | `ceiling 25.0% of the step at the stable state` —— 引用的正是上面那個舊 metric | 同上，跟著 `from` 一起改才一致 |
| `G4.from` | `104.60 ms` 是 **E2b** 的 union；交付那輪實測 64.15／71.73 ms，且 union/total > 1 | 這牽涉 G4 的 `to` 要對哪個 regime 陳述 ⇒ 屬 G4 的門檻決策 |

**這五筆現在都還是「照字面讀就會得到錯答案」。** 建議把它們排進各自的線，而不是由本輪代改 ——
它們的修法有**值**的選擇（哪個 regime 的數值才算這個欄位的數值），那不是標記能解決的。

---

## 4. 方法（可重跑），與它抓不到什麼

**候選清單**：對 `targets.json` 的 18 條閘門、45 個長字串欄位（≥120 字元）掃三種形狀 ——
(i) `PLANNED/NEVER implemented` 出現在 `APPLIED/MEASURED` **之前**（就是 premise_b 的病）;
(ii) 同欄出現兩個以上日期；(iii) 欄位**開頭**沒有標記詞、但尾巴有
(`SUPERSEDED/RETRACT/CORRECTION/WRONG STATISTIC/DEAD as of/FALSIFIED/…`)。
本輪結果：**8/45 被標記**，其中 **3 個是假陽性**（`G2.coverage` 的 `CORRECTED 2026-09-19 BY
MEASUREMENT`、`G4.work_order` 的 `RETRACTED`、`G7.from` 的就地更正都在欄位中段而非開頭；
實質是**正確**的做法）。⇒ **這張清單是候選，不是判決**；每一筆都要讀過才算數。

**它抓不到的三類，本輪靠人讀補上**：
1. **跨欄取代**：修正寫在**另一個欄位**（`G4.resolution` ↔ `measurement_floor`）。
2. **跨閘門取代**：修正寫在**另一個閘門**（`G4.necessity` ↔ `G1.premise_b`）。
3. **值沒有 regime 標籤**：欄位本身沒矛盾，但它的**數值**屬於已消失的 regime
   （`G1.from`／`G4.from`／`G1.evidence`）。

**根因**：這些欄位是**追加式跑馬燈**（append-only running records），而**開頭不會被更新**。
所以正確的寫法有兩半，本輪各示範一個：
- **同欄**被取代 ⇒ **在欄位開頭**放 `ANSWER FIRST` / `SUPERSEDED`（`G1.premise_b` 現在的樣子）；
- **跨欄/跨閘門**被取代 ⇒ 標記要下在**被取代的那一端**，不能只下在修正的那一端
  （`G4.necessity`／`G4.resolution` 現在的樣子）。

## 5. 驗證（本輪實跑）

| 檢查 | 結果 |
|---|---|
| `python3 Backup/patch_g1_premise_b_unify_20260920.py`（dry run） | 8 錨點各自唯一、8 筆計畫、**6 個欄位改變**、只有預期的欄位 ⇒ 才寫檔 |
| 同上 `--apply` | 寫入成功；JSON 仍合法 |
| `git diff --stat agent_harness/portal/targets.json` | `9 insertions(+), 8 deletions(-)`（其中 3+/2- 是本輪之前**未 commit** 的 G6 `measurement_preconditions`，被完整保留） |
| `python3 agent_harness/portal/build_portal.py --check` | **rc=0**：18 條閘門、目標樹指針全可解析、2 個目標 × 97 筆綁定完整 |
| `python3 agent_harness/portal/build_portal.py --self-test` | **18/18 checks passed**（含「不寫任何檔到真正的 repo」那條陰性對照） |
| `python3 scripts/check/commit_gates.py --list` | 8 個 gate id 與 `what` 全部照舊 ⇒ commit 閘門的詞彙沒被我動到 |

**誠實邊界**：(1) 本輪沒有跑任何需要 server 的閘門（沒有 GPU 工作、零 build）；(2) 第 2 節那 5 筆
的「superseded」判定是**讀出來的**，不是機檢的 —— 掃描只把它們列成候選；(3) 第 3 節那 5 筆仍是
照字面讀會錯的狀態，這是**刻意留下**的，因為它們要的是決策而不是標記。


---

## 6. 十天後複查（2026-09-30，零 GPU）：本輪的 6 筆**一筆都沒落地**，而目標層從來不在掃描範圍

**起因**：有人問「既然 decode ≥25 判死，為什麼 `targets.json` 還在認為 25 可行？」

### 6.1 第 1／2 節那 6 筆：從未進入任何 commit

| 檢查（可重跑） | 結果 |
|---|---|
| `grep -c '<marker>' agent_harness/portal/targets.json`，marker ∈ {`ANSWER FIRST`, `SUPERSEDED THE SAME DAY`, `SUPERSEDED IN PREMISE`, `WHICH BRANCH GOVERNS`, `READ measurement_floor FIRST`, `REGIME CAVEAT`} | **各 0** |
| `git log -S'<marker>' --oneline -- agent_harness/portal/targets.json` | **各 0 個 commit** |
| 重跑 `Backup/patch_g1_premise_b_unify_20260920.py`（dry run） | **8 錨點仍全部唯一、6 個欄位仍會被改** ⇒ 腳本本身有效，也證明**沒有套用過** |
| `git status --porcelain -- agent_harness/portal/targets.json` | 乾淨（＝與 `9489b99b5` 一致，70031 bytes） |

§5 寫著「`--apply` 寫入成功；JSON 仍合法」，而 §0 寫著「**全部改動未 commit**」。
兩句合起來只有一個可能：**改在 worktree 裡發生過、沒有 commit、後來被還原或覆寫掉**
（`targets.json` 的歷史裡從來沒有這些標記）。⇒ **十天後的淨效果＝沒有套用。**
沒有任何東西能區分「已套用但未 commit」與「從未套用」—— 除了這份複查。

**§3 那 5 筆「只清點未動」的欄位**（`G1.from`／`G1.evidence`／`G4.from`／`G6.evidence`／`G3.stage_a`）
**全部仍在原狀**，也就是說它們現在仍然「照字面讀就會得到錯答案」。

### 6.2 ★ 本輪新增的範圍：目標層（`targets[]`）從未被納入

§4 的方法只掃**閘門層**的 45 個長欄位。而 `decode-25` 這一格（`targets[]` 的第 2 筆，
自稱「兩個數字目標的**單一真相來源**」）現在寫的是：

```
updated: 2026-09-18
current: 12.62
gap:     2.07×（要 25、現在 12.62）。★「已判掉的三條路…⇒ 到不了」已被量測推翻。
         零改碼上界探針 CGC_SUBMIT_AHEAD=1 量到 ×1.702 … ⇒ t/s 上界 = 24.15 …
         跨過 25 只需 mean_len ≥ 2.485（+3.5%）。
status:  not-met
```

那四個數字逐條對現在的判定：

| 欄位裡的數字 | 現況 | 出處 |
|---|---|---|
| `current: 12.62` | 屬 **MTP-on 輸出 ⇒ 另一個輸出函數** ⇒ 不可作交付讀數；board 的 `C6` 已寫「12.62 已作廢」 | `docs/ABBA_MEASUREMENT_PROTOCOL_2026-09-23.md:4`／`ACCEPTMOE_ADAPTATION_2026-09-24.md:4`／`ANCHOR_12_57_REPRODUCIBILITY_2026-09-28.md:4`／`BITIDENTITY_…:4` 的共同 banner；board `necessary_conditions.C6` |
| `×1.702` | 唯一來源是**故意寫錯**的探針（GPU 讀 stale remap）⇒「**dead by race, NOT by arithmetic**」 | `docs/G1_G7_SWEEP_2026-09-20.md:36`、`docs/DECODE_STEP_BUDGET_2026-09-19.md:639`，以及本檔 §2 的 `G4.necessity` |
| `24.15`（上界） | 建在上面那個探針上 ⇒ 前提不成立 | 本檔 §2 `G4.necessity`（「只在故意錯的探針下存在（全 NaN dump、殺 MTP）」） |
| `2.485` | `G6.to` 的**第一分支已死**；有效門檻是 **3.479** | 本檔 §2 `G6.to` |

⇒ **「25 可行」不是一個立場，是一組沒有 regime 標籤的舊數字**（12.62／×1.702／24.15／2.485）
**＋ 一組從未落地的標記**。

### 6.3 另一邊已經重新定價（本線 09-29）

`docs/SINGLESUBMIT_CORRECT_DESIGN_2026-09-28.md` §37（「空轉減半 −7 ms」沒有實測支撐）、
§39（四塊**各自放在已量上界**相加 ＝ `19.5＋12.0＋5.8＋3.9` ＝ **41.2 ms ＝ 24.3 t/s ＞ 40.0**）、
§51／§52（B 半補丁：備援重算 8.09 ms ⇒ 淨回收為負）⇒ **decode ≥25 判死**。

而且**對方自己的新產物同意判死**：`scripts/check/charters/e-decode25-blocks-2026-09-29.yaml` 的
`verdict_decode25_20260929`（「以現有軸加總到不了…唯一的正面讀數是 **20+**，天花板 19.2」）
與 `docs/NEXT_ACTIONS_2026-09-29.md:53`（「`m-decode25` 維持**判死**，力氣轉 `m-total`」）。

### 6.4 缺的不是判斷，是對帳

兩個「單一真相來源」（`agent_harness/portal/targets.json` 的**目標層** ↔
`scripts/check/decode_board_2026-09-29.yaml` 的 L25 判定）之間**沒有任何閘門**。
`build_portal.py --check` 驗的是「指針可解析、綁定完整」，不驗「這一格的數字有沒有被另一邊取代」。

**最小改動清單（給該檔的業主，3 個欄位）**：
- `updated`：`2026-09-18` → 實際更新日；
- `current`：`12.62` → 交付口徑的值（board 的 `C3` ＝ **11.703**），或直接改成指向 board 而不自持數值；
- `gap`：改寫成「**判死** ＋ 那四個已作廢數字的出處」，並把「已被量測推翻」這句標成
  *SUPERSEDED BY §37／§39／§51（2026-09-29）*。

⚠ 依 §3 的慣例，本輪**不代改**：這是**值**的決策（哪個 regime 的數值才算這一格的數值）。

### 6.5 教訓（本檔自己就是案例）

§1 把 `G1.premise_b` 的 `NOT APPLIED` 改成 `APPLIED` —— 方向完全正確：**寫狀態，不寫步驟**。
但**這份文件的自身修改沒有做到同一件事**：它寫「已修」而沒有把修改 commit，
於是十天後沒有人能分辨「套用了但沒 commit」與「從未套用」。

⇒ **「我修好了」如果沒有 commit，等於沒修。** 產生器／補丁的記錄要能回答
**applied? committed?** 這兩個問題，否則它只是另一則跑馬燈。
