#!/usr/bin/env python3
"""機器檢查：一個被引用的數字，它的量具**當時可不可以武裝**、**今天還活著嗎**。

WHY THIS EXISTS
---------------
2026-09-28 的稽核（`docs/TWENTY_ROUTE_INSTRUMENT_AUDIT_2026-09-28.md`）抓到七次同一族病，而它們
全部不是「程式碼錯」，是**數字比量具新**：

  * `CGC_SEG_BATCH` 2026-09-25 才進 `run_server.sh` 白名單，而 22.22 t/s 量在 **09-24**
    ⇒ 那個數字**不可能**產生自 `harness bench`（產物形狀也證明是 `llama_bench_matrix.py` 的 row）。
  * ρ 的 `+4.7%` 同理：`CGC_RHO_PROBE/FILL` **09-26** 才進白名單，A/B 在 09-23/24。
  * `19.0 / 17.36 / 14.39` **根本沒有量具**，它們是手算分解。
  * `±11.3%` 的噪聲底從它自己的產物重算是 **9.23%**，而該文件裡唯一的 11.3% 是**另一個量**（`skip%`）。

這四件事都可以機械判，所以不該再靠人讀文件讀出來。這支工具就是那個判準。

WHAT IT CHECKS（三個布林）
--------------------------
1. **ARMABLE-ON-DATE**：這個數字 `requires` 的每個 env 開關，其**首次**進入 `run_server.sh`
   白名單的日期（`git log -S<knob> --reverse`，**`--reverse` 才是 first；`-1` 是 last**）
   是否 `<= measured_on`。晚於量測日 ⇒ 那次量測不可能來自該入口。
2. **ALIVE-TODAY**：
   * `FORWARDABLE-NOW`：開關**現在**還在 launcher 的白名單（單行 `SERVER_ENV+=(K ...)` 或
     `for _v in ... ; do` 的清單塊——兩種寫法都要認）。
   * `COMPILED-IN`：開關字串還在**建置產物**裡（用 bytes 搜尋，不靠 `strings`）。
     ⚠ **不能只看一個庫**：`CGC_SEG_BATCH`／`CGC_GPU_TIMING` 在 `libggml-base*`，
     `CGC_CB_N_MAIN` 在 `libggml-metal*`，`CGC_S1_OUT_CAP` 在 `libllama*` —— 只看 libllama 會誤判成死的。
   * `SYMBOL`：宣告的符號還在原始碼裡。
   * `TOOL`：工具在，且（有宣告的話）`--selftest` 跑得起來、rc=0。
   * `ARTIFACTS`：原始產物還在磁碟上（glob 至少命中 `min` 個）。
3. **ENTRY/CALIBER**：入口與 profile 是不是 operator 認可的那一組（登記表頂層宣告）。
4. **BINDING**（2026-09-29 新增）：claim 可以宣告 `binding: [探針, ...]` —— 那些計數器**必須在
   某一份並存的 stderr log 裡真的動過**。上面三項驗的是「量具存在／可武裝／還活著」，沒有一項
   碰得到「它在這一輪真的有在跑」。而 2026-09-29 交付 cell 的四場空白嘗試，偏偏就是可武裝、
   還活著、產物也在，只是**計數器全都是 0**。判準住在 `instrument_binding.py`（寫入端用同一份）。

VERDICT 階梯（嚴重度由重到輕）
------------------------------
```
VOID-NO-INSTRUMENT      kind: estimator —— 它是估計，不是量測
VOID-WRONG-ENTRY        入口不是 accepted_entry（例：llama_bench_matrix 直跑）
VOID-IMPOSSIBLE-FOR-ENTRY  宣告 entry: harness_bench，但某開關首進白名單晚於量測日 ⇒ 自相矛盾
VOID-WRONG-CALIBER      口徑不是 accepted_caliber（例：prod25）
VOID-DEAD-INSTRUMENT    量具今天已經不輸出（開關不在白名單／不在產物／符號沒了／工具或產物沒了）
VOID-INSTRUMENT-UNBOUND 宣告 binding 的探針在每一份並存的 log 裡都是 0／沒印過（旗標設了 ≠ 綁上圖）
VOID-NO-PAIR-LOG        跑出來的產物旁邊**沒有成對的 stderr log** ⇒ 不可引用（沒有 log 時，
                        你不知道自己錯過了什麼 —— 那不是「未驗」，是「不能算」）。
                        只對**跑出來的產物**成立（結構判定），文件類產物與純文件 claim 不受影響。
WARN-UNPROVEN-ENTRY     入口未記錄（unknown）—— 不是錯，是「不知道」
QUOTABLE                以上皆非
```

EXIT CODE
---------
`0` = 每個 claim 都 QUOTABLE；`1` = 至少一個 VOID；`2` = 設定／用法錯誤。
⚠ **今天 rc=1 是預期中的**（登記表裡 5 條是 VOID）。這支工具存在的目的就是讓那件事一直看得見；
把它接到 CI 時要當成「報告」而不是「回歸」。這與 `gate_consistency` 的既有慣例一致。

Usage:
    python3 scripts/check/claim_instrument_check.py                 # 全部 claim
    python3 scripts/check/claim_instrument_check.py 22.22           # 只查含 "22.22" 的 claim
    python3 scripts/check/claim_instrument_check.py --json out.json
    python3 scripts/check/claim_instrument_check.py --self-test
"""
import argparse
import glob as _glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAUNCHER = "scripts/run_server.sh"
REGISTRY = "scripts/check/claim_instruments.yaml"

