# 交付 anchor 12.57 t/s 為什麼今天重不現（2026-09-28）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **12.57／12.62 t/s 屬 MTP-on 輸出**，是**另一個輸出函數**，
> **不可與 MTP-off 互比**（含「MTP 加速 X%」「MTP-on ≥ MTP-off」「以 12.57 為基線／錨點／要打敗的數字」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**。

**判定：`12.57` 是這條口徑分布的尾端抽樣，不是退化。** 今天用 anchor 自己的協定重跑七次，
**七次裡有兩次達到或超過它**（`16.38`、`12.95`），七次的中位數 `9.34`。

---

## 1. 直接量測：anchor 的原協定，今天七次

協定逐項與 anchor 相同：`--cell delivery`（`reps: 3`、單一程序內的 `-r 3`）、
arm `prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256`（＝ registry `prod25-stream`）、
`--prompt 0 --gen 128 --depths 512 --batch 512 --ctx-size 4096 --warm-skip 64
--spec-type draft-mtp`、cell 合約 **14/14 一致**、每次啟動 `NOMINAL`。

| # | 三次 rep 的 t/s | 均值 | 對 anchor 12.57 |
|---|---|---|---|
| 1 | 8.52 / 9.56 / 8.14 | 8.74 | |
| 2 | 11.08 / 6.34 / 8.87 | 8.76 | |
| 3 | 27.25 / 7.82 / 14.06 | **16.38** | **超過** |
| 4 | 10.46 / 7.92 / 8.40 | 8.93 | |
| 5 | 14.76 / 12.19 / 11.92 | **12.95** | **超過** |
| 6 | 9.15 / 9.92 / 9.34 | 9.47 | |
| 7 | 9.75 / 10.34 / 7.94 | 9.34 | |

```
排序：8.74  8.76  8.93  9.34  9.47  12.95  16.38
                       ↑ 12.57 落在第 5 與第 6 之間 ⇒ 約第 78 百分位
七次 mean 10.65   中位 9.34
```

**2/7 的 launch 達到或超過 anchor。** 它不是一個摸不到的數，它是這條分布右尾的一個點。
（第 5 次有完整的 draft 證據：`timed rounds=22 mean_len=3.0909 ratio=0.836`／`29 / 2.4828`／
`37 / 2.1622`，三次都活著。）

## 2. 被排除的解釋

| 假設 | 怎麼測 | 結果 |
|---|---|---|
| **協定不同**（anchor `-r 3` 一個程序；今天 rep-split 一 rep 一程序） | 用 anchor 的原協定（`--cell delivery --reps 3`）今天直接跑 | 得到的均值 **8.74**，**比 rep-split 的 9.74 還低** ⇒ 協定不是原因 |
| **rep 1 被丟掉造成上偏**（`prod_matrix.platform_of`：「Drop rep 1 … this is the number the standard quotes」，`n_kept=2`） | 查 `prod_profile.py:220`：記錄的 `t/s` 是 `r["avg_ts"]`＝**三個 rep 的平均**，不是 platform | 12.57 是三次的平均，沒有修剪 ⇒ 不成立 |
| **儀器旋鈕** | anchor 的 `extra_env` 無 `DECODE_PROFILE`／`GPU_TIMING`／`SEG_BATCH` 等 | 無旋鈕，與今天同口徑 |
| **盒子污染** | anchor 自己的產物：`thermal launch NOMINAL, worst NOMINAL, hist {'NOMINAL': 95}` | 乾淨 |
| **引擎退化** | 無法排除（見 §3） | **未排除** |

## 3. anchor 自己的產物就帶著這個散布

`Backup/prod_profile/prod_profile_20260920_1230.json` 同一次執行裡有**兩個 decode 軸、同一格 cell**：

| axis | arm | t/s | ± | thermal | verdict |
|---|---|---|---|---|---|
| `decode-delivery` | `prefill250` | **9.90** | ±1.25 | launch NOMINAL, worst MODERATE | ❌ 不達 bar |
| `decode-delivery-anchor` | `prod25-stream` | **12.57** | ±2.26 | 全 NOMINAL | ✅ |

