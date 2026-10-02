# MEMORY_FACTS — 長期事實／陷阱／周邊介面

> **這是快照，不是權威副本。**
> 權威位置：`.workbuddy/memory/MEMORY_FACTS.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 索引與漂移檢查見 `agent_harness/engine_loop/memory/INDEX.jsonl`。

> **這是 `MEMORY.md` 的主題分檔（2026-09-17 拆分），不是歷史存檔。** 動 build／載入／預算／`-ub`／
> mmap，或要碰 `agent_harness/` 之前讀本檔。這裡每一條都是「踩過一次就不該再踩」的。

## 長期事實（踩過就不該再踩）

- **`MTP_SUPPORT` 是編譯期開關且必須定義**（macOS 沒預設，靠 `scripts/build_fork_llama.sh`）——少了它被
  編掉的是**修正**（`qwen35moe.cpp:737` ＋ 四處 `[CGC MTP fix]`）。
- **SONAME 內嵌 commit 數** ⇒ 全量重建換檔名、舊檔變孤兒；載入集要從 `otool -L` 反推再 realpath，**不要
  寫死檔名**；`strings -a` 要一起印**不受 guard 保護的對照字串**。
- **錯誤路徑不得「先釋放、後記錄」**（`prefill250` req2 SIGSEGV 根因）。判準：**指令序本身就是證據** ⇒
  要保留當時的二進位（`Backup/pre_mtp_rebuild_20260916/`）；崩潰報告的 `imageOffset` 可反組譯。
- **`-ub` 是 req2 OOM 的旋鈕**：`4096` 全過、`6144` 的 req2 以 status 5 OOM 死（代價 −22%）。**機制沒修
  只有繞道。** 存活表：`4096 5/5`、`5120 3/3`、`5632 4/4`、`6144 0/5`、`6144＋池 6 GiB 3/3` ⇒ **邊界是
  (pool + compute) 的和**。出廠預設 5632。
  ⚠ **形狀不是我們能挑的**（2026-09-24 operator 糾正）：cell 的 `-b/-ub`／ctx／budget／`-p -n -d -r`
  一律照 `run_server.sh CGC_DUMP_ENV=1` 的解析值（`CGCENV BATCH/UBATCH/CTX/BUDGET/NGL/LOAD_MODE`）。
  曾有一次 `rc=-6` 被我歸因成「5632＋8GiB pool 衝突」而把 `-ub` 私自 **5632→512**，但存活表就寫著
  `5632 4/4` ⇒ 那是**未驗證的歸因**，而且它把 cell 改成一個沒人記錄過的形狀。**一次 OOM 只能另外
  查來源（他線佔 GPU／swap 髒），不能反過來改 cell。**
- **P0（`CGC_EXPERT_SKIP_READRAW`）與 P1/P2（`CGC_POOL_MADVISE`）的語意**：這族開關原為「存在即開」，
  P0.g 才改成取值判定且 **P0.g 尚未 build 進現有 binary** ⇒ 一律寫 **`=1`**（兩種語意下都是 ON）；
  要 OFF 就用 **absent**（`=0` 在 presence 語意下反而是 ON）。
- **`--load-mode mmap` 與 expert cache 不相容**（L4 pool 是 read-only file-backed 映射 ⇒ zeroing 寫唯讀頁
  ⇒ `SIGBUS` 在 `__bzero`）；缺陷已修但**任何量過的寬度仍是 0/5** ⇒ **不是 OOM 的槓桿，不要再試**。
- **`-ngl` 一律被顯式帶上 ⇒ `-fit` 永遠是 no-op**（`common/fit.cpp:377-379`）；`CGC_SERVER_FIT=1` 才真跑。
  **argv 是位置比對**（D5 config stamp）⇒ 不可重排。
- **`-expert-cache 8192` 是上限不是實配**（實配 ≈2.5 GiB）；啟動預算 13030＋8192 = 21222 vs 16384 MiB ⇒
  **OVERSUBSCRIBED 4838 MiB**（`[防護 2d]` 會印；`CGC_SERVER_STRICT_BUDGET=1` 超額 exit 1）。
- **`CGC_METAL_LIB` 不是載入覆蓋**（只做 `_Pool` 閘門偵測）⇒ metal 庫 A/B 只能**互換檔案**。
- **同一檔案的多個 Edit 不能並行送**（後寫覆蓋先寫，兩筆都回 success）⇒ 序列化或合併成一筆。
- `Backup/` 在 `.gitignore:396`（要 `git add -f`）；`check 8` 只看已 staged 檔。
  **★ 精確措辭（09-17 實測）**：`Backup/` 底下**已被追蹤**的檔案在 `git add`（不帶 `-f`）時**一樣會被拒**
  ——錯誤是 `The following paths are ignored ...: Backup`，而且**它會讓整條 `git add` 一起失敗**（其他檔案
  也不會進 staged）。所以 stage 要分兩段：`git add -f <Backup/...>` 先，其餘再一般 add。
- **★ 每次 commit 都要聲明它對 gate 鏈的立場**（使用者 2026-09-19 22:17 的規則）。
  訊息末尾一個 `Gates:` 段：`met` 或 `not-met (improve xxx)`；與 gate 無關的寫 `none (<理由>)`。
  預演（不必 commit 就能驗）：`python3 scripts/check/commit_gates.py --last`。
  gate id 來自 `agent_harness/portal/targets.json`（目標的單一真相來源，目前 G0–G7），
  所以**新增 gate 只改那個檔**，檢查器會自動跟上。檢查器自測 `--selftest`（12 項）；
  掛 hook 用 `--install-hook`（裝到 **common dir** ⇒ 本 repo 所有 worktree 生效，由人決定何時裝）。
  **沉默是錯誤** —— 沒有這段，`git log` 分不出「這輪推進了計畫」與「這輪什麼都沒動」。
- **★ 改了 `src/` 就必須先建置才能 commit**：`check_build_tracked.sh` 的 check 8 要求
  **產物比 staged 原始碼新** ⇒ 建置前 commit 一定紅。而 `run_server.sh`／`decode_sweep.py`／
  `Backup/run_spac_cold_ab.sh` **都不含 cmake/--build** ⇒ 排程量測用的是**舊 binary**，
  不會替你（也不會害你）重建。
- **★ 不要與排程量測搶機器**：D5 的 `m123_oracle_gate.py` 會**自己起 server**（要 8080 空 ＋ 獨占 GPU），
  而 prefill 的 COLD 交錯 A/B 需要「發射時 thermal=0」。⇒ 機器被排程佔用的時段**不 commit**（連
  「純 doc 的 commit」也不該，因為本 repo 把「省下 D5 再寫一段解釋」判為錯的做法）。
  未提交的改動先備份：`Backup/r12_wip/`（`tracked_changes.patch`，`git apply --check --reverse` 可驗）。
- **D5 的三個指標不是同一件事**（定義在 `scripts/check/cgc_logits_oracle_compare.py` 的 `METRIC_DEFS`，
  逐字可引）：**M1 `numeric_identity`＝逐位元相同**（*same `row_fnv1a64` for every step (= bit-identical
  logits)*）；**M2 `decision_agreement`＝argmax 相同**；**M3 `topk_set_agreement`＝top-N id 集合相同**。
  三者是**階梯** M1 ⊂ M2 ⊂ M3（逐位元相同 ⇒ 後兩者必然相同），階梯的用途是**部分分數可讀**。
  ⇒ **「M1/M2/M3 都是 bit identical」是錯的述句**：只有 M1 是；M2/M3 較粗。
  **`n_compared` 的單位是「探針點」不是 prompt**（現行參考檔 9 點 ＝ 6 `DEF` ＋ 3 `MTP`、step 0–5、
  一條確定性 prompt、答案 `42`）。**兩個前提**：① `comparable=False` 時**不得讀** M1/M2/M3
  （`.cap` 的 resolved env 指紋不符 ⇒ 會有「三個都 9/9 但 `ok=False`」的不可比 9/9，如 09-17 的
  `r52/r53`）；② D5 是**不變性／確定性**閘門，**不證明模型對不對**（v6 參考是修正後 build dump 的；
  修 r33 nb-aware 之前同一個閘門對 v5 只給 5/9，而錯的是參考）。
- **★ `scripts/check/harness.py` 是唯一的窗口前門**（09-20，`eb5426e30`）：`show`（視窗看板）／
  `run <tool> [harness 選項] -- [工具參數]`／`list`／`audit`／`selftest`。**任何會起 server 的工具都該
  從它跑** —— `run` 會在發射前問共享探針並閘一次，不必改那個工具本身。它**不取代**
  `decode_window_harness`／`m123_gate_window`／`plain_match_window`，只是收斂「等盒子」這件事
  （那四套實作彼此不一致，而擋不擋得到原本取決於你選到哪一個）。
  `launches_server` 由 `server_window.audit()` 合併、**不手寫** ⇒ 新 launcher 自動進看板。
- **新增會起 server 的腳本時，必須一併接 `server_window.require_first()`**，否則
  `window_gate.py check` 會紅（規則：未受閘 launcher 只減不增）。**踩過一次**：`cb_headroom_probe.py`
  （09-20 `6bc5e24b1`）讓未受閘 4→5；修法是**上閘不是 re-baseline**。用 `require_first` 不是
  `require` —— `--runs N` 第 2 臂起跑時第 1 臂頁快取還在，逐次上閘會拒掉正確執行的 run。
- **「安靜」有兩套定義，而且會分歧**：harness 用 `server_window.NEED_MB`（8000 MB reclaimable），
  launcher 自己用 free% ≥ 40% ＋ others。實測同一盒子 harness REFUSED 而 launcher admits。
  ⇒ **分歧要印出來（看板標 `DISAGREE`），不要取平均**；要用較寬的那把尺就明確傳 `--need-mb`，
  因為紀錄會寫下用的是哪一把。

## agent_harness（另一條線；本線只讀）

兩迴圈 `tb_loop/`／`engine_loop/`；`CONVENTIONS.md` = 判準憲章（A 可引用／B 診斷／C 讀原始碼／D 流程／
E 自我約束），同時是 `sft_pi/` 的 system prompt ⇒ 改它要走 §6.3 閉環對照。`traces/`：
`episodes/decisions/lessons.jsonl`；`engine_loop/memory/` **只放索引**，原檔在 `.workbuddy/memory/`。

- **D6**：`agent_harness/{memory,skills}/` 是**非權威 dated 快照**（只為跨機器搬運，因為
  `auto_git_push.ps1` 推送的是內容而不是指標）⇒ 要一起重生得**先**
  `scripts/import_harness_snapshot.py`、**後**索引；**兩個 `--check` 都不驗快照**。
- 快照是**手動**的，`SNAPSHOT.jsonl` 的 `source_sha256`／`source_bytes`／`source_mtime` 是它「對應原檔
  哪一版」的唯一依據 ⇒ **看到它落後是預期狀態，不是故障**（它只在有人跑那支腳本時更新）。
- **★ `auto_git_push.ps1` 在這台機器上不會跑（2026-09-17 實查）**：它是 **PowerShell**，硬編碼
  `$RepoDir = "D:\alex\flashkv0516\cgcengine_full"`、remote `cgc0907`、branch `fusionroutemot`
  ⇒ 那是**另一台 Windows 機**上的備份工作。本機沒有 `pwsh`／`powershell`、crontab 與 LaunchAgents
  都沒有相關條目，而且 `agent_harness/scripts/logs/`（腳本第一次跑就會建）**不存在** ⇒ 從未在此執行。
  **⇒ 「D6 說 `auto_git_push.ps1` 會週期性推送」≠「現在確實在推」**：GitHub 上 `fusionroutemot` 的
  最後一筆是 `1f3b0a78d "auto(agent_harness): periodic backup 2026-09-16 14:04"`（正是該腳本的訊息格式
  ⇒ **它至少成功跑過一次**），之後沒有；而本線的工作在**另一條分支** `demo/sweet-spot-windows-fix`。
  ⇒ **記憶要真的上 GitHub，得先有人在這台機器上刷新快照＋commit＋push**（快照與索引本身不會自己上傳）。
- **不要拆成獨立 git repo**：它刻意不自足（`index_assets.py` 是 `REPO=HERE/../..`、
  `build_memory_index.py` 是 `HERE/../../..`、`emit_episodes.py` 用 `find_repo()` 往上找 `.git`）。

## 記憶機制（09-17 拆分後）

- `.workbuddy/memory/` 現在是 **1 索引（`MEMORY.md`）＋ 3 主題檔（`_PERF`／`_S1`／`_FACTS`）＋每日
  append-only 日誌**。三支工具全部用 **glob** 而不是白名單（`build_memory_index.py:66` 的 `*.md`、
  `index_assets.py:352` 的 `.workbuddy/memory/*.md`、`import_harness_snapshot.py:72`）⇒ **新增／刪除
  任何 `.md` 都會讓索引與 manifest 漂移，必須重生（順序見 `MEMORY.md` 的入口）**。
- **★ `.workbuddy/` 整個在 `.gitignore:41` 被忽略，`git ls-files .workbuddy` 是空的** ⇒ **記憶本體
  不在版控內**（`git status` 看不到它，`git add` 也不會收它）。要讓記憶跨機器、或進 `auto_git_push.ps1`
  的推送線，**唯一的路是 `agent_harness/memory/` 的實體快照**（D6 修訂）。⇒ 動完記憶後，若那個推送線
  需要這批內容，得跑 `python3 agent_harness/scripts/import_harness_snapshot.py`（它會把**當日日誌全文**
  與 4 個 skill 一起快照，所以是一個要有人決定的動作，不是自動的）。
- `index_assets.py` 對記憶檔只驗**存在**（`.workbuddy/memory/` 在 `VOLATILE_PREFIXES` 內，每日日誌當天
  必變 ⇒ 驗 bytes/mtime 會是永遠紅的閘門）；`build_memory_index.py --check` 則會**重新推導並在 drift 時
  exit 1** ⇒ 記憶有動就要重生，且**不要**靠「`--check` 紅是預期」當藉口跳過。
- **重生順序固定、而且「每一次」都要按序**：先 `build_memory_index.py`、後 `index_assets.py`。
  `build_memory_index.py` 每次都會重寫 `INDEX.jsonl`（bytes 可能相同、只有 mtime 動）⇒ 若 `index_assets`
  報漂移**且差異只在 mtime**，那就是「順序寫反了」，重跑一次兩支即可，不要去找內容差異。
- **★ `docs/` 在 `CURATED` 是「逐條列」而不是 glob**（`index_assets.py:266+` 一條一條寫死，
  形狀是 `("docs/<檔名>", role, loop, replayable, produces_record, note)`）⇒
  **新增 `docs/*.md` 與 `docs/*.html` 都不會漂移**（09-17 實測兩次）。**修正**一個已在 `CURATED`
  裡的 docs 條目（例如改某份白皮書的 note）才會紅。省下一輪「我加了檔案，先重跑索引吧」的無用重生。
  **判準（兩步都做）**：`grep -c "<檔名>" agent_harness/engine_loop/MANIFEST.jsonl` 命中 0
  ＋ `index_assets.py --check` 仍印 `OK: N assets`。反過來說：**要讓一份新 docs 被索引收錄，
  必須手動加進 `CURATED`**（像 `scripts/check/*` 的新腳本要加 `role`＋`note` 一樣）。

- **★ `src/llama.cpp/build/bin/*.dylib` 與 `llama-server` 是「受版控」且「跨 session 共用」的資源**
  ⇒ **建置＝對機器上所有正在跑的實驗的一次寫入**，不是本機動作。11:29 踩過一次：`lsof` 同一行印出了
  別條線的 server 在聽 8080，build 還是跑了、蓋掉他們正在 map 的 `libggml-metal`／`libllama`，
  而他們那輪 A/B 因此橫跨兩個 build。**閘門要同時看 (a) 8080 的 listener 與 (b) 別條線的量測行程
  （`run_ids_dst_capture.sh`／`decode_sweep.py`），而且必須真的 abort —— 只印出來不算。**
  （lesson `eng-mh-0048`）

- **★ `run_server.sh` 的 preflight 會主動 SIGTERM 掉「別條線正在跑的 server」**
  ⇒ 在 8080 上的佔用**沒有互斥**，而且不只是「搶不到」，是**對方會把你的 server 殺掉**。
  機制：`:638-740` `[防護 1] 清殘留`，起自己的 server 之前先把所有殘留 `llama-server`
  送 `-TERM`（目的是防「行程疊加」造成的 `GPU OOM ret=-3` 與 kernel panic）；
  可用 `CGC_PREFLIGHT_KILL=0` 關掉，但那是例外路徑。
  **後果：「起跑前檢查港埠」只保護起跑那一瞬間，保護不了長跑。** 14:01 實例：13:56:54 過閘門
  （當下 8080 確實空）起了一個 48 題的閘門臂，14:01:53 被別條線的 `r37_pool4` 起跑 preflight
  SIGTERM 掉（死在 longform-zh #6，只跑到 14/48），而工具事後才把它標成 `invalid`。
  ⇒ **要跑 20–30 分鐘的協定，需要的是互斥**（雙方都認的 lockfile／請對方設 `CGC_PREFLIGHT_KILL=0`），
  或切成 `--profiles`／`--max-per-profile` 的受控片段；單次 pre-launch 檢查不夠。
  （lesson `eng-mh-0052`）

## 量測工具的口徑邊界（2026-09-21 補）

- **`llama-bench` 沒有 `-np` / `--parallel`** ⇒ 併發序列軸測不到（既有結論）。
- **`llama-batched-bench` 有 `-np, --parallel N`**（`-npl` 是 parallel prompts，別混）。
  但**沒有 `--spec-type`** ⇒ 測不到 MTP 交付形狀；且池的 `CGC_*` env 由 `run_server.sh`
  allowlist 注入 server，`batched-bench` 路徑**未驗證**同 regime。
- ⇒ 今天要測 `-np` 只能走 server＋HTTP（`Backup/phase_decomp/parallel_decode_ab.py`），
  而 **HTTP 不是交付口徑**（同 build：llama-bench 11.0–11.3 vs HTTP 13.78）。

## ★★★ `LLAMA_EXPERT_CACHE_ALLOW_NGL=1` —— expert cache 的總門（2026-09-24 定讞）

**沒有這條 env，整個 expert cache（L4 Metal 池 + hook + gather）不會啟用，跟
`-expert-cache <bytes>` 給多少無關。** 這是 `-ngl 99` 路徑上所有本地 runner 的隱形前置條件。

- 門在兩處（都是 `getenv` 非空才算開，presence-gated）：
  - `llama-model-loader.cpp:1113` `compute_l4_pool_capacity()`：
    `if ((no_gather && no_gather[0]) || !getenv("LLAMA_EXPERT_CACHE_ALLOW_NGL")) return;`
    ⇒ 直接 `expert_cache_pool_capacity = 0`
  - `llama.cpp:402` `l4_path = n_gpu_layers > 0 && getenv("LLAMA_EXPERT_CACHE_ALLOW_NGL") && ...`
- 症狀（缺它）：`CGC-SHAPE ... pool_cap_slots=0 slots_layer=147`、`PHASE-SPLIT ... routable=0 slots`、
  `prefill slab NOT armed` ⇒ 13.0 GB 模型整包上 Metal（`recommendedMaxWorkingSetSize 11453 MB`）
  ⇒ prefill 收尾 `ggml_metal_synchronize` 撞
  `kIOGPUCommandBufferCallbackErrorOutOfMemory`，`rc=-6`（SIGABRT）。
- 正確值（帶它）：`pool_cap_slots=143` / `routable=143` / `L4 metal pool: 143 slots/layer`
  / `Soft Pool init: L0=0 L1=0 (n_slots=143, partition disabled)`
  —— **對上 514 份歷史 log 的主流值**；`0` 在整個 `Backup/` 只出現 2 次（就是踩坑那兩趟）。
- ⚠ **全 repo 只有 `deploy-harmonyos/*` 會 export 它**。`scripts/check/` 下沒任何 runner 自帶
  ⇒ 已補進 `miss_axis.py`（`REQUIRED_ENV`）與 `seg_batch_abba.py`，**新增 runner 要照抄**。
- ⚠ 開了它之後 `CGC_PREFILL_STREAM=1` 才有意義：clamp 只在 `cgc_prefill_stream` 為真時解除，
  否則 `n_batch` 會被夾到 `cgc_pool_max_tokens()`(=8) ⇒ `GGML_ASSERT(n_tokens_all <= cparams.n_batch)`
  （`llama_bench_matrix.py` 檔頭 20-40 行有這整段說明）。

### ★★ 16GB 上 Metal OOM 的第一順位成因：`--ctx-size 8192`（2026-09-24 四變體隔離定讞）

```
A) 最小 env，無 --ctx-size                 存活，6 steps
B) 最小 env + --ctx-size 8192              OOM rc=-6，1 step   ← 只差這一個 flag
C) harness 全 env（無 ctx-size）           存活
D) harness 全 env 但 CGC_DBUF=0            存活
```

⇒ **不是** env、`CGC_DBUF`、儀器、`-p`、`-ub`。標準測試卡 §2 的指令沒有 `--ctx-size`、§7 寫
`--ctx-size 0`；**任何 runner 都不要自己加 `--ctx-size 8192`**（`miss_axis.py` 預設已改 0）。
`--ctx-size 8192` 會讓 llama-bench 配置 8192-token KV/compute buffer，把 Metal 推過
`recommendedMaxWorkingSetSize 11453 MB`。

⚠ 之前三則「隔離」結論都是在 `pool_cap_slots=0` 下做出來的，**全部無效**：
「去掉 `CGC_GPU_TIMING` 仍 OOM」「`--ctx-size` 8192 與 0 都 OOM」「`-ub` 2048 也 OOM」。
（`-p 2048 -n 128 -d 512 -r 3` 在去掉 `--ctx-size` 後是跑得完的，見
`docs/MISS_AXIS_RESULT_2026-09-24.md`。）
⚠ 另一個會讓 OOM 看起來像「跑不起來」的坑：`llama_bench_matrix.py` 用 `capture_output=True`
吃掉子行程 stderr，只寫進 `{workdir}/llama_bench_{tag}_p{p}_n{n}_d{d}_r{r}.stderr.log`；
要 parse `CGC-DECPROF`/`CGC-HOOK*` 必須讀**那一份**，不是父行程的 stderr。

## ★ 提交閘門是「防退化」閘門，不是「有進展」閘門（2026-09-26 operator 質問後定案）

**D5（`m123_oracle_gate`）驗的是「數字沒被我弄壞」（M1 數值同一性／M2 決策一致／M3 top-k 集合），
它對「速度有沒有變快」一句話都不驗。** 所以「D5 過了／FAIL 屬既有」只授權我「可以提交」，
**完全不構成「這一輪有推進」的證據**。

2026-09-26 的實例：連提 `8afb56af4`（修首啟動 abort ＋ 報 G2=100%）與 `828f4d1c2`
（修 leaf-blind 成員測試，把 100% 翻成 42.93%）—— **兩筆都真的修了壞東西，但 decode 一格
都沒動**（交付仍是 12.57）。operator 09:34「搞半天你沒有任何進展」、09:35「那你憑啥一直
commit」——**兩句都成立**，第二句的答案就是上面那條區分我沒講清楚。

由此定出兩條回報紀律（下次開始照做）：
1. **回報時把「修儀器」與「推進目標」分成兩欄**，不要用前者充數；後者為空就寫「本輪速度
   無增益」。
2. **儀器沒被證明餵到值之前，不要把它的讀數當結論寫進 dated 文件並 commit**
   （`8afb56af4` 把 G2=100% 當結論寫進 §9.2，下一輪就被 §9.8 推翻）。先加 provenance 自證
   印、證明儀器有輸入，再寫結論；否則結論要標 provisional。

## ★ 「最近 commit 的 prefill/decode 是多少」怎麼查（2026-09-26）

**權威出處＝ commit message 自己**，慣例（`2457124bf`／`2eb84420d`／`efba7c1d5`／`d242af39e` 都遵守）：

```
…（prefill=236.46t/s decode=12.17t/s(mtp=off) (commit_bench prod-new p2048 n128 d512 r3)）
```

一條 grep 就撈到整條 cell 的歷史：
```sh
git log -40 --format='%h %ad %s' --date=format:'%m-%d %H:%M' \
  | grep -oE "^[0-9a-f]+ .*decode=[0-9]+\.[0-9]+t/s[^ ]*"
```
★ 這比掃產物可靠得多 —— `Backup/**/bench.json` 只有 12 臂，真正的家族是
`Backup/**/llama_bench_*.json`（**341 臂**），而 `Backup/` 整個被 gitignore（無版控安全網）。

**產出那些數字的工具＝ `scripts/check/commit_bench.py`（pre-commit 生產 cell）。**
固定 `prod-new`＋`-p 2048`＋`-n 128`＋`-d 512`＋`reps=3`＋`--warm-skip 64`（測試卡 §2 權威口徑）、
**MTP off**；判定 `prefill >= --prefill-min(120)`、`decode >= --decode-min(10)`，未達 **rc=1（擋 commit）**，
`--record-only` 只記錄不擋。**它與 `harness.py bench --arm prod-new` 是同一個 cell**；
差別＝ harness 版多了散熱閘門（`_box_gate`，會等到 NOMINAL）＋ `attribution`／`sys_before/after` 注入。

⛔ **這條 cell 自己的歷史散佈是 6.59 ~ 13.25 t/s（2.0×）** ⇒ 引用任何單點之前先看散佈，
別把某一次的 12.17 當「應該值」。

## ★★ 三條 cell 的「撞名」：交付 cell ≠ commit_bench cell（2026-09-26 驗）

**「12.57」和「12.17」不是同一條 cell，引用前必須指名。** 三支都叫「prod-new 家族」，形狀不同：

| cell | 入口 | spec | ctx | warm-skip | 已記錄值 |
|---|---|---|---|---|---|
| **交付 cell** | `scripts/check/prod_profile.py`（`--spec-type draft-mtp`，`:103`/`:113`） | **MTP on** | **4096** | 64 | **12.57 t/s** |
| **commit_bench cell** | `scripts/check/commit_bench.py`（PROFILE=prod-new） | **MTP off** | **0** | 64 | 12.17 t/s（`2457124bf`） |
| harness 標準臂 | `harness.py bench --arm prod-new` | MTP off | 0 | 64 | 同 commit_bench（同 cell） |

★ `prod_profile.py:9` 自己寫著「…cell, which carries no `--spec-type` and no `--warm-skip`,
i.e. **MTP off and a cold clock**」⇒ 舊 cell 與現行交付 cell 的差別被明寫在註解裡。
⇒ **G5 的「vs 12.57」自動意味著 `--ctx-size 4096 --spec-type draft-mtp --warm-skip 64`。**
⚠ 而 **MTP on 正是今天在這台盒上 OOM 三次的配置**（`rhoA1`／`rhoB2`：Metal
`Insufficient Memory`）⇒ **G5 目前跑不起來，它依賴記憶體側的工作先落地。**
