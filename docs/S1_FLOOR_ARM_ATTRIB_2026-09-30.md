# 不用 `CGC_SEG_BATCH` 也能量到 S1 的 step 底線嗎？（2026-09-30，L20-1 的臂類別解鎖）

**答案：不行。** 現有旗標裡**沒有一支乾淨臂**能取代那支 R6 臂量到 36–49 ms 的 step 底線 ——
而且這一輪順手把「為什麼不行」量成了機制：**S1 的紅利不是 hook 的 CPU，是「提交次數」**。

## 為什麼要問這題

L20-1 的底（`36.33 ms` ＝ 27.526 t/s）經機檢是 **R5 乾淨、R6-DIRTY**（唯一規則 `CGC_SEG_BATCH`），
而 R6 的解除條件（輸出見證）在 2026-09-30 跑過並**否證**（`M1 0/9` vs 對照臂 `9/9`，
見 `Backup/m123_oracle_gate/summary_r6-segbatch-2026-09-30.json`）⇒ 那一格不會靠乾淨窗口脫困。
唯一出路是**換一支不在登記表裡的臂**，或證明紅利與那面旗標不可分離。這份文件就是後者的量測。

## 方法（可重現）

同一場、同一 cell、prod-new ＋ `harness bench`，五臂序列（MTP off、`--fixed-fill-seed 1`、reps 3）：

```
python3 scripts/check/harness.py bench \
  --arm 'prod-new' \
  --arm 'prod-new:CGC_SLOT_TABLE_GPU=1' \
  --arm 'prod-new:CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1' \
  --arm 'prod-new:CGC_HOOK_SPLIT=1' \
  --arm 'prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1' \
  --prompt 2048 --gen 128 --depths 512 --reps 3 --warm-skip 64 --fixed-fill-seed 1 --batch 5632 \
  --charter scripts/check/charters/e-s1-flag-attrib-2026-09-30.yaml \
  --json Backup/l201_flagattrib_2026-09-30/ablation3.json
```

## 結果（五臂同場）

| 臂 | tg t/s | ms/step | R5／R6（機檢） | 窗口 |
|---|---:|---:|---|---|
| `prod-new`（控制） | 10.07 ± 0.45 | 99.3 | 乾淨 | both（HEAVY＋swap） |
| `+CGC_SLOT_TABLE_GPU=1` | 8.47 ± 3.87 | 118.1 | 乾淨 | thermal |
| `+CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`（＝S1 減掉 R6 那面） | 11.00 ± 0.40 | 90.9 | 乾淨 | both |
| `+CGC_HOOK_SPLIT=1` | 10.89 ± 1.13 | 91.8 | 乾淨 | thermal |
| **`+CGC_SEG_BATCH=1;…`（S1，R6）** | **20.39 ± 1.04** | **49.0** | **R6-DIRTY** | both |

⚠ **五臂全部 DIRTY**（`thermal=HEAVY`；兩臂還有 swap 成長）⇒ 這些數字**不可引用**，只能當**診斷級歸因**。
（同一顆種子在 09-29 的冷場是 11.327 → 27.526 t/s ＝ 88.3 → 36.3 ms；本場整體慢約 12%，
但**臂之間的落差一樣大**，所以歸因結論不受窗口影響。）

## 兩個結論

**(1) 換臂不可行（這是「設計一支乾淨臂」的答案：不存在）。**
四支乾淨臂全部落在 **91–118 ms/step**（互相差距在窗口噪音內：控制臂自己一場內 ±0.45 t/s ≈ ±4 ms），
而 S1 是 **49.0 ms**。關鍵那一格是 `CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1` —— 它**就是** S1 拿掉
R6 旗標之後的樣子：**紅利一毛都不剩**（90.9 ms，比控制還慢一點）。⇒ S1 的底**與 `CGC_SEG_BATCH`
不可分離**，L20-1 用今天的引擎與旗標集合**無法**換臂脫困。

**(2) 機制被推翻，而且方向換了。**
`CGC_HOOK_SPLIT=1`（乾淨臂，R5 登記表未列）量到 hook 自己的成本：

```
CGC-HOOKSPLIT: n=15360  pre=5.5  ensure=156.4  drain=0.3  tail=0.5 us/call (total 162.6)
⇒ 162.6 µs × 40 層/步 = 6.5 ms/step
```

