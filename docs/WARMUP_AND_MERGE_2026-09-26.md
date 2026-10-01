# 從 edge0 能搬的三件事：一件量翻了它的數字、一件量出我們預設開關是個 no-op、一件不值得做

**Date** 2026-09-26 · carrier Nail IQ3_XXS-denseIQ4X (12.72 GiB) · 本檔所有讀數的盒況見 §0
**Source** `/Users/alexchuang/Documents/edge0`（Edge0-AI/Edge0, MLX 後端, commit 已在本地）

---

## 0. 盒況（每一個數字都帶著它）

| 讀數 | swap used | free | inactive (cache) | thermal |
|---|---:|---:|---:|---|
| `--instrument-check` | 5343.4 MB | 402.7 MB | 4279 MB | n/a（無引擎） |
| `--box-gate` | 5343.4 MB | 334.0 MB | 4522 MB | n/a |
| 正式臂（hint/control） | 5343.4 MB | 273.3 MB | 4586 MB | n/a |
| advice sweep | 5343.4 MB | 202.6 MB | 4607 MB | n/a |

**swap 5.3 GB／swap 檔只剩 784 MB free** ⇒ 這是一個被榨乾的盒子，不是乾淨槽。契約 §3.3 的規則是
「可引用性與 swap 解耦、只由 thermal ＋ rep 散度裁決」，而本檔的項目**不經引擎、沒有 rep**，所以
它們的判準是工具自己帶的三道自我檢查（儀器自證、盒況閘、對照臂）。凡是被盒況污染的執行，
工具**拒簽**而不是給一個數字——那次拒簽本身在 §2.2。

---

## 1. ① 「macOS 的 madvise 只暖一半，所以要整檔順讀」——**方向對，數字不成立，而且它證明我們現有做法是對的**

edge0 `src/edge0/streaming/mmap.py::seq_read()` 的原話：

> macOS madvise only warms roughly half the file, so a full read is the reliable way to remove
> per-expert page-fault cost from the first request.

### 1.1 儀器：`mincore` over `MAP_SHARED`，先自證再用

工具的 `--instrument-check` 在真檔真盒上跑（這個輸出是**儀器有效性的前提**，不是結果）：

```
cold_fraction 0.0    cold_read 1563 MiB/s      # 從沒讀過的範圍
warm_fraction 1.0    warm_read 14286 MiB/s     # 同一範圍讀一次之後
speedup 9.1x  ->  VALID
```

⇒ 冷 0.000／暖 1.000，且讀速差 9.1×；兩者互相印證。`--box-gate` 再確認這台**有能力**保留讀過的頁：
`[7680,+64) MiB` 讀時 1463 MiB/s、之後 1.000 resident 且 14658 MiB/s（10.0×）。
所以下面「hint 暖不起來」**不是**「快取留不住」的假象。

### 1.2 正式臂：hint 只暖 0.4%，對照臂 0.0%（rc=0，非 ENOMEM）

| range | arm | madvise rc | resident | 暖了多少 |
|---|---|---:|---|---:|
| [8192,+256) MiB | **HINT** | 0 | 0.000 → 0.004 | **1.0 MiB** |
| [9216,+256) MiB | **HINT** | 0 | 0.000 → 0.004 | **1.0 MiB** |
| [9728,+256) MiB | control | — | 0.000 → 0.000 | 0.0 MiB |

（跳過的兩臂是 [7168,+256) 與 [8704,+256)，起跑 resident 0.0039 —— 那是前面同一個 offset 的 hint
留下的那 1 MiB。臂自己**跳過非冷範圍**，所以它們不會污染中位數。）

**判定**：`MADV_WILLNEED` 在 256 MiB 的冷範圍上暖 **1.0 MiB ＝ 0.4%**，對照臂 0.0%。
edge0 的「大約一半」在本機**不重現**；但他們的操作結論——「別信 hint，去做一次順讀」——**成立，
而且比他們講的更強**：順讀暖 100%（1.1 節的 1.000）。

