# 在**認可入口**（`harness bench`、`prod-ne… — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：在**認可入口**（`harness bench`、`prod-new`、`delivery` cell、MTP off）上跑 **single-submit 臂**（`CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`）並開 miss-mask 插樁：每一步「被選中但不在池」的 (layer, expert) 有多少？ 換句話說，**G4 的每步重算工作清單有多大比例**？ 判定的門檻（跑前定好，見 acceptance）： * miss 比例（misses / nsel）**≤ 25%** ⇒ 正確版 20+ 有路：預測+重算只付 1/4 的層， 而 28 t/s 的臂 vs 9.3 t/s 的臂差 3×，付掉一部分仍有 20+ 的空間。 * **≥ 60%** ⇒ 該機制在交付上沒有 headroom，力氣轉 shape/IO。

- 主題：實驗　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

在**認可入口**（`harness bench`、`prod-new`、`delivery` cell、MTP off）上跑 **single-submit 臂**（`CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`）並開 miss-mask 插樁：每一步「被選中但不在池」的 (layer, expert) 有多少？ 換句話說，**G4 的每步重算工作清單有多大比例**？ 判定的門檻（跑前定好，見 acceptance）： * miss 比例（misses / nsel）**≤ 25%** ⇒ 正確版 20+ 有路：預測+重算只付 1/4 的層， 而 28 t/s 的臂 vs 9.3 t/s 的臂差 3×，付掉一部分仍有 20+ 的空間。 * **≥ 60%** ⇒ 該機制在交付上沒有 headroom，力氣轉 shape/IO。

## 2. 判準

一次 `harness bench`、`delivery` cell、`prod-new`，臂為 `{CGC_SEG_BATCH=1, CGC_B_SCHEME=1, CGC_SLOT_TABLE_GPU=1, CGC_MISS_MASK=1, CGC_MISS_MASK_DBG=1, CGC_MISS_MASK_COST=1}`，且**同時**滿足： ① 插樁活著：stderr 出現 **≥10 行** `CGC-MISSMASK-STEP`，且 `nsel` > 0 （= 有量到被選中的專家；只有 `MISSMASK ... misses=nsel` 全層系列不算， 那正是 09-26 文件記的「publisher 沒到位」指紋）； ② 報**分布**：per-step 的 `misses/nsel` 給中位數與四分位，**不得**只報一個純量； ③ 分開記：本臂的 t/s 一律標 VOID（wrong-output 臂），只把 `CGC-MISSMASK-COST` 的 `sync_usec/total_usec` 當**代價**報，不當速率報。

## 3. 結果

**閘已過**（09-29 權威 cell）：重算工作清單中位 **6.09%**（19.0/step、IQR 3.85–8.65）、`mm_pub_n_leaf=39/39`、`instrument_gate=BOUND`，閘檻是 **≤25%** ⇒ 通過。**仍不主張吞吐**（單段提交架構如此：錯輸出臂）。（引用者注意：`Backup/mtp_off_clean/res.json` 是這個節點的**舊**產物、`attribution=swap`；權威讀數在 §34。） **§50**：越線的三格裡，**只有本節點的機制（`SEG_BATCH+B_SCHEME+SLOT_TABLE_GPU`＋餵料）那條路是池活著**的（`fillahead` A 半：39.0 ms／25.64 t/s、miss **6.1%**、`file_reads>0`）；它缺的是**重算**那 6.1%，價格 **1.60–3.20 ms**（§35）⇒ `39.0 ＋ x ＝ 40.6–42.2 ms ＝ 23.7–24.6 t/s` ⇒ **25 的 margin 0.6–2.2 ms**。 **§51（09-29）**：本節點量到的 6.09% 是**逐步總量**（19/312），**不是逐層**。而「每層 miss 的上界」正是決定 B（重算補丁）可行性的唯一空缺：靜態寬度 k 由**正確性**決定（一層可能 8 個全缺）⇒ 預設 k=8 ⇒ 備援重算 **8.09 ms/步**（實測）⇒ B 淨回收為負。**建議本節點的既有一行加印逐層直方圖**（`CGC-MISSMASK-STEP ... misses=[l1..l39]`）：一行儀器、一趟既有臂，直接決定 k=1／2／8。

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 假設：三件事在同一個機制上（single-submit 的必然後果），逐條寫死： (a) **錯誤的來源不是 bug，是結構**：正常路徑在每層的 hook 裡做 `ensure_batch` （拉缺的專家進池），而 `CGC_SEG_BATCH=1` 一次提交所有層 ⇒ 提交時池是「上一次 提交之後的狀態」⇒ 本步被選中但不在池的專家沒有機會被拉進來。 (b) **G3 把錯誤有界化**：那些位置從「映射到另一個真專家的權重」（無界錯誤）變成 寫進保留零槽 ⇒ 貢獻恰為 0（`llama-context.cpp:3765-3772`）。所以臂能跑完 128 token 而不是死在 ~8 token，但輸出仍是錯的。 (c) **G4 的工作清單 = miss 集合**：`CGC-MISSMASK-STEP misses= layers=` 報的 正是「有幾個被選中的 (layer, expert) 在提交時不在池」。 預期比例（兩個方向都寫死）：池在 decode 逐步之間幾乎不動（09-28 實測 consumed churn publish 級 19.8-20.2%），且 prod-new 的池命中率 ~96%（anchor 卡的 baseline）⇒ 被選中卻不在池的**暫時** miss 應該遠小於全部選中數。

