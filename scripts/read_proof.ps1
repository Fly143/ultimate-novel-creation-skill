# 读取证明校验/生成工具（P7 内容门禁）— PS1 镜像 read_proof.py
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][int]$Chapter,
    [string]$Proof = "",
    [switch]$Init,
    [string]$Reads = "",
    [int]$MinFiles = 3,
    [int]$MinExtracts = 2,
    [int]$MinChars = 6
)

$ErrorActionPreference = "Stop"
try { [System.Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$enc = New-Object System.Text.UTF8Encoding($false)
$encSig = New-Object System.Text.UTF8Encoding($true)

function Write-Line([string]$msg) { Write-Output $msg }

function ConvertTo-NormText([string]$s) {
    return [regex]::Replace($s, '[^\w]+', '')
}

function Get-Sha([string]$Path_) {
    try {
        if (Get-Command 'Get-FileHash' -ErrorAction SilentlyContinue) {
            return ((Get-FileHash -LiteralPath $Path_ -Algorithm SHA256).Hash.ToLower())
        }
    } catch { }
    # 兜底：环境里无 Get-FileHash（CI 精简 pwsh 态）时走 .NET SHA256，口径与 py hashlib 一致
    $sha = $null
    $stream = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path_)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $bytes = $sha.ComputeHash($stream)
        return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally {
        if ($stream) { $stream.Dispose() }
        if ($sha) { $sha.Dispose() }
    }
}

function Read-AnyText([string]$Path_) {
    try { return [System.IO.File]::ReadAllText($Path_, $encSig) } catch { }
    try { return [System.IO.File]::ReadAllText($Path_, $enc) } catch { }
    return [System.Text.Encoding]::GetEncoding("GB18030").GetString([System.IO.File]::ReadAllBytes($Path_))
}

function Parse-DateTime([string]$s) {
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    $dt = [datetime]::MinValue
    if ([datetime]::TryParse($s, [ref]$dt)) { return $dt }
    $d = 0.0
    if ([double]::TryParse([string]$s, [ref]$d)) {
        try { return [DateTimeOffset]::FromUnixTimeMilliseconds([int64]($d * 1000)).LocalDateTime } catch { return $null }
    }
    return $null
}

function Get-DefaultProofRel($root, [string]$Nnn, [int]$Ch) {
    $rels = @("报告/第${Nnn}章_读取证明.json", "报告/第${Ch}章_读取证明.json")
    for ($i = 0; $i -lt $rels.Count; $i++) {
        if (Test-Path -LiteralPath (Join-Path $root $rels[$i]) -PathType Leaf) { return $rels[$i] }
    }
    return "报告/第${Nnn}章_读取证明.json"
}

function Get-BodyCandidates($root, [string]$Nnn, [int]$Ch) {
    $out = @()
    $dir = Join-Path $root "正文"
    if (Test-Path -LiteralPath $dir -PathType Container) {
        foreach ($pat in @("第${Nnn}章_*.txt", "第${Ch}章_*.txt")) {
            try { $out += @(Get-ChildItem -LiteralPath $dir -Filter $pat | Where-Object { -not $_.PSIsContainer }) } catch { }
        }
    }
    return $out
}

function Join-Rel([string]$rel) { return Join-Path $ProjectRoot ($rel -replace '/', [string][char]92) }

$ProjectRoot = (Resolve-Path -LiteralPath $Project).Path
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    Write-Line "❌ 读取证明：项目目录不存在：$ProjectRoot"
    exit 1
}
$Nnn = "{0:D3}" -f $Chapter
if ($Proof -and $Proof -ne "") { $ProofRel = $Proof } else { $ProofRel = Get-DefaultProofRel $ProjectRoot $Nnn $Chapter }

if ($Init) {
    $rels = @([string[]]($Reads -split '[,;]')) | Where-Object { $_.Trim() -ne '' } | ForEach-Object { $_.Trim().Trim('"').Trim("'") }
    $arr = @()
    for ($i = 0; $i -lt $rels.Count; $i++) {
        $rel = $rels[$i]
        $p = Join-Rel -rel $rel
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
            Write-Line "❌ 读取证明初始化：输入档案不存在 -> $rel（--reads 必须指向项目内真实文件）"
            exit 1
        }
        $arr += [pscustomobject]@{
            path    = $rel
            sha256  = Get-Sha $p
            extracts = @()
            read_at = (Get-Date).ToString("yyyy-MM-dd'T'HH:mm:ss")
        }
    }
    if ($arr.Count -lt 1) {
        Write-Line "❌ 读取证明初始化：--reads 未提供任何档案（至少 3 份输入）"
        exit 1
    }
    $doc = [pscustomobject]@{
        chapter  = $Chapter
        generated = (Get-Date).ToString("yyyy-MM-dd'T'HH:mm:ss")
        note     = "写作输入留证：每份 input 的 extracts 填真实读到的原文子句(≥2 条/≥6 字)，交付前运行本脚本校验"
        reads    = @($arr)
    }
    $out = Join-Rel $ProofRel
    $dir = Split-Path -Parent $out
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [System.IO.File]::WriteAllText($out, ($doc | ConvertTo-Json -Depth 8), $enc)
    Write-Line "✅ 读取证明模板已生成：$ProofRel（$($arr.Count) 份输入；填入 extracts 后运行校验）"
    exit 0
}

