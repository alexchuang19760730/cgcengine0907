# 下一個 20+ 的槓桿：**權重讀取路徑的效率，不是 SSD、不是池容量**

Date 2026-10-02 · carrier `Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X` · worktree `flashkv-devserver`
現況：decode 最好 **15.19 t/s**（`prod-new:CGC_OVERLAP_FENCE=41`、cell p2048/g128/r3、DIRTY 窗）；
板載 waived 上限 C13 **15.210976**；乾淨窗 C12 **13.867**。

---

## 0. 結論（**已三次更正；§10 是現行最終判詞**）

**★ 目前最終判詞（§8 → §9 → §10）**：**本日三條候選槓桿全被各自事先寫死的判據否證，沒有一條具備
20 t/s 所需的量級（15.19 → 20 需要 +32%）：**

| 候選 | 判 | 量級 |
|---|---|---|
| ① 權重搬運通道 | §8 否證 | 只值 **7.5 ms/步（11%）** |
| ② MoE GEMV／kernel 等效權重頻寬 | §9 否證 | 全族天花板 **≈ 5%**（單獨 gate/up **47.6 GB/s**、down **27.9 GB/s**；in-situ 只佔 GPU 忙碌 **3.4%**） |
| ③ `residual 28.85 ms（30.7%）` | §10 否證 | **歸因假象**：`ns_total` 對 cb 粒度不變（91.9 vs 93.0 ms，~2%）⇒ 不可回收 |

**目前的實測上限**：`15.186`（全 rep ≥15.02，`swap` 窗）／棘輪 waived `15.211`（C13）／乾淨窗 `13.867`（C12）。
**⇒ 在這台 16 GB 盒子上，bit-identical 的 20+ 目前沒有已識別的路。**

---

## 1. 先把錯誤的靶劃掉（都是倉庫自己量出來的）

| 曾被當成靶 | 現況證據 | 判定 |
|---|---|---|
| **SSD 飽和（f≈20%）** | `docs/S2_OVERLAP_EXPERIMENT_2026-09-28.md:520-526` 用 `f=20%` 擬合出 85.9 ms，**吻合到小數第一位** | ✗ **已被 §15 推翻** |
| ↑ 同上 | 同檔 `:576-601` **實跑 E-A**：OS `vm_stat Pageins` = 6.22 GiB／引擎 `read_mib` = 9.02 GiB，對 376 token × 1117 MiB = 410 GiB ⇒ **`f_actual ≈ 1.5%（上界）`** | ✗ **SSD 佔 ~1.5–2%，decode 不是 SSD-bound** |
| **池容量 / residency** | 8→143 席命中只差 0.3pp；現行 membership 90.3% vs **best-possible 90.5% ⇒ placement gap 0.2pp**（`CGC_POOL_RESIDENCY_DEADEND_2026-09-28.md:104`） | ✗ **策略已近最佳，容量受 RAM 限制** ⇒ 封閉 |
| **量化 / 換盒子 / 降模型** | operator 已裁定否決 | ✗ 政策封閉 |
| **K0–K5 融合 / dispatch** | K3 實測只值 2.28% | ✗ 判死 |
| **MTP** | k 軸經濟學；六個 k 全 `B < A` | ✗ 判死 |
| **重疊柵欄的精細化（D2）** | 機制判不划算（每邊界 +130 µs × 41 > 收益） | ✗ 收束（×1.218 已是上限） |
| **消滅 host round-trip（R6／S1）** | `min_cold_il=0` ⇒ 重算＝整圖 | ✗ 判死（**但見 §3 的保留**） |

---

## 2. 真正的預算：48 ms/步是「記憶體之外」的

`S2_OVERLAP_EXPERIMENT_2026-09-28.md:515-522` 的恆等式（可驗的算術）：

```
每 token 必讀 bpt = 1.171 GB ；規格 RAM BW = 120 GB/s ；SSD ≈ 3 GB/s
t = bpt · [ (1−f)/120 + f/3 ]        ，f = 落 SSD 的比例

f = 20%  ⇒ 85.9 ms / 11.64 t/s   ← 擬合值（與當時實測吻合）
f =  2%  ⇒ 17.4 ms / 57  t/s     ← §15 的實測值
```

**把量到的 `f≈2%` 代回去，記憶體地板是 17.4 ms/token（57 t/s）。**
而我們最好的臂是 **65.8 ms/token（15.19 t/s）** ⇒ **約 48 ms/步（73%）不歸因於記憶體層。**
（同檔 `:603-614` 原文：「把 `f` 換成量到的值，85.8 ms 就解釋不了」。）

