#!/usr/bin/perl
# ============================================================
# 全能小说作家 - 正文分布统计脚本（perl 版，与 fingerprint.py / fingerprint.ps1 同口径）
# 用途：无 python/pwsh 但有 perl 的环境（macOS/Unix/常用 shell）跑分布统计。
# 用法: perl fingerprint.pl <正文.txt>
# 输出: 句长/段长/对话占比/标点/修饰密度分布
# ============================================================
use strict; use warnings;
use utf8;
binmode(STDOUT, ":encoding(UTF-8)");

my $path = shift or die "用法: perl fingerprint.pl <正文.txt>\n";
open(my $fh, "<:encoding(UTF-8)", $path) or die "无法打开 $path: $!\n";
my $t = do { local $/; <$fh> };
close($fh);
my $total = length($t);
die "文件为空\n" if $total == 0;

# 段落（按换行切）
my @paras;
for my $line (split(/\r?\n/, $t)) {
    $line =~ s/^\s+|\s+$//g;
    push @paras, $line if length($line) > 0;
}
my @pl = sort { $a <=> $b } map { length($_) } @paras;
my $pAvg = int(0.5 + (eval join "+", @pl) / @pl);
my $pMed = $pl[int($#pl/2)];
my ($p50, $p100) = (0,0);
$p50++ for grep { $_ <= 50 } @pl; $p100++ for grep { $_ <= 100 } @pl;
$p50 = sprintf("%.1f", 100.0*$p50/@pl); $p100 = sprintf("%.1f", 100.0*$p100/@pl);

# 句子（按中文句末标点切；口径同 fingerprint.py 的 findall：每段非句末标点串后接至多一个句末标点）
(my $raw = $t) =~ s/\r?\n//g;
my (@sents, @sl, @cl);
while ($raw =~ /([^。！？!?…；;]+)([。！？!?…；;]?)/g) {
    my $s = $1 . $2;
    $s =~ s/^\s+|\s+$//g;
    push @sents, $s if length($s) > 0 && length($s) < 300;
}
@sl = sort { $a <=> $b } map { length($_) } @sents;
my $sSum = 0; $sSum += $_ for @sl;
my $sAvg = sprintf("%.1f", $sSum/@sl); my $sMed = $sl[int($#sl/2)];
my ($s12, $s20) = (0,0);
$s12++ for grep { $_ <= 12 } @sl; $s20++ for grep { $_ <= 20 } @sl;
$s12 = sprintf("%.1f", 100.0*$s12/@sl); $s20 = sprintf("%.1f", 100.0*$s20/@sl);
my $sMean = $sSum/@sl; my $sVar = 0;
$sVar += ($_-$sMean)**2 for @sl;
$sVar = sprintf("%.1f", $sVar/@sl);

# 逗号间小节（句内节奏；同 python：逗号/顿号作切点，段末至多跟一个逗号/顿号）
while ($raw =~ /([^，。！？!?…；;、]+)([，、]?)/g) {
    my $c = $1 . $2;
    $c =~ s/^\s+|\s+$//g;
    push @cl, length($c) if length($c) > 0;
}
my $cSum = 0; $cSum += $_ for @cl;
my $cAvg = sprintf("%.1f", $cSum/@cl);

# 对话占比（引号内字符，$& 取整段匹配；两组引号均可）
my $dqSum = 0; $dqSum += length($&) while $raw =~ /“[^”]*”|「[^」]*」/g;
my $dqPct = sprintf("%.1f", 100.0*$dqSum/$total);

# 每千字
my $totalK = $total/1000.0;
my ($excl, $quest, $ellip, $de) = (0,0,0,0);
$excl++ while $raw =~ /！/g; $quest++ while $raw =~ /？/g;
$ellip++ while $raw =~ /……/g; $de++ while $raw =~ /的/g;

print "总字数=$total 段落=".scalar(@pl)." 句子=".scalar(@sl)."\n";
print "段长: 均值=$pAvg 中位=$pMed | <=50字=$p50% <=100字=$p100%\n";
print "句长: 均值=$sAvg 中位=$sMed | <=12字=$s12% <=20字=$s20% 方差=$sVar\n";
print "逗号间小节均值=$cAvg 对话占比=$dqPct%\n";
printf "每千字: ！=%.1f ？=%.1f ……=%.1f 的=%.1f\n", $excl/$totalK, $quest/$totalK, $ellip/$totalK, $de/$totalK;
