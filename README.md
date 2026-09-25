# 出口商品国际市场观测报告工具

中国出口商品国际市场观测报告自动化生成系统：海关数据 → 指标计算 → 新闻聚合 → AI 洞察 → PDF 报告。

## 项目结构

```
.
├── config/             # 配置层
│   ├── settings.py          # 全局配置（pydantic-settings，从 .env 读取）
│   ├── database_binds.py    # 三库分离：引擎工厂、建表、Session 管理
│   └── hs_table.json        # 品类映射表（10 个高频品类 + 口语词）
├── core/               # 核心业务（不依赖 web 层）
│   ├── models/              # 海关库模型：ExportRecord / HSMaster / DataImportBatch
│   ├── parser.py            # CSV 智能解析（编码探测、列名模糊匹配、金额/日期归一）
│   ├── metrics.py           # 指标计算：同比/环比、份额、集中度、排名、雷达得分
│   ├── hs_service.py        # 品类映射与 HS6 主数据服务
│   ├── repository.py        # 仓储 + CSV 导入服务（幂等、溯源、健康度）
│   ├── charts.py            # 图表生成（市场份额饼图、增长柱状图、雷达图）
│   ├── report.py            # PDF 报告生成（封面、核心指标、市场结构、洞察）
│   ├── report_task.py       # 报告任务状态跟踪
│   ├── news.py              # 新闻聚合（GDELT / NewsAPI / Bing News，兜底模拟）
│   ├── ai_insight.py        # AI 洞察（DeepSeek，无 Key 走模板兜底）
│   └── security.py          # 密码哈希、JWT 签发与校验
├── web/                # Web 层
│   ├── main.py              # FastAPI 应用入口
│   ├── api/                 # 用户 API（8 个模块）
│   │   ├── deps.py          # 鉴权依赖：当前用户、管理员、数据库 Session
│   │   └── schemas/         # Pydantic 请求/响应模型
│   ├── admin/endpoints/     # 管理后台 API
│   └── models/
│       ├── user_models.py   # 用户库：User / Subscription / Report / Payment / InviteCode ...
│       └── content_models.py# 内容库：SiteConfig / Page / Announcement ...
├── worker/             # Celery 异步任务
│   ├── celery_app.py        # Celery 配置 + 定时任务（每月数据检查、月报生成）
│   └── tasks.py             # 任务定义
├── frontend/           # 前端 SPA（原生 JS + Vite）
│   ├── index.html
│   ├── vite.config.js
│   └── src/
│       ├── main.js          # 路由 + 布局
│       ├── api/client.js    # API 封装（自动携带 Token）
│       ├── views/           # 页面：报告生成、报告详情、登录/注册
│       └── styles/          # 样式
├── data/               # 运行时数据目录
│   ├── inbound/             # 待导入 CSV
│   ├── reports/             # 生成报告
│   ├── charts/              # 图表产物
│   └── hs/                  # HS6 主数据
├── scripts/            # 运维脚本
│   ├── import_csv.py        # CSV 导入（命令行）
│   └── run_worker.py        # 开发态报告生成（无需 Celery）
├── deploy/             # 部署文档
└── tests/              # 单元测试
```

## API 端点一览（共 35 个）

