<#
.SYNOPSIS
  foreshadow_timeline.ps1 —— 伏笔时间线可视化渲染（MU-1，P3-21）。
  与 foreshadow_timeline.py 双实现对拍：产物与 stdout 逐字节一致。
  数据源：_state.json.foreshadow（新架构）或 记忆系统/圣经/伏笔清单.md 表格（旧项目）。
  产物：记忆系统/圣经/伏笔时间线.md（过期点名 + 状态表 + Mermaid gantt），派生件禁手改。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$CurrentCh = '0'
)
# 参数错误收敛：纯 ASCII 十进制（[long] 会把 "1e3"/"+5"/"0x10"/" 5"/"" 静默转换，与 py _i64 反向裁决）
if ($CurrentCh -notmatch '^-?[0-9]+$') { [Console]::Out.WriteLine('❌ 参数错误：章节号须为纯 ASCII 十进制整数（对齐 py _i64）'); exit 1 }
$CurrentChRaw = $CurrentCh  # param([string]) 类型约束会把赋值回炉成 String，先存原值再解约束重赋
Remove-Variable -Name CurrentCh -Force -ErrorAction SilentlyContinue
$_cvtCurrentCh = 0L
if (-not [long]::TryParse($CurrentChRaw, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$_cvtCurrentCh)) { [Console]::Out.WriteLine('❌ 参数错误：超出 Int64 范围（对齐 py _i64）'); exit 1 }  # TryParse 收敛：裸 [long] cast 在 EAP Stop 前是非终止错误、变量落 $null fail-open（纯十进制超界静默放行分叉）
$CurrentCh = $_cvtCurrentCh

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$MARK = '<!-- generated:foreshadow-timeline v1 | 派生件禁手改，重跑本脚本再生成 -->'

function Get-Day([long]$ch) {
    if (([math]::Max($ch, 1) - 1) -gt 2913088) {  # date 9999-12-31 上界；对齐 py day_of（不再裸 ArgumentOutOfRangeException）
        [Console]::Out.WriteLine(('❌ 章号超出日期换算上界：{0}（≤2913088 才能映射到 9999 年内）' -f $ch)); exit 1
    }
    $n = [Math]::Max($ch, 1)
    return ([datetime]::new(2000, 1, 1)).AddDays($n - 1).ToString('yyyy-MM-dd', $inv)
}
function Get-Digits([string]$s) {
    $m = [regex]::Match([string]$s, '([0-9]+)')  # ASCII 收口对齐 py（.NET \d 认 Unicode 数字族 ١٢/全角）
    if ($m.Success) {
        $n = 0L
        if (-not [long]::TryParse($m.Groups[1].Value, [ref]$n)) { [Console]::Out.WriteLine(("❌ 章号超出日期换算上界：{0}（≤2913088 才能映射到 9999 年内）" -f $m.Groups[1].Value)); exit 1 }  # 超 Int64 同文案兜底对齐 py day_of（裸 .NET 异常为分叉）
        return $n
    }
    return $null
}
function Split-Row([string]$line) {
    $s = $line.Trim()
    if ($s.StartsWith('|')) { $s = $s.Substring(1) }
    if ($s.EndsWith('|')) { $s = $s.Substring(0, $s.Length - 1) }
    return @($s -split '\|' | ForEach-Object { $_.Trim() })
}
$TitleBad = [regex]'[:,：，]'