---

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_COST=1`　（profile `prod-new`；被測 option：`CGC_SEG_BATCH=1`；`CGC_B_SCHEME=1`；`CGC_SLOT_TABLE_GPU=1`；`CGC_MISS_MASK=1`；`CGC_MISS_MASK_COST=1`）
- **來源**：① 實跑 arm（有 log）　·　置信度 `high`
- **儀器開關**（不是被測 option）：`CGC_MISS_MASK_DBG=1`
- **測試 log**：[g4miss_delivery.json](../../../Backup/g4miss_2026-09-28/g4miss_delivery.json)
- 節點自帶實跑 arm（15 筆 run，log 可點）

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [CGC_EB_NOFILL 診斷臂（fill 成本）](io-nofill.md) | 3a | fill 3.955 → 0.206 ms/step（−95%）⇒ 機制證；生產口徑同步 fill 僅占 step 4.7% |
| [單段提交（41 段 → 1 段）](s1-segbatch.md) | 3a | 判詞：41 段→1 段的 A/B **作廢**（兩端都不可引用）。本節點不主張吞吐。**補（09-29）**：它的**可交付上界**＝序列化 − 必須還回去的 fill ⇒ **1 |
| [異步 gather 流水線（單段＋miss 後台補＋局部重算）](s1-asyncgather.md) | 3a | E0 已給出決定性答案：前提成立——S1（暖池 8 GiB）輸出 文摘文摘… 且 logits 全非有限（引擎自蓋 INVALID），同一輪的分段臂是 42 ⇒ 異步 fill＋補 |
| [ρ 路線（按層批次化 prefetch）](cache-rho.md) | 3a | 判活，但本節點**不主張吞吐**：覆蓋 0.849、視窗 1.30~1.59 ms、每步付 4.76 ms GPU 插入 ⇒ 有前置條件。 |
| [prebind／方案 A（預指派 slot）](cache-prebind.md) | 3a | 判活，但本節點**不主張吞吐**：qu2/qu3 全過、qu1 邊緣（預指派 slot，提前一整步發起 ⇒ 視窗 ≈ 一步）。 |
| [G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 …](exp-s-retro.md) | 3a | 判詞：11.97 那一族**作廢**（該臂 9 個 run 全部非乾淨）。本節點不主張吞吐。 |

## 7. 運行設置與 Log（生產級腳本 prod-new ＋ 自己 option）

**Run 1** · 2026-09-29 00:01　·　thermal NOMINAL　·　swap 5776 MiB→8002 MiB
- arm：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[g4miss_delivery.json](../../../Backup/g4miss_2026-09-28/g4miss_delivery.json)
- 結果：tg=27.17
- 判定：swap：swap_growth=2225.38 MiB, max_swap=8121.5 MiB

**Run 2** · 2026-09-29 00:01　·　thermal HEAVY　·　swap 6479 MiB→8013 MiB
- arm：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[g4miss2_default.json](../../../Backup/g4miss_2026-09-28/g4miss2_default.json)
- 結果：pp=369.45　tg=24.88
- 判定：both：thermal=HEAVY, swap_growth=1541.3099999999995 MiB

**Run 3** · 2026-09-29 00:05　·　thermal HEAVY　·　swap 6084 MiB→8330 MiB
- arm：`prod-new:CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-prodnew-decode20-g4miss_20260929_000557.json](../../../Backup/exp_runs/exp-prodnew-decode20-g4miss_20260929_000557.json)
- 結果：pp=201.67　tg=6.9
- 判定：both：thermal=HEAVY, swap_growth=2261.5600000000004 MiB

