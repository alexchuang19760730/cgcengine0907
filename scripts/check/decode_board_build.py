#!/usr/bin/env python3
"""decode 子目標看板的產生器（單一來源 ⇒ 三個產物）。

唯一來源
--------
  scripts/check/decode_board_2026-09-29.yaml   ← 判定、預期、立項、結案狀態（人寫）
  docs/mindmap/mindmap.json                    ← 節點的 name/tier/sub/targets/res/docs（機器寫）

產物（全部**生成**，不要手改）
------------------------------
  docs/mindmap/prefill250decode20.html         子目標看板（掛進 mindmap 總圖）
  docs/mindmap/subgoals/<id>.html|.md          逐子目標頁（預期／現況／驗收／立項／結案 ＋ 對應節點與 log 連結）
  docs/mindmap/subgoals/index.html|.md         左表：15 個子目標 × 結案狀態

用法
----
  python3 scripts/check/decode_board_build.py             # 生成全部產物
  python3 scripts/check/decode_board_build.py --check      # 只驗：YAML 合法 ＋ 無漂移 ＋ 產物未落後（rc=1 有問題）
  python3 scripts/check/decode_board_build.py --selftest   # 內建案例（不碰 repo 檔案）

漂移（drift）＝ --check rc=1 的六類
----------------------------------
  D1 引用了 mindmap 裡不存在的節點
  D2 有「未結案」節點既沒被子目標覆蓋、也沒被分類（＝完備性稽核）
  D3 欄位不合規（缺預期／分類缺欄／立項不存在…）
  D4 產物落後於 YAML（有人手改 HTML，或忘了重跑）
  D5 立項（charter）指到的檔案不存在
  D6 標了「結案」卻不滿足結案規則（prod-new ＋ harness bench ＋ 達標）——唯一例外是「結案（排除）」要有出處
  D7 引用閘門（quote_gate）：子目標宣告的可引用性必須與閘門**當場**判的一致；且「結案」不得
     建立在不可引用的讀數上（`attribution=none` 不再是充分的理由 —— 見 §55）
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_YAML = os.path.join(HERE, "decode_board_2026-09-29.yaml")
MINDMAP = os.path.join(ROOT, "docs", "mindmap", "mindmap.json")
PAGES_DIR = os.path.join(ROOT, "docs", "mindmap", "subgoals")

LIVE_TIERS = {"1", "2", "3a", "3b"}

# 徽章色 → CSS 類別。`danger` 是**別名**：2026-09-29 另一個執行緒在 L20-3 寫了
# `tone: danger`（「前置否證」），而合法的既有紅色類別是 `b-dead`。把別名加進來
# 比改別人的資料列安全，也比讓整個 build 掛掉好（視覺與 b-dead 相同）。
TONE_CLASS = {"ok": "b-ok", "warn": "b-warn", "info": "b-info", "dead": "b-dead", "danger": "b-dead"}
BADGE_TONES = tuple(TONE_CLASS) + (None,)
CLASSES = {"handled", "done", "goal", "c-axis", "account", "no-claim", "recheck"}
NEEDS = {
    "handled": ("by",), "done": ("why",), "goal": ("why",), "c-axis": ("why",),
    "account": ("why", "impact"), "no-claim": ("why",), "recheck": ("why",),
}
TARGET_FIELDS = ("id", "title", "nodes", "now", "action", "accept", "falsify",
                 "expect_ms", "expect_tps", "state", "evidence", "badge", "stage")

CSS = """  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans TC',Roboto,sans-serif; line-height:1.6; color:#1a1b1c; background:#f8f9fa; }
  .container { max-width:1280px; margin:0 auto; padding:36px 20px; }
  h1 { font-size:27px; font-weight:700; margin-bottom:8px; }
  .subtitle { font-size:14.5px; color:#6b7280; margin-bottom:24px; }
  h2 { font-size:20px; font-weight:600; margin:34px 0 12px; padding-bottom:7px; border-bottom:2px solid #e5e7eb; }
  h3 { font-size:16.5px; font-weight:600; margin:20px 0 9px; }
  .card { background:#fff; border-radius:12px; padding:18px 22px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); gap:14px; }
  .stat { text-align:center; padding:16px; }
  .stat-value { font-size:29px; font-weight:700; color:#2563eb; }
  .stat-value.ok { color:#059669; } .stat-value.bad { color:#dc2626; } .stat-value.warn { color:#b45309; }
  .stat-label { font-size:12.5px; color:#6b7280; margin-top:4px; }
  table { width:100%; border-collapse:collapse; margin:12px 0; }
  th,td { padding:8px 10px; text-align:left; border-bottom:1px solid #e5e7eb; vertical-align:top; }
  th { background:#f3f4f6; font-weight:600; font-size:12.5px; white-space:nowrap; }
  td { font-size:13px; }
  code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-family:'SF Mono',Monaco,monospace; font-size:12px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; white-space:nowrap; }
  .layer-met { border-left:6px solid #059669; background:#ecfdf5; padding:6px 12px; border-radius:6px; }
  .b-ok { background:#d1fae5; color:#065f46; } .b-warn { background:#fef3c7; color:#92400e; }
  .b-info { background:#dbeafe; color:#1e40af; } .b-dead { background:#f3f4f6; color:#6b7280; }
  ul,ol { margin:8px 0 12px 22px; } li { margin-bottom:5px; font-size:13.5px; }
  .warning { background:#fffbeb; border-left:4px solid #f59e0b; padding:13px 15px; border-radius:0 8px 8px 0; margin:12px 0; font-size:13.5px; }
  .success { background:#ecfdf5; border-left:4px solid #10b981; padding:13px 15px; border-radius:0 8px 8px 0; margin:12px 0; font-size:13.5px; }
  .info { background:#eff6ff; border-left:4px solid #3b82f6; padding:13px 15px; border-radius:0 8px 8px 0; margin:12px 0; font-size:13.5px; }
  .danger { background:#fef2f2; border-left:4px solid #dc2626; padding:13px 15px; border-radius:0 8px 8px 0; margin:12px 0; font-size:13.5px; }
  .steps { display:inline-flex; gap:3px; vertical-align:middle; }
  .steps i { width:13px; height:7px; border-radius:2px; background:#e5e7eb; display:inline-block; }
  .steps i.on { background:#2563eb; }
  .legend { font-size:12.5px; color:#6b7280; }
  nav { background:#fff; border-bottom:1px solid #e5e7eb; padding:10px 20px; font-size:13.5px; margin-bottom:0; }
  nav a { color:#2563eb; text-decoration:none; margin-right:16px; }
  footer { margin-top:40px; padding-top:14px; border-top:1px solid #e5e7eb; font-size:12.5px; color:#6b7280; }"""

PAGE_CSS = CSS + """
  .kv { width:100%; } .kv th { width:150px; background:#fff; font-size:13px; }
  .verdict { font-size:15px; font-weight:600; }"""


# ────────────────────────── 讀取 ──────────────────────────
def load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_mindmap(path=None):
    with open(path or MINDMAP, encoding="utf-8") as fh:
        return json.load(fh)


def index_nodes(mm):
    return {e.get("id"): e for e in mm.get("entries", [])}


def live_nodes(nodes):
    return sorted(i for i, e in nodes.items() if str(e.get("tier")) in LIVE_TIERS)


def esc(t):
    return str(t)


def rel_from(here, dest):
    """產物搬到哪裡都成立：連結一律相對『本頁所在目錄』計算。"""
    return os.path.relpath(dest, here)


def nav(here, board_out, n_targets=15):
    return ('<nav><a href="%s">◀ 攻關總圖</a><a href="%s">子目標看板</a>'
            '<a href="%s">逐子目標（%d）</a></nav>'
            % (rel_from(here, os.path.join(ROOT, "docs/mindmap/index.html")),
               rel_from(here, board_out),
               rel_from(here, os.path.join(PAGES_DIR, "index.html")), n_targets))


# ────────────────────────── 驗證 ──────────────────────────
def layer_goal(layer, root=ROOT):
    """主節點（L20／L25）是否達標：**只有可引用的讀數能讓它變綠**（§55 的閘門精神）。

    回 (met, why)。met=False ⇒ 不變色、不加任何記號（「若無則沒有變化」）；
    met=None ⇒ 這一層沒有宣告 goal／evidence（不變色）。`why` 一律寫進 <h2 title="…">，
    所以「為什麼沒變綠」在 HTML 裡查得到，不必靠推測。
    """
    g = layer.get("goal") or {}
    ev = layer.get("evidence") or {}
    if not g and not ev:
        return None, ""
    if not ev:
        return None, "本層未宣告 evidence（達標必須指名一場可引用的產物）"
    target, val = g.get("target"), ev.get("value")
    metric = g.get("metric", "tps")
    crit = ("（%s）" % g["crit"]) if g.get("crit") else ""
    verdict = ev.get("quote_verdict")
    head = "目標 %s ≥ %s%s" % (metric, target, crit)
    if target is None or val is None:
        return None, head + "；本層的目標或實測值缺一 ⇒ 不判定"
    met = (float(val) >= float(target)) and verdict == "QUOTABLE"
    body = "；目前最好的可引用讀數 %s（引用閘門 %s）" % (val, verdict or "未判")
    note = ("；" + str(ev["note"])) if ev.get("note") else ""
    return met, head + body + (" ⇒ 達標" if met else " ⇒ 未達標（不變色）") + note


def gate_verdict(root, artifact):
    """跑引用閘門判該產物：回 (verdict, why)。

    verdict=None ⇒ 判不了（產物不存在／沒有可判的 row／載不進模組）。
    verdict="MIXED(...)" ⇒ 檔內 row 判詞不一致（預期／解碼兩軸分開判，一致才算一件事）。
    """
    path = artifact if os.path.isabs(artifact) else os.path.join(root, artifact)
    if not os.path.exists(path):
        return None, "產物不存在：%s" % artifact
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import quote_gate
    except Exception as exc:  # noqa: BLE001
        return None, "載不進 quote_gate：%s" % exc
    try:
        recs = quote_gate.scan([path])
    except Exception as exc:  # noqa: BLE001
        # 閘門自己爆掉 = 「判不了」，不是「build 跟著爆」。D7／D8 會把這句當錯誤報出來，
        # 所以失敗方向仍然是不放行（fail-closed），只是訊息比 traceback 有用。
        return None, "quote_gate 判不了 %s：%s: %s" % (artifact, type(exc).__name__, exc)
    if not recs:
        return None, "產物裡沒有可判的 llama-bench row：%s" % artifact
    verdicts = sorted(set(r["verdict"] for r in recs))
    if len(verdicts) == 1:
        return verdicts[0], ""
    return "MIXED(%s)" % "/".join(verdicts), "檔內 row 判詞不一致"


D7_EXCLUDED_CLOSURES = ("結案（排除）",)

# ── 計數器端點的結案路徑（2026-09-29，L20-2 是第一例）────────────────────────
# 為什麼需要第二條路：D7 的引用閘門（quote_gate）判的是**時間**讀數（逐 rep 離散、樣本數、
# 窗口、參考帶）。可是有些子目標的主端點**按設計就是計數器** —— 判準是二值翻轉，不需要乾淨窗，
# 而它們的 t/s 反而**不可引用**（單段提交臂輸出是 garbage）。硬要這種格拿 QUOTABLE，
# 唯一的出路是去捏一個時間讀數，或讓閘門永久紅著。
# 所以補一條**對稱**的路：宣告 `counter_quote`（見證產物 ＋ 判它的檢查器），build 當場重跑。
# 一樣 fail-closed：產物不見了、或判詞不再是宣告的那個，board 就紅。
COUNTER_METRICS = ("zero_slot_flip",)


def counter_verdict(root, cq):
    """重跑計數器端點的檢查器 → (verdict, why)；判不了回 (None, why)。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    tool = cq.get("tool") or ""
    if tool != "g3_zeroslot_ab":
        return None, "counter_quote.tool 不認得：%r（已知：g3_zeroslot_ab）" % tool
    a, b = cq.get("artifact_a"), cq.get("artifact_b")
    if not (a and b):
        return None, "counter_quote 缺 artifact_a／artifact_b"
    try:
        import g3_zeroslot_ab as G
    except Exception as exc:  # noqa: BLE001
        return None, "載不進 g3_zeroslot_ab：%s" % exc
    try:
        pa, pb = G.resolve_log(os.path.join(root, a)), G.resolve_log(os.path.join(root, b))
        if pa is None or pb is None:
            return None, "見證產物不存在：%s" % (a if pa is None else b)
        verdict, _ = G.classify(G.parse(pa), G.parse(pb))
    except Exception as exc:  # noqa: BLE001
        return None, "g3_zeroslot_ab 判不了：%s: %s" % (type(exc).__name__, exc)
    return verdict, ""


def validate(board, nodes, root=ROOT):
    errs, targets, by_id = [], [], {}
    for layer in board.get("layers") or []:
        for t in layer.get("targets") or []:
            t = dict(t); t["_layer"] = layer.get("id"); targets.append(t)
    for t in targets:
        if not t.get("id"):
            errs.append("D3 子目標缺 id"); continue
        if t["id"] in by_id:
            errs.append("D3 子目標 id 重複：%s" % t["id"])
        by_id[t["id"]] = t
        for f in TARGET_FIELDS:
            if f not in t or t[f] in (None, "", [], {}):
                errs.append("D3 %s 缺欄位 %s" % (t["id"], f))
        if not isinstance(t.get("stage"), int) or not (0 <= t["stage"] <= 5):
            errs.append("D3 %s 的 stage 必須是 0..5 的整數" % t["id"])
        b = t.get("badge") or {}
        if not b.get("text"):
            errs.append("D3 %s 缺 badge.text" % t["id"])
        if b.get("tone") not in BADGE_TONES:
            errs.append("D3 %s 的 badge.tone 不合法：%r" % (t["id"], b.get("tone")))
        # D5 立項
        ch = t.get("charter")
        for c in ([ch] if isinstance(ch, str) else (ch or [])):
            if not os.path.exists(os.path.join(root, c)):
                errs.append("D5 %s 的立項卡不存在：%s" % (t["id"], c))
        # D6 結案規則
        ev = t.get("evidence") or {}
        st = str(t.get("state") or "")
        if st.startswith("結案"):
            if st.startswith("結案（排除）") or ev.get("cert_by") == "排除附機制":
                if not (ev.get("cert_by") and ev.get("source")):
                    errs.append("D6 %s 是『結案（排除）』但缺 cert_by／source（排除也要指出出處）" % t["id"])
            else:
                if ev.get("profile") != "prod-new":
                    errs.append("D6 %s 標結案，但 evidence.profile=%r ≠ prod-new" % (t["id"], ev.get("profile")))
                if ev.get("entry") != "harness bench":
                    errs.append("D6 %s 標結案，但 evidence.entry=%r ≠ `harness bench`" % (t["id"], ev.get("entry")))
                if ev.get("meets") is not True:
                    errs.append("D6 %s 標結案，但 evidence.meets 不是 true（未達預期值）" % t["id"])
                if str(ev.get("value") or "").strip() in ("", "—"):
                    errs.append("D6 %s 標結案，但 evidence.value 為空" % t["id"])
        # D7 引用閘門（2026-09-29 §55）：宣告的可引用性必須與閘門當場的判定一致。
        # 為什麼要這條：`attribution=none` 曾是唯一的准入條件，但交付 cell 唯一一場
        # `none` 的讀數是**最慢且離散 46%** 的那一場（rep 3 慢了 2.7 倍）⇒ 標籤必要不充分。
        # 判準現在由 scripts/check/quote_gate.py 持有（逐 rep 離散 ＋ 樣本數 ＋ 窗口 ＋ 參考帶）。
        q = ev.get("quote")
        closed_real = st.startswith("結案") and not any(st.startswith(x) for x in D7_EXCLUDED_CLOSURES) \
            and ev.get("cert_by") != "排除附機制"
        if q:
            if not isinstance(q, dict) or not q.get("artifact") or not q.get("verdict"):
                errs.append("D7 %s 的 evidence.quote 缺 artifact／verdict" % t["id"])
            else:
                live, why = gate_verdict(root, q["artifact"])
                if live is None:
                    errs.append("D7 %s 的引用判不了（%s）" % (t["id"], why))
                elif live != q["verdict"]:
                    errs.append("D7 %s 宣告 verdict=%s，但閘門現在判 %s（%s）"
                                % (t["id"], q["verdict"], live, q["artifact"]))
        # D7b 計數器端點：宣告 counter_quote 的格，當場重跑檢查器驗它（見 counter_verdict）。
        # ⚠ 它與時間引用**互斥但不強迫**：有 counter_quote 的格不必再拿一個時間引用（那正是
        #   本條存在的理由）；反之亦然。兩者都宣告就兩者都驗。
        cq = ev.get("counter_quote")
        if cq:
            if not isinstance(cq, dict) or not cq.get("verdict"):
                errs.append("D7b %s 的 counter_quote 缺 verdict" % t["id"])
            else:
                live, why = counter_verdict(root, cq)
                if live is None:
                    errs.append("D7b %s 的計數器判詞判不了（%s）" % (t["id"], why))
                elif live != cq["verdict"]:
                    errs.append("D7b %s 宣告 counter verdict=%s，但檢查器現在判 %s"
                                % (t["id"], cq["verdict"], live))
            if closed_real:
                if ev.get("metric") not in COUNTER_METRICS:
                    errs.append("D7b %s 用 counter_quote 結案，但 evidence.metric=%r 不在 %s"
                                % (t["id"], ev.get("metric"), list(COUNTER_METRICS)))
                elif cq.get("verdict") != "FLIP":
                    errs.append("D7b %s 用計數器端點結案，但 counter_quote.verdict=%s ≠ FLIP"
                                % (t["id"], cq.get("verdict")))
        if closed_real and not cq:
            if not q:
                errs.append("D7 %s 標結案但沒有 evidence.quote（結案必須有一場可引用的讀數）" % t["id"])
            elif q.get("verdict") != "QUOTABLE":
                errs.append("D7 %s 標結案，但引用判詞是 %s ≠ QUOTABLE" % (t["id"], q.get("verdict")))
        for f in ("expect_ms", "expect_tps"):
            v = str(t.get(f) or "").strip()
            if v.startswith("—") or v == "":
                continue          # 「—（說明）」＝本格不主張量化預期，允許
            if not any(c.isdigit() for c in v):
                errs.append("D3 %s 的 %s 既無數字也非「—」（預期必須可量化）" % (t["id"], f))
        for n in t.get("nodes") or []:
            if n not in nodes:
                errs.append("D1 %s 指向不存在的節點 %s" % (t["id"], n))
    covered = {n for t in targets for n in (t.get("nodes") or [])}
    seen = set()
    # ⚠ `notes` **仍然必需**，雖然 09-30 之後不再渲染：它是 D2 的唯一依據（每個未結案節點
    #   必須「被子目標覆蓋」或「被分類」）。拿掉它會讓下面那條 D2 對所有里程碑／A 軸節點報紅，
    #   或者（若順手也拿掉 D2）讓「全數有歸屬」從一條會紅的閘門降級成一句沒人驗的聲明。
    for note in board.get("notes") or []:
        n = note.get("node")
        if n not in nodes:
            errs.append("D1 分類指向不存在的節點 %s" % n); continue
        if n in seen:
            errs.append("D3 節點 %s 有兩筆分類" % n)
        seen.add(n)
        cls = note.get("class")
        if cls not in CLASSES:
            errs.append("D3 %s 的分類不合法：%r" % (n, cls)); continue
        for f in NEEDS[cls]:
            if not note.get(f):
                errs.append("D3 %s（class=%s）缺欄位 %s" % (n, cls, f))
        if cls == "handled" and note.get("by"):
            for tok in [x.strip() for x in str(note["by"]).split("／") if x.strip()]:
                if tok not in by_id:      # 複合格（L20-1／2／3）逐段驗
                    errs.append("D3 %s 的 by 指到不存在的子目標：%s" % (n, tok))
        covered.add(n)
    # `stale`（節點文字就地更新的紀錄）已於 2026-09-30 隨「本頁只保留三段」一併從 YAML 移除；
    # 它沒有任何閘門依賴它（純敘述），所以這裡不留死碼。
    for n in [x for x in live_nodes(nodes) if x not in covered]:
        errs.append("D2 未結案節點既沒被子目標覆蓋、也沒被分類：%s" % n)
    cr = board.get("closure_rule") or {}
    if not cr.get("text") or not cr.get("requires"):
        errs.append("D3 缺 closure_rule.text／requires（結案規則必須明寫）")
    # D8 主節點（L20／L25）的目標／證據：綠燈只能建在**可引用的讀數**上（§55 的精神）。
    for layer in board.get("layers") or []:
        lg, lev = layer.get("goal") or {}, layer.get("evidence") or {}
        if not lg and not lev:
            continue          # 未宣告目標的圖層：不判定、不變色（「若無則沒有變化」）
        if lg and lg.get("target") is None:
            errs.append("D8 %s 的 goal 缺 target" % layer.get("id"))
        if lg and lev.get("value") is None:
            errs.append("D8 %s 有 goal 卻沒有 evidence.value（達標必須有一場實測）" % layer.get("id"))
        if lev:
            if not lev.get("artifact"):
                errs.append("D8 %s 的 layer.evidence 缺 artifact（達標的讀數必須指名產物）" % layer.get("id"))
            else:
                live, why = gate_verdict(root, lev["artifact"])
                if live is None:
                    errs.append("D8 %s 的 layer.evidence 判不了（%s）" % (layer.get("id"), why))
                elif lev.get("quote_verdict") != live:
                    errs.append("D8 %s 宣告 quote_verdict=%s，但閘門現在判 %s"
                                % (layer.get("id"), lev.get("quote_verdict"), live))
            if lev.get("met") is True and lev.get("quote_verdict") != "QUOTABLE":
                errs.append("D8 %s 宣告達標，但引用判詞是 %s ≠ QUOTABLE（不可引用的讀數不能變綠）"
                            % (layer.get("id"), lev.get("quote_verdict")))
        # met 只能由 layer_goal() 算出來；YAML 明文宣告 met=false 卻算成 true 也要擋
        met, _why = layer_goal(layer, root)
        if lev.get("met") is not None and bool(lev.get("met")) != bool(met):
            errs.append("D8 %s 宣告 met=%s，但依 goal ∩ evidence 算是 %s"
                        % (layer.get("id"), lev.get("met"), met))

    for c in board.get("certified") or []:
        for f in ("id", "item", "value", "profile", "entry", "meets", "source"):
            if c.get(f) in (None, "", []):
                errs.append("D3 certified %s 缺欄位 %s" % (c.get("id"), f))
        if c.get("quote"):
            live, why = gate_verdict(root, (c["quote"] or {}).get("artifact"))
            if live is None:
                errs.append("D7 certified %s 的引用判不了（%s）" % (c.get("id"), why))
            elif live != (c["quote"] or {}).get("verdict"):
                errs.append("D7 certified %s 宣告 verdict=%s，但閘門現在判 %s"
                            % (c.get("id"), (c["quote"] or {}).get("verdict"), live))
        elif c.get("meets") is True and not c.get("quote_ungateable"):
            errs.append("D7 certified %s 標 meets 卻沒有 evidence 可判（要嘛給 quote、"
                        "要嘛明文 quote_ungateable 附理由）" % c.get("id"))
        if c.get("meets") is True and (c.get("profile") != "prod-new" or c.get("entry") != "harness bench"):
            errs.append("D6 certified %s 標 meets，但 profile／entry 不是 prod-new／harness bench" % c.get("id"))
    out = board.get("out") or ""
    if not out.startswith("docs/") or not out.endswith(".html"):
        errs.append("D3 out 必須是 docs/*.html（得到 %r）" % out)
    return errs, by_id


# ────────────────────────── 渲染：看板 ──────────────────────────
def render_board(board, nodes, here, out_path):
    L = []; A = L.append
    A("<!DOCTYPE html>")
    A("<!-- generated by scripts/check/decode_board_build.py — 請勿手改本檔 -->")
    A('<html lang="zh-Hant"><head><meta charset="UTF-8">')
    A('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    A("<title>%s</title><style>%s</style></head><body>" % (board.get("title", "board"), CSS))
    A(nav(here, out_path, len(collect_targets(board))))
    A('<div class="container">')
    A("<h1>%s</h1>" % board.get("title", ""))
    A('<p class="subtitle">%s</p>' % board.get("subtitle", ""))
    cr = board.get("closure_rule") or {}
    A('<div class="success"><b>結案規則（機器強制）</b>：%s<br><span class="legend">條件：%s ｜ %s</span></div>'
      % (cr.get("text", ""), "、".join(cr.get("requires") or []), cr.get("note", "")))
    A('<div class="grid">')
    for s in board.get("summary") or []:
        tone = (' %s' % s["tone"]) if s.get("tone") else ""
        A('<div class="card stat"><div class="stat-value%s">%s</div><div class="stat-label">%s</div></div>'
          % (tone, s.get("value", ""), s.get("label", "")))
    A("</div>")
    # `reading`（①–⑧ 的敘述區塊）已於 2026-09-30 從 YAML 刪除：它與 `certified_note` 講同一件事
    #   （認證只有三格、09-27 的 +25.7% 是冷啟 rep），同一頁講兩次就是「很亂」的來源。
    #   可操作的兩句已併入 `certified_note`；其餘 §50–§58 的細節留在各自的 docs 與 git 歷史裡。
    A('<div class="legend"><span class="steps"><i class="on"></i><i class="on"></i><i class="on"></i><i class="on"></i></span> 進展＝%s ／ '
      '<span class="badge b-ok">已成立</span><span class="badge b-warn">有前置／待跑</span><span class="badge b-info">未立卡</span><span class="badge b-dead">已排除</span></div>'
      '<br>主節點（L20／L25）<span class="badge b-ok">達標</span>＝該層已有一場<b>可引用</b>''（引用閘門 <code>QUOTABLE</code>）的讀數達到目標；未達標<b>不變色</b>，理由寫在標題的 <code>title</code> 裡。''<br><span style="padding:2px 10px;border-left:6px solid #059669;background:#ecfdf5;border-radius:6px">綠底＝達標</span></div>'
      % " ／ ".join("①②③④"[i] + " " + x for i, x in enumerate(board.get("stage_legend") or [])))
    A("<h2>已認證（符合結案規則的實測）</h2>")
    cn = board.get("certified_note") or {}
    if cn:
        A('<div class="warning"><b>%s</b>%s</div>'
          % (cn.get("title", ""), "".join("<br>%s" % x for x in (cn.get("lines") or []))))
    A("<table><thead><tr><th>id</th><th>項目</th><th>實測</th><th>profile</th><th>入口</th><th>達標</th>"
      "<th>引用閘門</th><th>出處</th></tr></thead><tbody>")
    for c in board.get("certified") or []:
        q = c.get("quote") or {}
        if q.get("verdict"):
            gate = '<span class="badge %s">%s</span>' % (
                "b-ok" if q["verdict"] == "QUOTABLE" else "b-warn", q["verdict"])
            if q.get("why"):
                gate += '<br><span class="legend">%s</span>' % q["why"]
        elif c.get("quote_ungateable"):
            gate = '<span class="badge b-info">不可判</span><br><span class="legend">%s</span>' % c["quote_ungateable"]
        else:
            gate = "—"
        A("<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td><code>%s</code></td><td><code>%s</code></td>"
          "<td>%s</td><td>%s</td><td><code>%s</code></td></tr>"
          % (c["id"], c["item"], c["value"], c["profile"], c["entry"],
             "✅" if c.get("meets") else "—", gate, c.get("source", "")))
        A("</tbody></table>")
    # [CGC 2026-09-30 版面收斂] 本頁＝**三段**：已認證／L20／L25。operator 的指令：
    # 「三個『結案』到現在只成立三格；以下（冷啟 rep 的產物，不是機器變快）以上的內容請整理在
    #   已認證/L20/L25 上面，其餘刪除。」
    # ⇒ 兩句必須留在**已認證**段裡（它們正是「什麼才叫認證」的判準），而不是散在後面的敘述段。
    #   資料來源＝`certified_note`（YAML）；其餘段落的 `order`／`calls`／`stale` 已從資料裡移除。
    for layer in board.get("layers") or []:
        met, why = layer_goal(layer)
        head = layer.get("title", layer.get("id", ""))
        if met:
            # 只有「可引用的讀數達到目標」才變綠（見 layer_goal；D8 會複判）
            A('<h2 class="layer-met" title="%s">%s <span class="badge b-ok">達標</span></h2>'
              % (_attr(why), head))
        else:
            # 未達標／未宣告 ⇒ 完全不動（「若無則沒有變化」）；理由只放在 title，可稽核但不喧賓奪主
            A('<h2 title="%s">%s</h2>' % (_attr(why), head))
        if layer.get("note"):
            A('<p class="legend">%s</p>' % layer["note"])
        A("<table><thead><tr><th>id</th><th>子目標</th><th>節點</th><th>預期 ms/step</th><th>預期 t/s</th>"
          "<th>現況（已量）</th><th>action</th><th>驗收／否證</th><th>立項</th><th>結案</th><th>進展</th></tr></thead><tbody>")
        for t in layer.get("targets") or []:
            cell = "／".join("<code>%s</code>" % n for n in t.get("nodes") or [])
            ch = t.get("charter")
            chs = [ch] if isinstance(ch, str) else (ch or [])
            chcell = "／".join('<a href="%s">%s</a>' % (rel_from(here, os.path.join(ROOT, c)),
                                                        os.path.basename(c).replace(".yaml", ""))
                               for c in chs) or "—"
            ev = t.get("evidence") or {}
            st = str(t.get("state") or "")
            cls = "b-ok" if st.startswith("結案") else "b-warn"
            cert = ""
            if ev.get("cert_by"):
                cert = '<br><span class="legend">排除依據：%s</span>' % ev.get("source", "")
            else:
                cert = ('<br><span class="legend"><code>%s</code>／<code>%s</code>／%s</span>'
                        % (ev.get("profile", "—"), ev.get("entry", "—"), ev.get("value", "—")))
            if ev.get("quote"):
                qv = ev["quote"].get("verdict", "—")
                cert += ('<br><span class="legend">引用閘門：<b>%s</b></span>' % qv)
            A("<tr><td><b>%s</b></td><td><b>%s</b><br><a class=\"legend\" href=\"%s\">逐條頁 ›</a></td><td>%s</td>"
              "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><b>驗收</b>：%s<br><b>否證</b>：%s</td>"
              "<td>%s</td><td><span class=\"badge %s\">%s</span>%s</td>"
              "<td><span class=\"steps\">%s</span></td></tr>"
              % (t["id"], t["title"], rel_from(here, os.path.join(PAGES_DIR, t["id"] + ".html")),
                 cell, t["expect_ms"], t["expect_tps"], t["now"], t["action"],
                 t["accept"], t["falsify"], chcell, cls, st, cert,
                 "".join('<i class="%s"></i>' % ("on" if i < t["stage"] else "") for i in range(4))))
        A("</tbody></table>")
    # ── 以下是 09-30 之前會渲染、現在**整段移除**的區塊（operator：其餘刪除）─────────
    #   ① `calls`（要點）：其中兩句已整理進 已認證 段的 `certified_note`；
    #   ② `order`（建議施工順序）：活項都已寫在各格的 `action` 裡；
    #   ③ `notes` 的五個分類段（C 軸上界／已仲裁帳目／節點文字就地更新／對位表／其餘未結案節點）
    #      —— 這些是**導覽視圖**，資料仍在 `mindmap.json` 與各節點頁。
    #   ⚠ `notes` **刻意留在 YAML 裡**：它同時是 D2 的依據（未結案節點必須有歸屬），
    #     刪它等於刪掉一條會紅的閘門、把「全數有歸屬」變成沒人驗的聲明。
    #     所以本檔只停渲染，不動那份分類表；`stale`（節點文字更新紀錄）純敘述 ⇒ 已從 YAML 移除。
    A("<footer>本檔由 <code>scripts/check/decode_board_build.py</code> 從 <code>%s</code> ＋ <code>docs/mindmap/mindmap.json</code> "
      "<b>生成</b>（勿手改）；資料截止 <b>%s</b>；本頁<b>只保留三段</b>（已認證／L20／L25）——"
      "未結案節點共 <b>%d</b> 個，歸屬仍在 <code>mindmap.json</code> 與各節點頁（<code>--check</code> 可驗）。<br>%s</footer>"
      % (os.path.relpath(DEFAULT_YAML, ROOT), board.get("data_asof", "—"), len(live_nodes(nodes)), board.get("footer", "")))
    A("</div></body></html>")
    return "\n".join(L) + "\n"


# ────────────────────────── 渲染：逐子目標頁 ──────────────────────────
def node_doc_links(entry, here=PAGES_DIR):
    """節點的產物／log 連結（evid ＋ docs 清單），全部相對本頁目錄。"""
    out = []
    if not entry:
        return out
    ev = entry.get("evid")
    if ev:
        for piece in str(ev).split("／"):
            piece = piece.strip()
            if piece:
                out.append((piece, None))
    for d in entry.get("docs") or []:
        name = str(d)
        cand = [name] if name.startswith("docs/") else ["docs/%s" % name, name]
        hit = next((c for c in cand if os.path.exists(os.path.join(ROOT, c))), None)
        out.append((name, rel_from(here, os.path.join(ROOT, hit)) if hit else None))
    return out


def render_page(board, t, nodes):
    L = []; A = L.append
    ev = t.get("evidence") or {}
    A("<!DOCTYPE html>")
    A("<!-- generated by scripts/check/decode_board_build.py — 請勿手改本檔 -->")
    A('<html lang="zh-Hant"><head><meta charset="UTF-8">')
    A('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    A("<title>%s — %s</title><style>%s</style></head><body>" % (t["id"], t["title"], PAGE_CSS))
    A(nav(PAGES_DIR, os.path.join(ROOT, board.get("out", "")), len(collect_targets(board))))
    A('<div class="container">')
    A("<h1>%s ｜ %s</h1>" % (t["id"], t["title"]))
    st = str(t.get("state") or "")
    A('<p class="subtitle">結案狀態：<span class="badge %s">%s</span> ｜ 依 <b>%s</b> 的結案規則（prod-new ＋ harness bench ＋ 達標）</p>'
      % ("b-ok" if st.startswith("結案") else "b-warn", st, board.get("id", "")))
    A('<div class="card"><table class="kv">')
    A("<tr><th>預期（ms/step）</th><td>%s</td></tr>" % t["expect_ms"])
    A("<tr><th>預期（t/s）</th><td>%s</td></tr>" % t["expect_tps"])
    A("<tr><th>現況（已量）</th><td>%s</td></tr>" % t["now"])
    A("<tr><th>action</th><td>%s</td></tr>" % t["action"])
    A("<tr><th>驗收</th><td>%s</td></tr>" % t["accept"])
    A("<tr><th>否證</th><td>%s</td></tr>" % t["falsify"])
    A("</table></div>")
    A("<h3>結案判準的現況</h3><table><thead><tr><th>profile</th><th>入口</th><th>實測值</th><th>metric</th><th>達標</th></tr></thead><tbody>")
    A("<tr><td><code>%s</code></td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>"
      % (ev.get("profile", "—"), ev.get("entry", "—"), ev.get("value", "—"), ev.get("metric", "—"),
         "✅" if ev.get("meets") else "❌"))
    A("</tbody></table>")
    if ev.get("cert_by"):
        A('<div class="success"><b>排除依據（%s）</b>：%s</div>' % (ev["cert_by"], ev.get("source", "")))
    q = ev.get("quote")
    if q:
        art = str(q.get("artifact", ""))
        artp = os.path.join(ROOT, art)
        art_html = ('<a href="%s"><code>%s</code></a>' % (rel_from(PAGES_DIR, artp), art)
                    if os.path.exists(artp) else '<code>%s</code>' % art)
        A('<div class="%s"><b>引用閘門：%s</b>（判準＝<code>scripts/check/quote_gate.py</code>，'
          '逐 rep 離散 ＋ 樣本數 ＋ 窗口 ＋ 參考帶）<br>產物 %s<br>%s</div>'
          % ("success" if q.get("verdict") == "QUOTABLE" else "warning",
             q.get("verdict", "—"), art_html, q.get("why", "")))
    elif ev.get("quote_ungateable"):
        A('<div class="warning"><b>引用閘門：無從判</b> —— %s</div>' % ev["quote_ungateable"])
    ch = t.get("charter")
    chs = [ch] if isinstance(ch, str) else (ch or [])
    A("<h3>立項（charter）</h3>")
    if chs:
        A("<ul>")
        for c in chs:
            A('<li><a href="%s"><code>%s</code></a></li>' % (rel_from(PAGES_DIR, os.path.join(ROOT, c)), c))
        A("</ul>")
    else:
        A('<div class="warning">未立項（本輪未指派 arm）</div>')
    A("<h3>對應節點與其產物／log</h3>")
    for nid in t.get("nodes") or []:
        A('<div class="card"><b><code>%s</code></b> ｜ <a href="../briefs/%s.html">逐條白皮書（html）</a> '
          '／ <a href="../briefs/%s.md">md</a></div>' % (nid, nid, nid))
        A('<table><thead><tr><th>產物／log</th><th>連結</th></tr></thead><tbody>')
        for label, href in node_doc_links(nodes.get(nid)):
            if href:
                A('<tr><td><code>%s</code></td><td><a href="%s">開啟</a></td></tr>' % (label, href))
            else:
                A('<tr><td><code>%s</code></td><td><span class="legend">（文件引用，非檔案路徑）</span></td></tr>' % label)
        A("</tbody></table>")
    A("<footer>本頁由 <code>scripts/check/decode_board_build.py</code> 從 <code>scripts/check/decode_board_2026-09-29.yaml</code> "
      "＋ <code>docs/mindmap/mindmap.json</code> 生成（勿手改）。</footer>")
    A("</div></body></html>")
    return "\n".join(L) + "\n"


def render_page_md(board, t):
    ev = t.get("evidence") or {}
    ch = t.get("charter")
    chs = [ch] if isinstance(ch, str) else (ch or [])
    L = ["# %s ｜ %s" % (t["id"], re_plain(t["title"])), "",
         "- **結案狀態**：%s" % t.get("state", ""),
         "- **預期 ms/step**：%s" % re_plain(t["expect_ms"]),
         "- **預期 t/s**：%s" % re_plain(t["expect_tps"]),
         "", "## 現況（已量）", re_plain(t["now"]), "",
         "## 動作與判準", "- **action**：%s" % re_plain(t["action"]),
         "- **驗收**：%s" % re_plain(t["accept"]), "- **否證**：%s" % re_plain(t["falsify"]), "",
         "## 結案判準現況", "",
         "| profile | 入口 | 實測值 | metric | 達標 |", "|---|---|---|---|---|",
         "| `%s` | `%s` | %s | %s | %s |" % (ev.get("profile", "—"), ev.get("entry", "—"),
                                             ev.get("value", "—"), ev.get("metric", "—"),
                                             "✅" if ev.get("meets") else "❌"), ""]
    if ev.get("cert_by"):
        L += ["> **排除依據（%s）**：%s" % (ev["cert_by"], re_plain(ev.get("source", ""))), ""]
    q = ev.get("quote")
    if q:
        L += ["### 引用閘門", "",
              "- **verdict**：`%s`（判準＝`scripts/check/quote_gate.py`）" % q.get("verdict", "—"),
              "- **產物**：`%s`" % q.get("artifact", ""),
              "- **為什麼**：%s" % re_plain(q.get("why", "")), ""]
    elif ev.get("quote_ungateable"):
        L += ["### 引用閘門", "", "> **無從判** —— %s" % re_plain(ev["quote_ungateable"]), ""]
    L += ["## 立項（charter）", ""]
    L += ["- `%s`" % c for c in chs] or ["（未立項）"]
    L += ["", "## 對應節點", ""]
    for nid in t.get("nodes") or []:
        L.append("- `%s`（逐條白皮書：`docs/mindmap/briefs/%s.html`）" % (nid, nid))
    L += ["", "_本頁由 scripts/check/decode_board_build.py 生成，勿手改。_", ""]
    return "\n".join(L)


def _attr(text):
    """HTML 屬性值（title 用）：只要跳出雙引號與角括號就夠，不引入新依賴。"""
    return (str(text).replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def re_plain(s):
    import re as _re
    s = _re.sub(r"<br\s*/?>", " ", str(s))
    s = _re.sub(r"<[^>]+>", "", s)
    return s


def render_index(board, targets):
    L = []; A = L.append
    A("<!DOCTYPE html>")
    A("<!-- generated by scripts/check/decode_board_build.py — 請勿手改本檔 -->")
    A('<html lang="zh-Hant"><head><meta charset="UTF-8">')
    A('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    A("<title>逐子目標（L20／L25）</title><style>%s</style></head><body>" % CSS)
    A(nav(PAGES_DIR, os.path.join(ROOT, board.get("out", "")), len(targets)))
    A('<div class="container"><h1>逐子目標頁：L20（8 格）／L25（6 格）</h1>')
    A('<p class="subtitle">每個子目標一頁：預期 ms/step 與 t/s、現況、驗收／否證、立項卡、結案判準（profile／入口／實測）。'
      '看板：<a href="%s">%s</a>。全部由 YAML ＋ mindmap.json 生成。</p>'
      % (rel_from(PAGES_DIR, os.path.join(ROOT, board.get("out", ""))), os.path.basename(board.get("out", ""))))
    A("<table><thead><tr><th>id</th><th>子目標</th><th>預期 ms/step</th><th>預期 t/s</th><th>立項</th><th>結案</th></tr></thead><tbody>")
    for t in targets:
        ch = t.get("charter")
        chs = [ch] if isinstance(ch, str) else (ch or [])
        st = str(t.get("state") or "")
        A("<tr><td><b><a href=\"%s.html\">%s</a></b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
          "<td><span class=\"badge %s\">%s</span></td></tr>"
          % (t["id"], t["id"], t["title"], t["expect_ms"], t["expect_tps"],
             "／".join("<code>%s</code>" % os.path.basename(c) for c in chs) or "—",
             "b-ok" if st.startswith("結案") else "b-warn", st))
    A("</tbody></table>")
    A('<div class="warning"><b>結案規則</b>：%s</div>' % (board.get("closure_rule") or {}).get("text", ""))
    A("<footer>生成器：<code>scripts/check/decode_board_build.py</code>；<code>--check</code> 會驗這 15 頁是否落後。</footer>")
    A("</div></body></html>")
    return "\n".join(L) + "\n"


def render_index_md(board, targets):
    L = ["# 逐子目標頁（L20／L25）", "",
         "| id | 子目標 | 預期 ms/step | 預期 t/s | 結案 |", "|---|---|---|---|---|"]
    for t in targets:
        L.append("| [%s](%s.html) | %s | %s | %s | %s |" % (t["id"], t["id"], re_plain(t["title"]),
                                                           re_plain(t["expect_ms"]), re_plain(t["expect_tps"]), t.get("state", "")))
    L += ["", "> **結案規則**：%s" % re_plain((board.get("closure_rule") or {}).get("text", "")), ""]
    return "\n".join(L)


# ────────────────────────── 主流程 ──────────────────────────
def collect_targets(board):
    return [dict(t, _layer=l.get("id")) for l in (board.get("layers") or []) for t in (l.get("targets") or [])]


def artifacts(board, nodes):
    board_out = os.path.join(ROOT, board.get("out", ""))
    here = os.path.dirname(board_out)
    out = {board_out: render_board(board, nodes, here, board_out)}
    out[os.path.join(PAGES_DIR, "index.html")] = render_index(board, collect_targets(board))
    out[os.path.join(PAGES_DIR, "index.md")] = render_index_md(board, collect_targets(board))
    for t in collect_targets(board):
        out[os.path.join(PAGES_DIR, "%s.html" % t["id"])] = render_page(board, t, nodes)
        out[os.path.join(PAGES_DIR, "%s.md" % t["id"])] = render_page_md(board, t)
    return out


def build(yaml_path=DEFAULT_YAML, root=ROOT):
    board = load_yaml(yaml_path)
    nodes = index_nodes(load_mindmap())
    errs, by_id = validate(board, nodes, root)
    return board, nodes, errs, artifacts(board, nodes)


def cmd_check(yaml_path, quiet=False):
    board, nodes, errs, arts = build(yaml_path)
    for path, text in arts.items():
        rel = os.path.relpath(path, ROOT)
        if not os.path.exists(path):
            errs.append("D4 產物不存在：%s" % rel)
        else:
            with open(path, encoding="utf-8") as fh:
                if fh.read() != text:
                    errs.append("D4 產物落後於 YAML（跑一次 build 即可）：%s" % rel)
    if not quiet:
        tg = collect_targets(board)
        closed = [t["id"] for t in tg if str(t.get("state", "")).startswith("結案")]
        print("[decode-board] 子目標 %d 個；結案 %d 個（%s）" % (len(tg), len(closed), "、".join(closed) or "—"))
        print("[decode-board] 產物 %d 個（看板 1 ＋ 逐子目標頁 %d）" % (len(arts), len(arts) - 1))
        for e in errs:
            print("  ✗ %s" % e)
        print("VERDICT: %s（%d 個問題）" % ("PASS" if not errs else "FAIL", len(errs)))
    return 0 if not errs else 1


def cmd_build(yaml_path=DEFAULT_YAML):
    board, nodes, errs, arts = build(yaml_path)
    if errs:
        for e in errs:
            print("  ✗ %s" % e)
        print("VERDICT: FAIL — 有漂移，未寫檔。")
        return 1
    os.makedirs(PAGES_DIR, exist_ok=True)
    for path, text in arts.items():
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    tg = collect_targets(board)
    closed = [t["id"] for t in tg if str(t.get("state", "")).startswith("結案")]
    print("[decode-board] 寫入 %d 個產物：%s ＋ docs/mindmap/subgoals/（%d 頁 html＋md）"
          % (len(arts), board["out"], len(tg)))
    print("[decode-board] 子目標 %d；結案 %d（%s）；認證 %d 格"
          % (len(tg), len(closed), "、".join(closed) or "—", len(board.get("certified") or [])))
    print("VERDICT: PASS")
    return 0


def cmd_selftest():
    import copy, tempfile
    import yaml as _y
    ok = 0
    base = load_yaml(DEFAULT_YAML)
    nodes = index_nodes(load_mindmap())

    total = 0

    def case(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %-46s -> %s%s" % (name, "PASS" if cond else "FAIL", "" if cond else " " + str(detail)))

    errs, _ = validate(base, nodes)
    case("clean base", not errs, errs)
    b = copy.deepcopy(base); b["layers"][0]["targets"][0]["nodes"] = ["no-such-node"]
    case("D1 unknown node", any(e.startswith("D1") for e in validate(b, nodes)[0]))
    b = copy.deepcopy(base); b["notes"] = [n for n in b["notes"] if n["node"] != "c-bandwidth"]
    case("D2 uncovered live node", any(e.startswith("D2") for e in validate(b, nodes)[0]))
    b = copy.deepcopy(base); b["layers"][0]["targets"][0]["charter"] = "scripts/check/charters/nope.yaml"
    case("D5 missing charter", any(e.startswith("D5") for e in validate(b, nodes)[0]))
    b = copy.deepcopy(base)
    b["layers"][0]["targets"][0]["state"] = "結案"
    b["layers"][0]["targets"][0]["evidence"] = {"profile": "prod25", "entry": "decode_sweep", "value": "8.43", "metric": "tps", "meets": True}
    case("D6 closure without prod-new/harness", any(e.startswith("D6") for e in validate(b, nodes)[0]))
    b = copy.deepcopy(base)
    b["layers"][0]["targets"][0]["state"] = "結案"
    b["layers"][0]["targets"][0]["evidence"] = {"profile": "prod-new", "entry": "harness bench", "value": "20.5", "metric": "tps", "meets": True}
    case("D6 closure with prod-new + harness", not any(e.startswith("D6") for e in validate(b, nodes)[0]))
    # --- D7 引用閘門（fixture 自帶，不依賴 Backup/ 的實跑產物） ---
    gate_tmp = tempfile.mkdtemp()
    art_ok = os.path.join(gate_tmp, "gate_ok.json")
    art_dirty = os.path.join(gate_tmp, "gate_dirty.json")

    def _fixture(path, attrib):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512,
                                 "avg_ts": 11.703, "stddev_ts": 0.348,
                                 "samples_ts": [11.65, 11.38, 12.07]}],
                       "attribution": {"verdict": attrib, "thermal_worst": "NOMINAL"},
                       "cell": {"named_cell": "delivery"}}, fh)

    _fixture(art_ok, "none")     # 逐 rep 穩 ＋ 窗口乾淨 ⇒ QUOTABLE
    _fixture(art_dirty, "swap")  # 逐 rep 穩但換頁 ⇒ DIRTY
    b = copy.deepcopy(base)
    b["layers"][0]["targets"][0]["evidence"]["quote"] = {"artifact": art_dirty, "verdict": "QUOTABLE"}
    case("D7 declared verdict ≠ live", any(e.startswith("D7") for e in validate(b, nodes)[0]))
    b = copy.deepcopy(base)
    b["layers"][0]["targets"][0]["state"] = "結案"
    b["layers"][0]["targets"][0]["evidence"] = {"profile": "prod-new", "entry": "harness bench",
                                                "value": "20.5", "metric": "tps", "meets": True}
    case("D7 closure without quote", any(e.startswith("D7") for e in validate(b, nodes)[0]))
    b = copy.deepcopy(base)
    b["layers"][0]["targets"][0]["state"] = "結案"
    b["layers"][0]["targets"][0]["evidence"] = {"profile": "prod-new", "entry": "harness bench",
                                                "value": "11.703", "metric": "tps", "meets": True,
                                                "quote": {"artifact": art_ok, "verdict": "QUOTABLE"}}
    case("D7 closure with QUOTABLE quote", not any(e.startswith("D7") or e.startswith("D6")
                                                   for e in validate(b, nodes)[0]))
    # --- D8／主節點綠燈（L20／L25） ---
    def _board_html(b):
        b = copy.deepcopy(b)
        b["goal_of"] = None if False else b.get("goal_of")
        path = os.path.join(gate_tmp, "lvl.yaml")
        b["out"] = "docs/__selftest_board.html"
        with open(path, "w", encoding="utf-8") as fh:
            _y.safe_dump(b, fh, allow_unicode=True, sort_keys=False)
        _, _, e, arts = build(path)
        html = next((t for p, t in arts.items() if p.endswith("__selftest_board.html")), "")
        return html, e

    b = copy.deepcopy(base)
    b["layers"][0]["goal"] = {"metric": "decode_tps", "target": 10.0, "crit": "fixture"}
    b["layers"][0]["evidence"] = {"value": 11.703, "artifact": art_ok, "quote_verdict": "QUOTABLE"}
    html, e = _board_html(b)
    case("L20 達標 ⇒ 綠（<h2 class=\"layer-met\">）", '<h2 class="layer-met"' in html and not e, e)
    b = copy.deepcopy(base)
    b["layers"][0]["goal"] = {"metric": "decode_tps", "target": 20.0, "crit": "fixture"}
    b["layers"][0]["evidence"] = {"value": 11.703, "artifact": art_ok, "quote_verdict": "QUOTABLE"}
    html, e = _board_html(b)
    case("未達標 ⇒ 不變色（無綠標題）", '<h2 class="layer-met"' not in html and not e, e)
    b = copy.deepcopy(base)
    b["layers"][0]["goal"] = {"metric": "decode_tps", "target": 10.0}
    b["layers"][0]["evidence"] = {"value": 11.703, "artifact": art_dirty,
                                  "quote_verdict": "DIRTY", "met": True}
    html, e = _board_html(b)
    case("宣告達標但讀數不可引用 ⇒ D8 擋", any(x.startswith("D8") for x in e), e)
    tmp = tempfile.mkdtemp()
    y = os.path.join(tmp, "b.yaml")
    b2 = copy.deepcopy(base); b2["out"] = "docs/__selftest_board.html"
    with open(y, "w", encoding="utf-8") as fh:
        _y.safe_dump(b2, fh, allow_unicode=True, sort_keys=False)
    probe = os.path.join(ROOT, "docs", "__selftest_board.html")
    had = os.path.exists(probe)
    old = open(probe, encoding="utf-8").read() if had else None
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("<!-- stale -->")
        case("D4 stale artifact", cmd_check(y, quiet=True) == 1)
    finally:
        if had:
            open(probe, "w", encoding="utf-8").write(old)
        elif os.path.exists(probe):
            os.remove(probe)
    print("SELFTEST %s（%d/%d）" % ("PASS" if ok == total else "FAIL", ok, total))
    return 0 if ok == total else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 decode 子目標看板與逐子目標頁")
    ap.add_argument("--yaml", default=DEFAULT_YAML)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return cmd_selftest()
    if a.check:
        return cmd_check(a.yaml)
    return cmd_build(a.yaml)


if __name__ == "__main__":
    sys.exit(main())