**我們現況**：我們的池暖機 `llama_expert_cache_prewarm_hot`（`llama-context.cpp:1976`）本來就是**真
pread**，不是 hint ⇒ **不需要任何改動**。這一項的價值是把「用 madvise 省掉那次暖機讀取」這個省
法在它被寫成程式之前先判死。

### 1.3 順手量到的兩個平台事實（下一個人會踩）

* `madvise(WILLNEED)` 的**大小上限存在但是狀態相依的**：同一段掃描裡 32/64/128/256/512/768 MiB 回 0，
  1024/2048 MiB 回 **-1 errno=12（ENOMEM）**；而在記憶體更緊之後，**連 64 MiB 都回 ENOMEM**。
  ⇒ 這不是「長度上限」，是「核心為 readahead 配置不到就失敗」。所以任何以 madvise 為機制的程式
  都必須**檢查 rc**，而任何拿它量出來的效果都必須**把 ENOMEM 的執行判為無效**（工具已如此，見 1.4）。
* 1.0 MiB 這個量在所有 hint 臂上**完全一致**（0.0039 × 256 MiB）⇒ 這個呼叫有一個固定的、與範圍大小
  無關的小讀取窗。它不是「逐漸暖」，是「幾乎不動」。

### 1.4 這支工具自己犯過的兩個錯（都留著，因為它們是這條線的典型病）

第一版用**讀取時間**當儀器，得到「hint 暖了 72%」。那是假的：協定裡的 pass A 已經把整個範圍讀過
一次 ⇒ 它量到的是**自己造成的暖**。改成 mincore 之後，同一個問題的答案是 0.4%。
第二版（更早）斷言「mincore 在 macOS 無效、永遠 1.000」。那是**從一個案例推廣過度**：那個 1.000 是
一顆**剛寫入**的檔（本來就在快取裡），量得完全正確。真檔冷範圍是 0.000、暖範圍是 1.000。

⇒ 現在工具的三道閘是從這兩次錯誤直接長出來的：儀器自證（冷/暖雙向 + 讀速）→ 盒況閘（快取留不留得住）
→ 對照臂（沒有對照的百分比只是形狀，不是效應）。跑法：

```bash
python3 Backup/rerun/madvise_warm_probe.py --instrument-check   # 儀器是否可用（rc 3 = 不可用）
python3 Backup/rerun/madvise_warm_probe.py --box-gate           # 這台現在留得住頁嗎（rc 4 = 留不住）
python3 Backup/rerun/madvise_warm_probe.py --arms 6 --range-mib 256 --wait 2
python3 Backup/rerun/madvise_warm_probe.py --selftest           # 10/10
```

---

## 2. 意外發現：**prod-new 預設打開的 P1/P2，在這台機器上不動任何一頁**

### 2.1 起因

`madvise` 的 ENOMEM 讓上一個 session 的 advice 列舉（`/tmp/advice_enum.json`）全回 `rc=-1`，
**所以「DONTNEED 到底有沒有丟頁」從來沒有被觀察到**。今天補上，三種獨立讀數都指向同一個答案。

### 2.2 量測

| 對象 | 呼叫 | rc | 讀數 |
|---|---|---:|---|
| file `MAP_SHARED`，64 MiB **已暖** | `MADV_DONTNEED` | 0 | resident 1.000 → 1.000（丟 0.0%） |
| 同上 | `MADV_FREE` / `SEQUENTIAL` / `WILLNEED` | 0 | 1.000 → 1.000（丟 0.0%） |
| anon `MAP_PRIVATE`，64 MiB **已寫** | `MADV_DONTNEED` | 0 | resident 1.000 → 1.000，RSS 74 → 75 MiB |
| 同上 | `MADV_FREE` / `MADV_ZERO` | 0 | 同上，RSS 不變 |
| anon 8 MiB，memset `0x5A` | `MADV_DONTNEED` / `FREE` / `ZERO` | 0 | **再讀仍是 0x5A**（內容沒被丟） |

