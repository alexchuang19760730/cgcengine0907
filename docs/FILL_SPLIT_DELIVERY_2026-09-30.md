# 交付 cell：每 miss 918 µs 的「等」逐桶分解（`CGC_FILL_SPLIT`，2026-09-30）

**問題**：§69 把交付 cell 的 fill term 定價成一個**區間**（儀器 6.79／牆鐘 20.68 ms/step），
§70 再把它換算成 **0.756 ms/miss**，並推論「98.72% 是固定開銷（不是位元組）」。但「固定開銷」
是一個**桶的名字，不是一個數字**：那一桶裡可能住著

1. **開檔**（每次 pread 前 `open()`，典型的 5–30 µs/次）；
2. **解碼**（pread 本身：檔案冷熱、page 是否被換出、裝置忙不忙）；
3. **喚醒路徑**（condvar 交接：submit → worker 醒 → 做完 → 呼叫端醒）。

三者的修法完全不同（前者＝別 open、中者＝少讀／早讀／讀大塊、後者＝換同步機制或加 worker），
所以**單一個和數無法排序要先修哪一個**。這一輪把那一桶拆開量。

---

## 1. 儀器（引擎側，預設關）

`llama-expert-cache.cpp` 的 `fill_segments_pool`（交付 cell 的 decode 唯一填充路徑：
`ensure_batch → fill_pool_direct_collect → fill_segments_pool → 8-worker pool`）加了
`CGC_FILL_SPLIT=1`（**環境變數沒設就一個分支都不進**；`run_server.sh` 已轉送，漏轉會被
`llama_bench_matrix.arm_env_dropped()` 抓成 DROPPED）。桶全部由**呼叫端線程**累計
（可與牆鐘相比），只有兩個戳記由 worker 落：

| 桶 | 誰量 | 覆蓋什麼 |
|---|---|---|
| `madvise_us` | 呼叫端 | 提交**前**的 P1 discard（prod 預設不開） |
| `build_us` | 呼叫端 | 提交**前**：排序／run-merge／推 job（持 `pool_m`） |
| `advise_us` | 呼叫端 | 提交**後**的 `fcntl(F_RDADVISE)` 提示 ⇒ **與 worker 重疊**，永不計入 wait |
| `wake_in_us` | 兩者 | `submit → 第一個 worker 拿到 job`（線程喚醒＋排在既有工作後面） |
| `span_us` | 兩者 | `first_dequeue → 最後一個完成`（pread 在飛的窗口） |
| `wake_out_us` | 兩者 | `最後完成 → 呼叫端重新跑`（含最後一次的 notify） |
| `wait_us` | — | **恆等式**：`wake_in + span + wake_out`（本輪 6 臂**逐臂精確成立**） |

**「開檔」刻意不是一個桶**：這條路上沒有 per-fill 的 `open()`——`cache->files` 是 init 開好的
`FILE*`，worker 只做 `pread(fileno(f), …)`。唯一的 per-fill `open()+pread` 在
`cgc_exact_cache_verify_post_fill()`（`llama-expert-cache.cpp:949`），由 `CGC_EXACT_CACHE_VERIFY`
把守（prod 預設 off），檢查器的閘 6 用 arm env 直接證明它沒被武裝。

## 2. 這一輪跑了什麼（交付 cell，`prod-new`，6 臂 × 3 rep，同一窗口）

```
scripts/check/llama_bench_matrix.py --arms <6 臂> --prompt 0 --gen 128 --depths 512 \
    --reps 3 --ctx-size 0 --warm-skip 64 --fixed-fill-seed 1 --cell delivery \
    --json Backup/fill_split_delivery_2026-09-30/ab.json
```

| 臂（額外 env） | t/s | 每 miss 的 wait |
|---|---:|---:|
| （無，＝參考臂） | 10.61 ± 0.53 | **918.2 µs** |
| `LLAMA_EXPERT_CACHE_NO_RDADVISE=1` | 10.43 ± 0.26 | 894.8 µs |
| `CGC_DBUF=0`（關掉 step-ahead） | 10.56 ± 0.57 | 924.6 µs |
| `CGC_SERVER_WORKERS=16` | 10.83 ± 0.86 | 938.7 µs |
| `CGC_POOL_MADVISE=1`（P1 開） | 10.46 ± 0.49 | 908.2 µs |
| `CGC_EB_NOFILL=1`（不讀位元組） | 13.75 ± 0.18 | **0（全 0，陰性對照）** |

