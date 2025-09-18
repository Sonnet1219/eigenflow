# 🛠 Supabase 本地自部署全流程指南

## 📋 目录

* [概述](#概述)
* [准备环境](#准备环境)
* [配置环境变量](#配置环境变量)
* [启动服务](#启动服务)
* [访问 Dashboard](#访问-dashboard)
* [启用 pgvector 扩展](#启用-pgvector-扩展)
* [数据库连接](#数据库连接)
* [常见问题与解决方案](#常见问题与解决方案)

---

## 概述

本指南基于 [Supabase 官方 Docker 自托管文档](https://supabase.com/docs/guides/self-hosting/docker)，并结合实践经验，详细说明如何从零开始部署本地 Supabase 环境，包括 `.env` 配置、服务启动、Dashboard 登录，以及在 SQL Editor 启用 **pgvector** 扩展。

---

## 准备环境

1. 安装依赖：

   * [Docker](https://docs.docker.com/get-docker/)
   * [Docker Compose](https://docs.docker.com/compose/)

2. 克隆官方仓库：

   ```bash
    # Get the code
    git clone --depth 1 https://github.com/supabase/supabase

    # Make your new supabase project directory
    mkdir supabase-project

    # Tree should look like this
    # .
    # ├── supabase
    # └── supabase-project

    # Copy the compose files over to your project
    cp -rf supabase/docker/* supabase-project

    # Copy the fake env vars
    cp supabase/docker/.env.example supabase-project/.env

    # Switch to your project directory
    cd supabase-project

    # Pull the latest images
    docker compose pull
   ```

---

## 配置环境变量

在supabase-project目录下的`.env`中修改以下变量：

```env
# 数据库配置
POSTGRES_PASSWORD=
POSTGRES_DB=

# Supabase 配置（https://supabase.com/docs/guides/self-hosting/docker#jwt-secret 页面找到官方提供的表单生成）
JWT_SECRET=
ANON_KEY=
SERVICE_ROLE_KEY=

# Dashboard 登录账号
DASHBOARD_USERNAME=
DASHBOARD_PASSWORD=

# 默认服务端口
KONG_HTTP_PORT=8000
```

👉 注意：

* **JWT\_SECRET**：必须和 Supabase 容器保持一致，直接用官方页面提供的。
* **ANON\_KEY / SERVICE\_ROLE\_KEY**： 与JWT_SECRET配套，建议直接用官方推荐生成器生成。
* **DASHBOARD\_USERNAME / PASSWORD**：是登录 Dashboard 的账号密码，自行设置。

---

## 启动服务

在 `docker` 目录执行：

```bash
docker compose up -d
```

检查服务状态：

```bash
docker ps
```

应能看到这些容器：

* `supabase-db` (Postgres)
* `supabase-kong` (API Gateway)
* `supabase-studio` (Dashboard)
* 其他如 `auth`、`rest`、`realtime`

---

## 访问 Dashboard

浏览器打开：

```
http://localhost:8000
```

输入 `.env` 中设置的：

* **DASHBOARD\_USERNAME**
* **DASHBOARD\_PASSWORD**

即可登录 Supabase Studio。

---

## 启用 pgvector 扩展

进入 **Dashboard → SQL Editor**，执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## 默认用户授权
进入 **Dashboard → SQL Editor**，执行：

```sql
-- 确保 postgres 用户有 schema 的使用和创建权限
GRANT USAGE ON SCHEMA public TO postgres;
GRANT CREATE ON SCHEMA public TO postgres;

-- 允许 postgres 管理 public 下已有的所有表
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;

-- 允许 postgres 管理 public 下已有的所有序列（自增 id）
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;

-- 确保以后新建的表/序列自动继承权限
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT ALL PRIVILEGES ON TABLES TO postgres;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT ALL PRIVILEGES ON SEQUENCES TO postgres;
```

⚠️ 注意：

* 必须使用超级用户或具备足够权限的角色执行。

---

## 数据库连接

本地连接字符串示例：

```bash
psql "postgresql://postgres.your-tenant-id:eulerai-12345@127.0.0.1:5432/eulerai-arxiv?sslmode=disable"
```

应用内可使用 `.env` 中的：

```env
DATABASE_URL="postgresql://postgres.your-tenant-id:eulerai-12345@127.0.0.1:5432/eulerai-arxiv?sslmode=disable"
```

---

## 常见问题与解决方案

### 1. `type "vector" does not exist`

* **原因**：pgvector 扩展未启用。
* **解决**：进入 SQL Editor，执行 `CREATE EXTENSION vector;`

### 2. Dashboard 登录失败

* 检查 `.env` 中 `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` 是否正确。
* 确认 `KONG_HTTP_PORT` 未被占用。

### 3. 数据库连接报错 `Tenant or user not found`

* 确认连接字符串是否为多租户格式：

  ```text
  postgresql://postgres.your-tenant-id:password@127.0.0.1:5432/dbname
  ```

---

## 总结

1. 修改 `.env` 配置数据库和 Supabase 参数。
2. 使用 `docker compose up -d` 启动服务。
3. 访问 `http://localhost:8000` 登录 Dashboard。
4. 在 SQL Editor 执行 `CREATE EXTENSION vector;` 启用 pgvector。
5. 使用 `DATABASE_URL` 在应用中访问数据库。

