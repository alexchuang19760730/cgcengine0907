# MTP ON 優化計畫 —— 只建立在可支撐證據上的那一份（2026-09-25）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **12.57／12.62 t/s 屬 MTP-on 輸出**，是**另一個輸出函數**，
> **不可與 MTP-off 互比**（含「MTP 加速 X%」「MTP-on ≥ MTP-off」「以 12.57 為基線／錨點／要打敗的數字」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**。

> **目的**：回答「要怎麼優化 MTP ON」。方法：先把所有既有量測按**支撐等級**過一遍
> （`M` 實測／`D` 量綱恆等或定義／其餘一律丟摘要末端），再看剩下的空格落在哪裡。
> 機檢沿用的是 `scripts/check/formula_audit.py` 的分級規則。
>
> ⚠ **本篇沒有新的 GPU 量測**（今天的量測是既有的 `Backup/phase_decomp/spec_cost_en-mtp-repro-250925.json`）。
> 本篇新增的是**同輪交叉檢核**與**用既有係數算出的決策門檻**，兩者都是離線算術，可複算（見 §7 複算命令）。

---

## 0. 一句話

**MTP ON 在交付 cell 的增益目前是 UNRESOLVED**（`docs/MEASUREMENT_CONTRACT_2026-09-25.md` §7），
所以「優化 MTP」這件事的**第一個動作不是改東西，是解一個矛盾（§1.3 C1）**：同一個引擎，
兩個口徑各自測出來的「每個 decode step 交付幾個 token」差 **1.9×**。在那之前，任何
「這樣改會 +X%」都沒有地基。

在這個前提下，今天仍然**可以引用**的三條硬結論：

| id | 結論 | 級 | 出處 |
|---|---|---|---|
| **N1** | verify 多一個 token 的成本裡，**draft head 只佔 ~12%，target verify 路徑佔 ~88%**（斜率 +8.35 vs +61.21 ms/k，`dof=2`） | `M` | `docs/MTP_K_SWEEP_2026-09-22.md` §3 |
| **N2** | verify 邊際 token（+51~55 ms/token）的通道拆分：**device span 56–61% ／ expert-cache host hook 35–40% ／ dispatch ~2%**（三次 launch 各自前方 "`UNION-LED`"） | `M` | `docs/VERIFY_TOKEN_CHANNELS_2026-09-23.md` §2 |
| **N3** | 在部署接受率下**沒有觀察到「加深更划算」的現象**，`VERIFY_TOKEN_CHANNELS` §9b 的模型給出「k=1 最好、寬 verify 要 `a≈0.80` 才划算、任何 a 都到不了 25 t/s」 | `M`+模型（⚠ 帶前提，見 §2.4） | 同上 §9b |

⇒ **N1+N2 直接告訴我們一件事：可以動的空間不在 draft head，在 verify 路徑，而 verify 路徑有 6 成是 device 時間（至今沒有地板）、近 4 成是 expert cache 的 host hook。**

---

## 1. 現狀座標（每一格都標了口徑，缺量測的格子直接寫「缺」）

### 1.1 交付 cell（唯一拿來下決策的口徑）

```
cell   : prod_profile.py 的 decode-delivery
         --prompt 0 --gen 128 --depths 512 --batch 512 --ctx-size 4096
         --warm-skip 64 --spec-type draft-mtp      （reps=3、NOMINAL 全程）
基線   : 12.57 t/s ／ step 247.98 ms   ⇒ ASL = tps × step = 3.117 token/step  [D 恆等 + M 同源]
產物   : Backup/prod_profile/prod_profile_decode_20260920_2041.json
```

- 若這個 step 真的以 `k=3` 運作，反解的 chain 接受率 **p = 0.8356**（`docs/MTP_VERIFY_HEADROOM_2026-09-24.md` §2）。
- **`12.57` 這個數字本身含 MTP ON**（cell 帶 `--spec-type draft-mtp`），所以它不是「OFF 參考」。
- ⚠ **交付 cell 的 MTP ON／OFF 配對讀數：缺量測 ⇒ UNRESOLVED**（契約 §7）。這就是 §5 的 P0 要補的洞。

### 1.2 今天已有的 MTP 配對（`prod-new`，`-p 0 -n 128 -d 512`，ABBA 3 輪，全程 NOMINAL）

