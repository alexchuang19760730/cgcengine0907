# k=3 的 16.4% 為什麼還不能認證：**那份協定跑不動**（2026-09-28）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **12.57／12.62 t/s 屬 MTP-on 輸出**，是**另一個輸出函數**，
> **不可與 MTP-off 互比**（含「MTP 加速 X%」「MTP-on ≥ MTP-off」「以 12.57 為基線／錨點／要打敗的數字」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**。

對象：`docs/K3_PAIR_CERT_V2_BENCH_2026-09-23.md`（下稱 v2）——已事先登記、用來把
「k=2 比 k=3 快 16.4%」從 screen 判成 CERTIFIED 的那份協定。

結論先講：**v2 照字面執行不會產生任何一個讀數**，而且它有 **8 個各自獨立的原因**，
其中 6 個是靜默的。這一輪把可以就地修的都修了（有 selftest、有零 GPU 驗證），
但**最後一個是結構性的：它登記的量測入口已經量不到它自己的 cell**——那不是我能修的，
要換入口就等於**重新登記一份協定**，那是 owner 的判斷。

**這一輪沒有產生任何新的 t/s 讀數。16.4% 仍未認證，狀態與昨天相同。**

---

## 0. 它在哪一步停住（照協定原文的順序）

```
第 1 步  python3 prod_profile.py ...           ⇒ D3  rc=1（3.9 跑不動）★已修（驅動改直譯器）
第 2 步  llama_bench_matrix 拒跑               ⇒ D5  rc=2（缺 CGC_INTERNAL_CALL=1）★已修
第 3 步  同一支再拒跑（cell contract）        ⇒ D8  rc=1（交付 cell ≠ 測試卡權威 block）✗ 未修
第 4 步  prod_profile 崩潰（KeyError）        ⇒ D6  ★已修
第 5 步  k_swing_decompose 讀不到、靜默回 n=0 ⇒ D1  ★已修（且改成大聲失敗）
```

D8 是牆：**`prod_profile.py` 從某一天起就量不到它自己的交付 decode cell。**
下面逐條，每一條都實測過。

---

## 1. 八個缺陷

### D1｜工具讀不懂那份口徑，而它讀不懂的方式是靜默的 ★已修

`k_swing_decompose.py::arm_rows()` 只認 server 口徑（`requests[].decode_tps`），
而 `prod_profile.py` 的輸出是 `{"axes": [{"rows": [{...}]}]}`。**實測**（把真實的
`prod_profile_20260920_1230.json` 複製成 `r1_a_k2.json` / `r1_b_k3.json` 餵進去）：

```
skipped (group not identifiable or <2 usable requests): r1_a_k2.json, r1_b_k3.json
  fewer than two pairs: no sd, no plan
rc=0
```

每支 arm 被跳過、`n=0`、**rc=0**。而 `n=0` 與「還沒跑」在輸出上無法區分。

### D2｜那份 `±` 讀不回來：3 個 rep 的平均與標準差**不足以**還原 3 個數 ★已修

配對統計只要 arm mean，沒問題；但 `decompose`（§5.1 整節的基礎）要的是**每一 rep**。
`prod_profile.py` 讀了 `samples_ts`（只為了數 `n_reps`）然後**丟掉**。
`llama-bench.cpp:2190` 一直在印它，`llama_bench_matrix.py:412` 也整包收下來
⇒ 這是一條**漏掉的傳遞**，不是缺量測。

### D3｜`python3` 在這台機器上是 3.9.6，跑不動 `prod_profile.py` ★已修

```
$ python3 scripts/check/prod_profile.py --help
  File ".../profile_duo.py", line 92, in <module>
    def val(res: dict) -> float | None:
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'   rc=1
```

需要 ≥ 3.10；正確的是 **`/opt/homebrew/bin/python3`（3.14.6）**，協定沒說。

### D4｜`--no-ref` 刪掉 anchor —— **但我要更正我自己上一版的說法**

我上一版寫「§4 的 `--no-ref` 會量到**另一個臂**（prefill250 的 9.90，而交付 cell 是
prod25-stream 的 12.57）」。**那個推論是錯的**：

- `prod_profile.py` 自己的 docstring 記載：**`prefill250` 與 `prod25-stream` 在這個
  decode cell 上旋鈕集合完全相同**（`CGC_DUMP_ENV=1` 零 GPU 驗證過，resolved env diff 為空；
  差別只有 BATCH/CTX/chat-template，而交付 cell 三個都覆蓋掉）。
