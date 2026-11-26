# 智能体评测任务

> 运行器：`tools/agent_eval.py`；首版基线：`docs/eval/BASELINE.md`。
> 原则：**验收必须程序可判定**，不靠 LLM 打分；每个任务独立数据目录与服务。

## 目录

```
tests/eval/
  golden/            黄金任务（*.json，一个文件一个任务）
  fixtures/          夹具（任务开始时复制到独立工作区）
tools/agent_eval.py  运行器
out/eval/            运行产物（已 gitignore）
```

## 任务格式

```json
{
  "id": "file_markdown_report",
  "category": "files",
  "turns": ["读取 sales.csv，生成 out/report.md ..."],
  "fresh_session_each_turn": false,
  "fresh_session_at": [3],
  "setup": ["sales.csv"],
  "local_http": ["feed.xml"],
  "config": {"memory_enabled": true},
  "accept": [
    {"type": "file_exists", "path": "out/report.md"},
    {"type": "file_contains", "path": "out/report.md", "text": "合计：3500"},
    {"type": "no_tool_error"}
  ],
  "budget": {"max_tool_calls": 20, "max_cny": 1.5, "max_seconds": 300},
  "notes": "给维护者看的说明"
}
```

字段：

- `id`：小写字母/数字/`-`/`_`，全局唯一；
- `turns`：用户消息数组；多轮会在同一会话内顺序发送；
  `fresh_session_each_turn=true` 时每轮新会话，`fresh_session_at: [3]` 只让第 3 轮换新会话（用于记忆测试）；
- `setup`：`fixtures/` 下的文件或目录名，复制到独立工作区；
- `local_http`：`fixtures/` 下的文件，由评测进程起一个只读本地 HTTP 服务提供；提示里用 `{base_url}` 引用（如 `{base_url}/feed.xml`），用于消除外部网络抖动；
- `config`：本任务的配置覆盖（目前支持 `memory_enabled` / `auto_memory` / `memory_harvest_mode`）；
- `accept`：验收条件数组，全部通过才算 PASS；
- `budget`：`max_tool_calls`（用于推导最大轮数）、`max_cny`（记录用）、`max_seconds`（单轮硬超时）。

## 验收类型

| 类型 | 参数 | 说明 |
|---|---|---|
| `file_exists` / `file_not_exists` | `path` | 工作区内相对路径 |
| `dir_exists` | `path` | 目录 |
| `file_contains` / `file_not_contains` | `path`, `text` | 文本包含 / 不包含（UTF-8 BOM 容忍） |
| `file_regex` | `path`, `pattern` | 正则搜索（多行） |
| `file_min_bytes` / `file_max_bytes` | `path`, `bytes` | 文件大小 |
| `file_number_min` | `path`, `min` | 文件里第一个数字 ≥ min |
| `json_field` | `path`, `field`, `equals` | 支持点号路径；数字容差 1e-6 |
| `json_keys` | `path`, `keys` | 顶层对象包含指定键 |
| `json_len` | `path`, `length` | 数组/对象长度 |
| `csv_columns` | `path`, `columns` | 表头精确匹配 |
| `csv_row_count` | `path`, `rows` | 数据行数（不含表头） |
| `csv_row` | `path`, `index`, `values` | 第 N 行数据（0 起） |
| `image_size` | `path`, `width`, `height` | 精确尺寸；需要 Pillow |
| `image_min_size` | `path`, `width`, `height` | 至少尺寸（容忍渲染留白）；需要 Pillow |
| `xlsx_cells` | `path`, `cells` | 单元格断言；需要 openpyxl |
| `zip_entries` | `path`, `entries` | 压缩包内条目齐全 |
| `command` | `argv`, `timeout` | 在工作区执行命令，exit 0 即通过；`{python}` 替换为当前解释器 |
| `tool_called` | `name` | 任意一轮调用过该工具 |
| `no_tool_error` | `allow`（可选） | 没有以「错误：」开头的工具结果 |
| `final_contains` / `final_not_contains` | `text` | 最后一轮助手正文包含 / 不包含 |
| `final_regex` | `pattern` | 最后一轮助手正文正则 |

## 新增任务

1. 在 `golden/` 新建 JSON，先用 `py -3.15 tools/agent_eval.py --list` 确认能加载；
2. `--dry-run` 校验夹具与类型；
3. `--only <id>` 实跑，确认验收条件本身可达（避免「任务永远不可能通过」）；
4. 把任务说明写进 `notes`，不要写在 `turns` 里（会进模型上下文）。

## 注意

- 任务工作区是 `${run}/data/workspace`，相对路径都以它为根；不要写绝对路径；
- 提示词里的目标路径要写死（如 `out/report.md`）并加一句「不要新建子目录」——实测模型会按任务名/会话名自行造目录；
- 每个任务独立起服务（约 3s），任务之间不共享会话、记忆、大脑；
- 评测配置默认关闭生命循环、大脑快照、自动记忆与更新检查，避免污染与额外网络请求；
- 跑完临时配置里的密钥会被替换成 `***`，但 `out/` 仍不应提交。
