"""SSRF / URL 安全校验工具。

从 deepseek_client.py 中拆出的纯安全函数，供客户端、测试与后续模块复用。
云元数据地址（169.254.0.0/16）永远不可豁免。
"""
import re
import socket

SSRF_TRUSTED = []

# 单个点分标签：十进制数字或十六进制（0x…）
_NUMERIC_LABEL_RE = re.compile(r"0x[0-9a-f]+|\d+", re.IGNORECASE)
# 严格点分十进制（无前导零、每段 0-255）
_STRICT_LABEL_RE = re.compile(r"0|[1-9]\d{0,2}")


def _classify_numeric_host(host):
    """区分「规范点分十进制」与「非规范数值型主机」。

    返回 (kind, canonical)：
      - ("clean", "1.2.3.4")  规范点分十进制（已去尾部点）
      - ("ambiguous", None)   整数 / 十六进制 / 八进制 / 缺段等数值型写法
      - (None, None)          普通主机名（含 IPv6 字面量）

    为什么需要它：`http://2130706433/`、`http://0x7f000001/`、`http://10.0.0.1.`
    等写法 `ipaddress` 无法解析，会落到 DNS 分支并因解析失败被 fail-open 放行，
    而浏览器/Chromium 会把它们归一化为真实 IP——是 SSRF 硬底线的绕过面。
    对这类非规范数值型一律判为需拒绝。
    """
    h = host[:-1] if (len(host) > 1 and host.endswith(".")) else host
    labels = h.split(".")
    if not all(_NUMERIC_LABEL_RE.fullmatch(p) for p in labels):
        return (None, None)
    if len(labels) == 4 and all(
        _STRICT_LABEL_RE.fullmatch(p) and int(p) <= 255 for p in labels
    ):
        return ("clean", ".".join(labels))
    return ("ambiguous", None)


def _url_host(url):
    """解析 URL 主机名；非法返回 None。"""
    try:
        from urllib.parse import urlparse

        host = urlparse(str(url)).hostname
        return (host or "").lower()
    except Exception:
        return None


def set_ssrf_trusted(hosts):
    """设置 SSRF 信任主机白名单（内网/保留网段经用户显式信任后放行）。

    支持：主机名精确匹配、IP 精确匹配、CIDR 网段（192.168.1.0/24）、
    域后缀（example.com. 通配 *.example.com）。云元数据地址永远不可豁免。
    """
    # 原地替换保持同一列表对象：deepseek_client/main 中 `from security import SSRF_TRUSTED`
    # 绑定的是同一对象，重新赋值会导致外部引用看不到更新。
    SSRF_TRUSTED[:] = [str(h).strip().lower() for h in (hosts or []) if str(h).strip()]


def _trusted_host(host, trusted):
    """信任白名单匹配：主机名/IP 精确、CIDR 网段、域后缀。"""
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for item in trusted or []:
        if not item:
            continue
        if item == host:
            return True
        if ip is not None and "/" in item:
            try:
                if ip in ipaddress.ip_network(item, strict=False):
                    return True
            except ValueError:
                pass
        elif item.startswith("*.") and host.endswith(item[1:]):
            return True
    return False


def _is_private_host(host, allow_loopback=True):
    """SSRF 防护：主机是否为回环/内网/链路本地地址（模型可控 URL 禁止访问）。

    安全分层（桌面单用户智能体：模型指令均来自用户）：
    - 回环（localhost / 127.0.0.0/8 / ::1）：默认放行——本地开发服务器验证
      （localhost:3000 等）是最高频正当场景；严格场景（如搜索结果过滤）传
      allow_loopback=False。
    - 内网 / 链路本地 / 保留网段：默认阻止，SSRF_TRUSTED 白名单可显式信任
      （内网服务 / NAS 等）。
    - 云元数据地址（169.254.169.254 等 169.254.0.0/16）：永远阻止，白名单
      不可豁免（云环境 SSRF 的最终攻击面）。

    DNS 重绑定防护：域名先解析，只要任一解析结果落在内网即拦截——
    模型可控的域名指向 127.0.0.1 时（恶意/失陷 DNS）不再放行。解析失败
    时放行并保持原行为（避免离线环境误杀可用功能）。
    """
    host = (host or "").strip().lower()
    if not host:
        return True
    # 非规范数值型主机（整数/十六进制/八进制/缺段/超范围）：浏览器会归一化为
    # 真实 IP，逐字符串判定可被绕过，一律拦截（须在信任白名单之前）。
    kind, canon = _classify_numeric_host(host)
    if kind == "ambiguous":
        return True
    if kind == "clean":
        host = canon
    # 云元数据 / 链路本地（169.254.0.0/16）：永远阻止，信任白名单不可豁免
    if host.replace(".", "").isdigit():
        parts = host.split(".")
        if len(parts) == 4:
            try:
                if int(parts[0]) == 169 and int(parts[1]) == 254:
                    return True
            except ValueError:
                return True
    if _trusted_host(host, SSRF_TRUSTED):
        return False
    if host in ("localhost", "ipv6-localhost"):
        return not allow_loopback
    # 形如 127.0.0.1 的纯数字点分式
    if host.replace(".", "").isdigit():
        parts = host.split(".")
        if len(parts) == 4:
            try:
                a, b = int(parts[0]), int(parts[1])
                if a == 127:
                    return not allow_loopback
                if a == 10:
                    return True
                if a == 172 and 16 <= b <= 31:
                    return True
                if a == 192 and b == 168:
                    return True
                if a == 169 and b == 254:
                    return True  # 云元数据 / 链路本地：永远阻止
                if a == 0:
                    return True
            except (ValueError, IndexError):
                return True
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            return not allow_loopback
        # 链路本地（含 169.254.169.254 云元数据）与保留地址永远阻止
        if ip.is_link_local or ip.is_reserved:
            return True
        return bool(ip.is_private)
    except ValueError:
        pass
    # 非 IP 主机名：解析 DNS，任一解析结果落内网即拦截（防 DNS 重绑定）
    try:
        infos = socket.getaddrinfo(
            host, None, socket.AF_INET | socket.AF_INET6, socket.SOCK_STREAM
        )
    except Exception:
        return False  # 解析失败（离线/DNS 不可用）：维持放行，避免误杀
    seen = set()
    for info in infos:
        try:
            ip = (info[4] or ["", ""])[0]
            ip = ip.split("%")[0]  # 去掉 IPv6 区域 ID
            if ip in seen:
                continue
            seen.add(ip)
            addr = ipaddress.ip_address(ip)
            if addr.is_loopback:
                # 回环：仅当不允许时才拦截；允许时继续检查其余解析记录，
                # 不能「见回环即放行」——多 A 记录可被攻击者控制（DNS 重绑定）。
                if not allow_loopback:
                    return True
                continue
            if addr.is_link_local or addr.is_reserved or addr.is_private:
                return True
        except ValueError:
            continue
    return False