換一個等價的說法（`CONTRADICTION_CONVERGENCE_2026-09-28.md:16,26`）：
**端到端等效頻寬 = 1.171 GB ÷ 85.8 ms = 13.65 GB/s = 規格 120 GB/s 的 11.4%。**

| 目標 | 需要等效頻寬 | 相對現況 |
|---|---|---|
| 16 t/s | 18.7 GB/s | ×1.37 |
| **20 t/s** | **23.4 GB/s** | **×1.72** |
| 25 t/s | 29.3 GB/s | ×2.15 |

而**可及的天花板很高**：DRAM 串流實測 **100.5 GB/s**（`SHAPE_ROOFLINE_2026-09-22.md:95`）、6 執行緒
**108.8 GB/s**（`SHAPE_GAP_TO_TARGETS_2026-09-24.md:106`）。⇒ **23.4 GB/s 只有量測天花板的 23%。**

---

## 3. 機制：那 24912 次讀取，每次 0.37 MiB、每次 1.86 ms

`S2_OVERLAP_EXPERIMENT_2026-09-28.md:616-638`（E-A 那一趟的實測列印）：

```
read shape: jobs=24912  bytes=9688858624 (0.37 MiB/job)  us/job=1856  effective_rate=210 MiB/s
pool      : pool_cap_slots=143  union=64
miss      : compulsory=6458  capacity=2240 (74.2% / 25.8%)  evict=8586  hit_pct=81.62
critical  : fill_wait_us=101314（= 0.101 s）   而請求總時 47.9 s
```

三個推論（原文的）：
1. **`0.37 MiB/job` 取 `1.86 ms`。若真是 3 GB/s 的 SSD，這個大小只要 0.12 ms —— 量到的是它的 15 倍。**
   ⇒ **受限的是「每讀取的固定開銷（issue / queue 延遲）」，不是帶寬。**
   而 fill 路的**等效速率只有 210 MiB/s**（＝SSD 容量的 ~7%）。
2. **臨界路徑上只等了 0.101 s / 47.9 s ⇒ 請求執行緒幾乎沒有被 I/O 阻塞。**
3. `capacity` 只佔 miss 的 25.8% ⇒ **不是 thrashing 主導**。

原文的結論（`:636-638`）值得逐字引用：

> **降 `m` / 降 `S0` 的靶不是池容量，是「0.37 MiB 一次、1.86 ms 一次」的那 24912 次讀取的發行方式，
> 以及那個 11.4% 的 RAM 帶寬效率本身。**

★ **這條槓桿同時解釋了兩件先前分開看待的事**：
- `CGC-DECPROF` 的 `cb` 項（語意＝「下一層 top-k hook：slot 管理 ＋ **阻塞式 pool 填充**」）；
- 柵欄拿到的 ×1.218 之所以只是「一半」，是因為它只把 encode/submit 移出關鍵路徑，
  **搬不動那條讀取路徑本身的效率**。

⇒ **讀取路徑既是「每段邊界的阻塞成本」，也是「11.4% 的 RAM 效率」—— 同一個靶，兩個症狀。**

---

## 3b. ★ 當日實測（2026-10-02，`prod-new`／MTP-off）—— 比 §15 更乾淨，且結論更硬

`Backup/cgc_logs/llama_server_20261002_203146.log`（今天的一支臂，跑完在 teardown 印；**`read shape:` 是無條件印出的**）：

```
CGC-SHAPE v=1 phase=final M=8 width=8 union=64 pool_cap_slots=143 slots_layer=143 n_layer=41
  req=5976  hits=5823  misses=153  hit_pct=97.44  compulsory=153  capacity=0  evict=41
  read_mib=0.0   pread_us=455566327   fill_wait_us=73707   final_counters=1
miss attribution: compulsory=153 capacity=0 (100.0% / 0.0% of 153)  worst=layer 40 distinct=113 slots=256
llama_expert_cache: read shape: jobs=15351 bytes=0 (0.00 MiB/job)  us/job=29677  effective_rate=0 MiB/s  total_bytes=0.00 GiB
```

**三個彼此獨立的計數器（`read_mib` = 0.0、`read shape bytes` = 0、`total_bytes` = 0.00 GiB）都說：
整支臂期間 SSD 讀了 ~0 位元組。** 同時：
- **`hit_pct = 97.44`、`capacity = 0`（153 個 miss 全是 compulsory）、`evict = 41`。**
- ⇒ **既不是容量不足、也不是淘汰抖動** —— §1 表裡「池容量／residency」被劃掉，這裡得到第二次獨立確認。

**⇒ 結論（比 §15 更強）**：**這一版引擎的 decode 根本不碰 SSD。**
於是恆等式在 `f ≈ 0` 處的**記憶體地板是 `1.171 × 1/120 = 9.76 ms/token ＝ 102.5 t/s`**
（`S2_OVERLAP:517` 原文「物理上限（100% 命中 RAM）= 9.76 ms/token」）。

