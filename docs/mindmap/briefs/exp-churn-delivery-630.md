# 在**今天的交付口徑**上（`prod-new` profile、`… — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：在**今天的交付口徑**上（`prod-new` profile、`harness bench`、**MTP off**、`delivery` cell、 **`ntok=1` 主 context**），一步 decode 之內／之間，**被消費者實際讀到的 expert→slot 映射** 會不會移動？ 這不是「再量一次 churn」，是**判詞的重新檢驗**：S3（41 段收成 1 段）被判無前提， 依據是 42.3%，而那是 `ntok=4` verify 桶、MTP-on 形狀的讀數。 兩個口徑是**不同的輸出函數**（`MTP_CALIBER_REDEFINE_2026-09-28`），數字不可互相推論。 若主 context 的 `ntok=1` churn 是 **0** ⇒ 表在逐步之間是常數 ⇒ 發布冗餘 ⇒ **S3 復活**， 而 S3 是唯一在認可形狀上越過 20 的實測路徑。 若它不是 0 ⇒ S3 的前提**在今天的口徑上也確實不存在**，22.2 那條路關閉， 力氣轉向 shape／IO（其絕對天花板 20.49／16.31，見下）。

- 主題：實驗　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

在**今天的交付口徑**上（`prod-new` profile、`harness bench`、**MTP off**、`delivery` cell、 **`ntok=1` 主 context**），一步 decode 之內／之間，**被消費者實際讀到的 expert→slot 映射** 會不會移動？ 這不是「再量一次 churn」，是**判詞的重新檢驗**：S3（41 段收成 1 段）被判無前提， 依據是 42.3%，而那是 `ntok=4` verify 桶、MTP-on 形狀的讀數。 兩個口徑是**不同的輸出函數**（`MTP_CALIBER_REDEFINE_2026-09-28`），數字不可互相推論。 若主 context 的 `ntok=1` churn 是 **0** ⇒ 表在逐步之間是常數 ⇒ 發布冗餘 ⇒ **S3 復活**， 而 S3 是唯一在認可形狀上越過 20 的實測路徑。 若它不是 0 ⇒ S3 的前提**在今天的口徑上也確實不存在**，22.2 那條路關閉， 力氣轉向 shape／IO（其絕對天花板 20.49／16.31，見下）。

## 2. 判準

一次 `harness bench`、`delivery` cell、`prod-new` ＋ `{CGC_S1_TABLE_CHURN=1, CGC_SLOT_TABLE_GPU=1, CGC_GPU_TIMING=1, CGC_DECODE_PROFILE=1}`，熱啟動暖機、量測期 thermal NOMINAL， 且**同時**滿足： ① **量具活著**：teardown 印出 per-width 那一行，且 `python3 scripts/check/premise_b_read.py -v ` 報 **`delivery_ntok1_measured = True`**（即 `ntok=1` 被歸屬到主 context，不是 draft 的 0/612）。 這個欄位是 2026-09-28 加的：它把「量具活著」從一句話變成一個布林， 因為 `ntok=1` 的 draft 與主 context **在計數器上長得一樣**（`_by_ntok` 無 `is_draft` 守衛）； ② 報**分布**：churn 報 per-publish 的 changed/total ＋ 逐 (step, layer) 的散布， **不得**只報一個純量（登記明文禁止把 42.3% 當常數，同理由）； ③ 品質閘同時綠：`clamped_selected=0`、`zero_mapped_selected=0`、`n_fast_cold` 不上升。

## 3. 結果

主 context ntok=1 的 churn 非 0（publish 級 3026/14976、entry 級 4014/119808，兩筆獨立 run）——本實驗不報吞吐：tg 為 swap-void，不得引用

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 假設：計數點是**宿主側的發布路徑**，不是讀回路徑： `prev` = 該 (context, layer) 上一次發布的 table 快照； `now = (const int32_t *) table->data`； 比較對象是 `ids[j]`（hook 自己發布的 top-k），逐 publish 累計 （`llama-context.cpp:5655-5686`，桶為 `*_by_ntok[n_tokens]`）。 ⇒ 它與 09-27 的 leaf 讀回修復無關（那修的是區域指標 `rm`/`tb` 被釘 `nullptr`， 整塊在 `CGC_S1_DBG` 之下，檔自陳對預設臂零行為影響）。 ⇒ 所以 42.3% **沒有被汙染**，它只是**另一個口徑、另一個 build、單一 run**。 本卡的機制預測（兩個方向都寫死，**跑前**）： (a) MTP off ⇒ 每步一個 token ⇒ 主 context 的 `ntok=1` 桶就是交付步； (b) 若池在那一步內是凍結的（`prev == now` 對所有被消費的 id）⇒ churn = 0； (c) 反之若映射逐步在動 ⇒ churn > 0，與 42.3% 同號（因為兩者問的是同一件事， 只是一個在 verify 圖、一個在 decode 圖）。

