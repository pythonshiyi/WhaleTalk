# -*- coding: utf-8 -*-
"""运行时工具域模块聚合（P0-1 巨石拆分）。

deepseek_client.py 在共享基建定义后执行 `from agent_tools import *`：
  - 各域模块顶层运行 @tool() 注册（toolkit 注册表是模块级单例，跨模块共享）；
  - 本包 __all__ 显式 re-export 工具函数名，保证 dc.get_date / dc.read_csv /
    dc.image_understand 等旧访问路径（外部代码经 deepseek_client 命名空间访问）
    保持不变。

加载顺序契约（P1-3 收窄）：工具域阈值常量/锁已下沉 shared（见 shared.py
「工具域阈值与锁」节），域模块直接 from shared 导入、不再回指主文件；
仍引用 deepseek_client 的仅是剩余辅助函数（_atomic_write / _capture_screen_png /
get_active_client 等）与运行态配置（dc.WORKING_DIR / dc.*_FILE 注入项）——
deepseek_client 必须在其 `from agent_tools import *`（共享基建全部定义后）之前
完成这些符号的定义，故本包导入位于主文件共享基建之后、六层构建之前。

新增工具域：新建 tool_*.py 并在下方 import；若含新工具名记得同步 __all__；
新增阈值常量请放 shared.py 对应「工具域阈值与锁」分组，勿回写主文件。
"""

from .tool_basic import *  # noqa: F401,F403  # get_date / get_weather
from .tool_data import *   # noqa: F401,F403  # read_csv / write_csv
from .tool_media import *  # noqa: F401,F403  # 媒体与图像 10 工具
from .tool_docs import *  # noqa: F401,F403  # 📊 数据与文档
from .tool_desktop import *  # noqa: F401,F403  # 🖱 桌面与视觉语音
from .tool_system import *  # noqa: F401,F403  # 🔧 系统与项目
from .tool_msg import *  # noqa: F401,F403  # 📧 邮件与消息
from .tool_brain import *  # noqa: F401,F403  # 🧠 记忆与定时任务
from .tool_files import *  # noqa: F401,F403  # 📁 文件与进程
from .tool_code import *  # noqa: F401,F403  # 💻 编程与执行
from .tool_web import *  # noqa: F401,F403  # 🌐 浏览器与网页

__all__ = [
    "get_date",
    "get_weather",
    "read_csv",
    "write_csv",
    "image_process",
    "ocr_image",
    "image_understand",
    "screen_capture",
    "screen_see",
    "chart_read",
    "screenshot_to_html",
    "debug_screenshot",
    "scan_read",
    "image_batch",
    'fetch_url',
    '_run_fetch_blocked',
    'create_plugin',
    'download_file',
    'search_web',
    'search_github',
    'search_realtime',
    'browser_navigate',
    'web_screenshot',
    'net_diagnose',
    'fetch_url_smart',
    'rss_fetch',
    'webdav',
    'call_api',
    'track_web',
    'run_python',
    'run_command',
    'run_lint',
    'run_tests',
    'verify_project',
    'project_scaffold',
    'dev_plan',
    'get_status',
    'project_map',
    'find_symbol',
    'code_lookup',
    'write_code_project',
    'pip_install',
    'subagent_run',
    'verify_output',
    'read_file',
    'write_file',
    'edit_file',
    'list_dir',
    'search_local',
    'clipboard_get',
    'clipboard_set',
    'delete_file',
    'archive_files',
    'extract_archive',
    'list_snapshots',
    'restore_snapshot',
    'batch_rename',
    'start_process',
    'stop_process',
    'list_processes',
    'environment_info',
    'write_memory',
    'self_profile',
    'delete_memory',
    'update_memory',
    'read_memory',
    'query_memory_graph',
    'knowledge_index',
    'knowledge_search',
    'schedule_task',
    'list_schedules',
    'cancel_schedule',
    'task_checkpoint_save',
    'task_checkpoint_load',
    'run_workflow',
    'send_email',
    'publish_draft',
    'send_webhook',
    'im_send',
    'telegram_poll_updates',
    'read_email',
    'email_summary',
    'agent_mail',
    'run_wechat_writer',
    'daily_brief',
    'watch_files',
    'recall_session',
    'project_info',
    'read_project_file',
    'create_evolution',
    'self_evolve',
    'verify_files',
    'git_tool',
    'notify_desktop',
    'app_manage',
    'usage_report',
    'rpa_screen_size',
    'rpa_click',
    'rpa_type',
    'rpa_hotkey',
    'rpa_move',
    'rpa_scroll',
    'rpa_screenshot',
    'screen_find_click',
    'vision_loop',
    'tts_save',
    'speech_to_text',
    'tts_speak',
    'tts_stop',
    'voice_chat_loop',
    'image_generate',
    'qrcode',
    'media_ffmpeg',
    'team_run',
    'database_query_mysql',
    'database_query_postgres',
    'read_excel',
    'epub_read',
    'mobi_read',
    'doc_read',
    'msg_read',
    'archive_list',
    'write_excel',
    'chart_data',
    'database_query',
    'database_execute',
    'pdf_extract',
    'pdf_create',
    'docx_read',
    'pptx_read',
    'secret_store',
    'kv_store',
    'create_doc',
]