| | ms/token | t/s |
|---|---|---|
| 記憶體地板（f=0、規格 120 GB/s） | **9.76** | 102.5 |
| 用 DRAM **實測** 100.5 GB/s 算的地板 | **11.7** | 85.7 |
| **今天最好的一臂** | **65.8** | **15.19** |
| 20 t/s 的目標 | 50.0 | 20 |

⇒ **等效權重頻寬 = 1.171 GB ÷ 65.8 ms = 17.8 GB/s ＝ DRAM 實測天花板（100.5 GB/s）的 18%。**
⇒ **`pread_us=455.6 s` 而 `bytes=0`** —— 那 15351 次讀取的執行時間**不是花在位元組上**
（與 §15 的「受限的是每次讀取的固定開銷，不是帶寬」同一個形狀）。

⇒ **成本不在 SSD、不在池容量、不在策略 —— 在「pool／RAM → GPU」那條搬運通道本身**
（`docs/EXPERT_CHANNEL_SHAPE_2026-09-23.md:0`：通道「**not limited by the SSD nor by Metal**；
limited by its own shape: one 0.34–0.56 MB read per (layer,expert,kind)，merged only on file-adjacency，
**joined to zero once per layer**」）。
⚠ 儀器保留：`read_mib=0.0` 仍可能是「欄位沒填」而非「真的 0」（同 `eng-mh-0079` 的教訓）；
要 `footprint`／`vmmap` 審計才能完全關掉。但**三個獨立計數器同聲**、且 `hit_pct`／`capacity` 都填了值，所以可信度高。

---

## 4. 下一個實驗（唯一入口、零重建）

**E-A′｜在健康臂上重跑那個探針**（§15 那趟是在 `prod25`（MTP ON）+ 記憶體壓力下跑的，
`f` 的**形狀**可信、**絕對 t/s 不可引用**）：

- **臂**：`prod-new:CGC_OVERLAP_FENCE=41`（＋既有的 fill／read-shape 計數器），一支、reps 3、冷卻起跑。
- **要讀的量**：`read_mib` / `n_gen` ⇒ `ssd_bytes_per_token = read_mib × 1.048576 ÷ n_gen`、
  `f_actual = ssd_bytes_per_token ÷ 1.171`；並取 `read shape:` 行的 **MiB/job、us/job、effective_rate**。
- **判準（跑前寫死）**：
  - `f_actual ≲ 5%` ⇒ **確認靶是搬運通道** ⇒ 進 §5 的工程。**§3b 已用今天的日誌先行回答（f≈0）**，
    E-A′ 只是把它放到一個**冷卻、可引用的臂**上再確認一次。
  - `f_actual ≈ 15–20%` ⇒ §15／§3b 的樣本不可推廣 ⇒ 回頭重做池／residency 帳。
- **同時量**：`fill_wait_us` 佔牆鐘的比例（§3b：73.7 ms／~90 s ⇒ 0.08%，**填充完全不在臨界路徑上**）
  ⇒ 工程應做在**搬運通道**，不是同步端。

**E-B′｜把 54 ms 拆開（真正該做的下一槍）**：`f` 既然 ≈0，剩下的問題是
「54 ms 裡有多少是 **pool→GPU 搬運**、多少是 **GPU 計算**、多少是 **host 序列化**」。三個既有儀器就夠：
- `CGC_EB_TIMER=1`（既有的 expert-buffer 計時器，`L20-4` 用它把 fill 定價在 6.79–20.68 ms/step）⇒ 搬運那一項；
- `CGC-DECPROF` 的 `wait`／`cb`／`submit` ⇒ 分段預算；
- 柵欄開／關的成對 ⇒ 序列化那一項（已知 ~35 ms，`S2_OVERLAP:8-24`）。
**在拿到這張三分的表之前，不要選工程方向。**

## 5. 若判準成立，工程的著力點（按預期值排序，尚未實作）

1. **降低每次讀取的固定開銷**：24912 次 × 1.86 ms 的「發行」成本。相關未採礦旋鈕（皆已存在、
   未被單獨量測）：`LLAMA_EXPERT_CACHE_BATCH_SPAWN`、`LLAMA_EXPERT_CACHE_SERIAL_FILL`、
   `CGC_GATHER_SLAB_CAP`、`LLAMA_EXPERT_CACHE_PREAD_DBG`；以及已判 **UNUSABLE／+2.5–3.3%** 的
   `CGC_CB_N_MAIN`。這是**最便宜的第一槍**（純旋鈕掃描，零 src 改動）。
