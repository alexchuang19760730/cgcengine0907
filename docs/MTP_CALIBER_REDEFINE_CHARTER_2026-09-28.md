# MTP 口徑重定義立項（2026-09-28）

> 🟢 **狀態：已開立**（operator 2026-09-28 14:2x 裁定同意）
> 前因：**「MTP ON bit-identical 修復立項」已關閉（operator 同批追認）**
> ⇒ 收尾判定全文 `docs/MTP_BITIDENT_CLOSEOUT_2026-09-28.md`；
> 根因全文 `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`
>
> 提出：本線（`線A (ace)`）· **0 build**（純標註立項，不改引擎、不改交付口徑）

---

## 0. 一句話

把 `CGC_SERVER_MTP=1` 從「同一個模型的**加速開關**」明文重定義為
**另一個輸出函數**（與 OFF 不在同一座標系），並把這個事實寫進
**量測入口、推廣閘門與既有結論的引用規則**，避免任何人再拿 ON 的 t/s 去比 OFF 的基線。

## 1. 為什麼（依據，非推論）

| 項 | 內容 |
|---|---|
| 根因 | `--spec-type draft-mtp` ⇒ `common.h:395 need_n_rs_seq()` 硬編碼回傳 `draft.n_max` ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` 的 recurrent（GDN／conv）狀態從「單槽位原地寫回」換成「**K 槽位回滾**」 |
| 性質 | **計算份數本身變了**（`gdn` 30→60、`conv_input` 30→150）⇒ 不是舍入、不是寫回位置 |
| 分歧點 | 第一個 decode（prefill 尾仍 **identical**） |
| 是否可逆 | ⛔ **原理上不可逆**：`n_rs_seq=0` 唯一來源是 `n_max=0`，而那觸發 `GGML_ASSERT`（`llama-context.cpp:3057`，P0-5 實跑）⇒ 沒有任何 config／env 旋鈕能「MTP on 且走原地路徑」 |
| 品質影響 | ✅ **不是缺陷**：兩側 greedy 答案皆通順、語義等價，ON 自身 176/176 可重現 ⇒ 自洽的另一個輸出函數，**不是 garbage** |

⇒ 結論：**ON 與 OFF 的 t/s 數字不在同一座標系**，任何「MTP 加速 X%」不可引用。

## 2. 驗收（B1–B4）

| # | 判據 | 落地點 | 現況 |
|---|---|---|---|
| **B1** | MTP on 的 t/s **不可與 OFF 互比**，跨臂比較一律拒絕並說明理由 | `scripts/check/mtp_promotion_gate.py`（`output_function`／`comparability` 兩節）＋ `scripts/check/mtp_off_baseline.json`（新增 `output_function` 節，schema → **v4**） | ✅ **已完成**（2026-09-28 21:0x）：既有 8 條繼續管**盒況可比性**；**新增獨立語義層** `output_function.identical` ＋ `comparability.interpretable_speedup` ⇒ **ON 即使配對通過且較快，仍不得表述為「OFF 的 X%」**。端到端實測見 §7 |
| **B2** | 引用 ON 數字必須附「輸出函數 = MTP-on」標籤；既有把 ON 當 OFF 加速版的結論全部重標 | `docs/*.md`（**兩批合計 76 份**：第一批 20 份＝字面 `MTP-on`＋`t/s` 同行；第二批 56 份＝引用 `12.57`／`12.62` 且當基線寫） | ✅ **已完成**（2026-09-28 16:4x ＋ 20:5x，皆純插入） |
| **B3** | 交付口徑**維持 MTP off**（`prod-new` 的 `CGC_SERVER_MTP=0` 不動） | `scripts/check/harness.py` profile 定義 | ✅ **已是現狀**，只需明文寫進本卡與收尾文（本卡即為該明文） |
| **B4** | 若要重新追求「ON 當加速用」，**先決條件**＝ON 在乾淨窗口上 paired 快過 OFF（目前方向**相反**：OFF 中位 11.92 vs ON 7.34） | `scripts/check/mtp_promotion_gate.py` ＋ `mtp_off_baseline.json` | ✅ **已完成**（v2/v3 機制 09-27 已有；「**ELIGIBLE ≠ 同一輸出函數**」一條隨 B1 於 2026-09-28 追加，寫進 baseline 的 `output_function.claim_rule`） |

### ★ B1 的精確增量（本立項真正要加的東西）

現有 `mtp_promotion_gate.py` 的 `ELIGIBLE` 只證明「這個 ON 讀數與同場的 OFF 控制組在
同一個盒況窗口內、重現性達標」——它**不能**證明兩者算的是同一個東西。
⇒ 閘門要新增的判讀層：

> **ELIGIBLE（可比性）≠ 同一輸出函數（可互比性）。** 即使 paired 通過、即使 ON 快過 OFF，
> ON 的 t/s 也只能標為「MTP-on 輸出函數下的 t/s」，**不得**表述為「OFF 的 X% 加速」。

沒有這一層，09-27 那套 gate 會在下一次 ON 變快時**自動放行一個語意錯誤的結論**。

## 3. 明確不做

- ⛔ 不放寬 M1/M2/M3（它們是護欄不是獎勵）
- ⛔ 不換 cell／prompt 讓 ON「看起來一樣」
- ⛔ 不改交付口徑（`CGC_SERVER_MTP=0` 保持）
- ⛔ 不動 `src/`（P2-b 已評估為負收益：動 recurrent kernel，修成也比 OFF 慢）
- ⛔ 不重啟任何「MTP 加速」預算

## 4. 步驟（全部 0 build）

| 步 | 動作 | 成本 | 備註 |
|---|---|---|---|
| S1 | 本卡開立（✅ 已完成） | 0 | 本檔 |
| S2 | **B3** 明文（✅ 已完成，見 §2 表） | 0 | 交付口徑未動 |
| S3 | **B2** 重標：docs 逐一加「輸出函數」標籤 | 純編輯 | ✅ **已完成**（**20 ＋ 56 ＝ 76 份**，每份 6 行純插入、既有數字未改；第二批 **7 份是未追蹤檔 ⇒ 已加標籤但未 stage**，見 §5） |
| S4 | **B1／B4** 補語義層到 `mtp_promotion_gate.py`（＋ `--selftest` 要過） | 動 `scripts/check/` | ✅ **已完成**（2026-09-28 21:0x）：`--selftest` **18/18 PASS**（既有 12 條未破壞 ＋ B1 新增 6 條），**4 組突變實測皆會紅** ⇒ 語義層非裝飾。⚠ **卡上「`scripts/check/*` 在自動索引範圍」是錯的**：實測 `mtp_promotion_gate.py` 與 `mtp_off_baseline.json` 皆**不在 MANIFEST**（`index_assets.py --check` 報 `on disk but not in the manifest`）⇒ 改它們不會新增漂移，也**不需要重生索引**（重生反而會把別條線 76 筆未提交 WIP 寫進 MANIFEST） |
| S5 | 收尾資產：更新 `MEMORY.md`／日誌，視需要開一份 `docs/MTP_CALIBER_*_RESULT_*.md` | 純編輯 | — |

## 5. B2 重標清單（✅ 已完成）

> ⚠ **判據修訂（2026-09-28 16:4x）**：原寫「`grep -l "CGC_SERVER_MTP=1" docs/*.md` ⇒ 8 份」是**錯的**。
> 那個 grep 只撈到 **20 份**，而且其中多數只是把 flag 當 arm 定義寫，**沒有 ON／OFF 互比**。
> 真正的判據應該是「**同一行同時出現 MTP-on 與 `t/s`／`ms/tok`**」：
> `grep -rnE "MTP[- ]on" docs/*.md | grep -E "t/s|ms/tok"` ⇒ **23 份**（扣掉本輪已自帶標籤的
> `MTP_BITIDENT_CHARTER`/`CLOSEOUT`/`MTP_CALIBER_REDEFINE` ⇒ **實標 20 份**）。
> ⚠ 反向也別用「檔內同時出現 MTP-on 與 t/s」當判據 ⇒ 那會給 **102 份**（幾乎全是假陽性）。

**已加標籤的 20 份**（每份標題下插入 6 行，既有數字一行未改）：

```
docs/ACCEPT_LEVERS_AND_DIRTY_BOX_PAIR_2026-09-26.md   ← 未追蹤檔（無 git 安全網，已加）
docs/BIGGER_FISH_2026-09-22.md                        ←「MTP off 9.82 vs on 12.62 ＝ +28.5%」這類最危險
docs/DECODE_STEP_BUDGET_2026-09-19.md
docs/DECODE_STEP_ROW_POPULATIONS_2026-09-20.md        ←「MTP on 1.48×」
docs/GAP_FIX_EXEC_2026-09-24.md                       ←「交付 cell（MTP on）12.57 t/s」
docs/M1_WORKITEM4_CANON_ORDER_STATUS_2026-09-17.md    ← 門檻寫成「MTP-on ≥ MTP-off」
docs/M3_M4_STATUS_2026-09-17.md                       ←「MTP-on ≥ MTP-off PASS」
docs/M4_VERIFY_CELL_2026-09-18.md
docs/MEASUREMENT_CONTRACT_2026-09-25.md
docs/MILESTONE_MAP_RECHECK_2026-09-25.md
docs/MTP_2X_BOUNDARY_2026-09-19.md
docs/MTP_ABBA_RECHECK_2026-09-18.md
docs/MTP_INSTRUMENT_PLAN_2026-09-17.md
docs/NEXT_ACTIONS_2026-09-24.md
docs/NEXT_MILESTONES_2026-09-27.md                    ← 未追蹤檔（無 git 安全網，已加）
docs/PARALLEL_AND_WORKERS_AB_2026-09-21.md
docs/PROD_NEW_TEST_CARD_2026-09-24.md                 ← ⚠ 同時有別條線 73 行未提交改動 ⇒ 只 stage 自己那段
docs/ROUTING_TRACE_2026-09-17.md
docs/S0_MTP_ONOFF_AB_2026-09-21.md
docs/VOID_NUMBER_CITATIONS_2026-09-25.md
```

重標規則：**只加標籤、不改既有數字**（dated 產物不回改），在該檔「引用 ON 數字處」加一句
「輸出函數 = MTP-on，不可與 OFF 互比」。

### 5.1 第二批（2026-09-28 20:5x）：`12.57`／`12.62` 那一族，**56 份**

> ⚠ **第一批的判據有漏網，而且漏在最關鍵的一族**：12.57 那一族寫的是
> `--spec-type draft-mtp`（或只寫數字），**不是字面 `MTP-on`** ⇒ 第一批的 grep 整族沒撈到。
> `grep -rl "12\.57" docs/*.md` = **94 份**；`12.57`＋`12.62` 合計引用 **106 份**。

第二批判據（**同一行**同時命中數字與「基線語」）：

```
引用 12.57 或 12.62            : 106 份
  其中未帶第一批標籤           :  93 份
  同一行把該數字當「基線／錨點／交付／對照／目標／要打敗」寫 :  58 份
  扣除兩份卡自身（BITIDENT_CHARTER／本卡）                  :  56 份  ← 實標
```

⚠ 寬判據（檔內任意處有基線語）會給 **86 份**，窄判據（同一行）給 58 ⇒ **用窄判據**，
因為標籤的目的是防「把 ON 數字當可比基線讀」，只有同一行才構成那個誤讀。

**56 份全數純插入 6 行、`del=0`、既有數字一行未改**（逐檔 `git diff` 驗過）。
第二批中 **7 份是未追蹤檔**（`ANCHOR_12_57_REPRODUCIBILITY_2026-09-28.md`、
`CANDIDATE_SPECS_2026-09-27.md`、`K3_PAIR_CERT_V2_EXECUTABILITY_2026-09-28.md`、
`K3_PAIR_CERT_VERDICT_2026-09-28.md`、`MTP_ON_OPTIMIZATION_PLAN_2026-09-25.md`、
`MTP_UNARMED_VOID_2026-09-26.md`、`S2_OVERLAP_EXPERIMENT_2026-09-28.md`）
⇒ **已加標籤，但未 stage**（屬別條線未提交檔案，不併入本線 commit）。

最該標的四份（12.57 的源頭與傳播鏈）：
`PRODUCTION_PROFILE_2026-09-20.md`（12.57 出處，明寫「這是另一個 agent 要打敗的數字」）、
`BITIDENTITY_AND_0907_KNOWHOW_2026-09-22.md`（明寫「12.57 那個 cell 本來就開著 MTP」）、
`ACCEPTMOE_ADAPTATION_2026-09-24.md`、`ABBA_MEASUREMENT_PROTOCOL_2026-09-23.md`。

## 6. 完成後的引用規則（一句話）

> **MTP on 與 off 是兩個輸出函數。** 引用任一方時必須標輸出函數；
> 交付口徑 = **MTP off**；在 ON 於乾淨窗口 paired 快過 OFF 之前，不投任何「MTP 加速」資源。

---

## 7. B1 落地實錄（2026-09-28 21:0x）

### 7.1 補的是哪個洞

既有 gate 的 8 條規則判的是**盒況可比性**。缺的這一層是：**即使全部通過，ON 與 OFF
算的不是同一個函數**。實證如下 —— 構造一個「配對全綠且 ON 快 **+21.4%**」的候選
（`Backup/b1_gate_demo/cand_on_faster.json`：off 11.61 vs on 14.09，null 底噪 0.18%，
兩側 spread 0.26%／0.43%）：

```
verdict: ELIGIBLE
  output function: candidate=MTP-on baseline=MTP-off identical=False
  NOT INTERPRETABLE AS A SPEEDUP: on t/s only as 'MTP-on output function'; never as a % of off
    root cause: cparams.n_rs_seq 0 -> draft.n_max > 0 (delta-net-base.cpp:494)
  note: paired ratio: on/off = 1.214x (14.10 vs 11.61 t/s)
```

⇒ 既有 gate **仍給 `ELIGIBLE`、exit 0**（正確：配對確實可比），但**原先會被讀成
「MTP 加速 21.4%」** —— 那正是本立項要擋的語義錯誤。現在這一層由機器印出來。

### 7.2 判據（機檢欄位）

| 欄位 | 意義 |
|---|---|
| `output_function.identical` | 兩側是否同一輸出函數（`spec` 歸一化；candidate 可用 `output_function` 顯式宣告，優先於 `spec`） |
| `comparability.paired_comparable` | 既有 8 條（盒況可比性） |
| `comparability.interpretable_speedup` | **`paired_comparable` AND `identical`** ⇒ 只有兩者都成立才可寫「X% of off」 |

未知 spec 採 **fail-closed**：回傳原字串 ⇒ 永遠不等於 `MTP-off` ⇒ 判為不同函數。

### 7.3 自測會紅（4 組突變實測）

`--selftest` **18/18 PASS**（既有 12 條未破壞 ＋ B1 新增 6 條）。突變實測：

| 突變 | 結果 |
|---|---|
| M1 `ON_SPEC_MARKERS` 清空（映射失效） | ✅ 會紅 |
| M2 `interpretable_speedup` 恆 True（語義層失效） | ✅ 會紅 |
| M3 `declared` 不優先 | ✅ 會紅 |
| M4 fallback 改 fail-open（未知 spec 當 off） | ✅ 會紅 |

⚠ **M1 第一次沒紅** —— 因為 fallback 本身也 fail-closed，清空 marker 表是**等價突變**，
只斷言布林值沒有區分力。已改為**同時斷言歸一化後的字面標籤**（`MTP-on`／`MTP-off`／原字串）
才拿到區分力。這條值得記：**斷言要能區分「對」與「碰巧對」。**

### 7.4 baseline 側（schema → v4）

`scripts/check/mtp_off_baseline.json` 只動 4 個鍵（已逐鍵比對 v3 備份）：

- 新增 `output_function` 節（7 個鍵：根因／為何無旋鈕可還原／品質說明／claim 規則／誰強制）
- `note` 追加 v4 一句
- `schema` `mtp-off-baseline/3` → `/4`
- `reproduce` 修掉過期後門 `--charter none` → `--charter waive --waive-reason ...`

⛔ **量測值一個未動**：`measured.tg_t_s` 仍 **11.61**、`pp_t_s` 275.74、`frozen` 未改。

### 7.5 未做／待辦

- **索引未重生**（`index_assets.py`）—— 76 筆漂移**全部來自別條線未提交的
  `agent_harness/`／`scripts/check/`**；本輪改的兩檔本身不在 MANIFEST，重生只會把別人的
  WIP 凍進去。
- B2 第二批那 **7 份未追蹤檔**已加標籤但仍未 stage（同原則：不併入本線 commit）。
