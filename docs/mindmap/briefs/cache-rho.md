# ρ 路線（按層批次化 prefetch） — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：走 ρ 路線：按層批次化 prefetch，用一次大批次取代多次零散小預取，減少預取本身的開銷。

- 主題：池／快取　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

走 ρ 路線：按層批次化 prefetch，用一次大批次取代多次零散小預取，減少預取本身的開銷。

## 2. 判準

qu gate（覆蓋率 ＋ 視窗）

## 3. 結果

判活，但本節點**不主張吞吐**：覆蓋 0.849、視窗 1.30~1.59 ms、每步付 4.76 ms GPU 插入 ⇒ 有前置條件。

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 與 prebind 同價、不可相加

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-7`　prefetch 的兩個前置分支
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **被測 option**：`CGC_RHO_PROBE=1`；`CGC_PREBIND_PROBE=1`
- **儀器開關**（不是被測 option）：`CGC_PREBIND_PROBE_VERBOSE=1`；`CGC_RHO_PROBE_LATE=1`
- **arm 1（可複製）**：`prod-new:CGC_PREBIND_PROBE=1`
- **結案狀態**：未結案（有前置）
- **逐條處置**：整合進子目標　→ `L20-7`　—　ρ 按層批次化 prefetch（覆蓋 0.849、每步付 4.76 ms）
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- 前置題：先付 4.76 ms（藏掉或攤平）再談收益；前置成本 ≤ 收益且覆蓋不降才算過。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [CGC_EB_NOFILL 診斷臂（fill 成本）](io-nofill.md) | 3a | fill 3.955 → 0.206 ms/step（−95%）⇒ 機制證；生產口徑同步 fill 僅占 step 4.7% |
| [單段提交（41 段 → 1 段）](s1-segbatch.md) | 3a | 判詞：41 段→1 段的 A/B **作廢**（兩端都不可引用）。本節點不主張吞吐。**補（09-29）**：它的**可交付上界**＝序列化 − 必須還回去的 fill ⇒ **1 |
| [異步 gather 流水線（單段＋miss 後台補＋局部重算）](s1-asyncgather.md) | 3a | E0 已給出決定性答案：前提成立——S1（暖池 8 GiB）輸出 文摘文摘… 且 logits 全非有限（引擎自蓋 INVALID），同一輪的分段臂是 42 ⇒ 異步 fill＋補 |
| [prebind／方案 A（預指派 slot）](cache-prebind.md) | 3a | 判活，但本節點**不主張吞吐**：qu2/qu3 全過、qu1 邊緣（預指派 slot，提前一整步發起 ⇒ 視窗 ≈ 一步）。 |
| [G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 …](exp-s-retro.md) | 3a | 判詞：11.97 那一族**作廢**（該臂 9 個 run 全部非乾淨）。本節點不主張吞吐。 |
| [S2：段邊界免等（天花板 18.7 t/s）](exp-s2-overlap.md) | 3a | **已結（FAIL 附機制）**。segment／gap 部分：段界是**資料依賴**（每層 argsort 一個邊界把 top-k 拿回 host 寫 remap leaf，L+ |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/RHO_STAGE0_RESULT_2026-09-23.md／F1_CB_MISS_REGRESSION_RESULT_2026-09-20.md` |
| 備註 | 與 prebind 同價、不可相加 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 5 份 |

- [F1_CB_MISS_REGRESSION_RESULT_2026-09-20.md](../../F1_CB_MISS_REGRESSION_RESULT_2026-09-20.md)
- [GAP_SPLIT_SWAP_BIAS_2026-09-20.md](../../GAP_SPLIT_SWAP_BIAS_2026-09-20.md)
- [RHO_BATCH_REGRESSION_2026-09-24.md](../../RHO_BATCH_REGRESSION_2026-09-24.md)
- [RHO_MAXQ_SETTLED_2026-09-23.md](../../RHO_MAXQ_SETTLED_2026-09-23.md)
- [RHO_STAGE0_RESULT_2026-09-23.md](../../RHO_STAGE0_RESULT_2026-09-23.md)

---

← [accept rule／dynamic-k／n_max 調整](mtp-accept.md)　·　[總目錄](index.md)　·　[HTML 版](cache-rho.html)　·　[prebind／方案 A（預指派 slot） →](cache-prebind.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
