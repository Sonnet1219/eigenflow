# EigenFlow 服务 API 使用手册

本文档覆盖 EigenFlow 项目中 **Agent 服务**（LangGraph 主编排）与 **Alert 服务**（保证金监控）两大组件的接口说明、启动方式以及常用测试方法，便于前端、风控和运维团队协作调试。

---

## 1. 总览

| 服务 | 默认端口 | 主要代码位置 | 功能概述 |
|------|----------|--------------|-----------|
| Agent Service | `8001` | `src/api/graph.py` | 接收人工或监控请求，调用 LangGraph 生成保证金风险分析报告，支持 HITL 复查与执行历史回溯 |
| Alert Service | `8002` | `alert_service/api.py` | 定时监控 LP 保证金，触发多级阈值告警并与 Agent 服务对接，支持复查、忽略、备注等人工动作 |

两者均基于 FastAPI 构建，默认返回 JSON；若请求头包含 `Accept: text/event-stream` 或查询参数 `?stream=1`，可启用 SSE 流式输出。

---

## 2. Agent 服务（`http://localhost:8001`）

### 2.1 启动方式

```bash
uvicorn src.api.app:app --host 0.0.0.0 --port 8001 --reload
```

常用环境变量：

- `MODEL_NAME`：调用的 LLM 名称（默认 `qwen-plus-latest`）
- `DASHSCOPE_API_KEY`：阿里 DashScope API Key
- `INTENT_CONFIDENCE_THRESHOLD`：LLM 意图分类置信度阈值（默认 `0.98`）

### 2.2 接口明细

#### POST `/agent/margin-check`

- **用途**：生成 LP 保证金风险分析报告，支持普通对话或监控告警直接触发。
- **请求字段**：
  - `messages`：可选，历史对话数组（仅保留 `type=human` / `role=user` 的内容）
  - `thread_id`：可选，会话 ID；留空时服务端按消息哈希生成
  - `eventType` / `payload`：可选，用于 `MARGIN_ALERT` 事件直达分析

示例 1：自定义请求

```bash
curl -X POST http://localhost:8001/agent/margin-check \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"type": "human", "content": "请生成[CFH] MAJESTIC FIN TRADE 的保证金风险报告"}
    ],
    "thread_id": "cfh_demo_001"
  }'
```

示例 2：监控告警触发

```bash
curl -X POST http://localhost:8001/agent/margin-check \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "MARGIN_ALERT",
    "payload": {
      "lp": "[CFH] MAJESTIC FIN TRADE",
      "marginLevel": 0.92,
      "threshold": 0.80
    },
    "thread_id": "alert_cfh_20250209"
  }'
```

响应（完成态 `type=complete`）：

```json
{
  "type": "complete",
  "status": "complete",
  "report": "【风险总览】CFH 当前保证金占用率 92%...",
  "thread_id": "cfh_demo_001"
}
```

若需要人工确认，会返回 `type=interrupt` 和 `interrupt_data`，其中包含 `margin_check_approval` 卡片内容，可由前端弹窗展示。

> **SSE 用法**：
> ```bash
> curl -N -H "Content-Type: application/json" \
>      -H "Accept: text/event-stream" \
>      "http://localhost:8001/agent/margin-check?stream=1" \
>      -d '{"messages":[{"type":"human","content":"生成保证金报告"}]}'
> ```
> 服务会按 `status` / `delta` / `progress` / `final` 事件逐步推送。

#### POST `/agent/margin-check/recheck`

- **用途**：在人类反馈（HITL）后重启分析，对最新数据生成复查报告。
- **请求字段**：
  - `thread_id`（必填）：原会话线程 ID
  - `messages`：可选，取首条 `human` 消息作为复查说明

示例：

```bash
curl -X POST http://localhost:8001/agent/margin-check/recheck \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "cfh_demo_001",
    "messages": [
      {"type": "human", "content": "已经补充 200 万 USD 保证金，请复查。"}
    ]
  }'
```

返回的 `report` 为重新生成的叙述性分析；SSE 调用方式与 `/margin-check` 相同。

#### POST `/agent/margin-check/history`