最後一列是決定性的：如果頁真的被丟掉，再讀應該是 0（或零填充）；它回的是我們寫進去的 pattern。

### 2.3 為什麼這值得單獨寫一節

`scripts/run_server.sh:346-355`（prod-new profile）**預設 armed** 三件事，其中兩件與此有關：

```sh
[ -z "${CGC_EXPERT_SKIP_READRAW+x}" ] && CGC_EXPERT_SKIP_READRAW=1   # P0
[ -z "${CGC_POOL_MADVISE+x}" ]        && CGC_POOL_MADVISE=2          # P1+P2  ← 這一個
[ -z "${CGC_B_SCHEME+x}" ]            && CGC_B_SCHEME=1
```

而 `llama-expert-cache.cpp:774-799` 對 P1/P2 的機制寫得很明確：

> The 8 GiB pool is malloc'd ANONYMOUS memory. ... the kernel writes them out even when the very
> next instruction overwrites them. **madvise(MADV_DONTNEED) says "just drop them".**

⇒ 「丟掉它們」這個動詞在本機**沒有實現**。這**不動** P0（P0 是配置決策：不要 read_raw 進 heap，
與頁面建議無關），也不動 B_SCHEME。它動的是 P1/P2 的**理由**。

### 2.4 更強的發現（零 GPU，直接來自既存 log）：**交付配置裡 P1/P2 根本不會被呼叫**

上面兩節是「呼叫了就沒有效」。但交付用的池**不是**那個 malloc 池。最近六份啟動 log 每一份都寫：

```
llama_expert_cache: L4 metal pool: 143 slots/layer, regions adopted from expert tensors
```

而 `run_server.sh:346` 預設的 `CGC_POOL_MADVISE=2` 要作用，得先過兩道閘：

| 閘 | 來源 | 交付配置的結果 |
|---|---|---|
| `pool_region()` 先回 `pool_ext`（採納的 Metal 區域），沒有才回 malloc 池 | `llama-expert-cache.cpp:757-765` | **回 Metal 指標** |
| P1 的 `cgc_in_malloc_pool(dsts[i])` | `:3638`，掃 `cache->pool[l][k]` 向量 | 指標不在 malloc 池內 ⇒ **false** |
| P2 的 `cgc_discard_pool_slot()` | `:839-847`，`if (v.empty()) continue;` | malloc 池沒被配置 ⇒ **整個迴圈空轉** |
| 代碼自己的警告 | `:785` | *“Metal's pool_ext is NOT anonymous malloc — never pass those pointers here.”* |

⇒ 在**我們出貨的這個 profile 上**，那根開關是一次也不會呼叫 `madvise` 的。它不是「syscall 無效」，是**守衛正確地拒絕它**（Metal 區域本來就不能丟頁，丟了就是把 GPU 的位元組抽走）。而註解裡的前提
（*“The 8 GiB pool is malloc'd ANONYMOUS memory”*）描述的是**另一條配置**（`CGC_POOL_SPLIT` 的 legacy malloc 池），不是我們跑的這條。

**兩個層級要分清楚**：（平台層）即使它被呼叫，在這台也不丟頁（§2.2）；**（配置層）在交付上它連被呼叫都不會。**

### 2.5 也去跑了一趟：**256 MiB 的臂在這個盒子裡沒有解析力（工具自己拒簽）**

P1/P2 的目的是**壓 swap 寫出**，不是「立刻歸零」，所以「內容還在」不足以判死它們。真正的可否證
實驗是：各配置一塊髒匿名記憶體、一臂 `MADV_DONTNEED` 一臂不呼叫、比對那塊記憶體**離開 RAM 的
比例**（以及落點是 swap 還是壓縮器），ABBA 交錯。工具是 `Backup/rerun/madv_swap_ab.py`
（自測 11/11），三個欄位分工：`own SWAPPED`（`vmmap --summary` 的製程歸屬，主判準）、
`left RAM = 1 − resident`（`mincore`）、`box compressor`（落點）。

