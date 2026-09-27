# 速度提升的驗收鏈：什麼叫「驗收」、要過哪幾關、最快什麼時候

日期：2026-09-26 01:3x　作者：本線（S1／段邊界）
狀態：**判準在跑之前寫死。本文不改任何已量到的數字，只規定「什麼時候可以按哪個鍵」。**
關聯：`docs/S1_LINE_VERDICT_2026-09-25.md`（×1.83）、`docs/STEP23_BLOCKER_AND_RECOVERY_2026-09-26.md`（§8 第 2 步重建）

> ### 【2026-09-27 強制更新，所有 agent 適用，優先於下文】
> 1. **GPU 實驗內核級互斥**：所有速度入口（`harness bench/run/verify`、`commit_bench`、
>    `ab_interleave.py`、`abba_p0_3arm.sh`）在**實際跑 GPU 子進程**期間持有同一把 flock
>    （鎖檔 `/tmp/flashkv_gpu_window.lock`）。第二個實驗在第一個全程結束前阻塞等待；進程退出
>    （含 kill／崩潰）內核自動關 fd 釋鎖。舊的 ps 快照窗口門有 TOCTOU（兩個 harness 可在對方
>    llama-bench 尚未 fork 的間隙同時看到 quiet、一起放行而並發），已由 flock 取代。
> 2. **`--charter none` 無聲後門已關閉**：速度實驗跑前必須立項；急件改 `--charter waive
>    --waive-reason "為何不立項"`，理由記進產物。無 charter 且無顯式 waive 一律拒跑（return 2）。
>
> 觸發：09-27 兩個 harness（off_dense 與 Freebuff null_series）並發，off_dense 被擠到 pp47/tg3.6。
> 詳見 `docs/NEXT_ACTIONS_2026-09-27.md`。

---

## 0. 一句話答案

**在「穩態 miss 率」被量出來之前，沒有可靠的驗收日期可以給。** 那個數字決定三條路線裡走哪一條
（直接做 kernel ／ 先做 async fill ／ 這條線收益被吃掉），而它**今晚就能量**（但**不是** G1 同一支臂 —— 見 §4）。第 2 步已在 `553424ec1` 進版控，`strings` 證明兩側（`MISSMASK il=` 與
`BATCHDBG layer=`）都在 `libllama.dylib` 裡 ⇒ 儀器不是瓶頸，窗口才是。

---

## 1. 先定義：「驗收速度提升」到底是驗什麼

不是「跑一次看 t/s 有沒有變快」。**速度數字早就量到了**：`S1_LINE_VERDICT` §1 的同 build 同 cell
配對是 A 分段 **11.30** → B 單段 **20.73**（**×1.83**，兩臂皆無 spec）。

問題在於那 20.73 是**用錯的權重算出來的**（§5 會給證據鏈）。所以：

> **驗收判據 = 「20.73（或它退化後的值）＋ `answer_md5` 與分段基準逐 rep 相同」。**
> 只有速度沒有正確性，那個數字不是「變快」，是「算別的東西」。

這也解釋了為什麼不能再跑一次「看看多快」當驗收：跑得越快，只代表錯得越快。

---

## 2. 驗收鏈五道閘門（判準凍結於此，跑完不得改）

