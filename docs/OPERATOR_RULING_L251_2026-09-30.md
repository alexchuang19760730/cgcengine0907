# operator 裁定：不换盒子／不降模型（2026-09-30 19:1x）

## 裁定

- **不换盒子**（仍在这台 16 GB／M3 级盒子上）、**不降模型**（仍是 Nail-Qwen3.6-35B-A3B-MTP，
  IQ3_XXS/dense-IQ4X）⇒ **L25-1 的 SECONDARY（`load_mode=mmap`）出路关闭**。

## 事实链（今天量到的）

| 尝试 | 结果 |
|---|---|
| `delivery-mmap`（pool 8 GiB） | `Insufficient Memory`、command buffer fail-stop、**rc=−6** |
| `delivery-mmap-p6`（pool 6 GiB，本线新宣告的格子） | **同样 rc=−6**，与 8 GiB 同形态 |
| 再降 pool（4 GiB…） | ⛔ **不做**：pool 变小本身就会改变 miss 率 ⇒ B 臂不再只改 `load_mode` ⇒ 混淆，量到的不是要量的东西 |

⇒ 判詞：**同形不可测（unmeasurable），不是否证（not falsified）**。

## 这条裁定改变了什么

1. **L25-1 这一格**：以「**不可测**」结案（排除·不可测），**不是**「否证」。
   ⇒ 本格自己的否證句「SECONDARY 否證 ⇒ 25 定案判死」**因此不触发**——
   25 没有被这一格判死，它是「这一格量不出来」。
2. **25 的定案现在只挂在 S1**（修 `is_mem_shared` 的 draft 轮级掉线 ⇒ `k_eff` 1.59 → k）。
3. ⚠ **但 S1 也要过同一个盒子**：MTP-on 在这台盒子的存活线是**可回收 ≥7703 MB**，
   而 `budget_gate` 对交付 cell 三场**都判 OVERBUDGET**（赤字 ~8.3–8.4 GiB，最大项＝模型侧 Metal 驻留）。
   ⇒ **S1 就算修好，也可能在这台盒子上跑不完**（那会是"盒子判死"，不是"机制判死"）。
   ⇒ 建议 S1 的第一趟先带足迹闸（同 `l255_close.py --check` 的做法），不要直接跑大 k。

## 待贴：看板 L25-1 那一行的新文本（看板 YAML 目前在他线工作树，等它释放再贴）

```yaml
        runnable: "⛔ 不用再跑 — operator 2026-09-30 裁定<b>不換盒子、不降模型</b> ⇒ B 臂（mmap）在 pool 8 GiB 與 6 GiB <b>皆</b> Metal OOM（rc=−6），而再降 pool 會與 A 臂不同形（pool 本身改 miss ⇒ 混淆）⇒ <b>同形不可測</b>，出路已關閉。"
        badge: {text: "PRIMARY 否證；SECONDARY 不可測（出路關閉）", tone: dead}
        state: "結案（排除·不可測）（operator 2026-09-30：不換盒子／不降模型）——<b>PRIMARY 已否證</b>（ngram 的 E <b>0.984</b>）；SECONDARY＝<code>load_mode=mmap</code>：A 臂跑通（miss/step 中位 <b>6.0</b>、有 miss 層數中位 <b>6.0/39</b>、正規化 <b>1.92%</b>），B 臂在 pool <b>8 GiB 與 6 GiB 皆</b> <code>Insufficient Memory</code>、rc=<b>−6</b> ⇒ <b>同形不可測</b>。<br>⛔ <b>這不是否證</b> ⇒ 本格自己的否證句「SECONDARY 否證 ⇒ 25 定案判死」<b>不觸發</b>；25 的定案現在<b>只掛在 S1</b>（is_mem_shared 的 draft 輪級掉線），而 S1 也要過同一個盒子（MTP-on 存活 ≥7703 MB、budget_gate 現判 OVERBUDGET）。"
```

证据补记（`evidence_b` 加一行）：`Backup/l251_p6_2026-09-30/`（pool 6 GiB 的 OOM log）。

## 关键步骤清单的对应改动

- v3 第 5 条（"L25-1：换盒子／降模型"）⇒ **删除**（operator 已否定）。
- 新增一条：**S1 的足迹前提**——S1 的第一趟要带足迹闸，若跑不完 ⇒ 25 是**盒子判死**，届时才回到
  L25-4 的产品／口径决策（那条 operator 已拍：MTP-on 作独立口径）。
