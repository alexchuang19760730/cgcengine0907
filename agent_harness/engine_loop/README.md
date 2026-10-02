# engine_loop — 引擎調校迴圈

`agent_harness/` 底下有**兩個**迴圈，形狀相同、獎勵不同：

| | `tb_loop/`（既有） | `engine_loop/`（本目錄） |
|---|---|---|
| 任務 | Terminal-Bench 題目 | 引擎的一個調校假設（「S1 應該 bit-identical」） |
| 環境 | Terminal-Bench 容器 | `prod25` / `prefill250` profile × 臂 |
| 觀測 | 測試 pass/fail | 吞吐、**bit-identical 閘門**、池計數不變量、logits oracle |
| 學習 | 失敗軌跡 → `/refine` → harness 狀態（文本） | 判準數字的方法論 → `CONVENTIONS.md` + `traces/lessons.jsonl` |

兩者的共同點是：**學習發生在 harness 的狀態（文本），不在模型權重**。這是 `tb_loop/README.md`
已驗證過的原則，本目錄直接沿用。

---

## 1. 紅線

**`scripts/check/*` 與 `scripts/run_server.sh` 不得複製進來。**

它們是生產腳本：pre-commit 閘門與技術白皮書引用的是 `scripts/` 那一份
（`knifeedge_matrix.py` 181 KB、`decode_sweep.py` 34 KB、`m123_oracle_gate.py` 31 KB）。
複製一份就等於製造兩份真相，而且會分叉——一個 bug 要修兩處，總有一處被漏。
本目錄只放**指向它們的呼叫**（`index_assets.py` 記錄權威路徑）與**它們產出的 record 的投影**。

## 2. 四階段

```
evaluate  →  extract  →  refine  →  compare
（跑一個臂）  （記錄觀測）  （蒸餾成決策/教訓）（對照前後）
```

| 階段 | 本目錄的產物 | 現況 |
|---|---|---|
| **evaluate** | `index_assets.py` 指到的臂與 profile，由 `scripts/check/decode_sweep.py` 執行 | 已存在（今天就在跑） |
| **extract** | `traces/episodes.jsonl`（T0，純腳本，**已完成**） | ✅ E0 |
| **refine** | `distill/refine_engine.sh` ＋ `distill/collect_evidence.py` ＋ `distill/prompt/refine_engine.md` → `distill/out/<ts>/*.candidate.jsonl`（人審後 `--accept`） | ✅ E2（機制已建並通過離線自測；**未接真實模型跑過**） |
| **compare** | `harness_engine/`（注入用的 harness 狀態）＋ `sft_pi/` `sft_prime/`（兩份投影）＋ `distill/closed_loop.py`（四臂閉環對照） | ✅ E2（投影已產出並可重生、可用 `--check` 驗）；執行器與實驗設計 ✅（`closed_loop_selftest.py` 全過），但**模型半仍未跑** ⇒ **E3 本體未結清**，見 §9 |

### 2.1 E2 新增的四個目錄

```
engine_loop/
├── harness_engine/            prime-agent 的第二份狀態（PLAN §6.1）
│   ├── extensions -> ../../tb_loop/harness/extensions   ← 符號連結，**不是複本**（R1）
│   ├── build_memories.py      lessons.jsonl -> memories/engine/<id>.md（可重生、有 --check）
│   └── memories/engine/       一 lesson 一檔（檔數 = live lesson 數，不寫死）；第一行固定 `[engine] <rule>`（/refine --global 的 scope 標記）
├── sft_pi/                    工具軌跡投影（messages + tool_calls）
│   ├── build_sft_pi.py        episode 序列 + decision.action -> train/valid.jsonl
│   ├── PROVENANCE.json        這批資料是「怎麼被建出來的」：invocation ＋ 每個輸入的 sha256[:16]
│   └── {train,valid}.jsonl
├── sft_prime/                 (證據→教訓) 與 (狀態→下一個動作)
│   ├── build_sft_prime.py
│   ├── PROVENANCE.json        同上（沒有它，`--check` 分不出「滯後」與「換了參數重建」）
│   └── {train,valid}.jsonl
├── distill/                   T1 蒸餾（半自動）
│   ├── collect_evidence.py    可單獨跑：--stats / --next-ids / 證據區塊
│   ├── refine_engine.sh       --dry-run（離線可驗）/ 真跑 / --accept
│   ├── prompt/refine_engine.md
│   ├── selftest.py            假 prime-agent，驗 prompt 的手遞與 --accept 路徑
│   ├── closed_loop.py         四臂閉環對照（A/B = D6 欠帳、C/D = E3）
│   ├── closed_loop_selftest.py  離線自測（含「儀器必須說得出『沒有差異』」的陰性對照；項數自己印）
│   ├── closed_loop_questions.md  8 題 + 每題的承重點
│   ├── action_replay.py       決策排序重放（把 traces 變成「真動作 vs 候選動作」；§10）
│   ├── action_replay_selftest.py  離線自測（含錯長度/同分/長度殘差化三種必須失敗的對照）
│   ├── expert_replay.py       ⛔ **已退役**：專家 id 預測重放（曾把 CGC-IDS 軌跡變成「歷史 -> 下一層的 id」；§11）
│   └── expert_replay_selftest.py  ⛔ 同上（地板／殘差化自檢／洩漏哨兵／協定拒絕／**退役告示**，61 項）
└── sft_common.py              兩份投影共用的載入／渲染／切分（**只有一份渲染器**）
```

「能推導就不要維護」在這四個目錄上是貫徹的：`memories/engine/` 由 `lessons.jsonl` 產生、
兩份 SFT 由 `traces/*.jsonl` 產生、`extensions/` 是指向而不是複本。**手改衍生物會被下一次
重生蓋掉，而那次重生看起來完全正常。**

