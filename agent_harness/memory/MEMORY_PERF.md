# MEMORY_PERF — profile／模型／幾何／速度／散熱

> **這是快照，不是權威副本。**
> 權威位置：`.workbuddy/memory/MEMORY_PERF.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 索引與漂移檢查見 `agent_harness/engine_loop/memory/INDEX.jsonl`。

> **這是 `MEMORY.md` 的主題分檔（2026-09-17 拆分），不是歷史存檔。** 動手前的規則、指令入口、
> 索引導覽在 `MEMORY.md`。**要引用任何 t/s、profile、幾何、散熱數字之前讀本檔** —— 本專案的
> 「decode 速度」有四個互不相容的定義，不指名就一定會引用錯。

## profile／模型／幾何

- **統一 profile（09-16 定案）＝ `prefill250` ＋ `CGC_SPAC=1`（alpha 0.75）**，同時服務 prefill 與 decode。
  依據：`CGC_POOL_MAX_TOKENS`（`llama-graph.h:18-37`，預設 8、可調 [2,64]）的註解把「MTP verify batch」
  綁在它上面，夾具只在 `!cgc_prefill_stream` 時生效 ⇒ `prod25` 把池路徑鎖在 8，而 **M1／M4 的槓桿住在
  寬 batch（whole-layer slab）**。
- **SPAC=1 的證據**（`Backup/run_unified_ab.sh`，交錯、**09-16 21:47 第二次獨立確認**）：ctx 8192 的 d512
  上**均值 +16.5～17%**（9.21 → 10.73；與 08:48 的 9.25 → 10.85 互在 2% 內）、**離散 ±2.97 → ±0.04**
  （§EN-4／§EN-5）—— **主效果是移掉不穩定，均值增益是附帶的**。
  - ⚠️ **alpha 的值還沒有交錯證據**：C2 的循序 α 掃描（a050/a075/a090）已被 21:52 的 A/B 判為受順序／
    熱污染（它給出 +6.2%，真值是 +16.5%）⇒ **四個 arm 的 α 排序全部不可引用**；要選 α 得做**輪轉式**掃描。
  - ⚠️ **`run_unified_ab.sh` 的 A 臂必須寫 `prefill250:CGC_SPAC=0`**：profile 自 20:33 起自己會設
    SPAC=1，裸 `prefill250` 會讓 A≡B 而讀成「SPAC 沒效果」。
- **量測形狀**：prefill 用 profile 的 `-b/-ub 5632`；**decode／depth 矩陣用 `-b 512`**（5632 於 `-d≥512`
  在 16 GB 會 OOM）。
- **模型**：`prod25` → `Nail-…-MTP-…-denseIQ4X.gguf`（13.6 GB）；**`CGC_SERVER_MTP=0` 換成非 MTP 的
  `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`**（`run_server.sh:154`）⇒ **引用任何數字都要指名模型**。
  **不是 Gemma 4 26B-A4B**（更早的設定，忽略）。家族 `qwen35moe`、41 blocks、`full_attention_interval=4`
  ⇒ layer 0/1/2 是 gated delta-net，**layer 3 才是第一個 full attention**。
- **幾何**：pool 8 GiB → 143 slots/layer；`LAYER_CAPS 40-40:256`；CTX 4096（prefill250 8192）；
  `SPEC_DRAFT_N_MAX=3`。三支柱 bit-identical：`CGC_MM_BITIDENT=1`／`SERVER_MTP_NO_WARMUP=1`／
  `SERVER_NO_SEQ_RM_PROBE=1`。**池壓力熱點層會變**（layer 0 與 layer 2 都出現過）。

- **★ 2026-09-20 交付 decode 的權威讀數（凍結）**：profile＝**`prefill250`** —— 它**就是**「prefill250 ＋
  最好的 decode」，逐旋鈕同一性見 `docs/PRODUCTION_PROFILE_2026-09-20.md` §3（零 GPU 比對，解析後 env 差異＝空）。
  **交付 decode＝ 12.57 t/s**（`NOMINAL` **全程**、±2.26；`Backup/prod_profile/prod_profile_20260920_1230.json`）
  ⇒ **這是「下一個 agent 要打敗的數字」**。既有帶：13.10／10.94（09-19）、10.78／10.91／10.98（instrument of record）。
  - **交付口徑＝四個 bench 約定**：`--batch 512`、`--ctx-size 4096`、`--warm-skip 64`、`--spec-type draft-mtp`。
    ⚠ **`profile_duo.py`／`prod_matrix.py` 的 `decode` cell 表達不了它**（沒有 spec／warm-skip／ctx）⇒ 同一 profile
    在那兩支工具裡只讀 **7.96–9.12**，比交付低約 **1.4×**。**要引用交付 decode，一律用 `scripts/check/prod_profile.py`。**
  - **★ 單臂噪音底 ≈ ±27%**：**同一個命令**、同一次 session 讀 9.90 與 12.57（更低的那次 `worst=MODERATE`）
    ⇒ **小於 ~27% 的效應，單臂前後對比證明不出來**；只能靠配對交錯 A/B（第一步 `--null`）。
  - ⚠ **`±2.26` 是 stdev 不是 CI**（`llama-bench.cpp:2453` 印 `stdev_ts()`＝reps 樣本標準差，reps=3）
    ⇒ 單次 rep sd＝**18% of mean**，而 **12.57 的 95% CI ＝ [6.96, 18.18]，半寬 ±45%**（df=2）
    ⇒ **12.57 只能當配對比較的一臂，不能與另一次 anchor 的單點數字比**。
    ⚠ 12.57 是 **`anchor_arm=prod25-stream`** 那一臂；同 json 的 `prefill250` 臂是 9.90±1.25（同形狀差 21%）。
  - ⚠ **「llama-bench 離散只有 1.1%」是 09-17 那次場合的數字，不是交付 cell 的性質**（交付 cell 實測 18%，
    與 server 口徑同量級）⇒ 別再用它推論「bench 上要的對數比較少」。加 reps 也救不了：
    k=3 有 **launch 級項**（13.16%），同 launch 內 reps 共享 ⇒ 配對 sd 有地板 `√2·σ_launch`，
    reps 3→12 只把需要對數從 11 降到 9 ⇒ **要檢出力就加對數，不要加 reps**（詳 `docs/K3_PAIR_CERT_V2_BENCH_2026-09-23.md` §5.1）。
  - **prefill 的 250 bar 在 09-20 未驗證**（不是退步）：三次 188.12／212.59／222.40，全部 `worst ≥ MODERATE`、
    全部 < 250；而 09-16 的判準是「Nominal 6/6 全部 ≥250（最低 253.42）；**非 Nominal 0/21（最高 211.65）**」，
    本日三次都落在非 Nominal 側 ⇒ **不可引用**。
  - ⚠ **`wait_nominal` 可以立即回 0（`waited 0s`）而底盤仍然熱**（第三次 prefill 重跑即如此）
    ⇒ **NOMINAL 是必要條件，不是充分條件**；重跑大臂中間的空閒要按**分鐘**計。
## 里程碑現況（M0–M6；**09-17 查證**，不是憑記憶）

- **M0 量測能力：完成。**
- **M1 解耦 pool 與圖：五個工作項實作了四個（第 1 個判 EXPERIMENTAL 不可用），但★ 五條離開條件
  沒有一條被系統性重核 ⇒ 現在的瓶頸是「驗收」不是「實作」。**
  （★ 09-17 18:2x 改寫：先前寫「做了一半以上、卡住」，那句在 `605452177` 之後已經不準 ——
  工作項 2/3/4 都已實作並提交，卡住的是離開條件，細節見下面 §離開條件的更正。）
  **★ 09-17 15:45 狀態更新：別條線已經開始做工作項 4（canonical gather order）** —— 新增
  `src/llama.cpp/src/llama-cgc-canon.h` 與 `scripts/check/canon_order_selftest.cpp`，並在
  `scripts/run_server.sh` 的 allowlist 加了 **`CGC_CANON_ORDER`**（`=1` 依 expert id 排序、
  **`=2` 是 identity 對照**，用來把「重排改了數字」與「多出來的節點改了數字」分開）。
  他們自己在註解裡寫明：**兩個模式都會改變模型輸出，所以都不能當品質或 D5 證據**
  （對某個在別的 mode dump 的參考）。⇒ 這正是交接白皮書 §4 建議的起手式，**已接走**；
  本線不碰 `src/`。
  **★ 09-17 17:0x：工作項 2＋3 也實作並驗證了**（`docs/M1_WORKITEM2_PHASE_SPLIT_STATUS_2026-09-17.md`）。
  可引用的**持久事實**（這句話是判準，不是進度）：
  - 相位判定式在 `src/llama-cgc-phase.h`，**兩側共用**：
    `cgc_decode_width(slots, top_k, cap) = min(cgc_decode_bound(slots, top_k, cap), T_prefill-1)`，
    `cgc_select_graph_phase(n_tokens, w) = n_tokens <= w ? DECODE : PREFILL`。
    真實幾何下 `bound = floor(routable_slots / top_k) = floor(142/8) = 17`。
  - ⇒ **而這有一個直接後果：decode（T=1；MTP 也只 2–4）恆 ≤ 17 ⇒ 恆走 DECODE
    ⇒ whole-layer slab 永不參與 decode。**
    **slab 是 prefill-only**（`run_server.sh:1618-1620` 原文：*Decode stays on the pool path*）。
    ⇒ **任何「armed slab vs pool」的 decode A/B 是 by construction 的零差異**，
    那個零**不可以**被讀成「slab 沒有收益」。要量就量 prefill。
  - **未 arm 時 prefill 同時吃 `n_batch` clamp**（`[arm] OFF` 行自證）⇒
    「armed vs unarmed」在寬 chunk 上是**兩個變數**（slab＋clamp），不是單變數。
  - `cap` 已**降級**成 decode 圖的寬度上限（不再是相位開關）：`cap=8 → width 8`、
    `cap=64 → width 17`（被 bound 夾住，不是 64）。
  - gate：`Backup/m123_oracle_gate/summary_phase_p2.json`（17:00:27）`comparable=True`、9/9/9；
    真實 cross-tab ＝ `{eq_eq:9, eq_ne:0, ne_eq:0, ne_ne:0}`。
  - ⚠ 同批的 `r52_canon1_pool4gb`／`r53_base_pool4gb` 是 **`comparable=False` 而 M1/M2/M3 仍印 9/9**
    ⇒ 又一個「先讀 comparable 再讀判決」的活例（他們沒引用它們，處置正確）。
  數值那一半 **09-14 已達成**：2/4/6/8/10 GiB 全部 **M1 = M2 = M3 = 117/117**，2 GiB 是唯一
  `union > slots` 那格（compacted gather），`union-routable` PASS，RSS 達標。
  **⚠ 證據地位（09-17 更正）**：這個 117/117 是**跨池不變性**，**不蘊含正確性** ——
  別條線的 r33 nb-aware 修正（`docs/ROUTING_TRACE_2026-09-17.md` §12.1／§12.4）證明
  **每一臂都把 T≥2 的 token 路由到 token 0 的專家**，而「每一臂都犯同一個錯」正好讓跨池
  不變性通過。該檔原文：*cross-pool invariance (M1/M2) passed while the ids were wrong for
  all of them*。⇒ M1 的數值半應改述為「**不變性**已達成」；要主張「數對」必須用 r33 之後的
  參考（v6_nbaware）。**兩條沒過**：
  ① `decode 不得退步` ⇒ 2 GiB（gather＋slab）**6.36** vs 8 GiB（pool）**8.87 t/s** ＝ **0.72×**；
  **⚠ 這條的基準要重定（09-17 15:0x）**：8.87 是 09-14 的 build，而今天同類量測（Nail、8 GiB、MTP off）
  是 **9.82**（`ROUTING_TRACE` §14.2），且 r33 修正改動了 routing（hit 57.1→72.0）⇒ **0.72× 這個比值
  跨了 build，不能直接沿用**；要重跑 M1 的 2 GiB 格對**當天**的 8 GiB 格比。
  ② `prefill chunk 2048` ⇒ 當初缺工作項 2。**工作項 1**（`CGC_POOL_SPLIT=1`，保持 expert tensor 全寬）
  實作了但被判 **EXPERIMENTAL, NOT USABLE**：Blocker A（寬 tensor 讓 gather 把 Metal buffer 的指標
  重指到 Metal 不知道的 host 指標 ⇒ **靜默** `tensor buffer is nil`、**M1 2/42**；且 16 GB 上
  warmup OOM），Blocker B 已於 **09-16** 修好。
  **★ 09-17 18:2x 更正（本行先前寫「工作項 2 未實作」，那句已經過時且與上面 17:0x 那段矛盾）**：
  **工作項 2 與 3 已實作並提交** —— `605452177 feat(moe): decide the build graph from the request
  phase, and demote cap to a width ceiling`（`src/llama-cgc-phase.h`：`cgc_decode_width()` ＋
  `cgc_select_graph_phase()`，兩側共用）。⇒ ② 的處置從「缺實作」變成「**要重測**」。
  **★★ 而五條離開條件沒有一條被系統性重核（這是 M1 現在真正的缺口）**：
  - **「M1/M2 @ 4/6/8/10 GiB = 100%（含 `union > slots`）」** —— 今天 **75 筆 D5 裡
    `comparable=True` 的全部是 8 GiB（46 筆）**；4 GiB 只有 `r52_canon1_pool4gb`／`r53_base_pool4gb`
    **兩筆，且都是 `comparable=False`**（池預算 4 GiB ⇒ `.cap` 的 `CGCENV.BUDGET` 與 8 GiB 參考不符
    ⇒ 不可比）。⇒ **phase split 之後，多池尺寸那條從未被重新確立**；要它就得像 09-14 那樣
    **每個預算各建一份參考**（那次是 117/117）。
  - **`prefill chunk 2048`** —— 他們的 §A 只證了 35-token 與 12-token 兩個**單點**（舊述詞會誤路由
    的那格），**不是 chunk 2048**。
  - **`RSS @ 10 GiB` ±0.5 GiB vs 9.08** —— **沒有任何一筆讀數**。
  - **`union-routable` PASS** —— 09-14 的結果，**未在 phase split 之後重跑**。
  - **`decode 不得退步`** —— 見 ①，基準本身要重定。
- **M2 prefill 整層串流：核心機制已落地**（`CGC_PREFILL_STREAM=1` ＋ `CGC_GATHER_SLAB_CAP=256`，
  `prefill250` 用它跑到 250+）。**★ 09-17 17:0x 五條離開條件已逐條審計（本線，`docs/M2_EXIT_CONDITIONS_AUDIT_2026-09-17.md`）
  ⇒ 3 PASS / 0 FAIL / 2 未裁決**：條件 1（bytes/token @ chunk 2048 ≤3.0 MB）PASS（靠引擎自印的
  `slab fills` 非駐留 44.1%，`11.92 GB × 0.441 ÷ 2048 = 2.57 MB/token`）；條件 4（prefill t/s 誠實記錄）
  PASS **但附否證**（15:37 的 `COLD-STATE`、安靜 1940 s 那臂反而只有 242.42／227.27／249.06，
  見 lesson `eng-mh-0054`）；條件 5 PASS（D5 9/9）。**條件 2（裝置持續 ≥1.0 GB/s）與 3
  （wall < I/O + compute）＝ 未裁決，不是不合格** —— `grep slab_*us/ms/time` 在 `src/` 裡**零命中**
  ⇒ 引擎沒有 slab 計時計數器，這兩條**沒有儀器**。儀器設計寫在該文件裡，**且都不需要動 `src/`**。
- **M3 decode 的 compute 削減：判定書已出（尚未實作；依賴 M1）**。
  （★ 09-17 18:2x 改寫：先前寫「未開始」，但當天已產出 `docs/M3_VERDICT_2026-09-17.md` 並**建了它缺的儀器**。）
  兩個探針試過且**不可引用**：`CGC_MMV_FUSE`
  （MoE gather 融合）輸出損壞且更慢；`CGC_SUBMIT_AHEAD`（序列化）天花板 ×1.711 但輸出損壞。
  離開條件「decode（MTP off）≥ 15 t/s」未達。
  **★ 09-17 17:1x 判定（`docs/M3_VERDICT_2026-09-17.md`）：它與 D3 的 S2／S3 是同一件事。**
  今天 10.78 t/s ＝ 92.8 ms/token。兩個目標要分開：**離開條件 15 t/s 只要 1.39×，
  目標 100→40 ms 要 2.32×**。判決：
  ① **host 側正式關閉** —— `CGC-PHASE` 實測 `build 0.023 + alloc 0.037 + inputs 0.026 = 0.086 ms/步`
     ＝ **0.07%**，而 `compute=118.459`（**99.9%**）、`fill_wait=0.000`（IO 不在關鍵路徑）
     ⇒「減少圖重建／配置／IO」三條路**沒有量**（三個數量級差 ⇒ 對熱不敏感）；
  ② **去序列化的空間存在**（`SUBMIT_AHEAD` 實測 82.5→31–44 ms ⇒ 38–51 ms/步，
     足以讓 15 t/s 擦線過），**但步級三分（cb 8–10% + submit 4% + gap 19–36%）只能解釋 11–29 ms**
     ⇒ **差額 9–22 ms 住在 `wait` 的儀器盲區裡**；
  ③ **M3 缺的不是路，是儀器** —— 兩個既有儀器都**拆不到 T=1 的層內**：
     `CGC_PHASE_TIMING` 把整步歸成 `compute`（`compute == gpu`，是副本不是 GPU 鐘）；
     `CGC_VERIFY_OP_TIMING` 的目標判定含 `node->src[2]->ne[1] > 1`（`llama-context.cpp:3830`）
     ⇒ **只在 T>1（verify）生效**，對 MTP off 產生不了輸出 ⇒
     `debug-verify-path-breakdown.md` 那組 gate/up/down（0.8/0.8/1.0 ms）**不能移植到 T=1**。
     唯一活路是**逐節點 GPU 時間**（Metal counter sample buffer），成本最高。
  ⇒ **判準（先寫死）**：若逐節點 GPU 時間顯示 `ffn_moe_*` 佔 `wait` ≥40% ⇒ Cell 2 有量；
  < 15% ⇒ 三條路全不足，走「找不到路」分支。**沒有判準就不要實作**（`CGC_MMV_FUSE` 的學費）。
- ⚠ **[2026-09-24 20:56] 「MTP ×1.28（9.82 → 12.62）」在本線交付口徑下不可再引用。**
  實測（同 cell prod-new／b=ub 5632／ctx 0／**warm-skip 64**／r3／NOMINAL，唯一變數＝儀器）：
  **MTP off 乾淨 = 12.05 ± 0.57 t/s**，已貼近交付 anchor 12.57 ⇒ 本口徑下 MTP 只剩 **+4.3%**。
  9.82 vs 12.62 是**無 warm-skip 的舊口徑**（HTTP／`llama-speculative-simple` 路徑），
  與 cold-start 混雜；舊口徑的單臂 sd 是 **±3.54（35%）**，warm-skip 後掉到 **±0.57（4.8%）**
  ⇒ 那 1.28× 裡有冷啟動成份。**MTP 增益必須在 warm-skip 口徑下重測**（開 spec 後母體換 ntok=4）。
  同趟副產物：**儀器（5 個 CGC_* env）代價 = +1.6%，在噪音內 ⇒ 儀器幾乎免費**；
  「某數字不可交付是因為帶儀器」這個理由**不成立**，不可交付通常是**口徑**
  （median×1000／DECPROF step total 只含 layer loop，比 llama-bench tg 高 ~13%）。
  乾淨臂 cache：hit 93.1%，**miss_compulsory 3167 vs miss_capacity 93** ⇒ 池容量不是瓶頸。
- **M4 MTP 拒絕取樣 ＋ verify 真批次：核心數字已達成（09-17 14:2x，別條線），但離開條件之一被證明是錯的判準。**
  同 binary A/B（build 14:12，carrier `nail`，pool 8 GiB，`n_predict=96`，每臂 3 請求）：
  **MTP off 9.82 t/s｜MTP on 修前（`CGC_IDS_LINEAR_READ=1`）8.62 t/s（accept 73.81%）｜
  MTP on nb-aware 修正 12.62 t/s（accept 58.25%）** ⇒ **MTP-on ≥ MTP-off 達成（+28.5%）**，
  修前是淨負。★ **accept 下降而吞吐上升 46%**：修前 verify 批次的 token≥1 用別的 token 的專家算
  ⇒「accepted」是拿錯分布比出來的、而且**被抬高**。⇒ **「accept ≥ 60%」這個離開條件要作廢**
  （改成「MTP-on ≥ MTP-off ＋ M1/M2 = 100% with MTP ON」），並且**修前記錄的每個 accept 數字
  （含 09-14 的 70.4%、roadmap 的 19.9%）都屬於被抬高的 regime**。全文 `ROUTING_TRACE` §14。
  **⇒ 今天補上的正好是 09-14 就指名的那唯一缺口**：`docs/MTP_HEAD_PROVENANCE_GATE_2026-09-14.md` §5
  早已寫「Nail 配對已達 70.39%，超過 M4 的 60% 離開條件；**該配對仍未過的是 `MTP-on ≥ MTP-off`
  （6.74 < 8.87）**」⇒ 今天 12.62 ≥ 9.82 ⇒ **M4 的功能缺口關閉**。仍未處理：`plain_match=False`
  （batch verify 與逐 token 解碼不一致；同一份文件 §5 第 2 點）與「M1/M2 = 100% with MTP ON」。
- **M5 prerouter 只當預取提示：未開始**（可選、期望值低）。**`PREFETCH_ONLY` 在本 repo 0 筆。**
- **M6 換量化幾何：頭條目標已達成，剩下的只有「一個綁定層」**（09-17 12:1x 實測；
  全文 `docs/M6_QUANT_GEOMETRY_PLAN_2026-09-17.md`）。
  **★ roadmap 的 1.769→1.122 MB 與「6 GB pool 85→133 slots」指的是 Edge0-int4，不是出貨模型。**
  出貨的 `Nail-…-denseIQ4X.gguf`（12.72 GiB）實測：41 層 × 256 experts，**典型層 274 MiB
  ＝1.0703 MiB/expert（＝roadmap 的目標值 1.122 MB）**，但 **blk.39 是 356 MiB＝1.3906 MiB
  （gate/up 是 IQ3_S、down 是 IQ4_XS，其餘 38 層是 IQ2_S/IQ3_S）**，而
  `capacity = clamp(budget/(41×per_slot), 8, 256)` 取 **MAX over TRUNK layers**（NextN/MTP 被跳過，
  但 `denom` 仍數它 —— `llama-model-loader.cpp:1171-1174`）⇒ **一層厚、全班付錢**。
  現行 `BUDGET_DEFAULT = 10 GiB` ⇒ **179 slots**；`run_server.sh:411-419` 自記 **143 slots 時
  hit 90.8%**（counterfactual K=96 79.7／128 87.7／192 97.1／256 100）⇒ **hit 早已超過 roadmap 的 84%**。
  **★★ 決定（09-17 13:0x，使用者裁定）：只走無損的設定路線，不動模型。**
  **★★ 裁定（09-17 15:0x，閘門 4 之後）：設定路線「不可交付」，不是「機制被否證」。**
  同負載實測（`docs/M6_…md` §4.9）：均勻 143 的 **capacity 佔 miss 的 45.6%**，但 **miss 只佔抓取的
  3.16%** ⇒ capacity ≈ **1.4% 的抓取**；而 `--slack 256MiB`（薄層 149）**消不掉它** —— 引擎自己報
  **最差層 layer 2 的 distinct ＝ 239 > 149**、且 **9 層 distinct > slots**。
  ⇒ **代價不是 +238 MiB，而是 cap ≈ 240–256（+2 GiB 等級）**，而吃緊的形狀連 +238 MiB 都已在
  MTP-on 上**請求即 GPU OOM**（§4.8 ③）。上限反推：這個負載（hit 96.8%）只有 **≤ +0.7%**；
  用生產負載的 hit（89.2%）反推是 **≤ +2.5%**。
  ⇒ **機制存在、上限存在、代價落在買不起的一側**；而且別條線的 r33 修正已把 hit 由 57.1% 抬到
  **72.0%（與池無關）** ⇒ 一部分本來要 M6 買的東西已被「修路由」買走。
  **能復活的三條路**：① 加預算（> +2 GiB）；② **改淘汰／填充策略**（不靠更多 slot 提高複用 —— 唯一
  不需預算的路）；③ 改目標（少讀本來是 M2 的地盤）。另：M6 的**本體**（per-expert 1.769→目標 1.122 MB）
  **早已在出貨檔裡**（實測 1.0703 MiB）⇒ 頭條價值已兌現、未被推翻。
  **設定路線＝一條既有 env 字串，零程式改動、零重建、零 D5**：`LLAMA_EXPERT_CACHE_LAYER_CAPS`
  是**雙邊讀取**的（loader 的 `ne[2]=cgc_layer_cap(il,cap)` @`llama-model-loader.cpp:1492/1502`
  ＋ cache 的 `n_slots_l` @`llama-expert-cache.cpp:3136-3142`），而 **`run_server.sh:1804-1807`
  每次啟動都寫它**（預設 `40-40:256`）⇒ 覆蓋只要 `CGC_SERVER_LAYER_CAPS`。
  10 GiB 下的字串：`0-33:232;34-34:212;35-37:232;38-38:212;39-39:179;40-40:256`
  ⇒ **37 個典型層 179→232（+29.6%）**、池由 **7.85 GiB（78.5%）→ 9.97 GiB（99.7%）**
  （今天有 **2.15 GiB 預算買不到任何 slot**）。6 GiB＝`0-33:136;34-34:125;35-37:136;38-38:125;39-39:107;40-40:256`、
  8 GiB 把 136/125/107 換成 184/168/143。
  產生器：`python3 scripts/check/gguf_pool_geometry.py --layer-caps`（規則＝每層等位元組，
  **夾在今天的 uniform 值之上** —— 太少 slots 會**靜默讀到空**：Edge0 的 33 slots 是 585 次
  `buffer is nil`、48 題 0/48）。
  **★ 逐格驗證（引擎自己就印普查行 ⇒ 驗證是一行 grep）**：`LAYER_CAPS per-layer caps: total N slots
  (avg A/layer, min M/layer)`，`N = 40×base + 256` ⇒ Nail 8 GiB `5976/145.8/143`、10 GiB `7416/180.9/179`、
  **Ornith 2 GiB `1416/34.5/29`、4 GiB `2616/63.8/59`** —— 工具與引擎**逐格相同**；
  `resident` 亦相符（179→7990.71 MiB、143→6430.6 MiB）。
  **★ 仍未量測**：逐層變動的 trunk caps **從未跑過**（用過的只有 `1-39:32` 與 `40-40:256`）
  ⇒ 四道依序：普查行 → 48 題（基準 **9/48**）→ `m123_oracle_gate`（換池幾何**不該**動 logits，
  M1/M2/M3 應 9/9）→ 才談 hit%／t/s。而 **hit% 在這個區間由負載主導**（真實 log：179→87.9%（18.9 萬請求）、
  143→86.7%、71→44.9–82.8%）⇒ **+29% slots 的收益要量不要推**。
  **MTP 的 cap 與預算無關**：`40-40:256` 固定 256 個 slot，Ornith（Q8_0 頭）在 2 GiB 下它吃掉
  0.80 GiB＝40% ⇒ 今天的形狀在 2 GiB **已超支 118.5%**（要調就用 `--mtp-cap`）。
  **★ 模型路線（拉平 blk.39）已否決**：`gen_denseiq4x_tt.py` 的政策是「**每個非 dense 張量釘住它現有的
  型別 ⇒ byte-copy**」⇒ **expert 型別是從上游 `UD-IQ3_XXS`（UD＝Unsloth Dynamic）逐位元組繼承的**，
  厚的那幾層是**逐張量重要性校準的結果**；拉平＝**有方向的降精度**（gate/up −25.5%、down −19.1%），
  而且只換到 +19%（單層）／+30%（4 層）—— **不比設定路線多，卻要付精度代價**。
  （模型工具與記錄仍在：`scripts/gguf_retensor.py` set-type／restore／verify／digest，dry run 是預設；
  `--normalize-binding` 的那三行情境；**`verify`／`set-type` 必須用 `/opt/homebrew/bin/python3`**，
  系統與 managed python 都沒有 numpy ⇒ gguf-py 對帳 SKIPPED ⇒ 直接拒收。
  roadmap 指名的 `scripts/verify_edge0_gguf.py` **在本 repo 不存在**。）
  **幾何 census ＝ `scripts/check/gguf_pool_geometry.py`**（09-17 建；純讀標頭、不需 numpy、
  可在別人量測跑著時執行）；`analyze_pool_geometry.py` 仍留著但那是為 Edge0 寫的、需要 numpy。
- **★ D3（`REMAP_ROUNDTRIP_REMOVAL_PLAN` 的主設計：把 expert→slot 查表搬到 GPU、消除段邊界）
  **★★ 09-20 07:2x 更正（原句保留在下方，原文一字未刪）：S1 的 bit-identical 閘門**已經過了**。**
  同一天在交付配置上量到：`CGC_SLOT_TABLE_GPU=1` 且**不開** `CGC_S1_DBG` ⇒ gate **PASS**
  （`comparable=true`、`config_diffs=[]`、M1/M2/M3 各 9/9、`zero_mapped_selected=0`）；補丁後
  同一顆 binary 再量一次仍 **PASS**（`summary_s1-postfix.json`）。⇒ 下方
  「**已實作且會跑，但不過 bit-identical**（錨 `2d4e5099` vs S1 `f0acf7d2`）」與
  「⇒ **D3 無對外數字，且仍依賴卡住的 M1**」的**前半**已作廢（S2 因此沒有數值前置阻塞）。
  而 07:00 那份「S1 會在交付配置下 abort」的讀數是**探針自己的 bug**，不是 S1 的 ——
  根因 `docs/S1_DIAGNOSTIC_ABORT_ROOT_CAUSE_2026-09-20.md`、lesson `eng-diag-0037`。
  ⚠️ **「過了閘」不等於「有速度」**：S1 仍然**無速度主張，而且結構上不可能有** ——
  見 `MEMORY_S1.md:36`（`expert_cache_on_topk` 在**兩臂都跑**，S1 從未移除任何 host 步驟；
  **段數不變**），且 S1 臂**預設關閉**（`run_server.sh:1467`）⇒ 對出貨速度無影響。
  ⚠️ **「19 t/s」是 S2 的下界，不是 S1 的。** 出處：`2026-09-20.md` §EN-304(2) 的表
  （暖態 `mean_len 2.40`、現況 17.2 → S2 完美 **22.9**／`39×0.32` 不可約 20.5／
  **只有 `submit` 能搬（`cb` 留在關鍵路徑）19.0**），而**它是從 139.46 ms 的步分解推算的，不是量測**。
  可交付的 llama-bench 讀數是 **7.7–10.8**（最好一次 13.10）⇒ **不要把 19 搬到 llama-bench 口徑上**，
  也不要用它當 S2 的期望值（同表自註：交付配對那一欄是 HEAVY，是下界）。
  不是里程碑，是 S1→S2→S3 階梯；現況（09-17 03:3x 查證）：沒跑通。**
  **S1**（`CGC_SLOT_TABLE_GPU=1`，leaf 改由 GPU 算、**段數不變**）**已實作且會跑，但不過
  bit-identical**（錨 `2d4e5099` vs S1 `f0acf7d2`）；分歧＝**第一個被 GPU table 服務的層的 MoE gather**
  （層號由 `CGC_S1_MIN_IL` 決定）。**S2（段邊界去等待）／S3（收段 `n_segs 40→~1`，＝那 ~44%）
  從未開始**，且 **S3 的前提 B 已倒**（47.5% 的步會動被消費的映射 ⇒ 發布不冗餘）。
  同階梯的 D0 只是量具（輸出損壞）、D1／D2／D2' 判死。⇒ **D3 無對外數字，且仍依賴卡住的 M1**。
  S1 的細節全部在 `MEMORY_S1.md`。
- **★ S1 的「池內容是不是載體」＝ 09-17 09:1x **已用裝置側讀數結清：不是**。**
  兩次 A/B（探針 4096 B 與整列 `nb02`）都得到 `SAME=90 DIFF=15 NOT-READ=0`，**分歧處一律是 ids 不同**；
  兩臂選到同一組 slot 的**每一個** graph（含第一個 T=1 步 g30）上，那些列的**整列位元組逐位元組相同**。
  ⇒ 「同 ids、同池佈局、不同位元組」**被否證**，下一步要查的是**層 0 的 delta-net 遞迴路徑**（g30 就 DIFF）。
  儀器與全文：`docs/POOL_ROW_DIGEST_20260917_0355.html`。
  **★ 09-17 10:2x 修正（重要、會改「下一步」）**：那個「ids 相同」只涵蓋**每個 chunk 的 token 0**
  （`rows=8`；而 T=2 的運算元有 16 個、T=8 有 64 個）。`POOLROWS=12` 一跑就顯示**真正的第一分歧在 g1**：
  S1 臂對「同一 chunk 的第 2 個 token」給出**錯誤的 slot**（含重複 slot 0），而 token 0 的 ids、
  router logits、routing weights、MoE 輸入**全部逐位元組相同**   ⇒ **是 mapping 缺陷，不是 residency**。
  **★ 11:2x 定論（`own=` 欄把這一軸結案）**：同臂控制 1200/1200 SAME 先過，A/B 得
  **`CONTENT=0`、`SLOT-REUSED=0`、`RELAYOUT=0`，只有 `ROUTING=477`**
  ⇒ **池的位元組／residency 這一軸結案**；載體是「**拿到的專家不同**」，位置在 **gather 上游**。
  而 `exp=`（host 認為該讀哪個 slot，§9.18.8）**已建置但實跑回 `exp=none`**（來源在 S1 臂不存在）
  ⇒ 來源要換成 host 現算 `st[e_j]`，**尚未量測**；全文 `docs/S1_OWNER_EXPECT_CHANNELS_20260917_1145.html`。
  細節與下一步在 `MEMORY_S1.md`。
- 文件：`docs/ROADMAP_PREFILL250_DECODE25_2026-09-13.md`（M0–M6 定義）；
  **`docs/roadmap-2026-09-14/ROADMAP_PREFILL250_DECODE25_2026-09-14.html`**（M1 的實作與量測結果、
  「M1 還沒完成的離開條件」、「M2 的狀態」，**09-14 之後未更新**）；
  `docs/M1_POOL_SPLIT_COST_2026-09-14.md`（Blocker A/B 的成本分析）。
- ⚠️ **`M1/M2/M3` 在本 repo 有兩個意思**：roadmap 的里程碑 vs **D5 的三個判決指標**
  （今天跑是 M1/M2/M3 各 9/9、`comparable=true`）。被問「M1/M2 狀態」時先確認是哪一個。

## decode 速度：現在到底多少（09-16 20:36 盤點）

「decode 速度」有四個互不相容的定義，引用必須指名：

| 定義 | 數字 | 條件 |
|---|---|---|
| **★ 唯一的 instrument of record：llama-bench 暖平台**（丟 rep1） | **10.78／10.91／10.98**（d512，三次獨立量測）；11.3（n=128 平台） | `prefill250+SPAC=1`、`-b512`、NOMINAL、**Nail denseIQ4X**（見下更正）。10.98 是 09-17 18:04 在**當前 build**（`libggml-base 23f533ad`、per-layer 儀器之後）量的，NOMINAL 105/105 ⇒ **儀器 inert 的量測確認（不只 D5）** |
| llama-bench `-d 0`（冷格，**不是標準格**） | 9.52–9.79 | 同上；`--depths 0` 是最冷的格子 |
| ~~decode_bench（HTTP）n=128~~ | ~~12.36（NOMINAL）→ 10.64（HEAVY）~~ | **★ 2026-09-17 使用者裁定：`decode_bench` 退休，不要再量、不要再引用** |
| llama-server（`p25-gputime` 等 HTTP 臂） | 12.95 可持續（**僅歸因用**） | **不再是 headline 口徑**，角色只剩逐步分解／歸因；`20.3` 一律不得引用（n=24 短爆） |
| **「25 t/s」的出處** | 09-05 的 **27.71** | **71 slots／4 GiB pool ＋ L0=32/L1=32 分區**，`draft accept 0.9974`（3.99 token/step、144 ms/step）——**不是**現在的 143 slots／8 GiB |

- **★★ 09-17 裁定：decode 統一用 `llama-bench`。** 標準形狀＝
  `llama_bench_matrix.py --prompt 0 --gen 128 --depths 512 --reps 3`（warmup ON、**丟 rep1**）；
  報數字一律附 **reps 數 ／ warmup 規則 ／ 模型家族**（`MTP=0` 會換檔）。
  ⇒ **可引用的 decode ＝ 10.8–10.9 t/s；距 25 約 2.3×（不是 2.0×）。**
  退休理由：兩器同 env／同模型／同 n／交錯差 9.4 vs 12.4（n≈128）、7.2 vs 18.7（n=24），
  且 `decode_bench` 離散大得多（12.36／12.95／10.64 vs llama-bench 三次 **1.1%**）。
  **⚠ 未決：MTP 不在這個口徑裡** —— 歷史的 `12.62 vs 9.82` 是 HTTP／`llama-speculative-simple`
  量的（⛔ **2026-09-25：該對數字已作廢、不得作決策依據** —— 見 `MEASUREMENT_CONTRACT` §7）；
  兩條路＝把 `--spec-*` 加進 llama-bench（三處、動 `src/`）或保留 spec-simple 但永不並排。
- **★★ 09-17 更正（`MTP=0 模型` 那條標註是錯的）**：現行 harness 下
  **`--arm prefill250` 與 `--arm prod25` 都載 `Nail-…-denseIQ4X.gguf`**；
  **只有顯式 `prod25:<…>;CGC_SERVER_MTP=0`（臂 `prod25-stream-mtpoff`）才換成
  `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`**（16:1x 用 `prefill_certifiability.py --dry-run`
  逐字讀它印出的 `-m` 行驗證）。`llama_bench_matrix.py` 的 `ARMS` 表裡**只有
  `prod25-stream-mtpoff` 設了 `CGC_SERVER_MTP`**。
  ⇒ 歷史的 **10.78／10.91 與 `prod25-stream` 的 10.79／10.89 都在 Nail denseIQ4X 上**，
  與 prefill 的 `pp2048` cell **同一個檔** ⇒ **pp/tg 這一對是自洽的**。
  ⚠ 但 **`tag` 不含模型檔**（json 只存 tag）⇒ **每一份 llama-bench 產出都要記 `-m` 那一行**
  （matrix 已經會印），否則事後無法分辨。
- **★★ 09-17 (A) 裁定：prefill 也統一 llama-bench，且要兩個 cell**（使用者追加「這個也要加上去」）：
  - **cell 1（自家形狀）**：`prefill_certifiability.py --arm prefill250 --prompt 2048 --gen 16
    --reps 3 --runs 5` ⇒ 形狀 `-p 2048 -n 16 -d 0 -b 5632 [profile]`（dry-run 驗證過）。
  - **cell 2（上游可比）**：`… --prompt 512 --gen 128 --depths 0 --reps 3 --runs 5 --batch 2048`
    ⇒ 形狀 `-p 512 -n 128 -d 0 -b 2048 [cli]`（＝上游預設 `-p 512 -n 128 -d 0 -b 2048`，
    `llama-bench.cpp:367-377`；上游標準輸出列 `pp512/tg128/pp512@d512`，`README.md:180-187`）。
  - **兩個 cell 必須是兩次獨立 run**（同一行程內第二格繼承暖池 ⇒ 不獨立）。
  - **前置條件已補**：`prefill_certifiability.py` 原本**沒有** `--batch` 透傳（固定吃 profile 的
    5632 ⇒ `pp512` 會變成「-b 5632 的 pp512」＝同名不同量）⇒ 已加 `--batch/--ubatch/--dry-run`。

- **「加大 pool／提高 hit rate 是槓桿」已推翻**：71 slots 的舊幾何（09-05）反而快（該筆 `resident=0.00 MiB`
  ⇒ 另一條填充路徑）。09-15 同幾何 MTP-on 只有 6.48 ⇒ **MTP 當前是淨損失**（`llama-speculative-simple`：
  無投機 **7.483** vs draft-mtp **6.517**）。
- **llama-bench 對 MTP 是瞎的**（`sampler|speculat|draft|MTP` 零命中、token 是 `rand()%n_vocab`）⇒ 當不了
  instrument of record；「量不到」已解決（用 `llama-speculative-simple`），露出的是 accept 太低（19.9%）。
- **兩個 decode 儀器不能並排引用**：同場交錯 LB 9.4 vs DB 12.4（n≈128），大半是「**第一個 rep 是冷的**」
  ⇒ 丟掉後 **11.3 vs 12.4 = 1.10×**；殘差與 n=24 的 2.6× **皆未歸因**。
  `depth` 是 llama-bench 唯一有效的暖機軸（`-d≥512` 才 10–13）；`llama-bench.cpp` **沒有 `srand`**。
- 病因已證是**序列化**（`CGC_SUBMIT_AHEAD=1` 讓每步 82.5→31–44 ms，~44% 可移除；**該探針輸出損壞**⇒
  其 16.82 t/s 不可引用）。
- **16–18 的歸屬（易搞混）**：那是 **M3／D3 的目標區間**，**不是 S1 的產物**。`dec-20260915-2246` 拆成
  **前提 A＝池／表一致 ⇒ 速度增益 0**；**前提 B＝`publish_slot_table` 發布離開熱路徑 ⇒ 才買到
  `n_segs 40 → ~1` 與那 ~44%**。折扣：16.82 是 **MTP off** 量的（production MTP on 已 ~17 ⇒ 兩個 ≈1.8×
  可能吃**同一份空窗、不可加乘 ⇒ `ceiling 必須在 MTP on 下重量`，還沒做**）；輸出損壞。
  **⇒ 16–18 掛在 M1→M3，不是 S1。** 到 25 的算術：9–10 × 1.78 ≈ 16–18，**還缺 ~1.5×**，只能來自 M4
  （依賴 M1/M3）。
- 文件：`docs/ROADMAP_PREFILL250_DECODE25_2026-09-13.md`（M0–M6）、
  `docs/INSTRUMENT_COMPARE_20260916_1821.html`。

## Ornith vs Nail：兩個「decode t/s」不可並排（09-18 04:3x 查證）

**兩者是同一個架構的兩個量化版本**（標頭實測：`gguf-py` ＋ `gguf_pool_geometry.py`）：
`qwen35moe`／41 層／**n_expert 256、top-k 8**／embedding 2048／ctx 262144／nextn 1，**逐項相同**。

| | Nail（旗艦 `denseIQ4X`） | Ornith（`…APEX-I-Compact-v2D-lite`） |
|---|---|---|
| 檔案 | 12.72 GiB | **16.36 GiB（+29%）** |
| 專家位元組 | 11.11 GiB（IQ2_S／IQ3_S／IQ4_XS） | 14.68 GiB（Q3_K／Q4_K／Q8_0） |
| per-expert（binding） | 1.3906 MiB（blk.39） | 1.6875 MiB（blk.0-4／blk.35-39） |
| 8 GiB → slots | 143 | 118 |
| **每 token 專家讀取**（×8/256） | **355 MiB** | **470 MiB（+32%）** |

**⇒「模型大 ⇒ 慢」不成立（同架構）；但「模型大 ⇒ 每 token 讀得多」在這裡成立 ⇒ 速度差不可能來自位元組。**
IO 本來就不在關鍵路徑（`fill_wait = 0.000`）。

**★ 兩個數字來自不同 build／熱態／MTP 狀態**（`Backup/phase_decomp/en_dc_orn_ab_rev_20260918.json`
04:00 vs `en_dc_st_20260918.json` 04:15）：

| 條件 | Ornith | Nail |
|---|---|---|
| build | `libggml-metal 05b2da9388ea`／`libllama e4e432f27aee` | `c6bddce9796a`／`feaaa647f4f8` |
| 熱態 | 6/6 **MODERATE** | 6/6 **HEAVY** |
| MTP | **OFF**（`CGC_SERVER_MTP=0`，無 `draft_*`） | **ON**（`draft_accept 0.4286`／`draft_mean_len 2.2`） |
| `predicted_n` | 24 | 24 |

⇒ **可引用的述句只有**：「Ornith 在 MTP-off／MODERATE 下量到 15.72–19.65；Nail 在 MTP-on／HEAVY 下
量到 8.09–9.85」。**❌ 不可說「Ornith 引擎效率高 1.6×」**——只有 `n_predict=24`（短爆，本檔已判不可引用）相同。

**裁決它的一次實驗（尚未跑）**：同 build、同 `n_predict`、**兩臂都 `CGC_SERVER_MTP=0`**、交錯 A/B ×3。
⚠️ `prod25-stream-mtpoff` **不是**這一臂 —— 它載的是**第三個檔** `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`，不是 Nail。
⚠️ 「Nail 的 MTP-on vs off」與「同 MTP 狀態下 Nail vs Ornith」是**兩個不同的問題**（MTP-on 讓 trunk 變 verify 圖，ntok 2/4）。

**★★ 09-18 04:39 對齊 A/B（ABBA）已完成 —— 這一節的懸案結清**：同 build、`--profile prod25`、
`--n-predict 96`、`--rounds 3`、**兩臂都 MTP off、differ-by-one 只差模型檔**（`p25-gputime` vs `orn-p25`）：

| 順序 | Nail | Ornith |
|---|---|---|
| 前向（Nail → Ornith） | **12.59** | 7.94 |
| 反向（Ornith → Nail） | **11.89** | 8.47 |

⇒ **兩個順序都是 Nail 快**（不是位置效應）⇒ 配對中位 **12.24 vs 8.21 ⇒ Nail 快 1.49×**。
⇒ **那組 15.72–19.65 不可重現**（Ornith 今天 7.94–8.47，**低 2.4×**）⇒ 主嫌是 **build**，不是熱態。
★ **可引用述句：「換成 Ornith 不是提速手段 —— 它慢 1.49×」**；「模型更大卻更快」在本 repo 沒有實測支持。
（Nail 這條臂重現了歷史 HTTP 帶 12.36–12.95 ⇒ 夾具沒變。）產物 `Backup/phase_decomp/aligned_nail_vs_ornith_{fwd,rev}.json`。

## decode 到 25 的算術（09-18 04:4x —— 回答「25 有沒有機會」的框架）

**25 ＝ 步速（steps/s）× 每步 token 數**，兩個乘數的現況差很多：

| 乘數 | 今天的值 | 已量的槓桿 | 要到 25 的缺口 | 卡在 |
|---|---|---|---|---|
| 步速 | **~10.9 steps/s**（1 tok/step） | `CGC_SUBMIT_AHEAD` **82.5 → 31–44 ms/步**（×1.8–2.3） | ×2.0–2.3 | **探針輸出損壞**；S2/S3 的前提 B 已倒 |
| 每步 token | **1.0**（MTP off） | ⛔ MTP-on `9.82 → 12.62`（×1.28）**已作廢**（契約 §7）⇒ 交付 cell 缺配對量測 | ×1.6–2.0 | accept 58.25%（09-05 的 0.9974 是舊幾何的飽和值） |
| host 側 | build+alloc+inputs ＝ **0.086 ms/步（0.07%）** | — | **0** | **正式關閉**（`compute` 99.9%、`fill_wait` 0.000） |

**★ 25 的原始出處正好證明「乘數」才是題目**：09-05 的 **27.71 t/s ＝ 3.99 token/step ÷ 144 ms/step**
⇒ 那時的步速只有 **6.94 steps/s（比今天慢 1.57×）** ⇒ **那個數字幾乎全部住在「每步 token 數」那一項**。

**算術**：`9.82 × 1.28（MTP，已量）× 2.0（去序列化，未證）＝ 25.1` ⇒ **剛好擦線**。而：
- ⚠️ **兩個乘數能不能相乘還沒量過** —— 12.62 與 16.82 是**不同儀器**量的。本檔早已註明
  「兩個 ≈1.8× 可能吃同一份空窗、**不可加乘** ⇒ ceiling 必須**在 MTP on 下重量**，還沒做」。
- ⚠️ **25 / 10.9 ＝ 2.29×**（不是 2.0×）；用 HTTP 臂 12.2 算是 2.05×。
- ⚠️ **n=24 短爆會給 18.7–20.3 的假希望**（已判不可引用）。

**★★ 09-18 05:0x 更正（上面那兩個乘數被量到，而且都比我的估計小）—— 來源是別條線的
09-17 23:11–23:15 量測（`docs/M3_M4_STATUS_2026-09-17.md` §2／§3b）與 §EN-111／§EN-112。**

| 乘數 | 我 04:4x 寫的 | **實測上界** | 出處 |
|---|---|---|---|
| 去序列化（步速） | ×2.0（壞探針的 82.5→31–44 ms） | **×1.5**：GPU 時鐘跨度 ~101 ms、其中 idle **55.3 ms（35.3%）**；移除**全部** idle 仍只有 **10–11.5 t/s**；步預算另一條 ⇒ `gap → 0` 給 **12.1 t/s** | `M3_M4_STATUS` §3b（GPUTIME ＋ 層級 `gap_sum` 兩路互相印證 0.4%） |
| MTP（每步 token） | ⛔ ×1.28（9.82→12.62）**已作廢**（契約 §7） | **×1.06（同一 launch 配對）**；跨啟動 +9～25%（不可配對）；accept **58.25%**，差 60% 只有 1.75pp，且**在 greedy 下不可由 accept rule 移動**（它是 (base, head) 配對的性質）；`CGC_MTP_REJECTION` 在 temp 0 是 **by construction 的 null** | `M3_M4_STATUS` §1 |

**★★★★ 09-18 12:1x 裁決（同檔 ABBA、生產路徑）：MTP 是 ×0.695，不是 ×1.0。**
新臂 `p25-nail-mtpoff`（釘 `CGC_SERVER_MODEL=Nail-…denseIQ4X.gguf` ＋ `MTP=0` ⇒ **同檔**；見 §EN-145 的換檔陷阱）
vs `p25-mtp-on`，`--profile prod25 --n-predict 96 --rounds 3`：
前向（ON 先）9.40/11.28 = **0.833**、反向（OFF 先）6.32/10.91 = **0.579**
⇒ **ABBA 校正 M = √(0.833×0.579) = 0.695**（兩個順序都 < 1）；prefill 也 **−18%~−30%**。
`io_bytes`／`file_reads`／**`evictions`** 三個獨立計數器**同步 ×3.02**（29.12/9.66 GB、75123/24837、25721/8526）
⇒ **IO 完全由「驅逐」驅動**（`cold(ZERO) = 0`，不是 union 的 miss）；`capacity` miss **×6.94**、
`distinct_over_slots` **14 → 33**、hit **94.9% → 84.3%**。
⇒ **樹上的「MTP ≈ 0」是 llama-bench 路徑的結論**（那條路 verify 走 `ensure_batch`、`verify: calls=0`），
**生產 server 路徑是 −30%** ⇒ 兩者不矛盾，是同一現象在兩條池分支上的兩個量級。
**⇒ 25 的算術要重做：MTP 不是 ×1.0（可忽略），是 ×0.695（要還回去）。**
**⇒ 對症的不是 `batched-union gather`，是「池裝不下長期工作集」（247 distinct vs 143 slots 必然 thrash）**
⇒ 投**淘汰／填充策略**（`M6` 的「唯一不需預算的路」）。

**★★★ 09-18 12:1x 追補（MTP 那條）：裁決讀數＝`union/32 = 0.595`；而成本不在「單次 union」在「長期工作集」。**
儀器早就在印（**不是 env-gated**）：`llama-expert-cache.cpp:2529` 的
`MTP fast path: … verify: calls=474 union=9028 cold=0   draft: calls=36 union=288` ⇒ **`9028/474/32 = 0.595`**
（核定：draft `288/36 = 8.00` = top-k ✓；`19.05 < 32` ⇒ verify batch = 4 ✓；`n = uni.size()` @`llama-context.cpp:6303`）。
`cold(ZERO) = 0` ⇒ **單次 verify 不打穿池**（推翻「union 打穿 143 slots」那句）。真正的成本是**長期**：
`layers_distinct_over_slots` **3（off）→ 11（llama-bench）／18（server）**、`capacity` miss **218 → 2229（×10.2）**、
server 路徑 hit **96.0 → 87.1%**、`worst` 層 167/143 → 240/143。
⇒ 對症的路是「**改淘汰／填充策略**」（`M6` 的「唯一不需預算的路」），**不是**「增大池」。
⚠️ **新疑點**：llama-bench 路徑 `verify: calls = 0`（258 次全算進 draft）⇒ 它的 verify **不走 fast path**
（走 `ensure_batch` 的填充），而 **server 路徑的 verify 走 fast path** ⇒ **那個 −4~−6% 可能高估了生產路徑的成本**；
`M3_M4_STATUS` 的 HTTP **+5.7%** 正是 server 路徑 ⇒ **兩份量測的不一致有了機制解釋**。
⇒ **MTP off/on 的正確配對要在 server 路徑做（HTTP），不是 llama-bench。** 全文 §EN-143／§EN-144。

⇒ **`10.9 × 1.5 × 1.06–1.25 ≈ 17–20 t/s` ⇒ 這三個方向到不了 25。** 25 的那 2.4× **只能來自
「削 GPU work」**（`M3_M4_STATUS` §2 的兩條界：移除 CPU 序列化 ⇒ 12.1 t/s；GPU 跨度本身 82.84 ms
⇒ 12.1 t/s 上限）。
★★ **而那個「削 GPU work」的第一名不是 MoE**：名字表接上「會編碼」權重後的排名（§EN-112，家族 ms ÷
同步 `wait`，粗/細）是 `node`（**無名真運算**）**24.1/20.3%** ＞ `(other)` 11.4/12.1% ＞
`cache`（**狀態快取**，見下方更正）7.7/9.8% ＞ `norm` 7.7/7.5% ＞ `ffn_moe_`（逐元素合併鏈）7.6/6.7%
＞ … ＞ **`moe_gemv`（MUL_MAT_ID）3.2/6.3%** ＞ `dense_gemm` 2.2/4.2%。
⇒ **`cache` ≥ 整個 MoE 專家 GEMV 家族。**

### ★ 09-18 11:0x 更正：`cache` 是**狀態快取**，不是「池自己的暫存」；而它與 `node` 一起把答案指向 delta-net

用**當前詞彙表**把一份新 `CGC-GRPH` dump（log `050331`，4116 節點）的 `cache` 桶成員名字逐字印出來：

```
150  cache_r_l# (view)                          120  cache_r_l# (view) (copy of conv_input-# (view))
 60  cache_s_l# (view)                           30  cache_r_l# / cache_s_l# (reshaped)(…)
 20  cache_v_l# (view)   20  cache_k_l# (view)   10+10  cache_k_l#/cache_v_l# (view) (permuted)
