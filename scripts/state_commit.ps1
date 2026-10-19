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
    [string]$Chapter = '0',
    [string]$InputFile = '',
    [switch]$Apply
)

try { [System.Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
# fail-closed：写盘/渲染失败必须中止并 rc1，禁止失败仍打 ✅ 并落 .done（门禁语义）
# 参数错误收敛：纯 ASCII 十进制（[long] 会把 "1e3"/"+5"/"0x10"/" 5"/"" 静默转换，与 py _i64 反向裁决）
if ($Chapter -notmatch '^-?[0-9]+$') { [Console]::Out.WriteLine('❌ 参数错误：章节号须为纯 ASCII 十进制整数（对齐 py _i64）'); exit 1 }
$ChapterRaw = $Chapter  # param([string]) 类型约束会把赋值回炉成 String，先存原值再解约束重赋
Remove-Variable -Name Chapter -Force -ErrorAction SilentlyContinue
$Chapter = [long]$ChapterRaw

$ErrorActionPreference = 'Stop'
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
    # 原子写（P1-9）：临时文件 + 覆盖移动，失败不落半截。
    # 三参 File.Move / File.Replace(null backup) 在 Windows PowerShell 5.1(.NET Framework) 不可用 → Move-Item -Force（5.1/7 通用，失败保留原文件）。
    $tmp = "$Path.tmp"
    [System.IO.File]::WriteAllText($tmp, $Text, $enc)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
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
    if ($State -isnot [pscustomobject] -or $State.GetType().FullName -ne 'System.Management.Automation.PSCustomObject') { return @('状态单不是 JSON 对象') }  # 数组/标量判型（PS5.1 数组 -is pscustomobject 恒 True，须按类型全名硬判，对齐 py isinstance(dict)）
    if ($State.schema_version -isnot [int] -and $State.schema_version -isnot [long]) { $errs += ('schema_version 应为 {0}，实际 {1}' -f $SchemaVersion, $(if ($null -eq $State.schema_version) { 'None' } elseif ($State.schema_version -is [double] -or $State.schema_version -is [decimal]) { ([double]$State.schema_version).ToString('0.##########', [System.Globalization.CultureInfo]::InvariantCulture) } else { $State.schema_version })) }
    elseif ([long]$State.schema_version -ne [long]$SchemaVersion) { $errs += ('schema_version 应为 {0}，实际 {1}' -f $SchemaVersion, $State.schema_version) }
    foreach ($k in $RequiredKeys) {
        if ($null -eq $State.PSObject.Properties[$k]) { $errs += ('缺字段：{0}' -f $k) }
    }
    # 三标量：键存在即须为 [int]/[long]（显式 null 与超 Int64（BigInteger/Double）fail-closed，与 py 同判）
    foreach ($tri in @(@('state_revision', 'state_revision 必须是整数（乐观锁比较用）'),
                       @('imported_through_chapter', 'imported_through_chapter 必须是整数'),
                       @('last_committed_chapter', 'last_committed_chapter 必须是整数'))) {
        if ($null -ne $State.PSObject.Properties[$tri[0]] -and -not ($State.($tri[0]) -is [int] -or $State.($tri[0]) -is [long])) {
            $errs += $tri[1]
        }
    }
    if ($null -ne $State.summaries -and $State.summaries -isnot [pscustomobject]) {
        $errs += 'summaries 须为对象'
    }
    elseif ($null -ne $State.summaries) {
        foreach ($sk in $State.summaries.PSObject.Properties) { if ([string]$sk.Name -notmatch '\A[0-9]{1,18}\z') { $errs += 'summaries 键必须是十进制数字字符串（≤18 位）'; break } }
    }
    if ($null -eq $State.timeline -or $null -eq $State.timeline.PSObject.Properties['readers_known'] -or $null -eq $State.timeline.PSObject.Properties['authors_truth']) {
        $errs += 'timeline 必须含 readers_known / authors_truth 两键'
    }
    if ($null -ne $State.PSObject.Properties['context'] -and ($null -eq $State.context -or $State.context -isnot [pscustomobject])) { $errs += 'context 必须是对象' }
    if ($null -ne $State.context -and $State.context -is [pscustomobject] -and $null -ne $State.context.PSObject.Properties['position'] -and $null -ne $State.context.position -and $State.context.position -isnot [pscustomobject]) { $errs += 'context.position 须为对象' }
    if ($null -ne $State.overview -and $State.overview -isnot [pscustomobject]) { $errs += 'overview 须为对象' }
    if ($null -ne $State.overview -and $State.overview -is [pscustomobject] -and $null -ne $State.overview.PSObject.Properties['stages'] -and $null -ne $State.overview.stages -and $State.overview.stages -isnot [array]) { $errs += 'overview.stages 须为数组' }
    if ($null -ne $State.wordcounts -and $State.wordcounts -isnot [pscustomobject]) { $errs += 'wordcounts 须为对象' }
    if ($null -ne $State.facts -and $State.facts -isnot [array]) { $errs += 'facts 须为数组' }
    elseif ($null -ne $State.facts -and $State.facts -is [array]) {
        for ($fi2 = 0; $fi2 -lt $State.facts.Count; $fi2++) {
            $fe2 = $State.facts[$fi2]
            if ($fe2 -isnot [pscustomobject]) { $errs += ('facts[{0}] 须为对象' -f $fi2); break }
        }
    }
    if ($null -ne $State.overview -and $State.overview -is [pscustomobject] -and $State.overview.stages -is [array]) {
        foreach ($se in $State.overview.stages) { if ($se -isnot [string] -and $se -isnot [pscustomobject]) { $errs += 'overview.stages 元素须为字符串或对象'; break } }
    }
    if ($null -ne $State.timeline -and $State.timeline -is [pscustomobject]) {
        foreach ($tk in @('readers_known','authors_truth')) {
            $pvt = $State.timeline.PSObject.Properties[$tk]
            if ($null -ne $pvt -and $null -ne $pvt.Value -and $pvt.Value -isnot [array]) { $errs += ('timeline.{0} 须为数组' -f $tk) }
            elseif ($null -ne $pvt -and $pvt.Value -is [array]) {
                foreach ($te in $pvt.Value) { if ($te -isnot [string] -and $te -isnot [pscustomobject]) { $errs += ('timeline.{0} 元素须为字符串或对象' -f $tk); break } }
            }
        }
    }
    if ($null -ne $State.foreshadow -and $State.foreshadow -isnot [pscustomobject]) { $errs += 'foreshadow 须为对象' }
    elseif ($null -ne $State.foreshadow -and $State.foreshadow -is [pscustomobject]) {
        foreach ($fsv2 in $State.foreshadow.PSObject.Properties) {
            if ([string]$fsv2.Name -eq '') { $errs += 'foreshadow 键名不能为空（空键 pwsh7 ConvertFrom-Json 拒读）'; break }
            if ($fsv2.Value -isnot [pscustomobject]) { $errs += ('foreshadow.{0} 须为对象' -f $fsv2.Name); break }
            foreach ($ffk in @('planted_ch', 'planned_resolve_ch', 'resolved_ch')) {
                $ffp = $fsv2.Value.PSObject.Properties[$ffk]
                if ($null -ne $ffp -and $null -ne $ffp.Value -and (-not ($ffp.Value -is [int] -or $ffp.Value -is [long]) -or [long]$ffp.Value -lt 0)) {
                    $errs += ('foreshadow.{0}.{1} 必须是非负整数' -f $fsv2.Name, $ffk); break
                }
            }
        }
    }
    if ($null -ne $State.overview -and $State.overview -is [pscustomobject] -and $null -ne $State.overview.PSObject.Properties['summary'] -and $null -ne $State.overview.summary -and $State.overview.summary -isnot [string]) { $errs += 'overview.summary 须为字符串' }
    if ($null -ne $State.context -and $State.context -is [pscustomobject]) {
        foreach ($ck in @('recent_chapters','next_chapter_commitments','characters_present','mystery_knowledge','goals','long_term_constraints')) {
            $pvc = $State.context.PSObject.Properties[$ck]
            if ($null -ne $pvc -and $null -ne $pvc.Value -and $pvc.Value -isnot [array]) { $errs += ('context.{0} 须为数组' -f $ck) }
        }
    }
    if ($null -ne $State.characters -and $State.characters -is [pscustomobject]) {
        foreach ($ce in $State.characters.PSObject.Properties) {
            if ([string]$ce.Name -eq '') { $errs += 'characters 键名不能为空（空键 pwsh7 ConvertFrom-Json 拒读）'; break }
            if ($ce.Value -is [pscustomobject]) {
                foreach ($ak in @('aliases','known_facts')) {
                    $pva = $ce.Value.PSObject.Properties[$ak]
                    if ($null -ne $pva -and $null -ne $pva.Value -and $pva.Value -isnot [array]) { $errs += ('characters.{0}.{1} 须为数组' -f $ce.Name, $ak) }
                    elseif ($null -ne $pva -and $pva.Value -is [array]) {
                        foreach ($ae in $pva.Value) { if ($ae -isnot [string]) { $errs += ('characters.{0}.{1} 元素须为字符串' -f $ce.Name, $ak); break } }
                    }
                }
            }
        }
    }
    if ($null -ne $State.facts -and $State.facts -is [array]) {
        for ($fi = 0; $fi -lt $State.facts.Count; $fi++) {
            $fe = $State.facts[$fi]
            if ($fe -is [pscustomobject] -and $null -ne $fe.PSObject.Properties['chapter'] -and $null -ne $fe.chapter -and ($fe.chapter -isnot [int] -and $fe.chapter -isnot [long])) { $errs += ('facts[{0}].chapter 须为整数' -f $fi) }
        }
    }
    if ($null -ne $State.characters -and $State.characters -isnot [pscustomobject]) { $errs += 'characters 须为对象' }
    elseif ($null -ne $State.characters) {
        foreach ($cv in $State.characters.PSObject.Properties) { if ($null -ne $cv.Value -and $cv.Value -isnot [pscustomobject]) { $errs += ('characters.{0} 须为对象' -f $cv.Name); break } }
    }
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
    return (([pscustomobject]$st | ConvertTo-Json -Depth 100) | ConvertFrom-Json)
}

function ConvertTo-StateJson {
    param($State)
    return ($State | ConvertTo-Json -Depth 100) + $nl
}

function Write-StateFile {
    param($Project, $State)
    $p = Get-StatePath $Project
    $tmp = $p + '.tmp'
    Write-Utf8File -Path $tmp -Text (ConvertTo-StateJson $State)
    Move-Item -LiteralPath $tmp -Destination $p -Force
}

function Merge-InitDefault {
    # 递归合并（对齐 py _deep_merge）：嵌套对象保留默认键，非对象值整体替换
    param($Dst, $Src)
    foreach ($sp in $Src.PSObject.Properties) {
        $dp = $Dst.PSObject.Properties[[string]$sp.Name]
        if ($null -ne $sp.Value -and $sp.Value -is [pscustomobject] -and $null -ne $dp -and $dp.Value -is [pscustomobject]) {
            Merge-InitDefault -Dst $dp.Value -Src $sp.Value
        } else {
            Set-NoteProperty -Obj $Dst -Name ([string]$sp.Name) -Value $sp.Value
        }
    }
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
        return ($Entry | ConvertTo-Json -Compress -Depth 100)
    }
    return [string]$Entry
}