- 而 09-20 那個 9.90 的 arm，它的 verdict 是 **`worst MODERATE`（`ok:False`，「left NOMINAL」）**
  —— 那是一個**被熱污染**的讀數，不是不同的臂。12.57 那支才是 `worst NOMINAL`。

⇒ 正確的 D4 只是：**`--no-ref` 丟掉的是「同一份紀錄內的再現性錨」**
（工具存在的理由：「profile 與 anchor 若不一致，findings 是『交付約定沒有再現』」）。
它是**代價**，不是**錯誤**。

### D5｜★ 09-25 起的 `CGC_INTERNAL_CALL` 閘門：每一支 arm 都秒退 ★已修

`llama_bench_matrix.py:859` 在 09-25 加了一道 fail-closed 閘門：沒有
`CGC_INTERNAL_CALL=1` 就拒跑。其他內部呼叫者都帶了
（`harness.py:1241`、`commit_bench.py:62`、`arm_two_pass.py:338`），
**`prod_profile.py:159` 沒有**。⇒ 自 09-25 起，**`prod_profile.py` 的每一支 arm 都是
rc=2、耗時 ~0 秒、沒有 row**。

### D6｜然後它崩潰，而不是報告 ★已修

`verdict()` 在沒有 row 時回傳 `{"ok": False, "why": "no row"}`——**沒有 `launch` 鍵**，
而下一個 print 無條件讀 `rec["verdict"]["launch"]`：

```
File ".../prod_profile.py", line 405, in main
    rec.get("rc"), row.get("t/s"), rec["verdict"]["launch"], ...
KeyError: 'launch'
```

⇒ **失敗被報成崩潰**，而且 `--json` 還沒寫 ⇒ 紀錄（含子行程的 stderr）**整份消失**。

### D7｜診斷被丟掉：tee 在早退之後 ★已修

`--log-dir` 的 tee 原本在 `if not tmp.exists(): return rec` **之後**，所以
「沒有 row」那兩條路徑不會留任何檔。實測第一次跑完 `/tmp/kb/logs/` 是**空的**。

### D8｜★✗ **結構性：交付 cell 已經量不到了**

D5 修好後再跑，matrix 給出第二個拒跑：

```
  ⛔ cell 口徑與測試卡權威 block 不一致 — 拒跑（fail-closed）：
     - batch:  實際 512  ≠ 權威 5632
     - ubatch: 實際 512  ≠ 權威 5632
     - prompt: 實際 0    ≠ 權威 2048
     請走生產入口（harness bench / commit_bench）的預設，或顯式對齊測試卡 §2.5。
```

`cell_contract.py` 從 `docs/PROD_NEW_TEST_CARD_*.md §2.5` 讀**一個**權威 CELL
（＝ prod-new 的 host prefill cell：5632/5632/2048），嚴格維度不符就**拒跑**。
而 `prod_profile.py` 的**全部用途**就是量交付 decode cell
（`--batch 512 --prompt 0 --depths 512 --warm-skip 64 --spec-type draft-mtp`）。

⇒ **兩者互斥。** `prod_profile.py` 不是「偶爾失敗」，是**它自己的 cell 通不過它自己的
下游閘門**。v2 §3 登記的正是這支工具。

**而這不是我能修的**：換到 09-25 裁定指名的入口（`harness.py bench`）就等於**換口徑**，
而 v2 §1 之所以存在，正是因為上一版 v1 **換口徑**而失效。在一個事先登記的實驗中途換
量測入口，要重新登記，不是修 bug。

好消息：**k 軸在新入口是可表達的**——`harness.py bench` 有 `--spec-draft-n-max`
（`:1411`）且會傳給 matrix（`:1081`），也有 `--json`、`--arm PROFILE:!OVERRIDE;KEY=VAL`、
以及 `--charter`。

---

## 2. 這一輪真正改動了什麼

### 程式（三支，都有 selftest 或零 GPU 驗證）

