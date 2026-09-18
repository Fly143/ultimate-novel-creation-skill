# ============================================================
# 全能小说作家 - CI 完整性审计脚本（GitHub Actions 调用）
# 检查：①死引用 ②孤儿文件 ③版本号一致性(SKILL vs README徽章) ④残留英文路径token
#      ⑤裸英文术语(警告) ⑥裸行号引用(警告) ⑦写后顺序与 zhuque 门禁一致性
# 用法：pwsh .github/scripts/audit.ps1 -Root <仓库路径> [-StrictOrphan] [-LintTerm] [-LintLineRef] [-LintOrder]
#   -StrictOrphan : 孤儿判定收紧为「必须存在指向该资产路径的引用」（默认关闭，便于渐进收紧）
#   -LintTerm     : 额外检查裸英文术语 volume/phase/stage/summary（仅警告，不阻断）
#   -LintLineRef  : 额外检查「第N行」式行号引用（仅警告，不阻断）
#   -LintOrder    : 额外检查写后顺序权威（先朱雀后修复）与 .done_zhuque 语义锚点（默认开启可单独关：-LintOrder:$false）
# 发现问题输出清单并 exit 1（CI 失败）；全部通过 exit 0。
# ============================================================
param(
    [string]$Root = (Get-Location).Path,
    [switch]$StrictOrphan,
    [switch]$LintTerm,
    [switch]$LintLineRef,
    [bool]$LintOrder = $true
)

$enc = New-Object System.Text.UTF8Encoding($false)
$errors = @()
$warns = @()

# 归一化 Root 为绝对路径：后续用 $Root.Length 做 Substring 截取相对路径，
# 传相对路径（如 -Root .）会导致路径错位、全部检查失效。
$Root = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\','/')

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

if($StrictOrphan){
  # 收紧版：必须存在「带目录前缀、指向该资产路径」的引用。
  # 旧版用「裸文件名是否出现在任意文档」判定，会被与项目侧产物同名的裸名（如 进度看板.md、
  # 创作状态追踪表.md）骗过而漏报——那类模板名字在仓库里到处都是，却可能无人真正引用。
  $orphans = @()
  $docRoots = @('README.md','LICENSE','.gitignore','SKILL.md','system_prompt.md')
  # 可解析资产清单（排除 .git）
  $allFiles = Get-ChildItem -Recurse -File $Root | ForEach-Object { $_.FullName.Substring($Root.Length+1) -replace '\\','/' }
  $assetSet = @{}; foreach($a in $allFiles){ if($a -notlike '.git/*'){ $assetSet[$a] = $true } }
  # 引用前缀目录白名单（与 skill 资产目录一致）
  $strictDirs = @('modules','references','templates','memory-system','agents','scripts','templates/constraints','references/rulesets')
  $strictDirsCn = @('模块','参考资源','模板','圣经','摘要','阶段','卷','约束')
  $strictPre = '(?:' + (($strictDirs + $strictDirsCn) -join '|') + ')'
  # 引用形态：<白名单目录>/<文件名>.md —— 要求 .md 前有 '/'，故「(模板：xxx.md)」这类裸名不计入
  $strictRe = "(?<![A-Za-z0-9_/-])($strictPre)/[^\s``，。；:：)\]】》:'\"" ]+\.md"
  $refs = @{}
  foreach($k in $allText.Keys){
    foreach($m in [regex]::Matches($allText[$k], $strictRe)){
      $c = $m.Value -replace '^\./','' -replace '[\.,;:，。；：、）)】》」』\]\["''`]+$',''
      # 只取最长匹配（避免 templates/constraints/x.md 被记成 constraints/x.md）
      if($c -match '/' -and -not $refs.ContainsKey($c)){ $refs[$c] = $true }
    }
  }
  foreach($f in $files){
    if($docRoots -contains $f){ continue }
    if($f -like '.github/*'){ continue }        # 维护者工具，无需被 skill 内容引用
    if($f -like 'modules/*'){ continue }        # 模块为主动加载入口，由 00 协议/索引引用
    if(-not $refs.ContainsKey($f)){ $orphans += $f }
  }
  if($orphans.Count){
    $errors += "孤儿文件（不存在指向该资产路径的引用）："
    $orphans | Sort-Object | ForEach-Object { $errors += "  $_" }
  }
} else {
  # 兼容版（默认）：文件名出现在任意文档中即视为已引用
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
}

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

