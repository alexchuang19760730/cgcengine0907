# 量測衛生／環境坑／入口（09-21 從 `MEMORY.md` 移出）

> **這是快照，不是權威副本。**
> 權威位置：`.workbuddy/memory/MEMORY_HYGIENE.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 索引與漂移檢查見 `agent_harness/engine_loop/memory/INDEX.jsonl`。

動量測、起 server、要引用環境坑或入口指令之前讀本檔。本檔與 `MEMORY.md` 是同一份長期記憶。

## ★★ 並行 session 安全（2026-09-18 血的教訓，動手前必讀）

同一台 Mac 上有多條線同時量測。**任何用「binary 名字」當範圍的清理，都是在殺別人的受試對象。**

- **`run_server.sh` 的 preflight 曾是全機 cross-kill**（`'pattern + pgrep -f'` 不看 port、不看 session，
  且**排在 memory guard 之前** ⇒ 自己被 guard 擋下、別人卻已經死了）。09-18 已改：
  **`CGC_PREFLIGHT_KILL=1`（預設）只列出 pid+etime、不送任何訊號**；要清場得明確說
  `CGC_PREFLIGHT_KILL=all`。第二欄 etime 用來分辨「自己的殘留」vs「別人剛發的量測」。
- **`stop()` 不得用 `pkill -9 -f llama-server`**（`http_duo.py` 就是這麼踩的）：清理按**自己的 pid／port**。
- **「查 env」不能有副作用**：`CGC_DUMP_ENV=1` 會跳過 preflight 與所有閘門（STALE 硬檢查、memory guard）
  ⇒ 別人在跑時仍取得到解析結果。`llama_bench_matrix.resolve()`／`prod_matrix`／`m123_oracle_gate`／
  `phase_split_ab` 都靠它，**這是唯一的真相來源，不要在工具裡重打一份 env**。
- **工具自選 port**：`http_duo.py --port auto` ＋ `start_new_session=True` ⇒ 兩條線各跑一台 server 不互害。
- **★ 跨 worktree 比 binary 用 `scripts/check/server_path_ab.py`**（09-22 新增）：兩支 `run_server.sh`
  各起 server（ABBA、臂間冷卻、逐臂記 gate/thermal/swap）＋ p2 那份位元組相同的 `decode_bench.py`。
  判準一律是**兩臂比值**，絕對值不是交付口徑（server 路徑＋短 context＋熱池）。
  起跑前先用 `CGC_DUMP_ENV=1` 比對兩臂配置（0 風險），**配置相同才值得跑**。
- **⚠ `mem_gate` 的 swap 門檻是固定 MiB，但本機 swap total 會變**（觀測到 5120／7168／8192）
  ⇒ total 變小就永遠拒、變大就永遠放行。**應改成比例門檻**（待修）。
- **★ server 路徑跑一次會把機器打爛**（09-22 實測）：usable 6.44 → **0.70 GiB**、
  swap 67% → **83%**（macOS 還自己加 swap 檔）。判據：**prefill t/s 掉到個位數就表示在分頁**
  （40 token 過 13 GB ⇒ 有效 1.9 GB/s，健康值 ~14.8 GB/s），這種樣本整批作廢，不要只把 decode 拿去比。
- **server 死於 SIGTERM 要當無效樣本**：日誌 `[CGC] Received SIGTERM` ＝ 外部獵殺（watchdog 走 `GGML_ABORT`、
  OOM 是另一回事）。`http_duo.py` 每 rep 重查存活＋掃該標記，中了就印診斷、**exit 1**。

## ★ 起跑前流程：關掉吃記憶體的 app ＋ purge（2026-09-22 新增，§EN-421）

**門禁＝ `window_sentinel.mem_gate()`**（`decprof_overhead_ab`／`gpu_ceiling_measure`／
`workers_ab_delivery` 的 `gate()` 都已接）。每次起跑前照這個順序做：

1. **先關掉門禁點名的 app**（它會列 `>= 1 GiB` 的 pid／名字）。本機最大戶常是瀏覽器／別的 IDE／
   別條線的 agent app。**WorkBuddy Helper(Renderer) 2.7–2.8 GiB 是我們自己**，別關。
2. **`sudo purge`** —— ⚠ **需要 root，agent 沙箱做不到**：`sudo -n` 得到 "a password is required"、
   純 `sudo` 得到 "no tty present"（工具殼沒有 TTY）⇒ 由 **operator 手動敲**，或用
   `python3 scripts/check/window_sentinel.py --purge`（osascript 彈 macOS 授權對話框，唯一可互動的路）。
3. **重跑門禁**，OK 才起跑。

- **門禁判準**：`usable_gib < 8.0`（free+inactive+speculative）／`swap_used > 6144 MiB`
  （8 GB 的 0.75）／單一 app `>= 4 GiB` ⇒ 拒絕（exit 2）。可用 `--swap-max-mib`／`--usable-min-gib` 覆寫。
- **⚠ `purge` 不減 swap_used**（實測：free 0.07→7.3 GB，swap 6490–6770 MiB 不動）
  ⇒ **swap_used 是黏的**：只有關掉持有它的 app 或重開機才降。**門禁不能寫「swap==0」。**
- **為什麼要這一道**：09-22 交付 cell 從封版 12.57 掉到 8.36／8.59 t/s，而 thermal=NOMINAL、
  沒有別人在跑、`usable_pct>=30%` 三道舊門禁**全過** ⇒ 真因是 swap 83% ＋ usable 只剩 1.26 GB。

## ★★ 實驗臂必須是「生產 cell 的增量」——不許自己拼形狀（2026-09-25 **operator 裁定**）

> 使用者原話：**「你加的 arm 必須基於生產腳本來加，我的生產腳本起步就是 11 tok/s」**

**權威來源**：`python3 scripts/check/commit_bench.py --dry-run`（它就是 pre-commit 生產cell），
固定的完整側參數是：

```sh
python3 scripts/check/llama_bench_matrix.py --arms prod-new --prompt 2048 --gen 128 \
        --depths 512 --reps 3 --ctx-size 0 --warm-skip 64
