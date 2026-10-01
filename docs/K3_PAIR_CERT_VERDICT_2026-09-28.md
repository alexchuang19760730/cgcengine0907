# k=2 vs k=3 配對認證：判決（2026-09-28）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **12.57／12.62 t/s 屬 MTP-on 輸出**，是**另一個輸出函數**，
> **不可與 MTP-off 互比**（含「MTP 加速 X%」「MTP-on ≥ MTP-off」「以 12.57 為基線／錨點／要打敗的數字」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**。

**判決：NOT SEPARATED。** 在測試卡 §2.5.1 的 `delivery` cell 上，k=2 與 k=3 的配對差是
**+0.54 t/s（+5.5%）**，n=12 對、sd 1.96、SE 0.57、**t=0.95 < crit 2.20（df=11, α=0.05）**。

這不是「跑得不夠」。這一輪的 N=12 是**看見任何判決之前**依 charter §3 寫死的
（`N = min(12, max(8, n_needed_at_point_sd))`，階段 1 量到 19 ⇒ N=12），跑滿之後只做**一次**
檢定。charter 的 falsify 分支寫著「跑滿 N 對而 |t| < crit ⇒ NOT SEPARATED。**不再加對**
（optional stopping），改走機制路線」——所以下一步是機制，不是更多的對。

---

## 1. 設計與它為什麼改成這樣

| | |
|---|---|
| 問題 | 在**權威 cell** 上 k=2 與 k=3 是否可分離 |
| cell | `delivery-repsplit`（測試卡 §2.5.1 的孿生 cell：reps:1 ＋ `rep_split{of:delivery, launches:3, cool_to:NOMINAL}`） |
| 臂 | `prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256` |
| 形狀 | `--prompt 0 --batch 512 --ctx-size 4096 --warm-skip 64 --fixed-fill-seed 0 --spec-type draft-mtp`（k=2 加 `--spec-draft-n-max 2`） |
| 交錯 | 每對 ABBA（r 奇數 a=k2，偶數 a=k3） |
| 每支臂 | 3 次**獨立的程序啟動**、啟動之間冷卻到 NOMINAL、每次啟動恰好 1 個 measured rep |

**為什麼一個 rep 一次啟動**：同一次 `-r 3` 啟動裡的三個 rep 不可交換——`rep2/3` 的 draft 鏈
可能整個死掉、量到純解碼，而臂仍被讀成 k=3（`docs/MTP_DRAFT_FIRST_STEP_2026-09-28.md`）。
`ARM_DRAFT_LIVENESS_2026-09-28.md` 記錄 corpus 裡唯一沒出現過死鏈的 k 結論正是「一個 rep 一次量」。
`reps` 沒有被放寬：卡片上宣告的是孿生 cell，未宣告的 `reps=1` 會被 `cell_contract` 拒。

## 2. 閘門證據：72 次啟動全部乾淨

```
測量啟動總數            72   （12 對 × 2 臂 × 3 次啟動）
rc != 0                 0
k 讀回不符              0     （k=2 的臂全部讀回 2；llama-bench 預設是 3，所以這是真的讀回）
起跑記憶體閘 ok         72/72  （available ≥ 4000 MiB 的 steady 讀值）
跑後記憶體判詞 ok       72/72
draft 鏈活著            72/72  （每一個 measured launch，不只是「臂沒有炸」）
啟動後 thermal          59 NOMINAL / 11 MODERATE / 2 HEAVY（每次啟動**前**都要 NOMINAL，這是啟動後的殘熱）
```

`k3_pair_cert.sh` 是 fail-closed 的：任一臂 rc≠0、k 讀不回來、或產不出可用的 decode row，
就整段中止而不是留半支臂。這一輪**沒有觸發過任何一次**中止。

## 3. 階段 1：四對 pilot（只估 sd，不判）

| | k2 | k3 |
|---|---|---|
| within-arm sd（一個 request） | 20.62% | 19.12% |
| launch 級 sd | 10.57%（F=1.79, df=(3,8)） | 0.00%（F=0.40） |
| 判詞 | 未偵測到 launch 級項 | 未偵測到 |

