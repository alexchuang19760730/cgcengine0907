# MTP on 修復方案（2026-09-28）

> 範圍：只回答「要怎麼修」。**本輪沒有跑量測、沒有 build、沒有改 `src/`、沒有動任何 shared 檔。**
> 證據分級：**【實讀】**＝從 09-28 那兩趟的 stderr 直接讀出；**【檔證】**＝模型檔／GGUF 普查可重現；
> **【碼證】**＝工作區程式碼可驗證；**【推算】**＝公式推導，非量測。

## 0. 一句話

**`rc=-6` 不是 bug，是餘量不足：ON 比 OFF 高 ~180 MiB，而這台 16 GB 機器在權威 cell 上只剩 ~47 MiB。**
修法不是「修 MTP」，而是**先騰出 ≥150 MiB 讓 target prefill 過關，再砍掉 draft ctx 那 1 GiB 形狀**
——後者樹上已經有兩道閘，只是**從來沒有被走到過**（它們在 draft ctx 建立時才生效，而我們一直死在那之前）。

## 1.【實讀】精確的帳（不是 census 估算，是兩份 stderr 對讀）

| 階段 | OFF（03:52，`prod-new`） | ON（03:32，`+draft-mtp`） | 差 |
|---|---:|---:|---:|
| `expert_index built` | 30720 | 31488 | +768 |
| `CGC Soft Pool init` | n_slots=143 | n_slots=143 | 同 |
| `ctx#1 after-PP sched` | 2530.72 MiB | 2530.72 MiB | **完全相同** |
| reserve 當下 rss | 7623.2 | 7801.0 | **+177.8** |
| PP 穩定期 rss（il=1..38） | ~8103 | ~8282 | **+179** |
| il=39 當下 rss | 8341.7（**存活**） | 8335.3 → 8363.3 → **8388.5（崩）** | +46.8 |
| `CGC-HOOK` 最後一層 | il=39 `ids=[108 226 …]`（正常） | il=39 `ids=[-67044352 … ]`（**垃圾**） | — |

三條硬結論：

1. **ON 全程只比 OFF 高 ~180 MiB，而崩與活只差 ~47 MiB。** ⇒ 需要的不是大改，是 ~150 MiB 的 margin。
2. **崩時 `ids` 是垃圾，那是結果不是原因。** `CGC-METAL-FAIL` 是在 `ggml_metal_synchronize` 才被觀測到，
   hook 只是先一步把 stale buffer 印出來。追「il=39 的 ids 為什麼是 -67044352」是錯的方向。
3. **`CGC-GATHER-SLAB` 三條（110 + 110 + 136 = 356 MiB）兩趟完全相同** ⇒ slab 不是差值來源，
   差值全在 model buffer。

### 1.1【檔證】那 180 MiB 是什麼

`scripts/check/gguf_tensor_census.py`：`block_count=41`、`expert_count=256`、`nextn_predict_layers=1`
⇒ ON 的 expert index = 41×3×256 = **31488**（一分不差）。

| | MiB |
|---|---:|
| blk.40 的 `ffn_down/gate/up_exps`（全寬 256）| 278.00 |
| 在 cap=143 下實際 resident（`278 × 143/256`）| **155.34** |
| blk.40 的非 expert 張量（attn / eh_proj / shexp / norms）| **24.95** |
| ⇒ 合計 | **180.29**（實測 177.8，差 1.4%）|

**24.95 MiB 是普通 tensor，任何 expert-cache 旋鈕都砍不掉**；能砍的只有那 155.34。

## 2. 修復目標：要騰出多少

- 回到 OFF 的存活線只需 **≥47 MiB**。
- 但 ON 在 il=39 時 rss 還在往上爬（+28/→+25/→+25），而且**後面還沒建 ctx#2**。
- ⇒ **設計目標 ≥120 MiB**，margin 才夠。

## 3. ★ 方案 A（推薦先跑，env-only，零 build）

### 3.1 槓桿

`LLAMA_EXPERT_CACHE_LAYER_CAPS`（`llama-expert-cache.h:631` `cgc_layer_cap()`，語法 `start-end:cap;…`，
未覆蓋的層保留 `def`）。它同時被 loader（`llama-model-loader.cpp:1496` 決定 expert tensor 的 `ne[2]`）
和 cache（`llama-expert-cache.cpp:3939` 決定 slot 向量）讀 ⇒ 設了就真的會讓 Metal buffer 變小。

