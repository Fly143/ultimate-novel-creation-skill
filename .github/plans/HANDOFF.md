# 移交说明（HANDOFF）

> 本包用途：把「从 6 个 GitHub 项目移植机制进 `ultimate-novel-creation-skill`」的完整方案，
> 带到**另一个环境**开工实施。

## 一、这个包里有什么

```
novel-skill-mechanism-port/
├── HANDOFF.md                    ← 本文件（先读这个）
├── manifest.json                 ← 23 项任务的机器可读清单（19 主清单 + 4 补录，status 已回填 completed，可导入任务系统）
├── README.md                     ← 文件索引 + 状态表
├── 2026-10-15-总案.md             ← 主文档：优先级、纪律、验收、不移植清单
├── 2026-10-15-执行清单.md         ← 23 项可勾选清单（19 主清单 + 4 补录；人读版）
├── 2026-10-15-规格-章节任务书.md    ← P0-2 可实施规格（含模板 + 5 条回归）
├── 2026-10-15-规格-状态单真源.md    ← P0-3 可实施规格（含 schema + 6 条回归）
└── 2026-10-15-来源-*.md ×6        ← 各项目机制清单（带证据引用）
```

**只有方案文本，没有任何待移植的代码。** 所有机制需按本 skill 的形态（文件 + PowerShell/Python 脚本）重新实现。

## 二、开工前环境准备

### 2.1 目标仓库

```bash
git clone https://github.com/Fly143/ultimate-novel-creation-skill.git
cd ultimate-novel-creation-skill
git checkout -b feat/mechanism-port        # 建议开分支，逐项提交
```

### 2.2 运行时依赖

| 依赖 | 用途 | 必需性 |
|---|---|---|
| Python 3.8+ | `scripts/check-done.py`、新增的 `state_commit.py`、`check-doc-budget.py` | **必需**（本 skill 的 py 实现） |
| Node.js | `check-ai-patterns.js` 类检测器（若按 P1-8 引入） | P1-8 时需要 |
| PowerShell (pwsh) | `audit.ps1`、`check-done.ps1`、新增脚本的 ps1 实现 | **建议装 pwsh ≥7**（2026-10-16 起仓库 `.ps1` 已全量补 UTF-8 BOM，Windows PowerShell 5.1 亦可直跑——5.1 实测 audit 全开关与回归均过；pwsh≥7 为推荐项）；本机无 pwsh → `winget install Microsoft.PowerShell`，或审计交 CI（`audit.yml`）跑 |

自检命令：

```bash
python -V                       # 期望 ≥3.8（版本随 PATH 而异，本机 3.11.16/3.14.7 并存；python3 无可用实现——WindowsApps 存根可被 which 命中但 rc=49 无输出）
node -v                         # 期望 ≥16（P1-8 时需要）
pwsh -v                         # 期望 ≥7（本机实测缺失，建议装；2026-10-16 起无 pwsh 时 5.1 亦可直跑，CI 兜底）
ls modules modules/02_功能模块索引.md scripts/ .github/scripts/
```

### 2.3 基线快照（**动手前先做**）

```bash
# 1) 现有门禁是否全绿（确认起点干净）
python .github/scripts/test-check-done.py                # 期望全绿（2026-10-20 终态 469 项 = py/ps1 双引擎；无 pwsh 时 ps1 引擎由系统 PowerShell 5.1 承担——探测顺序 pwsh→powershell，仅当机器完全无 PowerShell 时才单跑 py）
pwsh .github/scripts/audit.ps1 -Root . -StrictOrphan -LintTerm -LintLineRef -LintBudget   # -LintOrder 默认 $true，禁裸传；-File 形态勿传 $false（PS5.1 -File 绑定失败；仅 pwsh7 -File 冒号形态 `-LintOrder:$false` 可用、空格形态亦 rc1；需关一律用 -Command 形态）

# 2) 记录现状指标（P0-4 需要基线数据）
python - <<'PY'
import glob, os
for pat in ("SKILL.md","system_prompt.md","modules/*.md","references/**/*.md","templates/**/*.md"):
    tot = sum(len(open(f,encoding="utf-8").read().replace(" ","").replace("\n","").replace("\t",""))
              for f in glob.glob(pat, recursive=True))
    print(f"{pat:28s} {tot:>10,} 字符")
PY
```

把第 2 步的输出贴进 `2026-10-15-执行清单.md` 的 P0-4 条目下——**没有基线就无法定预算上限**。

## 三、实施顺序

```
P0（一周）  P0-1 优先级链 → P0-2 任务书(+P1-5 节拍) → P0-3 状态单真源 → P0-4 预算表
P1（第二周）P1-5 节拍 → P1-6 台账/时间线 → P1-7 作者真相 → P1-8 检测器 → P1-9 三区/事务
P2/P3       按需，见总案总表
```

**推荐第一步**：`P0-1 + P2-11`（优先级裁决链 + 主控三原则）——纯文档、2 小时、立刻见效，且能先把"冲突行为可复现"这条基线立住。

## 四、每项完成的九步纪律（总案 §四 摘要）

```
1 改文件
2 新文件 → modules/02_功能模块索引.md
3 新命令 → modules/00_强制执行协议.md 命令表
4 新触发词 → SKILL.md frontmatter
5 新硬规则 → system_prompt.md
6 写 ADR（P2-15 之后强制）
7 python .github/scripts/test-check-done.py     全绿
8 check-doc-budget（P0-4 之后）           PASS
9 更新 README.md
```

