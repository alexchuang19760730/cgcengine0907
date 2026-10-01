# 單次提交的正確版：設計、價格與否證條件（2026-09-28）

> ## ⚑ 結果（2026-09-29 00:41，A 半已實作並量測）
>
> **A 半成功。** 在 `graph_compute` 的 mask 回讀迴圈（`:4011-4042`）把 `ibuf`（該層本步被選中的
> id）餵給兩個「只活在 hook 裡」的成員維護者（`spac_update` ；`cache_step_union`），
> 並在 `run_server.sh` 補 `CGC_SPAC_DBG` 的轉發（觀察用）：
>
> | 閘 | 未補齊 | **補齊後** |
> |---|---|---|
> | 機制真的跑了 | `prefetch=0/0`、無 `CGC-SPAC` | **`prefetch=2854/0`**、`CGC-SPAC: feeds=1560 queued=6 n_prefetch=2129 dropped=0`、`CGC-RB-FEED: feeds=…`（我的餵料自己也有計數器） |
> | 提交時未駐留（預設 cell，中位） | **134/312 = 42.9%**（IQR 130–138） | **19/312 = 6.1%**（IQR 12–28） |
> | 尾 20 步 | 133（42.6%） | **18（5.6%）** |
> | 同趟 t/s | 24.88（VOID） | 25.64（VOID）—— **沒有變慢** |
>
> ⇒ 兩道閘（`prefetch` 離開 `0/0`；misses ≤10%）**都過**，而且**維護的成本沒有出現在步時上**
> （§3 憂慮的 18.7 ms/步 fill 似乎真的被藏住了；但兩筆皆 VOID，這只是**同band**，不是證據）。
> 剩下的 **~6%**（≈19 個 (layer, expert)/步）就是 B 半要重算的量——比原本的 134 少了 7 倍。
>
> 產物：`Backup/fillahead_2026-09-28/fed_default.{json,stderr.log}`；build 指紋
> `libllama.0.0.630.dylib` `ea089e41e1162df9 → 5a121936d18b1f3c`（**跨 build，不得與早先的數字直接比**，
> 但本對照是同一次建置的前後趟，且 `src/` 的 delta 只有本補丁）。
> 交付 cell（p0）的同一個量測尚未跑完（第一次被指令逾時殺掉），列為下一步。

**一句話**：把「那 43% 不做」換成「**那 43% 其實不需要做**」——因為**逐步路由是一步級可預測的**
（8 個席位 ≈ 上一步的路由 ⇒ 駐留率 **96.0%（預設 cell）／96.8%（交付 cell）**，放大到 143 席位只多 0.3pp）。
缺的不是預測能力，是**池沒有重定中心**：而重定中心的機器**已經在樹上**，只是它的餵料來自每層 hook
（`llama-context.cpp:7866`），而單次提交臂正是靠「跳過 hook」拿到那 2.1–2.7× 的。
⇒ 本文件把設計拆成兩半、給出**可查證的行號**、**價格表**與**否證條件**。

```
實測前提（今天，全部合規入口；t/s 一律 VOID，只讀計數器）
  誠實臂逐步駐留：預設 cell 143 席位 96.3%（124106/128920）；交付 cell 96.7%（124677/128920）
  席位曲線：      8 → 96.0/96.8%、16 → 96.5%、64 → 96.3%、143 → 96.3/96.7%
  單次提交臂：    提交時未駐留 42.9%（預設）／41.8%（交付）  ← 池凍結於 prefill，零 fill
```

---

## 1. 兩半，以及哪一半缺

| | 需要什麼 | 樹上有沒有 | 規模 |
|---|---|---|---|
| **A. 提交前補齊** | 用「最近幾步的 union」在**步與步之間**把缺的專家補進池（非阻塞、與下一步的 GPU 視窗重疊） | **機制有**（`prefetch_slot` ＋ `CGC_PREFETCH_SRC=prev\|hist` ＋ `CGC_PREFETCH_WINDOW`，`llama-context.cpp:2127-2260`）；**餵料沒有**（union 在 `:7866` 由 hook 寫，臂跳過 hook ⇒ 空集合） | 小：餵料改從 mask 回讀（`:4011-4042` 的 `ibuf` 本來就是「該層被選中的 id」）＋ `run_server.sh` 補 3 行白名單 |
| **B. 處理殘差** | 那 ~3.2–3.7% 未駐留的 (layer, expert) —— **逐專家**重算（不是整步退回） | 沒有（G4 的本體）；前置零件有（G3 零槽讓未駐留者貢獻 0、mask 回讀已能列出未駐留的 id） | 大：需要新的小圖／第二趟提交 ＋ 正確性驗證 |

**A 的證據（可查證）**：兩臂的 `prefetch=0/0` **完全相同**。原始碼註解說 `src=step` 時
「the hook just ensured (all resident) → prefetch_slot drops everything」——
但單次提交臂是**連 hook 都沒跑**，所以三個來源（step/prev/hist）拿到的都是空的 `cache_step_union`。

---

## 2. 為什麼殘差必須是**逐 (layer, expert)**，不能是「整步退回」

今天量到：每步 `nsel = 8 × 39 = 312`，殘差 3.2%（交付 cell，補齊後的最佳情形）。

| 量 | 值 |
|---|---:|
| P（這一步 312 個選取全乾淨） | **(1−0.032)^312 = 3.9 × 10⁻⁵** |
| P（某一層的 8 個全乾淨） | 0.968⁸ = **0.771** |
| P（39 層全乾淨） | 0.771³⁹ ≈ **4 × 10⁻⁵** |

⇒ 「預測到的話就單次提交、否則退回分段」**在整步粒度上永遠不會觸發**（~4e-5），
在單層粒度也只有 77% ⇒ 退回到「某些層分段」會把 39 次提交的結構成本帶回來大半。
**唯一可行的粒度是逐 (layer, expert)**：把每步 ~10 個未駐留的專家交出去，做一小段重算。

這與樹上已有的定價註記同向（`:6036-6037`：「the payoff is all-or-nothing PER HOOK CALL … the quantity
that prices it is P[every id of this call was predicted]」）——但那講的是**分段臂**的每個 hook call；
單次提交把粒度放大到**整步**，所以這個 all-or-nothing 問題在那條路上更嚴重，必須用逐專家重算繞開。

---

## 3. 價格表（把 20 放進算術裡）

誠實臂今天量到的 fill 成本（同一趟，390 步）：
`file_reads=82230`、`read_mib=2423.0`、`pread_usec=3.20 s`（**8.2 ms/步**）、`fill_wait_us=4.08 s`（**10.5 ms/步**）。

單次提交臂今天的速率是 **24.88 t/s ⇒ 40.2 ms/步**（VOID，只當上限探針）。
若正確版必須把那份 fill 付回來（那些專家**確實要讀**，這是正確性的定義）：

| fill 的隱藏程度 | 步時 | 速率 |
|---|---:|---:|
| 完全沒有隱藏（照誠實臂擋在關鍵路徑上） | 40.2 + 18.7 = **58.9 ms** | **17.0 t/s** |
| 藏一半（與下一步的 GPU 視窗重疊） | 49.6 ms | **20.2 t/s** |
| 全部藏住 | 40.2 ms | 24.9 t/s（＝今天那個 VOID 的上限） |

⇒ **這就是本設計的否證條件**：只有當 fill 能被藏在「前一步的 GPU 視窗 + sampler」之後**至少一半**，
算術才會回到 20 以上。而預取路徑的原始碼註解自述它的用途正是如此
（「overlaps the disk IO with the sampler + next step's whole GPU window instead of stalling the hook」）
⇒ 這個條件不是奢望，但**必須量到**（`prefetch=N/M`、`fill_wait_us` 的趟內增量、以及步時的分解），
不能因為機制寫在那裡就假設它會發生（本 repo 已經兩次把「旗標在 env 裡」誤讀成「機制跑了」）。

殘差的重算成本（B）**不是**本卡要價的：每步 ~10 個專家的單 token 前向，GPU 成本遠小於上面的 fill；
真正的成本是**第二趟提交**的 dispatch 與同步，那個要等 B 卡自己量。

---

## 4. 施工清單（精確到行）

**A（本卡；已實作，見檔首「結果」）**
1. `llama-context.cpp:4011-4042`（mask 回讀迴圈）：已在该迴圈把 `ibuf`（該層本步被選中的 id）
   餵給 `llama_expert_cache_spac_update`（SpAc utility EMA）與 `cache_step_union[il]`，
   **只在 `CGC_SEG_BATCH=1` 且池活著時**（誠實路徑完全不動）＋ 自帶 `CGC-RB-FEED` 計數器
   （「機制跑了」要有證明）。⚠ 已知限制：餵料由 `CGC_MISS_MASK_DBG=1` 的回讀迴圈驅動，
   那個回讀本身帶**額外一次 `sched_synchronize`**（分段臂上量到 11.5 ms/步，單次提交臂上 2–3 µs）
   ⇒ 這是**原型**：最終形態應該是不經回讀的 device-side 來源（或把 sync 拿掉）。
2. `scripts/run_server.sh`：補 `CGC_PREFETCH_SRC` 的轉發（3 行，仿 `CGC_PREFETCH_WINDOW` 的區塊，
   `:2261-2265`）。該旋鈕 09-13 被移出白名單（`:1482-1486` 的註解保留了「opt back in for A/B」）。
3. 閘門：`prefetch=` 必須離開 `0/0`；`CGC-MISSMASK-STEP misses/nsel` 從 42.9% 落到 ≤10%。

**B（另立一卡）**
- 用 mask 已經回讀到的未駐留 id，為那 ~10 個 (layer, expert) 做第二段小提交／重算；
  驗收是**正確性**（`answer_md5` 參考集）＋ 步時分解，不是 t/s。

**協調成本（必須一起寫）**
- `src/` 另有他線未提交的改動 ⇒ 這次 build 會把他們的東西帶進我的 dylib；
- **跨 build 的 A/B 無效**（`docs/G1B_G3_INSTRUMENTS_2026-09-26.md` §6 已記指紋序列）⇒
  產物必須記 `engine_build`，且不得與今天的 24.88／27.17 直接比；
- 今天另外兩條線在同一台盒子上跑量測／建置 ⇒ 跑前先 `ps`，並記窗口。

---

## 5. 這條路與「分段臂」那條路的關係

* 分段臂的問題是**結構成本**：39 次提交／步。誠實臂的步時 86 ms（11.6 t/s）裡，
  已被量到的 fill 只佔 18.7 ms ⇒ **其餘 ~67 ms 是每層提交／同步的結構**。
* 單次提交臂把那部分拿掉，換來 40.2 ms／步，代價是 43% 的選取不做事。
* ⇒ 兩條路其實問同一件事的兩半：**能不能用一次提交把正確性做完**。
  本設計的答案是「可以，前提是 A 接得上、且 fill 藏得住一半」（§3）。
* ⛔ 不要再把 27.17／24.88 當「效能的證據」引用（wrong-output 臂）；它們的正確用途是
  **上限探針**：本設計最多只能拿到 24.9，而目標是 20。

---

## 6. 引用邊界

* 本文件的**所有** t/s（24.88／27.17／11.6）都是 VOID，且只用來做算術上限。
* 可引用的結構數：逐步駐留 96.3/96.7%、席位曲線 8→96.0/96.8%、未駐留 42.9/41.8%、
  `nsel=312/步`、`slots_layer=143`、`n_expert=256`、fill 成本 8.2+10.5 ms/步（同一趟）。
* 登記表：`scripts/check/claim_instruments.yaml` 的 `honest-decode-residency`／`decode-hot-set-8`／
  `g4-miss-per-step`（本文件不新增數字主張，只新增**設計**）。
* 立項卡：`scripts/check/charters/exp-singlesubmit-fillahead.yaml`（A 半）。

---

## 7. 量具綁定：把「旗標設了」與「量具真的動了」分開（2026-09-29 新增）

### 為什麼要這一節

交付 cell 的四場嘗試都沒有產物，而事後讀 `/tmp/diag_hang.log` 才看到：
`CGC-MM-PUB n_leaf=0 wrote=0`、`CGC-MISSMASK-STEP 0`、`CGC-RB-FEED 0` —— **三個計數器全部是 0**。

這一族病和 `claim_instrument_check.py` 處理的那族（數字比量具新）是同一族的反面：
那次是「量具當時還不存在」，這次是「量具在、旗標也設了，但它**沒接上去**」。後者更陰險，因為
**四道既有檢查全過**（開關在白名單、符號在原始碼、字串在建置產物、產物在磁碟），只有讀 stderr 才看得到 0。

⇒ 補上第五道：**它在這一輪真的有在跑嗎**。`llama-context.cpp:3943-3945` 自己寫過這條判準
（「A run that prints only n_leaf=0 rows is now proof the map never filled」），本節把那句話變成布林。

### 實現

* 新工具 `scripts/check/instrument_binding.py`：探針三態 `BOUND`／`ZERO-ONLY`／`NO-MARKER`，
  以及 `--log`（單場）、`--scan`（唯讀掃全部產物）、`--self-test`（29 條）。
  「要驗哪些探針」由臂的 env 推，但**只推「開關在、輸出就應該在」的**
  （`CGC_MISS_MASK_DBG` ⇒ mask 地圖；`CGC_SPAC_DBG` ⇒ SpAc 刷新）。
  `rb_feed` 這類「輸出本身需要 2026-09-29 補丁才存在」的探針**不由 env 推**，要 claim 明文宣告
  —— 否則「舊 build 的臂沒印它」會被誤讀成「量具沒動」，而那正是上面那族病的鏡像。
* 寫入端：`llama_bench_matrix` 把判定寫進產物的 `instrument` 欄位並在 stdout 印一行（`UNBOUND` 會吵）；
  `experiment_sync` 的「乾淨」= 歸因乾淨 ∧ 量具綁上，`instrument-audit` 子命令唯讀列出舊產物的狀態。
* 檢查端：`mindmap_void_check` 新增 `VOID-INSTRUMENT-UNBOUND`（與 `VOID-IN-RESULT` 分開，兩者的 why 不同）；
  `claim_instrument_check` 新增登記表欄位 `binding: [探針, ...]`。
* **政策不回溯**：沒有 `instrument` 欄位的舊 run 不會因為這條規則被翻成 VOID（一次政策改動不該靜默
  改寫整份語料）；要清就 `experiment_sync instrument-audit`（看得見的一步），
  `CGC_INSTRUMENT_STRICT=1` 則把 `UNVERIFIABLE` 也擋住。
* **2026-09-29 再往前一步**：上面這些都只是「記錄 + 事後檢查」—— 沒有一個地方會因為 UNBOUND 而不做事。
  現在它同時是**起跑／落地的硬條件**（拒跑／拒寫，rc=2／rc=4），見 §9。

### 第一次執行（全部是機器判定，唯讀）

| 產物 | 判定 | 證據 |
|---|---|---|
| `Backup/fillahead_2026-09-28/fed_default.json` | `BOUND` | `n_leaf=39`／`wrote=39`、`feeds=14000`、`spac=1560`、`prefetch=2854/0` |
| `Backup/g4miss_2026-09-28/ctl_segmented_default.json` | **`UNBOUND`** | 8 列 `CGC-MM-PUB n_leaf=0 wrote=0`，且 390 步裡 **0 列** `MISSMASK il=` |
| `/tmp/diag_hang.log`（交付 cell 崩掉那一輪） | `UNBOUND` | 三個計數器全 0、`MISSMASK il=` **0 列** |
| `g4miss2_default.json`／`g4miss_delivery.json`（42.9%／41.8% 的出處） | `UNVERIFIABLE` | 產物旁邊沒有並存的 stderr log 　**（→ §32：2026-09-29 已 backfill 成 `g4miss2_default.stderr.log`／`g4miss_delivery.stderr.log`，判定由 `UNVERIFIABLE` → `BOUND`）**|
| `Backup/phase_decomp/*/bench.json`（7 份） | `UNVERIFIABLE` | 同上 |

* 登記表 `fillahead-fed-miss` 因此多了一行 `✓ BINDING`（四個探針都動過）—— 一個**可機械複驗**的
  存活證明，而不只是一句「旗標有設」。
* `g4-miss-per-step` 維持 `QUOTABLE`（兩個數字的出處 JSON 沒有 log，但同一批列的兩份 cell 日誌
  各 14.9k 列 `MISSMASK il=`、`n_leaf` max=39 ⇒ mask 在兩個 cell 上都真的綁上了）；
  要不要把它寫成 `binding:` 由 claim owner 確認「那兩份日誌就是那兩筆產物」後再定。

### 兩個必須一起講的界線

1. **新發現，不是本次修改造成的**：誠實分段臂那一輪（`ctl_segmented_default`）武裝了 `CGC_MISS_MASK*`
   卻從沒填起來（390 步、`MISSMASK il=` 0 列）⇒ 它的 mask 統計全 0。它引用的三個數
   （96.3% 駐留、4814 misses、`read_mib 2423`）出自 expert-cache 自己的計數器，**不是**這個量具，
   所以那些數不受影響；但這一輪不得再被拿來支持任何與 mask 有關的主張。
2. **視窗的界線**：`CGC-MM-PUB` 只印**前 8 個** compute（`llama-context.cpp:3943-3945`：前 5 個是
   mask-less 的 prefill／warmup，第 6 個才是第一個 decode step），所以「全 0」也可能是
   「那個視窗一個帶葉的步都沒照到」。判定因此有一條**視窗退路**：若 `MISSMASK il=` 有列
   （沒有 placeholder 寫進圖就不可能有 miss 列）就判 `BOUND`。交付 cell 那一輪走不了退路（0 列），
   才判 `UNBOUND`。

### 怎麼用

```bash
python3 scripts/check/instrument_binding.py --log X.stderr.log --arm-env 'CGC_MISS_MASK_DBG=1'
python3 scripts/check/instrument_binding.py --scan 'Backup/**/*.json'      # 唯讀，2.8 s
python3 scripts/check/experiment_sync.py instrument-audit                 # 唯讀，列出舊產物
python3 scripts/check/claim_instrument_check.py                           # 登記表（含 BINDING）
```

rc：`0` = 沒有 UNBOUND；`1` = 至少一個 UNBOUND（與 `claim_instrument_check` 同一個慣例：
把它接到 CI 時要當成**報告**，不是回歸）。

---

## 8. Metal 工作集預算閘：池子 ＋ Metal 駐留 vs `recommendedMaxWorkingSetSize`（2026-09-29）

### 為什麼

§7 開頭那四場沒有產物的嘗試，其中一次的直接死因不是邏輯而是 **Metal 命令緩衝區 OOM**：
`ggml_metal_synchronize: error: command buffer 0 failed with status 5`
（`kIOGPUCommandBufferCallbackErrorOutOfMemory`，fail-stop）。而當時四道閘門一道都沒攔：
cell 口徑過、起跑 `available` 過、歸因不是它管的、產物也還沒寫。

Metal 的上限是**全機**的（不是本行程的），而這一輪要同時放進去的東西是兩項：
「池子（宣告的 `--expert-cache`）」與「Metal 駐留（`wired`，量得到的）」。
兩者相加超過上限時，OOM——或至少是「池子必然有一部分在 swap 上」——是幾何決定的，與盒子當時多安靜無關。

### 怎麼做（`memory_pressure.metal_gate`，判準與寫入端同一份）

```
pool（宣告）＋ Metal 駐留（量到的）＋ 保留（1024 MiB，CGC_METAL_RESERVE_MB）<= 上限
```

* 上限三個來源，順序就是「誰最接近正在跑的那個」：`CGC_METAL_WORKING_SET_MB` ＞
  **本次** stderr 的 `recommendedMaxWorkingSetSize` ＞ `sysctl iogpu.wired_limit_mb`（>0 才算）
  ＞ 快取檔 `Backup/metal_working_set.json`。
* **這台盒子上 `sysctl iogpu.wired_limit_mb` 回 0**（未設）⇒ 唯一能在 spawn **之前**拿到上限的方法就是快取；
  而 `memory_pressure` 本身不寫檔，所以寫快取的是產物端（`llama_bench_matrix` 跑完一輪就刷新一次）。
  冷啟動（沒快取、沒跑過）⇒ `armed=False`：**不是通過**，也不拒跑，並在 stdout 與產物裡說「這一輪沒有這根桿子」。
* 兩個半邊：`phase="pre-launch"`（起跑 wired）⇒ **拒跑**；`phase="peak"`（整趟峰值）⇒ 判讀那一輪的幾何。
* 引擎自己留了 OOM 一行時，那比預算算術硬：直接判「這一輪沒有可用的輸出」。

### 實測（用**真的**產物與日誌跑出來的，不是注入值）

```
$ python3 -c '...metal_gate(產物的 memory block, pool=8192, log_text=fed_default.stderr.log)'
上限 11453.25 MiB（引擎 stderr 本次）  池子 8192 ＋ Metal 駐留 12105 ＋ 保留 1024 ＝ 21321 MiB
⇒ 超 9868 MiB；最大項＝「Metal 駐留 12105 MiB」
（fed_default：起跑 wired 1972 MiB → 整趟峰值 12105 MiB）

$ 起跑那一半（用快取的上限）：池子 8192 ＋ Metal 駐留 1925 ＋ 保留 1024 ＝ 11141 MiB ＜ 11453 ⇒ 放行（餘裕 312 MiB）
  ~~ 起跑合格、但整趟會超：池子 8192 ＋ 上一趟峰值 Metal 駐留 12105 ＋ 保留 1024 ＝ 21321 MiB ＞ 11453 MiB
     ⇒ 這一輪的池子必然有一部分在 swap 上。起跑那道閘看不到這一塊，因為它取在 spawn 之前。

$ 交付 cell 崩掉那一輪（/tmp/diag_hang.log）：直接證據命中 ⇒ 無輸出，不進入預算算術。
```

### 三件必須一起講的事

1. **起跑合格 ≠ 整趟合格**。起跑那道閘用「起跑 `pages_wired`」，而那個讀值取在 spawn **之前**
   ——那時引擎那 12.1 GB 還不存在。所以「上一趟的峰值」是唯一能在起跑前講出這件事的數字，
   它被快取下來、在警告裡點名（但**不拒跑**：「必然換頁」不是「不准跑」，那條線由歸因閘畫）。
2. **峰值那一半刻意不接進「乾淨」**。這台盒子上每一趟 prod-new 都會超過（12105 ＋ 8192 ＞ 11453），
   把它接進 `is_clean` 等於一次把整份語料判死 —— 那是 operator 的裁決，不是這次的修改。
   現階段：寫進產物（`metal_gate`／`metal_gate_peak`）並在 stdout 點名。
3. **代價：這道閘真的會拒跑。** 8 GiB 池子在起跑時只剩約 300 MiB 餘裕 ⇒
   盒子只要再多 ~300 MiB 的 wired（別的 app、另一條線的 llama），這一輪就會被拒。
   這是「硬預算閘」應有的行為；要跑就得減壓或 `CGC_IGNORE_METAL_BUDGET=<理由>`（理由會進產物）。

### 怎麼用

```bash
python3 scripts/check/memory_pressure.py --selftest              # 78 條（含 metal 那 11 條）
# 冷啟動：從既有產物的 stderr 把上限＋上一趟峰值種進快取（一次性，可重複）
python3 -c "import sys;sys.path.insert(0,'scripts/check');import memory_pressure as m;\
print(m.remember_metal_ceiling(11453.25,'引擎 stderr:fed_default',peak_wired_mb=12105.45))"
CGC_METAL_WORKING_SET_MB=11453 python3 scripts/check/llama_bench_matrix.py --dry-run ...   # 手動指定上限
```


## 9. 第五道閘門從「記錄」變成產線硬條件（2026-09-29）

### 為什麼要動

§7 的判定當時有三個住處在*看*它（產物的 `instrument` 欄位、`mindmap_void_check` 的
`VOID-INSTRUMENT-UNBOUND`、`claim_instrument_check` 的 `binding:`），但**沒有一個地方會因為它而不做事**：
量具 UNBOUND 的場次照樣跑完、照樣寫進產物、照樣有人拿它的 t/s 去比。§7 抓到的那一輪
（`Backup/g4miss_2026-09-28/ctl_segmented_default`）就是這樣：四道既有檢查全過、量具八列全 0，
而它仍是「誠實分段臂」的引用候選。判定寫在哪裡決定了它是**註解**還是**閘門**。

### 兩個半邊（`harness bench` 這扇門）

| 時機 | 判定 | 動作 |
|---|---|---|
| 跑前 | 臂武裝了量具、卻沒開它的前提（`instrument_binding.static_precheck`；同一族病引擎自己也會印 `... without ...`） | **拒跑**：rc=2，不花 GPU 時間 |
| 跑後 | 產物的 `instrument.verdict` 有任何一臂是 `UNBOUND`／缺欄位 | **拒寫進決策面**：rc=4 |

「拒寫」≠「抹掉證據」：產物照寫本地檔（含判定與理由），只是**不覆寫 mindmap** ——
`auto-sync` 那一步在 `return 4` 之後，所以不會發生。量具判定寫進 `instrument_gate` 欄位
（`ok`／`waived`／`arms[]`／`rule`／`checked`），與 `instrument` 並存：
一個是「這一輪量到什麼」，一個是「這一輪能不能用」。

### 兩個洞，以及為什麼第二個要補

`harness verify` 不經過 `cmd_bench`：它把每一輪**直接交給** `llama_bench_matrix`（`arm_two_pass.py`），
所以閘門若只做在 bench 上，verify 這條路就只剩「記錄」。⇒ `cmd_verify` 現在把嵌在 `result.json`
裡每一輪的 matrix 產物攤平、跑同一份判準（`runs_unbound`），UNBOUND 且未豁免 ⇒
**HTML 仍生成**（看得到是哪一臂）但 **rc=4**，且判定寫回 `result.json`。
**政策不回溯**：閘門存在之前跑的舊 `result.json`（matrix 產物沒有 `instrument` 欄位）標成
`legacy=True` 並放行 —— 一次政策改動不該靜默改寫整份舊報告。

**還留著的一個洞（刻意的）**：`llama_bench_matrix` 自己只「記錄 + 吵」，不拒。
它三種呼叫者要不同行為（harness bench 要拒寫、arm_two_pass 要雙輪、分析腳本要唯讀），
所以「拒」住在呼叫端；直接跑 matrix 的人只會看到 ⛔ 那一行與產物裡的 UNBOUND，不會被擋。

### 怎麼用

```bash
python3 scripts/check/harness.py selftest                  # 跑前 6 條 + 跑後 4 條 + verify 5 條
harness bench  ... --require-instrument rb_feed,prefetch    # 明文宣告「這些探針這一輪必須動過」
harness bench  ... --waive-instrument "理由"                # 明知不可引用仍要寫（理由進產物）
harness verify ... --waive-instrument "理由"                # verify 那扇門的同一把鑰匙
CGC_IGNORE_INSTRUMENT_GATE=1                                # 環境變數版的鑰匙（兩扇門都認）
# rc=2 跑前拒跑；rc=4 量具 UNBOUND（bench 拒寫／verify 拒收報告）
```

### 已驗（不花 GPU）

* **bench**：`--charter scripts/check/charters/exp-singlesubmit-fillahead.yaml` ＋ 一份 UNBOUND 的假產物
  ⇒ rc=4、`instrument_gate.ok=False`、stdout 點名該臂、**`docs/mindmap/mindmap.json` 的 md5 未變**；
  同一份帶 `--waive-instrument` ⇒ rc=0 且理由進產物。
* **verify**：stub 掉 `arm_two_pass`（雙輪不真的跑），塞一組 `clean=N/A` ＋ `instrumented=UNBOUND`
  ⇒ 未豁免 rc=4、豁免 rc=0，`instrument_gate.waived` 留在 `result.json` 裡。


## 10. 成對保存：缺 stderr log 就自動不可引用（2026-09-29）

### 病根：log 落在 workdir，產物被歸檔

`llama_bench_matrix` 把 stderr 寫到 `--workdir/llama_bench_<tag>_<shape>.stderr.log`（預設 `/tmp`），
而**產物**由 `harness bench --json Backup/...` 落地。歸檔那一刻，產物就永遠少了另一半：
log 在 `/tmp` 裡等被清掉，或根本已經不在。實例就是 §7 抓到的 `g4miss2_default.json`／
`g4miss_delivery.json`（42.9%／41.8% 的出處）—— 唯讀稽核顯示：**520 支跑出來的產物裡 296 支缺成對 log**。
**（→ §32：這兩支已用 `--pair-backfill` 修好，全庫缺數 296 → 294。）**
從前這件事只能事後在 `claim_instrument_check` 看到 `UNVERIFIABLE`，而那已經太晚（產物在磁碟上、log 不在了，
驗不回來）。

### 寫入端（產物落地時就成對）

* 每一臂在**產物旁邊**寫一份不可被覆寫的 log：`<dir>/<stem>.logs/<safe_tag>.stderr.log`
  （唯一檔名 ⇒ 不會像 09-16 那次一樣被別輪的同名檔蓋掉）。
* 收尾（`main` 寫產物之前）再寫**合併版** `<dir>/<stem>.stderr.log`，含 `# cgc-pair: artifact=… arm=…`
  標頭與每臂分隔。這就是 sibling 慣例 `X.json` ↔ `X.stderr.log`。
* **順序是先 log、後產物**：中斷只會留下孤兒 log，不會留下一支無 log 的產物。
* `harness bench` 跑後自檢：產物旁邊沒有 log ⇒ **rc=5 拒收**（優先於 rc=4）。
  `harness verify` 逐輪檢查每一圈 `matrix.json`。

### 判準（唯一住處：`instrument_binding.pair_status`）

三態，且**說得出是哪一態**：

1. `sibling` —— `<stem>.stderr.log` 存在（最強）。
2. `workdir-name` —— 同名錄下存在矩陣從前那個寫法、且名字**由這支產物的臂名與形狀推得出來**的 log：
   `llama_bench_<safe_tag>_p…_n…_d…_r*.stderr.log`。形狀要從產物反推回**呼叫參數**：
   `n ＝ 最大 n_gen ＋ warm_skip`（`-n 128 --warm-skip 64` 的臂，它的 tg row 是 `n_gen=64`）。
   這比 sibling 弱（同一組 tag+shape 的別輪覆寫過它），所以 `.why` 直接寫明「要當引用依據就 backfill」。
3. 都沒有 ⇒ `paired=False` ⇒ **不可引用**。

「不是在跑的產物」（文件、mindmap、登記表）用**結構判定**排除：`is_run_artifact`（有 `tag`
且（有 `rows`／`refused_preflight`／`dry_run`／`state_gate`））。所以文件類產物不會因為沒有 log
被誤殺，而「豁免誰」也不是靠人記得。

### 檢查端（四處，同一份判準）

| 位置 | 規則 |
|---|---|
| `claim_instrument_check` | 新 verdict `VOID-NO-PAIR-LOG`，排在**最前面**（連 log 都沒有時，其他判定都失去根據） |
| `experiment_sync` | 乾淨 ＝ 歸因乾淨 ∧ 量具綁上 ∧ **成對**；`instrument-audit` 新增成對那一段 |
| `mindmap_void_check` | 新標籤 `VOID-NO-PAIR-LOG`（排在 `VOID-IN-RESULT` 之後、`VOID-INSTRUMENT-UNBOUND` 之前） |
| `harness`（兩扇門） | 跑後 rc=5 拒收（見 §9） |

### 修舊語料：唯讀稽核 ＋ 人工登記

```bash
python3 scripts/check/instrument_binding.py --pairs 'Backup/**/*.json'   # 唯讀，24 s，列出缺的＋可能在哪
python3 scripts/check/instrument_binding.py --pair-backfill <artifact.json> --from <那份 log>
```

`--pair-backfill` **不發明證據**：來源必須真的存在，而且新 log 的開頭會被寫上
`backfilled-from=<路徑>` 一行，所以「這份 log 是哪來的」永遠可查。已成對的產物拒絕覆寫。

### 這道規則對現有語料的影響（唯讀，機器判定）

* `claim_instrument_check`：**5 QUOTABLE → 3 QUOTABLE、8 VOID → 10 VOID**。
  **（→ §32：配對修好後回到 5 QUOTABLE／8 VOID，`g4-miss-per-step` 那一列重新 `QUOTABLE`。）**
  兩條新 VOID 都是因為引用了 `g4miss2_default.json`／`g4miss_delivery.json`：
  `g4-miss-per-step`、`fillahead-fed-miss`（後者只有 42.9% 那一半缺 log；6.1% 那一半是成對的）。
* `mindmap_void_check`：**0/55 → 1/55**。點名 `score-leaderboard`：它引用的
  `Backup/mtp_off_clean/res.json`（9.3107 t/s 的乾淨基線）與 `Backup/k3_pair_cert_*/…/launch01.json`
  旁邊都沒有 log ⇒ 那兩個數從今天起不可引用。
* 其他三支本來就已 VOID（入口／口徑），只是「為什麼」換成更前面的那一條。

⇒ 那不是「現象不存在」，是那些數字得先補上證據（或把 res 改成不主張吞吐）。

### 一個意外，與它換來的一條界線

用 stub（不跑 GPU、假產物）驗 rc=0 那條路時，`harness bench` 的 auto-sync 把那筆**假 run**
寫進了 `exp-singlesubmit-fillahead`（還複製到 `Backup/exp_runs/`）。已清除（run ＋ 複本），
並且加上一條界線：**只有 repo 內的產物才回寫決策面**（`--json /tmp/...` 是診斷用的）。
這一條有 selftest 釘住（`_inside_root`）。


## 11. 第三扇門：**同步本身**也要過閘（2026-09-29）

### 為什麼要有這一扇

§9 的 rc=4／rc=5 只在 `harness bench`／`verify` 那兩條路上。但寫進決策面的動作不只經由 harness：
人（或任何腳本）直接 `experiment_sync sync <artifact>` 時，那道閘根本不會被執行 —— 而 sync 就是
寫進 mindmap 的那一步。把閘放在 `sync_artifact_file` 裡（**手動 sync 與 harness 的 auto-sync 都必經它**），
兩條路就不再可能一個有一個沒有。

### 規則（與 harness 兩扇門刻意的不對稱）

| 情形 | 動作 | 豁免 |
|---|---|---|
| 缺成對 stderr log（跑的產物） | 拒寫，**rc=5** | **沒有**：「沒有 log 所以驗不了」不該是一種狀態 |
| 量具 `UNBOUND` | 拒寫，**rc=4** | `sync --waive-instrument "理由"`：豁免只讓它進得來，**不讓它變乾淨** |
| 不是在跑的產物 | 不適用 | — |

用例外（`SyncRefused(code, gate)`）而不是回空 list：回空 list 會跟「這支產物沒有 charter」
（正常的空結果）混在一起，而兩者的下一步完全不同。

### 排行榜也是決策面（順帶收緊）

`experiment_sync leaderboard` 會把跨語料的榜首寫進 `score-leaderboard`節點的 `res`，而那是別人最容易
直接抄走的一條數字。它的上榜規則從「只有乾淨歸因」（09-28）再收緊成：**乾淨歸因 ∧ 量具不是 UNBOUND
∧ 有成對 log**（`_arm_on_board(a, path)`；被刷掉的理由計數一併回傳，不静默丟掉）。

### 後果（唯讀實測，2026-09-29）

```
掃描 765 個臂，上得了榜 0 個
  缺成對 log 522／沒有 attribution 126／both 56／swap 28／
  量具 UNVERIFIABLE 14／thermal 12／contention 4／量具 UNBOUND 3
```

⇒ **目前語料裡沒有任何一個「又乾淨、又驗得出來」的吞吐讀數**：539 個歸因乾淨的臂全部缺成對 log
（那些正是今天之前 harness 跑出來的、log 留在 `/tmp` 的臂）。節點的 `res` 因此會寫出
「僅乾淨歸因：0/765 個臂」——那是事實，不是退步；而它比以前「榜首 17.669（prod25、非認可口徑）」
清楚得多。這也解釋了為什麼修復必須從**寫入端**做，而不是靠人工補 296 支舊產物。

⚠ 一個保守的邊界：`workdir-name` 只在**同一個目錄**裡找，不進子目錄（像
`Backup/anchor_repro_2026-09-28/` 那種每跑一個 `wdN/` 的目錄）。那些產物的 log 可能就在隔壁的
`wd5/`，但「哪一個 wd 是這一場」證明不了 ⇒ 判不可引用、並在 `--pairs` 裡把它列為候選。


## 12. 補證嘗試：09-27 的 log 確定不在；重跑一場 → VOID（2026-09-29 02:47）

背景：`Backup/mtp_off_clean/res.json`（decode **9.3107**、prefill 306.68）是 §10 新規則下第一個被
點名的可引用基線（`score-leaderboard` 引用著它）。本節記的是「讓它重新可引用」的嘗試結果。

### 一、找 log：確定不在

* repo 内 09-27 21:55–22:10 之間**只有 `Backup/mtp_off_clean/res.json` 這一個檔**被動過（`find -newermt`）。
* `/tmp` 同時段 0 個檔。`/tmp/harness_bench/` 最早的檔是 **09-28 16:55**，而它的命名
  （`llama_bench_prod-new_p2048_n128_d512_r3.stderr.log`）與 09-27 那場**同一個 tag+shape**
  ⇒ 09-27 的 log 被後來的同名場次蓋掉了（矩陣 09-16 註解警告的那個坑）。
* ⇒ 沒有可指出的來源 ⇒ **不可 backfill**，只能重跑。

### 二、重跑（同 build、同 cell、同 charter）

```bash
python3 scripts/check/harness.py bench --arm prod-new \
  --charter scripts/check/charters/exp-mtp-onoff-standard.yaml \
  --json Backup/mtp_off_clean/res_2026-09-29.json
```

新產物**有成對 log**（`res_2026-09-29.stderr.log` 59216 bytes ＋ per-arm 一份）—— §10 的寫入端
在真跑上第一次落地（`log pair -> …`）。量具閘 `N/A`（本臂沒武裝受檢量具）⇒ 過；成對閘 ⇒ 過。
（auto-sync 跳過：`exp-mtp-onoff-standard` 從來沒有 init 過節點。）

| | 09-27（無 log，不可引用） | 09-29 02:47（有 log，VOID） |
|---|---|---|
| engine build | `e5d1c0f14` | `e5d1c0f14`（**同一個**） |
| prefill | 306.68 ± 2.77 | 245.57 ± 30.50 |
| decode | **9.311 ± 4.50** | **5.821 ± 1.51** |
| 起跑 swap → 峰值 | 588 → 872 MiB | 3989 → **10894** MiB |
| thermal worst | NOMINAL | **HEAVY**（159/231 樣本） |
| attribution | `none` | **`both`**（thermal=HEAVY ＋ swap_growth +5360 MiB） |
| Metal 峰值 | （當時未量） | 21644 MiB vs 上限 11453 ⇒ **REFUSE** |

### 三、結論（三個都成立）

1. **9.31 沒有被復原**：它的 log 不存在，而今天重跑的那一場**自己不可引用**（§5.1：thermal HEAVY ＋
   swap 成長）—— 沒有拿一個 VOID 的數去填一個不可引用的數。
2. 同 build、同 cell、同一支臂，只因**盒子状態**（起跑 swap 588 → 3989 MiB，available 5.2 GB）就差
   **37%**（9.31 → 5.82）。這正是歸因閘存在的理由，也說明 9.31 的可信度**完全依賴那份 log**
   （log 要證明的是「當時盒子是安静的」）。
3. Metal 峰值 21644 vs 上限 11453（超 10.2 GB）：池子 8 GiB ＋ 模型 Metal 駐留 ~12.4 GB 在 16 GB 機上
   不可能同時裝下 ⇒ **每一趟 prod-new 都會有一部分池子在 swap 上**。§8 那條矛盾的又一次實證。

### 四、要重建一個可引用的 decode 基線，只有兩條路

* **靜機窗口**：起跑 swap 存量回到 ~1 GB 以下（其他 app 讓開）再跑同一個 cell —— 指令與判準都現成，
  harness 的起跑閘會自己確認（本輪就是它先把 02:46 那兩次 33.6 MiB/s 的壓縮機流量擋下來的）。
* **換一個宣告得住的 cell**：縮池（例：`prod-new:!CGC_EXPERT_CACHE_BYTES=4GiB`）另立口徑量乾淨。
  那**不是** 09-27 的基線，不能拿來替 9.31 背書。

新產物留在 `Backup/mtp_off_clean/res_2026-09-29.{json,stderr.log}`：它記的是「盒子不安静時這支臂
長什麼様」，不是成績。

## 13. 重跑 #2（06:43）：這支臂**最快、最緊**的一場，被一條校準時已退役的欄杆判 VOID（2026-09-29）

指令與 §12 相同（同 arm／cell／charter／build），只換產物路徑：

```bash
python3 scripts/check/harness.py bench --arm prod-new \
  --charter scripts/check/charters/exp-mtp-onoff-standard.yaml \
  --json Backup/mtp_off_clean/res_2026-09-29b.json
```

起跑閘這輪自己放行：thermal 起跑即 NOMINAL、compressor `quiet (compressions 0.00, pageouts 0.00 MiB/s)`
⇒ **沒有用 `CGC_WINDOW_OVERRIDE`**（02:47 那場是等到 02:46:51 的安靜窗口才跑，這輪連等都不用等）。
產物有成對 log（`res_2026-09-29b.stderr.log` 47050 B ＋ per-arm 一份）⇒ §10 的成對閘過；量具閘 `N/A`；
auto-sync 跳過（`exp-mtp-onoff-standard` 沒有節點）⇒ 決策面未被動到（claims 3 QUOTABLE／1 WARN／10 VOID、
mindmap 1/55 都不變）。

### 一、三場並排（同 build `e5d1c0f14`、同 cell、同一支臂、同一列 `n_prompt=0 n_gen=64 d=512`）

| | 09-27 22:03（原基線，無 log） | 09-29 02:47（#1） | 09-29 06:43（#2） |
|---|---|---|---|
| prefill（p2048） | 306.68 ± 2.77 | 245.57 ± 30.50 | 302.00 ± 2.73 |
| decode（n=64、warm 64） | 9.311 ± 4.495 | 5.821 ± 1.505 | **11.583 ± 0.139** |
| 起跑 swap 存量 | 588 | 3989 | 5714 MiB |
| swap 成長 → 峰值 | +252 → 872 | +5360 → 10894 | +3191 → 9098 MiB |
| pageouts（整場） | 439 pg／6.9 MiB | 1723 pg／26.9 MiB | 550 pg／**8.6 MiB** |
| pageins（整場） | 17.07 GiB | 33.84 GiB | 20.54 GiB |
| thermal worst | NOMINAL | **HEAVY** | **NOMINAL（154/154 樣本）** |
| attribution | `none` | `both` | **`swap`** |
| 可引用？ | ✗（缺 log） | ✗ | ✗（swap） |

最快、最緊的一場（CV 1.2%）反而被拒；唯一被標成 `none` 的那場 CV 是 **48%**。第二列 pageouts
只有 8.6 MiB，而同一場的 swap 存量成長 3191 MiB —— 兩個觀測差 ~370 倍，本節只並列，**不裁定**它的
機制（可能是 `vm_stat` 的 pageouts 與 `vm.swapusage` 的 used 在 macOS 上計的不是同一件事；
`compressor_pressure.py` 的說明把它當「流量 vs 存量」兩種量，而產物裡的 memory samples 沒有壓縮機速率）。

### 二、擋下它的不是那條校準過的閘，是一條已經退役的欄杆

同一份產物裡，機器對「這一場的記憶體行為」給出兩個方向相反的判決：

```
memory gate : OK      growth 3191.2 MiB，預測 3635.2 ⇒ 殘差 -251.6 MiB
              absolute growth: off（… 要舊的絕對桿子請設 CGC_SWAP_BUDGET_MB=<N>）
              residual: warning-only（… 要它拒就設 CGC_GROWTH_RESIDUAL_MB）
state_gate  : ok      available 8047 MiB ≥ 4000；free 633 MiB ⇒ 警告（自由頁是快取統計，不是可用量）
attribution : swap    swap_growth=3191.2 MiB > SWAP_GROWTH_MB=512   ← 未校準的絕對桿子
is_clean    : False   ⇒ 不可引用（這一行才是成績面看的）
```

`memory_pressure.py` 的模組說明自己寫著 **「The ABSOLUTE `SWAP_GROWTH_MB` bar is now OPT-IN」**
——因為它與起跑 free 的 rho 是 **-0.870**（主要在量「盒子當時多鬆」，不是在量臂），而同檔的
`attribution()` 仍用它，且 `is_clean()`（`merge_run`／`mindmap_void_check`／排行榜共用的唯一判準）
只認 `attribution.verdict`。於是同一支量測有**兩個判官、兩份判準**：閘門說 OK，成績面說 VOID。

而且對 prod-new 這支臂，這不是運氣問題：池子 8 GiB ＋ 模型 Metal 駐留 ~12.4 GB > 上限 11453 MiB（§8）
⇒ 起跑只要不是接近空機（絕對桿子等價於要求起跑 free ≳ 8.5 GB），成長就必然越過 512 MiB 那條線。
**在這台機器今天的狀態下，「乾淨的 prod-new decode」用絕對桿子是不可達的，不是不巧。**

### 三、規模（唯讀掃描 `experiment_sync.scan_artifact_arms()`）

| 現況 | 支數 |
|---|---|
| 無 attribution（舊產物） | 364 |
| `both` | 92 |
| **`swap` 但殘差 ≤250（校準下應為乾淨）** | **55** |
| `swap` 且殘差 >250 | 12 |
| `swap` 但 thermal 非 NOMINAL | 21 |
| `none` | 48 |
| `thermal` | 15 |
| `contention` | 5 |

全 repo 有 **55 個讀數**正坐在這個矛盾上（本輪的 11.583 是其中之一）。

### 四、兩邊都說得通 ⇒ 這是政策決定，不由我裁

* **維持 VOID 的理由**：成長 3191 MiB 是真的，池子確實有一部分在 swap 上（§8 的結構事實）；
  「殘差 -251」只代表「以這台當時有多緊來說不算異常」，不代表「沒有成本」。
* **改用校準判準的理由**：同一支臂在成長 3191 時量到 11.58 ± 0.14，在成長 252 時量到 9.31 ± 4.50；
  pageouts 只有 8.6 MiB；而成長有 87% 由起跑狀態決定。用一根與臂無關的欄杆擋掉最好的一場，是選錯變數。

三個最小選項（等裁決，我先不動任何判準或程式）：
(a) 不動判準，只把 `swap` 拆成 `swap-structural`（殘差在預算內）與 `swap-excess`（超出）；
(b) `is_clean` 改用校準後的殘差判準（一次翻動上述 55 支的引用資格）；
(c) 維持現狀，把「可引用」繼續交給絕對桿子。

新產物：`Backup/mtp_off_clean/res_2026-09-29b.{json,stderr.log}`（＋`res_2026-09-29b.logs/`）。
本輪**沒有改任何程式碼、沒有 rebuild、沒有 commit**。

## 14. 「可達的乾淨口徑」到底長什麼樣：把兩道條款反推成起跑條件（2026-09-29）

§13 証明了「同一支臂有兩個判官」。這一節不談要不要改判準，只回答一個更實際的問題：
**在現行判準下，什麼盒況下 prod-new 的 decode 真的可以變乾淨？口徑能不能換得到？**

### 一、先看能不能「換口徑」——池子這條路已經被關掉，且語料從沒試過它

* `docs/CGC_POOL_WIRED_BUDGET_2026-09-28.md` 與 `scripts/run_server.sh` 都記著同一條：
  **8 GiB 12.06 → 6 GiB 10.48 → 4 GiB 7.67 t/s**（8→4 慢 **36%**），因為熱集（143/256 專家吃 98.8% 路由）
  需要 8 GiB 才裝得下。⇒ **縮池不是「另一條口徑」，是降級**，不能拿它當基線（它跟 9.31 不可比）。
* 唯讀掃了語料（`Backup/**` ＋ `/tmp/**`，`experiment_sync.scan_artifact_arms()`）：
  **162 支有記憶體樣本的 prod-new 臂，池子全部是 `8192 MiB`** ⇒ 池子大小在 harness 產物裡**沒被變動過**，
  上面那條曲線是 server 上量的（override 還要帶 `SERVER_` 前綴，否則 `run_server.sh` 靜默忽略）。

### 二、把判準反推成起跑條件（可先驗、再花 GPU）

`attribution()` 的 `swap` 需要兩道條款都避開，反推得到：

| 條款 | 反推的起跑條件 | 今天 06:50–06:53 |
|---|---|---|
| `swap_growth <= 512 MiB` | 起跑 `pages_free >= 7725 MiB`（校準式 `max(226, 3914 - 0.4404*free) = 512`） | free ~3600 ⇒ **✗**（預測成長 2329） |
| `max_swap <= 6144 MiB` **或** 成長 ≤ 0 | 起跑存量 ≤ 6144 MiB（否則整場必須**淨減少**） | stock 7131 且 3 分鐘完全不動 ⇒ **✗** |

⇒ **乾淨區間＝「起跑接近空機」**，不是「手臂調得對」。語料側完全一致：

* 唯一一支 8 GiB 池＋乾淨歸因的臂是 09-27 那場：起跑 **free 9601 MiB、存量 588 MiB**、成長 +252 ⇒ `none`。
* 另外四支 `none`（`Backup/phase_decomp/cbnmain_p1build/clean{5,6,7,8}_*.json`）成長恰好 `0.0`、存量 4595–4619 MiB，
  但它們**沒有 decode 列**（tg 為 null）⇒ 證明「成長 0 是可能的」，卻還不是一條基線。
* 語料中 **存量 > 6144 MiB 的臂，沒有任何一支被標過 `none`**。

### 三、結論：這條路存在，但需要人動盒子

「找到一條可達的乾淨口徑」的答案是：**口徑不變（prod-new 預設 cell、8 GiB 池），換的是起跑盒況**；
而那個盒況無法由手臂內部產生——它是**其他行程的匿名頁在不在**的問題（模型 12.7 GiB 的 Metal 駐留不可換頁，
其餘的驅逐壓力就是別人的記憶體）。今天 free 3.6 GB / 存量 7131 MiB 卡住 ⇒ **今天剩下的時間裡，
這個口徑在現行判準下達不到**。能改的只有起跑狀態：釋放常駐行程 或 重開機
（09-27 那場的 free 9601 / 存量 588 就是那個狀態的樣子）。

⇒ 基線一產生，它就是與 9.31 可比的那一列（同 arm／同 cell／同 build），並且改成**有成對 log** 的版本。

## 15. 那一列出來了：06:56 的 11.703 t/s —— 全語料唯一三扇門全過的臂（2026-09-29）

§14 寫完之後又跑了一場（同 arm／cell／charter／build，輸出路徑 `…29c.json`）。它不是靠「盒子變鬆」過關的
——起跑 free 只有 3165 MiB、存量 7083 MiB（比 §14 那張表預告的還差）——而是**走 clause2 唯一的出口：
整場 swap 淨減少**。中場峰值曾到 8380 MiB，收尾卻回到 7036 MiB（**成長 −46.7 MiB**）。

### 一、機器判定（全部讀自產物與現成的檢查器）

```
is_clean        : (True, 'attribution.verdict=none（thermal NOMINAL）')
pair_status     : paired | sibling | Backup/mtp_off_clean/res_2026-09-29c.stderr.log（46330 B）
sync_gate（第三門）: ok, code 0
排行榜准入        : (True, '')
掃全語料          : 639 個臂裡合格 **1** 個（就是這一支；之前是 0/765）
```

⇒ 本 repo 第一支「乾淨歸因 ∧ 量具非 UNBOUND ∧ 有成對 log」都具備的 prod-new decode 讀數。
決策面**未寫入**（`exp-mtp-onoff-standard` 沒有節點；`docs/mindmap/mindmap.json` md5 前後同値 `b7ce36aa…`）。

### 二、四場同臂並排（同 build `e5d1c0f14`、同 cell、`n_prompt=0 n_gen=64 d=512`）

| 場次 | 起跑 free | 成長 | 趟內峰值成長 | 校準預測 | **校準殘差** | attribution | decode |
|---|---|---|---|---|---|---|---|
| 09-27 22:03（原基線，無 log） | 9601 | +252 | 284 | 226 | **+58** | `none` | 9.311 ± 4.495 |
| 09-29 02:47 | 367 | +5360 | 6905 | 3752 | **+3152** | `both` | 5.821 ± 1.505 |
| 09-29 06:43 | 633 | +3191 | 3384 | 3635 | **−252** | `swap` | 11.583 ± 0.139 |
| **09-29 06:56** | 3165 | **−47** | 1297 | 2520 | **−1223** | **`none`** | **11.703 ± 0.348** |

把四場放在**同一根校準軸**（殘差）上看，順序與標籤不一致但與速度一致：
**06:56（殘差 −1223，11.70）> 06:43（−252，11.58）> 09-27（+58，9.31）> 02:47（+3152，5.82）**。
也就是說：今天這支「舊基線」在兩根軸上都是四場裡第三名。

### 三、可引用的讀數（附它自己的口徑）

`prod-new`、預設 cell、MTP off、`-p 2048 -n 128 -d 512 -r 3`、warm-skip 64、`--fixed-fill-seed 1`、engine `e5d1c0f14`：

| 指標 | 值 |
|---|---|
| prefill | **302.28 ± 7.55 t/s** |
| decode（`n_gen=64`，3 reps） | **11.703 ± 0.348 t/s** |
| 熱門快取 | hit **96.3%**（miss 4784：compulsory 3991／capacity 793）、resident 6197.04 MiB |
| thermal | **NOMINAL 150/150** |
| pageins／pageouts（整場） | 19.83 GiB ／ **5.1 MiB** |

⇒ 對 decode 25 目標是 **46.8%**（從 9.31 的 37% 上來），比 09-27 同一支臂快 **+25.7%**。

### 四、這個 `none` 有多硬？三個必須連著讀的限制

1. **它靠端點，不靠低壓。** 通過 clause2 的理由是「收尾 < 起跑」；同一趟的**峰值成長是 +1297 MiB**
   （遠超 512）。同一份資料，若收尾多 60 MiB，標籤就回到 `swap`。⇒ 這個 `none` 是**脆的**，
   而它的可信度仰賴兩根獨立的軸同時支持：校準殘差 −1223（四場最好）與 thermal 150/150。
2. **金屬工作集仍然超額**：峰值 21550 vs 上限 11453 MiB（§8）⇒ 池子仍然有一部分在 swap 上；
   這不是「乾淨的盒子」，是「乾淨的歸因標籤」。
3. **`none` 不是可重現性保證**：同一支臂、同一 cell，兩個乾淨標籤之間差 **+25.7%**（9.31 → 11.70，
   而 9.31 自己的 rep 散度是 ±48%）。一條可以拿去用的基線應該是**重複數次的中位數與散度**，
   而不是一場的單一數字——這一場是那一組的第一個成員。

## 16. 用 11.703 當基準拆 decode 的每步成本：缺口不在池讀取，在 GPU 每個 dispatch 太小（2026-09-29）

問題：11.703 t/s ⇒ **85.45 ms/token**；decode 25 t/s 要 **40 ms/token**。差的 **45.45 ms 在哪一格？**

方法：同 arm／cell／charter／build，只加開量具（都是**探針場，t/s 不可引用**，但成對 log 照落）：

| 探針 | 開啟 | 產物 | 探針自己的 tg |
|---|---|---|---|
| A：逐步 CPU/GPU 切分 | `CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1` | `Backup/stepbudget_2026-09-29/decprof.{json,stderr.log}` | 11.89（VOID：swap） |
| B：GPU 節點種類歸屬 | 同 A ＋ `CGC_GPU_NODES=1` | `Backup/stepbudget_2026-09-29/gpunode.{json,stderr.log}` | 10.45（量具本身 ~14% 成本） |

### 一、每步的帳（探針 A，`ntok=1` 的解碼步 48 個取樣，mean）

| 成分 | ms/step | 佔 85.45 的 | 定義（源碼） |
|---|---|---|---|
| `wait` | **66.12** | 77% | 該層 segment 的 GPU 等待窗（CPU 側輪詢完成） |
| `cb` | **4.36** | 5% | 該層 hook：槽位管理 **＋ 阻塞式填充** |
| `submit` | **3.42** | 4% | 下一段的前送提交 |
| DECPROF `total` | 73.89 | 86% | 上三者之和（每步實測，非推導） |
| 圖外（end-to-end − total） | **~11.6** | 13% | 取樣、圖組裝、每 token 的 API 開銷（**目前沒有儀器**） |
| **end-to-end** | **85.45** | 100% | 11.703 t/s 的實測 |
| （GPU 側）`busy_sum` | 101.01 | — | Σ(end−start)，緩衝重疊會重複計 |
| （GPU 側）`union_sum` | **69.42** | 81% | 該步的 Metal 執行跨度（同一把 GPU 鐘） |
| （GPU 側）`gap_sum` | **14.04** | 16% | 段與段之間的 GPU 空轉 |

兩把鐘的差額要說明白：CPU 側的 `wait` 是 **66.12**，而 Metal 鐘在**同一批步**上量到的跨度是 **69.42**，
其中 `busy_sum` 101.01（重疊重複計）、`union` 69.42、`gap` 14.04 ⇒ **GPU 淨忙 ≈ 55.4 ms**（`union − gap`）。
兩邊差 ~3.3 ms，是不同時鐘與不同取樣實例的差額，不是第三個成分；下面所有情景都以
**`wait` = 66.1＝淨忙 55.4 ＋ 空轉 14.0（含 3.3 ms 對鐘誤差）** 這條拆法來算。

三條直接可讀的結論：

1. **`wait` 是真的在算，不是啟動/完成延遲。** CPU 側的 66.12 ms 對上 Metal 鐘的 69.42 ms 跨度
   ⇒ 2026-09-15 留下的那個「(A) GPU 真的忙 vs (B) 啟動＋完成回報延遲」問題，**在現在的 build 上量到 (A)**。
   ⇒ 槓桿在「減少/合併 dispatch」，不是「拿掉 GPU→CPU→GPU 往返」。
2. **池讀取不是瓶頸。** 臨界路徑上的填充（`cb`）只有 **4.36 ms/step（5%）**，中位數 3.17、最大 22.9（miss 風暴）。
   對照同一場的**第一段 512-token 冷塊**：`cb` 佔 **57%**（2.38 s / 4.22 s）、`gap` 2.37 s ⇒ 池的成本是
   **冷啟動現象**，穩態 decode 已被 worker 前饋藏掉。（舊算術「20+ 要靠藏掉一半填充」是**單次提交臂**的口徑，
   而那一支的輸出是 garbage ⇒ 不同口徑，不得混用。）
3. **45.45 ms 的缺口在「GPU 忙」與「非計算」兩邊同時存在**，沒有任何一格單獨夠大：
   GPU 淨忙（`union − gap`）≈ **55.4 ms**、GPU 空轉 **14.0**、圖外 **~11**、填充 4.4、提交 3.4。

### 二、那 55.4 ms 的 GPU 淨忙是什麼？（探針 B，`CGC-GPU_NODES`）

儀器自檢先過：`seg_busy` 與 `layer gpu_sum` 相等（**delta 0.000%**，7 個區塊）。但 per-kind 表是
**上下界，不是量測**：44 個 kind 裡**只有 2 個的 `lb` > 0**，而 `cntw` 只加總到 `seg_busy` 的 **65%**
（未歸屬 35%）。能說的只有這些：

| kind | cntw（佔 seg_busy 117.4 ms） | lb | ub |
|---|---|---|---|
| `cache`（MoE 專家權重搬運） | 15.7 ms（13.4%） | **6.96（唯一有下界）** | 67.4（57%） |
| `ffn_moe_*` 家族 | ~10.6 + 3.7 + 2.5 + 1.3 ≈ 18 ms | 0 | 39.9（34%） |
| `conv`／`linear_attn`／`z-`／`gdn_state`（GDN 家族） | ~9 ms | 0 | 33（28%） |
| `norm`／`node`／`attn_*`／`(other)` | ~15 ms | 0 | 58（48%） |
| **未歸屬** | **41.5 ms（35%）** | — | — |

⇒ **沒有任何一族單獨佔一半**；「MoE 權重搬運」的**可證明下界只有 6.96 ms**（~6%）。這與另一條線的
`c-bandwidth` 一致：dense GEMV 實跑 56–108 GB/s，就算全打到 100% 峰值也只省 1.92 ms（2.42% step）。

### 三、把兩邊接起來：每 token 是 ~360 個「太小的 dispatch」

* 每 token 的權重位元組 ≈ **1.171 GB**（`docs/DECODE25_CEILING_2026-09-20.md` §7 的口徑）。
* 每 token 的 Metal 緩衝數 = 40 層 ×（`CGC_N_CB`=8 ＋ 1）= **~360 個**。
* ⇒ 每個緩衝 **~3.25 MB**、GPU 忙 **0.19–0.33 ms**（用 union 或 busy_sum 當分母）
  ⇒ 每個 dispatch 的**有效頻寬 ~10–17 GB/s**，是 M4 規格 ~120 GB/s 的 **10–15%**。
* ⇒ 每個 dispatch 都比「頻寬極限」慢 **~7–10×**：這不是 kernel 寫得慢（同一族的 kernel 實測 56–108 GB/s），
  是**每個緩衝帶的位元組太少、數量太多**（3.25 MB 打不滿頻寬）＋每層一個 GPU→CPU→GPU 邊界。

### 四、要到 25 t/s 還缺什麼（算術，不是願望）

| 情境 | GPU 淨忙 | GPU 空轉 | 圖外 | 填充＋提交 | step | t/s |
|---|---|---|---|---|---|---|
| 今天（11.703） | 55.4 | 14.0 | 11.6 | 7.8 | **88.8**（實測 85.45） | 11.7 |
| 只有 kernel 打到 108 GB/s（1.171 GB / 108） | 10.8 | 14.0 | 11.6 | 7.8 | 44.2 | 22.6 |
| 只有把 dispatch 併到 ~10× 大（60 GB/s） | 19.5 | 7.0 | 11.6 | 7.8 | 45.9 | 21.8 |
| **兩者都做，且空轉/圖外/填充各減半** | 19.5 | 7.0 | 5.8 | 3.9 | **36.2** | **27.6** |

（今天那一列加總 88.8 而實測 85.45：差 3.3 ms 就是上面那條對鐘誤差，不是漏掉的一格。）

⇒ 兩個決定性的結論：

1. **只靠計算側到不了 25。** 就算權重搬運跑在實測的上限（108 GB/s），step 也只到 44.2 ms = 22.6 t/s；
   因為「非計算」的 **~33 ms（空轉 14 ＋ 圖外 11.6 ＋ 填充/提交 7.8）** 不動。25 t/s 需要那 33 ms 也一起縮。
2. **缺口的主力是「每個 dispatch 太小」這件事本身**，而它同時餉到兩格：少而大的 dispatch 既提升有效頻寬，
   又直接縮小段間 `gap`。所以這**不是**一條「把填充藏得更好」的路，也不是「換模型/加記憶體」的路。

### 五、這份帳的限制

* 三個場次（.29c 基準、探針 A、探針 B）是三次啟動，**跨場差**不可忽略（同臂同日已見 5.82–11.70）；
  本節只用「每步內部比例」，不跨場相減速度。
* 探針 A 的 11.89 與探針 B 的 10.45 都**不可引用**（前者 attribution=swap，後者量具本身 ~14% 成本）。
* per-kind 表是**上下界**；只有 `cache` 有非零下界。若要用它結案，需要一個「不重疊的歸屬」而非再跑一次。
* 圖外那 ~11 ms（12–13%）至今**沒有儀器**，它現在是「end-to-end − DECPROF total」的差值。
* `gpu_sum` 重複計重疊緩衝，只有 `union`／`gap` 可以相加；本節所有 GPU 淨忙都是 `union − gap`。

## 17. 「把一層的 8 個專家併成一次 dispatch」—— 已經是現況，而再往前併會變慢（2026-09-29）

圖上那條路走不通的原因要在兩層看：**dispatch 數**與**每個 dispatch 的效率**。兩者都量了。

### 一、先把「已併」確認到底（讀碼 ＋ 普查）

* `MUL_MAT_ID` 在 Metal 側是**一次** `dispatch_threadgroups`，第三個 grid 維度就是
  `ne123 = ne20*ne21`（＝專家數 × token 數）（`ggml-metal-ops.cpp:4820-4860`）⇒ **8 個專家的 GEMV
  本來就在一個 dispatch 裡**（上游 ggml 的行為，不是本 repo 的補丁）。
* 新的 decode 普查（`CGC_DISPATCH_CENSUS=1`，前 48 張圖；這次特意用 **delivery cell（p=0）**，
  前 48 張圖才全是 decode）：MoE 圖每層 **`MUL_MAT_ID`×3（gate／up／down）＋ `GLU`×2**，
  每層 segment 共 **31 dispatches**。⇒ 每個投影本就是「一個 dispatch 吃全部 8 個專家」。
* 真正還能併的是 **gate＋up＋SwiGLU**（`CGC_MMV_FUSE=1`，已實作、預設關、以前甚至沒進 launcher
  白名單 ⇒ 那個旗標一直是惰性的）。它把每層的 MoE 家族從 5 個 dispatch 降到 2 個（計數口徑），
  每層 segment 31→**28** dispatches（每 token −80 至 −120 個）。

### 二、A/B（delivery cell，兩臂同一批儀器；兩臂都 `attribution=none`）

| | baseline（`MUL_MAT_ID`×3） | `CGC_MMV_FUSE=1`（×1） | Δ |
|---|---|---|---|
| 每層 segment dispatches | 30.9 | **28.0** | **−2.9（−9%）** |
| 每 token dispatches | ~1240 | ~1120 | −120 |
| 每 MoE dispatch 背的位元組 | gate 2.56／up 2.56／down 3.44 MiB | **gate_up_glu 5.12／down 3.44 MiB** | **×2** |
| 穩定解碼步（DECPROF，n=41） | **90.41 ms** | **93.56 ms** | **+3.15 ms（慢 3.5%）** |
| └ `wait`／`union`／`gap` | 77.37／82.18／19.54 | 79.82／85.39／20.13 | 全變差 |
| MoE 家族 GPU 時間（cntw） | 3.48（`MUL_MAT_ID`）＋2.32（`GLU`） | 3.41＋2.27 | **−0.12（噪訊內）** |
| 探針 t/s（delivery cell） | 9.91 | 9.38 | −5.3% |

**每 MoE dispatch 的位元組數與有效頻寬（算術，分子用 GGUF 權威尺寸）**：

| | baseline | fused |
|---|---|---|
| 每 token MoE 權重 = 8 專家 ×（gate 328 ＋ up 328 ＋ down 440 KiB）× 40 層 | **334.7 MiB** | 334.7 MiB |
| 家族 GPU 時間 | 5.80 ms/step | 5.68 ms/step |
| ⇒ **有效頻寬** | **~59 MiB/ms ≈ 62 GB/s** | **~60 MiB/ms ≈ 63 GB/s** |
| 單一 dispatch 的量級 | 2.56–3.44 MiB／約 43–58 µs | 5.12 MiB／約 87 µs |

（交叉核對：池子 resident 6197.04 MiB ÷（143 槽 × 40 層）= **1.083 MiB/槽** ≈ GGUF 算出的 1.070 MiB ✓）

### 三、三個結論

1. **「8 個專家一次 dispatch」沒有東西可併**：它已經是 3 個 MUL_MAT_ID（gate／up／down），
   每個都是一個 dispatch 吃 8 個專家。
2. **再多併一次（gate＋up＋GLU）沒有收益，而且更慢**：dispatch 少了 9%，但每步 **+3.5%**，
   家族 GPU 時間不動。這與 repo 裡既有的兩條彼此矛盾的紀錄都不同：
   `MMAP_CACHE_KERNEL_25` 說 p50 −0.59 ms（≈沒有）——**與本量測同向**；
   `autotune_mmv.py` 記錄的 15.94 vs 22.07 t/s（−28%）**與本量測同向但幅度大得多**（那是舊配置／別的 cell）。
   ⇒ 三方一致的最小公約數是「**併了不會更快**」。
3. **這條路即使做到完美，也接不到 25 t/s**：MoE 家族只占一步的 **5.8／90 ms（6.4%）**，
   就算整數消掉也只 +6%。更關鍵的是：**有效頻寬本來就在 62 GB/s**，這已經落在 dense GEMV 家族
   實測的 56–108 GB/s 區間內 ⇒ 「dispatch 太小 → 頻寬上不去」這個前提，在 MoE 這一家族上**不成立**。

⇒ 45 ms 的缺口必須從別處找：`MUL_MAT`（dense，15.3 ms/step）、`MUL`／`ADD`／`UNARY`／`RMS_NORM`／
`CPY`／`GET_ROWS`（合計 ~42 ms）、`gap` 19.5 ms（22%）、以及圖外 ~11–13%。

### 四、正確性：已用 M1/M2/M3 結案（`m123_oracle_gate.py`）

用 repo 既有的 per-commit 品質閘跑（profile `prefill250`、參考 oracle
`Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v7_20260926.jsonl`、同一顆 build
`libllama.0.dylib=c0a852ffb99bb008`／tree `2823fdc76`）：

| 臂 | M1（逐位元） | M2（argmax） | M3（top-N） | 閘門 |
|---|---|---|---|---|
| **對照**：生產配置（不加任何 env） | **9/9 PASS** | 9/9 PASS | 9/9 PASS | PASS |
| `CGC_MMV_FUSE=1` | **4/9 FAIL** | 9/9 | 4/9 | INVALID COMPARISON（env 有未宣告的數值決定項） |

兩件事必須一起讀：

1. **對照是關鍵的一手。** 沒有它，M1 4/9 可能只是「參考 oracle 比今天 build 舊」。對照在同一顆
   build／同一棵樹上 9/9 全同 ⇒ 參考在這顆 build 上**逐位元有效** ⇒ 那 4/9 只能歸因到這個旗標。
2. **分歧不是 ULP 級。** 5 行不同的列，logits 總和差到 ~1e5、mean 位移 ~0.7（不是累加順序那種
   ~1e-5）⇒ 那個融合 kernel 算出的東西**與生產路徑不是同一個數值**。M2 9/9 只是說「這 9 列上還沒翻
   過決策」，工具的警告寫得很直白：不要單獨引用 M2。

工具的閘門拒給結論（policy：未宣告的數值決定性配置變更），所以引用時必須這樣寫：**閘門拒發判決；
資訊性 M1＝4/9，且由同 build 的對照證成可比**。

產物：`Backup/m123_oracle_gate/summary_mmvfuse{,_ctl}.json`。

### 四之二、封存

⇒ **這個旋鈕結案：不用。** 它既沒有更快（+3.5% 慢），也不是 bit-identical（M1 4/9，且分歧量級很大）。
結論寫回 `scripts/run_server.sh` 的白名單註釋（這就是那個旗標以後被看到的地方），並註明：要看
的話唯一認可的路是**重新造一份融合配置的參考**（`--write-ref`），不是引用歷史上那句「宣稱 bit-identical」。

順帶一個與本節主題同型的收穫：這個旗標之所以能「宣稱」任何事，是因為它好幾天都**沒在轉發清單裡**
（惰性的），所以根本沒人量得到它——直到一個 dispatch 普查把它變成可量的問題。

### 五、本節順手改的兩處 launcher 轉發（都不是數值路徑）

* `CGC_DISPATCH_CENSUS`：儀器 09-20 就寫好了但**沒接進白名單** ⇒ 一直是惰性的（跟本節主題一樣的坑）。
* `CGC_MMV_FUSE`：`run_server.sh` 自己在檔頭記著這個坑（[perf] banner 為一個惰性旋鈕印了好幾天的
  `glu_fused_down=1`）⇒ 補上轉發，讓它可以被量（也只有被量了才敢說它沒用）。

產物：`Backup/stepbudget_2026-09-29/{census,delivery_base,delivery_fuse}.{json,stderr.log}`（全部成對保存）。

## 18. 元素級六族的逐一開價：家族價有了（25–33% step），單點價沒有（2026-09-29）

追問 §17 尾巴那句「`MUL`／`ADD`／`UNARY`／`RMS_NORM`／`CPY`／`GET_ROWS` 合計 ~42 ms」——
**把六族逐一開價，找出下一個 ≥3 ms、值得動的目標**。

### 一、先修一個引用陷阱：這張表的 `step=1` 是 **prefill**

`CGC-GPUOPS` 在 `dp_step % 8 == 0 || dp_step == 1 || dp_ntok > 1` 時列印，而 `dp_step` 是
`graph_computes` 的計數 ⇒ **第一張圖是 2048-token 的 prefill**。本輪的七個區塊是：

| 區塊 | `total` | 是什麼 |
|---|---:|---|
| step=1 | **6574.98 ms** | prefill（2048 token，≈300 t/s） |
| step=32 / 96 / 160 / 224 / 288 / 352 | 83.58 / 111.50 / 68.26 / 82.47 / 72.27 / 74.91 ms | **decode（本節只用這 6 張）** |

⇒ 任何「`CGC-GPUOPS` 說 MUL_MAT 佔 23%」的句子，如果沒先看 step 那一欄，**報的是 prefill**。
（§17 引用同一支儀器時用的是 delivery cell 的 p=0 臂，那支前 48 張圖才全是 decode；本節三個讀數
都在下面各自標了 cell。）

### 二、三個讀數（同 arm／cell／charter／build，只改量具）

| 讀數 | 臂 env（除 `CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1;CGC_GPU_NODES=1;CGC_GPU_OPS=1` 外） | cell | 產物（成對） | 探針 tg |
|---|---|---|---|---|
| **A** 生產編碼 | — | default | `Backup/stepbudget_2026-09-29/ops_default.{json,stderr.log}` | 10.19（VOID：swap） |
| **B** 生產編碼 | —（§17 同一批） | **delivery** | `.../delivery_base.{json,stderr.log}` | 9.91（VOID：swap） |
| **C** 逐節點編碼 | `CGC_CB_N_MAIN=1;CGC_SERVER_N_CB=16;!CGC_N_CB=16;CGC_DISPATCH_CENSUS=1` | default | `.../ops_pernode.{json,stderr.log}` | 10.80（VOID：swap） |

六族的**工作加權份額**（`wcntw`，ms／每步，6 張 decode 圖的中位）：

| op | `nd` | A default（coarse） | B delivery（coarse） | C default（per-node，`n_cb=16`） | 穩定度 C/A |
|---|---:|---:|---:|---:|---:|
| **ADD** | 421 | 10.42 | 11.89 | **2.83** | **0.27×** |
| **MUL** | 278 | 7.30 | 8.37 | 10.36 | 1.42× |
| **UNARY** | 169 | 5.72 | 6.58 | 4.63 | 0.81× |
| **RMS_NORM** | 130 | 5.19 | 5.96 | 3.53 | 0.68× |
| **CPY** | 120 | 5.06 | 5.78 | 4.59 | 0.91× |
| **GET_ROWS** | 159 | 4.22 | 4.81 | 4.16 | **0.99×** |
| **六族合計** | 1277 | **37.92** | **43.40** | **30.10** | 0.79× |
| （對照）`MUL_MAT` | 426 | 12.98 | — | 16.53 | 1.27× |
| （對照）`MUL_MAT_ID` | 117 | 2.98 | — | 5.21 | **1.75×** |
| **該圖 `total` 中位** | | **78.69 ms** | **95.20 ms** | **108.62 ms** | 1.38× |
| **六族佔 `total`** | | **48.2%** | **45.6%** | **27.7%** | — |

讀法（三條都重要）：

1. **`nd` 在兩個 cell 完全相同**（421/278/169/130/120/159）⇒ 圖的拓樸不隨 cell 變，變的只有時間。
2. **六族的份額跨 cell 穩定**（48.2% vs 45.6%，兩次啟動、兩個 cell），**跨編碼不穩定**（27.7%）。
   兩個 coarse 讀數差 2.6 個百分點 ⇒ 「六族是那張表上最大的一塊」可以說；「ADD 比 MUL 貴 1.4 倍」
   **不可以說**（同一個 ADD 在 C 讀數裡是所有族裡最小的，0.27×）。
3. §17 尾巴的「~42 ms」= 讀數 B 的 **43.40**（同一欄、同一支臂）⇒ 兩處一致，但**它是一個份額**，
   不是「MUL 花了 8.37 ms 做乘法」。下一節是為什麼。

### 三、為什麼「逐一開價」到份額就止步：這一欄排的是 node 數

本節獨立看到 repo 在 G4 已記錄過的那件事（`docs/G4_WORK_ORDER_2026-09-20.md` §2，LSQ R²=0.13、
出現負係數 ⇒ 逐 kind 邊際成本不可識別）的另一面：**所有 work op 的 µs/node 都擠在分割平均附近**。

| op | net ms（=wcntw×0.704） | µs/node | op | µs/node |
|---|---:|---:|---|---:|
| `MUL_MAT` | 9.13 | 30.5 | `DIV`／`SUM_ROWS`／`CLAMP` | 25.5 |
| `ADD` | 7.33 | 24.8 | `GLU` | 25.4 |
| `MUL` | 5.14 | 26.3 | `MUL_MAT_ID` | 25.5 |
| `UNARY` | 4.03 | 33.8 | `CONCAT`／`SSM_CONV` | 27.0 |
| `RMS_NORM` | 3.65 | 39.9 | `ROPE` | 20.8 |
| `CPY` | 3.56 | 42.2 | `SOFT_MAX` | 11.1 |
| `GET_ROWS` | 2.97 | 26.6 | （分割平均 = 78.69/2365 work node） | **33.3** |

一個 kernel 吃 8 個元素（`DIV`）與一個 kernel 掃 2048×8（`MUL_MAT`）拿到**同一個單價**
⇒ 這一欄量的是「這個 op 的 buffer 裡有幾個 work node」，不是它的成本。**能讀的是偏離平均的方向**：
`CPY`／`RMS_NORM`／`UNARY`／`GDN`／`FLASH_ATTN` 高於平均（它們的 buffer 時間不只是自己的 node 數），
`ADD`／`MUL`／`MUL_MAT` 低於平均（**與「ADD 鏈已被上游融合」同向**）。

### 四、唯一精確的欄位 `uni`：六族**一次都沒拿到**

`uni` = 只在「整條緩衝的節點全是同一種 op」時才算得出的 **EXACT µs/node**。三個讀數的結果：

| 讀數 | 拿到 `uni` 的 op | 值（µs/node） | 讀法 |
|---|---|---|---|
| A／B（coarse，buffer ≥64 node） | `VIEW` 970.90、`RESHAPE` 5317.75 | — | **NOOP 反證**：一條只裝 VIEW 的緩衝不含 GPU 命令，它的時長不可能是自己的。源碼註記 09-18 記的是 592 µs/node，**這次更極端 8.9×** |
| C（per-node，buffer ≈1–6 node） | `RMS_NORM` **6.08**、`ARGSORT` 27.35、`VIEW` 239.13 | — | 仍只有 3 種 op 命中；**六族裡只有 `RMS_NORM` 拿到一個精確價** |

**`RMS_NORM` 的精確價推翻了它自己的份額價**：130 node × 6.08 µs = **0.79 ms/step**，
是份額價（net 3.65 ms）的 **1/4.6** ⇒ 它那一格主要是**別人的佇列時間**（縱使 C 讀數的 `total`
本身被放大 38%，0.79 ms 仍是它的**上界**）。`MUL`／`ADD`／`UNARY`／`CPY`／`GET_ROWS` 在
兩種顆粒度下**都沒有**進入 op-pure 緩衝 ⇒ **這五族沒有精確價，只有份額**，而份額已被 §三證明
不可識別。這是本節最主要的負面結果，也是「逐一開價」這個要求在本儀器上的**結構性上限**。

### 五、唯一可用的「單價」：一個 dispatch 值多少（09-20 的配對，`G4_FUSION_ANSWER`）

| 臂 | t/s | dispatch/步 | 哨兵 |
|---|---:|---:|---|
| fusion ON（生產） | 10.434 | 1004 | HEALTHY |
| fusion OFF（`GGML_METAL_FUSION_DISABLE=1`） | 9.620 | 1119 | HEALTHY |

⇒ Δ=115 dispatch／步 ⇒ **0.0736%/dispatch** ⇒ 在 85.45 ms 的步上 **≈63 µs/dispatch**
⇒ **要動 ≥3 ms（3.5%），得在一個地方移掉 ≈48 個 dispatch**。

掛三個警語（否則這條單價會被過度引用）：① 原文件已寫明它是**上界**，且 n=1/臂；
② G4 自己記著「elementwise 全體 376 個 × 單價 = 27.7% 是**算術外推不是工程承諾**」；
③ **本線自己有一個反例**：§17 的 `CGC_MMV_FUSE` A/B 每層 segment 少 2.9 個 dispatch，
結果**慢 3.5%** ⇒ 「少 dispatch ⇒ 快」不是通則。

### 六、用這條單價逐一檢定：六族有沒有單點 ≥3 ms？

用今天同一批 log 的逐節點普查（`CGC-DISPATCH`：`dispatches/nodes` 的比值就是融合買到什麼）
與 09-20 的逐張量普查（`G4_ELEMENTWISE_TARGETS`）交叉：

| 候選（單點） | 可移除 dispatch/步 | 上界（×0.0736%） | 上界 ms（85.45 步） | 份額價 net ms | 判定 |
|---|---:|---:|---:|---:|---|
| **群集 1**：MoE 權重歸一化三連 `ffn_moe_weights_sum`[**1**] → `_clamped`[**1**] → `_norm`[**8**]，消費者 `ffn_moe_weighted`[2048,8] | **117**（`SUM_ROWS`/`CLAMP`/`DIV` 各 39 node，普查 x**1.00** = 完全未融合） | 8.6% | **7.4** | **1.89** | 份額價 **< 3 ms**，只有外推價過檻 |
| **群集 2**：`shared_expert_gate_sigmoid`[**1**] → `ffn_shexp_gated`[2048] | 40（`UNARY` 一項，x1.00） | 2.9% | 2.5 | ~1.0 | ✗ |
| 群集 3：GDN 支撐 op（`beta_sigmoid`／`conv_output_silu`／`a_softplus`／`q_conv_predelta`／`k_conv_predelta`／`gate`／`dn_normg_mul`） | 90–170，但**形狀各異、7 個不同宿主** | 6.6–12.5% | 5.6–10.7 | 散在 6–9 個 kind，**單一宿主最大 1.46** | ✗（單點） |
| `ADD` 的集中宿主 `ffn_moe_add` | **0**（9 node → 2 dispatch，09-20 `CGC_ADDFUSE_DBG` 量到 `n_fuse=7 + 2`） | 0% | 0 | 4.2 | ✗ 已融合 |
| `RMS_NORM`（`norm`，x1.86 已融合） | 0（只剩 1 dispatch/層可談） | 0% | 0 | **0.79（精確值）** | ✗ 精確值低於門檻 4× |

⇒ **六族裡沒有一個「單點 ≥3 ms」的目標。** 理由三條，各自獨立可證：
① 兩個最大的集中塊（MoE 的 9-ADD 鏈、`norm` 的 RMS_NORM 鏈）**已經在融合**（`ADD` x2.68、`RMS_NORM` x1.86）；
② 每一族的份額都**散在 6–9 個宿主**（最大 1.46 ms）；
③ 唯一有份額 ≥3 ms 的族（`MUL` 5.14／`UNARY` 4.03／`CPY` 3.56）裡，「同一個節點圖上」可以移掉的
   dispatch 是**單節點 dispatch**（普查 x1.00），要動就得**新寫 kernel**，而那筆交換的價格
   （0.0736%/dispatch）比份額價低 3.9 倍（群集 1：外推 7.4 ms vs 份額 1.89 ms）。

### 七、結論：下一個 ≥3 ms 的目標不在元素級六族裡

1. **家族價是有的，而且很大**：六族合計 **21–29 ms/step（25–33% of 85.45）**，跨 cell 穩定
   （兩個 coarse 讀數換算：48.2%×0.704=**26.7 ms**；45.6%×0.658=**28.6 ms**；逐節點讀數 **21.1 ms**）
   ⇒ 它是 GPU 這一側**最大的單一塊**，但它是「很多小塊的總和」，不是一塊。
2. **單點價沒有**：`uni` 只能給出 `RMS_NORM`（≤0.79 ms），其餘五族在兩種編碼下都拿不到 op-pure 緩衝。
   逐 op 的份額不可識別（§三），而唯一的外推單價（63 µs/dispatch）有本線自己的反例（§五③）。
3. ⇒ **值得動的順序不變，而且更清楚**：仍以 §16 的「非計算 33 ms」為第一順位
   （`gap` 14–19.5 ms ＋ 圖外 11.6 ms），第二順位是 `MUL_MAT` dense（net 9.1–10.4 ms，但
   c-bandwidth 已把它壓到 ≤2.42% 的可動量）。元素級六族的貢獻是**集體的**：它們餵養那個 `gap`，
   而不是各自藏著一塊 ≥3 ms。
4. **如果真要動群集 1**，動手前先做那個**判準量測**（它 0 GPU、只要一次 build）：
   一趟 skip-probe —— env-gated、預設關、**跳過所有輸出元素數 ≤8 的 dispatch**
   （`SUM_ROWS`[1]／`CLAMP`[1]／`DIV`[8]／scalar sigmoid），輸出**必然是錯的（VOID）**，
   但它把 7.4 ms 的上界換成**實測的 Δstep**。這是本 repo 已有的模式（`CGC-HOOK=0`、
   「權重不進池」都是靠故意錯的臂量上界）。**在這個量測跑出來之前，不要寫那顆 300 行的
   `ffn_moe_weighted` 融合 kernel**（G4 §4.3 已判它「過不了 G4 門檻」，本節補的是「連 3 ms 都還沒被證實」）。
5. 數值面的一條區分（下一個要做融合的人該知道）：**群集 1 不安全、群集 2 安全**。
   `SUM_ROWS` 是**歸約** ⇒ 要逐位元就得複刻 `kernel_sum_rows` 的 simd 蝴蝶樹；
   scalar **sigmoid 是逐元素、無歸約** ⇒ 折進 `ffn_shexp_gated` 的 prologue，
   只要運算式逐字相同（同一個 `precise::exp` 路徑），**逐位元等價是構造性的**。

### 八、本節順手挖出來的一個新坑（與 §17 同型，但更陰）

`CGC_N_CB` **不是被丟掉，是被覆寫**：

| 傳法 | `resolve('prod-new', …)['env']['CGC_N_CB']` | 結果 |
|---|---:|---|
| 直接 `CGC_N_CB=16` | **`'8'`** | 靜默變回 `SERVER_N_CB` 的預設 8 ⇒ 以為開了 16 粒度，其實沒有 |
| `CGC_SERVER_N_CB=16` | `'16'` | 正確入口（`run_server.sh:121` 讀它，`:1488` 再寫進 `CGC_N_CB`） |

而且 `CGC_N_CB` 是 profile 的**既有值** ⇒ harness 的 base gate 會拒跑：
`CGC_N_CB: base='8' arm='16' (NOT declared, use !)`，**rc=2**（實測一次；這是閘門第一次在這條線上
證明它會擋「沒宣告的數值/編碼變更」）。正確寫法是 `!CGC_N_CB=16`，本節讀數 C 就是這樣跑的。

第二個坑：**`CGC_DISPATCH_CENSUS` 的 `graph=N` 其實是 worker 切片**，不是圖。它靠 `idx==0` 分片，
而每個 worker 的切片都從自己的 0 開始 ⇒ coarse 下每片 29–31 個 dispatch，per-node 下每片 **1–6** 個；
且 cap 是 `cgc_dsp_graphs < 48`（**切片**數）⇒ **一整步的 dispatch 總數從這支儀器拿不到**
（48 片 ≈ 0.3 步）。要 per-step 總數得把 cap 提到 ≥ (n_cb+1)×n_segs ≈ 680。§17 的「每層 segment 31」
應讀作「一個切片 31」；兩者對「MoE 是 3 個 `MUL_MAT_ID` ＋ 2 個 `GLU`」的結論不影響（那是計數，不是時間）。

### 九、自檢與不可引用清單

* `delta=0.000%`（`seg_busy == layer gpu_sum`）在三個讀數、每個列印區塊都成立。
* `ub ≥ wcntw` 的違反數：**0**（7 個區塊 × 24 行）。`wcntw` 對 work op 的分割和 = `total` 的
  **69.2–93.0%**（其餘被 24 行列印上限截掉，不是遺漏）。
* 三個探針的 t/s（10.19／9.91／10.80）**全部不可引用**（`attribution=swap`；C 另有量具成本）。
* `uni` 對 NOOP op（`VIEW`／`RESHAPE`）的值**不可引用**，它在那裡印的是佇列等待。
* 本節沒有動任何 `src/`、沒有 commit；`docs/mindmap/mindmap.json` 未被本節改動。
* 未做（本節的已知缺口）：per-step dispatch 總數（需要改 census cap）；逐族精確 µs（需要
  op-pure 緩衝，現有顆粒度給不出）；`MUL`／`UNARY`／`CPY`／`GET_ROWS`／`ADD` 的 `uni`。

產物：`Backup/stepbudget_2026-09-29/{ops_default,ops_pernode,delivery_base}.{json,stderr.log}`（三組成對）

## 19. `gap`（段間 GPU 空轉）的歸因與它的下界：它與 buffer 數／dispatch 數無關，與「段邊界停下來觀察」本身有關（2026-09-29）

問題：`gap` 在 §16 是 **14.04 ms/步**、在 §17 的 delivery cell 是 **19.54 ms（22%）**。
它與 (i) **dispatch 數**、(ii) **command-buffer 數**、還是 (iii) **層間等待**相關？能降到多少？

### 一、先把儀器語意講死（不先講這節，後面每個數字都會被誤讀）

* 定義（`ggml-backend.cpp:2155-2175`）：`gap_i = start_i − end_{i−1}`，**只取正值**；
  `start_i` = 段 i 的**第一條** buffer 的 `GPUStartTime`、`end_{i−1}` = 段 i−1 的**最後一條** 的 `GPUEndTime`。
  讀的時刻是「段 i−1 的緩衝全部完成、且段 i+1 **尚未**提交」的那一刻（poll 之後、下一次 submit 之前）。
  ⇒ **這條欄位的語意天生就是「CPU 還沒把下一段送進 GPU」的那個窗**，而不是 GPU 的一種成本。
* 內建交叉檢查：它落在前一段的 hook+submit 時間裡 ⇒ 要對 `(cb + submit)` 讀，不能單獨讀。
* 這條欄位只有在「分段提交＋每段一次完成觀察」的順序下才存在（見 §六）。

### 二、假設檢定（一）：`gap` 對 CPU 側段邊界窗，不對 GPU 側工作量

五支既有臂（皆 NOMINAL、非 `ALL` 列印），**步層級**（48 個 ntok=1 步）的相關：

| 臂 | r(gap, cb) | r(gap, submit) | r(gap, union) | r(gap, wait) | gap/(cb+submit) 中位 |
|---|---:|---:|---:|---:|---:|
| `ops_default`（default, n_cb=8） | **0.99** | 0.01 | 0.14 | 0.31 | 1.69 |
| `decprof`（default, n_cb=8） | **0.99** | 0.03 | 0.05 | 0.21 | 1.95 |
| `delivery_base`（delivery, n_cb=8） | **1.00** | 0.89 | 0.43 | 0.60 | 1.54 |
| `delivery_fuse`（delivery, MMV_FUSE） | **1.00** | 0.90 | 0.16 | 0.42 | 1.50 |
| `ops_pernode`（default, c16+n_main=1） | 0.93 | −0.03 | −0.20 | 0.06 | 0.25 |

⇒ (iii) 成立、GPU 側假設被駁（`gap` 與 `union`／`wait` 幾乎不相關）。但要注意：**這是步層級相關，
由尾巴驅動**（見下一節）。

### 三、逐層看：`gap` 是「每層 0.24 ms 的均勻段邊界稅」＋「疊在 fill 風暴上的尾巴」

`CGC_DECODE_PROFILE_ALL=1` 給每層一列（40 層 × 53 步）。base 臂（本輪同一次呼叫）：

| 量 | 值 |
|---|---|
| 逐層 `gap` 中位 | **0.24 ms/層**（p90 = 1.17、8% 的列精確為 0） |
| 逐層 `gap` 對層號的相關 | **r(gap, L) = 0.05**（L0 = 0.00：第一段沒有前一段） |
| 分帶中位 | L0 0.00／L1-9 0.26／L10-19 0.24／L20-29 0.24／L30-39 0.24 ⇒ **完全均勻** |
| 逐層 `cb` 中位 | **0.01 ms/層**（平均 0.11）⇒ `cb` 集中在少數層（fill 事件）、`gap` 均勻 |
| 最貴的三層 | L1 1.14（cb 0.03）、L3 0.73、L2 0.37 ⇒ 仍是毫秒以下 |

⇒ 兩個成分可分辨：① **均勻的段邊界稅**（≤0.35 ms 平均 × 40 ≈ 10–14 ms/步）；
② **尾巴**（步層級 `gap` 最高到 36.5 ms，與 `cb` 同步 = miss/fill 風暴）。
`gap` 的「22%」主要是①，而 `r(gap,cb)=0.99` 主要來自②——兩個都要講，否則會誤以為
「把 fill 藏好就沒有 gap 了」。

### 四、假設檢定（二）：command-buffer 數（同一次呼叫的 n_cb 掃描）

`CGC_SERVER_N_CB` 1／8／16（＝每段 2／9／17 條 command buffer；**數值路徑完全相同**）：

| 臂 | 每段 buffer | `gap` p50 | `gap` p10／p90 | `union` p50 | step p50 | prefill（batched）步 | pp |
|---|---:|---:|---:|---:|---:|---:|---:|
| base（n_cb=8） | 9 | **13.89** | 11.11／22.15 | 70.66 | 75.91 | 6.68／7.06／7.07 s | 279.2 |
| n_cb=1 | 2 | **14.83** | 12.68／18.98 | 61.52 | 75.32 | 9.36／10.11／10.43 s | 192.2 |
| n_cb=16 | 17 | **14.70** | 10.83／23.45 | 84.71 | 86.42 | **12.77／12.88／13.04 s** | 155.9 |

⇒ **`gap` 對 buffer 數不敏感**（三個方向都動了 2× 以上，`gap` 只動 0.9 ms）。被 buffer 數動到的是
`union`（重疊更多 ⇒ 重複計數更多）與 **prefill（1.5–1.9× 變慢）**。
另一個保守證據：G4 已量過 n_cb={1,2,16} 的 t/s 是平的（`cb_sweep.json`）。

### 五、假設檢定（三）：dispatch 數 —— 它**不可能**在同一 run 內相關

decode 每一步的圖完全相同（§18 的 `nd` 在兩個 cell 一模一樣）⇒ **同 run 內 `gap` 與 dispatch 數
沒有變異，相關性檢定在此構造上不存在**。唯一可用的介入是 §17 的 `CGC_MMV_FUSE` A/B：
每層 segment **−2.9 個 dispatch**（30.9→28.0）⇒ `gap` **19.54→20.13（+0.59 ms，方向相反）**。
⇒ dispatch 數不是 `gap` 的驅動量。

### 六、能降到多少：三個層次，只有第三層是真的（但代價不是零）

| 層次 | 手段 | `gap` | step | 代價／限制 |
|---|---|---:|---:|---|
| **佈局** | `CGC_CB_N_MAIN=1`(+c16)：把段的起點畫在一條 1-node buffer 上 | 15.38 → **2.42** | 82.33 → 81.19（兩個 NOMINAL 場次） | `union` 77.68 → **90.44**、`(union+gap)` 93.06 → **92.86** ⇒ **守恆**：挪走的只是位置的重新分配，值 ~1.1 ms（噪音內）|
| **刪除觀察點** | `CGC_SUBMIT_AHEAD=1`（下段在 hook 之前就提交） | **量不到**：儀器直接印 `(NO TIMESTAMPS)`（45 次） | 75.91 → **58.97（−22.3%）** | 依構造 racy／輸出 garbage；起跑 thermal HEAVY；**prefill 7.06 s → 11.0–12.7 s（1.6–1.8× 慢）** |
| **刪除機制** | `CGC_SEG_BATCH=1`（41 段 → 1 段） | 無逐層列 | 該臂 tg 16.15（不可引用） | 連 pool 讀取都是 **0 次**（做查表的 per-layer hook 被跳過）⇒ 是「把機制拿掉」的天花板，不是設計 |

⇒ 三條結論：

1. **`gap` 不是一個可以單獨倒空的桶。** 它只在「分段提交＋每段一次完成觀察」這個順序下存在；
   把觀察點拿掉它就歸零，**但同一支儀器同時失去回報能力**（`(NO TIMESTAMPS)`）。
   ⇒ 「`gap` 能降到多少」的正確答案不是一個 ms 數，而是：**它的下界是這條設計的下界**。
2. **可收割的那部分**的上界由 submit-ahead 給：**≈17 ms/步（−22%）**，而它要同時付
   (a) 正確性（racy，garbage）、(b) **prefill 1.6–1.8× 變慢**（交付的另一軸！）、(c) 起跑 HEAVY。
   ⇒ 這一格不能與 §16 表裡的 kernel 兩列相加；§16「空轉減半」那一列應改寫為
   「段邊界窗減半（代價：prefill 1.6×、正確性）」。
3. **窗內還有一段沒有儀器**：`gap/(cb+submit)` 在這些臂裡是 1.36–2.79 ⇒ 段邊界窗裡有
   一大段 CPU 時間（encode 下段、commit）兩支儀器都沒量到。要結案 `gap` 的帳，缺的是
   **段 i 的 end 到段 i+1 的 start 之間的 CPU 側切分**（不是再一次 GPU 側掃描）。

### 七、本輪不可引用清單（這節的硬限制）

* 五臂**全部 VOID**：arm 1 起跑 NOMINAL 但 worst **HEAVY**（73/154 樣本 HEAVY），arm 2–5 **起跑即 HEAVY**。
  ⇒ 所有 t/s（11.49／11.28／9.76／14.90／16.15）不得引用；`gap`／`union` 是 GPU 時鐘量，
  但**跨臂熱狀態不同** ⇒ 只有「同一臂內的比例」與「跨臂的**結構性**結論」可用，跨臂的 ms 差要當上界。
* `CGC_SEG_BATCH=1` 臂的 cache 統計是 `hit 100.0% misses 0 reads 0`（pool 路徑沒跑）⇒ 那一臂
  連「decode 步成本」都不是同一個口徑。
* 協定修正（下一次要點數的話）：**冷窗**（idle ≥150 s）＋ **交錯 base/treat/base** ＋ **每臂開跑前**過熱閘
  （本輪的閘只在前置檢查，臂序一長就整批落在 HEAVY 上）。
* 順手發現一個**產物完整性**缺陷：`harness bench` 的 per-arm 目錄用截斷檔名 ⇒
  `CGC_SERVER_N_CB=1` 與 `=16` 兩臂**撞名**，只留一份（本節的逐臂 log 缺一支；
  合併的 `gap_sweep.stderr.log` 完整，本節全部讀數取自它）。修法：per-arm 檔名加 hash 或長度上限。

產物：`Backup/stepbudget_2026-09-29/gap_sweep.{json,stderr.log}`（含 5 臂；`gap_sweep.logs/` 缺 n_cb=1 那支，見 §七）

---

## 20. dispatch 普查的巢狀盲點：把「每個 encode 呼叫」換成「每個 kernel」，並修掉普查自己的一個 race（2026-09-29）

### 一、問題：v1 數的是「encode 呼叫」，不是 kernel

`CGC-DISPATCH:`（v1）每個**encode 呼叫**記一次 dispatch（一個 graph node 或一組融合 node）。
GPU 啟動的是 kernel，不是 encode 呼叫。靜態盤點 `ggml-metal-ops.cpp`：**61 個 op encoder 裡有 15 個
含 ≥2 個 dispatch call site**，decode 上正是要緊的幾個（`flash_attn_ext` 7、`mul_mat` 4、
`mul_mat_id` 4、`mul_mat_id_glu_fused` 3、`bin` 2、`unary` 2）。
⇒ 一列寫著 `MUL_MAT_ID dispatches=3` 的表，可能是 3 個 encode 呼叫、卻啟動 5–12 個 kernel；
**由融合函式自己在內部啟動的 kernel 不在任何一欄裡**：它有成本、有位址、沒有計數。
兩欄之間的差因此長年被讀成「融合買到多少」，但其中一部分其實是「計數器看不到」。

### 二、做法（instrument，add-only）

* **攔截點**：在 `ggml-metal-ops.cpp` 的 include 之後、第一個呼叫者之前，把
  `ggml_metal_encoder_dispatch_threadgroups` 包成 function-like macro：先 tick，再把每個引數原封不動
  轉給本體（`ggml-metal-device.m` 是全樹唯一的定義）。macro 是 token 比對 ⇒ **三個跨行的呼叫點也一併改寫**。
* **kernel 的名字**：dispatch 不帶 pipeline 參數，而 pipeline 物件是不透明的
  `id<MTLComputePipelineState>`。所以在 `struct ggml_metal_pipeline` 加 `char name[160]`
  （編譯時 `snprintf` 填入；**用固定緩衝不用指標**，因為 `kernel_%s_%s` 這種名字是堆疊上的 `char[]`，
  存指標會懸空），在 `struct ggml_metal_encoder` 記 `last_pipeline`，新增
  `ggml_metal_encoder_last_pipeline()` 給普查讀——名字是唯一能把「數到了」變成「看得懂」的東西。
* **繞過 encoder 的唯一一條路**：`ggml_metal_spec_decode_verify`（`ggml-metal-context.m`）自己開
  command buffer、用裸的 `[encoder dispatchThreads:]`。那是 ObjC message send，**沒有 macro 攔得到**
  ⇒ 讓它自己申報（`cgc_dispatch_census_direct()`，宣告在 `ggml-metal-ops.h`）。
* **歸屬**：`ggml_metal_op_encode_impl` 只有一個入口（`ggml_metal_op_encode`），而且設定 op 之後
  唯一的出口就是函式尾端、尾端前一行就是重設 ⇒ 沒有 dispatch 會落到過期的 op 上。
* 輸出（每 slice）：`kernels`（GPU 實際啟動數）／`enc_launch`（啟動 ≥1 kernel 的 encode 呼叫數）／
  **`nested = kernels − enc_launch − direct`（盲點的大小）**／`direct`（op 之外啟動的）。
  接著兩張表：每個 op 的 `enc/kernels/nested`，以及**按 `nested` 排序的 kernel 名字**。

### 三、第一次跑就抓到普查自己的錯：encoder 迴圈是**並行**的

第一版把累加器做成 process 全域，log 立刻出現 `slice=4` 印了 **5 次**、而且內容不同
（`kernels=4 direct=0` 與 `kernels=8 direct=1` 交替）——**lost update，不是重印**。

原因在 `ggml-metal-context.m`：`ggml_metal_graph_compute` 先在呼叫執行緒上跑
`ctx->encode_async(n_cb)`（頭段），再 `dispatch_apply(n_cb, ctx->d_queue, ctx->encode_async)`
把其餘段丟上佇列 ⇒ **n_cb+1 個 slice 同時**跑 `ggml_metal_op_encode_impl`，也就同時 tick。
兩個獨立的地方因此壞掉：

1. 計數本身 lost update ⇒ `kernels` 是「連下界都不算」的下界，per-(op,kernel) 表會丟掉輸的那條執行緒
   剛 append 的列；
2. slice 編號在 `fprintf` 的**引數**裡讀、卻在**約 50 個 fprintf 之後**才 `++` ⇒ 讀-改-寫窗極大。
   這正是為什麼 v1 看起來乾淨（它的 `cgc_dsp_graphs++` 在印之前幾個指令就完成）、v2 卻不乾淨。

**修法**：累加器全部改 `thread_local`（一個 slice 一條執行緒 ⇒ 沒有共享），slice 編號改用
`std::atomic` 的 `fetch_add`，整塊列印用 `std::mutex` 包住（否則兩個並行 slice 的列會交錯，
沒有任何一列能歸屬到 slice）。

### 四、修好之後：自檢與實測（n_cb=8 的四場 + n_cb=1 的一場）

自檢是**硬條件**：`kernels == enc_launch + nested + direct`，以及 slice 編號 1..48 全唯一。

| 場次 | slice | sum kernels | enc_launch | **nested** | direct | 身分式違反 | 編號唯一 | tg |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 修前（`census_v2`） | **45** | 392 | 342 | 25 | 0 | 0/45 | **41/45** | — |
| fixed | 48 | 356 | 334 | 22 | 0 | 0/48 | 48/48 | 11.80（VOID:swap）|
| ctl | 48 | 362 | 339 | 23 | 0 | 0/48 | 48/48 | 11.66（VOID:swap）|
| final1 | 48 | 363 | 340 | 23 | 0 | 0/48 | 48/48 | 9.06（VOID:both）|
| final2 | 48 | 360 | 337 | 23 | 0 | 0/48 | 48/48 | 10.59（VOID:both）|
| ncb1（n_cb=1） | 48 | 1174 | 1099 | 75 | 0 | 0/48 | 48/48 | 11.84（VOID:both）|

**每 op 實測**：除了**一個例外**，**每個 op 都是「一個 encode 呼叫 = 一個 kernel」**。
(n_cb=8 取 final1／final2；n_cb=1 取 ncb1)

| op | enc (8/8/1) | kernels | nested |
|---|---|---:|---:|
| MUL_MAT | 78／77／255 | 78／77／255 | 0 |
| **MUL_MAT_ID** | **21／21／69** | **42／42／138** | **21／21／69** |
| UNARY | 31／33／101 | 31／33／101 | 0 |
| MUL | 30／31／88 | 30／31／88 | 0 |
| ADD | 27／26／88 | 27／26／88 | 0 |
| RMS_NORM | 24／22／78 | 24／22／78 | 0 |
| GET_ROWS | 19／19／59 | 19／19／59 | 0 |
| GLU | 14／14／46 | 14／14／46 | 0 |

⇒ **盲點是精確的 2×，而且是兩個 op 寬**：`MUL_MAT_ID` 的 N 個 encode 呼叫啟動 2N 個 kernel
（21→42、69→138，三場一致），`FLASH_ATTN_EXT` 也同樣是 2×（13→26，見 §22 §三）。
⚠ **本句原本寫成「恰好只有一個 op 寬」，與同一節自己那張表（`FLASH_ATTN_EXT ... nest=1`）矛盾**——
是 §20 的一處錯誤，已在 §22 §三用 400-slice 窗口改正並留下痕跡。

**被漏掉的 kernel 有名字**（`n=次數`，`nested=` 其中屬於巢狀的次數）：

```
n_cb=8 (final1 / final2)
  n=14 nested=14  kernel_mul_mm_id_iq2_s_f32_bci=0                        op=MUL_MAT_ID
  n=7  nested=7   kernel_mul_mm_id_iq3_s_f32_bci=0                        op=MUL_MAT_ID
  n=2  nested=2   kernel_flash_attn_ext_q8_0_dk256_dv256_mask=1_...       op=FLASH_ATTN_EXT
n_cb=1 (ncb1)
  n=46 nested=46  kernel_mul_mm_id_iq2_s_f32_bci=0                        op=MUL_MAT_ID
  n=23 nested=23  kernel_mul_mm_id_iq3_s_f32_bci=0                        op=MUL_MAT_ID
  n=6  nested=6   kernel_flash_attn_ext_q8_0_dk256_dv256_mask=1_...       op=FLASH_ATTN_EXT
```

（iq2_s : iq3_s = 2 : 1 在兩個 n_cb 下都成立；46+23=69、14+7=21 ⇒ 與 `nested` 總數對得上。）

它們是什麼：`mul_mat_id` 在**同一個 encode 呼叫內**先啟動自己的 `..._map0_...` id 重映射 kernel，
再啟動專家 matmul。map0 從不是巢狀的（`kernel_mul_mm_id_map0_ne20_8_ne02=256` ＝ n=21 nested=0：
永遠是該呼叫的第一個 kernel）⇒ **被漏掉的是「map0＋matmul」這一對裡的 matmul 那半。**

**順帶更正兩件原本寫在註解裡的假設**：

1. 最初的理由是「融合函式內部自己叫的 down 投射」，即 `kernel_mul_mv_id_down_combine_*`。
   實測 **0 次**（每一場都沒有）⇒ `ggml_metal_op_mul_mat_id_down_combine`（有定義、也有呼叫點）
   **不在此模型的 decode 路徑上**。盲點是真的，但命名它的那個 kernel 不是這張圖藏起來的那個。
2. 靜態盤點說 15 個 encoder 有 ≥2 個 dispatch call site（`flash_attn_ext` 甚至 7 個），
   **實測只有 1 個 op 真的多次啟動**。其餘的多站點是 if/else 分支（每次呼叫只走一條），
   不是連續多次啟動。**靜態 call site 數不能當成「漏掉多少」的估計量。**

### 五、這一節同時證明了：v1 的 `dispatches` 欄位被並行度污染

v1 每個 encode 呼叫記一次 ⇒ 在無 race 的世界裡 **v1 必然 ≥ v2 的 `enc_launch`**（前者是後者的超集）。
只要把並行度調低就能檢定，而且用**整場加總**比對（不靠逐 slice 配對，避免輸出交錯的干擾）：

| 並行 slice 數 | sum v1 `dispatches` | sum v2 `enc_launch` | 差 | 逐 slice「v1 < enc_launch」（邏輯不可能） |
|---|---:|---:|---:|---:|
| **2**（n_cb=1） | 1098 | 1099 | **−1（0.1%）** | **1/48** |
| **9**（n_cb=8，final1） | 467 | 340 | **+127（+37%）** | **12/48** |
| 9（n_cb=8，fixed） | 406 | 334 | +72（+22%） | 10/48 |

⇒ **偏差隨並行度成長**，2 條時兩者幾乎相等（1098 vs 1099），9 條時差 37%。
這不是「encode 呼叫但沒啟動 kernel」能解釋的（那在兩個 n_cb 下都該等比例出現），而是共用池被
reset／互相覆蓋。v1 的 `graph=` 編號之所以看似乾淨，只是因為它的讀-改-寫窗（印之前幾個指令）
比 v2 修前的窗（印之後 ~50 個 fprintf）窄得多——**不是沒有 race**。

**影響面與本節沒有動它的理由**：§18 依賴 v1 的 `dispatches` 欄位，那些表的**逐 slice 歸屬**必須
視為近似。本節**刻意不改 v1**：v1 已被既有的 claim／arm ledger 綁定（`claim_instrument_check`），
改它等於讓 §18 的表失去儀器依據，這該由 operator 裁決，不是順手改。
建議的後續：把 v1 也改 `thread_local`，再用同一支臂重跑 §18 的表。
在此之前，**v2 的 op 表是 v1 那張表的嚴格超集**（`enc`／`kernels`／`nested` 三欄，列出所有啟動過
kernel 的 op），可以直接取代它。

### 六、本節的可引用性

* **可引用**：`kernels == enc_launch + nested + direct`（5 場 48/48，0 違反）；`MUL_MAT_ID` 的精確 2×
  （21→42 與 69→138，跨並行度一致）；三個巢狀 kernel 的名字與次數（同 n_cb 下跨場一字不差）；
  「靜態 15/61 vs 實測 1 個 op」；「v1 偏差隨並行度成長」這張表。
* **不可引用**：這五場的 **t/s 全部 VOID**（fixed/ctl：`swap`；final1/final2/ncb1：`both`、
  thermal 起跑即 HEAVY）。本輪刻意用 `--no-thermal-gate`，因為這是**儀器檢查不是量測**，
  而且 cell 口徑走權威預設（`harness bench` 對 `--gen 16 --reps 1` 是 fail-closed，
  實測會被拒跑：`gen: 實際 16 ≠ 權威 128`）。t/s 只當「機器還活著」的附帶資訊。
* **沒被跑到的一條路**：`direct` 在五場**全為 0** ⇒ `cgc_dispatch_census_direct()`（spec-decode 的
  裸 dispatch）**寫了、接上了、但這些臂都沒有觸發它**。修前那場出現的 `direct=1` 是 race 的產物
  （同一編號印 5 次那場），不可當證據。要驗它需要一支真的走 spec-decode 的臂。
* **建置**：改 5 個檔（`ggml-metal-ops.{h,cpp}`、`ggml-metal-device.{h,m}`、`ggml-metal-context.m`）
  ＋ `scripts/run_server.sh` 的 allowlist 註解。`build_when_clear.sh --now` rc=0、
  **新增 0 個 warning**（`struct ggml_metal_pipeline.name` 在 `ggml_metal_pipeline_init` 顯式初始化：
  patch 帶進來的 warning 是 patch 自己的債）。`bash -n scripts/run_server.sh` OK。
  `libggml-metal.dylib` `0854d51d…`（改前）→ `f749161f…`；`CGC-DISPATCH2:` 字串 3 處、
  `kernel_spec_decode_verify` 2 處、`_cgc_dispatch_census_direct` 與
  `_ggml_metal_encoder_last_pipeline` 已 export。
* **工具陷阱（給下一個 session）**：本 session 的檔案編輯工具以 **project root** 解析相對路徑，
  而 project root 是 `~/Documents/flashkv0516`（branch `dev`），**不是** `~/Documents/flashkv-devserver`。
  第一次「成功」的編輯因此落在**錯的 worktree** 上；已逐字還原（只移除自己插入的區塊，
  不動別條線的 hunk）。**要改 `flashkv-devserver` 只能用 shell 的絕對路徑。**

產物：`Backup/stepbudget_2026-09-29/census_v2{,_fixed,_ctl,_final1,_final2,_ncb1}.{json,stderr.log,stdout.log}`
（`census_v2.*` ＝修前那場；其餘為修後）。程式碼見上。

**補正（同日稍後，見 §21 §三）：上面所有 `nested=` 都是 PREFILL 讀數。** 那個 48-slice 窗口全部落在
第一個 decode step 之前（最後一列在 stderr 第 746 行，DECPROF 第一個 `ntok=1` 在第 1244 行），
而 400-slice 窗口也到不了 decode（第 400 列在第 3717 行 / 第一個 ntok=1 在第 4097 行）。
「唯一多 kernel 的 op 是 `MUL_MAT_ID`、2×」這個結構結論在兩個窗口都成立，但它成立的位置是 prefill；
decode 端（`mul_mv_id`，一次呼叫一個 kernel）在這兩個窗口裡**一次都沒出現**。要看 decode，
slice 上限是錯的閘門（prefill 一個階段就吐超過 400 個 slice），閘門必須是 batch size。

---

## 21. 評估：把 `mul_mat_id` 的 `map0` 併進專家 matmul（或整層省掉）—— 判決是「不改」，代價是 0 個 dispatch/步（2026-09-29）

§20 的普查把一個具體候選放到桌上：`MUL_MAT_ID` 是唯一「一個 encode 呼叫啟動 2 個 kernel」的 op，
多出來的那個是 `kernel_mul_mm_id_map0_ne20_8_ne02=256`（名字下 `nested=` 那一欄）。本節問它值不值得動。

### 一、`map0` 是什麼

`ggml-metal.metal:10459` 起的 `kernel_mul_mm_id_map0`：**一個 threadgroup、`ne02` 條 thread**
（`ne02` = 專家數），每條 thread 認領一個**專家 id**，掃過該批次的全部 token × `ne20`（每 token 選幾個專家）
的 ids，數出「這個專家被選了幾次」，把線性位置 `(i21+t)*ne20 + sel - 1` 寫進
`hids[ide*ne21 + n_all]`，最後寫 `tpe[ide] = n_all`。也就是一份**逐專家的直方圖／壓縮表**
（`tpe` = tokens-per-expert，`hids` = 每個專家要吃哪些列）。MM 版的專家 matmul 靠這兩份資料把
「同一專家的列」聚成一批去做批次矩陣乘。

### 二、直接檢定：它有一道 `ne21 >= 32` 的閘，而 decode 是 1 個 token

`ggml-metal-ops.cpp:4936` 與 `:4949`：

```c
const int ne21_mm_id_min = 32;      // ne21 = n_rows (batch size)
if (props_dev->has_simdgroup_mm && ne00 >= 64 && (ne21 >= ne21_mm_id_min)) {
    ... map0 在這裡面 ...
```

⇒ **`map0` 只在 batch ≥ 32 個 token 時被啟動。** decode 一步是 **1 個 token** ⇒ 走 MV 路徑
（`kernel_mul_mv_id_*`，一個 encode 呼叫一個 kernel），**`map0` 一次都不會啟動**。
所以「把 map0 併掉」在 decode 上省下的 dispatch 數是**精確的 0**，而 decode 正是 25 t/s 那一軸。

### 三、普查端證實：把窗口開到 400 個 slice 也到不了 decode

同一支臂、`CGC_DISPATCH_CENSUS_MAX` 開到 400（本節為此新增的旋鈕，見 §七）：

| 窗口 | slice | kernels | enc_launch | nested | 身分式違反 | `MUL_MAT_ID` enc→kernels | `mul_mv_id` 出現次數 |
|---|---:|---:|---:|---:|---:|---|---:|
| 預設 48 | 48 | 363 | 340 | 23 | 0/48 | 21 → 42 | **0** |
| 400 | 400 | 2642 | 2473 | 169 | **0/400** | 156 → 312 | **0** |
| 400（補正後再跑一次預設） | 48 | 315 | 296 | 19 | 0/48 | 18 → 36 | **0** |

* 兩個窗口裡 `MUL_MAT_ID` 都是**精確的 2×**，多出來的那半由
  `kernel_mul_mm_id_iq2_s_f32_bci=0`（n=102 nested=102）、`iq3_s`（51/51）、`iq4_xs`（3/3）組成。
* **`mul_mv_id` 一次都沒出現**，而所有帶 `MUL_MAT_ID` 的 slice 都是 MM 路徑（kernels > enc）。
* 位置證據：48-slice 窗口的最後一列在 stderr **第 746 行**、400-slice 的在**第 3717 行**，
  而 DECPROF 第一個 `ntok=1`（decode）步分別在**第 1244 行 / 第 4097 行**。
  ⇒ **兩個窗口都完全落在 decode 之前。**

⇒ 這兩件事互相印證：`map0` 的閘（§二，程式碼）說「decode 沒有 map0」，普查說「這兩個窗口裡
一個 decode 步都沒有」。**結論一致，而且兩邊都不是靠推論。**

### 四、那它值多少？—— 在 prefill 上，而 prefill 已經不是瓶頸

400-slice 窗口（＝prefill 階段的一部分）：`map0` 佔 `MUL_MAT_ID` kernel 數的 **50%**（156/312），
佔**全部** kernel 數的 **5.9%**（156/2642）。交付目標是「prefill 250 / decode 25」，
而這批臂的 prefill 是 **288–296 t/s**（量測，非引用值見 §六）⇒ **prefill 這一軸已經在目標之上**。

### 五、結構上能不能併？—— 「整層省掉」不等於「少一個 dispatch」，而是「改走另一條路」

不能便宜地併進專家 matmul：跑專家 `ide` 那些列的 threadgroup **必須先知道** `tpe[ide]`（有幾列）
與 `hids` 那份清單才能開工。把 map0 折進去的唯一做法，是讓每個專家的 threadgroup **各自重掃**
整個批次的 ids（prefill 2048×8 = 16K 個 u16）——**那正是 MV 路徑在做的事**，而 MV 路徑存在的理由
就是它對小批次更好。所以：

> 「消掉 map0」≡「改走 MV 路徑」。兩者不是獨立的選擇；那道 `ne21 >= 32` 的閘就是「哪一邊贏」的界線。

再加上本引擎已經量過的反例（§17）：`CGC_MMV_FUSE` 把每層 segment 的 dispatch 從 30.9 砍到 28.0
（−2.9），結果是 **90.41 → 93.56 ms（+3.5% 更慢）**，M1 4/9 FAIL。
以及 09-20 的單位價（0.0736%/dispatch ≈ 63–70 µs/dispatch）——就算 400-slice 窗口裡那 156 個 map0
全部免費，它們所在的位置也不是目標那一軸。

### 六、判決

**不改。** 三個理由，依序是決定性的：

1. **decode 每步省 0 個 dispatch**（`ne21 >= 32` 的閘；普查在 400 個 slice 內沒看過任何 MV 呼叫）。
   交付要的是 decode 25 t/s，這一刀切在別的地方。
2. **唯一會被它影響的 prefill 已經在目標之上**（288–296 t/s vs 250）。
3. **同一類改動在本引擎有量過的反例**（§17 砍 dispatch 反而慢 3.5%），而結構上「省掉 map0」等於
   改走 MV，那不是省，是換路。

* 本節的 t/s（288.37／11.50 等）**一律 VOID**：`--no-thermal-gate`、attribution `both`／thermal HEAVY，
  且這是儀器檢查不是量測；只當「機器還活著」。**可引用**的是 §三 的結構數字
  （2×、身分式 0/400、`mul_mv_id` 0 次）與 §二 的程式碼閘。

### 七、這輪順手修掉的一個儀器缺陷：v2 被 v1 的天花板綁住（重要）

把窗口開到 400 時第一次跑**仍然只印 48 列**。原因不是旋鈕沒生效（`CGC_DUMP_ENV` 證實
`CGC_DISPATCH_CENSUS_MAX=400` 有到 server），而是**結構**：

* v2 的列印整塊**嵌在 v1 的 `if (cgc_dsp && cgc_dsp_graphs < 48)` 裡面**；
* 而 v1 到 48 之後**連累加都停**（`if (cgc_dsp_graphs < 48)` 才累加），
  ⇒ v1 的 flush 條件 `cgc_dsp_tot > 0` **永遠不再成立**，v2 的 flush 也就永遠不會觸發。

所以無論旋鈕給多少，v2 都不可能超過 48 —— 一個「靜默地再次印出 prefill 窗口」的失敗，
正好是這一節要避免的那種。修法兩件：

1. 新增 `CGC_DISPATCH_CENSUS_MAX`（預設 48＝與 2026-09-29 那批可比的窗口），**並列進 allowlist**
   （launch line 是 `env "${SERVER_ENV[@]}"`，沒列到的 CGC_* 會靜默丟掉——丟掉窗口旋鈕是最糟的一種，
   因為普查仍會印出一個看起來很合理的 48-slice 窗口）。
2. 把 v2 的列印抽成 `cgc_dsp2_flush()`，並改由 **v2 自己的狀態**觸發
   （`idx == 0 && cgc_dsp2_kern_tot > 0`），位置搬到 v1 區塊**外面**。

修後以預設窗口重跑一次（§三 第三列）：48 slice、編號全唯一、身分式 0/48、`MUL_MAT_ID` 18 → 36
（仍然精確 2×）⇒ 解耦沒有改變預設行為的結構，只讓「開大窗口」真的有效。

**仍然未解**：就算解耦了，400 個 slice 也到不了 decode（prefill 一個階段就吐 >400 個 slice）。
要普查 decode，閘門必須是 **batch size**（例如只在 `ne21 == 1` 的 pass 印），不是 slice 上限。
這是本節留給下一步的唯一一件儀器工作。

產物：`Backup/stepbudget_2026-09-29/census_v2{,_post,_decode}.{json,stderr.log,stdout.log}`
（`_decode` ＝ `CENSUS_MAX=400`；`_post` ＝ 解耦後的預設窗口）。

---

## 22. 「逐 op kernel 數」× 「逐 op GPU 時間」的 join **不成立**——以及為什麼（附 map0 的三個界）（2026-09-29）

§21 判決 map0 不值得改，但留下一筆技術債：**map0 到底吃掉多少？** 本節試圖把兩支儀器接起來回答。
**結論是接不起來**，而且接不起來的原因是本節最有價值的產出。過程中也抓到 §20 自己的一處錯誤。

### 一、原先的假設，以及它錯在哪

想法很直：

* **普查（v2）** 給逐 op 的真實 **kernel 數**（`kernels`）與 **encode 呼叫數**（`enc_launch`）；
  於是 `k = kernels / enc_launch` 是「一次 encode 啟動幾個 kernel」。
* **`CGC_GPUOPS`** 給逐 op 的 **GPU 時間預算**（`wcntw`）與它的 **node 數**（`nd`）。
* 若「一個 node ⇔ 一次 encode 呼叫」，就能用 `µs per kernel = wcntw / kernels`，並把
  §16／§18 那些「按 node 分帳」的表校準到真實 kernel 數。

**第二步的那個若就是錯的。** 我原先在 §一 想寫「`enc_launch` 就是 node 數」，但沒有先驗它就往下算；
一驗就發現兩個量在逐 op 上差到 15 倍（§三）。**一個未驗的假設差點變成一節有數字的結論。**

### 二、總和對得上——而這個「對得上」是巧合

| 量 | 值 |
|---|---:|
| 普查窗口（400 slice） | `kernels=2645`、`enc_launch=2476`、`nested=169`、`direct=0` |
| `CGC-GPUOPS step=1` | `total=6842.81 ms`、`nodes_all=3799`、`nodes_work=2365` |
| **`enc_launch / nodes_work`** | **1.047** |

總和只差 4.7%，看起來像「窗口 ≈ 1.05 個 prefill step」。**但逐 op 一拆就崩**（§三）。
真正發生的是**兩個方向相反的效果相消**：

* 窗口其實蓋了 **>1 個 step**（§三 的 0.75 族顯示約 4/3）；
* 有幾個 op 的一次 encode 呼叫**吃掉多個 node**（融合），把 `enc_launch` 往下拉。

兩者相乘剛好 ≈1.05。**所以「總和對得上」不但不能證明 join 成立，還正好掩蓋了它不成立。**

### 三、逐 op 對照：`nodes/encode = nd / enc_launch` 從 **0.75** 到 **15.00**

| op | `nd`(GPUOPS) | `enc`(普查) | `enc/nd` | `nd/enc` |
|---|---:|---:|---:|---:|
| MUL_MAT | 426 | 564 | 1.32 | 0.76 |
| MUL_MAT_ID | 117 | 156 | 1.33 | 0.75 |
| FLASH_ATTN_EXT | 10 | 13 | 1.30 | 0.77 |
| UNARY | 169 | 224 | 1.33 | 0.75 |
| RMS_NORM | 130 | 172 | 1.32 | 0.76 |
| GLU | 78 | 104 | 1.33 | 0.75 |
| DIV / SUM_ROWS / CLAMP | 39 | 52 | **1.33** | **0.75** |
| SCALE / L2_NORM / CONCAT / SOFT_MAX / ROPE / SET_ROWS / GATED_DELTA_NET | — | — | 1.27–1.33 | 0.75–0.79 |
| **ADD** | 421 | 197 | **0.47** | **2.14** |
| CPY | 120 | 80 | 0.67 | 1.50 |
| GET_ROWS | 159 | 133 | 0.84 | 1.20 |
| MUL | 278 | 234 | 0.84 | 1.19 |
| **SSM_CONV** | 30 | 2 | **0.07** | **15.00** |
| ARGSORT / CONT | 0 | 51 / 13 | — | GPUOPS 不列為 work op |

兩件事同時成立：

1. **13 個彼此無關的 op 落在 `enc/nd = 1.333` 上，一字不差**（4/3）。
   這是**儀器之間的常數**，方向是「窗口比一個 step 大」，不是那些 op 有共同性質。
2. 其餘的偏離**全部朝同一個方向**（`enc` 偏少 ⇒ 一次 encode 吃多個 node）：ADD 2.14、CPY 1.50、
   MUL 1.19、GET_ROWS 1.20、**SSM_CONV 15.00**。這與普查自己在 kernel 名字裡印的 `n_fuse`
   （`kernel_bin_fuse_..._nf=1`…）以及 §18 記過的「`ffn_moe_add` 9-ADD 鏈已融合（n_fuse=7+2）」一致。
   **SSM_CONV 的 15.00 是這裡最大的一個**：30 個 node 只發 2 次 encode。

⇒ **`enc_launch` 是「encode 呼叫數」，不是「node 數」**，而 GPUOPS 的 `nd` 是 node 數。
在融合族上兩者差 1.2–15 倍，方向固定；在非融合族上差一個常數 4/3（窗口效應）。
**沒有任何逐 op 的正規化可以同時消掉這兩個效應**，因為第二個是逐 op 的、第一個是全局的。

### 三之二、普查本身仍然成立的結果：`k = kernels / enc_launch`

這一欄只用普查自己的兩個數，與 GPUOPS 無關，所以不受上面影響（400 slice）：

| op | `enc` | `kernels` | **k** |
|---|---:|---:|---:|
| **MUL_MAT_ID**（map0 + mm） | 156 | **312** | **2.00** |
| **FLASH_ATTN_EXT**（主 kernel + `..._blk_nqptg=8_ncpsg=64`） | 13 | **26** | **2.00** |
| 其餘 **21** 個 op（MUL_MAT 564、MUL 234、UNARY 224、ADD 197、RMS_NORM 172、GET_ROWS 133、GLU 104、…） | — | — | **1.00** |

共 **23** 個 op 出現過；**只有兩個** k≠1，且**精確**是 2，沒有中間值。
⇒ 任何「按 node 數分帳」的預算，對這兩個 op 都把單一 kernel 的價格算成一半，而多出來的那個
kernel（map0、FA 的 blk）**沒有自己的 node**，所以在這類預算裡**被計價 0 次**。這是結構性的。
⇒ 這也**改正了 §20 的一句話**：那裡寫「盲點恰好只有一個 op 寬」，但同一節自己的表就列了
`FLASH_ATTN_EXT ... nest=1`。是**兩個 op**，`MUL_MAT_ID` 與 `FLASH_ATTN_EXT`，兩者都精確 2×。

### 四、結論：每個 kernel 的平均 µs **算不出來**

不是精度不足，是**兩支儀器量的對象不同**：

* 普查的 `kernels` 有**真實 kernel 數**但**沒有時間**；`enc_launch` 有**呼叫數**但不是 node 數。
* `wcntw` 有**時間**但按 **node 數**分一個共同的池（§22-old 觀察：`nd=39` 的 DIV/SUM_ROWS/CLAMP
  三者 `wcntw` 都是 **95.01 ms**，逐位元相同），而 map0 這種巢狀 kernel **沒有自己的 node**。

⇒ 把兩邊相除，得到的是「某個 op 的分帳 ÷ 某個不是它的 kernel 數」，不是任何東西的價格。
⇒ 想算「純索引 kernel 吃多少」，**這兩支儀器在定義上就不可能給出答案**，補再多樣本也一樣。

### 五、map0 到底吃掉多少？——三個界（都不是量測）

**decode 步：精確的 0 ms。** 它不執行（`ne21 >= 32` 的閘，§21 §二），400 個 slice 裡
`kernel_mul_mv_id_*` 一次都沒出現。對 25 t/s 那一軸，這一項沒有成本可省。

**prefill 步：三個界**，依可信度排序：

1. **上界（分帳）**：`MUL_MAT_ID` 整個 op 在 prefill step 的預算是 **285.02 ms / 6842.81 ms = 4.2%**
   （`nd=117`、`cntw=184.66`、`ub=3939.39`），而它要付 **312 個 kernel**（每呼叫 2 個）。
   map0 只是其中一半，且是純索引工作 ⇒ **≤2.1%**。對照 `FLASH_ATTN_EXT`：`wcntw=289.77`（4.2%），
   而它的 `ub` **等於** `wcntw`（289.77）——同一種「巢狀 kernel 讓分帳失真」的痕跡。
2. **工作模型**（估算，非量測）：map0 是**一個 threadgroup、256 條 thread**，外迴圈
   `ne21/ntg = 2048/256 = 8` 塊，每塊內迴圈 `ntg=256` 個 token × 展開的 `ne20=8`
   ⇒ 每條 thread 約 **16K 次 compare+add**，外加每塊 2 次 threadgroup barrier（16 次）。
   在 M4 GPU clock 上是**數十 µs** 量級 ⇒ 對 6842 ms 的 step 是 **≤0.5%**。
3. **dispatch 下限（沿用 09-20 的單位價）**：0.0736%/dispatch ≈ **63–70 µs** 是「臨界路徑上多一個
   dispatch」的價格。map0 這個 dispatch 的**位置成本**就是這個量級，與運算量無關。

⇒ 三個界一致：**map0 是「幾十 µs 級」且只存在於 prefill**。與 §21 的判決相同，但現在有數字。

### 六、順手量到的兩個普查輸出限制（都已驗證，且都不影響上面的結論）

1. **逐 op 表每 slice 上限 14 列**，排序為 `kernels − enc` 遞減（巢狀優先）、再按 `kernels`。
   400 個 slice 裡有 **39 個印滿 14 列**。截斷的影響可以**精確證明為零**：
   表內 `enc` 總和 **2437** vs 表頭 `enc_launch` 總和 **2476**，缺口 **39**；
   表內 `kernels` 總和 **2606** vs `kernels` 總和 **2645**，缺口**也是 39**。
   每一列都有 `kernels ≥ enc`，兩個缺口相等 ⇒ **每一列被截掉的 op 都滿足 `kernels == enc`（k=1）**
   ⇒ **巢狀優先的排序保證 k=2 的那兩列永遠不會被截掉**，§三之二的 k 表因此不受上限影響。
2. **`slice=` 編號修好之前的錯**（§20 已記）：修後 400 個 slice 編號 1..400 全唯一，
   身分式 `kernels == enc_launch + nested + direct` **400/400 成立**（`nested=169`、`direct=0`）。

### 七、可引用性

* **可引用**：§二 的 1.047（當作「兩個總數剛好接近」，**不可**當作對齊證據）；
  §三 的逐 op 表（13 個 op 精確 4/3 ＋ 融合族偏離方向一致）；§三之二 的 k 表（23 個 op 只有 2 個 k=2）；
  §六 的截斷證明；`MUL_MAT_ID` 佔 prefill step **4.2%**。
* **不可引用**：本節的 **t/s**（`--no-thermal-gate`、attribution `thermal`、worst HEAVY；儀器檢查非量測）；
  以及**任何「每 kernel µs」的數字**——§四 說明了它不存在。
* **未量到**：decode 步的 `kernels`（普查窗口到不了 decode，§21 §七 未解）。

### 八、要真的替巢狀 kernel 定價，缺的是什麼

缺**逐 dispatch 的 GPU 時間**。現有兩個來源的粒度都不夠：buffer 級 `GPUStartTime/GPUEndTime`
（§19 的 `gap` 用的）是 **command buffer / segment** 級；`wcntw` 是 **node** 級。

本節原本在此建議「讓普查在 `nested == true` 的 tick 上對 command buffer 取一個
`MTLCounterSampleBuffer` 樣本」。**那條路在 §23 被實測證明是關著的**——
`supportsCounterSampling(AtDispatchBoundary)` 回 0，而且就算退到 `AtStageBoundary`（唯一回 1 的點），
在那個 encoder 上呼叫 `sampleCountersInBuffer:` 會**直接 abort 行程**。
所以這一節的「最小步」是錯的，map0 的最終答案就是 §五 的三個界。

產物：`Backup/stepbudget_2026-09-29/kernprice.{json,stderr.log,stdout.log}`
（同場含 `CGC-DISPATCH2:` 400 slice 與 `CGC-GPUOPS` 7 個 step）。

---

## 23. 逐 dispatch GPU 時間：這條路在 Apple M4 上是**關的**——量到了，不是猜的（2026-09-29）

§22 §八 把「逐 dispatch GPU 時間」指為巢狀 kernel 定價的唯一缺口，並建議用
`MTLCounterSampleBuffer` 在 `nested` 的 tick 上取樣。本節**先驗這條路是否存在**，再決定要不要寫那
~200 行。**答案是關的，而且關了兩層**；下面是證據、方法、以及還剩什麼。

### 一、證據（`scripts/check/metal_counter_probe.m`，rc=1）

裝置：Apple M4 / `applegpu_g16g` / macOS 15.2 SDK / 統一記憶體。

```
PROBE: supportsCounterSampling(AtStageBoundary)=1
PROBE: supportsCounterSampling(AtDrawBoundary)=0
PROBE: supportsCounterSampling(AtDispatchBoundary)=0
PROBE: supportsCounterSampling(AtTileDispatchBoundary)=0
PROBE: supportsCounterSampling(AtBlitBoundary)=0
PROBE: counterSets=1
PROBE:   set=timestamp counters=1
PROBE: childOutput| -[AGXG16GFamilyComputeContext sampleCountersInBuffer:atSampleIndex:withBarrier:]:996:
        failed assertion `MTLComputeCommandEncoder:sampleCountersInBuffer:atSampleIndex:withBarrier
        not supported on this device'
PROBE: childExit=34304
PROBE: flag_dispatchBoundary=0 child_attempt_succeeded=0
PROBE: RESULT=UNAVAILABLE per-dispatch GPU time cannot be sampled on this device; the flag reads 0 and the call aborts
```

三場獨立執行一致（`34304` = 134<<8 = SIGABRT）。**兩層**是重點：

1. **旗標層**：`AtDispatchBoundary = 0`。逐 dispatch 取樣不支援。
2. **旗標會騙人**：唯一回 `1` 的是 `AtStageBoundary`，而照著那個 `1` 去呼叫
   `[computeEncoder sampleCountersInBuffer:…]` **不是回一個 error，是直接 abort 行程**
   （`MTLComputeCommandEncoder:… not supported on this device`）。
   ⇒ **能力旗標與可呼叫 API 在這台裝置上不一致；探針必須「試打」，不能讀旗標。**
   這是本節最可攜的一條教訓，與 §18 那個「`step=1` 是 prefill」的引用陷阱同類。

### 二、方法：致命的斷言要用**子行程**試

`failed assertion` 是 `abort()`，不是 `NSException`，**在同一個行程裡接不住**。
所以探針用 `popen` 把自己當子行程重跑（`--try-stage`）：**子行程的死就是資料**，父行程活著把整份報告印完。
沒有這個模式，第一版探針在三種結果裡只印得出「旗標」那一半，而錯的那一半正是要緊的那一半。

### 三、順手量到時鐘的真相（`SELFCHECK clockBase`）

同一場執行：

```
PROBE: SELFCHECK clockBase gpuStart_ns=25968118667750 sampleTimestamps(cpu=25968118196208 gpu=25968118196208)
PROBE: SELFCHECK clockBase gpuStartMinusSampleTsCpuMs=0.472   (小 => 同一個基準)
PROBE: SELFCHECK clockBase gpuStartMinusMachAbsoluteMs=25344883.821 machAbsVsMachContMs=0.000 (大 => 不同基準)
```

三件事，都與直覺相反，且都會影響既有儀器：

1. `GPUStartTime` 的單位是**秒**，×1e9 後與 `sampleTimestamps` 同一尺度、**同一基準**（差 0.47 ms）。
2. 那個基準**不是** CPU 的 `mach_absolute_time`（差 **25 344 884 ms ≈ 7.0 h**）。第一版檢查用
   `mach_absolute_time` 比對、差了 7 小時，才發現這件事——而這台機器的 `mach_absolute_time` 與
   `mach_continuous_time` 讀數**完全相同**（差 0.000 ms），所以「用 continuous 就對了」這個猜測也是錯的。
3. `sampleTimestamps` 在這台裝置上回傳的 `cpu` 與 `gpu` **一字不差**（25968118196208 = 25968118196208），
   ⇒ 它**不能**用來橋接兩個時鐘。

⇒ **對既有儀器的後果**：§19 的 `gap`/`union` 是 GPU 時戳的**差值**，從來不需要共同時鐘，所以它們
**不受影響**；但想把它們放上 CPU 的時間軸（§19 說「視窗裡有一大塊 CPU 工作沒有儀器」正是這個需求）
**這條便宜的路也是關的**。

### 四、還剩什麼（都不是「逐 dispatch」）

1. **command-buffer 級（現成、免費）**：`GPUStartTime/GPUEndTime`。拿來量**整個 `MUL_MAT_ID`
   encode（map0+mm）**是可行的——只要把那一段切進自己的 command buffer。
   代價是真的：切分本身改變提交結構，而 §17（`CGC_MMV_FUSE` 砍 2.9 dispatch/layer → **+3.5% 更慢**）
   與 §19（`CGC_SUBMIT_AHEAD` 讓 step −22% 但輸出報廢）都示範過這類改動會擾動被測系統。
   而且 09-18 已量到 command buffer 在 ~64 個 in-flight 就被 Metal 節流（n_cb=127 直接 deadlock），
   所以「每個巢狀 dispatch 一個 buffer」不是選項。
2. **重播微基準**：把目標 dispatch 的 pipeline / grid / 真實緩衝區記下來，在**自己一個** command buffer
   裡重跑 N 次取平均。得到的是那個 kernel 的**內稟** GPU 時間（真實形狀、真實資料），
   **不是在位成本**（不含排隊與競爭）。這是唯一能給「map0 幾 µs」一個數字的做法，但要誠實標成微基準。
3. **維持 §22 §五 的三個界**。

### 五、可引用性

* **可引用**：§一 的兩個否定結論與 `childExit=34304`；§二 的方法；§三 的三個時鐘事實。
* **不可引用**：本節沒有任何 t/s；也沒有任何「map0 幾 µs」——本節只證明**那個數字目前取不到**。
* **適用範圍**：硬體/OS 特定（Apple M4、macOS 15.2 SDK）。換裝置要重跑探針，rc=0 才成立。

### 六、與 §22 的關係

§22 §三之二「`MUL_MAT_ID` 與 `FLASH_ATTN_EXT` 精確 2×」**不變**（那是普查自己的計數，與時鐘無關）。
§22 §五 的三個界**不變**，且現在知道它們是**短期內的最終答案**，不是等待更好儀器的過渡。
§22 §八 的建議**作廢**，已在該節原地更正。

產物：`Backup/stepbudget_2026-09-29/counter_probe.{stdout.log,stderr.log,m.txt}`
（`m.txt` 是探針本體在跑的那一刻的副本，供日後比對）。

### 七、收束（operator 裁決，2026-09-29）

**map0 收在 §22 §五 的三個界，這一條線不再往儀器方向走，力氣回到 decode 的 45.45 ms 缺口。**
理由：map0 在 decode 上是精確的 0（§21 §二），而 decode 才是 25 t/s 那一軸；
為了 prefill 上 ≤2.1% 的一格去開一個已被硬體關掉的儀器，成本與收益不成比例。
**本節的否定結果照留**——它不是「運氣不好」，是一條永久的邊界（換裝置要重跑探針）。

---

## 24. 回到 decode 的 45.45 ms：`gap` 不是量具自己造成的（0.07%），而是一個**每段固定 0.356 ms 的邊界稅**（2026-09-29）

按 §23 §七 的裁決回到 25 t/s 那一軸。§16 已經用算術把話說死：**只靠計算側到不了 25**
（權重搬運打到實測上限 108 GB/s ⇒ 44.2 ms = 22.6 t/s），因為「非計算」的 **~33 ms**
（`gap` 14.0 ＋ 圖外 11.6 ＋ 填充/提交 7.8）不會跟著動。所以 45.45 ms 的缺口裡，
**有 33.4 ms 不在 kernel 上**。本節先把這 33.4 ms 裡最大的一格（`gap`）拆開。

### 一、先驗一個會讓 §16/§19 整段作廢的可能：量具自己造成 `gap`？

§16 §五 自己記著「探針 B 的量具本身 ~14% 成本」；而兩個回讀呼叫（`cgc_gpu_take`、
`cgc_gpu_take_cb`）**正好跑在 `gap` 要量的那個窗裡**（segment i 已完成、segment i+1 還沒提交）。
若 `gap` 主要是量具，那 §16 的 14.0 與 §19 的整段歸因就都是觀測假象。
⇒ 這必須先量，於是在 `hook_seg` 裡對兩個呼叫各自加時戳，並在 `CGC-GPUTIME` 加三欄
（`take`／`take_cb`／`instr_total`）＋一個自我檢查 `instr_of_gap`。

**結果：假設被否證。** 兩場（新 build、steady 96 步、排除 step 4 的冷塊）：

| 場次 | `wait` | `union` | **`gap`** | `take` | `take_cb` | **量具/`gap`** |
|---|---:|---:|---:|---:|---:|---:|
| run1 | 64.83 | 66.64 | **14.545** | 0.010 | 0.00 | **0.07%** |
| run2 | 67.96 | 71.79 | **14.245** | 0.010 | 0.00 | **0.07%** |

⇒ `cgc_gpu_take` 的回讀是 **0.010 ms/step ＝ 0.07% 的 `gap`**。§16/§19 的 `gap` **不是**量具造成的。
⇒ **但 `take_cb` 這一格本節沒有量到**：它由 `CGC_GPU_NODES` 開啟（`cgc_gpu_take_cb != nullptr`），
而這兩場只開了 `CGC_GPU_TIMING`，所以 `take_cb` 恆為 0.00 是**構造出來的**，不是「便宜」。
§16 說的「~14%」是**探針 B 整體**（`CGC_GPU_NODES` ＋ trace ＋ 逐節點歸屬）的口徑，
與這裡「**一次回讀** 0.07%」不是同一句話，兩者不衝突但也**不可互相引用**。
⇒ 未結案的小項：`cgc_gpu_take_cb` 自己的成本（要一場開 `CGC_GPU_NODES` 的 run 才量得到）。

順帶：**§16 的每步帳在新 build 上複現**（`wait` 66.12→64.83/67.96、
`union` 69.42→66.64/71.79、`gap` 14.04→14.55/14.25），並複現了 §19 的
`gap/(cb+submit)` 比值（本節 join 到同一步的 DECPROF：`cb` 中位 4.69、`submit` 3.50 ⇒ **1.74**，
落在 §19 的 1.36–2.79 內）。**這條帳是可重複的，不是單場僥倖。**

### 二、`gap` 的真身：**每段一個固定稅，與該段有多少工作無關**

* `gap` 中位 **14.245 ms/step**，每步 **40 段** ⇒ **每段 0.356 ms**（run1：14.545/40 = 0.364）。
* §19 的獨立證據指向同一件事：**段數不變時 `gap` 對 buffer 數不敏感**（n_cb 1/8/16 ⇒
  gap p50 14.83/13.89/14.70），而 **`CGC_SEG_BATCH=1` 把 41 段併成 1 段之後 `gap` 整個消失**。
* ⇒ **`gap` ∝ 段數，不是 ∝ 段內工作量、也不是 ∝ dispatch 數**（§19 的 MMV_FUSE 反號 +0.59 已排除後者）。

### 三、同一場量到的第二個東西：CPU 在整步都在**空轉**

新增的 `poll_iters` 計數（`hook_seg` 裡 `while (cgc_done(...) < target)` 的圈數）：

| 量 | run1 | run2 |
|---|---:|---:|
| polls / 段 | **14 858** | **15 933** |
| polls / 步 | 594 312 | 637 330 |
| `wait` / 段 | 1.62 ms | 1.699 ms |
| ⇒ 每次輪詢 | **~109 ns** | **~107 ns** |

⇒ 主執行緒在**整步**（~68 ms）裡以一顆核 100% 的密度打 `sched_yield()` 輪詢，
每一步 **~60 萬次**。這是一個在此之前**沒有數字**的量（§19 只知道「窗裡有一大塊 CPU 工作沒有儀器」）。

### 四、下一個實驗，決策規則寫在跑之前

**假設 H-spin**：主執行緒的滿載輪詢與 Metal 的驅動執行緒／填充 worker 爭 CPU，
於是 GPU 在段邊界**等不到下一批被送上去**，形成那 0.356 ms 的固定稅。

* **做法**：**只改等待策略**（前 N 圈純 spin 後改用退避輪詢，或 `nanosleep(0)` 級的讓出），
  **不動任何資料流**。這一點是關鍵：它**不改變正確性**（不像 §19 tier 2 的 submit-ahead 會產生
  garbage），所以它是一個**可以留用的候選**，不只是探針。
* **決策規則（先寫）**：同一臂／cell／charter，只切等待策略，比 `gap`**每段**值（step 數不變）：
  * **每段 `gap` 降 ≥30%** ⇒ H-spin 成立 ⇒ 這是合法的最佳化，進 A/B 速度臂；
  * **每段 `gap` 不動（±10% 內）** ⇒ 那是 **Metal 自己的批次啟動延遲**，
    唯一的槓桿是**減少段數**——而段數被每層 top-k hook 的資料依賴釘住（§19），
    所以答案會是「這 14 ms 拿不掉」，並應把力氣轉到圖外那 11.6 ms。
* 這一場必須是**乾淨窗**（idle ≥150 s）且**逐臂重過閘**（§19 的協定），否則又是 VOID。

### 五、順手記下的最大槓桿，以及它真正的性質

decode 軸上量到過的**最大單一槓桿仍是 §19 tier 2**：`CGC_SUBMIT_AHEAD=1` ⇒ step 75.91 → **58.97 ms
（−22.3%）＝ −16.9 ms**。而它的代價不是「慢」，是**錯**：

```c
// submit-ahead 把 segment[i+1] 在 segment[i] 的 top-k hook「寫完 remap leaf」之前就 commit，
// 而 segment[i+1] 透過 mul_mat_id 消費那個 leaf ⇒ 讀到 stale leaf ⇒ 整張圖壞掉。
if (submit_ahead && i + 1 < n_segs) { ec = submit_seg(i + 1); }   // 先提交
if (i < n_as_found)                 { if (!hook_seg(i)) break; }  // 後寫 leaf
```
（`ggml-backend.cpp` 的 `CGC_SUBMIT_AHEAD` 分支，註解原文見 `hook_seg` 上方那條 raciness fix。）

⇒ 所以 45.45 ms 裡最大的一塊**不是缺儀器，是缺一個正確的順序**。若 top-k remap 能在 GPU 上算
（或讓 CPU 不必回讀即可寫 leaf），submit-ahead 的 −16.9 ms 就會變成**合法**而非 racy。
這是本軸上報酬最高的一條路，且它與 §一~§四 的量測互不衝突。

### 六、可引用性

* **可引用**：§一 的否證（回讀 0.010 ms/step、0.07%）；§二 的每段 0.356/0.364 ms 與它與段數成正比；
  §三 的輪詢計數（14 858／15 933 每段、~107 ns 每次）；§一 對 §16/§19 的跨 build 複現。
* **不可引用**：本節所有 **t/s**（兩場 attribution 皆 `swap`、`--no-thermal-gate`、worst MODERATE ⇒
  **儀器檢查不是量測**）；`take_cb=0.00` 也不可讀成「那個量具免費」（見 §一 的說明）。
* **未量到**：`cgc_gpu_take_cb` 的成本；H-spin 的真偽（§四 的實驗還沒跑）。

產物：`Backup/stepbudget_2026-09-29/gapsplit{,2}.{json,stdout.log,stderr.log}`
（`gapsplit` 是標籤修正前的場次，第三欄標成 `cb_rest`；`gapsplit2` 是修正後，
標籤為 `instr_total`。兩場的 `gap` 都落在 14.2–14.5，故本文兩者並用。）


---

## 25. decode 25 t/s 能不能拆成子目標？——能，5 塊；以及 charter 面的現況快照（2026-09-29）

### 一、拆解：4 個 ms 區塊 ＋ 1 個「使能項」

目標 25 t/s ⟺ **40.0 ms/token**；現況 11.703 t/s ⟺ **85.45 ms**（§15 的乾淨臂）⇒ 缺口 **45.45 ms**。
按 §16 的每步帳（同一批 48 個 `ntok=1` 步）拆成互不重疊的五塊，每塊附**已量到**的槓桿：

| 子目標 | 區塊 | 現在 | 已量到的槓桿 | 單獨命中之後 |
|---|---|---:|---|---|
| **SG-KERNEL** | GPU 淨忙 `union − gap` | 55.4 | 頻寬上限 108 GB/s ⇒ 10.8；10× 大 dispatch ⇒ 19.5 | 44.2–45.9 ms ＝ **21.8–22.6 t/s**（**到不了 25**）|
| **SG-GAP** | 段間空轉 | 14.0 | §24：**每段邊界稅 0.356 ms × 40 段**；§19 tier 2 的 −16.9（racy）| 71 ms 級（若只動這塊）|
| **SG-OFFGRAPH** | 圖外 | 11.6 | **無**（目前只是殘差）| 未知 |
| **SG-FILLSUB** | 填充＋提交＋輪詢 | 7.8 | §24：每段 15 933 次 `sched_yield`（~107 ns/次、每步 ~60 萬次）| 太小，須與 SG-GAP 共用機制 |
| **SG-ORDER** | 正確性使能 | 0 | — | 0 ms，但它是 SG-GAP 那 −16.9 的**前提** |

**決定性的算術（§16 §四）**：只動 kernel ⇒ 22.6 t/s；只動 dispatch ⇒ 21.8 t/s；
**四塊同時命中**（19.5 ＋ 7.0 ＋ 5.8 ＋ 3.9）＝ **36.2 ms ＝ 27.6 t/s**。
⇒ **答案是「可以拆，而且必須同時命中至少四塊」**；沒有任何一塊單獨夠大。這是本拆解唯一真正的斷言。

### 二、這張拆解已經立卡（gate PASS）

`scripts/check/charters/e-decode25-blocks-2026-09-29.yaml`
（owner `shared`；`targets: decode 25 / prefill 250`；5 個 `subgoals` 各帶 `expect`／`how`；
`baseline.source` 指向 §15 那一場 `Backup/mtp_off_clean/res_2026-09-29c.json`（attribution `none`、NOMINAL）。
用 harness 真正的 `_charter_gate()` 驗過：**PASS**，無任何缺欄。）

⚠ **刻意沒有跑 `experiment_sync.py init`**：那會寫 `docs/mindmap/mindmap.json`（產生節點）。
那份檔目前不是本線在改（md5 全程未動），要動它應該由持有 mindmap 的那條線決定。

### 三、charter 面現況快照（唯讀掃描，2026-09-29）

* **21 張卡**（20 張既有 ＋ 本節新立的 1 張；不含 `_TEMPLATE`）。全部都通過 fail-closed 閘
  （缺欄／`baseline.source` 不存在／`question` 用「測一下」開頭都會被拒跑）。
* **其中 8 張帶機器可讀 `targets`**（`exp-c-eff-bandwidth` 16、`exp-c-read-issue` 14、
  `exp-s2-overlap` 18.7、`exp-m-draft-cost` 19.25、`exp-caliber-calibration` 25、
  `exp-churn-delivery-630` 20、`exp-prodnew-decode20-g4miss` 20、`exp-singlesubmit-fillahead` 20）。
  **這 8 張的 `best` 全部是 `null`、`target_gap.current` 全部是 `null`**
  ⇒ mindmap 上**沒有任何一張卡有登記成績**，也就是說「進度%」這一欄整體是空的。
* 子任務狀態：多數卡是 `st-charter: done` ＋ `st-run: doing` ＋ 驗收未跑；
  **只有 4 張的 `st-run` 是 `done`**（`exp-s-retro`、`exp-churn-delivery-630`、
  `exp-prodnew-decode20-g4miss`、`exp-singlesubmit-fillahead`），而它們的驗收欄仍是 `todo`。
* 稽核面（唯讀）：`claim_instrument_check` **3 QUOTABLE / 1 WARN / 10 VOID**；
  `arm_ledger_check` **17 列 PASS**；`mindmap_void_check` **1/55**。
  ⇒ 卡很多、閘很嚴，但**能引用的成果很少**——瓶頸在「跑得出來且過閘的場次」，不在立卡。

### 四、與 `m-decode25` 的關係（必須講清楚的一處張力）

`m-decode25` 節點（2026-09-25 生成）的立場是 **判死**：
「交付 12.57 ＝ 79.6 ms ⇒ 要砍 49.6%；**IO 全關也只 ~+5%**（保守 +9%）⇒ 判死」。
§16／§19／§24 用 11.703（85.45 ms）這一場重拆，得到的是**不同形狀**的結論：

* 「IO 全關只 +5%」與 §16「填充只佔 4.36 ms（5%）」**互相印證**，不是矛盾；
* 但判死的**外推方式**（以 IO 為主要變數）與 §16 的結論不同：缺口的主項是
  **「每個 dispatch 太小」＋「每段邊界稅」**，兩者都不是 IO；
* 而 §19／§24 量到 decode 軸上**最大的單一槓桿 −16.9 ms**（submit-ahead），
  它的阻礙是**正確性**（stale remap leaf）而不是物理。

⇒ **本線不動 mindmap**（md5 全程未動）。但這是一條需要 operator 裁決的張力：
`m-decode25` 的判死是 09-25 的；09-29 的拆解把它變成「5 塊、須同時命中 4 塊」的可判定問題。
在裁決之前，本節的拆解**不主張推翻判死**，只主張判死所依據的那條外推（IO 為主項）**與後續實測不符**。

### 五、最大的兩個空白（拆解自己也看得見）

1. **SG-OFFGRAPH 沒有儀器、沒有業主**：11.6 ms／13.6% 的每步時間目前只是
   「end-to-end − DECPROF total」的差值，而它**比 §23 那條被硬體關掉的逐 dispatch 路更便宜可修**
   （純 CPU 側時戳，不需要任何 GPU 取樣）。
2. **SG-ORDER 沒有業主**：−16.9 ms 是 decode 軸上量到過最大的單一槓桿，
   但它需要的是**改順序**（把 top-k remap 移到 GPU 或免回讀），不是調參或補儀器。

---

## 26. 把 top-k remap 移到 GPU：**已經有實作**（`CGC_SLOT_TABLE_GPU=1`），而 −16.9 ms 能不能保留是三條判據（2026-09-29）

§24 §五 指出 decode 軸上最大的單一槓桿（submit-ahead，−16.9 ms）卡在**正確性**而不是物理。
本節把它的依賴讀清楚，結果是：**「移到 GPU」不是待評估的設計，而是已經在版控裡的旋鈕**；
真正未回答的是「它是否足以讓 submit-ahead 合法」，而那個問題**從來沒有人把兩個旋鈕一起開過**。

### 一、依賴到底是什麼（讀碼，不是讀註解）

依賴**不是 ids**。ids 是 GPU 算出來的（`ffn_moe_argsort-` / top-k）。真正被 CPU 寫、
被 segment[i+1] 讀的是 **expert → slot 的對照**——而它取決於**駐留狀態**（pool 只有 ~143 槽），
不是取決於 routing。兩處原文：

* `ggml-backend.cpp`（`CGC_SUBMIT_AHEAD` 上方）：「per segment, first WAIT for segment[i] to fully
  complete …, then fire the top-k hook which **writes the remap leaf**, and only THEN submit
  segment[i+1] (which **consumes that remap leaf via `mul_mat_id`**)」；
  「modifying a buffer of an in-flight command buffer is UB」。
* `llama-context.cpp`（B-scheme preflight）：「With `CGC_SLOT_TABLE_GPU=1` the graph consumes
  `get_rows(slot_table, argsort_ids)` on the GPU, so **the host only needs to publish the
  per-layer expert→slot table ONCE before dispatch (it does not depend on routing)**」。

邊界是切在 `ffn_moe_argsort-` 節點上 ⇒ **remap 的消費者剛好在 segment[i+1] 的開頭**。
這一件事就殺掉兩個看起來很自然的做法：

* **分割 segment[i+1]**（把不依賴 remap 的部分先提交）：**沒有東西可以先提交**——開頭第一個就是消費者。
* **double-buffer leaf**：只解掉 UB（正在飛的 buffer 被改），解不掉**順序**（leaf 必須先於消費者存在）。
  它讓 race 更容易贏，不是讓 race 消失。

### 二、已經存在的實作：`CGC_SLOT_TABLE_GPU=1`

`scripts/run_server.sh` 自己的說明（allowlist 區塊）：

> `CGC_SLOT_TABLE_GPU=1` replaces the host-written remap leaf with a GPU-side gather: the eval hook
> publishes the per-layer expert→slot table (I32 [1, n_expert]) and the graph computes
> `slots = get_rows(table, selected_experts)`, **which is byte-for-byte what the hook used to write**.

⇒ 語意上是**逐位元等價**的替代（不是近似），所以它**不是**探針，是**可以留用的候選**。
⇒ 但注意它移掉的是 **leaf 的「寫」**，而**table 的「發佈」目前仍在 eval hook 裡、逐層做**
（同一段註解：「the eval hook publishes the per-layer … table」）。
「一次就夠」那個版本屬於 `CGC_B_SCHEME` 的單段提交路徑，不是預設的分段路徑。
**⇒ 所以 `SLOT_TABLE_GPU=1` 單獨開，未必就讓 submit-ahead 合法；這正是要量的東西。**

### 三、這兩個旋鈕從來沒有一起開過（語料掃描）

| 量 | 值 |
|---|---:|
| `Backup/` 裡提到 `CGC_SUBMIT_AHEAD` 的檔 | 35（其中 19 個是真正的臂規格 `CGC_SUBMIT_AHEAD=1`）|
| 提到 `CGC_SLOT_TABLE_GPU` 的檔 | 231 |
| **同時提到兩者** | **1** —— 而且是 `Backup/g123_campaign_20260919.sh`（**企劃腳本，不是跑過的結果**）|

⇒ **「GPU remap ＋ submit-ahead」這個組合沒有任何量測**。而 `run_server.sh` 自己把
`CGC_SUBMIT_AHEAD` 定位成**整個重疊家族的零改碼上界探針**，並寫明了判準：

> If decoding does not speed up here, **no double-buffered remap design can help** and the segmented
> dispatch is not the bottleneck.

§19 量到它**確實變快**（75.91 → 58.97 ms，−22.3%）⇒ **這個家族沒有被否證，門是開的**。

### 四、實驗階梯（前兩階零改碼）

| 階 | 臂 | 回答什麼 |
|---|---|---|
| **E1** | `prod-new:CGC_SLOT_TABLE_GPU=1`（控制：`prod-new`）| leaf 的寫入是否在臨界路徑上？看 `cb` 與 §24 的 gap/段是否下降；且是否仍 bit-identical |
| **E2** | `prod-new:CGC_SLOT_TABLE_GPU=1;CGC_SUBMIT_AHEAD=1` | **本節的核心**：leaf 一旦由 GPU 算，submit-ahead 是否（a）不再 racy、（b）仍保有 −16.9 |
| **E3** | 把 table 的**發佈凍結到步層**（A 前置）＋ 逐層重算（G4）| 若 E2 仍 racy，這是唯一的路：`exp-prodnew-decode20-g4miss`、`e-s1-g3-zeroslot`、`CGC_ZERO_SLOT` |

兩個旋鈕都已列在 allowlist（`run_server.sh:1990` / `:2004`），且**都不在 `prod-new` 的 base env**
（實查 `resolve('prod-new')`：`CGC_B_SCHEME=1` 有、這兩個沒有）⇒ 用 `--arm` 直接加即可，
不需要 `!` 宣告。E1／E2 是速度實驗 ⇒ **需要 charter**（本節的新卡 `e-decode25-blocks-2026-09-29.yaml`
的 `sg-order-legal` 就是它的業主）＋ 乾淨窗。

### 五、判據：−16.9 ms 可保留 ⟺ **R1 ∧ R2 ∧ R3**

1. **R1（正確性）**：E2 的輸出 **bit-identical**——M1 9/9，**不是「通常會贏 race」**。
   §24 記的原始症狀正是「暖長 prompt 剛好贏、冷短 prompt 輸」⇒ 「大部分時候對」不算通過。
2. **R2（重疊還在）**：E2 相對**同一 build 的 `SLOT_TABLE_GPU=1` 控制臂**，step 增益 **≥10 ms**。
   （用控制臂而不是 baseline，因為我們要的是「submit-ahead 還值多少」，不是「整體快多少」。）
3. **R3（機制歸屬）**：E1 必須顯示 `cb` **或** gap/段在 `SLOT_TABLE_GPU=1` 下**已經下降**。
   若 R3 不成立，那 R1/R2 的改善**不是來自「leaf 寫入離開臨界路徑」**，
   §25 的 SG-GAP 那一格就要重新定價，不能記在這個機制上。

**否證與處置（兩條，都是可判定的）**：

* **E2 仍 non-deterministic** ⇒ 依賴是**逐層的 table 發佈**，不是 leaf 的寫入。
  ⇒ −16.9 只能靠 **E3**，判據改為「**E3 的 G4 重算成本 < 16.9 ms**」（否則零和）。
* **E2 bit-identical 但增益 ≤3 ms** ⇒ `run_server.sh` 那句判準成立：
  **remap 的順序不是那 −16.9 的來源**，整個 overlap 家族要重新定價
  （同時 §19 tier 2 的 −22.3% 也要重新歸因——它將變成「移掉了不在 remap 上的某個窗口」）。

### 六、可引用性

* **可引用**：§一 的依賴（附檔名與原文）、§二 的 byte-for-byte 等價（`run_server.sh` 原文）、
  §三 的語料掃描計數（35／231／1）、兩個旋鈕都不在 base env（`resolve` 實查）。
* **不可引用**：本節**沒有任何新數字**——它是評估，不是量測。E1/E2 尚未跑。
  §三 的「19 個真正的臂規格」是文字比對的計數，不是跑過的場次數（其中有文件重複引用）。
* **不成立的做法（本節已排除，附理由）**：分割 segment[i+1]（消費者在段首）、
  double-buffer leaf（解 UB 不解順序）、把 remap「整個省掉」（remap 是 expert cache 的內在產物：
  pool 只駐留 ~143 槽，id→slot 的映射必須存在）。

---

## 27. 圖外那 11.6 ms 拆開了：**8.86 ms 是 `llama_synchronize`（80%）**、2.19 是 decode 內非 segment、**取樣是 0**（2026-09-29）

§25 §五 把 SG-OFFGRAPH（11.6 ms、13.6%）列為拆解裡**唯一沒有儀器**的一塊：它一直只是
`end-to-end − DECPROF total` 的殘差。本節把它量開，結果**推翻了原本的三分法**——
其中一項在這支 harness 上不存在，另一項早就被量到且只有 0.1 ms。

### 一、先修正前提：這支 harness **不取樣**

`tools/llama-bench/llama-bench.cpp` 的 `test_gen()`（交付口徑用的那一支）：

```cpp
for (int i = 0; i < n_gen; i++) {
    int res = llama_decode(ctx, llama_batch_get_one(&token, 1));
    ...
    llama_synchronize(ctx);            // ← 每 token 一次全步同步
    token = std::rand() % n_vocab;     // ← 不是取樣：從頭到尾沒讀過 logits
}
```

⇒ llama-bench 上**沒有「取樣」這一項**（同一個檔自己的註解也寫著「it never looks at logits」）。
⇒ 剩下的候選只有兩個：**`llama_decode` 裡沒有被 DECPROF 覆蓋的部分**，以及**每 token 的
`llama_synchronize`**（它不在 DECPROF 的 segment 帳裡：最後一段之後沒有 hook）。

### 二、量測（新儀器 `CGC-BENCH-PHASE`）

在 `test_gen` 的迴圈裡對三個相位加時戳（`decode`／`sync`／`rand`），迴圈結束後才印
（不擾動被計時區間），並附一個**自我檢查**：三者相加必須等於 harness 自己的 loop 計時。

```
CGC-BENCH-PHASE: n_gen=64 loop=85.083 decode=76.244 sync=8.858 rand=0.000 accounted=85.082 unaccounted=0.000 ms/token
```

7 場（含 reps）中位：

| 相位 | ms/token | 佔 85.08 | 誰量的 |
|---|---:|---:|---|
| **`llama_synchronize`（步尾全同步）** | **8.858** | **10.4%** | `CGC-BENCH-PHASE`（新）|
| decode 內、非 segment 的部分 | **2.194** | 2.6% | `decode` − `CGC-DECPROF total` 中位 |
| **`CGC-DECPROF total`**（40 段的 wait+cb+submit）| **74.050** | 87.0% | `CGC-DECPROF`（既有）|
| 圖組裝（build+alloc+inputs） | ~0.14 | 0.16% | `CGC-PHASE`（既有，見 §五 註）|
| **取樣** | **0.000** | **0%** | 不存在（§一）|

* 加總檢查：`74.050 + 2.194 + 8.858 = 85.102` vs loop 中位 `85.083` ⇒ **差 0.019 ms**。
* 儀器自檢：7 場的 `|accounted − loop|` 最大 **0.0010 ms**、`|unaccounted|` 最大 **0.0010 ms**。
  ⇒ 這是本文件裡第一個「三個相位相加恰好等於 end-to-end」的分解，不是靠減法得到的殘差。
* 對齊：loop 85.08 ms 對上 §15 的 85.45 ms（11.703 t/s）⇒ **同一口徑，儀器沒有改變被測物**。

### 三、這改變了 SG-OFFGRAPH 的**性質**，不只是它的數字

§16 §四 與 §25 都把圖外那 11.6 ms 當成「可以減半的成本」（示範行寫 −5.8 ms）。
現在知道它**八成不是成本，是等待**：

* `llama_synchronize` 的 8.86 ms 是**每 token 一次的序列化點**——它在 `llama_decode` 回來之後
  才阻塞，等的其實是**最後一段的收尾**（最後一段後面沒有 hook，所以那一段的完成
  不落在 DECPROF 的 `wait` 裡）。
* 這種東西**不是靠「把工作變少」縮掉的，是靠「不要停下來等」隱藏的**——
  而 §26 正在處理的正是同一族的問題（SG-GAP 的 −16.9 ms 也是「停下來等」）。
* ⇒ **§25 的 SG-OFFGRAPH 應該改記為「8.86 ms 的步尾序列化 ＋ 2.19 ms 的 decode 內部非 segment」**，
  並把「減半」那個假設刪掉：它不是一個可等比例削減的成本項，
  **它的槓桿與 SG-GAP 是同一支**（重疊／不要停在段邊界）。

⇒ 若 8.86 ms 能被藏掉，step 由 85.08 → 76.24 ms ⇒ **13.1 t/s**（是 §15 那個 11.703 的 +12%），
而且**不必改任何計算**。這是 decode 軸上目前第二大的單一可回收塊（次於 §19 的 −16.9）。

### 四、一個尚未回答、但很要緊的問題（本節刻意不推論）

`llama_synchronize` 是 **llama-bench 這一支 harness** 的迴圈行為。
**產品路徑（`llama-server`）每 token 是否也做同樣的全同步，本節沒有查**。
若沒有，則交付口徑的 11.703 t/s **低估了產品**，且 SG-OFFGRAPH 這一塊在產品上不存在；
若有，它就是產品成本。**兩種情形要用不同的處置**，所以在查清楚之前，
本節的 8.86 ms **只算「harness 口徑」的數字**，不得寫成產品成本。

### 五、可引用性

* **可引用**：§二 的表與加總/自檢（`|Δ| ≤ 0.001 ms`）；loop 85.08 對 §15 85.45 的口徑一致；
  §一 的「llama-bench 不取樣」與其原文。
* **不可引用**：本節的 **t/s**（attribution 未必乾淨，且 `--no-thermal-gate`：這是儀器檢查，
  **不是速度場次**）。CGC-PHASE 的 `compute=84.533` 也**不可**與 decode=76.244 直接比：
  CGC-PHASE 的均值涵蓋 384 個 decode 步（含冷步與 warm-skip 之前），
  而 `CGC-BENCH-PHASE` 是同一個 loop 同一批被計時的步 ⇒ **只有後者可以拿來對帳**。
  這也是本節「圖組裝 ~0.14」寫成「~」的原因：它的值是 CGC-PHASE 的均值，不是同批步。
* **未查**：§四 的產品路徑同步行為。

儀器：`src/llama.cpp/tools/llama-bench/llama-bench.cpp` 的 `test_gen`（新 `CGC-BENCH-PHASE`，
由 `CGC_PHASE_TIMING` 開啟，避免新增旋鈕；已列在 `run_server.sh:2213`）。
產物：`Backup/stepbudget_2026-09-29/offgraph.{json,stdout.log,stderr.log}`。
---

## 28. E1／E2 跑了：**R1∧R2∧R3 = FAIL，−16.9 ms 不可保留**；E2 不是「會有時錯」，是**在 hook 裡 SIGSEGV**（2026-09-29）

§26 把「GPU remap ＋ submit-ahead」立案成一條可判定的階梯，判據是 **R1∧R2∧R3**。
本節把它跑完。結論先寫：**三個條件沒有一個以「可保留」的形式成立**，而且否證的方式比 §26 預期的更硬——
E2 不只是 non-deterministic，它在**第一個 decode 步**就把行程打死，堆疊落在**發佈那張表的那個 hook 裡**。

立項：`scripts/check/charters/e-decode25-blocks-2026-09-29.yaml`（`sg-order-legal` 為業主，跑前 `_charter_gate()` PASS）。

### 一、Run A（乾淨臂，`harness bench`，3 臂一場）

cell=(default)（prompt 2048 / gen 128 / depths 512 / reps 3 / warm_skip 64 / fixed_fill_seed 1 / batch 5632）、
`warm_skip_applied=True`、`memory_gate.ok=True`；build `e5d1c0f14`、tree `2823fdc76`；起跑 thermal **NOMINAL**、壓縮機 **quiet**。

| 臂 | tg t/s | ms/step | cache hit | thermal 起→worst | attribution |
|---|---:|---:|---:|---|---|
| `prod-new`（控制） | 11.683 ± 0.247 | **85.59** | 96.32% | NOMINAL→NOMINAL | **swap**（+813.5 MiB）|
| **E1** `prod-new:CGC_SLOT_TABLE_GPU=1` | 11.630 ± 0.126 | **85.99** | 96.23% | NOMINAL→MODERATE | **swap**（+242.0）|
| **E2** `+CGC_SUBMIT_AHEAD=1` | **22.048 ± 1.891** | **45.36** | **65.92%** | MODERATE→HEAVY | **both** |

> **（2026-09-29 §29 事後更正）這一列是倖存者偏差**：同一指令重跑 8 次 arm 呼叫，**8/8 rc=−11**，
> 0 存活。此處 `45.36 ms` 不是 E2 的速度，是九次裡唯一一次沒崩、且帳不平（38 280 筆無落點、hit 65.9%）的執行。

* **E1 與控制沒有差別**：+0.39 ms/步（11.63 對 11.68，兩者互在 ±1σ 內）。
* **E2 快了 2×**（−40.63 ms/步對 E1）——**但它的 cache hit 由 96.3% 崩到 65.9%**，
  而且計數器**不平**：控制 `128920 = 124177 + 4743` ✔、E1 `128920 = 124062 + 4858` ✔，
  但 E2 `128760 − 84880 − 5600 = 38280`（**38 280 筆請求沒有落點**）。
  一個「讀到未發佈的表」的臂，其請求帳本來就不該平；這一行是把 E2 的 t/s 讀成「重疊的價值」之前必須先看的東西。
* **今天沒有任何一臂是乾淨歸因**（三臂都是 `swap`／`both`；§15 那場是 `none`）⇒
  **本節的 t/s 一概 VOID，只讀計數器與二元事實**。三臂的 metal 工作集「峰值」檢查也都 REFUSE
  （池 8192＋Metal 駐留 ~12 250＋保留 1024 ≈ 21 500 vs 上限 11 453），只有起跑那道過。

### 二、Run B（同三臂＋儀器 `CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1`）——R3 的證據

`ntok=1` 的 decode 步各 48 個，取中位：

| 臂 | total | wait | **cb** | submit | union | **gap** | **gap/seg** | `poll_iters` | `instr_of_gap` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 控制（儀器） | 75.43 | 66.90 | **4.20** | 3.54 | 13.83 | **14.09** | **0.352** | 629 193 | 0% |
| E1（儀器） | 79.64 | 68.78 | **5.34** | 3.44 | 15.07 | **14.58** | **0.364** | 644 034 | 0% |
| E2（儀器） | — | — | — | — | — | — | — | — | 見 §三 |

**R3 要求的「`cb` 或 gap/段已經下降」沒有發生**：`cb` 4.20 → **5.34**（升）、gap/段 0.352 → **0.364**（升）、
union 13.83 → 15.07（升）。段數（`segs=40`）與每段稅仍然落在 §24 量到的 0.356–0.364 ms 帶內。

**一個必須標明的混淆**：控制那臂全程 NOMINAL（171/171），E1 那臂是 NOMINAL 69／MODERATE 18／HEAVY 67 ⇒
E1 的 +4.2 ms **方向可信、幅度不可信**。但 R3 問的是**有沒有下降**，而要驗的槓桿（−16.9 家族）是 ≥10 ms 的級別，
熱噪聲（~4 ms）不足以把那種下降藏起來；且乾淨側同向（11.63 對 11.68，E1 沒有變快）⇒ **R3 的否證成立**。

### 三、R1：E2 在**發佈表的 hook 裡** SIGSEGV

`m123_oracle_gate.py`：profile `prefill250`、
參考 `Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v7_20260926.jsonl`、
oracle pin `CGC_SERVER_BATCH/UBATCH=6144`、探針 `15+27 等於多少？請只輸出答案`（max_tokens=48）。

| 臂 | 可比性 | M1 | M2 | M3 | 閘門 |
|---|---|---|---|---|---|
| 控制（無 env） | **comparable**（0 diff）| **9/9** | 9/9 | 9/9 | **PASS**，答 `42` |
| E1（`CGC_SLOT_TABLE_GPU=1`） | **comparable**（0 diff）| **9/9** | 9/9 | 9/9 | **PASS**，答 `42` |
| **E2**（`+CGC_SUBMIT_AHEAD=1`） | **NOT COMPARABLE**（1 diff: `ENV.CGC_SUBMIT_AHEAD: ref=<absent> now='1'`）⇒ 以 `--allow-incomparable` 讀 | **無 dump（行程在起服務前就死）** | — | — | **FAIL** |

三件事值得單獨記：

1. **控制臂 9/9 是這一節的支點**。它證明「今天這顆 build（tree `2823fdc76`、`dirty_tracked=155`）
   仍然逐位元重現 v7 參考」⇒ E2 的 FAIL **不是**「參考過期」。
2. **E1 的可比性不是巧合**：`CGC_SLOT_TABLE_GPU` 本來就被列在 `m123_oracle_gate.py` 的 `DIAGNOSTIC_KEYS`
   （註解原文：「that IS its claim: … must produce the same id vector」），所以閘門**能表達**它要測的那個命題，
   而它這次給的答案是 PASS。**E1 是乾淨的位元等價替換，只是零收益。**
3. **E2 的 FAIL 是記憶體安全，不是「數值不同」**。日誌（`Backup/cgc_logs/llama_server_20260929_111952.log`）：

```
CGC-HOOK: ctx=... il=0 ntok=2 ids=[64 161 125 112 216 89 100 39 ...]
CGC-WARM verify n_past=1 warm=0 fast=0
CGC-PRE : il=0 st[64]=64 st[161]=-1 st[125]=125 st[112]=112 st[216]=-1 ...   ← 消費者讀到的表
CGC-POST: il=0 st[64]=64 st[161]=1  st[125]=125 st[112]=112 st[216]=3  ...   ← 這一步自己發佈的表
W CGC-MMID-ASSERT name=ffn_moe_gate-1 ne02=143 n_ids=16 | id_oob=16 (first=1003731148, total=16) | ...
CGC-PRE : il=1 st[214]=-1 st[82]=82 st[250]=-1 ...
W CGC-MMID-ASSERT name=ffn_moe_gate-2 ne02=143 n_ids=16 | id_oob=16 (first=1000640709, total=64) | ...
CGC-HOOK: ctx=... il=2 ntok=2 ids=[-1125231906 1034384912 1019091433 1016706563 ...]   ← id 已是垃圾
scripts/run_server.sh: line 2704: 44479 Segmentation fault: 11
```

* `CGC-PRE ≠ CGC-POST` ⇒ **消費者讀的是「這一步還沒寫的那張表」**（`st[161]`、`st[216]` 都還是 `-1`）。
* `id_oob=16`（**16/16**，`first=1003731148`）⇒ 全部 16 個 id 都在 143 槽的池外。
* 崩潰報告（`llama-server-2026-09-29-112001.ips`）：`EXC_BAD_ACCESS` / `SIGSEGV` /
  `KERN_INVALID_ADDRESS`，faulting thread = `com.apple.main-thread`，堆疊自頂向下：
  **`llama_context::expert_cache_on_topk` ← `llama_context::expert_cache_eval_cb` ← `ggml_backend_sched_compute_splits`**。
* **同一個簽名出現兩次**：儀器版 bench 臂也是 SIGSEGV（`rc=-11`、`CGC-WARM verify n_past=513`），
  崩潰報告 `llama-bench-2026-09-29-111519.ips`，frame offsets 與上面**逐位元相同**（205344／147608／144616）。
  即：乾淨臂勉強活到 128 token 的計時段，儀器臂死在 513，oracle 臂**連服務都起不來**——
  同一個缺陷，只是被三個不同的時序壓力推倒在不同深度。

⇒ **§26 預測的依賴是對的，而且比預測的更早發作**：「依賴是**逐層的 table 發佈**，不是 leaf 的寫入」——
證據不是推論，是崩潰堆疊直接落在發佈函式裡。

### 四、判據結算

| 判據 | 要求 | 實測 | 結果 |
|---|---|---|---|
| **R1** | E2 輸出 bit-identical（M1 9/9，不是「通常會贏 race」）| E2 無 dump：第一 decode 步 `id_oob=16/16` 後 SIGSEGV，服務未 ready | **FAIL** |
| **R2** | E2 相對同 build 的 `SLOT_TABLE_GPU=1` 控制臂，step 增益 ≥10 ms | 45.36 vs 85.99 ⇒ **−40.63 ms** | **數值上過，但不算過**（見下）|
| **R3** | E1 必須顯示 `cb` 或 gap/段已經下降 | `cb` 4.20→**5.34**、gap/段 0.352→**0.364**、t/s 11.68→11.63（平） | **FAIL** |

**R2 那個「過」要扣掉**：§26 的 R2 問的是「submit-ahead **還值多少**」，前提是兩臂算同一件事。
E2 的 45.36 ms 是在**讀未發佈的表**之下量到的（hit 96.3%→65.9%、38 280 筆請求無落點、16/16 id 出界），
那不是「同一個計算的重疊」，是**另一個計算比較快**。把它記成 SG-GAP 的槓桿，等於把 OOB 讀取的代價記成收益。
（順帶：它比 §19 的 −22.3%／−16.9 ms 大了 2.4 倍，這個差本身就是「不是同一個現象」的訊號——
§19 的臂沒有把 hit 打崩。）

⇒ **R1∧R2∧R3 = FAIL。−16.9 ms 不可保留。**

### 五、兩條否證分支都落地了，而且方向一致

§26 寫的兩條否證處置，本節各命中一條（第二條以 R3 的形式命中）：

1. **「E2 仍 non-deterministic」→ 依賴是逐層的 table 發佈**：成立（§三 的堆疊）。
   ⇒ **−16.9 只能靠 E3**，判據改為「**E3 的 G4 重算成本 < 16.9 ms**」（否則零和）。
   E3 的零件在樹上：`exp-prodnew-decode20-g4miss`、`e-s1-g3-zeroslot`、`CGC_ZERO_SLOT`。
2. **R3 不成立 ⇒ 「leaf 寫入離開臨界路徑」不是那 −16.9 的來源**：成立（§二）。
   ⇒ §26 §五 的原話「§25 的 SG-GAP 那一格就要重新定價，不能記在這個機制上」**現在生效**。
   同時 §19 tier 2 的 −22.3% 也必須重新歸因：它**不可能**是「移掉了 leaf 的寫入窗口」，
   因為把 leaf 的寫入整個換成 GPU gather（E1、位元等價、9/9）**一步都沒有省**。

**由此得到本節最有用的那個負面事實**：

> `CGC_SLOT_TABLE_GPU=1`（官方註解自己說「byte-for-byte what the hook used to write」）把 leaf 的**主機寫入**
> 移走了，而 `cb`（4.20→5.34）、gap/段（0.352→0.364）、union、t/s **四項全部不動**。
> ⇒ 段邊界那筆固定稅（§24：0.352–0.364 ms × 40 段）**不是被 leaf 的寫入定價的**。

那它是被什麼定價的？§24 已把「儀器造出來的 gap」排除（`instr_of_gap=0%`，本節兩臂都重現）、
把「主機在輪詢」量到了（`poll_iters` 629k–644k／步）。E1 再把「leaf 的寫入」排除。剩下的候選只剩
**段本身的完成邊界**（每層一次 stream 同步 ＋ 一次 command buffer 邊界），也就是一個**段數的函數**，
不是 remap 的函數。**這是一個可證偽的預測，本節不當結論用**：

* 決策規則（跑之前先寫死）：若把**段數**減半（而不是把 leaf 移走）而 gap/段**不變**、gap 總量減半 ⇒
  預測成立，力氣應轉向「少而大的段」（`CGC_SEG_BATCH`／單次提交家族，即 `CGC_B_SCHEME` 已在的地方）；
  若 gap 總量不動 ⇒ 段數不是定價軸，§24 的「每段稅」要改寫成「每步稅」。

### 六、還沒讀懂、本節刻意不下結論的一行  **（→ §29 已結案：`changed_entries=0` 是真的 0，不是矛盾）**

E1 與 E2 的日誌裡都有這一行，且**逐字相同**：

```
CGC-S1 slot-table: publishes=15015 clamped_selected=0 clamped_table=1696695 changed_entries=0 consumed_changed=0 consumed_unchanged_publishes=0
```

`changed_entries=0` 與上面 `CGC-PRE ≠ CGC-POST` 的可見矛盾，**未查**（`consumed-subset churn` 的儀器要用 `CGC_S1_TABLE_CHURN=1`，本輪沒開）。
它可能是「發佈的鍵集合與上一步無交集」而不是「內容沒變」，但**在把語義查清楚之前，這一行的任何一種讀法都不可引用**。
（這是本文件第 N 次遇到「計數器名字 ≠ 它數的東西」，見 §22 §二 的 `wcntw`。）

### 七、可引用性

* **可引用**：
  * Run A 的三列 t/s／hit／attribution（**但 t/s 本身 VOID，因為三臂皆非乾淨歸因**；可引用的是
    「E1 與控制同帶」「E2 的 hit 崩塌」「三臂的 attribution 標籤」）；
  * §二 的 `ntok=1` 中位表（48 步／臂，同 build 同 cell）；
  * §三 的三列 oracle 結果（控制與 E1 的 **9/9**、E1 的可比性、E2 的 NOT COMPARABLE 那一條 diff）；
  * §三 的崩潰三件套：`CGC-PRE/POST`、`id_oob=16/16`、兩份 `.ips` 的 frame stack（**逐位元相同**）；
  * §一 的計數器不平（38 280）——這是**算術**，不是估計。
* **不可引用**：本節任何 t/s 當作速度（三臂 attribution = swap／both）；E2 的 45.36 ms 當作 SG-GAP 的槓桿
  （不同計算）；§二 E1 那 4.2 ms 的**幅度**（該臂 worst HEAVY）；§六 那一行。
* **未查**：§五 的「段數 vs 每步稅」預測；§六 的 `changed_entries=0` 語義；
  E2 為何在乾淨 `llama-bench` 下能活到 128 token 而在 oracle 下活不到 n_past=2（時序壓力差異，未定位）。

產物：`Backup/stepbudget_2026-09-29/e1e2_clean.{json,run.log,stderr.log,logs/}`、
`e1e2_instr.{json,run.log,stderr.log,logs/}`、`m123_{ctl,e1,e2}.log`；
閘門產物 `Backup/m123_oracle_gate/{summary,cap,launch,oraclecmp}_e1e2-{ctl,e1,e2}.*`；
崩潰報告 `~/Library/Logs/DiagnosticReports/llama-{server,bench}-2026-09-29-11{2001,1519}.ips`。
### 八、與台帳的交叉檢查：那 ~40 ms **不是新數字，台帳上早有一列**

`docs/DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md` 第 21 列（單段提交 S1：
`CGC_SEG_BATCH+CGC_B_SCHEME+CGC_SLOT_TABLE_GPU`）量到 **A 11.30 → B 20.73 t/s**，
即 **−40.3 ms/token（45.5%）**（`Backup/seg_batch_s1_pairs/abba_212809.json`），
標記「⚠ 上界已量、可交付值未驗」、分級「③a 實驗目標達成（階段性）」。
第 25 列（`CGC_SLOT_TABLE_GPU` **單獨**，探針臂）則是「576/576 全同」⇒
本節 E1 的 **M1 9/9** 是對它的**獨立複驗**（不同 harness、不同 profile、不同探針）。

把三列排在一起，這段關係才看得出形狀：

| 臂 | 機制（拆掉的是什麼）| step 增益 | 收下的代價 |
|---|---|---|---|
| §19 tier 2 | submit-ahead（**leaf 仍是 host 寫的**）| **−16.9 ms（−22.3%）** | garbage／racy |
| 台帳 21 | `SEG_BATCH`＋`B_SCHEME`（**跳過每層 hook**）| **−40.3 ms（−45.5%）** | 路由錯（跑到 20.73 t/s ⇒ 沒有崩潰），不可交付 |
| **本節 E2** | `SLOT_TABLE_GPU`＋submit-ahead | **−40.63 ms（−47.4%）** | **SIGSEGV，落在發佈 hook 內** |

⇒ **兩件事**：

1. **「拆掉段邊界那筆等待」這個家族的天花板是 ~40 ms，而 §26 的 −16.9 是它最弱的一種形式。**
   ⇒ §19／§25 拿 16.9 當 SG-GAP 的槓桿數字**低估了這一塊**。
   但這 ~40 ms **一毫都不能收**：三種形式**全部**以正確性為代價，而本節把第三種的代價量成**行程死亡**。
2. **E1 ＝ 0 ms 正好解釋了為什麼這三列會落在兩個帶（16.9 vs 40）**：
   把 leaf 的**主機寫入**換掉（E1，位元等價 9/9）省 **0**；
   把**整層的 hook／等待**拆掉省 **~40**。
   ⇒ 被定價的是「**等**」，不是「**寫**」。這與 §五 的預測同向，但**證據等級不同**：
   §五 是尚未跑的決策規則，這裡是台帳上**已經存在的兩列對照**。

**引用邊界**：E2 的 47.4% 是在 OOB 讀取之下量到的（hit 96.3%→65.9%、38 280 筆請求無落點），
**不得**當作這族的上界填入 §25 的 SG-GAP；台帳 21 的 45.5% 才是目前唯一「跑到完、但輸出不可交付」的上界。

## 29. §28 兩個未查項結案：E2 在同一乾淨設定下 **8/8 rc=−11**；Run A 那次「存活」是倖存者偏差，而 `changed_entries=0` 是**真的 0**（2026-09-29）

§28 §六 留了兩件沒查的事：(1) E2 為何在乾淨 `llama-bench` 下活到 128 token 而在 oracle 下活不到 `n_past=2`；
(2) `changed_entries=0` 與可見的 `CGC-PRE ≠ CGC-POST` 的矛盾。本節把兩件都結掉。

**沒有新的速度數字；§28 的判決不動，只是變得更強。** 本節量的是「會不會死」，不是「多快」。

### 一、重複實驗：8/8 崩在同一族位置

指令**逐字取自** `e1e2_clean.run.log` 第 10 行（同 profile、同 cell、同 `-p 2048 -n 128 -d 512 -r 3 --warm-skip 64 --fixed-fill-seed 1`），
只把臂數縮成 1（就是 E2：`prod-new:CGC_SLOT_TABLE_GPU=1;CGC_SUBMIT_AHEAD=1`）。跑兩輪，共 **8 次 arm 呼叫**：

| rep | rc | 最後一行 | 崩點 `n_past` |
|---|---|---|---|
| lb_1 | **−11** | `CGC-WARM verify n_past=512 warm=2048 fast=0` | 512 |
| lb_2 | **−11** | `… n_past=513 …` | 513 |
| lb_3 | **−11** | `… n_past=0 …` | **0** |
| lb_4 | **−11** | `… n_past=514 …` | 514 |
| （第二輪：4 次 arm 也全崩，僅在寫出 artifact 時才報錯） | — | 8 份 `.ips` 對 8 次呼叫，**0 存活** | 12:10–12:16 |

八份新的崩潰報告（`12:10, 12:11, 12:12, 12:13 ×2, 12:14, 12:15, 12:16`）簽名與 §28 §三 那一份**同一個函式、同一個偏移**：

```
0 libllama      llama_context::expert_cache_on_topk(ggml_tensor*)                    205344 / 205368
1 libllama      llama_context::expert_cache_eval_cb(ggml_tensor*, bool, void*)        147608
2 libggml-base  ggml_backend_sched_compute_splits(ggml_backend_sched*)                 144616
exception: EXC_BAD_ACCESS / SIGSEGV / KERN_INVALID_ADDRESS
```

⇒ **「乾淨 `llama-bench` 會存活」不成立。** 崩點散在 `n_past = 0, 512, 513, 514`，
`lb_3` 死在 `n_past=0`（**第一個 decode**），正是 oracle 的死點。

**這就是那條「不對稱」的答案**：它不是 harness 的性質，是**同一個 race 的不同抽樣**。
oracle 抽到 `n_past=1`，`llama-bench` 抽到 `0/512/513/514`，Run A 抽到「沒踩到」。窗戶開在**頭幾步**，不是開在其中一邊。

### 二、Run A 那一次「存活」不是成功，是**讀到錯的表而活下來**

Run A 的 E2 確實走過了同一個轉折點（它自己的 log 有 `n_past=512 → 515`），所以它沒有換路徑，只是這一輪沒踩到。
但代價寫在同一份 log 的收尾：

```
llama_expert_cache: decode/pool (ensure_slot+batch) hits=84880/128760 (65.9%)
```

對照 **E1（同樣 `CGC_SLOT_TABLE_GPU=1`、沒有 submit-ahead）是 96.2%**。
所以 65.9% 不是「這個開關的常態」，是**這一輪獨有**的。加上 §28 §一 的算術缺口
（`128760 − 84880 − 5600 = 38280` 筆請求無落點）⇒ 那次「存活」是**踩到舊表、但值剛好合法**。

**⇒ §28 §一 的 E2 那一列要降級**：`22.048 ± 1.891 t/s` / `45.36 ms` 不是「E2 的速度」，
是**九次裡唯一一次沒崩、而且帳不平的執行**。R2 的「−40.63 ms ≥ 10 ms」因此不只是「算了不同的事」，
而是**在一場幾乎必崩的競賽裡抽到的那一次**。

### 三、兩支 harness 的結構差異找到了，但三個都不是原因

在崩點鎖定之前，兩個 harness 確實有三處不同。**第一項是本節新找到的**：

1. **`CGC_WARM_NPAST`：server `0`、llama-bench `2048`。**
   `run_server.sh:2471-2475` 對 `SERVER_DENSE_IQ4X=1`（我們的模型就是 denseIQ4X）走
   `SERVER_ENV+=(CGC_WARM_NPAST=0)`，而 llama-bench 走 C++ 預設 2048（`llama-context.cpp:7257`）。
   **但它不是原因**：兩邊崩時都印 `fast=0`，warm gate 的兩側都會死。
2. **publish site**：E1 與 E2 都是 `site=pool`（16/16）。MTP fast path 沒參與 ⇒ 「MTP 開/關」也不是原因。
3. **其餘計數器逐位元相同**：`publishes=15015`、`clamped_table=1696695`、`changed_entries=0`。

把三項扣掉，E1 與 E2 之間**只剩一個變數**：

| | `SLOT_TABLE_GPU` | `SUBMIT_AHEAD` | site | S1 計數器 | 結果 |
|---|---|---|---|---|---|
| E1 | 1 | — | pool | 同上 | 跑完、**M1 9/9** |
| E2 | 1 | 1 | pool | 同上 | **rc=−11**（8/8）|

⇒ **§28 §六 問的「harness 差異」是假問題。** 真正的失敗變數在 §28 §三 就被 E1 隔離出來了
（`CGC_SUBMIT_AHEAD=1`）；重複實驗只是把它唯一的前提「乾淨側會不會存活」推翻。

### 四、`changed_entries=0` 結案：它是**真的 0**，而且它回答了 premise B

§28 §六 說「`changed_entries=0` 與可見的 `PRE ≠ POST` 矛盾」。**不矛盾，兩者在量不同的區間**：

* `n_slot_table_changed`（→ `changed_entries`）比的是**同一層、同一 context 的上一次 publish**
  （`llama-context.cpp:5712-5745`），而且每個 key 的**第一次** publish 被 `seeded` 守門排除
  （`prev.size() == n_expert`；第一次 `prev` 是空的，所以那一次的分歧不計）。
* `CGC-PRE` / `CGC-POST` 比的是**同一次 publish 之內**（`ensure_batch` 之前／之後），
  而且只印**前 24 次**（`cgc_pre_post_n < 24`，`:7641`）——也就是**冷啟動那幾步**。

決定性的算術（本節新算）：

```
clamped_table / publishes = 1696695 / 15015 = 113.0000     （精確到小數第四位）
256 − 143 = 113
```

**每一次 publish 都夾住同樣的 113 個非駐留 expert。** 駐留集合是**不動點**，
所以「這一步的表與上一步相同」是真的、不是儀器瞎了。

而 `PRE ≠ POST` 出現的位置**在存活臂與崩潰臂完全相同**：

```
E1     (存活)  PRE≠POST 序列 = [T,F,F,F,F,T,T,F]
E2-A   (存活)                = [T,F,F,F,T,T,F,T]
E2-lb1 (崩潰)                = [T,F,T,F]
```

⇒ `PRE ≠ POST` 是**冷啟動補格**，不是那條 race。§28 §六 那一行**現在可以引用了**，讀法是：

> **穩態下被消費的映射不會在步與步之間移動 ⇒ 重新發佈是多餘的工作。**

這正是 header 裡 premise B 的原話（`llama-expert-cache.h:353`、`:392`），
也就是 §26 說 E3 要解決的那件事的**另一半**：排序要求**不存在於穩態，只存在於「形狀改變後的那一次 publish」**。

### 五、這一節改變了什麼（預測，不是結論）

* §28 的判決 **不變且更強**：R1 FAIL（無 dump）、R3 FAIL（E1 零收益）、R2 作廢（倖存者偏差）。
* **E3 的立項理由要改寫。** 不再是「逐層重算 < 16.9 ms」，而是
  **「把形狀改變後的那一次 publish 排到消費者之前」**。因為 §四 說穩態不需要排序，
  所以要做的是**冷啟動那一步的順序**，不是每一步的順序。
  決策規則（跑之前先寫死）：若 E3 只在「prefill→decode 轉折後第一次 publish」加一次同步，
  而該同步的成本 < 5 ms／轉折（每 token 攤提 ≈ 0）⇒ 這條路值得；
  若必須每步同步 ⇒ 等於關掉 submit-ahead，退回 §28。
* 底線：**`CGC_SUBMIT_AHEAD=1` 在現行設計下不可交付**（8/8 rc=−11；另一種結果是靜默讀舊表，hit 96.2→65.9）。

### 六、可引用性

* **可引用**：
  * 8 份新的 rc=−11 與其崩點（`n_past = 0/512/513/514`，8 次呼叫 0 存活）；
  * 8 份新 `.ips` 的 frame stack（與 §28 §三 同一函式、同一偏移）；
  * `clamped_table/publishes = 113.0000 = 256−143`（**算術**，不是估計）；
  * E1 96.2% vs E2-A 65.9% 的 hit 差（同 build、同 `SLOT_TABLE_GPU`，只差 submit-ahead）；
  * 三個臂**逐位元相同**的 S1 計數器；
  * `run_server.sh:2471-2475` 的 `CGC_WARM_NPAST=0` 分支（新找到的 harness 差異，已排除為原因）。
* **不可引用**：任何 t/s（本輪 probe 無 thermal／charter 閘門，且八臂全崩）；`lb_*.json` 裡的
   `pp 265.45 t/s`（該臂標 `[INCOMPLETE]`，且 attribution=both）；「E2 有時會活」當成可用性論據。
* **仍未查**：`expert_cache_on_topk` 裡**具體哪一行**在讀野指標
  （目前只知函式與 imageOffset，沒有符號內定位）；§五 的 E3 成本預測（未開跑）。

產物：`Backup/stepbudget_2026-09-29/asym/lb_{1..4}.{json,log,stderr.log}`、
`asym/lb_last_arm.stderr.log`、`/tmp/probe_asym/`（per-arm stderr）；
新崩潰報告 `~/Library/Logs/DiagnosticReports/llama-bench-2026-09-29-12{1044,1125,1210,1307,1359,1445,1536,1631}.ips`。

---
## 30. E1/E2 能不能修好 —— 答問，並把 §29 §六「仍未查」那一條結掉（2026-09-29）

問句（operator，2026-09-29）：「e1/e2 可以修得好嗎」。本節先給出**崩點的精確定位**（§29 §六
列為「仍未查」），因為它直接決定 E2 的答案。

### 一、崩點定位：`CGC-PRE` 那一行的 `st0[ids[j]]`（llama-context.cpp:7653-7657）

四步，全部可重跑，不需要重編譯或重跑：

**(1) `.ips` 的 faulting frame**（`llama-bench-2026-09-29-121631.ips`，10 份裡的一份，
10 份的 `symbol` 與 `imageOffset` 完全相同）：

```
exception   EXC_BAD_ACCESS / SIGSEGV / KERN_INVALID_ADDRESS at 0x00000000105d6a64
esr         (Data Abort) byte read Translation fault      <- 讀，不是寫
frame 0     llama_context::expert_cache_on_topk(ggml_tensor*)  image 3  imageOffset 205344
frame 1     llama_context::expert_cache_eval_cb(...)           image 3  imageOffset 147608
frame 2     ggml_backend_sched_compute_splits(ggml_backend_sched*) image 7 imageOffset 144616
image 3     libllama.0.0.630.dylib  base 4346789888   （build 檔期 09-29 10:24，
            與 §28/§29 的 `e5d1c0f14` 同一顆；崩潰發生在 12:16，晚於 build）
```

**(2) `otool -tvV` 在 `0x32220`**（= 205344，`__TEXT` vmaddr 0 == fileoff 0，所以
imageOffset 就是虛擬位址）：

```
0x321e4  adrp x8, 534 ; 0x248000
0x321e8  ldr  w9,  [x8, #0xe4c]        ; cgc_pre_post_n   (llama-context.cpp:7641)
0x321ec  add  w9,  w9, #0x1            ; cgc_pre_post_n++ (:7651)
0x321f0  str  w9,  [x8, #0xe4c]
0x321f4  ldr  w1,  [sp, #0x228]        ; il
0x321f8  ldr  x0,  [sp, #0xf0]         ; cache
0x321fc  bl   __Z29llama_expert_cache_slot_tablePK18llama_expert_cachej   ; (:7652)
0x32200  mov  x8,  x0                  ; st0
0x32204  adrp x9,  518 ; 0x238000
0x32208  ldr  x9,  [x9, #0xdd8]        ; ___stderrp
0x3220c  ldr  x0,  [x9]                ; stderr
...
0x3221c  ldpsw x12, x16, [x23, #0xc]   ; ids[3], ids[4]      (x23 = ids_snap.data(), :6006)
0x32220  ldr  w14, [x8, x12, lsl #2]   ; <-- 崩點：st0[ids[3]]
```

**(3) 逐位算回 `far`**（register dump，faulting thread）：

```
x8  = 4832429056 = 0x120090400          （合法的 heap 位址 —— st0 沒壞）
x12 = 18446744072570083737 → signed −1139467879  = 0xffffffffbc151999
x8 + x12*4 = 274557540 = 0x105d6a64     == .ips 的 far，一字不差
```

⇒ 崩的是 **`st0[ids[3]]`**：`ids[3]` 是一個**野的 int32**（−1.1e9），被拿去索引 host 的 slot table。
`x8` 本身合法，`far` 是「合法 base ＋ 野下標」，這正是 `vector[index]` 沒有護欄的簽名。

**(4) 是三個同形狀診斷列中的哪一個**：整顆 dylib 只有兩處編出
`ldpsw x?, x?, [x?, #0xc]`（`0x3221c`、`0x32dec`），而 `0x32220` 屬於前者，因為它前面緊接著
`cgc_pre_post_n++`，而那條自增**只存在於 `CGC-PRE` 區塊**（:7651）。`CGC-POST`（:7837）與
`CGC-SLOT`（:7861）是同形狀的另外兩處，**同樣沒有下標護欄**；差別只在 `CGC-PRE` 跑在最前面，
所以先被踩到。

**日誌佐證**（`Backup/stepbudget_2026-09-29/asym/lb_3.stderr.log`，n_past=0 那一臂，末三行）：

```
CGC-PRE: il=1 st[214]=74 st[45]=45 st[19]=19 st[233]=57 st[33]=33 st[250]=-1 st[38]=38 st[172]=-1
CGC-SLOT-TABLE-CLAMP: site=pool verify il=1 clamped=113/256 (no consumed id affected: sel_wrong=0)
```

最後一行 `CGC-PRE` 是**印完的**（stderr 無緩衝），publish 的 CLAMP 行也在它之後同一次呼叫裡
（:5473）。⇒ 死掉的是**下一次** hook 呼叫的 `CGC-PRE`，那一次連行都沒寫出來。也就是說
**野 id 是間歇出現的**：前面幾次搶贏了 race，那一次搶輸。

### 二、野 id 從哪來：`CGC_SUBMIT_AHEAD` 把消費者的「輸入」也一起拿掉了

`expert_cache_on_topk` 的 ids 前面已經有一次快照（`:5961` `ids = (const int32_t *) t->data;`
→ `:5995-6006` nb-aware copy → `:6006` `ids = ids_snap.data();`）。那段註解自己就把病灶寫出來了：

> the top-k ids live in the graph work buffer ... **ggml-alloc can REUSE the top-k buffer for later
> tensors of the same step**. Re-reading `t->data` after the blocking ensure_batch/drain_layer below
> then returns clobbered values (**segfault in the CGC-POST/st slot lookup** + a corrupted remap).

那個修補擋的是「**hook 執行期間**管線繼續前進」（async 分段 dispatcher）。`CGC_SUBMIT_AHEAD`
動的卻是 **hook 之前**（`ggml-backend.cpp:2701-2719`）：

```
submit_seg(i+1)     <-- cc 檔自己寫的「old racy submit-ahead order」(ggml-backend.cpp:2114-2118)
hook_seg(i) -> snapshot(t->data) -> ensure_batch -> remap leaf
```

segment i+1 一被 commit，它的節點就在寫，而 ggml-alloc 會把 segment i 的工作緩衝回收給它
⇒ 這次 hook 的**快照本身就可能是別人的中間值**。兩種下場，都是同一個 race：

* **搶輸** ⇒ id 落在 `[0, n_expert)` 之外 ⇒ `st0[ids[j]]` 越界 ⇒ SIGSEGV（10/10）。
* **搶贏但讀到合法值** ⇒ **remap 寫錯**（:7847 有 `e < n_expert` 護欄，所以它不會崩，只會寫錯）
  ⇒ §28 的 E2-A：hit 96.2/96.3% → 65.9%、request accounting 差 38 280、輸出是垃圾。
  E2-A「存活」只是沒有踩到越界那一步。

⇒ 「E2 會崩」與「E2 的值是錯的」是**同一個 race 的兩種下場**，不是兩個獨立的問題。

### 三、答問

**E1（`CGC_SLOT_TABLE_GPU=1`）：不必修，但也修不成收益。**

* 正確性沒問題：M1 9/9 逐位元（§28 §一）。
* 它想拿掉的東西（逐層 drain）**不是那張 host leaf 造成的**。同一層 FFN 的 `mul_mat_id` 讀的是
  pool 的 slot，而 pool 是 hook 的 `ensure_batch` 在**這一步**填的。fill 沒落地，這一層就不能
  dispatch ⇒ **drain 的主因是 fill ＋ residency publish，不是 leaf**。把 leaf 換成
  `get_rows(slot_table, ids)`，只是把「一次 32 B 的 host 寫」換成「每層兩個節點（CONT+GET_ROWS）
  ＋每層一次 1 KiB 的 publish」，fill 與 publish 一個都沒少。
* R3 的算術直接印證：`cb` 4.20→5.34 ms、gap/seg 0.352→0.364、union 13.83→15.07 —— **純加法**。
* 唯一能讓 E1 變成收益的路，是把**同一步的 fill** 從關鍵路徑上拿掉（step-ahead prefetch，
  路由穩定度 ~87%），再加上 §29 §四 的不動點（`changed_entries=0`、永遠夾住同 113 個
  ⇒ 表在穩態根本不動 ⇒ 形狀改變後 publish 一次就夠）。那是**另一件事**，不是「修 E1」。

**E2（`+CGC_SUBMIT_AHEAD=1`）：修不動；而且「修」的方向本身是錯的。**

* 它買到的 **−40.63 ms 就是那道 interlock**（hook 先讀 ids、填完 pool、寫完 leaf，再 commit
  消費它們的 command buffer）。拿掉 interlock 才有的重疊，正是它錯的地方 —— 不是實作沒寫好，
  是**把契約拿掉**。
* 兩個看似可行的「修法」都不成立：
  1. **給那三行加下標護欄**（`(size_t)e < cache->n_expert`，就像 :7847 已經做的）⇒ 不崩了，
     但野 id 照樣寫進 remap ⇒ **「響的錯」變成「靜的錯」**。這不是修，是降級。
  2. **把 publish 排到 submit 之前** ⇒ 那就是預設（非 ahead）順序 ⇒ 也就是沒有 E2。
* 所以 E2 的判決不變，但**理由要換**：§28/§29 把它判死時，第一理由是「8/8 rc=−11」。現在知道那
  10 次 SIGSEGV 是**一個沒護欄的診斷列**，不是機制本身。判死該改用 §28 的**值證據**
  （hit 96.2→65.9、accounting 差 38 280）—— 那條與崩不崩無關。**判決不變，而且更乾淨。**

### 四、這一節要做的事

* **立刻做**（1 行 × 3）：給 `CGC-PRE`（:7653）、`CGC-POST`（:7837）、`CGC-SLOT`（:7861）三個下標
  加護欄。理由不是美觀：這三處把「一個未知的野 id」升級成 SIGSEGV，而 SIGSEGV **吃掉整個後面的
  量測**（§29 的 8 臂就是這樣全滅的）。加護欄之後才數得到「野 id 出現幾次、值域在哪」——
  那才是 E2 真正的死因計數器，也是本 repo 反覆付學費的那個形狀（沒量到 vs 量到很低）。
* **E3 的門檻不變**（§29 §五），但多一條必要條件：**fill 也必須離開同一步**。只把
  publish 排序、留著同步 fill ⇒ 等於 §28，不會有 −40 ms。
* **`CGC_SUBMIT_AHEAD` 的定位改回程式碼自己寫的**：`diagnostic`（`ggml-backend.cpp:1804`、`:2117`）。
  它不是待修的候選臂。

### 五、可引用性

* 可引用：`§一` 的 `.ips`→`otool`→register→source line 定位鏈與 `far = st0 + ids[3]*4` 的算術；
  `lb_3.stderr.log` 的末三行；`cgc_pre_post_n++` 只出現在 `CGC-PRE` 區塊；§28 的 hit／accounting
  值證據。
* 不可引用：E2 的 −40.63 ms（值證據否決）；把「不崩了」當成「修好了」；任何 t/s（本節沒有新跑）。
* 仍未查：野 id 的**值域分佈**（要先加護欄）；`CGC-POST`／`CGC-SLOT` 兩處在 E2 下是否也曾越界
  （只有 `CGC-PRE` 被踩到，因為它跑在最前面）。

---
## 31. SG-ORDER 護欄落地與驗證；20／25 t/s 子目標再定位（2026-09-29）

§30 §四 說「立刻做」的三個下標護欄，這一節把它做完並量完，然後回答「20／25 的子目標實現了嗎」。
**這一節不產出任何可引用的 t/s**（兩跑都不在 charter 之下、也不是生產入口）；它產出的是
（a）一個不再能被診斷殺死的行程，以及（b）一個**可數**的野 id。

### 一、實作

`src/llama.cpp/src/llama-context.cpp`（`expert_cache_on_topk`），三行診斷
（`CGC-PRE` :7653、`CGC-POST` :7837、`CGC-SLOT` :7861）不再直接 `st[ids[j]]`，改走一個新加的
判別式：

```cpp
static int64_t cgc_id_oob_n = 0;
static int32_t cgc_id_oob_min = 0, cgc_id_oob_max = 0;
auto cgc_st_or = [&](const int32_t * tab, int32_t e, int32_t fallback) -> int32_t {
    if (tab == nullptr) return fallback;
    if (e < 0 || (int64_t) e >= (int64_t) cache->n_expert) {   // 越界：不讀，計數，回 fallback
        cgc_id_oob_n++;
        ... fprintf(stderr, "CGC-S1: OOB-ID n=%lld il=%d id=%d n_expert=%lld min=%d max=%d ...");
        return fallback;
    }
    return tab[e];
};
```

判別式與 `cache->n_expert` 的比對沿用本檔既有的寫法（`:7805`、`:7847`）——**這三處是唯一沒有護欄
的地方**，所以這是修 bug 而不是改行為：落在 `[0, n_expert)` 之外本來就沒有 slot 可報。
`OOB-ID` 印前 8 次與之後每 64 次一行，並帶 `min/max` 值域。

* build：`cmake --build build --target llama-server llama-bench -j 8`，12:46 rc=0；
  `libllama.0.0.630.dylib` md5 `304f2926970fad707785e92d501e359d`、mtime 09-29 12:46；
  `git rev-parse --short HEAD` = `2823fdc76` ＋本檔工作區 `+88/−10`（其中 +42 是**另一條線**的既有
  髒污，本節只加了三處呼叫與一個 lambda）。

### 二、驗證：兩跑，都是探針（VOID）

兩跑都用 §29 同一條重現路徑（`scripts/check/llama_bench_matrix.py`，`CGC_INTERNAL_CALL=1`，
`-p 2048 -n 128 -d 512 -r 3 --warm-skip 64 --fixed-fill-seed 1`，cell 由 fail-closed 契約強制對齊
測試卡）。**沒有 charter ⇒ t/s 一律不可引用**，下面也只引用「活／死」與計數器。

**跑 1（E2 臂：`prod-new:CGC_SLOT_TABLE_GPU=1;CGC_SUBMIT_AHEAD=1`）**

* **活了。** 同一支臂在補護欄前是 **8/8 rc=−11**（§29 §一），補護欄後 1/1 跑完；
  `~/Library/Logs/DiagnosticReports/` 最新一份仍是 **12:16:31**（補護欄之前），沒有新增。
* **野 id 是真的、而且被數出來了**：`CGC-S1: OOB-ID` 共 **9 行**，**全部 `il=1`**（與
  `lb_3.stderr.log` 崩點那一層同一層），`n_expert=256`，`min/max` 覆蓋正負兩邊：

```
n=1 id= 1017538649   n=2 id= 1017655563   n=3 id=-1123009795
n=4 id=  996832566   n=5 id=-1132000742   n=6 id=-1137900530
```

* **野 id 的形狀**（本節新算）：把它們當 int32 看是六個「億」級數字，當 **float** 看則是

```
0x3ca66859 =  0.02031    0x3ca8310b =  0.02053    0xbd103afd = -0.03521
0x3b6a7536 =  0.00358    0xbc870a1a = -0.01648    0xbc2d040e = -0.01056
```

  —— **全部是「小量級浮點」**（|x| ≤ 0.036），而且 `0.00358` 就落在 `1/256 ≈ 0.003906` 旁邊。
  也就是說 hook 讀到的不是「舊的 expert id」，而是**另一個張量的浮點內容**
  （值域與一個 256 路的 softmax／router probs 同量級；**這是假說，不是結論**，驗法見 §四）。
  這與 §30 §二 的別名（aliasing）解釋一致，而且把它從「可能是舊值」收窄成「是別人的浮點」。
* **臂仍然是錯的**：同跑的 `hit 55.8%`（§28 的 E2-A 是 65.9%，E1 是 96.2%）⇒ 護欄拿掉的是
  **響的錯**，不是錯本身。這正是 §30 §三 的預測，現在是量到的。

**跑 2（對照臂：`prod-new:CGC_SLOT_TABLE_GPU=1`，即 E1，非 ahead）**

* `CGC-S1: OOB-ID` = **0 行** ⇒ 護欄對「ids 合法」的臂**完全惰性**（不進那個分支）。
* S1 計數器與 §29 一字不差：`publishes=15015 clamped_table=1696695`（`= 113.0000`）、
  `changed_entries=0`、`hit 96.2%` ⇒ 這一跑同時也是「補護欄沒有改變任何既有量測」的控制。
  （`attribution=none`，但入口是 internal 驅動 ⇒ 仍然不可引用，見檔頭的 2026-09-25 ruling。）

### 三、20／25 t/s 子目標再定位

交付面寫死在 `scripts/check/charters/e-decode25-blocks-2026-09-29.yaml` 的 `targets`：
**decode 25.0 t/s、prefill 250.0 t/s**；基線 `11.703 t/s = 85.45 ms/token`
（`Backup/mtp_off_clean/res_2026-09-29c.json`，attribution=none）⇒ 25 t/s = 40.0 ms，缺口 45.45 ms。

**（1）prefill ≥ 250：✅ 已過線。** `docs/MILESTONE_MAP_RECHECK_2026-09-25.md:138`：9 次 launch
≥250（最高 296.24；乾淨視窗 283.01），判「達標但未超越」。這一格自 09-25 起沒有被複驗，但
也沒有反證。

**（2）decode 25（M-25）：❌ 未達成，且目前沒有已量到的槓桿。**
`MILESTONE_MAP_RECHECK:141` 已判死（交付基線 12.57 ⇒ 79.6 ms ⇒ 要再砍 49.6%）。
`e-decode25-blocks` 把「判死」重開成 5 塊可獨立驗收的子目標；§28／§29／§30 之後，這 5 塊的狀態是：

| subgoal | 目標 | 現況（證據） | 判 |
|---|---|---|---|
| `sg-order-legal` | 讓 submit-ahead 合法（top-k remap 移到 GPU） | §30：E1 對 leaf 的改動**值 0 ms**，而 drain 的主因是**同一步的 fill**；E2 就是 racy 順序本身，其 −40.63 ms 與它的錯是同一件事 | **否證** |
| `sg-gap-boundary` | gap 14.0 → ≤4 ms | 唯一實測上界 −16.9 ms 來自 submit-ahead（§19 tier 2）⇒ **前提被否證後失效**；§24 §四 寫好的 H-spin 決策規則**尚未跑** | **未命中（槓桿失效）** |
| `sg-offgraph` | 11.6 ms 先取儀器 | §27 取到儀器：`llama_synchronize` **8.858 ms = 殘差 80%** | **部分（有儀器、無槓桿）** |
| `sg-fillsub-spin` | 7.8 → ~3.9 ms | 只有現象（`poll_iters` ~15 933/seg、~107 ns/次）；穩態 `cb` 只 4.20 ms（§29）⇒ 沒有獨立臂量到收益 | **未命中** |
| `sg-kernel-busy` | 55.4 → ≤20 ms | 無槓桿：§22 的 kernel×time join **失敗**（沒有把 55.4 ms 拆到任何一個可攻擊的算子上）⇒ 有效頻寬 10–17 → 60 GB/s 這條至今只有外推 | **未命中** |

⇒ **四塊的「實測槓桿」加總 = 0 ms**（唯一進過台帳的 −16.9／−40.3 兩列都是錯輸出臂，§28 §一已降級）。
按本卡自己的 `acceptance.falsify`（任一塊拿不出實測槓桿 ⇒ 移除；移除後加總 > 40.0 ms ⇒
**本拆解不成立**），**以 ms 為軸的拆解目前不成立**；`on_fail` 指定的下一站是「以段數為軸重拆」。

**（3）decode 20+（＝正確版的 single-submit 家族）：未達成，而且判定閘門仍未在交付 cell 上量到。**
`MILESTONE_MAP_RECHECK:142` 給的可回收上界是「序列化 − 必須還回去的 fill」＝
`48.2 + 3.96 = 52.2 ms` ⇒ **19.2 t/s**（也就是 20+ 的天花板落在 19.2，而 shape/IO 的絕對天花板是
20.49／16.31）。`exp-prodnew-decode20-g4miss` 把「值不值得投工程」寫成一道閘：
**per-step `misses/nsel` ≤ 25% ⇒ 有路；≥ 60% ⇒ 交付上沒有 headroom**。現況：

* 量到的兩個數是 **42.9%（預設 cell）／41.8%（交付 cell）**，來源是 `g4miss2_default`／
  `g4miss_delivery` —— **兩份都沒有並存的 stderr log**，`instrument_binding` 判 UNBOUND ⇒ **不可引用**；
* 交付 cell 的四場嘗試**都無產物**（`/tmp/diag_hang.log`，`n_leaf=0`、0 列 `MISSMASK`）；
* 唯一有閘門紀錄的那一跑是**預設 cell** 的 fill-ahead A 半：**42.9% → 6.1%**（`prefetch 2854/0`，
  「未駐留 ≤10%」那道閘過），但該跑自陳**輸出仍是 garbage**（B 半未做），且**不能**推到交付口徑。

⇒ 20+ 的判定是 **UNRESOLVED**：不是判死，是**閘門本身還沒在交付 cell 上取得可引用的讀數**。
§30 不改變這一格（它拿掉的是「E2 會崩」這個理由，不是臂的價值）。

### 四、可引用性

* **可引用**：護欄的程式位置與 build 指紋；「補護欄前 8/8 rc=−11 → 補護欄後 1/1 存活」；
  9 行 `OOB-ID` 與其 `min/max`；六個野 id 的 float 讀法（**算術**，不是估計）；
  E1 對照臂 `OOB=0` 且 S1 計數器與 §29 一字不差；milestone map 的 138／141／142 三列；
  兩張 charter 的 `targets` 與門檻。
* **不可引用**：本節任一 t/s（無 charter、internal 驅動；E2 那跑 `attribution=swap`、E1 那跑
  雖 `attribution=none` 但入口不合規）；「42.9%／41.8%」這兩個 G4 miss 比例（沒有並存 stderr log）；
  「護欄修好了 E2」——護欄只讓它不再崩，`hit 55.8%` 證明它仍然是錯的。
* **仍未查**（本節結出來的新問題）：那個被別名覆蓋的緩衝**裝的是哪一個張量**？
  驗法便宜且可判定：在 `il=1` 的第一步開 `CGC_TD_CB`（或直接比對 segment i+1 前幾個節點的
  值域）看誰寫進那片記憶體；若是 router probs，就等於「hook 讀到了**下一步**的機率向量」，
  這條 race 的形狀就完全確定，也才能判斷「把 top-k 輸出搬進自有緩衝」是否足以讓 submit-ahead
  在**讀取面**變安全（那會讓 E2 的 −40.63 ms 重新可談，但**只限讀取面**，leaf 的寫入契約還在）。

---
## 32. G4 的每步 miss 比例（delivery cell）：**中位 41.99%**，配對修好了，而權威 cell 的新讀數這次拿不到（2026-09-29）

執行 `exp-prodnew-decode20-g4miss`（`targets.decode_tps: 20.0`）的驗收 ①②③。結論先寫：

1. **比例有了，而且可引用**：delivery cell 每步 `misses/nsel` 的 **中位 41.99%**（IQR **40.38–43.59%**），
   default cell 中位 **42.95%**（IQR 41.67–44.23%）。插樁活著（386 步 / 14 920 行 / `nsel=8>0`）。
2. **卡上的兩個門檻都沒到**：預期帶 10–35%（實測 42%，**在帶外**），關閉門檻 ≥60%（**沒到**）
   ⇒ 這一格是 **UNRESOLVED（模糊帶）**，不是「有路」也不是「無 headroom」。
3. **配對修好了**：`Backup/g4miss_2026-09-28/` 由「8 支產物、2 支缺成對 log」變成 **0 支缺**；
   修好的 log 開頭有 `# cgc-pair: artifact=… backfilled-from=…` 可查；儀器判定由 UNBOUND → **BOUND**。
4. **權威 cell 的『新』讀數這次拿不到，而且原因是量到的**（§三）：卡要求 `gen 128`，
   但同一支臂 `gen 128` + `CGC_MISS_MASK_DBG` **跑不完**；拿掉 DBG 就完成。

### 一、配對修復（唯讀檢查 → 唯一入口 backfill）

```
$ python3 scripts/check/instrument_binding.py --pairs 'Backup/g4miss_2026-09-28/*.json'
  小計：8 支跑的產物，2 支缺成對 log        <-- 正是 g4miss2_default / g4miss_delivery
$ python3 scripts/check/instrument_binding.py --pair-backfill Backup/g4miss_2026-09-28/g4miss_delivery.json \
      --from Backup/g4miss_2026-09-28/delivery_cell.stderr.log
✅ 成對 log 已寫入 Backup/g4miss_2026-09-28/g4miss_delivery.stderr.log（965206 bytes，開頭記下來源）
$ ... （g4miss2_default ← default_cell.stderr.log 同樣）
$ python3 scripts/check/instrument_binding.py --pairs 'Backup/g4miss_2026-09-28/*.json'
  小計：8 支跑的產物，0 支缺成對 log
```

配對的依據（寫在這裡，因為 backfill 只記錄「誰登記的」，不判斷「對不對」）：

* 檔名 1:1（兩個產物 ↔ 兩份 log）且 **mtime 同到分**（兩份都是 09-28 23:57）；
* 步數算術吻合：`g4miss_delivery` 的 rows 是 `n_gen 64` + `warm_skip 64`、3 reps
  ⇒ 3×(64+64) = 384 步，log 的 `CGC-MISSMASK-STEP` 是 **386** 行（+2 為深度填充那幾步）；
* 層集合吻合：log 只出現 `il=1..39`（39 個池層，`il=0` 不進池，所以永不印）；
* 內容吻合：`il=1` 起就有 misses（單次提交臂的簽名），而 `-p 0` 的對照臂
  （`delivery_ctl_143.stderr.log`）**一行 MISSMASK 都沒有**。

修完之後，儀器判定（`--require missmask_step,missmask_row`）：

```
✅ BOUND  受檢量具都動過：mm_pub_n_leaf=39、mm_pub_wrote=39、missmask_step=386、missmask_row=14920
```

### 二、每步比例（`scripts/check/miss_rate_summary.py` + 逐層行自己算，兩者對得上）

口徑（**寫死，避免又一次「分母是幾個」的爭議**）：一步的 `misses` 取
`CGC-MISSMASK-STEP: step=.. misses=..`；分母取 **該步的 `nsel0` × 39**（`nsel0` = 該步每層的
`nsel`，decode 步為 8；39 = 實際印出的池層數）。只算 `nsel0 == 8` 的 decode 步。

| | delivery cell | default cell |
|---|---|---|
| 產物 / log | `g4miss_delivery.json` / `g4miss_delivery.stderr.log` | `g4miss2_default.json` / `g4miss2_default.stderr.log` |
| build | `e5d1c0f14` | `e5d1c0f14` |
| computes（`CGC-MISSMASK-STEP` 行） | 386（decode 385 ＋ 填充 1） | 390（decode 385 ＋ 零 miss 5） |
| 每層列數（`MISSMASK il=`） | 14 920 | 14 923 |
| `nsel` 值 | 8（＋1 步夾雜 16384/24576/2097152） | 8 |
| **每步 `misses/nsel` 中位** | **0.4199（41.99%）** | **0.4295（42.95%）** |
| **IQR** | **0.4038 – 0.4359** | **0.4167 – 0.4423** |
| 漂移（head64 → tail64） | 0.4171 → 0.4168（平） | 0.4303 → 0.4253（平） |
| `MISSMASK-COST` 行 / `sync÷total` 中位 / `total_usec` 中位 | 386 / **0.0040（0.40%）** / **470 µs** | 390 / 0.0055（0.55%） / 388 µs |
| t/s | **VOID**（wrong-output 臂，卡 ③ 明訂） | 同上 |

**這兩個數把舊語料的引用對上了，也修掉一個分母錯**：

* 文件裡的 **42.9%** = `134/312`（預設 cell、分母 312 = 8×39）—— 本節從 log 直接算出的
  預設 cell 中位是 **0.42949**，**一字不差**。
* 文件裡的 **41.8%**（delivery）：`instrument_binding` 的引用行寫的是 **130.5/312 = 41.83%**，
  分母 **312 是對的**（和 42.9% 同一口徑）。本節復算得到 delivery 的每步 misses **平均 130.92**
  （386 步）／**131.02**（385 個 decode 步）、**中位 131 ⇒ 41.99%**。⇒ 「41.8%」應記為
  **41.99%**，而那 0.2 個百分點的差來自**分子的選樣**（引用行寫 130.5 但未寫明排除哪幾步），
  **不是分母錯**。這一行順帶證明兩件事：舊語料的 312 是共識口徑，以及本文 §二 的算法與它對得上。

`miss_rate_summary.py` 另外給的兩個數**問的不是同一件事**，不可混用：

```
miss rate MICRO       : 0.0224  (2.24%)  <- sum misses / sum nsel（含那一步 2097152 的分母）
miss rate MACRO       : 0.4226  (42.26%) <- 每層速率的平均
```

要拿去乘「每專家重算成本」的是**每步的中位／總量（≈42%）**，不是 2.24%：2.24% 被單一
巨型 shape 的分母稀釋了（`nsel=2097152` 那一步就是 `8×262144`，是 warmup/填充期的殘留 shape）。
本節建議的引用口徑：**per-step median（41.99%）＋ IQR ＋ 兩個 cell 的值**，並註明 42.26% 是
per-layer 平均、2.24% 是含巨型分母的總量比。

### 三、為什麼「權威 cell 的新讀數」這一次拿不到（中止位置）

卡要求 `delivery` cell，而測試卡 §2.5 的權威 block（今天）對 delivery 是
`batch 512 / ubatch 512 / prompt 0 / gen 128`。實跑（今日 build，`2823fdc76` ＋ §31 護欄）：

| 跑法（delivery cell、`-p 0 -d 512 -r 3 --warm-skip 64`） | 結果 |
|---|---|
| **gen 64**（`harness bench` 與 matrix 走同一條 cell 校驗） | **拒跑（fail-closed）**：`gen: 實際 64 ≠ 權威 128` |
| `harness bench`，gen 128，整卡臂（含 `CGC_MISS_MASK_DBG`） | **跑不完**：三次嘗試（600 s／500 s／160 s）都**沒有任何 per-arm 產物落盤** |
| 同上但 **拿掉 `CGC_MISS_MASK_DBG`**（只留 `CGC_MISS_MASK`） | **完成**（<150 s；t/s VOID） |
| 同上但 **拿掉 `CGC_MISS_MASK`**（只留 SEG_BATCH+B_SCHEME+SLOT_TABLE_GPU） | **完成**（<150 s；t/s VOID） |
| 對照：`prod-new` 同 shape | **完成**（<150 s；t/s VOID） |

⇒ 停滯**專門**落在 `CGC_MISS_MASK_DBG` 這條路徑（逐層回讀 ＋ `CGC_SEG_BATCH` 下的
`CGC-RB-FEED` 餵 `spac_update`／`cache_step_union`，`llama-context.cpp:3990-4080`），
不是 shape、不是 SEG_BATCH、也不是 `CGC_MISS_MASK` 的節點本身。
**而回讀本身的成本不可能是原因**：gen 64 那一跑量到 `sync/total` 只有 **0.40%**、`total_usec` 中位
**470 µs**（對比一步 ~35 ms）。

⚠ 精確的邊界：gen 128 那兩次被 kill 時**沒有任何 log 落盤**（驅動在子行程結束後才寫檔），
所以「停在哪一行」**還沒有讀數**——只知道「第一個 rep 之內」。要分「慢」與「卡死」，下一跑必須
**串流**子行程 stderr（直跑 llama-bench，或先讓驅動 tee），這是本節留下的第一個動作。

另一件必須記下來的事：**唯一存在的 delivery-cell G4 產物本身也不是權威口徑**。
`g4miss_delivery.json` 的 `cell` block 寫 `gen: 128`，但它的 row 是 `n_gen 64`，
而當時的 `contract` 只宣告了 `fixed_fill_seed: None → 1`（`mismatches: []`）——今天的檢查器
會把 `gen` 當嚴格維度擋掉，當時不會。⇒ 本節 §二 的 delivery 讀數是**「delivery *shape*、gen 64」**，
不是權威 cell；權威 cell 的那一格**仍然缺**，且缺的理由現在是量到的（§三 的表）。

### 四、這一節對「20+ 有沒有路」的裁決

卡自己的門檻：`≤25% ⇒ 有路`、`≥60% ⇒ 該機制在交付上沒有 headroom`、`expected 10–35%`。
實測（單次提交臂、無 fill-ahead 餵料）：**41.99%（delivery）/ 42.95%（default）**。

* 對卡上那支臂：**UNRESOLVED**——在預期帶之外，但沒到關閉門檻。⇒ 不能宣布有路，也不能關掉它。
* 但「有路」的**唯一**正面證據在另一個臂：`singlesubmit-fillahead` 的 A 半把同一個量從
  42.9% 打到 **6.1%**（預設 cell、`prefetch 2854/0`），也就是「**餵料前饋**」才是把 G4 工作清單
  縮到 25% 以下的東西，而它**沒有 delivery cell 的讀數**，且輸出仍是 garbage（B 半未做）。
* ⇒ 下一步的判別式很簡單：**把 A 半搬進 delivery cell 再量同一格**。
  若仍 ≤25% ⇒ 20+ 的路成立（接著做 G4 重算）；若回到 >60% ⇒ 依卡的 `on_fail` 關掉並轉 shape/IO。
  在這之前，「正確版 20+ 有路」只是一句有前提的話。

### 五、可引用性

* **可引用**：兩個 cell 的每步 `misses/nsel` 中位＋IQR（口徑與分母在本節寫死）；
  `42.9% = 134/312` 的獨立復算；「41.8%（130.5/312）與本節 41.99%（131/312）同分母、只在選樣」；
  兩份 log 的配對修復與 `BOUND` 判定；§三 那張「哪一種跑法完成／不完成」的表；
  `sync/total` 0.40% 與 `total_usec` 470 µs。
* **不可引用**：本節任何 t/s（全部 VOID：wrong-output 臂、且多數是 internal 驅動）；
  `hit 100.0%`（`CGC_SEG_BATCH` 跳過 hook ⇒ 快取計數器在這個臂上沒有意義）；
  「42% 就是 G4 的成本」——它是**工作清單的大小**，重算成本要另外乘單價；
  MICRO 2.24%（分母被巨型 shape 稀釋，問的不是同一個問題）。
* **仍未查**（依序）：① gen 128 + DBG 到底停在哪一行（要串流 stderr）；② A 半在 delivery cell 的
  同一格讀數（上表的判別式）；③ `nsel=2097152` 那一步是哪一種 compute、要不要從口徑裡排除。

產物：`Backup/g4miss_2026-09-29/miss_dist_delivery.json`、`miss_dist_default.json`（本節算出的
分布摘要）；`Backup/g4miss_2026-09-28/g4miss_delivery.stderr.log`、`g4miss2_default.stderr.log`
（backfill 後的成對 log，開頭帶來源）。

---
## 33. 「跑不完」不是卡死：停滯點在 `llama-context.cpp:4066` 的 O(nsel × |union|) membership feed（2026-09-29）

執行 §32 留下的第一個動作：**讓驅動串流子行程 stderr**，把 `gen 128` ＋ `CGC_MISS_MASK_DBG` 在
權威 cell（`delivery`）上的停滯點讀出來。結論先寫：

1. **停滯點有了，而且它是可讀的一行**：`MISSMASK il=9 step=2 nsel=24576 …`（第一個**寬 shape** 步），
   之後 135 s 的空白裡每一條進展行都沒有——而那段時間唯一隨 shape 成長的工作就是 feed
   （唯一會量到 ~27 層的量，見 §五；零 miss 的層不印 MISSMASK 行，但仍然被餵料）。
2. **不是卡死、不是死鎖**：主執行緒 100% 在一條 `bl _wmemchr` 的迴圈裡（兩次取樣 2315/2315、
   2322/2322 一致），8 條 `pool_loop` 在 `condition_variable::wait`、RSETS 執行緒在 `usleep`。
   **心臟（`CGC-RSS` 心跳）在跳、工作沒有動** ⇒ 用「有沒有新的一行」量停滯會被心跳騙過（§一 的教訓）。
3. **那一條迴圈就是原始碼的一行**：反組譯 `graph_compute+6388` → `bl _wmemchr`，對應
   `llama-context.cpp:4066` 的 `if (std::find(u.begin(), u.end(), e) == u.end())` ——
   2026-09-28 新增、**尚未 commit** 的「single-submit membership feed」區塊（`CGC_SEG_BATCH` 開啟時才跑）。
4. **成本是形狀的平方**：`routed` 有 `nsel` 個元素、`u` 會長到該步的相異專家數（≤256）⇒
   每個被餵的層 `nsel × |u|` 次 `wmemchr`；`nsel=24576` 時 ~5 s/層（實測兩條進展行之間 **135.3 s**），
   而**同一段程式在 decode shape 上只值 315 µs/步**（`nsel=8`）⇒ 這條路徑只在 prefill shape 爆掉。
   **〔§34 §六 在位更正〕**「`|u| ≤ 256`」只在 ids 是**真的 expert id** 時成立。爆掉的那兩層
   （`nsel ∈ {1048576, 2097152}`）的 `ffn_moe_ids_cont` 是未初始化／被重指的緩衝區 ⇒ ids 是**隨機 uint32**、
   `|u| ≈ nsel`、成本 ≈ `nsel²/2`；`nsel=24576` 那一層（il=9）**只花 1.5 s 就印出來了**。
5. **09-28 那一場（同一個 tag、同一組 env、同一個 cell）只要 28.4 s、`step 2` 的 COST 是 8 071 µs**
   （build `e5d1c0f14`）——因為它跑在 feed 區塊之前。⇒「gen 128 + DBG 跑不完」的原因不是 shape、
   不是 mask 節點，是**這個區塊本身**，而且它讓權威 cell 的 G4 讀數至今拿不到（§32 §四）。
6. **修法一行**（**未動**：`src/llama.cpp/src/llama-context.cpp` 目前是他線的未提交 hunk，+88/−10；
   本節只寫出修法，不動別人的紅線）：把 `std::find` 換成 `std::vector<uint8_t> seen(n_expert, 0)`
   位圖（或 `std::unordered_set`）⇒ O(nsel)；或把餵料**只限 decode shape**。

### 一、驅動的修法：串流 ＋ 剎車 ＋ 心跳感知的停滯看門狗

`scripts/check/llama_bench_matrix.py` 新增 `_stream_child()`（`run_arm` 唯一的 subprocess 入口），
三件事：

* **tee**：子行程 stderr 逐行 `write` ＋ `flush` 到 live log。舊路是
  `subprocess.run(..., capture_output=True)` —— stderr 收在**驅動的記憶體**裡，只有子行程結束才輪到
  `write_text`，所以驅動自己被殺時磁碟上一行都沒有。live log 放在**產物旁邊**
  （`<json>.logs/live/<tag>.<shape>.stderr.log`），不是 `--workdir`（`/tmp` 遲早被清）。
* **剎車**：`--arm-timeout`（秒）。逾時只 `killpg` **自己的 process group**（子行程 `start_new_session=True`
  起跑）⇒ 驅動活下來把 live log／產物寫完，讀數不再取決於外面那把刀幾點落下。中止的臂**不再走**
  「沒有完成任何 instance ⇒ 直接放棄」那條路（那條路連產物都不寫）。
* **看門狗**：`--stall-watch`（秒）＋ `sample <pid> 3 -mayDie -file …` 取堆疊。**判準是「距上一次進展行」**
  （`_PROGRESS_RE = ^(?!CGC-RSS)`）：引擎有 ~0.1 s 一顆的心跳，第一版用「有沒有新的一行」量，
  在 300 s 的停滯裡**一次都沒發火**——這是這一節自己踩到並修掉的儀器錯誤。

新旗標預設全關 ⇒ 既有 cell 的命令列與行為一個字都不變（`harness.py` 的 `_bench_cmd` 只在宣告時加）。
自測：`llama_bench_matrix.py --selftest` 新增 4 例（活著就有檔、逾時回收、停滯點名、**心跳不蓋住停滯**）
4/4；`harness.py selftest` PASS（含「預設不出現／宣告了就真的傳到 matrix」兩例）。

```
$ python3 scripts/check/harness.py bench --arm 'prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1' \
    --prompt 0 --gen 128 --depths 512 --reps 3 --warm-skip 64 --fixed-fill-seed 1 --batch 512 --cell delivery \
    --charter scripts/check/charters/exp-prodnew-decode20-g4miss.yaml \
    --arm-timeout 300 --stall-watch 90  --json Backup/g4miss_2026-09-29/g4miss_delivery_harness.json      # 跑 1（300 s）
    （同一條命令，最後三個換成 --arm-timeout 120 --stall-watch 25
      --json …/g4miss_delivery_harness_stall.json）                                                      # 跑 2（120 s，取堆疊）
```

### 二、停滯點（兩場的讀數）

| | 跑 1（`--arm-timeout 300 --stall-watch 90`） | 跑 2（`--arm-timeout 120 --stall-watch 25`） |
|---|---|---|
| live log | `…delivery_harness.json.logs/live/<tag>.p0_n128_d512_r3.stderr.log`（85 607 B／2 818 行） | `…delivery_harness_stall.json.logs/live/<同名>`＋`sample1/2.txt` |
| 結束 | 驅動剎車 rc=−15 @ wall **300.1 s** | 驅動剎車 rc=−15 @ wall **120.3 s** |
| step 1（decode、`nsel=8`） | 39 層 MISSMASK 行 @ t≈0.00；`COST total=315 µs sync=1` | 同（`total=315 µs sync=5`） |
| **最後一條進展行** | `MISSMASK il=9 step=2 nsel=24576` @ t=**1.50** | 同（t≈1.5） |
| 下一條進展行 | `MISSMASK il=35 step=2 nsel=16384` @ t=**136.83** ⇒ **中間 135.3 s 沒有進展** | **沒有**（到剎車前 118 s 一片空白） |
| 停滯 marker | 無（心跳騙過第一版判準） | 兩條：silent 25.2 s @ 17:27:41、25.2 s @ 17:28:09（心跳分別到 t=30.74／59.11） |
| 心跳 | 2 768 行 `CGC-RSS`（~0.1 s 一顆，`rss≈7547 MiB` 平坦） | 同 |

⇒ **停滯點寫得出來的樣子**：不是「卡在哪一行」，而是「step 2 這個**寬 shape** 步裡的兩條進展行之間
135 s；而 step 2 只會印 3 層（`il=9/35/36`），所以那 135 s 內真正被餵的層**一行都不印**」。

### 三、堆疊 → 原始碼那一行

兩次取樣的折疊統計完全一致：

```
sample1  main-thread leaf = wmemchr (libsystem_c) 2315/2315；sample2 同 2322/2322
   start → llama_bench → test_prompt → llama_decode → llama_context::decode
     → llama_context::process_ubatch → llama_context::graph_compute + 6388
  其餘：8 × llama_expert_cache::pool_loop @ condition_variable::wait（__psynch_cvwait 37 040）
        ggml_metal_rsets 執行緒 @ usleep／__semwait_signal（4 571）、__workq_kernreturn（6 945）
  leaf 0x18b82d7e0／0x18b82d7d4 = wmemchr 的 **symbol stub**（`_wmemchr` 進入點 +4/+16）
```

反組譯 `libllama.0.0.630.dylib`（file addr：`graph_compute` 符號在 0x245F8，`+6388` = 0x25CEC）：

```
0000000000025cc4	add	x22, x22, #4            ; 走訪 routed[]（int32）
0000000000025cd4	ldr	w26, [x22]              ; 取一個 id
0000000000025ce0	asr	x2, x8, #2              ; n = (u.end() − u.begin())/4（u 還在長）
0000000000025ce8	bl	0x1e39cc ; symbol stub for: _wmemchr
0000000000025cec	cmp	x0, #0                 ; 找不到 ⇒ csel → push_back
```

⇒ 唯一符合這個形狀的原始碼：**`src/llama.cpp/src/llama-context.cpp:4062-4070`**

```cpp
if ((size_t) il < cache_step_union.size()) {
    auto & u = cache_step_union[(size_t) il];
    u.clear();
    for (uint32_t e : routed) {
        if (std::find(u.begin(), u.end(), e) == u.end()) {   // :4066  <-- O(nsel × |u|)
            u.push_back(e);
        }
    }
}
```

它整塊由 `if (cgc_rb_seg_batch)`（`CGC_SEG_BATCH`）＋ `llama_expert_cache_pool_active()` 開啟
（區塊註解自述「single-submit membership feed」：`spac_update` 與 `cache_step_union` 兩條餵料），
而 `routed` 的大小就是 `nsel`（`MISSMASK` 的 `nsel=` 是**同一種量**：`n_expert_used × n_tokens`）。
`git blame`：這 42 行目前標「Not Committed Yet」⇒ **是他線正在改的同一個檔案**，本節不動。

### 四、為什麼 09-28 同一支臂只有 28 秒（這是最有力的對照）

| | 09-28 23:52（`Backup/g4miss_2026-09-28/g4miss_delivery.json`） | 今天（本節兩場） |
|---|---|---|
| tag／env | **完全相同**的 `CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1` | 同 |
| build | `e5d1c0f14` | `2823fdc76` ＋ §31 護欄 |
| 整支臂 wall | **28.4 s**（`rows` 有 `(0, 64)`） | >300 s／>120 s，`rows` 空 |
| step 2（寬 shape）的 COST | **8 071 µs**（`nsel0=24576`、`ngets=8`） | 從未走到 COST 行 |
| step 2 的 wall（心跳 t） | 1.54 → 1.65（**0.11 s**） | 135.3 s 且仍未結束 |
| feed 區塊 | 當時尚不存在 | 存在（未提交） |

⇒ 「跑不完」是**新程式碼的**性質，不是 cell、不是 shape、不是 `CGC_MISS_MASK` 的節點：
舊版在同一格上把**同一個** `nsel=24576` 步連同 3 層回讀一起用 8 071 µs 做完。
（注意 `ngets` 是**次數**，不是時間；那個臂的池子仍然沒有被重定中心——本節量的是**停滯成本**，
不是「餵料值多少」；後者要修完之後才量得到。）

### 五、算術與修法（寫出來，未套用）

* 每個被餵的層：`nsel` 次 `std::find`，每次掃 `|u|`（≤ `n_expert`=256；該步至少 12–64 個 miss ⇒ 相異專家實測兩百上下）
  ⇒ `nsel=24576` 時 ~4.9 M 次 `wmemchr` 呼叫。實測 135.3 s 的空白 ÷ ~5 s/層 ≈ **~27 層**，
  與「零 miss 的層也照樣餵、但一行都不印」一致（也解釋了為什麼 `CGC-RB-FEED`（每 2000 feeds 印一次）
  在兩份 log 裡都是 **0 行**）。
* 同一段程式在 decode shape（`nsel=8`）上是 8 次 find/層 ⇒ 39 層 315 µs。**形狀差 3 072 倍、成本差 10⁴ 倍。**
* `nsel=2097152` 那一步（§32 §二 記過的異常 shape）：2.1 M × ~200 ≈ 4×10⁸ ⇒ ~分鐘級**每層**，
  足以單獨解釋「永遠不會完成」。口徑上應把它**點名排除**（它同時是 garbage exps −11 億那一行）。
* 修法（三選一，成本都是 O(nsel)）：①`std::vector<uint8_t> seen(n_expert, 0)` 位圖；
  ②`std::unordered_set<uint32_t>`；③把餵料**只限 decode shape**（`nsel <= n_expert_used × width`）。
  ①最貼近原意（`u` 是**該步的 union**，本來就是去重集合）。

### 六、可引用性與這一節留下什麼

* **可引用**：停滯點那兩行與其 t（心跳給的牆鐘，135.3 s／0.11 s 是**時間**不是推論）；
  兩份取樣的折疊統計與 8 條等待中的執行緒（⇒ 不是死鎖）；反組譯那五道指令與 `graph_compute+6388`
  的符號化；`llama-context.cpp:4066` 的行號與它的 gate；09-28 的 `COST 8 071 µs`／wall 28.4 s；
  驅動修改的程式位置與自測 4/4、harness selftest PASS。
* **不可引用**：任何 t/s（兩場都是**空的** `rows`，本來就沒有速率）；「feed 區塊值多少 ms」
  （本節量到的是**停滯的牆鐘**與**取樣的堆疊**，不是單價——單價要修完再量）；
  這兩支產物的 `cell`／`instrument_gate` **是空的**：`harness bench` 在 `rc != 0` 時於注入
  這些欄位**之前**就返回（fail-closed 的既有行為）⇒ 它們是診斷產物，不是過閘的成績。
* **仍未查**（依序）：① feed 區塊修好後重跑同一格 ⇒ 權威 cell 的 G4 讀數（§32 §四 判別式的前半）
  ——**已由 §34 回答：中位 6.09%（IQR 3.85–8.65%）**，而那 6.09% 恰好落在 §32 給 default cell 的 6.1% 上；
  ② `nsel=2097152` 是哪一種 compute；③ feed 的**輸出**（`cache_step_union`）在 decode 上到底改變了
  多少 prefetch 命中——它才是「餵料值不值」的那個數。

產物：`Backup/g4miss_2026-09-29/g4miss_delivery_harness.json`（＋`.stderr.log`、`.logs/live/…`）、
`g4miss_delivery_harness_stall.json`（＋`sample1.txt`／`sample2.txt`）；
指令碼：`scripts/check/llama_bench_matrix.py`（`_stream_child`、`_PROGRESS_RE`、四個新旗標）、
`scripts/check/harness.py`（`_bench_cmd` 與 `bench` 的同一組旗標）。

---

> 在位更正：§32 §三 的表最後一列與「要分『慢』與『卡死』，下一跑必須串流」那句，
> **已由 §33 回答**：沒有卡死，135 s 的空白是 `:4066` 的 O(nsel × |u|) feed；
> 「沒有任何 per-arm 產物落盤」的原因在**驅動**（`capture_output=True`），已修。

---

## 34. `:4066` 修好後的權威 cell 讀數：G4 每步 miss 比例 **中位 6.09%**（IQR 3.85–8.65%），不是 §32 的 41.99%（2026-09-29）

§33 把停滯釘在 `llama-context.cpp:4066` 的 `std::find`，並把「修好再跑同一格 ⇒ 權威 cell 的 G4 讀數」
列為**第一件未查**。本節就是那一跑。改了**一行類別**的程式碼（§33 §五 的修法①：位圖），
別條線的 hunk **一個字都沒動**；delivery cell 的 `cell` 與 `instrument_gate` **第一次**被填起來
（§32 §四 缺的正是這兩個欄位），而 G4 的讀數是 **41.99% → 6.09%**。

### 一、讀數（同一格、同一個 arm 字串、同一個 `misses/312` 口徑）

| | §32（09-28 23:57） | §34（09-29 17:55） |
|---|---|---|
| 每步 `misses / 312` **中位** | **0.4199（41.99%）** | **0.0609（6.09%）** |
| IQR（25–75%） | 40.38–43.59%（126–136/312） | **3.85–8.65%（12–27/312）** |
| 平均 | 0.41993 | 0.07588 |
| head64／tail64 | 0.4171／0.4168 | 0.1665／**0.0421** |
| 最小／最大 | — | 0.0032（1/312）／0.5064（158/312） |
| `decode_steps` | 385 | 385 |
| `step 2`（prefill shape）的 COST | 8 071 µs | 25 110 µs |
| `MISSMASK-COST` sync/total | 0.400% | **0.400%** |
| decode `total_usec` 中位 | 470 | 412 |
| `cell` 欄位 | **空**（`rc != 0`，未注入） | **gen 128／`delivery`／13 項嚴格維度一致** |
| `instrument_gate` | **空** | **`ok=true`、verdict `BOUND`**（`mm_pub_n_leaf=39`、`mm_pub_wrote=39`） |
| 整臂 `wall_s` | 28.4 | 23.7 |

**口徑怎麼確認是同一個量**：先用同一支分析（照 `miss_dist_*.json` 的欄位定義，分母
**312 = 39 層 × 8**）跑 §32 那一份 log，**逐位復現**它自己的數字——中位 0.4198717…、
IQR 0.4038…／0.4358…、`head64` 0.417117…、`tail64` 0.416816…。所以右欄不是我另挑的口徑。
（另一種分母「8 × 有印出的層數」會給 15.91%／14.29–18.30%；它與 §32 的 `head64/tail64` **對不上**，
所以 §32 用的是 /312，本節也報 /312，兩種都列在 JSON 裡。）

### 二、改了哪一行（以及「真的編進去了」的證據）

`llama-context.cpp` 的 hunk B（別條線 2026-09-28 的未提交區塊）內，`cache_step_union` 餵料迴圈：

```c
u.clear();
for (uint32_t e : routed) {
    if (std::find(u.begin(), u.end(), e) == u.end()) u.push_back(e);   // ← :4066，O(nsel × |u|)
}
```

換成 `n_expert` 大小的**位圖**（`static thread_local std::vector<uint8_t> rb_seen`，每層
`fill` 一次）：保留原本的**首次出現順序**（位圖只會抑制「已在 `u` 裡」的值 ⇒ 對每個界內 id
產生的 `u` 與舊迴圈逐位相同），成本降為 O(nsel)。新增的只有一個靜態計數器 `cgc_rb_oob`
與一個**需 `CGC_RB_FEED_DBG` 才會印**的 `CGC-RB-FEED-OOB` 行（§五）。

* build：`bash scripts/check/build_when_clear.sh --now`，17:53:02 開閘、17:53:07 rc=0；
  log 內有 `Building CXX object src/CMakeFiles/llama.dir/llama-context.cpp.o`（1 次）＋ relink。
* 產物：`src/llama.cpp/build/bin/libllama.0.0.630.dylib` 17:53；
  `strings … | grep -c CGC-RB-FEED-OOB` = **1**（同時 `CGC-S1: OOB-ID` = 1 ⇒ 這一顆也含 §30 的守衛）。
* §30 守衛在本跑**一次都沒發火**（`CGC-S1: OOB-ID` 出現 0 次）⇒ 它不可能解釋下面的差異。

### 三、G4 為什麼從 42% 掉到 6%（正面證據 ＋ 一個反面檢查）

1. **09-28 那一份根本沒有餵料區塊**：`Backup/g4miss_2026-09-28/delivery_cell.stderr.log` 內
   `CGC-RB-FEED` 出現 **0** 次（本跑 7 次：`feeds=2000…14000`，`il` 遞增 7/18/29/1/12/23/34）。
   配對的收尾統計：09-28 `file_reads=0 pread_usec=0 fill_batch_usec=0 prefetch=0/0`；
   本跑 `file_reads=7215 pread_usec=2 640 847 fill_batch_usec=2 643 691 prefetch=2405/39`
   ⇒ 本跑的池子**真的被搬動過**（2.64 s 的 pread），09-28 那場**一頁都沒讀**。
2. **`CGC-MM-PUB` 的 resident 欄**（每場印 8 次，上限 8）：09-28 凍結在 **5577/9984 = 55.9%**、
   `nres=143` 不變；本跑 55.9 → 54.5 → 53.7 → 53.0 → 52.5 → **51.9%**、`nres` 143→138
   ⇒ 池子的**內容**在換（數量級別本來就被 slot 預算鎖住，變的是「換進哪些」）。
3. **不是暖機假象（鋸齒檢查）**：`-r 3` 的換檔在 step 129／257，若每次重跑都把池子打回原狀，
   兩處會跳回 ~42%。實測 step 128→129 = 6.73%→4.49%、256→257 = 3.85%→6.09%、384→385 = 3.21%→6.09%
   ⇒ **沒有鋸齒**，低 miss 是跨重跑延續的狀態。
4. **同一起點、之後才分岔**：兩份 log 的 step 1（139 misses）與 step 2（95 misses）**逐層逐值相同**
   （`--fixed-fill-seed 1` 讓路由可重現），分岔從 **step 3** 開始——正是餵料開始攪動池子的時候。
5. **跨 cell 收斂**：§32 自己量到 default cell 在 feed-ahead 下是 **6.1%**
   （「`singlesubmit-fillahead` 把同一個量打到 6.1%」）。本節在 **delivery cell** 上量到 **6.09%**
   ⇒ §32 §四 那個判別式問的兩邊，現在**同一側**。

**反面檢查（必須寫下來）**：這兩份 log 來自**不同的 build**（09-28 那顆沒有餵料區塊），
所以「是餵料造成」是 **機制 ＋ 這對 log ＋ 跨 cell 一致**三件事合起來的結論，不是單一變因的實驗。
能推翻它的做法見 §七。

### 四、殘餘成本：只剩 O(nsel)，而且集中在 386 個 compute 裡的 1 個

step 2 是 prefill shape（4 層在圖上：`nsel = 24576 / 16384 / 2097152 / (一個 0-miss 層)`，
`CGC-MM-PUB` 第 2 次的第一片葉是 `nn=1048576`）。它的 COST 從 8 071 µs 升到 **25 110 µs**，
差值就是餵料現在的 O(nsel) 工：`routed` 的複製（8 MB）、`spac_update`（每 id 兩次界檢 ＋ 互斥鎖，
~2.1 M 個 id）、位圖、以及 miss 計數。全程 386 個 compute 中**只有這一個**是這個量級
（其餘 385 個是 `nsel=8`，decode `total_usec` 中位 412 µs）。整臂 `wall_s` 23.7 s
——**不可引用**：本跑 `attribution=swap`（VOID），§32／§33 的 t/s 才是該比的那組。

### 五、唯一的語意差：界外的 id 現在被丟掉（不進 `u`）

* 觀測到的界外 id **只有一個地方**：`nsel=2097152` 那一層（`il=36`），
  `exps:` 裡有 `1074741469`、`-1091668163` 這種值；**兩份 log 都有**（各 19 個，數字不同）
  ⇒ 那是那一層 `ffn_moe_ids_cont` 被重指／未初始化，**不是本跑引入的**。
* **decode 沒有這個問題**：6092 個 decode 列、9115 個 id **全部落在 [0,255]**（兩份 log 皆然）。
* 丟掉它是**與鄰居一致**的行為：同一區塊上一行的 `llama_expert_cache_spac_update` 自己的迴圈就是
  `if (experts[i] < ne)`；下游消費者 `llama_expert_cache_prefetch_slot` 也對同一個 id
  `expert >= cache->n_expert` 走 `guard_reject` ⇒ **prefetch 的動作不變**，變的只是記錄下來的
  union 不再背著一個永遠解析不了的 id（舊碼會把 ~2.1 M 個隨機 id 收進 `u`，那正是 §六 的爆點）。
* **未量**：`cgc_rb_oob` 的次數需要 `CGC_RB_FEED_DBG=1`（預設不印）⇒ 列為未查①。

### 六、§33 §五 的在位更正：停滯層不是 `nsel=24576` 那一層

§33 的估計是「`u` ≤ 該步相異專家數（≤256）⇒ `nsel=24576` 時 ~5 s/層」。這在 ids 是**真 id** 時才對。
停滯的那一場，`il=9`（`nsel=24576`）的列 **t=1.50 就印出來了**，下一列 `il=35`（`nsel=16384`）在
**t=136.83**，而 `il=36`（`nsel=2097152`）**永遠沒印**——`cache_missmask_tensors` 是 `std::map`
**升冪**走訪，所以 135.3 s 是花在 **10…34 之間某一層**（含 2097152 形狀那群），不是 il=9。
隨機 uint32 的 `u` 會長到 ≈ `nsel` ⇒ 成本 ≈ `nsel²/2`，這才是「永不完成」的來源；
位圖把那一層壓回 O(nsel)（實測：3 層合計 25 110 µs 完成）。
**另一個必須記的 provenance 陷阱**：`engine_build` 出自 `llama-bench` 的 `build_commit`
（upstream 的 commit），**我們對 `src/llama.cpp` 的未提交修改不會改變它**——本跑與 09-28 都寫
`e5d1c0f14`。要用 build 區分，只能看產物自己的 stderr（`CGC-RB-FEED` 在不在）或 `strings`。

### 七、可引用性、產物、仍未查

* **可引用**：§一 的表（兩欄都是同一支分析、同一分母）；G4 中位 6.09%／IQR 3.85–8.65%；
  `cell`（gen 128 `delivery`，13 項）與 `instrument_gate`（`BOUND`，`mm_pub_n_leaf=39`／`mm_pub_wrote=39`）；
  `CGC-RB-FEED` 7 行／`file_reads=7215`／`prefetch=2405/39`；`CGC-MM-PUB` 的 55.9→51.9% 漂移；
  兩份 log 的 step 1–2 逐位相同；`MISSMASK-COST` 0.400%；step 2 的 8 071→25 110 µs；
  `:4066` 的修法與 `strings` 那一行。
* **不可引用**：任何 **t/s**（本跑 `attribution=swap` ⇒ VOID）；
  「6.09% 的**因果**」單獨引用（見 §三 的反面檢查）；
  `engine_build` 當 build 身分。
* **仍未查**（依序）：① `cgc_rb_oob` 的實際次數（`CGC_RB_FEED_DBG=1` 的一場 VOID 探針）；
  ② 能做**單一變因**的那一場：同一顆 binary 下把餵料關掉再量同一格（本節做不到——餵料的 gate 就是
  這一格定義用的 `CGC_SEG_BATCH`）；③ feed 的輸出（`cache_step_union`）在 decode 上改變了多少
  prefetch 命中（§33 的未查③，仍未查）；④ `nsel=2097152` 是哪一種 compute。

* **稽核面（唯讀重跑，與基準逐項一致）**：`void_number_check` **PASS**（決策面 14 檔，無未標記的作廢數字）；`provenance_gate check` **PASS**（offenders 40／baseline 41，無新 offender；`selftest 74/74`）；`arm_ledger_check` **17 列 PASS**；`claim_instrument_check` **5 QUOTABLE / 1 WARN / 8 VOID**（＝ §32 配對回填後的狀態，未因本節上升）；`mindmap_void_check` **1/55**（未上升）；無殘留行程、無新 `.ips`。

* 產物：`Backup/g4miss_2026-09-29/g4miss_delivery_harness_fix4066.json`（48 797 B；`cell`／
  `instrument_gate` 已注入、`incomplete=false`、`driver_abort=null`）＋ `.stderr.log`（394 766 B）＋
  `.json.logs/live/<tag>.p0_n128_d512_r3.stderr.log`（396 154 B）；分析
  `Backup/g4miss_2026-09-29/miss_dist_delivery_gen128_fix4066.json`；對照
  `Backup/g4miss_2026-09-28/delivery_cell.stderr.log`；build log
  `.workbuddy/build_when_clear.log`；驅動
  `python3 scripts/check/harness.py bench --arm 'prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1' --prompt 0 --gen 128 --depths 512 --reps 3 --warm-skip 64 --fixed-fill-seed 1 --batch 512 --cell delivery --charter scripts/check/charters/exp-prodnew-decode20-g4miss.yaml --arm-timeout 300 --stall-watch 90 --json …/g4miss_delivery_harness_fix4066.json`。

> **副作用（非手改）**：`harness bench` 的 §D 自動回寫在產物過 `instrument_gate` 後把本跑 sync 進
> mindmap ⇒ 17:55 重繪了 `docs/mindmap/` 下 **115 個檔案**（`mindmap.json` +1 464/−31 行、22 個新
> entry、`fix4066` 出現 3 次）。這是 harness 的設計行為，**沒有撤**：要撤得 `git checkout docs/mindmap`，
> 那會連別條線未提交的重繪一起丟掉。

---

## 35. 把 §34 的 G4 讀數變成可執行的一步：「預測＋重算」的施工順序、驗收條件，與 19.2 t/s 天花板下的實際回收值（2026-09-29）

§34 把判定閘門從 `UNRESOLVED` 推成 **6.09% ≤ 25% ⇒ 有路**。但「有路」不等於「值得走」，
也不等於「知道下一步做什麼」。本節把那句話拆成**可執行、可驗收、可被否證**的東西，
並且先把 §34 沒寫的一個機制更正補上——因為它決定 P1 要做在哪裡。

### 一、先更正一件事：交付臂上「活的餵料」是 SpAc EMA，不是 `cache_step_union`

§34 把 41.99% → 6.09% 歸因於「餵料區塊存在」，但沒有分辨區塊裡的**兩條**餵料哪一條在跑。
讀源碼（`llama-context.cpp:2180-2262`）後確定只有一條：

| 餵料 | 消費者 | 交付臂上 |
|---|---|---|
| (1) `llama_expert_cache_spac_update`（`:`4059） | `spac_prefetch`（`:2182-2189`） | ✅ **活的** |
| (2) `cache_step_union[il] = u`（`:4085` 附近） | step／prev／hist 三個分支（`:2191-2246`） | ⛔ **惰性** |

理由：`if (spac_on) { spac_prefetch(...) } else if (pf_hist) {…} else {…}` ——
`prod-new` 的 profile 預設 `CGC_SPAC=1`（`run_server.sh:290/339/410`）⇒ `pf_hist`／`pf_prev`／
`pf_src` 三個分支**不可達**，`cache_step_union` 在本 cell 上**沒有任何讀者**。

兩條直接推論：

1. **`:4066` 的位圖修法買到的是「跑得完」，不是「miss 變低」。** 它逐位保留 `u`（§34 §二），
   而 `u` 沒人讀 ⇒ miss 率對這一修的敏感度**構造上為 0**。41.99% → 6.09% 只能歸因於
   「`spac_update` 被餵到了」，也就是區塊的存在，不是區塊的速度。
2. **P1 要永久化的是 `spac_update` 的餵料。** 現在它掛在 `CGC_MISS_MASK_DBG=1` 的回讀迴圈裡
   （§4 施工清單第 1 條自己寫的限制）⇒ 「開著 debug 插樁才會有正確的池子」。這是原型狀態。

### 二、價格：殘差的錢花在 **IO**，不在計算——而且 **B（重算）省不掉它**

| 量 | 值 | 出處 |
|---|---:|---|
| 交付 cell 的穩態 miss | **1.97%（6.31/step）** | `e-fillbudget-m-decomp.targets.result_p1`（誠實臂，p2048/`-b -ub 5632`） |
| 交付 cell 的 fill | **p50 5.41**／p90 12.72 ms/step | 同上（EBTIMER 修好 `seg=` 後） |
| 交付 cell 的 IO 體積 | `read_mib` 2614.0／`req` 128920、`MiB/miss` **0.524** | 同上 |
| 交付 cell 的**有效吞吐** | **≈ 1.2 GiB/s**（反解） | 同上 |
| 一個專家的權重 | 1.083 MiB（舊 cell 六臂 `MiB/miss` 1.081–1.088，±0.3%） | `e-asyncfill-recompute.p2_adjudication.fill_budget_closure` |

⇒ 殘差 19.0 個/step × 0.524 MiB = **9.96 MiB/step** ⇒ 在量到的 1.2 GiB/s 下 = **8.3 ms/step**。

⛔ **這裡是否證一個直覺的地方**：想算一個缺席專家的 FFN，**必須先有它的權重**。
所以「重算（B）」和「阻塞式 fill」付的是**同一筆 IO**；B 只買到「不阻塞」——
而「不阻塞」的機制就是**把 IO 排到前一步的 GPU 窗之後**，那是 **A** 在做的事。
⇒ **B 不是 IO 的替代品**，它是 A 漏掉那最後一小塊的補丁（見 §四 P4）。

### 三、殘差有地板：A 再好也到不了 0，但 §34 的 6.09% 離地板還有 1.7 倍

席位掃描（同一份分析、同一 cell 家族）：

| 席位 | 逐步駐留 |
|---|---:|
| 8 | 96.0%（預設 cell）／96.8%（交付 cell） |
| 16 | 96.5% |
| 64 | 96.3% |
| 143 | 96.3%／96.7% |

⇒ 席位加到滿也只到 **96.7%** ⇒ **殘差地板 3.3–3.7%（≈ 11–12 個/step）**。
§34 量到的 **6.09%（19.0/step）是這個地板的 1.6–1.8×** ⇒ **A 還有一步可走**，
而且它**比 B 便宜**（不動圖，只動預取來源／窗）。

⚠ 別把兩個 miss 率混為一談：誠實臂的 **1.97%**（交付 cell）定義是「hook 當下不在池」，
G4 的 **6.09%** 是「**提交**當下不在池（且 hook 不存在）」。後者更大不是矛盾，是不同口徑。

### 四、施工順序（依「先便宜、先否證、先不動 src/」排序）

**P0 ✅ A：把本步被選中的 id 餵給 `spac_update`（已完成，§34）**
驗收（已成立）：`CGC-RB-FEED` ≥1 行、`prefetch` 離開 `0/0`、`file_reads>0`、
`instrument_gate=BOUND`（`mm_pub_n_leaf=39`／`mm_pub_wrote=39`）、miss 中位 ≤25%。

**P1 把餵料從 `CGC_MISS_MASK_DBG` 的回讀裡拆出來**（`src/`，小）
現在「池子會重定中心」這件事**綁在一個 debug 插樁上**，而那個插樁自己多一次
`sched_synchronize`（分段臂量到 11.5 ms/step；S1 臂 2–3 µs）。
* 做法：讓餵料有自己的觸發點（device-side 來源，或把那個 sync 拿掉），
  `CGC_MISS_MASK_DBG` 只留列印。
* 驗收：**不開** `CGC_MISS_MASK_DBG` 的那一趟仍有 `CGC-RB-FEED` 與 `prefetch>0/0`；
  且 `MISSMASK-COST sync/total` **不再出現在生產路徑**上（現在是 0.400%）。
* 否證：拿掉 sync 後餵料拿不到本步 id（`ibuf` 就是回讀的產物）⇒ 得改来源，
  登記「餵料需要一個新的 device-side 來源」，不硬做。

**P2 把殘差從 6.09% 壓到地板（≤3.7%）——先做這個，不要先做 B**
* 做法：A 的來源是 **SpAc EMA**，而 EMA 的**窗／K／alpha 是旋鈕**
  （`CGC_SPAC_K`／`CGC_SPAC_ALPHA`／`CGC_SPAC_REFRESH` 已在白名單，
  `run_server.sh:2289/2300`）。若要走 union 那條，`CGC_PREFETCH_SRC` **不在**白名單
  （`:1482-1486`，09-13 移出、註解保留「opt back in for A/B」）⇒ 補 3 行即可，
  且 `CGC_PREFETCH_WINDOW` **已在**白名單（`:2344`）。
* 驗收：交付 cell 上 G4 中位 **≤3.7%**，且 `prefetch` 的 `n_prefetch`／`n_prefetch_dropped`
  不失控（dropped 相對 n_prefetch <10%），`read_mib/step` 不超過 2× 現值（6.49 MiB/step）。
* 否證：把旋鈕轉到極端仍停在 ~6% ⇒ 殘差不是「預算不足」而是「無資訊」
  （那些專家在**任何**近期窗裡都沒出現）⇒ 直接跳 P4，不再花錢在預取上。

**P3 武裝 G3（`CGC_ZERO_SLOT`）——這是 B 的**前置**，且只需 2 趟、不需動 `src/`**
已在 `e-s1-g3-zeroslot-2026-09-29.yaml` 立卡；關鍵事實（09-29 查證）：
`CGC_ZERO_SLOT=1` **單獨開在交付臂是 no-op**（`zero_slot=0 placeholder=0`、逐層行 0 次），
因為 slot table 的 leaf 只在 `CGC_SLOT_TABLE_GPU=1` 時才存在。
* 驗收：B 臂 `CGC-G3-ZEROSLOT-TOTAL: zero_slot>0 placeholder=0`；
  A 臂（對照）`zero_slot=0 placeholder>0`。**主端點是計數器翻轉，不受 MDD 8.5% 限制。**
* 否證：B 臂仍是 `placeholder>0` ⇒ B 的「0 ＋ 補算 ＝ 原值」前提不成立 ⇒ **B 不成立**。

**P4 B：靜態 acc 補丁（`ggml_acc`）**
`e-asyncfill-recompute.p2_adjudication` 已把 B 的**形狀**裁定完，直接沿用：
* **靜態圖約束**：decode 的圖**建一次、重複用**，而 miss 集合**逐 step 變**
  ⇒ 不可能「這步 2 個 miss 就建 2 個節點」⇒ 唯一靜態可行的形狀是
  **每層常備 k 個 `ggml_acc`**（缺席那幾列填真值、其餘 `+=0`）。
* ⇒ **成本 = k×n_layer 固定，與 miss 率無關**：交付 cell 實測 `n_layer=40`、`k=8`
  ⇒ **320 op/step**（先前的 312／328 兩版都作廢）⇒ × 5–10 µs/op = **1.60–3.20 ms/step**。
* 適用性已 resolve-only 查證：prod-new 的 `CGC_DOWN_COMBINE` = `<unset>`
  ⇒ 走未融合 combine（`llama-graph.cpp:2838-2941`）⇒ acc 寫回那條路成立。
* 風險（未變）：`grep ggml_acc(` 在 `src/llama.cpp/src/` **0 命中** ⇒ 新路徑、無範例可抄；
  且 `ggml-alloc` 為這個常備子圖新增的 buffer 與 zero-fill **尚未估**。
* 驗收：`m123_oracle_gate.py` **M1 9/9 且 M2 9/9**、`config_diffs == []`、
  coverage 100% 且無鍵碰撞警告；生產路徑**無新增 `synchronize`**；
  op 增量 = 320（**不隨 miss 放大**）。
* 否證（任一成立即停）：M1 < 1.0 且 coverage 100%；或 acc 子圖的成本**隨 miss 縮放**
  （⇒ 靜態圖論證不成立、需要逐步重建圖 ⇒ 成本量級不同）；或 G3 在 P3 沒過。

**P5 測速**：交付 cell、`--warm-skip 64`、`-r 3`、thermal NOMINAL、`attribution=none`，
與 **11.703 t/s / 85.45 ms** 的錨點**成對**（不是與 24.88 比——那是錯輸出探針）。

### 五、19.2 t/s 天花板下的**實際回收值**

| 列 | step (ms) | t/s | 來源／性質 |
|---|---:|---:|---|
| 交付錨點（今天） | **85.45** | **11.703** | §15／§31，三扇門全過，**可引用** |
| S1 空轉探針（凍結池、錯輸出） | 40.2 | 24.88 | §3，**VOID，只當上限探針** |
| ＋ 里程碑地圖的 fill 回補（12.0 ms） | **52.2** | **19.2** | `MILESTONE_MAP_RECHECK:142`：`48.2 + 3.96` |
| ＋ 本節用交付 cell 實測的 IO 重算回補（9.96 MiB ÷ 1.2 GiB/s = 8.3） | 48.5 | 20.6 | 本節（§二） |
| ＋ B 的靜態 acc 補丁（J2 = 320 op × 5–10 µs） | **50.1–51.7** | **19.3–20.0** | 交付 cell `n_layer=40`/`k=8` |

兩條獨立算路徑（地圖的 `12.0 ms` 與交付 cell 的 `8.3 ms`）落在同一個帶上，
且都指向同一個結論：

> **實際回收值 ≈ 11.7 → 19 t/s（+7.3；區間 18.0–20.0）**，
> 也就是 45.45 ms 的缺口**打掉 ~33 ms、還剩 ~12 ms**，
> 而 `exp-prodnew-decode20-g4miss.targets.decode_tps = 20.0` 正好壓在帶的**上緣**
> ——**不保證過**。

三個必須一起講的限制：

1. **這條路不是 25 的路。** 它與 `e-decode25-blocks` 的 `sg-gap-boundary` **重疊**
   （那條槓桿就是「序列化」本身，§31 已把它判為槓桿失效）⇒ **兩者不可相加**；
   19 t/s 是「只做 S1 正確版」的落點，是 25 的**必要不充分**。
2. **`e-asyncfill-recompute` 的結案不涵蓋本節。** 那張卡結案的理由是
   「移動 fill 出臨界路徑只值 5.41 ms ⇒ +2.6~4.6% < MDD 8.5%」，那是在**仍然付序列化**的
   誠實臂口徑下量的；本節的價值來源是**序列化本身**（~33 ms），不是 fill。
   兩者是不同 regime ⇒ 不衝突、也**不構成重開**那張卡的條件
   （它的 `reopen_condition` 要求 ≥32 GB 盒 ＋ 重測 fill ≥8.5%，且明文禁止用髒窗重開）。
3. **一個待校準的口徑差**：§34 的分母是 **312 = 39×8**，而交付 cell 的實測是
   **`n_layer=40`、`k=8` ⇒ 320**（`e-fillbudget-m-decomp.targets` 明文更正「舊 cell 才是 41」）。
   312 是量具真的印出來的層數（40 層裡有 1 層沒有 map entry），逐位複現過 ⇒ **不改讀數**；
   但**報比例時兩個分母都要寫**：6.09%（/312）與 5.94%（/320）。

### 六、可引用性與產物

* **可引用**：本節全部是既有實測的**組裝**（交付 cell 的 `n_layer=40`/`k=8`/`n_sum=320`、
  `MiB/miss=0.524`、有效吞吐 1.2 GiB/s、fill p50 5.41、J2 = 320 × 5–10 µs、
  席位掃描 96.0/96.7%）；`:2180-2262` 的分支可達性判定；`run_server.sh` 的白名單現況
  （`CGC_SPAC_*` 在、`CGC_PREFETCH_SRC` 不在、`CGC_PREFETCH_WINDOW` 在）；
  地圖的三列（`138`／`141`／`142`）。
* **不可引用**：上表**任何一列當成已達成的速度**（24.88 是錯輸出探針；19.2 是**上限**；
  本節**沒有跑任何新場次**）；把 J2 的 1.60–3.20 ms 當成已量到（它是 5–10 µs/op 的**推算**，
  該數字本身是推算）；把 `MiB/miss` 0.524 與 1.083 的 2× 差異當成已解釋（成因未查）。
* **仍未查**（本節結出來的）：① acc 子圖的 `ggml-alloc` buffer／zero-fill 成本；
  ② SpAc 的 K／alpha／refresh 對殘差的敏感度（P2 的主端點）；
  ③ `MiB/miss` 交付 cell 0.524 vs 舊 cell 1.083 的成因；④ 312 vs 320 那一層是誰。
* **沒有新產物**（本節 0 跑）；引用的是 `Backup/g4miss_2026-09-29/miss_dist_delivery_gen128_fix4066.json`、
  `Backup/g4miss_2026-09-29/g4miss_delivery_harness_fix4066.{json,stderr.log}`、
  `Backup/mtp_off_clean/res_2026-09-29c.json`、`Backup/mtp_off_clean/res.json`。
* **沒有動 `src/`、沒有 commit**；`docs/mindmap/` 沿用 §34 的註記（未撤）。

---

## 36. §35 P2 跑了：G4 殘差**壓不下去**，而且 §34 的 6.09% 已經在旋鈕的**最佳點**上（交付 cell，5 臂）（2026-09-29）

§35 §四 P2 寫的驗收是「殘差 6.09% → ≤3.7%（席位掃描地板）」。本節是那一跑。
結論是 **Q1 = NO**，而且不是「差一點」，是**方向本身是懸崖**：把預取餵得更多會讓池子
**永久崩潰到 100% miss**；餵得更少則 miss 翻倍。**K=8 是這個旋鈕的最小值，不是它的上限。**

### 一、控制臂先證明今天的盒子忠實複現 §34

同一格、同一 arm 字串、同一分母（`misses/312`），五個窗口都比：

| 窗口 | §34（17:55） | 控制臂 K=8（今天 19:0x） |
|---|---|---|
| med（全部 385 個 decode compute） | **0.0609** | **0.0609** |
| IQR | 0.0385 / 0.0865 | 0.0385 / **0.0897** |
| med（跳過暖機 64） | 0.0513 | 0.0545 |
| `sum_misses` | 9210 | 9318（+1.2%） |

⇒ 差異落在複現帶內（控制臂多 108 個 miss、IQR 上緣多 0.003）。**下面每一列都讀在同一把尺上。**

### 二、五臂（交付 cell；`--prompt 0 --gen 128 --depths 512 --reps 3 --warm-skip 64 --batch 512`）

| 臂 | feeder | med（全部） | IQR | med（跳過 64） | `sum_misses` | `prefetch` queued/**dropped** | `file_reads` | `pread_usec` | `wall_s` | 崩潰 onset |
|---|---|---:|---|---:|---:|---|---:|---:|---:|---:|
| **K=4** | SpAc | 0.1282 | 0.1026/0.1571 | 0.1186 | 16 479 | 568/**19** | 1 704 | 0.91 s | 23.9 | 沒有 |
| **K=8**（控制） | SpAc | **0.0609** | 0.0385/0.0897 | **0.0545** | 9 318 | 2 477/**39** | 7 431 | 2.81 s | 23.9 | 沒有 |
| **K=32** | SpAc | **1.0000** | 1.0000/1.0000 | 1.0000 | 117 624 | 18 568/**447 579** | 38 998 | **14.64 s** | 28.2 | step 16 |
| **SPAC=0 ＋ hist W4** | union/hist | **1.0000** | 0.9968/1.0000 | 1.0000 | 118 595 | 25 744/**444 610** | 60 597 | **14.30 s** | 26.8 | step 11 |
| （§34 參考） | SpAc | 0.0609 | 0.0385/0.0865 | 0.0513 | 9 210 | 2 405/39 | 7 215 | 2.64 s | 23.7 | — |

⛔ **本表所有 t/s 一律 VOID**：五臂都是「單次提交、無重算」的錯輸出臂（架構如此，非意外）。
主端點是 `misses/312` 的**中位數**（計數）⇒ 髒窗仍可裁判。`attribution`：K=4 那臂是
**`none`**（乾淨窗，本輪唯一）；其餘 `swap`。`instrument_gate` 五臂全 **BOUND**。

### 三、崩潰的機制：**磁碟先被打滿，不是池子裝不下**

兩個「餵更多」的臂走的是**兩條不同的餵料路徑**（SpAc EMA ／ union-hist 窗），卻給出
**同一個崩潰**：100% miss、onset 在 step 11–16、dropped 44 萬。共同量只有一個——**落地面積**：

* `prefetch` 的 queued 由 2 477 → **18 568／25 744**（7.5–10×），dropped 由 **39 → 447 579／444 610**；
* `file_reads` 7 431 → **38 998／60 597**；`pread_usec` 2.81 s → **14.64／14.30 s**——
  而整臂只有 24–28 s ⇒ 磁碟幾乎整場滿載；
* 那麼多請求裡落地的比例是 **4% 與 5.5%**（18 568/466 147、25 744/470 354）。
* 池子的席位數**從頭到尾沒變**（143／層）⇒ 崩潰不是容量問題，是**進不來**。

⇒ 排進去的位元組在需要之前落不了地，池子最後持有的**全是錯的專家**（不是「少數正確的
＋多數缺的」，是 0 個正確的）。這是**懸崖**，不是斜率：K=8→32 之間沒有中間失敗模式。

**另一側同樣清楚**：K=4 餵得少一半（568 queued、0.91 s pread）⇒ miss **翻倍到 12.82%**。

### 四、正面證據：殘差不是「沒被預測到」，是「預測到的也留不住」

控制臂自己的成員探針（`CGC_SPAC_DBG=1`，K=8，`feeds=39`）：

```
CGC-SPAC-MEM: layer=0 topK=8 resident=8 (100%) queued=0
CGC-SPAC-MEM: layer=1 topK=8 resident=4 (50%)  queued=4
CGC-SPAC-MEM: layer=2 topK=8 resident=4 (50%)  queued=8
CGC-SPAC-MEM: layer=3 topK=8 resident=4 (50%)  queued=12
```

⇒ **utility top-K 自己就有一半不在池裡**，而 `n_prefetch_dropped` 整場只有 39。
這兩件事放在一起只能是同一個結論：**丟失發生在池子的駐留期，不在預取的投遞**。

把它與誠實臂的 **1.97%**（`e-fillbudget-m-decomp.result_p1`，同一交付 cell）對照：
缺席的專家**是拿得到的**——誠實臂會停下來等，S1 臂不等。所以 6.09% 就是
**「等／不等」的價差**，不是「資料拿不到」的價差。這正是 §35 §二 那句
「B 不是 IO 的替代品」的另一面：**連 A 也不是 IO 的替代品**。

### 五、裁決與路由

* **Q1 = NO**：殘差沒有被壓到 3.7% 的地板，而且**沒有一條可用的方向**——
  往上（K=32／hist）是崩潰，往下（K=4）是翻倍。§35 §三 那個「6.09% 是地板的 1.6–1.8×，
  所以 A 還有一步可走」的推論**被本節推翻**：那個差距不是預算，是**駐留期**。
* 依 `e-s1-predict-recompute.acceptance.falsify` 分支① ⇒ **`sg-a-residual` 結案 FAIL（附機制）**，
  不再掃旋鈕，直接進 **P4（B：靜態 acc 補丁，`ggml_acc`）**。
* ⛔ **不得用「換一個 feeder 再試一次」重開本項**：兩個不同 feeder 在同樣的落地面積下
  給出同一個崩潰 ⇒ 這已經是 mechanism-level 的判死，不是參數沒調好。
* ⚠ 這一跑也**順手關掉一條替代路線**：`CGC_PREFETCH_SRC=hist` 曾被記為
  「opt back in for A/B」的候選（`run_server.sh:1482-1486`）。現在有了讀數：
  **它在單次提交臂上是崩潰的**，不是候選。

### 六、附帶的樹上改動（3 行，已驗證中性）

為了跑 hist 臂，`scripts/run_server.sh` 補回 `CGC_PREFETCH_SRC` 的**白名單接回**
（§4 施工清單第 2 條），只推值、不推就不送。
**已驗證它不移動任何沒要它的 profile**：`matrix.resolve('prod-new', {})` 仍是
`CGC_PREFETCH_SRC=None`、`CGC_SPAC='1'`（與修改前一致）；`bash -n` 通過。
**沒有動 `src/`、沒有 commit。**

### 七、可引用性與產物

* **可引用**：§一 的複現（五個窗口）；§二 的四臂 `med`／IQR／`sum_misses`／
  `prefetch` queued-dropped／`file_reads`／`pread_usec`／`wall_s`／崩潰 onset；
  §四 的 `CGC-SPAC-MEM` 四行與 `n_prefetch_dropped=39`；五臂 `instrument_gate=BOUND`。
* **不可引用**：本節任何 **t/s**（五臂皆錯輸出臂；四個 `attribution=swap`）；
  把 `sum_misses` 當成「池子裡有幾個專家」（那是整個臂的累計，不是瞬時狀態）。
* **仍未查**：① 崩潰臂的 `pread` 到底是**頻寬**滿還是**佇列深度**滿（`file_reads` 的
  per-read µs 由 378 µs 升到 236–375 µs ⇒ 傾向頻寬，但沒有 ssd 側讀數）；
  ② `sg-g3-armed`（P3）與 P4 未跑。
* 產物：`Backup/spac_sweep_2026-09-29/`（`k_sweep_{C,T1_K32,T4_K4}.json`、`hist_w4.json`
  ＋各自的 `.stderr.log` 成對 log ＋ `{C,T1,T4,H1}.stdout.log`；
  分析 `miss_{C,T1,T4,H1}.json`）。驅動：`harness bench`，charter
  `scripts/check/charters/e-s1-predict-recompute-2026-09-29.yaml`（結果寫在
  `targets.result_p2_q1`）。

---

## 37. 以**段數**為軸重拆 25 t/s：把段數、每段稅數清楚之後，這條軸上**沒有獨立槓桿**（2026-09-29）

§31 把 `e-decode25-blocks` 判成「以 ms 為軸的拆解不成立」，並依該卡自己的 `acceptance.falsify`
把下一站指定為「**以段數為軸重拆**」。本節是那一站。它不需要新跑：段數由一行程式決定、
每段稅由既有產物可數、而這條軸上**唯一被量過的介入**已經量過了（§19 §六 layer 1），
本節把它獨立複現。

### 一、先數段數：**每步 41 段**（40 層 argsort ＋ 1 條尾巴），其中 **40 段帶 GPU buffer**

段數不是旋鈕，是一行程式（`ggml-backend.cpp:1810-1845`）：

```c
const char * bd_prefix = use_topk_bd ? "ffn_moe_topk-" : "ffn_moe_argsort-";
int n_as = 0;
for (i in nodes) if (name starts_with bd_prefix) n_as++;
if (n_as == 0) { ggml_backend_graph_compute_async(...); }        // ← 單次提交，沒有逐段迴圈
else { ... int n_segs = n_as_found + 1;  for (i = 0; i < n_segs; i++) { hook_seg(i); submit(i+1); } }
```

⇒ **`n_segs` = 圖上 `ffn_moe_argsort-<layer>` 節點的個數 ＋ 1（尾巴）**。交付 cell 實測：

| 量 | 值 | 出處 |
|---|---|---|
| `DECPROF segs=`（真正的 `n_segs`） | **41**（53/53 個抽樣 decode 步） | `gapsplit2`、`delivery_base` |
| `GPUTIME segs=`（**有** GPU buffer 的段數） | **40**（150/150 列） | 同上 |
| 兩個數差 1 的原因 | `gt_nseg` 只在 `gns > 0` 時 ++（`:2176`）⇒ 第 41 段**沒有 Metal buffer** | 源碼 |
| **可量的邊界數** | **40**（第 0 段沒有前一段 ⇒ `gap_0 ≡ 0`，§19 §三「L0 = 0.00」）| `ggml-backend.cpp:2179-2180` |
| 單次提交臂 | **`segs=1`**（`gap_sweep` 20 列）⇒ 那一臂的池子路徑**沒跑** | §19 §七 |

⚠ **一個脆弱的耦合要寫下來**：段數是**字串前綴比對**出來的（`ffn_moe_argsort-`），不是由資料依賴
推導的。任何改名（含 GPU-lookup 路徑的命名）都會**靜默地**改變分段；`CGC_TOPK_BOUNDARY=1`
就是靠換前綴來複現舊的 VIEW 邊界。⇒ 讀 `segs=` 之前要先確認那一場的前綴是哪一個。

### 二、每段稅：**每段 0.342 ms**，其中 **42% 沒有儀器**

> **⚠ §38 原地更正（2026-09-29 19:2x）**：這一節的「42% 沒有儀器」**描述撤銷**。
> §38 用三個時戳把邊界窗的 CPU 端整段封起來後量到：該 42%（0.1545 ms/邊界）**不在 CPU 的
> 工作視窗裡**，它是 Metal 側的**完成通知延遲 ＋ commit→起跑延遲**（與 CPU 編碼時間 r=−0.00、
> 近乎每事件常數 CV=0.11）。下表與本節其餘數字不變，只有**歸屬**改了。

同一場、同 48 個 ntok=1 的穩態步（`gapsplit2`，`CGC-DECPROF` join `CGC-GPUTIME` 逐 step）：

| 層級 | 量 | 值 |
|---|---|---|
| **每步** | `total` 77.03／`wait` 67.99／`cb` 4.62／`submit` 3.46 ms |（`gap` 14.02、`union` 72.19，GPU 鐘）|
| **每邊界** | `gap` = 14.02 ÷ 40 = **0.342 ms** |（§24 兩場獨立量到 0.356／0.364）|
| 其中可歸屬 | `cb` 0.113 ＋ `submit` 0.084 = **0.197 ms（58%）** | |
| **其中沒有儀器** | **0.145 ms（42%）** | ~~正是 §19 §六 結論 3 缺的那段 CPU 側切分（encode 下段／commit）~~ **§38 更正：不是 CPU 工作**——是 `end_i→poll 觀察` 的通知延遲 ＋ `commit 返回→start` 的 launch 延遲（§38 §四）|
| 量具自己在窗內 | `take` 0.010 ms/步 = **`gap` 的 0.07%** | ⇒ 不是尺造成的（§24 §一 複現）|
| 主執行緒的活動 | `poll_iters` 643 032/步 ÷ 40 = **15 684 次/邊界**；`wait`/邊界 1.70 ms ⇒ ~107 ns/次 | ⇒ CPU **整步在忙等**，`gap` 是它「該去提交下一段」的那個窗 |

### 三、這條軸上唯一被量過的介入，**量到 ~0**（本節獨立複現 §19 §六 layer 1）

§19 用「把段的起點畫在一條 1-node buffer 上」（`CGC_CB_N_MAIN=1` ＋ `CGC_SERVER_N_CB=16`）
去攻這個稅。同一 default cell、96 個穩態步的中位：

| 臂 | `segs` | `bufs` | **`gap`** | **`union`** | `gap+union` | `wait` |
|---|---:|---:|---:|---:|---:|---:|
| `ops_default`（n_cb=8） | 40 | 360 | **15.32** | 80.31 | **95.63** | 74.92 |
| `ops_pernode`（`CB_N_MAIN=1`, c16） | 40 | **679** | **2.25** | **91.45** | **93.70** | 69.72 |

⇒ 佈局改動**拿掉了 13.07 ms 的 `gap`**（15.32 → 2.25），同時**加回 11.14 ms 的 `union`**
⇒ **淨 −1.93 ms**，而 `bufs` 幾乎翻倍（360 → 679）。§19 同場的步時是 82.33 → 81.19（−1.14 ms）。

**這是本軸的核心事實**：邊界窗**不是空的 GPU 時間**，而是**被挪動位置的 CPU 側工作**——
把段的起點畫早一點，同一段牆鐘時間就出現在 `union` 裡。
⇒ 「把 `gap` 降下來」**不等於**「把步變快」；本軸上量到的淨值就是 **~1–2 ms（噪音帶內）**。

### 四、另外兩條路的量測結果（都是「不是槓桿」）

| 手段 | `gap` | 步 | 代價 |
|---|---|---|---|
| `CGC_SUBMIT_AHEAD=1`（刪掉觀察點） | 量不到（`(NO TIMESTAMPS)`） | 75.91 → **58.97（−22.3%）** | racy／輸出 garbage；與它的錯是同一件事（§30）；**prefill 1.6–1.8× 慢**（交付的另一軸）|
| `CGC_SEG_BATCH=1`（41 段 → 1 段） | 消失（`segs=1`） | 該臂 tg 16.15（**不可引用**） | 池子路徑**一次都沒跑**（reads 0）⇒ 是「把機制拿掉」的天花板 |

### 五、裁決，以及 §16 §四 那張表要改哪裡

* **這條軸上沒有獨立槓桿。** 三個可能方向各自被量掉：佈局（**守恆**，~1–2 ms）、
  刪觀察點（**＝錯**，且付 prefill）、刪機制（**＝S1**，是另一條路不是本軸的槓桿）。
  而**段數本身推不動**：`n_segs = argsort 節點數 ＋ 1`，每個 argsort 都需要一個邊界
  把 top-k 結果拿回 host 寫 remap leaf，而第 L+1 層的 argsort 又依賴第 L 層的輸出（GPU）
  ⇒ **每層一個邊界是資料依賴，不是調參**。
* ⇒ `e-decode25-blocks` 的 `sg-gap-boundary`（§31 已判「未命中（槓桿失效）」）**結案為 FAIL（附機制）**，
  且該卡 `on_fail` 指的那個替代軸（段數）**現在也走完了、同樣沒有槓桿**。
* ⛔ **§16 §四 的示範行要改寫**：那一列「兩者都做，且空轉/圖外/填充各減半 ⇒ 36.2 ms ＝ 27.6 t/s」
  裡的「**空轉減半（−7 ms）**」**沒有實測支撐**——本軸量到的上界是 **~2 ms**。
  以 2 ms 取代 7 ms，同一列變成 **41.2 ms ⇒ 24.3 t/s**，而不是 27.6。
* ~~**仍未查（本軸的唯一殘項）**：那 42% 沒有儀器的邊界窗（0.145 ms × 40 ＝ **5.8 ms/步**）。~~
  **已查（§38）**：0.1545 ms/邊界 ＝ **非 CPU** 的啟動／通知延遲；CPU 封包只佔 gap 的 58.5%。
  它唯一量過的開採方式（§19 `ops_pernode`，gap 2.25 ms/步）**淨 −1.93 ms**。
  它的預註冊測試是 §24 §四 的 **H-spin**（只改等待策略、不動資料流）。但 §三 的守恆結果
  已經**上限住**它的報酬：窗可以被清空（15.32 → 2.25）而步只動 1.1 ms
  ⇒ **H-spin 的正面上限就是那 ~2 ms**，除非守恆本身被推翻。⇒ 執行與否是資源決定，不是資訊缺口。

### 六、可引用性

* **可引用**：§一 的 `n_segs = n_as_found + 1`（源碼）與 41／40 兩個讀數的**差一**原因
  （`gt_nseg` 只在 `gns > 0` 時 ++）；§二 的每邊界 0.342 ms 與 58%／42% 切分、
  `take`＝0.07%、`poll_iters` 15 684/邊界；§三 的守恆表（95.63 → 93.70）；§四 兩列。
* **不可引用**：`CGC_SEG_BATCH=1` 臂的任何 t/s（16.15，池子沒跑）；跨 cell 相減
  （`delivery_base` 的 gap 18.97 是**不同 cell**，不得與 default 的 15.32 相減）。
* **產物**（全部既有、本節 0 跑）：`Backup/stepbudget_2026-09-29/gapsplit2.stderr.log`（join 用）、
  `ops_default.stderr.log`／`ops_pernode.stderr.log`（守恆對照）、`gap_sweep.stderr.log`（`segs=1`）、
  `delivery_base.stderr.log`。

---

## §38 邊界窗那 42%：是 **Metal 側的延遲**，不是「沒被量到的 CPU 工作」

> 問題（§37 留下的唯一殘項）：`gap - (cb+submit) = 0.145 ms/邊界 × 40 = 5.8 ms/步`
> 被寫成「**沒有儀器的 CPU 切分**」。本節把它量起來，並**推翻那個描述**：它不在 CPU 的
> 工作視窗裡。改動：`ggml/src/ggml-backend.cpp` **ADD-only** 6 行 + 1 個欄位群
> （`bdcpu/bdhook/bdsub/gap_not_cpu/bdcpu_of_gap`，掛在既有的 `CGC_GPU_TIMING` 下，預設行為不變）。

### 一、儀器：把邊界窗的 CPU 端整段封起來

`gap` 是 **GPU 時鐘**量（`start_{i+1} - end_i`，兩個端點都來自 command buffer），
而 `cb`/`submit` 都是 CPU 側各自一小段 —— 中間那段沒人認領。三個時戳把它封成一個**連續**的封包：

| 時戳 | 位置 | 意義 |
|---|---|---|
| `gt_st1` | `hook_seg()` 的 poll **剛觀察到第 i 段完成**的那一刻 | CPU 最早可能知道 `end_i` 的瞬間 |
| `bd_t0` | hook 返回（`take` + `cb` 之後） | |
| `bd_t1` | `submit_seg(i+1)` 返回（＝encode+commit 完成） | |

⇒ `bdcpu = st1 → submit 返回`（**整個 CPU 活躍窗**）、`bdhook = st1 → 返回`、`bdsub = 返回 → submit 返回`。
記帳條件是 `!submit_ahead && i+1 < n_segs`，**只在上一個動作是 hook 的那一輪**入帳
（`i = 0..n_as_found-1`），所以不存在跨邊界的殘留區間；`submit_ahead` 的診斷臂**構造上不會被記**。

**關鍵構造性質**：`bdcpu` 與 `gb_hook/bdsub` 是**同一段區間的兩段切分**，而該區間就是邊界裡
全部的 CPU 工作（poll 之後 → commit 返回）。所以 `gap - bdcpu` **在定義上不含此迴圈任何未量到的 CPU 工作**。

### 二、控制臂逐位複現（同一支臂、同一 cell、同一環境變數）

| | `gapsplit2`（10:26，本節前） | `bdenv`（19:22，本節） |
|---|---:|---:|
| arm / cell | `prod-new:CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1`、default、p2048_n128_d512_r3 | 同 |
| ntok=1 穩態步 | 48 | 48 |
| **`gap` 每步中位** | **14.015 ms** | **14.875 ms** |
| 每邊界（÷41） | 0.342 | 0.3628 |

⇒ 控制臂在 `gap` 上與 §37 同尺（+6%，今日機器在換頁：pagein 16.6k/s、swap 10.2 GB）。

### 三、結果：邊界窗現在**全部有名字**

ntok=1、48 步；中位（ms/步 → 每邊界 ÷41，與 §37 同慣例）：

| 項 | ms/步 | ms/邊界 | 佔 gap | 性質 |
|---|---:|---:|---:|---|
| **`gap`**（GPU 閒置） | 14.875 | 0.3628 | 100% | GPU 時鐘 |
| ├ **`bdcpu`**（CPU 封包） | 8.699 | 0.2122 | **58.5%** | **CPU** |
| │ ├ `bdhook`（poll→take+cb） | 5.199 | 0.1268 | 34.9% | CPU |
| │ └ `bdsub`（encode+commit） | 3.473 | 0.0847 | 23.3% | CPU |
| └ **`gap_not_cpu`** | **6.336** | **0.1545** | **42.6%** | **不是 CPU 工作** |

**兩支獨立量具互證**（同一場、同一組 48 步）：`DECPROF submit=3.495` vs `bdsub=3.473` ⇒ **差 −0.6%**；
`DECPROF cb=5.190` vs `bdhook=5.199` ⇒ 差 +0.2%；`bdcpu − (cb+submit) = 0.014 ms/步` = `take` 自己（0.010）。
⇒ 封包的口徑被兩套儀器交叉鎖住，不是自證。

### 四、判別：為什麼不是 CPU 工作

| 判準 | 觀測 | 讀法 |
|---|---|---|
| 與「CPU 編碼時間」的相關 | `gap_not_cpu` vs `bdsub` **r = −0.00** | **零相關**：殘項與編碼工作量無關 |
| 離散度 | `gap_not_cpu` CV=**0.11**（IQR 6.08–6.66） vs `gap` CV=0.38、`bdcpu` CV=0.59 | 近乎**每事件常數** ⇒ 事件驅動的延遲，不是負載驅動的工作 |
| 尺度 | 殘項 0.1545 **> 整個 encode+commit** 0.0847 | 若是 CPU 工作，會比它想解釋的整段還大 |
| 構造 | 封包是**連續**的（st1→submit 返回） | 迴圈內**不存在**未被蓋到的 CPU 區間 |

殘項在兩個時戳端點**之外**，所以它只能是這兩段的合：
(a) `end_i(GPU) → st1(CPU)`：Metal **完成通知**的延遲（poll 是 ~107 ns 的 `sched_yield` 緊迴圈，
    它只負責「多快知道」，不負責「為什麼還不知道」——CPU 在該區間是**空轉**，不是在做工）；
(b) `submit 返回(CPU) → start_{i+1}(GPU)`：commit 之後到 GPU **真正開始**跑該段之間的 **launch 延遲**。
⇒ **兩者都是驅動／佇列側，都不是「少插一支時戳的 CPU 工作」。**

### 五、它能不能被藏掉：§19 是這題的**不受時鐘偏移影響**的錨

上面 (a)(b) 的切分需要跨時鐘域對齊（`gap` 是純 GPU 時鐘、CPU 時戳是 mach 時基），所以
「0.1545 裡 (a) 與 (b) 各佔多少」**本節不宣稱**。但有一個**不需要跨域**的獨立事實：

`ops_pernode`（`CGC_CB_N_MAIN=1`、c16）那一臂的 `gap` 曾被壓到 **2.25 ms/步 = 0.055 ms/邊界**——
**比 0.1545 低 2.8 倍**，而它**也是同一個純 GPU 時鐘量**。所以：

1. 這 0.1545 **不是固定的每 commit 成本**（否則更多的 commit 只會更大，不會更小）；
2. 它是「**沒被藏起來的那一部分**」——由 `poll → 讀回 → encode → commit → 起跑` 這個**序列**造成，
   CPU 一旦跑到 GPU 前面，它就縮；
3. 而 §19 同時量到那次隱藏的代價：`union` **+11.14 ms**、`gap` **−13.07 ms** ⇒ **淨 −1.93 ms**。
   ⇒ 名目上 6.3 ms/步 的殘項，**目前唯一量過的開採方式只付得起 ~2 ms**（守恆，§19 §六）。

### 六、更正 §37 的一句話（同節內原地改）

§37 §二 把 0.145 ms/邊界 寫成「**42% 沒有儀器** ＝ §19 缺的那段 CPU 切分」。**該描述撤銷**：
現在整段都被蓋到了，而且蓋到的部分裡**沒有**這 42%——它是**非 CPU** 的啟動／通知延遲。
連帶後果：

* 「再往邊界窗裡塞 CPU 儀器」這條路**關閉**（沒有 CPU 工作可找）；
* §16 §四 的示範行**不動**（唯一殘項 42% 的正面開採上限仍是 §19 的 ~2 ms ⇒
  `41.2 ms / 24.3 t/s` 仍是該行該有的數字）；
* 要動這 6.3 ms/步，只能動**序列本身**（讓 CPU 跑到 GPU 前面），而這正好是
  `CGC_SUBMIT_AHEAD`（racy／garbage）與 `CGC_SEG_BATCH=1`（S1 那條路）**已經各量過一次**的位置。

### 七、產物與樹上改動

* 跑：`harness bench --arm 'prod-new:CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1' --prompt 2048 --gen 128 --depths 512 --reps 3 --warm-skip 64 --fixed-fill-seed 1 --charter scripts/check/charters/e-decode25-blocks-2026-09-29.yaml --json Backup/stepbudget_2026-09-29/bdenv.json`
  （thermal `NOMINAL`@19:22:16、全場 NOMINAL 155 樣本；**attribution=swap ⇒ t/s 一律 VOID**，本節不引用任何速率）
* 產物：`Backup/stepbudget_2026-09-29/bdenv.{json,stderr.log,stdout.log}`（＋`.json.logs/live/<tag>.stderr.log`）
* 樹上：`src/llama.cpp/ggml/src/ggml-backend.cpp` **ADD-only**（3 個時戳 + 3 個累加器 + 1 組列印欄位），
  `scripts/check/charters/e-decode25-blocks-2026-09-29.yaml` 新增 1 條探針臂；**未動 `llama-context.cpp`、未 commit**。

---

## §39 25 t/s 的槓桿再盤點，與「必須為真」的條件清單（2026-09-29）

> **一句話**：ms 軸（§31／§37／§38）與段數軸（§37）都已量空 ⇒ **25 t/s 目前沒有任何已量到的槓桿**；
> 而把每一格放在**它自己的已量上界**上相加得到 **41.2 ms ＝ 24.3 t/s ＜ 25**。所以這個目標現在的性質
> 不是「還沒做完」，是**「以現有的軸加總到不了」**。本節 **0 跑**：全部唯讀，讀自 22 張卡、
> 臂台帳 17 列、`MILESTONE_MAP_RECHECK` §5.2、以及本檔 §1–§38。

### 一、門檻與基準（先把算術釘住）

| | 值 | 出處 |
|---|---:|---|
| 目標 | **25 t/s ⇔ 40.0 ms/token** | `e-decode25-blocks.targets.decode_tps: 25.0` |
| 基準（本卡宣告） | **11.703 t/s ⇔ 85.45 ms** | §15，`Backup/mtp_off_clean/res_2026-09-29c.json`（attribution `none`、NOMINAL）|
| 缺口 | **45.45 ms** | §16 |
| 另一條線的交付基線 | 12.57 ⇔ 79.6 ms ⇒ 要砍 **49.6%** | `MILESTONE_MAP_RECHECK:141`（**口徑差未裁**）|
| prefill ≥ 250 | ✅ **已過線**（9 次 launch ≥250，最高 296.24、乾淨視窗 283.01）| `MILESTONE_MAP_RECHECK:138` |

⚠ 里程碑 `① 攻關成功` 的條件是**兩件事同時**：`pp ≥ 250` **且** `tg > 12.57`（同一次量測）。
現況停在 **②**（250 達標、decode 未超越）。

### 二、把「已量到」按**可回收性**分四級（這一欄才是槓桿清單）

**① ③b 可放生產——正確性使能，速度 0**（它們的價值是「不擋路」，不是「變快」）

| 列 | 實測 | 產物 |
|---|---|---|
| 3a `CGC_MISS_MASK`／`CGC_ZERO_MISS` | 逐位元與 host 相同（38 層／421 元素／0 差異）＋ Δ = **−0.95 ± 2.01 ms** ⇒ 過線（**§42 §一 就地更正：原始判決是成本 UNRESOLVED**——SE 1.027、95% CI [−2.96, +1.06]、median **+0.59**、門檻 0.2 ms） | `Backup/miss_axis_mtpoff_ws64_r3/miss_axis_res.json` |
| S1 數值身分（`CGC_SLOT_TABLE_GPU` 單獨） | 指紋 **576/576 全同**、`answer_md5` 相同 | `docs/S1_LINE_VERDICT_2026-09-25.md`（**本臂不可報吞吐**）|
| swap 結構修復（L0–L4+P0） | `swap launch 0`、decode 11.49、NOMINAL | **執行線主張，本線未複核** |

**② ③a 有量到值，但不可交付**（錯輸出臂或探針場）

| 列 | 實測 | 為什麼不能換 t/s |
|---|---|---|
| S1 單段提交 | A **11.30** → B **20.73**（**−40.3 ms**、45.5%）| 同一個開關讓 `HOOK=0`、`file_reads=0`（權重永不進池）⇒ garbage、無 `answer_md5` |
| `CGC_EB_NOFILL` | `fill_batch` **3.955 → 0.206 ms/step（−95%）** | 輸出天生 garbage ⇒ **合法產物是差值**，不是 t/s |
| k-sweep（MTP） | `step ≈ **14.61 + 42.03·T** ms`（max resid 11.5）| 模型可移植、**最佳 k 不可移植**；交付口徑是 **MTP off** |
| **G4 權威 cell（§34）** | 中位 **6.09%**（IQR 3.85–8.65，**19.0 個/step**）≤25% ⇒ **閘門過** | 天花板 **19.2 t/s**（見 §四）；且 P2 已否證（§36）|
| 交付 cell 誠實臂（§35 §二） | miss **1.97%**、fill **p50 5.41**／p90 12.72、`MiB/miss` **0.524**、有效吞吐 **≈1.2 GiB/s** ⇒ 9.96 MiB/step ＝ **8.3 ms/step** | 這是**已在付**的 IO（誠實臂會停下來等），不是可回收值 |

**③ 量到「上界」但前提已被否證**（數字有效，歸屬失效）

| 列 | 實測 | 否證它的 |
|---|---|---|
| 段間 `gap` 的可消滅量 | §19 tier 2 **−16.9 ms**（submit-ahead）| §30／§31：racy 順序本身；護欄後仍 `hit 55.8%` ⇒ **錯就是它的 −40.63 ms 的同一件事** |
| top-k remap 移到 GPU（E1） | 對 leaf 的改動 **值 0 ms**（bit-identical）| §30（值 0 ⇒ 不是槓桿）|
| 圖外那 11.6 ms | **`llama_synchronize` 8.858 ms ＝ 殘差 80%**（§27）| 有儀器、**無槓桿、無業主** |

**④ ④ 廢棄（量到「不成立」）**：`CGC_LAYER_AHEAD_PREFETCH` −7.1%；M-W（WORKERS 8→2）`us/job` −11.1% 但 **t/s −0.6%**（順帶得 IO ≈9%）；3b 合併 pread **182.6×**；3c 依賴 3b；逐 op kernel 數 × GPU 時間的 **join 不成立**（§22）；逐 dispatch GPU 時間**在 M4 上是關的**（§23）；`mul_mat_id` 的 `map0` 併入＝**0 個 dispatch/步**（§21）；8 專家併一 dispatch＝**已是現況、再併更慢**（§17）；元素級六族**無 ≥3 ms 單點**（§18 §七）；**ms 軸**（§31／§37／§38）；**段數軸**（§37）。

### 三、25 t/s 必須為真的條件（C1–C7）

每一條都寫成「**條件／現在的狀態／若為假則**」，並附上**可判定的量法**。

**C1（算術封頂，最硬的一條）**
條件：四塊同時命中時，總和必須 < 40.0 ms。
現況：代入**各自的已量上界** ⇒ `19.5 ＋ 12.0 ＋ 5.8 ＋ 3.9 = 41.2 ms ＝ **24.3 t/s**`
（§16 §四 那一列原本用「空轉減半 −7 ms」⇒ 36.2 ms／27.6 t/s，而 §37 已證那 −7 ms **沒有實測支撐**，
上界只有 ~2 ms）。
若為假則：**至少有一格必須超過它自己的已量上界**，或**需要第六條軸**（見 C6）。
量法：只要有一場 `attribution=none`、NOMINAL、reps≥3 的交付 cell 量測 ≥25，C1 即被推翻。

**C2（SG-KERNEL：GPU 淨忙 55.4 → ≤19.5）**
條件：在**每 token 位元組數不變**（1.171 GB）下，有效頻寬要從量到的 **10–17 GB/s** 提到 **≥58 GB/s**
（要打到量測上限 108 GB/s 才得 10.8 ms）。
現況：**沒有已量到的承載算子**——§18 §七 明確寫「下一個 ≥3 ms 的目標不在元素級六族裡」，
而 §17（再併更慢）／§21（0 dispatch/步）／§23（M4 關）已把「併 dispatch」這條關掉。
若為假則：kernel 側單獨最多 22.6 t/s（§16 §四）。

**C3（SG-GAP：每步被**觀察**到的段邊界 40 → ~1）**
條件：`top-k` 的 leaf 寫入契約必須能**不經 CPU 往返**完成（第 L+1 層 argsort 依賴第 L 層輸出 ⇒ 每層一邊界是資料依賴）。
現況：**修法已被否證**——E1（把 table 放 GPU）值 **0 ms**；E2（改順序）**就是 racy 順序本身**，
它的 −40.63 ms 與它的錯是同一件事。§38 另外量到：邊界窗的殘差 42%（0.1545 ms/邊界）
**是 Metal 側的完成通知＋launch 延遲，不是 CPU 工作** ⇒ 「往邊界窗加 CPU 儀器」這條也關閉。
若為假則：`gap` 只能吃到 §19 守恆下的 **~2 ms**。

**C4（SG-OFFGRAPH：圖外 11.6 → ≤5.8）**
條件：`common_sampler_sample` 開頭那個 `llama_synchronize`（**8.858 ms**）必須從每 token 路徑移除；
它的註解寫明是**刻意的**（「in order not to measure any ongoing async operations」）⇒ 這是**產品成本**，不是量具假象。
**產品路徑確認為真（§40 §二）**：`tools/server/server-context.cpp:4170` → `common/sampling.cpp:611`
（`common_sampler_sample` 第一行）⇒ §27 §四 的「若產品沒有 ⇒ 11.703 低估產品」這個分支**不成立**。
現況（§40 §三 就地更正）：**不是「無業主」而是「無獨立槓桿、附機制」** ——
§38 的 `gap_not_cpu` = 0.1545 ms/邊界 把「等待本身」的上界壓到 **0.31 ms** ⇒
8.858 ms 的 **≥96.5% 是尾端 GPU 忙碌**，與 SG-GAP **同一支**，而 SG-GAP 已 FAIL ⇒ 這一條**不可滿足**。
若為假則：圖外那一格原封不動進 40.0 ms 的預算。

**C5（SG-FILLSUB：填充＋提交 7.8 → ≤3.9）**
條件：`fill` 的 **3.955 ms/step** 必須離開臨界路徑，**而池子仍然正確**。
現況：S1 量到它能被拿掉（−40.3 ms），但**同一個開關把正確性前提一起拿掉了**（`file_reads=0`）
⇒ 這兩件事**目前是同一件事**，尚未被分離。
若為假則：填充＋提交那 7.8 ms 只能動提交那 3.4 ms。

**C6（口徑／產品決策：唯一「乘」而不是「減」的軸）**
條件：若要靠「每步多 token」攤薄固定成本，必須先把 **MTP-on 變成交付口徑**
（`step ≈ 14.61 + 42.03·T` 已量到、可移植）。
現況：交付口徑是 **MTP off**（量測契約 §7）⇒ 在交付 cell 上**不可測**；
交付 cell 的 MTP-on 讀數 **UNRESOLVED**（`12.62` 已作廢），而生產 server ABBA 反而量到 **×0.695（−30%）**；
`CANDIDATE_SPECS_2026-09-27` 已裁定「要它回來是**產品決策**，不是本線槓桿」。
若為假則：C1 沒有第六條軸可用，25 只能靠「某一格超額」。

**C7（可引用性：門檻本身要先能量）**
條件：那 25 t/s 的那一場必須 `attribution=none`、NOMINAL、**reps≥3**、`--warm-skip 64`、
**paired stderr log**、同 build。
現況：§33–§38 的每一場都是 `attribution=swap` ⇒ 連「有沒有往 25 推」都**無法回答**（t/s 一律 VOID）。
若為假則：就算某次真的到 25，它也是 VOID。

### 四、天花板（20+ 那條路的算術，與 25 的關係）

| 列 | step | t/s | 出處 |
|---|---:|---:|---|
| 交付錨點 | 85.45 | 11.703 | §15 |
| S1 探針（錯輸出） | 40.2 | 24.88 | 台帳（VOID 上限）|
| ＋里程碑地圖的 fill 回補（12.0） | **52.2** | **19.2** | `MILESTONE_MAP_RECHECK:142`（`48.2 + 3.96`）|
| ＋交付 cell 實測 IO 重算（9.96 MiB ÷ 1.2 GiB/s = 8.3） | 48.5 | 20.6 | §35 §二 |
| ＋B 的靜態 acc 補丁（J2 = 320 op × 5–10 µs） | **50.1–51.7** | **19.3–20.0** | 交付 cell `n_layer=40`／`k=8` |

⇒ 兩條獨立算路徑（12.0 與 8.3）落在同一帶 ⇒ **11.7 → 約 19 t/s（區間 18.0–20.0）**。
**G4 路就算全做出來也到不了 25**（`decode_tps=20.0` 壓在帶的上緣 ⇒ 不保證過）。
⇒ 20+ 與 25 是**兩條不同的路**，且**不可與 §16 的五塊相加**（重疊項：序列化／fill／IO）。

### 五、結論：還剩下哪三格沒被判死

1. **G4／預測＋重算**（`e-s1-predict-recompute`）：閘門已過（6.09% ≤25%）、P2 已否證（殘差壓不下去）
   ⇒ 剩下 **P3（武裝 G3：`zero_slot>0`、`placeholder=0`）＋ P4（靜態 acc 補丁）**。
   它服務 **20+**，不服務 25（天花板 19.2）。
2. **邊界的唯一未開採形式**：**不刪邊界而削它**。§38 量到它是延遲 ⇒ 唯一的方向是「CPU 提前 submit」，
   而那正是 racy 的那個位置 ⇒ 需要的是一個**新機制**（讓 leaf 寫入不需要觀察），不是把 table 放 GPU。
3. **圖外那 8.858 ms 的同步點**（§27）：三格裡**唯一在拆解之外、而且是純 CPU 側**的一格，
   §25 §五 自己也把它列為「比 §23 那條被硬體關掉的路更便宜可修」。

⇒ **要讓 25 t/s 重新變成可判定的問題，只有兩條路**：
(a) 找到**第六條軸**（改變每 token 的 *bytes* 或 *steps*，而不是每步的 ms）；
(b) 讓某一格**超過自己的已量上界**（C2 的頻寬、C3 的邊界、C4 的同步）。
在 (a)／(b) 任一成立之前，**20+ 是唯一有量到閘門、有施工順序的路**。

### 六、本節的邊界（不可引用清單）

* 本節 **0 跑**、**不動 `src/`**、**不動 `docs/mindmap/`**、**未 commit**；所有數字都是**轉引**，出處逐列標明。
* 轉引的數字各自仍受它原本的引用邊界約束（例如 S1 探針自陳 garbage、G4 的那幾場是 swap）；
  本節**不新增**任何可引用的 t/s。
* C1–C7 是**條件清單**，不是預測；每一條都在第三欄寫了「若為假則」⇒ 任何一條被推翻都不需要重跑本節。

---

## 40. 四個未死子目標：缺的東西各不相同 ⇒「前進」也各不相同（2026-09-29）

### 一、先把「前進」定義在卡自己的條款上

`e-decode25-blocks-2026-09-29` 的 `acceptance.falsify` **已經觸發**：以各塊的**已量上界**代入，
`19.5 ＋ 12.0 ＋ 5.8 ＋ 3.9 = 41.2 ms ＝ 24.3 t/s` **> 40.0 ms** ⇒ **以 ms 為軸的這個拆解不成立**。
所以本節不列「再試哪個旋鈕」，只做 `on_fail` 指定的兩件事：

1. 把無槓桿的塊登記進 `NEXT_ACTIONS`，並註明**缺的是槓桿還是儀器**（本輪補做：
   `docs/NEXT_ACTIONS_2026-09-29.md`）；
2. 判定 `SG-KERNEL` 是否也無槓桿 —— 它決定 `m-decode25` 的判死**是否維持**、
   力氣是否轉 `m-total`（pp≥250 ∧ tg>12.57）。

⇒ 四個「沒被判死」的塊，**缺的東西不是同一種**，所以它們的下一步在**種類上**就不同：
一個缺**槓桿**（kernel）、一個缺**分離**（fillsub）、一個缺**機制**（order）、一個近似純**缺業主**（offgraph）。

### 二、先結掉 §27 §四（0 跑、樹上可查）：8.858 ms 確認為**產品成本**

§27 §四 把「產品路徑是否也每 token 全同步」列為未查，並指出兩個分支要用不同處置。答案是**前者**：

| 位置 | 內容 |
|---|---|
| `tools/server/server-context.cpp:4170` | `id = common_sampler_sample(slot.smpl.get(), slot.ctx_tgt, tok_idx);` |
| `common/sampling.cpp:611` | `common_sampler_sample()` 的**第一行**就是 `llama_synchronize(ctx);` |

而 llama-bench 自己那一支（`tools/llama-bench/llama-bench.cpp:2595`）也同步 ⇒ **兩條路徑都在每 token 全同步**，
`§27 §四` 的「若沒有 ⇒ 11.703 低估產品、SG-OFFGRAPH 在產品上不存在」這個分支**不成立**。
（附註：交付 cell 的 `llama-bench` 迴圈**不做取樣**（`std::rand()`），所以在**量具**上這 8.858 是「步尾收尾」；
在**產品**上是「取樣前必須有 logits」。兩者的共同點是：都要等同一段東西。）

### 三、SG-OFFGRAPH 可以用**既有讀數關掉**（0 跑、0 build、0 新儀器）

§27 §三 說這 8.858「不是靠把工作變少縮掉的，是靠不要停下來等隱藏的」，並說「它的槓桿與 SG-GAP 是同一支」。
§38 剛好提供了**把「等待」與「工作」分開的常數**：每邊界的非 CPU、非工作殘項
`gap_not_cpu` = **0.1545 ms**（CV 0.11、與 CPU 編碼時間 `bdsub` 相關 **r = −0.00**）——
它就是 **Metal 完成通知 ＋ launch 延遲**的**每事件上界**。

`llama_synchronize` 這一段的兩端是「最後一段的 GPU 完成 → poll 觀察 → 回傳」，中間不經過別的階段，
所以最多經過 2 次這類事件（完成通知 ＋ 執行緒喚醒）。代入：

* **可歸因於「等待本身」的上界 = 2 × 0.1545 = 0.31 ms**
* ⇒ **≥ 96.5% 的 8.858 ms 只能是「最後一段還沒做完的 GPU 時間」**

⇒ 它**不是**「停下來的成本」，是**管線尾端的 GPU 忙碌**；改變它的唯一方式是**把尾端提前做完**
（＝ SG-GAP/序列那條），而 SG-GAP 已於 §37 判 **FAIL（附機制）**、§38 又證殘差不是 CPU 工作。
**⇒ SG-OFFGRAPH 結案：無獨立槓桿（附機制）**，`§16 §四` 示範行的「圖外減半 −5.8 ms」**撤銷**。

**這個結論的兩個邊界（必須一起講）**：

1. 它是**上界推論**，不是量測。要變成量測需要**跨時鐘域**（CPU↔GPU）對齊＝新儀器；而
   **即使量了也改不了方向**——要翻案得讓 0.31 → 8 的 **27 倍**差距反過來，那是量級不是細節。
2. 單流的唯一出路是**併發**（尾端等待期讓別的序列佔 GPU）⇒ 那是**換 cell** 不是換程式，
   **不服務單流 25 t/s**（與 §32 的交付 cell 定義不相容）。

### 四、`on_fail` 義務：登記表（已落地成 `docs/NEXT_ACTIONS_2026-09-29.md`）

| 塊 | 判詞 | **缺的是** | 依據 | 下一個能做的動作 | ⛔ 明確禁止 |
|---|---|---|---|---|---|
| `sg-gap-boundary` | 🔴 FAIL（附機制） | — | §37、§38 | 無 | 再往邊界窗塞 CPU 儀器；用「降 gap」當加速 |
| `sg-offgraph` | 🔴 無獨立槓桿（附機制，§三） | — | §38 `gap_not_cpu`；§27 §三 | 無（單流） | 再用 −5.8 ms 示範值；把它記成「無業主」而不是「無槓桿」|
| `sg-kernel-busy` | 無槓桿（**未判死**） | **槓桿**（缺承載算子） | §18 §七：六族合計 21–29 ms、**單點 ≤0.79 ms**；§23 | **§18 §七(4) 的 skip-probe**（見 §五(2)） | 在 skip-probe 前寫融合 kernel；用 63 µs/dispatch 外推當單價 |
| `sg-fillsub-spin` | 部分（未判死） | **分離** fill 成本與正確性前提（C5） | §36：`resident 8/8 → 4/8`、`dropped` 僅 39 | 駐留**生命週期**探針（見 §五(3)） | 再掃 `CGC_SPAC`／K／REFRESH（§36 已否證旋鈕方向） |
| `sg-order-legal` | 未判死（**否證的是其中一個修法**） | **機制**（新） | §28 E1 實測**值 0 ms**；§24 §五 | 唯讀：leaf 的 producer→consumer 調查（見 §五(4)） | 把 E2（`CGC_SUBMIT_AHEAD`）當成果；把 E1 的 0 ms 當「已試完」 |

### 五、四個塊各自「前進」的具體形狀

**（1）`sg-offgraph`：已結（§二、§三）。** 剩下的 `decode − DECPROF` 2.194 ms 也**不夠大**
（§24 §三 的 H-spin 正面上限 ~2 ms），且與 gap 同源。

**（2）`sg-kernel-busy`：唯一「能加」的一塊，而它的判準量測早就被寫好了。**
§18 §七(4)：動手前先做**一趟 skip-probe** —— env-gated、預設關、**跳過所有輸出元素數 ≤8 的 dispatch**
（`SUM_ROWS`[1]／`CLAMP`[1]／`DIV`[8]／scalar sigmoid），輸出**必然是錯的（VOID）**，
但它把「群集 1 的 **7.4 ms 上界**」換成**實測 Δstep**。這是本 repo 已有的模式（`CGC-HOOK=0`、
「權重不進池」都是靠故意錯的臂量上界）。

⇒ 這是四個塊裡**唯一「一次 build ＝ 一個答案」**的動作，而且它答的正是 `on_fail` 第二支要問的那個問題
（SG-KERNEL 有沒有槓桿）。在它跑出來之前：(a) 不寫那顆 300 行的 `ffn_moe_weighted`；
(b) `on_fail` 的分支**無法關閉**。

⚠ **但不要把它的期望讀成「有機會找到 35 ms」**（2026-09-29 追加更正）：

1. **量測天花板只有 7.4 ms**：skip-probe 動的是群集 1（`ffn_moe_weights_sum`[1]→`_clamped`[1]→`_norm`[8]，
   117 個完全未融合的 dispatch，8.6% 的 dispatch 數），§18 §一二 的**上界 7.4 ms／份額價 1.89 ms**
   —— 那是 45.45 ms 缺口的 **4–16%**。
2. **這塊的主要目標值（19.5 ms）所依賴的機制已被量成反方向**：§17 §三 —— (a)「一層 8 專家一次 dispatch」
   **已經是現況**（3 個 `MUL_MAT_ID`），沒有東西可併；(b) 再多併一次（gate＋up＋GLU，`CGC_MMV_FUSE=1`）
   **dispatch −9% 而每步 +3.5%（更慢）**，三方紀錄同向「併了不會更快」；
   (c) 該節還直接否掉前提本身：**MoE 家族的有效頻寬本來就在 62 GB/s**，落在 dense GEMV 家族實測
   56–108 GB/s 之內 ⇒「dispatch 太小 ⇒ 頻寬上不去」在**這一家族上不成立**。
   而「108 GB/s ⇒ 10.8 ms」那一支則只有頻寬上限、**沒有任何機制**；§22 的 kernel×time join 又已**失敗**
   （55.4 ms 至今沒有被拆到任何一個可攻擊的算子上）。

⇒ 因此 skip-probe 的正確價值是：**用一個實測數字關閉 `on_fail` 的分支**（而不是「找到一塊大的」）。
四個塊的**證據等級**因此是：`SG-FILLSUB`（實測差分 **3.955 → 0.206 ms/step**，§39 ②；合法產物是差值）
＞ `SG-GAP`（−16.9 ms 只在 racy 臂上，§37 把真開採量壓到 ~2 ms）
＞ `SG-KERNEL`（**機制反方向**、join 失敗、只剩 7.4 ms 上界）＞ `SG-ORDER`（定義上 0）。

**（3）`sg-fillsub-spin`：缺的是「把 fill 成本與正確性前提分離」（C5），而 §36 已經指出方向。**
§36 的探針（`CGC_SPAC_DBG`）顯示 `utility topK resident 8/8 → 4/8`，而全場 `n_prefetch_dropped` 只有 **39**
⇒ **丟失發生在駐留期，不在抓取**。下一個能做的量測：對「被需要的 id」記錄**它上一次被填的時刻與被逐出的時刻**
（單調時戳），把 miss 分成 **never-filled** 與 **evicted** 兩類。

* Acceptance：兩類可分辨，且 `evicted` 占多數 ⇒ 槓桿在**駐留保證（政策）**，不在抓取（旋鈕）
  ⇒ 這是**不重掃旋鈕**就能前進的縫。
* Falsify：`never-filled` 占多數 ⇒ 回到抓取，而 §36 已把旋鈕方向判死 ⇒ 該塊進 `on_fail` 登記。

**（4）`sg-order-legal`：缺的是機制，而唯一沒試的方向在卡上 `how` 裡。**
E1（把 remap 移上 GPU）實測**值 0 ms** ⇒ 否證的是**那個修法**，不是「所有機制」。剩下的問題是
「**讓 CPU 不回讀即可寫 leaf**」。下一個能做的**唯讀**調查：找出 remap leaf 的**生產者**與**消費者**，
問消費者能不能直接吃 **device buffer**。

* Acceptance：找到一對可以留在 device 的 producer→consumer ⇒ 開新卡（卡上已寫「需要一張新卡，目前沒有業主」）。
* Falsify：消費者在**構造上**就是 host 端 argsort ⇒ 需要新 kernel，成本要另估 ⇒ 不在本卡範圍。

### 六、對「25 要成立」的更新

§39 的 C1–C7 不變，本節**只改一格**：

* **C4 由「未裁」變成「已答，且很可能是不可滿足」**：產品確實每 token 全同步（§二），
  而那個 8.858 **沒有獨立槓桿**（§三）⇒ C4 若要有救，只能靠「削短尾端」（＝ C3/序列），不是靠移掉同步。
* ⇒ 剩下的必要條件收斂成 **C2（新承載算子）、C6（MTP 是產品決策）、C7（乾淨窗口）**；
  其中 **C2 的判準量測就是 §五(2) 那趟 skip-probe**。

### 七、本節的邊界

* **0 跑、0 build**、不動 `src/`、不動 `docs/mindmap/`、**未 commit**；除 §二 的三個行號外全部是**轉引**。
* §三 是一個**上界推論**（已在上文標明它需要什麼才能變成量測）；它**不新增**任何可引用的 t/s。
* 本節**不改** `§16 §四` 的算術（維持 §37 修正後的 **41.2 ms ＝ 24.3 t/s**），只撤銷示範行中
  「圖外減半 −5.8 ms」這半的**依據**。

---

## 41. 用現有證據「做到底」：把已量到的全疊上，落點在哪（2026-09-29）

### 一、規則（先定什麼算「已量到的」）

| 類 | 允許？ | 標籤 |
|---|---|---|
| **同一臂內**的實測差 Δ | ✅ | Δ |
| **實測臂**的絕對 step／t/s | ✅ | 絕對 |
| 外推（頻寬上限、理論併法、地圖的加總） | ⛔ 不算證據，**另列** | 外推 |

外加兩條紀律：**(1) 每列都要標可交付性**（bit-identical／錯輸出／VOID）；
**(2) 亞加性警告**——同一個開關造成的 Δ **不可與它底下的 Δ 相加**（`S1` 的 −40 ms 已經包含「不填池」）。

### 二、可交付線（唯一能進生產的）

| 列 | step | t/s | 可交付性 |
|---|---:|---:|---|
| **交付錨點** | **85.45** | **11.703** | ✅ 三扇門全過（§15／§31）|
| ＋3a `CGC_MISS_MASK`／`CGC_ZERO_MISS` | 84.50 | 11.83 | ✅ M1 **9/9 bit-identical**；成本 **UNRESOLVED**（點估計 −0.95、median **+0.59**、CI 半寬 ±2.01、門檻 0.2 —— 見 **§42 §一**）|

⇒ **做到底的可交付回收 ≈ 0 ms**（§42 §一：這一格的名目 −0.95 連符號都不穩定，判決是 **UNRESOLVED**，不是一個值）。這一格的價值是「**不擋路**」，不是「變快」（§39 ① 早就這樣分級）。

### 三、實測但**錯輸出**（Δ 合法，t/s 不可引用）

| 列 | step | t/s | 出處／性質 |
|---|---:|---:|---|
| **單次提交 / 交付 cell**（A 臂） | **36.8** | **27.17 ± 0.27** | `PROD_NEW_DECODE20_G4MISS:70`；**thermal NOMINAL 54/54**，但 `attribution=swap` ⇒ VOID；錯輸出 |
| ＋fillahead（餵料補齊，同趟） | 39.0 | 25.64 | §0 結果表（同趟、VOID）|
| S1 空轉探針（凍結池） | 40.2 | 24.88 | §35，VOID 上限探針 |
| `CGC_EB_NOFILL` 的 **Δ** | −3.75 | — | `fill_batch 3.955 → 0.206 ms/step`（§39 ②）|

### 四、外推線（只列位置，不算證據）

| 列 | step | t/s | 註 |
|---|---:|---:|---|
| kernel：108 GB/s 上限 | 44.2 | 22.6 | **機制已反方向**（§17）|
| kernel：10× 大 dispatch | 45.9 | 21.8 | 同上 |
| G4 地圖的 fill 回補 | 52.2 | 19.2 | `MILESTONE_MAP_RECHECK:142` |
| 交付 cell 實測 IO 重算 | 48.5 | 20.6 | §35 §二 |
| B 靜態 acc 補丁 | 50.1–51.7 | 19.3–20.0 | §35 |
| **目標** | **40.0** | **25.0** | 攻關卡 |

### 五、做到底的三個讀數（本節真正的結論）

1. **可交付**：11.703 → **11.83 t/s**（+1.1%，且在 ±2.01 ms 誤差內）⇒ 實質 **0**。
2. **實測**：**27.17 ± 0.27 t/s ＝ 36.8 ms 已經跨過 25**——而且它是語料裡**唯一 thermal NOMINAL 54/54** 的一場。
3. **全部越線的格子都是錯輸出臂**（27.17／25.64／24.88），**但它們越線的機制不是同一個（本節原稿把它們混為一談，§50 更正）**：
   * **27.17／24.88（`_DBG/_COST` 臂與凍結探針）**：`gather (ensure) 0/0` ⇒ **無 fill、池子凍結**（`misses 130.5/步 = 41.8%`）。
   * **25.64／39.0 ms（`fillahead` A 半，**池是活的**）**：`prefetch=2854/0`、`CGC-SPAC feeds=1560`、`CGC-RB-FEED` 有印、
     **`file_reads>0`**、miss 由 42.9% 降到 **6.1%**（P0 五項驗收**全過**，§0 結果表＋§34）。它的錯不在「沒填」，
     而在**沒重算**：那 6.1%（≈19 個 (layer,expert)/步）是 **B 半**要補的，尚未實作。
   ⇒ 這一格**已經過 G4 的 ≤25% 閘**，所以它越線的原因與 27.17 **不同** ⇒ 判死的算式也不同（見 §50）。

### 六、這把缺口 45.45 ms 的**性質**定死了

* 從 **85.45 → 36.8 ms 的 48.65 ms**，全部來自**同一個正確性前提**（權重不進池／leaf 不觀察）。
* 45.45 ms 的缺口裡，**除了誤差內的 0.95 ms，沒有一格是經由「正確且可交付」的路徑拿到的**。
* ⇒ 所以「優化到極限」不是「還差幾 ms 的效能」，而是：**缺口的 98% 與「正確性」是同一件事**（C5 的實證版）。
  這也是 §39 §三 說「要讓 25 重新可判定只有兩條路（第六條軸／某格超過自己的已量上界）」的實測根據。

### 七、因此任何新槓桿的入場判準（三問，缺一不算）

1. 它的 Δ 是否在 **M1／M2 bit-identical** 的前提下量到？
2. 它是否**不依賴**「池子凍結／leaf 不觀察」？
3. 它的 t/s 是否在 `attribution=none` ＋ thermal NOMINAL 的窗口裡？

### 八、邊界

* 本節 **0 跑**、**0 build**、全部**轉引**（外推列已單獨隔開）；未動 `src/`、未動 `docs/mindmap/`、**未 commit**。
* 第 3 列是**同臂內**的差，**不可**與第 2 列相減；跨臂的 ms 差不當證據用。
* 表中除第 1 列外**任何 t/s 都不可當成績**；第 2 列那一場 `attribution=swap` ⇒ 連「有沒有推進」都答不出（C7）。

---

## 42. 3a（`CGC_MISS_MASK`／`CGC_ZERO_MISS`）可以優化到什麼程度（2026-09-29）

### 一、先更正「那一格是什麼」——它不是「−0.95 ms」

§39 ① 與 §41 ② 都把 3a 寫成一列 `Δ = −0.95 ± 2.01 ms`。原始出處（`docs/MISS_MASK_STEP2_2026-09-24.md` §一）寫的其實是：

```
Δ(mean)   = −0.948 ms/step    SE = 1.027    95% CI = [−2.961, +1.064]
Δ(median) = +0.585 ms/step
解析度（95% CI 半寬）= ±2.01 ms/step     門檻 = 0.2 ms/step
⇒ 正確性 PASS，成本 UNRESOLVED（不是 FAIL）
```

⇒ **點估計在兩個統計量上連符號都不一致**（mean −0.95／median **+0.59**），而 **CI 半寬是預註冊門檻的 10 倍**。
該文件的判決是 **UNRESOLVED**，不是「−0.95 ms」。本節沿用它的判決。

### 二、而且定價對象要分清：**要定價的是 `CGC_MISS_MASK=1`，不是 `_DBG`**

同文件 §一 的兩開關分工（原文）：

| env | 作用 | 代價 | 可否報吞吐 |
|---|---|---|---|
| `CGC_MISS_MASK=1` | 建節點 ＋ 每步發布 `valid_table`。**這是要定價的那一個** | **2 節點／層 × 39 層** | 可以（受門檻 0.2 ms/step 約束）|
| `CGC_MISS_MASK_DBG=1` | 額外 `synchronize` ＋ 讀回 ＋ 印 | 1 次同步／步 | ⛔ **禁止拿來報吞吐量**（原文自己寫的）|

### 三、今天從既有產物量到的：`_DBG` 那 464 µs 的**形狀**（交付 cell 權威場）

`Backup/g4miss_2026-09-29/` 的 `CGC-MISSMASK-COST`（382–386 步，avg）：

| 量 | 值 |
|---|---|
| `total_usec`／步 | **464** |
| ├ `read_usec`（**`ngets/step = 77.8` 的 `ggml_backend_tensor_get`**） | **462**（⇒ **5.9 µs/次**）|
| └ `sync_usec`（那個 `ggml_backend_sched_synchronize`）| **1.9（0.40%）** |
| `nsel0` | 8（＝ `n_expert_used 1 × n_tokens 1` 的 decode 形狀）|

⇒ 這一段的成本**幾乎全是「回讀次數 × 每次固定開銷」**（78 次 = 39 層 × 2 張量），**同步只佔 0.4%**。
這與 §38 的結論同族：**貴的是事件數，不是等待**。

### 四、可以優化的三格（附天花板、判準、以及「能不能算成績」）

| # | 優化物件 | 具體做法 | 上界 | 能不能算進吞吐 |
|---|---|---|---|---|
| **A** | `MISS_MASK=1` 的 **78 個小節點/步**（2/層） | 收成 1/層，或把它折進既有的 `mul_mat_id` id 路徑 | **預註冊的判準就是 ≤0.2 ms/step**（§一 那個門檻，至今 **UNRESOLVED**）；換算 = **0.23%** | ✅ 可以（這是生產路徑上的東西）|
| **B** | `_DBG` 的 **78 次 get** | 把 39 層的 mask／ids 收進**一個連續的 per-step 緩衝**（或一個拼接張量）⇒ 78 次 → 2 次（甚至 1 次） | **−450 µs/步 ≈ 0.53%** | ⛔ **不行**（原文禁止；它是診斷開關）|
| **C** | **形狀**：同一支儀器在**分段臂**上是 `sync_usec ≈ 11.5 ms/step`（`ngets=0 nsel0=-1`，drain 主導，`PROD_NEW_DECODE20_G4MISS` §3 記載並附「該趟 thermal HEAVY、swap +2261 MiB」的保留）| **不要在分段臂上開它**；單次提交把它攤掉 ⇒ 464 µs vs ~11.5 ms ⇒ **差 25 倍** | 25× | —（是「避免付」，不是「省」）|

### 五、誠實的天花板

* `_DBG` 全拿：**0.464 ms ÷ 45.45 ms = 1.0%**；`MISS_MASK=1` 打到門檻：**0.2 ms = 0.23%**。
* 換成速度：**11.703 → 11.73–11.76 t/s**。⇒ **「可以優化」＝是，但它是 0.2–0.5% 級的項目。**
* 這一格真正的價值不在這 0.5 ms，而在它**不擋路**：`MISS_MASK`／`ZERO_MISS` 是 **M1 9/9 bit-identical**
  的正確性使能項，而 `CGC_ZERO_SLOT`（G3）武裝後才能把 zero-fill 移到 GPU（§35 的 P3，B 的前置）
  ⇒ 它服務的是 **G4 的 19.2 t/s 天花板**，不是 decode 的 25。

### 六、另外一件要一起講的事（本輪才看到的）

`_DBG` 的回讀迴圈同時是**池子餵料的宿主**（§40 §五(1) 的 `spac_update` 餵料掛在它裡面），
所以「把 B 做掉」**不是純優化**——它會動到「不開 debug 就有正確池子」這件事（§35 的 P1）。
⇒ B 的正確形狀是：**先搬餵料、再刪回讀**；單獨刪回讀會讓 §34 那個 6.09% 的池子重新變成 42%。

### 七、邊界

* 本節 **0 跑、0 build**：§三 是**既有產物**的新讀法（`CGC-MISSMASK-COST` 在 log 裡一直都有，只是沒人拆過 `read`/`sync`），
  §一/§二 是轉引原文；未動 `src/`、未動 `docs/mindmap/`、**未 commit**。
* §三 的 464 µs **不可**當成「3a 的成本」引用：它定價的是 `_DBG`，而原文明文禁止該開關報吞吐。
* §四 的 A/B/C 都是**未執行的建議**，不含任何實測推進；本節不新增可引用的 t/s。

---

## 43. G4 路線與它的 19.2 t/s：哪一格能優化、哪一格已被量死（2026-09-29）

### 一、先拆同名字：這個 repo 裡有**兩個 G4**

| 名字 | 屬於 | 它是什麼 | 判詞 |
|---|---|---|---|
| **G4(a)** | 09-20 系列（`G4_WORK_ORDER`／`G4_FUSION_ANSWER`／`DECODE25_CEILING`）| **縮 union／融合 kernel、省 dispatch** | ❌ **已結案：別寫那顆 300 行 kernel**（單次 dispatch 只值 **0.0736%**；§18 §七(4) 又補「連 3 ms 都還沒被證實」）|
| **G4(b)** | 09-28/29（§32–§35）| **20+ 路線的第 4 道閘＝重算工作清單（每步 miss 比例）** | ✅ **已過閘**（41.99% → §34 的 **6.09%** ≤25%）；它的**天花板 = 19.2 t/s** |

本節問的是 **G4(b) 的 19.2**。**G4(a) 不是「可以優化」，是「已被量死」**——這兩件事在對話裡極容易混在一起。

### 二、19.2 t/s 是誰付的錢

里程碑地圖 `MILESTONE_MAP_RECHECK:142`（S1 那一列）的原文算式：

```
可回收上界 = 序列化；扣掉必須還回去的 fill ⇒  48.2 + 3.96 = 52.2 ms ⇒ 19.2 t/s
```

⇒ **92% 在第一項**（48.2），7.6% 在第二項（3.96）。所以「G4 可以優化嗎」＝「這兩項各自可以動嗎」。

### 三、第一項（48.2）：**沒有已量到的機制**

| 已量到的 | 結果 |
|---|---|
| §17 | 一層 8 專家**已是** 3 個 `MUL_MAT_ID`；再併一次（`CGC_MMV_FUSE`）**dispatch −9% 但每步 +3.5%**；MoE 家族有效頻寬本來就在 62 GB/s（落在 dense 實測 56–108 內）⇒「dispatch 太小」的前提不成立 |
| §18 §七 | 六族合計 21–29 ms，但**單點 ≤0.79 ms**（唯一可識別者 `RMS_NORM`）|
| §22 | 「逐 op kernel 數 × GPU 時間」的 join **失敗** ⇒ 55.4 ms 沒被歸屬到任何可攻擊的算子 |
| §23 | 逐 dispatch GPU 時間**在 M4 上是關的**（硬體）|
| G4(a) | 融合 kernel：單次 dispatch **0.0736%** |

⇒ 這一項**在現有機制下不可優化**；而且它來自**錯輸出臂**（池子凍結、無 fill）⇒ 它已經**不含任何正確性工作**，是「不做正確的事」時的底。

### 四、第一項的**底不唯一**（本節的新發現，且它比任何程式優化都大）

同一個 S1 開關組合、同一批儀器，兩個 cell 給出**兩個不同的 step**：

| 來源 | cell | step | t/s | 歸因 |
|---|---|---:|---:|---|
| `Backup/seg_batch_s1_pairs/llama_bench_prod-new_CGC_SEG_BATCH_1_…_p2048_n128_d512_r3.json`（同一場的對照臂 **11.714**，與 §15 錨點 11.703 同尺）| 預設（`p2048`）| **49.18**（20.335 ± 0.789）| 20.33 | 該場為**乾淨**（對照臂 = 錨點）|
| `PROD_NEW_DECODE20_G4MISS:70` A 臂（`+MISS_MASK{,_DBG,_COST}`）| 交付（`p0`）| **36.80**（27.17 ± 0.27）| 27.17 | `attribution=swap` ＋ 帶 `_DBG`／`_COST`（**原文禁止用來報吞吐**）|

⇒ **差 12.4 ms ⇒ 天花板在 19.2 與 24.5 t/s 之間未裁。**這是這條路線上**最大的單一格**，
而且它**不需要改一行程式**：要的是**校準**（同一開關組合、同一 cell、乾淨窗口、成對）。
⚠ 但有兩個反向保留，必須一起講：交付 cell 那一筆是 `swap` 歸因、且帶了明令禁止報吞吐的 `_DBG`／`_COST`
⇒ **不能直接拿 36.8 去改寫 19.2**；要嘛它在乾淨窗口重現，要嘛它被證明是熱盒子假象。

### 五、第二項（3.96 的「還回去的 fill」）：**一個 term、四個數字**

| 數字 | 出處 | 性質 |
|---:|---|---|
| **3.955** | `Backup/nofill_prod/nf_fill.json`（地圖用它）| fill 的**同步**成本（4.7% of 84.8 ms）|
| **5.41（p50）** | 交付 cell 誠實臂（§35 §二）| 實際付的 fill（p90 12.72）|
| **8.3** | 交付 cell IO 反解（`9.96 MiB ÷ 1.2 GiB/s`，§35 §二）| 同一件事的**實測 IO 口徑** |
| **1.60–3.20** | B 靜態 acc 補丁（320 op × 5–10 µs，§35 P4）| **取代**它的東西（op 成本，非 IO）|

⇒ 這**一個** term 橫跨 **1.60–8.3 ms（5×，step 的 3–16%）**。這是第二個該校準的格子，
而且它同時是「B 到底值多少」的判準（§35 P4 的否證條件之一就是它隨 miss 縮放）。

### 六、MISS_MASK／ZERO_MISS／G3 各自在哪

| 件 | 在 19.2 裡的角色 | 可優化？ | 上限 |
|---|---|---|---|
| `CGC_MISS_MASK`／`CGC_ZERO_MISS` | **bit-identical 的正確性使能項**（使 20+ 在正確性上成立）| 是，但小：節點成本（2/層 × 39）| 判準 0.2 ms/step ＝ **0.23%**（§42）|
| `CGC_ZERO_SLOT`（G3） | B 的**前置**：讓未駐留者貢獻 0 | **目前單獨開是 no-op**（`zero_slot=0 placeholder=0`；leaf 只在 `CGC_SLOT_TABLE_GPU=1` 時存在，§35 P3）⇒ 要「武裝」 | 主端點是**計數器翻轉**，不是 ms |
| `_DBG` 回讀 | 池子餵料的宿主（§40 §五(1)）| 是（78 次 get → 2）| −450 µs ＝ 0.53%，且**不得**算成績 |

### 七、一句話

**G4(b) 這一格可以「優化」的不是 19.2 這個數字，而是它的兩個輸入**：
底（49.18 vs 36.80，值 **12.4 ms**）與 fill 回補（1.60–8.3，值 **5× 口徑差**）——兩者都是**校準**，不是改碼。
程式裡的部分（GPU 繁忙那一格 48–49 ms）已被 §17／§18／§22／§23／G4(a) **四次實測堵住**。

### 八、邊界

* 本節 **0 跑、0 build**；§四 的兩列都是**既有產物**的轉引（並附各自的歸因保留）；未動 `src/`、未動 `docs/mindmap/`、**未 commit**。
* §四 **不是**「天花板是 24.5」的主張，而是「19.2 建立在單一底上，而另一個底不同 ⇒ 需校準」。
* 本節不新增可引用的 t/s。

---

## 44. 20+ 還能不能做、以及「已拆解清單」裡還有什麼可以動（2026-09-29）

### 一、先引卡的原文：20+ 的判準與判決

`scripts/check/charters/exp-prodnew-decode20-g4miss.yaml` 的門檻是**跑前寫死的**：

| 觀測 | 判決 |
|---|---|
| miss 比例 **≤25%** | 「正確版 20+ **有路**：預測＋重算只付 1/4 的層」 |
| **≥60%** | 「該機制在交付上**沒有 headroom**，力氣轉 shape/IO（絕對天花板 **20.49／16.31**）」|

觀測值（§34，權威 cell）：**中位 6.09%**（IQR 3.85–8.65、19.0 個/步）⇒ **過閘**。
卡的驗收另外要求「≥10 行 `CGC-MISSMASK-STEP`」與「報分布不只報純量」——§34 兩條都滿足。

⇒ **就卡自己的條款而言：20+ 有路，而且這張卡的驗收已達成。**（卡的產出是**結構數**，不是 t/s。）

### 二、但施工清單要重新報價：**P2 已否證，所以用 6.09% 報，不用 3.7%**

§35 的 P0–P5 現況：

| 步 | 內容 | 現況 |
|---|---|---|
| P0 | A 餵 `spac_update` | ✅ 成立（§34）|
| P1 | 把餵料從 `CGC_MISS_MASK_DBG` 回讀拆出來 | ⏳ 未做；且**它是 `_DBG` 可刪的前提**（§42 §六）|
| **P2** | 殘差 6.09% → **≤3.7%**（掃 `CGC_SPAC_K`／`ALPHA`／`REFRESH`）| ❌ **已否證**（§36）：K 是**懸崖**（K=32 ⇒ miss 1.0000、dropped 44.7 萬；K=4 ⇒ miss 0.1282），且丟失發生在**駐留期**不在抓取 |
| P3 | 武裝 G3（`CGC_ZERO_SLOT`）| ⏳ 未做；**2 趟、不動 `src/`**；單獨開是 no-op（leaf 只存在於 `CGC_SLOT_TABLE_GPU=1`）|
| P4 | B：靜態 `ggml_acc` 補丁（320 op/step 固定）| ⏳ 未做，且**風險最高**：`grep ggml_acc(` 在 `src/llama.cpp/src/` **0 命中**（無範例可抄）、`ggml-alloc` 的新 buffer／zero-fill **尚未估** |
| P5 | 成對測速（`attribution=none`、NOMINAL、reps≥3）| ⏳ 未做 |

⇒ **P2 的否證改變的是報價**：重算清單不會被壓到地板，**它就是 ~6%（19.0 個/步）**，
而 `MISS_MASK`／`ZERO_MISS` 讓「0 ＋ 補算 ＝ 原值」在數值上成立（M1 9/9 bit-identical）。

### 三、決定「值不值得投 P4」的是**底**，不是 P4 本身

| 底 | 算式 | 天花板 |
|---|---|---:|
| 預設 cell S1（**乾淨**，同場對照臂 11.714 ＝ 錨點）| 49.18 ＋ 3.96 | **19.2** |
| 交付 cell S1（`swap`，且帶 `_DBG`／`_COST`）| 36.80 ＋ 3.96 | **24.5** |

§43 §四 已記這個 12.4 ms 的**口徑未裁**。**⇒ 施工順序應該是：P3（2 趟、不動 src）→ 校準底 → 才決定 P4**，
而不是先寫那顆 300 行、且沒有範例可抄的 acc 路徑。
理由很實際：底 = 36.8 時這條路**幾乎碰到 25**（24.5）；底 = 49.18 時它只到 **19.2**（連 20 都要靠四捨五入）。

### 四、「已拆解清單」裡還有什麼可以動（按服務的里程碑分類）

| 已拆解項 | 服務 | 已量實測 | 可動？ | 上限 |
|---|---|---|---|---|
| **G4(b) 重算本體（B）** | **20+** | 閘已過（6.09%）；B 的 op 成本 1.60–3.20 | 需 P3 ＋ P4 | **19.2–24.5** |
| **S1「底」的校準** | **20+** | 12.4 ms 未裁（§43 §四）| ✅ **校準（不改碼）** | 動的是天花板本身（+5 t/s 級）|
| **fill 那一個 term 的定價** | **20+** | 1.60／3.955／5.41／8.3（**5× 口徑差**）| ✅ **校準** | 決定 P4 的回收值 |
| fill 分離（C5）| ✗ 25 | Δ **3.75**（`CGC_EB_NOFILL`）| 需先分離正確性前提 | 12.3 t/s ⇒ **不服務 20+** |
| `MISS_MASK` 節點成本 | 20+（使能）| 門檻 0.2 ms/step（前註冊）| ✅ 小 | **0.23%** |
| `_DBG` 回讀（78→2 次 get）| — | 464 µs/步（read 主導）| ✅ | **0.53%**，且**不得**算成績 |
| **MTP（C6）** | 25？ | 交付 UNRESOLVED；生產 ABBA **×0.695** | **產品決策**，非技術 | 唯一「**乘**」的軸 |
| swap 結構修復 | 全部 | 執行線主張（launch swap 0、11.49、NOMINAL），**本線未複核** | ✅ 複核 | 讓讀數可引用 |
| **C7 乾淨窗口** | 全部 | — | ✅ | 讓「有沒有推進」變成可答 |

### 五、25+ 的結論（不變）

**已拆解的清單裡沒有任何一項服務 25**：§41 的「做到底」已證——越過 25 的每一格都是錯輸出臂
（27.17／25.64），可交付線停在 **11.703**（＋0.95，且在誤差內）。
⇒ 25 仍然只剩 §39 §三 的兩條路：**(a) 第六條軸（改每 token 的 bytes 或 steps）**、**(b) 某格超過自己的已量上界**。

### 六、邊界

* 本節 **0 跑、0 build**；全部轉引（卡、§34–§43）；未動 `src/`、未動 `docs/mindmap/`、**未 commit**。
* §一 的「有路」是**卡自己預註冊的判準**下的結論，**不是**對 20+ 可達性的新主張；
  20+ 的實際天花板仍受 §三 那個未裁的口徑控制。

---

## 45. 20+／25+ 的子目標拆解，與 mindmap 節點對位（2026-09-29）

### 一、對位規則（先寫死，免得生出第二套命名）

1. **一個子目標 ＝ 一個既有 mindmap 節點**；本節**不新增節點**。
2. **本輪不動 `docs/mindmap/mindmap.json`**（mtime 保持 17:55）⇒ 對位以表格登記，節點層的更正列成
   **「建議更新」清單**（§四）交由節點業主套用。
3. 每個子目標只寫四欄：**現況（已量）／action／驗收／否證**。
4. 兩層硬分開：**L20**（decode ≥20，節點 `exp-prodnew-decode20-g4miss`，`targets.decode_tps = 20.0`）
   與 **L25**（≥25，節點 `m-decode25`／`exp-caliber-calibration`，`targets.decode_tps = 25.0`）。
   **L20 的子目標不得被當成 L25 的加數**（§44 §五）。

### 二、L20（decode ≥20）：5 個子目標

| id | 子目標 | mindmap 節點（軸／級／target）| 現況（已量）| action | 驗收 | 否證 |
|---|---|---|---|---|---|
| **L20-1** | **底（S1 的 step）校準** | `exp-prodnew-decode20-g4miss`（S／3a／20.0）| 49.18 ms（乾淨、同場對照臂 11.714 ＝ 錨點）vs 36.80 ms（`swap`、帶 `_DBG`／`_COST`）⇒ §43 §四 | 同一組 S1 開關、交付 cell、乾淨窗口、成對 | 兩者收斂（CI 重疊）**或**差異被指名 | 差異被證明是熱盒子機械 ⇒ 只能採用較嚴的那個底 |
| **L20-2** | **武裝 G3**（`CGC_ZERO_SLOT`）| 同上（B 的**前置**）| 單獨開是 no-op（`zero_slot=0 placeholder=0`；leaf 只在 `CGC_SLOT_TABLE_GPU=1` 下存在）| **2 趟、不動 `src/`**（`e-s1-g3-zeroslot` 已立卡）| `zero_slot>0 ∧ placeholder=0`（**計數器翻轉**，不是 ms）| 仍 `placeholder>0` ⇒ B 的「0 ＋ 補算 ＝ 原值」前提不成立 |
| **L20-3** | **重算本體 B**（靜態 `ggml_acc`）| 同上 | 未做；成本 1.60–3.20 ms/步；`grep ggml_acc(` 在 `src/llama.cpp/src/` **0 命中** | 先做**可行性尖兵**（op 成本 ＋ `ggml-alloc` 新 buffer／zero-fill），再實作 | M1 **9/9** ∧ M2 **9/9**、`config_diffs == []`、op 增量 **320 且不隨 miss 放大**、生產路徑**無新增 `synchronize`** | 成本隨 miss 縮放 ⇒ 靜態圖論證不成立（成本量級不同）|
| **L20-4** | **fill 那一個 term 的定價** | `io-nofill`（S／3a）| **1.60／3.955／5.41／8.3**（5× 口徑差，§43 §五）| 校準：指名哪一個該進 52.2 的算式，其餘三個各是什麼口徑 | 交付 cell 上**一個**數字 ＋ 不確定度 | 收斂不了 ⇒ 20+ 天花板維持為區間 |
| **L20-5** | **餵料搬家（P1）** | `exp-singlesubmit-fillahead`（S／3a／20.0）| 餵料掛在 `CGC_MISS_MASK_DBG` 的回讀迴圈上（§40 §五、§42 §六）| 搬到不依賴 debug 的位置 | 不開 debug 仍有 `CGC-RB-FEED` 且 `prefetch>0/0` | 拿不到本步 id（無替代來源）⇒ 不硬做，改列為 L20-1 的替代底 |

**已結、不再重開**：P2 旋鈕（§36：懸崖）、`sg-gap-boundary`（§37／§38）、`sg-offgraph`（§40）。
**⇒ L20 的性質**：其中 **2 格是校準（不改碼）**、2 格要動 `src/`、1 格是搬家；**上限 19.2–24.5 t/s**。

### 三、L25（decode ≥25）：4 個子目標

| id | 子目標 | mindmap 節點（軸／級／target）| 現況 | action | 驗收 | 否證 |
|---|---|---|---|---|---|
| **L25-1** | **第六條軸**：改「每 token 的 bytes 或 steps」（不是每步的 ms）| `m-decode25`（both／4）；口徑面屬 `exp-caliber-calibration`（na／3a／**25.0**）| 現有軸（ms、段數、kernel、IO）全部量完；缺口 45.45 ms | 立一張卡，先問「本步的 **bytes 或 steps** 有沒有可改的乘數」| 需要一個**新的乘數**：≤40.0 ms 且 M1 bit-identical | 找不到候選乘數 ⇒ 25 維持判死（現行 `note`：P(25)<3%）|
| **L25-2** | **讓某一格超過自己的已量上界** | `m-decode25`（both／4）| 每一格都已放在自己的上界上（§39 §二、§41）| 指名**一格** ＋ 指名它上界**以外的**機制 | 該格出現超出上界的實測（且 M1 過）| 指不出來 ⇒ 25 維持判死 |
| **L25-3** | **C7：乾淨窗口** | `sys-window`（na／3b）＋ `exp-caliber-calibration` | §33–§45 **全部** `attribution=swap` ⇒ 連「有沒有推進」都答不出 | 找一場 `attribution=none` ∧ thermal NOMINAL ∧ reps≥3 的窗口，跑交付 cell 錨點 | 一場**可引用的成對**錨點（同時回答「這一輪有沒有推進」）| 窗口拿不到（盒子持續不靜）⇒ 所有新讀數維持 VOID |
| **L25-4** | **MTP（C6）** | `exp-m-draft-cost`（M／3a／19.25）＋ `mtp-*` 群 | 交付口徑 MTP **off**；on 的交付讀數 **UNRESOLVED**；生產 ABBA **×0.695**；k=3 即使 a=1.0 也只 **19.25**、k→∞ 極限 **24.59** | **產品決策**：是否改交付口徑（技術上「唯一乘的軸」，但數學上到不了 25）| 決策記錄 ＋ 一次合規的 on/off 成對 | 決定 off ⇒ M 軸退出 25 的組合 |

**⇒ L25 的性質**：**沒有任何一個是既有清單的加數**——它們各自是「讓 25 重新變成**可判定**的問題」的手段，
不是「讓 25 成立」的手段（§39 §三）。

### 四、對位：這一輪的判定**改變了哪些節點**（建議更新，本輪不改檔）

| 節點 | 現存文字 | 建議更新為 | 依據 |
|---|---|---|---|
| `exp-prodnew-decode20-g4miss`（3a／S）| `res`:「尚無可引用讀數：1 個 run 全部非乾淨」 | 閘**已過**：權威 cell 中位 **6.09%**（IQR 3.85–8.65、19.0/步）、`mm_pub_n_leaf=39/wrote=39`、`instrument_gate=BOUND`；仍**不主張吞吐** | §34 |
| `miss-3a`（3b／S）| `res`:「兩條都過（…；Δ = −0.95 ± 2.01 ms）」 | 正確性 **PASS**；成本 **UNRESOLVED**（SE 1.027、95% CI [−2.96, +1.06]、median **+0.59**、門檻 0.2 ms）| §42 §一（引自 `MISS_MASK_STEP2` 原文）|
| `m-decode25`（4／both）| `note`:「P(25) < 3%；物理可達但已知槓桿只到 13.2~13.8」 | 補上 §41：**實測越線的每一格都是錯輸出臂**（27.17／25.64），可交付線 **11.703**（＋0.95，誤差內）| §41 |
| `exp-s2-overlap`（3a／S）、`exp-singlesubmit-fillahead` 的部分假設 | 「實驗進行中」 | S 軸的 segment/gap 部分**已結**（§37 FAIL 附機制；§38 殘項非 CPU）| §37／§38 |
| `s1-segbatch`（3a／S）| 「A/B 作廢、不主張吞吐」 | 補上「可交付上界 ＝ 序列化 − 必須還回去的 fill ⇒ **19.2–24.5**（底未裁）」| §43 §四、§44 §三 |

**不動**：`m-prefill250`（②，已過）、`m-total`（①，仍空，最接近 pp 260.41 ＋ tg 12.195 差 3%）、
`g4`（4／C，融合 kernel 判 0 —— 與 L20-3 的 `ggml_acc` 補丁是**不同的東西**）。

### 五、一句話

**L20 ＝ 5 格（2 校準、2 動碼、1 搬家），上限 19.2–24.5；L25 ＝ 4 格，且全部是「讓問題重新可判定」的手段，不是加數。**

### 六、邊界

* 本節 **0 跑、0 build**；全部轉引（§34–§44、卡、`mindmap.json` 的 `crit/res/targets` 欄）。
* **未寫 `docs/mindmap/`**（`mindmap.json` mtime 保持 17:55）；§四 是**建議**，不是已套用的變更。
* 本節不新增可引用的 t/s；子目標 id（L20-x／L25-x）**不是** charter id，只是本節的登記編號。

---

## 46. 完備性稽核：§45 有沒有包含「所有」可優化的東西（2026-09-29）

### 一、方法與結果

把 `docs/mindmap/mindmap.json` 的 **55 個節點全掃**，按 tier 分：

| | 數 | 說明 |
|---|---:|---|
| 未結案（tier 1／2／3a／3b）| **34** | 其中 tier 1 只有 `m-total`，tier 2 有 `m-prefill250`／`score-leaderboard` |
| 已結案（tier 4／na）| **21** | |

§45 的 L20／L25 直接指到的節點只有 **7 個**（`exp-prodnew-decode20-g4miss`、`io-nofill`、
`exp-singlesubmit-fillahead`、`m-decode25`、`exp-caliber-calibration`、`sys-window`、`exp-m-draft-cost`）。

⇒ **答案：§45 作為「節點清單」不完整——漏了 10 個未結案節點**（§二）。但作為「**槓桿**清單」，
它沒有漏掉任何**有實測 headroom** 的東西：34 個未結案節點裡，具「已量到的 headroom」的只有
**2 個**（`c-bandwidth` 2.42%、`io-poolsize` +3.1% 且在噪音內），其餘要嘛**無讀數**、要嘛**有前置**、
要嘛節點自己就寫著「**不是可用槓桿**」。這兩件事要分開講。

### 二、漏掉的 10 個（按性質分三群）

**群 A：S 軸的**替代機制**（與 L20 同目標、不同做法）——最該補**

| 節點 | tier | 節點自己寫的 | 進 L20 的方式 |
|---|---|---|---|
| `s1-asyncgather` | 3a | goal：單段提交下「命中即算、miss 只寫清單並 MASK，CPU **後台異步 fill** 與下一步 GPU 重疊，再**只補算 miss expert**」⇒ 拿單段的 ×1.7–1.8 且保正確性。note：**收益【推算】上界 ~19–20 t/s**；**E1 裁決後按 20.8 ms/step 重算 ⇒ ~14.5 t/s**（19–20 那個數押在已降級的 3.955 上）| **已排除（附 14.5 t/s 的重算）**，但必須登記——它與 L20-3 是**同一個目標的兩條機制**，不寫下來下一個人會重做 |
| `cache-rho` | 3a | 按層批次化 prefetch；覆蓋 0.849、視窗 1.30–1.59 ms、**每步付 4.76 ms GPU 插入** ⇒ **判活但有前置**；與 prebind **同價、不可相加** | 進 L20，標「有前置」 |
| `cache-prebind` | 3a | 預指派 slot；qu2/qu3 全過、qu1 邊緣；note：**「方案 A 的 h=0.03 判死不適用於 S1」**（ids 由 GPU 端 `get_rows` 產生）| 進 L20，標「判死不覆蓋 S1 這一支」 |
| `exp-churn-delivery-630` | 3a／tgt **20.0** | 在今天的交付口徑（`ntok=1` 主 context）**重新檢驗 churn 判詞**——42.3% 那個數是 `ntok=4` verify 桶、MTP-on 形狀的讀數，兩者是**不同的輸出函數** | 進 L20：這是 L20-1／L20-5 的**前提題** |

**群 B：C 軸——依節點自己的定義是「天花板軸、不是槓桿」，但「不能在分類裡消失」**

| 節點 | tier／tgt | 節點自己寫的 |
|---|---|---|
| **`shape-knobs`** | 3b／C | ★ **BEST_SHAPE 標「唯一沒被結論覆蓋、還活著的方向」（一處 switch case）** ⇒ 這是全清單裡**唯一被標成活著**的一格 |
| `c-device-span` | 3a／C | 57% 住 MoE 區、42% 住 attention／GDN 區；note：**「歸因成立，但**不是可用槓桿**」** |
| `c-bandwidth` | 3a／C | dense GEMV 13.39 ms；實跑 56–108 GB/s；**打滿 100% 峰值也只省 1.92 ms ＝ 2.42%** ⇒ 低於 3% 門檻已結 |
| `g1` | 3a／C | 空轉 19.2% 是「可恢復」的形狀，但 **G1 已判不可達（下界 9.0% > 5%）** |
| `exp-c-eff-bandwidth` | 3a／tgt 16.0 | 問「13.65 GB/s 的分子與分母」；E-A 把「20% 掉到 SSD」否掉（f≈1.5%）⇒ **失效項不是 f，是實際達到的帶寬本身** |
| `exp-c-read-issue` | 3a／tgt 14.0 | 固定成本 0.37 MiB/job、24912 筆、1856 µs/筆；四條候選機制可分辨；`io-shape`（4）已判死同題（收益 2.9% < 3%）|
| `k3-price` | 3b／C | 邊際 **0.0195%/dispatch/步（17.9 µs）** ⇒ **所有舊「融合能省 X%」的算術全部作廢** |

**群 C：兩個「未仲裁的帳目」（沒有業主，也不是槓桿，但會影響所有算術）**

| 節點 | 問題 |
|---|---|
| `io-poolsize` | 3b；實測 4 GiB **10.60** vs 8 GiB **10.28**（+3.1%、噪音內），note 寫：★**與白皮書「8G→4G −21%」矛盾且從未仲裁**，且 **3 GiB 那格從未乾淨量過** |
| `score-leaderboard` | 2；**decode 最高 17.669 t/s（prod25，非 §5.0 認可口徑）** ⇒ 「目前最好」有兩個版本（12.57 vs 17.669）|

### 三、修正後的完整清單

**L20（decode ≥20）：5 → 9 格**

| id | 子目標 | 節點 | 相對 §45 的變化 |
|---|---|---|---|
| L20-1 | 底（S1 的 step）校準 | `exp-prodnew-decode20-g4miss` | 不變 |
| L20-2 | 武裝 G3 | 同上 | 不變 |
| L20-3 | 重算本體 **B**（靜態 `ggml_acc`）| 同上 | 不變 |
| L20-4 | fill 那一個 term 的定價 | `io-nofill` | 不變 |
| L20-5 | 餵料搬家（P1）| `exp-singlesubmit-fillahead` | 不變 |
| **L20-6** | **替代機制：非同步 fill ＋ 只補算** | `s1-asyncgather` | **新增**（以「已排除，重算 14.5 t/s」入表）|
| **L20-7** | **prefetch 的兩個前置分支** | `cache-rho`／`cache-prebind` | **新增**（有前置、不可相加）|
| **L20-8** | **churn 判詞在交付口徑的重檢** | `exp-churn-delivery-630` | **新增**（L20-1／L20-5 的前提）|
| **L20-9** | **C 軸的唯一活著格** | `shape-knobs`（BEST_SHAPE）| **新增** |

**L25（decode ≥25）：4 → 6 格**

| id | 子目標 | 節點 | 變化 |
|---|---|---|---|
| L25-1 | 第六條軸 | `m-decode25` ＋ `exp-caliber-calibration` | 不變 |
| L25-2 | 讓某格超過已量上界 | `m-decode25` | 不變 |
| L25-3 | 乾淨窗口（C7） | `sys-window` | 不變 |
| L25-4 | MTP | `exp-m-draft-cost` | 不變 |
| **L25-5** | **MTP 的產品化上限**：`mtp-2x`（「加速比 2」**不成立** ⇒ 改寫成 m／acc 兩條可攻目標）＋ `mtp-caliper`（同 launch ×1.06／生產 ×0.695，**方向相反**）| `mtp-2x`／`mtp-caliper` | **新增** |
| **L25-6** | **口徑認證**：`k3-swing`（**16.4% 仍未認證**）＋ `score-leaderboard` 的 17.669 是否為「目前最好」| `k3-swing`／`score-leaderboard` | **新增** |

### 四、結論

1. **作為槓桿清單**：§45 **沒有漏**——34 個未結案節點裡，有實測 headroom 的只有 `c-bandwidth`（2.42%）
   與 `io-poolsize`（+3.1%、噪音內），兩者都 < 3% 門檻。
2. **作為節點清單**：**漏了 10 格**（上面三群）。漏掉的主因不是疏忽，而是 §45 只繞著「G4 路線＋校準」建表
   ⇒ **同目標的替代機制（群 A）與 C 軸背景上界（群 B）沒有位置**。
3. **兩件事仍無人認領**：`io-poolsize` 的**白皮書矛盾**與 `score-leaderboard` 的 **17.669 合法性**
   ⇒ 它們不影響 20+／25+ 的槓桿清單，但影響**所有算術的起點**。

### 五、邊界

* 本節 **0 跑、0 build**；全部轉引 `mindmap.json` 的 `goal／note／res／targets` 與 §34–§45。
* **未寫 `docs/mindmap/`**（`mindmap.json` mtime 保持 17:55）；§三 的 L20-6…9／L25-5…6 是**登記編號**，不是 charter id。
* 本節不新增可引用的 t/s。

---

## §47 結案規則（operator 定，2026-09-29）＋兩個帳目的仲裁 ＋ 看板產生鏈

### 一、規則：結案 = prod-new ＋ harness bench ＋ 達標

> 「統一把最好的數據都在 **prod-new profile ＋ harness bench** 跑通、達到子目標的**預期目標**，才算結案。」

它把三件事分開（以前混在一起）：

| 概念 | 定義 | 這頁的用法 |
|---|---|---|
| **已量到** | 有產物、有歸因判定 | 可以當**證據**引用（含錯輸出臂的 Δ） |
| **已認證** | 還要在 `prod-new` ＋ `harness bench` 上重現 | 只有 C1–C3 成立 |
| **已結案** | 已認證 **且** 達到跑前寫死的預期值 | 15 個子目標裡 **1 格**（`L20-6 結案（排除）`） |

**立即後果（要講清楚）**：本輪轉引的漂亮數字幾乎都來自**別的 profile／儀器**——
`27.17／25.64 t/s`（單段提交臂，`swap`）、`49.18 ms`（預設 cell）、`36.80 ms`（交付 cell，帶 `_DBG/_COST`）、
`8.43／10.74 t/s`（`prod25` ＋ `decode_sweep`）、`17.669 t/s`（`prod25` ＋ `-r 1`）——
**全部不滿足結案條件**。它們是證據，不是結案。

機器強制：`scripts/check/decode_board_build.py` 的 **D5**（立項卡必須存在）與
**D6**（標了「結案」就必須 `evidence.profile=prod-new ∧ entry=harness bench ∧ meets=true`，
唯一例外是「結案（排除）」需附出處）；`--check` rc=1 就是漂移。

### 二、兩個未仲裁帳目的仲裁（都是**證據級**，不是新跑；都**不結案**）

**(a) `io-poolsize`：矛盾是「儀器」造成的，不是效應。**

| 儀器 | 讀數 | 可引用嗎 |
|---|---|---|
| server／HTTP（`decode_sweep.py`，交付 regime，**同場配對 ＋ 臂序相反兩輪**） | 8 GiB **10.74** vs 4 GiB **8.43 t/s ⇒ −21.4%**；6 GiB 9.21（−13.8%）、3 GiB 7.08（−33.8%） | ✅（`docs/CACHE_SIZE_AB_2026-09-23.md` §2.1b；計數器逐位元相同：143→71 槽、hit 86–87.5%→67–70.5%、capacity ×4–5） |
| `llama-bench` matrix（`IO_PATH_AB_2026-09-21`） | 4 GiB **10.60** vs 8 GiB **10.28 ⇒ +3.1%** | ❌ 同日已證**無法分辨池軸**（同支啟動自己的三個 rep 差 1.1–2.7×） |

⇒ 白皮書那條「8G→4G −21%（0.79 倍速）」**成立**，其出處就是 `CACHE_SIZE_AB`；
節點原本引的 `+3.1%` 是**不可引用的儀器**。**3／4 GiB 皆不可選**（−22～−34%），8 GiB 維持生產配置。
池是**前提**不是槓桿（0 GiB 物理不可行：模型 13,026 MB 已超 GPU 建議工作集 11,453 MB，`CGC-METAL-FAIL status 5`）。
**唯一未開的格子是「加大池」（8→10 GiB）**；業主＝既有驅動 `decode_sweep.py --arms baseline,pool-10g`（成本 ~10 分鐘），
因 16 GB 上會加深超訂，**預期為假**。惟兩個數字都是 `prod25` ⇒ 依新規則**不結案**。

**(b) `score-leaderboard`：17.669 t/s 降級為「分布尾端的一次抽樣」。**

它是 `r4_b_k2` 的**第一次啟動**、`-r 1`（`stddev 0` 是必然），需要 **26 rounds × 139.3 ms**；
同臂／同 cell／同 shape 的**三次全新啟動**是 **7.63／10.51／8.17**（mean 8.77）對原本 **17.67／11.95／10.74**（mean 13.45）
⇒ 儀器沒故障、沒截窗、沒貼錯 cell，是抽到分布尾端（`docs/K3_PAIR_CERT_VERDICT_2026-09-28.md` §5.1）。
而且它是 `prod25`＝MTP on（**另一個輸出函數**）、非 §5.0 認可口徑。
⇒ **「目前最好」＝交付錨點 11.703 t/s**；17.669 **不得再出現在任何 commit 標題／錨點／「要打敗的數字」**。

### 三、節點文字：就地更新（7 筆）

`mindmap.json` 本輪更新 7 個節點（原本列為「建議更新」的 5 筆 ＋ 兩個帳目）：
`exp-prodnew-decode20-g4miss`（閘已過 6.09%）、`miss-3a`（成本 UNRESOLVED）、`m-decode25`（算術換過、12.57 是 MTP-on）、
`exp-s2-overlap`（segment／gap 已結、殘項是 Metal 側延遲）、`s1-segbatch`（補可交付上界 19.2–24.5）、
`io-poolsize` ＋ `score-leaderboard`（上述仲裁）。
兩個節點（`m-decode25`、`io-poolsize`）另加機器可讀的 `no_throughput_claim`——它們的 res 含**轉引**數字，
**不是**吞吐主張；空洞守門因此回到基線 **1/55**（唯一一筆是 `score-leaderboard` 的舊產物缺成對 stderr log，屬既存）。

### 四、看板產生鏈（單一來源 ⇒ 三個產物）

* 資料源：`scripts/check/decode_board_2026-09-29.yaml`（判定／預期／立項／結案）＋ `docs/mindmap/mindmap.json`（節點）。
* 產生器：`scripts/check/decode_board_build.py`（build／`--check`／`--selftest` 7 案例）。
* 產物：`docs/mindmap/prefill250decode20.html`（看板）＋ `docs/mindmap/subgoals/<id>.html|md`（**逐子目標 15 頁**：預期 ms/step 與 t/s、
  現況、驗收／否證、**立項卡**、結案判準（profile／入口／實測）、**對應節點與其產物／log 連結**）＋ `subgoals/index`。
* **已掛進總圖**：`docs/mindmap/index.html` 標頭新增「子目標看板 ｜ 逐子目標 15 頁」兩個入口。
* 單一入口：`scripts/check/board_pipeline.sh`（重建：看板 → 逐子目標頁 → 總圖 → 逐節點白皮書 55 對）；
  `--check` 只驗（`decode_board --check`、`mindmap --check`、`mindmap_void_check`）。
* 本輪**新立項 9 張卡**（全部過 harness 的 `_charter_gate`）：`e-s1-base-calibrate`、`e-b-accpatch-feasibility`、
  `e-fill-term-price`、`e-prefetch-prereq`、`e-shape-knobs`、`e-sixth-axis`、`e-beyond-ceiling`、`e-clean-window`、`e-mtp-m-acc`。
  加上既有 6 張（`e-s1-g3-zeroslot`／`e-s1-predict-recompute`／`exp-singlesubmit-fillahead`／`e-asyncfill-recompute`／
  `exp-churn-delivery-630`／`exp-m-draft-cost`／`exp-kt-bracket`／`exp-k3-pair-cert`）⇒ **15 個子目標全部有業主**。

---

## §48 未結案子目標的再 review ＋ 節點文字清整 ＋ 三個過時節點的移除（2026-09-29 第二輪）

### 一、先講這一輪**沒有**做的事

原定「在 prod-new 交付 cell 跑一場乾淨窗口錨點」**沒有起跑**：第一次嘗試用 `timeout 560 …` 包住 harness，
而這台 macOS 沒有 `timeout`（`bash: line 11: timeout: command not found`）⇒ 指令在 0 秒內失敗，**沒有任何讀數產生**。
正確的作法是拿掉外層 `timeout`、改用 harness 自己的 `--arm-timeout`／`--stall-watch`（那兩支就是為此存在的）。
**本節 0 跑**：全部是唯讀 review ＋ 既有產物轉引 ＋ 一次窗口閘唯讀讀數。

**窗口閘當下的讀數（唯一的新數字，唯讀）**：`server_window.decision()` → **`admits=False`**，
`need_mb=8000`、`reclaimable=7362 MB`（缺口 ~638 MB）。依 L25-3 的 `on_fail`，不降 `NEED_MB`、不換 cell
⇒ **L25-3 的「乾淨窗口」今天拿不到，而且缺口的量級已被指名**（不是「不知道為什麼拿不到」）。

### 二、14 個未結案子目標的再 review（依新規則逐條）

| id | 本輪 review 的結論 | 缺什麼（可執行） |
|---|---|---|
| **L20-1** 底校準 | 兩個底仍各來自不同組合（49.18 預設 cell／36.80 交付 cell ＋ `_DBG/_COST`）；**未新增證據**（本輪 0 跑） | 同一組 S1 開關、同 cell、成對（卡 `e-s1-base-calibrate`）——**與 L25-3 同一場就能一起拿** |
| **L20-2** 武裝 G3 | 不變：單獨開 no-op，需 `CGC_SLOT_TABLE_GPU=1`；主端點是**計數器翻轉** | 2 趟、不動 `src/`（卡已立） |
| **L20-3** B 補丁 | 不變：形狀已裁定（320 op/step 固定）、成本 1.60–3.20、`ggml_acc` 在 src/ 0 命中 | 可行性尖兵（卡 `e-b-accpatch-feasibility`），**必須排在 L20-1／L20-4 之後** |
| **L20-4** fill 定價 | 不變：一個 term 四個數字（1.60／3.955／5.41／8.3）；**本輪新增**：`io-nofill` 的節點文字已補上立項連結 | 唯讀口徑對照即可裁定（最便宜的一格） |
| **L20-5** 餵料搬家 | 不變：餵料仍掛在 `CGC_MISS_MASK_DBG` 的回讀迴圈 | P1 搬家（卡 `exp-singlesubmit-fillahead`）；**先搬家再刪回讀**（順序不可反） |
| **L20-6** 替代機制 | **結案（排除）**（本頁唯一結案） | — |
| **L20-7** rho／prebind | 不變：判活但有前置（每步 4.76 ms GPU 插入）；兩者同價不可相加 | 先付前置（卡 `e-prefetch-prereq`） |
| **L20-8** churn 重檢 | 不變：未跑；它是 L20-1／5 的前提題（42.3% 是 `ntok=4` verify 桶） | 在 `ntok=1` 主 context 重測（卡 `exp-churn-delivery-630`） |
| **L20-9** shape-knobs | 本輪**把「指名那一處 switch case」正式立項**（原本只是節點註記） | 唯讀指名 ＋ 一次邊界量測（卡 `e-shape-knobs`） |
| **L25-1** 第六條軸 | 本輪**立項**（原本未立）；判定產出是「有無候選乘數」 | 唯讀盤點 bytes／steps（卡 `e-sixth-axis`） |
| **L25-2** 超過上界 | 本輪**立項**（原本未指名）；最低要求 41.2 → ≤40.0 ms | 指名一格＋上界外的機制（卡 `e-beyond-ceiling`） |
| **L25-3** 乾淨窗口 | **本輪新增實測**：閘門 `admits=False`（缺口 ~638 MB）⇒ 現在拿不到，且原因被指名 | 等窗口，或依 `on_fail` 回報盒子狀態；**不降門檻** |
| **L25-4** MTP 決策 | 不變：交付 off；on 是另一個輸出函數；`exp-m-draft-cost` 的 res 已改寫（**未跑（有卡、無讀數）**＋ E-A 支臂 draft 幾乎沒跑的警告） | 產品決策（不是技術題） |
| **L25-5** m／acc | 不變：2× 已判不成立；改攻 m ≤0.30 | 卡 `e-mtp-m-acc`（prod25，已附 `non_prod_reason`） |
| **L25-6** 口徑認證 | 不變：`k3-swing` 16.4% 未認證（n=5、t=2.08 < 2.776）；17.669 已降級 | 加大配對 n（卡 `exp-k3-pair-cert`） |

**這一輪 review 的淨結果**：沒有任何子目標升到結案；**新增的是「缺什麼」的精確度**（L25-3 有了量化的 blocker，
L20-9／L25-1／L25-2 從「未立項」變成「有卡、有預期值」），以及兩個統計更正：
看板摘要的結案數由誤寫的 `3 / 15` 更正為 **`1 / 15`**（其餘 14 格未結案）。

### 三、節點文字：本輪再更新 11 筆（第二輪）

| 節點 | 舊文字（過時） | 新文字 |
|---|---|---|
| `exp-c-eff-bandwidth` | 「實驗進行中（尚無讀數）」 | **背景（不再獨立跑）**：E-A 已否掉「20% 掉到 SSD」（f≈1.5%）⇒ 失效項是達到的帶寬本身；無讀數不主張值 |
| `exp-c-read-issue` | 「實驗進行中（尚無讀數）」 | **已由同題判詞結清**：`io-shape` 量過同一問題，收益 2.9% < 3% ⇒ 背景 |
| `exp-m-draft-cost` | 「實驗進行中（尚無讀數）」 | **未跑（有卡、無讀數）**＋模型（k→∞ **24.59**）＋**取臂警告**（E-A 支臂 draft 幾乎沒跑 ⇒ 對 m 無代表性） |
| `exp-caliber-calibration` | 「實驗進行中（尚無讀數）」 | **未跑（有卡、無讀數）**＋四個「步」的具體數字（167.81／207.8／139.46／99.39 ms）與它決定哪個天花板 |
| `sys-window` | 只有「已建單一來源」 | ＋**09-29 21:29 實測 `admits=False`（need 8000／reclaimable 7362）** |
| `m-prefill250` | 「達標但 decode 未超越 12.57」 | **已認證（C1）**（296.24／乾淨視窗 283.01） |
| `m-total` | 只有「空」 | ＋結案規則下的定義（需 prod-new ＋ harness bench 同場成對） |
| `io-nofill` | 端到端 reps=3 仍缺 | ＋**5× 口徑差已立項**（`e-fill-term-price`） |
| `shape-knobs` | BEST_SHAPE ★ 註記 | ＋**該 switch case 已立項**（`e-shape-knobs`） |
| `mtp-ksweep` | 模型／最佳 k 不可移植 | ＋**口徑界線**：12.57／12.62 屬 MTP-on 函數 ⇒ 只在 prod25 內部有效 |
| `io-poolsize`／`score-leaderboard`／`m-decode25`／`miss-3a`／`exp-s2-overlap`／`s1-segbatch`／`exp-prodnew-decode20-g4miss` | （第一輪已更新） | — |

第二輪結束後，`mindmap.json` 裡**已經沒有任何節點的文字是「實驗進行中（尚無讀數）」**
（唯一出現該字串的地方是 `removed` ledger 裡引述被移除節點的舊文）。

### 四、過時節點：移除 3 個（附 `removed` ledger，可回溯）

判準：**沒有任何讀數、且內容已被別的節點或判詞取代／不屬於本線判決**。三個都寫進 `mindmap.json` 的新頂層鍵 `removed`
（`id`／`when`／`tier`／`sub`／`why`／`evidence_lives_in`），所以「移除」不是靜默的：

| 移除 | 為什麼 | 證據去哪裡 |
|---|---|---|
| `exp-mtp-retro`（3a／M） | 純佔位：res 只有「實驗進行中（尚無讀數）」、只有假設、無產物；MTP 活躍節點是 `mtp-caliper`／`mtp-2x`／`exp-m-draft-cost`，且交付口徑已定 MTP off | `docs/MTP_CTX_REPRODUCIBLE_2026-09-26.md`；卡片 `charters/exp-mtp-retro.yaml` **保留** |
| `misc-omlx`（4／C） | 純指標節點：res =「判死（見 OMLX_VERIFY_KERNEL_VERDICT）」，自己沒有任何數字；同類判詞已在 `g4`（judged 0）與 `k3-price`（邊際 17.9 µs） | `docs/OMLX_VERIFY_KERNEL_VERDICT_2026-09-24.md` |
| `na-softpool`（4／na） | 節點自陳「⚠ **推定**；非本線判決」（零外部引用統計）——把推定當節點留在地圖上就是過時內容 | 原 docs 清單仍在檔案系統內；白皮書頁已刪 |

**保留的**：所有帶真實判詞的 tier-4 節點（`s1-refuted`／`miss-3b`／`miss-3c`／`mtp-accept`／`mtp-rsl`／
`mtp-verify-opt`／`k-series`／`g4`／`io-shape`／`io-mw`／`io-layer-ahead`／`io-mpf`／`io-constraint`／`m3`／`na-olddata`）
—— 契約要求作廢與判死的記錄留在分類裡（否則最大的一塊時間會無人認領），且它們各自帶數字或判詞，不是佔位。
`na-entry`／`na-crossline` 是**索引容器**（承載 194 份報告的對映）⇒ 保留。

**連帶同步**：白皮書 `docs/mindmap/briefs/{exp-mtp-retro,misc-omlx,na-softpool}.*` 已刪除；
逐節點白皮書 **55 → 52 對**；對映報告 206 → 194 份；看板 YAML 移除指向 `exp-mtp-retro` 的 `recheck` 列
（D1／D2 完備性稽核仍 PASS）。

### 五、驗證（本輪）

| 檢查 | 結果 |
|---|---|
| `decode_board_build --check` | **PASS**（33 產物、0 問題；結案 1／15） |
| `mindmap_build --check` ＋ 重建 | 通過（節點 52、C 軸 9、不適用 10） |
| `mindmap_void_check` | **1/52**（唯一一筆＝`score-leaderboard` 既存缺成對 stderr log；EXEMPT 9 → 節點文字更新後仍合規） |
| `provenance_gate check` | **PASS**（無新 offender、legacy 40） |
| `board_pipeline.sh` | 看板 → 逐子目標頁 → 總圖 → 52 對白皮書，全部同步 |

---

## §49 乾淨窗口的那一對真的跑了：S1 的 36.80 ms 重現，而窗口本身仍是 swap

**時間** 2026-09-29 21:33–21:36 · 入口 `harness bench`（**沒有**外層 `timeout`；改用 `--arm-timeout 900 --stall-watch 300`）
· cell `(default)`＝p2048／gen 128／depths 512／**warm-skip 64**／fixed-fill-seed 1／reps 3 · MTP off · 每個產物都有成對 stderr log
· 產物 `Backup/clean_window_2026-09-29/{anchor_1,s1_1}.json`

### 一、窗口閘為什麼 `admits=False`（先回答這個，因為它決定要不要跑）

| 探針 | 判定 | 依據 |
|---|---|---|
| **共用窗口探針**（`server_window.decision`） | **`admits=False`** | `harness:memory = False`：**reclaimable 7245 MB < need 8000 MB**；另三項全過（port 空、無 foreign llama、compressor 安靜） |
| **launcher 自己的探針** | `admits=True` | class `full-mtp`、free 85% ≥ req 40%、other_servers 0 |
| ⇒ 兩者 **DISAGREE**，`binding=harness` | | 這是「檔次不同」而不是「盒子忙」（`_verdict` 的註解就是這樣寫的） |
| **bench 起跑閘**（state／memory／metal／base_check） | **全 `ok`，`refused_preflight=False`** | 量測確實跑了 ⇒ 「跑不動」不是事實；「跑了會髒」才是 |

**誰佔著那 7000–8000 MB**：不是引擎（0 個 llama 行程），是**使用者的 app**（WorkBuddy Helper 1402 MB、Freebuff bun 677 MB、
Freebuff Helper 538 MB、Doubao Helper 391 MB、WorkBuddy Electron 303 MB…）＋ **8.2 GB swap 存量**（別條線留下的，P0/P1/P2 管不到）。
`box_gate.swap_stock_label` 自己標明：**存量是標籤、不是閘**（起跑閘是壓縮機安靜度）⇒ 存量本身不降速，但它吃掉了探針要的可回收量。

### 二、讀數（成對、同場、同 cell）

| 臂 | prefill | decode | **ms/step** | hit% | thermal（worst） | swap growth | attribution |
|---|---:|---:|---:|---:|---|---:|---|
| **A 控制**（`prod-new`） | 290.88 ± 10.05 | **11.327 ± 0.349** | **88.29** | 96.1 | NOMINAL | **+1768 MiB** | **swap** |
| **B S1**（`+CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`） | **364.06 ± 1.56** | **27.526 ± 0.228** | **36.33** | **100.0** | MODERATE | **+851 MiB** | **swap** |
| 成對差 | +73.18（+25.2%） | **+16.20 t/s（+143.0%）** | **−51.96 ms（−58.9%）** | | | | |

### 三、兩個結論（一個是關於 S1，一個是關於窗口）

**(1) S1 的「36.80 ms」重現了，而且不是在 `_DBG`／`_COST` 之下重現的。**
今天 36.33 ms（27.526 ± 0.228）對歷史 36.80 ms（27.17 ± 0.27）——差 0.5 ms，落在噪音帶內；
而歷史那一筆是**帶 debug 開關**測的、今天沒有 ⇒ **debug 開關不是產生 36.8 的原因**。
同時把舊的「49.18 ms」定位清楚了：那是**另一個 cell 的一支**，不是同一個量。
⇒ L20-1 的問題不再是「哪個底才是真的」，而是「**36.3 ms 這一側已經兩次獨立重現**」；
依 §43 的算式，天花板因此站在 **24.5** 那一側（19.2 是 49.18 那一側的產物）。

**(2) 但窗口沒有變乾淨——而且「不乾淨的代價」現在是量到的，不是推測的。**
兩臂都 `attribution=swap`（+1768／+851 MiB，門檻 500）⇒ **這兩個 t/s 不可引用**（契約 §5.1）。
換句話說：**L20-1 與 L25-3 是同一個 blocker**——36.3 ms 的可引用版本需要 `attribution=none`，
而那需要「可回收 ≥ 8000 MB」，今天的實得是 7245 MB。
依 L25-3 的 `on_fail`：**不降 `NEED_MB`、不換 cell**；能改變這一格的合法手段只有「讓盒子多出 ~800 MB 可回收」
（關掉使用者 app／等 swap 回吐／別的線收工），不是重跑。

### 四、樹上與驗證

看板 `L20-1`（state 改為「底已收斂到交付 cell：36.33 ms 重現；缺的是乾淨窗口」、evidence 換成今日成對）與
`L25-3`（state/now/evidence 換成今日實跑：`admits=False` ＋ 兩臂 swap ⇒ VOID）；
`mindmap.json` 的 `s1-segbatch` 與 `sys-window` 補上這對讀數（兩個節點都已有 `no_throughput_claim` 或只記 MB，不合規數維持 1/52）。
`board_pipeline.sh` 重建全鏈；`decode_board_build --check` PASS（結案仍 **1／15**）；
`mindmap_void_check` **1/52**；`provenance_gate check` PASS。

---

## §50 §41 的判死依據有一格是錯的：25.64 t/s 那一格**池是活的**，缺的是 B 半

**問題**：§41 §五(3) 說「越線的格子機制同一個 ⇒ 無 fill、池子凍結（misses 41.8%）」，把 27.17 與 25.64 綁在一起。
**事實**（樹上原文）：

| 越線格 | step／t/s | 池子狀態 | 錯的性質 |
|---|---:|---|---|
| **27.17 ± 0.27**（`_DBG/_COST`、交付 cell） | 36.8 ms | `gather (ensure) 0/0` ⇒ **凍結**（41.8% 提交時未駐留） | 選取不做 ⇒ 每步錯 43% |
| **24.88**（凍結上限探針） | 40.2 ms | 同上 | 同上 |
| **25.64**（**`fillahead` A 半**） | **39.0 ms** | **活**：`prefetch=2854/0`、`feeds=1560`、`CGC-RB-FEED` 有印、**`file_reads>0`**、miss **42.9% → 6.1%**（P0 五項驗收全過） | **只錯那 6.1%**（≈19 個 (layer,expert)/步，B 半未做） |

⇒ 25.64 是**唯一「越線且已過 G4 的 ≤25% 閘」的一格**，它的池**在餵**，缺的是**重算**，不是填池。

### 判死的算式因此要重寫（結論留著，margin 完全不同）

| 版本 | 算式 | 結果 | 距 40.0 |
|---|---|---:|---:|
| §41 原版（把 25.64 當凍結臂） | 「缺口 98% 與正確性同一件事」 | — | 量級差距 |
| **§50 修正版** | `39.0 ms（fillahead A 半，已過閘）` **＋** `B 半重算 1.60–3.20 ms`（§35 已定價） | **40.6–42.2 ms ＝ 23.7–24.6 t/s** | **差 0.6–2.2 ms** |

⇒ **判死不變（仍差 0.6–2.2 ms 到 40.0），但它不再立在「正確性吃掉 98%」上，而是立在「一個已定價補丁的邊界」上**——
而那個 margin **落在**本文件已量到的窗口漂移與底漂移（§49：同日控制臂 88.29 vs 認證 85.45 ＝ +3.3%）**之內**。
依 §39 §三 的說法，這使 25 的狀態應從「判死」改記為「**未判定**」：它有確定的機制（B 半）、確定的價格（1.60–3.20 ms）、
以及**只差 1–2 ms 的算式**。

### 尚未量到的交叉格（本節唯一的新動作項目）

* **`fillahead` × 交付 cell**：§0 明文記「交付 cell（p0）的同一個量測尚未跑完」；而交付 cell 的同族 step 是 36.8（對預設 cell 的 40.2）
  ⇒ 這個交叉格**從未量過**，而它正是「39.0 ＋ B」可不可能 < 40.0 的決定項。
* 對應子目標：**L20-3（B 半）** 與 **L20-5（餵料搬家／fillahead）** 的交集；卡：`e-b-accpatch-feasibility`、`exp-singlesubmit-fillahead`。
* 入場仍受 §41 §七 三問與 §49 窗口限制：`attribution=none` ＋ thermal NOMINAL 才算成績。

**邊界**：本節 0 跑、0 build，全部轉引（§0 結果表、§34、§35、§41）；25.64／39.0 與 27.17 皆 `attribution=swap` ⇒ **仍不可當成績**。

### §50 落地：判詞由「判死」改記「未判定」

**改了什麼、改在哪**（本輪**只動文字與看板**，0 跑、0 build、未動 `src/`）：

| 位置 | 改動 |
|---|---|
| `m-decode25`（tier 4） | `res` 全文改寫：**「未判定」** ＋ §41 的機制更正（27.17／24.88 ＝ 池凍結；**25.64 ＝ 39.0 ms 池活著**）＋ 修正算式 `39.0 ＋ 1.60–3.20 ＝ 40.6–42.2 ms` ⇒ **差 0.6–2.2 ms**，並指名**邊界＝B 半補丁**（`e-b-accpatch-feasibility`）；`note` 與 `no_throughput_claim` 同步 |
| `exp-caliber-calibration`（3a） | 加「尖例」：margin **0.6–2.2 ms ＝ 1.5–5.5%** ⇒ **小於**它自己記的 `k3-swing` **16.4% 未認證飄移** ⇒ 「連 25 死不死都落在未校準帶內」⇒ 本節點優先序**更高** |
| `exp-prodnew-decode20-g4miss`（3a） | 記明「**越線且池活著**的那條路就是本節點的機制」（fillahead A 半、miss 6.1%、`file_reads>0`），缺的是重算那 6.1%（1.60–3.20 ms）|
| `s1-segbatch`（3a） | 記明越線且池活著的一格屬於 `fillahead` 族（非本節點的凍結簽名），並接上 §50 的 margin |
| `s1-asyncgather`（3a） | 記明它的構想與 fillahead **合流**；並指出**兩個估價差一個量級**（~14.5 vs 23.7–24.6）⇒ 差別在「**填池的可引用價還沒定**」（`io-nofill`／L20-4）⇒ 在定價前不要把 25 押在 14.5 上 |
| `m-total`（tier 1） | note 補：① 現在等的是「一個乾淨窗口 ＋ 那支補丁」，不是「新的軸」 |
| 看板 `prefill250decode20.html` | 摘要列 `判死`（bad）→ **`未判定`（warn）**；新增 `.stat-value.warn` 樣式；`reading` 加第 ④ 條；**L25-1／L25-2** 的 `now`／`falsify` 對齊（L25-2 現在指名 B 半補丁）；一處 traceback cell 的措辭對齊 |
| doc | §41 §五(3) 就地更正 ＋ 本節（§50） |

**驗證**：`decode_board_build --check` **PASS**（0 問題；結案仍 **1／15**）、`--selftest` **PASS 7/7**；
`mindmap_build --check` 通過；`mindmap_void_check` **1/52**（維持基線：唯一一筆是 `score-leaderboard` 既存缺成對 log）；
`provenance_gate check` PASS、`void_number_check` **PASS**；`board_pipeline.sh` 全鏈重建。

**一個中途踩到的閘**：給 `s1-asyncgather` 加 §50 文字後，它從 N/A 變成 **VOID-UNBACKED**（「主張了吞吐但無產物」）
——因為我在沒有 run 的節點裡寫了 `t/s` 字樣。照既有機制宣告 `no_throughput_claim`（明文說明 ~14.5 是**推算**、23.7–24.6 是**轉引算式**、E4 比值是**診斷價**）⇒ 回到基線。**這是規則在正常工作，不是被繞過。**

**邊界**：判詞變更是**結構判定與算術更正**，不是新量測。40.6–42.2 ms 的兩個輸入（39.0 與 1.60–3.20）都是**轉引**且 `attribution=swap`；`fillahead × 交付 cell` 那個交叉格**仍未量**。

---

## §51 B 半可行性尖兵（唯讀，0 跑／0 build）：**能建，但價目表漏掉了主項**

**問題**（卡 `e-b-accpatch-feasibility`）：靜態 `ggml_acc` 補丁（每層常備 k 個 acc、固定 320 op/step）的 op 成本能不能落在 **1.60–3.20 ms/step**，且不隨 miss 縮放？

### 一、`sg-b-alloc`：**PASS（附四個條件）**——`ggml_acc` 存在，且 Metal **原生**支援

| 查證 | 結果 | 出處 |
|---|---|---|
| op 存在 | `ggml_acc()`／`ggml_acc_inplace()`；`GGML_OP_ACC` | `ggml.c:2204`／`:2215`；`ggml.h:938` |
| **Metal supports_op** | **`return true`**（`src[0]`／`src[1]` 皆 contiguous rows 且 `src[0]->type == F32`） | `ggml-metal-device.m:1229` |
| Metal 實作 | `ggml_metal_op_acc()`（真的 encoder 路徑） | `ggml-metal-ops.cpp:1117` |
| 每步歸零的 op | `GGML_OP_FILL` 也在 Metal 的 unary 路徑上 | `ggml-metal-ops.cpp:636` |
| 先前的「0 命中」 | 指的是 **llama 層**沒人用（`src/llama.cpp/src/`）；**ggml 核心有實作** ⇒ 「無範例可抄」是**誤判** | 上列兩處 |

**四個條件**（每一條都在原始碼裡有出處）：

1. **必須用 inplace**：非 inplace 的 Metal 版會**先多跑一次完整 cpy kernel**（原始碼自己註解 `not sure how to avoid this`）⇒ 若照預設寫法，dispatcher 數由 320 → **640**。`ggml_acc_inplace` 可避開。
2. **兩側都要 F32**（`GGML_ASSERT` × 3 + supports_op）：combine 的基底與被累加的那一列都得是 F32。
3. **必須 contiguouse rows**：`ggml_view_2d`（本圖實際用的切片方式，`llama-graph.cpp:2865`）符合。
4. **⚠ 必須只作用在 decode**：acc 的形狀是 `[n_embd, n_tokens]`。`n_embd=2048`、F32 ⇒ decode（`n_tokens=1`）**每 slot 8 KiB**、320 slot ＝ **2.56 MB**（無關痛癢）；但 prefill `n_tokens=2048` ⇒ 每 slot **16 MiB**、320 slot ＝ **5.4 GiB** ⇒ **直接把 8 GiB 的池預算吃掉** ⇒ 這條補丁**只能是 decode-only**（否則 prefill ≥250 那一半會一起掛）。

### 二、`sg-b-cost`：**FAIL**——卡上那個價，定價的是**散佈**，不是**重算**

卡上的價目是「**320 op × 5–10 µs ⇒ 1.60–3.20 ms/step**」。三個問題：

| 問題 | 事實 | 出處 |
|---|---|---|
| 那個單價**自己標明是推算** | `ASYNCFILL_P2_ADJUDICATION:356`：「**沒把『每 op 派發 5–10 µs』坐實 —— 那是推算**」；`:113` 同一句 | 樹上 |
| 本機**實測**的每 dispatch 邊際價是 **17.9 µs** | `0.0195%/dispatch/步`（＝ 11.64% ÷ 597）；換算到 85.45 ms 的步 ⇒ ≈ 16.7–17.9 µs ⇒ 320 op ⇒ **5.73 ms**（不是 1.6–3.2） | `CONTRADICTION_CONVERGENCE:29`、`CEILING_STACK:33`、`K3_PRICE_MEASURED` |
| **形狀對得上的**邊際價是 **50–100 µs/op** | MoE 家族 T=1 實測：`down_exps` **100.30**、`gate_exps` **50.79**、`up_exps` **51.25 µs/op（每層一發）** ⇒ 家族 **8.09 ms/步** | `MOE_GATHER_BOUND_2026-09-22 §2` |

**但真正致命的是第二件，與單價無關**：

> **靜態寬度 k 是「正確性」逼出來的，不是「miss 數」決定的。**
> 一層的 8 個選取有可能**全數不在池**（最壞情形），而靜態圖不能按本步實際 miss 數改寬度
> ⇒ **k 必須取 8（＝每層選取數）** ⇒ 這條補丁每步**一定**計算 **40×8 = 320 列**的備援專家
> ⇒ 依**同一支已經量過的儀器**（T=1 家族 8.09 ms/步，320 列就是它），備援補算＝**再跑一次完整的 MoE** ⇒ **+8.09 ms/step**。

於是 B 的總價（全部用樹上實測值）：

| 項 | 價 | 依據 |
|---|---:|---|
| 備援**重算**（320 列，靜態寬度逼出來的） | **+8.09 ms** | `MOE_GATHER_BOUND §2`（T=1，同儀器） |
| 散佈（320 acc；inplace） | +1.6–5.7 ms | 卡上的 5–10 µs **或**實測 17.9 µs |
| ｜合計（悲觀／樂觀） | **+9.7 ～ +13.8 ms** | |
| **它「取代」的東西** | **−3.955 ms**（同步 fill） | §39 ② |
| ⇒ **淨回收** | **−5.7 ～ −9.9 ms／步（負！）** | |

⇒ 卡的 `acceptance.success`（−0.76 ~ −2.36 ms/step）**不成立**；否證的**理由不是**卡上寫的那一條（成本隨 miss 縮放——它其實**不隨 miss 縮放**，正是靜態圖的定義），而是**價格漏掉了主項**：
**被定價的是散佈，沒有被定價的是「把 320 列備援專家算出來」本身。**

### 三、尖兵真正的產物：**一個能救 B 的量（一行儀器的距離）**

成本 ∝ **靜態寬度 k**，與實際 miss 數無關 ⇒ 若能把 k 從 8 降到 **1–2**，備援重算從 8.09 → **1.01–2.02 ms**，B 立刻變成正回收。**能不能降 k ＝「一層最多錯幾個」**：

* 交付 cell 的**每層** miss 分布**在樹上沒有**：權威產物 `Backup/g4miss_2026-09-29/miss_dist_delivery_gen128_fix4066.json` 只有**逐步總量**與**有 miss 的層數**（`CGC-MISSMASK-STEP: step=N misses=<總> layers=<有 miss 的層數>`，例 `step=1 misses=139 layers=39`、`step=2 misses=95 layers=3`），**沒有逐層計數**。
* 現有資料能給的界：**上界 8**（最壞情形，見上）；**平均 ≈0.49/層**（19/39）。*k=2* 需要的保證是「**沒有任何一步的任何一層 > 2**」——**這正是缺的那個量**。
* 取得它**不需要寫補丁**：在既有的 `CGC-MISSMASK-STEP` 那一行多印一個**逐層直方圖**（`misses=[l1..l39]`）即可 ⇒ **一行儀器改動 + 一趟既有臂** ⇒ 直接決定 B 可行或不可行，以及 k=1／2／8。

### 四、對 §50 的連帶更正（**「未判定」的復活路被這一節量死**）

§50 的算式是 `39.0 ＋ B 半 1.60–3.20 ＝ 40.6–42.2 ms`（margin 0.6–2.2 ms）。把 B 換成**上表實測價**：

| 情形 | 算式 | 結果 | 對 40.0 |
|---|---|---:|---|
| B 樂觀（散佈 1.6 ＋ 重算 8.09） | `39.0 ＋ 9.7` | **48.7 ms ＝ 20.5 t/s** | 差 **8.7 ms** |
| B 悲觀（5.7 ＋ 8.09） | `39.0 ＋ 13.8` | **52.8 ms ＝ 18.9 t/s** | 差 **12.8 ms** |
| （§50 原本假設） | `39.0 ＋ 1.60–3.20` | 40.6–42.2 ms | 差 0.6–2.2 ms |

⇒ **L25-2 那一格（「讓某格超過自己的已量上界」＝指名 B）要撤回**；decode ≥25 在**已知軸上**因此**回到「判死」**，而且這一次的依據是**實測單價 × 靜態寬度的結構限制**，不是假設。
唯一還沒被判死的仍是 **L25-1（第六條軸）**。

### 五、方向重排（本節的結論）

1. **不要現在寫那 300 行 acc 補丁**：它的天花板（備援重算 8.09 ms）已經低於它要取代的 fill（3.955 ms）。
2. **先補那一行儀器**（逐層 miss 直方圖）⇒ 若「最大 ≤2」，B 的重算掉到 1.0–2.0 ms，**B 復活**；若出現 3+，B 需要 fallback 或維持判死。**這是本卡唯一值得先做的事。**
3. **`on_fail` 指定的 L20-6（asyncgather）與 B 共用同一個 blocker**（靜態寬度 ⇒ 補算按 k 計價），所以兩者必須**一起重定價**，不能只換機制。
4. 便宜的替代（重算以外的路）：**讓 fill 在 gather 之前落地**（＝ §30／§39 的「leaf 讓誰寫」），那是**順序**問題、不是**成本**問題——但它的代價已被量過一次（racy／`SUBMIT_AHEAD`）⇒ 未開採，屬 `sg-order-legal` 的新機制。

**邊界**：本節 **0 跑、0 build、唯讀**（`grep`／`sed` 讀原始碼與既有產物）；**沒有**新的實測；`8.09 ms` 是**同儀器、同 T、同模型**的既有讀數，`17.9 µs` 是**通用**邊際價；「k 必須 8」是**最壞情形**論證，其平均情形（0.49/層）尚未量。**未動 `src/`、未 commit。**

---

## §52 fillahead × 交付 cell 的成對：**池活著時落在 36.8 那一側（37.8 ms）**

**這一節回答 §50／§51 那個「從未量過的交叉格」**：把 fillahead（A 半，池活著）搬到**交付 cell** 上，與同窗口的控制臂成對。

**入口**：`harness bench`，cell `delivery`＝**p0／b512／ctx4096**，`gen 128／depths 512／reps 3／warm-skip 64／fixed-fill-seed 1`
（與 §33 的 `...p0_n128_d512_r3` 同尺）·MTP off ·成對 stderr log（`pair_gate.paired=true`，903772 B）·`--arm-timeout 600 --stall-watch 300`
**兩臂都需要兩道逃生口（逐字記在產物裡）**：`CGC_WINDOW_OVERRIDE=1`（壓縮機流量在 0 ↔ 240 MiB/s 之間跳動）＋
`CGC_IGNORE_STATE_BUDGET=…`（`available 3057–3593 MiB < 4000 MiB 地板`；**Metal 預算閘是 `ok=true`**）。

| 臂 | env（逐字） | tg t/s | **ms/step** | hit% | thermal | swap growth | attribution |
|---|---|---:|---:|---:|---|---:|---|
| **A 控制** | `prod-new` | 10.28 ± 0.34 | **97.28** | 96.7 | NOMINAL (n=118) | **+6511.8 MiB** | `swap` |
| **B fillahead** | `+CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_SPAC_DBG=1` | **26.45 ± 0.51** | **37.81** | 100.0 | NOMINAL (n=57) | +1379.3 MiB | `swap` |
| 成對差 | | **+16.17（+157%）** | **−59.47 ms（−61%）** | | | | |

產物 `Backup/fillahead_delivery_2026-09-29/{ctl_run,filla_run}.json`（＋各自 live stderr log）。

### 一、直接回答：**36.8 那一側，不是 39.0**

| 候選 | 值 | cell／歸因 |
|---|---:|---|
| 「39.0」 | 39.0 ms（25.64 t/s） | **預設 cell（p2048）**、`attribution=both`（HEAVY＋swap 3513）|
| 「36.8」 | 36.8 ms（27.17 t/s） | **cell 未逐筆核實**：§41 記的是 tag `PROD_NEW_DECODE20_G4MISS:70`（`swap`、帶 `_DBG/_COST`）；樹上同族有兩支候選——p2048（`Backup/mtp_off_clean/res_2026-09-29c.json`）與 p0／ctx4096（`Backup/exp_runs/exp-singlesubmit-fillahead_20260929_012703.json`，`attribution=both`）|
| **本次（fillahead × 交付 cell）** | **37.81 ms（26.45 t/s）** | **交付 cell（p0）**、`swap`、**`instrument_gate=BOUND`** |

⇒ 交付 cell 上、**池真的在餵**（量具閘判定，不是我的判讀）的 step 是 **37.81 ms ⇒ 26.45 t/s**，
**比 39.0 快 1.2 ms、離 36.8 只差 1.0 ms** ⇒ `39.0` 與 `36.8` 的差主要是**cell／場次**，不是「餵料的代價」。

### 二、「池活著」這件事現在是**閘門判定**（升級，不是轉引）

`--require-instrument rb_feed,prefetch` 的第五道閘門回報 **`instrument.verdict=BOUND`**：
`mm_pub_n_leaf=39`、`mm_pub_wrote=39`、`spac=1564`、**`rb_feed=14000`**、**`prefetch=30301`**。
計數器也一致：**`file_reads` 82932（對照 26352，3.1×）**、`fill_batch` 累計 **14.89 s（對照 6.01 s）** ——
餵料的代價第一次在交付 cell 上是**看得見的**（多讀 2.18× 的檔案、多花 2.5× 的 fill 時間），而它**沒有出現在步時上**（37.8 ms）。

### 三、兩條必須一起講的限制

1. **兩臂都 `attribution=swap` ⇒ 沒有一個數字可引用**（契約 §5.1）。改寫不了 11.703 的錨點，也補不了 L25-3 的結案條件。
2. **成對的 Δ 也**不能全額歸給那個開關：控制臂的 swap 成長（**+6511.8 MiB**）是 fillahead 臂（**+1379.3**）的 **4.7 倍**
   ⇒ 控制臂那一邊**自己在換頁**，所以 −59.47 ms 這個差**被控制臂的 thrash 放大了**（§41 §八 的同一條紀律：跨臂的 ms 差不可當證據）。

### 四、對 §51 的連帶：底座換了，結論不變

§51 用的是 `39.0 ＋ B(9.7~13.8)`。把底座換成本次**交付 cell 實測**的 37.81：

| 算式 | 結果 | 對 40.0 |
|---|---:|---|
| `37.81 ＋ 9.7（B 樂觀）` | **47.5 ms ＝ 21.05 t/s** | 差 **7.5 ms** |
| `37.81 ＋ 13.8（B 悲觀）` | **51.6 ms ＝ 19.4 t/s** | 差 **11.6 ms** |

⇒ **25 仍差 7.5–11.6 ms，§51 的判死成立**（而且底座這次是**同一格、池活著**的讀數，不是另一個 cell 的轉引）。
⇒ 也順帶更正 §50 的算式來源：`39.0` 是**預設 cell** 的讀數，**交付 cell 的同一族是 37.81**。

**邊界**：本節 2 臂 1 窗口；兩臂皆 `swap`、且啟動用了兩道逃生口（理由逐字留在產物）⇒ **一律 VOID**；
`37.81` 只能當**同窗口成對**的診斷，**不得**寫進成績面。**未動 `src/`、未 commit。**

---

## §53 窗口攻堅：**記憶體那一半拿下來了，`attribution=none` 的錨點仍然沒有**

**目標**：讓共用窗口探針的記憶體項過關（`reclaimable ≥ 8000 MB`）⇒ 取得**第一場 `attribution=none`** 的交付 cell 錨點。

### 一、這道閘實際在算什麼（先把定義釘住）

`reclaimable = (Pages free ＋ Pages purgeable ＋ Pages inactive) × 16 KiB`，門檻 **8000 MB**（＝512,000 頁）
——**不是** `Pages free`（macOS 刻意把它壓到近零）。22:0x 的實測缺口是 **1438 MB**（不是 800）。

### 二、三個槓桿，兩個有實測結果

| 槓桿 | 量到的效果 | 判詞 |
|---|---|---|
| **idle 回收** | 30 s 內 `6596 → 6482 MB`（**往下漂**） | ❌ 無效 |
| **`purge`（管理員權限，已執行）** | `6482 → 4376 MB`（**−2106**），`Pages free` 66→63 **幾乎不動** | ❌ **反效果**——見下 |
| **關掉 app（你做的）** | 立刻 `8006 MB`，稍後 **`9736 MB`**；壓縮機 quiet 0.00 | ✅ **有效** |

**`purge` 為什麼反效果**：它丟的是 **file-backed cache**，而 file-backed 正是 `Pages inactive` 的主體——
也就是**閘門量的那個量本身**。被丟出來的頁沒有變成 `free`（66→63 MB），而是立刻被**還在跑的程式的匿名需求**吃掉。
⇒ 對這道閘而言，`purge` 把數字**往下推**。（這是可複驗的一條：判準是 `free` 有沒有動。）

### 三、四次錨點嘗試（全部交付 cell，`prod-new`，**無 override**）

| # | 時間 | 起跑 reclaimable | thermal worst | **swap growth** | t/s | ms/step | attribution |
|---|---|---|---:|---|---:|---:|---:|---|
| 1 | 22:13（purge 後） | 4452 | NOMINAL | +4830.8 MiB | 9.70 ± 1.11 | 103.1 | `swap` |
| 2 | 22:18（關 app 後） | 8006 | **HEAVY** | **+338.6** ✅ | 6.11 ± 2.54 | 163.7 | `both` |
| 3 | 22:31（機會式） | 8457 | MODERATE | +1394.6 | 10.64 ± 1.02 | 94.0 | `swap` |
| 4 | 22:36（機會式） | **9736** | MODERATE | +2401.5 | **10.69 ± 0.57** | 93.6 | `swap` |

產物 `Backup/anchor_clean_2026-09-29/anchor{,2,3,4,5}.json`。

**四個讀數**：

1. **記憶體項拿下來了**：第 3、4 場的起跑 `reclaimable` 都 **≥8000**、壓縮機 quiet ⇒ 探針**該格通過**，
   而且**沒有用任何 override** 就起跑了（與 22:0x 那兩場必須 override 的情況相反）。
2. **擋住的是 `swap growth`**：1.39–4.83 GB，**沒有一場 <500 MiB**（唯一 <500 的那場是 #2，而它 thermal HEAVY）。
3. **門檻自己低於這支臂的變異**：harness 的 `residual` 訊息自己寫著——同一支臂一小時內的成長歷是
   **550／670／1971／2801／2984 MiB**，而**閘門的預算只有 500 MiB** ⇒ 原文：**「拒或不拒有一半是抽籤」**。
   今天的 4 場把這句話變成實測：成長落在 **338 / 1394 / 2401 / 4830**。
4. **兩個條件各過一次、從未同時**：#2 是「swap 過、thermal 不過」，#1/#3/#4 是「thermal 過、swap 不過」。

### 四、結論（誠實版）

* **窗口目標達成**：`admits=False → True`（記憶體項），而且**不需逃生口**就能起跑。
* **錨點目標未達成**：**4 場、0 場 `attribution=none`** ⇒ **沒有**新的可引用錨點；11.703 那筆仍然是唯一一個。
* **剩下的 blocker 是結構的、不是時機的**：13.6 GB 模型 ＋ 8 GiB 池 在 16 GB 盒子裡是超額配置
  ⇒「每跑一場就長 1.4–4.8 GB swap」是它運作的方式，而不是某一刻運氣不好。
  ⇒ 要拿到乾淨錨點，方向不是「等一個好時機」，而是**換一個不超額的配置**（更小的池／更小的模型／更大的盒子）——
  而那會**換掉格子的定義**，所以它不能再叫「同一個錨點」。

**邊界**：4 場全部 `attribution≠none` ⇒ **一律 VOID**（不得引用其 t/s）；`purge` 是唯一動到系統狀態的操作（已授權、已記錄）；
**未動 `src/`、未 commit、無殘留行程**。

---

## §54 第一場 `attribution=none` 拿到了——而它**沒有**重現 11.703；「縮池」這條路在**三個層面**都不通

### 一、`attribution=none` ✅（09-29 22:40，8 GiB、交付 cell、**無 override**）

| 項 | 值 |
|---|---|
| `attribution` | **`none`**（`swap_growth=356.44 MiB` < 500；`thermal=NOMINAL`）|
| thermal | **NOMINAL 126/126** |
| 起跑 reclaimable | 8083 MB（≥8000）|
| **tg** | **8.26 ± 3.79 t/s ＝ 121.1 ms/step** |
| cell | `delivery`（p0／b512／ctx4096）、`gen 128／d 512／r 3／warm-skip 64` |

產物 `Backup/pool_window_2026-09-29/anchor8g.json`。**這是本 session 第一場三扇門全過的讀數。**

### 二、但**它比錨點慢 29%，而且離散 46%**

| 這一輪交付 cell 的 8 GiB 嘗試 | swap growth | attribution | t/s |
|---|---:|---|---:|
| 22:13 | +4830.8 | `swap` | 9.70 ± 1.11 |
| 22:18 | **+338.6** | `both`（thermal HEAVY） | 6.11 ± 2.54 |
| 22:31 | +1394.6 | `swap` | 10.64 ± 1.02 |
| 22:36 | +2401.5 | `swap` | 10.69 ± 0.57 |
| **22:40** | **+356.4** | **`none`** ✅ | **8.26 ± 3.79** |
| **對照：認證錨點** | — | `none` | **11.703**（85.45 ms）|

**三個讀數**：

1. **乾淨標籤與好數字無關**：唯一 `none` 的那一場是這一輪**最慢**的一場，而且 `±3.79`（**46%**）——
   比所有 VOID 場都更不穩。⇒ `attribution=none` 是**必要不充分**：它保證「沒有換頁、沒有降頻」，
   **不保證**這一場量到的是同一個東西。
2. 因此 **L25-3 的結案條件（「36.33／27.53 在 `none` 重現」）今天仍然不成立**——
   而且現在知道它不成立的原因**不是**拿不到窗口，是**窗口乾淨時值也不穩**。
3. 錨點 11.703 仍然沒有被複現過（這一輪 5 場：6.11／8.26／9.70／10.64／10.69，全都不是它）。

### 三、「較小的 expert-cache」在三個層面都不通（**實跑驗證，不是推論**）

| 層 | 障礙 | 證據 |
|---|---|---|
| **機制** | `--arm 'prod-new:CGC_EXPERT_CACHE_BYTES=4294967296'` **送不到引擎** | driver **rc=1** 明文：「環境變數沒有送達引擎（量到的會是 profile 預設值）」＋ 指向 `grep -n 'SERVER_ENV+=(' scripts/run_server.sh`。池子是經由**被轉發的 `-expert-cache` CLI**（由 profile 解出）進去的，不是臂的 env |
| **合約** | **`expert_cache 8589934592` 是兩個 cell 宣告的「機器不變量」之一**（與 `ngl 99／load_mode none／threads 8／ctk=ctv=q8_0` 並列）；命令與 §2.5 不一致 ⇒ **fail-closed 拒跑** | `docs/PROD_NEW_TEST_CARD_2026-09-24.md:158-159` ＋ 檔頭宣告 |
| **決定** | 樹上有既有裁決：**「pool 固定 8 GiB…6GB／縮 pool 方向否決」**；且 **「4 GiB 會製造假的 treatment 效果」** | `docs/NEXT_ACTIONS_2026-09-27.md:94`、`docs/MTP_LAUNCH_REQUIRED_PARAMS_2026-09-18.md:45` |

（過程中另外踩到兩道無關的閘：`llama_bench_matrix` 不吃 `--charter`；直跑它需要顯式 `CGC_INTERNAL_CALL=1`。兩者都只是入口禮儀。）

### 四、直接回答「是不是同一個格子」

* **22:40 那一場（8 GiB）與 11.703 是同一個格子**——同機器不變量、同量測形狀、同 profile ⇒ 這是唯一合法的比較，
  而它的結論是「**同格、乾淨、仍差 29%**」。
* **一個 4 GiB 的場次永遠不會是「那個錨點」**：池子大小是 cell 的**宣告不變量**，改了它就是**另一個格子**。
  所以「用較小的池換一個乾淨窗口」這條路，即使能跑，也答不了原本的問題。

### 五、剩下真正可行的兩條

1. **把「乾淨窗口」與「可引用」分開**：今天證明 `none` 不足以讓數字可用 ⇒ 需要一個新的穩定性判準
   （例如同一窗口內 **reps 的離散**，今天 46%），而不是只看 attribution 標籤。
2. **換盒子**：repo 自己的重開條件就寫著 ≥32 GB（`e-asyncfill-recompute.reopen_condition`）。
   16 GB 上「13.6 GB 模型 ⊕ 8 GiB 池」是**宣告層就超額**的組合，這不是排程問題。

**邊界**：本節 1 場 `none`（8.26 ± 3.79 ⇒ 三個 rep 的離散很大，**不建議引用其 t/s**）＋ 3 次入口被拒；
`swap_growth 356.44` 與 `NOMINAL 126/126` 是這一場的**窗口事實**，可引用。**未動 `src/`、未 commit。**

## 55. 引用閘門：用「逐 rep 的離散」而不是 `attribution` 標籤決定一個數字能不能引用（2026-09-29）

§54 的結果是一個反例：**唯一拿到 `attribution=none` 的那一場，是五場裡最慢、最不穩的一場。**
標籤說「窗口乾淨」，但那場的第 3 個 rep 慢了 2.7 倍，`±3.79` 完全由**一個 rep** 扛。
本節把 §15 §四(3) 早就寫成散文的原則（「`none` 不是可重現性保證…一條可以拿去用的基線
應該是重複數次的中位數與散度」）落成**可執行、fail-closed 的閘門**，並接進看板的 build。

### 一、五場交付 cell 讀數的逐 rep 樣子（權威產物）

`-p 0 -n 64 -d 512`、`ctx 4096`、`warm_skip 64`、`reps 3`、`prod-new`、8 GiB 池：

| 場次 | file | t/s | stddev | CV% | **逐 rep `samples_ts`** | max/min | attribution |
|---|---|---:|---:|---:|---|---:|---|
| 22:13 | `anchor_delivery.json` | 9.702 | 1.115 | 11.5 | [10.18, **8.43**, 10.49] | 1.244 | `swap` |
| 22:18 | `anchor2.json` | 6.112 | 2.544 | 41.6 | [8.74, **3.66**, 5.94] | 2.388 | `both`（HEAVY） |
| 22:31 | `anchor4.json` | 10.644 | 1.021 | 9.6 | [**9.62**, 10.66, 11.65] | 1.211 | `swap` |
| 22:36 | `anchor5.json` | 10.692 | 0.572 | 5.4 | [**10.05**, 10.86, 11.16] | 1.110 | `swap` |
| **22:40** | `anchor8g.json` | **8.257** | **3.788** | **45.9** | [10.45, 10.44, **3.88**] | **2.691** | **`none`** |

⇒ 兩個軸是**正交**的：`attribution` 答「窗口乾不乾淨」，逐 rep 離散答「這一場量到的是不是同一個東西」。
`none` 那一場同時是**兩軸都最差**的一場。

### 二、判準（全部沿用既有常數，不新增品味）

一支臂的**一個 row** 要能被引用，必須四項同時成立：

| # | 條件 | 值 | 出處 |
|---|---|---|---|
| R1 | 樣本數 | `len(samples_ts) >= 3` | house bench 標準；**已認證錨點本身就是 `-r 3`** |
| R2 | 全 rep 離散 | `max/min <= 1.10` | `http_duo.py`（2026-09-18 校準：乾淨 1.008–1.034、不乾淨 1.207–1.941） |
| R3 | kept rep 離散 | 同上 | 同上（rep 1 天生偏冷；`--warm-skip`／`platform_ts` 就是為此存在） |
| R4 | 窗口 | `attribution.verdict == none` | 既有合約（**仍必要、只是不再充分**） |
| R5 | （選配）參考帶 | `--reference <t/s>` ⇒ `|Δ| <= 10%` | 本節新增，只在指名參考值時生效 |

另有 **R0（內部自洽）**：回報的 `stddev_ts` 必須與 `samples_ts` 反算值相符（±0.5 pp），
否則 **REFUSE** —— 產物自己不一致時，任何判詞都不能信。
讀不出逐 rep 向量 ⇒ **REFUSE**（「沒有證據說它穩」不是「它穩」）；`reps < 3` ⇒ **THIN**。

**兩支儀器獨立挑中同一個極限**：bench 族的乾淨場落在 1.06–1.09（11.703→**1.061**、11.583→1.023、
27.526→1.01、26.455→1.04），不乾淨場落在 1.24–2.91（9.702→1.244、8.257→2.691、6.112→2.388、
5.821→1.681）；HTTP 族的缺口是 1.034 → 1.207。**1.10 同時落在兩個缺口內。**

⚠ **樣本 floor 兩支儀器不同，而且必須講清楚**：HTTP 那支 quote 的是**中位數**（兩人組的中位數是
退化的 ⇒ 要求 3 個 **kept**，即 `--reps 4`）；本閘門 quote 的是 bench 的 `avg_ts ± stddev`，
house 標準是 3 reps ⇒ floor 是 3 個**樣本**。用 3 個 kept 會把 house 自己的認證錨點一起作廢。

### 三、落地：`quote_gate.py` ＋ 看板 D7

* `scripts/check/quote_gate.py`：判準的**單一定義**（`MIN_REPS`／`SPREAD_LIMIT`／`MIN_KEPT_MEDIAN`
  ＋ `judge()` ＋ CLI ＋ 自測，fixture 全部取自真產物）。`http_duo.py` 已改為 **import** 這兩個常數
  —— 原本兩支儀器各有一套等價的品味，現在判準只有一份。
  判詞：`QUOTABLE`／`UNSTABLE`（離散）／`DIRTY`（窗口）／`THIN`（樣本不足）／`REFUSE`（判不了）。
* **看板 D7**（`decode_board_build.py`）：子目標或認證項宣告的引用判詞必須與閘門**當場**判定一致，
  不一致 ⇒ `--check` rc=1；且 **「結案」不得建立在不可引用的讀數上**（缺 `quote` 或判詞非
  `QUOTABLE` 即失敗）。認證項若真的無從判，必須明文 `quote_ungateable: <理由>`——**「沒判過」與
  「判過」不能長得一樣**。
* 產生鏈：`board_pipeline.sh` 兩條路徑都先跑 `quote_gate.py --selftest`（校準 fixture 11/11）。

### 四、套用結果

| 集合 | 可引用 | 說明 |
|---|---|---|
| 交付 cell 今日 5 場 | **0 / 5** | 4 場擋在 R4（`swap`）、1 場擋在 R2／R3（逐 rep 2.691） |
| 今日其餘臂（fillahead 成對、default cell、g4miss） | **0 / 7** | **全部只輸在 R4**；離散 1.001–1.065 全數合格 |
| 歷史 `mtp_off_clean` 四支（8 列） | **3 / 8** | prefill 306.678、prefill 302.284、decode **11.703**（1.061）✔ |

⇒ 閘門**不是只會拒絕**：它認證了 house 自己的錨點（`res_2026-09-29c.json` ⇒ `QUOTABLE`），
並把 §15 自陳「不可重現」的 09-27 那場（9.311）擋掉。而且它**沒有為那 7 支臂新增任何阻礙**
——那些臂本來就因為 `swap` 不可引用。
閘門是**逐 row** 判的：同一場的 prefill 與 decode 可以一個過、一個不過（09-27 那場正是如此）。

### 五、閘門抓到的第一件事（改寫一個既有說法）

09-27 22:03 的「舊基線」是 **9.311 ± 4.495**（`attribution=none`），§15 用它算「今天 +25.7%」。
它的逐 rep 是 **[4.12, 12.01, 11.80]**：**rep 1 是 4.12，其餘兩個是 12.01／11.80**。

* 全 rep spread = **2.915**（擋）／kept spread = **1.019**（合格）⇒ 若只查 kept，這一場會**溜過去**。
* kept 半邊 ＝ **11.905 t/s**，與認證錨點 **11.703** 幾乎相同。

⇒ §15 那個「+25.7% 進步」**主要是冷啟 rep 的產物**，不是機器變快。
這也是 R2 **同時查兩個向量**的理由，以及「本節點的換算必須同時報 avg 與 kept 半邊」的理由
（已寫進 `exp-caliber-calibration` 節點）。

### 六、邊界（一起講）

1. **門檻校準樣本只有 ~13 列**（bench 9＋HTTP 4）⇒ 門檻本身有不確定度；要改門檻必須用**新的一批場次**，不能因為某個好數字被擋而放寬。
2. **沒有逐 rep 向量的產物判不了**（`REFUSE`，不是「過」）。C1（prefill 296.24）的依據是摘要 §5.2，不是帶 `samples_ts` 的產物 ⇒ 已明文 `quote_ungateable`；要讓它可判就得**重跑那一臂**。
3. **閘門不改變任何現象**：它只決定「這個數字能不能放進成績面」。今日 5 場全不可引用，與它們的 t/s 是多少無關。
4. 它**不取代** `attribution`：R4 仍在。它取代的是「R4 **唯一**」這件事。

## 56. 「同一個格子但差 29%」到底是什麼在變：前提不成立，而且最大的那一項是**量測起點**（2026-09-29）

§54 把 22:40 的 **8.26 t/s** 與認證錨點的 **11.703 t/s** 並排，讀成「同一個格子、差 29%」。
本節把那兩場的**產物逐欄拉平**，結論是三件事疊在一起，而且其中最大的一件**不是機器變慢**。

### 一、先把前提拆掉：那兩個數字不是同一個格子

| 欄位 | 06:56 錨點（`res_2026-09-29c.json`） | 22:40（`anchor8g.json`） | 同／異 |
|---|---|---|---|
| `cell.named_cell` | **`(default)`** | **`delivery`** | **異** |
| row `n_batch` / `n_ubatch` | **5632 / 5632** | **512 / 512** | **異** |
| `cell.prompt` | **2048** | **0** | **異** |
| rows 數（同一次 launch） | **2**（`pp2048` ＋ `tg64`） | **1**（只有 `tg64`） | **異** |
| gen / depths / reps | 128（量 64）／512／3 | 同 | 同 |
| `warm_skip` / `fixed_fill_seed` | 64／1 | 同 | 同 |
| `ctx_size` | 0（derived） | 同 | 同 |
| `load_mode` / `ngl` / kv | none／99／q8_0 | 同 | 同 |
| build / 池 / profile / MTP | `e5d1c0f14`／8192 MB／prod-new／off | 同 | 同 |

⇒ 兩個數字的**唯一 row 級差異**是 `-b/-ub 5632↔512`，而 `-p 2048↔0` 決定**那一場跑不跑 pp 測試**。
而 harness 的 `_cell()` 自己寫著「a number is only comparable to another number from the same cell」，
`cell_contract` 更把 `batch`／`prompt`／`warm_skip` 列為**嚴格維度**（改了 fail-closed 拒跑）。
⇒ 按本 repo 自己的規則，這兩個數字**不可互比**：「同一個格子但差 29%」這個問句的前提不成立。

### 二、同一個格子裡，今天量到的盒子效應只有 **−3.2%**

| 場次 | cell | attribution | tg t/s |
|---|---|---|---:|
| 09-29 06:56 | (default) | `none` | **11.703** |
| 09-29 06:43 | (default) | `swap` | 11.583 |
| 09-29 21:35（今天） | (default) | `swap` | **11.327** |

今天比 06:56 低 **3.2%**（in-cell、同 build、同 profile）⇒ 盒子（swap／thermal／別的 app）解釋 **~3%**，
不是 29%。

### 三、兩格的結構差：**pp 測試在 tg 之前，同一個行程**

矩陣程式碼自己的註解（`llama_bench_matrix.py:975`）：

> Per-row windows, because **ONE launch here measures TWO rows** (a 2048-token prefill and
> a 64-token decode) and they can sit on opposite sides of the thermal line.

* **default cell**：`rows = [pp2048, tg64]`，**同一個行程、pp 先 tg 後**；tg 窗在 pp 窗開始後
  **32 s（06:56）／40 s（21:35）**才開始（`thermal_windows.start_epoch`）。
* **delivery cell**：只有 `tg64` 一列 ⇒ decode 是那個行程量的**第一個東西**（前面只有模型載入、
  bench 暖機、以及每 rep 的 64 token warm-skip）。

整臂的填充代價（`cache` 區塊，同一個計數器口徑）：

| 量 | 06:56 (default) | 21:35 (default) | 22:36 (delivery) | 22:40 (delivery) |
|---|---:|---:|---:|---:|
| `fill_batch_usec` | 26.03 s | 31.01 s | **5.46 s** | **4.93 s** |
| `pread_usec` | 2175.6 s | 2667.1 s | **529.8 s** | **473.9 s** |
| `file_reads` | 82 146 | 82 752 | **26 202** | **26 379** |
| `misses` | 4 784 | 4 991 | 4 175 | 4 232 |

⇒ default 臂在 decode 被量之前，**已經先做掉一整段 2048-token 的填充**（`fill_batch` 與 `pread` 都是
delivery 的 **4.6–5.3 倍**）——也就是說，那兩格的 decode 讀數**不是在同一個「起點狀態」上量的**。

### 四、支持「起點假設」的三條證據（含一條反向的）

1. **reps 的形狀**：default 三場都是**平的**（[11.65, 11.38, 12.07]、[11.74, 11.48, 11.53]、
   [11.71, 11.03, 11.24]）；delivery 出現**單調上升**（[9.62→10.66→**11.65**] ＝ +21%、
   [10.05→10.86→**11.16**] ＝ +11%）。
2. **上升那兩場的末 rep 落在 default 的平台高度**（11.65、11.16 對 11.3–11.7）。
3. **反向證據（很重要）**：default cell 的 tg 窗在**較晚、較熱**的位置（矩陣量過「同一 launch 裡
   `pp` NOMINAL／`tg` HEAVY」）——**晚又熱的那一格反而快** ⇒ 熱與順序不是主因，
   「起點狀態」是更好的候選。

### 五、22:40 的 −29% 是**第三件事**：場內崩（與格子無關）

逐 rep [10.45, 10.44, **3.88**]，其 tg 窗 wall **28.74 s**，其餘三場是 16.4–18.0 s（**1.6–1.75×**）
⇒ rep 3 真的多花了時間，不是算術。引用閘門判 **UNSTABLE**。

### 六、「什麼在變」的清單（依可量性排序）

| # | 候選 | 已經量到的 | 判別方式 |
|---|---|---|---|
| 1 | **量測起點**（有／沒有先做過 2048-token prefill） | 間接：`fill_batch` 5×、reps 上升、末 rep 到平台 | **B1**：`delivery` 的孿生 cell 加大 `warm_skip` |
| 2 | `-b/-ub 5632 ↔ 512` | **未分離**（row 只差這一欄） | **B3**：`(default)` 的孿生 cell 用 `batch 512` |
| 3 | 盒子（swap／thermal／別的 app） | **−3.2%**（同 cell、今天） | 既有窗口閘門的工作 |
| 4 | 場內 rep 崩 | 22:40：rep 3 ×2.7 | 引用閘門（UNSTABLE） |

**B1 與 B3 的預測互相排斥**：B1 成立 ⇒ (default) 的 batch-512 孿生仍 ≈11.5；
B3 成立 ⇒ 加大 warm-skip 的 delivery 仍 ≈9.7。⇒ 兩臂、同一窗口、成對，就能把兩者分開。
⚠ **但兩者都不能用現有旋鈕做**：`batch`／`prompt`／`warm_skip` 是 §2.5 的嚴格維度，而現有的孿生機制
只開放 `reps`／`rep_split` ⇒ 這是一個 **cell 定義的決定**（測試卡新增一格），不是一次「跑得動」的實驗。

### 七、順帶更正我自己寫錯的一格

§52 寫「交付 cell ＝ p0／b512／**ctx4096**」。權威定義（`harness.py:_BENCH_DEFAULTS` ＋ `--cell` 說明）
是 **`--batch 512 --prompt 0`**，`ctx_size = 0`（由 `n_prompt + n_gen + n_depth` 導出）。
「ctx 4096」是 **prod25／server** 那邊的值，不是這個 cell 的欄位。

### 八、這件事對 §54 的影響（一起講）

若起點假設成立，**delivery cell 量的是「冷啟動的 decode」**，而**產品**是「prefill 之後的 decode」
——更接近 default cell。那 §54 的「8.26 vs 11.703 ⇒ 差 29%」就是拿**冷 bench** 比**熱產品**，
兩者本來就不該放在同一根軸上。**但這只是假設**：B1／B3 沒跑之前不能拿它改寫任何判詞。
現在能確定的只有兩句：那兩個數字**不可互比**；而「29%」裡量得到的那一項（盒子）只有 **3%**。

## 57. 主節點的綠燈：**只有「可引用的達標讀數」才變色**，其餘完全不動（2026-09-29）

### 一、規則（一句話）

L20／L25 的標題只在「該層宣告的目標 ≤ 一場**經引用閘門判 `QUOTABLE`** 的實測」時，才加上 `class="layer-met"`
（左側 6px 綠條 ＋ 綠底 ＋ `達標` 徽章）。<b>其餘一律維持原樣</b>：不加徽章、不改底色、不動版面；
「為什麼沒變綠」只寫進 `<h2 title="…">`，所以不必靠推測，hover 就看得到判詞。

這條規則讓「達標」與「好看」分開：<b>綠燈是結論，不是裝飾</b>。一個頁面可以整頁都在講進步，而主節點仍然是黑的——
因為 §55 的閘門還沒有放行任何一場達標讀數。

### 二、資料從哪裡來（看板 YAML 兩個新鍵，沒有就什麼都不做）

- `layer.goal: {metric, target, crit}` —— 這一層的目標（L20 `target: 20.0`、L25 `target: 25.0`，`metric: tps`）。
- `layer.evidence: {artifact, value, quote_verdict, met, note}` —— 這一層「目前最好的一場」是哪一支產物、
  讀數多少、閘門判什麼。`met` 只是<b>宣告</b>，真正的值由 `layer_goal()` 當場算（goal ∩ evidence）；
  `note` 會接在 `title` 後面，負責說「還差多少」。
- **沒有宣告 `goal`／`evidence` 的圖層 ⇒ 不判定、不變色**（`layer_goal()` 回 `None`）——
  這就是「若無則沒有變化」的機器版本，不是靠人記得不要動它。

兩層現在的宣告（都是**未達標**）：

| 層 | 目標 | 目前最好的可引用讀數 | 閘門 | 結果 |
|---|---|---|---|---|
| L20 | decode ≥ **20.0** t/s | `11.703`（`res_2026-09-29c.json`，逐 rep **1.061**） | `QUOTABLE` | 差 **41%** ⇒ 不變色 |
| L25 | decode ≥ **25.0** t/s | `11.703`（同上；§51 已把 25 記回判死） | `QUOTABLE` | 差 **53%** ⇒ 不變色 |

「全語料只有這一個可引用的 decode 讀數」是 §55 的套用結果（今日交付 cell 5 場 0/5）⇒
第二個候選還不存在，綠燈目前**沒有**資料可以亮，這是事實，不是沒接線。

### 三、D8：綠燈不能自己宣告

build 的 D8 逐層複判，任何一項不符就 **FAIL 且不寫檔**（不是靜靜改個顏色）：

1. 有 `goal` 卻沒有 `target`；有 `goal` 卻沒有 `evidence.value`（達標必須有一場實測）。
2. `evidence` 沒指名 `artifact`；`artifact` 判不了（不存在／沒有可判的 llama-bench row／載不進 `quote_gate`）。
3. 宣告的 `quote_verdict` ≠ 閘門<b>當場</b>判的（含檔內 row 判詞不一致的 `MIXED(…)`）。
4. 宣告達標（`met: true`）但判詞 ≠ `QUOTABLE` —— **不可引用的讀數不能變綠**。
5. 宣告的 `met` 與 `goal ∩ evidence` 算出來的不一致。

### 四、實測（這一輪真的跑過，不是推論）

| 探針 | 期待 | 實測 |
|---|---|---|
| 現況（兩層都未達標） | `class="layer-met"` **0** 個 | **0**（`layer-met` 只出現在 CSS 定義） |
| L20 `target` 暫時降到 `11.0`（≤ 11.703） | L20 變綠 | **1** 個，`title` 寫「⇒ 達標」 |
| 只改 `target`、`met` 留在 `false` | D8 擋 | `✗ D8 L20 宣告 met=False，但依 goal ∩ evidence 算是 True` ⇒ **未寫檔** |
| 目標放寬 ＋ `met: true`，但判詞改 `UNSTABLE` | D8 三條全開 | `quote_verdict` 不符／「不可引用的讀數不能變綠」／`met` 不符 ⇒ **FAIL** |
| `--selftest`（含達標／未達標／宣告達標但不可引用三例） | 全過 | **13/13** |

探針跑完都<b>逐字回復</b>（YAML 與 HTML 回到現況），所以現在頁面上的 L20／L25 仍是**沒有顏色**的那一版。

### 五、邊界（一起講）

- 綠燈<b>不改變任何結案判定</b>：結案仍走 §47 的規則（prod-new ＋ harness bench ＋ 達標），
  而主節點綠燈只回答「這一層的目標有沒有被一場可引用的實測跨過」。
- 綠燈<b>不掩蓋沒有讀數</b>：`goal` 宣告了、`evidence` 缺席時 D8 直接擋（不允許「先變綠、之後補證據」）。
- 這一層的目標數字（20.0／25.0）與 `summary` 的「19.2–24.5 天花板」「判死」並存而不矛盾：
  目標是座標，判詞是現況。
- 11.703 來自 `(default)` cell，L20／L25 的目標是<b>交付 cell</b> 的目標 ⇒ `note` 必須寫明這一點（§56），
  否則綠燈會拿兩個不可互比的格子互相認證。

## §58 交付 cell 的第一列可引用讀數（9.377），以及一個差點變成假綠燈的 27.3（2026-09-29 第三輪）

§57 裝好了綠燈（可引用的讀數達標 ⇒ `<h2 class="layer-met">`；否則完全不變色），
但 §57 同時留下一件必須先做的事：**交付 cell 沒有任何可引用的讀數**（今日 5 場 0/5）
⇒ 綠燈就算接好了，也沒有東西可以亮。這一節把那一格補上，順手擋掉一個**會讓兩個主節點一起變綠**的數字。

### 一、先把「有哪些候選」用機器掃出來（不是我挑的）

    python3 scripts/check/quote_gate.py check --glob 'Backup/**/*.json' --json /tmp/qg_all2.json

**全語料 1061 列**（每一列＝一支臂的一個 row），判詞分布：

| 判詞 | 列數 |
|---|---|
| `UNSTABLE` | 494 |
| `DIRTY` | 385 |
| `THIN` | 115 |
| `REFUSE` | 63 |
| `QUOTABLE` | **4** |

其中 `cell=delivery` 的 **30 列裡只有 1 列**可引用：

| t/s | 產物 | 逐 rep | all ／ kept | attribution | 判詞 |
|---|---|---|---|---|---|
| **27.338** | `spac_sweep_2026-09-29/k_sweep_T4_K4.json` | [27.38, 27.24, 27.39] | 1.005 ／ 1.005 | `none` | ~~QUOTABLE~~ → **DIRTY（R5）** |
| 9.909 | `stepbudget_2026-09-29/delivery_base.json` | [9.10, 10.33, 10.30] | 1.134 ／ 1.003 | `none` | `UNSTABLE` |
| **9.377** | `stepbudget_2026-09-29/delivery_fuse.json` | [8.83, 9.59, 9.70] | 1.099 ／ 1.011 | `none` | **`QUOTABLE`** |
| 10.692 | `anchor_clean_2026-09-29/anchor5.json` | [10.05, 10.86, 11.16] | 1.110 ／ — | `swap` | `UNSTABLE` |
| 8.257 | `pool_window_2026-09-29/anchor8g.json` | [10.45, 10.44, 3.88] | 2.691 ／ 2.689 | `none` | `UNSTABLE` |

（表中只列前 5 高；完整 30 列見 `/tmp/qg_all2.json`。交付 cell 的定義＝`cell.prompt=0`、`cell.ctx_size=0`、
`cell.spec_type=""`、`cell.reps=3`、`cell.warm_skip=64`，`anchor_delivery`／`delivery_fuse`／`delivery_base`
三者的 `cell` 字典逐欄相同 ⇒ 同一格。）

⇒ **交付 cell 的第一列可引用讀數是 9.377**（`stepbudget_2026-09-29/delivery_fuse.json`，07:21，
`contract.ok=true`，產物旁有成對的 `delivery_fuse.stderr.log`）。
它的孿生 `delivery_base.json`（同 cell、逐欄同 env，**只差 `CGC_MMV_FUSE`**）報 9.909，但它的**第一個 rep 冷啟**
（9.10）⇒ 全 rep 1.134 越線判 `UNSTABLE`；而它 kept 兩 rep 的平均是 **10.31** ⇒ 兩者其實在**同一帶**，
9.377 只是「冷啟代價小到讓三個 rep 落在 1.10 內」的那一場。這句話必須一起寫，否則 9.377 會被讀成「fuse 比較慢」。

### 二、那個 27.3 為什麼不能引用（這才是本節的主要發現）

`k_sweep_T4_K4` 是 09-29 19:11 由另一條執行緒跑的 SPAC `K` 掃描（`Backup/spac_sweep_2026-09-29/`），
它在**同一個交付 cell** 上報 **27.338 t/s**（逐 rep 1.005、`attribution=none`、`swap_growth=-8.0 MiB`、
`thermal=NOMINAL`）⇒ 相對錨點 9.702 是 **2.8×**。R1–R4 **全過**，而 27.338 **同時 ≥20 與 ≥25**：

> 只要有人把 L20／L25 的 `layer.evidence.artifact` 指到這一列（並宣告 `QUOTABLE`），
> **兩個主節點會一起變綠** —— 而那個數字原始碼自己禁止引用。

反證來自原始碼（不是判詞、不是我對機制的偏好）：

| 旗標 | 出處 | 原文 |
|---|---|---|
| `CGC_MISS_MASK_DBG=1` | `src/llama.cpp/src/llama-context.cpp:3962` | 「DIAGNOSTIC ONLY. One extra synchronize per step…**Never quote throughput from an arm with this on**」（該臂的 stderr 有 **386** 行 `CGC-MISSMASK-STEP` ⇒ 確實開著） |
| `CGC_MISS_MASK_COST=1` | 同上 `:3985` | G1b 的 mask-cost 計時，靠**同一次**回讀 ⇒ 也付那個 synchronize |
| `CGC_SPAC_DBG=1` | `src/llama.cpp/src/llama-expert-cache.cpp:2347` | 逐層成員探針，與 SPAC 饋送共用同一個列印點 |
| `CGC_SEG_BATCH=1` | `llama-context.cpp:4059` | **跳過 per-layer hook**，改由單次提交的回讀路徑補餵（`:4067+` `cgc_rb_seg_batch`）⇒ 餵的是**另一條路徑**，不是「同一個臂快一點」 |

⇒ 診斷 cost 的方向是「偏低」，所以 27.3 的**真值只會更高**；但引用與否不取決於它偏哪一邊，
取決於**它量到的還是不是那個臂**。§34 早就把單次提交臂的 t/s 記成 VOID（池凍結、輸出是 garbage），
這裡只是把同一句話從散文搬進閘門。附帶的旁證：同日同格的 `k_sweep_C`（27.488）／`k_sweep_T1_K32`（26.782）／
`hist_w4`（26.687）全部 `attribution=swap` ⇒ 27.3 這一批是**同一個臂家族**的速度，`T4_K4` 只是碰巧沒換頁。

### 三、閘門補上 R5 臂身分（fail-closed，清單附出處）

`scripts/check/quote_gate.py` 新增 **R5**：臂上掛著「原始碼明文 never-quote」的量具時，
判詞一律 **`DIRTY`**（與窗口項同級：不是「這一場不夠穩」，是「這一場不是那個臂」）。
清單是 `THROUGHPUT_VOID_INSTRUMENTS`（6 個旗標，每一條附 `file:line`）：
`CGC_MISS_MASK_DBG`／`_COST`／`_HIST`、`CGC_MMV_FUSE_DBG`、`CGC_RHO_PROBE`、`CGC_SPAC_DBG`。

判準的三個刻意選擇：

1. **不是「有儀器就髒」**。`CGC_GPU_TIMING`／`CGC_GPU_NODES`／`CGC_GPU_OPS` 這類在 Metal 上記錄
   command buffer 的 `GPUStartTime/GPUEndTime`、且原始碼自陳「unset 時無熱路徑成本」的取樣**不在表內** ——
   否則交付 cell 的那一列 9.377 會被自己的取樣開關擋掉，而它的取樣不在關鍵路徑上。
2. **`=0`／沒開一律不擋**（`CGC_MISS_MASK_DBG=0` 不是武裝）。兩個 selftest 案例把這個邊界釘住。
3. **env 三個地方都要看**：`env`、`extra_env`、`tag` 的 `K=V` 尾串 —— 三種產物各寫一處，
   只看一個就會漏（`k_sweep_T4_K4` 三處都寫，但舊產物只有 `env`）。

套用結果（同一份掃描）：`QUOTABLE` **5 → 4**、`DIRTY` +1；受影響的 32 列全部是那一族診斷臂。
**已宣告的四個 artifact 判詞全部不變**（`s1_1.json`=`DIRTY`、`filla_run.json`=`DIRTY`、
`res_2026-09-29c.json`=`QUOTABLE`、`anchor8g.json`=`UNSTABLE`）⇒ D7／D8 不受影響。

### 四、實測（跑過的，不是推論）

| 探針 | 期待 | 實測 |
|---|---|---|
| `quote_gate --selftest`（＋R5 兩例：診斷臂擋／`=0` 不擋） | 全過 | **16/16** |
| 27.338 那一列 | 由 `QUOTABLE` 轉 **`DIRTY`** | `DIRTY`（理由指名 `CGC_MISS_MASK_DBG`：每步多一次 synchronize ＋ 出處） |
| 交付 cell 那一列 9.377 | `QUOTABLE` | `QUOTABLE` |
| **假綠燈情境**：把 L20 的 `evidence` 指到 27.338、宣告 `QUOTABLE`／`met: true` | build **FAIL 且不寫檔** | `✗ D8 L20 宣告 quote_verdict=QUOTABLE，但閘門現在判 DIRTY` ＋ D4 落後 ⇒ **FAIL（2 個問題）**；探針後 YAML **逐字回復**（md5 相同） |
| 現況 HTML 的綠色標題 | `class="layer-met"` **0** 個 | **0**（CSS 定義不計） |
| `decode_board_build --check` ／ `--selftest` | PASS ／ 13/13 | PASS（0 問題）／13/13 |

### 五、這一節改變了什麼、沒有改變什麼

**改變**：交付 cell 現在有**一列**可引用的 decode 讀數（9.377，`stepbudget/delivery_fuse.json`），
登記在 **L20-10**（格子判別）的 `evidence.quote`；摘要卡的「交付 cell 可引用數」由 `0 / 5` 改為 `1 / 30`
（全語料口徑，避免「5 場」只涵蓋 `anchor_clean` 那一批）；`summary` 的 27.3 標為 R5 判 `DIRTY`。

**沒有改變（重要）**：

- **沒有一個主節點變綠** —— 9.377 < 20.0 < 25.0，L20／L25 維持「未達標、不變色」。補上候選不等於達標，
  這一節的價值是「閘門放行時真的有東西可判」，不是「把燈弄亮」。
- 引用 9.377 時必須一起寫：它的臂帶 **5 個** `CGC_GPU_*`／`CGC_DISPATCH_CENSUS` 取樣開關
  （Metal 記 command buffer 時間，unset 時無熱路徑成本）＋ `CGC_MMV_FUSE`（真優化，不是量具），
  且孿生 `delivery_base` 判 `UNSTABLE`（冷啟 rep）⇒ 同帶，不是「fuse 退步」。
- 27.3 的**速度**沒有被否證（診斷 cost 只會讓它更低）——被否證的是「它可以被**引用**」。
  要讓 27.3 變成可引用，得跑一支**不帶這四個旗標**的同 cell 臂，那是 L20-10／`e-cell-discriminate`
  的 B1／B3 之後的下一個動作（仍需測試卡 §2.5 的新 cell 宣告）。

### 六、追加（09-30 00:15 的判別臂）：旗標洗掉，level 還在 ⇒ R5 不夠，補 R6

如果那三個診斷旗標只是「雜訊」，把旗標拿掉之後 level 就該回來。為了不靠推論，
照 §5.0 的認可入口（`harness bench --charter scripts/check/charters/e-quote-hygiene-k4-2026-09-30.yaml`）
跑了一次：**同一個 build**（`e5d1c0f14`／`build_number 630`）、同一個交付 cell、其餘逐欄不變，
只少了 `CGC_MISS_MASK_DBG`／`_COST`／`CGC_SPAC_DBG`。

| 臂 | 旗標 | t/s | 逐 rep | all ／ kept | 窗口 | 判詞 |
|---|---|---|---|---|---|---|
| `k_sweep_T4_K4`（19:11） | ＋3 個診斷 | 27.338 | [27.38, 27.24, 27.39] | 1.005 ／ 1.005 | `none` | `DIRTY`（R5） |
| `quote_hygiene/k4_noflags`（00:15） | **洗掉** | **26.203** | [26.06, 26.31, 26.24] | **1.010** ／ 1.003 | `swap`（+4344 MiB） | `DIRTY`（窗口） |

⇒ **「維持」分支成立**：level 幾乎不動（−4%，落在旗標成本與窗口差之間），離散甚至更好（1.010）。
這一場的窗口剛好是 swap ⇒ 仍不可引用；但窗口乾不乾淨是**抽籤**（§53 四場全 swap），
下一次抽到 `none` 就會出現一個「R1–R4 全過、26.2 ≥ 20 且 ≥ 25、而**輸出未驗**」的讀數 ⇒ 兩個主節點會一起變綠。

那支臂自己的 stderr 也把機制寫出來了（`k4_noflags.stderr.log`）：

    llama_expert_cache: decode/pool (ensure_slot+batch) hits=5720/5720 (100.0%)  gather (ensure) hits=0/0
    ... prefetch=0/0  file_reads=0  resident=6197.04 MiB  zero_mapped=0

5720 ＝ 143 席位 × 40 層 ⇒ 池是 **prefill 填的**、decode 期 `gather (ensure)` **一次都沒跑**
（正是 §34／`honest-decode-residency` 描述的那個指紋）⇒ 26.2／27.3 量的不是「交付目標的那個函數」。

⇒ 閘門因此補上 **R6 輸出見證**：單次提交臂（`CGC_SEG_BATCH`）跳過 per-layer hook
（`llama-context.cpp:4059`）⇒ **沒有 M1／answer-hash 見證就不得引用它的 t/s**
（計數器端點不受影響，仍走 D7b 的 `counter_quote`／`no_throughput_claim`）。
解除條件寫死在 `UNVERIFIED_OUTPUT_ARMS` 的註解裡——這一條刻意做成**可解除**的：
B 半（正確性）落地、附上輸出見證之後，這個禁令就該消失。

實測：`quote_gate --selftest` **18/18**（＋R6 兩例：洗掉旗標仍擋／誠實臂不擋）；
已宣告的四個 artifact 判詞**全部不變**（`s1_1`=`DIRTY`、`filla_run`=`DIRTY`、
`res_2026-09-29c`=`QUOTABLE`、`anchor8g`=`UNSTABLE`）⇒ D7／D8 不受影響；`decode_board_build --check` PASS。

**這一輪的 net**：交付 cell 有了一列可引用的讀數（9.377），
而兩個「差一點就被引用」的 26–27.3 各自被一條**有出處、可解除**的規則擋住（R5 擋量具、R6 擋輸出未驗）。
兩者都不是「那個數字不真」——是「那個數字不是這一格要的東西」。

## §59 預算閘門：`池子 ＋ Metal 峰值 ＋ 保留 ＞ 上限` ⇒ 這一場的讀數是**足跡**，不是**能力**（2026-09-30）

**問題（R4 的漏洞）**：§55／§58 的 R1–R6 判的是「這一場的三個 rep 是不是同一個量」、
「窗口乾不乾淨」、「量到的還是不是交付臂」。它們都**不問**一件更前面的事：
**這一場在算術上有沒有可能不換頁**。`attribution` 讀的是**窗口內的事件**——
而池子的頁可能在起跑前就已被趕出去（存量在窗口裡不變化 ⇒ R4 看到一個「乾淨」的窗口）。
`k_sweep_T4_K4` 正是這一格：`attribution=none`、逐 rep 1.005，起跑時 swap 存量已 9008.69 MiB、
`min_free` 55.66 MiB ⇒ **它快，是因為該被趕的頁在起跑前就已經不在**。

**可算的那一半**：harness 早就把三項寫進每個產物（`memory_pressure.metal_gate(phase="peak")`）：

    池子 ＝ `--expert-cache` 宣告值 ｜ Metal 駐留 ＝ 整趟峰值 pages_wired ｜ 保留 ＝ CGC_METAL_RESERVE_MB(1024)
    上限 ＝ 引擎 stderr 的 recommendedMaxWorkingSetSize ＝ **11453.25 MiB**
    ⇒ deficit = pool + wired_peak + reserve − ceiling

同一個交付 cell、同一個 build、`contract.ok=true`、batch 512/512 的三場：

| 場 | t/s | Metal 峰值 | deficit | swap 成長 | misses ／ 有效讀取 | 判詞 |
|---|---|---|---|---|---|---|
| `anchor_clean/anchor_delivery` | 9.702 | 10989.83 | **+8753** | **+4830.8** | 4329 ／ 7.0 MiB/s | OVERBUDGET · PAID |
| `quote_hygiene/k4_noflags`（00:15） | 26.203 | 10273.48 | **+8036** | **+4344.4** | 0 ／ — | OVERBUDGET · PAID |
| `spac_sweep/k_sweep_T4_K4` | 27.338 | 9935.86 | **+7699** | −8.0 | 0 ／ 0.0（`file_reads=1704`、`io_bytes=0`） | OVERBUDGET · **UNPRICED** |

harness 自己在 stdout 也印過同一句話（`Backup/spac_sweep_2026-09-29/T4.stdout.log:816`）：
「起跑合格、但整趟會超：池子 8192 ＋ 上一趟峰值 9959 ＋ 保留 1024 ＝ 19175 ＞ 上限 11453 ⇒
**這一輪的池子必然有一部分在 swap 上**」。本節就是把那行字變成可執行的判詞。

**閘門**：`scripts/check/budget_gate.py`（自測 **15/15**）。判準沿用既有常數，只新增兩個（附出處）：

* **B1** `deficit > 0` ⇒ **OVERBUDGET**（必然有一部分池子在 swap）
* **B2** 餘裕 ≤ `SWAP_GROWTH_MB(512)` ⇒ **TIGHT**（放行，但沒有緩衝）
* **B3** 讀不到 池子／峰值駐留／上限 任一項 ⇒ **REFUSE**（不猜；「未武裝」≠「通過」。舊產物沒有
  `metal_gate_*` 時，用 `memory.worst.max_wired_mb` ＋ env／`scalars.BUDGET` 的池子重算，同一口徑）
* **B4** 產物自寫的 `total_mb` 與三項相加不符（>1 MiB）⇒ **REFUSE**（自洽性，同 R0）
* 報告軸：**PAID** ＝ `swap 成長 > 512`，或 `misses > 0` 且有效讀取 ≤ **IO_FLOOR_MIB_S(76)**
  （＝§`docs/IO_AXIS_VERDICT_2026-09-25.md` §2 `pread_cost_probe` 冷讀 **760 MiB/s** 的 10%；
  實測 1.0–17.0 MiB/s 比裝置冷讀低 1–3 個數量級 ⇒ 那些 miss 不是 SSD 服務的）；
  **UNPRICED** ＝ 超預算而**一次代價都沒量到**（T4 那一格：比 PAID 更該盯著看）。
* `--mode strict`（預設）＝「必然 swap 就降級」；`--mode paid` ＝ 只降級代價量到的（給同機 A/B）。
* 程式介面 `blocks_capability(prod, mode)` 給別的閘門直接呼叫，不必重寫一遍。

**全語料第一次登記**（`check --glob 'Backup/**/*.json'`，2629 場；帳本 `Backup/budget_gate_ledger.json`）：

    OVERBUDGET 322（PAID 300、UNPRICED 22）／ REFUSE 2307 ／ IN-BUDGET **0**
    赤字 min ／ median ／ max ＝ 7564 ／ 9997 ／ 10266 MiB
    REFUSE 中 380 場是「預算閘未武裝」——2026-09-24 加 memory 取樣器之前的產物，連峰值都沒有

**代價（要講清楚，這是本節最重的一句）**：閘門把**已認證的 11.703 錨點**
（`mtp_off_clean/res_2026-09-29c.json`）也判 **OVERBUDGET**（wired 12334 ⇒ 超 10097 MiB；
它整趟 4784 次 miss 以 **1.0 MiB/s** 服務 ⇒ 也在 PAID）。也就是說：
**在這台機器上（pool 8192 ＋ 模型側峰值駐留 9.9–12.3 GiB ＋ 上限 11453.25），
「能力數」在 strict 口徑下不可能成立；能成立的是「補頁政策數」。**
⇒ 因此本輪**沒有**把它當 R7 接進 `quote_gate`：那會讓 L20／L25 已宣告的 evidence 當場變 `DIRTY`、
D7 直接 FAIL。要不要接、用哪個 mode 接，是 operator 的決定；工具已經備好（`blocks_capability`）。

**接線**：`scripts/check/board_pipeline.sh` 兩條路徑都先跑 `budget_gate.py --selftest`
（與 `quote_gate --selftest` 並排，引用判準與預算判準同一道門）；重建路徑再產一本帳
`Backup/budget_gate_ledger.json`（`rc=1` 是預期，同 `mindmap_void_check`）。

**下一步的兩條（若經營者要 25+ 進量產）**：
① 讓補頁**真的被藏起來**（而不是靠「起跑前頁就已經不在」——那只是把成本挪到上一次跑）；
② 或把配置縮到真的塞得下（池子 ≤ 上限 − 峰值駐留 − 保留，本機約 **493 MiB**），
再用真負載重測命中率——那個尺寸下的 miss 率才是量產要看的數字。

## §60 交付 cell 的「全 slot 暖身」A/B：publish 成功了，但 compulsory **上升**；而誠實臂在**乾淨窗口**只有 9.63（2026-09-30 01:07）

立項卡 `scripts/check/charters/e-slab-handoff-delivery.yaml`（charter gate PASS）；
產物 `Backup/slab_handoff_delivery_2026-09-30/ab.json`（＋成對 stderr `ab.stderr.log`）。
起跑閘：壓縮機 **quiet**（0.00 MiB/s）、thermal NOMINAL、起跑 free **8894 MiB**、swap 存量 2838 MiB
——這是這一輪難得的乾淨窗口（`harness.py show` 在跑前一分鐘還在 REFUSED）。

| 臂（同一個 launch，同一個 cell） | tg t/s | 逐 rep | spread | compulsory ／ capacity ／ evict | 讀取 (MiB) | io 有效 | wall | attribution |
|---|---|---|---|---|---|---|---|---|
| `prod-new`（對照） | **9.634** ±1.03 | [8.51, 9.87, 10.52] | **1.236** | 3610 ／ 520 ／ 4121 | 4459.2 | 8.0 MiB/s | 69.9 s | **none**（swap −1237） |
| `+CGC_SLAB_HANDOFF=143`（treatment） | **11.175** ±0.31 | [11.16, 11.49, 10.87] | **1.058** | 4297 ／ 806 ／ 5103 | 2734.2 | 5.0 MiB/s | 58.8 s | swap（+2612） |

`CGC-SLAB-HANDOFF: cap=143 warmed=**5720** experts in **5481.3 ms** (before the first decode step)`
—— 5720 ＝ 143 × 40，**整個池的每一席都填滿了**（＝立項卡要的「全 slot 暖身」），成本 5.48 s。

**判定（照跑前寫死的驗收）**：
* 「compulsory ≤ 1000」**不成立**：3610 → **4297（+19%）**。這正是 §`SLAB_POOL_HANDOFF_2026-09-19` 的
  同一個指紋（那一輪 1671 → 2089）：publish 的集合與 decode 真正首次觸及的集合不重疊，
  而它佔滿了席位 ⇒ 反而把 decode 要的那批擠掉（`evict` 4121 → 5103）。
* 「中位增益 ≥3%」成立：**+16.0%**（9.634 → 11.175），wall **−11.1 s**（69.9 → 58.8），
  離散從 1.236 → **1.058**（全語料交付 cell 的**誠實臂**裡唯一 spread ≤ 1.10 的讀數；
  spread 更小的 9 個全部帶 `CGC_SEG_BATCH`）。
* ⇒ 兩條是**不同的事**：移動的是「首次觸及的**成本**」（4.46 → 2.73 GiB 的讀取、且發生在
  被量的窗口之前），而不是「首次觸及的**次數**」。立項卡的成功判準寫成後者 ⇒ 機制**否證**，
  但**成本轉移有效**，值得當成一條獨立機制追（§59 的 PAID 軸）。

**這一輪真正的頭條不是那 +16%**：對照臂是在**乾淨窗口**（free 8894 MiB、壓縮機 0.00 MiB/s、
swap 成長 −1237 MiB、`attribution=none`）跑出來的，它給出 **9.634 ± 1.03**。
⇒ 「交付 cell 的 9.7 是盒子壓力造成的」這個假設**在這格不成立**：誠實臂在我們量過最乾淨的窗口裡
還是 9.6。而 26–27 那一族全部帶著 `CGC_SEG_BATCH`（跳過 per-layer hook、計數器回報常數 5720）。
**兩者的差別在臂身分，不在環境** ⇒ §58 的 R6 是對的，而 §59 對這格的「ambient eviction」措辭要收窄。

**閘門讀數**：兩臂都被 `budget_gate` 判 `OVERBUDGET`（赤字 8589 ／ 8664 MiB，最大項＝模型側），
且都 `PAID`（對照 8.0 MiB/s、treatment 5.0 MiB/s ≤ 地板 76）。`quote_gate` 上兩臂都**不可引用**：
對照＝`UNSTABLE`（spread 1.236）、treatment＝`DIRTY`（`attribution=swap`）⇒ 這一輪**沒有**可引用的讀數。

**未做／留給下一輪（講清楚，不要當成已做）**：
① 這一輪是單次 A/B，**控制臂先跑**（盒子狀態不同）⇒ 要分離「臂」與「次序」，得跑 ABBA
（`ab_interleave.py`）並讓 treatment 先跑一次；
② cap 只試了 143（填滿）；09-19 的 cap 32 不適用於本 cell，而「尾巴 union（只 publish 最後一個
prefill token 的 8 experts/層）」仍未測——依 §09-19 的算術，覆蓋上限約 1/3；
③ publish 的 5.48 s 落在 `prompt eval`／首 token 延遲上（09-19 已量過同一件事），所以它對
**吞吐**的貢獻（−11.1 s wall）與對**延遲**的傷害必須分開報。

## §61 把「量不到交付目標」的臂從子目標選項裡刪掉：看板層的 D10（2026-09-30 01:30）

§60 把話說死了：**20+／25+ 目前沒有一列是在交付目標的函數上、用活著的計數器量到的**。但那只確立了
「這些讀數不可引用」，沒有動到**看板上仍留著那條路**這件事——`options.arms` 是「跑這一格要開什麼」的
唯一來源（`mindmap_subgoal_sync.py` 把它搬到總圖與立項卡，對照報告也從它生成）。留著這種臂，
下一個人照著跑只會再得到一個不可引用的數，而「不可引用」這件事要重跑一輪量測才會發現。
⇒ 這一節把判準**往上搬到選項層**，並刪掉已經被判準擋掉的臂。

### 61.1 判準的唯一定義：quote_gate 的 R5／R6

不在這裡重寫清單。`scripts/check/quote_gate.py` 持有兩張登記表，每一條都附出處：

- **R5 臂身分**（`THROUGHPUT_VOID_INSTRUMENTS`）：原始碼自己寫「DIAGNOSTIC ONLY … **Never quote
  throughput from an arm with this on**」、或登記表明文 never-quote 的量具。逐條附檔名行號。
- **R6 輸出見證**（`UNVERIFIED_OUTPUT_ARMS`）：`CGC_SEG_BATCH=1` 跳過 per-layer hook
  （`llama-context.cpp:4059`），成員維護改走回讀路徑；本臂 stderr 指紋是 `gather (ensure) hits=0/0` ＋
  `prefetch=0/0` ⇒ 池的計數器是**常數**（實測 `cache.requests=5720=143×40`，連 `n_gen=8` 與帶 2048-prompt
  的兩場都一樣），而 G3 未武裝時未駐留的選取走 `e % ns` ⇒ 輸出未驗。

`decode_board_build.py` 新增的 `arm_void_flags()` **只做轉接**（走 quote_gate 的同一條 `tag` 解析路徑），
不複製清單——兩份清單一旦漂移，看板就會跟同一格產物的判詞說不同的話。

### 61.2 掃描結果：7 支臂、5 格（其餘 11 格的臂乾淨）

| 子目標 | 狀態 | 刪掉的臂（去掉 `prod-new:`） | 規則 | 旗標 |
|---|---|---|---|---|
| `L20-1` | 未結案 | `CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1` | R6 | CGC_SEG_BATCH |
| `L20-2` | 結案（PASS／FLIP） | `CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1` | R6 | CGC_SEG_BATCH |
| `L20-2` | 結案（PASS／FLIP） | `CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1;CGC_ZERO_SLOT=1` | R6 | CGC_SEG_BATCH |
| `L20-3` | 未結案（前置否證） | `…;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_MISS_MASK_HIST=1` | R5＋R6 | DBG／COST／HIST／SEG_BATCH |
| `L20-5` | 未結案（P0 成立、P1 未做） | `…;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_SPAC_DBG=1` | R5＋R6 | DBG／COST／SPAC_DBG／SEG_BATCH |
| `L20-5` | 未結案（P0 成立、P1 未做） | `…;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_PREFETCH_SRC=hist;CGC_PREFETCH_WINDOW=4` | R5＋R6 | DBG／COST／SEG_BATCH |
| `L20-7` | 未結案（有前置） | `CGC_RHO_PROBE=1` | R5 | CGC_RHO_PROBE |

**這不是說「那些臂是假的」。** 它們量到的東西仍然有效——L20-2 的計數器翻轉（`zero_slot>0` 且
`placeholder=0`）、單段提交的 step 成本、miss 遮罩的逐層直方圖，都還是機制證據。
被否定的只有一件事：**它們當交付目標（20+／25+）的讀數**。

### 61.3 刪除的紀錄：`options.arms_removed`（欄位比對現算，說謊會紅）

```yaml
options:
  arms: []                       # 沒有可跑的量測臂
  arms_removed:
  - arm: "prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1"
    rules: [R6]
    flags: { CGC_SEG_BATCH: R6 }
    why: "這一格的數只能從單次提交臂出來，而單次提交臂的 t/s 與交付目標不是同一個函數（R6）。"
```

`flags` 不是自由文字：D10 每次都用 quote_gate 的登記表**現算**一次並逐鍵比對，
`rules` 也必須等於 `flags` 的值集合。⇒ 「刪了什麼、為什麼」不能只寫在散文裡。

### 61.4 閘門：D10（`decode_board_build.py`）

| 檢查 | 結果 |
|---|---|
| `options.arms` 裡的臂帶 R5／R6 量具 | **error**（要嘛刪掉、要嘛搬進 `arms_removed`） |
| `arms_removed` 少 `arm`／`rules`／`why`，或規則不合法（不是 R5／R6） | error |
| `arms_removed.flags` 與登記表現算不符 | error（**刪除紀錄說謊**） |
| 收了**沒有** R5／R6 量具的臂 | error（這一欄只能放判準擋掉的臂） |
| 同一支臂同時列在 `arms` 與 `arms_removed` | error |
| 重複的 `arms_removed` 條目 | error |

自測 24/24（新增 7 例：現況不擋、單次提交臂、診斷量具臂、合法刪除、說謊的旗標、兩邊都列、誤刪乾淨臂）。
反向實測（用 YAML 副本、不動看板本體）：把 L20-1 的 `arms: []` 塞回一支 `CGC_SEG_BATCH=1` ⇒

```
  ✗ D10 L20-1 的 options.arms 留著不能量交付目標的臂（CGC_SEG_BATCH=R6）：prod-new:CGC_SEG_BATCH=1
VERDICT: FAIL（4 個問題）
```

### 61.5 看到的數字（不是隱藏在某一格的 options 裡）

- 看板頁首（由 YAML 現算，不是手寫 summary）：**臂的身分（D10）**：可跑 **12** 支／已依 R5／R6 刪除 **7** 支；
  其中 **4 格（L20-1、L20-2、L20-3、L20-5）已沒有任何可跑的臂** ⇒ 目前沒有任何一條已知的路能量到它的交付數字。
- 逐子目標頁新增「臂（options）」節：可跑的臂逐條列出；被刪的臂附規則與理由。
- build 的 console 同一組數；`decode_board_build --check` PASS、`--selftest` 24/24、
  `board_pipeline.sh --check` rc=0（quote_gate 18/18、budget_gate 15/15）。

### 61.6 這一節**沒有**動的東西（以及為什麼）

- **5 張立項卡的 `arms:`**：`mindmap_subgoal_sync.py --apply` 的 `apply_charters()` 設計上是「只補不刪」
  （卡的 `arms:` 是**跑過的歷史**）；看板不再提供這些臂 ⇒ 之後 `--apply` 也不會把它們加回去。
  卡片的那份清單不是選項，選項的唯一來源是看板。要真正同步卡片內容得跑
  `python3 scripts/check/mindmap_subgoal_sync.py --apply`（它會整塊換掉本工具寫的 `options:` 區塊，
  且會寫 `docs/mindmap/mindmap.json`）——本輪沒跑，因為那兩個檔案同時有另一條執行緒的未提交異動。
- **對照報告** `SUBGOAL_OPTION_MAP_2026-09-29.{md,html}`：`--report` 重新生成後與舊檔 **byte-identical**
  （它的欄位是節點綁定與 subject knobs，不含 `arms`）⇒ 沒有漂移。
- **看板的 `summary`／各格 `note:`**：沒有改任何一句話。判詞變了要改的句子，改的人要看得見自己在改什麼。

### 61.7 對 20+／25+ 的意義

剩下 **11 格**（含整個 L25）的臂是乾淨的——它們的問題不是臂，是**還沒有 20+／25+ 的讀數**。
所以這一節不會讓任何一格變綠，它只是把「已知走不通的路」從選項裡拿掉：
今天之後，「照著看板跑」不可能再產出一個假 25。

## §62 假 25 的回歸測試：全語料 34 個 decode ≥20 都必須**過不了閘門**（2026-09-30 01:40）

§55–§61 的結論是：**20+／25+ 目前沒有一列是在交付目標的函數上、用活著的計數器量到的。**
那個結論靠的是**一次掃描**，而掃描會過期：新產物、新臂、新旗標都會生出新的 ≥20，
而「不可引用」這件事本來要重跑一輪量測才會被發現。⇒ 把它變成**可重算的斷言**：
`scripts/check/fake25_regression.py` ＋ 凍結的 fixture
`scripts/check/fixtures/fake25_corpus_2026-09-30.json`（34 個讀數，每個連它那一場的 `prod` 一起凍結；
判準改了會跟著**重判**，不是快照）。

### 62.1 判準：三軸並排，`admissible` 要兩條都過

    ① quote_gate    R1–R3 逐 rep 離散／樣本數、R4 窗口、R5 臂身分、R6 輸出見證
    ② budget_gate   `池子 ＋ Metal 峰值駐留 ＋ 保留 ＞ 上限` ⇒ 這一場的 t/s 是**足跡**，不是**能力**（§59）
    ③ 臂身分        已折進 ①，但**獨立再印一次**（見 62.4 的更正）

    admissible（可以當交付讀數）＝ ① 判 `QUOTABLE` **且** ② 不 block

判準不重寫：本檔 import 兩個閘門的同名函式（`quote_gate.judge`／`arm_instruments`／
`unverified_output_arms`、`budget_gate.blocks_capability`）。旗標表改了、閘門改了，這條測試跟著改。

門檻 `≥20 t/s` 且**只收 decode row**（`n_gen>0`）。prefill row（`n_gen=0`，pp ≈ 374 t/s）不算——
它不是交付目標，只是同一張表上的另一列。

### 62.2 凍結的語料（2026-09-30 掃描）

    檔案 2558／產物 3103／row 1031；decode row 707
    decode ≥ 20 t/s：34 個（其中 ≥ 25 t/s：21 個）
    身上帶 `CGC_SEG_BATCH`：33／34（≥25 的 21 個：21／21）
    沒有臂旗標的那一個：`Backup/stepbudget_2026-09-29/e1e2_clean.json` 22.048（全 rep max/min 1.177）

### 62.3 今天的實測（重播 ＋ 現地一致）

    quote 判詞：DIRTY 31 ／ THIN 2 ／ UNSTABLE 1 ／ **QUOTABLE 0**
    budget 判詞：**OVERBUDGET 34／34**
    admissible：**0／34**（allowlist 放行 0）
    現地：與 fixture 相同 34 列；新出現 0 列

**而且把記憶體那一軸整個關掉（假設無限 unified memory）仍然 0／34**：

```
$ python3 scripts/check/fake25_regression.py --ignore-budget
  讀數 34 個：admissible 0 個
```

這一條是本節最重要的新事實：**「換一台 unified memory 更大的機器就有 25」不成立**。
34 個讀數裡有 33 個身上掛著「跳過 per-layer hook 的單次提交臂」或明文 never-quote 的量具
——那不是記憶體能解決的事。（`--ignore-budget` 只是**報告模式**：admissible 仍要求預算軸的真判詞。）

### 62.4 測試自己抓到的一個更正（臂身分必須獨立算）

第一版把臂身分取自 `quote_gate.judge()` 回傳的 `metrics`——但 `judge` 在 `THIN`（reps<3）會**提早回傳**，
於是 `Backup/seg_batch_abba/res_B2.json`（27.867、`reps=1`）被記成「沒有臂旗標」。
改成直接呼叫 `arm_instruments()`／`unverified_output_arms()`（不看 verdict）之後，
33／34 與 21／21 才是對的數字。⇒ **判詞的短路不是判準的短路**：要問「身上有什麼」，就別透過 verdict 問。

### 62.5 斷言（含反空洞，這是它能不能當測試的關鍵）

| | 斷言 |
|---|---|
| A1 | 每一個讀數都**不 admissible**（除非列在 `ALLOWLIST`） |
| A2 | 每一個被擋的讀數都要有**指名**的理由（不能「被擋了但不知道為什麼」） |
| A3 | **反空洞**：合成的「乾淨 ≥25」（spread 1.008、`attribution=none`、無 R5／R6 旗標、預算內）必須**過** ⇒ 證明這條測試不是靠閘門一律說不而成立 |
| A4 | 反空洞第二半：同一個乾淨讀數加上 `CGC_SEG_BATCH=1`（＝真實 21 個 ≥25 的形狀）⇒ 必須被 **R6** 擋 |

自測 11/11（含 R0 內部自洽：樣本很亂卻回報很穩 ⇒ `REFUSE`；R2 離散 ⇒ `UNSTABLE`；
B1 必然 swap ⇒ `OVERBUDGET`；ALLOWLIST 放行）。

### 62.6 刻意不自動放行（`ALLOWLIST` 的用意）

真的量到第一個誠實的 20+／25+ 時，這條測試會**紅**，並直接說要怎麼做
（把產物路徑與證據加進 `ALLOWLIST`，再 `--build-ledger`）。這是刻意的：**自動放行等於沒有這條測試**，
而「這一列給的是這台機器能跑多快，還是補頁政策藏了多少」這個問題不能由程式代答。

### 62.7 接進產生鏈

`board_pipeline.sh` 現在驗四條閘門（判準各自只有一份定義）：

    quote_gate（可不可引用）｜budget_gate（是能力還是足跡）｜fake25_regression（假 25 的回歸測試）
    ｜decode_board_build（看板 D1–D10）

- `--check` 路徑：`fake25_regression --selftest` ＋ `fake25_regression --check`（fixture 重播 ＋ 全語料現地掃描，
  約 1 秒）。**現地掃到 admissible 就會讓 pipeline 紅**（rc=1）——那時要決定的是「這是真 25 還是假 25」。
- 重建路徑：另存 `Backup/fake25_regression.json`（帳），並用 `|| true` 不擋頁面重建（與預算帳本同一慣例）。

### 62.8 這條測試**不**主張的事

- 它不說「那些臂是假的」。它說的是：**它們的 t/s 不是交付目標的那個函數**（機制證據仍然有效）。
- 它不說「永遠不會有 25」。它說的是：**今天這 34 個沒有一個站得住**，而新的必須是刻意的動作。
- 它不取代 §59 的預算閘門，也不取代 §55 的引用閘門：它是**把兩者一起套在「所有 ≥20 的歷史讀數」上**的那一層。

### 62.9 順手補的：`--apply` 之後，卡片上要看得見「為什麼沒有臂」

§61.6 說「5 張立項卡的 `arms:` 是跑過的歷史，`apply_charters()` 只補不刪」。但那是**另一半**的問題：
卡片的 `options:` 區塊是由看板同步過去的，而它原本只搬 `arms` ⇒ 下次任何人跑
`mindmap_subgoal_sync.py --apply`，卡片上會出現一個**空的 `arms:`**（YAML 讀出來是 `None`），
沒有任何理由——讀卡的人分不出「還沒指定」與「全部被判準刪掉」。

⇒ 兩處小改：

- `charter_options_block()`：空的臂寫成 **`arms: []`**（不是沒有內容的 `arms:`），
  並在後面多一塊 `arms_removed`（逐條 `arm`／`rules`／`flags`／`why`，出處指向看板同一格）。
  「這一格沒有可跑的臂」與「還沒有人指定」從此長得不一樣。
- 同步工具的 `--check` 仍然 0 缺口、`--selftest` OK；本輪仍然**沒有**執行 `--apply`
  （它會寫 `docs/mindmap/mindmap.json`，而那個檔同時有另一條執行緒的未提交異動）。

## §63 L25 收尾：三格結案（窗口／端點／排除）、兩個儀器錯、一個「早就跑過但沒被讀」的判決（2026-09-30 02:2x）

指令是「關掉 WorkBuddy 與 Chrome，把 L25 能跑的全跑完，不是結案就是升級到已認證」。
WorkBuddy／Chrome 退出後機器回到安靜狀態（可回收 **10.6 GB**、壓縮機 0.00 MiB/s、熱 NOMINAL、
埠空、無殘留 llama），於是這一輪全部在同一個窗口裡跑；**沒有一趟是重跑舊結論**。

### 63.1 跑了什麼

| 趟 | 時間 | cell | 產物 | 結果 |
|---|---|---|---|---|
| L25-3 錨點 ① | 02:15 | delivery（p0/n128/d512/r3/ws64） | `Backup/l253_anchor_20260930/anchor_delivery.json` | 8.933 [3.81, 11.41, 11.57] ⇒ **UNSTABLE**（第 1 rep 冷，spread 3.036） |
| L25-3 錨點 ② | 02:16 | 同上 | `…_2.json` | 8.486 [10.16, 3.95, 11.35] ⇒ **UNSTABLE**（第 2 rep 塌，2.877） |
| L25-3 錨點 ③ | 02:17 | 同上 | `…_3.json` | **10.923 [10.34, 11.34, 11.08] ⇒ QUOTABLE**（all 1.097、kept 1.023、cold-rep +2.7%） |
| L25-5 k=1 | 02:14 | (default)（p2048/n128/d512/r3/ws64、pool 8 GiB、MTP k=1） | `Backup/l255_close_20260930_cli/run_cli_only.json` | `attribution=none`、tg 9.991、hit 91.6% ⇒ 五閘全過，**m ＝ 0.159** |

三場錨點都是同形（交付 cell、`prod-new`、`harness bench`、同一支 CLI），所以三個 rep 向量可以直接比：
**窗口是可重現的，但一場只有約 1/3 的機率不塌**——塌掉的那一個 rep 掉到 3.8–3.9 t/s，讓 `platform_ts`
偏 −9.8% 到 +28.7%。這不是新機制，是 §54 那個指紋（`[10.45, 10.44, 3.88]`）換了一批樣本再現。

### 63.2 L25-3 ⇒ 結案：交付 cell 的第一個**可引用**讀數

`10.923 t/s`（＝91.55 ms/step）逐 rep **1.097**、kept 1.023、窗口 none、臂上無 R5／R6 ⇒
`quote_gate` 判 **QUOTABLE**。這是**全語料第一列可引用的交付 cell 讀數**（此前唯一的 QUOTABLE 是
`(default)` cell 的 11.703，兩者依 §56 **不可互比**）。它取代 §58 的 9.377 成為交付 cell 的基準，
也升為 **C4**（新增認證格，與 C1–C3 同級）。

同時把 L25-3 的**驗收句**改掉：原寫「重現 36.33 ms／**27.53 t/s**」。那個 27.53 的出處是
`CGC_SEG_BATCH` 單次提交臂＋`(default)` cell（§61 的 R6＋cell 契約雙重不合格）；拿它當驗收，
本格**永遠不可能結案**。新驗收＝「在 `attribution=none` 的窗口跑出可引用的交付 cell 錨點（≥10.9）」。
⚠ 這不是把標準放寬：原句驗收的是一個**不可引用**的數字，那不是更嚴，是**不可判定**。

### 63.3 L25-5 ⇒ 結案：拆 m 的儀器有兩個錯，而且錯了兩天沒人發現

`m` 是 L25-5 宣告的端點（每輪 draft 成本 ÷ 一個 plain step，門檻 0.30）。今晚要用它結案時，
`scripts/check/mtp_round_split.py`（**untracked**，另一條線剛寫的）跑出：

* **閘 (a) 對單深度 cell 結構上不可能過**——它寫死 `len(tg) >= 2`，那是矩陣 cell 的形狀；而交付／預設
  cell 宣告 `depths: "512"` ⇒ 只有 1 個 tg row。**L25-5 要的正是單深度那一趟** ⇒ 工具永遠交不出 m。
* **欄位錯位**：`parse_perf` 取 `group(8)`＝`t_accept_ms`（恆為 0.1–0.2），下面卻把它當 `t_draft_ms`
  差分 ⇒ `T_draft` 恆 **0.00 ms**、`m` 恆 **0.000**。連帶把 `E`／`k_eff`／`acc_rate` 三個標籤也印錯一格。
  為什麼沒人發現：**沒有任何閘門消費這支工具**，而文件裡的 m 是人工從 raw 欄位算的（§9 的 0.098–0.110）。

修法與驗證：欄位按名字取（`t_draft`＝group 7）、閘 (a) 改成「**這一場宣告了幾個 depth 就該有幾個 tg row**」
（cell 沒宣告就保留舊行為 ≥2，以免放寬既有矩陣產物）、新增 `judge()` 供閘門當場重跑、新增 `--selftest`
**14/14**，其中最重要的一條是把 **09-24 那一趟的真實 PERF 行**餵進去、斷言 **m ≈ 0.099**
（＝文件早就記下來的值）。錯位那版會得到 0.000 ⇒ 這條測試釘住的是**儀器的正確性**，不是我的數字。

修好後今晚的乾淨窗口：`T_draft` 差分 12.96／12.29／10.83／14.26／13.70 ms/輪 ⇒ 穩態 **13.70** ⇒
**m ＝ 0.159 ≤ 0.30**。對照 09-24 舊 build（同 cell、pool 3 GiB、0.0.578）：**m ≈ 0.099** ⇒
**prod-new 的 draft 成本高 60%**。卡片自己推的 k→∞ 上界 24.59 是用 **m ≤ 0.30** 推的 ⇒ 實測更小 ⇒
上界仍成立（且更緊）。單邊性照舊：劣化窗只會把 `t_draft` 吹大 ⇒ 0.159 是**上界**。

**端點種類**：`m` 不是時間讀數（引用閘門判不了），也不是二值翻轉（D7b 的既有判詞），而是一條**門檻**。
於是 D7b 補了第二種端點：`COUNTER_METRICS` 加 `m_ratio`、`COUNTER_CLOSE_VERDICTS` 讓每種 metric 各自
指名結案判詞（`zero_slot_flip → FLIP`、`m_ratio → WITHIN`），`counter_verdict` 對 `tool: mtp_round_split`
**當場重跑** `judge()`，並要求五閘全過 ＋ `attribution=none`（宣告 `require_attrib`）＋ cell 相符 ＋
`m ≤ limit`；任一不符 ⇒ `REFUSE`，產物不在 ⇒ 判不了（fail-closed）。看板自測新增 8 例（含「門檻 0.10
而 m=0.16 ⇒ REFUSE」「宣告 FLIP 但該 metric 要 WITHIN」），總計 **34/34**。

### 63.4 L25-6 ⇒ 結案（排除）：那個判決早就在樹上，只是看板還在讀階段 1 的樣本

看板的 L25-6 一直寫「16.4% 未認證（配對 n=5、t=2.08 < 2.776）」。要跑「加大 n」時先去看產物，
發現**事先登記的 N=12 判決 09-28 18:12 就跑完了**，而且被保存下來：

```
Backup/k3_pair_cert_2026-09-28/verdict_fcrit_2.22.txt
  n=12 pairs   mean diff +0.54 t/s (+5.5%)   sd 1.96   SE 0.57
  t=0.95   crit=2.20 (df=11, alpha=0.05)     VERDICT: NOT SEPARATED
  k3 slower in 7/12 pairs
  k2 launch-level 8.61% (F=1.58 < 2.22) ; k3 0.00% (F=0.87)  ⇒ ABBA 消不掉 k2 那一項
```

立項卡在跑第 5 對之前就把 N 寫死成 12（`min(12, max(8, n_needed_at_point_sd))`），並寫死
「跑滿 N 對而 |t| < crit ⇒ NOT SEPARATED。**不再加對**（optional stopping）」⇒ **16.4% 的關卡是「關掉」**，
不是「再跑」。所以 L25-6 以**排除**結案（`cert_by: 事先登記的判決`），端點是**配對 t**、不是 t/s。

⇒ 教訓（值得記住）：**`runnable: ✅ 現在能跑` 之前應該先掃一次「這一格要的判決是不是已經存在」**。
這一格差一點就要用 ~40 趟 launch 去重跑一個兩個晚上前就有的答案。`k3_pair_cert.sh` 的產物同時落在
`/tmp/kb`（會被清）與 `Backup/k3_pair_cert_2026-09-28/`（不會）——**只有後者算留存**。

### 63.5 看板與閘門的改動（本節全部已進 `--check`）

* **D7b 的第二種端點**：見 63.3。裁決詞種：`zero_slot_flip → FLIP`、`m_ratio → WITHIN`。
* **`counter_quote` 現在會渲染**：以前它只被驗證、不顯示（L20-2 的端點判詞在頁面上**看不到**）。
  現在逐子目標頁多一區「端點判詞」。
* **C4 認證格**：交付 cell 第一列可引用讀數 10.923（`quote` 由閘門當場重判）。
* **摘要帳目**：交付 cell 可引用數 **1 / 30 → 2 / 30**；子目標結案數 **→ 6 / 16**
  （L20-2／L20-3／L20-6／L25-3／L25-5／L25-6（排除））。
* **兩層主節點證據註記**：補上「交付 cell 的可引用候選是 10.923（不是 9.377）」，並重申
  `(default)` 的 11.703 與它**不可互比**。綠燈仍然 **0 個**（目標 20／25 都遠高於這兩個讀數）。

### 63.6 閘門讀數與誠實的保留

* `decode_board_build.py --check` **PASS**、`--selftest` **34/34**；`mtp_round_split.py --selftest` **14/14**；
  `quote_gate --selftest` **18/18**、`budget_gate --selftest` **15/15**；`fake25_regression` 全語料
  **34 列／admissible 0／無新列**；`board_pipeline.sh --check` **PASS**。
* **`budget_gate` 對三場錨點全部判 OVERBUDGET**（赤字 8317／8336／8439 MiB，最大項＝模型側 Metal 駐留）
  ⇒ 10.923 **可引用，但不是能力數**。這一輪沒有改變 §59 的結論：這台機器上**任何** t/s 都是足跡類。
* **單次 A/B 的保留**：三場錨點是**同一形態連跑**，不是 ABBA ⇒「1/3 不塌」這個比例本身還只是三場的觀察。
* **L25 剩下的三格全部卡在機制／決策**（不是卡在窗口）：L25-1（唯一有算術的模型側槓桿被 operator
  裁定不做量化後，沒有候選機制）、L25-2（唯一被指名的機制已被量死，缺的是**新機制**）、
  L25-4（**產品決策**：是否改交付口徑）。⇒ **L25 這一層今晚能結的都結了**：六格裡三格結案、
  三格明確卡在 L25 之外。
* 未 commit。`mtp_round_split.py` 在樹上是 **untracked**（另一條線剛寫的）；`/tmp/kb` 已被清空，
  L25-6 的證據只在 `Backup/k3_pair_cert_2026-09-28/`。

### 63.7 追加：D11（`state` ↔ `settled` 必須一致）

把 L25-3／L25-5／L25-6 寫成「結案」之後，看板上三列**沒有變綠底**——因為綠底是資料旗標
`settled`（另一條線 09-30 立的），而我沒有一起寫。這正是「判定」與「顯示」分頭演化會長出來的東西，
所以補一條閘門 **D11**：

* `state` 以「結案」開頭卻沒有 `settled` ⇒ error（結案但不會綠底）；
* `settled: true` 而 `state` 仍以「未結案」開頭 ⇒ error（綠底但紀錄說還在跑）。

三格補上 `settled: true` 後：**看板 6 列綠底**（L20-2／L20-3／L20-6／L25-3／L25-5／L25-6）、
**主節點綠燈仍是 0 個**（20／25 未達標就不變色）。看板自測 **37/37**（D11 三例：缺旗標、反向、
一致）。原「settled ⇒ 整列綠底」那條既有案例也順手改成「兩邊一致」的形狀，否則它自己就會被 D11 擋下
——**新規則要能通過自己的既有測試**，這件事不該靠人記得。

---

## §64 前置閘門 runnable_gate：「這一格要的判決是不是已經存在」變成流程（2026-09-30）

### 64.1 為什麼（L25-6 差一點白跑 ~40 趟）

L25-6 的看板一直寫「✅ 現在能跑 — 前置＝加大配對 n」。它要的那個判決（事先登記的 N=12 配對檢定）**兩個晚上前就跑完並留在樹上**：`Backup/k3_pair_cert_2026-09-28/verdict_fcrit_2.22.txt` ⇒ 配對差 **+0.54 t/s、t=0.95 < crit 2.20 ⇒ NOT SEPARATED**，立項卡自己寫死「不再加對」。也就是說那一格的答案是「**關掉**」，而板子還在邀請人重跑它。

§63 是靠「動手前先掃一次產物」偶然閃掉的。那不是流程，運氣才是。**這一節把它變成閘門。**

### 64.2 判準（`scripts/check/runnable_gate.py`，單一定義）

每一格宣告一個 `precondition`，語意由 `rerun` 三選一決定，**機器判**：

| rerun | 語意 | 閘門要求 |
|---|---|---|
| `needed` | 這一趟會產出**新資訊** | 必須**真掃描**且 `expect=verdict_absent` ⇒ 掃到判決就紅（證明這一趟不值得跑） |
| `duplicate` | 這一趟只會**重印**已存在的判決 | 必須指名 `artifact`，而且它必須被掃到 ⇒ 掃不到就紅（指名錯檔） |
| `blocked` | 跑不了的原因**不是判決**（缺機制／決策／窗口／程式改動） | 可以 `kind=none`，理由寫在 `why` |

判詞 `EXISTS`／`ABSENT`／`CANNOT_JUDGE`，掃不了＝`CANNOT_JUDGE`（**fail-closed**：板子會紅，不會靜靜放行）。掃描器兩種：`verdict_file`（glob ＋ 可選內容正規式）、`quotable_reading`（依 cell 篩 decode 列再用 `quote_gate` **當場**判可不可引用 —— 判準不重寫）。自測 **20/20**，含反空洞（乾淨的 ≥25 必須過）與 fail-closed（宣告 `verdict_exists` 但現判 ABSENT ⇒ 紅）。

看板端是 **D12**（`decode_board_build.py`）：16 格逐格重跑前置，宣告與現判不符 ⇒ error。D12 一上線就抓到一條真的。

### 64.3 D12 抓到的第一條：**L20-8 過期了兩個晚上**

L20-8 的資料列寫「**未跑**／`現在能跑`」，而事實是 **09-28 23:06／23:11 兩筆獨立 run 已經跑完，判決也寫成分析文件**（`docs/PREMISE_B_CHURN_DELIVERY_2026-09-28.md`）：交付 cell、`ntok=1`、MTP off 下，**publish 級 churn 19.8–20.2%、entry 級 3.2–3.4%**（cell 口徑 15 項嚴格維度一致、`clamped=0`、`zero_mapped=0`、385 graphs × 40 layers 逐格 `SLOT-SEL` 15400 筆 `wrong=0`）。

⇒ 依它自己的否證句（「churn 仍大 ⇒ S1 的前提維持不成立」）更正為 **結案（排除）・churn≠0**：**「逐步之間那張表是常數」在今天的交付口徑上也不成立**。⚠ 這一格**沒有**可引用的 t/s（兩筆都 `attribution=swap`，文件自己寫「都不得引用」）⇒ 走**排除**路徑，不是達標。L20-8 因此成為第 **7** 個結案格（L20-2、L20-3、L20-6、L20-8、L25-3、L25-5、L25-6）。

### 64.4 今天這 16 格的誠實帳目（`runnable_gate` 現判）

```
needed 1／duplicate 7／blocked 8（共 16 格）
```

* **可以真的產出新資訊的只有一格：L20-9**（天花板軸；目前樹上沒有任何 `Backup/ceiling_*/**` 產物寫過它要的判決）。
* **7 格是 duplicate**：L20-2（`s1_g3_ab_2026-09-29/A.json`）、L20-3（`MISSHIST_REVIVAL_GATE_2026-09-29.md`）、L20-6（設計文件 §）、L20-8（`PREMISE_B_CHURN_DELIVERY_2026-09-28.md`）、L25-3（`anchor_delivery_3.json`）、L25-5（`l255_close_20260930_cli/run_cli_only.json`）、L25-6（`k3_pair_cert_2026-09-28/verdict_fcrit_2.22.txt`）⇒ **再跑只會重印**。
* **8 格 blocked**（缺機制／決策／窗口／程式改動）：L20-1、L20-4、L20-5、L20-7、L20-10、L25-1、L25-2、L25-4。

**這張表把「還能做什麼」第一次變成可核對的清單**：不是「16 條路」，而是「1 條會產生新資訊 ＋ 7 條已在樹上 ＋ 8 條卡在人／機制」。

而且它**印在看板上**（`precondition_census()` 的頁首那一行，由 YAML 現算）：`可產出新資訊 1 格 ／ 只是重印已存在判決 7 格 ／ 卡在人與機制 8 格（共 16 格）`；逐子目標頁則有 `rerun`／`kind`／**現判**／命中檔／宣告理由一整列。儀表本身也有測試（看板自測 +5 ⇒ 42/42 中的 5 條：needed×2、duplicate×2、blocked×1）。

### 64.5 順帶更正一個**身分誤讀**：C4 是 **MTP off**

§63 把 `10.923 t/s`（`Backup/l253_anchor_20260930/anchor_delivery_3.json`）升為 **C4**。有人會把它讀成「MTP on 的成績」——**不是**。該產物寫著 `spec_type=null`、`spec_draft_n_max=null` ⇒ **MTP off**（與 C3 同一族）。

MTP-on 是**另一條軸**（L25-4），而且它的算術自己封死：k=3 ⇒ 19.25、**k→∞ 的上界 24.59 < 25**。⇒ 就算把 MTP-on 那一族洗成可引用，也到不了 25。已把這個身分補進 C4 與 L25-3 的資料列（看板頁面直接可見）。

### 64.6 接線與驗證

`board_pipeline.sh` 現在驗**五條**閘門（quote_gate／budget_gate／fake25_regression／**runnable_gate**／decode_board_build D1–D12）：

```
quote_gate 18/18 · budget_gate 15/15 · fake25_regression 11/11（全語料 0 admissible）
runnable_gate 20/20 · 逐格前置 PASS（0 問題）· decode_board_build 42/42、--check PASS
board_pipeline.sh --check → rc=0
```

綠燈仍 **0**（L20／L25 兩層主節點不變色）；C4 是**認證基準，不是目標達成**。

---

## §65 L20-9 結案（排除）＋「假的 needed」：前置閘門的第一個弱點（2026-09-30）

### 65.1 指令：跑掉 16 格裡唯一被判 `needed` 的那一格

§64 的閘門把「還能做什麼」變成清單，清單上只有一格是 `needed`（這一趟會產出新資訊）：**L20-9**，C 軸、題目是「BEST_SHAPE 那『一處 switch case』到底動到什麼、邊界多大」。這一節是它的答案——**而答案是不用跑**。

### 65.2 先講閘門自己：那是一個**假的 needed**

L20-9 的宣告掃的是 `Backup/ceiling_*/**`。**樹上沒有這個目錄**，所以閘門照規則判 `ABSENT ⇒ needed`。但那一格要的判決其實**早就寫成文件了**：`docs/DENSE_NSG_RESCAN_2026-09-23.md`（09-23 02:2x，兩趟、約 700 次探針呼叫）。

⇒ 單一根、而且是**手寫猜的**目錄，就會生出一個假的 `needed`。閘門只能和它的宣告一樣好。§65.5 把這一點收緊成機械規則。

### 65.3 L20-9 的兩個子目標，答案都在樹上

**`sg-shape-id`（指名那處 switch case）—— 指得出來，而且早就接好了。**
`ggml-metal-device.cpp:1007-1015`：plain `mul_mv` 的 `case GGML_TYPE_IQ4_XS` 走 `ggml_metal_nsg_env(GGML_TYPE_IQ4_XS)`；helper 在 `:834/:841`（`IQ4_XS/Q6_K/Q8_0` 三個 case）。已落地為 **P1-3d**（dense）與 **P1-3e**（`mul_mv_id` 的 IQ3_S），且**現行 binary 已含**。
⇐ `BEST_SHAPE_IQ3XXS_M4_2026-09-22.md` §3 說「唯一缺的一環是那處 switch」——那句話**當晚就被自己的實作取代了**（該檔 mtime 09-22 20:10，而分析是 17:5x 寫的）。**C 軸「唯一還活著的方向」的那個缺口，缺口本身已經補上了。**

**`sg-shape-bound`（邊界）—— 量完了，買不到東西。**
09-23 的 dense NSG 重掃（`docs/DENSE_NSG_RESCAN_2026-09-23.md`；產物 `Backup/phase_decomp/L3/shape_probe/nsg_dense_*.json`）：

| 項目 | 實測 |
|---|---|
| 射程 | 逐臂以**編譯出的 pipeline 名**驗（`kernel_mul_mv_iq4_xs_f32_nsg=16…`），不是看 env ⇒ 這一輪才**有效**（前一輪量的其實是 plumbing） |
| 唯一可讀通道 `lm_head`（398 MiB/step，dense 最大單項，過去從未被定價） | 峰值 **84–87%**；`nsg`∈{2,4,8,16,32} 六臂中位數跨度 **2.1 點（2.5%）** ＜ unset 臂自己的 10.1% 跨度；最佳最小（nsg=32，＋5.4%）＝ unset 底噪自己的 ±5.4% ⇒ **不可分辨** |
| 兩個大 `iq4_xs` 形狀 | 底噪 ±15.9%／±73.7% ⇒ **拒絕讀數**（是「讀不到」，不是「沒有槓桿」） |
| `f32` router（84 MiB/step） | 這族 kernel 裡**根本沒有 runtime tile 維度** ⇒ 這個旋鈕原則上碰不到 |
| **整族上限** | **3.09 ms/step ＝ 2.16%** of a 143 ms step（實測 13.39 ms vs 峰值模型 10.30 ms） |

而 25 t/s 要砍掉約 **30%** 的步。**就算整族歸零也到不了** ⇒ 落在本格自己的否證句上（「量出來在噪音內 ⇒ C 軸維持純背景」）⇒ **結案（排除）**，成為第 **8** 個結案格。

⚠ 這一格**沒有**可引用的 t/s（端點是上界，不是吞吐），也沒有走達標路徑 —— 排除就是排除。

### 65.4 這張看板**現在沒有任何一格**能靠跑一趟產出新資訊

```
needed 0／duplicate 8／blocked 8（共 16 格）
```

* **`needed` 歸零。** L20-9 收掉之後，**沒有任何一格的「跑」會給出新的判決**。
* **8 格是 `duplicate`**（判決已在樹上 ⇒ 再跑只會重印）：L20-2、L20-3、L20-6、L20-8、L20-9、L25-3、L25-5、L25-6 —— 這 8 格同時就是 8 個結案格。
* **8 格是 `blocked`**（跑不了的原因**不是判決**，是缺機制／缺決策／缺窗口／缺程式改動）：L20-1、L20-4、L20-5、L20-7、L20-10、L25-1、L25-2、L25-4。

⇒ 對「接下來要跑什麼」這個問題，看板現在的誠實回答是：**沒有。** 要往前只有兩條路 —— **改機械**（`blocked` 那 8 格裡的機制／程式改動）或**改決策**（L25-4 的產品口徑、L20-10 的格子宣告）。這比「16 條看起來都能跑的路」精確得多。

### 65.5 收緊：`needed` 現在必須掃**兩個獨立根**，其中一個必須是 `docs/`

`runnable_gate.py` 的兩個改動（`--selftest` **23/23**，原 20/20）：

1. **`precondition.spec` 可以是清單** ⇒ 逐一掃再合併：任何一個 `EXISTS` ⇒ `EXISTS`；全部 `ABSENT` ⇒ `ABSENT`；有 `CANNOT_JUDGE` 且沒有 `EXISTS` ⇒ `CANNOT_JUDGE`（fail-closed）。
2. **`rerun: needed` 必須用 ≥2 個獨立根，且至少一個 glob 必須以 `docs/` 開頭** ⇒ 手寫單一根猜錯目錄的假 `needed` 從此是紅燈。理由直接寫進錯誤訊息：**判決常常被寫成文件**（L20-9 的判決就在 `docs/`，而宣告只掃了 `Backup/`）。

L20-9 因此改成 `duplicate`，指名的判決是 `docs/DENSE_NSG_RESCAN_2026-09-23.md`（第二根掃 `Backup/phase_decomp/L3/shape_probe/nsg_dense_*.json` 的產物）。

### 65.6 誠實欄

* **閘門的第一個 `needed` 是它自己的假警報**，不是判準錯 —— 判準（「真掃描且掃不到」）照寫對了，**是宣告把搜尋範圍寫窄了**。新的兩根＋`docs/` 規則縮掉了這個洞，但**沒有關掉它**：一個 `docs/*.md` 的 glob 若剛好沒命中一份名字不同的文件，仍會生出假的 `needed`。真正的防線是「下一格宣告 `needed` 之前，先把 `docs/` 用主題字串搜一遍」——這條現在寫在錯誤訊息裡。
* **L20-9 的「無槓桿」只在一個通道上成立**（`lm_head`）。兩個大 `iq4_xs` 形狀是**讀不到**（底噪 ±16%／±74%），不是量到零。翻案的門是「安靜盒子 ＋ 更寬的 b-spread」——但**整族上限已經被釘在 2.16%**，所以翻案也只會得到一個小於 3% 的數字 ⇒ **這一格不需要再跑**（這也是它能結案（排除）而不是「未結案」的依據）。
* 本節**沒有跑任何引擎**：判決是 09-23 的探針產物 ＋ 09-22 的接線 commit；這一節做的是**把判決接上閘門**（並把閘門的一個漏洞補掉）。

---

## §66 決策佇列：8 格 `blocked` 各卡在**哪一型**、**誰**、**下一個動作**是什麼（2026-09-30）

### 66.1 問題：`blocked` 原本只說「跑不了」，沒說「卡在什麼、誰該動」

§64 的閘門把 16 格分成 `needed 0／duplicate 8／blocked 8`。前兩類都是**有答案的**（判決在樹上）；剩下 8 格寫的是「前置不是判決」——但**「不是判決」有五種完全不同的意思**：缺一個人的決定、缺一段程式、缺一個新機制、缺一個乾淨窗口、缺一次建置。把它們混成一類，等於把「下一個動作」藏起來。

所以每一格現在必須宣告 **`precondition.block{kind, owner, next}`**，四型（定義只在 `runnable_gate.BLOCK_*` 一份）：

| kind | 意思 | 解鎖方式 |
|---|---|---|
| `decision` | 缺**一個決定**（operator 的口徑／測試卡 §2.5 的新格子） | 有人裁，量測側給不出新數字 |
| `code` | 缺**原始碼／腳本改動**（可能還要建置閘） | 改完就能量 |
| `mechanism` | 缺**一個新機制**（現有機制已量死或不存在） | 要先有人**指名** |
| `window` | 缺**一個可引用窗口**（盒子狀態） | 等／清機器 |

`owner` ∈ `operator｜engine｜box｜harness`。佇列**排序規則寫死在頁首**：**決定 → 改動 → 機制 → 窗口**（最便宜又解鎖最多的先做），同型再按 id。

### 66.2 佇列（現在就印在看板頁首）

```
decision 3 → code 2 → mechanism 2 → window 1
```

| 型別 | 誰 | 格 | 下一個動作 |
|---|---|---|---|
| `decision` | operator | **L20-10** | 裁：是否在測試卡 §2.5 新增一格（`batch`／`prompt`／`warm_skip` 是嚴格維度，現有孿生只開 `reps`）；獲准後同一窗口跑 B1／B3 成對 |
| `decision` | operator | **L25-1** | 裁：是否為 `CGC_SERVER_LOAD_MODE`（cell 級欄位）新增一格 —— PRIMARY 已否證（E 0.984）；SECONDARY 若也否證 ⇒ 立項卡自寫的死線 ⇒ 25 定案判死 |
| `decision` | operator | **L25-4** | 產品決策：是否改交付口徑（MTP-on 的 k→∞ 上界 24.59 < 25，量測側已封） |
| `code` | engine | **L20-4** | 在 `scripts/run_server.sh` 加 `CGC_EB_NOFILL` 的轉送（對照 `:1719` 的 `CGC_EB_TIMER` 區塊）；免重建，改完即可在交付 cell 定價 fill 項 |
| `code` | engine | **L20-5** | 把 readback 的餵料路徑（`llama-context.cpp:4062-4100`，現掛在 `CGC_SEG_BATCH` 閘下）解放給誠實臂 ＋ 建置閘；再驗 P0 的 6.09% 能否在誠實臂重現 |
| `mechanism` | engine | **L20-7** | 把每步 4.76 ms 的 GPU 插入藏掉或攤平（ρ 支的收益端點）；儀器端點已可量 |
| `mechanism` | operator | **L25-2** | 指名一個上界以外的新機制（⛔ 不得再指 B 半補丁，§51 已撤回） |
| `window` | box | **L20-1** | 等乾淨窗口（壓縮機關、可回收 ≥8.5 GiB、thermal NOMINAL）：先 `harness.py show` 再跑交付 cell 成對重跑 |

### 66.3 D13：型別是**機器強制**的，不是散文

`runnable_gate` 的 D13：`rerun: blocked` 的格必須有 `block`，且 `kind`／`owner` 必須在登記表內、`next` 非空；否則 build 紅。自測 **27/27**（＋4 條：缺型別／kind 不合法／owner 不合法／缺 next ⇒ 紅）。
看板端：`blocked_queue()` 產生佇列，**排序與計數都有測試**（看板自測 **46/46**，＋4 條：排序＝決定→改動→機制→窗口、逐型計數、同型按 id、**不吞重複的格**）。逐子目標頁多一列「**卡在哪一型（D13）／誰／下一個動作**」。

### 66.4 順手對樹事實查核了兩格的「卡點」（`blocked` 的敘述也可能過期）

§65 教訓是「宣告可能過期」——`blocked` 的**卡點敘述**同樣是手寫的，所以對兩格做了唯讀查核：

* **L20-4**：`grep CGC_EB_NOFILL scripts/run_server.sh` ⇒ **0 命中**。launcher 只轉送**明列**的 `CGC_*`（`SERVER_ENV+=(...)`，例如 `:1719` 的 `CGC_EB_TIMER`），未列者**靜默丟掉**（腳本自己的註解就寫過這件事是最貴的混淆之一）⇒ **卡點是真的**，而且是**一段轉送碼、不必重建**。
* **L20-5**：`llama-context.cpp:4062-4100` 的 readback 餵料路徑確實包在 `if (cgc_rb_seg_batch)`（＝`CGC_SEG_BATCH` 那條單次提交臂）之下 ⇒ **卡點是真的**，且註解自己寫明誠實臂要靠 per-layer hook 另有一個 feeder（submit-time residency 57.1% arm vs 96.3% honest）。

### 66.5 這張表怎麼讀

* **3 個決定全部在 operator 手上**（L20-10、L25-1、L25-4），而且**都不需要跑任何東西**就能往前：兩個是「要不要開一格 cell」，一個是「要不要改交付口徑」。
* **2 個改動在 engine**：L20-4 是**一段轉送碼**（最便宜的一項）；L20-5 是**一段原始碼 ＋ 建置**。
* **唯一「只要等」的是 L20-1**（窗口），而它同時是**唯一端點是 tok/s** 的一格。
* ⇒ 整張板子現在的形狀是：**8 格結案、0 格能靠跑產新資訊、8 格等三種人／一種盒子**。

### 66.6 誠實欄

* 佇列回答的是「**誰該動、動什麼**」，**不是**「哪一格比較重要」。排序（決定→改動→機制→窗口）是**成本**排序，理由寫在頁面上，免得被讀成優先級。
* D13 只保證卡點**被型別化、被指派、有下一步** —— **不保證它是真的**。§66.4 的查核是人工做的，而且只做了兩格；第三次出現「宣告與樹不符」時，應該把 `block` 的**卡點**也變成一種可掃描的斷言（像 `precondition` 那樣），而不是再靠人記得去 grep。

---

## §67 卡點也要是可掃描的斷言：`block.assert` ＋ D14（2026-09-30）

### 67.1 問題：§66.4 的查核是**人工 grep**

§66 把 8 格 `blocked` 型別化了（`decision／code／mechanism／window`）——但**卡點本身仍然是散文**：「`CGC_EB_NOFILL` 被 allowlist 丟掉」、「餵料還掛在那條閘下」。§66.4 只是**手動 grep 了兩格**就承認其餘靠信任。而 §65 的教訓正是同一件事發生在上一層：**宣告會過期，而沒有人會記得去查**。

所以每一格的卡點現在必須寫成一條**當場重掃的斷言**。

### 67.2 語意：斷言問的是「**這個卡點現在還在嗎**」

`precondition.block.assert` 三型（定義只在 `runnable_gate.BLOCK_CHECKS` 一份）：

| kind | 意思 | 什麼時候翻（＝紅燈） |
|---|---|---|
| `present_in_file` | 卡點的**證據字串**在某檔裡（例：餵料確實還在那道閘下） | 字串不再命中 ⇒ 卡點可能已解 |
| `absent_in_file` | 卡點的**解方字串**還沒出現（例：launcher 還沒轉送那個旗標） | 解方出現 ⇒ 卡點已解 |
| `none` | 樹上掃不到（必須寫理由） | 不會翻；但會被**數出來**（見 67.5） |

判詞 **`HELD`（卡點還在，正常）／`GONE`（卡點已不在樹上）／`CANNOT_JUDGE`（掃不了，fail-closed）／`UNSCANNED`（沒有可掃的東西）**。

**D14**（`runnable_gate`）：`blocked` 的格必須有 `assert`，`kind` 合法、檔案型必須有 `glob` 與 `contains`；檔案型現判不是 `HELD` ⇒ **紅**（`GONE` 的訊息直接說「這一格該改成可跑或結案，不該還排在佇列裡」）；`none` 沒寫理由 ⇒ 紅。自測 **34/34**（＋7 條：`none` 缺理由、kind 不認得、glob 沒命中（fail-closed）、`present_in_file` 命中／不命中、`absent_in_file` 缺席／出現 ⇒ 逐條紅綠）。

### 67.3 八條斷言（現在各綁在什麼上）

| 格 | 型別 | 斷言 | 現判 |
|---|---|---|---|
| L20-4 | code | `absent_in_file`：`scripts/run_server.sh` 未出現 `CGC_EB_NOFILL` | **HELD** |
| L20-5 | code | `present_in_file`：`llama-context.cpp` 命中 `if \(cgc_rb_seg_batch\)` | **HELD** |
| L20-10 | decision | `present_in_file`：`cell_contract.py` 仍寫著 `ONLY in reps/rep_split`（孿生限制） | **HELD** |
| L25-1 | decision | `absent_in_file`：測試卡尚未出現 `"load_mode": "mmap"` 的 cell | **HELD** |
| L25-4 | decision | `present_in_file`：測試卡仍釘著 `cells.delivery`（＝交付口徑未改） | **HELD** |
| L20-1 | window | `none` —— 窗口是**逐次量測的環境狀態**（free／壓縮機／thermal），不是樹上的事實；跑前判準是 `harness.py show` | UNSCANNED |
| L20-7 | mechanism | `none` —— 缺的是**新做法**（把 4.76 ms 藏掉），不是既有痕跡 | UNSCANNED |
| L25-2 | mechanism | `none` —— 卡點是「**沒有人指名**新機制」，那是一個尚不存在的東西 | UNSCANNED |

⇒ **可掃描 5／8，全部 HELD；3 格樹上掃不到**（逐條列在頁首，不讓它們靜靜被信任）。

### 67.4 反向實測（在 `/tmp` 的副本上，不動真看板）

把 L20-4 的斷言從 `absent_in_file` 翻成 `present_in_file`（＝假裝「轉送已經加上了」）：

```
✗ D14 L20-4 宣告的卡點**已經不在樹上**（CGC_EB_NOFILL 已不再命中（卡點可能已解））
        ⇒ 這一格該改成可跑（needed）或結案，不該還排在佇列裡
VERDICT: FAIL（1 個問題）
```

**這就是這節要買的東西**：以前「卡點已解」要靠人記得回來看；現在誰把轉送碼加上去，下一個 build 就紅。

### 67.5 看不見的東西也要被看見

看板頁首：`卡點斷言（D14）：可掃描 5／8 —— 其中 5 格現判「卡點還在」（HELD，每次 build 當場重掃）；3 格樹上掃不到（UNSCANNED，只靠散文，逐列標示）`；佇列表多一欄**卡點斷言**（HELD 綠、GONE 黃），有 `GONE` 時頁首再開一條警示；逐子目標頁多一列「**卡點斷言（D14）**：判詞 ＋ 命中說明 ＋ 宣告的理由」。看板自測 **49/49**（＋3 條：佇列每列要帶 `HELD`／`GONE`／`UNSCANNED` 三種現判）。

### 67.6 誠實欄

* **斷言的錨點是人挑的字串**：挑得太鬆（例如某檔的註解仍提到那個名字）就會在修好之後繼續報 `HELD`。規則因此寫進 `assert.why`：**要挑程式碼本體的那一行**（L20-5 挑的是 `if (cgc_rb_seg_batch)`，不是變數名）。改檔名／搬家 ⇒ `CANNOT_JUDGE` ⇒ **紅**（fail-closed），不會靜靜變綠。
* **3 格掃不到不是偷懶，是那三種卡點本來就不是檔案事實**：一個是**環境狀態**（每次量測都可能不同）、一個是**尚未出現的做法**、一個是**還沒有人說出口的指名**。「數出來」是對它們唯一誠實的處置 —— 假裝掃得到只會生出下一個假的綠燈（§65 的假 `needed` 就是這樣來的）。

---

## §68 把佇列最便宜的那一項做掉：L20-4 解卡（2026-09-30）

### 68.1 動作：`scripts/run_server.sh` 補一個轉送區塊

launcher 只轉送**明列**的 `CGC_*`，未列者**靜默丟掉**——而 `CGC_EB_NOFILL`（引擎自 2026-09-25 就在 `llama-expert-cache.cpp:3688` 讀它）從來沒有被轉送過。照 `CGC_EB_TIMER`（`:1719`）的樣式補上：

```bash
if [ -n "${CGC_EB_NOFILL:-}" ]; then
    SERVER_ENV+=(CGC_EB_NOFILL="$CGC_EB_NOFILL")
fi
```

區塊裡的註解同時記下三件事：它是**關掉 fill 的診斷臂**（槽位照配置、位元組不讀）、引擎自己寫著 **TIMING ONLY／輸出是垃圾、永不得當正確性量測**，以及**落地怎麼驗**。

### 68.2 落地證明（不必跑模型）

`llama_bench_matrix.arm_env_dropped()` 是唯一的一般性判準（「開關沒作用」vs「開關從沒被設」）：

| | `arm_env_dropped("prod-new", {"CGC_EB_NOFILL": "1"}, …)` | `resolve()` 的 env |
|---|---|---|
| 改之前 | `['CGC_EB_NOFILL=1']`（**被丟掉**） | 沒有這個鍵 |
| 改之後 | `[]`（**落地**） | `1` |

### 68.3 D14 抓到了，並逼出狀態更新

轉送一加，下一輪 `runnable_gate` 立刻紅：

```
✗ D14 L20-4 宣告的卡點**已經不在樹上**（CGC_EB_NOFILL 出現在 scripts/run_server.sh ⇒ 卡點已解）
        ⇒ 這一格該改成可跑（needed）或結案，不該還排在佇列裡
```

**這就是 §67 要買的東西**：卡點解除不是靠人記得回來改看板，而是下一個 build 就紅。

### 68.4 改完之後，這一格要的是什麼（兩根掃描，§65 規則）

L20-4 的 accept 是「**交付 cell** 上**一個**數字 ＋ 不確定度」。兩根獨立掃描：

* **產物根**：`Backup/**/*CGC_EB_NOFILL*` ⇒ **空**（用 `--arms prod-new:CGC_EB_NOFILL=1` 跑的產物，檔名會帶旗標名）。
* **文件根**：`docs/*FILL*` 含 `(fill term|fill 項)…(交付 cell|delivery)` ⇒ **沒有這句**。

⇒ 現判 **ABSENT**，宣告 `needed`：**這一趟有價值**（全板唯一一格）。

### 68.5 順手抓到一個口徑誤標（而且它就在這一格的四個數字裡）

解卡時查核發現：`docs/FILLBUDGET_M_DECOMP_2026-09-29.md` §4 把那一趟寫成「交付 cell」，但產物 `Backup/phase_decomp/fillbudget_m/p1/p1.json` 自己是 **`"named_cell": "(default)"`、`batch 5632`／`p2048`** ⇒ 那個 **5.41 ms 是 `(default)` cell 的讀數**，依 §56 與交付 cell **不可互比**。

⇒ 已在該文件補 **§9 口徑更正**（那條線對「期望值」的結論仍有效；但**不能**當成交付 cell 的 fill 定價）。看板 L20-4 的 `now` 也改成把四個數字逐一標身分：`5.41` = `(default)` cell、`3.955`／`8.3` = 別的儀器與形狀、`1.60–3.20` = op 成本而非牆鐘 ⇒ **四個都不是交付 cell 的數**。

### 68.6 帳目變化（同一輪）

| | 解卡前（§67） | 解卡後 |
|---|---|---|
| 前置口徑 | needed 0／duplicate 8／blocked 8 | **needed 1**／duplicate 8／**blocked 7** |
| 決策佇列 | decision 3／code 2／mechanism 2／window 1 | decision 3／**code 1**／mechanism 2／window 1 |
| 卡點斷言 | 可掃描 5／8（HELD 5） | **可掃描 4／7（HELD 4）**；3 格樹上掃不到 |

⇒ 看板現在第一次能說出「**有一格跑下去會產生新資訊**」。其餘 7 格仍等人（3 個決定、1 個機制提名、1 個工程解）與盒子（1 個窗口）。

### 68.7 驗證

`runnable_gate --selftest` **34/34**、逐格 PASS（0 問題）；看板自測 **49/49**、`--check` PASS；`board_pipeline.sh --check` **rc=0**（quote 18/18、budget 15/15、fake25 11/11、admissible 0/34）；綠燈仍 **0**。**未 commit**；`scripts/run_server.sh` 已備份至 `Backup/quote_gate_2026-09-29/run_server.sh.bak`。

### 68.8 誠實欄

* 這一節**沒有跑模型**：解卡是「程式 ＋ 狀態」的改動，落地用 `arm_env_dropped()` 機械驗證。**實測仍待跑**（交付 cell、乾淨窗口），而它現在是看板上唯一的 `needed`。
* `CGC_EB_NOFILL` 是**診斷臂**：用它量到的 fill 是「**排除 fill**」的價差，**不是**可引用的 t/s（引擎明文寫 TIMING ONLY、輸出垃圾）。這件事寫在 launcher 的新註解裡，不只是文件裡。

## §69 L20-4 結案：交付 cell 上 fill term 的定價＝**區間**〔6.79, 20.68〕ms/step（2026-09-30）

§68 把這一格的**卡點**（launcher 不轉送 `CGC_EB_NOFILL`）解掉之後，它是全板唯一 `needed`。
這一節把那趟跑完，並把判決寫進樹上。

### 69.1 跑了什麼（03:21–03:22，同一個乾淨窗口）

一趟 launch、兩個臂、同一個交付 cell（`-b 512 -ub 512 -p 0 -n 128 -d 512 -r 3 --warm-skip 64
--fixed-fill-seed 1`；contract 13 維 strict 全過、`cell: delivery`）：

| 臂 | extra_env | 角色 |
|---|---|---|
| A | `CGC_EB_TIMER=1` | fill 全開；EBTIMER 是儀器 |
| B | `CGC_EB_TIMER=1;CGC_EB_NOFILL=1` | 不讀位元組 ⇒ fill 的**底**（TIMING ONLY） |

兩臂**都是 `attribution=none`**（`swap_growth=0.0`、thermal `NOMINAL`）、**385／385 個
decode 窗口**（3 rep × 128 步 ＋ 儀器最後一次 flush）⇒ 步數相同，配對合法。產物
`Backup/fill_term_delivery_2026-09-30/ab.json`（＋`ab.logs/*.stderr.log`）。

### 69.2 兩個口徑（「一個數字」的真正答案）

| 口徑 | 值 | 逐 rep | CV |
|---|---:|---|---:|
| ① 儀器（EBTIMER 包住整個 `ensure_batch`） | **6.79 ms/step** | 9.93／4.91／5.40 | 33.6%（穩態後 2 rep 5.18、**4.1%**） |
| ② 牆鐘（逐 rep 配對 `1000/ts_A − 1000/ts_B`） | **20.68 ms/token** | 30.96／16.89／14.19 | 35.5% |

B 臂的 timer 底 **0.061 ms/step** ⇒ 可移除 **6.73 ms/step**（⇒ fill 的成本幾乎全在
「讀位元組」那一支，不在鎖／assignment）。
步時：A `10.773 t/s = 92.83 ms/token`；B `13.733 t/s = 72.82 ms/token`（B 是診斷臂，
輸出是垃圾 ⇒ 那個 t/s 永遠不是能力讀數；這裡只用它的**差**）。

⇒ **價格 ＝ 區間 [`6.79`, `20.68`] ms/step ＝ 交付 cell 步時的 7.3–22.3%**。兩個口徑差
**3.0×**，方向與 09-25 已記過的不一致相同（`S1_SHAPE1_WAIT_BUDGET_2026-09-25.md` §①③：
同一步裡「儀器欄位只看得到 1.86 ms」而 EBTIMER 報 20.8）⇒ 這一格**不是收斂成單點，是收斂
成區間**，而立項卡的 `falsify` 正是「收斂不了 ⇒ 20+ 天花板維持為區間」。

### 69.3 四個舊候選各被指名（驗收的第二半）

| 舊數字 | 口徑 | 為什麼不能進算式 |
|---|---|---|
| 1.60–3.20 | 替代方案（B 半）的 **op 成本** | 不在本 build 的路上，是另一條路的操作數 |
| 3.955 | **同一支儀器、不同 cell**（`fillbudget_m/p1/p1.json`、`named_cell=(default)`、batch 5632／p2048） | 跨 cell 不可比（§56）；交付 cell 的同位數字是今晚的 5.2／6.8 |
| 5.41 | 同一份 (default) 產物的**誠實臂 p50** | 同上 |
| 8.3 | **IO 反解**（導出值） | 導出值；今晚有直接量 |

**順帶查核（這一格的新事實）**：`48.2 ms` 自己出自 **p2048** 的
`Backup/seg_batch_s1_pairs/abba_212809.json`（arm B 單段 20.73 t/s）⇒ `48.2 + x` 是
「**別格**的步時 ＋ **本格**的價格」的混合口徑式。本格能合法交付的是**價格**（區間），
不是「48.2 + x 等於幾 t/s」。另一個直接後果：**光是為 fill 定價買不到 20+**——整個 term
只值步時的 7.3–22.3%，而 20+ 要步時從 92.8 ms 砍到 ≤50 ms（−46%）。

### 69.4 判詞與閘門

- 端點檢查器 **`scripts/check/fill_term_ab.py`**（自測 **21/21**，七條結構閘 fail-closed：
  cell／窗口／步數／B 真的沒讀／B 的底／行數＝reps×(warm_skip+n_gen)／價格落在一步內）。
  判詞 **PRICED**。
- 看板 `L20-4`：`未結案（可跑）` → **結案**（`settled`），`evidence.metric = fill_term_ms`、
  `counter_quote.tool = fill_term_ab` ⇒ **D7b 每次 build 當場重跑**那七條閘 ＋ 價格區間
  與宣告一致（±10%）。`precondition` 由 `needed` 改 `duplicate`（指名
  `docs/FILL_TERM_DELIVERY_2026-09-30.md`＋產物）⇒ 再跑只會重印。
- `COUNTER_METRICS` 增第三型 `fill_term_ms` ⇒ `COUNTER_CLOSE_VERDICTS` 加 `PRICED`
  （前三型是翻轉 `FLIP`／門檻比值 `WITHIN`）。這一型存在的理由：`ms/step` 的**價格**既不是
  時間讀數（正確性未證的臂不給引用），也不是二值翻轉。
- `board_pipeline.sh` 現在驗**六條**閘門（多了 `fill_term_ab --selftest`）。
- 前綴掃描：**`needed 0 / duplicate 9 / blocked 7`**；結案 **9** 格；綠燈仍 **0**。

### 69.5 保留

- **單次成對、A 先 B 後**（非 ABBA）⇒ 次序未分離；A 讀了 4.53 GiB 會把 page cache 弄熱，
  那一項對 B 有利還是無利沒有分離。
- 三場連跑裡只有約 2/3 的 rep 落在穩態（冷頭 9.93 vs 穩態 5.18），**CV 由冷頭主導**；
  子目標 `sg-fill-price`（CV ≤ 20%）因此在整跑口徑**未達**（只有在穩態窗口才達標）。
- `step_ms` 用 `avg_ts`；逐 rep 步時比 `avg_ts` 散。
- 這一節**沒有**改變任何 L20/L25 的達標狀態：fill 這一項就算全部回收也不足 20+。

### 69.6 順帶炸出來的跨格問題（**只是旗標，不在本節重判**）

§69.3 把 `3.955` 指名為 **(default) cell** 的儀器讀數之後，有一條既有結論的**價格那一側**會跟著動：

`docs/MISSHIST_REVIVAL_GATE_2026-09-29.md` §4 的排除表，左欄是**交付 cell** 實測的逐層直方圖
推出的重算價（k=6 ⇒ **6.07 ms**），右欄卻寫「它要取代的 fill ＝ **3.955 ms**」（＝ (default) cell）。
把右欄換成今晚在**交付 cell** 上直接量的價格：

| 價格取哪一個 | k=6 重算 6.07 vs 價格 | 淨 |
|---|---:|---:|
| 3.955（(default) cell，原表） | 6.07 vs 3.955 | **−2.1**（原結論） |
| 6.79（交付 cell，整跑口徑） | 6.07 vs 6.79 | **+0.72** |
| 5.18（交付 cell，穩態口徑） | 6.07 vs 5.18 | −0.89 |
| 20.68（交付 cell，牆鐘上界） | 6.07 vs 20.68 | +14.6 |

⇒ **價格那一側換成同格數字之後，邊際在 ±0.9 ms 之內變號**（原本是 −2.1）。
該節第一條結論（「每層 ≤2」被否證：只有 64% 的步滿足、峰值 6）**不受影響**（那是直方圖的事），
但**第二條**（「連把設計點降到峰值 6，重算都比它要取代的 fill 貴」）**建立在價格是 (default) cell
的前提上**。

這一節**沒有**重判 L20-3，也**沒有**動它的資料列——重判需要一趟**同格**的量（同 cell 的
fill 價 ＋ 逐列重算價，成對），而今晚這一趟不是那個設計。把它記在這裡，是因為它正是 §69
的副產品：**價格一旦指到正確的格子，倚賴舊價格的結論就要重新問一次**。

## §70 L20-3 同格重判：B 半的邊際**不可分辨**（不是負）（2026-09-30）

§69.6 只把 L20-3 的跨格問題掛成旗標。這一節把它量掉，判詞由檢查器
`scripts/check/samecell_margin.py`（自測 17/17，fail-closed）當場現算。

### 70.1 為什麼原本是跨格

`docs/MISSHIST_REVIVAL_GATE_2026-09-29.md` §4 的排除表：左欄是**交付 cell** 直方圖推出的
重算價（k=6 ⇒ 6.07 ms），右欄「它要取代的 fill ＝ 3.955 ms」卻是 **(default) cell** 的儀器
讀數（§69.3 已指名）。§56 之下不可比 ⇒ 這一節把兩側都換成**同一格**。

### 70.2 兩側都在交付 cell（今晚、乾淨窗口）

| 側 | 值 | 出處 |
|---|---|---|
| fill（逐 rep 的計時窗口 p50） | **9.93／4.91／5.40 ms/step**（合計 6.79、穩態 5.16） | `Backup/fill_term_delivery_2026-09-30/ab.json` ＋ EBTIMER |
| 每 miss | **0.756 ms**（0.715–0.810；rep 間 CV 6%） | 同上（逐 rep miss/步 12.27／6.88／7.25） |
| 每 miss 的 bytes | 1.0789 MiB ⇒ 峰值只要 **0.00968 ms** ⇒ **固定開銷 98.72%** | `cache.io_bytes / misses` ÷ 108.8 GiB/s |
| 重算列價 | **0.02491 ms/列**（family 7.62／8.71／7.59 ms per 320 列） | `Backup/mmid_delivery_2026-09-30/mmid_t1_*.json`（used=8、layers=40、T=1、**clean**） |

（09-22 的 8.09 ms 是 **busy-overridden** 窗口（usable 5.31 GiB）⇒ 它的絕對值本來只能當量級。）

### 70.3 能不能分辨（fill 逐 rep × 探針逐趟，9 個組合）

| k | 重算 ms | 覆蓋率 | margin min~max | 符號 |
|---:|---:|---:|---|---|
| 2 | 1.99 | 64.0% | +2.74 ~ +8.03 | + |
| 3 | 2.99 | 89.4% | +1.65 ~ +7.08 | + |
| 4 | 3.99 | 98.1% | +0.56 ~ +6.14 | + |
| 5 | 4.98 | 99.7% | −0.53 ~ +5.19 | +/− |
| **6** | **5.98** | **100.0%** | **−1.62 ~ +4.24** | **+/−** |
| 7 | 6.98 | 100.0% | −2.70 ~ +3.29 | +/− |
| 8 | 7.97 | 100.0% | −3.79 ~ +2.34 | +/− |

靜態圖要**每一步都對** ⇒ 設計點只能取 k*＝6 ⇒ **判詞 `NOT_SEPARATED`**。
（覆蓋率由直方圖現算，與 09-29 §3 的表逐格相同 ⇒ 檢查器的解析與那份文件互為見證。）

### 70.4 判詞的處置

- **L20-3 維持結案（排除）**，`state` 的理由改寫：「不是貴，是**不可分辨**」；
  `evidence` 換成 `margin_ms` 端點（`NOT_SEPARATED`），`precondition` 改指
  `docs/SAMECELL_FILL_VS_RECOMPUTE_2026-09-30.md`。**達標路徑沒有被放寬**：
  `COUNTER_CLOSE_VERDICTS["margin_ms"] = "POSITIVE"` ⇒ 要用這個端點**達標結案**，
  邊際必須分離為正（`NOT_SEPARATED` 永遠不能當達標）。
- **MISSHIST 文件就地更正**：加了 §10（口徑更正），把 §4 的第二條結論標成跨格。
- **真正的槓桿被量出來**：每-miss 成本 **98.72% 是固定開銷** ⇒ 交付 cell 每步 ~8.8 miss
  是「等」（6.6 ms），bytes 只要 0.08 ms ⇒ 該動的是**讓 miss 不阻塞**（`L20-6`），
  不是把 miss 換成重算。剛好與 §60 的 slate handoff（+16%、讀取 4.46→2.73 GiB）同向。
- 測試與閘門：`samecell_margin --selftest` 17/17（含「探針窗口不乾淨 ⇒ REFUSE」
  「T≠1／幾何不符 ⇒ REFUSE」「若真的處處都貴 ⇒ EXCLUDED」）；D7b 多 8 條案例
  （看板自測 58 → **66**）；`board_pipeline.sh` 現在驗**七條**閘門。

### 70.5 保留

- 兩側**不是同一次 launch**（引擎 vs kernel 探針）：同格、同機、各有窗口記錄，
  但不是配對實驗 ⇒ 檢查器報的是 **9 個組合的 min~max**，不是一個點。
- 探針的 `mul_mat_id` 是**重算路徑的代理**（B 沒實作、§51 撤回），與 MISSHIST §4
  用同一個代理 ⇒ 與原表可比，但不是 B 自己的量測。
- 冷頭那一 rep（9.93）是否該算進決策口徑是**決策問題**；表裡兩種都留著，不替它選邊。

---

## §71　交付 cell 上那筆 fill 代價的逐桶分解（2026-09-30）：開檔 0／解碼 99.0%／喚醒 1.0%

**為什麼要做**：§69 把 fill term 定價成區間、§70 換算成「0.756 ms/miss，其中 98.72% 是固定開銷」。
但**「固定開銷」是一個桶名，不是一個數字** —— 那一桶裡可能住著開檔（每次 `open()`）、解碼（pread
本身）或喚醒路徑（condvar 交接），而三者的修法完全不同。這一輪把那一桶拆開，並把**哪幾條槓桿
被量死**釘進樹上。

**儀器**：`CGC_FILL_SPLIT=1`（`llama-expert-cache.cpp` 的 `fill_segments_pool`，預設關；
`run_server.sh` 已轉送）。桶＝`madvise_us`／`build_us`（提交前、呼叫端）＋
`advise_us`（提交後、與 worker **重疊**）＋ `wake_in_us`／`span_us`／`wake_out_us`，
後三者滿足恆等式 `wait == wake_in + span + wake_out`（每批現算）。
判詞：`scripts/check/fill_split.py`（**11/11** 自測；七條結構閘，含恆等式、`jobs<=segs`、
`segs==3×misses`、交付 cell、開檔見證、以及 NOFILL 臂**全 0** 的陰性對照）。

**71.1 量到的（交付 cell、`prod-new`、6 臂 × 3 rep、同一乾淨窗口，`attribution=none`）**

| 桶 | 每 miss (µs) | 佔 wait | 讀法 |
|---|---:|---:|---|
| 開檔 `open()` | **0.0** | 0% | 這條路**沒有** per-fill open（`cache->files` 是 init 的 `FILE*`）；唯一的例外 `cgc_exact_cache_verify_post_fill()` 由 `CGC_EXACT_CACHE_VERIFY` 把守、prod 預設 off |
| `madvise`（P1） | 0.5 | — | µs 量子以下；開 P1 的臂 t/s 無差 |
| `build`（sort/merge） | 5.2 | — | run-merge 在這一格只省 45／12 861 個 job（0.35%） |
| `advise`（rdadvise） | 113.6 | — | 呼叫端真實時間，但**整個藏在 wait 裡**（153 µs/批 vs 等 1 234 µs）⇒ 關掉（`NO_RDADVISE`）span 只 −0.7% |
| `wake_in` | **7.3** | **0.8%** | submit → 第一個 worker 接手 |
| **`span`** | **908.9** | **99.0%** | first dequeue → 最後完成：**pread 在飛** |
| `wake_out` | **2.0** | **0.2%** | 最後完成 → 呼叫端重新跑（含最後一次 notify） |
| **`wait`** | **918.2** | 100% | 恆等式在 6 臂上精確成立 |

**71.2 交叉驗證（三個獨立來源）**：① 恆等式逐臂精確成立；② `CGC_EB_NOFILL` 臂（函式頭即返回）
**桶全 0 且 `read_mib=0`**，但 `misses` 仍 3 909（計數來自 `ensure_batch`）⇒ 桶只量填充路徑；
③ A−NOFILL 的 `pread_usec` 差 **16.18 s／12 780 讀 ＝ 1 266 µs/讀**，`span/batches＝1 222 µs/批`
⇒ 隱含並行度 **4.15 ≈ jobs/batches 4.02** ⇒ `span` 就是 pread 的牆鐘。每次讀取 **0.360 MiB**
⇒ **284 MB/s 每條流**（裝置遠未飽和 ⇒ **latency-bound**）。

**71.3 順帶更正一個口徑**：`final stats` 的 `file_reads/pread_usec` **不是**交付 miss 的成本 ——
NOFILL 臂照樣有 13 740 讀／363.8 s（prefill 的 slab 串流在閘的上游）⇒ `cache: us/job 14150`
與 decode miss 的真實單位成本（1 266 µs）差 **11×**。

**71.4 被量死的槓桿（各自 ≤1%）**：換同步機制／加 worker（`CGC_SERVER_WORKERS=16`：`wake_in`
反漲到 72 972 µs、`span` 不動）、關 `F_RDADVISE`（提示是**免費的**）、開 P1（買不到）、
關 step-ahead（`CGC_DBUF=0` 只 +0.7% ⇒ **DBUF 這一格上沒有在替 demand fill 爭時間**）。

**71.5 指向的機制**：① **把讀取提早**（不要在第一個需要它的 hook 上才發）——每批只有 4.02 個
job、8 個 worker ⇒ 一半閒著，同樣 16.18 s（worker 累計）可以只花 2.0 s 牆鐘；② **少讀**
（miss 4 287／compulsory 3 670，受 `budget_gate` 的記憶體軸封頂）。

**71.6 天花板**：把整個 term 100% 藏掉，牆鐘上界＝NOFILL 臂的 **13.75 t/s**
（94.26→72.72 ms/step＝21.54 ms/step）。⇒ **回收「等」到不了 20+，這一格能買 ~3 t/s。**
⚠ NOFILL 是 timing-only（輸出是垃圾）⇒ 那個 t/s 不是能力讀數，只拿差。
⚠ 兩條口徑差 2.1×（918 µs/miss 儀器 vs 1 929 µs/miss 牆鐘上界）：差的是「不讀」的二階效應
（命中率 96.7→97.0、頁面足跡、與 slab 串流的 I/O 競爭）⇒ **918 不是 t/s 的定價**。

**71.7 產物**：`docs/FILL_SPLIT_DELIVERY_2026-09-30.md`（判決文件）、
`Backup/fill_split_delivery_2026-09-30/`（6 臂 ab.json ＋ stderr）、
`scripts/check/fill_split.py`（檢查器；已進 `board_pipeline.sh`：自測 ＋ 對這批冷凍產物的現判）。

## §72　R5 補收：NOFILL 臂 —— 全語料**最大的兩個**「可引用」decode 讀數當場翻黑（2026-09-30）

### 72.0 這一節是被一個問題炸出來的

operator 問的是「**目前最新的 decode 速度是多少**」。要回答這件事，唯一合法的做法是叫
`quote_gate` 掃全語料（`Backup/**/*.json`，1 116 列），把 `QUOTABLE` 的那幾列列出來排序。
排出來的第一名不是 11.703、也不是 10.923：

    13.750  Backup/fill_split_delivery_2026-09-30/ab.json   逐 rep 1.027  attrib=none  QUOTABLE
    13.733  Backup/fill_term_delivery_2026-09-30/ab.json    逐 rep 1.040  attrib=none  QUOTABLE

兩列都是**同一支臂**：`CGC_EB_NOFILL=1`。而同一個 repo 的原始碼在 `llama-expert-cache.cpp:3735-3745`
自己寫著（這段話是 09-25 加那支臂時寫的）：

    // CGC_EB_NOFILL=1 -- diagnostic no-op arm.
    // ... Output is garbage: this arm is TIMING ONLY, never a correctness measurement.

也就是說：**「最新 decode 速度」這個問題，閘門會先給 13.75，而那個數字來自一支「根本不讀位元組」
的診斷臂。** 這正是 `THROUGHPUT_VOID_INSTRUMENTS` 存在的理由，只是那份清單漏收了它。

### 72.1 為什麼會漏（不是疏忽，是清單只收了一族）

`THROUGHPUT_VOID_INSTRUMENTS` 原本 6 條，全部是**探針族**（`MISS_MASK_DBG`／`MISS_MASK_COST`／
`MISS_MASK_HIST`／`MMV_FUSE_DBG`／`RHO_PROBE`／`SPAC_DBG`）——共同的形狀是「讀回值 ⇒ 多一次
synchronize ⇒ 拖慢」。`CGC_EB_NOFILL` 的形狀不一樣：它**加速**（把整條 fill 短路成
`ok.assign(n, 1); return;`）。清單當初是按「有害的量具」寫的，於是這支**有害的臂**漏在外面。

### 72.2 修正（兩行資料 ＋ 兩條自測）

1. `THROUGHPUT_VOID_INSTRUMENTS` 收第 7 條 `CGC_EB_NOFILL`，理由直接引原始碼那句
   「Output is garbage … TIMING ONLY」＋一句「它量到的是**終點上界**，不是交付臂」。
2. 自測 18 → **20**：新增「R5 NOFILL 診斷臂（窗口乾淨、散度極小）」⇒ `DIRTY`（用今晚真產物的
   形狀：13.750／逐 rep 1.027／`none`／delivery cell，R1–R4 全過，只有臂身分擋得住），
   以及「R5 NOFILL 未開不擋」⇒ `QUOTABLE`（只在真的開著時擋，否則等於所有產物永久紅）。

**效果（現地重掃，同一份 corpus）**：`QUOTABLE 11 → 9`，翻黑的正是 13.750 與 13.733。
修正後，全語料**可引用的 decode 讀數**只剩這五列：

    11.703  (default)  09-29 06:57  mtp_off_clean/res_2026-09-29c.json     逐 rep 1.061
    10.923  delivery   09-30 02:18  l253_anchor_20260930/anchor_delivery_3.json  1.097 / kept 1.023  ← C4
    10.463  （cell 未標）09-30 04:23  fill_split_delivery_2026-09-30/ab.json   1.095
    10.434  （cell 未標）09-30 04:23  fill_split_delivery_2026-09-30/ab.json   1.048
     9.377  delivery   09-29 07:21  stepbudget_2026-09-29/delivery_fuse.json   1.099

### 72.3 連帶：D10 需要一個「基準臂」的概念（否則看板會為了誠實而紅）

閘門收緊的同一個 build 裡，`D10`（臂身分在看板面）立刻紅了 L20-4：它的 `options.arms` 列著
`prod-new:CGC_EB_NOFILL=1`。但 L20-4 的**成對量測本來就需要那支臂**（它是那對的底下那一半，
§69／§71 的價格區間與逐桶分解都靠它差分）。把它刪掉，等於看板說謊「這一格不必開 NOFILL」。

於是 `options.arms` 的每一項現在可以是 `{arm, role, why, source}`：

* `role: reference` ⇒ 這是**成對量測的基準端**（終點上界／底），帶著 R5／R6 的量具是本來就如此；
  **放行的代價**是必須附 `why` ＋ `source`，而且頁面／逐子目標頁都標成
  「**基準臂**：成對量測的端點，報出來的 t/s 不是交付讀數」。
* 其他一切（字串、沒有 `role`、`role: option`）照舊：帶量具就紅。
* `arms` 的每一項要嘛是字串、要嘛是有 `arm` 的 dict，形狀不對 ⇒ D10 紅（不再靜默忽略）。

L20-4 因此改成基準臂形（`why` 寫明「不讀位元組 ⇒ 終點上界」、`source` 指 §71／§69）。
看板自測 66 → **72**（＋6：基準臂附 why＋source 放行／缺 why 擋／缺 source 擋／沒有 role 擋／
`role: option` 擋／壞掉的 arms 項擋）。

### 72.4 「目前最新的 decode 速度」現在的**正確答案**

* **最新一趟（09-30 04:23，§71 的 6 臂矩陣）**：誠實的那五支臂 **10.43–10.83 t/s**；
  同一天 03:22 那趟（§69）A 臂 **10.77**。
* **最新一列可引用的交付 cell 讀數**：**10.923 t/s**（09-30 02:18，C4；`none`、逐 rep
  `[10.34, 11.34, 11.08]`、all 1.097／kept 1.023）。
* **全語料最高的可引用 decode 讀數**：**11.703 t/s**，但那是 `(default)` cell（§56 ⇒ 與交付 cell
  **不可互比**）。
* **13.75 不是任何東西的速度** —— 它是「完全不讀位元組」的上界（§71），現在閘門判它 `DIRTY`。

驗證：`quote_gate --selftest` **20/20**、`decode_board_build --selftest` **72/72**、
`board_pipeline.sh --check` **rc=0**（quote／budget／fake25／runnable／fill_term／samecell／fill_split
七條 ＋ 看板 D1–D14 PASS、綠燈仍 0）。未 commit。

---

## §73　「以前有 12+」的那些紀錄是什麼（2026-09-30）

問：之前有 12+ 以上的紀錄，跟現在最高的 10.923 差在哪裡。
**答：差在五條軸上 —— 沒有一條是「速度掉下來了」。12+ 沒有一筆是 MTP-off 交付 cell 的乾淨讀數。**

掃描口徑：`python3 -X utf8 scripts/check/quote_gate.py check --glob 'Backup/**/*.json'`
⇒ **1 116 列、`QUOTABLE` 只有 9 列**。decode（`shape=p0/*`、cell 不是 `prefill-*`）的 **≥12 共 79 列**，
逐列歸類如下（同一趟的多個 rep-split 啟動算一條）。

### 73.1 五條軸

| # | 軸 | 12+ 的那些是 | 10.923 是 |
|---|---|---|---|
| A | **輸出函數** | `spec_type=draft-mtp`（MTP-on：`prod25`／`prod25-stream`） | `spec_type=''`（MTP-off：`prod-new`） |
| B | **cell** | `delivery-repsplit`（MTP-on 那條線）／`delivery` | `delivery`（同函數的上限是 `(default)` 11.703） |
| C | **臂純度** | R5 量具（`CGC_MISS_MASK_DBG/COST/HIST`、`CGC_SPAC_DBG`）／R6（`CGC_SEG_BATCH`）／`CGC_EB_NOFILL` | 乾淨（`arm_instruments=[]`、`unverified_output_arms=[]`） |
| D | **穩定性** | spread 1.24–3.49 | spread 1.097（kept 1.023、sd 0.52、CV 4.7%） |
| E | **n** | 好幾條 `-r 1` ⇒ `stddev=0` 是結構性的必然 | 單一程序內 `-r 3` |

### 73.2 逐條

| 讀數 | 出處 | 為什麼不能跟 10.923 比 |
|---|---|---|
| **17.669** | `Backup/k3_pair_cert_2026-09-28/logs/r4_b_k2/launch01.json` | A（`draft-mtp`）＋E（`reps=1`）＋B（`delivery-repsplit`） |
| 15.078／15.073×4／14.185×2／12.230×3／11.955／11.758 | 同家族 `Backup/k3_pair_cert_2026-09-28/logs/*` ＋ `Backup/leaderboard/20260928_*_launch0*.json` | 同上：全部 `draft-mtp` × `reps=1` |
| **16.378** | `Backup/anchor_repro_2026-09-28/r3_3.json` | A（`prod25-stream`、`draft-mtp`）＋D：逐 rep `27.25／7.82／14.06`、spread **3.485** |
| **12.954** | `Backup/anchor_repro_2026-09-28/r3_5.json` | A ＋ D：逐 rep `14.76／12.19／11.92`、spread **1.238 > 1.10** |
| **12.57／12.62** | `Backup/prod_profile/prod_profile_20260920_1230.json`（軸 `decode-delivery-anchor`、arm `prod25-stream`） | A：MTP-on，±2.26（18%）。**同一次執行的另一個軸** `decode-delivery`／`prefill250` 只有 **9.90**（±1.25）——同格 cell 三分鐘內差 27% |
| **13.750** | `Backup/fill_split_delivery_2026-09-30/ab.json` arm 5 | C：`CGC_EB_NOFILL=1` 診斷臂（§72；原始碼「TIMING ONLY, never a correctness measurement」）——那是**完全不讀位元組的終點上界** |
| **13.733** | `Backup/fill_term_delivery_2026-09-30/ab.json` arm B | 同上（§69 配對的下界那半） |
| 28.151／28.040／27.940／27.526／27.345／27.811／26.203 | `Backup/eseries/E2_{seg,s1}`、`Backup/s1_g3_ab_2026-09-29/{A,B}.json`、`Backup/clean_window_2026-09-29/s1_1.json`、`Backup/seg_batch_abba_s1b/res_B2.json`、`Backup/quote_hygiene_2026-09-30/k4_noflags.json` | C：R6（`CGC_SEG_BATCH`＝單次提交臂，輸出未驗證） |
| 27.478／27.173／26.782／26.687／26.455／24.420／27.488／27.338 | `Backup/g4miss_*`、`Backup/spac_sweep_2026-09-29/*`、`Backup/fillahead_delivery_2026-09-29/*`、`Backup/miss_hist_2026-09-29/runA.json` | C：R5 量具（`CGC_MISS_MASK_DBG/COST/HIST`、`CGC_SPAC_DBG`）＋R6 |

### 73.3 決定性的那張表

把「乾淨臂 ＋ `attribution=none` ＋ decode」的列拉出來（≥10 t/s 共 34 列），讀每一列的 `spec_type`：

```
17.669 THIN     delivery-repsplit sp=0.000 spec=['draft-mtp']
16.378 UNSTABLE delivery         sp=3.485 spec=['draft-mtp']
15.078 … 12.230 (×10)  THIN/UNSTABLE      spec=['draft-mtp']   ← 全部
12.954 UNSTABLE delivery         sp=1.238 spec=['draft-mtp']
11.703 QUOTABLE (default)        sp=1.061 spec=['None']        ← MTP-off
10.923 QUOTABLE delivery         sp=1.097 spec=['None']        ← MTP-off
```

* **乾淨臂 ＋ `attribution=none` 的 decode ≥12 ⇒ 13／13 全部是 `draft-mtp`（MTP-on）。**
* **乾淨的 MTP-off decode 只有 8 列 ≥10，最高的就是 `(default)` 11.703。** 交付 cell 上最高就是 10.923。

⇒ 兩邊**不是同一條分布**，不存在「從 12 掉到 10.9」這件事。

### 73.4 那 12+ 到底是什麼

* 它是**另一條輸出函數（MTP-on）分布的右尾**：12.57 那條口徑自己的七次重跑是
  `8.74／8.76／8.93／9.34／9.47／12.95／16.38`，**中位 9.34、mean 10.65**，只有 **2／7** 達到 12.57
  （`docs/ANCHOR_12_57_REPRODUCIBILITY_2026-09-28.md` §1）⇒ 12.57 約在**第 78 百分位**。
* 或**診斷臂**（NOFILL 13.75／13.73）——「終點上界」，不是交付讀數（§71／§72）。
* 或**帶量具／單次提交臂**（R5／R6，26–28）。
* 或**n=1**（17.669＝`-r 1` 的第一次啟動）。
* 留在**同一個輸出函數內**的差，只有 `(default) → delivery` 這一格：11.703 → 10.923 ＝ **−6.7%**
  （§56 說 cell 不同不可互比；這裡只是把它當「同一函數內的口徑差」記帳）。

> 一句話：10.923 不是退步，它是「**MTP-off × 交付 cell × 乾淨臂 × n=3 × spread ≤1.10**」這條軸上目前最高的一筆。
> 它自己的缺口是 `budget_gate` ＝ **OVERBUDGET**（足跡級），那是另一本帳。

驗證（本次）：`quote_gate --selftest` **20/20**；全語料掃描 **`QUOTABLE` 9／1 116**；
decode ≥12 共 79 列，其中「乾淨臂 ＋ `attribution=none`」的 ≥12 為 **13／13 全 `draft-mtp`**。未 commit。

### 73.5　那兩筆能不能「重跑成 delivery cell」（追加）

**先看 cell 的差 —— 只有兩個旗標。**（`docs/PROD_NEW_TEST_CARD_2026-09-24.md` 的 machine-readable block）

| 欄 | `(default)`（11.703） | `delivery`（10.923） |
|---|---|---|
| prompt | **2048** | **0** |
| batch ／ ubatch | **5632** | **512** |
| ctx_size | 0 | 4096（這一輪的 delivery 產物 declared override `4096 → 0`） |
| fixed_fill_seed | 1 | null（實際跑 1，declared override） |
| reps ／ warm_skip ／ depths ／ gen | 3 ／ 64 ／ 512 ／ 128 | 同 |
| 量到的那一列 | `p0/n64/d512` | `p0/n64/d512`（**同形狀**） |

**(a) 11.703 → delivery：已經做過，而且三場連續。** 同一支臂（`prod-new`、`extra_env` 空、MTP off）在 delivery cell 上：

| 場（09-30） | 逐 rep | 均值 | 判詞 |
|---|---|---|---|
| 02:15 `anchor_delivery.json` | `[3.81, 11.41, 11.57]` | 8.933 | UNSTABLE（3.036） |
| 02:16 `anchor_delivery_2.json` | `[10.16, 3.95, 11.35]` | 8.486 | UNSTABLE |
| 02:18 `anchor_delivery_3.json` | `[10.34, 11.34, 11.08]` | **10.923** | QUOTABLE |

⇒ 暖 rep 帶 **10.16–11.57**（中位 11.34）；三場裡**兩場各有一個 rep 崩到 3.8–3.9**（位置還從 rep1 換到 rep2）
⇒ 均值被拖到 8.5–8.9。所以「11.703 搬到 delivery cell 會是多少」的實測答案
是 **暖帶 10.2–11.6 ／ 均值 8.5–10.9**，**不是 11.7**。
11.703 自己的逐 rep 是 `[11.655, 11.3814, 12.0729]`，跟交付 cell 的暖帶**重疊**、只高 0.3–0.5
⇒ 兩者是同一個帶；差的是 cell 那**兩個旗標** ＋ 那顆會崩的 rep。

**(b) 12.954 → delivery：它本來就是 delivery cell。** `Backup/anchor_repro_2026-09-28/r3_5.json` 的 `contract`
寫著 `{"ok": true, "cell": "delivery", "declared": ["fixed_fill_seed: None → 1"]}`，而 `batch/ubatch 512`、
`ctx_size 4096`、`prompt 0`、`reps 3`、`warm_skip 64`、`spec_type=draft-mtp`。
所以它不是「不同格」；它的問題是**再現性**。同一支臂／同一格已經 7 趟（09-28 18:34）：

`8.739　8.764　16.378　8.930　12.954　9.469　9.345`（中位 **9.345**、mean 10.65）⇒ ≥12 是 **2／7**；
12.954 自己的逐 rep 是 `[14.76, 12.19, 11.92]`（spread **1.238** > 1.10）。

**現在能不能開跑：不能（窗門拒）。** `python3 -X utf8 scripts/check/harness.py show`（09-30 08:38）⇒
`VERDICT: REFUSED -- reclaimable 6497MB < 8000`；`port 8080 free`、`foreign llama: none`、`other sessions: none`。
要跑得排窗（`--window-timeout-s`）或先把實體記憶體讓出來。

命令（速度實驗必須帶立項卡；`exp-anchor-prodnew` 明文只收 `prod-new`）：

```
python3 -X utf8 scripts/check/harness.py bench --cell delivery --arm prod-new \
  --charter scripts/check/charters/exp-anchor-prodnew.yaml \
  --workdir Backup/<dir>/wd1 --json Backup/<dir>/run.json

python3 -X utf8 scripts/check/harness.py bench --cell delivery \
  --arm 'prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256' --spec-type draft-mtp \
  --charter <另立卡> --workdir Backup/<dir>/wd2 --json Backup/<dir>/run.json
```

**順手量到的一個洞（未修）**：`quote_gate` 的 R1–R6 **沒有一條看 profile**（`prod-new` 與 `prod25` 同權）。
operator 2026-09-28 的命令是「只接受 prod-new ＋ harness bench」，但閘門今天仍然會把一支**平穩**的
`prod25-stream` 讀數放行成 `QUOTABLE`。12.954 之所以只是 `UNSTABLE` 而不是被擋，靠的純粹是它的 spread。

---

## §74　R7（口徑變成閘門）＋ D15（搬成功要放到已認證）＋ 排窗為什麼排不動（2026-09-30）

operator 這一輪的兩句話都落成機器規則了：**「補成 R7：非 prod-new 的 arm 一律不得 QUOTABLE」**
與**「搬成功要放到已認證」**。

### 74.1 R7：口徑（`profile`）進引用閘門

* 判準：`QUOTABLE_PROFILES = ("prod-new",)`（`scripts/check/quote_gate.py`）。出處＝operator
  2026-09-28「只接受 prod-new profile ＋ harness bench，其他測試方法的結果皆不接受」，
  留在樹上的那張卡是 `scripts/check/charters/exp-anchor-prodnew.yaml` 的檔頭。
* 讀法：先讀產物自己的 `profile`；沒有才退回 `tag` 的冒號前段；**兩條都讀不到 ⇒ 擋**（fail-closed）。
* 為什麼 R1–R6 不夠：它們全部只看**這一場內部**（離散／窗口／臂上的旗標／輸出見證），
  沒有一條問「這一場是不是在認可口徑上跑的」。實測：`prod25-stream` 的 12.954（`contract.ok=true`、
  cell=delivery）今天只是 `UNSTABLE` —— 只要那一場的三個 rep 落在 1.10 內，R1–R6 會全部放行。
* 上線後的帳（全語料 `Backup/**/*.json`，1 116 列）：**`QUOTABLE` 9→9（不變）**，
  `UNSTABLE` 502→138、`DIRTY` 393→757 —— 355 列由 `UNSTABLE` 降為 `DIRTY`，而九列可引用的
  **全部**本來就是 `prod-new` ⇒ 這一條沒有動到任何既有的可引用讀數。
* 自測：`quote_gate --selftest` 20→**24/24**（＋4：平穩的 prod25 臂／只有 tag 沒有 `profile`／
  完全讀不到 ⇒ 三者都 DIRTY；prod-new ⇒ 仍 QUOTABLE）。

### 74.2 落地的連帶修正（兩處 fixture 少了 `profile`）

R7 一上線，兩支閘門的**校準 fixture** 當場變紅 —— 這是對的（真產物的臂一定帶 `profile`），
但要修在 fixture 而不是放寬規則：

| 檔案 | 症狀 | 修法 |
|---|---|---|
| `scripts/check/runnable_gate.py` | `quotable_reading：有一列可引用的 ≥10 ⇒ EXISTS` 變 FAIL | fixture 補 `profile: prod-new`；**並新增一列**「R7 非認可口徑 ⇒ ABSENT（不得結掉 needed）」⇒ 自測 34→**35/35** |
| `scripts/check/decode_board_build.py` | D7／D8 的 `gate_ok.json` 被判 DIRTY | fixture 補 `profile`／`tag` |

### 74.3 D15：搬成功要放到已認證（**跑前**寫死，成立就紅）

新增看板鍵 `pending_promotion`（唯一來源＝`scripts/check/decode_board_2026-09-29.yaml`）與閘門 **D15**：

* 每一格必須先寫死 `accept`（人看的驗收）＋ `judge`（機器判準）＋ `charter` ＋ `arms`；
  `arms` 的 profile **必須在認可口徑內**（R7）——否則那一格量到什麼都升不了級，是白等的 pending。
* `judge.kind = quotable_launch_cluster`：掃 `judge.glob` 命中的每一場，問「幾場真的有
  `cell=judge.cell` 的 decode 列**通過引用閘門**」。**≥ `min_quotable` 場就紅**
  （「條件已達成卻還在 pending ⇒ 升進 `certified`」），失敗方向與 `runnable_gate` 的
  「duplicate ＋ 指名判決 ＋ 未結案 ⇒ 紅」一致。
* 自測：`decode_board_build --selftest` 72→**80/80**（＋8：現況不紅／缺 accept／候選臂非認可口徑／
  kind 不認得／門檻不合理／3 場 2 可引用 ⇒ 紅／門檻 4 場 ⇒ 不紅／門檻 3 可引用 ⇒ 不紅）。
  ⚠ 寫的時候踩到一次：fixture 的 `stddev_ts` 必須與 `samples_ts` 的**樣本**標準差相符（除以 n−1），
  用母體標準差會差 1.22× ⇒ 每個 fixture 都被 R0 判 `REFUSE`，案例就變成在測 R0 而不是測 D15。

**C5（已登記，未升級）**：把 C4 從**單點**升級成**分布** —— 交付 cell、MTP off、`prod-new`。
驗收：新一批 ≥3 場（各自獨立啟動）裡至少 **2 場**通過引用閘門 ⇒ 取那 ≥2 場的**中位數**並附區間；
**單點不得升級**。理由寫在格子裡：C4 是 09-30 02:18 那一場，而同一支臂同一格的前兩場各有一顆 rep
塌到 3.8–3.9（§73.5）⇒ 10.923 是右尾之一。

### 74.4 排窗為什麼排不動（這一輪的實測，不是推測）

* `harness.py show`（08:53）：`reclaimable 6648 MB (need 8000)` ⇒ **REFUSED**。
  差值約 1.35 GB，而**最大兩個消費者就是這個 session 自己**：
  `Freebuff Helper (Renderer)` 869 MB ＋ `Freebuff` 的 `bun` 630 MB（＋ Chrome 250 MB）
  ⇒ **從 session 內部解不掉**（關掉它＝關掉這個對話）。
* `launchctl submit` 這條路**不通**：提交成功（rc=0，`launchctl list` 看得到 job），但 job
  立刻以 **exit 1** 結束，連 `>> driver.log` 的第一行都寫不出來 ⇒ launchd 子程序沒有
  `~/Documents` 的 TCC 權限（讀不到專案目錄，所以連 log 都開不了）。已 `launchctl remove`。
* 因此這一批要嘛由 **operator 在自己的終端**跑（那時 Freebuff 不在記憶體裡，窗就開了），
  要嘛在 session 裡**輪詢等窗**。命令（照抄可跑）：

```
cd ~/Documents/flashkv-devserver
mkdir -p Backup/delivery_anchor_rerun_2026-09-30
for i in 1 2 3; do
  python3 -X utf8 scripts/check/harness.py bench --cell delivery --arm prod-new \
    --prompt 0 --gen 128 --depths 512 --reps 3 --warm-skip 64 --fixed-fill-seed 1 --batch 512 \
    --charter scripts/check/charters/exp-anchor-prodnew.yaml \
    --workdir Backup/delivery_anchor_rerun_2026-09-30/wd$i \
    --window-timeout-s 1800 \
    --json Backup/delivery_anchor_rerun_2026-09-30/launch$i.json
done
```

⚠ **`--prompt 0 --batch 512` 不能省**（§82 實測）：`harness bench` 會把預設 cell 的 `-p 2048 -b 5632`
一起送進去，而交付 cell 的權威值是 `-p 0 -b 512` ⇒ `cell_contract` 會 fail-closed 拒跑
（`batch: 5632 ≠ 512`、`prompt: 2048 ≠ 0`）。少了這兩支，三趟會**在 llama-bench 起來之前就全滅**
（12:11:58／12:12:11／12:12:24 三次 rc=1，各 ~13 秒）。

⇒ 產物一落地，`pending_promotion.judge` 的 glob 就會掃到它們；**2 場可引用**的那一刻，
`board_pipeline.sh --check` 會變紅並指名「C5 該升級了」。

驗證（這一輪）：`quote_gate --selftest` **24/24**、`runnable_gate --selftest` **35/35**、
`decode_board_build --selftest` **80/80**、`board_pipeline.sh --check` **rc=0**
（quote／budget／fake25／runnable／fill_term／samecell／fill_split 七條 ＋ 看板 D1–D15 PASS；
既存的 `instrument_binding` 那一格 `VOID-NO-PAIR-LOG`（`score-leaderboard`）——**這一格在同日的 §75 已結清**，理由見該節）。未 commit。


## §75　成績面那一格紅燈結清：排行榜的榜規落後 `quote_gate` 兩條（2026-09-30）

### 紅在哪
`mindmap_void_check.py` 全樹只開一張單：`score-leaderboard` 的 `VOID-NO-PAIR-LOG`，
點名 `Backup/mtp_off_clean/res.json`（09-27 22:03，prefill **306.678**）旁邊沒有成對的 stderr log
（同目錄的 `res_2026-09-29`／`…b`／`…c` 三兄弟都有 `.stderr.log` ＋ `.logs/`，只有它沒有）。

### 病根：節點上那一份榜是**舊榜**
`leaderboard_provenance()` 讀的是節點裡**存下來的** `leaderboard` 區塊，而那一份是 09-29 成對閘門上線
**之前**產生的 ⇒ 它還留著一條規則已經不再允許的列。當場重跑 `build_leaderboard()` 就證明這一點：
prefill `steady` 榜首已經換成 **305.404**（`Backup/l255_close_20260930_cli/run_cli_only.json`，成對 ✓），
306.678 那條列根本不該在榜上。**這不是量測退步，是一份快取落後。**

### 但天真重刷會用一個**更大的洞**補這個洞
同一支重跑把 decode `all` 榜首算成 **27.338**（`Backup/spac_sweep_2026-09-29/k_sweep_T4_K4.json`，
`CGC_SEG_BATCH=1;CGC_B_SCHEME=1;…`），而且仍然收著 **13.75／13.733**（`CGC_EB_NOFILL` 診斷臂）。
這三筆在 `quote_gate` 09-30 就已經是 **DIRTY**（§72 的 R5 ＋ R6 兩條）。所以：

> 一條判準有兩份實作，就會有一份落後。`_arm_on_board` 的 docstring 從 09-29 就寫著「＋有成對 log」，
> 卻對 R5／R6 一無所知 —— 於是「補上成對 log」這一刀砍下去，砍掉的是一個**已經被判死**的洞旁邊那個小洞。

### 修法（單一來源 ＋ 純函式）

| 位置 | 改動 |
|---|---|
| `experiment_sync._arm_on_board` | 直接 `import quote_gate`，用 `arm_instruments()`／`unverified_output_arms()`；**放在 `path is not None` 之外**（臂的身分不需要檔案 ⇒ 不該因為「這個檔判不了」而放行）。新理由：`R5 量具臂 CGC_…`／`R6 輸出未見證臂 CGC_…`。清單**不重寫**。 |
| `experiment_sync._merged_res` | 新純函式：`cmd_leaderboard` 的自動刷新行**不得吃掉人工仲裁**。第一次遇到非自動的 `res` 就把它收進 `res_ruling`，之後每輪以 `res_ruling` 為準 ⇒ 人工的部分永遠只有一份、也不會逐輪累積。 |
| `board_pipeline.sh` | 成績面那一格從 `|| true`（註解寫「rc=1 是預期」）改成**強制**，並補上它自己的 fixture（`--self-test`）。判準既然已經綠了，就不該再留一個「預期會紅」的豁免。 |

### 效果（實測，不是推論）
- 榜掃描 **719** 支臂：`arms_clean` **57 → 53**。理由分佈一併寫回 `scan.skipped`（不是靜默丟掉）：
  R6 `CGC_SEG_BATCH` **19** 支、R5 **25** 支（`NOFILL` 2／`MISS_MASK_*` 群 22／`RHO_PROBE` 1）。
  這 44 支裡只有 **4 支**原本是「上榜乾淨」（其餘 40 支本來就被別的條目擋著，只是理由換了一條）——
  而那 4 支正好就是榜首那一列與 §72 抓出來的兩支 NOFILL。
- decode `all` 榜首回到 **17.669**（prod25／MTP on，帶口徑旗標，仍受 09-29 降級裁定約束）；
  13.75／13.733／27.338 從榜上消失。
- prefill `steady` 榜首 = **305.404**（成對 ✓）。節點 `note` 的臂數一併改成 719／53 並加上「09-30 榜規再收緊」那句
  —— 同一頁上不能同時存在兩個計數。
- `mindmap_void_check.py`：**rc=1 → rc=0**，`0 節點不合規 / 52 已檢`，而且**首次出現 `CLEAN=1`**
  （那個 CLEAN 就是 `score-leaderboard` 自己：它現在的主張有產物、量具有綁、log 成對）。

### 驗證
`experiment_sync --selftest` **80/80**（本輪 +8：R5／R6 上榜判準 4 條、人工仲裁保留 4 條）、
`mindmap_void_check --self-test` ALL PASS、`mindmap_void_check` 現判 **rc=0**、
`board_pipeline.sh --check` **rc=0**（quote／budget／fake25／runnable／fill_term／samecell／fill_split／void
八條 ＋ 看板 D1–D15 全過）。頁面已重建（`index.html`／`TAXONOMY`／52 對白皮書）。未 commit。

### 還開著
- 成對 log 仍是**最大的一格缺口**：719 支臂裡 **338 支**沒有成對 log。修舊語料的唯一入口是
  `instrument_binding.py --pair-backfill <artifact.json> --from <那份 log>`，而且只收**真的存在**的那一份。
- `Backup/leaderboard/` 的副本是掃描**刻意排除**的（`_skip_scan_path`）：不排除的話它會一輪一輪自我增長，
  榜首前十會有一半是副本（09-28 實測）。


## §76　交付文件與 commit 訊息也要有主：`doc_claim_gate`（2026-09-30）

要求：**任何把 t/s 寫進交付文件字串的地方都要指向一個通過閘門的產物，否則就紅。**
這一節記的是「為什麼不能照字面做」與「實際做成什麼」。

### 先量，再寫規則（照字面做的下場）
| 掃法 | 命中 | 其中誤報 |
|---|---|---|
| `docs/**` 全部 t/s 提及 | **2 788** | — |
| 同**段落**出現產物路徑 ⇒ 當成綁定 | 292 | 「產物裡沒有這個數」**201** 筆（表格是一整段、密集敘述一行多檔） |
| 同**行**出現產物路徑 ⇒ 當成綁定 | 136 | 同上 **104** 筆（`| **M-25（25 t/s）** | …（`nf_fill.json`）|` 這種：25 是目標、檔案是隔壁欄的） |
| **一行恰好一個小數 t/s ＋ 恰好一個 `.json`** | **17** | 3（`.log` 指標、整數目標） |

⇒ 前兩種掃法會用 **200 筆誤報**淹掉真訊號，等於沒有閘門。第三種只有 17 筆、而且
**7 筆對得上且可引用／2 筆不可引用（真紅）／5 筆不是 bench 產物**——精度夠。
所以「綁定」被定義成**作者刻意寫出來的形式**（一數一檔），而不是靠距離猜。

### 三條規則（`scripts/check/doc_claim_gate.py`，selftest **22/22**）
* **B 綁定**（判定）：一行恰好一個小數 `t/s` ＋ 一個 `.json` ⇒ 必須存在、是 bench 產物（有 `rows`）、
  有一列的 `avg_ts` 在**文件寫的位數**上等於它、且那一列在 `quote_gate` 是 `QUOTABLE`。
  整數 `t/s`（目標／門檻）與非 `.json` 指標（`.log`／`.yaml`）**不綁**（實測：那兩類壓倒性不是量測主張）。
* **U 無主**（判定）：**交付型文件**（檔名含 `DELIVERY`／`VERDICT`／`REPORT`／`WHITEPAPER`／`CERT`）
  裡的每個 `t/s` 必須被 B 綁定、或所在段落帶「非主張」標記（目標／推算／不可引用／作廢／`非 §5.0`／
  MTP 標籤的「不可與」…）、或登記在凍結帳上。
* **C commit**：訊息逐行套 B。**範圍本身就是它的帳**（推送前掃 `origin/dev..HEAD`），
  歷史用 `--commits-audit HEAD` 只列不判 —— 不把 228 筆歷史 sha 凍進帳面，否則帳就失去「只能往下走」的意義。
  **這條規則在 §77 接成了掛勾**（commit 當下與 push 當下各一道），不再靠人記得跑。

### 範圍分層（沿用專案慣例，不是新發明）
交付面＝檔名含上述字樣且日期 ≥ `2026-09-25`（＝量測契約生效日）⇒ **11 檔、45 個 t/s**。
更早的 dated 交付件（22 檔）是歸檔面：專案慣例「dated 產物不回改」，只稽核。
（`docs/archive/**` 一律排除。）

### 凍結帳：`scripts/check/doc_claim_ledger.json`（7 筆，每一筆都有手寫理由）
上線時 7 筆未結清，逐筆寫明「為什麼它可以先留著」：兩份 09-25／09-28 的 dated 判決自己的讀數、
k-pair 的**配對差**（配對量不是任何單一產物的 `avg_ts`）、`-r 1` 的分布尾端異常（就是被解釋的那個本體）、
一支產物只在 `/tmp` 的臂、以及我自己今晚那份文件的**逐 rep 展幅**（不是第二個讀數）。
鑰匙是 `檔案|數字|sha1(段落)[:8]` ⇒ **段落一被改寫，舊鑰匙立刻失效**（不會無限沿用），
`--prune-ledger` 結清、`--update-ledger` 只加新的。

### 校準時踩到、值得記下來的三個坑
1. **`tg 11.02 ± 1.62 t/s` 的速率是 11.02。** 單位只寫在最後 ⇒ 正則若不吃掉 `X ± Y t/s` 這個形式，
   會把 `1.62 t/s` 讀成速率、而 11.02 完全看不到。第一版就是這樣量錯的（真語料裡現形）。
2. **`±` 展幅本身不是讀數**（`| 11.02 | ± 1.62 t/s |`）⇒ 另加展幅前綴排除，否則製造純誤報。
3. **位移一定要用全文位移。** 我一度把行內位移傳給 `paragraph_at()` ⇒ 段落全切在檔案開頭 ⇒
   標記一個都看不到 ⇒ 無主讀數從 11 變 31。**假紅和假綠一樣貴。**

### 接線與驗證
`board_pipeline.sh` 的閘門數 **7 → 8**（新增 fixture ＋ 現判兩條）。
驗證：`doc_claim_gate --selftest` **22/22**；現判 **PASS**（11 檔／45 提及／帳上 7 筆／未登記 0）；
端到端示範：把「交付 decode 是 13.9 t/s」寫進一份新的交付型文件 ⇒ **FAIL**；
寫成 `10.923 t/s（`Backup/l253_anchor_20260930/anchor_delivery_3.json`）`（QUOTABLE）⇒ 放行；
寫成 `9.3107 t/s（`Backup/mtp_off_clean/res.json`）` ⇒ **綁定違規**
（`NOTQ 對得上但 UNSTABLE：全 rep max/min=2.915>1.10`）。
`board_pipeline.sh --check` **rc=0**。未 commit。

### 實測（commit 模式，這一條才是有牙齒的部分）
`--commits HEAD~3..HEAD` 當場抓到一筆：`commit:7dbbf56c` 的訊息裡有 **24.5 t/s** 而沒有出處 ⇒ 紅。
這正是要的：**歷史是債務，新 commit 是義務**。要過就兩條路 —— 訊息裡帶上產物（`… 10.923 t/s（\`Backup/…json\`）`），
或把那個數字寫成非主張（「目標 24.5」「推算」「已作廢」）。`--commits-audit HEAD` 則把 228 筆歷史一次列出來（只列不判）。

### 還開著
* 歸檔面（22 檔）與 `docs/archive/**` 只稽核；那 2 788 個提及裡沒被綁定的絕大多數是**推算／目標／轉引**，
  定義上不該逐條追。
* 歷史 commit 訊息 228 筆只稽核（sha 不可改）。**新的 commit 沒有出處就會在推送前紅。**
* 帳上 7 筆要逐條結清：正解是那兩份 dated 判決下次改版時把產物路徑補進同一段。


---

## §77　把「記得跑」換成掛勾：commit／push 當下就攔（2026-09-30）

§76 做出了判準（B／U／C）與 CLI，但**什麼時候跑**還是靠人記得。這一節把它接成 git 掛勾 ——
因為「沒跑的那一次」不是少一次檢查，而是那個數字**永久留在歷史裡**（sha 不能改）。

### 一、裝了什麼（`--install-hook`；位置由 git 自己解析，不猜路徑）
| 掛勾 | 何時被叫 | 判什麼 | 有違規時 |
|---|---|---|---|
| `commit-msg` | 每次 commit（git 把訊息檔交給它） | 那一個訊息（`--message-file`） | rc=1 ⇒ **commit 不成立** |
| `pre-push` | 每次 push | **這次真的要推出去的那些 commit** | rc=1 ⇒ **push 不成立** |

`--hooks-status` 是唯讀回報（rc 一律 0），並掛進 `board_pipeline.sh --check`：管線**不因掛勾缺席變紅**
（掛勾不進版控，全新 clone 本來就沒有），但每次跑管線都看得見它裝了沒、出生時間是什麼。

### 二、hook 模式**零豁免**：不吃凍結帳
`--message-file` 不查 `doc_claim_ledger.json`。帳的用途是「上線前既存、且依慣例不回改的 dated 文字」；
新訊息不屬於那個集合。**一旦吃帳，被凍結的那幾筆就變成「這句話可以永遠再寫一次」** ——
閘門會從「新的不可以不合規」被偷換成「舊的白名單」。要過就兩條路：同一行寫出產物，或把那句話寫成非主張。

### 三、工具不在這個 worktree ⇒ **警告放行**（不是靜默，也不是拒收）
`hooks/` 由 git worktree **共用**：本機 `git rev-parse --git-path hooks` 在 `flashkv-devserver` 與
`flashkv0516` 兩棵樹都指向 `flashkv0516/.git/hooks`。所以一支掛勾會同時服務**還沒同步到這支工具**的樹。
在那裡拒收＝用「別的樹還沒更新」把別人鎖死；靜默放行＝壞掉也沒人知道。故：**一行警告 + 放行**，
要硬起來設 `DOC_CLAIM_STRICT=1`（缺席即拒收）。兩種行為都在自測與 scratch repo 裡實測過。

### 四、不覆蓋別人的掛勾；要串接得自己說
既有 `commit-msg`／`pre-push` 不是這支工具裝的 ⇒ **拒裝（rc=1）並印出串接指令**，不動它一個字元。
`--install-hook --chain` 才把原檔保留成 `<name>.chain`，新的入口**先跑它**再跑我們（別人的判準優先）；
`--uninstall-hook` 把 `.chain` 還原回去。**pre-push 不支援串接**：它從 stdin 讀要推的 refs，
兩支都讀會互相吃掉 —— 與其做一個讀一半的串接，不如說「不支援」。

### 五、pre-push 的底線＝**閘門出生時間**，不是日期
安裝時寫一次 `doc_claim_gate.since`（例：`2026-09-30T10:53:21+08:00`），pre-push 用它當底線：
* 出生**之後**的 commit ⇒ 判（這一筆就是「用 `--no-verify` 繞過 commit-msg」的補網）。
* 出生**之前**的 ⇒ 只列不判，並當場說明理由（訊息已在歷史裡，改寫不是這支工具的職權；同 §76 的 228 筆）。
* 戳記一旦寫下**不再前移**（重裝不推底線）—— 否則每次重裝都是一次靜默的赦免。
* 底線只有一個來源：`--commit-since` 沒給時**讀戳記**（掛勾與手跑同一條路），沒有戳記才用常數。
  「CLI 用常數、掛勾用戳記」＝同一個問題兩個答案，遲早有人拿到比較鬆的那一個。

為什麼不能只寫日期：這支閘門是 **09-30 當天**做好的，而當天稍早的 commit 訊息也在歷史裡；
只寫日期會對**不可能回改**的東西開紅單（當天 39 筆未推送 commit 裡有 9 個數字落在這一類）。

### 六、實測（端到端，在 `/tmp` 的 scratch repo 與一支本機 bare remote；不是本樹）
| 情境 | 期望 | 實測 |
|---|---|---|
| commit 訊息 `decode 13.9 t/s`（無出處） | 擋 | rc=1，並印出「第 1 行 13.9 t/s 沒有出處」 |
| 同一句改成 `decode 10.923 t/s（\`Backup/good.json\`）` | 放行 | commit 成立（乾淨時掛勾**完全沒有輸出**） |
| 出處對得上但視窗髒（swap） | 擋 | rc=1，`[NOTQ] attribution=swap≠none` |
| **`--no-verify` 硬塞壞訊息，再 push** | 擋 | push rc=1，點名 `commit:01b19d2f:1 13.9 t/s` |
| 同一筆 amend 成有出處 | 放行 | push rc=0（新分支） |
| 工具不在該樹（`DOC_CLAIM_STRICT` 未設／設 1） | 警告放行／拒收 | rc=0 一行警告／rc=1 |

### 七、這條路上量到的兩個坑
* **ISO 時間字串比大小是錯的**：`2026-09-30T01:00:00+00:00`（＝09:00+08:00）比
  `2026-09-30T02:00:00+08:00` 晚，字串卻比較小。故底線與 commit 日期都解析成 aware datetime 再比；
  commit 日期讀不懂 ⇒ 當「上線後」（fail-closed，不靠壞欄位放行）。三種情形都有自測釘住。
* **`--not --remotes` 是三個詞的 revspec**：`git log` 要三個參數才成立 —— 交給 `shlex.split` 切，
  不要自己發明一套引號規則。

### 八、現況（本樹，09-30）
* `doc_claim_gate.py --commits "HEAD --not --remotes"` ⇒ **rc=0**：39 筆未推送 commit 全部早於出生時間
  ⇒ 只列不判。列入稽核的 8 筆／9 個數字：`7dbbf56c`／`12ed0527`／`a8f490ad` 的 **24.5**、
  `9c8158ba` 的 **12.93／18.13**、`c0d78755` 的 **0.392**、`d91d6c87` 的 **11.61**、
  `1caf35a9`／`29192539` 的 **13.000**。
* 交付面（`docs/**`）**PASS**，凍結帳仍 7 筆、未登記 0 筆。
* `doc_claim_gate.py --selftest` **43/43**（＋19：hook 模式 6、安裝／卸載／串接 6、上線分流 5、revspec 2）。
* `board_pipeline.sh --check` **rc=0**（含新的 `--hooks-status` 一行）。
* **未 commit**；`scripts/check/hooks/*.sh`、`scripts/check/doc_claim_gate.py`、`board_pipeline.sh` 的改動都只在工作樹。

---

## §78　用「真的 harness 產物」復現掛勾 ＋ 放行要留痕（2026-09-30）

operator 的問題：§77 那六條實測用的是**手做的 fixture**（自己捏的 GREEN 臂 ＋ 一支 `Backup/good.json`）——
「可以用 prod-new profile ＋ harness bench 復現嗎」。

答案要分兩層，因為它們的可行性**完全不同**：**判準層已經復現（不需要再跑任何 bench）；產物層現在跑不動，
而且跑出來對這一格不會有新資訊。**

### 78.1 「掛勾對真產物會不會動」——已復現

那一夜（09-30 02:1x）就是這個口徑：`prod-new` profile ＋ `harness bench` ＋ 交付 cell（`-p 0 --gen 128
--depth 512 -b/-ub 512`）、MTP off（`spec_type=null`）、`attribution=none`、thermal NOMINAL、reps 3、
`warm_skip=64`。**三場同形，唯一的差別是逐 rep 離散**：

| 產物 | t/s | 全 rep max/min | 引用閘門 | 掛勾拿它當出處 |
|---|---|---|---|---|
| `Backup/l253_anchor_20260930/anchor_delivery_3.json` | **10.923** | 1.097（kept 1.023） | **QUOTABLE** | **放行**（且完全沒有輸出） |
| `Backup/l253_anchor_20260930/anchor_delivery_2.json` | 8.486 | 2.877 | UNSTABLE | **擋**（`[NOTQ]`，點名 2.877） |
| `Backup/l253_anchor_20260930/anchor_delivery.json` | 8.933 | 3.036 | UNSTABLE | 同左（判準同一條，未逐一入測） |

⇒ 這一格比 fixture 強的地方**不是「會不會擋」**（那個 fixture 已經證了），而是它**分得出是哪一場**：
同一個檔名家族、同一個 cell、同一個 profile、同一個數字範圍，差別只在逐 rep 離散 —— 而閘門認的是
**那一場**，不是那支臂。

### 78.2 五條實測（在**真的**掛勾檔上跑，不是函式呼叫）

`H=~/Documents/flashkv0516/.git/hooks/commit-msg`（兩個 worktree 共用 git common dir ⇒ 這支掛勾就是
本機現在生效的那一支）：

| 訊息 | rc | 掛勾輸出 |
|---|---|---|
| `decode 10.923 t/s`（無出處） | **1** | `⛔ 第 1 行 10.923 t/s 沒有出處` |
| `decode 10.923 t/s（`Backup/l253_anchor_20260930/anchor_delivery_3.json`）交付錨點` | **0** | **（空）** |
| `decode 8.486 t/s（`Backup/l253_anchor_20260930/anchor_delivery_2.json`）` | **1** | `✗ 第 1 行 … [NOTQ] 對得上但 UNSTABLE：全 rep max/min=2.877>1.10` |

另外兩條（`--no-verify` 硬塞 ⇒ pre-push 點名，amend 成有出處 ⇒ 推得出去）在 scratch repo 裡跑，
見 `78.5`。**數字 10.923／8.486 都不是編的**：就是那兩個檔的 `avg_ts`，閘門是拿檔案重新掃出來的。

### 78.3 「現在再跑一趟 harness bench」——**跑不動，而且跑出來也不會多說什麼**

`python3 -X utf8 scripts/check/harness.py show`（09-30 11:39）：

```
port 8080  free　foreign llama: none　other sessions: none
reclaimable 6003 MB (need 8000)   launcher 72% (req 40%, class full-mtp)
VERDICT: REFUSED -- reclaimable 6003MB < 8000 [DISAGREE: the launcher's own probe would admit; binding=harness]
```

* 缺口 ~2 GB，而最大兩個消費者就是**這個 session 自己**（Freebuff renderer ＋ `bun`，§74.4 已記）
  ⇒ **從 session 內部解不掉**。
* 兩條可行路：**由 operator 在自己的終端跑**（那時 Freebuff 不在記憶體裡，窗就開了），
  或在 session 裡 `--window-timeout-s 1800` 輪詢等窗。命令照抄 §63：

```
python3 -X utf8 scripts/check/harness.py bench --cell delivery --arm prod-new \
  --charter scripts/check/charters/exp-anchor-prodnew.yaml \
  --workdir Backup/<dir>/wd1 --window-timeout-s 1800 --json Backup/<dir>/launch1.json
```

* **但它對這一格不會有新資訊**：窗口可重現，一場卻只有約 **1/3** 的機率不塌（§63 三場裡兩場 UNSTABLE，
  塌掉的那個 rep 掉到 3.8–3.9 t/s）⇒ 你會拿到的是一個**新數字**，不是一個**新判準**。
  要驗「掛勾對真產物會不會動」，缺的從來不是更多產物（`anchor_delivery_2.json` 這種**同夜同形的
  UNSTABLE 兄弟**比任何新場都值錢，因為它把「判準認的是那一場」這件事變成可測的）。

### 78.4 「放行」必須留痕（§77 自己最大的洞）

工具不在這個 worktree 時掛勾**警告放行**（理由：hooks 目錄由 worktree 共用，在那裡拒收＝用別人的工具缺席
鎖死別的 worktree）。病根是：**那一行 stderr 只活在當時那個終端裡** ⇒ 事後看 `--hooks-status` 的人分不出
「每一次 commit 都被判過」與「那一段根本沒人看」。三個動作：

* **模板**（`scripts/check/hooks/{commit-msg,pre-push}.sh`）：缺席時寫
  `<ISO 時間> <哪一支掛勾> <worktree 路徑>` 到 `hooks/doc_claim_gate.missing`（與 `doc_claim_gate.since` 同目錄）。
* **`--hooks-status`**：有痕跡就多印一行 `missing ⚠ 工具曾缺席：…（那次 commit 是**放行**的，不是判過的）`；
  rc 仍**一律 0**（hook 不進版控，不能讓它把管線弄紅）。
* **`--install-hook`**：工具回來 ⇒ 刪掉痕跡並印出來（重裝＝承認已同步 ⇒ 那一段放行結案）。

### 78.5 驗證（本輪）

* `doc_claim_gate.py --selftest` **46/46**（＋3：模板要留痕、痕跡要看得見、重裝要清掉）。
* `hook_e2e.py` **15/15**（＋5 真產物那一段；另修掉一條測試自身的缺陷：真產物段改跑自己的分支
  —— 前面刻意壞掉的 commit 還在本機歷史裡，任何 `--not --remotes` 的掃描都會把它們撈進來，
  那不是閘門的問題、是測試沒有把**底線**講清楚）。
* `board_pipeline.sh --check` **rc=0**（含 `--hooks-status`；交付面 11 檔／未登記 0 筆／帳上 7 筆）。
* 真樹：`doc_claim_gate.py --commits "HEAD --not --remotes"` ⇒ **rc=0**；掛勾已用新模板重裝。
* **未 commit**：`scripts/check/{doc_claim_gate.py,hook_e2e.py,hooks/*.sh}` 與本節都只在工作樹。

---

## §79　「12+／13+ 那些紀錄都是錯的嗎」——全語料普查（2026-09-30）

判準：`python3 -X utf8 scripts/check/quote_gate.py --glob 'Backup/**/*.json'` ⇒ **1589 支產物／1116 個 row**。
結論先寫：**不是「都錯」，是四種不同的東西被同一個數字擠在一起**，而它們的處置完全不同。

### 79.1 全語料只有 9 個 row 可引用，其中 decode 最高的兩筆是 11.703／10.923

| row | cell | shape | t/s | 全 rep |
|---|---|---|---|---|
| `mtp_off_clean/res_2026-09-29c.json` | `(default)` | p0/n64/d512 | **11.703** | 1.061 |
| `l253_anchor_20260930/anchor_delivery_3.json` | `delivery` | p0/n64/d512 | **10.923** | 1.097 |
| `fill_split_delivery_2026-09-30/ab.json` | — | p0/n64/d512 | 10.463／10.434 | 1.095／1.048 |
| `stepbudget_2026-09-29/delivery_fuse.json` | `delivery` | p0/n64/d512 | 9.377 | 1.099 |
| `l255_close_*`／`mtp_off_clean/res.json` | — | p2048/n0/d512 | 302.284–312.103 | ≤1.048 |

⇒ **decode 的可引用上緣就是 11.703**（`(default)` cell，§56 不可與交付 cell 的 10.923 互比）。

### 79.2 tg（`gen>0`）≥12 的 row 共 **79 筆，可引用 0 筆**——但理由分四種

| 唯一／主要判詞 | 筆數 | 這是什麼 |
|---|---|---|
| `reps=1<3`（THIN） | **19** | 連中央趨勢都沒有：單趟抽樣 |
| `attribution≠none` ＋ **輸出未驗**（R6） | **30** | 單次提交臂（`CGC_SEG_BATCH` 跳過 hook、池凍結、`gather hits=0/0`）⇒ 量到的**不是交付目標的那個函數** |
| `attribution≠none` **且其他全合格** | **9** | **真正只差一個乾淨窗口**（見 79.3） |
| 離散 >1.10／`口徑` 非 prod-new／臂身分（`CGC_EB_NOFILL`、`CGC_MISS_MASK_COST`）／產物自洽性 | 21 | 前三者是**身分錯**，最後一項 2 筆是 `stddev` 與 `samples` 反推的 CV 差 5 個百分點 ⇒ `REFUSE` |

### 79.3 真正「只差一個乾淨窗口」的只有 9 筆，水準是 12.0–14.9（不是 27）

| 產物 | t/s | 全 rep | attribution |
|---|---|---|---|
| `stepbudget_2026-09-29/gap_sweep.json` | **14.900** | 1.022 | both |
| `phase_decomp/cbnmain_pair/on1/bench.json` | 12.419 | 1.096 | both |
| `eb_timer/row.json` | 12.246 | 1.084 | swap |
| `seg_batch_s1_pairs/res_A1.json` | 12.202 | 1.042 | thermal |
| `mw_ab/mw_ctrl.json` | 12.195 | 1.044 | swap |
| `mw_ab/mw_w2.json` | 12.123 | 1.059 | swap |
| `anchor_prodnew_2026-09-28/r5.json` | 12.084 | 1.004 | both |
| `clean_noinstr_r3/res.json` | 12.046 | 1.094 | both |
| `mtp_off_corridor/off_corridor_20260927.json` | 12.019 | 1.044 | swap |

（前兩筆與 `r5` 的 `profile=prod-new`、`reps=3`、逐 rep 合格 ⇒ 擋住它們的**只有窗口**。其餘各筆的 cell 欄位缺
`named_cell`，要引用前得先確認 cell 契約。）

### 79.4 形態類：名字是「13+」但**根本不是讀數**

* `291925396`／`1caf35a93` 的 **13.000 t/s** ＝ **G7 的門檻**（訊息原文 `improve 11.741 -> 13.000 t/s`；
  另一則是 `11.468 -> 13.000`），不是量測值。兩則都同時寫「本輪不產出可引用的 t/s」。
* `9c8158ba7` 的 **12.93／18.13** ＝ **推算區間**的兩端（`34.5−22.43=12.07 ms ⇒ 77.34 ms ⇒ 12.93 t/s`），
  而且同一則訊息自己寫「兩個輸入都不牢，區間不能拿去做 go/no-go」。
* ⇒ 這一類在**內容上沒有錯**，錯的是**形態**：`N t/s` 出現在訊息裡卻沒有身分標記 ⇒ 讀起來像一場量測。
  `doc_claim_gate` 的 `NON_CLAIM_LABELS` 已經有 `門檻`／`推算`／`外推`／`上界`／`轉引`／`作廢` 等詞
  ——**寫出來就放行**，不寫就擋。這正是新掛勾要逼出來的那一行字。

### 79.5 沒有任何一筆是「量測造假」

79 筆的判詞全部落在**方法論**（reps／離散／attribution／臂身分／輸出見證／自洽性），沒有一條是
「這個數字不是那次跑出來的」。而被判 DIRTY 的那一批，數字多半**真的量到**——只是：
① 窗口髒（swap／thermal／both），② 同一支臂的抽樣離散極大（`anchor_repro_2026-09-28/r3_3.json` 逐 rep
`[27.25, 7.82, 14.06]`；同族 7 趟 `8.74/8.76/16.38/8.93/12.95/9.47/9.35` ⇒ **≥12 只占 2/7**）
⇒ 在這個 regime 裡「12+」不是能力，是**右尾**。

---

## §80　「髒窗口但分數滿足」為什麼不能算——這題在本 repo 有實測，而且仍是待裁決項（2026-09-30）

operator 的問題：「`attribution=both/swap/thermal` 如果是條件不滿足、但分數滿足，為啥不行？」

先說結論：**這個直覺在本 repo 有實測支持，它正是 §13 記下來的那個未裁決爭點**；而此刻仍然不放行，
不是因為「不合規」，是因為**判不出來**。三件事要分開講。

### 80.1 先承認：髒窗口真的可能比較快（不是「髒就該慢」）

* §13：**同一支臂**在 swap 成長 **3191 MiB** 時量到 **11.58 ± 0.14**，在成長 **252 MiB** 時量到
  **9.31 ± 4.50** ⇒ **髒的 +24%**。而 pageouts 只有 8.6 MiB，且**成長有 87% 由起跑狀態決定**
  ⇒ 「用一根與臂無關的欄杆擋掉最好的一場，是選錯變數」（§13 原話）。當時全 repo 有 **55 個讀數**
  坐在這個矛盾上。
* §15 四(1)：`none` 標籤**靠端點不靠低壓** —— 同一趟峰值成長 **+1297 MiB**（遠超 512），只因收尾 < 起跑
  才拿到 `none`；收尾多 60 MiB 就翻回 `swap` ⇒ **這個標籤是脆的**。
⇒ 所以「髒窗口的分數」不是「有瑕疵的分數」，它是一個**真實量到的、可能更高的操作點**。這一點必須先講清楚，
否則後面的話會被讀成「髒就慢」。

### 80.2 那為什麼還是不放行：三個理由，全部與「分數」無關

1. **方向不固定 ⇒ 沒有修正係數可扣**。同一個標籤下兩個方向都存在：髒的 **+24%**（§13 的 11.58 vs 9.31）
   與髒的**崩**（§63 同形三場，某個 rep 掉到 **3.81／3.95**）。既然方向不定，唯一能判的辦法是**重跑**，
   不是換算。
2. **`none` 只是必要、不充分，而「分數滿足」被拆到另一條腿**。五場交付 cell 裡唯一 `none` 的那一場是
   **最慢**的一場（`anchor8g` 8.257，逐 rep `[10.45, 10.44, 3.88]`）；兩個**乾淨標籤**之間自己就差 **+25.7%**
   （9.31 → 11.70，且 9.31 的 rep 散度 ±48%）。⇒ 閘門因此有兩組腿：R4 判**窗口**，R1–R3 判**這一場是不是
   同一個量**。operator 說的「分數滿足」正是 R2／R3 —— 它不是被忽略，是被**拆開**（任一腿不過都不算）。
3. **代價不對稱**。放過一個髒窗口的高分 ＝ 把一個**事故**寫成**能力**（下一步就有人拿它算槓桿 ——
   §41→§50→§51 的三次翻案就是這種帳）；擋住的代價是 **~50 秒**的一趟重跑。

### 80.3 閘門沒有「禁止你寫」

`NON_CLAIM_LABELS`（`上界`／`診斷`／`推算`／`外推`／`轉引`／`不可與…互比`…）與認賬標記（同一段寫出
「不可引用」）都是合法出口。所以準確的說法是：**這個數字可以寫，但不能寫成不帶身分的能力數**。合法的形態：

```
診斷：髒窗口 14.900 t/s（attribution=both；不可與交付 cell 互比）
```

要讓它升格成可引用，唯一的路是**同臂同 cell 在乾淨窗口重跑**：重現 ⇒ 升格；不重現 ⇒ 你用 50 秒買到
「那個數字屬於事故」這個結論。

### 80.4 這是政策，不是品味（§13 的三個選項，仍待裁決）

`R4` 用的是**絕對桿子**，而 §13 已經開出三個最小選項：

* **(a) 拆標籤**：`swap` 拆成 `swap-structural`（殘差在預算內）與 `swap-excess`（超出）⇒ 只翻動
  「結構性超額但殘差合格」那一小批，是三者中最小的一步。
* **(b) 改校準殘差判準**：`is_clean` 改用校準後的殘差 ⇒ 一次翻動 55 支讀數的引用資格。
* **(c) 維持現狀**：絕對桿子，代價是持續把「最好的一場」擋在門外（並讓 §79 那 9 筆停在 12.0–14.9）。

⇒ **未裁決前，閘門維持 (c)**（fail-closed）；要換哪一個是 operator 的決定，本節不自行套用品味。

---

## §81　「同臂同 cell 乾淨窗口重跑」實跑：12+ 沒重現，但先抓到 R6 的一個洞（2026-09-30）

operator 的指令：「唯一的路是同臂同 cell 在乾淨窗口重跑：重現 ⇒ 升格，**試試**」。
試了兩趟（`Backup/clean_window_repro_2026-09-30/run{1,2}.json`，逐趟獨立啟動、`prod-new` 裸臂、
權威預設 cell、reps 3、warm_skip 64、MTP off、立項卡 `exp-anchor-prodnew`、每趟 ~2.5 min）。

### 81.1 先說撈到的那個洞：R6 只認 `CGC_SEG_BATCH`，漏了 `CGC_SUBMIT_AHEAD`

排 §79.3 那張「只差窗口」清單時，**第一名 14.900 是假的**：`stepbudget_2026-09-29/gap_sweep.json`
那一支的 tag 是
`prod-new:CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1;CGC_DECODE_PROFILE_ALL=1;`**`CGC_SUBMIT_AHEAD=1`**。
而本文件 §19／§25 早就寫死了它的性質：

> submit-ahead 把 segment[i+1] 在 segment[i] 的 top-k hook「寫完 remap leaf」之前就 commit，
> 而 segment[i+1] 透過 `mul_mat_id` 消費那個 leaf ⇒ 讀到 stale leaf ⇒ **整張圖壞掉**。
> …而它的代價不是「慢」，是**錯**。（step 75.91 → 58.97 ms，−22.3%）

⇒ 它與 `CGC_SEG_BATCH` 是**同一類**（量到的不是交付目標的那個函數），但 R6 的清單只有後者。
**後果**：只要它抽到一個乾淨窗口，一支輸出壞掉的臂就會變成 `QUOTABLE` —— 而它「只差窗口」。
**處置**：`UNVERIFIED_OUTPUT_ARMS` 補上 `CGC_SUBMIT_AHEAD`（附原始碼出處 ＋ 解除條件＝E2
`CGC_SLOT_TABLE_GPU=1` 的 bit-identical 實測，尚未跑）＋ 兩條 selftest（真形狀 14.900 ⇒ DIRTY；
同一組數把旗標關掉 ⇒ QUOTABLE，證明差的只有那支旗標）。`quote_gate --selftest` **26/26**。

**重掃的結果**（`--glob 'Backup/**/*.json'`，1116 個 row、tg ≥12 共 79 筆）：

| 唯一／主要判詞 | §79 記的 | 補收後 |
|---|---:|---:|
| `reps=1<3`（THIN） | 19 | 19 |
| `attribution≠none` ＋ **輸出未驗**（R6） | 30 | **35** |
| `attribution≠none` **且其他全合格** | 9 | **8** |
| 其他（離散 >1.10／NOFILL 身分／自洽性／`?`） | 21 | **17** |

### 81.2 兩趟實測

| 趟 | 牆鐘 | pp | tg | 逐 rep（tg） | 窗口 | 引用閘門 |
|---|---|---:|---:|---|---|---|
| `run1` | 11:52:39→11:55:5x | 296.156 | **11.577** | `[12.15, 11.37, 11.21]` | swap `+1014 MiB`（worst 1637）、thermal **MODERATE** | **DIRTY** |
| `run2` | 11:56:33→11:58:4x | **309.668** | **11.629** | `[12.12, 11.48, 11.28]` | **`none`**（growth `+14.93 MiB`）、**NOMINAL** | **QUOTABLE**（兩列都過） |

`run2` 的兩列都過 R1–R7 ⇒ **`(default)` cell 上第一次（今天）拿到可引用讀數**，
而且它是這支臂**第二筆**跨日可引用讀數：`11.703`（`mtp_off_clean/res_2026-09-29c.json`，09-29）
vs `11.629`（09-30）⇒ Δ **−0.63%**、同臂／同 cell／同 shape ⇒ 這是這條線第一次有**成對**的乾淨讀數。

### 81.3 「重現 ⇒ 升格」的判讀：**12+ 沒有重現**

* 那 8 筆「只差窗口」全是 **裸 `prod-new`**（無旗標），水準 **12.019–12.419**；而今天同一支臂、
  同一個 cell、同一張卡、窗口乾淨的那一趟是 **11.629**，髒的那一趟是 **11.577** ⇒
  **12.0–12.25 那一簇今天沒有出現**（還差 3–6%）。要嘛是那幾天的漂移，要嘛是那些場次的窗口
  —— 兩個解釋都指向同一個處置：**它不能當門檻**。
* 順手證了 §80.2 的第 1 條（方向不固定）：**今天髒的那一趟比乾淨的那一趟更慢**
  （`11.577`(swap) < `11.629`(none)），與 §13「髒的 +24%」方向相反 ⇒ 「髒窗口的分數」不能靠加減校正。
* 窗口仍是抽籤：兩趟裡一趟乾淨（**1/2**，比 §63 的 1/3 好）。`run1` 的失敗是**它自己的足跡**
  ——(default) cell 的 `-b/-ub 5632` 比交付 cell 的 512 大一個量級，起跑 583 MiB → 峰值 1637 MiB，
  而交付 cell 的三場錨點 growth 是 0（`anchor_delivery_3`：`swap_growth=0.0 MiB`）。

### 81.4 副作用（好的那個）：`run2` 的 prefill 可能是新的榜首

`run2` 的 `p2048/n0/d512` ＝ **309.668 t/s**、`attribution=none`、逐 rep `1.047`／kept `1.029`、
3 reps、`prod-new`、`harness bench` ⇒ 它是**可引用**的，而現行排行榜（`20260930_091943`）的
prefill 榜首是 **305.404**（僅乾淨歸因 53/719 臂）⇒ **下一次排行榜重建時它會變成榜首**。
（本節只記錄；未重建榜面。）

### 81.5 這批沒有動到的東西

* **C5 待升級那條**沒動：它的 judge glob 是 `Backup/delivery_anchor_rerun_2026-09-30/*.json`（**交付 cell**），
  本節兩趟是 `(default)` cell ⇒ 不在它的視窗內（`board_pipeline.sh --check` 因此仍 **rc=0**）。
* 交付 cell 的三場批次仍是「待跑」（§74.3），而窗口**這一刻是開的**（11:56 `reclaimable 10587 MB ≥ 8000`、
  port 8080 free、無 foreign llama）—— 這種窗不會常開。

---

## §82　交付 cell 六場批次（C5 那次嘗試）：**卡點不是窗口，是「第 1 個 rep 天生冷」**（2026-09-30）

operator 的指令：趁窗口開著跑交付 cell 的三場錨點批次，看能不能湊到兩場可引用把 C5 升級。
**跑了 6 場**（`Backup/delivery_anchor_rerun_2026-09-30/launch1..6.json`，逐場獨立啟動、
`--cell delivery --arm prod-new --prompt 0 --gen 128 --depths 512 --reps 3 --warm-skip 64
--fixed-fill-seed 1 --batch 512`、立項卡 `exp-anchor-prodnew`、12:13→12:21）。

### 82.1 結果：1/6 可引用 ⇒ **C5 未升級**（門檻 2 場）

| # | tg | 逐 rep | all | kept | attrib | 閘門 |
|---|---:|---|---:|---:|---|---|
| launch1 | 10.772 | `[10.03, 11.17, 11.12]` | 1.113 | 1.004 | none | UNSTABLE |
| launch2 | 10.869 | `[10.19, 11.17, 11.24]` | 1.103 | 1.007 | none | UNSTABLE |
| **launch3** | **10.754** | `[10.41, 10.66, 11.18]` | 1.074 | 1.049 | none | **QUOTABLE** |
| launch4 | 8.562 | `[3.93, 10.75, 11.01]` | 2.802 | 1.035 | none | UNSTABLE |
| launch5 | 9.849 | `[9.42, 10.67, 9.46]` | 1.132 | 1.128 | none | UNSTABLE |
| launch6 | 10.411 | `[9.59, 11.12, 10.52]` | 1.160 | 1.057 | none | UNSTABLE |

分布：中位 **10.61**、範圍 **8.56–10.87**（C4 的 10.923 不在這 6 場裡 —— 與 §73.5「C4 是那條分布的
右尾」一致）。

### 82.2 真正的卡點：**不是窗口**（六場全部 `attribution=none`）

`launch1..6` 的 `attribution` **全部 `none`**（growth ≤ **17 MiB**、thermal 全 **NOMINAL**）。
擋住的那五場，唯一理由是 **R2 全 rep 離散**（1.113／1.103／2.802／1.132／1.160），
而其中**兩場只差 0.003–0.013**（1.113、1.103），形狀一模一樣：**第 1 個 rep 冷**。

* **5/6 場的第 1 個 rep 低於後兩個 rep**（10.03／10.19／10.41／3.93／9.42 vs 10.7–11.2）；
  `kept` 那一欄幾乎不動（1.004／1.007／1.035）⇒ 冷的是「那一顆 rep」，不是那一場。
* 機制是**格子定義本身**：交付 cell 是 `-p 0`（**沒有 pp 那一列**）⇒ 在被量之前，
  **沒有任何東西把池預熱**；`--warm-skip 64`（跳過 64 token 才開始量）蓋不掉這個。
  對照：(default) cell 有 `pp2048`，今天那兩場的逐 rep 是 `[12.15, 11.37, 11.21]`／`[12.12, 11.48, 11.28]`
  —— **第 1 個 rep 是最高的**（池剛被 prefill 餵飽）。
* ⇒ 這一格的門檻要的是「**第 1 個 rep 不冷**」，不是「窗口乾淨」。§63 把卡點記成「窗口
  只有 1/3 不塌」是**在窗口還真的會髒的年代**成立；今天不是。

### 82.3 順手撈到的第二個觀察：`attribution` 可能在記「模型今天被讀進來了沒」

今天 8 場的 growth 有一個很乾淨的形狀（**n=8、單日 ⇒ 這是觀察，不是結論，但可測**）：

| 今天的第幾場 | 場 | growth | 標籤 |
|---:|---|---:|---|
| 1 | 11:52 `(default)` run1 | **+1014 MiB** | swap |
| 2 | 11:56 `(default)` run2 | +14.9 | none |
| 3–8 | 12:13–12:21 交付 cell ×6 | **+17／0／0／0／−116／−18** | 全 none |

⇒ **只有當天第一場有換頁成長**，之後 7 場全部 ≈0。若這個形狀重複，那 `attribution=none`
有相當一部分是「這個 13 GB 模型今天已經在 page cache 裡」，而不是「盒子忙不忙」——
這正好補上 §80 的論點（標籤脆、且它的值由起跑狀態決定）。**可測**：同一支臂**連跑兩趟**，
第一趟有成長、第二趟沒有 ⇒ 標籤在記第一次讀入。

### 82.4 處置（本輪實際做的）

* **C5 留在 `pending`**（1/6 < 2）：判準沒成立，D15 不紅 ⇒ `board_pipeline.sh --check` **rc=0**。
* **看板那一格的 `state` 就地更正**（`decode_board_2026-09-29.yaml`）：把「待跑（窗門 REFUSED）」
  換成這 6 場的實測與上面那個真卡點 —— 否則下一個 agent 會繼續追「窗口」，而窗口今天根本不缺
  （8 場裡 7 場乾淨）。
* **重跑一次 `decode_board_build.py`**：新的 `launch*.json` 進了 judge 的 glob ⇒ D4 會判
  「產物落後於 YAML」；重建後 **D4 恢復、`--check` rc=0**（這是 D4 的設計行為，不是壞掉）。
* **不動閘門**：R2（全 rep 離散）要不要對「沒有 pp 列的 cell」改用 `kept`／`platform_ts`，
  是**政策決定**（與 §13 的窗口桿子同一個抽屜）；今天只把證據擺出來，不自行套用品味。
