# 第六條軸（L25-1）的第一個候選：`--spec-type ngram-simple`

**日期**：2026-09-30 00:0x–01:0x
**判決**：**⛔ ② 不成立（乘數不攤薄）：E = 0.984 ＜ 1** ⇒ 依卡上的否證條款，這個候選**不產生攤薄**
（「回到以現有軸加總到不了」）。**③（輸出同一性）未被量到，且現在不需要** ——
但它**不是**因為「輸出不同」而失敗，這點要說清楚（見 §4）。
**產物**：`Backup/ngram_simple_ab_2026-09-30/{A,B}.json` ＋ `.stderr.log` ＋ `{A,B}_oracle.jsonl`

---

## 1. 發車前的通路查證（使用者指定的先決條件）

`--spec-type` 走 CLI，而 `bench` 的 arm 語法只裝 env ⇒ 先確認它真的送得下去。**逐層查，不用試跑**：

| # | 查什麼 | 結果 |
|---|---|---|
| 1 | `llama_bench_matrix.py` 怎麼組 cmd | `:751 if args.spec_type: cmd += ["--spec-type", args.spec_type]` ⇒ **原樣附加**，無 allowlist、無改名 |
| 2 | llama-bench 認不認這個值 | `--spec-type ngram-simple` ⇒ 一路走到「模型載入失敗」（故意給不存在的模型）；`--spec-type bogus-xyz` ⇒ **在參數層被拒**並印出全表。⇒ 值合法且**不需要 draft model** |
| 3 | llama-bench 有沒有真的用它 | `llama-bench.cpp` 有**為此而寫的 n-gram 臂**（2026-09-19）：`is_ngram_spec_type()`（5 個 ngram 型別）被**型別驗證 `:1379`／spec 設定 `:1400`／量測迴圈 `:2737` `:2983`** 三處消費 |

★ ③ 的旁證：該檔開頭的註解自己寫明了這條臂的用途 ——
「the n-gram CONTROL arm: the draft comes from the token HISTORY and there is **NO draft forward** …
its m is **the control for draft-mtp's m**」。也就是說這條臂**本來就是為「分開 draft 前向與 verify 成本」而存在的**。

### ⛔ 但查證抓到兩個「看起來設了、其實沒設」（都已修）

| 儀器 | 症狀 | 原因 |
|---|---|---|
| `LLAMA_BENCH_SPEC_DBG` | **allowlist 完全沒有這一條** ⇒ 在 `harness bench` 下**靜默丟掉**，跑完 0 行見證 | `SPECDBG round:` 是**唯一**能在非 MTP 投機臂上量 E 的見證行（`llama-bench.cpp:3123`，presence-based） |
| `CGC_MTP_PERF` | 它的**唯一** allowlist 條目在 `if [ "$SERVER_MTP" = "1" ]`（`:2477`）區塊**之內** ⇒ ngram 臂（`SERVER_MTP=0`）整塊不跑 ⇒ 靜默丟掉 | 名字叫 MTP，但它印的是**泛用**的 `CGC-MTP-PERF type=<t> … emit_tok_per_round=` |

兩者症狀同形：**跑完看起來完全像「儀器沒東西可報」**。修法＝兩條都移進常開的 allowlist
（`scripts/run_server.sh:1528` 的 `for _v in …`），並在原地留註解說明為什麼。
見證（0 GPU，`CGC_DUMP_ENV=1` 的 resolved env）：

```
LLAMA_BENCH_SPEC_DBG=1      CGC_MTP_PERF=1      CGC_LOGITS_ORACLE_DUMP=/tmp/oracle_test.jsonl
```
修好後實跑：**A 臂 PERF=0／SPECDBG=0（對照，本來就無投機）；
B 臂 PERF=6／SPECDBG=384** ⇒ 兩條儀器都真的到了。

### ⚠ 第三個發現（結構性）：ngram-simple 的**參數在 bench 上不可調**