| 輪 | t/s OFF | t/s ON | step OFF | step ON | E（SPECDBG rounds） | cost = step比 | S | k_eff |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| r1 | 8.315 | 7.596 | 120.26 | 224.68 | 1.7067 | 1.8683 | **0.9135** | 1 |
| r2 | 8.552 | 5.828 | 116.94 | 249.58 | 1.4545 | 2.1343 | **0.6815** | 1 |
| r3 | 7.102 | 7.458 | 140.81 | 217.25 | 1.6203 | 1.5429 | **1.0502** | 1 |
| 中位 | 8.315 | 7.458 | — | — | 1.6203 | 1.8683 | **0.9135** | 1 |

`[M]` 全部來自 `Backup/phase_decomp/spec_cost_en-mtp-repro-250925.json`（工具 `spec_cost_curve.py`，selftest 26/26）。

⚠ **絕對 t/s 不可引用**（`-r 1`、無 `--warm-skip 64`、冷池）；**比值可引用**。
⚠ 三輪 S 散在 **0.682 ~ 1.050** ⇒ 在本lineage的單臂噪音底（±27%）之內 ⇒ **只能說「量不到正增益」**，不能說「MTP 是淨虧」。

### 1.3 ★ C1：兩個實測互相矛盾，這是目前最大的未知

| 觀測 | 值 | 來源 |
|---|---:|---|
| 交付 cell 的 `ASL = tps × step` | **3.117 token/step** | §1.1（同一 launcher 的兩個數，`D` 恆等） |
| 今天直數 verify round 的 `E` | **1.62 token/step** | §1.2（`M`，round 計數） |
| 比值 | **1.92×** | — |

兩者都不是推論：一個是「同一支程式印的兩個數字相乘」，另一個是「逐輪數出來的整數」。
差 1.9× 只有三種可能：**(a)** 兩個 cell 的 draft 深度真的不同（`k_eff` 3 vs 1，例如 ckpt／profile 的 MTP head 配置不同）；
**(b)** 其中一個計數器的定義不是它看起來的意思；**(c)** 兩者都對，但 step 的定義不同（round vs wall-clock per token）。
**在沒有裁定之前，「加深 k」與「提高 a」這兩條路的期望值都無法寫死** —— 因為它們的前提就是 `k_eff`。
⇒ **P0 就是為它設計的**（§5）。此處我只寫「有矛盾」，**不寫「誰對」**。

---

## 2. 我今天做的離線交叉檢核（可複算，見 §7）

### 2.1 好消息：今天這一輪的兩個 `E` 來源**完全重合**

`ASL = tps × step` 與 `SPECDBG rounds` 數出來的 `E`，三輪差都 **= 0.0000**：

| 輪 | ASL（tps×step） | E（rounds） | 差 |
|---|---:|---:|---:|
| r1 | 1.7067 | 1.7067 | 0.0000 |
| r2 | 1.4545 | 1.4545 | 0.0000 |
| r3 | 1.6203 | 1.6203 | 0.0000 |

`[D]` `ASL = tps × step` 是定義（`mtp_verify_headroom.tok_per_step`），`[M]` 兩端都是同輪實測。
⇒ **今天的 `E=1.62` 與「這台機器這輪跑多快」是自洽的**；也就是說 §1.3 的矛盾不是今天這一輪的算術錯。

### 2.2 壞消息：仿射 `step(T) = c + m·T` 被今天的資料自己證偽

用今天的兩個點（`T=1` 的 plain step、`T=2` 的 verify 寬度）反解：

| 輪 | step(T=1) | step(T=2) | Δ ms | 反解 c | 反解 m_per_tok | m/c |
|---|---:|---:|---:|---:|---:|---:|
| r1 | 120.26 | 224.68 | 104.42 | +15.84 | 104.42 | 6.59 |
| r2 | 116.94 | 249.58 | 132.64 | **−15.70** | 132.64 | **−8.45** |
| r3 | 140.81 | 217.25 | 76.44 | +64.37 | 76.44 | 1.19 |

⇒ **r2 的固定成本是負的**（物理上不可能），三輪 `m` 散佈 **1.74×**（76~133 ms）。
這與 `MTP_AMORTIZATION_RECHECK_2026-09-25.md` F24（09-24 的 `m(k)` 散佈 1.40×）同向。