## 3. 答案：三個桶各佔多少

參考臂（交付 cell、乾淨窗口、`attribution=none`）：
`batches=3189  misses=4287  segs=12861  jobs=12816`（run-merge 只省掉 **45** 個 job ＝ 0.35%）。

| 桶 | 總計 (µs) | **每 miss (µs)** | 佔 wait | 在哪裡 |
|---|---:|---:|---:|---|
| 開檔 `open()` | 0 | **0.0** | 0% | 路上沒有 per-fill open |
| `madvise`（P1） | 2 080 | 0.5 | — | 提交前（µs 量子以下） |
| `build`（sort/merge） | 22 355 | 5.2 | — | 提交前 |
| `advise`（rdadvise） | 487 182 | 113.6 | — | 提交後，**與 worker 重疊** |
| `wake_in`（喚醒） | 31 265 | **7.3** | **0.8%** | wait 內 |
| **`span`（pread 在飛）** | 3 896 301 | **908.9** | **99.0%** | wait 內 |
| `wake_out`（喚醒） | 8 740 | **2.0** | **0.2%** | wait 內 |
| `wait`（＝三者之和） | 3 936 306 | **918.2** | 100% | 恆等式已驗 |

**⇒ 交付 cell 上每 miss 918 µs 的等，99.0% 是 pread 本身，1.0% 是喚醒路徑，0% 是開檔。**
每批次：1 234 µs 的等 ÷ 1.34 miss ÷ 4.02 job；`wait_max`（單批最久）10 550 µs。

## 4. 三個獨立交叉驗證（為什麼這不是儀器的自言自語）

1. **恆等式**：`wake_in+span+wake_out == wait` 在 6 臂上**精確**成立（不是湊的：三者由
   同一次提交的三個戳記決定，誤差只有在 µs 量化上）。
2. **陰性對照**：`CGC_EB_NOFILL=1` 臂（在函式頭就返回）**所有桶全 0**、`read_mib=0`，
   而它的 `misses` 仍是 3 909（計數來自 `ensure_batch`）⇒ 桶真的只量填充路徑。
3. **`pread_usec` 差分**：A−NOFILL `= 16.18 s / 12 780 讀 ＝ 1 266 µs/讀`，而
   `span / batches = 3 896 301/3 189 = 1 222 µs/批` ⇒ 隱含並行度 = 16.18/3.90 = **4.15**，
   與 `jobs/batches = 4.02` 相符 ⇒ `span` 就是 pread 的牆鐘，不是排程抖動。
   每次讀取 **0.360 MiB**（`read_mib` 4 628.1 MiB / 12 861 段）⇒ **284 MB/s 每條流**。

順帶一個**口徑更正**：`final stats` 的 `file_reads=26 520 / pread_usec=380.0 s` 裡
**絕大多數是 prefill 的 slab 串流**（NOFILL 臂照樣有 13 740 讀／363.8 s，因為那條路上閘的上游），
decode 的 demand fill 只有 **12 780 讀／16.18 s**。所以 `cache: us/job 14150` 那一個欄位
**不是**交付 miss 的單位成本（差 11×）。

## 5. 哪一段可回收？（這一輪真正買到的東西）

**用這張表可以淘汰的機制（它們都只值 1% 或更少）**：

- **換同步機制／加 worker**：喚醒合計 9.3 µs/miss（1.0%）。`CGC_SERVER_WORKERS=16` 的
  `wake_in` 反而漲到 72 972 µs（鎖競爭），`span` 不動 ⇒ **worker 數不是槓桿**。
- **關掉 per-job 的 `F_RDADVISE` 提示**：`advise_us` 487 ms 是呼叫端的**真實**時間
  （113.6 µs/miss），但它全部**藏在 wait 裡面**（每批 153 µs vs 等 1 234 µs）⇒ 關掉它
  （NO_RDADVISE 臂）對 `span` 只 −0.7%、t/s 沒有可量到的差 ⇒ **提示是免費的，不是成本**。
- **P1 discard（`CGC_POOL_MADVISE=1`）**：桶 ≤ 1 µs/批（µs 量子以下），t/s 無差 ⇒
  「pread 的目的頁要先被換出」這一項在交付 cell 上**買不到東西**。
- **step-ahead（`CGC_DBUF=0`）**：關掉它 wait 只 +0.7%（924.6 vs 918.2）⇒ 這一格上
  **DBUF 幾乎沒有在替 demand fill 爭取時間**（不然關掉會明顯變差）。

