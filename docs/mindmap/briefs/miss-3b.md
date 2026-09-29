# 3b：fill 觸發點搬出 hook ＋ batch 化 — 技術白皮書　·　4 ④ 廢棄

> **一句話**：把 fill 的觸發點搬出 hook 並批次化，讓填池不再被切成 41 次小動作，而是一次大批次。

- 主題：缺失處理　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：已結案（廢棄／不適用）　·　④ 廢棄 ＋ 不適用

---

## 1. 目標

把 fill 的觸發點搬出 hook 並批次化，讓填池不再被切成 41 次小動作，而是一次大批次。

## 2. 判準

目標 ≤5 ms/step（現況 21 ms，超 4.3×）

## 3. 結果

判死：合併邏輯早已存在、幾何 182.6×、生產口徑上界 ~9% ⇒ 收益 ~2.9%

## 4. 判定

**4 · ④ 廢棄** — 判死／撤回／不做

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-5`　餵料搬家（P1）
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **被測 option**：`CGC_SPAC_DBG=1`；`CGC_PREFETCH_SRC=hist`；`CGC_PREFETCH_WINDOW=4`
- **arm 1（可複製）**：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_SPAC_DBG=1`
- **arm 2（可複製）**：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_PREFETCH_SRC=hist;CGC_PREFETCH_WINDOW=4`
- **結案狀態**：未結案（P0 成立、P1 未做）
- **逐條處置**：整合進子目標　→ `L20-5`　—　fill 觸發點搬出 hook ＋ batch 化 —— 正是 L20-5「餵料搬家」要搬的那一段
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- 驗收：不開 debug 仍要有 CGC-RB-FEED 且 prefetch > 0/0。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [IO 請求形狀（合併 pread）](io-shape.md) | 4 | 一個 expert 三段相隔 88~115 MB ⇒ 3→1 要付 182.6× 位元組；合併邏輯早已存在（preadv）；固定開銷占 90% |
| [M-W：WORKERS 8→2](io-mw.md) | 4 | us/job −11.1%、us_per_miss −14.7%，但 t/s −0.6% ⇒ 未分離（by-product：IO 只占 step ≈9%） |
| [CGC_LAYER_AHEAD_PREFETCH（提前一層）](io-layer-ahead.md) | 4 | −7.1%（未分離、方向偏負） |
| [M-PF：背景 neighbour prefetch](io-mpf.md) | 4 | 撤回（讀入位元組 1.98×，實測最好只到 35%） |
| [S1 早期診斷系列（09-16/17）](s1-refuted.md) | 4 | 多輪被自己否證：slot owner 推論被推翻、時序推論被推翻、09-16 那七輪「第一個分歧」全部不可引用（同時踩三個盲點） |
| [3c：per-expert 重算 kernel](miss-3c.md) | 4 | 不做（依賴鏈斷；上限同受 ~5% 約束；舊估 +13~18% 來自已作廢的非生產 cell） |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md／docs/IO_AXIS_VERDICT_2026-09-25.md` |
| 備註 |  |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 0 份 |

- （無）

---

← [3a：未填充 expert 貢獻歸零（MISS_MASK / ZERO_MISS）](miss-3a.md)　·　[總目錄](index.md)　·　[HTML 版](miss-3b.html)　·　[3c：per-expert 重算 kernel →](miss-3c.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
