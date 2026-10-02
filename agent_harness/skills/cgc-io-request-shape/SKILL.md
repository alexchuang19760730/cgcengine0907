---
name: cgc-io-request-shape
description: 在動 C++ 之前，判定一次 IO 請求形狀改動（合併 pread／加大 span／batching）在 llama.cpp CGC fork 上到底值不值。當被問「放寬 merge 條件值不值」「少幾次 syscall 有沒有用」「要不要為了少 read 而多讀 bytes」時使用。核心是三條獨立證據：檔案幾何、機會、IO 物理——三者缺一就會得出自信的錯誤結論。
agent_created: true
---

> **這是快照，不是權威副本。**
> 權威位置：`~/.workbuddy/skills/cgc-io-request-shape/SKILL.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 要改 skill 請改原檔，再重跑 `python3 agent_harness/scripts/import_harness_snapshot.py`。

# 判定一次 IO 請求形狀改動值不值

適用 repo：`flashkv-devserver`（llama.cpp CGC fork， Metal + expert cache pool， SSD 常駐）。
目標：在**不寫 C++、不重建**（重建會蓋掉並行 session 的 dylib）的前提下，給出可裁決的上界。

## 為什麼要三條證據

任何「把 N 次小讀併成 1 次大讀」的改動都同時在兩個貨幣上結帳：
少幾次 syscall（省） vs 多吃幾個 gap 的 bytes（付）。只看一邊一定錯。

| 證據 | 回答什麼 | 怎麼拿 |
|---|---|---|
| **A 幾何** | 一次合併要付多少 bytes | 直接讀 GGUF header，**免費、不開 server** |
| **B 機會** | 今天有多少減量空間 | 引擎計數器 + `NO_MERGE` 反向對照 |
| **C 物理** | 一次 pread 的固定成本多大 | 冷讀直接對抗，**不擬合任何模型** |

**少了任何一條的歷史教訓**：只有 A+C 會以為「物理上划算」就值得做，但可能根本沒幾個
機會可砍（本輪：G≤8 只碰得到 1% 的 job）；只有 A+B 會把「平均攤銷成本」當邊際。

## 步驟 −1（本輪新加，最先做）：這條軸**佔目標的百分之幾**

在踏進步驟 0 之前先問：我要優化的東西，在那個「要改善的指標」裡佔多少？
**佔比就是這條軸的上界**，不管改得多漂亮都跨不過它。

```sh
# joint capture 已量過的分量（n=65 穩態，中位 total 122.39 ms/step）
#   GPU busy 97.90 = 80.0% ／ gap 23.52 = 19.2% ／ cb(專家 SSD) 11.26 = 9.2%
/opt/homebrew/bin/python3 -c "import json;d=json.load(open('Backup/phase_decomp/joint_capture_20260920_2144.json'));print(d)" 2>/dev/null | head
```

本輪教訓（09-20）：**所有「專家池／專家 IO」類槓桿共享同一個上界 9.2%**（server ∵ cb 只佔步時 9.2%）。
合併讀 ≤1%、neighbour prefetch 輸在 bytes、per-job overhead 只存在於 `-np N`
——三條路各自精緻，加起來仍在 9.2% 裡面。**先算佔比，再決定要不要量三條證據。**
（佔比拿不到時，退化方案＝**相對干預**：把該軸的成本直接挪掉，看步時怎麼動。見文末「佔比 vs 干預」。）

## 步驟 0：先問「這個改動是打 prefill 還是 decode」——兩者答案常常相反

用一次自洽 capture 就能分開，成本 ~1 分鐘：

```sh
/opt/homebrew/bin/python3 Backup/phase_decomp/capture_demand_trace.py --n-predict 64 --reps 4
```

`LLAMA_EXPERT_CACHE_DEMAND_DUMP`（`cgc-demand v2`）＋**同進程**的 `MISS_DUMP` 與 `final stats`
⇒ 閘門是**恆等式檢驗**而非擬合：replay 必須同時復現引擎自己的 `batch_misses` 與 `all_misses`
（本輪實測 **兩者都 0.00% 誤差**）。parser 復用 `scripts/check/reuse_distance.py`（**只讀，別改**）。

兩個把這一步做對的必要動作：

- ⚠️ **絕不跨 run 校驗**：拿 A run 的 trace 去對 B run 的 miss 數會得到自信的錯答案
  （本輪踩到：`36312` 是 `n_slots=71` 那次 run 的數，今天這格是 **142 槽**）。
  正確做法是**同一 process 同時產出 trace 與 counter**。
- ⚠️ **`B` 事件的 union 大小可以認 regime**：完美雙峰 1–8 / 14–32，而 **union 恰好 = 8 就是單 token
  的 top-8**；`B 數 / 層數 ≈ 生成步數`。若懷疑某段是 prefill，**用 prompt 長度縮放去證**
- ⚠️ **`pread_usec` 不能用來歸因**：它在 `fill_job`（`:41`）與 worker 迴圈（`:3424`）**各累加一次**
  且按 segment 計 ⇒ 推出 17.9 ms/job，而 `fill_batch_usec / file_reads` 是 184.8 µs/job，**差 97×**。
  要 IO 單價一律用 **`fill_batch_usec / file_reads`**。

然後在 trace 上 replay 你要問的策略（參考 `Backup/phase_decomp/neighbour_gate.py`），
同時輸出兩個貨幣：**miss 數** 與 **讀入專家數**。

> ★ 通貨是**位元組，不是 miss 數**：device 吞吐「與讀取形狀無關」⇒ 時間 ∝ 位元組。
> 打平條件 = `省力% ≥ 字節漲幅的倒數補數`（例如讀入變 1.98× ⇒ 每 byte 要便宜 ≥ 48% 才打平）。
> 本輪 neighbour prefetch 就是這樣死的：miss 真的降 22.1%，但讀入 1.98×，
> 而 `coalesce_race.py` 量到的最好合併折扣只有 35%。

## 步驟 1（A）：幾何 —— `models/gguf/*.gguf` header

```sh
/opt/homebrew/bin/python3 Backup/phase_decomp/parse_expert_geometry.py
```
拿每個 expert tensor 的 bytes 與 **expert stride in file**（gid 相鄰就一定銜接）。
GGUF value type 常踩：STRING=8、ARRAY=9。

## 步驟 2（B）：機會 —— 反向對照，同一 build

```sh
# 四臂含 NO_MERGE（最後再跑一次 base 當漂移監測）
/opt/homebrew/bin/python3 Backup/phase_decomp/perjob_s02.py --np 1 --tokens 128 --reps 2 \
  --arms base,no_merge,no_rdadvise,base2 --json <out>.json
# 初始填池差分（必要：final stats 含初始值）
/opt/homebrew/bin/python3 Backup/phase_decomp/perjob_s02.py --np 1 --reps 0 --tokens 128 \
  --arms base --json <init>.json
```
- `NO_MERGE`：**判斷某個合併條件是否真的生效的唯一反向對照**（注意它**不在 `run_server.sh` 的 allowlist**，
  走 launcher 會被靜默丟棄 ⇒ 用 `perjob_s02.py` 的 bypass launcher）。
- job 數幾乎不變 ⇒ 該合併條件今天已經是死的，剩餘空間就是「機會」。
- 換算：`jobs/step`、`miss/(layer,step)`、`bytes/job`，再配幾何算得失的数量。

⚠️ **兩個必踩的坑**
- `final stats` 混了初始填池（`--reps 0` 差分，本輪佔 2%）。
- **`fill_batch_usec/file_reads` 是攤銷平均不是邊際成本**；要邊際必須真的改變 job 數，
  而如果 merge 已死，**這個 cell 裡沒有旋鈕做得到** ⇒ 走步驟 3。

## 步驟 3（C）：物理 —— 冷讀直接對抗

```sh
/opt/homebrew/bin/python3 Backup/phase_decomp/coalesce_race.py --iters 24 --threads 8 \
  --gaps 1,2,4,8,16,32
```
偶數 sample 走 2 次 pread、奇數走 1 次合併 pread，**奇偶交替** ⇒ 漂移自消；
thread 數對齊 `LLAMA_EXPERT_CACHE_WORKERS`。

### 🔴 預熱陷阱（一定會踩）

固定隨機種子 + 多輪 trial ⇒ **第 1 輪就把那些頁讀熱了**，于是 `F_NOCACHE` 跟 `cached`
一樣快（都是 18–21 GB/s，那是 DRAM 不是 SSD）。症状是「合併怎麼算都划算」。

- 偵測：開一組 reuse vs fresh，**冷 139 KB p50 ≈ 235 µs vs 熱 ≈ 7.5 µs（差 31×）**。
- 修正：每輪用**不重疊**的偏移；對抗要在同一次單調掃描內交錯。
- 不要用 `latency = fixed + size/bw` 的最小二乘擬合：拟合對預熱完全不設防，
  而且擬合出來的 bw 可能物理上不可能（曾擬出 6 GB/s 的 SSD）。

## 步驟 4：合起來算上界

把「物理上划算的 gap 區間」與「隨機能排到的 gap 分布」畫成同一張表。
本輪結論的形狀：**兩條曲線不重疊** —— 划算的 G≤8 只碰得到 1% 的 job，
有機會砍 4% 的 G=32 物理上慢 2× ⇒ 那是「≦1% 的上界」，直接結案。

換算成 decode：`<= (砍的 job 比例) × (每次省的比例) × (fill 佔 step 的比例)`。
與單臂噪音 ±27% 比；低於它就**不要寫 C++**。

## 輸出惯例

寫一份 `docs/*_VALUE_YYYY-MM-DD.md`，把三條證據與唯一的交叉表都放進去，
並在方案文件（`docs/NP_PERJOB_FIX_PLAN_*.md`）的對應條款上加一行裁決指標。
「× cell 上成立」永遠要寫成「在那個 cell 上成立」，不要拿 np1 的數字替 prefill 說話。

## 2026-09-22 補充：先把這四個數值釘下來，再問要不要動這條軸

動手前先確認這四個數，**它們決定了這條軸還剩多少錢可撿**（權威
`docs/IOCACHE_CONSTRAINT_ADMISSION_2026-09-22.md`）：

| 值 | 數字 | 來源 |
|---|---|---|
| per-layer union 隨列數 M 增長 | **線性 8·M**（列間去重 ≈ 0）⇒ pool full 在 **M=18**（>143 slot） | `docs/CAP_INVARIANCE_LOCALIZATION_2026-09-14.md:100-101`（cap5→40、cap8→64）、`Backup/cgc_logs/union_evidence_2gib_20260914.txt`（MTP 穩態 4 列 → min=max=32） |
| per-slot bytes（一位專家的全部 kind） | **1.391 MiB**（binding blk.39）⇒ 8 GiB/(41 層×1.391)=143.6 ✓ 對上 `n_slots=143` | `docs/EXPERT_CACHE_THRASH_2026-09-19.md:115` |
| 每次 miss 被服務的速度 | ~1.11 MiB / 0.695 ms = **1.6 GiB/s** ＝ DRAM 峰值 108.8 GB/s 的 **1.5%**；比磁碟 823 MB/s 快一半 ⇒ **不是 disk-bound，是被 overhead 統治的 RAM 拷貝** | `docs/F1_CB_MISS_REGRESSION_RESULT_2026-09-20.md:13,87-95` |
| 減 miss 到底有沒有用 | **9-21 判「不是驅動項」**：4 GiB 的 miss 是 8 GiB 的 1.91×、capacity 3.15×、`cb` 41.8→90.0 ms，**而 t/s 10.60 vs 10.28 不動** | `docs/IO_PATH_AB_2026-09-21.md` §三結論③ |

⇒ **三個直接推論**：

1. 想把 miss 清零只能靠「更多 slot」，而 capacity miss 只佔 miss 的 **34%**（@143 slot）
   ⇒ 就算容量無限也只省 34%；要真正到 ~0 需要 **215–251 slot/層 = 12–14 GiB**
   （16 GB 機器上不可行）⇒ 8 GiB 前提下唯一出路是**把 per-slot 砍到 ≤0.929 MiB（−33%）**。
2. 之前做過的「相鄰段合併成一個 `preadv`」 ＋ `rdadvise` ＋ persistent worker pool
   （`fill_segments_pool:3170-3245`，2026-08-29，`LLAMA_EXPERT_CACHE_NO_MERGE` 可 A/B）**早已占位**；
   要重新問這條軸，先回答「剩下的 overhead 是 syscall 還是 page fault」。
3. **歸屬檢查**：常被期待由 expert-cache I/O 面來管的三件事（同層列取聯集、每 tensor 每步只讀一次、
   工作集 ≤143）**今天全部已實作**；「加寬每 forward 列數 M」不屬於這條面（屬 `CGC_POOL_MAX_TOKENS`
   與 `common/speculative.cpp` 排程），而且因為 union 線性，**加寬一列就是多付 8 個全新專家的 bytes**。

## 佔比 vs 干預（當「佔比」拿不到時的替代做法）

互不信任的兩條線各自給出結論時，用**干預**來仲裁 —— 把成本直接挪掉，而不是估它多大。

```sh
/opt/homebrew/bin/python3 Backup/phase_decomp/poolsize_ab.py --np 1 --reps 2 --tokens 96 \
    --arms big,small,big,small --json Backup/phase_decomp/poolsize_ab.json
```

本輪用它結掉「記憶體超訂」：把靜態需求從 **23.27 GB（超訂 6.89 GB）降到 15.31 GB（塞得下）**
⇒ RSS −5.9 GB、每 rep 解壓縮 −4×、但步時只 **+5.8%**（3/3 配對同號）
⇒ 觀測派估的「足以解釋 gap 19.2%」被下修到 **~6%**。
**注意：最好看的那一格也只有 +8.2% ⇒ 結論對噪音穩健。**

### 🔴 兩個環境陷阱（本輪實際踩到）

1. **池大小不在 env，在 ARGV。** `CGC_DUMP_ENV=1 ./scripts/run_server.sh` 給的是
   `ARG -expert-cache` / `ARG 10737418240`。而 `perjob_s02.py::BASE_ENV` 長期寫著
   `CGC_SERVER_EXPERT_CACHE_BYTES=8 GiB` —— **`Server.start()` 裡 `resolved["env"]` 排在
   `BASE_ENV` 之後，會把它蓋掉，且真正的 size 來自 argv** ⇒ 那個 8 GiB **從來沒生效過**。
   改池必須改 argv（見 `poolsize_ab.py::set_pool()`）。**凡是「改了 env 沒反應」，先確認它在
   allowlist、且沒有被後面的 env／argv 蓋掉。**
2. **`free_pct()` 看不見 swap。** 它報 `vm_stat` 的 free pages；本輪起點 `free=82.0%`
   看似乾淨，同刻 `vm.swapusage` 其實已 **used 4468 MiB**，跑完系統變 7168/5620 MiB。
   ⇒ **t/s 絕對值一律要附 swap 基線**，否則跨 session 不可比（本輪同 config 早上 13.4、
   下午 5.7 t/s，差 2.3×，來源就是它）。替代儀器：`poolsize_ab.py::MemSampler`
   （逐秒記 `vm.swapusage` ＋ compressor ＋ 該 pid RSS）。
   ⚠️ 併發 session 也會踩同一個：兩條線在同一時窗各起一台 `llama-server` 會互相污染
   （本輪與另一條線在 8080 上撞車）⇒ 用不同 port（`--port auto`）。

### 2026-09-25 補充（僅生產口徑，詳見 `docs/IO_AXIS_VERDICT_2026-09-25.md`）

**非生產口徑（`--prompt 0` 冷池 ＋ `MTP on` ＋ `--batch 512`）的數據已全部作廢刪除，勿引用。**
生產 cell 下的三條硬事實：

1. **幾何判死**：一個 expert 的三段在檔案裡相隔 **88~115 MB**
   ⇒ 合併成一次 pread 要多讀 **182.6×** 位元組（`scripts/check/expert_file_layout.py`，selftest 7/7）。
2. **合併邏輯早已存在**：`fill_segments_pool` 已把 file-contiguous 的 run 合成一次 `preadv`
   ⇒ **「一次 pread 搬多個 expert」不是待做的改動**。
3. **固定開銷佔 90%**：一個 job 共 371 µs，其中傳輸只佔 36 µs
   ⇒ 方向（batch 化）對，但**可合併的機會已被幾何鎖死**。

⚠ IO 單價一律用 **`fill_batch_usec / file_reads`**；`io_us_per_job` 是跨 worker 累加口徑，會誤導 30×。
