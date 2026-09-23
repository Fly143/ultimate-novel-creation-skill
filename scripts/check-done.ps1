# ============================================================
# 章节 .done 客观核验（与 scripts/check-done.py 同口径）
# 【同步约定】规则源 = scripts/check-done-rules.json（单源）。
# 改规则只改 JSON，并跑 python .github/scripts/test-check-done.py 确认双入口一致。
# 标准：
# - 闭环只认空标记（size=0）；非空无效。
# - 章号：第NNN章 / 第N章。
# - 默认硬校验（有 zhuque）：报告须含「人工证据包」；结构证据 ≥2 类且含强证据；
#   报告声明段号不得超过正文段落数。-SoftReport 降为警告。
# - 默认要求 chapter.done；-NoChapterDone 仅供流水线中途检查。
# - .done_zhuque = 4.1b–4.1e 已登记；朱雀达标以用户回传三态为准。
# 用法：
#   powershell -File scripts/check-done.ps1 -Project <书名目录> -Chapter 12
#   powershell -File scripts/check-done.ps1 -Project <书名> -Chapter 12 -SoftReport
#   powershell -File scripts/check-done.ps1 -Project <书名> -Chapter 12 -NoChapterDone
#   powershell -File scripts/check-done.ps1 -Project <书名> -Chapter 12 -AllowNonEmpty
# 退出码：0=必需空标记齐且默认硬校验通过；1=失败。
# -AllowNonEmpty / -SoftReport / -NoChapterDone：闭环宣称禁止使用。
# -StrictReport / -IncludeChapterDone：兼容别名（默认已生效）。
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][int]$Chapter,
    [switch]$IncludeChapterDone,
    [switch]$StrictReport,
    [switch]$SoftReport,
    [switch]$NoChapterDone,
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

$rulesPath = Join-Path $PSScriptRoot 'check-done-rules.json'
if (-not (Test-Path -LiteralPath $rulesPath)) {
    Write-Output ('❌ 缺少规则单源：{0}' -f $rulesPath)
    exit 1
}
$rules = Get-Content -LiteralPath $rulesPath -Raw -Encoding UTF8 | ConvertFrom-Json
$required = @($rules.base_marks | ForEach-Object { [string]$_ })
if (($Chapter % 10) -eq 0) {
    $required += 'innovation'
}
$sectionTitle = [string]$rules.section_title
$minHits = [int]$rules.min_hits
$requireStrong = [bool]$rules.require_strong
$claimParaRe = [string]$rules.claim_para_pattern
$evidenceRules = @()
foreach ($r in $rules.evidence_rules) {
    $evidenceRules += @{ Name = [string]$r.name; Pattern = [string]$r.pattern; Strong = [bool]$r.strong }
}
$strongNames = @($evidenceRules | Where-Object { $_.Strong } | ForEach-Object { $_.Name })

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

function Count-BodyParagraphs {
    param([string]$Text)
    $blocks = New-Object System.Collections.Generic.List[string]
    $cur = New-Object System.Collections.Generic.List[string]
    foreach ($raw in ($Text -split "`r?`n")) {
        $line = $raw.Trim()
        if ($line -eq '') {
            if ($cur.Count -gt 0) { $blocks.Add(($cur -join "`n")); $cur.Clear() }
            continue
        }
        $cur.Add($line)
    }
    if ($cur.Count -gt 0) { $blocks.Add(($cur -join "`n")) }
    if ($blocks.Count -eq 0) { return 0 }
    $start = 0
    if ($blocks.Count -gt 1) {
        $first = $blocks[0]
        if ($first.Length -gt 1 -and ($first.StartsWith('第') -or $first.Contains('章'))) {
            $start = 1
        }
    }
    $body = @()
    for ($i = $start; $i -lt $blocks.Count; $i++) { $body += $blocks[$i] }
    if ($blocks.Count -eq 1 -and $blocks[0].Contains("`n")) {
        $lines = @($blocks[0] -split "`n" | Where-Object { $_.Trim() -ne '' })
        if ($lines.Count -gt 1) {
            if ($lines[0].StartsWith('第')) { return [Math]::Max(0, $lines.Count - 1) }
            return $lines.Count
        }
    }
    return $body.Count
}