而觀測到的紅利是 **50.3 ms/step**（09-29 冷場 52.0 ms）⇒ **hook 的 CPU 只解釋約 13%**。

> ⇒ 也就是說：**「S1 快是因為它跳過 per-layer hook」是錯的**（至少不是主因）。
> 紅利的主體是**提交次數**：41 段提交 → 1 次提交，每多一次提交約 **1.1–1.25 ms**（50 ms ÷ 40），
> 那是同步／GPU 空轉的成本，不是 CPU 的。這一條也回頭解釋了源碼那句
> 「the GPU's idle time tracks it (gap ≈ 1.3 × (cb + submit))」——**主項是 submit，不是 cb**。

## 對 L20-1 的處置（兩條，都需要人決定）

- **(a) 引擎：把「單段提交」做成輸出可驗的版本**（源碼自己寫的下一版：`prev-token prediction +
  per-layer recompute of mismatched layers`）。驗收現成：配對 `M1/M2/M3` 回到 `9/9`（量法與基準都已存在，
  今天跑過一次）。這條一綠，不只 L20-1，**整個 ≥20 家族**（P1／SPAC 那支 26–27.3）同時解鎖，
  因為它們都掛在同一面旗標上。
- **(b) 改端點：把這一格從「時間讀數」改成「機制／計數器端點」**（例：每步提交數 41 → 1、或
  `ms/submit` 的比值）。代價是它**回答不了**原本那個「36.80 vs 49.18 ms 哪個才是底」的問題 ——
  那個問題本質上是時間問題。若走這條，L20 的天花板就應該繼續採**較嚴的底 19.2**。

在 (a) 完成之前，**S1 的 36.33／49.0 ms 只能當診斷數字**（永不可引用），
L20 層的天花板算式**不得**採用 24.5 那一側。

## 附帶：`CGC_B_SCHEME` 的登記表洞 —— 查了，而且**結案**

`CGC_B_SCHEME=1` 的源碼註解自己寫著「**diagnostic arm: routing is wrong, output is garbage,
never a deliverable**」，但它**不在** `quote_gate` 的 R5／R6 任何一張登記表裡 ⇒
在那之前，一支帶它的臂在閘門眼裡是**乾淨的**。這一輪把它查清楚了：

| 量什麼 | 結果 | 讀法 |
|---|---|---|
| 速度（五臂同場） | `CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1` ＝ **90.9 ms/step** vs 控制 **99.3** | 沒有紅利會被它偷走（差在窗口噪音內） |
| 數值（配對 M1） | 對照臂 **9/9** vs `--env CGC_B_SCHEME=1` **9/9**（答案均「42」、覆蓋率 100%） | **hook 在時佔位被覆寫，routing 是對的** |

```
python3 scripts/check/m123_oracle_gate.py --profile prefill250 \
    --tag r6-bscheme-2026-09-30 --env CGC_B_SCHEME=1 --allow-incomparable
```

⇒ **裁決：不進 R6**（沒有理由）。源碼那句警告的適用範圍是**單段提交的組合**（hook 被跳過時），
不是這面旗標本身。（要改那句話成「僅在 `CGC_SEG_BATCH` 之下」就得動 `src/`，依 repo 自己對引擎
改動的規則（D5）要再跑一次 oracle ⇒ 先留在登記表的註解⑦ 與本文件裡。）
該趟閘門自己標 **INVALID COMPARISON**（跨組態不得當判詞）—— 那正是這個測試的形式，
配對結論來自「同日、同 build 的對照臂 9/9」（`summary_r6ctl-nosegbatch-2026-09-30.json`）。
產物：`Backup/m123_oracle_gate/summary_r6-bscheme-2026-09-30.json`。

## 產物

- 五臂產物：`Backup/l201_flagattrib_2026-09-30/ablation3.json`（＋ `.logs/live/*.stderr.log`）
- hook 成本：`…/live/prod-new_CGC_HOOK_SPLIT_1.p2048_n128_d512_r3.stderr.log`（`CGC-HOOKSPLIT:` 行）
- 立項卡：`scripts/check/charters/e-s1-flag-attrib-2026-09-30.yaml`
- 前一輪（被打斷的兩場，同結論方向）：`…/harness.log`、`harness2.log`、`harness3.log`
