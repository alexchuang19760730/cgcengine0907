# 一支臂為什麼會掉：draft 鏈在第一個 measured rep 之後死亡（不是熱）

2026-09-28 · `prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256`（＝ registry `prod25-stream`）
· `delivery` cell（測試卡 §2.5.1：`-p 0 -b 512 -ub 512 -d 512 -n 128 -r 3 --warm-skip 64 -c 4096`）
· k=3（`--spec-type draft-mtp`，不帶 `--spec-draft-n-max` ＝ llama-bench 預設 3）

## 0. 被問的問題

> 查為什麼一支只要 94 秒的 arm 能從 NOMINAL 掉到 HEAVY，並找出能不能把量測窗收在 NOMINAL 內。

兩個答案，而第二個把第一個的框架整個換掉：

1. **它掉下去與熱無關。** 真正發生的是：**第一個 measured rep 之後，MTP draft 鏈整個停止 draft**，
   剩下的 rep 量到的是**純解碼**。熱標籤（MODERATE/HEAVY）與它同時出現，但不在因果鏈上。
2. **「把窗戶收在 NOMINAL」做得到 —— 本輪就做到了（124 次取樣裡 117 次 NOMINAL）—— 而它保護不了
   量測。** 因為殺死數字的那件事在 NOMINAL 下照樣發生。

## 1. 事實：逐階段（`CGC-BENCH-ACCEPT`，兩支獨立臂）

`mean_len = 1 + accepted/rounds`；`mean_len = 1.0` ＝ 每回合一個 token ＝ **沒有 draft 在跑**。

**臂 A（r1.b，k=3，14:26 起跑；該輪 thermal 最差 HEAVY，hist 91/167）**

| 階段 | rounds | drafted | acc | mean_len | 該 rep 的 t/s |
|---|---:|---:|---:|---:|---:|
| rep1 warm_skip | 35 | 97 | 46 | 2.3143 | |
| rep1 **timed** | 16 | 48 | 48 | **4.0000** | **13.07** |
| rep2 warm_skip | 64 | 0 | 0 | 1.0000 | |
| rep2 **timed** | 64 | 3 | 3 | **1.0469** | 5.24 |
| rep3 warm_skip | 64 | 0 | 0 | 1.0000 | |
| rep3 **timed** | 64 | 0 | 0 | **1.0000** | 4.91 |

**臂 B（本輪重跑，14:34 起跑；起跑水平 NOMINAL(0)，全程 117/124 NOMINAL）**

| 階段 | rounds | drafted | acc | mean_len | 該 rep 的 t/s |
|---|---:|---:|---:|---:|---:|
| rep1 warm_skip | 35 | 82 | 43 | 2.2286 | |
| rep1 **timed** | 41 | 90 | 42 | **2.0244** | **9.80** |
| rep2 warm_skip | 64 | 0 | 0 | 1.0000 | |
| rep2 **timed** | 64 | 0 | 0 | **1.0000** | 8.84 |
| rep3 warm_skip | 64 | 0 | 0 | 1.0000 | |
| rep3 **timed** | 64 | 0 | 0 | **1.0000** | 8.79 |

**⇒ 2/2 重現：死亡綁在「第二個 measured rep」，不是綁在時間、也不是綁在溫度。**

## 2. 機制：`llama_decode[0] returned -1` → 該回合產出 0 個 draft

`common/speculative.cpp:374` 的 draft 迴圈：

```cpp
while (n_drafting > 0) {
    ...
    ret = llama_decode(ctx_dft, batch);
    if (ret != 0) { SPC_ERR("llama_decode[%d] returned %d\n", i, ret); break; }
    ++i;
}
```

**第一顆 draft token 就失敗（`i == 0`）⇒ `break` ⇒ 該回合 draft 數 = 0 ⇒ 純解碼。**
臂 A 有 **256 次**、臂 B 有 **264 次** 這行；而 `-1` 從 t≈30 s 起飽和在 **每秒 5 次**，
與純解碼的 ~5 round/s 同量級 ⇒ **幾乎每個回合的第一步都失敗**。

