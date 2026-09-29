# ① 攻關成功（pp≥250 ∧ tg>12.57） — 技術白皮書　·　1 ① 攻關成功

> **一句話**：同一次量測裡同時達成 prefill ≥250 與 decode >12.57 t/s——只有這樣才算「① 攻關成功」。

- 主題：交付里程碑　·　子目標：**不適用**（非攻關軸：約束／帳本／上界算術／入口索引／跨線產品）
- 階段：生產（可放生產／已交付）　·　① ② ③b —— 已進生產、或已是生產決策依據

---

## 1. 目標

同一次量測裡同時達成 prefill ≥250 與 decode >12.57 t/s——只有這樣才算「① 攻關成功」。

## 2. 判準

兩件事同時成立

## 3. 結果

⛔ 空 —— 最接近的一次是同 cell pp **260.41** ＋ tg **12.195**（差 3%）。

## 4. 判定

**1 · ① 攻關成功** — prefill ≥ 250 ∧ decode 比目前最好還要好（目前最好＝交付錨點 12.57）

> 目前無任何一格是 ①。結案規則（09-29）下它需要 **prod-new ＋ harness bench** 的同場成對（pp 與 tg 同 cell），而現有最接近的一場是不同日比較 ⇒ 仍未結案。 **§50（09-29）**：decode ≥25 那一側的判詞已由「判死」改記「**未判定**」（margin 0.6–2.2 ms，邊界是 B 半補丁）⇒ ① 仍然空著，但它現在等的是**一個乾淨窗口**加上那一支補丁，而不是「新的軸」。

---

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod-new:CGC_LAYER_AHEAD_PREFETCH=1`　（profile `prod-new`；被測 option：`CGC_LAYER_AHEAD_PREFETCH=1`）
- **來源**：③ 證據文件掃描　·　置信度 `med`
- **測試 log**：[nf2_fill.json](../../../Backup/nofill_prod/nf2_fill.json)　[nf_fill.json](../../../Backup/nofill_prod/nf_fill.json)　[mw_ctrl.json](../../../Backup/mw_ab/mw_ctrl.json)
- **證據報告**：[MILESTONE_MAP_RECHECK_2026-09-25.md](../../MILESTONE_MAP_RECHECK_2026-09-25.md)
- 從證據文件掃到的 arm 字串（1 份文件）

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [prefill ≥ 250（交付 cell）](m-prefill250.md) | 2 | **已認證（C1）**：9 次 launch ≥250，最高 **296.24**；乾淨視窗 **283.01**；同期 decode 11.49~12.20。（結案規則下，這是本 |
| [swap 結構修復（L0–L4 + P0/P1/P2）](sys-swap.md) | 3b | launch swap 0、decode 11.49、thermal NOMINAL（commit efba7c1d5） |
| [server 窗口／box 准入（單一來源閘門）](sys-window.md) | 3b | BOX_ADMISSION_SINGLE_SOURCE 定為單一來源；SERVER_WINDOW_LEDGER 記錄逐次窗口。**09-29 21:29 實測：`admits=Fa |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/MILESTONE_MAP_RECHECK_2026-09-25.md` |
| 備註 | 目前無任何一格是 ①。結案規則（09-29）下它需要 **prod-new ＋ harness bench** 的同場成對（pp 與 tg 同 cell），而現有最接近的一場是不同日比較 ⇒ 仍未結案。 **§50（09-29）**：decode ≥25 那一側的判詞已由「判死」改記「**未判定**」（margin 0.6–2.2 ms，邊界是 B 半補丁）⇒ ① 仍然空著，但它現在等的是**一個乾淨窗口**加上那一支補丁，而不是「新的軸」。 |
| 軸性質 | 非攻關軸：約束／帳本／上界算術／入口索引／跨線產品 |
| 對應報告 | 0 份 |

- （無）

---

← [prefill ≥ 250（交付 cell）](m-prefill250.md)　·　[總目錄](index.md)　·　[HTML 版](m-total.html)　·　[decode ≥ 25（M-25） →](m-decode25.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
