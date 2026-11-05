#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""text_guard.py —— 正文指纹与手改保护（P1-9）。

记录：正文/第NNN章_*.txt 的 sha256 + UTF-16 单元数 → {书名}/.story/text-digests.json（临时文件+原子替换写入）。
核对：与记录不一致 → exit1 并给出「停下询问：沿用/重写/只看」处置口径（03_26 五·文本指纹）。
无记录/缺正文 → exit2（先 digest 或修路径，不算手改）。
双实现对拍：与 text_guard.ps1 输出逐字节一致（长度=UTF-16 单元；sha256=正文 UTF-8 字节的十六进制小写）。
用法：
  python scripts/text_guard.py digest  --project "路径/书名" --chapter 3
  python scripts/text_guard.py verify  --project "路径/书名" --chapter 3
  python scripts/text_guard.py verify-all --project "路径/书名"
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

def _i64(s):
    # Int64 范围收口（对齐 ps1 [long] 绑定失败 rc1 / check-doc-budget _in_i64 先例）
    _t = str(s)
    if not re.fullmatch(r"-?[0-9]+", _t):  # 纯 ASCII 十进制（int(…,10) 会接受下划线/非 ASCII 数字如 3_0/١٢，ps1 [long] 绑定拒绝 → 反向裁决）
        raise argparse.ArgumentTypeError(f"非十进制整数：{s}")
    try:
        v = int(_t, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"非十进制整数：{s}")
    if not (-2**63 <= v < 2**63):
        raise argparse.ArgumentTypeError(f"超出 Int64 范围：{s}")
    return v


class _ArgParser(argparse.ArgumentParser):
    # 参数错误收敛 rc1 + 中文（对齐 check-done/check-doc-budget/detect；ps1 绑定失败亦 rc1）
    def error(self, message):
        print(f"❌ 参数错误：{message}")
        raise SystemExit(1)


LEDGER_REL = os.path.join(".story", "text-digests.json")
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-done-rules.json")


