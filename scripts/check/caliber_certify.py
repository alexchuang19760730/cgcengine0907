#!/usr/bin/env python3
"""認證入口（口徑統一）：一個讀數**可不可以入認證表**，只有這一條判詞。

為什麼要它（operator 2026-10-01：「量測口徑一定要完全統一，這樣過 12+ 才可以直接認證並放在
已認證區」）
--------------------------------------------------------------------------------
今天站上的實況是**入口一致、口徑不一致**：`harness bench` 的 cell row 有一套判準
（`quote_gate` R1–R8），而 12+ 那一族是 server 的輪級聚合（R8c 判 REFUSE）、
`prod_profile.py` 的交付讀數又是第三種 schema（連判都判不了）。於是「過 12+」與
「放進已認證區」之間隔著一次**逐件裁定**。本工具把那一步機器化，且**不新寫任何一條判準**：

    可不可以入表  ⇔
      ① 口徑三條   : caliber_gate.classify(path) == UNIFIED
                    （入口＝harness bench 的 cell row、格子∈測試卡 §2.5、聚合＝逐 rep 向量）
      ② 引用四∼八  : quote_gate.judge(prod, row) == QUOTABLE（R1–R8；**目標 row 逐列判**）
      ③ **棘輪**   : 目標 row **嚴格高於現行可認證上限**（operator 2026-10-01：「要比現在的
                    已認證高才進去」）——上限＝看板的 `certify_anchor.decode_ts`（**單一來源**，
                    現值＝C8 的 11.982966）。**唯一的例外＝紀錄本人**：判的檔就是看板                     `certify_anchor.artifact` 指的那件產物時，以 `avg ≥ 上限 − 1e-4` 放行
                    （decode_ts 是手抄的顯示值）—— 否則「拿紀錄本人的產物重驗一次」必然失敗。
                    平手也不算高過（差 < 1e-9 視為同一件讀數）。下限（floor）另可用 --target-ts
                    指定（預設 0；政策值 12.0 用 `--target-ts 12`，一旦量到 12+ 就直接過）。
    ⚠ 棘輪是**入表**的條件：已入表的舊件重驗會得到「沒有高過現行上限 ⇒ REFUSED」，
      那不是「它當時沒過」，是「它現在已經在上限之下（被後來的更高讀數推過去了）」。
      ④ 格子仍在   : row 的格名 ∈ 現行 cell_contract.cell_names()
    四條缺一 ⇒ REFUSED。任何「判不了」（模組壞、讀不出、格子清單讀不到、**棘輪讀不到**）
    ⇒ REFUSED（fail-closed）—— 上限讀不到就不知道「有沒有比現在好」，那就不放行。

    `--board-row`：CERTIFIED 時多印一段**可直接貼**的 `certified:` 片段 —— 含 D17 要的
    `ratchet:`（`decode_ts` 逐字抄產物 avg_ts；`entered_above`＝入表當時的上限＝判這件時的
    看板上限；第一件＝0）。
    `--update-board`：這一條**過了棘輪**（avg 嚴格高於現行上限）時，把看板 `certify_anchor`
    推到新高（若 `certified` 還沒有這一列，順手補同一份片段進去），並**當場重跑看板閘門**
    （`decode_board_build.py --check`，D1–D17 含 D7 引用閘門）：推之前看板已紅 ⇒ 不碰；
    推之後紅 ⇒ **整檔回滾**（fail-closed —— 本工具不留紅的看板）。

⚠ 本工具**不搬動任何門檻**：MIN_REPS／SPREAD_LIMIT／QUOTABLE_PROFILES／THROUGHPUT_VOID_INSTRUMENTS／
UNVERIFIED_OUTPUT_ARMS 全部沿用 `quote_gate` 的常數；格子的定義沿用 `cell_contract`。
統一的是**入口與範圍**，不是放寬。

用法
----
    python3 scripts/check/caliber_certify.py certify <path> [--kind decode|prefill|any]
        [--target-ts 12.0] [--prefill-ts 250.0] [--anchor-ts V] [--board RWD]
        [--board-row] [--update-board] [--json out.json]
    python3 scripts/check/caliber_certify.py audit 'Backup/**/*.json' [--top 12] [--json out.json]
    python3 scripts/check/caliber_certify.py --selftest

rc：0 ＝ CERTIFIED；1 ＝ 沒有可認證的 row；2 ＝ 用法錯；3 ＝ 產物裡沒有任何可判的 row。

預註冊與「哪些算、哪些不算」的唯一落點：`docs/CALIBER_UNIFIED_PREREG_2026-10-01.md`
（立項卡 `scripts/check/charters/e-caliber-unified-2026-10-01.yaml`）。
"""
from __future__ import annotations

import argparse
import datetime
import glob as _glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import caliber_gate  # noqa: E402
import quote_gate  # noqa: E402

BOARD = os.path.join(HERE, "decode_board_2026-09-29.yaml")
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))

# 下限（floor）：**預設不設**——operator 2026-10-01 的規則是棘輪（「要比現在的已認證高才進去」），
# 因為「現在」本身就在往上跑。政策值 12.0（「過 12+ 直接認證」）用 `--target-ts 12` 加上去即可；
# DECODE_TARGET_TS 留作文件上的政策值，不再是隱藏門檻。
DECODE_TARGET_TS = 12.0
DECODE_FLOOR_DEFAULT = 0.0
PREFILL_TARGET_TS = 250.0

# 棘輪的兩個浮點常數：
#   RATCHET_EPS：「嚴格高於」只擋浮點噪聲 —— 差小於這個值就當**同一件讀數**（平手不算高過）。
#   HOLDER_EPS ：紀錄本人（judge 的檔＝certify_anchor.artifact）以 `≥ 上限 − 這個值` 放行：
#                decode_ts 往往是人手抄上去的**顯示值**（4 位小數），跟產物自己存的 avg_ts
#                可能差一個顯示級捨入（例如 avg_ts=11.199999… 而 decode_ts 寫 11.2）。
RATCHET_EPS = 1e-9
HOLDER_EPS = 1e-4

RC_OK, RC_NOT, RC_USAGE, RC_NOTHING = 0, 1, 2, 3


_CELLS = None


