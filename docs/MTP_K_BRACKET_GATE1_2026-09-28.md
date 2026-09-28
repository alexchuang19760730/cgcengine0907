# 門 1（速度）k 端點包夾 —— 判死未達成，但拿到三個改判下一步的事實（2026-09-28）

> 🟡 **門 1 判決：UNRESOLVED（判死未達成）** —— 且按預註冊 `on_fail`，**不再投入盒子**
> 立項卡：`scripts/check/charters/e-mtp-k-bracket-2026-09-28.yaml`（跑之前定死，未事後修改）
> 產物：`Backup/phase_decomp/mtp_kbracket/`（`run_k_bracket.sh`／`analyze.py`／4＋1 份 json／`cand_k1*.json`）
> 提出：本線（`線A (ace)`）· **0 build**（純量測，不改引擎、不改交付口徑）

---

## 0. 一句話

門 1 的 `ratio ≥ 1.0` **判不出來**（實測 0.989，兩側 spread 5.9%／6.0% ≫ 3% 閘門線），
但這一輪證實了兩件改變「下一步該打哪裡」的事：
**`a(k=1) = 0.81 > 0.742`（接受率這條已經過門檻）**、**`m(k=1) ≈ 0.60–0.84`（攤薄極差）**。
⇒ 瓶頸不在 accept，在 `m`。「練 draft head」這條槓桿應降級，改查「為什麼一個 draft token 要
花 0.6–0.9 個 plain decode step」。

## 1. 設計：為什麼只掃 k=1 與 k=3

兩個互相競爭的假說**都預測 `S(k)` 單調**，只是方向相反：

| 假說 | 內容 | 預測 |
|---|---|---|
| **H+ 理論** | `S(k) = (1+a·k)/(1+m·k)`，`a=0.62 > m=0.474` ⇒ 遞增，極限 `a/m = 1.31` | k 越大越好 |
| **H− 實測** | 09-21 prod25 sweep：k=0..3 t/s = 10.845 / 10.320 / 10.809 / 10.072 | k 越大越差 |

單調函數的最值落在端點 ⇒ **量兩個端點就能包住整條曲線**；只有「內點有峰」這種兩個假說都不預測的
情形會漏。這比「4 個 k 各 1 趟、OFF 只有 1 趟」資訊量高（後者無法扣漂移，且 gate 規則 3 要求每側 ≥2 reps）。

順序 **OFF(caps) → ON k=1 → ON k=3 → OFF(caps)**：OFF 前後各一次做漂移括號。
`k=0` 就是 OFF 臂本身 ⇒ 實際掃到 `k ∈ {0, 1, 3}`。

⚠ **OFF 控制組帶與 ON 同源的三個旋鈕**（`CGC_SERVER_LAYER_CAPS=40-40:16`／`CGC_DRAFT_CTX_ALIGN=1`／
`CGC_DRAFT_SMALL_BATCH=1`）⇒ paired ratio 的唯一差異就是 `--spec-type`，不是「MTP ＋ 順便改了 cap」。

## 2. 預註冊判決規則（節錄，全文在立項卡）

| 分支 | 條件 | 處置 |
|---|---|---|
| `success` | 任一端點 ratio ≥ 1.10 | 門 1 數值 PASS；**但 B1 ⇒ 不得表述為「OFF 的 X%」**，立項 §3 不解除 |
| `falsify` | 兩端點 ratio 皆 ≤ 0.90 | 門 1 判死 FAIL（配合單調性 ⇒ 所有 k 皆不過） |
| `undecidable` | 任一端點落在 (0.90, 1.10) | 登記 UNRESOLVED，**不再投入盒子** |
| `window_valid` | 4 趟起跑 NOMINAL ＋ `pages_available ≥ 4000` ＋ OFF 中位 ∈ [9.87, 13.35] | 任一不成立 ⇒ 本輪 0 個可引用 ratio |

## 3. 逐趟

| 趟 | 起跑 | arm | tg reps | 中位 | rep1 | pp | swap 起→峰 (MiB) | wall |
|---|---|---|---|---|---|---|---|---|
| `off_a` | 22:01 | OFF＋caps | 10.87 / 10.91 / 10.22 | **10.869** | 10.869 | 250.0 | 2410 → 6828 | 96 s |
| `on_k1` | 22:06 | ON k=1 | 10.74 / 9.20 / 15.28 | **10.742** | 10.742 | 257.9 | 5434 → 9026 | 96 s |
| `on_k3` | 22:06 | ON k=3 | 0.392 / 0.371 / 0.397 | **0.392** ⚠ | — | 267.1 | **7434 → 8680** | **1012 s** ⚠ |
| `off_b` | 22:25 | OFF＋caps | 4.09 / 11.82 / 11.53 | **11.528** | 4.087 ⚠ | 274.5 | 7650 → 9482 | 93 s |
| `on_k1_witness` | 22:39 | ON k=1（重跑，只為取見證行） | 12.35 / 11.41 / 9.58 | **11.407** | **12.349** | 291.5 | 5651 → 8737 | 89 s |

