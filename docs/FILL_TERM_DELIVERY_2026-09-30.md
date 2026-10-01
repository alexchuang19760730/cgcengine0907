# 交付 cell 上 fill term 的定價（L20-4 結案）

日期：2026-09-30（跑於 03:21–03:22）　立項卡：`scripts/check/charters/e-fill-term-price-2026-09-29.yaml`
端點檢查器：`scripts/check/fill_term_ab.py`（自測 21/21，七條結構閘 fail-closed）
產物：`Backup/fill_term_delivery_2026-09-30/ab.json` ＋ 兩份成對 stderr

## 1. 這一格在問什麼

立項卡的原句：**進 `48.2 + x` 的那一個 x，是 1.60、3.955、5.41 還是 8.3 ms/step？**
驗收要的是「在**交付 cell** 上指名一個數字 ＋ 不確定度，並列出其餘三個各是什麼口徑」。

## 2. 跑了什麼

同一趟 launch、同一個交付 cell（`-b 512 -ub 512 -p 0 -n 128 -d 512 -r 3 --warm-skip 64
--fixed-fill-seed 1`，contract 13 維 strict 全過、`cell: delivery`），兩臂只差在環境：

| 臂 | env | 角色 |
|---|---|---|
| A | `prod-new:CGC_EB_TIMER=1` | fill 全開；EBTIMER 是儀器 |
| B | `prod-new:CGC_EB_TIMER=1;CGC_EB_NOFILL=1` | 同形狀、**不讀位元組** ⇒ fill 的底（TIMING ONLY，輸出是垃圾） |

窗口：兩臂**都是 `attribution=none`**（`swap_growth=0.0 MiB`、thermal `NOMINAL`）。
兩臂 **dencode 窗口數 385 / 385（＝3 rep × 128 步 ＋ 儀器最後一次 flush）** ⇒ 步數相同，
配對是合法的。miss 數 4300（compulsory 3670）vs 3793（compulsory 3340）。

## 3. 數字

| 量 | A（`prod-new:CGC_EB_TIMER=1`） | B（`+CGC_EB_NOFILL=1`） |
|---|---:|---:|
| tg t/s（逐 rep） | 10.773（9.505 / 11.325 / 11.489） | 13.733（13.467 / 14.004 / 13.728） |
| 步時 | **92.83 ms/token** | 72.82 ms/token |
| fill term（EBTIMER，被計時的 64 步窗口 p50，逐 rep） | **9.93 / 4.91 / 5.40 → 平均 6.79 ms/step** | 0.067 / 0.060 / 0.057 → 0.061 ms/step |
| io_bytes | 4864417792（4.53 GiB，有效 13.0 MiB/s） | **0** |
| wall | 50.2 s | 39.9 s |

### 兩個口徑（這就是「一個數字」的真正答案）

- **口徑① 儀器（EBTIMER，包住整個 `ensure_batch`）＝ 6.79 ms/step**
  逐 rep 9.93 / 4.91 / 5.40 ⇒ **CV 33.6%**；只取穩態的後 2 rep ⇒ **5.18 ms/step、CV 4.1%**。
  配對 B 的底 0.061 ⇒ **可移除 6.73 ms/step**（⇒ 幾乎全部 fill 成本都在「讀位元組」那一支，
  不在鎖／assignment）。
- **口徑② 牆鐘（逐 rep 配對 `1000/ts_A − 1000/ts_B`）＝ 20.68 ms/token**
  逐 rep 30.96 / 16.89 / 14.19 ⇒ **CV 35.5%**。這是「不讀位元組之後牆鐘省掉的全部」，
  所以是**上界**：它含讀取路徑造成的所有停頓，不只 EBTIMER 包得住的那一段。

