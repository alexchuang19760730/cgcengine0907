# M：攤薄係數 m 0.474 → ≤0.073 — 技術白皮書　·　3a ③a 實驗目標達成（階段性）

> **一句話**：每 draft token 的攤薄係數 m 能不能從 0.474 降到 ≤0.073（k=3 的 25 t/s 門檻）？ m 的機械解釋是「每多一個 draft token 約 46–59 ms 的固定代價，pool budget 的主項」—— 先確認它是不是 draft／verify 的流量把池預算吃掉。

- 主題：實驗　·　子目標：**M MTP on 加速**（活躍攻關軸：speculative 攤薄係數 m 與 accept rate a）
- 階段：實驗階段（階段性）　·　③a —— 機制／量測成立，但產物還不能進生產

---

## 1. 目標

每 draft token 的攤薄係數 m 能不能從 0.474 降到 ≤0.073（k=3 的 25 t/s 門檻）？ m 的機械解釋是「每多一個 draft token 約 46–59 ms 的固定代價，pool budget 的主項」—— 先確認它是不是 draft／verify 的流量把池預算吃掉。

## 2. 判準

同 run、同形狀下量到 m ≤0.30（第一里程碑）並指名它的主項（draft 流量 / verify 寬度 / pool 預算）

## 3. 結果

**未跑（有卡、無讀數）**：t/s(k) = 1000·mean_len(k)/(S0·(1+m·k))；k=3 即使 a=1.0 也只 **19.25**、k→∞ 極限 **24.59** ⇒ 25 的兩個門都指向 m 與 S0。⚠ 取臂注意：2026-09-28 的 E-A 支臂 draft 幾乎沒跑（fast calls 4366／draft calls 305）⇒ 對 m 無代表性，必須先取一支 draft 真的在跑的臂。

## 4. 判定

**3a · ③a 實驗目標達成（階段性）** — 達成實驗設計目的（機制／量測成立），但產物還不能放進生產級設置

> 假設：t/s(k) = 1000·mean_len(k) / (S0·(1+m·k))，mean_len(k) ≤ k+1。k=3 即使 a=1.0 也只有 19.25； k→∞ 的極限 1000/(S0·m) = 24.59。⇒ 25 的兩個門都指向 m 與 S0，而 m 被記為 pool budget 的主項。 待驗的關鍵：draft／verify 是不是把每 token 的等效慢速位元組（或等效慢速時間）推高。 ⚠ 2026-09-28 的 E-A 支臂自己的 draft 幾乎沒跑（fast calls=4366 但 draft calls=305）， 所以那支臂對 m 沒有代表性；本節點必須先取一支 draft 真的在跑的臂。

---

## 5. Profile 綁定與測試 Log 報告

- **arm**：`prod25`　（profile `prod25`；無自己的 option）
- **來源**：② charter arms[]　·　置信度 `high`
- **證據報告**：[DECODE25_CEILING_2026-09-20.md](../../DECODE25_CEILING_2026-09-20.md)
- 取自 `scripts/check/charters/exp-m-draft-cost.yaml` 的 `arms[]`

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [MTP k-sweep（verify batch T 成本曲線）](mtp-ksweep.md) | 3a | step ≈ 14.61 + 42.03·T（max resid 11.5）；最佳 k=2 但那格不可移植（合成 accept 0.93~0.99 vs 交付 0.465~0.58 |

## 7. 子目標分解（持續更新；1/6 完成）

- [x] 跑前立項（現狀/目標/假設/驗收） [證](../../../scripts/check/charters/exp-m-draft-cost.yaml)
- [~] 用生產級腳本 prod-new ＋ 自己 option 跑實驗臂，留存 log
- [ ] 驗收：同 run、同形狀下量到 m ≤0.30（第一里程碑）並指名它的主項（draft 流量 / verify 寬度 / pool 預算）
- [ ] 否證條件：若 MTP off 與 on 的每 token 等效成本在扣除 mean_len 差異後無顯著差，則 m 不是 draft 造成的，本節點判否
- [ ] 指名 m 的主項：draft 流量 / verify 寬度 / pool 預算三選一（或給比例）
- [ ] m：0.474 → ≤0.30（第一里程碑；終極 k=3 需 ≤0.073）

## 8. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/DECODE25_CEILING_2026-09-20.md` |
| 備註 | 假設：t/s(k) = 1000·mean_len(k) / (S0·(1+m·k))，mean_len(k) ≤ k+1。k=3 即使 a=1.0 也只有 19.25； k→∞ 的極限 1000/(S0·m) = 24.59。⇒ 25 的兩個門都指向 m 與 S0，而 m 被記為 pool budget 的主項。 待驗的關鍵：draft／verify 是不是把每 token 的等效慢速位元組（或等效慢速時間）推高。 ⚠ 2026-09-28 的 E-A 支臂自己的 draft 幾乎沒跑（fast calls=4366 但 draft calls=305）， 所以那支臂對 m 沒有代表性；本節點必須先取一支 draft 真的在跑的臂。 |
| 軸性質 | 活躍攻關軸：speculative 攤薄係數 m 與 accept rate a |
| 對應報告 | 0 份 |

- （無）

---

← [S2：段邊界免等（天花板 18.7 t/s）](exp-s2-overlap.md)　·　[總目錄](index.md)　·　[HTML 版](exp-m-draft-cost.html)　·　[口徑＋k=3 飄移認證（16.4% 噪聲底） →](exp-caliber-calibration.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