2. **把權重搬運做成一次大搬運**：目前是 `0.34–0.56 MB/(layer,expert,kind)`、只按**檔案相鄰**合併
   （`EXPERT_CHANNEL_SHAPE_2026-09-23.md:0`）。跨 kind 合併已被幾何判死（會多讀 182.6×
   —— `IO_AXIS_VERDICT_2026-09-25.md:22-32`）；但**同 kind 內的請求合併／更深的 queue** 尚未量過。
3. **11.4% 的 RAM 效率**：若瓶頸是 GEMV 取數的存取樣式（而非頻寬），那是 kernel 端的事，
   `SHAPE_ROOFLINE` / `SHAPE_GAP_TO_TARGETS` 兩份文件是起點。

## 6. 這份結論不能支持的事（明寫）

- §15 與 §3b 都是**單一臂**的形狀證據，不是分布；絕對 t/s 不可引用。
- **仍未關的一條**：無法分辨「真的讀了 0 位元組」與「讀取發生在 mmap 路徑而計數器沒計到」——
  需 `footprint` / `vmmap` 審計。
- ⇒ 但**方向不需要等它**：**`f≈0` 或 `f≈2%` 兩種情況下，「池容量／residency／SSD」都不是瓶頸**
  （`hit_pct=97.4`、`capacity=0`），所以**工程不該投在那裡**；該做的是 §4 的 **E-B′ 三分表**。
  **不要在未拆開那 54 ms 之前改 src。**

---

## 7. 一頁結論

```
記憶體地板（f≈0，規格 120 GB/s）   9.76 ms/token  = 102.5 t/s    ← S2_OVERLAP:517 原文
記憶體地板（DRAM 實測 100.5 GB/s） 11.7 ms/token  = 85.7  t/s
量到的最好（今天）                65.8 ms/token  = 15.19 t/s    ← 等效權重頻寬 17.8 GB/s
未歸因                            54   ms/token   ← 這 54 ms 就是 20+ 的全部空間
  ├─ 邊界 host round-trip / 序列化        ~35 ms（racy vs 正確序：total 73.54 → 38.10）
  └─ pool／RAM → GPU 搬運通道的效率
     （0.34–0.56 MB/(layer,expert,kind)、joined once per layer、DRAM 只用 18%）

20 t/s ⇒ 等效權重頻寬 17.8 → 23.4 GB/s（實測天花板 100.5 GB/s 的 23%）
```

---

## 8. ★ 更正（E-B′ 實測，2026-10-02 21:2x）：**搬運不是主項 —— 靶在 `wait` 那 51.7 ms**

**這一節推翻本文 §0 的假設。** §E-B′ 的預註冊判別句是「`step_usec` 折算 ≤8 ms/步 ⇒ 搬運不是主項」，
實測落在**那一支**，所以照事先寫死的規則下判：**§0 的「搬運通道是主項」不成立。**

### 8.1 臂與讀數（ABBA 四臂，`Backup/l201_accel/eb_prime/`）

| 臂 | decode | 逐 rep | EBTIMER `step_usec`（每步，40 層） |
|---|---|---|---|
| arm0 fence-off | 11.637 | [11.74, 11.69, 11.48] | 3.95 / 7.32 / 7.99 / 6.79 / 11.27 ms |
| arm1 fence-on | **14.995** | [16.25, 15.48, 13.25] | 7.27 / 13.22 / 4.81 / 2.62 / 10.61 ms |
| arm2 fence-on | 14.192 | [15.96, 13.25, 13.37] | — |
| arm3 fence-off | 9.879 | [8.52, 11.69, 9.43] | — |

**EBTIMER 均值**：fence-off **7.46 ms/步**／fence-on **7.70 ms/步** ⇒ **相差 0.24 ms**，
遠小於預註冊的 2 ms 門檻 ⇒ **柵欄只影響序列化，完全不動搬運**（⇒ 兩軸**可疊加**）。

### 8.2 三分表（DECPROF，`ntok=1`，均值）

| 項 | fence-off | **fence-on（交付形狀）** | 佔比 |
|---|---|---|---|
| **total** | 83.0 ms | **65.7 ms** | 100% |
| `wait` | 71.4 | **51.7** | **79%** |
| `cb`（＝ hook：slot 管理 ＋ 阻塞式 pool 填充滿） | 4.6 | **7.7** | 12% |
| `submit`（CPU 編碼） | 7.1 | **6.3** | 10% |
| （記憶體地板，f=0／DRAM 100.5 GB/s） | — | 11.7 | 18% |

