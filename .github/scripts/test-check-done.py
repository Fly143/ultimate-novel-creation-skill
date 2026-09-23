#!/usr/bin/env python3
"""check-done 契约回归（维护者工具）。同一 fixture 双跑 py / ps1。

用法：
  python .github/scripts/test-check-done.py
"""

from __future__ import annotations

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
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run(project: Path, chapter: int, *extra: str, engine: str = "py") -> tuple[int, str]:
    if engine == "ps1":
        return run_ps1(project, chapter, *extra)
    return run_py(project, chapter, *extra)


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

    ok_n = sum(results)
    total = len(results)
    print(f"\n结果：{ok_n}/{total} 通过")
    return 0 if ok_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
