# ============================================================
# 全能小说作家 - CI 完整性审计脚本（GitHub Actions 调用）
# 检查：①死引用 ②孤儿文件 ③版本号一致性(SKILL vs README徽章) ④残留英文路径token
# 用法：pwsh .github/scripts/audit.ps1 -Root <仓库路径>
# 发现问题输出清单并 exit 1（CI 失败）；全部通过 exit 0。
# ============================================================
param(
    [string]$Root = (Get-Location).Path
)

$enc = New-Object System.Text.UTF8Encoding($false)
$errors = @()

# ---------- ① 死引用审计 ----------
$files = Get-ChildItem -Recurse -File $Root -Filter *.md | ForEach-Object { $_.FullName.Substring($Root.Length+1) -replace '\\','/' }
$fileSet = @{}; foreach($f in $files){ $fileSet[$f] = $true }
$dirSet = @{}
foreach($f in $files){ $p = Split-Path $f -Parent; while($p -and $p -ne ''){ $dirSet[($p -replace '\\','/')]=$true; $p = Split-Path $p -Parent } }
$dirRe = '(?:modules|references|templates|memory-system|记忆系统模板|模块|参考资源|模板|圣经|摘要|阶段|卷|约束)'
$q = [char]34 + [char]39
$cls = '[^\s`，。；:：)\]】》' + $q + ']+'
$broken = @{}

function Test-Cand([string]$cand, [string]$srcDir, [string]$label, [string]$line, [int]$matchIdx) {
  $c = $cand.Trim()
  if($c -eq ''){ return }
  $c = $c -replace '^read_file\("','' -replace '"\)?$',''
  if($c -match '^(https?://|mailto:|#)'){ return }
  if($c -match '\s'){ return }
  $c = $c -replace '[\.,;:，。；：、）)】》」』\]\["'']+$',''
  if($c -notmatch '\.md$' -and $c -notmatch '/$'){ return }
  $c = $c -replace '^\./','' -replace '\\','/'
  if($c -match '^(\[书名\]|\{书名\}|\*|\.\./)'){ return }
  if($c -match '(NNN|XXX|ch_\*|chX|phase_N|volume_N|N\+|第X章|第NNN章|\{序号\}|\{起始\}|\{结束\}|\{章节编号|第\{|阶段N_|卷N_)'){ return }
  # 部署/宿主外部路径豁免（多 Agent 版部署说明，非仓库文件）
  if($c -match '^(skills/|agents/<|SOUL\.md$)'){ return }
  if($label -like 'BARE*' -and $matchIdx -gt 0){
    $prefix = $line.Substring(0, $matchIdx)
    if($prefix -match '(modules|references|templates|memory-system|记忆系统模板|模块|参考资源|模板|圣经|摘要|阶段|卷|约束)/$'){ return }
  }
  $cands = @($c)
  if($srcDir){ $cands += "$srcDir/$c" }
  if($c -match '^[0-9]{2}_'){ $cands += "agents/$c" }   # agents/ 角色目录内引用（BARE 拆出 NN_ 前缀）
  if($srcDir -match '^agents(/|$)' -and $c -notmatch '/'){
    $cands += "references/$c"; $cands += "templates/$c"; $cands += "memory-system/$c"; $cands += "modules/$c"
  }
  foreach($t in $cands){
    if($null -eq $t){ continue }
    $t = $t.TrimEnd('/') -replace '//+','/'
    if($fileSet.ContainsKey($t) -or $dirSet.ContainsKey($t)){ return }
  }
  $broken["$srcDir|$label|$c"] = $true
}

Get-ChildItem -Recurse -File $Root -Filter *.md | ForEach-Object {
  $rel = $_.FullName.Substring($Root.Length+1) -replace '\\','/'
  $parts = $rel -split '/'
  $srcDir = if($parts.Count -gt 1){ ($parts[0..($parts.Count-2)] -join '/') } else { '' }
  $lines = [System.IO.File]::ReadAllLines($_.FullName, $enc)
  for($i=0; $i -lt $lines.Count; $i++){
    $line = $lines[$i]; $ln = $i+1
    [regex]::Matches($line, '\]\(([^)]+)\)') | ForEach-Object { Test-Cand $_.Groups[1].Value $srcDir "LINK $rel :$ln" $line $_.Index }
    [regex]::Matches($line, '`([^`]+)`') | ForEach-Object { Test-Cand $_.Groups[1].Value $srcDir "SPAN $rel :$ln" $line $_.Index }
    $pathRe = "(?<![A-Za-z0-9_/])${dirRe}/" + $cls
    [regex]::Matches($line, $pathRe) | ForEach-Object { Test-Cand $_.Value $srcDir "PATH $rel :$ln" $line $_.Index }
    $bareRe = "[0-9]{2}_" + $cls + '\.md'
    [regex]::Matches($line, $bareRe) | ForEach-Object { Test-Cand $_.Value $srcDir "BARE $rel :$ln" $line $_.Index }
  }
}