# 量具可能住在不同層：只看 libllama 會把 ggml 層的開關誤判成死的（2026-09-28 實測）。
BUILD_GLOBS = [
    "src/llama.cpp/build/bin/*.dylib",
    "src/llama.cpp/build/bin/llama-server",
    "src/llama.cpp/build/bin/llama-bench",
    "src/llama.cpp/build/bin/*.metallib",
]
SYMBOL_ROOTS = ["src/llama.cpp/src", "src/llama.cpp/common"]
SYMBOL_EXT = (".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm")

V_NO_INSTRUMENT = "VOID-NO-INSTRUMENT"
V_WRONG_ENTRY = "VOID-WRONG-ENTRY"
V_IMPOSSIBLE = "VOID-IMPOSSIBLE-FOR-ENTRY"
V_WRONG_CALIBER = "VOID-WRONG-CALIBER"
V_DEAD = "VOID-DEAD-INSTRUMENT"
V_UNBOUND = "VOID-INSTRUMENT-UNBOUND"
V_NO_PAIR = "VOID-NO-PAIR-LOG"
W_ENTRY = "WARN-UNPROVEN-ENTRY"
# 明文降級（例：只主張數值身分的 claim 豁免口徑）——與「入口未記錄」不同，所以要有自己的標籤，
# 否則一條宣告了理由的豁免會被叫成「入口未證」（那是錯的描述）。
W_DOWN = "WARN-DOWNGRADED"
V_OK = "QUOTABLE"
QUOTABLE_VERDICTS = (V_OK, W_DOWN)


