#!/usr/bin/env python3
"""action_replay.py — 「CLM 能不能對決策有投票權」的可執行問句。

THE QUESTION THIS FILE ANSWERS, AND WHY IT CANNOT BE ANSWERED BY RUNNING CLM
---------------------------------------------------------------------------
CLM 不會生成。它是兩塔對比模型，輸出的是 (state, action) 的相似度分數。所以「讓 CLM 參與決策」
不能做成「叫它寫下一個決策」—— 那是生成，它做不到，而 `closed_loop.py` 那一半（生成式的
prime-agent）也不該被它取代。它能做、而且只有它能做的那一件事是：**把已經寫出來的候選動作排序**。

於是問題從「CLM 聰明嗎」換成一個儀器問題：

    在既有的決策紀錄上，把真動作與一堆候選動作混在一起，一個**純排序器**能不能把真的排到第一？

這支把 `traces/*.jsonl` 變成那道題。它不下載權重、不啟動引擎、不呼叫模型（除非你顯式給
`--scorer command`），所以「要不要為 CLM 花掉這台 16 GB 機器的記憶體」這個問題可以先被回答一次，
再決定要不要付那筆錢。**順序反過來才是錯的**：先下載 16 GB、再想辦法證明它有用。

WHY THE DATA ALREADY EXISTS (AND IS BETTER THAN A LABELED SET)
--------------------------------------------------------------
`traces/decisions.jsonl` 每一筆本來就是一個 (狀態, 動作, 被否決的動作) 三元組：

    question + evidence[].reading   -> 狀態
    action                          -> 當時被採納的動作（正例）
    ruled_out[].claim               -> 它**自己明文否決過**的動作（硬負例）

對比學習（InfoNCE）要的正是這個形狀，而這裡的三元組是當時真的付過代價的判斷，不是標註員編的。
負例的權威定義在 repo 自己的 README §3：`ruled_out` 是負面知識，也是「最常被重用」的那一欄。

TWO POOLS, TWO DIFFERENT QUESTIONS — AND WHY THEY MUST NOT BE AVERAGED
---------------------------------------------------------------------
  * **sibling 池**（真動作 vs 其他決策的動作）：一般排序能力。池子大、樣本多，但負例是
    「別的題目的答案」，隨機性高，一個只會抓關鍵字的排序器就能贏。
  * **rejected 池**（真動作 vs 這一題自己否決的動作）：**唯一有資格當閘門的那一池**。
    兩者混在一起報一個平均，等於讓容易的那池替難的那池背書。

而 rejected 池有一個**必須講明的捷徑**：`sft_common.render_state_for_decision()` 把 ruled_out
印在狀態裡（`ALREADY RULED OUT (do not re-walk these):`）。所以一個「把狀態唸一遍」的排序器
會給那些否決項**高分**——它們字面上就在輸入裡。`--state-ruled-out strip` 剪掉那一段重跑，
兩個變體都報，捷徑才會現形。這一點是這支腳本存在的主要理由之一：**不報這個變體，rejected
池的分數會同時是「判別力」與「抄輸入」的混合物，而兩者的下一步完全不同。**

WHY IT REUSES THE SFT PROJECTION INSTEAD OF RENDERING ITS OWN PROMPTS
---------------------------------------------------------------------
`README.md` §2.1 的紅線是「只有一份渲染器」。這支**不寫第二份**：它 `import build_sft_prime`
並呼叫 `state_to_next_arm()` / `evidence_to_lesson()`，也就是說被排序的那段狀態**逐位元組**
等於 SFT 資料裡的那一段。理由是實驗上的：如果重放用的字串與訓練／推論用的字串不是同一份，
那量到的分數不能轉移到任何決策上，而**這兩份字串會漂移的方式是靜默的**。
`--verify-against-sft`（預設開）把重建結果逐筆對回 `sft_prime/{train,valid}.jsonl`，讓
「只有一份渲染器」是一條被檢查的斷言，不是一句聲明。

WEAK STATISTICS, STATED UP FRONT
--------------------------------
決策 66 筆（68 扣掉 2 筆 `judgement=refuted`，見 `decision.schema.json` 的防污染規則）。
n=66 時 top-1 的 95% 半寬 ≈ 1.96*sqrt(0.25/66) ≈ **12 個百分點**。所以：
**兩個排序器差不到 12 點，不要說誰贏。** top-1 只是「順便看一眼」的欄位。

第二次跑就跑出來的那件事（**這支腳本存在的第二個理由**）：
`ruled_out[].claim` 是一句被否決的短句，`action` 是一段執行敘述 —— **97/97 對都是正例較長**
（中位差 +367 字）。所以在原始分數上，連 `len()` 都能在 rejected 池拿到 AUC = 1.000。
**原始（raw）的 rejected 數字單獨沒有資訊**。因此每個排序器都報兩套：raw 與 `|len`
（見 `residualize()`：在題內候選池上對長度做最小平方回歸，回傳殘差）。
`length` 對照的 `|len` 讀數在殘差化後應回到 0.500 —— 那正好是這個調整器自己的自檢。

用法::

    # 純讀，不寫檔：內建排序器的階梯（chance / 長度陰性對照 / 詞彙基線）
    python3 agent_harness/engine_loop/distill/action_replay.py

    # 剪掉狀態裡的 ruled_out 再跑一次（2026-09-28：這一列是「keep 把判別力吃掉」的證據）
    python3 agent_harness/engine_loop/distill/action_replay.py --state-ruled-out strip

    # CLM 進來的地方：任何讀 stdin 寫 stdout 的評分器
    python3 agent_harness/engine_loop/distill/action_replay.py --scorer command \
        --scorer-command 'python3 my_clm_adapter.py' --report

    python3 agent_harness/engine_loop/distill/action_replay_selftest.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent       # .../engine_loop/distill
ENGINE = HERE.parent                         # .../engine_loop
SFT_PRIME = ENGINE / "sft_prime"             # build_sft_prime.py 住在這裡
for _p in (str(SFT_PRIME), str(ENGINE), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_sft_prime as B                  # noqa: E402  同一份渲染器，不寫第二份
import sft_common as C                       # noqa: E402

RULED_OUT_MARKER = "\nALREADY RULED OUT (do not re-walk these):"
OUT_ROOT = HERE / "action_replay_out"
BUILTIN_SCORERS = ("random", "length", "lexical")


# --------------------------------------------------------------------------------------
# 資料：決策／教訓 -> (狀態, 動作, 被否決的動作)
# --------------------------------------------------------------------------------------

def strip_ruled_out(state: str) -> str:
    """剪掉「已經被排除」那一段（見 docstring：這是量測抄捷徑的消融，不是清理）。"""
    return state.split(RULED_OUT_MARKER)[0]


def recorded_invocation(sft_dir: Path | None = None) -> tuple[dict, list[str]]:
    """SFT 投影**自己記下來的**呼叫參數。

    `val_frac` / `system` 不寫死在這裡：`sft_common.reconcile` 的設計意圖就是「沒給旗標 =
    重現磁碟上的那份」，而重放必須與它同一個切分、同一段系統提示，否則量到的東西與被訓練的
    東西不是同一個題目。
    """
    rec = C.load_provenance(sft_dir or B.REAL_OUT_DIR)
    return C.reconcile(rec, builder=B.BUILDER, mode=None, head_chars=None,
                       val_frac=None, check=False, default_head_chars=0)


def build_pairs(task: str = "decision", state_mode: str = "keep",
                sft_dir: Path | None = None) -> tuple[list[dict], dict]:
    """-> (pairs, info)。pairs 每筆是 {case_id, state, action, rejected[], meta}。

    `sft_dir` 只為了自測能把 PROVENANCE / artifact 指到暫時樹（自測不得讀真 traces 以外的東西
    當作期望值）；正式呼叫不給，走 `build_sft_prime.REAL_OUT_DIR`。
    """
    eff, notes = recorded_invocation(sft_dir)
    sys_text = C.system_text(C.charter()[0], eff["system"], eff["head_chars"])

    episodes = C.load(C.EPISODES)
    by_ep = {e["episode_id"]: e for e in episodes}

    if task == "decision":
        recs = C.load(C.DECISIONS)
        rows, skipped = B.state_to_next_arm(recs, by_ep, sys_text)
        by_id = {d["decision_id"]: d for d in recs}
        skipped_why = "judgement=refuted（防污染閘門，decision.schema.json）"
    elif task == "lesson":
        recs = C.load(C.LESSONS)
        rows = B.evidence_to_lesson(recs, sys_text)
        skipped = len(recs) - len(rows)
        by_id = {l["lesson_id"]: l for l in recs}
        skipped_why = "superseded_by 非空（已更換的規訓不得當現行）"
    else:
        raise SystemExit(f"unknown task: {task}")

    pairs = []
    for r in rows:
        src = by_id.get(r["source_id"], {})
        state = r["messages"][1]["content"]
        if state_mode == "strip":
            state = strip_ruled_out(state)
        pairs.append({
            "case_id": r["source_id"],
            "state": state,
            "action": r["messages"][2]["content"],
            "rejected": [x["claim"] for x in (src.get("ruled_out") or [])],
            "meta": {k: v for k, v in r.items() if k != "messages"},
        })

    deltas = [len(p["action"]) - len(r) for p in pairs for r in p["rejected"]]
    info = {
        "task": task,
        "records": len(recs),
        "pairs": len(pairs),
        "skipped": skipped,
        "skipped_why": skipped_why,
        "invocation": eff,
        "notes": notes,
        "with_rejected": sum(1 for p in pairs if p["rejected"]),
        "rejected_claims": sum(len(p["rejected"]) for p in pairs),
        # 這一塊是量出來的資料性質，不是參數：它決定 rejected 池能不能當閘門。
        "length_confound": {
            "n_pairs": len(deltas),
            "pos_longer": sum(1 for d in deltas if d > 0),
            "median_delta": int(statistics.median(deltas)) if deltas else 0,
            "min_delta": min(deltas) if deltas else 0,
        },
    }
    return pairs, info


def split_pairs(pairs: list[dict], split: str, val_frac: float) -> list[dict]:
    """切分**必須**與 SFT 相同（同一個 `sft_common.split`、同一個 seed）。

    否則「評估集」可能正是被拿去訓練的那一半，而那種洩漏在數字上長得跟「模型很強」一樣。
    注意 `sft_prime` 是**一份合併的** rows（lesson 在前、decision 在後）再切分，所以單跑
    一個 task 時切分結果與合併時不同；這也是為什麼 `--split` 預設 valid 而不是 all。
    """
    if split == "all":
        return pairs
    train, valid = C.split(pairs, val_frac, C.SPLIT_SEED)
    return valid if split == "valid" else train


def build_cases(pairs: list[dict], k: int, seed: int) -> list[dict]:
    """每題：正例 + (k-1) 個 sibling 負例 + 該題自己的 rejected 負例。

    候選清單**永遠**把正例放在 index 0（指標函式靠這個前提），sibling 用帶 seed 的
    `random.Random` 抽，所以同一組參數重跑會得到同一個題目集 —— 一個每次重洗的題目集
    沒辦法跟前一次 diff，而「題目變了」與「排序器變了」就會不可分。
    """
    rng = random.Random(seed)
    all_actions = [(p["case_id"], p["action"]) for p in pairs]
    cases = []
    for p in pairs:
        others = [t for cid, t in all_actions if cid != p["case_id"]]
        n = min(max(k - 1, 0), len(others))
        sib = rng.sample(others, n) if n else []
        cands = [{"id": "POS", "text": p["action"], "kind": "positive"}]
        cands += [{"id": f"SIB{i}", "text": t, "kind": "sibling"} for i, t in enumerate(sib)]
        cands += [{"id": f"REJ{i}", "text": t, "kind": "rejected"} for i, t in enumerate(p["rejected"])]
        cases.append({"case_id": p["case_id"], "state": p["state"],
                      "candidates": cands, "n_rejected": len(p["rejected"]),
                      "meta": p["meta"]})
    return cases


# --------------------------------------------------------------------------------------
# 排序器：介面固定為 score(state, candidates, corpus) -> list[float]
# --------------------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9_][a-z0-9_.:/-]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """英數詞 + CJK 字元 bigram。

    中文單字沒有資訊量（「的」「是」滿篇），單字元詞袋等於把語料變成雜訊；bigram 是
    不需要分詞器、也不需要任何依賴的最低成本替代。
    """
    t = text.lower()
    toks = _WORD_RE.findall(t)
    cjk = _CJK_RE.findall(t)
    toks += ["".join(p) for p in zip(cjk, cjk[1:])]
    return toks


def build_idf(docs: list[str]) -> dict[str, float]:
    n = max(len(docs), 1)
    df: dict[str, int] = {}
    for d in docs:
        for tok in set(tokenize(d)):
            df[tok] = df.get(tok, 0) + 1
    return {tok: math.log((1.0 + n) / (1.0 + c)) + 1.0 for tok, c in df.items()}


def _vec(text: str, idf: dict[str, float]) -> dict[str, float]:
    v: dict[str, float] = {}
    for tok in tokenize(text):
        v[tok] = v.get(tok, 0.0) + idf.get(tok, 1.0)
    return v


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(w * b.get(tok, 0.0) for tok, w in a.items())
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return 0.0 if na == 0.0 or nb == 0.0 else dot / (na * nb)


def score_random(state, cands, corpus):
    rng = random.Random(f"{corpus['seed']}|{hashlib.sha256(state.encode('utf-8')).hexdigest()[:12]}")
    return [rng.random() for _ in cands]


def score_length(state, cands, corpus):
    """陰性對照：最長的贏。它若贏了，表示這個題目在量長度，不是在量判斷。"""
    return [float(len(c["text"])) for c in cands]


def score_lexical(state, cands, corpus):
    """下限基線：狀態與候選的 tf-idf 餘弦。"""
    idf = corpus["idf"]
    qv = _vec(state, idf)
    return [_cos(qv, _vec(c["text"], idf)) for c in cands]


def score_command(state, cands, corpus):
    """外部評分器（**CLM 進來的地方**）。

    協定固定，而且刻意小到任何模型都能接：

        stdin  : {"query": "<狀態>", "candidates": [{"id": ..., "text": ...}, ...]}
        stdout : {"scores": [<float>, ...]}      或 [{"id":..., "score":...}, ...]

    長度不符就**失敗**，不截斷也不補零：一個默默回錯長度的評分器會讓所有名次看起來
    很合理，而那是這整支腳本唯一不能接受的失敗模式。
    """
    payload = {"query": state,
               "candidates": [{"id": c["id"], "text": c["text"]} for c in cands]}
    cmd = corpus["command"]
    proc = subprocess.run(cmd, input=json.dumps(payload, ensure_ascii=False), text=True,
                          capture_output=True, shell=True)
    if proc.returncode != 0:
        raise SystemExit(f"scorer command exited {proc.returncode}\n--- stderr ---\n{proc.stderr[-2000:]}")
    out = proc.stdout.strip()
    if not out:
        raise SystemExit("scorer command produced no stdout (協定見 score_command 的 docstring)")
    line = out.splitlines()[-1]
    try:
        got = json.loads(line)
    except json.JSONDecodeError as e:
        raise SystemExit(f"scorer stdout is not JSON ({e}); last line was: {line[:400]}")
    if isinstance(got, dict):
        got = got.get("scores")
    if not isinstance(got, list) or len(got) != len(cands):
        raise SystemExit(f"scorer returned {len(got) if isinstance(got, list) else type(got).__name__} "
                         f"scores for {len(cands)} candidates -- refusing to rank a misaligned answer")
    if got and isinstance(got[0], dict):
        by_id = {str(d["id"]): float(d["score"]) for d in got}
        got = [by_id[c["id"]] for c in cands]
    return [float(x) for x in got]


SCORERS = {"random": score_random, "length": score_length,
           "lexical": score_lexical, "command": score_command}


def make_corpus(cases: list[dict], scorer: str, seed: int, command=None) -> dict:
    corpus = {"seed": seed, "command": command, "idf": {}}
    if scorer == "lexical":
        docs = [c["state"] for c in cases]
        for c in cases:
            docs += [x["text"] for x in c["candidates"]]
        corpus["idf"] = build_idf(docs)
    return corpus


# --------------------------------------------------------------------------------------
# 指標
# --------------------------------------------------------------------------------------

def rank_of_positive(scores: list[float]) -> int:
    """1-based 名次，**同分時正例排在最後**（悲觀 tie-break）。

    反過來（樂觀）會讓一個「全部同分」的排序器拿到第一名，而那正是最常見的退化模式。
    """
    pos = scores[0]
    better = sum(1 for i, s in enumerate(scores) if i and s > pos)
    ties = sum(1 for i, s in enumerate(scores) if i and s == pos)
    return 1 + better + ties


def residualize(scores: list[float], lens: list[int]) -> list[float]:
    """拔掉長度的分數：在**同一題的候選池內**對 len 做最小平方回歸，回傳殘差。

    為什麼非拔不可（2026-09-28 實測）：`ruled_out[].claim` 通常是一句被否決的短句
    （中位 82 字），而 `action` 是一段執行敘述（中位 436 字），**97/97 對都是正例較長**。
    所以在原始分數上 `len()` 自己就拿到 AUC_rej = 1.000 —— 那個池子的分數量到的是長度，
    不是判斷。把長度回歸掉之後，「不可能靠長度贏」的那個讀數才是判斷。

    池內回歸而不是全域回歸：決策的可比區間是它自己的候選池，跨題的長度無關。
    斜率算不出來時（池內長度全同、或點太少）**原值回傳**，不假裝做過調整。
    """
    n = len(scores)
    if n < 3:
        return list(scores)
    mx = sum(lens) / n
    my = sum(scores) / n
    sxx = sum((l - mx) ** 2 for l in lens)
    if sxx <= 1e-9:
        return list(scores)
    b = sum((lens[i] - mx) * (scores[i] - my) for i in range(n)) / sxx
    a = my - b * mx
    return [scores[i] - (a + b * lens[i]) for i in range(n)]


def _pair_auc(pos: float, negs: list[float]) -> list[float]:
    """同分算 0.5（標準 c-index），與名次的悲觀 tie-break 不同是刻意的：
    名次要保守（別把平手說成贏），AUC 要是無偏的機率估計。"""
    out = []
    for n in negs:
        out.append(1.0 if pos > n else (0.5 if pos == n else 0.0))
    return out


def evaluate(cases: list[dict], scores_by_case: list[list[float]]) -> dict:
    """兩套讀數同時報：原始（raw）與**拔掉長度後**（`|len`）。

    為什麼不能只報一套：`length` 在 rejected 池拿 AUC=1.000（見 `residualize`），所以 raw 的
    rejected 數字單獨没有資訊；而 sibling 池剛好相反（`length` 在那裡 ≈0.52，拔不拔都一樣）。
    兩套並排，讀者才知道一個高分是「判斷」還是「字數」。
    """
    n = len(cases)
    ranks, hits, mrr, chance = [], 0, 0.0, 0.0
    sib_pairs: list[float] = []
    rej_pairs: list[float] = []
    sib_resid: list[float] = []
    rej_resid: list[float] = []
    per_case = []

    for case, scores in zip(cases, scores_by_case):
        lens = [len(c["text"]) for c in case["candidates"]]
        resid = residualize(scores, lens)

        r = rank_of_positive(scores)
        ranks.append(r)
        hits += int(r == 1)
        mrr += 1.0 / r
        chance += 1.0 / len(scores)

        pos, pos_r = scores[0], resid[0]
        sib, rej, sib_r, rej_r = [], [], [], []
        for i, c in enumerate(case["candidates"]):
            if i == 0:
                continue
            if c["kind"] == "sibling":
                sib.append(scores[i])
                sib_r.append(resid[i])
            else:
                rej.append(scores[i])
                rej_r.append(resid[i])

        sib_pairs += _pair_auc(pos, sib)
        rej_pairs += _pair_auc(pos, rej)
        sib_resid += _pair_auc(pos_r, sib_r)
        rej_resid += _pair_auc(pos_r, rej_r)

        best_other = None
        for i, c in enumerate(case["candidates"]):
            if i == 0:
                continue
            if best_other is None or scores[i] > best_other[1]:
                best_other = (c["kind"], scores[i])
        per_case.append({
            "case_id": case["case_id"], "pool": len(scores), "rank_pos": r,
            "n_sibling": len(sib), "n_rejected": len(rej),
            "auc_rejected": (sum(_pair_auc(pos, rej)) / len(rej)) if rej else None,
            "auc_rejected_resid": (sum(_pair_auc(pos_r, rej_r)) / len(rej_r)) if rej_r else None,
            "beaten_by": best_other[0] if r > 1 else None,
            "source_id_meta": case["meta"],
        })

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    p = hits / n if n else 0.0
    half = 1.96 * math.sqrt(p * (1.0 - p) / n) if n else 0.0
    return {
        "n_cases": n,
        "chance_top1": chance / n if n else None,
        "top1": p,
        "top1_ci95_half_width": half,
        "mrr": mrr / n if n else None,
        "median_rank": sorted(ranks)[len(ranks) // 2] if ranks else None,
        "auc_vs_siblings": {"value": _mean(sib_pairs), "n_pairs": len(sib_pairs)},
        "auc_vs_siblings_len_removed": {"value": _mean(sib_resid), "n_pairs": len(sib_resid)},
        "auc_vs_rejected": {"value": _mean(rej_pairs), "n_pairs": len(rej_pairs),
                            "n_cases_with_rejected": sum(1 for c in cases if c["n_rejected"])},
        "auc_vs_rejected_len_removed": {"value": _mean(rej_resid), "n_pairs": len(rej_resid)},
        "cases": per_case,
    }


def run(cases: list[dict], scorer: str, seed: int, command=None) -> dict:
    corpus = make_corpus(cases, scorer, seed, command)
    fn = SCORERS[scorer]
    scores_by_case = []
    t0 = time.time()
    for case in cases:
        s = fn(case["state"], case["candidates"], corpus)
        if len(s) != len(case["candidates"]):
            raise SystemExit(f"scorer {scorer} returned {len(s)} scores for "
                             f"{len(case['candidates'])} candidates (case {case['case_id']})")
        scores_by_case.append(s)
    res = evaluate(cases, scores_by_case)
    res["scorer"] = scorer
    res["seconds"] = round(time.time() - t0, 3)
    return res


# --------------------------------------------------------------------------------------
# 「只有一份渲染器」是一條被檢查的斷言
# --------------------------------------------------------------------------------------

def verify_against_sft(pairs: list[dict], state_mode: str = "keep",
                       sft_dir: Path | None = None) -> dict:
    """重建的 (狀態, 動作) 逐筆對回 `sft_prime/{train,valid}.jsonl` 的內容。

    這條檢查的理由：重放若用了與訓練不同的字串，分數不能轉移到任何決策上，而
    **兩份字串漂移的方式是靜默的**（都在同一支 repo、都「看起來對」）。

    `strip` 模式下要求的是**前綴**而不是相等：那個模式的目的就是刪掉尾段，所以相等是
    錯的斷言；而「刪掉的正好是最後那一段」仍然是一條真的、可檢查的斷言。
    """
    sft_dir = sft_dir or B.REAL_OUT_DIR
    idx = {}
    for name in ("train", "valid"):
        path = sft_dir / f"{name}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                # 以 (kind, source_id) 為鍵：兩份投影在同一個目錄，`source_id` 單獨不唯一。
                idx[(row.get("kind"), row.get("source_id"))] = (
                    row["messages"][1]["content"], row["messages"][2]["content"])
    checked = same = 0
    mismatch = []
    for p in pairs:
        key = (p["meta"].get("kind"), p["case_id"])
        if key not in idx:
            continue
        checked += 1
        st, act = idx[key]
        state_ok = (st == p["state"]) if state_mode == "keep" else st.startswith(p["state"])
        if state_ok and act == p["action"]:
            same += 1
        else:
            mismatch.append(p["case_id"])
    return {"checked": checked, "same": same, "mismatch": mismatch}


# --------------------------------------------------------------------------------------
# 輸出
# --------------------------------------------------------------------------------------

def print_info(info: dict, out=print) -> None:
    print = out  # noqa: A001 -- 人類可讀輸出走哪個流是呼叫端決定的（--json 時走 stderr）
    print(f"  資料源      : traces/{'decisions' if info['task'] == 'decision' else 'lessons'}.jsonl"
          f"  記錄 {info['records']}  可用 {info['pairs']}")
    print(f"  排除        : {info['skipped']} 筆（{info['skipped_why']}）")
    print(f"  否決負例    : {info['with_rejected']} 題帶否決、共 {info['rejected_claims']} 條")
    lc = info["length_confound"]
    if lc["n_pairs"]:
        print(f"  長度混淆    : {lc['pos_longer']}/{lc['n_pairs']} 對是正例較長（中位 +{lc['median_delta']} 字，"
              f"最短 +{lc['min_delta']}）⇒ AUC_rej 原始值連 len() 都拿得到，"
              f"可讀的那一層是 AUC_*|len")
    else:
        print("  長度混淆    : 這個 task 沒有否決負例池（只有 lesson 才有；rejected 欄位不適用）")
    inv = info["invocation"]
    print(f"  呼叫參數    : system={inv['system']} head_chars={inv['head_chars']} "
          f"val_frac={inv['val_frac']}（取自 sft_prime/PROVENANCE.json）")


def _f(v, nd=3):
    return "  n/a" if v is None else f"{v:.{nd}f}"


def print_result(res: dict, label: str = "", out=print) -> None:
    print = out  # noqa: A001
    a, asl = res["auc_vs_siblings"], res["auc_vs_siblings_len_removed"]
    r, rl = res["auc_vs_rejected"], res["auc_vs_rejected_len_removed"]
    tag = f" [{label}]" if label else ""
    print(f"  {res['scorer']:>8}{tag}  chance_top1={_f(res['chance_top1'])}  "
          f"top1={_f(res['top1'])} ±{_f(res['top1_ci95_half_width'])}  mrr={_f(res['mrr'])}  "
          f"rank_med={res['median_rank']}")
    print(f"            AUC_sib={_f(a['value'])} (n={a['n_pairs']})  "
          f"AUC_sib|len={_f(asl['value'])}  "
          f"AUC_rej={_f(r['value'])} (n={r['n_pairs']})  "
          f"AUC_rej|len={_f(rl['value'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=("decision", "lesson"), default="decision")
    ap.add_argument("--split", choices=("valid", "train", "all"), default="all",
                    help="預設 all（n 最大）；valid/train 是與 SFT 相同的切分，"
                         "訓練過任何東西之後**必須**改用 valid")
    ap.add_argument("--state-ruled-out", choices=("keep", "strip"), default="keep",
                    help="strip = 剪掉狀態裡的 ruled_out 段（消融抄捷徑）")
    ap.add_argument("--k", type=int, default=6, help="候選池大小（含正例）")
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--scorer", choices=BUILTIN_SCORERS + ("command",), default=None,
                    help="不給 = 跑內建階梯（random / length / lexical）")
    ap.add_argument("--scorer-command", default=None,
                    help="外部評分器命令（讀 stdin 寫 stdout，協定見 score_command docstring）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--cases", action="store_true", help="印每一題的名次")
    ap.add_argument("--json", action="store_true",
                    help="只印 JSON（機器模式；人類可讀的階梯改到 stderr，兩者不相混）")
    ap.add_argument("--report", action="store_true", help="寫 distill/action_replay_out/<ts>/")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--no-verify-sft", dest="verify_sft", action="store_false")
    args = ap.parse_args()

    # --json 是機器模式：人類可讀的輸出改走 stderr，所以 stdout 從第一行就是 JSON。
    # （把 JSON 接在進度報告後面，任何 `| jq` 或 `json.loads(stdout)` 都會在冒號上碎掉。）
    P = (lambda *a, **k: print(*a, file=sys.stderr, **k)) if args.json else print

    pairs, info = build_pairs(args.task, args.state_ruled_out)
    eff = info["invocation"]
    pairs = split_pairs(pairs, args.split, eff["val_frac"])
    if args.limit:
        pairs = pairs[:args.limit]
    if not pairs:
        raise SystemExit("no cases after splitting -- nothing to replay")
    cases = build_cases(pairs, args.k, args.seed)

    P(f"=== action replay: {args.task} / split={args.split} / k={args.k} "
      f"/ seed={args.seed} / state={args.state_ruled_out} ===")
    print_info(info, P)
    v = {"checked": 0, "same": 0, "mismatch": []}
    if args.verify_sft:
        v = verify_against_sft(pairs, args.state_ruled_out)
        mode_note = "位元組相同" if args.state_ruled_out == "keep" else "是 sft_prime 狀態的前綴（消融模式）"
        state = "OK" if v["checked"] and v["same"] == v["checked"] else (
            "無法核對（artifact 不在）" if not v["checked"] else "有漂移")
        P(f"  渲染器核對  : {v['same']}/{v['checked']} 筆{mode_note} -> {state}")
        if v["mismatch"]:
            for cid in v["mismatch"][:5]:
                P(f"      mismatch: {cid}")
    P(f"  候選池      : 每題 {args.k-1} sibling + 自己 {info['rejected_claims']} 條否決負例"
      f"（總計 {sum(len(c['candidates']) for c in cases) - len(cases)} 個負例位置）")
    if args.split == "all":
        P("  警告        : split=all 只有在排序器**沒見過**這些案例時才是合法評估集。"
          "任何在 traces 上訓練過的東西一律用 --split valid。")
    P()

    results = []
    if args.scorer:
        if args.scorer == "command" and not args.scorer_command:
            raise SystemExit("--scorer command 需要 --scorer-command")
        results.append(run(cases, args.scorer, args.seed, args.scorer_command))
    else:
        for s in BUILTIN_SCORERS:
            results.append(run(cases, s, args.seed))
        if args.scorer_command:
            results.append(run(cases, "command", args.seed, args.scorer_command))

    for res in results:
        print_result(res, out=P)

    # 判讀放在數字下面，而且先講「雜訊」再講「誰贏」：n 小時最容易發生的錯
    # 是把 ±12 點的抖動讀成勝負（docstring 的 WEAK STATISTICS 那一段）。
    by_name = {r["scorer"]: r for r in results}
    P()
    for res in results:
        d_chance = res["top1"] - res["chance_top1"]
        readable = abs(d_chance) > res["top1_ci95_half_width"]
        beat_len = ""
        if res["scorer"] != "length" and "length" in by_name:
            key = "auc_vs_rejected_len_removed"
            lv = by_name["length"][key]["value"]
            mv = res[key]["value"]
            if lv is not None and mv is not None:
                beat_len = (f"  AUC_rej|len {mv:.3f} vs length 對照的 {lv:.3f} -> "
                            + ("在拔掉長度後贏過對照" if mv > lv + 0.05
                               else "**沒贏過長度對照：那是長度不是判斷**"))
        P(f"  判讀 {res['scorer']:>8}: top1 相對 chance {d_chance:+.3f} "
          f"({'可讀' if readable else '**在雜訊內**，不要說誰贏'}){beat_len}")

    if args.cases:
        for res in results:
            P(f"\n  --- {res['scorer']}: 每題名次 ---")
            for c in res["cases"]:
                if c["rank_pos"] > 1:
                    P(f"    {c['case_id'][:52]:54s} rank={c['rank_pos']:>2}/{c['pool']} "
                      f"被 {c['beaten_by']} 蓋過"
                      + (f"  auc_rej={c['auc_rejected']:.2f}" if c["auc_rejected"] is not None else ""))

    if args.report or args.out_dir:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out = args.out_dir or (OUT_ROOT / stamp)
        out.mkdir(parents=True, exist_ok=True)
        inputs = {p.name: C.sha16(p.read_bytes()) for p in (C.DECISIONS, C.LESSONS, C.EPISODES)}
        manifest = {
            "task": args.task, "split": args.split, "k": args.k, "seed": args.seed,
            "state_ruled_out": args.state_ruled_out, "limit": args.limit,
            "inputs_sha256_16": inputs, "sft_invocation": eff,
            "verify_against_sft": v,
            # 命令只記 sha256、不記原文：manifest 會被 commit，而命令列可能帶著 API key
            # （沿用 closed_loop.py 的同一條規則）。
            "scorer_command_sha256_16": (C.sha16(args.scorer_command.encode("utf-8"))
                                         if args.scorer_command else None),
            "generated_by": "agent_harness/engine_loop/distill/action_replay.py",
        }
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (out / "replay.json").write_text(
            json.dumps({"info": info, "results": results}, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n", encoding="utf-8")
        P(f"\n  寫入: {out}/manifest.json + replay.json")

    if args.json:
        print(json.dumps({"info": info, "results": results}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
