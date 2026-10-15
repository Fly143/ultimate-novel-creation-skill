# ============================================================
# 热路径文档预算守卫（与 scripts/check-doc-budget.py 同口径）
# 规则单源 = scripts/doc-budget.json；度量 = 去掉所有空白后的字符数。
# - 文件条目支持 `路径#小节标题`（只计该小节，到下个同级/更高级标题为止；代码围栏里的 # 不算）。
# - paths[].branches 互斥取最大；paths[].stacking 叠加取全和；path_ceiling.exempt 内 label 不判上限。
# - generated 条目由脚本产出，不手测（只报登记状态）。
# 用法：
#   pwsh scripts/check-doc-budget.ps1 [-Root <仓库根>] [-PrintBaseline]
# 退出码：0=全部在预算内；1=超预算/缺文件/规则缺失。
# ============================================================
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$PrintBaseline
)

# 输出统一 UTF-8（Windows 中文控制台默认 GBK 会把标记打成 ?；CI 上为无害 no-op）
try { [System.Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$enc = New-Object System.Text.UTF8Encoding($false)
$budgetPath = Join-Path $PSScriptRoot 'doc-budget.json'
if (-not (Test-Path -LiteralPath $budgetPath)) {
    Write-Output ("❌ 缺少规则单源：{0}" -f $budgetPath)
    exit 1
}
$rules = Get-Content -LiteralPath $budgetPath -Raw -Encoding UTF8 | ConvertFrom-Json
# 度量口径（与 py 逐字一致）：UTF-8 字节解码剥 BOM → 换行归一化为 \n → 移除空白集合
$wsRe = [regex]'[ \t\n\f\v\u00a0\u2028\u2029\u3000]+'
$headRe = [regex]'^(#{1,6})\s+(.*)$'
$nlRe = [regex]"\r\n|\r"
$errors = @()

function Get-StrippedCount {
    param([string]$Text)
    # 度量=Unicode 码点数（与 py len() 同口径）：UTF-16 长度需减去代理对中的高位代理数
    $stripped = $wsRe.Replace($Text, '')
    $highSurrogates = ([regex]::Matches($stripped, '[\ud800-\udbff]')).Count
    return ($stripped.Length - $highSurrogates)
}

function Get-SectionText {
    param([string]$Text, [string]$Title)
    $lines = @($Text -split "`r?`n")
    $inFence = $false
    $start = -1
    $level = 0
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line.TrimStart().StartsWith('```')) { $inFence = -not $inFence; continue }
        if ($inFence) { continue }
        if ($line -notmatch '^(#{1,6})\s+(.*)$') { continue }
        $hLevel = $Matches[1].Length
        $hTitle = $Matches[2]
        if ($start -lt 0) {
            if ($hTitle.Contains($Title)) { $start = $i; $level = $hLevel }
            continue
        }
        if ($hLevel -le $level) { return (($lines[$start..($i - 1)]) -join "`n") }
    }
    if ($start -lt 0) { return $null }
    return (($lines[$start..($lines.Count - 1)]) -join "`n")
}

function Measure-Ref {
    param([string]$RootDir, [string]$Ref)
    if ([string]::IsNullOrWhiteSpace($Ref)) { return @{ Value = $null; Status = '空引用' } }
    $parts = $Ref -split '#', 2
    $rel = $parts[0].Trim()
    if ($rel -eq '') { return @{ Value = $null; Status = ('空引用：{0}' -f $Ref) } }
    $frag = ''
    if ($parts.Count -gt 1) { $frag = $parts[1] }
    $p = Join-Path $RootDir ($rel -replace '/', '\')
    if (-not (Test-Path -LiteralPath $p)) { return @{ Value = $null; Status = ("缺文件 {0}" -f $rel) } }
    $bytes = [System.IO.File]::ReadAllBytes($p)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $text = $text.Substring(1) }
    $text = $nlRe.Replace($text, "`n")
    if ($frag -ne '') {
        $sliced = Get-SectionText -Text $text -Title $frag
        if ($null -eq $sliced) { return @{ Value = $null; Status = ("缺小节 {0}" -f $Ref) } }
        return @{ Value = (Get-StrippedCount -Text $sliced); Status = $Ref }
    }
    return @{ Value = (Get-StrippedCount -Text $text); Status = $rel }
}

function Sum-Refs {
    param([string]$RootDir, $Refs)
    $total = 0
    $problems = @()
    foreach ($ref in @($Refs)) {
        if ($null -eq $ref -or [string]::IsNullOrWhiteSpace([string]$ref)) { continue }
        $m = Measure-Ref -RootDir $RootDir -Ref ([string]$ref)
        if ($null -eq $m.Value) { $problems += $m.Status } else { $total += [int]$m.Value }
    }
    return @{ Total = $total; Problems = $problems }
}

