# Alert Service

围绕 LP 保证金的监控、告警与 HITL 流程的独立编排服务。该组件常驻运行，定期从 EigenFlow Data Gateway 拉取账户快照，判断是否触发阈值，再将标准化事件投递到主编排 (LangGraph) 服务，最后把返回的建议、卡片、审核状态反馈给前端或下游系统。

---

## 总览

- **监听端口**：默认 `8002`
- **核心依赖**：`fastapi`, `httpx`, `EigenFlowAPI`
- **协作对象**：主编排服务 (默认 `http://localhost:8001`)
- **核心能力**：多级阈值监控、迟滞防抖、告警节流、HITL 支撑、自动解除、爆仓检测、AI 建议日志

目录结构：

```
alert_service/
├── api.py                # FastAPI 路由 & 监控服务逻辑
├── app.py                # FastAPI 实例、CORS 配置
├── config.py             # 监控阈值 & 节流等配置模型
├── main.py               # 启动入口 (uvicorn)
├── models.py             # 告警状态、HITL 请求的 Pydantic 模型
├── orchestrator_client.py# 调主服务的 httpx 异步客户端
├── state_manager.py      # 告警状态机、迟滞/节流控制
└── README.md             # 本说明
```

---

## 快速开始

1. **安装依赖**（项目根目录执行）：
   ```bash
   pip install -r requirements.txt  # 或使用 poetry/pdm 等
   ```

2. **配置环境变量（可选，根据实际部署调整）**：
   ```bash
   export ORCHESTRATOR_BASE_URL="http://localhost:8001"
   export ALERT_MONITOR_INTERVAL=60
   export ALERT_WARN_TRIGGER=80
   export ALERT_WARN_CLEAR=75
   export ALERT_CRITICAL_TRIGGER=90
   export ALERT_CRITICAL_CLEAR=85
   export ALERT_EMERGENCY_TRIGGER=95
   export ALERT_EMERGENCY_CLEAR=90
   export ORCHESTRATOR_TIMEOUT=150
   export ALERT_BLOWUP_MARGIN_LEVEL=5
   export ALERT_BLOWUP_SUPPRESS=300
   ```
   > 未设置时，会使用 `config.py` 中的默认值。

3. **启动服务**：
   ```bash
   python alert_service/main.py
   ```
   默认会在 `http://0.0.0.0:8002` 暴露接口。

4. **启动监控循环**：
   ```bash
   curl -X POST http://localhost:8002/alert/start-monitoring
   ```

5. **停止监控循环**：
   ```bash
   curl -X POST http://localhost:8002/alert/stop-monitoring
   ```

---

## 核心工作流

1. **定时拉取**：`MonitoringService` 按 `ALERT_MONITOR_INTERVAL` 定期调用 `EigenFlowAPI.get_lp_accounts()` 获取各 LP 的保证金快照。
2. **多级阈值与迟滞**：对照 `warn/critical/emergency` 阈值判断触发级别，并使用 `clear` 阈值做迟滞，防止在阈值附近抖动。
3. **节流 & 忽略**：
   - `initial_intervals` 覆盖前几次提醒频率（默认 1min, 1min, 1min, 5min）。
   - `steady_state_interval` 控制后续提醒节奏（默认 15min）。
   - 用户点击卡片的 Ignore 后，`ignoreUntil` 内不会再次推送。
4. **触发主编排**：组装标准事件 `{triggerType:"monitor", traceId, slots, occurredAt, payload}`，调用 `/agent/margin-check`。
5. **AI 报告日志**：主服务返回后，`[ALERT][AI]` 日志会截取前 800 字的建议/报告摘要。
6. **HITL**：若响应 `type=interrupt`，监控状态置为 `awaiting_hitl`，等待前端通过 `/alert/human-action` 反馈。
7. **自动解除**：若后续检测发现 margin 跌回 `clear` 以下，则发送 `autoCleared` 事件并标记为 `AUTO_CLEARED`。
8. **爆仓检测**：满足 `equity <= margin`、`marginLevel < ALERT_BLOWUP_MARGIN_LEVEL` 或存在强平标记时，触发 `ACCOUNT_BLOWUP` 事件，并按 `ALERT_BLOWUP_SUPPRESS` 控制重复告警。

