---
source: ExplosiveCoderflome/AI-Novel-Writing-Assistant（Biz Novel Studio）
form: Electron 桌面 + Express/Prisma 服务端（pnpm monorepo），LangChain + LangGraph
license: AGPL-3.0（**只借鉴方法论，不复制代码**）
---

# 来源：AI-Novel-Writing-Assistant

## 项目概况

> AI 帮你规划世界与角色、逐章写作、检查和保存，让长篇创作有一条可以继续走下去的路。长篇由**自动导演**持续推进。

**导演管线（`server/src/modules/director/steps/planDefinition.ts` 原文）**：

```
story_macro          生成故事宏观规划        （主线：整书级承诺、阶段推进、总节奏）
book_contract        生成书级创作约定
world_setup          准备本书世界
character_setup      准备角色阵容与角色资产   [gateable ✓]
volume_strategy      生成分卷策略与推进路线   [gateable ✓]
volume_beat_sheet    生成目标卷节奏板
volume_chapter_list  生成卷拆章列表
chapter_detail_bundle 细化章节任务单与执行资源 [gateable ✓]
execution_contract_sync 同步章节执行合同      [gateable ✓]
chapter_batch        执行章节生成批次        → 正文    [gateable ✓]
```

每步带 `requires → produces`（DAG 依赖）+ `needs`（如 `structured_output`）+ `overwrites`。
另有：Qdrant 向量 RAG、拆书模块、市场雷达、时间线模块、断点恢复、阶段自动重试、写法引擎（反 AI 规则）。

---

## 机制清单

### AN-1 十步产物 DAG + 人工确认门 `[收敛]`

**证据**：上述 `planDefinition.ts`（每步 `gateable: true/false`；**实测 `gateable:true` 共 5 个**：character_setup / volume_strategy / chapter_detail_bundle / execution_contract_sync / chapter_batch；`bootstrap.ts` 另有旧版 4 步计划）

**含义**：流程不是"提示词建议"，而是**显式登记的阶段 + 产物 + 依赖 + 是否需要人工确认**。
**与 OpenFic Phase0–5 关系**：同源 `[收敛]` → 合并为本 skill「阶段化流程 + 确认门」（总案 P2-10）
**落点**：`03_01`–`03_05` 显式化为阶段表

---

### AN-2 确认点可编辑（不是"点一下通过"）

**证据**：README 更新日志（2026-10-12）

> 导演 V2 在"章节任务与场景"确认时，**可直接编辑章节任务、场景和目标字数**，保存后按修改继续创作；卷纲与章节任务重新生成可附上人工修正建议。

**价值**：确认点从"审批"升级为"可改后再走"，避免"退回重来"的低效。
**落点**：`03_04` / `03_06` 确认交互（并入 P2-10）

---

### AN-3 正文质检三连 + 失败保留原稿 `[收敛]`

**证据**：README 更新日志

> 写法检查结合重复表达线索与本书叙事视角，分层展示风险；**局部修文超出允许范围或事实复验失败时保留原稿。**

链条：`起草 → 写法检查 → 局部修文（超范围→保留原稿）→ 事实复验（失败→保留原稿）→ chapter audit + state sync`

**本 skill 现状**：有 03_26 写后流水线（朱雀/一致性/记忆），但无"修文超范围/复验失败 → 回退原稿"的显式保护。
**落点**：`03_26` 写后（总案 P2-12）

---

### AN-4 生成预算 + 断点保留

**证据**：README 更新日志

> 连续生成章节前检查正文、审校、修文和资料整理的剩余用量，**接近预算上限时保留正文与恢复位置。**

**与 OH-2 关系**：OH-2 管**加载体积**（35K），本项管**生成消耗**（token 成本）→ 互补。
**落点**：并入 `scripts/doc-budget.json` 的生成预算栏 + `03_26`

---

### AN-5 阶段自动重试语义

**证据**：README 更新日志

> 导演 V2 按阶段计算自动重试，**前面阶段已经恢复成功的失败不再占用后续阶段机会**，减少规划推进中的过早暂停。

**价值**：重试预算不被无效消耗。
**落点**：`00` 错误处理节

---

### AN-6 半自动人工修订 + 建议重生成 + 失败给具体原因

**证据**：README 更新日志

> 半自动导演重新生成章节任务时，能正确读取已保存场景，避免将完整场景误判为缺失；**未填写修正建议也会重新生成，失败时展示具体原因，便于从保存进度继续**。
> 章节计划编辑保留已有正文、其他章节和归档规划，旧页面或不完整场景不能覆盖保存结果，提交失败时保留人工输入。

**落点**：`03_16` 修订优化

---

### AN-7 书级创作约定（book_contract）

**证据**：`planDefinition.ts` 第 2 步 `book_contract`「生成书级创作约定」

**与 WN-7 关系**：重叠，**只落一处**（已挂到总案 P0-2：用 WN-7 命名与形态，参考本项字段）。

---

## 与本 skill 的对比结论

| 维度 | AI-Novel-WA | 本 skill |
|---|---|---|
| 分层框架 | 主线-世界/角色-分卷-章节任务单-写作，**四层完整** | 有 |
| 流程透明度 | **阶段 + 产物 + 依赖 + 确认门全部显式** | 模块化流程，阶段门不显式 |
| 人工干预 | **确认点可编辑**；半自动人工修订 | 有确认点，无可编辑语义 |
| 质检保护 | **修文超范围/复验失败保留原稿** | ❌ 无回退保护 |
| 成本控制 | 生成预算 + 断点保留 | ❌ 无 |
| 形态 | Electron + 服务端 + Qdrant | 文件 + 脚本 |

**结论**：借它的**流程显式化与人工干预语义**（AN-1/2/6）+ **质检回退保护**（AN-3）+ **预算/重试语义**（AN-4/5）。
**形态绝不照搬**（服务端、Electron、Qdrant 均不可引入）。
