# pool 常住足跡：三條路全封 —— 線 I 記憶體預算結案（0 build）

日期：2026-09-28　授權：跨線處理線 I（expert cache／cb）
立項卡：`scripts/check/charters/e-softpool-residency-2026-09-28.yaml`（跑之前預註冊，事後未改）
實測：`Backup/phase_decomp/softpool/soft32.json` ＋ 既有 3 份控制 json

---

## §0 一句話

**(B) 那條路（pool 改 file-backed）不存在**——`CGC_MMAP_POOL_FIX` 不是開關。
而替代它的 soft pool 路，實測**常住足跡完全沒降**（wired 12402 vs 控制 12364 MB，+0.3%）
⇒ 三條記憶體側的路全部封死。**16 GB 上沒有記憶體側槓桿，`cb` 是結構成本。**

---

## §1 先更正：`CGC_MMAP_POOL_FIX` 到底是什麼（我上一輪說錯了）

我上一輪建議「確認它在不在 binary 裡，在就開一趟對照」。查完的結果是：

| 我以為 | 其實 |
|---|---|
| 它是一個 `CGC_*` env 儀器，`strings` 找得到 | **它在任何 dylib／執行檔裡都是 0 命中**——它是 `run_server.sh:1024` 的 **shell 變數** |
| 找到了就能當開關開一趟 A/B | 它是**探測變數**，由 `strings -a libggml-metal*.dylib \| grep -c '_Pool'` 算出（`:1027`） |
| 它跟 pool 的換頁行為有關 | 它只服務於 `load_mode=mmap` 那條路（`:1146-1158`），判「這個 binary 有沒有 `_Pool` buft 修復」 |

**可檢查的事實**：`strings -a libggml-metal.0.dylib | grep -c '_Pool'` ⇒ **1**（修復存在）。

**但它對交付 cell 不適用**，而且源碼註釋把話寫死了：

> `run_server.sh:1140-1142`：只在 use_mmap 時選它（`llama-model-loader.cpp: select_pool_buft`），
> **所以 `load_mode=none` 路徑的 buft 選擇與以前逐位元相同。**

交付 cell 就是 `load_mode=none`（16 GB 上唯一實測存活的載入模式）⇒ **這條路在我們的 cell 上是 no-op。**

### 而且它早就被量過、被判「不是槓桿」

`run_server.sh:1089` 原文：

> mmap（**不是**槓桿，不要再試）：這個 binary 含 pool/mmap 修復，所以載入不會 SIGBUS，
> 但修好之後它在 **4096/5120/6144 全部不存活（0/3）**，而且工作集警告只在 mmap 出現
> ⇒ **它把 GPU 工作集撐大**。

⇒ (B) 不成立，**不需要也不應該為它開一趟**。我上一輪的建議作廢。

---

## §2 替代路：soft pool 降可駐留槽位（本輪實測）

### 2.1 為什麼值得問

上一輪（`docs/CGC_POOL_WIRED_BUDGET_2026-09-28.md`）算出：
`wired 峰值 12.4 GB（模型權重 Metal 駐留）＋ pool 8 GiB ＝ 20.4 GiB vs 實體 16 GiB`
⇒ 必有 ~4.4 GiB pool 頁在 swap 上。

那輪結論「縮 pool 不行」（真曲線 8G 12.06 → 6G 10.48 → 4G 7.67 t/s）。
但 **soft pool 是另一件事**：pool 仍配置 8 GiB、`n_slots` 仍 143，只是把槽位 64..142 標為
spare「currently unused」⇒ 若那些頁真的永不觸及，常住足跡應從 8 GiB 降到 ~3.6 GiB，
**而不改變 pool 的宣告容量**。這是第一個「砍掉那 4.4 GiB 卻不縮 pool」的候選。

### 2.2 旋鈕真的生效（見證行）

```
CGC Soft Pool init: L0=32 L1=32 (n_slots=143, partition active)
pool_cap_slots=143
```

（對照：交付 cell 當前被 `run_server.sh:2294-2302` 強制設為 `0/0`，
印 `L0=0 L1=0 (partition disabled)`。⇒ 本趟確實是「開著」的那一面，不是靜默 no-op。）
resolve-only 亦確認：`L0=32 L1=32` vs base `0/0`，`!` 由 `_parse_arm` 剝掉後才餵給
`matrix.resolve`（`harness.py:858`），故 §1.12 的「`!K` 被 allowlist 丟掉」陷阱不適用於這條路徑。

### 2.3 結果：常住足跡**完全沒降**

| 趟 | tg reps | 中位 | rep1 | pageins 增量 | max_wired (MB) | swap 成長 | hit% |
|---|---|---|---|---|---|---|---|
| off_a（控制） | 10.87 / 10.91 / 10.22 | 10.87 | 10.87 | 1,621,605 | 12,364 | +4418 | 96.2 |
| on_k1（控制） | 10.74 / 9.20 / 15.28 | 10.74 | 10.74 | 1,674,011 | 12,423 | +3592 | 91.4 |
| off_b（控制） | 4.09 / 11.82 / 11.53 | 11.53 | 4.09 | 1,416,351 | 12,349 | +1832 | 96.2 |
| **soft32** | 10.17 / 9.13 / 8.38 | **9.13** | **10.17** | **1,774,912** | **12,402** | **+4846** | 96.2 |

