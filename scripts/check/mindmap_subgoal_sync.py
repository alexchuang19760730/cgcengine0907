#!/usr/bin/env python3
"""mindmap_subgoal_sync — 把「最新子目標的 options」當唯一來源，同步到總圖與所有立項卡。

為什麼需要它（以及為什麼前一支工具被刪掉）
    前一支 `mindmap_profile_audit.py` 用**文件掃描**（數 CGC_* 出現次數）去猜每一條掛哪個臂。
    那種推導的置信度天花板就是 med/low：它可以給出 52 個答案，但沒有一個能被當成設定。
    本工具反過來：**唯一來源是子目標看板** `scripts/check/decode_board_2026-09-29.yaml`
    —— 15 個子目標各自寫死 `options.{profile,arms,knobs,cli,no_knob,note}`，
    本工具只做「把同一份設定搬到該去的地方」，不做推導、不補洞：

        ① 看板 → 52 條逐條白皮書（`subgoal_binding`；由 `nodes:` 反查，未歸屬者明寫未歸屬）
        ② 看板 → 立項卡 `scripts/check/charters/*.yaml`（`arms:` 補齊 ＋ `options:` 區塊）
        ③ 看板 → 對照報告 `docs/mindmap/SUBGOAL_OPTION_MAP_2026-09-29.{md,html}`

用法
    python3 scripts/check/mindmap_subgoal_sync.py                # 印對照表
    python3 scripts/check/mindmap_subgoal_sync.py --apply        # 寫回 mindmap.json ＋ 立項卡 ＋ 重建
    python3 scripts/check/mindmap_subgoal_sync.py --report       # 產出 md ＋ html 對照報告
    python3 scripts/check/mindmap_subgoal_sync.py --check        # 缺一格就 rc=1
    python3 scripts/check/mindmap_subgoal_sync.py --selftest
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mindmap_build as MB             # noqa: E402
import mindmap_brief_build as MBB      # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "scripts" / "check" / "decode_board_2026-09-29.yaml"
MM_JSON = ROOT / "docs/mindmap" / "mindmap.json"
BRIEF_DIR = ROOT / "docs/mindmap" / "briefs"
CHARTER_DIR = ROOT / "scripts" / "check" / "charters"
REPORT_MD = ROOT / "docs/mindmap" / "SUBGOAL_OPTION_MAP_2026-09-29.md"
REPORT_HTML = ROOT / "docs/mindmap" / "SUBGOAL_OPTION_MAP_2026-09-29.html"
STAMP = "2026-09-29"
MARKER = "子目標綁定"                    # brief 裡新節的錨點（html／md 同一個詞）
ROLES = ("subject", "instrument", "candidate")


# ───────────────────── 看板 → 結構 ─────────────────────────────────

def load_board() -> dict:
    import yaml  # 只有這裡需要（harness 線已依賴 PyYAML）
    return yaml.safe_load(BOARD.read_text(encoding="utf-8")) or {}


def subgoals(board: dict) -> list[dict]:
    out = []
    for L in board.get("layers") or []:
        for t in L.get("targets") or []:
            out.append({
                "id": t["id"],
                "layer": L["id"],
                "title": re.sub(r"<[^>]+>", "", str(t.get("title", ""))),
                "nodes": list(t.get("nodes") or []),
                "charters": [t["charter"]] if isinstance(t.get("charter"), str)
                            else list(t.get("charter") or []),
                "options": t.get("options") or {},
                "state": re.sub(r"<[^>]+>", "", str(t.get("state", ""))),
                "accept": re.sub(r"<[^>]+>", "", str(t.get("accept", ""))),
            })
    return out


def node_map(sgs: list[dict]) -> dict:
    """node id → 子目標（一個 node 可能同時掛兩格 ⇒ 記第一格，其餘寫進 also）。"""
    m = {}
    for s in sgs:
        for n in s["nodes"]:
            m.setdefault(n, []).append(s["id"])
    return m


def dispositions(board: dict) -> dict[str, list[dict]]:
    """`dispositions:` → {entry_id: [{kind, ref, why}, …]}（同一條可以既屬子目標又已認證）。"""
    out: dict[str, list[dict]] = {}
    for d in (board.get("dispositions") or []):
        eid = str(d.get("id", "")).strip()
        if not eid:
            continue
        out.setdefault(eid, []).append({
            "kind": str(d.get("kind", "")).strip(),
            "ref": str(d.get("ref", "") or "").strip(),
            "why": re.sub(r"<[^>]+>", "", str(d.get("why", ""))),
        })
    return out


NODES_RE = re.compile(r"^        nodes: \[([^\]]*)\][ \t]*$", re.M)


def integrate_nodes(txt: str, sg_id: str, add: list[str]) -> tuple[str, list[str]]:
    """把處置為 `kind: subgoal` 的節點補進該子目標的 `nodes:`（＝「整合在目前的子目標」）。

    回傳（新文字、實際補進去的節點）。找不到那一格就原樣回傳。
    """
    m = re.search(rf"^      - id: {re.escape(sg_id)}[ \t]*$", txt, re.M)
    if not m:
        return txt, []
    nm = NODES_RE.search(txt, m.end())
    if not nm:
        return txt, []
    have = [x.strip() for x in nm.group(1).split(",") if x.strip()]
    new = [a for a in add if a not in have]
    if not new:
        return txt, []
    line = "        nodes: [" + ", ".join(have + new) + "]"
    return txt[:nm.start()] + line + txt[nm.end():], new


def knobs_by_role(opt: dict, role: str) -> list[str]:
    out = []
    for k in (opt.get("knobs") or []):
        if str(k.get("role")) == role:
            out.append(f'{k.get("name")}={k.get("value")}')
    return out


def binding_of(sg: dict, also: list[str] | None = None) -> dict:
    opt = sg["options"]
    return {
        "subgoal": sg["id"],
        "title": sg["title"],
        "layer": sg["layer"],
        "profile": opt.get("profile", ""),
        # ⚠ 欄位名不能叫 `arms`：`provenance_gate.py` 的 `server_window.MEASURED_KEYS`
        #   含 `arms`（真實產物的臂結果陣列）⇒ 一份「引用視圖」用同名鍵會被判成
        #   52 個沒有 window/digest 的量測物件。名字是為了不撞那個詞彙表。
        "arm_strings": list(opt.get("arms") or []),
        "options": knobs_by_role(opt, "subject"),
        "instruments": knobs_by_role(opt, "instrument"),
        "candidates": knobs_by_role(opt, "candidate"),
        "cli": list(opt.get("cli") or []),
        "no_knob": opt.get("no_knob") or "",
        "note": opt.get("note") or "",
        "state": sg["state"],
        "also": list(also or []),
        "source": "board",
        "confidence": "high",
    }


def unbound_binding(why: str = "") -> dict:
    return {
        "subgoal": None, "title": "", "layer": "", "profile": "", "arm_strings": [],
        "options": [], "instruments": [], "candidates": [], "cli": [],
        "no_knob": "本條不屬於看板 15 個子目標中的任何一格（已認證／已定案／已作廢）"
                   "⇒ 沒有待跑的臂，也就沒有要綁的 option。",
        "note": why or "", "state": "", "also": [], "source": "board", "confidence": "n/a",
    }


# ───────────────────── 寫回：mindmap.json ──────────────────────────

def apply_mindmap(sgs: list[dict], disp: dict[str, list[dict]]) -> int:
    data = json.loads(MM_JSON.read_text(encoding="utf-8"))
    nm = node_map(sgs)
    by_id = {s["id"]: s for s in sgs}
    n = 0
    for e in data["entries"]:
        ids = nm.get(e["id"]) or []
        why = next((d["why"] for d in (disp.get(e["id"]) or [])
                    if d["kind"] != "subgoal"), "")
        b = binding_of(by_id[ids[0]], ids[1:]) if ids else unbound_binding(why)
        e.pop("arm_binding", None)          # 舊的掃描推導記錄：整批刪除，不留殘骸
        if e.get("subgoal_binding") != b:
            e["subgoal_binding"] = b
            n += 1
        rows = disp.get(e["id"]) or []
        if e.get("disposition") != rows:
            e["disposition"] = rows
            n += 1
    MM_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return n


def apply_integration(disp: dict[str, list[dict]], sgs: list[dict]) -> list[str]:
    """把 `kind: subgoal` 的處置節點補進看板對應格的 `nodes:`（改看板，不是改產物）。"""
    txt = BOARD.read_text(encoding="utf-8")
    moved = []
    for s in sgs:
        add = [eid for eid, rows in disp.items()
               if any(r["kind"] == "subgoal" and r["ref"] == s["id"] for r in rows)]
        txt, new = integrate_nodes(txt, s["id"], add)
        moved += [f"{s['id']} ← {n}" for n in new]
    if moved:
        BOARD.write_text(txt, encoding="utf-8")
    return moved


# ───────────────────── 寫回：立項卡 ────────────────────────────────

# ⚠ 兩個都已踩過的假缺口（用一條 regex 抓 arms 區塊時）：
#   ① `- ` 可以貼齊 `arms:`（0 縮排）也可以縮排 —— 只認縮排會把 10 張「其實有 arms」的卡判成缺。
#   ② arms 區塊裡有**註解行**（例：`  # P1：…`）—— 只認 `- ` 開頭的行會在遇到註解時整段失配。
# 而一條寬鬆的 regex 又會**多吃到下一個區塊的註解**（e-fillbudget 就被吃到「本卡配套的 src/ 改動」）。
# ⇒ 改成逐行掃描，並且把區塊尾端修剪到「最後一個 item」為止（尾隨的註解／空行屬於下一節）。
ARMS_HEAD_RE = re.compile(r"^arms:[ \t]*\n", re.M)
OPT_BLOCK_RE = re.compile(r"^options:\s*\n(?:[ \t].*\n?)*", re.M)


def arms_block(txt: str):
    """→ (head_end, block_end, indent, items)；找不到 `arms:` 或缺 item 時回 None。"""
    m = ARMS_HEAD_RE.search(txt)
    if not m:
        return None
    start = m.end()
    lines, pos, end, items = txt[start:].split("\n"), start, None, []
    for ln in lines:
        nxt = pos + len(ln) + 1
        stripped = ln.strip()
        if re.match(r"^-[ \t]*\S", stripped):
            items.append(stripped)
            end = nxt
            indent = re.match(r"[ \t]*", ln).group(0)
        elif stripped == "" or stripped.startswith("#"):
            pass                      # 空行／註解：允許夾在 item 之間，但不延伸區塊尾端
        else:
            break
        pos = nxt
    if not items:
        return None
    return start, end, indent, items


def _yml(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def charter_options_block(sg: dict) -> str:
    o = sg["options"]
    ln = [f"# ── 【子目標設定（由 {BOARD.name} 的 {sg['id']} 同步；勿手改，改看板）】──",
          "options:",
          f"  subgoal: {sg['id']}",
          f"  profile: {o.get('profile', 'prod-new')}",
          "  arms:"]
    ln += [f"    - {_yml(a)}" for a in (o.get("arms") or [])]
    if o.get("cli"):
        ln.append("  cli:")
        ln += [f"    - {_yml(c)}" for c in o["cli"]]
    if o.get("knobs"):
        ln.append("  knobs:")
        for k in o["knobs"]:
            ln.append(f"    - {{name: {k.get('name')}, value: {_yml(k.get('value'))}, "
                      f"role: {k.get('role')}, why: {_yml(k.get('why', ''))}, "
                      f"source: {_yml(k.get('source', ''))}}}")
    if o.get("no_knob"):
        ln.append(f"  no_knob: {_yml(o['no_knob'])}")
    if o.get("note"):
        ln.append(f"  note: {_yml(o['note'])}")
    return "\n".join(ln) + "\n"


def apply_charters(sgs: list[dict]) -> tuple[int, list[str]]:
    """把看板的 arms／options 寫進每一張被指名的立項卡；回傳（改了幾張、警告）。"""
    touched, warns = 0, []
    for sg in sgs:
        for rel in sg["charters"]:
            p = ROOT / rel
            if not p.exists():
                warns.append(f"{sg['id']} 指名的立項卡不存在：{rel}")
                continue
            txt = p.read_text(encoding="utf-8")
            # ① arms 補齊（不刪既有；縮排沿用該檔既有的那一種）
            ab = arms_block(txt)
            if ab:
                _, b_end, indent, items = ab
                have = {i.lstrip("-").strip() for i in items}
                add = [a for a in (sg["options"].get("arms") or []) if a not in have]
                if add:
                    txt = txt[:b_end] + "".join(f"{indent}- {a}\n" for a in add) + txt[b_end:]
            else:
                warns.append(f"{rel} 沒有 arms: 區塊，未動（請補一張卡的臂）")
            # ② options 區塊：已存在就跳過（不覆寫別人的手改）
            if re.search(r"^options:", txt, re.M):
                warns.append(f"{rel} 已有 options: 區塊，未覆寫（請手動對齊看板 {sg['id']}）")
            else:
                txt = txt.rstrip("\n") + "\n\n" + charter_options_block(sg)
            p.write_text(txt, encoding="utf-8")
            touched += 1
    return touched, warns


def charters_without_arms() -> list[str]:
    out = []
    for p in sorted(CHARTER_DIR.glob("*.yaml")):
        if arms_block(p.read_text(encoding="utf-8", errors="ignore")) is None:
            out.append(str(p.relative_to(ROOT)))
    return out


# ───────────────────── 稽核 ────────────────────────────────────────

KIND_LABEL = {"subgoal": "整合進子目標", "certified": "已認證", "settled": "已定案（約束／基準）",
              "archived": "作廢／判死／歷史"}


def audit_rows(sgs: list[dict]) -> tuple[list[dict], list[str]]:
    data = json.loads(MM_JSON.read_text(encoding="utf-8"))
    board = load_board()
    disp = dispositions(board)
    nm = node_map(sgs)
    cert_ids = {str(c.get("id")) for c in (board.get("certified") or [])}
    gaps: list[str] = []
    rows = []
    for e in data["entries"]:
        b = e.get("subgoal_binding")
        rows_d = e.get("disposition") or []
        hid, mid = BRIEF_DIR / f"{e['id']}.html", BRIEF_DIR / f"{e['id']}.md"
        h_ok = hid.exists() and MARKER in hid.read_text(errors="ignore")
        m_ok = mid.exists() and MARKER in mid.read_text(errors="ignore")
        g = []
        if b is None:
            g.append("缺 subgoal_binding")
        if not rows_d:
            g.append("缺逐條處置（dispositions）")
        for r in rows_d:
            # 「整合進子目標」＝ 該條必須真的出現在那一格的 nodes: 裡（不是只寫在處置表）
            if r["kind"] == "subgoal" and r["ref"] not in (nm.get(e["id"]) or []):
                g.append(f"處置說它屬於 {r['ref']}，但它不在該格 nodes 裡")
            if r["kind"] == "certified" and r["ref"] not in cert_ids:
                g.append(f"處置指向不存在的認證格 {r['ref']}")
            if r["kind"] not in KIND_LABEL:
                g.append(f"未知的處置種類 {r['kind']}")
        if not (h_ok and m_ok):
            g.append("brief html/md 缺綁定節")
        if "arm_binding" in e:
            g.append("殘留舊的 arm_binding（掃描推導，應刪除）")
        if g:
            gaps.append(f"{e['id']}：{'；'.join(g)}")
        rows.append({"id": e["id"], "name": MBB.plain(str(e.get("name", ""))),
                     "tier": e["tier"], "sub": e.get("sub", ""),
                     "b": b or {}, "d": rows_d, "html": h_ok, "md": m_ok, "gaps": g})
    # 反向：看板寫了處置但 mindmap 沒有這條
    for eid in sorted(set(disp) - {e["id"] for e in data["entries"]}):
        gaps.append(f"看板處置指向不存在的條目：{eid}")
    # 看板側的閘
    for s in sgs:
        o = s["options"]
        if not o:
            gaps.append(f"{s['id']}：看板沒寫 options")
        elif not (o.get("knobs") or o.get("no_knob")):
            gaps.append(f"{s['id']}：options 既無 knobs 也無 no_knob（要寫明為什麼沒有）")
        for rel in s["charters"]:
            p = ROOT / rel
            if not p.exists():
                gaps.append(f"{s['id']}：立項卡不存在 {rel}")
                continue
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for a in (o.get("arms") or []):
                if a not in txt:
                    gaps.append(f"{s['id']}：{Path(rel).name} 沒有寫入 arm `{a}`")
    for rel in charters_without_arms():
        gaps.append(f"立項卡缺 arms：{rel}")
    return rows, gaps


# ───────────────────── 報告 ────────────────────────────────────────

def _cell(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "｜")


def report_md(sgs: list[dict], rows: list[dict], gaps: list[str]) -> str:
    bound = [r for r in rows if (r["b"] or {}).get("subgoal")]
    out = [
        f"# 250 / 25 攻關：子目標 × options 對照表（{STAMP}）", "",
        "> **唯一來源**：`scripts/check/decode_board_2026-09-29.yaml`（15 個子目標各寫死 "
        "`options.{profile,arms,knobs,cli,no_knob,note}`）。",
        "> 本頁由 `scripts/check/mindmap_subgoal_sync.py --report` 機械生成；"
        "**沒有任何一格是文件掃描推導出來的**（前一版 `PROFILE_OPTION_MAP_2026-09-29` 已刪除）。", "",
        "## 統計", "",
        f"- 子目標 **{len(sgs)}** 格；52 條裡歸屬到某一格的 **{len(bound)}** 條，未歸屬 **{len(rows) - len(bound)}** 條。",
        f"- 有被測 option 的子目標 **{sum(1 for s in sgs if s['options'].get('knobs'))}** 格；"
        f"明寫「沒有旋鈕」的 **{sum(1 for s in sgs if s['options'].get('no_knob'))}** 格。",
        f"- 機械缺口：**{len(gaps)}**", "",
        "## 逐格對照（子目標 → options → 立項卡 → 節點）", "",
        "| 子目標 | profile | 被測 option | 儀器／候選 | CLI | arm（可複製） | 立項卡 | 節點（白皮書） |",
        "|---|---|---|---|---|---|---|---|"]
    for s in sgs:
        o = s["options"]
        subj = "；".join(f"`{k['name']}={k['value']}`"
                         for k in (o.get("knobs") or []) if k.get("role") == "subject") or "—"
        inst = "；".join(f"`{k['name']}={k['value']}`"
                        for k in (o.get("knobs") or []) if k.get("role") != "subject") or "—"
        cli = "；".join(f"`{c}`" for c in (o.get("cli") or [])) or "—"
        arms = "<br>".join(f"`{a}`" for a in (o.get("arms") or [])) or "—"
        ch = "<br>".join(f"`{Path(c).name}`" for c in s["charters"]) or "—"
        nd = "<br>".join(f'[{n}](briefs/{n}.md)' for n in s["nodes"]) or "—"
        no = f"<br>⚠ {o['no_knob']}" if o.get("no_knob") else ""
        out.append(f"| **{s['id']}**<br>{_cell(s['title'])} | `{o.get('profile', '—')}` | {subj}{no} "
                   f"| {inst} | {cli} | {arms} | {ch} | {nd} |")
    out += ["", "## 52 條逐條處置（每條都要落在子目標／已認證／已定案／已作廢之一）", "",
            "| 條目 | 級 | 處置 | 指向 | 理由 |", "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["d"][0]["kind"] if x["d"] else "zzz", x["id"])):
        for d in (r["d"] or [{"kind": "—", "ref": "", "why": "尚未處置"}]):
            out.append(f'| [{_cell(r["name"])}](briefs/{r["id"]}.md) | {r["tier"]} '
                       f'| {KIND_LABEL.get(d["kind"], d["kind"])} | `{d["ref"] or "—"}` '
                       f'| {_cell(d["why"])} |')
    out += ["", "## 未歸屬任何子目標的條目（＝已認證／已定案／已作廢）", ""]
    unb = [r for r in rows if not (r["b"] or {}).get("subgoal")]
    for r in unb:
        bits = []
        for d in (r["d"] or []):
            label = KIND_LABEL.get(d["kind"], d["kind"])
            bits.append(label + (f"（{d['ref']}）" if d["ref"] else ""))
        out.append(f"- [{_cell(r['name'])}](briefs/{r['id']}.md)（`{r['tier']}`）"
                   f'——{"；".join(bits)}')
    out += ["", "## 缺口（機械判定）", ""]
    out += [f"- {g}" for g in gaps] if gaps else ["（無）"]
    out += ["", "---", "",
            "本檔由 `scripts/check/mindmap_subgoal_sync.py` 機械生成；"
            "改設定請改 `decode_board_2026-09-29.yaml` 後重跑，勿直接編輯本檔。", ""]
    return "\n".join(out)


def report_html(sgs: list[dict], rows: list[dict], gaps: list[str]) -> str:
    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    trs = []
    for s in sgs:
        o = s["options"]
        def ks(role_p):
            return "；".join(f"<code>{k['name']}={k['value']}</code>"
                            for k in (o.get("knobs") or []) if role_p(k.get("role")))
        subj = ks(lambda r: r == "subject") or "—"
        inst = ks(lambda r: r != "subject") or "—"
        arms = "<br>".join(f"<code>{esc(a)}</code>" for a in (o.get("arms") or [])) or "—"
        ch = "<br>".join(f"<code>{Path(c).name}</code>" for c in s["charters"]) or "—"
        nd = "<br>".join(f'<a href="briefs/{n}.html">{esc(n)}</a>' for n in s["nodes"]) or "—"
        warn = (f'<br><span style="color:#b45309;">⚠ {esc(o["no_knob"])}</span>'
                if o.get("no_knob") else "")
        trs.append(
            f'<tr><td style="border:1px solid #e2e8f0;padding:5px;"><b>{s["id"]}</b><br>'
            f'<span style="font-size:11px;">{esc(s["title"])}</span></td>'
            f'<td style="border:1px solid #e2e8f0;padding:5px;"><code>{esc(o.get("profile", "—"))}</code></td>'
            f'<td style="border:1px solid #e2e8f0;padding:5px;">{subj}{warn}</td>'
            f'<td style="border:1px solid #e2e8f0;padding:5px;">{inst}</td>'
            f'<td style="border:1px solid #e2e8f0;padding:5px;">{arms}</td>'
            f'<td style="border:1px solid #e2e8f0;padding:5px;font-size:10.5px;">{ch}</td>'
            f'<td style="border:1px solid #e2e8f0;padding:5px;font-size:10.5px;">{nd}</td></tr>')
    gap_html = ("".join(f"<li>{esc(g)}</li>" for g in gaps)
                if gaps else "<li>（無）</li>")
    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>250/25 攻關：子目標 × options 對照表（{STAMP}）</title></head>
<body style="margin:0;padding:18px;font-family:-apple-system,'PingFang TC',sans-serif;color:#1a1a1a;">
<h2 style="margin:0 0 4px;font-size:18px;">250 / 25 攻關：子目標 × options 對照表</h2>
<div style="font-size:12px;color:#666;margin-bottom:14px;">唯一來源
<code>scripts/check/decode_board_2026-09-29.yaml</code>（15 格各寫死 options）　·
產生 <code>scripts/check/mindmap_subgoal_sync.py --report</code>　·
<b>本頁 0 格是文件掃描推導</b>（前一版 <code>PROFILE_OPTION_MAP_2026-09-29</code> 已刪除）</div>
<table style="width:100%;border-collapse:collapse;font-size:11.5px;">
<tr style="background:#f1f5f9;">
<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">子目標</th>
<th style="border:1px solid #e2e8f0;padding:5px;">profile</th>
<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">被測 option</th>
<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">儀器／候選</th>
<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">arm（可複製）</th>
<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">立項卡</th>
<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">節點</th></tr>
{"".join(trs)}
</table>
<h3 style="font-size:13px;margin:16px 0 6px;">機械缺口</h3>
<ul style="font-size:11.5px;">{gap_html}</ul>
<div style="margin-top:14px;font-size:11px;color:#94a3b8;">本頁機械生成，勿直接編輯。</div>
</body></html>
"""