op: VIEW 290 | CPY 210 | RESHAPE 60 | SCALE 60 | SET_ROWS 20 | PERMUTE 20
```

`cache_r_l*`／`cache_s_l*` 是**遞歸狀態**（`llama-model.cpp:378-379` 的 `pattern_r_cache`／`pattern_s_cache`），
`cache_k_l*`／`cache_v_l*` 是 **10 個全注意力層的 KV**。⇒ **這個桶是「每 token 讀寫遞歸／KV 狀態」的管線，
與專家池無關。** 池的填充是 **CPU 側的 hook**（計在 `CGC-SEG` 的 `cb`），在 GPU 節點表裡**沒有成本**
（與 `fill_wait = 0.000` 一致）。

**⇒ 因此「池自己的暫存 ≥ 整個 MoE 家族」這句作廢**（它在 `docs/NAME_WORK_TABLE_2026-09-18.md` §6.1／§6.2 與
兩份白皮書裡都出現過，已加標註框）。**更正後的排名語意**：

| 家族 | 份額 | 是什麼 |
|---|---|---|
| `node`（350） | 17.7% | **delta-net 內部運算**（`MUL_MAT ne=[8192,2]`×29、`MUL_MAT ne=[32,2]`×30、`GET_ROWS`×90、`ADD`/`UNARY`×30、10 個 `FLASH_ATTN_EXT`） |
| `cache`（660） | 10.6% | **遞歸／KV 狀態管線**（真工作 290：CPY 210＋SCALE 60＋SET_ROWS 20） |
| `z-`＋`gdn_out`＋`conv`＋`linear_attn`＋`q/k/v_conv`＋`alpha`＋`beta` | ≈15–20% | 其餘 delta-net |
| `ffn_moe_`＋`moe_gemv`＋`ffn_` | ≈15% | MoE 逐元素合併鏈 ＋ 專家 GEMV ＋ 稠密 FFN |

⇒ **支配區塊是線性注意力（GatedDeltaNet）＋它的狀態管線（約 35–45%），不是 MoE（約 12–15%）。**

⚠️ **09-18 13:4x 追加：這個排名只能讀到「家族」層級，細粒度不能用它。**
名字表的 `wcntw` 是 `ns_kind_wns[q] += dur * wcnt[q] / wtot`（`ggml-backend.cpp:2306`）——
**把一個 command buffer 的 `dur` 平均分給該 buffer 內有工作的節點**。而 `prod25` 的一個 decode 步有
`bufs=633` / `nodes_all=3979`（≈6.3 節點/buffer）⇒ **落在小 buffer 裡的節點會獨吞整段時間**，
受害者正好是**每層的第一個具名 op**。實證：`attn_norm` 被報成 **36.76 ms（8.5%，step 88）**，
而 dump 直讀它是 `MUL ne=[2048,2]` —— 40 個 **4096 元素**的逐元素乘法，不可能花 36.76 ms。
**⇒ 細粒度改讀 op 級表**（`CGC-GPUOPS`，`nd=` 是確定值）：`MUL_MAT` 426 個 **32.7%** ＞
`MUL` 278 16.6% ＞ **`MUL_MAT_ID` 117 個 10.0%（MoE 專家 GEMV）** ＞ `CPY` 210 8.1% ＞
`UNARY` 6.6% ＞ `GATED_DELTA_NET` 4.8% ＞ `GET_ROWS` 4.3%。
⇒ **在 op 口徑下 MoE 專家 GEMV 只有 10.0%，最大單塊是 dense `MUL_MAT`。**
（lesson `eng-mh-0070`；白皮書 §25／§25.1；`VIEW` 866／`RESHAPE` 598 是 NOOP，`wcntw=0` 而 `cntw` 12.4%／15.0%。）

⚠️ **09-18 13:1x：`dnqkv_proj` 那一塊「不是記憶體壓力」**（池 8→6 GiB 的 differ-by-one，
`dnqkv_proj` 中位 29.34 → 30.55 ms ＝ ×1.041，跟著全局漂移，**沒有選擇性下降**；
縮池只換來 `file_reads` +54%、`io_bytes` +56%、hit 87.1→79.7%）。
⇒ **它是 GPU 側的成本**（8.5 MiB IQ4_XS × 30 層 = 255 MiB/步 ⊕ 29 ms/步 ⇒ **≈8.8 GB/s**，
M4 DRAM ~120 GB/s ⇒ 延遲／固定開銷主導）。白皮書 §24。

**而且它是「操作數／啟動開銷」受限，不是頻寬受限**：`cache` 的真工作位元組 ≈ 每 token 十幾 MB
（`conv_input` = 4×8192、`conv_state_last` 回寫 8192…）⇒ 在 ~120 GB/s 下 ≈ 0.1–0.5 ms/token，
而它量到的是 **27 ms（=`cache` 的 `wcntw`）**。**120 個 CPY 的來源已定位**：
`models/delta-net-base.cpp:509-526` 在 `n_rs_seq != 0` 時**每層建 K = n_rs_seq+1 個獨立 `ggml_cpy`**
（實測 30 層 × 4 = 120，與 dump 逐格吻合），而 `n_rs_seq == 0` 那條路徑只建 1 個。

**⇒ 判準（下一輪要動這裡）：** 若把 4 個重疊視窗的 CPY 收成 1 個 ops **不是**數值等價的
（它們寫進 K 個不同 slot，`s_slot = K - t`）⇒ 先確認那 K 個 slot 是否真的都要寫。
**不成立 ⇒ 這條路作廢，回到「段數／序列化」那條（天花板 ×1.711，被 pool 的每層 publish 擋住）。**

**★★ 同一批量測把 M3 的判準本身判掉了（`ffn_moe_* ≥ 40% of wait?` ⇒ NO）**，三個獨立方法：
① 名字表 **3.2–6.3%**（跨粒度不穩）；② work-weighted op 表 **8.0/8.4%**（跨粒度穩定，`MUL_MAT_ID`）；
③ 聯集上界 **≤28.3% of 步 busy**。⇒ 點估計全部 **< 15%** ⇒ **依判準走「三條路全不足／找不到路」分支。**
⚠️ ③ 的 28.3% 落在 15–40% 的灰色帶，所以「死路」是**靠點估計**成立的，不是靠上界。

**該先做哪一件（09-18 05:0x 重排 —— ① 與 ③ 被實測判掉，② 上界 1.5×）**：
1. **不要再建「逐節點 GPU 時間」的儀器** —— ① 已由別條線做完，而且**不需要** Metal sampling：
   `ggml-backend.cpp:1848` 的註解明說 `ggml_metal_graph_compute` 本來就把每段切成固定節點範圍的
   command buffer ⇒ 逐範圍的 GPU 時長可歸因，而 **`MTLCounterSampleBuffer` 的 sample point 會插
   barrier、會擾動被測物**（原文：*would perturb the thing being measured*）。開關是 `CGC_GPU_NODES`。
   **判準已由它判成 NO（見上）**。
2. **`node` 那 20–25% 的命名**（別條線明寫的下一件事；它 100% 是會編碼的節點 ⇒ 純歸因問題）。
3. **`cache`（＝遞歸／KV **狀態**管線，不是池 staging；見上方 11:0x 更正）7.7–9.8%**：
   真工作是**每層 5 個 CPY ＋ 1.5 SCALE ＋ 0.5 SET_ROWS**，其中 **4 個 CPY（120/步）來自
   `delta-net-base.cpp:509-526` 的 K 次迴圈**。可攻擊，但先驗「那 K 個 slot 是否都要寫」
   （寫的是不同 slot ⇒ 不是數值等價的摺疊）。
4. **不要算進預算**：down-combine 融合對旗艦是 **−15~18%**；換 Ornith 是 **−1.49×**。
5. ⚠️ **M3／M4／節點歸因現在是別條線在做**（`M3_M4_STATUS` 與 `decode_step_profile.py` 都還沒提交）
   ⇒ **不要同時動 `ggml-backend.cpp`**（撞車）。

## prefill 250 的條件式交付

`CGC_SERVER_PROFILE=prefill250`（`-b/-ub 5632`、`CGC_PREFILL_STREAM=1`、`CGC_GATHER_SLAB_CAP=256`、
pool 8 GiB、ctx 8192）**必要非充分**；還要散熱前提 ＋ 量測紀律。

- **★★ 09-17 盤點：prefill 有兩個入口，而「哪一個是交付口徑」未定**（decode 已於同日統一在
  llama-bench）：
  ① **HTTP 驗收臂** `run_req2_retest.sh`（**2873-token prompt** 的 `prompt eval t/s`，
  每請求邊界帶熱讀數）—— 本節 §1–§3 的交付規程是為它寫的，白皮書的 278.56／261.17／275.01 出自它；
  ② **llama-bench `-p 2048`** `prefill_certifiability.py --arm prefill250 --prompt 2048 --gen 16
  --reps 3 --runs 5`（走**同一支** `llama_bench_matrix.py`；`-b/-ub` 由 dump 取 ＝ 5632）——
  已記錄 276.25／300.43（Nominal ×2）與 227.27／151.28／145.79／182.39（非 Nominal）。
  ⚠️ **兩邊數字很近但不可並排**（`-p 2048` vs 2873-token、歷史 `-b` 6144 vs 5632）。
  ⚠️ **llama-bench 那條是抽籤不是規格**：`pp2048 @ -ub 6144` 四次獨立啟動
  276.59／198.84／176.18／122.68 ＝ **2.25× 離散**（啟動內部只差 2.08–15.41）
  ⇒ 統一用 llama-bench 對 prefill **不是免費的**：把「熱條件不足」換成「跨啟動離散」，後者未解。

- **權威儀器（非 root、11 ms）**：`notifyutil -g com.apple.system.thermalpressurelevel`
  （`0=Nominal 1=Moderate 2=Heavy 3=Trapping 4=Sleeping`）。**判準：發射前讀到 `0`。** 分離度（request
  級、零重疊）：發射 0 → **6/6 ≥250**；發射 1 或 2 → **0/21**。反面教材：`NSProcessInfo.thermalState`
  367/367 讀 `fair`、零區辨力——**找到介面 ≠ 找到儀器**。
- **★★ 09-17 15:37 更正：上面的判準是「必要非充分」—— `COLD-STATE`（安靜 1940 s）＋ 發射前與每個
  請求邊界都讀到 `0/NOMINAL`，仍然只量到 242.42／227.27／249.06（三個都 <250）。**
  同 build／同 profile／同 2873-token prompt；2 Hz 序列顯示 `15:37:15→15:38:24` 連續 Nominal，
  **req1／req2 完全落在那段之內** ⇒ 熱解釋不了。反向對照：同日 15:03 標籤只是 `UNKNOWN-STATE`
  （無 state file）卻得 **278.56／261.17／275.01** ⇒ **安靜秒數與熱等級都不預測這個 t/s**。
  池路徑已排掉（兩臂 `pread_usec` 1501 vs 1548 s、`us/job` 31733 vs 32533，都在 1.26 MiB/s 線上）。
  ⇒ 標籤只能背書「**讀數的來歷**」，**不能**背書「≥250 這個門檻」。（lesson `eng-mh-0054`；
  白皮書 §2.3／§2.4；skill 已同步修正）
- **「距上次持續 prefill ≥150 s」作為充分條件已被推翻**（安靜 18 s → 289.86；34 s → 166.95）⇒ 自變數是
  **累積負載（同序列第幾次啟動）**；機制是 DVFS 階（1470→928→618 MHz）與熱壓同秒。
- **可交付述句（09-17 修正）**：可引用的是「**該臂的 req1–req3 讀數是 X／Y／Z**」＋ 熱標籤；
  ❌ 舊述句「發射時讀到 `0` 的那一臂，req1–req3 全部 ≥250」**已被否證**（見上）。
  **不能**說「250 隨時可重現」。熱態平台 167–201。`t(token) = 0.5575 ms + 4226/f_eff(MHz)`；
  純階反讀 1470→291、928→227、618→135。**250 不是上限**；`swap` **不是因是果**。
  **直接變數是有效時脈，熱等級只是它的粗代理**（本次 4.13 ms/tok 反推 ≈1130 MHz vs 對照臂 3.59 ≈1250）
  ⇒ 裁決「為何 COLD 仍 <250」得用 `powermetrics` 的 GPU 時脈駐留（**需 root**，且沒試過）；
  未排掉的第一候選是**背景 GPU 客戶端**（`Freebuff Helper (GPU)`／`WorkBuddy Helper (GPU)`／
  `WebKit GPU`／`WindowServer`；發射時 load 1 分鐘 2.42–2.93，同日 15:39 量到 6.18）。
- **閘門**：`COLD-STATE` 需安靜 ≥1800 s，否則 `HOT-STATE`；**HOT 不得當交付數字**。**機器地板是 HEAVY**
  （零 llama 行程時 thermal=2、GPU util 20%、Electron ~103%）⇒ agent UI 造成。
  `ARMS=2 bash Backup/run_thermal_gate.sh`（不成立 exit 3，fail closed）；
  `docs/PREFILL250_CONDITIONAL_DELIVERY_20260916.html`。
- **已結清（09-17 07:0x）：`prefill250 + CGC_SPAC=1` 的 prefill 代價不成立。** 同 build（server
  `054fb22f`／`libggml-metal 2b87af2e`）COLD 交錯 off/on/off/on，四臂全 `COLD-STATE`／`survived=yes`：
  req1 off {215.30, 287.80} median **251.55** vs on {283.36, 284.82} median **284.09**；req2 268.51 vs
  285.95；req3 291.03 vs 295.11。**唯一的大落差（req1 配對 +68.06／−2.98 符號相反）是序列位置造成的**：
  同一個 off 配置在位置 1 與 3 差 **72.50**，而兩臂的池讀 bytes **完全相同**（2 784 305 152，`us/job` 只差 3%）
  ⇒ 第一次啟動的代價在池之外（page cache／Metal 暖機）＝機器的狀態，不是 flag。池：off 永遠 `2604/0`
  （100% compulsory）、on 永遠 `2502/5`；SPAC=on 少讀 3.9% bytes，**不買也不付** prefill。
  **20:54 那臂不可搬用**：它載入的 `libllama 233a172`／`libggml-metal ec3ece90` 與今天不同 ⇒ 不同 build，
  且它的 req1 244.96 比今天的 off@pos1 215.30 **還快**。
  產物 `Backup/cgc_logs/spac_cold_ab/RESULT.md`。**⚠️ 這條數字只在同 build 內可比；且 ABAB 的
  第一個臂不可與後面的臂交換**（pos1 溢價 72.50）。下一個能裁決「反向增益」的設計＝丟棄臂開頭 ＋
  鏡射後半（`off,on,off | on,off,on`）。

## 指紋／戳記

可比性 key ＝ **pool／engine／weights／launch 四組**（`decode_sweep.build_fingerprint()` 用 glob 8 鍵；
`knifeedge_matrix.{source,pool_geometry,binary,model}_stamp`；`mtp_head_identity.fingerprint()`；
A11 啟動環境指紋），`suite` 是第五軸，`harness.script_digest` **刻意不進 key**。

- `build_fingerprint()` **用 glob**（`libggml*.dylib`／`libllama*.dylib` ＋ server，8 鍵）；舊版只手動雜湊
  3 檔、**漏了 `libggml-base`** ⇒ 兩跑指紋相同被誤判可比；**v1（3 鍵）的歷史列不可比**。
- `binary_stamp()` 只記 `{size, sha256(前 64 KiB)}`，而 Metal kernel 在 offset ~169 KB ⇒ 判別力「安全但靠
  意外」（靠 ld64 把內容衍生的 LC_UUID 寫在前 1,881 位元組）。**不是設計出來的**，沒有測試釘住。
- `source_stamp`／`pool_geometry_stamp` 的來源清單是**人工列舉**；不在清單但會改數值的至少還有
  `ggml-metal.metal`、`ggml-backend.cpp`、`llama-model-loader.cpp`、`ggml-metal-context.m`（靠
  `binary_stamp` 兜住）。

## MTP／speculative（2026-09-18 由 MEMORY.md 移入，本節為權威）

> ⛔ **2026-09-25 operator 下令：本節（及全檔）的 `9.82`／`12.62`／`+28.5%` 一律不得作為決策依據**
> —— HTTP 舊口徑、無 warm-skip；且**生產口徑 MTP off 已 11.03~12.20 ⇒ 分母失效**。
> 作廢登記表 `docs/MEASUREMENT_CONTRACT_2026-09-25.md` §7。**以下全部降為歷史記錄。**

- **★ acceptance 是「同一 prompt + greedy ⇒ 逐 rep 完全相同」的確定量（09-19 實測 6/6）。**
  prod25 / `http_duo` 那個 prompt = `0.53061（78/147）mean_len 2.59`；先前記的 0.46541 是**別的
  prompt**。**⇒ ① 的問題從「是不是 bug」改寫成「是不是系統性偏差」：隨機不一致類已被排除。**
  副作用（重要）：既然同 prompt+greedy 逐位相同，**batch-verify 的 logits 與逐 token 解碼的 logits
  必須逐位相同** ⇒ plain_match 從「0.5–1 天只讀」壓成**一支跑**——「兩次跑本來就不同」這層藉口沒了。
- **★ server 側 decode t/s 一律是 post-prefill 暫態（09-19 實測 6.38–10.69 t/s）**，正好落在
  `llama-expert-cache.cpp:385` 注的 degraded band（8.1–8.9），遠低於 steady **22.2**；
  `n_predict=128` 走不完暫態 ⇒ 引用前要把 `n_predict` 拉到 ≥512 且只報尾段，或明確標注「暫態」。

- **MTP／spec：攤薄係數 `m` 已量（09-18，`prefill250`，llama-bench）**＝0.474（3 輪配對中位）／
  0.689（唯一全程 NOMINAL 點）⇒ **每個 k 都虧（S 0.55–0.74），accept 拉到 0.92 也只 ~1.5×**；
  **2× 需 m ≤ 0.214**。成本來源＝`union/call = 9.36 + 3.12·k_eff`（單 token 一步＝8）。
  工具 `scripts/check/spec_cost_curve.py`。→ `docs/SPEC_COST_CURVE_2026-09-18.md`
  （服務路徑 prod25 卻是 +31%／m≈0.21，**兩台儀器結論相反，未解決**）。
- **★ RSL-MTP（我提的「draft 的 top-8 ⊆ 已付費並集」）已被自己的數據否決：0.70–0.97×，無一格 ≥1.0。**
  親和性存在（單專家邊際重疊 q≈0.55）但「**整個** top-8」⇒ q⁸=0.008；放寬成預算 b 後
  **接受率掉得比成本快**。m=0.474 下 **2× 即使 a→1 也不可能**。⇒ 別練 draft head。
  → `docs/RSL_MTP_GAIN_ESTIMATE_2026-09-18.md`
- **★ 那該攻什么：verify 的 residency thrash（新主項，未驗證）。** 同一批 log 的 teardown 就印了：
  `layers_distinct_over_slots` 0→4→15→17（k=0..7）；worst layer distinct/slots **143/143 已零餘量**
  →219/143；hit% 92.6→49.2 就發生在越界那一刻；每步讀取 7.9→67 MiB。回歸
  `ms/step=89+3.35·MiB`（r²=0.70，**MiB 與 k 共線 ⇒ 弱證據**，真正提供機制的是那條整數計數器）。
  ⚠ **更正**：「pool 143→179 slots ⇒ I/O −3.7× 但 decode 只 +9% ⇒ 非 IO bound」是在 **MTP-off、
  非 verify 路徑**量的（那裡 k=0 時不 thrash）⇒ 不能推到 verify，要重跑。修好後上界 S=E=1.31–1.88×；
  m=0.474 時 a=0.85 也只 1.23× ⇒ **thrash 的價值是讓 accept 變得值得買**。`CGC_NO_PREFETCH=1`
  是預設（`run_server.sh:2029`）⇒ 以上全在 prefetch 關閉下量得。dynamic-k oracle 只 1.035×
  （且被噪音膨脹）⇒ 別做。→ `docs/MTP_VERIFY_OPT_2026-09-18.md`
- **★ 承上，「residency thrash 是主項」已被第三批雙軸數據否決（09-18 晚）。** 工具加 budget 第二軸
  （`--budgets 8,6,4`）：slots 143/107/71，**k=0 就已有 0/10/38 層越界**。但**同 k 跨池的
  bytes→時間彈性只有 0.10–0.17（k=1/3 甚至 −0.38）**；`k_eff` 單變數解釋 **84%** 的 ms/step 變異
  （59 ms/token），bytes 只 65%、加進去後係數掉 3×。draft 側只佔 union **3%**
  ⇒ **`m=0.43–0.56` 的機械解釋是「每個 draft token 的固定代價」，不是流送**。
  **修 residency 的上界只剩 ~15%**（超額 +269 ms 中 bytes 只解釋 ~40 ms）。
  ⇒ thrash／per-layer 配額／prefetch 開關**降為次要**。
- **★ 2026-09-19 → `docs/MTP_2X_BOUNDARY_2026-09-19.md`（「MTP 到 2×」的還輯列為權威）：**
  `S = (1+a·k)/(1+m·k)`，今天 server `a=0.4654`、`m=0.322`（`C=168.4/85.65=1.966 @ k=3`）、bench `m=0.474`。
  ➜ **k→∞ 的上界是 `a/m`：1.445（server）／0.982（bench）**
     ⇒ **k 加到多大都不到 2×**（與 dynamic-k oracle 1.035× 互證）⇒ `n_max 3→5` 作廢。
  ➜ **k=3 到 2× 的邊界**：`m ≤ ((1+3a)/2 − 1)/3`
     a=0.465→m≤0.066（−79%）、a=0.70→≤0.183（−43%）、**a=0.80→≤0.233（−28%）**、
     a=0.90→≤0.283（−12%）、**a=1.00→≤0.333（今天已夠，給 2.03×＝23.75 t/s）**。
     ⇒ **a 的標數比 m 大得多 ⇒ 首選是攻接受率**，不是再削成本。
  ➜ 攻接受率的下手處：`plain_match=False`（batch verify 與逐
     token 不一致，**至今未出理**）；量化天花板假說用
     **Ornith 的 acceptance 當探針**（只當探針，它慢 1.49×）。
  ➜ ⚠ **第三次共線陷阱**：`ms/step ≈ requests × 1.59 ms`也是 k 的另一種形狀
     （`requests/輪 = 8.49 + 3.61·k`）⇒ 未證實也未否證。
     判它的仍是 **`--spec-type ngram-map-k` 消融` 與 `CGC_P_ROUTE=1`**（都還沒跑）。新的並列第一是兩個廉價實驗：
  **`CGC_P_ROUTE=1`**（探針已在樹裡 `llama-context.cpp:5929`，直接印 `P(top8_{t+j}⊆top8_t)`，
  不需額外 forward）與 **n-gram draft**（`--spec-type ngram-map-k` 去掉 draft 前向：
  m 崩 ⇒ 是 draft 前向；m 不動 ⇒ 在 verify 每 token 路徑／T=k+1 沒攤薄）。
  ⚠ 這批三個混淆：**run 順序與 budget 完全混淆**（每回合永遠 8→6→4，重跑得上拉丁方）、
  漂移 −1.93 ms/run 但熱態同時變壞 ⇒ 是預熱不是熱態、同格跨回合 max/min **1.03–1.74×**
  ⇒ 絕對 t/s 不可引用，只引用回合內比值與計數器。→ `docs/POOL_BUDGET_COST_DECOMP_2026-09-18.md`