- **搬運項（EBTIMER 7.5 ms）幾乎全部落在 `cb` 裡**（cb 7.7 ms ⊇ 搬運 7.5 ms）
  ⇒ `cb` 這個桶**就是搬運＋slot 管理**，不是別的。
- ⇒ **搬運 ＝ 7.5 ms ＝ 步時的 11%**。**它不是 54 ms 的主項。**

### 8.3 那 54 ms 在哪 —— 在 `wait`

`wait` = 51.7 ms 是主機輪詢等**每一段的 GPU 工作完成**。等效權重頻寬：

```
1.171 GB ÷ 51.7 ms = 22.6 GB/s   ← 只有 DRAM 實測天花板（100.5 GB/s）的 22.5%
```

⇒ **靶是「MoE GEMV（`mul_mat_id`）在 GPU 上的等效權重頻寬」，不是 IO、不是池、不是搬運通道。**
要 20 t/s（50 ms 總步時）⇒ `wait` 必須降到 **~34 ms** ⇒ kernel 層等效權重頻寬要提到 **~34 GB/s**。

### 8.4 附帶確認（與 §3b 一致）

`CGC-EBTIMER` 的 `miss=` 每步只有 **3–12**（`n_sum=320` ＝ 40 層 × 8 專家）⇒ **miss 率 1–4%**，
與 §3b 的 `hit_pct=97.44` 互相背書 ⇒ **池容量／替換策略／SSD 三條都確定不是靶**（第三次獨立確認）。

### 8.5 因此任務重新排序

| 舊靶（本文 §0–§5） | 新判 |
|---|---|
| 搬運通道（fill／pool→GPU 搬運） | **7.5 ms，11%** ⇒ 不是主項（**本文 §0 錯了**） |
| 讀取發行開銷（0.37 MiB/job 那條） | 同上，量級太小 ⇒ 不值得先做 |
| 池容量／residency／SSD | 三次獨立確認不是靶 |
| **`wait` 51.7 ms（GPU GEMV 的等效權重頻寬 22.6 GB/s）** | **← 這才是 20+ 的靶** |
| 邊界序列化（~26 ms 殘量） | 真依賴；消滅它的路（R6）已判死 ⇒ 目前無路 |

---

## 9. ★★ kernel 微基準（2026-10-02 21:4x）：**這條路到不了 20+，而且我 §8 的說法要再更正一次**

### 9.1 單獨能力（`test-backend-ops perf -o MUL_MAT_ID -b MTL0`，**形狀就是我們的 MoE 形狀**）

產物 `Backup/l201_accel/kbench/mmid_perf.txt`；case 為 `n_mats=256, n_used=8, n=1`（＝1 token、8/256 experts）：

| case（我們每層的三條 GEMM） | µs/run | 權重 bytes/run | **GB/s** |
|---|---|---|---|
| `m=512, k=2048`（gate／up） | **67.45** | 3.211 MB | **47.6** |
| `m=2048, k=512`（down） | **115.28** | 3.211 MB | **27.9** |

⇒ **每 token 的 expert GEMV 單獨成本 ＝ (67.45+67.45+115.28) µs × 40 層 ＝ 10.0 ms**。
（bytes/run 以 IQ3_XXS＝98 B/256 權重＝3.0625 bpw 換算；`16.78 MFLOP/run` ＝ 2·8·512·2048 對得上。）
參考基準（大塊連續、09-22）：`MUL_MAT iq3_xxs 63.9 GB/s`、`q6_K 100.5 GB/s`
⇒ **dequant／查表成本 ＝ 1.57×**（`SHAPE_ROOFLINE_2026-09-22.md:91-92`）。

### 9.2 卡在哪一項（三選一，用既有讀數逐一對質）

| 候選 | 判 | 證據 |
|---|---|---|
| **SIMD 群**（`CGC_MMV_NSG`） | ⛔ **已被否證** | **1..32 全射程掃過**：gate 通道六值跨度 **0.6 pp**；`nsg≥16` 反而退（32.2%／21.4%）（`SHAPE_GAP_TO_TARGETS_2026-09-24.md` §3.2） |
| **dequant** | ⚪ **真實但有界、且不是槓桿** | iq3_xxs 只有 q6_K 的 **64%**（1.57×）。但那是**量化型別**的成本，而量化已被 operator 凍結 ⇒ 不是可動的路 |
| **occupancy／execution** | ★ **binding** | ① `gpu_busy_sum = 66.15 ms` **> `wait` 52.76 ms（125%）**（緩衝重疊）⇒ **GPU 全程在忙**，不是 launch 延遲；② `gap` 只有 **7.6 ms** ⇒ 幾乎沒有 idle 可回收；③ in-situ 的成本**散在 2365 個 named work node** 上（見 9.3） |

