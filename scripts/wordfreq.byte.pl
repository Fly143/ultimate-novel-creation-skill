#!/usr/bin/perl
# ============================================================
# 全能小说作家 - AI味词频率统计（perl 字节级零依赖版）
# 适用：任何 perl 5 核心环境（含缺 Encode/PerlIO 的精简沙箱），
#       不 use utf8、不用 :encoding，纯字节级 UTF-8 处理。
# 用途：写后检测「用词检查」——对照真人实测密度，找出超出人类水平的 AI 味词。
# 分档依据：真人样章每千字实测（高危 0-0.5、中频 0-1.3、正常 0.3-1.0），
#           单类 >1.5/千字 即超出真人水平；高危类真人几乎不用。
#
# 用法:
#   perl wordfreq.byte.pl <正文.txt>                          # 整文件
#   perl wordfreq.byte.pl <正文.txt> --skip-first              # 跳过第一行（标题）
#   perl wordfreq.byte.pl <正文.txt> --stop-at-dashes          # 遇 "----" 行截断（去掉元数据）
#
# 输出: 每类词每千字频次，超档线(>1.5)或高危类(>0.5)标记 ❌，其余按档位显示
# ============================================================
use strict; use warnings;

my $path = shift or die "用法: perl wordfreq.byte.pl <正文.txt> [--skip-first] [--stop-at-dashes]\n";
my ($skipFirst, $stopDashes) = (0, 0);
for (@ARGV) {
    $skipFirst = 1 if $_ eq "--skip-first";
    $stopDashes = 1 if $_ eq "--stop-at-dashes";
}

# ---- 字节级 UTF-8 序列常量 ----
my $CHAR = qr/[\x00-\x7F]|[\xC2-\xDF][\x80-\xBF]|[\xE0-\xEF][\x80-\xBF]{2}|[\xF0-\xF4][\x80-\xBF]{3}/;
my $WS = qr/(?:[ \t\x0B\x0C\r\n]|\xC2\xA0|\xE3\x80\x80)/;

# ---- 词表（与 references/高级写作技巧-整合版.md 铁律三分档一致）----
# 分档依据：真人样章每千字实测上限——高危类 ≤0.5（然而0.45/不禁0.46/瞬间0.27/宛如0.15/犹如0.38/仿佛0.22），
#           中频类 ≤1.3（微微1.34/淡淡1.34/似乎1.06/不由1.02/心中1.15/轻轻/缓缓/默默/悄悄/眼中/脸上），
#           正常类 0.3-1.0（突然/马上/一下/一些）
# 每行: 档位|词(UTF-8字节)|真人每千字上限
my @WORDSPEC = (
    ["高危", "\xE7\x84\xB6\xE8\x80\x8C", 0.5],      # 然而
    ["高危", "\xE4\xB8\x8D\xE7\xA6\x81", 0.5],      # 不禁
    ["高危", "\xE7\x9E\xAC\xE9\x97\xB4", 0.5],      # 瞬间
    ["高危", "\xE5\xAE\x9B\xE5\xA6\x82", 0.5],      # 宛如
    ["高危", "\xE7\x8A\xB9\xE5\xA6\x82", 0.5],      # 犹如
    ["高危", "\xE4\xBB\xBF\xE4\xBD\x9B", 0.5],      # 仿佛
    ["中频", "\xE5\xBE\xAE\xE5\xBE\xAE", 1.5],      # 微微
    ["中频", "\xE6\xB7\xA1\xE6\xB7\xA1", 1.5],      # 淡淡
    ["中频", "\xE8\xBD\xBB\xE8\xBD\xBB", 1.5],      # 轻轻
    ["中频", "\xE7\xBC\x93\xE7\xBC\x93", 1.5],      # 缓缓
    ["中频", "\xE9\xBB\x98\xE9\xBB\x98", 1.5],      # 默默
    ["中频", "\xE6\x82\x84\xE6\x82\x84", 1.5],      # 悄悄
    ["中频", "\xE5\xBF\x83\xE4\xB8\xAD", 1.5],      # 心中
    ["中频", "\xE7\x9C\xBC\xE4\xB8\xAD", 1.5],      # 眼中
    ["中频", "\xE8\x84\xB8\xE4\xB8\x8A", 1.5],      # 脸上
    ["中频", "\xE4\xBC\xBC\xE4\xB9\x8E", 1.5],      # 似乎
    ["中频", "\xE4\xB8\x8D\xE7\x94\xB1", 1.5],      # 不由
    ["正常", "\xE7\xAA\x81\xE7\x84\xB6", 99],       # 突然
    ["正常", "\xE9\xA9\xAC\xE4\xB8\x8A", 99],       # 马上
    ["正常", "\xE4\xB8\x80\xE4\xB8\x8B", 99],       # 一下
    ["正常", "\xE4\xB8\x80\xE4\xBA\x9B", 99],       # 一些
);

