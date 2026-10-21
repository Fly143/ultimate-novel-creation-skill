<#
.SYNOPSIS
  package_skill.ps1 —— 技能发布包校验（P3-19）。
  -Check：校验 SKILL.md frontmatter（name/description/version）并打印载荷清单，
  与 package_skill.py --check 输出逐字节一致。
  zip 产出仅 py 实现（确定性 zip 需固定时间戳+压缩器一致，ps1 .NET 压缩结果不同 → 见实施记录偏离项）。
#>
[CmdletBinding()]
param(
    [string]$Root,
    [switch]$Check
)
# $PSScriptRoot 在 Windows PowerShell 5.1 的 param 默认值里为 null → 挪到 param 之后求值（5.1/7 通用）
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
# py 口径 ws 全集（str.isspace/strip；.NET 默认 Trim 不含 U+001C-U+001F）
$wsChars = [char[]]@(0x09,0x0a,0x0b,0x0c,0x0d,0x1c,0x1d,0x1e,0x1f,0x20,0x85,0xa0,0x1680,0x2000,0x2001,0x2002,0x2003,0x2004,0x2005,0x2006,0x2007,0x2008,0x2009,0x200a,0x2028,0x2029,0x202f,0x205f,0x3000)

$includeDirs = @('modules', 'templates', 'references', 'scripts', 'memory-system')  # memory-system=项目初始化模板源（03_01 步骤8.2）
$IncludeFiles = @("SKILL.md", "system_prompt.md", "LICENSE", "NOTICE", "THIRD-PARTY-LICENSES.txt")  # NOTICE+THIRD-PARTY-LICENSES=Apache-2.0 §4(a)(b) 随包义务（与 py 同步）
$required = @('name', 'description', 'version')

$skillPath = Join-Path $Root 'SKILL.md'
if (-not (Test-Path -LiteralPath $skillPath)) { [Console]::Out.WriteLine('❌ 缺少 SKILL.md'); exit 1 }
$text = [System.IO.File]::ReadAllText($skillPath, [System.Text.Encoding]::UTF8)
$fm = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)
if ($text.StartsWith('---')) {
    $end = $text.IndexOf("`n---", 3, [System.StringComparison]::Ordinal)
    if ($end -gt 3) {  # $end=3 即空 frontmatter（---\n---）→ $end-4=-1 裸崩，跳过解析对齐 py text[4:3]='' 返回 {}
        foreach ($line in ($text.Substring(4, $end - 4) -split '\r\n|\r|\v|\f|[\x1c-\x1e]|\x85|\u2028|\u2029|\n')) {
            if ($line -match ':' -and $line -notmatch '^[ \t]') {  # 与 py frontmatter 同口径（仅排空格/Tab 首行，NBSP 等不排）
                $i = $line.IndexOf(':')
                $fm[$line.Substring(0, $i).Trim($wsChars)] = $line.Substring($i + 1).Trim($wsChars)
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
    $rootFull = (Get-Item -LiteralPath $Root).FullName  # Resolve-Path 不展开 8.3 短名，Get-ChildItem FullName 是长名 → Substring 错位（对齐 state_commit Get-RelPath 前缀归一）
    foreach ($p in Get-ChildItem -Recurse -File -LiteralPath $base) {  # -Path 会把含 [] 的合法 Root 当通配符 → 静默少算载荷
        # 与 py collect 同排除：__pycache__/*.pyc 不进载荷（读数随本机状态漂移）
        $parts = $p.FullName -split '\\|/'
        if ($parts -contains '__pycache__' -or $p.Extension -eq '.pyc' -or $p.Extension -eq '.pyo') { continue }  # 整段匹配对齐 py in p.parts（子串匹配 __pycache__x 会误排）
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
