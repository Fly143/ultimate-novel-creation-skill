# ============================================================
# 全能小说作家 - 章节 .done 客观核验脚本
# 用途：按项目侧 `.done/` 空标记清单核验第 N 章写后流水线是否闭环。
# 原则：闭环判定只认文件系统**空标记（size=0）**，不读正文、不代替主代理补跑。
#       非空标记视为无效（疑似伪造/误写内容），计入未闭环。
# 软校验：标记齐全时，额外 warn 审核报告「人工证据包」节的**结构证据**
#         （防「只造标记/只贴标题/抄规格词表」；标题 + 结构证据类别 ≥2 类
#          且至少含一类强证据——强证据须带段号/字数结构，裸词表不算；默认不阻断）。
#         策略词（人工为主/密度自查）单独出现不算通过。
# 注意：`.done_zhuque` 存在 = 4.1b–4.1e 步骤已登记；
#       不等于朱雀三态已「人工 > 疑似」达标（目标仍以用户回传为准）。
# 用法：
#   powershell -File scripts/check-done.ps1 -Project <书名目录> -Chapter 12
#   pwsh scripts/check-done.ps1 -Project .\我的书 -Chapter 10
#   powershell -File scripts/check-done.ps1 -Project <书名> -Chapter 12 -StrictReport
#   powershell -File scripts/check-done.ps1 -Project <书名> -Chapter 12 -AllowNonEmpty
# 退出码：0=必需空标记齐；1=缺标记/非空标记/路径错误（-StrictReport 时报告软校验失败亦 exit 1）
# 章号兼容：标记/报告同时接受 第NNN章 与 第N章；两者同时存在时告警并优先零填充
# -AllowNonEmpty：仅限用户本地调试。宿主/LLM 做闭环核验或对外宣称时禁止携带。
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][int]$Chapter,
    [switch]$IncludeChapterDone,
    [switch]$StrictReport,
    [switch]$AllowNonEmpty
)

$enc = New-Object System.Text.UTF8Encoding($false)

function Test-MarkerState {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 'missing' }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) { return 'nonempty' }
    if ($item.Length -eq 0) { return 'present' }
    return 'nonempty'
}

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

function Get-ChapterCandidates {
    param([string]$Nnn, [int]$Ch, [string]$Suffix)
    $candidates = @(
        ('第{0}章{1}' -f $Nnn, $Suffix)
    )
    $unpadded = ('第{0}章{1}' -f $Ch, $Suffix)
    if ($candidates -notcontains $unpadded) { $candidates += $unpadded }
    return ,$candidates
}

function Resolve-ChapterFile {
    param([string]$BaseDir, [string]$Nnn, [int]$Ch, [string]$Suffix)
    $names = Get-ChapterCandidates -Nnn $Nnn -Ch $Ch -Suffix $Suffix
    $found = @()
    foreach ($name in $names) {
        $file = Join-Path $BaseDir $name
        if (Test-Path -LiteralPath $file) { $found += $file }
    }
    $warnings = @()
    if ($found.Count -gt 1) {
        $nameList = ($found | ForEach-Object { Split-Path $_ -Leaf }) -join ', '
        $warnings += ('章号命名冲突：同时存在 {0}（默认优先零填充）→ {1}' -f $nameList, $BaseDir)
    }
    $primary = $null
    if ($found.Count -gt 0) { $primary = $found[0] }
    return @{ Path = $primary; Warnings = $warnings; Candidates = $names }
}

function Test-MarkerByChapter {
    param([string]$DoneDir, [string]$Nnn, [int]$Ch, [string]$Suffix)
    $resolved = Resolve-ChapterFile -BaseDir $DoneDir -Nnn $Nnn -Ch $Ch -Suffix $Suffix
    if ($null -eq $resolved.Path) {
        return @{ State = 'missing'; Warnings = $resolved.Warnings }
    }
    return @{ State = (Test-MarkerState -Path $resolved.Path); Warnings = $resolved.Warnings }
}

$present = @()
$nonempty = @()
$missing = @()
$nameConflicts = @()
foreach ($mark in $required) {
    $r = Test-MarkerByChapter -DoneDir $doneDir -Nnn $nnn -Ch $Chapter -Suffix ('_{0}.done' -f $mark)
    if ($r.Warnings.Count) { $nameConflicts += $r.Warnings }
    $state = $r.State
    if ($state -eq 'present') {
        $present += $mark
    }
    elseif ($state -eq 'nonempty') {
        if ($AllowNonEmpty) { $present += $mark } else { $nonempty += $mark }
    }
    else {
        $missing += $mark
    }
}

