# MTP-on 生產路徑存活翻案 ＋ llama-bench sched 過度分配根因（2026-09-27）

> **這份文件的來源**：`.workbuddy/memory/2026-09-27.md` 的 `## §EN-475 2026-09-27：MTP on 生產路徑翻案（存活）＋ llama-bench 量具 sched 過度分配根因`。
> `.workbuddy/` 被 `.gitignore:36` 排除 ⇒ 那段記憶**從來不在版控裡**。本檔是把它落成可引用產物的第一份；
> 記憶本身的實體鏡像是 `agent_harness/memory/2026-09-27.md`（snapshot，不是權威），索引入口是
> `agent_harness/engine_loop/memory/INDEX.jsonl`。

## 0. 編號：§EN-475 是同號，不是同節

- `§EN-475` 在 **2026-09-23** 已被佔用：`.workbuddy/memory/2026-09-23.md:1745`「oracle all-hit replay『能提升啥』→ **0 t/s，提升的是決策**」，
  `INDEX.jsonl:228` 與 `docs/CAPABILITY_VS_FLASHMLX_2026-09-24.md:121/150/160` 指的都是那一節。
- 同理 09-27 的 `§EN-474`（GPU 內核級 flock，對應 commit `44569cd4e`）與 09-23 的 `§EN-474`（expert cache × FlashMLX 判決）也撞號。
- ⇒ **引用一律帶日期／路徑**：`2026-09-27 §EN-475`（或本檔）。不要用裸 `§EN-475`。

## 1. 翻案（最重要）：引擎／生產路徑沒有 prefill OOM

直接啟生產 server（`CGC_SERVER_PROFILE=prod-new CGC_SERVER_MTP=1 bash scripts/run_server.sh`），真實 HTTP prefill 請求**全部存活**。

證據：`Backup/cgc_logs/llama_server_20260927_222748.log`（22:27 起，build 630；`/tmp/mtp_server.log` 記 `[mode] MTP ON (draft-mtp, n_max=3)`）。

| 觀察 | 讀數 |
| --- | --- |
| 請求數 | 4（`launch`=4；另有 1 次 2048 寬 prefill） |
| prompt | 825／825／1761／2034 tok |
| prefill | 164.70（冷）／188.81／305.76／310.20 t/s |
| decode（16 tok 短樣） | 5.67／13.47／9.43／10.8 t/s |
| draft acceptance（MTP 真的在跑） | 0.318／0.318／0.273（mean len 1.88／1.75） |
| `truncated` | 0 |
| `CGC-METAL-FAIL`／`Insufficient Memory`／`OutOfMemory` 命中 | **0**（全檔 grep） |

`/private/tmp/prefill_*.txt` 是那幾次請求的 HTTP 回應本體（答案正確）。

放大口徑（同一天、同一 build）：09-27 共 13 支 `MTP=1` server、21 個請求全存活、零 OOM；09-25→09-27 合計 40 支、52 請求、0 OOM。
最長的真實 prompt 在 09-26 00:00／00:13 兩支（2873 tok ×3）。

## 2. 量具根因：llama-bench 的 pp reserve graph 比 server 寬 5.5 倍

同模型、同 cparams、乾淨對比（`CGC-RESERVE-SIZE: … after-PP sched=`）：

| 跑法 | ctx | target sched | 結果 |
| --- | --- | --- | --- |
| `llama-server`（MTP **on**，`/tmp/mtp_server.log` ← 222748 log） | 8192 | **1016.81 MiB**（ctx#3；ctx#4 MTP 側 984.87） | 正常出 token |
| `llama-server`（MTP **off**，`Backup/cgc_logs/llama_server_20260927_225143.log`） | 8192 | **1016.81 MiB**（ctx#2） | 正常出 token |
| `llama-bench`（MTP **on**，`/tmp/mtp_on_clean.log`、`/tmp/mtp_on_lazy.log`） | 8192 | **5599.48 MiB** | rc=-6 |
| `llama-bench`（MTP **off**，`/tmp/direct_bench.log`、`/tmp/mtp_off_ctx8192_run.log`） | 8192 | **5599.48 MiB** | rc=-6 |
| `llama-bench`（ctx derived2688） | 2688 | 2530.72 MiB | rc=-6 |