**結論（可引用）**：在這個噪音底下，**任何「把兩個點擬成一條 `step(T)` 直線再外推」的做法都不合格**。
決策要用**同一輪內的比值**，或用多點（≥4）且逐 rep 擬合（`mtp_k_sweep.py` 就是這樣設計的，`dof=2`）。

### 2.3 加深 k 的決策門檻（用既有係數算，附上它的弱點）

⚠ **係數的出處要先過它自己的閘門**（本節 §2.3–2.5 的所有門檻都踩在這裡）：
`Backup/s1_ksweep/20260924_225548/s1_ksweep.json` 裡 `quotable: False` —— 因為
`polluted_launches = nospec#r1, k4#r1, k3#r2, k4#r2, nospec#r2`（**10 次 launch 有 5 次被自己的閘門判污染**）、
`window.class = unknown`（「這個 process 從來沒問共享探針，所以這份產物回答不了機器當時忙不忙」）、
而 `io_symmetry_between_arms` 的第一筆是 **blocking asymmetric**（nospec 做了 186171 次 cache 讀取、k1 一次都沒有）。
⇒ **`14.61 / 42.03` 這兩個係數不是可引用量測，本節與 §2.5 的門檻只能當「結構示意」**，
真正的裁定要用 P1 在乾淨視窗重跑。這也是本篇把「加深」判成 UNRESOLVED 而不是「別加深」的理由。

取 `docs/DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md` §2.3 那份**被判定為「模型可移植」**的係數：

```
step(T) = 14.61 + 42.03 × T          （T = k+1，單段提交＋凍結 3 GiB 池）
⇒ step(k) = 56.64 + 42.03 × k
配合該 sweep 自己的 mean_len = 1 + k·acc
⇒ t/s(k) = 1000·(1 + k·acc) / (56.64 + 42.03·k)
⇒ d/dk > 0  ⇔  acc > 42.03 / 56.64 = 0.742          [D，且與 k 無關，因為上下都是 k 的線性式]
```

| acc | k=1 | k=2 | k=3 | k=4 | 判讀 |
|---:|---:|---:|---:|---:|---|
| 0.465（交付 acc 下緣） | 14.85 | 13.72 | 13.11 | 12.72 | 越深越差 |
| 0.5825（部署值） | 16.04 | 15.39 | 15.04 | 14.82 | 越深越差 |
| 0.62（今天中位） | 16.42 | 15.92 | 15.65 | 15.48 | 越深越差 |
| **0.742（門檻）** | 17.65 | 17.65 | 17.65 | 17.65 | 完全無關 |
| 0.836（交付 ASL 反解，前提 k=3） | 18.61 | 18.99 | 19.20 | 19.33 | 才開始微幅划算 |

⚠ **這些 t/s 絕對值不可引用**（非交付 cell：單段提交 ＋ 3 GiB 池）。可引用的是
**「門檻 = `c_tok / (C + c_tok)`」這個結構**與它的方向性結論：**在 acc ≤ 0.62 的現實下，加深是淨虧。**

### 2.4 四個 k 點的**直接讀數**（同一支 driver、同一 cell，連它的閘門一起看）

同一份產物的 `medians`（各自的 `step` 與 `mean_len` 都**取自同一臂**，沒有跨批、沒有引用別批的分母）：

| 臂 | T | step ms | E（mean_len） | 該臂自己的 acc | 推算 t/s |
|---|---:|---:|---:|---:|---:|
| k1 | 2 | 104.87 | 2.000 | 1.000 | 19.07 |
| k2 | 3 | 129.16 | 2.830 | 0.915 | 21.91 |
| k3 | 4 | 187.19 | 3.319 | 0.773 | 17.73 |
| k4 | 5 | 225.61 | 4.730 | 0.932 | 20.96 |

⇒ 這張表**不是單調**的（k2 最好、k3 最差，散佈 22%），而且**各臂的接受率本身就不一樣**
（1.000／0.915／0.773／0.932）⇒ 「加深」與「吞吐」之間沒有任何模型能從這張表裡抽出因果。
另一件值得單獨講的事：**step 的一階差分是 24.29／58.03／38.42 ms**（k1→k2→k3→k4），
而 §2.3 那條直線假設它是常數 42.03 ⇒ **仿射模型連這份自己產生的四個點都描述不了。**

