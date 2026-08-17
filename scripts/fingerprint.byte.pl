#!/usr/bin/perl
# ============================================================
# 全能小说作家 - 正文分布统计脚本（perl 字节级零依赖版）
# 适用：缺 Encode/PerlIO 的精简 perl 环境（如 mawk+精简perl 沙箱），
#       不依赖任何非核心模块，纯字节级 UTF-8 处理。
# 口径与 fingerprint.pl / fingerprint.py / fingerprint.ps1 完全一致
# （总字数=全部字符；句长=句内字符数；方差=总体方差 Σ(x-μ)²/n）。
#
# 用法:
#   perl fingerprint.byte.pl <正文.txt>                                  # 整个文件（同三实现默认口径）
#   perl fingerprint.byte.pl <正文.txt> --skip-first                      # 跳过第一行（标题）
#   perl fingerprint.byte.pl <正文.txt> --stop-at-dashes                  # 遇 "----" 行截断（去掉元数据）
#   perl fingerprint.byte.pl <正文.txt> --skip-first --stop-at-dashes     # 正文口径：不含标题、不含元数据
#
# 说明: 本脚本不 use utf8、不使用 :encoding，一切按 UTF-8 字节序列匹配，
#       因此没有模块依赖，任何 perl 5 核心都能跑。
# ============================================================
use strict; use warnings;

my $path = shift or die "用法: perl fingerprint.byte.pl <正文.txt> [--skip-first] [--stop-at-dashes]\n";
my ($skipFirst, $stopDashes) = (0, 0);
for (@ARGV) {
    $skipFirst = 1 if $_ eq "--skip-first";
    $stopDashes = 1 if $_ eq "--stop-at-dashes";
}

# ---- 字节级 UTF-8 序列常量（\x 转义，源码为纯 ASCII，避免任何编码问题）----
# 任意一个 UTF-8 字符：ASCII 1字节 | 2字节 | 3字节 | 4字节
my $CHAR = qr/[\x00-\x7F]|[\xC2-\xDF][\x80-\xBF]|[\xE0-\xEF][\x80-\xBF]{2}|[\xF0-\xF4][\x80-\xBF]{3}/;
# 句末标点（。！？…；;!?）
my $TERM = qr/\xE3\x80\x82|\xEF\xBC\x81|\xEF\xBC\x9F|\xE2\x80\xA6|\xEF\xBC\x9B|;|!|\?/;
# 逗号/顿号（逗号间小节切点）
my $COMMA = qr/\xEF\xBC\x8C|\xE3\x80\x81/;
# 逗号间小节切点 = 句末标点 + 逗号 + 顿号（与 fingerprint.py 的 [，。！？!?…；;、] 一致）
my $SEPALL = qr/$TERM|$COMMA/;
# 空白：ASCII 空白 + NBSP(\xC2\xA0) + 全角空格(\xE3\x80\x80)
my $WS = qr/(?:[ \t\x0B\x0C\r\n]|\xC2\xA0|\xE3\x80\x80)/;

sub charcount { my $s = shift; my $n = 0; $n++ while $s =~ /$CHAR/g; return $n; }
# 消费「若干完整字符，且不以 SEP 开头」——按字符边界切，绝不在多字节字符中间切开
sub nonsep_run { my $SEP = shift; return qr/(?:(?!$SEP)$CHAR)+/; }

# ---- 读文件（原始字节，不解码）----
open(my $fh, "<", $path) or die "无法打开 $path: $!\n";
binmode($fh);
local $/;
my $t = <$fh>;
close($fh);
die "文件为空\n" unless length($t) > 0;

