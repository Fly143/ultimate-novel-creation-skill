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
PY = sys.executable

FLAG_MAP = {
    "--strict-report": "-StrictReport",
    "--allow-non-empty": "-AllowNonEmpty",
    "--include-chapter-done": "-IncludeChapterDone",
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


def empty_done(project: Path, chapter: int, marks: list[str], pad: bool = True) -> None:
    done = project / ".done"
    done.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}" if pad else str(chapter)
    for m in marks:
        (done / f"第{nnn}章_{m}.done").write_bytes(b"")


def write_report(project: Path, chapter: int, body: str, pad: bool = True) -> None:
    d = project / "报告"
    d.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}" if pad else str(chapter)
    (d / f"第{nnn}章_全量审核报告.md").write_text(body, encoding="utf-8")


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

        p3 = tmp_path / "p3"
        empty_done(p3, 1, BASE)
        results += check_engines(
            "空标记齐 + 无报告 → exit0",
            True,
            lambda e, p=p3: run(p, 1, engine=e)[0] == 0,
        )
        results += check_engines(
            "strict 无报告 → exit1",
            True,
            lambda e, p=p3: run(p, 1, "--strict-report", engine=e)[0] == 1,
        )

        write_report(p3, 1, GOOD_REPORT)
        results += check_engines(
            "strict 结构合格报告 → exit0",
            True,
            lambda e, p=p3: run(p, 1, "--strict-report", engine=e)[0] == 0,
        )

        write_report(p3, 1, WEAK_REPORT)
        results += check_engines(
            "strict 弱证据报告 → exit1",
            True,
            lambda e, p=p3: run(p, 1, "--strict-report", engine=e)[0] == 1,
        )

        p4 = tmp_path / "p4"
        empty_done(p4, 1, BASE)
        (p4 / ".done" / "第001章_merge.done").write_text("FAKE", encoding="utf-8")
        write_report(p4, 1, GOOD_REPORT)
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
        empty_done(p5, 10, BASE)
        results += check_engines(
            "ch10 缺 innovation → exit1",
            True,
            lambda e, p=p5: run(p, 10, engine=e)[0] == 1,
        )
        empty_done(p5, 10, ["innovation"])
        write_report(p5, 10, GOOD_REPORT)
        results += check_engines(
            "ch10 含 innovation → exit0",
            True,
            lambda e, p=p5: run(p, 10, "--strict-report", engine=e)[0] == 0,
        )

        p6 = tmp_path / "p6"
        empty_done(p6, 1, BASE, pad=False)
        write_report(p6, 1, GOOD_REPORT, pad=False)
        results += check_engines(
            "不填充章号 第1章 → exit0",
            True,
            lambda e, p=p6: run(p, 1, "--strict-report", engine=e)[0] == 0,
        )

        results += check_engines(
            "include-chapter-done 缺 chapter → exit1",
            True,
            lambda e, p=p3: run(p, 1, "--include-chapter-done", engine=e)[0] == 1,
        )

    ok_n = sum(results)
    total = len(results)
    print(f"\n结果：{ok_n}/{total} 通过")
    return 0 if ok_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