⚠ 同一份產物的 pool 計數器還給出一個**與既有結論直接衝突**的讀數：k1–k4 四臂全是
`requests 2173 / hits 2173 / misses 0 / hit_pct 100.0`（只有 nospec 臂是 125320/103751/21569、82.8%）
⇒ 這裡**看不到** §3 引用的「MTP 臂 miss 數約為 OFF 臂的 3×」（出自 `docs/MTP_K_SWEEP_2026-09-22.md` §4）。
兩份產物要不量到不同的 pool 路徑，要不其中一份的計數器不成立 ⇒ **本篇不替它們調停**，P1 要一起量同一組計數器。

### 2.5 第二個模型：chain 接受模型下的 `m/c` 門檻（機械複算，用既有工具函數）

用 `mtp_verify_headroom.breakeven_m`（部署口徑 `p = 0.5825`）算「加深一步恰好打平」所需的
`m/c`，拿今天那組係數的 `c_tok / c = 0.7421` 去比：

| 加深 | 打平門檻 m/c（p=0.5825） | 今天的係數 m/c | 讀法 |
|---|---:|---:|---|
| k=1 → 2 | **0.3754** | 0.7421 | 高於門檻 ⇒ 淨虧 |
| k=2 → 3 | 0.1487 | 0.7421 | 更高 ⇒ 更虧 |
| k=3 → 4 | 0.0694 | 0.7421 | ⇒ 淨虧 |

**但這個遊戲對接受率極度敏感** —— 而接受率正是 §1.3 的 C1 爭的東西：

| 接受率 p | k=1→2 門檻 | k=2→3 | k=3→4 | 今天的係數 0.7421 之下 |
|---|---:|---:|---:|---|
| 0.5825（部署值） | 0.3754 | 0.1487 | 0.0694 | 全部低於係數 ⇒ **k=1** |
| 0.62（今天中位） | 0.4516 | 0.1848 | 0.0895 | ⇒ **k=1** |
| **0.8356**（交付 ASL 反解，前提 k=3） | **1.59** | **0.7446** | 0.4177 | ⇒ **最佳落在 k≈2~3**（接近今天部署的設定） |

⇒ **「該不該加深」的答案會因為 C1 的解不同而翻面**。這也是為什麼 P0 排在一切之前：
它不是「多跑一份報告」，它是唯一能把這張表寫死的動作。

⇒ **兩個模型、兩份係數同向**（但它們踩在同一份被自己閘門拒收的產物上，見 §2.3 的警告）：`(1)` §2.3 的 `mean_len = 1 + k·acc` 模型 ⇒ 需要 `acc > 0.742`（今天是 ≤0.62）；
`(2)` 本節的 chain 模型 ⇒ 需要 `m/c < 0.3754`（今天的係數是 0.7421）。
另有 `MTP_VERIFY_HEADROOM` §3 的第三個口徑（`k=3→4` 門檻 `0.4176`，實測估 `0.9172`）⇒ **結論一致：先別加深。**

⚠ **兩個都帶前提**：§2.3 假設 `mean_len` 線性於 k（該 sweep 自己的模型），本節假設**逐位置接受率獨立且恆定**
（chain 模型；「接受率是否隨 k 保持不變」未量測）。⇒ 它們能給「方向」，**不能給數字**；真正的裁定要等 P1 的多點量測。

---

## 3. 成本在哪裡（與接受率無關的那一層）

### 3.1 第一層：verify 側專家 union 撐破 per-layer 配額

今天的整數計數器（同一輪，ON vs OFF）：

| 計數器 | MTP off | MTP on（k_eff=1） | 讀法 |
|---|---:|---:|---|
| MiB/step | 19.87 ~ 20.25 | **49.87 ~ 53.70** | 多驗 1 個 token ⇒ **+30 MiB** 專家讀取 |
| worst layer distinct / slots | 111~130 / 143 | **168~205 / 143** | 工作集**撐破 per-layer 配額**（越界 18%~43%） |
| layers_over_slots | **0** | **2 ~ 4** | 整數，非擬合 |
| 池 hit% | 94.9 ~ 95.0 | **91.9 ~ 92.3** | −2.7 pp |
| miss_compulsory / capacity | 2331 / 20 | 3559 / 156 | capacity miss 佔比 ~4% |

