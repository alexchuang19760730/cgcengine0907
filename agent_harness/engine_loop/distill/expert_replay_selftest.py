#!/usr/bin/env python3
"""expert_replay_selftest.py — 離線自測：不讀真軌跡以外的東西，也不需要 GPU／模型。

⛔ **本自測所測的工具已退役**（`expert_replay.py` 的 docstring 開頭）：它量的排序準確度
不是那條路徑的值函數（README §12）。自測保留，是因為退役告示本身必須**被檢查**——
`test_retired_banner()` 驗證每次執行都會印出它。其它的檢查只是證明那個「不要用它」的
建議是在一個當時確實能跑的儀器上做的，而不是對一支壞掉的東西下的結論。

為什麼要有一整套「必須失敗」的對照
------------------------------------
這支工具唯一的產出是**分數**，而分數的失敗模式是靜默的：一個把候選對錯位、把答案洩漏
進狀態、或把 chance floor 算錯的版本，印出來的表照樣很好看。所以這裡測的不是「有沒有
輸出」，是四個**結構性斷言**：

  1. **地板是對的**：`random` 的 precision@k 必須等於 `mean(demand)/256`（不是 k/256）。
  2. **調整器是對的**：`freq` 的 `AUC_hard|freq` 必須**正好** 0.500（把 freq 拔掉，
     freq 排序器就沒有資訊）；而一個真的有資訊的排序器在殘差化後**必須仍然** 1.000
     （調整器不得把真訊號削掉）。
  3. **狀態不洩漏**：第 t 步的答案不得出現在第 t 題的狀態裡 —— 用一個「只在第 t 步
     出現過的 id」當哨兵。
  4. **協定會拒絕**：外部評分器回錯長度必須 SystemExit，不得默默補零或截斷。

用法:: python3 agent_harness/engine_loop/distill/expert_replay_selftest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import expert_replay as ER     # noqa: E402

TOOL = HERE / "expert_replay.py"
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))


def write_trace(path: Path, steps, ntok: int = 1, top_k: int = 8,
                ctx: str = "0x1", truncate_at=None, ctx2=None) -> Path:
    """把 steps（list[dict[layer, list[id]]]）寫成一份最小 CGC-IDS 軌跡。

    `truncate_at=(step_idx, layer)` 會把那一行的 id 清單砍短 —— 用來測「截斷整行丟棄」。
    """
    lines = []
    for si, step in enumerate(steps):
        for layer in sorted(step):
            ids = list(step[layer])
            # 真 log 的每行恰好帶 ntok*top_k 個 id（每個 token 各 top_k 個，重疊）。這裡把它
            # **補到滿寬**：測試夾具本身短一截的話，解析器的「截斷就整行丟棄」規則會把每一行
            # 都當成截斷行（第一次寫這支自測時就是這個錯，症狀是「6 行解析出 0 步」）。
            want = ntok * top_k
            full = [ids[i % len(ids)] for i in range(want)] if ids else []
            if truncate_at == (si, layer) and full:
                full = full[:-1]
            lines.append(f"CGC-IDS: ctx={ctx} pmax=1 il={layer} ntok={ntok} "
                         + " ".join(str(x) for x in full))
    for si, step in enumerate(ctx2 or []):
        for layer in sorted(step):
            lines.append(f"CGC-IDS: ctx=0x2 pmax=1 il={layer} ntok=1 "
                         + " ".join(str(x) for x in step[layer]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_tool(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL)] + args,
                          capture_output=True, text=True, cwd=str(ER.REPO))


# --------------------------------------------------------------------------------------
# 1. 解析：步界線、截斷、多 ctx
# --------------------------------------------------------------------------------------

def test_parser(tmp: Path) -> None:
    steps_in = [
        {0: [1, 2, 3], 1: [4, 5]},
        {0: [1, 2], 1: [6, 7]},
        {0: [8, 9], 1: [1, 2]},
    ]
    p = write_trace(tmp / "t1.log", steps_in, ntok=1)
    steps, info = ER.parse_trace(p)
    check("parser: 步數 = 輸入步數", len(steps) == 3, f"got {len(steps)}")
    # 現在存的是**逐 token 扁平序列**（不去重不排序）。真 log 每行恰好帶 ntok*top_k 個 id，
    # 所以夾具會把不足的補到滿寬（[1,2,3] 在 ntok=1、top_k=8 下 ⇒ 循環補成 8 個）。
    check("parser: 逐 token 序列原樣保留（含補寬）",
          steps[0][0] == [1, 2, 3, 1, 2, 3, 1, 2], f"{steps[0][0]}")
    check("parser: 第三步的層 1 集合", set(steps[2][1]) == {1, 2})
    # token 邊界：ntok=2、top_k=3 ⇒ 每行 6 個 id，前 3 個屬 token 0、後 3 個屬 token 1
    p5 = write_trace(tmp / "t5.log", [{0: [7, 7, 7, 8, 8, 8]}], ntok=2, top_k=3)
    steps5, _ = ER.parse_trace(p5, top_k=3)
    check("parser: token 區塊順序可辨（逐 token 而非排序過的集合）",
          steps5[0][0] == [7, 7, 7, 8, 8, 8], f"{steps5[0][0]}")
    check("parser: id 範圍", (info["id_min"], info["id_max"]) == (1, 9))

    # 同一層多 token 的 top-k 取聯集（ntok=2、top_k=2 ⇒ 每行 4 個 id，聯集可小於 4）
    p2 = write_trace(tmp / "t2.log", [{0: [1, 1, 2, 3]}], ntok=2, top_k=2)
    steps2, _ = ER.parse_trace(p2, top_k=2)
    check("parser: ntok 個 token 的 id 全部留著（不去重）", steps2[0][0] == [1, 1, 2, 3],
          f"got {steps2[0][0]}")

    # 截斷的行整行丟棄，而且要被計數
    p3 = write_trace(tmp / "t3.log", [{0: [1, 2, 3]}, {0: [4, 5, 6]}], ntok=1,
                     truncate_at=(1, 0))
    steps3, info3 = ER.parse_trace(p3)
    check("parser: 截斷的整行被丟棄（該步該層不存在）",
          len(steps3) == 1, f"steps={len(steps3)}")
    check("parser: 截斷行數被報出來", info3["truncated_lines_dropped"] == 1,
          f"got {info3['truncated_lines_dropped']}")

    # 多 ctx：取行數最多的那個；也可以顯式指定
    p4 = write_trace(tmp / "t4.log", [{0: [1], 1: [2]}, {0: [3], 1: [4]}], ntok=1,
                     ctx="0xmain", ctx2=[{40: [9]}, {40: [9]}, {40: [9]}])
    _, info4 = ER.parse_trace(p4)
    check("parser: 預設取行數最多的 ctx（主幹）", info4["ctx_picked"] == "0xmain",
          info4["ctx_picked"])
    _, info4b = ER.parse_trace(p4, base_ctx="0x2")
    check("parser: 可顯式指定 ctx", info4b["ctx_picked"] == "0x2")

    # ★ 交錯寫入：無緩衝 stderr 上另一個 writer 把字插進同一行（2026-09-28 在真 log 上遇到，
    #   第一版直接 ValueError traceback）。應該「解析到第一個非整數就停」→ 長度不足 → 丟棄該行。
    p6 = tmp / "t6.log"
    p6.write_text("CGC-IDS: ctx=0x1 pmax=1 il=0 ntok=1 1 2 3 4 5 6 7 8\n"
                  "CGC-IDS: ctx=0x1 pmax=1 il=1 ntok=1 9 10 11 12 13 14 15 16\n"
                  "CGC-IDS: ctx=0x1 pmax=1 il=0 ntok=1 17 18 19 20 21CGC-RSS: t=96.48 rss=9640.3\n"
                  "CGC-IDS: ctx=0x1 pmax=1 il=1 ntok=1 25 26 27 28 29 30 31 32\n", encoding="utf-8")
    steps6, info6 = ER.parse_trace(p6)
    check("parser: 交錯寫入的行不會 traceback", True)
    check("parser: 交錯行被歸到 interleaved 而不是 truncated",
          info6["interleaved_lines_dropped"] == 1 and info6["truncated_lines_dropped"] == 0,
          f"interleaved={info6['interleaved_lines_dropped']} truncated={info6['truncated_lines_dropped']}")
    check("parser: 交錯行被丟棄後，好的行仍然完整",
          len(steps6) == 2 and set(steps6[1][1]) == {25, 26, 27, 28, 29, 30, 31, 32},
          f"steps={len(steps6)}")

    # 壞檔要吵，不要回一個空集合
    bad = tmp / "bad.log"
    bad.write_text("nothing here\n", encoding="utf-8")
    try:
        ER.parse_trace(bad)
        check("parser: 沒有 CGC-IDS 的檔案會 SystemExit", False)
    except SystemExit:
        check("parser: 沒有 CGC-IDS 的檔案會 SystemExit", True)


# --------------------------------------------------------------------------------------
# 2. 題目：compulsory、不洩漏、篩選
# --------------------------------------------------------------------------------------

def test_cases() -> None:
    # 層 0：第 0 步 {1,2}；第 1 步 {1,2}（沒有新東西）；第 2 步 {1,9}（9 是首次）
    steps = [{0: {1, 2}}, {0: {1, 2}}, {0: {1, 9}}]
    cases, info = ER.build_cases(steps, history_steps=4)
    check("cases: 兩題（t=1、t=2）", len(cases) == 2, f"got {len(cases)}")
    c1 = [c for c in cases if c["step"] == 1][0]
    check("cases: 第 1 題沒有 compulsory", c1["first_touch"] == [], f"{c1['first_touch']}")
    c2 = [c for c in cases if c["step"] == 2][0]
    check("cases: 第 2 題的 compulsory 恰為 {9}", c2["first_touch"] == [9], f"{c2['first_touch']}")
    # 需求總數 2+2=4，其中 1 個是首次（第 2 題的 9）⇒ 0.25
    check("cases: compulsory 比例 = 1/4", abs(info["compulsory_share"] - 0.25) < 1e-12,
          f"{info['compulsory_share']}")
    check("cases: 歷史只含嚴格早期步驟",
          c2["last_steps"] == [[1, 2], [1, 2]], f"{c2['last_steps']}")

    # ★ 計數口徑：引擎的 `record_routes` 是逐出現次數（同一個專家被兩個 token 選中 ⇒ +2），
    #    而第一版這裡是每步 +1。兩者排序不同，所以這是一條**手算可驗**的斷言：
    #    step0 = [5,5,1,2]（top_k=2、ntok=2 ⇒ 每行 4 個 id）、step1 = [5,5,1,2]、step2 = [5,6]
    #      token 模式：t=1 的計數 {5:2,1:1,2:1} ⇒ top-2={5,1}，truth={1,2,5} ⇒ 1.0
    #                   t=2 的計數 {5:4,1:2,2:2} ⇒ top-2={5,1}，truth={5,6}   ⇒ 0.5  平均 0.75
    #      step  模式：t=1 計數三個都 1（同分），tie-break 取小 ⇒ top-2={1,2} ⇒ 1.0
    #                   t=2 計數 {5:2,1:2,2:2} ⇒ top-2={1,2}，truth={5,6}   ⇒ 0.0  平均 0.50
    seq_steps = [{0: [5, 5, 1, 2]}, {0: [5, 5, 1, 2]}, {0: [5, 6]}]
    ct, it = ER.build_cases(seq_steps, count_mode="token")
    cs, _ = ER.build_cases(seq_steps, count_mode="step")
    rt = ER.run(ct, "freq", 2, 1)
    rs = ER.run(cs, "freq", 2, 1)
    check("count-mode: token 模式 precision = 0.75（手算）",
          abs(rt["precision_at_k"] - 0.75) < 1e-12, f"{rt['precision_at_k']}")
    check("count-mode: step 模式 precision = 0.50（手算）",
          abs(rs["precision_at_k"] - 0.50) < 1e-12, f"{rs['precision_at_k']}")
    check("count-mode: 兩個口徑真的不同（不是同一個排序換名字）",
          it["count_mode"] == "token" and rt["precision_at_k"] != rs["precision_at_k"])

    # layer / min_history 篩選
    _, i2 = ER.build_cases(steps, layer_filter={7})
    check("cases: layer 篩選", i2["cases"] == 0, f"got {i2['cases']}")
    _, i3 = ER.build_cases(steps, min_history=10)
    check("cases: min_history 篩選", i3["cases"] == 0, f"got {i3['cases']}")


def test_no_leak() -> None:
    """哨兵：一個只在第 t 步出現的 id 不得出現在第 t 題的狀態裡。"""
    steps = [{0: [1, 2, 3]}, {0: [1, 2, 3]}, {0: [1, 2, 255]}]
    cases, _ = ER.build_cases(steps)
    c = [x for x in cases if x["step"] == 2][0]
    state = ER.render_state(c)
    toks = set(state.replace("|", " ").replace("--", " ").split())
    check("no-leak: 首次出現的 id 255 不在狀態裡",
          "255" not in toks, "255 出現在狀態中")
    check("no-leak: 狀態標題標明只到 t-1 步",
          "up to step 1" in state, state.splitlines()[0])
    check("no-leak: 狀態不含第 t 步的標籤", "step 2:" not in state)
    # 對照：同一題若把答案餵進去，哨兵一定會被抓到（證明這個檢查不是空的）
    bad_case = dict(c)
    bad_case["last_steps"] = [[1, 2, 255]]
    check("no-leak: 對照組（故意洩漏）會被抓到",
          "255" in set(ER.render_state(bad_case).split()))


# --------------------------------------------------------------------------------------
# 3. 指標：地板、調整器自檢、真訊號不被削掉
# --------------------------------------------------------------------------------------

def _synthetic_cases():
    """10 步：每層輪流選 {0..7} 與 {8..15}，穩定、可預測。"""
    steps = []
    for t in range(10):
        sel = {0: list(range(8))} if t % 2 == 0 else {0: list(range(8, 16))}
        steps.append(sel)
    return ER.build_cases(steps, history_steps=4)


def test_metrics() -> None:
    cases, cinfo = _synthetic_cases()
    check("metrics: 有題目", len(cases) > 0)

    rnd = ER.run(cases, "random", 8, 1)
    check("metrics: random 的 precision 貼著 chance floor",
          abs(rnd["precision_at_k"] - rnd["chance_precision_at_k"]) < 0.02,
          f"{rnd['precision_at_k']:.4f} vs {rnd['chance_precision_at_k']:.4f}")
    # 容忍度＝抽樣誤差，不是隨手放寬：一題的 sd(AUC) = sqrt(P·N·(P+N+1)/12)/(P·N)
    # ≈ 0.104（P=8,N=248），9 題平均 ⇒ ≈ 0.035。用 0.02 會偶發紅燈。
    check("metrics: random 的 AUC_all ≈ 0.5",
          abs(rnd["auc_all"]["value"] - 0.5) < 0.06, f"{rnd['auc_all']['value']:.4f}")
    check("metrics: chance floor = mean(demand)/256（不是 k/256）",
          abs(rnd["chance_precision_at_k"] - rnd["mean_demand"] / 256.0) < 1e-12,
          f"{rnd['chance_precision_at_k']} vs {rnd['mean_demand']/256.0}")

    freq = ER.run(cases, "freq", 8, 1)
    hr = freq["auc_hard_freq_removed"]["value"]
    check("metrics: freq 的 AUC_hard|freq 正好 0.500（調整器自檢）",
          hr is not None and abs(hr - 0.5) < 1e-12, f"{hr}")
    check("metrics: freq 在週期性軌跡上贏過 chance",
          freq["precision_at_k"] > rnd["precision_at_k"] + 0.3,
          f"{freq['precision_at_k']:.3f} vs {rnd['precision_at_k']:.3f}")

    # oracle：把答案當分數 ⇒ 1.000，而且**殘差化後仍然 1.000**（調整器不得削掉真訊號）
    def score_oracle(case, corpus):
        return [1.0 if e in case["truth"] else 0.0 for e in range(case["n_ids"])]
    ER.SCORERS["_selftest_oracle"] = score_oracle
    try:
        ora = ER.run(cases, "_selftest_oracle", 8, 1)
    finally:
        ER.SCORERS.pop("_selftest_oracle", None)
    check("metrics: oracle 的 precision = 1.0", abs(ora["precision_at_k"] - 1.0) < 1e-12)
    check("metrics: oracle 的 AUC_hard = 1.0", abs(ora["auc_hard"]["value"] - 1.0) < 1e-12)
    check("metrics: oracle 殘差化後仍 1.0（調整器不削真訊號）",
          abs(ora["auc_hard_freq_removed"]["value"] - 1.0) < 1e-12,
          f"{ora['auc_hard_freq_removed']['value']}")

    # 決定性：同一個 seed 兩次跑必須逐欄相同
    a = ER.run(cases, "random", 8, 7)
    b = ER.run(cases, "random", 8, 7)
    a.pop("seconds"), b.pop("seconds")          # 秒數本來就會不同，不是不可重現
    check("metrics: 同 seed 可重現",
          json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))

    # 長度不符必須拒絕（不是補零、不是截斷）
    bad = [0.0] * (cases[0]["n_ids"] - 1)
    try:
        ER.evaluate(cases[:1], [bad], 8)
        check("metrics: 分數長度不符會 SystemExit", False)
    except SystemExit:
        check("metrics: 分數長度不符會 SystemExit", True)

    # lag：軌跡是「每步都與前一步相同」⇒ lag 應該命中
    same, _ = ER.build_cases([{0: [1, 2, 3, 4, 5, 6, 7, 8]} for _ in range(6)])
    lag = ER.run(same, "lag", 8, 1)
    check("metrics: lag 在重複軌跡上 precision = 1.0",
          abs(lag["precision_at_k"] - 1.0) < 1e-12, f"{lag['precision_at_k']}")
    check("metrics: lag 的 compulsory 在重複軌跡上是 0",
          abs(ER.build_cases([{0: [1, 2, 3, 4, 5, 6, 7, 8]} for _ in range(6)])[1]
              ["compulsory_share"]) < 1e-12)


# --------------------------------------------------------------------------------------
# 4. 協定：外部評分器（JEv / CLM 的入口）
# --------------------------------------------------------------------------------------

def test_command_protocol(tmp: Path) -> None:
    steps = [{0: [1, 2, 3, 4]}, {0: [1, 2, 3, 4]}, {0: [1, 2, 3, 4]}]
    trace = write_trace(tmp / "proto.log", steps)

    oracle_cmd = tmp / "oracle_adapter.py"
    oracle_cmd.write_text(textwrap.dedent("""
        import json, sys
        p = json.load(sys.stdin)
        # 協定：candidates[i]['id'] = 'e<expert id>'；這裡用「出現次數最多」當預測
        import re
        def cnt(c):
            m = re.search(r"route count so far (\\d+)", c["text"])
            return int(m.group(1)) if m else 0
        best = sorted(((cnt(c), c["id"]) for c in p["candidates"]),
                      key=lambda t: (-t[0], t[1]))[:8]
        top = {i for _, i in best}
        print(json.dumps({"scores": [1.0 if c["id"] in top else 0.0 for c in p["candidates"]]}))
    """), encoding="utf-8")

    r = run_tool(["--trace", str(trace), "--scorer", "command",
                  "--scorer-command", f"{sys.executable} {oracle_cmd}"])
    check("protocol: 外部評分器可以跑完整條路徑", r.returncode == 0, r.stderr[-400:])
    # 這條軌跡的 demand 只有 4 個 id、k=8 ⇒ **precision@8 的天花板是 4/8 = 0.5**（見 docstring
    # 的「precision 被 demand 封頂」）。所以可檢查的讀數是 recall：adapter 的分數真的到達指標了。
    ok = False
    for line in r.stdout.splitlines():
        if line.strip().startswith("command") and "precision@8" in line:
            body = line.split("precision@8=")[1].split()[0]
            rec = line.split("recall=")[1].split()[0]
            ok = abs(float(body) - 0.5) < 1e-9 and abs(float(rec) - 1.0) < 1e-9
    check("protocol: 外部評分器的分數真的進了指標（recall 1.0、precision 被 k 封頂在 0.5）",
          ok, r.stdout[-300:])

    bad_cmd = tmp / "bad_adapter.py"
    bad_cmd.write_text("import json,sys; json.load(sys.stdin); print(json.dumps({'scores':[1.0]}))\n",
                       encoding="utf-8")
    r2 = run_tool(["--trace", str(trace), "--scorer", "command",
                   "--scorer-command", f"{sys.executable} {bad_cmd}"])
    check("protocol: 長度不符的外部評分器被拒絕（非 0 結束）", r2.returncode != 0, r2.stdout[-200:])
    check("protocol: 拒絕訊息說明原因",
          "misaligned" in r2.stderr or "SystemExit" in r2.stderr, r2.stderr[-300:])


# --------------------------------------------------------------------------------------
# 5. CLI：--json 純度、report manifest、預設軌跡選擇
# --------------------------------------------------------------------------------------

def test_cli(tmp: Path) -> None:
    steps = [{0: [1, 2, 3]}, {0: [1, 2, 3]}, {0: [4, 5, 6]}]
    trace = write_trace(tmp / "cli.log", steps)

    r = run_tool(["--trace", str(trace), "--json"])
    check("cli: --json 時 stdout 從第一行就是 JSON", r.returncode == 0, r.stderr[-300:])
    try:
        payload = json.loads(r.stdout)
        ok = payload["trace_info"]["trace"].endswith("cli.log")
    except Exception as e:      # noqa: BLE001
        ok = False
        payload = {"err": str(e)}
    check("cli: JSON 不含人類可讀輸出", ok, str(payload)[:200])
    check("cli: 人類可讀輸出改走 stderr", "===" in r.stderr, r.stderr[:120])

    out = tmp / "out"
    r2 = run_tool(["--trace", str(trace), "--out-dir", str(out)])
    man = out / "manifest.json"
    check("cli: --out-dir 產生 manifest.json", man.exists())
    if man.exists():
        m = json.loads(man.read_text(encoding="utf-8"))
        check("cli: manifest 記 sha256 而不是命令原文",
              "scorer_command_sha256_16" in m and m["scorer_command_sha256_16"] is None)
        check("cli: manifest 記軌跡指紋", len(m["trace_sha256_16"]) == 16)
        check("cli: manifest 記 k / ema_alpha 等參數",
              m["k"] == 8 and abs(m["ema_alpha"] - ER.ENGINE_SPAC_ALPHA) < 1e-12)
    check("cli: replay.json 產生", (out / "replay.json").exists())

    # 只有一步的軌跡：要吵，不要回一個空表
    one = write_trace(tmp / "one.log", [{0: [1, 2]}])
    r3 = run_tool(["--trace", str(one)])
    check("cli: 只有一步的軌跡會拒絕", r3.returncode != 0 and "step" in r3.stderr.lower(),
          r3.stderr[-200:])

    # 預設軌跡選擇只挑真檔（Backup/cgc_logs 裡有斷掉的 symlink）
    picked = ER.default_trace(min_lines=1)
    check("cli: 預設軌跡是存在的檔案", picked.is_file(), str(picked))


def test_retired_banner(tmp: Path) -> None:
    """退役告示必須每次都印，而且必須說得出「值函數是什麼」。

    一條「不要用這個」的建議如果是靠人情記住的，它就等於沒有。所以它是一條斷言。
    """
    steps = [{0: [1, 2, 3]}, {0: [1, 2, 3]}, {0: [4, 5, 6]}]
    trace = write_trace(tmp / "retired.log", steps)

    r = run_tool(["--trace", str(trace)])
    check("retired: 一般執行會印出退役告示", "已退役" in r.stdout + r.stderr, r.stdout[:200])
    check("retired: 告示指名值函數（阻塞 µs），不是只說「沒用」",
          "阻塞" in r.stdout + r.stderr)
    check("retired: 告示指向成本端模擬（thrash_sim）而不是留白",
          "thrash_sim" in r.stdout + r.stderr)
    check("retired: 告示附上那個數字的出處（§12）", "§12" in r.stdout + r.stderr)

    rj = run_tool(["--trace", str(trace), "--json"])
    check("retired: --json 時告示走 stderr，stdout 仍是純 JSON",
          "已退役" in rj.stderr and "已退役" not in rj.stdout)
    try:
        json.loads(rj.stdout)
        check("retired: --json 的 stdout 可被 json.loads", True)
    except Exception as e:      # noqa: BLE001
        check("retired: --json 的 stdout 可被 json.loads", False, str(e))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_parser(tmp)
        test_cases()
        test_no_leak()
        test_metrics()
        test_command_protocol(tmp)
        test_cli(tmp)
        test_retired_banner(tmp)

    npass = sum(1 for _, ok, _ in CHECKS if ok)
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL  {name}" + (f"   [{detail}]" if detail else ""))
    print(f"{npass}/{len(CHECKS)} passed")
    return 0 if npass == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
