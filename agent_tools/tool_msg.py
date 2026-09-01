# -*- coding: utf-8 -*-
"""tool_msg —— P0-1 批量拆分（工具域模块）：📧 邮件与消息.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
引用 deepseek_client 的常量与辅助依赖加载顺序契约——主文件在共享基建全部定义后
才执行 `from agent_tools import *`，此处 from-import 可安全解析。
"""

import json
import os
import re
from datetime import datetime

import permissions

from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
from deepseek_client import (

    _agent_mail_run,
    _agent_mail_tip,
    _decrypt_secret,
    _http_client,
    _load_im_config,
    get_active_client,
    send_webhook_notify,
)



@tool(
        {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": "发送邮件（需要先在数据目录配置 email_config.json 的 SMTP 信息，未配置会提示）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "收件人邮箱"},
                        "subject": {"type": "string", "description": "邮件主题"},
                        "body": {"type": "string", "description": "邮件正文"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='发送邮件（SMTP）',
    preactivate=(('邮件', '发邮件', '收件箱'),),
)
def send_email(to, subject, body):
    """发送邮件：需要先配置 SMTP（email_config.json：smtp_host/smtp_port/user/password/from）。"""
    if not _dc.EMAIL_CONFIG_FILE or not os.path.exists(_dc.EMAIL_CONFIG_FILE):
        return (
            "错误：未配置邮件。请在数据目录创建 email_config.json，格式：\n"
            '{"smtp_host": "smtp.example.com", "smtp_port": 465, '
            '"user": "you@example.com", "password": "***", "from": "you@example.com"}'
        )
    try:
        with open(_dc.EMAIL_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict) and cfg.get("password"):
            cfg["password"] = _decrypt_secret(cfg["password"])
        smtp_host = str(cfg.get("smtp_host", "")).strip()
        smtp_port = int(cfg.get("smtp_port", 465))
        user = str(cfg.get("user", "")).strip()
        password = str(cfg.get("password", ""))
        from_addr = str(cfg.get("from") or user).strip()
        if not (smtp_host and user and password):
            return "错误：email_config.json 缺少 smtp_host / user / password"
        to = str(to or "").strip()
        # 严格校验：parseaddr + 无换行（CRLF 注入面），多收件人逗号分隔逐个校验
        import email.utils

        recipients = [r.strip() for r in to.split(",") if r.strip()]
        if not recipients or any(
            "@" not in email.utils.parseaddr(r)[1] or
            "\n" in r or "\r" in r
            for r in recipients
        ):
            return "错误：收件人邮箱格式不正确（支持逗号分隔多个地址）"
        to = ", ".join(recipients)
        import smtplib
        from email.header import Header
        from email.mime.text import MIMEText

        msg = MIMEText(str(body or ""), "plain", "utf-8")
        msg["Subject"] = Header(str(subject or ""), "utf-8")
        msg["From"] = from_addr
        msg["To"] = to
        try:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
        except Exception:
            # SSL 失败回退普通 SMTP+STARTTLS：465 端口是 SSL 专用，回退必须用 587
            fallback_port = 587 if smtp_port in (465, 0) else smtp_port
            server = smtplib.SMTP(smtp_host, fallback_port, timeout=10)
            try:
                server.starttls()
            except Exception:
                pass
        try:
            server.login(user, password)
            # sendmail 第二参必须是收件人列表：此前把逗号拼接串当单个收件人，
            # 多收件人时对方收不到信（rcpt 被当成一个非法地址）
            server.sendmail(from_addr, recipients, msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return f"邮件已发送至 {', '.join(recipients)}"
    except Exception as e:
        return f"错误：邮件发送失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "publish_draft",
                "description": "保存发布草稿到本地草稿箱（只建草稿不发布，发布权始终在用户手中）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "platform": {"type": "string", "description": "目标平台，如 公众号/博客/小红书"},
                        "title": {"type": "string", "description": "草稿标题"},
                        "content": {"type": "string", "description": "草稿正文"},
                    },
                    "required": ["platform", "title", "content"],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='发布草稿',
    preactivate=(('公众号', '公众号文章', '自动写作', '写公众号'), ('草稿', '草稿箱', '存草稿')),
)
def publish_draft(platform, title, content):
    """保存发布草稿到本地草稿箱（只建草稿不发布，双确认由审批流保证）。"""
    if not permissions.WORKSPACE_DIR:
        return "错误：工作区未初始化"
    drafts = os.path.join(permissions.WORKSPACE_DIR, "drafts")
    # 路径穿越防护：写前必须经权限模型判定（草稿箱必须在工作区内）
    ok, reason = permissions.check_filesystem(drafts, write=True)
    if not ok:
        return reason
    try:
        os.makedirs(drafts, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # platform 与 title 一律清洗，杜绝 '..' / 分隔符穿越
        safe_platform = re.sub(r'[\\/:*?"<>|]', "_", str(platform or "draft")).strip(" .")[:40] or "draft"
        safe = re.sub(r'[\\/:*?"<>|]', "_", str(title or "草稿"))[:40] or "草稿"
        path = os.path.join(drafts, f"{safe_platform}_{safe}_{ts}.md")
        # 二次兜底：规范化后必须仍位于草稿箱内
        if os.path.normpath(path) != path or not path.startswith(os.path.normpath(drafts) + os.sep):
            return f"错误：非法路径被拦截：{path}"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{content}")
        permissions.audit("publish_draft", path, safe_platform)
        return f"草稿已保存到本地草稿箱（未发布）：{path}\n正式发布请在平台后台操作。"
    except Exception as e:
        return f"错误：草稿保存失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "send_webhook",
                "description": "推送通知到配置的 Webhook（钉钉/ServerChan/Slack/通用，webhooks.json 配置）。适合任务完成提醒、定时巡检结果推送",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "可选：通知标题（默认 鲸语提醒）"},
                        "text": {"type": "string", "description": "通知正文"},
                        "channel": {"type": "string", "description": "可选：指定通道（dingtalk/serverchan/slack/generic），留空推送全部已配置通道"},
                    },
                    "required": ["text"],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='Webhook 推送（钉钉/ServerChan/Slack）',
    preactivate=(('发微信', '发企微', '发telegram', '推送消息', '消息推送', '通知我'),),
)
def send_webhook(title="", text="", channel=""):
    """主动推送通知到配置的 Webhook（钉钉/ServerChan/Slack/通用）。"""
    return send_webhook_notify(str(text or "").strip() or "（无内容）", str(title or "鲸语提醒"), channel)