$genuine = @($broken.Keys | Where-Object { $_ -notmatch '记忆系统|设定/|正文/|导出/|拆解/|细纲/|报告/|\.done|项目|进度看板|创作状态追踪|商业可行性|创新深度|章节规划表|立项定位|文风设定|故事圣经|人物弧线|剧情时间线|伏笔清单|角色数据库|金手指约束|时间约束|叙事线|约束|阶段|卷|摘要|圣经|SMOKE_TEST|我的修仙小说|memory-system/|拆解报告|情节节点|情绪模块|人物模块|素材引用说明|文风' } | Where-Object { $_ -notmatch '\|\.md$' })
if($genuine.Count){
  $errors += "死引用审计发现 $($genuine.Count) 处疑似问题："
  $genuine | Sort-Object | ForEach-Object { $errors += "  $_" }
}

# ---------- ② 孤儿文件 ----------
$allText = @{}
Get-ChildItem -Recurse -File $Root -Filter *.md | ForEach-Object {
  $rel = $_.FullName.Substring($Root.Length+1) -replace '\\','/'
  $allText[$rel] = [System.IO.File]::ReadAllText($_.FullName, $enc)
}
$orphans = @()
foreach($f in $files){
  if($f -match '^(README|LICENSE|\.gitignore)'){ continue }
  if($f -like '.github/*'){ continue }   # .github/ 为维护者工具/文档，无需被 skill 内容引用
  $base = [System.IO.Path]::GetFileName($f); $fwd = $f -replace '\\','/'
  $hits = 0
  foreach($k in $allText.Keys){ if($k -ne $fwd -and ($allText[$k].Contains($base) -or $allText[$k].Contains($fwd))){ $hits++ } }
  if($hits -eq 0){ $orphans += $fwd }
}
if($orphans.Count){ $errors += "孤儿文件：" ; $orphans | ForEach-Object { $errors += "  $_" } }

# ---------- ③ 版本号一致性 ----------
$skillVer = ''
$skillContent = [System.IO.File]::ReadAllText((Join-Path $Root 'SKILL.md'), $enc)
if($skillContent -match 'version:\s*([0-9]+\.[0-9]+\.[0-9]+)'){ $skillVer = $Matches[1] }
$readmeVer = ''
$readmeContent = [System.IO.File]::ReadAllText((Join-Path $Root 'README.md'), $enc)
if($readmeContent -match 'version-([0-9]+\.[0-9]+\.[0-9]+)-blue'){ $readmeVer = $Matches[1] }
if($skillVer -eq '' -or $readmeVer -eq ''){ $errors += "版本号解析失败：SKILL=$skillVer README=$readmeVer" }
elseif($skillVer -ne $readmeVer){ $errors += "版本号不一致：SKILL.md=$skillVer vs README徽章=$readmeVer" }

# ---------- ④ 残留英文路径 token（项目侧）----------
$forbidden = @('记忆系统/bible','记忆系统/summaries','记忆系统/phases','记忆系统/volumes','记忆系统/constraints','记忆系统/bible/')
$tokens = @()
Get-ChildItem -Recurse -File $Root -Filter *.md | ForEach-Object {
  $rel = $_.FullName.Substring($Root.Length+1)
  $lines = [System.IO.File]::ReadAllLines($_.FullName, $enc)
  for($i=0;$i -lt $lines.Count;$i++){
    foreach($f in $forbidden){
      if($lines[$i].Contains($f)){ $tokens += "$rel :$($i+1) 含残留路径 '$f'" }
    }
  }
}
if($tokens.Count){ $errors += "残留英文记忆系统路径 $($tokens.Count) 处：" ; $tokens | Select-Object -Unique | ForEach-Object { $errors += "  $_" } }

# ---------- 汇总 ----------
if($errors.Count){
  Write-Output "❌ 审计未通过："
  $errors | ForEach-Object { Write-Output $_ }
  exit 1
} else {
  Write-Output "✅ 审计全部通过：死引用 0 / 孤儿 0 / 版本一致($skillVer) / 无残留英文路径"
  exit 0
}
