#!/usr/bin/env python3
"""r6_witness.py — R6 的**輸出見證**：把「單次提交臂的輸出與對照臂不同」從散文變成機器判詞。

端點（operator 2026-09-30）：見證本身是**配對的 M1/M2/M3**（同一 build、同一參考、同一 prompt，
唯一差異＝那面旗標）。判詞三態，與 `p1_feed_ab.py` 同一形狀（二值 ＋ fail-closed 的 REFUSE）：

  WITNESS_RED    對照臂位元等同（M1=M2=M3=n）而見證臂不是 ⇒ R6 的「輸出未驗」是**量到的**
                 ⇒ 該臂的 t/s 不是交付臂的函數（不可引用），解除條件未達成。
  WITNESS_GREEN  見證臂也位元等同 ⇒ 解除條件成立 ⇒ 該臂回到正常判準（`quote_gate` 的 R6 條目
                 要走它的解除路徑；看板那一格也要跟著重判）。
  REFUSE         配對不成立 ⇒ **紅**（缺欄位、跨 build、跨參考、prompt 不同、對照臂本身不是全綠、
                 見證臂其實沒開那面旗標…）。不放行是刻意的：見證的失敗方向若反過來，
                 「見證紅／綠」會變成一句誰都能寫的散文。

判準（跑前寫死；看板的 `witnesses` 段落與 docs/R6_SEGBATCH_FIX_PLAN_2026-09-30.md 同步）：

  ① 兩份都是 m123 的 summary（m1/m2/m3 ＋ n_compared 齊全），且都 `comparable=True`、
     `config_diffs=[]` —— 跨組態的趟次不得當見證（那正是 `--allow-incomparable` 的用途）。
  ② 同一 build：兩份 `engine_digest` 的每一個 artifact md5 完全相同（不然數字不可歸因）。
  ③ 同一錨：`ref` 與 `ref_md5` 相同、兩邊都 `ref_pinned=True`；`probe_prompt_md5` 相同、
     兩邊 `probe_prompt_match` 都不是 False。
  ④ 角色由 `extra_env` 判定：對照臂**沒有** `CGC_SEG_BATCH`、見證臂**有**（缺 `extra_env`
     ⇒ REFUSE，不得用猜的）。兩邊都開或都沒開 ⇒ REFUSE。
  ⑤ 對照臂必須 M1=M2=M3=n_compared（錨本身要綠——錨壞了什麼都不能歸因），**且**
     `coverage_pct=100`：這是參考來源的唯一證明。參考的 `.cap` 常是 2026-09-30 之前寫的
     （沒記 `probe_prompt_md5`，m123 自己會印 PROMPT UNKNOWN）——對照臂位元等同又把參考
     整份覆蓋，就等於把參考「復現」出來（prompt／組態都對上），見證臂的差異才歸因得到旗標。
  ⑥ 見證臂**不**要求覆蓋 100%：輸出一旦分歧，它的 dump 就會比參考長（這趟 72 個記錄
     只有 9 個對得上 = 12.5%），覆蓋率低是**分歧的症狀**，不是配對失敗。但至少要有可比記錄
     （`n_compared>0`），否則什麼都比不出來。
  ⑦ 判詞：見證臂 M1=M2=M3=n_compared ⇒ GREEN；任一項不足 ⇒ RED（附第一個分歧的 key 與兩邊雜湊）。

用法：
    python3 scripts/check/r6_witness.py \
        --control Backup/m123_oracle_gate/summary_r6ctl-nosegbatch-2026-09-30.json \
        --arm     Backup/m123_oracle_gate/summary_r6-segbatch-2026-09-30.json
    python3 scripts/check/r6_witness.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

FLAG = "CGC_SEG_BATCH"
GREEN, RED, REFUSE = "WITNESS_GREEN", "WITNESS_RED", "REFUSE"
VERDICTS = (GREEN, RED, REFUSE)


def _load(path: str, root: str | None = None) -> tuple[dict | None, str]:
    p = path if os.path.isabs(path) else os.path.join(root or os.getcwd(), path)
    if not os.path.isfile(p):
        return None, "產物不存在：%s" % path
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, "讀不出產物（%s）：%s" % (type(exc).__name__, exc)
    if not isinstance(d, dict):
        return None, "產物不是 mapping（%s）" % type(d).__name__
    return d, ""


def _metrics(s: dict) -> tuple[int, int] | None:
    """把 `m1_numeric_identity` 的 "k/n" 形字串解成 (k, n)；缺欄位或格式不合 ⇒ None。

    ⚠ 三個指標的 **n 必須相同**，但 k **本來就會不同**（RED 的形狀就是 M1 0/9、M2 2/9、
    M3 0/9）—— 一開始寫成「三個 (k,n) 要相等」會把正是要判的那種產物誤判成「不是 summary」
    （2026-09-30 selftest 抓到）。"""
    out = []
    for f in ("m1_numeric_identity", "m2_decision_agreement", "m3_topk_set_agreement"):
        v = s.get(f)
        if not isinstance(v, str) or "/" not in v:
            return None
        a, _, b = v.partition("/")
        try:
            out.append((int(a), int(b)))
        except ValueError:
            return None
    if len({n for _k, n in out}) != 1:
        return None
    return out[0][0], out[0][1]


def _engine_ids(s: dict) -> dict:
    """engine_digest → {artifact: md5}；只取 md5（mtime 不影響數值）。"""
    dig = s.get("engine_digest") or {}
    return {k: str((v or {}).get("md5")) for k, v in dig.items()} if isinstance(dig, dict) else {}


def _first_diverge(s: dict) -> dict | None:
    """從見證臂自己那份比對報告裡取第一個分歧（key ＋ 兩邊雜湊）。"""
    rep = s.get("report")
    if not rep or not os.path.isfile(rep):
        return None
    try:
        with open(rep, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        return None
    ex = (d.get("diff_examples") or [None])[0]
    if not isinstance(ex, dict):
        return None
    diffs = {}
    for row in (ex.get("diffs") or []):
        if isinstance(row, list) and len(row) == 3:
            diffs[str(row[0])] = [row[1], row[2]]
    return {"key": ex.get("key"), "diffs": diffs}


def judge(control: str, arm: str, root: str | None = None) -> dict:
    """回 {verdict, why, reasons, control, arm}；判不了 ⇒ REFUSE（fail-closed）。"""
    cb, cw = _load(control, root)
    ab, aw = _load(arm, root)
    reasons: list[str] = []
    if cb is None or ab is None:
        return dict(verdict=REFUSE, why="；".join(x for x in (cw, aw) if x), reasons=[cw or aw],
                    control=None, arm=None)

    # ① 兩份都要是 m123 的 summary，而且是**可比**的趟次
    for name, s in (("對照臂", cb), ("見證臂", ab)):
        if _metrics(s) is None:
            reasons.append("%s 不是 m123 的 summary（m1/m2/m3 欄位缺失或不成對）" % name)
        if s.get("comparable") is not True:
            reasons.append("%s 的 comparable≠True（跨組態的趟次不得當見證）" % name)
        elif s.get("config_diffs"):
            reasons.append("%s 的 config_diffs 非空（%s）" % (name, s["config_diffs"][:3]))
    ci, ai = _engine_ids(cb), _engine_ids(ab)

    # ② 同一 build
    if not ci or not ai:
        reasons.append("engine_digest 缺失（無法證明兩臂同 build）")
    elif ci != ai:
        diff = [k for k in sorted(set(ci) | set(ai)) if ci.get(k) != ai.get(k)]
        reasons.append("兩臂不同 build（engine_digest 差 %s）⇒ 數字不可歸因" % diff)

    # ③ 同一參考、同一 prompt
    if str(cb.get("ref")) != str(ab.get("ref")):
        reasons.append("兩臂的參考不同（ref 不一致）")
    if str(cb.get("ref_md5")) != str(ab.get("ref_md5")):
        reasons.append("兩臂的參考 md5 不同 ⇒ 比的是不同錨")
    if cb.get("ref_pinned") is not True or ab.get("ref_pinned") is not True:
        reasons.append("ref_pinned 不是 True（參考不在 REF_PINS 上）")
    if cb.get("probe_prompt_md5") != ab.get("probe_prompt_md5"):
        reasons.append("prompt 指紋不同 ⇒ 兩邊比的不是同一段內容")
    if cb.get("probe_prompt_match") is False or ab.get("probe_prompt_match") is False:
        reasons.append("probe_prompt_match=False（prompt 與參考自己的不合）")

    # ④ 角色由 extra_env 判定（不得用猜的）
    ce, ae = cb.get("extra_env"), ab.get("extra_env")
    if not isinstance(ce, dict) or not isinstance(ae, dict):
        reasons.append("extra_env 缺失（舊產物或旗標沒記）⇒ 判不出哪一邊是見證臂；"
                       "用帶這個欄位的場次（m123_oracle_gate 2026-09-30 之後）")
    else:
        if FLAG in ce:
            reasons.append("對照臂帶著 %s ⇒ 角色反了（對照臂必須是不開它的那一支）" % FLAG)
        if FLAG not in ae:
            reasons.append("見證臂沒有 %s ⇒ 這一趟不是那個見證" % FLAG)

    # ⑤ 錨本身要綠（M1=M2=M3=n），**而且**要把參考整份覆蓋（coverage 100）——這是參考來源
    #    的唯一證明：pre-2026-09-30 的參考 .cap 沒記 probe_prompt_md5（m123 會印 PROMPT
    #    UNKNOWN），對照臂位元等同 ＋ 覆蓋 100% ⇒ 參考已被「復現」，見證臂的差異才歸因得到旗標。
    cm = _metrics(cb)
    if cm and cm[0] != cm[1]:
        reasons.append("對照臂自己就不是全綠（M1 %d/%d）⇒ 錨壞了，什麼都不能歸因" % cm)
    if cb.get("coverage_pct") != 100:
        reasons.append("對照臂 coverage_pct=%s ≠ 100 ⇒ 參考沒被完整復現（來源不明，可能是別份 prompt）"
                       % cb.get("coverage_pct"))
    # ⑥ 見證臂**不**要求 coverage 100：輸出分歧本來就會讓它少覆蓋（分歧的症狀）。
    #    但要比得出東西 —— n=0 的「0/0」會讓 `am[0] == n` 成立而誤判 GREEN，這裡擋掉。
    am = _metrics(ab)
    if am is None or am[1] <= 0:
        reasons.append("見證臂沒有可比的記錄（M1=%s）⇒ 比不出來" % ab.get("m1_numeric_identity"))

    if reasons:
        return dict(verdict=REFUSE, why="；".join(reasons), reasons=reasons,
                    control=control, arm=arm)

    n = am[1]
    if am[0] == n:
        return dict(verdict=GREEN, why="見證臂位元等同（M1/M2/M3 %d/%d）⇒ R6 的解除條件成立" % (n, n),
                    reasons=[], control=control, arm=arm, n=n,
                    answers={"control": cb.get("probe_answer"), "arm": ab.get("probe_answer")})
    diverged = _first_diverge(ab)
    why = ("見證臂不是位元等同（M1 %d/%d、M2 %s、M3 %s）⇒ R6 的「輸出未驗」是量到的"
           % (am[0], n, ab.get("m2_decision_agreement"), ab.get("m3_topk_set_agreement")))
    if diverged and diverged.get("key") is not None:
        why += "；第一個分歧 key=%s" % (diverged["key"],)
        h = diverged["diffs"].get("row_fnv1a64")
        if h:
            why += "（row %s → %s）" % (h[0], h[1])
    return dict(verdict=RED, why=why, reasons=[], control=control, arm=arm, n=n,
                first_diverge=diverged,
                answers={"control": cb.get("probe_answer"), "arm": ab.get("probe_answer")})


# ────────────────────────────── selftest ──────────────────────────────
def _fix(tmp: str, **over) -> str:
    """一份最小的合法 summary；`over` 逐欄覆寫。"""
    d = {
        "tag": "fix", "profile": "prefill250", "ref": "/x/ref.jsonl", "ref_md5": "e1663cc10d529571",
        "ref_pinned": True, "probe_prompt_md5": "50f8c5ae120661bf", "probe_prompt_match": True,
        "coverage_pct": 100, "probe_answer": "42", "comparable": True, "config_diffs": [],
        "m1_numeric_identity": "9/9", "m2_decision_agreement": "9/9", "m3_topk_set_agreement": "9/9",
        "n_compared": 9, "engine_digest": {"libllama.0.dylib": {"md5": "c76358aa99"}},
        "extra_env": {},
    }
    d.update(over)
    p = os.path.join(tmp, "s_%d.json" % (len(os.listdir(tmp)) + 1))
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return p


def selftest() -> int:
    ok = total = 0

    def case(name, cond, extra=""):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print("  %-62s -> %s%s" % (name, "PASS" if cond else "FAIL", extra if not cond else ""))

    with tempfile.TemporaryDirectory() as tmp:
        # 角色判定用：見證臂必須真的帶著那面旗標
        arm_env = {FLAG: "1"}
        cases = [
            ("見證綠：兩臂皆 9/9 ⇒ WITNESS_GREEN",
             dict(arm=dict(m1_numeric_identity="9/9", extra_env=arm_env)), GREEN),
            ("見證紅：對照 9/9、見證 0/9 ⇒ WITNESS_RED",
             dict(arm=dict(m1_numeric_identity="0/9", m2_decision_agreement="2/9",
                           m3_topk_set_agreement="0/9", extra_env=arm_env)), RED),
            # 真產物的形狀：見證臂覆蓋只有 12.5%（dump 72 個、只有 9 個對得上）——
            # 覆蓋率低是分歧的症狀，不能把它當配對失敗（否則 RED 永遠到不了）。
            ("見證紅且覆蓋 12.5%（分歧的症狀）⇒ 仍判 WITNESS_RED",
             dict(arm=dict(m1_numeric_identity="0/9", m2_decision_agreement="2/9",
                           m3_topk_set_agreement="0/9", extra_env=arm_env,
                           coverage_pct=12.5, dump_records=72)), RED),
        ]
        for name, over, want in cases:
            c = _fix(tmp)
            a = _fix(tmp, **over["arm"])
            got = judge(c, a)["verdict"]
            case(name, got == want, "（得到 %s）" % got)

        refuse = [
            ("錨自己不是全綠 ⇒ REFUSE", dict(ctl=dict(m1_numeric_identity="5/9")),
             dict(arm=dict(extra_env=arm_env))),
            ("跨 build ⇒ REFUSE", dict(ctl={}),
             dict(arm=dict(extra_env=arm_env, engine_digest={"libllama.0.dylib": {"md5": "deadbeef"}}))),
            ("對照臂 coverage<100（參考沒被完整復現）⇒ REFUSE", dict(ctl=dict(coverage_pct=88.0)),
             dict(arm=dict(extra_env=arm_env))),
            ("見證臂沒有可比記錄（n=0）⇒ REFUSE", dict(ctl={}),
             dict(arm=dict(extra_env=arm_env, m1_numeric_identity="0/0",
                           m2_decision_agreement="0/0", m3_topk_set_agreement="0/0"))),
            ("參考 md5 不同 ⇒ REFUSE", dict(ctl={}), dict(arm=dict(extra_env=arm_env, ref_md5="other"))),
            ("prompt 指紋不同 ⇒ REFUSE", dict(ctl={}), dict(arm=dict(extra_env=arm_env, probe_prompt_md5="x"))),
            ("見證臂沒開旗標 ⇒ REFUSE", dict(ctl={}), dict(arm=dict(extra_env={}))),
            ("對照臂帶著旗標（角色反）⇒ REFUSE", dict(ctl=dict(extra_env=arm_env)),
             dict(arm=dict(extra_env={}))),
            ("缺 extra_env（舊產物）⇒ REFUSE", dict(ctl=dict(extra_env=None)),
             dict(arm=dict(extra_env=None))),
            ("跨組態（comparable=False）⇒ REFUSE", dict(ctl={}),
             dict(arm=dict(extra_env=arm_env, comparable=False, config_diffs=["ENV.X"]))),
        ]
        for name, ctl, armo in refuse:
            c = _fix(tmp, **ctl.get("ctl", {}))
            a = _fix(tmp, **armo["arm"])
            got = judge(c, a)["verdict"]
            case(name, got == REFUSE, "（得到 %s）" % got)

        # 「不是 summary」要真的丟一份非 summary 進去（用 _fix 會得到合法 summary，測不到）
        c = _fix(tmp)
        raw = os.path.join(tmp, "raw.json")
        with open(raw, "w", encoding="utf-8") as fh:
            json.dump({"note": "不是 summary"}, fh)
        got = judge(c, raw)["verdict"]
        case("見證臂不是 summary ⇒ REFUSE", got == REFUSE, "（得到 %s）" % got)

        # 缺檔 ⇒ REFUSE（不是例外）
        c = _fix(tmp)
        got = judge(c, os.path.join(tmp, "nope.json"))["verdict"]
        case("見證臂產物不存在 ⇒ REFUSE（不是當掉）", got == REFUSE, "（得到 %s）" % got)

        # 第一個分歧要被抓出來（從 report 的 diff_examples）
        rep = os.path.join(tmp, "cmp.json")
        with open(rep, "w", encoding="utf-8") as fh:
            json.dump({"diff_examples": [{"key": [0, 0, "DEF"],
                                          "diffs": [["row_fnv1a64", "30f3eb924b6617dd", "b322b408424a944b"]]}]}, fh)
        c = _fix(tmp)
        a = _fix(tmp, m1_numeric_identity="0/9", extra_env=arm_env, report=rep)
        r = judge(c, a)
        case("RED 會附上第一個分歧的 key 與兩邊雜湊", r["verdict"] == RED and
             r.get("first_diverge", {}).get("key") == [0, 0, "DEF"],
             "（得到 %s / %s）" % (r["verdict"], r.get("first_diverge")))

    print("SELFTEST %s（%d/%d）" % ("PASS" if ok == total else "FAIL", ok, total))
    return 0 if ok == total else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R6 輸出見證的配對檢查器（control vs 單次提交臂）")
    ap.add_argument("--control", help="對照臂（不開 CGC_SEG_BATCH）的 m123 summary")
    ap.add_argument("--arm", help="見證臂（開 CGC_SEG_BATCH）的 m123 summary")
    ap.add_argument("--json", help="把判詞寫成 JSON")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.control or not a.arm:
        print("需要 --control 與 --arm（或 --selftest）", file=sys.stderr)
        return 2
    res = judge(a.control, a.arm)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["verdict"] == GREEN else 1


if __name__ == "__main__":
    sys.exit(main())