配對差：`r1 −0.80 / r2 −0.97 / r3 +2.67 / r4 +3.72 t/s` ⇒ **sd 2.39 t/s**（95% 上界 6.99）。
規劃差 +1.16 t/s 是**觀察值，只拿來定 n，不是發現**。⇒ `n_needed` 點估 19、上限 >40
⇒ 依 §3 登記 **N = 12**（登記寫在 `charters/exp-k3-pair-cert.yaml`，跑第 5 對之前）。

## 4. 階段 2：登記的 12 對，一次判決

| | k2 | k3 |
|---|---|---|
| 臂數 | 12 | 12 |
| grand mean | **10.28 t/s** | **9.74 t/s** |
| within-arm sd | 19.51% | 15.80% |
| 臂均值散布 | 14.18%（其中 11.26% 只是 request 噪音） | 9.12% |
| launch 級 sd | **8.61%**（F=1.58, df=(11,24), 95% 上界 19.94%） | **0.00%**（F=0.87, 95% 上界 10.24%） |
| 判詞 | 未偵測到 launch 級項（1.58 < 2.22） | 未偵測到 |

```
r1  −0.80   r2  −0.97   r3  +2.67   r4  +3.72   r5  −0.01   r6  −3.25
r7  +0.52   r8  +0.96   r9  +3.34   r10 +0.21   r11 +0.37   r12 −0.31
n=12  mean diff +0.54 t/s (+5.5%)  sd 1.96  SE 0.57  t=0.95  crit=2.20 (df=11)
k3 slower in 7/12 pairs
⇒ NOT SEPARATED
```

**方向與 charter 的先驗一致，量級比歷史小。** 歷史是 +1.71 t/s（+16.4%，n=5，在
**server 口徑**上，`K3_PAIR_CERT_V2_BENCH_2026-09-23.md`）。charter 的 `hypothesis` 事先寫著：
若單邊慢狀態只是 server 口徑的現象，「在 12.57 那個交付基線較高的 cell 上，可被 k=2 救回來的
那一段會比較短 ⇒ 配對差變小甚至消失。**兩個方向都只是先驗。**」——量到的就是這一支。

## 5. 機制路線：變異不是 k，也不是熱，是兩個獨立因子

這是這一輪**真正的新資訊**，也是「改走機制路線」的第一份材料。判決顯示 k 的效應藏在
一個比它大的散布裡；把那個散布拆開：

```
t/s = 64 tokens ÷ (rounds × ms/round)

rounds      23 – 49         sd/mean 16%
ms/round    138 – 248 ms    sd/mean 13%
相乘                        sd/mean 21%

corr(rounds, ms/round) = −0.048   ⇒ 兩個因子獨立（不是同一個狀態的兩面）
corr(t/s, rounds)      = −0.816
corr(t/s, ms/round)    = −0.485
```

**`rounds` 是 draft 接受鏈的運氣，`ms/round` 是機器當下的每回合成本。17.67 t/s 那一次是
兩者同時抽到極端（26 回合 × 139.3 ms）。**

### 5.1 那個 17.67 是「真」的但不是可重現的

`r4_b_k2` 的第一次啟動：`avg_ns 3622086125`、`n_gen 64` ⇒ 17.6694 t/s，`-r 1` 所以 `stddev 0`
是必然；引擎自己的 `ACCEPT phase=timed rounds=26 draft_ratio=0.84` 與它一致；k 讀回 = 2。
**儀器沒有故障、沒有截窗、沒有貼錯 cell。**

但同一支臂、同 cell、同 shape 的三次全新啟動（寫到分開的 `/tmp/kb_repro`，沒有進認證目錄）：

```
原本 r4_b_k2    17.67 / 11.95 / 10.74     三次 mean 13.45
重現（三次）     7.63 / 10.51 /  8.17     三次 mean  8.77   ← 15 分鐘後，什麼都沒改
```

17.67 需要 26 rounds × 139.3 ms；重現組是 36×233、34×179、45×174。**它不是這支臂的成績，
是分布在尾端的一次抽樣**，只能引用為觀察到的抽樣。

而且這**不是 session 漂移**：全部 72 次啟動按時間排，前半 mean **10.41**、後半 mean **10.05**。

### 5.2 這對判決意味著什麼

