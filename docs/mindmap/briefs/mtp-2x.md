# MTP 到 2× 的邊界（攤薄算術） — 技術白皮書　·　3b ③b 實驗目標達成（可放生產）

> **一句話**：算清「MTP 加速比 2」需要什麼條件（攤薄算術），判斷這個目標值不值得追、缺的是 k 還是別的。

- 主題：MTP／spec　·　子目標：**M MTP on 加速**（活躍攻關軸：speculative 攤薄係數 m 與 accept rate a）
- 階段：生產（可放生產／已交付）　·　① ② ③b —— 已進生產、或已是生產決策依據

---

## 1. 目標

算清「MTP 加速比 2」需要什麼條件（攤薄算術），判斷這個目標值不值得追、缺的是 k 還是別的。

## 2. 判準

S = (1+a·k)/(1+m·k)，k→∞ 上界 = a/m

## 3. 結果

k 加到多大都不到 2×（server a/m=1.445、bench 0.982）；現況每產出 token 成本 45.6~56.4 vs baseline 48.2 ⇒ 攤薄 ≈ 0；2× 需 verify 邊際 42.03 → ≤17.9 ms（−57%）

## 4. 判定

**3b · ③b 實驗目標達成（可放生產）** — 產物已可放進生產級設置：不破壞正確性 ∧ 成本可接受 ∧ 無前置條件

> ★ 產品化結論：『MTP on 加速比 2』不成立 ⇒ 改寫成 m/acc 兩條可攻目標

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
- **逐條處置**：整合進子目標　→ `L25-5`　—　2× 邊界算術（S=(1+a·k)/(1+m·k)，上界 a/m）
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- ★ 09-29 起本格的量測口徑改為 prod-new ＋ harness bench（唯一認可入口），不再是 prod25／server ABBA。判準：m ≤ 0.30 且指名它的主項（draft 流量／verify 寬度／pool 預算）。⚠ 單邊性：窗劣化只把 wall 吹大 ⇒ 實測 t_draft ≥ 真值 ⇒ 由它算出的 m 是上界。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [MTP 儀器化（接進 llama-bench）](mtp-instrument.md) | 3b | 接通（真因＝test_gen_spec 的 n_past 初始化）；此後 MTP 不再跨儀器比較 |
| [MTP 口徑與混淆定位（pool／跨啟動／順序）](mtp-caliper.md) | 3b | 定案：任何單點 MTP 數字不可引用；pool 4 vs 8 GiB 是一階混淆；同 launch 配對 ×1.06、生產 server 路徑 ×0.695（方向相反） |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/MTP_2X_BOUNDARY_2026-09-19.md／docs/MTP_AMORTIZATION_2026-09-25.html` |
| 備註 | ★ 產品化結論：『MTP on 加速比 2』不成立 ⇒ 改寫成 m/acc 兩條可攻目標 |
| 軸性質 | 活躍攻關軸：speculative 攤薄係數 m 與 accept rate a |
| 對應報告 | 2 份 |

- [MTP_2X_BOUNDARY_2026-09-19.md](../../MTP_2X_BOUNDARY_2026-09-19.md)
- [REUSE_DISTANCE_MTPON_CONFIRM_2026-09-20.md](../../REUSE_DISTANCE_MTPON_CONFIRM_2026-09-20.md)

---

← [MTP k-sweep（verify batch T 成本曲線）](mtp-ksweep.md)　·　[總目錄](index.md)　·　[HTML 版](mtp-2x.html)　·　[RSL-MTP（draft top-8 ⊆ 已付費並集）＋ 練 draft head →](mtp-rsl.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
