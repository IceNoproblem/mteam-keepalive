#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同步登录态：把日常浏览器的 localStorage（含 JWT 会话令牌）
热复制到自动化专用 profile，实现免验证码、免登录直连。

原理：目标站点把 JWT 存在 localStorage 而非 cookie，
      因此复制 Local Storage 目录即可携带有效会话。

用法:
  ./sync_auth.py           # 同步并校验
  ./sync_auth.py --check   # 只检查不修改
"""
import os
import sys
import json
import glob
import time
import shutil
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
DST_PROFILE = os.path.join(BASE, "profile")
DST_LS = os.path.join(DST_PROFILE, "Default", "Local Storage")


def load_cfg():
    p = os.path.join(BASE, "config.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def find_edge_dir():
    """定位日常浏览器的用户数据目录（优先与主控脚本同一个浏览器）"""
    home = os.path.expanduser("~")
    cands = [
        os.path.join(home, "Library/Application Support/Microsoft Edge"),
        os.path.join(home, "Library/Application Support/Google/Chrome"),
    ]
    for d in cands:
        if os.path.isdir(os.path.join(d, "Default", "Local Storage", "leveldb")):
            return d
    return None


def log(m):
    print(m, flush=True)


def find_auth_token(src_ls):
    """从浏览器 Local Storage 的 leveldb 中提取 JWT令牌。

    JWT 形如eyJ...<header>.<payload>.<signature>，
    在 leveldb 中以 key 'auth' 附近的 value 形式存在。
    """
    ldb_dir = os.path.join(src_ls, "leveldb")
    if not os.path.isdir(ldb_dir):
        return None, None
    for f in glob.glob(os.path.join(ldb_dir, "*.ldb")) + \
             glob.glob(os.path.join(ldb_dir, "*.log")):
        try:
            with open(f, "rb") as fh:
                data = fh.read()
        except Exception:
            continue
        idx = data.find(b"auth")
        while idx != -1:
            seg = data[idx:idx + 1400]
            j = seg.find(b"eyJ")
            if j != -1:
                token = seg[j:j + 900]
                end = token.find(b"\x00")
                if end > 0:
                    token = token[:end]
                try:
                    s = token.decode("ascii", "ignore")
                    parts = s.split(".")
                    if len(parts) >= 3 and len(parts[0]) > 10:
                        cand = ".".join(parts[:3])
                        if cand.startswith("eyJ") and len(cand) > 60:
                            return cand, f
                except Exception:
                    pass
            idx = data.find(b"auth", idx + 1)
    return None, None


def main():
    check_only = "--check" in sys.argv
    cfg = load_cfg()
    site_url = cfg.get("site_url", "目标站点")

    log("=== 登录态同步（localStorage JWT 方案）===")
    edge_dir = find_edge_dir()
    if not edge_dir:
        log("")
        log("❌ 未找到日常浏览器的 Local Storage 目录")
        log("请确认 Edge / Chrome 已正常启动过一次。")
        return 1
    src_ls = os.path.join(edge_dir, "Default", "Local Storage")
    log("浏览器数据目录: %s" % edge_dir)

    jwt, src = find_auth_token(src_ls)
    if not jwt:
        log("")
        log("❌ 未在Local Storage 中找到 auth JWT")
        log("")
        log("请先在日常浏览器中登录一次站点：")
        log("  1. 打开 %s" % site_url)
        log("  2. 账号密码 + 邮箱验证码完成登录")
        log("  3. 停留在首页后重跑本脚本")
        return 1

    log("✅ 找到 auth JWT（长度 %d，来源 %s）"
        % (len(jwt), os.path.basename(src) if src else "?"))
    if check_only:
        return 0

    # 停掉占用该 profile 的自动化浏览器，避免拷贝时文件被锁
    subprocess.run(["pkill", "-f", os.path.join(BASE, "profile")], capture_output=True)
    time.sleep(1)

    if os.path.exists(DST_LS):
        shutil.rmtree(DST_LS)
    os.makedirs(os.path.dirname(DST_LS), exist_ok=True)
    shutil.copytree(src_ls, DST_LS)
    log("✅ Local Storage 已复制到自动化 profile")

    # Local State 存放加密密钥来源，必须一并同步，否则令牌读不出来
    ls_dst = os.path.join(DST_PROFILE, "Local State")
    src_ls_state = os.path.join(edge_dir, "Local State")
    if os.path.exists(src_ls_state):
        shutil.copy(src_ls_state, ls_dst)
        log("✅ Local State 已同步")

    # 同步 Preferences：UA / 语言与日常一致，降低风控命中率
    for sub in ("Preferences",):
        s = os.path.join(edge_dir, "Default", sub)
        d = os.path.join(DST_PROFILE, "Default", sub)
        if os.path.exists(s):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy(s, d)

    # 校验目标里确实带上了 JWT
    found = False
    for f in glob.glob(os.path.join(DST_LS, "leveldb", "*.ldb")) + \
             glob.glob(os.path.join(DST_LS, "leveldb", "*.log")):
        try:
            with open(f, "rb") as fh:
                if b"eyJ" in fh.read():
                    found = True
                    break
        except Exception:
            pass
    log("✅ 目标 profile 校验: JWT 存在 = %s" % found)
    log("")
    log("下一步：运行 ./run_task.sh 验证免验证码直连")
    return 0


if __name__ == "__main__":
    sys.exit(main())