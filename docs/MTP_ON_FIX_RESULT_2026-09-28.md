# MTP on 修復 — 第 1 步實跑結果（2026-09-28 11:50–12:05）

> 範圍：方案 A 首跑 + 配對對照 + 兩個基礎設施 bug 的定位。**本輪未 build、未改 `src/`、未動任何 shared 檔。**
> 證據分級：**【實跑】**＝本輪產物／stderr；**【碼證】**＝可重現的程式行為；**【推算】**＝公式推導。

## 0. 一句話

**MTP on 修好了 —— 它在權威 cell 上不再 `rc=-6`，首次跑出完整 3 reps。**
但真正的根因**不是**「差 47 MiB」，而是**旋鈕從來沒有送達引擎**：我上一份方案 A 指令用了
`!LLAMA_EXPERT_CACHE_LAYER_CAPS=40-40:16`，而 `!` 前綴在 bench 路徑會讓這個環境變數**整顆被丟棄**
（見 §3）。改用正確旋鈕名 `CGC_SERVER_LAYER_CAPS` 且**不帶 `!`** 之後，第一次就活了。

壞消息：活下來之後 **ON 比 OFF 慢**（tg 中位 11.53 → 8.26~9.47），方向明確為負。

## 1.【實跑】三臂結果（**同 build `e5d1c0f14`**，權威 cell 一致）

| 臂 | 時間 | pp 中位 | tg 中位 | tg 逐 rep | hit% | 盒況污染 |
|---|---|---:|---:|---|---:|---|
| **OFF**（`prod-new`，無 spec-type） | 11:46 | **273.43** | **11.53** | 11.95 / 11.52 / 11.53 | 96.0 | swap（min_free 15 MiB） |
| **ON `40-40:16`** + 兩道 draft 閘 | 11:55 | 259.23 | **8.26** | 0.45 / 8.26 / 9.49 | 92.0 | swap（min_free 14 MiB） |
| **ON `40-40:64`** + 兩道 draft 閘 | 12:03 | 158.97 | **9.47** | 9.58 / 9.47 / 7.99 | 92.8 | **thermal HEAVY** + swap |

讀法（務必）：

- `attribution.verdict` 三臂**全部不是 clean**：OFF/ON-cap16 是 `swap`，ON-cap64 是 `both`
  （thermal 最差 HEAVY）。⇒ **「ON 比 OFF 慢多少」不可定價**，只能說方向明顯為負（−18%~−28%）。
- cap16 的 rep1 = **0.45 t/s** 是單發暴走（那一分鐘 `pages_free` 掉到 62 MiB）；
  用中位數 8.26 而不是均值 6.07。
- cap64 的 pp 158.97 明顯被 HEAVY 拉低，**不可**與 cap16 的 259.23 直接比。
- 結論只有一句：**MTP on 在這個盒子上目前是負增益，與既有結論（S=0.89~0.91）同向，且沒有任何
  證據支持「ON 有增益」。**

## 2.【實跑】正確的修復指令（這一行就是「怎麼修」）

```sh
/opt/homebrew/bin/python3 scripts/check/harness.py bench \
  --arm 'prod-new:CGC_SERVER_LAYER_CAPS=40-40:16;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1' \
  --spec-type draft-mtp \
  --charter scripts/check/charters/exp-mtp-on-fix-a.yaml \
  --json /tmp/mtp_on_fix_a3.json
```

三個要點，**缺一不可**：

1. 旋鈕名是 **`CGC_SERVER_LAYER_CAPS`**，不是 `LLAMA_EXPERT_CACHE_LAYER_CAPS`。
   `run_server.sh` 的 `SERVER_ENV` 是 **allowlist**（`run_server.sh:2462` 的註解明寫
   「an unlisted CGC_* is dropped silently」）；`LLAMA_EXPERT_CACHE_LAYER_CAPS` 只能由
   `CGC_SERVER_LAYER_CAPS` 導出（`run_server.sh:2470`）。產品 json 現在看得到
   `env.LLAMA_EXPERT_CACHE_LAYER_CAPS = 40-40:16` ⇒ 證明有送到。
2. **不要加 `!`**（見 §3）。加了就等於沒設。
3. `CGC_DRAFT_CTX_ALIGN` / `CGC_DRAFT_SMALL_BATCH` 在 `run_server.sh:1531` 的直通清單裡
   ⇒ 直接給值即可，它們是「過第二關（draft ctx 形狀）」用的。

### 2.1 見證行**首次**真的印出來了（這兩道閘在此之前全 repo 0 命中）

```
llama_expert_cache: LAYER_CAPS per-layer caps: total 5736 slots (avg 139.9/layer, min 16/layer)
CGC-DRAFT-CTX-ALIGN: applied path=mtp n_ctx_req=0 -> n_ctx_draft=768 (target ctx=768)
CGC-DRAFT-SMALL-BATCH: applied path=mtp n_batch_req=2048/512 -> n_batch_draft=4 (n_max=3)
```

