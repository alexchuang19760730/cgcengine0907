#!/usr/bin/env python3
"""mindmap_profile_audit — 250/25 攻關總圖：逐條「profile ＋ option ＋ 測試 log 報告」對照掃描。

為什麼需要它
    總圖 52 條各自有判詞，但「這一條到底是掛在哪個 profile 的哪個 option 上跑出來的」原本
    只存在於 4 條（有 `runs` 的實驗節點）裡；其餘 48 條只寫了依據文件，沒有人能從總圖上
    回答「它的 arm 是什麼、log 在哪一份」。本工具把這件事變成可機檢的：

        · 每一條都要有 `arm_binding`（profile ＋ option，arm 語法 `<profile>:K=V;K=V`）
        · 每一條都要有 html ＋ md 兩份逐條白皮書，且兩份都要有「Profile 綁定與測試 Log 報告」節
        · 每一條都要有可點的測試 log／報告（runs 的 log，或證據報告 md/html）；都沒有就是缺口

綁定來源（優先序，寫進 `arm_binding.source`，誰優先就是誰負責）
    ① run    ：節點自帶 `runs[].arm`（實跑過，有 log）          → confidence high
    ② charter：同名 `scripts/check/charters/<id>.yaml` 的 `arms[]` → confidence high
    ③ scan   ：從該條映射到的證據文件裡掃 arm／option 出現次數    → confidence med
    ④ default：沒有任何 arm 線索 ⇒ `prod-new` 預設臂、無自己的 option → confidence low
    ⑤ n_a    ：非實驗結論（tier=na／約束／帳本／入口索引／跨線）⇒ 無待測 option

用法
    python3 scripts/check/mindmap_profile_audit.py                 # 掃描 ＋ 印對照表
    python3 scripts/check/mindmap_profile_audit.py --apply         # 寫回 arm_binding ＋ 重建
    python3 scripts/check/mindmap_profile_audit.py --report        # 產出 md ＋ html 對照報告
    python3 scripts/check/mindmap_profile_audit.py --check         # 缺一條就 rc=1
    python3 scripts/check/mindmap_profile_audit.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mindmap_build as MB            # noqa: E402  （總圖：資料載入／docs 映射／重建）
import mindmap_brief_build as MBB     # noqa: E402  （逐條白皮書重建）

ROOT = Path(__file__).resolve().parents[2]
MM_JSON = ROOT / "docs/mindmap" / "mindmap.json"
BRIEF_DIR = ROOT / "docs/mindmap" / "briefs"
CHARTER_DIR = ROOT / "scripts" / "check" / "charters"
REPORT_MD = ROOT / "docs/mindmap" / "PROFILE_OPTION_MAP_2026-09-29.md"
REPORT_HTML = ROOT / "docs/mindmap" / "PROFILE_OPTION_MAP_2026-09-29.html"
STAMP = "2026-09-29"

# ── profile（arm 語法的前綴；真正來源是 run_server.sh 的解析，這裡只做字串層辨識）──
# ⚠ `devserver` 是**分支名**不是 profile：docs 裡出現的次數只代表那份報告寫在 devserver 期，
#   把它當 profile 會把 09-05~09-07 期的條目綁到一個不存在的臂上 ⇒ 不列入。
PROFILES = ("prod-new", "prefill250", "prod25", "bare48")
DEFAULT_PROFILE = "prod-new"          # charter 規則：「臂：預設一律基於 prod-new」

PROFILE_RE = re.compile(r"\b(prod-new|prefill250|prod25|bare48)\b")
ARM_RE = re.compile(
    r"\b((?:prod-new|prefill250|prod25|bare48)"
    r"(?::[A-Za-z_][A-Za-z0-9_]*=[^`\s\"'；;，,）)、]+)*)\b")
OPT_RE = re.compile(r"\b((?:CGC|LLAMA)_[A-Z0-9_]{3,})\s*=\s*([0-9A-Za-z_.:-]{1,16})")
# 環境變數形式的 profile 宣告（比文件裡的提及次數更可靠）
PROFILE_ENV_RE = re.compile(r"\bCGC_SERVER_PROFILE\s*=\s*(prod-new|prefill250|prod25|bare48)")
# 非 CGC_／LLAMA_ 的少數引擎變數（run_server.sh 會吃）；其餘（含文件裡的 `KEY=VAL` 佔位）一律不算
OTHER_OPT_RE = re.compile(r"\b(WORKERS|ALLOW_NGL|CTX|BATCH|UBATCH|NGL)\s*=\s*([0-9A-Za-z_.:-]{1,16})")
# `CGC_SERVER_PROFILE=prod25` 是**宣告 profile**，不是被測 option ⇒ 抽出後不進 options
PROFILE_DECL_RE = re.compile(r"\bCGC_SERVER_PROFILE\s*=\s*(prod-new|prefill250|prod25|bare48)")
LOG_RE = re.compile(r"\b(Backup/[A-Za-z0-9_./-]+\.json)\b")

# 儀器開關不是「被測的 option」：只開計時／取樣，不改演算法路徑 ⇒ 分開列，不進 options
INSTRUMENT_RE = re.compile(
    r"^(CGC_GPU_TIMING|CGC_DECODE_PROFILE|CGC_DECODE_PROFILE_ALL|CGC_DUMP_ENV|CGC_PHASE_TIMING"
    r"|CGC_PHASE_DBG|CGC_GPU_NODES|CGC_GPU_NODES_START|CGC_GPU_NODES_MATRIX|CGC_GPU_OPS"
    r"|CGC_DISPATCH_CENSUS|CGC_ELEMW_CENSUS|CGC_TENSOR_CAPTURE|CGC_TENSOR_CAPTURE_WORDS"
    r"|CGC_TENSOR_CAPTURE_TAIL|CGC_PREFLIGHT_KILL|CGC_WINDOW_OVERRIDE|CGC_RIG_SNAPSHOT"
    r"|CGC_MISS_MASK_DBG|CGC_S1_DBG|CGC_PREBIND_PROBE_VERBOSE|CGC_HOOK_PROFILE|CGC_N_CB"
    r"|CGC_RHO_PROBE_LATE|CGC_ORACLE_MODEL_DIGEST|CGC_EXACT_CACHE_VERIFY|CGC_MTP_PERF"
    r"|CGC_DST_FILTER_MAX|CGC_DST_STRIDE|CGC_S1_MIN_IL)$")

# 非實驗結論的主題／tier：本來就沒有「待測 option」
NON_EXPERIMENTAL_THEMES = ("約束", "帳本", "上界算術", "入口索引", "跨線產品", "跨產品", "紀錄", "設計")
MAX_OPTIONS = 3


# ───────────────────── 純邏輯（selftest 測這一層）─────────────────────

OPT_NAME_RE = re.compile(r"^(?:CGC|LLAMA)_[A-Z0-9_]{3,}$|^(?:WORKERS|ALLOW_NGL|CTX|BATCH|UBATCH|NGL)$")


def parse_arm(arm: str) -> tuple[str, list[str]]:
    """`'prod-new:CGC_X=1;CGC_Y=2'` → `('prod-new', ['CGC_X=1','CGC_Y=2'])`。

    未知前綴（不是既有 profile）一律視為沒有 profile、也不當 option —— 寧可標缺口，
    也不要把 `on_k1` 這種自訂標籤當成 profile。option 名字不合引擎變數格式者同理丟棄
    （文件裡的 `KEY=VAL` 佔位就是這樣來的）。
    """
    s = (arm or "").strip()
    if not s:
        return "", []
    head, _, tail = s.partition(":")
    if head not in PROFILES:
        return "", []
    opts = [o.strip() for o in tail.split(";")
            if o.strip() and "=" in o and OPT_NAME_RE.match(o.split("=", 1)[0])]
    return head, opts


def split_options(opts: list[str]) -> tuple[list[str], list[str], str]:
    """option →（被測 option／儀器開關／profile 宣告值）。

    `CGC_SERVER_PROFILE=prod25` 會被抽出來當 profile（它是「用哪個臂」的宣告，
    不是「這個臂測什麼」）⇒ 不進 options，否則會出現「option 是換 profile」這種循環。
    """
    subj, inst, decl = [], [], ""
    for o in opts:
        name = o.split("=", 1)[0]
        if name == "CGC_SERVER_PROFILE":
            decl = o.split("=", 1)[1]
            continue
        (inst if INSTRUMENT_RE.match(name) else subj).append(o)
    return subj, inst, decl


def _exists(rel: str) -> bool:
    try:
        return (ROOT / rel).exists()
    except Exception:
        return False


def make_binding(profile: str, options: list[str], source: str, note: str,
                 instruments: list[str] | None = None, logs: list[str] | None = None,
                 reports: list[str] | None = None) -> dict:
    opts = list(dict.fromkeys(o for o in options if o))
    inst = list(dict.fromkeys(i for i in (instruments or []) if i and i not in opts))
    conf = {"run": "high", "charter": "high", "scan": "med",
            "default": "low", "n_a": "n/a"}.get(source, "low")
    arm = profile + (":" + ";".join(opts) if opts else "")
    # ⚠ 只有真的在工作區裡的檔案才進 `logs`：Backup/ 大半被 .gitignore 排除，
    #   把不存在的路徑寫成連結 ⇒ brief 的「相對連結都有效」檢查會紅，讀者點開是 404。
    #   不存在者另存 `logs_missing`，在 brief 裡以純文字標示（保留出處，但不假裝可點）。
    logs_all = list(dict.fromkeys(logs or []))
    reports_all = [r for r in dict.fromkeys(reports or []) if _exists(r)]
    return {
        "arm": arm,
        "profile": profile,
        "options": opts,
        "instruments": inst,
        "source": source,
        "confidence": conf,
        "note": note,
        "logs": [l for l in logs_all if _exists(l)][:3],
        "logs_missing": [l for l in logs_all if not _exists(l)][:3],
        "reports": reports_all[:4],
    }


# ───────────────────── 掃描來源 ────────────────────────────────────

_DOCS_CACHE: dict[str, list[str]] = {}


def docs_of_entry(data: dict, eid: str) -> list[str]:
    """該條映射到的證據文件（`docs` glob 匹配）＋ `evid` 指名的那一份。"""
    if eid in _DOCS_CACHE:
        return _DOCS_CACHE[eid]
    docs = MB.branch_docs()
    scoped = MB.scope_docs(docs)
    mapping, _ = MB.map_docs(data["entries"], scoped)
    ev = next((x.get("evid", "") for x in data["entries"] if x["id"] == eid), "")
    out = sorted({d for d, i in mapping.items() if i == eid and (ROOT / d).exists()})
    m = re.search(r"([A-Za-z0-9_./-]+\.(?:md|html))", ev or "")
    if m and (ROOT / m.group(1)).exists() and m.group(1) not in out:
        out.append(m.group(1))
    if not out:
        # 退回「不分 scope」：SCOPE_RE 只認攻關關鍵字，跨線／產品類條目（例 na-crossline）
        # 的證據檔名一個都對不上 ⇒ 那份條目會變成「有 docs glob 卻 0 份報告」的假缺口。
        import fnmatch
        pats = next((x.get("docs") or [] for x in data["entries"] if x["id"] == eid), [])
        for d in docs:
            if any(fnmatch.fnmatch(Path(d).name, p) for p in pats) and (ROOT / d).exists():
                out.append(d)
    _DOCS_CACHE[eid] = out
    return out


def scan_text(text: str) -> tuple[Counter, Counter, Counter, list[str]]:
    """一份文件 →（profile 計數、option 計數、arm 字串計數、Backup log 路徑）。"""
    prof = Counter(PROFILE_RE.findall(text))
    prof.update(PROFILE_ENV_RE.findall(text))
    opts = Counter(f"{k}={v}" for k, v in OPT_RE.findall(text))
    arms = Counter(a for a in ARM_RE.findall(text) if "=" in a)
    logs = LOG_RE.findall(text)
    return prof, opts, arms, logs


def charter_arms(eid: str) -> list[str]:
    """同名 charter 的 `arms:`（YAML 可用就用，不可用就退化成文字塊掃描）。"""
    for name in (f"{eid}.yaml", f"e-{eid}.yaml", f"exp-{eid}.yaml"):
        p = CHARTER_DIR / name
        if not p.exists():
            continue
        try:
            import yaml  # type: ignore
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            arms = d.get("arms")
            if isinstance(arms, list):
                return [str(a) for a in arms if a]
        except Exception:
            pass
        txt = p.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^arms:\s*\n((?:\s+-\s*\S+\s*\n?)+)", txt, re.M)
        if m:
            return [ln.strip().lstrip("- ").strip()
                    for ln in m.group(1).splitlines() if ln.strip()]
    return []


def bind_entry(e: dict, data: dict) -> dict:
    """單條 → arm_binding（含來源與置信度）。"""
    eid = e["id"]
    # ① 實跑過的 arm（最權威：有 cmd、有 log）
    for r in (e.get("runs") or []):
        prof, opts = parse_arm(str(r.get("arm", "")))
        if prof:
            subj, inst, decl = split_options(opts)
            logs = [str(r["log"])] if r.get("log") else []
            return make_binding(decl or prof, subj, "run",
                                f"節點自帶實跑 arm（{len(e.get('runs') or [])} 筆 run，log 可點）",
                                inst, logs)
    # ② 同名 charter 的 arms[]：優先帶 option 的那一臂（對照臂通常是裸 profile）
    ch = [a for a in charter_arms(eid) if parse_arm(a)[0]]
    if ch:
        with_opt = [a for a in ch if parse_arm(a)[1]]
        arm = with_opt[0] if with_opt else ch[0]
        prof, opts = parse_arm(arm)
        subj, inst, decl = split_options(opts)
        _files = docs_of_entry(data, eid)
        _logs = []
        for f in _files[:20]:
            try:
                _logs += scan_text((ROOT / f).read_text(errors="ignore"))[3]
            except Exception:
                pass
        return make_binding(decl or prof, subj, "charter",
                            f"取自 `scripts/check/charters/{eid}.yaml` 的 `arms[]`"
                            + ("（取有 option 的那一臂；對照臂是裸 profile）" if with_opt else ""),
                            inst, _logs, pick_reports(_files))
    # ⑤ 非實驗結論：本來就不該有待測 option（但報告還是要給得出來，否則讀者無從查）
    theme = str(e.get("theme", ""))
    if e.get("tier") == "na" or any(t in theme for t in NON_EXPERIMENTAL_THEMES):
        return make_binding(DEFAULT_PROFILE, [], "n_a",
                            "非實驗結論（約束／帳本／上界算術／入口索引／跨線）⇒ 無待測 option；"
                            "其數字全部來自 prod-new 預設臂",
                            [], [], pick_reports(docs_of_entry(data, eid)))
    # ③ 證據文件掃描（＋節點自己的文字，權重最低）
    files = docs_of_entry(data, eid)
    prof, opts, arms, logs = Counter(), Counter(), Counter(), []
    for f in files[:20]:
        try:
            p_, o_, a_, l_ = scan_text((ROOT / f).read_text(errors="ignore"))
        except Exception:
            continue
        prof.update(p_)
        opts.update(o_)
        arms.update(a_)
        logs += l_
    own = json.dumps(e, ensure_ascii=False)
    p_, o_, a_, l_ = scan_text(own)
    prof.update(p_)
    opts.update(o_)
    arms.update(a_)
    logs += l_
    rep = pick_reports(files)
    if arms:
        arm = arms.most_common(1)[0][0]
        prof_h, subj = parse_arm(arm)
        if prof_h:
            s_, inst, decl = split_options(subj)
            return make_binding(decl or prof_h, s_, "scan",
                                f"從證據文件掃到的 arm 字串（{len(files)} 份文件）", inst,
                                logs, rep)
    if prof or opts:
        profile = DEFAULT_PROFILE
        if prof:
            # 文件裡的提及次數只是證據強度；有 `CGC_SERVER_PROFILE=` 就聽它的
            profile = prof.most_common(1)[0][0]
        # 出現 ≥2 次才當「被測 option」；只有 1 次的話照樣列出但置信度壓到 low
        strong = [o for o, c in opts.most_common(40) if c >= 2]
        subj, inst, decl = split_options(strong)
        note = (f"從 {len(files)} 份證據文件掃 option 出現次數（取 ≥2 次者前 {MAX_OPTIONS} 個；"
                f"儀器開關已分開列）")
        if not subj:
            subj, inst, decl2 = split_options([o for o, _ in opts.most_common(1)])
            decl = decl or decl2
            note = (f"證據文件裡只有出現 1 次的 option（{subj[0] if subj else '—'}）"
                    f"⇒ 置信度壓到 low，需人工確認")
        b = make_binding(decl or profile, subj[:MAX_OPTIONS], "scan", note, inst, logs, rep)
        if not strong:
            b["confidence"] = "low"
        return b
    # ④ 什麼都沒有
    return make_binding(DEFAULT_PROFILE, [], "default",
                        "無 arm 線索：按 charter 規則綁 prod-new 預設臂，"
                        "本條沒有自己的 option（視為缺口，需補證據）")


def pick_reports(files: list[str]) -> list[str]:
    """證據報告：優先同名 md/html 成對者，最多 4 份。"""
    md = [f for f in files if f.endswith(".md")]
    html = [f for f in files if f.endswith(".html")]
    paired = []
    for m in md:
        h = m[:-3] + ".html"
        paired += ([m, h] if h in html else [m])
    out = (paired or files)[:4]
    return out


# ───────────────────── 稽核 ────────────────────────────────────────

MARKER = "Profile 綁定"          # brief 裡新節的錨點（html／md 都用同一個詞）


def audit_row(data: dict, e: dict) -> dict:
    b = e.get("arm_binding") or {}
    hid, mid = BRIEF_DIR / f"{e['id']}.html", BRIEF_DIR / f"{e['id']}.md"
    h_ok = hid.exists() and MARKER in hid.read_text(errors="ignore")
    m_ok = mid.exists() and MARKER in mid.read_text(errors="ignore")
    logs = list(b.get("logs") or [])
    for r in (e.get("runs") or []):
        if r.get("log") and _exists(str(r["log"])):
            logs.append(str(r["log"]))
    logs = list(dict.fromkeys(l for l in logs if l))
    logs_missing = list(b.get("logs_missing") or [])
    reports = b.get("reports") or []
    has_log = bool(logs)
    has_rep = bool(reports)
    gaps = []
    if not b.get("profile"):
        gaps.append("缺 profile")
    if not (h_ok and m_ok):
        gaps.append("brief html/md 缺綁定節" if not (h_ok or m_ok) else
                    ("md 缺綁定節" if not m_ok else "html 缺綁定節"))
    # log 只有「不在工作區」的版本不算缺口（`Backup/` 大半被 .gitignore 排除，
    # 那是版控政策的結果，不是這條沒做實驗）⇒ 但仍要在報表上標出來，不能被當成有得點。
    if not (has_log or has_rep or logs_missing):
        gaps.append("無測試 log／報告")
    if b.get("source") == "default":
        gaps.append("綁定是預設值（無證據）")
    return {
        "id": e["id"], "name": MBB.plain(str(e.get("name", ""))),
        "stage": MB.STAGE_OF.get(e["tier"], "closed"),
        "tier": e["tier"], "sub": e.get("sub", ""),
        "profile": b.get("profile", ""), "arm": b.get("arm", ""),
        "options": b.get("options", []), "instruments": b.get("instruments", []),
        "source": b.get("source", ""), "confidence": b.get("confidence", ""),
        "logs": logs, "logs_missing": logs_missing, "reports": reports,
        "html": h_ok, "md": m_ok, "gaps": gaps,
    }


def audit(data: dict) -> list[dict]:
    return [audit_row(data, e) for e in data["entries"]]


# ───────────────────── 報告 ────────────────────────────────────────

STAGE_LABEL = {"prod": "生產交付", "exp": "實驗階段", "closed": "已結案（廢棄／不適用）"}
SRC_LABEL = {"run": "① 實跑 arm", "charter": "② charter arms", "scan": "③ 證據文件掃描",
             "default": "④ 預設臂（無證據）", "n_a": "⑤ 非實驗結論"}


def _cell(s: str) -> str:
    s = " ".join(str(s).split())
    return s.replace("|", "｜")


def _href_from_map(p: str) -> str:
    """對照報告放在 `docs/mindmap/` ⇒ 從那裡連到 repo 內任何檔案的相對路徑。"""
    pp = Path(p)
    if pp.parts and pp.parts[0] == "docs":
        return "../" + str(Path(*pp.parts[1:]))
    return "../../" + str(pp)


def report_md(rows: list[dict]) -> str:
    n = len(rows)
    gap_rows = [r for r in rows if r["gaps"]]
    out = [
        f"# 250 / 25 攻關：52 條 × profile option 對照表（{STAMP}）", "",
        "> 規則：`docs/MEASUREMENT_CONTRACT_2026-09-25.md` §5（四級）＋ 臂語法 "
        "`<profile>:<option>=<值>`（charter 規則「臂：預設一律基於 prod-new」）",
        "> 產生：`python3 scripts/check/mindmap_profile_audit.py --apply --report`；"
        "資料 `docs/mindmap/mindmap.json` 的 `arm_binding` 欄位。", "",
        "## 統計", "",
        "| 階段 | 條數 | 有實跑 log | 有報告 | 綁定來自實跑／charter | 缺口 |",
        "|---|---|---|---|---|---|",
    ]
    for st in ("prod", "exp", "closed"):
        rs = [r for r in rows if r["stage"] == st]
        out.append(
            f'| **{STAGE_LABEL[st]}** | {len(rs)} | '
            f'{sum(1 for r in rs if r["logs"])} | '
            f'{sum(1 for r in rs if r["reports"])} | '
            f'{sum(1 for r in rs if r["source"] in ("run", "charter"))} | '
            f'{sum(1 for r in rs if r["gaps"])} |')
    out += [f"| **合計** | **{n}** | {sum(1 for r in rows if r['logs'])} | "
            f"{sum(1 for r in rows if r['reports'])} | "
            f"{sum(1 for r in rows if r['source'] in ('run', 'charter'))} | "
            f"**{len(gap_rows)}** |", "",
            "## 逐條對照", "",
            "| # | 條目 | 階段 | profile | option（被測） | 來源 | 測試 log／報告 | 白皮書 |",
            "|---|---|---|---|---|---|---|---|"]
    for st in ("prod", "exp", "closed"):
        rs = [r for r in rows if r["stage"] == st]
        out.append(f"| | **{STAGE_LABEL[st]}（{len(rs)}）** | | | | | | |")
        for i, r in enumerate(rs, 1):
            opts = "；".join(r["options"]) if r["options"] else "（無自己的 option）"
            docs = []
            if r["logs"]:
                docs += [f'[log]({_href_from_map(l)})' for l in r["logs"][:2]]
            if r["logs_missing"]:
                docs += [f'⚠ {Path(l).name}（不在工作區）' for l in r["logs_missing"][:1]]
            if r["reports"]:
                docs += [f'[報告]({_href_from_map(p)})' for p in r["reports"][:1]]
            brief = (f'[md](briefs/{r["id"]}.md)　[html](briefs/{r["id"]}.html)'
                     if (r["md"] and r["html"]) else "⚠ 缺")
            out.append(f'| {i} | [{_cell(r["name"])}](briefs/{r["id"]}.md) | {r["tier"]} | '
                       f'`{r["profile"] or "—"}` | `{_cell(opts)}` | '
                       f'{SRC_LABEL.get(r["source"], r["source"])} | '
                       f'{"　".join(docs) if docs else "⚠ 無"} | {brief} |')
    out += ["", "## 缺口（機械判定）", ""]
    if gap_rows:
        out += ["| 條目 | 缺口 |", "|---|---|"]
        out += [f'| [{_cell(r["name"])}](briefs/{r["id"]}.md) | {"；".join(r["gaps"])} |'
                for r in gap_rows]
    else:
        out.append("（無）")
    out += ["", "---", "",
            "本檔由 `scripts/check/mindmap_profile_audit.py` 機械生成；"
            "改內容請改 `mindmap.json` 後重跑，勿直接編輯本檔。", ""]
    return "\n".join(out)


def report_html(rows: list[dict]) -> str:
    def esc(s: str) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    body = []
    for st in ("prod", "exp", "closed"):
        rs = [r for r in rows if r["stage"] == st]
        body.append(f'<h3 style="margin:16px 0 6px;font-size:14px;">{STAGE_LABEL[st]}'
                    f'（{len(rs)} 條）</h3>')
        body.append('<table style="width:100%;border-collapse:collapse;font-size:11.5px;">'
                    '<tr style="background:#f1f5f9;">'
                    '<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">條目</th>'
                    '<th style="border:1px solid #e2e8f0;padding:5px;">級</th>'
                    '<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">profile</th>'
                    '<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">option</th>'
                    '<th style="border:1px solid #e2e8f0;padding:5px;">來源</th>'
                    '<th style="border:1px solid #e2e8f0;padding:5px;text-align:left;">log／報告</th>'
                    '<th style="border:1px solid #e2e8f0;padding:5px;">白皮書</th></tr>')
        for r in rs:
            opts = "；".join(r["options"]) if r["options"] else "（無自己的 option）"
            docs = []
            if r["logs"]:
                docs += [f'<a href="{_href_from_map(l)}" target="_blank">{Path(l).name}</a>'
                         for l in r["logs"][:2]]
            if r["logs_missing"]:
                docs += [f'<span style="color:#94a3b8;">⚠ {Path(l).name}（不在工作區）</span>'
                         for l in r["logs_missing"][:1]]
            if r["reports"]:
                docs += [f'<a href="{_href_from_map(p)}" target="_blank">{Path(p).name}</a>'
                         for p in r["reports"][:1]]
            gap = ('<span style="color:#dc2626;">⚠ ' + esc("；".join(r["gaps"])) + "</span>"
                   if r["gaps"] else "")
            body.append(
                f'<tr><td style="border:1px solid #e2e8f0;padding:5px;">'
                f'<a href="briefs/{r["id"]}.html">{esc(r["name"])}</a> {gap}</td>'
                f'<td style="border:1px solid #e2e8f0;padding:5px;text-align:center;">{esc(r["tier"])}</td>'
                f'<td style="border:1px solid #e2e8f0;padding:5px;"><code>{esc(r["profile"] or "—")}</code></td>'
                f'<td style="border:1px solid #e2e8f0;padding:5px;"><code>{esc(opts)}</code></td>'
                f'<td style="border:1px solid #e2e8f0;padding:5px;text-align:center;">'
                f'{esc(SRC_LABEL.get(r["source"], r["source"]))}</td>'
                f'<td style="border:1px solid #e2e8f0;padding:5px;">'
                f'{"　".join(docs) if docs else "⚠ 無"}</td>'
                f'<td style="border:1px solid #e2e8f0;padding:5px;text-align:center;">'
                f'<a href="briefs/{r["id"]}.html">html</a>　<a href="briefs/{r["id"]}.md">md</a></td></tr>')
        body.append("</table>")
    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>250/25 攻關：52 條 × profile option 對照表（{STAMP}）</title></head>
<body style="margin:0;padding:18px;font-family:-apple-system,'PingFang TC',sans-serif;color:#1a1a1a;">
<h2 style="margin:0 0 4px;font-size:18px;">250 / 25 攻關：52 條 × profile option 對照表</h2>
<div style="font-size:12px;color:#666;margin-bottom:14px;">臂語法 <code>&lt;profile&gt;:&lt;option&gt;=&lt;值&gt;</code>
　·　產生：<code>scripts/check/mindmap_profile_audit.py --apply --report</code>
　·　資料：<code>docs/mindmap/mindmap.json</code> 的 <code>arm_binding</code></div>
{"".join(body)}
<div style="margin-top:14px;font-size:11px;color:#94a3b8;">本頁機械生成，勿直接編輯。</div>
</body></html>
"""


