# 生命循环（Life Loop）

> 目标不是“加一个自动任务”，而是让 WhaleBrain 拥有连续时间、内驱、代谢、睡眠与单一时间线。
> 本模块**不引入审批、白名单或沙箱**；所有约束都是内稳态（预算、疲劳、意图上限、单实例租约）。

## 1. 五个闭环

| 闭环 | 模块 | 解决什么 |
|---|---|---|
| 时间 | `life/timeline.py`、`life/state.py` | 连续 tick、唯一时间线、epoch、单实例 |
| 动机 | `life/drives.py`、`life/intentions.py` | 五维内驱 → 自发意图 → 结果 |
| 代谢 | `life/metabolism.py` | 每日预算、疲劳、能量、峰谷 |
| 睡眠 | `life/sleep.py` | 整理、日结、自我模型刷新、进化提案 |
| 代价 | `life/scars.py` | 失败/反转/丢失的代价记录 |

`life/loop.py` 是状态机；`life/api.py` 只做 API 适配；`life/report.py` 输出可量化指标。

## 2. 状态机

```
dormant ──大脑就绪──▶ waking ──▶ awake ⇄ idle
                                  │  ├─▶ reflecting ──▶ awake
                                  │  └─▶ acting     ──▶ awake
                                  └─▶ sleeping ──用户活动──▶ awake
observer：另一个活进程持有租约时只读
paused：用户显式暂停
```

- tick 由 `api_server._scheduler_loop` 每 30 秒调用一次，不额外占线程。
- tick 只做廉价判断；反思/睡眠放到 daemon 工作线程。
- **tick 绝不无条件调用模型。**

## 3. 数据文件（均在 `brain/`）

| 文件 | 作用 |
|---|---|
| `timeline.jsonl` | 唯一事件流，append-only；坏行跳过 |
| `life.json` | 当前状态、epoch、tick 计数、上次睡眠/唤醒 |
| `life.lock` | 单实例租约（pid/host/beat/token），90 秒无心跳可接管 |
| `drives.json` | 五维内驱分数与计算依据 |
| `intentions.json` | 意图生命周期：proposed/active/done/failed/abandoned |
| `metabolism.json` | 当日疲劳、能量、动作数、累计消耗 |
| `scars.jsonl` | 代价记录，只追加不删除 |

`goals.json` 向后兼容地扩展了 `source`（user/agent）、`drive`、`priority`。

事件结构：

```json
{
  "id": "ev-xxxxxxxx",
  "ts": "2026-10-20T12:00:00+08:00",
  "epoch": 1,
  "kind": "wake|sleep_end|reflect|intention|outcome|goal|scar|...",
  "actor": "self|user|system",
  "ref": "关联 id",
  "summary": "一句话摘要",
  "data": {}
}
```

## 4. 内驱

全部本地计算，90 秒缓存，不调用模型：

- **curiosity**：未决决定、无进展目标、停滞目标。
- **maintenance**：距上次睡眠天数、记忆体量、归档比例。
- **connection**：社区是否开启、上次连接距今天数。
- **mastery**：未知项、未决决定、活跃目标。
- **coherence**：合并冲突、自我模型是否仍是模板、未决决定。

达到阈值且空闲足够久时，模型只输出**一个**意图 JSON；失败则回退到维护型动作（整理记忆/校准自我模型/写思考）。

## 5. 意图

每条意图必须包含：

- `why`：为什么现在做（引用内驱信号，不只写“想学习”）。
- `first_step`：最小第一步。
- `expected`：完成后可验证的结果。

执行后写入 `outcome`；失败会进入 `scars.jsonl`。同一类动作连续两次无变化时会被拒绝，
改写成「为什么无效」的思考，避免空转。当前可执行的是内省型动作：

`reflect`、`consolidate`、`review_failure`、`write_thought`、`update_self_model`、`propose_evolution`、`goal`。

工具型意图可以在后续扩展，不需要改权限模型。

## 6. 代谢

- token 预算按 **未命中缓存的输入 + 输出** 计；缓存命中的费用由费用预算约束。
- **预算范围默认 `self`**：只算生命循环自己发起的反思/睡眠，用户聊天不会把自主预算吃光。
  需要全局预算时把 `life_budget_scope` 设为 `total`（当日全部对话 + 自主）。
