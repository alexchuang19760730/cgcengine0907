# thermal 標籤對 prod-new 的 tg 有沒有影響：受控實驗（2026-09-28，**未完成**）

**問題**：權威預設 cell 自帶一個 2048-token prefill，它讓每次啟動的 tg 窗落在 HEAVY。
契約 §5.1 說「thermal HEAVY ⇒ unreliable」，所以這條口徑的 decode row 在制度上拿不到可引用值。
要嘛挑冷窗跑（A），要嘛給這條 cell 的 tg row 豁免（B）。**B 需要一個獨立證據：thermal 標籤
對 tg 值沒有系統影響。** 這份文件是那個實驗的第一次嘗試，結果是**設計本身不成立**。

## 1. 加熱器標定：CPU 負載**不能**驅動這個標籤（決定性否定）

```
8 個 `yes` burner 跑滿 8 核，取樣 `com.apple.system.thermalpressurelevel`：
   +  0.0s level=1 MODERATE
   +  8.0s level=0 NOMINAL     ← 起點就被壓回 NOMINAL
   +  8.0s … +112.1s level=0 NOMINAL（全程不變）
   120s TIMEOUT，沒到 HEAVY；停掉 burner 後仍是 NOMINAL
```

⇒ **「人工加熱」這一臂不能用 CPU 工作做出來。** 這個 notify(3) 鍵反映的不是 CPU 熱。
唯一觀察到能把它推上去的是 **GPU 工作**（llama 的 prefill/decode），也就是說：
**權威 cell 自己就是那個加熱器**——每支臂都從 NOMINAL 起跑，然後在 tg 窗內變成 HEAVY。

⇒ 實驗設計必須改成：**H 臂＝GPU 加熱器**（例如 `--prompt 8192 --gen 0` 的 prefill-only 臂），
**C 臂＝真正涼的盒子**（09-25 那三次 00:36 / 02:05 / 02:35 是全程 NOMINAL，所以它可達，
但只在盒子涼的時段）。CPU burner 這條路已排除，不要再試。

## 2. 意外得到的 H 臂：一支「起跑 NOMINAL、tg 窗全熱」的臂

加熱器失敗之後，那支臂等於跑在**它宣稱的 NOMINAL** 狀態（`box_gate.thermal_gate=False`，
因為用了 `--no-thermal-gate`；但起跑讀值確實是 NOMINAL）。它給出今天最低的 tg：

```
H1   pp 287.81 t/s   tg 8.95 t/s
     pp 窗 21.4s {NOMINAL:34}          ← 前處理乾淨
     tg 窗 27.4s {MODERATE:3, HEAVY:52} ← decode 窗 100% 熱
     attribution: both（thermal=HEAVY, **swap 成長 932 MiB**）
```

**這個 8.95 不是乾淨的 thermal 效應**：它同時帶著 932 MiB 的 swap 成長（同日其他臂是
74–286 MiB）。也就是說它至少混了記憶體壓力，而那是另一條已知的通道。

## 3. 目前 n=2（**不是證據，只是形狀**）

```
12.084  r5    tg 窗 81% 熱   swap 成長  74.6 MiB   ← 最高
 8.952  H1    tg 窗 100% 熱  swap 成長 932.0 MiB   ← 最低
```

**兩個都熱，兩個都在分布的兩端。thermal 標籤沒有把它們排序。** 但 n=2、其中一支帶
混淆（swap），所以這**不能**用來支持豁免，也不能用來否證；它只說明「標籤不是決定性的」。

順帶一個儀器性質：`dur_s = avg_ns × n_reps`，所以**慢的臂窗更長**（H1 27.4s vs r5 15.9s），
HEAVY 的樣本數因而與結果相關。這在解讀比例時要記得。

## 4. 修正後的設計（下一步，不要照舊的跑）

1. **H 臂用 GPU 加熱器**：`harness bench --arm prod-new --prompt 8192 --gen 0`（prefill-only）
   加熱到 HEAVY，**停掉它**，再跑被測的臂（`--no-thermal-gate`）。加熱器與被測臂共用引擎，
   加熱方式與真實情況同型。
2. **C 臂的門檻是「盒子涼」而不是「我先 idle 一下」**：判定要用**被測臂自己的 tg 窗**
   （`thermal_windows.tg.hist`），不是起跑讀值。若 C 臂的 tg 窗仍然 HEAVY ⇒ 那一對沒有對比，
   該對作廢而不是當成 C。
3. **控記憶體混淆**：H1 的 swap 成長 932 MiB 說明 swap 是一條會同時動的通道。每對都要記
   swap 成長並在配對裡檢查（既有 `memory_pressure` 已經算）。
4. **樣本數**：同日同口徑單次散布 4–27%（今天 6 支：11.607 / 11.625 / 11.636 / 11.664 /
   12.084 / 8.952）。要在 α=0.05、paired 下偵測 5% 的 thermal 效應，需要 ~10 對以上；
   成本主要卡在「H 要先加熱、C 要等盒子涼」。
5. **若 C 不可得**（今晚的盒子就是這樣），替代設計是**劑量—反應迴歸**：跑 N 支臂，把 tg 對
   **tg 窗的 HEAVY 比例**迴歸。它不需要人工造出 C，但需要 N 大（≥20）且必須同時控 swap。

## 5. 產物

```
/tmp/heat.py                        加熱器（CPU 版；已證明無效，保留作為否定證據）
/tmp/heatctl/H1.json                那支臂的產物（含 thermal_windows）
/tmp/heatctl_H1.log                 harness stdout
/tmp/anchor_prodnew/r5.json         tg 窗 81% 熱、tg 12.084 的那支
```