- **用途**：查询指定线程的 LangGraph 检查点历史，便于审计与调试。
- **请求体**：`{"thread_id": "cfh_demo_001"}`
- **响应**：按时间顺序列出 checkpoint 元数据、消息摘要、执行节点、任务栈等。

### 2.3 调试要点

- 日志关键字：`Invoking graph with initial_state`、`Supervisor subgraph produced no assistant content` 等。
- 返回的 `report` 字段已经处理过工具原始 JSON，确保得到自然语言文稿。如仍出现结构化内容，可检查日志确认 AI 渲染是否失败。
- `thread_id` 与 Alert 服务的 `traceId` 共同帮助串联 HITL 全流程。

---

## 3. Alert 服务（`http://localhost:8002`）

### 3.1 启动方式

```bash
uvicorn alert_service.app:app --host 0.0.0.0 --port 8002 --reload
```

常用环境变量（也可在 `.env` 配置）：

```bash
export ORCHESTRATOR_BASE_URL="http://localhost:8001"
export ALERT_MONITOR_INTERVAL=60
export ALERT_WARN_TRIGGER=15
export ALERT_WARN_CLEAR=10
export ALERT_CRITICAL_TRIGGER=25
export ALERT_CRITICAL_CLEAR=20
export ALERT_EMERGENCY_TRIGGER=35
export ALERT_EMERGENCY_CLEAR=30
export ORCHESTRATOR_TIMEOUT=150
export ALERT_BLOWUP_MARGIN_LEVEL=5
export ALERT_BLOWUP_SUPPRESS=300
```

### 3.2 核心路由

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/alert/start-monitoring` | 启动后台监控循环 |
| POST | `/alert/stop-monitoring` | 停止监控循环 |
| GET  | `/alert/monitoring-status` | 查看监控状态、阈值及所有活跃告警 |
| POST | `/alert/human-action` | 处理 HITL 操作（复查、忽略、备注）|
| GET  | `/alert/history/{trace_id}` | 查看单个告警的历史事件轨迹 |

#### POST `/alert/start-monitoring`

```bash
curl -X POST http://localhost:8002/alert/start-monitoring
```

若服务已运行，会返回 `{"status":"info","message":"Monitoring service already running"}`。

#### POST `/alert/stop-monitoring`

```bash
curl -X POST http://localhost:8002/alert/stop-monitoring
```

#### GET `/alert/monitoring-status`

示例响应：

```json
{
  "status": "running",
  "thresholds": {
    "warn": {"trigger": 15, "clear": 10},
    "critical": {"trigger": 25, "clear": 20},
    "emergency": {"trigger": 35, "clear": 30}
  },
  "interval": 60,
  "blowupMarginLevel": 5,
  "alerts": {
    "[CFH] MAJESTIC FIN TRADE": {
      "severity": "critical",
      "marginLevel": "27.80",
      "traceId": "monitor_CFH_critical_1728819210",
      "threadId": "margin_check_-4060508849888328085",
      "latestReport": "【风险总览】..."
    }
  }
}
```

#### POST `/alert/human-action`

- **action = "recheck"**：触发复查，可流式或一次性返回

```bash
curl -N -H "Content-Type: application/json" \
     -H "Accept: text/event-stream" \
     -d '{
       "traceId": "monitor_CFH_critical_1728819210",
       "threadId": "margin_check_-4060508849888328085",
       "action": "recheck",
       "message": "已追加 200 万美元保证金，请复查"
     }' \
     "http://localhost:8002/alert/human-action?stream=1"
```

- **action = "ignore"**：忽略指定分钟数

```bash
curl -X POST http://localhost:8002/alert/human-action \
  -H "Content-Type: application/json" \
  -d '{
    "traceId": "monitor_CFH_critical_1728819210",
    "action": "ignore",
    "ignoreMinutes": 240,
    "message": "盘前不处理"
  }'
```

- **action = "comment"**：记录备注，无状态切换

```bash
curl -X POST http://localhost:8002/alert/human-action \
  -H "Content-Type: application/json" \
  -d '{
    "traceId": "monitor_CFH_critical_1728819210",
    "action": "comment",
    "message": "等待市场回稳"
  }'