## ★ 2026-09-19 更正：本檔「llama-bench 對 MTP 是瞎的」那一節已過期（部分）

**`8194a2ba4`（2026-09-18 20:24）已修**：`test_gen_spec` 補上了缺的那一行
`llama_context_set_cgc_phase(ctx, CGC_PHASE_VERIFY)`（＋decode 後 fail-closed reset）。
病因：fast path 的閘門是 **caller 設的 phase**，不是 batch 形狀（`llama-context.cpp:6065`），
而全樹只有 server-context.cpp 與 speculative.cpp 兩個 caller ⇒ bench 的 verify 批次一律掉回
`ensure_batch` 精確路徑。

**已驗證生效（09-19 四支帶 spec 的 run，`verify: calls` 全部非零）**：
union/call **16.30–18.15**，而 server 側參考值是 **18.69** ⇒ **bench 現在與 server 同側**；
修復前的症狀是 `verify: calls=0`、`union/calls = 8.00`。

⇒ 因此本檔這兩句**作廢**：
- ~~「llama-bench 對 MTP 是瞎的」~~
- ~~「llama-bench 路徑 `verify: calls = 0` ⇒ 它的 verify 不走 fast path」~~
⇒ 由它們推出的「**MTP on/off 的正確配對要在 server 路徑做（HTTP），不是 llama-bench**」
  **需要重估**（現在 bench 也能量）。