| `40-40:N` | blk.40 resident | 騰出 |
|---|---:|---:|
| 143（現狀）| 155.34 | 0 |
| 64 | 69.50 | **85.8** |
| 32 | 34.75 | **120.6** |
| 16 | 17.38 | **138.0** |
| 8（clamp 下限）| 8.69 | **146.7** |

### 3.2 為什麼它不傷正確性

- **target 完全不跑 layer 40**：兩趟 `ctx#1` 都只建 il=0..39，reserve 一字不差（2530.72）。
- **draft 只產生候選 token，輸出由 target verify 決定** ⇒ layer 40 的命中率只影響速度，不影響 logits。
  （⚠ 這條是機制推論，仍要用 M1/M2/M3 實測確認，不能只靠推。）

### 3.3 ⚠ 單靠 A 不夠 —— 還有第二關

**ON 死在 PP 時 draft ctx 根本還沒建立**（log 裡 `expert_index built` 只 1 次、沒有第二個 model load、
`CGC-DRAFT-*` 見證兩條都沒印）。而一旦活過 PP，gen 階段會建 draft ctx，
`ACCEPT_LEVERS §11` 的 census 說那要 **KV 512 MiB + compute 493 MiB = 1005 MiB**。

⇒ 方案 A 只讓你過第一關。**必須同時開那兩道形狀閘**：

- `CGC_DRAFT_CTX_ALIGN=1`（`speculative.cpp:2573`）⇒ `cparams.n_ctx = llama_n_ctx(ctx_tgt)`，KV 512 → ~5 MiB
- `CGC_DRAFT_SMALL_BATCH=1`（`speculative.cpp:2608`）⇒ `n_ubatch → n_max+1 = 4`，graph reserve 掉到幾 MiB

⚠ 這兩個閘 **`SMALL_BATCH` 的條件含 `spec_mtp`**，所以一定要配 `--spec-type draft-mtp`；
而且它們在 repo 裡**從來沒有被實跑驗證過**（`grep -rl "CGC-DRAFT-SMALL-BATCH" Backup/ docs/` = 0 命中）。
判據是見證行：
- `CGC-DRAFT-CTX-ALIGN: n_ctx_req=… -> n_ctx_draft=… (target ctx=…)`
- `CGC-DRAFT-SMALL-BATCH: applied path=mtp …`

**見證行沒印 ⇒ 閘沒生效 ⇒ 那趟不能拿來結論。**

### 3.4 指令

```sh
python3 scripts/check/harness.py bench \
  --arm "prod-new:!LLAMA_EXPERT_CACHE_LAYER_CAPS=40-40:16;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1" \
  --spec-type draft-mtp \
  --json /tmp/mtp_on_fix_a.json
```

- `LAYER_CAPS` 不在 `_EXPERIMENT_KNOBS`（`harness.py:804`）⇒ 用 `!` 顯式宣告，會進 `base_check.overrides`。
- `--spec-type draft-mtp` 是**唯一**能讓 `spec_mtp=true` 的入口（`CGC_SERVER_MTP` 在 bench 路徑是 inert 的，
  見 `MTP_ON_RC6_ROOT_CAUSE_2026-09-28.md` §4）。
- 若仍 `rc=-6` ⇒ 降到 `40-40:8`（多騰 8.7 MiB）或疊加方案 B。

## 4. 方案 B（A 不夠時疊加，會改 prefill 路徑）

`CGC_GATHER_SLAB_CAP`（`llama-context.cpp:685`，預設 64，生產設 256）控制 PREFILL_STREAM 的 slab。
現在 slab = 356 MiB。降到 64 ⇒ ~89 MiB，**騰 267 MiB**。

⛔ 但有個陷阱（`llama-context.cpp:722-730`）：**`cap < n_expert(256)` 時 `cgc_prefill_stream_enabled()`
會拒絕並關掉 prefill stream，退回 pool 路徑**（「slower, but the numbers stay right」）。
⇒ B 不是「同一條路省記憶體」，是**換一條路**。OFF/ON 兩臂都要同設才能比，且 pp t/s 不可與現有基線比。

## 5. 方案 C（真正的治本，code change，最小的一刀）

**讓 MTP block（blk.40）的 expert cap 在 `load_mtp` 時自動降為獨立的小值**，不必靠使用者設 env。