**每条都要跑**：`pwsh .github/scripts/audit.ps1 -Root . -StrictOrphan -LintTerm -LintLineRef -LintBudget`（`-LintOrder` 默认开启；需显式关在 `-Command`/交互式下写 `-LintOrder $false`，`-File` 形态省略）

## 五、三条不可破的约束（违反即返工）

1. **不引入宿主依赖** —— 只用「文件 + PowerShell/Python」。凡需要 hooks / 常驻服务 / 图编排框架的形态，**只搬方法论、改写成脚本**。
2. **不新开二级跳转** —— 新机制必须挂进既有权威文件：
   - 步骤权威 = `modules/03_26_功能模块.md`
   - 协议权威 = `modules/00_强制执行协议.md`
   否则 `audit.ps1` 的 `LintOrder`（默认开启）/ `-LintLineRef` 会红。
3. **保持 py + ps1 双实现** —— 这是本 skill 的独特优势（Windows 无 Python 也能跑门禁），新脚本必须双份，且回归对比同口径。

## 六、需要人工决策的开放问题

| # | 问题 | 建议默认 | 影响项 |
|---|---|---|---|
| 1 | 章节任务书写在**细纲头部**还是独立文件？ | 细纲头部（少一个索引登记）**【已决：P0-2 落地，四段在细纲头部】** | P0-2 |
| 2 | 状态单真源是否把「阶段/卷压缩」也纳入 state 派生？ | 先纳入摘要与圣经；阶段/卷保持现有手写（降低首期风险）**【已决：P0-3 按此落地】** | P0-3 |
| 3 | `.done` 是否新增 `state.done`（12→13 项，口径 `scripts/check-done-rules.json`）？ | 是（否则状态提交无法纳入闭环校验）**【已决：已实现，13 项口径】** | P0-3 |
| 4 | 预算上限是否直接用 oh-story 的 35000 字符？ | 先出基线，再定（可能需按本 skill 规模放宽，但必须显式记录理由）**【已决：2026-10-17 时点 48,500 = 实测 46,315 + 约4.7%（十五轮时点，现值与沿革见 doc-budget.json；十四轮 45,919、九轮 45,916、八轮 45,795、七轮 48,000/45,708、六轮 48,000/45,522、五轮 48,000/45,402、四轮 47,500/45,091、三轮 44,665、二轮 47,000/44,322），论证见 `scripts/doc-budget.json` path_ceiling.why 与实施记录§三 #1】** | P0-4 |
| 5 | 检测器用 Node（照 oh-story）还是 Python（保持双实现）？ | Python + PowerShell 双实现，与现有脚本一致**【已决：P1-8 落地】** | P1-8 |
| 6 | 是否做"发布包"（modules 镜像到 references 以便被 OpenFic 类系统导入）？ | 暂缓，P3-19 按需**【已决：P3-19 已做 -Check 双实现校验，zip 仅 py】** | P3-19 |

> 状态：六问全部已决（2026-10-16）；机器可读副本 `manifest.json.open_questions` 已同步 status。

## 七、参考：每个来源文件里能查到什么

| 需要查 | 去哪份文件 |
|---|---|
| 优先级链的准确层级与例外 | `来源-webnovel-writer.md` → WN-1 |
| 三件套/任务书六要素的原始定义 | `来源-inkos.md` → IK-1；`来源-openfic.md` → OF-3 |
| 状态单真源的原始纪律（锁、事务、迁移） | `来源-oh-story.md` → OH-1 |
| 预算表的字段语义（branches/stacking/ceiling） | `来源-oh-story.md` → OH-2 |
| 十步 DAG 与确认门 | `来源-ai-novel-writing-assistant.md` → AN-1 |
| 节拍/剧情单元切分信号 | `来源-openfic.md` → OF-1、OF-2 |
| 为什么某些东西不移植 | `2026-10-15-总案.md` §六 |

## 八、许可注意（重要）

| 来源项目 | 许可 | 允许做法 |
|---|---|---|
| oh-story-claudecode | MIT | 可自由借鉴/改写/引用，保留版权声明 |
| OpenFic | Apache-2.0 | 同上，另需保留 NOTICE 类声明 |
| inkos | **AGPL-3.0** | **只借鉴方法论描述，不得复制其代码** |
| AI-Novel-Writing-Assistant | **AGPL-3.0** | 同上 |
| webnovel-writer | GPL-3.0 | 借鉴机制；用它写小说不传染权，但**别整段复制其文本** |
| MuMuAINovel | GPL-3.0 | 仅 MU-1 一项，可自行实现 |

> 本包内**所有内容都是对机制的描述性总结、并标注了出处**，不含任何项目的源码片段。
> 实施时同理：抄机制、写自己的实现，不要复制上游代码/文本。

## 九、验收总目标

| 指标 | 目标 |
|---|---|
| 手改派生记忆文件 | `check` 必须拒绝（0 容忍） |
| 写一章的加载字符数 | 建立基线后设上限并强制 |
| 写后闭环项 | 必需标记 12 → 新架构 13 项（含 `state.done`）；`chapter.done` 另计（10 倍数章 +`innovation`）；全绿才算完成 |
| 冲突行为 | 优先级链可复现，降级必显式声明 |
| 规则膨胀 | 新规则须有 ADR + Validation |
| 既有门禁 | `test-check-done.py` 全绿 + `audit.ps1` 全开关 PASS（**不得回退**） |