| 閘 | 名稱 | 前置 | 要跑什麼 | 凍結判準（PASS 才算過） | 成本 |
|---|---|---|---|---|---|
| **G1** | 第 2 步正確性複驗 | `553424ec1` 已入檔（✅） | 一臂帶 4 個 env（§4） | `SET_DIFF=0` **且** `LEN_DIFF=0` **且** 可比層 ≥30 **且** 對齊元素 ≥200 **且** 每元素 miss 數平均絕對差 = 0.0（`miss_mask_check.py` 寫死值） | ~5 min |
| **G2** | **單段下**的 miss 率 | G1 過 | ⚠ **另一支臂**（§4 的理由） | 見 §3 的三分支判決 | ~5 min |
| **G1b** | mask 的成本 | G1 過 | 兩臂：有／無 `CGC_MISS_MASK` | 差值 **≤ 0.2 ms/step**（`COST_BUDGET_MS`） | ~10 min |
| **G3** | zero slot（第 3 步第一個 commit） | G1 過 | 改 `CGC_B_SCHEME` 的 `e % ns` → zero slot；建置；一臂 | 逐 rep greedy 與分段基準相同；`answer_md5` 相同 | 改碼 ~0.5 h ＋ 一臂 ~5 min |
| **G4** | per-expert 重算 kernel | **G2 落在低區間才做**（§3） | 新 kernel ＋ 建置 | 同 G3 的正確性判據 | 未估（設計未完成、owner 未定） |
| **G5** | 交付 cell 配對 | G3（＋G4 若需要） | 交付 cell ABBA，基準 **12.57 t/s** | 落點 **13.6~14.2**；**<13.0 退回**；配對門檻 3% | ~20–30 min |

**G5 才是「驗收速度提升」那一鍵。** G1–G4 全是它的前置。

---

## 3. G2 是分歧點：三條路線，判準先寫死

`STEP23` §4 的定價：低 miss 率補算 **3.6~4.5 ms**，高 miss 率（當年診斷臂量到 k=8 裡 5 個是佔位
≈62%）補算 **24.6~33 ms** —— 後者直接吃掉全部收益。

| G2 量到的穩態 miss 率 | 判決 | 下一關 |
|---|---|---|
| **< 10%** | kernel 划算 | G3 → **G4** → G5 |
| **10%~20%** | 邊界，補算吃一半 | G3 → G4，但 G5 門檻要重訂（不得沿用 13.6~14.2） |
| **> 20%** | **補算不划算，路線要換** | 先做 async／後台 fill。⚠ prebind／預測路線已判死（h=0.03），「先猜再補」這條路沒有已成立的機制 ⇒ 這一格是**本線目前最大的未決風險**，不是「多花點時間」 |

---

## 4. G1 與 G2 是**兩支臂**（本輪更正：先前寫成同一支是錯的）

mask 建在 `if (cgc_slot_table_gpu && il >= cgc_s1_min_il)`（`llama-graph.cpp:2261`）—— **不是**
`CGC_SEG_BATCH` 的分支。所以 `CGC_SLOT_TABLE_GPU=1` 單獨就建得出 mask；但這也意味著
「單段」與「有 hook」是兩個獨立開關，而 `BATCHDBG` 是 **`ensure_batch` 印的**
（`llama-expert-cache.cpp:1433`）—— 單段下 hook 不跑 ⇒ `BATCHDBG` 一行都沒有 ⇒ `SET_DIFF`
無從比起。⇒：

| 臂 | env | 產什麼 | 為什麼必須這樣 |
|---|---|---|---|
| **G1（正確性）** | `CGC_SLOT_TABLE_GPU=1 CGC_MISS_MASK=1 CGC_MISS_MASK_DBG=1 LLAMA_EXPERT_CACHE_BATCH_DBG=1`（**不開** `CGC_SEG_BATCH`） | `MISSMASK` ＋ `BATCHDBG` ⇒ `SET_DIFF`／`LEN_DIFF` | 兩側都要有：hook 必須跑（它是 `BATCHDBG` 的 producer），mask 也要建。分段提交，速度無意義也沒關係 |
| **G2（miss 率）** | `CGC_SEG_BATCH=1 CGC_B_SCHEME=1 CGC_SLOT_TABLE_GPU=1 CGC_MISS_MASK=1 CGC_MISS_MASK_DBG=1` | 只有 `MISSMASK` ⇒ `CGC-MISSMASK-STEP` 的 `misses/layers` | 要的是**單段下**的 miss 率，而那正是 hook 不跑的狀態。**mask 存在的全部意義就在這裡**：它是裝置端答案，不需要 host |

### 4.1 前置：`run_server.sh` 的 env allowlist（本輪已補）

