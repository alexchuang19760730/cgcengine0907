# CGC_EB_NOFILL 診斷臂（fill 成本） — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：診斷臂：用 CGC_EB_NOFILL 把填池關掉，量出「fill 在一個 decode step 裡到底佔多少」。

- 主題：fill／IO　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

診斷臂：用 CGC_EB_NOFILL 把填池關掉，量出「fill 在一個 decode step 裡到底佔多少」。

## 2. 判準

同 build 同 cell，fill 開/關；機制看 fill ms/step 是否歸零

## 3. 結果

fill 3.955 → 0.206 ms/step（−95%）⇒ 機制證；生產口徑同步 fill 僅占 step 4.7%

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 端到端 reps=3 仍缺（nofill 臂曾被看門狗 SIGTERM）。**3.955／1.60–3.20／5.41／8.3 的 5× 口徑差已立項**（`e-fill-term-price-2026-09-29.yaml`，targets 20.0）：不收斂成一個數 ⇒ 20+ 天花板只能寫成區間。

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-4`　fill 那一個 term 的定價
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **被測 option**：`CGC_EB_NOFILL=1`
- **儀器開關**（不是被測 option）：`CGC_EB_TIMER=1`
- **arm 1（可複製）**：`prod-new:CGC_EB_NOFILL=1`
- **結案狀態**：結案（§69）——價格＝區間 [6.79, 20.68] ms/step（交付 cell 步時 92.8 ms 的 7.3–22.3%）。口徑①儀器（EBTIMER 包住整個 ensure_batch）6.79 ms/step（逐 rep 9.93／4.91／5.40、CV 33.6%；穩態 5.18、CV 4.1%）、口徑②牆鐘（逐 rep 配對 1000/ts_A−1000/ts_B）20.68 ms/token（30.96／16.89／14.19、CV 35.5%）；B 的底 0.061 ⇒ 可移除 6.73。子目標 sg-fill-price（CV ≤ 20%）未達（整跑 33.6%／35.5%；只有換到穩態窗口才 4.1%）⇒ 依 on_fail 天花板維持區間，不得只用有利的那一端。⚠ 48.2 出自別的 cell（p2048）⇒ 本格交付的是價格，不是 t/s。
- **逐條處置**：整合進子目標　→ `L20-4`　—　CGC_EB_NOFILL 診斷臂：fill 那一個 term 的定價就靠它（3.955→0.206 ms/step）
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- 本格要的是「哪一個 term 進 48.2 + x」的算式與不確定度，不是提速。⚠ §69.6 跨格旗標：價格指到正確的格子之後，倚賴舊價 3.955（(default) cell）的結論要重問一次——MISSHIST_REVIVAL_GATE_2026-09-29.md §4 的邊際在 ±0.9 ms 之內變號（k=6 重算 6.07 vs 交付 cell 價 6.79 ⇒ +0.72；vs 穩態 5.18 ⇒ −0.89）。重判需要一趟同格成對量，不是這一趟。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [單段提交（41 段 → 1 段）](s1-segbatch.md) | 3a | 判詞：41 段→1 段的 A/B **作廢**（兩端都不可引用）。本節點不主張吞吐。**補（09-29）**：它的**可交付上界**＝序列化 − 必須還回去的 fill ⇒ **1 |
| [異步 gather 流水線（單段＋miss 後台補＋局部重算）](s1-asyncgather.md) | 3a | E0 已給出決定性答案：前提成立——S1（暖池 8 GiB）輸出 文摘文摘… 且 logits 全非有限（引擎自蓋 INVALID），同一輪的分段臂是 42 ⇒ 異步 fill＋補 |
| [ρ 路線（按層批次化 prefetch）](cache-rho.md) | 3a | 判活，但本節點**不主張吞吐**：覆蓋 0.849、視窗 1.30~1.59 ms、每步付 4.76 ms GPU 插入 ⇒ 有前置條件。 |
| [prebind／方案 A（預指派 slot）](cache-prebind.md) | 3a | 判活，但本節點**不主張吞吐**：qu2/qu3 全過、qu1 邊緣（預指派 slot，提前一整步發起 ⇒ 視窗 ≈ 一步）。 |
| [G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 …](exp-s-retro.md) | 3a | 判詞：11.97 那一族**作廢**（該臂 9 個 run 全部非乾淨）。本節點不主張吞吐。 |
| [S2：段邊界免等（天花板 18.7 t/s）](exp-s2-overlap.md) | 3a | **已結（FAIL 附機制）**。segment／gap 部分：段界是**資料依賴**（每層 argsort 一個邊界把 top-k 拿回 host 寫 remap leaf，L+ |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `Backup/nofill_prod/nf_fill.json` |
| 備註 | 端到端 reps=3 仍缺（nofill 臂曾被看門狗 SIGTERM）。**3.955／1.60–3.20／5.41／8.3 的 5× 口徑差已立項**（`e-fill-term-price-2026-09-29.yaml`，targets 20.0）：不收斂成一個數 ⇒ 20+ 天花板只能寫成區間。 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 1 份 |

- [IO_AXIS_VERDICT_2026-09-25.md](../../IO_AXIS_VERDICT_2026-09-25.md)

---

← [M-PF：背景 neighbour prefetch](io-mpf.md)　·　[總目錄](index.md)　·　[HTML 版](io-nofill.html)　·　[S1 探針臂：slot table 放 GPU（數值身分） →](s1-probe.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