`--spec-ngram-simple-size-n/-size-m/-min-hits` 對 llama-bench **一律拒絕**
（`error: invalid parameter for argument`，值 4/8/12/32 都一樣）。
不是值域問題（lambda 允許 1–1024），是 **llama-bench 自己那份手寫 arg 迴圈**的白名單：
`llama-bench.cpp:1330 else { invalid_param = true; }` —— `--spec-type` 有被列進去，
這三個**沒有**。⇒ **在 `harness bench` 這條唯一認可的入口上，ngram-simple 只能跑編譯期預設值
（`common.h:367-371`：`size_n=12`、`size_m=48`、`min_hits=1`）**，掃參數需要改 `src/`。

### ⚠ 第四個發現：`--help` 與程式碼互相矛盾

`llama-bench --help` 對 `--spec-type` 說「**only 'draft-mtp' is implemented here**」，
但 `:1379` 的型別驗證同時接受 5 種 ngram 型別，`:1400`/`:2737`/`:2983` 都有實作。
⇒ **過期的說明文字**（這行會讓下一個人不去試那條其實可用的臂）。
⛔ **本輪不改它**：改它要重編，而樹上現在有別條線**未提交的 `src/`**
（`common/speculative.cpp`／`ggml-backend.cpp`／`ggml-metal-context.m`）⇒ 重編會把別人的
半成品烤進共用 binary。留給它自己的 commit。（同「建置產物是共用資源」那條。）

---

## 2. 兩臂與結果

同一交付 cell（預設 bench cell、`-r 3`）、MTP off、兩臂都帶
`CGC_MTP_PERF=1;LLAMA_BENCH_SPEC_DBG=1;CGC_LOGITS_ORACLE_DUMP=…`：

| | A（對照） | B（處理） |
|---|---|---|
| arm | `prod-new`（無任何投機） | 同 A ＋ CLI `--spec-type ngram-simple` |
| rc | 0 | 0 |
| `CGC-MTP-PERF` | **0 行**（無投機物件可報） | **6 行**，`type=ngram-simple` |
| `SPECDBG round:` | 0 行 | **384 行**（6 rep × 64 輪） |
| oracle dump | 390 行 | 1158 行 |

### 2.1 ★ ② E（每輪產出 token）：**0.984**

用**迴圈自己的見證**（`SPECDBG round: n_done=… draft=…`）按 rep 切段（`n_done` 變小＝換 rep），
六個 rep **完全一致**：

| rep | rounds | 產出 token | **E** | draft 寬度 |
|---|---:|---:|---:|---|
| 1–6（各） | 64 | 63 | **0.984** | `{1: 32, 3: 32}` |
| 合計 | 384 | 378 | **0.984** | — |

提交分布：**192 輪提交 0 個、186 輪提交 2 個**（其餘 5 次負值＝rep 邊界）。
⇒ **每輪平均只產出 0.984 個 token，比 plain decode 的 1.0 還少**，而每輪的成本更高。

**獨立複核（archive）**：`Backup/phase_decomp/ngram_ab/` 有 09-20 的 `ngram-map-k`
（另一種 ngram 型別、另一個 cell：`-p 0 -b/-ub 512`、`n_max=3`）⇒ 128 輪產出 127 token
⇒ **E = 0.992**。⇒ **兩種 ngram 型別、兩個 cell、兩套儀器，E 都 ≈ 1.0。**

### 2.2 ⚠⚠ 儀器警告：PERF 那行**不能**直接讀成 E

同一趟 B 的 `CGC-MTP-PERF` 印 `gen_tok_per_round=3.000`、`acc_rate=0.3333`、
`emit_tok_per_round=2.00`。若照字面取 `gen_tok_per_round` 當 E，會得到 **E = 3（一個 3× 乘數！）**
—— 而迴圈的見證說 **0.984**。**差了 3 倍，且方向相反。**

源碼查證（`common/speculative.cpp`）：
- `:148` `n_gen_tokens` 的註解是「tokens generated **by this implementation**」，
  `:3022` 的遞增是 `n_gen_tokens += result.size()` —— 也就是**起草出來（proposed）的 draft token 數**，
  **不是**輸出 token 數；
