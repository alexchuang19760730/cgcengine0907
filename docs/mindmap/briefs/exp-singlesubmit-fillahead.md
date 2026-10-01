# 在單次提交臂上，**把每一步的 union 不經 hook 交給預取… — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：在單次提交臂上，**把每一步的 union 不經 hook 交給預取器**（`cache_step_union` 改由 mask 回讀餵；`CGC_PREFETCH_SRC=hist` ＋ `CGC_PREFETCH_WINDOW=4` 開起來）， 提交時「被選中但未駐留」的比例會不會從 **42.9% 降到 ~3% 這個量級**？ 也就是：**單次提交臂的 39pp 缺口，是不是只由「餵料被 hook 綁住」造成的？**

- 主題：實驗　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

在單次提交臂上，**把每一步的 union 不經 hook 交給預取器**（`cache_step_union` 改由 mask 回讀餵；`CGC_PREFETCH_SRC=hist` ＋ `CGC_PREFETCH_WINDOW=4` 開起來）， 提交時「被選中但未駐留」的比例會不會從 **42.9% 降到 ~3% 這個量級**？ 也就是：**單次提交臂的 39pp 缺口，是不是只由「餵料被 hook 綁住」造成的？**

## 2. 判準

① **量具活著**：`prefetch=` 不再是 `0/0`（這是「治療真的執行了」的唯一閘；本 repo 已兩次 把「旗標在 env 裡」誤讀成「機制跑了」——`docs/F2_F5_OVERLAP_AND_AUDIT_RESULT_2026-09-20.md:33`）。 ② **缺口真的縮**：`CGC-MISSMASK-STEP` 的 `misses/nsel` 中位數從 42.9% 落到 **≤10%** （依 (c) 預期 3–8%），且分布（四分位）一起報。 ③ **t/s 一律標 VOID**：本卡**不**驗收速率；輸出仍是 garbage（後半沒做）， 任何 t/s 只能當作「上限探針」並明文標 VOID。

## 3. 結果

decode 27.26 t/s

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 假設：(a) **餵料被 hook 綁住**（可證）：`cache_step_union[il] = uni` 在 hook 內（`:7866`）， 而 `CGC_SEG_BATCH=1` 跳過 hook ⇒ 預取器的三個來源（step/prev/hist）都拿到空集合。 證據：**兩臂的 `prefetch=0/0` 完全相同**（原始碼註解明說 `src=step` 時 「the hook just ensured (all resident) -> prefetch_slot drops everything」， 但單次提交臂是連 hook 都沒跑）。 (b) **補齊的資訊本來就在 host**：`CGC_MISS_MASK_DBG=1` 的回讀迴圈 （`:4011-4042`）每步把 `cache_ids_cont_tensors[il]`（該層被選中的 id）讀回 host 只為了 數 miss；同一份 `ibuf` 直接寫進 `cache_step_union[il]` 即可。 (c) **需求側的上界已知**：一步級可預測性 96.0–96.8% ⇒ 若預取器真的動起來、 且覆蓋到「上一步的 union」，則提交時未駐留應落到 **3–6%**（compulsory 那一側）。

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-5`　餵料搬家（P1）
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **被測 option**：`CGC_SPAC_DBG=1`；`CGC_PREFETCH_SRC=hist`；`CGC_PREFETCH_WINDOW=4`
- **結案狀態**：未結案（P0 成立、P1 未做）
- **逐條處置**：整合進子目標　→ `L20-5`　—　單次提交臂上把 union 交給預取；P1 餵料的載體
- **測試 log（實跑）**：[fed_default.json](../../../Backup/fillahead_2026-09-28/fed_default.json)　[exp-singlesubmit-fillahead_20260929_012703.json](../../../Backup/exp_runs/exp-singlesubmit-fillahead_20260929_012703.json)　[filla_run.json](../../../Backup/fillahead_delivery_2026-09-29/filla_run.json)　[certified.json](../../../Backup/p1_rbfeed_2026-09-30/certified.json)
- 驗收：不開 debug 仍要有 CGC-RB-FEED 且 prefetch > 0/0。

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

**Run 1** · 2026-09-29 00:52　·　thermal HEAVY　·　swap 376 MiB→3890 MiB
- arm：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_SPAC_DBG=1`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[fed_default.json](../../../Backup/fillahead_2026-09-28/fed_default.json)
- 結果：pp=344.25　tg=25.64
- 判定：both：thermal=HEAVY, swap_growth=3513.94 MiB

