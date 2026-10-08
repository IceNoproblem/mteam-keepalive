#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mteam-keepalive · 主控脚本

每天定时用无头浏览器登录目标PT 站点并随机漫游若干页面，
避免因长期未登录触发站点的闲置账号清理机制。

流程：
  自检 → 同步日常浏览器的登录态 → 浏览器登录（含邮箱2FA）→ 随机漫游 → 出报告并发信

依赖：仅Python 标准库（IMAP/SMTP/JSON/subprocess），无需pip 安装任何东西。
"""
import os
import sys
import re
import json
import time
import random
import glob
import subprocess
import threading
import traceback
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import mail_tool

LOGDIR = os.path.join(BASE, "logs")
SHOTDIR = os.path.join(BASE, "shots")
PROFILE = os.path.join(BASE, "profile")


def _find_browser():
    """自动探测本机的 Chromium 系浏览器，避免路径写死导致升级后失效。
    优先使用日常在用的浏览器 —— 因为登录态同步依赖它。"""
    import glob
    patterns = [
        "/Applications/Microsoft Edge*.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome*.app/Contents/MacOS/Google Chrome",
        "~/Applications/Microsoft Edge*.app/Contents/MacOS/Microsoft Edge",
        "~/Applications/Google Chrome*.app/Contents/MacOS/Google Chrome",
    ]
    for p in patterns:
        for c in sorted(glob.glob(os.path.expanduser(p))):
            if os.path.exists(c):
                return c
    return None


BROWSER = _find_browser()
NODE_MODULES = os.path.join(BASE, "node_modules")
NODE_BIN = os.path.join(BASE, "runtime", "node")
RUNNER = os.path.join(BASE, "browser_runner.cjs")

for d in (LOGDIR, SHOTDIR, PROFILE):
    os.makedirs(d, exist_ok=True)

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOGFILE = os.path.join(LOGDIR, "run-%s.log" % STAMP)
STATS = {"visited": [], "errors": [], "login_ok": False, "retries": 0,
         "code_used": None, "duration": 0, "started": time.time()}


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _scrub(text):
    """把绝对路径压成文件名，避免本机用户名 / 目录结构出现在报告或邮件里。"""
    s = str(text)
    s = s.replace(BASE + "/", "").replace(BASE, ".")
    return re.sub(r"(/[^\s:,'\"()]+)+/([\w.\-]+)", r"\2", s)


def read_cfg():
    """读取配置；高级项（_advanced）自动展开到顶层，简化调用方逻辑。"""
    p = os.path.join(BASE, "config.json")
    if not os.path.exists(p):
        raise SystemExit("缺少 config.json，请先执行：cp config.example.json config.json 并填写")
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    adv = cfg.pop("_advanced", None) or {}
    for k, v in adv.items():
        if k.startswith("_"):        # 保留 _note 这类说明字段但不覆盖业务配置
            continue
        cfg.setdefault(k, v)
    return cfg


def preflight(cfg):
    """执行前自检：把能提前发现的故障挡在浏览器启动之前。
    原则：致命项（缺配置/缺依赖）让任务失败并报警；
         非致命项（代理不通/站点抖动）降级继续，不浪费当天机会。"""
    log("=== 自检开始 ===")
    ok = True

    # 1 必填凭据
    missing = [k for k in ("site_user", "site_pass", "site_url",
                           "mail_user", "mail_auth_code") if not cfg.get(k)]
    if missing:
        log("[自检] 致命：config.json 缺少字段: %s" % ", ".join(missing))
        ok = False
    else:
        log("[自检] 凭据字段齐备")

    # 2 浏览器
    if not BROWSER or not os.path.exists(BROWSER):
        log("[自检] 致命：未找到 Chromium 系浏览器（Edge / Chrome）")
        ok = False
    else:
        log("[自检] 浏览器: %s" % os.path.basename(BROWSER))

    # 3 node 运行时
    if not os.path.exists(NODE_BIN):
        log("[自检] 致命：缺少私有 node 运行时（runtime/node），"
            "请运行 ./install.sh 自动下载，或手动放置")
        ok = False
    else:
        log("[自检] node 运行时就绪")

    # 4 puppeteer-core
    if not os.path.exists(os.path.join(NODE_MODULES, "puppeteer-core")):
        log("[自检] 致命：缺少 puppeteer-core，请运行 ./install.sh")
        ok = False
    else:
        log("[自检] puppeteer-core 就绪")

    # 5 代理连通性（不通则降级直连）
    px = cfg.get("proxy", "")
    if px:
        hostport = px.replace("http://", "").replace("https://", "")
        try:
            r = subprocess.run(["nc", "-z", "-G", "3"] + hostport.split(":"),
                               capture_output=True, timeout=8)
            if r.returncode != 0:
                log("[自检] 警告：代理 %s 不通，本次尝试直连" % px)
                cfg["proxy"] = ""
            else:
                log("[自检] 代理 %s 可用" % px)
        except Exception as e:
            log("[自检] 代理检测异常(%s)，本次尝试直连" % e.__class__.__name__)
            cfg["proxy"] = ""

    # 6 站点可达性（仅记录，不作失败判据：登录页返302 也算正常）
    env = dict(os.environ)
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("ALL_PROXY", None)
    if cfg.get("proxy"):
        env["HTTPS_PROXY"] = env["HTTP_PROXY"] = cfg["proxy"]
    env["NO_PROXY"] = ""
    try:
        r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                            "--max-time", "20", "-L", cfg["site_url"]],
                           capture_output=True, text=True, env=env, timeout=30)
        code = r.stdout.strip()
        if code == "000":
            log("[自检] 站点探测失败，浏览器阶段会重试")
        else:
            log("[自检] 站点可达 HTTP %s" % code)
    except Exception as e:
        log("[自检] 站点探测异常: %s（浏览器阶段会重试）" % e)

    log("=== 自检结束 ok=%s ===" % ok)
    return ok


def run_browser(cfg, deadline):
    """调用 Node + 无头浏览器执行登录与漫游。

    浏览器进入 2FA 页面后会等待一个验证码文件；本函数同时开后台线程
    持续从邮箱取码写入该文件，实现同一浏览器会话内完成 2FA
    （重开浏览器会导致验证码作废，这点很关键）。"""
    pj = os.path.join(BASE, ".payload.json")
    code_file = pj + ".code"
    payload = {
        "browser": BROWSER, "profile": PROFILE, "proxy": cfg.get("proxy", ""),
        "headless": cfg.get("headless", True),
        "url": cfg["site_url"], "user": cfg["site_user"], "pass": cfg["site_pass"],
        "email": cfg["mail_user"], "code": STATS.get("code_used"),
        "deadline": deadline, "shotdir": SHOTDIR,
        "minPages": cfg["min_pages"], "maxPages": cfg["max_pages"],
        "minMs": cfg["min_seconds"] * 1000, "maxMs": cfg["max_seconds"] * 1000,
        "payloadPath": pj,
        "loginPath": cfg.get("login_path", "/login"),
        "siteBase": cfg.get("site_base", ""),
        "pages": cfg.get("pages", []),
    }
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    # 清掉上一轮残留的验证码
    try:
        os.remove(code_file)
    except OSError:
        pass

    log("[browser] 启动浏览器自动化")
    proc = subprocess.Popen([NODE_BIN, RUNNER, pj],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)

    def watch_code():
        """仅在浏览器真的走到 2FA 页面时才起作用。
        若登录态已同步，浏览器会直接跳过登录，此线程空转退出。"""
        since = int(time.time()) - 60
        deadline_ts = time.time() + 240
        n = 0
        # 先等 25s：登录态有效时浏览器此时已在漫游了
        time.sleep(25)
        while time.time() < deadline_ts:
            if proc.poll() is not None:
                return
            if os.path.exists(code_file):
                return
            n += 1
            time.sleep(15)
            if proc.poll() is not None:
                return
            if os.path.exists(code_file):
                return
            code = mail_tool.fetch_code(since_ts=since, log=log)
            if code:
                try:
                    with open(code_file, "w") as f:
                        f.write(code)
                    STATS["code_used"] = code
                    log("[2fa] 验证码已送达浏览器")
                    return
                except Exception as e:
                    log("[2fa] 写入验证码文件失败: %s" % e)
                    return
            log("[2fa] 第 %d 次查询未收到，等待重试" % n)

    t = threading.Thread(target=watch_code, daemon=True)
    t.start()

    try:
        out, _ = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        STATS["errors"].append("浏览器执行超时")
    out = out or ""
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(out + "\n")

    res = None
    for line in out.splitlines():
        if line.startswith("__RESULT__"):
            try:
                res = json.loads(line[len("__RESULT__"):])
            except Exception:
                pass
    if not res:
        STATS["errors"].append("未获得浏览器结果输出")
        return "error"

    STATS["visited"] = res.get("visited", [])
    STATS["login_ok"] = res.get("login_ok", False)
    if res.get("usedCode"):
        STATS["code_used"] = res["usedCode"]
    if res.get("error"):
        STATS["errors"].append(res["error"])
        return "error"
    if res.get("login_ok"):
        return "ok"
    if res.get("code_needed"):
        return "need_code"
    return "error"


def build_report(cfg, result_state):
    dur = int(STATS["duration"])
    L = []
    L.append("Keepalive 每日访问任务报告")
    L.append("=" * 40)
    L.append("站点: %s" % cfg["site_url"])
    L.append("执行时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("耗时: %d 分 %d 秒" % (dur // 60, dur % 60))
    L.append("最终状态: %s" % result_state)
    L.append("登录成功: %s" % ("是" if STATS["login_ok"] else "否"))
    L.append("登录方式: %s" % ("复用已同步登录态（免验证码）"
                              if STATS["login_ok"] and not STATS.get("code_used")
                              else "账号密码 + 邮箱验证码"))
    L.append("重试次数: %d" % STATS["retries"])
    L.append("")
    L.append("访问页面 (%d 个):" % len(STATS["visited"]))
    for i, v in enumerate(STATS["visited"], 1):
        L.append("  %2d. %s" % (i, v))
    L.append("")
    if STATS["errors"]:
        L.append("异常记录:")
        for e in STATS["errors"]:
            L.append("  - %s" % _scrub(e))
        if any("2FA" in e or "验证码" in e for e in STATS["errors"]):
            L.append("")
            L.append("【已知问题】目标站点对频繁请求邮箱验证码有静默限流：")
            L.append("  接口返回 200 但邮件不投递。需要间隔较长时间后重试。")
    else:
        L.append("异常记录: 无")
    L.append("")
    L.append("说明: 本次为自动访问浏览任务，不执行任何下载操作。")
    return "\n".join(L)


def sync_auth_if_needed():
    """任务开始前尝试把日常浏览器的登录态同步过来，实现免验证码直连。
    会话令牌通常有有效期，过期后本步骤自动失效，
    届时会退回『账号密码 + 邮箱验证码』流程。"""
    script = os.path.join(BASE, "sync_auth.py")
    try:
        # 显式用当前解释器执行，不依赖脚本自身的可执行位与 shebang
        r = subprocess.run([sys.executable, script],
                           capture_output=True, text=True, timeout=90)
        out = (r.stdout or "") + (r.stderr or "")
        if "找到 auth JWT" in out and "已复制到自动化 profile" in out:
            log("[sync] 登录态已同步（免验证码）")
            return True
        log("[sync] 未找到可同步的登录态，将走账号密码 + 邮箱验证码流程")
        return False
    except Exception as e:
        log("[sync] 同步登录态异常: %s" % e)
        return False


def main():
    cfg = read_cfg()
    log("任务启动 · %s" % RUNNER)

    if not preflight(cfg):
        log("自检未通过，但仍尝试执行（以便收集错误报告并告警）")

    sync_auth_if_needed()

    total_pages = random.randint(cfg["min_pages"], cfg["max_pages"])
    total_ms = random.randint(cfg["min_seconds"], cfg["max_seconds"]) * 1000
    log("本次计划: %d 个页面 / %d 秒" % (total_pages, total_ms // 1000))

    state = "failed"
    for attempt in range(1, cfg.get("max_retry", 3) + 1):
        STATS["retries"] = attempt
        log("--- 第 %d 次尝试 ---" % attempt)
        try:
            r = run_browser(cfg, time.time() + 400)
            if r == "ok":
                state = "ok"
            else:
                state = "failed"
                if r == "need_code":
                    STATS["errors"].append("2FA 验证码获取失败或超时")
        except subprocess.TimeoutExpired:
            STATS["errors"].append("浏览器执行超时")
            state = "failed"
        except Exception as e:
            STATS["errors"].append(_scrub("%s: %s" % (type(e).__name__, e)))
            log("[error] %s" % traceback.format_exc())
            state = "failed"

        if state == "ok":
            break
        if attempt < cfg.get("max_retry", 3):
            # 站点发码限流：指数退避
            cooldown = 300 * attempt
            log("[retry] 冷却 %d 秒后重试" % cooldown)
            time.sleep(cooldown)

    STATS["duration"] = time.time() - STATS["started"]
    log("任务结束 · 状态=%s · 页面 %d 个 · 耗时 %d 秒"
        % (state, len(STATS["visited"]), STATS["duration"]))

    report = build_report(cfg, state)
    rf = os.path.join(LOGDIR, "report-%s.txt" % STAMP)
    with open(rf, "w", encoding="utf-8") as f:
        f.write(report)
    log("报告已保存: %s" % rf)

    if cfg.get("mail_auth_code"):
        subj = "[Keepalive] %s - 访问%d页 - %s" % (
            datetime.now().strftime("%m-%d %H:%M"), len(STATS["visited"]),
            "成功" if state == "ok" else "异常")
        if not mail_tool.send_report(subj, report, log=log):
            log("[mail] 报告发送失败，已留本地文件")
    else:
        log("[mail] 未配置授权码，跳过邮件发送")

    print("__REPORT__" + rf)
    return 0 if state == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())