```

- **加一個實驗臂＝只加 env，一個形狀參數都不許動。**
  特別是**不要**自己加 `--spec-type`（prod-new 是 **MTP off**）、`--ctx-size`、`--batch`，
  也不要把 `--prompt` 改成 0。
- ⚠ **`--prompt 0` 與 `--prompt 2048` 不是同一個 cell 的兩種看法**：2048 的 prefill 會
  **把專家池暖起來** ⇒ miss 率直接不同。任何 **fill／IO／命中率** 的結論
  **必須聲明是哪一個**，兩者不可混用。
- **錨定驗證（每次必做）**：這個 cell 跑出來必須落在 **~11.5 t/s**
  （`efba7c1d5` 乾淨基線 **11.49**、另一輪 **12.17**）。
  **偏差 >15% ⇒ 先懷疑口徑，不是懷疑機器。** 不要拿偏差值去推任何結論。

### 反面案例（2026-09-25 03:2x，我自己踩的）
自拼 cell（`--prompt 0` **冷池** ＋ `--spec-type draft-mtp` **MTP on，union 19/層 vs 8**
＋ `--ctx-size 4096` ＋ `--batch 512`）⇒ 兩處偏差**都朝把 fill 放大**的方向，
得到 tg **8.47** 而生產是 **11.49**。
⇒ 那天所有基於它的數字（含「fill 佔 step 33.5%」）都**必須標注為「冷池 + MTP on」regime**，
不能直接寫成交付結論。教訓：**先對錨點，再談槓桿。**

### 配套：後台任務「一輪一跑」
`sleep 600` 承載兩輪的那種長後台任務，**會在 sleep 期間被掐掉且無任何輸出**
（03:2x 一晚發生兩次，`a3` 連 gate 都沒跑到）。
⇒ **一輪＝一個後台任務**，冷卻與複驗閘門放在**主線程**，被掐最多丟一輪。
- **【使用者約定 2026-09-18 起；09-21 重申】速度數字一律用 llama-bench**，prefill＋decode 兩軸並列、
  禁止單軸引用。`http_duo.py` 只用來研究「儀器間差異」。
  - **禁止跨儀器相乘**：llama-bench 的 t/s × server log 的 ms ⇒ 不可引用（§EN-392 第一版犯過，已撤銷）。
    **server log 的 ms 級步時只做機制診斷**，不得進 t/s 表。
  - **引用必帶 cell 口徑**：`--reps`／`--batch`／`--ctx-size`／`--warm-skip`／`--spec-type`，缺一不可引用。
  - **儀器歸屬（已核對原始碼）**：llama-bench ＝ `prod_profile.py`／`profile_duo.py`／`paired_ab.py`；
    server（不可算 t/s）＝ `http_duo.py`／`decode_sweep.py`／`llama-server` log／
    `Backup/phase_decomp/poolsize_ab.py`（它起 server 聽 port）。
    ⇒ **「記憶體超訂 +5.8%」是 server 口徑，待以 llama-bench 重測。**
- **★ 單臂噪音 ≈ ±1.9 t/s**，「發射時 NOMINAL」「臂內 worst」「引擎版本」「記憶體水位」四個已記錄變數
  **都排不出順序**（單 cell n=8 的 `|r|` ≪ 臨界 0.705；「噪音源＝記憶體」已收回）⇒
  **走配對設計抵銷未知慢漂，不要繼續找解釋變數**。後設相關**一定要限定同一 cell**。
- **配對設計（`paired_ab.py`）**：**AB／BA 交替 ＋ `median(A/B)`**；熱閘門**預設關**（開了會把一對的兩半
  隔開幾分鐘，正好放大要消除的漂移）。**必跑第一步 `--null`（兩槽同 binary）＝ 儀器噪音底**；
  自身就散 ±10% ⇒ 正解是「不可測」不是「沒測到」。
- **★★ `--reps` 是 decode cell 口徑的一部分**：reps=1→7.43、2→6.03、3→9.10／10.15（12.57 也是 reps=3）。
  機制：rep 1 冷池，`--warm-skip 64` 只跳過**單次 run 內**前 64 token、**不跨 rep**
  ⇒ **decode 只能在「同一個 `--reps`」下互比**。
- **記憶體壓力別想著「釋放」**（`purge` 要 root、沒 swapoff）：把「進入每一臂的記憶體狀態」設成
  enforced 條件（`--min-headroom-mb`），等不到就 abort。**「確認沒別的 session」不能用 `pgrep llama`**
  （09-18 的競爭者 argv 裡沒有 llama 字串）⇒ 用 `paired_ab.py preflight()`，每一對之前都重查。
- **cell 型工具 ≠ run_server.sh：batch 差 11 倍**（`caliber_env.py --equiv`）⚠ **只對 `prod_matrix`／
  `profile_duo` 的 cell 成立**（`-b 512`）；`spec_cost_curve.py` 走 `lbm.default_batch()` ⇒
  `-b/-ub 5632`，與 server 同。⇒「服務 vs bench 的差距是環境造成的」**尚未被證明**（實測 +13~30%，非常數）。
- **`CGC_SERVER_MTP=0` 是一整組旋鈕**：讓 8 個 engine env 整塊消失（`CGC_DRAFT_DECODE`、
  `CGC_MM_BITIDENT`、`CGC_MTP_NO_WARMUP`、`CGC_NO_PREFETCH`、`CGC_NO_SEQ_RM_PROBE`、`CGC_VERIFY_DECODE`、
  `CGC_WARM_NPAST`、`LLAMA_EXPERT_CACHE_LAYER_CAPS`）。⚠ 但它換的那份模型與預設**逐視窗全同 bytes**
  ⇒ **模型檔不是混淆變數**。
- **prefill 只比同 prompt 長度**；`http_duo` 依 profile ctx 自動縮（prod25 ctx=4096 ⇒ ~2025）。
- **`prod25` 在 llama-bench 兩軸上都量不到**（`n_batch` 被池路徑 `cgc_pool_max_tokens()` 夾在 8、
  `compat()` 拒 `-p 2048`）⇒ 高速候選只剩 prefill250 血統。
- **`CGC_MM_BITIDENT=1` 本來就是預設**（`run_server.sh:2187` 未設就強制）⇒ 想量「不上鎖」必須明確設 `=0`。
- **★ bisect 不必重建**：`git archive <commit> src/llama.cpp/build/bin` 取舊 engine ＋
  `install_name_tool -rpath <真build/bin> <tmp/bin> <f>` ＋ `codesign -f -s -`（**`LC_RPATH` 是絕對路徑，
  不改就載到當前 dylib，bisect 等於沒做**）。**不要為 bisect 重建。**
- **⚠ 儀器坑：`CGC_GPU_OPS` 的 `uni` 欄不可用**（24 個 op 裡 23 個是 `-1`，唯一有值的 RESHAPE
  826 µs/node × 598 node ≫ 步時 112.84 ms，物理不可能）⇒ per-op 歸因要換 `CGC-NSM` 的 per-buffer `dur_ns`。
- **`CGC-MMID-ASSERT` 的 `id_oob` 不是「消費者讀到什麼」的證據**（encode 期由主機讀 `op->src[2]->data`）
  ⇒ 只能用內核側讀數。
- **md5 只在同 build 指紋 ＋ 同 `predicted_n` 下可比**；交錯 A/B ×3 ＋配對中位。
- **建置新鮮度的判準是「輸出有沒有編譯行」**，不是 exit code／產物存在／mtime。
- **D5**：`ORACLE_PINNED_ENV` 釘在 `BATCH/UBATCH=6144`；**先看 `comparable` 再讀 M1/M2/M3**。
  現行參考檔 `ref_iq3_pool8gb_M2_6144_bitident_v6_nbaware.jsonl`（md5 `72d82a33ad79e0e69bc935acd24228f2`）；
  該檔在 `Backup/`（未受版控）⇒ 09-19 起 **md5 釘在程式的 `REF_PINS`**（不符 exit 4），
  **重新基線後要在同一顆 commit 更新 `REF_PINS`**（`--write-ref` 不會自動改它）。
- lesson schema：`superseded_by` 必填（可 null）、`applies_to` 每項是**檔案路徑**。

## ★ budget 閘門（2026-09-24，所有速度 runner 的 launch 前置）

- 任何會起 server／llama-bench 的量測腳本，**launch 之前加一行**：
  `. "${REPO}/scripts/check/budget_gate.sh"`（`scripts/check/budget_gate.sh`，`--self-test` 7/7）。
- `BUDGET_GATE=strict`（預設，超訂 exit 2 拒跑）／`warn`（放行但 export
  `CGC_BUDGET_OVERSUBSCRIBED=1`，樣本帶污染標記）／`off`（不檢查）。
  未超訂時額外 export `CGC_SERVER_STRICT_BUDGET=1` ⇒ `run_server.sh` 自己再擋一次（同口徑）。
- ⚠ **16 GB 這台：prod-new pool 8 GiB 靜態超訂 4838 MiB**（13030+8192=21222>16384，與實測
  resident 對上）⇒ strict **會連交付 cell 一起拒跑**。這不是 bug，是強迫選擇：
  `BUDGET_GATE=warn` 明確承認，或 pool ≤ 3 GiB（3354 MiB）。
- ⚠ `budget_preflight.py` 是**工具**、不會自己被叫到 —— 沒接線的 runner 等於沒閘門。
  已接：`rho_fill_ab.sh`／`route_overlap_3prompt.sh`／`masscov_decode_shape.sh`。
  ★⚠ **`llama_bench_matrix.py` 沒接** ⇒ 本線跑 8 GiB、執行線跑 3 GiB（見下節）。

## ★★ 唯一的 gate 規格（2026-09-25 operator 下令；權威 `docs/MEASUREMENT_CONTRACT_2026-09-25.md` §3）

**現況是三處矛盾**：`arm_two_pass` 的 G1 起跑門檻 `swap ≤ 1024` ⚔ `lane_watchdog` 的 kill 門檻
`swap > 3072` ⚔ `budget_gate` 超訂 4838 MiB 即拒。**以下為唯一版本，衝突時以它為準。**

**量測形狀（所有臂一律，不得逐臂自訂）**
`profile=prod-new`、`-p 2048 -n 128 -d 512 --warm-skip 64 --ctx-size 0`、`-b/-ub 5632`、
`--load-mode none -ngl 99`、**`-r 3`**、`CGC_SERVER_MTP` 必須 **ABSENT**、**pp+tg 同報**。

**budget**
- `CGC_EXPERT_CACHE_BYTES = 8589934592`（8 GiB）。
- ★★ **pool 大小是 cell 的一部分，不是可調旋鈕** ⇒ **廢除「pool ≤ 3 GiB 才合規」那條**。
  超訂（16 GB 上靜態超訂 4838 MiB）時合格做法只有兩種：
  ① `BUDGET_GATE=warn` ＋ 把 `CGC_BUDGET_OVERSUBSCRIPTED=1` **寫進產物**；
  ② **整條線（含所有對照臂）一起改 cell**，並在報告裡標明換過 cell。
  **不准只為通過閘門而縮 pool** —— 那等於偷偷換了 cell。

**swap**（三條線，互不取代）

| 用途 | 門檻 | 誰執行 |
|---|---|---|
| 起跑前置 | `swap_used ≤ 2048 MiB`（＝ `LAUNCH_SWAP_KILL`） | 所有 runner 的 gate |
| 執行中止血 | `swap_used > 3072` **或** `free < 150` | `lane_watchdog --kill` |
| 產物可引用性 | **與 swap 解耦**：只看 thermal ＋ rep 散度(>12%)；swap 高只標 `stressed` | `judge_artifact`（保留） |

⇒ 具體一行：`arm_two_pass.py` 的 `--max-swap-mb` 預設 **1024 → 2048**。

**觸發點與冷卻**
① 所有會起 server／llama-bench 的 runner 一律接 `budget_gate.sh`，**含 `llama_bench_matrix.py`**；
② G1 必須查**現行**看門狗名（`watchdog_daemon`／`lane_watchdog`），不是 `auto_bench_watchdog`；
③ 看門狗對**已宣告的診斷臂**只 warn 不 kill（診斷臂天生 swap 高）；
④ 同臂兩輪之間 `--cool-s 420`（實測最小冷卻）；跨臂配對間隔 ≤ 20 min。

## ★ 外部 harness（`2eb84420d`）的兩個盲區（2026-09-25 審計，權威 `docs/HARNESS_METHOD_AUDIT_2026-09-25.md`）

**跑任何 arm 前先知道這兩件事，否則會拿到假的綠燈：**

1. **它的 G1 `no_watchdog` 只查 `auto_bench_watchdog`**（`arm_two_pass.py:201`），
   而**真正在跑的是 `watchdog_daemon.sh` → `lane_watchdog.py --kill`**（每 600 s 一 tick）
   ⇒ **G1 會在殺手上膛時放行**。（本線 10:21 的 arm 就是這樣死的：tick 10:22:18 / 死於 10:22:19。）
   ⇒ 本線自己的 gate 必須自己查 `pgrep -f watchdog_daemon`，**不要依賴它的 no_watchdog**。
2. **它的預設 `--reps 1` 讓 `judge_artifact` 的散度判據失效** —— `rep_spread` 需 `len(samples_ts)>=2`
   ⇒ `-r 1` ⇒ 「量具被壓垮（>12%）⇒ 作廢」**永遠不觸發** ⇒ 輸出全是綠燈。
   ⇒ **引用它報的數字前，先確認那一輪用的是 `-r 3`。**
3. ⚠ 它內部對 swap **兩把尺不一致**：`judge_artifact` 說「不由 swap 裁決」，
   G1（`--max-swap-mb` 預設 1024）卻把 swap>1024 當拒跑。**引用時要問是哪一把尺。**
- ⚠ shell 坑：被 source 的檔案裡 `return` 只結束自己、**呼叫方會繼續跑** ⇒ 拒跑必須 `exit`。

## ★★ 引用任何數字前：先查「作廢登記表」（2026-09-25 operator 下令）

`docs/MEASUREMENT_CONTRACT_2026-09-25.md` **§7 作廢數字登記表** ⇒ 表內數字**不得作決策依據**
（若要提及，**同一段內必須帶 `作廢`／`⛔`**）。首批三筆：

| 作廢數字 | 為什麼 | 替代 |
|---|---|---|
| **`9.82`**（MTP off） | 09-17 **HTTP 舊口徑、無 warm-skip**（舊口徑 sd ±3.54＝35%） | **生產口徑 MTP off＝11.034／11.793／12.195**（`Backup/nofill_prod/nf2_fill.json`、`nf_fill.json`、`Backup/mw_ab/mw_ctrl.json`） |
| **`12.62`**（MTP on） | 同口徑；`MTP_ABBA_RECHECK` 判「未複現也未證偽」；生產 server 路徑反而是 **×0.695** | **無** ⇒ 交付 cell 缺量測 |
| **`+28.5%`** | 兩個作廢數字之比 | **UNRESOLVED**（要用 warm-skip 口徑在交付 cell 重測） |

⇒ **「現行生產口徑 MTP off 已是 11+」是作廢它的關鍵**：分母本身失效，比值無意義。
★ `12.57`（交付錨點）**不作廢** ⇒ **「目前最好」一律以 12.57 為準**，不含 MTP-on 12.62。
機檢 `scripts/check/void_number_check.py`（selftest 8/8；決策面違規 ⇒ exit 1；
`--all` 列出歸檔面 **172 處**歷史引用 —— dated 產物不回改，但**不得再被引用**）。

## 入口（照抄）

```sh
cmake --build src/llama.cpp/build --target llama-server -j 8   # 產物 src/llama.cpp/build/bin
RUN_REPLAY_BENCH=0 python3 scripts/check/decode_sweep.py --profile prod25 \
  --arms p25-gputime,<臂> --rounds 3 --warmup 0 --n-predict 24 \
  --json Backup/phase_decomp/<名>.json --force
