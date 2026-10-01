# 既有臂的 draft-liveness 審計（2026-09-28）

> 問題：「用 `draft_liveness` 掃過 repo 裡所有既存的 llama-bench stderr log，列出哪些『k=n 臂』其實有
> measured rep 是純解碼，以及這是否汙染了已發布的 k 結論。」

## 0. 工具與重跑方式

`scripts/check/draft_liveness.py` 新增 **audit 模式**（同一支工具、同一個判準、**同一份**
讀者選擇函式 `series_of`，所以閘門與審計不會各說各話）：

```sh
# llama-bench 口徑（每個 measured rep = 一個 phase=timed 區塊）
python3 scripts/check/draft_liveness.py --audit . --audit /tmp/harness_bench
# 全 corpus，含 server 側（每個 measured request 一行 mean len）
python3 scripts/check/draft_liveness.py --audit Backup --audit-name .log
```

**兩種讀者，同一個量**：

| src | 行 | 粒度 |
|---|---|---|
| `accept` | `CGC-BENCH-ACCEPT phase=timed ... mean_len=2.0244` | **一個 measured rep**（bench 口徑） |
| `server` | `draft acceptance = 0.45000 ( 54 accepted / 120 generated), mean len = 2.35` | **一個 measured request**（server 口徑） |

`accept` 優先（更細）。兩者都沒有 → `UNREADABLE`，**永遠不是 pass**。

## 1. 結果

**bench 口徑（`*.stderr.log`，435 份）**

```
DEAD=5   SPECDBG-only=27   UNREADABLE=145   not-an-mtp-arm=258      ALIVE = 0
```

**全 corpus（`Backup/**/*.log`，4847 份，1319 MB，16.8 s）**

```
DEAD=2   LOW-UNK-K=176   SPECDBG-only=28   UNREADABLE=149
ALIVE=1434   not-an-mtp-arm=3057   UNREADABLE-FILE=1
```

那 2 份 DEAD 是我自己收進 `Backup/arm_draft_liveness_2026-09-28/` 的兩份 bench 臂。

**⇒ 判決分兩半，而且兩半都重要：**

- **bench 口徑（3 個 rep 在同一次 llama-bench 啟動內）：可查的臂 5/5 都是 rep2–3 純解碼，counter-example 為 0。**
- **server 口徑（一個 request 一次量）：1434 份 ALIVE，判定的 DEAD 為 0。**

⇒ **死亡與「引擎的 spec 實作」無關，與「一個行程內連續多次生成」有關。**
反例不是不存在 —— 它在 server 路徑上，而且是壓倒性多數。

## 2. ⚠ 我自己的一個誤判，已修：`LOW-UNK-K`

第一次跑全 corpus 時它報 **178 DEAD**，其中 106 份的 `min_mean_len` 落在 **1.05–1.50**。
那一帶**正是合法的 k=1 臂住的地方**：k=1 的 `mean_len = 1 + a`，實測 a=0.46667 ⇒ **1.47**。
而 1.47 **同時**是 `KEFF_CAP` 記的 k_eff 被壓成 1 的簽名。**光看這個數字分不出來。**

所以判準改成 k 感知的：

- `k` 已知（log 有 `draft n_max=` ／ `CGC_SERVER_MTP_N_MAX=`）：用門檻 1.5；**但 k=1 用 1.0**
  （k=1 的上界是 1+accept < 2，用 1.5 會把一支正常實驗判死）。
- `k` 未知 且 `min_mean_len < 1.5` ⇒ **`LOW-UNK-K`：報告，不判**。gate 回 rc=2（不可判），不是 rc=3。

**這就是這個 repo 反覆咬到的那一族**：一個數字穿判決的衣服。修完 176 份從 DEAD 變成「不可判」。

## 3. 已發布的 k 結論，逐條判決

