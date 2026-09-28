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

## 6. P3 結果（新場次第 1 趟，env 已修好）—— 窗仍崩，但拿到一個**與閘門無關**的單邊結論

產物：`Backup/phase_decomp/fillbudget_m/p3/`。arm 帶 `CGC_SERVER_MTP=1`（§2.2 的解法），
`--spec-type draft-mtp` 已上命令行，rc=0、跑完。

| 指標 | 值 | 閘門 |
|---|---|---|
| tg | **0.506 t/s** | ⛔ (b) 需 ≥ 8.0 |
| hit% | **51.8** | ⛔ (c) 需 ≥ 90 |
| `CGC-MTP-PERF` | 12 行（＝6 唯一 × 2 reps）、calls_draft=124 | ✅ (d) |
| rc / 完整 | rc=0、incomplete=False | ✅ (a) |

⇒ 四條預註冊閘門 **2 條 FAIL** ⇒ 按 `validity_gate`，**`m` 與 `v` 判 UNDECIDABLE，不硬算**。

### 6.1 ★★ 但有一個結論不需要閘門：**m ≤ 0.34**，09-28 的反解被反駁

理由是單邊的，與窗無關：**thrash 與冷啟動都只會把計時「吹大」，不會吹小**
（`t_draft_us` 是 wall clock，等頁只會更久）⇒ 實測 `T_draft` ≥ 真值 ⇒ `m` 的上界成立。

| 讀法 | T_draft | m = T_draft/86.13 |
|---|---|---|
| 末行累計均（`ms_per_round`） | 35.08 ms | **≤ 0.407** |
| **差分後的穩態增量** | 23.7–29.5 ms | **≤ 0.34** |

兩種讀法都 **< 0.60** ⇒ ⛔ **09-28 由 tg 反解出的 `m ∈ [0.60, 0.84]` 偏高、應予作廢**。

⇒ **E 軸的提問要改寫**：不是「為何一個 draft token 要 0.6–0.9 個 plain step」，
而是「**為何一次 verify forward 比一個 plain step 貴**」——
draft head 只值 ≤0.34 個 plain step，每輪成本的主導項是 **verify（v）**。

⛔ `v` 仍是 UNDECIDABLE：`T_ver = T_round − T_draft` 裡的 `T_round` 要用到 tg，
而本趟 tg 是 thrash 值（⇒ v ≈ 72，純垃圾）⇒ **必須等乾淨窗**。

### 6.2 ⚠ 新儀器坑：`ms_per_round` 是**累計均值、未收斂**，必須差分

P3 的六行（唯一值）`ms_per_round` **單調下降**：55.91 → 48.94 → 42.05 → 38.83 → 37.00 → 35.08。
它沒有收斂 ⇒ **末行高估穩態成本約 20–50%**。差分相鄰行（Δcalls × 增量）才是穩態：

```
P3:  Δ27輪 43.25 | Δ19輪 24.30 | Δ18輪 26.66 | Δ20輪 29.14 | Δ18輪 23.74  ms/輪
P2:  Δ25輪 37.85 | Δ19輪 40.20 | Δ22輪 40.18 | Δ24輪 28.59 | Δ33輪 29.48  ms/輪
```

⇒ 穩態帶 **24–30 ms/輪**（P2、P3 各自的最後兩段一致）。
另：P3 的 12 行是 **6 個唯一值各印兩次**（reps），直接取「末行」前要先去重。

### 6.3 ⛔ 撤回一個假設（method_culpa #4）

我一度從「P2 的 35.577 vs P3 的 35.076 只差 1.4%，而 tg 差 55%」推出
「`ms_per_round` 是 thrash 不變量的證據」。**撤回**：兩趟的 `calls_draft` 相近
（144 / 124）、且序列都未收斂 ⇒ 一致是**累計結構相似**使然，不是不變性證據。
（真正的穩態值來自 §6.2 的差分，不是末行。）

### 6.4 ★ 盒子事實再更新：**不只第 2 趟崩，新場次第 1 趟也崩**

P3 是新場次（無佔用、thermal 0、起跑前 81% free）的第 1 趟，仍然
tg **0.506**、hit **51.8**、swap 用掉 **7186/8192 MiB**。
⇒ 機器在上一場次之後**沒有真正回收**（起跑時 swap 已佔 3567 MiB、free 僅 ~1.2 GB）。
⇒ **MTP on 臂目前拿不到可用的 t/s，只能拿計數器**；量 `m` 的點估計要等機器真的回收。