`[M]` 同源。`docs/MTP_K_SWEEP_2026-09-22.md` §4 的結論同向且更強：**MTP 臂的 miss 數約為 OFF 臂的 3×，且隨 k 上升**
（hit 96.3–96.7% → 85.0–87.7%）⇒ **任何 verify 成本模型若不帶 pool-churn 項，就少算了與主項同量級的一項。**

### 3.2 為什麼 MoE 攤不開（有算得出來的成因）

```
expert bank shape-probe（真幾何 256 experts × top-8 × 40 層）:
   T=1..4 → 8.09 / 15.98 / 23.79 / 31.81 ms/步      ⇒ extra_over_first = 0.977   [M]
成因：top-8 of 256 ⇒ 兩個 token 的專家集期望重疊 = 8²/256 = 0.25 個        [D 定義算術]
⇒ verify 時多驗的 token 幾乎不共享專家 ⇒ expert bank 近似線性，沒有 batching 收益
```
（`docs/MOE_GATHER_BOUND_2026-09-22.md` §2、`MTP_VERIFY_HEADROOM_2026-09-24.md` §4.1）

若把第 2 個 token 起的成本從 0.977 降到 `share`，**家族內**的降幅：

| share | T=4 家族成本 | 對今天 |
|---:|---:|---:|
| 0.977（今天） | 31.81 | — |
| 0.750 | 26.29 | −17.3% |
| **0.500** | **20.23** | **−36.4%** |

⚠ **不可把它乘到 t/s**：缺「expert bank 佔交付 device 的比例」這個數 ⇒ **⛔ 不可引用為 t/s 預測**（`MTP_VERIFY_HEADROOM` §4.2 的換算警告）。

---

## 4. 槓桿矩陣（推算欄 × 量測欄 × 判定）

規則沿用 `docs/MEASUREMENT_CONTRACT_2026-09-25.md`：**門檻 3%**（單臂噪音底 ±27% ⇒ 一律配對輪內比值）；
**缺量測的格子顯式標「⚠ 缺量測」，不靜默通過。**

| 槓桿 | 推算欄（上界＋前提） | 量測欄（實測＋產物） | 判定 | 下一步 |
|---|---|---|---|---|
| **A. 提高接受率 a / 練 draft head** | `E = 1 + a·k_eff`（定義式）。`k_eff=1` 時 `a: 0.62→0.90` ⇒ `E` +17% | draft head forward 只佔邊際 token 的 **12%**，且已在自己的 kernel 地板（8.36 ms/token）`MTP_K_SWEEP` §3；既有 ceiling 判決已判「別練 draft head」 | ❌ **不做** | 不必排程 |
| **B. 加深 draft（k 3→4…）** | 門檻 `acc > c_tok/(C+c_tok) = 0.742`；另一口徑門檻 `m/c < 0.4176` vs 現況 ~0.92 | 現實 acc 0.465~0.62（`DIAGNOSTIC_ARMS_LEDGER` §2.3／`VERIFY_TOKEN_CHANNELS` §9b） | ⚠ **UNRESOLVED**（不是「別加深」的實證結論） | P0 解 C1；P1 在乾淨視窗重跑多點 k |
| **C. verify 的 device 時間（kernel／頻寬）＝ C 軸** | 擁有邊際 token 的 **56–61%**；這個比例本身是實測 | ⚠ **缺地板**：45 份 log 帶 op 表，25 被 sampling mix 拒、17 被寬度 cell 拒、**0 份通過**；補跑的 NSM capture 得 rank 20/44、R² 0.67 ⇒ 不可歸因（`VERIFY_TOKEN_CHANNELS` §8/§8b） | ⚠ **最大空間，但今天沒有數字** | P3：shape probe（先鋪地板，再談優化） |
| **D. expert-cache host hook（`cb`，35–40%）** | 池單元成本下降 ⇒ 直接砍這個 slice；理論天花板：路由 top-143 覆蓋 **98.8~98.9%**（masscov 與 route-dump 兩個獨立口徑） | 今天 pool 只吃到 ~92%；`LLAMA_EXPERT_CACHE_PIN_PROFILE` 實測把 count-cold **34.2% → 10.6%**（hit 65.8→89.4%） | ⚠ **t/s 未驗證**（09-23 §4：三趟不是同一 swap 狀態，`3.11 vs 11.98` 不可當 A/B；且有 09-21 的反向否證） | **P2：乾淨配對**（swap=0 全程） |
| **E. 讓 verify token 共享專家（residency / reuse gating）** | 唯一能降 `extra_over_first 0.977` 的機制 ⇒ 家族內 −36.4%（不可乘到 t/s） | AcceptMoE 定價：在今天的駐留覆蓋下要付 **21.26% 路由質量**換 +17.5% 結構上限 ⇒ **先修駐留（D）才划算** | ⚠ 排在 D 之後 | D 有結論才輪到它 |
| **F. 純 IO 軸（`cb` 填池／prefetch／併發度）** | 交付 cell 的 fill **同步**只佔 step **4.7%** ⇒ 上限 ~5% | M-W（workers 8→2）：`us/job` −11.1% 但 t/s **−0.6%**；layer-ahead prefetch **−7.1%**（門檻 15%，未分離） | ❌ **已結案，別再賭** | — |

