#!/usr/bin/env python3
"""主張標籤：把「這個 t/s 可不可以主張、主張時必須寫什麼」印成一段可直接貼的文字。

為什麼要它：operator 授權「attribution 非 none 也可主張 t/s」，但那種主張**很容易被斷章取義**
—— 下一手只看見「11.47 t/s」而看不見「attribution=swap」。與其靠每個人記得加註，不如讓機器
把「必須同時寫出的條件」連同數字一起吐出來。

用法：
    python3 scripts/check/claim_label.py <artifact.json> [<artifact.json> ...]
    python3 scripts/check/claim_label.py --selftest

輸出是一段可直接貼進報告／commit 訊息的文字，例如：

    decode 11.47 t/s（attribution=swap、swap_growth=2885 MiB、thermal NOMINAL；
    逐 rep [11.81, 11.27, 11.32]；非認證值、不進 C 表；依 allow-nonquote-tps-2026-09-30）
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
POLICY = os.path.join(HERE, "claim_policy.yaml")


def _load_policy():
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        return yaml.safe_load(open(POLICY, encoding="utf-8"))
    except Exception:
        return None


def active_ruling(policy=None, allow_load=True):
    """回傳今日有效的裁定（找不到／過期 ⇒ None ⇒ 不得主張）。

    ⚠ `policy=None` 原本會回頭去載真政策 ⇒ 「讀不到 policy」這個狀況根本測不到
    （判例看起來 PASS，其實沒在測）。用 `allow_load=False` 才是真的在測「沒有政策」。
    """
    p = policy if policy is not None else (_load_policy() if allow_load else None)
    if not p:
        return None
    today = _dt.date.today().isoformat()
    for r in (p.get("rulings") or []):
        exp = str(r.get("expires") or "")
        if exp and exp < today:
            continue
        return r
    return None


def _dig(o, key, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                acc.append(v)
            _dig(v, key, acc)
    elif isinstance(o, list):
        for v in o:
            _dig(v, key, acc)


def artifact_facts(path):
    """取出主張時需要的那幾個欄位；讀不到就回 None（不猜）。"""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    out = {}
    a = []
    _dig(d, "attribution", a)
    at = a[0] if a else None
    if isinstance(at, dict):
        out["attribution"] = at.get("verdict")
        out["swap_growth"] = at.get("swap_growth_mb")
        out["thermal"] = at.get("thermal_worst")
    s = []
    _dig(d, "samples_ts", s)
    # decode row＝最後一組（前一組通常是 prefill）。
    # ⚠ `_dig` 會把整個 `samples_ts` 的值塞進來：有的產物是「一列一個 key」（拿到扁平的 rep 列），
    #   有的是「一個 key 裝兩列」（拿到 [[prefill],[decode]]）⇒ 兩種形狀都要認，不然同一支閘門
    #   在別人的產物上會讀到空的 rep 列而報「缺資料」。
    grp = s[-1] if s else None
    if isinstance(grp, list) and grp and isinstance(grp[0], list):
        grp = grp[-1]
    out["reps"] = grp
    raw_t = []
    _dig(d, "platform_ts", raw_t) or _dig(d, "tg", raw_t)
    # 同 samples_ts：有的產物「一列一個 key」（拿到純量），有的「一個 key 裝兩列」（拿到 list）
    # ⇒ 攤平成純數字列，下面才能安全地取「最接近 rep 均值」的那一個。
    t = []
    for v in raw_t:
        if isinstance(v, list):
            t.extend([float(x) for x in v if isinstance(x, (int, float))])
        elif isinstance(v, (int, float)):
            t.append(float(v))
    # ⚠ 一次 launch 會量兩行（prefill 行、decode 行），`_dig` 的第一個命中是 **prefill**
    #   ⇒ 第一版把 276.62（prefill）當成 decode 印出去。decode 值＝與逐 rep 均值最接近的那一個。
    # 只接受純數字列：`samples_ts` 有時是「每臂一組」的巢狀結構，直接 sum 會炸，
    # 而這種炸法會讓整支閘門在別人的產物上崩掉（不能這樣）。
    raw = out.get("reps") or []
    reps = [float(x) for x in raw if isinstance(x, (int, float))]
    if reps:
        out["reps"] = reps
    else:
        out["reps"] = None
    mean = sum(reps) / len(reps) if reps else None
    if t:
        out["tg"] = min(t, key=lambda v: abs(float(v) - mean)) if (mean is not None and len(t) > 1) \
            else t[-1]
        out["tg_how"] = "platform"
    elif reps:
        out["tg"] = sum(reps) / len(reps)
        out["tg_how"] = "逐 rep 均值"
    return out


def label(path, ruling=None, policy=None):
    r = ruling if ruling is not None else active_ruling(policy)
    f = artifact_facts(path)
    name = os.path.basename(path)
    if f is None:
        return "  %-28s ⇒ 讀不到產物 ⇒ 不得主張（fail-closed）" % name
    if r is None:
        return "  %-28s ⇒ 沒有有效的 operator 裁定 ⇒ 不得主張（依 quote_gate：非 QUOTABLE 即不可引用）" % name
    missing = [k for k in ("attribution", "swap_growth", "thermal", "reps") if f.get(k) is None]
    if missing:
        return "  %-28s ⇒ 缺 %s ⇒ 不得主張（裁定要求這些必須同時寫出）" % (name, "、".join(missing))
    return ("  %-28s ⇒ decode %.2f t/s（%s；attribution=%s、swap_growth=%.0f MiB、thermal %s；"
            "逐 rep %s；**非認證值、不進 C 表**；依 %s）"
            % (name, float(f["tg"]), f.get("tg_how") or "平臺", f["attribution"],
               float(f["swap_growth"]), f["thermal"], f["reps"], r.get("id")))


def cmd(paths):
    pol = _load_policy()
    r = active_ruling(pol)
    if pol is None:
        print("  ⚠ 讀不到 claim_policy.yaml ⇒ 不得主張（fail-closed）")
        return 1
    if r is None:
        print("  ⚠ 沒有有效的 operator 裁定（可能已過期）⇒ 一律不得主張")
    for p in paths:
        print(label(p, ruling=r))
    return 0


def cmd_selftest():
    import tempfile
    ok = total = 0

    def case(n, cond, detail=""):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %-52s -> %s%s" % (n, "PASS" if cond else "FAIL",
                                    "" if cond else " " + str(detail)))

    # 過期的裁定不得生效
    p = {"rulings": [{"id": "old", "expires": "2000-01-01"}]}
    case("過期裁定 ⇒ 不得主張", active_ruling(p) is None)
    p2 = {"rulings": [{"id": "new", "expires": "2999-01-01"}]}
    case("有效裁定 ⇒ 可主張", (active_ruling(p2) or {}).get("id") == "new")
    case("沒有 policy ⇒ 不得主張", active_ruling(None, allow_load=False) is None)
    case("真的政策檔讀得到（否則一律不得主張）", _load_policy() is not None)
    # 缺欄位 ⇒ 不得主張（避免只報數字）
    tmp = tempfile.mkdtemp()
    f = os.path.join(tmp, "x.json")
    json.dump({"samples_ts": [[1, 2, 3]], "tg": 11.0}, open(f, "w"))
    lab = label(f, ruling={"id": "r"})
    case("缺 attribution ⇒ 不得主張", "不得主張" in lab, lab)
    # 完整 ⇒ 產出可貼的標籤，且含「非認證值」
    json.dump({"samples_ts": [[1, 2, 3], [11.8, 11.2, 11.3]], "tg": 11.47,
               "attribution": {"verdict": "swap", "swap_growth_mb": 2885,
                               "thermal_worst": "NOMINAL"}}, open(f, "w"))
    lab = label(f, ruling={"id": "r"})
    case("完整 ⇒ 可貼標籤且含『非認證值』", "非認證值" in lab and "11.47" in lab, lab)
    case("標籤含 attribution 與 swap_growth", "attribution=swap" in lab and "2885" in lab, lab)
    # 兩行（prefill／decode）時，主張的必須是 decode 那一行
    f2 = os.path.join(tmp, "y.json")
    json.dump({"samples_ts": [[270.0, 276.0, 275.0], [11.8, 11.27, 11.32]],
               "platform_ts": [276.62, 11.295],
               "attribution": {"verdict": "swap", "swap_growth_mb": 2885,
                               "thermal_worst": "NOMINAL"}}, open(f2, "w"))
    lab2 = label(f2, ruling={"id": "r"})
    case("不得把 prefill 行當 decode 主張", "11.29" in lab2 and "276.62" not in lab2, lab2)
    print("SELFTEST PASS（%d/%d）" % (ok, total))
    return 0 if ok == total else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="主張標籤（operator 授權＋強制標註）")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return cmd_selftest()
    if not a.paths:
        ap.print_help()
        return 2
    return cmd(a.paths)


if __name__ == "__main__":
    sys.exit(main())
