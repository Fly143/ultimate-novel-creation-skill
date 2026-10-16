<#
.SYNOPSIS
  text_guard.ps1 —— 正文指纹与手改保护（P1-9）。
  与 text_guard.py 双实现对拍：输出逐字节一致（长度=UTF-16 单元；sha256=正文 UTF-8 字节小写十六进制）。
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
    [int]$Chapter = 0
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

$rulesPath = Join-Path $scriptDir 'check-done-rules.json'
$rules = [System.IO.File]::ReadAllText($rulesPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$globs = @($rules.body_globs)

function Find-Body([string]$proj, [int]$ch) {
    $nnn = '{0:D3}' -f $ch
    foreach ($g in $globs) {
        $relPat = $g.Replace('{nnn}', $nnn).Replace('{ch}', "$ch")
        $dir = Join-Path $proj (Split-Path $relPat -Parent)
        $name = Split-Path $relPat -Leaf
        if (Test-Path -LiteralPath $dir) {
            $hits = @(Get-ChildItem -File -Path $dir -Filter $name | Sort-Object -Property Name)
            if ($hits.Count -gt 0) {
                $rel = $hits[0].FullName.Substring((Resolve-Path -LiteralPath $proj).Path.Length).TrimStart('\', '/').Replace('\', '/')
                return $rel
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
    $ledger = [System.IO.File]::ReadAllText($ledgerPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
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
    # 原子写：临时文件 + 覆盖移动
    $tmp = $ledgerPath + '.tmp'
    $ordered = [ordered]@{}
    foreach ($k in ($chapters.Keys | Sort-Object)) { $ordered[$k] = $chapters[$k] }
    $obj = [pscustomobject]@{ version = 1; chapters = [pscustomobject]$ordered }
    [System.IO.File]::WriteAllText($tmp, ($obj | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::Move($tmp, $ledgerPath, $true)
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
    [Console]::Out.WriteLine("【手改检测】第$nnn 与记录不一致 —— 旧 sha256=$(Get-Short $rec.sha256)/单元=$($rec.units)，新 sha256=$(Get-Short $d[0])/单元=$($d[1])（差 $sign）→ 必须停下询问：沿用旧稿/按新稿重写/只看不处理（03_26 步骤7之二）")
    exit 1
}

# verify-all
$okN = 0; $badN = 0
foreach ($nnn in ($chapters.Keys | Sort-Object)) {
    $rec = $chapters[$nnn]
    $rel = Find-Body $Project ([int]$nnn)
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