| 檔 | 改動 |
|---|---|
| `scripts/check/prod_profile.py` | D2：`rows[]` 直通 `samples_ts`。D5：子行程帶 `CGC_INTERNAL_CALL=1`。D6：`verdict()` 的 no-row 分支補齊鍵，且報告路徑改印 **`NO ROW` ＋ 子行程 stderr**。D7：`--log-dir` 的 tee **移到早退之前**。另補 `import os`。 |
| `scripts/check/k_swing_decompose.py` | D1：新增 `bench_decode_cells` / `bench_pick_cell` / `bench_rep_ts`，`arm_rows` 先試 server 再試 bench。**讀不動就在印任何數字之前拒跑（rc=2）並指名原因**。選格靠規則（`--bench-cell auto\|anchor\|primary`），**並公告它讀了哪一格**。F 檢定門檻與 df 不符時出聲。「零 row」的紀錄會被說成「這支臂什麼都沒產出」而不是形容形狀。**selftest 59 → 65/65**。 |
| `scripts/check/k3_pair_cert.sh`（新） | 四個更正 ＋ 五個守門：直譯器探針（直接試 import，不是比版本號）、不覆蓋既有 arm、窗口閘門、已有 llama 就拒跑、**以及 arm 產物驗證**——因為 `prod_profile.py` 在**每一支軸都 `NO ROW` 時仍回 rc=0**（實測），所以 rc 抓不到「16 支空 arm 跑完」；產物沒有可讀的 decode cell 就 `exit 3`。ABBA 輪替；k=3 臂不傳 `--spec-draft-n-max`（與 12.57 逐字相同）。**只跑、不判**。 |

### 零 GPU 驗證（判準 0：先證明讀出會動）

| 檢查 | 結果 |
|---|---|
| 舊 bench 紀錄（無 `samples_ts`）| **rc=2**，指名 `samples_ts`，在印任何數字**之前** |
| 真 `prod_profile` 形狀（anchor ＋ 主軸）| 讀 `decode-delivery-anchor` **並公告** |
| 兩個候選格且無 anchor | 拒跑，原因含 `ambiguous` |
| v1 server 口徑 5 對 | 仍讀得到，**重現已發布的數字**（k2 within 10.54%、launch 0.00%、ub 9.98%、F=0.62/(4,10)；k3 grand 10.43）|
| `--dry-run` 命令列 | k=3 臂 ＝ `--arms prod25-stream … --spec-type draft-mtp`（與 12.57 同） |
| 驅動的直譯器守門 | 指到 3.9 時 rc=2 ＋ 指名 `profile_duo.py:92` |
| 驅動的 arm 產物守門 | 空 arm ⇒ `exit 3` ＋ 指名原因 ＋ 指出紀錄與 log 路徑 |
| 驅動的窗口守門 | `admits=False` ⇒ rc=2（並且抓到了另一條 session 剛起的 `llama-server`）|

### 實跑（有碰 GPU）

`prod_profile.py --axes decode --reps 3 --profile prod25` 跑了兩次：修 D5/D6/D7 前後各一次。
第一次：rc=1、`KeyError`、**診斷全丟**。第二次：**`NO ROW` ＋ 子行程 stderr ＋ JSON 有寫出來
＋ `--log-dir` 抓到完整 stdout**——量具是好的，擋住的是 D8 的 cell contract。
兩次都在 ~1 秒內結束、**沒有 llama-bench 真的啟動**（拒跑發生在建指令之後、執行之前）。

---

## 3. 誠實欄

- **沒有任何新的 t/s 讀數。** 16.4% 的認證狀態不變。
- **D5/D6/D7 是我自己的執行過程挖出來的，不是讀碼推的**，而它們只有在**真的去跑**的時候
  才會現形——靜態看 `prod_profile.py` 它完全正常。
- **我上一版把 D4 講錯了並已更正**（見 §1 D4）：我拿兩個讀數（9.90 / 12.57）當成兩個臂的證據，
  而工具自己的 docstring 說那兩個 profile 在這個 cell 上旋鈕相同，且 9.90 那支的 verdict
  是 `worst MODERATE`。**錯的那個版本會用錯誤的理由支持一條規則。**
- D1 的「靜默」與 D5/D6 的「全丟」是同一族：**失敗沒有形狀**。這一輪把三處都改成有形狀
  （rc≠0 ＋ 指名 ＋ 保留 stderr）。
