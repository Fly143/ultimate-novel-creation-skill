#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""读取证明校验/生成（P7 内容门禁的校验工具）。

写作输入保真：以「读取证明」+ 原文子串交叉验证兜底「未读即写」——
未读文件的幻觉抽取无法连续命中原文（规则见 modules/03_26 步骤1.5）。

用法（校验）：
    python scripts/read_proof.py -p <项目> -c <章节> [--proof <相对路径>]

用法（生模板，供写章子代理填写 extracts）：
    python scripts/read_proof.py -p <项目> -c <章节> --init
        --reads "记忆系统/圣经/故事圣经.md,设定/细纲/第016章_细纲.md,报告/第015章_全量审核报告.md"

调用方 = scripts/check-done.py 的 read_proof 分支（经 subprocess）。
输出：✅ 汇总行 / ❌ 逐条问题行；rc 0=过，1=不过。
"""
from __future__ import annotations

import argparse
import glob
import datetime as _dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MIN_FILES = 3
MIN_EXTRACTS_PER_FILE = 2
MIN_CHARS_PER_EXTRACT = 6


def _w(msg: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def norm_text(s: str) -> str:
    r"""抽取比对用规范化：仅保留词字符（中文/字母/数字/下划线），去标点空白（re: [^\w]+）。"""
    return re.sub(r"[^\w]+", "", s, flags=re.UNICODE)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def _parse_ts(raw) -> tuple[_dt.datetime | None, str]:
    if raw is None:
        return None, "缺失"
    s = str(raw).strip()
    if not s:
        return None, "空白"
    try:
        return _dt.datetime.fromisoformat(s), ""
    except ValueError:
        pass
    try:
        return _dt.datetime.fromtimestamp(float(s)), ""
    except (ValueError, OSError, OverflowError):
        return None, s


def default_proof_path(project: Path, nnn: str, chapter: int) -> str:
    for tmpl in ("报告/第{nnn}章_读取证明.json", "报告/第{ch}章_读取证明.json"):
        rel = tmpl.replace("{nnn}", nnn).replace("{ch}", str(chapter))
        if (project / rel).is_file():
            return rel
    return "报告/第" + nnn + "章_读取证明.json"


def body_candidates(project: Path, nnn: str, chapter: int) -> list[Path]:
    out: list[Path] = []
    for pat in ("正文/第{nnn}章_*.txt", "正文/第{ch}章_*.txt"):
        for g in glob.glob(str(project / pat.replace("{nnn}", nnn).replace("{ch}", str(chapter)))):
            p = Path(g)
            if p.is_file() and p not in out:
                out.append(p)
    return out


def init_proof(project: Path, nnn: str, chapter: int, reads: list[str], proof_rel: str) -> int:
    reads_out = []
    for rel in reads:
        rel = str(rel).strip().strip('"').strip("'")
        p = project / rel.replace("\\", "/")
        if not p.is_file():
            _w(f"❌ 读取证明初始化：输入档案不存在 -> {rel}（--reads 必须指向项目内真实文件）")
            return 1
        reads_out.append({
            "path": str(rel).replace("\\", "/"),
            "sha256": sha256_file(p),
            "extracts": [],
            "read_at": _dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        })
    if not reads_out:
        _w("❌ 读取证明初始化：--reads 未提供任何档案（至少 3 份输入）")
        return 1
    doc = {
        "chapter": chapter,
        "generated": _dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "note": "写作输入留证：每份 input 的 extracts 填『真实读到的原文子句』(≥2 条/≥6 字)，交付前运行本脚本校验",
        "reads": reads_out,
    }
    outpath = project / proof_rel.replace("\\", "/")
    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    _w(f"✅ 读取证明模板已生成：{proof_rel}（{len(reads_out)} 份输入；填入 extracts 后运行校验）")
    return 0


def verify_proof(
    project: Path,
    chapter: int,
    nnn: str,
    proof_rel: str,
    min_files: int = MIN_FILES,
    min_extracts: int = MIN_EXTRACTS_PER_FILE,
    min_chars: int = MIN_CHARS_PER_EXTRACT,
) -> int:
    errs: list[str] = []
    proof_path = project / proof_rel.replace("\\", "/")
    if not proof_path.is_file():
        _w(f"❌ 读取证明（第{chapter}章）：缺读取证明文件 {proof_rel}（写作必须留输入证据；见 modules/03_26 步骤1.5）")
        return 1
    try:
        doc = json.loads(proof_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        _w(f"❌ 读取证明（第{chapter}章）：读取证明解析失败 {proof_rel}：{exc}")
        return 1
    if not isinstance(doc, dict):
        _w(f"❌ 读取证明（第{chapter}章）：读取证明格式非法（应为对象，含 chapter/reads）{proof_rel}")
        return 1
    if doc.get("chapter") != chapter:
        _w(f"❌ 读取证明（第{chapter}章）：章号不符（证明声明第{doc.get('chapter')}章）{proof_rel}")
    reads = doc.get("reads")
    if not isinstance(reads, list) or not reads:
        _w(f"❌ 读取证明（第{chapter}章）：reads 缺失/为空（至少 {min_files} 份输入）{proof_rel}")
        return 1

    text_cache: dict[str, str] = {}
    total_extracts = 0
    seen_files: set[str] = set()
    ts_items: list[tuple[_dt.datetime | None, str]] = []

    for idx, item in enumerate(reads):
        if not isinstance(item, dict):
            errs.append(f"第{idx + 1} 项格式非法")
            continue
        rel = str(item.get("path", "")).strip().replace("\\", "/")
        p = project / rel
        if not rel:
            errs.append(f"第{idx + 1} 项缺 path")
            continue
        seen_files.add(rel)
        read_at, ts_err = _parse_ts(item.get("read_at"))
        if read_at is not None:
            ts_items.append((read_at, idx))
        else:
            errs.append(f"读取时间 read_at 缺失/不可解析「{item.get('read_at')}」（应 ISO 时间）")
        if not p.is_file():
            errs.append(f"{rel} 不存在")
            continue
        recorded = str(item.get("sha256", "")).strip().lower()
        if not recorded:
            errs.append(f"{rel} 缺 sha256 声明（未做文件指纹）")
        actual = sha256_file(p)
        if recorded and not (actual.startswith(recorded) or recorded.startswith(actual)):
            errs.append(
                f"{rel} 的 sha256 不一致（记录 {recorded[:12]} ≠ 实际 {actual[:12]}）"
            )
        text_cache.setdefault(rel, _read_text(p))
        extracts = item.get("extracts")
        if not isinstance(extracts, list) or not extracts:
            errs.append(f"{rel} 未填抽取子句（extracts 为空，需≥{min_extracts} 条『真实读到』的原文子句）")
            continue
        total_extracts += len(extracts)
        if len(extracts) < min_extracts:
            errs.append(f"{rel} 抽取子句不足（需≥{min_extracts} 条，实得 {len(extracts)}）")
        norm_full = norm_text(text_cache[rel])
        for ex in extracts:
            s = str(ex).strip()
            if not s:
                errs.append(f"{rel} 存在空抽取子句")
                continue
            ne = norm_text(s)
            if len(ne) < min_chars:
                errs.append(f"{rel} 的抽取子句长度不足（需≥{min_chars} 汉字，实得 {len(ne)}）")
                continue
            if ne not in norm_full:
                errs.append(f"{rel} 的抽取子句「{s[:24]}」不在原文中（未读即写疑证）")

    if len(seen_files) < min_files:
        errs.append(f"读取输入仅 {len(seen_files)} 份，低于下限 {min_files}（跨文件≥3）")

    # 顺序注记：read_at 晚于正文最终稿 mtime → 疑似事后补证
    bodies = body_candidates(project, nnn, chapter)
    if bodies and ts_items:
        latest = max(r for (r, _) in ts_items)
        if latest.tzinfo is not None:
            latest = latest.astimezone().replace(tzinfo=None)
        for rb in bodies:
            try:
                bmt = _dt.datetime.fromtimestamp(rb.stat().st_mtime)
                if latest > bmt + _dt.timedelta(seconds=300):
                    errs.append(
                        f"读取时间晚于正文最终稿 mtime（{latest.isoformat()} > {rb.name} {bmt.isoformat()}；顺序异常，疑似事后补证）"
                    )
            except OSError:
                pass

    if errs:
        _w(f"❌ 读取证明（第{chapter}章）未通过（{len(errs)} 处）：")
        for e in errs[:10]:
            _w(f"  · {e}")
        for e in errs[10:]:
            _w(f"  · {e}")
        return 1
    _w(f"✅ 读取证明：第{chapter}章；共 {len(seen_files)} 份输入、{total_extracts} 条抽取子句全部命中原文")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--project", required=True)
    ap.add_argument("-c", "--chapter", required=True, type=int)
    ap.add_argument("--proof", default=None)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--reads", default=None)
    ap.add_argument("--min-files", type=int, default=MIN_FILES)
    ap.add_argument("--min-extracts", type=int, default=MIN_EXTRACTS_PER_FILE)
    ap.add_argument("--min-chars", type=int, default=MIN_CHARS_PER_EXTRACT)
    args = ap.parse_args(argv)

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        _w(f"❌ 读取证明：项目目录不存在：{project}")
        return 1
    chapter = int(args.chapter)
    nnn = f"{chapter:03d}"
    proof_rel = args.proof or default_proof_path(project, nnn, chapter)
    if args.init:
        reads = [s for s in (args.reads or "").split(",")] if args.reads else []
        return init_proof(project, nnn, chapter, reads, proof_rel)
    return verify_proof(
        project, chapter, nnn, proof_rel,
        min_files=args.min_files,
        min_extracts=args.min_extracts,
        min_chars=args.min_chars,
    )


if __name__ == "__main__":
    sys.exit(main())