★ **附帶：這也把 `ggml-metal-context.m:143-151` 留的 (A)/(B) 分叉判掉了** ——
(A)「GPU 真的忙 ⇒ GEMV 是 execution／occupancy-bound」**成立**；
(B)「GPU 很快跑完、時間多在 launch／completion 延遲 ⇒ 該消滅 GPU→CPU→GPU round-trip」**不成立**。
（讀數：`Backup/l201_accel/leafonly_d1b/d1b.stderr.log`，柵欄 OFF ⇒ 儀器有效，`skipped=3`。）

### 9.3 為什麼這條路到不了 20+ —— in-situ 的 per-kind 歸屬

`CGC-GPUNODE`（D1，`Backup/l201_accel/leafonly_d1/d1.stderr.log`，步時 93.86 ms）：

```
work-attributed 65.01 of 93.86 ms (69.3%) | named_work_nodes=2365 | residual=28.85 ms (30.7%)
*bywork ffn_moe_      7.9%     （MoE 全家）
*bywork node          8.8%     （單節點類）
*bywork cache         5.3%
*bywork norm          5.1%
*bywork conv          3.7%
per-kind: ffn_moe_gate 1.0% / ffn_moe_up 1.0% / ffn_moe_down 1.4%   ← MoE GEMV 三條合計 3.4%
```

**⇒ MoE GEMV 三條 GEMM 合計只佔 GPU 忙碌的 3.4%（`ffn_moe_` 全家 7.9%）；把它全部變免費，步時只降 ~3–15%。**

用微基準獨立核算也同調：expert GEMV 單獨 10.0 ms ÷ 65.7 ms ＝ **15.2% 上限**
⇒ 就算**整族 GEMV 變免費**，也只有 65.7 × 0.85 ＝ 55.8 ms ⇒ **17.9 t/s < 20**。
再叠加 `SHAPE_ROOFLINE §6` 的結論（把這族 kernel 寫到完美 ⇒ 端到端 **2–3%**），
以及 §3.1 的 dense GEMV **結案**（滿峰值只值 2.42%）⇒ **GEMV 全族的天花板約 5%。**

### 9.4 判詞（對 operator 的前提的直接回答）

> operator 的前提：「攻 kernel 是 20+ 唯一還有量級的路」。

**⇒ 不成立。** kernel／GEMV 全族的天花板約 **5%**，而 15.19 → 20 需要 **+32%**。
**這條路的量級差 6 倍以上，不是優化空間的問題，是方向問題。**

**⇒ 我 §8 的說法也要再更正一次**：`wait` 51.7 ms **不能**除以 1.171 GB 當作「GEMV 的等效權重頻寬」——
`wait` 包住**整條 GPU 管線**（attention／dense GEMV／norm／router／conv＋2365 個節點的入場費），
而 GEMV 只佔其中一小塊。正確的讀法是：
**「GPU 全程在忙（125% of wait），但忙的事分散在 2365 個小節點上，加權只有 69.3% 被歸因到具名節點。」**

### 9.5 唯一還沒被解釋的數（下一個該量的）

**`residual = 28.85 ms ＝ 步時的 30.7%`** —— 它**比任何單一具名節點都大**，而且**從來沒有人查過**。
在所有具名桶（含 `cache` 5.3%／`norm` 5.1%／`conv` 3.7%）之外，這 30.7% 是這張圖上最大的一塊。
⇒ 下一步若還要找 20+，該問的是「**這 28.85 ms 是什麼**」（是 pool 的 gather／搬運？KV？還是 2365 個節點
各自的入場費加總？），而不是繼續打磨 GEMV。

---

## 10. ★★ 28.85 ms 的 residual 查清了：**它是歸因假象，不是隱藏成本**（2026-10-02 22:0x）

### 10.1 先讀儀表自己的定義（`ggml-backend.cpp:3134-3149`）

```
residual = ns_total − ns_kind_work_ns
ns_total        ＝ 這一趟「cb 的 GPU 時長總和」
ns_kind_work_ns ＝ 其中被歸因到「具名工作節點」的部分（wcntw）
```
同一段的原文註解：
> The residual is the same quantity the op-keyed table reports as `total - sum(wcntw)`
> (**10.6% of segment busy at n_cb=16, 51.7% at n_cb=63**), and it **GROWS with the number of
> command buffers** — which is why the residual is the thing that says **"do not quote an absolute
> seg_busy"**, not the column itself.

⇒ 也就是說：**它的大小是「切法」的函數**，而它之所以存在，是因為**名字分類迴圈會跳過「空名節點」**
（`ggml-backend.cpp:2539-2542`：*"the name loop above SKIPS empty-named nodes (a name is what it
needs), while the op of such a node is still perfectly well defined"*）—— 空名節點的 GPU 時間
就落進 residual。