---

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod-new:CGC_S1_TABLE_CHURN=1;CGC_SLOT_TABLE_GPU=1`　（profile `prod-new`；被測 option：`CGC_S1_TABLE_CHURN=1`；`CGC_SLOT_TABLE_GPU=1`）
- **來源**：① 實跑 arm（有 log）　·　置信度 `high`
- **儀器開關**（不是被測 option）：`CGC_GPU_TIMING=1`；`CGC_DECODE_PROFILE=1`
- **測試 log**：[churn.json](../../../Backup/churn_delivery_630_2026-09-28/churn.json)
- 節點自帶實跑 arm（2 筆 run，log 可點）

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

**Run 1** · 2026-09-28 23:08　·　thermal NOMINAL　·　swap 3909 MiB→7755 MiB
- arm：`prod-new:CGC_S1_TABLE_CHURN=1;CGC_SLOT_TABLE_GPU=1;CGC_GPU_TIMING=1;CGC_DECODE_PROFILE=1`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[churn.json](../../../Backup/churn_delivery_630_2026-09-28/churn.json)
- 結果：tg=9.07
- 判定：swap：swap_growth=3877.69 MiB, max_swap=8074.75 MiB

**Run 2** · 2026-09-28 23:12　·　thermal NOMINAL　·　swap 5924 MiB→7503 MiB
- arm：`prod-new:CGC_S1_TABLE_CHURN=1;CGC_SLOT_TABLE_GPU=1;CGC_GPU_TIMING=1;CGC_DECODE_PROFILE=1`
- 命令：`llama-bench（prod-new）-p 0 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[exp-churn-delivery-630_20260928_231234.json](../../../Backup/exp_runs/exp-churn-delivery-630_20260928_231234.json)
- 結果：tg=9.94
- 判定：swap：swap_growth=1579.0 MiB, max_swap=7543.06 MiB

## 8. 子目標分解（持續更新；2/7 完成）

- [x] 跑前立項（現狀/目標/假設/驗收） [證](../../../scripts/check/charters/exp-churn-delivery-630.yaml)
- [x] 用生產級腳本 prod-new ＋ 自己 option 跑實驗臂，留存 log
- [ ] 驗收：一次 `harness bench`、`delivery` cell、`prod-new` ＋ `{CGC_S1_TABLE_CHURN=1, CGC_SLOT_TABLE_GPU=1, CGC_GPU_TIMING=1, CGC_DECODE_PROFILE=1}`，熱啟動暖機、量測期 thermal NOMINAL， 且**同時**滿足： ① **量具活著**：teardown 印出 per-width 那一行，且 `python3 scripts/check/premise_b_read.py -v ` 報 **`delivery_ntok1_measured = True`**（即 `ntok=1` 被歸屬到主 context，不是 draft 的 0/612）。 這個欄位是 2026-09-28 加的：它把「量具活著」從一句話變成一個布林， 因為 `ntok=1` 的 draft 與主 context **在計數器上長得一樣**（`_by_ntok` 無 `is_draft` 守衛）； ② 報**分布**：churn 報 per-publish 的 changed/total ＋ 逐 (step, layer) 的散布， **不得**只報一個純量（登記明文禁止把 42.3% 當常數，同理由）； ③ 品質閘同時綠：`clamped_selected=0`、`zero_mapped_selected=0`、`n_fast_cold` 不上升。
- [ ] 否證條件：① teardown 印 **`not instrumented`**（旋鈕沒生效）或 `premise_b_read` 報 `delivery_ntok1_measured = False`（只有 draft 桶／桶被標成 mixed）⇒ 這是「**沒有讀數**」，不是「churn=0」。⇒ 先照 §5.1 的分類查是哪一種 （`capture is not a node`／`not int32s`／`gather=null`），**不得**記成結果。 ② 若 `delivery` cell 的步根本不進那條 gate（`cgc_is_decode_graph` 未涵蓋）⇒ 口徑不成立，改記為「主 context 的 churn 在本 cell 上不可量」，並說明為什麼。
- [ ] 在 build 630 上第一次證明這個量具活著（全 repo 220 個帶該旋鈕的產物全部落在 09-16/09-17）
- [ ] 主 context `ntok=1` 的 churn（today's delivery shape）
- [ ] 用 ②的結果裁決 S3 的前提在今日口徑上成不成立

## 9. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `agent_harness/portal/targets.json` |
| 備註 | 假設：計數點是**宿主側的發布路徑**，不是讀回路徑： `prev` = 該 (context, layer) 上一次發布的 table 快照； `now = (const int32_t *) table->data`； 比較對象是 `ids[j]`（hook 自己發布的 top-k），逐 publish 累計 （`llama-context.cpp:5655-5686`，桶為 `*_by_ntok[n_tokens]`）。 ⇒ 它與 09-27 的 leaf 讀回修復無關（那修的是區域指標 `rm`/`tb` 被釘 `nullptr`， 整塊在 `CGC_S1_DBG` 之下，檔自陳對預設臂零行為影響）。 ⇒ 所以 42.3% **沒有被汙染**，它只是**另一個口徑、另一個 build、單一 run**。 本卡的機制預測（兩個方向都寫死，**跑前**）： (a) MTP off ⇒ 每步一個 token ⇒ 主 context 的 `ntok=1` 桶就是交付步； (b) 若池在那一步內是凍結的（`prev == now` 對所有被消費的 id）⇒ churn = 0； (c) 反之若映射逐步在動 ⇒ churn > 0，與 42.3% 同號（因為兩者問的是同一件事， 只是一個在 verify 圖、一個在 decode 圖）。 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 0 份 |

- （無）

---

← [口徑＋k=3 飄移認證（16.4% 噪聲底）](exp-caliber-calibration.md)　·　[總目錄](index.md)　·　[HTML 版](exp-churn-delivery-630.html)　·　[在**認可入口**（`harness bench`、`prod-ne… →](exp-prodnew-decode20-g4miss.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
