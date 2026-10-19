<#
.SYNOPSIS
  text_guard.ps1 —— 正文指纹与手改保护（P1-9）。
  与 text_guard.py 双实现对拍：输出逐字节一致（长度=UTF-16 单元；sha256=正文 UTF-8 字节小写十六进制）。账本 JSON 文件格式不承诺跨引擎一致（py=indent2+sort_keys+LF，ps1=ConvertTo-Json 键序/缩进随引擎），跨引擎 verify 语义一致。
  记录 {书名}/.story/text-digests.json（临时文件+原子替换）。不一致 → exit1 + 「沿用/重写/只看」处置口径。
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
$Chapter = [long]$ChapterRaw

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

function Find-Body([string]$proj, [long]$ch) {
    $nnn = '{0:D3}' -f $ch
    foreach ($g in (Get-BodyGlobs)) {
        $relPat = $g.Replace('{nnn}', $nnn).Replace('{ch}', "$ch")
        $dirRel = (Split-Path $relPat -Parent).Replace('\', '/')
        $name = Split-Path $relPat -Leaf
        $dir = Join-Path $proj $dirRel
        if (Test-Path -LiteralPath $dir) {
            $hits = @(Get-ChildItem -File -Path $dir -Filter $name | Sort-Object -Property Name)
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
function Get-Short([string]$sha) { return $sha.Substring(0, 12) }

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
$chapters = @{}
foreach ($p in $ledger.chapters.PSObject.Properties) { $chapters[$p.Name] = $p.Value }

if ($Command -eq 'digest') {
    $rel = Find-Body $Project $Chapter
    if ($rel -eq '') { [Console]::Out.WriteLine("【文本指纹】第{0:D3} 正文文件未找到（glob：正文/第{0:D3}章_*.txt）" -f $Chapter); exit 2 }
    $d = Get-Digest (Join-Path $Project $rel)
    $nnn = '{0:D3}' -f $Chapter
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
    $nnn = '{0:D3}' -f $Chapter
    if (-not $chapters.ContainsKey($nnn)) { [Console]::Out.WriteLine("【手改检测】第$nnn 无指纹记录 → 先跑 digest"); exit 2 }
    $rec = $chapters[$nnn]
    $rel = Find-Body $Project $Chapter
    if ($rel -eq '') { $rel = $rec.file }
    $fp = Join-Path $Project $rel
    if (-not (Test-Path -LiteralPath $fp)) { [Console]::Out.WriteLine("【手改检测】第$nnn 正文文件未找到（glob：正文/第${nnn}章_*.txt）"); exit 2 }
    $d = Get-Digest $fp
    if ($d[0] -ceq [string]$rec.sha256) {
        [Console]::Out.WriteLine("【手改检测】第$nnn 一致（sha256=$(Get-Short $d[0]) | 单元=$($d[1])）")
        exit 0
    }
    $diff = [int]$d[1] - [int]$rec.units
    $sign = if ($diff -ge 0) { "+$diff" } else { "$diff" }
    [Console]::Out.WriteLine("【手改检测】第$nnn 与记录不一致 —— 旧 sha256=$(Get-Short $rec.sha256)/单元=$($rec.units)，新 sha256=$(Get-Short $d[0])/单元=$($d[1])（差 $sign）→ 必须停下询问：沿用旧稿/按新稿重写/只看不处理（03_26 五·文本指纹）")
    exit 1
}

# verify-all
$okN = 0; $badN = 0
foreach ($nnn in ($chapters.Keys | Sort-Object)) {
    $rec = $chapters[$nnn]
    $rel = Find-Body $Project ([long]$nnn)
    if ($rel -eq '') { $rel = $rec.file }
    $fp = Join-Path $Project $rel
    if (-not (Test-Path -LiteralPath $fp)) {
        [Console]::Out.WriteLine("【手改检测】第$nnn 正文文件未找到（记录：$($rec.file)）")
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