`scripts/run_server.sh:1495` 明文警告：launch line 走 `env "${SERVER_ENV[@]}"`，是 **allowlist**，
沒列的 `CGC_*` **靜默丟棄**，與「儀器沒作用」無法區分。本輪查：`LLAMA_EXPERT_CACHE_BATCH_DBG`
**已**在 allowlist（:1666），但 **`CGC_MISS_MASK`／`CGC_MISS_MASK_DBG` 都不在** ⇒ 就算有窗口，
直接設這兩個 env 會是一個靜默 no-op（不是本輪獨有的陷阱：:1495 的註解說 NOHOOK/NOGATHER
曾因此從 `run_server.sh` 完全不可達）。

本輪已加（:1675／:1689 兩塊，`bash -n` 通過）。加嚴不加寬：`-n` 判斷，未設時完全惰性。

### 4.2 兩個還沒解的風險

1. **`MIN_PAIRS=200` 可能湊不滿。** `Backup/run_req2_retest.sh:125` 的請求是 `max_tokens=24`
   ⇒ 24 個 decode 步 × 38 層，而且**只有「該步該層有 miss」才印一行**。若 miss 稀疏，對齊元素
   可能 < 200 ⇒ G1 判 FAIL，但那是**樣本不足**，不是 mask 錯。
   **凍結在這裡：對齊元素 < 200 時，先加長 `max_tokens` 重跑，不判 mask 錯。**
   （G1 本來就不產速度，所以改請求形狀不違反任何東西。）
2. 這兩支臂的 **t/s 都不可引用**（`_DBG` 每步多一次 `synchronize`）。

### 4.3 窗口

01:29 查為空；**01:36 重查已被佔**（`llama-bench` PID 80831，`-p 512 -n 128 --spec-type draft-mtp`，
不是本線起的）⇒ **今晚的臂要等那個窗口**，或明天。

## 5. 一個會改變讀法的發現（本輪新增，需 G2 裁定）

三條獨立事實串起來：

1. 單段提交下 hook 完全不跑（`CGC-HOOK=0`、`file_reads=0`）⇒ **權重永遠不進池**。
2. 載入時**沒有預填池**（`grep preload|warm_pool|prefetch_all|CGC_PRELOAD src/llama.cpp/src/` **0 命中**）。
3. 缺席 expert 現在被指到 `e % ns` —— **別人的權重**，不是 0（`llama-context.cpp:3607-3634`）。

⇒ **20.73 t/s 是「用別人的權重算，而且不用等 SSD」的速度。** 它快，正是因為它不必做任何
I/O、也不必等任何 fill —— 而那 88.7% 的 `wait` 本來就是 fill 的等待。

由此推出一個**會讓 G2 很難看**的推論：在單段提交下沒有 fill，池子只會變冷不會變熱 ⇒
**miss 率不是圍繞某個穩態抖動，而是隨步數單調上升**，直到工作集規模。若 G2 證實如此，
則第 3 步的 kernel **單獨不夠** —— 重算一個從沒被填過的池，等於把 MoE 算兩遍。

⚠ 這是**推論**，不是量測。它必須由 G2 裁定，而且**它若成立，§3 的高區間分支就是預設路線**，
「明天就能驗收」不成立。正因如此，§0 的答案是「先量 G2 才有日期」。

---

## 6. 最快時間（把未知數標出來，不用樂觀值填）

| 項 | 估計 | 依據 |
|---|---|---|
| G1＋G2 | **~10–15 min**（兩側儀器已入檔、allowlist 已補；**窗口 01:36 被佔，要等**） | 兩臂各 ~5 min |
| G1b | ~10 min | 兩臂 |
| G3 | ~0.5 h 改碼＋建置＋一臂 | 與 `CGC_B_SCHEME` 同區塊，已有 zero slot 設施（`llama-expert-cache.cpp:268-270` 的 gate 要放寬） |
| G4 | **未知** | 設計未完成、**owner 未明文**（本線＝S1／段邊界，freebuff＝GPU 實驗＋量測工具，`src/llama.cpp/src/` 的 kernel 無歸屬） |
| G5 | ~20–30 min | 交付 cell ABBA |