---

## API 说明

| 方法 | 路径 | 描述 |
|------|------|------|
| GET  | `/` | 服务运行测试 |
| GET  | `/health` | 健康检查 |
| POST | `/alert/start-monitoring` | 启动后台监控任务 |
| POST | `/alert/stop-monitoring` | 停止监控任务 |
| GET  | `/alert/monitoring-status` | 查看监控状态、阈值配置、活动告警（含 `traceId`/`threadId`） |
| POST | `/alert/human-action` | 接收前端 HITL 操作 (Re-check / Ignore / Comment) |

### `/alert/human-action` 请求示例

- **复查**：
  ```json
  {
    "traceId": "monitor_[GBEGlobal]GBEGlobal1_emergency_1758195430",
    "threadId": "margin_check_-123456789",
    "action": "recheck",
    "message": "已补仓，请复查"
  }
  ```
- **忽略 4 小时**：
  ```json
  {
    "traceId": "monitor_[GBEGlobal]GBEGlobal1_emergency_1758195430",
    "action": "ignore",
    "ignoreMinutes": 240,
    "message": "与风控确认暂时忽略"
  }
  ```
- **备注**：
  ```json
  {
    "traceId": "monitor_[GBEGlobal]GBEGlobal1_emergency_1758195430",
    "action": "comment",
    "message": "等待市场回稳后再处理"
  }
  ```

返回体会回传主编排的响应（`type=complete/interrupt`）或忽略状态。

---

## 日志约定

监控流程遵循统一前缀，便于快速 grep：

- `[ALERT]`：监控触发/总体状态（含 LP、traceId、severity）。
- `[ALERT->ORCH]`：向主编排发起调用。
- `[ALERT][AI]`：主编排返回的建议摘要（前 800 字）。
- `[ALERT][HITL]`：等待或仍需人工审批的提示。
- `[ALERT][RESOLVED]` / `[ALERT][AUTO-CLEAR]` / `[ALERT][IGNORED]`：各类关闭动作。
- `[ALERT][COMMENT]`：记录用户备注。

借助 `traceId`，可以在日志、`monitoring-status`、LangGraph 历史接口间轻松串连同一告警的生命周期。

---

## 测试建议

- 使用 `pytest` / `pytest-asyncio` 对 `MonitoringService._process_account` 做单元测试，mock `EigenFlowAPI` 和 `orchestrator_client`，验证阈值命中、节流、忽略、自动解除等路径。
- 利用 `FastAPI TestClient` 或集成测试脚本，分别覆盖手动触发、HITL 回调、爆仓事件。
- 检查日志是否按预期输出 traceId 和建议摘要。

---

## 常见问题

1. **总是提示 `Alert requires human approval`？** 说明主编排返回了 interrupt，需要通过 `/alert/human-action` 传回结果，或选择忽略。
2. **频繁重复触发？** 检查是否未达到解除阈值或忽略时长已过；必要时调整阈值/节流策略。
3. **读取 Data Gateway 超时？** 调整 EigenFlow API 的重试/超时配置，或在监控服务里增加异常回退逻辑（默认已经记录 WARN 并继续下一轮）。
4. **前端如何拿到报告？** 监控服务已经在内存中拿到 LangGraph 的完整响应，可在 `_process_account` 中转发给消息中间件或直接推送给前端。也可以通过 `/agent/margin-check/history` 查历史。

---

如需扩展新类型告警或切换告警存储方式（Redis/DB），可在 `AlertStateManager` 替换策略实现；监控循环本身不依赖具体存储。欢迎根据业务需求扩展。祝使用顺利！
