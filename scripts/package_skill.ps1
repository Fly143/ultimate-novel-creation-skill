<#
.SYNOPSIS
  package_skill.ps1 —— 技能发布包校验（P3-19）。
  -Check：校验 SKILL.md frontmatter（name/description/version）并打印载荷清单，
  与 package_skill.py --check 输出逐字节一致。
  zip 产出仅 py 实现（确定性 zip 需固定时间戳+压缩器一致，ps1 .NET 压缩结果不同 → 见实施记录偏离项）。
#>
[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

$includeDirs = @('modules', 'templates', 'references', 'scripts')
$includeFiles = @('SKILL.md', 'system_prompt.md', 'LICENSE')
$required = @('name', 'description', 'version')

$skillPath = Join-Path $Root 'SKILL.md'
if (-not (Test-Path -LiteralPath $skillPath)) { [Console]::Out.WriteLine('❌ 缺少 SKILL.md'); exit 1 }
$text = [System.IO.File]::ReadAllText($skillPath, [System.Text.Encoding]::UTF8)
$fm = @{}
if ($text.StartsWith('---')) {
    $end = $text.IndexOf("`n---", 3, [System.StringComparison]::Ordinal)
    if ($end -gt 0) {
        foreach ($line in ($text.Substring(4, $end - 4) -split "`n")) {
            if ($line -match '^[^\s].*?:' ) {
                $i = $line.IndexOf(':')
                $fm[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim()
            }
        }
    }
}
$missing = @()
foreach ($k in $required) { if (-not $fm.ContainsKey($k) -or [string]::IsNullOrWhiteSpace([string]$fm[$k])) { $missing += $k } }
if ($missing.Count) { [Console]::Out.WriteLine("❌ SKILL.md frontmatter 缺字段：$($missing -join '、')"); exit 1 }

$rels = New-Object System.Collections.Generic.List[string]
foreach ($f in $includeFiles) { if (Test-Path -LiteralPath (Join-Path $Root $f)) { [void]$rels.Add($f) } }
foreach ($d in $includeDirs) {
    $base = Join-Path $Root $d
    if (-not (Test-Path -LiteralPath $base)) { continue }
    $rootFull = (Resolve-Path -LiteralPath $Root).Path
    foreach ($p in Get-ChildItem -Recurse -File -Path $base) {
        [void]$rels.Add($p.FullName.Substring($rootFull.Length).TrimStart('\', '/').Replace('\', '/'))
    }
}
$rels.Sort([System.StringComparer]::Ordinal)
$distinct = New-Object System.Collections.Generic.List[string]
$seen = @{}
foreach ($r in $rels) { if (-not $seen.ContainsKey($r)) { $seen[$r] = $true; [void]$distinct.Add($r) } }

$total = 0
foreach ($rel in $distinct) {
    $bytes = [System.IO.File]::ReadAllBytes((Join-Path $Root ($rel -replace '/', '\')))
    $t = [System.Text.Encoding]::UTF8.GetString($bytes).Replace("`r`n", "`n")
    $total += $t.Length
}
[Console]::Out.WriteLine("【技能发布包】name=$([string]$fm['name']) version=$([string]$fm['version'])")
[Console]::Out.WriteLine("载荷：$($distinct.Count) 个文件 ｜ 字符（UTF-16 单元）：$total")
foreach ($rel in $distinct) {
    $sz = (Get-Item -LiteralPath (Join-Path $Root ($rel -replace '/', '\'))).Length
    [Console]::Out.WriteLine(('  {0,8}  {1}' -f $sz, $rel))
}

if (-not $Check) {
    [Console]::Out.WriteLine('❌ zip 产出仅支持 py 实现（package_skill.py --out …；ps1 只做 -Check 校验，见实施记录偏离项）')
    exit 2
}
exit 0