def body_globs():
    if not os.path.isfile(RULES_PATH):
        print(f"❌ 缺少规则单源：{RULES_PATH}")
        raise SystemExit(1)
    try:
        with open(RULES_PATH, encoding="utf-8-sig") as f:
            _rules = json.load(f)
    except ValueError as exc:
        print(f"❌ 规则单源解析失败：{RULES_PATH}")  # 对齐 check-done/check-doc-budget 同场景中文
        print(f"解析详情：{exc}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(_rules, dict) or "body_globs" not in _rules:
        print(f"❌ 规则单源解析失败：{RULES_PATH}（须为含 body_globs 的 JSON 对象）")  # 对齐 check-done/check-doc-budget 同场景（原 KeyError 裸崩）
        raise SystemExit(1)
    return _rules["body_globs"]


def find_body(project, ch):
    cands = []
    _proj = glob.escape(project)  # 项目路径含 []?* 时须转义：整段嵌进 glob 模式会被当字符类/通配 → 匹配不到（对齐 ps1 -LiteralPath，d49① 分叉回归）
    for g in body_globs():
        pat = os.path.join(_proj, g.replace("{nnn}", f"{ch:03d}").replace("{ch}", str(ch)))
        cands.extend(sorted(p for p in glob.glob(pat) if os.path.isfile(p)))  # 过滤目录对齐 ps1 -File（目录冒名 open 裸崩 IsADirectoryError）
    return os.path.relpath(cands[0], project).replace("\\", "/") if cands else ""


def digest_of(path):
    with open(path, "rb") as f:
        data = f.read()
    # 已知口径：PS5.1（.NET Framework）对个别非法 UTF-8 序列（如 E0 80）替换粒度少 1 个 U+FFFD → units 展示可差 1；sha256 为字节级不受影响，verify 语义三引擎一致
    units = len(data.decode("utf-8", errors="replace").replace("\r\n", "\n").encode("utf-16-le")) // 2
    return hashlib.sha256(data).hexdigest(), units


def ledger_path(project):
    return os.path.join(project, LEDGER_REL)


def load_ledger(project):
    p = ledger_path(project)
    if not os.path.exists(p):
        return {"version": 1, "chapters": {}}
    try:
        with open(p, encoding="utf-8-sig") as f:
            data = json.load(f)
    except ValueError as exc:
        print(f"❌ 账本 JSON 损坏：{p}（可删 .story/text-digests.json 重新 digest 生成）")  # 分隔符固定正斜杠（os.path.join 在 Win 产反斜杠，与 ps1 文案不一致）
        print(f"解析详情：{exc}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(data, dict):
        print(f"❌ 账本须为 JSON 对象：{p}（顶层非对象，如数组/标量）")  # 对齐 check-done 规则单同款判型（原 TypeError 裸崩 / ps1 误判「无记录」rc2）
        raise SystemExit(1)
    chs = data.get("chapters")
    if not isinstance(chs, dict):
        print(f"❌ 账本须为 JSON 对象：{p}（chapters 非对象，如 null/数组/标量）")  # 判型 fail-closed（原 KeyError 裸崩 / ps1 fail-open 重写账本丢历史条目）
        raise SystemExit(1)
    for _k, _rec in chs.items():
        if (not _k.isdigit() or not _k.isascii()) or int(_k) > 9223372036854775807:
            print(f"❌ 账本条目非法：chapters.{_k} 键须为纯数字章号（先删账本重跑 digest）")  # 键判型 fail-closed；isascii 排 Unicode 数字族；超 Int64 挡（ps1 [long] 裸崩、py 渲染正常为分叉）
            raise SystemExit(1)
        _units = _rec.get("units") if isinstance(_rec, dict) else None
        if (not isinstance(_rec, dict) or not isinstance(_rec.get("sha256"), str)
                or not isinstance(_rec.get("units"), int) or isinstance(_rec.get("units"), bool)  # bool 是 int 子类，须显式排除（ps1 判型拒 → 分叉）
                or (isinstance(_units, int) and not (-9223372036854775808 <= _units <= 9223372036854775807))  # Int64 上界对齐 ps1 [long] 判型与 check-doc-budget _in_i64 范式（py 原放行进 verify 判「手改」语义分叉）
                or not isinstance(_rec.get("file"), str)):
            print(f"❌ 账本条目非法：chapters.{_k} 须含 str sha256 / int units / str file（先删账本重跑 digest）")  # 条目 schema fail-closed（原 KeyError/TypeError 裸崩；IsADirectoryError 目录落盘）
            raise SystemExit(1)
    return data


def save_ledger(project, ledger):
    p = ledger_path(project)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, p)  # 原子替换


def short(sha):
    return sha[:12]


def main():
    ap = _ArgParser()
    ap.add_argument("command", choices=["digest", "verify", "verify-all"])
    ap.add_argument("--project", "-p", required=True)
    ap.add_argument("--chapter", "-c", type=_i64, default=0)
    a = ap.parse_args()

    ledger = load_ledger(a.project)
    if a.command == "digest":
        rel = find_body(a.project, a.chapter)
        if not rel:
            print(f"【文本指纹】第{a.chapter:03d} 正文文件未找到（glob：正文/第{a.chapter:03d}章_*.txt）")
            return 2
        sha, units = digest_of(os.path.join(a.project, rel))
        ledger["chapters"][f"{a.chapter:03d}"] = {"sha256": sha, "units": units, "file": rel}
        save_ledger(a.project, ledger)
        print(f"【文本指纹】第{a.chapter:03d} → sha256={short(sha)} | 单元={units} | 文件={rel} | 已记录")
        return 0

    if a.command == "verify":
        rec = ledger["chapters"].get(f"{a.chapter:03d}")
        if not rec:
            print(f"【手改检测】第{a.chapter:03d} 无指纹记录 → 先跑 digest")
            return 2
        rel = find_body(a.project, a.chapter) or rec.get("file", "")
        fp = os.path.join(a.project, rel)
        if not os.path.isfile(fp):  # 目录按未找到处理对齐 ps1（open 目录裸 IsADirectoryError）
            print(f"【手改检测】第{a.chapter:03d} 正文文件未找到（glob：正文/第{a.chapter:03d}章_*.txt）")
            return 2
        sha, units = digest_of(fp)
        if sha == rec["sha256"]:
            print(f"【手改检测】第{a.chapter:03d} 一致（sha256={short(sha)} | 单元={units}）")
            return 0
        diff = units - rec["units"]
        print(
            f"【手改检测】第{a.chapter:03d} 与记录不一致 —— 旧 sha256={short(rec['sha256'])}/单元={rec['units']}，"
            f"新 sha256={short(sha)}/单元={units}（差 {diff:+d}）→ 必须停下询问：沿用旧稿/按新稿重写/只看不处理（03_26 五·文本指纹）"
        )
        return 1

    # verify-all
    ok_n = bad_n = miss_n = 0
    for k in sorted(ledger["chapters"].keys()):
        rec = ledger["chapters"][k]
        rel = find_body(a.project, int(k)) or rec.get("file", "")
        fp = os.path.join(a.project, rel)
        if not os.path.isfile(fp):  # 目录按未找到处理对齐 ps1（open 目录裸 IsADirectoryError）
            print(f"【手改检测】第{int(k):03d} 正文文件未找到（记录：{rec.get('file','')}）")
            bad_n += 1
            continue
        sha, _ = digest_of(fp)
        if sha == rec["sha256"]:
            ok_n += 1
        else:
            bad_n += 1
            print(f"【手改检测】第{int(k):03d} 与记录不一致（旧 {short(rec['sha256'])} / 新 {short(sha)}）→ 停下询问：沿用/重写/只看")
    print(f"【手改检测】全书：一致 {ok_n} 章 / 不一致 {bad_n} 章")
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())