⇒ **與 MTP on/off 完全無關**：同一跑法開關兩側的 sched 一模一樣；1090 MiB vs 5599 MiB 的差別在**跑法**。

機制：llama-bench 的 pp reserve graph 用 full `n_tokens = min(n_ctx, n_ubatch)`（`src/llama.cpp/src/llama-context.cpp:1086/1136`），
ctx8192 ⇒ 5632 寬 × ≈0.99 MiB/token ⇒ 5599 MiB；server 走 streaming／slab，reserve 只有 1016 MiB。
`PHASE-SPLIT`／`CGC-SHAPE`（M=8）兩側打印相同 ⇒ 差異不在模型或 MTP，而在 reserve graph 的實際寬度。

死法：`ggml-metal-context.m:925  CGC-METAL-FAIL: command buffer 1 failed (status 5, Insufficient Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory))`（rc=-6）。
重開機後重試（21:59／22:18／22:37 MTP on、**22:48 MTP off**）全部 rc=-6 ⇒ **off 也死**，即量具問題、不是 MTP 的問題。

## 3. 被推翻的假設

`--ctx-size 8192` 讓 sched 從 2530.72 反升到 5599.48，prefill 仍 status 5 OOM ⇒ **加大 n_ctx 不解 OOM**（它把 reserve 一起放大）。

## 4. 修復方向（量具，不是引擎）

1. 讓 llama-bench 的 pp reserve graph 寬度對齊**實際 prefill chunk**（而非 full ubatch）——這是讓權威 cell（`llama-bench --spec-type draft-mtp`）能出分數的關鍵。
2. 或 harness 測 MTP **on** 改走 server certify 路徑（`run_server.sh prod-new MTP=1` ＋ 真實 prefill）。

殘留：卡住的 server 對 SIGINT／SIGTERM 無反應（雙 SIGINT 踩 `GGML_ASSERT([rsets->data count] == 0)`，`ggml-metal-device.m:657`），最後只能 SIGKILL。

## 5. 限度（不要把 1 節讀成 soak 結論）

- **不是 soak**：單 process、4 個請求、約 1.5 分鐘。
- prompt 是 `send_prefill.py` 的合成重複文（真 HTTP／真 token 流，但不是上游生產文件）。
- 這是**存活**證明，不是穩定度或 TPOT 證明；MTP=1 的 decode 仍低於 off（約 −14%），走廊結論不變
  （見 `docs/ACCEPT_LEVERS_AND_DIRTY_BOX_PAIR_2026-09-26.md` §12.2／§12.3 與 `Backup/mtp_off_corridor/off_corridor_20260927.json`）。

## 6. 可重建指令

```sh
cd ~/Documents/flashkv-devserver
# 1) 生產路徑存活（要一個真實 prefill 請求）
CGC_SERVER_PROFILE=prod-new CGC_SERVER_MTP=1 bash scripts/run_server.sh   # 看 log 的 CGC-RESERVE-SIZE / draft acceptance
# 2) 量具（會死，死法就是本文第 2 節）
llama-bench … --ctx-size 8192                                             # 看 CGC-RESERVE-SIZE after-PP sched=5599.48 與 status 5
```

歷史對照（MTP=1 早期確實 OOM 過，backtrace 帶 libllama `0.0.279`／`0.0.578`）：
09-16 113518／120538／131547／131720／133211／133345、09-17 135458、09-19 141304／203833、09-20 140934／142546、09-24 095307／103204；
**09-25 之後（現行 build 630）沒有再發生**。
