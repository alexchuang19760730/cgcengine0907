# `CGC_CB_N_MAIN=32` 在 09-28 13:55 build 上的複測 —— **未證實（建議不改預設值）**

> 立項卡 `scripts/check/charters/e-cbnmain32-p1build.yaml`（owner: lineA，2026-09-28）
> 產物 `Backup/phase_decomp/cbnmain_p1build/`（`abba1..4.*`、`ref_cb64_long_20260928.jsonl`）
> 前置問題（數值中立）**已確認**：D5 長探針 **M1/M2/M3 = 1045/1045**（見 §1）

---

## §1 前置：`n_main` 對數值中立 —— 在新 build 上**重新確認**（✅ PASS）

09-26 那次是在 `libllama.0.dylib=312ba56a00a14199` 上測的；13:55 這顆是新的
（`6375e39296c349e3`，含別人 4 處改動＋本線的 `CGC_GRAPH_NAMES`）⇒ 前提必須重驗。

設計與 09-26 相同（control 寫 ref、treatment 比對）：

```
m123_oracle_gate.py --profile prod25 --probe-max-tokens 400
  control  : --tag cb64_long_p1build  --write-ref ref_cb64_long_20260928.jsonl
  treatment: --tag cb32_long_p1build  --ref <ref> --allow-incomparable --env CGC_CB_N_MAIN=32
```

| 量 | 值 |
|---|---|
| `n_compared` / coverage | **1045** / **100.0%** |
| M1 numeric identity | **1045/1045（100%）** |
| M2 decision agreement | **1045/1045（100%）** |
| M3 top-k set agreement | **1045/1045（100%）** |
| (aux) full_fnv1a64 | 1045/1045 |
| CROSS-TAB | `M1 same & M2 same` = 1045；其餘三格 **全 0** |
| build | `libllama.0.dylib=6375e39296c349e3`（兩臂同） |

⛔ treatment 那趟被閘門戳 `INVALID COMPARISON`（1 diff）—— **那個 diff 正是被測變數**
`CGC_CB_N_MAIN`，`--allow-incomparable` 的說明書就是為此用途提供的。行級證據
（1045/1045、`only_A=0`、cross-tab 三格為 0）與該戳記無關。
（control 那趟 `rc=2` 是 `--write-ref` 沒有可比 ref 造成的假象，不影響；ref 1045 行已寫出。）

⇒ **`n_main` 不改變數值** ⇒ 它「可交付的前提」在新 build 上**仍然成立**。

---

## §2 速度 ABBA —— 結果

cell = `prod-new p2048 n128 d512 r3`（唯一對外門 `harness.py bench`），順序 ctl64a → nm32a → nm32b → ctl64b，
趟間冷卻 40 s。⚠ 14:58 那批（`thermal_worst=HEAVY`、tg 9.58）**已整批作廢**並重跑。

| 趟 | `n_main` | tg（3 reps） | 中位 | ± | pp | thermal HEAVY% | swap 成長 | `attribution.verdict` |
|---|---|---|---|---|---|---|---|---|
| abba1_ctl64a | 64（預設） | 11.63 / 10.17 / 9.46 | 10.17 | 1.11 | 216.49 | 48%（84/174） | **+1403.63 MiB** | `both` |
| abba2_nm32a | 32 | 11.75 / 8.72 / 10.00 | 10.00 | 1.52 | 228.03 | 60%（100/168） | +443.24 MiB | `both` |
| abba3_nm32b | 32 | 11.61 / 12.13 / 11.98 | **11.98** | 0.27 | 262.17 | 53%（81/153） | +220.82 MiB | `both` |
| abba4_ctl64b | 64（預設） | 11.30 / 10.10 / 8.41 | 10.10 | 1.45 | 249.99 | 61%（100/164） | **−959.38 MiB** | `thermal` |

**配對（中位）符號不一致**：對 1 = **−1.7%**，對 2 = **+18.6%** ⇒ 兩對沒有共同的效應方向。
四趟 `thermal.worst` 全是 **`HEAVY`**（HEAVY 採樣佔 48–61%）⇒ **全窗不乾淨**。
（`attribution.verdict` 前三趟是 `both`＝thermal＋swap 成長；第四趟是 `thermal`，因為它那一趟
swap 是**淨回收 −959.38 MiB** —— 「窗不乾淨」的結論由 thermal 那一半獨立成立，不依賴 swap 的符號。）

---

## §3 決定性的一刀：看 rep1，不要看中位

四趟的 **rep1 幾乎相同**（11.63 / 11.75 / 11.61 / 11.30），而 rep2–rep3 發散：

- 三趟（ctl64a、nm32a、ctl64b）的 reps 呈**單調遞減** ⇒ carry-over／熱累積的簽名；
- **只有 nm32b 不遞減**（11.61 → 12.13 → 11.98，且它 swap 成長最小 221 MiB、HEAVY 採樣最少 81）
  ⇒ 它那一趟拿到的是**較好的窗**，不是「`n_main=32` 的效應」。

| 判據 | 64 | 32 | 差 |
|---|---|---|---|
| **rep1 平均（最冷、未受 carry-over）** | **11.468** | **11.681** | **+1.86%** |
| 3-rep 中位（配對 1／2） | 10.17 / 10.10 | 10.00 / 11.98 | −1.7% ／ +18.6% |

⇒ **在最乾淨、可比的那一層（rep1）上，32 只比 64 快 1.9%**：
低於立項卡門檻 3%，也遠低於最小可辨差 **8.5%**（走廊 1σ=3.0%）。
⇒ 中位看到的 +8.4%（均值差）**完全由 nm32b 一趟的好窗驅動**，不是 `n_main` 效應。

---

## §4 判定

| 立項卡項目 | 結果 |
|---|---|
| success：兩對配對增益 ≥3% 且 D5 1045/1045 | ❌ **不成立**（符號不一致；rep1 僅 +1.9%） |
| falsify：配對增益 <3%（落在噪聲內）⇒ 假設否證 | ✅ **觸發** |
| D5 數值中立 | ✅ **1045/1045**（§1） |

⇒ **判定：09-26 的 `+12.8%` 未能在這顆 build 上複現。**本輪最強的證據（rep1）指向**無增益**。

⛔ **尚未定死**：本輪四趟全是髒窗（HEAVY 48–61%），所以「未複現」與「窗太髒量不出來」**目前無法分離**。
要到乾淨窗（全程 `thermal=NOMINAL`、swap 成長近 0）重跑一次 ABBA 才能判死。

## §5 處置

1. **不改 `scripts/run_server.sh` 的預設值**（`CGC_CB_N_MAIN` 維持 unset ⇒ upstream 行為）。
   這是本輪唯一會動到別線的動作，缺乏支持證據時不做。
2. `n_main` 對數值中立這條（§1）**可以引用** —— 它是本輪確定下來的成果。
3. 若要定案：在乾淨窗重跑同一組 ABBA；若 rep1 差異仍 <3% ⇒ 按 `on_fail` 登記判死、
   從待辦移除（⚠ 記憶教訓：「排在待辦最前」≠「還沒做」，引用前先看有無量測欄）。
