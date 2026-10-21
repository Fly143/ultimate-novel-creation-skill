# ============================================================
# 全能小说作家 - 旧项目记忆系统路径迁移脚本
# 用途：将 v9.4.0 之前创建的旧项目（记忆系统为英文路径）迁移到
#       9.4.0+ 的中文路径规范，否则跨会话恢复（搜索 */记忆系统/圣经/故事圣经.md）找不到旧项目。
#
# 用法（在项目根目录运行，或指定 -TargetDir）：
#   powershell -ExecutionPolicy Bypass -File migrate_memory_paths.ps1            # 预览（不带 -Apply）
#   powershell -ExecutionPolicy Bypass -File migrate_memory_paths.ps1 -Apply     # 实际迁移
#   powershell -ExecutionPolicy Bypass -File migrate_memory_paths.ps1 -TargetDir D:\我的小说 -Apply
#
# 特性：幂等（旧路径不存在则跳过，可重复运行）；不带 -Apply 即预览（未启用 SupportsShouldProcess，传 -WhatIf 会报参数不存在）；输出完整操作日志。
# ============================================================

[CmdletBinding()]
param(
    [string]$TargetDir = (Get-Location).Path,
    [switch]$Apply
)

try { [System.Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }  # 中文输出不乱码（对齐其余 ps1）
$ErrorActionPreference = 'Stop'

# Root 存在性 fail-fast（对齐 audit.ps1:31-35：不存在时 SilentlyContinue 吞错 → 误报「已迁移」fail-open rc0）
if (-not (Test-Path -LiteralPath $TargetDir -PathType Container)) {
    [Console]::Out.WriteLine(('❌ 目标目录不存在：{0}' -f $TargetDir)); exit 1
}

# ---- 目录映射（记忆系统/ 下）----
$dirMap = @{
    'bible'       = '圣经'
    'summaries'   = '摘要'
    'phases'      = '阶段'
    'volumes'     = '卷'
    'constraints' = '约束'
}

# ---- 固定文件名映射 ----
$fileMap = @{
    'story_bible.md'          = '故事圣经.md'
    'character_arcs.md'       = '人物弧线.md'
    'plot_timeline.md'        = '剧情时间线.md'
    'chekhovs_gun.md'         = '伏笔清单.md'
    'character_database.md'   = '角色数据库.md'
    'cheat_constraints.md'    = '金手指约束.md'
    'time_constraints.md'     = '时间约束.md'
    'narrative_threads.md'    = '叙事线.md'
    '_template.md'            = '模板.md'
}

function Rename-Pattern([string]$dir, [string]$pattern, [scriptblock]$converter) {
    $enum = @(Get-ChildItem -LiteralPath $dir -Filter $pattern -File -ErrorAction SilentlyContinue)
    $enumNames = [string[]]@($enum | ForEach-Object { $_.Name })
    [Array]::Sort($enumNames, [System.StringComparer]::Ordinal)  # Ordinal 排序：原枚举序引擎不定 → 首个报错文件分叉
    $enumByName = @{}
    foreach ($e in $enum) { $enumByName[$e.Name] = $e }
    foreach ($eName in $enumNames) {
        $_ = $enumByName[$eName]
        $newName = & $converter $_.Name
        if($newName -and $newName -ne $_.Name){
            $dest = Join-Path $dir $newName
            if(Test-Path -LiteralPath $dest){ Write-Host "  [跳过] $($_.FullName) -> $dest (目标已存在)" -ForegroundColor Yellow }
            else {
                Write-Host "  $('[' + $(if($Apply){'迁移'}else{'预览'}) + ']') $($_.FullName) -> $dest" -ForegroundColor $(if($Apply){'Green'}else{'Cyan'})
                if($Apply){ Move-Item -LiteralPath $_.FullName -Destination $dest }
            }
        }
    }
}

# ---- 找到所有含 记忆系统/ 的旧目录（旧项目特征：bible 目录存在或英文文件名存在）----
$projects = Get-ChildItem -LiteralPath $TargetDir -Directory -Recurse -Depth 2 -ErrorAction SilentlyContinue | Where-Object {
    # 递归两层：书放在 TargetDir/外层/书 时也能发现（只扫一层曾静默 0 项报「可能已迁移」）
    $_.FullName -notmatch '记忆系统' -and (Test-Path -LiteralPath (Join-Path $_.FullName '记忆系统'))
} | Sort-Object -Property FullName

if($projects.Count -eq 0){
    Write-Host "未在 '$TargetDir' 下找到含 记忆系统/ 目录的项目（可能已迁移或路径不对）。" -ForegroundColor Yellow
    exit 0
}

$totalMoves = 0
$conflicts = @()   # 目录级冲突：新旧目录并存时不得再在旧目录内改名（否则两处内容分叉且 rc0 报成功）
$skipDirs = @{}
foreach($proj in $projects){
    $memRoot = Join-Path $proj.FullName '记忆系统'
    Write-Host ""
    Write-Host "===== 项目：$($proj.Name) =====" -ForegroundColor Magenta

    # 1) 迁移子目录
    foreach($k in ($dirMap.Keys | Sort-Object)){  # 键序确定（pwsh7/PS5.1 哈希迭代序不同，预览日志对拍一致）
        $old = Join-Path $memRoot $k
        $new = Join-Path $memRoot $dirMap[$k]
        if(Test-Path -LiteralPath $old){
            if(Test-Path -LiteralPath $new){
                Write-Host "  [跳过] 目录 $old (目标 $new 已存在)" -ForegroundColor Yellow
                $conflicts += $old
                $skipDirs[$old] = $true
            }
            else {
                Write-Host "  $('[' + $(if($Apply){'迁移'}else{'预览'}) + ']') 目录 $old -> $new" -ForegroundColor $(if($Apply){'Green'}else{'Cyan'})
                if($Apply){ Move-Item -LiteralPath $old -Destination $new }
                $totalMoves++
            }
        }
    }

    # 2) 迁移固定文件名（新目录 + 旧目录都扫，兼容部分迁移状态）
    $dirNames = [string[]]@($dirMap.Values) + @('bible','summaries','phases','volumes','constraints')
    [Array]::Sort($dirNames, [System.StringComparer]::Ordinal)  # 键序已定但值序仍随哈希迭代漂移（pwsh7/PS5.1 不同）→ 排定
    foreach($dirName in $dirNames){
        $dir = Join-Path $memRoot $dirName
        if(-not (Test-Path -LiteralPath $dir)){ continue }
        if($skipDirs.ContainsKey($dir)){ Write-Host "  [跳过] $dir 内不改名（目录级冲突，先人工合并两处内容）" -ForegroundColor Yellow; continue }
        foreach($k in ($fileMap.Keys | Sort-Object)){  # 键序确定（同上）
            $old = Join-Path $dir $k
            if(Test-Path -LiteralPath $old){
                $dest = Join-Path $dir $fileMap[$k]
                if(Test-Path -LiteralPath $dest){ Write-Host "  [跳过] $old (目标已存在)" -ForegroundColor Yellow }
                else {
                    Write-Host "  $('[' + $(if($Apply){'迁移'}else{'预览'}) + ']') $old -> $dest" -ForegroundColor $(if($Apply){'Green'}else{'Cyan'})
                    if($Apply){ Move-Item -LiteralPath $old -Destination $dest }
                    $totalMoves++
                }
            }
        }
    }

    # 3) 模式化文件名（摘要/阶段/卷/参数）
    $bibleDir    = Join-Path $memRoot '圣经'
    $sumDir      = Join-Path $memRoot '摘要'
    $phaseDir    = Join-Path $memRoot '阶段'
    $volumeDir   = Join-Path $memRoot '卷'
    $constraintDir = Join-Path $memRoot '约束'

    if(Test-Path -LiteralPath $sumDir){
        Rename-Pattern $sumDir 'ch_*_summary.md' { param($n)
            if($n -match '^ch_([0-9]+)_summary\.md$'){  # [0-9] 收口（.NET \d 认 Nd 族后 [int] 裸转换英文异常页）
                $cn = 0L
                if([long]::TryParse($Matches[1], [ref]$cn)){ return '第{0}章摘要.md' -f $cn.ToString('000') }  # TryParse 收敛（裸 [int] 超界英文 Overflow 分叉）
            } }
        Rename-Pattern $sumDir 'ch_*_quality.md' { param($n)
            if($n -match '^ch_([0-9]+)_quality\.md$'){  # [0-9] 收口同 summary
                $qn = 0L
                if([long]::TryParse($Matches[1], [ref]$qn)){ return '第{0}章质量.md' -f $qn.ToString('000') }
            } }
    }
    if(Test-Path -LiteralPath $phaseDir){
        Rename-Pattern $phaseDir 'phase_*_ch*-*.md' { param($n) if($n -match '^phase_([0-9]+)_ch([0-9]+)-([0-9]+)\.md$'){ '阶段{0}_第{1}-{2}章.md' -f $Matches[1], $Matches[2], $Matches[3] } }  # [0-9] 收口（字符串插值无转换风险，仅 Nd 族正则拒）
        Rename-Pattern $phaseDir 'phase_*_params.md' { param($n) if($n -match '^phase_([0-9]+)_params\.md$'){ '阶段{0}_参数.md' -f $Matches[1] } }  # [0-9] 收口同上
    }
    if(Test-Path -LiteralPath $volumeDir){
        Rename-Pattern $volumeDir 'volume_*_ch*-*.md' { param($n) if($n -match '^volume_([0-9]+)_ch([0-9]+)-([0-9]+)\.md$'){ '卷{0}_第{1}-{2}章.md' -f $Matches[1], $Matches[2], $Matches[3] } }  # [0-9] 收口同上
        Rename-Pattern $volumeDir 'volume_*_params.md' { param($n) if($n -match '^volume_([0-9]+)_params\.md$'){ '卷{0}_参数.md' -f $Matches[1] } }  # [0-9] 收口同上
    }
}

Write-Host ""
if(-not $Apply){
    Write-Host "========== 预览完成：共 $totalMoves 项待迁移。确认无误后加 -Apply 执行。 ==========" -ForegroundColor Cyan
} else {
    if($conflicts.Count -gt 0){
        Write-Host ("❌ 有 {0} 处目录级冲突未处理（新旧目录并存，旧目录内文件未改名）：" -f $conflicts.Count) -ForegroundColor Red
        foreach($cf in $conflicts){ Write-Host ("   - {0}" -f $cf) -ForegroundColor Red }
        Write-Host "   请先人工合并两处内容（或删除已空的旧目录）后重跑；本次已迁移 $totalMoves 项。" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "========== 迁移完成：共 $totalMoves 项已迁移。 ==========" -ForegroundColor Green
    Write-Host "提示：迁移后旧项目的报告文件命名不受影响；正文无需改动。若项目内自定义文档引用了旧路径，请手动更新。" -ForegroundColor Yellow
    Write-Host "      另：v9.5.18 起正文不再追加元数据块（改存 报告/第NNN章_章节档案.md），旧章节无需迁移；" -ForegroundColor Yellow
    Write-Host "      旧正文明细里的『完成步骤』字段已废除（完成状态只认 .done/ 空标记），可忽略。" -ForegroundColor Yellow
}