**Run 2** · 2026-09-29 01:27　·　thermal HEAVY　·　swap 4986 MiB→5927 MiB
- arm：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-singlesubmit-fillahead_20260929_012703.json](../../../Backup/exp_runs/exp-singlesubmit-fillahead_20260929_012703.json)
- 結果：tg=11.5
- 判定：both：thermal=HEAVY, swap_growth=941.5699999999997 MiB

**Run 3** · 2026-09-29 22:08　·　thermal NOMINAL　·　swap 10831 MiB→12202 MiB
- arm：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_SPAC_DBG=1`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[filla_run.json](../../../Backup/fillahead_delivery_2026-09-29/filla_run.json)
- 結果：tg=26.45
- 判定：swap：swap_growth=1379.3100000000013 MiB, max_swap=12239.69 MiB

**Run 4** · 2026-09-30 14:40　·　thermal NOMINAL　·　swap 5080 MiB→5146 MiB
- arm：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[certified.json](../../../Backup/p1_rbfeed_2026-09-30/certified.json)
- 結果：tg=27.26
- 判定：none：swap_growth=25.25 MiB, thermal=NOMINAL

## 8. 子目標分解（持續更新；2/8 完成）

- [x] 跑前立項（現狀/目標/假設/驗收） [證](../../../scripts/check/charters/exp-singlesubmit-fillahead.yaml)
- [x] 用生產級腳本 prod-new ＋ 自己 option 跑實驗臂，留存 log
- [ ] 驗收：① **量具活著**：`prefetch=` 不再是 `0/0`（這是「治療真的執行了」的唯一閘；本 repo 已兩次 把「旗標在 env 裡」誤讀成「機制跑了」——`docs/F2_F5_OVERLAP_AND_AUDIT_RESULT_2026-09-20.md:33`）。 ② **缺口真的縮**：`CGC-MISSMASK-STEP` 的 `misses/nsel` 中位數從 42.9% 落到 **≤10%** （依 (c) 預期 3–8%），且分布（四分位）一起報。 ③ **t/s 一律標 VOID**：本卡**不**驗收速率；輸出仍是 garbage（後半沒做）， 任何 t/s 只能當作「上限探針」並明文標 VOID。
- [ ] 否證條件：① `prefetch` 仍是 `0/0`，或補完餵料後仍未駐留沒有下降 ⇒ **餵料不是瓶頸**， 那 39pp 另有來源（優先查 `prefetch_slot` 的丟棄計數與 LRU 受害者選擇）。 ② 未駐留下降但**輸出逐位惡化**（比對 `answer_md5` 或 native adapter 的參考集）⇒ 補齊改變了正確性語意，必須先解釋再往下走。
- [ ] 把每步的 union 從 mask 回讀餵進 `cache_step_union`（不經 hook）
- [ ] 提交時未駐留比例的下降量
- [ ] 確認補齊沒有把正確性語意改壞
- [ ] 把剩下的 3–8% 的價格寫出來（後半 G4 的規模）

## 9. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `Backup/g4miss_2026-09-28/ctl_segmented_default.json` |
| 備註 | 假設：(a) **餵料被 hook 綁住**（可證）：`cache_step_union[il] = uni` 在 hook 內（`:7866`）， 而 `CGC_SEG_BATCH=1` 跳過 hook ⇒ 預取器的三個來源（step/prev/hist）都拿到空集合。 證據：**兩臂的 `prefetch=0/0` 完全相同**（原始碼註解明說 `src=step` 時 「the hook just ensured (all resident) -> prefetch_slot drops everything」， 但單次提交臂是連 hook 都沒跑）。 (b) **補齊的資訊本來就在 host**：`CGC_MISS_MASK_DBG=1` 的回讀迴圈 （`:4011-4042`）每步把 `cache_ids_cont_tensors[il]`（該層被選中的 id）讀回 host 只為了 數 miss；同一份 `ibuf` 直接寫進 `cache_step_union[il]` 即可。 (c) **需求側的上界已知**：一步級可預測性 96.0–96.8% ⇒ 若預取器真的動起來、 且覆蓋到「上一步的 union」，則提交時未駐留應落到 **3–6%**（compulsory 那一側）。 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 0 份 |

- （無）

---

← [在**認可入口**（`harness bench`、`prod-ne…](exp-prodnew-decode20-g4miss.md)　·　[總目錄](index.md)　·　[HTML 版](exp-singlesubmit-fillahead.html)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
