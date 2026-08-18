# ============================================================
# 全能小说作家 - 正文分布统计脚本（去AI味客观指标）
# 用途：统计章节正文的语言分布特征，供写后流水线对照真人网文指纹判断 AI 腔。
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File fingerprint.ps1 -Path "正文.txt"     (Windows)
#   pwsh -File fingerprint.ps1 -Path "正文.txt"                                              (跨平台)
# 输出：句长/段长/对话占比/标点/修饰密度等分布数值。
# 判定参照：references/高级写作技巧-整合版.md「真人网文统计指纹」；
#          设定了文风的书对照 [书名]_文风设定.md 的文风指纹。
# ============================================================
param(
    [Parameter(Mandatory=$true)][string]$Path,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
$enc = New-Object System.Text.UTF8Encoding($false)
if(-not (Test-Path -LiteralPath $Path)){ Write-Error "文件不存在: $Path"; exit 1 }
$t = [System.IO.File]::ReadAllText($Path, $enc)
$total = $t.Length
if($total -eq 0){ Write-Error "文件为空"; exit 1 }

# 段落（按换行切）
$paras = @($t -split "`r?`n" | Where-Object { $_.Trim().Length -gt 0 })
$pl = @($paras | ForEach-Object { $_.Trim().Length })
$plSorted = $pl | Sort-Object
$pAvg = [math]::Round(($pl | Measure-Object -Average).Average,0)
$pMed = $plSorted[[math]::Floor($plSorted.Count/2)]
$p50 = [math]::Round(100.0 * ($pl | Where-Object { $_ -le 50 }).Count / [math]::Max(1,$pl.Count),1)
$p100 = [math]::Round(100.0 * ($pl | Where-Object { $_ -le 100 }).Count / [math]::Max(1,$pl.Count),1)

# 句子（按中文句末标点切）
$raw = $t -replace "`r?`n",""
$sents = @([regex]::Matches($raw, '[^。！？!?…；;]+[。！？!?…；;]?') | ForEach-Object { $_.Value.Trim() } | Where-Object { $_.Length -gt 0 -and $_.Length -lt 300 })
$sl = @($sents | ForEach-Object { $_.Length })
$slSorted = $sl | Sort-Object
$sAvg = [math]::Round(($sl | Measure-Object -Average).Average,1)
$sMed = $slSorted[[math]::Floor($slSorted.Count/2)]
$s12 = [math]::Round(100.0 * ($sl | Where-Object { $_ -le 12 }).Count / [math]::Max(1,$sl.Count),1)
$s20 = [math]::Round(100.0 * ($sl | Where-Object { $_ -le 20 }).Count / [math]::Max(1,$sl.Count),1)
$sMean = ($sl | Measure-Object -Average).Average
$sVar = [math]::Round((($sl | ForEach-Object { ($_-$sMean)*($_-$sMean) }) | Measure-Object -Average).Average,1)

# 逗号间小节（句内节奏）
$cl = @([regex]::Matches($raw, '[^，。！？!?…；;、]+[，、]?') | ForEach-Object { $_.Value.Trim().Length } | Where-Object { $_ -gt 0 })
$cAvg = [math]::Round(($cl | Measure-Object -Average).Average,1)

# 对话占比（引号内字符）
$dq = [regex]::Matches($raw, '“[^”]*”|「[^」]*」')
$dqChars = ($dq | ForEach-Object { $_.Value.Length } | Measure-Object -Sum).Sum
$dqPct = [math]::Round(100.0 * $dqChars / $total,1)

# 每千字标点与修饰密度
$totalK = $total / 1000.0
$excl = ([regex]::Matches($raw, '！')).Count
$quest = ([regex]::Matches($raw, '？')).Count
$ellip = ([regex]::Matches($raw, '……')).Count
$de = ([regex]::Matches($raw, '的')).Count

if($Json){
    [pscustomobject]@{
        总字数 = $total; 段落数 = $paras.Count; 句子数 = $sl.Count
        段长均值 = $pAvg; 段长中位 = $pMed; '段长<=50占比' = $p50; '段长<=100占比' = $p100
        句长均值 = $sAvg; 句长中位 = $sMed; '句长<=12占比' = $s12; '句长<=20占比' = $s20; 句长方差 = $sVar
        逗号间均值 = $cAvg; 对话占比 = $dqPct
        每千字叹号 = [math]::Round($excl/$totalK,1); 每千字问号 = [math]::Round($quest/$totalK,1)
        每千字省略号 = [math]::Round($ellip/$totalK,1); 每千字的 = [math]::Round($de/$totalK,1)
    } | ConvertTo-Json
} else {
    Write-Output "总字数=$total 段落=$($paras.Count) 句子=$($sl.Count)"
    Write-Output "段长: 均值=$pAvg 中位=$pMed | <=50字=$p50% <=100字=$p100%"
    Write-Output "句长: 均值=$sAvg 中位=$sMed | <=12字=$s12% <=20字=$s20% 方差=$sVar"
    Write-Output "逗号间小节均值=$cAvg 对话占比=$dqPct%"
    Write-Output "每千字: ！=$([math]::Round($excl/$totalK,1)) ？=$([math]::Round($quest/$totalK,1)) ……=$([math]::Round($ellip/$totalK,1)) 的=$([math]::Round($de/$totalK,1))"
}
