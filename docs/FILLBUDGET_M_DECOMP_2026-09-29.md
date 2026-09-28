# fill 預算 ＋ E 軸：一場次 2 趟同時收兩個未知數（`e-fillbudget-m-decomp`）

開卡／執行：線 A，2026-09-29 01:2x–01:4x。前置 commit `ab8bb9e36`（fill 預算閉合）。
結果段見 §4／§5（跑完填入）。

## 0. 一句話

P1 把「fill 占多少 ms/step」從**沒有任何可信實測**變成實測（先修儀器口徑，動 `src/` 一處、
env-gated）；P2 用**已存在**的 `CGC_MTP_PERF` 把 `m` 從單點反解值變成**構成**（draft／verify 拆分）。
兩趟共用同一個 build，正好等於盒子一場次的上限。

## 1. P1：修 EBTIMER 口徑（唯一的 `src/` 改動）

### 1.1 壞法（閉合白皮書 §2）

| 現象 | 證據 |
|---|---|
| 窗口跨段 | 同一份 log 裡 `n_sum/41` 既有 **8.00**（純 decode）也有 **14.9–21.3**（混 prefill） |
| `calls` 不是觀測值 | `fprintf` 第 4 實參是 `nl`（=`slot_owner.size()`）⇒ **恆 41** |
| 讀數與預算矛盾 | fill 臂 `step_usec` = 170–199 ms，而 decode step 預算 86 ms |

### 1.2 改法（`src/llama.cpp/src/llama-expert-cache.{h,cpp}`）

- `cgc_eb_timer` 構造多收一個 `bool is_prefill` ＝ caller 的 **`defer_decode_protect`**
  （`llama-context.cpp:7678` 傳 `cgc_current_phase == CGC_PHASE_PREFILL`；`:7435` 傳 false）
  ⇒ **不必改 `ensure_batch` 簽名**就拿到段別。
- 每次呼叫累加 `eb_nseg_prefill` / `eb_nseg_decode`（header 兩個新 atomic）。
- flush 時印 **`seg=decode|prefill|mixed` ＋ `n_pf=`／`n_dec=`**，**加在行尾**
  ⇒ 既有 `EB_RE`（`scripts/check/{column_census,fill_onpath_ab,pair_ab}.py`，以 `n_sum=(\d+)`
  結尾且用 `.search`）**不必改**。
- 預設行為不變（只在 `CGC_EB_TIMER` 存在時計時，不加 synchronize）⇒ M1/M2/M3 無影響。

### 1.3 判讀判據（預註冊）

`sig`：`seg=decode` **且** `n_sum == 328`（41 層 × 8 expert）⇒ 該行 `step_usec` 就是
「一步 41 層 `ensure_batch` 的牆鐘和」，且應 **< 86.13 ms**。
⇒ 有效吞吐 = 17.3 MiB ÷ fill ms；落點 = 1000 / (86.13 − fill_ms + J2)。

## 2. P2：E 軸 —— `m` 的構成（**不用動 `src/`**）

### 2.1 本輪更正（method_culpa #3）

我上一輪宣布「`CGC_MTP_PERF` 不存在」——**錯**。它在 `common/speculative.cpp:3179`，
環境門 `CGC_MTP_PERF`，欄位正是需要的：`t_draft_ms` / `t_accept_ms` / `ms_per_round` /
`emit_tok_per_round` / `acc_rate` / `gen_tok_per_round`。
（只搜 `src/llama.cpp/src/` 而不搜 `common/` ⇒ 假陰性；它也不在 `libllama.0.dylib`
而在 bench 側產物。）

### 2.2 allowlist 坑（新查到）

`CGC_MTP_PERF` 的 allowlist 條目在 `run_server.sh` 的 **`if [ "$SERVER_MTP" = "1" ]` 區塊**內
（:2425，與 `CGC_MTP_DBG`、`CGC_MTP_SAMPLER_PARITY` 同塊），而 `SERVER_MTP` 由 CLI
`--spec-type draft-mtp` 設定、**不是 env**
⇒ **resolve-only 一律回報 dropped（假警報）**；送達只能用見證行驗。

### 2.3 判讀判據（預註冊）

`m := ms_per_round / (eval_ms_per_round − ms_per_round)`。
⛔ 若 `t_eval` 的分母含混（無法確定是否含 draft ctx）⇒ **不硬算**，登記 UNDECIDABLE。
⛔ P2 的 t/s **不可與 MTP off 比**（已判死）⇒ 本卡只取構成比值。

## 3. 執行

- build：`cmake --build src/llama.cpp/build --target llama-bench -j 8`（54 s，閘門已開）。
- 兩趟（`Backup/phase_decomp/fillbudget_m/{p1,p2}`）：
  - P1 `--arm "prod-new:CGC_EB_TIMER=1"`
  - P2 `--arm "prod-new:CGC_SERVER_LAYER_CAPS=40-40:16;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;CGC_MTP_PERF=1" --spec-type draft-mtp`
- 背景行程要 `export PYTHONPATH=/opt/homebrew/lib/python3.14/site-packages`。

## 4. P1 結果（**推翻了這條線的期望值**）

產物：`Backup/phase_decomp/fillbudget_m/p1/`（交付 cell `-p 2048 -n 128 -d 512 -r 3 -b/-ub 5632`）。

### 4.1 儀器修復確認 ✅