function Get-DerivedViews {
    param($State)
    $views = [ordered]@{}
    $rev = [long]$State.state_revision
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
    $sortedProps = @($sumProps | Sort-Object -Property @{ Expression = { [long]$_.Name } })
    foreach ($pr in $sortedProps) {
        $nnn = '{0:d3}' -f ([long]$pr.Name)     # 文件名按数值补零；内容仍用原键取（"002" 不能反推成 "2"）
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
            return @{ Lock = $lock; Ok = $false; Msg = ('已有提交在进行中（锁 {0}，{1}s 前创建）' -f $lock, [int]$age) }
        }
    }
    return @{ Lock = $lock; Ok = $false; Msg = '锁获取失败' }
}

function Release-Lock {
    param($Lock)
    if ($null -ne $Lock -and (Test-Path -LiteralPath $Lock)) { Remove-Item -LiteralPath $Lock -Force -ErrorAction SilentlyContinue }
}

function Merge-DeepObject {
    # 与 py _deep_merge 同语义：两侧均为对象时递归深合并（任意深度保留旧键），否则整体替换
    param($Target, $DeltaObj)
    foreach ($p in $DeltaObj.PSObject.Properties) {
        $cur = $Target.PSObject.Properties[$p.Name]
        if ($null -eq $cur) { Set-NoteProperty -Obj $Target -Name ([string]$p.Name) -Value $p.Value }
        elseif ($null -ne $p.Value -and $p.Value -is [pscustomobject] -and $null -ne $cur.Value -and $cur.Value -is [pscustomobject]) { Merge-DeepObject -Target $cur.Value -DeltaObj $p.Value }
        else { Set-NoteProperty -Obj $Target -Name ([string]$p.Name) -Value $p.Value }
    }
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
                if ($null -ne $s.Value -and $s.Value -is [pscustomobject] -and $null -ne $State.overview.PSObject.Properties[$s.Name] -and $State.overview.($s.Name) -is [pscustomobject]) { Merge-DeepObject -Target $State.overview.($s.Name) -DeltaObj $s.Value }
                else { Set-NoteProperty -Obj $State.overview -Name ([string]$s.Name) -Value $s.Value }
            }
        }
        elseif ($key -eq 'characters' -and $null -ne $val) {
            foreach ($c in $val.PSObject.Properties) {
                $existing = $State.characters.($c.Name)
                if ($null -eq $existing) { Set-NoteProperty -Obj $State.characters -Name ([string]$c.Name) -Value $c.Value }
                else { Merge-DeepObject -Target $existing -DeltaObj $c.Value }
            }
        }
        elseif ($key -eq 'foreshadow' -and $null -ne $val) {
            foreach ($f in $val.PSObject.Properties) {
                $existing = $State.foreshadow.($f.Name)
                if ($null -eq $existing) { Set-NoteProperty -Obj $State.foreshadow -Name ([string]$f.Name) -Value $f.Value }
                else { Merge-DeepObject -Target $existing -DeltaObj $f.Value }
            }
        }
        elseif ($key -eq 'context' -and $null -ne $val) {
            foreach ($g in $val.PSObject.Properties) {
                if ($null -ne $g.Value -and $g.Value -is [pscustomobject] -and $null -ne $State.context.PSObject.Properties[$g.Name] -and $State.context.($g.Name) -is [pscustomobject]) { Merge-DeepObject -Target $State.context.($g.Name) -DeltaObj $g.Value }
                else { Set-NoteProperty -Obj $State.context -Name ([string]$g.Name) -Value $g.Value }
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
if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) { Write-Output ('❌ 项目目录不存在：{0}' -f $ProjectPath); exit 1 }  # Container 判定（对齐 py is_dir / check-done.ps1:65）

$sp = Get-StatePath $ProjectPath

switch ($Command) {

    'init' {
        if (Test-Path -LiteralPath $sp) { Write-Output ('❌ 已存在 {0}（如需重来请先手工移除，禁止静默覆盖）' -f $StateRel); exit 1 }
        $chapFiles = @(Get-ChildItem -LiteralPath (Join-Path $ProjectPath '记忆系统/摘要') -Filter '第*章摘要.md' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match '^第([0-9]+)章摘要$' } | ForEach-Object { [long]([regex]::Match($_.BaseName, '^第([0-9]+)章摘要$').Groups[1].Value) } | Sort-Object)
        if ($chapFiles.Count -gt 0) { Write-Output ('❌ 检测到存量章节（摘要至第{0}章）——init 会以空渲染覆盖手写圣经；存量项目升级请走 migrate（草案 + --apply）' -f $chapFiles[-1]); exit 1 }
        $biblePath = Join-Path $ProjectPath '记忆系统/圣经/故事圣经.md'
        if (Test-Path -LiteralPath $biblePath) {
            $bibleHead = [IO.File]::ReadAllText($biblePath)
            if ($bibleHead.Length -gt 200) { $bibleHead = $bibleHead.Substring(0, 200) }
            if ($bibleHead -notmatch 'derived:state-render') { Write-Output '⚠️ 检测到非派生圣经文件：init 将以空渲染覆盖其内容（存量/已填充项目请改用 migrate）' }
        }
        $payload = [pscustomobject]@{}
        if ($InputFile -ne '') {
            if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) { Write-Output ('❌ 输入不存在：{0}' -f $InputFile); exit 1 }  # Leaf 判定（目录亦判不存在，对齐 py is_file）
            try { $payload = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8 | ConvertFrom-Json }
            catch { Write-Output ('❌ 状态单 JSON 损坏：{0}（init 输入 JSON 空键名；pwsh7 ConvertFrom-Json 拒读）' -f $_.Exception.Message); exit 1 }  # 文案头对齐 py load_json；详情归引擎 stderr 不比对
            if ($null -eq $payload -or $payload -isnot [pscustomobject] -or $payload.GetType().FullName -ne 'System.Management.Automation.PSCustomObject') {
                Write-Output ('❌ init 输入须为 JSON 对象：{0}（顶层非对象，如数组/标量）' -f $InputFile); exit 1  # 对齐 py 判型（原 fail-open 静默落盘）
            }
        }
        $title = $ProjectPath | Split-Path -Leaf
        if ($null -ne $payload.PSObject.Properties['book_title'] -and [string]$payload.book_title -ne '') { $title = [string]$payload.book_title }
        $st = New-DefaultState -BookTitle $title
        # 判型守卫：非对象值交由 Test-StateSchema 报错（防字符串枚举 CLR 属性 Length/Chars 污染权威 state）
        foreach ($k in @('overview', 'context')) {
            $prop = $payload.PSObject.Properties[$k]
            if ($null -ne $prop -and $null -ne $prop.Value) {
                if ($prop.Value -isnot [pscustomobject]) { $st.$k = $prop.Value }
                else { Merge-InitDefault -Dst $st.$k -Src $prop.Value }  # 递归合并，保留默认键（对齐 py _deep_merge）
            }
        }
        foreach ($k in @('characters', 'foreshadow', 'wordcounts', 'summaries')) {
            $prop = $payload.PSObject.Properties[$k]
            if ($null -ne $prop -and $null -ne $prop.Value) {
                if ($prop.Value -isnot [pscustomobject]) { $st.$k = $prop.Value }
                else { foreach ($f in $prop.Value.PSObject.Properties) { Set-NoteProperty -Obj $st.$k -Name ([string]$f.Name) -Value $f.Value } }
            }
        }
        $prop = $payload.PSObject.Properties['facts']
        if ($null -ne $prop -and $null -ne $prop.Value) {
            # PS5.1 对单元素 JSON 数组解包为标量 → 数组态显式重包；非数组交由 schema 报「facts 须为数组」（对齐 py）
            if ($prop.Value -is [array]) { $st.facts = @($prop.Value) } else { $st.facts = $prop.Value }
        }
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
        if ($Chapter -lt 0) { Write-Output '❌ --chapter 必须 ≥0（--chapter 0 = 大纲阶段种子提交，不产生 .done 标记）'; exit 1 }
        $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json
        $txnDir = Join-Path $ProjectPath (('{0}/第{1:d3}章/state.json' -f $WorkRel, $Chapter) -replace '/', [string][char]92)
        $txn = [ordered]@{
            chapter                   = $Chapter
            expected_state_revision   = [long]$st.state_revision
            mode                      = 'commit'
            _hint                     = '只填 delta：变化角色快照 / 新增伏笔 / 本章事实 / 时间线条目 / 摘要 / 字数 / 变化时的 overview（状态快照·阶段）与 context（位置·近三章·下一章承诺）；不要重写整份 state'
            delta                     = [ordered]@{
                characters = [ordered]@{}; foreshadow = [ordered]@{}; facts = @()
                timeline   = [ordered]@{ readers_known = @(); authors_truth = @() }
                summaries  = [ordered]@{}; wordcounts = [ordered]@{}; overview = [ordered]@{}; context = [ordered]@{}
            }
        }
        Write-Utf8File -Path $txnDir -Text (([pscustomobject]$txn | ConvertTo-Json -Depth 100) + $nl)
        Write-Output ('✅ 草稿已生成：{0}（expected_state_revision={1}）' -f (Get-RelPath $ProjectPath $txnDir), [long]$st.state_revision)
        exit 0
    }

    'commit' {
        if (-not (Test-Path -LiteralPath $sp)) { Write-Output ('❌ 缺少 {0}（先 init 或 migrate --apply）' -f $StateRel); exit 1 }
        if ($Chapter -lt 0) { Write-Output '❌ --chapter 必须 ≥0（--chapter 0 = 大纲阶段种子提交，不产生 .done 标记）'; exit 1 }
        if ($InputFile -eq '' -or -not (Test-Path -LiteralPath $InputFile -PathType Leaf)) { Write-Output '❌ 事务文件缺失（-InputFile）；用 draft 生成后填写再提交'; exit 1 }  # Leaf 判定（对齐 py is_file）
        try { $txn = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Write-Output ('❌ 事务 JSON 解析失败（改事务重跑）：{0}' -f $_.Exception.Message); exit 1 }
        if ($null -eq $txn -or $txn -isnot [pscustomobject] -or $txn.GetType().FullName -ne 'System.Management.Automation.PSCustomObject') {
            Write-Output ('❌ 事务须为 JSON 对象：{0}（顶层非对象；改事务重跑）' -f $InputFile); exit 1  # 对齐 py 判型
        }
        if ($txn.chapter -isnot [int] -and $txn.chapter -isnot [long]) { Write-Output ('❌ 事务 chapter={0} 与 -Chapter {1} 不符（改事务重跑）' -f $(if ($null -eq $txn.chapter) { 'None' } elseif ($txn.chapter -is [double] -or $txn.chapter -is [decimal]) { ([double]$txn.chapter).ToString('0.##########', [System.Globalization.CultureInfo]::InvariantCulture) } else { $txn.chapter }), $Chapter); exit 1 }
        elseif ([long]$txn.chapter -ne [long]$Chapter) { Write-Output ('❌ 事务 chapter={0} 与 -Chapter {1} 不符（改事务重跑）' -f $txn.chapter, $Chapter); exit 1 }
        $expProp = $txn.PSObject.Properties['expected_state_revision']
        if ($null -eq $expProp -or ($expProp.Value -isnot [int] -and $expProp.Value -isnot [long])) { Write-Output '❌ 事务缺 expected_state_revision（整数；改事务重跑）'; exit 1 }
        if ($null -eq $txn.PSObject.Properties['delta'] -or $txn.delta -isnot [pscustomobject]) { Write-Output '❌ 事务缺 delta 对象（改事务重跑）'; exit 1 }
        foreach ($dk2 in @('summaries','wordcounts','overview','context','characters','timeline','foreshadow')) {
            $pv2 = $txn.delta.PSObject.Properties[$dk2]
            if ($null -ne $pv2 -and $null -ne $pv2.Value -and $pv2.Value -isnot [pscustomobject]) { Write-Output ('❌ delta.{0} 须为对象（改事务重跑）' -f $dk2); exit 1 }
        }
        $pvF2 = $txn.delta.PSObject.Properties['facts']
        if ($null -ne $pvF2 -and $null -ne $pvF2.Value -and $pvF2.Value -isnot [array]) { Write-Output '❌ delta.facts 须为数组（改事务重跑）'; exit 1 }
        $dtl2 = $txn.delta.PSObject.Properties['timeline']
        if ($null -ne $dtl2 -and $null -ne $dtl2.Value -and $dtl2.Value -isnot [pscustomobject]) { Write-Output '❌ delta.timeline 须为对象（改事务重跑）'; exit 1 }
        if ($null -ne $dtl2 -and $dtl2.Value -is [pscustomobject]) {
            foreach ($tk2 in @('readers_known','authors_truth')) {
                $pvt2 = $dtl2.Value.PSObject.Properties[$tk2]
                if ($null -ne $pvt2 -and $null -ne $pvt2.Value -and $pvt2.Value -isnot [array]) { Write-Output ('❌ delta.timeline.{0} 须为数组（改事务重跑）' -f $tk2); exit 1 }
            }
        }
        $dctx2 = $txn.delta.PSObject.Properties['context']
        if ($null -ne $dctx2 -and $dctx2.Value -is [pscustomobject]) {
            foreach ($ck2 in @('recent_chapters','next_chapter_commitments','characters_present','mystery_knowledge','goals','long_term_constraints')) {
                $pvc2 = $dctx2.Value.PSObject.Properties[$ck2]
                if ($null -ne $pvc2 -and $null -ne $pvc2.Value -and $pvc2.Value -isnot [array]) { Write-Output ('❌ delta.context.{0} 须为数组（改事务重跑）' -f $ck2); exit 1 }
            }
        }
        $dc2 = $txn.delta.PSObject.Properties['context']
        if ($null -ne $dc2 -and $dc2.Value -is [pscustomobject] -and $null -ne $dc2.Value.PSObject.Properties['position'] -and $null -ne $dc2.Value.position -and $dc2.Value.position -isnot [pscustomobject]) { Write-Output '❌ delta.context.position 须为对象（改事务重跑）'; exit 1 }
        $dov2 = $txn.delta.PSObject.Properties['overview']
        if ($null -ne $dov2 -and $dov2.Value -is [pscustomobject] -and $null -ne $dov2.Value.PSObject.Properties['stages'] -and $null -ne $dov2.Value.stages -and $dov2.Value.stages -isnot [array]) { Write-Output '❌ delta.overview.stages 须为数组（改事务重跑）'; exit 1 }
        $dch2 = $txn.delta.PSObject.Properties['characters']
        if ($null -ne $dch2 -and $dch2.Value -is [pscustomobject]) {
            foreach ($cv2 in $dch2.Value.PSObject.Properties) { if ($null -ne $cv2.Value -and $cv2.Value -isnot [pscustomobject]) { Write-Output ('❌ delta.characters.{0} 须为对象（改事务重跑）' -f $cv2.Name); exit 1 } }
        }
        if ($null -ne $txn.delta.PSObject.Properties['summaries'] -and $txn.delta.summaries -is [pscustomobject]) {
            foreach ($dk in $txn.delta.summaries.PSObject.Properties) { if ([string]$dk.Name -notmatch '\A[0-9]{1,18}\z') { Write-Output '❌ summaries 键必须是十进制数字字符串（≤18 位）'; exit 1 } }
        }
        $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json
        $errs = Test-StateSchema $st
        if ($errs.Count) { Write-Output ('❌ 现有状态单校验失败（先修复再提交）：{0}' -f ($errs -join '；')); exit 1 }
        if ([long]$st.state_revision -eq [long]::MaxValue) {
            Write-Output '❌ state_revision 已到 Int64 上界，自增会溢出（拒绝提交；请人工归零并同步派生视图）'
            exit 1
        }
        if ([long]$txn.expected_state_revision -ne [long]$st.state_revision) {
            Write-Output ('❌ stale transaction：expected_state_revision={0}，当前 state_revision={1}（重跑 draft 取新 revision 再提交）' -f [long]$txn.expected_state_revision, [long]$st.state_revision)
            exit 1
        }
        $lk = Acquire-Lock -Project $ProjectPath
        if (-not $lk.Ok) { Write-Output ('❌ 写入失败：{0}（修正环境后重跑同一份 commit，事务文件已保留）' -f $lk.Msg); exit 1 }
        $commitOk = $false
        try {
        try {
            $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([long]$txn.expected_state_revision -ne [long]$st.state_revision) {
                Write-Output ('❌ stale transaction（锁内复核）：expected={0} actual={1}' -f [long]$txn.expected_state_revision, [long]$st.state_revision)
                exit 1
            }
            $st = Merge-Delta -State $st -Delta $txn.delta
            $verr2 = Test-StateSchema $st
            if ($verr2.Count) { Write-Output ('❌ 合并结果复验失败（改 delta 后重跑同一份 commit）：{0}' -f ($verr2 -join '；')); exit 1 }
            $st.state_revision = [long]$st.state_revision + 1
            $newLast = [Math]::Max([long]$st.last_committed_chapter, $Chapter)
            $st.last_committed_chapter = $newLast
            $views = $null
            try {
                $views = Get-DerivedViews $st   # 先渲染后落盘：渲染崩溃不半提交，事务保留可重跑
            } catch {
                Write-Output ('❌ 渲染派生视图失败（状态单类型非法：{0}）' -f $_.Exception.Message); exit 1
            }
            Write-StateFile -Project $ProjectPath -State $st
            $n = 0
            try {
                $n = Write-DerivedViews -Project $ProjectPath -Views $views
            } catch {
                Write-Output ('❌ 写入失败：派生视图未刷新（状态单已写入、事务已过期）：{0}。修环境后跑 state_render.py --project 补渲染 + state_commit check 复核' -f $_.Exception.Message); exit 1
            }
            $donePath = $null
            if ($Chapter -ge 1) {
                $doneDir = Join-Path $ProjectPath '.done'
                if (-not (Test-Path -LiteralPath $doneDir)) { New-Item -ItemType Directory -Path $doneDir -Force | Out-Null }
                $donePath = Join-Path $doneDir ('第{0:d3}章_state.done' -f $Chapter)
                [System.IO.File]::WriteAllBytes($donePath, [byte[]]@())
            }
            $commitOk = $true
        }
        catch {
            Write-Output ('❌ 写入失败：{0}（修正环境后重跑同一份 commit，事务文件已保留）' -f $_.Exception.Message)
        }
        }
        finally {
            Release-Lock $lk.Lock
        }
        if (-not $commitOk) { exit 1 }
        Remove-Item -LiteralPath $InputFile -Force -ErrorAction SilentlyContinue
        Write-Output ('✅ state.commit ok：chapter={0} revision→{1} last_committed_chapter={2}' -f $Chapter, [long]$st.state_revision, [long]$st.last_committed_chapter)
        if ($null -ne $donePath) {
            Write-Output ('✅ 派生已刷新（{0} 个文件）；标记 {1}' -f $n, (Get-RelPath $ProjectPath $donePath))
            Write-Output '提示：.done/第NNN章_state.done 为闭环必需标记（check-done 在存在 _state.json 时自动要求）'
        } else {
            Write-Output ('✅ 派生已刷新（{0} 个文件）；章节 0 种子提交不产生 .done 标记' -f $n)
        }
        exit 0
    }

    'check' {
        if (-not (Test-Path -LiteralPath $sp)) { Write-Output ('❌ 缺少 {0}（存量项目走旧路径即可，无需本命令）' -f $StateRel); exit 1 }
        # 已知口径：PS5.1 的 ConvertFrom-Json 对超双精度字面量（如 1e400）在解析层拒绝（下行走「JSON 损坏」），
        # py/pwsh7 则解析为 Infinity 并进入类型校验报「必须是整数」——三引擎均 rc1 fail-closed，主行类别差异为已声明口径
        try { $st = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Write-Output ('❌ 状态单 JSON 损坏：{0}' -f $_.Exception.Message); exit 1 }
        $errs = Test-StateSchema $st
        $views = $null
        if ($errs.Count) {
            $views = @{}   # 有校验错即跳过渲染（派生文件=0），与 py 同口径
        } else {
            try { $views = Get-DerivedViews $st } catch {
                Write-Output ('❌ 渲染派生视图失败（状态单类型非法：{0}）' -f $_.Exception.Message); exit 1
            }
        }
        $bad = if ($null -ne $views -and $views.Count) { Compare-DerivedViews -Project $ProjectPath -Views $views } else { @() }
        $lockPath = Join-Path $ProjectPath ($LockRel -replace '/', [string][char]92)
        $lockNote = ''
        if (Test-Path -LiteralPath $lockPath) {
            $age = ((Get-Date) - (Get-Item -LiteralPath $lockPath).LastWriteTime).TotalSeconds
            $extra = ''
            if ($age -gt $LockStaleSeconds) { $extra = '（超过 300s 下次提交会接管）' } else { $extra = '（<300s，可能有提交在进行）' }
            $lockNote = ('；⚠️ 残留锁 {0}s{1}' -f [int]$age, $extra)
        }
        Write-Output ('【state-commit check】{0}' -f $ProjectPath)
        Write-Output ('revision={0} last_committed_chapter={1} 派生文件={2}{3}' -f $(if ($st.state_revision -is [int] -or $st.state_revision -is [long]) { $st.state_revision } else { '非法' }), $(if ($st.last_committed_chapter -is [int] -or $st.last_committed_chapter -is [long]) { $st.last_committed_chapter } else { '非法' }), $views.Keys.Count, $lockNote)
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
if ($null -ne $st.PSObject.Properties['last_committed_chapter'] -and $st.last_committed_chapter) { $lastCh = [long]$st.last_committed_chapter }
$summKeys = @{}
if ($null -ne $st.PSObject.Properties['summaries'] -and $null -ne $st.summaries) {
    foreach ($spProp in $st.summaries.PSObject.Properties) { $summKeys[[string]$spProp.Name] = $true }
}
$staleChs = @()
# 只按已有正文的章判 stale（O(实际章数)：last_committed 可达 3e9，线性扫 1..last 会挂死；无正文的章不需要摘要）
$bodyDir = Join-Path $ProjectPath '正文'
if (Test-Path -LiteralPath $bodyDir) {
    foreach ($bf in Get-ChildItem -LiteralPath $bodyDir -Filter '第*章*' -File) {
        if ($bf.Name -match '^第([0-9]+)章') { $c = 0L; if ([long]::TryParse($Matches[1], [ref]$c) -and $c -le $lastCh -and -not $summKeys.ContainsKey(('{0:D3}' -f $c)) -and -not $summKeys.ContainsKey([string]$c)) { $staleChs += $c } }  # 键零填充/裸数字双形查表（键 "7" 合法）
    }
}
$staleChs = @($staleChs | Sort-Object)
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
        exit 0
    }

    'migrate' {
        $mem = Join-Path $ProjectPath '记忆系统'
        if (-not (Test-Path -LiteralPath $mem)) { Write-Output ('❌ 记忆系统 不存在：{0}' -f $mem); exit 1 }
        $spExist = Test-Path -LiteralPath $sp
        if ($spExist -and -not $Apply) { Write-Output ('✅ 已存在 {0}，无需重复 migrate' -f $StateRel); exit 0 }
        $draftPath = Join-Path $ProjectPath ($DraftRel -replace '/', [string][char]92)
        # 只归档会重渲染/可再生的派生件；非派生（如 圣经/角色数据库.md）保留原位（否则 03_26 步骤0/00 步骤B 缺件）
        $archiveNames = @('故事圣经.md', '人物弧线.md', '剧情时间线.md', '伏笔清单.md', '作者真相时间线.md', '事实台账.md', '伏笔时间线.md')
        $olds = @()
        foreach ($sub in @('圣经', '摘要')) {
            $d = Join-Path $mem $sub
            if (Test-Path -LiteralPath $d) {
                $found = @(Get-ChildItem -LiteralPath $d -Filter *.md -File | ForEach-Object { $_.FullName })
                if ($sub -eq '圣经') {
                    $found = @($found | Where-Object { $archiveNames -contains [System.IO.Path]::GetFileName($_) })
                } else {
                    # 只归档可由 state.summaries 重建的 第NNN章摘要；模板.md 等非派生件留原位（与 py 同口径）
                    $found = @($found | Where-Object { [System.IO.Path]::GetFileName($_) -match '^第(\d+)章摘要\.md$' })
                }
                $olds += Sort-Ordinal $found
            }
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
                    if ($f.Name -match '^第([0-9]+)章摘要\.md$') { $chapters += [long]$Matches[1] }
                }
            }
            $st = New-DefaultState -BookTitle $title
            $imported = 0
            if ($chapters.Count) { $imported = ($chapters | Measure-Object -Maximum).Maximum }
            $st.imported_through_chapter = [long]$imported
            $st.overview.snapshot = ('（迁移草案：由 {0} 个旧文件生成，关键内容需人工补进本 state）' -f $olds.Count)
            Write-Utf8File -Path $draftPath -Text (ConvertTo-StateJson $st)
            Write-Output ('✅ 迁移草案已生成：{0}（**尚未生效**）' -f $DraftRel)
            Write-Output '   - 待人工补全字段：characters / foreshadow / facts / timeline / context / summaries（与 wordcounts；草案只带书名与章数；characters 按保留原位的 圣经/角色数据库.md 摘填，timeline.authors_truth 按归档的 作者真相时间线.md 摘填，summaries 按归档的 摘要/第NNN章摘要.md 摘填——摘要/模板.md 保留原位）'
            $keptRoles = Join-Path $mem '圣经/角色数据库.md'
            if (Test-Path -LiteralPath $keptRoles) { Write-Output '   - 保留原位（非派生，不归档）：圣经/角色数据库.md' }
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
        Write-Output ('✅ migrate -Apply 完成：{0}（revision=0，last_committed_chapter={1}）' -f $StateRel, [long]$st.last_committed_chapter)
        Write-Output ('   归档旧文件 {0} 个 → {1}/（只移动、未删除）' -f $moved, $ArchiveDirName)
        Write-Output ('   重渲染派生 {0} 个文件' -f $n)
        Write-Output '⚠️ 新派生内容目前只含草案字段：请把关键信息补进 _state.json 后重跑 commit 或 state_render，并用 check 验证（summaries 未补则 摘要/ 不会重建，00 步骤B 会因缺摘要终止）'
        exit 0
    }
}