- 沒有動任何別人的檔案。`Backup/` 未新增。`/tmp/probe1*`、`/tmp/kb` 是暫存。
- **這一輪有兩個 bug 在我自己寫的守門裡，而且都是測試的時候才抓到**：
  (i) 直譯器守門第一版用版本正則 `3.[2-9]`，它**誤匹配 3.9**——正是要排除的那個版本；
  現在改成**直接試 import**（要的是能力，不是號碼）。
  (ii) 窗口守門寫成 `if ! "$PY" -c ... | tee -a "$LOG"; then`，而**管線會把退出碼換成 `tee` 的**
  ⇒ `admits=False` 的那一次它**照樣往下啟動**。已加 `set -o pipefail`。
  兩個都是同一族：**守門自己沒被驗證過**。「寫了閘門」不等於「閘門會關」。
- **v2 的正文我沒有改**：D3/D4 是命令層面的錯、D8 是口徑層面的衝突，兩者都需要 owner。

---

## 3.5 決定與登記（2026-09-28，跑前問、跑後不改）

D8 的三條路問了 owner，**選定：在測試卡 §2.5 的權威 cell 上、走 `harness.py bench` 認證**。

為什麼這是唯一「今天就能跑又不撞閘門」的路：`harness.py` 的 `_BENCH_DEFAULTS`
（`prompt 2048, gen 128, depths 512, reps 3, warm_skip 64, ctx 0, batch/ubatch 5632, fixed_fill_seed 1`）
與 §2.5 的 CELL **逐項相同** ⇒ 它的預設就是合約要的那個 cell；而它有 `--spec-draft-n-max`
⇒ k 軸可表達。

**口徑變了，所以這是一次重新登記，不是「換一支工具跑同一件事」：**

| | v2（已失效） | 這次 |
|---|---|---|
| 入口 | `prod_profile.py` | `harness.py bench` |
| cell | 交付 cell `-p 0 -b 512` | 權威 cell `-p 2048 -b 5632` 的 **tg row** |
| 登記 | `K3_PAIR_CERT_V2_BENCH_2026-09-23.md` | **`scripts/check/charters/exp-k3-pair-cert.yaml`** |
| 為什麼可接受 | — | k 的效應是 kernel 層的（Q_K 家族在 `ne11>=4` 才進 `mul_mv_ext`），機制與 cell 無關 |
| 為什麼仍有缺口 | — | 「換了 k 之後交付數字是多少」仍需要交付 cell 被寫進 §2.5 才能問 |

charter gate 已離線驗證通過（`_charter_gate(c, ['prod-new']) → ok=True`）。
驅動只換了「跑哪一支工具」與「驗 k 有沒有真的送達」兩層，其餘（ABBA、守門、loader、選格）
直接沿用。

**而它仍然沒跑成，原因不在協定：**

```
window: admits=False refused_by=['harness:memory']   reclaimable 6144 < need 8000 MB
```

`NEED_MB` 是 **`server_window` 的共用視窗契約**（`harness.py:1465` 也讀它），不是誰的私有門檻，
所以不該覆寫。機器上**沒有**任何 llama 程序（`pgrep` 空、我啟動的全部收乾淨），大戶是
**使用者自己的應用程式**（WorkBuddy 3.4 GB、Freebuff 1.2 GB、WebKit…）。
⇒ 這是一個「等人清出記憶體」的阻塞，不是技術阻塞。

---

## 3.6 實跑（2026-09-28 14:08–14:16）：鏈通了，撞到的是 **GPU 記憶體**

終於進場，而且每一個閘門都顯示自己的讀數：

```
window: admits=True need_mb=6500 reclaimable=8813        ← owner 授權的妥協
[charter] 立項通過：exp-k3-pair-cert — owner=shared       ← 登記生效
[compressor] quiet                                        ← 起跑閘
  cell 口徑校驗通過（嚴格維度 15 項一致）。              ← §2.5 contract 過
```

而 llama-bench 的命令正是權威 cell：
`-b 5632 -ub 5632 -p 2048 -n 128 -d 512 -r 3 --warm-skip 64 --spec-type draft-mtp --spec-draft-n-max 2`。
**第一支 arm 就量到真數字**（`tg 11.02 ± 1.62 t/s`，1 分 46 秒）——但在 MTP 開著時崩潰。

### D9｜`--spec-type` 沒帶 ⇒ MTP 靜默關著（★我的守門抓到）

第一次呼叫沒帶 `--spec-type draft-mtp`，而 **`--spec-draft-n-max` 在沒有 `--spec-type` 時是惰性的**
（harness 自己的 help 就寫著「需配合 `--spec-type`」）。結果：matrix 命令裡沒有任何 `--spec-type`、
llama-bench 跑的是 **MTP off**、而產物裡 `spec_draft_n_max` 回 **None**。
⇒ **兩支臂量的會是同一個與 k 無關的 kernel**，而表格看起來完全正常。