# ---- 可选截断/跳标题（先截元数据再跳首行，顺序保证标题在元数据之前）----
if ($stopDashes)  { $t =~ s/\r?\n----.*$//s; }
if ($skipFirst)   { $t =~ s/^[^\r\n]*\r?\n//; }

my $total = charcount($t);                 # 总字符数（含标点，同 fingerprint.pl 的 length）
my $han = 0; $han++ while $t =~ /[\xE4-\xE9][\x80-\xBF][\x80-\xBF]/g;  # 汉字数（辅助参考）

# ---- 段落（按换行切）----
my @paras;
for my $line (split(/\r?\n/, $t)) {
    $line =~ s/^$WS+|$WS+$//g;
    push @paras, $line if length($line) > 0;
}
my @pl = sort { $a <=> $b } map { charcount($_) } @paras;
my $pSum = 0; $pSum += $_ for @pl;
my $pAvg = int(0.5 + $pSum/@pl);
my $pMed = $pl[int($#pl/2)];
my ($p50, $p100) = (0, 0);
$p50++ for grep { $_ <= 50 } @pl; $p100++ for grep { $_ <= 100 } @pl;
$p50 = sprintf("%.1f", 100.0*$p50/@pl); $p100 = sprintf("%.1f", 100.0*$p100/@pl);

# ---- 句子（findall 语义：非句末标点串 + 至多一个句末标点，同 fingerprint.pl）----
(my $raw = $t) =~ s/\r?\n//g;
my $NONSENT = nonsep_run($TERM);
my @sl;
while ($raw =~ /($NONSENT)($TERM)?/g) {
    my $s = $1 . (defined($2) ? $2 : "");
    $s =~ s/^$WS+|$WS+$//g;
    my $L = charcount($s);
    push @sl, $L if $L > 0 && $L < 300;
}
@sl = sort { $a <=> $b } @sl;
my $sSum = 0; $sSum += $_ for @sl;
my $sAvg = sprintf("%.1f", $sSum/@sl); my $sMed = $sl[int($#sl/2)];
my ($s12, $s20) = (0, 0);
$s12++ for grep { $_ <= 12 } @sl; $s20++ for grep { $_ <= 20 } @sl;
$s12 = sprintf("%.1f", 100.0*$s12/@sl); $s20 = sprintf("%.1f", 100.0*$s20/@sl);
my $sMean = $sSum/@sl; my $sVar = 0;
$sVar += ($_-$sMean)**2 for @sl;
$sVar = sprintf("%.1f", $sVar/@sl);

# ---- 逗号间小节（句内节奏：切点=句末标点+逗号+顿号，尾随至多一个逗号/顿号，同 fingerprint.pl）----
my $NONALL = nonsep_run($SEPALL);
my @cl;
while ($raw =~ /($NONALL)($COMMA)?/g) {
    my $c = $1 . (defined($2) ? $2 : "");
    $c =~ s/^$WS+|$WS+$//g;
    my $L = charcount($c);
    push @cl, $L if $L > 0;
}
my $cSum = 0; $cSum += $_ for @cl;
my $cAvg = sprintf("%.1f", $cSum/@cl);

# ---- 对话占比（“…”或「…」，含引号字符，同 fingerprint.pl 的 $&）----
my $dqSum = 0;
while ($raw =~ /\xE2\x80\x9C(?:(?!\xE2\x80\x9D).)*\xE2\x80\x9D|\xE3\x80\x8C(?:(?!\xE3\x80\x8D).)*\xE3\x80\x8D/g) {
    $dqSum += charcount($&);
}
my $dqPct = sprintf("%.1f", 100.0*$dqSum/$total);

# ---- 每千字标点与修饰密度 ----
my $totalK = $total/1000.0;
my ($excl, $quest, $ellip, $de) = (0, 0, 0, 0);
$excl++  while $raw =~ /\xEF\xBC\x81/g;          # ！
$quest++ while $raw =~ /\xEF\xBC\x9F/g;          # ？
$ellip++ while $raw =~ /\xE2\x80\xA6\xE2\x80\xA6/g;  # ……
$de++    while $raw =~ /\xE7\x9A\x84/g;          # 的

print "总字数=$total 汉字数=$han 段落=".scalar(@pl)." 句子=".scalar(@sl)."\n";
print "段长: 均值=$pAvg 中位=$pMed | <=50字=$p50% <=100字=$p100%\n";
print "句长: 均值=$sAvg 中位=$sMed | <=12字=$s12% <=20字=$s20% 方差=$sVar\n";
print "逗号间小节均值=$cAvg 对话占比=$dqPct%\n";
printf "每千字: ！=%.1f ？=%.1f ……=%.1f 的=%.1f\n", $excl/$totalK, $quest/$totalK, $ellip/$totalK, $de/$totalK;