class Ctx(object):
    """所有對外部的讀取都走這裡，讓 `--self-test` 可以在 temp tree 上跑同一條程式路徑。"""

    def __init__(self, root=ROOT, launcher=LAUNCHER, build_globs=None,
                 symbol_roots=None, knob_dates=None, run_tools=True):
        self.root = root
        self.launcher = launcher
        self.build_globs = build_globs if build_globs is not None else BUILD_GLOBS
        self.symbol_roots = symbol_roots if symbol_roots is not None else SYMBOL_ROOTS
        self._knob_dates = knob_dates          # 測試用覆寫；None = 真的去問 git
        self._knob_cache = {}
        self.run_tools = run_tools
        self._blobs = None

    # ── launcher ────────────────────────────────────────────────────────────
    def launcher_text(self):
        p = os.path.join(self.root, self.launcher)
        if not os.path.exists(p):
            return ""
        return open(p, errors="replace").read()

    def knob_forwardable_now(self, knob, text=None):
        """白名單的兩種寫法都要認：單行 `SERVER_ENV+=("K=$K")` 與 `for _v in ... ; do` 清單塊。"""
        text = self.launcher_text() if text is None else text
        if re.search(r"SERVER_ENV\+=\([^)]*%s" % re.escape(knob), text):
            return True
        for blk in re.findall(r"for\s+_v\s+in(.*?)\bdo\b", text, re.S):
            if re.search(r"\b%s\b" % re.escape(knob), blk):
                return True
        return False

    def knob_first_forwarded(self, knob):
        """開關**首次**進入白名單的日期。`--reverse` 才是 first——`git log -S ... -1` 給的是 last。"""
        if self._knob_dates is not None:
            return self._knob_dates.get(knob)
        if knob in self._knob_cache:
            return self._knob_cache[knob]
        out = None
        try:
            r = subprocess.run(
                ["git", "log", "-S" + knob, "--reverse", "--format=%ad", "--date=short",
                 "--", self.launcher],
                cwd=self.root, capture_output=True, text=True, timeout=60)
            lines = [l for l in r.stdout.splitlines() if l.strip()]
            out = lines[0].strip() if lines else None
        except Exception:
            out = None
        self._knob_cache[knob] = out
        return out

    # ── 建置產物（bytes 搜尋；不靠 strings）────────────────────────────────
    def blobs(self):
        if self._blobs is None:
            self._blobs = []
            for pat in self.build_globs:
                for p in _glob.glob(os.path.join(self.root, pat)):
                    try:
                        self._blobs.append((p, open(p, "rb").read()))
                    except Exception:
                        pass
        return self._blobs

    def compiled_in(self, knob):
        need = knob.encode()
        return [os.path.basename(p) for p, b in self.blobs() if need in b]

    # ── 原始碼符號 ──────────────────────────────────────────────────────────
    def symbol_hits(self, sym):
        hits = []
        for root in self.symbol_roots:
            base = os.path.join(self.root, root)
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in (".git", "build", "__pycache__")]
                for fn in filenames:
                    if not fn.endswith(SYMBOL_EXT):
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        txt = open(p, errors="replace").read()
                    except Exception:
                        continue
                    if sym in txt:
                        hits.append(os.path.relpath(p, self.root))
                        break
                if hits:
                    break
            if hits:
                break
        return hits

    # ── 工具 ────────────────────────────────────────────────────────────────
    def tool_status(self, path, selftest_args, ok_if=None):
        """`ok_if` = 一個 regex：工具**活著**的判準不一定是 rc=0。

        例：`m123_oracle_gate --selftest` 的 8/9 是健康狀態（唯一 FAIL 是自我指涉那條：它要求
        `scripts/check/m123_oracle_gate.py` **自己當下是 dirty 的**，所以乾淨樹上永遠不可能 9/9）。
        硬寫 rc==0 會把一支活著的工具報成死的——那正是本工具要防的那種錯。
        """
        p = os.path.join(self.root, path)
        if not os.path.exists(p):
            return False, "檔案不存在"
        if not selftest_args or not self.run_tools:
            return True, "存在"
        cmd = [sys.executable, p] + list(selftest_args)
        try:
            r = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=300)
        except Exception as e:
            return False, "selftest 無法執行: %s" % type(e).__name__
        out = (r.stdout or "") + (r.stderr or "")
        tail = (out.strip().splitlines()[-1:] or [""])[0]
        if ok_if:
            good = re.search(ok_if, out) is not None
            return good, "selftest 輸出符合 /%s/  (rc=%d, %s)" % (ok_if, r.returncode, tail[:60])
        return r.returncode == 0, "selftest rc=%d  %s" % (r.returncode, tail[:80])

    # ── 產物 ────────────────────────────────────────────────────────────────
    def artifact_hits(self, patterns):
        files = []
        for pat in patterns:
            files += _glob.glob(os.path.join(self.root, pat), recursive=True)
        return sorted(set(files))

    def read_text(self, rel):
        """讀一份 repo 相對路徑的文字；讀不到回 None（不是空字串 —— 兩者對綁定判定不同意義）。"""
        p = rel if os.path.isabs(rel) else os.path.join(self.root, rel)
        if not os.path.exists(p):
            return None
        try:
            with open(p, errors="replace") as f:
                return f.read()
        except Exception:  # noqa: BLE001
            return None

    def binding_logs(self, claim):
        """與宣告產物**並存**的 stderr log（`X.json` ↔ `X.stderr.log`）＋ `binding_logs` 指定的。

        只看並存的：綁定的意思是「這一輪自己的日誌說它動過」，而同一目錄裡的另一場日誌
        不能證明這一場。明文指定的路徑也算（有些舊產物的 log 被搬到別的地方）。
        """
        out = []
        for pat in (claim.get("artifacts") or []):
            for p in _glob.glob(os.path.join(self.root, pat), recursive=True):
                # ① 產物直接就是日誌（有些 claim 就把 stderr log 列成產物）
                if p.endswith((".stderr.log", ".log")):
                    out.append(os.path.relpath(p, self.root))
                    continue
                # ② 產物 JSON 旁邊**並存**的日誌（`X.json` ↔ `X.stderr.log`）：這一輪自己的日誌
                cand = (p[:-len(".json")] + ".stderr.log") if p.endswith(".json") \
                    else p + ".stderr.log"
                if os.path.exists(cand):
                    out.append(os.path.relpath(cand, self.root))
        for p in (claim.get("binding_logs") or []):
            full = p if os.path.isabs(p) else os.path.join(self.root, p)
            if os.path.exists(full):
                out.append(os.path.relpath(full, self.root))
        return sorted(set(out))


_BINDING_MOD = None
_BINDING_WHY = "未載入"


def _binding_mod():
    """(module, why)。綁定判準向 `instrument_binding` 要，**不在本檔重寫一份**（否則遲早漂移）。
    拿不到 ⇒ 回 (None, 理由)，由呼叫端標成 WARN（不是靜默放行）。
    """
    global _BINDING_MOD, _BINDING_WHY
    if _BINDING_MOD is not None or _BINDING_WHY != "未載入":
        return _BINDING_MOD, _BINDING_WHY
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import instrument_binding as _ib
        _BINDING_MOD, _BINDING_WHY = _ib, "instrument_binding.py 已載入"
    except Exception as e:  # noqa: BLE001
        _BINDING_MOD, _BINDING_WHY = None, "instrument_binding.py 不可用（%s）" % e
    return _BINDING_MOD, _BINDING_WHY