四趟起跑皆 `thermal = NOMINAL`、`pages_available ≥ 4000`；`worst` 四趟皆 HEAVY（既有事實：
「全程 NOMINAL」在這盒子上做不到）。`attribution.verdict` 四趟皆 `both` —— 按本線慣例，
**`attribution` 會被 swap 淨回收帶偏，判髒窗看 `thermal.worst`，不看它**。

⚠ **`on_k3` 的 0.392 不是 MTP 的速度**：那一趟起跑時盒子 swap 已 7434 MiB，wall 1012 s
（正常 90–96 s）⇒ 是**盒子崩潰**，不是 `k=3` 的效應。⇒ **`k=3` 這一輪沒有量到**，
預註冊規則裡的 `ratio=0.035` 應視為無效讀數，不進入判決。

## 4. 判決

漂移校正（OFF 前後線性內插）：`k=1` ⇒ **ratio 0.9687（−3.1%）**；`k=3` ⇒ 0.035（無效）。
`0.9687 ∈ (0.90, 1.10)` ⇒ **UNRESOLVED**。

送進 `mtp_promotion_gate.py`（`Backup/phase_decomp/mtp_kbracket/cand_k1_verdict.json`）：

```
verdict: REFUSED
  output function: candidate=MTP-on baseline=MTP-off identical=False
  NOT INTERPRETABLE AS A SPEEDUP: on t/s only as 'MTP-on output function'; never as a % of off
  REFUSE: off repeats disagree by 5.88% > 3% -- pair noise is above the gate
  REFUSE: on repeats disagree by 6.00% > 3% -- pair noise is above the gate
  REFUSE: MTP-on is slower than its own paired off control: 0.99x (11.07 vs 11.20 t/s)
  note: paired ratio: on/off = 0.989x
```

⇒ **連「盒況可比性」那一層都沒過**（規則 4），根本沒走到 ratio 那一條。
B1 語義層照常印出 ⇒ **`on_k1_witness` 的 rep1 12.349 只能標為「MTP-on 輸出函數下的 t/s」**，
不得寫成「比 OFF 快 X%」（它高於 OFF 走廊上緣 12.02，很容易被誤讀成一個勝利）。

## 5. ★ 本輪最有價值的東西：見證行（證實 ON 真的在 draft）

`on_k1` 的 stderr 被 `off_b` 覆蓋（同 arm 字串 ⇒ 同檔名，見 §7 第 4 條），所以重跑一趟並用
`--workdir /tmp/kbracket_on_k1` 分開。拿到：

```
CGC-DRAFT-CTX-ALIGN: applied path=mtp n_ctx_req=0 -> n_ctx_draft=768 (target ctx=768)
CGC-DRAFT-SMALL-BATCH: applied path=mtp n_batch_req=2048/512 -> n_batch_draft=4 (n_max=1)
CGC-BENCH-ACCEPT phase=warm_skip rounds=38 drafted=38 acc_drafts=32 mean_len=1.8421 draft_ratio=0.84211 gen_tokens=64 n_gen=64
CGC-BENCH-ACCEPT phase=timed   rounds=36 drafted=36 acc_drafts=32 mean_len=1.8889 draft_ratio=0.88889
CGC-BENCH-ACCEPT phase=warm_skip rounds=39 drafted=39 acc_drafts=32 mean_len=1.8205 draft_ratio=0.82051
CGC-BENCH-ACCEPT phase=timed   rounds=42 drafted=42 acc_drafts=32 mean_len=1.7619 draft_ratio=0.76190
CGC-BENCH-ACCEPT phase=warm_skip rounds=40 drafted=40 acc_drafts=32 mean_len=1.8000 draft_ratio=0.80000
CGC-BENCH-ACCEPT phase=timed   rounds=45 drafted=45 acc_drafts=32 mean_len=1.7111 draft_ratio=0.71111
```

兩個結論：

1. **排除「ON 那趟其實量到 plain decode」這個替代解釋。** 今天 18:26 的 prod25 log 裡有
   `spec draft: llama_decode[0] returned -1 on the FIRST draft step => THIS GENERATION YIELDS 0
   DRAFTS/ROUND`；**本趟沒有這一行**，且有 `n_max=1` 的見證 ⇒ MTP 真的在 draft。
   ⇒ `ratio ≈ 0.99` 是真的在量 MTP，不是「兩個 OFF 互比」。
2. **`a(k=1) = 0.711–0.889，中位 0.810`**，`mean_len ∈ [1.711, 1.889]`。

## 6. ★ 由此改判：門檻問題從 accept 移到 m