@tool(
        {
            "type": "function",
            "function": {
                "name": "im_send",
                "description": "发送消息到 IM 通道（Telegram Bot / 企业微信群机器人，im_config.json 配置）。用于任务完成主动汇报、定时提醒。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "消息正文"},
                        "title": {"type": "string", "description": "可选：消息标题（默认 鲸语提醒）"},
                        "channel": {"type": "string", "description": "可选：指定通道 telegram/wecom，留空推送全部已配置通道"},
                    },
                    "required": ["text"],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='IM 消息（Telegram/企业微信）',
    preactivate=(('发微信', '发企微', '发telegram', '推送消息', '消息推送', '通知我'),),
)
def im_send(text, title="", channel=""):
    """主动触达：发送消息到 Telegram / 企业微信群机器人（可同时推送多通道）。"""
    if not str(text or "").strip():
        return "错误：text 必填"
    cfg, err = _load_im_config()
    if not cfg:
        return err
    title = str(title or "鲸语提醒").strip()
    body = f"{title}\n{text}" if title else str(text)
    ch = str(channel or "").strip().lower()
    targets = {}
    if not ch or ch == "telegram":
        if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
            targets["telegram"] = (cfg["telegram_bot_token"], str(cfg["telegram_chat_id"]))
    if not ch or ch in ("wecom", "wechat", "weixin"):
        if cfg.get("wecom_webhook"):
            targets["wecom"] = (cfg["wecom_webhook"],)
    if not targets:
        return "错误：未配置可用的 IM 通道（telegram_bot_token/telegram_chat_id 或 wecom_webhook）"
    sent = []
    for name, val in targets.items():
        try:
            if name == "telegram":
                token, chat_id = val
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                resp = _http_client().post(url, json={"chat_id": chat_id, "text": body[:4000]}, timeout=15)
            else:
                resp = _http_client().post(val[0], json={"msgtype": "text", "text": {"content": body[:4000]}}, timeout=15)
            ok = resp.status_code < 400
            sent.append(f"{name}:{'✅' if ok else '❌' + str(resp.status_code)}")
        except Exception as e:
            sent.append(f"{name}:❌ {e}")
    permissions.audit("im_send", ",".join(sent), body[:80], result="ok")
    return "；".join(sent)


@tool(
        {
            "type": "function",
            "function": {
                "name": "telegram_poll_updates",
                "description": "接收 Telegram Bot 新消息（长轮询，游标自动去重）。AI 可定期调用检查用户是否通过 Telegram 召唤/下达新指令。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timeout": {"type": "integer", "description": "可选：长轮询秒数 1-60，默认 15"},
                        "limit": {"type": "integer", "description": "可选：最多返回条数 1-20，默认 5"},
                    },
                    "required": [],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='轮询 Telegram 更新',
    preactivate=(('telegram消息', 'tg更新', 'tg消息', '远程指令'),),
)
def telegram_poll_updates(timeout=15, limit=5):
    """接收 Telegram 消息（供 AI 定期检查或用户召唤）。返回最近消息；游标自动前移去重。"""
    global _TELEGRAM_OFFSET
    cfg, err = _load_im_config()
    if not cfg:
        return err
    token = cfg.get("telegram_bot_token")
    chat_id = str(cfg.get("telegram_chat_id") or "").strip()
    if not token:
        return "未配置 telegram_bot_token（系统菜单 → IM 通道配置 可开启）"
    try:
        timeout = max(1, min(60, int(timeout or 15)))
        limit = max(1, min(20, int(limit or 5)))
    except (TypeError, ValueError):
        timeout, limit = 15, 5
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        params = {"timeout": timeout, "limit": limit}
        if _TELEGRAM_OFFSET:
            params["offset"] = _TELEGRAM_OFFSET + 1
        resp = _http_client().post(url, json=params, timeout=timeout + 15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"错误：Telegram 接收失败: {e}"
    updates = data.get("result") or []
    if not updates:
        return "（暂无新消息）"
    lines = []
    for u in updates:
        msg = u.get("message") or {}
        if chat_id and str(msg.get("chat", {}).get("id")) != chat_id:
            continue
        sender = (msg.get("from") or {}).get("username") or (msg.get("from") or {}).get("first_name") or "?"
        text = str(msg.get("text") or "[非文本消息]")[:500]
        lines.append(f"@{sender}: {text}")
        if int(u.get("update_id") or 0) > _TELEGRAM_OFFSET:
            _TELEGRAM_OFFSET = int(u["update_id"])
    if not lines:
        return "（暂无来自配置 chat_id 的新消息）"
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "read_email",
                "description": "读取邮箱近期邮件（IMAP，email_config.json 配置 imap 段）。隐私操作：读取邮箱内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "可选：最多返回封数（默认 10，最大 50）"},
                        "since_days": {"type": "integer", "description": "可选：最近 N 天（默认 3，0=全部）"},
                    },
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='读取邮件（IMAP）',
    preactivate=(('邮件', '发邮件', '收件箱'),),
)
def read_email(limit=10, since_days=3):
    """读取邮箱近期邮件（IMAP，email_config.json 配置 imap 段：host/port/user/password/ssl）。"""
    if not _dc.EMAIL_CONFIG_FILE or not os.path.exists(_dc.EMAIL_CONFIG_FILE):
        return "错误：未找到 email_config.json（需配置 imap 段）"
    try:
        with open(_dc.EMAIL_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        imap = cfg.get("imap") if isinstance(cfg, dict) else None
        if not isinstance(imap, dict):
            imap = {}  # 兼容扁平键格式：imap_host / imap_port / imap_user / imap_password / imap_ssl
        host = str(imap.get("host") or cfg.get("imap_host") or "")
        user = str(imap.get("user") or cfg.get("imap_user") or "")
        pwd = _decrypt_secret(str(imap.get("password") or cfg.get("imap_password") or ""))
        if not (host and user and pwd):
            return "错误：imap 配置不完整（host/user/password 必填）"
        try:
            lim = max(1, min(50, int(limit or 10)))
        except (TypeError, ValueError):
            lim = 10
        try:
            days = max(0, min(30, int(since_days or 3)))
        except (TypeError, ValueError):
            days = 3
        import imaplib
        from email.header import decode_header
        from email import message_from_bytes

        ssl_flag = imap.get("ssl", cfg.get("imap_ssl", "true"))
        if str(ssl_flag).lower() in ("true", "1", "yes"):
            conn = imaplib.IMAP4_SSL(host, int(imap.get("port") or cfg.get("imap_port") or 993), timeout=15)
        else:
            conn = imaplib.IMAP4(host, int(imap.get("port") or cfg.get("imap_port") or 143), timeout=15)
        try:
            conn.login(user, pwd)
            conn.select("INBOX")
            if days > 0:
                # IMAP SINCE 需要 d-MMM-yyyy（英文月份）。不能用 strftime("%b")：
                # 中文系统下输出"8月"导致服务器返回 BAD（真实 bug）
                import datetime as _dt

                _d = _dt.date.today() - _dt.timedelta(days=days)
                _months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
                since = f"{_d.day:02d}-{_months[_d.month - 1]}-{_d.year}"
                status, data = conn.search(None, "SINCE", since)
            else:
                status, data = conn.search(None, "ALL")
            ids = (data[0] or b"").split() if status == "OK" and data and data[0] else []
            ids = ids[-lim:]
            out = []

            def dec(v):
                parts = decode_header(str(v or ""))
                return "".join(
                    p.decode(ch or "utf-8", errors="replace") if isinstance(p, bytes) else str(p)
                    for p, ch in parts
                )

            for mid in reversed(ids):
                st, msg_data = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                if st != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                try:
                    m = message_from_bytes(raw)
                    out.append(f"发件人: {dec(m.get('From'))}\n主题: {dec(m.get('Subject'))}\n日期: {m.get('Date')}")
                except Exception:
                    continue
            if not out:
                return f"邮箱（{user}）近 {days} 天没有邮件"
            return f"邮箱（{user}）最近 {len(out)} 封邮件：\n\n" + "\n\n".join(out)
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        return f"错误：读取邮件失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "email_summary",
                "description": "读取近期邮件并整理为清单，供 AI 生成新邮件摘要（IMAP 配置同 read_email）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "可选：最多返回封数（默认 10，最大 50）"},
                        "since_days": {"type": "integer", "description": "可选：最近 N 天（默认 1）"},
                    },
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='邮件摘要/统计',
    preactivate=(('邮件', '发邮件', '收件箱'), ('收件箱', '邮件助手', 'agent邮箱', '邮件列表', '邮件搜索')),
)
def email_summary(limit=10, since_days=1):
    """读取近期邮件并整理为可汇总的清单（供 AI 生成摘要）。"""
    raw = read_email(limit=limit, since_days=since_days)
    if str(raw).startswith("错误"):
        return raw
    return "新邮件汇总任务：请根据以下邮件清单生成要点摘要（发件人/主题/日期）：\n\n" + raw