**若 G2 < 10% 且 G4 順利** ⇒ 最快「G3 當晚＋G4 一個工作段＋G5 一窗口」。
**若 G2 > 20%** ⇒ 日期取決於 async fill 這個**還沒有已成立機制**的工程，本文件不給日期。

---

## 7. 現在就該決定的兩件事

1. **要不要跑 G1/G2 這兩支臂**（01:29 閘門為空、**01:36 已被 `llama-bench` PID 80831 佔用**
   ⇒ 現在不能跑，要等窗口）。判準已凍結於 §2，allowlist 已於本輪補上 ⇒ 剩下的只有窗口。
2. **本輪對 `scripts/run_server.sh` 的 allowlist 修改要不要 commit**（它不在 `src/` ⇒ D5 數值閘門
   不是必需；但它是共用檔，另一條線也在用）。
3. **G4（kernel）的 owner**。它在 `src/llama.cpp/src/`，撞 09-20 那五個碰撞面
   （`llama-context.cpp`、`ggml-backend.cpp`、`libggml-base`）。G2 出來之前不定也行，
   但 G3 落地之後它立刻變成關鍵路徑。

---

## 8. G4（kernel）owner 提案（2026-09-26 01:4x，**待 operator 確認**，不是既定事實）

### 8.1 提案：歸**本線（線A / S1 段邊界）**

依 09-20 operator 裁定的**按擁有物**定義：線A 擁有「S1／段邊界（`wait`／`gap`／S2）＋逐層
KIND×OP 儀器」；線 I（freebuff）擁有「`cb`（expert cache 填池 IO）／快取命中儀器」。
per-expert 重算 kernel 是 **S1 的酬載**，不是填池 IO、也不是量測工具 ⇒ 落在線A。

第二個理由是**接續性**：第 2 步的 miss mask 已由本線重建並提交（`553424ec1`）、allowlist 已由
本線補（`58e0f4020`）。同一個 kernel 交給另一條線，會再踩一次已立案的「多 session 同寫一 repo」
＋「建置產物共用」兩個碰撞面 —— 而這次的代價是「第 2 步丟失」那種等級。

### 8.2 配套的去衝突協議（若確認，本線自我約束）

1. **只動** `src/llama.cpp/src/llama-graph.cpp`（建圖）與 `llama-context.cpp` 的 S1 區塊。
2. `ggml-backend.cpp`、`libggml-base`、`llama-expert-cache.cpp` 是 09-20 明列的碰撞面：
   **非必要不動**；必須動時先在記憶檔掛號再動。例外：G3 的 zero slot 需要放寬
   `llama-expert-cache.cpp:268-270` 的 `zero_slot_enabled()` gate —— 那是線 I 的檔案，
   ⇒ **這一處要先跟線 I 打招呼**。
3. **建置前跑閘門，且檢查與建置在同一個分支裡**（`lsof` 8080 ＋ `pgrep` 量測行程，任一非空就
   停手）。本文件 §4.3 那一輪就是這樣被擋下來的。
4. kernel **第一個 commit 不是 kernel**（沿用既有判決）：先 zero slot，讓「缺席那一項 ≈ 0」
   成立，才有得重算。

### 8.3 時點

G2 出來之前不定也不會卡住任何事（G1／G2 不需要 kernel）。**但 G3 一落地它就是關鍵路徑**
⇒ 建議最晚在 G3 那個 commit 之前確認。

---

## 9. G2 實測結果（2026-09-26 03:03–03:20，本輪新增）

### 9.1 先修好一個擋路的 abort（不是量測，是儀器能不能跑）

第 2 步的 `vmask` 只有 `set_output` 而沒有 `ggml_build_forward_expand` ⇒ **首次啟動就 abort**：

```
GGML_ASSERT(buffer_id >= 0) failed   ggml-alloc.c:623
libllama  llama_context::graph_reserve <- sched_reserve <- llama_init_from_model   (rc=-6)
```