> ⛔ **不要同時做兩件**：這個 repo 的每個比較都建立在「同一 build、同一 cell、swap=0」上。
> ⛔ **收益不可相加**：C 與 D 打的是同一個 step 的不同 slice，但各自的 baseline 重疊；既有判決「分量相加在這個 regime 不閉合」。

---

## 5. 建議的執行順序（全部 0 重建）

| 步驟 | 動作 | 時間 | 判準（預先寫死） | 解什麼 |
|---|---|---:|---|---|
| **P0** | 在**交付口徑**重跑 MTP ON/OFF 配對，同時抓 `CGC-MTP-PERF` 與 round 計數 | ~25 min | ① `ASL(tps×step)` 與 `E(rounds)` 相差 **≤10%**；② 三輪輪內 `S` 能否全部 **>1.03**；③ 12/88 的 draft/verify 拆分是否複現 | §1.3 的 **C1**、交付 cell 的 ON/OFF 洞 |
| **P1** | `mtp_k_sweep`（4 個 k 點，`dof=2`）搬到交付 caliber | ~40 min | per-rep 擬合的 `c_tok` 是否重現 69.56 ms；`acc` 是否**不再 = 1.000**（否則這個儀器不能排 k） | 是否真的別加深 |
| **P2** | PIN_PROFILE vs base，delivery cell，ABBA ×3，swap=0 全程 | ~25 min | 輪內 `cb` ms/step 比值下降 **≥15%** 才算分離（t/s 可能仍在噪音裡） | D 這一格能不能收 |
| **P3** | verify device 時間的 shape probe（attention/GDN/expert bank 分開要有地板） | 半天 | 先產出地板，不設 t/s 門檻 | C 軸唯一能開始設計的前置 |

### P0 的命令（可直接複製）

```sh
# 交付口徑：工具不吃 --warm-skip，所以走 env（llama-bench.cpp:516 讀 CGC_BENCH_WARM_SKIP）
# profile 沿用 anchor 那支（prod25-stream），避免再引入一個新變因
CGC_BENCH_WARM_SKIP=64 LLAMA_BENCH_SPEC_DBG=1 CGC_MTP_PERF=1 \
python3 scripts/check/spec_cost_curve.py --profile prod25-stream --ks 0,3 --rounds 3 \
  --prompt 0 --gen 128 --depth 512 --batch 512 --ubatch 512 \
  --json Backup/phase_decomp/spec_cost_delivery_<tag>.json
```

### P1 的命令

```sh
python3 scripts/check/mtp_k_sweep.py --reps 3 --logdir /tmp/mtp_ksweep_<tag>
# 說明：4 個 k 點 + OFF；OFF 臂跑 -b 512 而 decode-spec 跑 -b 8 ⇒ OFF 是外部參考，不是擬合點
```

### P2 的命令（沿用 09-23 的 pin 流程，但**必須全程 swap=0**）

```sh
# 產生 profile
CGC_MASSCOV=1 LLAMA_EXPERT_CACHE_ROUTE_RECORD=1 LLAMA_EXPERT_CACHE_ROUTE_DUMP=/tmp/route_dump.txt \
  python3 scripts/check/llama_bench_matrix.py --arms prod25-stream --reps 1 --prompt 0 \
  --gen 128 --depths 512 --batch 512 --ctx-size 4096 --warm-skip 64 --workdir /tmp/x
# 用 profile 靜態釘住（AB/BA 交錯，reps=3）
CGC_MASSCOV=1 LLAMA_EXPERT_CACHE_PIN_PROFILE=/tmp/route_dump.txt \
  python3 scripts/check/llama_bench_matrix.py --arms prod25-stream --reps 3 ... --workdir /tmp/y
```

