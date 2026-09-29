# accept rule／dynamic-k／n_max 調整 — 技術白皮書　·　4 ④ 廢棄

> **一句話**：調整 accept rule／dynamic-k／n_max，把接受率 a 與每輪產出 token 數拉高，直接放大攤薄倍數。

- 主題：MTP／spec　·　子目標：**M MTP on 加速**（活躍攻關軸：speculative 攤薄係數 m 與 accept rate a）
- 階段：已結案（廢棄／不適用）　·　④ 廢棄 ＋ 不適用

---

## 1. 目標

調整 accept rule／dynamic-k／n_max，把接受率 a 與每輪產出 token 數拉高，直接放大攤薄倍數。

## 2. 判準

k→∞ 上界 a/m ＋ dynamic-k oracle

## 3. 結果

不做：dynamic-k oracle 只 1.035×；n_max 3→5 作廢；greedy 下 accept 不可由 accept rule 移動（是 (base,head) 配對的性質）

## 4. 判定

**4 · ④ 廢棄** — 判死／撤回／不做

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L25-5`　MTP 的產品化上限
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod25`
- **被測 option**：`CGC_SERVER_MTP_N_MAX=1`
- **儀器開關**（不是被測 option）：`CGC_MTP_PERF=1`
- **arm 1（可複製）**：`prod25`
- **結案狀態**：未結案（已判、未立新判準）
- **逐條處置**：整合進子目標　→ `L25-5`　—　accept rule／dynamic-k／n_max：L25-5 的「acc 那一條目標」
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- 判準：m ≤ 0.30 且指名它的主項（draft 流量／verify 寬度／pool 預算）。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [RSL-MTP（draft top-8 ⊆ 已付費並集）＋ 練 draft head](mtp-rsl.md) | 4 | 被自己的數據否決：0.70–0.97×，無一格 ≥1.0；m=0.474 下即使 a→1 也不可能 2× |
| [verify residency thrash ／ 池配額 ／ prefetch 開關](mtp-verify-opt.md) | 4 | 降到次要：彈性只有 0.10–0.17（k=1/3 甚至 −0.38）；k_eff 單變數解釋 84%；修 residency 上界只剩 ~15%；m 的機械解釋是「每 draft |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/MTP_ACCEPT_RULE_G6_2026-09-20.md／MEMORY_PERF.md` |
| 備註 |  |
| 軸性質 | 活躍攻關軸：speculative 攤薄係數 m 與 accept rate a |
| 對應報告 | 2 份 |

- [MTP_ACCEPT_RULE_G6_2026-09-20.md](../../MTP_ACCEPT_RULE_G6_2026-09-20.md)
- [MTP_ROUND_COST_OPT_BACKLOG_2026-09-19.md](../../MTP_ROUND_COST_OPT_BACKLOG_2026-09-19.md)

---

← [verify residency thrash ／ 池配額 ／ prefetch 開關](mtp-verify-opt.md)　·　[總目錄](index.md)　·　[HTML 版](mtp-accept.html)　·　[ρ 路線（按層批次化 prefetch） →](cache-rho.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