`set_output` 只舉旗標、不把節點放進 `gf->nodes`，所以 `vmask` 永遠拿不到後端指派。同檔另外兩個同型
張量（`slot_table` :2267、`rn_mask` :2027）都有 `expand`，補上（`llama-graph.cpp:2446`）後 arm 跑完。
⚠ 界線：09-24 原碼沒進版控、也沒有副本 ⇒ 「忠實重建」是**不可驗證的主張**；可驗證的只有地板：不 abort。

### 9.2 讀到的數：100%，而且釘死

| 項 | 值 |
|---|---|
| `MISSMASK` 行數／compute 次數／層數 | 15015 ／ 385 ／ 39（il=1..39） |
| nsel | 一律 8（top-8） |
| miss 率 MICRO（總和比）／MACRO（逐層平均） | **1.0000 ／ 1.0000** |
| drift head(128) vs tail(128) | 1.0000 vs 1.0000，Δ=**+0.0000** |
| 重複性 | 另一支臂（覆寫失效的同配置）**位元級一致**（15015 行、385 次全同） |

⇒ **每一步、每一層、8 個選中專家全部是佔位。**

### 9.3 機制：不是儀器壞，是「從來沒有人建立過駐留」

`slot_table` 初值全 `-1`（`llama-expert-cache.cpp:3855`），**正值只在真 fill 時寫入**（同步 fill `:1107`、
bg 迴圈 `:3441`/`:3493`）。本 cell 的 `CGC-SHAPE phase=final`：

```
req=5720 hits=5720 misses=0 compulsory=0 capacity=0 evict=0
read_mib=0.0 pread_us=0 fill_wait_us=0     pool_cap_slots=143 slots_layer=143 union=64
```

⇒ **整趟一次 pread 都沒有** ⇒ 沒有任何一次 publish ⇒ 表從頭到尾是 -1 ⇒ 全 invalid。而 B-scheme writer
對非駐留項的 fallback 正是 `e % ns`（`llama-context.cpp:3631`），其註解自己寫明那是
「legal-but-wrong placeholder … routing is wrong, output is garbage, never a deliverable」。

**所以 §5 的推論在方向上成立，但形狀被修正**：不是「miss 率隨步數單調上升」，而是**第一次 compute
就釘在 100% 且不再動** —— 不是「漸漸缺」，是「從來沒有熱過」。

### 9.4 判決：落在 §3 的 **>20%** 分支 —— 但只對這一支臂成立

本臂的 per-expert 重算 = 把 MoE 算兩遍（100% 要補算）⇒ 走「先做 async／後台 fill」那格。

⛔ **不可拿這個 100% 去定價交付 cell。** 它是「單段提交＋hook 不跑」這個診斷配置的性質；
一旦 fill 真的會發生，穩態 miss 率會是另一個數（而且 143 slots/layer vs 256 experts、
`union=64` 說明**這個 cell 的工作集本來就裝得下**）。它回答的是「這條捷徑為什麼快」，
不是「交付系統穩態會缺多少」。

### 9.5 還沒封口的那一個洞（以及為什麼今晚補不上）

`llama-context.cpp:3681` 的 `continue`（nullptr／不在圖／形狀不符）若被觸發，publisher 就沒寫，
葉值是競技場殘留 —— **讀到的 100% 與「真的全 invalid」下游完全無法分辨**。已加 `CGC-MM-PUB`
自證印（publisher 逐類回報 skip 原因＋寫入的 resident 數，`CGC_MISS_MASK_DBG` 閘，只印一次且
`n_wrote>0` 才推進，避免被 prefill compute 吃掉那一次機會）。

那支臂**沒跑成**：`CGC-WATCHDOG: Metal stall … stale=10020ms` 於 compute #1 abort（rc=-6）。
起跑時系統 free **14%**、外來 swap 4.46 GiB；**前三次成功的臂起跑 free 皆為 85%** ⇒ 歸因記憶體飽和，
不是這次的碼（新增的只有一次性的 fprintf）。⇒ 下次要跑之前先釋放記憶體，harness 的 swap advisory
已經這樣講。

### 9.6 今晚的速度數字：一個都不能引用

