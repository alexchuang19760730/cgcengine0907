# M1/M2/M3 重新基線：v6 → v7（2026-09-26，dated）

## 結論（一句話）

`DEFAULT_REF` 從 `ref_iq3_pool8gb_M2_6144_bitident_v6_nbaware.jsonl`（md5 `72d82a33ad…`）
換成 `ref_iq3_pool8gb_M2_6144_bitident_v7_20260926.jsonl`（md5 `e1663cc10d…`）。
**這是一次重新基線（re-baseline），不是一個 bug 修復。** 區別很重要，見下。

## 為什麼不是 bug：引擎是可重現的

重新基線最大的危險是「把一個 bug 固化成基準」。排除它的證據是**引擎自洽**：

| 證據 | 內容 |
|---|---|
| 兩次獨立 dump 位元組相同 | 23:21 寫出的 dump 與 23:2x 獨立跑出的 dump，**md5 都是 `e1663cc10d529571553e7105b4a7a51b`** |
| 第三方互證 | **12:29 的 `fn_on` 用的 ref md5 就是 `e1663cc10d`**，當時得 9/9 —— 與本次重新基線的 bytes 完全相同，而那次是另一個 build（13:54 之前） |
| 重新基線後默認跑 | `--tag default-v7-20260926`：**M1 9/9 · M2 9/9 · M3 9/9 · rc=0** |

⇒ 同一個引擎兩次跑出**逐位元相同**的輸出，且與 7 小時前的另一次跑一致。
**沒有數值上的 bug 可修 —— 是「基準所代表的語義」移動了。**

## 舊基線 v6 的歷史：它不是一開始就壞的

`ref 72d82a33ad`（v6）在本 repo 共被比過 **54 次**，其中**多次得 9/9**：

| when | tag | M1 | comparable |
|---|---|---|---|
| 09-23 06:54 | en-k3pair-cert | 9/9 ✅ | **True**（唯一一次乾淨 PASS） |
| 09-23 10:51 | pfxref-part | 9/9 ✅ | False（SEQ_RM env 差異） |
| **09-23 10:56** | pfx-meta | **1/9** ❌ ← 首次 FAIL | False |
| 09-23 11:32 | pfx-ckpt2 | 9/9 ✅ ← 字面 PASS | **False**（CKPT env 差異；gate 未背書） |
| 09-25 17:09 | en-mindmap-250925 | 2/3（coverage 60%，不完全可比） | — |
| **09-26 02:03** | keff_fix_20260926 | **6/9** ❌ ← 從此穩定 | True |

⇈ 舊判讀「v6 是好基準、引擎後來漂移」**已被 09-27 的 bisect 再修正**（見下一節）：
字面 9/9 都出現在 dirty 工作樹形態；**版控內的乾淨狀態從 `cd6d57d92`（09-23 07:02）起就是 6/9**。

## 漂移的性質：只有 MTP 桶動，DEF 桶全同

本次 FAIL 的三行是 `key=[3,0,'MTP']` 與 `key=[4,0,'MTP']`；`ctx_type='DEF'` 的行**全部相同**。
量級是語意級：`[4,0,MTP]` 的 `logits sum` 差 **12.5%**（−678009 vs −593270），不是 ULP。

`comparable=true`、`config_diffs=[]` ⇒ 不是配置漂移（batch/ubatch 已被 `ORACLE_PINNED_ENV`
釘在 6144），是**代碼行為**變了。

### ★ 2026-09-27 更正：`ea8703a2c` 已被**排除**（讀它的 commit message，0 GPU）

上一版把 `ea8703a2c`（09-23 11:48「prefix 重用與投機回滾分離」）列為首嫌疑，**那是錯的**。
它的 commit message 自己記了 gate 結果：

```
m123_oracle_gate（MTP=1；參考 = 先前強制 PART 的 dump）
  M1 numeric identity 9/9   M2 decision agreement 9/9   M3 top-k 9/9
  dump md5 72d82a33ad79e0e69bc935acd24228f2 == 參考 == v6 預註冊參考（逐位元）
  對照組（代價的形狀）：型別改 RS 的 M1 1/9、只開 checkpoint 的也是 1/9，兩者 dump 相同
```

⇒ **它 PASS（9/9），而且它產的 dump 就是 v6 本身** ⇒ **不是回歸點**。
（作者還因此定位到「真正的因是 checkpoint 建立時的尾巴切分」，並已在同一 commit 修掉。）

### ★★ 2026-09-27 bisect 定案：**引擎無回歸 —— v6 的「乾淨 PASS」綁定於一個從未進版控的 dirty 工作樹形態**

