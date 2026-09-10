# -*- coding: utf-8 -*-
"""进程工具：进程树终止。

从 deepseek_client.py 中拆出，供 run_python / run_command / 后台进程管理等复用。
"""
import os
import subprocess


def kill_tree(proc, wait_seconds=3):
    """终止整个进程树：Windows 上 kill() 只杀直接子进程，pip/pytest/服务器
    派生的孙进程会残留（继续占端口/CPU）。taskkill /T 递归，失败回退 kill()。

    **返回 bool：True = 已确认进程退出**。
    此前本函数吞掉全部异常且无返回值，调用方无从判断是否真的杀掉 —— 上游因此
    出现过「杀失败却照样把进程条目摘除」的缺陷，进程沦为孤儿：端口还占着，而
    stop/list 都看不到它，用户再也停不掉（实测复现）。如实返回结果是修这个
    缺陷的前提。
    """
    if proc is None:
        return False
    try:
        if os.name == "nt" and proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=max(2, int(wait_seconds)),
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=wait_seconds)
    except Exception:
        pass
    try:
        return proc.poll() is not None
    except Exception:
        return False
