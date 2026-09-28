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
| **B1** | MTP on 的 t/s **不可與 OFF 互比**，跨臂比較一律拒絕並說明理由 | `scripts/check/mtp_promotion_gate.py`（verdict 語義）＋ `scripts/check/mtp_off_baseline.json`（`note`） | ⚠ **部分存在**：現有 gate 做的是「paired 設計 ＋ 3% 重現性 ＋ 凍結 OFF 錨點」，管的是**盒況可比性**；**沒有「輸出函數不同」這一層** ⇒ 補一層語義 |
| **B2** | 引用 ON 數字必須附「輸出函數 = MTP-on」標籤；既有把 ON 當 OFF 加速版的結論全部重標 | `docs/*.md`（8 份提到 `CGC_SERVER_MTP=1`） | ❌ 未做 |
| **B3** | 交付口徑**維持 MTP off**（`prod-new` 的 `CGC_SERVER_MTP=0` 不動） | `scripts/check/harness.py` profile 定義 | ✅ **已是現狀**，只需明文寫進本卡與收尾文（本卡即為該明文） |
| **B4** | 若要重新追求「ON 當加速用」，**先決條件**＝ON 在乾淨窗口上 paired 快過 OFF（目前方向**相反**：OFF 中位 11.92 vs ON 7.34） | `scripts/check/mtp_promotion_gate.py` ＋ `mtp_off_baseline.json` | ✅ **已有機制**（v2/v3，09-27）⇒ 只需追加「**ELIGIBLE ≠ 同一輸出函數**」一條 |

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
| S3 | **B2** 重標：8 份 docs 逐一加「輸出函數」標籤 | 純編輯 | 清單見 §5 |
| S4 | **B1／B4** 補語義層到 `mtp_promotion_gate.py`（＋ `--selftest` 要過） | 動 `scripts/check/` | ⚠ `scripts/check/*` 在自動索引範圍 ⇒ **commit 前要重生索引**（`build_memory_index.py` → `index_assets.py`，順序不可顛） |
| S5 | 收尾資產：更新 `MEMORY.md`／日誌，視需要開一份 `docs/MTP_CALIBER_*_RESULT_*.md` | 純編輯 | — |

## 5. B2 待重標清單（`grep -l "CGC_SERVER_MTP=1" docs/*.md`）

```
docs/ACCEPT_LEVERS_AND_DIRTY_BOX_PAIR_2026-09-26.md
docs/CANDIDATE_SPECS_2026-09-27.md
docs/DECODE_STEP_BUDGET_2026-09-19.md
docs/DECODE_SYNC_BATCH_2026-09-19.md
docs/M6_QUANT_GEOMETRY_PLAN_2026-09-17.md
docs/MTP_ON_LIVENESS_AND_BENCH_SCHED_2026-09-27.md
docs/MTP_ON_RC6_ROOT_CAUSE_2026-09-28.md
docs/MTP_BITIDENT_*（本輪 4 份，已自帶標籤）
```

重標規則：**只加標籤、不改既有數字**（dated 產物不回改），在該檔「引用 ON 數字處」加一句
「輸出函數 = MTP-on，不可與 OFF 互比」。

## 6. 完成後的引用規則（一句話）

> **MTP on 與 off 是兩個輸出函數。** 引用任一方時必須標輸出函數；
> 交付口徑 = **MTP off**；在 ON 於乾淨窗口 paired 快過 OFF 之前，不投任何「MTP 加速」資源。
