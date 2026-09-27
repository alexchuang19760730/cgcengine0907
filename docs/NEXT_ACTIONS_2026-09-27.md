# NEXT_ACTIONS 2026-09-27：GPU 互斥鎖 + 強制立項（治理），MTP on OOM 根因

日期：2026-09-27（日本新宿）　分支：demo/sweet-spot-windows-fix
狀態：**本文件是所有 agent 開跑前的第一順位通告。規則已在代碼落地，本文規定誰該做什麼。**

---

## 0. 今天為什麼有這份通告（現場）

兩個 harness 幾乎同時跑、互搶記憶體：

| 進程 | 是誰 | charter | 結果 |
|---|---|---|---|
| Freebuff `null_series`（pid 54054，orchestrator `/Applications/Freebuff.app`） | for i=1..4 跑 `harness bench --arm prod-new --charter none` | **none（無立項）** | 第一個拿資源：tg 11.66/11.48/11.61/11.31（穩定）、pp 302.85→258.01 |
| 本線 `off_dense` | prod-new 配對 | 有 | **第二個被擠到 pp 47.03 / tg 3.62**（swap +1053、worst 9212） |

⇒ 兩個獨立問題：**能並發**（窗口門是 ps 快照、無原子鎖，TOCTOU）＋**能未立項**（`--charter none` 後門）。

---

## 1. 已落地的修復（代碼已改、已驗證，**尚未 commit**）

### 1.1 內核級 GPU 互斥鎖 `flock`
- 新增 `harness.gpu_window_lock(...)`（context manager），鎖檔 **`/tmp/flashkv_gpu_window.lock`**，
  `fcntl.LOCK_EX` 排他；非阻塞輪詢、支持 timeout；進程退出（含 kill/崩潰）內核自動釋放。
- 上鎖點（**實際跑 GPU 子進程**的那段）：
  - `harness cmd_bench`（matrix subprocess）
  - `harness cmd_run`（僅 `needs_window` 的 subprocess）
  - `harness cmd_verify`（arm_two_pass subprocess）
  - `commit_bench.py`（matrix subprocess，`import harness` 復用）
  - `ab_interleave.py`（整個配對循環）
  - `abba_p0_3arm.sh`（後台 python 持鎖守護 + `trap EXIT` 殺守護）
- 驗證（獨立程序，非自證）：
  - 持有期間第二個 `LOCK_NB` 被擋 = True；釋放後新獲取成功；拿不到鎖 timeout 如約（1.2s）。
  - **跨入口**：harness 持鎖時 shell/abba 側取鎖被擋；harness 釋放後 shell 側可獲取。
  - `harness selftest` 新增 3 個用例（互斥／釋放／逾時），**SELFTEST PASS**。

### 1.2 關閉 `--charter none` 無聲後門
- `none` 不再豁免：`cmd_bench`／`cmd_run` 見 `--charter none` 直接 **return 2**。
- 急件改顯式 **`--charter waive --waive-reason "理由"`**，理由＋時間記進產物（可審計）。
- argparse（bench/run 兩處）新增 `--waive-reason`，help 移除「none=豁免」。

---

## 2. 各 agent 待辦（按順序）

### P0：commit 本次治理（owner：本線）
1. 跑 `harness selftest`（已 PASS）＋ `window_gate` / `provenance_gate`。
2. 基礎設施 commit 需一張 charter；按規則跑 `commit_bench prod-new`、**標題帶 prefill/decode 成績**。
3. 只提交本輪 hunk（harness.py / commit_bench.py / ab_interleave.py / abba_p0_3arm.sh /
   SPEED_ACCEPTANCE_GATE）；**分離**他線未提交改動（CGC_PREBIND 等），勿連帶。

### P1：MTP on 取得權威成績（owner：本線，核心缺口）
- **根因已定量（見 §3）**：on 構建 prefill graph 峰值 8514.8 MiB（OOM）、off 8348.7 MiB（過），
  差 **166 MiB** 機械對齊第二個 llama_context（MTP draft）。
- 待選方向（**非結論，先驗證再實作**）：
  1. 壓縮 draft 的 166（構造時共享 target KV，`cparams.ctx_other`，約省 ~110，臨界不保證）；
  2. prefill graph build 期讓 draft 不存在（延遲創建，09-18 ret=-1 失敗、接法待重評）；
  3. 減 target graph build 的 +525（最大共同頭，構成未細查）。
- 驗證命令形如：
  `harness bench --arm 'prod-new:CGC_SERVER_MTP=1;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1'`
  `--spec-type draft-mtp --spec-draft-n-max 3 --charter scripts/check/charters/exp-mtp-onoff-standard.yaml`

### P2：MTP on/off 標準成文（owner：本線）
- off（pp ~304 / tg ~12）與 on（待補）結果＋完整設置寫進標準，**目標不得低於此標準**。
- 漂移 ≤5% 驗證：off 跨狀態 tg 11.98 vs 12.02（~0.3%）、Freebuff 四次 tg 散度 1.3%；
  更早見 10.73–11.90（~11%）⇒ **不能預先承諾，需同場 ABBA 測定**（ab_interleave / abba，勿直跑 matrix）。

### 通告（P0 同時做）
- 本文件 = NEXT_ACTIONS 通告；`.workbuddy/memory/2026-09-27.md` 追加一段；
  `docs/SPEED_ACCEPTANCE_GATE_2026-09-26.md` 頂部已加強制更新。

---

## 3. MTP on OOM 根因（100ms 密採樣，定量）

| 階段 | RSS（MiB） | 增量 |
|---|---|---|
| target ctx#1 after-PP | 7829.8 | — |
| draft ctx#2 構造（common_speculative_init，t=0.1–0.4s） | 7864 → 7989 | **+160** |
| target prefill graph build（il=0..39，t=0.56–11.25s，非 40 次實際執行） | 7990 → 平台 8294/8405/**8515** | **+525** |
| il=39 | **8514.8（Metal OOM，status 5）** | — |
| 同環境 off_dense graph build 峰值 | **8348.7（過，log 1561 行）** | — |

⇒ **on − off = 166.1 MiB，機械對齊 draft context 的存在。** 不是 swap 藉口（同時段同機況 off 能過）。
- draft sched compute buffer 僅 3.93 MiB（target 2530.72）⇒ 166 主要是 draft KV 殘量＋第二 Metal backend
  ＋graph build 占用，**內部構成尚未細分**（決定「共享 KV」能省多少）。
- 髒環境可承受點約 **8450–8500 MiB**。

---

## 4. 明確不做 / 已判死（避免重試）

- **pool 固定 8 GiB（8589934592）**：用戶堅持不縮 6GB、不退 app；6GB/縮 pool 方向否決。
- prebind（跨 token 猜，q24=0.354）、MoE NSG sweep、dense GEMV（<3%）、鄰居背景 prefetch、
  3a cap→189/191 高水位 OOM、3b 可泛化 pin（泛化失敗）、gate up/down fuse、分 N 段無甜點。
- 25 t/s 現有 MoE shape 不可達（shape 上限 ~20.5）。

---

## 5. 仍殘留、需另開格子的缺陷（非本輪 owner）

- `llama-context.cpp:3919` rn_mask、`:3923` slot table 靜默 nullptr（leaf 不被舊 in_graph 識別，已定位未修）。
- matrix arm 解析不剝 `!` 前綴（約 llama_bench_matrix.py:758，對 8GB 主線無影響）。
- CGC-STAGE 插樑在構造早期 task_info 返回 rss=0（要細分 draft 內部需先修）。