**結果（ABBA D,C,C,D，各 256 MiB，5 s 取樣 ×4）**：

| arm | own SWAPPED | left RAM | content | madvise rc |
|---|---:|---:|---|---:|
| D | +0.0 MB | 0.000 | intact | 0 |
| C | +0.0 MB | 0.000 | intact | — |
| C | +0.0 MB | 0.000 | intact | — |
| D | +0.0 MB | 0.000 | intact | 0 |

⇒ 兩臂**完全沒有任何頁離開 RAM**，box compressor 幾乎不動（−4.4 vs +0.6 MB）。工具依凍結規則
判 **INVALID**：control 臂 100% 駐留 ⇒ 兩臂都留在 RAM 的實驗不可能顯示出差異。

**為什麼（這是這一輪最有用的機制事實）**：盒子上可回收的**乾淨檔案快取有 6.2–6.4 GB**，而
核心先踢快取、才輪到髒匿名頁。量到的取證（兩臂對照，5 s 取樣）：`free` 294 MB、`cache` 6252 MB，
我們那 256 MiB 塞得進去 ⇒ 核心**不需要**驅逐任何東西，`swap_used` 與 `cache` 在整段觀察裡都沒動。

**工具因此自己算出「多大的臂才有解析力」並拒絕過小的臂**：

```
resolving size for this box: >= 5689 MiB (= free 438 + cache 6275 - margin 1024)
REFUSED: a 256 MiB arm is smaller than the 5694 MiB this box can absorb without evicting
         anything ... Re-run with --mb 6144, or --force to explore anyway.
```

而 6 GiB 的臂還需要 swap 餘裕，閘門第二段會講明白：

```
REFUSED: this arm needs ~7168 MiB of swap headroom and only 937 MiB is free.
         That is the clean-slot precondition: run it right after a reboot,
         or with --force to accept the churn.
```

⇒ 實驗**設計完成、可重現、判準寫死**，但它在這台盒子的**任何時刻都只有一個合法的起跑點**：
slot 的 `free + cache` 必須小於臂大小、且 swap 餘裕足夠。實務上就是**剛重開機的窗口 + 6–7 GiB 的臂**
（也就是引擎自己的 regime：12.7 GiB 模型 + 8 GiB 池壓在 16 GB 上）。引擎那邊的對應讀數已經存在：
`swap_owner_probe.py` 量到**引擎自己的** SWAPPED 峰值 **1331 MiB** —— 那正是 P1/P2 宣稱要作用的
那一群頁。所以有兩條路，成本差很多（見 §6）。

---

## 3. ② 「預設換路徑＋把量測數字寫進註解」——套到我們自己預設 armed 的開關上

edge0 的模板（`EDGE0_BUILD_PREAD=1`）：**預設值本身就是一個有數字的判決**，而判決和數字寫在同一個
地方（`streaming/layer.py` 的註解裡有 23.6 → 17.0 ms/token、360 → 740 MB/s、裝置上限 823 MB/s）。

把它套到 prod-new **預設 armed** 的開關上，逐項要求「claim／載體／盒況／狀態」四欄。**凡是查不到
出處的一律寫 UNKNOWN——不發明數字**：

