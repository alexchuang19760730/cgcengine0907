# F_NOCACHE on the fill path: ABBA, 4 arms (2026-09-26)

**Pre-registered acceptance**（跑前寫死，見上一輪）：① 同 cell 的 `pageins` 與 `compressions` 增量同時下降；
② `tg` 的跨臂離散縮到 <5%。**結果：① 部分成立（pageins 成立且可重現；compressions 只有一半）；② 不成立。**

## 這是什麼

`CGC_FILL_NOCACHE=1` ⇒ 對 expert cache 的檔案 handle 下 `fcntl(F_NOCACHE)`（`llama-expert-cache.cpp`
`cgc_fill_nocache`，開檔處 `llama_expert_cache_init`）。它只改**位元來自哪裡**，不改讀哪些位元：同一個
`pread`、同一個 offset、同一個目的緩衝。預設關（unset = 與舊路徑逐位元相同），env 已進 `run_server.sh`
白名單，並有見證行 `CGC-FILL-NOCACHE: applied=1 failed=0 handles=1`。

## 四臂（同一 cell：prod-new、p2048/n128/d512/r3、warm-skip 64、單窗、ABBA 順序）

| # | 順序 | 旋鈕 | pp | **tg** | swapouts | compressions | pageins | 時長 |
|---:|---:|---|---:|---:|---:|---:|---:|---:|
| A1 | 1st | off | 288.63 ± 9.96 | **11.81 ± 0.30** | +3247 | +27556 | +21470 | 80 s |
| B1 | 2nd | on | 234.60 ± 23.52 | **10.99 ± 0.76** | +304 | +18252 | +10482 | 83 s |
| B2 | 3rd | on | 239.07 ± 1.32 | **11.76 ± 0.22** | +519 | +23529 | +11201 | 79 s |
| A2 | 4th | off | 181.67 ± 4.02 | **10.69 ± 0.30** | +71 | +21384 | +21133 | 92 s |

（`tg` 單位 t/s；計數器為該臂期間 `vm_stat` 增量，MiB。）

## 判定

| 判準 | 結果 | 證據 |
|---|---|---|
| ①a `pageins` 下降 | **成立、可重現** | on 10.5 / 11.2 GB vs off 21.5 / 21.1 GB ⇒ 兩對都**腰斬** |
| ①b `compressions` 下降 | **只有一半成立** | on 18.3 / 23.5 vs off 27.6 / 21.4 ⇒ 一對降、一對打平 |
| ② `tg` 離散 <5% | **不成立** | on 10.99–11.76（7.0%）；off 10.69–11.81（10.5%）；兩組水平重疊（中位 on 11.38 vs off 11.25 ⇒ +1.1%，在噪音內） |

## 讀法（三件必須一起說的事）

1. **機制被證實了一半**：我們的 fill 讀取確實是頁快取被灌滿的來源之一——把它們移出快取，`pageins` 就
   腰斬。這是本輪唯一**可重現**的物理效應。
2. **它不是速度的槓桿（至少不是單獨的）**：`tg` 兩組重疊、離散沒收斂，而四臂的壓縮機流量都在
   18–28 GB 之間沒有消失。所以那個 ±25% 的漂移還有另一個更大的來源，而它與我們的 fill 讀取無關。
3. **順序/漂移更兇**：A2（第 4 臂）的 pp 181.67 是四臂最低——同一顆 binary、同一組 env（旋鈕關），
   第 1 臂 288.63。⇒ 任何「單臂 vs 單臂」的比較在這個盒子上都會讀到漂移，不是讀到設定。

## 下一步（依證據，不依直覺）

- **找出那 18–28 GB 的壓縮機流量是誰的**：`vmmap -summary` 要對著**孫行程**（真正的 `llama-bench` pid），
  我這輪取到的是 matrix 那個 python 父行程（footprint 14.5 M），所以那張表無效。
- 在知道是誰之前，不再對 fill 路徑加任何最佳化：`pageins` 的效應已經被拿下，剩下的錢不在那裡。

## M1/M2/M3 閘門（同日補跑）：**自我配對通過**

旋鈕的命題是「不改變數字」，所以正確的閘門是**自我配對**：同一顆 build 跑兩次，一次 off、一次 on。

| 步驟 | 指令 | 結果 |
|---|---|---|
| 1（寫參考） | `m123_oracle_gate.py --tag fn_off --dump Backup/fn_oracle/off.json --write-ref Backup/fn_oracle/off.ref.json` | M1 **6/9**、M2 9/9（**對舊的釘死參考**；那 3 列是既有的 `ctx=MTP` 落差，不是本旋鈕——同一顆 build 的 off/on 兩臂在這一點上完全一致） |
| 2（自我配對） | 同指令 + `--ref Backup/fn_oracle/off.ref.json --env CGC_FILL_NOCACHE=1` | **M1 9/9、M2 9/9、M3 9/9、full_fnv1a64 9/9 ⇒ PASS** |

兩臂的 build 與 tree 相同（`libllama.0.dylib=1dfd4394f41c58cf`、`tree 828f4d1c2`），連池計數都逐項相同（`requests=7378 hit=89.2% evictions=685 / 686`）。

閘門端同時登錄了 `CGC_FILL_NOCACHE` 到 `DIAGNOSTIC_KEYS`（與 `CGC_SLOT_TABLE_GPU` 同一個先例與理由：這個鍵**的命題本身**就是「不改變數字」；不登錄的話每次開旋鈕都會被判 `INCOMPARABLE`，閘門就無法表達它唯一要測的那句話。若命題是假的，閘門會在 logits 上紅）。

**界線**：跑的是**短探針（9 筆）**，不是 884 筆的長探針；MTP-on/off 的配對也沒做。要拿來當生產預設之前，這兩格要補。

## 產物

`/tmp/fn_off`、`/tmp/fn_on`、`/tmp/fn_on2`、`/tmp/fn_off2`（各自的 `result.json`、`matrix_stdout.txt`、
引擎 stderr 含見證行）；引擎改動未 commit（`src/llama.cpp/src/llama-expert-cache.cpp`、`scripts/run_server.sh`），
`CGC_FILL_NOCACHE` 預設關 ⇒ 對既有讀數零影響。旋鈕的 ON 路徑尚未過 M1/M2/M3 閘門（本輪只證明它不改變
「讀什麼」，未證明逐位元；要用於生產前必須補跑）。
