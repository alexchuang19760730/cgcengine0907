# G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 … — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 gap、消 cb）能否在保持 M1/M2/M3 bit-identical 的前提下，帶來可交付（生產口徑可復現）的 decode 提升？

- 主題：實驗　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 gap、消 cb）能否在保持 M1/M2/M3 bit-identical 的前提下，帶來可交付（生產口徑可復現）的 decode 提升？

## 2. 判準

保留 hook 與 demand-fill 的生產口徑下，ABBA 配對 decode 提升 ≥ 3%， 且 M1/M2/M3 全過（護欄不讓步）。

## 3. 結果

判詞：11.97 那一族**作廢**（該臂 9 個 run 全部非乾淨）。本節點不主張吞吐。

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 假設：41 段提交的段間同步與 top-k hook（cb）是串行開銷；合成單段（CGC_SEG_BATCH＋ B_SCHEME）可消掉段間同步，宣稱 step −40.3 ms（11.30→20.73 t/s）——**該宣稱的兩端皆已作廢**，見本節點 res 判詞與 no_throughput_claim。

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-1`　底（S1 的 step）校準
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **被測 option**：`CGC_SEG_BATCH=1`；`CGC_B_SCHEME=1`；`CGC_SLOT_TABLE_GPU=1`
- **儀器開關**（不是被測 option）：`CGC_MISS_MASK=1`；`CGC_MISS_MASK_DBG=1`；`CGC_MISS_MASK_COST=1`
- **結案狀態**：結案（排除）——2026-10-02 判死（否證式）：提交路徑 0.75 ms/步（預註冊否證判據 T_sub &lt; 0.31 ms 成立）⇒ 不再投入。⚠ 原比較項「GPU 空轉 ~47 ms/步」已於 10-03 作廢 ⇒ 判死理由改由該判據承接。出處：Backup/l201_accel/fast_cost_split_decode_20261002.json、Backup/cgc_logs/gpu_util_decode_20261002.log。沿革：底已收斂到交付 cell：36.33 ms 重現；該讀數經引用閘門判 DIRTY：逐 rep 1.015 合格，但 attribution=swap）⚠ 2026-09-30 機檢更正：窗口不是這一格的主要卡點。對這支臂跑 quote_gate 的登記表 ⇒ R5 乾淨、R6-DIRTY（唯一規則 CGC_SEG_BATCH）⇒ 「只差窗口」作廢。⚠⚠ 同日把那條解除條件也跑掉了：配對 M1/M2/M3 ＝ 9/9 vs 0/9（第一格 decode 就不同）⇒ 輸出確實不同。⚠⚠⚠ 同日第三件事：換臂的設計題也有答案了 —— 沒有這種臂（五臂同場：控制 99.3／SLOT 118.1／B_SCHEME+SLOT 90.9／HOOK_SPLIT 91.8 ms；S1 49.0 ms）⇒ 底與旗標不可分離；且 hook 自身 CPU 只有 6.5 ms/step（vs 紅利 50 ms）⇒ 真正的卡點是提交路徑。詳見 docs/S1_FLOOR_ARM_ATTRIB_2026-09-30.md；該輪五臂皆 DIRTY（thermal HEAVY／swap）⇒ 診斷級，不可引用。2026-10-01：S2-c 首次 GPU 驗證未過 —— courier 有交棒（CGC-RHO-S2C: layers/step=40、SKIP 守衛也對），但 fill 開啟時 logits 全 NaN（引擎自蓋 invalid）；另抓到一個先於 S2-c 的獨立 NaN：CGC_SLOT_TABLE_GPU×單段臂。patch 已反轉、未套用（llama-context.cpp md5 逐字回原值）；帳：docs/R6_SEGBATCH_FIX_PLAN_2026-09-30.md §4.7。2026-10-01 16:5x：兩根 NaN 已 bisect（修正歸因） —— CGC_SLOT_TABLE_GPU=1 單獨跑是 valid／M1 9/9，加上 CGC_SEG_BATCH 才 全非有限值⇒ 不是那顆旗標自己 NaN，是單段提交 × GPU slot-table leaf 的交互（卡上舊語已改）。兩根各有指紋、可各自查；驗收都是 M1 9/9。證據 docs/L201_NAN_BISECT_2026-10-01.md。2026-10-01 結構裁決落帳：R6 三路判死（R1 恢復循環不足／C 全駐留 OOM／(i) post-submit 不可行）⇒「引擎把單段提交做成輸出可驗」作廢，卡點改 decision／operator（端點重定義：機制／計數器，或判死）。X 線診斷腳手架（a9/a10、Phase 0/1、R1PROBE）已全撤；2026-10-01 晚間再撤 S2-c 區塊與兩根 NaN 修（chartered 偵測亦除）⇒ 樹上 R6 殘留＝0（未提交僅餘 L25-2 精修與另一線 MTP／bench 校準）。2026-10-01 深夜：端點重定義落帳 —— 時間端點判死（36.33／49.18 不再主張；天花板維持 19.2 側）；機制端點（提交數 41→1／ms-submit）立項 scripts/check/charters/e-l201-submit-path-2026-10-01.yaml。2026-10-01 深夜：端點已量、判 MECHANISM —— 誠實臂（prod-new:CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1）T_sub 中位 0.937 ms／step≥64 1.007 ⇒ 40×T_sub＝37.5／40.3 ms（同場參考紅利 50.3 的 74–80%）⇒ 提交路徑＝紅利主體（預註冊判據 [0.63, 1.89]）。⚠ 本場髒窗（HEAVY／swap +1475 MiB）⇒ 任何 t/s 不引用；T_sub 本身是引擎內計時器（不隨窗口漂移）。S1 臂 1 提交/步只作結構對照（該臂未武裝儀器、0 行計數；t/s 依 R6 永不引用）。2026-10-03 措辭更正（零新量測；Backup/spec_tree/WAIT_ATTRIBUTION_2026-10-03.md）：① gpu_busy_sum/wait＝153%（儀器自帶判據：≥70% ⇒ wait 是真 GPU 執行）、gpu_union＝105% ⇒ 「GPU 空轉 47 ms/步」作廢；② 唯一有名字的縫＝gap 16.54 ms（23.7%），其 CPU 部分（hook 6.88＋submit 3.70＝10.58 ms）正是 overlap fence 收割的量（×1.218 ≈ 23.7%，內部自洽）；③ 殘餘 gap_not_cpu ≈ 6.0 ms 是 host 自旋（poll_iters 632,391/步），其兩個候選解（S1＝換口徑；async fill＝收益不足）都已試過 ⇒ 該檔結論＝20+ 在 bit-identical 口徑下判死（不存在 ≥5 ms 的可回收項）；本格判詞（提交路徑非主體）不變，改的是措辭與下一個靶。
- **逐條處置**：整合進子目標　→ `L20-1`　—　G0–G7 的序列化回顧：同一條軸的歷史與作廢清單，決定 L20-1 只認哪一端
- **測試 log（實跑）**：[bench.json](../../../Backup/phase_decomp/cbnmain_pair/on1/bench.json)　[bench.json](../../../Backup/phase_decomp/cbnmain_pair/off2/bench.json)　[bench.json](../../../Backup/phase_decomp/cbnmain_pair/on3/bench.json)
- 控制臂＝裸 prod-new；成對重跑（交付 cell、MTP off、warm-skip 64）。底 36.80 ⇒ 天花板 24.5；底 49.18 ⇒ 19.2。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [CGC_EB_NOFILL 診斷臂（fill 成本）](io-nofill.md) | 3a | fill 3.955 → 0.206 ms/step（−95%）⇒ 機制證；生產口徑同步 fill 僅占 step 4.7% |
| [單段提交（41 段 → 1 段）](s1-segbatch.md) | 3a | 判詞：41 段→1 段的 A/B **作廢**（兩端都不可引用）。本節點不主張吞吐。**補（09-29）**：它的**可交付上界**＝序列化 − 必須還回去的 fill ⇒ **1 |
| [異步 gather 流水線（單段＋miss 後台補＋局部重算）](s1-asyncgather.md) | 3a | E0 已給出決定性答案：前提成立——S1（暖池 8 GiB）輸出 文摘文摘… 且 logits 全非有限（引擎自蓋 INVALID），同一輪的分段臂是 42 ⇒ 異步 fill＋補 |
| [ρ 路線（按層批次化 prefetch）](cache-rho.md) | 3a | 判活，但本節點**不主張吞吐**：覆蓋 0.849、視窗 1.30~1.59 ms、每步付 4.76 ms GPU 插入 ⇒ 有前置條件。 |
| [prebind／方案 A（預指派 slot）](cache-prebind.md) | 3a | 判活，但本節點**不主張吞吐**：qu2/qu3 全過、qu1 邊緣（預指派 slot，提前一整步發起 ⇒ 視窗 ≈ 一步）。 |
| [S2：段邊界免等（天花板 18.7 t/s）](exp-s2-overlap.md) | 3a | **已結（FAIL 附機制）**。segment／gap 部分：段界是**資料依賴**（每層 argsort 一個邊界把 top-k 拿回 host 寫 remap leaf，L+ |

## 7. 運行設置與 Log（生產級腳本 prod-new ＋ 自己 option）

**Run 1** · 2026-09-27 10:50　·　thermal HEAVY　·　swap 2973 MiB→5924 MiB
- arm：`prod-new:CGC_DECODE_PROFILE=1;CGC_HOOK_SPLIT=1;CGC_GPU_TIMING=1;CGC_CB_N_MAIN=32`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[bench.json](../../../Backup/phase_decomp/cbnmain_pair/on1/bench.json)
- 結果：pp=271.11　tg=12.42
- 判定：both：thermal=HEAVY, swap_growth=2959.56 MiB

**Run 2** · 2026-09-27 10:50　·　thermal HEAVY　·　swap 5480 MiB→5959 MiB
- arm：`prod-new:CGC_DECODE_PROFILE=1;CGC_HOOK_SPLIT=1;CGC_GPU_TIMING=1`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[bench.json](../../../Backup/phase_decomp/cbnmain_pair/off2/bench.json)
- 結果：pp=283.01　tg=11.59
- 判定：both：thermal=HEAVY, swap_growth=486.5 MiB

**Run 3** · 2026-09-27 10:50　·　thermal NOMINAL　·　swap 5663 MiB→6683 MiB
- arm：`prod-new:CGC_DECODE_PROFILE=1;CGC_HOOK_SPLIT=1;CGC_GPU_TIMING=1;CGC_CB_N_MAIN=32`
- 命令：`llama-bench（prod-new）-p 2048 -n 64 --warm-skip 64（由產物參數重建）`
- Log／產物：[bench.json](../../../Backup/phase_decomp/cbnmain_pair/on3/bench.json)
- 結果：pp=279.27　tg=11.97
- 判定：swap：swap_growth=1027.4300000000003 MiB, max_swap=7197.5 MiB

## 8. 子目標分解（持續更新；2/4 完成）

- [x] 跑前立項（現狀/目標/假設/驗收） [證](../../../scripts/check/charters/exp-s-retro.yaml)
- [x] 用生產級腳本 prod-new ＋ 自己 option 跑實驗臂，留存 log
- [ ] 驗收：保留 hook 與 demand-fill 的生產口徑下，ABBA 配對 decode 提升 ≥ 3%， 且 M1/M2/M3 全過（護欄不讓步）。
- [ ] 否證條件：大數字（20.73 t/s、busy −42 ms）只在「無 hook／無 demand-fill」時出現， 生產口徑復現不了；或待測效應 < 實測噪音底（臂間極差 11.9%）。

## 9. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/CEILING_STACK_2026-09-21.md` |
| 備註 | 假設：41 段提交的段間同步與 top-k hook（cb）是串行開銷；合成單段（CGC_SEG_BATCH＋ B_SCHEME）可消掉段間同步，宣稱 step −40.3 ms（11.30→20.73 t/s）——**該宣稱的兩端皆已作廢**，見本節點 res 判詞與 no_throughput_claim。 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 0 份 |

- （無）

---

← [舊口徑數據報告（Gemma4／MTP_BENCHMARK_WIN8GB／TPOT 路線圖）](na-olddata.md)　·　[總目錄](index.md)　·　[HTML 版](exp-s-retro.html)　·　[成績排行榜（最高配置＋檢驗檔） →](score-leaderboard.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