## 7. 結論與下一步

1. **P1（fill 預算）✅ 收斂，而且結論是負的**：交付 cell 的 fill 只占 **5.41 ms/step**
   ⇒ 全重疊也只到 **11.92–12.15 t/s（+2.6%~+4.6%）**，**低於 MDD 8.5%**
   ⇒ `async fill ＋ per-expert 重算` 在 16 GB 盒子上**量不出來**。
   加上它要動 `src/` 碰 M1/M2/M3 ⇒ **建議結案：不做**（J1 原理可行的事實保留在卡上）。
2. **P2／P3（E 軸）**：`m`、`v` 的**點估計 UNDECIDABLE**（兩趟窗都崩，見 §6.4）；
   但 **`m ≤ 0.34` 的單邊上界成立** ⇒ 09-28 的 `m∈[0.60,0.84]` 作廢，
   E 軸的提問改寫為「verify 為何比 plain 貴」（§6.1）。
   ⇒ 儀器與送達方式已坐實，**剩下的只是等一個乾淨窗**；重跑時記得**差分**取穩態（§6.2）。
3. ★ **盒子新事實（09-29 修訂）**：不只是「一場次的第 2 趟必崩」——
   **新場次的第 1 趟也會崩**（P3：tg 0.506、hit 51.8）。
   兩趟都撞 **metal 工作集 REFUSE**（池 8192 + Metal 駐留 12379 + 1024 = 21595
   > 上限 11453，超額 10142 MiB），而機器在場次之間**不回收**（起跑時 swap 已佔 3567 MiB）。
   ⇒ **任何 MTP on 臂目前只能拿計數器，不能拿 t/s**。
4. 未解：`MiB/miss` 交付 cell 0.524 vs 舊 cell 1.083（差 2×）成因未查。

## 8. ⛔ 結論 2、3 的「等乾淨窗」是錯的（09-29 02:5x，method_culpa #5／#6）

上面的第 2、3 條寫「剩下的只是等一個乾淨窗」「等機器真的回收」。**兩條都撤回**，
因為「等什麼」本身就定錯了。

### 8.1 ⛔ 等待條件 `swapusage free > 6 GB` 作廢（culpa #5）

- **(a) 不可達**：`vm.swapusage` 的 total 是**動態**的，此刻只有 **5120 MiB**
  ⇒ `free > 6 GB` **永遠不成立**（寫那條時 total 是 8192）。
- **(b) 指標禁用**：09-28 已裁定「macOS 的 swap used 是**歷史累積高水位**，不是佔用量」
  （`.workbuddy/memory/2026-09-28.md:1016`）⇒ 我們自己禁過用它判髒窗。

⇒ 起跑指標改用 harness 自己的 `memory.launch.pages_available_mb`
（＝ free＋inactive＋speculative＋purgeable）。

### 8.2 ⛔ 而且「起跑可用記憶體」也不具鑑別力（122 趟產物的劑量反應）

| MTP-on 趟 | 起跑 `pages_available_mb` | 趟內 pageins Δ | tg | wall |
|---|---|---|---|---|
| on_k1（22:06） | **7703** | 1.67 M | **11.74** ✅ | 95.5 s |
| on_k1_witness（22:39） | **6964** | 1.60 M | **11.11** ✅ | 88.7 s |
| on_k3（22:25） | **9460（更高！）** | **23.59 M** | **0.39** ❌ | 1010.9 s |
| P3 | 7985 | 21.12 M | 0.51 ❌ | 983 s |
| P2 | 7317 | 25.23 M | 0.33 ❌ | 1099 s |

⇒ **9.46 GB 照崩、6.96 GB 照活** ⇒ 起跑可用記憶體**不是**變因 ⇒ 「等回收」沒有停止規則。

### 8.3 ★★ 真因：P2／P3 用的是**錯的臂**（culpa #6）

09-28 唯一存活的 ON 臂是 `on_k1`，它比 P2／P3 **多帶一個 `--spec-draft-n-max 1`**：