def read_anchor(board_path: str | None = None):
    """棘輪的上限：看板的 `certify_anchor`（**單一來源**）。回 (ts, why, artifact)。

    ts=None ⇒ 讀不到 ⇒ 呼叫端 fail-closed（不放行）：上限讀不到就不知道「有沒有比現在好」。
    `why` 一律附出處（cell／產物／日期），讓拒絕訊息自己說得出比較的對象是誰。
    `artifact` 是上限產物的路徑 —— 棘輪對它自己用 `≥`（紀錄本人），對別件用 `>`。
    """
    p = board_path or BOARD
    try:
        import yaml
        with open(p, encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, "看板讀不到（%s: %s）" % (type(exc).__name__, exc), None
    a = (d or {}).get("certify_anchor")
    if not isinstance(a, dict):
        return None, "看板沒有 certify_anchor —— 棘輪的上限沒有單一來源", None
    try:
        ts = float(a.get("decode_ts"))
    except (TypeError, ValueError):
        return None, "certify_anchor.decode_ts 不是數字（%r）" % (a.get("decode_ts"),), None
    art = a.get("artifact") or None
    return ts, "%s t/s（cell=%s、產物=%s、%s）" % (ts, a.get("cell") or "?",
                                                  art or "?", a.get("at") or "?"), art


def declared_cells() -> set:
    """測試卡 §2.5 今天的格子清單（與 caliber_gate／R8 同一份來源：cell_contract）。

    快取一次：審計要掃上千檔，而這是**同一個行程內不變**的宣告（跑一次載一次測試卡）。
    """
    global _CELLS
    if _CELLS is None:
        _CELLS = caliber_gate.declared_cells()
    return _CELLS


def retired_cell_names() -> set:
    """已退役的格名（2026-10-03：所有具名格退役，名字留墓碑）。

    退役名與「打錯的格名」走**不同**的判詞：前者是歷史（加註記、照常判 R1–R8），
    後者是錯誤（DIRTY）。來源與 `caliber_gate.retired_cells()` 同一份。
    """
    try:
        return set((caliber_gate.retired_cells() or {}).keys())
    except Exception:  # noqa: BLE001  讀不到 ⇒ 空集合（fail-closed：退役名會退回 DIRTY）
        return set()


def row_axis(row: dict) -> str:
    """這一列是 decode 還是 prefill（認證表的行只有這兩軸）。"""
    try:
        np_ = int(row.get("n_prompt") or 0)
        ng = int(row.get("n_gen") or 0)
    except (TypeError, ValueError):
        return "?"
    if np_ == 0 and ng > 0:
        return "decode"
    if ng == 0 and np_ > 0:
        return "prefill"
    return "?"


def target_for(axis: str, decode_target: float, prefill_target: float) -> float | None:
    if axis == "decode":
        return decode_target
    if axis == "prefill":
        return prefill_target
    return None


# ── 看板寫入（--board-row 的片段；--update-board 的推上限／補列／回滾）────────────────
# 這裡是**唯一**會寫看板的程式路徑：只改兩個地方（`certify_anchor:` 那一格、`certified:`
# 清單尾端），其餘位元組一個都不動 —— 看板的註解就是文件，不能用 yaml round-trip 洗掉。
def run_board_gate(board_path: str | None = None, build: bool = False):
    """當場重跑看板閘門（D1–D17，含 D7 引用閘門）。回 (rc, 尾段輸出)。

    驗證器的**唯一實作**＝ `decode_board_build.py`（不另寫第二套判準）：
      build=False ⇒ 只 `--check`（不寫任何檔；推之前的預檢用）。
      build=True  ⇒ 先 build（驗證 ＋ 重建產物），再 `--check` 逐字對 —— 推上限後用：
                    改了 YAML 就一定會讓 HTML「落後」（D4），不重建就不算驗完。
                    驗不過 ⇒ build 自己就不寫檔（cmd_build 的既有行為）。
    """
    base = [sys.executable, os.path.join(HERE, "decode_board_build.py")]
    extra = ["--yaml", board_path] if (board_path and os.path.abspath(board_path) != os.path.abspath(BOARD)) else []

    def _run(cmd):
        try:
            cp = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        except Exception as exc:  # noqa: BLE001
            return 99, "閘門跑不起來：%s: %s" % (type(exc).__name__, exc)
        out = (cp.stdout or "").strip() or (cp.stderr or "").strip()
        return cp.returncode, "\n".join(out.split("\n")[-6:])

    rc, tail = _run(base + extra)
    if rc == 0 and build:
        rc, tail = _run(base + ["--check"] + extra)
    return rc, tail


def _num(v) -> str:
    """看板要的數字字面：不四捨五入到顯示位（D17 的②就是逐字比對 `ratchet.decode_ts`）。"""
    return "%.10g" % float(v)


def _yaml_str(s) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _board_art(p: str) -> str:
    """寫進看板的產物路徑：repo 內→相對（看板風格）；repo 外→照給。"""
    ap = os.path.abspath(p)
    return os.path.relpath(ap, REPO_ROOT) if ap.startswith(REPO_ROOT + os.sep) else p


def _ratchet_row(rec: dict):
    """這一條裡「過了棘輪的 decode 讀數」（QUOTABLE ∧ meets）取最高那條；沒有 ⇒ None。

    `best` 是**全軸**最大值（prefill 296 > decode 12 是常態），所以推棘輪、寫片段都不能看它。
    """
    dec = [r for r in rec["rows"] if r.get("axis") == "decode" and r.get("avg") is not None]
    qual = [r for r in dec if r.get("verdict") == "QUOTABLE" and r.get("meets")]
    return max(qual, key=lambda x: x["avg"]) if qual else None


def board_row_lines(rec: dict, row_id: str = "C?") -> list:
    """CERTIFIED 的這一列 ⇒ 可直接貼進看板 `certified:` 的片段（list[str]）。

    `--board-row` 印它、`--update-board` 貼**同一份** ⇒ 兩條路吐的欄位不可能不一致。
    D17 的 `ratchet:`：`decode_ts` 逐字抄產物 avg_ts；`entered_above`＝入表當時的上限
    （＝判這件時的看板上限；第一件＝0）。唯一例外＝**紀錄本人**（avg≈現行上限）：這一列
    通常已經在表上，真要補列時 entered_above 得由人填「前一上限」⇒ 片段留**哨兵字串**
    （貼上去 D17 當場擋，不會靜默放行）。
    """
    b = rec["best"]
    dec = [r for r in rec["rows"] if r.get("axis") == "decode" and r.get("avg") is not None]
    rt = _ratchet_row(rec)
    art = _board_art(rec["path"])
    if rt:
        ra = rt.get("anchor")
        if rt.get("holder") or ra is None:
            item = "decode 棘輪（認證入口 caliber_certify；入表條件＝≥（紀錄本人））"
            rat_line = ("    ratchet: {decode_ts: %s, entered_above: 前一上限}   # ⚠ 紀錄本人／上限未指定："
                        "貼上前把「前一上限」填成數字（表上僅次於這個 decode 的那一列；第一件才填 0）"
                        % _num(rt["avg"]))
            rat_text = "棘輪（紀錄本人：以 ≥ 自己的上限重驗）"
        else:
            item = "decode 棘輪（認證入口 caliber_certify；入表條件＝> %s）" % _num(ra)
            rat_line = "    ratchet: {decode_ts: %s, entered_above: %s}" % (_num(rt["avg"]), _num(ra))
            rat_text = "棘輪（%s > %s）" % (_num(rt["avg"]), _num(ra))
    else:
        item = "%s 認證列（認證入口 caliber_certify）" % b["axis"]
        if dec:
            dd = max(dec, key=lambda x: x["avg"])
            rat_line = ("    ratchet_exempt: \"入表依據不是 decode 新高（%s 軸達標）；這件的 decode 讀數 %s"
                        " 沒有高過現行上限 %s ⇒ 不參與棘輪。\""
                        % (b["axis"], _num(dd["avg"]),
                           _num(dd["anchor"]) if dd.get("anchor") is not None else "?"))
            rat_text = "棘輪：這一列豁免（見 ratchet_exempt）"
        else:
            rat_line = "    # ⚠ 這件沒有 decode 讀數 ⇒ D17 不要求 ratchet／ratchet_exempt"
            rat_text = "棘輪：這件沒有 decode 讀數"
    val = rt if rt else b       # 這一列入表的**依據**那一軸：棘輪列＝decode 那條；其餘＝best
    why = ("口徑 UNIFIED ∧ quote_gate R1–R8（cell=%s、逐 rep %s、spread %s/%s、thermal=%s）∧ %s "
           "⇒ 認證入口判 CERTIFIED。" % (val["cell"], val["samples"], _fmt(val["spread_all"], "%.3f"),
                                          _fmt(val.get("spread_kept"), "%.3f"), val["thermal"], rat_text))
    return ["  - id: %s" % row_id,
            "    item: \"%s\"" % item,
            "    value: \"%s t/s（逐 rep %s；cell %s）\"" % (_num(val["avg"]), val["samples"], val["cell"]),
            "    profile: %s" % (val.get("profile") or "prod-new"),
            "    entry: \"harness bench\"",
            "    meets: true",
            rat_line,
            "    source: \"%s\"" % art,
            "    quote: {artifact: \"%s\", verdict: QUOTABLE, why: \"%s\"}" % (art, why)]


def board_text_with_row(text: str, snippet: list) -> str:
    """把一列貼進 `certified:` 清單尾端（下一個頂層鍵／註解之前；縮排與空行照看板風格）。"""
    lines = text.split("\n")
    i = next((k for k, l in enumerate(lines) if l.startswith("certified:")), None)
    if i is None:
        raise ValueError("看板沒有 certified: 這一節")
    k, j = i, i + 1
    while j < len(lines) and (not lines[j].strip() or lines[j][:1] in (" ", "\t")):
        if lines[j].strip():
            k = j
        j += 1
    head, tail = lines[:k + 1], lines[j:]
    while tail and not tail[0].strip():
        tail.pop(0)
    return "\n".join(head + [""] + list(snippet) + [""] + tail)


def board_text_with_anchor(text: str, ts, cell, artifact, at: str, note: str) -> str:
    """把 `certify_anchor:` 那一格改寫成新高（回新全文；其餘位元組一個都不動）。

    舊的 `why:` 沿革**保留**，自動推升的句子接在後面 —— 下一次推升時人還看得到歷史。
    """
    lines = text.split("\n")
    i = next((k for k, l in enumerate(lines) if l.startswith("certify_anchor:")), None)
    if i is None:
        raise ValueError("看板沒有 certify_anchor: 這一格")
    j = i + 1
    while j < len(lines) and lines[j].strip() and lines[j][:1] in (" ", "\t"):
        j += 1
    while j < len(lines) and not lines[j].strip():
        nxt = next((l for l in lines[j:] if l.strip()), "")
        if nxt[:1] not in (" ", "\t"):      # 空白後接的是下一個頂層鍵 ⇒ 那是區塊外的分隔行
            break
        j += 1
    old_why = None
    for l in lines[i:j]:
        m = re.match(r'^\s*why: "(.*)"\s*$', l)
        if m:
            old_why = m.group(1)
    why = (old_why + " ｜ " + note) if old_why else note
    new = ["certify_anchor:",
           "  decode_ts: %s" % _num(ts),
           "  cell: \"%s\"" % _yaml_str(cell),
           "  artifact: \"%s\"" % _yaml_str(artifact),
           "  at: \"%s\"" % _yaml_str(at),
           "  why: \"%s\"" % _yaml_str(why)]
    return "\n".join(lines[:i] + new + lines[j:])


def _board_has_row(text: str, art: str) -> bool:
    """表上已經有這件產物的列了嗎（比對 `quote.artifact`；只看不寫，不會洗掉註解）。"""
    try:
        import yaml
        d = yaml.safe_load(text) or {}
    except Exception:  # noqa: BLE001
        return False
    want = os.path.normpath(art)
    for c in (d.get("certified") or []):
        a = ((c or {}).get("quote") or {}).get("artifact")
        if a and os.path.normpath(a) == want:
            return True
    return False


def _next_cert_id(text: str) -> str:
    ids = [int(m) for m in re.findall(r"^\s*- id: C(\d+)\s*$", text, flags=re.M)]
    return "C%d" % (max(ids) + 1 if ids else 1)


def _indent(text: str) -> str:
    return "\n".join("    " + l for l in (text or "").split("\n"))


def board_update(recs, paths, anchor_ts, board_path, gate) -> int:
    """--update-board：這一條過棘輪 ⇒ 看板上限推到新高（必要時補列）＋當場重跑閘門。

    fail-closed 三段：① 推之前看板已紅 ⇒ 不碰；② 推之後閘門紅 ⇒ 整檔**回滾**；
    ③ 不是「嚴格新高」／不是 decode 軸 ⇒ 一個位元組都不動。回 rc（0＝看板最後是綠的）。
    """
    cert = [r for r in recs if r["certified"]]
    if len(cert) != 1 or len(paths) != 1:
        print("--update-board：這次有 %d/%d 條 CERTIFIED ⇒ 一次只推一件（看板不動）"
              % (len(cert), len(paths)))
        return RC_OK if cert else RC_NOT
    rec = cert[0]
    rt = _ratchet_row(rec)
    if rt is None:
        print("--update-board：這一條沒有「decode 過棘輪」的讀數 ⇒ 不動（棘輪只管 decode；"
              "prefill 達標不會推 decode 上限）")
        return RC_OK
    if not float(rt["avg"]) > float(anchor_ts) + RATCHET_EPS:
        print("--update-board：decode avg %s 沒有**嚴格高於**現行上限 %s ⇒ 不推（平手不算新高；"
              "舊件／紀錄本人本來就在表上）" % (_num(rt["avg"]), _num(anchor_ts)))
        return RC_OK
    bp = board_path or BOARD
    if not os.path.exists(bp):
        print("--update-board：看板檔不存在（%s）⇒ 不推" % bp)
        return RC_NOT
    rc, tail = gate(bp)
    if rc != 0:
        print("--update-board：⛔ 看板**現在就是紅的** ⇒ 不推（先修綠再來）：\n%s" % _indent(tail))
        return RC_NOT
    with open(bp, encoding="utf-8") as fh:
        original = fh.read()
    art = _board_art(rec["path"])
    text, added = original, None
    try:
        if not _board_has_row(original, art):
            added = _next_cert_id(original)
            text = board_text_with_row(text, board_row_lines(rec, added))
        note = ("caliber_certify --update-board 自動推升：%s → %s（cell=%s、產物=%s）；D17：上限＝"
                "certified 表上最高的 decode、artifact＝那一列的 quote.artifact。"
                % (_num(anchor_ts), _num(rt["avg"]), rt["cell"], art))
        text = board_text_with_anchor(text, rt["avg"], rt["cell"], art,
                                      datetime.date.today().isoformat(), note)
    except ValueError as exc:
        print("--update-board：⛔ %s ⇒ 不推" % exc)
        return RC_NOT
    with open(bp, "w", encoding="utf-8") as fh:
        fh.write(text)
    rc2, tail2 = gate(bp, True)      # 驗證（D1–D17）＋重建產物（不然 D4 一定說 HTML 落後）＋逐字對
    if rc2 != 0:
        with open(bp, "w", encoding="utf-8") as fh:
            fh.write(original)
        rc3, tail3 = gate(bp, True)  # 產物也重建回原樣（build 決定性；原狀本來就是綠的）
        print("--update-board：⛔ 推完重跑閘門沒過 ⇒ **已回滾**（看板逐字回到推之前）：\n%s" % _indent(tail2))
        if rc3 != 0:
            print("  ⚠ 回滾後的重建也紅了（那代表推之前就沒有真的綠；看它的輸出）：\n%s" % _indent(tail3))
        return RC_NOT
    print("--update-board：✅ 棘輪 %s → %s（artifact=%s）%s；閘門重跑（重建＋--check）PASS：\n%s"
          % (_num(anchor_ts), _num(rt["avg"]), art,
             ("；已補列 %s" % added) if added else "；這一列已在表上（只推上限）", _indent(tail2)))
    print("  ⚠ 看板字面（summary／certified_note 的「最高 …」）請順手同步 —— D17 不管字面，"
          "但同一頁要講同一個數。")
    return RC_OK


def judge_file(path: str, kind: str = "any", decode_target: float = DECODE_FLOOR_DEFAULT,
               prefill_target: float = PREFILL_TARGET_TS, anchor_ts=None,
               board_path: str | None = None, anchor_why: str | None = None,
               anchor_artifact: str | None = None) -> dict:
    """判一個產物：回 {caliber, cell, rows:[...], certified: bool, best: {...}|None, notes:[...]}。

    `anchor_ts=None` ⇒ 從看板讀棘輪上限（fail-closed：讀不到就不放行）；顯式給值則用它
    （selftest 與重播用，避免測試與看板現值綁死）。
    棘輪唯一的例外＝**紀錄本人**（判的檔＝certify_anchor.artifact）：以 `≥ 上限` 放行。
    """
    out = dict(path=path, caliber=None, caliber_why="", cell="?", rows=[], certified=False,
               best=None, notes=[], anchor_ts=None, anchor_why="", anchor_error=False,
               anchor_artifact=None, record_holder=False)
    if anchor_ts is None:
        anchor_ts, anchor_why, anchor_artifact = read_anchor(board_path)
    elif anchor_why is None:
        anchor_why = "%.4g（命令列指定）" % float(anchor_ts)
    out["anchor_ts"], out["anchor_why"] = anchor_ts, anchor_why
    out["anchor_artifact"] = anchor_artifact
    out["anchor_error"] = anchor_ts is None
    if out["anchor_error"]:
        out["notes"].append("棘輪：%s ⇒ 不判可認證（fail-closed；確要繞過用 --anchor-ts）" % anchor_why)
    out["record_holder"] = bool(anchor_artifact) and \
        os.path.realpath(path) == os.path.realpath(anchor_artifact)
    if out["record_holder"]:
        out["notes"].append("棘輪：這件就是 certify_anchor 指的上限產物（紀錄本人）⇒ 以 ≥ 自己的上限重驗")
    if not os.path.exists(path):
        out["notes"].append("產物不存在")
        return out
    # ④ 格子清單（fail-closed：讀不到 ⇒ 不判可認證）
    # ⚠ 順序：**先**取清單再判口徑。口徑那一條要用同一份清單才分得出「退役（歷史）」與
    # 「未宣告（改名／打錯）」—— 兩者判詞不同（2026-10-03：具名格全部退役）。
    cells = declared_cells()
    retired = retired_cell_names()
    if not cells:
        out["notes"].append("測試卡 §2.5 的格子清單讀不到 ⇒ 格子這一條判不了（fail-closed）")
    # ① 口徑三條
    try:
        v, why = caliber_gate.classify(path, cells, retired)
    except Exception as exc:  # noqa: BLE001
        v, why = "UNKNOWN", "caliber_gate 判不了：%s: %s" % (type(exc).__name__, exc)
    out["caliber"], out["caliber_why"] = v, why
    if v != "UNIFIED":
        out["notes"].append("口徑：%s ⇒ %s" % (v, why))
    # ②③ 逐 row 判引用與達標（唯一實作：quote_gate）
    try:
        stream = list(quote_gate.iter_rows([path]))
    except Exception as exc:  # noqa: BLE001
        out["notes"].append("quote_gate 判不了：%s: %s" % (type(exc).__name__, exc))
        return out
    for p, prod, row, pre_v, pre_why in stream:
        if pre_v:
            out["rows"].append(dict(axis="?", shape="?", avg=None, spread_all=None, spread_kept=None,
                                    attrib="?", thermal="?", verdict=pre_v, reasons=list(pre_why),
                                    samples=None))
            continue
        try:
            verdict, reasons, m = quote_gate.judge(prod, row)
        except Exception as exc:  # noqa: BLE001
            verdict, reasons, m = "REFUSE", ["quote_gate.judge 爆掉：%s: %s"
                                             % (type(exc).__name__, exc)], {}
        ax = row_axis(row)
        floor = target_for(ax, decode_target, prefill_target)
        ratchet = anchor_ts if ax == "decode" else None
        avg = m.get("avg")
        ok_floor = avg is not None and ((floor or 0) <= 0 or float(avg) >= float(floor))
        ok_rat = not out["anchor_error"] and (ratchet is None or (
            avg is not None and (float(avg) > float(ratchet) + RATCHET_EPS
                                 or (out["record_holder"]
                                     and float(avg) >= float(ratchet) - HOLDER_EPS))))
        meets = bool(avg is not None and ok_floor and ok_rat)
        miss = []
        if avg is not None:
            if (floor or 0) > 0 and float(avg) < float(floor):
                miss.append("下限：%s %.4g < floor %.4g" % (ax, avg, floor))
            if not ok_rat and not out["anchor_error"] and ratchet is not None:
                miss.append("棘輪：%s %.4g ≤ 現行上限 %.4g（要比現在的已認證高才進去；"
                            "已入表的舊件重驗也落在這裡）" % (ax, avg, ratchet))
            if out["anchor_error"]:
                miss.append("棘輪讀不到 ⇒ fail-closed")
        cell = m.get("cell") or "?"
        if cell in retired:
            # 2026-10-03 具名格全部退役之後：退役名**不是**「宣告被改名／拿掉」。這一條抓的是
            # 「改名 ⇒ 舊數字被靜默重新錨定」，而退役留有墓碑（名字、prompt、日期都在）。
            # 這一行照常過 R1–R8 —— 它是「歷史可讀」，不是「可拿它當新讀數的格子」。
            out["notes"].append(
                "格名 %r 已退役（2026-10-03）⇒ 這一行的數字僅供歷史對照，"
                "不可再作為新讀數的格子（現行唯一可選＝'(default)'：llama-bench 出廠形狀）。" % cell)
        elif cell != "?" and cells and cell not in cells:
            verdict, reasons = "DIRTY", list(reasons) + [
                "權威格：格子 %r 不在現行測試卡 §2.5 的宣告裡（宣告被改名／拿掉 ⇒ 這一行不是這一格）" % cell]
        out["rows"].append(dict(
            axis=ax, shape="p%s/n%s/d%s" % (row.get("n_prompt"), row.get("n_gen"), row.get("n_depth")),
            avg=avg, spread_all=m.get("all_spread"), spread_kept=m.get("kept_spread"),
            attrib=m.get("attrib"), thermal=m.get("thermal"), cell=cell, profile=m.get("profile"),
            reps=m.get("reps"), samples=m.get("samples"), target=floor, anchor=ratchet,
            holder=out["record_holder"], meets=meets, miss=miss, verdict=verdict,
            reasons=list(reasons)))
        if kind in ("any", ax) and verdict == "QUOTABLE" and meets:
            if out["best"] is None or (avg is not None and float(avg) > float(out["best"]["avg"])):
                out["best"] = dict(axis=ax, avg=avg, target=floor, anchor=ratchet, cell=cell,
                                   profile=m.get("profile"), spread_all=m.get("all_spread"),
                                   spread_kept=m.get("kept_spread"), samples=m.get("samples"),
                                   thermal=m.get("thermal"), holder=out["record_holder"])
    if not out["rows"]:
        out["notes"].append("產物裡沒有任何可判的 llama-bench row（不是統一 schema？走統一入口重跑："
                            "`python3 scripts/check/cell_contract.py --cell <name>` 會印那一條 harness bench 命令）")
    # `RETIRED` 與 `UNIFIED` 同權：退役格**跑不出新產物**（`cell_contract.resolve_cell` 對
    # 退役名 fail-closed），所以一個退役格的產物只能是舊的 ⇒ 這一條路只會被用來**重驗歷史**。
    out["certified"] = (bool(out["best"]) and out["caliber"] in ("UNIFIED", "RETIRED")
                        and bool(cells) and not out["anchor_error"])
    return out


def _fmt(v, spec="%.4g"):
    return "—" if v is None else (spec % v)


def report_file(rec: dict, board_row: bool) -> None:
    print("  %s" % rec["path"])
    print("    口徑   %-12s %s" % (rec["caliber"], rec["caliber_why"]))
    for r in rec["rows"]:
        print("    %-7s %-14s t/s %-9s spread %s/%s  attrib %-6s thermal %-8s %s"
              % (r["axis"], r["shape"], _fmt(r["avg"]), _fmt(r["spread_all"], "%.3f"),
                 _fmt(r.get("spread_kept"), "%.3f"), r["attrib"], r["thermal"], r["verdict"]))
        for why in r["reasons"][:3]:
            print("        · %s" % why)
        if r["verdict"] == "QUOTABLE" and not r["meets"]:
            for m in (r.get("miss") or ["（可引用但未過收貨條件）"]):
                print("        · %s" % m)
    for n in rec["notes"]:
        print("    ⚠ %s" % n)
    if rec["certified"]:
        b = rec["best"]
        cond = " ∧ ".join(x for x in (
            (("%s ≥%s（棘輪・紀錄本人）" if b.get("holder") else "%s >%s（棘輪）")
             % (b["axis"], _fmt(b["anchor"]))) if b.get("anchor") is not None else "",
            ("%s ≥%s（下限）" % (b["axis"], _fmt(b["target"]))) if (b.get("target") or 0) > 0 else "") if x)
        print("    ⇒ CERTIFIED：%s %.4g t/s（%s；cell=%s、profile=%s、spread %s/%s、thermal=%s）"
              % (b["axis"], b["avg"], cond or "—", b["cell"], b["profile"],
                 _fmt(b["spread_all"], "%.3f"), _fmt(b["spread_kept"], "%.3f"), b["thermal"]))
        if board_row:
            print()
            print("  # ── 可直接貼進看板 certified:（id 由 operator 給；貼上去時把 certify_anchor 推到這個值）──")
            for line in board_row_lines(rec):
                print(line)
    else:
        print("    ⇒ REFUSED（見上列理由）")


def cmd_certify(paths, kind, decode_target, prefill_target, anchor_ts, board_path,
                board_row, json_out, update_board=False, gate_runner=None) -> int:
    anchor, anchor_why, anchor_artifact = anchor_ts, None, None
    if anchor is None:
        anchor, anchor_why, anchor_artifact = read_anchor(board_path)
    else:
        anchor_why = "%.4g（命令列指定）" % float(anchor_ts)
    print("認證入口 caliber_certify：口徑三條（caliber_gate UNIFIED） ∧ 引用四∼八（quote_gate R1–R8）"
          " ∧ 棘輪（decode **嚴格高於**現行上限） ∧ 下限（decode ≥%s／prefill ≥%s） ∧ 格子仍在（§2.5）"
          % (_fmt(decode_target, "%.4g"), _fmt(prefill_target, "%.4g")))
    print("  棘輪單一來源＝看板的 certify_anchor：%s" % (anchor_why or "（讀不到）"))
    if anchor is None:
        print("  ⛔ 讀不到 ⇒ 全部 REFUSED（fail-closed；確必要時用 --anchor-ts 顯式指定）")
    print()
    recs, cert = [], 0
    for p in paths:
        r = judge_file(p, kind, decode_target, prefill_target, anchor_ts=anchor,
                       anchor_why=anchor_why, anchor_artifact=anchor_artifact)
        recs.append(r)
        report_file(r, board_row or update_board)
        cert += 1 if r["certified"] else 0
    print()
    print("VERDICT: %d/%d CERTIFIED" % (cert, len(paths)))
    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(recs, fh, ensure_ascii=False, indent=1)
        print("  （帳已寫入 %s）" % json_out)
    if update_board:
        return board_update(recs, paths, anchor, board_path, gate_runner or run_board_gate)
    if cert:
        return RC_OK
    return RC_NOTHING if all(not r["rows"] for r in recs) else RC_NOT


def cmd_audit(patterns, top, decode_target, prefill_target, anchor_ts, board_path, json_out) -> int:
    anchor, anchor_why, anchor_artifact = anchor_ts, None, None
    if anchor is None:
        anchor, anchor_why, anchor_artifact = read_anchor(board_path)
    else:
        anchor_why = "%.4g（命令列指定）" % float(anchor_ts)
    files = []
    for pat in patterns:
        files += sorted(set(_glob.glob(pat, recursive=True)))
    files = [f for f in files if os.path.isfile(f)]
    print("認證入口審計：%d 檔；棘輪＝%s；下限 decode ≥%s／prefill ≥%s（判準＝caliber_certify 的四條）"
          % (len(files), _fmt(anchor, "%.4g"), _fmt(decode_target, "%.4g"), _fmt(prefill_target, "%.4g")))
    buckets = {"CERTIFIED": [], "QUOTABLE_NOT_ABOVE_ANCHOR": [], "REFUSED": [],
               "NOT_JUDGEABLE": [], "UNREADABLE": []}
    for p in files:
        try:
            with open(p, encoding="utf-8") as fh:
                json.load(fh)
        except Exception:
            buckets["UNREADABLE"].append((p, "讀不出 JSON"))
            continue
        r = judge_file(p, "any", decode_target, prefill_target, anchor_ts=anchor,
                       anchor_why=anchor_why, anchor_artifact=anchor_artifact)
        if not r["rows"]:
            buckets["NOT_JUDGEABLE"].append((p, r["notes"][-1] if r["notes"] else ""))
        elif r["certified"]:
            buckets["CERTIFIED"].append((p, "%s %.4g t/s" % (r["best"]["axis"], r["best"]["avg"])))
        elif any(x["verdict"] == "QUOTABLE" for x in r["rows"]):
            best = max((x for x in r["rows"] if x["verdict"] == "QUOTABLE"),
                       key=lambda x: (x["avg"] or 0))
            buckets["QUOTABLE_NOT_ABOVE_ANCHOR"].append(
                (p, "%s %.4g t/s（可引用，但沒有高過 %s）" % (best["axis"], best["avg"], _fmt(anchor, "%.4g"))))
        else:
            why = next((x["reasons"][0] for x in r["rows"] if x["reasons"]), "")
            buckets["REFUSED"].append((p, why[:110]))
    for k in ("CERTIFIED", "QUOTABLE_NOT_ABOVE_ANCHOR", "REFUSED", "NOT_JUDGEABLE", "UNREADABLE"):
        print()
        print("== %s : %d ==" % (k, len(buckets[k])))
        for p, why in buckets[k][:top]:
            print("   %-64s %s" % (p, why))
        if len(buckets[k]) > top:
            print("   …（其餘 %d 筆；--top 調量）" % (len(buckets[k]) - top))
    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(buckets, fh, ensure_ascii=False, indent=1)
        print("\n（帳已寫入 %s）" % json_out)
    return RC_OK if buckets["CERTIFIED"] else RC_NOT


# ───────────────────────────── selftest ─────────────────────────────
def _std(samples):
    n = len(samples)
    mean = sum(samples) / n
    return (sum((x - mean) ** 2 for x in samples) / (n - 1)) ** 0.5


def _prod(samples, cell_name="delivery", rep_reported=None, **over):
    """一個**通過全部閘門**的合成 bench 產物；負控制用 over 逐項弄髒。"""
    p = {
        "profile": "prod-new", "tag": "prod-new", "extra_env": {},
        "attribution": {"verdict": "none", "thermal_worst": "NOMINAL"},
        "contract": {"ok": True, "mismatches": [], "cell": cell_name},
        "cell": {"prompt": 0, "gen": 128, "depths": 512, "reps": 3, "warm_skip": 64,
                 "ctx_size": 4096, "named_cell": cell_name},
        "warm_skip_applied": True,
        "rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512, "avg_ts": sum(samples) / len(samples),
                  "stddev_ts": rep_reported if rep_reported is not None else _std(samples),
                  "samples_ts": list(samples)}],
    }
    p.update(over)
    return p


