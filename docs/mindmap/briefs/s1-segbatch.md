# 單段提交（41 段 → 1 段） — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：把 41 段提交合成 1 段（CGC_SEG_BATCH＋CGC_B_SCHEME），消掉段與段之間的同步與序列化開銷。

- 主題：S1／段邊界　·　子目標：**S 序列化消減**（活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

把 41 段提交合成 1 段（CGC_SEG_BATCH＋CGC_B_SCHEME），消掉段與段之間的同步與序列化開銷。

## 2. 判準

同 build 同 cell 同 pool，兩臂皆無 spec（無驗收門檻）

## 3. 結果

判詞：41 段→1 段的 A/B **作廢**（兩端都不可引用）。本節點不主張吞吐。**補（09-29）**：它的**可交付上界**＝序列化 − 必須還回去的 fill ⇒ **19.2–24.5 t/s**（底未裁：預設 cell 的 S1 step 同場讀 **49.18**，交付 cell 讀 **36.80**，差 12.4 ms；兩者只在同一組開關下才可比）。**09-29 21:3x 成對實測（交付 cell、(default)、MTP off、warm_skip 64、未開 `_DBG`/`_COST`）**：S1 **36.33 ms（27.526±0.228 t/s）** vs 控制 **88.29 ms（11.327±0.349）** ⇒ **36.80 ms 那一側重現**（差 0.5 ms），而舊的「49.18」是**不同 cell** 的另一支。⚠ 兩臂皆 `attribution=swap`（+851／+1768 MiB）⇒ 這些 t/s **不可引用**。 **§50**：越線且**池活著**的一格屬於 `fillahead` 族（39.0 ms），不是本節點（本節點是凍結簽名）；依 §50，decode ≥25 的判詞已由「判死」改記「**未判定**」，剩下的 **0.6–2.2 ms** 是 **B 半重算補丁**的邊界。 **§52（09-29）**：同族的 **fillahead** 臂在**交付 cell** 上實測 **37.81 ms（26.45 t/s）**、`instrument_gate=BOUND`（池真的在餵）⇒ 交付 cell 的底座應記 **37.81**，而 §43 用的 39.0 是**預設 cell**。

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 無 hook ⇒ 無 demand fill ⇒ 正確性未證 ⇒ 不能進生產

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-1`　底（S1 的 step）校準
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **被測 option**：`CGC_SEG_BATCH=1`；`CGC_B_SCHEME=1`；`CGC_SLOT_TABLE_GPU=1`
- **儀器開關**（不是被測 option）：`CGC_MISS_MASK=1`；`CGC_MISS_MASK_DBG=1`；`CGC_MISS_MASK_COST=1`
- **結案狀態**：未結案（底已收斂到交付 cell：36.33 ms 重現；缺的是穩定窗口 —— 該讀數經引用閘門判 DIRTY：逐 rep 1.015 合格，但 attribution=swap）
- **逐條處置**：整合進子目標　→ `L20-1`　—　單段提交本體 —— L20-1 要校準的那個 step 就是它
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- 控制臂＝裸 prod-new；成對重跑（交付 cell、MTP off、warm-skip 64）。底 36.80 ⇒ 天花板 24.5；底 49.18 ⇒ 19.2。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [CGC_EB_NOFILL 診斷臂（fill 成本）](io-nofill.md) | 3a | fill 3.955 → 0.206 ms/step（−95%）⇒ 機制證；生產口徑同步 fill 僅占 step 4.7% |
| [異步 gather 流水線（單段＋miss 後台補＋局部重算）](s1-asyncgather.md) | 3a | E0 已給出決定性答案：前提成立——S1（暖池 8 GiB）輸出 文摘文摘… 且 logits 全非有限（引擎自蓋 INVALID），同一輪的分段臂是 42 ⇒ 異步 fill＋補 |
| [ρ 路線（按層批次化 prefetch）](cache-rho.md) | 3a | 判活，但本節點**不主張吞吐**：覆蓋 0.849、視窗 1.30~1.59 ms、每步付 4.76 ms GPU 插入 ⇒ 有前置條件。 |
| [prebind／方案 A（預指派 slot）](cache-prebind.md) | 3a | 判活，但本節點**不主張吞吐**：qu2/qu3 全過、qu1 邊緣（預指派 slot，提前一整步發起 ⇒ 視窗 ≈ 一步）。 |
| [G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 …](exp-s-retro.md) | 3a | 判詞：11.97 那一族**作廢**（該臂 9 個 run 全部非乾淨）。本節點不主張吞吐。 |
| [S2：段邊界免等（天花板 18.7 t/s）](exp-s2-overlap.md) | 3a | **已結（FAIL 附機制）**。segment／gap 部分：段界是**資料依賴**（每層 argsort 一個邊界把 top-k 拿回 host 寫 remap leaf，L+ |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `Backup/seg_batch_s1_pairs/abba_212809.json／docs/S1_LINE_VERDICT_2026-09-25.md` |
| 備註 | 無 hook ⇒ 無 demand fill ⇒ 正確性未證 ⇒ 不能進生產 |
| 軸性質 | 活躍攻關軸：41 段提交的同步／資料搬運，主項可被工程手段消掉 |
| 對應報告 | 7 份 |

- [S1_CAPTURE_ROUND2_20260916_2035.html](../../S1_CAPTURE_ROUND2_20260916_2035.html)
- [S1_DIAGNOSTIC_ABORT_ROOT_CAUSE_2026-09-20.md](../../S1_DIAGNOSTIC_ABORT_ROOT_CAUSE_2026-09-20.md)
- [S1_DIAGNOSTIC_ABORT_ROOT_CAUSE_20260920_0715.html](../../S1_DIAGNOSTIC_ABORT_ROOT_CAUSE_20260920_0715.html)
- [S1_FRONTIER_2026-09-18.md](../../S1_FRONTIER_2026-09-18.md)
- [S1_INSTRUMENT_AND_MEMORY_DELIVERY_20260917_0320.html](../../S1_INSTRUMENT_AND_MEMORY_DELIVERY_20260917_0320.html)
- [S1_TIMING_SEGMENT_20260917.html](../../S1_TIMING_SEGMENT_20260917.html)
- [S1_WINDOW_ANCHOR_AND_LAYER1_20260917.html](../../S1_WINDOW_ANCHOR_AND_LAYER1_20260917.html)

---

← [S1 探針臂：slot table 放 GPU（數值身分）](s1-probe.md)　·　[總目錄](index.md)　·　[HTML 版](s1-segbatch.html)　·　[S1 早期診斷系列（09-16/17） →](s1-refuted.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