---

## 6. 明確**不要**做的事

1. **別相信** `13.7 t/s` / `S=1.19` / `E=2.31` / `a=0.44` / `m=0.19` —— 已作廢並歸檔
   （`docs/archive/mtp_amortization_SUPERSEDED_2026-09-25.html`，契約 §7）。
2. **別用** `cost = 1 + m·k` 外推到沒量過的 k —— 線性模型被它自己的資料證偽（F24：09-24 的 `m(k)` 散佈 1.40×；今天 §2.2 更慘，`c` 出現負值）。
3. **別把** expert-bank 家族的降幅（−36.4%）**乘到 t/s** —— 缺「family 佔 device 的比例」。
4. **別把 S1 的 1.83× 算成 MTP 的** —— 那是序列化（`docs/S1_LINE_VERDICT_2026-09-25.md`）；交付 cell 的 MTP 增益仍是 UNRESOLVED。
5. **別用 pooled fit 下結論** —— 本 line 的 rep 間漂移到 1.5×（`MTP_K_SWEEP` §6）；一律 per-rep。
6. **別在 swap > 0 的機器上報任何 t/s** —— 09-23 的 `3.11 vs 11.98` 就是這樣報廢的。

---

## 7. 複算（本篇所有新算術都不需要新程式）

```sh
cd /Users/alexchuang/Documents/flashkv-devserver
python3 - <<'PY'
import json, sys
sys.path.insert(0,'scripts/check')
import mtp_verify_headroom as H
d=json.load(open('Backup/phase_decomp/spec_cost_en-mtp-repro-250925.json'))
runs={(r['k'],r['round']):r for r in d['runs']}
for rd in (1,2,3):                                    # 2.1 同輪交叉檢核
    on=runs[(3,rd)]; off=runs[(0,rd)]
    asl=H.tok_per_step(on['tps_tg'], on['ms_per_step'])
    print(rd, round(asl,4), on['E'], round(on['ms_per_step']/off['ms_per_step'],4),
          round(on['E']/(on['ms_per_step']/off['ms_per_step']),4))
Cf,ctok=14.61,42.03; c=Cf+ctok                        # 2.3 加深門檻
print('threshold acc >', round(ctok/c,4))
PY
```

引用產物：

| 產物 | 內容 |
|---|---|
| `Backup/phase_decomp/spec_cost_en-mtp-repro-250925.json` | 今天 6 趟（k=0/3 × 3 輪）的原始羽毛：tps、rounds、E、read MiB、hit%、worst distinct |
| `docs/MTP_K_SWEEP_2026-09-22.md` | 4 點 k sweep、`c_tok=69.56`、12/88 拆分、pool churn 3× |
| `docs/VERIFY_TOKEN_CHANNELS_2026-09-23.md` | 邊際 verify token 的通道拆分（56–61/35–40/2）與 §9b 的接受率決策 |
| `docs/DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md` §2.3 | 可移植的 `step = 14.61 + 42.03·T` |
| `docs/MTP_VERIFY_HEADROOM_2026-09-24.md` | `m/c` 門檻幾何、expert bank 線性成因 |
| `docs/REPLACEMENT_POLICY_75PCT_2026-09-23.md` | 池治理膠（hit 的天花板 98.8% 與今天的 65.8~92%） |
| `docs/NEXT_STEP_MISS_HANDLER_2026-09-24.md` | miss 是穩態現象；⬜ pool 加大／換策略只能吃到 ~4%（capacity miss 佔比） |

⚠ **OPEN（未裁定）**：「加大池／換替換策略只能消 4%」（`NEXT_STEP_MISS_HANDLER`）與
「PIN_PROFILE 把 count-cold 從 34.2% 打到 10.6%」（`REPLACEMENT_POLICY_75PCT`）看起來張力很大 ——
兩者量的是不同東西（miss 分類 vs count-cold 比例），**本篇不替它們調停**，P2 的判準是 `cb` ms/step，不是任一個百分比。