@tool(
        {
            "type": "function",
            "function": {
                "name": "agent_mail",
                "description": "Agent 原生邮箱（agently-cli）：me/list/search/read/send/reply/forward/trash/delete/download。写操作需两阶段确认：首次调用返回 confirmation-token，确认后再次调用",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "me / list / search / read / send / reply / forward / trash / delete / download"},
                        "q": {"type": "string", "description": "search：关键词"},
                        "id": {"type": "string", "description": "read/reply/forward/trash：msg_xxx"},
                        "to": {"type": "string", "description": "send/forward：收件人，多个用逗号分隔"},
                        "subject": {"type": "string", "description": "send：主题"},
                        "body": {"type": "string", "description": "send/reply/forward：正文"},
                        "dir": {"type": "string", "description": "list/search：inbox/sent/trash/spam"},
                        "limit": {"type": "integer", "description": "list/search：返回条数（默认 10）"},
                        "cursor": {"type": "string", "description": "list/search：翻页游标"},
                        "confirmation_token": {"type": "string", "description": "写操作二次确认：首次调用返回的 ctk_xxx"},
                        "attachment": {"type": "string", "description": "send/reply：附件路径，多个逗号分隔"},
                        "msg": {"type": "string", "description": "download：msg_xxx"},
                        "att": {"type": "string", "description": "download：att_xxx"},
                        "output": {"type": "string", "description": "download：保存目录"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='Agent 邮箱（查看/列表/搜索/回复/转发）',
    preactivate=(('收件箱', '邮件助手', 'agent邮箱', '邮件列表', '邮件搜索'),),
)
def agent_mail(action="list", q="", id="", to="", subject="", body="", dir="",
               limit=10, cursor="", confirmation_token="", attachment="", msg="", att="", output=""):
    """Agent 原生邮箱（通过 agently-cli）：me/list/search/read/send/reply/forward/trash/delete/download。

    写操作遵循 CLI 两阶段确认：首次调用不带 confirmation_token 会返回 ctk 与 summary，
    请向用户展示并等待明确许可后，再用相同参数 + confirmation_token 完成。
    """
    if not _dc.AGENT_MAIL_ENABLED:
        return _agent_mail_tip()
    act = str(action or "list").strip().lower()
    # 解析收件人与附件（逗号分隔，CLI 要求可重复参数）
    def _multi(value):
        return [x.strip() for x in str(value or "").split(",") if x.strip()]

    cmd = []
    try:
        limit = max(1, min(50, int(limit or 10)))
    except (TypeError, ValueError):
        limit = 10

    try:
        if act == "me":
            cmd += ["+me"]
        elif act == "list":
            cmd += ["message", "+list", "--limit", str(limit)]
            if str(dir or "").strip():
                cmd += ["--dir", str(dir).strip()]
            if str(cursor or "").strip():
                cmd += ["--cursor", str(cursor).strip()]
        elif act == "search":
            cmd += ["message", "+search", "--q", str(q or ""), "--limit", str(limit)]
            if str(dir or "").strip():
                cmd += ["--dir", str(dir).strip()]
            if str(cursor or "").strip():
                cmd += ["--cursor", str(cursor).strip()]
        elif act == "read":
            if not str(id or "").strip():
                return "错误：read 需要 id（msg_xxx）"
            cmd += ["message", "+read", "--id", str(id).strip()]
        elif act == "send":
            if not _multi(to) or not str(subject or "").strip():
                return "错误：send 需要 to（可逗号分隔多个）与 subject"
            cmd += ["message", "+send", "--subject", str(subject).strip(), "--body", str(body or "")]
            for x in _multi(to):
                cmd += ["--to", x]
            for x in _multi(attachment):
                cmd += ["--attachment", x]
            if str(confirmation_token or "").strip():
                cmd += ["--confirmation-token", str(confirmation_token).strip()]
        elif act == "reply":
            if not str(id or "").strip():
                return "错误：reply 需要 id（msg_xxx）"
            cmd += ["message", "+reply", "--id", str(id).strip(), "--body", str(body or "")]
            for x in _multi(attachment):
                cmd += ["--attachment", x]
            if str(confirmation_token or "").strip():
                cmd += ["--confirmation-token", str(confirmation_token).strip()]
        elif act == "forward":
            if not str(id or "").strip() or not _multi(to):
                return "错误：forward 需要 id 与 to"
            cmd += ["message", "+forward", "--id", str(id).strip(), "--body", str(body or "")]
            for x in _multi(to):
                cmd += ["--to", x]
            if str(confirmation_token or "").strip():
                cmd += ["--confirmation-token", str(confirmation_token).strip()]
        elif act == "trash":
            if not str(id or "").strip():
                return "错误：trash 需要 id"
            cmd += ["message", "+trash", "--id", str(id).strip()]
            if str(confirmation_token or "").strip():
                cmd += ["--confirmation-token", str(confirmation_token).strip()]
        elif act == "delete":
            if str(id or "").strip():
                cmd += ["message", "+delete", "--id", str(id).strip()]
            else:
                cmd += ["message", "+delete", "--all"]
        elif act == "download":
            if not str(msg or "").strip() or not str(att or "").strip():
                return "错误：download 需要 msg（msg_xxx）与 att（att_xxx）"
            cmd += ["attachment", "+download", "--msg", str(msg).strip(), "--att", str(att).strip()]
            if str(output or "").strip():
                cmd += ["--output", str(output).strip()]
        else:
            return "错误：action 仅支持 me/list/search/read/send/reply/forward/trash/delete/download"
    except Exception as e:
        return f"错误：参数构造失败: {e}"

    timeout = 120 if act in ("send", "reply", "forward") else 60
    code, out = _agent_mail_run(cmd, timeout=timeout)
    if code == 127:
        return out
    # 授权失效：提示用户重新 OAuth，不自动重试
    if code == 3 or "invalid_grant" in str(out).lower() or "unauthorized" in str(out).lower():
        return (
            "[授权失效] Agent Mail 登录状态已过期。请在系统终端运行 "
            "`agently-cli auth login` 重新授权后再试。\n\n" + out
        )
    # exit 8 = 缺少 confirmation-token：把 ctk/summary 原样交回，AI 必须停下等用户确认
    if code == 8 and not str(confirmation_token or "").strip():
        return (
            "[需要用户确认] 请把下面的 summary 展示给用户并等待明确许可；"
            "用户许可后，用相同参数加 confirmation_token 再次调用。\n\n" + out
        )
    if code == 0:
        return out or "（命令成功，无输出）"
    return f"[agently-cli exit {code}] " + out


@tool(
        {
            "type": "function",
            "function": {
                "name": "run_wechat_writer",
                "description": "运行公众号自动写作（WeChat Writer）：采集资讯→选题去重→LLM 写作→质量门禁→存草稿箱（只产草稿不发布）。dry_run 只预览，topic 指定主题，use_blocked 被墙信源走代理",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dry_run": {"type": "boolean", "description": "可选：true 只预览不写草稿（默认 false）"},
                        "topic": {"type": "string", "description": "可选：指定主题，跳过自动选题"},
                        "use_blocked": {"type": "boolean", "description": "可选：true 时被墙信源自动经代理通道采集（需 fetch_blocked 能力就绪）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='公众号文章生成/排版',
    preactivate=(('公众号', '公众号文章', '自动写作', '写公众号'),),
)
def run_wechat_writer(dry_run=False, topic="", use_blocked=False):
    """运行公众号自动写作工具：采集→选题→写作→质检→存草稿箱（只产草稿）。

    耗时可能 1-3 分钟（多次 LLM 调用）；返回结构化摘要文本。
    草稿统一写到工作区 drafts/（与 publish_draft 同目录，用户可从产物面板直达）。
    use_blocked=True 时被墙信源（linux.do/hostloc 等）自动经代理通道采集。
    """
    try:
        from wechat_writer import run_once
    except ImportError:
        return "错误：wechat_writer 模块不可用（请确认项目目录完整）"
    try:
        drafts_dir = None
        archive_dir = None
        if permissions.WORKSPACE_DIR:
            drafts_dir = os.path.join(permissions.WORKSPACE_DIR, "drafts")
            archive_dir = os.path.join(permissions.WORKSPACE_DIR, "wechat_articles")
        result = run_once(
            dry_run=bool(dry_run),
            topic_override=str(topic or ""),
            drafts_dir=drafts_dir,
            archive_dir=archive_dir,
            use_blocked=bool(use_blocked),
        )
    except Exception as e:
        return f"错误：公众号写作工具运行失败: {e}"
    if not result.get("ok"):
        reasons = result.get("quality", {}).get("reasons") or result.get("errors") or ["未知原因"]
        return f"公众号写作未完成：{'；'.join(str(r) for r in reasons[:3])}"
    q = result.get("quality") or {}
    paths = result.get("paths") or {}
    lines = [
        f"✅ 公众号文章已完成（{result.get('chars', 0)} 字，质检分 {q.get('score', '?')}）：",
        f"主题：{result.get('topic', '')}",
        f"标题：{result.get('title', '')}",
    ]
    if paths.get("draft_path"):
        lines.append(f"草稿：{paths['draft_path']}")
    if paths.get("html_path"):
        lines.append(f"HTML：{paths['html_path']}")
    if paths.get("archive_path"):
        lines.append(f"存档：{paths['archive_path']}")
    if dry_run:
        lines.append("（dry-run 预览：未写入草稿箱，正式运行后草稿存草稿箱，请在公众号后台审阅后手动发布）")
    else:
        lines.append("草稿已存入草稿箱，请在公众号后台审阅后手动发布（工具不自动发布）。")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "daily_brief",
                "description": "生成每日简报：采集当日 AI/科技资讯（RSS+搜索）→ LLM 提炼要点与点评 → 保存到工作区 briefs/。适合『今天的资讯有什么』『生成今日简报』等请求；可配合 schedule_task 定时生成晨报",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "可选：主题关键词（仅保留相关素材）"},
                        "max_items": {"type": "integer", "description": "可选：素材上限（默认 8，最大 15）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='每日简报（采集当日资讯→提炼点评）',
    preactivate=(('每日简报', '今日简报', '晨报', '简报生成'),),
)
def daily_brief(topic="", max_items=8):
    """生成每日简报：采集当日 AI/科技资讯（复用 WeChat Writer 采集引擎）
    → LLM 提炼要点与点评 → 保存到工作区 briefs/brief_YYYYMMDD.md。

    topic：可选主题关键词（仅保留标题/摘要命中的素材）。
    返回简报正文 + 落盘路径。
    """
    try:
        from wechat_writer import config as _ww_config
        from wechat_writer import sources as _ww_sources
    except ImportError:
        return "错误：wechat_writer 模块不可用（请确认项目目录完整）"
    try:
        cfg = _ww_config.load_config()
        items = _ww_sources.collect_all(cfg)
    except Exception as e:
        return f"错误：资讯采集失败: {e}"
    kw = str(topic or "").strip()
    if kw:
        items = [it for it in items if kw in (f"{it.title} {it.summary}")]
    if not items:
        return "今日暂无资讯素材（RSS 与搜索均无结果），可稍后再试"
    try:
        limit = max(3, min(15, int(max_items or 8)))
    except (TypeError, ValueError):
        limit = 8
    items = items[:limit]
    client = get_active_client()
    if client is None:
        return "错误：没有可用客户端（请先在设置中配置 API Key）"
    material = "\n\n".join(
        f"{i + 1}. {it.title}（{it.source}）\n   {it.url}\n   {it.summary[:200]}"
        for i, it in enumerate(items)
    )
    prompt = (
        "你是每日资讯主编。基于以下今日采集的资讯，生成一份精炼简报：\n"
        "1. 简报标题：一句话概括今日主题（## 开头）\n"
        "2. 3-6 条要点，每条用 - 前缀：主题 + 一句话点评\n"
        "3. 结尾「今日趋势」：2-3 句话总结值得关注的动向\n"
        "只输出简报正文（Markdown），不要任何说明文字。\n\n"
        f"素材（{len(items)} 条）：\n{material}"
    )
    try:
        resp = client.client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            stream=False,
            timeout=120.0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        brief = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"错误：简报生成失败: {e}"
    if not brief:
        return "错误：简报生成失败：模型返回空内容，请重试"
    out = ""
    if permissions.WORKSPACE_DIR:
        d = os.path.join(permissions.WORKSPACE_DIR, "briefs")
        ok, reason = permissions.check_filesystem(d, write=True)
        if not ok:
            out = f"\n（简报落盘被权限拒绝：{reason}）"
        else:
            try:
                os.makedirs(d, exist_ok=True)
                path = os.path.join(d, f"brief_{datetime.now():%Y%m%d}.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# 每日简报 {datetime.now():%Y-%m-%d}\n\n{brief}\n")
                out = f"\n已保存：{path}"
            except Exception as e:
                out = f"\n（简报落盘失败：{e}）"
    return f"📰 今日简报（{len(items)} 条素材）：\n\n{brief}{out}"


__all__ = ['send_email', 'publish_draft', 'send_webhook', 'im_send', 'telegram_poll_updates', 'read_email', 'email_summary', 'agent_mail', 'run_wechat_writer', 'daily_brief']