三條獨立的記憶體指標，**沒有一條支持假設**：

1. **`max_wired` 12402 MB vs 控制 12349–12423** ⇒ 差異 **+0.3%**，在噪聲內 ⇒
   **常住足跡一點都沒降**。spare 槽位仍然被觸及（與 `prewarm_hot` 會把 143 槽填滿一致）。
2. **pageins 增量 1,774,912 ⇒ 比所有控制都高**（off_a 1.62M，+9.5%），方向相反。
3. **swap 成長 +4846 MiB 是四趟最大**。

⇒ **假設「spare 槽位永不觸及 ⇒ 常住足跡降 4.4 GiB」被證偽。**

### 2.4 速度面：按預註冊規則落在 UNDECIDED，但已無意義

- 預註冊 falsify 門檻 `tg ≤ 9.0`：中位 9.13、rep1 10.17 ⇒ **都沒過門檻** ⇒ 字面上 UNDECIDED。
- 但 `§1.18` 的判讀規則是**看 rep1 不看中位**：rep1 10.17 vs 控制 10.87／10.74
  ⇒ **−5.5%／−6.4%，落在 MDD 8.5% 內** ⇒ 速度差不可判。
- 中位 −16% 那部分是 carry-over 簽名（reps 單調遞減 10.17 → 9.13 → 8.38）。

**關鍵**：不管速度面判不判得出來，**記憶體面已經證偽** ⇒ soft pool 沒有 upside 去平衡任何速度代價
⇒ 沒有理由再投入。

### 2.5 hit% 不變（96.2% = 控制）

soft pool 被關掉的原始理由（`run_server.sh:2288-2293`）是命中率層面：
「placement 最多值 0.2pp（current 90.3% vs best-possible 90.5%）」。
本趟實測 hit% **96.2%，與 off_a／off_b 完全相同** ⇒ 那個理由在本 cell 上再次成立
⇒ **soft pool 在本 cell 上既不省記憶體、也不改命中率**。

---

## §3 三條路全封 —— 線 I 記憶體預算結案

| 路 | 旋鈕 | 結果 |
|---|---|---|
| ① 縮 pool | `CGC_SERVER_EXPERT_CACHE_BYTES` | 真曲線 8G **12.06** → 6G 10.48 → 4G 7.67 ⇒ 8G 最好，縮了就慢 |
| ② 降可駐留槽位 | `CGC_SOFT_POOL_L0/L1=32/32` | **本輪實測：wired 不變、pageins 反升、hit 不變** ⇒ 證偽 |
| ③ pool 改 file-backed | `CGC_MMAP_POOL_FIX` | **不是開關**（shell 探測變數）；且只對 `load_mode=mmap` 生效，交付 cell 是 `none`；歷史 0/3 |

⇒ **16 GB 上沒有記憶體側槓桿。**

對「這應用能不能用」的最終答案維持上一輪的結論，且現在有第三條獨立證據：
**能用，`cb` 是結構成本不是 bug；不要對使用者說「請升級到 32 GB」。**
（wired 12.4 GB 是模型權重的 Metal 駐留、不可換頁，且是所有路上最大的單項；
要動它得動量化／ngl，而兩者都碰護欄，須先過 bit-identical —— 那是另一張卡的事。）

---

## §4 本輪作廢／更正的條目（勿引用）

- 上一輪「下一步走 (B)，確認 `CGC_MMAP_POOL_FIX` 在不在、在就開一趟對照」⇒ **作廢**。
  它是探測變數不是開關，且對 `load_mode=none` 不適用，且歷史已判 0/3。
- 「`strings` 查 `CGC_MMAP_POOL_FIX`」是**假陰性陷阱**：它在 binary 裡本來就 0 命中。
  正確的查法是 `strings -a libggml-metal.0.dylib | grep -c '_Pool'`。

## §5 重現指令

```sh
/opt/homebrew/bin/python3 scripts/check/harness.py bench \
  --arm 'prod-new:!CGC_SOFT_POOL_L0=32;!CGC_SOFT_POOL_L1=32' \
  --warm-skip 64 --reps 3 --workdir /tmp/softpool_32 \
  --charter scripts/check/charters/e-softpool-residency-2026-09-28.yaml \
  --json Backup/phase_decomp/softpool/soft32.json
```

raw trace：`Backup/phase_decomp/softpool/`（被 gitignore）；
stderr 見證行：`/tmp/softpool_32/*.stderr.log`（`CGC Soft Pool init: L0=32 L1=32`）。

## §6 機械坑（本輪新踩）

- `harness.py bench` **沒有 `--dry-run`**（那是 `llama_bench_matrix.py` 的參數）⇒
  生效性檢查改用 skill §1.12 的 resolve-only 片段，毫秒級、不碰 GPU。
- charter gate 會驗 **`baseline.source` 檔案真的存在**：寫花括號展開
  `{off_a,on_k1,off_b}.json` 會 fail-closed 拒跑 ⇒ 必須指到單一真實檔案。
- base 已宣告的鍵（`CGC_SOFT_POOL_L0` base='0'）要覆寫必須加 `!`，否則 base gate 拒跑。