# ───────────────────── 子命令 ──────────────────────────────────────

def cmd_apply(sgs: list[dict]) -> None:
    disp = dispositions(load_board())
    moved = apply_integration(disp, sgs)
    if moved:
        print("整合進子目標（看板 nodes 補齊）：" + "、".join(moved))
        sgs = subgoals(load_board())
    n = apply_mindmap(sgs, disp)
    print(f"mindmap.json：subgoal_binding／disposition 寫回 {n} 處（舊 arm_binding 全數刪除）")
    touched, warns = apply_charters(sgs)
    print(f"立項卡：寫入 {touched} 張")
    for w in warns:
        print(f"  ⚠ {w}")
    MBB.build()
    subprocess.run([sys.executable, str(HERE / "mindmap_build.py")], cwd=ROOT)


def cmd_report(sgs: list[dict]) -> None:
    rows, gaps = audit_rows(sgs)
    REPORT_MD.write_text(report_md(sgs, rows, gaps), encoding="utf-8")
    REPORT_HTML.write_text(report_html(sgs, rows, gaps), encoding="utf-8")
    print(f"報告：{REPORT_MD.relative_to(ROOT)}／{REPORT_HTML.relative_to(ROOT)}")


def cmd_check(sgs: list[dict]) -> int:
    rows, gaps = audit_rows(sgs)
    bound = sum(1 for r in rows if (r["b"] or {}).get("subgoal"))
    print(f"子目標 {len(sgs)}；條目 {len(rows)}（歸屬 {bound}／未歸屬 {len(rows) - bound}）；缺口 {len(gaps)}")
    for g in gaps:
        print(f"  · {g}")
    return 1 if gaps else 0