### 10.2 實測證明（同一支臂，只改 cb 粒度；產物 `Backup/l201_accel/residual/`）

| 臂 | `ns_total`（逐 step） | 中位 | 被歸因質量 |
|---|---|---|---|
| A `CGC_CB_N_MAIN=1`（cb **多**） | 77.34 / 104.83 / 96.05 / 87.84 | **91.9 ms** | 57.73 / 74.94 / 66.80 / 61.68 |
| B 預設（cb **少**） | 80.43 / 102.34 / **187.43** / 96.12 | **99.2 ms**（去離群 93.0） | 50.52 / 69.80 / 76.76 / 72.65 |

⇒ **`ns_total` 在兩種粒度下為 91.9 對 99.2 ms（去掉 B 的離群 step 後 92.0，差 ~2%）**
⇒ **同一份工作、只是切法不同**。照預註冊判別句（`相差 ≤5%` ⇒ 假象）**判：假象。**

★ 附帶：本輪**沒有**重現註解說的「residual 隨 cb 數增長」的方向（A 多 cb 的 residual% 29.2 <
B 少 cb 的 34.5）—— 那段註解（09-18/19）講的是 `n_cb=16` 與 `n_cb=63` 的另一個區間。
兩個讀數不矛盾：**判定假象靠的是 `ns_total` 不變，不是 residual% 的大小。**

### 10.3 判詞

**那 28.85 ms 不是一塊「可回收的成本」，它是「cb 時間中沒被歸因到具名節點的部分」——
主要由空名節點的 GPU 工作 ＋ cb 級的不可歸因時間組成。因為 `ns_total` 不隨切法改變，
把它的 % 做大做小都動不到真正的工作量。**

⇒ **§9.5 的那條線索死。** 而這也第三次從側面確認同一件事：**這台盒子上，
decode 的 GPU 時間就是那麼多，可歸因／不可歸因只是切法問題。**

⇒ **累計（本日）：一個一個被自己的預註冊判據否證掉的靶 ——**
① 搬運通道（§8：只值 7.5 ms／11%）→ ② GEMV／kernel 等效權重頻寬（§9：全族天花板 ~5%）→
③ residual 30.7%（§10：歸因假象，總量不變）。**沒有一條有 20+ 所需的量級（+32%）。**

### 10.4 若還要再往下挖，唯一沒試過的儀器

`CGC_GPU_NODES_MATRIX=1`（**每個 command buffer 一行**）與 `CGC_GPU_NODES_TRACE=1`（**原始逐節點**）
—— 它們能把 residual 攤成「哪些 cb／哪些節點沒被歸因」。但**先寫死判準**：
只有當那些 cb 的內容是**開銷（非工作）**時才構成槓桿；若是空名節點的真實 GPU 工作，那就不可回收。

---

## 11. residual 第二槍（MATRIX／NSM 設計矩陣）：**儀器在此不可用 —— 設計矩陣秩虧**

### 11.1 這兩支儀表是什麼（讀自 `ggml-backend.cpp:2609-2620` / `:1997-2010`）

- `CGC_GPU_NODES_MATRIX=1` → **每個 command buffer 一行** `CGC-NSM a= b= dur_ns= nk= <kind>:<cnt>…`
  ＝ 設計矩陣 `dur_i = Σ_k cnt_ik · t_k`，**離線用最小二乘解出每個 kind 的單節點成本**。
  它存在的理由（原文）：`n_main = MAX(64, 0.1·n_nodes)` 讓每個 main buffer ≥64 節點 ⇒ 任何住在裡面的
  kind 都被除以 ≥64（**實測 `ffn_moe_gate` cntw 1.91 ms vs ub 122.37 ms，而 seg busy 才 271.94 ms**）。
  ⇒ **`residual ≡ wcntw 與 ub 之間那段歸因不確定性`。**
- `CGC_GPU_NODES_TRACE=1` → 最熱 command buffer 的**原始節點名** ＋ 範圍大小直方圖。

### 11.2 實跑（**我們的 MoE 形狀**、MTP-off、柵欄 OFF 以保儀器有效）

臂：`CGC_GPU_NODES=1;CGC_DECODE_PROFILE=1;CGC_GPU_NODES_MATRIX=1;CGC_GPU_OPS=1;CGC_GPU_TIMING=1`
—— 產物 `Backup/l201_accel/nsm3/`（**123,584 行** NSM、53 個 step）。
用倉庫自己的解析器＋求解器（`scripts/check/gdn_split.py` 的 `parse_steps` / `fit_kinds`）：