Write-Output ("【check-doc-budget】root={0}" -f $Root)
Write-Output ("规则={0} 度量=去空白字符" -f (Split-Path -Leaf $budgetPath))
Write-Output ''
Write-Output '文件预算：'
foreach ($item in @($rules.files)) {
    $ref = [string]$item.path
    $budget = [int]$item.budget
    $m = Measure-Ref -RootDir $Root -Ref $ref
    if ($null -eq $m.Value) {
        $script:errors += ("文件校验：{0}" -f $m.Status)
        Write-Output ("  ❌ {0} — {1}" -f $ref, $m.Status)
        continue
    }
    $n = [int]$m.Value
    if ($n -gt $budget) {
        $script:errors += ("超预算：{0} {1:N0} > {2:N0}" -f $ref, $n, $budget)
        Write-Output ("  ❌ {0} {1:N0}/{2:N0} 超出 {3:N0} 字符" -f $ref, $n, $budget, ($n - $budget))
    }
    else {
        Write-Output ("  ✅ {0} {1:N0}/{2:N0}（余 {3:N0}）" -f $ref, $n, $budget, ($budget - $n))
    }
}
foreach ($g in @($rules.generated)) {
    Write-Output ("  ⚙ generated（脚本产出，不手测）：{0}" -f $g)
}

$ceiling = $rules.path_ceiling
$limit = [int]$ceiling.limit
$exempt = @($ceiling.exempt | ForEach-Object { [string]$_ })
Write-Output ''
Write-Output ("路径合计（上限 path_ceiling.limit={0:N0}）：" -f $limit)
$worst = 0
$worstLabel = ''
foreach ($path in @($rules.paths)) {
    $label = [string]$path.label
    $sum = Sum-Refs -RootDir $Root -Refs $path.files
    $total = [int]$sum.Total
    $script:errors += @($sum.Problems | ForEach-Object { ("{0}：{1}" -f $label, $_) })
    $detail = @("base=$($total.ToString('N0'))")
    $branchGroups = @()
    if ($null -ne $path.PSObject.Properties['branches'] -and $path.branches) { $branchGroups = @($path.branches) }
    foreach ($grp in $branchGroups) {
        if ($null -eq $grp) { continue }
        $branchTotals = @()
        foreach ($b in @($grp)) {
            if ($null -eq $b -or @($b).Count -eq 0) { continue }
            $bs = Sum-Refs -RootDir $Root -Refs $b
            $script:errors += @($bs.Problems | ForEach-Object { ("{0} 分支：{1}" -f $label, $_) })
            $branchTotals += [int]$bs.Total
        }
        if ($branchTotals.Count -gt 0) {
            $maxB = ($branchTotals | Measure-Object -Maximum).Maximum
            $total += [int]$maxB
            $detail += "+branch(max)=$($maxB.ToString('N0'))"
        }
    }
    $stackGroups = @()
    if ($null -ne $path.PSObject.Properties['stacking'] -and $path.stacking) { $stackGroups = @($path.stacking) }
    foreach ($grp in $stackGroups) {
        if ($null -eq $grp) { continue }
        $ss = Sum-Refs -RootDir $Root -Refs $grp
        $script:errors += @($ss.Problems | ForEach-Object { ("{0} 叠加：{1}" -f $label, $_) })
        $total += [int]$ss.Total
        $detail += "+stack=$($ss.Total.ToString('N0'))"
    }
    $flag = '✅'
    if ($exempt -contains $label) { $flag = '⚪ exempt' }
    elseif ($limit -gt 0 -and $total -gt $limit) {
        $script:errors += ("路径超上限：{0} {1:N0} > {2:N0}" -f $label, $total, $limit)
        $flag = '❌'
    }
    if ($exempt -notcontains $label) {
        if ($total -gt $worst) { $worst = $total; $worstLabel = $label }
    }
    Write-Output ("  {0} {1} {2:N0}/{3:N0}（{4}）" -f $flag, $label, $total, $limit, ($detail -join ' '))
}

Write-Output ''
if ($PrintBaseline) {
    Write-Output ("基线：最坏路径 {0:N0}（{1}）/ 上限 {2:N0}" -f $worst, $worstLabel, $limit)
}
if ($errors.Count -gt 0) {
    Write-Output ("❌ 预算未通过，{0} 项：" -f $errors.Count)
    $errors | ForEach-Object { Write-Output ("  - " + $_) }
    Write-Output '处置：删等量旧文本，或显式调高 doc-budget.json 预算并写明 why（禁止静默放宽）'
    exit 1
}
Write-Output ("✅ 全部在预算内（最坏路径 {0:N0} ≤ {1:N0}）" -f $worst, $limit)
exit 0
