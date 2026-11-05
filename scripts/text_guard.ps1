<#
.SYNOPSIS
  text_guard.ps1 —— 正文指纹与手改保护（P1-9）。
  与 text_guard.py 双实现对拍：输出逐字节一致（长度=UTF-16 单元；sha256=正文 UTF-8 字节小写十六进制）。账本 JSON 文件格式不承诺跨引擎一致（py=indent2+sort_keys+LF，ps1=ConvertTo-Json 键序/缩进随引擎），跨引擎 verify 语义一致。
  记录 {书名}/.story/text-digests.json（临时文件+原子替换）。不一致 → exit1 + 「沿用/重写/只看」处置口径。
  已知口径：账本含空属性名（`"":`）时 ConvertFrom-Json 解析层拒（文案「账本 JSON 损坏」rc1；py 键判型「账本条目非法」rc1）——rc 同、文案类别随解析器，fail-closed 方向一致。
.PARAMETER Command
  digest | verify | verify-all
.PARAMETER Project
  项目目录（含 正文/ 与 .story/）
.PARAMETER Chapter
  章号（digest/verify 必填；verify-all 忽略）
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('digest', 'verify', 'verify-all')][string]$Command,
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Chapter = '0'
)
# 参数错误收敛：纯 ASCII 十进制（[long] 会把 "1e3"/"+5"/"0x10"/" 5"/"" 静默转换，与 py _i64 反向裁决）
if ($Chapter -notmatch '^-?[0-9]+$') { [Console]::Out.WriteLine('❌ 参数错误：章节号须为纯 ASCII 十进制整数（对齐 py _i64）'); exit 1 }
$ChapterRaw = $Chapter  # param([string]) 类型约束会把赋值回炉成 String，先存原值再解约束重赋
Remove-Variable -Name Chapter -Force -ErrorAction SilentlyContinue
$_cvtChapter = 0L
if (-not [long]::TryParse($ChapterRaw, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$_cvtChapter)) { [Console]::Out.WriteLine('❌ 参数错误：超出 Int64 范围（对齐 py _i64）'); exit 1 }  # TryParse 收敛：裸 [long] cast 在 EAP Stop 前是非终止错误、变量落 $null fail-open（纯十进制超界静默放行分叉）
$Chapter = $_cvtChapter

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

