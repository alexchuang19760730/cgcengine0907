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

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod-new:CGC_HOOK_SPLIT=1;CGC_CB_N_MAIN=32`　（profile `prod-new`；被測 option：`CGC_HOOK_SPLIT=1`；`CGC_CB_N_MAIN=32`）
- **來源**：① 實跑 arm（有 log）　·　置信度 `high`
- **儀器開關**（不是被測 option）：`CGC_DECODE_PROFILE=1`；`CGC_GPU_TIMING=1`
- **測試 log**：[bench.json](../../../Backup/phase_decomp/cbnmain_pair/on1/bench.json)
- 節點自帶實跑 arm（3 筆 run，log 可點）

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
