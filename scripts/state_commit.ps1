# ============================================================
# 状态单唯一写入方（PowerShell 兜底；与 scripts/state_commit.py 同口径）
# 【单源】路径/字段常量与 py 一致；渲染内联（对应 scripts/state_render.py，输出须逐字节一致，
#         这样 py 提交的项目可用 ps1 check，反之亦然）。
# 【路径】记忆系统/_state.json（权威）| 记忆系统/.state-commit.lock（乐观锁）
#        事务 JSON 只写 {项目}/.story/work/，成功后删除；失败保留。
# 用法：
#   pwsh scripts/state_commit.ps1 -Command init    -Project DIR [-InputFile FILE]
#   pwsh scripts/state_commit.ps1 -Command draft   -Project DIR -Chapter N
#   pwsh scripts/state_commit.ps1 -Command commit  -Project DIR -Chapter N -InputFile TXN.json
#   pwsh scripts/state_commit.ps1 -Command check   -Project DIR
#   pwsh scripts/state_commit.ps1 -Command migrate -Project DIR [-Apply]
# 退出码：0=成功/通过；1=校验失败、stale、派生不一致或环境错误。
# ============================================================
param(
    [Parameter(Mandatory = $true)][ValidateSet('init', 'draft', 'commit', 'check', 'migrate')][string]$Command,
    [Parameter(Mandatory = $true)][string]$Project,
    [int]$Chapter = 0,
    [string]$InputFile = '',
    [switch]$Apply
)