| | on_k1 | on_k3 | P2／P3 |
|---|---|---|---|
| `--spec-draft-n-max` | **1** | 3 | **未帶（null）** |
| `k_eff`（實測 `gen_tok_per_round`） | ~1 | 3 | **2.333** |
| tg | 11.74 / 11.11 | 0.39 | 0.33 / 0.51 |
| hit% | 91.4 / 91.8 | 77.0 | 47.2 / 51.8 |
| `file_reads` | **97 K** | **2.80 M（29×）** | thrash 同類 |

而記憶裡那條「MTP on 存活配方」**漏寫了 `--spec-draft-n-max 1`**（只記了三個 env）
⇒ 我照抄它跑 P2／P3 ⇒ 落在 on_k3 那一類。

⇒ **P2／P3 的崩不是髒窗，是 k_eff ≈ 2.33 的 thrash**；變因是 k_eff，不是回收。
⇒ 不必等機器：**改臂就好**（P4）。

---

## 8. P4（2026-09-29 03:12，k=1 修正臂）—— 也崩；「記憶體無鑑別力」這條**作廢**

P4 臂＝存活配方全上：`CGC_SERVER_MTP=1`＋`CGC_SERVER_LAYER_CAPS=40-40:16`＋
`CGC_DRAFT_CTX_ALIGN=1`＋`CGC_DRAFT_SMALL_BATCH=1`＋`CGC_MTP_PERF=1`，CLI 帶
`--spec-type draft-mtp --spec-draft-n-max 1`（cmd 行已見證，n_max **確實送到**）。

起跑條件是這幾天最好的一次：`swap total = 0.00M`（**連 swap 檔都沒分配**）、thermal
`NOMINAL lv=0`、壓縮機 `compressions 0.00 / pageouts 0.01 MiB/s`、free% 75%。

**結果：仍然崩。** 已跑到 8 分鐘時手動中止（健康趟 wall ≈ 95 s）：

| 判據 | 正常（+170 萬／趟） | 崩（+2360 萬／趟） | **P4 實測** |
|---|---|---|---|
| 趟內 pageins | +1.7 M | +23.6 M | **+11.74 M（8 分鐘内 426,634 → 12,168,045）** |
| swap used | ~4–5 GiB | thrash | **8.33 GiB（且還在長）** |
| wall | ~95 s | ~1000 s | **>480 s（未完）** |

### 8.1 劑量反應（同一 k=1 內比較）—— 這才是真的鑑別力

| 趟 | launch `pages_available_mb` | k_eff | 結果 |
|---|---|---|---|
| on_k1（09-28） | **7703** | 1 | ✅ tg 11.74／hit 91.4／wall 95.5 s |
| **P4（今天）** | **6376** | 1 | ❌ thrash |
| off_a（09-28） | 6612 | —（MTP off） | ✅ tg 10.67／hit 96.2 |
| on_k3（09-28） | 9460 | 3 | ❌ tg 0.39 |

⇒ ⛔ **撤回 09-29 03:0x 那條「起跑可用記憶體也無鑑別力（9460 照崩、6964 照活）」**：
那兩個點是**跨 k 比出來的**，被 k_eff 混擾了。**在 k=1 之內，記憶體是鑑別力**
（7703 活／6376 崩），門檻落在 `server_window.NEED_MB = 8000` 附近 ⇒
**那道閘沒設錯；是我們先前把它判成「不鑑別」判錯了。**

⇒ ⛔ 連帶撤回「不必等機器，改臂就好」：**臂改對了仍不夠，還要有 ~8 GB 可回收**
（當下可回收 6.38 GB ＝ `Pages free 4181 + inactive 402784 + purgeable`）。

### 8.2 P4 對「decode 會不會變快」的答案是：**不會，一個 token 都不會**

P4 是**量測**，不是優化。產物只有 `m`（draft head 攤薄係數）與 `v`（verify forward 相對
plain step 的成本）兩個數字，用途只有一個：把「MTP 到底值不值得再投」從 UNRESOLVED
變成有數字。**它不改 kernel、不改圖、不改提交路徑。**

而且就算 P4 跑通、結論是「MTP 值得」，**也不得寫成「decode 提速 X%」** —— 門 2 已封
（M2 FAIL：greedy 下 ON／OFF 輸出不同 ⇒ 是**不同的輸出函數** ⇒ t/s 不可互比），
交付口徑維持 **MTP off／錨點 11.61**。