# ---- 读文件（原始字节）----
open(my $fh, "<", $path) or die "无法打开 $path: $!\n";
binmode($fh);
local $/;
my $t = <$fh>;
close($fh);
die "文件为空\n" unless length($t) > 0;

if ($stopDashes)  { $t =~ s/\r?\n----.*$//s; }
if ($skipFirst)   { $t =~ s/^[^\r\n]*\r?\n//; }

my $total = 0; $total++ while $t =~ /$CHAR/g;
my $totalK = $total/1000.0;
die "文件为空\n" if $total == 0;

# ---- 统计 ----
my @results;
for my $spec (@WORDSPEC) {
    my ($tier, $word, $cap) = @$spec;
    my $n = 0; $n++ while $t =~ /$word/g;
    my $perK = $n/$totalK;
    my $flag = "";
    $flag = " ❌" if ($tier eq "高危" && $perK > 0.5) || $perK > 1.5;
    push @results, { tier=>$tier, word=>$word, perK=>$perK, n=>$n, flag=>$flag };
}

# ---- 连环堆叠检测（★v9.5.7 实测修正）----
# 真人不是不用这些词，是"堆叠"才 AI 味。判定按句长加权 + 部位词降权：
#   堆叠 = 加权词数 ≥ max(3, 句字符数/15)
# 身体部位词（心中/眼中/脸上）真人大量用（金庸"眼中不禁湿润，微微叹气"很自然），权重 0.5；
# 副词/虚词类（微微/不禁/似乎/然而…）权重 1。加权后仍 ≥3 才标 ⚠️。
# 注：字节模式下不能用字符类 [。！？] 切句（多字节标点会被拆成单字节误伤），
#     用 CHAR 逐字符消费 + 句末标点字节序列判断句界。
my @WATCH_HI = map { $_->[1] } grep { $_->[0] eq "高危" } @WORDSPEC;   # 权重1
# 中频除身体词外权重1（身体词单独 0.5，避免双重计分）；身体词字节序列显式列出
my @WATCH_MID = map { $_->[1] } grep { $_->[0] eq "中频" } @WORDSPEC;
my @WATCH_BODY = ("\xE5\xBF\x83\xE4\xB8\xAD","\xE7\x9C\xBC\xE4\xB8\xAD","\xE8\x84\xB8\xE4\xB8\x8A"); # 心中/眼中/脸上 权重0.5
# 从 MID 中剔除身体词（字节序列相等比较），避免双重计分
@WATCH_MID = grep { my $w=$_; !grep { $w eq $_ } @WATCH_BODY } @WATCH_MID;
my $stack = 0;
my $stackSample = "";
my ($sbeg, $i) = (0, 0);
my @SENTEND = ("\xE3\x80\x82","\xEF\xBC\x81","\xEF\xBC\x9F","\xE2\x80\xA6","\xEF\xBC\x9B"); # 。！？…；
while ($t =~ /$CHAR/g) {
    my $ch = $&;
    my $isEnd = 0;
    for my $se (@SENTEND) { if ($ch eq $se) { $isEnd = 1; last; } }
    if ($isEnd || ($ch eq "\n") || ($ch eq ";" || $ch eq "!" || $ch eq "?")) {
        my $sent = substr($t, $sbeg, pos($t) - $sbeg - length($ch));
        $sbeg = pos($t);
        my $slen = 0; $slen++ while $sent =~ /$CHAR/g;
        my $score = 0;
        for my $w (@WATCH_HI)  { my $c = 0; $c++ while $sent =~ /$w/g; $score += $c; }
        for my $w (@WATCH_MID) { my $c = 0; $c++ while $sent =~ /$w/g; $score += $c; }
        for my $w (@WATCH_BODY){ my $c = 0; $c++ while $sent =~ /$w/g; $score += 0.5 * $c; }
        my $thresh = $slen / 15;
        $thresh = 3 if $thresh < 3;
        if ($score >= $thresh) {
            $stack++;
            if ($stackSample eq "") {
                $stackSample = $sent;
                $stackSample =~ s/^$WS+|$WS+$//g;
                $stackSample = substr($stackSample, 0, 40) . "…" if length($stackSample) > 40;
            }
        }
    }
}

print "总字数=$total\n";
for my $r (sort { $b->{perK} <=> $a->{perK} } @results) {
    printf "  [%s] %s: %.2f/千字 (%d次)%s\n", $r->{tier}, $r->{word}, $r->{perK}, $r->{n}, $r->{flag};
}
print "连环堆叠句: $stack 句" . ($stack ? " ⚠️ 示例: $stackSample" : " ✅") . "\n";