# ───────────────────── 子命令 ──────────────────────────────────────

def load() -> dict:
    return json.loads(MM_JSON.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    # indent=2 是這份檔既有的排版：寫回時若換成別的縮排，整個檔會變成一份
    # 「每一行都改過」的 diff，審的人看不出到底動了什麼（而且會和別線同檔的編輯打架）。
    MM_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")


def cmd_apply(data: dict) -> int:
    n = 0
    for e in data["entries"]:
        b = bind_entry(e, data)
        if e.get("arm_binding") != b:
            e["arm_binding"] = b
            n += 1
    save(data)
    print(f"arm_binding 寫回：{n} 條新增／更新（共 {len(data['entries'])} 條）")
    MBB.build()
    print("briefs（html＋md）已重建")
    subprocess.run([sys.executable, str(HERE / "mindmap_build.py")], cwd=ROOT)
    return 0


def cmd_report(data: dict) -> int:
    rows = audit(data)
    REPORT_MD.write_text(report_md(rows), encoding="utf-8")
    REPORT_HTML.write_text(report_html(rows), encoding="utf-8")
    print(f"報告：{REPORT_MD.relative_to(ROOT)}／{REPORT_HTML.relative_to(ROOT)}")
    return 0


def cmd_check(data: dict) -> int:
    rows = audit(data)
    gaps = [r for r in rows if r["gaps"]]
    print(f"條目 {len(rows)}；有缺口 {len(gaps)}")
    for r in gaps:
        print(f"  · {r['id']}：{'；'.join(r['gaps'])}")
    return 1 if gaps else 0


def cmd_show(data: dict) -> int:
    rows = audit(data)
    print(f"{'id':30s} {'stage':7s} {'prof':11s} {'src':8s} {'log':3s} {'rep':3s} opt")
    for r in rows:
        print(f"{r['id'][:29]:30s} {r['stage']:7s} {r['profile'] or '—':11s} "
              f"{r['source']:8s} {len(r['logs']):3d} {len(r['reports']):3d} "
              f"{';'.join(r['options'])[:60]}")
    print(f"\n條目 {len(rows)}；缺口 {sum(1 for r in rows if r['gaps'])}")
    return 0


# ───────────────────── selftest ────────────────────────────────────

def selftest() -> bool:
    ok = True

    def chk(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {name}")
        ok = ok and cond

    chk("parse_arm 正常", parse_arm("prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1")
        == ("prod-new", ["CGC_SEG_BATCH=1", "CGC_B_SCHEME=1"]))
    chk("parse_arm 只有 profile", parse_arm("prod-new") == ("prod-new", []))
    chk("parse_arm 未知前綴不當 profile", parse_arm("on_k1:CGC_X=1") == ("", []))
    chk("parse_arm 空字串", parse_arm("") == ("", []))
    chk("parse_arm 丟掉 KEY=VAL 這類佔位", parse_arm("prod25:KEY=VAL") == ("prod25", []))
    chk("devserver 不是 profile（它是分支名）", parse_arm("devserver:CGC_X=1") == ("", []))
    s, i, d = split_options(["CGC_A=1", "CGC_GPU_TIMING=1", "CGC_DECODE_PROFILE=1"])
    chk("split_options 儀器分離", s == ["CGC_A=1"] and len(i) == 2 and d == "")
    chk("split_options 抽出 profile 宣告",
        split_options(["CGC_SERVER_PROFILE=prod25"])[2] == "prod25")
    chk("split_options 的 profile 宣告不進 option",
        split_options(["CGC_SERVER_PROFILE=prod25"])[0] == [])
    b = make_binding("prod-new", ["CGC_A=1"], "run", "x", ["CGC_GPU_TIMING=1"], ["Backup/a.json"])
    chk("make_binding arm 組裝", b["arm"] == "prod-new:CGC_A=1" and b["confidence"] == "high")
    chk("make_binding 無 option 時不加冒號",
        make_binding("prod-new", [], "n_a", "")["arm"] == "prod-new")
    chk("n_a 不需要 option", make_binding("prod-new", [], "n_a", "")["confidence"] == "n/a")
    chk("audit_row 抓到缺口", bool(audit_row({"entries": []}, {"id": "zzz-none", "tier": "3a",
                                                             "sub": "S"})["gaps"]))
    chk("報告可產生", "52 條" in report_md(audit(load())))
    return ok


# ───────────────────── main ────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="52 條 × profile option 對照掃描")
    ap.add_argument("--apply", action="store_true", help="寫回 arm_binding 並重建 briefs／總圖")
    ap.add_argument("--report", action="store_true", help="產出 md ＋ html 對照報告")
    ap.add_argument("--check", action="store_true", help="缺一條就 rc=1")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        print("════ mindmap_profile_audit selftest ════")
        ok = selftest()
        print(f"\n{'SELFTEST OK' if ok else 'SELFTEST FAIL'}")
        return 0 if ok else 1
    data = load()
    rc = 0
    if a.apply:
        rc |= cmd_apply(data)
        data = load()
    if a.report:
        rc |= cmd_report(data)
    if a.check:
        rc |= cmd_check(data)
    if not (a.apply or a.report or a.check):
        rc |= cmd_show(data)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
