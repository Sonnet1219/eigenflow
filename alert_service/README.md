# Alert Service

独立的告警监控服务，专注于LP保证金监控和告警检测。

## 目录结构

```
alert_service/
├── __init__.py          # 包初始化文件
├── main.py             # 服务启动入口
├── app.py              # FastAPI应用配置
├── api.py              # API端点定义
└── README.md           # 服务说明文档
```

## 启动服务

```bash
# 在项目根目录下运行
python -m alert_service.main
```

服务将在 http://localhost:8002 启动

## API端点

- `GET /` - 服务状态检查
- `GET /health` - 健康检查
- `POST /alert/start-monitoring` - 启动监控服务
- `POST /alert/stop-monitoring` - 停止监控服务
- `GET /alert/monitoring-status` - 获取监控状态
- `POST /alert/test-alert` - 测试告警功能

## 服务特性

1. **独立进程**: 运行在8002端口，避免自循环调用问题
2. **外部调用**: 通过HTTP调用主服务(8001端口)的margin-check接口
3. **专注职责**: 只负责数据监控和告警检测
4. **清晰架构**: 独立的目录结构，避免代码混淆

## 配置

- `MONITORING_INTERVAL`: 监控间隔(秒)，默认60秒
- `MARGIN_THRESHOLD`: 保证金阈值(百分比)，默认20%
- `MAIN_SERVICE_ENDPOINT`: 主服务接口地址
