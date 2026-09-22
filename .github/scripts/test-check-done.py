#!/usr/bin/env python3
"""check-done.py / check-done.ps1 关键契约回归测试（维护者工具，不进 skill 运行时）。

对同一组 fixture 双入口跑测（Python 必跑；PowerShell 在宿主可用时一并跑）。
用法：
  python .github/scripts/test-check-done.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows CI 默认 cp1252，打印中文会炸；强制 UTF-8 输出与子进程环境。
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

# 结构软校验合格样例：标题 + ≥2 类 + 强证据（人工段带段号/字数）
GOOD_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 人工段注入：第3段、第7段，合计约320字\n"
    "- 段落位置：第3段起，已写入报告\n"
    "- 结构破坏：中段跳时间，未解释信息残留\n"
    "- 对话毛刺：主要角色口癖2处\n"
)

# 仅有标题/一笔带过 → weak-section
WEAK_REPORT = "# 审核\n## 人工证据包\n已注入\n"

# 策略话术（抄协议原文，无强证据）→ strict 必须拒绝
POLICY_COPY_REPORT = (
    "# 审核\n"
    "## 人工证据包\n"
    "按人工为主协议完成密度自查，目标人工为主。\n"
    "高疑似段：无。\n"
)

# 只有弱锚点、无强证据 → strict 必须拒绝
NO_STRONG_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 结构破坏：中段跳时间\n"
    "- 对话毛刺：口癖2处\n"
)

# 把规格里的强证据词表原样抄进报告 → strict 必须拒绝（防 teach-to-test）
COPY_SPEC_REPORT = (
    "# 审核\n"
    "## 人工证据包\n"
    "至少写明 ≥2 类可核验证据锚点，且至少含一类强证据"
    "（人工段/人工注入/段落位置/300字/≥300）；"
    "弱锚点示例：结构破坏、对话毛刺、高疑似段、密度自查。\n"
)

# 仅裸词「人工段」无段号/字数结构 → 不得当强证据
BARE_KEYWORD_REPORT = (
    "# 审核\n"
    "## 人工证据包\n"
    "- 人工段：已处理\n"
    "- 段落位置：见上文\n"
    "- 300字/≥300 词表说明\n"
)


def case(name: str, ok: bool, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond == ok else "FAIL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail and not cond else ''}")
    return cond == ok


def check_engines(name: str, ok: bool, fn, detail: str = "") -> list[bool]:
    """fn(engine) -> bool，对每个可用入口各判一次。"""
    results = []
    for engine in ENGINES:
        try:
            cond = fn(engine)
            detail_e = detail
        except Exception as exc:  # noqa: BLE001 — 回归里要抓双入口异常
            cond = False
            detail_e = f"{type(exc).__name__}: {exc}"
        results.append(case(f"{name} [{engine}]", ok, cond, detail_e))
    return results


def main() -> int:
    results: list[bool] = []
    if not PS_RUNNER:
        print("（未找到 pwsh/powershell，本轮仅跑 check-done.py；CI 的 windows-latest 会双入口）")
    else:
        print(f"双入口回归：check-done.py + check-done.ps1（{PS_RUNNER}）")

    with tempfile.TemporaryDirectory(prefix="check-done-test-") as tmp:
        tmp_path = Path(tmp)

        # 1) 无 .done 目录
        p1 = tmp_path / "p1"
        p1.mkdir()
        results += check_engines(
            "缺 .done 目录 → exit1",
            True,
            lambda e, p=p1: run(p, 1, engine=e)[0] == 1,
        )

        # 2) 全缺
        p2 = tmp_path / "p2"
        (p2 / ".done").mkdir(parents=True)

        def _all_missing(engine: str) -> bool:
            code, out = run(p2, 1, engine=engine)
            return code == 1 and "zhuque" in out

        results += check_engines("全部标记缺失 → exit1 且含 zhuque 提示", True, _all_missing)

        # 3) 仅缺 zhuque（空标记）
        p3 = tmp_path / "p3"
        empty_done(p3, 1, [m for m in BASE if m != "zhuque"])

        def _miss_zhuque(engine: str) -> bool:
            code, out = run(p3, 1, engine=engine)
            return code == 1 and ("4.1b" in out or "人工证据包" in out)

        results += check_engines("缺 zhuque → exit1 并提示补跑", True, _miss_zhuque)

        # 4) 空标记齐 + 无报告 + 默认软校验 → exit0 + warn
        p4 = tmp_path / "p4"
        empty_done(p4, 1, BASE)

        def _ok_soft(engine: str) -> bool:
            code, out = run(p4, 1, engine=engine)
            return (
                code == 0
                and ("软校验" in out or "报告" in out)
                and ("对外汇报硬约定" in out or "三态" in out)
            )

        results += check_engines("空标记齐无报告 → exit0 + 软校验 + 汇报约定", True, _ok_soft)

        # 5) strict-report + 无报告 → exit1
        results += check_engines(
            "strict 无报告 → exit1",
            True,
            lambda e, p=p4: run(p, 1, "--strict-report", engine=e)[0] == 1,
        )

        # 6) 报告无「人工证据包」+ strict → exit1
        write_report(p4, 1, "# 审核\nPASS\n")

        def _no_section(engine: str) -> bool:
            code, out = run(p4, 1, "--strict-report", engine=engine)
            return code == 1 and "人工证据包" in out

        results += check_engines("strict 缺人工证据包节 → exit1", True, _no_section)

        # 6b) 仅标题无结构证据 + strict → exit1（weak-section）
        write_report(p4, 1, WEAK_REPORT)

        def _weak(engine: str) -> bool:
            code, out = run(p4, 1, "--strict-report", engine=engine)
            return code == 1 and ("结构证据" in out or "weak-section" in out)

        results += check_engines("strict 弱结构证据 → exit1", True, _weak)

        # 6c) 策略话术抄协议 + strict → exit1
        write_report(p4, 1, POLICY_COPY_REPORT)

        def _policy(engine: str) -> bool:
            code, out = run(p4, 1, "--strict-report", engine=engine)
            return code == 1 and "强证据" in out

        results += check_engines("strict 策略话术假闭环 → exit1", True, _policy)

        # 6d) 仅弱锚点无强证据 + strict → exit1
        write_report(p4, 1, NO_STRONG_REPORT)
        results += check_engines(
            "strict 无强证据 → exit1",
            True,
            lambda e, p=p4: run(p, 1, "--strict-report", engine=e)[0] == 1,
        )

        # 6e) 抄规格强证据词表 + strict → exit1（P1 防 teach-to-test）
        write_report(p4, 1, COPY_SPEC_REPORT)
        results += check_engines(
            "strict 抄规格词表假闭环 → exit1",
            True,
            lambda e, p=p4: run(p, 1, "--strict-report", engine=e)[0] == 1,
        )

        # 6f) 裸词「人工段/段落位置/300字」无段号字数结构 + strict → exit1
        write_report(p4, 1, BARE_KEYWORD_REPORT)
        results += check_engines(
            "strict 裸词表无结构 → exit1",
            True,
            lambda e, p=p4: run(p, 1, "--strict-report", engine=e)[0] == 1,
        )

        # 7) 报告结构合格 + strict → exit0
        write_report(p4, 1, GOOD_REPORT)
        results += check_engines(
            "strict 结构合格报告 → exit0",
            True,
            lambda e, p=p4: run(p, 1, "--strict-report", engine=e)[0] == 0,
        )

        # 8) 非空标记默认拒绝
        p5 = tmp_path / "p5"
        empty_done(p5, 1, BASE)
        (p5 / ".done" / "第001章_merge.done").write_text("FAKE", encoding="utf-8")
        write_report(p5, 1, GOOD_REPORT)

        def _nonempty(engine: str) -> bool:
            code, out = run(p5, 1, "--strict-report", engine=engine)
            return code == 1 and "非空" in out

        results += check_engines("非空标记默认 → exit1", True, _nonempty)

        # 9) --allow-non-empty 兼容放行（报告需结构合格；输出须带调试禁令）
        def _allow_ok(engine: str) -> bool:
            code, out = run(p5, 1, "--strict-report", "--allow-non-empty", engine=engine)
            return code == 0 and ("禁止" in out or "仅限用户" in out or "AllowNonEmpty" in out or "allow-non-empty" in out)

        results += check_engines("allow-non-empty + 合格报告 → exit0 且带调试警示", True, _allow_ok)

        # 9b) allow-non-empty + 弱报告 + strict → exit1
        write_report(p5, 1, WEAK_REPORT)
        results += check_engines(
            "allow-non-empty + 弱报告 + strict → exit1",
            True,
            lambda e, p=p5: run(p, 1, "--strict-report", "--allow-non-empty", engine=e)[0] == 1,
        )

        # 10) 第10章缺 innovation
        p6 = tmp_path / "p6"
        empty_done(p6, 10, BASE)

        def _miss_inno(engine: str) -> bool:
            code, out = run(p6, 10, engine=engine)
            return code == 1 and "innovation" in out

        results += check_engines("ch10 缺 innovation → exit1", True, _miss_inno)

        # 11) 第10章齐（含空 innovation）+ 合格报告
        empty_done(p6, 10, ["innovation"])
        write_report(p6, 10, GOOD_REPORT)
        results += check_engines(
            "ch10 空标记齐 → exit0",
            True,
            lambda e, p=p6: run(p, 10, "--strict-report", engine=e)[0] == 0,
        )

        # 12) 标记齐但报告缺失 + strict：exit1 且打印软校验
        p7 = tmp_path / "p7"
        empty_done(p7, 1, BASE)

        def _strict_missing_report(engine: str) -> bool:
            code, out = run(p7, 1, "--strict-report", engine=engine)
            return code == 1 and ("软校验" in out or "报告" in out)

        results += check_engines("标记齐无报告+strict → exit1 且打印软校验", True, _strict_missing_report)

        # 13) 缺标记 + 弱报告 + strict：exit1 且输出 weak 诊断
        p8 = tmp_path / "p8"
        empty_done(p8, 1, BASE)
        (p8 / ".done" / "第001章_merge.done").unlink()
        write_report(p8, 1, WEAK_REPORT)

        def _miss_and_weak(engine: str) -> bool:
            code, out = run(p8, 1, "--strict-report", engine=engine)
            return code == 1 and ("结构证据" in out or "软校验" in out)

        results += check_engines("缺merge+弱报告+strict → exit1 且含诊断", True, _miss_and_weak)

        # 14) 不填充章号 第1章_* 应被识别
        p9 = tmp_path / "p9"
        empty_done(p9, 1, BASE, pad=False)
        write_report(p9, 1, GOOD_REPORT, pad=False)
        results += check_engines(
            "不填充章号 第1章 → exit0",
            True,
            lambda e, p=p9: run(p, 1, "--strict-report", engine=e)[0] == 0,
        )

        # 15) 双章号同时存在 → 告警但仍优先零填充判定
        p10 = tmp_path / "p10"
        empty_done(p10, 1, BASE)  # 第001章_*
        empty_done(p10, 1, BASE, pad=False)  # 第1章_*
        write_report(p10, 1, GOOD_REPORT)

        def _conflict_warn(engine: str) -> bool:
            code, out = run(p10, 1, "--strict-report", engine=engine)
            return code == 0 and ("命名冲突" in out or "冲突" in out)

        results += check_engines("双章号并存 → exit0 且告警", True, _conflict_warn)

        # 16) allow-non-empty 输出含「禁止」宿主字样（P3 文案）
        p11 = tmp_path / "p11"
        empty_done(p11, 1, BASE)
        (p11 / ".done" / "第001章_bible.done").write_text("X", encoding="utf-8")

        def _allow_warn_text(engine: str) -> bool:
            code, out = run(p11, 1, "--allow-non-empty", engine=engine)
            return code == 0 and re.search(r"禁止|仅限用户", out) is not None

        results += check_engines("allow-non-empty 警示文案存在", True, _allow_warn_text)

    ok_n = sum(results)
    total = len(results)
    print(f"\n结果：{ok_n}/{total} 通过（引擎：{', '.join(ENGINES)}）")
    return 0 if ok_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
