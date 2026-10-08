#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮箱工具：IMAP 拉取 2FA 验证码 + SMTP 发送每日报告。

只用标准库，兼容任意支持 IMAP/SMTP 的邮箱（QQ / 163 / Gmail 等）。

单独调试：
  python3 mail_tool.py fetch                # 拉取最新一封验证码邮件
  python3 mail_tool.py send <主题> <正文文件> # 发送报告
"""
import imaplib
import smtplib
import ssl
import re
import sys
import json
import time
import email
import os
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_cfg():
    """读取配置；高级项（_advanced）自动展开到顶层。"""
    with open(CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    adv = cfg.pop("_advanced", None) or {}
    for k, v in adv.items():
        if not k.startswith("_"):
            cfg.setdefault(k, v)
    return cfg


def _dec_header(raw):
    """解码 MIME 编码的邮件头（中文站点常见 base64/GBK 混编）"""
    if not raw:
        return ""
    out = []
    for part, enc in decode_header(raw):
        if isinstance(part, bytes):
            for cs in ("utf-8", "gbk", "gb18030", "latin-1"):
                try:
                    out.append(part.decode(cs))
                    break
                except Exception:
                    continue
            else:
                out.append(part.decode("utf-8", "ignore"))
        else:
            out.append(part)
    return "".join(out)


# 独立成组的6 位数字，避免匹配到长数字串里的一段
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def fetch_code(since_ts=None, log=print):
    """
    从邮箱抓取站点验证码。
    since_ts: 只接受该 Unix 时间戳之后收到的邮件（防止抓到旧码）
    返回 6 位字符串；失败返回 None
    """
    cfg = load_cfg()
    user = cfg["mail_user"]
    auth = cfg["mail_auth_code"]
    sender_kw = cfg.get("sender_keywords", ["m-team", "mteam"])
    ctx = ssl.create_default_context()

    M = None
    try:
        M = imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.qq.com"),
                               int(cfg.get("imap_port", 993)), ssl_context=ctx)
        M.login(user, auth)
        log("[mail] IMAP 登录成功")

        # 部分邮箱文件夹名含特殊字符，LIST 返回原始字节，这里逐个尝试选中
        folder = cfg.get("imap_folder", "INBOX")
        typ, _ = M.select(folder)
        if typ != "OK":
            log("[mail] 选中 %s 失败，改用 INBOX" % folder)
            typ, _ = M.select("INBOX")
        log("[mail] 已选中文件夹: %s" % folder)

        since_str = ""
        if since_ts:
            since_str = ' SINCE "%s"' % time.strftime(
                "%d-%b-%Y", time.localtime(since_ts - 86400))
        typ, data = M.search(None, "ALL" + since_str)
        if typ != "OK":
            typ, data = M.search(None, "ALL")
        ids = (data[0].split() if data and data[0] else [])
        # 只扫最近 30 封，控制单次执行时间
        ids = list(reversed(ids[-30:]))
        log("[mail] 扫描最近 %d 封邮件" % len(ids))

        # 收集 (邮件时间, 验证码) 候选，最后取最新的那个
        best = None  # (timestamp, code, subject)
        for mid in ids:
            typ, msgdata = M.fetch(mid, "(RFC822)")
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            msg = email.message_from_bytes(msgdata[0][1])
            subj = _dec_header(msg.get("Subject", ""))
            frm = _dec_header(msg.get("From", ""))
            low = (subj + " " + frm).lower()

            ts = 0
            try:
                dt = parsedate_to_datetime(msg.get("Date"))
                if dt:
                    ts = dt.timestamp()
            except Exception:
                ts = 0

            # 时间过滤 + 发件人/主题过滤
            if since_ts and ts and ts < since_ts - 60:
                continue
            if not any(k in low for k in sender_kw):
                continue

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() in ("text/plain", "text/html"):
                        try:
                            body += part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", "ignore")
                        except Exception:
                            pass
            else:
                try:
                    body = msg.get_payload(decode=True).decode(
                        msg.get_content_charset() or "utf-8", "ignore")
                except Exception:
                    pass

            # 剥掉 HTML 标签再匹配，避免数字被标签属性干扰
            text = re.sub(r"<[^>]+>", " ", body)
            m = CODE_RE.search(text) or CODE_RE.search(subj)
            if m and (best is None or ts > best[0]):
                best = (ts, m.group(1), subj)

        if best:
            log("[mail] 命中验证码（%s | %s）"
                % (time.strftime('%H:%M:%S', time.localtime(best[0])),
                   best[2][:40]))
            return best[1]
        log("[mail] 未找到时间窗口内的验证码邮件")
        return None
    except Exception as e:
        log("[mail] IMAP 异常: %s: %s" % (type(e).__name__, e))
        return None
    finally:
        if M:
            try:
                M.logout()
            except Exception:
                pass


def send_report(subject, body, log=print):
    """通过 SMTP 发送报告邮件"""
    cfg = load_cfg()
    user = cfg["mail_user"]
    auth = cfg["mail_auth_code"]
    to = cfg.get("report_to") or user
    ctx = ssl.create_default_context()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    try:
        s = smtplib.SMTP_SSL(cfg.get("smtp_host", "smtp.qq.com"),
                              int(cfg.get("smtp_port", 465)),
                              context=ctx, timeout=30)
        s.login(user, auth)
        s.sendmail(user, [to], msg.as_string())
        s.quit()
        log("[mail] 报告已发送")
        return True
    except Exception as e:
        log("[mail] SMTP 失败: %s: %s" % (type(e).__name__, e))
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "fetch":
        print(fetch_code())
    elif cmd == "send":
        subj = sys.argv[2] if len(sys.argv) > 2 else "Keepalive 任务报告"
        path = sys.argv[3] if len(sys.argv) > 3 else None
        txt = open(path, encoding="utf-8").read() if path else "test"
        print(send_report(subj, txt))