- `:3064` `n_acc_tokens += n_accepted` ⇒ **被接受的 draft token 數**；
- 於是 `acc_rate = acc/gen = 32/96 = 1/3`（＝draft 命中率，不是接受率）；
  `gen_tok_per_round = 96/32 = 3`（＝**每個起草輪的 draft 寬度**，不是每輪產出）；
  `emit_tok_per_round = (acc + calls)/calls = 2`（＝**起草輪**的產出，不是全部輪）。
- 真正對得上的讀法：**每 rep 64 輪、其中 32 輪起草（寬度 3）、32 輪不起草；
  32 個起草輪各接受 1 個 ⇒ 約 31 輪提交 2 個 token（1 個接受的 draft ＋ 1 個 verify 自產）＝ 63 個。**
  ⇒ **E 只能從 `SPECDBG`（或等價的每輪見證）拿，不能從 `CGC-MTP-PERF` 的 per-round 欄位拿。**

---

## 3. 判決

| 卡的驗收條 | 判準 | 實測 | 判 |
|---|---|---|---|
| ① `need_n_rs_seq() == 0` | 源碼已得 | `common.h:395` 只列 MTP／EAGLE3／DFLASH／DSPARK ⇒ **0** | ✅ |
| ② `E > 1` | 有攤薄 | **0.984**（6/6 rep 一致；archive 的 map-k 0.992） | ⛔ **不成立** |
| ③ `answer_md5`／輸出同一 | 與對照臂相同 | **見 §4：量不到，且與判決無關** | — |

⇒ 依卡上的否證條款：**「② 若 ② 不成立（E ≤ 1）⇒ 這個乘數不產生攤薄，回到以現有軸加總到不了
（維持判死）」** ⇒ **`ngram-spec` 這個 PRIMARY 候選結案為「不成立」**。

★ 這條的價值不在「ngram 不行」，而在**「steps/token 這個方向的唯一無 draft-head 候選，實測不出攤薄」**
⇒ 第六條軸的 steps 半邊只剩下「要產品決策」的 MTP 那條（§39 C6）；**bytes 半邊的 SECONDARY
（`CGC_SERVER_LOAD_MODE`）成為下一個要量的**。

---

## 4. ③ 為什麼量不到（以及為什麼不影響判決）

`cgc_logits_oracle_compare.py` 對這兩份 dump 給：

- `--align step`（預設）：common **390**、only_B **768** ⇒
  `M1 5/390（1.3%）`、`M2 152/390（39.0%）`；
- `--align pos`：`keys_common=4`、only_A 128、only_B 384 ⇒ `FAIL (3/4)`。

**兩種對齊都不成立，所以那 39% 不能讀成「輸出不同」。** 原因：dump 是**每次 context decode**
落一筆，而兩臂的 forward 結構本來就不同（A 一輪 1 token、B 一輪 1–2 token ⇒ 份數與位置都不同）
⇒ 這是**投機臂的結構性後果**，不是數值分歧。⚠ 工具自己的 cross-tab 也在警告這件事
（`M1 diff & M2 same = 147` 與 `M1 diff & M2 diff = 238` 混在一起、且 `only_B=768`）。

⇒ 要判「ngram 是不是同一個輸出函數」，需要的是**取樣 token 序列**的比對（答案層），
不是 logits dump 的逐筆比對；而 `llama-bench` **不產生文字**（bench 產物沒有任何輸出欄位），
所以這條要嘛走 server/probe 路徑、要嘛另造儀器。**本輪不做** —— 因為 ② 已經否證，
③ 不改變判決（**乘數不攤薄，就沒有「要不要接受它的輸出」這個問題**）。

---

## 5. 本輪踩到的第五件事：charter gate 擋下了我自己的卡

第一版 `e-sixth-axis` 的 `baseline.source` 寫成
`docs/SINGLESUBMIT_CORRECT_DESIGN_2026-09-28.md §39（C1–C7 ＋ 四級槓桿清單）` ——
**把節號寫進了路徑**，而 charter gate 對 `source` 做 `os.path.exists` ⇒ **兩臂都 rc=2 拒跑**
（連發車都沒發出去）。修法：`source` 只放路徑，節號移進 `source_note`。
★ 這是「**閘門抓到我自己的卡**」的第二例（第一例是 L20-2 的 `evidence.quote`）。