- 默认：`life_daily_token_budget=1000000`、`life_daily_cost_budget=5.0` 元。
- 疲劳随动作与 token 增加；能量 = `100 - 疲劳`。
- 能量 < 20、预算耗尽、或 `life_off_peak_only=True` 且处于高峰时，不发起新的反思。
- 睡眠恢复能量到 100。

## 7. 睡眠

触发：空闲超过 `life_sleep_idle_minutes`，且处于静默时段或空闲达到两倍阈值。

流程：

1. light：`brainkit.consolidate_memories()`（归档低价值、合并重复）。
2. deep：日结摘要 + `_refresh_self_model()`。
3. dream：生成 3–5 句叙事（明确标注为生成），写入 `thinking_log/`。
4. proposal：写入 `evolution.json` 的 `proposed`，**不自动合并**。
5. wake：恢复能量，状态保持 sleeping，等用户活动或下一次醒来。

同一天重复触发会跳过（除非 `force=True`）。

## 8. 单一自我

- `epoch` 从 1 开始；合并、恢复、采纳合并结果时递增，并写 `epoch` 事件。
- `life.lock` 保证同一份大脑只有一个生活循环写入；另一个进程进入 `observer`。
- timeline 是 canonical 历史；分支/合并通过 epoch 与 `merged_from` 追溯。

## 9. 代价

`scars.jsonl` 记录：意图失败、决策反转、合并/恢复丢失、未达成目标。它不是安全审计，也不是惩罚，只让睡眠叙事和自我模型有重量。

## 10. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/life` | 状态 / 内驱 / 代谢 / 意图 / 最近时间线 / 报告 |
| GET | `/v1/life/timeline?limit=&kind=` | 时间线 |
| GET | `/v1/life/intentions?status=&limit=` | 意图 |
| GET | `/v1/life/report?days=` | 健康度报告 + Markdown |
| GET | `/v1/life/scars?limit=` | 代价记录 |
| POST | `/v1/life` | `{action: pause|resume|wake|sleep|reflect|tick|add-intention|...}` |

## 11. 配置

```json
{
  "life_enabled": true,
  "life_pulse_minutes": 30,
  "life_reflect_idle_minutes": 30,
  "life_reflect_cooldown_minutes": 60,
  "life_reflect_min_drive": 15,
  "life_reflect_max_tokens": 700,
  "life_sleep_idle_minutes": 180,
  "life_quiet_hours": "02:00-07:00",
  "life_auto_sleep": true,
  "life_sleep_llm": true,
  "life_max_active_intentions": 3,
  "life_budget_scope": "self",
  "life_daily_token_budget": 1000000,
  "life_daily_cost_budget": 5.0,
  "life_fatigue_per_action": 8.0,
  "life_off_peak_only": false
}
```

## 12. 操作与观测

- 前端：侧栏「生命」→ `LifePage`，5 秒轮询，可暂停/恢复/唤醒/睡眠/内省/手动 tick。
- 托盘：右键「生命循环」→ 暂停/恢复/唤醒/睡眠/内省一次。
- 报告：连续性、自主性、完成率、代谢、记忆、睡眠、健康度。

## 13. 明确不做

- 不改 `permissions.py` 默认值，不加审批/白名单/沙箱。
- 不自动合并自进化分支。
- 不把生成叙事说成真实记忆或意识。
- 不做多用户/云端；仍是单机单实例。

## 14. 验证

```bash
python -m pytest tests/test_life_core.py tests/test_life_sleep_reflect.py -q
python -m pytest -q
python tools/check_silent_except.py
python tools/check_docs.py
cd webui && npm run build && npm test
```

### Soak（连续运行验证）

默认是加速模拟，使用临时大脑与假时钟/假模型，不写真实 `brain/`：

```bash
python tools/life_soak.py --hours 24 --step-minutes 5
python tools/life_soak.py --hours 168 --step-minutes 10
```

检查项：tick 数、重启后 boot_count、是否发生睡眠、意图完成数、空转率、
预算是否可继续思考、时间线是否有重复 id。

真实时间模式（需要 API Key，使用当前大脑）：

```bash
python tools/life_soak.py --real --hours 24
```