- 單次啟動的散布 21% ⇒ 一支臂只用 3 次啟動估均值，臂均值自帶約 **12%** 誤差。
- 要被認證的效應是 **+5.5%**（點估）。
- 在 n=12、sd 1.96 下，這個設計的可偵測下限是 `2.20 × 1.96/√12 = **1.24 t/s（12.1%）**`。
- 要偵測量到的 **+0.54**，需要 `n ≈ 53 對`（約 4.4 倍於登記的上限、數小時的機器時間）。
- 反過來說：如果效應真的是歷史的 **+1.71**，這個 sd 下 `n ≈ 6` 就會看見它。**看不見，是因為
  效應小，不是因為設計笨。**

**charter 的 on_fail 路線**：把 k 當已知噪音源——k=2 當便宜 screen，winner 回 k=3 確認一次
（兩者 PSO 不同，排名不可直接搬）。

### 5.3 配對取消不掉的那一項

```
asymmetry: k2 carries a launch-level term of 8.61%, k3 carries 0.00%
  => pairing (AB/BA) can only cancel a term BOTH groups share, so it cannot remove this one.
```

ABBA 交錯能抵銷**兩組共有**的漂移（時段、熱、盒子狀態）。它**不能**抵銷只長在 k2 上的那一項。
這是目前最像機制的一條線索：**k=2 有 k=3 沒有的啟動級項**。它與 charter 記載的「單邊慢狀態」
同族，值得當成下一個要量掉的東西。

## 6. 這一輪順手抓到並修掉的缺陷

- **`k3_pair_cert.sh` 把 F gate 的臨界值寫死成 4.07。** 那是**階段 1 的 df**（4 arms × 3 reps
  ⇒ df=(3,8)）的 95% 點；階段 2 是 12 arms × 3 reps ⇒ **df=(11,24) ⇒ 2.22**。工具的預設
  （(4,10) → 3.48）兩個都不是。我第一次就是拿 4.07 去讀 12 對，工具當場印
  「4.07 is the 95% point of a DIFFERENT df」。已改成寫出 df 的公式。**兩次讀數結論相同
  （k2 F=1.58 < 2.22、k3 F=0.87 < 2.22），但結論相同是運氣，不是理由。**
- **「第一個 measured launch 系統性偏低（冷啟）」這條假設沒有被證實，而且方向常常相反。**
  這一輪 12 支臂裡 `first→last` 有 **6 支是單調下降**（k2 那組：+8% +27% −11% +1% −8% +32%
  +39% +35% +48% −8% +2% +34%）。它已經寫在驅動檔頭的注意事項裡，應改成
  「第一個 launch 是**離群的位置**（兩端都發生），不是偏低」。

## 7. 沒回答的

- **交付數字是多少**（12.57 那個口徑）：需要交付 cell 被寫進測試卡 §2.5 才問得到，仍是未決。
- **這台盒子上兩種 k 的可偵測下限**：本文件量到 sd 1.96 是**在「一個 rep 一次啟動＋冷卻」
  協定下的標準差**；它比歷史的 1.83（server 口徑 n=5）大，但那兩者不可比（不同 cell、不同協定）。
- **`ms/round` 為什麼散 138–248**：直方圖在 130–139（3 個）、150–159（8 個）、180–189（9 個）
  有堆疊的樣子，但沒量到對應的 GPU 時脈／電壓狀態。這是「`rounds` 之外的 13%」的來源，
  也是把 sd 壓下來最直接的地方。
- **k2 那個 8.61% 的啟動級項**：偵測到方向但沒有機制（5.3）。

## 8. 產物與可重現

```
/tmp/kb/r{1..12}_{a,b}_k{2,3}.json     24 份臂產物（+ 每支臂 rep_split 逐啟動證據）
/tmp/kb/logs/<arm>/launches/L0{1,2,3}/ 72 份 llama-bench 原始 log（含 CGC-BENCH-ACCEPT）
/tmp/kb/driver.log                     逐對、逐臂、逐啟動的驅動記錄
/tmp/kb_repro/                         17.67 的重現嘗試（3 次啟動，**不屬於認證**）
```

判決指令（一次、在跑滿登記的 N 之後）：

```bash
/opt/homebrew/bin/python3 scripts/check/k_swing_decompose.py \
  --dir /tmp/kb --groups k2,k3 --paired --planned-n 12 --f-crit 2.22
```

`--planned-n` 是必須的：`paired_verdict` 在 n < planned_n 時**拒判**（那會是 optional stopping），
在 n > planned_n 時也拒（那是另一個實驗）。`--f-crit` 用**這個階段**的 df，見 §6。
