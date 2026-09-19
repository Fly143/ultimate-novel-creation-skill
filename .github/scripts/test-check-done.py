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

# Windows CI 默认 cp1252，打印中文会炸；强制 UTF-8 输出与子进程环境。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
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

# 结构软校验合格样例：标题 + ≥2 类锚点 + 强证据（人工段/段落位置/300字）
GOOD_REPORT = (
    "# 审核报告\n"
    "## 人工证据包\n"
    "- 人工段注入：第3段、第7段，合计约320字\n"
    "- 段落位置：已写入报告\n"
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
        results.append(
            case("成功路径含对外汇报硬约定", True, "对外汇报硬约定" in out or "三态" in out)
        )

        # 5) strict-report + 无报告 → exit1
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 无报告 → exit1", True, code == 1))

        # 6) 报告无「人工证据包」+ strict → exit1
        write_report(p4, 1, "# 审核\nPASS\n")
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 缺人工证据包节 → exit1", True, code == 1))
        results.append(case("输出含人工证据包", True, "人工证据包" in out))

        # 6b) 仅标题无结构证据 + strict → exit1（weak-section）
        write_report(p4, 1, WEAK_REPORT)
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 弱结构证据 → exit1", True, code == 1, out[:400]))
        results.append(
            case("输出含 weak-section/结构证据", True, "结构证据" in out or "weak-section" in out)
        )

        # 6c) 策略话术抄协议 + strict → exit1（不得仅凭「人工为主/密度自查」通过）
        write_report(p4, 1, POLICY_COPY_REPORT)
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 策略话术假闭环 → exit1", True, code == 1, out[:400]))
        results.append(case("输出含强证据要求", True, "强证据" in out))

        # 6d) 仅弱锚点无强证据 + strict → exit1
        write_report(p4, 1, NO_STRONG_REPORT)
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 无强证据 → exit1", True, code == 1, out[:400]))

        # 7) 报告结构合格 + strict → exit0
        write_report(p4, 1, GOOD_REPORT)
        code, out = run(p4, 1, "--strict-report")
        results.append(case("strict 结构合格报告 → exit0", True, code == 0, out[:400]))

        # 8) 非空标记默认拒绝
        p5 = tmp_path / "p5"
        empty_done(p5, 1, BASE)
        (p5 / ".done" / "第001章_merge.done").write_text("FAKE", encoding="utf-8")
        write_report(p5, 1, GOOD_REPORT)
        code, out = run(p5, 1, "--strict-report")
        results.append(case("非空标记默认 → exit1", True, code == 1))
        results.append(case("输出提示非空", True, "非空" in out))

        # 9) --allow-non-empty 兼容放行（报告需结构合格，否则 strict 仍拦）
        code, out = run(p5, 1, "--strict-report", "--allow-non-empty")
        results.append(case("allow-non-empty + 合格报告 → exit0", True, code == 0, out[:400]))

        # 9b) allow-non-empty + 弱报告 + strict → exit1
        write_report(p5, 1, WEAK_REPORT)
        code, out = run(p5, 1, "--strict-report", "--allow-non-empty")
        results.append(case("allow-non-empty + 弱报告 + strict → exit1", True, code == 1, out[:400]))

        # 10) 第10章缺 innovation
        p6 = tmp_path / "p6"
        empty_done(p6, 10, BASE)
        code, out = run(p6, 10)
        results.append(case("ch10 缺 innovation → exit1", True, code == 1))
        results.append(case("输出含 innovation", True, "innovation" in out))

        # 11) 第10章齐（含空 innovation）+ 合格报告
        empty_done(p6, 10, ["innovation"])
        write_report(p6, 10, GOOD_REPORT)
        code, out = run(p6, 10, "--strict-report")
        results.append(case("ch10 空标记齐 → exit0", True, code == 0, out[:400]))

        # 12) 标记齐但报告缺失 + strict：exit1 且打印软校验（非「缺标记」场景）
        p7 = tmp_path / "p7"
        empty_done(p7, 1, BASE)
        code, out = run(p7, 1, "--strict-report")
        results.append(case("标记齐无报告+strict → exit1", True, code == 1))
        results.append(
            case("标记齐无报告时仍打印软校验", True, "软校验" in out or "报告" in out)
        )

        # 13) 缺标记 + 弱报告 + strict：exit1 且输出 weak 诊断
        p8 = tmp_path / "p8"
        empty_done(p8, 1, BASE)
        (p8 / ".done" / "第001章_merge.done").unlink()
        write_report(p8, 1, WEAK_REPORT)
        code, out = run(p8, 1, "--strict-report")
        results.append(case("缺merge+弱报告+strict → exit1", True, code == 1))
        results.append(
            case("缺标记路径仍输出结构软校验诊断", True, "结构证据" in out or "软校验" in out)
        )

        # 14) 不填充章号 第1章_* 应被识别
        p9 = tmp_path / "p9"
        done9 = p9 / ".done"
        done9.mkdir(parents=True)
        for m in BASE:
            (done9 / f"第1章_{m}.done").write_bytes(b"")
        rep9 = p9 / "报告"
        rep9.mkdir()
        (rep9 / "第1章_全量审核报告.md").write_text(GOOD_REPORT, encoding="utf-8")
        code, out = run(p9, 1, "--strict-report")
        results.append(case("不填充章号 第1章 → exit0", True, code == 0, out[:400]))

    ok_n = sum(results)
    total = len(results)
    print(f"\n结果：{ok_n}/{total} 通过")
    return 0 if ok_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