$statePath = Join-Path $Project '记忆系统/_state.json'
$items = @()
$src = 'state'
$hasState = $false
if (Test-Path -LiteralPath $statePath) {
    try {
        $probeSt = [System.IO.File]::ReadAllText($statePath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    } catch {
        Write-Output '❌ 记忆系统/_state.json JSON 损坏（先 state_commit check 修复状态单）'; exit 1  # 对齐 py load_state：损坏 rc1（不再降级「无数据源」）
    }
    if ($null -eq $probeSt -or $probeSt.GetType().FullName -ne 'System.Management.Automation.PSCustomObject') {  # null 兜底：pwsh7 管道 ConvertFrom-Json 会把空数组 [] 枚举成 $null（PS5.1 不枚举）；数组/字符串/数字按类型全名硬判（对齐 py isinstance(dict)）
        Write-Output '❌ 记忆系统/_state.json 须为对象（先 state_commit check 修复状态单）'; exit 1  # 对齐 py 顶层判型
    }
    # 与 py 同口径：state 存在且 foreshadow 键非 null 才走 state，否则回退清单
    $fsProbe = $probeSt.PSObject.Properties['foreshadow']
    if ($null -ne $fsProbe -and $null -ne $fsProbe.Value -and $fsProbe.Value -isnot [pscustomobject]) {
        Write-Output '❌ foreshadow 须为对象（先 state_commit check 修复状态单）'; exit 1  # 对齐 py from_state 顶层数值判型
    }
    $hasState = ($null -ne $fsProbe -and $null -ne $fsProbe.Value)
}

if (-not $hasState) {
    $clPath = Join-Path $Project '记忆系统/圣经/伏笔清单.md'
    if (-not (Test-Path -LiteralPath $clPath)) {
        [Console]::Out.WriteLine('❌ 无数据源：既无 记忆系统/_state.json 也无 记忆系统/圣经/伏笔清单.md')
        exit 2
    }
    $src = '清单'
    $lines = [System.IO.File]::ReadAllLines($clPath, [System.Text.Encoding]::UTF8)
    $header = $null
    $idx = @{}
    $seq = 0
    foreach ($line in $lines) {
        if (-not $line.Trim().StartsWith('|')) { $header = $null; $idx = @{}; continue }
        $cells = Split-Row $line
        if ($cells.Count -eq 0) { continue }
        if ($null -eq $header) {
            $joined = -join $cells
            if ($joined.Contains('首现') -and $joined.Contains('状态')) {
                $header = $cells
                $idx = @{}
                for ($i = 0; $i -lt $cells.Count; $i++) {
                    $c = $cells[$i]
                    if ($c.Contains('伏笔') -and -not $idx.ContainsKey('name')) { $idx['name'] = $i }
                    elseif ($c.StartsWith('首现') -and -not $idx.ContainsKey('first')) { $idx['first'] = $i }
                    elseif ($c -eq '状态') { $idx['status'] = $i }
                    elseif ($c.Contains('预期回收')) { $idx['plan'] = $i }
                    elseif (($c -eq '#' -or $c -eq '＃') -and -not $idx.ContainsKey('id')) { $idx['id'] = $i }
                }
                if (-not $idx.ContainsKey('first') -or -not $idx.ContainsKey('status')) { $header = $null; $idx = @{} }
            }
            continue
        }
        $allDash = $true
        foreach ($c in $cells) { if ($c -ne '' -and $c -notmatch '^:?-+:?$') { $allDash = $false; break } }
        if ($allDash) { continue }
        $name = ''
        if ($idx.ContainsKey('name') -and $idx['name'] -lt $cells.Count) { $name = $cells[$idx['name']] }
        if ($name -eq '') { continue }
        $seq++
        $firstTxt = ''
        if ($idx.ContainsKey('first') -and $idx['first'] -lt $cells.Count) { $firstTxt = $cells[$idx['first']] }
        $planted = Get-Digits $firstTxt
        if ($null -eq $planted -or $planted -eq 0) { continue }
        $rawId = ''
        if ($idx.ContainsKey('id') -and $idx['id'] -lt $cells.Count) { $rawId = $cells[$idx['id']] }
        $fid = ''
        if ($rawId -ne '') {
            # [0-9] 收口对齐 py isascii（.NET \d=\p{Nd} 认 ١٢/全角，py Nd 族不加 # 为产物分叉）
            if ($rawId -match '^[0-9]+$') { $fid = "#$rawId" } else { $fid = $rawId }
        } else { $fid = 'F{0:D3}' -f $seq }
        $st = ''
        if ($idx.ContainsKey('status') -and $idx['status'] -lt $cells.Count) { $st = $cells[$idx['status']] }
        $status = 'open'
        if ($st.Contains('已回收')) { $status = 'resolved' }
        elseif ($st.Contains('废弃')) { $status = 'dropped' }
        elseif ($st.Contains('暂停')) { $status = 'paused' }
        elseif ($st.Contains('准备回收')) { $status = 'near' }
        $planTxt = ''
        if ($idx.ContainsKey('plan') -and $idx['plan'] -lt $cells.Count) { $planTxt = $cells[$idx['plan']] }
        $planned = Get-Digits $planTxt
        if ($null -eq $planned) { $planned = 0 }
        $items += [pscustomobject]@{ id = $fid; name = $name; planted = [long]$planted; planned = [long]$planned; resolved = 0; status = $status; seq = $items.Count }
    }
} else {
    $state = [System.IO.File]::ReadAllText($statePath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $fs = @{}
    if ($null -ne $state.PSObject.Properties['foreshadow'] -and $null -ne $state.foreshadow) {
        foreach ($p in $state.foreshadow.PSObject.Properties) { $fs[$p.Name] = $p.Value }  # @{} 仅供存在性/取值；遍历顺序须走 Properties（Keys 枚举序随引擎哈希漂移 → 首错归因分叉）
    }
function Get-NumCh($v, [string]$k, [string]$field) {
    # 对齐 py _numch：缺失→0；bool→rc1「bool 不是章号」；非数值→rc1「章号不是数值：<repr>」；浮点向零截断且不小于 0
    if ($null -eq $v.PSObject.Properties[$field]) { return 0 }
    $raw = $v.$field
    if ($raw -is [bool]) { if ($raw) { [Console]::Out.WriteLine(("❌ foreshadow.{0} 章号非法：bool 不是章号" -f $k)); exit 1 }; return 0 }  # false 按 0 对齐 py（true 才非法）
    if ($raw -is [string] -and $raw -eq '') { return 0 }  # 空串按 0 对齐 py None/false/空串口径
    try {
        # [double] 宽松转换拒：pwsh7 把 "0x10" 静默转 16（PS5.1/py 拒）→ 用 invariant TryParse 纯十进制（对齐 py float() 拒 hex/Unicode 数字族）
        $d = 0.0
        if ($raw -isnot [string]) { $d = [double]$raw }
        elseif (-not [double]::TryParse([string]$raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) { throw 'nan' }
        if ([double]::IsInfinity($d) -or [double]::IsNaN($d)) { throw 'nan' }
        return [Math]::Max(0, [long][Math]::Truncate($d))
    } catch {
        # 统一 JSON/G 渲染对齐 py（str→json.dumps 双引号转义；数组/对象→ConvertTo-Json；浮点→[string] 大写 E；None→None）
        # 已知口径：PS5.1 ConvertFrom-Json 把超 Int64 整数字面量解析为 Double → [string] 渲染 1E+30（pwsh7 BigInteger/py 精确 int 全数字），仅错误文案数字渲染差异，rc 三引擎同 1 fail-closed，接受为解析器固有差异；浮点溢出字面量（1e400）同理：PS5.1 ConvertFrom-Json 解析层拒「JSON 损坏」、py 走「章号不是数值：Infinity」，rc 同 1 fail-closed
        $repr = if ($null -eq $raw) { 'None' } elseif ($raw -is [string] -or $raw -is [array] -or $raw -is [System.Collections.IDictionary] -or $raw.GetType().FullName -eq 'System.Management.Automation.PSCustomObject') { (ConvertTo-Json -InputObject $raw -Compress -Depth 4) } else { [string]$raw }
        [Console]::Out.WriteLine(("❌ foreshadow.{0} 章号非法：章号不是数值：{1}" -f $k, $repr)); exit 1
    }
}
    foreach ($fsProp in $state.foreshadow.PSObject.Properties) {  # 保 JSON 插入序遍历对齐 py dict 序（$fs.Keys 枚举序随引擎哈希漂移 → 多条目同坏时首错键名三引擎三样）
        $k = $fsProp.Name
        $v = $fs[$k]
        if ($v -isnot [pscustomobject]) { Write-Output ("❌ foreshadow.{0} 须为对象（先 state_commit check 修复状态单）" -f $k); exit 1 }
        # 章号解析对齐 py _numch：非法 rc1「章号非法」中文文案（裸 cast .NET 异常为分叉）；Truncate 对齐 py int()
        $pl = Get-NumCh $v $k 'planted_ch'
        $planned = Get-NumCh $v $k 'planned_resolve_ch'
        $rs = Get-NumCh $v $k 'resolved_ch'
        $stt = 'open'; if ($rs -gt 0) { $stt = 'resolved' }
        $descRaw = $null; if ($null -ne $v.PSObject.Properties['desc']) { $descRaw = $v.desc }
        $desc = ''; if ($descRaw -is [string]) { $desc = $descRaw }  # 非字符串 desc 按空串对齐 py（[string] cast 渲染 "1 2" vs py "[1, 2]" 分叉）
        $items += [pscustomobject]@{ id = [string]$k; name = $desc; planted = $pl; planned = $planned; resolved = $rs; status = $stt; seq = $items.Count }
    }
}

# 当前章（与 py 同源：正文最大编号 → state.last_committed → 0）
if ($CurrentCh -le 0) {
    $CurrentCh = [long]0  # 显式 [long]：= 0 会把变量重绑 Int32，下游 [Math]::Max 绑到 Max(int,int) 对 planted+1>2^31 裸崩
    $bodyDir = Join-Path $Project '正文'
    if (Test-Path -LiteralPath $bodyDir) {
        foreach ($f in Get-ChildItem -File -LiteralPath $bodyDir) {  # -Path 通配含 [] 路径会漏文件 → 当前章口径分叉（对齐 package_skill 同型修复）
            $m = [regex]::Match($f.Name, '^第([0-9]+)章')  # ASCII 数字收口对齐 py（.NET \d 同认 Unicode 数字族）
            if ($m.Success) {
                $n = 0
                if (-not [long]::TryParse($m.Groups[1].Value, [ref]$n)) { [Console]::Out.WriteLine(("❌ 章号超出日期换算上界：{0}（≤2913088 才能映射到 9999 年内）" -f $m.Groups[1].Value)); exit 1 }  # 超 Int64 同文案兜底对齐 py（裸 .NET 溢出异常为分叉）
                if ($n -gt $CurrentCh) { $CurrentCh = $n }
            }
        }
    }
    if ($CurrentCh -eq 0 -and $hasState) {
        $st2 = [System.IO.File]::ReadAllText($statePath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($null -ne $st2.PSObject.Properties['last_committed_chapter'] -and $st2.last_committed_chapter) {
            # 对齐 py int(...)：整数/整数串 Truncate 截断；非整数串（"3.7"/"abc"）回退 0（[double] 宽松转换会把 "3.7" 截成 3 为分叉）
            try {
                $lc = $st2.last_committed_chapter
                if ($lc -is [string]) { if ($lc.Trim() -notmatch '^[0-9]+$') { throw 'lc' }; $CurrentCh = [long]::Parse($lc.Trim(), [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture) }  # 拒负号对齐 py isdecimal（"-3" 原被接受分叉）
                else {
                    $lcD = [double]$lc
                    if ($lcD -lt 0) { throw 'lc' }  # 负数拒对齐 py best<0 回退 0（原直通「第-5章」分叉）
                    $CurrentCh = [long][math]::Truncate($lcD)  # 回 cast [long]：double 渲染科学计数法（E+18）与 py int 精确输出分叉
                }
            }
            catch { $CurrentCh = 0 }
        }
    }
}

# 排序：(planted, id) ordinal（插入排序，与 py sorted 一致）
for ($i = 1; $i -lt $items.Count; $i++) {
    $key = $items[$i]; $j = $i - 1
    while ($j -ge 0) {
        $dp = $items[$j].planted - $key.planted
        $worse = ($dp -eq 0) -and ([string]::CompareOrdinal([string]$items[$j].id, [string]$key.id) -gt 0)
        if (($dp -gt 0) -or $worse) { $items[$j + 1] = $items[$j]; $j-- } else { break }
    }
    $items[$j + 1] = $key
}

$overdue = @($items | Where-Object { ($_.status -eq 'open' -or $_.status -eq 'near') -and $_.planned -gt 0 -and $CurrentCh -gt $_.planned })
$overSeq = @{}
foreach ($o in $overdue) { $overSeq[[int]$o.seq] = $true }  # 序号判「不在过期集合」对齐 py `i not in overdue` 值相等语义（同值重复必然同进 overdue，seq 判等价；id 集合判会把同 id 另一条连坐排除 → 条目静默丢失分叉）
$pending = @($items | Where-Object { ($_.status -eq 'open' -or $_.status -eq 'near') -and -not $overSeq.ContainsKey([int]$_.seq) })
$resolved = @($items | Where-Object { $_.status -eq 'resolved' })
$rest = @($items | Where-Object { $_.status -eq 'dropped' -or $_.status -eq 'paused' })
$ord = [System.StringComparer]::Ordinal

$out = @()
$out += '# 伏笔时间线（派生件·禁手改）'
$out += $MARK
$out += ('> 数据源={0} ｜ 当前章=第{1}章 ｜ 条目 {2}：待收 {3} / 过期 {4} / 已回收 {5} / 停废 {6}' -f $src, $CurrentCh, $items.Count, $pending.Count, $overdue.Count, $resolved.Count, $rest.Count)
$out += ''

$StatusTxt = @{ resolved = '✅ 已回收'; dropped = '❌ 废弃'; paused = '⏸️ 暂停'; near = '🔄 准备回收'; open = '🔘 已埋设' }

function Add-Table([string]$title, $rows, [string]$mode) {
    $script:out += "## $title"
    if ($rows.Count -eq 0) { $script:out += '（无）'; $script:out += ''; return }
    $script:out += '| 线索 | 首现 | 计划回收 | 状态 | 备注 |'
    $script:out += '|---|---|---|---|---|'
    foreach ($i in $rows) {
        $plan = '—'
        if ($i.planned -gt 0) { $plan = "第$($i.planned)章" }
        $note = ''
        if ($mode -eq 'overdue') { $note = "超期 $($CurrentCh - $i.planned) 章" }
        elseif ($mode -eq 'pending') {
            if ($i.planned -gt 0 -and $i.planned -ge $CurrentCh) { $note = "剩 $($i.planned - $CurrentCh) 章" }
            else { $note = '未定回收章' }
        }
        $script:out += ('| {0} {1} | 第{2}章 | {3} | {4} | {5} |' -f $i.id, $i.name, $i.planted, $plan, $StatusTxt[[string]$i.status], $note)
    }
    $script:out += ''
}
Add-Table '🚨 过期未回收（优先清旧债）' $overdue 'overdue'
Add-Table '⏳ 待回收' $pending 'pending'
Add-Table '✅ 已回收' $resolved 'plain'
Add-Table '⏸️ 暂停 / ❌ 废弃' $rest 'plain'

$out += '## Mermaid 时间线（第 N 章 → 2000-01-01 起第 N 天）'
$out += '```mermaid'
$out += 'gantt'
$out += '    title 伏笔埋设与回收'
$out += '    dateFormat YYYY-MM-DD'
$out += '    axisFormat %Y-%m-%d'
$out += '    section 待回收'
$pendOrder = @()
foreach ($o in $overdue) { $pendOrder += $o }
foreach ($p in $pending) { $pendOrder += $p }
foreach ($i in $pendOrder) {
    $raw = '{0} {1} {2}-{3}' -f $i.id, $i.name, $i.planted, $(if ($i.planned -gt 0) { $i.planned } else { $CurrentCh })
    $title = $TitleBad.Replace($raw, '')
    $endCh = $i.planned
    if ($endCh -le 0) { $endCh = [Math]::Max($CurrentCh, $i.planted + 1) }
    if ($endCh -le $i.planted) { $endCh = $i.planted + 1 }
    $tag = ''
    if ($overSeq.ContainsKey([int]$i.seq)) { $tag = 'crit' }  # seq 判对齐 py 值相等（原 $overIds 已改名，此处漏改致 null 方法裸崩）
    elseif ($i.status -eq 'near') { $tag = 'active' }
    if ($tag -ne '') { $out += ('    {0} :{1}, {2}, {3}' -f $title, $tag, (Get-Day $i.planted), (Get-Day $endCh)) }
    else { $out += ('    {0} : {1}, {2}' -f $title, (Get-Day $i.planted), (Get-Day $endCh)) }
}
$out += '    section 已回收'
if ($resolved.Count -eq 0) { $out += '    （无）' }
foreach ($i in $resolved) {
    $endR = $i.resolved
    if ($endR -le 0) { $endR = $i.planned }
    if ($endR -le 0) { $endR = $i.planted + 1 }
    if ($endR -le $i.planted) { $endR = $i.planted + 1 }
    $raw = '{0} {1} {2}-{3}' -f $i.id, $i.name, $i.planted, $endR
    $title = $TitleBad.Replace($raw, '')
    $out += ('    {0} :done, {1}, {2}' -f $title, (Get-Day $i.planted), (Get-Day $endR))
}
$out += '```'
$out += ''

$target = Join-Path $Project '记忆系统/圣经/伏笔时间线.md'
$tDir = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $tDir)) { New-Item -ItemType Directory -Path $tDir -Force | Out-Null }
$tmp = "$target.tmp"
[System.IO.File]::WriteAllText($tmp, (($out -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
# 三参 File.Move 为 .NET Core 独有 → Move-Item -Force（5.1/7 通用，失败保留原文件）
Move-Item -LiteralPath $tmp -Destination $target -Force

[Console]::Out.WriteLine(('【伏笔时间线】源={0} ｜ 当前章=第{1}章 ｜ 条目 {2}：待收 {3} / 过期 {4} / 已回收 {5} / 停废 {6}' -f $src, $CurrentCh, $items.Count, $pending.Count, $overdue.Count, $resolved.Count, $rest.Count))
[Console]::Out.WriteLine('已写出：记忆系统/圣经/伏笔时间线.md')
foreach ($i in $overdue) {
    [Console]::Out.WriteLine(('🚨 过期：{0} {1}（第{2}章埋 → 计划第{3}章收，超期 {4} 章）' -f $i.id, $i.name, $i.planted, $i.planned, ($CurrentCh - $i.planned)))
}
exit 0