def _hard_floor_reason(host, allow_loopback=None):
    """blacklist 模式下的 SSRF 硬底线：私网 / 链路本地 / 保留段一律拦截。

    为什么需要它：默认自由模式下 `_safe_url` 只查用户黑名单，而出厂黑名单仅一条
    `169.254.169.254` —— 结果是内网段（10/172.16/192.168）、**整段**链路本地
    （169.254.0.0/16，云元数据不止一个地址）实际处于放行状态，且
    `_is_private_host` 里的 DNS 重绑定校验（域名解析分支）在默认模式下压根不执行。
    模型可自主抓取任意 URL，而抓取内容会回灌上下文（prompt injection 面），
    故补一道不依赖用户配置的硬底线。

    回环（localhost / 127.0.0.1）默认**放行**：本产品是「本机单用户软件」，
    本地开发服务器验证（localhost:3000 等）是高频正当场景；且工具层默认本就
    允许 run_command/run_python（本机任意执行），把回环当作最后一道闸并无实际
    收益。需要加严时把 network.allow_loopback 置 false 即可。

     返回 "" 表示放行，否则返回拒绝原因。开关：
      - blocklist_enabled=false（一键全放行）→ 整体跳过；
      - network.block_private=false → 单独关闭本底线。
    `allow_loopback`：调用方若显式传 False（如搜索结果过滤），与配置取**更严**者
    （任一禁止回环即禁止），避免调用方参数被配置文件静默覆盖。
    配置读取失败时放行（保持与其它判定一致的 fail-open，避免误杀可用功能）。
    """
    try:
        import permissions
        data = permissions.get_data() or {}
        if not bool(data.get("blocklist_enabled", True)):
            return ""
        net = data.get("network") or {}
        if not bool(net.get("block_private", True)):
            return ""
        allow_loop = bool(net.get("allow_loopback", True)) and (
            True if allow_loopback is None else bool(allow_loopback)
        )
    except Exception:
        return ""
    if not _is_private_host(host, allow_loopback=allow_loop):
        return ""
    return ("已阻止访问内网/链路本地/保留地址（SSRF 硬底线）；"
            "确需访问内网服务可在权限页调整 network 配置")


def _safe_url(url, allow_loopback=True):
    """URL 安全校验。

    blacklist 模式（默认）：拦用户 network.blocklist + **SSRF 硬底线**
    （私网/链路本地/保留段，见 `_hard_floor_reason`；回环默认放行）。
    whitelist 模式：保持旧 SSRF 严格判断。
    """
    if not url or not str(url).startswith(("http://", "https://")):
        return "URL 必须以 http:// 或 https:// 开头"
    host = _url_host(url)
    if not host:
        return f"URL 主机名解析失败：{url[:80]}"
    try:
        import permissions
        if permissions.security_mode() == "blacklist":
            ok, reason = permissions.check_network_host(host)
            if not ok:
                return f"{reason}：{url[:80]}"
            hard = _hard_floor_reason(host, allow_loopback=allow_loopback)
            if hard:
                return f"{hard}：{url[:80]}"
            return ""
    except Exception:
        pass
    # 旧 whitelist 模式 / 权限模块未初始化：保留严格 SSRF 判断
    if _is_private_host(host, allow_loopback=allow_loopback):
        return f"已阻止访问内网/回环地址（SSRF 防护）：{url[:80]}"
    return ""
