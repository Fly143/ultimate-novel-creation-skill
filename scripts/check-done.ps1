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
#   powershell -File scripts/check-done.ps1 -Project <书名> -Chapter 12 -RequireTaskSheet
# 退出码：0=必需空标记齐且默认硬校验通过；1=失败。
# -AllowNonEmpty / -SoftReport / -NoChapterDone：闭环宣称禁止使用。
# -StrictReport / -IncludeChapterDone：兼容别名（默认已生效）。
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$Chapter,
    [switch]$IncludeChapterDone,
    [switch]$StrictReport,
    [switch]$SoftReport,
    [switch]$NoChapterDone,
    [switch]$AllowNonEmpty,
    [switch]$RequireTaskSheet
)

$enc = New-Object System.Text.UTF8Encoding($false)
# 控制台输出统一 UTF-8：GBK 控制台下 ✅/❌ 才不会变 '?'
try { [System.Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
# py 口径空白全集（str.strip 含 U+001C-U+001F；.NET 默认 Trim 不含 → 必须显式给集，防与 py 反向裁决）
$wsChars = [char[]]@(0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000)
# fail-closed：读盘/解析错误必须中止并 rc1，禁止吞错继续（目录当报告等场景不得 exit0 放行）
# 参数错误收敛：纯 ASCII 十进制（[long] 会把 "1e3"/"+5"/"0x10"/" 5"/"" 静默转换，与 py _i64 反向裁决）
if ($Chapter -notmatch '^-?[0-9]+$') { [Console]::Out.WriteLine('❌ 参数错误：章节号须为纯 ASCII 十进制整数（对齐 py _i64）'); exit 1 }
$ChapterRaw = $Chapter  # param([string]) 类型约束会把赋值回炉成 String，先存原值再解约束重赋
Remove-Variable -Name Chapter -Force -ErrorAction SilentlyContinue
$_cvtChapter = 0L
if (-not [long]::TryParse($ChapterRaw, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$_cvtChapter)) { [Console]::Out.WriteLine('❌ 参数错误：超出 Int64 范围（对齐 py _i64）'); exit 1 }  # TryParse 收敛：裸 [long] cast 在 EAP Stop 前是非终止错误、变量落 $null fail-open（纯十进制超界静默放行分叉）
$Chapter = $_cvtChapter

$ErrorActionPreference = 'Stop'

function Sort-OrdinalPaths {
    # 对齐 py sorted(Path)：Windows 下 py 走 normcase（小写化）+ 码点序。
    # Sort-Object Name 走当前 culture（zh-CN 拼音序会与 py 反向）；
    # 用 Sort(keys, items, Ordinal) 双数组形态（键=小写=py normcase 口径）；不走 Sort-Object Name（culture 拼音序会与 py 反向）。
    param($Items)
    $arr = [string[]]@($Items | Where-Object { $null -ne $_ -and [string]$_ -ne '' })
    if ($arr.Count -le 1) { return ,$arr }
    $keys = [string[]]@($arr | ForEach-Object { $_.ToLowerInvariant() })
    [Array]::Sort($keys, $arr, [System.StringComparer]::Ordinal)
    return ,$arr
}

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
# 与 py expanduser 同口径：~ / ~\ / ~/ 前缀展开为用户主目录
if ($ProjectPath -eq '~') {
    $ProjectPath = $HOME
} elseif ($ProjectPath.StartsWith('~\') -or $ProjectPath.StartsWith('~/')) {
    $ProjectPath = Join-Path $HOME $ProjectPath.Substring(2)
}
if (-not [System.IO.Path]::IsPathRooted($ProjectPath)) {
    $ProjectPath = Join-Path (Get-Location).Path $ProjectPath
}
if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    Write-Output ('❌ 项目目录不存在：{0}' -f $ProjectPath)
    exit 1
}

$doneDir = Join-Path $ProjectPath '.done'
if (-not (Test-Path -LiteralPath $doneDir -PathType Container)) {
    Write-Output ('❌ 缺少 .done 目录：{0}（项目可能未初始化写后流水线）' -f $doneDir)
    exit 1
}

$nnn = '{0:d3}' -f $Chapter

$rulesPath = Join-Path $PSScriptRoot 'check-done-rules.json'
if (-not (Test-Path -LiteralPath $rulesPath -PathType Leaf)) {
    Write-Output ('❌ 缺少规则单源：{0}' -f $rulesPath)
    exit 1
}
try {
    $rawRules = Get-Content -LiteralPath $rulesPath -Raw -Encoding UTF8 -ErrorAction Stop
} catch {
    Write-Output ('❌ 规则单源解析失败：{0}' -f $rulesPath)
    [Console]::Error.WriteLine(('解析详情：{0}' -f $_.Exception.Message))
    exit 1
}
if ([string]::IsNullOrWhiteSpace($rawRules)) {
    Write-Output ('❌ 规则单源解析失败：{0}' -f $rulesPath)
    [Console]::Error.WriteLine('解析详情：Unexpected end of input（空/仅空白文件）')
    exit 1
}
# 顶层须为 JSON 对象：原始串前缀判定（PS5.1 的 -is [pscustomobject] 对数组为 True、pwsh7 为 False，不可用作判据）
if (-not $rawRules.TrimStart().StartsWith('{')) {
    Write-Output ('❌ 规则单源解析失败：{0}（须为 JSON 对象）' -f $rulesPath)
    exit 1
}
try {
    $rules = $rawRules | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-Output ('❌ 规则单源解析失败：{0}' -f $rulesPath)
    [Console]::Error.WriteLine(('解析详情：{0}' -f $_.Exception.Message))
    exit 1
}
if ($null -eq $rules) {
    Write-Output ('❌ 规则单源解析失败：{0}（须为 JSON 对象）' -f $rulesPath)
    exit 1
}
# 必填键 schema 校验（与 check-done.py load_rules 同清单同文案，fail-closed）
$rcErrs = @()
foreach ($k in @('base_marks', 'evidence_rules', 'section_title', 'min_hits', 'require_strong', 'claim_para_pattern', 'body_globs', 'task_sheet')) {
    if ($null -eq $rules.PSObject.Properties[$k]) { $rcErrs += ('缺 {0}' -f $k) }
}
if ($null -ne $rules.PSObject.Properties['base_marks']) {
    if ($rules.base_marks -isnot [array]) { $rcErrs += 'base_marks 须为数组' }
    elseif (@($rules.base_marks | Where-Object { $_ -isnot [string] }).Count -gt 0) { $rcErrs += 'base_marks 元素须为字符串' }
}
if ($null -ne $rules.PSObject.Properties['evidence_rules']) {
    if ($rules.evidence_rules -isnot [array]) { $rcErrs += 'evidence_rules 须为数组' }
    else {
        $erBad = $false
        foreach ($r in $rules.evidence_rules) {
            if ($null -eq $r -or $r -isnot [pscustomobject] -or $null -eq $r.PSObject.Properties['name'] -or $null -eq $r.PSObject.Properties['pattern'] -or $null -eq $r.PSObject.Properties['strong']) { $erBad = $true; break }
        }
        if ($erBad) {
            $rcErrs += 'evidence_rules[i] 须为含 name/pattern/strong 的对象'
        } else {
            foreach ($r in $rules.evidence_rules) {
                $ep = $r.pattern
                if ($ep -isnot [string]) { $rcErrs += 'evidence_rules[i].pattern 须为字符串' }
                elseif ($ep -match '\(\?P<|\(\?<|\(\?P=') { $rcErrs += 'evidence_rules[i].pattern 须为跨引擎安全语法（禁命名组/命名回引 (?P<、(?<、(?P=）' }
                else { try { [void][regex]::new([string]$ep) } catch { $rcErrs += 'evidence_rules[i].pattern 正则无效' } }
            }
        }
    }
}
if ($null -ne $rules.PSObject.Properties['section_title'] -and $rules.section_title -isnot [string]) { $rcErrs += 'section_title 须为字符串' }
if ($null -ne $rules.PSObject.Properties['min_hits']) {
    # 严格整数：5.1=Int32、7=Int64 放行；Decimal/Double/String/Boolean 一律拒（防 [int] 四舍五入与 py 截断反向）
    if ($rules.min_hits -isnot [int] -and $rules.min_hits -isnot [long]) { $rcErrs += 'min_hits 须为整数' }
}
if ($null -ne $rules.PSObject.Properties['claim_para_pattern']) {
    if ($rules.claim_para_pattern -isnot [string]) { $rcErrs += 'claim_para_pattern 须为字符串' }
    else {
        $cp = [string]$rules.claim_para_pattern
        # 与 py 同拒跨方言构造：命名组/命名回引双引擎不兼容，统一在双引擎 fail-closed
        if ($cp -match '\(\?P<|\(\?<|\(\?P=') { $rcErrs += 'claim_para_pattern 须为跨引擎安全语法（禁命名组/命名回引 (?P<、(?<、(?P=）' }
        else { try { [void][regex]::new($cp) } catch { $rcErrs += 'claim_para_pattern 正则无效' } }
    }
}
if ($null -ne $rules.PSObject.Properties['body_globs']) {
    if ($rules.body_globs -isnot [array]) { $rcErrs += 'body_globs 须为数组' }
    elseif (@($rules.body_globs | Where-Object { $_ -isnot [string] }).Count -gt 0) { $rcErrs += 'body_globs 元素须为字符串' }
}
if ($null -ne $rules.PSObject.Properties['task_sheet']) {
    if ($rules.task_sheet -isnot [pscustomobject]) { $rcErrs += 'task_sheet 须为对象' }
    else {
        foreach ($subSpec in @(@('dirs', 'array'), @('suffix', 'string'), @('required_sections', 'array'), @('required_item_mark', 'string'))) {
            $sub = $subSpec[0]; $typ = $subSpec[1]
            $prop = $rules.task_sheet.PSObject.Properties[$sub]
            if ($null -eq $prop) { $rcErrs += ('task_sheet 缺 {0}' -f $sub); continue }
            $v = $prop.Value
            if ($typ -eq 'array') {
                if ($v -isnot [array]) { $rcErrs += ('task_sheet.{0} 类型错误' -f $sub) }
                elseif (@($v | Where-Object { $_ -isnot [string] }).Count -gt 0) { $rcErrs += ('task_sheet.{0} 元素须为字符串' -f $sub) }
            } else {
                if ($v -isnot [string]) { $rcErrs += ('task_sheet.{0} 类型错误' -f $sub) }
            }
        }
    }
}
if ($rcErrs.Count -gt 0) {
    Write-Output ('❌ 规则单源校验失败：{0}（{1}）' -f $rulesPath, ($rcErrs -join '；'))
    exit 1
}
$required = @($rules.base_marks | ForEach-Object { [string]$_ })
if (($Chapter % 10) -eq 0) {
    $required += 'innovation'
}
$sectionTitle = [string]$rules.section_title
$minHits = [long]$rules.min_hits
$requireStrong = [bool]$rules.require_strong
$claimParaRe = [string]$rules.claim_para_pattern
$evidenceRules = @()
foreach ($r in $rules.evidence_rules) {
    $evidenceRules += @{ Name = [string]$r.name; Pattern = [string]$r.pattern; Strong = [bool]$r.strong }
}
$strongNames = @($evidenceRules | Where-Object { $_.Strong } | ForEach-Object { $_.Name })
$taskSheetDirs = @($rules.task_sheet.dirs | ForEach-Object { [string]$_ })
$stateMarkName = 'state'
$stateRel = '记忆系统/_state.json'
$stateRequireLast = $true
$stateAutoWhen = $true
if ($rules.state_mark) {
    if ($rules.state_mark.name) { $stateMarkName = [string]$rules.state_mark.name }
    if ($rules.state_mark.state_file) { $stateRel = [string]$rules.state_mark.state_file }
    if ($null -ne $rules.state_mark.require_last_committed) { $stateRequireLast = [bool]$rules.state_mark.require_last_committed }
    if ($null -ne $rules.state_mark.auto_when_state_file) { $stateAutoWhen = [bool]$rules.state_mark.auto_when_state_file }
}

$stateFile = Join-Path $ProjectPath ($stateRel -replace '/', [string][char]92)
$stateOn = $stateAutoWhen -and (Test-Path -LiteralPath $stateFile -PathType Leaf)
$stateErrs = @()
if ($stateOn) { $required += $stateMarkName }
$taskSheetSuffix = [string]$rules.task_sheet.suffix
$taskSections = @($rules.task_sheet.required_sections | ForEach-Object { [string]$_ })
$taskItemMark = [string]$rules.task_sheet.required_item_mark

function Get-ChapterCandidates {
    param([string]$Nnn, [long]$Ch, [string]$Suffix)
    $candidates = @(
        ('第{0}章{1}' -f $Nnn, $Suffix)
    )
    $unpadded = ('第{0}章{1}' -f $Ch, $Suffix)
    if ($candidates -notcontains $unpadded) { $candidates += $unpadded }
    return ,$candidates
}

function Resolve-ChapterFile {
    param([string]$BaseDir, [string]$Nnn, [long]$Ch, [string]$Suffix)
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
    param([string]$DoneDir, [string]$Nnn, [long]$Ch, [string]$Suffix)
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
    # 切行集合与 py str.splitlines() 同口径（LF/CRLF/CR/VT/FF/\x1c-\x1e/\x85/U+2028/U+2029；py 不切 \x1f），防边界反向裁决
    foreach ($raw in ($Text -split '\r\n|\r|\v|\f|[\x1c-\x1e]|\x85|\u2028|\u2029|\n')) {
        $line = $raw.Trim($wsChars)
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
        # 标题判定（与 check-done.py count_body_paragraphs 逐字同口径）：
        # 首块前 40 个 UTF-16 码元内含「第」或「章」→ 视为标题、段号去掉标题块。
        $head = $blocks[0]
        if ($head.Length -gt 40) { $head = $head.Substring(0, 40) }
        if ($head.Contains('第') -or $head.Contains('章')) { $start = 1 }
    }
    $body = @()
    for ($i = $start; $i -lt $blocks.Count; $i++) { $body += $blocks[$i] }
    if ($blocks.Count -eq 1 -and $blocks[0].Contains("`n")) {
        $lines = @($blocks[0] -split "`n" | Where-Object { $_.Trim($wsChars) -ne '' })
        if ($lines.Count -gt 1) {
            if ($lines[0].StartsWith('第')) { return [Math]::Max(0, $lines.Count - 1) }
            return $lines.Count
        }
    }
    return $body.Count
}

$bodyGlobs = @()
if ($rules.body_globs) { $bodyGlobs = @($rules.body_globs | ForEach-Object { [string]$_ }) }
function Find-BodyFile {
    param([string]$ProjectRoot, [string]$Nnn, [long]$Ch)
    $bodyDir = Join-Path $ProjectRoot '正文'
    if (-not (Test-Path -LiteralPath $bodyDir)) { return $null }
    function Guard-DirCandidate([object[]]$Dirs) {
        # 目录冒充正文 → 双引擎同文案 rc1（对齐 py find_body _guard_dir 与本文件 fail-closed 头注放行纪律）
        if ($Dirs.Count -gt 0) {
            [Console]::Out.WriteLine(('❌ 正文交叉校验：正文候选路径是目录（应为 .txt 文件）：{0}' -f [string]$Dirs[0]))
            exit 1
        }
    }
    # 正文定位单源 = rules.body_globs（与 py find_body 同序、同双尝试、同排序）
    foreach ($g in $bodyGlobs) {
        $rel = $g.Replace('{nnn}', $Nnn).Replace('{ch}', [string]$Ch)
        $idx = $rel.LastIndexOf('/')
        if ($idx -ge 0) { $dirRel = $rel.Substring(0, $idx); $filePat = $rel.Substring($idx + 1) }
        else { $dirRel = ''; $filePat = $rel }
        if ($dirRel -ne '') {
            $absDir = Join-Path $ProjectRoot ($dirRel -replace '/', [string][char]92)
            if (Test-Path -LiteralPath $absDir) {
                $gd1 = Sort-OrdinalPaths @(Get-ChildItem -LiteralPath $absDir -Filter $filePat -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
                Guard-DirCandidate $gd1
                $m = Sort-OrdinalPaths @(Get-ChildItem -LiteralPath $absDir -Filter $filePat -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
                if ($m.Count -gt 0) { return [string]$m[0] }
            }
        }
        $gd2 = Sort-OrdinalPaths @(Get-ChildItem -LiteralPath $bodyDir -Filter $filePat -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        Guard-DirCandidate $gd2
        $m2 = Sort-OrdinalPaths @(Get-ChildItem -LiteralPath $bodyDir -Filter $filePat -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        if ($m2.Count -gt 0) { return [string]$m2[0] }
    }
    # 兜底：前缀匹配（与 py find_body 一致）
    foreach ($prefix in @(('第{0}章' -f $Nnn), ('第{0}章' -f $Ch))) {
        $gd3 = Sort-OrdinalPaths @(Get-ChildItem -LiteralPath $bodyDir -Filter ($prefix + '*.txt') -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        Guard-DirCandidate $gd3
        $matches = Sort-OrdinalPaths @(Get-ChildItem -LiteralPath $bodyDir -Filter ($prefix + '*.txt') -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        if ($matches.Count -gt 0) { return [string]$matches[0] }
    }
    return $null
}

function Test-TaskSheet {
    param([string]$ProjectRoot, [string]$Nnn, [long]$Ch)
    $path = $null
    $expectedParts = @()
    foreach ($rel in $taskSheetDirs) {
        $dir = Join-Path $ProjectRoot $rel
        $resolved = Resolve-ChapterFile -BaseDir $dir -Nnn $Nnn -Ch $Ch -Suffix $taskSheetSuffix
        foreach ($cand in $resolved.Candidates) { $expectedParts += (Join-Path $dir $cand) }
        if ($null -ne $resolved.Path -and (Test-Path -LiteralPath $resolved.Path -PathType Leaf)) { $path = $resolved.Path; break }
    }
    if ($null -eq $path) {
        return @{ Status = 'missing'; Errors = @(('任务书校验：缺章节任务书（写前应构造、写后必须存在）：{0}' -f ($expectedParts -join ' / '))) }
    }
    $text = [System.IO.File]::ReadAllText($path, $enc)
    # 与 py text.splitlines() 同切行集合
    $lines = @($text -split '\r\n|\r|\v|\f|[\x1c-\x1e]|\x85|\u2028|\u2029|\n')
    $headings = @{}
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $stripped = $lines[$i].Trim($wsChars)
        foreach ($sec in $taskSections) {
            if (-not $headings.ContainsKey($sec) -and $stripped.StartsWith('### ' + $sec)) { $headings[$sec] = $i }
        }
    }
    $missingSecs = @()
    foreach ($sec in $taskSections) { if (-not $headings.ContainsKey($sec)) { $missingSecs += $sec } }
    if ($missingSecs.Count -gt 0) {
        $need = ($taskSections -join '/')
        return @{ Status = 'no-section'; Errors = @(('任务书校验：缺段 {0}（必需四段：{1}）→ {2}' -f ($missingSecs -join ', '), $need, $path)) }
    }
    if ($taskSections -notcontains 'MUST DO') {
        return @{ Status = 'config-error'; Errors = @('任务书校验：check-done-rules.json 的 task_sheet.required_sections 缺 MUST DO（节拍清单锚点不可移除/改名）→ 修改规则单源后需同步双实现测试') }
    }
    $start = [int]$headings['MUST DO']
    $end = $lines.Count
    for ($i = $start + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim($wsChars).StartsWith('### ')) { $end = $i; break }
    }
    $items = @()
    for ($i = $start + 1; $i -lt $end; $i++) {
        if ($lines[$i] -match ('^\s*-\s*\[([ xX])\][^\n]*' + [regex]::Escape($taskItemMark))) {
            $isDone = ($Matches[1].ToLower() -eq 'x')
            $items += @{ Done = $isDone; Label = $lines[$i].Trim($wsChars) }
        }
    }
    if ($items.Count -eq 0) {
        return @{ Status = 'no-item'; Errors = @(('MUST DO 缺「{0}」节拍清单项（不允许边写边想）→ {1}' -f $taskItemMark, $path)) }
    }
    $unchecked = @($items | Where-Object { -not $_.Done } | ForEach-Object { $_.Label })
    if ($unchecked.Count -gt 0) {
        return @{ Status = 'unchecked'; Errors = @(('任务书校验：漏必落拍 {0}/{1}（写后须全勾，否则本章判未完成）：{2}' -f $unchecked.Count, $items.Count, ($unchecked -join '；'))) }
    }
    return @{ Status = 'ok'; Errors = @() }
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
    if ($null -eq $reportPath -or -not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
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
                $strongClause = ''
                if ($requireStrong) { $strongClause = ' 且须含强证据：' + ($strongNames -join ' / ') }
                $reportMsgs += ('报告校验：报告含「人工证据包」标题但结构证据不足（证据类别命中 {0}/≥{1}{2}；强证据=人工段/人工注入/段落位置与段号或字数邻接）。命中类别：{3}；强证据：{4} → {5}' -f $hits.Count, $minHits, $strongClause, $hitTxt, $strongTxt, $reportPath)
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
if ($stateOn -and $stateRequireLast) {
    try {
        $stObj = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $lastRaw = $stObj.last_committed_chapter
        if ($null -eq $lastRaw -or -not ($lastRaw -is [int] -or $lastRaw -is [long])) {
            $stateErrs += ('状态单校验：last_committed_chapter 不是整数 → {0}' -f $stateFile)
        }
        elseif ([long]$lastRaw -lt $Chapter) {
            $stateErrs += ('状态单落后：last_committed_chapter={0} < 第{1}章（先 state_commit commit --chapter {1} 再闭环）' -f [long]$lastRaw, $Chapter)
        }
    }
    catch {
        $stateErrs += ('状态单校验：无法读取 {0}：{1}' -f $stateFile, $_.Exception.Message)
    }
}
if ($stateOn) { Write-Output ('状态单：required（{0} 存在）' -f $stateRel) }
elseif (-not $stateAutoWhen) { Write-Output '状态单：skipped（auto_when_state_file=false，走旧路径）' }
else { Write-Output '状态单：skipped（无 _state.json，走旧路径）' }
$taskStatus = 'skipped'
$taskErrs = @()
if ($RequireTaskSheet) {
    $taskResult = Test-TaskSheet -ProjectRoot $ProjectPath -Nnn $nnn -Ch $Chapter
    $taskStatus = $taskResult.Status
    $taskErrs = @($taskResult.Errors)
    Write-Output ('任务书校验：{0}（require-task-sheet）' -f $taskStatus)
}
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
    if ($stateOn -and ($missing -contains $stateMarkName)) {
        Write-Output ('提示：缺 {0} → 先 draft --chapter N 生成事务、填 delta，再 commit --chapter N --input <事务文件>（派生一致性用 state_commit check 核验）' -f $stateMarkName)
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

if ($stateErrs.Count -gt 0) {
    Write-Output ''
    Write-Output '❌ 状态单校验未通过：'
    $stateErrs | ForEach-Object { Write-Output ('  ' + $_) }
    exit 1
}

if ($taskErrs.Count -gt 0) {
    Write-Output ''
    Write-Output '❌ 任务书校验未通过（-RequireTaskSheet）：'
    $taskErrs | ForEach-Object { Write-Output ('  ' + $_) }
    Write-Output '修复：补写 细纲/第NNN章_细纲.md 任务书四段并勾选必落拍（模板见 templates/章节任务书模板.md）'
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