理由（全是已證的）：
- blk.40 在 target PP 期間**完全用不到**（target 只建 il=0..39，reserve 兩趟相同）。
- 它現在卻跟 trunk 一樣拿到 uniform cap=143，白白吃掉 155.34 MiB，正好是壓垮 PP 的那一段。
- trunk 的 cap 是 `budget / (max_layer × per_slot)`，**`max_layer` 在 ON 時從 40 變 41**，
  也就是說 MTP 還順帶把 trunk 的分母放大了 —— 這層效應本輪**沒量，待查**。

改法（約 10 行，`llama-model-loader.cpp` + `llama-expert-cache.h`）：
對 `il >= hparams.n_layer()` 的層，`cgc_layer_cap()` 回傳
`min(def, CGC_MTP_BLOCK_CAP 或預設 32)`，並印一行見證。

代價：動 `src/`（與另一條 session 共用），要 build，要重跑 OFF/ON 同 build 對照。
⛔ 沒有 build 之前不要對外引用任何 C 的數字。

## 6. 方案 D（最治本，也最貴）：延後 materialize

讓 blk.40 的權重**在 draft ctx 建立時才進 Metal**，target PP 期間完全不存在。
理論上能把 ON 的 PP 峰值壓到 = OFF。

需要 loader 支援 lazy / 二次 load，而 `load_mtp` 目前是一次性的 `TENSOR_SKIP vs 0` 二選一
（`models/qwen35moe.cpp:45`）。⇒ 這是新增機制，不是調參。**本輪不建議當第一步。**

## 7. ⛔ 不要用的兩條捷徑

| 捷徑 | 為什麼不行 |
|---|---|
| `CGC_METAL_FAIL_STOP=0` | 只是不 abort，回傳 stale output。`il=39` 的 `ids` 已經是 `-67044352`，輸出是 garbage。 |
| 降 `-p` / `-ub`（改 cell） | 能活，但 cell 不再可比，**不可引用為成績**；只適合作為「逐層累積工作集」的機制驗證。 |

## 8. 驗證序列（按成本排序，每步都有判據）

| # | 做什麼 | 判據 | 成本 |
|---|---|---|---|
| 1 | 方案 A（`40-40:16` + 兩道 draft 閘）ON 臂 | 存活出 JSON？兩條 `CGC-DRAFT-*` 見證有沒有印？ | ~10 min |
| 2 | 同 build OFF 臂（原 `prod-new`，不帶 spec-type） | 配對對照；`attribution.verdict` 是不是 swap | ~10 min |
| 3 | 若 1 失敗 ⇒ `40-40:8` | 再騰 8.7 MiB | ~10 min |
| 4 | 若 3 失敗 ⇒ 疊加方案 B（兩臂同設 `CGC_GATHER_SLAB_CAP=64`） | 見 `CGC-PREFILL-STREAM` 消失、退回 pool 路徑 | ~20 min |
| 5 | 方案 C（code） | 新見證行 + 同 build OFF/ON 對照 | build + 20 min |

⚠ 跑之前兩件事（既有紀律）：`lsof -nP -iTCP:8080 -sTCP:LISTEN` 與
`pgrep -fl 'run_ids_dst_capture|decode_sweep|llama-server'` 都要空；**檢查與動作要在同一個分支裡**。

## 9. 限度

- 方案 A 的騰出量是**推算式**（`278 × N/256`），只有 cap=143 → 177.8 MiB 這**一個**實測點撐著
  ⇒ 第 1 步跑完要回頭對一次實測 rss 差，不對就要修這個模型。
- 「draft 只影響速度不影響 logits」是機制推論，**必須**用 M1/M2/M3 實跑確認。
- 兩個 `CGC_DRAFT_*` 閘的效果**沒有任何實跑支撐**，§3.3 的 1005 MiB 是 census 值不是本 cell 實測。
- 就算全過，ON 也只是「活下來」；**MTP 的增益仍然要 ≥2 reps 存活樣本才可判**
  （`mtp_promotion_gate.py` v2 的配對設計要求）。
- 【未跑】本輪無量測、無 build、`src/` 一行未改。

## 10. artifact

- `docs/MTP_ON_RC6_ROOT_CAUSE_2026-09-28.md`（§8 實跑、§9 同 build OFF 對照、§10 B 軸結案）
- `/tmp/harness_bench/llama_bench_prod-new_CGC_DRAFT_CTX_ALIGN_1_CGC_DRAFT_SMALL_BATCH_1_….stderr.log`（ON，03:32）
- `/tmp/harness_bench/llama_bench_prod-new_p2048_n128_d512_r3.stderr.log`（OFF，03:52）
- `scripts/check/gguf_tensor_census.py`
