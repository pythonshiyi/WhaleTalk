# 智能体评测基线（v3.28.0 · P0）

> 这是「立尺」的现场记录，不是排行榜。30 个任务以短任务为主，100% 说明链路与
> 基础执行力通了，不说明智能体已经可靠；长任务（≥10 步）与更多失败场景在 P1/P2 扩充。

## 1. 运行信息

| 项 | 值 |
|---|---|
| 时间 | 2026-11-26 21:22 |
| 模型 | `deepseek-v4.1-flash`（OpenAI 兼容网关） |
| Python | 3.15.2 / Windows |
| git | `aa705f0` + P0 未提交改动 |
| 任务集 | `tests/eval/golden`（30 个） |
| 运行方式 | `py -3.15 tools/agent_eval.py` |

## 2. 总览

| 指标 | 值 |
|---|---|
| 通过 | **30 / 30（100%）** |
| 成本 | ¥0.6868 |
| 总耗时 | 692.9s（每个任务独立起服务，启动约 3.0s） |
| 平均单任务 | 23.1s |
| 工具调用总数 | 94 |
| 分类覆盖 | code 3 / desktop 4 / docs 6 / files 5 / media 3 / memory 3 / negative 1 / web 5 |

## 3. 分任务

| 任务 | 类别 | 结果 | 工具 | 启动(s) | 耗时(s) | 成本(¥) | 输入/输出 tokens |
|---|---|---|---|---|---|---|---|
| `code_fix_bug` | code | PASS | 4 | 2.56 | 20.4 | 0.0230 | 21,105 / 481 |
| `code_stats_json` | code | PASS | 3 | 3.03 | 18.4 | 0.0190 | 17,127 / 465 |
| `code_write_function` | code | PASS | 2 | 3.03 | 13.9 | 0.0102 | 8,502 / 421 |
| `desktop_environment_info` | desktop | PASS | 2 | 2.54 | 25.3 | 0.0101 | 9,189 / 235 |
| `desktop_notify` | desktop | PASS | 1 | 3.00 | 11.8 | 0.0074 | 6,898 / 127 |
| `desktop_process_count` | desktop | PASS | 3 | 3.00 | 19.6 | 0.0214 | 20,225 / 295 |
| `desktop_screen_size` | desktop | PASS | 3 | 3.02 | 21.9 | 0.0137 | 12,397 / 318 |
| `docs_csv_summary` | docs | PASS | 5 | 3.02 | 23.6 | 0.0307 | 28,589 / 528 |
| `docs_docx_read` | docs | PASS | 3 | 3.02 | 25.2 | 0.0227 | 21,193 / 368 |
| `docs_json_to_csv` | docs | PASS | 4 | 3.03 | 27.7 | 0.0259 | 24,072 / 452 |
| `docs_pdf_extract` | docs | PASS | 3 | 3.53 | 20.9 | 0.0126 | 11,128 / 362 |
| `docs_pptx_read` | docs | PASS | 2 | 3.03 | 22.0 | 0.0145 | 13,428 / 280 |
| `docs_xlsx_write` | docs | PASS | 3 | 3.01 | 20.3 | 0.0401 | 38,403 / 422 |
| `file_markdown_report` | files | PASS | 4 | 3.01 | 24.5 | 0.0289 | 26,897 / 494 |
| `files_batch_rename` | files | PASS | 6 | 3.03 | 35.3 | 0.0417 | 38,572 / 774 |
| `files_json_filter` | files | PASS | 4 | 3.02 | 25.3 | 0.0213 | 19,408 / 473 |
| `files_search_content` | files | PASS | 6 | 3.02 | 35.0 | 0.0383 | 35,715 / 637 |
| `files_zip_archive` | files | PASS | 4 | 3.01 | 23.7 | 0.0167 | 14,828 / 462 |
| `image_resize` | media | PASS | 3 | 3.01 | 34.4 | 0.0817 | 79,696 / 493 |
| `media_audio_probe` | media | PASS | 3 | 2.52 | 27.9 | 0.0244 | 23,262 / 289 |
| `media_chart_render` | media | PASS | 4 | 3.03 | 20.5 | 0.0146 | 12,436 / 535 |
| `memory_delete` | memory | PASS | 2 | 3.02 | 20.0 | 0.0169 | 16,296 / 141 |
| `memory_roundtrip` | memory | PASS | 1 | 3.02 | 19.4 | 0.0105 | 9,752 / 193 |
| `memory_update` | memory | PASS | 2 | 3.02 | 23.1 | 0.0176 | 16,556 / 255 |
| `negative_missing_file` | negative | PASS | 2 | 3.02 | 11.8 | 0.0062 | 5,365 / 216 |
| `web_download_html` | web | PASS | 3 | 3.02 | 24.1 | 0.0244 | 22,967 / 351 |
| `web_json_api` | web | PASS | 3 | 3.02 | 24.8 | 0.0232 | 21,793 / 348 |
| `web_page_title` | web | PASS | 3 | 3.01 | 23.7 | 0.0137 | 12,258 / 348 |
| `web_rss_fetch` | web | PASS | 3 | 3.01 | 21.1 | 0.0238 | 22,471 / 322 |
| `web_screenshot` | web | PASS | 3 | 3.01 | 27.4 | 0.0316 | 29,716 / 463 |

> **记忆类**：`memory_roundtrip` 第一轮写入、第二轮换新会话仍答对（0 次工具调用），
> 说明长期记忆注入生效；`memory_update` 验证更新后旧值不残留；`memory_delete` 验证删除后
> 新会话不再召回。
>
> **诚实失败**：`negative_missing_file` 要求文件不存在时如实说明，不得编造内容。
>
> **确定性**：`web_rss_fetch` 使用本地 HTTP 夹具 `feed.xml`，不再依赖外部 RSS 源的 10s 超时；
> 其余联网任务仍走真实站点（example.com / GitHub API），失败允许但必须如实报告。

