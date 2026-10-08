#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置助手：写入 / 查看邮箱授权码，自动收紧文件权限。

用法:
  ./set_auth_code.py# 查看当前是否已配置（不显示明文）
  ./set_auth_code.py <16位授权码> # 写入并 chmod 600
"""
import json
import os
import sys
import stat

P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def main():
    if len(sys.argv) < 2:
        if not os.path.exists(P):
            print("尚未创建 config.json，请先执行：")
            print("  cp config.example.json config.json")
            return 1
        with open(P, encoding="utf-8") as f:
            cur = json.load(f).get("mail_auth_code", "")
        print("当前授权码: %s" % ("已配置（%d 位）" % len(cur) if cur else "(未设置)"))
        print("用法: %s <授权码>" % os.path.basename(sys.argv[0]))
        return 0

    code = sys.argv[1].strip()
    if not os.path.exists(P):
        print("缺少 config.json，请先执行：cp config.example.json config.json")
        return 1
    with open(P, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["mail_auth_code"] = code

    # 原子写入，避免中途失败损坏配置
    tmp = P + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, P)
    os.chmod(P, 0o600)
    print("✅ 授权码已写入（%d 位），config.json 权限已设为 600" % len(code))
    print("下一步: ./run_task.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())