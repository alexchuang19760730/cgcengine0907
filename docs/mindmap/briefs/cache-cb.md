# cb 口徑定讞（42 vs 74） — 技術白皮書　·　3b ③b 實驗目標達成（可放生產）

> **一句話**：定讞「cb」是什麼、成本多少（42 vs 74 ms 之爭）——它是 expert cache 填池 IO，不是 command buffer。

- 主題：池／快取　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：生產（可放生產／已交付）　·　① ② ③b —— 已進生產、或已是生產決策依據

---

## 1. 目標

定讞「cb」是什麼、成本多少（42 vs 74 ms 之爭）——它是 expert cache 填池 IO，不是 command buffer。

## 2. 判準

交付 mean 口徑重算

## 3. 結果

定讞 42~51 ms；74.18 撤回

## 4. 判定

**3b · ③b 實驗目標達成（可放生產）** — 產物已可放進生產級設置：不破壞正確性 ∧ 成本可接受 ∧ 無前置條件

> 已作為後續所有上界計算的單價

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：—（未歸屬看板 15 格中的任何一格）
- ⚠ 本條不屬於看板 15 個子目標中的任何一格（已認證／已定案／已作廢）⇒ 沒有待跑的臂，也就沒有要綁的 option。
- **逐條處置**：已定案（約束／基準）　—　cb 口徑定讞（42 vs 74）：現行 `cb` 桶的讀法就是它
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- cb 口徑定讞（42 vs 74）：現行 `cb` 桶的讀法就是它

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [池大小掃描（8→4→2 GiB）](io-poolsize.md) | 3b | **仲裁（09-29）：矛盾是「儀器」造成的，不是效應。** 交付 regime（server／HTTP，`decode_sweep.py`）同場配對、**臂序相反的兩輪**：8  |
| [S1 探針臂：slot table 放 GPU（數值身分）](s1-probe.md) | 3b | 576/576 全同；answer_md5 相同 |
| [3a：未填充 expert 貢獻歸零（MISS_MASK / ZERO_MISS）](miss-3a.md) | 3b | 正確性 **PASS**／成本 **UNRESOLVED**。正確性：38 層／421 元素、0 差異（M1 9/9）；成本：mean Δ = **−0.948 ms**、SE * |
| [expert cache 血統設計（09-05~09-09 期）](na-design.md) | 3b | HYBRID_DESIGN／INVARIANTS／INTEGRATION_DIFF／COMMIT_DIGEST：血統進了生產的 expert cache（現行 pool 即其後代） |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/CB_DELIVERY_SETTLED_2026-09-23.md` |
| 備註 | 已作為後續所有上界計算的單價 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 0 份 |

- （無）

---

← [prebind／方案 A（預指派 slot）](cache-prebind.md)　·　[總目錄](index.md)　·　[HTML 版](cache-cb.html)　·　[K3 邊際單價（dispatch 成本） →](k3-price.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
