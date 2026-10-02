---
name: cgc-measure-without-rebuild
description: 在 flashkv-devserver（TurboFieldfare / llama.cpp CGC fork）要用某個 env-gated 儀器跑一次交付 cell 拿數據時，先判定「要不要重建」並安全地跑完。當需要 `strings` 確認 binary 是否已含某個 `CGC_*` 儀器、要把 env 透傳給 llama-bench、或用共用閘門擋住 build 時使用。核心收益：省掉 `cmake --build` 就等於不對共用產物做寫入，因此可以在別條線佔用機器時仍然取得數據。
agent_created: true
---

> **這是快照，不是權威副本。**
> 權威位置：`~/.workbuddy/skills/cgc-measure-without-rebuild/SKILL.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 要改 skill 請改原檔，再重跑 `python3 agent_harness/scripts/import_harness_snapshot.py`。

# 0 重建量測（flashkv-devserver）

多 session 共用同一顆 GPU 與同一份 build 產物 ⇒ **`cmake --build` 是對所有正在跑的實驗的一次寫入**。
所以「跑一次量測」的第一步永遠是：**問這一次能不能不建**。

## §1 先別重建：三步（**按序做，第一步就可能收工**）

### 1.1 儀器是不是已經在 binary 裡 —— 看**內容**，不要看 mtime

```sh
cd /Users/alexchuang/Documents/flashkv-devserver/src/llama.cpp/build/bin
strings libllama.0.dylib | grep -c CGC_IDSEQ_DUMP      # 引擎層儀器在這裡
strings libllama-common.0.dylib | grep -c <KEY>        # common 層的看這裡
strings libggml-base.dylib | grep -c <KEY>             # backend / graph 層的看這裡
```

**⚠ 假陰性陷阱：`libllama.0.dylib` 是 symlink，樹上堆著 8 份同名不同號的 dylib**
（實測見過 `libllama.0.0.{100,182,190,239,275,277,279,578}.dylib`）。
直接 `strings libllama.0.0.239.dylib` 很可能挑到過期那一顆 ⇒ **挑 `libllama.0.dylib`（會跟 symlink）**，
或用 `otool -L llama-bench | grep llama` 確認 binary 實際連結的是哪一顆。

先分辨 layer 可以省兩趟 `strings`：想在 `llama-context.cpp` / `llama-expert-cache.cpp` 的量 ⇒ `libllama`；
`ggml-backend.cpp` ⇒ `libggml-base`；`models/*.cpp` 也算 `libllama`。

**★ 拿不準就總掃一遍，而且一定要掃、不要只挑一顆**（2026-09-24 實例）：

```sh
for f in *.dylib; do n=$(strings "$f" 2>/dev/null | grep -c CGC_SEG_BATCH); [ "$n" -gt 0 ] && echo "$f=$n"; done
```

`CGC_SEG_BATCH`（單段提交，41 段 → 1 段）的 source 在 `ggml-backend.cpp` ⇒ 落在
`libggml-base`（09-24 11:14），**`libllama` 上恆為 0**。只看 `libllama` 會得到假陰性，
進而誤判「要 rebuild」——而 rebuild 正是本 skill 想避免的那次寫入。同一支手臂的另一半
（`CGC_B_SCHEME` / `CGC_SLOT_TABLE_GPU`）卻在 `libllama`（14:58）⇒ **兩半不在同一個 build
也算數**，第一次跑自己看成績是否自洽就好。

### 1.2 確認要跑的那支工具吃得到這個 env

```sh
grep -n "subprocess.run\|Popen" scripts/check/<tool>.py
grep -n "env=" scripts/check/<tool>.py        # **沒有輸出才是好消息**
```

`prod_profile.py` 是 `subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)`
——**沒有 `env=` 覆蓋** ⇒ 子行程繼承 `os.environ` ⇒ **在呼叫它的 shell 裡 `export` 就到得了 llama-bench**。

若目標工具**有** `env=`，改用 `scripts/check/llama_bench_matrix.py` 直接跑（它是 `prod_profile` 的下游），
或用 `--emit-spec` 拿到 pinned knobs 再自己組 —— 但那樣就不能保證與交付 cell 同規格，**優先前者**。

### 1.3 共用閘門：**用 `pgrep -x`，不用 `pgrep -f`**

```sh
busy=0
lsof -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1 && busy=1
pgrep -x llama-server >/dev/null 2>&1 && busy=1
pgrep -x llama-bench  >/dev/null 2>&1 && busy=1
pgrep -f '[d]ecode_sweep\.py|[m]123_oracle_gate\.py|[p]rod_matrix\.py|[d]sp_out/driver' >/dev/null 2>&1 && busy=1
if [ "$busy" = 1 ]; then echo "!! another line is using the machine"; exit 3; fi
```

**★ 本 skills 存在的理由之一：`pgrep -f 'llama-bench'` 會假陽性。** 實例（2026-09-24 18:0x）：
並行送出的另一則 Bash 命令列裡含 `llama-bench` 字串，`pgrep -fl` 就抓到**兄弟 shell 自己**，
閘門印 `BLOCKED` 而機器其實是空的。`pgrep -x` 比對 basename（且不做命令列比對）⇒ 同時躲開
這個誤報與 `ps ... $2=="llama-server"` 的假陰性（本機 `comm` 是完整路徑）。

**★ 守窗自動任務（automation）的 prompt 裡要把上面那段原樣貼進去，而且要在 prompt 明寫
「不要用 `pgrep -f`／`pgrep -fl` 匹配二進位名」。** 實例（2026-09-25 01:1x）：我建了一個
`automation`，prompt 寫 `pgrep -fl 'llama-server|llama-bench|llama-completion'`，結果**永遠假陽性**
——agent 自己那行 shell 命令列就含這些字串 ⇒ 閘門恆為 BLOCKED ⇒ 一次都不會跑，而且**靜默**：
看起來像「窗口一直被佔」，實際是機器空著卻什麼都沒做。自動任務沒有下一次人工介入去發現它。
⇒ 寫守窗 prompt 時：`lsof` 照舊，**行程一律 `pgrep -x <basename>`**（`-x` 只比 comm/basename，
不做命令列比對 ⇒ 不會抓到自己）。

**★ 但 `pgrep -x` 只修了「假陽性」；它還留著一個更貴的坑：單次採樣沒有時間維度（2026-09-26 15:0x 實踩）。**
別的 agent 的 ABBA 有**臂間隙**：兩臂之間 `lsof` 乾淨、`pgrep -x` 全空，**看起來完全像空窗**。
實例：14:58:17 我單次通過就啟動，正好落在對方 `/tmp/armed_fc.sh`（paired MTP on/off）**跑完 off
（`/tmp/frag.log`: `14:57:41 off: OK rc=0`）、正要跑 on** 的那個間隙 ⇒ thermal 1 → **2（HEAVY）**，
等於在別人實驗中段跑，只能中止（kill 自己的 pid）。
**⇒ 閘門必須有時間維度：連續 ≥3 次（間隔 20s）都乾淨，而不是一次。**
**被動可靠信號是 thermal**：`notifyutil -g com.apple.system.thermalpressurelevel` 必須 `== 0`
—— 別人跑 GPU 一定發熱，這**不需要知道對方腳本名**就能用。
⚠ **別指望 `harness` 內建的 thermal gate 救你**：`thermal_pressure.wait_nominal` 是 **fail-OPEN**，
逾時仍 `proceeding`、只把讀數標 `NOT quotable`（`thermal_pressure.py:225-232`）
⇒ 它會把「拿不到窗口」變成「一個不可引用的數字」。要的是**自己補一道 fail-closed**（非 NOMINAL 就 `exit 3`），
且把終態寫成 `REFUSED_*` **而不是 `DONE`**（否則等待器把「拒跑」讀成「跑完」）。
可抄的樣板：`Backup/phase_decomp/cbnmain_nm32/{run.sh,gate.py}`。

### 1.3b ★ 「要不要 rebuild」一律**查產物**，不要靠記憶或對話裡的狀態

本 skill 的名字叫 measure-without-rebuild，但實務上最容易出錯的恰恰是這一步：記憶／日誌裡寫
「**尚未 build**」的那一行，**在 build 真的發生後不會自己更新**。實例（2026-09-25 01:2x）：
日誌 §EN-1305 明寫「改碼完成但 binary 還是舊的」，於是我打算為此 build 一次（而 build 是對
共用產物的寫入，本該盡量避免）；實際查：

```sh
cd src/llama.cpp/build/bin
for f in *.dylib; do strings "$f"; done | grep 'CGC-ZEROMISS\['   # 目標格式字串，命中⇒已有
stat -f "%Sm %N" -t "%H:%M:%S" libllama.dylib ../../src/../src/llama.cpp/src/llama-graph.cpp
```

⇒ `libllama.dylib` 01:14:25 vs `llama-graph.cpp` 01:05:45，且新格式已在、**舊格式已消失**
⇒ **早就 build 過了，不用 build**。

三條規則：
1. **目標字串要用「新版才有的字串」**（例如我把 `CGC-ZEROMISS:` 改成 `CGC-ZEROMISS[%d]:`，
   就查 `CGC-ZEROMISS\[`）。若沿用的舊字串，**新舊 binary 都會命中 ⇒ 查了等於沒查**。
2. **再比 mtime**：產物 vs 你改的那個源檔。產物較新 ⇒ 不用 build。
3. 掃 binary 要**總掃 `*.dylib`**（`CGC_SEG_BATCH` 在 `libggml-base`、其餘在 `libllama`），
   只掃 `libllama` 會假陰性 ⇒ 誤判成「要 build」。

## §1.4 ★★★ 跑之前先確認引擎在正確的 regime：`pool_cap_slots` 指紋

**Metal OOM（`kIOGPUCommandBufferCallbackErrorOutOfMemory`，`rc=-6`）的第一個檢查點不是
`-ub`、不是 `-p`、不是儀器，是 expert cache 有沒有起來。** 2026-09-24 花了十趟 run 才定位。

```sh
grep -o "pool_cap_slots=[0-9]*" Backup/**/*.stderr.log | sort | uniq -c   # 歷史分佈
# 主流是 143。出現 0 ⇒ 引擎不在它該在的 regime。
```

`0` 的唯一成因是缺 `LLAMA_EXPERT_CACHE_ALLOW_NGL`（`-ngl 99` 路徑的總門，
`llama-model-loader.cpp:1113` + `llama.cpp:402`；presence-gated，**跟 `-expert-cache <bytes>` 無關**）。
缺它 ⇒ 13.0 GB 模型整包上 Metal（`recommendedMaxWorkingSetSize 11453 MB`）⇒ prefill／decode 收尾
`ggml_metal_synchronize` OOM。帶它 ⇒ `pool_cap_slots=143`、`L4 metal pool: 143 slots/layer`。

⚠ **全 repo 只有 `deploy-harmonyos/*` 會 export 它** —— `scripts/check/` 下的 runner 一律要自己補。
⚠ 第二層：開了它還需要 `CGC_PREFILL_STREAM=1`，否則 `n_batch` 被夾到 `cgc_pool_max_tokens()`(=8)
⇒ `GGML_ASSERT(n_tokens_all <= cparams.n_batch)`（`llama_bench_matrix.py` 檔頭 20-40 行有寫）。
⚠ 第三層：`-p 2048` 的 prefill 測項在 16 GB 上仍會 OOM；**decode 診斷類的 run 走 `-p 0`**。

## §1.5 ★★ 新增一支 env-gated 儀器之後：**build 要抓 `error`，跑完要驗三件事**

2026-09-26 的實例（新增 mask 成本計時器 `CGC_MISS_MASK_COST`）。三個獨立的坑，每一個都會讓
「儀器看起來在跑、其實沒有」：

**(a) build 失敗 ⇒ dylib 保持舊的，而 mtime 不變 ⇒ 看起來像「根本沒改過」。**
我的計時器用了 `n_tokens`，而 `llama_context::graph_compute(ggml_cgraph*, bool batched)`
**沒有這個變數** ⇒ `error: use of undeclared identifier 'n_tokens'`。
⚠ 我當時的 build 指令是 `cmake --build … | grep -E "error|Built target …"` —— **這個寫法是對的**，
因為只 `tail` 的話，`make: *** [llama-server] Error 2` 會被埋在 7 條 warning 之後。
⇒ 規則：**build 的輸出一定 grep `error`；build 之後一定 `strings -a libllama*.dylib | grep -c '^<新字串>'`**，
    兩者都過才叫「儀器已經在 binary 裡」（＝ §1.1 的前置）。

**(b) 印出來了，但標籤說謊。** 第一版把 `gets`（**次數**，78）印在 `avg_usec total=… sync=… gets=…`
的位置 ⇒ 讀者（包括我）會以為「讀取要 77 µs」。真相是 `read = total − sync`。
⇒ **這與本 repo 當天第 9 個「儀器說謊」是同一類**。規則：**每個欄位的標籤必須寫出它的量綱**
（`ngets=` 是計數、`read_usec=` 是時間），而且**不要用同一個字根同時表示計數與時間**。

**(c) 新增欄位時別把既有格式改壞。** 下游 parser 靠**行內配對**（`miss_rate_summary.py` 讀
`MISSMASK` / `CGC-MISSMASK-STEP`）。⇒ 新增東西一律**另開一行**，並用**行數 1:1** 當判據：

```sh
L=<workdir>/llama_bench_*.stderr.log
echo "rows=$(grep -ac '^MISSMASK' $L) steps=$(grep -ac '^CGC-MISSMASK-STEP' $L) cost=$(grep -ac '^CGC-MISSMASK-COST' $L)"
# 三者相等 ⇒ 既有的兩行沒被改壞，新的那一行也每步都有
```

**(d) 驗「武裝」與「惰性」都要，且不要用別人的旋鈕當開關**（`CGC_VERIFY_DECODE` 會同時開 MTP
fast path ⇒ 兩個效果糾纏、無法歸因）⇒ 見專案的 `MEMORY_HYGIENE.md`「新增一個 env 旋鈕」清單。

**(e) 最便宜的驗法＝一支 smoke**：把新舊儀器放在**同一支臂**上跑一次（省一次窗口），判準是
「新印出現 ＋ 欄位語意對 ＋ 行數 1:1 ＋ 臂存活（`rc=0`）」。**smoke 的 t/s 不可引用**
（若臂本身是單段／garbage 配置，>20 t/s 的讀數依規則一律不引用）。

## §1.6 ★★★ 「same binary ⇒ same behaviour」**只在 artifact digest 覆蓋得到時成立**

2026-09-27 的教訓，代價是一條錯誤的 bisect 結論。若你要用「兩次的 engine/build digest 相同」
去證明「引擎沒變」——**先讀這條**。

- 一個在 **dirty 工作樹**下編出來的 binary，與「乾淨 HEAD 編出的同一個 binary」會被記錄成
  **完全相同的 digest**。tracked 的部分確實在 dylib 裡，**未提交的源碼在任何 artifact digest
  裡都不存在**（既不在 `build/bin`，也不在 `engine_digest()` 裡）。
- 而且「dirty 的路徑」也不夠：它說得出**哪裡髒**，說不出**髒的是什麼位元組**。
- 實例：一次 bisect 看到 `pfx-ckpt2`（09-23 唯一乾淨 9/9）與之後每次 6/9 跑在同一組 dylib
  （`0cd5a628`/`128b6048`/`7099cb53`）⇒ 據此宣稱「引擎無回歸」。**結論是錯的**——差別在那些
  未提交源碼，digest 看不見。

**判準**：只有同時比對以下兩組，才叫「同一個 build」：

| 層 | 覆蓋什麼 | 由誰記錄 |
|---|---|---|
| artifact digest（dylib／launcher 的 md5） | **已提交、已 link 進去的**部分 | `engine_digest()` |
| **dirty 引擎源碼的內容 hash** | **沒進 artifact 的那部分** | `tree_dirty()["dirty_src_hashes"]` |

⇒ 想讓這條推論成立，兩邊都要有；只有 artifact digest 時，它是一個**假設**，不是證據。
（`m123_oracle_gate.py` 已在 09-27 補上後者，見其 `SRC_DIR_PREFIXES` / `dirty_source_hashes()`。）

**附帶**：dirty 的「路徑清單」也常是空的或截斷的（舊版 `tree_dirty` 的 `dirty_paths` 就是空的）。
要比較就比較**內容 hash**。

### §1.7 ★★ 「這段 instrumentation 沒印出任何一行」有兩種成因，先分清再下結論

同一個觀察（0 行）指向兩個完全不同的結論，而第二種最常被誤判成第一種：

| 成因 | 例子 | 該怎麼做 |
|---|---|---|
| **① 進入條件沒開**（缺 env / 缺 arm） | `CGC_S1_DBG=1` 但沒開 `CGC_SLOT_TABLE_GPU` ⇒ `ffn_moe_slots` 根本不建，`cache_slots_out_tensors` 空 | 先找出**誰决定这组 capture 是否被构建**，把它的开关打开 |
| **② 進入了但被 guard 丟掉**（membership / null / 形狀） | leaf-blind：capture 是 `leafs` 而非 `nodes`，nodes-only 測試逐層 false | 找 guard，問「它是不是在考一個錯誤的問題」 |

判讀順序：**先看開關開了沒，再看 guard**。跳過第一步會把「①」讀成「②」，然後去修一個不用修的
東西 —— 2026-09-27 那次差一點就是這樣：真實原因是 ①，而 ② 的缺陷（`cgc_node_in_graph`
只走 `nodes`）一直都在、只是被 ① 蓋住了。

**附帶一條**： reproducing「這條路的第一次輸出」之前，先
`grep -hc "<該前綴>" Backup/cgc_logs/*.log` 確認歷史 log 裡**一份都沒有**。如果一份都沒有，
那麼它從來沒輸出過 ⇒ 任何被認為來自這條路的既有數字都要重新溯源
（例：G2 = 42.93% 其實來自 `scripts/check/decode_sweep.py`，不在這條路）。
反之若歷史 log 裡有，**0 行就等於回歸**，那才是 bug。

### §1.8 ★★★ 引用一個「上界 %」之前，先查三件事（**都可以在 0 GPU 下做**）

任何被寫成「`上界 +X%`／`值得做`」的數字，落地前至少要過這三關。三關都是查得出來的，
但**沒有人會主動去查** —— 它們只體現在「這個數憑什麼是免费的」。

**① 它是量出來的，還是反推出來的？（`推算量` vs `插樁量`）**

`grep -ri "<術語>" src/<tree>/*.cpp` **零命中** ⇒ 它是推算量，不是可插樁的對象。
實例：`barrier ≈ 12.9 ms/step` 是 09-23 從 `cb 23.8` 用兩個常數（`0.46 ms/層` × `~28 層有 miss`）
**反推出來**的固定項，而 `barrier` 這個詞在本 tree 的 `src/` 裡**根本不存在**。
⇒ 於是「`12.9/157.8 ≈ 8%`」是**算術上界**，不是量測到的收益。
判準：先問「我要優化的這個東西，代碼裡有沒有對應的詞」；沒有 ⇒ 先做插樁，別先改代碼。

**② 這個槓桿有沒有「換桶」的前科？**

`n_main=32` 的實測：`union −22.6%`、`busy −48.4%` 過檻，而 `gap`、`tg` 在噪聲底內 ⇒
判定 **總時間守恆**、槓桿判死。**所以在只看 `tpot` 之前，先去看看同一件事有沒有前科。**
判據必須是三重：**①目標量下降 ＋ ②`tpot` 跟著降 ＋ ③別的桶不上升**。缺 ②＝假的；缺 ③＝換桶。

**③ ★ 這個上界是否與護欄綁死？（最容易漏，代價最大）**

拿一個 `+X%` 之前先問：**「拿到它，需要放棄什麼？」**
實例：軸 D 的 `E`（accept／mean len）`+19%`（回到 `mean len 2.02`），算術上等價於
**revert 一個正確性修復**（`65c76b8c7`，那段 diff 註解自陳 accept `0.98 → 0.44`、
mean len `2.02 → 1.70`，**同向下降**）。而那段修復的理由是「MTP draft context 靜默丟失
`ctx_other`"⇒ **要這個 +19% 就得讓護欄受損**，而護欄是護欄不是獎勵。
⇒ 判準：**先把「這個數的代價」寫在紙上，再決定要不要排它。** 很多時候不是「值不值得做」，
而是「**能不能要求你先證明有別條路**」。

**④ 附帶一條環境陷阱（本專案第三次同型）**：引擎 `getenv` 讀了某個開關，但
`scripts/run_server.sh` 的 allowlist **從來沒轉發它** ⇒ 單開那開關的 A/B 必然測出 `Δ ≈ 0`，
而它**看起來像「這個機制無效」**。實例：`CGC_RHO_FILL` 單開是**惰性**的（預測在 HOST 從
captured logits 算 ⇒ `Inert without PROBE TODAY`）。
⇒ **A/B 之前先做「生效性檢查」**：跑一次只有開關、不帶效應的那個臂，確認 log 有蹤跡；
沒有就換開關，不要把它讀成結論。

**⑤ ★ 用閉式公式「反解」某個量之前，先確認公式裡的參數與實測口徑一致。**

反解用的是哪條曲線，決定了反出來的數**差一倍到二倍**。實例：已知 `mean len` 與 `accept rate`，
想反解 accept rate，用了 `E ≈ 1 + r + r²`（`k = 2` 的幾何級數）⇒ 對 `E = 1.70` 反出 `r ≈ 0.50`；
而實測 accept rate 是 **`0.23266`**。正確模型是 **`E = 1 + k·a`、`k = 3`**（交付配方
`--spec-draft-n-max 3`）⇒ `r = (1.70−1)/3 = 0.2333`，與實測差 0.6%。**同一份資料，模型選錯
就差 2.1×。**
⇒ 判準：**拿一組現成的 `(x, y)` 實測對去「驗證」公式**（不是去 fit），誤差應在你宣稱的解析度內
（這個 case 是 ≤0.6%）。對不上就换模型，不要反解。
⇒ 順帶：順手查一下 `generated`／分母是不是 `k` 的倍數（`378 = 126×3`、`447 = 149×3`）⇒ 直接證明
「兩端的 `k` 都沒變，差值全在分子上」。否則你會把「`k` 變了」誤讀成「`accept` 掉了」。

### §1.9 ★★★ 把一條候選量到「交付口徑」上之前，先查它的**因變量在不在**（而且開關要改兩處）

**案例**：`scripts/check/rho_fill_ab.sh` 的臂定義是

| 臂 | `arm_spec` | `arm_spec_flag` |
|---|---|---|
| `base` | `prod-new` | 空 |
| 所有 ρ 系列 | `prod-new:CGC_SERVER_MTP=1`（落 `*` 分支） | `--spec-type draft-mtp` |

⇒ 歷史上所有 ρ 的 A/B（`+14.2%`／`+4.7%` 兩個數的原始樣本）**都在 MTP on 上**，而**交付口徑是
MTP off** ⇒ 「ρ 在交付 cell 的淨增益」從來沒量過 —— 這是**機制原因**，不是「量過是 0」。
順帶同一張表也證明軸 D（`E`／accept rate）**在 MTP off 下因變量根本不存在**（無 draft ⇒ 無
`draft acceptance` 行 ⇒ 無 `mean len`）⇒ **那條槓桿是「除名」，不是「期望值下修」**。

**三條判準**：

1. **先查因變量在不在測那個口徑上**：不是「期待多少」，是「要測的量在那個配置下有沒有定義」。
   因變量不存在 ⇒ 除名，並寫明**復活的唯一條件**（此例＝把 MTP on 變成產品口徑，那是決策不是槓桿）。
2. **加「只在某口徑下有意義」的開關，要改**同一個決策的**每一處**。本例只改 `arm_spec`（切 profile）
   而不改 `arm_spec_flag`（是否傳 `--spec-type`），llama-bench 就會**替我們把 MTP 打開** ⇒
   **一個只看一半的開關比沒這個開關更危險**（它讓你以為量到了 MTP off）。
   ⇒ 守則：一個開關在一個以上的函式裡落地時，**改的時候整套一起改，並在每處寫「為什麼另一處也要改」**。
3. **「從來沒量過」要先找到機制原因，再判死刑**。此例的原因是 `arm_spec` 的 `*` 默認分支為 MTP on
   而設；找到之後開關就能補上。而在補之前，先做**生效性查證**（不跑 GPU，只讀碼回答三個問題）：
   開關是存在性閘還是取值閘？它的擋件在該口徑下會不會把我們的路徑也擋掉？（此例：`:4451` 的
   `ctx_type != DEFAULT` 擋的是 **MTP draft ctx**，MTP off 下主幹 `ctx_type = DEFAULT` ⇒ 不擋；
   `:4559` 的相位閘用 `cgc_is_decode_graph`，decode 步照常通過）
   ⇒ 確認「不是惰性」之後，ABBA 量出 `Δ≈0` 就**只能是效應為零**，不用再懷疑開關。

**附帶的窗口事實**：想跑那一格還被兩道閘擋住 —— `BUDGET_GATE=strict` 因 `pool 8 GiB +
load_mode=none` 靜態超訂 4838 MiB 而 **exit 2**；thermal gate 更硬，**`swap = 4965 MiB` >
`MAX_SWAP_MIB = 2048` ⇒ REFUSE**（09-24 對照點：`swap 5301 ⇒ base 12.57 → 4.62`）。
⇒ 擋住你的如果是 swap／記憶體，**那是物理資源不是設定**：要在結論裡明白寫「阻塞是 swap，
且當下已達 09-24 崩潰值的 94%」，而不是去抬閘硬跑（`BUDGET_GATE=warn` 讓樣本帶標記、
不可當乾淨基線，但**同批次內的配對比較仍有效**）。
⇒ 附帶實測：**swap 會自己回落**（02:47 讀到 4965，62 秒後 02:48 已是 2584）。所以「swap 高所以不能跑」
不代表要重開機 —— 先隔一兩分鐘再讀一次，往往就過閘了。

### §1.10 ★★★ 「這支 A/B 腳本」和「歷史上那個數字」是不是同一支？先查 cell 定義有沒有對齊

**案例**：`scripts/check/rho_fill_ab.sh` 的 `CELL` 是 2026-09-23 寫的（`-p 0 -b 512 --ctx-size 4096`），
而測試卡 §2.5 的 machine-readable CELL 是之後定的 ⇒ `cell_contract.py` **fail-closed**：

```
⛔ cell 口徑與測試卡權威 block 不一致 — 拒跑（fail-closed）：
     - batch 512 ≠ 5632 / ubatch 512 ≠ 5632 / prompt 0 ≠ 2048 / reps 1 ≠ 3
```

⇒ 這支腳本**從寫成以來就沒有跑通過**。更關鍵的是：歷史上那兩個 ρ 數字（`+14.2%`/`+4.7%`）
**是更早別的路徑的產物，不是這支腳本的產物** —— 也就是說「某腳本報的數字」這件事，
要先查「這支腳本上次成功跑通是什麼時候」，再决定能不能引用它。

**三條守則**：

1. **引用一個 A/B 數字前，先查它對應的 driver 能不能當下跑通。** 最快的方法是 `--dry-run`
   （`llama_bench_matrix.py --dry-run` 會把 group 出的命令與 cell contract 檢查一起走一遍，
   毫秒級返回、不碰 GPU）。它過不了，那個數字的來源就要重查，而不是假設「它跑過所以是我的」。
2. **對齊 cell 之後，第一件事是講「與歷史不可比」。** 本例 `batch` 差 11×、`prompt` 0 vs 2048，
   歷史數字與本次**不能同表比較**；本次是第一次在權威 cell 上量的，要在結論裡寫明。
3. **`${VAR:-default}` 的覆寫次序**：我先在下游寫 `REPS="${REPS:-3}"`，但上游那行
   `REPS="${REPS:-1}"` 早已把 `REPS` 設成 `"1"` ⇒ `${REPS:-3}` 取既有值 `1` ⇒
   **命令帶著騙人的 `-r 1` 去跑，再被 contract 擋下，而擋下它的不是我以為改好的那一行。**
   ⇒ 要改預設就改**源頭**，下游留註解「這裡不要再賦值」，並且改完一定要**再跑一次 dry-run**
   看命令真的變成 `-r 3`，不要相信 Edit 的 success 訊息。

### ② 「兩個數字不可比」之前，先確認**維度配接種都對齊**

只對上其中一個維度就宣佈不可比，會把一個**正常的數字誤判成回歸** —— 而且通常是在你已經
花掉一輪量測之後。

實例（2026-09-27，`flashkv-devserver` 軸 R）：base 臂量到 `tg 7.49 t/s`，而錨點是 `12.57`，
我立刻寫「7.49 ≠ 12.57 ⇒ 有問題，可能是回歸」。查下去，**錯在兩處，每處都能單獨致命**：

| | 我以為 | 其實 |
|---|---|---|
| `12.57` 的 MTP 狀態 | MTP off 的錨 | **`--spec-type draft-mtp` ⇒ 含 MTP ON**（原文：「它不是 OFF 參考」） |
| `12.57` 的 shape | 與我們同 | **`--prompt 0`（純 decode）**；我們是 `--prompt 2048`（混合） |

⇒ 兩個維度都不同 ⇒ 不存在回歸；而且**拿 `+14%` 去除 `12.57` 會得到一個沒有語意的比值**。

**對照動詞做三問，一次問完不要分批問：**

1. 它的 **switch 狀態**（開關有沒有開、MTP on/off）和我的樣本一樣嗎？
2. 它的 **shape**（`--prompt`／`--gen`／`--batch`／深度）和我的樣本一樣嗎？
3. 它的 **warm-skip／reps** 呢？（同一個引擎：`-n 128` 讀 7.96、`-n 512` 讀 10.22、
   `warm-skip 64` 讓報告的 `n_gen` 變成 64 —— **三個數，同一個引擎**）

⇒ 「不可比」是**結論**，不是起手式；起手式是「把兩個數的接种（inoculum）列表出來逐項對照」。
同理，**「A 比 B 高 X%」也不能只對一個維度成立就講** —— 务必連開關與 shape 一起引用。

### ③ 「某一趟能跑」是運氣，不是條件

同一天兩次同配置量測，一次通過閘門一次被拒，通常是**環境在當下這一分鐘的狀態**，不是配置。
判準：看**啟動前**的環境值（例如 swap），而不是過程中達到過的最大值。若啟動前恰好低於閘門、
過程中漲到遠高於閘門仍得出好數字 ⇒ 那是**撞上的**，不是可重現的條件；
它在配對意義上或許仍可用（同批次、同污染），但**不能當「該機制的基線」引用**。

### ④ 量測入口：只用唯一對外門，別自己拼命令

**規則**：任何 t/s 讀數只從兩條路出來 —— 通用測量 `harness.py bench --arm "prod-new"`、
commit 前 `commit_bench.py`。**直跑 `llama_bench_matrix.py`／`prod_matrix.py` 是 internal 驅動**，
即使跑通，數字也沒有完整的 cell 口徑標注 ⇒ **「口徑不明」，不可引用**（測試卡 §7 的 2026-09-25 裁定）。

⇒ 代價與 keeping：你自己寫的 A/B 腳本（例如 `rho_fill_ab.sh`）**可以繼續當配對篩選工具**
（同批次內的相對比值仍有語意），但**每個絕對值都要再用對外門重跑一次才可引用**，
否則你會把「internal 驅動的數字」當「生產級數字」講出去。

⚠ 兩個同名陷阱：

- `agent_harness/.../wrappers/bench.sh`（wrapper 入口，四種用法、硬前置 `engine_loop/build.json`）
  ≠ **`scripts/check/harness.py bench`**（测试卡说的对外门）。wrapper 的入參是**相對
  `scripts/check/` 的檔名**：傳 `scripts/check/commit_bench.py` 會被接成
  `scripts/check/scripts/check/...` 並報「不在這一類」。
- wrapper 需要 `agent_harness/engine_loop/build.json`（**可能不存在**，它叫你先跑
  `runners/rebuild.sh`）；`harness.py bench` 不依賴它 ⇒ 量測走後者，別碰前者。

## §2 跑

```sh
cd /Users/alexchuang/Documents/flashkv-devserver
mkdir -p Backup/idseq
export CGC_IDSEQ_DUMP="$PWD/Backup/idseq/<name>_$(date +%H%M%S).txt"
/Users/alexchuang/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
    scripts/check/harness.py bench --arm "prod-new" \
    --json /tmp/harness_decode_$(date +%H%M%S).json
```
⚠ 上面這條是**唯一對外門**（測試卡 §7）。舊的 `prod_profile.py` 是 internal 驅動
（2026-09-25 裁定），它的絕對值**不可引用** —— 只保留作內部篩選。
- macOS **沒有 `timeout`** —— 不要寫 `timeout 120 ...`。
- 先用 `--dry-run` 看解析後的命令與 thermal/mem 閘門（**它自己會重讀 NOMINAL**）。
- `--reps 1 --no-ref` 只為拿 trace 時夠用；**要引用 t/s 就別這樣**（單臂噪音 ±27%，見 §3）。
- 獨立重跑一次（另開 process）比加大 `--reps` 更能證明可重現。

## §3 收尾與判定

- **絕對 t/s 要另外錨**：今天環境常常不是交付錨點那天（實測同 cell 讀過 7.24 與 9.62，對上錨點 12.57）
  ⇒ 「t/s 不可引用，但序列屬性（命中率／union 大小這類）可以」—— 引用時要把這句寫明。
- 結論寫成 dated 檔 `docs/<TOPIC>_<YYYY-MM-DD>.md`，提交走 **`cgc-commit-gate`**
  （含 `Gates:` 段、`RUN_REPLAY_BENCH=0`、`BIN_DIR='src/llama.cpp/build/bin'`）。
- 0 個 `src/` ⇒ D5 數值那一半**不適用**（不用跑 oracle gate），但要在 message 裡寫明理由。
- raw trace 放 `Backup/`（**被 gitignore**）⇒ 也要在結論檔裡交代它的路徑與重現指令，
  否則下一輪查不到證據本體。

## §4 相關

- `cgc-decode-attribution`（decode 相位歸因）
- `cgc-prefill-thermal-delivery`（prefill t/s 的條件式交付）
- 實測範本：`docs/H_MEASURED_A_VERDICT_2026-09-24.md`（§EN-509，量 h ⇒ 方案 A 判死）
