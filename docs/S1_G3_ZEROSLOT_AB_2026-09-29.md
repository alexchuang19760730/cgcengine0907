# S1 線交接：G3（`CGC_ZERO_SLOT`）在 S1 組上的 A/B —— 唯一能驗它是否真生效的配置

> 開卡：2026-09-29 00:4x，線 A（`209cf650b` P1 驗收後）｜**執行：S1 線**
> 立項卡：`scripts/check/charters/e-s1-g3-zeroslot-2026-09-29.yaml`（預註冊，`_charter_gate` 已 PASS）
> 上下文：`docs/ASYNCFILL_RECOMPUTE_CHARTER_2026-09-28.md` §8、`docs/S1_LINE_VERDICT_2026-09-25.md`

---

## 1. 一句話

G3（`CGC_ZERO_SLOT=1`）**早已落地**，但它在 **prod-new 交付臂上是 no-op**（逐層見證行 0 次）。
它只在 **S1 那一組開關**下會跑。09-26 有過一次**單臂**證據（G3 生效），但**沒有對照臂**
⇒ 缺的正好是「同一組、不開 G3」的那一趟。本卡就是補那一趟，共 2 趟。

**主端點是計數器，不是速度** ⇒ 不受 MDD 8.5%／趟間漂移限制 ⇒ **不要求 NOMINAL 乾淨窗**。

---

## 2. 為什麼只有 S1 組能驗（源碼證據鏈，00:4x 現查）

| 環節 | 位置 | 結論 |
|---|---|---|
| G3 的 writer（`for (const auto & kv : cache_slot_table_tensors)`） | `llama-context.cpp:3776` | 在 **`if (cgc_b_scheme)`**（`:3730-3731`）裡面 |
| `CGC_B_SCHEME` 是否開啟 | `run_server.sh:355`（`[ -z ... ] && CGC_B_SCHEME=1`）＋ resolve-only 實測 base env 含 `CGC_B_SCHEME=1` | **預設開** ⇒ 交付臂**有進這個 if**；空轉不是外層 if 的鍋 |
| 空轉的真正原因 | `llama-context.cpp:3776` 的 map 是空的 | `cache_slot_table_tensors` 只在 graph 建出 `ffn_moe_slot_table` 時填入（`llama-graph.cpp:2261`／`:2266` → `llama-context.cpp:8261`） |
| 那條分支的開關 | `llama-graph.cpp:2146` `cgc_slot_table_gpu = getenv("CGC_SLOT_TABLE_GPU")` | **要讓 G3 可被觀察，最小條件是 `CGC_SLOT_TABLE_GPU=1`** |
| 為什麼還要 `CGC_SEG_BATCH=1` | `llama-context.cpp:3720-3729` | SEG_BATCH **跳過逐層 hook**（hook 會在 argsort 後把正確映射寫回）⇒ preflight 的寫入成為唯一寫入 ⇒ 佔位符／zero slot 的選擇才真的影響輸出，A/B 才可讀 |
| G3 故意不 bit-identical | `llama-context.cpp:3768-3772`（"not meant to be"、"A zero slot does NOT make G1 pass"） | ⇒ **M1／M2 在這一組上不適用**，見證行是唯一判據 |

⛔ 糾錯：`CGC_ZERO_MISS` 這個 env **不存在**（`src/` 0 命中）；開卡時引用的
`llama-expert-cache.cpp:277-282`（驅逐）／`:292-316`（MTP fast path）／`llama-graph.cpp:2718`（gelu）
**都不是** G3 的點。真點只有 `llama-context.cpp:3745-3818`。

---

## 3. 已知的 vs 缺的

**已知（09-26 smoke2，`Backup/phase_decomp/g1b_g3_smoke2/s1.log`，rc=0，單臂）**

```
CGC-G3-ZEROSLOT: il=9 ns=143 zero_slot=142 (reserved slot exists and is now zeroed)
CGC-G3-ZEROSLOT-TOTAL: zero_slot=3398260 placeholder=0
```

**已知（09-29 00:2x，`Backup/phase_decomp/g3/zeromiss.json`，prod-new 交付臂）**

```
CGC-G3-ZEROSLOT-TOTAL: zero_slot=0 placeholder=0     ← 印 6 次
CGC-G3-ZEROSLOT: il=..                                ← 0 次（for 迴圈沒進）
```

**缺的**：`zero_slot=3398260` 沒有對照 ⇒ 無法與「計數器本來就會數到東西」區分，
也無法排除「本 cell 沒有非駐留專家 ⇒ 佔位符路徑從沒走到」。
⇒ 需要 **A 臂（同組、不開 G3）** 證明 `placeholder > 0`。

---

## 4. 命令（照抄，勿改）

### 4.1 跑前：resolve-only 確認三鍵送達（0 GPU，必做）

```bash
cd /Users/alexchuang/Documents/flashkv-devserver
PYTHONPATH=/opt/homebrew/lib/python3.14/site-packages /opt/homebrew/bin/python3 -c "
import importlib.util
def load(n,f):
    s=importlib.util.spec_from_file_location(n,'scripts/check/'+f); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
mb=load('mb','llama_bench_matrix.py'); h=load('h','harness.py')
for spec in ['prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1',
             'prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1;CGC_ZERO_SLOT=1']:
    prof, env, ov = h._parse_arm(spec); res = mb.resolve(prof, env)
    print(spec, '| overrides=', ov, '| dropped=', mb.arm_env_dropped(prof, env, res))
    print('   env:', {k:v for k,v in res['env'].items() if k.startswith('CGC_')})
"
```

