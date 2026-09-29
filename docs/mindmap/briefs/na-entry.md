# 入口／索引／決策頁（非實驗） — 技術白皮書　·　na 不適用（非實驗結論）

> **一句話**：（非實驗）維護入口／索引／決策頁，讓判決有唯一可查的落點。

- 主題：不適用　·　子目標：**不適用**（非攻關軸：約束／帳本／上界算術／入口索引／跨線產品）
- 階段：已結案（廢棄／不適用）　·　④ 廢棄 ＋ 不適用

---

## 1. 目標

（非實驗）維護入口／索引／決策頁，讓判決有唯一可查的落點。

## 2. 判準

—

## 3. 結果

不進四級：它們是入口與索引

## 4. 判定

**na · 不適用（非實驗結論）** — 設計／指南／紀錄／跨產品；或本線無實測權 —— 不塞進四級

---

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod-new`　（profile `prod-new`；無自己的 option）
- **來源**：⑤ 非實驗結論　·　置信度 `n/a`
- **證據報告**：[DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md](../../DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md)　[ENGINE_LINE_ASSIGNMENT_AND_G1_LADDERS_2026-09-20.md](../../ENGINE_LINE_ASSIGNMENT_AND_G1_LADDERS_2026-09-20.md)　[LATEST_COMMIT_GAP_ANALYSIS_2026-09-15.md](../../LATEST_COMMIT_GAP_ANALYSIS_2026-09-15.md)　[MILESTONE_MAP_2026-09-21.md](../../MILESTONE_MAP_2026-09-21.md)
- 非實驗結論（約束／帳本／上界算術／入口索引／跨線）⇒ 無待測 option；其數字全部來自 prod-new 預設臂

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [M3／M4／M5 離開條件（09-17 期）](m3.md) | 4 | M3 未達（9.82 vs 門檻 15）；M4 靠一個本身壞掉的量測被否決、修好後才關閉 —— 而那個量測現已作廢 |
| [跨線／其他產品（Wan2.2、HarmonyOS、Windows client、Colibri、Unified IR…）](na-crossline.md) | na | 本線無實測權或非本線主題 ⇒ 不塞進四級 |
| [舊口徑數據報告（Gemma4／MTP_BENCHMARK_WIN8GB／TPOT 路線圖）](na-olddata.md) | 4 | 作廢：09-17 起 decode 一律 llama-bench、warm-skip 口徑（`MEMORY_PERF.md` 裁定），且 MTP_BENCHMARK_WIN8GB  |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `—` |
| 備註 |  |
| 軸性質 | 非攻關軸：約束／帳本／上界算術／入口索引／跨線產品 |
| 對應報告 | 11 份 |

- [DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md](../../DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md)
- [ENGINE_LINE_ASSIGNMENT_AND_G1_LADDERS_2026-09-20.md](../../ENGINE_LINE_ASSIGNMENT_AND_G1_LADDERS_2026-09-20.md)
- [LATEST_COMMIT_GAP_ANALYSIS_2026-09-15.md](../../LATEST_COMMIT_GAP_ANALYSIS_2026-09-15.md)
- [MILESTONE_MAP_2026-09-21.md](../../MILESTONE_MAP_2026-09-21.md)
- [MILESTONE_MAP_20260920_1345.html](../../MILESTONE_MAP_20260920_1345.html)
- [MILESTONE_MAP_RECHECK_2026-09-25.md](../../MILESTONE_MAP_RECHECK_2026-09-25.md)
- [MTP_AMORTIZATION_2026-09-25.html](../../MTP_AMORTIZATION_2026-09-25.html)
- [MW_WORKERS_VERDICT_2026-09-25.md](../../MW_WORKERS_VERDICT_2026-09-25.md)
- [S1_LINE_VERDICT_2026-09-25.md](../../S1_LINE_VERDICT_2026-09-25.md)
- [S1_TPOT_DECOMPOSITION_2026-09-25.html](../../S1_TPOT_DECOMPOSITION_2026-09-25.html)
- [TODAY_TECH_DECISIONS.html](../../TODAY_TECH_DECISIONS.html)

---

← [M3／M4／M5 離開條件（09-17 期）](m3.md)　·　[總目錄](index.md)　·　[HTML 版](na-entry.html)　·　[跨線／其他產品（Wan2.2、HarmonyOS、Windows client、Colibri、Unified IR…） →](na-crossline.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
