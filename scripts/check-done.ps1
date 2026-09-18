# ============================================================
# 全能小说作家 - 章节 .done 客观核验脚本
# 用途：按项目侧 `.done/` 空标记清单核验第 N 章写后流水线是否闭环。
# 原则：闭环判定只认文件系统标记，不读正文、不代替主代理补跑流程。
# 软校验：标记齐全时，额外 warn 审核报告是否含「人工证据包」节
#         （防「只造空标记不干活」的假闭环；默认不阻断）。
# 注意：`.done_zhuque` 存在 = 4.1b–4.1e 步骤已登记；
#       不等于朱雀三态已「人工 > 疑似」达标（目标仍以用户回传为准）。
# 用法：
#   powershell -File scripts/check-done.ps1 -Project <书名目录> -Chapter 12
#   pwsh scripts/check-done.ps1 -Project .\我的书 -Chapter 10
#   powershell -File scripts/check-done.ps1 -Project <书名> -Chapter 12 -StrictReport
# 退出码：0=必需标记齐；1=缺标记/路径错误（-StrictReport 时报告软校验失败亦 exit 1）
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][int]$Chapter,
    [switch]$IncludeChapterDone,
    [switch]$StrictReport
)

$errors = @()
$warns = @()

if ($Chapter -lt 1) {
    Write-Output ('❌ 章节号无效：{0}（应为 ≥1 的整数）' -f $Chapter)
    exit 1
}

$ProjectPath = $Project
if (-not [System.IO.Path]::IsPathRooted($ProjectPath)) {
    $ProjectPath = Join-Path (Get-Location).Path $Project
}
if (-not (Test-Path -LiteralPath $ProjectPath)) {
    Write-Output ('❌ 项目目录不存在：{0}' -f $ProjectPath)
    exit 1
}

$doneDir = Join-Path $ProjectPath '.done'
if (-not (Test-Path -LiteralPath $doneDir)) {
    Write-Output ('❌ 缺少 .done 目录：{0}（项目可能未初始化写后流水线）' -f $doneDir)
    exit 1
}

$nnn = '{0:d3}' -f $Chapter
$required = @(
    'wordcount',
    'bible',
    'summary',
    'zhuque',
    'gates',
    'constraints',
    'logic_causal',
    'review',
    'quality',
    'coherence',
    'rhythm',
    'merge'
)
if (($Chapter % 10) -eq 0) {
    $required += 'innovation'
}

$present = @()
$missing = @()
foreach ($mark in $required) {
    $name = '第{0}章_{1}.done' -f $nnn, $mark
    $file = Join-Path $doneDir $name
    if (Test-Path -LiteralPath $file) { $present += $mark } else { $missing += $mark }
}

$chapterName = '第{0}章_chapter.done' -f $nnn
$chapterFile = Join-Path $doneDir $chapterName
$hasChapter = Test-Path -LiteralPath $chapterFile
$hasZhuque = $present -contains 'zhuque'

# 报告软校验：标记齐全/含 zhuque 时，看审核报告是否写了「人工证据包」
$reportStatus = 'skipped'
if ($hasZhuque) {
    $reportPath = Join-Path $ProjectPath ('报告/第{0}章_全量审核报告.md' -f $nnn)
    if (-not (Test-Path -LiteralPath $reportPath)) {
        $reportStatus = 'missing'
        $msg = ('软校验：存在 .done_zhuque，但未找到审核报告（疑似只造标记未写报告）：{0}' -f $reportPath)
        if ($StrictReport) { $errors += $msg } else { $warns += $msg }
    }
    else {
        $enc = New-Object System.Text.UTF8Encoding($false)
        $reportText = [System.IO.File]::ReadAllText($reportPath, $enc)
        if ($reportText -notmatch '人工证据包') {
            $reportStatus = 'no-section'
            $msg = ('软校验：审核报告未出现「人工证据包」节标题（4.1e 可能未真正执行）：{0}' -f $reportPath)
            if ($StrictReport) { $errors += $msg } else { $warns += $msg }
        }
        else {
            $reportStatus = 'ok'
        }
    }
}

Write-Output ('【check-done】项目={0} 章节=第{1} 章' -f $ProjectPath, $nnn)
Write-Output ('必需标记数：{0}（10 倍数章含 innovation）' -f $required.Count)
if ($present.Count) { $presentText = $present -join ', ' } else { $presentText = '（无）' }
if ($missing.Count) { $missingText = $missing -join ', ' } else { $missingText = '（无）' }
Write-Output ('已有：{0}' -f $presentText)
Write-Output ('缺失：{0}' -f $missingText)
Write-Output ('chapter.done：{0}' -f $(if ($hasChapter) { '存在' } else { '不存在' }))
Write-Output ('报告软校验：{0}' -f $reportStatus)

if ($missing.Count -gt 0) {
    $errors += ('未闭环：缺 {0} 项必需 .done：{1}' -f $missing.Count, ($missing -join ', '))
    if ($missing -contains 'zhuque') {
        $errors += '提示：缺 zhuque → 须补跑 03_26 4.1b–4.1e 人工证据包（见 modules/03_26_功能模块.md）'
    }
    if ($hasChapter) {
        $errors += '异常：chapter.done 已存在但必需标记不齐（疑似提前闭环/标记被删）'
    }
    $errors += '语义提醒：.done 齐全 ≠ 朱雀「人工>疑似」达标；达标以用户回传三态为准'
    Write-Output ''
    Write-Output '❌ 章节未闭环：'
    $errors | ForEach-Object { Write-Output ('  ' + $_) }
    if ($warns.Count) {
        Write-Output '⚠️ 软校验警告：'
        $warns | ForEach-Object { Write-Output ('  ' + $_) }
    }
    Write-Output '下一步：主代理按漏步自愈矩阵补缺，或用户回复「补缺」/「写后」'
    exit 1
}

if ($IncludeChapterDone -and -not $hasChapter) {
    Write-Output ''
    Write-Output '❌ 必需标记已齐，但缺 chapter.done（-IncludeChapterDone）'
    if ($warns.Count) {
        Write-Output '⚠️ 软校验警告：'
        $warns | ForEach-Object { Write-Output ('  ' + $_) }
    }
    exit 1
}

if ($StrictReport -and $errors.Count -gt 0) {
    Write-Output ''
    Write-Output '❌ -StrictReport：报告软校验未通过：'
    $errors | ForEach-Object { Write-Output ('  ' + $_) }
    exit 1
}

Write-Output ''
Write-Output ('✅ 第{0}章必需 .done 已齐（允许称流水线闭环；朱雀目标仍以用户回传三态为准）' -f $nnn)
if ($warns.Count) {
    Write-Output '⚠️ 软校验警告（不阻断；可用 -StrictReport 将其视为失败）：'
    $warns | ForEach-Object { Write-Output ('  ' + $_) }
}
if (-not $hasChapter) {
    Write-Output '备注：chapter.done 尚未写入——若流水线尚未走到步骤4.4/章节档案，请继续执行；若已闭环请补写 chapter.done'
}
exit 0
