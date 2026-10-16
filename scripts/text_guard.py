#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""text_guard.py —— 正文指纹与手改保护（P1-9）。

记录：正文/第NNN章_*.txt 的 sha256 + UTF-16 单元数 → {书名}/.story/text-digests.json（临时文件+原子替换写入）。
核对：与记录不一致 → exit1 并给出「停下询问：沿用/重写/只看」处置口径（03_26 步骤7之二）。
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
import sys

LEDGER_REL = os.path.join(".story", "text-digests.json")
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-done-rules.json")


def body_globs():
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)["body_globs"]


def find_body(project, ch):
    cands = []
    for g in body_globs():
        pat = os.path.join(project, g.replace("{nnn}", f"{ch:03d}").replace("{ch}", str(ch)))
        cands.extend(sorted(glob.glob(pat)))
    return os.path.relpath(cands[0], project).replace("\\", "/") if cands else ""


def digest_of(path):
    with open(path, "rb") as f:
        data = f.read()
    units = len(data.decode("utf-8").replace("\r\n", "\n").encode("utf-16-le")) // 2
    return hashlib.sha256(data).hexdigest(), units


def ledger_path(project):
    return os.path.join(project, LEDGER_REL)


def load_ledger(project):
    p = ledger_path(project)
    if not os.path.exists(p):
        return {"version": 1, "chapters": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["digest", "verify", "verify-all"])
    ap.add_argument("--project", "-p", required=True)
    ap.add_argument("--chapter", "-c", type=int, default=0)
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
        if not os.path.exists(fp):
            print(f"【手改检测】第{a.chapter:03d} 正文文件未找到（glob：正文/第{a.chapter:03d}章_*.txt）")
            return 2
        sha, units = digest_of(fp)
        if sha == rec["sha256"]:
            print(f"【手改检测】第{a.chapter:03d} 一致（sha256={short(sha)} | 单元={units}）")
            return 0
        diff = units - rec["units"]
        print(
            f"【手改检测】第{a.chapter:03d} 与记录不一致 —— 旧 sha256={short(rec['sha256'])}/单元={rec['units']}，"
            f"新 sha256={short(sha)}/单元={units}（差 {diff:+d}）→ 必须停下询问：沿用旧稿/按新稿重写/只看不处理（03_26 步骤7之二）"
        )
        return 1

    # verify-all
    ok_n = bad_n = miss_n = 0
    for k in sorted(ledger["chapters"].keys()):
        rec = ledger["chapters"][k]
        rel = find_body(a.project, int(k)) or rec.get("file", "")
        fp = os.path.join(a.project, rel)
        if not os.path.exists(fp):
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
