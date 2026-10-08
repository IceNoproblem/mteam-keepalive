#!/bin/bash
# mteam-keepalive · 卸载定时任务（保留配置与数据）
set -uo pipefail

LABEL="com.keepalive.mteam.visit"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; then
    say "已卸载运行中的任务"
else
    say "任务未在运行，跳过 bootout"
fi

if [ -f "$PLIST" ]; then
    rm -f "$PLIST"
    say "已删除 $PLIST"
fi

say "配置、日志与浏览器 profile 均保留在项目目录，重新install.sh 即可恢复"
say "如需彻底清除，请手动删除项目目录"