⇒ **2026-09-20 當天，同一格 cell 在三分鐘內的兩支臂差了 27%**，而 12.57 自己帶 ±2.26（18%）。
這條口徑從第一天起就是這個散布；`12.57` 是其中被挑出來當標準的那一個點。

## 4. 機制：t/s 是兩個獨立因子的乘積

```
t/s = 64 tokens ÷ (rounds × ms/round)
```
`rounds` 由 draft 接受鏈決定（今天同一支臂的 `mean_len` 落在 1.65–3.09 ⇒ `rounds` 22–51），
`ms/round` 是機器當下的每回合成本（今天量到 138–248 ms）。兩者相關 **−0.048**（獨立），
相乘後散布 21%。今天最高那次（run 3 的 27.25 t/s）就是兩者同時抽到極端。

## 5. 這件事真正的代價：**閘門本身就是一個尾端值**

`MEASUREMENT_CONTRACT` §5：① 攻關 ＝ `prefill ≥ 250 ∧ decode > 目前最好（12.57）`，
而 anchor 那次執行自帶的 bar 是 `12.0`。以今天量到的分布（中位 9.34、mean 10.65）：

- `12.0` 約在第 78 百分位 ⇒ **「攻關成功」有約 1/4 的成分是抽到一次好 launch**
- 這解釋了為什麼 ① 從 2026-09-25 掛到現在**一直是空的**

**建議（不是這一輪改的）**：把交付 baseline 從「一個點」改成「**這條分布的 median ＋ q90**」，
並且量測端一律用多次啟動的分布，而不是單點比大小。單點比大小在 sd 21% 的量上沒有鑑別力。

## 6. 這一輪的缺陷（我自己的）

- **共用 workdir 把證據覆蓋掉了。** 我前四次重跑沒帶 `--workdir`，於是每次的
  `llama-bench ... .stderr.log` 都落在 `/tmp/harness_bench` 的同一個檔名上，後一次蓋掉前一次
  ⇒ run 3 的 `16.38`（含那筆 `27.25`）**沒有留下 draft 證據**，只剩 JSON 裡的逐 rep 數字。
  這正是 `k3_pair_cert.sh` 檔頭記過的同一個缺陷（r1.a, 2026-09-28），而 rep-split 驅動已用
  逐臂 workdir 修掉 —— 我這次是單跑 `harness bench`，所以又踩了一次。後三次已帶 `--workdir`。
- `llama_bench_matrix.py` 直跑回 **rc=2**（「這支是 internal 驅動…請走 harness.py bench」）——
  那是它該有的行為，不是缺陷；我第一版就是直跑而撞上它。
- 我先前寫進 `k3_pair_cert.sh` 檔頭的「第一個 measured launch 系統性偏低（冷啟）」與
  「rep1 被丟掉造成上偏」兩個假設，**這一輪都被自己的量測否證**（第 1 節第 3 行）。

## 7. 仍未回答

- **build 279 → 630 之間有沒有退化**：今天所有數字都在 `12.57` 的分布內，所以就算有退化，它也
  被埋在比它更大的散布裡。要真的排除需要**分布對分布**（每邊 20+ launch），不是單點比單點。
- anchor 的原始逐 rep 三個數字沒有在任何產物裡（`prod_profile` 只留 mean±sd；逐 rep 的
  pass-through 是 **2026-09-28** 才加進去的）⇒ 12.57 那三次各自是多少**已永久遺失**。

## 8. 產物

```
/tmp/anchor_repro/r3.json, r3_{2..7}.json    七次 anchor 協定的重跑（逐 rep samples 在 JSON 裡）
/tmp/anchor_repro/wd{5,6,7}/                後三次的 llama-bench 原始 log（含 ACCEPT）← 只有這三次沒被覆蓋
/tmp/anchor_repro_{2..7}.log                harness stdout
Backup/prod_profile/prod_profile_20260920_1230.json   anchor 的原始產物
```