$chapterResolved = Test-MarkerByChapter -DoneDir $doneDir -Nnn $nnn -Ch $Chapter -Suffix '_chapter.done'
if ($chapterResolved.Warnings.Count) { $nameConflicts += $chapterResolved.Warnings }
$chapterState = $chapterResolved.State
$hasChapter = ($chapterState -eq 'present') -or ($chapterState -eq 'nonempty' -and $AllowNonEmpty)
$hasZhuque = $present -contains 'zhuque'

# 报告软校验：有效 zhuque 空标记存在时，看「人工证据包」节是否有结构证据（≥2类且含强证据）
$reportStatus = 'skipped'
$reportMsgs = @()
# 结构证据规则：强证据必须带段号/字数结构，防止抄规格词表假闭环
$evidenceRules = @(
    @{ Name = '人工段-段号'; Pattern = '人工(?:段|注入)[^\n]{0,48}?第\s*\d+\s*段'; Strong = $true },
    @{ Name = '人工段-字数'; Pattern = '人工(?:段|注入)[^\n]{0,48}?(?:约|合计|共|至少|超过|≥|>)\s*\d+\s*字'; Strong = $true },
    @{ Name = '段落位置-段号'; Pattern = '段落位置[^\n]{0,24}?第\s*\d+\s*段'; Strong = $true },
    @{ Name = '规模字数'; Pattern = '(?:合计|约|共|至少|超过)[^\n]{0,6}\d{3,}\s*字'; Strong = $true },
    @{ Name = '规模-≥300字'; Pattern = '(?:≥|>)\s*300\s*字'; Strong = $true },
    @{ Name = '结构破坏'; Pattern = '结构破坏'; Strong = $false },
    @{ Name = '对话毛刺'; Pattern = '对话毛刺'; Strong = $false },
    @{ Name = '高疑似段'; Pattern = '高疑似段'; Strong = $false },
    @{ Name = '密度自查'; Pattern = '密度自查'; Strong = $false }
)
$strongNames = @($evidenceRules | Where-Object { $_.Strong } | ForEach-Object { $_.Name })

if ($hasZhuque) {
    $reportDir = Join-Path $ProjectPath '报告'
    $reportResolved = Resolve-ChapterFile -BaseDir $reportDir -Nnn $nnn -Ch $Chapter -Suffix '_全量审核报告.md'
    if ($reportResolved.Warnings.Count) { $nameConflicts += $reportResolved.Warnings }
    $reportPath = $reportResolved.Path
    $reportCandidates = $reportResolved.Candidates
    if ($null -eq $reportPath -or -not (Test-Path -LiteralPath $reportPath)) {
        $reportStatus = 'missing'
        $expected = ($reportCandidates | ForEach-Object { Join-Path $reportDir $_ }) -join ' / '
        $reportMsgs += ('软校验：存在 .done_zhuque，但未找到审核报告（疑似只造标记未写报告）：{0}' -f $expected)
    }
    else {
        $reportText = [System.IO.File]::ReadAllText($reportPath, $enc)
        if ($reportText -notmatch '人工证据包') {
            $reportStatus = 'no-section'
            $reportMsgs += ('软校验：审核报告未出现「人工证据包」节标题（4.1e 可能未真正执行）：{0}' -f $reportPath)
        }
        else {
            $hits = @()
            $strongHits = @()
            foreach ($rule in $evidenceRules) {
                if ([regex]::IsMatch($reportText, $rule.Pattern)) {
                    $hits += $rule.Name
                    if ($rule.Strong) { $strongHits += $rule.Name }
                }
            }
            if ($hits.Count -lt 2 -or $strongHits.Count -lt 1) {
                if ($hits.Count) { $hitTxt = ($hits -join ', ') } else { $hitTxt = '（无）' }
                if ($strongHits.Count) { $strongTxt = ($strongHits -join ', ') } else { $strongTxt = '（无）' }
                $reportStatus = 'weak-section'
                $strongList = $strongNames -join ' / '
                $reportMsgs += ('软校验：报告含「人工证据包」标题但结构证据不足（证据类别命中 {0}/≥2 且须含强证据：{1}；强证据须带段号或字数结构，裸抄规格词表/策略话术不算）。命中类别：{2}；强证据：{3} → {4}' -f $hits.Count, $strongList, $hitTxt, $strongTxt, $reportPath)
            }
            else {
                $reportStatus = 'ok'
            }
        }
    }
}
Write-Output ('【check-done】项目={0} 章节=第{1}章' -f $ProjectPath, $nnn)
Write-Output ('必需标记数：{0}（10 倍数章含 innovation；只认空标记 size=0）' -f $required.Count)
if ($present.Count) { $presentText = $present -join ', ' } else { $presentText = '（无）' }
if ($nonempty.Count) { $nonemptyText = $nonempty -join ', ' } else { $nonemptyText = '（无）' }
if ($missing.Count) { $missingText = $missing -join ', ' } else { $missingText = '（无）' }
Write-Output ('已有（空）：{0}' -f $presentText)
Write-Output ('非空无效：{0}' -f $nonemptyText)
Write-Output ('缺失：{0}' -f $missingText)
if ($chapterState -eq 'present') {
    Write-Output 'chapter.done：存在（空）'
}
elseif ($chapterState -eq 'nonempty') {
    if ($AllowNonEmpty) { Write-Output 'chapter.done：存在（非空，已兼容放行）' }
    else { Write-Output 'chapter.done：存在但非空（无效）' }
}
else {
    Write-Output 'chapter.done：不存在'
}
Write-Output ('报告软校验：{0}' -f $reportStatus)
if ($nameConflicts.Count) {
    Write-Output '⚠️ 章号命名冲突：'
    $nameConflicts | ForEach-Object { Write-Output ('  ' + $_) }
}