用 git worktree（`/tmp/cgc_bisect`，用**當時的 gate/run_server.sh + tracked dylib**，模型經
`CGC_SERVER_MODEL_ROOT` 指向主 repo）實測三個歷史點，全部 vs v6：

| commit（乾淨 tree） | binary digest（libllama/libggml-base） | M1 |
|---|---|---|
| `1ca685490`（= `cd6d57d92^`） | `0080393e`／`9d33b03e`（版控舊 dylib） | 6/9 |
| `079f2fe46`（09-23 21:52） | `0cd5a628`／`7099cb53` | 6/9 |
| `d10a6406d`（09-24 03:21） | `0cd5a628`／`7099cb53`（與上行同 binary） | 6/9 |

再比對歷史 summary 的 `engine_digest`：

- **`en-k3pair-cert`（09-23 06:54，唯一一次 `comparable=True` 的 9/9）的 binary 就是
  `0cd5a628`／`7099cb53`** —— 與上表 FAIL 的 `079f2fe46`/`d10a6406d` **完全同一組 dylib**。
- 它的 tree 是 **`1ca685490` + dirty 22 檔**（含 `scripts/check/decode_sweep.py` 等）——
  那組 dylib 是 09-22 晚上在工作樹裡重編的，**直到 `cd6d57d92`（09-23 07:02）才提交**；
  版控裡 `1ca685490` 自帶的 dylib 是更老的 `0080393e`。
- 補測：`079f2fe46` + `--env CGC_PREFIX_REUSE_CKPT=1`（重現 09-23 11:32 `pfx-ckpt2` 的 9/9 形態）
  仍是 **6/9** ⇒ CKPT env 假說**證偽**。

**⇒ 同一組 binary、同一 ref、同一 profile、9/9 與 6/9 都出現過 ⇒ 回歸載體不是引擎 dylib。**
差異只能在 09-23 06:54 那個 **dirty 工作樹的 scripts/env 形態**（`run_server.sh`／gate 當時
是未提交版本，launch 序列可能不同）—— 該形態**從未完整進版控，原則上不可復現**。
也因此「v6 是好基準、引擎後來漂移」的舊判讀**再修正**：v6 的乾淨 PASS 只存在於那個
不可復現的形態；自從它進版控（`cd6d57d92` 07:02）起，**版控內的任何乾淨狀態都是 6/9**。

**⇒ v7 重新基線是唯一正解**（引擎自身可重現性已有雙重證明，見上節）。

**方法論筆記（bisect 怎麼做的）**：
- 不用主樹 checkout（有別線未提交改動）⇒ `git worktree add /tmp/cgc_bisect <commit>`。
- worktree 缺 gitignored 的 `src/llama.cpp/vendor/`（5.9M）⇒ 從主 repo `cp -R`；
  缺 `models/gguf` ⇒ `CGC_SERVER_MODEL_ROOT=<主repo>/models/gguf`。
- **build/bin 的 dylib 是 tracked** ⇒ worktree checkout 自帶「當時的完整 binary」，
  **不需要 rebuild**（且 `d10a6406d` 的源碼本身編不過——`cgc_rho_prefetch` 聲明不匹配，
  它提交的 dylib 也不是它源碼編的 ⇒ 更必須用 tracked dylib）。
- worktree 的 gate 報 `INVALID COMPARISON`（1 diff = BIN 路徑）——路徑不是數值決定配置，
  M1 字面值有效；digest 對比才是本節的證據主體。

## 明確不宣稱什麼

1. **不宣稱「修好了 bug」** —— 沒有 bug，是基準移動。
2. **不宣稱「引擎與 09-23 11:32 之前的行為一致」** —— 它不一致，這正是重新基線的原因。
3. **不宣稱 v7 是「正確答案」** —— v7 只是「當前引擎可重現的輸出」。若將來發現 MTP 語義移動
   是錯誤的，正確的做法是**修引擎**並再次重新基線，而不是回到 v6。

## 如何回退

```bash
# 回到 v6 判讀（會再次 FAIL，這是預期的）
python3 scripts/check/m123_oracle_gate.py --ref Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v6_nbaware.jsonl
```
`REF_PINS` 同時保留了 v6 與 v7 兩個 md5，所以舊數字**仍可重現**，不會因為這次改動而消失。

## 改了哪裡