**指向的機制（唯一有算術的兩條）**：

1. **把讀取提早**（不要在第一個需要它的 hook 上才發出）。量到的結構限制是：
   每批只有 **4.02 個 job**、卻有 8 個 worker ⇒ 一半閒著；把獨立的讀取提早一代（layer l−1
   或上一步）並行，同樣的 16.18 s（worker 累計）可以在 2.0 s 的牆鐘內跑完，而不是 3.90 s。
   這正是 DBUF／prerouter 想做的事，而上面第 4 條說它**現在沒在做**。
2. **少讀**（把 miss 從 4 287 壓下去）：每一 miss 的值 = 3 讀 × 1.27 ms；這一格
   `compulsory=3 670` ⇒ 命中率那一側還是主項（但受記憶體預算封頂，見 `budget_gate`）。

**天花板（誠實）**：把整個 fill term 100% 藏掉，牆鐘上界是 NOFILL 臂的 **13.75 t/s**
（94.26 → 72.72 ms/step ＝ 21.54 ms/step；每 miss 1 929 µs）。也就是說——
**把「等」全部回收也到不了 20+**；25+ 更不在這條路上。這一格能買的是 ~3 t/s。

## 6. 保留（必須跟數字一起讀）

1. **兩條口徑差 2.1×**：儀器 918 µs/miss（呼叫端看到的等）vs 牆鐘上界 1 929 µs/miss。
   差的部分是「不讀」帶來的二階效應（NOFILL 臂的命中率 96.7→97.0、頁面足跡、與 slab
   串流的 I/O 競爭）。**不要把 918 當成 t/s 的定價**——那要用 §69 的區間。
2. **單趟、非 ABBA、每臂 3 rep**：t/s 欄位的 ±0.26–0.86 蓋掉了臂與臂之間 0.2–0.4 t/s 的差
   ⇒「NO_RDADVISE／DBUF=0／WORKERS=16 不比參考臂慢」只能讀成**在這個解析度下不可分辨**，
   不是「等價」。桶本身穩定得多（`span` 六臂 3.88–3.92 s，±1%）。
3. **`madvise` 桶在 µs 量子以下**（0.5 µs/miss）：只能說「≤ 1 µs/批」，不能說「精確為 0」。
   判「宣告的卡點是否還在」請照 §67 的規則做，不要拿這個數當見證。
4. **NOFILL 是 timing-only**（引擎明文：輸出是垃圾、永不得當正確性量測）⇒ 它的 13.75 t/s
   **不是能力讀數**，只拿它的**差**。
5. 這一輪是**內部驅動**（`CGC_INTERNAL_CALL=1`，非 charter 立項）；機器狀態由
   `llama_bench_matrix` 自己記錄：`attribution=none`（swap 平坦 353.62 MiB、thermal NOMINAL）。
   但 metal 閘照舊印「起跑合格、整趟會超」（池 8192 ＋ Metal 峰值 10 379 ＋ 保留 1 024
   vs 上限 11 453）——這是 `budget_gate` 那一軸的既有事實，與本分解無關。

## 7. 產物與怎麼重跑

```
# 分解（交付 cell；6 臂；約 5 分鐘）
export CGC_INTERNAL_CALL=1
python3 scripts/check/llama_bench_matrix.py --arms \
  "prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1,\
   prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1;LLAMA_EXPERT_CACHE_NO_RDADVISE=1,\
   prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1;CGC_DBUF=0,\
   prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1;CGC_SERVER_WORKERS=16,\
   prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1;CGC_POOL_MADVISE=1,\
   prod-new:CGC_EB_TIMER=1;CGC_FILL_SPLIT=1;CGC_EB_NOFILL=1" \
  --prompt 0 --gen 128 --depths 512 --reps 3 --ctx-size 0 --warm-skip 64 \
  --fixed-fill-seed 1 --cell delivery --json Backup/fill_split_delivery_2026-09-30/ab.json

# 判詞（七條結構閘，含恆等式與陰性對照）
python3 scripts/check/fill_split.py --dir Backup/fill_split_delivery_2026-09-30   # → VERDICT: SPLIT
python3 scripts/check/fill_split.py --selftest                                     # → 11/11
```

儀器與檢查器都在樹上未提交；`fill_split.py` 已進 `board_pipeline.sh`（自測 ＋ 對這批冷凍產物的現判）。
