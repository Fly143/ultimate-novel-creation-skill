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
[CmdletBinding()]
param(
    [string]$Root,
    [switch]$PrintBaseline
)
# $PSScriptRoot 在 Windows PowerShell 5.1 的 param 默认值里为 null → 挪到 param 之后求值（5.1/7 通用）
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
# 与 py --root resolve 对齐：无条件归一为绝对路径再回显/度量（GetFullPath 对不存在路径同样归一，py 非严格 resolve 同语义）
try { $Root = [System.IO.Path]::GetFullPath($Root) } catch { }
# fail-closed：任何读盘/解析错误必须中止并 rc1，禁止吞错后打 ✅（与 py exit1 对齐）
$ErrorActionPreference = 'Stop'
# 千分位与 py 的 `{n:,}` 恒等：{N0} 默认走 CurrentCulture（de-DE 47.000 / fr-FR 47 000 会致双实现对拍假红）
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture

# 输出统一 UTF-8（Windows 中文控制台默认 GBK 会把标记打成 ?；CI 上为无害 no-op）
try { [System.Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
# py 口径空白全集（脚本级：Measure-Ref / Sum-Refs 等函数共用；.NET 默认 Trim 不含 U+001C-U+001F）
$wsChars = [char[]]@(0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000)

$enc = New-Object System.Text.UTF8Encoding($false)
$budgetPath = Join-Path $PSScriptRoot 'doc-budget.json'
if (-not (Test-Path -LiteralPath $budgetPath -PathType Leaf)) {
    Write-Output ("❌ 缺少规则单源：{0}" -f $budgetPath)
    exit 1
}
try {
    $rawRules = Get-Content -LiteralPath $budgetPath -Raw -Encoding UTF8 -ErrorAction Stop
} catch {
    Write-Output ("❌ 规则单源解析失败：{0}" -f $budgetPath)
    [Console]::Error.WriteLine(("解析详情：{0}" -f $_.Exception.Message))
    exit 1
}
if ([string]::IsNullOrWhiteSpace($rawRules)) {
    Write-Output ("❌ 规则单源解析失败：{0}" -f $budgetPath)
    [Console]::Error.WriteLine('解析详情：Unexpected end of input（空/仅空白文件）')
    exit 1
}
# 顶层须为 JSON 对象：用原始串前缀判定（PS5.1 的 -is [pscustomobject] 对数组为 True、pwsh7 为 False，不可用作判据）
if (-not $rawRules.TrimStart().StartsWith('{')) {
    Write-Output ("❌ 规则单源解析失败：{0}（须为 JSON 对象）" -f $budgetPath)
    exit 1
}
try {
    $rules = $rawRules | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-Output ("❌ 规则单源解析失败：{0}" -f $budgetPath)
    [Console]::Error.WriteLine(("解析详情：{0}" -f $_.Exception.Message))
    exit 1
}
if ($null -eq $rules) {
    Write-Output ("❌ 规则单源解析失败：{0}（须为 JSON 对象）" -f $budgetPath)
    exit 1
}
# 必填键 schema 校验（与 check-doc-budget.py validate_rules 同清单同文案，fail-closed）
function Test-RefShape {
    param($V, [bool]$Nested)
    # str ✓；str[] ✓；Nested 时允许 元素=非空 str[]（branches/stacking 组）
    if ($V -is [string]) { return $true }
    if ($V -isnot [array]) { return $false }
    foreach ($e in $V) {
        if ($e -is [string]) { continue }
        if ($Nested -and $null -ne $e -and $e -is [array] -and @($e).Count -gt 0) {
            $innerOk = $true
            foreach ($x in $e) { if ($x -isnot [string]) { $innerOk = $false; break } }
            if ($innerOk) { continue }
        }
        return $false
    }
    return $true
}
$schemaErrs = @()
if ($null -eq $rules.files -or $rules.files -isnot [array]) { $schemaErrs += 'files 须为数组' }
if ($null -eq $rules.paths -or $rules.paths -isnot [array]) { $schemaErrs += 'paths 须为数组' }
if ($null -eq $rules.path_ceiling -or $rules.path_ceiling -isnot [pscustomobject]) { $schemaErrs += 'path_ceiling 须为对象' }
if ($null -ne $rules.files -and $rules.files -is [array]) {
    for ($i = 0; $i -lt $rules.files.Count; $i++) {
        $item = $rules.files[$i]
        if ($null -eq $item -or $item -isnot [pscustomobject] -or $null -eq $item.PSObject.Properties['path'] -or $null -eq $item.PSObject.Properties['budget']) {
            $schemaErrs += ("files[{0}] 须为含 path/budget 的对象" -f $i)
            continue
        }
        if ($item.path -isnot [string]) { $schemaErrs += ("files[{0}].path 须为字符串" -f $i) }
        # 严格整数：5.1 整数=Int32、7=Int64 都放行；Decimal/Double/String/Boolean 一律拒（防 [int] 四舍五入与 py 截断反向）
        if ($item.budget -isnot [int] -and $item.budget -isnot [long]) { $schemaErrs += ("files[{0}].budget 须为整数" -f $i) }
    }
}
if ($null -ne $rules.paths -and $rules.paths -is [array]) {
    $seenLbl = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    for ($i = 0; $i -lt $rules.paths.Count; $i++) {
        $item = $rules.paths[$i]
        if ($null -eq $item -or $item -isnot [pscustomobject] -or $null -eq $item.PSObject.Properties['label'] -or $null -eq $item.PSObject.Properties['files']) {
            $schemaErrs += "paths[{0}] 须为含 label/files 的对象" -f $i
            continue
        }
        if (-not $seenLbl.Add([string]$item.label)) { $schemaErrs += ("paths[{0}] label 重复：{1}（重复行会静默双计/歧义豁免）" -f $i, $item.label) }  # label 唯一性 fail-closed 对齐 py（原重复不校验）
        foreach ($key in @('files', 'branches', 'stacking')) {
            $prop = $item.PSObject.Properties[$key]
            if ($null -eq $prop) { continue }
            $nested = ($key -ne 'files')
            if (Test-RefShape $prop.Value $nested) { continue }
            if ($key -eq 'files') { $schemaErrs += ("paths[{0}].files 须为字符串或字符串数组" -f $i) }
            else { $schemaErrs += ("paths[{0}].{1} 组须为字符串或非空字符串数组" -f $i, $key) }
        }
    }
}
if ($null -ne $rules.PSObject.Properties['generated'] -and $null -ne $rules.generated) {
    if (-not (Test-RefShape $rules.generated $false)) { $schemaErrs += 'generated 须为字符串或字符串数组' }
}
$exemptVal = $null
if ($null -ne $rules.path_ceiling -and $rules.path_ceiling -is [pscustomobject] -and $null -ne $rules.path_ceiling.PSObject.Properties['exempt']) {
    $exemptVal = $rules.path_ceiling.exempt
}
if ($null -ne $exemptVal -and -not (Test-RefShape $exemptVal $false)) { $schemaErrs += 'exempt 须为字符串或字符串数组' }
$limVal = $null
if ($null -ne $rules.path_ceiling -and $rules.path_ceiling -is [pscustomobject] -and $null -ne $rules.path_ceiling.PSObject.Properties['limit']) {
    $limVal = $rules.path_ceiling.limit
}
if ($limVal -isnot [int] -and $limVal -isnot [long]) { $schemaErrs += 'path_ceiling.limit 须为正整数（0 会使路径上限闸短路 fail-open）' }
elseif ([long]$limVal -le 0) { $schemaErrs += 'path_ceiling.limit 须为正整数（0 会使路径上限闸短路 fail-open）' }
if ($schemaErrs.Count -gt 0) {
    Write-Output ("❌ 规则单源校验失败：{0}（{1}）" -f $budgetPath, ($schemaErrs -join '；'))
    exit 1
}
# 度量口径（与 py 逐字一致）：UTF-8 字节解码剥 BOM → 换行归一化为 \n → 移除空白集合
$wsRe = [regex]'[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+'   # 全空白集（对齐 py WS_RE）
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
    # 与 py section_slice 的 splitlines() 同切行集合（py 不切 \x1f）
    $lines = @($Text -split '\r\n|\r|\v|\f|[\x1c-\x1e]|\x85|\u2028|\u2029|\n')
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
    if (([string]$Ref).Trim($wsChars) -eq '') { return @{ Value = $null; Status = '空引用' } }
    $parts = $Ref -split '#', 2
    $rel = $parts[0].Trim($wsChars)
    if ($rel -eq '') { return @{ Value = $null; Status = ('空引用：{0}' -f $Ref) } }
    $frag = ''
    if ($parts.Count -gt 1) { $frag = $parts[1] }
    $p = Join-Path $RootDir ($rel -replace '/', '\')
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { return @{ Value = $null; Status = ("缺文件 {0}" -f $rel) } }
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
    # $wsChars 用脚本级定义（:28），此处不再重复定义防双份口径漂移
    $total = 0
    $problems = @()
    foreach ($ref in @($Refs)) {
        if ($null -eq $ref -or ([string]$ref).Trim($wsChars) -eq '') { continue }
        $m = Measure-Ref -RootDir $RootDir -Ref ([string]$ref)
        if ($null -eq $m.Value) { $problems += $m.Status } else { $total += [long]$m.Value }
    }
    return @{ Total = $total; Problems = $problems }
}

Write-Output ("【check-doc-budget】root={0}" -f $Root)
Write-Output ("规则={0} 度量=去空白字符" -f (Split-Path -Leaf $budgetPath))
Write-Output ''
Write-Output '文件预算：'
foreach ($item in @($rules.files)) {
    $ref = [string]$item.path
    $budget = [long]$item.budget
    $m = Measure-Ref -RootDir $Root -Ref $ref
    if ($null -eq $m.Value) {
        $script:errors += ("文件校验：{0}" -f $m.Status)
        Write-Output ("  ❌ {0} — {1}" -f $ref, $m.Status)
        continue
    }
    $n = [long]$m.Value
    if ($n -gt $budget) {
        $whyS = if ($null -ne $item.why -and $item.why -is [string]) { [string]$item.why } else { '' }
        $script:errors += ("超预算：{0} {1:N0} > {2:N0}（{3}）" -f $ref, $n, $budget, $whyS)
        Write-Output ("  ❌ {0} {1:N0}/{2:N0} 超出 {3:N0} 字符" -f $ref, $n, $budget, ($n - $budget))
    }
    elseif ($budget -gt 0 -and ($budget - $n) * 100 -lt $budget * 5) {
        $pct = [math]::Floor([double]($budget - $n) * 1000.0 / [double]$budget) / 10.0
        $script:errors += ("余量<5%：{0} {1:N0}/{2:N0}（余 {3:N0}，{4:0.0}%）——删等量旧文本或扩预算写明 why（禁止静默放宽）" -f $ref, $n, $budget, ($budget - $n), $pct)
        Write-Output ("  ❌ {0} {1:N0}/{2:N0} 余 {3:N0}（{4:0.0}%<5%）" -f $ref, $n, $budget, ($budget - $n), $pct)
    }
    else {
        Write-Output ("  ✅ {0} {1:N0}/{2:N0}（余 {3:N0}）" -f $ref, $n, $budget, ($budget - $n))
    }
}
# 上下文分层（P3-18）：合法层名单单源 = context_layers 的键（排除 'why' 元字段），与 py 同口径
$validLayers = @()
if ($null -ne $rules.context_layers) {
    foreach ($p in $rules.context_layers.PSObject.Properties) { if ($p.Name -ne 'why') { $validLayers += [string]$p.Name } }
}
if ($validLayers.Count -eq 0) { $validLayers = @('protected', 'compressible') }
$layerKeys = [string[]]@($validLayers)  # Ordinal 成员判定对齐 py dict 键大小写敏感（@{} 字面量默认不敏感 → 非法 layer 放行 fail-open）
$layerValid = New-Object 'System.Collections.Generic.Dictionary[string,int]' ([System.StringComparer]::Ordinal)
foreach ($k in $layerKeys) { $layerValid[$k] = 0 }
$layerCounts = $layerValid
foreach ($item in @($rules.files)) {
    $layer = ''
    if ($null -ne $item.PSObject.Properties['layer']) { $layer = [string]$item.layer }
    if ($layerValid.ContainsKey($layer)) { $layerCounts[$layer]++ }
    else { $script:errors += ("分层缺失/非法：{0} layer={1}" -f $item.path, $layer) }
}
$pathTotals = @{}
$maxSingleRef = ''
$maxSingleVal = [long]-1
foreach ($item in @($rules.files)) {
    $refM = [string]$item.path
    if ([string]::IsNullOrEmpty($refM)) { continue }
    $mM = Measure-Ref -RootDir $Root -Ref $refM
    if ($null -ne $mM.Value -and [long]$mM.Value -gt $maxSingleVal) { $maxSingleRef = (([string]$refM).Split('#'))[0].Trim($wsChars); $maxSingleVal = [long]$mM.Value }
}
$layerParts = @()
foreach ($k in $validLayers) { $layerParts += ("{0} {1}" -f $k, $layerCounts[$k]) }
Write-Output ("分层校验：{0}（压缩时只裁 compressible，protected 永不裁）" -f ($layerParts -join ' / '))

if ($null -ne $rules.generated) {
    foreach ($g in @($rules.generated)) {
        if ($null -eq $g) { continue }
        Write-Output ("  ⚙ generated（脚本产出，不手测）：{0}" -f $g)
    }
}

$ceiling = $rules.path_ceiling
$limit = [long]$ceiling.limit
$exempt = @($ceiling.exempt | ForEach-Object { [string]$_ })
Write-Output ''
Write-Output ("路径合计（上限 path_ceiling.limit={0:N0}）：" -f $limit)
$worst = 0
$worstLabel = ''
foreach ($path in @($rules.paths)) {
    $label = [string]$path.label
    $sum = Sum-Refs -RootDir $Root -Refs $path.files
    $total = [long]$sum.Total
    $detail = @("base=$($total.ToString('N0'))")
    # branches = 互斥条件分支：每组是一串 ref（组内求和），组间取最大值（与 py 及 _comment 口径一致）
    $branchGroups = @()
    if ($null -ne $path.PSObject.Properties['branches'] -and $path.branches) { $branchGroups = @($path.branches) }
    $branchTotals = @()
    foreach ($grp in $branchGroups) {
        if ($null -eq $grp) { continue }
        $bs = Sum-Refs -RootDir $Root -Refs $grp
        $script:errors += @($bs.Problems | ForEach-Object { ("{0} 分支：{1}" -f $label, $_) })
        $branchTotals += [long]$bs.Total
    }
    if ($branchTotals.Count -gt 0) {
        $maxB = ($branchTotals | Measure-Object -Maximum).Maximum
        $total += [long]$maxB
        $detail += "+branch(max)=$($maxB.ToString('N0'))"
    }
    $stackGroups = @()
    if ($null -ne $path.PSObject.Properties['stacking'] -and $path.stacking) { $stackGroups = @($path.stacking) }
    foreach ($grp in $stackGroups) {
        if ($null -eq $grp) { continue }
        $ss = Sum-Refs -RootDir $Root -Refs $grp
        $script:errors += @($ss.Problems | ForEach-Object { ("{0} 叠加：{1}" -f $label, $_) })
        $total += [long]$ss.Total
        $detail += "+stack=$($ss.Total.ToString('N0'))"
    }
    # base 问题在分支/叠加之后登记（与 py errors 追加顺序逐条一致）
    $script:errors += @($sum.Problems | ForEach-Object { ("{0}：{1}" -f $label, $_) })
    $flag = '✅'
    $isExempt = $false
    foreach ($ex in @($exempt)) { if ([string]::Equals([string]$ex, $label, [System.StringComparison]::Ordinal)) { $isExempt = $true; break } }  # Ordinal 对齐 py set 成员判定（-contains 大小写不敏感 → 错配免判上限 fail-open）
    if ($isExempt) { $flag = '⚪ exempt' }
    elseif ($limit -gt 0 -and $total -gt $limit) {
        $script:errors += ("路径超上限：{0} {1:N0} > {2:N0}" -f $label, $total, $limit)
        $flag = '❌'
    }
    if (-not $isExempt) {
        $pathTotals[$label] = $total
        if ($total -gt $worst) { $worst = $total; $worstLabel = $label }
    }
    Write-Output ("  {0} {1} {2:N0}/{3:N0}（{4}）" -f $flag, $label, $total, $limit, ($detail -join ' '))
}

# 非法基线值展示归一（与 py _bl_disp 口径对齐）
function Convert-BLDisp {
    param($V)
    if ($null -eq $V) { return '' }
    if ($V -is [System.Numerics.BigInteger]) { return ([double]$V).ToString('G15', [System.Globalization.CultureInfo]::InvariantCulture).ToUpperInvariant() }
    if ($V -is [double] -or $V -is [decimal]) { return (([double]$V).ToString('G15', [System.Globalization.CultureInfo]::InvariantCulture).ToUpperInvariant() -replace '^-0$', '0') }
    if ($V -is [array] -or $V -is [pscustomobject]) { return (ConvertTo-Json -InputObject $V -Compress -Depth 100) }
    return [string]$V
}
# 基线防漂移闸：baseline 五字段须与实测一致（改文档后必须回填，禁止静默过期）；缺键/换型一律 fail-closed
$bl = $null
if ($null -ne $rules.PSObject.Properties['baseline']) { $bl = $rules.baseline }
if ($null -eq $bl -or $bl -isnot [pscustomobject]) {
    $script:errors += '基线非法：baseline 块缺失或非对象（须含 worst/writer/resident/precheck/max_single 五字段）'
} else {
    foreach ($kMiss in @('worst_path_chars','writer_delegated_chars','session_resident_chars','precheck_path_chars','max_single_file')) {
        if ($null -eq $bl.PSObject.Properties[$kMiss]) { $script:errors += ("基线缺键：baseline.{0}（五字段必须齐全）" -f $kMiss) }
    }
    # PS7 ConvertFrom-Json 整型为 Int64、PS5.1 为 Int32——int/long 双收，避免版本性漏闸（R18 修复）
    if ($null -ne $bl.PSObject.Properties['worst_path_chars']) {
        $wvB = $bl.PSObject.Properties['worst_path_chars'].Value
        if (-not ($wvB -is [int] -or $wvB -is [long])) {
            $wvS = Convert-BLDisp $wvB
            $script:errors += ("基线非法：baseline.worst_path_chars={0}（应为整数，非整型值会绕过过期闸——fail-closed）" -f $wvS)
        } elseif ([long]$wvB -ne [long]$worst) {
            $script:errors += ("基线过期：baseline.worst_path_chars={0:N0} ≠ 实测 {1:N0}（改文档后回填 doc-budget.json baseline）" -f [long]$wvB, [long]$worst)
        }
    }
    # 根因：PS7 ConvertFrom-Json 整型为 Int64、判型若写死 -is [int] 会静默漏闸（R18 实测确认；分隔串仅为防御性收敛写法，7.4.6 实测并不拍平该嵌套数组）
    foreach ($pairB in @('写一章·写手委派|writer_delegated_chars','主会话常驻（入口+协议+索引）|session_resident_chars','写前预检|precheck_path_chars')) {
        $partsB = [string]$pairB -split '\|'
        $lblB = $partsB[0]
        $keyB = $partsB[1]
        if ($null -ne $bl.PSObject.Properties[$keyB]) {
            $kvB = $bl.PSObject.Properties[$keyB].Value
            if (-not ($kvB -is [int] -or $kvB -is [long])) {
                $kvS = Convert-BLDisp $kvB
                $script:errors += ("基线非法：baseline.{0}={1}（应为整数，非整型值会绕过过期闸——fail-closed）" -f $keyB, $kvS)
            } elseif ($pathTotals.ContainsKey([string]$lblB) -and [long]$kvB -ne [long]$pathTotals[[string]$lblB]) {
                $script:errors += ("基线过期：baseline.{0}={1:N0} ≠ 实测 {2:N0}（改文档后回填 doc-budget.json baseline）" -f $keyB, [long]$kvB, [long]$pathTotals[[string]$lblB])
            }
        }
    }
    if ($null -ne $bl.PSObject.Properties['max_single_file']) {
        $msfRaw = $bl.PSObject.Properties['max_single_file'].Value
        if ($msfRaw -isnot [string] -or -not ([string]$msfRaw).Contains('=')) {
            $script:errors += ("基线非法：baseline.max_single_file={0}（应为 path=整数）" -f (Convert-BLDisp $msfRaw))
        } else {
            $msfStr = [string]$msfRaw
            $eqIdx = $msfStr.LastIndexOf('=')
            $snPart = $msfStr.Substring($eqIdx + 1)
            $snVal = 0L
            if ($snPart -notmatch '^\A-?[0-9]+\z') {
                $script:errors += ("基线非法：baseline.max_single_file={0}（应为 path=整数）" -f $msfStr)
            } elseif (-not [long]::TryParse($snPart, [ref]$snVal)) {
                $script:errors += ("基线非法：baseline.max_single_file={0}（应为 path=整数）" -f $msfStr)
            } elseif (-not [string]::Equals($msfStr.Substring(0, $eqIdx), [string]$maxSingleRef, [System.StringComparison]::Ordinal) -or $snVal -ne [long]$maxSingleVal) {  # Ordinal 对齐 py 字符串 !=（-ne 大小写不敏感 → 防漂移闸失效）
                $script:errors += ("基线过期：baseline.max_single_file={0} ≠ 实测 {1}={2:N0}（路径段须为实测最大单文件名；改文档后回填 doc-budget.json baseline）" -f $msfStr, $maxSingleRef, [long]$maxSingleVal)
            }
        }
    }
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