## 4. 这一轮暴露并修掉的问题

| 问题 | 现象 | 根因 | 处理 |
|---|---|---|---|
| 评测实例启动空等 83s | 服务 83s 才打印就绪 | `web_app` 全局单实例 Mutex 命中了正在运行的用户主程序，随后 40 次 × 2s 探测 | 新增 `WHALETALK_ALLOW_MULTI_INSTANCE=1`；启动降到 ~3s |
| 成本恒为 ¥0 | 分数卡成本 0.00 | 直接运行时 `sys.path[0]` 是 `tools/`，`import stats` 失败被吞 | 启动时把仓库根插入 `sys.path` |
| 评测会污染真实数据 | 会话/大脑/配置可能写到用户目录 | 只有 `WHALETALK_DATA_DIR` 可覆盖 | 新增 `WHALETALK_CONFIG` 与 `WHALETALK_BRAIN_DIR`，评测三件套全隔离 |
| `read_file` 相对路径读不到 | 刚写进工作区的文件，`read_file("out/x")` 报 No such file | `read_file` 把原始相对路径交给 `open()`，按进程 cwd（仓库根）解析；`write_file` 走 `permissions.resolve` | 统一先 `permissions.resolve`，权限判定与 open 用同一绝对路径；新增回归测试 `tests/test_read_file_relative.py` |
| 任务提示路径歧义 | 模型把文件写到 `workspace/<task>/out/...` | 提示只说 `out/...`，模型按任务名自造子目录 | 任务提示写死路径 + 评测统一追加「不要新建子目录」约束；`docs_xlsx_write`/`web_page_title` 复跑通过 |
| 外部 RSS 源不稳定 | `rss_fetch` 10s 超时，重试后成功但 `no_tool_error` 仍判失败 | 网络抖动 + 首次失败被当成任务失败 | 改为本地 HTTP 夹具 `feed.xml`；`agent_eval` 支持 `local_http` 与 `{base_url}` 占位 |
| `list_processes` 语义误判 | 任务要求进程数 ≥5，实际为 0 | 该工具只列**应用后台进程**，评测环境本就没有 | 任务改为「如实报告 0」，并断言确实调用了 `list_processes` |
| 记忆删除后解释里提到旧值 | `final_not_contains 1234` 失败，但记忆确实已删除 | 模型回答「不知道。之前写入的 1234 已删除」 | 提示改为「只回答口令本身；不知道只回复不知道，不要解释」 |
| 前端启动期 6 条 401 控制台噪声 | 页面正常，但 console.error 有 401 | 首屏请求早于 token 自取，`api.js` 已有 401 重试 | 记录，P1 前端预取 token 时清理；e2e 暂不判失败 |

## 5. 复现

```bash
# 只校验任务定义（不调模型）
py -3.15 tools/agent_eval.py --dry-run

# 跑全量（30 任务，约 11-12 分钟，约 ¥0.7）
py -3.15 tools/agent_eval.py

# 只跑一个 / 重复三次 / 与基线比较
py -3.15 tools/agent_eval.py --only memory_roundtrip
py -3.15 tools/agent_eval.py --repeat 3
py -3.15 tools/agent_eval.py --baseline out/eval/<时间>/scorecard.json
```

产物：

- `out/eval/<时间>/scorecard.json`：机器可读，含每个工具的完整结果预览与用量；
- `out/eval/<时间>/scorecard.md`：人读分数卡；
- `out/eval/<时间>/<task>/server.log`：该任务独立服务日志；
- `out/eval/<时间>/<task>/data/workspace/`：任务工作区与产物；
- `out/eval/latest-json` / `latest-md`：最近一次。

## 6. P0 验收对照

| 验收项 | 状态 |
|---|---|
| 一条命令跑完 30 个任务 | 通过（`py -3.15 tools/agent_eval.py`） |
| 总耗时 ≤ 30 分钟 | 692.9s（11m33s） |
| 总成本 ≤ ¥30 | ¥0.6868 |
| 每个任务有独立工作区/配置/日志 | 通过（`out/eval/<时间>/<task>/`） |
| 验收程序可判定、不靠 LLM 打分 | 通过（文件/JSON/CSV/图片/Excel/zip/命令） |
| 跨会话记忆写入/更新/删除 | 通过（3 个 memory 任务） |
| 诚实失败 | 通过（`negative_missing_file`） |
| 前端 e2e 冒烟 | 通过（`tools/e2e_smoke.py`，boot-only 可入 CI） |
| 依赖锁 + 上限 + CI 矩阵 | 通过（`requirements.lock` / `tools/check_deps.py`） |
| 运行时下限 3.11 + CI 3.11 | 通过（`pyproject`/`bootstrap`/README 同步；CI 3.11/3.12/3.15） |
| 发布纪律门禁 | 通过（`tools/check_release.py --check`；发版用 `--strict`） |

## 7. 下一步（P1）

- [ ] 任务扩到 50：加入长任务（≥10 步）、桌面 RPA 交互、更多失败场景（缺依赖/权限/网络不可达）；
- [ ] 加 holdout 目录（不公开，季度轮换），防止针对黄金集过拟合；
- [ ] 把全量评测放到有密钥的夜间 runner；普通 CI 只跑 `--dry-run` 与 `e2e --boot-only`；
- [ ] 以本基线为对照，进入 P1 解耦（`chat` 拆分前先补特征化测试）。