| 模块 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 认证 | POST | /api/auth/register | 注册（手机号/邮箱） |
| 认证 | POST | /api/auth/login | 登录 |
| 认证 | GET | /api/auth/me | 当前用户资料 |
| 认证 | PUT | /api/auth/me | 更新资料 |
| 认证 | POST | /api/auth/change-password | 修改密码 |
| 认证 | POST | /api/auth/verify-code | 发送验证码 |
| 品类 | GET | /api/categories/search | 搜索品类 |
| 品类 | GET | /api/categories/{hs_code} | 品类详情 |
| 数据 | GET | /api/data/overview | 数据概览 |
| 数据 | GET | /api/data/market-share | 市场份额 |
| 数据 | GET | /api/data/growth | 增长/下滑分桶 |
| 数据 | GET | /api/data/rank-changes | 排名变化 |
| 数据 | GET | /api/data/opportunity | 机会度评估 |
| 数据 | GET | /api/data/periods | 可用数据期 |
| 报告 | POST | /api/reports/generate | 生成报告 |
| 报告 | GET | /api/reports/tasks/{id} | 任务状态 |
| 报告 | GET | /api/reports/tasks | 任务列表 |
| 报告 | GET | /api/reports/download/{id} | 下载报告 |
| 订阅 | GET/POST | /api/subscriptions | 订阅列表/创建 |
| 订阅 | POST | /api/subscriptions/activate | 邀请码兑换 |
| 订阅 | POST | /api/subscriptions/{id}/pause | 暂停订阅 |
| 订阅 | POST | /api/subscriptions/{id}/resume | 恢复订阅 |
| 订阅 | GET/POST | /api/subscriptions/admin/invite-codes | 管理员邀请码管理 |
| 新闻 | GET | /api/news | 获取相关新闻 |
| 洞察 | GET | /api/insight | AI 市场洞察 |
| 管理 | GET/PUT | /api/admin/content/config | 网站配置 |
| 管理 | GET/POST/PUT/DELETE | /api/admin/content/pages | 静态页面管理 |
| 管理 | GET/POST/PUT/DELETE | /api/admin/content/announcements | 公告管理 |
| 管理 | POST | /api/admin/data/import | 数据导入 |
| 管理 | GET | /api/admin/data/batches | 导入批次列表 |
| 管理 | GET | /api/admin/data/health | 数据健康度自检 |

## 数据层架构：三库分离

| bind | 内容 | 模型文件 |
|------|------|----------|
| `bind_trade` | 海关明细、HS6 主数据、导入批次 | `core/models/` |
| `bind_user` | 用户、订阅、报告、付费、邀请码、验证码、登录日志 | `web/models/user_models.py` |
| `bind_content` | 网站配置、静态页面、公告、导航菜单 | `web/models/content_models.py` |

业务代码只通过 `get_session(bind)` 获取 Session，禁止跨库 JOIN。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 重点修改：DATABASE_*_URL、JWT_SECRET
# 可选：DEEPSEEK_API_KEY、NEWSAPI_KEY、BING_NEWS_KEY

# 3. 导入海关 CSV（自动识别 GB18030、幂等）
python scripts/import_csv.py /path/to/data.csv

# 4. 启动 API 服务（文档地址 http://localhost:8000/docs）
uvicorn web.main:app --reload --port 8000

# 5. 开发态直接生成报告（无需 Celery/Redis）
python scripts/run_worker.py 950691 2026-08
```

## 开发态注意事项

SQLite 对文件系统锁的实现依赖挂载选项。默认策略：

1. 优先 `/tmp`（tmpfs，可读写，允许 journal 文件）
2. 其次 `/dev/shm`
3. 最后回退项目内 `data/` 目录

所有 SQLite 引擎已强制 `journal_mode=DELETE` 与 `temp_store=MEMORY`。
生产部署一律使用 PostgreSQL，不受此策略影响。

## 降级与兜底策略

系统对外部依赖做了完整降级设计，任一服务不可用都不会中断报告生成：

| 依赖 | 降级方案 |
|------|----------|
| DeepSeek API | 模板化文案（基于真实统计数据） |
| 新闻源（GDELT/NewsAPI/Bing） | 模拟数据，结构一致 |
| 海关数据 | 使用本地最新已有数据，报告标注截止月份 |
| Celery/Redis | 开发态直接同步执行 |

## 部署

详见 `deploy/README.md`，包含 PostgreSQL 建库、环境变量、Nginx 配置、systemd 服务等。

## 验证状态

- 真实数据测试：HS950691（健身器材）2026年3-8月，1109 条记录，218 个国家/地区
- 报告生成：5 页 PDF，368KB，中文显示正常，图表清晰
- 认证链路：注册→登录→鉴权→权限校验全部通过
- 35 个 API 端点已在 OpenAPI 文档中注册并验证