⚠ 但 **09-18 20:24 之後**量的 bench MTP 數字（§EN-187 的 `m=0.474`、§EN-190 的 bench V1 21:59）
都在修復之後 ⇒ **不受影響**；只有 09-18 上午及更早的要打折。
（全文見 `.workbuddy/memory/2026-09-19.md` §EN-200。）

## L3（async fill／把 cb 藏到 GPU work 底下）——2026-09-20 定錨（詳細在 `docs/L3_ADOPT_DECISION_2026-09-20.md`）
- **cb 是滿額串行**：78 支 log 的組內 `total ~ cb` 斜率中位 **1.455**（IQR 1.234–1.644，97% ≥1.0）
  ⇒ 每 1 ms cb ≈ ≥1 ms step（>1 那段＝`gap_L = 1.00·cb_{L-1}+0.3`，線 A 的 G1 同源）。
- **重疊做滿的硬天花板＝ `gpu_sum 181.56 ms`** ⇒ **18.59 t/s @ mean_len 3.375 ／ 22.03 @ ml 4.0**（今天是 247.98 → 13.61）。
  ⚠️ 任何寫成 `248 − 74 = 174 ⇒ 19.4` 的算法都**穿過了地板**，不可用（= 需同時壓 GPU busy，那是 G4 的事）。
- ⇒ **25 仍不可達**（需 ≤135 ms @ml3.375／≤160 ms @ml4.0，兩者都低於 gpu_sum）。L3 只把缺口 11.4 → 3.0。
- 採納條件：≤181.56 ms 為註冊上界；第 1 支 run 就要三個數同讀（`fill_wait_us` ↓ 且 `total` ↓ 且 GPU idle 未等量上升 ——
  那個否證式失敗正是斜率 ≥1 預言的）；`fill_wait_us` 在 decode step 上**從沒量過**（全庫只有 09-16 一支 prefill log）
  ⇒ 先證明它會出現在今天的 row 裡。

## ★★ `cb` 是**記憶體水位敏感**的（2026-09-20 21:5x 聯合捕獲，`§EN-350`，權威在那裡）
- **同一支 binary、同一 profile：`cb` 從 74.18 ms（29.9%）掉到 11.26 ms（9.2%），差 6.6 倍。**
  差別是 swap／水位（本次 `free=84%`；74.18 那次高 swap）。機制：`cb` ＝ ensure 的 fill、走 `pread`。
- ⇒ **「cb 佔 30%，所以 cb 是最大槓桿」依賴那個水位**，不可跨 run 引用；
  **線 I 的 victim rule 上界（Belady 31%）是在那個 cb 上算的**，水位變了上界也變。
  **兩條線數字長期對不上不是儀器分歧，是狀態分歧。**
- 引用任何 `cb`／`wait`／`union` 之前，先寫明它是在哪個水位讀的（與 `--reps` 同級的口徑要求）。
- **★ 2026-09-20 22:3x 更正（三條否證，`§EN-351`，權威在 `docs/CB_WATER_CONTROLLED_REREAD_2026-09-20.md`）**：
  **「把 usable%／swap 當強制進入條件」做不到，而且水位不是 `cb` 的主導變數。**
  ① 壓力帶**進不去**（`--max-usable-pct 15 --max-free-pct 25` 等 60 s 逾時）——壓力態是 run 自己造出來的，
  run 結束後盒子 ~2 min 自己回 `usable 56–64%`；② 水位**讀數不穩**（空盒子 free 5.62→0.06 GiB／30 s）
  ⇒ 當閘門會不可重複；③ **反例**：同載體連發，r2 進入水位四軸全比 r1 好（usable 63.8 vs 51.0、
  headroom 10447 vs 8360），`cb` 卻 23.89→**33.87**（+42%）、步時 139→**238 ms**（+71%）。
  n=4 相關係數 `usable +0.940／headroom +0.939／cached −0.836／swap +0.761`，
  **臨界 0.950，全沒過**，且 `usable` 符號與假設相反。
- ⇒ **真正隨 `cb` 動的是 run 內位置**：5/5 run 前半→後半單調衰減 **1.63–1.82×**
  （`cb_max` 4 s→0.2 s），比同載體 run 間離散（1.42×）還大 ⇒ 6.6 倍差距裡有一部分是**取樣落在 run 哪一段**。
  **`cb` 的受控條件＝「載體 + run 內位置（固定丟棄前半，或並列前／後半）」，不是水位。**
- ⇒ victim rule 優先級**不要建立在 `cb` 絕對值**上，要建在**同載體配對的 Δcb**；
  與線 I（74.18）對帳要對「載體 + run 內位置 + 頁快取狀態」。
  工具 `scripts/check/cb_headroom_probe.py`（`--runs N`、`--band any` 連發）一次給
  all/first_half/last_half/thirds + 進入水位 + run 內軌跡；`--min-*`/`--max-*` 是**記錄／篩選**，不是閘門。
- 聯合捕獲的閉合關係（可引用）：`union_sum+gap_sum = span`（殘差 −0.06 ms ⇒ 段嚴格串行）；
  `span vs total` 殘差 +0.26 ms（0.2%）⇒ 步時沒有第三塊。
  ⚠ `wait+cb+submit = total` 是**恆等式**（`ggml-backend.cpp:2691`）⇒ 零資訊量，別當證據。
  ⚠「`gap ⊆ cb+submit` 失敗 10.09 ms」＝ `wait − union`（本次 +7.97／+7.74），
  兩者不需相等 ⇒ 它不是恆等式，別當閘門。
→ `scripts/check/joint_reconcile.py`；`docs/JOINT_CAPTURE_2026-09-20.md`。

## ★★ 250／25 達成性（2026-09-20 22:2x 定錨；權威在 `docs/PREFILL250_DECODE25_VERDICT_2026-09-20.md`）
- **prefill 250 已達**：281.51（NOMINAL/NOMINAL）＋281.95；壞樣本 214.30（同形狀 1.32×）⇒ 風險是**窗口不是引擎**。
- **25 的等值線：`step = mean_len x 40 ms`**（25 = ml/step）。k=3 ⇒ ml 硬頂 4.0。
- ⚠️ **步時地板被讀到兩個值、差 1.52×，且分支結論相反**：
  **149.24 ms**（交付 work row 023021，該 run 高 swap、`cb=74.18`）vs **97.90 ms**（聯合捕獲 214450，free 84%、`cb=11.26`）。
  地板 149.24 ⇒ 25 只在「ml ≥3.731 且 step ≤149.24」的角上成立（**不可達**）；
  地板 ~98 ⇒ 低水位那次 total 122.39 ms 意味 **ml ≥3.06 就過 25**（已量過 3.375 ⇒ **邊緣可達**）。
- ⇒ **引用任何「25 的天花板」之前，必須先說用的是哪個地板。** 決定性量測＝
  「乾淨窗口 ＋ 交付形狀（prod25-stream, reps=3）＋ 同臂」把 step/union/gap/cb/ml 五數讀進同一份 log。
- 已指名槓桿總和 **+5~10%（12.57 → 13.2–13.8）**；25 需要 **×1.99** ⇒ 沒有槓桿在 3 倍以內。
- 別再做：`248−74=174`（穿過地板）、`wait+cb+submit=total` 當閉合證據（恆等式）、
  `gpu_busy_sum 181.56` 當地板（段內 buffer 併發算兩次，比值 1.22）、跨臂相乘 ml×step。

### ★★ 2026-09-20 22:5x 那次決定性量測**跑了**（`§EN-352`，權威在 `docs/FIVE_NUMBER_RUN_2026-09-20.md`）
- **結論＝分支 A**：交付形狀下 `union_sum = 175.59`（best third 仍有 159.95）⇒ 判準的 B 帶（88–125）連邊都沒碰到。
  ⇒ **25 在 k=3 交付形狀下不可達**；目標改寫 16 或改配方。同輪 `cb 76.18`、`gap 101.84`、`mean_len 2.612`。
- ⚠️ **`step` 有兩個口徑，差到 2×，本節上面那條等值線之前沒指明用哪個**：
  `step_decprof`（DECPROF work row `total`）＝**只有 graph_compute 窗口**；
  `step_round = mean_len / tps` ＝**一輪實付**。本輪 285.85 vs **561.83**（49% 在窗口外）；
  對照 server 214450 是 129.57 vs 188.47（31%）。
  ⇒ **`step = mean_len x 40 ms` 必須用 `step_round`**，拿 DECPROF 的數會把餘裕高估 ~2×。
  本輪誠實餘裕 `104.49 − 561.83 = −457 ms` ⇒ 25 差 ~5×，不是「差一點」。
- ⚠️ **單一 ~106 s 滿載 decode run 沒有穩態**：union 按 rep 單調 +26%（159.95→175.59→201.32）、cb 反向降（87.04→65.53）
  ⇒ GPU 時鐘在 run 內走低（**冷池假說被推翻**，它預測先慢後快）。
  ⇒ 引用任何 decode 數字都要假設它是在這種漂移下量的；**窗口認證要在跑之前與之後各做一次**。
- ⚠️ `cb` 的水位解釋要修正：本輪 `cb 76.18` 但開跑前 **idle free 83%**
  ⇒ 「74.18 vs 11.26 是水位造成」不成立；候選＝8 GiB 池 dirty resident／形狀（`-d 512`/`-c 4096` vs server 短 ctx）。
- **provenance：這是 22:16 建置 binary 的數字**，不是工作樹的（樹上有別條線未提交的 `process_ubatch`/
  `expert_cache_on_topk` 改動）。**warm-skip 64 的複跑仍欠**（run 1 是 `--warm-skip 0`，冷池算進 t/s）。

### ★★★ 2026-09-20 23:0x 定案（`§EN-354`，權威在 `docs/DECODE25_CEILING_2026-09-20.md`）：**25 沒有機會**
- **上面「地板 149.24 vs 97.90 ⇒ 分支 A/B」那個二分法問錯了題。** 本輪同 run 讀到
  `union_sum 106.91`（落在 B 帶）**與** `step_round 207.8`（否證 B）⇒ 分量與週期互相矛盾。
  **該問的是 `step_round`，不是任何單一分量。**
- **天花板（同 run 反推，`S0 = 207.8/(1+0.474×3) = 85.8 ms`）**：
  **k=3 即使 `a=1.0` 也只有 19.25**（需 ml ≥5.20 > 硬頂 4 ⇒ 無解）；
  **加深 k 無用**（k=1→64 只 12.16→13.16）；**k→∞ 且 a=1.0 極限 `1000/(S0·m) = 24.59 < 25`**。
- **唯一兩條路**：`m` 0.474 → ≤0.073（k=3）／≤0.184（k=8），**降 61–85%**；或 `S0` → ≤43.1 ms（**×0.50**）。
  ⇒ **門票在 expert cache pool 的每 draft token 固定代價**（46–59 ms），不在 dispatcher／k／dispatch 數。
- **推翻條件（唯一殘餘不確定）**：同 run 內量到 `t/s ≥ 12.57` 且 `step_round ≤ 160 ms`。
- ⚠️ **`--warm-skip` 跳過的是另一個接受率母體，不是只是「不計時」**：本輪 measured 段 ml=2.025、
  warmup 段 ml=**1.015**（MTP 在冷段幾乎完全失效）。整體 1.352 是壞商數
  （會推出 `step_round 138.7 < step_decprof 167.81` 的物理矛盾）。
  ⇒ **warm-skip 0 與 64 的數字禁止混用**；先前「分支 B 邊緣可達」即由此而來。
- 建議改寫目標 **16**（指名槓桿 +5~10% ⇒ 12.57→13.2–13.8）。prefill 250 不受影響。

### ★★★ 2026-09-20 23:2x 修正（`§EN-355`）：上面「25 沒有機會」是**條件性**的，不是物理的
（權威在 `docs/DECODE25_CEILING_2026-09-20.md` §7。被問「既然沒到 SSD／內存物理上限」才算出來。）
- **機型 `Mac16,12`＝MacBook Air M4（120 GB/s 規格值）；模型 12.72 GiB、3.12 bpw、A3B
  ⇒ 每 token 必讀 1.171 GB。帶寬物理上限 = 9.76 ms/token = 102.5 t/s。**
  實測 `S0 85.8 ms` ⇒ **效率只有 11.4%，餘量 8.8×**。25 t/s 只需 40 ms = 峰值 24%。
- **等效 SSD miss 率**：`t = bpt[(1−f)/120 + f/3]`；`f=20%` ⇒ 85.9 ms（實測 85.8，吻合到小數點一位）。
  **25 ⇒ `f ≤ 7.9%`；16 ⇒ `f ≤ 13.9%`。** ⚠️ `f` 是**等效**上界（把 SSD miss＋dequant＋kernel
  效率＋dispatch 全折算），真實 miss 率可能更低。
- **根因＝16 GB 這台機器的牆**：模型 **12.72 GiB** vs pool **8 GiB** ⇒ **4.72 GiB 在 SSD**，
  SSD 比統一記憶體慢 **40×**。這就是 `cb` 對水位敏感（74.18 vs 11.26）的機制。
- ⇒ 判決改寫：**25 在「當前每 token 讀取效率 11.4%」下不可達，是效率工程問題，不是物理限制。**
  上一節的 19.25／24.59 是「`m`、`S0` 凍結」的算術，而它們正是 miss 率的函數。

### ★★★★ 2026-09-21 00:2x 剩餘之路清點（`§EN-360`，權威在 `docs/REMAINING_PATHS_2026-09-21.md`）
使用者再加兩個約束後重算：**accept 不能用 `CGC_RN_ROUTING=1+CGC_WCOLD_EN=1` 那支**
（25.9 t/s／98.7% accept 但 quality 0.3，renorm mask bug）＝量測假象；09-05 的 0.9974 同理
（`auditable=NO`）。
- **① M4（唯一無損、已實作、未測的槓桿）**：交付跑 **temp 0.4**，但 draft chain 在 **init** 時從
  `params_base.sampling` 建、被啟動行釘在 **`--temp 0`（greedy）**，且沒人把 request 的 chain
  交給 draft ⇒ **draft 是 greedy、target 是 0.4 採樣，這就是 accept=0.537 的結構性原因**。
  `CGC_MTP_SAMPLER_PARITY` 為此存在但 **no profile sets it ⇒ 此處每一個 MTP 臂都是 off**；
  `CGC_SERVER_MTP_P_MIN` 也 plumbed 了，**此前每臂 `p_min=0`**。
  上界：k=3 ⇒ ml≤4.0 ⇒ **step 不變下 14.9 t/s**。⚠️ **必須聯合判讀**：G6 實測
  「+11% ml 但 +14% step ⇒ 淨 −0.45 t/s」。命令見該文件 §4。
  **★ 已跑完並判死（`§EN-361`，2026-09-21 00:45，`docs/M4_PARITY_AB_2026-09-21.md`）**：
  paired 3 reps、rotating order ⇒ **REFUTED**（3/3 同號 −0.91／−1.74／−2.73，中位 −1.74 t/s）。
  控制中位 **12.82**，離 25 差 **11.9 t/s**。有效性三驗全過（API 符號 `T` 在
  `libllama-common.0.0.279.dylib`、呼叫點 `U` 在 `libllama-server-impl.dylib`、env 到引擎
  27 vs 25 個 `CGC_*`）⇒ 不是對空氣施力。**★ r0 accept 51.49→57.94%（+6.45 點）而 t/s −0.91**
  ⇒ G6 形狀在獨立槓桿上複現 ⇒ **accept 只能當機制指標，不能當目標指標**。
  ⇒ **M4 死、25 下架、目標改 14。**
  **★★ 第二次配對跑（`§EN-362`，2026-09-21 01:00，同一文件附錄 A）**：`--reps 4`、
  floor 7800（`reclaimable` 7978，仍差 22 MB 到 8000）⇒ Δ 全部為負（−0.78／−1.04／
  **−0.09**／−0.77）但最弱 0.09 < 0.50 ⇒ **單獨 = BELOW THRESHOLD**。
  **兩輪合併 N=7：7/7 全負、中位 −0.91、平均 −1.15、符號檢定 p=0.0156** ⇒ **方向判死**；
  量級跨輪不穩定（0.09–2.73）⇒ **引用時兩個標籤要一起帶**。
  **單流 decode 可引用基準＝控制臂 n=7 中位 12.77 t/s（11.83–13.46）**，與 bench 12.57 同形狀
  ⇒ **12.6–12.8，距 25 差 12.2 t/s（需 1.96×）**。Δaccept +1.19 pp（5/7 正）而 Δt/s −1.15
  ⇒ **accept 軸封版**。附帶更正：第一輪「Δ 單調惡化」是窗口問題，不是 M4 的性質。
- **② 並行 decode（吞吐口徑）：~~從未測、量級最大~~ → ★★ 2026-09-21 01:00 使用者裁定
  **25 tok/s 指單流 decode 速度，不是吞吐** ⇒ **本路整條出局**（不再是候選，勿再提）。
  （原紀錄：`F=79.9 ms/step` 不隨序列數變 ⇒ batch 2–4 可攤薄；現有唯一相關實測
  `decode-up`（`-b 2048`）5.32 t/s 是把 decode 形狀 prefill 化，不是多序列並行。）
- **③ 降 `m`（0.474）**：`m→0` 且今天 accept ⇒ 30.4 t/s，但 09-05 的 `m≈0` 極可能是 bug 副產品，
  且池／miss 機制已排除 ⇒ **未歸因，無可執行手段，只能列待研究不列計畫**。
- **④ 縮 `S0` 的非頻寬部分（8.8×）**：已證不是 miss／池／dispatch ⇒ 同樣未歸因。
- **`gap` 不是獨立靶**：既有結論 `gap` 只有兩成分 ＝ `cb` ＋ 每邊界 0.29–0.35 ms
  （40 段 ⇒ 地板 ≈12.5 ms）；本輪 `cb=41.8`、`gap=52.46`，差 10.66 ≈ 地板。
- **判決：單流 decode 25 在「不動模型／不降精度／16 GB／無損」下沒有已知可執行路徑。**
  最樂觀現實路徑是 ① ⇒ **14.9 t/s**（G6 警告可能淨負）。**建議改寫 14–16**，或先裁定
  25 是單流還是吞吐口徑。**若 M4 也淨負 ⇒ accept 軸封版、25 下架、目標定 14。**
- **槓桿排序**：① 模型整包進池（**bpw ≤1.96 或模型 ≤8 GiB**，理論 ~100 t/s）② ≥19–24 GB 機器
  ③ prefetch／專家局部性（**線 I 的 `cb` 靶**）④ 改配方。**kernel 融合／省 dispatch 量級不夠**（G4 0.0736%/次）。

### ★★★ 2026-09-20 23:3x 用**實測 miss**重算（`§EN-356`）——上面「等效 miss 20%／7.9%」的模型是錯的
- **結構（讀 gguf）**：41 層／256 專家／top-8／FFN 512／emb 2048；總 35.51 B，**專家 ≈33 B（93%）**。
  每 token 專家 **381 MB**、dense **790 MB（常駐、不受 miss 影響）**。
- **實測 miss 率 22.4%**（`hits=28584 misses=8246`）；線 I F1：**73.83 miss/step × 0.695 ms =
  51.3 ms**（cb 77%）＋**每層 barrier 12.86 ms**（有 miss 就付，m=0→0.01 ms），有 miss 層 **27.96/40**。
- ⚠️ **線 I 更正：邊際 miss 走 1.6 GB/s（page cache／RAM 拷貝），不是磁碟**（該輪磁碟峰值 823 MB/s）
  ⇒ 上面用「SSD 3 GB/s」套全部 1.171 GB 是錯模型。miss 的成本是 **page fault＋拷貝＋等待**。
- **新分解：單 token 85.8 ≈ 64 ms（miss 相關，75%）＋ 22 ms（其他）** ⇒ **miss 清零上限 ≈45 t/s**。
- **★★ 2026-09-20 23:3x 使用者裁定：xxs 已是精度下限，不再壓**（`§EN-357`）⇒ **槓桿 4（bpw）下架**。
  - 剩餘槓桿順序重排：**① accept（量級最大、佔損失 76%）② 池 143→179 slots（上界 ≤+2.5%）
    ③ 穩態 vs ramp 的驗證 ④ 換機**。**kernel／dispatch 封版。**
  - **⚠️ 上面 ② 已被 `§EN-358` 撤回**：「加大 pool／提高 hit 是槓桿」**已在 09-15／09-17 被推翻兩次**
    （71 slots 反而快，**容量更小反而快 ⇒ 容量不是約束**）。**別再花時間在 pool 大小上。**
  - **★ `§EN-358` 給出的新第一槓桿（零精度代價）＝ IO／駐留路徑**：
    **reads/token 13.5 → 95.6 → 475（35×）**（09-05 → 今天 MTP-off → 今天 MTP-on），
    misses 3397→20080→77266。**09-05 那筆 `resident=0.00 MiB` ⇒ 另一條駐留路徑
    （mmap／page cache 常駐，非 8 GiB anon pool）**。
  - **F/V 擬合：`F=79.9 ms/step`、`V=16.1 ms/token`**（09-05 的 MTP 邊際 = 16 ms ≈ 普通 token，
    **m≈0**；今天 MTP-on 邊際 63–130 ms ⇒ **3.9–8.1×**）。`ml=4 → 144 ms → 27.7 t/s` 自洽復現。
  - **m→0 的量級**：ml 2.61（今天 accept）⇒ **21.4 t/s**；25 需 `ml ≥ 3.34`（accept **0.780**）。
  - **`miss attribution: compulsory 68–75% / capacity 25–31%`**；compulsory ≈ `143×41=5863 ≈ 6214`
    ⇒ **compulsory ＝ 池首次填滿，一次性開機成本** ⇒ **所有 bench 量的是 ramp 不是穩態**。
  - **引擎自記 hit-vs-slots：96/128/143/192/256 → 79.7/87.7/90.8/97.1/100%**；
    `per_slot=1.3906 MiB`（MAX over TRUNK）⇒ 143=7.96 GiB、179=9.97、**256=14.25 GiB（裝不進）**。
  - **27.71→9.74 分解：tokens/step −49%（−13.7）＋ step +44%（−4.3）**。只修 accept ⇒ 19.25；
    兩者都修（ml=4）⇒ 需 step ≤160 ms（−23%）。**⇒ bpw 凍結下 25 不可達，可達 12.5–14。**
  - ⚠️ **待仲裁矛盾**：線 I「miss ≈ step 35% ⇒ 清零 +54%」vs 池掃描「143→256 ≤+2.5%」。
    （可能解：barrier 是階躍的，清零最後 10% miss 換不到 barrier。）
- **3（prefetch）**：工作集反推 `8/(1−0.224) = **10.3 GiB**`（專家總 11.5 GiB ⇒ 長序列幾乎全摸到）
  ⇒ 看起來是**容量 miss，prefetch 救不了**。決定性量測＝**`n_miss_capacity` vs `n_miss_compulsory`**
  （`llama-expert-cache.cpp:932/934` 有計數但 `final stats` 沒印分類）。甜點只餘 barrier 的階躍性。
  **`-expert-cache` 用過 109 次都是 8 GiB**（10 GiB 只見於非性能的 `bitident_ab.py`）⇒ 掃 10/11 GiB
  幾乎沒做過，但 16 GB 餘量只 ~2 GiB，很可能踩 §EN-350 的 `cb` 水位坑。
- **4（改配方）——最確定，且正確形式是「只壓專家」**：dense 常駐不佔 pool，要進池的是
  **專家 ≈11.5 GiB**。專家 bpw 3.0→**2.5**（IQ2_XS）⇒ 專家 9.6 GiB、工作集 ~8.6、miss ~7%
  ⇒ **單 token ~40.8 ms ⇒ 24.5 t/s（無 MTP）／~26（含 MTP）** ✓；bpw→**1.94**（IQ2_XXS）⇒
  全進池、miss→0 ⇒ **~45 t/s**。**別降 top-k**（質量代價大於降 bpw）；**降專家 bpw 雙重收益**
  （工作集↓＋每 token 位元組↓），且模型已是 `UD-IQ3_XXS-denseIQ4X` 混合量化 ⇒ **工具鏈現成**。

### ★★★ 2026-09-21 00:2x IO 路徑 A/B 實跑（`§EN-359`，權威在 `docs/IO_PATH_AB_2026-09-21.md`）
**上面 `§EN-358` 列的第一槓桿「IO／駐留路徑」—— 實跑後撤下。判準跑之前寫死在
`Backup/phase_decomp/io_path_ab.py`（自測 7/7）。**
- **① 關池物理不可行**：`-expert-cache 0` 在 16 GB 上**兩個 load-mode 都死於
  `CGC-METAL-FAIL status 5`**（load-mode none 16 s、mmap 108 s）。
  **`recommendedMaxWorkingSetSize = 11453.25 MB` < 模型 12.72 GiB（13026 MB），差 ~1.5 GB。**
  ⇒ **池不是優化，是讓這支模型能在這台機器跑起來的前提**（`run_server.sh` 的
  `EXPERT_CACHE_BYTES=0` 只出現在 OOM-safe 的縮小形狀：ctx 1024 / ngl 8）。
- **② 縮池無效**：4 GiB（09-05 的池大小）**10.60 t/s** vs 8 GiB **10.28**（+3.1%），
  而**同場 8 GiB 控制臂自己從 8.36 量到 10.28（+23%）** ⇒ +3.1% 在噪音裡。**09-05「4 GiB 更快」不重現。**
- **③ miss 不是 t/s 的驅動項（仲裁了上次那個矛盾）**：4 GiB 的 miss 是 8 GiB 的 **1.91×**
  （19447 vs 10192），capacity miss **3.15×**（11372 vs 3612，佔 58% vs 35%）——池真的餓到——
  **而 t/s 是 10.60 vs 10.28。miss 翻倍、t/s 不動。**
  ⇒ **池掃描那邊（143→256 ≤+2.5%）對，線 I 的「清零 miss 值 +54%」不成立。**

  - ⚠ **2026-09-25 補：同一旋鈕在 repo 裡有一筆矛盾的記錄，引用前先讀這裡。**
    `docs/CGC_ENGINE_WHITEPAPER_2026-09-24.md`（「L1 pool 8G→4G」一行）寫 **−21%（0.79×）**，
    與上面 §EN-359 的 **+3.1%（噪音內）** 直接衝突，**兩者從未被仲裁**。
    以 rigor 論 §EN-359 更強（判準跑前寫死 ＋ 自測 7/7）；白皮書那行**未引來源**。
    ⇒ **引用「縮池會降速」時必須註明來源與未仲裁狀態。**
  - ⚠ **3 GiB 這一格從未被乾淨量過**：有記錄的是 2 GiB=0.72×、4 GiB≈1.0×。
    09-24 的 k-sweep 在 3 GiB 跑，但它的 hook-on 臂（nospec）**兩次都被自己的閘門判 POLLUTED**
    （reclaimable 6309／4370 < need 8000）⇒ **不可用作 3 GiB 的證據**。
  - ★ **S1 讓 pool 容量與速度解耦**：同一個 3 GiB 池，S1 臂（`file_reads=0`、無逐出）跑出
    **22.45 t/s**，是同池 hook-on 臂的 3× 以上。⇒ **「縮池降速」是 hook-on regime 的結論，
    不自動搬到 S1。**（代價：填充不跑、正確性未驗證。）
  - ★ **兩條線的 pool 不一致有制度原因**：`budget_gate` 只接在
    `rho_fill_ab.sh`／`route_overlap_3prompt.sh`／`masscov_decode_shape.sh`，
    **`llama_bench_matrix.py` 沒接** ⇒ 本線跑 8 GiB、執行線跑 3 GiB（16 GB 上 8 GiB 靜態超訂
    4838 MiB，strict 會連交付 cell 一起拒跑）⇒ **兩邊的絕對 t/s 不可併排。**
