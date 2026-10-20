<#
.SYNOPSIS
  detect_ai_text.ps1 —— AI 文本启发式检测（本地辅助，P1-8）。
  与 detect_ai_text.py 双实现对拍：输出逐字节一致（长度=UTF-16 单元；数字用 InvariantCulture）。
  **不门禁、不替代朱雀三态**；0 命中≠达标。
.PARAMETER InputFile
  待检文本（UTF-8）。如 正文/第001章_试炼.txt
.PARAMETER ExtraLexicon
  可选外部词表（一行一词，# 注释）；与内置 scripts/ai-tells.txt 合并
.PARAMETER FailOver
  总命中 ≥ N 时 exit 1；0=关闭（默认）
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputFile,
    [string]$ExtraLexicon = "",
    [string]$FailOver = '0'
)
# 参数错误收敛：纯 ASCII 十进制（[long] 会把 "1e3"/"+5"/"0x10"/" 5"/"" 静默转换，与 py _i64 反向裁决）
if ($FailOver -notmatch '^-?[0-9]+$') { [Console]::Out.WriteLine('❌ 参数错误：--fail-over 须为纯 ASCII 十进制整数（对齐 py _i64）'); exit 1 }
$FailOverRaw = $FailOver  # param([string]) 类型约束会把赋值回炉成 String，先存原值再解约束重赋
Remove-Variable -Name FailOver -Force -ErrorAction SilentlyContinue
$_cvtFailOver = 0L
if (-not [long]::TryParse($FailOverRaw, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$_cvtFailOver)) { [Console]::Out.WriteLine('❌ 参数错误：超出 Int64 范围（对齐 py _i64）'); exit 1 }  # TryParse 收敛：裸 [long] cast 在 EAP Stop 前是非终止错误、变量落 $null fail-open（纯十进制超界静默放行分叉）
$FailOver = $_cvtFailOver

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

$inv = [System.Globalization.CultureInfo]::InvariantCulture
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

function Load-Lexicon([string[]]$paths) {
    $seen = @{}; $out = @()
    foreach ($p in $paths) {
        if ([string]::IsNullOrWhiteSpace($p) -or -not (Test-Path -LiteralPath $p -PathType Leaf)) { continue }  # -PathType Leaf 对齐 py isfile（目录须跳过，ReadAllLines 目录裸崩）
        foreach ($line in [System.IO.File]::ReadAllLines($p, [System.Text.Encoding]::UTF8)) {
            $s = $line.Trim($wsChars)
            if ($s -ne '' -and -not $s.StartsWith('#') -and -not $seen.ContainsKey($s)) {
                $seen[$s] = $true; $out += $s
            }
        }
    }
    return $out
}
# py 口径 ws 全集（str.isspace/strip；.NET 默认 Trim 不含 U+001C-U+001F，缺它们即与 py 反向）
$wsChars = [char[]]@(0x09,0x0a,0x0b,0x0c,0x0d,0x1c,0x1d,0x1e,0x1f,0x20,0x85,0xa0,0x1680,0x2000,0x2001,0x2002,0x2003,0x2004,0x2005,0x2006,0x2007,0x2008,0x2009,0x200a,0x2028,0x2029,0x202f,0x205f,0x3000)
$quoteChars = [char[]]@(0x22,0x27,0x300c,0x300d,0x300e,0x300f)
function Trim-Sent([string]$s) {
    # py 口径：s.strip()（ws 全集一轮）→ .strip(引号)（一轮），不交替（旧实现交替切得比 py 多）
    $s = $s.TrimStart($wsChars); $s = $s.TrimEnd($wsChars)
    $s = $s.TrimStart($quoteChars); $s = $s.TrimEnd($quoteChars)
    return $s
}

if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) { Write-Output ('❌ 文件不存在：{0}' -f $InputFile); exit 1 }  # Leaf 判定（目录亦判不存在，对齐 py isfile）
$raw = [System.IO.File]::ReadAllText($InputFile, [System.Text.Encoding]::UTF8)
$t = $raw.Replace("`r`n", "`n").Replace("`r", "`n")  # py open() 通用换行：lone \r 亦归 \n
$units = $t.Length
$paras = @($t -split "`n" | Where-Object { $_.Trim($wsChars) -ne '' })
$splitRe = [regex]'[。！？；…]+'
$sents = @()
foreach ($s in $splitRe.Split($t)) {
    $u = Trim-Sent $s
    if ($u -ne '') { $sents += $u }
}

$hitsTotal = 0
$lines = @()
$lines += '【AI 文本启发式检测】（本地辅助，非朱雀）'
$lines += "文件：$InputFile"
$lines += "字符（UTF-16 单元）：$units ｜ 段落：$($paras.Count) ｜ 句子：$($sents.Count)"