function Write-ReportMsgs {
    if ($reportMsgs.Count -eq 0) { return }
    Write-Output '⚠️ 软校验警告：'
    $reportMsgs | ForEach-Object { Write-Output ('  ' + $_) }
    if ($StrictReport) {
        Write-Output '（-StrictReport：报告软校验亦未通过）'
    }
}

if (($missing.Count -gt 0) -or ($nonempty.Count -gt 0)) {
    Write-Output ''
    if ($missing.Count -gt 0) {
        Write-Output ('❌ 未闭环：缺 {0} 项必需 .done：{1}' -f $missing.Count, ($missing -join ', '))
    }
    if ($nonempty.Count -gt 0) {
        Write-Output ('❌ 未闭环：{0} 项标记非空（契约=size0，疑似伪造或误写）：{1}' -f $nonempty.Count, ($nonempty -join ', '))
        Write-Output '修复：清空对应 .done 文件内容（保留文件名），或删除后由流水线重写'
    }
    if ($missing -contains 'zhuque') {
        Write-Output '提示：缺 zhuque → 须补跑 03_26 4.1b–4.1e 人工证据包（见 modules/03_26_功能模块.md）'
    }
    if ($hasChapter) {
        Write-Output '异常：chapter.done 已存在但必需标记不齐/非空（疑似提前闭环/标记被删）'
    }
    Write-Output '语义提醒：.done 齐全 ≠ 朱雀「人工>疑似」达标；达标以用户回传三态为准'
    if ($AllowNonEmpty) {
        Write-Output '⚠️ 已启用 -AllowNonEmpty（仅限用户调试；宿主/LLM 禁止用此开关宣称闭环）'
    }
    Write-ReportMsgs
    Write-Output '下一步：主代理按漏步自愈矩阵补缺，或用户回复「补缺」/「写后」'
    exit 1
}

if ($IncludeChapterDone -and -not $hasChapter) {
    Write-Output ''
    Write-Output '❌ 必需标记已齐，但缺有效 chapter.done（-IncludeChapterDone）'
    Write-ReportMsgs
    exit 1
}

if ($StrictReport -and $reportMsgs.Count -gt 0) {
    Write-Output ''
    Write-Output '❌ -StrictReport：报告软校验未通过：'
    $reportMsgs | ForEach-Object { Write-Output ('  ' + $_) }
    exit 1
}

Write-Output ''
Write-Output ('✅ 第{0}章必需空 .done 已齐（允许称流水线闭环；朱雀目标仍以用户回传三态为准）' -f $nnn)
Write-Output '对外汇报硬约定：必须分列「流水线闭环」与「朱雀三态/待检测」；禁止只贴 .done 或本脚本 exit0 冒充检测通过'
if ($AllowNonEmpty) {
    Write-Output '⚠️ 已启用 -AllowNonEmpty（仅限用户调试；宿主/LLM 禁止用此开关宣称闭环）'
}
if ($reportMsgs.Count) {
    Write-Output '⚠️ 软校验警告（不阻断；可用 -StrictReport 将其视为失败）：'
    $reportMsgs | ForEach-Object { Write-Output ('  ' + $_) }
}
if (-not $hasChapter) {
    Write-Output '备注：chapter.done 尚未写入——若流水线尚未走到步骤4.4/章节档案，请继续执行；若已闭环请补写空 chapter.done'
}
exit 0
