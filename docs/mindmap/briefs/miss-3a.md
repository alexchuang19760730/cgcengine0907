# 3a：未填充 expert 貢獻歸零（MISS_MASK / ZERO_MISS） — 技術白皮書　·　3b ③b 實驗目標達成（可放生產）

> **一句話**：讓未填充 expert 的貢獻歸零（CGC_MISS_MASK／ZERO_MISS），使「有 miss 也能算對」成為可能——這是單段提交的正確性前提。

- 主題：缺失處理　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：生產（可放生產／已交付）　·　① ② ③b —— 已進生產、或已是生產決策依據

---

## 1. 目標

讓未填充 expert 的貢獻歸零（CGC_MISS_MASK／ZERO_MISS），使「有 miss 也能算對」成為可能——這是單段提交的正確性前提。

## 2. 判準

預先寫死雙判準：① 逐位元與 host BATCHDBG 相同 ② Δ ≤ 0.2 ms/step

## 3. 結果

正確性 **PASS**／成本 **UNRESOLVED**。正確性：38 層／421 元素、0 差異（M1 9/9）；成本：mean Δ = **−0.948 ms**、SE **1.027**、95% CI **[−2.96, +1.06]**、median **+0.585**（點估計連符號都相反）、門檻 **0.2 ms** ⇒ **不可引用為「−0.95 ms」**（CI 半寬 ±2.01 是門檻的 10 倍）。

## 4. 判定

**3b · ③b 實驗目標達成（可放生產）** — 產物已可放進生產級設置：不破壞正確性 ∧ 成本可接受 ∧ 無前置條件

> 零成本且逐位正確的**使能項**；零 fill 下無正確性收益（3c 的前置）。生產價值上限 **≤0.23%**：權威 cell 的 `CGC-MISSMASK-COST` 是 464 µs/step，其中 read 462（77.8 次 `tensor_get`）、sync 1.9（0.40%）⇒ 成本形狀是「次數 × 固定開銷」，可優化對象是**次數**（§42）。

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-2`　武裝 G3（CGC_ZERO_SLOT）
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **被測 option**：`CGC_ZERO_SLOT=1`；`CGC_SEG_BATCH=1`；`CGC_SLOT_TABLE_GPU=1`
- **arm 1（可複製）**：`prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1`
- **arm 2（可複製）**：`prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1;CGC_ZERO_SLOT=1`
- **結案狀態**：結案（PASS／FLIP）：A zero_slot=0 placeholder=250582 → B zero_slot=250636 placeholder=0；逐層 zero_slot=142=ns−1；寫入量守恆（差 0.02%）
- **逐條處置**：整合進子目標　→ `L20-2`　—　未填充 expert 貢獻歸零（MISS_MASK／ZERO_SLOT）—— L20-2 武裝 G3 的正確性前提
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- A/B 兩臂只差 CGC_ZERO_SLOT，不動 src/；2 趟，主端點是計數器不是 t/s。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [池大小掃描（8→4→2 GiB）](io-poolsize.md) | 3b | **仲裁（09-29）：矛盾是「儀器」造成的，不是效應。** 交付 regime（server／HTTP，`decode_sweep.py`）同場配對、**臂序相反的兩輪**：8  |
| [S1 探針臂：slot table 放 GPU（數值身分）](s1-probe.md) | 3b | 576/576 全同；answer_md5 相同 |
| [cb 口徑定讞（42 vs 74）](cache-cb.md) | 3b | 定讞 42~51 ms；74.18 撤回 |
| [expert cache 血統設計（09-05~09-09 期）](na-design.md) | 3b | HYBRID_DESIGN／INVARIANTS／INTEGRATION_DIFF／COMMIT_DIGEST：血統進了生產的 expert cache（現行 pool 即其後代） |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `Backup/miss_axis_mtpoff_ws64_r3/miss_axis_res.json` |
| 備註 | 零成本且逐位正確的**使能項**；零 fill 下無正確性收益（3c 的前置）。生產價值上限 **≤0.23%**：權威 cell 的 `CGC-MISSMASK-COST` 是 464 µs/step，其中 read 462（77.8 次 `tensor_get`）、sync 1.9（0.40%）⇒ 成本形狀是「次數 × 固定開銷」，可優化對象是**次數**（§42）。 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 3 份 |

- [GAP_VS_MISS_2026-09-20.md](../../GAP_VS_MISS_2026-09-20.md)
- [MISS_PATH_COST_AUDIT_2026-09-13.md](../../MISS_PATH_COST_AUDIT_2026-09-13.md)
- [SWAP_MISS_LINK_2026-09-24.md](../../SWAP_MISS_LINK_2026-09-24.md)

---

← [S1 早期診斷系列（09-16/17）](s1-refuted.md)　·　[總目錄](index.md)　·　[HTML 版](miss-3a.html)　·　[3b：fill 觸發點搬出 hook ＋ batch 化 →](miss-3b.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