try { [System.Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$enc = New-Object System.Text.UTF8Encoding($false)
$nl = "`n"

$SchemaVersion = 1
$StateRel = '记忆系统/_state.json'
$DraftRel = '记忆系统/_state.migrate-draft.json'
$LockRel = '记忆系统/.state-commit.lock'
$LockStaleSeconds = 300
$WorkRel = '.story/work'
$ArchiveDirName = '_旧存档'
$RequiredKeys = @('schema_version', 'state_revision', 'book_title', 'last_committed_chapter',
    'imported_through_chapter', 'characters', 'foreshadow', 'timeline', 'facts', 'context', 'wordcounts')

function Write-Utf8File {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    # 原子写（P1-9）：临时文件 + 覆盖移动，失败不落半截
    $tmp = "$Path.tmp"
    [System.IO.File]::WriteAllText($tmp, $Text, $enc)
    [System.IO.File]::Move($tmp, $Path, $true)
}

function Get-RelPath {
    param([string]$Base, [string]$Full)
    return $Full.Substring($Base.Length).TrimStart('\', '/').Replace('\', '/')
}

function Sort-Ordinal {
    param($Items)
    $arr = @($Items | Where-Object { $null -ne $_ -and [string]$_ -ne '' })
    [Array]::Sort($arr, [System.StringComparer]::Ordinal)   # 与 Python sorted() 同为码点序
    return ,$arr
}

function Get-StatePath { param($Project) return (Join-Path $Project ($StateRel -replace '/', [string][char]92)) }

function Test-StateSchema {
    param($State)
    $errs = @()
    if ($null -eq $State) { return @('状态单不是 JSON 对象') }
    if ([int]$State.schema_version -ne $SchemaVersion) { $errs += ('schema_version 应为 {0}，实际 {1}' -f $SchemaVersion, $State.schema_version) }
    foreach ($k in $RequiredKeys) {
        if ($null -eq $State.PSObject.Properties[$k]) { $errs += ('缺字段：{0}' -f $k) }
    }
    if ($null -ne $State.state_revision -and -not ($State.state_revision -is [int] -or $State.state_revision -is [long])) {
        $errs += 'state_revision 必须是整数（乐观锁比较用）'
    }
    if ($null -eq $State.timeline -or $null -eq $State.timeline.PSObject.Properties['readers_known'] -or $null -eq $State.timeline.PSObject.Properties['authors_truth']) {
        $errs += 'timeline 必须含 readers_known / authors_truth 两键'
    }
    if ($null -eq $State.context) { $errs += 'context 必须是对象' }
    return ,$errs
}

function New-DefaultState {
    param([string]$BookTitle)
    $st = [ordered]@{
        schema_version           = $SchemaVersion
        state_revision           = 0
        book_title               = $BookTitle
        last_committed_chapter   = 0
        imported_through_chapter = 0
        overview                 = [ordered]@{ summary = ''; world_brief = ''; stages = @(); snapshot = '' }
        characters               = [ordered]@{}
        foreshadow               = [ordered]@{}
        timeline                 = [ordered]@{ readers_known = @(); authors_truth = @() }
        facts                    = @()
        summaries                = [ordered]@{}
        context                  = [ordered]@{
            position                 = [ordered]@{ chapter = 0; volume = ''; story_time = '' }
            long_term_constraints    = @()
            active_character_names   = @()
            recent_chapters          = @()
            next_chapter_commitments = @()
        }
        wordcounts               = [ordered]@{}
    }
    # 嵌套 [ordered] 需转成 PSCustomObject 树，否则 .PSObject.Properties 查不到内层键（校验/合并都会踩）
    return (([pscustomobject]$st | ConvertTo-Json -Depth 12) | ConvertFrom-Json)
}

function ConvertTo-StateJson {
    param($State)
    return ($State | ConvertTo-Json -Depth 12) + $nl
}

function Write-StateFile {
    param($Project, $State)
    $p = Get-StatePath $Project
    $tmp = $p + '.tmp'
    Write-Utf8File -Path $tmp -Text (ConvertTo-StateJson $State)
    Move-Item -LiteralPath $tmp -Destination $p -Force
}

function Set-NoteProperty {
    param($Obj, [string]$Name, $Value)
    if ($null -eq $Obj) { return }
    $Obj | Add-Member -MemberType NoteProperty -Name $Name -Value $Value -Force
}

function Get-LineText {
    param($Entry)
    if ($Entry -is [string]) { return $Entry }
    if ($null -ne $Entry) {
        $parts = @()
        foreach ($k in @('chapter', '章', 'event', '事件', 'subject', '主体', 'fact', '事实', 'value', '值', 'time', '时间')) {
            $prop = $Entry.PSObject.Properties[$k]
            if ($null -ne $prop -and $null -ne $prop.Value -and [string]$prop.Value -ne '') { $parts += [string]$prop.Value }
        }
        if ($parts.Count) { return ($parts -join ' | ') }
        return ($Entry | ConvertTo-Json -Compress -Depth 6)
    }
    return [string]$Entry
}

function Get-DerivedViews {
    param($State)
    $views = [ordered]@{}
    $rev = [int]$State.state_revision
    $mk = ('<!-- derived:state-render v1 | revision={0} -->' -f $rev) + $nl

    # 1) 故事圣经
    $ov = $State.overview
    if ($null -eq $ov) { $ov = [pscustomobject]@{ summary = ''; world_brief = ''; stages = @(); snapshot = '' } }
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($mk.TrimEnd([char]10)); $lines.Add(''); $lines.Add('# 故事圣经'); $lines.Add('')
    $lines.Add(('书名：{0}' -f $State.book_title)); $lines.Add('')
    $lines.Add('## 项目概况'); $lines.Add([string]$ov.summary); $lines.Add('')
    $lines.Add('## 世界观简版'); $lines.Add([string]$ov.world_brief); $lines.Add('')
    $lines.Add('## 阶段划分')
    $stages = @($ov.stages)
    if ($stages.Count -eq 0) { $lines.Add('- （未登记）') }
    else { foreach ($s in $stages) { $lines.Add(('- {0}' -f (Get-LineText $s))) } }
    $lines.Add(''); $lines.Add('## 状态快照'); $lines.Add([string]$ov.snapshot); $lines.Add('')
    $views['圣经/故事圣经.md'] = ($lines -join $nl)

    # 2) 人物弧线
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($mk.TrimEnd([char]10)); $lines.Add(''); $lines.Add('# 人物弧线'); $lines.Add('')
    $names = Sort-Ordinal @($State.characters.PSObject.Properties.Name)
    if ($names.Count -eq 0) { $lines.Add('（无登记角色）') }
    foreach ($n in $names) {
        $c = $State.characters.$n
        $lines.Add(('## {0}' -f $n))
        $aliases = @($c.aliases)
        if ($aliases.Count -gt 0 -and $null -ne $aliases[0]) { $lines.Add(('别名：{0}' -f ($aliases -join '、'))) }
        $lines.Add(('当前状态：{0}' -f [string]$c.snapshot))
        $facts = @($c.known_facts)
        if ($facts.Count -gt 0 -and $null -ne $facts[0]) {
            $lines.Add('已知事实：')
            foreach ($f in $facts) { $lines.Add(('- {0}' -f $f)) }
        }
        $lines.Add('')
    }
    $views['圣经/人物弧线.md'] = ($lines -join $nl)

    # 3) 伏笔清单
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($mk.TrimEnd([char]10)); $lines.Add(''); $lines.Add('# 伏笔清单'); $lines.Add('')
    $lines.Add('| 编号 | 线索 | 埋于 | 计划回收 | 已回收 | 优先级 |'); $lines.Add('|---|---|---|---|---|---|')
    $ids = Sort-Ordinal @($State.foreshadow.PSObject.Properties.Name)
    if ($ids.Count -eq 0) { $lines.Add('| — | （无） | | | | |') }
    foreach ($id in $ids) {
        $it = $State.foreshadow.$id
        $resolved = $it.resolved_ch
        $resTxt = '未回收'
        if ($null -ne $resolved -and [string]$resolved -ne '') { $resTxt = [string]$resolved }
        $lines.Add(('| {0} | {1} | {2} | {3} | {4} | {5} |' -f $id, [string]$it.desc, [string]$it.planted_ch, [string]$it.planned_resolve_ch, $resTxt, [string]$it.priority))
    }
    $lines.Add('')
    $views['圣经/伏笔清单.md'] = ($lines -join $nl)

    # 4/5) 时间线
    foreach ($item in @(@('圣经/剧情时间线.md', 'readers_known', '剧情时间线（读者已知）'),
                        @('圣经/作者真相时间线.md', 'authors_truth', '作者真相时间线（未揭示）'))) {
        $rel = $item[0]; $key = $item[1]; $title = $item[2]
        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add($mk.TrimEnd([char]10)); $lines.Add(''); $lines.Add(('# {0}' -f $title)); $lines.Add('')
        $entries = @($State.timeline.$key)
        if ($entries.Count -eq 0) { $lines.Add('（无条目）') }
        $i = 0
        foreach ($e in $entries) { $i++; $lines.Add(('{0}. {1}' -f $i, (Get-LineText $e))) }
        $lines.Add('')
        $views[$rel] = ($lines -join $nl)
    }

    # 6) 事实台账
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($mk.TrimEnd([char]10)); $lines.Add(''); $lines.Add('# 事实台账'); $lines.Add('')
    $lines.Add('| 章 | 主体 | 事实 | 值 |'); $lines.Add('|---|---|---|---|')
    $facts = @($State.facts)
    if ($facts.Count -eq 0) { $lines.Add('| — | （无） | | |') }
    foreach ($e in $facts) {
        if ($null -ne $e -and $e -isnot [string]) {
            $lines.Add(('| {0} | {1} | {2} | {3} |' -f [string]$e.chapter, [string]$e.subject, [string]$e.fact, [string]$e.value))
        }
        else { $lines.Add(('|  | {0} |  |  |' -f (Get-LineText $e))) }
    }
    $lines.Add('')
    $views['圣经/事实台账.md'] = ($lines -join $nl)

    # 7) 上下文（固定 7 栏）
    $ctx = $State.context
    $pos = $ctx.position
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($mk.TrimEnd([char]10)); $lines.Add(''); $lines.Add('# 上下文卡'); $lines.Add('')
    $lines.Add('## 当前位置')
    $lines.Add(('- 第 {0} 章 | 卷：{1} | 故事时间：{2}' -f [string]$pos.chapter, [string]$pos.volume, [string]$pos.story_time))
    $lines.Add('')
    foreach ($sec in @(@('## 长期约束', 'long_term_constraints'), @('## 核心角色状态', 'active_character_names'))) {
        $lines.Add($sec[0])
        $vals = @($ctx.($sec[1]))
        if ($vals.Count -eq 0 -or $null -eq $vals[0]) { $lines.Add('- （空）') }
        else { foreach ($v in $vals) { $lines.Add(('- {0}' -f $v)) } }
        $lines.Add('')
    }
    $lines.Add('## 活跃伏笔')
    $ids = Sort-Ordinal @($State.foreshadow.PSObject.Properties.Name)
    $any = $false
    foreach ($id in $ids) {
        $it = $State.foreshadow.$id
        if ($null -eq $it.resolved_ch -or [string]$it.resolved_ch -eq '') {
            $lines.Add(('- {0}：{1}' -f $id, [string]$it.desc)); $any = $true
        }
    }
    if (-not $any) { $lines.Add('- （无）') }
    $lines.Add('')
    $lines.Add('## 近三章速记')
    $vals = @($ctx.recent_chapters)
    if ($vals.Count -eq 0 -or $null -eq $vals[0]) { $lines.Add('- （空）') } else { foreach ($v in $vals) { $lines.Add(('- {0}' -f $v)) } }
    $lines.Add('')
    $lines.Add('## 下一章承诺')
    $vals = @($ctx.next_chapter_commitments)
    if ($vals.Count -eq 0 -or $null -eq $vals[0]) { $lines.Add('- （空）') } else { foreach ($v in $vals) { $lines.Add(('- {0}' -f $v)) } }
    $lines.Add('')
    $lines.Add('## 本章应推进')
    if ($vals.Count -gt 0 -and $null -ne $vals[0]) { $lines.Add(('- {0}' -f $vals[0])) } else { $lines.Add('- （空）') }
    $lines.Add('')
    $views['上下文.md'] = ($lines -join $nl)

    # 8) 摘要
    $sumProps = @($State.summaries.PSObject.Properties | Where-Object { $null -ne $_.Name -and $_.Name -ne '' })
    $sortedProps = @($sumProps | Sort-Object -Property @{ Expression = { [int]$_.Name } })
    foreach ($pr in $sortedProps) {
        $nnn = '{0:d3}' -f ([int]$pr.Name)     # 文件名按数值补零；内容仍用原键取（"002" 不能反推成 "2"）
        $views[('摘要/第{0}章摘要.md' -f $nnn)] = ('{0}{1}# 第{2}章摘要{1}{1}{3}{1}' -f $mk, $nl, $nnn, [string]$pr.Value)
    }
    return $views
}

function Write-DerivedViews {
    param($Project, $Views)
    $base = Join-Path $Project '记忆系统'
    $written = 0
    foreach ($k in $Views.Keys) {
        $p = Join-Path $base ($k -replace '/', [string][char]92)
        Write-Utf8File -Path $p -Text $Views[$k]
        $written++
    }
    return $written
}

function Compare-DerivedViews {
    param($Project, $Views)
    $base = Join-Path $Project '记忆系统'
    $bad = @()
    foreach ($k in $Views.Keys) {
        $p = Join-Path $base ($k -replace '/', [string][char]92)
        if (-not (Test-Path -LiteralPath $p)) { $bad += ('{0}（缺失）' -f $k); continue }
        $cur = [System.IO.File]::ReadAllText($p, $enc).Replace("`r`n", "`n")
        if ($cur -ne $Views[$k]) { $bad += $k }
    }
    return ,$bad
}

function Acquire-Lock {
    param($Project)
    $lock = Join-Path $Project ($LockRel -replace '/', [string][char]92)
    $dir = Split-Path -Parent $lock
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    foreach ($attempt in 1, 2) {
        try {
            $fs = [System.IO.File]::Open($lock, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
            $ts = [int][double]::Parse((Get-Date -UFormat %s))
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(('{{ "pid": {0}, "ts": {1} }}' -f $PID, $ts))
            $fs.Write($bytes, 0, $bytes.Length); $fs.Close()
            return @{ Lock = $lock; Ok = $true; Msg = 'ok' }
        }
        catch {
            $age = 0.0
            try { $age = ((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalSeconds } catch { $age = 0 }
            if ($age -gt $LockStaleSeconds -and $attempt -eq 1) {
                Write-Output ('⚠️ 发现 {0}s 以上的残留锁，接管（{1}）' -f $LockStaleSeconds, $lock)
                Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
                continue
            }
            return @{ Lock = $lock; Ok = $false; Msg = ('已有提交在进行中（锁 {0}，{1:N0}s 前创建）' -f $lock, $age) }
        }
    }
    return @{ Lock = $lock; Ok = $false; Msg = '锁获取失败' }
}

function Release-Lock {
    param($Lock)
    if ($null -ne $Lock -and (Test-Path -LiteralPath $Lock)) { Remove-Item -LiteralPath $Lock -Force -ErrorAction SilentlyContinue }
}

function Merge-Delta {
    param($State, $Delta)
    foreach ($p in $Delta.PSObject.Properties) {
        $key = $p.Name; $val = $p.Value
        if ($key -eq 'facts') {
            $cur = @($State.facts) + @($val)
            $State.facts = @($cur)
        }
        elseif ($key -eq 'timeline' -and $null -ne $val) {
            foreach ($sub in $val.PSObject.Properties) {
                $cur = @($State.timeline.($sub.Name)) + @($sub.Value)
                Set-NoteProperty -Obj $State.timeline -Name ([string]$sub.Name) -Value @($cur)
            }
        }
        elseif ($key -eq 'summaries' -and $null -ne $val) {
            foreach ($s in $val.PSObject.Properties) {
                Set-NoteProperty -Obj $State.summaries -Name ([string]$s.Name) -Value $s.Value
            }
        }
        elseif ($key -eq 'wordcounts' -and $null -ne $val) {
            foreach ($s in $val.PSObject.Properties) {
                Set-NoteProperty -Obj $State.wordcounts -Name ([string]$s.Name) -Value $s.Value
            }
        }
        elseif ($key -eq 'overview' -and $null -ne $val -and $null -ne $State.overview) {
            foreach ($s in $val.PSObject.Properties) {
                Set-NoteProperty -Obj $State.overview -Name ([string]$s.Name) -Value $s.Value
            }
        }
        elseif ($key -eq 'characters' -and $null -ne $val) {
            foreach ($c in $val.PSObject.Properties) {
                $existing = $State.characters.($c.Name)
                if ($null -eq $existing) { Set-NoteProperty -Obj $State.characters -Name ([string]$c.Name) -Value $c.Value }
                else { foreach ($f in $c.Value.PSObject.Properties) { Set-NoteProperty -Obj $existing -Name ([string]$f.Name) -Value $f.Value } }
            }
        }
        elseif ($key -eq 'foreshadow' -and $null -ne $val) {
            foreach ($f in $val.PSObject.Properties) {
                $existing = $State.foreshadow.($f.Name)
                if ($null -eq $existing) { Set-NoteProperty -Obj $State.foreshadow -Name ([string]$f.Name) -Value $f.Value }
                else { foreach ($g in $f.Value.PSObject.Properties) { Set-NoteProperty -Obj $existing -Name ([string]$g.Name) -Value $g.Value } }
            }
        }
        elseif ($key -eq 'context' -and $null -ne $val) {
            foreach ($g in $val.PSObject.Properties) {
                Set-NoteProperty -Obj $State.context -Name ([string]$g.Name) -Value $g.Value
            }
        }
        else {
            Set-NoteProperty -Obj $State -Name ([string]$key) -Value $val
        }
    }
    return $State
}

# ---------------- 命令实现 ----------------
$ProjectPath = $Project
if (-not [System.IO.Path]::IsPathRooted($ProjectPath)) { $ProjectPath = Join-Path (Get-Location).Path $Project }
if (-not (Test-Path -LiteralPath $ProjectPath)) { Write-Output ('❌ 项目目录不存在：{0}' -f $ProjectPath); exit 1 }

$sp = Get-StatePath $ProjectPath

switch ($Command) {

    'init' {
        if (Test-Path -LiteralPath $sp) { Write-Output ('❌ 已存在 {0}（如需重来请先手工移除，禁止静默覆盖）' -f $StateRel); exit 1 }
        $payload = [pscustomobject]@{}
        if ($InputFile -ne '') {
            if (-not (Test-Path -LiteralPath $InputFile)) { Write-Output ('❌ 输入不存在：{0}' -f $InputFile); exit 1 }
            $payload = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        $title = $ProjectPath | Split-Path -Leaf
        if ($null -ne $payload.PSObject.Properties['book_title'] -and [string]$payload.book_title -ne '') { $title = [string]$payload.book_title }
        $st = New-DefaultState -BookTitle $title
        foreach ($k in @('overview', 'context')) {
            $prop = $payload.PSObject.Properties[$k]
            if ($null -ne $prop -and $null -ne $prop.Value) {
                foreach ($f in $prop.Value.PSObject.Properties) { Set-NoteProperty -Obj $st.$k -Name ([string]$f.Name) -Value $f.Value }
            }
        }
        foreach ($k in @('characters', 'foreshadow', 'wordcounts', 'summaries')) {
            $prop = $payload.PSObject.Properties[$k]
            if ($null -ne $prop -and $null -ne $prop.Value) {
                foreach ($f in $prop.Value.PSObject.Properties) { Set-NoteProperty -Obj $st.$k -Name ([string]$f.Name) -Value $f.Value }
            }
        }
        $prop = $payload.PSObject.Properties['facts']
        if ($null -ne $prop -and $null -ne $prop.Value) { $st.facts = @($prop.Value) }
        $errs = Test-StateSchema $st
        if ($errs.Count) { Write-Output ('❌ init 输入校验失败：{0}' -f ($errs -join '；')); exit 1 }
        Write-StateFile -Project $ProjectPath -State $st
        $work = Join-Path $ProjectPath ($WorkRel -replace '/', [string][char]92)
        if (-not (Test-Path -LiteralPath $work)) { New-Item -ItemType Directory -Path $work -Force | Out-Null }
        $n = Write-DerivedViews -Project $ProjectPath -Views (Get-DerivedViews $st)
        Write-Output ('✅ init 完成：{0}（revision=0）+ 派生 {1} 个文件' -f $StateRel, $n)
        exit 0
    }

    'draft' {
        if (-not (Test-Path -LiteralPath $sp)) { Write-Output ('❌ 缺少 {0}（先 init 或 migrate --apply）' -f $StateRel); exit 1 }
        if ($Chapter -lt 1) { Write-Output '❌ --chapter 必须 ≥1'; exit 1 }
        $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json
        $txnDir = Join-Path $ProjectPath (('{0}/第{1:d3}章/state.json' -f $WorkRel, $Chapter) -replace '/', [string][char]92)
        $txn = [ordered]@{
            chapter                   = $Chapter
            expected_state_revision   = [int]$st.state_revision
            mode                      = 'commit'
            _hint                     = '只填 delta：变化角色快照 / 新增伏笔 / 本章事实 / 时间线条目 / 摘要 / 字数；不要重写整份 state'
            delta                     = [ordered]@{
                characters = [ordered]@{}; foreshadow = [ordered]@{}; facts = @()
                timeline   = [ordered]@{ readers_known = @(); authors_truth = @() }
                summaries  = [ordered]@{}; wordcounts = [ordered]@{}; context = [ordered]@{}
            }
        }
        Write-Utf8File -Path $txnDir -Text (([pscustomobject]$txn | ConvertTo-Json -Depth 8) + $nl)
        Write-Output ('✅ 草稿已生成：{0}（expected_state_revision={1}）' -f (Get-RelPath $ProjectPath $txnDir), [int]$st.state_revision)
        exit 0
    }

    'commit' {
        if (-not (Test-Path -LiteralPath $sp)) { Write-Output ('❌ 缺少 {0}（先 init 或 migrate --apply）' -f $StateRel); exit 1 }
        if ($Chapter -lt 1) { Write-Output '❌ --chapter 必须 ≥1'; exit 1 }
        if ($InputFile -eq '' -or -not (Test-Path -LiteralPath $InputFile)) { Write-Output '❌ 事务文件缺失（-InputFile）；用 draft 生成后填写再提交'; exit 1 }
        try { $txn = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Write-Output ('❌ 事务 JSON 解析失败（改事务重跑）：{0}' -f $_.Exception.Message); exit 1 }
        if ([int]$txn.chapter -ne $Chapter) { Write-Output ('❌ 事务 chapter={0} 与 -Chapter {1} 不符（改事务重跑）' -f $txn.chapter, $Chapter); exit 1 }
        if ($null -eq $txn.PSObject.Properties['expected_state_revision']) { Write-Output '❌ 事务缺 expected_state_revision（整数；改事务重跑）'; exit 1 }
        if ($null -eq $txn.PSObject.Properties['delta']) { Write-Output '❌ 事务缺 delta 对象（改事务重跑）'; exit 1 }
        $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json
        $errs = Test-StateSchema $st
        if ($errs.Count) { Write-Output ('❌ 现有状态单校验失败（先修复再提交）：{0}' -f ($errs -join '；')); exit 1 }
        if ([int]$txn.expected_state_revision -ne [int]$st.state_revision) {
            Write-Output ('❌ stale transaction：expected_state_revision={0}，当前 state_revision={1}（重跑 draft 取新 revision 再提交）' -f [int]$txn.expected_state_revision, [int]$st.state_revision)
            exit 1
        }
        $lk = Acquire-Lock -Project $ProjectPath
        if (-not $lk.Ok) { Write-Output ('❌ 写入失败：{0}（修正环境后重跑同一份 commit，事务文件已保留）' -f $lk.Msg); exit 1 }
        try {
            $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([int]$txn.expected_state_revision -ne [int]$st.state_revision) {
                Write-Output ('❌ stale transaction（锁内复核）：expected={0} actual={1}' -f [int]$txn.expected_state_revision, [int]$st.state_revision)
                exit 1
            }
            $st = Merge-Delta -State $st -Delta $txn.delta
            $st.state_revision = [int]$st.state_revision + 1
            $newLast = [Math]::Max([int]$st.last_committed_chapter, $Chapter)
            $st.last_committed_chapter = $newLast
            Write-StateFile -Project $ProjectPath -State $st
            $views = Get-DerivedViews $st
            $n = Write-DerivedViews -Project $ProjectPath -Views $views
            $doneDir = Join-Path $ProjectPath '.done'
            if (-not (Test-Path -LiteralPath $doneDir)) { New-Item -ItemType Directory -Path $doneDir -Force | Out-Null }
            $donePath = Join-Path $doneDir ('第{0:d3}章_state.done' -f $Chapter)
            [System.IO.File]::WriteAllBytes($donePath, [byte[]]@())
        }
        catch {
            Write-Output ('❌ 写入失败：{0}（修正环境后重跑同一份 commit，事务文件已保留）' -f $_.Exception.Message)
            Release-Lock $lk.Lock
            exit 1
        }
        Release-Lock $lk.Lock
        Remove-Item -LiteralPath $InputFile -Force -ErrorAction SilentlyContinue
        Write-Output ('✅ state.commit ok：chapter={0} revision→{1} last_committed_chapter={2}' -f $Chapter, [int]$st.state_revision, [int]$st.last_committed_chapter)
        Write-Output ('✅ 派生已刷新（{0} 个文件）；标记 {1}' -f $n, (Get-RelPath $ProjectPath $donePath))
        Write-Output '提示：.done/第NNN章_state.done 为闭环必需标记（check-done 在存在 _state.json 时自动要求）'
        exit 0
    }

    'check' {
        if (-not (Test-Path -LiteralPath $sp)) { Write-Output ('❌ 缺少 {0}（存量项目走旧路径即可，无需本命令）' -f $StateRel); exit 1 }
        try { $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Write-Output ('❌ 状态单 JSON 损坏：{0}' -f $_.Exception.Message); exit 1 }
        $errs = Test-StateSchema $st
        $views = Get-DerivedViews $st
        $bad = Compare-DerivedViews -Project $ProjectPath -Views $views
        $lockPath = Join-Path $ProjectPath ($LockRel -replace '/', [string][char]92)
        $lockNote = ''
        if (Test-Path -LiteralPath $lockPath) {
            $age = ((Get-Date) - (Get-Item -LiteralPath $lockPath).LastWriteTime).TotalSeconds
            $extra = ''
            if ($age -gt $LockStaleSeconds) { $extra = '（超过 300s 下次提交会接管）' } else { $extra = '（<300s，可能有提交在进行）' }
            $lockNote = ('；⚠️ 残留锁 {0:N0}s{1}' -f $age, $extra)
        }
        Write-Output ('【state-commit check】{0}' -f $ProjectPath)
        Write-Output ('revision={0} last_committed_chapter={1} 派生文件={2}{3}' -f [int]$st.state_revision, [int]$st.last_committed_chapter, $views.Keys.Count, $lockNote)
        if ($errs.Count) {
            Write-Output ('❌ 状态单校验失败（{0} 项）：' -f $errs.Count)
            foreach ($e in $errs) { Write-Output ('  - {0}' -f $e) }
            exit 1
        }
        if ($bad.Count) {
            Write-Output ('❌ derived view differs from _state.json（{0} 个文件）：' -f $bad.Count)
            foreach ($b in $bad) { Write-Output ('  - 记忆系统/{0}' -f $b) }
            Write-Output '修复：不要手改派生文件；改 state 后重跑 commit（或 state_render 重新渲染）'
            exit 1
        }
        Write-Output '✅ 派生视图与状态单逐字一致（check 二次运行仍应 PASS）'
# OF-6 摘要陈旧检测（警告不阻断：范围 1..last_committed，缺单章摘要即 stale）
$lastCh = 0
if ($null -ne $st.PSObject.Properties['last_committed_chapter'] -and $st.last_committed_chapter) { $lastCh = [int]$st.last_committed_chapter }
$summKeys = @{}
if ($null -ne $st.PSObject.Properties['summaries'] -and $null -ne $st.summaries) {
    foreach ($sp in $st.summaries.PSObject.Properties) { $summKeys[[string]$sp.Name] = $true }
}
$staleChs = @()
for ($c = 1; $c -le $lastCh; $c++) { $kk = '{0:D3}' -f $c; if (-not $summKeys.ContainsKey($kk)) { $staleChs += $c } }
if ($staleChs.Count -gt 0) {
    $shownParts = @()
    foreach ($c in ($staleChs | Select-Object -First 10)) { $shownParts += ('第{0:D3}章' -f $c) }
    $shown = $shownParts -join '、'
    if ($staleChs.Count -gt 10) { $shown += ('…（共 {0} 章）' -f $staleChs.Count) }
    Write-Output ('⚠️ 摘要陈旧（stale）：{0} 缺单章摘要 → 上下文可能不完整（先补摘要再续写）' -f $shown)
}
else {
    Write-Output '✅ 摘要连续（stale=0）'
}
        exit 00
    }

    'migrate' {
        $mem = Join-Path $ProjectPath '记忆系统'
        if (-not (Test-Path -LiteralPath $mem)) { Write-Output ('❌ 记忆系统 不存在：{0}' -f $mem); exit 1 }
        $spExist = Test-Path -LiteralPath $sp
        if ($spExist -and -not $Apply) { Write-Output ('✅ 已存在 {0}，无需重复 migrate' -f $StateRel); exit 0 }
        $draftPath = Join-Path $ProjectPath ($DraftRel -replace '/', [string][char]92)
        $olds = @()
        foreach ($sub in @('圣经', '摘要')) {
            $d = Join-Path $mem $sub
            if (Test-Path -LiteralPath $d) { $olds += Sort-Ordinal @(Get-ChildItem -LiteralPath $d -Filter *.md -File | ForEach-Object { $_.FullName }) }
        }
        $ctxFile = Join-Path $mem '上下文.md'
        if (Test-Path -LiteralPath $ctxFile) { $olds += $ctxFile }

        if (-not $Apply) {
            $title = Split-Path -Leaf $ProjectPath
            $bible = Join-Path $mem '圣经/故事圣经.md'
            if (Test-Path -LiteralPath $bible) {
                $first = @(([System.IO.File]::ReadAllText($bible, $enc) -replace "`r`n", "`n") -split "`n" | Where-Object { $_.Trim() -ne '' })
                if ($first.Count -gt 0 -and $first[0].StartsWith('#')) { $title = $first[0].TrimStart('#').Trim(); if ($title -eq '') { $title = Split-Path -Leaf $ProjectPath } }
            }
            $chapters = @()
            $sumDir = Join-Path $mem '摘要'
            if (Test-Path -LiteralPath $sumDir) {
                foreach ($f in Get-ChildItem -LiteralPath $sumDir -Filter '第*章摘要.md' -File) {
                    if ($f.Name -match '^第(\d+)章摘要\.md$') { $chapters += [int]$Matches[1] }
                }
            }
            $st = New-DefaultState -BookTitle $title
            $imported = 0
            if ($chapters.Count) { $imported = ($chapters | Measure-Object -Maximum).Maximum }
            $st.imported_through_chapter = $imported
            $st.overview.snapshot = ('（迁移草案：由 {0} 个旧文件生成，关键内容需人工补进本 state）' -f $olds.Count)
            Write-Utf8File -Path $draftPath -Text (ConvertTo-StateJson $st)
            Write-Output ('✅ 迁移草案已生成：{0}（**尚未生效**）' -f $DraftRel)
            Write-Output '   - 待人工补全字段：characters / foreshadow / facts / timeline / context（草案只带书名与章数）'
            Write-Output ('   - 待归档旧文件：{0} 个（阶段/卷/约束不动）' -f $olds.Count)
            foreach ($o in $olds) { Write-Output ('     · {0}' -f (Get-RelPath $ProjectPath $o)) }
            Write-Output ('   - 确认无误后执行：migrate -Project "{0}" -Apply（归档旧文件 + 写入权威 state + 渲染派生）' -f $ProjectPath)
            exit 0
        }

        if (-not (Test-Path -LiteralPath $draftPath)) { Write-Output ('❌ 缺草案 {0}（先跑一次不带 -Apply 的 migrate 生成草案并人工确认）' -f $DraftRel); exit 1 }
        $st = Get-Content -LiteralPath $draftPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $errs = Test-StateSchema $st
        if ($errs.Count) { Write-Output ('❌ 草案校验失败：{0}' -f ($errs -join '；')); exit 1 }
        $moved = 0
        foreach ($o in $olds) {
            $rel = Get-RelPath $mem $o
            $target = Join-Path (Join-Path $mem $ArchiveDirName) ($rel -replace '/', [string][char]92)
            $tDir = Split-Path -Parent $target
            if (-not (Test-Path -LiteralPath $tDir)) { New-Item -ItemType Directory -Path $tDir -Force | Out-Null }
            if (Test-Path -LiteralPath $target) { Write-Output ('❌ 归档目标已存在（先人工处理）：{0}' -f $target); exit 1 }
            Move-Item -LiteralPath $o -Destination $target
            $moved++
        }
        Write-StateFile -Project $ProjectPath -State $st
        $views = Get-DerivedViews $st
        $n = Write-DerivedViews -Project $ProjectPath -Views $views
        Remove-Item -LiteralPath $draftPath -Force -ErrorAction SilentlyContinue
        Write-Output ('✅ migrate -Apply 完成：{0}（revision=0，last_committed_chapter={1}）' -f $StateRel, [int]$st.last_committed_chapter)
        Write-Output ('   归档旧文件 {0} 个 → {1}/（只移动、未删除）' -f $moved, $ArchiveDirName)
        Write-Output ('   重渲染派生 {0} 个文件' -f $n)
        Write-Output '⚠️ 新派生内容目前只含草案字段：请把关键信息补进 _state.json 后重跑 commit 或 state_render，并用 check 验证'
        exit 0
    }
}