| 開關（預設） | 它宣稱的效果（source） | 載體／盒況 | 狀態 |
|---|---|---|---|
| `CGC_EXPERT_SKIP_READRAW=1` (P0) | expert 不再 `read_raw` 進 heap，省 ~10.9 GiB 匿名駐留（`run_server.sh:346`） | 16 GB / swap 5.3 GB | **CLAIMED**（本次未重測；先前 P0 臂的證據是記憶體降幅，非 t/s） |
| `CGC_POOL_MADVISE=2` (P1/P2) | fill 前／evict 時丟頁以壓 churn（`:348`，`llama-expert-cache.cpp:774`） | 同上 | **DEAD**：§2.35 —— 交付配置走採納的 Metal 池，守衛使它一次都不呼叫；而即使呼叫，這台的 `MADV_DONTNEED` 也不丟頁（§2.2） |
| `CGC_B_SCHEME=1` | 熱門優先替換，讓 miss 收斂（`:349`） | 同上 | **UNKNOWN**：本次找不到帶盒況的 A/B 出處 |
| `CGC_SERVER_PREFIX_REUSE_CKPT=1` | prefix 重用：重複 prompt 的重 prefill 由 208 token 降到 8 token；checkpoint 記憶體 129 vs 256 MB | 同 carrier | **MEASURED**（已 commit 的那條線，M1 9/9 通過） |
| `CGC_SPAC=1` / `CGC_SPAC_ALPHA=0.75` | 空間快取路由（`:87`） | 同 carrier | **MEASURED**（有 `p25-nospac` 對照臂） |
| `CGC_MM_BITIDENT=1` | 把 M≤8 的 matmul 釘在 bit-identical 路徑（`:19`） | — | 不屬效能欄：它是 **correctness pillar**，不該用 t/s 評估 |
| `CGC_SERVER_OA_ASYNC=1` / `CGC_SERVER_DENSE_IQ4X=1` / `CGC_SERVER_NO_SEQ_RM_PROBE=1` | 形狀／行為開關 | 同 carrier | **UNKNOWN**（本次未追） |

模板的價值就在這張表本身：**7 個預設 armed 的開關裡，只有 2 個帶著載體明確的實測數字**，
1 個今天被量成 no-op，其餘 4 個是 UNKNOWN。任何人要再翻一個預設值，就必須把同一列的四欄填滿。

---

## 4. ③ 「合併相鄰 expert 的 pread（gap≤2）」——**有數字，但不值得做**

edge0 有這條（gap ≤ 2 就併成一個 syscall，多讀的中間位元組可忽略）。我們有 merged-iov 但那條界限
沒有實測。用既有的 miss 捕獲離線算（`scripts/check/merge_gap_ab.py --miss-dump
Backup/phase_decomp/miss_dump_mtpon.txt`，19,006 筆／6,805 個 batch／41 層，0 GPU）：

| max_gap | syscalls vs 現況 | 多讀 | 每個省下的 syscall 要多付 | 划算嗎 |
|---:|---|---:|---:|---|
| 1（現況） | 55,743 (+0) | 0 | — | — |
| 2 | 54,576 (**−1,167 = 2.1%**) | 416.3 MiB | 374,097 B | 只有單次讀 > **433 µs** 才划算 |
| 3 | 53,580 (−2,163) | 1,127.0 MiB | 546,358 B | 需 > 633 µs |
| 4 | 52,530 (−3,213) | 2,250.8 MiB | 734,571 B | 需 > 851 µs |
| 8 | 48,777 (−6,966) | 9,604.9 MiB | 1,445,799 B | 需 > 1,675 µs |

要點：我們的形狀與 edge0 不同 —— 每個 expert 是**3 個分開的張量**（gate/up/down），所以「合併」
只能在**同一個張量內**的相鄰 expert 之間發生，救不了跨張量的那三段。以 5 µs/read 的系統呼叫成本代入，
gap=2 淨賠 500.4 ms／整份捕獲。⇒ **維持現狀；不要把 merge 放寬到 gap≤2**，除非先量到單次 pread
真的超過 433 µs（那要用 `--us-per-read` 的實測值重跑，而不是引用這個 4.7 µs 的樂觀值）。

判準已經寫死在工具裡（自測 3/3 綠），下一個人不必重新推導。

---

## 5. 這三件事的整合結論

| # | 能不能搬 | 一句話 |
|---|---|---|
| ① | **已經在做了** | 池暖機本來就是 pread；本輪的價值是把 hint 這條省法判死（它只暖 0.4%），並把儀器留下 |
| ② | **搬了模板，暴露了缺口** | 7 個預設 armed 只有 2 個有帶載體的數字；P1/P2 被量成 no-op |
| ③ | **搬了判準，結論是不做** | gap≤2 省 2.1% 的 syscall、賠 416 MiB 讀取；門檻 433 µs 已寫進工具 |