```

#### GET `/alert/history/{trace_id}`

```bash
curl http://localhost:8002/alert/history/monitor_CFH_critical_1728819210
```

返回字段包含：事件类型、发生时间、主编排返回的报告片段、人工操作记录等。

### 3.3 监控工作流摘要

1. `MonitoringService` 按 `ALERT_MONITOR_INTERVAL` 定期调用 `EigenFlowAPI` 拉取账户数据。
2. `AlertStateManager` 对照阈值 (`trigger`/`clear`) 判断 `warn/critical/emergency`，并执行迟滞、节流、忽略控制。
3. 命中阈值时构造事件调用 Agent 服务 `/agent/margin-check`，保留 `traceId` 和 `threadId`。
4. 若返回 `interrupt`，状态标记为 `awaiting_hitl`，等待 `/alert/human-action` 结果。
5. 后续监测到保证金回落至 `clear` 以下，会自动触发 `autoCleared` 并记录。
6. 若满足爆仓条件（`equity <= margin` 或 `marginLevel < ALERT_BLOWUP_MARGIN_LEVEL`），发送 `ACCOUNT_BLOWUP` 事件，并遵守 `ALERT_BLOWUP_SUPPRESS` 的冷却策略。
7. 日志前缀：`[ALERT]`、`[ALERT->ORCH]`、`[ALERT][AI]`、`[ALERT][HITL]`、`[ALERT][IGNORED]` 等，便于过滤追踪。

---

## 4. 测试与验证

### 4.1 单元测试建议

| 模块 | 目标 | 思路 |
|------|------|------|
| Agent Service | `/agent/margin-check`、`recheck`、`history` | 使用 `FastAPI TestClient` + `pytest-asyncio`，mock LangGraph `graph.ainvoke` / `graph.astream_events` / `graph.aget_state_history`，验证普通流程、MARGIN_ALERT、异常路径 |
| Alert Service | 监控处理、HITL、爆仓检测 | mock `EigenFlowAPI`、`orchestrator_client`，针对阈值命中、忽略节流、自动解除、爆仓触发等分支断言状态变化 |

### 4.2 集成测试示例

1. 启动两个服务：
   ```bash
   uvicorn src.api.app:app --port 8001 &
   uvicorn alert_service.app:app --port 8002 &
   ```
2. 启动监控并手工触发告警：
   ```bash
   curl -X POST http://localhost:8002/alert/start-monitoring
   curl -X POST http://localhost:8001/agent/margin-check \
        -H "Content-Type: application/json" \
        -d '{"eventType":"MARGIN_ALERT","payload":{"lp":"[CFH] MAJESTIC FIN TRADE","marginLevel":0.92,"threshold":0.8}}'
   ```
3. 查看监控状态与报告：
   ```bash
   curl http://localhost:8002/alert/monitoring-status | jq .alerts
   curl http://localhost:8001/agent/margin-check/history -H "Content-Type: application/json" -d '{"thread_id":"..."}'
   ```
4. 模拟复查：
   ```bash
   curl -X POST http://localhost:8002/alert/human-action \
     -H "Content-Type: application/json" \
     -d '{"traceId":"...","threadId":"...","action":"recheck","message":"已追加保证金"}'
   ```

### 4.3 故障排查

- **`Successfully transferred to ...` 出现在报告中**：说明 LangGraph 只返回了 handoff 提示，检查 `call_supervisor` 逻辑是否成功渲染工具输出。
- **日志中出现 `wrote to unknown channel branch:to:ai_responder`**：代表 supervisor 手动添加的 handoff 与子图分支不匹配，可取消自定义 handoff 或补齐分支。
- **长时间无响应**：确保 DashScope API Key / EigenFlow API 网络连通；必要时提高 `ORCHESTRATOR_TIMEOUT`。

---

## 5. 名词释义

- **LP (Liquidity Provider)**：流动性提供商账户。
- **HITL (Human-in-the-loop)**：人工审核环节。
- **`traceId` / `threadId`**：告警在监控系统与 LangGraph 中的双重标识，追踪全流程时需成对使用。
- **`report` 字段**：经 AI 渲染后的自然语言分析；`content` 与其保持一致。

---

如需扩展新的告警类型、替换数据源或对接更多执行动作，可在对应服务的 `config.py`、`state_manager.py` 与 LangGraph 流程中扩展。欢迎根据业务要求继续迭代引导文案、报告模板与执行闭环。