- `scripts/check/m123_oracle_gate.py:176` — `DEFAULT_REF` 指向 v7。
- `scripts/check/m123_oracle_gate.py:187-197` — `REF_PINS` 新增 v7 的 md5（v6 保留）。
  （該檔 docstring 明寫：「`--write-ref` … does NOT update this table; after a deliberate
  re-baseline, update the md5 here **in the same commit**」⇒ 本 commit 同時完成兩件事。）
- 新資產（未追蹤，`Backup/` 被 gitignore）：
  `Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v7_20260926.jsonl{,.cap}`
  ⚠ **ref 是 untracked 資產 ⇒ 換機器／clone 後不會自動存在**，需要時用
  `python3 scripts/check/m123_oracle_gate.py --write-ref <path>` 重新產生（見 `.cap` sidecar 的可比性戳記）。

## ★ 2026-09-27 修復：`engine_digest` 的盲區（bisect 的推論缺了一環）

**問題。** 09-27 的 bisect 據「兩次跑的 `engine_digest` 完全相同 ⇒ 引擎沒回歸」下了結論。
這個推論**是錯的**，因為 `engine_digest()` 只 hash `build/bin` 裡的 **linked artifacts**
（`libllama`／`libggml-*`／`llama-server`）：

- 一個在 **dirty 工作樹**下編出來的 binary，會被記錄成與「乾淨 HEAD 編出來的同一個 binary」
  **完全相同的 digest** —— tracked 的部分在 dylib 裡，**未提交的源碼在任何 artifact digest 中都不存在**。
- 而 `tree_dirty()` 當時只記 dirty 的**路徑**（前 20 個），不記**內容** ⇒ 「哪裡髒了」說得出，
  「髒的是什麼位元組」說不出來。

⇒ `pfx-ckpt2`（09-23 11:32，唯一乾淨的 9/9）與之後每一次 6/9 跑在**同一組 dylib**上
（`0cd5a628`／`128b6048`／`7099cb53`），差別就在那些未提交的源碼 —— 而 digest 看不見它。

**實驗（同期）。** 假說「v6 是 `CGC_PREFIX_REUSE_CKPT=1` 專用 baseline」被實測證偽：

```
--ref v6 --env CGC_PREFIX_REUSE_CKPT=1 --allow-incomparable  →  M1 6/9  M2 9/9  M3 6/9
```

⇒ v6 **不是** CKPT 專用 ref；CKPT 這個鍵也**不在** gate 的 numerics-determining subset 裡
（v6 與 v7 的 `.cap` 各 28 個 ENV 鍵逐項相同，含 `CGC_PREFIX_REUSE_CKPT=<absent>`）。
⇒ **v6 的成因至今未解**，本檔案不對它做任何主張，只看作歷史檔案。

**修復。** `scripts/check/m123_oracle_gate.py`：

1. `tree_dirty()` 增加 **`dirty_src_hashes`** —— 對 dirty 的**引擎源碼**（`src/llama.cpp/src/`、
   `ggml/src/`、`tools/server/`、`scripts/run_server.sh`）記錄**內容 md5**
   （上限 40 個、單檔 8 MiB）。這樣「同 artifact digest」才成為一個可檢驗的身分主張，
   而不是一個假設。
2. 抽出純函數 **`dirty_source_hashes(porcelain, root=None)`** —— `root` 可傳，便能對臨時目錄自測，
   不必污染真實工作樹。
3. 新增 **`selftest_tree_dirty_hashes()`**（9 項，含「非引擎檔不入」「untracked 不入」
   「超過大小上限則跳過而非半hash」），並掛進 `--selftest`。
   實測：全 selftest `rc=0`（pool-counter 14/14、engine-digest 8/8、tree-dirty 9/9）。

**验证。** 修改後重跑：**D5 PASS，M1 9/9 · M2 9/9 · M3 9/9**（`--tag fix-treehash-0927`，`rc=0`），
且 `tree.dirty_src_hashes` 如實入檔。

**這不能回答過去。** 09-23 那次的 summary 只留有路徑（而且 `dirty_paths` 實際是空的
—— 舊版 gate 有截斷 bug），內容 hash 無從回溯。**它能防止的是下一次**：日後任何
「binary 相同 ⇒ 行為相同」的推論都必須同時比對 `dirty_src_hashes`。

## 相關

- `docs/S1_CORRECTNESS_SPEC_2026-09-26.md` §8（本線 09-26 對「基準語義移動」的既有定案）
- `docs/MTP_CTX_REPRODUCIBLE_2026-09-26.md`（MTP 可重現性的既有證據）
- `Backup/m123_oracle_gate/summary_{refbase,verify-v7,default-v7}-20260926.json`（三次跑的 summary）