def _cmp_dates(a, b):
    """兩個 ISO 日期字串比大小；`unknown`/None 回 None（＝不知道，不猜）。"""
    if not a or not b or a == "unknown" or b == "unknown":
        return None
    return (a > b) - (a < b)


def evaluate(claim, ctx, accepted_entry, accepted_caliber):
    findings = []          # (level, code, detail)
    info = []

    def void(code, detail):
        findings.append(("void", code, detail))

    def warn(code, detail):
        findings.append(("warn", code, detail))

    def ok(code, detail=""):
        findings.append(("ok", code, detail))

    inst = claim.get("instrument") or {}
    measured = str(claim.get("measured_on", "unknown"))
    entry = claim.get("entry", "unknown")
    caliber = claim.get("caliber", "unknown")

    # ── 1. 有沒有量具 ──────────────────────────────────────────────────────
    if inst.get("kind") == "estimator":
        void("NO-INSTRUMENT", "宣告為 estimator：它是**估計**，不是量測，無量具可驗")
    else:
        # ── 2. 入口 ─────────────────────────────────────────────────────────
        knobs = list(claim.get("requires") or [])
        if entry == accepted_entry:
            late = []
            for k in knobs:
                d = ctx.knob_first_forwarded(k)
                rel = _cmp_dates(d, measured)
                if rel is None:
                    warn("ARMABLE-ON-DATE", "%s 首進白名單日不可得（%s）" % (k, d))
                elif rel > 0:
                    late.append((k, d))
            if late:
                void("IMPOSSIBLE-FOR-ENTRY",
                     "宣告 entry=%s，但 %s 首次進白名單（%s）**晚於**量測日 %s ⇒ 那次量測不可能來自它"
                     % (accepted_entry, ", ".join(k for k, _ in late),
                        ", ".join(d for _, d in late), measured))
            else:
                ok("ARMABLE-ON-DATE", "requires 的開關在 %s 之前都可武裝" % measured)
        elif entry in ("unknown", "", None):
            warn("UNPROVEN-ENTRY", "entry 未記錄 ⇒ 無法證明它在 {%s} 上產生" % accepted_entry)
        else:
            void("WRONG-ENTRY",
                 "entry=%s 不是認可入口 %s（未列出的 env 在 launcher 會被靜默丟掉，所以繞過 launcher 的"
                 "入口不受白名單保護）" % (entry, accepted_entry))

        # ── 3. 口徑 ─────────────────────────────────────────────────────────
        if caliber == "unknown":
            info.append("caliber 未記錄")
        elif accepted_caliber and caliber != accepted_caliber:
            # `caliber_exempt` 是**被印出來的**降級，不是靜默放寬：口徑管的是數值以外的東西
            # （profile 決定池大小／命中率），所以一個**只主張數值身分**（不主張吞吐）的 claim
            # 可以豁免——但理由必須寫在登記表裡，並在輸出裡顯示，讓下一個讀者能反駁它。
            ex = claim.get("caliber_exempt")
            if ex:
                warn("CALIBER-EXEMPT", "caliber=%s != %s，宣告豁免：%s"
                     % (caliber, accepted_caliber, ex))
            else:
                void("WRONG-CALIBER", "caliber=%s != 認可的 %s" % (caliber, accepted_caliber))
        else:
            ok("CALIBER", caliber)

        # ── 4. 量具今天還活著嗎 ─────────────────────────────────────────────
        text = ctx.launcher_text()
        for k in knobs:
            fwd = ctx.knob_forwardable_now(k, text)
            comp = ctx.compiled_in(k)
            if not fwd:
                void("DEAD-KNOB", "%s 不在 launcher 白名單裡了 ⇒ 從 --arms 武裝會**靜默無效**" % k)
            elif not comp:
                void("DEAD-KNOB", "%s 不在任何建置產物裡（%d 個）" % (k, len(ctx.blobs())))
            else:
                ok("KNOB-ALIVE", "%s（%s）" % (k, comp[0]))

        for sym in (claim.get("symbols") or []):
            hits = ctx.symbol_hits(sym)
            if hits:
                ok("SYMBOL-ALIVE", "%s @ %s" % (sym, hits[0]))
            else:
                void("DEAD-SYMBOL", "%s 在 %s 找不到" % (sym, "/".join(ctx.symbol_roots)))

        for t in (claim.get("tools") or []):
            good, why = ctx.tool_status(t["path"], t.get("selftest"), t.get("selftest_ok_if"))
            if good:
                ok("TOOL-ALIVE", "%s（%s）" % (t["path"], why))
            else:
                void("DEAD-TOOL", "%s：%s" % (t["path"], why))

        # ── 5. 量具綁定：有没有真的動過（上面四項全部碰不到這件事）─────────────────
        declared = [str(p).strip() for p in (claim.get("binding") or []) if str(p).strip()]
        if declared:
            mod, why = _binding_mod()
            if mod is None:
                warn("BINDING-UNCHECKED", "%s ⇒ 綁定未驗（不是通過）" % why)
            else:
                logs = ctx.binding_logs(claim)
                if not logs:
                    void("UNBOUND", "宣告了 binding=%s，但宣告的產物旁邊沒有任何並存的 stderr log"
                         " ⇒ 沒有證據可驗（沒有證據不是通過）" % ", ".join(declared))
                else:
                    # claim 的 `requires` 就是「它宣告當時武裝了什麼」；拿它當 env 讓視窗退路也適用。
                    env = {k: "1" for k in (claim.get("requires") or [])}
                    bad_unknown = [p for p in declared if p not in mod.PROBES]
                    if bad_unknown:
                        void("UNBOUND", "宣告了未知探針 %s ⇒ 拼錯會被靜默放行，所以先擋"
                             "（已知：%s）" % (", ".join(bad_unknown),
                                             "、".join(sorted(mod.PROBES))))
                    ok_probe, missed = {}, []
                    for rel in [] if bad_unknown else logs:
                        txt = ctx.read_text(rel)
                        if txt is None:
                            missed.append("%s：讀不到" % rel)
                            continue
                        rep = mod.binding_report(txt, env=env, require=declared)
                        for p in declared:
                            st = (rep["probes"].get(p) or {}).get("state")
                            if st == mod.POSITIVE:
                                ok_probe.setdefault(p, "%s（%s max=%s n=%s）"
                                                    % (rel, p, rep["probes"][p]["max"],
                                                       rep["probes"][p]["n"]))
                            elif p in (rep.get("escaped") or []):
                                # 只看得到 `CGC-MM-PUB` 的前 8 個 compute（llama-context.cpp:3943-3945）
                                # ⇒ 逐層列有東西時，不得把「視窗沒照到」判成「沒動過」。
                                ok_probe.setdefault(p, "%s（%s 走視窗退路：%s）"
                                                    % (rel, p, rep.get("escape_why") or ""))
                            else:
                                missed.append("%s：%s=%s" % (rel, p, st or "?"))
                    if bad_unknown:
                        pass
                    elif len(ok_probe) < len(declared):
                        void("UNBOUND", "宣告的探針沒有一個並存的 log 證明它動過：%s；細節：%s"
                             % (", ".join(p for p in declared if p not in ok_probe),
                                "；".join(missed[:4])))
                    else:
                        ok("BINDING", "；".join(ok_probe[p] for p in declared))

        arts = claim.get("artifacts") or []
        if arts:
            hits = ctx.artifact_hits(arts)
            need = int(claim.get("artifacts_min", 1))
            if len(hits) >= need:
                ok("ARTIFACTS", "%d 個（need %d）" % (len(hits), need))
            else:
                void("DEAD-ARTIFACT", "只找到 %d 個（need %d）：%s" % (len(hits), need, "; ".join(arts)))

        # ── 6. 成對：跑出來的產物旁邊有沒有它的 stderr log（2026-09-29）───────────────
        # log 是唯一能證明「量具真的動了」的東西，所以「跑出來的產物沒有 log」不是「未驗」而是
        # **不可引用**：沒有 log 時你不知道自己錯過了什麼（42.9%／41.8% 那兩支就是這樣）。
        # 只認「跑的產物」（結構判定），免得文件類產物被這條誤殺。
        pmod, _pwhy = _binding_mod()
        if pmod is not None:
            run_arts, unpaired = [], []
            for rel in (arts or []):
                cands = ctx.artifact_hits([rel])
                if not cands:
                    continue
                is_run, _d, _w = pmod.load_run_artifact(cands[0])
                if not is_run:
                    continue
                run_arts.append(os.path.relpath(cands[0], ctx.root))
                st = pmod.pair_status(cands[0])
                if not st["paired"]:
                    unpaired.append("%s（%s）" % (os.path.relpath(cands[0], ctx.root), st["why"]))
            if unpaired:
                void("NO-PAIR-LOG", "跑出來的產物旁邊沒有成對的 stderr log ⇒ 不可引用：%s"
                     % "；".join(unpaired[:3]))
            elif run_arts:
                ok("PAIRED", "%d 支跑出來的產物都有成對的 stderr log" % len(run_arts))
            else:
                info.append("沒有「跑出來的產物」可查成對性（文件類產物豁免）")

    # ── verdict：嚴重度由重到輕 ────────────────────────────────────────────
    # `V_NO_PAIR` 排在最前面：連 log 都沒有時，其他每一項判定都失去根據（它們都是「拿那份 log 算的」）。
    order = [V_NO_PAIR, V_NO_INSTRUMENT, V_WRONG_ENTRY, V_IMPOSSIBLE, V_WRONG_CALIBER, V_DEAD, V_UNBOUND]
    code2verdict = {
        "NO-PAIR-LOG": V_NO_PAIR,
        "NO-INSTRUMENT": V_NO_INSTRUMENT,
        "WRONG-ENTRY": V_WRONG_ENTRY,
        "IMPOSSIBLE-FOR-ENTRY": V_IMPOSSIBLE,
        "WRONG-CALIBER": V_WRONG_CALIBER,
        "DEAD-KNOB": V_DEAD, "DEAD-SYMBOL": V_DEAD, "DEAD-TOOL": V_DEAD,
        "DEAD-ARTIFACT": V_DEAD,
        "UNBOUND": V_UNBOUND,
    }
    voids = [code2verdict[c] for lvl, c, _ in findings if lvl == "void" and c in code2verdict]
    warns = [c for lvl, c, _ in findings if lvl == "warn"]
    verdict = next((v for v in order if v in voids), None)
    if verdict is None:
        if "UNPROVEN-ENTRY" in warns:
            verdict = W_ENTRY
        elif warns:
            verdict = W_DOWN
        else:
            verdict = V_OK

    return {"id": claim.get("id"), "value": claim.get("value"), "verdict": verdict,
            "measured_on": measured, "entry": entry, "caliber": caliber,
            "cited_in": claim.get("cited_in"), "caveat": claim.get("caveat"),
            "findings": findings, "info": info,
            "quotable": verdict in QUOTABLE_VERDICTS}