| 量 | 值 | 對照門檻 | 判讀 |
|---|---|---|---|
| `a(k=1)` | **0.810**（0.711–0.889） | model-one `acc > c_tok/(C+c_tok) = 0.742` | ✅ **過門檻** |
| `E = mean_len(k=1)` | 1.71–1.89 | — | 一輪真的吐 ~1.8 個 token |
| `S`（實測 paired） | 0.989 | gate `≥ 1.0` | ❌ 持平 |
| **`m` 反解** | **0.60 – 0.84** | skill 判讀 `m ≤ 0.25` 攤薄良好／`m ≥ 0.6` 幾乎沒攤薄 | ⛔ **落在最差的那一區** |

反解方式：`cost = E / S`，`m = (cost − 1)/k_eff`，`k_eff = 1`。
- 用閘門的 `S = 0.989` 與 `E = 1.82` ⇒ `cost = 1.84` ⇒ **`m = 0.84`**
- 用 rep1 配對（12.349 vs 10.869）⇒ `S = 1.136` ⇒ **`m = 0.60`**

⚠ **這是單點反解不是擬合**（`k_eff = 1` ⇒ 只有 `k ∈ {0,1}` 兩點）。按 `cgc-mtp-cost-curve`
鐵律 9，**不得拿這個 `m` 外推到別的 k**。可引用的範圍就是「`k=1` 這一點上 `m ∈ [0.60, 0.84]`」。

⇒ **「accept 不夠」在 k=1 上不成立**（0.81 > 0.742）；**「練 draft head 值得」也不成立**
（`m ≥ 0.6` ⇒ skill 判讀明確寫「練 head 白花錢，去查 batch／並集路徑」）。
兩個既有結論**在這個 k 上同時被修正**。

⚠ 與既有「accept ≤ 0.62」不衝突：那一族多半是較大 k 的每位置平均，而 **`a` 隨 k 變**
（本輪 k=1 得 0.81）。⇒ **不能再拿單一 accept 數字當全域結論**，引用時必須帶 k。

## 7. 盒子層事實（可複用，比本輪的結論更耐用）

1. **這顆 cell 在這盒子上實際只能連跑 2 趟，不是 2–4 趟。** 第 3 趟（`on_k3`）起跑時 swap 已
   7434 MiB ⇒ 0.392 t/s、wall 1012 s（正常 90–96 s）。既有認知「2–4 趟」偏樂觀，應下修為 **2 趟**。
2. **同一配置的兩趟（`on_k1` 10.742 vs `on_k1_witness` 11.407）中位差 6.0%** ⇒ 單趟噪音仍是
   效應量級 ⇒ 4 趟 ABBA 在這個盒子上結構上無法分辨 ±5% 以內的效應。
3. **單臂內 spread 最高 67%**（`off_b` rep1 4.087 vs 中位 11.528）⇒ **中位與 rep1 會給出相反
   結論，兩者都要報**；只看一個一定會錯。
4. ⚠ **`/tmp/harness_bench` 的 stderr 檔名由 arm 字串決定 ⇒ 同 arm 的第二趟會覆蓋第一趟的 log。**
   本輪因此丟了 `on_k1` 的見證行（`off_b` 寫進同一個檔名），只能靠重跑補回。
   ⇒ **要見證行就必須 `--workdir` 分開**。

## 8. 與既有結論的關係

- ⛔ 「accept ≤ 0.62 ⇒ 別加深」：**在 k=1 上被本輪讀數挑戰**（0.81）。見 §6 的 k 依賴性說明。
- ⚠ 09-28 修復後那批「−18%~−28%」：**本輪沒有復現**（−1.1%~−3.1%）。但兩者皆非乾淨窗 ⇒
  「慢多少」仍然**不可定價**，只能說方向 ≤ 1.0。
- ✅ B1 生效：`on_k1_witness` rep1 **12.349** 是交付 cell 上最高的單趟讀數之一（> 走廊上緣 12.02），
  但它是 **MTP-on 輸出函數**下的數字，**不可與 OFF 的 11.61 相減**。
- ✅ 交付口徑未動（`CGC_SERVER_MTP=0`）；`mtp_off_baseline.json` 一個鍵未動。

## 9. on_fail（按預註冊執行）

**不再投入盒子。** 理由（寫在立項卡裡，不是事後找的）：門 1 的三種輸出 —— FAIL／數值 PASS／
UNRESOLVED —— **導向同一個行動**（都不投 MTP 加速資源、交付口徑維持 MTP off）：
PASS 那一支被 B1 擋掉「可表述為加速」，也被立項 §3「不重啟 MTP 加速預算」擋掉。
⇒ 追加盒子不會改變決策。

⇒ **門 1 結案為 UNRESOLVED（且不再投入盒子）**，不是 FAIL、也不是 PASS。

**若將來要重啟，正確的入口不是「再掃 k」**，而是量 `m` 的構成：
`CGC_MTP_PERF=1`（`run_server.sh:2267` 已 hoist ⇒ `--extra-env` 直接拿）取 `t_draft_ms`，
或用 n-gram draft（`--spec-type ngram-simple`）做診斷：同 k、無 draft 前向 ⇒
`m` 掉 ⇒ 成本是 draft 前向；`m` 不動 ⇒ 成本在 verify 的每 token 路徑。