這不是新病，而是**已知病的一個殘餘**：`docs/KEFF_CAP_2026-09-26.md` 記的是 `[1]`（第二步）
那一類 —— 它會讓 `--spec-draft-n-max k` 靜默變成 `k_eff = 1`（`draft_hist {1: 90}`），而那個
已被 `one_position_drafts` 修掉（修後 `[1]` 為 0）。同一份文件明寫：**修完剩下的是 `[0]`**，
「same M-RoPE text with `X == Y`」。本輪量的就是那個殘餘 —— 而它比 `[1]` 更嚴重：`[1]` 給
`k_eff=1`，`[0]` 給 **`k_eff=0`**。

## 3. 熱被排除，而這正是重點

- 臂 A 在熱盒子上跑（最差 HEAVY）。
- 臂 B **從 NOMINAL(0) 起跑，124 次取樣裡 117 次 NOMINAL**，只在最後 4 秒碰到 MODERATE。
- **兩支的 draft 鏈都在第一個 measured rep 之後死掉。**

⇒ 熱標籤是**同時發生**的相關物，不是原因。也順帶更正一個我自己的讀法：先前 60 秒閒置讀到
`1 (MODERATE)` 是**暫時的**（當時你自己的 apps 在跑）；這台盒子**回得到 NOMINAL**，
所以 `harness.py` 的 `tp.wait_nominal(420)` 是**可以滿足**的 —— 而**滿足它並不保護任何東西**。

**真正的共變量在記憶體側，而它每次都被記下來、從來沒有被閘**：

```
memory: launch swap=2656.81 MiB free=278.09 MiB -> end swap=5459.25 MiB worst_swap=5579.25 min_free=16.77 MiB
attribution: verdict=swap, swap_growth=2802.44 MiB, max_swap=5579.25 MiB, max_llama_procs=1
```

**一支 77 秒的臂讓 swap 長了 2.8 GB，free 一度低到 16.8 MiB。** 而 `_box_gate` 只在**起跑前**
量壓縮機與 swap（起跑前是乾淨的、也記成 `[compressor] quiet` 起跑閘）—— **跑中的成長沒有閘**。

## 4. 那這些臂的「k=3 數字」是什麼

它們是**兩條程式碼路徑的混合**：

| | 手臂的 `avg_ts` | 其實是 |
|---|---:|---|
| 臂 A（r1.b） | 7.74 ± 4.62 | 1 個 speculative rep（13.07）＋ 2 個純解碼 rep（5.24, 4.91） |
| 臂 B | 9.14 ± 0.57 | 1 個 speculative rep（9.80）＋ 2 個純解碼 rep（8.84, 8.79） |

（`platform_ts` 也沒救：它是「丟掉第 1 個 rep 的平均」＝ **正好丟掉唯一活的那個**。
臂 A `platform_ts 5.08`、臂 B `8.82`。）

**損害幅度不固定**，所以「用某個修正係數補回來」不成立：NOMINAL 那一輪的死亡只值 **−10%**，
熱那一輪值 **−60%**。兩件事混在一起：draft 是否活著，以及盒子的狀態。

**還有一件不能當成好事的事**：臂 A 的 rep1 `mean_len = 4.0000` 是 **k+1 的硬上界**
（16 回合、48 drafted、48 accepted ＝ 全數接受）。它是 13.07 t/s 的來源，但它不是一個
「好」的 spec 鏈，而是**完全接受**的退化情形 —— 在把它當基準之前，那份本身的合理性要先被
檢查，不能因為它數字漂亮就跳過。

## 5. 修了什麼

| 檔 | 改動 |
|---|---|
| `scripts/check/draft_liveness.py`（新） | 讀 `CGC-BENCH-ACCEPT` 的逐階段 `phase` 區塊，**逐 measured rep** 判 `mean_len >= 1.5`。無 ACCEPT 行 → `UNREADABLE`（**不是 pass**）；timed 區塊數 ≠ reps → 拒。**selftest 10/10**，且對 r1 與本輪兩份真實 log **都判拒** |
| `scripts/check/k3_pair_cert.sh` | ① 每支臂 `--workdir $OUT/logs/r${r}_${pos}_k${k}`（見下）② 跑完後呼叫 `draft_liveness.py --expect-timed $REPS`，非 0 即 `exit 3`；③ abort 訊息原本指向 `$OUT/logs/<arm>/` —— **一個腳本從來沒建立的目錄**，現在真的建立了 |
| `scripts/check/harness.py:1024` | `snap["thermal"]["lv"]` 讀的是 `t.get("lv")`，而 `stamp()` 的鍵是 **`level`** ⇒ **每一份 harness 產物的 `lv` 都是 `None`**（本輪實物：`{'label': 'NOMINAL', 'lv': None}`）。而 `None` 正是那個模組的 `UNREADABLE` 值 ⇒ 缺陷長得像「儀器缺失」而不是「鍵寫錯」 |
| `scripts/check/lane_watchdog.py:79` | 同一個鍵的同一個錯：`st["thermal_level"] = t.get("lv")` ⇒ watchdog 的數值熱水平**每一筆都是 None** |