**この一件就是驅動裡那個「把 k 從紀錄讀回來驗」存在的理由**——它在第一次跑就會回
`arm says k=3 but was asked for k=2` 並 `exit 3`。我當時是**直接呼叫 harness 繞過了守門**才讓它發生（見誠實欄）。

### D10｜✗ **權威 cell 在還台機器上跑不動：Metal command buffer OOM**

加上 `--spec-type draft-mtp` 後，第一支 arm（k=2）**崩潰**（`rc=-6` ＝ SIGABRT），堆疊是：

```
CGC-METAL-FAIL: command buffer 0 failed (status 5, Insufficient Memory
  (kIOGPUCommandBufferCallbackErrorOutOfMemory)) - refusing to return stale output;
  lower Metal memory pressure (expert cache / -ub) or set CGC_METAL_FAIL_STOP=0 to override
0 ggml_print_backtrace → 1 ggml_abort
2 libggml-metal  ggml_metal_synchronize + 816
3 libggml-base   ggml_backend_sched_synchronize
4 libllama       llama_context::synchronize()
5 llama-bench    test_prompt(...)          ← 段 p2048
```

**這是 fail-closed 的設計**（「refusing to return stale output」）——引擎寧可 abort 也不回傳可疑資料。

而 §2.5 的 cell 是 `-b 5632 **-ub 5632** -p 2048`，交付 cell 是 `-b 512 -ub 512 -p 0`
⇒ **ubatch 大了 11 倍**。崩潰前 `rss=8388.7 MiB`、swap 6576 MiB 且在長。

⇒ **認證現在真的只差“盒子跑得動權威 cell”這件事。** 而這也反過來解釋了歷史：
12.57 那個交付 cell（`-b 512`）之所以被選，其中一個原因就是它輕。

**不能用 `CGC_METAL_FAIL_STOP=0` 繞過去**——那條開關的語意是「我寧可拿可能過期的輸出」，
不該用在認證上。

**而它是可重現的**：14:15 一次（t≈9 s，崩前 rss 8388 MiB）、14:16 再一次（reclaimable 升到 **9336 MB**、
比第一次多 2.5 GB，仍然在 **t≈6.2 s、rss 8341 MiB** 崩同一行）⇒ **不是「窗戶不夠乾淨」，
是這個 cell 在這個盒子上就進不去。**

而且**逃生口是關的**：那一行的建議（``lower Metal memory pressure (expert cache / -ub)``）
正好指向 CELL 裡兩個**嚴格維度**（`expert_cache_bytes`、`ubatch`）——降它們就是離開 §2.5，
合約會當場拒跑。而 `CGC_METAL_FAIL_STOP=0` 的語意是「允許回傳可能過期的輸出」，
**不能用在認證上**。

⇒ 這是決定 (a)（在權威 cell 上認證）的**物理阻塞**，而證據指回 (b)：
**交付 cell（`-b 512`，也就是 12.57 那個）才跑得動**——這大概就是它当初被選中的原因。

**驅動的行為是對的**：arm 沒有可用產物 ⇒ `exit 3` ＋ 指名原因，而不是積 8 支空臂。

---

## 3.7 (b) 執行完成：（delivery cell）已宣告且可選，協定首次產出真資料 — **但數字不可用**

### 做了什麼

| 檔 | 改動 |
|---|---|
| `cell_contract.py` | 支援 `cells: {name: {...}}`。新增 `cell_names()` / `resolve_cell()`；**指名不存在的 cell 是 fail-closed，不退回預設**。`Report` 多一欄 `cell`（artifact 必須說自己屬於哪個 cell）。**selftest 11 → 15/15**。 |
| `llama_bench_matrix.py` | 新增 `--cell`，傳給 `check_cell`；`contract`/dry-run 區塊都記下 cell 名。 |
| `harness.py` | `_bench_cmd` 新增 `--cell` 透傳（放在那個「讓 selftest 能證明旋鈕真的到達」的建構器裡），bench 子命令暴露 `--cell`。 |
| `PROD_NEW_TEST_CARD_2026-09-24.md` §2.5.1 | 宣告 `delivery` cell（batch/ubatch 512、prompt 0、ctx 4096、fixed_fill_seed null），附「為什麼有第二個 cell」與「不可互比」的說明。 |
| `exp-k3-pair-cert.yaml` | 補 `non_prod_reason`（gate 自己要求的），並把這次口徑變更**記成登記修正**而不是靜默改掉。 |