# ---------- ⑤ 裸英文术语（警告，不阻断）----------
if($LintTerm){
  # v9.4.0 已声称记忆系统路径中文化，但散文里的英文术语漏改过（volume/phase/stage/summaries）。
  # 排除：代码围栏内内容、.done 标记名（受文件系统约束不可改，且已不在检查词表内）
  $termRe = '(?<![A-Za-z_/-])(volume|phase|stage|summaries)(?![A-Za-z_])'
  $termHits = @()
  Get-ChildItem -Recurse -File $Root -Filter *.md | ForEach-Object {
    $rel = $_.FullName.Substring($Root.Length+1)
    if($rel -like '.github/*'){ return }
    $lines = [System.IO.File]::ReadAllLines($_.FullName, $enc)
    $inFence = $false
    for($i=0;$i -lt $lines.Count;$i++){
      $ln = $lines[$i]
      if($ln -match '^\s*(```|~~~)'){ $inFence = -not $inFence; continue }
      if($inFence){ continue }
      if($ln -match '→|->'){ continue }   # 路径映射表（如 summaries→摘要）本就需要英文原名，豁免
      if($ln -match $termRe){ $termHits += "$rel :$($i+1) 裸英文术语 '$($Matches[1])'" }
    }
  }
  if($termHits.Count){
    $warns += "裸英文术语 $($termHits.Count) 处（建议中文化：卷压缩/阶段压缩/单章摘要）："
    $termHits | Select-Object -Unique | ForEach-Object { $warns += "  $_" }
  }
}

# ---------- ⑥ 裸行号引用（警告，不阻断）----------
if($LintLineRef){
  # 用「第 N 行」定位文档内容极易因增删行而失准（CI 无法发现）。改用节名/标题引用。
  $lineHits = @()
  Get-ChildItem -Recurse -File $Root -Filter *.md | ForEach-Object {
    $rel = $_.FullName.Substring($Root.Length+1)
    if($rel -like '.github/*'){ return }
    $lines = [System.IO.File]::ReadAllLines($_.FullName, $enc)
    $inFence = $false
    for($i=0;$i -lt $lines.Count;$i++){
      $ln = $lines[$i]
      if($ln -match '^\s*(```|~~~)'){ $inFence = -not $inFence; continue }
      if($inFence){ continue }
      # 只查「定位文档结构」的语境，避开写作指标（如「每千字」「第 N 章」）
      if($ln -match '第\s*\d+\s*[-–~到至]\s*\d+\s*行' -or $ln -match '(总流程|本文件|上文|下文|步骤表).{0,10}第\s*\d+\s*行'){
        $lineHits += "$rel :$($i+1) 裸行号引用"
      }
    }
  }
  if($lineHits.Count){
    $warns += "裸行号引用 $($lineHits.Count) 处（增删行即失准，建议改为节名引用）："
    $lineHits | Select-Object -Unique | ForEach-Object { $warns += "  $_" }
  }
}