---

## 6. P1/P2 那格：**現在不需要窗口了**（§2.4 把兩條路都變成了低價值）

§2.4 出來之前，這格的下一步是「跑一對臂」。出來之後，**兩條路的預期報酬都掉了**：路 A（合成臂）量的是合成頁，
而那個開關在交付上根本不作用；路 B（引擎 A/B）仍然成立，但預期結果是一個 null（兩臂 own SWAPPED 都一樣，因為
madvise 沒被呼叫）。用一個乾淨窗口去買一個已經從程式碼與 log 推定的 null，是這個 repo 一直想避免的那種花費。

**建議的正確下一步（不花窗口，一行）**：把 `run_server.sh:354` 的 `CGC_POOL_MADVISE` 預設從 `2` 退回 `0`（或直接
移除那行），並把 `llama-expert-cache.cpp:774` 的前提註解改成「**僅作適用於 `CGC_POOL_SPLIT` 的 legacy malloc 池；
採用採納的 Metal 池時，守衛會使它失效，已由 log 證實**」。驗收：下一個交付臂的 log 裡仍應出現
`regions adopted from expert tensors`，而引擎行為與現況**逐位元相同**（因為它本來就是 no-op，所以這是這個改動唯一可
觀察的後果）。

若有人仍想要正向確認，路 B 的指令與凍結判準留在下面。

### 路 A：合成臂（把 §2.5 的實驗真的跑起來，需要一個乾淨 boot）

```bash
# 剛重開機的窗口，一條指令（工具自己會拒絕不合法的臂）
python3 Backup/rerun/madv_swap_ab.py --arms D,C,C,D --mb 6144 --wait 60 --every 10 \
        --json /tmp/madv_swap_ab_gb.json
```

* 前提：`free + cache < 6144 MiB`（工具自己算）且 swap 餘裕 ≥ mb+512（工具自己檢）。
* 成本：四臂共約寫入 10–20 GB 到 swap（同一顆 SSD 也是 pool 在讀的那顆）、一個窗口。
* 局限：量的是**合成頁**，不是池的頁。

### 路 B（推薦）：對**引擎自己**做同一個 A/B

```bash
# 同一個 launch 設定，只差一根開關；量引擎自己的 SWAPPED（不靠合成）
CGC_POOL_MADVISE=0 ./scripts/run_server.sh     # 對照
CGC_POOL_MADVISE=2 ./scripts/run_server.sh     # 交付預設
python3 Backup/rerun/swap_owner_probe.py --pid <engine-pid> --seconds 120   # 逐區歸屬
```

* 它測的是**真正的那個開關、真正的那群頁**：`swap_owner_probe` 已經量到引擎自己的 SWAPPED
  峰值 **1331 MiB**（none 臂），而 P1/P2 宣稱要作用的就是這些頁。
* 判準（跑前寫死）：`P1/P2=2` 臂的引擎 own SWAPPED 峰值相對 `=0` 臂下降 ≥30% 且 t/s 不變 ⇒ P1/P2 有效；
  兩臂 own SWAPPED 落在彼此 15% 內 ⇒ **default-ON 的 P1/P2 是裝飾**，應把預設退回 0 並在註解裡記下這個讀數。

（§2.4 已經把這條路的預期結果推到 null，所以它現在是「誰想要正向確認就做」而不是下一步。）
* 成本：兩次啟動（~10 min），需一個乾淨窗口（thermal NOMINAL、起跑 swap ≤ 2048）。沒有引擎原始碼改動 ⇒
  不需要 M1/M2/M3 閘門。

**兩條路都卡在同一個前置：一個乾淨窗口。** 路 B 更值（真開關、真頁群、成本低一個數量級）。