### 2.2 E3 的執行器怎麼用（`distill/closed_loop.py`）

```sh
python3 agent_harness/engine_loop/distill/closed_loop.py --dry-run    # 不需要模型
python3 agent_harness/engine_loop/distill/closed_loop_selftest.py     # 33 項離線自測

# 真跑。需要一個 OpenAI 兼容端點；「用哪個模型」是實驗的一部分，所以腳本不給預設值。
CLOSED_LOOP_MODEL_CMD='llm -m <model>' \
  python3 agent_harness/engine_loop/distill/closed_loop.py --memories-scope first:8
```

| 參數 | 意思 |
|---|---|
| `--memories-scope all`（預設） | 注入全部 live lesson |
| `--memories-scope first:8` | `lessons.jsonl` 的**前 8 筆**（PLAN §6.3 的「那 8 條」） |
| `--memories-scope class:mh` | 單一 class（縮寫或全名） |
| `--memories-scope none` | 空。**陰性對照**：C 與 D 的 prompt 必須位元組相同 |
| `--memories-scope ids:<path>` | 明確清單，一行一個 id，`#` 可註解 |
| `--reps N`（預設 3） | 每個（題, 臂）呼叫幾次。**1 次證明不了「可重現」**，而 §9 的驗收要求的是後者 |
| `--only Q3` | 只跑一題 |

產物在 `distill/closed_loop_out/<ts>/`：

- `answers.jsonl` —— 每次呼叫一筆
- `compare.json` —— 每題的**臂內一致性** ＋ 跨臂判斷
- `manifest.json` —— 注入了哪些 id、每個 rep 的臂順序、charter 與 memories 的 sha256。
  **model cmd 只記 sha256、不記原文**：manifest 會被 commit，而命令列可能帶著 API key。

**讀 `compare.json` 的先後是固定的**：先看 `comparable`，再看跨臂欄位。`comparable: false`
（某個臂自己前後不一致）的題目，其跨臂判斷一律無效。而**「不同」不等於「改善」**——
承重點在 `closed_loop_questions.md` 的表裡，要人工核對。

## 3. 三種 record

唯一的訓練數據出口是 `traces/*.jsonl`，一行一 record，只有三種類型。權威定義在
`traces/schema/*.schema.json`，說明在 `../CONVENTIONS.md` 與 `../PLAN_ENGINE_LOOP_2026-09-15.md` §4。

| 類型 | 是什麼 | 為什麼值錢 |
|---|---|---|
| `episode` | 一次 run、一個臂：build 指紋 + 量測 + artifact | 觀測本身。**不含任何詮釋** |
| `decision` | 一個判斷點：問題 + 證據 + 推理 + **排除掉什麼** | 最高價值。`ruled_out` 是負面知識，最常被重用 |
| `lesson` | 可重用的規訓（= harness 狀態） | 換一個問題也適用，能注入回 agent |

### 三條硬規則（`traces/validate.py` 強制）

1. **`build == null ⇒ usable_as_evidence == false`。** 不知道二進位檔的數字不能比——不是「比較不準」，
   是「不可比」。E0 匯出的 100 筆裡只有 13 筆有指紋，因為 `decode_sweep.py` 直到今天才開始記錄它。
2. **有生成（`decode_tps.median` 是數字）⇒ `answer_md5` 不得為空。** 否則「答案穩定」是空真。
3. **`judgement == refuted ⇒ superseded_by` 非空。** 被推翻的假設不得當正例訓練
   （`PLAN §10 R3`：雙緩衝 remap 那個假設就是這樣被擋下來的）。

驗證器的自我證明：`traces/selftest.py` 注入 8 種違規，8/8 必須被拒。**它現在還沒被證明會擋錯之前，
不算閘門。**

## 4. 用法

```bash
cd <repo>

# 盤點：哪些資產存在、角色是什麼、能不能重放（純讀，安全）
python3 agent_harness/engine_loop/index_assets.py
python3 agent_harness/engine_loop/index_assets.py --check     # 檢查 manifest 是否 drift

# 匯出 + 驗證（純讀檔，不啟動引擎）
python3 agent_harness/engine_loop/traces/emit_episodes.py --stats           # 只看盤點，不寫檔
python3 agent_harness/engine_loop/traces/emit_episodes.py                   # 寫 episodes.jsonl 並驗證
python3 agent_harness/engine_loop/traces/validate.py                        # 三種 record 一起驗
python3 agent_harness/engine_loop/traces/selftest.py                        # 證明驗證器會擋錯
```

`emit_episodes.py` **從不啟動引擎**。它只讀 `Backup/**` 已經寫好的 JSON 與 log。
一旦它開始跑引擎，它就變成第二份會分叉的 harness。

## 5. 資料源（E0 已接的）