| 臂 | pp | tg | attribution |
|---|---|---|---|
| 03:03（FIX 後第一支） | 310.55 ± 10.33 | 22.65 ± 1.03 | `both`（thermal HEAVY ＋ swap +2074 MiB） |
| 03:10（同配置重覆） | 351.45 ± 16.16 | 27.04 ± 0.14 | `swap`（+1306 MiB） |

同 arm 相隔 7 分鐘 tg 差 **+19%**，且本臂程式碼自述 output 是 garbage ⇒ 只能當「儀器有跑」的證明。
**本輪可引用的只有結構性結論**（不隨窗口變）：fill 從未發生、駐留從未建立、100% 走佔位。

### 9.7 本輪新踩的坑（寫死，避免再踩）

1. **池 budget 覆寫的鍵名**：必須 `CGC_SERVER_EXPERT_CACHE_BYTES`；無 `SERVER_` 前綴的
   `CGC_EXPERT_CACHE_BYTES` 被 `run_server.sh` 守門**靜默忽略**（`run_server.sh:572-575` 有註）
   ⇒ 兩臂其實都是 8 GiB ⇒ 假結論。本輪實測：覆寫後 `pool budget` 仍印 8589934592。
2. **`harness bench` 的 cell contract 是 fail-closed**：`expert_cache_bytes` 偏離權威值
   8589934592 ⇒ `⛔ cell 口徑與測試卡權威 block 不一致 — 拒跑`。⇒ **池大小掃描不能走生產入口**
   （是設計好的；要掃就得走別的入口並標非生產口徑）。

### 9.8 更正（2026-09-26 09:0x–09:17）：§9.2 的 100% 是**儀器產物**，真值是 **42.93%**

§9.2–§9.6 依 repo 慣例（dated 產物不回改）保留原樣；本節是它們之後的真值與作廢宣告。

#### 9.8.1 §9.5 那個洞封起來了 —— 答案是「publisher 從來沒寫」

`CGC-MM-PUB` 自證印（`n_wrote>0` 才推進的 v1 版）**一行都沒印**。把閘門改成前 8 次呼叫無條件自報
（provenance v2）後，第一次就說清楚了：

```
# run3（03:18 build，publisher v2 自報版）
CGC-MM-PUB n_leaf=39 wrote=0 skip_null=35 skip_not_in_graph=4  ...  # 前 5 次 compute（prefill/暖機）
CGC-MM-PUB n_leaf=39 wrote=0 skip_null=0  skip_not_in_graph=39 ...  # 第 6 次起（decode），每次都這樣
```

⇒ 表**有** 39 張 leaf（`n_leaf=39`），而 `skip_null=0` 表示 39 張**全都有被分配到 data**（即真的在圖裡），
但沒有一張通過成員測試。**publisher 從未寫入任何一個位元組** ⇒ §9.2 的 100% 讀的是競技場殘留值，
**不是駐留狀態的量測**。§9.4「落在 >20% 分支」的判決因此**失去依據**（後來證實結論方向相同，
那是運氣，不是證據）。

#### 9.8.2 根因：`cgc_node_in_graph` 只認 op 節點，不認 leaf

`cgc_node_in_graph`（`llama-context.cpp:3405`）只走 `ggml_graph_n_nodes`（**op 節點**）。而
`ggml_new_tensor_2d` 造出來的 tensor 沒有 producer op，`ggml_visit_parents` 把它歸到
`cgraph->leafs`（`ggml-impl.h:337` 註解「tensors with constant data」），**永遠不會出現在 `nodes`**；
ggml 當時也沒有 leaf 的公開訪問器（正是原作者那段「Public accessors on purpose」註解的由來）。

修法：補 `ggml_graph_leaf`／`ggml_graph_n_leafs` 兩個 `GGML_API` 訪問器（`ggml.h`／`ggml.c`），
並新增 **`cgc_tensor_in_graph`**（nodes＋leafs 都查，`where` 回報命中哪一邊）。**只有 miss-mask
publisher 改用新函式**；既有呼叫點（`:3901` S1 POST、`:3919` rn_mask、`:3923` slot table）
一個都沒動 —— 縮小爆炸半徑。

