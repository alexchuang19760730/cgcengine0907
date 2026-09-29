# 池大小掃描（8→4→2 GiB） — 技術白皮書　·　3b ③b 實驗目標達成（可放生產）

> **一句話**：掃描 expert cache 池大小（8 → 4 → 2 GiB），量出池容量對 decode 的敏感度，判斷「加大池」能不能換到吞吐。

- 主題：fill／IO　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：生產（可放生產／已交付）　·　① ② ③b —— 已進生產、或已是生產決策依據

---

## 1. 目標

掃描 expert cache 池大小（8 → 4 → 2 GiB），量出池容量對 decode 的敏感度，判斷「加大池」能不能換到吞吐。

## 2. 判準

同 cell 配對，看 t/s 與 miss

## 3. 結果

**仲裁（09-29）：矛盾是「儀器」造成的，不是效應。** 交付 regime（server／HTTP，`decode_sweep.py`）同場配對、**臂序相反的兩輪**：8 GiB **10.74** vs 4 GiB **8.43 t/s ＝ −21.4%**（`docs/CACHE_SIZE_AB_2026-09-23.md` §2.1b），而計數器逐位元相同（143→71 槽、hit 86–87.5%→67–70.5%、capacity 淘汰 ×4–5）；曲線續：6 GiB **9.21**（−13.8%）、3 GiB **7.08**（−33.8%）。`IO_PATH_AB_2026-09-21.md` 的 **+3.1%** 用 **llama-bench matrix** 口徑，而該口徑同日已被否證**無法分辨池軸**（同支啟動自己的三個 rep 差 1.1–2.7×）⇒ **非可引用**。

## 4. 判定

**3b · ③b 實驗目標達成（可放生產）** — 產物已可放進生產級設置：不破壞正確性 ∧ 成本可接受 ∧ 無前置條件

> ★ 仲裁結論：白皮書「8G→4G −21%」**成立**（其出處就是 `CACHE_SIZE_AB`），節點原本引的那個 +3.1% 是**不可引用的儀器**。**3／4 GiB 皆不可選**（−22～−34%）；8 GiB 維持生產配置。池是**前提**不是槓桿（0 GiB 物理不可行：模型 13,026 MB 已超 GPU 建議工作集 11,453 MB，`CGC-METAL-FAIL status 5`）。唯一未開的格子是**加大池**（8→10 GiB）；業主＝既有驅動 `decode_sweep.py --arms baseline,pool-10g`（閘門：NOMINAL ＋ 反序一輪，成本 ~10 分鐘）；因 16 GB 上會加深超訂，**預期為假**，屬低優先。

---

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod25:CGC_SERVER_EXPERT_CACHE_BYTES=0`　（profile `prod25`；被測 option：`CGC_SERVER_EXPERT_CACHE_BYTES=0`）
- **來源**：③ 證據文件掃描　·　置信度 `med`
- **測試 log**：[ds_346A.json](../../../Backup/cache_size_ab/ds_346A.json)
- **證據報告**：[IO_PATH_AB_2026-09-21.md](../../IO_PATH_AB_2026-09-21.md)　[CACHE_SIZE_AB_2026-09-23.md](../../CACHE_SIZE_AB_2026-09-23.md)
- 從證據文件掃到的 arm 字串（2 份文件）

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [S1 探針臂：slot table 放 GPU（數值身分）](s1-probe.md) | 3b | 576/576 全同；answer_md5 相同 |
| [3a：未填充 expert 貢獻歸零（MISS_MASK / ZERO_MISS）](miss-3a.md) | 3b | 正確性 **PASS**／成本 **UNRESOLVED**。正確性：38 層／421 元素、0 差異（M1 9/9）；成本：mean Δ = **−0.948 ms**、SE * |
| [cb 口徑定讞（42 vs 74）](cache-cb.md) | 3b | 定讞 42~51 ms；74.18 撤回 |
| [expert cache 血統設計（09-05~09-09 期）](na-design.md) | 3b | HYBRID_DESIGN／INVARIANTS／INTEGRATION_DIFF／COMMIT_DIGEST：血統進了生產的 expert cache（現行 pool 即其後代） |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/CACHE_SIZE_AB_2026-09-23.md（可引用的那一支：同場配對＋反序）／docs/IO_PATH_AB_2026-09-21.md（不可引用：llama-bench 口徑）／scripts/check/decode_sweep.py ARMS（pool-3g/4g/6g/10g、nocache）／docs/H_MEASURED_2026-09-24.md:49` |
| 備註 | ★ 仲裁結論：白皮書「8G→4G −21%」**成立**（其出處就是 `CACHE_SIZE_AB`），節點原本引的那個 +3.1% 是**不可引用的儀器**。**3／4 GiB 皆不可選**（−22～−34%）；8 GiB 維持生產配置。池是**前提**不是槓桿（0 GiB 物理不可行：模型 13,026 MB 已超 GPU 建議工作集 11,453 MB，`CGC-METAL-FAIL status 5`）。唯一未開的格子是**加大池**（8→10 GiB）；業主＝既有驅動 `decode_sweep.py --arms baseline,pool-10g`（閘門：NOMINAL ＋ 反序一輪，成本 ~10 分鐘）；因 16 GB 上會加深超訂，**預期為假**，屬低優先。 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 1 份 |

- [IO_PATH_AB_2026-09-21.md](../../IO_PATH_AB_2026-09-21.md)

---

← [decode ≥ 25（M-25）](m-decode25.md)　·　[總目錄](index.md)　·　[HTML 版](io-poolsize.html)　·　[IO 請求形狀（合併 pread） →](io-shape.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