**Run 4** · 2026-09-29 00:07　·　thermal HEAVY　·　swap 6084 MiB→8330 MiB
- arm：`prod-new:CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[ctl_segmented_default.json](../../../Backup/g4miss_2026-09-28/ctl_segmented_default.json)
- 結果：pp=201.67　tg=6.9
- 判定：both：thermal=HEAVY, swap_growth=2261.5600000000004 MiB

**Run 5** · 2026-09-29 00:15　·　thermal HEAVY　·　swap 6021 MiB→8320 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=8;!CGC_SOFT_POOL_L1=8`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-prodnew-decode20-g4miss_20260929_001516.json](../../../Backup/exp_runs/exp-prodnew-decode20-g4miss_20260929_001516.json)
- 結果：pp=167.58　tg=5.86
- 判定：both：thermal=HEAVY, swap_growth=2338.999999999999 MiB

**Run 6** · 2026-09-29 00:20　·　thermal HEAVY　·　swap 6258 MiB→8611 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=32;!CGC_SOFT_POOL_L1=32`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-prodnew-decode20-g4miss_20260929_002005.json](../../../Backup/exp_runs/exp-prodnew-decode20-g4miss_20260929_002005.json)
- 結果：pp=159.8　tg=6.34
- 判定：both：thermal=HEAVY, swap_growth=2369.000000000001 MiB

**Run 7** · 2026-09-29 00:24　·　thermal HEAVY　·　swap 7108 MiB→8607 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=4;!CGC_SOFT_POOL_L1=4`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-prodnew-decode20-g4miss_20260929_002452.json](../../../Backup/exp_runs/exp-prodnew-decode20-g4miss_20260929_002452.json)
- 結果：pp=140.62　tg=8.37
- 判定：both：thermal=HEAVY, swap_growth=1515.0699999999988 MiB

**Run 8** · 2026-09-29 00:25　·　thermal HEAVY　·　swap 7108 MiB→8607 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=4;!CGC_SOFT_POOL_L1=4`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[recency_usable8.json](../../../Backup/g4miss_2026-09-28/recency_usable8.json)
- 結果：pp=140.62　tg=8.37
- 判定：both：thermal=HEAVY, swap_growth=1515.0699999999988 MiB

**Run 9** · 2026-09-29 00:25　·　thermal HEAVY　·　swap 6021 MiB→8320 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=8;!CGC_SOFT_POOL_L1=8`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[recency_usable16.json](../../../Backup/g4miss_2026-09-28/recency_usable16.json)
- 結果：pp=167.58　tg=5.86
- 判定：both：thermal=HEAVY, swap_growth=2338.999999999999 MiB

**Run 10** · 2026-09-29 00:25　·　thermal HEAVY　·　swap 6258 MiB→8611 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=32;!CGC_SOFT_POOL_L1=32`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[recency_usable64.json](../../../Backup/g4miss_2026-09-28/recency_usable64.json)
- 結果：pp=159.8　tg=6.34
- 判定：both：thermal=HEAVY, swap_growth=2369.000000000001 MiB

**Run 11** · 2026-09-29 00:30　·　thermal HEAVY　·　swap 6740 MiB→9095 MiB
- arm：`prod-new`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-prodnew-decode20-g4miss_20260929_003011.json](../../../Backup/exp_runs/exp-prodnew-decode20-g4miss_20260929_003011.json)
- 結果：tg=8.33
- 判定：both：thermal=HEAVY, swap_growth=2362.8100000000004 MiB

**Run 12** · 2026-09-29 00:33　·　thermal MODERATE　·　swap 6341 MiB→9278 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=8;!CGC_SOFT_POOL_L1=8`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-prodnew-decode20-g4miss_20260929_003354.json](../../../Backup/exp_runs/exp-prodnew-decode20-g4miss_20260929_003354.json)
- 結果：tg=10.28
- 判定：swap：swap_growth=2977.000000000001 MiB, max_swap=9338.81 MiB

**Run 13** · 2026-09-29 00:36　·　thermal HEAVY　·　swap 6740 MiB→9095 MiB
- arm：`prod-new`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[delivery_ctl_143.json](../../../Backup/g4miss_2026-09-28/delivery_ctl_143.json)
- 結果：tg=8.33
- 判定：both：thermal=HEAVY, swap_growth=2362.8100000000004 MiB

**Run 14** · 2026-09-29 00:36　·　thermal MODERATE　·　swap 6341 MiB→9278 MiB
- arm：`prod-new:!CGC_SOFT_POOL_L0=8;!CGC_SOFT_POOL_L1=8`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[delivery_usable8.json](../../../Backup/g4miss_2026-09-28/delivery_usable8.json)
- 結果：tg=10.28
- 判定：swap：swap_growth=2977.000000000001 MiB, max_swap=9338.81 MiB