**為什麼 `--workdir` 是閘門的前提（＝另一個缺陷）**：llama-bench 的 stderr 檔名是
`llama_bench_{arm}_{shape}`，`shape` 只有 `p0_n128_d512_r3` —— **不含 `--spec-type`、也不含
`--spec-draft-n-max`**。r1 的兩支臂只差在 k，所以它們**同名**，k=3 的 log **覆蓋了 k=2 的**。
`/tmp/harness_bench` 現在對那個 profile 只有一份檔（內容是 k=3 的），**k=2 臂的逐 rep 證據永久
遺失**。per-arm `--workdir` 讓這件事不可能再發生，也讓 liveness 閘門有東西可讀。

## 6. 沒有回答的

- **k=2 臂的 draft 活著嗎？** 不知道 —— 它的 log 被覆蓋了。若 k=2 也在第一個 rep 之後死，
  那 r1 那一對的「k=2 較快」整個是**不等量的 draft 存活率**造成的，與 k 無關。
- **死亡是不是記憶體觸發的？** 目前只有相關：swap +2.8 GB／77 s、`verdict=swap`。要定因需要一支
  **記憶體受控**的臂（例如起跑前把 pool 預算壓低、或把 `min_free` 當成受測變數），而不是再跑一支
  「看看會不會發生」的臂。
- **16.4% 仍未認證。** 這一輪沒有產生任何可引用的 t/s 讀數。

## 7. 所以下一步的正確形狀

不是「把窗戶收在 NOMINAL」，而是：

1. **逐 rep 驗 draft liveness**（已接進驅動、離線 10/10、對兩支真實臂都判拒）。
   這是**不變量**，不是啟發式：一個 dead rep 不是 k=3 的樣本。
2. **把「跑中」的 swap 成長變成被閘的元素**，而不是標籤。目前 `_box_gate` 只量起跑前
   （壓縮機 quiet、swap 存量當標籤），而實測的成長是 2.8 GB/臂、free 觸底 16.8 MiB。
3. **接受一件事：rep 之間不可能冷卻。** `--delay` 是**每個 test 一次**（`llama-bench.cpp:3353`，
   在 reps 迴圈 `:3434` **之外**）⇒ 它冷不到 rep 之間；而 `cell_contract.py` **明文拒 `reps=1`**
   ⇒「一個 rep 一次啟動、之間冷卻」在合約上不可表達。**所以 rep 不可交換是這個 cell 的性質，
   只能偵測、不能移除。** 任何 paired 統計都必須先通過第 1 點。

## 8. 後續（2026-09-28 同日）：第一步的 `-1` 找到原因並修好了

第 1 點那個 `-1` 的**原因**與**修法**見 `docs/MTP_DRAFT_FIRST_STEP_2026-09-28.md`：原因是
llama-bench 的 null sink 把引擎的解釋丟了（所以 `-1` 不帶理由），而失敗本身是 draft KV 的
**跨 generation 尾巴**，而在共用 KV 下沒有任何人清它。修完後同一支臂的 liveness 從
**rc=3（rep 2,3 死）** 變成 **rc=0（三個 measured rep 全活）**，M-RoPE 拒絕事件 268 → 28。
**項目 1（逐 rep 驗 liveness）仍然成立且仍是必要條件** —— 它正是擋下這一類的閘門，只是現在
它過了；剩下的生成中 `X == Y` 類（28 事件，~12%）不毀臂，但仍被這道閘門與 log 記著。