```
steps=53  with_nsm=52  NSM buffers=121372
fit: r2 = 0.029   cond_ok = False   n_kinds = 45
```

**⇒ 判：設計矩陣秩虧、成本不可辨識。** 三條證據彼此獨立：
1. **R² = 0.029** —— 模型對 `dur_i` 幾乎沒有解釋力。
2. **`cond_ok = False`** ＋ 解出**負成本**（`conv −976.3`／`beta −341.2`／`(other) −332.2` µs/node）。
3. **MoE 四個 kind 全部 `patterns = 2`** —— 依求解器自己的規矩，`patterns < 3` ⇒ `ident = weak`
   ⇒ **「它掛著一個它不擁有的成本 ⇒ 標記、永不引用」**。

### 11.3 為什麼（儀表自己的設計說明）

`gdn_split` 的 docstring 寫得很清楚：**計數矩陣本來就是秩虧的（實測 44 個 kind 有 20 個）**，
而且 `verify_marginal.nsm_attribution` 明說它的輸入是**多個不同寬度的臂**：
> *"the marginal per-token split needs buffer compositions that only exist at different widths, and the
> widths live in different arms (k=1 → T=2 … k=3 → T=4). A launch is a parameter here, not noise."*

⇒ **我們這條臂是 MTP-off、只有寬度 1 ⇒ 沒有跨寬度的組態變化 ⇒ 設計矩陣退化。**
（同一個原因也讓 `verify_marginal --nsm` 直接拒跑：`no trunk step in widths [2,4]`。）

### 11.4 判詞

**MATRIX／NSM 這條路在「我們的 decode 臂」上不可用** —— 它的前提（多寬度）在 MTP-off 下不存在。
⇒ **residual 的性質維持 §10 的判詞**（`ns_total` 對切法不變 ⇒ 歸因假象），
本節只補上一條：**用設計矩陣也分解不開它（秩虧）**。

⇒ 若還要往下，只剩 **`CGC_GPU_NODES_TRACE=1`（最熱 cb 的原始節點名＋範圍大小直方圖）**一支沒試過。
判準仍照 §10.4 先寫死：**只有當那些 cb 裡是「開銷（非工作）」才構成槓桿。**

---

## 12. ★★ 順帶修掉一個我自己造成的**靜默回歸**（19 個 parser）

- **症狀**：`verify_marginal.py --nsm <log>` 回 `no CGC-DECPROF step rows` —— 而那個 log 裡有 **477 行**。
- **根因**：`scripts/check/verify_marginal.py:128` 的 `STEP` 正則以 `… submit=(%) ntok=(…)` 結尾，
  而 **commit `b96ee4989`（我加的 D2「治療已施加」見證）把 `splits=%lld` 插在兩者之間**。
  `grep -rln "submit=.*ntok=" scripts/check/*.py` ＝ **19 個檔案**；而 `splits=` 是 **write-only**
  （全 `scripts/` 沒有任何 parser 讀它）⇒ **成本全在破壞、收益為零**。
- **修法（照本檔自己的規矩）**：`ggml-backend.cpp` 裡兩處既有註解早就寫過 ——
  *"Deliberately a NEW line tag so the existing … parsers keep seeing exactly the format they were
  written for."* ⇒ 把 `splits=` **移出** DECPROF 行（恢復原格式），見證改印**新 tag**
  `CGC-LEAFSPLIT: step=%lld splits=%lld`（且僅在 `cgc_split_n > 0` 時印 ⇒ 未走該路徑零噪音）。
- **驗證**：`.o` 22:19:45 ＞ src 22:19:38（新鮮）；**D5 PASS**（`--tag fmt-repair-2026-10-02`：
  **M1/M2/M3 9/9、`comparable=True`、`config_diffs=[]`、`ok=True`**）⇒ 逐位元等同。
- **lesson `eng-mh-0082`**：**「純列印」也可以是破壞性改動 —— 判準是它改不改「既有行的格式」，
  不是改不改數值；而格式破壞是靜默的**（下游只會說「沒有資料列」）。
- ⚠ **本日儀表前提坑的完整清單**（都是「缺了不報錯、只安靜不印／不匹配」）：
  ① `CGC_GPU_NODES` 需 `CGC_DECODE_PROFILE`；② `gdn_split.STEPHDR` 需行尾
  `| layer gpu_sum=… union_sum=… gap_sum=… ms` ⇒ 需 `CGC_GPU_TIMING`；
  ③ 起跑閘是壓縮機**流量**（我輪詢 QUIET、閘門讀 BUSY ⇒ 差在取樣窗）；
  ④ ……而 ①② 這類「格式前提」正是 19 個 parser 被一行 `splits=` 打斷的同一類問題。