$rulesPath = Join-Path $scriptDir 'check-done-rules.json'
$script:globs = $null
function Get-BodyGlobs {
    # 惰性加载（对齐 py body_globs()：verify-all 空账本不触碰规则单源，同输入同 rc）
    if ($null -ne $script:globs) { return $script:globs }
    if (-not (Test-Path -LiteralPath $rulesPath -PathType Leaf)) {
        [Console]::Out.WriteLine(("❌ 缺少规则单源：{0}" -f $rulesPath)); exit 1
    }
    try {
        $rulesObj = [System.IO.File]::ReadAllText($rulesPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    } catch {
        [Console]::Out.WriteLine(("❌ 规则单源解析失败：{0}" -f $rulesPath)); exit 1
    }
    if ($null -eq $rulesObj -or $rulesObj -isnot [pscustomobject] -or $rulesObj.GetType().FullName -ne 'System.Management.Automation.PSCustomObject' -or $null -eq $rulesObj.PSObject.Properties['body_globs']) {
        [Console]::Out.WriteLine(("❌ 规则单源解析失败：{0}（须为含 body_globs 的 JSON 对象）" -f $rulesPath)); exit 1  # 对齐 py 判型（原 ps1 静默空 globs → rc2 误导「正文未找到」）
    }
    $script:globs = @($rulesObj.body_globs)
    return $script:globs
}

function Format-Ch([long]$ch) {
    # 对齐 py f"{ch:03d}"：.NET D3 把符号计入宽度（-1 → -001 vs py -01）为文案分叉
    if ($ch -lt 0) { return ('-' + [Math]::Abs($ch).ToString('00')) }
    return ('{0:D3}' -f $ch)
}
function Find-Body([string]$proj, [long]$ch) {
    $nnn = Format-Ch $ch
    foreach ($g in (Get-BodyGlobs)) {
        $relPat = $g.Replace('{nnn}', $nnn).Replace('{ch}', "$ch")
        $dirRel = (Split-Path $relPat -Parent).Replace('\', '/')
        $name = Split-Path $relPat -Leaf
        $dir = Join-Path $proj $dirRel
        if (Test-Path -LiteralPath $dir) {
            $hits = @(Get-ChildItem -File -LiteralPath $dir -Filter $name)
            if ($hits.Count -gt 1) {  # 纯码点序对齐 py sorted(str)（culture 拼音序会选错文件致跨引擎互判手改）
                # [Array]::Sort(keys, object[], Ordinal) 实测不联动重排 items → 键序排定后按名映射回对象
                $sNames = [string[]]@($hits | ForEach-Object { $_.Name })
                [Array]::Sort($sNames, [System.StringComparer]::Ordinal)
                $byName = @{}
                foreach ($h in $hits) { $byName[$h.Name] = $h }
                $hits = [object[]]@($sNames | ForEach-Object { $byName[$_] })
            }
            if ($hits.Count -gt 0) {
                # 相对路径直拼（不靠前缀 Substring 反推，规避 CI 路径形态差异）
                return "$dirRel/$($hits[0].Name)"
            }
        }
    }
    return ''
}
function Get-Digest([string]$fp) {
    $bytes = [System.IO.File]::ReadAllBytes($fp)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($bytes)
    $hex = -join ($hash | ForEach-Object { $_.ToString('x2') })
    $t = [System.Text.Encoding]::UTF8.GetString($bytes).Replace("`r`n", "`n")
    return , @($hex, $t.Length)
}
function Get-Short([string]$sha) { if ([string]::IsNullOrEmpty($sha) -or $sha.Length -le 12) { return $sha }; return $sha.Substring(0, 12) }  # 安全截断对齐 py sha[:12]（短串 Substring 裸崩为分叉）

$ledgerPath = Join-Path $Project '.story/text-digests.json'
if (Test-Path -LiteralPath $ledgerPath) {
    try {
        $ledger = [System.IO.File]::ReadAllText($ledgerPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    } catch {
        [Console]::Out.WriteLine(("❌ 账本 JSON 损坏：{0}（可删 .story/text-digests.json 重新 digest 生成）" -f $ledgerPath)); exit 1  # 对齐 py
    }
    if ($null -eq $ledger -or $ledger -isnot [pscustomobject] -or $ledger.GetType().FullName -ne 'System.Management.Automation.PSCustomObject') {
        [Console]::Out.WriteLine(("❌ 账本须为 JSON 对象：{0}（顶层非对象，如数组/标量）" -f $ledgerPath)); exit 1  # 对齐 py 判型（原误判「无记录」rc2 会诱导重跑 digest 覆盖账本）
    }
} else {
    $ledger = [pscustomobject]@{ version = 1; chapters = [pscustomobject]@{} }
}
if ($null -eq $ledger.chapters -or $ledger.chapters.GetType().FullName -ne 'System.Management.Automation.PSCustomObject') {
    [Console]::Out.WriteLine(("❌ 账本须为 JSON 对象：{0}（chapters 非对象，如 null/数组/标量）" -f $ledgerPath)); exit 1  # 对齐 py 判型：原 fail-open rc0 会重写账本丢历史条目
}
$chapters = @{}
foreach ($p in $ledger.chapters.PSObject.Properties) {
    $keyCvt = 0L
    if ($p.Name -notmatch '^[0-9]+$' -or -not [long]::TryParse($p.Name, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$keyCvt)) { [Console]::Out.WriteLine(("❌ 账本条目非法：chapters.{0} 键须为纯数字章号（先删账本重跑 digest）" -f $p.Name)); exit 1 }  # 键判型 fail-closed；超 Int64 挡（原 [long] 裸 .NET 异常；py 侧同拒）
    $rec = $p.Value
    $bad = ($null -eq $rec -or $rec.GetType().FullName -ne 'System.Management.Automation.PSCustomObject' -or
            $null -eq $rec.PSObject.Properties['sha256'] -or $rec.sha256 -isnot [string] -or
            $null -eq $rec.PSObject.Properties['units'] -or ($rec.units -isnot [long] -and $rec.units -isnot [int]) -or  # PS5.1 ConvertFrom-Json 给 Int32、pwsh7 给 Int64，须双收
            $null -eq $rec.PSObject.Properties['file'] -or $rec.file -isnot [string])
    if ($bad) { [Console]::Out.WriteLine(("❌ 账本条目非法：chapters.{0} 须含 str sha256 / int units / str file（先删账本重跑 digest）" -f $p.Name)); exit 1 }  # 对齐 py 条目 schema fail-closed（原裸异常/异常崩）
    $chapters[$p.Name] = $rec
}

if ($Command -eq 'digest') {
    $rel = Find-Body $Project $Chapter
    if ($rel -eq '') { [Console]::Out.WriteLine(("【文本指纹】第{0} 正文文件未找到（glob：正文/第{0}章_*.txt）" -f (Format-Ch $Chapter))); exit 2 }
    $d = Get-Digest (Join-Path $Project $rel)
    $nnn = Format-Ch $Chapter
    $chapters[$nnn] = [pscustomobject]@{ sha256 = $d[0]; units = $d[1]; file = $rel }
    # 原子写：临时文件 + 覆盖移动（.story 尚未创建时先建——与 py os.makedirs 同口径，防止未跑 init 的项目 digest 崩异常页）
    $ledgerDir = Split-Path -Parent $ledgerPath
    if ($ledgerDir -and -not (Test-Path -LiteralPath $ledgerDir)) { New-Item -ItemType Directory -Path $ledgerDir -Force | Out-Null }
    $tmp = $ledgerPath + '.tmp'
    $ordered = [ordered]@{}
    foreach ($k in ($chapters.Keys | Sort-Object)) { $ordered[$k] = $chapters[$k] }
    $obj = [pscustomobject]@{ version = 1; chapters = [pscustomobject]$ordered }
    [System.IO.File]::WriteAllText($tmp, ($obj | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
    # 三参 File.Move 为 .NET Core 独有 → Move-Item -Force（5.1/7 通用，失败保留原文件）
    Move-Item -LiteralPath $tmp -Destination $ledgerPath -Force
    [Console]::Out.WriteLine("【文本指纹】第$nnn → sha256=$(Get-Short $d[0]) | 单元=$($d[1]) | 文件=$rel | 已记录")
    exit 0
}

if ($Command -eq 'verify') {
    $nnn = Format-Ch $Chapter
    if (-not $chapters.ContainsKey($nnn)) { [Console]::Out.WriteLine("【手改检测】第$nnn 无指纹记录 → 先跑 digest"); exit 2 }
    $rec = $chapters[$nnn]
    $rel = Find-Body $Project $Chapter
    if ($rel -eq '') { $rel = $rec.file }
    $fp = Join-Path $Project $rel
    if (-not (Test-Path -LiteralPath $fp -PathType Leaf)) { [Console]::Out.WriteLine("【手改检测】第$nnn 正文文件未找到（glob：正文/第${nnn}章_*.txt）"); exit 2 }  # Leaf 对齐 py isfile（目录顶替 ReadAllBytes 裸崩）
    $d = Get-Digest $fp
    if ($d[0] -ceq [string]$rec.sha256) {
        [Console]::Out.WriteLine("【手改检测】第$nnn 一致（sha256=$(Get-Short $d[0]) | 单元=$($d[1])）")
        exit 0
    }
    $diff = [long]$d[1] - [long]$rec.units  # [long] 对齐 :114 判型闸放行域（[int] 收窄超 Int32 裸 .NET 异常分叉）
    $sign = if ($diff -ge 0) { "+$diff" } else { "$diff" }
    [Console]::Out.WriteLine("【手改检测】第$nnn 与记录不一致 —— 旧 sha256=$(Get-Short $rec.sha256)/单元=$($rec.units)，新 sha256=$(Get-Short $d[0])/单元=$($d[1])（差 $sign）→ 必须停下询问：沿用旧稿/按新稿重写/只看不处理（03_26 五·文本指纹）")
    exit 1
}

# verify-all
$okN = 0; $badN = 0
$keyList = [string[]]@($chapters.Keys)
    [Array]::Sort($keyList, [System.StringComparer]::Ordinal)  # Ordinal 序对齐 py sorted(keys)（culture Sort-Object 会重排混合长度键；Sort-Ordinal 是 state_commit 函数不在本作用域）
    foreach ($keyRaw in $keyList) {
    $nnn = Format-Ch ([long]$keyRaw)  # 补零渲染对齐 py {int(k):03d}（直出原始键 "2" vs "002" 分叉）
    $rec = $chapters[$keyRaw]
    $rel = Find-Body $Project ([long]$keyRaw)
    if ($rel -eq '') { $rel = $rec.file }
    $fp = Join-Path $Project $rel
    if (-not (Test-Path -LiteralPath $fp -PathType Leaf)) {  # Leaf 对齐 py isfile（目录顶替 ReadAllBytes 裸崩）
        [Console]::Out.WriteLine(("【手改检测】第{0} 正文文件未找到（记录：{1}）" -f $nnn, $rec.file))
        $badN++; continue
    }
    $d = Get-Digest $fp
    if ($d[0] -ceq [string]$rec.sha256) { $okN++ } else {
        $badN++
        [Console]::Out.WriteLine("【手改检测】第$nnn 与记录不一致（旧 $(Get-Short $rec.sha256) / 新 $(Get-Short $d[0])）→ 停下询问：沿用/重写/只看")
    }
}
[Console]::Out.WriteLine("【手改检测】全书：一致 $okN 章 / 不一致 $badN 章")
if ($badN -gt 0) { exit 1 }
exit 0
