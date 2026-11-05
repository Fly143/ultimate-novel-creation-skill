#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""package_skill.py —— 技能发布包打包与校验（P3-19）。

校验 SKILL.md frontmatter（name/description/version 必填）→ 收集运行时载荷
载荷：SKILL.md / system_prompt.md / modules / templates / references / scripts / memory-system（权威白名单见 INCLUDE_FILES / INCLUDE_DIRS）。
  memory-system 为项目初始化模板源（03_01 步骤8.2 读取 8 个模板），属运行时必需，不得排除。
→ --check 只打印清单；默认再产出确定性 zip（固定时间戳+排序；同一 Python 解释器两次运行字节一致，跨解释器 zlib 差异可致字节不同，载荷计数不受影响）。
排除：.git / .github / dist 等过程资产。
双实现口径：--check 清单输出与 package_skill.ps1 逐字节一致；zip 仅 py 产出（见实施记录偏离项）。
用法：
  python scripts/package_skill.py --check
  python scripts/package_skill.py --out dist/skill-package.zip
"""
import argparse
import os
import sys
import zipfile
from pathlib import Path

class _ArgParser(argparse.ArgumentParser):
    # 参数错误收敛 rc1 + 中文（对齐 check-done/check-doc-budget/detect；ps1 绑定失败亦 rc1）
    def error(self, message):
        print(f"❌ 参数错误：{message}")
        raise SystemExit(1)


INCLUDE_DIRS = ["modules", "templates", "references", "scripts", "memory-system"]  # memory-system=项目初始化模板源（03_01 步骤8.2）
INCLUDE_FILES = ["SKILL.md", "system_prompt.md"]
FM_REQUIRED = ["name", "description", "version"]


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def collect(root: Path) -> list[str]:
    rels: list[str] = []
    for f in INCLUDE_FILES:
        if (root / f).is_file():
            rels.append(f)
    for d in INCLUDE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if "__pycache__" in {x.lower() for x in p.parts} or p.suffix.lower() in (".pyc", ".pyo"):  # 大小写不敏感对齐 ps1 -contains/-eq（.PYC/__PyCache__ 分叉）
                continue  # 机器本地字节码不进载荷（否则读数随本机状态漂移、zip 带陈旧字节）
            rels.append(p.relative_to(root).as_posix())
    return sorted(set(rels))


def main() -> int:
    ap = _ArgParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--out", default="")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的输出文件（默认拒绝，防手工补过的包被静默抹掉）")
    a = ap.parse_args()
    root = Path(a.root).expanduser().resolve()

    skill = root / "SKILL.md"
    if not skill.is_file():
        print("❌ 缺少 SKILL.md")
        return 1
    fm = frontmatter(skill.read_text(encoding="utf-8-sig"))  # 带 BOM 的合法 SKILL.md 与 ps1 ReadAllText 剥 BOM 同口径
    missing = [k for k in FM_REQUIRED if not fm.get(k)]
    if missing:
        print(f"❌ SKILL.md frontmatter 缺字段：{'、'.join(missing)}")
        return 1

    rels = collect(root)
    total_units = 0
    for rel in rels:
        data = (root / rel).read_bytes()
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        total_units += len(text.encode("utf-16-le")) // 2
    print(f"【技能发布包】name={fm.get('name')} version={fm.get('version')}")
    print(f"载荷：{len(rels)} 个文件 ｜ 字符（UTF-16 单元）：{total_units}")
    for rel in rels:
        print(f"  {(root / rel).stat().st_size:>8}  {rel}")

    if a.check:
        return 0
    out = Path(a.out or (root / "dist" / "skill-package.zip")).expanduser().resolve()
    if out.exists() and not a.force:
        print(f"❌ 输出文件已存在：{out}（确认要覆盖请加 --force；默认不抹掉已有包）")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in rels:
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, (root / rel).read_bytes())
    print(f"已产出：{out}（{out.stat().st_size} 字节，确定性打包）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
