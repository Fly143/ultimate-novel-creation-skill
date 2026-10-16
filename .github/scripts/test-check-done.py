#!/usr/bin/env python3
"""check-done 契约回归（维护者工具）。同一 fixture 双跑 py / ps1。

用法：
  python .github/scripts/test-check-done.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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


def run_ps1(project: Path, chapter: int, *extra: str) -> tuple[int, str]:
    mapped = [FLAG_MAP[e] for e in extra]
    proc = subprocess.run(
        [
            PS_RUNNER,
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


def run(project: Path, chapter: int, *extra: str, engine: str = "py") -> tuple[int, str]:
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
STATE_ENGINES = ["py", "ps1"] if PS_RUNNER else ["py"]


def run_state(engine: str, command: str, *args: str) -> tuple[int, str]:
    """调用 state_commit（命令与 flag 分离：py 用 --flag，ps1 用 -Command/-Flag）。"""
    if engine == "ps1":
        mapped: list[str] = ["-Command", command]
        it = iter(args)
        for a in it:
            val = next(it, "")
            flag = {"--project": "-Project", "--chapter": "-Chapter", "--input": "-InputFile"}.get(a, a)
            mapped += [flag, val]
        proc = subprocess.run(
            [PS_RUNNER, "-NoProfile", "-File", str(STATE_PS1), *mapped],
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


def main() -> int:
    results: list[bool] = []
    print(f"引擎：{', '.join(ENGINES)}")
    if not RULES.is_file():
        print(f"❌ 缺少规则单源：{RULES}")
        return 1

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
            return code == 0 and ("报告校验" in out or "警告" in out)

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
            results.append(case(f"[state:{engine}] init → exit0", True, code == 0))
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

    # ---------------- AI 文本启发式检测（P1-8）----------------

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
            cmd = [PS_RUNNER, "-NoProfile", "-File", str(DETECT_PS1), "-InputFile", str(txt)]
        else:
            cmd = [PY, str(DETECT_PY), "--input", str(txt)]
        proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return proc.returncode, proc.stdout

    for e in ("py", "ps1"):
        _c, _o = _detect(e, _ai_txt)
        _txt = _o.decode("utf-8", "replace")
        results.append(case(f"[detect:{e}] AI 味样例 exit0 且含总命中行", True,
                            _c == 0 and ("总命中：" in _txt)))
        _c2, _o2 = _detect(e, _clean_txt)
        _txt2 = _o2.decode("utf-8", "replace")
        results.append(case(f"[detect:{e}] 干净样例 总命中=0 且声明不构成朱雀结论", True,
                            _c2 == 0 and "总命中：0" in _txt2 and "不构成朱雀结论" in _txt2))
    _cp, _op = _detect("py", _ai_txt)
    _cs, _os = _detect("ps1", _ai_txt)
    results.append(case("[detect] AI 味样例 py/ps1 输出逐字节一致", True, _cp == _cs and _op == _os))
    _cp2, _op2 = _detect("py", _clean_txt)
    _cs2, _os2 = _detect("ps1", _clean_txt)
    results.append(case("[detect] 干净样例 py/ps1 输出逐字节一致", True, _cp2 == _cs2 and _op2 == _os2))
    _p = subprocess.run([PY, str(DETECT_PY), "--input", str(_ai_txt), "--fail-over", "3"], capture_output=True)
    _s = subprocess.run([PS_RUNNER, "-NoProfile", "-File", str(DETECT_PS1), "-InputFile", str(_ai_txt), "-FailOver", "3"], capture_output=True)
    results.append(case("[detect] fail-over 3（命中≥3）→ 双实现 exit1", True, _p.returncode == 1 and _s.returncode == 1))
    _p = subprocess.run([PY, str(DETECT_PY), "--input", str(_ai_txt), "--fail-over", "999"], capture_output=True)
    _s = subprocess.run([PS_RUNNER, "-NoProfile", "-File", str(DETECT_PS1), "-InputFile", str(_ai_txt), "-FailOver", "999"], capture_output=True)
    results.append(case("[detect] fail-over 999（不可达）→ 双实现 exit0", True, _p.returncode == 0 and _s.returncode == 0))

    # ---------------- 正文指纹/手改保护（P1-9）----------------

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
            cmd = [PS_RUNNER, "-NoProfile", "-File", str(GUARD_PS1), "-Command", command, "-Project", str(pj), "-Chapter", str(ch)]
        else:
            cmd = [PY, str(GUARD_PY), command, "--project", str(pj), "--chapter", str(ch)]
        proc = subprocess.run(cmd, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        err = proc.stderr.decode("utf-8", "replace").strip()
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), (f" | stderr: {err[:220]}" if err else "")

    for e in ("py", "ps1"):
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
    results.append(case("[guard] digest/verify 四场景 py/ps1 输出逐字节一致", True, _o_py == _o_ps1))
    # verify-all：手改 ch2 → exit1
    _pjA = _mk_guard_proj("p_guard_all")
    _guard("py", "digest", _pjA, 1); _guard("py", "digest", _pjA, 2)
    (_pjA / "正文" / "第002章_夜行.txt").write_text("风停了。\n", encoding="utf-8")
    cA, oA, _eA = _guard("py", "verify-all", _pjA, 0)
    cB, oB, _eB = _guard("ps1", "verify-all", _pjA, 0)
    results.append(case("[guard] verify-all（ch2 被改）双实现 exit1 且逐字节一致", True,
                        cA == 1 and cB == 1 and oA == oB and "不一致 1 章" in oA, _eA + _eB))

    # ---------------- 技能发布包（P3-19）----------------

    PKG_PY = ROOT / "scripts" / "package_skill.py"
    PKG_PS1 = ROOT / "scripts" / "package_skill.ps1"
    _pa = subprocess.run([PY, str(PKG_PY), "--check"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _pb = subprocess.run([PS_RUNNER, "-NoProfile", "-File", str(PKG_PS1), "-Check"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    results.append(case("[pkg] --check/-Check 双实现 exit0 且输出逐字节一致", True,
                        _pa.returncode == 0 and _pb.returncode == 0 and _pa.stdout == _pb.stdout))
    _pkg_dir = Path(tempfile.mkdtemp(prefix="pkg-test-"))
    (_pkg_dir / "modules").mkdir()
    (_pkg_dir / "SKILL.md").write_text("---\nname: demo\ndescription: 测试\n---\n# demo\n", encoding="utf-8")
    _na = subprocess.run([PY, str(PKG_PY), "--check", "--root", str(_pkg_dir)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _nb = subprocess.run([PS_RUNNER, "-NoProfile", "-File", str(PKG_PS1), "-Check", "-Root", str(_pkg_dir)], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _ta = (_na.stdout + _na.stderr).decode("utf-8", "replace")
    _tb = (_nb.stdout + _nb.stderr).decode("utf-8", "replace")
    results.append(case("[pkg] frontmatter 缺 version → 双实现 exit1 且输出一致", True,
                        _na.returncode == 1 and _nb.returncode == 1 and _ta == _tb and "缺字段" in _ta and "version" in _ta))
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

    # ---------------- 摘要陈旧检测（OF-6 / P3-24）----------------

    _st_dir = Path(tempfile.mkdtemp(prefix="stale-test-"))
    _sp = _st_dir / "p_stale"
    _sp.mkdir()
    for _e in STATE_ENGINES:
        run_state(_e, "init", "--project", str(_sp))
        run_state(_e, "draft", "--project", str(_sp), "--chapter", "1")
        write_txn(_sp, 1, chapter_delta(1, "艾露"))
        run_state(_e, "commit", "--project", str(_sp), "--chapter", "1", "--input", str(_sp / ".story" / "work" / "第001章" / "state.json"))
        break  # 引擎无关：任一引擎建一次即可
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
    results.append(case("[stale] 双实现 stale 警告输出逐字节一致", True,
                        _outs[STATE_ENGINES[0]][1] == _outs[STATE_ENGINES[1]][1]))
    _stf.write_text(_orig_state, encoding="utf-8")
    _c, _o = run_state(STATE_ENGINES[0], "check", "--project", str(_sp))
    results.append(case("[stale] 摘要补全后 → ✅摘要连续（stale=0）", True, "✅ 摘要连续（stale=0）" in _o))

    # ---------------- 伏笔时间线可视化（MU-1 / P3-21）----------------

    FS_PY = ROOT / "scripts" / "foreshadow_timeline.py"
    FS_PS1 = ROOT / "scripts" / "foreshadow_timeline.ps1"
    _fs_dir = Path(tempfile.mkdtemp(prefix="fs-test-"))

    def _fs_run(engine: str, pj: Path):
        if engine == "ps1":
            cmd = [PS_RUNNER, "-NoProfile", "-File", str(FS_PS1), "-Project", str(pj)]
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
    for _e in ("py", "ps1"):
        _c, _o, _md = _fs_run(_e, _old)
        _r[_e] = (_c, _o, _md)
        results.append(case(f"[fs:{_e}] 旧清单源 → exit0 且产物含过期点名/mermaid", True,
                            _c == 0 and "🚨 过期：#1 祖传玉佩" in _o and "```mermaid" in _md and "第 X 章" not in _md))
    results.append(case("[fs] 旧清单源 py/ps1 stdout 与产物 md 双逐字节一致", True,
                        _r["py"] == _r["ps1"]))
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
    for _e in ("py", "ps1"):
        _c, _o, _md = _fs_run(_e, _new)
        _r2[_e] = (_c, _o, _md)
        results.append(case(f"[fs:{_e}] state 源 → 数据源=state 且 F001 过期 30 章", True,
                            _c == 0 and "源=state" in _o and "超期 30 章" in _o and "师父的信" in _md))
    results.append(case("[fs] state 源 py/ps1 stdout 与产物 md 双逐字节一致", True,
                        _r2["py"] == _r2["ps1"]))

    ok_n = sum(results)
    total = len(results)
    print(f"\n结果：{ok_n}/{total} 通过")
    return 0 if ok_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
