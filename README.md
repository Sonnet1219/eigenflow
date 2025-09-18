# EigenFlow Multi-Agent System

基于 **LangGraph** + **PostgreSQL** + **FastAPI** 构建的生产级多智能体金融风险分析系统。

## ✨ 核心特性 Core Features

- 🤖 **智能意图分类 Intent Classification**: 自动识别用户查询类型，精准路由到对应智能体
- 📊 **实时保证金监控 Real-Time Margin Monitoring**: 集成 EigenFlow API，提供 LP 账户风险分析和交叉净额建议
- 🔄 **人机协作工作流 Human-in-the-Loop**: 关键决策节点支持人工审核和反馈
- 💾 **会话状态持久化 Session Persistence**: 基于 AsyncPostgresSaver 的检查点机制，支持长对话记忆
- ⚡ **全异步架构 Full Async Architecture**: 从数据库到 API 的完整异步实现，支持高并发
- 🔍 **执行历史追踪 Execution History**: 详细的检查点历史记录，支持审计和调试
- 🛠️ **结构化数据解析 Structured Data Parsing**: 自动解析 JSON 响应和意图上下文为可读格式

## 🏗️ 系统架构 System Architecture

### 核心组件 Core Components

```
src/
├── agent/              # 多智能体核心
│   ├── graph.py        # 主图 + 监督者子图架构
│   ├── state.py        # 状态管理和数据流
│   ├── mcp.py          # EigenFlow API 工具集成
│   └── prompts.py      # 智能体提示词模板
├── api/                # FastAPI 服务层
│   ├── app.py          # 应用生命周期管理
│   └── graph.py        # 对话和历史查询接口
└── db/                 # 异步数据库层
    ├── database.py     # 连接池管理
    └── checkpoints.py  # 检查点持久化
```

### 工作流架构 Workflow Architecture

1. **意图分类节点** → 识别用户查询类型
2. **监督者子图** → 调用相应工具和生成响应  
3. **人工审核节点** → 保证金报告需人工确认
4. **检查点持久化** → 自动保存会话状态

## 🚀 快速开始 Quick Start

### 1. 安装依赖 Install Dependencies

```bash
pip install .
```

### 2. 环境配置 Environment Setup

复制 `.env.example` 为 `.env` 并配置必要参数：

```bash
cp .env.example .env
```

**必填配置项 Required Configuration:**
```env
# LangSmith 可观测性 (可选) Observability (optional)
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=eigenflow

# 大模型 API Key Model API Key
DASHSCOPE_API_KEY=your_dashscope_key

# 数据库连接 Database Connection
DATABASE_URL="postgresql://user:password@host:port/database"

# EigenFlow API 配置 API Configuration
EIGENFLOW_EMAIL=your_email
EIGENFLOW_PASSWORD=your_password
EIGENFLOW_BROKER=your_broker

# Supabase 设置 (本地部署) Local deployment
SUPABASE_URL=http://localhost:8000
SUPABASE_ANON_KEY=your_anon_key
```

### 3. 启动服务 Start Services

```bash
python -m src.main
```

- 🌐 API 服务 API Service：`http://localhost:8001`
- 📖 API 文档 Documentation：`http://localhost:8001/docs`

### 4. 开发模式 Development Mode (可选)

启用 LangGraph 可视化开发：
```bash
langgraph dev
```

## 🚀 核心功能 Core Functions

### 1. 智能保证金分析 Intelligent Margin Analysis

- **实时数据获取**: 直接调用 EigenFlow API 获取 LP 账户数据
- **风险评估**: 自动计算保证金水平和风险指标
- **交叉净额建议**: 识别可优化的持仓对冲机会
- **结构化报告**: 生成详细的风险分析和操作建议

### 2. 会话状态管理 Session Management

- **检查点持久化**: 每个对话步骤自动保存到 PostgreSQL
- **历史查询**: 支持按 thread_id 查询完整执行历史
- **状态恢复**: 支持中断后恢复对话上下文

### 3. API 接口 API Endpoints

```bash
# 发起对话
POST /agent/margin-check
{
  "messages": [{"role": "user", "content": "检查一下当前LP账户的保证金水平"}],
  "thread_id": "session-123"
}

# 重新检查
POST /agent/margin-check/recheck
{
  "messages": [{"role": "user", "content": "我已经按建议清算了部分对冲头寸，请你重新检查一下当前LP账户的保证金水平是否健康"}],
  "thread_id": "session-123"
}

# 查询执行历史
POST /agent/margin-check/history
{
  "thread_id": "session-123"
}
```

## 🔧 技术栈 Tech Stack

- **LangGraph**: 多智能体工作流编排
- **FastAPI**: 异步 Web 框架
- **PostgreSQL**: 检查点和状态持久化
- **Qwen**: 大语言模型（通义千问）
- **EigenFlow API**: 金融数据源

## 📚 相关文档 Documentation

- 🎯 [LangGraph 官方文档](https://langchain-ai.github.io/langgraph/)
- 🤖 [LangGraph Supervisor](https://github.com/langchain-ai/langgraph-supervisor-py)
- 📋 [Supabase 部署指南](resource/supabase_docker_guide.md)

---

**生产级多智能体金融风险分析系统，支持实时保证金监控和智能决策辅助。**