- **④ `pread_usec` 不是成本代理**：4 GiB 1143 s、8 GiB 464 s（2.5×）而 t/s 一樣
  （`llama-expert-cache.cpp:2432` 已註明它是 worker thread **加總**，非牆鐘）。
- **⚠️ 判準在跑前被我自己改過一次**：原提「`reads/token` 從 ~95 掉回 ~13」是**錯的**——
  `file_reads`/`pread_usec`/`hits`/`misses` 是**池**的計數器，`llama.cpp:467` 只在
  `expert_cache_bytes > 0` 建池 ⇒ 關池時它們**不存在**（不是讀到 0）⇒「reads→0」是量測假象。
  決策變數改為**交付 t/s**。（本線第四次「商數／指標來自兩個 regime」。）
- **`cb` 兩臂都讀到**：關池 `cb=0.15/0.37`（全部 row 中位）vs 8 GiB work-row `cb=41.8`
  ⇒ **`cb` 的分量就是池**（但它仍然在 OOM 之前就死，拿不到 t/s）。
- **⇒ 25 在「不動模型／不降精度／16 GB」下沒有已知可執行路徑**；可達區間仍是 **12.5–14**。
  剩下只有：① 09-05 的差異來自**模型檔**（`Qwen3.6-...-UD-IQ3_XXS` vs 今天
  `Nail-...-denseIQ4X`，27.71 已被標 `auditable=NO`）② **accept 軸**
  （`CGC_RN_ROUTING=1 + CGC_WCOLD_EN=1` 曾實測 25.9 t/s / 98.7% accept，quality 0.3）。
- **噪音再確認**：8 GiB 控制臂場內 8.36 → 10.28。**任何 <27% 的效應必須同場配對，不能跨場比。**

## 兩個「並行」旋鈕已結案（2026-09-21，`§EN-364`/`§EN-365`；權威在那裡）

- **`CGC_SERVER_WORKERS`（IO 並行，池 fill worker，預設 8）＝ 不起槓桿。**
  配對 A/B（prod25／decode／pairs 3／reps 3，走 llama-bench）：**W8 vs W32 = 1.0805（3/3 偏 W8）**
  ⇒ 往上加**慢 ~8%**；**W8 vs W4 = 0.989（跨 1.0，未決）** ⇒ 往下減沒差。 ⇒ **維持 8，別再掃。**
  儀器 MAD 5.2–5.8% ⇒ 8% 只略高於雜訊，結論寫「沒油水」不寫「32 有害 X%」。
- **`-np N`（序列並行）＝ 不是槓桿，是 7 倍損失。** ⚠️ **llama-bench 沒有 `-np`/`--parallel**
  （`--help` 全表無此項）⇒ 它結構上**不能走交付儀器**，只能 HTTP ⇒ 絕對值不可引用，只有對內比值可用。
  實測（server 自己的 `slot print_timing`）：**np1 = 13.88 t/s（72.04 ms/tok）**；
  **np4 = 0.44–0.51 t/s／槽（1965–2292 ms/tok），聚合 ≈ 2.0 t/s ⇒ 比值 0.14。**
  4 槽數字完全相同 ⇒ server 確實批進同一 step，機制是活的，只是代價遠大於收益。
- **★ 機制（比結論重要）**：np4 的 `jobs=2,218,197`（35× np1 的 62,829）而
  `bytes=0.57 GB`（反而比 np1 的 8.36 GB **少 15×**）⇒ **每 step 約 13.7k 個 257 B 的讀**
  （np1：1.14k 個 133 KiB），`us/job` 18.9 → 32.5 ms；而 **pool hit 反而從 70.9% 升到 94.1%**。
  ⇒ **不是 miss、也不是頻寬，是「專家 union 變寬 → fill 路徑碎成大量小 job」的 per-job overhead。**
  這是「miss 不是驅動項」的第二次獨立證據（第一次見上一節）。
- **★ 更正（重要）**：`§EN-363` 的 bytes 模型 `step=(D+B·E)/BW_eff` 只算了 790 MB dense 的攤薄，
  沒算 per-job overhead ⇒ **N=4 預測 16.05、實測 2.0（差 8×）**。
  **該模型已從「假說產生器」降級為「已證偽的外推」，不要再拿它排並行的優先序。**


## ★ `-np`（併發序列）不是通往 25 的路（2026-09-21 01:57 實測；權威在 `docs/PARALLEL_AND_WORKERS_AB_2026-09-21.md` §3，
##   理論那半在 `docs/NP4_THEORY_VS_MEASURED_2026-09-21.md`，`§EN-366`）

- **N=1 → N=4：單流 13.78 → 0.44–0.51 t/s（27× 損失）；聚合 13.78 → ≈2.0 t/s（7× 損失）。**
  預註冊判準 `r = agg(4)/agg(1) < 1.10 ⇒ NOT A LEVER`，實測 **r ≈ 0.14**。
- 25 已裁定＝**單流** ⇒ 並行在單流上 27× 損失、聚合 7× 損失，**兩條路都不成立**。
- **名詞**：`-np N` 是 n_parallel ＝ **continuous batching**（N 條獨立序列），**不是**序列並行
  （把一條序列切給多裝置）；單 Metal 裝置上 llama.cpp 沒有序列並行。
- **機制＝ per-job overhead**：job 大小 **133 KiB → 257 B**、每步 **1 142 → 13 692** 個。
  **不是 miss**（命中率 70.9% → **94.1% 反升**）、**不是頻寬**（bytes 少 44×）。
  ⇒ 「miss 不是驅動項」的第二次獨立證據。
- ⚠️ **「step = (dense + B·expert)/BW_eff」這類模型已被證偽 8×**：我從 gguf 實讀推出的
  dense **827 MB/步**、expert **346 MB/token** 與 §EN-363 的 790／381 收斂，兩者都預測
  `-np 4` 聚合會升（27.4 / 16.05），實測 **2.0**。
  ⇒ **別再用它排並行優先序**；它只在**單序列**範圍內對兩個標定點（85.8 / 167.81 ms）還吻合。
- **真實幾何（gguf 實讀，之前一直當 128）**：`expert_count=256`、`top_k=8`、`block_count=41`、
  `full_attention_interval=4`（**混合線性注意力**，1/4 層才全注意力）、非 MoE **20.68 MiB/層**
  vs 專家 **1.083 MiB/個** ⇒ 單 token 每層 29.34 MiB，**非 MoE 佔 70%**（「batching 該很划算」
  的直覺來源，已被證偽）。
- `D(B) = 256·(1−(248/256)^B)`：B=4→30.5、8→57.4、16→102、32→163；池 143 slot/層 ⇒ **B≈25 飽和**（算出來未實測）。


## ★ 25 tok/s 是 GPU 效率題，不是 IO 題（2026-09-21，`§EN-367`，權威在
##   `docs/WHY_MODEL_MISSED_AND_IS_25_REACHABLE_2026-09-21.md`）

- **步時佔比（joint_capture n=65 穩態中位）**：GPU busy **80.0%**、GPU idle(gap) 19.2%、
  **專家 fill(cb) 只 9.2%** ⇒ **IO 全免費也只 +10.1% t/s**。
- **25 需要 step 快 1.80×**（166.4 → 92.4 ms/step）⇒ **GPU busy 要快 1.8–2.2×**。
- **沒撞物理牆**：每 step RAM 1.34 GB / GPU busy 97.9 ms ⇒ 有效 **13.6 GB/s ≈ 峰值 11%**；
  @100–120 GB/s 地板 **172–207 t/s**。⇒ 現況是**效率 11%**，不是極限。
- **已知槓桿全加 ⇒ 13.2–13.8** ⇒ 25 在已知槓桿上**不可達**；唯一未量過的大空間是
  **expert GEMV/MUL_MAT kernel**（瘦高 GEMM，前例 2.5–3.4×），**但它的時間佔比沒人量過**。
- ⚠️ **「MUL_MAT 23.1%／elementwise 27.7%」是 dispatch 次數不是時間** —— 別拿它當時間佔比。

## ★ IO 粒度曲線（`scripts/check/io_granularity_curve.py`，2026-09-21）

- **一次 pread 固定成本 ≈ 95–100 µs**：256 B ＝ 97.0 µs、4 KiB ＝ 103.6 µs
  ⇒ **4 KiB 以下，「讀幾次」決定成本，「讀多大」幾乎無關**。
- **128 KiB 單執行緒 0.609 GB/s（今天的 job 大小）→ 4 MiB × 4 執行緒 8.241 GB/s ＝ 13.5×**，
  純粹來自（大小, 併發）。擬合 `t = size/BW + tau` ⇒ BW 2.03 GB/s、tau 136 µs（tau 非常數，已印）。
- **交叉驗證 `llama-expert-cache.cpp:48`「serial ~204 µs cold」** ⇒ 本曲線 128 KiB 單執行緒 200.5 µs，
  兩個獨立儀器對上 ⇒ 池的 job ＝ ~128 KiB pread / ~200 µs 冷讀（已確認 fill 走 `pread`/`preadv`）。
- **適用範圍**：N=1 別追它（只 9.2%）；**在 IO 被暴露的區間（冷池／長 ctx／`-np` ≥ 2）它是決定性的** ——
  `-np 4` 的 job 133 KiB → 257 B 就是 27× 單流損失的直接成因。

## ★★ 模型方法論（血淚，`§EN-367`）

`step = (D + B·E)/BW_eff` 有 **3 個自由參數、只有 2 個標定點**，且兩點都在同一條序列內
⇒ **必然吻合，不是驗證**；`-np` 沿著它沒有的維度（job 結構／暴露與否）移動 ⇒ 錯 8×。
**先數自由度再數標定點。** 且驅動項會換區：N=1 GPU-bound、N=4 job-bound ⇒ 跨區外推必錯。


## ★ MTP verify 的寬度與接受率（2026-09-21，`§EN-368`）

- **設定**：`--spec-type draft-mtp --spec-draft-n-max 3` ⇒ **verify 上限 4 token**；
  draft head = 模型自帶 nextn（`nextn_predict_layers=1`）；decode graph width = 8（`cap=8`）。
- **現在實測**：acceptance **0.444 / 0.459**、**mean len 2.31 / 2.35**、hook `ntok=2`
  ⇒ **今天多數 verify 步只有 2 個 token，沒吃滿 4**。
- **歷史 25 t/s 那一檔**：`draft_accept 98.2%`，日誌裡有 **`0.97024 / mean len 3.91`**。
  acceptance 在全部日誌裡是**雙峰**的（0.11 → 1.00），**text/temp 敏感**。
- **★ 算術**：mean len 2.31 → 3.91 ＝ **1.69×** ⇒ 步時不變時 13.78 → **23.3 t/s**。
  ⇒ **「把 MTP chain 從 2 拉回 4」是比「GPU kernel 快 2.2×」更接近 25 的路，而且是查 regression。**
  ⚠️ 未判定退步 vs prompt/temp：06:52 基線是 temp 0.4 / top_p 0.8；-np 那支 log 有 `--temp` 重複指定 warning。
- **★ 更正 `-np 4` 的歸因**：死因**不是** union 撐寬 —— **MTP verify 一樣撐到 ~30 個/層**
  （`run_server.sh:532-533`）卻是基線。4×1 142 = 4 568 ≠ 實測 13 692，**還差 3× 未歸因**
  ⇒ 殺手是 **per-sequence 的 job bookkeeping**，不是 union 寬度。


## ★ 「換一個開源的 verify 工具」不能救 25（2026-09-21，`§EN-369`）

- **verify ＝ 目標模型的前向，不是可替換部件**：llama.cpp MTP 把 n+1 個 draft token 放進
  **同一張圖一次 forward** 驗完 ⇒ 沒有 per-token 驗證迴圈可優化。換工具只買到常數因子；
  **用更小的模型當驗證者 ⇒ 破分布保證、破 G2/G5**。
- **25 ＝ 今天的 `m` ＋ 歷史的 `a`**：`S=(1+a·k)/(1+m·k)`，k=3。
  今天 `a=0.444, m=0.322 ⇒ S=1.186`；歷史 `a=0.982` 配同一個 m ⇒ **S=2.007（＝25.17）**。
  反解非 spec 基線 11.62 / 12.54，與實測非 spec 11.0–11.3 吻合 ⇒ **模型自洽**。
- **25 的兩條路**：固定 a ⇒ `m ≤ 0.055`（−83%）；固定 m ⇒ `a ≥ 0.977`（歷史 0.982）
  ⇒ **攻接受率，別動 verify**。
- **m 的物理量（算術吻合 0.7%，機制待證）**：`m×85.65 = 27.58 ms/token` ≈ 每 token 多讀
  **355 MiB**（8 專家 × 41 層 × 1.083 MiB）÷ 有效 **13.6 GB/s = 27.4 ms**
  ⇒ m 是 union 增長在我們低效頻寬上付的錢 ⇒ 拉不起頻寬就動不了 m。
  ⚠ 與 09-18「bytes→時間彈性 0.10–0.17」不矛盾（那是否定**彈性**，這是否定**量級**之外的事）。
- **唯一有 upside 的 verify-side 想法＝ tree verification**（等效抬 a），但要 top-k 分支 draft ＋
  Metal tree mask，且**寬 union 正是 `-np 4` 的死因** ⇒ 別當便宜路。


## ★ `--spec-draft-n-max 1` 的接受率（2026-09-21 實測，`§EN-370`）

- **k=1：acceptance 0.875（238/272）、mean_len 1.875；k=3：0.698（345/494）、mean_len 3.095**
  （同 prompt、prod25；全 repo 207 支 log 原本全是 n_max=3，這是第一次量 k=1）。
- **接受率隨深度下降，但每步 token 數仍上升（1.875 → 3.095）⇒ 看 `mean_len` 不是看 `a`。**
- server `m=0.322` ⇒ k=3 贏（S 1.57 vs 1.42）；**k=1 要贏需要 `m > 0.482`**
  ⇒ bench（m 0.474，k=1 自己 0.759）上 k=1 贏、server 上 k=3 贏 —— **兩儀器在此軸又相反**。
- **★ 接受率是 prompt 的強函數**：同一 prod25＋n_max=3，01:30 那支 0.444 vs 本輪 0.698。
  ⇒ 查「0.982 → 0.444 是否 regression」必須**同 prompt** 重跑。
- 旋鈕：`CGC_SERVER_MTP_N_MAX`（`run_server.sh:436`），ambient env 會被 `decode_step_profile` 繼承。
- `draft acceptance` 在 `slot print_timing` 裡（**slot 累計**、多 task 印同一組），
  只在 `Backup/cgc_logs/llama_server_*.log`，不在 profiler 的 launch log。


## ★★ 2026-09-21 K0–K5 結案總表（由 `MEMORY.md` 移入，本節為權威）

### K0／K1（已結案）

`threads ÷ 輸出元素` = 16–128（每個觀測到的 decode 形狀）⇒ 輸出軸早已切滿，
加 thread 只能切 K ＝ **G2 禁區** ⇒ **K1 不做**；合成的 13.6× 不適用。
⇒ **P(25 t/s) <3%**、中央情境 **~14–15 t/s**；K4 主機側否證（`gpu_union` 佔步時 92%）。
→ `docs/K0_RESULT_2026-09-21.md`、`METAL_OCCUPANCY_PROJECT_PLAN_2026-09-21.md` §11

### K2（冗餘 bytes）證偽＝空（2 臂取數）

CPY 實為 **210 個**（MTP on）／120（MTP off）⇒ MTP 專屬只 90 個、只搬 8.64 MiB
⇒ **≤0.6% 步時，且真會被讀不能刪**；**舊說「120／124 個 ≤22%」停用**。
→ `docs/K2_CPY_AUDIT_2026-09-21.md`
**零位元組 CPY 那 4.4% 也已證偽（§8，純讀碼）**：`ggml_is_empty()` 為 true 的節點在
`ggml-metal-ops.cpp:68-74` 就被濾掉、根本不進 encode 名單（第二道閘門 `:238`）
⇒ **成本 0** ⇒ **K3 上界退回 5.3%，不是 9.7%**。

### K3（dispatch 單價）複審 → 實測結案

- **複審（§EN-401，純讀碼）**：「集群 1 的 72」**是真 dispatch 不是圖節點**
  （`CGC_ELEMW_CENSUS` 在 `ggml-metal-ops.cpp:573-609`，位於所有過濾與 dispatch switch 之後）
  ⇒ **不打折**。但舊單價 0.0736% 的分母有三個問題：① `metal_fusion_dispatch_cost_ab.py:140`
  regex **同時吃匯總行** ⇒ `disp_total` ≈ 2× 真實量；② 普查的「graph」是 **segment**
  （`idx==0` 每顆 cb 都觸發）⇒ 「1098/步」是 48 段的合計；③ 8.5% 受 thermal 污染偏大。
- **實測（§EN-402，2 臂，0 重建）→ cluster-1 ≈ 2.3%，上述區間作廢**：
  OFF 臂 t/s **+11.64%**、`gpu_union` **+11.25%**（兩儀器吻合，兩臂 NOMINAL→NOMINAL、
  sentinel 皆 HEALTHY），Δdispatch（同 48-cmd_buf 窗口）=143。
  三個一直錯的數字：① **「1098 dispatch/步」作廢** —— `CGC-GPUTIME` 按步印 `segs=40`／
  `bufs≈353`，census 的 `graph=` 單位是 **command buffer** ⇒ **48 個只覆蓋一步的 23.9%**，
  真值 ~3056/步。② **「72 個/步」作廢，真值 117**（SUM_ROWS/CLAMP/DIV 各 39 ⇒ **39 層不是 24**）。
  ③ v1 的 5.3% 錯在**單位錯配**（拿「每 dispatch、每窗口」的價乘「每步」的數量，放大 4.176×）。
  ⇒ `k = 3804/911 = 4.176`（**別用 `bufs÷48=7.354`**，高估 1.76×）
  ⇒ **cluster-1 = 11.64%×117/597 = 2.28%**（還是上界：OFF 臂省的中間結果落地，cluster-1 沒有）。
  → `docs/K3_PRICE_MEASURED_2026-09-21.md`

### K5（「每節點 ~45.6 µs 常數」）→ (C) 成立，作廢

假說（§EN-403）：① 步時 ≈ 50.8 µs × work_node 數、截距 ≈ 0 ⇒ 時間由**節點數**定；
② µs/node 眾數 = 45.6，11 種 op 全落 45.6±0.1 ⇒ 像常數；③ 扣掉 K3 一個 dispatch 17.9 µs
⇒ 27.7 µs 是融合省不掉的，×2365 node = 65.5 ms/步。三候選：
**(A) grid 太小**（`ggml-metal-ops.cpp:915-921` `dispatch_threadgroups(ne11,ne12,ne13,nth,1,1)`
grid 只由行數定；decode `ne11=T≈1` ⇒ 1 組 × 256 threads vs 駐留 ~1024 ⇒ 只用 25%；
若成立不在 G2 禁區，elementwise 佔 work 48% ⇒ 上界 ~30%）、
**(B) Metal 單佇列 per-kernel 固定成本**（不可改、天花板鎖死）、**(C) `wcntw` 口徑假象**。
⚠ 圖 dump（20671 節點）重算得 prefill threads 中位 2048 > 1024 ⇒ (A) 在 prefill 不成立；
但那份是 prefill（`CGC_GRPH_DBG` 只 fire 前 6 次），decode 的 `ne11≈1` ⇒ (A) 在 decode 下可能成立。
→ `docs/K5_GRID_HYPOTHESIS_2026-09-21.md`（**§2 數字不可引用**）

**判決（§EN-406）：(C) 成立，30–38% 上界作廢** → `docs/K5_VERDICT_2026-09-21.md`
① **`wcntw` 是分攤不是測量**：`ggml-backend.cpp:2421` `nsop_wns[q] += dur*ocnt[q]/owork`。
   `uni` 欄**全 -1**；Σ`wcntw` 只蓋 87–93%；**分攤指紋**：CLAMP(39)/DIV(39)/GLU(78)/SUM_ROWS(39)/
   MUL_MAT_ID(117) µs/node **恰好都 52.05**。Σ_q wcntw[q] = Σ_b dur_b = 總時長（**精確劃分**）
   ⇒ op 在 buffer 間均勻（實測 `ub` 每種 op 都 75–86%）⇒ **每種 op 必然回歸 總時長÷總節點數**
   ⇒ **落在常數上的 op 資訊量為零；偏離的才有資訊**（GATED_DELTA_NET 188／FLASH_ATTN_EXT 120／
   SOFT_MAX 29，因集中在少數 buffer）。
② **M 掃描分不出 (A)/(B) —— 數學同形**（固定開銷被 M 攤薄，兩者預測一樣）。
   既有 `M_WIDTH_CURVE` pool 路徑 `step ≈ 250.0 + 46.4×M`（R²=0.868）⇒ 確有 ~250 ms 與 M 無關，
   但**不能**除以節點數當「每節點固定成本」（M=1 讀權重本就與 M 無關 ⇒ 截距最大塊是權重頻寬）。
   ★ **邊際價 ≠ 平均價**：250 ms÷3056 = 81.8 µs/dispatch，是 K3 **邊際**價 17.9 µs 的 **4.6×**。
③ **正確工具 `CGC_GPU_NODES_MATRIX=1`（0 重建，註釋 `:2438`）** ⇒ 逐 range `dur_ns`＋每 kind 節點數。
   每步帳：64-node range **44.5 個/步 佔 61.3%**；5-node 214.6 個 20.9%；4-node 62.6 個 13.6%
   ⇒ **µs/node 17.7–110.9，6× 跨度 ⇒ 「45.6 常數」不存在**；且**長尾**
   （5-node 中位 58.2 vs 均值 165.1，2.8×）。
   ★ **別去拆 `n_main` 打包**：64-node range 內容 = `ffn_moe_` 25.9%／`cache` 9.6%／
   `ffn_moe_add` 9.1% ⇒ **MoE 真實大計算 = 讀專家權重 = L4 頻寬問題（已判不可及）**。
   （5-node range 才是小 op 群：cache 13.0%、q_conv/k_conv/gate/z-/norm/linear_attn 各 6.0%，佔 36.5%。）
⇒ **45.6 作廢／K5 上界 30–38% 作廢／L2 歸零／P(25) 回到 <3%**；
   (A)/(B) **仍未定**，且 0 重建工具已用盡 ⇒ 要判只能拿 decode 形狀 `ne`（**要重建**）。
⇒ **per-kind 最小二乘別再試**：`CGC-NSM` 的 kind 是**張量名前綴**不是 op 類型 ⇒ 共線性，
   fit 122.7% 且出現負值（只用 ≤5 節點 fit 87.5% 但 `t_k` 從 4 到 613 µs/node）。

### ★★★ 天花板分層（§EN-404）→ `docs/CEILING_STACK_2026-09-21.md`

**所有舊的「融合能省 X%」都用錯單價，全部作廢**（舊 0.0736 → **K3 實測 0.0195%/dispatch/步**）。

| 層 | 天花板 | 狀態 |
|---|---|---|
| L0 今天交付 | **12.57 t/s** | 實測 |
| L1 ~~+5.6%~~ → **實測只剩 1.33%，結案不做**（§EN-405） | 見下 | **已結案** |
| L2 K5 若成立 | ~~+30~38%~~ → **歸零**（§EN-406） | **已證偽** |
| L3 重疊做滿 | **18.59 t/s** @ml3.375（今天 13.61，`gpu_sum 181.56 ms`） | 已量測 |
| L4 理論頻寬 | ~75 t/s | 不可及 |

**9-ADD 從 11.5% 下修到 2.34%**：① 單價錯；② **ADD 融合早就開了**
（census 窗口 ADD **ON 26/68 vs OFF 86/86** ⇒ 已融 **61.8%**）⇒ 只能再省 120。
⚠ **census 的 per-op 只能用比值不能絕對外推**（ADD 窗口 68 vs 全步 421 比例 6.19，
而 `nodes_all` 是 4.176 ⇒ 覆蓋率不均）。
**★ L2 與 L3 打的是不同東西（L3 消 cb 不動 GPU busy；L2 壓 `gpu_sum` 本身）⇒ 可疊**
⇒ 量級 `13.61×1.366×1.30 ≈ 24.2`。⚠ **跨 cell 相乘不可引用**，且若 K5=(B)/(C) 則 L2=0。

### ★★★ L1 復審結案（§EN-405，0 重建）→ `docs/L1_FUSION_REAUDIT_2026-09-21.md`

**L1 實測只剩 1.33% < 3% 門檻 ⇒ 不做。**（9-ADD cap7→9 **0.76%** ＋ MUL 納入 **0.57%** ＋ DIV **0**）
- **「9-ADD 鏈」真的存在**：圖 dump ADD = 351 孤立 + **201 條長 9**（平均 3.91 是**被孤立點拉低的假象**）。
  現狀熔 7 ⇒ 9-鏈變 2 dispatch；提到 9 ⇒ 1 ⇒ 省 39.2 條/步。
- **bin 融合只認 ADD**：`ggml-metal-ops.cpp:5770-5778` 把 `fops[0..7]` **寫死 `GGML_OP_ADD`**
  ⇒ MUL／SUB／DIV 從不進 `can_fuse`（實測 ratio 恆 1.000）。
  **但 kernel 早支援 mul/div**（`ggml-metal.metal:1279` `FC_OP` 0=add 1=sub 2=mul 3=div）
  ⇒ 讓 MUL 鏈熔**只改 host 端 fops 填充，不用動 shader**。⚠ `FC_OP` 單一值 ⇒ 只支援同類型連續鏈。
  兩道上限：`n_fuse<=6`（硬編碼）＋ `n_fuse < cgc_fuse_max()-1`（env 預設 8）⇒ 實熔 **7**，要熔 9 得改兩處。
- **DIV 的 201 個全孤立** ⇒ **K3 的 cluster-1 2.28% 可及值 ≈ 0**（只能靠垂直融合＝G4 已判別寫的東西）。
- ⚠ **「計數指標＝確定」是錯的**：兩次 `MAX=8` ratio 0.351 vs 0.373（census 只覆蓋一步 13.6%）；
  反而 **t/s 更穩**（9.917 vs 9.803）。⇒ 計數只穩在量級，別拿絕對數外推。
- **★ 戰略：L1 已死，K5 是唯一主線。**不成鏈、融合永遠救不了的孤立小 op ≈ **324 個/步**
  （孤立 MUL ~217／ADD ~68／DIV ~39），對比 L1 能救的 68 個/步 ⇒ 它們正是 45.6 µs 常數的載體。
  **融合＝把鏈壓短（已壓完）；K5＝把每個節點固定成本壓低（一點沒動）。**

### L3 分工（本線別碰）

`cb` **不是 command buffer，是 expert cache 填池 IO** ⇒ 屬**線 I**。
線 I 已裁 neighbour prefetch 對 decode「有效但太貴」；F2 null；**F2-R 未跑**
⇒ **「把 cb 藏到 GPU 底下」尚未被證明可行**，而 18.59 t/s 完全建立在它之上。
本線在 L3 只剩 `submit` 10.09 ms 與 `wait` 159.77 ms，而 **S2 已結案**。

### G4／S2 已結案

S2 建議不做完（天花板 18.7，可及 ×1.087）；**G4 別寫融合 kernel**（一個 dispatch 實測 0.0736%；
最大單一集群只 72 個 ⇒ 上界 5.3%；9-ADD 融合上界 11.5%）。
→ `docs/G4_FUSION_ANSWER_2026-09-20.md`


## ★★★ L3 更正（2026-09-21 §EN-408）：可遮窗口實測為 0，`18.59 t/s` 不是可達值

（權威在 `docs/L3_WINDOW_ZERO_2026-09-21.md`；本節只放索引，上面的「L3 定錨」節的 18.59 要照這裡讀）

- **MoE core 永遠在 segment 的第一個 command buffer**（5811/5811，MoE 前 duration 佔比 med/p90 = 0.000，
  分層後仍 0）＋ 派送器 `hook_seg(i)` → fill 阻塞 → `submit_seg(i+1)` ⇒ **async fill 沒有視窗**。
- **交叉驗證**：`gap 54.35 ms`（GPU 時鐘空檔）> `cb 42.04 ms`（CPU fill）⇒ 遮蔽量零。
- **M1 交接單的 A/B 段拆分上界 = 0**（A 段依賴 B 段輸出）⇒ 別寫。
- **唯一活路＝不需 ids 的背景預取**（file-neighbour R=1 在 GPU busy 陰影下）：IO 146.9 vs GPU busy 159.77 ms
  ⇒ 餘量 8%；R=2 出局 ⇒ **上界 +7%（13.5 t/s）**，跨 run 只當量級。


## ★★★ SSD 吞吐實測（2026-09-21 §EN-409）：讀取形狀差 4.4× ⇒ neighbour prefetch 判決翻案

（權威在 `docs/L3_PREFETCH_REPRICED_2026-09-21.md`）

- 冷讀（F_NOCACHE）GB/s：rand-1MiB 1.19/1.80/1.68/1.44（1/2/4/8 緒）；**rand-3MiB 1.56/4.78/3.87/4.09**；
  seq-1MiB 5.39、seq-8MiB 5.50 ⇒ **隨機 vs 順序差 4.4×**（與 `CB_IS_THE_DEVICE_RESULT` 的 shape-flat 衝突）。
- 引擎今天有效吞吐 1.19–1.73 GB/s（3 支 run 反推）＝「隨機 1MiB、8 緒」的量級 ⇒ 已貼著低效曲線。
- ⇒ **R=1 時間比 = 1.98 × (1.44/4.09) = 0.70** ⇒ IO 總時間 −30%，非 +98% ⇒ `NEIGHBOUR_PREFETCH_VERDICT`
  §3.3「淨虧」**翻案**；L3 上界回到 **+7%（13.5 t/s）**，IO 只佔 GPU 陰影 33%。
- 併發：隨機讀 **2 緒最優**，8 緒 −20%，而引擎預設 **8** ⇒ `LLAMA_EXPERT_CACHE_WORKERS=2` 的 A/B：
  µs/miss 兩輪同向 0.871/0.917、fill_batch −36%、t/s 方向一致但幅度不可信（±27% 噪音）。


## ★★★ L3 撤回（2026-09-21 §EN-410）：R=1 淨虧，L3 天花板回到 0

（權威在 `docs/L3_PREFETCH_RETRACTED_2026-09-21.md`；本節取代上面 §EN-409 的「翻盤」）

- **每 expert = 1.0703 MiB，分在 3 支獨立 GGUF 張量**（gate/up 0.3203、down 0.4297 MiB）。
  GGUF `dims` 逆序 ⇒ `dims[-1]=256` 最外層 ⇒ **e±1 在支內連續**；但支與支相隔上億位元組。
  ⇒ R=1 = **3 次各 ×3 大的讀**（0.96/0.96/1.29 MiB），**沒有 3 MiB 讀**。引擎現在每次讀 **~220 KiB**
  （`reads/miss=4.96`，prewarm miss=0）。
- **★ `F_NOCACHE` 探針在本機不可信**：同一 3 MiB/2 緒，`pread` 2.36 vs `preadv` 5.46 GB/s（背靠背交替），
  同 `preadv` 在另一支腳本 1.93 ⇒ 2.3× 儀器差 ⇒ **12.72 GiB vs 16 GB RAM，頁快取污染**。
  ⇒ **所有絕對 GB/s 作廢（含本檔 §EN-409 的 1.19/1.80/4.09/4.78）**；只可做同儀器內相對比較。
- 同儀器：0.32→1.29 MiB 吞吐持平 ⇒ **R=1 時間比 1.87/2.24/2.62/3.05（1/2/4/8 緒）** ⇒ 步級
  IO 309–429 ms vs GPU 陰影 159.77 ms ⇒ **裝不進去**。`NEIGHBOUR_PREFETCH_VERDICT`「淨虧」**恢復有效**。
- **L3 = 0**。`18.59` 與 `13.5 t/s` 都不是可達值。
- **唯一存活**：`LLAMA_EXPERT_CACHE_WORKERS=8 → 2`（µs/miss 三輪同向 0.871/0.917/0.897；
  另見 miss 數 −21.5%）。不依賴探針絕對值。

## GPU 有效頻寬／屋頂線（§EN-420，2026-09-22）

- **★ 本機實測峰值（統一記憶體，多執行緒順序讀 `/tmp/membw.c`）＝ 108.8 GB/s（6 緒）**；
  1/2/4/8 緒 = 67.8/72.3/79.7/103.4。512 MiB 與 1 GiB 一致。**120 GB/s 只是規格值，別當實測。**
- **有效頻寬（1.277 GB/step，GGUF 實測）**：union **11.9–15.1 GB/s**、union+gap **7.9–10.7**、
  busy 7.8–9.7 ⇒ **只佔實測峰值 7–14%** ⇒ **今天不是頻寬受限**，瓶頸在 tile/occupancy、dequant、
  段間空檔、dispatch 延遲 ⇒ **kernel／autotuner 的上界不受 roofline 綁**（比舊估計寬鬆）。
- **⚠ 作廢**：`docs/MMAP_CACHE_KERNEL_25` 的「25 → 33.5 GB/s、峰值 34%」＝每步 bytes ÷ 每 token
  時間混用 ＋ 沒除 1e9 ⇒ **絕對量不可引用**。**`×1.345` 比值仍成立**。25/12.6 = **1.98×**（與 ml 無關）。
- **★ 兩支儀器對上帳了**：`DECPROF total` ≈ `GPUTIME union+gap`（差 <1%）⇒ 兩者都是
  **一步的 GPU 時鐘跨度**，不是牆鐘步時。⚠ 別拿 `busy(sum)` 或 `DECPROF total` 當步時。
- **未定（下一步）**：① 開 `CGC_DECODE_PROFILE` 後 avg_ts 12.60 → 8.36（−34%），開銷還是別人搶 GPU
  分不清；② **ml 3.375 還是 ~1.7 沒釘** ⇒ 步時 268 還是 133 ms 差 2×。兩條閉合前別寫 kernel。
- 帳上矛盾（最該問的）：s2b0 `gap 39.42 ms` 含 **cb 23.93（填池 IO）+ submit 5.38** ⇒ 理論 +19%，
  但 L3 實測可遮窗口 = 0 ⇒ **帳上有錢、結構上沒地方放**。

## MEMORY.md 瘦身移入（2026-09-22 16:5x；本節＝當天第二次瘦身，dated 判決全文）

### K5（§EN-414 更正：「要重建」是錯的，第 2 格判不做）→ `docs/M_K5_SHAPE_2026-09-21.md`
- ① **decode 形狀已 0 重建到手**：用 **`CGC_ELEMW_CENSUS=1`**（`ggml-metal-ops.cpp:582`，配額 3000 行、
  印 `ne[0..3]` + name），不是 `CGC_GRPH_DBG`（6 圖、只 2 維、5/6 是 prefill）。實測 102 個
  elementwise dispatch/步，**≤1024 元素佔 42.2%**，典型 `ne=[1,4,1,1]`＝4 元素 ⇒ **(A) 必要條件成立**。
  ⚠ 它的 `want` 不含 ADD。
- ② **但一個 dispatch ≈ 18–19.5 µs 且幾乎全是固定開銷**（兩獨立儀器互證：NSM `nk==1` buffer 的
  `ffn_moe_argsort` 中位 18.6 µs vs K3 邊際 0.0195%×100 ms = 19.5 µs）⇒ 撐 grid 動不了它
  ⇒ 「小 op 群 36.5% 全拿回＝30–38%」是高估 ⇒ **M-K5 不做**（(A)/(B) 同形判不出）。
- **別再試**：per-kind 最小二乘（共線性病態）／M 掃描（與 (A)/(B) 同形）／用 `wcntw` 做 per-op 歸因。
- ⚠ 開 NSM 要四個 env 一起：`CGC_GPU_NODES=1` `CGC_GPU_OPS=1` `CGC_GPU_TIMING=1`
  `CGC_GPU_NODES_MATRIX=1`（只設 MATRIX ⇒ 0 行）。⚠ 未解：ELEMW 與 NSM 同 run 時 NSM 0 行
  ⇒ 「元素數×時長」同 run 回歸做不了 ⇒ **別引用 36.5% 的拆分**。
- **別去拆 `n_main` 打包**：佔步時 61.3% 的 64-node buffer 內容是 `ffn_moe_` 25.9% 等真實 MoE 大計算
  ⇒ L4 頻寬問題（已判不可及），不是可省開銷。

### M-W（WORKERS 8→2）交付 cell 判決 → `docs/M_W_DELIVERY_VERDICT_2026-09-21.md`
10 臂／5 配對、全部 NOMINAL、ABBA、臂間冷卻 150 s：µs/miss 中位 0.902（−9.8%）、5/5 同向 p=0.031；
t/s 中位 +6.0% 但只有 3/5 同向 ⇒ 幅度不可引用。pool_wait 只佔步時 ~16.7% ⇒ 端到端 ≈ +1.7%（低於門檻）。
⚠ 設法：只有 `CGC_SERVER_WORKERS` 有效（`run_server.sh:1353`）；直接設 `LLAMA_EXPERT_CACHE_WORKERS`
走 matrix 會被靜默覆蓋回 8。⚠ 「miss 數 −21.5%」已撤回（補輪後比值 0.986）。

### p2 分支與 0907 knowhow（09-22）
- p2 rebased 分支**只有 kernel**（demo 分支 + 1 提交 1f703c4d4，淨 8 行 sum_parts）；harness／raw／
  MTP 重建／13:41 誠實版都不在上面。⚠ HEAD 已被切到該分支且 dylib 14:01 重建 ⇒ 現在量測都是新 kernel。
- `CGC_DUMP_ENV=1` 證明兩臂有效配置逐位元組相同，差異只剩 p2 binary 少了 `-DMTP_SUPPORT`。
- 31.5 ≈ 我們 `union+gap` 地板 × ml=4（主機側歸零），與已判不可達的 18.59 同一件事。
- ⚠ 別再說「dylib 不含那 24 行」（已撤回：`.metal` 與 dylib 同為 02:43）。

### FP 順序方法論（§EN-423／§EN-427）
- **fast-math 證據**：`CMakeCache.txt GGML_METAL_EMBED_LIBRARY=ON` ⇒ `.metal` `incbin` 後 runtime 才編譯
  （`ggml-metal-device.m:229-234`），`fastMathEnabled` 預設 YES，`:232` 的
  `//[options setFastMathEnabled:false];` 被註解；`GGML_METAL_SHADER_DEBUG` OFF。
