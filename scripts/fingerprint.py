#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全能小说作家 - 正文分布统计脚本（python 版，与 fingerprint.pl / fingerprint.ps1 同口径）
用法: python fingerprint.py <正文.txt>
输出: 句长/段长/对话占比/标点/修饰密度分布（对照 references/高级写作技巧-整合版.md 实测指纹区间判定）
"""
import os, re, sys, json

def u(s: str) -> int:
    """UTF-16 单元数（与 ps1 .Length / detect 的 utf-16-le 口径一致；含非 BMP 字符时≠ len()）。"""
    return len(s.encode("utf-16-le")) // 2

def main():
    if len(sys.argv) < 2:
        print("用法: python fingerprint.py <正文.txt>", file=sys.stderr); sys.exit(1)
    if len(sys.argv) > 2:
        print(f"❌ 参数错误：多余参数 {' '.join(sys.argv[2:])}", file=sys.stderr); sys.exit(1)  # 对齐 ps1 绑定失败 rc1（不再静默忽略）
    path = sys.argv[1]
    if not os.path.isfile(path):  # 与 ps1 Test-Path 同口径：缺文件同文案 exit1（不再裸 traceback）
        print(f"文件不存在: {path}", file=sys.stderr); sys.exit(1)
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:   # newline="" 保留换行原样；非 UTF-8 替换回退（对齐 ps1）
        t = f.read()
    total = u(t)
    if total == 0:
        print("文件为空", file=sys.stderr); sys.exit(1)

    # 段落（按换行切）
    paras = [x.strip() for x in t.splitlines() if x.strip()]
    pl = [u(x) for x in paras]
    pl.sort()
    if not pl:
        print("❌ 无可统计段落（正文仅含空白），无法计算段落分布")
        sys.exit(1)
    pAvg = round(sum(pl)/len(pl)); pMed = pl[len(pl)//2]
    p50 = round(100.0*sum(1 for x in pl if x<=50)/len(pl), 1)
    p100 = round(100.0*sum(1 for x in pl if x<=100)/len(pl), 1)

    # 句子（按中文句末标点切；\r\n 全删，与 perl/ps1 口径一致）
    raw = re.sub(r"\r?\n", "", t)
    sents = [x.strip() for x in re.findall(r"[^。！？!?…；;]+[。！？!?…；;]?", raw)]
    sl = [u(x) for x in sents if 0 < u(x) < 300]
    sl.sort()
    if not sl:
        print("❌ 无可统计句子（全部句子 ≥300 单元，无法计算句长分布）")
        sys.exit(1)
    sAvg = round(sum(sl)/len(sl), 1); sMed = sl[len(sl)//2]
    s12 = round(100.0*sum(1 for x in sl if x<=12)/len(sl), 1)
    s20 = round(100.0*sum(1 for x in sl if x<=20)/len(sl), 1)
    mean = sum(sl)/len(sl)
    sVar = round(sum((x-mean)**2 for x in sl)/len(sl), 1)

    # 逗号间小节（句内节奏）
    cl = [u(x.strip()) for x in re.findall(r"[^，。！？!?…；;、]+[，、]?", raw)]
    cl = [x for x in cl if x > 0]
    cAvg = round(sum(cl)/len(cl), 1) if cl else 0.0  # 空集均值 0.0（对齐 ps1 空集→0）

    # 对话占比（引号内字符）
    dq = re.findall(r"“[^”]*”|「[^」]*」", raw)
    dqPct = round(100.0*sum(u(x) for x in dq)/total, 1)

    # 每千字标点与修饰密度
    totalK = total/1000.0
    out = {
        "总字数": total, "段落数": len(paras), "句子数": len(sl),
        "段长均值": pAvg, "段长中位": pMed, "段长<=50占比": p50, "段长<=100占比": p100,
        "句长均值": sAvg, "句长中位": sMed, "句长<=12占比": s12, "句长<=20占比": s20, "句长方差": sVar,
        "逗号间均值": cAvg, "对话占比": dqPct,
        "每千字叹号": round(len(re.findall(r"！", raw))/totalK, 1),
        "每千字问号": round(len(re.findall(r"？", raw))/totalK, 1),
        "每千字省略号": round(len(re.findall(r"……", raw))/totalK, 1),
        "每千字的": round(len(re.findall(r"的", raw))/totalK, 1),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