python3 scripts/check/m123_oracle_gate.py --tag <標籤>          # D5，自己起 server（先確認 8080 空）
python3 scripts/check/http_duo.py --profile prod25              # 服務路徑兩軸；prefill 依 ctx 自動縮
python3 scripts/check/profile_duo.py --profile prefill250       # llama-bench 兩軸（**交付口徑**）

# 索引重生（順序固定；有動 .workbuddy/memory/*.md、scripts/check/*、docs/*、agent_harness/engine_loop/* 就跑）
python3 agent_harness/engine_loop/memory/build_memory_index.py     # 先：寫 INDEX.jsonl
cd agent_harness/engine_loop && python3 index_assets.py && cd -    # 後：MANIFEST 記 INDEX 的 bytes/mtime
```

- **`run_server.sh` 有 env allowlist**：未列出的 `CGC_*` **靜默丟棄** ⇒「沒效果」與「沒設到」同形。
  現成的坑：`CGC_PREFETCH_SRC=hist` 在 C++ 有（`llama-context.cpp:2002`）但已被 allowlist 刪除 ⇒
  從任一條路設都不生效；替代是 `CGC_SERVER_NO_PREFETCH=0`（`run_server.sh:2029`）。
- 索引重生**順序固定**：先 `build_memory_index.py` 再 `index_assets.py`（顛倒或只重生 manifest 都不會修）；
  **memory 寫完要在索引重生之前**；`index_assets.py` 不要加 `--out`；範圍不含 `agent_harness/memory/`
  與 `skills/`（另一條線手動 `import_harness_snapshot.py`，要跑就**先快照、後索引**）。

## 環境坑

- BSD `grep` 不支援 `\|`（一律 `-E`）；**本 sandbox 的 bash `grep` 會靜默失效 ⇒ 用內建 Grep 工具**；
  zsh `*.log` 無匹配會不執行整條指令；**沒有 `timeout`**；`ps` 被擋、`pgrep` 可用；
  **`notifyutil -g com.apple.system.thermalpressurelevel` 連 key 一起印，要 `awk '{print $NF}'`**。
- **heredoc 陷阱**：工具呼叫裡的 shell heredoc 會把 `$VAR` 吃掉（patch 腳本先 Write 落檔再執行）。
- **寫 shell 時 `$VAR` 一律寫成 `${VAR}`**：`$VAR` 後緊接中文會把多位元組位元組吃進變數名
  （`bash -n` 抓不到，`set -u` 下才爆）。檢查器 `agent_harness/shared/check_shell_cjk.py`。
- **圖 dump 解析坑**：節點名**含空格** ⇒ `name=(\S*)` 只解析到 ~63%，必須 `name=(.*?)\s+op=`。

## Skill（動手前先讀）

`~/.workbuddy/skills/`：`cgc-commit-gate/`（閘門鏈、`BIN_DIR`、`RUN_REPLAY_BENCH=0`、索引順序、多段式收尾、
lesson 欄位陷阱）、`cgc-decode-attribution/`（decode 歸因與輸出擷取規則）、
`cgc-prefill-thermal-delivery/`（prefill t/s 的條件式交付）、`cgc-whitepaper-delivery/`（`docs/*.html` 版式）。


## ★ 儀器口徑坑（2026-09-21 由 `MEMORY.md` 移入）

- **★★ `run_server.sh` 的 `SERVER_ENV` 是白名單，沒列到的 `CGC_*` 會被靜默丟掉**
  （2026-09-24 實踩）：launch line 走 `env "${SERVER_ENV[@]}"`，`llama_bench_matrix.resolve()`
  也只認 `CGC_DUMP_ENV=1` 印出來的 `ENV K=V` 行 ⇒ **ambient 設了但沒進白名單 = 完全沒設，且不報錯**
  （A/B 三臂會跑出一模一樣的數字）。新增任何 `CGC_*` 開關都必須同時在 `run_server.sh` 加
  `if [ -n "${X:-}" ]; then SERVER_ENV+=(X="$X"); fi`。
- **★ 開關判定要寫「取值」不要寫「存在」**：`getenv(X) != nullptr` 會讓 `X=0` 也變成**開**。
  實害：把「顯式預設 0」寫進 profile 而 binary 還是舊的 ⇒ **別條線的量測靜默開著 P0 跑**
  （2026-09-24 11:19 的 llama-bench，那一趟數字作廢）。配套：白名單也只傳非 0 值，
  這樣新舊 binary 下「關」都真的是關。
- **`prod-new` 的 MTP=0 分支會換 checkpoint**：`CGC_SERVER_MTP=0` ⇒ `MODEL=Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`
  （**沒有 nextn head**）；`=1` 才是 Nail（與 prod25 同一個檔）。用 prod-new 量 MTP 相關的東西
  必須疊 `CGC_SERVER_MTP=1`，否則是跨 checkpoint 比較。
- **`prod_profile.py --profile` 收的是「server profile 名」不是 arm 名**（2026-09-24 實踩）：
  `--profile prod25-stream` 直接報 `CGC_SERVER_PROFILE must be off|qa-zh|longform-zh|coding|
  legacy-25plus|prod25|prefill250|prod-new`（`run_server.sh` 枚舉）。`prod25-stream` 是
  `llama_bench_matrix.ARMS` 的 arm，只能出現在 `--ref-arm`。**decode 交付 cell 的 profile 是 `prod25`。**

- **`nd` ≠ dispatch 數**：`CGC-GPUOPS` 的 `nd` 是**圖節點計數**（`ggml-backend.cpp:2372-2379`
  逐圖節點取 `cgc_node_op()`），它不知道 Metal 之後的 empty 過濾，`work`/`NOOP` 欄也只看 op 類型不看形狀
  ⇒ **任何以節點數為分母的歸因都要先問「有沒有被 `ggml_is_empty` 濾掉」**。
- **`gpu_union` 是比 t/s 靈敏得多的配對指標**：同配置連跑兩次差 **0.2%**（102.0 vs 101.8）vs t/s ±27%。
  ⚠ **但那只對 prefill 配對成立**：decode **逐步**的 `gpu_union` 臂內 p90/p10 = **2.27×**（50–193 ms），
  並不比 t/s 緊（09-21 實測）。
- **census 三坑（09-21）**：① per-op 行由多執行緒 encode **交錯寫出**，`graph=` 編號不順序
  ⇒ **逐圖歸因不可用**，只有全域求和有效；② **top-14 截斷**，per-op 表只解釋 44–47% 的 dispatch
  （cluster-1 捕獲率僅 68%）⇒ 絕對量必須先修正；③ 上限 48 cmd_buf ≪ 一步的 ~353 ⇒ 任何
  「每步」量都要先乘 `k = nodes_step/nodes_窗口`。
- **⚠ sentinel 自己是一支 `-p 2048` prefill，會把機器推進 HEAVY** ⇒ 放臂**前**會害該臂被 gate 拒絕
  （09-21 `off` 臂：sentinel 讀 HEALTHY 270 → 隨即 gate 讀 HEAVY）。改放臂**後**即可拿到視窗。
- **⚠ `CGC_MM_BITIDENT=1` 本來就是預設**（`run_server.sh:2187`）⇒ 想量「不上鎖」必須明確設 `=0`；
  上鎖價錢 ≈ 4–5%，**今天已在付且 G2 禁止退**。
- **★ miss 分解（compulsory/capacity）欄位名與門控（2026-09-24 讀碼覆核）**：
  計數器本身**無 env 門控**（`llama-expert-cache.cpp:891-902`、`:1167-1172`）。三個出口不同名：
  `CGC-SHAPE ... compulsory= capacity= evict=`（`llama-shape-knob.cpp:234`，縮寫 `evict`）；
  `miss attribution: ... evictions=`（`:2764`，**全名**、且**沒有 hit_pct**）；
  `final stats: ... (hit rate X%) ... pread_usec=`（`:2553`）。
  ⚠ `CGC-RIG-SNAPSHOT ... pread_usec=`（`:520`）**有門控**：要設 `CGC_PREFILL_PROTECT_FILE`
  （`:535-537` 沒設就 return）⇒ 09-23 log 零命中是這個，不是沒資料。
  ⚠ `pread_us` 是**跨 worker 加總**（`llama-expert-cache.h:600`）不是 wall clock。
  ⚠ **`capacity 佔比** 會隨 run 長度機械上升**：`ever_loaded` 只在 init 清一次（`:3738`），
  compulsory 上界 `n_layer×n_expert`=10240 ⇒ 比 **capacity/1k-req**，別比佔比。
  機檢：`scripts/check/miss_attr_gate.py`（`--self-test` 15/15）。
- **★★ llama-bench 預設用 `std::rand()%n_vocab` 填 prompt，不是真實文本**（`--help` 原文：
  "The random fill is NOT what the server sees"）⇒ **歷來沒給 `--prompt-file` 的 bench，
  其路由／cache 統計都不代表真實語言分佈**。`scripts/check/pin_profiles/route_top142_p0_2026-09-23.txt`
  就是 `--prompt 0`（= `-p 0`）且無 `--prompt-file` 的產物 ⇒ 它是隨機 token 的路由，別拿它當
  「真實 prompt 的駐留需求」。要真實文本一律加 `--prompt-file <path>`（內容會 cycled 填滿）。
- **⚠ masscov 的 `cur`／`selcold` 是「整 run 累計」，含 prefill**（`llama-expert-cache.cpp:3084-3089`）
  ⇒ 在 prefill 主導形狀下（如 `pin_abba.sh` 的 `-p 2048 -n 128`，prefill 佔 91%）池從空開始填，
  冷訪問被 prefill 大量貢獻，讀到的 34% **不是 decode 穩態**。量 decode 的價格要用
  decode 主導形狀（`-p 256 -n 512`）。見 `scripts/check/masscov_decode_shape.sh`。
- **⚠ masscov dump 的欄位口徑不統一**：`cur`／`kN` 是**比率**（0–1），`sel` 是**次數**（絕對量），
  `selcold` 又是**比率**（`:3088` 除以 `massc_sel_total`）。把 `selcold` 當次數加總會得到
  荒謬的「冷訪問 ≈ 0」。正確讀法：`selcold` 取**逐層平均**，`sel` 才是次數。

## ★★ 指標穩定性排名（2026-09-22 §EN-417 實測，選主指標前必讀）

權威在 `docs/METRIC_STABILITY_2026-09-21.md`。同一交付 cell、可直接比：

| 指標 | 離散度 | 用法 |
|---|---:|---|
| **`µs/miss`（pool_wait_us/misses）** | **CV 1.95%**（w=8,n=5）／4.55%（w=2） | ✅ **主指標** |
| t/s（llama-bench avg_ts） | **CV 7.88%**／7.43% | 只當「有沒有跑反」的哨兵，幅度不引用 |
| `union`（block-mean 修正後 SEM，8 run 中位） | 5.0% | 同 run 內相對比較 |
| `union+gap` | **7.7%**（逐行 CV 35–47%） | ❌ 與 t/s 同級或更差 |
| `gap` 單獨 | **逐行 CV 64–85%** | ❌ 不可作證據 |

- **⚠ 更正**：「單臂 t/s 噪音 ±27%」是 **K3 cell** 的值；**交付 cell（reps=3 ＋臂間冷卻 150 s）
  實測只有 7.88%**（差 3.4×）。兩個 cell 的噪音底不同，別混用。
- **⚠ `union+gap` 不是步時**：交付 cell 同 run，步時 267.84 ms vs union+gap 127.29 ms
  ⇒ **低估 2.10×**（它是 GPU 時鐘跨度，不含 `wait`/`cb`/`submit`）⇒ 拿它算天花板會得到 26.5 t/s
  這種不存在的數字。要步時就用 `ML / t_s`。
- **「逐步樣本多 ⇒ 穩」是錯的**：37–59 行強自相關，block mean(5) 修正後有效 n 只剩 5–8；
  且後半 vs 前半 −2.2%~−21.6%（8 支全負）⇒ 非平穩，平均哪幾步會直接改變答案（bias）。
- **所有 run 的 `skipped` 均值 4.9–7.3（非 0）**，程式註明「skipped 必須為 0」
  ⇒ `busy`/`union`/`gap` 帶未知偏差，**只可做同 run 內相對比較**（含 §EN-416 的 busy/union=1.510）。
- 測出 1.7% 效應需要每側幾臂（雙樣本 95%，未配對上界）：**t/s ≈165 臂，µs/miss ≈10 臂**。
- **⚠ 門禁只在「起跑前查一次」會漏掉跑途中的散壓惡化（09-22 §EN-423 實例）**：
  `context_depth_ab.py:103` 的 `gate()` 只叫一次；14:47 NOMINAL 起跑、14:55 讀到 **HEAVY**，
  整輪 A 臂掉到 7.5–8.6（封版 12.57）⇒ **絕對值全廢**。
  ⇒ 規矩：A/B 類腳本要**逐臂（最好逐 rep）記 `tp.stamp()`**，收尾判「**全程 NOMINAL**」，
  不只在起跑時 gate。ABBA 只能抵**單調漂移**，抵不了**步階**（散壓掉檔）。

## ★ 量測前閘門第 4 條：rogue watchdog（2026-09-25 05:2x 實測）

`scripts/check/auto_bench_watchdog.sh`（**不是本線的**，03:36 由 Doubao agent session 建並拉起）
**每 4 秒 `kill -9` 所有 `llama-bench`／`llama-server`** ⇒ 03:36–05:22 之間每一次量測都是
`rc=-9` 被秒殺。閘門除了 thermal／8080／`pgrep -x llama-*` 之外，**必須再加一條**：

```sh
pgrep -f auto_bench_watchdog.sh >/dev/null 2>&1 && busy=1     # 非空就別跑
```

- **診斷特徵**：`rc=-9`（SIGKILL）＋ stderr 停在 `[spec] draft-mtp enabled ...` banner（model 還沒
  load 完）⇒ **外部 SIGKILL，不是 OOM／assert**（後者 rc=-6/-11，或 stderr 有 `error:`／`GGML_ASSERT`）。
  之前把它歸因成「背景任務被網路中斷殺掉」是**誤診**，浪費了三輪。
- 止血：`pkill -f auto_bench_watchdog.sh` **沒生效**，要 `kill -9 <pid>` 顯式打；`/tmp/auto_bench_watchdog.log`
  有一筆一筆的 `-> killed <pid>` 可對帳。

## ★ 交付 decode cell 的對照臂必須 15~20 min 內配對（2026-09-25 05:2x 量到）

同臂、同 build、同形狀、兩臂 thermal 都 NOMINAL 的情況下，**ctrl 03:21 = 8.47 t/s → 05:39 = 11.38 t/s
（+34.3%）**。⇒ 這個 cell 跨 ~2 小時的單點數字**完全不可比**；拿 2 小時前的 ctrl 當對照臂會得到
假的 +24.7%。**配對窗口沿用 r5/r6 的 15~20 min**；來不及就用 **ABA**（A→B→A）把 B 夾住。


## MEMORY.md 瘦身移入（第六輪，2026-09-25）

★★ **2026-09-25 operator 下令：量測契約 `docs/MEASUREMENT_CONTRACT_2026-09-25.md`（必讀，四條）**
① 白皮書必標 **TPOT 分解 / decode 分段拆解 / pp+tg 同報**（skill `cgc-whitepaper-delivery` §1.5）；
② 里程碑改**雙欄制**（數學推算 ＋ 量測打底，缺量測顯式標）；
③ **唯一 gate**：形狀一律 `-r 3`、**pool 是 cell 的一部分（廢除「≤3 GiB 才合規」）**、
swap 三條線分開；
④ **每支臂都要 目標→判準→結果→判定**（全臂台帳 `docs/DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md`，
機檢 `scripts/check/arm_ledger_check.py`，selftest 9/9）。
⑤ **成果分級（修正版，契約 §5）**：**① 攻關成功（`prefill ≥250 ∧ decode 超越目前最好 12.57`）／
   ② 攻關過線（`250+ ∧ decode ≈ 現在`）／③a 實驗目標達成·階段性／③b 實驗目標達成·可放生產／④ 廢棄**。
   ★ **① 是兩件事同時**，只做一半只能 ②；**③a→③b 的分界是「能不能進生產設置」**。
   ⛔ **現況 17 列：① 0 / ② 1 / ③a 5 / ③b 3 / ④ 8 ⇒ 本線沒有 ①。**
   （MTP 那格因 `9.82/12.62/+28.5%` 作廢，由 ③b 降為 ③a。）
   無實測權的格子（M-F5／M-CB）標「不適用（非本線）」。
⑥ **子目標 S/M**（契約 §4）：③ 的成果必須標 **S 序列化消減 / M MTP on 加速 / 兩者 / 不適用**。
⑦ **每次實驗畢必出分解報告**（契約 §8）：算術 ＋ 量測（標實測/擬合）＋ 小目標 ＋ 邊界。
   範本 `docs/S1_TPOT_DECOMPOSITION_2026-09-25.html`、`docs/MTP_AMORTIZATION_2026-09-25.html`。
   ★ **MTP 攤薄 ≈ 0 ⇒「加速比 2」不成立**（需 verify 邊際 42.03→≤17.9 ms；光拉 accept 只值 +6%）
   —— 算術全文見 `MTP_AMORTIZATION`。
   機檢 `arm_ledger_check.py`（18/18）／`gate_consistency.py`（7/7，4/8 紅＝執行線未接線）。
⑧ **作廢數字登記表（契約 §7）**：`9.82` / `12.62` / `+28.5%` **不得作決策依據**
   （HTTP 舊口徑、無 warm-skip；**生產口徑 MTP off 已 11.03~12.20 ⇒ 分母失效**）。
   **引用任何數字前先查表**（要提及就得同段寫 `作廢`／`⛔`）。★ `12.57` 不作廢 ⇒「目前最好」以它為準。
   配套 `docs/VOID_NUMBER_CITATIONS_2026-09-25.md`（逐字依據 ＋ 格式 ＋ 全庫 **292 處**盤點）。
   ★ 教訓：這組數字 **09-24 20:56 就判過不可引用卻仍流通** ⇒ **口頭降級不算降級，進表才算**。
   機檢 `void_number_check.py`（12/12；決策面 exit 1，`--all` 稽核，`--citations` 生表）。
⑨ **250/25 攻關總圖**（`docs/mindmap/`）：**41 條目／192 份報告全映射**，**兩維度** ——
   **階段**（生產 14／實驗 7／已結案 20）× **子目標**（**S 序列化＝粉紅 17**／**M MTP on＝黃 8**／
   兩者 1／不適用 15）。★ **點子目標標籤＝看說明**（1現狀/2推論/3量測/4目標 ＋ 拆解狀況 ＋
   原始報告連結／內嵌），**點標籤旁 ⊘ ＝只過濾**；S→`S1_TPOT_DECOMPOSITION_2026-09-25.html`、
   M→`mtp_amortization.html`（四段內容固化在 `mindmap.json` 的 `subgoal_briefs`）。
   ★★ **每條目一份技術白皮書** `briefs/<id>.html`（**目標→判準→結果→判定** ＋ 依據＋對應報告；
   總目錄 `briefs/index.html`）；單條面板有「技術白皮書 ↗」與「內嵌」。
   資料＝`mindmap.json` 的 `goal`（目標）／`crit`／`res`／`evid`／`note`（**缺 goal 會紅**）。
   機檢 `mindmap_build.py`（15/15）＋ `mindmap_brief_build.py`（**13/13**）＋
   `mindmap_click_smoke.mjs`（**44/44**，需 jsdom）。


## MEMORY.md 瘦身移入（第七輪，2026-09-25）

★★ **2026-09-25 operator 下令：量測契約 `docs/MEASUREMENT_CONTRACT_2026-09-25.md`（必讀）**
① 白皮書必標 **TPOT 分解／decode 分段拆解／pp+tg 同報**；② 里程碑**雙欄制**（推算＋量測，缺量測要顯式標）；
③ **唯一 gate**：形狀一律 `-r 3`、pool 是 cell 的一部分、swap 三條線分開；
④ **每支臂都要 目標→判準→結果→判定**（台帳 `DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md`）；
⑤ **成果分級（§5）**：① 攻關成功（pp≥250 ∧ tg 超越 12.57）／② 攻關過線／③a 階段性／③b 可放生產／④ 廢棄
—— **① 是兩件事同時**，只做一半只能 ②；**本線現況沒有 ①**；
⑥ **子目標**：S 序列化／M MTP／兩者／**C kernel＋頻寬**／不適用；
★★ **M 軸已被複核推翻** ⇒ `docs/MTP_AMORTIZATION_RECHECK_2026-09-25.{html,md}`（**v2 支撐審計版**）：
實測 **k_eff=1（不是 3）／S=0.914·0.682·1.050（三輪，淨負）／E=1.62／a=0.62／m=0.868（僅 k_eff=1）**
⇒「攤薄倍數幾乎完全由 a 決定」**不成立**。
原 `docs/mtp_amortization.html` 已歸檔（`docs/archive/mtp_amortization_SUPERSEDED_2026-09-25.html`）。
⛔ **a=0.44 不可再當輸入**；`13.7 t/s／S=1.19` 作廢（13.78 實為 **MTP off** 的 HTTP 單流）。
★ **公式推論支撐分級**（operator 09-25 下令）：`M` 實測／`D` 量綱恆等可引用；
`X` 外推／`A` 假設獨立／`S` 推測因果／`U` 無出處 ⇒ **一律廢棄**（機檢 `formula_audit.py` **13/13**）。
⇒ v2 廢棄 9 條：k=3 的 0.794、`a/m` 上界 0.714、「沿 k 軸都虧」、a 拉滿 +7%、
**「m≈0.87 差 0.5%」（量綱錯配，同義口徑 1.176 vs 0.868 差 35%）**、「draft 7.5%」、「hit% 57%」、
「S=1.19 來自兩儀器相除」、「a=0.44 是 ctx_other」（未歸因）。★ 09-24 的 `m(k)` 散佈 1.40× ⇒ **m 非常數**。
⑦ 每次實驗畢必出分解報告（範本 `S1_TPOT_DECOMPOSITION_2026-09-25.html`、`MTP_AMORTIZATION_2026-09-25.html`）；
⑧ **作廢數字表（§7）**：`9.82`／`12.62`／`+28.5%` 不得作決策依據（機檢 `void_number_check.py`）；
⑨ **250/25 總圖 `docs/mindmap/`**：**44 條目／196 份報告全映射**；維度＝**階段**（生產 14／實驗 10／已結案 20）
× **子目標 5 類**（**S 18**／**M 7**／兩者 1／**C 8**／不適用 10）。
★ **軸性質**：S、M＝活躍攻關軸；**C＝天花板軸**（受物理約束、持續證偽，但必須留在分類裡 —— 否則
device span 56–61% 無人認領）；**both 保留**（S／M 都動 hook，**收益不可相加**）。
★ 狀態橫幅三條（**兩軸皆未交付**／耦合／C 定位）固化在 `mindmap.json` 的 `meta.status`。
★ 點子目標標籤＝看說明（1現狀/2推論/3量測/4目標＋拆解＋報告連結／內嵌），點 ⊘ ＝只過濾。
★★ **每條目一對白皮書** `briefs/<id>.html` ＋ `.md`（**html＋md 成對**，樣式對齊
`docs/S1_ASYNC_GATHER_PIPELINE_2026-09-25.html|md`：三卡＋四段卡＋對照表＋依據表＋黃框結論），
總目錄 `briefs/index.html`／`index.md`；條目可選 `diff`／`risks`／`detail_html`。
機檢 `mindmap_build.py` **19/19** ＋ `mindmap_brief_build.py` **13/13** ＋ `mindmap_click_smoke.mjs`
（**60/60**，需 jsdom；ESM 不吃 NODE_PATH ⇒ `JSDOM_PATH=<workspace>/node_modules/jsdom/lib/api.js`）。
★ **已交付 commit `65c76b8c7`**（146 檔，含他線 src bytes；**未 push**）⛔ **但它的 D5 未通過**
（M1 2/3／M3 2/3，coverage 60% vs 歷次 100%，未歸因）⇒ **owner 重新基線前不要引用它**。
**契約九條的逐條全文（含分級 17 列、作廢數字 292 處盤點）⇒ `MEMORY_HYGIENE.md` 末節第六輪移入。**

## 入口坑（2026-09-26 實踩，寫死）

1. **池 budget 覆寫的鍵名**：要 `CGC_SERVER_EXPERT_CACHE_BYTES`（有 `SERVER_` 前綴）。
   無前綴的 `CGC_EXPERT_CACHE_BYTES` 被 `run_server.sh` 守門**靜默忽略**
   （`run_server.sh:572-575` 有註；09-25 曾因此做出「4G≈8G」的假結論）⇒ 兩臂其實都 8 GiB。
   靜態驗法（不吃 GPU）：`llama_bench_matrix.resolve('prod-new', {'CGC_SERVER_EXPERT_CACHE_BYTES':...})`
   ⇒ 看 `server_argv` 裡的 `-expert-cache` 有沒有跟著變（會變才算數）。
2. **`harness.py bench` 的 cell contract 是 fail-closed**：`expert_cache_bytes` 偏離權威
   8589934592 ⇒ `⛔ cell 口徑與測試卡權威 block 不一致 — 拒跑`。⇒ **池大小／預算類掃描
   不能走生產入口**（設計如此；要用別的入口並標非生產口徑）。
3. **起跑前看「系統 free%」而不只是 swap**：2026-09-26 03:20 那支臂在 free **14%**、
   外來 swap 4.46 GiB 下起跑 ⇒ `CGC-WATCHDOG: Metal stall`（stale=10020ms）於 compute #1 abort
   （rc=-6）；同一支臂在 free 85% 時連跑兩次都成功。harness 的 `[swap] verdict=advise` 已列出
   佔用者（多是 WorkBuddy/Freebuff renderer，不是引擎）⇒ **先關掉或重開機再跑**。
4. **`harness bench --workdir` 不會幫你 mkdir**：`... | tee <workdir>/log` 會先開檔失敗 ⇒
   管子壞掉、harness 仍繼續跑並寫入壞管子。先 `mkdir -p`，或用 `>` 重導向。
5. **`llama-bench` 一個 arm 會起兩個行程**（pp cell 一支、tg cell 一支）⇒ `pgrep llama-bench`
   看到 2 個不等於「有別條線在跑」；判別看父行程。
6. ⚠ **`free%` 的口徑只有一個權威**：`memory_pressure` 的 **`System-wide memory free percentage`**
   （`harness._sys_snapshot()` 存的 `memory_pressure` 欄位就是它）。**不要用 `vm_stat` 的
   free＋speculative 自己算** —— 2026-09-26 09:0x 就這樣算出 **8%**，而權威值同時是 **84%**
   （漏了 inactive／purgeable，macOS 上絕大多數可回收頁在那裡）。
   ⇒ 差 10 倍的結論會直接決定「今晚能不能跑 GPU」。門檻（實測）：成功臂起跑 **78~86%**，
   被 watchdog 擋下那次 **14%**。承上第 3 條，看的是同一個數字。
7. ⚠ **自證印不要把閘門設成「有成功才印」**：`CGC-MM-PUB` v1 用 `n_wrote > 0` 才推進 ⇒
   「表是空的」與「全部被 skip 」印出**同一個沉默** —— 而那正是它要分辨的兩件事。
   改法：前 N 次呼叫**無條件自報**（含 `n_leaf=0` 的行），靠覆蓋 prefill／暖機那幾次無 mask 的
   compute 來同時看到兩種狀態。教訓：**診斷碼的「安靜」不是成功，是資訊缺失。**

## ★★ 窗口判準：`swap_used` 是錯的軸，壓縮器**流量**才是對的（2026-09-26，freebuff 線）

**一句話**：`vm.swapusage` 的 `used` 是**存量**（幾小時前推出去、還躺在磁碟上的頁），**對 t/s 沒有預測力**。
移動 t/s 的是 macOS **記憶體壓縮器的流量**（`Compressions`／`Pageouts` 的 MiB/s）。

**同一台機上實測的兩個 regime（校準來源）**：

| regime | swap 存量 | compressions | 結果 |
|---|---:|---:|---|
| 閒置盒 | **7993 MiB**（＝舊門檻 2048 的 3.9×） | **0.00 MiB/s** | 乾淨 |
| 跑一支 3-rep 生產臂 | 同 | **~150 MiB/s** | 髒 |

**相差四個數量級，而存量完全看不出你在哪一個**。因果證據：**同配置、同 launch swap，
tg 11.36 vs 8.31，壓縮器流量 32 GB vs 127 GB/臂**。

**入口（唯一來源）**：
```bash
python3 scripts/check/compressor_pressure.py --seconds 12        # 印 QUIET/DEGRADED + 存量 label
python3 scripts/check/compressor_pressure.py --require           # launcher 語意：不 quiet 就 exit 1
```
- 門檻 **1 MiB/s**（刻意鬆，只用來分 regime，不是拿來省小數點）；**fail-closed**，
  且 **`unknown` 是獨立判決**（讀不到計數器 ≠ quiet）。
- **存量只當 label 印出來，永不決定**。計數器 parser 單一來源＝`mem_oversub_probe.vm_snapshot/delta`，
  page size 向 `memory_pressure._page_size_kb` 要（**不要硬編 4096：這台是 16 KiB，差 4×**）。

⛔ **因此作廢以下兩種說法**（我今天都講過）：
1. 「swap 6~8 GB ⇒ 盒子髒，要關掉 renderer／重開機」—— **用錯軸**。實測同水位（6140 MiB）而
   `QUIET  compressions 0.00, pageouts 0.00 MiB/s` ⇒ **那是乾淨的盒子**。
2. 「等 swap 掉回 2048 MiB 以下再跑」—— 存量**不會**掉，它只是躺著；等它是等一個沒有機制的东西。
   （`harness.py` 的 `[swap] verdict=advise ... > 起跑門檻 2048 MiB` 那行屬於這一類，
   它印的是 label 不是判準；判準要走 `compressor_pressure.py`。）

⚠ **熱條件與壓縮器是兩個獨立的閘門**：`COLD-STATE`（安靜 ≥1800 s）管的是**散熱包絡**
（→ GPU 有效時脈 → pp 吞吐，見 skill `cgc-prefill-thermal-delivery`），
壓縮器流量管的是**記憶體階層**（→ decode 的 IO/延遲）。
**兩個都要過**，而且**任何一個都不能由另一個推出來**。

## ★★ 「無 llama 進程」≠ 空窗：別的 agent 的 ABBA 有**臂間隙**（2026-09-26 15:0x 實踩）

**事件**：14:58:17 我的窗口門**單次**通過（`lsof :8080` 乾淨、`pgrep llama-*` 空）就啟動四臂 ——
**正好落在別的 session 的兩臂之間**。對方的工作是 `/tmp/armed_fc.sh`（paired MTP on/off；
`/tmp/frag.log`：`14:54:55 target 2 pairs` → **`14:57:41 off: OK rc=0`**，接著跑 on）。
我啟動那一刻它**剛跑完 off、正要跑 on** ⇒ **「沒有進程的瞬間」被讀成「空窗」**。
後果：thermal 1 → **2（HEAVY）**（我在別人實驗中段跑）。**15:00 已中止**（kill 我的 5300/5349）。
owner 是別條線，不是我的紅燈。

**為什麼 `harness` 內建的 thermal gate 救不了**：它是 **fail-OPEN** ——
`thermal_pressure.wait_nominal` 逾時仍 `proceeding`，只把讀數標 `NOT quotable`
（`thermal_pressure.py:225-232`）。⇒ **它把「拿不到窗口」轉成「一個不可引用的數字」。**

**★ 正確的窗口判準（四條，缺一不可）**
1. **時間維度**：要**連續 N 次（≥3，間隔 20s）**都乾淨 —— **單次採樣必然被臂間隙騙**。
2. **thermal 是被動可靠信號**：乾淨的定義**必須**含
   `notifyutil -g com.apple.system.thermalpressurelevel == 0`。別人跑 GPU 一定發熱；
   這是**不需要知道對方腳本名**就能用的第三方證據。
3. **fail-closed**：拿不到 NOMINAL 就 `exit 3` **拒跑**（借鑑 `/tmp/armed_fc.sh` 的設計），
   並寫 **`REFUSED_*` 而不是 `DONE`** —— 否則等待器會把「拒跑」讀成「跑完」。
4. 另加別線已知的布防名（`armed_fc`／`frag_orchestrate`）—— **只當補充，不當主判**（名字會變）。

⚠ 這與本檔 §並行 session 安全 的「檢查與動作必須在同一個分支裡」是同一類錯誤的兩面：
**檢查除了要在同一分支，還要有時間維度。**
產物：`Backup/phase_decomp/cbnmain_nm32/gate.py`（fail-closed 閘）、`driver_attempt1.log`（誤判記錄）。

## ★★ 連跑多臂會累積 swap ⇒ OOM：16 GB 上「一支腳本跑四臂」不可靠（2026-09-26 **兩次**實踩）

**兩次 OOM 的讀數幾乎相同** ⇒ 那不是隨機，是撞硬底：

| 時間 | `min_free` | `wired` | swap 變化 | 結果 |
|---|---:|---:|---|---|
| 15:23 ctl64b | **14.09 MiB** | 12488 MiB | 10668 → 20165 MiB | `rc=-6 ggml_abort` |
| 22:23 nm32b | **14.14 MiB** | 12428 MiB | 4394 → 12607 MiB | `rc=-6 ggml_abort` |

`ggml_abort` 的 stack 兩次都在 **`ggml_metal_synchronize → llama_context::synchronize → test_gen`**。

**機制**：每跑**一臂**（`-p 2048 -n 128 -d 512 -r 3`、pool 8 GiB），**swap 漲 ~8 GB**；
而 **swap 存量不會自動回落**（見本檔「`swap_used` 是錯的軸」——它只是躺著）。
⇒ **四臂連跑 ≈ 累積 +32 GB 的 swap 需求**，第 3~4 臂必然撞硬底。

**規則**
- **16 GB 上不要把「四臂 ABBA」寫成一支腳本連續跑**。要嘛拆兩支（中間 `purge`／重啟），
  要嘛只跑**相鄰一對**（`off → on`，少一臂 = 少一次累積），要嘛先量 swap 增量再決定。
- 解法 `purge` **需要 root**：本機 `sudo -n purge` 回 **`a password is required`** ⇒ **不可自動化**
  （要嘛請 operator 手動 purge，要嘛避開）。
- ⚠ **判定 OOM 的指紋 = `min_free` 貼在 ~14 MiB**（`wired` ~12.4 GB）。
  看到這個組合就別再找別的原因了。
- ⚠ 另一面：**OOM 的那一臂會留下 `[INCOMPLETE]` 的 `bench.json`**
  ⇒ 若 runner 的可續跑判據是「`bench.json` 存在就 SKIP」，**它會把壞數據當成已完成**
  ⇒ **重跑前必須 `rm -rf` 該臂目錄**。

## ★★ allowlist 的「局部可信」陷阱：補鍵前必須掃全檔（2026-09-26 兩次踩）

`scripts/run_server.sh` 的 launch line 走 `env "${SERVER_ENV[@]}"`，是一個 **allowlist** ——
沒列到的 `CGC_*` 被**靜默丟棄**。這是本 repo 最高頻的假陰性來源。已踩過的兩次：

| # | 症狀 | 真相 |
|---|---|---|
| 1 | `CGC_SERVER_EXPERT_CACHE_BYTES` 設了但池沒縮 ⇒ 兩臂其實都是 8 GiB | 要用的鍵是 `CGC_SERVER_` 前綴；無前綴的**被守門靜默忽略**（`:572-575` 有註） |
| 2 | ρ 三鍵「不在 allowlist」⇒ 09-24 的 A/B 只能繞過 launcher | **只有 PROBE/FILL 真的不在**；`CGC_RHO_PREFETCH_MAXQ` 自 09-23 就在（`~:2453`）。我補的時候**把它寫重了**——因為我只看了 `CGC_EB_TIMER` 附近就下判斷 |

**規則（兩條，都要）：**
1. **新增／懷疑任何 `CGC_*` 之前，先掃全檔、不是掃附近**：
   ```sh
   grep -c "SERVER_ENV+=(<KEY>=" scripts/run_server.sh     # 期望 0（要新增）或 1（已存在）
   ```
   這個檔 ~2500 行、**100+ 個獨立 if-block**，**沒有任何一處是「全部」**。
   「找相鄰」不等於「找全部」——與「局部可信被當成整體可信」是同一個錯誤家族。
2. **驗「穿透」與「惰性」都要做**（一次 `CGC_DUMP_ENV=1` resolve，0 GPU）：
   ```sh
   # 惰性：未設時不該多出任何鍵
   CGC_SERVER_PROFILE=<p> CGC_DUMP_ENV=1 bash scripts/run_server.sh 2>&1 | grep -E "^ENV" | grep -c "<KEY>"
   # 穿透：設了要能在 ENV 裡看到
   CGC_SERVER_PROFILE=<p> CGC_DUMP_ENV=1 <KEY>=1 bash scripts/run_server.sh 2>&1 | grep -E "^ENV" | grep "<KEY>"
   ```
   ★ **只驗「穿透」不驗「惰性」是不夠的**：惰性壞掉會讓**所有既有 A/B 悄悄變成同一個配置**。

⚠ **同理：不要用一個已有別用途的鍵當新功能的開關。** 例：G3 想用 `CGC_VERIFY_DECODE` 武裝零槽，
但它**同時**在 `llama-context.cpp:~7084` 開 MTP fast path ⇒ 兩個效果糾纏、無法歸因。
⇒ 新增**專屬**鍵（`CGC_ZERO_SLOT`）並逐一驗它只到達預定的三個 helpers。

★ **一份可直接照抄的「新增一個 env 旋鈕」清單**（本輪做 `CGC_MISS_MASK_COST` / `CGC_ZERO_SLOT` 時成立）：
1. `grep -c` 全檔確認不存在 → 2. 在 `run_server.sh` 加一個 if-block（沿用相鄰註解風格，
含「為什麼需要」與「未設時等價」）→ 3. `bash -n` → 4. 驗惰性（0 命中）→ 5. 驗穿透 →
6. `grep -c "SERVER_ENV+=(<KEY>="` 應為 **1**（不是 2）→ 7. 引擎側確認**未設時逐位元不變**。