⇒ **在交付 cell 上，這個 term 的價格是 [`6.79`, `20.68`] ms/step ＝ 92.8 ms 步時的 7.3–22.3%。**
兩個口徑差 **3.0×**，方向與 09-25 已記過的那個不一致相同
（`docs/S1_SHAPE1_WAIT_BUDGET_2026-09-25.md`：同一步裡「儀器欄位只看得到 1.86 ms」而
EBTIMER 報 20.8）⇒ 這一格**不是收斂成單點，是收斂成區間**，而立項卡的 `falsify` 正是
「收斂不了 ⇒ 20+ 天花板維持為區間」，所以天花板**維持區間**，不得只用有利的那一端。

### 其餘四個數字各是什麼口徑（驗收的第二半）

| 舊數字 | 口徑 | 為什麼不能進算式 |
|---|---|---|
| 1.60–3.20 ms | **替代方案的 op 成本**（B 半的逐位元補算），不是本 build 上的 on-path 成本 | 它量的是另一條路的操作數，從來不是這條路上的項 |
| 3.955 ms | **同一支儀器、但不同 cell**（`Backup/phase_decomp/fillbudget_m/p1/p1.json` 的 `named_cell` 是 **`(default)`**、batch 5632／p2048；文件曾把它寫成「交付 cell」是口徑誤標，§68 已更正） | 跨 cell 不可比（§56）。交付 cell 上的同位數字是今晚的 5.2（穩態）／6.8（整跑） |
| 5.41 ms | 同一份 `(default)` 產物的**誠實臂 p50** | 同上：不是交付 cell |
| 8.3 ms | **IO 反解**（由 io 量回推的導出值） | 導出值不是量測值；今晚有直接量（6.79／20.68） |

### 順帶查核：算式的地基自己也是跨 cell 的

`48.2 ms` 這一位數出自 `Backup/seg_batch_s1_pairs/abba_212809.json`（B 單段四列的**中位數**
20.73 t/s —— **不是任何一列的讀數**；該臂兩端已作廢（B 臂 `attribution=thermal` HEAVY、
`max_llama_procs=3`）⇒ 這個數**不可引用**），
而那份產物的 cell 是 **`prompt 2048 / gen 128 / batch 5632`** ⇒ **不是交付 cell**。
所以 `48.2 + x` 是把**別格**的步時加上**本格**的價格的混合口徑式；本格自己的步時是
92.83 ms。⇒ 本格能合法交付的是**價格**（區間），不是「48.2 + x 等於幾 t/s」。

## 4. 判詞

- **定價：PRICED**（`fill_term_ab.py`，七條結構閘全過）。價格 ＝ **6.79–20.68 ms/step**
  ＝ 交付 cell 步時的 **7.3–22.3%**。**光是為 fill 定價買不到 20+**（20+ 要步時砍到 ≤50 ms
  ＝ 要砍掉 46%）。
- **子目標 `sg-fill-price`（CV ≤ 20%）：未達**（整跑口徑 33.6%／35.5%）。穩態口徑 4.1% 達標，
  但那是**換了窗口**才達標 ⇒ 依 `on_fail`：天花板維持區間。
- **子目標 `sg-fill-pick`：達成**（一個 term、兩個具名口徑、其餘四個各被指名）。
- ⚠ B 臂的 13.733 t/s **不是能力讀數**（輸出是垃圾）；這裡只用它的**差**。

## 5. 保留

- 單次成對、**A 先跑 B 後跑**（非 ABBA）⇒ 次序未分離；A 讀了 4.53 GiB 會把 page cache 弄熱，
  那一項對 B 有利還是無利沒有分離。
- 三場連跑裡**只有約 2/3 的 rep 落在穩態**（冷頭 9.93 vs 穩態 5.18），CV 由冷頭主導。
- `step_ms` 用的是 `avg_ts`；逐 rep 的步時散得比 `avg_ts` 開（9.5 → 11.5 t/s）。

## 6. 這一節改了看板上什麼

`L20-4`：`未結案（可跑）` → **結案**（`settled`），端點＝`fill_term_ms`／`PRICED`（D7b 當場重跑），
`precondition` 由 `needed` 改 `duplicate`（判決已存在 ⇒ 不必再跑）；前綴掃描因此
**`needed 0 / duplicate 9 / blocked 7`**。