# [A] 高频套话
$paths = @((Join-Path $scriptDir 'ai-tells.txt'))
if ($ExtraLexicon -ne '') { $paths += $ExtraLexicon }
$toks = Load-Lexicon $paths
$pairs = @()
foreach ($tok in $toks) {
    $c = 0; $idx = 0
    while ($true) {
        $idx = $t.IndexOf($tok, $idx, [System.StringComparison]::Ordinal)
        if ($idx -lt 0) { break }
        $c++; $idx += $tok.Length
    }
    if ($c -gt 0) { $pairs += , @($tok, $c) }
}
# 排序：计数降序 → 词序升序（ordinal，与 py 的 str 比较一致）
$sorted = New-Object System.Collections.ArrayList
foreach ($p in $pairs) { [void]$sorted.Add($p) }
for ($i = 1; $i -lt $sorted.Count; $i++) {
    $key = $sorted[$i]; $j = $i - 1
    while ($j -ge 0) {
        $dc = [int]$sorted[$j][1] - [int]$key[1]
        $same = ($dc -eq 0)
        $worse = $same -and ([string]::CompareOrdinal([string]$sorted[$j][0], [string]$key[0]) -gt 0)
        if (($dc -lt 0) -or $worse) { $sorted[$j + 1] = $sorted[$j]; $j-- } else { break }
    }
    $sorted[$j + 1] = $key
}
$aKinds = $sorted.Count
$hitsTotal += $aKinds
$sumA = 0; foreach ($p in $sorted) { $sumA += [int]$p[1] }
$partsA = @()
foreach ($p in $sorted) { $partsA += "$($p[0])×$($p[1])" }
$detailA = if ($partsA.Count) { $partsA -join '，' } else { '（无）' }
$lines += "[A] 高频套话：$aKinds 种 / 共 $sumA 次 —— $detailA"

# [B] 同头连句
$runs = @()
$i = 0
while ($i -lt ($sents.Count - 2)) {
    $k = $sents[$i].Substring(0, [Math]::Min(2, $sents[$i].Length))
    $j = $i
    while (($j + 1) -lt $sents.Count) {
        $k2 = $sents[$j + 1].Substring(0, [Math]::Min(2, $sents[$j + 1].Length))
        if ($k2 -ceq $k) { $j++ } else { break }
    }
    if ((($j - $i) -ge 2) -and ($k -ne '')) { $runs += , @(($i + 1), ($j + 1), $k); $i = $j + 1 } else { $i++ }
}
$hitsTotal += $runs.Count
$partsB = @()
foreach ($r in $runs) { $partsB += "第$($r[0])-$($r[1])句同头「$($r[2])」" }
$detailB = if ($partsB.Count) { $partsB -join '，' } else { '（无）' }
$lines += "[B] 同头连句（连续≥3句同起2字）：$($runs.Count) 处 —— $detailB"

# [C] 句长均匀
$cHit = 0; $ratio = 0.0
if ($sents.Count -ge 10) {
    $lens = @(foreach ($s in $sents) { $s.Length })
    $mean = ($lens | Measure-Object -Average).Average
    $sum2 = 0.0; foreach ($x in $lens) { $sum2 += ($x - $mean) * ($x - $mean) }
    $ratio = if ($mean -gt 0) { [Math]::Sqrt($sum2 / $lens.Count) / $mean } else { 0.0 }
    if ($ratio -lt 0.25) { $cHit = 1 }
}
$hitsTotal += $cHit
$lines += "[C] 句长均匀（std/mean）：$($ratio.ToString('0.000', $inv))（阈值<0.25 命中，句数<10 不判）→ $(if ($cHit) { '命中' } else { '未命中' })"

# [D] 长句占比
$dHit = 0; $pct = 0.0
if ($sents.Count -ge 5) {
    $long = @($sents | Where-Object { $_.Length -gt 60 }).Count
    $pct = 100.0 * $long / $sents.Count
    if ($pct -gt 40.0) { $dHit = 1 }
}
$hitsTotal += $dHit
$lines += "[D] 长句占比（>60 单元）：$($pct.ToString('0.0', $inv))%（阈值>40% 命中，句数<5 不判）→ $(if ($dHit) { '命中' } else { '未命中' })"

# [E] 标点密度
$d1 = if ($units -gt 0) { 1000.0 * ([regex]::Matches($t, '…')).Count / $units } else { 0.0 }
$d2 = if ($units -gt 0) { 1000.0 * ([regex]::Matches($t, '！')).Count / $units } else { 0.0 }
$d3 = if ($units -gt 0) { 1000.0 * ([regex]::Matches($t, '——')).Count / $units } else { 0.0 }
$e1h = if ($d1 -gt 15) { 1 } else { 0 }
$e2h = if ($d2 -gt 30) { 1 } else { 0 }
$e3h = if ($d3 -gt 10) { 1 } else { 0 }
$eHit = $e1h + $e2h + $e3h
$hitsTotal += $eHit
$lines += "[E] 标点密度（每千单元）：省略号 $($d1.ToString('0.0', $inv))（>15 命中）/ 感叹号 $($d2.ToString('0.0', $inv))（>30 命中）/ 双破折号 $($d3.ToString('0.0', $inv))（>10 命中）→ $eHit 项命中"

$lines += "总命中：$hitsTotal"
$lines += '说明：本地启发式仅供参考，不构成朱雀结论；0 命中≠达标，达标以用户回传朱雀三态为准。'
foreach ($l in $lines) { [Console]::Out.WriteLine($l) }

if (($FailOver -gt 0) -and ($hitsTotal -ge $FailOver)) { exit 1 }
exit 0