def selftest() -> int:
    import contextlib
    import io
    import tempfile
    import yaml
    ok = total = 0

    def case(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %-58s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                    "" if cond else " " + str(detail)))

    tmp = tempfile.mkdtemp()

    def w(name, obj):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return p

    # ① 乾淨的 12+ decode 產物 ⇒ CERTIFIED
    p = w("ok12.json", [_prod([12.1, 12.3, 12.2])])
    r = judge_file(p, anchor_ts=11.0)
    case("乾淨 12.2 t/s（cell=delivery、reps 3、attrib none）⇒ CERTIFIED", r["certified"], r["notes"])
    case("  └ best 是 decode 12.2", r["best"] and r["best"]["axis"] == "decode"
         and abs(r["best"]["avg"] - 12.2) < 1e-9, r["best"])
    # ② QUOTABLE 但沒過棘輪（11.2 ≤ 上限 11.5）⇒ REFUSED（且**不是**因為閘門）
    p = w("below.json", [_prod([11.1, 11.3, 11.2])])
    r = judge_file(p, anchor_ts=11.5)
    case("乾淨 11.2 t/s ⇒ QUOTABLE、但沒過棘輪（≤11.5）⇒ REFUSED",
         (not r["certified"]) and any(x["verdict"] == "QUOTABLE" for x in r["rows"]), r["notes"])
    case("  └ miss 說得出棘輪（11.2 ≤ 11.5）",
         any("棘輪" in m for x in r["rows"] for m in (x.get("miss") or [])), r["rows"][0])
    # ③ 髒窗（attribution=swap）⇒ DIRTY ⇒ REFUSED
    p = w("dirty.json", [_prod([12.1, 12.3, 12.2],
                               attribution={"verdict": "swap", "thermal_worst": "NOMINAL"})])
    r = judge_file(p, anchor_ts=11.0)
    case("attribution=swap ⇒ DIRTY、不認證",
         (not r["certified"]) and r["rows"][0]["verdict"] == "DIRTY", r["rows"][0]["verdict"])
    # ④ R5 量具臂 ⇒ DIRTY
    p = w("r5.json", [_prod([12.1, 12.3, 12.2], extra_env={"CGC_RHO_PROBE": "1"})])
    r = judge_file(p, anchor_ts=11.0)
    case("臂上 CGC_RHO_PROBE（R5）⇒ DIRTY、不認證",
         (not r["certified"]) and r["rows"][0]["verdict"] == "DIRTY", r["rows"][0])
    # ⑤ R6 未驗輸出臂 ⇒ DIRTY
    p = w("r6.json", [_prod([12.1, 12.3, 12.2], extra_env={"CGC_SEG_BATCH": "1"})])
    r = judge_file(p, anchor_ts=11.0)
    case("臂上 CGC_SEG_BATCH（R6）⇒ DIRTY、不認證",
         (not r["certified"]) and r["rows"][0]["verdict"] == "DIRTY", r["rows"][0])
    # ⑥ 離散超限 ⇒ UNSTABLE
    p = w("spread.json", [_prod([12.1, 12.4, 13.4])])
    r = judge_file(p, anchor_ts=11.0)
    case("逐 rep max/min 1.107>1.10 ⇒ UNSTABLE、不認證",
         (not r["certified"]) and r["rows"][0]["verdict"] == "UNSTABLE", r["rows"][0])
    # ⑦ reps<3 ⇒ THIN
    p = w("thin.json", [_prod([12.1, 12.2])])
    r = judge_file(p, anchor_ts=11.0)
    case("reps=2<3 ⇒ THIN、不認證",
         (not r["certified"]) and r["rows"][0]["verdict"] == "THIN", r["rows"][0])
    # ⑧ 格子未宣告 ⇒ 口徑 NON_UNIFIED
    p = w("nocell.json", [_prod([12.1, 12.3, 12.2], cell_name="no-such-cell")])
    r = judge_file(p, anchor_ts=11.0)
    case("未宣告格 ⇒ NON_UNIFIED、不認證",
         (not r["certified"]) and r["caliber"] == "NON_UNIFIED", r["caliber"])
    # ⑨ 輪級聚合（12+ 那一族）⇒ REFUSE（R8c）
    p = w("round.json", [{"profile": "prod-new", "tag": "prod-new", "n_rounds": 3,
                          "decode_tps_median": 12.9, "rounds": [{"decode_tps": 12.97}]}])
    r = judge_file(p, anchor_ts=11.0)
    case("輪級聚合（n_rounds／decode_tps_median）⇒ REFUSE、不認證",
         (not r["certified"]) and r["rows"] and r["rows"][0]["verdict"] == "REFUSE"
         and "輪級" in r["rows"][0]["reasons"][0], r["rows"][0] if r["rows"] else r["notes"])
    # ⑩ 外來 schema（prod_profile 風格）⇒ NOT_JUDGEABLE，且訊息指向統一入口
    p = w("foreign.json", {"tool": "prod_profile.py", "created": "2026-09-20T20:41:25",
                           "axes": {"decode": [{"rows": [{"t/s": 12.5, "samples_ts": [12.1, 12.3, 12.2]}]}]}})
    r = judge_file(p, anchor_ts=11.0)
    case("外來 schema ⇒ 沒有可判 row（fail-closed）",
         (not r["certified"]) and not r["rows"] and any("統一入口" in n for n in r["notes"]),
         r["notes"])
    # ⑪ R0 內部不自洽（回報 stddev 與樣本不符）⇒ REFUSE
    p = w("liar.json", [_prod([12.1, 12.3, 12.2], rep_reported=0.9)])
    r = judge_file(p, anchor_ts=11.0)
    case("回報 stddev 與逐 rep 不符 ⇒ REFUSE、不認證",
         (not r["certified"]) and r["rows"][0]["verdict"] == "REFUSE", r["rows"][0])
    # ⑫ kind 過濾：只有 prefill 達標時，--kind decode 不得認證
    # prefill 行要落在**宣告了 prompt 的格**上（delivery 是 decode-only：prompt=0 ⇒ (2048,0) 不是它的行）
    _pp = _prod([12.1, 12.3, 12.2], cell_name="(default)")
    _pp["cell"]["prompt"] = 2048
    _pp["rows"] = [{"n_prompt": 2048, "n_gen": 0, "n_depth": 512, "avg_ts": 260.0, "stddev_ts": 0.1,
                    "samples_ts": [259.0, 261.0, 260.0]}]
    p = w("pp.json", [_pp])
    r_any = judge_file(p, kind="any", anchor_ts=11.0)
    r_dec = judge_file(p, kind="decode", anchor_ts=11.0)
    case("prefill 260 達標 ⇒ CERTIFIED（any）", r_any["certified"], r_any["best"])
    case("同一檔用 --kind decode ⇒ 不認證（只有 prefill 達標）",
         (not r_dec["certified"]) and r_dec["rows"][0]["verdict"] == "QUOTABLE", r_dec["best"])

    # ⑬ 棘輪讀不到 ⇒ fail-closed（上限讀不到就不曉得「有沒有比現在好」）
    p = w("below2.json", [_prod([11.1, 11.3, 11.2])])
    r = judge_file(p, board_path=os.path.join(tmp, "no_such_board.yaml"))
    case("看板檔不存在 ⇒ 棘輪讀不到 ⇒ fail-closed、不認證",
         (not r["certified"]) and r["anchor_error"]
         and any("fail-closed" in n for n in r["notes"]), r["notes"])
    ts, why, art = read_anchor(os.path.join(tmp, "no_such_board.yaml"))
    case("  └ read_anchor 回 (None, why, artifact=None)",
         ts is None and art is None and ("certify_anchor" in why or "看板讀不到" in why), why)
    bd_nokey = os.path.join(tmp, "board_no_key.yaml")
    with open(bd_nokey, "w", encoding="utf-8") as fh:
        json.dump({"summary": []}, fh)
    r = judge_file(p, board_path=bd_nokey)
    case("看板沒有 certify_anchor ⇒ 一樣 fail-closed（不是默認 0）",
         (not r["certified"]) and r["anchor_error"] and "certify_anchor" in r["anchor_why"],
         r["anchor_why"])
    # ⑭ 紀錄本人：判的檔就是 certify_anchor.artifact ⇒ 以 ≥ 自己的上限重驗
    holder = w("holder.json", [_prod([11.1, 11.3, 11.2])])
    bd = os.path.join(tmp, "board_anchor.json")
    with open(bd, "w", encoding="utf-8") as fh:
        json.dump({"certify_anchor": {"decode_ts": 11.2, "cell": "delivery",
                                       "artifact": holder, "at": "selftest"}}, fh)
    ts, why, art = read_anchor(bd)
    case("  └ read_anchor 讀回 (11.2, why, artifact)",
         ts == 11.2 and art == holder and "11.2 t/s" in why, (ts, why, art))
    r = judge_file(holder, board_path=bd)
    case("紀錄本人（avg＝上限 11.2）⇒ 不擋自己 ⇒ CERTIFIED",
         r["certified"] and r["record_holder"] and r["best"] and r["best"]["holder"], r["notes"])
    # ⑮ 同一份看板、同樣的 11.2，但換一件檔 ⇒ 棘輪＝**嚴格 >** ⇒ REFUSED
    p2 = w("not_holder.json", [_prod([11.1, 11.3, 11.2])])
    r = judge_file(p2, board_path=bd)
    case("同值但不是上限產物（不是紀錄本人）⇒ 平手不算進 ⇒ REFUSED",
         (not r["certified"]) and not r["record_holder"]
         and any("棘輪" in m for x in r["rows"] for m in (x.get("miss") or [])), r["rows"][0])

    # ⑯ --board-row 的片段要帶 D17 的 ratchet（decode_ts 逐字、entered_above＝入表時的上限）
    p = w("row12.json", [_prod([12.1, 12.3, 12.2])])
    r = judge_file(p, anchor_ts=11.0)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        report_file(r, True)
    printed = out.getvalue()
    m = re.search(r"ratchet: \{decode_ts: ([\d.]+), entered_above: ([\d.]+)\}", printed)
    case("--board-row：片段帶 ratchet（decode_ts 逐字、entered_above＝入表時的上限）",
         bool(m) and abs(float(m.group(1)) - 12.2) < 1e-9 and float(m.group(2)) == 11.0,
         printed[-400:])
    wrapped = yaml.safe_load("certified:\n" + "\n".join(board_row_lines(r, "C99")))
    wr = (wrapped or {}).get("certified") or [{}]
    case("  └ 片段是合法 YAML、D17 欄位齊（id／ratchet／quote.artifact）",
         wr[0].get("id") == "C99" and wr[0].get("ratchet", {}).get("decode_ts") == 12.2
         and wr[0].get("quote", {}).get("artifact") == p, wrapped)
    # ⑰ 紀錄本人：entered_above 是「前一上限」⇒ 片段留哨兵（不靜默填 0）
    rh = judge_file(holder, board_path=bd)
    hlines = "\n".join(board_row_lines(rh, "C98"))
    case("紀錄本人：片段把 entered_above 留成哨兵（貼上去 D17 當場擋）",
         "entered_above: 前一上限" in hlines and "紀錄本人" in hlines, hlines[-300:])
    # ⑰b 有 decode 讀數但沒過棘輪（入表依據＝prefill）⇒ ratchet_exempt＋why
    _pe = _prod([11.1, 11.3, 11.2], cell_name="(default)")
    _pe["cell"]["prompt"] = 2048
    _pe["rows"].append({"n_prompt": 2048, "n_gen": 0, "n_depth": 512, "avg_ts": 260.0,
                         "stddev_ts": 0.1, "samples_ts": [259.0, 261.0, 260.0]})
    pe = w("prefill_and_decode.json", [_pe])
    r_pe = judge_file(pe, anchor_ts=12.0)
    ex = "\n".join(board_row_lines(r_pe, "C97"))
    case("有 decode 讀數但沒過棘輪 ⇒ 片段改用 ratchet_exempt＋why（不是硬寫 ratchet）",
         r_pe["certified"] and "ratchet_exempt:" in ex and "沒有高過現行上限 12" in ex, ex)

    # ⑱ --update-board：過棘輪 ⇒ 推上限＋補列＋當場重跑閘門（stub；真閘門＝D1–D17）
    base_board = ("id: tmp\n"
                  "certify_anchor:\n"
                  "  decode_ts: 11.0\n"
                  "  cell: \"(default)\"\n"
                  "  artifact: \"old.json\"\n"
                  "  at: \"2026-09-30\"\n"
                  "  why: \"舊上限\"\n"
                  "certified:\n"
                  "  - id: C1\n"
                  "    quote: {artifact: \"old.json\", verdict: QUOTABLE}\n"
                  "\n"
                  "summary:\n"
                  "  - {value: \"11.0\"}\n")

    def mkboard(name, txt=base_board):
        p_ = os.path.join(tmp, name)
        with open(p_, "w", encoding="utf-8") as fh:
            fh.write(txt)
        return p_

    hp = w("high12.json", [_prod([12.1, 12.3, 12.2])])
    bp1 = mkboard("board_upd1.yaml")
    calls = []

    def gate_ok(b_, build=False):
        calls.append((b_, build))
        return 0, "VERDICT: PASS（0 個問題）"

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cmd_certify([hp], "any", 0.0, PREFILL_TARGET_TS, None, bp1, False, None,
                         update_board=True, gate_runner=gate_ok)
    txt = open(bp1, encoding="utf-8").read()
    d = yaml.safe_load(txt)
    crow = next((c for c in d["certified"] if c.get("id") == "C2"), None)
    case("--update-board：上限推到 12.2、artifact＝新件（append＋anchor 同一次寫）",
         rc == RC_OK and abs(float(d["certify_anchor"]["decode_ts"]) - 12.2) < 1e-9
         and bool(crow) and crow["quote"]["artifact"] == d["certify_anchor"]["artifact"],
         (rc, d["certify_anchor"]))
    case("  └ 新列 C2 的 ratchet 寫對（12.2 > entered_above 11）",
         bool(crow) and abs(float(crow["ratchet"]["decode_ts"]) - 12.2) < 1e-9
         and float(crow["ratchet"]["entered_above"]) == 11.0, crow)
    case("  └ 閘門跑了兩次（推前只驗；推後 build＋--check）、rc=0",
         len(calls) == 2 and calls[0][1] is False and calls[1][1] is True and rc == RC_OK, calls)
    case("  └ 舊 why 沿革保留、自動推升句接上",
         "舊上限" in d["certify_anchor"]["why"] and "自動推升" in d["certify_anchor"]["why"],
         d["certify_anchor"]["why"][:120])
    case("  └ 其他位元組不動（id／summary 沒被 yaml round-trip 洗掉）",
         txt.startswith("id: tmp\n") and "summary:\n  - {value: \"11.0\"}\n" in txt)
    # ⑲ 推完閘門紅 ⇒ 整檔回滾（逐字）
    bp2 = mkboard("board_upd2.yaml")
    seen = []

    def gate_red_after(b_, build=False):
        seen.append(build)
        if len(seen) == 2:
            return 1, "VERDICT: FAIL（1 個問題）\n  ✗ D17 certify_anchor.decode_ts ≠ 表上最大的 decode_ts"
        return 0, "VERDICT: PASS（0 個問題）"

    before = open(bp2, encoding="utf-8").read()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cmd_certify([hp], "any", 0.0, PREFILL_TARGET_TS, None, bp2, False, None,
                         update_board=True, gate_runner=gate_red_after)
    case("推完閘門紅 ⇒ **整檔回滾**（逐字＝推之前）＋產物也重建回去＋rc=1",
         rc == RC_NOT and open(bp2, encoding="utf-8").read() == before
         and len(seen) == 3 and seen[1] is True and seen[2] is True
         and "已回滾" in out.getvalue(), (seen, out.getvalue()[-300:]))
    # ⑳ 紀錄本人（avg＝上限）⇒ 不推：一個位元組都不動
    bph = mkboard("board_holder12.yaml", base_board.replace("11.0", "12.2").replace("old.json", hp))
    calls2 = []

    def gate_never(b_, build=False):
        calls2.append((b_, build))
        return 0, "PASS"

    before = open(bph, encoding="utf-8").read()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cmd_certify([hp], "any", 0.0, PREFILL_TARGET_TS, None, bph, False, None,
                         update_board=True, gate_runner=gate_never)
    case("紀錄本人（avg＝上限）⇒ 不推：一個位元組都不動、閘門不用跑",
         rc == RC_OK and open(bph, encoding="utf-8").read() == before and not calls2
         and "不推" in out.getvalue(), (rc, out.getvalue()[-200:]))
    # ㉒ best 是 prefill 但 decode 過棘輪 ⇒ 推的仍是 decode（best 是全軸最大，prefill 幾乎總是最大）
    bp4 = mkboard("board_upd4.yaml")
    calls4 = []

    def gate_ok4(b_, build=False):
        calls4.append((b_, build))
        return 0, "VERDICT: PASS（0 個問題）"

    _pd = _prod([12.1, 12.3, 12.2], cell_name="(default)")
    _pd["cell"]["prompt"] = 2048
    _pd["rows"].append({"n_prompt": 2048, "n_gen": 0, "n_depth": 512, "avg_ts": 260.0,
                         "stddev_ts": 0.1, "samples_ts": [259.0, 261.0, 260.0]})
    pd12 = w("prefill_best_decode12.json", [_pd])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cmd_certify([pd12], "any", 0.0, PREFILL_TARGET_TS, None, bp4, False, None,
                         update_board=True, gate_runner=gate_ok4)
    d4 = yaml.safe_load(open(bp4, encoding="utf-8").read())
    case("best 是 prefill（260）但 decode 過棘輪 ⇒ 推的仍是 decode 上限 12.2",
         rc == RC_OK and abs(float(d4["certify_anchor"]["decode_ts"]) - 12.2) < 1e-9
         and len(calls4) == 2, d4["certify_anchor"])
    # ㉑ 推之前看板就紅 ⇒ 不碰（fail-closed）
    bp3 = mkboard("board_upd3.yaml")
    red = []

    def gate_always_red(b_, build=False):
        red.append((b_, build))
        return 1, "VERDICT: FAIL（1 個問題）"

    before = open(bp3, encoding="utf-8").read()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cmd_certify([hp], "any", 0.0, PREFILL_TARGET_TS, None, bp3, False, None,
                         update_board=True, gate_runner=gate_always_red)
    case("看板推之前就是紅的 ⇒ 不碰（逐字不動、只驗一次、rc=1）",
         rc == RC_NOT and open(bp3, encoding="utf-8").read() == before and len(red) == 1
         and "現在就是紅的" in out.getvalue(), (rc, out.getvalue()[-200:]))

    print("SELFTEST %d/%d" % (ok, total))
    return 0 if ok == total else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="認證入口（口徑統一）")
    ap.add_argument("cmd", nargs="?", choices=["certify", "audit"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--kind", choices=["decode", "prefill", "any"], default="any")
    ap.add_argument("--target-ts", type=float, default=DECODE_FLOOR_DEFAULT,
                    help="decode 的**下限**（預設 0＝只套棘輪；政策值 12.0 用 --target-ts 12）")
    ap.add_argument("--prefill-ts", type=float, default=PREFILL_TARGET_TS)
    ap.add_argument("--anchor-ts", type=float, default=None,
                    help="棘輪上限（預設＝讀看板的 certify_anchor；顯式指定用於重播／測試）")
    ap.add_argument("--board", dest="board_path", default=None, help="看板路徑（預設 scripts/check/decode_board_2026-09-29.yaml）")
    ap.add_argument("--board-row", action="store_true", help="CERTIFIED 時加印可貼的 certified: 片段")
    ap.add_argument("--update-board", action="store_true",
                    help="過棘輪時把看板 certify_anchor 推到新高（必要時把這一列補進 certified），"
                         "當場重跑看板閘門（decode_board_build --check，D1–D17）；重跑沒過就整檔回滾")
    ap.add_argument("--top", type=int, default=12, help="audit 每類最多印幾筆")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.cmd or not a.paths:
        ap.print_help()
        return RC_USAGE
    if a.cmd == "certify":
        return cmd_certify(a.paths, a.kind, a.target_ts, a.prefill_ts, a.anchor_ts,
                           a.board_path, a.board_row, a.json_out, a.update_board)
    return cmd_audit(a.paths, a.top, a.target_ts, a.prefill_ts, a.anchor_ts,
                     a.board_path, a.json_out)


if __name__ == "__main__":
    sys.exit(main())