| 已發布的結論 | 它的原始證據（在 corpus 內？） | 判決 | 汙染？ |
|---|---|---|---|
| **09-23 k=2 vs k=3 ABBA**（16.4% 飄移那條） | ✅ `Backup/k_abba_2026-09-23/abba/`（8 臂 JSON ＋ `driver.log`） | **ALIVE**：src=server、**21 個讀數、`mean_len` 2.21–2.97** | **否** |
| 同上，prefetch 線 | ✅ `Backup/p0_arms_2026-09-23/prefetch_arm_server.log` | **ALIVE**（4 個讀數、min 2.35） | **否** |
| **09-18/19 spec_cost corpus**（m、每 token 成本、keff） | ✅ 27 份 `.stderr.log`，**每個 rep 一個獨立檔案** | **27/27 檔都出現要求的那個 k**（k1→mode 1、k2→2、k3→3、k5→5、k7→7） | **否** |
| **`KEFF_CAP_2026-09-26`** | ✅ 引用 `Backup/cgc_logs/llama_server_20260926_000012.log` | **`LOW-UNK-K`**：3 個讀數、min **1.47** ⇒ 落在 k=1 與 k_eff=1 同一帶，**該 log 沒有 `draft n_max=` 橫幅** | **該文件自己就是這個機制的發現者**（它的 claim 正是 `{1: 90}`／k_eff=1）；審計與它**同向**，不是推翻 |
| **`MTP_ON_FIX_RESULT_2026-09-28`**（今天） | ✅ 兩個 ON 臂的 `*.json` 在 corpus：cap16／cap64 | **兩支都 DEAD**（rep2–3，`mean_len` 1.00，265／262 次 `-1`） | **部分** —— 見下 |
| **今天 r1 那一對**（k=2 vs k=3） | 已遺失（檔名碰撞）；我先前已判不可用 | DEAD | 已被標記 |

### `MTP_ON_FIX_RESULT` 的受影響範圍（精確講）

那兩個 JSON 各含 **2 列**，而兩列的值與該文件的表格逐位對得上：

| 臂 | PP(prefill) samples | TG(decode) samples | 文件的 tg 逐 rep |
|---|---|---|---|
| cap16 | `[275.215, 258.155, 251.619]` | **`[0.385344, 7.34234, 8.77045]`** | `0.45 / 8.26 / 9.49`（另一輪） |
| cap64 | `[183.31, 158.965, 150.696]` | **`[9.58274, 9.4722, 7.9907]`** | `9.58 / 9.47 / 7.99` ← **逐位相同** |

- **PP 數字不受影響**：prefill 不 draft，draft 鏈死活與它無關。
- **TG 數字是混合**：rep1 是活著的 spec rep，**rep2–3 是純解碼**。該文件把這個 rep 內落差讀成
  盒況噪音（它自己的 `attribution.verdict` 已經是 `swap`／`both`，所以它沒把數字當可定價）。
- **它的結論（MTP on 沒有增益、方向為負）活下來，而且更強**：ON 臂的**純解碼**那兩個 rep
  （9.47、7.99）**仍然低於** OFF 臂的（11.5）—— 也就是 MTP-on 的配置**在不 draft 的時候就已經付了成本**。
  但「中位數」這個統計量混了兩條路徑，不能被當成單一 k 的數字。

## 4. 沒被回答的（誠實列）

- **145 份 MTP-on 的 bench 臂完全沒有逐回合證據**（全部 ≤ 09-26；`CGC-BENCH-ACCEPT` 09-26 才入庫，
  見 `dc397fb2f`）⇒ 它們的 k_eff **事後不可知**。其中 124 份要求的是 k=3。
- **176 份 server 讀數因為 log 沒寫要求的 k 而不可判** —— 不是「沒事」，是「沒讀到」。
  `KEFF_CAP` 那份就是這個情形（k 寫在文件裡，不在 log 裡）。
- **`mean_len` 分不出「k_eff 被壓成 1」與「合法 k=1」**。要分得靠 `draft_hist`（`SPECDBG`），
  而它需要 `LLAMA_BENCH_SPEC_DBG` —— 全 corpus 只有 28 份有。
- **`--audit-name` 預設只收 `.stderr.log`**，而 server 側是 `.log`。**這是本輪修掉的範圍缺陷**：
  第一版審計若只跑預設，會回報「沒發現汙染」，而它其實**一份 server log 都沒開**。

## 5. 對協定的意思

**這份審計沒有推翻任何已發布的 k 結論 —— 它把它們洗清了，並把病灶定位到今天的認證協定上。**

1. **k 的結論（09-18/19、09-23）是乾淨的**，因為它們用的是
   **一個 rep 一次量**（server 每 request 一行；spec_cost 每 rep 一個檔）。
2. **`-r 3` 在同一次啟動內量 3 個 rep 是這件事的來源**，而 `cell_contract.py` **明文拒 `reps=1`**
   —— 也就是說，**合約剛好禁止了 corpus 裡唯一沒有出現過死鏈的那種配置**。
3. 因此下一步不是「再量一次 k」，而是：**要么把一個 rep 一次量合法化（第二個 named cell，
   或修 `reps` 的嚴格性），要么接受每個 measured rep 都必須逐 rep 驗 liveness**
   （`k3_pair_cert.sh` 已接，且對兩支真實臂都判拒）。
4. 兩個都做不衝突：**逐 rep 驗證是底線，一個 rep 一次量是把它根除的方法。**
