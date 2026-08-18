# ============================================================
# 全能小说作家 - 旧项目记忆系统路径迁移脚本
# 用途：将 v9.4.0 之前创建的旧项目（记忆系统为英文路径）迁移到
#       9.4.0+ 的中文路径规范，否则跨会话恢复（搜索 */记忆系统/圣经/故事圣经.md）找不到旧项目。
#
# 用法（在项目根目录运行，或指定 -TargetDir）：
#   powershell -ExecutionPolicy Bypass -File migrate_memory_paths.ps1            # 预览（WhatIf）
#   powershell -ExecutionPolicy Bypass -File migrate_memory_paths.ps1 -Apply     # 实际迁移
#   powershell -ExecutionPolicy Bypass -File migrate_memory_paths.ps1 -TargetDir D:\我的小说 -Apply
#
# 特性：幂等（旧路径不存在则跳过，可重复运行）；-WhatIf 默认预览；输出完整操作日志。
# ============================================================

param(
    [string]$TargetDir = (Get-Location).Path,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'

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
    Get-ChildItem -LiteralPath $dir -Filter $pattern -File -ErrorAction SilentlyContinue | ForEach-Object {
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
$projects = Get-ChildItem -LiteralPath $TargetDir -Directory -ErrorAction SilentlyContinue | Where-Object {
    Test-Path -LiteralPath (Join-Path $_.FullName '记忆系统')
}

if($projects.Count -eq 0){
    Write-Host "未在 '$TargetDir' 下找到含 记忆系统/ 目录的项目（可能已迁移或路径不对）。" -ForegroundColor Yellow
    exit 0
}

$totalMoves = 0
foreach($proj in $projects){
    $memRoot = Join-Path $proj.FullName '记忆系统'
    Write-Host ""
    Write-Host "===== 项目：$($proj.Name) =====" -ForegroundColor Magenta

    # 1) 迁移子目录
    foreach($k in $dirMap.Keys){
        $old = Join-Path $memRoot $k
        $new = Join-Path $memRoot $dirMap[$k]
        if(Test-Path -LiteralPath $old){
            if(Test-Path -LiteralPath $new){ Write-Host "  [跳过] 目录 $old (目标 $new 已存在)" -ForegroundColor Yellow }
            else {
                Write-Host "  $('[' + $(if($Apply){'迁移'}else{'预览'}) + ']') 目录 $old -> $new" -ForegroundColor $(if($Apply){'Green'}else{'Cyan'})
                if($Apply){ Move-Item -LiteralPath $old -Destination $new }
                $totalMoves++
            }
        }
    }

    # 2) 迁移固定文件名（新目录 + 旧目录都扫，兼容部分迁移状态）
    foreach($dirName in @($dirMap.Values | ForEach-Object { $_ }) + @('bible','summaries','phases','volumes','constraints')){
        $dir = Join-Path $memRoot $dirName
        if(-not (Test-Path -LiteralPath $dir)){ continue }
        foreach($k in $fileMap.Keys){
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
        Rename-Pattern $sumDir 'ch_*_summary.md' { param($n) if($n -match '^ch_(\d+)_summary\.md$'){ '第{0}章摘要.md' -f ([int]$Matches[1]).ToString('000') } }
        Rename-Pattern $sumDir 'ch_*_quality.md' { param($n) if($n -match '^ch_(\d+)_quality\.md$'){ '第{0}章质量.md' -f ([int]$Matches[1]).ToString('000') } }
    }
    if(Test-Path -LiteralPath $phaseDir){
        Rename-Pattern $phaseDir 'phase_*_ch*-*.md' { param($n) if($n -match '^phase_(\d+)_ch(\d+)-(\d+)\.md$'){ '阶段{0}_第{1}-{2}章.md' -f $Matches[1], $Matches[2], $Matches[3] } }
        Rename-Pattern $phaseDir 'phase_*_params.md' { param($n) if($n -match '^phase_(\d+)_params\.md$'){ '阶段{0}_参数.md' -f $Matches[1] } }
    }
    if(Test-Path -LiteralPath $volumeDir){
        Rename-Pattern $volumeDir 'volume_*_ch*-*.md' { param($n) if($n -match '^volume_(\d+)_ch(\d+)-(\d+)\.md$'){ '卷{0}_第{1}-{2}章.md' -f $Matches[1], $Matches[2], $Matches[3] } }
        Rename-Pattern $volumeDir 'volume_*_params.md' { param($n) if($n -match '^volume_(\d+)_params\.md$'){ '卷{0}_参数.md' -f $Matches[1] } }
    }
}

Write-Host ""
if(-not $Apply){
    Write-Host "========== 预览完成：共 $totalMoves 项待迁移。确认无误后加 -Apply 执行。 ==========" -ForegroundColor Cyan
} else {
    Write-Host "========== 迁移完成：共 $totalMoves 项已迁移。 ==========" -ForegroundColor Green
    Write-Host "提示：迁移后旧项目的『完成步骤』/报告文件命名不受影响；正文无需改动。若项目内自定义文档引用了旧路径，请手动更新。" -ForegroundColor Yellow
}
