#!/bin/bash
# mteam-keepalive · launchd 入口
#
# 设计要点：
#  - 只用系统自带 python3（或本目录私有副本），不依赖任何特定发行版的运行时
#  - node 与 puppeteer 都在本目录 runtime/ 和 node_modules/ 下
#  - 脚本自身定位 BASE，换目录安装也能跑
#
# 休眠 / 重启适配：
#  - launchd 的 StartCalendarInterval 在机器睡眠时会「错过」，唤醒后补跑一次
#  - 本脚本做幂等保护：同一天只真正执行一次，避免重复访问
#  - 补跑窗口为当天 05:10 ~ 23:59，过了就顺延到次日
set -o pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$BASE/logs/launchd.log"
STAMP_DIR="$BASE/logs/.daily"
mkdir -p "$BASE/logs" "$STAMP_DIR"

TODAY="$(date '+%Y%m%d')"
STAMP="$STAMP_DIR/$TODAY"

# ---- 幂等保护：当天已成功执行过就跳过（只有成功才打标，失败留给下次重试）----
if [ -f "$STAMP" ]; then
    echo "===== 跳过 $(date '+%F %T') 当天已执行过 =====" >> "$LOG"
    exit 0
fi

# ---- 窗口判断：05:10 ~ 23:59 之间才执行 ----
NOW_H=$(date '+%H')
if [ "$NOW_H" -lt 5 ]; then
    echo "===== 跳过 $(date '+%F %T') 未到执行窗口(05:10起) =====" >> "$LOG"
    exit 0
fi

# ---- 选解释器：优先系统 python3，回退本目录私有副本 ----
if [ -x /usr/bin/python3 ]; then
    PY="/usr/bin/python3"
elif [ -x "$BASE/runtime/python3" ]; then
    PY="$BASE/runtime/python3"
else
    echo "===== 致命: 找不到 python3 解释器 $(date '+%F %T') =====" >> "$LOG"
    exit 1
fi

echo "===== 启动 $(date '+%Y-%m-%d %H:%M:%S') 解释器=$PY =====" >> "$LOG"
cd "$BASE" || exit 1

"$PY" "$BASE/mteam_visit.py" >> "$LOG" 2>&1
RC=$?

#依据主控输出的状态决定是否打标
LAST_REPORT=$(ls -t "$BASE"/logs/report-*.txt 2>/dev/null | head -1)
if [ -n "$LAST_REPORT" ] && grep -q "最终状态: ok" "$LAST_REPORT" 2>/dev/null; then
    date '+%F %T' > "$STAMP"
    echo "===== 打标成功 当天不再重复 =====" >> "$LOG"
else
    echo "===== 未成功 不打标 允许重试 =====" >> "$LOG"
fi

# 清理 7 天前的打标文件
find "$STAMP_DIR" -name "20*" -mtime +7 -delete 2>/dev/null

echo "===== 结束 $(date '+%Y-%m-%d %H:%M:%S') 退出码=$RC =====" >> "$LOG"
exit $RC