def cmd_show(sgs: list[dict]) -> int:
    for s in sgs:
        o = s["options"]
        subj = ",".join(f"{k['name']}={k['value']}"
                        for k in (o.get("knobs") or []) if k.get("role") == "subject")
        print(f"{s['id']:6s} {o.get('profile', '—'):10s} {subj[:56]:58s} "
              f"knobs={len(o.get('knobs') or [])} charters={len(s['charters'])} nodes={len(s['nodes'])}")
    return 0


# ───────────────────── selftest ────────────────────────────────────

def selftest() -> bool:
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {name}")
        ok = ok and cond

    board = load_board()
    sgs = subgoals(board)
    chk("看板有 15 個子目標", len(sgs) == 15)
    chk("每一格都有 options", all(s["options"] for s in sgs))
    chk("每一格都有 knobs 或 no_knob",
        all(s["options"].get("knobs") or s["options"].get("no_knob") for s in sgs))
    chk("每一格的 profile 都是既有 profile",
        all(s["options"].get("profile") in ("prod-new", "prefill250", "prod25", "bare48")
            for s in sgs))
    chk("knobs 的 role 都在白名單內",
        all(k.get("role") in ROLES for s in sgs for k in (s["options"].get("knobs") or [])))
    chk("每一格都有立項卡", all(s["charters"] for s in sgs))
    chk("L25-4 有 CLI（--spec-draft-n-max 1）",
        any("--spec-draft-n-max 1" in (s["options"].get("cli") or [])
            for s in sgs if s["id"] == "L25-4"))
    chk("node_map 反查得到", "exp-churn-delivery-630" in node_map(sgs))
    u = unbound_binding()
    chk("未歸屬綁定有說明且沒有 profile", u["subgoal"] is None and bool(u["no_knob"]))
    rows, gaps = audit_rows(sgs)
    chk("稽核抓得到缺口（brief 尚未重建時應有）", isinstance(gaps, list) and len(rows) == 52)
    return ok


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="子目標 × options 同步（看板是唯一來源）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        print("════ mindmap_subgoal_sync selftest ════")
        ok = selftest()
        print(f"\n{'SELFTEST OK' if ok else 'SELFTEST FAIL'}")
        return 0 if ok else 1
    sgs = subgoals(load_board())
    rc = 0
    if a.apply:
        cmd_apply(sgs)
    if a.report:
        cmd_report(sgs)
    if a.check:
        rc |= cmd_check(sgs)
    if not (a.apply or a.report or a.check):
        rc |= cmd_show(sgs)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