cap=64 那趟也一樣（`total 5784, min 64/layer`）⇒ 兩個 cap 都讓 PP 過關。

## 3.【碼證】⚠ 兩個基礎設施 bug（比 MTP 本身更值得處理）

### 3.1 ★ `!` 前綴在 bench 路徑是**靜默 no-op**

`scripts/check/llama_bench_matrix.py:782`：

```python
extra = dict(kv.split("=", 1) for kv in envs.split(";") if "=" in kv)
```

**沒有剝 `!`**。於是 `--arm 'prod-new:!K=V'` 餵給 `run_server.sh` 的環境變數名叫 **`!K`**，
不在 allowlist ⇒ 丟棄。harness 自己的 `_parse_arm`（`harness.py:841`）有剝，但那一份**只用於
base gate 與列印**，不進引擎。

實測（resolve-only，不佔 GPU）：

```
SPEC: prod-new:!CGC_GATHER_SLAB_CAP=64
  extra(餵給 run_server.sh 的鍵): ['!CGC_GATHER_SLAB_CAP']
  解析回來: {'CGC_GATHER_SLAB_CAP': '256'}      ← 仍是預設，override 蒸發
SPEC: prod-new:CGC_GATHER_SLAB_CAP=64
  extra(餵給 run_server.sh 的鍵): ['CGC_GATHER_SLAB_CAP']
  解析回來: {'CGC_GATHER_SLAB_CAP': '64'}       ← 生效
```

含義：**過去任何用 `--arm 'prod-new:!K=V'` 宣告的臂，引擎跑的都是預設值。**
base gate 會「通過」（因為它看的是剝過 `!` 的那份），所以**沒有任何紅燈會亮**。
⚠ 本輪**沒有修它**：修了會讓所有既有的 `!` 臂突然真的生效，等於回溯性地改變既往結論，
且 `scripts/check/` 是共用資產 —— 需要 owner 決定（見 §6）。

### 3.2 背景執行會讓 harness 找不到 PyYAML

前景跑 `/opt/homebrew/bin/python3` 正常；同一指令在背景跑會在 150 ms 內
`需要 PyYAML 讀 charter`。⇒ 跑量測時**不要丟背景**。

## 4. 與既有結論的關係

- §「差 47 MiB」那份帳（ON 比 OFF 高 ~180 MiB）**沒有被推翻**：cap 從 143 → 16 確實騰出了
  PP 需要的空間，`min 16/layer` 見證行可證。被推翻的是「我上一趟已經試過騰記憶體」
  —— 那一趟什麼都沒試。
- **`40-40:256`（生產預設）仍然不該動**：本輪只是證明「砍到 16/64 可以讓 ON 活」，
  而活的代價是 hit% 96.0 → 92.0/92.8。這仍然是**拿 draft 命中率換記憶體**，是帳單不是 waste。

## 5. 還沒做的（按重要性）

| # | 項目 | 為什麼 |
|---|---|---|
| 1 | **M1/M2/M3 bit-identical** | 只做了機制推論（draft 只給候選、輸出由 target verify 定）。**沒驗就是沒修完。** |
| 2 | 乾淨窗口的配對重跑 | 三臂 verdict 全 `swap`/`both` ⇒ 現在這個 −18~−28% 不可引用 |
| 3 | `40-40:32` / `:8` 的存活邊界 | 只為了畫出 margin 曲線，對速度無幫助 |

## 6. 需要 owner 決定的一件事

`llama_bench_matrix.py:782` 要不要剝 `!`？

- 修：既往所有 `!` 臂的結論要重跑（可能是大批）。
- 不修：所有 `!` 臂繼續靜默無效，base gate 繼續假裝通過。
- 折衷：保留 `!` 語意（宣告用），但**在 resolve 時剝掉再餵**，並在產物記一筆
  `bang_stripped`，讓舊產物可辨識。

本輪不擅自改 —— 上一輪改 shared 資產已被判定為越權。

## 7. artifact

- `scripts/check/charters/exp-mtp-on-fix-a.yaml`（本輪新建）
- `/tmp/mtp_on_fix_a3.json`（ON cap16）、`/tmp/mtp_on_fix_a64.json`（ON cap64）、`/tmp/probe_x.json`（OFF 對照）
- `/tmp/harness_bench/llama_bench_prod-new_CGC_SERVER_LAYER_CAPS_40-40_16_….stderr.log`（見證行）
- `docs/MTP_ON_FIX_PLAN_2026-09-28.md`（方案，§1 的帳仍成立）
- `docs/MTP_ON_RC6_ROOT_CAUSE_2026-09-28.md`（根因）