### 閘門沒有變鬆（三個方向都驗了）

| 測試 | 結果 |
|---|---|
| 預設 cell（權威形狀） | 通過，`cell=(default)`，**行為與改前相同** |
| `delivery` 形狀 ＋ `--cell delivery` | 通過，`嚴格維度 15 項一致`，`cell=delivery` |
| **`delivery` 形狀而不指名 cell** | **仍拒跑** |
| 指名不存在的 cell | `ContractError`，**不退回預設** |

### 首次真的量到（2026-09-28 14:23–14:28，交付 cell，1 對）

```
cell 口徑校驗通過（cell=delivery，嚴格維度 15 項一致）。
r1.a k=2  rc=0  94s   tg  6.45 ± 0.61   samples [7.05, 6.49, 5.82]   thermal worst HEAVY (19/154)
r1.b k=3  rc=0 165s   tg  7.74 ± 4.62   samples [13.07, 5.24, 4.91]  thermal worst HEAVY (91/167)
```

而且**k 確實送到引擎**：產物裡 `spec_draft_n_max` ＝ **2**（k=2）／**None→3**（k=3）——那是驅動從紀錄讀回來的，不是相信旗標。

### 為什麼這個 pair 不能用

1. **兩支臂都離開 NOMINAL**（`worst HEAVY`），k=3 更是 **167 個樣本裡 91 個 HEAVY**。
2. **k=3 的三個 rep 是 13.07 → 5.24 → 4.91**：第一 rep 還是正常水準，後兩 rep 掉到 1/3。
   那是**量測中崩掉**（熱＋swap），不是「k=3 比較慢」——而 6.45 vs 7.74 的配對差
   （**k=2 較慢**，與 v1 的方向相反）全部是那個崩掉造成的。
3. 把 6.45 拿去和 09-20 的 **12.57** 比是**跨 run 比較**，本文件不允許（而且本輪沒有同 run 錨點）。

⇒ **不是協定的問題了**：協定現在跑得動、看得清、每個數字都帶 cell 名與 thermal 標籤。
缺的是一個**能在量測期間留住 NOMINAL 的盒子**。而這正是 §5.1 早就說的那件事
（launch 級項是地板），現在有了具體形状：**rep 內部的退化比 rep 之間的差大一個數量級。**

**所以沒有繼續跑 r2–r4。** 在一個正在往下滑的盒子上收集 4 對，只會得到四個不可用的 pair。

---

## 4. 建議的下一步（依價值排序）

1. **決定 D8 怎麼走**，這是唯一的牆：
   - **(a) 換入口到 `harness.py bench`**（09-25 裁定指名的生產入口，有 `--spec-draft-n-max`），
     並**重新登記**一份 v3（新的 planned_n、新的口徑聲明）。這會讓 v1 那 5 對與 v2 的計畫
     全部變成 screen —— 但 v2 本來就已經把 v1 降級過一次，同一件事再發生一次是可接受的，
     前提是**寫下來**。
   - **(b) 讓 `cell_contract` 認得交付 decode cell**（把 v2 §3 的 cell 加進權威 block）。
     這動的是別條線的 fail-closed 閘門，需要該線 owner 同意。
   - **(c) 放棄用 bench 認證**，回到機制路線（`pread_usec`／`CGC_S1_TABLE_CHURN`）。
     但 v1 §4 那份 `pread_usec` 配方**也已經過期**（`CGC_RIG_SNAPSHOT` 不存在、
     行號全部位移、而 server 路徑的 env 要走 allowlist）—— 若要走這條，配方要重寫。
2. **若選 (a)**：`k3_pair_cert.sh` 只需要換掉「跑哪一支工具」那一層，其餘
   （ABBA、守門、k=3 逐字同一條命令列、loader、選格規則）都可以直接沿用；
   `k_swing_decompose.py` 要再認第三種 JSON 形狀（`harness.py bench --json`），
   而它的「讀不動就拒跑」會**當場告訴你**形狀對不上。
3. **回頭更正 v2 的正文**（§4 的 `python3`、`--no-ref`、以及它登記的入口已失效）。
   下一條 session 讀到 §4 還是會照抄；工具會擋住，但那要花掉他們半小時才知道是為什麼。