| 來源 | 產生者 | record 種類 |
|---|---|---|
| `Backup/phase_decomp/*.json` | `decode_sweep.py` / `ab_interleave.py` | `episode` (sweep / interleave) |
| `Backup/llama_bench/matrix_*.json` | `llama_bench_matrix.py` | `episode` (bench) |
| `Backup/knifeedge_matrix/capinv_*.json` | `knifeedge_matrix.py` | `episode` (gate，**有 verdict**） |
| `Backup/m123_oracle_gate/cap_*.json` | `m123_oracle_gate.py` | `episode` (gate，僅 provenance) |
| `Backup/knifeedge_matrix/*.jsonl` | oracle dump | `episode` (oracle) |

**未接（刻意）**：`Backup/cgc_logs/*` 原文（463 MB / ~1400 檔，單檔 > 32 MiB 只記路徑與大小，
不複製）、`harness_engine/logs/`、`sessions/`。log 只被**摘要**成 episode 的 `obs.phase_ms` /
`obs.asserts`，原文留在原地。

## 6. 體量與去隱私

- `episodes.jsonl` 100 筆約 220 KB。log 原文不進 repo。
- 絕對路徑 → `$REPO` / `$HOME`；`192.168.x.x` → `$LAN_IP`；`sk-*` → `$API_KEY`。
  `run_server.sh` 會印含區網 IP 的「連線卡」，oracle 的 `.cap` 內嵌完整 argv（含絕對模型路徑）
  —— 兩者都必須被 `sanitize()` 擋掉，而這兩者都會進 record。

## 7. 已知的誠實邊界

- **E0 的 122 筆裡只有 27 筆 `usable_as_evidence == true`（22%）。** 這不是匯出失敗，是今天之前的資料
  沒有 build 指紋、`n_rounds` 不足 3、或臂本身是探針。門檻是刻意設在那裡的。
  注意這個欄位擋的是**引用數字**，不是「能不能被引用」：層梯二分最關鍵的那一列
  （全臂、跨輪不穩定）正是 `usable_as_evidence == false`，而 `decisions.jsonl` 照樣用它。
- **`tb_loop` 的三個入口腳本曾經跑不起來——E1 已修（套件解析層面）。**
  原本 `import tb_loop` → `ModuleNotFoundError`：三支入口都寫 `--agent-import-path "tb_loop.agents.…"`，
  而 `PYTHONPATH` 指向 `agent_harness/` 自己（那時它就是 `tb_loop`），所以解譯器看不到名為
  `tb_loop` 的套件。E1 把資產搬進 `agent_harness/tb_loop/`，並讓 `PYTHONPATH` 指向其**父目錄**，
  import 字串一行未改。**已驗證**：`tb_loop` 現在是**正規套件**（`__init__.py`），
  `tb_loop.agents.{prime_agent_adapter,codebuff_api_agent,loopmoe_agent_adapter}` 與
  `tb_loop.learning.attribution` 四個模組都解析到搬遷後的位置；對照組
  （`PYTHONPATH=agent_harness/tb_loop`）仍正確地報 `No module named 'tb_loop'`。
  **仍未驗的**：`tb run --n-tasks 1` 的 smoke 沒跑——`tb_loop/.venv` 不存在、`terminal-bench`
  未安裝，且 Docker daemon 未執行。**`bash -n` 抓不到套件解析問題**，這一條靠真的 `import` 才成立。
  詳見 `docs/AGENT_HARNESS_E1_RESTRUCTURE_20260916.html`。
- **S1 的 bit-identical 閘門尚未通過。** 值層面的解釋已全部排除，但今天的兩個決定性配對把問題**重新定型**：
  - 層梯二分：`min_il=20`（20 層、約 80 個多出節點）與基準**逐位元相同且穩定**；`min_il=18/14/10/6`
    **穩定地不同**；`min_il=2` 與全臂**跨輪不穩定**（3 個值）。
  - `p25-keepleaf`（全部 S1 節點都建、`mul_mat_id` 消費 host leaf，**分段**）：**逐位元相同且穩定**。
  - `p25-slotgpu-dbg`（**真臂**，沒有 leaf）：`EQUIV` 2067 次全部 `mismatch=0`，且 `EXPECT` vs
    `POST` 在 39 層 × 6 個 build 上每一行都一致。⇒ **真臂消費的 id 就是 host 想要的那一份。**
  - `p25-buildleaf`（照 `llama-context.h` 註解做）**跑不起來**：無消費者的 graph root 拿不到
    backend，`ggml-alloc.c:623` 斷言。⇒ 那條路要劃掉。
  - **`p25-slotgpu-noasync`（閘門修好後真非分段）= `{ff68c5a2}` 穩定**，而同一 sweep 的基準
    `p25-gputime-noasync` = `{dc055e63}` 三個量全同 ⇒ **dispatcher 對基準是數值中性的**（分段關閉的
    代價是 ~10×：6.95 → 0.72 t/s，這是 S2/S3 的獎金大小）。時序假說**被否決**，但失敗的**形狀**
    變了：分段下不穩定、非分段下穩定但錯 ⇒ 不確定性來自分段 dispatcher，底下有一個確定性缺陷。
  - **`p25-keepleaf-noasync` = `{8c870183}` 穩定 ≠ 基準** ⇒ **「節點是惰性的」只成立於分段
    dispatcher**。同一個控制臂、同一個節點集合、同樣消費 leaf，只換 dispatcher 就換了答案，
    而且**從第一個 token 就是另一種回答模式**（不是最後位元），池計數同時 +16%/+18%。
    ⇒ 這個調查的控制臂本身對受測變數敏感；§9.9 的「節點不是原因」要加註條件，
    「少一個 pinned 節點造成佈局差異」這條候選失去結構前提。
  - 剩下的唯一候選是「**消費的那一刻**那個 buffer 裡是什麼」。三個現有儀器都答不了
    （POST 是 sync 之後讀、`id_oob` 是 encode 期主機讀裝置 buffer、`EQUIV` 只比兩個主機實作），
    它們的共同病是「量的東西與宣稱量的東西不同一個」，見
    `docs/REMAP_ROUNDTRIP_REMOVAL_PLAN_2026-09-15.md` §9.13/§9.14 與 `traces/decisions.jsonl`
    `dec-20260915-2136` / `-2138` / `-2158` / `-2202`。
- **建置指紋漏了 `libggml-base`（已修）**。`build_fingerprint()` 只雜湊三個檔案
  （`llama-server` + `libggml-metal` + `libllama`），而 `ggml-backend.cpp`——排程器與 `CGC_OA_ASYNC`
  閘門——編進 **`libggml-base`**。後果是 21:34（閘門未修）與 21:41（閘門已修）兩次跑帶**完全相同**的
  指紋，被標成可比較。現改為 glob `libggml*.dylib` / `libllama*.dylib`（8 鍵，鍵名不含版本號），
  `ab_interleave.py` 改為直接委派同一份實作。**v1（3 鍵）的歷史列在這一維上不可比**——這正是要揭露的。
  教訓：`eng-mh-0007`。
- **一個「被設了但值被忽略」的閘門已修**：`ggml/src/ggml-backend.cpp:1745` 原本用
  `getenv("CGC_OA_ASYNC") != nullptr` 選分段 dispatch，所以 `CGC_OA_ASYNC=0` 選到的仍是分段分支
  （而 `run_server.sh` 無條件設定它，連「不設」都做不到）。修成 value-aware 後，prod25 設 `"1"`，
  錨定 digest `dc055e63` 不變。
- **同上，閘門修好只保住 prod25，卻把另外四個 profile 靜默改道（已修）**。乾跑實測修正前
  `off` / `prefill250` / `qa-zh` / `longform-zh` 都是 `CGC_OA_ASYNC=0`——而在修正前那個 `0` 因為閘門
  讀存在而**從未生效**（一直是分段）；修正後它第一次真的生效，等於把這些 profile 從 6.95 降到
  0.72 t/s。`prefill250` 是 oracle 閘門與 bench matrix 的口徑，所以影響落在驗證鏈上。
  `run_server.sh:109` 的預設已回到 `1`（= 它們**實際**在做的事），顯式 `0` 仍可用。
  教訓：`eng-gate-0006`（**錨 digest 不變 ≠ 行為不變**）。

## 8. 專案記憶（`memory/`）

`.workbuddy/memory/` 是這個 repo 資訊密度最高的紀錄，也是最少被用的：當日日誌到 1000+ 行，
整檔讀取會靜默截斷，而截斷後的「沒查到」與「沒寫過」同形。

`memory/` **只放衍生物**，原檔留在 host 寫入的位置（複製等於製造第二份真相，見 `CONVENTIONS.md` D6
與它 **2026-09-16 的修訂**——那個修訂沒有放寬這一條，只是另外承認了一份非權威的 dated 快照
`agent_harness/memory/`，用途僅限跨機器搬運）：

```bash
python3 agent_harness/engine_loop/memory/build_memory_index.py            # 重建 INDEX.jsonl
python3 agent_harness/engine_loop/memory/build_memory_index.py --check    # drift 則 exit 1
python3 agent_harness/engine_loop/memory/build_memory_index.py --query mmid -n 8
```

`--query` 回報 `path:line_start-line_end` 與該節的 `###` 子標題，所以下一步是精準讀取
（`Read(offset=line_start)`）。record 的 `action` / `evidence` 要引 `path:line_start`，不要複述內容。

---

## 9. E2 的驗收與兩件**沒有結清**的事（2026-09-16）

### 9.1 已建並通過離線驗證

| 產物 | 驗證方式 | 結果 |
|---|---|---|
| `harness_engine/memories/engine/` | `build_memories.py --check` | 無漂移；第一行都是 `[engine] <rule>` |
| `harness_engine/extensions` | 符號連結解析 ＋ `--check` 會擋「變成真目錄」 | 指向 `tb_loop/harness/extensions`（單一複本） |
| `sft_pi/` | `build_sft_pi.py --check` | 重生 == 磁碟；每筆帶 `_provenance.reconstructed` |
| `sft_prime/` | `build_sft_prime.py --check` | 同上；排除 refuted decision 與 superseded lesson |
| `distill/` | `distill/selftest.py`（假 prime-agent） | 31 項檢查全過 |
| `distill/closed_loop.py` | `closed_loop_selftest.py` ＋ `--dry-run`（不需模型） | 41 項全過；四臂的 prompt 大小／sha256／逐對 diff |
| `distill/expert_replay.py` ⛔ | `expert_replay_selftest.py` | 61 項全過；地板／殘差化自檢／洩漏哨兵／協定拒絕／**退役告示每次都印** |

**這一表原本每一列都帶一個計數（「106 檔」「141 筆」「14 條軌跡」），全部拿掉了。** 那些數字隨
`traces/*.jsonl` 增長，寫死在這裡就會變成假話，而當時**沒有任何東西會發現**：`index_assets.py
--check` 只報它管得到的資產，而這四個目錄一筆都不在它的 80 筆裡 —— **綠燈與「沒被檢查」在這裡
長得一樣**。實測它們在一天內全部過期（106→132 檔、141→166 筆），而所有既有閘門都是綠的。
要數字就問產它的那一支：`build_memories.py --check` 報檔數、兩個 `build_sft_*.py --check` 報筆數、
`distill/*selftest.py` 報自己的項數。**能推導的就不要維護。**

`distill/selftest.py` 抓到一個**真的 prompt 設計缺陷**並留下回歸斷言：v1 的 prompt 把
「下一個可用 id」放在最末，而證據區先列了 106 個**已在使用**的 id ⇒ 從文件中第一個看到的
id 取用就會撞號（`validate.py` 報 `duplicate lesson_id`）。修法是把可用 id 用哨兵包起來
並明文禁止重用，**並且把「文件中第一個 id ≠ 可用 id」變成一條斷言**——否則這個缺陷會在下一次
改 prompt 時無聲復發。

### 9.2 **閉環對照的模型半沒有跑**（D6 的欠帳仍然開著）

`CONVENTIONS.md` §E 要求「每次改 `CONVENTIONS.md` 都要走一次閉環對照」，
而 D6 的 2026-09-16 修訂聲稱「改變不到任何執行時行為」。那句話**目前仍然沒有產物支撐**。

- **執行器已建**：`distill/closed_loop.py`，四臂（A_preD6 / B_postD6 / C_head / D_head_mem），
  只有 A→B 回答欠帳，另兩對是反歸因用的。`--dry-run` 已證實三對各自只隔離一件事
  （A→B ＋18/−0 行、B→C ＋18/−4 行、C→D 章程 0 行／memories ＋23,785 B）。
  **第一版把 `e688f346e^` 直接對上 HEAD，結果那一對同時量了 D6 與後來的 E1 D1 更新——
  是 `--dry-run` 的逐對 diff 把它抓出來的。**
- **模型半未跑**，原因不是「做不到」，而是兩個必須先解決的條件：
  1. 本機沒有可用的模型端點（1234/1235/8080/11434/8000/5000/9000 全部無回應，
     `~/.workbuddy/models.json` 是空的）。
  2. `llama-server` 與本 repo 的 13.6 GB 模型**都在**，所以技術上可以起一個。
     **但那會與另一個 session 正在進行的 prefill/decode 量測互相干擾**——
     那些量測對 GPU 時脈與記憶體狀態極敏感（見 A16 的 τ 與熱暫態），
     起一個 13.6 GB 的 server 去回答 32 個問題，代價可能是對方整輪的數字作廢。
     **「為了結清一條欠帳而弄壞另一條線的資料」不是結清。**
- **要跑它的指令**（在沒有併行量測的時間窗）：
  ```sh
  CLOSED_LOOP_MODEL_CMD='<讀 stdin 寫 stdout 的任何模型命令>' \
    python3 agent_harness/engine_loop/distill/closed_loop.py
  ```
  然後**人工核對** `closed_loop_questions.md` 的承重點表格——字串相同只是自動部分。
- **不得宣稱 D6 修訂已結清**（PLAN §9 的 E2 欄原文即如此要求）。

---

## 10. 任何排序器要對決策有投票權之前（`distill/action_replay.py`，2026-09-28）

### 10.1 為什麼是「排序」而不是「生成」

CLM（兩塔對比模型）**不會生成**。所以「讓 CLM 參與決策」不能做成「叫它寫下一個決策」——那是
生成，它做不到，`closed_loop.py` 那一半（生成式 prime-agent）也不該被它取代。它能做、而且只有
它能做的那件事是：**把已經寫出來的候選動作排序**。

於是問題換成儀器問題：在既有的決策紀錄上，把真動作與一堆候選動作混在一起，一個純排序器能不能
把真的排到第一？`action_replay.py` 把它變成可執行的——它**離線、不呼叫模型、不啟動引擎**
（除非你顯式給 `--scorer command`），所以「要不要為 CLM 花掉這台 16 GB 機器的記憶體」可以先被
回答一次，再決定要不要付那筆錢。順序反過來才是錯的。

資料不必另外標：`decisions.jsonl` 每一筆本來就是一個三元組 —— `question` + `evidence[].reading`
是狀態、`action` 是當時採納的動作、`ruled_out[].claim` 是**它自己明文否決過**的動作。

```bash
python3 agent_harness/engine_loop/distill/action_replay.py                     # 內建階梯
python3 agent_harness/engine_loop/distill/action_replay.py --state-ruled-out strip
python3 agent_harness/engine_loop/distill/action_replay_selftest.py            # 項數自己印
```

### 10.2 兩個池子，**不可平均**

| 池 | 問的是什麼 | 2026-09-28 的讀數（`lexical` 基線，`--split all`） |
|---|---|---|
| **sibling**（真動作 vs 別的決策的動作） | 一般排序能力。池大、負例隨機性高 | AUC 0.927；拔掉長度 0.900 |
| **rejected**（真動作 vs **這一題自己否決的動作**） | **唯一有資格當閘門的那一池** | keep 0.371 / strip 0.814；拔掉長度 0.546 → **0.711** |

混在一起報一個平均，等於讓容易的那池替難的那池背書。

### 10.3 這支量出來的三件事（都不是猜的）

1. **長度混淆是結構性的。** `ruled_out[].claim` 是一句被否決的短句、`action` 是一段執行敘述，
   **97/97 對都是正例較長**（中位 +367 字，最短 +20）。所以在原始分數上連 `len()` 都能在
   rejected 池拿到 AUC = 1.000 ⇒ **raw 的 rejected 讀數單獨沒有資訊**。因此每個排序器同時報
   `|len`（題內候選池上對長度做最小平方回歸取殘差）。`length` 對照的 `|len` 回到 **0.500** ——
   那正好是這個調整器自己的自檢。
2. **現行的狀態渲染會把排序器的判別力吃掉。** 同一個 `lexical`，rejected 池的 `|len` 由
   keep 的 **0.546** 變成 strip 的 **0.711**：`render_state_for_decision()` 把
   `ALREADY RULED OUT (do not re-walk these): claim / why false` 印在 prompt 裡，那對**生成式**
   agent 是必要的指令，對相似度排序器則是把否決項的字詞灌進查詢。⇒ **CLM 要接的話，狀態編碼
   不能直接吃現行那一段**；否決項應該當對比目標的負例，而不是查詢的一部份。
3. **n 小到什麼程度。** 66 筆決策（68 扣掉 2 筆 `judgement=refuted`），top-1 的 95% 半寬 ≈
   **±12 個百分點**。差不到 12 點不要說誰贏；被否決的配對有 97 對，比 top-1 有力。

### 10.4 CLM 進來的地方，與還沒有排序器通過的那道門

```bash
python3 agent_harness/engine_loop/distill/action_replay.py --scorer command \
    --scorer-command '<讀 stdin 寫 stdout 的 CLM 轉接器>' --split valid --report
```

協定在 `score_command()` 的 docstring（stdin `{query, candidates[]}`、stdout `{scores: [...]}`）。
**錯長度的回覆一律 SystemExit**，不截斷也不補零——一個默默回錯長度的評分器會讓所有名次看起來
合理，而那是這條線唯一不能接受的失敗模式。

通過條件（**目前沒有任何一支排序器通過**）：`AUC_rej|len` 要比 `length` 對照高出 0.05 以上，
**而且** top-1 的差距要大於它自己的 95% 半寬。

兩個必須一起讀的欄位：

- `--split valid` 是與 `sft_prime` 相同的切分（同一個 `sft_common.split`、同 seed）。
  **任何在 traces 上訓練過的東西，用 `all` 報出來的數字都不是評估**（工具會在 `all` 時印警告）。
- `--verify-against-sft`（預設開）把重建的 (狀態, 動作) 逐筆對回 `sft_prime/{train,valid}.jsonl`：
  被排序的字串必須與被訓練的字串**同一個位元組**，否則「只有一份渲染器」只是聲明。

`--report` 寫 `distill/action_replay_out/<ts>/{manifest.json,replay.json}`；manifest 記 traces 的
sha256 與 scorer 命令的 sha256，**不記命令原文**（沿用 §2.2 的同一條規則：manifest 會被 commit，
而命令列可能帶著 API key）。

---

## 11. ⛔ 退役：任何系統一模型要對專家預測有投票權之前（`distill/expert_replay.py`，2026-09-28）

> **這一節讀作「追跡紀錄」，不是「待辦」。** 工具與指標都已在同一天退役：它量的是**排序準確度**，
> 而 §12 在生產路徑上把那個量證明為**沒有消費者** —— 可避免的非駐留帳戶只有 1.00%、可支付的
> 阻塞窗口 ≲0.5% 的 decode、79% 的缺口是 compulsory（原理上不可預測）。這條路徑的值函數是
> **臨界路徑上的阻塞 µs**，那個量要用**成本端**模擬（`Backup/thrash_sim_20260919.py` 的
> residency/miss 模型）來量，**不是**用 precision@k。
>
> **教訓：先指名這個數字的消費者是誰、他的值函數用什麼單位，再造尺。**
> 這支腳本自己的 docstring 當初就寫著「順序反過來才是錯的：先落地模型、再想辦法證明它有用」——
> 而作者在同一支腳本上、上一層重犯了那個錯誤：先造計分板，才去問那個分數有沒有價值。
> 保留本節與本檔的唯一理由，是這裡的每個讀數要有可重現的出處。

§10 量的是「**寫下一個決策**」那一側。這一節量的是另一側，而它是**已經在生產路徑上跑**的一側：
引擎自己就有四個專家 id 預測器（prerouter／ρ-fill／draft-prefetch／SpAc），它們的成績是被自己的
計數器量過的。所以 JEv、CLM-8B 或任何外部模型進場時，第一個要回答的不是「它聰明嗎」，是

> 在**我們自己的**路由資料上，它的 precision@k 有沒有超過引擎既有的頻率／EMA 預測器？

`expert_replay.py` 把 `CGC-IDS:` 路由軌跡變成那道題：每一題 = 某一層某一步，狀態 = **嚴格早於該步**
的路由歷史，答案 = 該步實際被選中的集合（`ntok × top_k` 的聯集）。它離線、不呼叫模型、不啟動引擎。

```bash
python3 agent_harness/engine_loop/distill/expert_replay.py --report --per-layer
python3 agent_harness/engine_loop/distill/expert_replay.py --scorer command \
    --scorer-command 'python3 my_jev_adapter.py'          # 協定 = §10.4 的同一個，一個 adapter 兩邊都能接
python3 agent_harness/engine_loop/distill/expert_replay_selftest.py   # 48 項
```

### 11.1 第一次跑就把一條死路翻過來（2026-09-19 軌跡，193 步 × 40 層，7680 題）

| 排序器 | precision@8 | AUC_all | AUC_hard | AUC_hard\|freq |
|---|---|---|---|---|
| `random`（地板） | 0.073 | 0.501 | 0.501 | 0.501 |
| `freq` ＝ **prerouter 複刻** | **0.705** | 0.905 | 0.810 | **0.500**（調整器自檢） |
| `ema` ＝ SpAc 複刻 | 0.704 | 0.840 | 0.796 | 0.644 |
| `lag` ＝ 前一步（draft/prev-token 家族） | 0.566 | 0.768 | 0.741 | 0.628 |
| `static`（離線表，**用了未來**） | 0.724 | 0.923 | 0.847 | 0.614 |

### 11.1b 第二條軌跡（2026-09-20，354 步 × 40 層，14120 題）—— **贏家會換人**

| 排序器 | precision@8 | AUC_hard | AUC_hard\|freq | 對照 |
|---|---|---|---|---|
| `random` | 0.084（− chance 0.082） | 0.500 | 0.500 | 地板吻合 |
| `freq` | **0.499** | 0.769 | 0.500 | 09-19 是 0.705 |
| `ema` | **0.617** | 0.776 | 0.678 | 09-19 是 0.704 |
| `lag` | 0.493 | 0.710 | 0.608 | — |
| `static`（離線表） | 0.508 | 0.790 | 0.552 | **比 ema 還差** |

兩條軌跡的標題結論一致（都是 0.5~0.7 量級，不是引擎計數器的 0.04），但**可部署的贏家不一樣**：
09-19 是 `freq` 與 `ema` 打平（0.705 / 0.704），09-20 是 `ema` 贏 `freq` 一大截（0.617 / 0.499）。
這是可解釋的 —— 軌跡越長，生成越會漂離前段累積的分布，累積計數表就越落後於近期性。
**⇒ 「哪一支比較好」不是常數，是軌跡長度／漂移的函數；任何只在一條軌跡上做的比較都不能當結論。**
副產品：09-20 上 `static`（一個離線擬合、部署時凍結的表）**比純近期性還差** ⇒
「把排序結果預算成靜態表」不是自動有價值的設計，它得先在這種重放上證明它贏得過 `ema`。

**引擎自己的 `CGC-PREROUTER` 在同一條規則上報的是 3.8%–4.2%**（`llama_server_20260917_*.log`）——
**差 17 倍**，而差別只有一個：`freq` 表**由誰餵**。引擎只有兩個寫入點，
`llama-context.cpp:6701`（**stock，在大型前綴分支裡**，`n_tokens > pmax`）與 `:~7019`
（解碼/pool 路徑，**被 `LLAMA_EXPERT_CACHE_ROUTE_RECORD` opt-in，預設關**，它自己的註解就寫著
「stock 那個只在前綴觸發 … 這就是 freq 空掉的原因」）。⇒ 那四個數字量的是「前綴餵的表用在解碼上」，
**不是這條規則的能力**。判它死路所依據的那個讀數，在它自己那條路徑上不成立。

### 11.2 兩個讀數修正（都是第一次跑就被自檢逼出來的）

1. **chance floor 是 `mean(demand)/256`，不是 `k/256`。** 均勻隨機排序取 top-k 時
   `E[hit] = k·P/N` ⇒ precision = P/N，**與 k 無關**。這條軌跡的平均 demand 是 18.6/256 ⇒ 地板是
   **7.3%**，不是 3.1%。所以引擎那四個 3.8%/4.2% 連「明顯高於地板」都不成立。自檢斷言：
   `random` 的 precision 必須等於 `mean(demand)/256`（實測 0.0732 vs 0.0727）。
2. **`precision@k` 被 demand 封頂**：`|demand| < k` 的步最多只能拿 `|demand|/k`。
   同一條軌跡的 demand union 中位是 18、**最大 32**（ntok=4 × top_k=8），所以這裡不封頂，
   但換一份 ntok 較小的軌跡就會，而那個時候表會整排偏低而看起來像「大家都變差了」。

### 11.3 不可預測的那一塊，是從軌跡算出來的，不是引用的

compulsory（該層**首次**出現的 id）**4812/142965 = 3.37%** ⇒ 任何排序器的 recall 上限是 96.63%。
這與 `portal/targets.json` 的 `one_step_lag_cost`（143 槽時 compulsory 3.79%）同一個家族，
但**是這支自己算的**：換一條軌跡就換一個數，而它會直接限制 recall 的天花板。

### 11.4 判讀規則，與還沒被證明的事

- **`|freq` 才是那一欄。** hard 池（正例 vs 這一層被路由過、但這步沒選的 id）才有判別力；
  all 池有一大塊「歷史上從未出現」的負例是白送的。而 hard 池的分數若來自頻率，那就是它自己的
  共變數 ⇒ 一律報殘差化後的版本。`freq` 自己的 `AUC_hard|freq` **必須正好 0.500**（自檢），
  而 oracle 殘差化後**必須仍然 1.000**（調整器不得削掉真訊號）。兩條都在自測裡。
- 通過條件（暫定）：`AUC_hard|freq` 比 `freq` 基線高 **+0.05** 以上。**目前沒有任何外部模型跑過。**
- **沒有證明的事**：這是一條軌跡**內部**的排序能力，案例高度自相關（相鄰步需求重疊 ~70%），
  所以 `±` 那個區間是假設獨立算的、**低估變異**，不是「顯著與否」的開關。要泛化就換一份軌跡重跑，
  並比較兩者的 compulsory 比例。另外 `ema` 只重建得出視窗內的近期性（狀態只帶最近幾步的原始集合），
  比 `freq` 用的狀態弱 —— 兩者的差別混了「衰減」與「可見歷史長度」，要分開得 dump 引擎的 `u`。

---

## 12. 生產路徑實測：那個 4% 不是成績，是「沒有預測」（2026-09-28，build 630，**零重建**）

§11 是離線重放。這一節是把它放回**生產啟動器**上驗：同一台機器、同一個 binary（`libllama.0.dylib
→ 0.0.630`）、同一份 prompt（126 token）、同樣兩個 `max_tokens=192` 的請求，**只差一個環境變數**。

```bash
# arm A（今天的行為）
CGC_PREROUTER=1 CGC_PREROUTER_TOP_K=8 CGC_IDS_MAX_LINES=20000 ./scripts/run_server.sh --detach
# arm B（唯一的差別）
LLAMA_EXPERT_CACHE_ROUTE_RECORD=1 CGC_PREROUTER=1 CGC_PREROUTER_TOP_K=8 \
    CGC_IDS_MAX_LINES=20000 ./scripts/run_server.sh --detach
```

| | arm A（今天） | arm B（＋`ROUTE_RECORD`） |
|---|---|---|
| log | `Backup/cgc_logs/llama_server_20260928_121510.log` | `…_121709.log` |
| `CGC-PREROUTER` | `calls=7480 queued=0 nodata=0 **scored=0 pred_total=0 hit=0 (precision 0.0%)**` | `calls=7480 queued=15 nodata=0 **scored=7405 pred_total=59240 hit=26929 (precision 45.5%)**` |
| 同一條軌跡的離線 `freq` 重放 | 0.456 | 0.455 |
| compulsory（該層首次出現） | 4.07% | 4.07% |
| decode（**不可引用**，見下） | 11.9 / 14.1 t/s | 10.1 / 10.9 t/s |

### 12.1 arm A 的 0.0% 是**除以零**，而且它長得跟「很爛」一模一樣

`scored=0` ⇒ **預測一次都沒做過**。機制：`freq` 表全空 ⇒ `llama_expert_cache_prerouter_predict`
的 `ranked` 是空的 ⇒ `pred` 空 ⇒ 下一層的 `prerouter_score` 在 `pred.empty()` 就早退。

而唯一能示警的計數器 `n_prerouter_nodata` 印的是 **`nodata=0`**：它測的是
`cache->freq[layer].empty()`，而那是「這個 vector 有沒有配到 256 格」，不是「有沒有被填過」——
**一個沒有任何資料的 run 與一個預測很爛的 run，印出同一行。** 這是 `eng-mh-0009`
（「算了卻沒被觀測」）家族在計數器層的第 N 例，而且是最貴的一種：它把「沒跑過」包裝成「跑了、很爛」。

### 12.2 為什麼表會空，以及 09-17 那四個讀數的來歷

`[arm] slab OFF (profile=off)`：生產 profile 下前綴走 **pool 路徑**，而 stock `record_routes`
（`llama-context.cpp:6701`）座落在**標準/slab 前綴路徑**裡（那一段後面緊接著 `if (!cgc_prefill_stream) return;`）
⇒ 這條路徑上**沒有任何東西會寫 `freq`**。程式碼位置與實測（arm A 的 `scored=0`）兩邊一致。
所以 09-17 的 12.8% / 6.1% / 3.8% / 4.2% 是**另一個配置**下的數字，不是今天生產配置的數字——
而它在今天的配置下根本不是「低」，是「不存在」。

### 12.3 打開 `ROUTE_RECORD` ⇒ 45.5%，而離線工具對得上

`LLAMA_EXPERT_CACHE_ROUTE_RECORD=1` 讓解碼/pool 路徑也寫同一份計數 ⇒ `scored=7405`、
`precision 45.5%`。**同一條軌跡用 `expert_replay.py` 的 `freq` 重放是 0.455**（另一臂 0.456）：
差 **0.05 個百分點** ⇒ 兩個儀器量的是同一件事（計數口徑已對齊成 `record_routes` 的逐出現次數，
見 §11 的 `--count-mode`）。這也是為什麼 §11 的離線讀數可以拿來當這條路徑的預測值。

### 12.4 精確度 ≠ 用處（45.5% 是真的，但幾乎沒有可下手的空間）

`queued=15` 對 `pred_total=59240` ⇒ **99.97% 的預測「已經在池裡」**（179 槽/layer；`prewarm_hot`
在第一步之前就用**同一份 ranking** 的 top-K 填過池，`llama-context.cpp:1928`）。
預測器預測的是「已經在裡面的東西」。要讓它有用，得讓它去猜**prewarm 沒填到**的那一塊，
或者換掉 prewarm 的 ranking —— **提高 precision 不會自動提高有用性**，這兩件事在這條路徑上是分開的。

### 12.5 兩個順帶的量測

- **`ema`（近期性）0.588 > `freq` 0.455**，`static`（離線凍結表）只有 0.468 ⇒ 與 §11.1b 同一結論。
- **`CGC-IDS` 的日誌是兩個 writer 交錯的**：實測 3 行的尾段被 `CGC-RSS:` 插入同一行
  （無緩衝 stderr）。解析器遇到第一個非整數就停（已修，自測 55/55）—— 對整行 `split()` 做 `int()`
  會讓工具在真 log 上直接 traceback，而那是這個 bug 第一次出現時的樣子。

### 12.6 這兩個 t/s **不可引用**

`CGC_IDS_MAX_LINES=20000` 只在量測臂開，它付的代價是熱路徑上的無緩衝 stderr 寫入（設計註解自己
寫著「decode runs with n_tokens=2 … 4000 unbuffered stderr writes per run」）。所以那些 t/s 混了
儀器成本。**要 t/s 就在關掉 trace 的臂上量**，這裡只引用 precision 與計數器。
另外：這不是 soak（2 個請求、~187 步/decode），prompt 也不是生產文件。

### 12.7 處置：工具與指標一起退役（2026-09-28）

不是「這條路優先度低」，是**它量的量沒有消費者**。所以：

- `distill/expert_replay.py` 的 docstring 開頭是退役說明（為什麼、值函數是什麼、該用什麼代替），
  而且**每次執行都會把那段印出來**——由 `expert_replay_selftest.py` 的 `test_retired_banner()`
  檢查（61 項裡有 5 項是這件事）。一條靠人情記住的「不要用」等於沒有；所以它是一條斷言。
- 檔案與 `distill/expert_replay_out/` 的報告**保留**，唯一理由是 §11/§12 的每個讀數要有可重現出處。
- `--scorer command` 那條**JEv／CLM 的入口不再被推薦**：它的目的是在一個 0.5% 帳戶裡比排序。
  外部決策模型要在我們這裡證明價值，該量的是**決策**（§10 那一側），不是專家 id。

**這一輪真正的交付物與工具無關**：M5 prerouter 在生產配置下 `scored=0`（判它死路的依據是
`0/0`）、一個環境變數讓它變成 45.5%、而速度上限 ≲0.5%（§12.1–12.4）。