- **完整配方＝三件事**：多累加器 ILP ＋ 固定合併樹 ＋ 關 fast-math。p2 只做 2→8 累加器 ⇒ 數值 no-op（M1 仍 9/9）。
- **decode 三目標全落空**：① 支配區塊 GatedDeltaNet 35–45% 是遞歸（`gated_delta_net_impl:2649` 已
  `simd_sum`）⇒ 只能 chunked scan（演算法級、M1 歸零）；② `ssm_conv_f32_f32:2210` 完美目標但被
  18 µs dispatch 吃掉；③ `moe_gemv` 只佔 3.2/6.3%，FP 鏈被 threadgroup 間接查表掩蓋（地址依賴資料）。
  ⚠ **載體全檔 `IQ3_XXS = 0`** ⇒ p2 那 24 行 kernel 不可達 ⇒ 目標③理由改「目標不存在」。
- 12.93：跨儀器／2.86%<3%／落在基線離散內 ⇒ 不可引用；落地值 12.57×1.054=13.25。
  ⚠ 別在 t/s 上測（3% 效應需 ~50 臂/側）。

### 兩條更大的魚（§EN-428）→ `docs/BIGGER_FISH_2026-09-22.md`
- **`node` 20–25% 不可引用**：來自 `wcntw`（`ggml-backend.cpp:2306`，buffer 的 `dur` 平均分給節點；
  一步 bufs=633/nodes=3979 ⇒ 小 buffer 節點獨吞整段時間；實證 `attn_norm` 報 36.76 ms 而它只是
  40 個 4096 元素乘法）。細粒度一律讀 op 級 `CGC-GPUOPS`：**dense `MUL_MAT` 426 個 32.7%** ＞
  `MUL` 16.6% ＞ **`MUL_MAT_ID` 117 個 10.0%（MoE 專家 GEMV）** ＞ `CPY` 210 8.1%
  ⇒ **op 口徑第一名是 dense，不是 MoE**。
- **120 個 CPY 來源＝ MTP**：`--spec-draft-n-max 3` ⇒ `common.h:395 need_n_rs_seq()`=3 ⇒
  `delta-net-base.cpp:503`（n_rs_seq==0 只建 1 個 CPY，否則 K=4）⇒ 30 層×4=120。但 MTP on 是 +28.5%
  ⇒ 別動。折疊也❌：K 個 CPY 寫進 K 個不同 slot（`s_slot=K-t`）＝K 份不同回滾快照，非數值等價。
- **★ 真正最大的是非 MoE**：單 token 每層 29.34 MiB vs 專家 1.083 MiB/個 ⇒ 非 MoE 佔每步位元組 70%
  （每步 ~1.277 GB 裡 ~0.89 GB 是 dense 權重重複讀）。有效頻寬只佔峰值 7–14%
  ⇒ 下一步用 `CGC_GPUOPS` 量那 426 個 dense `MUL_MAT` 判「啟動開銷受限 vs 頻寬受限」。
  ⚠ **K5 的 18 µs 是 5-node 小 op 群量的，dense GEMV 沒被判過。**
- `dense_bytes_census.py` 交叉驗證：模型 13.66 GB；trunk dense 827.0 MiB、experts/step 342.5、
  **lm_head 397.9（＝全部 dense 的 32.5%）**；與 roofline 342.9/827.0 對上。


## MEMORY.md 瘦身移入（第二輪，2026-09-22 夜；原文出自 `MEMORY.md` 的「09-22 的判決」表）

## 09-22 的判決（全文已移入 `MEMORY_PERF.md` 末節，這裡只留指標）

