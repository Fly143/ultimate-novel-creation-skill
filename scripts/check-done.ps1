# ============================================================
# 全能小说作家 - 章节 .done 客观核验脚本
# 用途：按项目侧 `.done/` 空标记清单核验第 N 章写后流水线是否闭环。
# 原则：只认文件系统标记，不读正文、不代替主代理补跑流程。
# 注意：`.done_zhuque` 存在 = 4.1b-4.1e 步骤已登记；
#       不等于朱雀三态已「人工 > 疑似」达标（目标仍以用户回传为准）。
# 用法：
#   powershell -File scripts/check-done.ps1 -Project <书名目录> -Chapter 12
#   pwsh scripts/check-done.ps1 -Project .\我的书 -Chapter 10
# 退出码：0=必需标记齐；1=缺标记或路径错误
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][int]$Chapter,
    [switch]$IncludeChapterDone
)

$errors = @()

if ($Chapter -lt 1) {
    Write-Output ('[FAIL] invalid chapter: {0}' -f $Chapter)
    exit 1
}

$ProjectPath = $Project
if (-not [System.IO.Path]::IsPathRooted($ProjectPath)) {
    $ProjectPath = Join-Path (Get-Location).Path $Project
}
if (-not (Test-Path -LiteralPath $ProjectPath)) {
    Write-Output ('[FAIL] project dir not found: {0}' -f $ProjectPath)
    exit 1
}

$doneDir = Join-Path $ProjectPath '.done'
if (-not (Test-Path -LiteralPath $doneDir)) {
    Write-Output ('[FAIL] missing .done dir: {0}' -f $doneDir)
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

Write-Output ('[check-done] project={0} chapter={1}' -f $ProjectPath, $nnn)
Write-Output ('required={0} (innovation if chapter%10==0)' -f $required.Count)
if ($present.Count) { $presentText = $present -join ', ' } else { $presentText = '(none)' }
if ($missing.Count) { $missingText = $missing -join ', ' } else { $missingText = '(none)' }
Write-Output ('present: {0}' -f $presentText)
Write-Output ('missing: {0}' -f $missingText)
if ($hasChapter) { $chText = 'yes' } else { $chText = 'no' }
Write-Output ('chapter.done: {0}' -f $chText)

if ($missing.Count -gt 0) {
    $errors += ('missing {0} required .done marks: {1}' -f $missing.Count, ($missing -join ', '))
    if ($missing -contains 'zhuque') {
        $errors += 'hint: missing zhuque -> rerun 03_26 steps 4.1b-4.1e human-evidence package'
    }
    if ($hasChapter) {
        $errors += 'warn: chapter.done exists but required marks incomplete (premature close?)'
    }
    $errors += 'semantics: done marks complete != Zhuque human>suspected pass; pass needs user feedback'
    Write-Output ''
    Write-Output '[FAIL] chapter not closed:'
    $errors | ForEach-Object { Write-Output ('  ' + $_) }
    Write-Output 'next: agent auto-remediates via leak matrix, or user says buque/xiehou'
    exit 1
}

if ($IncludeChapterDone -and -not $hasChapter) {
    Write-Output ''
    Write-Output '[FAIL] required marks ok but chapter.done missing (-IncludeChapterDone)'
    exit 1
}

Write-Output ''
Write-Output ('[OK] chapter {0} required .done marks complete (pipeline closed; Zhuque target still needs user tri-state)' -f $nnn)
if (-not $hasChapter) {
    Write-Output 'note: chapter.done not written yet - continue pipeline if needed'
}
exit 0