修後（run4，09:16）：

```
CGC-MM-PUB n_leaf=39 wrote=39 as_leaf=39 skip_null=0 skip_not_in_graph=0 skip_shape=0 st_null=0
            | first_leaf: nn=256 nexp=256 slots=143 had_st=1 nres=143 | resident 5577/9984
```

`as_leaf=39` 直接證實：39 張全在 `leafs` ⇒ 它們本來就在圖裡，只是舊測試看不見。

#### 9.8.3 真值：42.93%

| 項 | 值 |
|---|---|
| `MISSMASK` 行數／compute 次數／層數 | 14926 ／ 385 ／ 39（il=1..39） |
| nsel | 一律 8 |
| miss 率 MICRO ／ MACRO | **0.4293 ／ 0.4293** |
| drift head(128) vs tail(128) | 0.4300 vs 0.4291，Δ=**−0.0010** |
| publisher | wrote=39/39，resident 5577/9984（每層 `nres=143 / slots=143`） |

14926 < 15015 是因為**有 89 個「層×步」真的 0 miss**（0-miss 不印行，是 `miss_mask_check.py` §2
記下的陷阱）⇒ 與「全 miss」互斥，反過來佐證儀器修好了。

#### 9.8.4 判決：仍落 §3 的 **>20%**，但現在是量測而非產物

42.93% ≈ 每步每層 8 個選中專家裡約 **3.4 個**走 `e % ns` 佔位。與 §3 表格裡「當年診斷臂量到 k=8 裡
5 個是佔位 ≈62%」同量級 ⇒ **補算不划算，路線要換：先做 async／後台 fill**。

⛔ 兩個「不可以」：
1. **不可拿 100% 定價**（§9.2／§9.4 已被本節作廢）。
2. **也不可直接拿 42.93% 定價交付 cell** —— 本臂仍是「單段提交＋hook 不跑」的配置，fill 不會發生；
   它量的是「駐留表靜態停在 143/256」下的路由缺口。交付系統穩態是另一個數。

#### 9.8.5 順手挖出來的橫向缺陷（未修，先掛號）

同一個 `cgc_node_in_graph` 被另外兩處用在**同類 leaf** 上，因此**長期靜默**：
- `llama-context.cpp:3919` `rm`（`ffn_moe_rn_mask`，`ggml_new_tensor_2d` leaf）⇒ 永遠 `rm = nullptr`
- `llama-context.cpp:3923` `tb`（slot table，同為 leaf）⇒ 永遠 `tb = nullptr`

凡是「靠這兩個指標為 null 推出來的結論」都要重跑。這兩條不是本線本輪的格子 ⇒ **未修**，等 owner 確認。

#### 9.8.6 另一個坑：free% 的口徑（本輪自己踩的）

權威數字是 `memory_pressure` 的 **`System-wide memory free percentage`**（`harness._sys_snapshot()`
存的就是這個）。**不要用 `vm_stat` 的 free+speculative 自己算** —— 那會漏掉 inactive／purgeable，
本輪算出 8% 而權威值是 **84%**。實測門檻：成功的臂起跑 **78~86%**，被 watchdog 擋下那次 **14%**。

#### 9.8.7 本輪（09:0x）的速度數字：同樣不可引用

| 臂 | pp | tg | 註 |
|---|---|---|---|
| run2（09:01，publisher v1，儀器餓死） | 349.71 ± 2.68 | 25.82 ± 0.38 | 100% 為偽 |
| run3（09:06，publisher v2 自報） | 352.56 ± 9.12 | 26.02 ± 0.17 | 同上 |
| run4（09:16，publisher 修好） | 267.85 ± 28.03 | 21.38 ± 0.40 | 真值臂 |

三支都開著 `CGC_MISS_MASK_DBG`（每步一次額外 synchronize；程式碼註解明寫 “never quote throughput
from an arm with this on”）⇒ **只能證明儀器有跑**。