def fmt_human(res):
    mark = {V_OK: "✅", W_ENTRY: "⚠️", W_DOWN: "⚠️"}.get(res["verdict"], "⛔")
    out = ["%s %-34s %s" % (mark, res["verdict"], res["value"])]
    out.append("     id=%s   measured_on=%s   entry=%s   caliber=%s"
               % (res["id"], res["measured_on"], res["entry"], res["caliber"]))
    for lvl, code, detail in res["findings"]:
        s = {"ok": "   ✓", "warn": "   ⚠", "void": "   ✗"}[lvl]
        out.append("%s %-22s %s" % (s, code, detail))
    for line in res["info"]:
        out.append("   · %s" % line)
    if res["caveat"]:
        first = True
        for seg in str(res["caveat"]).split("\n"):
            seg = " ".join(seg.split())
            if not seg:
                continue
            out.append("   %s %s" % ("⚠ caveat：" if first else "          ", seg))
            first = False
    return "\n".join(out)


# ────────────────────────────────────────────────────────────────────────────
def self_test():
    import tempfile
    import textwrap
    import shutil

    ok = True

    def expect(tag, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print("  [%s] %s: %r" % ("OK" if good else "FAIL", tag, got)
              + ("" if good else "   (want %r)" % (want,)))

    print("claim_instrument_check --self-test")
    d = tempfile.mkdtemp(prefix="cict_")
    try:
        # 假樹：launcher（兩種白名單寫法）＋ 一個建置產物 ＋ 一個工具 ＋ 一個產物
        os.makedirs(os.path.join(d, "scripts", "check"))
        os.makedirs(os.path.join(d, "src", "llama.cpp", "build", "bin"))
        with open(os.path.join(d, "scripts", "run_server.sh"), "w") as f:
            f.write(textwrap.dedent("""
                if [ -n "${SYN_KNOB:-}" ]; then
                    SERVER_ENV+=(SYN_KNOB="$SYN_KNOB")
                fi
                for _v in SYN_LOOPKNOB SYN_OTHER; do
                    if [ -n "${!_v:-}" ]; then SERVER_ENV+=("$_v=${!_v}"); fi
                done
            """))
        with open(os.path.join(d, "src", "llama.cpp", "build", "bin",
                               "libllama.0.0.999.dylib"), "wb") as f:
            f.write(b"\x00SYN_KNOB\x00SYN_LOOPKNOB\x00fake")
        with open(os.path.join(d, "scripts", "check", "fake_tool.py"), "w") as f:
            f.write("print('PASS')\n")
        with open(os.path.join(d, "art.json"), "w") as f:
            f.write("{}")

        ctx = Ctx(root=d,
                  build_globs=["src/llama.cpp/build/bin/*.dylib"],
                  symbol_roots=["src/llama.cpp/src"],
                  knob_dates={"SYN_KNOB": "2026-01-01", "SYN_LOOPKNOB": "2026-01-01",
                              "SYN_LATE": "2026-09-25"})
        A, C = "harness_bench", "prod-new"

        def claim(**kw):
            base = dict(id="x", value="v", measured_on="2026-09-20", entry=A, caliber=C,
                        requires=["SYN_KNOB"], artifacts=["art.json"])
            base.update(kw)
            return base

        # 1) 乾淨的一條 -> QUOTABLE
        expect("a clean claim is QUOTABLE",
               evaluate(claim(), ctx, A, C)["verdict"], V_OK)
        # 2) estimator -> 無量具
        expect("an estimator is VOID-NO-INSTRUMENT",
               evaluate(claim(instrument={"kind": "estimator"}), ctx, A, C)["verdict"],
               V_NO_INSTRUMENT)
        # 3) 入口不是認可的
        expect("a matrix entry is VOID-WRONG-ENTRY",
               evaluate(claim(entry="llama_bench_matrix"), ctx, A, C)["verdict"], V_WRONG_ENTRY)
        # 4) 宣告合規入口，但開關晚於量測日 -> 自相矛盾（這就是 22.22 的形狀）
        expect("a knob whitelisted after the measurement is VOID-IMPOSSIBLE-FOR-ENTRY",
               evaluate(claim(requires=["SYN_LATE"]), ctx, A, C)["verdict"], V_IMPOSSIBLE)
        # 5) 口徑不對
        expect("a prod25 caliber is VOID-WRONG-CALIBER",
               evaluate(claim(caliber="prod25"), ctx, A, C)["verdict"], V_WRONG_CALIBER)
        # 5b) 但「只主張數值身分」的 claim 可以**明文**豁免口徑，而降為 WARN（不是 QUOTABLE、
        #     也不是「入口未證」——它有自己的標籤，否則描述就是錯的）
        expect("a declared caliber exemption downgrades to WARN-DOWNGRADED",
               evaluate(claim(caliber="prod25", caliber_exempt="身分證明，不報吞吐"),
                        ctx, A, C)["verdict"], W_DOWN)
        expect("... and it is not silent (the reason is printed)",
               "身分證明" in "".join(
                   d for lvl, code, d in evaluate(
                       claim(caliber="prod25", caliber_exempt="身分證明，不報吞吐"),
                       ctx, A, C)["findings"]), True)
        # 6) 入口未記錄 -> WARN
        expect("an unknown entry is WARN-UNPROVEN-ENTRY",
               evaluate(claim(entry="unknown"), ctx, A, C)["verdict"], W_ENTRY)
        # 7) 量具死了：開關不在白名單（且不在產物裡）
        expect("a knob that is nowhere is VOID-DEAD-INSTRUMENT",
               evaluate(claim(requires=["SYN_GONE"]), ctx, A, C)["verdict"], V_DEAD)
        # 8) 產物不見了
        expect("a missing artifact is VOID-DEAD-INSTRUMENT",
               evaluate(claim(artifacts=["nope/*.json"]), ctx, A, C)["verdict"], V_DEAD)
        # 9) `for _v in` 清單塊也算白名單（只看單行寫法會誤判成死的）
        r = evaluate(claim(requires=["SYN_LOOPKNOB"]), ctx, A, C)
        expect("the for-loop allowlist counts as forwardable", r["verdict"], V_OK)
        # 10) 工具 selftest 會真的被跑
        r = evaluate(claim(tools=[{"path": "scripts/check/fake_tool.py",
                                   "selftest": ["--self-test"]}]), ctx, A, C)
        expect("a tool selftest is executed", r["verdict"], V_OK)
        expect("... and a missing tool is VOID-DEAD-INSTRUMENT",
               evaluate(claim(tools=[{"path": "scripts/check/nope.py"}]), ctx, A, C)["verdict"], V_DEAD)
        # 11) --reverse 的教訓：first ≠ last
        expect("knob_first_forwarded uses FIRST, not LAST",
               ctx.knob_first_forwarded("SYN_KNOB"), "2026-01-01")

        # ── 12) 量具綁定：上面的檢查全部碰不到「它在這一輪真的有在跑嗎」──
        # 並存的 log 形狀：(a) 真的動過 / (b) 印了但全是 0（2026-09-29 交付 cell 的形狀）
        with open(os.path.join(d, "art_b.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(d, "art_b.stderr.log"), "w") as f:
            f.write("CGC-MM-PUB n_leaf=39 wrote=39\n"
                    "MISSMASK il=1 step=6 nsel=8 misses=6\n")
        with open(os.path.join(d, "art_dead.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(d, "art_dead.stderr.log"), "w") as f:
            f.write("CGC-MM-PUB n_leaf=0 wrote=0\n"
                    "CGC-MISSMASK-STEP: step=390 misses=0 layers=0\n")
        expect("binding：探針真的動過 ⇒ QUOTABLE",
               evaluate(claim(artifacts=["art_b.json"], binding=["mm_pub_n_leaf"]),
                        ctx, A, C)["verdict"], V_OK)
        expect("binding：印了但全是 0 ⇒ VOID-INSTRUMENT-UNBOUND",
               evaluate(claim(artifacts=["art_dead.json"], binding=["mm_pub_n_leaf"]),
                        ctx, A, C)["verdict"], V_UNBOUND)
        expect("binding：宣告了但沒有並存的 log ⇒ VOID（沒有證據不是通過）",
               evaluate(claim(artifacts=["art.json"], binding=["mm_pub_n_leaf"]),
                        ctx, A, C)["verdict"], V_UNBOUND)
        expect("binding：拼錯探針名也要擋（否則静默放行）",
               evaluate(claim(artifacts=["art_b.json"], binding=["mm_pub_n_leafd"]),
                        ctx, A, C)["verdict"], V_UNBOUND)
        expect("binding：沒宣告 binding 的 claim 不受影響",
               evaluate(claim(artifacts=["art_b.json"]), ctx, A, C)["verdict"], V_OK)
        # 視窗退路：`CGC-MM-PUB` 只看得到前 8 個 compute（llama-context.cpp:3943-3945）⇒
        # 全 0 但有逐層 miss 列時，不得把「視窗沒照到」判成「沒動過」。
        with open(os.path.join(d, "art_win.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(d, "art_win.stderr.log"), "w") as f:
            f.write("CGC-MM-PUB n_leaf=0 wrote=0\n" * 8
                    + "MISSMASK il=1 step=6 nsel=8 misses=6\n")
        expect("binding：全 0 但有逐層 miss 列 ⇒ 走視窗退路、不判死",
               evaluate(claim(artifacts=["art_win.json"], binding=["mm_pub_n_leaf"]),
                        ctx, A, C)["verdict"], V_OK)

        # ── 13) 成對：跑出來的產物旁邊必須有它的 stderr log（缺 ⇒ 不可引用，不是「未驗」）──
        with open(os.path.join(d, "run_bare.json"), "w") as f:
            json.dump([{"tag": "prod-new", "rows": [{"n_prompt": 0, "n_gen": 64}]}], f)
        with open(os.path.join(d, "run_ok.json"), "w") as f:
            json.dump([{"tag": "prod-new", "rows": [{"n_prompt": 0, "n_gen": 64}]}], f)
        with open(os.path.join(d, "run_ok.stderr.log"), "w") as f:
            f.write("CGC-SPAC: feeds=1\n")
        expect("成對：跑出來的產物沒有 log ⇒ VOID-NO-PAIR-LOG",
               evaluate(claim(artifacts=["run_bare.json"]), ctx, A, C)["verdict"], V_NO_PAIR)
        expect("成對：有並存的 log ⇒ QUOTABLE（同一份判準：pair_status）",
               evaluate(claim(artifacts=["run_ok.json"]), ctx, A, C)["verdict"], V_OK)
        expect("成對：文件類產物（不是跑的產物）不受影響",
               evaluate(claim(artifacts=["art_b.json"]), ctx, A, C)["verdict"], V_OK)
        expect("成對：路徑不存在 ⇒ 本來就是 DEAD-ARTIFACT，不重複報成對",
               evaluate(claim(artifacts=["nope/*.json"]), ctx, A, C)["verdict"], V_DEAD)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("  -> %s" % ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("claim", nargs="?", default=None, help="只查 id/value 含此子字串的 claim")
    ap.add_argument("--registry", default=REGISTRY)
    ap.add_argument("--json", default=None, metavar="PATH")
    ap.add_argument("--no-tools", action="store_true", help="不跑工具的 selftest（快）")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    try:
        import yaml
    except ImportError:
        print("[claim-instrument] 需要 PyYAML", file=sys.stderr)
        return 2
    path = os.path.join(ROOT, a.registry)
    if not os.path.exists(path):
        print("[claim-instrument] 找不到登記表 %s" % a.registry, file=sys.stderr)
        return 2
    reg = yaml.safe_load(open(path))
    accepted_entry = reg.get("accepted_entry", "harness_bench")
    accepted_caliber = reg.get("accepted_caliber", "prod-new")
    claims = reg.get("claims") or []
    if a.claim:
        claims = [c for c in claims
                  if a.claim in str(c.get("id", "")) or a.claim in str(c.get("value", ""))]
        if not claims:
            print("[claim-instrument] 沒有 claim 符合 %r" % a.claim, file=sys.stderr)
            return 2

    ctx = Ctx(run_tools=not a.no_tools)
    print("claim_instrument_check  (accepted_entry=%s  accepted_caliber=%s)"
          % (accepted_entry, accepted_caliber))
    print("registry: %s   claims: %d   建置產物: %d"
          % (a.registry, len(claims), len(ctx.blobs())))
    print()
    results = []
    for c in claims:
        r = evaluate(c, ctx, accepted_entry, accepted_caliber)
        results.append(r)
        print(fmt_human(r))
        print()
    n_void = sum(1 for r in results if r["verdict"].startswith("VOID"))
    n_warn = sum(1 for r in results if r["verdict"].startswith("WARN"))
    n_ok = sum(1 for r in results if r["quotable"])
    print("=" * 72)
    print("VERDICT: %d QUOTABLE / %d WARN / %d VOID   （rc=%d）"
          % (n_ok, n_warn, n_void, 1 if n_void else 0))
    print("⚠ VOID 不等於『現象不存在』，等於『這個數字不能這樣引用』。")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"accepted_entry": accepted_entry,
                       "accepted_caliber": accepted_caliber,
                       "results": results}, f, ensure_ascii=False, indent=2)
        print("json -> %s" % a.json)
    return 1 if n_void else 0


if __name__ == "__main__":
    sys.exit(main())
