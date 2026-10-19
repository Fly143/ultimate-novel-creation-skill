#!/usr/bin/env python3
"""check-done 契约回归（维护者工具）。同一 fixture 双跑 py / ps1。

用法：
  python .github/scripts/test-check-done.py
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 临时夹具目录统一回收（CI 一次性 runner 无感；本地反复回归不在系统临时目录堆积脚本副本）
_tmpdirs: list[str] = []
_mk_tempdir = tempfile.mkdtemp


def _tracked_mkdtemp(*a, **k):
    d = _mk_tempdir(*a, **k)
    _tmpdirs.append(d)
    return d


tempfile.mkdtemp = _tracked_mkdtemp
atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in _tmpdirs])

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-done.py"
PS1 = ROOT / "scripts" / "check-done.ps1"
RULES = ROOT / "scripts" / "check-done-rules.json"
PY = sys.executable

FLAG_MAP = {
    "--strict-report": "-StrictReport",
    "--soft-report": "-SoftReport",
    "--no-chapter-done": "-NoChapterDone",
    "--include-chapter-done": "-IncludeChapterDone",
    "--allow-non-empty": "-AllowNonEmpty",
    "--require-task-sheet": "-RequireTaskSheet",
}


def find_powershell() -> str | None:
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            return path
    return None


PS_RUNNER = find_powershell()
# 命名约定（R17③）：新增用例名必须带 [N轮] 前缀，使实施记录逐轮 +N 增量可机器对账。
ENGINES = ["py"] + (["ps1"] if PS_RUNNER else [])


def run_py(project: Path, chapter: int, *extra: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, str(SCRIPT), "-p", str(project), "-c", str(chapter), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _decode(data: bytes | None) -> str:
    """ps1 输出编码随控制台而变（Windows 中文环境默认 GBK，CI 为 UTF-8）→ 依次尝试，避免文案断言假红。"""
    if not data:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_PN_TOKEN = __import__("re").compile(r"[A-Za-z]:[\\/][^\s\"'<>|*?,，；、（）]+")


def _pnorm_one(_m):
    tok = _m.group(0).replace("/", "\\")
    tok = tok[0].upper() + tok[1:]
    try:
        import os as _os
        # realpath 对不存在路径也会解析存在的 8.3 短名前缀（尾部缺失段保留）→ 无需 exists 前置
        return _os.path.realpath(tok)
    except (OSError, ValueError):
        return tok


def _pnorm(s: str) -> str:
    """路径形态归一：CI 的 temp 路径（8.3 短名/盘符大小写/正斜杠）在 py 与 .NET 两侧呈现不同 → 对拍前统一展开为长名+大写盘符+反斜杠。"""
    return _PN_TOKEN.sub(_pnorm_one, s)


def run_ps1(project: Path, chapter: int, *extra: str) -> tuple[int, str]:
    mapped = [FLAG_MAP[e] for e in extra]
    proc = subprocess.run(
        [
            PS_RUNNER,
            "-ExecutionPolicy",
            "Bypass",
            "-NoProfile",
            "-File",
            str(PS1),
            "-Project",
            str(project),
            "-Chapter",
            str(chapter),
            *mapped,
        ],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)


LAST_RUN: dict = {}


def run(project: Path, chapter: int, *extra: str, engine: str = "py") -> tuple[int, str]:
    _res = _run_impl(project, chapter, *extra, engine=engine)
    LAST_RUN["v"] = _res
    return _res


def _run_impl(project: Path, chapter: int, *extra: str, engine: str = "py") -> tuple[int, str]:
    if engine == "ps1":
        return run_ps1(project, chapter, *extra)
    return run_py(project, chapter, *extra)


def check_engines_case(name: str, ok: bool, fn) -> list[bool]:
    """单实现（state_commit 引擎已在用例内循环）用例登记。"""
    try:
        cond = bool(fn())
    except Exception as exc:  # noqa: BLE001
        cond = False
        detail = f"{type(exc).__name__}: {exc}"
        print(f"  [FAIL] {name} — {detail}")
        return [cond == ok]
    print(f"  [{'PASS' if cond == ok else 'FAIL'}] {name}")
    return [cond == ok]

STATE_PY = ROOT / "scripts" / "state_commit.py"
STATE_PS1 = ROOT / "scripts" / "state_commit.ps1"
STATE_ENGINES = ["py", "ps1"]  # main() 强制 ps1 在场（缺则 rc1）；下方 [SKIP] 未检测到 PowerShell 分支为历史回退，现不可达 if PS_RUNNER else ["py"]


def run_state(engine: str, command: str, *args: str) -> tuple[int, str]:
    _res = _run_state_impl(engine, command, *args)
    LAST_RUN["v"] = _res
    return _res


def _run_state_impl(engine: str, command: str, *args: str) -> tuple[int, str]:
    """调用 state_commit（命令与 flag 分离：py 用 --flag，ps1 用 -Command/-Flag）。"""
    if engine == "ps1":
        mapped: list[str] = ["-Command", command]
        it = iter(args)
        for a in it:
            val = next(it, "")
            flag = {"--project": "-Project", "--chapter": "-Chapter", "--input": "-InputFile"}.get(a, a)
            mapped += [flag, val]
        proc = subprocess.run(
            [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(STATE_PS1), *mapped],
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
    proc = subprocess.run(
        [PY, str(STATE_PY), command, *args], capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)


def write_txn(project: Path, chapter: int, delta: dict) -> Path:
    """draft 后填充 delta（两引擎共用：文件格式一致）。"""
    txn = project / ".story" / "work" / f"第{chapter:03d}章" / "state.json"
    txn.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads((project / "记忆系统" / "_state.json").read_text(encoding="utf-8"))
    payload = {
        "chapter": chapter,
        "expected_state_revision": state.get("state_revision", 0),
        "mode": "commit",
        "delta": delta,
    }
    txn.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return txn


def chapter_delta(chapter: int, who: str) -> dict:
    return {
        "characters": {who: {"snapshot": f"第{chapter}章后状态", "known_facts": [f"fact{chapter}"]}},
        "facts": [{"chapter": chapter, "subject": who, "fact": f"事实{chapter}", "value": "v"}],
        "timeline": {"readers_known": [{"chapter": chapter, "event": f"事件{chapter}"}], "authors_truth": []},
        "summaries": {f"{chapter:03d}": f"第{chapter}章摘要内容。"},
        "wordcounts": {f"{chapter:03d}": {"chars": 3000 + chapter, "target": 3000, "band_source": "default"}},
        "foreshadow": {f"F{chapter:03d}": {"desc": f"线索{chapter}", "planted_ch": chapter,
                                "planned_resolve_ch": chapter + 10, "resolved_ch": None, "priority": "高"}},
        "context": {"position": {"chapter": chapter, "volume": "第一卷", "story_time": f"第{chapter}天"}},
    }


def empty_done(
    project: Path,
    chapter: int,
    marks: list[str],
    pad: bool = True,
    with_chapter: bool = True,
) -> None:
    done = project / ".done"
    done.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}" if pad else str(chapter)
    for m in marks:
        (done / f"第{nnn}章_{m}.done").write_bytes(b"")
    if with_chapter:
        (done / f"第{nnn}章_chapter.done").write_bytes(b"")


def write_report(project: Path, chapter: int, body: str, pad: bool = True) -> None:
    d = project / "报告"
    d.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}" if pad else str(chapter)
    (d / f"第{nnn}章_全量审核报告.md").write_text(body, encoding="utf-8")


def write_body(project: Path, chapter: int, paragraphs: int, pad: bool = True) -> None:
    d = project / "正文"
    d.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}" if pad else str(chapter)
    lines = [f"第{nnn}章 测试章"]
    for i in range(1, paragraphs + 1):
        lines.append(f"这是第{i}段正文，用于交叉校验段号上限。")
        lines.append("")
    (d / f"第{nnn}章_测试章.txt").write_text("\n".join(lines), encoding="utf-8")


def write_sheet(
    project: Path,
    chapter: int,
    sections: tuple[str, ...] = ("TASK", "GOAL", "MUST DO", "CONTEXT"),
    must_items: tuple[str, ...] = (),
    pad: bool = True,
) -> None:
    """设定/细纲/第NNN章_细纲.md 任务书区块（P0-2 结构门禁 fixture；仓库真实约定路径）。"""
    d = project / "设定" / "细纲"
    d.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}" if pad else str(chapter)
    blocks = []
    titles = {
        "TASK": "TASK 本章任务",
        "GOAL": "GOAL 原子目标",
        "MUST DO": "MUST DO 必须完成",
        "CONTEXT": "CONTEXT 信息索引",
    }
    for sec in ("TASK", "GOAL", "MUST DO", "CONTEXT"):
        if sec not in sections:
            continue
        if sec == "MUST DO":
            body = "\n".join(must_items) if must_items else "（无）"
            blocks.append(f"### {titles[sec]}\n{body}")
        else:
            blocks.append(f"### {titles[sec]}\n内容占位")
    (d / f"第{nnn}章_细纲.md").write_text(
        "## 章节任务书\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8"
    )


BASE = [
    "wordcount",
    "bible",
    "summary",
    "zhuque",
    "gates",
    "constraints",
    "logic_causal",
    "review",
    "quality",
    "coherence",
    "rhythm",
    "merge",
]

GOOD_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 人工段注入：第3段、第7段，合计约320字\n"
    "- 段落位置：第3段起，已写入报告\n"
    "- 结构破坏：中段跳时间，未解释信息残留\n"
    "- 对话毛刺：主要角色口癖2处\n"
)

WEAK_REPORT = "# 审核\n## 人工证据包\n已注入\n"

FAKE_WORDCOUNT_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 本章合计约2500字\n"
    "- ≥300字\n"
    "- 结构破坏\n"
    "- 对话毛刺\n"
)

STRATEGY_ONLY_REPORT = (
    "# 审核\n"
    "## 人工证据包\n"
    "- 人工为主，密度自查通过\n"
    "- 按人工为主协议执行强化轮\n"
)

FAKE_HUMAN_WORDCOUNT_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 人工痕迹不足，合计约2500字\n"
    "- 结构破坏\n"
    "- 对话毛刺\n"
)

FAKE_NEGATED_INJECT_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 人工注入不足，合计约2500字\n"
    "- ≥300字\n"
    "- 结构破坏\n"
)

# 声明第40段，正文只有 8 段 → body-mismatch
OVERCLAIM_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 人工段注入：第40段，合计约320字\n"
    "- 段落位置：第3段起\n"
    "- 结构破坏\n"
)


def case(name: str, ok: bool, cond: bool, detail: str = "") -> bool:
    if cond != ok and os.environ.get("SUITE_DEBUG") and "LAST" not in detail:
        _lr = LAST_RUN.get("v")
        if _lr:
            detail = f"{detail} | last_rc={_lr[0]} last_out={_lr[1][-260:]}"
    status = "PASS" if cond == ok else "FAIL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail and not cond else ''}")
    return cond == ok


def check_engines(name: str, ok: bool, fn, detail: str = "") -> list[bool]:
    results = []
    for engine in ENGINES:
        try:
            cond = bool(fn(engine))
            detail_e = detail
        except Exception as exc:  # noqa: BLE001
            cond = False
            detail_e = f"{type(exc).__name__}: {exc}"
        results.append(case(f"{name} [{engine}]", ok, cond, detail_e))
    return results


def _run(results: list[bool]) -> int:
    print(f"引擎：{', '.join(ENGINES)}")
    if not RULES.is_file():
        print(f"❌ 缺少规则单源：{RULES}")
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="check-done-test-") as tmp:
            tmp_path = Path(tmp)

            p1 = tmp_path / "p1"
            p1.mkdir()
            results += check_engines(
                "缺 .done 目录 → exit1",
                True,
                lambda e, p=p1: run(p, 1, engine=e)[0] == 1,
            )

            p2 = tmp_path / "p2"
            (p2 / ".done").mkdir(parents=True)
            results += check_engines(
                "全部标记缺失 → exit1",
                True,
                lambda e, p=p2: run(p, 1, engine=e)[0] == 1,
            )

            # 默认硬门禁：齐标记+chapter 但无报告 → exit1
            p3 = tmp_path / "p3"
            empty_done(p3, 1, BASE)
            write_body(p3, 1, 10)
            results += check_engines(
                "默认 空标记齐+chapter+无报告 → exit1",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 1,
            )
            results += check_engines(
                "soft 无报告 → exit0",
                True,
                lambda e, p=p3: run(p, 1, "--soft-report", engine=e)[0] == 0,
            )
            results += check_engines(
                "strict 兼容别名 无报告 → exit1",
                True,
                lambda e, p=p3: run(p, 1, "--strict-report", engine=e)[0] == 1,
            )

            write_report(p3, 1, GOOD_REPORT)
            results += check_engines(
                "默认 结构合格报告 → exit0",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 0,
            )
            results += check_engines(
                "strict 结构合格报告 → exit0",
                True,
                lambda e, p=p3: run(p, 1, "--strict-report", engine=e)[0] == 0,
            )

            write_report(p3, 1, WEAK_REPORT)
            results += check_engines(
                "默认 弱证据报告 → exit1",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 1,
            )
            results += check_engines(
                "soft 弱证据报告 → exit0",
                True,
                lambda e, p=p3: run(p, 1, "--soft-report", engine=e)[0] == 0,
            )

            write_report(p3, 1, FAKE_WORDCOUNT_REPORT)
            results += check_engines(
                "默认 裸字数+弱锚点 → exit1",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 1,
            )

            write_report(p3, 1, FAKE_HUMAN_WORDCOUNT_REPORT)
            results += check_engines(
                "默认 裸「人工」+字数 → exit1",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 1,
            )

            write_report(p3, 1, FAKE_NEGATED_INJECT_REPORT)
            results += check_engines(
                "默认 否定式人工注入不足 → exit1",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 1,
            )

            write_report(p3, 1, STRATEGY_ONLY_REPORT)
            results += check_engines(
                "默认 仅策略话术 → exit1",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 1,
            )

            # 正文段号交叉
            write_report(p3, 1, OVERCLAIM_REPORT)
            results += check_engines(
                "默认 报告段号超正文 → exit1",
                True,
                lambda e, p=p3: run(p, 1, engine=e)[0] == 1,
            )
            results += check_engines(
                "默认 报告段号超正文 含交叉校验文案",
                True,
                lambda e, p=p3: "正文交叉校验" in run(p, 1, engine=e)[1],
            )
            results += check_engines(
                "soft 报告段号超正文 → exit0",
                True,
                lambda e, p=p3: run(p, 1, "--soft-report", engine=e)[0] == 0,
            )

            write_report(p3, 1, GOOD_REPORT)

            # chapter 默认必需
            results += check_engines(
                "默认 缺 chapter → exit1",
                True,
                lambda e, p=p3: (
                    (lambda: [
                        (p3 / ".done" / "第001章_chapter.done").unlink(missing_ok=True),
                        run(p3, 1, engine=e)[0],
                    ])()[-1] == 1
                ),
            )
            # 恢复 chapter
            (p3 / ".done" / "第001章_chapter.done").write_bytes(b"")
            results += check_engines(
                "no-chapter-done 缺 chapter 时放行",
                True,
                lambda e, p=p3: (
                    (lambda: [
                        (p3 / ".done" / "第001章_chapter.done").unlink(missing_ok=True),
                        run(p3, 1, "--no-chapter-done", engine=e)[0],
                        (p3 / ".done" / "第001章_chapter.done").write_bytes(b""),
                    ])()[1] == 0
                ),
            )
            results += check_engines(
                "include-chapter-done 兼容别名 有 chapter → exit0",
                True,
                lambda e, p=p3: run(p3, 1, "--include-chapter-done", engine=e)[0] == 0,
            )

            p4 = tmp_path / "p4"
            empty_done(p4, 1, BASE)
            (p4 / ".done" / "第001章_merge.done").write_text("FAKE", encoding="utf-8")
            write_report(p4, 1, GOOD_REPORT)
            write_body(p4, 1, 10)
            results += check_engines(
                "非空标记 → exit1",
                True,
                lambda e, p=p4: run(p, 1, engine=e)[0] == 1,
            )
            results += check_engines(
                "allow-non-empty → exit0",
                True,
                lambda e, p=p4: run(p, 1, "--allow-non-empty", engine=e)[0] == 0,
            )

            p5 = tmp_path / "p5"
            empty_done(p5, 10, BASE, with_chapter=False)
            results += check_engines(
                "ch10 缺 innovation → exit1",
                True,
                lambda e, p=p5: run(p, 10, engine=e)[0] == 1,
            )
            empty_done(p5, 10, ["innovation"])
            write_report(p5, 10, GOOD_REPORT)
            write_body(p5, 10, 10)
            results += check_engines(
                "ch10 含 innovation+chapter+报告 → exit0",
                True,
                lambda e, p=p5: run(p, 10, engine=e)[0] == 0,
            )

            p6 = tmp_path / "p6"
            empty_done(p6, 1, BASE, pad=False)
            write_report(p6, 1, GOOD_REPORT, pad=False)
            write_body(p6, 1, 10, pad=False)
            results += check_engines(
                "不填充章号 第1章 → exit0",
                True,
                lambda e, p=p6: run(p, 1, engine=e)[0] == 0,
            )

            # 双章号并存
            p7 = tmp_path / "p7"
            empty_done(p7, 1, BASE, with_chapter=False)
            for mark in BASE:
                (p7 / ".done" / f"第1章_{mark}.done").write_bytes(b"")
            (p7 / ".done" / "第1章_chapter.done").write_bytes(b"")
            write_report(p7, 1, GOOD_REPORT)
            write_body(p7, 1, 10)

            def dual_chapter_warns(engine: str) -> bool:
                code, out = run(p7, 1, engine=engine)
                return code == 0 and ("章号命名冲突" in out or "命名冲突" in out)

            results += check_engines(
                "双章号并存 → exit0 且告警",
                True,
                dual_chapter_warns,
            )

            def allow_non_empty_warns(engine: str) -> bool:
                code, out = run(p4, 1, "--allow-non-empty", engine=engine)
                return code == 0 and "禁止" in out and "闭环" in out

            results += check_engines(
                "allow-non-empty 含禁用警示 → exit0",
                True,
                allow_non_empty_warns,
            )

            def soft_report_warns(engine: str) -> bool:
                # p3 恢复为弱报告场景：先写弱报告
                write_report(p3, 1, WEAK_REPORT)
                code, out = run(p3, 1, "--soft-report", engine=engine)
                # 断言钉到真实文案：状态行恒打印，仅断言它 ≈ 只断 rc=0（历史假阴性）
                return code == 0 and "⚠️ 报告校验警告" in out and "报告校验：weak-section（soft）" in out

            results += check_engines(
                "soft-report 警告文案 → exit0",
                True,
                soft_report_warns,
            )

            # ===== P0-2 章节任务书结构门禁 =====
            p8 = tmp_path / "p8"
            empty_done(p8, 1, BASE)
            write_report(p8, 1, GOOD_REPORT)
            write_body(p8, 1, 10)

            # 1) 缺任务书 → require 下 exit1
            results += check_engines(
                "任务书缺失 → require-task-sheet exit1",
                True,
                lambda e, p=p8: (
                    run(p, 1, "--require-task-sheet", engine=e)[0] == 1
                    and "任务书校验" in run(p, 1, "--require-task-sheet", engine=e)[1]
                ),
            )

            # 2) 缺 CONTEXT 段 → exit1 并点名缺段
            write_sheet(p8, 1, sections=("TASK", "GOAL", "MUST DO"))
            results += check_engines(
                "任务书缺 CONTEXT 段 → exit1 点名缺段",
                True,
                lambda e, p=p8: (
                    (lambda r: r[0] == 1 and "CONTEXT" in r[1])(
                        run(p, 1, "--require-task-sheet", engine=e)
                    )
                ),
            )

            # 3) 四段齐但无必落拍 → exit1
            write_sheet(p8, 1)
            results += check_engines(
                "MUST DO 无必落拍 → exit1",
                True,
                lambda e, p=p8: (
                    (lambda r: r[0] == 1 and "必落" in r[1])(
                        run(p, 1, "--require-task-sheet", engine=e)
                    )
                ),
            )

            # 4) 必落拍未勾选 → exit1
            write_sheet(p8, 1, must_items=("- [ ] 节拍1（必落）", "- [x] 节拍2（必落）"))
            results += check_engines(
                "必落拍未全勾 → exit1（漏拍判未完成）",
                True,
                lambda e, p=p8: (
                    (lambda r: r[0] == 1 and "漏必落拍" in r[1])(
                        run(p, 1, "--require-task-sheet", engine=e)
                    )
                ),
            )

            # 5) 全勾 → exit0
            write_sheet(p8, 1, must_items=("- [x] 节拍1（必落）", "- [x] 节拍2（必落）"))
            results += check_engines(
                "任务书四段齐+必落拍全勾 → exit0",
                True,
                lambda e, p=p8: run(p, 1, "--require-task-sheet", engine=e)[0] == 0,
            )

            # 6) 不带 require（默认口径不变）→ 缺任务书仍 exit0
            for rel in ("设定/细纲", "细纲"):
                for f in (p8 / rel).glob("*_细纲.md"):
                    f.unlink()
            results += check_engines(
                "默认不带 require → 缺任务书仍 exit0（不回退既有口径）",
                True,
                lambda e, p=p8: run(p, 1, engine=e)[0] == 0,
            )

            # ===== P0-3 状态单真源（规格 §六 六条；state_commit 双引擎）=====
            for engine in STATE_ENGINES:
                proj = tmp_path / f"p_state_{engine}"
                proj.mkdir()
                code, _out = run_state(engine, "init", "--project", str(proj))
                _derived = [p for p in (proj / "记忆系统").rglob("*") if p.is_file() and p.name != "_state.json"]
                results.append(case(
                    f"[state:{engine}] init → exit0 且落盘 _state.json+派生视图", True,
                    code == 0 and (proj / "记忆系统" / "_state.json").is_file() and len(_derived) >= 1,
                    f"rc={code} derived={len(_derived)}",
                ))
                # 准备：ch1 提交成功
                run_state(engine, "draft", "--project", str(proj), "--chapter", "1")
                txn1 = write_txn(proj, 1, chapter_delta(1, "艾露"))
                run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(txn1))

                # 条目1：手改派生 → check FAIL 且点名文件
                def tamper_check(e=engine, pr=proj):
                    tgt = pr / "记忆系统" / "圣经" / "事实台账.md"
                    tgt.write_text(tgt.read_text(encoding="utf-8") + "\n（手改）\n", encoding="utf-8")
                    code, out = run_state(e, "check", "--project", str(pr))
                    return code == 1 and "derived view differs from _state.json" in out and "事实台账.md" in out
                results += check_engines_case(f"[state:{engine}] 手改派生 → check exit1 点名文件", True, tamper_check)
                # 修复：用 state_render 重新渲染后再 check（派生须回到逐字一致）
                def rerender_check(e=engine, pr=proj):
                    subprocess.run(
                        [PY, str(ROOT / "scripts" / "state_render.py"), "--project", str(pr)],
                        capture_output=True,
                    )
                    return run_state(e, "check", "--project", str(pr))[0] == 0
                results += check_engines_case(f"[state:{engine}] 重渲染（state_render）后 check → exit0", True, rerender_check)

                # 条目2：stale revision → 拒绝且事务保留
                def stale_check(e=engine, pr=proj):
                    bad = pr / ".story" / "work" / "第009章" / "state.json"
                    bad.parent.mkdir(parents=True, exist_ok=True)
                    bad.write_text(json.dumps({"chapter": 9, "expected_state_revision": 0, "delta": {}},
                                              ensure_ascii=False), encoding="utf-8")
                    code, out = run_state(e, "commit", "--project", str(pr), "--chapter", "9", "--input", str(bad))
                    return code == 1 and "stale transaction" in out and bad.exists()
                results += check_engines_case(f"[state:{engine}] stale revision → commit 拒绝", True, stale_check)

                # 条目5：连续两章 → 推进 + 派生刷新
                def progress_check(e=engine, pr=proj):
                    run_state(e, "draft", "--project", str(pr), "--chapter", "2")
                    txn2 = write_txn(pr, 2, chapter_delta(2, "凯尔"))
                    code, _ = run_state(e, "commit", "--project", str(pr), "--chapter", "2", "--input", str(txn2))
                    st = json.loads((pr / "记忆系统" / "_state.json").read_text(encoding="utf-8"))
                    ledger = (pr / "记忆系统" / "圣经" / "事实台账.md").read_text(encoding="utf-8")
                    return (code == 0 and st["last_committed_chapter"] == 2 and st["state_revision"] == 2
                            and len(st["facts"]) == 2 and "凯尔" in ledger
                            and (pr / ".done" / "第002章_state.done").exists())
                results += check_engines_case(f"[state:{engine}] 连续两章提交 → 推进且派生刷新", True, progress_check)

                # 条目6：派生逐字一致（二次 check 仍 PASS）
                results += check_engines_case(f"[state:{engine}] 二次 check 仍 PASS（逐字一致）", True,
                                              lambda e=engine, pr=proj: run_state(e, "check", "--project", str(pr))[0] == 0)

            # 条目3：commit 后 check-done 自动要求 state 标记（13 项）；缺则1，补齐则0
            proj3 = tmp_path / "p_state_gate"
            proj3.mkdir()
            run_state("py", "init", "--project", str(proj3))
            run_state("py", "draft", "--project", str(proj3), "--chapter", "1")
            txn = write_txn(proj3, 1, chapter_delta(1, "艾露"))
            run_state("py", "commit", "--project", str(proj3), "--chapter", "1", "--input", str(txn))
            empty_done(proj3, 1, BASE)
            write_report(proj3, 1, GOOD_REPORT)

            def gate_missing(e):
                (proj3 / ".done" / "第001章_state.done").unlink(missing_ok=True)  # commit 会自建，先删以测缺失路径
                code, out = run(proj3, 1, engine=e)
                return code == 1 and "缺 1 项必需 .done：state" in out and "状态单：required" in out
            results += check_engines("check-done：缺 state 标记 → exit1（13 项口径）", True, gate_missing)

            def gate_present(e):
                (proj3 / ".done" / "第001章_state.done").write_bytes(b"")
                code, out = run(proj3, 1, engine=e)
                return code == 0 and "必需标记数：13" in out and "状态单：required" in out
            results += check_engines("check-done：state 标记齐 + last_committed≥N → exit0", True, gate_present)

            # 条目4：无 _state.json → 口径不变（12 项），向后兼容
            proj4 = tmp_path / "p_state_legacy"
            proj4.mkdir()
            empty_done(proj4, 1, BASE)
            write_report(proj4, 1, GOOD_REPORT)

            def legacy_ok(e):
                code, out = run(proj4, 1, engine=e)
                return code == 0 and "必需标记数：12" in out and "状态单：skipped" in out
            results += check_engines("check-done：无 _state.json → 维持 12 项（向后兼容）", True, legacy_ok)

            # 跨引擎互通：py 写派生 → ps1 必须能校验（逐字一致）
            if PS_RUNNER:
                def cross_check():
                    run_state("py", "check", "--project", str(tmp_path / "p_state_py"))
                    code, out = run_state("ps1", "check", "--project", str(tmp_path / "p_state_py"))
                    return code == 0 and "逐字一致" in out
                results.append(case("跨引擎互通：py 提交的项目 ps1 check PASS", True, cross_check()))
                def cross_check2():
                    code, out = run_state("py", "check", "--project", str(tmp_path / "p_state_ps1"))
                    return code == 0 and "逐字一致" in out
                results.append(case("跨引擎互通：ps1 提交的项目 py check PASS", True, cross_check2()))
    except Exception as exc:  # noqa: BLE001
        results.append(case("[段:基础/任务书/状态单 fixtures] 段内 setup/断言异常（登记 FAIL，后续段继续）", True, False,
                            f"{type(exc).__name__}: {exc}"))

    # ---------------- AI 文本启发式检测（P1-8）----------------
    try:

        DETECT_PY = ROOT / "scripts" / "detect_ai_text.py"
        DETECT_PS1 = ROOT / "scripts" / "detect_ai_text.ps1"
        _detect_dir = Path(tempfile.mkdtemp(prefix="detect-test-"))
        _ai_txt = _detect_dir / "ai.txt"
        _ai_txt.write_text(
            ("在这个快节奏的时代，值得注意的是，AI 写作正不断发展。\n"
             "他不仅加快了脚步，他不仅握紧了剑，他不仅看向了远方。\n"
             "他不仅加快了脚步，他不仅握紧了剑，他不仅看向了远方。\n"
             "综上所述，AI 写作值得我们深入探讨，全面提升写作效率，不断优化表达方式，有效提升内容质量。\n"
             "众所周知，显而易见，写作能力的提升需要多维度的训练，需要全方位的积累，需要一体化的方案。\n"
             "让我们一起砥砺前行，不负韶华！……（省略号很多）……\n") * 6,
            encoding="utf-8",
        )
        _clean_txt = _detect_dir / "clean.txt"
        _clean_txt.write_text(
            ("夜色压着山脊。\n老陈把烟掐了。\n「走吧。」他说。\n风从谷口灌进来，带着湿土味。\n"
             "他数了数背包里的压缩饼干，还有四块。\n够撑到垭口。\n远处有狼嚎，一声，又停了。\n「不是冲我们。」老陈说。\n"),
            encoding="utf-8",
        )

        def _detect(engine: str, txt: Path):
            if engine == "ps1":
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(DETECT_PS1), "-InputFile", str(txt)]
            else:
                cmd = [PY, str(DETECT_PY), "--input", str(txt)]
            proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            return proc.returncode, proc.stdout

        for e in ENGINES:
            _c, _o = _detect(e, _ai_txt)
            _txt = _o.decode("utf-8", "replace")
            results.append(case(f"[detect:{e}] AI 味样例 exit0 且含总命中行", True,
                                _c == 0 and ("总命中：" in _txt)))
            _c2, _o2 = _detect(e, _clean_txt)
            _txt2 = _o2.decode("utf-8", "replace")
            results.append(case(f"[detect:{e}] 干净样例 总命中=0 且声明不构成朱雀结论", True,
                                _c2 == 0 and "总命中：0" in _txt2 and "不构成朱雀结论" in _txt2))
        if PS_RUNNER:
            _cp, _op = _detect("py", _ai_txt)
            _cs, _os = _detect("ps1", _ai_txt)
            results.append(case("[detect] AI 味样例 py/ps1 输出逐字节一致", True, _cp == _cs and _op == _os))
            _cp2, _op2 = _detect("py", _clean_txt)
            _cs2, _os2 = _detect("ps1", _clean_txt)
            results.append(case("[detect] 干净样例 py/ps1 输出逐字节一致", True, _cp2 == _cs2 and _op2 == _os2))
            _p = subprocess.run([PY, str(DETECT_PY), "--input", str(_ai_txt), "--fail-over", "3"], capture_output=True)
            _s = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(DETECT_PS1), "-InputFile", str(_ai_txt), "-FailOver", "3"], capture_output=True)
            results.append(case("[detect] fail-over 3（命中≥3）→ 双实现 exit1", True, _p.returncode == 1 and _s.returncode == 1))
            _p = subprocess.run([PY, str(DETECT_PY), "--input", str(_ai_txt), "--fail-over", "999"], capture_output=True)
            _s = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(DETECT_PS1), "-InputFile", str(_ai_txt), "-FailOver", "999"], capture_output=True)
            results.append(case("[detect] fail-over 999（不可达）→ 双实现 exit0", True, _p.returncode == 0 and _s.returncode == 0))
        else:
            print("  [SKIP] [detect] 跨引擎对拍 4 项 — 未检测到 PowerShell")
    except Exception as exc:  # noqa: BLE001
        results.append(case("[段:AI文本启发式检测] 段内 setup/断言异常（登记 FAIL，后续段继续）", True, False,
                            f"{type(exc).__name__}: {exc}"))

    # ---------------- 正文指纹/手改保护（P1-9）----------------
    try:

        GUARD_PY = ROOT / "scripts" / "text_guard.py"
        GUARD_PS1 = ROOT / "scripts" / "text_guard.ps1"
        _gdir = Path(tempfile.mkdtemp(prefix="guard-test-"))

        def _mk_guard_proj(name: str) -> Path:
            pj = _gdir / name
            (pj / "正文").mkdir(parents=True)
            (pj / ".story").mkdir()
            (pj / "正文" / "第001章_试炼.txt").write_text("夜色压着山脊。老陈把烟掐了。\n「走吧。」他说。\n", encoding="utf-8")
            (pj / "正文" / "第002章_夜行.txt").write_text("风从谷口灌进来。他数了数饼干。\n", encoding="utf-8")
            return pj

        def _guard(engine: str, command: str, pj: Path, ch: int):
            if engine == "ps1":
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(GUARD_PS1), "-Command", command, "-Project", str(pj), "-Chapter", str(ch)]
            else:
                cmd = [PY, str(GUARD_PY), command, "--project", str(pj), "--chapter", str(ch)]
            proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            err = proc.stderr.decode("utf-8", "replace").strip()
            return proc.returncode, proc.stdout.decode("utf-8", "replace"), (f" | stderr: {err[:220]}" if err else "")

        for e in ENGINES:
            _pj = _mk_guard_proj(f"p_guard_{e}")
            c1, o1, _e1 = _guard(e, "digest", _pj, 1)
            results.append(case(f"[guard:{e}] digest ch1 exit0 且含指纹行", True, c1 == 0 and "【文本指纹】" in o1 and "已记录" in o1, _e1))
            c2, o2, _e2 = _guard(e, "verify", _pj, 2)
            results.append(case(f"[guard:{e}] verify 无记录 → exit2", True, c2 == 2 and "无指纹记录" in o2, _e2))
            _f = _pj / "正文" / "第001章_试炼.txt"
            _orig = _f.read_text(encoding="utf-8")
            _f.write_text(_orig + "（深夜又加了一句。）\n", encoding="utf-8")
            c3, o3, _e3 = _guard(e, "verify", _pj, 1)
            results.append(case(f"[guard:{e}] 手改后 verify → exit1 且给出处置口径", True,
                                c3 == 1 and "与记录不一致" in o3 and "停下询问" in o3, _e3))
            _f.write_text(_orig, encoding="utf-8")
            c4, o4, _e4 = _guard(e, "verify", _pj, 1)
            results.append(case(f"[guard:{e}] 还原后 verify → exit0", True, c4 == 0 and "一致" in o4, _e4))
            if e == "py":
                _o_py = (o1, o2, o3, o4)
            else:
                _o_ps1 = (o1, o2, o3, o4)
        if PS_RUNNER:
            results.append(case("[guard] digest/verify 四场景 py/ps1 解码后输出一致", True, _o_py == _o_ps1))
            # verify-all：手改 ch2 → exit1
            _pjA = _mk_guard_proj("p_guard_all")
            _guard("py", "digest", _pjA, 1); _guard("py", "digest", _pjA, 2)
            (_pjA / "正文" / "第002章_夜行.txt").write_text("风停了。\n", encoding="utf-8")
            cA, oA, _eA = _guard("py", "verify-all", _pjA, 0)
            cB, oB, _eB = _guard("ps1", "verify-all", _pjA, 0)
            results.append(case("[guard] verify-all（ch2 被改）双实现 exit1 且解码后输出一致", True,
                                cA == 1 and cB == 1 and oA == oB and "不一致 1 章" in oA, _eA + _eB))
        else:
            print("  [SKIP] [guard] 跨引擎对拍 2 项 — 未检测到 PowerShell")
    except Exception as exc:  # noqa: BLE001
        results.append(case("[段:正文指纹/手改保护] 段内 setup/断言异常（登记 FAIL，后续段继续）", True, False,
                            f"{type(exc).__name__}: {exc}"))

    # ---------------- 技能发布包（P3-19）----------------
    try:

        PKG_PY = ROOT / "scripts" / "package_skill.py"
        PKG_PS1 = ROOT / "scripts" / "package_skill.ps1"
        _pa = subprocess.run([PY, str(PKG_PY), "--check"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if PS_RUNNER:
            _pb = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(PKG_PS1), "-Check"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            results.append(case("[pkg] --check/-Check 双实现 exit0 且输出逐字节一致", True,
                                _pa.returncode == 0 and _pb.returncode == 0 and _pa.stdout == _pb.stdout))
        else:
            results.append(case("[pkg] --check py 单引擎 exit0", True, _pa.returncode == 0))
        _pkg_dir = Path(tempfile.mkdtemp(prefix="pkg-test-"))
        (_pkg_dir / "modules").mkdir()
        (_pkg_dir / "SKILL.md").write_text("---\nname: demo\ndescription: 测试\n---\n# demo\n", encoding="utf-8")
        _na = subprocess.run([PY, str(PKG_PY), "--check", "--root", str(_pkg_dir)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _ta = (_na.stdout + _na.stderr).decode("utf-8", "replace")
        if PS_RUNNER:
            _nb = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(PKG_PS1), "-Check", "-Root", str(_pkg_dir)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _tb = (_nb.stdout + _nb.stderr).decode("utf-8", "replace")
            results.append(case("[pkg] frontmatter 缺 version → 双实现 exit1 且输出一致", True,
                                _na.returncode == 1 and _nb.returncode == 1 and _ta == _tb and "缺字段" in _ta and "version" in _ta))
        else:
            results.append(case("[pkg] frontmatter 缺 version → py exit1", True,
                                _na.returncode == 1 and "缺字段" in _ta and "version" in _ta))
        (_pkg_dir / "SKILL.md").write_bytes(b"\xef\xbb\xbf" + "---\nname: demo\ndescription: 测试\nversion: 1.0.0\n---\n# demo\n".encode("utf-8"))
        _nab = subprocess.run([PY, str(PKG_PY), "--check", "--root", str(_pkg_dir)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _outb = (_nab.stdout).decode("utf-8", "replace")
        if PS_RUNNER:
            _nbb = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(PKG_PS1), "-Check", "-Root", str(_pkg_dir)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _outb2 = (_nbb.stdout).decode("utf-8", "replace")
            results.append(case("[修复] pkg SKILL.md 带 BOM → 双实现解析放行 exit0 且输出一致（🟠 回归）", True,
                                _nab.returncode == 0 and _nbb.returncode == 0 and _outb == _outb2 and "version=1.0.0" in _outb))
        else:
            results.append(case("[修复] pkg SKILL.md 带 BOM → py 解析放行 exit0（🟠 回归）", True,
                                _nab.returncode == 0 and "version=1.0.0" in _outb))
        import hashlib as _hashlib
        import zipfile as _zipfile
        _h = []
        for _i in range(2):
            _out = _pkg_dir / f"p{_i}.zip"
            subprocess.run([PY, str(PKG_PY), "--out", str(_out), "--root", str(ROOT)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _h.append(_hashlib.sha256(_out.read_bytes()).hexdigest())
        _names = _zipfile.ZipFile(_pkg_dir / "p0.zip").namelist()
        results.append(case("[pkg] zip 确定性（两次 sha 一致）且载荷含 SKILL.md、无 .github/.agents", True,
                            _h[0] == _h[1] and "SKILL.md" in _names and not any(n.startswith((".github", ".agents")) for n in _names)))
    except Exception as exc:  # noqa: BLE001
        results.append(case("[段:技能发布包] 段内 setup/断言异常（登记 FAIL，后续段继续）", True, False,
                            f"{type(exc).__name__}: {exc}"))

    # ---------------- 摘要陈旧检测（OF-6 / P3-24）----------------
    try:

        _st_dir = Path(tempfile.mkdtemp(prefix="stale-test-"))
        _sp = _st_dir / "p_stale"
        _sp.mkdir()
        for _e in STATE_ENGINES:
            run_state(_e, "init", "--project", str(_sp))
            run_state(_e, "draft", "--project", str(_sp), "--chapter", "1")
            write_txn(_sp, 1, chapter_delta(1, "艾露"))
            run_state(_e, "commit", "--project", str(_sp), "--chapter", "1", "--input", str(_sp / ".story" / "work" / "第001章" / "state.json"))
            break  # 引擎无关：任一引擎建一次即可
        (_sp / "正文").mkdir(parents=True, exist_ok=True)
        (_sp / "正文" / "第001章.md").write_text("这是第1段正文，用于交叉校验段号上限。\n", encoding="utf-8")  # 新口径：stale 按已有正文判（O(实际章数)）
        _stf = _sp / "记忆系统" / "_state.json"
        _orig_state = _stf.read_text(encoding="utf-8")
        _obj = json.loads(_orig_state)
        _obj["summaries"].pop("001", None)
        _stf.write_text(json.dumps(_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        _outs = {}
        for _e in STATE_ENGINES:
            _c, _o = run_state(_e, "check", "--project", str(_sp))
            _outs[_e] = (_c, _o)
            results.append(case(f"[stale:{_e}] 缺第001章摘要 → check 仍 exit0 且点名 stale", True,
                                _c == 0 and "摘要陈旧（stale）：第001章" in _o))
        if len(STATE_ENGINES) > 1:
            results.append(case("[stale] 双实现 stale 警告解码后输出一致", True,
                                _outs[STATE_ENGINES[0]][1] == _outs[STATE_ENGINES[1]][1]))
        else:
            print("  [SKIP] [stale] 跨引擎对拍 — 未检测到 PowerShell")
        _stf.write_text(_orig_state, encoding="utf-8")
        _c, _o = run_state(STATE_ENGINES[0], "check", "--project", str(_sp))
        results.append(case("[stale] 摘要补全后 → ✅摘要连续（stale=0）", True, "✅ 摘要连续（stale=0）" in _o))
    except Exception as exc:  # noqa: BLE001
        results.append(case("[段:摘要陈旧检测] 段内 setup/断言异常（登记 FAIL，后续段继续）", True, False,
                            f"{type(exc).__name__}: {exc}"))

    # ---------------- 伏笔时间线可视化（MU-1 / P3-21）----------------
    try:

        FS_PY = ROOT / "scripts" / "foreshadow_timeline.py"
        FS_PS1 = ROOT / "scripts" / "foreshadow_timeline.ps1"
        _fs_dir = Path(tempfile.mkdtemp(prefix="fs-test-"))

        def _fs_run(engine: str, pj: Path):
            if engine == "ps1":
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(FS_PS1), "-Project", str(pj)]
            else:
                cmd = [PY, str(FS_PY), "--project", str(pj)]
            proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            md = (pj / "记忆系统" / "圣经" / "伏笔时间线.md")
            return proc.returncode, proc.stdout.decode("utf-8", "replace"), (md.read_text(encoding="utf-8") if md.exists() else "")

        # 旧清单夹具
        _old = _fs_dir / "old"
        (_old / "记忆系统" / "圣经").mkdir(parents=True)
        (_old / "正文").mkdir()
        (_old / "正文" / "第045章_夜行.txt").write_text("x", encoding="utf-8")
        (_old / "记忆系统" / "圣经" / "伏笔清单.md").write_text(
            "# 伏笔清单\n## 伏笔清单\n| # | 伏笔 | 首现 | 状态 | 预期回收 | 原始文本 | 近况 | 关联 |\n"
            "|---|------|------|------|---------|---|---|---|\n"
            "| 1 | 祖传玉佩 | 第5章 | 🔘 已埋设 | 第20章 | [原文] | 有提及 | 主线 |\n"
            "| 2 | 反派卧底 | 第10章 | ✅ 已回收 | 第40章 | | | 角色A |\n"
            "| 3 | 沉睡预言 | 第12章 | ⏸️ 暂停 | 第90章 | | | 主线 |\n"
            "| 1 | | 第 X 章 | 🔘 已埋设 | 第 Y 章 | | | |\n",
            encoding="utf-8")
        _r = {}
        for _e in ENGINES:
            _c, _o, _md = _fs_run(_e, _old)
            _r[_e] = (_c, _o, _md)
            results.append(case(f"[fs:{_e}] 旧清单源 → exit0 且产物含过期点名/mermaid", True,
                                _c == 0 and "🚨 过期：#1 祖传玉佩" in _o and "```mermaid" in _md and "第 X 章" not in _md))
        if PS_RUNNER:
            results.append(case("[fs] 旧清单源 py/ps1 stdout 与产物 md 解码后文本一致", True,
                                _r["py"] == _r["ps1"]))
        else:
            print("  [SKIP] [fs] 旧清单源跨引擎对拍 — 未检测到 PowerShell")
        # 新架构 state 夹具
        _new = _fs_dir / "new"
        (_new / "记忆系统" / "圣经").mkdir(parents=True)
        (_new / "正文").mkdir()
        (_new / "正文" / "第060章_决战.txt").write_text("x", encoding="utf-8")
        (_new / "记忆系统" / "_state.json").write_text(json.dumps({
            "schema_version": 1, "state_revision": 7, "book_title": "测试书",
            "last_committed_chapter": 60,
            "foreshadow": {"F001": {"desc": "神秘玉佩·半块", "planted_ch": 3, "planned_resolve_ch": 30,
                                    "resolved_ch": None, "priority": "高"},
                           "F002": {"desc": "师父的信", "planted_ch": 8, "planned_resolve_ch": 55,
                                    "resolved_ch": 50, "priority": "中"}},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        _r2 = {}
        for _e in ENGINES:
            _c, _o, _md = _fs_run(_e, _new)
            _r2[_e] = (_c, _o, _md)
            results.append(case(f"[fs:{_e}] state 源 → 数据源=state 且 F001 过期 30 章", True,
                                _c == 0 and "源=state" in _o and "超期 30 章" in _o and "师父的信" in _md))
        if PS_RUNNER:
            results.append(case("[fs] state 源 py/ps1 stdout 与产物 md 解码后文本一致", True,
                                _r2["py"] == _r2["ps1"]))
        else:
            print("  [SKIP] [fs] state 源跨引擎对拍 — 未检测到 PowerShell")
    except Exception as exc:  # noqa: BLE001
        results.append(case("[段:伏笔时间线] 段内 setup/断言异常（登记 FAIL，后续段继续）", True, False,
                            f"{type(exc).__name__}: {exc}"))

    # ---------------- 审计修复回归：双实现同口径 / 规则单源 / 断言盲区补测 ----------------
    try:
        import re as _re

        def _cross(name: str, ok: bool, fn) -> None:
            """跨引擎对拍用例：无 PowerShell 时打印跳过、不计入总数。"""
            if not PS_RUNNER:
                print(f"  [SKIP] {name} — 未检测到 PowerShell")
                return
            results.append(case(name, ok, fn()))

        def _norm_flags(s: str) -> str:
            # -SoftReport → --soft-report 等拼写差异归一后再对拍
            for _py_f, _ps_f in FLAG_MAP.items():
                s = s.replace(_ps_f, _py_f)
            return s

        def _nl(s: str) -> str:
            # 行尾归一（ps1 Write-Output → \r\n，py print → \n；内容对拍不比较传输行尾）+ 路径形态归一（CI temp 8.3 短名/盘符大小写在 py 与 .NET 两侧呈现不同）
            return _pnorm(s.replace("\r\n", "\n"))

        _nhd = Path(tempfile.mkdtemp(prefix="nonhead-"))

        def _mk_nh(name: str, claim_para: str) -> Path:
            p = _nhd / name
            p.mkdir()
            empty_done(p, 1, BASE)
            (p / "正文").mkdir()
            (p / "正文" / "第001章_试炼.txt").write_text(
                "夜色压着山脊。\n\n老陈把烟掐了。\n\n风从谷口灌进来。\n", encoding="utf-8")
            write_report(p, 1,
                         "# 审核报告\n## 人工证据包\n- 人工段注入：第{0}段，合计约320字\n"
                         "- 段落位置：第3段起\n- 结构破坏\n".format(claim_para))
            return p

        # a) 正文首块非标题（历史 bug：py 恒少算 1 段 → 双引擎对同一输入反向裁决）
        p_nh_ok = _mk_nh("p_nh_ok", "3")
        _r_nh = {e: run(p_nh_ok, 1, engine=e) for e in ENGINES}
        results.append(case("[修复] 正文首块非标题：3 段容第3段 → py exit0",
                            True, _r_nh["py"][0] == 0, _r_nh["py"][1][-160:]))
        _cross("[修复] 正文首块非标题：py/ps1 同判 exit0 且输出逐字一致", True,
               lambda: _r_nh["ps1"][0] == 0 and _nl(_r_nh["ps1"][1]) == _nl(_r_nh["py"][1]))
        p_nh_bad = _mk_nh("p_nh_bad", "4")
        _r_nb = {e: run(p_nh_bad, 1, engine=e) for e in ENGINES}
        results.append(case("[修复] 正文首块非标题：第4段超3段 → py exit1 且点名交叉校验",
                            True, _r_nb["py"][0] == 1 and "正文交叉校验" in _r_nb["py"][1]))
        _cross("[修复] 正文首块非标题：超报 py/ps1 同判 exit1 且输出逐字一致", True,
               lambda: (_r_nb["ps1"][0] == 1
                        and _nl(_norm_flags(_r_nb["ps1"][1])) == _nl(_r_nb["py"][1])))

        # b) check-done 双引擎逐字对拍（历史盲区：只各自断言关键词，口径漂移测不出）
        p_x = _nhd / "p_x"
        p_x.mkdir()
        empty_done(p_x, 1, BASE)
        write_report(p_x, 1, GOOD_REPORT)
        _r_x = {e: run(p_x, 1, engine=e) for e in ENGINES}
        _cross("[修复] check-done 默认口径：py/ps1 输出逐字一致（对拍）", True,
               lambda: _r_x["py"][0] == _r_x["ps1"][0] == 0 and _nl(_r_x["py"][1]) == _nl(_r_x["ps1"][1]))
        # 旗标拼写差异归一后对拍（--allow-non-empty vs -AllowNonEmpty）
        (p_x / ".done" / "第001章_wordcount.done").write_text("非空调试", encoding="utf-8")
        _r_f = {e: run(p_x, 1, "--allow-non-empty", engine=e) for e in ENGINES}
        _cross("[修复] check-done --allow-non-empty：归一旗标拼写后输出逐字一致", True,
               lambda: (_r_f["py"][0] == _r_f["ps1"][0] == 0
                        and _nl(_norm_flags(_r_f["ps1"][1])) == _nl(_r_f["py"][1])))

        # c) FLAG_MAP 与双实现旗标 1:1 自检（防手工同步漂移）
        def _flag_map_check() -> bool:
            py_src = SCRIPT.read_text(encoding="utf-8-sig")
            ps1_src = PS1.read_text(encoding="utf-8-sig")
            py_store_true = set(_re.findall(
                r'add_argument\(\s*"(--[a-z0-9-]+)",\s*action="store_true"', py_src))
            ps1_switches = set(_re.findall(r"\[switch\]\$(\w+)", ps1_src))
            return (py_store_true == set(FLAG_MAP)
                    and ps1_switches == {v.lstrip("-") for v in FLAG_MAP.values()})
        results.append(case("[修复] FLAG_MAP ↔ py store_true ↔ ps1 switch 1:1 自检", True, _flag_map_check()))

        # d) 任务书目录优先级（设定/细纲 > 细纲）与回落
        p_pri = _nhd / "ppri"
        p_pri.mkdir()
        empty_done(p_pri, 1, BASE)
        write_report(p_pri, 1, GOOD_REPORT)
        write_sheet(p_pri, 1, must_items=("- [x] 节拍1（必落）",))
        _bad = p_pri / "细纲"
        _bad.mkdir(parents=True, exist_ok=True)
        (_bad / "第001章_细纲.md").write_text("## 章节任务书\n### TASK\n缺段占位\n", encoding="utf-8")
        results += check_engines(
            "[修复] 任务书目录优先级：设定/细纲 合法且优先 → ok exit0", True,
            lambda e, p=p_pri: run(p, 1, "--require-task-sheet", engine=e)[0] == 0)
        (p_pri / "设定" / "细纲" / "第001章_细纲.md").unlink()

        def _pri_fall(e, p=p_pri):
            c, o = run(p, 1, "--require-task-sheet", engine=e)
            return c == 1 and "任务书校验：no-section" in o
        results += check_engines("[修复] 任务书回落：仅剩 细纲/ 缺段 → no-section exit1", True, _pri_fall)

        # e) 任务书路径是目录（非文件）→ 双引擎同判 missing（ps1 历史会抛异常误报 no-section）
        p_dir = _nhd / "pdir"
        p_dir.mkdir()
        empty_done(p_dir, 1, BASE)
        write_report(p_dir, 1, GOOD_REPORT)
        (p_dir / "设定" / "细纲" / "第001章_细纲.md").mkdir(parents=True)

        def _dir_sheet(e, p=p_dir):
            c, o = run(p, 1, "--require-task-sheet", engine=e)
            return c == 1 and "任务书校验：missing" in o
        results += check_engines("[修复] 任务书路径为目录 → 双引擎同判 missing 且同文案", True, _dir_sheet)

        # f) 规则单源双消费：auto_when_state_file=false（复制脚本+规则副本，避免动仓库单源）
        def _copy_engine_dir(tag: str) -> Path:
            d = _nhd / tag
            d.mkdir()
            for f in (SCRIPT, PS1, RULES):
                shutil.copy2(f, d / f.name)
            return d

        def _run_copy(d: Path, proj: Path, engine: str, extra: tuple[str, ...] = ()) -> tuple[int, str]:
            if engine == "ps1":
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(d / "check-done.ps1"),
                       "-Project", str(proj), "-Chapter", "1"] + list(extra)
                proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
            cmd = [PY, str(d / "check-done.py"), "-p", str(proj), "-c", "1"] + list(extra)
            proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            out = (proc.stdout or b"").decode("utf-8", "replace")
            err = (proc.stderr or b"").decode("utf-8", "replace")
            return proc.returncode, out + err

        _cfgd = _copy_engine_dir("cfg_rules")
        _rjp = _cfgd / "check-done-rules.json"
        _rj = json.loads(_rjp.read_text(encoding="utf-8"))
        _rj["state_mark"]["auto_when_state_file"] = False
        _rjp.write_text(json.dumps(_rj, ensure_ascii=False, indent=2), encoding="utf-8")
        p_auto = _nhd / "pauto"
        p_auto.mkdir()
        empty_done(p_auto, 1, BASE)
        write_report(p_auto, 1, GOOD_REPORT)
        (p_auto / "记忆系统").mkdir()
        (p_auto / "记忆系统" / "_state.json").write_text("{}", encoding="utf-8")

        def _auto_check(e):
            c, o = _run_copy(_cfgd, p_auto, e)
            return (c == 0 and "必需标记数：12" in o
                    and "状态单：skipped（auto_when_state_file=false" in o)
        results += check_engines("[修复] auto_when_state_file=false → 双引擎同判 12 项（单源双消费）", True, _auto_check)

        # g) 规则单源双消费：body_globs 改 .md（ps1 历史只认前缀 *.txt）
        _glbd = _copy_engine_dir("glob_rules")
        _rjp = _glbd / "check-done-rules.json"
        _rj = json.loads(_rjp.read_text(encoding="utf-8"))
        _rj["body_globs"] = ["正文/第{nnn}章_*.md"]
        _rjp.write_text(json.dumps(_rj, ensure_ascii=False, indent=2), encoding="utf-8")
        p_glob = _nhd / "pglob"
        p_glob.mkdir()
        empty_done(p_glob, 1, BASE)
        (p_glob / "正文").mkdir()
        (p_glob / "正文" / "第001章_试炼.md").write_text(
            "夜色压着山脊。\n\n老陈把烟掐了。\n\n风从谷口灌进来。\n", encoding="utf-8")
        write_report(p_glob, 1,
                     "# 审核报告\n## 人工证据包\n- 人工段注入：第40段，合计约320字\n"
                     "- 段落位置：第3段起\n- 结构破坏\n")

        def _glob_check(e):
            c, o = _run_copy(_glbd, p_glob, e)
            # 找到 .md 正文（3 段）→ 第40段超界 → body-mismatch；找不到正文会静默 exit0 即为回归
            return c == 1 and "正文交叉校验" in o and "第40段" in o
        results += check_engines("[修复] body_globs 改 .md → 双引擎都按单源定位正文（同判 exit1）", True, _glob_check)

        # h) 预算守卫：负向注入 + branches/stacking/空引用/分层缺失 错误清单双引擎逐条一致
        _bd = Path(tempfile.mkdtemp(prefix="budget-rules-"))
        for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
            shutil.copy2(ROOT / "scripts" / _f, _bd / _f)
        (_bd / "A.md").write_text("一" * 80, encoding="utf-8")
        (_bd / "B.md").write_text("二" * 10, encoding="utf-8")
        (_bd / "doc-budget.json").write_text(json.dumps({
            "files": [
                {"path": "A.md", "budget": 5, "why": "超预算注入", "layer": "protected"},
                {"path": "B.md", "budget": 1000, "why": "正常", "layer": "compressible"},
                {"path": "C.md", "budget": 1000, "why": "分层缺失注入", "layer": ""},
                {"path": "MISSING.md", "budget": 10, "why": "缺文件注入", "layer": "protected"}
            ],
            "paths": [{"label": "路径X", "files": ["A.md", "", "B.md", "NOPE2.md"],
                       "branches": [["A.md", "NOPE.md"], ["B.md"]],
                       "stacking": [["A.md", "NOPE3.md"]]}],
            "path_ceiling": {"limit": 200, "exempt": [], "why": "测试上限"},
            "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        def _bd_run(engine: str) -> tuple[int, str]:
            if engine == "ps1":
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_bd / "check-doc-budget.ps1"), "-Root", str(_bd)]
                proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
            cmd = [PY, str(_bd / "check-doc-budget.py"), "--root", str(_bd)]
            proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            out = (proc.stdout or b"").decode("utf-8", "replace")
            err = (proc.stderr or b"").decode("utf-8", "replace")
            return proc.returncode, out + err

        _r_b = {e: _bd_run(e) for e in ENGINES}
        results.append(case("[修复] 预算负向注入：py exit1 且点名超预算/缺文件/超上限", True,
                            _r_b["py"][0] == 1 and "超预算：A.md" in _r_b["py"][1]
                            and "（超预算注入）" in _r_b["py"][1] and "路径超上限：路径X" in _r_b["py"][1]))
        _cross("[修复] 预算负向注入：错误清单顺序与 why py/ps1 逐条一致", True,
               lambda: (_r_b["ps1"][0] == 1 and _nl(_r_b["ps1"][1]) == _nl(_r_b["py"][1])))
    except Exception as exc:  # noqa: BLE001
        results.append(case("[段:审计修复回归补测] 段内 setup/断言异常（登记 FAIL，后续段继续）", True, False,
                            f"{type(exc).__name__}: {exc}"))

    # i-2) 余量<5% 硬闸（十五轮③：规则入码）——A.md 80 字符/预算 84（余量 4.8%）→ 双引擎 rc1 且文案逐字一致
    _m5d = Path(tempfile.mkdtemp(prefix="budget-margin5-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _m5d / _f)
    (_m5d / "A.md").write_text("一" * 80, encoding="utf-8")
    (_m5d / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 84, "why": "余量闸注入", "layer": "protected"}],
        "paths": [{"label": "路径M", "files": "A.md"}],
        "generated": "G.md",
        "path_ceiling": {"limit": 100000, "why": "t"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    def _m5_run(engine: str) -> tuple[int, str]:
        if engine == "ps1":
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_m5d / "check-doc-budget.ps1"), "-Root", str(_m5d)]
        else:
            cmd = [PY, str(_m5d / "check-doc-budget.py"), "--root", str(_m5d)]
        proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
    _r_m = {e: _m5_run(e) for e in ENGINES}
    results.append(case("[十五轮] 余量<5% 硬闸 → py rc1 点名「余量<5%」", True,
                        _r_m["py"][0] == 1 and "余量<5%" in _r_m["py"][1]))
    _cross("[十五轮] 余量<5% 硬闸 → ps1 与 py 输出逐字一致且 rc 同", True,
           lambda: (_r_m["ps1"][0] == 1 and _nl(_r_m["ps1"][1]) == _nl(_r_m["py"][1])))

    # i) 预算字符串简写形态（files/branches/stacking 单串）——历史 bug：py list(str) 拆成单字符、与 ps1 反向裁决
    _shd = Path(tempfile.mkdtemp(prefix="budget-shorthand-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _shd / _f)
    (_shd / "A.md").write_text("一" * 80, encoding="utf-8")
    (_shd / "B.md").write_text("二" * 10, encoding="utf-8")
    (_shd / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "简写形态不超预算", "layer": "protected"}],
        "paths": [{"label": "路径S", "files": "A.md", "branches": "B.md", "stacking": "B.md"}],
        "generated": "G.md",
        "path_ceiling": {"limit": 100000, "exempt": "路径S", "why": "t"},
        "baseline": {"worst_path_chars": 0, "writer_delegated_chars": 0,
                     "session_resident_chars": 0, "precheck_path_chars": 0,
                     "max_single_file": "A.md=80", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _bud_run(d: Path, engine: str) -> tuple[int, str]:
        if engine == "ps1":
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(d / "check-doc-budget.ps1"), "-Root", str(d)]
            proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
        cmd = [PY, str(d / "check-doc-budget.py"), "--root", str(d)]
        proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")

    _r_s = {e: _bud_run(_shd, e) for e in ENGINES}
    # exempt/generated/stacking 均为单串：py 若回归 set(str)/list(str) 拆字符 → ⚪ 变 ✅、⚙ 拆成 4 行 → 断言必失
    results.append(case("[修复] 预算字符串简写（files/branches/stacking/exempt/generated 单串）→ 不拆字符", True,
                        _r_s["py"][0] == 0 and "路径S 100/100,000" in _r_s["py"][1]
                        and "⚪ exempt 路径S" in _r_s["py"][1]
                        and _r_s["py"][1].count("⚙ generated（脚本产出，不手测）：G.md") == 1,
                        _r_s["py"][1][-200:]))
    _cross("[修复] 预算字符串简写：py/ps1 输出逐条一致（防 list(str)/set(str) 回归）", True,
           lambda: _r_s["ps1"][0] == 0 and _nl(_r_s["ps1"][1]) == _nl(_r_s["py"][1])
           and "⚪ exempt 路径S" in _r_s["ps1"][1]
           and _r_s["ps1"][1].count("⚙ generated（脚本产出，不手测）：G.md") == 1)

    # j) 行切分集合与 py splitlines 同口径：CR/VT/FF/RS/NEL/U+2028 切行（三段放行 exit0）；
    #    U+001F 是 splitlines 不切的边界（ps1 曾写 [-] 超集 → 反向裁决，须 exit1 且双引擎同判）
    for _sep_name, _sep, _want in (("CR", "\r", 0), ("U+2028", "\u2028", 0), ("VT", "\v", 0),
                                   ("FF", "\f", 0), ("RS", "\x1e", 0), ("NEL", "\x85", 0),
                                   ("U+001F", "\x1f", 1)):
        p_sep = _nhd / f"p_sep_{_sep_name}"
        p_sep.mkdir()
        empty_done(p_sep, 1, BASE)
        (p_sep / "正文").mkdir()
        (p_sep / "正文" / "第001章_试炼.txt").write_bytes(
            (_sep.join(["夜色压着山脊。", "老陈把烟掐了。", "风从谷口灌进来。"]) + _sep).encode("utf-8"))
        write_report(p_sep, 1,
                     "# 审核报告\n## 人工证据包\n- 人工段注入：第3段，合计约320字\n"
                     "- 段落位置：第3段起\n- 结构破坏\n")
        _r_sep = {e: run(p_sep, 1, engine=e) for e in ENGINES}
        results.append(case(f"[修复] {_sep_name} 分隔正文 → py 预期 exit{_want}", True, _r_sep["py"][0] == _want))
        _cross(f"[修复] {_sep_name} 分隔正文：py/ps1 同判 exit{_want} 且输出一致", True,
               lambda r=_r_sep, w=_want: r["ps1"][0] == w
               and _nl(_norm_flags(r["ps1"][1])) == _nl(_norm_flags(r["py"][1])))

    # k) 报告路径为目录 → 双引擎同判 missing（ps1 历史 fail-open exit0）
    p_rd = _nhd / "p_rd"
    p_rd.mkdir()
    empty_done(p_rd, 1, BASE)
    (p_rd / "报告" / "第001章_全量审核报告.md").mkdir(parents=True)
    results += check_engines(
        "[修复] 报告路径为目录 → 双引擎同判 missing exit1（ps1 不再放行）", True,
        lambda e, p=p_rd: (lambda r: r[0] == 1 and "报告校验：missing" in r[1])(run(p, 1, engine=e)))

    # l) 预算条目为目录 → 双引擎同判「缺文件」exit1（CI 的 -LintBudget 只跑 ps1，曾量成 0 字符 ✅）
    _bdd = Path(tempfile.mkdtemp(prefix="budget-direntry-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _bdd / _f)
    (_bdd / "adir.md").mkdir()
    (_bdd / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "adir.md", "budget": 10, "why": "目录注入", "layer": "protected"}],
        "paths": [],
        "path_ceiling": {"limit": 100, "exempt": [], "why": "t"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _r_bd = {e: _bud_run(_bdd, e) for e in ENGINES}
    results += check_engines(
        "[修复] 预算条目为目录 → 双引擎同判缺文件 exit1", True,
        lambda e: (lambda r: r[0] == 1 and "缺文件 adir.md" in r[1])(_r_bd[e]))

    # m) 空正文口径钉死：无可校验正文时跳过段号交叉（双引擎一致放行；收紧须双实现同步改）
    p_eb = _nhd / "p_eb"
    p_eb.mkdir()
    empty_done(p_eb, 1, BASE)
    (p_eb / "正文").mkdir()
    (p_eb / "正文" / "第001章_试炼.txt").write_text("", encoding="utf-8")
    write_report(p_eb, 1,
                 "# 审核报告\n## 人工证据包\n- 人工段注入：第999段，合计约320字\n"
                 "- 段落位置：第3段起\n- 结构破坏\n")
    _r_eb = {e: run(p_eb, 1, engine=e) for e in ENGINES}
    _cross("[钉死口径] 空正文+超界段号 → 双引擎一致放行（当前宽松口径，收紧须双实现同步）", True,
           lambda: _r_eb["py"][0] == _r_eb["ps1"][0] == 0 and _nl(_r_eb["py"][1]) == _nl(_r_eb["ps1"][1]))

    # q) 路径#小节 切片：小节边界前为 \f 时 py section_slice(splitlines) 切、ps1 曾用 `r?`n 不切 →
    #    小节一直吃到文件尾、计数虚高反向裁决（该功能历史零用例 → 掩盖了行切分第三处漏改）
    _fsec = Path(tempfile.mkdtemp(prefix="budget-fragment-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _fsec / _f)
    (_fsec / "片段.md").write_bytes(("# 标题\n甲乙丙丁" + "\f" + "# 下一节\n" + ("长" * 60)).encode("utf-8"))
    (_fsec / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "片段.md#标题", "budget": 10, "why": "小节切片", "layer": "protected"}],
        "paths": [],
        "path_ceiling": {"limit": 100000, "exempt": [], "why": "t"},
        "baseline": {"worst_path_chars": 0, "writer_delegated_chars": 0,
                     "session_resident_chars": 0, "precheck_path_chars": 0,
                     "max_single_file": "片段.md=7", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _r_fsec = {e: _bud_run(_fsec, e) for e in ENGINES}
    results.append(case("[修复] 文件#小节 带 \f 边界 → 双引擎同算小节 7/10 exit0（ps1 不再吃全文虚高超预算）", True,
                        _r_fsec["py"][0] == 0 and "片段.md#标题 7/10" in _r_fsec["py"][1]
                        and _r_fsec["ps1"][0] == 0 and "片段.md#标题 7/10" in _r_fsec["ps1"][1]))
    _cross("[修复] 路径#小节 切片：py/ps1 输出逐条一致", True,
           lambda: _nl(_r_fsec["ps1"][1]) == _nl(_r_fsec["py"][1]))

    # r) 预算规则单源 schema/顶层形态校验：畸形输入双引擎同文案 fail-closed（py 曾对 {} 打 ✅ exit0）
    for _tag, _rules, _want_msg in (
        ("空规则对象", {}, "❌ 规则单源校验失败"),
        ("paths 标量", {"files": [], "paths": "A.md",
                        "path_ceiling": {"limit": 1, "exempt": [], "why": "t"}}, "❌ 规则单源校验失败"),
        ("非对象顶层", [1, 2, 3], "❌ 规则单源解析失败"),
    ):
        _bad = Path(tempfile.mkdtemp(prefix="budget-bad-"))
        for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
            shutil.copy2(ROOT / "scripts" / _f, _bad / _f)
        (_bad / "doc-budget.json").write_text(json.dumps(_rules), encoding="utf-8")
        _r_bad = {e: _bud_run(_bad, e) for e in ENGINES}
        results.append(case(f"[修复] 预算规则 {_tag} → 双引擎同判 fail-closed exit1", True,
                            _r_bad["py"][0] == 1 and _want_msg in _r_bad["py"][1]
                            and _r_bad["ps1"][0] == 1 and _want_msg in _r_bad["ps1"][1]))
        _cross(f"[修复] 预算规则 {_tag}：解析/校验文案 py/ps1 逐字一致", True,
               lambda r=_r_bad: _nl(r["ps1"][1]) == _nl(r["py"][1]))

    # s) 残留锁文案对拍（py {int(age)} 无千分位 vs ps1 曾 {N0} → ≥1000s 时 2,001 vs 2000 文案反向）
    #    age 按实时算 → 两引擎启动间可能跨秒，故对年龄做占位归一后对拍全输出
    import re as _re
    import time as _time
    p_lk = _nhd / "p_lk"
    p_lk.mkdir()
    _r_init_lk = run_state("py", "init", "--project", str(p_lk))
    _lock_path = p_lk / "记忆系统" / ".state-commit.lock"
    _lock_path.parent.mkdir(parents=True, exist_ok=True)
    _lock_path.write_text("stale", encoding="utf-8")
    _ago = _time.time() - 2000
    os.utime(_lock_path, (_ago, _ago))
    _r_lk = {e: run_state(e, "check", "--project", str(p_lk)) for e in STATE_ENGINES}
    _nrm_lk = lambda s: _re.sub(r"残留锁 \d+s", "残留锁 <AGE>s", _nl(s))
    results.append(case("[修复] 残留锁 ≥1000s → py 文案无千分位（int 直出）且 exit0", True,
                        _r_init_lk[0] == 0 and _r_lk["py"][0] == 0
                        and _re.search(r"残留锁 \d+s", _r_lk["py"][1]) is not None
                        and "残留锁" in _r_lk["py"][1]))
    if "ps1" in STATE_ENGINES:
        _cross("[修复] 残留锁文案：py/ps1 归一年龄后全输出一致（ps1 不再 {N0} 千分位）", True,
               lambda: _r_lk["ps1"][0] == 0 and _re.search(r"残留锁 \d+s", _r_lk["ps1"][1]) is not None
               and _nrm_lk(_r_lk["ps1"][1]) == _nrm_lk(_r_lk["py"][1]))

    # t) 规则单源坏形态矩阵（check-done 侧：缺键/类型错 → 双引擎同文案 fail-closed；BOM → 双引擎同解析放行）
    rules0 = json.loads((ROOT / "scripts" / "check-done-rules.json").read_text(encoding="utf-8-sig"))

    def _done_fixture(rules_obj, bom=False):
        d = Path(tempfile.mkdtemp(prefix="done-rules-"))
        for f in ("check-done.py", "check-done.ps1"):
            shutil.copy2(ROOT / "scripts" / f, d / f)
        if isinstance(rules_obj, str):  # 原始文本（空文件/顶层数组等解析路径用例）
            raw_b = rules_obj.encode("utf-8")
        else:
            raw_b = json.dumps(rules_obj, ensure_ascii=False).encode("utf-8")
        (d / "check-done-rules.json").write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw_b)
        proj = d / "proj"
        proj.mkdir()
        empty_done(proj, 1, BASE)
        write_report(proj, 1, GOOD_REPORT)
        write_body(proj, 1, 10)
        return d, proj

    def _done_run(d, proj, engine):
        if engine == "py":
            cmd = [PY, str(d / "check-done.py"), "--project", str(proj), "--chapter", "1"]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(d / "check-done.ps1"), "-Project", str(proj), "-Chapter", "1"]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        # 主行对拍只比 stdout（解析详情 stderr 属引擎各自文案，与预算侧口径一致）
        return pr.returncode, _decode(pr.stdout)

    _bad_rules = []
    _r = dict(rules0); _r.pop("section_title")
    _bad_rules.append(("缺 section_title", _r, False, 1, "规则单源校验失败"))
    _r = dict(rules0); _r.pop("base_marks")
    _bad_rules.append(("缺 base_marks", _r, False, 1, "规则单源校验失败"))
    _r = dict(rules0); _r["base_marks"] = "wordcount"
    _bad_rules.append(("base_marks 字符串简写", _r, False, 1, "base_marks 须为数组"))
    _r = dict(rules0); _r.pop("task_sheet")
    _bad_rules.append(("缺 task_sheet", _r, False, 1, "规则单源校验失败"))
    _r = dict(rules0); _r["evidence_rules"] = [{"name": "x"}]
    _bad_rules.append(("evidence_rules 元素缺键", _r, False, 1, "evidence_rules[i] 须为含 name/pattern/strong 的对象"))
    _bad_rules.append(("合法规则带 BOM → 解析放行", dict(rules0), True, 0, "必需标记数"))
    for _tag, _robj, _bom, _want, _needle in _bad_rules:
        _fd, _pd = _done_fixture(_robj, _bom)
        _rr = {e: _done_run(_fd, _pd, e) for e in ENGINES}
        results.append(case(f"[修复] 规则单源 {_tag} → 双引擎同文案 exit{_want}", True,
                            len({(v[0], _nl(v[1])) for v in _rr.values()}) == 1
                            and _rr["py"][0] == _want and _needle in _rr["py"][1]))
        _cross(f"[修复] 规则单源 {_tag}：py/ps1 输出逐字一致", True,
               lambda r=_rr: _nl(r["ps1"][1]) == _nl(r["py"][1]))

    # t2) schema 类型/正则分支全覆盖（第五轮补：整数小数、命名组方言、evidence 编译、解析路径等 12 形态）
    def _mk(**kw):
        _r = dict(rules0)
        _r.update(kw)
        return _r
    _bad_rules2 = []
    _bad_rules2.append(("min_hits 小数", _mk(min_hits=1.5), 1, "min_hits 须为整数"))
    _bad_rules2.append(("claim 命名组 (?<g>", _mk(claim_para_pattern="(?<g>第)\\d+段"), 1, "跨引擎安全语法"))
    _bad_rules2.append(("claim 命名组 (?P<g>", _mk(claim_para_pattern="(?P<g>第)\\d+段"), 1, "跨引擎安全语法"))
    _bad_rules2.append(("evidence 非法 pattern", _mk(evidence_rules=[{"name": "坏", "pattern": "([第", "strong": True}]), 1, "evidence_rules[i].pattern 正则无效"))
    _bad_rules2.append(("evidence pattern 命名组", _mk(evidence_rules=[{"name": "x", "pattern": "(?P<g>第)\\d+", "strong": True}]), 1, "跨引擎安全语法"))
    _bad_rules2.append(("body_globs 标量", _mk(body_globs=123), 1, "body_globs 须为数组"))
    _bad_rules2.append(("section_title 标量", _mk(section_title=123), 1, "section_title 须为字符串"))
    _r = dict(rules0); _r.pop("require_strong")
    _bad_rules2.append(("缺 require_strong", _r, 1, "缺 require_strong"))
    _r = dict(rules0); _r["task_sheet"] = dict(_r["task_sheet"]); _r["task_sheet"].pop("suffix")
    _bad_rules2.append(("task_sheet 缺 suffix", _r, 1, "task_sheet 缺 suffix"))
    _r = dict(rules0); _r["task_sheet"] = dict(_r["task_sheet"]); _r["task_sheet"]["dirs"] = [123]
    _bad_rules2.append(("task_sheet.dirs 元素数字", _r, 1, "task_sheet.dirs 元素须为字符串"))
    _bad_rules2.append(("空规则文件（解析路径）", "", 1, "规则单源解析失败"))
    _bad_rules2.append(("顶层数组（解析路径）", "[1,2,3]", 1, "须为 JSON 对象"))
    for _tag, _robj, _want, _needle in _bad_rules2:
        _fd, _pd = _done_fixture(_robj)
        _rr = {e: _done_run(_fd, _pd, e) for e in ENGINES}
        results.append(case(f"[修复] 规则单源 {_tag} → 双引擎同文案 exit{_want}", True,
                            len({(v[0], _nl(v[1])) for v in _rr.values()}) == 1
                            and _rr["py"][0] == _want and _needle in _rr["py"][1]))
        _cross(f"[修复] 规则单源 {_tag}：py/ps1 主行逐字一致", True,
               lambda r=_rr: _nl(r["ps1"][1]) == _nl(r["py"][1]))

    GOOD_BUD = json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "t", "layer": "protected"}],
        "paths": [],
        "path_ceiling": {"limit": 100000, "exempt": [], "why": "t"},
        "baseline": {"worst_path_chars": 0, "writer_delegated_chars": 0,
                     "session_resident_chars": 0, "precheck_path_chars": 0,
                     "max_single_file": "A.md=50", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"},
    }, ensure_ascii=False)

    # u) 预算坏形态矩阵补测（可选键类型/空组/空文件/BOM/root=相对）
    _bforms = [
        ("空文件", "", 1, "规则单源解析失败"),
        ("仅空白", "\n  \n", 1, "规则单源解析失败"),
        ("generated 标量数字", GOOD_BUD.replace('"paths": []', '"paths": [], "generated": 123'), 1, "generated 须为字符串或字符串数组"),
        ("exempt 标量数字", GOOD_BUD.replace('"exempt": []', '"exempt": 123'), 1, "exempt 须为字符串或字符串数组"),
        ("stacking 空组", GOOD_BUD.replace('"paths": []', '"paths": [{"label":"L","files":"A.md","stacking":[[]]}]'), 1, "stacking 组须为字符串或非空字符串数组"),
        ("files 元素为数字", GOOD_BUD.replace('"path": "A.md"', '"path": 123'), 1, "files[0].path 须为字符串"),
        ("paths.files 元素为数字", GOOD_BUD.replace('"paths": []', '"paths": [{"label":"L","files":[123]}]'), 1, "paths[0].files 须为字符串或字符串数组"),
        ("budget 小数", GOOD_BUD.replace('"budget": 100000', '"budget": 10.9'), 1, "files[0].budget 须为整数"),
        ("budget 字符串", GOOD_BUD.replace('"budget": 100000', '"budget": "3250"'), 1, "files[0].budget 须为整数"),
    ]

    def _bud_stdout(d, engine):
        # 主行对拍只比 stdout（解析详情 stderr 属引擎各自文案，设计内不比）
        if engine == "py":
            cmd = [PY, str(d / "check-doc-budget.py"), "--root", str(d)]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(d / "check-doc-budget.ps1"), "-Root", str(d)]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    def _bud_fixture(txt_rules):
        d = Path(tempfile.mkdtemp(prefix="bud-bad2-"))
        for f in ("check-doc-budget.py", "check-doc-budget.ps1"):
            shutil.copy2(ROOT / "scripts" / f, d / f)
        (d / "A.md").write_text("一" * 50, encoding="utf-8")
        (d / "doc-budget.json").write_text(txt_rules, encoding="utf-8")
        return d

    for _tag, _txt, _want, _needle in _bforms:
        _bdir = _bud_fixture(_txt)
        _rr2 = {e: _bud_stdout(_bdir, e) for e in ENGINES}
        results.append(case(f"[修复] 预算规则 {_tag} → 双引擎同文案 exit{_want}", True,
                            len({(v[0], _nl(v[1])) for v in _rr2.values()}) == 1
                            and _rr2["py"][0] == _want and _needle in _rr2["py"][1]))
        _cross(f"[修复] 预算规则 {_tag}：py/ps1 输出逐字一致", True,
               lambda r=_rr2: _nl(r["ps1"][1]) == _nl(r["py"][1]))
    _bdir = Path(tempfile.mkdtemp(prefix="bud-bom-"))
    for f in ("check-doc-budget.py", "check-doc-budget.ps1"):
        shutil.copy2(ROOT / "scripts" / f, _bdir / f)
    (_bdir / "A.md").write_text("一" * 50, encoding="utf-8")
    (_bdir / "doc-budget.json").write_bytes(b"\xef\xbb\xbf" + GOOD_BUD.encode("utf-8"))
    _rr3 = {e: _bud_stdout(_bdir, e) for e in ENGINES}
    results.append(case("[修复] 预算规则带 BOM → 双引擎解析放行 exit0", True,
                        len({(v[0], _nl(v[1])) for v in _rr3.values()}) == 1 and _rr3["py"][0] == 0
                        and "全部在预算内" in _rr3["py"][1]))
    _cross("[修复] 预算规则带 BOM：py/ps1 输出逐条一致", True,
           lambda: _nl(_rr3["ps1"][1]) == _nl(_rr3["py"][1]))
    _bdir2 = _bud_fixture(GOOD_BUD)

    def _bud_dot(engine):
        if engine == "py":
            cmd = [PY, str(_bdir2 / "check-doc-budget.py"), "--root", "."]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_bdir2 / "check-doc-budget.ps1"), "-Root", "."]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(_bdir2))
        return pr.returncode, _decode(pr.stdout)  # 口径统一：仅 stdout（5.1 stderr 含 CategoryInfo 多行会假红）

    _rr4 = {e: _bud_dot(e) for e in ENGINES}
    _dot_root = str(_bdir2.resolve())
    results.append(case("[修复] 预算 -Root . → 首行回显归一为绝对路径（py/ps1 同）", True,
                        len({(v[0], _nl(v[1])) for v in _rr4.values()}) == 1 and _rr4["py"][0] == 0
                        and _dot_root.lower() in _rr4["ps1"][1].splitlines()[0].lower()
                        and _dot_root.lower() in _rr4["py"][1].splitlines()[0].lower()))

    # v) PathType 补测：.done 为普通文件 / _state.json 为目录 → 双引擎同判
    p_df = _nhd / "p_donefile"
    p_df.mkdir()
    empty_done(p_df, 1, BASE)
    write_report(p_df, 1, GOOD_REPORT)
    write_body(p_df, 1, 10)
    shutil.rmtree(p_df / ".done")
    (p_df / ".done").write_bytes(b"")
    results += check_engines(
        "[修复] .done 为普通文件 → 双引擎同判 exit1", True,
        lambda e, p=p_df: (lambda r: r[0] == 1 and "缺少 .done 目录" in r[1])(run(p, 1, engine=e)))
    p_sd = _nhd / "p_statedir"
    p_sd.mkdir()
    empty_done(p_sd, 1, BASE)
    write_report(p_sd, 1, GOOD_REPORT)
    write_body(p_sd, 1, 10)
    (p_sd / "记忆系统" / "_state.json").mkdir(parents=True)
    results += check_engines(
        "[修复] _state.json 为目录 → 双引擎同判 skipped exit0（-PathType Leaf）", True,
        lambda e, p=p_sd: (lambda r: r[0] == 0 and "状态单：skipped" in r[1])(run(p, 1, engine=e)))

    # w) migrate 归档白名单：角色数据库保留原位（🔴 回归钉死；py/ps1 双实现）
    def _mig_setup(name):
        proj = _nhd / name
        (proj / "记忆系统" / "圣经").mkdir(parents=True)
        (proj / "记忆系统" / "摘要").mkdir(parents=True)
        (proj / "记忆系统" / "圣经" / "故事圣经.md").write_text("# 旧故事圣经\n旧数据", encoding="utf-8")
        (proj / "记忆系统" / "圣经" / "角色数据库.md").write_text("# 角色库\n重要角色数据", encoding="utf-8")
        (proj / "记忆系统" / "圣经" / "作者真相时间线.md").write_text("# 真相\n1. 老陈是黑手", encoding="utf-8")
        (proj / "记忆系统" / "圣经" / "伏笔时间线.md").write_text("# 伏笔时间线\n", encoding="utf-8")
        (proj / "记忆系统" / "摘要" / "第001章摘要.md").write_text("# 摘要\n旧摘要", encoding="utf-8")
        (proj / "记忆系统" / "摘要" / "模板.md").write_text("# 摘要模板", encoding="utf-8")
        return proj

    def _mig_run(engine, proj, apply):
        if engine == "py":
            cmd = [PY, str(STATE_PY), "migrate", "--project", str(proj)] + (["--apply"] if apply else [])
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(STATE_PS1), "-Command", "migrate", "-Project", str(proj)] + (["-Apply"] if apply else [])
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout) + _decode(pr.stderr)

    def _mig_ok(engine):
        proj = _mig_setup(f"p_mig_{engine}")
        rc1, out1 = _mig_run(engine, proj, False)
        rc2, _out2 = _mig_run(engine, proj, True)
        bible = proj / "记忆系统" / "圣经"
        return (rc1 == 0 and rc2 == 0
                and (bible / "角色数据库.md").is_file()
                and (bible / "故事圣经.md").is_file()
                and (proj / "记忆系统" / "_旧存档" / "圣经" / "作者真相时间线.md").is_file()
                and "保留原位" in out1
                and (proj / "记忆系统" / "摘要" / "模板.md").is_file()
                and (proj / "记忆系统" / "_旧存档" / "摘要" / "第001章摘要.md").is_file())
    for _e_mig in ENGINES:  # 带子条件 detail：CI 上 ps1 曾单独红，须点名是哪一条（rc/文案/文件）
        try:
            _mig_cond = bool(_mig_ok(_e_mig))
            _mig_detail = ""
        except Exception as _exc:  # noqa: BLE001
            _mig_cond = False
            _mig_detail = f"{type(_exc).__name__}: {_exc}"
        if not _mig_cond and not _mig_detail:
            _mp_mig = _nhd / f"p_mig_{_e_mig}"
            _b_mig = _mp_mig / "记忆系统" / "圣经"
            _rc1_m, _o1_m = _mig_run(_e_mig, _mp_mig, False)
            _mig_detail = (f"dry_rc={_rc1_m} 保留原位={'保留原位' in _o1_m} "
                           f"角色库={(_b_mig / '角色数据库.md').is_file()} 圣经={(_b_mig / '故事圣经.md').is_file()} "
                           f"归档={(_mp_mig / '记忆系统' / '_旧存档' / '圣经' / '作者真相时间线.md').is_file()} "
                           f"| out头3行={_o1_m.splitlines()[:3]} | err尾={_o1_m.splitlines()[-2:]}")
        results.append(case(f"[🔴回归] migrate 归档白名单 → 角色数据库保留原位、派生件归档后重渲染 [{_e_mig}]", True, _mig_cond, _mig_detail))

    # x) ch0 大纲种子提交：draft/commit --chapter 0 → 渲染真相时间线、不产生 .done 标记
    def _seed_ok(engine):
        proj = _nhd / f"p_seed_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        rc, _o = run_state(engine, "draft", "--project", str(proj), "--chapter", "0")
        txn = proj / ".story" / "work" / "第000章" / "state.json"
        if rc != 0 or not txn.is_file():
            return False
        t = json.loads(txn.read_text(encoding="utf-8"))
        t["delta"]["timeline"]["authors_truth"] = [{"item": "老陈=黑手", "reveal_ch": 10}]
        txn.write_text(json.dumps(t, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rc2, out2 = run_state(engine, "commit", "--project", str(proj), "--chapter", "0", "--input", str(txn))
        view = proj / "记忆系统" / "圣经" / "作者真相时间线.md"
        return (rc2 == 0 and "章节 0 种子提交不产生 .done 标记" in out2
                and view.is_file() and "老陈" in view.read_text(encoding="utf-8")
                and not (proj / ".done" / "第000章_state.done").exists()
                and run_state(engine, "check", "--project", str(proj))[0] == 0)
    results += check_engines("[🔴回归] ch0 大纲种子提交 → 渲染真相时间线、无 .done 标记、check 通过", True, _seed_ok)

    # y) fingerprint/detect 双实现补测（分隔符与 ws 集口径；此前零/单夹具覆盖）
    def _fp_case(tag, content, want_para):
        f = _nhd / f"fp_{tag}.txt"
        f.write_bytes(content)
        outs = {}
        for e in ENGINES:
            if e == "py":
                cmd = [PY, str(ROOT / "scripts" / "fingerprint.py"), str(f)]
            else:
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "fingerprint.ps1"), "-Path", str(f), "-Json"]
            pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
            outs[e] = (pr.returncode, (pr.stdout or b"").decode("utf-8", "replace"))
        try:
            vals = [json.loads(o[1]) for o in outs.values()]
            norm = [{k: (float(v) if isinstance(v, (int, float)) else v) for k, v in d.items()} for d in vals]
            return (all(n == norm[0] for n in norm[1:])
                    and len({o[0] for o in outs.values()}) == 1
                    and int(float(norm[0]["段落数"])) == want_para)
        except Exception:
            return False

    _fp_parts = ["第一段落，测试句子一。", "第二段落更长一些，测试句子二！结尾", "第三段，句子三？"]
    for _n, _sep, _want in (("LF", "\n", 3), ("CR", "\r", 3), ("FF", "\f", 3),
                            ("U+2028", "\u2028", 3), ("U+001F", "\x1f", 1)):
        _blob = _sep.join(_fp_parts).encode("utf-8")
        results.append(case(f"[修复] fingerprint {_n} 分隔 → py/ps1 指标值全等（段落数={_want}）", True,
                            _fp_case(f"sp_{_n}", _blob, _want)))
    # 非 BMP（代理对）：py 改 UTF-16 单元口径后与 ps1 .Length 全等（此前码点口径 8 键错位）
    results.append(case("[修复] fingerprint 非 BMP 字符 → py/ps1 指标值全等（UTF-16 单元口径）", True,
                        _fp_case("sp_nonbmp",
                                 "夜色压着山脊。\U00020000\U00020000\U00020000\U00020000\U00020000老陈把烟掐了。\n第二段，普通文字。".encode("utf-8"),
                                 2)))

    def _det_case(tag, content):
        f = _nhd / f"det_{tag}.txt"
        f.write_bytes(content)
        outs = {}
        for e in ENGINES:
            if e == "py":
                cmd = [PY, str(ROOT / "scripts" / "detect_ai_text.py"), "--input", str(f)]
            else:
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "detect_ai_text.ps1"), "-InputFile", str(f)]
            pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
            outs[e] = (pr.returncode, (pr.stdout or b"").decode("utf-8", "replace"))
        return len({(o[0], _nl(o[1])) for o in outs.values()}) == 1 and outs["py"][0] == 0
    results.append(case("[修复] detect CR 分隔正文 → py/ps1 输出逐字一致", True,
                        _det_case("cr", "第一段，句子一。\r第二段，句子二！\r第三段，句子三？".encode("utf-8"))))
    results.append(case("[修复] detect 边缘 \x1c/\x1f/\t 空白 → py/ps1 输出逐字一致", True,
                        _det_case("edgews", "\x1c第一段，句一。\t\n 段二\x1f，句二！".encode("utf-8"))))

    results.append(case("[修复] detect 带 BOM 输入 → py/ps1 单元数与输出逐字一致（utf-8-sig）", True,
                        _det_case("bom", b"\xef\xbb\xbf" + "第一段，句子一。\n第二段，句子二！".encode("utf-8"))))

    # z2) -Root 指向不存在路径 → 双引擎首行同为绝对路径、同判 exit1（GetFullPath 对缺失路径同样归一）
    _missing = str(_bdir2 / "no-such-dir-r5")

    def _bud_missing(engine):
        if engine == "py":
            cmd = [PY, str(_bdir2 / "check-doc-budget.py"), "--root", _missing]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_bdir2 / "check-doc-budget.ps1"), "-Root", _missing]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(_bdir2))
        return pr.returncode, _decode(pr.stdout)

    _rr5 = {e: _bud_missing(e) for e in ENGINES}
    _miss_n = _pnorm(_missing).lower()  # needle 也走归一（CI 上首行路径形态与原形不同）
    results.append(case("[修复] 预算 -Root 不存在路径 → 双引擎首行同为绝对路径且同判 exit1", True,
                        len({(v[0], _nl(v[1])) for v in _rr5.values()}) == 1 and _rr5["py"][0] == 1
                        and _miss_n in _pnorm(_rr5["py"][1]).splitlines()[0].lower()
                        and _miss_n in _pnorm(_rr5["ps1"][1]).splitlines()[0].lower()))

    # z3) -Project 指向普通文件 → 双引擎同文案 exit1（ps1 补 -PathType Container 后与 py is_dir 同判）
    p_file = _nhd / "p_isfile.txt"
    p_file.write_text("x", encoding="utf-8")

    def _proj_file(engine):
        if engine == "py":
            cmd = [PY, str(ROOT / "scripts" / "check-done.py"), "--project", str(p_file), "--chapter", "1"]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "check-done.ps1"), "-Project", str(p_file), "-Chapter", "1"]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    _rr6 = {e: _proj_file(e) for e in ENGINES}
    results.append(case("[修复] -Project 指向普通文件 → 双引擎同判 exit1（Container 判定）", True,
                        len({(v[0], _nl(v[1])) for v in _rr6.values()}) == 1 and _rr6["py"][0] == 1
                        and "项目目录不存在" in _rr6["py"][1]))


    # === 六轮修复回归组（B域：detect [B] 崩溃、init 守卫、state/txn bool、全角段号、Long 消费、pkg 大小写、migrate 导入） ===
    # b1) detect 同头连句 [B] 命中分支（此前夹具 0 命中 → ps1 op_Addition 崩溃假绿）
    _bfx = Path(tempfile.mkdtemp(prefix="detect-B-")) / "ai.txt"
    _bfx.write_text("他不仅加快了脚步，还下意识地摸了摸口袋。他不仅要加快脚步，还下意识地摸了摸口袋。他不仅加快了脚步，还下意识地摸了摸口袋。\n其余内容另说。", encoding="utf-8")

    def _det_b(engine):
        if engine == "py":
            cmd = [PY, str(ROOT / "scripts" / "detect_ai_text.py"), "--input", str(_bfx)]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "detect_ai_text.ps1"), "-InputFile", str(_bfx)]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    _rrB = {e: _det_b(e) for e in ENGINES}
    results.append(case("[六轮] detect 同头连句 [B] 命中 → 双引擎同文案 exit0 且命中 1 处", True,
                        len({(v[0], _nl(v[1])) for v in _rrB.values()}) == 1 and _rrB["py"][0] == 0
                        and "同头" in _rrB["py"][1] and "1 处" in _rrB["py"][1]))

    # b2) init 存量章节守卫（旧项目 → exit1 指引 migrate，不写 _state.json）
    def _init_old_ok(engine):
        proj = _nhd / f"p_initold_{engine}"
        (proj / "记忆系统" / "摘要").mkdir(parents=True)
        (proj / "记忆系统" / "圣经").mkdir(parents=True)
        for _n in (1, 2, 3):
            (proj / "记忆系统" / "摘要" / f"第{_n:03}章摘要.md").write_text(f"第{_n}章摘要", encoding="utf-8")
        (proj / "记忆系统" / "圣经" / "故事圣经.md").write_text("# 旧书 旧内容", encoding="utf-8")
        rc, out = run_state(engine, "init", "--project", str(proj))
        return rc == 1 and "存量章节" in out and "migrate" in out and not (proj / "记忆系统" / "_state.json").exists()
    results += check_engines("[六轮] init 检测到存量章节 → 双引擎同判 exit1（升级走 migrate）", True, _init_old_ok)

    # b3) init 非派生圣经覆盖警告 → 双引擎 exit0 + ⚠️ 且写入 _state.json
    def _init_warn_ok(engine):
        proj = _nhd / f"p_initwarn_{engine}"
        (proj / "记忆系统" / "圣经").mkdir(parents=True)
        (proj / "记忆系统" / "圣经" / "故事圣经.md").write_text("# 模板已填充", encoding="utf-8")
        rc, out = run_state(engine, "init", "--project", str(proj))
        return rc == 0 and "⚠️" in out and "覆盖" in out and (proj / "记忆系统" / "_state.json").exists()
    results += check_engines("[六轮] init 非派生圣经 → 双引擎覆盖警告 exit0", True, _init_warn_ok)

    # b4) state bool（last_committed_chapter=true）→ 双引擎同判 exit1（rc+关键子串；py fail-open 修复）
    def _stbool_ok(engine):
        proj = _nhd / f"p_stbool_{engine}"
        proj.mkdir()
        empty_done(proj, 1, BASE)
        write_report(proj, 1, GOOD_REPORT)
        write_body(proj, 1, 10)
        sp = proj / "记忆系统" / "_state.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"schema_version": 1, "state_revision": 1, "book_title": "t",
                                  "last_committed_chapter": True, "imported_through_chapter": 0,
                                  "characters": {}, "foreshadow": {},
                                  "timeline": {"readers_known": [], "authors_truth": []},
                                  "facts": [], "context": {}, "wordcounts": {}, "summaries": {}},
                                 ensure_ascii=False), encoding="utf-8")
        (proj / ".done" / "第001章_state.done").write_bytes(b"")  # 空状态标记，防前置短路吃掉 state 分支输出
        rc, out = run(proj, 1, engine=engine)
        return rc == 1 and "状态单校验" in out and "不是整数" in out
    results += check_engines("[六轮] state bool（last_committed_chapter=true）→ 双引擎同判 exit1", True, _stbool_ok)

    # b5) claim 全角段号 → _try_i32 后双引擎同过 rc0（对齐 Int32.TryParse 跳过；ASCII 段号超出正文段数由「第40段」既有用例钉 rc1；Int32 上溢段号双引擎一致跳过，无独立用例）
    def _claim12_ok(engine):
        proj = _nhd / f"p_claim12_{engine}"
        proj.mkdir()
        empty_done(proj, 1, BASE)
        write_report(proj, 1, GOOD_REPORT + "\n人工段注入：第１２段完成。")
        write_body(proj, 1, 10)
        rc, out = run(proj, 1, engine=engine)
        return rc == 0 and "未闭环" not in out  # 对齐 TryParse：全角段号安全跳过，双引擎同过
    results += check_engines("[六轮] claim 全角段号（第１２段）→ 双引擎同过（对齐 TryParse 跳过）", True, _claim12_ok)

    # b6) min_hits 超 Int32 → 双引擎同文案（ps1 [long] 消费，不再截断崩溃）
    def _minhits_out(engine):
        d = _nhd / "p_minhit_shared"  # 单目录共享：表头含项目路径，分目录会假红对拍
        d.mkdir(exist_ok=True)
        (d / "正文").mkdir(exist_ok=True); (d / ".done").mkdir(exist_ok=True); (d / "报告").mkdir(exist_ok=True)
        for _f in ("check-done.py", "check-done.ps1", "check-done-rules.json"):
            shutil.copy2(ROOT / "scripts" / _f, d / _f)
        _rj = json.loads((d / "check-done-rules.json").read_text(encoding="utf-8-sig"))
        _rj["min_hits"] = 3000000000
        (d / "check-done-rules.json").write_text(json.dumps(_rj, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "正文" / "第001章.md").write_text("正文内容一二三四五", encoding="utf-8")
        (d / "报告" / "第001章_全量审核报告.md").write_text(GOOD_REPORT, encoding="utf-8")
        if engine == "py":
            cmd = [PY, str(d / "check-done.py"), "--project", str(d), "--chapter", "1"]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(d / "check-done.ps1"), "-Project", str(d), "-Chapter", "1"]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    _rrMH = {e: _minhits_out(e) for e in ENGINES}
    _mh_set = {(v[0], _nl(_norm_flags(v[1])).replace("\r", "")) for v in _rrMH.values()}
    _mh_ok = len(_mh_set) == 1 and _rrMH["py"][0] == 1
    if not _mh_ok:  # 自诊断：失败时打印归一后差异（便于定位对拍口径问题）
        _mh_lines = {e: _nl(_norm_flags(v[1])).replace("\r", "").splitlines() for e, v in _rrMH.items()}
        import difflib as _dl
        for _ln in _dl.unified_diff(_mh_lines.get("py", []), _mh_lines.get("ps1", []), lineterm="", n=0):
            print(f"    [min_hits diff] {_ln}")
    results.append(case("[六轮] min_hits 超 Int32 → 双引擎同文案（ps1 [long] 消费；归一 --soft-report/-SoftReport 开关名）", True, _mh_ok))

    # b7) txn expected_state_revision=bool → commit exit1「事务缺」（py fail-open 修复）
    def _txn_bool_ok(engine):
        proj = _nhd / f"p_txnbool_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        t = proj / ".story" / "work" / "第001章" / "state.json"
        if not t.is_file():
            return False
        j = json.loads(t.read_text(encoding="utf-8"))
        j["expected_state_revision"] = True
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        return rc == 1 and "expected_state_revision" in out
    results += check_engines("[六轮] txn expected_state_revision=bool → 双引擎同判 exit1", True, _txn_bool_ok)

    # b8) migrate --apply 后 imported_through_chapter 落盘为整数（无 pwsh Double 1.0）
    def _mig_imp_ok(engine):
        proj = _mig_setup(f"p_migimp_{engine}")
        rc1d, _o1d = _mig_run(engine, proj, False)  # 先出草案（--apply 依赖已存在草案）
        rc2, _o2 = _mig_run(engine, proj, True)
        stf = proj / "记忆系统" / "_state.json"
        if rc2 != 0 or not stf.is_file():
            return False
        val = json.loads(stf.read_text(encoding="utf-8")).get("imported_through_chapter")
        return rc1d == 0 and isinstance(val, int) and val == 1
    results += check_engines("[六轮] migrate imported_through_chapter 落盘为整数（无 .0）", True, _mig_imp_ok)

    # b9) package frontmatter 大小写（Name:/Version:）→ 双引擎同文案 exit1（Ordinal 字典）
    def _pkg_case_ok(engine):
        d = _nhd / f"p_pkgcase_{engine}"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nName: demo\ndescription: 测试\nVersion: 1.0.0\n---\n正文\n", encoding="utf-8")
        if engine == "py":
            cmd = [PY, str(ROOT / "scripts" / "package_skill.py"), "--check", "--root", str(d)]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "package_skill.ps1"), "-Check", "-Root", str(d)]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode == 1 and "缺字段" in _decode(pr.stdout)
    results += check_engines("[六轮] package frontmatter 大小写（Name:）→ 双引擎同判 exit1", True, _pkg_case_ok)


    # === 七轮修复回归组（B域：limit 入 schema、state_revision 超 Int32 消费、detect 缺文件、init 全角摘要一致跳过） ===
    # c1) path_ceiling.limit float → 双引擎同文案 fail-closed（schema 收口，防 py 截断/ps1 舍入反向）
    def _lim_out(engine, lim_val):
        d = _nhd / "p_lim_shared"
        d.mkdir(exist_ok=True)
        for _f in ("check-doc-budget.py", "check-doc-budget.ps1"):
            shutil.copy2(ROOT / "scripts" / _f, d / _f)
        (d / "A.md").write_text("短", encoding="utf-8")
        (d / "doc-budget.json").write_text(json.dumps({
            "files": [{"path": "A.md", "budget": 1000, "why": "w", "layer": "protected"}],
            "paths": [{"label": "路径X", "files": ["A.md"]}],
            "path_ceiling": {"limit": lim_val, "exempt": [], "why": "w"},
            "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
        }, ensure_ascii=False), encoding="utf-8")
        if engine == "py":
            cmd = [PY, str(d / "check-doc-budget.py"), "--root", str(d)]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(d / "check-doc-budget.ps1"), "-Root", str(d)]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    _rrLim = {e: _lim_out(e, 45001.5) for e in ENGINES}
    results.append(case("[七轮] 预算 limit=float → 双引擎同文案 fail-closed（path_ceiling.limit 入 schema）", True,
                        len({(v[0], _nl(_norm_flags(v[1])).replace("\r", "")) for v in _rrLim.values()}) == 1
                        and _rrLim["py"][0] == 1 and "path_ceiling.limit 须为非负整数" in _rrLim["py"][1]))

    _rrLimN = {e: _lim_out(e, None) for e in ENGINES}
    results.append(case("[七轮] 预算 limit=null → 双引擎同文案 fail-closed（防 ps1 上限静默失效）", True,
                        len({(v[0], _nl(_norm_flags(v[1])).replace("\r", "")) for v in _rrLimN.values()}) == 1
                        and _rrLimN["py"][0] == 1 and "path_ceiling.limit 须为非负整数" in _rrLimN["py"][1]))

    # c2) state_revision 超 Int32（3e9）→ 双引擎 commit 同判 exit0（ps1 [long] 消费，不再异常页）
    def _rev3e9_ok(engine):
        proj = _nhd / f"p_rev3e9_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        stj = json.loads(spf.read_text(encoding="utf-8"))
        stj["state_revision"] = 3000000000
        spf.write_text(json.dumps(stj, ensure_ascii=False, indent=2), encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        tj = json.loads(t.read_text(encoding="utf-8"))
        tj["expected_state_revision"] = 3000000000
        t.write_text(json.dumps(tj, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        return rc == 0 and "revision→3000000001" in out
    results += check_engines("[七轮] state_revision=3e9 → 双引擎 commit 同判 exit0（[long] 消费）", True, _rev3e9_ok)

    # c3) detect 缺输入文件 → 双引擎同文案 exit1（不再 py 回溯 / ps1 Write-Error+exit2 分叉）
    def _det_miss(engine):
        miss = str(_nhd / "no-such-input-r7.txt")
        if engine == "py":
            cmd = [PY, str(ROOT / "scripts" / "detect_ai_text.py"), "--input", miss]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "detect_ai_text.ps1"), "-InputFile", miss]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    _rrDM = {e: _det_miss(e) for e in ENGINES}
    results.append(case("[七轮] detect 缺输入文件 → 双引擎同文案 exit1", True,
                        len({(v[0], _nl(v[1])) for v in _rrDM.values()}) == 1
                        and _rrDM["py"][0] == 1 and "文件不存在" in _rrDM["py"][1]))

    # c4) init 全角摘要文件名 → 双引擎一致判为非章节（ASCII 数字口径）：⚠️ 覆盖警告后 rc0，不崩溃
    def _init_fw_ok(engine):
        proj = _nhd / f"p_initfw_{engine}"
        (proj / "记忆系统" / "摘要").mkdir(parents=True)
        (proj / "记忆系统" / "圣经").mkdir(parents=True)
        (proj / "记忆系统" / "摘要" / "第１２章摘要.md").write_text("全角摘要", encoding="utf-8")
        (proj / "记忆系统" / "圣经" / "故事圣经.md").write_text("# 手写内容", encoding="utf-8")
        rc, out = run_state(engine, "init", "--project", str(proj))
        return rc == 0 and "⚠️" in out and (proj / "记忆系统" / "_state.json").exists()
    results += check_engines("[七轮] init 全角摘要文件名 → 双引擎一致（ASCII 数字口径）：⚠️ 后 rc0", True, _init_fw_ok)


    # === 八轮修复回归组（B域：migrate 超 Int32 落盘 [long]、text_guard 缺 .story 目录对齐 py makedirs） ===
    # c5) migrate 摘要含超 Int32 章号 → 草案落盘整数、双引擎同判 exit0（ps1 [long] 消费，不再异常页）
    def _mig_big_ok(engine):
        proj = _mig_setup(f"p_migbig_{engine}")
        (proj / "记忆系统" / "摘要" / "第3000000000章摘要.md").write_text("超大章摘要", encoding="utf-8")
        rc, _o = _mig_run(engine, proj, False)
        draft = proj / "记忆系统" / "_state.migrate-draft.json"
        if rc != 0 or not draft.is_file():
            return False
        val = json.loads(draft.read_text(encoding="utf-8")).get("imported_through_chapter")
        if not (isinstance(val, int) and val == 3000000000):
            return False
        rc2, _o2 = _mig_run(engine, proj, True)  # --apply 落盘同为整数 3e9（防 Double 1.0 回归）
        stf = proj / "记忆系统" / "_state.json"
        if rc2 != 0 or not stf.is_file():
            return False
        v2 = json.loads(stf.read_text(encoding="utf-8")).get("imported_through_chapter")
        return isinstance(v2, int) and v2 == 3000000000
    results += check_engines("[八轮] migrate 超 Int32 章号 → 草案落盘整数、双引擎同判 exit0（[long]）", True, _mig_big_ok)

    # c6) digest 在无 .story/ 的项目 → 双引擎同判 exit0（ps1 先建目录，对齐 py os.makedirs；曾异常页 rc1）
    def _guard_nostory(engine):
        pj = _nhd / f"p_guardns_{engine}"
        (pj / "正文").mkdir(parents=True)
        (pj / "正文" / "第001章_试炼.txt").write_text("夜色压着山脊。老陈把烟掐了。\n", encoding="utf-8")
        if engine == "py":
            cmd = [PY, str(ROOT / "scripts" / "text_guard.py"), "digest", "--project", str(pj), "--chapter", "1"]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "text_guard.ps1"), "-Command", "digest", "-Project", str(pj), "-Chapter", "1"]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        ledger = pj / ".story" / "text-digests.json"
        return pr.returncode, _decode(pr.stdout), ledger.is_file()

    _rrGN = {e: _guard_nostory(e) for e in ENGINES}
    results.append(case("[八轮] digest 缺 .story/ 目录 → 双引擎同判 exit0（ps1 自动建目录，对齐 py makedirs；账本文件真实生成）", True,
                        len({(v[0], _nl(v[1])) for v in _rrGN.values()}) == 1 and _rrGN["py"][0] == 0
                        and "已记录" in _rrGN["py"][1] and all(v[2] for v in _rrGN.values())))


    # === 九轮修复回归组（B域：内部 [int]$Ch 收口、U+001C 引用、summaries 键 3e9、未知参数 fail-closed） ===
    # d1) check-done -Chapter 3e9 全链（3e9 章报告存在 → 触达 Find-BodyFile 正文交叉校验；再跑 -RequireTaskSheet 触达 Test-TaskSheet）→ 双引擎业务报告同文案
    def _cd3e9(engine):
        proj = _nhd / f"p_cd3e9_{engine}"
        proj.mkdir()
        empty_done(proj, 1, BASE)
        write_body(proj, 3000000000, 10)  # 正文按 3e9 章命名 → find_body 命中，正文交叉校验真实执行
        (proj / ".done" / "第3000000000章_zhuque.done").write_bytes(b"")  # 报告校验挂在 zhuque 标记上，缺则 skipped
        (proj / "报告").mkdir(exist_ok=True)
        (proj / "报告" / "第3000000000章_全量审核报告.md").write_text(GOOD_REPORT, encoding="utf-8")
        outs = []
        for _tq in (False, True):
            if engine == "py":
                cmd = [PY, str(ROOT / "scripts" / "check-done.py"), "--project", str(proj), "--chapter", "3000000000"]
                if _tq:
                    cmd.append("--require-task-sheet")
            else:
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "check-done.ps1"), "-Project", str(proj), "-Chapter", "3000000000"]
                if _tq:
                    cmd.append("-RequireTaskSheet")
            pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            outs.append((pr.returncode, _nl(_norm_flags(_decode(pr.stdout))).replace(str(proj), "<PROJ>").replace(_pnorm(str(proj)), "<PROJ>")))
        return outs

    _rrC9 = {e: _cd3e9(e) for e in ENGINES}
    results.append(case("[九轮→十轮] check-done -Chapter 3e9 全链（报告校验 ok+正文交叉真实执行/任务书）→ 双引擎业务报告同文案", True,
                        len({tuple(v) for v in _rrC9.values()}) == 1 and _rrC9["py"][0][0] == 1
                        and "报告校验：ok" in _rrC9["py"][0][1] and "任务书校验：missing" in _rrC9["py"][1][1]))

    # d2) 预算 files[].path 带 U+001C 前缀 → 双引擎同判（Measure-Ref Trim 集收口）
    def _u001c_out(engine):
        d = _nhd / "p_u001c_shared"
        d.mkdir(exist_ok=True)
        for _f in ("check-doc-budget.py", "check-doc-budget.ps1"):
            shutil.copy2(ROOT / "scripts" / _f, d / _f)
        (d / "A.md").write_text("短", encoding="utf-8")
        (d / "doc-budget.json").write_text(json.dumps({
            "files": [{"path": "\x1cA.md", "budget": 1000, "why": "w", "layer": "protected"}],
            "paths": [{"label": "路径X", "files": ["A.md"]}],
            "path_ceiling": {"limit": 1000, "exempt": [], "why": "w"},
            "baseline": {"worst_path_chars": 1, "writer_delegated_chars": 0,
                         "session_resident_chars": 0, "precheck_path_chars": 0,
                         "max_single_file": "A.md=1", "measured_on": "2026-10-18"},
            "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
        }, ensure_ascii=False), encoding="utf-8")
        if engine == "py":
            cmd = [PY, str(d / "check-doc-budget.py"), "--root", str(d)]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(d / "check-doc-budget.ps1"), "-Root", str(d)]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    _rrU = {e: _u001c_out(e) for e in ENGINES}
    results.append(case("[九轮] 预算 path 带 U+001C 前缀 → 双引擎同判（Measure-Ref Trim($wsChars)）", True,
                        len({(v[0], _nl(v[1])) for v in _rrU.values()}) == 1
                        and _rrU["py"][0] == 0))

    # d3) commit summaries 键 3e9 → 双引擎 rc0 渲染 第3000000000章摘要.md（[long] 排序/文件名）
    def _sum3e9_ok(engine):
        proj = _nhd / f"p_sum3e9_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        j["delta"]["summaries"] = {"3000000000": "超大章摘要内容"}
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, _o = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        return rc == 0 and (proj / "记忆系统" / "摘要" / "第3000000000章摘要.md").is_file()
    results += check_engines("[九轮] summaries 键 3e9 → 双引擎 rc0 渲染超大章摘要（[long] 排序/文件名）", True, _sum3e9_ok)

    # d4) migrate_memory_paths 未知参数 → fail-closed（CmdletBinding；曾静默吞掉）
    def _mmp_unknown(engine):
        if engine == "py":
            return None  # 脚本仅 ps1 实现
        cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "migrate_memory_paths.ps1"), "-NoSuchParam", "x"]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout) + _decode(pr.stderr)

    _rrMMP = {e: _mmp_unknown(e) for e in ENGINES}
    results.append(case("[九轮] migrate_memory_paths 未知参数 → fail-closed（CmdletBinding）", True,
                        all(v is None or (v[0] == 1 and "NoSuchParam" in v[1]) for v in _rrMMP.values())
                        and any(v is not None and v[0] == 1 for v in _rrMMP.values())))


    # === 十轮修复回归组（B域：delta.summaries 键写盘前 fail-closed，防污染状态单） ===
    # d5) delta.summaries 非数字键 → 写盘前 fail-closed、状态单字节不变
    def _badkey_ok(engine):
        proj = _nhd / f"p_badkey_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        j["delta"]["summaries"] = {"abc": "坏键"}
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        after = spf.read_text(encoding="utf-8") if spf.is_file() else ""
        return rc == 1 and "summaries 键必须是十进制数字字符串" in out and after == before
    results += check_engines("[十轮] delta.summaries 非数字键 → 双引擎同文案 exit1 且状态单未污染", True, _badkey_ok)

    # d6) delta.summaries 20 位键（>Int64）→ 双引擎同文案 exit1（≤18 位上限，渲染侧不再溢出）
    def _hugekey_ok(engine):
        proj = _nhd / f"p_hugekey_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        j["delta"]["summaries"] = {"99999999999999999999": "超界键"}
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        after = spf.read_text(encoding="utf-8") if spf.is_file() else ""
        return rc == 1 and "summaries 键必须是十进制数字字符串" in out and after == before
    results += check_engines("[十轮] delta.summaries 20 位键（>Int64）→ 双引擎同文案 exit1（≤18 位上限）", True, _hugekey_ok)


    # === 十一轮修复回归组（B域：delta 类型 fail-closed、19 位键边界档） ===
    # d7) delta.summaries 19 位键（Int64 可装但超 18 位渲染安全界）→ 双引擎同文案 exit1、状态单不变
    def _key19_ok(engine):
        proj = _nhd / f"p_key19_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        j["delta"]["summaries"] = {"1" * 19: "19位键"}
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        after = spf.read_text(encoding="utf-8") if spf.is_file() else ""
        return rc == 1 and "summaries 键必须是十进制数字字符串" in out and after == before
    results += check_engines("[十一轮] delta.summaries 19 位键 → 双引擎同文案 exit1（18 位=渲染安全界）", True, _key19_ok)

    # d8) delta 非对象五形态（null/字符串/数字/数组/布尔）→ 双引擎同文案 exit1、状态单不变（ps1 类型校验收口）
    def _deltanull_ok(engine):
        proj = _nhd / f"p_dnull_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j0 = json.loads(t.read_text(encoding="utf-8"))
        for bad in (None, "x", 123, [], True):
            j = dict(j0)
            j["delta"] = bad
            t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
            after = spf.read_text(encoding="utf-8") if spf.is_file() else ""
            if not (rc == 1 and "事务缺 delta 对象" in out and after == before):
                return False
        return True
    results += check_engines("[十一轮] delta 非对象五形态 → 双引擎同文案 exit1 且状态单不变", True, _deltanull_ok)


    # === 十二轮修复回归组（B域：delta 嵌套值类型、18 位放行档、正文交叉文案、context 单条 parity） ===
    # d9) delta 嵌套值畸形（summaries=数字 / facts=字符串）→ 写盘前双引擎同文案 exit1、状态单不变
    def _dnested_ok(engine):
        proj = _nhd / f"p_dnsted_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j0 = json.loads(t.read_text(encoding="utf-8"))
        for key, bad, needle in (("summaries", 5, "delta.summaries 须为对象"),
                                 ("wordcounts", 5, "delta.wordcounts 须为对象"),
                                 ("overview", 5, "delta.overview 须为对象"),
                                 ("context", 5, "delta.context 须为对象"),
                                 ("characters", 5, "delta.characters 须为对象"),
                                 ("timeline", "x", "delta.timeline 须为对象"),
                                 ("foreshadow", "x", "delta.foreshadow 须为对象"),
                                 ("facts", "abc", "delta.facts 须为数组")):
            j = json.loads(json.dumps(j0))
            j["delta"][key] = bad
            t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
            after = spf.read_text(encoding="utf-8") if spf.is_file() else ""
            if not (rc == 1 and needle in out and after == before):
                return False
        return True
    results += check_engines("[十二→十三轮] delta 嵌套值畸形八键全矩阵 → 双引擎同判 exit1 且状态单不变", True, _dnested_ok)

    # d10) summaries 键 18 位正向放行档（边界下沿）→ 双引擎 rc0 且摘要文件落盘
    def _key18_ok(engine):
        proj = _nhd / f"p_key18_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        j["delta"]["summaries"] = {"1" * 18: "18位键放行"}
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, _o = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        return rc == 0 and (proj / "记忆系统" / "摘要" / ("第" + "1" * 18 + "章摘要.md")).is_file()
    results += check_engines("[十二轮] summaries 键 18 位 → 双引擎 rc0 放行且摘要落盘（边界下沿）", True, _key18_ok)

    # d11) 正文交叉真实执行：3e9 章报告声明第40段（正文10段）→ 双引擎同文案 body-mismatch
    def _cross40(engine):
        proj = _nhd / f"p_cross40_{engine}"
        proj.mkdir()
        empty_done(proj, 1, BASE)
        write_body(proj, 3000000000, 10)
        (proj / ".done" / "第3000000000章_zhuque.done").write_bytes(b"")
        (proj / "报告").mkdir(exist_ok=True)
        (proj / "报告" / "第3000000000章_全量审核报告.md").write_text(GOOD_REPORT + "\n人工段注入：第40段完成。", encoding="utf-8")
        if engine == "py":
            cmd = [PY, str(ROOT / "scripts" / "check-done.py"), "--project", str(proj), "--chapter", "3000000000"]
        else:
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "check-done.ps1"), "-Project", str(proj), "-Chapter", "3000000000"]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout).replace(str(proj), "<PROJ>").replace(_pnorm(str(proj)), "<PROJ>")

    _rrC40 = {e: _cross40(e) for e in ENGINES}
    results.append(case("[十二轮] 正文交叉真实执行（3e9 章第40段声明）→ 双引擎同文案 body-mismatch", True,
                        len({(v[0], _nl(v[1])) for v in _rrC40.values()}) == 1 and _rrC40["py"][0] == 1
                        and "正文交叉校验" in _rrC40["py"][1]))

    # d12) context 缺键 → 双引擎恰好 1 条错误（文案 parity：不再双计）
    def _ctxmiss_ok(engine):
        proj = _nhd / f"p_ctxmiss_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        stj = json.loads(spf.read_text(encoding="utf-8"))
        stj.pop("context", None)
        spf.write_text(json.dumps(stj, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return (rc == 1 and "缺字段：context" in out and "context 必须是对象" not in out
                and "（1 项）" in out)
    results += check_engines("[十二轮] context 缺键 → 双引擎恰好 1 条错误（不再双计）", True, _ctxmiss_ok)


    # === 十三轮修复回归组（B域：chapter 判型/schema 严格/last_committed 闸/check 渲染兜底） ===
    # d13) 事务 chapter=1.0 / true（数值相等但类型非法）→ 双引擎同判 exit1、状态单不变
    def _ch10_ok(engine):
        proj = _nhd / f"p_ch10_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j0 = json.loads(t.read_text(encoding="utf-8"))
        for bad in (1.0, True):
            j = json.loads(json.dumps(j0))
            j["chapter"] = bad
            t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
            after = spf.read_text(encoding="utf-8") if spf.is_file() else ""
            if not (rc == 1 and "事务 chapter" in out and after == before):
                return False
        return True
    results += check_engines("[十三轮] 事务 chapter=1.0/true → 双引擎同判 exit1 且状态单不变", True, _ch10_ok)

    # d14) schema_version="1"/1.0 → 双引擎同判 exit1（严格判型；float 文案对齐）
    def _sv_ok(engine):
        proj = _nhd / f"p_sv_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j0 = json.loads(spf.read_text(encoding="utf-8"))
        for bad in ("1", 1.0):
            j = json.loads(json.dumps(j0))
            j["schema_version"] = bad
            spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "check", "--project", str(proj))
            if not (rc == 1 and "schema_version 应为 1" in out):
                return False
        spf.write_text(json.dumps(j0, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    results += check_engines("[十三轮] schema_version 字符串「1」/1.0 → 双引擎同判 exit1（严格判型）", True, _sv_ok)

    # d15) last_committed_chapter="abc" → 双引擎业务输出同文案（validate 先行，无裸 Traceback/空白 stdout）
    def _lastabc(engine):
        proj = _nhd / f"p_lastabc_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j = json.loads(spf.read_text(encoding="utf-8"))
        j["last_committed_chapter"] = "abc"
        spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return rc == 1 and "last_committed_chapter 必须是整数" in out

    _rrLA = {e: _lastabc(e) for e in ENGINES}
    results.append(case("[十三轮] last_committed_chapter 非法字符串 → 双引擎校验文案一致（无裸 Traceback/空白）", True,
                        all(v for v in _rrLA.values()))) 

    # d16) 现有 state summaries 非法键（abc）→ check 双引擎中文契约输出（ps1 不再异常页空白）
    def _summabc(engine):
        proj = _nhd / f"p_summabc_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j = json.loads(spf.read_text(encoding="utf-8"))
        j["summaries"] = {"abc": "x"}
        spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return rc == 1 and "状态单校验失败" in out and "summaries" in out

    results += check_engines("[十三轮] state summaries 非法键 → check 双引擎中文契约（ps1 兜底不空白）", True, _summabc)


    # === 十四轮修复回归组（B域：深层类型闸、三层深合并对拍、半提交契约） ===
    # d17) delta 深层畸形三形态（context.position=5 / overview.stages=5 / characters.C1=5）→ 写盘前双引擎同判、状态单不变
    def _deepdelta_ok(engine):
        proj = _nhd / f"p_deepd_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j0 = json.loads(t.read_text(encoding="utf-8"))
        for mk, needle in ((lambda j: j["delta"].setdefault("context", {}).update({"position": 5}), "delta.context.position 须为对象"),
                           (lambda j: j["delta"].setdefault("overview", {}).update({"stages": 5}), "delta.overview.stages 须为数组"),
                           (lambda j: j["delta"].setdefault("characters", {}).update({"C1": 5}), "delta.characters.C1 须为对象")):
            j = json.loads(json.dumps(j0))
            mk(j)
            t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
            after = spf.read_text(encoding="utf-8") if spf.is_file() else ""
            txn_kept = (proj / ".story" / "work" / "第001章" / "state.json").is_file()
            if not (rc == 1 and needle in out and after == before and txn_kept):
                return False
        return True
    results += check_engines("[十四轮] delta 深层畸形三形态 → 双引擎同判 exit1 且状态单不变（不半提交）", True, _deepdelta_ok)

    # d18) 三层嵌套深合并对拍：两笔 commit 后 foreshadow.F1.meta 保留旧键 src，双引擎终态一致
    def _deepmerge_ok(engine):
        proj = _nhd / f"p_deepm_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        def _commit(delta, ch):
            if run_state(engine, "draft", "--project", str(proj), "--chapter", str(ch))[0] != 0:
                return False
            t = proj / ".story" / "work" / f"第{ch:03d}章" / "state.json"
            j = json.loads(t.read_text(encoding="utf-8"))
            for k, v in delta.items():
                j["delta"][k] = v
            t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            return run_state(engine, "commit", "--project", str(proj), "--chapter", str(ch), "--input", str(t))[0] == 0
        if not _commit({"foreshadow": {"F1": {"desc": "伏笔A", "meta": {"src": "甲"}}}}, 1):
            return False
        if not _commit({"foreshadow": {"F1": {"meta": {"extra": "乙"}}}}, 2):
            return False
        st = json.loads((proj / "记忆系统" / "_state.json").read_text(encoding="utf-8"))
        meta = (st.get("foreshadow") or {}).get("F1", {}).get("meta", {})
        return meta.get("src") == "甲" and meta.get("extra") == "乙"
    results += check_engines("[十四轮] 三层嵌套深合并 → 保留旧键 src，双引擎同判（与 py _deep_merge 同语义）", True, _deepmerge_ok)

    # d19) 现有 state characters=5 → check 双引擎同判 rc1（validate 先行，ps1 不再 fail-open）
    def _ch5_ok(engine):
        proj = _nhd / f"p_ch5_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j = json.loads(spf.read_text(encoding="utf-8"))
        j["characters"] = 5
        spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return rc == 1 and "characters 须为对象" in out
    results += check_engines("[十四轮] state characters=5 → check 双引擎同判 rc1（validate 先行，不 fail-open）", True, _ch5_ok)


    # === 十五轮修复回归组（B域：族闸补齐、float 实值、溢出、写视图失败、跨引擎互认） ===
    # d20) 现有 state 族闸三形态（facts=5 / timeline.readers_known="abc" / foreshadow=5）→ check 双引擎 rc1
    def _fam_ok(engine):
        okv = True
        for _fi_f2, (mk, needle) in enumerate(((lambda j: j.update({"facts": 5}), "facts 须为数组"),
                           (lambda j: j["timeline"].update({"readers_known": "abc"}), "timeline.readers_known 须为数组"),
                           (lambda j: j.update({"foreshadow": 5}), "foreshadow 须为对象"))):
            proj = _nhd / f"p_fam_{engine}_{_fi_f2}"
            proj.mkdir()
            if run_state(engine, "init", "--project", str(proj))[0] != 0:
                return False
            spf = proj / "记忆系统" / "_state.json"
            j = json.loads(spf.read_text(encoding="utf-8"))
            mk(j)
            spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "check", "--project", str(proj))
            okv = okv and (rc == 1 and needle in out)
        return okv
    results += check_engines("[十五轮] state 族闸三形态 → check 双引擎同判 rc1（族闸补齐）", True, _fam_ok)

    # d21) delta 族闸（timeline.readers_known 非数组 / context.long_term_constraints 非数组）→ 写盘前 rc1 + 事务保留
    def _dfam_ok(engine):
        proj = _nhd / f"p_dfam_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        okv = True
        for mk, needle in ((lambda j: j["delta"].setdefault("timeline", {}).update({"readers_known": "abc"}), "delta.timeline.readers_known 须为数组"),
                           (lambda j: j["delta"].setdefault("context", {}).update({"long_term_constraints": "abc"}), "delta.context.long_term_constraints 须为数组")):
            if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
                return False
            t = proj / ".story" / "work" / "第001章" / "state.json"
            j = json.loads(t.read_text(encoding="utf-8"))
            mk(j)
            before = (proj / "记忆系统" / "_state.json").read_text(encoding="utf-8")
            t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
            after = (proj / "记忆系统" / "_state.json").read_text(encoding="utf-8") if (proj / "记忆系统" / "_state.json").is_file() else ""
            okv = okv and (rc == 1 and needle in out and after == before and t.is_file())
        return okv
    results += check_engines("[十五轮] delta 族闸两形态 → 写盘前 rc1 且状态单不变+事务保留", True, _dfam_ok)

    # d22) float 归一实值断言：schema_version=1.0 → 文案为「实际 1」且不含「1.0」
    def _f10_ok(engine):
        proj = _nhd / f"p_f10_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j = json.loads(spf.read_text(encoding="utf-8"))
        j["schema_version"] = 1.0
        spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return rc == 1 and "实际 1" in out and "实际 1.0" not in out
    results += check_engines("[十五轮] float 归一实值 → 「实际 1」不含「1.0」（双引擎）", True, _f10_ok)

    # d23) stale 扫描超长章号文件名（20 位，[long] 溢出边界）→ 双引擎 rc0 无英文异常页
    def _ovf_ok(engine):
        proj = _nhd / f"p_ovf_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        (proj / "正文").mkdir()
        (proj / "正文" / "第99999999999999999999章正文.md").write_text("x", encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return rc == 0 and "摘要连续" in out and "Exception" not in out and "Traceback" not in out
    results += check_engines("[十五轮] stale 超长章号文件名（20位）→ 双引擎 rc0 无异常页", True, _ovf_ok)

    # d24) 写派生视图失败（圣经目录被文件占位）→ 双引擎同文案「派生视图未刷新」+ 事务保留
    def _wv_ok(engine):
        proj = _nhd / f"p_wv_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        bible = proj / "记忆系统" / "圣经"
        import shutil as _sh
        _sh.rmtree(bible)
        bible.write_text("占位", encoding="utf-8")
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        t = proj / ".story" / "work" / "第001章" / "state.json"
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        return rc == 1 and "派生视图未刷新" in out and t.is_file() and "Traceback" not in out
    results += check_engines("[十五轮] 写派生视图失败 → 双引擎同文案（提示改走补渲染）+ 事务保留", True, _wv_ok)

    # d25) 跨引擎互认：同一项目 py 提交 ch1、ps1 提交 ch2，双方 check 均 rc0 且深合并终态一致
    proj_cross = _nhd / "p_cross"
    proj_cross.mkdir()
    def _commit_cross(engine, delta, ch):
        if run_state(engine, "draft", "--project", str(proj_cross), "--chapter", str(ch))[0] != 0:
            return False
        t = proj_cross / ".story" / "work" / f"第{ch:03d}章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        for k, v in delta.items():
            j["delta"][k] = v
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        return run_state(engine, "commit", "--project", str(proj_cross), "--chapter", str(ch), "--input", str(t))[0] == 0
    cross_ok = run_state("py", "init", "--project", str(proj_cross))[0] == 0
    cross_ok = cross_ok and _commit_cross("py", {"foreshadow": {"F1": {"desc": "伏笔A", "meta": {"src": "甲"}}}}, 1)
    cross_ok = cross_ok and _commit_cross("ps1", {"foreshadow": {"F1": {"meta": {"extra": "乙"}}}}, 2)
    if cross_ok:
        stc = json.loads((proj_cross / "记忆系统" / "_state.json").read_text(encoding="utf-8"))
        metac = (stc.get("foreshadow") or {}).get("F1", {}).get("meta", {})
        cross_ok = metac.get("src") == "甲" and metac.get("extra") == "乙"
        cross_ok = cross_ok and run_state("py", "check", "--project", str(proj_cross))[0] == 0
        cross_ok = cross_ok and run_state("ps1", "check", "--project", str(proj_cross))[0] == 0
    results.append(case("[十五轮] 跨引擎互认：py 提交 ch1 + ps1 提交 ch2 → 双方 check 均 rc0 且深合并终态一致", True, cross_ok, ""))


    # === 十六轮修复回归组（B域：bool 章号、全角色遍历、报错条数、合并复验） ===
    # d26a) facts[i].chapter=bool → check 双引擎 rc1（py bool 排除）
    def _boolch_ok(engine):
        proj = _nhd / f"p_boolch_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j = json.loads(spf.read_text(encoding="utf-8"))
        j["facts"] = [{"chapter": True, "title": "t"}]
        spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return rc == 1 and "facts[0].chapter 须为整数" in out
    results += check_engines("[十六轮] facts chapter=bool → check 双引擎 rc1（bool 排除）", True, _boolch_ok)

    # d26b) 多角色：甲 合法 + 乙 aliases 非数组 → 双引擎 rc1 点名乙（全角色遍历）
    def _multirole_ok(engine):
        proj = _nhd / f"p_multi_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j = json.loads(spf.read_text(encoding="utf-8"))
        j["characters"] = {"甲": {"snapshot": "s", "aliases": []}, "乙": {"snapshot": "s", "aliases": "串"}}
        spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return (rc == 1 and "characters.乙.aliases 须为数组" in out
                and "characters.甲" not in out and "（1 项）" in out)
    results += check_engines("[十六轮] 第二角色 aliases 非数组 → 双引擎 rc1 点名乙（全角色遍历）", True, _multirole_ok)

    # d26c) 同角色 aliases+known_facts 双坏 → 双引擎均报「2 项」（报错条数一致）
    def _count2_ok(engine):
        proj = _nhd / f"p_cnt2_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        spf = proj / "记忆系统" / "_state.json"
        j = json.loads(spf.read_text(encoding="utf-8"))
        j["characters"] = {"甲": {"snapshot": "s", "aliases": "串", "known_facts": "串2"}}
        spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "check", "--project", str(proj))
        return (rc == 1 and "（2 项）" in out and "（3 项）" not in out
                and "characters.甲.aliases 须为数组" in out and "characters.甲.known_facts 须为数组" in out
                and "Traceback" not in out)
    results += check_engines("[十六轮] 同角色双键同坏 → 双引擎均报「2 项」（明细一致）", True, _count2_ok)

    # d26d) delta 注入 aliases 非数组 → commit 合并复验 rc1 + 状态单不变 + 事务保留
    def _revalidate_ok(engine):
        proj = _nhd / f"p_reval_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        j["delta"]["characters"] = {"甲": {"snapshot": "s", "aliases": "串"}}
        spf = proj / "记忆系统" / "_state.json"
        before = spf.read_text(encoding="utf-8")
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        after = spf.read_text(encoding="utf-8")
        lock_left = any(proj.glob("*.lock"))
        okv = (rc == 1 and "合并结果复验失败（改 delta 后重跑同一份 commit）：" in out
               and "characters.甲.aliases 须为数组" in out and after == before and t.is_file()
               and not lock_left and "Traceback" not in out)
        if okv:  # 修 delta 后立即重跑同一份 commit（锁不残留）
            j["delta"]["characters"] = {"甲": {"snapshot": "s", "aliases": []}}
            t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            okv = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))[0] == 0
        return okv
    results += check_engines("[十六轮] delta 注入 aliases → 合并复验 rc1 且状态不变+事务保留+锁释放+可立即重跑", True, _revalidate_ok)


    # === 十七轮修复回归组（B域：锁释放契约、元素型闸、commit 路径 bool、消息逐字、引擎守卫） ===
    # d27) 元素型闸两形态（stages=[[1,2]] / foreshadow.F1="串"）→ check 双引擎 rc1 同文案
    def _elem_ok(engine):
        okv = True
        for _fi_e, needle in enumerate(("overview.stages 元素须为字符串或对象", "foreshadow.F1 须为对象")):
            proj = _nhd / f"p_elem_{engine}_{_fi_e}"
            proj.mkdir()
            if run_state(engine, "init", "--project", str(proj))[0] != 0:
                return False
            spf = proj / "记忆系统" / "_state.json"
            j = json.loads(spf.read_text(encoding="utf-8"))
            if "stages" in needle:
                j["overview"]["stages"] = [[1, 2]]
            else:
                j["foreshadow"] = {"F1": "串"}
            spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "check", "--project", str(proj))
            okv = okv and rc == 1 and needle in out
        return okv
    results += check_engines("[十七轮] 元素型闸两形态 → check 双引擎 rc1 同文案（封死渲染层穿透）", True, _elem_ok)

    # d28) commit 路径 bool 章号 → 合并复验拦截（双引擎同文案，锁释放，事务保留）
    _reval_msgs: dict = {}
    def _commitbool_ok(engine):
        proj = _nhd / f"p_cbool_{engine}"
        proj.mkdir()
        if run_state(engine, "init", "--project", str(proj))[0] != 0:
            return False
        if run_state(engine, "draft", "--project", str(proj), "--chapter", "1")[0] != 0:
            return False
        t = proj / ".story" / "work" / "第001章" / "state.json"
        j = json.loads(t.read_text(encoding="utf-8"))
        j["delta"]["facts"] = [{"chapter": True, "title": "t"}]
        before = (proj / "记忆系统" / "_state.json").read_text(encoding="utf-8")
        t.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run_state(engine, "commit", "--project", str(proj), "--chapter", "1", "--input", str(t))
        _reval_msgs[engine] = out.strip()
        return (rc == 1 and "合并结果复验失败" in out and "facts[0].chapter 须为整数" in out
                and (proj / "记忆系统" / "_state.json").read_text(encoding="utf-8") == before
                and t.is_file() and not any(proj.glob("*.lock")) and "Traceback" not in out)
    results += check_engines("[十七轮] commit 路径 bool 章号 → 合并复验 rc1 且状态不变+事务保留+锁释放", True, _commitbool_ok)
    results.append(case("[十七轮] 复验失败消息 py/ps1 逐字一致（单行「；」明细）", True,
                        len(_reval_msgs) == 2 and _reval_msgs.get("py") == _reval_msgs.get("ps1"),
                        f"py={_reval_msgs.get('py', '')[:80]} || ps1={_reval_msgs.get('ps1', '')[:80]}"))

    # 基础守卫：双引擎必须齐全（缺 PowerShell 时不得静默降级单引擎全绿）
    results.append(case("[十七轮] 双引擎守卫：ENGINES 必含 py+ps1", True,
                        len(ENGINES) == 2 and set(ENGINES) == {"py", "ps1"},
                        f"ENGINES={ENGINES}——本机需 pwsh(7) 或 Windows PowerShell 在 PATH"))


    # === 十八轮修复回归组（B域：null 元素穿透双引擎同判） ===
    # d29) stages=[null] / timeline=[null] / aliases=[null] / facts=[null] → check 双引擎同文案 rc1
    _null_msgs: dict = {}
    def _nullelem_ok(engine):
        okv = True
        _null_msgs[engine] = []
        forms = ((lambda j: j["overview"].update({"stages": [None]}), "overview.stages 元素须为字符串或对象"),
                 (lambda j: j["timeline"].update({"readers_known": [None]}), "timeline.readers_known 元素须为字符串或对象"),
                 (lambda j: j.update({"characters": {"甲": {"snapshot": "s", "aliases": [None]}}}), "characters.甲.aliases 元素须为字符串"),
                 (lambda j: j.update({"facts": [None]}), "facts[0] 须为对象"))
        for _fi_f, (mk, needle) in enumerate(forms):
            proj = _nhd / f"p_nullelem_{engine}_{_fi_f}"
            proj.mkdir()
            if run_state(engine, "init", "--project", str(proj))[0] != 0:
                return False
            spf = proj / "记忆系统" / "_state.json"
            j = json.loads(spf.read_text(encoding="utf-8"))
            mk(j)
            spf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_state(engine, "check", "--project", str(proj))
            _null_msgs[engine].append(out.strip().replace(str(proj), "<PROJ>").replace(_pnorm(str(proj)), "<PROJ>"))
            okv = okv and rc == 1 and needle in out
        return okv
    results += check_engines("[十八轮] null 元素四形态 → check 双引擎同文案 rc1（无 null 豁免）", True, _nullelem_ok)
    results.append(case("[十八轮] null 元素四形态错误输出 py/ps1 逐字一致", True,
                        len(_null_msgs) == 2 and _null_msgs.get("py") == _null_msgs.get("ps1"),
                        "输出不一致" if _null_msgs.get("py") != _null_msgs.get("ps1") else ""))


    # === 十八轮修复回归组 2（数字域：基线闸 pw7 数组拍平修复） ===
    # d30) baseline.session_resident_chars 过期 → py/ps1(pw7)/ps1(5.1) 三引擎同文案 rc1
    _bd3 = Path(tempfile.mkdtemp(prefix="budget-blsync-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _bd3 / _f)
    (_bd3 / "A.md").write_text("一" * 100, encoding="utf-8")
    (_bd3 / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "基线闸用例", "layer": "protected"}],
        "paths": [{"label": "路径B", "files": "A.md"},
                  {"label": "写一章·写手委派", "files": "A.md"},
                  {"label": "主会话常驻（入口+协议+索引）", "files": "A.md"},
                  {"label": "写前预检", "files": "A.md"}],
        "generated": "G.md",
        "path_ceiling": {"limit": 100000, "why": "t"},
        "baseline": {"worst_path_chars": 999, "writer_delegated_chars": 999,
                     "session_resident_chars": 999, "precheck_path_chars": 999,
                     "max_single_file": "A.md=999", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _bl3_run(engine: str) -> tuple[int, str]:
        if engine == "ps1":
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_bd3 / "check-doc-budget.ps1"), "-Root", str(_bd3)]
        else:
            cmd = [PY, str(_bd3 / "check-doc-budget.py"), "--root", str(_bd3)]
        proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
    _r_b3 = {e: _bl3_run(e) for e in ENGINES}
    results.append(case("[十八轮] 基线闸五字段全过期 → ps1 rc1 且五字段全点名（Int64 判型已修）", True,
                        _r_b3["ps1"][0] == 1 and _r_b3["ps1"][1].count("基线过期") == 5
                        and all(_k in _r_b3["ps1"][1] for _k in ("worst_path_chars", "writer_delegated_chars", "session_resident_chars", "precheck_path_chars", "max_single_file"))))
    results.append(case("[十八轮] 基线闸过期 → py/ps1 错误清单逐条一致", True,
                        _r_b3["py"][0] == 1 and _nl(_r_b3["ps1"][1]) == _nl(_r_b3["py"][1])))


    # === 十九轮修复回归组（数字域：基线值浮点型 fail-closed） ===
    # d31) baseline 浮点写法（46959.0）→ py/ps1 均 rc1「基线非法」
    _bd4 = Path(tempfile.mkdtemp(prefix="budget-badtype-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _bd4 / _f)
    (_bd4 / "A.md").write_text("一" * 100, encoding="utf-8")
    (_bd4 / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "浮点基线用例", "layer": "protected"}],
        "paths": [{"label": "路径F", "files": "A.md"}],
        "generated": "G.md",
        "path_ceiling": {"limit": 100000, "why": "t"},
        "baseline": {"worst_path_chars": 100.0, "writer_delegated_chars": 100,
                     "session_resident_chars": 100, "precheck_path_chars": 100,
                     "max_single_file": "A.md=100", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _badtype_run(engine: str) -> tuple[int, str]:
        if engine == "ps1":
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_bd4 / "check-doc-budget.ps1"), "-Root", str(_bd4)]
        else:
            cmd = [PY, str(_bd4 / "check-doc-budget.py"), "--root", str(_bd4)]
        proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
    _r_bt = {e: _badtype_run(e) for e in ENGINES}
    results.append(case("[十九轮] baseline 浮点值 → py rc1 且点名「基线非法」（非整型 fail-closed）", True,
                        _r_bt["py"][0] == 1 and "基线非法" in _r_bt["py"][1] and "worst_path_chars" in _r_bt["py"][1]))
    results.append(case("[十九轮] baseline 浮点值 → ps1 与 py 同文案逐条一致", True,
                        _r_bt["ps1"][0] == 1 and _nl(_r_bt["ps1"][1]) == _nl(_r_bt["py"][1])))


    # === 二十轮修复回归组（数字域：基线闸五键齐全/换型 fail-closed） ===
    # d32) 缺键/块缺失/字符串换型/布尔换型/max_single 非串 → 双引擎 rc1「基线非法/基线缺键」
    _bl_muts = [
        (lambda b: b["baseline"].pop("precheck_path_chars"), "基线缺键", "precheck_path_chars", "二十轮"),
        (lambda b: b.update({"baselineX": b.pop("baseline")}), "基线非法：baseline 块缺失", "baseline 块缺失", "二十轮"),
        (lambda b: b["baseline"].update({"worst_path_chars": "0"}), "基线非法", "worst_path_chars", "二十轮"),
        (lambda b: b["baseline"].update({"writer_delegated_chars": True}), "基线非法", "writer_delegated_chars", "二十轮"),
        (lambda b: b["baseline"].update({"max_single_file": 123}), "基线非法", "max_single_file", "二十轮"),
        (lambda b: b.update({"baseline": {}}), "基线缺键", "worst_path_chars", "二十一轮"),
        (lambda b: b["baseline"].update({"max_single_file": "B.md=50"}), "基线过期", "路径段须为实测最大单文件名", "二十一轮"),
    ]
    def _blmut_run(engine, mut, _d):
        if engine == "ps1":
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_d / "check-doc-budget.ps1"), "-Root", str(_d)]
        else:
            cmd = [PY, str(_d / "check-doc-budget.py"), "--root", str(_d)]
        pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return pr.returncode, _decode(pr.stdout)

    def _blmut_pair(i, mut):
        _d = Path(tempfile.mkdtemp(prefix="budget-blmut-"))
        for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
            shutil.copy2(ROOT / "scripts" / _f, _d / _f)
        (_d / "A.md").write_text("一" * 50, encoding="utf-8")
        _b = {"files": [{"path": "A.md", "budget": 100000, "why": "二十轮基线闸用例", "layer": "protected"}],
              "paths": [], "path_ceiling": {"limit": 100000, "exempt": [], "why": "t"},
              "baseline": {"worst_path_chars": 0, "writer_delegated_chars": 0,
                           "session_resident_chars": 0, "precheck_path_chars": 0,
                           "max_single_file": "A.md=50", "measured_on": "2026-10-18"},
              "context_layers": {"protected": "p", "compressible": "c", "why": "w"}}
        mut(_b)
        (_d / "doc-budget.json").write_text(json.dumps(_b, ensure_ascii=False), encoding="utf-8")
        return {en: _blmut_run(en, mut, _d) for en in ENGINES}
    _rr_blm = {i: _blmut_pair(i, m) for i, (m, _n1, _n2, _t1) in enumerate(_bl_muts)}
    for i, (m, needle1, needle2, _tagm) in enumerate(_bl_muts):
        results.append(case(f"[{_tagm}] 基线闸变异{i + 1}（{needle1}/{needle2}）→ py rc1 且点名", True,
                            _rr_blm[i]["py"][0] == 1 and needle1 in _rr_blm[i]["py"][1]
                            and needle2 in _rr_blm[i]["py"][1]))
    _cross("[二十轮] 基线闸七类变异 → ps1 与 py 错误清单逐条一致", True,
           lambda: all(_nl(_rr_blm[i]["ps1"][1]) == _nl(_rr_blm[i]["py"][1])
                       and _rr_blm[i]["ps1"][0] == 1 for i in range(len(_bl_muts))))


    # === 二十二轮修复回归组（BOM/深度/展示/Int64 双引擎同判） ===
    # d34) ①fingerprint BOM ②detect 词表 BOM ③foreshadow 状态单 BOM ④state 深层落盘 ⑤预算展示 1e30 ⑥超 Int64 limit
    # ① fingerprint 带 BOM → 双引擎指标值全等（py utf-8-sig 对齐 ps1 剥 BOM）
    _f_bom = _nhd / "fp_bom.txt"
    _f_bom.write_bytes(b"\xef\xbb\xbf" + "甲乙丙。丁戊己。庚辛壬。".encode("utf-8"))
    _rr_bfp = {}
    for _e_bfp in ENGINES:
        if _e_bfp == "py":
            _cmd_bfp = [PY, str(ROOT / "scripts" / "fingerprint.py"), str(_f_bom)]
        else:
            _cmd_bfp = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "fingerprint.ps1"), "-Path", str(_f_bom), "-Json"]
        _pr_bfp = subprocess.run(_cmd_bfp, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_bfp[_e_bfp] = (_pr_bfp.returncode, _decode(_pr_bfp.stdout))
    def _fp_json(o):
        d = json.loads(o)
        return {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in d.items()}
    _fp_norm = [{k: (float(v) if isinstance(v, (int, float)) else v) for k, v in _fp_json(v_[1]).items()} for v_ in _rr_bfp.values()]
    results.append(case("[二十二轮] fingerprint 带 BOM → py/ps1 全部指标键值全等 rc0（py utf-8-sig 对齐）", True,
                        all(v[0] == 0 for v in _rr_bfp.values())
                        and len(_fp_norm) == 2 and _fp_norm[0] == _fp_norm[1] and "总字数" in _fp_norm[0]))
    # ② detect --extra-lexicon 带 BOM → 双引擎命中数同文案（py 词表入口 utf-8-sig）
    _lex_b = _nhd / "lex_bom.txt"
    _lex_b.write_bytes(b"\xef\xbb\xbf" + "套话一\n套话二\n".encode("utf-8"))
    _in_b = _nhd / "in_bom.txt"
    _in_b.write_text("套话一。他说套话二。", encoding="utf-8")
    _rr_blex = {}
    for _e_blx in ENGINES:
        if _e_blx == "py":
            _cmd_blx = [PY, str(ROOT / "scripts" / "detect_ai_text.py"), "--input", str(_in_b), "--extra-lexicon", str(_lex_b)]
        else:
            _cmd_blx = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "detect_ai_text.ps1"), "-InputFile", str(_in_b), "-ExtraLexicon", str(_lex_b)]
        _pr_blx = subprocess.run(_cmd_blx, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_blex[_e_blx] = (_pr_blx.returncode, _decode(_pr_blx.stdout))
    results.append(case("[二十二轮] detect 词表带 BOM → 双引擎「套话一」同命中同文案 rc0", True,
                        all(v[0] == 0 and "套话一" in v[1] for v in _rr_blex.values())
                        and len({_nl(v[1]) for v in _rr_blex.values()}) == 1))
    # ③ foreshadow 状态单带 BOM → 双引擎 rc0 且伏笔时间线产物逐字一致（py utf-8-sig，不再裸 Traceback）
    def _fs_bom_run(engine):
        _pj = _nhd / f"p_fsbom_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        _st = _pj / "记忆系统" / "_state.json"
        if not _st.is_file():
            return (9, "init 未生成 _state.json", "")
        _st.write_bytes(b"\xef\xbb\xbf" + _st.read_bytes())
        (_pj / "正文").mkdir(exist_ok=True)
        (_pj / "正文" / "第001章_试炼.txt").write_text("夜色里他许下伏笔，前文曾提过约定。", encoding="utf-8")
        if engine == "ps1":
            _cmd_fs = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(FS_PS1), "-Project", str(_pj)]
        else:
            _cmd_fs = [PY, str(FS_PY), "--project", str(_pj)]
        _pr_fs = subprocess.run(_cmd_fs, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _md = _pj / "记忆系统" / "圣经" / "伏笔时间线.md"
        return (_pr_fs.returncode, _decode(_pr_fs.stdout), (_md.read_text(encoding="utf-8") if _md.is_file() else ""))
    _rr_fsb = {e: _fs_bom_run(e) for e in ENGINES}
    results.append(case("[二十二轮] foreshadow 状态单带 BOM → 双引擎 rc0 且产物逐字一致（py 不再裸 Traceback）", True,
                        all(v[0] == 0 for v in _rr_fsb.values())
                        and len({v[2] for v in _rr_fsb.values()}) == 1 and _rr_fsb["py"][2] != ""))
    # ④ state 深层 delta（meta 15 层）→ 双引擎落盘深度一致不截断（ps1 -Depth 100）
    def _deep_run(engine):
        _pj = _nhd / f"p_deep_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        (_pj / "记忆系统").mkdir(parents=True, exist_ok=True)
        _meta = "x"
        for _i_d in range(15):
            _meta = {"n" + str(_i_d): 1} if isinstance(_meta, dict) and not _meta else {"n" + str(_i_d): _meta}
        (_pj / "正文").mkdir(exist_ok=True)
        (_pj / "正文" / "第001章_试炼.txt").write_text("夜色压着山脊。", encoding="utf-8")
        run_state(engine, "draft", "--project", str(_pj), "--chapter", "1")
        _txn = write_txn(_pj, 1, {"foreshadow": {"F1": {"title": "t", "status": "pending", "chapter": 1, "meta": _meta}}})
        rc_c, out_c = run_state(engine, "commit", "--project", str(_pj), "--chapter", "1", "--input", str(_txn))
        _st = json.loads((_pj / "记忆系统" / "_state.json").read_text(encoding="utf-8-sig"))
        _m = _st.get("foreshadow", {}).get("F1", {}).get("meta")
        _depth = 0
        while isinstance(_m, dict) and _m:
            _depth += 1
            _m = next(iter(_m.values()))
        return rc_c, _depth
    _rr_deep = {e: _deep_run(e) for e in ENGINES}
    results.append(case("[二十二轮] state 深层 delta（meta 15 层）→ 双引擎落盘深度=15 不截断 rc0", True,
                        all(v[0] == 0 and v[1] == 15 for v in _rr_deep.values())))
    # ⑤ 预算 baseline 浮点 1e30 → 双引擎「基线非法」且数字串同为 1E+30（G15 归一）
    _bd_e = Path(tempfile.mkdtemp(prefix="budget-1e30-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _bd_e / _f)
    (_bd_e / "A.md").write_text("一" * 100, encoding="utf-8")
    (_bd_e / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "1e30 展示用例", "layer": "protected"}],
        "paths": [{"label": "路径F", "files": "A.md"}],
        "generated": "G.md",
        "path_ceiling": {"limit": 100000, "why": "t"},
        "baseline": {"worst_path_chars": 1e30, "writer_delegated_chars": 100,
                     "session_resident_chars": 100, "precheck_path_chars": 100,
                     "max_single_file": "A.md=100", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    def _run_1e30(engine):
        if engine == "ps1":
            cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_bd_e / "check-doc-budget.ps1"), "-Root", str(_bd_e)]
        else:
            cmd = [PY, str(_bd_e / "check-doc-budget.py"), "--root", str(_bd_e)]
        proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return proc.returncode, _decode(proc.stdout)
    _r_e30 = {e: _run_1e30(e) for e in ENGINES}
    results.append(case("[二十二轮] baseline=1e30 → 双引擎「基线非法」且数字串同为 1E+30", True,
                        all(v[0] == 1 and "1E+30" in v[1] and "基线非法" in v[1] for v in _r_e30.values())
                        and len({_nl(v[1]) for v in _r_e30.values()}) == 1))
    # ⑥ 超 Int64 整数 limit → py/ps1 同判 rc1「须为非负整数」（py _in_i64 收口）
    (_bd_e / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "Int64 用例", "layer": "protected"}],
        "paths": [],
        "path_ceiling": {"limit": 10 ** 30, "exempt": [], "why": "t"},
        "baseline": {"worst_path_chars": 100, "writer_delegated_chars": 100,
                     "session_resident_chars": 100, "precheck_path_chars": 100,
                     "max_single_file": "A.md=100", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _r_i64 = {e: _run_1e30(e) for e in ENGINES}
    results.append(case("[二十二轮] limit=10^30 超 Int64 → 双引擎同判 rc1「path_ceiling.limit 须为非负整数」", True,
                        all(v[0] == 1 and "path_ceiling.limit 须为非负整数" in v[1] for v in _r_i64.values())
                        and len({_nl(v[1]) for v in _r_i64.values()}) == 1))


    # === 二十三轮修复回归组（BOM 状态单读入口、展示归一） ===
    # d35① _state.json 带 BOM → py draft/check 与 ps1 同判 rc0（load_json utf-8-sig）
    def _bomst_run(engine):
        _pj = _nhd / f"p_bomst_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        (_pj / "正文").mkdir(exist_ok=True)
        (_pj / "正文" / "第001章_试炼.txt").write_text("夜色压着山脊。", encoding="utf-8")
        _st = _pj / "记忆系统" / "_state.json"
        _st.write_bytes(b"\xef\xbb\xbf" + _st.read_bytes())
        _rc1, _o1 = run_state(engine, "draft", "--project", str(_pj), "--chapter", "1")
        _rc2, _o2 = run_state(engine, "check", "--project", str(_pj))
        return _rc1, _nl(_o1).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>"), _rc2, _nl(_o2).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_bs = {e: _bomst_run(e) for e in ENGINES}
    results.append(case("[二十三轮] _state.json 带 BOM → 双引擎 draft/check 同判 rc0（load_json utf-8-sig）", True,
                        _rr_bs["py"][0] == 0 and _rr_bs["py"][2] == 0
                        and len(set(_rr_bs.values())) == 1))
    # d35② 非法基线展示：Infinity / 超 Int64 整数 / 全角 max_single 数字段 → 双引擎同文案 rc1
    def _disp_pair(mutate):
        _d = Path(tempfile.mkdtemp(prefix="budget-disp-"))
        for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
            shutil.copy2(ROOT / "scripts" / _f, _d / _f)
        (_d / "A.md").write_text("一" * 100, encoding="utf-8")
        _b = {"files": [{"path": "A.md", "budget": 100000, "why": "展示归一用例", "layer": "protected"}],
              "paths": [], "path_ceiling": {"limit": 100000, "why": "t"},
              "baseline": {"worst_path_chars": 0, "writer_delegated_chars": 0,
                           "session_resident_chars": 0, "precheck_path_chars": 0,
                           "max_single_file": "A.md=100", "measured_on": "2026-10-18"},
              "context_layers": {"protected": "p", "compressible": "c", "why": "w"}}
        mutate(_b)
        (_d / "doc-budget.json").write_text(json.dumps(_b, ensure_ascii=False), encoding="utf-8")
        outs = {}
        for _e in ENGINES:
            if _e == "ps1":
                cmd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_d / "check-doc-budget.ps1"), "-Root", str(_d)]
            else:
                cmd = [PY, str(_d / "check-doc-budget.py"), "--root", str(_d)]
            pr = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            outs[_e] = (pr.returncode, _nl(_decode(pr.stdout)))
        return outs
    _disp_forms = (
        (lambda b: b["baseline"].update({"worst_path_chars": float("inf")}), "INFINITY"),
        (lambda b: b["baseline"].update({"worst_path_chars": 10 ** 30}), "1E+30"),
        (lambda b: b["baseline"].update({"max_single_file": "A.md=５０"}), "基线非法"),
    )
    for _di, (_mut, _ndisp) in enumerate(_disp_forms):
        _rr_disp = _disp_pair(_mut)
        results.append(case(f"[二十三轮] 展示归一{ _di + 1 }（{_ndisp}）→ 双引擎同文案 rc1", True,
                            all(v[0] == 1 and _ndisp in v[1] for v in _rr_disp.values())
                            and len({_v[1] for _v in _rr_disp.values()}) == 1))


    # === 二十四轮修复回归组（max_single 串形/三标量 Int64/null/text_guard 规则 BOM） ===
    # d36① max_single='A.md=+100' → 双引擎同判 rc1「基线非法」（ps1 串形收严对齐 py 正则）
    # 用共享目录（root 行一致）
    _dp = Path(tempfile.mkdtemp(prefix="budget-plus-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _dp / _f)
    (_dp / "A.md").write_text("一" * 100, encoding="utf-8")
    (_dp / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "二十四轮串形用例", "layer": "protected"}],
        "paths": [], "path_ceiling": {"limit": 100000, "why": "t"},
        "baseline": {"worst_path_chars": 0, "writer_delegated_chars": 0,
                     "session_resident_chars": 0, "precheck_path_chars": 0,
                     "max_single_file": "A.md=+100", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False), encoding="utf-8")
    _rr_plus = {}
    for _e_p in ENGINES:
        if _e_p == "ps1":
            _c_p = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_dp / "check-doc-budget.ps1"), "-Root", str(_dp)]
        else:
            _c_p = [PY, str(_dp / "check-doc-budget.py"), "--root", str(_dp)]
        _pr_p = subprocess.run(_c_p, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr_plus[_e_p] = (_pr_p.returncode, _nl(_decode(_pr_p.stdout)))
    results.append(case("[二十四轮] max_single=A.md=+100 → 双引擎同判 rc1「基线非法」（ps1 串形收严）", True,
                        all(v[0] == 1 and "基线非法" in v[1] and "max_single_file" in v[1] for v in _rr_plus.values())
                        and len({v[1] for v in _rr_plus.values()}) == 1))
    # d36② 三标量超 Int64（10^30）→ state check 双引擎 rc1 同文案 + check-done 闭环双引擎 rc1
    def _tri64(engine):
        _pj = _nhd / f"p_tri64_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        empty_done(_pj, 1, BASE + ["state"])
        write_body(_pj, 1, 10)
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["last_committed_chapter"] = 10 ** 30
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        _rc1, _o1 = run_state(engine, "check", "--project", str(_pj))
        if engine == "ps1":
            _cd = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "check-done.ps1"), "-Project", str(_pj), "-Chapter", "1", "-SoftReport"]
        else:
            _cd = [PY, str(ROOT / "scripts" / "check-done.py"), "--project", str(_pj), "--chapter", "1", "--soft-report"]
        _pr_cd = subprocess.run(_cd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return _rc1, _nl(_o1).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>"), _pr_cd.returncode, _nl(_pr_cd.stdout.decode("utf-8", "replace")).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_tri = {e: _tri64(e) for e in ENGINES}
    results.append(case("[二十四轮] last_committed_chapter=10^30 → 双引擎 state check rc1 同文案（Int64 上界）", True,
                        all(v[0] == 1 and "last_committed_chapter 必须是整数" in v[1] for v in _rr_tri.values())
                        and len({(v[0], v[1]) for v in _rr_tri.values()}) == 1))
    results.append(case("[二十四轮] last_committed_chapter=10^30 → check-done 闭环双引擎同判 rc1（py 不再 fail-open）", True,
                        all(v[2] == 1 and "last_committed_chapter 不是整数" in v[3] for v in _rr_tri.values())
                        and len({(v[2], v[3]) for v in _rr_tri.values()}) == 1))
    # d36③ state_revision 显式 null → 双引擎 check 同判 rc1（键存在即须整数，fail-closed）
    def _revnull(engine):
        _pj = _nhd / f"p_revnull_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["state_revision"] = None
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        _rc1, _o1 = run_state(engine, "check", "--project", str(_pj))
        return _rc1, _nl(_o1).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_rn = {e: _revnull(e) for e in ENGINES}
    results.append(case("[二十四轮] state_revision 显式 null → 双引擎 check 同判 rc1「必须是整数」", True,
                        all(v[0] == 1 and "state_revision 必须是整数" in v[1] for v in _rr_rn.values())
                        and len(set(_rr_rn.values())) == 1))
    # d36④ text_guard 规则单带 BOM → 双引擎 digest rc0（rules 读取 utf-8-sig）
    # 共享临时目录：拷贝双引擎脚本 + 带 BOM 的规则单（RULES_PATH 固定为脚本目录）
    _tgd = _nhd / "p_tgbom_shared"
    _tgd.mkdir(parents=True, exist_ok=True)
    for _f_tg in ("text_guard.py", "text_guard.ps1"):
        shutil.copy2(ROOT / "scripts" / _f_tg, _tgd / _f_tg)
    (_tgd / "check-done-rules.json").write_bytes(b"\xef\xbb\xbf" + (ROOT / "scripts" / "check-done-rules.json").read_bytes())
    _rr_tgb = {}
    for _e_tg in ENGINES:
        _pj_tg = _tgd / "proj"
        (_pj_tg / "正文").mkdir(parents=True, exist_ok=True)
        (_pj_tg / "正文" / "第001章_试炼.txt").write_text("夜色压着山脊。老陈把烟掐了。\n", encoding="utf-8")
        if _e_tg == "ps1":
            _c_tg = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_tgd / "text_guard.ps1"), "digest", "-Project", str(_pj_tg), "-Chapter", "1"]
        else:
            _c_tg = [PY, str(_tgd / "text_guard.py"), "digest", "--project", str(_pj_tg), "--chapter", "1"]
        _pr_tg = subprocess.run(_c_tg, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _led_ok = (_pj_tg / ".story" / "text-digests.json").is_file()
        _rr_tgb[_e_tg] = (_pr_tg.returncode, _nl((_pr_tg.stdout + _pr_tg.stderr).decode("utf-8", "replace")).replace(str(_tgd), "<TG>").replace(_pnorm(str(_tgd)), "<TG>"), _led_ok)
    results.append(case("[二十四轮] text_guard 规则单带 BOM → 双引擎 digest rc0 且账本真实落盘同文案（rules 读取 utf-8-sig）", True,
                        all(v[0] == 0 and v[2] for v in _rr_tgb.values())
                        and len(set(_rr_tgb.values())) == 1))


    # === 二十五轮修复回归组（max_single 尾 \n 串形、Int64 上界自增） ===
    # d37① max_single="A.md=100\n" → 双引擎同判 rc1（ps1 \A\z 收严，不被 $ 尾换行放行）
    _dn = Path(tempfile.mkdtemp(prefix="budget-nl-"))
    for _f in ("check-doc-budget.py", "check-doc-budget.ps1", "doc-budget.json"):
        shutil.copy2(ROOT / "scripts" / _f, _dn / _f)
    (_dn / "A.md").write_text("一" * 100, encoding="utf-8")
    (_dn / "doc-budget.json").write_text(json.dumps({
        "files": [{"path": "A.md", "budget": 100000, "why": "二十五轮串形用例", "layer": "protected"}],
        "paths": [], "path_ceiling": {"limit": 100000, "why": "t"},
        "baseline": {"worst_path_chars": 0, "writer_delegated_chars": 0,
                     "session_resident_chars": 0, "precheck_path_chars": 0,
                     "max_single_file": "A.md=100\n", "measured_on": "2026-10-18"},
        "context_layers": {"protected": "p", "compressible": "c", "why": "w"}
    }, ensure_ascii=False), encoding="utf-8")
    _rr_nl = {}
    for _e_nl in ENGINES:
        if _e_nl == "ps1":
            _c_nl = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_dn / "check-doc-budget.ps1"), "-Root", str(_dn)]
        else:
            _c_nl = [PY, str(_dn / "check-doc-budget.py"), "--root", str(_dn)]
        _pr_nl = subprocess.run(_c_nl, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr_nl[_e_nl] = (_pr_nl.returncode, _nl(_decode(_pr_nl.stdout)))
    results.append(case("[二十五轮] max_single=A.md=100+尾换行 → 双引擎同判 rc1「基线非法」（ps1 \\A\\z 收严）", True,
                        all(v[0] == 1 and "基线非法" in v[1] and "max_single_file" in v[1] for v in _rr_nl.values())
                        and len({v[1] for v in _rr_nl.values()}) == 1))
    # d37② state_revision=Int64 上界 → 双引擎 commit 同判 rc1「自增会溢出」（拒绝提交，状态不变）
    def _i64top(engine):
        _pj = _nhd / f"p_i64top_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        (_pj / "正文").mkdir(exist_ok=True)
        (_pj / "正文" / "第001章_试炼.txt").write_text("夜色压着山脊。", encoding="utf-8")
        run_state(engine, "draft", "--project", str(_pj), "--chapter", "1")
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["state_revision"] = 2 ** 63 - 1
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        _txn = write_txn(_pj, 1, {"characters": {"甲": {"snapshot": "s"}}})
        _rc1, _o1 = run_state(engine, "commit", "--project", str(_pj), "--chapter", "1", "--input", str(_txn))
        return _rc1, _nl(_o1).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_top = {e: _i64top(e) for e in ENGINES}
    results.append(case("[二十五轮] state_revision=Int64 上界 → 双引擎 commit 同判 rc1「自增会溢出」（拒绝提交）", True,
                        all(v[0] == 1 and "自增会溢出" in v[1] for v in _rr_top.values())
                        and len(set(_rr_top.values())) == 1))


    # === 二十七轮修复回归组（非 BMP 同头/浮点章号闸/非 UTF-8 回退/非对象值；二十八轮随批入套） ===
    # d38① detect [B] 同头连句以 emoji 起首 → 双引擎同判同文案（UTF-16 码元前 2 口径）
    _ab = _nhd / "astral_b.txt"
    _ab.write_text("😀哈喽。😀世界。😀平安。", encoding="utf-8")
    _rr_ab = {}
    for _e_ab in ENGINES:
        if _e_ab == "py":
            _c_ab = [PY, str(ROOT / "scripts" / "detect_ai_text.py"), "--input", str(_ab), "--fail-over", "1"]
        else:
            _c_ab = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "detect_ai_text.ps1"), "-InputFile", str(_ab), "-FailOver", "1"]
        _pr_ab = subprocess.run(_c_ab, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_ab[_e_ab] = (_pr_ab.returncode, _nl(_decode(_pr_ab.stdout)))
    results.append(case("[二十七轮] detect 同头连句 emoji 起首 → 双引擎同判同文案（UTF-16 码元口径，--fail-over 1）", True,
                        len(set(_rr_ab.values())) == 1))
    # d38② foreshadow.planted_ch=3.5 → state check 双引擎 rc1「必须是整数」（闸拦浮点章号）
    def _ffloat(engine):
        _pj = _nhd / f"p_ffloat_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["foreshadow"] = {"F1": {"desc": "玉佩", "planted_ch": 3.5}}
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        _rc1, _o1 = run_state(engine, "check", "--project", str(_pj))
        return _rc1, _nl(_o1).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_ff = {e: _ffloat(e) for e in ENGINES}
    results.append(case("[二十七轮] foreshadow.planted_ch=3.5 → 双引擎 state check 同判 rc1「必须是非负整数」", True,
                        all(v[0] == 1 and "planted_ch 必须是非负整数" in v[1] for v in _rr_ff.values())
                        and len(set(_rr_ff.values())) == 1))
    # d38③ 非 UTF-8（GBK）正文 → text_guard digest / fingerprint 双引擎 rc0 同口径（替换回退）
    _gb = _nhd / "gbk_body.txt"
    _gb.write_bytes("夜色压着山脊。".encode("gbk"))
    _rr_gk = {}
    for _e_gk in ENGINES:
        if _e_gk == "py":
            _p_g = [PY, str(ROOT / "scripts" / "fingerprint.py"), str(_gb)]
            _t_g = [PY, str(ROOT / "scripts" / "detect_ai_text.py"), "--input", str(_gb)]
        else:
            _p_g = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "fingerprint.ps1"), "-Path", str(_gb), "-Json"]
            _t_g = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "detect_ai_text.ps1"), "-InputFile", str(_gb)]
        _pr_p = subprocess.run(_p_g, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _pr_t = subprocess.run(_t_g, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_gk[_e_gk] = (_pr_p.returncode, _pr_t.returncode)
    results.append(case("[二十七轮] GBK 正文 → fingerprint/detect 双引擎同判 rc0/rc0（errors=replace 回退，不裸崩）", True,
                        len(set(_rr_gk.values())) == 1 and _rr_gk["py"] == (0, 0)))
    # d38④ foreshadow.F1 非对象（绕闸独立运行）→ 双引擎 rc1 同文案「须为对象」
    def _fobj(engine):
        _pj = _nhd / f"p_fobj_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["foreshadow"] = {"F1": "串"}
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        if engine == "ps1":
            _c_fo = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(FS_PS1), "-Project", str(_pj)]
        else:
            _c_fo = [PY, str(FS_PY), "--project", str(_pj)]
        _pr_fo = subprocess.run(_c_fo, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return _pr_fo.returncode, _nl(_pr_fo.stdout.decode("utf-8", "replace")).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_fo = {e: _fobj(e) for e in ENGINES}
    results.append(case("[二十七轮] foreshadow.F1 非对象（独立运行）→ 双引擎 rc1 同文案「须为对象」", True,
                        all(v[0] == 1 and "须为对象" in v[1] for v in _rr_fo.values())
                        and len(set(_rr_fo.values())) == 1))


    # === 二十八轮修复回归组（混合前缀同头/负数章号闸/foreshadow null 数据源/空句长表） ===
    # d39① detect [B] 混合前缀「汉字+emoji」→ 双引擎同判同文案（悬空高位代理保留，surrogatepass）
    _mx1 = _nhd / "mix_prefix.txt"
    _mx1.write_text("我😀甲。我😀乙。我😀丙。", encoding="utf-8")
    _mx2 = _nhd / "mix_diff.txt"
    _mx2.write_text("我😀甲。我🎉乙。我😀丙。", encoding="utf-8")
    _rr_mx = {}
    for _e_mx in ENGINES:
        outs = []
        for _f_mx in (_mx1, _mx2):
            if _e_mx == "py":
                _c_mx = [PY, str(ROOT / "scripts" / "detect_ai_text.py"), "--input", str(_f_mx), "--fail-over", "1"]
            else:
                _c_mx = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "detect_ai_text.ps1"), "-InputFile", str(_f_mx), "-FailOver", "1"]
            _pr_mx = subprocess.run(_c_mx, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
            outs.append((_pr_mx.returncode, _nl(_decode(_pr_mx.stdout))))
        _rr_mx[_e_mx] = tuple(outs)
    results.append(case("[二十八轮] detect 同头混合前缀（汉字+emoji，含不同高位代理）→ 双引擎双夹具同判同文案", True,
                        len(set(_rr_mx.values())) == 1))
    # d39② resolved_ch=-1 → state check 双引擎 rc1「必须是非负整数」
    def _fneg(engine):
        _pj = _nhd / f"p_fneg_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["foreshadow"] = {"F1": {"desc": "玉佩", "planted_ch": 1, "resolved_ch": -1}}
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        _rc1, _o1 = run_state(engine, "check", "--project", str(_pj))
        return _rc1, _nl(_o1).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_fn = {e: _fneg(e) for e in ENGINES}
    results.append(case("[二十八轮] resolved_ch=-1 → 双引擎 state check 同判 rc1「必须是非负整数」", True,
                        all(v[0] == 1 and "必须是非负整数" in v[1] for v in _rr_fn.values())
                        and len(set(_rr_fn.values())) == 1))
    # d39③ foreshadow=null + 非空伏笔清单 → 双引擎同走清单路径（源=清单、条目一致）
    def _fnull(engine):
        _pj = _nhd / f"p_fnull_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["foreshadow"] = None
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        (_pj / "记忆系统" / "圣经").mkdir(parents=True, exist_ok=True)
        (_pj / "记忆系统" / "圣经" / "伏笔清单.md").write_text(
            "| # | 伏笔线索 | 首现 | 状态 | 预期回收 |\n|---|---|---|---|---|\n| F1 | 玉佩之谜 | 第1章 | 🔘 已埋设 | 第5章 |\n", encoding="utf-8")
        if engine == "ps1":
            _c_fn = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(FS_PS1), "-Project", str(_pj)]
        else:
            _c_fn = [PY, str(FS_PY), "--project", str(_pj)]
        _pr_fn = subprocess.run(_c_fn, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _md = _pj / "记忆系统" / "圣经" / "伏笔时间线.md"
        return _pr_fn.returncode, _nl(_pr_fn.stdout.decode("utf-8", "replace")).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>"), (_md.read_text(encoding="utf-8") if _md.is_file() else "")
    _rr_fnn = {e: _fnull(e) for e in ENGINES}
    results.append(case("[二十八轮] foreshadow=null + 非空伏笔清单 → 双引擎同走清单路径 rc0 产物一致", True,
                        all(v[0] == 0 for v in _rr_fnn.values())
                        and len(set(_rr_fnn.values())) == 1 and "玉佩" in _rr_fnn["py"][2]))
    # d39④ 全句 ≥300 单元 → fingerprint 双引擎 rc1 中文「无可统计句子」（不裸崩）
    _long = _nhd / "long_sent.txt"
    _long.write_text("这是一段很长的句子" * 60 + "。", encoding="utf-8")
    _rr_ls = {}
    for _e_ls in ENGINES:
        if _e_ls == "py":
            _c_ls = [PY, str(ROOT / "scripts" / "fingerprint.py"), str(_long)]
        else:
            _c_ls = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "fingerprint.ps1"), "-Path", str(_long), "-Json"]
        _pr_ls = subprocess.run(_c_ls, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_ls[_e_ls] = (_pr_ls.returncode, _nl(_decode(_pr_ls.stdout) + _decode(_pr_ls.stderr)))
    results.append(case("[二十八轮] 全句≥300 单元 → fingerprint 双引擎 rc1 中文「无可统计句子」（不裸崩）", True,
                        all(v[0] == 1 and "无可统计句子" in v[1] for v in _rr_ls.values())
                        and len(set(_rr_ls.values())) == 1))
    # d39⑤ 独立运行 planted_ch="3.5" 字符串 → 双引擎截断为 3 同文案 rc0（py float 强转对齐 ps1 Truncate）
    def _fstr(engine):
        _pj = _nhd / f"p_fstr_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["foreshadow"] = {"F1": {"desc": "玉佩", "planted_ch": "3.5", "planned_resolve_ch": 5}}
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        if engine == "ps1":
            _c_fs2 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(FS_PS1), "-Project", str(_pj)]
        else:
            _c_fs2 = [PY, str(FS_PY), "--project", str(_pj)]
        _pr_fs2 = subprocess.run(_c_fs2, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _md = _pj / "记忆系统" / "圣经" / "伏笔时间线.md"
        return _pr_fs2.returncode, _nl(_pr_fs2.stdout.decode("utf-8", "replace")).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>"), (_md.read_text(encoding="utf-8") if _md.is_file() else "")
    _rr_fs2 = {e: _fstr(e) for e in ENGINES}
    results.append(case("[二十八轮] 独立运行 planted_ch=\"3.5\" → 双引擎截断 3 同文案 rc0 产物一致", True,
                        all(v[0] == 0 for v in _rr_fs2.values())
                        and len(set(_rr_fs2.values())) == 1 and "第3章" in _rr_fs2["py"][2]))


    # === 二十九轮修复回归组（foreshadow 边界归一/init 契约/空键/纯标点指纹） ===
    # d40① foreshadow 边界四态（planned=-5 / planted="" / desc=null / 独立运行）→ 双引擎产物逐字一致
    def _fedge(engine):
        _pj = _nhd / f"p_fedge_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        (_pj / "正文").mkdir(exist_ok=True)
        (_pj / "正文" / "第010章_试炼.txt").write_text("夜色压着山脊。", encoding="utf-8")
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["foreshadow"] = {"F1": {"desc": None, "planted_ch": "", "planned_resolve_ch": -5}}
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        if engine == "ps1":
            _c_fe = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(FS_PS1), "-Project", str(_pj)]
        else:
            _c_fe = [PY, str(FS_PY), "--project", str(_pj)]
        _pr_fe = subprocess.run(_c_fe, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _md = _pj / "记忆系统" / "圣经" / "伏笔时间线.md"
        return _pr_fe.returncode, _nl(_pr_fe.stdout.decode("utf-8", "replace")).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>"), (_md.read_text(encoding="utf-8") if _md.is_file() else "")
    _rr_fe = {e: _fedge(e) for e in ENGINES}
    results.append(case("[二十九轮] foreshadow 边界（desc=null/planted=\"\"/planned=-5）→ 双引擎 rc0 产物逐字一致", True,
                        all(v[0] == 0 for v in _rr_fe.values())
                        and len(set(_rr_fe.values())) == 1))
    # d40② --current-ch 负数 → 双引擎视为未传（当前章=探测值）同文案
    def _fnegcur(engine):
        _pj = _nhd / f"p_fnc_{engine}"
        _pj.mkdir(parents=True)
        run_state(engine, "init", "--project", str(_pj))
        (_pj / "正文").mkdir(exist_ok=True)
        (_pj / "正文" / "第010章_试炼.txt").write_text("夜色压着山脊。", encoding="utf-8")
        _stf = _pj / "记忆系统" / "_state.json"
        _sj = json.loads(_stf.read_text(encoding="utf-8-sig"))
        _sj["foreshadow"] = {"F1": {"desc": "玉佩", "planted_ch": 1, "planned_resolve_ch": 8}}
        _stf.write_text(json.dumps(_sj, ensure_ascii=False, indent=2), encoding="utf-8")
        if engine == "ps1":
            _c_fc = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(FS_PS1), "-Project", str(_pj), "-CurrentCh", "-3"]
        else:
            _c_fc = [PY, str(FS_PY), "--project", str(_pj), "--current-ch", "-3"]
        _pr_fc = subprocess.run(_c_fc, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return _pr_fc.returncode, _nl(_pr_fc.stdout.decode("utf-8", "replace")).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_fc = {e: _fnegcur(e) for e in ENGINES}
    results.append(case("[二十九轮] --current-ch=-3 → 双引擎视为未传同文案（当前章=探测值）", True,
                        len(set(_rr_fc.values())) == 1 and "第10章" in _rr_fc["py"][1]))
    # d40③ init facts 非数组 → 双引擎 rc1「facts 须为数组」
    _iarr_seq = {"n": 0}

    def _iarr(engine, payload):
        _iarr_seq["n"] += 1
        _pj = _nhd / f"p_iarr_{engine}_{_iarr_seq['n']}"
        _pj.mkdir(parents=True)
        _inp = _pj / "in.json"
        _inp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        if engine == "ps1":
            _c_ia = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "state_commit.ps1"), "init", "-Project", str(_pj), "-InputFile", str(_inp)]
        else:
            _c_ia = [PY, str(ROOT / "scripts" / "state_commit.py"), "init", "--project", str(_pj), "--input", str(_inp)]
        _pr_ia = subprocess.run(_c_ia, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return _pr_ia.returncode, _nl(_pr_ia.stdout.decode("utf-8", "replace")).replace(str(_pj), "<PROJ>").replace(_pnorm(str(_pj)), "<PROJ>")
    _rr_fa = {e: _iarr(e, {"facts": "notalist"}) for e in ENGINES}
    results.append(case("[二十九轮] init facts=字符串 → 双引擎 rc1「facts 须为数组」（契约统一）", True,
                        all(v[0] == 1 and "facts 须为数组" in v[1] for v in _rr_fa.values())
                        and len(set(_rr_fa.values())) == 1))
    _rr_ov = {e: _iarr(e, {"overview": "notanobject"}) for e in ENGINES}
    results.append(case("[二十九轮] init overview=字符串 → 双引擎 rc1「overview 须为对象」（无键污染）", True,
                        all(v[0] == 1 and "overview 须为对象" in v[1] for v in _rr_ov.values())
                        and len(set(_rr_ov.values())) == 1))
    # d40④ 空键名 characters → 双引擎 rc1「键名不能为空」
    _rr_ek = {e: _iarr(e, {"characters": {"": {"snapshot": "s"}}}) for e in ENGINES}
    results.append(case("[二十九轮] characters 空键名 → 双引擎 rc1 且各自中文点名（py 键名不能为空 / ps1 输入 JSON 空键名）", True,
                        _rr_ek["py"][0] == 1 and "键名不能为空" in _rr_ek["py"][1]
                        and _rr_ek["ps1"][0] == 1 and "空键名" in _rr_ek["ps1"][1]))
    # d40⑤ 纯标点正文（逗号句）→ fingerprint 双引擎 rc0 且逗号均值=0（不裸崩）
    _pt = _nhd / "punct.txt"
    _pt.write_text("，，，。", encoding="utf-8")
    _rr_pt = {}
    for _e_pt in ENGINES:
        if _e_pt == "py":
            _c_pt = [PY, str(ROOT / "scripts" / "fingerprint.py"), str(_pt)]
        else:
            _c_pt = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "fingerprint.ps1"), "-Path", str(_pt), "-Json"]
        _pr_pt = subprocess.run(_c_pt, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_pt[_e_pt] = (_pr_pt.returncode, _pr_pt.stdout.decode("utf-8", "replace"))
    _pt_norm = []
    for _v_pt in _rr_pt.values():
        try:
            _pt_norm.append({k: (float(x) if isinstance(x, (int, float)) else x) for k, x in json.loads(_v_pt[1]).items()})
        except Exception:
            _pt_norm.append(None)
    results.append(case("[二十九轮] 纯标点正文 → fingerprint 双引擎 rc0 解析值全等且逗号均值=0（不裸崩；JSON 格式差归一）", True,
                        all(v[0] == 0 for v in _rr_pt.values())
                        and None not in _pt_norm and _pt_norm[0] == _pt_norm[1]
                        and _pt_norm[0].get("逗号间均值") == 0.0))


    # === 三十一轮修复回归组（Container 闸/参数错误收口/CmdletBinding/缺文件） ===
    # d41① state_commit -Project 指向普通文件 → 双引擎 rc1 同文案「项目目录不存在」（Container 判定）
    _pf = _nhd / "afile.txt"
    _pf.write_text("x", encoding="utf-8")
    _rr_pf = {}
    for _e_pf in ENGINES:
        if _e_pf == "py":
            _c_pf = [PY, str(ROOT / "scripts" / "state_commit.py"), "init", "--project", str(_pf)]
        else:
            _c_pf = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "state_commit.ps1"), "init", "-Project", str(_pf)]
        _pr_pf = subprocess.run(_c_pf, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr_pf[_e_pf] = (_pr_pf.returncode, _nl(_decode(_pr_pf.stdout)).replace(str(_pf), "<PROJ>").replace(_pnorm(str(_pf)), "<PROJ>"))
    results.append(case("[三十一轮] state_commit -Project 普通文件 → 双引擎 rc1 同文案「项目目录不存在」（Container 闸）", True,
                        all(v[0] == 1 and "项目目录不存在" in v[1] for v in _rr_pf.values())
                        and len(set(_rr_pf.values())) == 1))
    # d41② 四脚本未知参数 → py rc1 中文「参数错误」（对齐 ps1 rc1，不再 rc2 撞业务码）
    _rr_ap = {}
    for _mod in ("state_commit.py", "text_guard.py", "foreshadow_timeline.py", "package_skill.py"):
        _pr_ap = subprocess.run([PY, str(ROOT / "scripts" / _mod), "--definitely-not-a-flag"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_ap[_mod] = (_pr_ap.returncode, _nl(_decode(_pr_ap.stdout) + _decode(_pr_ap.stderr)))
    results.append(case("[三十一轮] 四脚本未知参数 → py 均 rc1 中文「参数错误」（不再 rc2 与业务 rc2 撞码）", True,
                        all(v[0] == 1 and "参数错误" in v[1] for v in _rr_ap.values())))
    # d41③ audit.ps1 未知旗标 → 双引擎 rc1（CmdletBinding，不再静默吞进 $args fail-open）
    _c_au = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / ".github/scripts" / "audit.ps1"), "-Root", str(ROOT), "-NoSuchParam"]
    _pr_au = subprocess.run(_c_au, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    results.append(case("[三十一轮] audit.ps1 未知旗标 → rc1（CmdletBinding 防静默吞参 fail-open）", True,
                        _pr_au.returncode == 1))
    # d41④ fingerprint 缺文件 / detect 目录输入 → 双引擎 rc1 同文案（中文，不裸崩）
    _miss = _nhd / "no_such_file.txt"
    _rr_mf = {}
    for _e_mf in ENGINES:
        if _e_mf == "py":
            _c_mf = [PY, str(ROOT / "scripts" / "fingerprint.py"), str(_miss)]
        else:
            _c_mf = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "fingerprint.ps1"), "-Path", str(_miss), "-Json"]
        _pr_mf = subprocess.run(_c_mf, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr_mf[_e_mf] = (_pr_mf.returncode, _nl(_decode(_pr_mf.stdout) + _decode(_pr_mf.stderr)))
    results.append(case("[三十一轮] fingerprint 缺文件 → 双引擎 rc1 各自中文「文件不存在」（py 不再裸 traceback）", True,
                        all(v[0] == 1 and "文件不存在" in v[1] for v in _rr_mf.values())))


    # === 三十一轮修复回归组·续（audit.ps1 变异检出钉死） ===
    # d42① 顺序闸：围栏外错序句（含「无论」前缀与「后」变体）→ rc1 且点名「顺序一致性」
    _ord_forms = ("流程要求：无论 PASS/FAIL，先修复再朱雀，然后再跑 4.1b–4.1e。",
                  "流程要求：先修复后朱雀，然后再跑 4.1b–4.1e。")
    for _oi, _op in enumerate(_ord_forms):
        _od = Path(tempfile.mkdtemp(prefix="audit-ord-"))
        shutil.copytree(ROOT, _od / "r", ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", ".suite-tmp"))
        _orf = _od / "r" / "modules" / "03_26_功能模块.md"
        _orf.write_text(_orf.read_text(encoding="utf-8") + f"\n\n{_op}\n", encoding="utf-8")
        _bo = subprocess.run([PY, str(_od / "r" / "scripts" / "check-doc-budget.py"), "--print-baseline", "--root", str(_od / "r")], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _bt = _decode(_bo.stdout)
        _bjf = _od / "r" / "scripts" / "doc-budget.json"
        _bcfg = json.loads(_bjf.read_text(encoding="utf-8-sig"))
        _bcfg["baseline"]["worst_path_chars"] = int(re.search(r"最坏路径 ([\d,]+)", _bt).group(1).replace(",", ""))
        for _bk, _bl in (("writer_delegated_chars", "写手委派"), ("session_resident_chars", "主会话常驻"), ("precheck_path_chars", "写前预检")):
            _bm = re.search(_bl + r"[^\d]*([\d,]+)", _bt)
            if _bm:
                _bcfg["baseline"][_bk] = int(_bm.group(1).replace(",", ""))
        _msm = re.search(r"(modules/03_26_功能模块\.md)\s+([\d,]+)/", _bt)
        _bcfg["baseline"]["max_single_file"] = f"{_msm.group(1)}={int(_msm.group(2).replace(',', ''))}"
        _bjf.write_text(json.dumps(_bcfg, ensure_ascii=False, indent=2), encoding="utf-8")
        _pr_ord = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_od / "r" / ".github/scripts" / "audit.ps1"), "-Root", str(_od / "r")], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        results.append(case(f"[三十一轮] audit 顺序闸变异{_oi + 1}（错序句{_op[:10]}…）→ rc1 且点名「顺序一致性」", True,
                            _pr_ord.returncode == 1 and "顺序一致性" in _decode(_pr_ord.stdout)))
        shutil.rmtree(_od, ignore_errors=True)
    # d42② 孤儿文件变异 → -StrictOrphan rc1 点名；版本漂移变异 → rc1 点名
    def _audit_mut(name, mutate, needle):
        _md2 = Path(tempfile.mkdtemp(prefix=f"audit-{name}-"))
        shutil.copytree(ROOT, _md2 / "r", ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", ".suite-tmp"))
        mutate(_md2 / "r")
        _pr2 = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_md2 / "r" / ".github/scripts" / "audit.ps1"), "-Root", str(_md2 / "r"), "-StrictOrphan"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _out2 = _decode(_pr2.stdout)
        shutil.rmtree(_md2, ignore_errors=True)
        return _pr2.returncode == 1 and needle in _out2
    results.append(case("[三十一轮] audit 孤儿变异（templates 游离件）→ -StrictOrphan rc1 点名「孤儿文件」", True,
                        _audit_mut("orphan", lambda r: (r / "templates" / "zz_orphan_probe.md").write_text("x", encoding="utf-8"), "孤儿文件")))
    def _ver_mut(r):
        _sk = r / "SKILL.md"
        _cur = re.search(r"version:\s*([\d.]+)", (ROOT / "SKILL.md").read_text(encoding="utf-8")).group(1)  # 动态取当前版本（升版后不脱靶）
        _sk.write_text(_sk.read_text(encoding="utf-8").replace(f"version: {_cur}", "version: 9.9.9"), encoding="utf-8")
    results.append(case("[三十一轮] audit 版本漂移变异（SKILL 9.9.9）→ rc1 点名版本", True,
                        _audit_mut("ver", _ver_mut, "9.9.9")))


    # === 三十二轮修复回归组（perl 降级路径空集/档位豁免/foreshadow 顶层数值/check-done 归因顺序） ===
    # d43① fingerprint.byte.pl 空白-only → rc1 中文（除零护栏，不再 rc255 英文）
    _d43 = _nhd / "d43"
    _d43.mkdir(exist_ok=True)
    (_d43 / "blank.txt").write_text("   \n\t\n", encoding="utf-8")
    (_d43 / "long.txt").write_text("这是一句超过三百字的句子。" * 25, encoding="utf-8")
    (_d43 / "comma.txt").write_text("，，，，，", encoding="utf-8")
    (_d43 / "one.txt").write_text("标题行\n第一段正文，有一些内容。\n", encoding="utf-8")
    (_d43 / "title_only.txt").write_text("标题行\n", encoding="utf-8")  # 跳行后为空的形态
    _pr_b1 = subprocess.run(["perl", str(ROOT / "scripts" / "fingerprint.byte.pl"), str(_d43 / "blank.txt")], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _pr_b2 = subprocess.run(["perl", str(ROOT / "scripts" / "fingerprint.byte.pl"), str(_d43 / "long.txt")], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _pr_b3 = subprocess.run(["perl", str(ROOT / "scripts" / "fingerprint.byte.pl"), str(_d43 / "one.txt"), "--skip-first"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    results.append(case("[三十二轮] byte.pl 三空集+跳行后空 → rc1 中文或 rc0（不再除零 rc255）", True,
                        _pr_b1.returncode == 1 and "无可统计段落" in _decode(_pr_b1.stdout)
                        and _pr_b2.returncode in (0, 1) and _pr_b3.returncode in (0, 1)))
    # d43② wordfreq.byte.pl：单行 --skip-first rc1（同文案不再 rc255）+ 正常类超高频不打 ❌（cap=99 豁免）
    (_d43 / "sudd.txt").write_text("突然" * 200, encoding="utf-8")
    _pr_w1 = subprocess.run(["perl", str(ROOT / "scripts" / "wordfreq.byte.pl"), str(_d43 / "title_only.txt"), "--skip-first"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _pr_w2 = subprocess.run(["perl", str(ROOT / "scripts" / "wordfreq.byte.pl"), str(_d43 / "sudd.txt")], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    results.append(case("[三十二轮] wordfreq 跳行后空 rc1 + 正常类「突然」超高频不打❌（03_26 正常类不检查）", True,
                        _pr_w1.returncode == 1
                        and _pr_w2.returncode == 0 and "❌" not in _decode(_pr_w2.stdout)))
    # d43③ foreshadow 顶层数值/数组/截断 JSON → 双引擎 rc1 同文案
    for _si, (_sname, _sbody) in enumerate((("fs_int", '{"foreshadow": 42}'), ("fs_arr", "[1,2,3]"), ("fs_bad", '{"foreshadow": {"a"'))):
        _pj43 = _nhd / f"d43_{_sname}"
        (_pj43 / "记忆系统").mkdir(parents=True, exist_ok=True)
        (_pj43 / "记忆系统" / "_state.json").write_text(_sbody, encoding="utf-8")
        _rr43 = {}
        for _e43 in ENGINES:
            if _e43 == "py":
                _c43 = [PY, str(ROOT / "scripts" / "foreshadow_timeline.py"), "--project", str(_pj43)]
            else:
                _c43 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "foreshadow_timeline.ps1"), "-Project", str(_pj43)]
            _pr43 = subprocess.run(_c43, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _rr43[_e43] = (_pr43.returncode, _nl(_decode(_pr43.stdout)).strip())
        results.append(case(f"[三十二轮] foreshadow 顶层{_sname} → 双引擎 rc1 同文案（顶层数值判型/损坏 rc1，不再 ps1 fail-open）", True,
                            all(v[0] == 1 and "❌" in v[1] for v in _rr43.values())
                            and len(set(_rr43.values())) == 1))
    # d43④ check-done 复合故障（项目不存在+规则损坏副本仓）→ 双引擎首条归因「项目目录不存在」
    _repo43 = _nhd / "d43repo"
    shutil.copytree(ROOT, _repo43, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", ".suite-tmp"))
    (_repo43 / ".github" / "scripts" / "check-done-rules.json").write_text("{broken", encoding="utf-8")
    _rr44 = {}
    for _e44 in ENGINES:
        if _e44 == "py":
            _c44 = [PY, str(_repo43 / "scripts" / "check-done.py"), "--project", str(_repo43 / "nope"), "--chapter", "1"]
        else:
            _c44 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_repo43 / "scripts" / "check-done.ps1"), "-Project", str(_repo43 / "nope"), "-Chapter", "1"]
        _pr44 = subprocess.run(_c44, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr44[_e44] = (_pr44.returncode, [l for l in _decode(_pr44.stdout).splitlines() if "❌" in l][:1])
    shutil.rmtree(_repo43, ignore_errors=True)
    results.append(case("[三十二轮] check-done 复合故障（项目缺失+规则损坏）→ 双引擎首条归因「项目目录不存在」（惰性规则加载）", True,
                        all(v[0] == 1 and v[1] and "项目目录不存在" in v[1][0] for v in _rr44.values())))


    # === 三十三轮修复回归组（目录冒充正文/Int64 收口/单源损坏中文/参数面收口/input 归因） ===
    # d44① 正文 .txt 为目录 → 双引擎 rc1 同文案（不再 ps1 fail-open rc0）
    _d44 = _nhd / "d44"
    _pj44 = _d44 / "pj"
    (_pj44 / ".done").mkdir(parents=True, exist_ok=True)
    (_pj44 / "正文").mkdir(exist_ok=True)
    (_pj44 / "报告").mkdir(exist_ok=True)
    (_pj44 / "正文" / "第001章_测试.txt").mkdir(exist_ok=True)
    import json as _json44
    _rules44 = _json44.loads((ROOT / "scripts" / "check-done-rules.json").read_text(encoding="utf-8-sig"))
    for _mk44 in _rules44["base_marks"]:
        (_pj44 / ".done" / f"第001章_{_mk44}.done").write_bytes(b"")
    (_pj44 / ".done" / "第001章_chapter.done").write_bytes(b"")
    (_pj44 / "报告" / "第001章_全量审核报告.md").write_text(
        "## 人工证据包\n人工段注入：第3段，≥300字，合计1200字。结构破坏。段落位置：第3段。\n", encoding="utf-8")
    _rr44a = {}
    for _e44a in ENGINES:
        if _e44a == "py":
            _c44a = [PY, str(ROOT / "scripts" / "check-done.py"), "--project", str(_pj44), "--chapter", "1"]
        else:
            _c44a = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "check-done.ps1"), "-Project", str(_pj44), "-Chapter", "1"]
        _pr44a = subprocess.run(_c44a, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr44a[_e44a] = (_pr44a.returncode, _nl(_decode(_pr44a.stdout)).strip())
    results.append(case("[三十三轮] 正文 .txt 为目录 → 双引擎 rc1 同文案「正文候选路径是目录」（ps1 不再 fail-open）", True,
                        all(v[0] == 1 and "正文候选路径是目录" in v[1] for v in _rr44a.values())
                        and len(set(_rr44a.values())) == 1))
    # d44② 五脚本整数参数超 Int64/非十进制 → py rc1 中文「参数错误」（对齐 ps1 绑定失败 rc1）
    _cases44 = (("detect_ai_text.py", "--fail-over 1e20 -i nope.txt"),
                ("text_guard.py", "verify -c 1e20 --project nope"),
                ("foreshadow_timeline.py", "--current-ch 1e20 --project nope"),
                ("state_commit.py", "draft --chapter 1e20 --project nope"),
                ("check-done.py", "-c 100000000000000000000 --project nope"))
    _rr45 = {}
    for _f44, _a44 in _cases44:
        _pr44b = subprocess.run(f'"{PY}" "{ROOT / "scripts" / _f44}" {_a44}', shell=True, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _rr45[_f44] = (_pr44b.returncode, _decode(_pr44b.stdout) + _decode(_pr44b.stderr))
    results.append(case("[三十三轮] 五脚本 Int64 超界/非十进制 → py 均 rc1 中文「参数错误」（detect/text_guard/foreshadow/state_commit/check-done）", True,
                        all(v[0] == 1 and "参数错误" in v[1] for v in _rr45.values())))
    # d44③ text_guard 规则单源损坏/账本损坏 → 双引擎 rc1 同文案（中文，不裸 traceback）
    def _tg_mut(name, mutate, needle):
        _md46 = Path(tempfile.mkdtemp(prefix=f"tg-{name}-"))
        shutil.copytree(ROOT, _md46 / "r", ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", ".suite-tmp"))
        mutate(_md46 / "r")
        _rr46 = {}
        for _e46 in ENGINES:
            if _e46 == "py":
                _c46 = [PY, str(_md46 / "r" / "scripts" / "text_guard.py"), "digest", "--project", str(_md46 / "r"), "--chapter", "1"]
            else:
                _c46 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_md46 / "r" / "scripts" / "text_guard.ps1"), "-Command", "digest", "-Project", str(_md46 / "r"), "-Chapter", "1"]
            _pr46 = subprocess.run(_c46, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _rr46[_e46] = (_pr46.returncode, _nl(_decode(_pr46.stdout)).strip())
        shutil.rmtree(_md46, ignore_errors=True)
        return all(v[0] == 1 and needle in v[1] for v in _rr46.values()) and len(set(_rr46.values())) == 1
    results.append(case("[三十三轮] text_guard 规则单源损坏 → 双引擎 rc1 同文案「规则单源解析失败」", True,
                        _tg_mut("rules", lambda r: (r / "scripts" / "check-done-rules.json").write_text("{broken", encoding="utf-8"), "规则单源解析失败")))
    def _tg_ledger(r):
        # project = r 本身（与 _tg_mut 调用一致），账本须落在 r/.story/ 下
        (r / ".story").mkdir(parents=True, exist_ok=True)
        (r / ".story" / "text-digests.json").write_text("{broken", encoding="utf-8")
    results.append(case("[三十三轮] text_guard 账本损坏 → 双引擎 rc1 同文案「账本 JSON 损坏」", True,
                        _tg_mut("ledger", _tg_ledger, "账本 JSON 损坏")))
    # d44④ fingerprint 多余参数 → py/ps1/perl 均 rc1（不再静默忽略）；perl 未知旗标 rc1
    _rr48 = {}
    _pr48a = subprocess.run([PY, str(ROOT / "scripts" / "fingerprint.py"), str(_d43 / "blank.txt"), "--bogus"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
    _rr48["py多参"] = _pr48a.returncode
    _pr48b = subprocess.run([PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "fingerprint.ps1"), "-Path", str(_d43 / "blank.txt"), "-Bad"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _rr48["ps1未知旗标"] = _pr48b.returncode
    for _pf48 in ("fingerprint.pl", "fingerprint.byte.pl", "wordfreq.byte.pl"):
        _pr48c = subprocess.run(["perl", str(ROOT / "scripts" / _pf48), str(_d43 / "blank.txt"), "--bogus"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr48[_pf48] = _pr48c.returncode
    results.append(case("[三十三轮] fingerprint 多余参数/未知旗标 → py/ps1/perl 均 rc1（不再静默忽略）", True,
                        all(v == 1 for v in _rr48.values())))
    # d44⑤ state_commit --input 为目录/损坏 JSON → 双引擎 rc1 同文案
    _in44 = _d44 / "indir"
    _in44.mkdir(exist_ok=True)
    (_d44 / "pj49").mkdir(exist_ok=True)  # 项目目录须存在，否则两引擎先报「项目目录不存在」（先于 input 检查）
    _rr49 = {}
    for _e49 in ENGINES:
        if _e49 == "py":
            _c49 = [PY, str(ROOT / "scripts" / "state_commit.py"), "init", "--project", str(_d44 / "pj49"), "--input", str(_in44)]
        else:
            _c49 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "state_commit.ps1"), "init", "-Project", str(_d44 / "pj49"), "-InputFile", str(_in44)]
        _pr49 = subprocess.run(_c49, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr49[_e49] = (_pr49.returncode, _nl(_decode(_pr49.stdout)).strip())
    results.append(case("[三十三轮] state_commit --input 为目录 → 双引擎 rc1 同文案「输入不存在」（Leaf 判定）", True,
                        all(v[0] == 1 and "输入不存在" in v[1] for v in _rr49.values())
                        and len(set(_rr49.values())) == 1))


    # === 三十四轮修复回归组（JSON 顶层非对象一族：init/check/foreshadow/text_guard/_i64 收紧） ===
    # d45① state_commit init --input 顶层非对象 → 双引擎 rc1 同文案且不落盘（原 ps1 fail-open rc0）
    for _bi, (_bname, _bbody) in enumerate((("arr", "[]"), ("str", '"x"'), ("num", "12"))):
        _ind45 = _nhd / f"d45in_{_bname}.json"
        _ind45.write_text(_bbody, encoding="utf-8")
        _pj45 = _nhd / f"d45pj_{_bname}"
        _pj45.mkdir(exist_ok=True)
        _rr45a = {}
        for _e45a in ENGINES:
            if _e45a == "py":
                _c45a = [PY, str(ROOT / "scripts" / "state_commit.py"), "init", "--project", str(_pj45), "--input", str(_ind45)]
            else:
                _c45a = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "state_commit.ps1"), "init", "-Project", str(_pj45), "-InputFile", str(_ind45)]
            _pr45a = subprocess.run(_c45a, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _rr45a[_e45a] = (_pr45a.returncode, _nl(_decode(_pr45a.stdout)).strip())
        results.append(case(f"[三十四轮] init --input 顶层{_bname} 非对象 → 双引擎 rc1 同文案且不落盘", True,
                            all(v[0] == 1 and "init 输入须为 JSON 对象" in v[1] for v in _rr45a.values())
                            and len(set(_rr45a.values())) == 1
                            and not (_pj45 / "记忆系统" / "_state.json").exists()))
    # d45② _state.json 顶层非对象 → check 双引擎（py+ps51 三引擎）同文案 rc1（不再 py 裸 traceback / ps1 双宿主分叉）
    for _sj, (_sname, _sbody) in enumerate((("empty_arr", "[]"), ("arr2", "[1,2]"), ("str", '"x"'))):
        _pj45b = _nhd / f"d45state_{_sname}"
        (_pj45b / "记忆系统").mkdir(parents=True, exist_ok=True)
        (_pj45b / "记忆系统" / "_state.json").write_text(_sbody, encoding="utf-8")
        _rr45b = {}
        for _e45b in ENGINES:
            if _e45b == "py":
                _c45b = [PY, str(ROOT / "scripts" / "state_commit.py"), "check", "--project", str(_pj45b)]
            else:
                _c45b = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "state_commit.ps1"), "check", "-Project", str(_pj45b)]
            _pr45b = subprocess.run(_c45b, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _rr45b[_e45b] = (_pr45b.returncode, _nl(_decode(_pr45b.stdout)).strip())
        results.append(case(f"[三十四轮] check _state 顶层{_sname} → 双引擎 rc1 同文案「不是 JSON 对象」（判型硬按类型全名，PS5.1 数组怪癖覆盖）", True,
                            all(v[0] == 1 and "状态单不是 JSON 对象" in v[1] for v in _rr45b.values())
                            and len(set(_rr45b.values())) == 1))
    def _tg_top2(kind, needle):
        _md45 = Path(tempfile.mkdtemp(prefix=f"tg45-{kind}-"))
        shutil.copytree(ROOT, _md45 / "r", ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", ".suite-tmp"))
        if kind.startswith("ledger"):
            (_md45 / "r" / ".story").mkdir(parents=True, exist_ok=True)
            (_md45 / "r" / ".story" / "text-digests.json").write_text("[]", encoding="utf-8")
            _cmd45 = ("digest", "--project", str(_md45 / "r"), "--chapter", "1"), ("-Command", "digest", "-Project", str(_md45 / "r"), "-Chapter", "1")
        else:
            (_md45 / "r" / "scripts" / "check-done-rules.json").write_text("[]", encoding="utf-8")
            _cmd45 = ("digest", "--project", str(_md45 / "r"), "--chapter", "1"), ("-Command", "digest", "-Project", str(_md45 / "r"), "-Chapter", "1")
        _rr45d = {}
        for _e45d in ENGINES:
            _c45d = ([PY, str(_md45 / "r" / "scripts" / "text_guard.py")] + list(_cmd45[0])
                     if _e45d == "py"
                     else [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(_md45 / "r" / "scripts" / "text_guard.ps1")] + list(_cmd45[1]))
            _pr45d = subprocess.run(_c45d, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _rr45d[_e45d] = (_pr45d.returncode, _nl(_decode(_pr45d.stdout)).strip())
        shutil.rmtree(_md45, ignore_errors=True)
        return all(v[0] == 1 and needle in v[1] for v in _rr45d.values()) and len(set(_rr45d.values())) == 1
    # d45③ text_guard 账本/规则单顶层非对象 → 双引擎 rc1 同文案（原 py 裸崩 / ps1 rc2「无记录」误导）
    results.append(case("[三十四轮] text_guard 账本顶层[] → 双引擎 rc1 同文案「账本须为 JSON 对象」（不再 ps1 误判无记录 rc2）", True,
                        _tg_top2("ledger_arr", "账本须为 JSON 对象")))
    results.append(case("[三十四轮] text_guard 规则单顶层[] → 双引擎 rc1 同文案「规则单源解析失败」（不再 KeyError 裸崩 / ps1 rc2 误导）", True,
                        _tg_top2("rules_arr", "规则单源解析失败")))


    # === 三十五轮修复回归组（ps1 整数宽松形态收口 / perl 上中位 / foreshadow 超界 / NOTICE 入包） ===
    # d46① 整数参数宽松形态（+5/1e3/0x10/前导空格）→ 三引擎 rc1（ps1 [long] 原静默转换与 py _i64 反向）
    _rr46 = {}
    for _v46 in ("+5", "1e3", "0x10", " 5"):
        for _e46 in ENGINES:
            if _e46 == "py":
                _c46 = [PY, str(ROOT / "scripts" / "check-done.py"), "-c", _v46, "--project", "nope"]
            else:
                _c46 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "check-done.ps1"), "-Chapter", _v46, "-Project", "nope"]
            _pr46 = subprocess.run(_c46, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _out46 = _decode(_pr46.stdout) + _decode(_pr46.stderr)
            _rr46[f"{_e46}:{_v46}"] = (_pr46.returncode, "参数错误" in _out46)
    results.append(case("[三十五轮] 整数宽松形态 +5/1e3/0x10/空格前缀 → 双引擎均 rc1「参数错误」（ps1 前置守卫，不再 [long] 静默转换）", True,
                        all(v[0] == 1 and v[1] for v in _rr46.values())))
    # d46② 合法整数 007/5 → 守卫不误伤（rc=业务 rc1「项目目录不存在」而非参数错误）
    _rr47 = {}
    for _v47 in ("007", "5"):
        for _e47 in ENGINES:
            if _e47 == "py":
                _c47 = [PY, str(ROOT / "scripts" / "check-done.py"), "-c", _v47, "--project", str(ROOT / "no_such_proj")]
            else:
                _c47 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "check-done.ps1"), "-Chapter", _v47, "-Project", str(ROOT / "no_such_proj")]
            _pr47 = subprocess.run(_c47, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            _rr47[f"{_e47}:{_v47}"] = ("参数错误" in (_decode(_pr47.stdout) + _decode(_pr47.stderr)), "项目目录不存在" in (_decode(_pr47.stdout) + _decode(_pr47.stderr)))
    results.append(case("[三十五轮] 合法整数 007/5 → 守卫不误伤（双引擎均走业务判定，非参数错误）", True,
                        all((not v[0]) and v[1] for v in _rr47.values())))
    # d46③ perl 上中位（偶数样本 [2,3,4,5] → 中位 4，与 py/ps1 同）
    _med48 = _nhd / "med.txt"
    _med48.write_text("甲乙。\n\n甲乙丙丁。\n\n甲乙丙丁戊。\n\n甲乙丙丁戊己。\n", encoding="utf-8")
    _pr48p = subprocess.run(["perl", str(ROOT / "scripts" / "fingerprint.pl"), str(_med48)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _pr48y = subprocess.run([PY, str(ROOT / "scripts" / "fingerprint.py"), str(_med48)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
    def _med_of(txt):
        # py JSON 键「段长中位」；perl 文本「段长: … 中位=N」——各按自家输出解析后比值
        _m = re.search(r"段长中位[\"']?\s*[:=]\s*(\d+)", txt)
        if _m:
            return _m.group(1)
        _m = re.search(r"段长[^|\n]*中位=(\d+)", txt)
        return _m.group(1) if _m else "?"
    _mp = _med_of(_decode(_pr48p.stdout))
    _my = _med_of(_decode(_pr48y.stdout))
    results.append(case("[三十五轮] perl 上中位口径（偶数样本段落中位与 py 一致，不再取下中位）", True,
                        _pr48p.returncode == 0 and _mp == _my and _mp != "?"))
    # d46④ foreshadow planted_ch 超日期上界 → 双引擎 rc1 中文（不再裸异常）
    _pj49 = _nhd / "d46over"
    (_pj49 / "记忆系统").mkdir(parents=True, exist_ok=True)
    (_pj49 / "记忆系统" / "_state.json").write_text(
        '{"schema_version":1,"state_revision":0,"last_committed_chapter":0,"book_title":"x","overview":{},"context":{},"characters":{},"foreshadow":{"a":{"name":"n","planted_ch":3000000,"planned_resolve_ch":null,"resolved_ch":null,"status":"open"}},"facts":[],"wordcounts":{},"summaries":{}}',
        encoding="utf-8")
    _rr49 = {}
    for _e49 in ENGINES:
        if _e49 == "py":
            _c49 = [PY, str(ROOT / "scripts" / "foreshadow_timeline.py"), "--project", str(_pj49)]
        else:
            _c49 = [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "foreshadow_timeline.ps1"), "-Project", str(_pj49)]
        _pr49 = subprocess.run(_c49, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _rr49[_e49] = (_pr49.returncode, _nl(_decode(_pr49.stdout)).strip())
    results.append(case("[三十五轮] foreshadow planted_ch 超日期上界 → 双引擎 rc1 同文案「超出日期换算上界」（不再裸 Overflow/AddDays 异常）", True,
                        all(v[0] == 1 and "超出日期换算上界" in v[1] for v in _rr49.values())
                        and len(set(_rr49.values())) == 1))
    # d46⑤ NOTICE 入包 → 双引擎载荷 100 且清单含 NOTICE
    _pk46 = {}
    for _e46b, _cmd46b in (("py", [PY, str(ROOT / "scripts" / "package_skill.py"), "--check"]),
                           ("ps1", [PS_RUNNER, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(ROOT / "scripts" / "package_skill.ps1"), "-Check"])):
        _pr46b = subprocess.run(_cmd46b, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
        _pk46[_e46b] = (_pr46b.returncode, "载荷：100 个文件" in _decode(_pr46b.stdout), "NOTICE" in _decode(_pr46b.stdout))
    results.append(case("[三十五轮] NOTICE 入包 → 双引擎载荷 100 文件且清单含 NOTICE（Apache-2.0 署名随包保留）", True,
                        all(v[0] == 0 and v[1] and v[2] for v in _pk46.values())))

    ok_n = sum(results)
    total = len(results)
    print(f"\n结果：{ok_n}/{total} 通过")
    return 0 if ok_n == total else 1



def main() -> int:
    """外壳：任一段外异常登记为 FAIL 并打印汇总（不静默崩溃、必出结果行）。"""
    if "ps1" not in ENGINES:
        print("❌ 本套件要求 py+ps1 双引擎：未检测到 PowerShell（CI windows-latest 自带；本机请安装 pwsh 或确保 powershell 在 PATH）")
        return 1
    results: list[bool] = []
    try:
        return _run(results)
    except Exception as exc:  # noqa: BLE001
        results.append(case("未捕获异常（段外 setup/断言崩溃，登记 FAIL）", True, False,
                            f"{type(exc).__name__}: {exc}"))
        print(f"\n结果：{sum(results)}/{len(results)} 通过（含未捕获异常登记；上方 FAIL 点名根因）")
        return 1

if __name__ == "__main__":
    sys.exit(main())
