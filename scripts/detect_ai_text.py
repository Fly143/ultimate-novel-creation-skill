#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""detect_ai_text.py —— AI 文本启发式检测（本地辅助，P1-8）。

检测五项：[A]高频套话 [B]同头连句 [C]句长均匀 [D]长句占比 [E]标点密度。
输出为固定口径报告；**不门禁、不替代朱雀三态**，0 命中≠达标。
双实现对拍口径（与 detect_ai_text.ps1 逐字节一致）：
  - 长度单位 = UTF-16 单元（py: len(text.encode('utf-16-le'))//2 == ps1: $t.Length）
  - 段落 = '\\n' 切分去空行；句子 = 。！？；… 切分去空白引号后非空
  - [C] 总体标准差/均值 <0.25（句数≥10）；[D] >60 单元句子占比 >40%（句数≥5）
  - [E] 每千单元：'…'>15 / '！'>30 / '——'>10
用法：
  python scripts/detect_ai_text.py --input 正文/第001章.md
  python scripts/detect_ai_text.py -i 正文/第001章.md --extra-lexicon 词表.txt --fail-over 3
"""
import argparse
import math
import os
import re
import sys

BASE_LEXICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai-tells.txt")
SENT_SPLIT = re.compile(r"[。！？；…]+")


def load_lexicon(paths):
    tokens = []
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    tokens.append(s)
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--extra-lexicon", default="")
    ap.add_argument("--fail-over", type=int, default=0)
    a = ap.parse_args()

    with open(a.input, encoding="utf-8") as f:
        text = f.read()
    text = text.replace("\r\n", "\n")
    units = len(text.encode("utf-16-le")) // 2
    paras = [p for p in text.split("\n") if p.strip()]
    sents = [s.strip().strip("\"'「」『』") for s in SENT_SPLIT.split(text)]
    sents = [s for s in sents if s]

    hits_total = 0
    lines = ["【AI 文本启发式检测】（本地辅助，非朱雀）", f"文件：{a.input}",
             f"字符（UTF-16 单元）：{units} ｜ 段落：{len(paras)} ｜ 句子：{len(sents)}"]

    # [A] 高频套话
    toks = load_lexicon([BASE_LEXICON] + ([a.extra_lexicon] if a.extra_lexicon else []))
    counts = [(t, text.count(t)) for t in toks]
    counts = [(t, c) for t, c in counts if c > 0]
    counts.sort(key=lambda x: (-x[1], x[0]))
    a_kinds = len(counts)
    hits_total += a_kinds
    detail = "，".join(f"{t}×{c}" for t, c in counts) if counts else "（无）"
    lines.append(f"[A] 高频套话：{a_kinds} 种 / 共 {sum(c for _, c in counts)} 次 —— {detail}")

    # [B] 同头连句
    runs = []
    i = 0
    while i < len(sents) - 2:
        k = sents[i][:2]
        j = i
        while j + 1 < len(sents) and sents[j + 1][:2] == k:
            j += 1
        if j - i >= 2 and k:
            runs.append((i + 1, j + 1, k))
            i = j + 1
        else:
            i += 1
    hits_total += len(runs)
    bdetail = "，".join(f"第{s}-{e}句同头「{k}」" for s, e, k in runs) if runs else "（无）"
    lines.append(f"[B] 同头连句（连续≥3句同起2字）：{len(runs)} 处 —— {bdetail}")

    # [C] 句长均匀
    c_hit = 0
    if len(sents) >= 10:
        lens = [len(s.encode("utf-16-le")) // 2 for s in sents]
        mean = sum(lens) / len(lens)
        var = sum((x - mean) ** 2 for x in lens) / len(lens)
        ratio = math.sqrt(var) / mean if mean > 0 else 0.0
        if ratio < 0.25:
            c_hit = 1
    else:
        ratio = 0.0
    hits_total += c_hit
    lines.append(f"[C] 句长均匀（std/mean）：{ratio:.3f}（阈值<0.25 命中，句数<10 不判）→ {'命中' if c_hit else '未命中'}")

    # [D] 长句占比
    d_hit = 0
    pct = 0.0
    if len(sents) >= 5:
        pct = 100.0 * sum(1 for s in sents if len(s.encode("utf-16-le")) // 2 > 60) / len(sents)
        if pct > 40.0:
            d_hit = 1
    hits_total += d_hit
    lines.append(f"[D] 长句占比（>60 单元）：{pct:.1f}%（阈值>40% 命中，句数<5 不判）→ {'命中' if d_hit else '未命中'}")

    # [E] 标点密度（每千单元）
    e_hit = 0
    if units > 0:
        d1 = 1000.0 * text.count("…") / units
        d2 = 1000.0 * text.count("！") / units
        d3 = 1000.0 * text.count("——") / units
    else:
        d1 = d2 = d3 = 0.0
    e1h, e2h, e3h = (1 if d1 > 15 else 0), (1 if d2 > 30 else 0), (1 if d3 > 10 else 0)
    e_hit = e1h + e2h + e3h
    hits_total += e_hit
    lines.append(
        f"[E] 标点密度（每千单元）：省略号 {d1:.1f}（>15 命中）/ 感叹号 {d2:.1f}（>30 命中）/ 双破折号 {d3:.1f}（>10 命中）→ {e_hit} 项命中"
    )

    lines.append(f"总命中：{hits_total}")
    lines.append("说明：本地启发式仅供参考，不构成朱雀结论；0 命中≠达标，达标以用户回传朱雀三态为准。")
    sys.stdout.write("\n".join(lines) + "\n")
    if a.fail_over > 0 and hits_total >= a.fail_over:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