期望：`dropped=[]`，且 env 裡看得到 `CGC_SEG_BATCH=1`／`CGC_SLOT_TABLE_GPU=1`（B 臂另加 `CGC_ZERO_SLOT=1`）。
**不要加 `!` 前綴**（prod-new 基底 env 為空 ⇒ `!` 是多餘的，且曾被丟過）。

### 4.2 兩趟（分兩次指令跑，別併成一條）

```bash
# A：對照（S1 組，不開 G3）
python3 scripts/check/harness.py bench \
  --arm "prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1" \
  --charter scripts/check/charters/e-s1-g3-zeroslot-2026-09-29.yaml \
  --json Backup/phase_decomp/s1_g3/a_nozeroslot.json --workdir /tmp/harness_s1g3_a

# B：處理（S1 組，開 G3）
python3 scripts/check/harness.py bench \
  --arm "prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1;CGC_ZERO_SLOT=1" \
  --charter scripts/check/charters/e-s1-g3-zeroslot-2026-09-29.yaml \
  --json Backup/phase_decomp/s1_g3/b_zeroslot.json --workdir /tmp/harness_s1g3_b
```

- `--reps 1` 就夠（見證行在最初幾步印完：`s_g3_shots<8`、`s_g3_total_shots<6`）；
  要沿用權威 cell 則帶 `-p 2048 -n 128 -d 512 -r 3 --warm-skip 64`。
- ⚠ `--workdir` 兩趟要分開（`/tmp/harness_bench` 的 stderr 檔名由 arm 字串決定，同 arm 會互蓋）。
- ⚠ 盒子一場次只能連跑 **2 趟**（第 3 趟會因 swap 7434 MiB 崩成 tg 0.392）⇒ 本卡正好 2 趟，**不要加第三趟**。
- ⛔ **不要開** `CGC_MISS_MASK_DBG`（每步多一次 synchronize，t/s 直接報廢且本卡不需要）；
  `CGC_MISS_MASK=1`／`CGC_MISS_MASK_COST=1` 是 G1b 定價用的，**驗 G3 不需要**。

---

## 5. 判讀表（預註冊，看到資料後不得改）

| 結果 | 條件 | 結論 | 後續 |
|---|---|---|---|
| **PASS** | A：逐層 `(NOT ARMED -> placeholder)` ＋ TOTAL `zero_slot=0`、`placeholder>0`；B：逐層 `zero_slot=142`（ns=143）＋ TOTAL `zero_slot>0`、`placeholder=0` | G3 在 S1 組**真生效**，且是唯一把佔位符清零的機制 | G3 可視為 G4（重算）的既有前置，不必重做 ⇒ `e-asyncfill-recompute` 可進 P2（圖建構／scatter 成本） |
| **FAIL** | B 的逐層行仍是 `zero_slot=-1`，或 TOTAL `placeholder>0` | arming 沒生效 | 查 `CGC_DUMP_ENV` 是否送出、`llama_expert_cache_usable_slots` 是否真變 `ns-1` ⇒ async fill 卡的 P2 要先補這個前置 |
| **UNRESOLVED** | A 的 `placeholder==0`（本 cell 無非駐留專家，計數器無法分辨） | 見證行在本 cell 不可判 | 改量 `CGC_MISS_MASK=1`（**不帶 `_DBG`**）或直接數 slot table 的非負項 |
| **趟無效** | 任一臂 `CGC-G3-ZEROSLOT: il=` **0 行** | map 仍是空的（env 沒送達） | **不結論**，先回報 env 層問題 |

三種輸出導向同一個行動：**把結果回填本卡與 `e-asyncfill-recompute`，不為 G3 再單獨投盒子**。

---

## 6. 明確不要做的事

1. ⛔ **不要引用任何 t/s**：單段提交臂輸出是 garbage（`docs/S1_LINE_VERDICT_2026-09-25.md`：
   分段 11.30 → 單段 20.73 是「沒有 recompute」的讀數），且 G3 依設計就不 bit-identical。
   本卡的數字欄只有計數器。
2. ⛔ **不要要求 M1／M2**：G3 把 unbounded error（指向別人的權重）換成 bounded 的 0，
   這是**語義改變**，不是精度誤差。
3. ⛔ **不要加第 3 趟**（盒子 2 趟上限）。
4. ⛔ 別用 `swap_used` 判窗好壞（黏性歷史量）⇒ 看趟內 pageins（正常 +170 萬／崩 +2360 萬）。
5. ⛔ 別把「乾淨窗」當成本卡的門檻：主端點是計數器，髒窗可裁判；thermal 只影響能不能跑完。

---

## 7. 回報格式（回填到本檔 §8 與 `2026-09-29.md`）

```
A: il 行數=..  逐層尾綴=..  TOTAL zero_slot=.. placeholder=..   rc=..
B: il 行數=..  zero_slot=..(ns=..)  TOTAL zero_slot=.. placeholder=..  rc=..
thermal.worst=..  pageins 增量=..
判決：PASS / FAIL / UNRESOLVED / 趟無效
```

---

## 8. 執行結果（S1 線回填處）

_（待跑；未跑前本節留白，勿自行填數字。）_