**Run 15** · 2026-09-29 17:55　·　thermal NOMINAL　·　swap 8933 MiB→8963 MiB
- arm：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[g4miss_delivery_harness_fix4066.json](../../../Backup/g4miss_2026-09-29/g4miss_delivery_harness_fix4066.json)
- 結果：tg=27.48
- 判定：swap：swap_growth=30.13000000000102 MiB, max_swap=8962.94 MiB

## 8. 子目標分解（持續更新；2/7 完成）

- [x] 跑前立項（現狀/目標/假設/驗收） [證](../../../scripts/check/charters/exp-prodnew-decode20-g4miss.yaml)
- [x] 用生產級腳本 prod-new ＋ 自己 option 跑實驗臂，留存 log
- [ ] 驗收：一次 `harness bench`、`delivery` cell、`prod-new`，臂為 `{CGC_SEG_BATCH=1, CGC_B_SCHEME=1, CGC_SLOT_TABLE_GPU=1, CGC_MISS_MASK=1, CGC_MISS_MASK_DBG=1, CGC_MISS_MASK_COST=1}`，且**同時**滿足： ① 插樁活著：stderr 出現 **≥10 行** `CGC-MISSMASK-STEP`，且 `nsel` > 0 （= 有量到被選中的專家；只有 `MISSMASK ... misses=nsel` 全層系列不算， 那正是 09-26 文件記的「publisher 沒到位」指紋）； ② 報**分布**：per-step 的 `misses/nsel` 給中位數與四分位，**不得**只報一個純量； ③ 分開記：本臂的 t/s 一律標 VOID（wrong-output 臂），只把 `CGC-MISSMASK-COST` 的 `sync_usec/total_usec` 當**代價**報，不當速率報。
- [ ] 否證條件：① 沒有 `CGC-MISSMASK-STEP` 行（插樁沒生效）⇒ 這是「**沒有讀數**」，不是「miss=0」， 照 `G1B_G3_INSTRUMENTS_2026-09-26.md` 的分類查是哪一種，**不得**記成結果； ② 該臂在 delivery cell 上跑不完（llama-bench 中止／no rows）⇒ 記「該機制在本 cell 上 不可量」，並寫出中止位置； ③ 若 `CGC_MISS_MASK_COST` 的 sync_usec 已經吃掉單步預算的多數 ⇒ 那個插樁自己就是 瓶頸，miss 比例仍可用，但**不得**用它推論可達速率。
- [ ] single-submit 臂每步的 G4 工作清單大小（missing selected experts / selected）
- [ ] 那份清單的代價（G1b 插樁）
- [ ] 用 ①的結果裁決「正確版 20+」這條路的 headroom

## 9. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `Backup/mtp_off_clean/res.json` |
| 備註 | 假設：三件事在同一個機制上（single-submit 的必然後果），逐條寫死： (a) **錯誤的來源不是 bug，是結構**：正常路徑在每層的 hook 裡做 `ensure_batch` （拉缺的專家進池），而 `CGC_SEG_BATCH=1` 一次提交所有層 ⇒ 提交時池是「上一次 提交之後的狀態」⇒ 本步被選中但不在池的專家沒有機會被拉進來。 (b) **G3 把錯誤有界化**：那些位置從「映射到另一個真專家的權重」（無界錯誤）變成 寫進保留零槽 ⇒ 貢獻恰為 0（`llama-context.cpp:3765-3772`）。所以臂能跑完 128 token 而不是死在 ~8 token，但輸出仍是錯的。 (c) **G4 的工作清單 = miss 集合**：`CGC-MISSMASK-STEP misses= layers=` 報的 正是「有幾個被選中的 (layer, expert) 在提交時不在池」。 預期比例（兩個方向都寫死）：池在 decode 逐步之間幾乎不動（09-28 實測 consumed churn publish 級 19.8-20.2%），且 prod-new 的池命中率 ~96%（anchor 卡的 baseline）⇒ 被選中卻不在池的**暫時** miss 應該遠小於全部選中數。 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 0 份 |

- （無）

---

← [在**今天的交付口徑**上（`prod-new` profile、`…](exp-churn-delivery-630.md)　·　[總目錄](index.md)　·　[HTML 版](exp-prodnew-decode20-g4miss.html)　·　[在單次提交臂上，**把每一步的 union 不經 hook 交給預取… →](exp-singlesubmit-fillahead.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
