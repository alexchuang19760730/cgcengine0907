---
name: cgc-decode-attribution
description: 在 flashkv-devserver（TurboFieldfare / llama.cpp CGC fork）上把一個 decode 步歸因到 GPU 執行、CPU 序列化、池/IO 或編碼，並用既有儀器取得可證的判準數字。當使用者問「decode 為什麼慢」「wait 是什麼」「瓶頸在哪」「要不要做 fusion / kernel 優化」或要求 decode 相位分解、GPU 佔用率、速度槓桿排序時使用。
agent_created: true
---

> **這是快照，不是權威副本。**
> 權威位置：`~/.workbuddy/skills/cgc-decode-attribution/SKILL.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 要改 skill 請改原檔，再重跑 `python3 agent_harness/scripts/import_harness_snapshot.py`。

# CGC decode 步歸因（儀器與陷阱）

專案：`/Users/alexchuang/Documents/flashkv-devserver`
目標配置：prod25 / prefill250，Gemma 4 26B-A4B，M4 Air 16GB。

## 鐵律

1. **~~每次跑之前 `pkill -9 -f llama-server`~~，並用 ABBA 配對 + md5 對比。**

   ⚠️ **2026-09-18 作廢前半句（`pkill -9 -f llama-server`）**：同一台機器上有多條 session 同時量測，
   那行會**把別人正在跑的量測一起殺掉**（看 port 不看 pid，殺了也不知道殺的是誰）。同一家族的缺陷
   還是當天兩次 server 離奇死亡的**真因**：`run_server.sh` 的 preflight 用 `pgrep -f` ＋ 固定 binary
   名清單、**不看 port／不看 session**，而且**排在 memory guard 之前** ⇒ 別人起一次 `run_server.sh`
   就把你的 llama SIGTERM 掉，就算他自己隨後被 guard 擋下，你這輪也已經死了。
   **取代作法**：
   - 先問「是誰在用」：`lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t`、`pgrep -fl 'llama-server|http_duo|profile_duo|run_server.sh'`
     （⚠ **只 pgrep llama 不夠**：09-18 的競爭者 argv 是 `http_duo.py`，字串裡沒有 llama）；
   - 要清就**按 pid 清自己的**：`kill -TERM <pid>`（讓 Metal buffer 正常釋放，別一開始就 -9）；
   - `run_server.sh` 的 preflight 自 09-18 起**預設不再送任何訊號**（只列 `pid/etime/command`），
     要清場必須明示 `CGC_PREFLIGHT_KILL=all`；
   - 自己的工具最好**自選 port**（`http_duo.py --port auto`）＋ `start_new_session=True`，
     清理只碰自己的 pid/port。
   - 题外定義「server 死了」：**看它自己的 log 有沒有 `[CGC] Received SIGTERM`**
     （watchdog 走 `GGML_ABORT`、OOM 是另一回事）；中了就把那臂判成 **無效樣本**，不要當數字用。

   ★★ **2026-09-18 修正：交錯 A/B 不夠，要 ABBA（含反序對）。** 同一個形狀上量 MTP，
   用 `off,on,off` 的 A/B/A ⇒ 兩個控制臂 **10.049 vs 6.791**（差 **48%**），而它們的池統計一致到
   0.2%（hit 96.6/96.7%、file_reads 26925/26685）⇒ 漂移是**外部的慢變數**。
   改成**配對**（交替、短臂、對內取比值）之後仍然有偏：**ABAB 的每一對裡 `off` 總是先跑**，
   於是 10 個臂裡「先跑的 5 個」中位 **8.387**、「後跑的 5 個」中位 **9.434**
   ⇒ **臂的位置本身就值 12.5%**。加上反序對（`on` 先跑）之後，正序 ratio 1.071/1.171/1.013、
   反序 0.938/0.701 ⇒ 順序校正後 `M = sqrt(1.071 × 0.820) = 0.937`。
   ⇒ **量兩臂一律 ABBA，並在報告裡寫明「誰先跑」。**
   （附帶定則：臂要短、丟掉 sample 0、用中位數、**計數器優於 t/s**。完整資料見
   `docs/MTP_NET_EFFECT_PAIRED_2026-09-18.md`。）
2. **啟動器有 env allowlist**：`scripts/run_server.sh` 只透傳列出的 `CGC_*`，
   未列出的**靜默丟棄** → 「沒效果」與「沒設到」長得一模一樣。要用任何新開關前，
   先確認它在 `run_server.sh` 裡有 `if [ -n "${VAR:-}" ]; then SERVER_ENV+=(VAR="$VAR"); fi` 區塊。
   歷史上 `CGC_MMV_FUSE`、`CGC_GPU_TIMING`、`CGC_SUBMIT_AHEAD` 都因此踩過。

   ★ **2026-09-17 新增第三種變體：旋鈕的 push 被關在「另一個 profile 分支」裡。**
   `CGC_SERVER_LAYER_CAPS` 在 `run_server.sh` 有讀者（`:421`）、有回顯（`:1033`），看起來完全支援；
   但它的 `SERVER_ENV+=(LLAMA_EXPERT_CACHE_LAYER_CAPS=…)` **原本放在 `if [ "$SERVER_MTP" = "1" ]` 區塊內**
   ⇒ 在 **MTP=0** 的臂（`p25-gputime`、`decode_sweep.py:273`）上**從未被 export**。
   實測：`CGC_SERVER_MTP=1` → 變數有值；`CGC_SERVER_MTP=0` → **空**。
   症狀與上一條**同形**（「沒設到」＝「沒效果」），而且它剛好落在「最想量它的那一臂」上。
   **判準（唯一可靠的）**：**讀引擎自己印的「已解析值」那一行**，不是讀你傳進去的值。
   這裡是 `llama_expert_cache: LAYER_CAPS per-layer caps: total N slots (avg A/layer, min M/layer)`
   —— **它缺席就是旋鈕沒生效**；只要它出現，`N` 就能與算好的計畫逐格對帳（實測 `37×232+2×212+179=9187` 全中）。
   ⇒ 通則：**任何旋鈕都要有一個「已解析值」的輸出，否則你無法區分「沒設到」與「沒效果」。**
   零成本查法：`CGC_DUMP_ENV=1 CGC_SERVER_PROFILE=<p> CGC_SERVER_MTP=<0|1> bash scripts/run_server.sh`
   （印完即 exit，不啟動任何東西）。
   （此條已修：push 移出 MTP 區塊、保留既有預設 ⇒ 對既有配置逐位元等價。）

   ★★ **2026-09-19：`CGC_SERVER_MTP=0` 不能當「無投機對照臂」——它是 7+ 個 env 的整塊開關。**
   `run_server.sh:2020-2094` 整個 env 區塊被 `if [ "$SERVER_MTP" = "1" ]` 包住，MTP=0 時全消失：
   `CGC_NO_PREFETCH`／`CGC_VERIFY_DECODE`／`CGC_DRAFT_DECODE`／`CGC_WARM_NPAST`／`CGC_MTP_NO_WARMUP`／
   **`CGC_MM_BITIDENT=1`**／`CGC_NO_SEQ_RM_PROBE`；`LLAMA_EXPERT_CACHE_LAYER_CAPS` 也在 `:2099-2104`
   對 MTP=1 給 `40-40:256`、MTP=0 什麼都不給。
   - **`CGC_MM_BITIDENT` 是 bit-identical pillar 1**：把 **M≤8** 的 matmul 釘在 M-invariant `mul_mv`
     路徑（`:2048-2052`），而 **decode 的 GEMV 是 M=1，正在範圍內** ⇒ 兩臂的 decode 走**不同 kernel**。
   - 它**只在那個 MTP=1 區塊裡讀**（`:2053`）⇒ **MTP=0 時設了也被靜默丟棄**，無法從外部補齊。
   ⇒ **任何「MTP on vs off」的逐值／輸出比對都不是單變量**：輸出一旦不同，無法區分
     「verify 路徑有缺陷」與「decode GEMV 換了 kernel、rounding 不同」。
   ⇒ **無投機對照臂＝ `CGC_SERVER_MTP=1` ＋ `CGC_SERVER_MTP_N_MAX=0`**（`:420`→`:1218` 轉發成
     `--spec-draft-n-max`）⇒ 同 env set、同 carrier、同 launcher 分支。
     ⚠ **2026-09-19 18:0x 實測推翻「k=0 合法」這一句**：`--spec-draft-n-max 0` 在**首次 decode**
     就 abort —— `llama-context.cpp:2961: GGML_ASSERT(n_outputs_max <= cparams.n_outputs_max) failed`
     ⇒ **這條對照臂不存在**（`spec_cost_curve.py` 的 k=0 基線也受影響）。
     ⇒ 要無投機對照只能用 **`CGC_SERVER_MTP=0`**，那就回到上面那 7 個 env 的混淆
       ⇒ **兩條路都不乾淨 ⇒ 「MTP on/off 的單變量對照」目前無解，不要假裝有。**
       （補償式替代：F 用 **MTP-off 的 ntok=1** 直接量，再對照曲線外推 ——
        兩者差 **−2.5%** ⇒ 該 env block 不實質移動 F，DESIGN GAP 被實証結清。）
   （同型歷史教訓：`:2048` 原注——「expert cache ON is not bit-identical」的調查一直少開這根支柱。）
3. **不要相信 wall-clock 推論**。`compute` / `wait` 都是 CPU 側牆鐘，無法區分「GPU 真的在算」
   與「GPU 早就算完，剩下是啟動／回報延遲」。要區分就用 GPU 端時間戳（見下）。
4. **md5 只在「同 build 指紋 + 同實際生成長度」下可比**。指紋自 2026-09-15 22:xx 起是
   **glob 所有 `libggml*` / `libllama*` shared library + server binary（8 鍵）**，
   由 `decode_sweep.py` 逐列寫入 `build`（`ab_interleave.py` 已改為委派它，不再自己留一份規則）。
   **舊版只雜湊 `server`/`metal`/`llama` 三檔，而閘門修正所在的 `ggml-backend.cpp` 編進
   `libggml-base`（不在那三檔裡）⇒ 修前修後兩跑被判定「可比」**，也就是閘門修正本身在指紋層面
   **不可見**。這不是「不準」，是**反向**的失效：漏檔會讓不同的東西看起來相同。
   長度是 **`predicted_n`**（實際吐出幾個 token），**不是** `--n-predict` 預算：
   `answer_md5_set` 覆蓋整段 completion，所以同一個 `--n-predict 24` 下各臂長度可以是 24/12/7/6，
   那些列的「md5 不同」**同時被長度混淆**，分不清「軌跡分歧」與「同樣前綴、只是提早停」。
   與長度無關的證據是 `sample`（`texts[0][:160]`）前綴。
5. **★★ 2026-09-19：引用任何 decode t/s 之前，先問「生成長度是多少」—— 而且別假設有穩態。**
   ⚠ **本條 12:0x 由 §EN-195 實測改寫**：原寫「穩態 22.2、暫態 8.1–8.9，拉長就回到 22.2」——
   **對當前 build 不成立**。實測（profile250、8 GiB、NOMINAL 到底）：`-n 128` 7.96（σ **45%**）、
   `-n 512` 隨機 **10.22**（σ 10.5%）、`-n 512` **真文本 10.19**（σ 8.7%）。
   ⇒ **真文本 vs 隨機差 0.2%**（§EN-190「bench 慢是因為餵隨機碼」對 t/s 被證偽）；
   ⇒ **沒有收斂到 22.2**，因為 `-n` 變大時 `capacity miss 12.9%→51.6%（變主項）`、
     `layers_over_slots 5→22`、`worst layer distinct 245 vs slots 143`、evictions/misses≈1.00
     ⇒ **143 槽裝不下真 token 工作集，且越長越糟 ⇒ 結構性 thrash，不是「暫態後收斂」。**
   ⇒ 仍然成立的部分：**採樣位置決定數字**（`-n 128` 比 `-n 512` 低 28%、噪音大 4×）
     ⇒ **報 decode 一律用 `-n ≥512`，且必須同時報 σ**（只看平均會把 45% 的散佈藏掉）。
   **`src/llama.cpp/src/llama-expert-cache.cpp:381-385` 的註解是本機實測，不是推測**：
   一次 67-token prefill 會把池翻掉，接下來 **32 個 decode token 必須重讀 ~1850 experts
   （~2 GB、~2.4 s）才收斂** ⇒ **steady 22.2 t/s，但 prefill 之後的第一段生成掉到 8.1–8.9 t/s**。
   自洽驗算：2 GB ÷ 2.4 s ÷ 32 tok ≈ **75 ms/token** 額外重讀 ＋ 穩態 45 = 120 ms/tok ≈ **8.3 t/s** ✓。
   ⇒ **`n_predict=128` 走不完暫態** ⇒ 今日所有 6.38–13.0 的讀數**全是暫態與穩態的混合**。
   兩台儀器都不例外（`llama-bench` 更糟：**它從不呼叫 `srand`**，4 個 rep 的 2025-token depth
   是 4 串不同隨機 token，每 rep 把剛暖起來的池整個沖掉 ⇒ 從來沒有穩態，spread 1.424 > 自定的 1.10）。
   **⇒ 修量測窗口（零代碼）優先於任何引擎改動**：`n_predict ≥512` 且只報尾段，或用
   `CGC_PREFILL_PROTECT_FILE`（⚠ **`CGC_PREFILL_PROTECT=0` 也會開**——`getenv != nullptr` 就成立）。
   ⚠ 但 **`PREFILL_PROTECT` 的 A/B 不要再跑第二遍**（09-19 §EN-191：漂移 −0.619 t/s per rep > 效應）。
   ⇒ 通則：**「差 2.4×」這種直覺先問「被比較的兩個量是不是同一個東西」** —— 本 repo 已第三次命中的
   正是這一型（§EN-190 的取樣位置、§EN-191 的暫態、此處的穩態 vs 暫態）。
6. **roofline 要先算「每 token 位元組的構成」，不要默認瓶頸在最顯眼的那一塊**（2026-09-19）。
   本機實測（`gguf_pool_geometry.py` 直讀標頭）：**專家 347 MiB/token ＋ 稠密 ~1.45 GiB/token
   ≈ 1.8 GiB/token** ⇒ **專家只佔 20%，稠密佔 80%**。
   而 M1–M6 那一整套池優化打的正是那 **20%**（與 09-18 節點剖析同向：GatedDeltaNet＋狀態管線
   35–45%，MoE 12–15%）。三個 roof 的實測佔用：DRAM ~19 GB/s（**15%** of 120）、
   SSD 7.9 MiB/token（`fill_wait=0.000`）、算力 ~65 GFLOPS（**2%**）
   ⇒ **既不是 IO bound 也不是 compute bound，是固定開銷／序列化受限**
   （633 command buffer/步、6.3 節點/buffer、GPU idle 35.3%；`cache` 桶真工作只夠 0.1–0.5 ms 卻量到 27 ms）。
   **「25 t/s 只需 48 GB/s ＝ 峰值 40%」⇒ 它是可達的，不是天花板問題。**
   （SSD 頂：347 MiB ÷ 3 GB/s = **8.6 t/s** ⇒ 沒有池就回個位數，池是命根子。）
   ⚠️ **2026-09-19 修正：這一條的「固定開銷／序列化受限」框架與由它推出的
   「天花板 1000/55.3 = 18.1 t/s」在生產載體上都不成立，引用前先讀這四行。**
   - `55.3 ms idle` / `633 CB` / `GPU idle 35.3%` 是 **`decode_step_profile.py` 的 `prof` arm** 量的，
     而該 arm 是 **MTP-off ＋ `CGC_SERVER_PROFILE=off`**（`run_server.sh:119` 的預設），
     且 **55.3 是每步、18.1 是每 token**（1 token/步）⇒ 對 MTP-on 的 25 t/s 目標三重不適用。
   - 在 **MTP-on ＋ prod25** 載體上實測（`--arms mtp`，兩支，12-tok 與 2250-tok prompt 一致）：
     **verify 步 203–232 ms，其中 GPU 162–176 ms ＝ 76–80%**；`cb` 54–73 ms（25–32%）；`submit` ~4 ms（2%）。
     ⇒ **GPU 在做實事，不是空轉**；`sync = wait − gpu` 在該載體上**為負** ⇒ 該分解不適用。
   - 每步拆帳：`segs=41 layers=40 ntok=4` ＝ **verify**（1＋3 drafted）；`segs=2 layers=1 ntok=4`
     ＝ **draft forward ≈ 1.8 ms（<1.5%）** ⇒ **優化 nextn 層前向給不了 25**；
     要動的是 verify 的 41 層（其主項是 4 個 token 的專家 union ≈ 132 ms）。
   - ★ 25 t/s 的算術：`13.98 ÷ mean_len 2.35 ⇒ 171.7 ms/步`，目標 **94 ms/步**；
     每步 GPU ≈ 0.78 × 172 ≈ **134 ms > 94** ⇒ **把 CPU 側全部砍到 0 也到不了 25**
     ⇒ **必須降低「每步 GPU 工作量」（＝讓同一個 union 攤更多被接受的 token）**。
7. **★★ 2026-09-19：報 decode 必須同時報「每步 token 數」（接受率），而且 server 那條線的
   第一個 request 是 warmup —— 它的 `mean len` 不是生產值。** 兩點各讓一個結論翻車過一次：
   - 兩側同定義的量是 **`mean_len ≡ emitted / verify_steps`**（server `1 + accepted/verif_steps`；
     bench `n_gen / rounds`）。它與 t/s 一起才能分解缺口：**`t/s 比 = (mean_len 比) × (ms/step 比)`**。
   - **陷阱 1（認 task 編號，不要抓第一行）**：`slot print_timing` 是一 request 一組，**第一組屬於
     warmup**（`n_predict=16`）⇒ 抓第一行會拿到 `mean len = 2.50`，而**實測請求是 2.40**
     （2026-09-19 §EN-212 就是這樣把 2.50 寫進分解的）。
   - **陷阱 2（KV 複用）**：server 的 `prompt eval time` 若只有十幾個 token ⇒ **它複用了前一個 request
     的 KV**，rep2+ **沒有重新 prefill**。所以 server 的 `mean_len` 跨 rep 不變（且逐字元相同），
     而 llama-bench **每個 rep 都重新 fill** ⇒ 池被 churn ⇒ `mean_len` 逐 rep 遞減。
     **這才是 bench/HTTP 差距的主要來源，不是引擎快慢**（`llama-bench.cpp:470` 的註解講的就是這個；
     用 `--fixed-fill-seed` 去驗它是**驗錯變數** —— churn 來自「重新 fill」這個動作，不是 fill 的內容）。
   - 取 `mean_len` 用 `LLAMA_BENCH_SPEC_DBG=1` ＋ 正則 `SPECDBG round: n_done=(\d+) n_past=(\d+) draft=(\d+)`。
     ⚠ `n_done` 印在累加**之前**（`:2955` vs `:3017`）⇒ 用 **`n_gen / rounds`**，不要用 `(末-首)/rounds`。
   - ⚠ **形狀本身會燒機**：`-p 2025 -d 0` 每 rep 全速 prefill ⇒ **thermal HEAVY** ⇒
     **`-p` 形狀的 t/s 不可引用**（該形狀要引用得先確認 NOMINAL）；`mean_len` 是 token 判定，不受熱影響。
   - ★ **權威值是「平台」，而只有 HTTP 能直接給出平台。** 2026-09-19 的階梯（同為 prod25、
     2025-token prompt、128 gen、k=3、8 GiB pool；單位 t/s）：
     | 值 | 形狀 | 為什麼 |
     |---|---|---|
     | 7.48 | `-p 0 -n 128 -d 512`（`profile_duo` 交付 cell） | 冷池＋只填 512＋窗口 128 ⇒ 全在暫態 |
     | 10.20 / 10.79 | llama-bench `-d 2025 -n 128 -r 4` 的 avg / platform | 每個 rep 重填 ⇒ 含暫態；4 rep 散 **1.94×** |
     | **13.1–14.0** | **HTTP `http_duo.py --reps ≥6`，rep 2 起** | **收斂平台（5＋3 個 rep 都不再爬升）** |
     | 10.2 / 11.1 | 首個 request | 冷 |
     ⇒ 報 decode **一律報這一格**：`decode_tps_steady`（reps 2..N 均值）＋ `decode_tps_cold`（rep1）。
     ⚠ **單一 rep 的 `samples_ts` 尖峰不要當收斂值** —— 2026-09-19 曾把 bench 的 rep4（**15.33**，n=1）
     當成「bench 收斂後超越 HTTP」，而 HTTP 側 8 個 rep 一致停在 13.0–14.0 ⇒ 那是上界樣本。
   - ⚠ 對帳 bench／server 的 env 時：**它們本來就是對齊的**（`lbm.resolve(profile, extra)` 與 bench 的
     `env` 逐鍵 diff = 0）。`extra_env` 只是 arm 在 profile 之上多加的 ⇒ 別把「arm 只有 2 個 env」
     誤讀成「少了 MTP env 塊」。

8. **★★ 2026-09-19：這台機器上「連著跑兩臂」默認是無效對照 ---- 第二臂會整段跑在熱池裡。**
   實例：`A(-d 512 -n 256)` 跑完後 thermal 已 **HEAVY 59/180**，緊接的
   `B(--warm-skip 128)` 是 **HEAVY 259/259**（**全程**）⇒ B 的讀數（avg 7.02）是降頻產物，
   而 A（avg 9.72）也已部分受污染 ⇒ **兩臂都不能引用**。
   ⇒ 規則：**每一臂起跑前都要 `thermal=0`**（實測約 2-3 分鐘）。
   ★ 2026-09-19 再踩：我把欄位寫成「兩臂之間」，結果把 **build 排在第一臂之前**
   —— 8 執行緒編譯本身就把機器加熱了，第一臂從一開始就是熱的（C 全段無 NOMINAL、
   D 的 HEAVY 占 172/223）⇒ **兩臒讀數全部作廢**。
   ⇒ **「build」、「前一臂」、「別條線的 run」全部算「前一臂」**；機器熱了就不要開始，
   不要把 build 與量測串在同一個 chain 裡。
   ★★ 後續量出來的**實際噪音底**（這是本機器上任何 decode A/B 的前提）：
   **同配置、同 binary、同形狀的兩臂相差 15–18%**
   （`decode_window_harness.py` 自己的 b1 vs b1b：**102.45 vs 89.25 ms/token = 1.148×**；
   它的 docstring 另記一次 **17.5%**；我自己 `--ctx-size` 重複實驗得 **1.12–1.18×**）。
   ⇒ 任何小於 ~15% 的 decode 效應，**單次或雙次啟動都不可判**，而且你會憑運氣生出 20% 的假效懜
   （2026-09-19 实例：我把某一次 13.819 當成突破報出，重跑變 8.845）。
   ⇒ 規則：**寫任何 decode A/B 結論之前，先跑 `paired_ab.py --null`（兩槽同配置）量底**；
   底若 ≥10% 則正解是「不可測」，不是繼續找槓桿。另外報**配對中位**而非 mean（本日出現過 17.64 這種離群 rep）。，並且每一臂都要報
   `thermal.hist`，不只報 launch/worst**。`worst` 是哪一瞬都不知道的值，
   `hist` 才能讓人看出「這一臂有多少時間在降頻」。
   ⇒ 可行的替代：**ABBA 交錯**（漂移會抵消），或把每臂做短、多跑幾輪。
   ⇒ 附帶：不要用 `pgrep -f 'llama-server'` 當閘門 ---- 會命中別人的包裝指令行
   （`bash -c ... pgrep -f 'llama-server' ...`）而誤報；用二進位路徑 `build/bin/llama-server` 或 `pgrep -x`。

9. **★★ 2026-09-19 晚：F 的組成量出來了 —— **70% 是 GPU `union`**，25 t/s 是「GPU 工作」問題，不是啟動稅問題。**
   量法：`scripts/check/sntok_curve.py` 在**單一臂**內（r0 ＝ MTP off、ntok=1）用**逐層中位數**
   拆開 —— 不走跨臂梯子（跨臂被證明不可行，見下）。
   `F = 71.1 ms/step = 1.81 ms/layer`（40 層；77.3 ms/token）
   | 桶 | 合計 ms | 每層 | 佔比 |
   |---|---:|---:|---:|
   | `union`（GPU span，**誠實的那個**） | 50.5 | 1.26 | **70%** |
   | `gap`（segment 之間 GPU idle） | 14.5 | 0.36 | 20% |
   | `cb`（專家填充） | 4.2 | 0.10 | 6% |
   | `submit`（segment 派發） | 3.0 | 0.08 | 4% |
   ⇒ **可當 overhead 的至多 30%。把非 GPU 的毫秒全部歸零，F 仍是 1.26 ms/layer ＝ 目標的 2.5×。**
   ⇒ **25 t/s 是關於 GPU 執行（attention ＋ MoE 數學）的陳述**，不是 dispatch／prefetch／cache fill。
   ⇒ **GDN 層是貴的**：比 10 個 full-attn 層多 **+0.48 ms/layer**（union），30 個 GDN ⇒ 14 ms/step
     ＝ F 的 20%；要砍 52 ms/step ⇒ **需要 3.6 個這種量級的缺口**。單一「GDN 修好」只是零頭。
   ⇒ **暖填充住在 L0–L4**：ntok=4 warm 時 92% 的 `cb`（8.92/9.75 ms）在 L0–L4，L5+ 只 0.83；
     冷的時候是散開的（L0–L4 10.40、L5+ 21.95）。**步總量看不到這件事。**
   ⚠ 儀器限制（要跟數字一起引）：`gpu` **重複計數重疊的 buffer**（可超過 `wait`）
     ⇒ **只能引 `union`**；四個桶**不嚴格分割步驟**（closure 75–121%）⇒ 百分比帶著這個寬度；
     沒有 per-node timing ⇒ **attention vs MoE 無法分開**，GDN/full-attn 的差只是下界。
   ⇒ 附帶：**跨臂比較在這台機器上已被證明不可行**（pass 2 的 4 個同配置 ntok=4 錨點散
     **97.5%**，且全程 **0 次** `window lost` ⇒ 温度之外還有 **ambient** 扰動：Spotlight 大量建索、
     TimeMachine、GUI 負載 ⇒ **「等安靜窗口」不會消除它**）。可行的只剩**單臂內、逐層、看中位數**。

10. **★★ 2026-09-19 晚：per-node 儀器本來就在樹裡 —— 別重造。**

11. **★★ 2026-09-22：動了 `src/` 裡的引擎原始碼之後，所有走 `decode_window_harness` 的探針
   會「拒絕量測」，而不是給你錯數字 —— 這是設計，不是 bug。**

   那道閘門是 `decode_window_harness.py:ANCHOR`：它比對目前 binary 的 md5 與「上次 **M1/M2/M3 oracle
   gate** 通過的那顆 binary」。加一個檔案（哪怕是只加一行 log）就讓 libllama 換 md5 ⇒ 所有經手的
   探針（`gdn_split.py`、`window_sentinel` 家族、任何 import `decode_window_harness` 的工具）在
   開場就印

   ```
   anchor: engine differs from the M1/M2/M3 anchor: {...} -> nothing may be concluded; refusing to measure
   ```

   而且**退出碼正常、看起來像「跑完了」**。遇到它就這樣處理，**不要用 `PROBE_ANCHOR=none` 繞過去**
   （那個開關是給「明知道自己在跑新 build 對照組」用的，開了就是自己放棄可比性）：

   1. `python3 scripts/check/m123_oracle_gate.py --tag <你的題目> --dump /tmp/<tag>.jsonl`
      （約 30 s warm，會自己 server + probe 48 token + 比對 logits）；
   2. 看三行：**M1 逐位元、M2 argmax、M3 top-k** 必須都 PASS，summary 會寫進
      `Backup/m123_oracle_gate/summary_<tag>.json`；
   3. 通過後才把 `decode_window_harness.ANCHOR` 的四個 md5（用同一支 md5、`[:16]`）更新成目前 binary，
      並在註解裡寫「哪個 summary 證明它是對的、tree hash 多少」，一行都別省：
      少了證明是哪個 summary 那一句，下一輪就沒有人敢信這個 anchor。

   附帶兩句定則：
   - **先改完再 build 再 gate。** 順序反了（gate 了舊 binary）等於白跑，而且看起來像有證據。
   - **`build/bin/` 裡躺著很多 `libllama.0.0.<n>.dylib`**（SOVERSION 是算出來的，會隨 tree 變），
     但是 `libllama.0.dylib` 這種**不帶版本號的 symlink 永遠指向最近一次 build**。anchor 走的就是它
     ⇒ 別把 build dir 當成自己的：同在一台機器上的另一條 session 重建一次，你的 anchor 就紅了
     （這正是它存在的理由）。

12. **★★ 2026-09-22：GDN（Gated Delta Net）已經是 Metal 上的 fused op，別再去「發明 chunked scan」。**

   `ggml_gated_delta_net` 在 Metal 上有專屬 pipeline `kernel_gated_delta_net_<type>_<nsg>`
   （`ggml-metal-device.cpp:638`），三個常數 `ne20/ne30/K` 走 **function constant**；K 是「狀態
   快照數」，所以 T=1 的 AR 與 T>1 捲起來的 recurrence **同一顆 kernel 就吃掉**（K=1 只留最終狀態）。
   樹裡三種實作都在：`build_delta_net_fused`（op）/ `build_delta_net_autoregressive`（T=1 手寫）/
   `build_delta_net_chunking`（T>1 手寫），派工在 `models/delta-net-base.cpp:430-451`，由
   `cparams.fused_gdn_ar` / `fused_gdn_ch` 決定。這個模型（qwen35moe，30 層 GDN + 10 層 full attention）
   今天印的就是 `fused_ar=1 fused_ch=1`。
   ⇒ 所以 axis D 能做的是 **ablation（把 fused 關掉換手寫圖），不是加速**，而且那種 run **不在
   bit-identity anchor 的保護範圍內** —— 它的 t/s 不能拿來跟任何錨定過的數字比。

   引擎側既有（全在 `run_server.sh` allowlist）：`CGC_DECODE_PROFILE=1`（**前置**）、
   `CGC_DECODE_PROFILE_ALL=1`、`CGC_GPU_TIMING=1`、**`CGC_GPU_NODES=1`（per-KIND 表）**、
   `CGC_GPU_OPS=1`（按 ggml OP 的第二張表）、`CGC_GPU_NODES_TRACE=1`、
   `CGC_GPU_NODES_MATRIX=1`（每個 command buffer 一行，`CGC-NSM`）。
   讀它的工具：`scripts/check/attn_moe_split.py`（`run` ／ `analyze <log>` ／ `selftest`）。
   實測（prod25、MTP on、`-n 128`；**只取 `kinds=44` 的 40 層表**，中位數佔該步 `seg_busy`）：
   | 桶 | `wcntw`% |
   |---|---:|
   | `attention` | 6.2 |
   | **`moe`（`ffn_moe_*` ＋ `shared_expert_gate`）** | **21.3** |
   | `gdn` | 11.6 |
   | `ffn_dense`（`ffn_*`） | 6.6 |
   | **`other`** | **34.2** |
   單項前二：**`node` 13.1、`cache` 8.1**（`ffn_moe_` 只有 **7.7**）。
   ⇒ ① MoE 家族（27.9%）約是 attention 家族（17.8%）的 **1.6×**。
   ⇒ ② **最大的可攻擊目標是 `node`／`cache` 這類管線葉子，不是 expert GEMV**。
   ⇒ ③ 引擎自檢 **`delta = 0.000%`**（`seg_busy` == `layer gpu_sum`，節點→buffer 映射自洽），
     **但 `lb` 幾乎全 0、`ub` 大量重疊 ⇒ [lb, ub] 分不開任何兩桶**；
     只有 `wcntw` 能排序，而它是「buffer 時長分給會 encode 的 node」這個 **歸因模型**，
     **不是獨立量測**。named-work 佔 80%、残差 20% 未歸屬。
   ★ **陷阱（我踩了）：分組要按「列數」。** 引擎**每個在該步出現過的 kind 印一列**
     ⇒ 列數就是形狀簽名。用「有沒有 `ffn_moe/attn/gdn/conv`」判形狀會**全部誤判**
     （MTP 層自己就是 GDN 層，帶 `conv`／`gdn_out`）⇒ 聚合把 trunk 種類稀釋 **2.2×**，
     讀出 `attention 0.0% / moe 2.5%`。實測形狀：38 張表 = **17×44 列**（40 層）＋ **21×7 列**（單一 MTP 層）。
   ★★ **op 級（`CGC_GPU_OPS`，trunk 形狀 `nodes_all=4076`，中位 total 115.6 ms）—— 兩個 matmul 只有 ~19%：**
   `MUL_MAT` **15.1** ／ `ADD` **14.2** ／ `MUL` **11.1** ／ `RMS_NORM` 7.5 ／ `UNARY` 7.5 ／
   `CPY` 6.9 ／ `GET_ROWS` 5.1 ／ **`MUL_MAT_ID` 3.7** ／ `L2_NORM` 3.3 ／ `GATED_DELTA_NET` 2.8 ／
   `GLU` 2.5 ／ `SCALE` 1.9 ／ `SUM_ROWS`·`CLAMP`·`DIV` 1.2 ／ `CONCAT` 1.0 ／ `ROPE` 0.6 ／ `SSM_CONV` 0.5 ／ `SET_ROWS` 0.4 ／ `SOFT_MAX` 0.4
   ⇒ **`MUL_MAT : MUL_MAT_ID = 4.1×` ，而每 token 位元組比＝稠密 1.45 GiB : 專家 347 MiB ＝ 4.2×**
     ⇒ 兩個 matmul 都是**純頻寬串流**，比例完全由位元組解釋。
   ⇒ **錢在 elementwise（`ADD`＋`MUL`＋`UNARY`＋`SCALE` ≈ **34.7%**）＋ norm ＋ copy/cache，不在 expert GEMV。**
   ⚠ 這與 §16 的「25 t/s 是 attention ＋ MoE 數學問題」**不同調**：§16 引的是 `union`（GPU **span**）佔 70%，
     op 級是**歸因模型**的佔比 —— 兩者不可互換。
   ★ **`VIEW` 962 個節點（23.6%）＋ `RESHAPE` 595（14.6%）＋ `TRANSPOSE`/`PERMUTE` 各 30 ⇒ 0% GPU**；
     **40% 的節點不產生任何工作**（`nodes_all 4076` vs `nodes_work 2455`）。
   ★ **身分：`node` ＝ 沒有名字的節點**（`ggml.c:7192` 自動 `node_%d`；單一最大 kind **13.1%**）；
     **`cache` ＝ KV cache ＋ GDN 遞歸狀態 cache**（`cache_k/v_l*`、`cache_r/s_l*`）—— **不是專家池**
     （`llama-expert-cache.cpp` 對節點 `format_name`／`set_name` **零命中**）。kind 名來自 **67 條前綴表 `ns_fix[]`**
     （`ggml-backend.cpp:2175-2231`，最長前綴命中，否則 `(other)`）⇒ **那份清單就是解碼環**。
   ★ **`lb` 為何永遠不能排序（機制）**：NSM（每個 command buffer 一行）44,135 個 buffer 裡，
     **沒有任何一個是前 40 個 kind 的 solo**（直方圖 `{1:1161, 2:3349, 3:895, 4:6901, 6:26187, 45:482, 64:5160}`；
     有 **26,187 個剛好 6 節點**、5,160 個是 64 節點）⇒ **`lb ≡ 0` 是構造性的**。
     ⇒ **只能引 `wcntw`，且要説它是一個模型**。
   ★ **形狀簽名在 op 表是 `nodes_all`，不是列數**（op 表每種形狀都印滿 27 列）
     —— kind 表的教訓**不轉移**。混算會得到 `CPY` 佔 466% 節點的荒謬值。
     工具：`attn_moe_split.py ops <log>`（自測 27 項）。
   ★★ **實測結果（trunk 形狀＝44 列；prod25、MTP on、`-n 128`）**：
     **`node`（12.8% of step）究竟是什麼**：**MUL 26% ＋ UNARY 26% ＋ GET_ROWS 21% ＋ MUL_MAT 14% ＋ ADD 7% ＋ FLASH_ATTN_EXT 5%**
     ⇒ **未命名節點 ＝ elementwise（MUL＋UNARY＋ADD ≈ **59%** of node）＋ row-gather（GET_ROWS 21%）＋ 未命名的稠密 MUL_MAT 14%**。
     ⇒ **不是 attention、不是 MoE 數學** —— §EN-229 的第二個獨立確認。
     **`ffn_moe_` 8.0%** ＝ ADD/MUL/DIV/SUM_ROWS/GET_ROWS/CLAMP **各 14%**（combine 管線，**不是 gather**）。
     **`cache` 8.0%** ＝ **CPY 74% ＋ SCALE 22% ＋ SET_ROWS 4%** ⇒ **KV／GDN 狀態 cache 的成本是「複製」，不是算術**。
     `ffn_moe_add` 6.8（ADD 100）、`norm` 6.5（RMS_NORM 100）、`conv` 2.6（CONCAT 35/GET_ROWS 35/SSM_CONV 15/UNARY 15）、
     `k_conv` 2.3（L2_NORM 100）、`z-` 2.3（MUL_MAT 100）、`gdn_out` 2.3（GATED_DELTA_NET 100）。
   ★ **強制交叉驗算（我第一版就是這樣被抓到的）**：把 GPUOPK 該 kind 各列相加，
     去對 kind 表**同一 block** 的 `wcntw` 值 —— **比值必須 1.000**。
     ★ 隨之而來的規則：**新增的 per-step 累加器要跟其他累加器一起在每步結尾歸零**（`ggml-backend.cpp`
     約 `:2822-2855`），**不要放在「印」的區塊裡**（它 1-in-8 才觸發）。
     我把歸零放在印的區塊 ⇒ 累了 **8 步** 對 **1 步**的 `ns_total` ⇒ `node` 讀出 **177×** 偏大（修後 22 個 block 全 1.000）。
   ★ 第三度踩形狀陷阱：我新寫的 GPUOPK 行**沒帶形狀** ⇒ 混算讀出 `node 75.3% of step`。
     修法不必改 C++：**讓每列繼承它所屬 `CGC-GPUNODE` block 的「kind 列數」**。
     ⇒ **通則：任何 per-step 儀器行都必須能歸屬到它所屬的步**，否則跨形狀聚合會產生「>100% of step」。
   ★★ **`cache` 的 CPY 指名了（2026-09-19 M8）**：kind `cache`（8.0–8.2% of trunk step）＝
     **CPY 74–75% ＋ SCALE 21–22% ＋ SET_ROWS 4%**。两次獨立 run 重現。
     CPY 的來源：**`build_rwkv_token_shift_store`（`llama-graph.cpp:4077`）逐 GDN 層對 `cache_r_l<il>`
     做 `ggml_cpy`**（TRACE 原始名直接看到 `cache_r_l30` 落在 `ffn_moe_*-29` 的 64-node 主 buffer 裡）。
     大小＝`n_embd_r()`＝`(d_conv−1)·(d_inner ＋ 2·n_group·d_state)`；本模型 **96 KiB／層 ⇒ **2.9 MiB／步**。
     ⇒ **2.9 MiB 卻值 ~6% 的步 ＝ 0.36 GB/s，比 120 GB/s 低 ~330× ⇒ overhead-bound
     （30 次序列化小 copy，~265 µs/copy），不是頻寬。**
     ⚠ **`delta-net-base.cpp:509-526` 的 rollback 迴圈（`K = n_rs_seq+1` 次 cpy）是另一組、而且沒有名字**
       ⇒ 它們落在 kind **`node`**，不是 `cache`。KV cache 也不是它 —— KV 是 `SET_ROWS` 那 4%。
     ⚠ 佔比是**模型**（`wcntw` 把 buffer 時長分給會 encode 的節點）；NSM 分不開
       （`cache` 出現在 **75% 的 buffer**、涵蓋 90.6% 的 buffer 時間、**從不 solo**）。
       ⇒ 報它時要分三層說：**身分確定（TRACE）、大小精確（幾何）、成本佔比是模型**。
   ★ **KIND × OP（新增儀器，2026-09-19）**：`node` 是 **13.1% 的未命名節點**，而三張既有表都
     答不了「它是哪些 op」（kind 表按**名字**、op 表按 **op**、TRACE 印的就是 `node_<i>`）。
     → **名字是標籤，op 才是工作**。實作在 `ggml-backend.cpp`（5 處插入、+48 行，
     `Backup/patch_kind_op_xref.py` 可重放），輸出 **`CGC-GPUOPK: <kind> <op> <ms> <% of kind> \| <% of step>`**，
     **只在 `CGC_GPU_OPS=1` 下印**（它本身已要求 `CGC_GPU_NODES=1`）⇒ 預設路徑不受影響。
     分配用**與 kind 欄相同的分母**（每個工作節點 `dur / wtot`）⇒ **同一 kind 的 op 列相加＝它的 `wcntw`**，
     是**細化**不是另一個模型。讀它：`attn_moe_split.py ops <log>` 的 `KIND x OP` 區塊。
   ★ **在別人正在量測、不能 build 時，怎麼驗證共享樹裡的 C++ 改動（不寫任何產物）**：
     從 `src/llama.cpp/build/compile_commands.json` 取該 TU 的完整編譯命令 → regex 去掉 `-o <obj>` 與 `-c`
     → 接上 **`-fsyntax-only`** → 以該 entry 的 `directory` 為 cwd 執行。**rc=0 且零診斷 ⇒ 編得過。**
     ⚠ **改動活在共享工作樹裡，別條線隨時可能編譯它** —— 不驗就等於把未爆的編譯錯誤
     放進別人的 build。而 `pgrep -x` 閨門會（且應該）擋下自己的 build。

   ★★ **引用指紋之前先指名是哪一套 digest —— 本 repo 同時有兩套，數字不可能相等**
     （2026-09-19 發現；本線當日曾把兩者當同一組比對而誤判「逐位相同」）：

     | 來源 | 範例 |
     |---|---|
     | `scripts/check/engine_freeze.py` | sha256 前 24 hex：`libggml-base 8cb4a6b74b9cc2f5433b7738` |
     | 線 B 的 harness 報告頁首 ╱ `m123_oracle_gate` 的 `build` 行 | 另一套：`libggml-base 054fb22f04a01c5c` |

     ⇒ 兩套各自同源（harness ↔ oracle gate 可互比），**但跨套比對等於比兩個不同的函數**。
     → **歸因的判準是指紋，不是 `git status` 乾淨**（本 repo 把 build 產物納入版控
     ⇒ rebuild 之後 `git status` 本來就會髒）。每一臂前後各跑一次
     `python3 scripts/check/engine_freeze.py verify --tag <tag>`，並貼 `flags`／`artifacts`／`source` 三段。
     ⚠ `Backup/engine_freeze/` **被 gitignore ⇒ 那些 tag 是本機記錄**，交接時要指明檔名。
     ⚠ 實測（2026-09-19 19:2x 對 `pre_plainmatch_0919`）：**唯一漂移的產物是 `libggml-base`**
     （KIND×OP 儀器）；`llama-server`／`libllama`／`libggml-metal` 全部 MATCH。
   ★ **共享 `src/` 的擁有權與歸因協定寫在 `docs/SHARED_SRC_OWNERSHIP_2026-09-19.md`**：
     目前 `ggml-backend.cpp` 已提交（`4fdfaa8de`）並轉移給 overlap 那條線；本線不再編輯它。
     要重放它的儀器：`Backup/patch_kind_op_xref.py`（5 個錨點，各自恰好 1 次）。
     ⚠「還原源碼」比「提交」更糟：**被追蹤的 dylib 仍含該儀器**
     ⇒ 原碼說 A、binary 說 B，正是閘門檢查 8 存在的理由。
   ★★ **逐層表（`CGC-DECPROF all: L<il>`）的欄位語意 —— 它是目前唯一能回答「這一層 GPU 在忙還是在等」的儀器**
     （`attn_moe_split.py layers <log>`；語意讀自 `ggml-backend.cpp` 的 `dp_lay_*`／`sg_*`）：
     - **可加的只有三項**：`wait`（CPU 等前一段 GPU）＋ `cb`（top-k hook：槽管理＋阻塞填充）＋ `submit`（派發）
       ＝ 步級行的 `total`。實測對帳：step=1 印 `cb=476.95`，逐層相加 **476.9** ⇒
       **逐層相加就是步級真值**。
     - `gpu`＝segment 內各 buffer 忙時**加總**，重疊者**重複計數** ⇒ 可以 > `union`，**永不可加**。
     - `union`＝segment 的 GPU **跨距**（span）⇒ **span 內的空檔看不見**。
     - `gap`＝**段間** GPU 空檔（前一段 GPU 結束 → 本段 GPU 開始）⇒ 這是這張表唯一能指名的空檔。
     - ★ **`ggml-backend.cpp:2130` 自己寫了內建交叉校驗**：該視窗坐在**前一段的 hook＋submit** 裡
       ⇒ **`gap` vs `cb+submit`**。實測：`gap/hook`＝**1.03–1.21**（全 run 穩定）、
       **`corr(Σgap, Σ(cb+submit))`＝1.000** ⇒ **它們是同一個區間的兩個視角**
       （CPU 在做槽管理＋阻塞填充的整段時間，GPU 沒有工作），不是兩個獨立量。
     - ★★ **逐層中位數 × 層數 ≠ 總和**（右偏分佈 ⇒ 乘起來系統性低估）。
       2026-09-19 就是這個差造出一個錯的「bottleneck 只有 7%」並且已交給別條線。
       規則：**要總和就相加，不要拿中位數乘次數**。
     - 家族判準：`FULL_ATTN = set(range(3, 40, 4))`（10 層），其餘 **30 層是 GDN** ——
       而 **GDN 就是擁有 `cache_r_l*` / `cache_s_l*` 的家族**（`build_rwkv_token_shift_store`，
       `llama-graph.cpp:4077`）⇒ **「GDN vs full-attn」就是「cache_r 的 cpy 有沒有變成空檔」**。
   ★★ **不要假設 `cb`（hook 窗）是「等填充」—— 用池自己的 `fill_wait_us` 對帳。**
     實測（2026-09-19，同一支 log）：**`fill_wait_us` 全程 只有 78 ms**，而 `cb` 是 **36.5 ms/步**
     （暖半段中位）；若 cb 是阻塞等填充，光暖半段就需 ≥2374 ms
     ⇒ **實際只夠 2.13 個步的 cb** ⇒ **填充早已完全被藏住**（`pread` 5378 s 在 IO 執行緒上跑）。
     ⚠ **預取的 drop 統計不等於「有人在等」**：`prefetch=482/190` 且 **190/190 全部 `drain_cleared`**
     （發起 482、完成 190、完成的全被丟），**但沒有人因此等待** ⇒ **不要拿它當「修預取就有收益」的證據**。
     ★ 那 `cb` 到底是什麽？先看**它集中在哪幾層**：實測是 **L0–L5**（每層 1.6–4.2 ms，
     其餘層平底 ≈0.2–0.4；**top4 = 42%%、top8 = 59%%**），而池自己的 dump 該幾層正是
     `layers_distinct_over_slots=5`、`worst=layer 1 distinct=217 slots=143`（**1.5× 超額**）
     ⇒ **hook 窗＝那幾層的槽位／驅逐簿記的 CPU 成本**（最一致的解釋，未證）。
     反面判據：**步內 `corr(cb, union)` 跨層 = −0.085**（若 cb 是等該層自己的 GPU，
     它應該同步）⇒ **不是 per-layer 同步/readback**。
     ⇒ 實測結語：**逐層空檔不是故事**（兩家族 `gpu/union`≥1，span 內塞滿；
     GDN 的 gap 1.09 vs attn 1.00 —— 沒有因為 cache_r 而多出空檔）；
     **真正的大空檔在步層級的 hook 窗（約 28–32% of step）**。


## 儀器清單（由粗到細）

| env | 輸出 | 作用 |
|---|---|---|
| `CGC_M2_PROFILE=1` | `CGC-M2-FILL` / `CGC-M2-PROF` / teardown `read shape` | 每個 (layer,kind) 的 pool vs disk 位元組、`us/job`、`effective_rate` |
| `CGC_PHASE_TIMING=1` | `CGC-PHASE` | build/alloc/inputs/compute/fill_wait/gpu（每 32 步一行） |
| `CGC_DECODE_PROFILE=1`（`+ALL`） | `CGC-DECPROF` | 每步 wait/cb/submit 三分 + 逐層歸因（top-8 或全部） |
| `CGC_GPU_TIMING=1` | `CGC-GPUTIME` | **GPU 自己的時鐘**：wait / gpu_busy_sum / gpu_union / gap |
| `CGC_GPU_NODES=1`（＋`CGC_GPU_TIMING=1`＋`CGC_DECODE_PROFILE=1`） | `CGC-GPUNODE` | **節點範圍級的 GPU 時間**（2026-09-18 新增）。把每個 command buffer 的 `GPUStartTime/GPUEndTime` **按它編的節點範圍**攤到節點種類上 ⇒ 比逐層細一級。**不需要 `MTLCounterSampleBuffer`**（見下方註）。自我檢查：該行的 `seg_busy` 必須等於同一行的 `layer gpu_sum`（兩條路徑加總同一批時間），`delta` 不為 0 就代表範圍或 buffer↔節點的對應錯了。**`CGC_GPU_TIMING=1` 是必要的**：`dp_lay_gpu[]` 只在它開著時才填，否則分母是 0 而 `delta=0.00%` 是空轉。**2026-09-18 新增三欄**：`wcntw`（只按**會編碼**的節點分攤，見下方「兩張表」）、`*bywork`（按 `wcntw` 排序的前 10 名）、`work-attributed … / residual …`（可主張質量的標頭行） |
| `CGC_GPU_OPS=1`（＋`CGC_GPU_NODES=1`） | `CGC-GPUOPS` | **以 ggml op 為鍵**的第二張表（2026-09-18）。回答名字表答不了的「**這個 op 值不值得動**」：`wcntw` 是 work-weighted 份額、`cntw` 是節點數份額、`ub` 上界、`uni` 只在「整格同一個 op」時才是精確的每節點成本。**只有五個 op 是 no-op**（`NONE/RESHAPE/VIEW/TRANSPOSE/PERMUTE`，`ggml-metal-ops.cpp:242-252` 逐字 `// noop -> next node`）⇒ 它們的 `wcntw` **按建構為 0**，這是證明不是量測 |
| `CGC_CB_N_MAIN=<n>` / `CGC_SERVER_N_CB=<n>` | `n_cb = N` 那行 | **切細 command buffer 的兩個旋鈕**（2026-09-18）。預設 `n_main = MAX(64, 0.1·n_nodes)`（`ggml-metal-context.m:1099`）⇒ **每個 segment 的主執行緒 buffer 永遠吃 ≥64 個節點**，任何住在裡面的桶都被除以 ≥64（這是 `ffn_moe_gate` 的 `cntw` 只有 0.7% 的原因）。`CGC_CB_N_MAIN=1` 免費（cb 數不變）；`CGC_SERVER_N_CB=16` 要付 17 顆 buffer/segment。⚠️ **`n_cb ≥ 64` 在載入期死鎖**（Metal 的在途 command buffer 配額；症狀只有 `/health` 回 `Loading model` ＋ `server never became ready`，與「載入慢」同形，lesson `eng-diag-0035`）。實測上限在 **(64, 129]** 之間 |
| `CGC_SUBMIT_AHEAD=1` | 無（改變順序） | 天花板上界探針。**★ 2026-09-19 分欄位更正**：**步時上界有效且重現（×1.702，與認證的 ×1.711 差 0.5%）**，但 `gap/union`（`(NO TIMESTAMPS)`，結構性）與 **t/s**（acceptance 崩掉 2.40→1.00）都不可讀 ⇒ 兩臂圖寬必須相同才可比，見陷阱 28 |
| `CGC_SLOT_TABLE_GPU=1` | 見 `CGC_S1_DBG` | S1：把 expert→slot 查表搬進圖（`slots = get_rows(table, selected_experts)`），移除每層 host 寫 leaf 的往返 |
| `CGC_S1_MIN_IL=<n>` | — | 只有 layer ≥ n 用 GPU 表（預設 1）。**layer 0 留在 host**：它的 FFN 讀全寬張量 + **原始 expert id**（程式為它寫 **IDENTITY 表**），而 GPU 算出的 ids 需要跨 backend 拷貝（20:18 那次 `libggml-cpu` SIGSEGV 的形狀）。**★ 2026-09-16 更正**：舊理由「它不被池化」已失效——layer 0 自 2026-09-16 起**是池化層**（見陷阱 14/17）。所以這條限制現在只靠跨 backend 拷貝那一半支撐，**能不能下調 `MIN_IL` 未測**；要動就自己跑閘門。**前綴閘的限制**：每個臂都是連續後綴 ⇒ 左界與服務層數同步移動 ⇒ 無法區分「某一層壞」與「服務層數 ≥ N 就壞」；再加同形狀的臂沒有用 |
| `CGC_S1_KEEP_LEAF=1` | 見 `CGC_S1_DBG` 的 `POST ... same=` | **對照臂**：建所有 S1 節點**且**建 host leaf，但讓 `mul_mat_id` 消費 **leaf**。用來切開「GPU 算出的 ids 是錯的」與「多出這些節點本身就會動答案」 |
| `CGC_S1_DBG=1` | `CGC-S1: graph/CAPTURE/POST/EXPECT-*/EQUIV-*` | 打開 S1 的三個 host 側探針（`POST` = 同步後讀回 GPU 產物；`EXPECT` = hook 當下印 `table[ids]`；`EQUIV` = 發表的表 vs `slot_table_safe` 逐專家比對） |
| ~~`CGC_S1_IDENT=1` / `CGC_S1_TAG=1`~~ | — | **已於 2026-09-15 從程式碼移除**（故意寫錯表的分支）。同輪也被移除的臨時診斷：`CGC-S1: CAPTURE`、`CGC-FAST:`、兩處 identity map 的 tag 分支。**`CGC_S1_DBG` 保留為 opt-in。** |
| `CGC_S1_OUT_CAP=1` / `=pre` | `CGC-S1: OUT`（整張 `fnv1a64`）、`CGC-S1: OUTSET`（逐專家列多重集雜湊） | **`ffn_moe_down` 的輸出捕捉**。`=1` 抓 decode（含 graph 序號），`=pre` 抓 prefill。用 `ggml_set_output` 釘住 ⇒ **診斷臂專用、閘門臂永不可開**；成本是「每 graph 釘幾個張量」（見陷阱 15） |
| `CGC_S1_OUT_LAYERS=<spec>` | — | `CGC_S1_OUT_CAP=pre` 時要釘的層（預設 `"0"`；支援 `0-3` 與 `0,4,8,...`）。**層窗是硬需求**：prefill 全 40 層會 fail-stop |
| `CGC_S1_TABLE_CHURN=1` | teardown `S1 slot-table: publishes=… consumed_changed=…`、逐 graph `TABLE-CHURN` | `publish_slot_table` 的發布量與**被消費子集**的變動。**不開時 teardown 會明印 `(consumed-subset churn not instrumented: ...)`**——預設 0 與量到的 0 必須長得不一樣 |
| `CGC_S1_CLAMP_ABORT=1` | `GGML_ABORT` | 對「被消費 id 被 clamp」升為**硬前置條件**（§8.3 的 tripwire，不是修法） |
| `CGC_IDS_CAPTURE=1` | `CGC-IDS-CAP`（dump 於 `ggml_metal_synchronize` 尾端） | **內核側的 ids 讀數**：`mul_mat_id` 的 Metal kernel **實際解碼出的 ids**。不做任何 host 推論，是唯一能同時切開「時序」與「allocator 別名」的儀器。4096 slots、stride 8；dump 用**游標**只印未印過的 slot（分段 dispatcher 會多次 sync）。**注意 stride=8 ⇒ `n_ids=64` 時只看得到 token 0**，看尾部要非零 `n_skip` |
| `CGC_TENSOR_CAPTURE=<精確節點名>`（＋`CGC_TENSOR_CAPTURE_WORDS`，預設 32 上限 32） | `CGC-IDS-CAP path=DST name=<節點>.dst` | **輸出張量**的快照（2026-09-16 新增）。重用 `CGC_IDS_CAPTURE` 的同一個內核（那個內核與 ids 無關，就是「把 stride 個 int32 抄進 slot」）⇒ `.metal`／`impl.h`／`device.*`／`context.m` 一行未改，比較器也原封不動。**必須與 `CGC_IDS_CAPTURE=1` 一起開**：ids 列是比較器切 graph 的依據，少了它們 dst 列會落進一個偽 graph 而被報成 IDENTICAL（假陰性）。**只掛在 `ggml_metal_op_mul_mat_id`** ⇒ 只能擷取 gate／up／down 的輸出。名稱**精確比對**（`ffn_moe_down-1` 是 `-10..-19` 的子字串）。★ **需要一個 `ggml_metal_encoder_memory_barrier`**：ids 運算元的生產者在很多節點之前（已沉降），dst 的生產者是**緊鄰的前一個 kernel**，而這個 fork 支援內核併發。沒有屏障時讀數**不可重現** |
| ~~`CGC_S1_OUT_CAP=1`／`=pre`~~（**既有，較舊**） | `CGC-S1: OUT`（`fnv1a64` ＋ `vals=[...]`）、`OUTSET` | **同一件事的舊版**：釘住 `ffn_moe_down` 的輸出。差別有兩點——(1) 它用 `ggml_set_output` **釘住張量**，而那會動 allocator／graph（原始碼自己寫「診斷臂專用、閘門臂永不可開」）；(2) 它印**雜湊**加少量值。⇒ **要「不擾動圖」的讀數用 `CGC_TENSOR_CAPTURE`，要層窗與雜湊用這一支。** ⚠️ 2026-09-16 交叉確認**未成立**：`CGC_S1_OUT_LAYERS=1` 跑出來的列是 `il=27` 而不是 layer 1，而 480/480 全不同 ⇒ **它的參數語意尚未弄清，不要拿它當獨立確認** |
| `GGML_SCHED_DEBUG=2` | per-node 後端 + `GET_CAUSE` | **唯一**能回答「這個 node 落在哪個 backend、它的 src 從哪來」的儀器（=1 只有 `## SPLIT`，且走 `GGML_LOG_DEBUG` 會被預設 verbosity 濾掉） |

**★ 節點級 GPU 時間是怎麼來的（2026-09-18；別再重推一次，也不要以為它需要 `MTLCounterSampleBuffer`）。**
`docs/M3_VERDICT_2026-09-17.md` 說「逐節點 GPU 時間是唯一活路、要用 Metal counter sample buffer、成本最高」
—— **活路對，成本判斷錯**。`ggml-metal-context.m:1304-1315` 的編碼回呼**本來就把圖切成固定範圍**：
主執行緒的 slot `n_cb` 編 `[0, n_nodes_0)`、worker `cb_idx < n_cb` 編
`[n_nodes_0 + cb_idx·p, …)`（`p = n_nodes_per_cb`），而 `n_cb / n_nodes_0 / n_nodes_per_cb`
在 sched 側讀取時**都還在** ⇒ **範圍可以直接導出，零新狀態、無抽樣點、無 barrier**。
既有的 `CGC_GPU_TIMING` **早就**逐 cb 讀 `[cb GPUStartTime]`／`[cb GPUEndTime` 的配對
（`ggml-metal-context.m:503-541`），只是把它們聚合成一個 segment 跨度（`union` 的定義在 `:497`）
⇒ **節點身分是在「聚合」那一步丟掉的，不是「取樣」那一步。** 實測指紋：
`bufs ÷ segs = 360 ÷ 40 = 9 = n_cb + 1`（env `CGC_N_CB=8`）⇒ 一層 9 個切片。
**★ 兩張表的關係、以及「份額不是粒度不變的」（2026-09-18；引用任何份額前先讀這裡）。**
- **名字表**（`CGC-GPUNODE`，按節點名前綴分桶）與 **op 表**（`CGC-GPUOPS`，按 ggml op 分桶）
  是**同一批 buffer 的兩種鍵**。它們的 `wcntw` 在**絕對值**上一致到 0.1%（實測 ms-ratio
  1.001／1.000：名字表 `ffn_moe_gate+up+down` vs op 表 `MUL_MAT_ID`）——**這是實作正確的證據，
  不是權重良置的證據**。⚠️ 兩表的**百分比不可比**：名字表除以 `ns_total`（每一個 buffer），
  op 表除以 `nsop_total`（有 ≥1 個可解析 op 的 buffer）。
- **權重是什麼**：`dur(buffer) × (#該桶且會編碼的節點) / (#會編碼的節點)`。
  它修掉的是「**no-op 稀釋**」（`n_main ≥ 64` + 約 40% 的節點是 no-op ⇒ 一個桶可能被除以 64；
  實測 `ffn_moe_gate` 的節點數份額 0.7% 而上界 44.7%）。**它假設每個會編碼的節點等成本**，
  所以它是**共同出現（co-location）的排名、不是成本排名**。
- **⚠️ 份額不是粒度不變的**：同一 profile、同熱態，只改切片寬度（`en-work` 預設 vs
  `en-work-fine`＝`CB_N_MAIN=1`+`SERVER_N_CB=16`）⇒ `moe_gemv` 3.2%→6.3%（1.97×）、
  `dense_gemm` 2.2%→4.2%（1.91×）、`conv/ssm` 2.5%→0.7%（0.28×）；穩定的只有
  `node`／`ffn_moe_`／`norm`／`(other)`／`ffn_`（0.84–1.06×）。
  ⇒ **規則：只引用「家族總和」；逐項只在兩端差 <1.3× 時才講成排名，否則報 `[粗, 細]` 區間。**
  （lesson `eng-mh-0064`。熱態是**另一個軸**：NOMINAL→HEAVY 對 11 個家族是**均勻**的 0.81–0.88。）
- **`residual`（標頭行）**＝整格沒有任何有名工作節點的 buffer 質量 ⇒ 四臂實測 19.9–23.9%。
  **它不是熱態的量**（HEAVY 細臂 20.0% ＝ NOMINAL 粗臂），是**切片組成**的量。
- **兩個已證的識別陷阱**：(1) `ffn_moe_gate/up/down` **就是** MUL_MAT_ID（每桶 80 節點、只有這個 op），
  但 **`ffn_moe_topk` 是 VIEW**（`op=38`, `ne=[8,2]`）⇒ 它的 `wcntw` 恆為 0.00 **是正確的**；
  真正的選擇成本在 `ffn_moe_argsort`（ARGSORT）與裸名 `top_k`（TOP_K）。
  (2) `node`／`(other)` 的差距不是詞表缺陷：`node` 620 節點**全部會編碼、零 no-op**
  （ADD 270／MUL_MAT 130／GET_ROWS 90／MUL 60／GATED_DELTA_NET 30／FLASH_ATTN_EXT 10）、
  `ffn_moe_` 680 節點只有 280 會編碼、`cache` 660 只有 290。**所以前三名是排名，不是偽影。**
- **千萬不要**：把 `sum(wcntw)`（＝`seg_busy` 的 76–83%）當步時長。`seg_busy` 本身隨切片數
  膨脹（同一 workload：143／296／766 ms @ n_cb = 8／16／63）⇒ **絕對 `seg_busy` 不可引用**。
  分母請用**同一步**的 `wait`（步配對），不要拿兩個中位數相除。

**兩個已知限制（先讀再引用）**：(1) ~~詞彙表尚未調校~~ **2026-09-18 已解決**：詞表是 40 條固定前綴，
`ffn_moe_gate/up/down` 在表上；但**桶名仍是匹配規則的產物**（`ffn_moe_topk`＝VIEW 就是反例）
⇒ 要用 `CGC-GRPH` dump 或 op 表交叉確認成員（lesson `eng-lf-0008`）。
(2) ~~攤分是按節點數加權~~ **2026-09-18 已由 `wcntw` 取代**（只按會編碼的節點分攤）；
但 `wcntw` **不是粒度不變的**（見上）⇒ 只引用家族總和。
實作上的坑：`ggml_metal_cgc_node_name()` 讀的是**快照**（在 `graph_compute` 內複製的節點名指標）——
`ctx->gf` 是**呼叫者**的 cgraph，而分段 dispatcher 把它建在區域變數上（`submit_seg` 的
`struct ggml_cgraph gv = seg_view(s);`）⇒ 呼叫一返回就懸空（lesson `eng-diag-0034`；症狀是載入期
SIGSEGV 11，而既有路徑完全正常）。**這支是全檔唯一在「呼叫之後」讀 `ctx->gf` 的讀者。**

**★ 不要為了 L0 去動 `CGC_S1_MIN_IL`（2026-09-18 實測；這是第二個理由）。**
上一列已經說了「layer 0 自 2026-09-16 起是池化層」，另外兩點：`CGC_S1_MIN_IL` **只在
`CGC_SLOT_TABLE_GPU` 存在時才被讀**，而 `run_server.sh:1518` 只在顯式給值時才傳它
⇒ **生產沒開 S1，它在生產上是 no-op**；而且 `decode_sweep.py:410-412` 把 L0 明文列為永久排除項
（它的 MoE 跑 CPU/BLAS，跨 backend 拷貝就是 20:18 SIGSEGV 的形狀）。**更重要的**：
「L0 的 GPU 時間是中位的 6~7 倍」**只在 llama-bench `-d 512` ＋ MTP env 開但沒有真的投機**那個
regime 成立（`llama_bench_matrix.forward_argv()` 不轉發 `--spec-type` ⇒ 它的 decode step `ntok` 恆為 1）。
在真的 decode 裡（`MTP off` 與 `MTP on+投機` 兩臂、各 42／137 個 steady step）L0 的 gpu 是
**0.46×／1.6×**，而它的 `union` **低於中位** ⇒ **L0 沒有可重現的異常，不要去追它。**
（`cb` 的前四名兩臂都是 L2/L1/L0/L3 —— 那是**池填充由最前面幾層付錢**的既有模式，不是 L0 的性質。）

**★ `read shape: … effective_rate=` 不是裝置速率（2026-09-17 22:4x 實測）。** 那一行是 `bytes/jobs/us_job`
的**導出值**，而 `us/job` 含 **fill worker 的排隊／鎖／CPU 調度**，不是 SSD 延遲：同一形狀
（散佈 0.17 MiB）**裝置實測每個 job 只要 0.24 ms（p90 0.34）**，而 log 上同一輪寫的是
**`us/job=19406`（19.4 ms）＝ 80×**。它的算術也自相矛盾：`jobs × us/job`（26883 × 19.4 ms = **521.7 s**）
**大於**該 cell 的 wall（**89.4 s**），因為 8 個 worker 並行（`pread_usec` 也正好是 521.7 s）。
⇒ **不要用 `effective_rate` 或「非居民 share」推論 IO 瓶頸。** 要判 IO，折算**每 token 的磁碟位元組**
（該輪 4.54 GiB / 512 token = **9.1 MiB/token**）× **裝置實測速率**（散佈 **639 MiB/s**、循序 **2.26 GiB/s**）
＝ **14 ms/token**，對比 209 ms/token 的預算 ⇒ **約 7%**；而同一輪 `prefill-house` 同樣 44.5% 非居民卻跑
**254 t/s**，是這個假設的直接反例。量法：`Backup/cgc_logs/en_io_probe_20260917.py`（唯讀，選沒被載入過的
大檔以免 page cache 假象；重讀若快一個數量級就代表前兩項不是裝置速率）。**`fill_wait=0` 是對的**：
消費者沒有在等 —— 這不等於 IO 不在成本裡，但這個案例裡它真的不在。

**★ `CGC-SEG` 怎麼讀（2026-09-17 用它把一個 209 ms/token 的帳拆平）。** 那裡印的是**每段平均**
（`ggml-backend.cpp:1848-1855` 註解原文）：分段迴圈把 **GPU 層 i → CPU top-k hook → submit 層 i+1**
序列化 ⇒ **step 的 wall = Σ(wait+cb+submit) over layers**；每行平均 **160 段 ≈ 4 steps** ⇒ **1 段 = 1 層**，
括號裡的數字是**累計段數**（÷40 = 步數，可與 reps×n_gen 對帳 ⇒ 能分辨「有沒有把某個 pass 算進去」）。
`cb` 是「**下一層的 top-k hook：slot 管理 ＋ 阻塞式 pool 填充**」——**不是 GPU**；`submit` 是 CPU 編碼。
判讀用**中位數**（第一段含冷池，`cb` 可貴 3 倍）。實例：96 行 × median(3021/1003/248 µs) × 40 層
= **171 ms/token**，對上同輪實測中位 rep **178 ms**（差 4%）⇒ 帳是平的，**可以直接這樣引用**。
**但它拆不開 `wait`**：wait 只說「CPU 在等 GPU」，要知道 GPU 是忙是閒必須**同時**開 `CGC_GPU_TIMING`
（`gpu_busy_sum` / `gpu_union` / `gap`）。⚠️ 這個迴圈只在 `CGC_OA_ASYNC=1` 下可達；沒有它整個 graph 是一次
async submit，三個成分都不可分。

跑法：`/opt/homebrew/bin/python3 scripts/check/decode_sweep.py --profile prod25 --arms <臂>,<臂> --rounds 1 --warmup 1 --n-predict 120 --json Backup/phase_decomp/x.json`
（`--profile` 吃的是 `CGC_SERVER_PROFILE`（如 `prod25`），**不是**矩陣臂名 `prod25-stream`。）
閘門：`scripts/check/m123_oracle_gate.py`（M1/M2/M3 bit-identical + 最新 M2 oracle）。

## 匿名節點（`node_NN`）：先零成本歸屬，需要時才命名（2026-09-18）

`node_NN` 是 **ggml 計數器自動名**（⇒ 兩臂的圖不同 ⇒ **同名不同物、不可跨臂比對**）。
41 層的 trunk 圖裡有 **620 個**，在節點加權表裡佔 **15–29%** —— 是最大的一塊「真運算但沒有子系統名」。
2026-09-18 把它降到 **350**。方法是兩步，**順序重要**：先歸屬（免費），再決定要不要動原始碼。

### 步驟 1（免費，不必重建）：用 dump 的**索引順序** ＋ 左右最近的有名節點

`CGC_GRPH_DBG=1` 的 dump 逐字印
`CGC-GRPH[<idx>] name=<name> op=<n>(<OP>) ne=[<ne0>,<ne1>]`，而 **`idx` 就是圖建構的順序**
⇒ 對每個 `node_NN`，往左／往右各走到第一個**有名**的節點，那兩個名字就是它的上下文。
實測（一個 4116 節點的圖）乾淨得可直接解讀：

| op | 數 | 左鄰 → 右鄰 | 解讀 |
|---|---|---|---|
| `ADD` | **240**/270 | `ffn_moe_weighted` → `ffn_moe_out` | **MoE 專家輸出的歸約鏈** |
| `GET_ROWS` | 90 | `cache_r_l*` → `cache_s_l*` | **遞歸狀態的 gather**（★ 2026-09-18 更正：舊寫「池的 gather」是錯的，見下） |
| `MUL_MAT` | 130 | `alpha`/`beta`、`attn_norm` → `linear_attn_qkv_mixed` | 線性注意力投影 |
| `MUL` | 60 | `norm` → `final_output` | 輸出縮放 |
| `GATED_DELTA_NET`／`UNARY`／`FLASH_ATTN_EXT` | 30／30／10 | — | 線性注意力核心／遞歸狀態更新／10 個全注意力層 |

**★ 2026-09-18 更正：`cache` 桶不是「池自己的暫存」，是「遞歸／KV 狀態管線」。**
把 `cache` 桶成員的名字逐字印出來（新 dump，log `050331`）是
`cache_r_l*`（遞歸狀態，`llama-model.cpp:378-379` 的 `pattern_r_cache`）、`cache_s_l*`、
`cache_k_l*`／`cache_v_l*`（10 個全注意力層的 KV）—— **與專家池無關**。
池的填充在 **CPU 側的 hook**（計在 `CGC-SEG` 的 `cb`），**在 GPU 節點表裡沒有成本**
（與 `fill_wait = 0.000` 一致）。
⇒ 「`cache` ≥ 整個 MoE 專家 GEMV 家族」這句的**份額**成立、**歸屬**不成立；
而更正後結論更強：**線性注意力（GatedDeltaNet）家族 ≈35–45%，MoE 家族 ≈12–15%。**
另外它是**操作數／啟動開銷**受限而非頻寬（`cache` 真工作位元組 ≈ 每 token 十幾 MB ⇒
0.1–0.5 ms，量到 27 ms）：**120 個 CPY** 來自 `delta-net-base.cpp:509-526`，`n_rs_seq != 0`
時每層建 `K = n_rs_seq+1` 個獨立 `ggml_cpy`（30 層 × 4 = 120，與 dump 逐格吻合）。
（出處 `.workbuddy/memory/2026-09-18.md` §EN-132。）

**★ 同一輪的另一個陷阱：`cb` 的份額會被「細粒度臂自己」灌大。**
本 skill 曾記「decode 步 ≈80 ms；`wait` 83–90%、`cb` 7–15%、`submit` 4%」。用
`en-work-fine`（`CB_N_MAIN=1` + `SERVER_N_CB=16` ⇒ **每段 17 顆 command buffer**）量，
`cb` 會變成步的 **42–75%** —— 那是**那個臂自己造成的偽影**，不是生產 shape。
⇒ **要引用相位分解，先指名臂的 `n_main`／`n_cb`；`n_cb` 掃描在生產 shape 上已於 09-15
判死（1→16 無影響、`cb+submit` 僅 9%）。**

**★ 而這個偽影不只影響 `cb` —— 同一個臂會同時灌大 `gap` 與名字表的份額（09-18 13:3x 實測）。**
判別式是 `CGC-GPUTIME` 那行的 **`bufs`**：生產 shape **360**（`skipped=0`），細粒度臂 **約 640**
（`skipped=37–47`）。同一批 log 的對照（都取 `segs=41 layers=40` 的 decode 步）：

| 臂 | `cb` | `gap/union` |
|---|---|---|
| 生產形狀（`p25-mtp-on-diag`） | **17.5–24.0 ms** | **21–25%** |
| 細粒度（`en-work-fine`） | **38.5–112.0 ms** | **29–93%** |

⇒ **可引用的述句是「生產形狀下 `gap` ≈ 21–33%」**；`en-work-fine` 的 57%／93% **不是生產數字**。
⇒ 名字表的 `wcntw` 也是同一個原因：`ns_kind_wns[q] += dur * wcnt[q] / wtot`（`ggml-backend.cpp:2306`）
把每個 buffer 的 `dur` **平均分給該 buffer 內有工作的節點**，所以 **`bufs` 越小／每個 buffer 的節點越少，
「落在小 buffer 裡的節點」就越會獨吞**。實證：`attn_norm` 被報成 **36.76 ms（8.5%）**，而它是
`MUL ne=[2048,2]` 的 4096 元素乘法。**⇒ 細粒度排名要讀 `CGC-GPUOPS`**（`nd=` 是確定值），
不要讀名字表。（lesson `eng-mh-0070`；白皮書 §25／§25.1／§26。）

**這一步只讀既有的 log**（`Backup/cgc_logs/*.log` 裡任一份帶 `CGC_GRPH_DBG` 的），不跑任何東西。
⇒ **通則：在「猜名字的代價是一整輪跑」之前，先問「我能不能從既有的 dump 讀出來」。**

⚠️ **兩個必須先知道的邊界**（`ggml-backend.cpp:2038-2057`）：

- dump 的條件是 **`cgc_grph_dbg_n < 6`**（`:2054-2055`）⇒ **只捕捉前 6 次 `graph_compute`**，
  而它們會被載入期的 warmup／prefill 吃掉 ⇒ **印出來的 `ne` 是 prefill 形狀**（不是 decode）。
  ⇒ **節點結構（名字、op、索引順序）可用；形狀不可用。** 要 decode 的形狀就得改那個條件，
  而那是動 `src/`。
- 它 dump 的是 **`split->graph`，也就是那個 split 的「完整」cgraph**（`:2057-2058`）——
  `gv0 = seg_view(0)` 只是它的 `ggml_graph_view`（`:2038-2043`），**不是** dump 的對象。
  所以**一個 dump ≈ 一步**（trunk 的 `n_nodes=4116` ÷ 41 層 ≈ 100 節點/層），**不是一層**。
  ⚠️ 2026-09-18 我在同一天先寫成「一個 dump ＝ 一個 segment（一層）」⇒ **正好說反**：
  拿一個 dump 的節點數除以層數才是「一層」，而不是把它當成一層。

### 步驟 2（要動原始碼）：在 builder 裡命名

只有當某個叢集**大到值得被單獨引用**時才做（本例：那條 240 核心的歸約鏈 ＝ **6.7% of `wait`**）。
改動是**只呼叫 `cb()`／`ggml_format_name`**，不碰圖的結構、不碰數值。實例（行號會漂）：

- `llama-graph.cpp` 的 MoE 合併迴圈 → `cb(moe_out, "ffn_moe_add", il)`。**寫在迴圈內**；
  最後一項仍會被既有的 `cb(moe_out, "ffn_moe_out")` 改名 ⇒ 實際 **6/7 生效**（`240 = 40 層 × 6`）。
- `models/delta-net-base.cpp` 的兩個 GDN 呼叫點 → `"gdn_state"`（K=1 路徑）／`"gdn_out_raw"`（K>1）。
  （K=1 那條**在 2-token 的圖裡不走** ⇒ 它會是 0 次，不要以為命名失敗。）
- ★ **詞彙表要同步加**（`ggml-backend.cpp` 的 `ns_fix[]`），否則新名字會被 longest-prefix
  兜底桶吃掉（`ffn_moe_add` → `ffn_moe_`、`gdn_*` → `(other)`），**等於沒命名**。

**命名之前必須驗的四條**（這是「我有沒有只是改了名字」的判準；四條都要查，不要用推論）：

1. `cb()` ≡ `ggml_set_name`／`ggml_format_name`（`llama-context.cpp`，搜 `cb = [`）—— 只寫名字。
2. 有沒有程式用「**名字為空**」當判準？搜 `name[0] ==`／`strlen(...->name)`；命中的要是**診斷路徑**。
3. 該節點有沒有進 `add_fused_node`？它**只 push_back、不讀名字** ⇒ 安全（要查，不要假設）。
4. 名字比對是 `strcmp` **精確**還是**前綴**？（前綴比對會被新名字意外命中。）

**證明是 D5 的 M1 逐位元 9/9**（`--tag <你的>`）：M1 不過 ⇒ 你不只是改了名字。

### ⚠️ 十個會讓人算出離譜數字的東西

- **`ne` 只印前兩維**（`ne=[ne0,ne1]`），而 MoE 的 token 數在 **`ne[2]`** ⇒ 用 `ne0*ne1*4`
  當「搬了幾 bytes」在 `n_tokens>1` 時**系統性低估**（decode 的 `ne[1]` 剛好等於 token 數，
  所以只在批次形狀露出來）。**prefill 的 dump 一律不能這樣算。**
- **`SET_ROWS`／`GET_ROWS` 的 `ne` 是整個張量，不是「這次搬的」**：`SET_ROWS ne=[512,8192]`
  是整條 KV cache，而 decode 每步只寫**一行**。實測我一度算出「335 MB/步」，複核時自己推翻
  （lesson `eng-mh-0063` 的形狀）。⇒ 要算搬運量，先問「**這個 op 是切片嗎**」。
- **`name` 是匹配規則的產物，不是語意的產物**：`ffn_moe_` 是 longest-prefix 的**兜底桶**，
  裝的是 `ffn_moe_swiglu`／`weights`／`weighted` ＋ **它自己的 8 個 VIEW**——**逐元素運算**，
  與同名的專家 GEMV（`ffn_moe_gate`／`up`／`down`，op 是 `MUL_MAT_ID`）**是兩回事**。
  照字面把「`ffn_moe_*` 那群」當成 MoE 矩陣乘，結論會**相反**。
- **★ 圖裡有這個節點 ≠ GPU 上發了這個 dispatch**（2026-09-21 坐實）：
  `ggml_is_empty()`（任一 `ne[i]==0`）為 true 的節點，在
  `ggml-metal-ops.cpp:68-74` 就被 `if (!ggml_op_is_empty(op) && !ggml_is_empty(node)) idxs.push_back(i)`
  **濾掉、不進 encode 名單**（第二道閘門 `ggml-metal-ops.cpp:238`；encode 循環只跑
  `n_nodes()==idxs.size()`，`ggml-metal-context.m:1476-1482`）⇒ **成本 0**。
  注意 `ggml_op_is_empty(GGML_OP_CPY)==false`（`ggml-impl.h:90` 只列 NONE/RESHAPE/TRANSPOSE/VIEW/PERMUTE）
  ⇒ 過濾靠的是**形狀那一半**。
  - 直接後果：**`CGC-GPUOPS` 的 `nd` 是圖節點計數，不是 dispatch 數**
    （`ggml-backend.cpp:2372-2379` 逐圖節點取 `cgc_node_op()`；`work`/`NOOP` 欄只看 op 類型不看形狀）。
    真 dispatch 數 = 圖節點數 − empty 數。
  - 通則：**任何以節點數為分母的歸因，先問「有沒有被 `ggml_is_empty` 濾掉」。**
  - 實例：K2 審計一度把 60 個 `ne=[X,0]` 的 CPY 算成 60 × 0.0736% = 4.4%，純讀碼即證偽為 0。
- **★ `CGC-DISPATCH` 有兩種同名前綴的行，regex 會雙算；而且它的「graph」是 segment 不是一步**
  （2026-09-21 複審發現）：
  - 每張圖印 `graph=N dispatches=T nodes=Tn`（匯總行）＋ 最多 14 行 `  OP dispatches=x nodes=y`。
    `re.match(r"CGC-DISPATCH:\s+(\S+)\s+dispatches=(\d+)\s+nodes=(\d+)")` **兩種都匹配**
    （實測 `group(1)` = `graph=1` / `ADD`）⇒ 若以 `group(1)` 為 key 再 `sum()`，`disp_total ≈ 2 × 真實量`
    （`Backup/metal_fusion_dispatch_cost_ab.py:140` 就是這樣，Δ115 其實 ≈58）。
  - `idx == 0` 在 `ggml-metal-context.m:1471-1482` 是**每顆 command buffer 的局部索引**
    ⇒ flush 每顆 cb 觸發一次 ⇒ 普查的「graph=N」＝ **segment**（原文自己標 `graph=2 ← ARGSORT（top-k 段）`）
    ⇒ **「1098 dispatch/步」是 48 段的合計，不是一步**。
  - 通則：**任何「每步」的 dispatch 數字都要先問「分母是段還是步、有沒有被匯總行雙算」。**
- **★ 單位錯配：拿「每窗口」的單價去乘「每步」的數量**（2026-09-21 實測後才發現，是最貴的一種錯）：
  - 承上一條：census 的 48 個 cmd_buf **只有一步的 23.9%**（`CGC-GPUTIME` 按步印 `bufs≈353`、
    `segs=40`；一步真有 ~3056 dispatch ⇒ 真值比「1098/步」還大 3×）。
  - 所以 `Δ%/Δdispatch` 得到的單價是**「每 dispatch、每窗口」**，要乘的對象必須也是**同一窗口內**
    的數量。**最穩的做法是用同一窗口內的比值**（外推因子自動抵掉），或先把數量換算成每步。
  - 實例：v1 的 `0.0736%/dispatch × 72 = 5.3%` ⇒ 實測正確答案是 **2.3%**（單價被放大 4.176×，
    而 72 其實是 117，兩個錯方向相反、沒抵掉）。
  - **外推因子用 `nodes_step / nodes_窗口`（=4.176），不要用 `bufs_per_step / 48`（=7.354）**
    —— 前 48 個 cmd_buf 比平均大（15.25 vs 8.7 dispatch/buffer），後者高估 1.76×。
  - 另兩個 census 坑：① per-op 行由**多執行緒 encode 交錯寫出**，`graph=` 編號**不順序**
    （實測 1,2,3,5,6,7,4,8…）⇒ **逐圖歸因不可用**，只有全域求和有效；
    ② **top-14 截斷**，per-op 表只解釋 44–47% 的 dispatch（cluster-1 捕獲率僅 68%）⇒ 絕對量要先修正。
  - 要「每步」的 op 數量，改用 **`CGC_GPU_NODES=1 + CGC_GPU_OPS=1 + CGC_GPU_TIMING=1`**
    （`ns_total > 0` 是印表條件，缺 `CGC_GPU_TIMING` 就 0 行）。⚠ **按步的 op 表混了異構步**：
    實測 37 步裡 20 步是 45-node 的 **MTP draft 圖**、17 步是 3800-node 的真 decode 步
    ⇒ **混著取中位數全錯**，必須先用 `nodes_all` 篩。
- **★「計數指標沒有噪音、所以一定能重複」是錯的**（2026-09-21 L1 復審實測）：
  - 同一配置連跑兩次 `CGC_FUSE_MAX=8`，ADD 的 `dispatch/nodes` 比值是 **0.351 vs 0.373**（差 6%），
    而 `nodes` 是 67 vs 97 —— census 只覆蓋前 48–50 個 cmd_buf（**一步的 13.6%**），
    **覆蓋到哪些 cmd_buf 有隨機性**。
  - 反直覺：**同一配置的 t/s 反而更穩**（9.917 vs 9.803，1.2%）。
  - ⇒ 計數指標**只穩在量級**（「沒熔 1.000」vs「熔了 0.36」是鴻溝，可放心用），
    **不穩在小數點第二位**。別拿 census 絕對數外推，也別指望它重複到 1%。
  - 通則：**先問「這個計數是抽樣還是全量」**。census／GPUTIME 是抽樣，圖 dump 與 `GPUOPS` 是全量。

### 融合（fusion）的既有事實，不要再重推一遍

- **現成旋鈕（都在 shipped binary，0 重建可用）**：`CGC_FUSE_OFF=bin|norm|snake|all`（逗號分隔）、
  `CGC_FUSE_MAX=<n>`（bin 鏈深度，`ggml-metal-ops.cpp:5688-5703`）。另有 `GGML_METAL_FUSION_DISABLE=1`
  （三條全關，**太鈍**）。確認有沒有編進去：`strings libggml-metal.dylib | grep CGC_FUSE`。
- **bin 融合只認 ADD**：`:5770-5778` 把 `fops[0..7]` **全寫死 `GGML_OP_ADD`**
  ⇒ MUL／SUB／DIV 從不進 `can_fuse`（實測 ratio 恆 **1.000**）。
  但 **kernel 早就支援 mul/div**（`ggml-metal.metal:1279` `kernel_bin_fuse_impl` 的
  `FC_OP` 0=add 1=sub 2=mul 3=div）⇒ 要讓 MUL 鏈熔，**只改 host 端 fops 填充，不用動 shader**。
  ⚠ `FC_OP` 是單一值 ⇒ 只支援**同類型**連續鏈，混合 ADD+MUL 不行。
- 兩道上限疊加：`n_fuse <= 6`（硬編碼）＋ `n_fuse < cgc_fuse_max()-1`（env 預設 8）⇒ **現狀實熔 7**。
- **判「還能熔多少」的正確方法**：拿圖 dump（`CGC_GRPH_DBG`）把節點序列切成
  **極大同類型連續段**，用 `ceil(len/cap)` 算 dispatch。實測 ADD = 351 孤立 + **201 條長 9**
  ⇒ 模型預測 cap=7 時 ratio 0.349，與實測 **0.351 吻合**。
  ⚠ **不要用「平均鏈長」**：3.91 是**被 351 個孤立節點拉低的假象**，會讓你以為沒有長鏈。
- **結論（已結案，別再重做）**：L1（融合擴展）只剩 **1.33%**，低於 3% 門檻
  ⇒ 9-ADD cap7→9 值 0.76%、MUL 納入 0.57%、**DIV 0（全孤立）**。
  真 decode 步裡不成鏈的孤立小 op ≈ **324 個/步**，融合救不了 ⇒ 那是 K5（45.6 µs 常數）的地盤。
  ⚠ **但 45.6 µs 已在 09-21 §EN-406 被判為分攤恆等式**（見下節）⇒ 別再拿它算收益。

### ⚠️ 第 8 條（最新、最容易騙人）：`wcntw` 是**分攤**，不是測量

`CGC-GPUOPS` 的 `wcntw` 欄 = `ggml-backend.cpp:2421` 的 `dur * ocnt[q] / owork`
⇒ **把每個 command buffer 的時長按節點數線性分攤給裡面的 op**。

- **「所有 op 的 µs/node 都一樣」是代數恆等式，不是發現**：Σ_q wcntw[q] = Σ_b dur_b = 總時長
  （精確劃分），而 op 在 buffer 間分佈均勻（實測 `ub` 每種 op 都 75–86%，即幾乎每個 buffer
  含幾乎所有 op）⇒ 每種 op 必然回歸到 **總時長 ÷ 總節點數**。
- **判「這是不是分攤假象」的三個指紋**：
  ① `uni` 欄**全 `-1`**（從無單一 op 的 buffer ⇒ 沒有精確除法可校準）；
  ② Σ`wcntw` 只蓋 **87–93%**（殘差隨 buffer 數增長）；
  ③ **不同 op 的 nd 差好幾倍、µs/node 卻小數點後兩位相同**（實測 CLAMP/DIV/GLU/SUM_ROWS/
     MUL_MAT_ID 全是 52.05）⇒ 只能是同一批 buffer 的同一個 `dur/n`。
- ⇒ **落在常數上的 op 資訊量為零；偏離常數的才有資訊**（GATED_DELTA_NET 188／
  FLASH_ATTN_EXT 120／SOFT_MAX 29，因為它們集中在少數 buffer）。
- **要真的 per-op 成本，用 `CGC_GPU_NODES_MATRIX=1`**（0 重建，已在 shipped binary）：
  逐 range 印 `dur_ns` ＋ 每 kind 節點數，離線擬 `dur_i = Σ_k cnt_ik·t_k`。
  ⚠ **但它的 kind 是「張量名前綴」（ffn_/attn_/cache/norm）不是 op 類型**，同一層張量
  總是一起出現 ⇒ **設計矩陣共線性，最小二乘會失敗**（fit 122.7%、出現負的 t_k）。
  ⇒ 它只能回答「哪一群張量貴」，**不能**回答「ADD 一個節點多少 µs」。

### ⚠️ 第 9 條：邊際價 ≠ 平均價，別拿邊際價去乘總量

K3 實測「融合省一檔 dispatch」的**邊際**價 = 17.9 µs；但 M 曲線截距反推的**平均**價 =
250 ms ÷ 3056 dispatch = **81.8 µs**，差 **4.6×**。
⇒ **A/B 量到的 Δ 只能除以該 A/B 的 Δdispatch**，不能拿去乘「全部 dispatch 數」。

### ⚠️ 第 10 條：M 掃描分不出「grid 太小」與「Metal 固定成本」

兩者在「改變 M」下的預測**數學同形**（固定開銷被 M 攤薄 ⇒ ms/位置 下降）。
⇒ 別再用 M 曲線去判 (A)/(B)。而且 M 曲線的截距**不能**除以節點數當「每節點固定成本」：
M=1 讀權重本就與 M 無關 ⇒ 截距裡最大的一塊是**權重頻寬**，不是 dispatch 開銷。

### ⚠️ 第 11 條：`ffn_moe_` 是前綴，路由節點會被誤當成「讀 pool slot 的 MoE」

`CGC-NSM`／`CGC-GPUNODE` 的 kind 是張量名前綴，而 **`ffn_moe_argsort` / `ffn_moe_logits` /
`ffn_moe_probs` / `ffn_moe_topk` 全都被 `ffn_moe_` 前綴命中**——但它們是**路由**，便宜、
且**不讀 expert pool slot**。寫 `k.startswith("ffn_moe_")` 當「MoE 計算」會得到假陽性
（第一版就這樣把「MoE 在 segment 第 0 個 buffer」誤判成「在最後一個」）。
⇒ 分類時**先排除路由再判 core**：`is_core = startswith(MOE_CORE) and not is_route`。

### ✅ 第 12 條（可重用招式）：用 `CGC-NSM` 的 `[a,b)` 重建 segment 內的節點順序

每列是一個 command buffer：`[a,b)` 節點區間 ＋ 實測 `dur_ns` ＋ 區間內各 kind 計數。
**`a==0` 就是新 segment 的開始**（該 buffer 總是先出現），其內按 `a` 排序 ⇒ **免費拿到
segment 內的節點先後順序**，不需要重建、不需要 `CGC_GRPH_DBG`（後者只 fire 前 6 次，只有 prefill）。

用處：判「某類節點前面還有多少 GPU 工作可以拿來遮別的東西」——例如 L3 的 async fill 視窗。
實測（2026-09-21）：**MoE core 永遠在 segment 的第一個 buffer**（5811/5811，前面 duration 佔比
med = 0.000，按 segment 大小分層後仍然 0）⇒ 單流 decode 的 async fill 視窗 = 0。
腳本範本：`Backup/phase_decomp/L3/segment_moe_position.py`。

**交叉驗證這類「遮蔽量」結論時，用 `gap` vs `cb`（同一支 run、兩個獨立儀器）**：
`gap`（GPU 時鐘空檔）若 ≥ `cb`（CPU 側 fill），代表「完全沒被遮到」，而且 GPU 空轉得比 fill 還久。

### ⚠️ 第 13 條：**【已撤銷 2026-09-21 當天】** 別用 `F_NOCACHE` 探針報絕對 GB/s

這一條原本寫「形狀差 4.4×、併發 2 緒最優」，並據此把 neighbour prefetch 判成賺。
**兩處都錯，已由 `docs/L3_PREFETCH_RETRACTED_2026-09-21.md` 撤銷**。錯的原因有兩個，
第二個是方法論等級的：

1. **讀尺寸搞錯**：expert 在檔內是 **3 支獨立 GGUF 張量**（詳見第 14 條），
   R=1 只會把每次讀從 ~0.21 MiB 放大到 ~0.64 MiB，**永遠不會出現 3 MiB 讀**。
   我拿 1 MiB ↔ 3 MiB 的比值去定價，兩端都不在真實區間。
2. **探針自己跟自己差 2.3×**（同一 3 MiB、2 緒、同檔案、背靠背交替）：
   `os.pread` 2.36 GB/s vs `os.preadv` 5.46 GB/s；而同一 `preadv` 在另一支腳本只有 1.93。
   根因幾乎可以確定是 **12.72 GiB 的檔放在 16 GB RAM 的機器上，頁快取裝得下大半**，
   `F_NOCACHE` 並沒有把讀隔離乾淨（seq 量到 5.4 GB/s 已超過這台 SSD 的物理上限）。

⇒ **規矩**：這台機器上的 `F_NOCACHE` 探針**只能用來做同一支儀器內部的相對比較**，
絕對 GB/s 一律不可引用，也不可跨腳本比。要真冷讀數字，先解決「檔 ≲ RAM」這個根本衝突
（用遠大於 RAM 的檔、或外接盤），否則不存在乾淨窗口。
⇒ 相對比較的結論是：**0.32 → 1.29 MiB 吞吐基本持平**（R=1 時間比 ×1.87–3.05）
⇒ **「多讀 X 倍位元組 ≈ 多花 X 倍時間」在這個區間是對的**，`NEIGHBOUR_PREFETCH_VERDICT`
原本的「淨虧」判決**恢復有效**。

**仍然有效的三條硬要求**：① `F_NOCACHE`（fcntl 48）；② **丟掉第一輪**（恆慢 20–40%）；
③ 用 `pool_wait_us / misses` 之類的 **run 級歸一化量**當主指標，不要用 t/s（單臂 ±27%）。

### ⚠️ 第 14 條：expert 在檔內的佈局要**讀 GGUF 頭**，不要用「每 expert 幾次 read」去猜

`python3 Backup/phase_decomp/L3/gguf_expert_layout.py --layer 5`（0 重建，直讀模型檔 header）：

```
blk.L.ffn_gate_exps.weight  dims=[2048,512,256]  82.0 MiB -> 0.3203 MiB / expert
blk.L.ffn_up_exps.weight    dims=[2048,512,256]  82.0 MiB -> 0.3203 MiB / expert
blk.L.ffn_down_exps.weight  dims=[512,2048,256] 110.0 MiB -> 0.4297 MiB / expert
                                      one expert = 1.0703 MiB（3 支張量，佔全檔 85.1%）
```

- **GGUF 的 `dims` 是逆序存放** ⇒ `dims[-1]`（=256，專家數）是**最外層**軸 ⇒
  **e-1 / e / e+1 在同一支張量內是檔案連續的**（neighbour prefetch 的連續性假設成立）。
- 但**三支張量彼此獨立、偏移相隔上億位元組** ⇒ 任何「把 3 個鄰居合併成一次大讀」的想像都是錯的：
  你只會得到 3 次各放大 (2R+1) 倍的讀。
- ⚠️ 引擎的 kind 枚舉是 `0=gate 1=up 2=down 3=gate_up`（`llama-expert-cache.h:78`），
  但實測 `reads/miss = 4.96`（prewarm miss = 0），**比 3–4 段還多**，多出來的 ~2 次來源未定位
  （draft/MTP 路徑 / wide path / cold path 各計一次 `n_reads`）。
  ⇒ 別用 `n_reads` 反推段數，也別用 `pread_usec / n_reads` 定價每讀成本
  （實測 13–17 ms/讀，對 220 KiB 物理上不可能，該計數器口徑有問題）。

### ⚠️ 第 15 條：thermal 是**單調漂移**，不是噪音 ⇒ 臂間要冷卻、順序要 ABBA、HEAVY 臂作廢

實測（2026-09-21 21:06，交付 cell，同一負載只差先後）：
第 1 臂 w=8 **120/120 採樣 NOMINAL**；緊接著的第 2 臂 w=2 **81/120 採樣 HEAVY**。
⇒ 只看「發射時 NOMINAL」的閘門**完全擋不住**：熱是臂**內部**升上去的，而且往單一方向走。

單調漂移**不能用 AB 交替抵銷**（交替只對雙向隨機漂移有效），要做三件事：

1. **臂間冷卻**：每臂前 `sleep 150` 再輪詢等到 NOMINAL（腳本 `wait_nominal()`，有硬上限）。
   實測加了 150 s 冷卻後，6 臂**全部 107–117/117 採樣 NOMINAL**。
2. **順序 ABBA**（round 奇偶反轉臂序），讓單向漂移對稱地打到兩臂。
3. **HEAVY 臂作廢**：`thermal.worst == HEAVY` ⇒ 直接標 `invalid` 踢出中位數，不要「降權引用」。

- **主指標用 run 級累加量的配對比**（`pool_wait_us / misses`），不要用 t/s：
  實測 us/miss 臂內 CV ≈ 0.7%（w=8）而 t/s 單臂噪音 ±27%。
- **n 小時唯一誠實的顯著性**是「配對同向數 / 總對數」＋ `2^-n`（硬幣零假設），
  不要去算 t 檢定：效應幾個百分點、n=3~5、母體不是常態。
- 交付 cell 一輪 ≈ 70 s，冷卻 150 s ⇒ **一對 ≈ 7 分鐘**，n=5 要 ~35 分鐘，排程要留這個量。

### ⚠️ 第 16 條：要 decode 的**節點形狀**用 `CGC_ELEMW_CENSUS`，不要用 `CGC_GRPH_DBG`

| 儀器 | 門檻 | 印什麼 |
|---|---|---|
| `CGC_GRPH_DBG`（`ggml-backend.cpp:2091`） | `static ... cgc_grph_dbg_n < 6` ⇒ **整個行程只有 6 個圖** | 只 `ne[0], ne[1]` 兩維；實測 5/6 是 `n_nodes=4116`（**prefill**）⇒ **拿不到 decode** |
| **`CGC_ELEMW_CENSUS=1`**（`ggml-metal-ops.cpp:582`） | **3000 行**（行數，不是圖數） | **`ne=[n0,n1,n2,n3]` 全四維** + `name` + `op` + unary sub-op |

⇒ 後者 **0 重建就能拿到 decode 形狀**（`python3 Backup/phase_decomp/K5/elemw_census.py --reps 1`，
30 s）。用 `CGC_GPU_TIMING=1` 的 `step=` 行當錨點，兩個 `step=` 之間就是純 decode。

- ⚠️ `want` 清單是 `UNARY/MUL/GLU/SCALE/L2_NORM/DIV/CLAMP/SUM_ROWS` ⇒ **不含 ADD**
  （小 op 最大一支看不到），但覆蓋 K3 的 cluster-1。
- ⚠️ `CGC_GPU_NODES_MATRIX` 的外層門檻是 `ns_ops = CGC_GPU_NODES && CGC_GPU_OPS`
  （`:1873`/`:1889`）⇒ **要四個 env 一起開**：`CGC_GPU_NODES=1 CGC_GPU_OPS=1 CGC_GPU_TIMING=1
  CGC_GPU_NODES_MATRIX=1`。只設 MATRIX 會得到 **0 行**（已實測兩次）。
- ⚠️ **未解**：ELEMW 與 NSM 同時開時 NSM 仍 0 行（原因未定位）⇒ 「元素數 × 實測時長」
  的同 run 回歸目前做不了，只能跨 run 用 kind 對齊（同 cell 可接受，但必須標注）。

### ⚠️ 第 17 條：一個 dispatch ≈ 18–19.5 µs，而且**幾乎全是固定開銷**

兩個獨立儀器互證（2026-09-21）：
- `CGC-NSM` 裡 `nk==1` 的 buffer ＝ 單 dispatch 的實測 GPU 時長：`ffn_moe_argsort`
  **1490 個、中位 18.6 µs**（另有 `cache` 68 個中位 1334 µs，是另一類東西）
- K3 實測邊際價 `0.0195%/dispatch` × 步時 ~100 ms = **19.5 µs**

⇒ 吻合在 5% 內，可引用。而且 argsort 的工作量只有幾個元素 ⇒ **這 18 µs 不是執行時間**。
⇒ **任何「撐 grid／提高佔用率」的構想都動不了它**；(A) grid 太小與 (B) Metal 固定成本在
任何「grid 大小 vs 時間」的觀測下**同形**，要判別得改 kernel 的 threadgroup 配置做 A/B
—— 那是另一個量級的工程，不是「改個 dump」。
⇒ 看到「小 op 群佔 X% 時間」就想「拿回 X%」的算法是錯的：**X% 裡最大的一塊是
dispatch 固定開銷**。

### ⚠️ 第 21 條：先算「這份預算能證明多大的效果」，再決定要不要 sweep（2026-09-22）

（`scripts/check/closed_loop_autotune.py`、`docs/CLOSED_LOOP_AUTOTUNE_2026-09-22.md`）

- **最有產出的一張表**：給定預算與 σ，能被認證的最小真實差距。
  16 launch、σ=0.16 ⇒ 約 **68%**；σ=0.06 ⇒ 約 **52%**（兩臂解析式給 23.5%／8.4%，**過於樂觀**，
  因為要打敗的對手不止一個，且 `log(cell/插值ref)` 疊了兩段 ref 的雜訊 ⇒ 用**仿真標定**
  `calibrate_boundary()`，不要用代數）。
- ⇒ **預期只差 3–5% 的 knob 不要 sweep**：14 次 launch 在數學上分不出來，加多少旋鈕都一樣。
  這是算出來的，不是悲觀。也解釋了為什麼這個 repo 一路上每層都「低於門檻」。
- 閉環三件事各自帶**自己的突變**（進程內對照，沒寫死布林值）：policy→後驗集中度掉 3.1 pts；
  sigma 固定 0.06→假發現 6%→54%；noev→6%→93%。哪天突變不再有代價，整套會自己變紅。
- ⚠️ **regret 不是它贏的地方**：small budget 下學習策略 regret 偶爾**比均勻分配差**
  （quiet 1.92% vs 1.75%）。贏的是「每次 launch 換到的證據」，不是「部署得多準」。
- ⚠️ 觀測假發現率 **6% > 標稱 5%**：同 block 內的 cell 共用那兩次 ref launch，誤差相關 ⇒ n 高估證據。
- ⚠️ 高噪聲下報價偏**高** 1–2 個百分點（`exp()` 是凸函數，Jensen；σ=0.02 時偏差 ≤0.2 pt）
  ⇒ **決策要用 P(best)（數 argmax，對單調變換免疫），不要用報價數字**。
- ⚠️ `--broken` 有值時 `--exec` 會拒絕啟動：突變是用來跑植入真值的，不是拿真實 GPU 時間去量已知錯的演算法。

### ⚠️ 第 19 條：GDN 軸「operator 存在」，但這個 workload **一次都沒進去**（2026-09-22）

（第 12 條說 GDN 已是 Metal fused op、別再發明 chunked scan —— 還是對的。這條是補上「那它有沒有在跑」。）

- 反了兩次才到答案：先因為只搜 `LLM_FUSED_OP_GDN_CH` enum 而誤判 ABSENT；更正後又以為「關掉 fusion
  能測出 fusion 的成本」，結果 **ablation 後的 dump 與 default 逐位元相同**。
- **不要從「沒差異」直接下結論**。判準是 builder 自己回報的 `gdn_saw_fused` / `gdn_saw_manual`
  （`models/delta-net-base.cpp` 呼叫 `cgc_shape_count_gdn`，印在 `phase=final`）：
  本次 server 歷程兩者皆 **0/0** ⇒ `build_delta_net` 從沒進去 ⇒ dumps 相同是「沒東西可比」，
  不是「兩條實作數值一致」。**GDN 相關的省時到目前為止全部是在量 0。**
- ⚠️ MTP 陷阱：spec decode 時 target 一次驗證多 token，`n_seq_tokens > 1` 走 **chunking** 分支，
  只關 `CGC_SHAPE_GDN_AR` 會看不見任何變化。**要 ablation 就兩個都關。**
- `bitident=NO` 的 row 在 harness 裡是 **ABLATED**，不可與錨定數字比較（它是「fusion 的成本」，
  永遠不是候選值）。

### ⚠️ 第 20 條：讀比率用**幾何**，而且要在**訊號的時間點**插值（2026-09-22）

（另一份：`docs/ORDER_DRIFT_CORRECTION_2026-09-22.md`）

- 吞吐是被**乘**的（熱機讀 `rho × t/s`），所以 `log` 才會變加法 ⇒ ref 的組合／插值要用
  **幾何（log 內插）**，權重取「Arm 實際被量的時刻」（cell 遠長於 ref，索引中點不是它的時刻）。
- 實測今晚 ref 序列 11.72/10.83/5.22：`M=17` 那列算術 ref 8.02 vs 幾何 7.52，**差 6.75%**，
  Δ 從 +17.47% 變 +25.40% —— 但那列**本來就 UNANCHORED**（refs 差 51.8% > 10%），
  **校正不會把它救回資料**，它只是量化了這個 artefact。
- `crossover_estimate.py --selftest`（14/14）＋ `--selftest --broken`（舊估計器 **必須紅**，最差錯 8.03%）。
- ⚠️ 幾何**不是處處都贏**：兩側 ref 長度差很多時，「被量測的時刻」不再是點，算術反而剛好較準
  （表裡 0.47% vs 0.07%）。所以 `mean_gap_pct` 是**診斷**不是第二道閘門。
- ⚠️ 交叉設計**去不掉** carry-over（pool／page cache／SLC 留在下一隻 arm 身上的狀態）：
  兩條 arm 分歧 >10% 要**印出來**，不要平均掉。

### ⚠️ 第 22 條：看到「residual / remainder / 未解釋項」**先審計它的減法公式**（2026-09-23）

實例（`docs/ROUND_REMAINDER_IDENTIFIED_2026-09-23.md`，零 GPU 重算既有 log）：
`verify_marginal.py:245` 的 `remainder = round_ms − Σ_40層(wait+cb+submit) − draft − begin`
被當成「post-layer per-round work（邊際 verify token 的 70–102%）」寫進結論，還因為
「它隨 T 成長」看起來像一個真實機制。實測：

- 那個 `Σ_40層` 是**一個 step** 的層時間，而**一個 round 有 1.5–2.3 個 `segs=41` verify step**
  （`CGC-DECPROF` step 數 ÷ `calls_draft`）⇒ 減掉的比實際花的少 ⇒ 剩的 ≈ `(n_step−1) × step`。
- **單一 step 的中位時間不隨 T 變**（179.8 vs 178.0 ms）⇒ 「隨 T 成長」只是 n_step 在長，
  免費的。把它包進 `round ≈ n_step × step + draft` 後閉合到 **+7~22%**，沒有 100+ ms 黑盒。

規則（順序照做）：
1. **指名單位**：減法兩邊是不是同一個計數單位（step vs round、per-call vs per-step、
   mean vs median）？殘差會把單位錯配**整數倍放大**。
2. **分母從哪來**：這裡的 `calls_draft` 是引擎計數器（可信），而 step 數是**取樣**的
   DECPROF（兩 arm 的 `rounds` 不同 ⇒ 取樣率直接污染比率）。取樣率不同的兩個 arm 不能比「每輪幾個」。
3. **單步時間要看中位數**：DECPROF 的**第一行**是 warmup（37/51/12），中位數是
   **wait 71–77% / cb 17–23% / submit 5–7%**，而且三項恆等 100% ⇒ **step 內沒有殘差**。
4. 追「殘差在哪」之前先問：**它是不是一個被低估的已知項乘上一個倍數？**

（`scripts/check/round_ledger.py --selftest` 9/9、`--arms <arms.jsonl>` 直接印這張表。）

### ⚠️ 第 18 條：選主指標前先看穩定性排名 —— `union+gap` **不比** t/s 穩，而且**不是步時**

實測（2026-09-22 §EN-417，同一交付 cell，權威 `docs/METRIC_STABILITY_2026-09-21.md`）：

| 指標 | 離散度 | 用法 |
|---|---:|---|
| **`µs/miss`（pool_wait_us/misses）** | **CV 1.95%**（w=8,n=5）／4.55%（w=2） | ✅ **主指標** |
| t/s（llama-bench avg_ts） | **CV 7.88%**／7.43% | 只當哨兵，幅度不引用 |
| `union`（block-mean 修正後 SEM） | 5.0% | 同 run 內相對比較 |
| `union+gap` | **7.7%**（逐行 CV 35–47%） | ❌ 與 t/s 同級或更差 |
| `gap` 單獨 | **逐行 CV 64–85%** | ❌ 不可作證據 |

**四個會翻船的點**：
1. **⚠「單臂 t/s 噪音 ±27%」是 K3 cell 的值**；交付 cell（reps=3 ＋臂間冷卻 150 s）實測
   **只有 7.88%**，差 3.4× ⇒ 噪音底是 cell 相關的，別混用。
2. **⚠ `union+gap` 不是步時**：交付 cell 同 run，步時 **267.84 ms** vs `union+gap` **127.29 ms**
   ⇒ **低估 2.10×**（它是 GPU 時鐘跨度，不含 `wait`/`cb`/`submit`）⇒ 拿它算天花板會得到
   26.5 t/s 這種不存在的數字。**要步時就用 `ML / t_s`。**
3. **「逐步樣本多 ⇒ 穩」是錯的**：37–59 行**強自相關**，用 block mean(5) 修正後有效 n 只剩
   5–8 個；且**後半 vs 前半 −2.2%~−21.6%（8 支全負）** ⇒ 非平穩，取哪幾步平均是 bias 不是 noise。
   ⇒ 算逐步量的不確定度**一定要做 block mean**，別直接用 `sd/sqrt(n)`。
4. **所有 run 的 `skipped` 均值 4.9–7.3（非 0）**，而程式註明「skipped 必須為 0」
   ⇒ `busy`/`union`/`gap` 帶未知偏差，**只可做同 run 內相對比較**（含 busy/union = 1.510）。

**測出 1.7% 效應需要每側幾臂**（雙樣本 95%，未配對上界）：**t/s ≈165 臂，µs/miss ≈10 臂**
⇒ 效應只有幾個百分點時，**唯一可行的主指標是 run 級累加量**，不是 t/s，更不是逐步 GPUTIME 量。

## 判準（已校準，直接照用）

- `gpu_union / wait ≥ 70%` → 傾向「執行受限」；`≤ 40%` → 傾向「序列化受限」。
  **但先讀陷阱 1**，`union` 的結構性偏誤會把兩種情況都推向 ~90%。
- **`gap`（相鄰段 `start_{i+1} − end_i`，GPU 時鐘）是唯一乾淨的證據**：
  `gap ≈ 0` ⇒ GPU 被餵飽；`gap` 佔 `wait` 的 20–35% ⇒ 有可證的閒置。
- `gpu_busy_sum / gpu_union ≈ 1` ⇒ 一個段的 `n_cb+1` 顆 buffer **幾乎序列**
  （此時 `n_cb` 掃描必然無效，兩者互相印證）。
- **最強的一招是上界探針**：把可疑的序列化用 `CGC_SUBMIT_AHEAD=1`（或任何等價的
  「移除該窗口但語意錯誤」開關）整段刪掉量一次。
  「第二個在飛的東西不可能讓真實計算變快」⇒ 若步時間下降 X%，就有 X% 是序列化。
  這比任何相位分解都決定性。輸出損壞是**預期**的，md5 必須變，否則代表旗標沒生效。
  ⚠ **★ 2026-09-19 更正：這一招今天「只對一半」—— 步時上界有效，gap/union 與 t/s 無效**（見陷阱 28）。那個臂的 header 印
  `(NO TIMESTAMPS)`（25/28 步）、`mean len = 1.00`、生成截到 21 token ⇒ **既沒有 gap 讀數、
  也沒有可比的 t/s**（acceptance 崩掉）。**但它仍然重現了步時上界：169.21 → 99.39 ms = ×1.702**
  （兩臂 verify 圖寬都是 4）。⇒ 用上界探針時**逐欄位判定哪一個可以讀**：
  `grep -c "NO TIMESTAMPS"` 非 0 ⇒ 禁讀 `gpu/union/gap`；`mean len` 與 base 不同級 ⇒ 禁讀 `t/s`。
  **兩臂圖寬相同時步時比仍然合法**，而那正是這一招要的那個數。
- **★ 一個新的歸屬份額（任何 weight／攤分模型）在能被引用之前，先過「換粒度」測試**：
  同 profile、**同熱態**跑粗／細兩臂，逐項比。
  ⚠️ **判準是 `thermal_hist`（全程分佈），不是 `thermal_launch`（單點）**（2026-09-18 實測）：
  兩臂的 `thermal_launch` 可以**都是 HEAVY**，而 `thermal_hist` 一邊 `{HEAVY: 8}`、
  另一邊 `{HEAVY: 1, MODERATE: 7}` ⇒ 後者的 median 高了 **69%**（4.69 vs 2.78 t/s），
  而池統計完全相同（hit 82.7% 對 82.7%、io 1967.3 對 1966.2 MB）⇒ **那個差異全是熱態**。
  **不是同一種熱歷程的兩臂不可比，即使 `thermal_launch` 相同。** 要嘛等到穩定、要嘛看 `thermal_hist`。
  不通過的項目就只報家族＋區間，不要報點估計（lesson `eng-mh-0064`）。
  同粒度下 run-to-run 穩定（實測 11 個家族 0.89–1.02）**不代表**跨粒度穩定。
- **M3 的 40% 判準已經有三個獨立方法都是否定的**（2026-09-18）：聯集上界 ≤28.3% of busy、
  op 表 work-weighted 8.0／8.4%、名字表 work-weighted **3.2–6.3% of `wait`**。
  ⇒ **Cell 2（MoE gather 融合／batched-union）沒有量，不要再投。**
  而「MoE 的**逐元素合併**（`ffn_moe_`，7.6%）比 MoE 的**專家矩陣乘**（3.2–6.3%）還大」。
- **★ 統計的可採性（admissibility，2026-09-18 定義）：一個 `t/s` 或一個 `r` 在能被引用前要過三道**
  ① **同一個量**：可同池 ⟺ `cell_key = (profile, metric, n_prompt, n_gen, depth, n_batch, n_ubatch,
     spec_state, pool_bytes)` 逐項相等。判準是**量綱**（y 軸標籤必須每列一樣）——
     **prefill(`prompt_per_second`) 與 decode(`predicted_per_second`) 永遠不可同池，與 n 無關**。
     混池會把 |r| **抬 2–3.4×**（實測 `inactive` **+0.182 → +0.628**），而該 n 的臨界是 0.631
     ⇒ **只差 0.003** ⇒ **危險不是「它過了」，而是「它被抬到臨界線邊上」**；
     ⇒ 規則的正當性**不可依賴當下有沒有過線**，否則它會隨資料漂移。
  ② **三態**：`decided`（key 完整 ∧ n≥3 ∧ |r|≥r_crit）／`suggestion`（|r|<r_crit）／
     `cannot decide`（key 不完整 ∨ n<3 ∨ 沒有 arm 同時帶吞吐與環境讀數）——第三態是**要出聲的結果**。
  ③ **裁決要機器可讀，而且要附「餵了哪些檔」**：**一句 caveat 不是閘門**。輸出要含
     `{cell_key, metric, n, r, r_crit, verdict}` 與 `--json-glob` 的實際值
     （實例：一個「n=8」因為沒記 glob，多花一輪才復現）。
  工具：`scripts/check/caliber_env.py --memory --cell-filter <cell> --json-glob …`（2026-09-18 新增）。
  ⚠ 它**尚未**有「預設拒絕」與 `--json` ⇒ 目前是**榮譽制**，引用前自己確認三態。定義全文：
  `docs/HTTP_VS_BENCH_CALIBER_2026-09-18.md` §8（含四條可機檢條件 H1–H4）。
- **★ 說「兩條路只差在量測路徑」之前，先跑 `caliber_env.py --equiv`**（2026-09-18 實測打臉）：
  **prod25 判 NOT CONFIG-EQUIVALENT** —— server 側 `-b/-ub` **完全沒給**、bench cell 硬編碼 `-b 512`
  （`prefill250` 下同一格是 5632 vs 512，差 11×）。根因是 `prod_matrix.py:319` 的
  `b = ub = spec["batch"]` 蓋掉 `resolve()` 從 profile 算出的 BATCH/UBATCH。
  ⇒ 對齊前，跨路徑的 t/s 差只能叫「**路徑 × batch**」的合併效應，不能叫口徑差、更不能拿去換算。
  **另記**：`CGC_SERVER_MTP=0` 換出去的兩份 gguf 是**同一份 bytes**（size ＋ 5 個視窗 shasum 全同，
  不同 inode）⇒ 模型檔不是 confound；但 MTP=0 會讓 **8 個 engine env 整塊消失**
  （`CGC_MM_BITIDENT`／`CGC_DRAFT_DECODE`／`CGC_VERIFY_DECODE`／`CGC_NO_PREFETCH`／layer caps…）
  ⇒ **「MTP off」是一整組旋鈕，不是一個旗標**。

## 唯一的 decode 儀器：`llama-bench`（2026-09-17 **使用者裁定**；舊標題「兩個 decode 儀器不能並排」）

**`decode_bench` 已退休 —— 不要再量、不要再引用它的數字。** 理由是 2026-09-16 的實測：同 env／
同模型／同 n／同場交錯，`llama-bench` 與 `decode_bench` 差 **9.4 vs 12.4**（n≈128）與
**7.2 vs 18.7**（n=24），而且 `decode_bench` 的離散大得多（12.36／12.95／10.64 vs llama-bench
三次 9.42／9.32／9.38 ＝ **1.1%**）。與其維護兩套互不並排的口徑，**現在只認 `llama-bench`**。
（細節 `docs/INSTRUMENT_COMPARE_20260916_1821.html`）

**標準形狀 —— 報任何 decode 數字都要附這三樣：reps 數、warmup 規則、模型家族。**

- **`-d 512`**：`--depths 0` 是最冷的格子，**不要拿它當標準**。depth 的預填充在 `t_start`
  之前完成、不計時 ⇒ 只有 `-d ≥ 512` 才是暖平台。
- **warmup 保持 ON、並丟掉 rep1**（llama-bench 的 tg warmup 只有 1 個 token，
  `llama-bench.cpp:2392`）⇒ **報平台值，不報 `avg_ts` 的原始平均**。
- **模型家族必須指名，而且要看 `-m` 那一行、不是看臂名。**（2026-09-17 更正）
  `MTP=0` 確實會換檔案，**但觸發條件是「顯式設 `CGC_SERVER_MTP=0`」而不是 profile 名**：
  16:1x 用 `prefill_certifiability.py --dry-run` 逐字讀它印出的 `-m` 行，
  **`--arm prefill250` 與 `--arm prod25` 都載 `Nail-…-denseIQ4X.gguf`**；
  `ARMS` 表裡**只有 `prod25-stream-mtpoff` 設了 `CGC_SERVER_MTP`** ⇒ 只有它換成
  `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`。
  **⚠ `tag` 不含模型檔**（json 只存 tag）⇒ **每份產出都要記 `-m` 那一行**（matrix 已經會印），
  否則事後無法分辨。**後果（好消息）**：`prefill250` 與 `prod25-stream` 血統**同一個檔** ⇒
  decode 的 10.8–10.9 與 prefill 的 `pp2048` cell **是自洽的一對**。
- **暖平台 ＝ `10.78 / 10.91 t/s`**（`prefill250+SPAC=1`、`-b 512`、d512、NOMINAL）。
  歷史交叉驗證：09-15 `prod25-stream` **10.79**、今天 `prod25-stream` **10.89**、
  `prefill250+SPAC` **10.78／10.91** ⇒ **拿 10.8–10.9 當 25 t/s 的分母。**
- **★ 要對外可比，就得跑上游形狀的那一列（2026-09-17 追加）。** 上游 `llama-bench` 的預設是
  **`-p 512 -n 128 -d 0 -b 2048`**（`llama-bench.cpp:367-377`），標準輸出列是
  **`pp512` / `tg128` / `pp512 @ d512` / `tg128 @ d512`**（`README.md:180-187`）。
  我們的 `--depths 512` **逐字就是 `tg128 @ d512` 那一列**（`-p 0` 只是把 pp 列關掉）——
  但**我們的 decode cell 跑在 `-b 512`，上游是 `-b 2048`**，而我們的 prefill cell 跑 `-p 2048`。
  ⇒ **「對外說得出口」的 cell ＝ `--prompt 512 --gen 128 --depths 0 --batch 2048`**，
  而且要**另開一次獨立 run**（同一行程內第二格繼承暖池 ⇒ 不獨立）。

**⇒ 一句話的後果：可引用的 decode 值是 `10.8–10.9`，不是 `decode_bench` 的 `12.36`；
距 25 t/s 約 `2.3×`，不是 `2.0×`。**

**⚠ 推論（未定，等裁定）：MTP 不在這個口徑裡。** 歷史上的 MTP A/B（HTTP 路的 `12.62 vs 9.82`、
以及「accept 是被抬高的指標」那條）都是**伺服器／`llama-speculative-simple`** 量的 ⇒
在「只認 llama-bench」之下，那類結論**目前沒有 instrument of record**。兩條路：
**(1)** 把 `--spec-*` 加進 `llama-bench`（下面那節說只有三處要改，但那是 `src/`，歸引擎層）；
**(2)** 保留 `llama-speculative-simple` 當 MTP 臂，並**永遠不與 llama-bench 並排**。

**★★ 2026-09-18 補充（MTP 的池成本儀器 — 已存在，不要重寫）：**

要問「verify 的 union 有多少複用」時，**儀器早就印了**，位置是
`src/llama.cpp/src/llama-expert-cache.cpp:2529`（**不是 env-gated**，只在 `n_fast_calls > 0` 時於結尾印一次）：

```
llama_expert_cache: MTP fast path: calls=510 union=9316 cold(ZERO)=0 (0.0%)   verify: calls=474 union=9028 cold=0 (0.0%)   draft: calls=36 union=288 cold=0 (0.0%)
```

- `n = uni.size()`（`llama-context.cpp:6303` 傳的是去重後的 union）⇒ **`union` 已是去重後的個數**。
- **讀法**：`verify 的 union / verify 的 calls` = 每次 verify 的平均 union；**除以 `(1+n_max) × top_k`**（我們是 `4 × 8 = 32`）
  ＝ 複用率。實測 **9028/474/32 = 0.595** ⇒ 4 個 verify token 之間有 **40.5% 的專家複用**。
- **兩個現成的內部核定**（讀之前先跑）：`draft` 的 `union/calls` 必須 **＝ top_k**（我們 288/36 = 8.00 ✓）；
  且 `union` 必須 `< (1+n_max)*top_k`（19.05 < 32 ⇒ batch 真的是 4）。
- ⚠️ **陷阱（會讓兩份量測互相矛盾）**：**llama-bench 路徑下 `verify: calls = 0`**（所有 call 都被算進 `draft`），
  因為那條路的 verify 走 `ensure_batch`（真實填充 + LRU），**而 server 路徑的 verify 走 fast path（`touch`，no-fill）**。
  ⇒ **MTP 的池成本在兩條路上結構不同，不可並排**（`MTP ≈ 0`（llama-bench −4~−6%）與 `+5.7%`（HTTP）的落差有這個成分；
  **不是**跨模型檔造成的 —— 見下一條，那兩份配對其實都是同檔的）。
- **成本的主因不是單次 union**：`cold(ZERO) = 0` ⇒ 單次 verify 不打穿池。真正在動的是
  `layers_distinct_over_slots`（**3 → 11／18**）與 `capacity` miss（**218 → 2229，×10.2**）
  ⇒ **長期工作集超過 143 slots** ⇒ 對症的是「改淘汰／填充策略」，不是「增大池」。
- ⚠️⚠️ **陷阱（一句話就會把「MTP off/on」變成跨模型檔比較）**：`run_server.sh:153-161` 是
  `MODEL_DEFAULT="$Q36"` ＋ `if [ "$SERVER_MTP" = "1" ] → "$Q36_MTP_DENSEIQ4X"` ⇒
  **只設 `CGC_SERVER_MTP=0` 就會把模型換成 `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`**（沒有 nextn 頭的另一個檔）。
  `CGC_DUMP_ENV=1` 逐字驗過（兩次的 `CGCENV MODEL` 不同）。
  ⇒ 影響面：`decode_sweep.py` 的 `p25-mtpoff`／`p25-gputime`／`p25-mtpoff-phase`／`p25-phase-w32`
  與 `llama_bench_matrix.py` 的 `prod25-stream-mtpoff` **全部跨檔**；
  `mtp_accept_ab.py` 的 `nail_nomtp`（`:122` **顯式釘 `MODEL=Nail`**）與 llama-bench（`-m` 顯式）
  **才是同檔** ⇒ 只有它們的配對算數。
  ⇒ **要同檔配對就必須顯式設 `CGC_SERVER_MODEL`**（`decode_sweep.py` 的 `p25-nail-mtpoff` 就是為此存在）。
  ⇒ 任何 MTP off 臂**開跑前**先比兩臂 ctrl log 的 `CGCENV MODEL` 那一行；跑完也要比。

要配對，env 必須是解析出來的而不是手抄的——`llama_bench_matrix.py` 支援 `PROFILE:ENV=VAL`：

```sh
python3 scripts/check/llama_bench_matrix.py \
  --arms 'prod25:CGC_SERVER_MTP=0;CGC_GPU_TIMING=1;CGC_DECODE_PROFILE=1' \
  --prompt 0 --gen 128 --depths 0,512 --reps 3 --json Backup/phase_decomp/lb_x.json
```

（這一支帶 `CGC_GPU_TIMING/CGC_DECODE_PROFILE` 是為了**逐步分解／歸因**，不是 headline；
headline 用上面那條 `--depths 512` 的標準形狀。`-d 0` 留著只為了看冷格。）

逐字就是 `decode_sweep --arms p25-gputime` 的 env（兩邊都經 **`CGC_DUMP_ENV=1` 環境變數**下的
`run_server.sh` —— 注意那是**環境變數**，不是 argv；弄錯會真的啟一個 13 GB 的 server，見下文）。

**★ 分隔符是冒號，不是分號 —— 弄錯會偽裝成「靜默空跑」**（2026-09-17 實例）：
`PROFILE` 與第一個 env 之間是 **`:`**，env 彼此才是 `;`。整串因此**必須含一個冒號**，否則
`llama_bench_matrix.py:492` 的 `if ":" in spec` 為假 ⇒ `:498` 拋 `unknown arm '...'`。

它偽裝得極好，因為 `raise SystemExit` 發生在 `run_arm()` 的無條件落檔（`:367-368`）**之前** ⇒
**cell 目錄全空**、wall **0.0s**、`rows=[]`、`build_commit=None`，看起來像「跑了但沒輸出」。
實例：`prod_matrix.py:234` 曾用 `";".join(...)` 拼出沒有冒號的臂，於是**所有** `--extra-env`
呼叫都在 0.05 s 內死掉（`/tmp/decode_ab` 臂 B、`/tmp/gpu_split` 臂 A 都是），而我一度把它
歸因成「MTP=0 特有的失敗」。

⇒ **遇到「空目錄 ＋ 0.0 s」時，下一個要看的地方是 `prod_matrix` 那份 summary JSON 的 `error`
欄位**（`prod_matrix.py:280-281` 會把 child 的 stdout+stderr 尾 1500 字存進去），
**不是 child 的 log —— 那份根本沒被建立**。

**★ `CGC_DUMP_ENV=1` 是「環境變數」，不是「參數」** —— 弄錯的代價是一次完整的 13 GB 載入：
`bash scripts/run_server.sh CGC_DUMP_ENV=1`（argv 形狀）**不會**進 dump 模式，而是**照常啟動 server**
（2026-09-17 23:26 實例：pid 16194、RSS **7.89 GB**、LISTEN 8080、存活 2:08，得手動 TERM）。
正確形狀是 `CGC_DUMP_ENV=1 bash scripts/run_server.sh`；`llama_bench_matrix.py:205` 用的就是
`env["CGC_DUMP_ENV"] = "1"`。⇒ **`run_server.sh` 把「查環境」與「啟動」放在同一支腳本裡，
形狀錯了沒有中間狀態。**

**⚠ 而且 `resolve()` / `prod_matrix.py --dry-run` 都「不是唯讀」—— 它們會殺別人的行程。**
`run_server.sh` 有一個 preflight，會 **SIGTERM 任何它找到的殘留 llama 行程**
（`[preflight] 發現 1 支殘留 llama 行程，先清乾淨再起 server`）。2026-09-17 23:31，一次
「零 GPU 讀 env」的 `resolve()` 呼叫**殺掉了鄰居正在跑的 llama-bench**（他們 30 秒後自動重試）。
`prod_matrix.py` 的 docstring 寫「Zero GPU: this is `run_server.sh CGC_DUMP_ENV=1` plus arithmetic」
—— **那句話是錯的**：它會摧毀鄰居的視窗。它的閘門（`--no-gate` 之外那個）保護的是 **cell**，
**不保護 `--dry-run` 或裸 `resolve()`**。
⇒ **別人可能正在量測時，不要呼叫 `resolve()`，也不要跑 `--dry-run`。** 要讀 env 就讀本 skill
或 `Backup/` 的既有 dump，或先確認沒有任何 llama 行程且對方明確 idle。

### `full-mtp` 的記憶體守衛，以及「不要用 prod fallback 繞過它」

`run_server.sh` 的守衛（讀碼，不是推論）：

| 位置 | 內容 |
|---|---|
| `:552 cgc_memory_guard_class` | `MTP=1 && NGL>=90 && CTX>=3072` ⇒ **`full-mtp`**（`SERVER_PROFILE=legacy-25plus` 才是 `…-known-profile`）；MTP=1 其他 ⇒ `fallback-mtp`；否則 `baseline` |
| `:566 cgc_memory_guard_req` | `full-mtp` ⇒ 只要求 **`free_pct ≥ 40`**；`…-known-profile` 35；`fallback-mtp` 20；`baseline` 15 |
| `:801` | `FREE_PCT` 來自 **`memory_pressure -Q`**，**不是 `vm_stat`** |
| `:816` → `:599 cgc_apply_prod_memory_fallback` | `CGC_SERVER_MEMORY_MODE=prod` ＋ `full-mtp` ⇒ **`CTX=1024`、`NGL=8`、`BATCH=64`、`UBATCH=32`、`BUDGET=0`** |

⇒ **`CGC_SERVER_MEMORY_MODE=prod` 不是「讓 MTP-on 跑得起來」的辦法**：它能把行程拉起來，但會把
cell 換成另一套配置（**8 層上 GPU、沒有 expert pool**）⇒ 那不是生產 cell，量到的數字不可用。
**正確的前提是等 `free_pct ≥ 40`。**

**而 `memory_pressure -Q` 的 free_pct 極不穩定**：同一台機器 2026-09-17 23:25 讀 **34%**（守衛擋掉）、
23:28 讀 **72%**（放行）—— 三分鐘內翻面。所以「現在能不能跑 MTP-on」**要用 `memory_pressure -Q` 問**，
不要用 `vm_stat` 的 `Pages free`（它當時只有 ~80 MB，會得到相反的答案）。

**`--depths 0` 是最冷的格子，不要拿它當標準。** 歷史上的「llama-bench tg 10–13 t/s」是
**depth 512–2048**，而 depth 0 一直是 **8.7–9.8**（2026-09-16 重跑：d0 **9.52**、
d512 **10.89**，重現 09-15 的 9.65/9.79 與 12.83/10.79，誤差 1.5–3%）。
`-d` 的前置填充在 `t_start`（`llama-bench.cpp:2444`）**之前**完成、不計時，所以 depth ≥512 的
實例自帶一份前置填充，d=0 沒有。**機制未確立**——同協定下 `tg@d0` 前面也有一個 `pp 512`，
所以不是單純的前置填充，也不能只歸給池。**報 decode 一律附 depth**，而且 `0,512,1024` 起跳。

**也要附「哪個臂」——本專案有三個 decode 相關的臂，只差 6 項 env，但其中一項是機制級的**
（見 `Backup/cgc_logs/instr_compare/arms_matrix_20260916.tsv`，由 `CGC_DUMP_ENV=1` 現場解析）：

| arm | CTX | -b/-ub | PREFILL_STREAM | **SPAC** |
|---|---|---|---|---|
| `prod25`（decode sweep 基準 `p25-gputime`） | 4096 | 模型預設（llama-bench 推 8/8） | - | **1** |
| `prod25-stream`（歷史 depth 矩陣；「10–13」出自此） | 4096 | 512/512 | 1 | **1** |
| `prefill250`（prefill 250 交付用） | **8192** | **5632/5632** | 1 | **-（全域預設關）** |

`CGC_SPAC` 全域預設關（`run_server.sh:1635-1637`），只有 prod25 血統自己開；而 prod25 的註解寫
「SPAC 同時是 membership 驅動，**關掉後 decode 工作集不駐留**」⇒ **prefill250 缺的是 decode 的
駐留機制，不只是 batch**。**prefill 的 profile 不能當 decode 的標準，也不該用 prefill 的 t/s 講 decode。**
`prefill250` 至今**沒有任何 decode 數字**：唯一嘗試（`llama_bench_prefill250.json`，`-b 6144`）是零列殘檔，
死於 `test_prompt: failed to decode prompt batch, res = -3`（GPU OOM）——**它的 batch 與 depth 矩陣不相容**。
要一個 profile 同時服務兩者，那是**新配置**（至少 `CGC_SPAC=1`），需要自己的基準。

**要選「哪個臂最有潛力」，判準是「剩下來的槓桿有沒有地方跑」，不是現在的 t/s。**
`CGC_POOL_MAX_TOKENS`（`src/llama.cpp/src/llama-graph.h:18-37`）預設 **8**、可調 **[2,64]**，
而它的註解自己把用途寫成「**Default 8 covers MTP n_max up to 7 (verify = n_max+1)**」；
`llama-context.cpp:286-291` 則寫「**The clamp is NOT lifted for an owned pool**」。
⇒ `prod25`（無 PREFILL_STREAM）把池路徑的批寬鎖在 8；放寬它就要在 **143 slots/layer** 裡裝下更寬的
expert union（worst layer 248 distinct vs 143 slots）。而 **M1（batched-union gather）與 M4（verify 真批次）
住在「寬 batch」的世界，那需要 whole-layer slab（`ne[2]=n_expert`，裝得下 256 個）——只有
PREFILL_STREAM 的臂有**。⇒ 三個臂裡潛力最低的是 `prod25`，最高的是 `prefill250`＋`CGC_SPAC=1`。

**已定案（2026-09-16）：統一在 `prefill250` ＋ `CGC_SPAC=1`（alpha 0.75）。**
否證實驗是 `Backup/run_unified_ab.sh`（交錯 A/B/A/B、每臂等 NOMINAL、
`-b 512 -p 0 -n 128 -d 0,512 -r 3`）；結果在 **d512**：

    SPAC 關  9.25 ±2.97  逐 rep 6.28–12.21（1.94×）
    SPAC 開 10.78 ±0.04 / 10.91 ±0.47  逐 rep 10.74–10.82（0.7%）   配對中位 +1.60（+17%）

**SPAC 主要不是把均值推上去，是把「decode 工作集不駐留」造成的不穩定拿掉。在 d0 上兩臂重疊**
（A 的 reps 自己就跨 5.95–10.58）⇒ **只有 d512 是決定性的。**
暖值交叉驗證：09-15 `prod25-stream` 10.79、今天 `prod25-stream` 10.89、今天 `prefill250+SPAC` 10.78／10.91
⇒ **本機 llama-bench 暖 decode ≈ 10.8–10.9 t/s**（拿它當 25 t/s 的分母，不是 d0 的 9.4）。
量測形狀：prefill 用 profile 的 `-b/-ub 5632`；decode/depth 矩陣用 `-b 512`（5632 會 OOM）。

**llama-bench 的 tg 沒有 `srand`**（`llama-bench.cpp` 只有 `std::rand()`）⇒ **每次跑餵的 token 流逐位相同**
（指紋：同 config 兩跑的 avg 與 stddev 可以逐位相同）。好處是 A/B 的工作負載完全相同、只剩時序；
壞處是它不是「一個隨機流」而是**一個固定的偽隨機流**，所以它與被服務請求的差異是**系統性**的。

**四個讓差距看起來像引擎快慢、其實不是的東西（按重要性）**

1. **第一個 rep 是冷的。** llama-bench 的 tg warmup 是 `test_gen(ctx, 1, ...)`（**1 個 token**，
   `llama-bench.cpp:2392`），而 context／池每 instance 建一次 ⇒ 池從空開始；
   `avg_ts` 把冷 rep 平均進去，`decode_bench --warmup 1` 恰好丟掉對應輪。
   n=128 的 rep1/平台 ＝ **1.35×**（120 vs 89 ms）。**丟掉後 11.3 vs 12.4 ＝ 1.10×。**
   ⇒ 報 llama-bench 一律附 rep 數與 warmup 規則；報 decode_bench 一律附 round 數與 `--warmup`。
2. ~~**它對 MTP 是瞎的。**~~ **2026-09-19 更正：這一條已作廢，別再引用。**
   `8194a2ba4`（09-18 20:24）在 `test_gen_spec` 補上 `llama_context_set_cgc_phase(ctx, CGC_PHASE_VERIFY)`
   （＋decode 後 fail-closed reset）。病因是 fast path 的閘門為 **caller 設的 phase**
   （`llama-context.cpp:6065`），而全樹只有 `server-context.cpp` 與 `speculative.cpp` 兩個 caller
   ⇒ 修前 bench 的 verify 批次一律掉回 `ensure_batch` 精確路徑。
   **已驗證生效**：四支帶 spec 的 run `verify: calls` 全部非零、`union/call` **16.30–18.15**
   （server 側參考值 18.69）；修前症狀是 `verify: calls=0`、`union/calls = 8.00`。
   ⇒ 現在 `--spec-type draft-mtp --spec-draft-n-max 3` 在 llama-bench 上可直接量 MTP。
   ⚠ 只有**修復前**（09-18 上午及更早）量的 bench MTP 數字要打折；
   `m=0.474`（09-18 §EN-187）等在此之後 ⇒ 不受影響。
3. **兩個 token 流給池的壓力結構不同**：隨機 id → 超訂 **7/40** 層、miss **85.4% compulsory**；
   連貫文本 → **40/40** 層、**51% compulsory / 49% capacity**。所以
   **hit%／miss／reads 不可跨行程比**（生命週期累加器，累加的工作不同）；
   可比的只有行程內部定義的比例。命中率高的那一邊反而慢（96.0% → 9.4；92.6% → 12.4）。
4. **`MTP=0` 會換模型。** `run_server.sh:154` 的 `if [ "$SERVER_MTP" = "1" ]` 才選 MTP 家族
   ⇒ MTP=0 的臂載入 `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`，不是 `Nail-…denseIQ4X.gguf`。
   引用「prod25 的 decode」時必須指名是哪一個。

### 要量 MTP 時：`llama-speculative-simple`，不要動 llama-bench，也不必開伺服器

投機迴圈在 `common/speculative.{h,cpp}`（`_init/_draft/_process/_accept/_print_stats`，另有
`common_speculative_need_embd_nextn`），全 repo **只有兩個呼叫者**：`server-context.cpp` 與
`examples/speculative-simple/speculative-simple.cpp`。後者用 `common_params_parse` ⇒ 吃
**逐字相同**的 `--spec-type draft-mtp --spec-draft-n-max 3`（`run_server.sh:1137` 就是這樣餵
llama-server 的），**內建無投機的基線臂**（原始碼註解 `C0 baseline arm`），本專案已把兩筆 MTP
修復提交給它（`b7364f886` bit-identical spec vs non-spec／`eb16bd129` chunked prefill），而
`check_build_tracked.sh` 早把它列為關鍵 exe。llama-bench 用的是**自己的**解析器
（`llama-bench.cpp:509` 的 `parse_cmd_params()`，內部 `arg_prefix = "--"`），所以 `--spec-*` 對它無效。
**「不要動 llama-bench」的判準不是「做不到」**：`libllama-bench-impl` 已經 link `llama-common`
（`tools/llama-bench/CMakeLists.txt`），`common/speculative.h` 就在手邊，要加也只是三處——參數解析、
`test_gen()` 的 token 來源、以及 `avg_ts` 的 token 定義（投機下應該是含接受的 `n_predict`，不是 `n_gen`）。
真正的理由是**做完會多出第三套不可並排的口徑**（本節開頭那條）。除非目標改成「llama-bench 當唯一的
instrument of record」，否則不要動它。

```sh
bash Backup/run_spec_simple.sh                 # MTP 臂
SPEC_TYPE=off bash Backup/run_spec_simple.sh   # 基線臂（同一支工具、同 env）
```

三個坑（2026-09-16 實測，都已寫進 runner 的註解）：
- **`-c 0` 會 OOM**（模型預設 32768 × 13.66 GiB 模型 ＋ 8 GiB 池）⇒ 用 profile 的 4096。
- **profile 的 CGC 旋鈕是必需的**（`CGC_N_CB=8`／`DBUF`／`NO_PREFETCH`／`OA_ASYNC` 是記憶體形狀）；
  一個都不給 → 連 `-c 4096` 都在 t=16.7 s OOM。**env 一律從 `run_server.sh CGC_DUMP_ENV=1` 取。**
- **`--spec-type none` 不是基線**：它會去載空路徑的 draft model（`failed to load draft model, ''`，
  exit 1）。基線是**完全省略** `--spec-type`。

### 第四條路：`llama-bench` 內的 MTP（`--spec-type draft-mtp`，2026-09-18 **可用**）

**★★ 最重要的一條（2026-09-18 真因，1 行）：batch 的 `n_past` 必須從
`llama_memory_seq_pos_max(...) + 1` 開始，不是 0。**
`test_gen_spec` 明確指定 batch 的 pos（`common_batch_add(batch, id_last, n_past++, …)`），
而非 spec 的 `test_gen` 用 `llama_batch_get_one()` 讓 llama 自己配位置 —— 所以只有這條路徑要自己知道位置。
llama-bench 每 instance 的順序是 **target ctx → warmup → depth prefill(`-d`) → prompt → gen**，
所以 `-d 512` 已經寫過 pos 0..511；`n_past` 從 0 重來時**第一個 verify batch 的 `llama_decode` 靜默回 -1**
（它不印任何訊息）。

**★★ 症狀會誤導**：stderr 只有 `verify decode failed: ret=-1 n_tokens=4(pos 0..3) n_ctx=768 n_past=1 draft=3`
與 teardown 的 `CGC-M2-UNREPOINT: … a second context built from this model would otherwise read a
freed buffer` ⇒ 看起來像 Metal／記憶體／「第二個 context 踩到 freed buffer」。
**判別式**：`-n 16 -d 0` 過、`-n 128 -d 512` 掛、`-n 128 -d 512 --no-warmup` **也**掛 ⇒ 觸發條件是
**depth prefill**，不是 warmup。**把 draft context 的建立提前到 warmup 之前沒有用**（2026-09-18 用一次
真的建置與一次真的執行否證了那個假設；位移本身保留在樹上，因為它與 server 同序，但**它不是原因**）。

`tools/llama-bench/llama-bench.cpp` 的 `test_gen_spec()`；設計與四個坑見
`docs/MTP_INSTRUMENT_PLAN_2026-09-17.md` 的「實作結果」節。摘要（每一條都是跑出來的）：

- **★ argv 不能自己組。** 只帶 `-m` ⇒ 少了 profile 的 `--load-mode none` ⇒ **每一次**都在第一個
  decode 的 `CGC-METAL-FAIL: command buffer 8 failed (status 5, Insufficient Memory)` abort，而
  8 GiB vs 2 GiB pool、`-b 256` vs `-b 512`、swap 8.7–12.4 GB **全都一樣**（很容易誤判成環境）。
  正解 `forward_argv(resolve(profile, {})['server_argv'])` —— **argv 與 env 一樣只有一條解析路徑**。
- **`llama_model_params.load_mtp` 預設 false** ⇒ qwen35moe 的 loader 用 `TENSOR_SKIP` 建 MTP 區塊
  （`qwen35moe.cpp:45`）⇒ `graph_mtp` assert `layer.nextn.eh_proj`（`:566`）。server 是靠
  `--spec-type draft-mtp` 經 `common.cpp:1635` 打開的，llama-bench 沒有那條路 ⇒ 要在
  `to_llama_mparams()` 自己設。
- **`n_ctx = n_prompt + n_gen + n_depth`**（llama-bench 自己算的）⇒ `-n 16 -d 0` 的 ctx 只有 16；
  verify 批次要 `1 + draft.size()` 個空位 ⇒ 短形狀會撞牆（生產 cell `-n 128 -d 512` ⇒ 640，有餘裕）。
- **`bin/llama-bench` 的 md5 不變不代表沒重建**：它只是 33 KB 的 stub，程式在
  `libllama-bench-impl.dylib` ⇒ 新鮮度要看後者。
- **部分接受時不要直接 `llama_memory_seq_rm`**：實測第三輪 `llama_decode` 靜默回 **-1**
  （`common_context_can_seq_rm` 卻回報 FULL）。參考 `examples/speculative-simple` 的
  `common_prompt_checkpoint` 路徑（`:570-593`）；`LLAMA_BENCH_SPEC_NOTRIM=1` 可把這一項單獨拿掉來定位。

**★ 但上一輪那個 −23% 的讀數已經被推翻（2026-09-18 00:48）。** 那個 A/B/A 的兩個控制臂
（10.049 / 6.791）自己差 48% ⇒ 分母不可用。用**配對 + 反序對**（10 臂）重做之後：
正序 ratio 1.071/1.171/1.013、反序 0.938/0.701 ⇒ **順序校正後的 MTP 倍數 = 0.937（−6%），
而 5 對的範圍是 0.701–1.171 ⇒ 與 0 不可區分**。
⇒ **`llama-bench` 的 `decode` cell 在生產 pool 上是 9.7–10.6 / 8.0–10.2（隨窗口），
而 MTP 在這個形狀上沒有可測的淨增益。** 現在可引用的只剩計數器（見下）。

**輸出（2026-09-18 起）**：`llama-bench` 的 JSON（`avg_ts` ＋ `samples_ts`）＋ stderr 的
`CGC-MTP-PERF type=draft-mtp calls_draft=… acc_rate=… gen_tok_per_round=… emit_tok_per_round=… ms_per_round=…`。
選法是 **`--spec-type draft-mtp` ＋ `--spec-draft-n-max <n>`**（22:20 由另一條線做成正式 CLI；
`LLAMA_BENCH_SPEC=1` 降級為 deprecated alias）。`--spec-type` **不是**註冊的 cell：
`--cells decode` 仍然 by construction 是 MTP-off（`cgc_spec_on == false` ⇒ 走原 `test_gen`），
而這正是它必須保持的（與上游 `tg128 @ d512` 逐位元可比）。

**可引用的計數器（10 個 on 臂）**：`gen_tok_per_round = 3.000`（每臂都吃滿 `n_max=3`）、
`acc_rate` **0.626–0.795**（變異 27%！）、`emit_tok_per_round` 2.878–3.385、
`ms_per_round`（**只是 draft head**，不含 verify）25.0–31.1、池 `hit rate` **93.7–94.8%**
（off 臂 96.0–96.1% ⇒ **MTP 讓命中率降 2.2pp**）。

**★★★ 但 `llama-bench` 的 MTP 只接了一半：它的 verify 不走生產的 fast path（2026-09-18 19:0x）**
`llama-context.cpp:6065-6090` 的門是
`getenv("CGC_VERIFY_DECODE"|"CGC_DRAFT_DECODE") && cgc_phase ∈ {VERIFY,DRAFT} && ctx_type/n_tokens 條件`，
而 **`cgc_phase` 由呼叫端在每次 `llama_decode()` 前設定**（該處註解逐字：*UNKNOWN is the safe default:
when caller forgets to set phase, we fall back to exact path*）。**`llama-bench.cpp` 對 phase 的引用是 0**
⇒ bench 的 target 多 token verify 永遠是 `UNKNOWN` ⇒ 走 `ensure_batch` 的精確填充路徑。
日誌直接印出來（`Backup/prod_matrix/20260918_15*_prod25_decode-spec/*.stderr.log`）：

```
MTP fast path: calls=395 union=3160   verify: calls=0 union=0   draft: calls=395 union=3160   ← llama-bench
MTP fast path: calls=9497 union=170202 verify: calls=8819 union=164778 draft: calls=678 union=5424  ← server
```

`union/calls = 8.00` ⇒ bench 那 395 次**全是單 token 的 draft**，多 token verify 一次都沒進來。
⇒ **bench 的 verify 與生產的 verify 走不同分支**（server 的 verify 是 18.69 experts/次、走 fast path）。
⇒ 這才是「bench 量到 MTP ≈1.0 vs server 量到 ×0.695」的真解釋：**不是量到不同的數字，是量到不同的東西**；
也解釋了上面那格 `ms_per_round` 為什麼「只是 draft head」——**verify 那一段在 bench 裡沒被分開量**。

**⚠ 但「走 ensure_batch」≠「一個一個丟」（19:1x 更正一處容易讀錯的措辭）**
兩條路都**用本層 union 整批**處理：`llama-context.cpp:6237-6260` 的 union 在分支**之前**就算好，
非快路徑是 `ensure_batch(cache, il, cold.data(), cold.size(), …)` —— **一批一次**；
而「串行」是**可切換的舊行為**（同處註解：`CGC_SYNCFILL_SERIAL=1 restores the old serial loop for A/B`），
**預設批次，且兩次量測都沒設它**（`run_server.sh:1504-1511` 只在明示時轉發）。
日誌的填充粒度兩邊逐位相同：**`read shape` 的 job 都是 0.37 MiB**（bench jobs=39282／server 8400）。
真正的差別是**策略**：fast path（`touch`）只 LRU-touch **已 resident** 的專家、**不 fill 不等**；
exact path（`ensure_batch`）**先把 cold 補齊再寫 remap**（有 IO／等待）。
**這兩條的貴賤沒有量過** ⇒ 只能說「bench 的數字不可搬去生產」，**不能**說 bench 偏樂觀或偏保守
（那個 14.08 GiB vs 3.01 GiB 是兩個不同 fixture，不能正規化成「每 token」比）。
（要判貴賤，得先有下面那支 ctx 標籤。）

**⚠⚠ 這個洞不是「傳個參數」能補的，而且修法方向容易被想反（19:1x）**
- **`--spec-type draft-mtp` 不是那個開關**（它有效，MTP 真的跑了）。它管的是**引擎/模式**；
  缺的是 **phase**，而 phase **沒有任何 CLI／env 表面**：唯一入口是
  `llama_context_set_cgc_phase(ctx, phase)`（`llama-ext.h:154` 的 `LLAMA_API`），
  **每次 `llama_decode()` 前由呼叫端設**。`CGC_VERIFY_DECODE` 只是 `getenv(...) != nullptr` 的**許可**。
  全樹呼叫者只有兩處：`tools/server/server-context.cpp:3897`（server）與
  `common/speculative.cpp`（**只設 `ctx_dft`**：DRAFT／CATCHUP／UNKNOWN）；**`llama-bench.cpp` 零**。
- **修法方向**：verify 要進的是 **`verify_fast`**（`phase==VERIFY && ctx_type==DEFAULT`）——
  **不是** draft 分支（`draft_fast` 要 `ctx_type==MTP && n_tokens==1`，verify 是 DEFAULT 的多 token）。
  作法＝在 target verify decode 前呼叫同一支 API，**分類邏輯照抄**
  `server-context.cpp:3845-3897` 的 `classify_batch_phase()`（掃 slot 狀態、**`n_phases==1` 才回傳**、
  混了回 `UNKNOWN` fail-closed、**phase 在 batch 層算一次**而不是每個 view 重算）。
  **不要另寫一份判斷**（兩份真相必然漂移）。
- **★ 為什麼這個洞能存在**：`common/speculative.cpp` 這支**共用** spec 庫**本身不設 VERIFY** ⇒
  就算讓 bench 改用 `common/speculative` **也不會**解決；VERIFY 只存在於 **server 私有 decode wrapper**。
  ⇒ **洞在「共用庫」與「server 私有 wrapper」的交界**；這類交界的東西最容易兩邊都以為對方有做。
- **第二個會擋的閘門**：`cgc_fast_eligible` 還要 `!warm_gate`（`CGC_WARM_NPAST` **預設 2048**），
  所以 `-d 512 / -d 0` 這種 bench cell 就算 phase 設對也會被擋。**但實測 bench 的 env 已是對的**
  （日誌 `CGC-WARM verify n_past=0 warm=0`；`run_server.sh:2035-2040` 在 `denseIQ4X=1` 時顯式設 0，
  因為 bench arm 吃的是 `resolve()` 的同一份 env）⇒ **缺的只有那一行呼叫**。

**★ 啟動必跑參數的權威清單（不要背清單，去問腳本）**：
`CGC_DUMP_ENV=1 CGC_SERVER_PROFILE=prod25 ./scripts/run_server.sh`（**只解析、不起 server**，
`run_server.sh:746-804`）逐行印 `ENV …`／`ARG …` ⇒ 那是完全解析後的清單；
**不在 `SERVER_ENV` allowlist 的變數會被靜默丟掉**，所以「文件上寫的」≠「真的傳進去的」。
三層分類（必跑 7 個 env／效能口徑／三個通用陷阱）、以及 **`--temp` 在 argv 裡出現兩次 ⇒ 後面的 0.4
生效、MTP 塊的 `--temp 0` 是死旗標**（server 自己會印 `W DEPRECATED: … only last value will be used`），
全部記在 `docs/MTP_LAUNCH_REQUIRED_PARAMS_2026-09-18.md`。
**不要在本 skill 再抄一份清單** —— 兩份必然漂移（本 repo 已為此付過學費）。
**機檢判準**：清單只證明「應該傳了什麼」；有沒有接上要看 launch 後的
`MTP fast path: … verify: calls=<>0> … draft: calls=< >0>` —— **兩類都要非 0**。

**★ k 的可掃旋鈕 ＝ `CGC_SERVER_MTP_N_MAX`（免改 `src/`，prod25 內部寫死 3、外部可覆寫）**。
2026-09-18 19:04–19:05 **同小時 A/B**（同命令、同請求）：

| 臂 | mean_len | ms/step | verify union/次 | **experts/token** | t/s |
|---|---|---|---|---|---|
| MTP off | 1.00 | 85.1 | —（無 fast path） | **8.00** | 11.75 |
| **k=1** | 1.72 | 167.3 | 12.27 | **7.13** | 10.28 |
| **k=3** | 2.31 | 259.5 | 18.61 | 8.06 | 8.90 |

- ⇒ k=3 步時比 k=1 高 55%、每 token 收益只多 34% ⇒ **k 的最優點在 3 的左邊**（k=2 未量）。
- ★ **k=1 的 experts/token 7.13 ＜ MTP off 8.00** ⇒ 投機在「專家讀取」軸上**本來有賺（−11%）**；
  它仍然更慢（10.28 ＜ 11.75）⇒ 差額只能是**每步多跑的那一次前向（draft pass）**。
  ⇒ **轉正判準可以寫死：`draft pass 的固定成本 ＜ verify 省下的 expert 讀取成本`**。
- **陷阱（我自己踩過）**：拿不同時段的 `ms/step` 相減會得到相反的結論。跨環境比會給出
  「union −34% 而步時不動」的假象；**同小時**一比，步時確實隨 union 漲（+19.1／+14.5 ms per expert）。
  **同 config 跨時段離散可達 ~1.4×** ⇒ 單點 MTP 數字不可引用，只認配對設計。
- **缺的儀器（「verify 還能省多少」的前置）**：`llama-context.cpp:1971` 的 `CGC-PHASE` 已印每次 decode 的
  `compute=`/`n=`，但**沒有 ctx 標籤** ⇒ MTP-on 每步兩次 decode（draft `n=1`、target `n=1+k`）
  **混在同一個平均裡**。加 ctx 標籤（或按 `n_tokens` 分桶）之前，那個問題無法回答。

⚠️ **`accept` 依取樣、prompt 與臂而變**：llama-bench 預設取樣（temp 0.8、top_k 40、top_p 0.95）下
量到 0.626–0.795；served 生產 prompt 下是 38.5% ⇒ **兩者不可比**，而且**單一 accept 讀數的變異
（27%）比「≥60%」這個門檻還寬** ⇒ 它不該當閘門。

★ **機制（代數吻合，非直接量測）**：`acc_tok_per_round = n × p`（實測 `2.023 = 3 × 0.6744`，逐位吻合）
⇒ MTP 的草稿是**平行**產生、各自與 target 比對的 ⇒ **加大 `n_max` 的邊際收益是線性的**
（不像 chain 投機那樣衰減）⇒ `n_max` 掃描是值得做的下一件事。

**歷史（A/B/A，2026-09-18 00:26）—— 已被上面的配對實驗取代，三個數字都不可引用**：
`off1` 10.049 / `on` 7.715 / `off2` 6.791。留著只是為了讓「為什麼舊紀錄寫 −23%」有出處。
附帶一個仍然成立的事實：**那兩個控制臂的池統計幾乎相同**（hit 96.6 vs 96.7%、file_reads 26925 vs 26685）
⇒ 那次漂移**不是池**造成的。

**MTP 的池副作用**（兩個 off 臂一致 ⇒ 非漂移；10 臂的配對實驗也重現：on 的 hit 率 93.7–94.8% vs
off 的 96.0–96.1%）：`file_reads` **+62%**、`bytes` **+129%**、`hit rate` **−2.2 ~ −3.2pp**、
`resident` **+277 MiB**。

**設計這種矩陣時**：`ARMS["prod25"]` 裸臂**不帶** `CGC_DECODE_PROFILE`，所以它只有 t/s、
沒有每步分解；要分解得用 `prod25:CGC_GPU_TIMING=1;CGC_DECODE_PROFILE=1`（不要加 `MTP=0`）。
`llama-bench` 側的散熱讀數用 `thermal_pressure.Sampler`（2 Hz 背景執行緒）——
它是一個獨佔 GPU 數分鐘的子行程，沒有「每個請求」可以掛讀數。

**跑多臂時，每臂之間要等讀數回 0。** 本輪的教訓：四個臂連續跑，第 2 臂起就是 HEAVY
（llama-bench −7%、decode_bench −14%），於是「加長會變快」那條預測**無法判讀**。
滿載後約 **35–47 s** 回 NOMINAL。**不要**用 `--no-warmup` 去對照：它在 `-p 0` 上是 no-op
（9.40 vs 9.38），因為 warmup 只有一個 token。

## 輸出擷取（kernel-side tensor capture）：四條規則，缺一條就會讀到假結果

`CGC_TENSOR_CAPTURE=<逗號分隔的節點名清單，或 `*`>`（＋`CGC_TENSOR_CAPTURE_WORDS`，預設 32）
把指定節點的**輸出張量**快照進一個不受 allocator 管理的 shared buffer，**在 synchronize 點**才印出。
`CGC_IDS_CAPTURE=1` 會被自動帶上，因為 ids 列是比較器切 graph 的標記。
實作在 `src/llama.cpp/ggml/src/ggml-metal/ggml-metal-ops.cpp`；分析器 `Backup/analyze_capture_nodes.py`；
驅動 `Backup/run_ids_dst_capture.sh`。

1. **同臂對照是引用任何跨臂結果的前提。** 同一個臂跑兩次、互相比對自己。
   第一版沒有記憶體屏障時，同臂**每個 graph 都 DIFF**——而跨臂跑出來的结果**看起來正好證實**
   正在測的假說（一個自信的假陽性）。屏障在儀器裡（`ggml_metal_encoder_memory_barrier`），
   但每次擴大節點清單都要重跑對照。成本：兩趟約四分鐘。
2. **命名規則是 `node(idx + n_fuse - 1)`，不是 `node(idx)`。** 可融合的 dispatcher 結尾會把
   `bid_dst` 重新指向**融合群的最後一個節點**；不可融合的以常數 `return 1;` 收尾。同一個運算式兩種都對。
   沒有這條就得追每條內部路徑（`mul_mat` 一個有八處 `set_buffer(..., bid_dst, ...)`）。
3. **`ABSENT` 不是 `SAME`。** 節點只有在融合群結束於它時才被擷取，而融合狀態取決於節點順序
   （不穩定）。實測：`norm-2` 在一個臂 41/41、在另一個臂 **0/41**。分析器把 `ABSENT`（附 `present=N`）
   與 `never` 分開印——**一個名字在其中一份日誌裡不存在，永遠不可讀成「相同」**。
4. **★ 32 詞的窗口從 element 0 起算 ⇒ 不同節點讀到不同 token，不能互相對照。**
   ggml 張量是 **ne0 最快**，所以「前 32 個 element」的意思取決於張量形狀。實測幾何（T=8）：

   | 節點 | ne | 形狀 | head 窗口覆蓋 |
   |---|---|---|---|
   | `conv_input-N` | 90112 | `(K-1+T, 8192)` = `(11, 8192)` | **全部 11 行**（state 3 行 + 8 個 token 的 qkv） |
   | `conv_output_raw-N` | 65536 | `(8192, T)` | **只有 token 0** 的 channel 0–31 |
   | `attn_norm-N`／`z-N`／`gate-N`／`linear_attn_out-N` | 16384 | `(2048, T)` | **只有 token 0** |

   ⇒ **所有 “SAME” 其實是「token 0 相同」**，而 token 0 是那次 pass 裡**最舊**、資訊量最低的 token。
   2026-09-16 為此白走了 r1–r4 七輪：整條定位鏈讀的是 prompt chunk 的第一個 token，
   而「dense 投影全同、只有 `conv_input` 不同」**不是矛盾，是兩個不同的窗口**。
   修法：內核本來就有 `n_skip`（恆為 0）⇒ **`CGC_TENSOR_CAPTURE_TAIL=1`** 令 `n_skip = ne - n`，
   把窗口錨到**張量尾端 ＝ 最新的 token**（遞歸鏈真正往下傳的那一個）；dump 加 `off=` 記錄錨點，
   分析器 `--windows` 先印 ne/off。**在讀任何 SAME/DIFF 之前先跑 `--windows`**：
   兩個節點的 `ne` 不同就代表量的不是同一個東西。**每個節點都補了 `off`，是因為一個沒印出錨點的
   窗口，與一次全張量讀取無法區分。**

**兩個比對時的陷阱**（都量到過）：

- **發射順序不是計算順序，而且不穩定**：同一個臂跑兩次，同樣的值以不同順序送出
  （`linear_attn_out-2` 有時在 `attn_residual-2` 之前、有時在之後）。localisation 要用**原始碼層鏈**排序，
  不是流的順序。
- **尾段 graph 邊界不可信**：ids 目的地上限 4096、一個 pass 約 114 列 ⇒ 尾端 chunk 把數個 pass 併在一起，
  按名字配對＝拿不同 pass 的列相比。**但 `--upto 24` 這個「解法」自己成了第二個盲點**：
  2026-09-16 實測一次 36 圖的捕獲，階段序列是
  `g0:T128, g1-2:T2, g3-27:T8, g28:T2, g29-30:T4, g31-35:T1`
  ⇒ graph 1..23 **100% 是 prefill**，而 `--upto 24` 恰好採了它。**r1–r5 的所有定位結論因此
  都是 prefill 的結論**。正法是用 **`--decode-only`**（由 `ne` 推導 T，選 T=1），不要手填數字。
  **通則：一個用來避開偽影的旗標，會變成一個盲點 —— 凡是「只看前 N 個」的預設，都必須先回答
  「這 N 個裡面有幾個是 decode」。** 階段要**量**出來，不是假設。

**先列舉再量測**：`CGC_TENSOR_CAPTURE='*'` 跑一次列出**全部節點名與 fuse 值**（本機 1021 個）。
猜名字的代價是一整輪跑。**匿名節點是 `node_NNN`，不可跨臂比對**（名字來自 ggml 計數器，
而兩臂的圖不同 ⇒ 同名不同物）。只有 builder 明確命名的節點能用。
⇒ **但它們不是只能忍的**：上面的「匿名節點：先零成本歸屬，需要時才命名」給出兩步——先用
`CGC-GRPH` 的**索引順序＋左右鄰居**免費歸屬（2026-09-18 把 trunk 的 620 個降到 350），
再對值得引用的叢集做 builder 命名（**也要驗那四條安全性**，並以 **D5 M1 逐位元 9/9** 為證）。

**★ 找出「一個新鉤子貢獻了哪些名字」的正法：兩次列舉的差集。** 掛鉤子**之前**先存一份 `'*'` 列舉
（名字集合 ＋ 那份 log 的路徑），掛完再跑一次，取差集 ⇒ **新增的名字就正好是那個 dispatcher 的 dst**。
2026-09-16 實測：掛上 `ssm_conv` / `ssm_scan` / `gated_delta_net` 後差集是 **60 個**——
30 個 `conv_output_raw-N` ＋ 30 個匿名 `node_NNN`，而層號是 `0,1,2,4,5,6,8,…`，**正好跳過 3,7,11**
（`full_attention_interval=4`）⇒ 順帶證明了鉤子落在對的位置；**`ssm_scan` 零命中**（模型沒有 Mamba 路徑）。
這比猜名字強，也比逐個鉤子加 debug 打印便宜——而且它同時回答了「鉤子有沒有被觸發」這個獨立問題。

**★ dispatcher 的歸屬要查 `switch (node->op)`，不要猜。** 我猜 `ggml_concat` 走 `bin` ⇒ **錯**：
`GGML_OP_CONCAT` 有自己的 `ggml_metal_op_concat`；`GGML_OP_CPY`/`DUP`/`CONT` 共用
`ggml_metal_op_cpy`。猜錯的形狀是「名單裡加了一個永遠不會出現的名字」——**安靜無效**，
與「鉤子根本沒生效」同形。查表 5 秒。

**★ 鉤子自己的位置也是儀器的一部分：`cgc_dst_capture_at` 的宣告要放檔案最前**（`#include` 之後）。
宣告若跟著「當前第一個呼叫者」跑，每次把鉤子往前移就會 `use of undeclared identifier` 丟一個 build
（實測三次：`mul_mat` → `ssm_conv` → `concat`）。**一個可以被加在任何位置的鉤子，必須宣告在最前面。**

**這一層的模型事實會決定「attention」是什麼**：Qwen3.6-35B-A3B 是混合堆疊，
`qwen35moe.full_attention_interval = 4` ⇒ **layers 0,1,2 是 gated delta-net，layer 3 才是第一個
full attention**。所以「layer 2 的 attention」的輸出投影是普通 `mul_mat`，不是 `flash_attn_ext`。
**已掛 10 個 dispatcher**（`src/.../ggml-metal-ops.cpp`）：`mul_mat`、`mul_mat_id`、`flash_attn_ext`、
`bin`、`norm`、`ssm_conv`、`ssm_scan`、`gated_delta_net`、`concat`、`cpy`。
**CPY/DUP/CONT 共用一個 dispatcher** ⇒ 擋住捕獲量的是**名字過濾器**，不是鉤子；不要配寬泛的名字。

**gated delta-net 的鏈條**（`src/models/qwen35moe.cpp:415-425` ＋ `src/models/delta-net-base.cpp:449-496`）
——這是 2026-09-16 用來把分歧往裡推的骨架：

```
cur = attn_norm(inp)
 ├ z / beta / alpha→softplus→gate              （投影，與 conv 並行）
 └ conv_input = ggml_concat(conv_states, qkv_mixed)      ← 前段是 recurrence state
     conv_output_raw = ggml_ssm_conv(conv_input, kernel)
     … → conv_state_update = ggml_cpy(conv_state_last)   ← state 寫回 KV cache，下一步再讀
     → q/k/v_conv → l2_norm → ggml_gated_delta_net → norm → linear_attn_out
```

**`conv_states` / `conv_state_last` 是 view（無 kernel）⇒ 擷取不到**；那個循環裡唯一能讀的一格是
`conv_state_update`（`ggml_cpy` 的目的地）。

**新判準：比對前先看「逐 graph 的 DST 列數」，不是總數。** 總數不等**未必**致命：實測
`gputime 533` vs `slotgpu 492`，但被測節點在**每個 graph 都是 1/1** ⇒ 那個差是**別的**節點被融合吸收
（局部、無害——配對是按 `(graph, name)`，不是列序號）。**致命的是圖標記（`GRAPH_START`）缺席**，
那會把兩個 pass 併成一個、之後每個 graph 整體錯位。⇒ 分析器現在印**逐 graph 的行數**並列出不一致的
graph；**最強的保證是「你要的節點在兩份日誌的每個 graph 都恰好出現一次」**（比總數相等更強）。


### ★ 第五條規則（2026-09-20 新增）：**捕獲表不是「這一輪的」** —— 讀之前先驗讀取的前置條件

任何「在 build 時把 tensor 指標存進一張表、稍後再讀它」的探針（`cache_slots_out_tensors` /
`cache_slot_table_tensors` / `cache_remap_tensors` 就是），都會遇到同一件事：**那張表只在它自己
建的那種圖上被填，而且從不清空**；而 ggml 每個 build 都 reset 並**重用 arena**。

⇒ 舊指標會落在**當前圖的別的張量**上，於是：

- **「這個位址是不是當前圖的節點」不是有效判準** —— 2026-09-20 實測：它對 39 條陳舊條目
  **全部放行**（同一次啟動的兩次呼叫，第一次 39 條形狀全對、第二次同一批 key 全錯）；
- 真正的判準是**讀取本身的前置條件**。要讀 `n` 個 int32 就要求
  `ggml_nbytes(t) == n * sizeof(int32_t)`（連續 ＋ 4-byte ＋ 長度對），因為 `ggml_nbytes()`
  是**由 `nb[]` 算的**：落在 F16／量化張量上時 `4n` 會超過它自己的位元組數 ⇒
  `ggml-backend.cpp:349 GGML_ASSERT(offset + size <= ggml_nbytes(tensor))` **abort**。

價格：一條純診斷指令 abort 之後，**它正在量測的那一輪就沒了**，而預設的讀法是把責任歸給受試物。
實例：`CGC_S1_DBG` 的 POST 探針被記成「S1 會 abort ⇒ S2 卡在一個要先修的 defect」，
而同 build、只把該旋鈕關掉的對照是 **PASS**（`comparable=true`、M1/M2/M3 各 9/9）。
lesson `eng-diag-0037`；根因報告 `docs/S1_DIAGNOSTIC_ABORT_ROOT_CAUSE_2026-09-20.md`。

**殘餘（明寫）**：那個判準讓讀取**安全**，沒有讓它**可歸屬** —— 長度對得上的舊指標仍會產生
錯誤的報告行。要關掉它需要在 capture 端記下**建置世代戳記**（「是哪一次 build 寫的」）。

### ★ 第六條規則（2026-09-20 新增）：**引用 decode t/s 之前先指名 cell —— 交付形狀不是預設形狀**

`t/s` 是**一個 cell** 的讀數，不是引擎的性質。本 repo 有兩個 decode 入口，而**同一個 profile**
在兩者之間差約 **1.4×**：

| 入口 | cell | 同一 profile 讀到 |
|---|---|---|
| `profile_duo.py` / `prod_matrix.py` 的 `decode` | `-p 0 -n 128 -d 512 -b 512`，**無 spec、無 warm-skip、
  ctx 由 llama-bench 自行推導 ~704** | **7.96–9.12** |
| **交付形狀**（`scripts/check/prod_profile.py` 的 `decode-delivery`） | 上列 ＋ `--ctx-size 4096`、
  `--warm-skip 64`、`--spec-type draft-mtp` | **12.57**（09-20，`NOMINAL` 全程） |

⇒ **交付 decode 的四個約定**：`--batch 512`、`--ctx-size 4096`、`--warm-skip 64`、
`--spec-type draft-mtp`。**任缺一個，那一列就不是交付 decode。**
⚠ 所以「記錄 7.7–10.8、今天 9.90」**不是退步** —— 那是**兩個 cell**。
（`--ctx-size` 特別容易忘：llama-bench 的 `n_ctx = p+n+d ≈ 704`，而生產 server 跑 4096；
KV 配置與 batch 夾制都吃它。`--warm-skip N` 讓時鐘在 N 個 token 之後才起算，報告的 `n_gen` 排除它們。）

**★ 同一條命令的單臂噪音 ≈ ±27%。** 2026-09-20 實測：**同一次 session、同一個命令**（只差 `--arms`
的名字），讀 **9.90**（`worst=MODERATE`）與 **12.57**（`worst=NOMINAL`）—— 更低的那次是更熱的那次。
⇒ **小於 ~27% 的效應，單臂前後對比證明不出來**；只能用配對交錯 A/B（AB/BA ＋ `median(A/B)`），
而第一步永遠是 `--null`（兩槽同 binary）。這與 09-18「四臂全 launch=NOMINAL 卻給 10.80/10.34/8.92/8.58」同向。

權威出處：`MEMORY_PERF.md` 的 profile 節（交付 decode＝12.57、四個約定、±27%）與
`docs/PRODUCTION_PROFILE_2026-09-20.md`（旋鈕全文、同一性證明、重現命令）。

### ★ 第七條規則（2026-09-20 新增）：**你自己的命令行會把你的閘門關掉**

`run_server.sh` 的 preflight 用 `pgrep -f` 找「別條 session 的 llama 行程」，而 **`pgrep -f`
比對的是整條命令列** ⇒ **只要你自己那一條 shell 的字串裡出現 `llama-server`（或 preflight 比對的
任何字串），它就會匹配到你自己的包裝 shell**，然後拒絕啟動：

```
[preflight] 發現 1 支 llama 行程（可能是別條 session 正在量測）→ 不送任何訊號
  [preflight]                      <-- 這一行的 pid/etime 列表是空的
error: 仍有 1 支 llama 行程，繼續啟動極可能 GPU OOM (ret=-3)
[detach] 120s timeout
  leader  : None   server log: None
```

**辨識簽名 ＝ 那個列表是空的。**（真的有競爭者時它會印出 pid ＋ etime。）
實測 2026-09-20：`m123_oracle_gate.py --tag s2-caps2` 就是這樣被卡死的，
而我當下「殺掉的殘留 pid」其實是**我自己的 shell**，**窗口一直是空的**。

規則：
1. **診斷命令一律用不會自匹配的寫法** —— `pgrep -f '[l]lama'` 而不是 `pgrep -f llama-server`。
2. **看到空的 pid 列表就當作「沒有競爭者」**，重跑或（確定是自己一個人在用機器時）
   `CGC_PREFLIGHT_SKIP_STALE_CHECK=1`。
3. 這一條與「`ps` 被擋時用 `pgrep`」是**相反的風險**：`pgrep -f` 太好匹配了。
   任何「我用它來判斷窗口」的指令，**先問它會不會匹配到我自己**。

## 陷阱（都踩過）

**★ 陷阱 0（2026-09-18）：`ls -t | head -1` 取到的「最新產物」可能還沒寫完。**
驗證一次跑完的結果時，若 driver 還在跑，最新的 log／json **只寫到一半**，而計數會少。
實例：IQ3_S 融合的首次驗證，我用 `ls -t Backup/cgc_logs/llama_server_*.log | head -1` 讀，
只看到 **10 行** `CGC-DCFUSED`（全是 il=40）⇒ 我寫下「trunk 仍然沒融合」並開始找原因；
等 driver 結束後同一份 log 是 **每層 24 行 × 40 層**，逐層與 audit 的 `fuse=1` **完全吻合**。
**判別式**：讀之前先 `pgrep -f '[d]ecode_sweep'`（或 `[l]lama-server`）。還在跑 ⇒ 要嘛等，
要嘛把「看到的行數」與「你預期的行數」對一眼（差一個數量級就是這條）。
⇒ 通則：**「取到最新產物」與「產物已經寫完」是兩件事**；前者是 `ls -t`，後者要問 driver。
（同一族：`grep -c` 對一個還在成長的檔案，數字沒有意義。）

1. **`union > gpu_busy_sum` 是數學上不可能的指紋** ⇒ 取樣有 ABA 競爭。
   在 completion handler 裡累加 atomics、再由 reader `atomic_exchange` 歸零，
   handler 的 min/max CAS 會在歸零後把舊值寫回。
   **正解：不要累加，直接讀已完成的 `ctx->cmd_bufs[i].obj` 的
   `GPUStartTime`/`GPUEndTime`**（每個 segment 各自是一次 `graph_compute_async`，
   段邊界剛好就是那批 buffer 還活著的時刻）。零共享狀態。
2. `gpu_union ≈ wait` 是**結構性**的，不是 GPU 在忙：同一條 queue 上
   `[min start, max end]` 本來就橫跨整個 queue 佔用窗口。
3. `ggml-metal-context.m` 是 **Objective-C**：函式內 `static` 需要編譯期常數初始子，
   `static const bool x = getenv(...)` 編不過。快取 env 要放 struct 欄位或惰性初始化。
4. 跨 dylib 呼叫 Metal 只能走 `ggml_backend_reg_get_proc_address(reg, "ggml_metal_get_*")`
   （`libggml-metal` 是獨立 dylib，`libggml` 不 link 它）。
5. `decode_sweep.py` 的 `CGC_SERVER_EXPERT_CACHE_BYTES=0` **不會**關掉快取
   （run_server.sh 仍會加 `-expert-cache 0` → 幾何不一致 → 每請求 HTTP 500）。
   真正 cache-free 要用 `CGC_SERVER_EXPERT_CACHE_OFF=1`；但 16GB 機器上那會 Metal OOM。
6. macOS BSD `grep` 的 BRE 不支援 `\|`：多模式一律加 `-E`。zsh 遇 `*.log` 無匹配會直接
   報 `no matches found` 而**不執行**整條指令，用 `ls -t ... | head` 取檔名再迭代。
7. **儀器的掃描視窗常常比假設窄，而沉默會被讀成確認。** 兩個 S1 host 側探針都寫死在
   **layer 0..3**（`for (int il = 0; il < 4; ++il)`；`if (!on || il > 3) return;` 且只印 8 行），
   而層梯二分把缺陷壓在 **18..19**。於是「探針說映射正確、bit-identical 閘門卻不過」看起來像矛盾，
   其實是**探針從未看過缺陷所在的層**。動任何診斷前先問：它的掃描範圍覆蓋我的假設嗎？
   （同一家族：rate-limited 斷言只能當樣本當普查；「0 行輸出」與「沒有東西可印」不可分。）
8. **`ggml_set_output` 是隱含契約，不是保險。** 沒有它，ggml-alloc 會把同形狀的節點疊到同一塊
   buffer：S1 的 11 層裡 10 層的 `ids_data` 是同一個位址（`0x128179d60`），每層讀到同一份 16 個 int。
   重寫資料流時要**清點原路徑所有看起來像保險的呼叫**。
9. **編碼期讀 GPU 產物是競態。** `CGC-MMID-ASSERT` 在 Metal **encode** 時於 host 讀 `op->src[2]->data`；
   對 host 寫的 leaf 無害，對 GPU 算出的 id 向量會讀到配置器殘留（F32 router 機率 `0x3F64E6C4` /
   `0x3F65BD44`、`+NaN`、同節點內合法/非法索引混雜）。S1 的 `id_oob` 全部是這個假警報。
   **現在有三個時刻，必須分清「你想量哪一個」**：

   | 時刻 | 量到什麼 | 儀器 |
   |---|---|---|
   | encode 期，command buffer **還沒跑** | **前一位佔用者**的殘留 | `CGC-MMID-ASSERT` |
   | 消費 kernel **之後**、**同一** command buffer 內 | **kernel 真正消費掉的值** | `CGC_IDS_CAPTURE` |
   | `ggml_backend_sched_synchronize()` 之後 | GPU **產物**（已經寫回的結果） | `CGC_S1_DBG` 的 `POST` |

   實測同一層、同一步：主機說 `id_oob=16/16 first=1063585220`（= `0x3F65BD44` = F32 0.895 = 路由器機率），
   內核說 `[2,105,7,9,5,106,1,84]` 全在 `[0,143)`。**兩個都「對」，因為它們量的是不同時刻的同一個 buffer。**
   要主張「kernel 用了什麼 id」，只有第二列算證據。
10. **反過來也一樣：圖跑完後讀「瞬態」張量是讀死緩衝。** 非 output 的張量在最後一個 consumer
   跑完就被回收。POST 探針第一版去讀 gather 的**索引向量**（CONT 的輸出，不是 output），
   layer 2/3 讀到 F32 位模式（`idx=1043923934`），報出 16/16 假 mismatch。
   兩個方向是同一個教訓的鏡像：**一個讀太早、一個讀太晚**。
11. **改道會靜默關掉儀器。** `cache->n_zero_mapped_selected`（QUALITY LEAK）唯一的加總點在
   leaf 寫入區塊內；S1 不建 leaf ⇒ 40 層只剩 layer 0 在計數，teardown 印的 `0` 與「真的沒 leak」
   不可區分。**任何「純排程／dispatch 改動」都要檢查它順手關掉了哪些計數器。**
   注意 `llama-context.h:364` 的註解聲稱「leaf 仍會建」——**程式是 if/else，leaf 不建**，註解是錯的。
12. **可比性 stamp 只跟「寫它的那個閘門」一樣有行為意義。** `m123_oracle_gate.py` 的 comparability
   檢查比對兩側解析出的 config 值。若某個閘門曾是 **presence-based**（`getenv(...) != nullptr`）
   而啟動器**無條件**把變數塞進 `SERVER_ENV`，那時**值不生效** ⇒ 那個年代寫下的 `.cap`
   記錄的是**意圖**，不是**行為**。實例：v2 oracle 的 `.cap` 記 `CGC_OA_ASYNC='0'`，
   但它是在 presence 閘門下 dump 的，那個 `0` 選的是**分段**分支，與今天的 `1` 同一條路。
   ⇒ **改閘門（presence→value）之後，所有舊 cap 都可能變成 stale metadata，會讓 comparability
   對每一個舊 ref 永久判 INVALID**，反過來把閘門自己的判別力關掉。
   **判別器是比 stamp 更強的那個證人**：兩條 dispatcher 是實測 10× 的分支、digest 不同
   （非分段 `ff68c5a2` vs 錨 `dc055e63`），所以若今天跑的是非分段就**不可能**對分段的舊 ref
   量到 9/9 bit-identical。**處置是修 stamp（重錄 ref + 讓 profile 顯式 pin），
   不是 `--allow-incomparable`**——後者是對邊界已知的一次性問題給永久特赦，
   會讓真正的跨配置回歸以 PASS 的形式到來。
   重錄之所以不算「移動球門」，唯一授權是**新 dump 與舊 dump 逐位元相同**（`md5` 兩檔一致）。
13. **profile 沒有顯式寫的那一格，就是會漂移的那一格。** 改一個**全域預設**會靜默重解析所有
   「只靠繼承」的 profile。實測：全域 `SERVER_OA_ASYNC` 由 0 改成 1 之後，
   `off / prefill250 / qa-zh / longform-zh` 四個 profile 的有效行為全變了，而
   **`prod25` 是唯一不受影響的**——因為它是唯一**顯式**把該旋鈕寫死的 profile。
   ⇒ 若用 `prod25` 的 digest 不變來「證明」修正無害，那是**用最差的證人**：顯式設定值的那一格
   正是 value-aware 修正**不可能移動**的那一格。**改 profile/預設時要拿「靠繼承的那幾個」當證人。**
   `prefill250` 已於 2026-09-15 顯式 pin `CGC_SERVER_OA_ASYNC=1`。
14. **profile 寫 `=0` 期望「關掉」一個測「存在」的旋鈕 ⇒ 等於打開它。** 陷阱 12/13 是
   **閘門**測存在；這一條是**同一個病在 profile 上**，而且後果更重：它讓一條文件化的修復
   **六年天數的空轉**。實測：`LLAMA_EXPERT_CACHE_L4_SKIP_LAYER0` 三個讀者全部測存在
   （`llama.cpp:396`、`llama-expert-cache.cpp:1708`、`llama-context.cpp:5222`），
   而 `run_server.sh:939-945` 把它設成 `0`、由 `:1493` 的 `env "${SERVER_ENV[@]}"` 真的傳進子行程
   ⇒ `getenv` 回傳**非空** `"0"` ⇒ **skip0 一直是開的**，blk.0 一直在 pool 之外，
   而註解白紙黑字寫的意圖正好相反（「blk.0 **回到 pool 內**」，因為 skip0 是 4/10 品質殺手）。
   - **唯一吐出真相的觀測是推導量**：`CGC-DECPROF` 的 **`layers=39`**（19/19 步；`layers=40` **0 次**）。
     旋鈕自己不會說謊也不會說話——**記錄下來的值只代表意圖**。
   - ⇒ 所有 `cap_*.json` 與 llama-bench 的 `env` 區塊對這個 knob 記的都是**意圖**。
     **任何宣稱 profile 行為的句子都不得引用它。**
   - 動任何旋鈕前先 grep 它的**讀者**，確認測的是**存在**還是**值**。
     `0` 與 unset 對 presence 閘門**同義** ⇒ 寫 `=0` 是寫了一個 no-op。
   - 這也是**改進 profile 前必須先查**的一步：你以為關掉的東西可能一直開著，
     於是你對「現況」的每一個解釋都建立在一份錯的配置上。
15. **擾動型儀器的成本是「每個 graph 釘幾個張量」，不是位元組（A8）。** `CGC_S1_OUT_CAP` 用
   `ggml_set_output` 釘住 `ffn_moe_down` 以便 sync 後讀活緩衝。實測邊界：
   **全 graph × 40 層 → 載入期就死**（`kIOGPUCommandBufferCallbackErrorOutOfMemory`
   → `CGC_METAL_FAIL_STOP` abort）；**只有 prefill × 11 層 → 同樣死**；
   **只有 prefill × 4 層 → 過**；**只有 decode × 40 層 → 過**。
   失敗的那個 graph 是 `ntok=2`，11 層共 **1.4 MB**——**1.4 MB 撐不爆任何 Metal 預算**。
   控制變數是「被標為 graph output 的張量個數」，改變了排程器的配置與切分；**機制未確立**。
   - **不要用算式訂上限**（我寫過兩個錯的位元組預算，都被同一組配對反駁兩次）；
     **也不要逐步加寬逼近**——失敗是 fail-stop abort，不是變慢。
   - 讀回**必須綁相位**：`if (cgc_s1_outcap && !batched)`。
     只綁旋鈕的話，`graph_compute` 的**前幾個 graph 是 warmup/prefill**，
     而 prefill 走的是**另一個分支**（不建 remap leaf）⇒ 兩臂會在「機制根本沒啟動」的圖上被比較，
     然後報「相同」，理由與機制無關。
16. **llama-bench 的 `build_commit` / `build_number` 不是本專案的指紋。** 三跑（其中一跑在重建之後）
    **全部**印 `build_commit=e8b85393d build_number=239`。它不追蹤本地編輯 ⇒
    **不可用來判斷「兩跑是否同一支 binary」**。要指紋就自己 md5 `libllama*.dylib` + `llama-server`。
    另一個同族：**純註解編輯也會改變 `libllama` 的 md5**（debug 資訊內嵌），而 `llama-server` 不變
    ⇒ **每一列都要記自己的指紋**，不能靠「上次閘門 PASS 過」推論。
17. **值語意（value-semantics）變更，閘門<u>看不見</u> —— 必須自己宣告重新基線。**
    `m123_oracle_gate.py` 的可比性前置條件是**比對 resolved env 字串**。若一個修復只改變
    「某個值代表什麼」而**字串不變**（例：讀者由測「存在」改成測「值」，而 profile 前後都寫 `"0"`），
    閘門會看到 **0 個差異** ⇒ 直接走正常判決 ⇒ 報一個**普通的 M1 FAIL，而那是 category error**。
    - 對照組：`CGC_OA_ASYNC` 那次**字串真的變了**（`0 → 1`），閘門自己就報 `INVALID COMPARISON`
      叫醒人。值語意類變更**沒有這個警報**。
    - **唯一的分類器是控制臂**：用**修復前的值**跑一次（`CGC_SERVER_SKIP0=1`），
      它必須**逐位元重現舊參照**。這同時證明 (a) 舊參照是在舊行為下 dump 的，
      (b) predicate 改動在其餘維度數值中性。**沒有控制臂，「新的值」與「改壞了」無法區分。**
    - 然後才用 `--write-ref` 寫新參照，並把 `DEFAULT_REF` 指過去（**否則之後每一跑都對著退役參照
      FAIL**）。`--write-ref` 在比對**之前**執行，所以同一跑就能同時拿到新參照與對舊參照的差異形狀。
    - 判讀新舊差異時看 **cross-tab**：`diff/diff = 0` + `M2 9/9` ⇒ **漂移**，不是分歧
      （2026-09-16 實測：M1 4/9、M2 9/9、M3 6/9、`{same/same 4, diff/same 5, diff/diff 0}`）。
18. **兩臂都印的同一個數字，不能用來區分兩臂。** 用它證明「狀態變了」之前，先確認它**真的隨該狀態變動**。
    - 反例：`LAYER_CAPS per-layer caps: total 5976 slots` 在 **39 層池化臂與 40 層池化臂印出一樣的 5976**
      （它是 LAYER_CAPS 的計畫總和 ＝ 40×143＋MTP 256，從不查詢實際池化層集合）。
      2026-09-09 的甜蜜點 log 把這個**常數**當成「layer 0 在 pool 內」的證據。
    - 能區分狀態的是 teardown 的 `owner-set slots`（**5793 → 5935，＋142 ＝ 恰好一層的
      resident slot 數**，不是湊整數 ⇒ 不是雜訊）。
    - **★ 2026-09-16 更正：`zero regions` 不能用來區分這個狀態。** 它自己就過不了這一條的檢驗——
      同為 **39 層池化**的兩臂印出不同的值（`5577 → zero-regions=2`、`5793 → zero-regions=0`）。
      它是**內容讀數**（池中當下有哪些 expert resident），不是狀態讀數：
      已查清是 checkpoint 的**零前綴**（10/31488 條列的開頭有 4592–13120 B 全零、整列 17.8–47% 非零），
      池中那 4 個 = 2 個 resident 零前綴 expert × {gate,up}。**不是填充缺陷**：
      同層 138/142 resident slot 非零；真正會靜默丟專家的保留 ZERO slot 讀數
      `verify-strict: refused=1 zero_mapped_selected=0`。
      工具：`scripts/check/gguf_dead_expert_census.py`（檔案側，不需 log）、
      `scripts/check/mmid_zero_row_triage.py`（log 側，讀整條 stride，`exit 3` ＝ 修探針）。
      判準寫在 CONVENTIONS **A13**：判定用的儀器不得沿用被判定對象的視窗寬度。
    - 乾淨的寫法是量**推出量**：`owner-set` ＋142、`resident` ＋152 MiB、`file_reads` ＋396、
      `requests` ＋191。**旋鈕的值永遠不得當成它自己生效的證據**（CONVENTIONS A9）。
19. **跑控制臂 / 開關旋鈕之前，先確認它<u>能不能被外部覆寫</u>。**
    `run_server.sh` 的 `SERVER_ENV` 是陣列，`env "${SERVER_ENV[@]}"` 會**蓋掉**呼叫端傳入的同名變數
    ⇒ 寫死字面值的那一格**無法用 `--env` 覆寫**，控制臂根本跑不起來。
    2026-09-16 為此把它改成 `${CGC_SERVER_SKIP0:-0}`。**要掃旋鈕前先確認它是 `${VAR:-default}` 形式。**
20. **吞吐數字先問「它重複得出來嗎」，再問它是多少。而記憶體壓力計數器不是資源的量度。**
    250 t/s 在 `prefill250` 形狀（13.66 GiB 模型 + 8 GiB pool + 6144-wide ubatch）上
    **11 次獨立啟動只有 1 次越過**（286.67，其餘十次全在 155.33–184.09）。
    兩個候選機制都被自己的實驗砍掉：`free`（rho −0.70，方向是反的）與
    **page cache**（`File-backed pages`）—— 後者被 `--warm-runs` **強制拉高 4.3× 到 9.96 GiB**，
    吞吐**沒動**（rho +0.09）。最乾淨的判準是一對同狀態樣本：free 3.90 GiB → 286.67、
    free 3.74 GiB → 179.09。**幾乎相同的啟動狀態，60% 的差距。**
    - 儀器：`scripts/check/prefill_certifiability.py`（`--warm-runs` 是打破
      「執行順序 ≡ 變數順序」的唯一方法；純加樣本永遠分不開因果與累積）。
    - 規則：**輸出「可重複的帶」＋「越過目標的次數 k/N」**，不是範圍也不是目標（CONVENTIONS **A15**）。
    - `Pages free` 低可以是 cache 大（快）也可以是別的行程佔住（慢），**方向相反而不可加總**
      （CONVENTIONS **A14**）。要宣稱記憶體機制，先指名**哪一個**資源並強制它到兩個極端。

21. **`CGC-DECPROF` 逐層表有三個坑，每個都會製造「所有層一起變差」的假象。**
    用 `scripts/check/decode_layer_cb.py`（2026-09-19，9 項自測），**不要手寫解析**。
    - **欄位**：`CGC-DECPROF all: L<k> wait=<w> cb=<c> submit=<s>` 裡**第 2 個數是 `wait`**，
      `cb` 是第 3 個。取錯得到 2–3.4 ms 的「cb」，而真值在 0.1–1.2 ms。
    - ★ **`ntok` 是「這張圖的 token 維度」（draft 寬），不是「接受了幾個 token」—— 不能拿它算 `mean_len`。**
      定義在 `ggml-backend.cpp:2493-2500`：`dp_t = ttopk->ne[1]`，註釋自己寫「`ntok=2048` ⇒ prefill、
      `ntok=1` ⇒ decode」。它**很好用**當 prefill/decode 的判別器，但 MTP 開啟時它幾乎恆等於
      `1 + n_max`（實測 E2b：**n=4 佔 179/199 步**），所以 `sum(ntok)/步數 ≈ 4`，不是 token 率。
      **`mean_len` 要讀 server 自己的 `mean len =` 行**（per request，由 acceptance 導出：
      `mean_len = 1 + n_max × acceptance`，例如 `acceptance 0.4654`、`n_max 3` ⇒ 2.40）。
      2026-09-19 我曾經用 `http_duo 的 t/s ÷ DECPROF 步/秒` 反推出 `mean_len ≈ 1.80`，
      並據此宣稱「到不了 25」—— **那是窗口／算術產物，已撤回**；正確值是 **2.40**。
    - **局部 profile 不是步**：引擎另外會印 `step=91 segs=2 layers=1 total=2.00 ms … ntok=4`
      這種單段 profile，其 ntok 過得了 decode 篩選 ⇒ 每筆塞進一個假的「2.00 ms decode 步」，
      把 request 的步數從 ~64 灌到 149、step 級中位數從 ~130 ms 壓到 2 ms。
      **只收 `layers == 40` 的 header。**
    - **request 邊界是「prefill→decode 的那一步」**，不是每個 prefill chunk（一個 request 的
      prefill 有 ~8 個 chunk ⇒ 3 個 request 會數成 24 個）。而且 **http_duo 的 log 有 4 個 request：
      第 1 個是啟動 anchor（1 步），第 2/3/4 才是 rep1/rep2/rep3** ⇒ kept 的是 3 與 4。
    - **一個崩潰的 request 能讓「全 run 中位數」對每一層同時說謊**（2026-09-19 E2：第三個 request
      崩潰 ⇒ 跨 request 中位數顯示 40 層全部 0.2–1.2 ms，而同一次 run 的穩定態是「零 loud 層」）。
      **一律先分段再取中位**；而且要檢查「loud 層有沒有搬家」—— 只看原本那幾層會漏掉水床效應
      （E2 的 warmup 有 10 層接手，穩定態才收乾淨）。
    - **`gpu/union` 與 `union` 正交，不要拿它當「還有多少可壓縮」的量度**（2026-09-19 自我否證）。
      逐層欄位是 `wait= cb= submit= ms gpu= union= gap=`（順序固定）。`union` 是該層佔用的 GPU
      窗口，`gpu` 是它的 buffer dur 之和（並行 ⇒ 重複計數 ⇒ 同一 step 常見到 `gpu_sum > union_sum`）。
      實測對照：`L1` union 2.82 / gpu 2.82 / ratio **1.00** 對 `L2` union 2.80 / gpu 4.10 / ratio **1.46**
      —— ratio 變了 1.46 倍，**union 2.82→2.80、gap 0.43→0.43 都沒動**。⇒ `gpu/union` 只是
      「這層的工作分在幾個 buffer」，**不是**重疊空間。而且 linear 層本身就分兩半（19 個 1.00、
      11 個 1.41–1.46，後者每 4 層一個、緊鄰 full_attn）⇒ 看起來像**架構特徵**，先問架構再當優化。
    - **逐層表的每個欄位都要先確認口徑再引用，而 `gap` 的「不閉合」是假警報（2026-09-19 更正）**：
      header 的 `gpu_sum/union_sum/gap_sum` **就是**逐層 `dp_lay_*` 的加總（`ggml-backend.cpp:2579`
      `for li { dp_gg += dp_lay_gap[li]; }`），**同一步內閉合到 0.01%**（184 步，max 0.25%）。
      先前報的「21.43 vs 34.76 ms、不閉合 39%」是**拿「每層中位數之和」去比「每步和的中位數」**：
      **中位數不可加**（各層的高 gap 不在同一步）。⇒ **G1 的 metric 一律讀 header 的 `gap_sum`。**
      判 overlap 值不值得，實驗要挑一對相鄰的 `ratio=1.00` 層（`L4/L5`、`L8/L9`），只改這一對的
      提交方式，量**同一批步的逐層 union**：合起來 < 2×單層 ⇒ 有效；不變 ⇒ `union` 由計算量決定。
22. **`LAYER_CAPS` 不是「只改哪些專家常駐」的免費旋鈕 —— 它會改變 logits 的數值。**
    2026-09-19，prod25、9 步 probe：**同配置跑兩次的空對照 M1 9/9（逐位元相同）**，
    而 **A（均勻 143）vs E2（6816 槽重分配）只有 M1 1/9**、M2 9/9（argmax 沒翻）、
    `d_mean` 0.04–0.25（最大約 12%）。step 0（prefill 的 logits）逐位元相同，只有 decode 步漂移
    ⇒ 與「caps 只作用在 decode 的 gather 路徑，prefill 走 slab」一致。
    ⇒ 要宣稱某個 caps 配置可用，**先跑 A-vs-A 的空對照排除儀器噪音，再跑 A-vs-candidate**；
    **長 probe 定案（同日 21:1x）：`LAYER_CAPS` 會改變模型輸出，它不是等價旋鈕。**
    把 probe 從 9 條拉到 884 條（見下面第 23 條）之後：**空對照 A-vs-A2 仍然 884/884**，
    而 **A-vs-E2 掉到 M1 1/884、M2 25/884、859 條真實分歧**（生成長度都變了：940 vs 884）。
    ⇒ **短樣本的「M2 全同」會把「已經分岔」看成「決策一致」**，那是這條路徑上最貴的誤判。
    機制不是冷 expert 被丟棄（`cold(ZERO)=0.0%`、`zero_mapped_selected=0` 兩支都是 0）。
    ⚠ **「槽位佈局改變歸約順序」這個假說已被否證（同日 22:0x–22:2x），兩條證據都不要引用舊句**：
    (a) **canonical 歸約順序修不了它** —— `CGC_CANON_ORDER=1` 之下 `canonA vs canonE2`（只差 caps）
    仍是 **M1 1/968**，onset 與 canon=0 逐值相同（index 0 逐位元相同 → index 1 出現數值差 →
    index 5 出現 argmax 差）；而 canon 本身確實生效（同 caps 下 off vs on 在 index 0 就分歧）。
    (b) **`ne[2]` 不是載體** —— pool 由 8 GiB 砍到 4 GiB 讓每層 slot 由 145.8 掉到 75.5（`ne[2]` 砍半、
    總數 5976 → 3096），輸出**逐位元相同**（884/884）⇒ 形狀不是載體，M1 的「跨 pool size」在 884 步下成立。
    ⇒ 「權重相同」不等於「浮點結果相同」仍成立，但**修法既不在歸約順序、也不在張量形狀**。
    未排除的只剩「caps 改變每層走哪條計算路徑」⇒ `docs/CANON_CAPS_NEGATIVE_RESULT_2026-09-19.md`。
    ⚠ 引用 logits 差值要用 **per-logit**（首個分歧行 0.0411），別用 `sum` 的 ~20%（那是 248,320 個
    有正負項總和的相對差，被簽號相消放大）。
    入口：`Backup/oracle_caps_longprobe_20260919.sh`。
    **另外 `--port` 不要傳**：它現在預設取 profile 的 `CGCENV PORT`，不符會在 launch 前 abort
    （2026-09-19 修掉的缺陷：原本傳錯埠會產生偽 dump ＋ 乾等滿 300 s ready-timeout ＋ 清錯 listener）。

23. **要判「某個旋鈕會不會改變決策」，樣本數本身是變數 —— 短 probe 會系統性報「沒差」。**
    引擎的 oracle `CGC_LOGITS_ORACLE_FIRST_N` 預設 unlimited，但**預設 probe（`15+27 等於多少？`）
    答案是「42」然後 EOS，永遠只給 9 條記錄** —— 那不是上限在截，是模型自己停的。
    ⇒ `m123_oracle_gate.py` 已加 `--probe-prompt` / `--probe-max-tokens`（預設值維持舊行為）；
    要判決策就用會持續生成的 prompt ＋ 數百 token（實測 400 tokens ⇒ 884 條）。
    兩次同配置的長 probe 連**池統計都逐位元相同**（`requests/hits/misses/resident/owner-set` 全等）
    ⇒ 這台引擎在固定配置下是完全確定性的，**空對照一定會過**；空對照不過就別往下比。

24. **共享機器上，清理程式碼本身就是一個攻擊面 —— 連「不是 server 的工具」也要查。**
    2026-09-19：`m123_oracle_gate.kill_servers(port)` 對 llama-server 已正確地按 port 收斂，
    但**同一函式裡留著無條件的 `pkill -9 -f llama-bench`**，而 llama-bench 沒有 port 可收斂。
    後果：**任何人跑一次 oracle gate，就會殺掉別條線正在跑的 t/s 量測**（`profile_duo.py` 正是用它），
    而受害者看到的是一支死掉的 bench，不是一個死掉的 gate。
    ⇒ 規則：**清理只能指名自己的 pid／port**；不能指名的（依 binary 名字匹配的）預設**只列出 pid+argv、
    不送訊號**，要殺得明示（本 repo 的 `CGC_PREFLIGHT_KILL=all`，`run_server.sh` 同款）。
    落地：`scripts/check/m123_oracle_gate.py` 已改成列表 ＋ opt-in。
    ⇒ 一般化：**改任何「收尾／預檢」程式碼之前，先問「它會不會匹配到別的 session 的行程」**，
    而判準不是「我自己的殘留」而是「這台機器上還有誰」；`--port auto` 的兩個 session 可以並存，
    但**共用同一顆 GPU 的量測不能同時跑**（兩份資料一起毀）。

25. **新增一個會改變排程／圖形的旋鈕時，必須同時給它一行啟動回顯 —— 否則那一臂的實驗不可歸因。**
    2026-09-19：`run_server.sh` 的 `[perf]` banner 印了 `n_cb / glu_fused_down / oa_async /
    load_mode / layer_caps`，但**沒印** `CGC_SUBMIT_AHEAD`、`CGC_SLOT_TABLE_GPU`、`CGC_CANON_ORDER`、
    `CGC_POOL_SPLIT_DBG` —— 這四個都在 allowlist 裡、都會改變圖或提交順序，卻在**任何 log 裡都看不到**。
    後果：若某一臂量出「沒差」，你**分不出「旋鈕沒生效」與「生效了但沒效果」**（allowlist 會靜默丟棄
    未列出的 `CGC_*`，而這兩者留下的產物一模一樣）。
    修法（已落）：`run_server.sh:1098` 之後加一行
    `[perf]  diag: submit_ahead=… slot_table_gpu=… canon_order=… pool_split_dbg=… s1_dbg=…`。
    ⚠ **回顯清單只能放 allowlist 真的轉發的旋鈕** —— 放一個會被丟棄的（如 `CGC_TOPK_BOUNDARY`）
    等於讓 banner 對「你要求了什麼」說謊，比沉默更糟。
    ⚠ 而且**不要編輯正在執行的 shell 腳本**：bash 邊讀邊跑（記 byte offset），改到一半會讓它執行到
    錯位的內容。要改就等那支跑完，或把改動寫進另一支新腳本。

26. **用文字插入改 JSON（本 repo 的既定做法）時，驗收要看「欄位集合」，不是「能不能 parse」。**
    2026-09-19 實例：`targets.json` 的 G6 用 `str.replace` 插入一個新欄位，**改了 `old` 的起點
    （多含一行 `"status": "open",`）卻忘了同步改 `new`** ⇒ 結果是 `"owner"` 出現兩次、`"status"` 消失。
    而 **JSON 對這兩件事都不報錯**（缺鍵合法、重複鍵 last-wins）⇒ `json.load()` 成功、`--check` 也 OK，
    欄位是在**語意上**被吃掉的。
    ⇒ 規則：文字插入之後，**印出目標物件的 `sorted(keys())` 與全表每個物件的鍵數**，
    以及 `len(x) == len(set(x))`（重複鍵偵測）。只驗「parse 得動」等於沒驗。
    ⇒ 同源的一般化：**改錨點時，`old` 與 `new` 必須一起改** —— 只改一邊是把替換變成刪除。
    ⚠ **插欄位要錨在「行首的 key」上**（例如 `"probe": ...` 那一行），**不要錨在句子中間**：
    2026-09-20 實例 —— 我先刪掉某個 value 結尾的半句話，於是插入文字裡的 `"` 變成**字串的收尾**，
    整份 JSON 從那裡開始全錯。同一天還犯過一次「替換文字裡帶未轉義的 `"`」。
    **兩次都是「先 `write_text` 才 `json.load`」讓壞檔落盤**，修法是固定順序：
    `s2 = s.replace(...)` → `json.loads(s2)` → **通過才** `write_text`。
    ⚠ **而驗證要在寫入「之前」做**：替換文字裡若帶了未轉義的 `"`（例如引述舊值），JSON 會當場壞掉。
    2026-09-20 實例：腳本先 `write_text` 才 `json.load` ⇒ **磁碟上留下非法 JSON**，要靠 portal 的檢查
    才發現。**先把「寫入後的內容」在記憶體裡 `json.loads(new_s)` 驗過，再落檔。**
    同類的文字陷阱：**JSON 字串裡用單引號**（不需轉義），不要用雙引號引述。

27. **停掉一支背景量測，不等於停掉它開的 server —— 而且「熱閘門」可能不只一層。**
    2026-09-19 兩個連續的坑：
    - **孤兒 server**：`TaskStop` 殺了 campaign 的 bash，`http_duo` 的子行程鏈斷掉，**它開的
      `llama-server` 留在 8080 上活著**（`http_duo` 自己的 cleanup 只在它正常結束時才跑）。
      ⇒ 停掉量測之後**一定要用 `lsof -nP -iTCP:8080 -sTCP:LISTEN -t` 查出 pid，再 `kill -TERM <pid>`**
      （graceful 才會釋放 Metal buffer；不要用名字批次殺）。判準：`--port auto` 意味著它可能在 8081。
    - **兩層熱閘門**：我在 campaign 裡已經把「等 NOMINAL」放寬成「TRAPPING 才拒絕」，但**內層的
      `http_duo` 自己有另一個** —— 它每一 rep 都 `wait_nominal`，預設 `--cooldown-timeout 420`。
      實測 22:31→22:38 只跑完 2 個 request（≈7 分鐘），thermal 從未到 0，因為**這台 server 自己就是熱源**。
      ⇒ 放寬熱閘門時要**往下問一層**：`http_duo` 用 `--cooldown-timeout <秒>` 收斂（改 CLI，不改原始碼），
      並讓 per-rep 的 `prefill_thermal`/`decode_thermal` 欄位負責記錄，這樣沒有數字是無標籤的。
    - 一般化：**「我已經處理了 X」要問成「X 在這條呼叫鏈上有幾個實例」** —— 今天兩次都是
      「外層處理了、內層沒處理」（熱閘門、清理）。

28. **上界探針的「半死」最危險：它仍量得到步時上界，但另兩樣會給假陽性 —— 先逐欄位判定合法性。**
    2026-09-19 實測 `CGC_SUBMIT_AHEAD=1`（專案指定的「唯一零改碼上界探針」）：
    - 25/28 個 decode 步的 header 印 **`... gpu_sum=0.00 union_sum=0.00 gap_sum=0.00 ms (NO TIMESTAMPS)`**
      —— poll 點移到「下一段已提交」之後，沒有完成的 command buffer 可讀 ⇒ **結構性**，不是 overlap 生效；
    - 同一臂 **`mean len = 1.00`**（base 2.40）、生成截到 **21 token**（base ~141）⇒ **acceptance 崩掉**。
    **但它仍然是有效的上界探針** —— 因為兩臂跑**同一種圖**（實測 base 174/177 步 `ntok=4`、
    ahead 28/28 步 `ntok=4`）⇒ 每步工作相同，差的只有那個 CPU 序列化窗：
    **169.21 → 99.39 ms = ×1.702，與認證的 ×1.711 差 0.5%。**
    ⇒ 那一臂的**合法讀數只有一個：步時比**。另兩樣會給假結論：
      - `gap/union` = 0 ⇒ 是**儀器死**（`(NO TIMESTAMPS)`），**不是「gap 已消除」**；
      - `t/s` ⇒ acceptance 崩掉 ⇒ 探針自己的 t/s 只有 base 的 **0.709×（更慢）**，那是 racy 的
        **損害讀數**，不是下界也不是天花板；合法的 t/s 報酬要分開講：`[×1.0, ×1.70]`。
    ⚠ **我的報告器當時真的把 0/0 算成「G1 MET / union −68.7 pt」** —— 已修成拒絕計分（任何
    `(NO TIMESTAMPS)` 或 median `union_sum==gap_sum==0` ⇒ 印 `UNREADABLE`，不給判定），
    並多印 server 的 `mean_len`，讓「工作量被換掉」當場可見。
    ⇒ **可攜的規則**：任何「把某個東西刪掉量上界」的臂，**先確認哪一個欄位在這個臂合法**
    （`grep -c "NO TIMESTAMPS"` 決定能不能讀 GPU 側；`mean len` 是否同級決定能不能讀 t/s），
    再讀那個欄位。⚠ **不要把「探針只壞一半」誤讀成「全壞」或「全好」—— 這兩種誤讀我同一天各寫過一次。**

29. **層間空檔（gap）已經分解完畢 —— 它只有兩個成分，而且門檻「≤5%」算術上不可達。**
    用逐層欄位對**四支 run**（涵蓋 `sum(cb)` 21→37 ms，所以會縮放、不是固定偏移）擬合：
    ```
    gap_L = 1.00 x cb_{L-1} + 0.29-0.35 ms      r(組內) = +0.94 .. +0.999
    每步：sum(gap) = sum(cb) + 39 x (0.29-0.35)     而 L0 的 gap 永遠是 0.000（唯一無前驅層）
    ```
    - 斜率 **1.00** ⇒ 那一層的 top-k hook **把整段時長加到關鍵路徑上、CPU/GPU 零重疊**；
    - 它**與前一層的 `union`（r +0.01…+0.13）和 `submit`（±0.02…+0.11）都不相關** ⇒ 不是「GPU 交得晚」；
    - hook 只在 **2–11 層**上發火（集合隨 caps 改變）⇒ `cb` 是**雙峰**的，pooled 中位數只有 ~0.02 ms。
    **★ 兩個硬結論：**
    1. 成分 (ii) 單獨是 `39 x 0.32 = 12.5 ms`，不減段數就消不掉 ⇒ `gap_sum ≥ ~11–14 ms`，而
       E2b 的 5% = 6.97 ms、base 的 5% = 8.54 ms **都在地板以下** ⇒ **「gap/total ≤ 5%」不可達**。
       可達的改寫：盯 `cb_sum/total`（15.2–21.6%）、或盯**邊界段數**（12.5 ms 對段數線性）。
    2. 那個窗的**定義**就是「CPU 在跑 hook、GPU 閒著」⇒ **「把 gap 藏到 GPU 工作底下」沒有東西可藏**。
       槓桿只剩：hook 更便宜，或邊界更少。`CGC_SUBMIT_AHEAD` 的 ×1.702 是**刪掉依賴本身**（racy）。
    ⇒ **可攜的規則**：任何「overlap 可以拿回 X%」的主張，先問**那個窗裡 GPU 有沒有別的工作**；
       若窗的定義就是 GPU idle，那它不是排程問題，是 CPU 序列化問題。
    ⚠ 兩個欄位陷阱：`gap` 是 `CGC-DECPROF all` 行的**第 7 組**（第 6 是 `union`）—— 取錯會得到
       「corr(gap, cb) ≈ 0」這種看起來像發現的東西；**修正方法是拿已知值對錨**
       （本 repo 的錨：union 1.85/2.81、gap 0.46/0.49、sum(gap 中位數) 21.4）。
       另一支 log 內 `ntok` 會**混多種圖寬**（2/4/14），不先取眾數過濾，「逐層中位數」就是混血的。

30. **一個名字在同一個函式裡綁兩次 ⇒ 判定档的欄位變成常數，而負向自測抓不到。**
    實例（2026-09-19，`m123_oracle_gate.py`）：`pin` 先綁「reference 的期望 md5」，30 行後又被綁成
    **oracle-env 的顯示字串**（`pin = "" if args.no_pin_oracle_env else ...`）⇒ 判定档的
    `ref_pinned` **永遠是 False**，讀起來正好是事實的反面。負向自測（把 pin 改錯 ⇒ 應拒絕）**抓不到**，
    因為拒絕發生在碰撞**之前**；只有端到端跑一次、去看那一格的值才會發現。
    - 修法：改名（`ref_pin` / `pin_note`）＋ **source-level 守衛**加進自測：
      `re.search(r"^\s*pin\s*=", 模組原始碼, re.M) is None`。名字碰撞用單元測試測不到，
      但「這個名字不准再被綁」測得到。
    - **可攜的規則**：驗收一個寫進判定档的欄位時，問「它在**所有分支**下都能取到不同的值嗎」；
      **恆定的欄位比缺欄位更糟** —— 缺欄位會被發現，恆定欄位會被當成事實。
31. **改變「被比較的內容」的 CLI 參數不在 config stamp 裡 ⇒ `comparable=True` 但比較的是無關序列。**
    實例：`--probe-prompt` 讓 975 筆的 dump 對 9 筆的 reference 報 **`comparable=True` ＋ `M1 1/9`**，
    看起來像退步；實際上 key `(step, token_idx, ctx_type)` 撞在一起卻描述**不同的 token 序列**
    （prompt 是 CLI 參數，不進 CGCENV/ENV/ARG 的 config stamp）。
    - 修法：判定档記 `dump_records` / `coverage_pct` / `probe_prompt_md5`，並在 `coverage_pct < 100`
      時印**警告**（白名「這讀起來像 M1 退步」）。
    - 可攜的規則：**「可比」的判準要涵蓋所有會改變內容的輸入**，不只是會改變環境的東西。

32. **要「借」別人的量測窗口，要讀他的 `require*` 條件，不要猜他「在等什麼」。**
    2026-09-20 實例：另一條線的 driver 用 `server_window.py wait --need-mb 8000` 排隊，我看到它 poll 到
    `reclaimable=6823<8000` **且在下降**，於是推論「他短期等不到 ⇒ 我跑 2 分鐘沒關係」。
    ⇒ **他的兩串實驗（`--rule-ab --reps 5` ＋ 一個 7-profile suite 基線）一次 launch 都沒跑成就 `RC=1`**：
    ```
    server_window.BusyBox: ... other llama process(es): [(40811, 'llama-server')]; reclaimable=1509MB<8000
    === RULE-AB RC=1 ===   === SUITE RC=1 ===
    ```
    - **`reclaimable` 只是他 `wait` 階段的條件**；他的 `measure()` 每次 launch 前還會呼叫
      `SW.require_first(need_mb=8000)`，而**那個條件還包含「沒有別的 llama 行程」**。
    - 規則一：**讀對方的 `require*` 原始碼**（`grep -n "require_first\|BusyBox" scripts/check/*.py`），
      不要從他排隊的訊息反推他在等什麼。
    - 規則二：**「他在等記憶體」與「他不介意別人跑」是兩件事** —— 這次只成立第一件，我把第二件當成了結論。
    - 規則三：對方的 driver **不一定重試**（這裡直接拋出 ⇒ 整串 RC=1）⇒ 借窗口失敗的代價可能是他一整輪。
    - 規則四：**建置也算佔用**（換掉 `binary.head_hash`）⇒ 同一套檢查要放在建置之前，不只是跑之前。

33. **`node` 這個 kind 是「沒有名字的節點」的自動兜底名，不是一種工作。** 2026-09-20：
    `ggml.c:7190-7193`（`ggml_build_forward_expand`）對每個 `strlen(name)==0` 的節點做
    `ggml_format_name(node, "node_%d", ...)`，而 kind 詞彙表（`ggml-backend.cpp:2228`）有一條
    `"node"` 前綴把它整桶收走 ⇒ `(other)` 桶因此在這些 log 上是 **0.00 ms**。
    判準：**任何「`node` 佔 X%」的句子，都要先問 X 是哪些 op 的**（`CGC-GPUOPK` 給）
    ——本模型上是 GET_ROWS 25.3% / MUL 22.5% / UNARY 22.5% / MUL_MAT 18.3% / ADD 8.4%，
    **最大項是專家 gather**。把它當「未 fuse 的 elementwise」會把標籤缺口當成熱點。
    修法是 builder 補名（`cb()` 就是 `ggml_format_name`，不進數值路徑），**不是 kernel 工作**。

34. **KIND × OP 那張「工作加權」（`wcntw`）表排的是 NODE 數，不是時間。** 定義在
    `ggml-backend.cpp:1962-1971`：把 buffer 時長分給裡面「會發出工作的節點」⇒
    **每個 op 都得到該步自己的平均**（實測 ~150 µs/node，六個最大 op 全落在 105–170 之間），
    且 `wcntw[op]/total ≈ nd[op]/nd_work`（MUL 11.3→10.6、ADD 17.1→16.1、MUL_MAT 17.4→15.8、
    RMS_NORM 5.3→6.6、GET_ROWS 6.5→6.0、CLAMP 1.6→1.5）。
    ⇒ **不要**用它排 kernel 工單。**逐 kind 邊際成本不可識別**：同一個 command buffer 裡的
    kind 向量共線，脊回歸得到 R² = 0.13 與負係數（`Backup/g4_kind_cost_lsq_20260920.py`）。
    可用的是**容器級**分組（command buffer 的 kind 集合＝它的身份）：
    `Backup/g4_buffer_signature_20260920.py`（69494 個 buffer／32 種簽名；40 個每層主 buffer
    佔 42% 的 buffer 時間；每步約 351 個 buffer）。

35. **「少開 command buffer」這條路 2026-09-15 就關掉了，別再掃一次；而且它 ≠ kernel 數。**
    `Backup/phase_decomp/cb_sweep.json`：n_cb ∈ {1,2,16} ⇒ decode **10.34 / 9.98 / 10.28 t/s**、
    三者 `answer_md5_set` 全同 ⇒ `run_server.sh:106` 的「cb8 sweet spot」是**高原不是峰**。
    ⚠️ 這條變的是 **MTLCommandBuffer 的分組**，**kernel 一個都沒少** ⇒
    引用它時**不可以**說成「kernel 數不是約束」。要動 kernel 數就得真的減少節點／dispatch。

36. **要 fuse 一條 elementwise 鏈之前，先問「有沒有既有的 fused op」，再問「它保序嗎」，
    最後——**這一條是三問裡最重要的**——先問「它**有沒有已經被量過**」。**
    2026-09-20 的實例（**我當天就犯滿了前兩問、漏掉第三問，代價是一份寫錯的工單**）：
    `GGML_OP_MUL_MAT_ID_DOWN_COMBINE` **早就實作好了**（`llama-graph.cpp:2714` gate；
    `kernel_mul_mv_id_down_combine_{q3_K,iq3_s,iq4_xs}_f32` 在
    `ggml-metal.metal:11886/12012/12124`，**`llama-graph.cpp:2729` 的「kernel is Q3_K-only」是過期註解**），
    gate 的型別集**剛好覆蓋本模型全部 40 個主幹層**（`ffn_down_exps` = IQ3_S ×37 ＋ IQ4_XS ×3
    ＋ Q3_K ×1），兩個 env（`CGC_DOWN_COMBINE:1406`、`CGC_DC_MULTITOK:1419`）**都已在 allowlist**。
    - 它算 `Σ_e Σ_k (w_e·q)`，圖上算 `Σ_e (w_e·(Σ_k q))`（權重乘在每個 K 項上、
      整個專家迴圈只做一次 `simd_sum`，`ggml-metal.metal:11847-11863`）⇒ **代數相等、浮點不等**。
      **M1 是 `row_fnv1a64` ⇒ 逐位元**（`m123_oracle_gate.py:16`），閘是 `M1/M2 = 884/884`
      ⇒ **任何 reassociation 都被依構造否決**。設計 fuse 的第一個問題是「它保序嗎」，不是「它快多少」。
      ⚠️ 而這件事樹上**早有證明**：`llama-graph.cpp:2803-2822` 寫明 `CGC_ADD_ORDER=rev`
      「早已證聚合對順序敏感」。**先讀那條註解，不要重新推導。**
    - ★★ **它早就被量過了，但那次量測沒有對照成功 ⇒ 符號 UNRESOLVED。**
      `docs/DOWN_COMBINE_IQ3S_IMPL_2026-09-18.md` 報「正確、慢 15–18%」
      （單 token 8.09 vs 9.85、verify 5.32 vs 6.23，兩臂 `answer_md5_set` 皆 `['72ca6608']`）。
      **但把 `union_sum` 從那兩個 log 逐步配對重讀之後，那對臂作廢**：`en-dc-on` **沒有**
      `CGC_DC_MULTITOK` ⇒ 閘門只在 `n_tokens == 1` 成立 ⇒ **`ntok=4` 的步上兩臂走同一條路，
      應該逐位元相同**。實測（n=27 配對）：host 側 `cb` ×1.031、`submit` ×1.022，
      而 GPU 側 `union` ×1.215（19/27）、`wait` ×1.236、`gap` ×1.269
      ⇒ **那對臂之間有一個 ~22% 的 GPU 狀態差，與效應同量級**（加上效應量 1.76 t/s 對上
      單臂噪音底 ±1.9 t/s）。⇒ **「慢 15–18%」不可引用，反向也不可引用。**
      仍然成立的只有：**輸出逐字相同、覆蓋 40/40、三個 kernel 都在 shipped dylib 裡**，
      以及未被量過的「平行度」候選（未融合的 `kernel_mul_mv_id_*` 把 expert 放 **grid 維** ⇒
      8 個 expert 並行 threadgroup；融合是 `for e in 0..nei0` ⇒ 同一個 threadgroup **序列**）。
      ⇒ **「9 個節點 → 1、每步 −320 dispatch = −13.0%」仍然是不成立的推論**（把 node 數當成本，
      從沒被量過），**但它也沒有被否證**。**任何「以 node 數估算收益」的句子都不要寫。**
    - ⚠️ **同批的 Q3_K（Ornith）「+25%」不可引用**：`en_dc_orn_ab_fwd` 17.88 vs 17.83（打平）、
      `en_dc_orn_ab_rev` 19.65 vs 15.72（+25%）——而**對照臂自己從 17.83 漂到 15.72（13%）**。
      兩次互相矛盾 ⇒ 那不是結果，是未受控的漂移。
    - ⚠️ **t/s 單獨不能判這題**：效應量 1.76 t/s，而本專案單臂噪音底是 **±1.9 t/s**。
      能分辨的儀器是 **`union_sum`**（`decode_sweep.py` 不報 union ⇒ 要開 `CGC_DECODE_PROFILE`
      再從 server log 用 `Backup/union_by_layer_20260919.py` 讀）。
    - ⚠️ **`answer_md5` 不能當 M1 的證據**：它是**生成文字**的 md5，argmax 對最後幾個 ulp 穩健。
      `llama-graph.cpp:2706-2710` 那句「單 token 那對 md5 相同（72ca6608 == 72ca6608）」
      **不是** M1 會過的證據，不要引用成證據。
      ⇒ 反過來也成立：**判值變更的優化，正確判準是「輸出逐字相同」，不是 M1**。
    - ⚠️ **`ggml_sum_rows` 不是序列和的替代品**：`kernel_sum_rows_impl`（`ggml-metal.metal:1721`）是
      **樹狀歸約**（`for i0 = tpitg.x; i0 < ne00; i0 += ntg.x` ＋ `simd_sum`）＝配對和。
    - **保序融合的唯一構造性安全做法**：**每個執行緒負責一個輸出元素，字面序列 f32 相加**
      （無跨執行緒歸約 ⇒ 逐位元等價是構造出來的，不是測出來的）——但先問它值不值得（見上）。

37. ★★ **在設計任何實驗、或論證任何機制之前，先搜自己已有的產物。10 秒，省一份錯工單。**
    2026-09-20 實錄：我讀完 kernel 原始碼、反推出 down-combine「算術不保序、所以 G2 會否決」，
    寫了一整份工單建議「修好算術以拿回 −13%」。**那份量測 2026-09-18 就做完並留在樹上了**
    （`docs/DOWN_COMBINE_IQ3S_IMPL_2026-09-18.md`；`Backup/phase_decomp/en_dc_*` 共 **8 個 JSON**），
    結論是那個融合**慢 15–18%** ⇒ **修法 A/B 全是白做**。
    我的取證沒有錯（M1 的判定甚至與樹上一致），錯的是**順序**：先推導，後查帳。
    固定第一步（兩條，都零成本、不碰 GPU）：
    ```sh
    # (a) 這個旋鈕/env/op 是否已經被量過？
    grep -rl '<ENV 名或 op 名>' docs/ .workbuddy/memory/ agent_harness/engine_loop/traces/ \
        agent_harness/engine_loop/manifest* 2>/dev/null
    # (b) 這個題目是否已經有產物？（Backup/ 未受版控，所以 grep 版控找不到它）
    ls -t Backup/phase_decomp/ | grep -iE '<關鍵字>' ; ls -t Backup/cgc_logs/ | head
    ```
    附帶一條同樣貴的教訓：**不要用自己剛說不可信的代理去算收益**。
    我前一節才證明「KIND×OP 表排的是 node 數、不是時間」，下一節就用 `node 數 × 平均` 算出
    「−320 dispatch = −13.0%」。**自相矛盾的算式會躲過複查，因為兩半分別看都對。**

    ★★ **同一天在這一條上摔了三次，三次都是「先算一個數，再問它回答的是不是我的問題」**：
    - 把 **node 數**當**成本**去算收益（§EN-281，「−320 dispatch = −13.0%」）。
    - 把一對**未受控**的 A/B 當判決（§EN-282/283，兩半符號相反的漂移被讀成效應）。
    - 把 **`3σ`（每步散佈）** 當**中位數的標準誤**（§EN-284；正確的是 `1.253·σ/√n`，誇大 ~5.4×），
      以及把**跨 run** 的散佈當**可分辨門檻**（§EN-286；正確的是**單一 run 內**的 `SE(中位)`）——
      兩者差 2–3 倍，而且**只有後者回答「這台機器能不能判這個效應」**。
    **判準（寫進流程）**：報任何「能不能分辨」之前，先寫下三件事 ——
    ① 我算的散佈是 **run 內**還是 **run 間**（run 間含機器漂移，Ornith 的 AB/BA 量到殘留 +9.95%）；
    ② 分母是 **每步**還是 **摘要量**（中位數的 SE = 1.253σ/√n，不是 σ）；
    ③ 樣本數是多少（n=45 與 n=180 差 2× 的門檻）。
    實測參考：`union_sum` 在 E2b 暖態下中位 132 ms、σ 37.7（全段）/21.0（暖態後半）
    ⇒ **n=179 給 3SE 8.0%、n=89 給 6.3%**；而在 48 步的短 run 上是 16–17%。
    ⇒ 結論：**12.8% 的效應只能用「一個夠長的暖 run」判，不能用「兩台先後 server」判**。

38. ★★ **任何帶「閘門／旗標」的 A/B，第一個動作是檢查「旗標不該影響的子集」上兩臂是否相同。**
    2026-09-20 實錄（同一天的第二輪更正）：`en-dc-on`/`en-dc-ctl` 那對臂是**先後跑、未交錯**的
    兩次 server。`en-dc-on` 只設 `CGC_DOWN_COMBINE`、**不設 `CGC_DC_MULTITOK`**
    ⇒ 閘門 `cgc_dc_shape_ok` 只在 `n_tokens == 1` 成立 ⇒ **`ntok=4` 的 verify 步上兩臂走同一條
    未融合路徑，理應逐位元相同**。實測（逐步配對 n=27）：`cb` ×1.031、`submit` ×1.022
    （host 側、與路徑無關）**卻** `union` ×1.215(19/27)、`wait` ×1.236、`gap` ×1.269（GPU 側）。
    ⇒ **這個 A/B 沒有對照成功，它的效應量（以及據它下的「已知變慢」結論）全部作廢。**
    - 做法：**子集**＝旗標不可能生效的那些步（這裡 `ntok` 篩選）；**對照量要一側 host
      （`cb`/`submit`）一側 GPU（`union`/`wait`）**——只對一側就看不出是「機器漂」還是「路徑變」。
    - 訊號判準：**本該不動的量動了 ⇒ 不必再讀效應量。** 成本＝對**已存在的 log** 跑一個 grep。
    - 為什麼這條值得單獨記：它**不需要 GPU**，而且今天同時救了兩件事 ——
      阻止把「未受控」當成「已證實」寫進 records，以及避免「放棄一條其實還沒被測過的路」。
    - 附帶：**交錯（AB/BA）不是可選項**。這對臂的 22% 漂移就是「兩次先後跑」的產物；
      本 skill 的配對設計規則（`paired_ab.py`、臂間散佈可達 1.54×）本來就要求交錯。

39. ★★ **「等窗口」的門檻量錯了東西：一個單次抽樣的閘門，落在它授權的動作前 10 秒。**
    2026-09-20 實錄：`server_window.py wait`（當天新寫的 `cmd_wait`）跑 **70 分鐘 → 60 次讀數、
    2 次 `window open`、0 次量測**。全文 `docs/WINDOW_GATE_POSTMORTEM_2026-09-20.md`。
    - **門檻 8000 在這個 box 上算術上不可達**：60 次讀數只有 **1 次** ≥8000，而那次是尖峰。
      降到 6500 之後是 **32/60 = 53%**，仍然是丟硬幣（中位 6515）。
    - **兩次「開啟」都是暫態，10 秒內消失**：`8778 → 6810`、`6753 → 5261`。
      ⇒ `[wait] window open` 是一句**預測**；而 `window stolen between the poll and its launch`
      **這個訊息名是錯的** —— 兩次都不是被搶，是讀數自己在 poll→launch 那個縫（行程啟動 ~10 秒）裡塌掉。
      **訊息指向錯的機制會讓人去查鄰居，而鄰居不存在。**
    - **機制**：`vm_free_mb() = free + purgeable + inactive`，實測 free/purgeable 只有 0.0–0.2 GB
      ⇒ **這個閘門實際上就是 `Pages inactive`**，而它是核心 LRU 的**老化佇列（一個速率，不是容量）**。
      零 llama 行程下 10 秒可動 **1.3 GB**（檔案快取漲 1.5 GB ⇒ 從匿名 inactive 拿頁；
      `page_pageable_internal` 8.63 GB vs `external` 2.05–3.55 GB）。
      ⇒ **「確認沒有別的 llama 行程」擋不住它**，因為成因與 llama 無關。
    - **修法**：宣告前**再確認一次**（`--confirm-s`，預設 0 = 原行為；
      `Backup/patch_cmd_wait_confirm_20260920.py`，已在 scratch 副本驗過 4 項）。
      **只可能更嚴、不可能更鬆**，所以是安全的改法。
    - ⚠️ **先確認你的啟動器在不在閘門名單裡**：走 `server_window` 的是 13 支
      （`phase_split_ab`／`pool_curve`／`mtp_accept_ab`／`window_gate`／`plain_match_ab`…），
      而 **`http_duo.py`／`profile_duo.py`／`decode_sweep.py` 不在其中** ⇒
      照著「等窗口」的卡去跑這三支，就是一次**不設防的啟動**。
    - ⚠️ **這道閘門在沒有 `ps` 權限的環境會拋 `PermissionError`，不是回 BUSY**：
      `foreign_llama` 與 `_fallback_llama` **兩者**都 shell out 到 `ps`，agent session 的 sandbox 拒絕它
      ⇒ 別在這種 session 裡安排「等窗口再跑」的計畫。**守門員缺依賴時該說 BUSY，不該拋例外。**
    - ★ **最有用的一句：先問「這一步真的需要窗口嗎」。** G4 的儀器底原本要自己起一台 server，
      而佇列裡那一輪已經帶著 `CGC_DECODE_PROFILE=1` ＋ `CGC_GPU_TIMING=1` ⇒ **它的 log 就有
      `union_sum`**，收割即可（`scripts/check/union_floor.py --newest 3`）。
      **等待的成本常常高於換一條路。**
    - ★★ **「先問樹上有沒有現成的自然實驗」**（2026-09-20 同日追蹤，★ 這是本條最貴的一課）：
      我當時把「run 之後閘門是否系統性偏高」寫成**「需要一次 run」**——**錯的**。
      `require_first()` 只擋一個行程的第一次啟動、之後**只記錄**，所以多臂的 `mtp_accept_ab`
      留下一個**行程內配對**，而它把抽樣**蓋進產物**（`window` 欄）：
      冷 8026/8116 vs 暖 8783…10234，**完全分離**，p ≈ 1/60。
      **要先搜產物（`Backup/**/*.json*` 裡帶 `window`/`quiet` 的區塊），再決定要不要動機器。**
    - ★★ **但那個效應的機制是反的 ⇒ 別做「顯而易見」的處置**：假說說「page cache 裝著模型頁」，
      實測是**讀檔讓閘門更低**（只讀 8 GB 模型、不起 server：gate 5456 → 3297，同時 `ext` ＋1726、
      `comp` ＋3612 —— 剛讀進來的檔案頁落在這個閘門**不計**的佇列）。回收很慢（180 s 仍 −514 MB，
      還在爬）⇒ **「把 cache 弄熱來開窗口」會讓情況更糟，而且會拖累別人。**
      **要殺掉一個「顯而易見的修法」，最便宜的方式是做一次只變一個因子的最小實驗。**
    - ★ **更正必須落在「作出斷言的那個欄位」，不是隔壁。** 這個 repo 反覆付這個學費：
      `work_order` 說「measured: 15-18% SLOWER」，而收回它的話寫在 `measured_counterexample`；
      `priority` 說「instrument floor is unmeasured」，而量到 floor 的話寫在 `measurement_floor`。
      **改記錄時，grep 那個斷言的原文，確定它在同一個欄位裡被改掉。**（`targets.json` 的逐欄
      整行替換 + 寫入前 `json.loads`：`Backup/patch_g4_record_completion_20260920.py`。）

40. ★★ **引用一個既有儀器的數字之前，先問它的 gate 涵蓋哪些步；而且要報散布，不是單點。**
    2026-09-20 的實例（G1 的前提 B），兩層都踩到：
    - **gate 太窄 ⇒ 讀數為空而不自知。** `CGC-S1: TABLE-CHURN` 的 gate 是 `n_tokens == 1`
      （`llama-context.cpp:4668`；而 `n_tokens = t->ne[1]`，`:4846`）。它在 **MTP off** 的 decode 步
      成立（ntok=1，31/60 步），而在**交付配置 MTP on** 上是 ntok=4 ⇒ **觸發 0/128**。
      ⇒ 樹上那 45 份讀數**全部**來自 MTP-off，**答不了交付配置的問題**。
      **動手前該做的**：拿一個已知配置的 `CGC-DECPROF` `ntok` 分布（它是現成的）去對 gate 的條件，
      **數出它會觸發幾次**。**「有 45 份讀數」不等於「問題被量過」。**
      （同型的另一半：CTX gate 印不出東西、`STEP_DBG` 的 block 在 fast-path 內 ⇒ 0 行。）
    - ⚠️ **不要把某個儀器的常數抄到另一個儀器。** `:4932` 的另一個儀器用 `n_tokens <= 2`，而它
      **留了理由**：**可讀性**（原話「skips the 8-token middle」）。抄過來在 MTP-on 上只覆蓋
      **1/128**。正確的 predicate 是**池路徑自己的**：
      `cgc_is_decode_graph(n_tokens, cgc_pool_max_tokens())` ——「這一步走不走池路徑」
      就是「它要不要發布表」的問題（`cgc_pool_max_tokens` 的自我描述就是
      「max n_tokens that the expert-cache pool path handles (multi-token decode)」）。
      兩個 helper 都是 `static inline` ⇒ static 自由函式可直接呼叫 ⇒ **一行而不是三處改動**。
    - **報散布。** 43 份**步組成完全相同**（`publishes=1599` ＝ 41 graphs × 39 層、10 個 instrument
      步）的 run，churn 散在 **37.2%–84.4%**（兩叢集 {37.2,49.2,50.8,53.3,67.2}／{83.8,84.4}），
      而同一簽名**逐位元重複 20 次** ⇒ **那是配置，不是噪音**（旋鈕未被記錄）。
      ⇒ 在這種量上引用**單一中位數**等於假造精度。
    - ★ **由此可以認出「看似效應的兩點比較」。** 樹上引用了五天的
      「churn `47.5%` → 修後 `16.5%`」：兩個數字**各來自一份 run**，而其中一那份 **80.8% 的步是
      decode**（26 graphs／21 instr 步），另一份只有 47.5%（61 graphs／29 步），隔三天、
      不同 build 與池狀態、**無控制** ⇒ **那個「下降」不是效應**。方向（≠0）成立，幅度不成立。
      **判準：兩個數字若都落在同一個已測的散布範圍內，它們的差就不是一個效應。**
      ⇒ 全文與分組表：`docs/G1_PREMISE_B_RECHECK_2026-09-20.md`。

41. ★ **先問「我要的量受散熱影響嗎」—— `wait_nominal` 是為速度量測設計的，量計數器時不要付它。**
    2026-09-20 實測：`http_duo.py:476` 在**每一軸之前**呼叫
    `wait_nominal(args.cooldown_timeout)`，而 `--cooldown-timeout` 預設 **420 秒**。
    當時 thermal 是 **HEAVY** ⇒ server 已經起來（log 有 `listening on http://0.0.0.0:8080`）
    但 **log 完全停滯**（兩次取樣都是 21072 bytes、`CGC-DECPROF` 只有 step=1）
    ⇒ 看起來像卡死，其實是**在等 NOMINAL**。
    - **繞過：`--cooldown-timeout 0`** ⇒ 立刻放棄等待並繼續（它會把該軸的 thermal 記成非 NOMINAL）。
    - **判準**：我要的是**計數器**還是**速率**？計數器（`consumed_changed`、`clamped_selected`、
      `zero_mapped_selected`、`publishes`、hit/miss）**不受散熱影響** ⇒ 不該為它們等窗口；
      速率（t/s、ms/step）才需要，而那時**必須**等（否則數字不可引用）。
    - ⚠️ **同一個 repo 裡的工具在這點上不一致**：`decode_sweep.py` **沒有**這個等待
      （2026-09-20 同一晚用它跑兩輪都很順），`http_duo.py` 有。**用之前先 grep `wait_nominal`**。
    - ⚠️ 附帶：**`server` 起來 ≠ 請求已送出**。判斷「有沒有在動」要看 **log 的 bytes 變化**，
      不是看行程存在；而 TERM 殺掉的 server **仍會印 teardown**，所以那個 log 是無效樣本
      （`scripts/check/premise_b_read.py` 會拒絕它）。

## 統一 profile（2026-09-16 定案，已落進 `run_server.sh`，不只是建議）

`prefill250` 現在 pin `CGC_SPAC=1` ＋ `CGC_SPAC_ALPHA=0.75`（`run_server.sh:335`），所以
**一個 profile 同時是 prefill 與 decode 的臂**。`-b/-ub` 是**量測形狀、不是 profile 差異**：
prefill 用 profile 的 5632；decode／depth 矩陣用 **512**（5632 在 16 GB 上於 `-d≥512` 會 OOM，
`Backup/llama_bench/llama_bench_prefill250.json` 就是那個零列殘檔）。

**判準不是現在的 t/s，是「剩下的槓桿有沒有地方跑」**：池夾制把 `prod25` 的池路徑批寬鎖在 8
（`llama-graph.h:18-37` 的註解自己把它綁在 MTP 上），而 M1（batched-union gather）與 M4（verify
真批次）需要 **whole-layer slab（ne[2]=n_expert，裝得下 256 個）**——只有本 profile 有。

**pin 一個 env 鍵的代價（每次都要盤點，兩個都真的發生了）**：
- D5 會對該鍵報 INVALID COMPARISON ⇒ **重新基線**，而**證據是 jsonl md5 相同**（v4/v5 都是
  `a0a0ca742ca94e843c54b39981742738`），不是「M1 9/9」。流程見 skill `cgc-commit-gate` §2.5。
- `decode_sweep.py` 的 `--profile` **預設就是 prefill250** ⇒ 不帶 `--profile` 的 sweep 會靜默取得
  SPAC=1，而它的 `spac-on` 臂從此與 `baseline` **同義**（退化）。要真正的 SPAC A/B 用
  `--profile prod25 --arms baseline,p25-nospac`，或 `prefill250:CGC_SPAC=0`。

## 現況結論（2026-09-15 收盤，供後續對照）

> **記憶位置（2026-09-17 拆分後）**：專案長期記憶已拆成 **1 索引 ＋ 3 主題檔**
> （`.workbuddy/memory/MEMORY.md` ＋ `_PERF`／`_S1`／`_FACTS`；原 ~19 KB 的單檔會被 session 注入截斷）。
> **S1／分歧定位的權威檔是 `.workbuddy/memory/MEMORY_S1.md`** —— 本節與下面的 S1 段是**摘要**，
> 兩者不一致時**以記憶檔為準**（舊報告寫的「`MEMORY.md` 的 S1 節」也是指它）。

- decode 步 ≈ 80 ms；`wait` 佔 83–90%，`cb` 7–15%，`submit` 4%；`fill_wait = 0`（fast path 不 fill）。
- `n_cb` 1→16 對 decode **完全無影響**；`CGC_MMV_FUSE` 現配置下**輸出損壞**且更慢。
- `gap` = 12–22 ms/步（wait 的 17–35%），**大於** CPU 側窗口（cb+submit = 8.5–15 ms）。
- **序列化天花板的認證數字是 ×1.711（配對中位，區間 1.327–1.924，交錯 3 輪 + build 指紋）。**
  由 `CGC_SUBMIT_AHEAD` 上界探針取得，且**它的輸出必然損壞** ⇒ 只能當天花板，不得進對外表格。
  ✅ **★ 2026-09-19 當天重測：這個 ×1.711 重現了** —— base 169.21 ms vs `CGC_SUBMIT_AHEAD=1`
  99.39 ms = **×1.702**（差 0.5%）。**成立的條件是兩臂的 verify 圖寬相同**（實測 base 174/177 步
  `ntok=4`、ahead 28/28 步 `ntok=4`）⇒ 每步工作一樣，差的就是那個 CPU 窗
  ⇒ **序列化窗 = 步時的 41%。** 但同一支探針**讀不到** `gap/union`（`(NO TIMESTAMPS)`）與 `t/s`
  （acceptance 2.40→1.00 ⇒ 探針自己的 t/s 只有 base 的 **0.709×**，證明不了那一半）
  ⇒ 引用時必須講清楚是**步時比**還是 t/s 比；`t/s` 的報酬是 **[×1.0, ×1.70]**，下界由 G2 保證。
  單輪曾量到 ×1.78，那個數字**不可引用**（不是不夠準，是不成立——每一輪 md5 都不同）。
  誠實的讀法是：區間下界 1.327 = 已證明可拿到的量；上界 1.924 = 最樂觀的天花板。
  設計見 `docs/REMAP_ROUNDTRIP_REMOVAL_PLAN_2026-09-15.md`。
- **`gpu_union/wait ≈ 90%` 不代表 GPU 飽和**（見陷阱 2，那是結構性偏誤）。因此
  「病因是序列化」**不是**由佔用率推出的，而是由上界探針推出的。**不要用佔用率反推病因。**
- node fusion / kernel 微優化 / encode 側調整優先度仍低——除非 S1 落地後 `n_segs` 從 40 降下來。
- **W3（`cap × top_k ≤ usable slots`）這個「總開關」仍未解 —— 而且不是「未實作」，是「已否決」。**
  `llama-context.cpp:286-293` 的 `cparams.n_batch = cgc_pool_max_tokens()`（預設 8）仍在樹上；
  只有 `CGC_PREFILL_STREAM` 會解除它，而且**只對 prefill**（「Decode / MTP verify still run
  n_tokens <= pmax through the pool path」）。M1 work item 1（`CGC_POOL_SPLIT` 把 pool 與
  graph 幾何解耦）**存在**，但 `docs/M1_POOL_SPLIT_COST_2026-09-14.md` 的狀態欄就是
  `implemented, measured, and rejected`，`llama.cpp:424` 印
  `EXPERIMENTAL, KNOWN-BROKEN (degenerate routing + SIGSEGV)`，而
  `llama-context.cpp:268-278` 記載**即使 pool 自有配置，夾制也刻意不解除**。
  兩個阻斷各有簽名：A＝wide 路徑（`mode=wide ne2=256`）是 Metal 已知讀不對的配置；
  B＝`CGC-POST: il=1 st[0]=1 st[1]=1`（兩 expert 共用一 slot）⇒ NaN ⇒ il=2 SIGSEGV。
  該文件自己指出真正的形狀：wide tensors 作為 **host 側讀取來源**餵給 pool，
  **不是**作為 graph 能 dispatch 的張量。**動這條路之前先讀那份文件**，起點是 ownership model
  （`slot_owner`/`slot_table`/`batch_owned`），不是 loader。
- **S1（GPU 側 slot 查表）仍未通過 bit-identical 閘門（M1 5/9、M2 7/9、M3 5/9），
  但分歧的「形狀」已定案（2026-09-15 23:5x，`dec-20260915-2350`）。**
  **★★★★ 2026-09-17 02:55 再往下一格（§EN-17）：`src[2]` 那條路線的儀器在 S1 臂上是瞎的。**

  想驗「同一組 ids 讀到不同位元組」時，**先別寫新儀器** —— `CGC_MMID_MV_DBG`（`ggml_metal_op_mul_mat_id`，
  已在 `run_server.sh` allowlist）逐 MoE 節點印 `src0 ne`、ids 運算元與 **ids 所選前 4 列各前 ≤4096 bytes
  的 FNV-1a 指紋**，它自己的註解就是判準：「ids 同而 hash 不同 ⇒ 池的內容錯了」。
  `=1` → 150 節點（一個完整 pass ＝ 41 層 × 3）；**`=0` 會被讀成 4096 節點**（解析器只看第一個字元），
  所以 `run_ids_dst_capture.sh` 的 `MMID=1|2|3` 穿透會拒收 `0`。比較器 `Backup/compare_mmid_fp.py`。

  **但它對 S1 臂無效**：同一節點 `ffn_moe_gate-1`，錨臂 `ids=[2,105,7,9,5,106,…]`、`id_oob_vs_ne02=0`、
  指紋完整；S1 臂 `ids=[1063628079,-1100333772,…]`（**float 位元模式**）、**`id_oob_vs_ne02=16/16`**、
  **一列指紋都印不出來** ⇒ **S1 臂在 encode 時刻 `op->src[2]->data` 還沒被填**，讀到的是被重用的工作緩衝
  殘留。**「主機側、encode 期」讀中間運算元不是「消費者讀到什麼」的證據**（repo 對 `CGC-MMID-ASSERT`
  的 `id_oob` 已有同樣註記）。有效的只有**內核側**讀數（在 command buffer 內、裝置上讀）。
  順帶量到：同一個 ids 運算元，兩臂的**填充時序不同**（錨臂 encode 時已正確、S1 臂仍殘留）。

  **附帶的通用教訓（比較器）**：指紋要**按 row id 獨立配對**，不能因為 ids 清單不同就短路 ——
  第一版短路後 `BYTES_DIFFER=0` 是**空洞的**（117 列全被判 IDS_ONLY，一列的位元組都沒比過）。
  修好後它印「0 shared rows」，把「讀不到」變成看得見。**「沒有差異」與「沒有比較」必須印得出來。**

  **★★★ 2026-09-17 02:40 取代下面全部（r23–r25）。分歧跟著 `CGC_S1_MIN_IL` 走，不跟著層號走。**

  **先講「哪一個 pass」怎麼定**（上一輪我是用推的，這輪直接量）：
  - 分析器的 **stage ＝ 模型 pass**，標記是 **ids 列 `name=ffn_moe_gate-1`（沒有 `.dst`）**。
    用 `.dst` 去找標記會讓整份 log 被當成一個 stage（我第一版就是這樣）。
  - T 要**用節點自己的 `ne` 直接讀**（`l_out-0` 的 `ne / 2048`），不要用「graph 內 min ne 的比值」——
    後者取決於該 graph 哪些名字被捕捉到，node set 一換就會給出不同的 T。
  - 本 run 形狀（`n_predict=12`、208-token prompt、`n_batch=8` 的池路徑）：chunk 序列是
    **`2,2,8×21,6,8,2,4,4`，之後 11 個 T=1 的 decode**，與引擎側 `n_past` 的 +8 節奏一致。
  - 新選項：**`--select-t N`** = 第一個**完整**的 T==N stage（「完整」＝列數等於該 T 的眾數；capture
    起始的 stage 0 只有 1 列，是 fragment，選它會把 `present=1` 讀成「另一臂缺這個節點」）；
    **`--list-stages`** 先列出每個 stage 的 T 與列數再選。**永遠先 list 再 select。**

  **r23／r24（17 個 layer-0 名字、兩臂 × 2；同臂對照全乾淨）——在第一次完整 T=2 pass 上**

  | 觀測 | 結果 |
  |---|---|
  | layer 0 全部 15 節點（含 `ffn_moe_logits_raw-0` 與 `gate/up/down/out-0`） | **SAME** |
  | `mul_mat_id` 消費的 **ids** | **120 SAME / 0 DIFF** |
  | 池佈局（SLOT-OWNER）與被消費專家集合（SLOT-SEL） | **40/40 SAME**（g0、g1 兩個 2-token pass） |
  | `wrong` / `unowned` / `owner==ids` | 0 / 0 / 560–560（degeneracy 同上一段） |
  | 唯一不同的節點 | **`l_out-1`** |

  ⇒ 引擎側的 SLOT-OWNER／SLOT-SEL 閘要放寬到 **`n_tokens <= 2`**，否則那個 pass 根本不被量到
  （decode-only 的閘會讓最關鍵的一步空白）。

  **★ r25 因果測試（零改碼，本輪最關鍵）**：把閘從 layer 1 移到 layer 2（`CGC_S1_MIN_IL=2`，臂
  `p25-slotgpu-l2` 已存在）：

  | 配置 | 第一個分歧節點 | 它之前 |
  |---|---|---|
  | `MIN_IL=1` | **`l_out-1`** | `l_out-0` SAME |
  | `MIN_IL=2` | **`ffn_moe_out-2`** | `l_out-0`／**`ffn_moe_out-1`／`l_out-1` 全部 SAME** |

  ⇒ **第一個分歧的層跟著閘走。同一個層 1：host leaf 服務時逐位元相同，GPU table 服務時就不同。**
  「某一層壞了」與「從 layer 0 數值傳染」**兩種讀法全部作廢**——之前所有「第一個分歧＝層 N」的述句都要
  改寫成「第一個被 GPU table 服務的層」。

  **⇒ 盒子裡剩什麼**：那個 pass 上 layer 0 整條鏈、ids、池佈局、被消費集合、反查一致性**全部相同**，
  而第一個被服務的層的 gather 輸出不同 ⇒ ids 相同 ⇒ 讀到的 slot 相同 ⇒ **唯一剩下的是 gather 讀到的
  「位元組」不同**，即 expert 權重運算元（`src[2]`）的指向／repoint 在兩種對映下不同。
  **這是「位址類」缺陷**，與先前排除的數值／對映／時序**不同類別**。
  **下一步**：在 `ggml_metal_op_mul_mat_id` 對 `il <= 2` 限次印 `op->src[2]->data` 與其 buffer id。

  **★★ 2026-09-17 01:25 取代下面的 r10/r11 段（r21/r22）。載體不在池；而且我自己的讀數退化了。**

  **問題**：前向對映（expert→slot）已被 `EQUIV-pool`（1599/1599 `mismatch=0`，同瞬）與 `SEL-DRIFT`
  （每圖 `entries=0`）清乾淨。剩下的是**反查**：兩臂都同意「slot *s* 屬於專家 *e*」時，*s* 裡裝的是不是
  *e* 的權重。`slot_owner[layer][slot]` 由**另一條路徑**寫入（`llama-expert-cache.cpp:827` 的
  `pick_slot`／`prefetch_slot`），publish 只讀 `slot_table` —— 同一指派、兩份表示、兩個寫者。

  **兩個新讀數**（gated on `CGC_S1_TABLE_CHURN=1`、只印不進 gate）：
  `CGC-S1: SLOT-OWNER graph=G il=L sum=… xor=… wsum=… n=… owned=…`（整層）；
  `CGC-S1: SLOT-SEL graph=G il=L ntok=T sum=… wrong=… unowned=… idsum=… idsxor=… idwsum=…`（只取被消費的 ids）。
  比較器 **`Backup/compare_slot_owner.py`**；新臂 **`p25-gputime-churn`／`p25-keepleaf-churn`**。

  **★ 兩條新的儀器規則（都踩過，比先前那三條更前置）**
  1. **掛點必須是「兩臂都跑到」的地方**。第一版掛在 publish 路徑
     （`cgc_publish_slot_table_counted`）——而它**只有裝了 GPU table 的臂才會被呼叫** ⇒ 錨臂輸出
     **零行**、S1 臂 390 行，比較的一側是空的。要跨臂比，就得選兩臂都經過的 hook
     （這裡是 `expert_cache_on_topk`；判準是兩臂的 `CGC-HOOK` trace 行數相等）。
  2. **取樣點必須在「填充之後」**。在 hook 開頭取會讀到**本步尚未填充**的表
     （`slot_table[e] = -1`）⇒ `unowned` ≈ 每格 1（≈12%，正是冷專家率），**會被讀成損壞**。

  **★ 第三條（這輪最貴的一條）：反查回身分的摘要，在池自洽時是恆等式。**
  若 `wrong == 0` 且 `unowned == 0`，則 `owner == ids[j]` 逐元素成立 ⇒ **owner 的摘要就等於 ids 的摘要**，
  讀數量的是**路由**不是池。修法是**在同一行印出對 `ids` 本身的摘要**（`idsum/idsxor/idwsum`）當作這個
  讀數自己意思的控制：相等 ⇒ 退化，把結論寫成「路由不同」而不是「池給了不同的專家」。
  **通則：任何「把 A 反查回 B 再摘要」的讀數，都要同時摘要 B 本身，否則測到的是恆等式。**

  **結果（r21/r22，三臂 × 兩輪；同臂對照 440/440 全 SAME、兩個絕對閘皆 0）**

  | 配對 | SLOT-OWNER | SLOT-SEL | 讀法 |
  |---|---|---|---|
  | 錨 vs `p25-keepleaf-churn` | **440/440 SAME** | 440/440 SAME | 光是**建出** S1 節點不改變池、也不改變路由 |
  | 錨 vs `p25-slotgpu-churn` | **438/440 DIFFERENT**（`PERMUTED=0`） | 440/440 DIFFERENT | 差異綁在「GPU table 真的被消費」上 |

  - **★ 否證**：`wrong = 0` 且 `owner == ids` 在 **440/440 格、每個臂**成立 ⇒ **被消費的專家，它落到的
    slot 裝的就是它自己**。「同一個 slot 裝了不同專家 ⇒ 同一組 ids 讀到不同權重」**不成立**；
    「gather 靜默讀到另一個專家的權重」在被消費子集上從未發生。**§9.18.4（池/slot 權重內容）這條走完。**
  - `PERMUTED = 0` 要單獨讀：若是同一組專家換 slot，判準會給 PERMUTED；現在 `sum`／`xor` 都不同
    ⇒ **常駐的專家集合本身不同**，不是重排 —— 但那是**後果**（見下）。
  - **★ 真正的讀數**：兩臂的 ids 摘要**各自 440 個互異、交集只有 1**；A 的 graph 0（40 層整組）不曾在
    B 的任何 graph 出現（⇒ 不是圖索引位移）；graph 0 逐層 il=0..5 全 DIFF；`sample` 欄
    錨「巴黎是法国首都…」vs S1「巴黎并非法国的首都…」⇒ 分歧在**第 1～2 個生成 token**；池統計也分岔
    （`miss_capacity` 4648 vs 5349）。而 `CGC-HOOK` 前 80 行**全是 `ntok=2`** ⇒ 那兩趟是
    **2-token 的池路徑 prefill chunk**，之後的 1-token 步才被記到。
    **⇒ 池不是載體，路由才是，且差異在 prompt 處理階段已存在**（與內核側 r5b/r6/r7 的
    「prefill `ffn_moe_*` DIFF@1」方向一致 —— 兩個獨立儀器互相支持）。
  - **下一步**：觀察點放到**第一次 2-token 池路徑 pass** 的 layer 0；**注意不能再用 `--decode-only`**
    （T 由 `ne` 自校准推出），那一步是 T=2。

  **★ 2026-09-17 00:35 取代下面的 r7 段（r10/r11）。又兩個儀器缺陷，然後是一個未裁決的矛盾。**
  **缺陷 #5**：`mul_mat_id` 的擷取點寫死 `cgc_dst_capture(..., (int32_t)(ne0*ne1))`，而該輸出是 3 維
  `[n_embd, n_expert_used, n_tokens]` ⇒ **只讀 token 0 切片**。簽名：同一 `n_embd=2048, n_expert_used=8, T=2`
  下 `ffn_moe_down-1` 報 `ne=16384` 而 `ffn_moe_weighted-1`（`mul`，全量）報 `ne=32768`。修法：拿掉 `n_words`
  參數，一律用 `ggml_nelements(op)`。**這讓 §9.18.6 第一輪對 §9.18.4 的否證失效。**
  **缺陷 #6（自己造的）**：`TAIL=1` 對任何 >32 元素的張量只寫哨兵——host 把 kargs 的 `n_ids` 傳成**窗口長度**，
  而內核的界是 `j < n_ids`、`j = n_skip + i`。實測 head 0/533 全哨兵、tail **188/205** 全哨兵。
  **⇒ r5／r5b 作廢**（它們的「幾乎全部 never」是哨兵，而全哨兵列互比是「相等」）。修法：`arg_n = n_words`。
  **讀法更正**：ids 在池路徑是 **`ffn_moe_slots-N`＝池 slot 索引**，不是專家 id；`ffn_moe_gate/up/down/weighted-*`
  **一直可讀**（`'*'` 列舉各 40 個），先前的 ABSENT 是 NODES 沒請求。
  **r10/r11 結果（兩臂各自的同臂對照皆乾淨）**：pass 0 的層 1 —— `ffn_moe_logits_raw-1`（router logits）
  **逐位元相同**（窗口 32/32、摘要四字全同），`ffn_moe_weights_norm-1` **逐位元相同**，而
  `ffn_moe_gate-1`／`up-1`／`down-1`／`weighted-1`／`out-1` **全部 DIFF@1**，且 **`sum` 與 `xor` 都不同**
  ⇒ **不是重排**（`sum`/`xor` 對置換不變），窗口真值顯示兩臂**相關係數 0.16–0.18、平均相對差 0.82–0.92
  ＝互相無關**。**⇒ 分歧在 gather 本身，不在之後的合成。§9.18.4 復活。**
  **★ 但這是未裁決的矛盾**：若層 1 的 MoE 在 pass 0 就讀到無關權重，token 流不可能一致 30 個 pass
  ⇒ 要嘛是引擎真缺陷，要嘛是**擷取位置**讀到「同形狀但不同位置」的資料（§3.2 的附帶觀察支持後者）。
  **裁決（零改碼）**：先跑 `m123_oracle_gate.py` 確認兩臂是否真 bit-identical；再用 `CGC_S1_KEEP_LEAF=1`
  看是否變 SAME。**陷阱**：`max相對差 ≈ 2.0` 看起來像「取負」，但整條分布否掉它（`b == -a` 精確 0/32）
  ——**一個極值可以偽造出一個不存在的形狀**。

  **★ 2026-09-17 00:05 更新（r7，全張量摘要）：分歧落在 layer 1 的 MoE block。**（**已被上面取代**）
  **先前 09-16 那幾輪的結論（「第一個分歧 = `conv_input-2` 的 recurrence state」等）全部作廢**，
  因為它們有兩個盲點，都在當晚被證實：**（1）32 詞窗口恆從 element 0 起算** ⇒ 對 `(2048, T)` 的輸出，
  窗口**就是 token 0**（該 pass 最舊的 token），只有 `conv_input`（ne0 = K-1+T < 32）跨全部 token
  ⇒ 那些 “SAME” 只是「token 0 相同」；**（2）`--upto 24` 只採 graph 1..23，而那 100% 是 prefill**。
  另外 `dst_filter[256]` 曾**靜默截斷**清單（23 名 = 312 字元 ⇒ 只進 19 名、第 20 名切在名字中間），
  丟掉的包含 `ffn_moe_logits_raw-2`，而分析器只列「出現過的名字」⇒ 截斷是隱形的。

  現行儀器是 **`CGC_TENSOR_CAPTURE_HASH=1`**：內核走**完整個張量**，寫 4 字
  `[sum, xor, 加權和(index+1), 元素數]`（加權和看得見**置換**；元素數讓形狀不能冒充相同）。
  **DIFF 是結論性的，SAME 是強證據而非證明。**
  r7（兩臂 × 2、同臂對照全 `never`、逐圖元素數兩臂相同 ⇒ 非形狀假象）在**同一個 pass** 內：

  | 節點 | first_diff |
  |---|---|
  | 層 1：`attn_norm-1`／`z-1`／`gate-1`／`conv_input-1`／`conv_output_raw-1`／`linear_attn_out-1`／`attn_residual-1`／`attn_post_norm-1`／**`ffn_moe_logits_raw-1`（router logits）** | 30（即 pass 0 全 SAME） |
  | **`ffn_moe_out-1`（層 1 的 MoE 輸出）** | **1** |
  | **`l_out-1`** | **1** |
  | 層 2 全部 11 個（含 `ffn_moe_logits_raw-2`／`l_out-2`） | 1 |
  | 層 0 全部 12 個（含 `l_out-0`） | 30 |

  ⇒ **層 1 的 MoE block：輸入相同（含 router logits）、專家 ids 相同（`120S/0D`）、輸出不同。**
  （ids 要到 graph 4 才開始不同：`6S/114D`，6 = 層 1、2 的 top-k 仍相同；graph 31 起 `0S/120D`，
  即 token 流本身分叉。）
  **分段陷阱**：段界是 `ffn_moe_gate-1`，所以段 i ＝ {pass P：層 1 的 MoE 出口起 → 層 2..40}
  ＋ {pass P+1：層 0 ＋ 層 1 的 attention 到 router}。`ffn_moe_out-1`／`l_out-1` 屬 **pass P**，
  `attn_norm-1`…`ffn_moe_logits_raw-1` 屬 **pass P+1**；「同一 pass」的判讀要靠段 0（21 列、全 SAME
  ＝ pass 0 的層 0 ＋ 層 1 attention），別把同段當同一 pass。
  **下一步**：摘要看不到**幅度** ⇒ 在 `ffn_moe_out-1` 上用窗口模式取實際數值比對。
  細節：`docs/S1_CAPTURE_ROUND2_20260916_2035.html`（已過時）／`docs/INSTRUMENT_COMPARE_20260916_1821.html`。

  **判別式：分歧由「服務路徑」決定，與層號無關。**
  - prefill 階梯做兩階（層 0-3、層 4-7；8 層 × 6 graph × 2 臂 = **96 個配對**），
    兩階**同構**：**slab 服務**的 graph（256/256 experts resident）**逐位元相同**；
    **partial pool 服務**的 graph（143/256）**從第一個池化層起分歧**。
  - 預測子是一個**獨立的 runtime log 行**：`CGC-PREFILL-STREAM: il=0 kind=0 ntok=182`
    恰好只出現在那兩個全 EQ 的 graph，96 個配對上完全一致。
  - **layer 0 從不分歧是「構造」不是相關性**（★ **本條已在 2026-09-16 失效，見下**）：
    當時它**根本不被池化**（`CGC-DECPROF` 報 `layers=39`，19/19 步；`layers=40` 0 次）
    ⇒「第一個分歧層 = 第一個池化層」是精確的。
    **★ 2026-09-16 起 layer 0 進池（40 層池化）**：`LLAMA_EXPERT_CACHE_L4_SKIP_LAYER0` 的三個讀者
    原本測「存在」⇒ profile 寫的 `=0` 其實是**開啟**，六天來 layer 0 一直被排除在池外。
    修成值語意後 `=0` 才是關閉 ⇒ **現在 layer 0 是池化層**。
    - **舊 log 的 `layers=39` 是修復前的狀態，不可再當「現況」引用**；
      判別式的句子要改成「第一個分歧層 = 第一個池化層」，而 layer 0 **現在也可能是那一個**。
    - 每個 profile 的池化層數改由 `CGC_SERVER_SKIP0`（`0`/unset ⇒ 40 層；`1` ⇒ 39 層）決定。
    - `-ngl 30`（`run_n30cache.sh`）**仍應設 1**：base 的 blk.0 在那裡留在 CPU，
      要與 base 同位元就得讓它留在 CPU（§8.36）。這是**佈局對齊**旋鈕，不是品質旋鈕（CONVENTIONS A10）。
    - 新 M2 oracle 參照是 `ref_iq3_pool8gb_M2_6144_bitident_v4_skip0off.jsonl`；
      v3 已退役（兩者的 resolved env 字串**相同**，都是 `"0"`，只有語意不同 —— 見下方陷阱 17）。
  - **decode 階梯不可用於定位**（`dec-20260915-2345`）：decode 第 0 步的 layer 0 輸入由 prefill 的 KV
    導出，而兩臂 prefill 本來就不同（M1 5/9）⇒ 每個 decode 層都是上游分歧的下游，
    「輸入相同 + ids 相同 ⇒ 輸出不同」**在那裡跑不起來**。decode 對的 480/480 全層不同，
    **逐專家列多重集比對也是 480/480 不同**（0 層同多重集）⇒ **不是排列，是內容**。
  - **探針本身是決定性的**（先立前提）：同一臂跑兩個獨立 process，**480/480 逐位元相同**
    ⇒ 釘住沒有引入不確定性，跨臂的 100% 差異是臂的性質。
  - **下一步唯一還站著的候選**：量**被消費 id 背後的 slot 內容（owner-set）**。
    映射（`dec-2148`）與排列（本輪）都已被排除。**開更深的層窗價值低於表面**——
    變數是路徑不是索引。
- **前提 B 已倒（`dec-20260915-2346`）。** `publish_slot_table` **每 decode step 呼 39 次**
  （每服務層一次 region；`1014 = 39 × 26 graphs`），而**被消費的映射在 389/819 = 47.5% 的
  decode 發布上會動** ⇒ **發布不是冗餘、逐步的 host→GPU 排序要求真實存在**
  ⇒ **D3 的 `n_segs` 收縮不由此推出**，S2/S3 的驗收條件要重建。
  要量這個必須開 `CGC_S1_TABLE_CHURN=1`（未開時 teardown 會明印
  `(consumed-subset churn not instrumented: ...)`——**預設值 0 與量到的 0 必須長得不一樣**）。
- **§8.3 地雷對「被消費 id」是 inert。** `clamped_selected=0`（兩臂）。
  機制是**時序**而非政策：`ensure_batch`（填槽）→ `publish` → 寫 leaf
  （`llama-context.cpp` `:5795` 在 `:5819` 之前）⇒ publish 看到的是已填好的槽。
  **因此選「clamp 升為硬前置條件」（`CGC_S1_CLAMP_ABORT`）而不是「強制保留 ZERO slot」**——
  後者會動 `usable_slots`/`pick_slot` 的算術 ⇒ 動 pool 布局 ⇒ 移動閘門正在比較的數值。
  `clamped_table` 的 113/256（= 143 vs 256 的非 resident 比例）**結構性、不含資訊**（B1）。
- **submit-ahead 不是「被廢棄的方案」——它一天都不曾是方案**（`CGC_SUBMIT_AHEAD=1` 是故意錯的
  ceiling probe）。D3 之下它的角色由「S2 天花板」變成「**段邊界的總價值 = S3 天花板**」。
  D3 明確優於它：**D3 沒有 race**。
- **數字陷阱**：`CGC_OA_ASYNC=0` 的 0.72 t/s **不能**推論「D3 收段會慢 ~20×」——那是
  **40 splits + 序列化調度**；D3 是 **~1 split**。`CGC_OA_ASYNC` 動的是**調度策略**，
  D3 動的是 **split 數**。
- **D3 有兩個獨立前提，只有一個買到速度**（`dec-20260915-2246`、計畫檔 §9.18.7）。
  前提 A（池／表一致）⇒ 買到 bit-identical **正確**，速度增益 **0**；
  前提 B（發布離開熱路徑）⇒ 才買到 `n_segs 40 → ~1`。**前提 B 已實測失敗**（見上）。
- **16.82 t/s 不可當 D3 的驗收門檻**（同一決策）。它是 **MTP off** 量到的
  （baseline 9.16/9.45 → 16.82，×1.78，step 82.5 → 31–44 ms）；而 production（**MTP on**）
  已經 ~17 t/s。兩個 ≈1.8× 都是「填滿空窗」型手段 ⇒ **很可能吃同一份空窗、不可加乘**。
  任何 D3 驗收數字之前，先在 **MTP on** 下重測 ceiling。
- **池內容仍未「被證明是載體」**（`dec-2148`）。它證明的是 **ids 與查表實作無罪**，比「池壞」窄。
  剩下兩個子嫌疑**修法不同**：(i) **table ≠ leaf** ⇒ 修發布時序／一致性；
  (ii) **slot 內容本身錯** ⇒ 修池的填充。判別式已把範圍縮到 **partial pool 路徑**，
  但 (i)/(ii) 尚未分開。

## commit 前的驗證鏈（慣例，勿省）

```bash
# 1) 重建（incremental；不要用 REPEAT=1 全量，build_fork_llama.sh 的 REBUILD=1 會 rm -rf build/）
cd src/llama.cpp && cmake --build build -j"$(sysctl -n hw.ncpu)"

# 1b) 記自己的指紋（llama-bench 的 build_commit 不可用，見陷阱 16）
md5 -q build/bin/libllama.*.dylib build/bin/llama-server build/bin/llama-bench

# 1c) ★ 若這一輪動到 run_server.sh（啟動邏輯 / profile 預設）⇒ 先做**啟動環境指紋前後對照**。
#     dump 印完即 exit、不啟動任何東西（run_server.sh:1497），可以放心跑。
norm() { grep -E '^(ENV |ARG |CGCENV )' | grep -vE '^CGCENV LOG|\.log$'; }
CGC_DUMP_ENV=1 CGC_SERVER_PROFILE=prefill250 bash scripts/run_server.sh 2>/dev/null | norm | md5 -q
#     ★ 必須濾掉 CGCENV LOG（含時間戳）與 `free=NN%` 兩類行，否則同一配置兩跑就不同 md5
#       （實測：prefill250 連兩跑不同、prod25 恰好相同 ⇒ 不過濾時「穩定/已變」兩個判決都不可信）。
#     指紋不變 ⇒ 生產 profile 的數值不可能移動 ⇒ **不需要重新基線**（比重跑閘門便宜得多）。
#     指紋變了 ⇒ 走 2b 的控制臂流程，不要憑感覺。
#     邊界：指紋只涵蓋啟動環境、不涵蓋程式碼路徑 ⇒ 步驟 2 仍然要跑。

# 2) M1/M2/M3 + 最新 M2 oracle —— 跑兩臂：
#    預設臂是「必須 PASS」的那一臂（提交門檻），被測臂記錄差異形狀。
RUN_REPLAY_BENCH=0 python3 scripts/check/m123_oracle_gate.py --profile prefill250 --tag <tag>
RUN_REPLAY_BENCH=0 python3 scripts/check/m123_oracle_gate.py --profile prefill250 \
  --env CGC_<被測旋鈕>=1 --tag <tag>_arm

# 2b) ★ 若這一輪動到「值的語意」或任何會改數值的東西 ⇒ 先跑**控制臂**（複現舊行為），
#     它必須逐位元重現舊參照；然後才換參照。舊參照要留檔（`v3` 這種），不要刪。
RUN_REPLAY_BENCH=0 python3 scripts/check/m123_oracle_gate.py --profile prefill250 \
  --env CGC_SERVER_SKIP0=1 --allow-incomparable --tag <tag>_control   # 期望 M1/M2/M3 全 9/9
RUN_REPLAY_BENCH=0 python3 scripts/check/m123_oracle_gate.py --profile prefill250 \
  --write-ref Backup/knifeedge_matrix/ref_<...>_v4_<語意>.jsonl --ref-note "<為什麼退役舊的>" \
  --tag <tag>_newdefault
#     換完之後把 m123_oracle_gate.py 的 DEFAULT_REF 指到新參照（漏了 ⇒ 之後每一跑都 FAIL）。

# 3) llama-bench（生產標準）—— 約 7 分鐘；會吃掉機器，不要和閘門並行
RUN_REPLAY_BENCH=0 python3 scripts/check/llama_bench_matrix.py \
  --arms prod25-stream --depths 0,512,1024,2048,4096 \
  --json Backup/llama_bench/matrix_<tag>.json --md Backup/llama_bench/matrix_<tag>.md

# 4) 白皮書 HTML → docs/PREFILL250_DECODE25_WHITEPAPER_YYYYMMDD_HHMM.html
#    （附錄要寫：兩臂閘門判決、matrix 三跑對照、M2 oracle 的 md5 + cap、本檔自身指紋）

# 5) 索引（**最容易漏，漏了要補一個 follow-up commit**；順序重要：先 INDEX 後 MANIFEST）
python3 agent_harness/engine_loop/memory/build_memory_index.py
python3 agent_harness/engine_loop/index_assets.py
python3 agent_harness/engine_loop/memory/build_memory_index.py --check
python3 agent_harness/engine_loop/index_assets.py --check
python3 agent_harness/engine_loop/traces/validate.py
python3 agent_harness/engine_loop/traces/selftest.py    # 必須 8/8 被拒

# 6) commit —— pre-commit hook 會跑 scripts/check_build_tracked.sh
RUN_REPLAY_BENCH=0 git commit -F <msg-file>
```

- **`RUN_REPLAY_BENCH=0` 必須與 `git commit` 寫在「同一條指令」上。** 每次 Bash 呼叫是獨立 shell
  ⇒ 前一條 `export` 不保留 ⇒ 單獨 `git commit` 會被 hook 第 11 項擋下（找不到 `llama-server` PID）。
- **步驟 5 不要跳**：新增 trace / 白皮書但沒重跑索引，會讓 `MANIFEST.jsonl` 與
  `memory/INDEX.jsonl` 描述「上一個瞬間的樹」⇒ 只能用 follow-up commit 補。
- **hook 不需要 `--no-verify`**：其他項（dylib/symlink/exe 追蹤、`@rpath`、死鎖防護、
  原始碼↔binary 同步）都會自己 PASS，只要 build 產物有一起 staged。

### 驗證鏈的三個補充陷阱（2026-09-16 新增）

- **`run_server.sh` 的 `if/else` 巢狀會讓一個旋鈕靜默決定另一個。** 實例：`CGC_LOOP_GUARD` 的 push
  被夾在 `CGC_SOFT_POOL_L1` 的 `else` 分支裡（少一個 `fi`，`:1381-1392`）⇒ 設了 L1
  （正是註解推薦的 `CGC_SOFT_POOL_L0=48 CGC_SOFT_POOL_L1=48`）會**丟掉** phrase-loop guard，
  連 `CGC_LOOP_GUARD=1` 都要不回來。教訓：**「不存在」有兩種——預設關（pass flag 可解）
  與不可達（pass flag 無效）**；從 dump 分不出來，必須讀控制流（`grep -n` 那個旋鈕的 push 點，
  看它被幾個 `fi` 包住）。
- **dump 時環境變數要寫成獨立賦值。** `env "A=1 B=2"` 在 zsh 下不會 word-split，會變成
  「一個變數名含空格」的賦值，再被 `SERVER_ENV+=(VAR="$VAR")` 原樣印成「一個元素含空格」，
  足以偽造出一整列錯誤的對照表（本輪因此誤判過 `L0+L1` 那列，一度以為 guard 還在）。
  寫成 `env A=1 B=2 …`，或每次只給一個變數。
- **hook 在 linked worktree 是「活的」，但 `--install-hook` 會裝錯位置。** git 用
  `git rev-parse --git-path hooks/…` 找 **common 目錄** 的 hook（所以 worktree 共用主 repo 的）；
  但在 worktree 內跑 `check_build_tracked.sh --install-hook` 會寫到第 98 行的
  `--absolute-git-dir`（**worktree 私有**目錄）⇒ **印出「已安裝」卻永遠不會被執行**。
  要裝就在主 worktree 裝。完整使用說明：`docs/PRECOMMIT_HOOK_GUIDE_20260916.html`。
- commit message 用 `-F -`（heredoc）或 `-F <file>`，不要用 `-m`：內容含中文、`§`、反引號與換行。
- **排除清單**：`hf-space-deploy/` 是**嵌套 git repo**（已被 gitignore，勿強加）；
  `.tmp_*.py` 是 scratch；`data/replay_bench/` 是 stale 資料；
  `Backup/` **整個 gitignored** ⇒ 附件不能進版控，白皮書要寫清楚可引用副本的路徑。
- **閘門結果與吞吐要分開講**：閘門（M1/M2/M3）是**相對不變量**，不是絕對正確性；
  一個**確定性**的錯誤在兩側完全相同、依構造不可見。cache-free 絕對 ground truth 在 16GB 上不可得
  （需 256 experts × 40 層常駐 ⇒ Metal OOM）。
- **吞吐一律不可由單次 matrix 引用**：同一 nominal 配置的三跑（其中兩跑同 binary）
  極差達 **33.6%**（`pp@1024` 79.88 → 106.69），**方向不一致**，且連 pool 行為都不同
  （hit 97.1%/reads 519501 vs hit 97.3%/misses 16185）。列原始讀數可以，**下結論不行**。

## K0：把「每節點 µs」拆成 GPU 執行 vs 主機端（2026-09-21 新增，動 kernel 前必跑）

「步時 ≈ 節點數 × 每節點固定成本」是自洽的（50 µs × 2455 nodes ≈ 123 ms ≈ 步時），
但**空 dispatch 實測只有 3.28 µs** ⇒ 那 30–90 µs 裡一定有第二個成分。**沒做這一步就寫 kernel，
就是在賭主詞是誰。** 全文 → `docs/METAL_OCCUPANCY_PROJECT_PLAN_2026-09-21.md`。

- **命令**（六個 env 2026-09-21 已在 `run_server.sh` allowlist，**不必 bypass launcher**；
  換 branch 要先用 `grep -c <VAR> scripts/run_server.sh` 查一次，allowlist 是 per-revision 的）：
  `CGC_GPU_NODES=1 CGC_GPU_OPS=1 CGC_CB_N_MAIN=1 CGC_GPU_NODES_TRACE=1 CGC_GPU_TIMING=1 CGC_MM_DBG=1`
- **⚠⚠ `uni` 欄實測沒有開火（2026-09-21 K0 親測，推翻上面原本的寫法）**：24 個 op 裡 **23 個是 `-1`**
  （真實 op 永遠共用 buffer；直方圖是 6-node 26,187 個、64-node 5,160 個），唯一有值的 `RESHAPE`
  = 826 µs/node × 598 node = 494 s ≫ 該步 112.84 ms ⇒ **物理上不可能**。`CGC_CB_N_MAIN=1` 救不了它。
  ⇒ **per-op 歸因不要用 `uni`**；替代＝ `CGC-NSM` 的 per-buffer `dur_ns` ＋ node 區間
     （但 per-kind 最小二乘會 collinear：`MUL_MAT_ID/GLU/SUM_ROWS/CLAMP/DIV` 同 `ub=55.85` ⇒ 不可分離）。
  ⚠️ 不要同時把 `CGC_SERVER_N_CB` 拉大：**n_cb=127 會卡死 Metal command buffer 建立，可用區間 ≤ ~16**。
- `CGC_MM_DBG=1` 印 `MMDBG <family> ne11 ne00 ne01 type0 type1` ⇒ 直接拿到每個 mul_mat 的形狀與選了哪一族。
- 解析器現成：`scripts/check/gdn_split.py`（支援 `uni` ＋ least squares）、`scripts/check/attn_moe_split.py ops`。
- **決策表要寫死在跑之前**（避免事後找解釋）：`uni` ≈ 30–90 µs ⇒ GPU-bound，主線是 context dispatch 幾何；
  `uni` ≪ 30 µs ⇒ 時間在主機端 encode/submit（`commit+waitUntilCompleted` 實測 **164 µs**），那是另一條線。

- **★ G2 硬約束（2026-09-21，使用者更正 + 逐行核對）**：`nxpsg` 不是「K 方向分組」這麼委婉——它就是
  **歸約樹的形狀開關**。`ggml-metal.metal:4067-4085` 用五個 `if (nxpsg >= N)` 逐層開 `simd_shuffle_down`
  (16/8/4/2/1)，而 host 端 `ggml-metal-ops.cpp:2604-2637` 拿 `ne11`(=M) 選 nxpsg ⇒
  **什麼都不動，樹就隨 batch 換**。
  ⇒ 永久禁用：`nxpsg`、`N_SIMDWIDTH`、以及 mul_mv 裡的 K 分組（`ix=tiisg/8`、`stride N_SIMDWIDTH/8`、
    `:3737-3742` ＋ `simd_sum` `:3760`）。`r1ptg` 機制上安全（每列獨立累加器）但由 ne11 選 variant ⇒ 當保險也禁。
  ⇒ 允許：沿輸出軸加 threadgroup、`nsg`、**以及 `nr0`**（2026-09-21 補：`nr0` 只改「一個 simdgroup 扛幾列」，
    **不改每列的 K 分割**（恆 32 路，`kernel_mul_mv_q1_0_f32_impl` 的 `first_row=(r0*NSG+sgitg)*nr0` ＋ `sumf[nr0]`）
    ⇒ bit-safe；`nr0=2→1` 合法且能讓總 thread 翻倍 —— **但沒有用，見下**）。
  ⇒ 允許：沿輸出軸加 threadgroup、`nsg`。K1 期間 verify 路徑全程鎖 `CGC_MM_BITIDENT=1`。
  ⇒ 不變量請寫成：「**K 分割必須是 (tsrc0, ne00, N_SIMDWIDTH) 的函數，永遠不得是 M 或新 grid 形狀的函數**」。
- **「改 grid」是 G2 相容的，而且 repo 內已有先例**：`ggml-metal-ops.cpp` 的 `CGC_MM_BITIDENT` 註解寫明
  「grid 的列數不影響逐位元結果」。禁的是 split-K／tree reduce／accumulator 型別／mul+add↔fma／scale 位置。
- **★ K0 已經跑完（2026-09-21 16:2x），結論是負的 ⇒ 上面這整套「改 dispatch 幾何」建議不做**：
  - **`threads ÷ 輸出元素` = 16–128**（`CGC_MM_DBG` 20,019 行，每個觀測到的 decode 形狀）
    ⇒ 輸出軸早已被完全切開（每列一個 simdgroup），**要再加 thread 只能切 K ＝ 上面那條永久禁區**。
    （`nsg` 加大**不增加**總 thread 數，只是把更多列塞進同一 threadgroup。）
  - **合成的 13.6× 不適用，機制已指名**：那探針是**純 streaming payload、沒有歸約軸**；
    真實 GEMV 的 32 條 lane 就是在切 K。
  - formula：dispatch 在 `ggml-metal-ops.cpp:2763/2765`，總 threads = `ceil(ne01/(nr0·nsg)) × ne11 × 32·nsg`，
    `nr1 = 1` 恆成立（`device.cpp:848`）⇒ `threads ÷ 輸出元素 = 32/nr0`（量化）或 `32·nsg/nr0`（f32）。
  - **decode 主導形狀是 `ne11=4`，不是 1**（ne11 分布 1→1193、2→1994、**4→12832**、14→800、489/512→3200）
    ⇒ **MTP verify 的 M=4 才是交付形狀**。
  - ⇒ P(25 t/s) **<3%**；中央情境 **~14–15 t/s**；K3 融合（≤11.5%）升為唯一主力；**K4 主機側否證**
    （`gpu_union` 佔步時 92%，`busy_sum/union = 1.17` ⇒ GPU 跨度吃掉整步，主機端沒空轉）。
- **`threads ÷ 輸出元素` 指標怎麼讀**（`= 32/nr0`，iq4_xs 的 `nr0=2` ⇒ 16）：
  它是「每個輸出元素分到幾條 lane」，而 kernel 端那 32 條 lane **就是在切 K**（不是閒著的）
  ⇒ ≥1 的意思是「輸出軸已經切滿、每列已被 16 條 lane 瓜分 K」。
  **但指標只是粗閘門，更硬的否證是絕對量**：實測主導形狀總 thread 數 **16k–524k**，
  而 M4 GPU 同時駐留量大約幾萬 ⇒ **早已超過硬體容量，加 thread 只增加排程**。
  唯一真的不夠的是 `ne01=1`（threads 64），但它只有 4 個輸出元素、`nr0=1` 頂多 128 ⇒ latency 主導，救不了。
- **★★ 說「G2 / bit-exact」之前先拆成兩個要求（2026-09-21，§EN-396）**：
  - **(a) M-invariance ＝ 功能需求，不能放**：MTP 的 draft 走 M=1、verify 走 M=4，兩邊必須自我一致，
    否則 ULP 層互相否決 ⇒ 假拒絕（正確 token 被拒）。這跟模型品質無關，是機制自洽性。
  - **(b) bit-exact vs 參考 kernel ＝ 測試約定**：業界不要求跨後端 bit-exact；greedy 下 1 ULP 只在
    「logits 幾乎相等」時翻 argmax，而平手換哪個 token 語義上都無所謂。
    它的真價值是**免費且檢定力無限的 oracle**（本機統計型驗證做不出 power）。
  - ⇒ **若要放寬用分層**：Tier1 逐位元（預設）→ **Tier2「117 步 top-1 token ID 全同」＋logits 最大相對偏差上界**
    （仍精確便宜，且放行所有不翻 argmax 的重排）→ Tier3 品質指標（**不要，做不出 power**）。
  - ⇒ **放寬只買 ~4–5%（K0 實測上鎖價錢），不是 2×。bit-exact 不是 25 t/s 的瓶頸**（K1 已被 occupancy 否證）。
  - ⇒ 放行 K-sharing 後**仍要保 (a)**，不變量不變：K 分割不得是 M 的函數。
- **★★ 講「融合（fusion）」之前必須先說是哪一種（2026-09-21，§EN-397）——三種省的東西完全不同**：
  - **① 合併 dispatch**（省 per-dispatch 固定成本）：G4 實測 **0.0736%/dispatch**，最大集群 72 個
    ⇒ **上界 5.3%**；K0 獨立印證 per-buffer 固定成本 ≈ 13 µs（×360 = 步時 4.6%）。
  - **② 垂直融合**（鏈式 producer→consumer，中間值留 register）：省**中間結果寫回顯存再讀回**的往返。
    ⚠️ **thread 數不變 ⇒ 不解決 occupancy**。多數人把「融合」默認想成這一種，它不是解決算力用不滿的那種。
  - **③ 水平融合**（N 個**互相獨立**的小 op 打包成一次 dispatch）：**這才是真的提高 occupancy**。
    但 **decode 期是一條串行鏈**，K0 實測小 op「小到無法再並行」（`ne01=1` 只有 4 個輸出元素）
    ⇒ **③ 在 decode 期沒有對象**。
  - ⇒ 一句話：**「算力用不滿」≠「有並行性可挖」** —— GPU 空著不是因為活沒打包，是因為下一步依賴上一步，
    融合不能憑空創造獨立性。
  - ⇒ **立場修正**：G4 當年判 5.3%「不到 12.8% 門檻 ⇒ 不做」；**K1 已死之後問題變了**，
    5.3% 是**目前唯一已量、且確定為正**的收益 ⇒ 應改判為值得做（但仍要配對 AB/BA 才讀得出來）。
    全部融合上界 11.5%，扣掉 5.3% 剩 **~6% 未量**。
  - ⇒ **大 op 不要碰**：`MUL_MAT` ≈ 屋頂 77%、`MUL_MAT_ID` ≥ 屋頂 ⇒ 無 occupancy 問題。
  - ⇒ **K3 第一步不是寫 kernel，是量一次「垂直融合省多少」** —— 那個數字從來沒被單點量過。
- **★★ K2（冗餘 bytes）已證偽（2026-09-21，§EN-399）：bytes 那一腿是空的，別再拿「120 個 CPY ≤22%」當槓桿**。
  - **正確數字是 210 個 CPY（MTP on）／120 個（MTP off）⇒ MTP 專屬只有 90 個。舊說 120／124 停用。**
    分離方法：`CGC_GRPH_DBG=1` 印全圖（`ggml-backend.cpp:2091-2099`），跑 MTP on vs `CGC_SERVER_MTP=0` 兩臂對照。
  - MTP 專屬 90 個＝ conv state 由 `if` 分支 1 個/層 變 `else` 分支 K=4 個/層（`delta-net-base.cpp:487-526`，30 層 ×3）。
    它們只搬 **8.64 MiB/步 ⇒ 步時 0.59%**，且是 rollback snapshot、真會被讀 ⇒ **不能刪**。
  - 全部 210 個搬 71.4 MiB，但其中 **60 MiB 是基礎必要的 ssm state 寫回**（MTP off 也有）⇒ 不可省。
  - **★ 但翻出一筆不屬於 K2 的帳：60 個零位元組 CPY**（每層 2 個，`ne=[24576,0]`／`ne=[524288,0]`，
    元素數 = ne0×ne1 = 0；源張量無名；**與 `n_rs_seq` 無關**）。搬 0 bytes 卻各付一次 dispatch
    ⇒ 60 × 0.0736%（G4 llama-bench 實測每個 dispatch）= **4.4% 步時** ⇒ **歸 K3，K3 對象從 72 擴到 132，上界 ~9.7%**。
  - ⚠️ 未坐實：`ne[1]=0 ⇒ 0 bytes` 是從打印的 ne 推得；且「0 元素張量是否真的產生 dispatch」未驗證。
- **⚠️ 解析 `CGC-GRPH` 行的坑**：節點名**含空格**（`cache_r_l0 (view) (copy of conv_input-0 (view))`）
  ⇒ `name=(\S*)` 只解析到 2604/4116（漏 37%）⇒ 必須用非貪婪 `name=(.*?)\s+op=`。
- **★ `gpu_union` 是比 t/s 靈敏得多的配對指標**：同配置連跑兩次差 **0.2%**（102.0 vs 101.8）
  vs t/s 的 ±27%（±1.9 t/s）⇒ 優先把它做成 `paired_ab.py` 的第二指標，別再只用 t/s 判小幅改動。
- **⚠ `CGC_MM_BITIDENT=1` 本來就是預設**（`run_server.sh:2187` 未設就強制 1）
  ⇒ 想量「不上鎖」必須**明確設 `=0`**，否則你那一對是 null（我第一對就是這樣白跑）。
  上鎖價錢 ≈ 4–5%（四指標同號）⇒ **今天已在付，且 G2 禁止退** ⇒ 那是 bit-exactness 的定價，不是收益。
- **選配有個「查env不能有副作用」的姊妹陷阱**：閘門底下取不到 env 時用 `CGC_DUMP_ENV=1`，
  它**同時**是唯一真相來源，不要在工具裡重打一份 env。
- **★★ 第 22 條（2026-09-22 §EN-441）：開量表前先證明「軸真的會動」——
  `run_server.sh` 的 SERVER_ENV 才是 llama-bench 的唯一入口**
  - 機制：`llama_bench_matrix.run_arm` 的子行程 env = `dict(os.environ)` + resolved env，而轉出來的
    resolved env **就是** `run_server.sh` 印的那份 `SERVER_ENV`。Knobs 是從**命令列**進去的
    （`--arms prod25:KEY=VAL`）⇒ 不在 `os.environ` ⇒ **沒進 SERVER_ENV 的變數 llama-bench 一輩子看不到**。
    （同一個坑第三次被抓到，見 `run_server.sh:1102`、`:1552`。）
  - **症狀是最壞的那一種**：cell 照跑、照出 row、看起來有資料，實際上是 baseline 的重複 —— 今晚的
    budget sweep 就是這樣：`LLAMA_ARG_EXPERT_CACHE` 不是任何程式讀的名字，10 個 arm ＋ 6 個 ref 的 stderr
    **全部印 `n_slots=143`**，然後我們差點把 ±11% 的次序飄移寫成「6 GiB +3.40%」。
  - **做法**：`scripts/check/shape_knob_search.py` 有 `resolved_env()` / `check_reachability()`。
    判準**不要問「我的 key 有沒有活下來」**（launcher 常換名字：`CGC_SERVER_EXPERT_CACHE_BYTES` →
    `CGC_EXPERT_CACHE_BYTES`），而是**整份 resolved env 對 reference cell 做 diff** —— 真旋鈕一定動到某物。
    `--plan/--run` 遇空 diff 直接 **rc=2 拒絕**，`--allow-inert` 是逃生門不是通行證。
    有 `--selftest`（27/27）兩條突變：正確 key 必須可達、舊 key 必須被判 INERT。
  - **已知 INERT / 需要把手的（2026-09-22 實測）**：直接設 `CGC_N_CB` / `LLAMA_EXPERT_CACHE_WORKERS` / `CGC_OA_ASYNC`
    會被覆寫 ⇒ 改用 `CGC_SERVER_N_CB` / `CGC_SERVER_WORKERS` / `CGC_SERVER_OA_ASYNC`；
    `CGC_EVICTED_RING` 被 `run_server.sh:1361` 硬釘 0；`CGC_MMV_NR0`、`CGC_DRAFT_PREFETCH(_SYNC)`、
    `CGC_PREFETCH_SRC`、`CGC_RN_ROUTING`、`CGC_P_ROUTE`、`CGC_DRAFT_STRICT` 完全沒 allowlist。
  - **配套教訓（同類）**：flag 在 env 裡 ≠ 有執行。`CGC_LAYER_AHEAD_PREFETCH` 那一輪
    （`docs/F2_F5_OVERLAP_AND_AUDIT_RESULT_2026-09-20.md:33`）就是 `prefetch=0/0`、零 PFDBG；
    `CGC_PREROUTER` 也是靠 `queued=0` 才發現被 `prewarm_hot` 全擋。**先看計數，再看 t/s。**

- **★★ 第 23 條（2026-09-22 夜 §EN-442）：「kernel 層也要一起調」是對的，但先把錯切面切掉**
  - **別把 NSG×dense 當新機會**：`CGC_MMV_NSG` 在 **IQ4_XS 上早已 1..32 掃過並否證**
    （`shape_probe/sweep_nsg.py:38-42` 那三條形狀就是 `iq4_xs`；pipeline 名
    `kernel_mul_mv_iq4_xs_f32_nsg=8` 是硬證）。而且 dense 家族有**自我否證的上界**：
    全部打到 100% DRAM 峰值也只有 **2.42%** ⇒ tile size / unroll / vector width 也救不了。
  - **三層成本階梯**（決定「要不要跑」而不是「能不能跑」）：調度 knob 94 s ／ kernel·泛函常數
    （`CGC_MMV_NSG`、clamp 1..32）94 s 且**免重建**（Metal function constant）／
    **kernel·原始碼 = rebuild + 重跑 m123 anchor gate**（會蓋掉別條線正在 mmap 的 dylib）。
  - **joint（shape ⊗ kernel）的儀器已經存在**：
    `scripts/check/shape_probe/mmid_shapes.py --tokens 1,2,3,4 --nsg-sweep ...`，
    shape 軸是 MTP tokens/op、kernel 軸是 NSG，用權威口徑 `marginal=(g(64)-g(32))/32`，
    每個 arm 對**實際編出的 pipeline 名**驗（不對 env 變數驗）。selftest 27/27。
  - **它的第一輪已經跑過，verdict 全 INVALID**（`Backup/phase_decomp/L3/shape_probe/mmid_nsg_sweep_rotated.json`）：
    窗口 `busy-overridden`（usable 5.18 GiB < 共用門檻 7.8 GiB）、**null cell 自身寬 44%**。
    ⇒ **先診斷 null cell，再診斷 kernel。** 方向對了不代表讀數能用。
  - **還沒被否證的只有 MoE `MUL_MAT_ID`**：%峰值 **20–36%**，對比 dense 的 76–91%（lm_head 91.2%）。
    量級換算 ~10% of step，**但中位數站在 44% 寬的 cell 上，不可引用**。
  - **`CGC_MMV_FUSE=1` 有相反記錄**：`autotune_mmv.py:108-115` 記載 777/800 rows divergent、
    端到端 15.94 vs 22.07 t/s 更慢、draft accept 崩。要把它列為下一步前，必須先在 prod25 cell
    重現或宣告不適用 —— **不能當不存在**。

- **★★ 第 24 條（2026-09-22 夜 §EN-443）：有人說「knob 太少所以找不到 2×，要 15 個全掃」時，先做三件事**
  - **第一步：查它是不是已經被判過，或根本不是自由變數。** 例子（本專案實測）：
    `n_batch`/`n_ubatch` **不是 knob** —— pool 開著時 `llama-context.cpp` 把它夾到
    `cgc_pool_max_tokens()`(=8)，寬 batch 會 `GGML_ASSERT(n_tokens_all <= cparams.n_batch)`，
    llama-bench 又不能外部設 ⇒ harness 固定 `-b 8 -ub 8`，**它被 M 綁死**；
    「dense vs pool 記憶體分配」**就是** pool size 同一個自由度（dense 走 OS page cache，沒有第二個把手）；
    tree speculation **在本 fork 不存在**（全樹 grep 無 tree/trie）；tile/unroll/vector width
    **沒有 env 把手** ⇒ 代價是 rebuild + 重跑 m123 anchor gate（會蓋掉別條線的 dylib）。
  - **第二步：找有沒有「自然實驗」已經給過答案。** 置換策略（LRU→LFU）看起來很誘人，但 09-21 `IO_PATH_AB` §③
    已經做過它的反向版本：4 GiB vs 8 GiB ⇒ miss **1.91×**、capacity miss **3.15×**、`cb` 41.8→**90.0 ms**，
    而 **t/s 10.60 vs 10.28 不動**。⚠ 那是跨 run（single-run ±27% 不能跨場比）⇒ 它是「降優先」不是「結案」。
  - **第三步：算 objective 的價格，不要只看 knob 的數量。** 同一個問題兩個儀器差兩個數量級：
    surrogate（`shape_probe/mmid_shapes.py`）跑完一整條軸 **~3 分鐘**、噪音底 **~2.4%**；
    端到端（prod25 cell）**94 s 一次 launch**、CV **11.2%**（log σ≈0.28）⇒ 認證 3% 要 ~1350 launch ≈ **35 h**。
    ⇒ **加 knobs 不會讓 3% 的效應變可見**（§EN-440：16 launch、σ=0.16 要真差距 ~68% 才認證得到）。
  - **正確的話術**：不是「多掃幾個」，是「先把篩選搬到便宜的 objective：surrogate 分鐘級全掃 →
    只有 ≥10% 的 arm 才值得上端到端」。加 axis 前先問：**這個 objective 一次多少錢、噪音底多少、認證 δ 要幾個樣本。**
\n
- **★★ 第 25 條（2026-09-23 §EN-444）：發任何量測之前，先問「這台機器現在是誰的」——而且要問兩次**
  - **第二次才是那趟 那一趟最值錢的**：起點問「能不能發」，終點問「機器有沒有在過程中換人」。
    前者你早就做得到，後者無法提前回答。已做成 `judge_window(before, after)` 放在
    `scripts/check/shape_knob_search.py`：`hijacked`（淨→忙，中途換人）是一個自己的類別，不是 drift；
    而且**有向** —— 只有「失去」機器算 hijack，反向（忙→淨）仍是 busy-overridden。
  - **用共用詞彙 `server_window.decision()`，不要自己重打一個。** 它同時給 harness 與 launcher 兩個答案，
    並標出 `binding` / `agree`；它看得見**外來的 llama 行程與 listening port** —— 這兩件事在任何
    sweep 自己印出來的數字裡都看不見。多份 threshold 就是這份筆記已經付過兩次的那個缺陷。
  - **實測到的事實（2026-09-23 夜）**：Mac16,12 / 16 GB 這台，**在完全沒有 llama 行程時
    reclaimable 也只有 ~6.3 GB**（連採五次 6376/6242/6229/6385/6433 MB），而共用門檻
    `NEED_MB=8000` ⇒ 桌面 app 常駐下**根本達不到**；那一夜的擺幅 0.83→9.43 GiB，
    9.43 GB 那次是別人 8.1 GiB 的 server **退出那一瞬間**才量到的。
    ⇒ 「乾淨窗口」在共用的小記憶體盒子上取決於別人的行程週期，不是排隊等得到。
  - **microbench 有 footprint**：`mmid_shapes.py` 跑一次把 swapfile 從 5.1 推到 6.1 GiB（72 次 probe
    invocation）。⚠ 我當初多寫了「**不會自動縮回**」—— **錯，它會**：實測 total 回到 4096 MiB。
    我把「觀測到它還沒縮」寫成「它不會縮」（**暫態當成本質**）。正確的保留部分：量完要知道自己推過 swap。
  - **先對時間線，再怪鄰居**：我一度把兩個 high-noise 的 sweep 怪到同步起來的 server 頭上，
    一查 `ps`（`ps -p <pid> -o lstart`）才發現它是在我的 sweep **結束後**才起來的。無罪但結論不變：
    讀數仍然不可用 —— 因為真兇是儀器自己的 footprint、拖垮一切的 pager。
  - **不要偷偷放寬自己要用的門檻**：`NEED_MB` 的校準根據是「guarded run 載不進模型」＝**載入失敗**
    門檻，與「讀得穩」的判準（joint probe 的 `nsg_decide` 要求 null cell ≤5%）不是同一件事。
    想換判準要在 `server_window` 層帶證據改，不能在自己的 harness 裡放寬。


26. **有人說「measurement tool 太吃記憶體、降 footprint 就好」時：先定價，再決定要不要砍**
    （2026-09-23 實例，全文 `docs/FOOTPRINT_HALFSTEP_2026-09-23.md`）
    - **三個成分要先分離**，砍錯一件就白砍：**分配量**（pool/bank/buffer）／**持續時間**（熱頁被摸多久）／
      **幾何**（bank 多大）。第三件**不是我們的**：在本 fork 砍 `--experts` 就是換一個模型在量。
    - **「行程多大」和「盒子少了多少」是兩個不相干的數字**：pool 320→146 MiB 讓**盒子可用下陷 −26.4%**，
      但**峰值 RSS 三臂都一樣 232 MB**（pool 超過 bank 的頁從沒 fault-in）。
      ⇒ 要看的是執行當下共用 `server_window` 的 `vm_free_mb`，不是 `/usr/bin/time -l` 的 child RSS。
    - **single-invocation 的下陷看不見「累積」**：joint sweep 標頭寫 1.25 GiB，單次只掉 0.4 GiB，少掉的 6 GB 叫累積。
      解法是 `--sequence N` 追**每次的出發點**，而且**多臂交錯**（讓共有的飄移公平分攤），不是單臂連打。
    - **判準要寫在程式裡再跑，不能跑完才決定**：可分辨與否的唯一根據是
      「差值是否大於兩邊各自的跨度」；只有一個 rep 就沒有跨度估計 ⇒ 要回 `unjudged`，不是回一個小贏。
      同一條規則適用於任何多 arm 比較：先有跨度估計，才有「可分辨」。
      （這條 register 住了自己的 selftest：我第一版把 1.7% 的差判成「可分辨」，是 selftest 抓出來的。）
    - **結果可能是否定的，而這正是有價值的產出**：本案中 footprint 槓桿量得到（−26%）但比真因**小 20 倍**
      （真因 = 跑的兩分鐘裡另一條線的 `llama-server` 正在載模型，盒子自己在 18 秒內擺 **1463↔9869 MB**）。
      ⇒ 結論是「**共用門檻維持不動**」，而且是「量過才維持」，不是「預設不動」。
    - **這種結論仰賴 begin/end 視窗**：只看 import 那一刻的 snapshot 回答不了「跑的時候盒子是誰的」。
      加 `window_now()` + `lost_the_box(w0,w1)`（**有向**：「乾淨→被佔」才算遺失；probe 讀不到回 **None 不回 False**，
      否則從沒取過視窗的那一輪會被印成「沒事」）。**而且閘門要真能擋（`return 2`）**——只印不擋等於沒裝。
    - ⚠ **附帶的自我更正（同一毛病）**：我前兩輪寫過「swapfile 不會縮回」「這台機器沒有 8 GB 空檔」，
      兩句都**被自己反證**（實測回 4096 MiB；連採六次 9364–9428 MB）。
      教訓：不要把「我採到的時間點」寫成「它的性質」，也不要把單一樣本寫成分佈。

27. **「同一個 config 重複跑卻散開」時：先做變異數分解，再談原因**
    （2026-09-23 實例，全文 `docs/K3_SWING_ANALYSIS_2026-09-23.md`，工具
    `scripts/check/k_swing_decompose.py`，selftest 18/18，**全程零 GPU**）
    - **「時間在飄」是兩個不同的假設，不是一個**：(a) 每一步都吵 ⇒ launch 條件無關，只有「每條 arm
      多跑幾個 request」有效；(b) **每次 launch 被指定一個不同的基準**，步與步之間其實很緊 ⇒
      配對/暖機/重複的行為完全不同。**這兩件事的答案決定整個實驗設計，而且通常已經躺在存檔 JSON 裡。**
    - **分解的前提是「一 arm = 一 launch = 多個 request」**——所以量測工具從一開始就要存
      **逐 request** 的數字，不能只存 arm 平均。只存平均 ⇒ 這條永遠做不了。
      估計式：單因子隨機效應，`MS_within` 期望 σ²_within、`MS_between` 期望 σ²_within + m·σ²_launch
      ⇒ **`σ²_launch = max(MS_between − MS_within, 0)/m`**。`max(…,0)` 是刻意的地板：
      MS_between<MS_within 會偶然發生，意思是「沒測到」，**不是負變異數**；印負數只會被人拿去相減。
    - **F 臨界要當參數傳進去（`--f-crit`），不能在看到資料後調**——與 `nsg_decide` 的 5% 同一條紀律。
    - **這個分解會順便解釋「配對為什麼沒用」**：配對（AB/BA）只能扣掉**兩組共有**的項。
      若只有一組有 launch 級項，配對 sd 就只能 ≈ √2 × 單臂 sd —— 實測 2.28 vs 2.18 t/s，吻合。
      **所以「配對沒買到東西」本身是一條證據，不是一句抱怨。**
    - **工具要印三個數字**：launch 級 sd、一個 arm mean 的總散佈、以及「單靠 request 噪音原本該散多少」。
      第三個才是配對有沒有用的判據。順手用它定價：`n = (總散佈/目標%)²` ⇒ 本案 k=3 要 25 臂/組 ≈ 58 min，
      k=2 只要 4 臂。**「消掉/觀測到那個項」通常比「多跑 arm」便宜一個數量級。**
    - ⚠ **`n = (散佈/目標)²` 壓的是標準誤，不是信賴區間**——95% CI 半寬要再乘 1.96² ≈ 3.8 倍。
      兩個數字都要印，只印一個會讓人把「4 臂」當成結論（本案：k=2 其實是 4(SE)/16(CI)）。
    - ⚠ **`max(MS_b − MS_w, 0)` 回 0 時不要寫成「沒有」——要給上界。**
      模型下 `F_obs/(1+m·λ) ~ F(df1,df2)`，`λ = σ²_launch/σ²_within`，反解 F 的**下尾**即得
      「資料排除不掉的最大 λ」。本案 k=2 點估 0.00% 但 **95% 上界 9.98%**；k=3 點估 13.16%、上界 35.78%。
      上界與點估差 5–6 倍 ⇒ 那個 n 根本沒被定價，要誠實說「n 太小，無法定價」。
      （**我第一版把 `F_0.05(4,10)` 算成 0.6126，正確是 0.1677** —— Simpson 積分寫錯，
      且錯的方向剛好有利於我本來就想相信的結論。修法：用不完全 beta 實作的 `f_cdf`，
      並用「`f_cdf(f_ppf(p))==p`」＋「`1/F_0.95(d2,d1)`」＋一個已知外部常數**三個錨把它釘進 selftest**。）
    - **注意「arm 自己把自己跑熱」**：本案 k=2 有 4/5 臂在 3 個 request 間單調下滑（最多 −24%）
      ⇒ ~70 s 的 arm 本身就是一條發熱曲線，**launch 前的閘門管不到它**。要看 first→last 的斜率。

28. **要比較兩個「看起來只差一個寬度」的 arm 時：先確認它們走的是不是同一條 kernel/PSO**
    （同上，2026-09-23）
    - 寬度（batch/ntok/n_seq_tokens）常常**不是純量**，而是**分支條件**。本案：MTP k=3 一步 4 token、
      k=2 是 3 token，而 `ggml-metal-ops.cpp:2590` 的 small-batch `mul_mv_ext` 對 **Q_K 家族門檻是
      `ne11>=4`**、對 F32/F16/BF16/Q8_0 那群是 `ne11>=2`（`:2581`）⇒ **ntok=3 與 ntok=4 走不同 kernel**；
      ext 內 `nxpsg`（`ne11<3`）、`r1ptg`（`case 4`）也不同 ⇒ 兩個不同 PSO。
      **結果：任何「寬一點划不划算」的 t/s 差，都混了「那顆 kernel 好不好」。**
    - **查法**：對每個型別群把進入門檻列出來（含「哪些型別根本不在清單裡 ⇒ 永遠走不到」），
      再對照該模型的 tensor 型別分佈。**順便會發現某些 knob 根本摸不到那條路**
      （本案 `mul_mv_ext` 裡 `nsg` 是寫死的 `=2` ⇒ `CGC_MMV_NSG` 無效）。
    - **也要查「看起來很像的那個開關」是不是真的開著**：本案相位閾值（`llama-cgc-phase.h:133-143`）
      一度是最可疑的候選，但**既有 log 的 `CGC-PHASE-SPLIT … width=8` 直接否證它**（3 與 4 都 ≤8）。
      **先 grep 存檔 log 再讀碼**——banner 常常已經把答案印出來了。
    - **「計數相同」不等於「時間相同」**：`CGC-SYNCFILL` 印的是**請求數**（`llama-context.cpp:6497`
      `cold_filled=%zu`），真正的讀是 `pread`（`llama-expert-cache.cpp:35`）⇒ 服務時間看頁快取。
      **所以「四次 arm 計數逐位元相同」完全不能排除 IO 服務時間差 1.43×。**
    - **動手前先找「樹上已經有、但我們沒開」的計數器**：本案 `llama-expert-cache.cpp:432` 的
      `CGC-RIG-SNAPSHOT … pread_usec=` 正是判生死的量，而 **09-23 全部 log 零命中**。
      ⇒ **最便宜的下一步常常是「把那一行打開」，而不是多跑幾十條 arm。**
    - **per-request 切換參數前先確認它沒被 `#if 0` 關掉**：本案 `server-schema.cpp:200` 的
      `speculative.n_max` 就在 `:198` 的 `#if 0` 裡 ⇒ 「單一 process 內交替 k」做不到。
      **設計了一個漂亮的單 process 實驗之前，先確認那個開關存在。**


29. **有人問「所以結論是用 X 配置、N 次重複？」時：先確認那是「效應」還是「噪音」**
    （2026-09-23 續，報告 §7）
    - **「哪一組比較不吵」和「哪一組比較快」是兩個問題**，而且後者常常更大、更便宜、也更該先答。
      本案：k=2 不只散得少（launch 級項 0%），它**平均快 16.4%**（12.13 vs 10.43 t/s）。
      只回答噪音問題 ⇒ 把一個 16% 的效應當成量測衛生問題處理掉。
    - **大效應不需要多臂**：配對 sd 1.83 t/s、diff 1.71 t/s ⇒ 約 **8 對 ABBA**（當時已有 5 對）。
      ⚠ 我第一版寫「7 對」：n=7 時 t=2.47 vs 臨界 **2.447**，**只過 0.025**，而 sd 本身是 n=5 估的
      ⇒ 那是一場 50/50 的賭。**用觀察到的 sd 去算 n，一定會算出一個剛好過的 n** —— 因為那個 sd
      就是讓它剛好過的那個樣本來的。要多留一點，且臨界值隨 df 下降本身也是餘裕來源。
      這比「25 臂」便宜一個數量級，**而且一次回答「台架用哪個 k」＋「交付 cell 該不該換 k」兩個問題。**
      ⇒ **先認證效應，再談重複數。** 順序反了會在錯誤的 k 上精算臂數。
    - **看形狀，不要只看 sd**：本案 k=3 是**雙峰**（3/5 掉到 8.6–10.7、2/5 正常 11.5–12.3），
      不是「吵」。⇒ 「有些 launch 掉進一個慢狀態」。
      **變異數項本來不該 bias 平均；它會 bias，正因為慢狀態是單邊的、不是對稱噪音。**
      所以「launch 級變異」和「平均較慢」可以是**同一件事**的兩個讀法，別當成兩個現象。
    - **動手前先查那個旋鈕是不是真的存在、要不要 rebuild**：本案 llama-bench 有
      **`--spec-draft-n-max`**（`llama-bench.cpp:666`，預設 3，1..16），`run_server.sh:436`
      把 `CGC_SERVER_MTP_N_MAX` 轉給它，`scripts/run_n30cache.sh:86` **預設就是 2**
      ⇒ 換 k 是一行旗標，不必 rebuild。**「預設值」常常藏在某個 script 裡，而且可能跟你以為的不同。**
    - **但換台架 ≠ 結論可搬**：k 同時換 kernel/PSO（見第 28 條）⇒ 在 k=2 排出的 knob 名次
      不能直接搬到 k=3。所以流程是「先認證 k ⇒ 若換則台架自然變 k=2 ⇒ 才在 k=2 上精算臂數」。


30. **「再跑 N 個就夠了」這句話本身是一個統計錯誤**（2026-09-23 續，協定見
    `docs/K3_PAIR_CERT_2026-09-23.md`；工具 `k_swing_decompose.py --paired --planned-n`）
    - **看過資料才決定要再跑幾個 = optional stopping**，它不會保留那個 α。
      所以 n 要**事先登記**，而工具要**真的拒絕**：`n < planned ⇒ NOT YET`（不是結果）、
      `n > planned ⇒ OVERRUN`（判決已發生，多跑的是另一個實驗）。
      「再 2 對就夠」聽起來像勤儉，其實是把固定 n 檢定換成一個沒有校正的序列檢定。
    - **用觀察到的 sd 反推 n，一定得到一個「剛好夠」的 n** —— 因為使它剛好夠的正是那筆樣本。
      實例：n=7 ⇒ t=2.47 vs 臨界 2.447（過 0.025）；改 8 ⇒ t=2.64 vs 2.365。
      而且臨界值本身隨 df 從 2.776 降到 2.365，這也是餘裕的一部分。
    - **偷看已經發生時，就把它寫進協定而不是假裝沒有**：本案 n=5 的 t=2.08/p≈0.11 是先算出來的。
      可補償的部分 = 「判決只做一次、做在登記的 n」；不可補償的部分要明寫。
    - **事先把「不顯著時要做什麼」也寫死**。否則「不過 ⇒ 再加幾對」會自動發生。
      本案：不過就改看機制（池 IO 時間），不是加樣本 —— **雙峰分佈的 sd 會越抽越大，加對無效。**
    - **t 分位數不要另外引一套實作**：用 `|T|>t ⇔ F(1,df)>t²` 複用既有的 F 尾，
      並把 `t_crit(4)=2.776 / t_crit(6)=2.447 / t_crit(10)=2.228` 釘進 selftest（外部錨）。

31. **「這個計數器要打開得改碼重編」常常是錯的：先找它的呼叫點有沒有 env 閘**
    （2026-09-23 實例）
    - 本案 `CGC-RIG-SNAPSHOT … pread_usec=`（判生死的量）在 09-23 全部 log 零命中，
      我原本以為要動 C++。**讀碼後發現它唯一呼叫點 `refresh_prefill_protect()`
      （`llama-expert-cache.cpp:446`）在 `CGC_PREFILL_PROTECT_FILE` 未設時直接 return**
      ⇒ 設一個 env 就有，**不必 rebuild**（在本 repo 特別重要：rebuild 會蓋掉別條線的 dylib）。
    - **查法**：先看那個 print 函式的**所有**呼叫點，再看呼叫點的 early-return 條件。
      只看「有沒有這個 env 名稱」會漏 —— 本案的閘是另一個 env（`CGC_PREFILL_PROTECT_FILE`）。
    - **開關要開成「維持現狀」那一檔**：本案 flag 檔寫 `"0"` ⇒ protect 維持關閉（＝預設行為），
      但 rig 照樣 snapshot。**做觀測不該同時改行為**，否則讀到的差混了兩件事。
    - ⚠ **`getenv(...) != nullptr` 這種寫法，`VAR=0` 也會開**（`:474`）——A/B 一定要用 FILE 開關。
    - ⚠ **舊 doc 的復現命令可能有不存在的 env**：本案
      `docs/O1_PREFILL_PROTECT_RERUN_2026-09-19.md:87` 的 `CGC_RIG_SNAPSHOT=1` 在 src 裡 grep 不到。
      **照抄之前先 grep 一次 src；新配方沒有實跑驗證過的話，要明寫「讀碼得來、未實跑」。**
    - 順帶：**同一趟就把兩個量都收了**（配對 t/s ＋ `pread_usec`），
      「為了機制再多跑一輪 arm」幾乎總是可以避免的。


32. **★★ 引用 `CGC-DECPROF` 的 `wait` 之前，先確認你在講 GPU 還是 CPU —— 兩者指向相反的解法**
    （2026-09-23 實例，我自己講錯過一次）
    - `ggml-backend.cpp:2109-2122` `hook_seg()` 的三個區間：
      `wait = st1-st0` ＝ **CPU 自旋忙等** GPU 跑完該段（`while (cgc_done(...) < target) sched_yield()`）；
      `cb = st2-st1` ＝ top-k hook；`submit` ＝ 提交下一段。
      **GPU 空轉是另一個欄位 `gap_sum`**（Metal 時間戳：上段 end → 下段 start）。
    - 實測（穩態 verify step，n=221 中位數）：`total 161.0` ＝ `wait 123.2 (78%)` ＋ `cb 23.8` ＋ `submit 10.8`；
      而 GPU 側是 `union 113.4 (71% 忙)` ＋ `gap 44.4 (28% 空)`，**union+gap = 157.8 ≈ total 161**。
      ⇒ 「wait 78%」讀成「GPU 空轉 78%」會把解法從「讓 CPU 不必在關鍵路徑上」
      翻成「讓 GPU 多做事」，**整套做白工**。
    - **查法**：欄位語義去讀**印它的那段程式碼**，不要從名字推。
      本案兩個儀器還互相打架（`CGC-GPUTIME` 的 `gpu_union=97%` vs DECPROF 的 `wait 71-77%`），
      判掉的方式是找閉合式（`union+gap ≈ total`），不是選一個相信。
    - **歸因 gap 要用回歸不要用中位數**：本案 `gap ~ (cb+submit)` 得 **r=0.957、slope=1.05、
      截距 +10.1 ms** ⇒ 近乎 1:1 的因果（GPU 空轉 = CPU 的 hook＋submit ＋ 啟動偏斜）。
      只比中位數看不出這件事。
    - ⚠ 同一支 log 裡混著 **draft model 的廉價 step**：本案 552 條 DECPROF 裡只有 298 條是
      verify（`segs=41` 且 `total>50`），其餘 ntok 小、total 中位數只有 3.3 ms。
      **不分層取中位數會得到一個完全假的數字。**
    - 順帶一條硬約束（註解原文）：*Waiting only on the main buffer fired the top-k hook while
      the argsort was still running → stale ids → garbage remap → whole-graph corruption*
      ⇒ **必須等整段跑完**，不能用「只等主緩衝區」這種看似便宜的最佳化。

33. **★★ 要驗證「CPU/GPU 重疊」類優化，主指標用 step 級計數器（`gap`/`cb`），不要用 t/s**
    （2026-09-23；上游是第 32 條的 wait/gap 口徑）
    - **成本差一個數量級**：`gap` 是 step 級，一支 log 就有 1300+ 樣本、**不需要跨 launch 配對**；
      `t/s` 是 launch 級、單臂 sd 18%、要 8 對 ABBA。同一個問題用前者可能只要一輪 A/B。
    - **先算斜率再決定做不做**：對穩態 verify step 做 `gap ~ (cb + submit)` 迴歸
      （`scripts/check/gap_attribution.py`，selftest 33/33）。
      實測 **slope 1.02、r 0.981、截距 +7.2 ms**。
      ⇒ slope ≈ 1 表示 CPU 的每一毫秒**全額**變成 GPU 空窗 ⇒ 把 CPU 挪出關鍵路徑回報是全額的；
      slope ≈ 0 表示空窗另有來源 ⇒ 重疊類優化打錯靶。
      **截距是這條路的地板**（本案 7.2 ms，消不掉）。
    - **可行性先看「幾何餘量」，不要先看命中率**：本案 GPU 窗口（`wait` 129.8 ms）是 CPU 需求
      （`cb` 39.3 ms）的 **3.3×** ⇒ 即使預測全部落空、每個都要真讀也來得及。
      ⇒ 「預測目標 100% 已 resident」對「省 fill」是死亡（F2），對「把 CPU 挪出關鍵路徑」
      卻**無害甚至有利**。**別用 prefetch 類實驗的 null 去否決預指派類設計。**
    - **prefetch ≠ 預指派**：`prefetch_slot` 只消「fill」；F1 模型 `cb = 0.46·L + 0.695·M` 的
      `0.46 ms/層` barrier **只要該層有 ≥1 miss 就照付** ⇒ 要連 barrier 一起消，必須讓該層在
      **真 ids 到達時已無 miss**（提前填），而不只是「背景發 IO」。
    - **預測源優先順序**：MTP 的 draft ctx 已在 verify 之前算出下一 token 的 ids
      （`llama-context.cpp:5386`，accept 92–98%）**優於**「猜上一 token 重演」（~87%），且已在樹上。
      ⚠ 真門檻是**整層 union 全覆蓋**，不是 per-expert：U=10、p=0.87 獨立 ⇒ `0.87^10 ≈ 25%`
      ⇒ **pred 必須比 top-8 寬**，或換高 p 的源。先量再動手。
    - **安全不變數**：快路徑的條件必須是「**真 ids 全部 resident**」，不是「預測對了就跳過」
      —— 預測只決定**提前填什麼**，不決定**相不信任結果**。
    - **計數器要有 `calls`**：`calls == 0` 就宣告該輪無效（F2 的 env flag 有設但沒被呼叫）。

34. **★★ 給「預測類」優化定價時，`f_clean` 是 `r^U` 不是 `q^U`，而 `r = p_res0 + (1-p_res0)·q`**
    （2026-09-23；我第一版把 q 直接當 r，**把預指派低估一個數量級**：q=0.87 → 我說 +0.8%，實為 +17.2%）
    - **錯在哪**：門檻事件是「整層 union 零 miss」，但「resident」不只有「被預測覆蓋」一條路 ——
      **基線已經有 `p_res0`（本案 85%）常駐**，預測只救那 `(1-p_res0)` 冷的。
      漏掉 p_res0 等於假設基線常駐率是 0，於是指數 `U` 打在一個過小的底上。
    - **自檢**：`q=0` 時 `f_clean` 必須等於基線 `p_res0^U`。把它寫進 selftest（本案第 3 項）。
    - **指數要拿實測 union，不要拿 top-k**：本案 U=15.5（ntok=4）不是 8 ⇒ `0.87^15.5 = 11.6%`，
      而使用者直覺算的 `0.87^8 = 33%`。**指數差 2 倍，門檻差 3 倍。**
    - **★ 結論：槓桿是「預測寬度」不是「預測準度」**。寬度決定 q 的**結構上限**
      （8 ids → 51.6%、16 → 76.3%、**24 → 91.1%**、32 → 100%），而準度只能在那之下打折。
      ⇒ 先買寬度（可測、可控），相關性／準度當安全邊際（只能買 ~10 個百分點）。
    - **★ 動手前先找「已經被算好但被丟掉」的寬度**：本案 draft ctx 一次算好 3 個 token × 8 = 24 ids，
      而 `llama-context.cpp:5390-5391` **只取 j=0 一行**（理由「verify 從第一個 token 驗證」
      —— 對 prefetch 的優先順序成立，對 prebind 的覆蓋率**不成立**）。
      **改成取 j=0..n_tokens-1 是一行改動，收益 +5.4% → +19.5%。**
    - **相關性用共用隨機效應量化**，不要只說「專家選擇不是獨立的」：
      `r = logistic(mu + sigma·z)`、`f = E[r^U]`。`r^U` 對 r 凸 ⇒ Jensen ⇒ sigma 上升確實幫忙；
      實測 sigma 0→3 只把 f 從 38% 拉到 75% ⇒ **幫忙但買不到一個數量級，不能當主論據**。
    - **事前寫死的 gate 要打在 q 上，不要打在 f_clean 上**：f_clean 是 q 的函數，
      用函數當 gate 等於把「門檻」和「模型」混在一起。本案：step −10% ⇒ **q ≥ 0.731**。
    - 工具：`scripts/check/prebind_ev.py`（`--self-test` / `--sweep` / `--breakeven`）。

35. **★★ 要引用 F1 的 `cb = 0.46·L + 0.695·M` 之前，先跟實測 cb 對一下 —— 它可能不相容**
    （2026-09-23）
    - 本案 ntok=4 實測 `cb = 39.3 ms`；若 p_res0 = 0.85，則 M ≈ 40 層 × 15.5 × 0.15 ≈ 93，
      **單 fill 項 `0.695 × 93 = 65 ms` 就超過實測 cb** ⇒ 係數與該 regime 的實測不相容。
      反解則得 p_res0 ≈ 0.95，與註解寫的 15% 冷缺口**差 3 倍**。
    - ⇒ **兩者必有一錯（或來自不同 regime）。在拆掉之前不要用那組係數做定價**，
      改用校準到實測的線性代理，並把矛盾本身登記成待量項。
    - 通則：**任何「係數模型」引用前，用代入法驗一次量級**。數量級不對就停手。

36. **★ 「按直方圖重分配」類的優化，先查直方圖是不是平的**
    （2026-09-23；M5 prerouter 的實測 `precision = 4.2%`，隨機基準 8/256 = 3.1%）
    - 樹上**已經有被量過命中率的預測器**：`CGC_PREROUTER`
      （`llama-expert-cache.cpp:1446-1521`，全域頻率直方圖），結案在 `docs/M3_M5_CLOSURE_2026-09-17.md`。
    - 4.2% ≈ 隨機 ⇒ **路由的邊際分佈幾乎是平的，沒有「永遠熱」的專家**
      ⇒ 「按路由直方圖按層重分配容量」**沒有可搬的東西**，不是「搬到別層」的問題。
    - **但它不能否決時間局部性類的預測**（prev-token 87%、draft ids）：
      「長期每個專家一樣熱」與「相鄰 token 高度重複」**完全相容**。
      ⇒ 判準：**看預測源是「邊際分佈」還是「時間局部性」，兩者的實測結論不可互搬。**
    - 順帶：**容量類優化不是重疊類優化的替代品，是它的乘數** ——
      本案 `p_res0` 0.85 → 0.95 讓同樣寬度的收益從 +5.5% 變 +9.6%、門檻 q 從 0.731 降到 0.578。

41. **★★ 儀器的「條件不成立」要印出來，不要把多個數字綁在同一個 `if` 上**
    （2026-09-23；第一支 `CGC-PREBIND-PROBE` log 整輪 0 行輸出，白跑一趟 13 GB 載入）
    - 錯法：把「需要預測源的量」（q）和「不需要預測源的量」（U、p_res0、clean）寫在
      **同一個 `if` 裡**，條件裡含 `draft_all_valid[il]`。預測源一壞，四個數字**一起消失**，
      而且 log 上「0 行」無法區分：env 沒到？層對不上？draft 從沒跑？
    - 正確：**拆開**。U／p_res0／clean 無條件輸出；q 只在 `pred=1` 的層上聚合，
      並且**每一行都印 `pred=0/1`**，讓「有多少層真的有預測」本身成為可看的診斷量。
    - ★★ 更關鍵的是**分母**：`pred=0` 的層 cov 是 0，把它們放進 q 的分母會把
      「預測源沒收集到」**偽裝成「預測不準」** —— 兩種失敗的解法完全相反
      （前者改收集、後者改預測器/寬度）。⇒ `q = Σhit / Σuni(pred=1)`，兩個分母分開記。
    - 通則：**儀器的每一個輸出數字，都要能單獨回答「是沒量到還是量到很低」**。

42. **★ 起量測之前先確認三件事：env 有沒有 allowlist、參數名是誰的、沉降期**
    （2026-09-23，同一輪連踩三個）
    - **① env allowlist**：`scripts/run_server.sh` 的 launch 行是
      `env "${SERVER_ENV[@]}" "$BIN" ...`，**沒有列進去的 `CGC_*` 會被靜默丟棄**。
      新加的 flag 必須自己 `SERVER_ENV+=(...)` 一段（腳本內已有十幾個例子與註解）。
      但 `llama_bench_matrix.py` 是 `run_env = dict(os.environ); run_env.update(env)`（**merge**），
      ⇒ 同一個 flag 走 launcher 要登記、走 bench matrix 不用。**兩條路的規則不同。**
    - **② 參數名屬於哪一層**：`prod_profile.py` 的 `DECODE_SHAPE` 用
      `--prompt/--gen/--depths/--batch/--reps`，那是 **`llama_bench_matrix.py` 的**參數；
      `llama-bench` 自己認的是 `-p/--n-prompt`、`-n/--n-gen`、`-d/--n-depth`、
      `-b/--batch-size`、`-r/--repetitions`。字面搬過去會得到
      `error: invalid parameter for argument: --prompt`，0.4 s 印完 help 退出。
      ⇒ **要嘛走 `llama_bench_matrix.py`（讓它轉），要嘛用對的名字，不要混。**
    - **③ 沉降期**：`ggml_metal_rsets_init: ... residency set (keep_alive = 180 s)`
      ⇒ 前一個 llama 行程退出後的 **180 s 內 GPU 記憶體還被佔著**。實測：別線 server 一退
      就起 bench，`usable 52%` 照樣 `CGC-METAL-FAIL: status 5, Insufficient Memory` -> SIGABRT。
      ⇒ **窗口的定義不是「沒有行程」，是「沒有行程且最後一支走了 ≥180 s」。**
    - 附帶（同一輪）：`prefill250` arm 強制 `-b/-ub 5632`，在 16 GB M4（working set 11453 MB）
      上 model 4617 MiB + 8 GiB pool 會 OOM；`prod25-stream` 不帶 -b/-ub 就能跑。
      ⇒ **換 arm 前先比 `-b/-ub`，不要只看 pool 預算。**

### ⚠️ 第 43 條：**配適用的統計量，不能是模型要預測的那個統計量**（2026-09-23）
   場景：定價模型有一個自由旋鈕（這裡是層內相關性 `sigma`），而你手上只有一個可以
   對上模型的實測量（這裡是 `clean_pct` = 整層零 miss 的層比例）。
   直覺做法是「用 clean_pct 反解 sigma」—— **那是循環論證**：
       模型要預測的量是 `E[r_z^U]`（U 階矩），而 clean_pct 正是它。
       拿它配適出的 sigma 再去降門檻 ⇒ **用來放寬的數字 == 放寬後要預測的數字**，
       而且方向永遠是「讓結論更好看」。
   （實測：由 clean_pct 反解 sigma = 1.191 ⇒ 門檻 0.543；由變異數估 1.019 ⇒ 門檻 0.595。
     差 0.05 的門檻就是 PASS/FAIL 的分界，而且反解那一側永遠比較寬。）
   **改法：用另一個統計量配適，讓原本那個變成驗證點。**
       二階矩（逐層常駐比例 `res=` 的變異數）估 sigma，再用它預測 U 階矩（clean_pct）。
       `res_layer = (1/U)·Σ Bern(r_z)` ⇒ `Var[res] = Var[r_z] + E[r_z(1-r_z)]/U`；
       二項修正項本身依賴 sigma ⇒ 不動點迭代（`prebind_ev.sigma_from_res_spread`）。
       「二階矩能不能預測 U 階矩」是一個**可以為偽**的檢定 —— 它過了（28.9% vs 32.9%，
       +4.0 pp）才能說這個函數形式站得住。
   ⇒ **通則：配適 k 階矩，驗證 m 階矩（m ≠ k）。** 用同一階矩配適又驗證，永遠會過。
   ⇒ 附帶：先檢查訊號是不是取樣噪音。這裡二項噪音 `E[r(1-r)]/U` 的 sd 是 0.066，
     觀測 sd 是 0.108 ⇒ 訊號只佔 62%，但足以辨識；若觀測 sd ≈ 噪音 sd，就**不能**估。

### ⚠️ 第 44 條：只要「編譯檢查」時，**只建目標檔，不要重連結**（2026-09-23）
   `cmake --build` 是對「機器上所有正在跑的實驗」的一次寫入（蓋掉別條線正在 mmap 的
   `libllama.dylib`）。但很多時候你只是想確認 `set -e` 之外的新程式碼能不能編譯，
   根本不需要可執行檔。**這時候可以安全地繞過危險：**
       cd src/llama.cpp/build && \
       make -f src/CMakeFiles/llama.dir/build.make \
            src/CMakeFiles/llama.dir/llama-context.cpp.o
   只更新 `.o`，**不重連結** ⇒ `bin/libllama.dylib` 完全不動 ⇒ 不會殺掉別人的行程。
   ⚠ 兩個前提（都踩過）：
     - 頂層 `Makefile` **沒有 `.o` 目標**（`make src/.../x.cpp.o` 會靜默什麼都不做，
       exit 0、無輸出，看起來像「已經是最新」）⇒ 必須用 `build.make`。
     - cwd 必須是 **build 根**（`build.make` 內部用 `src/CMakeFiles/...` 相對路徑 include
       `flags.make`；在 `build/src` 下跑會 `No such file or directory`）。
   ⇒ 用法：改完程式碼先這樣驗一遍，**把「拿到 GPU 窗口才發現編不過」的風險歸零**；
     真正的 `--build` 仍然留到窗口內（見第 42 條）。

### ⚠️ 第 45 條：「重疊有沒有機會」要拆成**三個各自可為偽的命題**，別混著答（2026-09-23）
   被問「GPU/CPU 能不能完全重疊」時，三個命題的答案常常**完全不同**，混著答一定會錯：
   ① **算術上界**（把 CPU 獨佔段從步時裡減掉）—— 一定有，但它只是減法，不是方案。
      例：步時 247.98，減掉 `cb` 42.04 ⇒ 15.14 t/s；減 74.18 ⇒ 17.94 t/s。
      ⚠ 兩個儀器對同一個 `cb` 差到 1.8×（42 vs 74）本身是未閉合項 ⇒ 上界只能給**量級**。
   ② **結構窗口**（issue 與 await 之間有沒有 GPU 工作可插）—— 這才是「做不做得到」。
      實測 **0**：MoE core 永遠在 segment 第一個 command buffer（5811/6362，pre-MoE 佔比
      med=0.000），ids 由段尾的 route 產出 ⇒ CPU 拿到 ids 時 GPU 已空；而 submit 後
      第一個 buffer 立刻是 MoE core ⇒ 中間沒有任何 GPU 工作。挪 MoE 也不行（後面的 attn
      依賴它的輸出）。**交叉驗證：`gap > cb` ⇒ GPU 空轉得比 fill 本身還久 ⇒ 遮蔽量零。**
   ③ **消滅 IO**（讓重疊變成無意義）—— 常常是記憶體題不是工程題，先算再說：
      `專家全集 = n_layer × n_expert × bytes/expert`，比對 `recommendedMaxWorkingSetSize − 非專家常駐`。
      實測 10.70 GiB vs 剩 6.68 GiB ⇒ 差 4.0 GiB ⇒ 直接出局，省掉一整輪實驗。
   ⇒ **回答順序：先算 ③（最便宜、常直接結案），再查 ②（決定做不做得到），最後才報 ①（且必須
      標明「這是上界不是可達值」）。** 反過來會讓人拿 ① 當承諾。
   ⇒ 附帶：**部分重疊 ≠ 0**。若已有實測覆蓋率 q，按「miss 數線性縮放」估樂觀上界
     （`ensure_batch` 是批量 ⇒ miss 數線性影響時間，比 `f_clean`「整層零 miss」口徑寬鬆），
     再減影子成本。實測 q=0.354 ⇒ 省 6.0~10.6%，但影子 ensure 38.3 ms/步 ⇒ 淨虧。
     兩種口徑對同一個 q 會給不同結論 —— **報兩種，但都先減成本再下判決。**

### ⚠️ 第 46 條：「讓它更快」和「讓它更早」天花板可以差 20 倍 —— 先問是哪一個（2026-09-23）
   被問「X 不能加速嗎」時，**先分辨問的是 latency 還是 schedule**：
   - **更快（latency）**：天花板就是 X 在步時裡的佔比，通常一眼就能判死。
     實測：ids 相關的全部時間 = CPU 側 unwrap `pre` 4.1 µs/call（佔 `cb` 的 **0.55%**）
     ＋ GPU 側 route 4.76 ms/步（步時 1.9%）⇒ **總天花板 2.0% < 3% 門檻** ⇒ 直接結案。
   - **更早（schedule）**：天花板是「能移出臨界路徑多少」，跟 X 多快無關，可以大 20 倍。
     實測：提前一層發起 fill ⇒ 上界 **+23.5%~+31%**。
   ⇒ **判死「更快」之後不要連帶判死「更早」**，那是兩個問題。
   ⇒ **動手造新儀器前，先查倉庫裡有沒有已經把那個量切開的 env/flag。**
     本例 `CGC_HOOK_SPLIT=1` 早就存在，把 `cb` 切成 pre／ensure／drain／tail，
     一份既有 log 就回答了「ids 佔多少」，比新寫一個 probe 便宜兩個數量級。
     （查法：`grep -rn "CGC_[A-Z_]*" scripts/run_server.sh` 看 allowlist 裡有哪些開關，
     再 `grep -rn` 那個名字找 docs／memory 裡的既有實測。）
   ⇒ **「提前」的窗口要用 segment 的百分比算，不要拍脑袋。**
     MoE core 佔 segment 60.1% ⇒ 最早只能在 60% 處發起 ⇒ 窗口是**後 40%**（1.30~1.59 ms），
     而每層 fill 1.855 ms ⇒ **裝不進**，最多移出 70~86%。
     ⇒ 通則：先量「觸發點在 segment 的哪個百分位」，再乘 segment 時長，最後才跟需求量比。

### ⚠️ 第 47 條：跨 step 的「時間局部性」預測源，先確認**相鄰 step 的 token 是不是同一批位置**（2026-09-23）
   直覺做法：把上一個 decode step 的 expert ids 存起來當這一步的預測源（註解還常寫
   「相鄰 token 路由重疊 70-90%」，看起來背書了這個做法）。
   **在 MTP／speculative decode 下這個直覺是錯的**：一個 step 的 `ntok` 是 `ntok` 個
   **不同位置**的待驗證 token；下一步若全部接受，token 位置整批往後移 `ntok` 個
   ⇒ 相鄰兩個 step 的 token **完全不相交**。「上一步的 token 0」是另一個 token，
   不是同一個 token 的上一個狀態。
   實測（Qwen3.6-35B-A3B，ntok=4，40 層）：只存 token 0 那一行 ⇒ 覆蓋率 0.354；
   存「整個 step 的聯集」⇒ 才是同一量級的預測源。兩個數字差 1.7 倍以上，
   而且**錯的那個比較難看** ⇒ 會做出錯誤的「證偽」結論。
   ⇒ **判準：預測源的粒度要跟「被預測對象」一致。** fill 發的是 union（跨 token），
     預測源就該是 union 量級；拿單一 token 的 top-k 去預測 union，
     天花板就是 `k / |union|`（實測 8/19.41 = 41%），再怎麼調都過不了。
   ⇒ **檢查方法：把「候選集合的實測大小」印出來**（`w8/w16/w24`）。
     名義寬度 24 實際只有 13.5 就是一個警訊；若 `w ≈ k`（單一 token 的 top-k 大小）
     而你以為在預測 union，那就是粒度錯了。
   ⇒ 附帶：同一個 bug 讓「加寬度」看起來沒用 —— 邊際命中率 ~30% 其實是「再加的
     都是別的 token 的專家」，不是「預測不準」。

### ⚠️ 第 48 條：圖裡加**影子分支**做量測時，用「stamp」驗證排程順序，不要用運氣（2026-09-23）
   為了量「如果提前算會得到什麼」，常見做法是在計算圖裡加一條**不接回主圖**的影子分支
   （例：用 pre-attn 殘差多算一次 gate，只為了跟真實 top-k 比）。
   問題：影子分支與真實路徑是**兩條獨立的 DAG 分支**，ggml 不保證誰先算。
   若影子排在 top-k 之後，hook 讀到的是**上一輪**的值 —— 數字照樣很漂亮，是靜默錯誤。
   **解法：兩個計數器。** 影子 capture 時 `shadow_stamp[il]++`，真實 hook 時
   `hook_stamp[il]++`；兩者相等才代表「本步的影子值已在 hook 之前算完」。
   不等就**跳過並計數**，最後把 skip 數印出來 ⇒ 「順序不對」變成可觀測的，不是假設。
   （這與第 41 條同源：不要把「沒量到」摺進平均裡當成「量到很低」。）
   ⇒ 影子分支的**命名**要能被 eval callback 認出來：圖建置期的 `cb(t, name, il)`
     會把張量命名成 `"name-il"`（見 `llama_context::graph_get_cb`），
     而 `expert_cache_eval_cb` 是靠 `t->name` 分派的 ⇒ 記得 `cb(shadow, "xxx", il)`，
     光 `ggml_build_forward_expand` 加進圖並不會讓它進 callback。
   ⇒ ⚠ 影子分支會真的佔 GPU 時間 ⇒ **那一輪的 t/s 不可引用**，只取幾何／命中率。

### ⚠️ 第 49 條：在模型層檔案加圖節點前，**先解 GGUF 的 `general.architecture` 確認是哪一支**（2026-09-23）
   一個 repo 裡常有好幾個結構幾乎一樣的 arch 檔（`qwen3next.cpp`／`qwen35moe.cpp`／
   `qwen35.cpp` 都有 `attn_post_norm` + `is_recr` 的 hybrid 層迴圈）。憑「模型名字像哪個」
   去猜會猜錯 —— 實測 `Qwen3.6-35B-A3B` 的 `general.architecture` 是 **qwen35moe**，
   不是 qwen3next。
   **症狀極具誤導性：改錯檔案 ⇒ 你加的節點從來沒被建出來 ⇒ 「完全沒量到」（計數器 0 行、
   skip 100%），而不是「量到很低」。** 很容易被誤讀成「這個方向不行」。
   ⇒ 查法（不用載模型，讀 GGUF header 前幾十個 KV 就夠）：
       python3 -c "讀 magic/version/n_kv，逐個讀 key，印 general.architecture"
   ⇒ 保險起見：加完之後**先跑一次看「有沒有任何輸出行」**，再去看數字。
       「零行」和「數字很爛」要分開診斷（第 41 條）。

### ⚠️ 第 50 條：這個 repo 的 eval callback 只轉發「段尾 top-k」—— 其它節點要開 `CGC_TD_CB`（2026-09-23）
   `ggml_backend_sched_set_eval_callback` 裝的 callback，在**一般** ggml 排程器下每個節點都會
   被叫（`ask=true` 問要不要算，`ask=false` 算完通知）。但這個 repo 走的是 **CGC 分段派送器**
   （`ggml-backend.cpp` ~2490 與 ~3010）：
     - `ask=false` 只對「每段最後的 top-k 節點」觸發（`ttopk`）；
     - 其它節點**只有在 `getenv("CGC_TD_CB")` 存在時**才被逐段轉發（那段程式原本是給
       `[CGC bit-bisect v7] in-compute tensor dump` 用的）。
   ⇒ **要讓自訂節點進 eval callback，必須設 `CGC_TD_CB`。** 它是「逗號分隔的張量名過濾器」，
     dispatcher 側轉發**全部**節點、dump 側才按名比對 ⇒ 設成 `1` 就不會真的 dump 任何張量。
     代價：它會 `while (cgc_done(...) < target) sched_yield()` ⇒ **序列化 async pipeline**
     ⇒ 那一輪的 t/s 不可引用（只取幾何／命中率）。
   ⇒ 同一個節點會被轉發**多次**（實測 shadow_stamp=3 vs hook_stamp=2）⇒ 見第 48 條，
     「是否本步」的判據要用 `stamp 有沒有前進`，不能用 `兩邊相等`。

### ⚠️ 第 51 條：一個方向被儀器 bug 判死、bug 修掉之後，要做**兩件事**而不只是重算數字（2026-09-23）
   ① **往下游追**：那個「判死」有沒有被拿去當**別的結論的前提**？
      實例：prebind 的 FAIL 被寫進 MEMORY.md 變成「結案＝不做」＋「下一步改走 pread_usec
      路線」⇒ bug 修掉後，**連帶作廢的還有「下一步」**，不只是那個數字。
      更麻煩的是：**連「根因」都可能是 bug 的產物**（「union 19.41 vs 每次只能預測 8 ⇒
      天花板 41%」—— 真實預測源是整步 union，寬度 16.9~28.7，不是 8）⇒
      **根因與數字要一起作廢**，只改數字會留下一個看起來很合理、其實是從錯的資料推出的故事。
      ⇒ 查法：`grep -rln` 那個方向的名字，逐個檔案看它是不是被當成前提。
   ② **往橫向比：被判死的方向與新方向常常是「互補」而不是「替代」** —— 因為它們卡在不同的軸。
      預取／預測類的方案永遠有三個軸，單看「覆蓋率」一定會選錯：
        - **覆蓋**（預測命中多少）  ρ 0.849 > prebind 0.726
        - **抓取**（過度抓取倍數）  ρ 1.03× < prebind 1.48×
        - **視窗**（能提前多久發起） ρ 1.30~1.59 ms（卡住） vs prebind ≈ 一整步（不受限）
      ⇒ 只看前兩軸會說「ρ 完勝」；加上第三軸，**在「視窗 1.30 ms」的世界裡 prebind 反而贏**
        （16.06 vs 15.53 t/s）。
      ⇒ **判準：把三個軸都列出來，明確說「誰卡哪個軸」，再回答選哪個。**
        如果結論取決於某個未閉合的口徑（這裡是 `cb` 42 vs 74、視窗 1.30 vs 1.59），
        **下一步是量那個口徑，不是再挑一個方向。**
   ⇒ 附帶：**「成本塞得進窗口」≠「划算」**。`shadow_cost` 只算時間（w=28 → 44 ms/步，
     塞得進 129.8 ms 陰影窗），**LRU 擾動與多餘 pread 的頻寬爭用都不在裡面**。
     報成本時要明說這兩項「沒被定價」，不要讓「fits」被讀成「值得」。

### ⚠️ 第 52 條：多機制的「疊加上界」——先問它住在哪個格點，再問它多大（2026-09-23）
   續第 51 條：把三個軸列出來之後，很容易接著問「那兩個都做，疊加起來是多少？」
   **那個數字九成是假的，而且假的方式是固定的：**
   ① **把手算搬進 script + selftest**，讓它逐個重現已發表的每個數字（本次 7 個，容差 0.03 t/s）。
      這麼做之前是「相信」，之後才是「可以驗」——而且 selftest 能把主張寫成不變式。
   ② **把它拆成本身的貢獻 vs 增量**：實例 16.76 = 16.46（只做 ρ，+30.9%）**+ 0.30**（prebind 增量）。
      「疊加 ⇒ +33%」是因果倒置 —— 33% 裡有 31% 是第一個機制自己的。
      ⇒ **必問句式：Δ = 疊加 − 只做最好的那一個**（做成 selftest 不變式：本次全格點只值 +0.03~+0.30 t/s）。
   ③ **它是哪一個格點？** 上界常常是「每個軸都取樂觀值」的那個角。本次是 cb=74.18 **且**
      視窗=1.59 同時成立；換成同 run 的 cb=42.04 ⇒ 14.50（+15.4%），不是 16.76。
   ④ **跨 regime 借數字 = 本線自己禁過的動作**（`joint_reconcile.py` 要求所有分量出自同一支 log；
      `cb_headroom_probe.py` 把水位變成 entry condition）。cb 在本專案飄過 6.6×（11.26/21.23/
      42.04/74.18）⇒ **用別支 log 的 cb 去放大本支 log 的結論，是同一類錯誤。**
   ⑤ **給了它的極限值還要對上解析度**。本次：疊加增量 +0.13 t/s，而 k=3 有 launch 級 13.16%
      配對扣不掉 ⇒ 需要三位數的臂 ⇒ **「測不到」就是結論**：不要做兩個。
   ⇒ 對外只講「同 regime 紀律下的那個數」＋「結構上限」（把 cb 全藏起來 ⇒ step − cb，
      這是唯一不需要任何覆盖率假設的上界）。

### ⚠️ 第 53 條：「哪個項綁住」是 cfg 的函數——所以「下一個實驗是什麼」取決於待定讞的那個量（2026-09-23）
   **不要因為某個項在一個格點上綁住，就把它當成全局的瓶頸。** 先算它在所有格點上綁不綁：
     每層需求 F = cb / 40；綁住的條件是「提前量 < cov·F」
     cb=42.04 ⇒ F=1.051 ms < 連最悲觀的視窗估計 1.30 ⇒ **視窗在任何情形都綁不住**
     cb=74.18 ⇒ cov·F=1.584 ⇒ 視窗 <1.584 才綁（1.59→1.30 讓 ρ 從 16.46 掉到 15.53）
   ⇒ 本案：**「下一步去量視窗」只有在 cb 偏大的那一口徑成立**；在 cb=42.04 那一格，
     量視窗量到的東西不會改變任何結論。**順序是 cb 先**，而且 cb 還同時決定絕對尺度
     （結構上限在 4 個 cb Candidate 之間是 +4.8% ~ +42.7%）。
   ⇒ 具體做法：**同一支 log 同時讀這兩個**（cb 照 `cb_headroom_probe` 的水位條件；視窗百分位見第 54 條）。
   ⇒ 泛化：只要者的 so-called「主導項」是**由另一個未定讞的量推導出來的**，
     就先當它是待測的前提，不是已知的天花板。

### ⚠️ 第 54 條：要量「提前／重疊窗口」之前，先驗那條影子分支在 node array 裡的落點（2026-09-23）
   **ggml 的執行順序 = 建圖順序（node array），排程器不會幫你往前挪。**
   ⇒ 影子分支的**輸入**何時可用 ≠ 它**何時被執行**。實例（`qwen35moe.cpp`）：
     `inpSA=inpL` 在 `:187`（輸入已可用）⇒ attn `:189/:200` ⇒ residual `:209` ⇒
     `attn_post_norm` `:216` ⇒ **影子分支建在 `:231`** ⇒ 真 gate 在 `:240`。
     ⇒ 它落在 node array 的 attn **之後**、離真 gate 只幾行 ⇒ **提前量 ≈ 0**，
       而那幾行正好就是我們在付的插入成本 ⇒ **那支 probe 只能量準度，拿不到任何窗口。**
   ⇒ **不先移就去量百分位 ⇒ 量到 ~0，然後用一個「建圖順序」的理由把方向殺掉。**
     這跟第 47 條（token-0 儀器 bug）是同一類：**結論是對的，但它量到的不是它以為的東西。**
     修正本身零風險：把它移到 `:187` 之後、`:189` 之前（同一組輸入與權重、與 attn 無依賴）
     ⇒ 節點落到 layer L 最前面，才是那個機制宣稱的「上一段一結束就發起」。
     ⚠ 移了要重驗：分段轉發會不會把它放進別的 segment、stamp 新鮮度判據還成不成立。
   ⇒ **怎麼量**：樹上已有 per-command-buffer 的 GPUStartTime/GPUEndTime + node range
     （`ggml_metal_cgc_gpu_take_cb`，消費者 `CGC_GPU_NODES`）。
     預設 `n_nodes_0 = MAX(64, 0.1N)` 會把目標節點埋進 ~64 節點的 buffer ⇒ 必須
     **`CGC_CB_N_MAIN=1` + 大 `CGC_N_CB`**（一 node 一 buffer），node 的時間軸位置才可解。
     **只取 START，不要取 duration**：全 VIEW/RESHAPE/PERMUTE 的 buffer 一條 GPU 命令都沒有
     （既有記錄：報出過 592 µs/node 的假值）。
     那一趟本身是重擾動環境（一 node 一 buffer 會拉長 encode／提交），
     ⇒ **只取其序數含義，不要引用它的 t/s 或 cb**。

### ⚠️ 第 55 條：修完「探針位置」之後，要用位置 A/B 證明 TRADE 是位置給的，不是數學給的
   （承第 54 條；2026-09-23 實際落地）
   把影子分支挪前的那一刻，必須先問：**哪個結論會變、哪個不會**。
   實例（ρ probe）：**準度（cov_uni=0.854）不受位置影響** —— 它比的是兩組 logits，
   與 node 在圖裡排第幾個無關；**只有「提前量／窗口」那一維被位置影響到全部**。
   ⇒ 挪位置**不需要**撤回已發表的準度數字，但要公開標注「那一輪是 late 位置量的」。
      寫法：`docs/RHO_STAGE0_RESULT_2026-09-23.md` §8（doc 開頭放 ⚠ 聲明，原文留著作错题）。

   ⇒ **機械動作**（三件，漏一件就白做）：
     ① 把影子 block 貼到**生產者的正下方**（本例 `inpSA = inpL` 的下一句），不是貼到「盡量前面」。
     ② **把它的輸入依賴一併提前**：本例 nextn 的 `inpSA = ggml_get_rows(...)` 原本與 `cur` 那行
        寫在 attn 之後。它與 attn 無依賴 ⇒ 上移不改值，但能保證 ρ 用到**與真實路線同一個**
        （已 mask 的）tensor，且**不多一顆 node**（留在原處再算一次就是重複節點，白付一份成本）。
     ③ **舊位置留成對照臂**（本例 `CGC_RHO_PROBE_LATE=1`），不要直接刪掉。
        ⇒ 「位置 A/B」的判據：**同一支 binary、同一組權重，`ρ` 必須逐位元相同，只有 GPU
        時間戳位置不同**。若 `ρ` 變了 ⇒ 「無依賴」的前提壞了，回頭查，不要直接報 lead。

   ⇒ 組合量 eval 時 **`env "${ARR[@]}" A=1 B=1 cmd`**（bash）；zsh 要用 `${=VAR}` 或寫死 flags，
     因為 **zsh 對未加引號的參數不做 word splitting**（`$COM` 會整串變成一個參數，
     include path 全丟 ⇒ `fatal error: 'llama-model.h' file not found`）。

   ⇒ **别在不会被执行的 arch 檔案裡留副本**：（本專案 `general.architecture = qwen35moe`，
     曾在 `qwen3next.cpp` 也貼一份）留副本會在下次改主角時**靜默分歧**，而同步它的成本
     比「換 arch 時重貼一次」還高。只留一行註解指回真身。
     改錯 arch 的症狀是**「完全沒量到」（0 行、skip=100%），不是「量到很低」** ⇒ 看到零行
     先懷疑 arch，不要懷疑 tuning。

### ⚠️ 第 56 條：量「提前量 lead」的四個機械坑（2026-09-23 實測，全踩過一遍）
   ① **CGC-NSM 沒有時鐘，只有 duration** ⇒ 它答不了「時間軸上早多少」。
      要 GPUStartTime 得另開一行（`CGC_GPU_NODES_START=1` → `CGC-NSCB`，本專案已加）。
      **新開關 + 新行標，不要改既有行的格式**：`CGC-NSM` 有三個parser 在吃它
      （`gdn_split` / `per_op_slice_parse` / `attn_moe_split`），加欄位會靜默餵壞它們。
   ② **buffer 不是 1 node 寬，是 ~5 node 寬**。`CGC_N_CB=127` 會卡死 Metal 的
      command-buffer 建立（`run_server.sh:1743` 有實測），可用上限 ≈ 16 ⇒
      **只能給 lead 的區間**（悲觀 = 對方 buffer 的 START − 我方 buffer 的 END），
      區間跨 0 就必須印 INDETERMINATE，不准挑一邊報。（`rho_lead_parse.py` 已把這條寫成
      selftest 不變式。）
   ③ **要印 range 裡的全部名字，不能只印第一個**：只印第一個時 `cgc_rho_logits` 被埋在
      5-node buffer 的中間，症狀是「影子節點彷彿根本不在圖裡」（0 命中）。
   ④ **`llama_bench_matrix.py --workdir` 不會自己 mkdir**，目錄不存在時它會把整趟 llama-bench
      跑完才在寫 stderr log 時炸 `FileNotFoundError` ⇒ 白跑 N×45 s（2026-09-23 白跑 4 趟）。
      ⇒ 每個 workdir 先 `mkdir -p`。

### ⚠️ 第 57 條：「逐位元相同」在 decode 準度量上是不可能達成的判據（2026-09-23 自我更正）
   我曾把位置 A/B 的判據寫成「兩臂 `cov_uni` 必須完全相同，否則『無依賴』前提壞了」。
   實測：同臂三趟 0.8528/0.8655/0.8616（散佈 0.013），兩臂差 0.019 —— **同階**。
   原因不是數學，是**母體**：解碼軌跡（MTP 接受數）每次跑都不一樣 ⇒ ntok=4 的層數
   2000/1840/2120 vs 1840/1680/1880 ⇒ token 母體不同 ⇒ 命中率本來就會動。
   ⇒ 正確判據換成兩條：**(a) 差落在同臂散佈的噪音帶內；(b) 兩臂都在門檻同一側。**
     兩條都過就可以說「位置不影響準度」，不要再要求位元相同。
     反之若差 **遠大於** 噪音帶，那才是數學被改動的訊號 —— 這時候要回頭查輸入張量，
     不是查 tune。

### ⚠️ 第 58 條：同一個量在不同 log 差 N 倍時，先問「是不是同一支 run 的不同窗口」（2026-09-23）
   `cb` 在本專案量過 11.26／21.23／42.04／74.18 ms（6.6×），兩天裡被當成「兩個 regime 分歧」
   在爭，而它決定收益是 +14% 還是 +24~31%。**實測後答案：它們是同一支 run 的兩個窗口。**

   在交付 cell 本體加 `CGC_DECODE_PROFILE=1` 跑兩趟，同支 log 裡：
       穩態（對齊 `--warm-skip 64`）ntok=4   cb median 42.09 / 42.24
       含池預熱                               cb mean   47.84 / 49.62（rep1 全部 61.84 / 70.59）
   ⇒ **74.18 = 含池預熱／高 swap 那一格；42.04 = 穩態。而交付錨點用 `--warm-skip 64`，
     時鐘從預熱之後才開始 ⇒ 交付 regime 就是 42，74 撤回。**

   判據（不要再用「哪個 regime 比較對」這種無法偽的問法）：
   ① **把兩個候選值都放回同一支 log 的不同窗口去定位**，能定位就是窗口問題，不是 regime 問題；
   ② **哪一個窗口對應錨點的採樣區間，就用哪一個** —— 錨點的 `--warm-skip`／warmup
      約定決定了答案，不是你的偏好；
   ③ 定價用 **mean 不是 median**（Σcb 才決定吞吐）；`joint_reconcile.py` 報的是 median，
      直接拿它去定價會低估長尾（這裡 mean 50 vs median 42）。

### ⚠️ 第 59 條：讀 `CGC-DECPROF` 的三個坑（2026-09-23，`scripts/check/cb_delivery_read.py`）
   ① **prefill 圖也算一個 step row**：`-d 512` 的 prefill 是一整個 `graph_compute`，
      印成 `ntok=512, cb=5791 ms`，一筆就把 rep1 的 mean 從 61.8 拉到 134.4 ⇒ 用
      `--max-ntok` 排除。
   ② **`dp_layers` 只有兩個值：1（MTP draft 模組）與 40（verify，主模型可路由層）**。
      混著算 median 描述的是一個不存在的母體 ⇒ 只取 verify 步。
      （順帶可用來確認定價模型裡的層數常數是否正確。）
   ③ **DECPROF 的 `total` 就是真實 step**（可用 bench 的 `n_gen ÷ t/s ÷ rep` 反算交叉驗證；
      兩趟都吻合到 2% 內）⇒ `Σcb/Σtotal` 是可引用的時間加權佔比。

### ⚠️ 第 60 條：一個軸被機械排除之後，要把它從下一步清單裡刪掉（2026-09-23）
   「視窗是不是主導項」爭了一輪，最後不是靠論證結案的，是靠一條 selftest 不變式：
   **`lead` 在實測區間（1.31→3.00 ms）換任何值，`t/s` 完全不變**
   （`cov·F = 0.860 × 50.40/40 = 1.084 ms < 1.31 ms` ⇒ 視窗永遠綁不住）。
   ⇒ 把「下一個實驗去量視窗百分位」**從清單刪除**，別再為它排 GPU。
   做法：把「這個軸不綁」寫成 `max−min < 1e-9` 這種會紅的檢查，而不是寫一句話在文件裡。

### ⚠️ 第 61 條：baseline 讀不到錨點時，先看 `swap_used_mib`，不要先怪噪音（2026-09-23）
   同一支 build、同一個 cell、thermal 全程 NOMINAL，base 卻從錨點 12.57 一路掉到
   **9.47 → 4.62**。我第一次把它歸給「單臂噪音 ±27%」，第二次歸給「熱浸沒冷卻」，
   **兩次都錯**。真因是 swap：
   `swap 5301 MiB` + `anon 8.69 GiB` ⇒ 8 GiB 的 expert pool 被換出到 SSD
   ⇒ `cache hit` 從 68.7% 崩到 **57.2%**、`us/job` 13907 ⇒ t/s 腰斬再腰斬。
   **重開機後 swap=0，同一支 build 立刻回到 11.97**（±1.22，錨點 12.57）。
   判據（每一趟都要看，不是只有可疑時才看）：
   - `mem_state()["swap_used_mib"]` **> 0 就拒跑**（不是當參考值）。
   - `driver.log` 的 `cache: hit xx%` 是同一件事的另一面：
     **hit% 掉了 ⇒ 先懷疑水位，不要懷疑被測的機制**。
   - ⚠ **熱壓 NOMINAL 完全擋不住這個**（hist 192/192 NOMINAL 照樣 4.62）
     ⇒ 「gate 過了」不等於「機器是乾淨的」，thermal 與 mem/swap 是兩個獨立的軸。
   ⇒ A/B 腳本要把 mem gate 寫成 `usable_pct >= 30` **且** `swap_used_mib == 0`，
     並在拒跑時印出 swap 值（否則下次還是會被誤讀成噪音）。

### ⚠️ 第 62 條：A/B 腳本必須複用交付工具的閘門，不能自己長一份（2026-09-23）
   `prod_profile.py` 每趟前做 **mem gate + `wait_nominal(420s)`，等不到就拒跑**。
   我自己寫的 `rho_fill_ab.sh` 只有「有沒有別的行程在跑」的檢查 ⇒ 少的就是這一套，
   結果整批數據都帶著同一個系統性偏移（見第 61 條）。
   規則：**A/B 腳本 import 交付工具自己用的那兩個實作**
   （`from profile_duo import wait_nominal`、`from prefill_certifiability import mem_state`），
   不要重寫一份 polling —— 重寫的那份一定會跟交付口徑漂移，而漂移的方向永遠是「比較寬鬆」。

### ⚠️ 第 63 條：per-layer 的 `CGC-DECPROF` 行是**部分口徑**，定價一律用 step 級 `gpu_union`（2026-09-24）

`CGC_DECODE_PROFILE_ALL=1` 會吐 `CGC-DECPROF topN: L<l> wait=.. cb=.. gpu=.. union=..` 逐層行。
**不要用它們做「一層 / 一個 expert 多少 ms」的定價**：

- 實測：逐層行的 `wait` ≈ 0.13–0.18 ms，40 層 × 0.175 = 7 ms，而 step 級 `wait` 是 **64 ms**
  ⇒ 逐層行低了 ~9×。這跟 `CGC-HOOKSPLIT` 的 `ensure` vs `CGC-DECPROF` 的 `cb` 差 9.5×
  是**同一個口徑洞**（`docs/MISS_AXIS_RESULT_2026-09-24.md` §3），不是兩個獨立的謎。
- 安全的做法：用 **step 級** `CGC-GPUTIME: .. gpu_union=U ms` 除以層數
  （2026-09-24 實測 67.42 ms ÷ 40 = **1.685 ms/層**），再乘你要的拆分係數。

⇒ 「單層 1.6~2.5 ms」「單 expert 0.126~0.169 ms」這種數字，只能從 step 級 union 往下拆，
   不能從逐層行往上讀。工具：`scripts/check/miss_reprice.py`（`--self-test` 16/16，附敏感度帶）。

### ⚠️ 第 64 條：切 step 不能用「單調性」—— 零行的 step 會讓相鄰 step 合併（2026-09-24）

寫 log parser 時很自然會用「某個欄位（layer id、step id）不再遞增 = 新 step 開始」來切段。
**只要有一種 step 是一行都不印的，這個啟發式就是錯的**，而且它會製造**假紅燈**：

- 實例（`scripts/check/miss_mask_check.py` 第一版）：`BATCHDBG`／`MISSMASK` 只在
  `misses > 0` 時印一行，所以 miss=0 的 step 完全不出現 ⇒ 兩個相鄰 step 被併成一個
  （後者首個 layer id 大於前者最後一個時）。它報出 **57/103 step 的 LAYER_DIFF**，
  但逐層抽樣每一層其實都對得上 —— 全是假的。
- 正確做法：**改成逐 key（例如逐 layer）的序列比對**。把同一個 key 在所有 step 的觀測排成
  序列，兩邊各自省略「該 step 無觀測」的情形 ⇒ 判斷一致時序列自然等長且逐項相同；
  長度不同（例如一邊含 prefill step）時**從尾端對齊**。
- 通則：**任何「某 step 可能零行」的 log 都不能用單調性切 step。** 同理，
  「用 median 判 warm-skip 效應」（見第 62/63 條的口徑坑）也是同一類錯誤：
  選了一個天生把你要量的東西排掉的統計量。

附帶：`CGC_MISS_MASK=1` / `CGC_MISS_MASK_DBG=1`（2026-09-24 第 2 步新增）是「哪些 (layer, expert)
是佔位」的 device-side 出口；實測與 host `BATCHDBG` **逐位相同**（38 層 / 421 元素 / 0 差異），
成本量不出來（Δ = −0.95 ± 2.01 ms/step，`segs=41` 不變 ⇒ 無新增 CB）。
詳見 `docs/MISS_MASK_STEP2_2026-09-24.md`。

### ⚠️ 第 65 條：填池統計的口徑 —— `fill_wait_us`/`pread_usec` 都不能拿來算 decode 段吞吐（2026-09-24）

`CGC-SHAPE v=1 phase=final` 會印 `read_mib / pread_us / fill_wait_us / compulsory / capacity`。
**這三個時間欄位沒有一個能代表「decode 一步花多少時間在填池」**：

| 欄位 | 實際語義 | 為什麼不能用 |
|---|---|---|
| `fill_wait_us` | 只有兩個累加点：`llama-expert-cache.cpp:998`（prefetch 在飛的等待）與 `:1102`（**單 expert** `fill_pool_direct`） | decode 走 `ensure_batch → fill_segments_pool`，**這一條完全不累加**。實測 3.40 s 基本來自 prefill，除以 decode 步數會得到虛高的 12.4 ms/step |
| `pread_usec` | 跨 worker **累加**，可以超過 wall clock（註釋自己寫「553 s inside a 92.6 s wall」） | 實測 1500.6 s / 56 s wall = 26.8×，大於 worker 數 8 ⇒ 歸因不明 |
| `read_mib` | 總搬運量 | 沒有時間，只有它能給「每次 fill 搬多少 bytes」（實測 1674.6 MiB / 4123 = **0.406 MiB/fill**） |

⇒ 要 decode 段填池吞吐，**必須新加一個只包住 `fill_segments_pool`、按 step 累計的計時器**。

### ⚠️ 第 66 條：開工閘門不能寫成「去量一個還不存在的臂」（2026-09-24）

寫閘門時很容易寫成「先把 X 做出來，量它的 Y，再決定要不要做 Z」。**如果 X 依賴 Z，這是循環依賴**。

- 實例：閘門「量『單段提交＋背景 fill』臂的穩態 miss 率，<10% 才開工第 3 步」。
  但單段提交把 hook 整個拿掉，而 fill（`ensure_batch` 同步填 ＋ `CGC_LAYER_AHEAD_PREFETCH`
  後台預取）**全住在 hook 裡** ⇒ 那一臂不存在，要跑它就得先做出背景 fill，而那正是閘門要判的。
- 通用解法：**換成「算損益平衡點，看實測區間落在哪一側」**。把不可測的那一維用**兩個可測端點夾逼**
  （本例：零 fill 天花板 43.0% 與同步 fill 穩態 4.7%），只要整個區間都在平衡點同一側，
  判決就與未知量無關 ⇒ 閘門可判定。
- 附帶：**端點要用長序列取**，前幾步的讀數會騙人（本例 gen 8 前幾步報 62%，256 步穩態是 43.0%）；
  同時**報「平的」還是「收斂的」本身就是結論**（`decay_rel`）—— 平的代表零 fill。
- 工具：`scripts/check/miss_rate_series.py`（`--self-test` 15/15，切 5 段 + `decay_rel` + FLAT/DECAYING 判定）。

### ⚠️ 第 67 條：正確性儀器的「一次性告警」不要用單一層號當 gate（2026-09-24）

為了避免「以為開了其實沒開」，很自然會寫：

```cpp
static bool announced = false;
if (ok) { ...; if (il == 0 && !announced) { announced = true; print("ACTIVE"); } }
else if (il == 0 && !announced) { announced = true; print("NOT APPLIED"); }
```

**這在「第 0 層是特例」的系統裡必錯**，而且錯的方向最壞：**假陰性**。

- 實例：`CGC_ZEROMISS` 的告警 gate 在 `il == 0`。但第 0 層不在 GPU slot-table 路徑上
  （step 2 的逐位比對也只找到 38/40 層可比）⇒ 它**永遠**走 else 分支印 NOT APPLIED，
  並把 `announced` 設成 true ⇒ **後面 39 層即使真的生效也不會印 ACTIVE**。
  結果是「程式明明生效了，日誌卻說沒生效」，會讓人往錯誤方向查很久。
- 通則：**不要用「某一個 index」當一次性 gate；用計數。**
  ```cpp
  static int n_applied = 0, n_skipped = 0;
  ... n_applied++ / n_skipped++;
  // 累計到預期總數後印一次摘要
  if (n_applied + n_skipped >= EXPECTED) print("applied=%d skipped=%d", ...);
  ```
  並對 **skipped 的前 2 筆**印明細（含為什麼跳過的欄位），不要只印總數。
- 附帶一條：**任何「只在某層/某步生效」的機制，都先確認那一層是不是特例**。
  本 repo 已知的特例：`il == 0` 走 CPU；第 1 步是冷啟動（miss 40/40，穩態是 18.1）。

## §68 判斷「某段 CPU 時間是否在關鍵路徑上」：**用 no-op 診斷臂做差分，不要用時間對齊**（2026-09-25）

要判斷某個 CPU 段（例：expert pool 的 demand fill）到底值不值得優化，唯一可靠的判據是
**關掉它、看端到端 t/s 變多少**。不要試圖把兩個儀器的時間軸對齊：

- 時間對齊會失敗，因為兩個儀器的**口徑不同、且可能是不同 regime**。實例：
  `CGC-EBTIMER` 量到 fill ≈ 21 ms/step，而 DECPROF 的 `cb`（註釋明寫含 blocking fill）
  只有 0.3~15.6 ms ⇒ 看似矛盾。真因是那份 DECPROF 是 **MTP off** 的舊檔，而 EBTIMER 那輪
  是 **MTP on**（verify 4 token，union 19/層 vs 8/層）⇒ regime 不同，不能互證。
- **做法**：加一個 env-gated 的 no-op 開關，讓該段變成空操作，同 build、同 cell 跑配對。
  ```cpp
  static const bool cgc_eb_nofill = getenv("CGC_EB_NOFILL") != nullptr;
  ok.assign(n, cgc_eb_nofill ? 1 : 0);   // ← 假裝成功，否則呼叫端的 memset 補零會把
  if (n == 0 || cgc_eb_nofill) return;   //    你想排除的成本又加回來
  ```
  ⚠ gate 要加在**函式開頭**（不是呼叫點），才能一次覆蓋所有呼叫路徑。
  ⚠ 這種臂輸出是 garbage（資料沒讀）⇒ **只能看時間，不能看正確性**。
- 判據：差 ≥15% 才算「在關鍵路徑上」（單臂噪音底就 ±27%）。
- 同一支開關順便當**儀器自檢**：nofill 臂的 `CGC-EBTIMER` 應該掉到 ~0，
  沒掉就代表你 gate 錯了地方（還有別條 fill 路徑）。

## §69 reps>1 的 log：冷啟動在**每個 rep 的開頭**，不在整份 log 的開頭（2026-09-25）

`llama-bench --reps 3` 會**每輪重新載入 13 GB 模型** ⇒ 每個 rep 都有自己的冷啟動段。
所以「看最後 N 行」和「看最後 25%」**都不安全**：末尾會跨進最後一個 rep 的冷啟動。

- 實例：madvise 對照臂「最後 40 行」p50 = 88.9 ms，看起來是基線 21.4 ms 的 4 倍，
  一度像「madvise 讓 fill 爆炸」。分段看之後，兩組在每段都在噪音內一致。
- 做法：**切成 4 段，取各段中位數，再取這些中位數的中位數**（`segment_profile` + median）。
  這樣一個 rep 的冷啟動（佔 1/3...1/4）會被其他段投票壓過去。
  `scripts/check/fill_onpath_ab.py --self-test` 有 4 個針對這個的測項（含中段冷啟動）。
- 相關：`--warm-skip 64` 只消掉**第一個** rep 的長尾，不消後面 rep 的。

## §70 no-op 臂的**快取統計是反向的**，不要拿它讀替換策略（2026-09-25）

`CGC_EB_NOFILL=1` 這種「假裝成功」的 no-op 臂，會讓 `ok.assign(n,1)` 把**沒有真正填充**
的 slot 也標成 resident ⇒ 後續 lookup 全部命中。實測：

| | hit% | misses | reads |
|---|---|---|---|
| fill（真） | 94.0 | 9769 | 43128 |
| nofill（no-op） | **96.9**（↑） | **5845**（↓） | **13962**（↓） |

看起來像「關掉 fill 之後快取變好了」，實際上**正好相反**——池裡根本沒有 bytes。

⇒ **no-op 臂只准看 t/s 與你自己那條經過自檢的計時器（`CGC-EBTIMER`）**，
`cache: hit / misses / reads / cap%` 那一行的任何差別都是開關副作用，**不是結論**。

同一輪的另一個反向指標：`wired` 在 nofill 臂反而更高（6532 MiB vs 1740 MiB），
因為沒有 pread 把內容真正 touch 到 ⇒ 也別拿 `wired`/`swap` 去比較這兩臂。