function Find-BodyFile {
    param([string]$ProjectRoot, [string]$Nnn, [int]$Ch)
    $bodyDir = Join-Path $ProjectRoot '正文'
    if (-not (Test-Path -LiteralPath $bodyDir)) { return $null }
    foreach ($prefix in @(('第{0}章' -f $Nnn), ('第{0}章' -f $Ch))) {
        $matches = @(Get-ChildItem -LiteralPath $bodyDir -Filter ($prefix + '*.txt') -File -ErrorAction SilentlyContinue | Sort-Object Name)
        if ($matches.Count -gt 0) { return $matches[0].FullName }
    }
    return $null
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

# 报告硬校验（有 zhuque）：标题 + 结构证据 + 正文段号交叉
$reportStatus = 'skipped'
$reportMsgs = @()

if ($hasZhuque) {
    $reportDir = Join-Path $ProjectPath '报告'
    $reportResolved = Resolve-ChapterFile -BaseDir $reportDir -Nnn $nnn -Ch $Chapter -Suffix '_全量审核报告.md'
    if ($reportResolved.Warnings.Count) { $nameConflicts += $reportResolved.Warnings }
    $reportPath = $reportResolved.Path
    $reportCandidates = $reportResolved.Candidates
    if ($null -eq $reportPath -or -not (Test-Path -LiteralPath $reportPath)) {
        $reportStatus = 'missing'
        $expected = ($reportCandidates | ForEach-Object { Join-Path $reportDir $_ }) -join ' / '
        $reportMsgs += ('报告校验：存在 .done_zhuque，但未找到审核报告（疑似只造标记未写报告）：{0}' -f $expected)
    }
    else {
        $reportText = [System.IO.File]::ReadAllText($reportPath, $enc)
        if (-not $reportText.Contains($sectionTitle)) {
            $reportStatus = 'no-section'
            $reportMsgs += ('报告校验：审核报告未出现「{0}」节标题（4.1e 可能未真正执行）：{1}' -f $sectionTitle, $reportPath)
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
            if (($hits.Count -lt $minHits) -or ($requireStrong -and $strongHits.Count -lt 1)) {
                if ($hits.Count) { $hitTxt = ($hits -join ', ') } else { $hitTxt = '（无）' }
                if ($strongHits.Count) { $strongTxt = ($strongHits -join ', ') } else { $strongTxt = '（无）' }
                $reportStatus = 'weak-section'
                $strongList = $strongNames -join ' / '
                $reportMsgs += ('报告校验：报告含「人工证据包」标题但结构证据不足（证据类别命中 {0}/≥{1} 且须含强证据：{2}；强证据=人工段/人工注入/段落位置与段号或字数邻接）。命中类别：{3}；强证据：{4} → {5}' -f $hits.Count, $minHits, $strongList, $hitTxt, $strongTxt, $reportPath)
            }
            else {
                $bodyFile = Find-BodyFile -ProjectRoot $ProjectPath -Nnn $nnn -Ch $Chapter
                $bodyFail = $false
                if ($null -ne $bodyFile) {
                    $paraMatches = [regex]::Matches($reportText, $claimParaRe)
                    $maxClaim = 0
                    foreach ($m in $paraMatches) {
                        $n = 0
                        if ([int]::TryParse($m.Groups[1].Value, [ref]$n)) {
                            if ($n -gt $maxClaim) { $maxClaim = $n }
                        }
                    }
                    if ($maxClaim -gt 0) {
                        $bodyText = [System.IO.File]::ReadAllText($bodyFile, $enc)
                        $paraN = Count-BodyParagraphs -Text $bodyText
                        if ($paraN -gt 0 -and $maxClaim -gt $paraN) {
                            $bodyFail = $true
                            $reportStatus = 'body-mismatch'
                            $reportMsgs += ('正文交叉校验：报告声明最大段号 第{0}段，但正文仅约 {1} 段（报告段号必须落在正文范围内）→ {2}' -f $maxClaim, $paraN, $bodyFile)
                        }
                    }
                }
                if (-not $bodyFail) { $reportStatus = 'ok' }
            }
        }
    }
}

$nnnOut = $nnn
Write-Output ('【check-done】项目={0} 章节=第{1}章' -f $ProjectPath, $nnnOut)
$chapterReqTxt = 'required'
if ($NoChapterDone) { $chapterReqTxt = 'skipped' }
$reportModeTxt = 'hard'
if ($SoftReport) { $reportModeTxt = 'soft' }
Write-Output ('必需标记数：{0}（10 倍数章含 innovation；只认空标记 size=0；chapter={1}；报告={2}）' -f $required.Count, $chapterReqTxt, $reportModeTxt)
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
Write-Output ('报告校验：{0}（{1}）' -f $reportStatus, $reportModeTxt)
if ($nameConflicts.Count) {
    Write-Output '⚠️ 章号命名冲突：'
    $nameConflicts | ForEach-Object { Write-Output ('  ' + $_) }
}

function Write-ReportMsgs {
    param([string]$Prefix = '⚠️ 报告校验：')
    if ($reportMsgs.Count -eq 0) { return }
    Write-Output $Prefix
    $reportMsgs | ForEach-Object { Write-Output ('  ' + $_) }
}

if (($missing.Count -gt 0) -or ($nonempty.Count -gt 0)) {
    Write-Output ''
    if ($missing.Count -gt 0) {
        Write-Output ('❌ 未闭环：缺 {0} 项必需 .done：{1}' -f $missing.Count, ($missing -join ', '))
    }
    if ($nonempty.Count -gt 0) {
        Write-Output ('❌ 未闭环：{0} 项标记非空（标准=size0）：{1}' -f $nonempty.Count, ($nonempty -join ', '))
        Write-Output '修复：清空对应 .done 文件内容（保留文件名），或删除后重写'
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
    Write-ReportMsgs -Prefix '⚠️ 报告校验诊断：'
    Write-Output '下一步：主代理按漏步自愈矩阵补缺，或用户回复「补缺」/「写后」'
    exit 1
}

if (-not $NoChapterDone -and -not $hasChapter) {
    Write-Output ''
    Write-Output '❌ 必需标记已齐，但缺有效 chapter.done（默认要求；中途检查用 -NoChapterDone）'
    Write-ReportMsgs -Prefix '⚠️ 报告校验诊断：'
    exit 1
}

$reportFail = ($reportMsgs.Count -gt 0) -and (-not $SoftReport)
if ($reportFail) {
    Write-Output ''
    Write-Output '❌ 报告校验未通过（默认硬校验；仅调试可用 -SoftReport）：'
    $reportMsgs | ForEach-Object { Write-Output ('  ' + $_) }
    exit 1
}

Write-Output ''
Write-Output ('✅ 第{0}章必需空 .done 已齐（允许称流水线闭环；朱雀目标仍以用户回传三态为准）' -f $nnnOut)
Write-Output '对外汇报硬约定：必须分列「流水线闭环」与「朱雀三态/待检测」；禁止只贴 .done 或本脚本 exit0 冒充检测通过'
if ($AllowNonEmpty) {
    Write-Output '⚠️ 已启用 -AllowNonEmpty（仅限用户调试；宿主/LLM 禁止用此开关宣称闭环）'
}
if ($SoftReport -and $reportMsgs.Count) {
    Write-Output '⚠️ 报告校验警告（-SoftReport 不阻断；闭环宣称禁止使用本开关）：'
    $reportMsgs | ForEach-Object { Write-Output ('  ' + $_) }
}
if ($NoChapterDone -and -not $hasChapter) {
    Write-Output '⚠️ 已启用 -NoChapterDone（仅限流水线中途检查；闭环宣称必须含 chapter.done）'
}
exit 0