# ---------- ⑦ 写后顺序与 zhuque 门禁一致性（防多文件再次漂移）----------
# 顺序权威 = modules/03_26_功能模块.md 步骤4（先朱雀 4.1b–4.1e → .done_zhuque → 再修复）。
# 其余权威文件必须至少含一条「先朱雀」正向锚点，且不得把「先修复再朱雀」写成必做顺序。
if($LintOrder){
  $orderFiles = @(
    @{ Path='modules/03_26_功能模块.md'; AnyOf=@('先执行 4.1b','均先做 4.1b','先 4.1b–4.1e','先做 4.1b','先跑 4.1b') },
    @{ Path='modules/00_强制执行协议.md'; AnyOf=@('先跑朱雀','先朱雀') },
    @{ Path='SKILL.md'; AnyOf=@('先跑朱雀','先 4.1b') },
    @{ Path='system_prompt.md'; AnyOf=@('先朱雀','先执行朱雀') },
    @{ Path='README.md'; AnyOf=@('先跑 4.1b','先写 zhuque','先朱雀','先 4.1b') }
  )
  foreach($spec in $orderFiles){
    $fp = Join-Path $Root $spec.Path
    if(-not (Test-Path -LiteralPath $fp)){
      $errors += "顺序一致性：缺失权威文件 $($spec.Path)"
      continue
    }
    $text = [System.IO.File]::ReadAllText($fp, $enc)
    $hit = $false
    foreach($a in $spec.AnyOf){ if($text.Contains($a)){ $hit = $true; break } }
    if(-not $hit){
      $errors += "顺序一致性：$($spec.Path) 缺少「先朱雀/先 4.1b」正向锚点（任一：$($spec.AnyOf -join ' / ')）"
    }
    if($text -notmatch 'done_zhuque'){
      $errors += "顺序一致性：$($spec.Path) 未出现 done_zhuque（闭环门禁锚点缺失）"
    }
  }

  # 禁止把错误顺序写成必做（「禁止/不得」语境豁免）
  $wrongOrderRe = '(先修复再朱雀|修复\s*→\s*朱雀|修复\s*→\s*\.done_zhuque|修复后再(跑)?朱雀|修复，再(跑)?朱雀)'
  $allowRe = '(禁止|不得|禁写|错误顺序|互斥|勿写|不要写|不要)'
  $orderHits = @()
  Get-ChildItem -Recurse -File $Root -Filter *.md | ForEach-Object {
    $rel = $_.FullName.Substring($Root.Length+1) -replace '\\','/'
    if($rel -like '.github/*'){ return }
    $lines = [System.IO.File]::ReadAllLines($_.FullName, $enc)
    $inFence = $false
    for($i=0;$i -lt $lines.Count;$i++){
      $ln = $lines[$i]
      if($ln -match '^\s*(```|~~~)'){ $inFence = -not $inFence; continue }
      if($inFence){ continue }
      if($ln -match $wrongOrderRe -and $ln -notmatch $allowRe){
        $orderHits += "$rel :$($i+1) 疑似写成「先修复后朱雀」必做顺序"
      }
    }
  }
  if($orderHits.Count){
    $errors += "顺序一致性：发现 $($orderHits.Count) 处错误写后顺序表述："
    $orderHits | Select-Object -Unique | ForEach-Object { $errors += "  $_" }
  }

  # 语义边界：权威文件应区分「.done 已登记」与「朱雀达标」
  $semanticFiles = @('README.md','modules/00_强制执行协议.md','modules/03_26_功能模块.md','SKILL.md')
  foreach($sf in $semanticFiles){
    $fp = Join-Path $Root $sf
    if(-not (Test-Path -LiteralPath $fp)){ continue }
    $text = [System.IO.File]::ReadAllText($fp, $enc)
    if($text -notmatch '(语义边界|≠\s*朱雀|不等于.*人工|≠\s*检测)'){
      $warns += "顺序一致性（警告）：$sf 建议显式写明 `.done_zhuque` ≠ 朱雀达标（语义边界）"
    }
  }

  # 可选工具脚本存在性（文档已引用时）
  $checkPs1 = Join-Path $Root 'scripts/check-done.ps1'
  $checkPy  = Join-Path $Root 'scripts/check-done.py'
  $docBlob = ''
  foreach($p in @('README.md','modules/00_强制执行协议.md','modules/03_26_功能模块.md')){
    $fp = Join-Path $Root $p
    if(Test-Path -LiteralPath $fp){ $docBlob += [System.IO.File]::ReadAllText($fp, $enc) }
  }
  if($docBlob -match 'check-done\.ps1' -and -not (Test-Path -LiteralPath $checkPs1)){
    $errors += "顺序一致性：文档引用了 scripts/check-done.ps1 但文件不存在"
  }
  if($docBlob -match 'check-done\.py' -and -not (Test-Path -LiteralPath $checkPy)){
    $errors += "顺序一致性：文档引用了 scripts/check-done.py 但文件不存在"
  }
}

# ---------- 汇总 ----------
$warnGroups = 0
if($warns.Count){ $warnGroups = @($warns | Where-Object { $_ -notmatch '^\s\s' }).Count }
if($errors.Count){
  Write-Output "❌ 审计未通过："
  $errors | ForEach-Object { Write-Output $_ }
  if($warns.Count){ Write-Output ""; Write-Output "⚠️ 另有警告 $warnGroups 组（不阻断）："; $warns | ForEach-Object { Write-Output $_ } }
  exit 1
} else {
  $orphanNote = if($StrictOrphan){ "孤儿 0（收紧判定）" } else { "孤儿 0（兼容判定）" }
  $orderNote = if($LintOrder){ " / 写后顺序一致" } else { "" }
  Write-Output "✅ 审计全部通过：死引用 0 / $orphanNote / 版本一致($skillVer) / 无残留英文路径$orderNote"
  if($warns.Count){ Write-Output ""; Write-Output "⚠️ 警告 $warnGroups 组（不阻断）："; $warns | ForEach-Object { Write-Output $_ } }
  exit 0
}
