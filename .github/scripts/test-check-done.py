#!/usr/bin/env python3
"""check-done.py / 关键契约回归测试（维护者工具，不进 skill 运行时）。

用法：
  python .github/scripts/test-check-done.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-done.py"
PY = sys.executable


def run(project: Path, chapter: int, *extra: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, str(SCRIPT), "-p", str(project), "-c", str(chapter), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def empty_done(project: Path, chapter: int, marks: list[str]) -> None:
    done = project / ".done"
    done.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}"
    for m in marks:
        (done / f"第{nnn}章_{m}.done").write_bytes(b"")


def write_report(project: Path, chapter: int, body: str) -> None:
    d = project / "报告"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"第{chapter:03d}章_全量审核报告.md").write_text(body, encoding="utf-8")


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


def case(name: str, ok: bool, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond == ok else "FAIL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail and not cond else ''}")
    return cond == ok


def main() -> int:
    results: list[bool] = []
    with tempfile.TemporaryDirectory(prefix="check-done-test-") as tmp:
        tmp_path = Path(tmp)

        # 1) 无 .done 目录
        p1 = tmp_path / "p1"
        p1.mkdir()
        code, out = run(p1, 1)
        results.append(case("缺 .done 目录 → exit1", True, code == 1, out[:200]))

        # 2) 全缺
        p2 = tmp_path / "p2"
        (p2 / ".done").mkdir(parents=True)
        code, out = run(p2, 1)
        results.append(case("全部标记缺失 → exit1", True, code == 1))
        results.append(case("输出含 zhuque 提示", True, "zhuque" in out))

        # 3) 仅缺 zhuque（空标记）
        p3 = tmp_path / "p3"
        empty_done(p3, 1, [m for m in BASE if m != "zhuque"])
        code, out = run(p3, 1)
        results.append(case("缺 zhuque → exit1", True, code == 1))
        results.append(case("提示补跑 4.1b", True, "4.1b" in out or "人工证据包" in out))

        # 4) 空标记齐 + 无报告 + 默认软校验 → exit0 + warn
        p4 = tmp_path / "p4"
        empty_done(p4, 1, BASE)
        code, out = run(p4, 1)
        results.append(case("空标记齐无报告 → exit0", True, code == 0, out[:400]))
        results.append(case("软校验 missing 警告", True, "软校验" in out or "报告" in out))

        # 5) strict-report + 无报告 → exit1
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 无报告 → exit1", True, code == 1))

        # 6) 报告无「人工证据包」+ strict → exit1
        write_report(p4, 1, "# 审核\nPASS\n")
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 缺人工证据包节 → exit1", True, code == 1))
        results.append(case("输出含人工证据包", True, "人工证据包" in out))

        # 7) 报告含「人工证据包」+ strict → exit0
        write_report(p4, 1, "# 审核\n## 人工证据包\n已注入\n")
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 报告合格 → exit0", True, code == 0, out[:400]))

        # 8) 非空标记默认拒绝
        p5 = tmp_path / "p5"
        empty_done(p5, 1, BASE)
        (p5 / ".done" / "第001章_merge.done").write_text("FAKE", encoding="utf-8")
        write_report(p5, 1, "## 人工证据包\n")
        code, out = run(p5, 1, "--strict-report")
        results.append(case("非空标记默认 → exit1", True, code == 1))
        results.append(case("输出提示非空", True, "非空" in out))

        # 9) --allow-non-empty 兼容放行
        code, out = run(p5, 1, "--strict-report", "--allow-non-empty")
        results.append(case("allow-non-empty → exit0", True, code == 0, out[:400]))

        # 10) 第10章缺 innovation
        p6 = tmp_path / "p6"
        empty_done(p6, 10, BASE)
        code, out = run(p6, 10)
        results.append(case("ch10 缺 innovation → exit1", True, code == 1))
        results.append(case("输出含 innovation", True, "innovation" in out))

        # 11) 第10章齐（含空 innovation）+ 合格报告
        empty_done(p6, 10, ["innovation"])
        write_report(p6, 10, "## 人工证据包\nok\n")
        code, out = run(p6, 10, "--strict-report")
        results.append(case("ch10 空标记齐 → exit0", True, code == 0, out[:400]))

        # 12) 缺标记时 strict-report 仍打印报告诊断
        p7 = tmp_path / "p7"
        empty_done(p7, 1, [m for m in BASE if m != "zhuque"])
        # 无 zhuque 时软校验 skipped — 再造一个缺 merge 但有 zhuque 的场景
        empty_done(p7, 1, ["zhuque"])  # add zhuque empty
        # still missing others; no report
        code, out = run(p7, 1, "--strict-report")
        results.append(case("缺标记+strict 仍 exit1", True, code == 1))
        results.append(
            case("缺标记时仍打印软校验", True, "软校验" in out or "报告" in out)
        )

    ok_n = sum(results)
    total = len(results)
    print(f"\n结果：{ok_n}/{total} 通过")
    return 0 if ok_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