| 判據 | 結果 |
|---|---|
| 新格式行 vs 舊格式行 | **385 / 0** ⇒ 跑的是新 binary（`seg=` 生效） |
| `seg` 分佈 | **decode 385／385**，`n_pf=0`、`n_dec=40` ⇒ **交付 cell 沒有跨段問題**（舊 cell 的 `n_sum/41` 8.00 vs 18.3 是 `p0_n128_d512` 特有的） |
| `calls` / `n_sum` | `calls=40`、`n_sum=320` ⇒ **交付 cell `n_layer = 40`（不是 41）、k = 8** |

⇒ ⛔ **修正 J2**：40×8 = **320 op/step**（先前按 41 層推的 328 作廢）⇒ 1.60–3.20 ms/step。

### 4.2 fill 實測

| 量 | 值 |
|---|---|
| `step_usec`（一步 40 層 `ensure_batch` 牆鐘和） | p10 **1.87** / **p50 5.41** / p90 12.72 / max 79.0 ms |
| miss/步 | **6.31**（miss 率 **1.97%**） |
| 趟末統計 | `n_layer=40 req=128920 hits=123935 misses=4985 hit_pct=96.13 read_mib=2614.0` |
| 由統計反推 | req/320 = 402.9 步 ⇒ **6.49 MiB/step**；MiB/miss = **0.524** |
| **有效吞吐** | 6.49 MiB ÷ 5.41 ms = **≈1.2 GiB/s** |

⚠ 舊 cell 是 17.3 MiB/step、MiB/miss 1.083；交付 cell 是 6.49、0.524 ⇒ **不可比**（舊 cell
`p0`、無 prefill 預熱）。⚠ `MiB/miss` 差 2× 的成因未查（同一模型；可能是 `read_mib` 口徑或
expert 大小不同）⇒ **存疑，不拿它做定點推論**。

### 4.3 落點（這條線的期望值）

1000 / (86.13 − 5.41 + J2) ⇒ **11.92 – 12.15 t/s（+2.6% ~ +4.6%）**。

⛔⛔ **低於 MDD 8.5%** ⇒ 即使完全重疊，`async fill ＋ per-expert 重算` 在 16 GB 盒子上
**量不出來**。而且本趟是髒窗（thermal HEAVY、swap +3261 MiB、tg 7.04 不可引用），
髒窗讓 fill **偏慢** ⇒ 乾淨窗的收益只會**更小**。

⇒ 結論方向穩健：**這條線的收益從「12.9→19.4」塌到「~12」**。

## 5. P2 結果（儀器可用，但窗崩了 ⇒ UNDECIDABLE）

產物：`Backup/phase_decomp/fillbudget_m/p2/`。rc=0、跑完，但：

| 指標 | 值 |
|---|---|
| tg | **0.33 ± 0.05 t/s**（崩；對照 P1 的 7.04、錨點 11.61） |
| hit% | **47.2**（P1 是 96.1） |
| wall | 1100 s |
| thermal | launch NOMINAL、worst **HEAVY**；swap +1976 MiB |
| Metal 工作集閘 | **REFUSE**：池 8192 + Metal 駐留 12379 + 保留 1024 = 21595 MiB vs 上限 11453 ⇒ **超額 10142 MiB** |

### 5.1 儀器本身 ✅ 可用（這是 P2 的真正收穫）

`CGC_MTP_PERF` 6 行全部取出（末行＝累計）：

```
calls_draft=144 gen_tokens=336 acc_tokens=245
t_draft_ms=5123.0  ms_per_round=35.577
acc_rate=0.7292  gen_tok_per_round=2.333  emit_tok_per_round=2.701
```

⇒ **送達方式已坐實**：arm 必須帶 `CGC_SERVER_MTP=1`（見 §2.2）。

### 5.2 但 `m` 判 **UNDECIDABLE**

`m := ms_per_round / (eval_ms_per_round − ms_per_round)` 需要 `eval_ms_per_round`，而
本趟 tg 0.33 t/s ⇒ `eval_ms_per_round ≈ 2.701 × 1000/0.33 ≈ 8.2 s`，是 thrash 下的數字，
**與 draft 的真實成本不可分**。用錨點 86.13 ms 去當 plain step 也不可比（ON/OFF 已判死）。
⇒ 按預註冊 `undecidable`，**不硬算**。

⛔ 所以 `ms_per_round = 35.6 ms` 這個數**只能證明儀器會吐值，不能當 m 的構成**。

## 6. 結論與下一步

1. **P1（fill 預算）✅ 收斂，而且結論是負的**：交付 cell 的 fill 只占 **5.41 ms/step**
   ⇒ 全重疊也只到 **11.92–12.15 t/s（+2.6%~+4.6%）**，**低於 MDD 8.5%**
   ⇒ `async fill ＋ per-expert 重算` 在 16 GB 盒子上**量不出來**。
   加上它要動 `src/` 碰 M1/M2/M3 ⇒ **建議結案：不做**（J1 原理可行的事實保留在卡上）。
2. **P2（E 軸）UNDECIDABLE**：儀器與送達方式都已坐實，缺的是**乾淨窗**。
   ⇒ 下次要量 `m`，單跑一趟即可（本場次的第 2 趟必崩，見下）。
3. ★ **盒子新事實**：本輪兩趟都撞到 **metal 工作集 REFUSE**（池 8192 + Metal 駐留 12379
   + 1024 = 21595 > 上限 11453，超額 10142 MiB）⇒ **一場次的第 2 趟必然崩**
   （P1 是第 1 趟 ⇒ tg 7.04；P2 是第 2 趟 ⇒ tg 0.33，hit% 96→47）。
   ⇒ **任何「兩趟」的卡都必須假設第 2 趟只能拿計數器、不能拿 t/s**。
4. 未解：`MiB/miss` 交付 cell 0.524 vs 舊 cell 1.083（差 2×）成因未查。