| 主題 | 一句話 | 權威 |
|---|---|---|
| p2 分支 31.5／25.4 | 無 raw、本人兩次撤回；誠實值 195tok 25.41／694tok 12.63 ≈ 我們 12.57 | `docs/P2_BITIDENT_REVIEW_2026-09-22.md`、`docs/P2_REBASED_BRANCH_CHECK_2026-09-22.md` |
| 12.57 的 bit-identity | **M1 = 9/9 已實測**（13:51 gate）⇒ 別再說「從未驗證」；但 ref 全 ≥09-13 都含 P2-B ⇒「P2-B 有無漂移」仍無解 | `Backup/m123_oracle_gate/summary_p2_rebased.json` |
| 0907 knowhow | 已在樹（P2-B 是 12.57 基線祖先、逐位元組相同）⇒ 可遷移增量 = 0；25.17 不可引用 | `docs/BITIDENTITY_AND_0907_KNOWHOW_2026-09-22.md` |
| fast-math | `GGML_METAL_EMBED_LIBRARY=ON` ⇒ runtime 編譯、`fastMathEnabled` 預設 YES，`:232` 關閉那行被註解 ⇒ **compiler 有權重排 FP** | `docs/FP_ORDER_SHAPE_2026-09-22.md` |
| 拆累加＋固定合併 | 處方缺一半（還要關 fast-math）；decode 三目標全落空 ⇒ 淨空間 0，是「通行證」不是加速器 | `docs/FP_ORDER_TARGETS_2026-09-22.md` |
| 12.93 | 跨儀器／+2.86%<3%／落在基線離散 11.83–13.46 內 ⇒ 不可引用 | 同上 §5 |
| **★§EN-434 dense GEMV 結案（接上第 59 行的通道）** | 通道開了、也掃了、結果是否定的：dense+lm_head **13.39 ms/步（13.7–16.8%）**，已跑 56–108 GB/s；**即使全部打滿 100% DRAM 峰值也只 2.42% < 3% 門檻**；**`CGC_MMV_NSG` 1..32 全射程否證**（±3% 噪音內、無單調趨勢，最好一格只值 0.05 ms/步）⇒ **dense GEMV 家族結案，位元組佔比 ≠ 可回收** | `docs/DENSE_GEMV_INSTRUMENT_2026-09-22.md` |
| **★★ op 級計費口徑（§EN-434，今後照抄）** | `sync`（單 dispatch＋等完成）＝**延遲**，同形狀兩輪差 2.6× ⇒ 棄；`batch(b)`＝含**每張圖固定開銷 220–660 µs** 的攤額 ⇒ 小形狀假性惡化（`ffn_gate_shexp` 看似 13.7 GB/s，實為 **57.3 GB/s**）；**權威口徑＝`marginal=(g(64)−g(32))/32`**；再用 **closure test**（priced 總合 vs step 79.5–97.9 ms）給口徑判死刑。⚠ batch 必須 `--distinct` 權重（共用＝互相餵快取）｜⚠ 張量沒落到 device buffer 時程式 **exit code 0 靜默死**；⚠ 小張量可能 SLC 命中（router 2 MiB 跑到 108.5 GB/s）⇒ 不可與 DRAM 峰值互比 | 同上 §1–§2、§7 |
| 兩條更大的魚 | `node` 24.1% 來自 `wcntw` 壞口徑不可引用；CPY 120 個源自 MTP（關了淨虧）；非 MoE dense 原記 70%，**§EN-433 更正為 81.7%**（漏算 lm_head 397.9 MiB/步）——但 §EN-434 已測出它的**可回收上界只有 2.4%** ⇒ **別再往 dense 找錢** | `docs/BIGGER_FISH_2026-09-22.md` |
| shape_roofline | 自適應清單那半可用（模型＋硬體），**無 codegen**；真旋鈕＝ **`CGC_MMV_NSG`**（非 `CGC_MM_NSG`） | `docs/SHAPE_ROOFLINE_REVIEW_2026-09-22.md` |
| **IO 面能否收 25 的約束（§EN-436）** | **不能**：七條裡只有 3 條歸 expert-cache Metal I/O 面，而那 3 條今天已實作；union 實測**線性 8·M**（推翻次線性）、per-slot=**1.391 MiB**、§EN-358 第一槓桿已撤下 ⇒ 屋頂下修 **M=2–3 ≈50 t/s**、markup 需 3.96×→**1.99×** | `docs/IOCACHE_CONSTRAINT_ADMISSION_2026-09-22.md` |
| **★ autotuner 配方（§EN-431/432）** | 三塊都在（inventory／`test-backend-ops perf`／`CGC_MMV_NSG`+`NR0`+`FUSE`），**缺的是接線層** ⇒ `scripts/check/autotune_mmv.py`（已實掃）。**儀器噪音底實測 2.4%**（unset 與 NSG=2 同值卻差 2.42%，對照 llama-bench ±27%）；NSG 全射程只 ~4%、端到端 ≈0.22% ⇒ **no evidence 不採用** | `docs/AUTOTUNER_WIRING_2026-09-22.md` |
| **★★ 兩個形狀函數已落地（§EN-437）** | `src/llama.cpp/src/llama-shape-knob.{h,cpp}`：INPUT `cgc_shape_knobs()`（env 單一來源＋跨軸驗證＋每軸狀態）、OUTPUT `cgc_shape_report_{init,final}` 一行 `CGC-SHAPE v=1` 含實收幾何＋**品質哨兵** `zero_mapped/verify_refused/inv_viol`（非零⇒該 row 不算量測）。harness `scripts/check/shape_knob_search.py`（`--plan/--run/--selftest` 14/14，cell 夾在兩 ref 間、drift>10% 判 UNANCHORED）。實測：**M 上限 17 確認**（routable=**142** 非 143）；**capacity miss 只佔 miss 6~7%** ⇒ 143 slot 非容量飢餓，**處方反了，該試縮池（4 GiB⇒~71 slot）把 RAM 還給 dense**；三輪 ref 序列 11.72→10.83→**5.22** ⇒ **沒給任何速度結論**、今晚沒往 25 移動。⚠ `expert_cache_pool_capacity` 是 **slot 數不是 bytes**。⚠ #7 GDN 仍 **ABSENT**（只有 enum）⇒ 無法靠 env 生成 | `docs/SHAPE_KNOB_LANDED_2026-09-22.md` |
| **★★ 最佳 shape（§EN-433）** | trunk dense 852 MiB 只 5 個形狀佔 90.5%，**IQ4_XS 四兄弟＝81.1%**，**0 knob**（① `CGC_MM_BITIDENT=1` 使 ext family 全繞過 ② IQ4_XS 不在 ext 型別清單 ③ `NSG`/`NR0` 的 `default: return -1` 只到 IQ2_S/IQ3_XXS）⇒ tile 全是編譯期宏 `N_SG_IQ4_XS 2`／`N_R0_IQ4_XS 2`。**但 nsg 已是 function constant**（pipeline 名 `kernel_mul_mv_iq4_xs_f32_nsg=2_ne12=1_r2=1_r3=1`、`device.cpp:1013`）⇒ **該通道已開（P1-3d/3e 已 build，見 §EN-441）⇒ 本列「0 knob」已過期**。⚠ 上界不可引用：`CGC-GPUOPS` 全開儀器（step 138.99–210.18 ms），`test-backend-ops` 的 MUL_MAT 內建 case 是 4096×14336、**不含本模型任何支配形狀** ⇒ dense achieved-BW 無可用儀器 | `docs/BEST_SHAPE_IQ3XXS_M4_2026-09-22.md` |
| **★★★ Knob 清單核對＋撤回今晚 budget sweep（§EN-441）** | ① **`LLAMA_ARG_EXPERT_CACHE` 不是程式讀的名字** ⇒ 今晚 4 個 budget arm **全是同一配置**（10 arm＋6 ref 全印 `n_slots=143`）⇒ **§EN-440「6 GiB +3.40%」撤回**；真鏈`CGC_SERVER_EXPERT_CACHE_BYTES`→`BUDGET`→`CGC_EXPERT_CACHE_BYTES`，grid 已修（ladder 3/4/6，8 GiB 不進 ladder＝它就是 ref）。副產品＝最乾淨的噪音標定：恆等 10 arm mean 9.976/sd 1.119/**CV 11.2%**、log σ≈0.28。② **`run_server.sh` 的 SERVER_ENV 才是唯一入口**（knob 從命令列進⇒不在 os.environ）⇒ 新儀器`check_reachability()`（resolved env 對 ref cell 做 diff，空 diff **rc=2 拒絕**），`--selftest` **27/27**。③ 18 個「值得掃」→**實測只有 7 個真會動**（形狀 3／kernel 2／prefetch 2／**MTP 0**）；8 處錯（`CGC_M`不存在＝`CGC_POOL_MAX_TOKENS`、`CGC_SYNCFILL_COLD`預設 ON、`CGC_IDS_*`/`CGC_POOL_CAPTURE`/`CGC_P_ROUTE`/`CGC_SERVER_MTP` 都不是性能旋鈕）。④ **唯一活著的兩條：`CGC_MMV_FUSE=1`**（prod 的GLU_FUSED_DOWN 今天惰性、§8.113 的 +6.5% 從未生效）**與 `CGC_MMV_NSG` 重掃**（IQ4_XS/Q6_K/Q8_0/IQ3_S 已接上且已 build）。⑤ 一次 launch **94 s**：認證 3%≈35 h、5%≈12.7 h、**10%≈3.2 h** ⇒ 一夜＝一條軸一個 ≥10% 效應，**槓桿是降 σ（時間∝σ²）** | `docs/KNOB_INVENTORY_VERIFIED_2026-09-22.md` |


- **硬體實測 Mac16,12 / M4 / 8 GPU / 16GB**（不是 M4 Max）；**實測峰值頻寬 108.8 GB/s**（6 緒），
  120 是規格值別當實測；有效頻寬只佔峰值 7–14% ⇒ **今天不是頻寬受限**。

| **GDN 軸結案（§EN-438，取代上面「#7 ABSENT」那一行）** | 「沒有 operator」是錯的：`ggml_gated_delta_net` 已在 Metal（`K`＝快照數是 function constant，T=1/T>1 同一 kernel），knob 也真的會翻（`fused_ar=0 ablated=1`）。**但 `build_delta_net` 在這個 workload 一次都沒進去**（builder 自報的 `gdn_saw_fused/manual` 皆 0）⇒ dump 逐位元相同是「沒東西可比」不是「兩條實作一致」⇒ **GDN 目前在量 0，軸可收掉**。⚠ MTP 下 `n_seq_tokens>1` 走 chunking，ablation 要 AR/CH 都關 | `docs/GDN_AXIS_RESOLVED_2026-09-22.md`、skill 第 19 條 |
| **順序／漂移校正（§EN-439）** | 讀比值要用**幾何**且在**被量時刻**插值（吞吐是被乘的，`log` 才變加法）。`scripts/check/crossover_estimate.py`（selftest 14/14，`--selftest --broken` 舊估計器必須紅、最差 8.03%）已接入 `shape_knob_search.py`（`--combine`，每列多印 `ref_ts_alt`/`mean_gap_pct`，selftest 24/24）。舊 `M17` 列均值選擇就移動 7.9 個百分點，**但那列本來就 UNANCHORED，校正沒把它變回資料** | `docs/ORDER_DRIFT_CORRECTION_2026-09-22.md`、skill 第 20 條 |

| **閉環 autotuner（§EN-440，回答「還差 20%」）** | 「00 閉環／1 就會學習／真正的 autotuner」落成三條**各自帶突變**的聲明：`scripts/check/closed_loop_autotune.py`（posterior over log(cell/ref)、Top-Two Thompson、共享 reference 的 block、σ 線上估計、selftest 17/17）。**最有用的產出是作業邊界表**：16 launch 下 σ=0.16 需 **~68%** 真實差距才認證得到、σ=0.06 需 **~52%**（兩臂解析式 23.5%／8.4% 過於樂觀，改用仿真標定 `calibrate_boundary()`）⇒ **預期只差 3–5% 的 knob 別 sweep**。⚠ **regret 不是它贏的地方**（偶爾輸給均勻分配）｜⚠ 觀測假發現 6% > 標稱 5%（同 block 共用 ref ⇒ 誤差相關）｜⚠ 高噪聲下報價偏高 1–2 pt（Jensen）⇒ **決策用 P(best)，不用報價** | `docs/CLOSED_LOOP_AUTOTUNE_2026-09-22.md`、skill 第 21 條 |

## MEMORY.md 瘦身移入（第三輪，2026-09-23）

從 `MEMORY.md` 移出來的 dated 判決全文（本檔是權威，MEMORY.md 只留導航）：

### 09-22 判決（原 MEMORY.md「09-22 的判決」節）
- `docs/BEST_SHAPE_IQ3XXS_M4_2026-09-22.md` —— 「IQ4_XS 0 knob」已廢除，但
  **`CGC_MMV_NSG` 在 IQ4_XS 上早已 1..32 掃過並否證**（§EN-442 更正）⇒ 別當新機會。
- `docs/KNOB_INVENTORY_VERIFIED_2026-09-22.md` —— 「唯一活著的兩條」剩 **FUSE 一條**（NSG 那條撤回）。

### 聯立 shape × kernel（原 §EN-442；權威 `docs/JOINT_SHAPE_KERNEL_AXIS_2026-09-22.md`）
- dense（含 IQ4_XS）判死且自我否證：**打滿 DRAM 峰值也只有 2.42%**，tile/unroll/vector width 救不了。
- joint 儀器 `shape_probe/mmid_shapes.py --tokens 1,2,3,4` ⊗ `--nsg-sweep`；**五次 sweep 五次都沒跑在乾淨盒子上**。
- 唯一還有空間的是 MoE `MUL_MAT_ID`（%峰值 20–36%），中位數站在 44% 寬的 cell 上，**不可引用**。
- §EN-444／§EN-445 的完整數字（footprint 半步、真兇＝別條線的 llama-server、NEED_MB 維持）
  見 `docs/FOOTPRINT_HALFSTEP_2026-09-23.md` 與當日記憶 `2026-09-23.md`。

### 15 個 knobs 地圖（原 §EN-443；權威 `docs/KNOB_FULL_MAP_15_2026-09-22.md`）
- 15 條裡：4 條已判／被自然實驗否定、2 條是既有 knobs 的同一自由度（`n_batch`/`n_ubatch`
  **被 `cgc_pool_max_tokens()`(=8) 夾死，不是 free knob**）、2 條不存在 ⇒ 真正新的只有 NSG×MoE 與 WIN_PIN。
- 置換策略已被一次自然實驗否定（4 vs 8 GiB：miss 1.91×、capacity miss 3.15×、cb 41.8→90.0 ms，
  **t/s 10.60 vs 10.28 不動**；⚠ 跨 run，故是降優先不是結案）。
- **核心論點：問題不是 knob 數量，是 objective 價格** —— surrogate ~3 min／噪音 2.4%，
  端到端 94 s/launch／CV 11.2% ⇒ 認證 3% ≈ 35 h。

## MEMORY.md 瘦身移入（第四輪，2026-09-24；原文出自 `MEMORY.md` 的 09-23 dated 判決塊）

### ρ 路線（原 §EN-4xx；權威 `docs/RHO_STAGE0_RESULT_2026-09-23.md`）
用 **pre-attn 殘差**算 gate(L)（同一 token、只差一個 submodule）⇒ **cov_uni = 0.854**
（ntok=4，2040 層，skip=0）vs 門檻 0.398 ⇒ **PASS（step −10%），且過 0.70 飽和點**
⇒ 瓶頸從「準不準」變成「窗口多大」。上界 **+14%~+31%（12.57 → 14.4~16.5 t/s）**，
區間寬是因為 `cb` 的 42/74 口徑未閉合。過度抓取僅 **1.03×**。
- **2026-09-23 16:45 探針位置已修**（§EN-468，`docs/RHO_STAGE0_RESULT_2026-09-23.md` §8）：
  影子 router 原本建在 attn **之後** ⇒ ggml 執行序 = 建圖序 ⇒ **提前量 ≈ 0**。
  已移到 `inpSA = inpL` 正下方（layer L 第一個算子），數學一字未改。
  ⇒ **`cov_uni=0.854` 仍有效**（準度與 node 位置無關），但 09-23 14:58 那輪**沒有任何 lead 讀數**。
  舊位置保留作對照臂 `CGC_RHO_PROBE_LATE=1` ⇒ 正確性判據是「`ρ` 逐位元相同」。
- **lead 已實測**（§EN-469，EARLY/LATE 各 3 趟）：影子從 segment 的 **80.4% → 45.1%**，
  lead 區間 **LATE +0.41~1.27 ms → EARLY +1.31~2.27 ms**（悲觀下界 ×3.3）。
  準度不受位置影響（cov_uni EARLY .8600 vs LATE .8406，差 0.019 ≈ 同臂散佈同階；
  ⚠「逐位元相同」做不到——解碼軌跡不可重現 ⇒ token 母體不同）。
  ⇒ **cb=42.04（同 regime）⇒ 14.39 t/s（+14.5%），且視窗不綁 ⇒ 那就是結構上限**
  （`cov·F`=0.904 < 1.31；就算位置有 29% 代價也仍是 14.39）。cb=74.18 ⇒ 15.56~16.50（僅對照）。

### prebind 判活（原 §EN-4xx；⚠ 引用入口 = `docs/PREBIND_VERDICT_SETTLED_2026-09-24.md`）
原「結案 = 不做」已作廢。作廢原因：儀器只存了 **token 0 那一行**，而 MTP decode 相鄰 step 的
token 完全不相交 ⇒ 0.354 是「另一個 token 的路由」（舊的「union 19.41 vs 每次只能預測 8 ⇒
天花板 41%」那個根因**同屬 bug 產物，作廢**）。
修正後（整步 union，ntok=4，U=19.36／p_res0=0.8995／sigma_hat=0.965）：
**qu1=0.589(寬16.9)／qu2=0.690(23.7)／qu3=0.726(28.7)**；門檻（step −10%）四種口徑
0.334／0.590／0.604／0.700 ⇒ **qu2、qu3 全過，qu1 邊緣** ⇒ **prebind 判活**。
⚠ 這批數字原本只存在 `docs/PREBIND_STAGE0_RESULT_2026-09-23.md`，而**那份檔標題是
「⛔ 本文件的主結論已作廢」**（作廢的是 13:31 的 FAIL 判決，不是修正後的數字）
⇒ 要引用就引 `PREBIND_VERDICT_SETTLED_2026-09-24.md`，不要引原檔路徑，
也不要因為看到它的標題就整份跳過。

### prebind 與 ρ 互補（不是替代），以及 cb 定讞後兩個上界各自砍一半
ρ 覆蓋 0.849 但**視窗只有 1.30~1.59 ms**（MoE core 佔 segment 60.1%）⇒ 卡視窗，
且要付 4.76 ms/步 GPU 插入；prebind 覆蓋 0.59~0.73 但**提前一整步發起 ⇒ 視窗 ≈ 一步
（~248 ms）不受限、不插 GPU 節點**，代價是過度抓取 1.22~1.48×。
單獨做：ρ 14.36~16.42 t/s、prebind qu3 14.33~16.06 ⇒ **誰先做取決於兩個未閉合口徑
（`cb` 42 vs 74、視窗 1.30 vs 1.59），不是取決於方向**。
- ⚠ **cb 定讞後上面兩個上界要各自砍一半**：16.42 與 16.06 都只出現在 **cb=74.18** 欄，
  而 74.18 已撤回 ⇒ 同 regime 只剩 **ρ 14.36（+14.2%）／prebind qu3 14.33（+14.0%），
  兩者幾乎同價**。（⚠ prebind 那側還沒用交付 mean 口徑 cb=50.40 重算過 ⇒ 它是空白，不是已知的 +19%。）
- ⚠ **2026-09-23 複核（`docs/STACK_UPPER_BOUND_2026-09-23.md`、`scripts/check/window_joint_ev.py`
  39/39）：「疊加 16.76（+33%）」不是兩機制相加** —— 16.76 = 16.46（只做 ρ）**+ 0.30**，
  而 delta 在所有實測格點只有 **+0.03~+0.30 t/s**；且 16.76 同時取 cb=74.18（**跨 regime 借的**）
  ＋視窗 1.59 ⇒ **同 regime 紀律下答案是 14.50（+15.4%）**，結構上限（cb 全藏）cb=42.04 ⇒ 15.14（+20.4%）。
  ⚠ **下一步順序 cb 先視窗後**：cb=42.04 時每層需求 1.051 ms < 最悲觀視窗 1.30 ⇒ **視窗根本綁不住**。

### `cb` 定讞（§EN-470，2026-09-23 18:0x；權威 `docs/CB_DELIVERY_SETTLED_2026-09-23.md`）
**42 與 74 不是兩個 regime，是同一支 run 的兩個窗口。** 在交付 cell 本體加
`CGC_DECODE_PROFILE=1` 實跑兩趟（A=HEAVY、B=NOMINAL），穩態 ntok=4 的
**cb median = 42.09 / 42.24**（對上歷史 42.04），mean = 50.33 / 51.50；
**74.18 只出現在「含池預熱」（rep1 全部 61.84/70.59）或「高 swap」那一格**，
而交付錨點用 `--warm-skip 64`（時鐘從預熱後才開始）⇒ **74.18 撤回，交付 cb = 42~51 ms**。
⇒ **真實收益 +14.5%（cb=42.04）~+19%（cb=50.40），不是 +24~31%。**
⇒ **視窗到此機械排除**：`lead` 1.31→3.00 換任何值 t/s 不變（`cov·F`=1.084 < 1.31）
⇒ **不用再量視窗百分位**；剩下槓桿＝覆蓋率與 `insert=4.76 ms/步` 的實測。
工具 `scripts/check/cb_delivery_read.py`（16/16）；`window_joint_ev.py` **54/54**
並新增 `CB_DELIVERY=50.40`／`CB_DELIVERY_LO=42.04`。
⚠ 三個坑：prefill 圖也是 step row（`ntok=512, cb=5791`）／`dp_layers` 只有 1(draft) 與
40(verify) 兩個值／**定價用 mean 不是 median**（`joint_reconcile.py` 報 median）。
⇒ **現在唯一還能移動 14.39 的只剩 `cb` 的 42 vs 74。**
新儀器：`CGC_GPU_NODES_START=1` → `CGC-NSCB`（per-CB 絕對 GPU START + range 全部 node 名，
加在 `ggml-backend.cpp`，**共用面**）；parser `scripts/check/rho_lead_parse.py`（16/16）。
⚠ buffer ~5 node 寬（`CGC_N_CB` 可用上限 ≈16，127 會卡死 Metal）⇒ **lead 只能給區間**。
⚠ **做疊加不可取**：增量低於可負擔解析度（k=3 launch 級 13.16%，+0.13 要三位數臂）。
兩者共同的空白：LRU 擾動與多餘 pread 的頻寬爭用**都沒被定價**（`shadow_cost` 只算時間）。

### k=3 的 1.43× 飄移已定位（§EN-446，2026-09-23，`docs/K3_SWING_ANALYSIS_2026-09-23.md`，零 GPU）
- 工具 `scripts/check/k_swing_decompose.py`（selftest 18/18）把「飄」拆兩層：
  **單一 request 噪音** k=2 10.54%／k=3 12.21%（幾乎一樣）；
  **launch 級項** k=2 **0.00%（F=0.62<1）**／k=3 **13.16%（F=4.49, df 4/10）**。
  ⇒ k=3 **專屬**一個「開 process 才被決定」的項；**這就是 ABBA 配對失效的原因**
  （配對只扣共有項 ⇒ 配對 sd 只會等於 √2×單臂 sd，實測 2.28 vs 2.18 吻合）。
- **k2 vs k3 不是乾淨的 A/B**：k=3 的 ntok=4 會跨過 `ggml-metal-ops.cpp:2590` 的
  **Q_K 家族 `ne11>=4` 門檻**（Q2_K/Q3_K/Q6_K 從 plain mul_mv 換成 `mul_mv_ext`），
  且 ext 內 `nxpsg`（`:2608` `ne11<3`）、`r1ptg`（`:2627` case 4）也不同 ⇒ **兩組走不同 PSO**。
  （相位閾值已否證：log 實測 `width=8`，3 與 4 都走 DECODE 圖。）
- **`CGC-SYNCFILL` 數的是請求數不是落盤數**（`llama-context.cpp:6497`；真讀是
  `llama-expert-cache.cpp:35` 的 `pread`）⇒ 「計數相同」**沒有**排除 IO 服務時間差。
  **`pread_usec` 計數器本來就在樹上**（`llama-expert-cache.cpp:432` `CGC-RIG-SNAPSHOT`），
  但 **09-23 全部 log 零命中** ⇒ 最便宜的下一步是把它打開，不是多跑 arm。
- **per-request 切 k 目前做不到**：`server-schema.cpp:200` 在 `:198` 的 `#if 0` 裡面。
- **價格**：k=3 的 arm mean 總散佈 14.93% ⇒ 定到 ±3% 要 **25 臂/組 ≈ 58 分鐘盒時**（k=2 只要 4 臂）。


## MEMORY.md 瘦身移入（第五輪，2026-09-25）

（dated 全文；本檔為權威，索引檔只留指標）

### A｜09-24 單段提交 2.2× → 1.72× 爭議（全文）

**2026-09-24 18:2x（§EN-510，權威 `docs/SHAPE_GAP_TO_TARGETS_2026-09-24.md`）**：
**單段提交（41 段 → 1 段）＋ GPU slot lookup**（`CGC_SEG_BATCH + CGC_SLOT_TABLE_GPU +
CGC_B_SCHEME`）在同環境配對 ABBA 上做到 **2.2×**（18.08/17.72/17.78 vs 8.24/7.81/7.93；
較乾淨那輪 16.66 vs 13.59）⇒ **第一個有資格重開 decode 25 的 shape**。

> ⚠ **修訂 2026-09-24 夜：那個 2.2× 在產物本身裡是 1.72×，而且不可歸因為 shape。**
> `Backup/seg_batch_s1_pairs/abba_212809.json`（8 次 launch、同 build、同 cell、**兩臂均無 `--spec-type`**）：
> A 分段 **11.84** / B 單段 **20.32** ⇒ **1.72×**（2.2× 其分母取自 8.24 那段已失敗的子視窗）。
> 而兩臂 **I/O 結構不同**：A `file_reads` **82,293** / `misses` 4,835 / `read_mib` 2,440；
> B **全為 0**（無 hook ⇒ 無 demand fill ⇒ 填充路徑根本沒跑）⇒ 比值是「**S1 ＋ 填充路徑不執行**」
> 的合併效果。両臂 `k_eff=1`（交付是 `mean_len 3.117`）⇒ **S1 從未在背著 4-token verify 下量過**。
> ⇒ 新硬規則 `scripts/check/io_symmetry.py`（selftest 11/11），已接進 `decode_carrier_ab.py`：
> 兩臂 I/O 結構不一致 ⇒ verdict `shape-confounded`、**exit 2**。產物也沒有任何 engine digest。
> 來源：`docs/PREFILL250_MET_AND_S1_REJUDGE_2026-09-24.md` §2。

### B｜第 2 步 miss mask ／ 第 3 步閘門 OPEN（09-24 全文）

- **第 2 步（miss mask 出口）已完成 2026-09-24 22:0x** → `docs/MISS_MASK_STEP2_2026-09-24.md`：
  `CGC_MISS_MASK=1`（＋診斷 `CGC_MISS_MASK_DBG=1`）在圖裡加 `get_rows(valid_table, ids_flat)`，
  實測**與 host `BATCHDBG` 逐位相同**（38 層／421 元素／0 差異，`miss_mask_check.py` 22/22）；
  成本 Δ = −0.95 ± 2.01 ms/step（**門檻 0.2 量不出來**，`segs=41` 不變 ⇒ 無新增 CB／無新增同步）。
  ★★ **第 3 步閘門已判定 OPEN**（22:4x，權威 `docs/MISS_GATE_STEP3_2026-09-24.md`）。
  ⚠ 原閘門（量「單段＋背景 fill」臂 miss 率）**不可執行＝循環依賴**：那臂不存在，fill 全住在
  hook 裡，單段提交把 hook 拿掉就沒 fill。改判**損益平衡點 66.1%~114.3% miss 率**，而實測區間
  **4.7%（同步 fill 穩態）~43.0%（零 fill 天花板，256 步 FLAT，非 62%）全在平衡點以下**，最壞
  仍有 1.54× 邊際 ⇒ **per-expert 重算在 0~66% 任一 miss 率下都淨正，可開工，不必等 fill 後台化**
  （落點 14.1~19.9 t/s）。⚠ 但 **fill 是正確性前提不是速度前提**：無 fill ⇒ 權重永不出現 ⇒
  永遠 garbage；**背景 fill 決定拿 14.1 還是 19.9，不決定正負**。
  ⚠ **填池吞吐量不出來**：`fill_wait_us` 不含 `ensure_batch→fill_segments_pool` 那條（只算
  prefetch 等待＋單 expert 填）；`pread_usec` 跨 worker 累加可超 wall。要就新加計時器。

## ★★ 2026-09-25 複核：「MTP 攤薄倍數幾乎完全由 a 決定」⇒ **不認同**（實測推翻）

> 單頁權威：`docs/MTP_AMORTIZATION_RECHECK_2026-09-25.{html,md}`。
> 被推翻的 `docs/mtp_amortization.html` 已 `git mv` 到
> `docs/archive/mtp_amortization_SUPERSEDED_2026-09-25.html`（它主張 a 決定一切／S=1.19／13.7 t/s）。

### 實測（prod-new／`-p 0 -n 128 -d 512`／ABBA 3 輪／全程 NOMINAL）
| 輪 | S（輪內） | E | k_eff | a | m |
|---|---|---|---|---|---|
| r1 | **0.914** | 1.707 | 1.0 | 0.707 | **0.868** |
| r2 | 0.682 | 1.455 | 1.0 | 0.455 | 1.134 |
| r3 | 1.050 | 1.620 | 1.0 | 0.620 | 0.543 |
| 中位 | **0.914** | 1.620 | **1.0** | **0.620** | **0.868** |

- 自洽：**每輪 `S = E/cost` 精確成立**。⚠ 絕對 t/s（7–8.5）**不可引用**（無 `--warm-skip 64`）。
- ★★ **`k_eff = 1`，不是 3**（`SPECDBG draft=1` 逐輪皆是，儘管 `--spec-draft-n-max 3`）
  ⇒ 用 k=3 算 `1+a·k` 是雙重錯；正確 `a = E−1`。
- ⛔ **「m≈0.87 是穩定讀數」已作廢（v2 支撐審計，2026-09-25）**：`42.03` 是 `d(step)/dT`
  （ms / verify-token），`48.2` 是「每產出 token 成本」⇒ **量綱不同，不能相除**。
  同義口徑（`Δstep/step_off`）下 09-24 的 `m(k=1)` = **1.176**（不是 0.872），與今天 0.868 差 **35%**。
- ★ **F24：09-24 的 `m(k)` = 1.176／0.840／0.961／0.920（散佈 1.40×）⇒ `m` 不是常數**
  ⇒ `cost = 1 + m·k` 連他批自己的資料都不成立 ⇒ 所有「把 m 當常數外推到別的 k」都沒支撐。
- 成本主項＝**verify 側 union 撐破 per-layer 配額**（MiB/step 19.9→49.9；
  worst distinct 111~130→168~205／slots 143 越界；layers_over_slots 0→2~4）
  ⇒ **批次化省的是權重讀取次數，省不掉「並集變寬」**。
  ⛔ v1 用「draft 前向只佔 ~7.5%」支撐這句 ⇒ **無出處已廢棄**（本輪 stderr 無 `CGC-MTP-PERF` 輸出）。

### 判決（v2 支撐審計後：只留有 M／D 支撐的）
- **可引用**：`S = E/cost`（量綱恆等，三輪誤差 <1e-3）、`E = 1+a·k_eff`（a 的定義式）、
  **`k_eff=1` 時 2× 數學上不可達**（`a≤1 ⇒ E≤2`，要 `S=2` 需 `m≤0`；**僅 k_eff=1**）、
  k=1 處彈性 `+0.383`／`−0.465`（**局部值**，k 變了就變）、原 html 高估 1.35~1.59×／低估 2.9~6.0×（算術）。
- ⛔ **已廢棄 9 條**（`docs/FORMULA_AUDIT_MTP_2026-09-25.{html,md}`）：
  k=3 的 `S=0.794`／`k→∞` 上界 `a/m=0.714`／「沿 k 軸加到多大都虧」／「a 拉滿 ⇒ +7%」(1.071)／
  「m≈0.87 差 0.5%」(量綱錯配)／「draft 7.5%」／「hit% 崩到 57%」／「S=1.19 來自兩儀器相除」(因果推測)／
  「a=0.44 是 ctx_other regression」(D5 未歸因 ⇒ 只能說相關未證明)。
- ⚠ **中位那一行不是真實的一輪**（逐列獨立取中位）⇒ 用中位 a、m 反推的 S 不是實測值；
  要引用 S 就報**三輪**的 0.914／0.682／1.050。
- **方向性結論不變**：三輪 S 中位 **0.914（淨負）**，與 09-21 獨立配對 **0.889** 同向。
- ⛔ `13.7 t/s` 出身：13.78 是 **MTP off** 的 HTTP 單流（`PARALLEL_AND_WORKERS_AB:114`，同文 `:173` 明寫別用）
  ⇒ 作廢；`13.78/11.49 = 1.199` 是算術可留，「所以它的 S 是這樣來的」是推測（廢棄）。
- 槓桿：**先降 verify 側 union 成本**（工作集／per-layer 配額），a 次之（k_eff=1 下 a 的彈性略小）；
  別練 draft head、別做 dynamic-k（不變）。要動 k 軸 ⇒ **先掃多個 k**（m 非常數，只量一個 k 不夠）。


## MEMORY.md 瘦身移入（第七輪，2026-09-26）

（以下三塊自 `MEMORY.md` 移出，原處各留一行指標）

### §A

  ⛔ **21:3x 那組「tg 28.15 ±0.04 / pp 377.16」不可引用**（`Backup/eseries/E2_seg/`）：
  MTP OFF／b5632／ctx 0（非交付 cell）、無同窗口對照（基準 HEAVY 窗 11.2~11.4）、
  **`io_symmetry.py` 判 `asymmetric`（基準 82374 reads vs S1 臂 0）**、池計數器塌縮
  （5720＝143×40 整數拍、隨機路由下不可能零 miss）。⚠ **`cell_contract` 的「15 項一致」
  不含 spec_type／輸出正確性／swap-thermal 窗口** ⇒ 它過關不代表可引用。
  全文 `.workbuddy/memory/2026-09-25.md` §EN-143x。
  ★ **「swap 是誰的」要分歸屬／成因**：★**歸屬＝別人**（23:20 實測 llama procs=0 而 swap 已
  3555 MiB；22:59 起跑快照 swap 2644 MiB 時 llama_procs=0；前 5 大全是桌面 999/803/524…）／
  **成因＝我們**（乾淨開機那輪 swap 0→5000：物理 16384 − wired 峰值 12371 ≈ 4013 MiB 留給桌面，
  桌面裝不進就進 swap）。「wired 回基線／swap 留著」是退出自明結果，不是 cache 失效的證據。
  ⚠ **本線曾誤斷「池＝Metal/wired 不可被 swap」—— 已更正**（成功輪 stderr 無 `expert pool split`
  該行 ⇒ split/Metal 路未走；`cache->pool[l][k]` 實為 `std::vector<uint8_t>`；唯曲線顯示「池開始填」
  那一刻 wired 恰 +3.3 GB 又指向 wired）⇒ **匿名 vs Metal 占比至今 UNRESOLVED**。
  ★ 原始帳本：hit 96.1%，**miss 中 compulsory 82.4%／capacity 17.6% ⇒ 池能影響的只有全部請求的
  0.68%**；問題是「貴」（常駐 6197 MiB）不是「沒用」。t/s 增益不可由此推算。
  ⛔ **`wired_probe.py`（8 vs 1 GiB 池配對）今晚兩臂皆 rc=-6 SIGABRT**，同死於 `test_prompt`
  Metal OOM（`ggml-metal-context.m:904`），`slots_layer` 有差 147/18 但沒跑到 decode
  ⇒ 「wired 峰值 12383 vs 12325，差 57 MiB」**絕對不可引用**，`attribution` 欄是 null。
  ⛔ **縮小 pool budget 的 A/B 另被 `cell_contract` fail-closed 擋住**（8192 是權威常數）
  ⇒ 需一張例外測試卡。全文 §EN-144x／§EN-145x。

### §B

  ⛔ **「MTP off 9.82 vs on 12.62 ＝ +28.5%」已作廢**（2026-09-25 operator 下令）：HTTP 舊口徑、無 warm-skip，
  且**現行生產口徑 MTP off 已是 11.03~12.20 ⇒ 分母本身失效**。**交付 cell 的 MTP 增益 = UNRESOLVED。**
  登記表 `docs/MEASUREMENT_CONTRACT_2026-09-25.md` §7；機檢 `scripts/check/void_number_check.py`。
  ★ **M 軸的下一步在 `docs/MTP_ON_OPTIMIZATION_PLAN_2026-09-25.{md,html}`**（20:2x 定稿，無新 GPU 量測）：
  三條主結論 N1 draft head 只佔 verify 邊際 token 的 **12%（target verify 88%）**／
  N2 通道 **device span 56–61%、host hook 35–40%、dispatch 2%**／
  N3 ⚠ **加深 = UNRESOLVED**（不是「別加深」的實證結論 —— 門檻係數 `14.61/42.03` 出自一份
  `quotable: False` 的產物：10 次 launch 5 次污染、window unknown、IO blocking asymmetric；
  且該產物四個 k 點 step 非單調、一階差分 24.29/58.03/38.42 ≠ 常數 42.03）。
  ⚠ **C1 矛盾**：交付 `ASL=tps×step` **3.117** vs 今天直數 round 的 `E` **1.62**（差 1.92×）⇒ 未裁定前
  「加深 k」與「提高 a」都無法寫死 ⇒ **P0 就是為它設計的**（spec_cost_curve ＋ `CGC_BENCH_WARM_SKIP=64`）。
  ⚠ **未調停的衝突**：`s1_ksweep/20260924_225548` 的 k1–k4 pool 是 **2173/2173/0/100.0%**（零 miss），
  看不到 `MTP_K_SWEEP_2026-09-22.md` §4 的「MTP 臂 miss ≈ OFF 3×」⇒ 報告明寫不調停，P1 一起量。

### §C

★★ **量測契約 `docs/MEASUREMENT_CONTRACT_2026-09-25.md`（必讀）**：①分解同報 ②里程碑雙欄制
③形狀一律 `-r 3`、pool 是 cell 的一部分 ④每支臂 目標→判準→結果→判定 ⑤**成果分級**：
①攻關成功＝pp≥250 ∧ tg 超越 12.57（**兩件事同時；本線現況沒有 ①**）⑥子目標 S/M/兩者/C/不適用
⑦每次實驗畢必出分解報告 ⑧**作廢數字表（§7）** ⑨總圖 `docs/mindmap/`（**44 條目**）。
★ **M 軸 v2（支撐審計）** `docs/MTP_AMORTIZATION_RECHECK_2026-09-25.{html,md}`：
**k_eff=1（不是 3）／S=0.914·0.682·1.050（三輪，淨負）／E=1.62／a=0.62／m=0.868（僅 k_eff=1）**
⇒「攤薄倍數幾乎完全由 a 決定」**不成立**；原 `docs/mtp_amortization.html` 已歸檔。
★ **公式推論支撐分級**（operator 09-25 下令，機檢 `formula_audit.py` **13/13**）：
`M` 實測／`D` 量綱恆等或定義式 ⇒ 可引用（D 須標適用範圍）；
`X` 外推到未量測點／`A` 假設兩量獨立／`S` 推測因果／`U` 數字無出處 ⇒ **一律廢棄**。
⇒ v2 廢棄 **9 條**：k=3 的 0.794、`a/m` 上界 0.714、「沿 k 軸都虧」、a 拉滿 +7%、
**「m≈0.87 差 0.5%」（量綱錯配 ⇒ 同義口徑 1.176 vs 0.868，差 35%）**、「draft 7.5%」、「hit% 57%」、
「S=1.19 來自兩儀器相除」、「a=0.44 是 ctx_other」（未歸因）。★ 09-24 的 `m(k)` 散佈 1.40× ⇒ **m 非常數**。
★ **mindmap 總圖**：維度＝階段（生產 14／實驗 10／已結案 20）× 子目標 5 類（**S 18**／M 7／兩者 1／**C 8**／不適用 10）；
**C＝天花板軸，必須留在分類裡**（否則 device span 56–61% 無人認領）；**both 保留**（收益不可相加）；
**每條目一對白皮書** `briefs/<id>.html`＋`.md`，總目錄 `briefs/index.html|md`。
機檢 `mindmap_build.py` **19/19** ＋ `mindmap_brief_build.py` **13/13** ＋ 冒煙 **62/62**
（需 jsdom；ESM 不吃 NODE_PATH ⇒ `JSDOM_PATH=<workspace>/node_modules/jsdom/lib/api.js`）。
★ **已交付 commit `65c76b8c7`**（146 檔，含他線 src bytes；**未 push**）⛔ **但 D5 未通過**
（M1 2/3／M3 2/3，coverage 60% vs 歷次 100%，**未歸因**）⇒ **owner 重新基線前不要引用它**。
**契約九條逐條全文 ＋ 分級 17 列 ＋ 作廢數字 292 處盤點 ⇒ `MEMORY_HYGIENE.md` 末節（第六＋七輪移入）。**

（並行 session 安全、llama-bench 單口徑、配對設計、`--reps`、儀器口徑坑、入口指令、索引重生順序、環境坑、Skill 清單）

## 第八輪移入（2026-09-26 11:3x，`MEMORY.md` 瘦身：16.2KB → ~9.6KB）

索引檔只留導航與一句話，以下為自 `MEMORY.md` 移出的全文（內容一字未改）。

### §A D5 紅燈的定案全文（P0，09-26 10:2x）

✅ **「基準的語意基準移動了」，不是儀器故障也不是 bug；護欄 M1/M2/M3 本身有效。**
判讀一個 D5 FAIL 的**三問順序**：① `comparable` 是 true 嗎（**false ⇒ 跨配置比較，不是退步，
直接丟棄** —— 09-23 `pfx-ckpt2` 的「9/9」就是這種，別當基準）；② **哪個 `ctx_type` 的行動了**；
③ 差異**量級**（ULP 級 vs 語意級）。
- 本輪實測：**6 個 DEF 行全同、3 個 MTP 行全不同**（`[2,0]/[3,0]/[4,0]`），`sum` 差 **8.4%~12.5%**
  （語意級），新 hash 跨 5 次執行/3 個 build **完全一致**（確定性）。
- 歸屬＝**`65c76b8c7`（09-25 17:16）**：對 `LLAMA_CONTEXT_TYPE_MTP` 保留 `ctx_other`
  ⇒ draft **不再自算 KV、改讀 target 的 KV** ⇒ 位移限定在 draft。
- ★ **引擎今天可重現**：長探針兩次獨立 launch **1045/1045（含 447 個 MTP 行）逐位相同**、
  `E` 五次 launch 同一字串、池計數器兩臂一致 ⇒ **「bit-identical」是今天已實測的性質**，
  缺的只是把 pin 重校準到共享 KV 之後的行為（**這是決定，不是工具細節**）。
- ★ **閘門該用 (c)「同 build 對照臂」**，不是 (a) 重校準 pin、也不是 (b) DEF 子集：
  ⛔ **(b) 已作廢** —— `llama-context.cpp:5435-5438` 自述 MTP draft 的專家張量**也被縮到池容量、
  也被 repoint 到池區域**、**也靠同一個 hook 寫 remap leaf**；`ggml-backend.cpp:3032`「the MTP draft
  context runs through here too」⇒ 分段／hook 對 draft **一視同仁** ⇒ 我們的工作**必定**碰 draft。
  ★ **(c) 的理由**：硬約束是「**保持** bit-identical（不要動到數值）」，**不是**「重現 09-17 參考檔」;
  pin 回答的是後者。做法＝ control 臂 dump → `m123_oracle_gate.py --ref <該檔>` 跑 treatment
  （先例 `cgcMTP_B` 1045/1045）⇒ **涵蓋 draft、不需任何「重校準」決定、已證可行**。
  (a) 仍值得做，但那是獨立的「把常駐紅燈關掉」家務決定，前置問題是先答「共享 KV 的 draft 是不是
  要的行為」（切換前 `mean len 2.02` vs 現在 `1.70`）。
- **全文／判讀腳本 ⇒ `2026-09-26.md` §EN-10:2x & §EN-10:1x ＋ skill `cgc-commit-gate` §2.4c ＋
  `docs/MTP_CTX_REPRODUCIBLE_2026-09-26.md`。**

### §B layer-ahead prefetch 結案全文

✅ **機制不成立**：`llama-context.cpp:7354` 每層 hook 都呼叫 `drain_layer`，而
`llama-expert-cache.cpp:1555` 會清掉該層**所有排入未開始**的 prefetch。實證：drop breakdown
兩臂**只有第 3 項非零**（la `total=15` / ctrl `total=148`，其餘 12 原因全 0）。
＋ 量測面同臂漂移 **+34.3%**（8.47→11.38，跨 2h）⇒ −7.1% 不可引用。全文 `2026-09-26.md` §EN-09:5x。

### §C 里程碑／天花板全文

- **天花板** L0 12.57／L1 1.33%（不做）／L2 0（證偽）／**L3 = 0**／L4 ~75（不可及）
  → `docs/CEILING_STACK_2026-09-21.md`。
- **★ 里程碑**（09-21 版 `docs/MILESTONE_MAP_2026-09-21.md`，**已由 2026-09-25 複核取代判決**：
  `docs/MILESTONE_MAP_RECHECK_2026-09-25.md`）。本線格子：M-L1 不做／M-W ≈+1.7% 低於門檻／
  M-L3、M-PF 判 0（撤回）／M-S2 不可達／**M-K5 不做**。
- ⚠ **M-25 判死**。依據（**生產口徑**）：fill 的**同步**時間只佔 decode step **4.7%**
  （EBTIMER 直讀；權威 `docs/IO_AXIS_VERDICT_2026-09-25.md`），端到端待 `nf2_nofill` 配對定案。
  ⇒ 即使把 fill 完全消滅也只有 +5%（端到端若為 ~20%，上限也就是 ~20%）⇒ **到不了 25**。
- ⚠ **2026-09-25 凌晨那批非生產口徑數據已全部作廢並刪除，勿引用。**
- ⚠ **M-F5／M-CB 是線 I 的格子**，本線無實測權，表中標「推算，需重述」**不是撤回**
  ⇒ 說「表一格不剩」只在**本線範圍內**成立。
- ⛔ **上面這兩張牌都已經跑完並結案了，別再當待辦推出去**（2026-09-26 我照這句瞎推薦了一次，
  被 operator 當場抓到）。複核 §5.1 的「原順序」表是**當初的排程**，§5.2 才是結論：
  `CGC_LAYER_AHEAD_PREFETCH=1` ⇒ **−7.1% 未分離、④ 廢棄**
  （`Backup/layer_ahead/VERDICT_layer_ahead_2026-09-25.md`：a2 8.47／a3_la 10.57／a4 11.38，
  最佳配對 a4 vs a3）；M-W ⇒ t/s −0.6% 未過門檻、④ 廢棄。
  ⇒ **教訓：工作記憶裡的「排在待辦最前」不等於「還沒做」；引用任何牌之前先去 §5.2 看它有沒有
  量測欄與判定，不要只讀「原順序」。**
- ⇒ **★ 09-26 結案：本線表上一格不剩（含 S1）。** 判「**gap ≡ 44.4 ms 是 fill 的窗口，不可分離**」
  ＝終局。三條獨立機制級證據：① `llama-context.cpp:7354` 每層 hook 的 `drain_layer` 清掉未開始的
  prefetch（無重疊窗口）；② 預測性預填被實測判死（`prev_union` h = **0.022~0.032**，門檻 0.65；
  top-16 的**結構上界只有 18~27%**，因真實 union 均值 19.0~19.8 > 16）；③ 唯一指向 gap 的機制
  （段數 41→1）需要「submit 前知道 ids」，而三個預測機制（`CGC_PREV_TOKEN_PREFETCH`／
  `CGC_LAYER_AHEAD_PREFETCH`／`CGC_PREROUTER`）**共用同一個來源**（`:5890`「Same prediction source」），
  且代碼假設「相鄰 token 重合 70~90%」**與交付形狀（ntok=4）實測的 51~56% 直接衝突**。
  ⇒ 應以**負結果**結案。全文 `docs/S1_CORRECTNESS_SPEC_2026-09-26.md` §8 ＋ `2026-09-26.md` §EN-10:4x。
- ⚠ 唯一還活著的是 (iii) 保留分段、把 hook 做便宜 —— **常數優化，barrier 12.9/158 ≈ 8%**。
- ⚠ 「M1 卡住／M2 落地／M3、M4 未開始」是 **D5 判決指標**的另一個意思，先確認問哪一個。
- **K0–K5 全部結案** → 本檔末節「K0–K5 結案總表」。
- **MTP／spec**（m、RSL、別練 draft head、別做 dynamic-k）→ 本檔「## MTP／speculative」。
  ⛔ **MTP 增益／N3 加深** 細節 ⇒ 本檔「## MTP／speculative」＋末節第七輪 §B。一句話：**交付 cell
  的 MTP 增益 UNRESOLVED**、N3 加深 UNRESOLVED、C1 矛盾（ASL 3.117 vs E 1.62）未裁定 ⇒ P0 為它而設。
- **G4／S2 已結案**：S2 建議不做完；**G4 別寫融合 kernel**。

### §D 交付 decode／prefill／乘載全文

- **交付 decode＝ `12.57 t/s`**（llama-bench，`prod_profile.py`，reps=3，全程 NOMINAL，±2.26）。
  不要用 `profile_duo` 的 decode cell。單臂噪音底 ≈ ±27%，3% 門檻。
  ⚠ **`±2.26` 是 stdev 不是 CI** ⇒ 12.57 的 95% CI 半寬 **±45%** ⇒ **只能當配對比較的一臂，
  不能與另一次 anchor 的單點數字比**（本檔「交付 decode 權威讀數」塊）。
  ⛔ **28.15／池 wired 歸屬／pool A/B 被擋／wired_probe 兩臂 SIGABRT** ⇒ 本檔末節「第七輪移入
  （2026-09-26）」§A。一句話：**21:3x 的 28.15 不可引用**；池＝wired 或匿名 UNRESOLVED；
  池 hit 96.1% 但 capacity miss 只佔 0.68%；縮池需例外測試卡。
- ★ **16GB 上唯一實測存活的組合＝ `--load-mode none` ＋ ngl 99 ＋ pool 8 GiB**（09-26 夜：mmap 0/2
  載入即死、none 2/2 存活；ngl 階梯與 pool-off 皆無單變量實測）⇒ 可說「跑得動」，**不可說「最快」**。
  ⚠ none 臂今晚 6 個 prefill 讀數 **228.94–258.25**（3/6 ≥250，r2 全數 <250 且標 HOT）
  ⇒「每次都 ≥250」不成立。
- ★ **prefill 250+ ＝ 已達標**（同 cell `-p 2048`。9 次 launch ≥250，最高 296.24；乾淨視窗 283.01）。
  ⚠ **白皮書的 `ctrl 162.5 → p0+B 209.5（+29%）` 作廢** —— 那是降級視窗的產物：同支臂十分鐘內
  四次 launch 是 275.39/196.72/207.33/204.98（同臂散佈 78.7 t/s）。未達的是**可重現的地板**，
  不是峰值。來源：`docs/PREFILL250_MET_AND_S1_REJUDGE_2026-09-24.md` §1。

### §E S1 節全文（`MEMORY.md` 的「目前第一順位：單段提交」）

**2026-09-24 18:2x（§EN-510）單段提交 ＋ GPU slot lookup**：配對 ABBA 實為 **1.72×**
（A 分段 11.84／B 單段 20.32，`Backup/seg_batch_s1_pairs/abba_212809.json`）；
⚠ 原報的 **2.2× 分母取自已失敗子視窗**，且**兩臂 I/O 結構不同**（B `file_reads=0` ⇒ 填充路徑沒跑）
⇒ 比值含「填充路徑不執行」，**不可歸因為 shape** ⇒ 新硬規則 `scripts/check/io_symmetry.py`
（I/O 不對稱 ⇒ verdict `shape-confounded`、exit 2）。**09-24 夜修訂全文見本檔末節「第五輪（2026-09-25）」。**

- ★★ **權威單頁 `docs/S1_LINE_VERDICT_2026-09-25.md`（引用前必讀）**：同 build 同 cell 乾淨分解
  A 分段 **11.30** → B 單段 **20.73**（**×1.83**，兩臂皆無 spec）→ spec k2 **22.45**（+8.3%）
  ⇒ **那個 2× 是 S1 的，不是 MTP 的**。
- ⛔ **>20 t/s 的 S1 讀數判不可信**（garbage／無 `answer_md5`／B 臂 ~8 token 撞 EOS）；
  **「23.3 t/s」不可引用**。
  ★ **撞名：兩個東西都叫 S1** —— 探針臂（`CGC_SLOT_TABLE_GPU`，576/576 通過但無速度主張）
  vs 單段提交（`CGC_SEG_BATCH+CGC_B_SCHEME`，無 hook ⇒ garbage）。引用前先問是哪一支。
- **主項是序列化不是 fill**（消掉 40.3 ms 裡 fill 只 3.96 ms／4.7%）。
- ⚠ **第 2 步 miss mask 的程式碼曾經根本不在樹裡** —— 「已完成」是 `docs/MISS_MASK_STEP2_2026-09-24.md`
  的文件結論，不是樹裡的事實（`git grep` 只有 docs、53 個 binary `strings` 全 0）⇒ **2026-09-26 已重建
  並 commit `553424ec1`**（`strings -a libllama.dylib` 驗 5 個字串已編入）。原「38 層／421 元素／0 差異」
  是 **09-24 那次的結論，重建後尚未複驗**；第 3 步閘門 OPEN 的 0~66%／落點 14.1~19.9 t/s 仍是**推算**。
- ★ **G2 已量（2026-09-26，`harness.py bench`+`prod-new`）＝ 42.93%**（14926 行／385 compute／39 層，
  micro=macro，drift Δ=−0.001；publisher `wrote=39/39`，resident 5577/9984 ＝ 每層 143/256 駐留）。
  ⛔ **09-26 03:0x 那個「100%（8/8 全佔位）」已作廢 —— 它是儀器產物**：publisher 因
  `cgc_node_in_graph` 只查 op 節點、不查 `leafs`，39 張 leaf 全被判「不在圖中」⇒ **一個位元組都沒寫**，
  裝置端讀到的是競技場殘留。全文 `docs/SPEED_ACCEPTANCE_GATE_2026-09-26.md` **§9.8**。
  判決**仍是 >20% 分支**（先做 async fill），但現在是量測不是產物；⛔ **兩個都不可拿去定價交付 cell**
  （本臂＝「單段＋hook 不跑」配置，fill 不會發生）。前置 FIX：`vmask` 缺 `ggml_build_forward_expand`
  會在首啟動 abort（`ggml-alloc.c:623`）。今晚 pp/tg 全部不可引用（見 `MEMORY_S1.md`）。
- ⚠ **`cgc_node_in_graph` 對 leaf 一律 false**（`ggml_new_tensor_2d` 的 tensor 落在 `cgraph->leafs`）
  ⇒ `llama-context.cpp:3919` 的 `rm`（rn_mask）、`:3923` 的 `tb`（slot table）**長期靜默為 nullptr**，
  任何靠它們推出來的結論都要重跑。本線只修了自己那一處（新增 `cgc_tensor_in_graph`，nodes＋leafs），
  既有呼叫點未動。
- ⚠ **fill 是正確性前提不是速度前提**；開關兩半分在 `libggml-base`／`libllama` ⇒ **驗 binary 要總掃 `*.dylib`**。
- **全文（1.72×→1.83× 修訂、池計數器塌縮、18.1 層/步、k-sweep 3 GiB 不可併排…）⇒ `MEMORY_S1.md` 末節
  「MEMORY.md 瘦身移入（第六輪，2026-09-25）」。**

**其餘所有線（ρ／prebind／方案 A／k=3 飄移／cb 42vs74）都是 09-23 的 dated 判決，
全文在本檔末節「MEMORY.md 瘦身移入（第四輪，2026-09-24）」，索引不重抄。**
一句話帶過：ρ 與 prebind 各自判活、同 regime 下兩者同價（**+14.0%~+14.5%，12.57 → ~14.3~14.4**），
`cb` 已定讞為 42~51 ms（74.18 撤回），視窗機械排除；**但 S1 出現後它們都降為第二順位**。

## ★★ 第九輪：`CGC_CB_N_MAIN`（Metal 編碼切分）＝ 第一個打桶的乾淨結果（2026-09-26 14:24）

**全文 `docs/CB_N_MAIN_BUCKETS_2026-09-26.md`；產物 `Backup/phase_decomp/cbnmain_buckets/`。**

`n_main`（`ggml-metal-context.m ~:1141`）＝ **Metal 後端把圖切給「誰來編碼」的分界線**：
`n_main` 個節點由**呼叫執行緒**序列編碼並提交，其餘由 `n_cb` 個 worker 並行。
預設 `MAX(64, 0.1*N)`；**分段後 `N≈99` ⇒ 地板 64 接管 ⇒ 65% 節點序列編碼**，
而 **64 是上游在 M1 Pro／M2 Ultra ＋ LLaMA 上調的**，不是這個 workload 的。**0 重建、純 env。**

**ABBA（`ctl64 → nm32 → nm32 → ctl64`，桶儀器兩臂都開，起跑 compressor QUIET）：**

| 臂 | n_main | tg | total | wait | cb | submit |
|---|---:|---:|---:|---:|---:|---:|
| ctl64a / ctl64b | 64 | 9.94 / 10.21 | 87.96 / 86.17 | 75.02 / 73.78 | 5.11 / 6.03 | 5.42 / 5.33 |
| nm32a / nm32b | 32 | **11.18 / 11.54** | **77.21 / 77.08** | 68.13 / 67.08 | 4.99 / 5.78 | **4.07 / 4.21** |

**配對差**：`total −9.92 ms（−11.4%）`／**`tg +12.8%`**（step −11.4% 推得 +12.9% ✓ **兩條獨立測量互證**）；
分解 **`wait −6.80（69%）＋ 餘項 −1.70（17%）＋ submit −1.24（12%）＋ cb −0.19（2%）`**。
★ 控制臂散佈 2.0%、**處理臂散佈 0.17%** ⇒ 效應比噪音大一個量級。
- **不是打 `cb`**（噪音內）；**打到 `submit`（−23%）**（分界線變小 ⇒ 更早提交首個 CB）✓。
- ⛔ **`wait` 是複合量（union＋gap）**：`CGC-DECPROF topN` 的 `union=/gap=` 全 `0.00`
  ⇒ 因為 **`CGC_GPU_TIMING` 沒開**（`ggml-backend.cpp:1860`；在 allowlist ✓）。
  ⇒ **拆分未定案：gap 縮＝目標；union 縮＝不算業績。** 結構性論據（`segs`/`layers` 全同 ⇒ 工作量不變）
  支持「是 gap」，但**不是證明**。
- ⛔ **未驗 D5**（改 CB 切法 ⇒ M1/M2/M3 逐位元不變必須證明）；⛔ **只測 32 vs 64**
  ⇒ 可引用述句＝「**`n_main` 不該留在預設地板 64**」，不是「32 最優」。
- ⓘ 此 cell **`fill` 不適用**（`CGC-HOOKSPLIT` 沒印 ⇒ hook 沒跑）。

**★ 桶儀器的真名（tag ≠ env）**：`CGC-DECPROF` ↔ **`CGC_DECODE_PROFILE`**（住 **`libggml-base`**）；
`CGC-HOOKSPLIT` ↔ **`CGC_HOOK_SPLIT`**（住 `libllama`）。四個桶 env **都已在 allowlist**
（`CGC_DECODE_PROFILE`／`_ALL`／`CGC_HOOK_SPLIT`／`CGC_EB_TIMER`）⇒ 打桶不需要任何前置。

**★ 第九輪補：D5 長探針 PASS（2026-09-26 14:33）** —— `n_main` **對數值中立**：
control（預設 64，`--write-ref`）vs treatment（`--env CGC_CB_N_MAIN=32`，`--allow-incomparable`）
在**長探針 1045 行上 M1=M2=M3=1045/1045**、`only_A=0`／`only_B=0`、cross-tab 三格全 0；
`ctx_type` **DEF 598／MTP 447** ⇒ **MTP 行也逐位相同**。**唯一的 config diff 就是被測的那個鍵**
（⇒ 證明武裝）。產物 `Backup/phase_decomp/cbnmain_d5/`。⚠ 閘門把那三行標「NOT a verdict」
（配置差一鍵）—— 行級證據與戳記無關；control 自己的 `rc=2`／`M1 1/9` 不可讀（它拿長 dump 比短的預設 ref）。
⇒ 合併 ABBA：**同樣 logits、更少時間** ⇒ 一個**數值免費、0 重建**的槓桿。**仍缺 `CGC_GPU_TIMING` 拆 union/gap。**

**★★ 第九輪補二：sweep `{16,64,128}` ＋ `CGC_GPU_TIMING`（2026-09-26 14:46）—— 推翻兩個先前說法**
- ⛔ **`union` 不是不變量**：`nm16` 用**同一組節點**把 union 從 **73.1 → 64.5**（−8.6）、gap 15.7→20.3
  ⇒ **union 是排程的函數**；「節點不變⇒只能是 gap」的推理**作廢**，拆分必須逐臂量。
- **`n_main=64` 的帳**：union **73.1** ＋ gap **15.7**（gap ≈ 步長 19%）；控制臂 GPU 側內部一致（−1.8%/+8.2%）。
- ⛔ **「64 最差」不成立**（那是髒窗產物）：乾淨窗下 **`16`（7.89）比預設 `64`（10.19/11.13）還差**
  （`submit` 3.77→7.31 近一倍 ＝ 餵不飽）；**`128` 中性**（t/s −2.0%，但 **`cb` −38%**：5.46→3.38）。
  ⇒ 改述：**`16` 是陷阱、`128` 中性、`32` 值得追、預設 64 中庸不是最差。**
- ⛔ **仍缺**：**`n_main=32` 的 union/gap 沒量** ⇒「−11.4% 是 gap 還是 GPU 效率」對 32 未答。
- ⚠ **儀器教訓**：`CGC-GPUTIME` 的 `skipped==0` **必要不充分** —— `nm128` 連合格行也給 `union=795 ms`
  （步長 82 ms）⇒ **讀之前先看值合不合理**（荒謬 % 是紅旗）。產物 `Backup/phase_decomp/cbnmain_sweep3/`。
