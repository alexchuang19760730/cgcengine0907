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
- **profile**：`prod-new`
- **被測 option**：`CGC_SERVER_MTP=1`
- **儀器開關**（不是被測 option）：`CGC_MTP_PERF=1`
- **CLI**：`--spec-type draft-mtp`；`--spec-draft-n-max 1`
- **arm 1（可複製）**：`prod-new:CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:16;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;CGC_MTP_PERF=1`
- **結案狀態**：結案（§63，端點＝m 比值門檻）：乾淨窗口（attribution=none、NOMINAL、起跑 free 7159 MB）一趟 k=1 的 m ＝ 0.159（T_draft 13.70 ms/輪 ÷ 86.13；五條閘全過）≤ 目標 0.30。⇒ 卡片自己推的 k 無窮上界 24.59 是由 m=0.474 推出來的。⚠ 09-30 更正：原文寫「實測更小 ⇒ 上界仍成立」是方向反了 —— m 在分母，m 越小上界越高：1000/(85.8×0.159) ＝ 73.30（不是 24.59）。⚠ 兩件事要說清楚：(i) m 在劣化窗只會被吹大（單邊性）⇒ 乾淨窗量到的 0.159 是上界；(ii) prod-new 的 draft 成本比 09-24 舊 build 高 60%（0.159 vs 0.099）⇒ 但「天花板比舊帳面更低」講反了：draft 成本變高 ⇒ m 變大 ⇒ 上限變低；本格量到的 m 比舊帳面小（0.159 vs 0.474）⇒ 天花板反而被抬高到 73.30。兩者不衝突：09-24 舊 build 的 m≈0.099 是「同 cell、pool 3 GiB」的讀數，不能與 prod-new 的 0.159 直接比價；真正的帳應以同一 cell 為準。
- **逐條處置**：整合進子目標　→ `L25-5`　—　accept rule／dynamic-k／n_max：L25-5 的「acc 那一條目標」
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- ★ 09-29 起本格的量測口徑改為 prod-new ＋ harness bench（唯一認可入口），不再是 prod25／server ABBA。判準：m ≤ 0.30 且指名它的主項（draft 流量／verify 寬度／pool 預算）。⚠ 單邊性：窗劣化只把 wall 吹大 ⇒ 實測 t_draft ≥ 真值 ⇒ 由它算出的 m 是上界。

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
