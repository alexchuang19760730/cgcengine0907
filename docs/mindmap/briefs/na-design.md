# expert cache 血統設計（09-05~09-09 期） — 技術白皮書　·　3b ③b 實驗目標達成（可放生產）

> **一句話**：確立 expert cache 的血統設計（09-05～09-09 期）：權重常駐 SSD → 池的整體架構與介面。

- 主題：不適用　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：生產（可放生產／已交付）　·　① ② ③b —— 已進生產、或已是生產決策依據

---

## 1. 目標

確立 expert cache 的血統設計（09-05～09-09 期）：權重常駐 SSD → 池的整體架構與介面。

## 2. 判準

設計是否進了生產血統

## 3. 結果

HYBRID_DESIGN／INVARIANTS／INTEGRATION_DIFF／COMMIT_DIGEST：血統進了生產的 expert cache（現行 pool 即其後代）

## 4. 判定

**3b · ③b 實驗目標達成（可放生產）** — 產物已可放進生產級設置：不破壞正確性 ∧ 成本可接受 ∧ 無前置條件

> ⚠ 推定而非實測（歸檔於 pre-consistency-metrics 期）

---

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod-new:CGC_WARM_NPAST=256`　（profile `prod-new`；被測 option：`CGC_WARM_NPAST=256`）
- **來源**：③ 證據文件掃描　·　置信度 `med`
- **證據報告**：[CGC_EXPERT_CACHE_INVARIANTS_v1.md](../../archive/pre-consistency-metrics-2026-09-11/CGC_EXPERT_CACHE_INVARIANTS_v1.md)
- 從證據文件掃到的 arm 字串（4 份文件）

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [池大小掃描（8→4→2 GiB）](io-poolsize.md) | 3b | **仲裁（09-29）：矛盾是「儀器」造成的，不是效應。** 交付 regime（server／HTTP，`decode_sweep.py`）同場配對、**臂序相反的兩輪**：8  |
| [S1 探針臂：slot table 放 GPU（數值身分）](s1-probe.md) | 3b | 576/576 全同；answer_md5 相同 |
| [3a：未填充 expert 貢獻歸零（MISS_MASK / ZERO_MISS）](miss-3a.md) | 3b | 正確性 **PASS**／成本 **UNRESOLVED**。正確性：38 層／421 元素、0 差異（M1 9/9）；成本：mean Δ = **−0.948 ms**、SE * |
| [cb 口徑定讞（42 vs 74）](cache-cb.md) | 3b | 定讞 42~51 ms；74.18 撤回 |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `（推定：依歸檔狀態 ＋ 後續 L4／hybrid 設計檔；本線未複核）` |
| 備註 | ⚠ 推定而非實測（歸檔於 pre-consistency-metrics 期） |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 8 份 |

- CGC_EXPERT_CACHE_COMMIT_DIGEST_2026-09-05.html（僅存在於分支，不在工作區）
- CGC_EXPERT_CACHE_HYBRID_DESIGN_2026-09-05.html（僅存在於分支，不在工作區）
- CGC_EXPERT_CACHE_INTEGRATION_DIFF_2026-09-05.html（僅存在於分支，不在工作區）
- CGC_EXPERT_CACHE_INVARIANTS_v1.md（僅存在於分支，不在工作區）
- [CGC_EXPERT_CACHE_COMMIT_DIGEST_2026-09-05.html](../../archive/pre-consistency-metrics-2026-09-11/CGC_EXPERT_CACHE_COMMIT_DIGEST_2026-09-05.html)
- [CGC_EXPERT_CACHE_HYBRID_DESIGN_2026-09-05.html](../../archive/pre-consistency-metrics-2026-09-11/CGC_EXPERT_CACHE_HYBRID_DESIGN_2026-09-05.html)
- [CGC_EXPERT_CACHE_INTEGRATION_DIFF_2026-09-05.html](../../archive/pre-consistency-metrics-2026-09-11/CGC_EXPERT_CACHE_INTEGRATION_DIFF_2026-09-05.html)
- [CGC_EXPERT_CACHE_INVARIANTS_v1.md](../../archive/pre-consistency-metrics-2026-09-11/CGC_EXPERT_CACHE_INVARIANTS_v1.md)

---

← [跨線／其他產品（Wan2.2、HarmonyOS、Windows client、Colibri、Unified IR…）](na-crossline.md)　·　[總目錄](index.md)　·　[HTML 版](na-design.html)　·　[舊口徑數據報告（Gemma4／MTP_BENCHMARK_WIN8GB／TPOT 路線圖） →](na-olddata.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