$errs = @()
$pf = Join-Rel $ProofRel
if (-not (Test-Path -LiteralPath $pf -PathType Leaf)) {
    Write-Line "❌ 读取证明（第${Chapter}章）：缺读取证明文件 $ProofRel（写作必须留输入证据；见 modules/03_26 步骤1.5）"
    exit 1
}
try { $text = [System.IO.File]::ReadAllText($pf, $encSig) } catch { $text = "" }
try { $doc = $text | ConvertFrom-Json } catch {
    Write-Line "❌ 读取证明（第${Chapter}章）：读取证明解析失败 $ProofRel：$($_.Exception.Message)"
    exit 1
}
if ($null -eq $doc) {
    Write-Line "❌ 读取证明（第${Chapter}章）：读取证明格式非法（应为对象，含 chapter/reads）$ProofRel"
    exit 1
}
$docCh = $null
if ($null -ne $doc.PSObject.Properties['chapter']) { $docCh = $doc.chapter }
if ($null -ne $docCh -and ("$docCh" -ne "$Chapter")) {
    Write-Line "❌ 读取证明（第${Chapter}章）：章号不符（证明声明第${docCh}章）$ProofRel"
}
$readList = @()
if ($null -ne $doc.PSObject.Properties['reads']) { $readList = @($doc.reads) }
if ($readList.Count -lt 1) {
    Write-Line "❌ 读取证明（第${Chapter}章）：reads 缺失/为空（至少 $MinFiles 份输入）$ProofRel"
    exit 1
}

$cache = @{}
$total = 0
$seen = @{}
$ts = @()
for ($r = 0; $r -lt $readList.Count; $r++) {
    $item = $readList[$r]
    if ($null -eq $item -or $null -eq $item.PSObject) {
        $errs += "第$($r + 1) 项格式非法"
        continue
    }
    $rel = ""
    if ($null -ne $item.PSObject.Properties['path']) { $rel = [string]$item.path }
    $rel = $rel.Trim()
    if ($rel -eq "") { $errs += "第$($r + 1) 项缺 path"; continue }
    $seen.Add($rel, 1) | Out-Null
    $ra = $null
    $raStr = ""
    if ($null -ne $item.PSObject.Properties['read_at']) { $raStr = [string]$item.read_at }
    $dt = Parse-DateTime $raStr
    if ($null -ne $dt) { $ts += @(, @($dt, $r)) } else { $errs += "读取时间 read_at 缺失/不可解析「$raStr」（应 ISO 时间）" }
    $p = Join-Rel $rel
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
        $errs += "$rel 不存在"
        continue
    }
    $rec = ""
    if ($null -ne $item.PSObject.Properties['sha256']) { $rec = [string]$item.sha256 }
    $rec = $rec.Trim().ToLower()
    if ($rec -eq "") { $errs += "$rel 缺 sha256 声明（未做文件指纹）" }
    $act = Get-Sha $p
    if ($rec -ne "" -and -not ($act.StartsWith($rec) -or $rec.StartsWith($act))) {
        $errs += ($rel + " 的 sha256 不一致（记录 " + $rec.Substring(0, [Math]::Min(12, $rec.Length)) + " ≠ 实际 " + $act.Substring(0, 12) + "）")
    }
    $key = $rel
    if (-not $cache.ContainsKey($key)) {
        $cache.Add($key, (Read-AnyText $p))
    }
    $exs = @()
    if ($null -ne $item.PSObject.Properties['extracts']) { $exs = @($item.extracts) }
    if ($exs.Count -lt 1) {
        $errs += "$rel 未填抽取子句（extracts 为空，需≥$MinExtracts 条『真实读到』的原文子句）"
        continue
    }
    $total += $exs.Count
    if ($exs.Count -lt $MinExtracts) { $errs += "$rel 抽取子句不足（需≥$MinExtracts 条，实得 $($exs.Count)）" }
    $norm = ConvertTo-NormText $cache[$key]
    for ($e = 0; $e -lt $exs.Count; $e++) {
        $s = [string]$exs[$e]
        $s = $s.Trim()
        if ($s -eq "") { $errs += "$rel 存在空抽取子句"; continue }
        $ne = ConvertTo-NormText $s
        if ($ne.Length -lt $MinChars) {
            $errs += "$rel 的抽取子句长度不足（需≥$MinChars 汉字，实得 $($ne.Length)）"
            continue
        }
        if (-not $norm.Contains($ne)) {
            $snip = if ($s.Length -gt 24) { $s.Substring(0, 24) } else { $s }
            $errs += "$rel 的抽取子句「$snip」不在原文中（未读即写疑证）"
        }
    }
}

if ($seen.Count -lt $MinFiles) {
    $errs += "读取输入仅 $($seen.Count) 份，低于下限 $MinFiles（跨文件≥3）"
}

$bodies = @(Get-BodyCandidates $ProjectRoot $Nnn $Chapter)
if ($bodies.Count -gt 0 -and $ts.Count -gt 0) {
    $latest = $ts[0][0]
    for ($i2 = 0; $i2 -lt $ts.Count; $i2++) { if ($ts[$i2][0] -gt $latest) { $latest = $ts[$i2][0] } }
    foreach ($b in $bodies) {
        try {
            $bmt = $b.LastWriteTime
            if ($latest.ToUniversalTime() -gt $bmt.ToUniversalTime().AddSeconds(300)) {
                $errs += "读取时间晚于正文最终稿 mtime（$($latest.ToString('o')) > $($b.Name) $($bmt.ToString('o'))；顺序异常，疑似事后补证）"
            }
        } catch { }
    }
}

if ($errs.Count -gt 0) {
    Write-Line "❌ 读取证明（第${Chapter}章）未通过（$($errs.Count) 处）："
    for ($i3 = 0; $i3 -lt $errs.Count; $i3++) {
        if ($i3 -lt 10) { Write-Line "  · $($errs[$i3])" }
    }
    for ($i4 = 10; $i4 -lt $errs.Count; $i4++) { Write-Line "  · $($errs[$i4])" }
    exit 1
}
Write-Line "✅ 读取证明：第${Chapter}章；共 $($seen.Count) 份输入、$total 条抽取子句全部命中原文"
exit 0
