# Distributed Task Platform

分布式任务管理平台。当前版本提供 FastAPI API、MySQL 持久化、Redis 消息代理、
Celery Worker、JWT 登录认证、任务状态查询，以及 Outbox 可靠派发。
内置 `sum_numbers` 示例任务，用于验证异步执行链路。
还支持上传 CSV，由 Celery 异步统计行数、缺失值及数值列分布。

## 目录说明

    app/
    ├── api/          HTTP 路由、依赖注入和 API 版本管理
    ├── core/         配置、安全、JWT、密码哈希和 Redis 客户端
    ├── db/           SQLAlchemy Base、异步引擎和 Session
    ├── dispatcher/   扫描 Outbox、回收超时任务并派发到 Redis/Celery
    ├── middleware/   后续放置 request_id、日志和限流中间件
    ├── models/       SQLAlchemy 数据库模型
    ├── repositories/ 数据访问层，集中维护 SQLAlchemy 查询
    ├── scheduler/    后续实现定时规则扫描和任务实例生成
    ├── schemas/      Pydantic 请求及响应模型
    ├── services/     应用服务与跨模型业务事务边界
    ├── tasks/        Celery 任务处理器和示例任务
    ├── celery_app.py Celery 应用及可靠消费基础配置
    └── main.py       FastAPI 应用入口和生命周期管理

    alembic/           数据库迁移环境和版本文件
    tests/             单元测试与后续集成测试
    Dockerfile         API、迁移服务和 Worker 共用的应用镜像
    docker-compose.yml 本地 MySQL、Redis、迁移、API、派发器和 Worker 编排
    pyproject.toml      Python 依赖、构建和工具配置

## 快速启动

在项目根目录复制配置模板，并将 .env 中的 SECRET_KEY 改为随机密钥：

    Copy-Item .env.example .env
    python -c "import secrets; print(secrets.token_urlsafe(48))"

将输出填入 .env 的 SECRET_KEY，然后启动：

    docker compose up --build

启动后：

- Swagger UI：http://localhost:8000/docs
- 存活检查：http://localhost:8000/api/v1/health/live
- 就绪检查：http://localhost:8000/api/v1/health/ready

在 VS Code 的 PowerShell 终端注册、登录并查询当前用户：

    $baseUrl = "http://localhost:8000/api/v1"
    $registration = Invoke-RestMethod -Method Post -Uri "$baseUrl/auth/register" -ContentType "application/json" -Body '{"username":"demo","email":"demo@example.com","password":"password123"}'
    $token = Invoke-RestMethod -Method Post -Uri "$baseUrl/auth/login" -Body @{username="demo"; password="password123"}
    Invoke-RestMethod -Method Get -Uri "$baseUrl/users/me" -Headers @{Authorization="Bearer $($token.access_token)"}

登录接口使用 OAuth2 密码表单，Swagger UI 的 Authorize 按钮也可以直接调用。JWT 默认 30 分钟过期。

## 任务接口

以下命令接续上面的 PowerShell 示例，使用登录后获得的 `$token`：

    $headers = @{Authorization="Bearer $($token.access_token)"}
    $task = Invoke-RestMethod -Method Post -Uri "$baseUrl/tasks" -Headers $headers -ContentType "application/json" -Body '{"task_type":"sum_numbers","parameters":{"numbers":[2,3,5],"delay_seconds":2}}'
    $task.task_id
    Invoke-RestMethod -Method Get -Uri "$baseUrl/tasks/$($task.task_id)" -Headers $headers
    Invoke-RestMethod -Method Get -Uri "$baseUrl/tasks/$($task.task_id)/result" -Headers $headers

- `POST /api/v1/tasks`：创建任务，返回 HTTP 202 和 `task_id`。
- `GET /api/v1/tasks/{task_id}`：查询 `PENDING`、`RUNNING`、`SUCCESS` 或 `FAILED`。
- `GET /api/v1/tasks/{task_id}/result`：任务未结束时返回 HTTP 202；结束后返回 HTTP 200 和结果或错误类别。
- 只有创建任务的用户可以查询它；其他用户收到 404。
- 示例任务只接受 1～1000 个整数，可用 `delay_seconds`（0～15）观察运行状态。

执行链路：API 在同一笔 MySQL 事务中写入任务和 Outbox 事件，任务初始状态为
`PENDING`。Dispatcher 将待派发事件发布到 Redis；Celery Worker 通过条件更新
抢占任务，写入 `RUNNING`，再将结果或失败状态写回 MySQL。Outbox 派发失败后按
退避间隔重试；即使消息重复投递，只有一次 `PENDING → RUNNING` 更新能成功。

Celery 将耗时工作移出 HTTP 请求，提供 Worker 并行执行和队列消费能力。
Redis 在这里充当 Celery 的消息代理，暂存待执行消息；MySQL 保存任务状态、输入、
结果和派发记录，重启后仍可查询，且不会依赖 Redis 临时数据作为业务事实来源。

## 第一阶段：Worker 异常退出恢复

Worker 领取任务时在 MySQL 写入 10 秒执行租约及唯一令牌，运行期间每 2 秒续租。
Dispatcher 每秒扫描过期的 `RUNNING` 任务：未达 3 次执行上限时将其重置为
`PENDING`，并在同一事务中重置 Outbox 事件重新派发；达到上限时持久化为 `FAILED`。
旧 Worker 即使重新运行，也无法用过期令牌写入结果。这里提供的是至少一次执行，
并非恰好一次；将来处理有外部副作用的业务时还需要业务幂等键。

先运行普通测试，再运行真实 Worker 崩溃测试。崩溃测试会对当前 Compose 项目的
`worker` 服务发送 SIGKILL；请只在没有其他重要任务的本地测试环境运行：

    pip install -e ".[dev]"
    pytest -q
    docker compose up --build -d
    $env:RUN_DOCKER_INTEGRATION = "1"
    pytest -q tests/integration/test_worker_crash_recovery.py
    Remove-Item Env:RUN_DOCKER_INTEGRATION

最后一个测试通过标准：先观察到 `RUNNING`，杀死 Worker 后由测试重新启动现有容器，最终得到
`SUCCESS` 和正确统计结果。测试不使用 SQLite 或内存仓库，而是经 API 使用 Compose
中的 MySQL、Redis 和 Celery；只重启 Worker，不重跑迁移。若服务不是在本机 8000 端口，将
`INTEGRATION_API_URL` 设为相应的 `/api/v1` 地址。

## 第二阶段：真实服务完整流程验证

自动化测试使用当前本地 Compose 中的 FastAPI、MySQL、Redis、Dispatcher 和 Celery
Worker。每次生成不同的用户名，通过 HTTP 注册、登录和提交现有 `sum_numbers` 任务，
不使用 SQLite、内存仓库或依赖替换，也不重启服务。

在项目根目录的 PowerShell 中运行（Compose 已启动时无需再次启动）：

    Set-Location E:\Projects\distributed-task-platform
    docker compose ps -a
    Invoke-RestMethod http://localhost:8000/api/v1/health/ready
    .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
    $env:RUN_DOCKER_E2E = "1"
    try {
        .\.venv\Scripts\python.exe -m pytest -q -s tests\integration\test_task_flow.py
    } finally {
        Remove-Item Env:RUN_DOCKER_E2E
    }

如果没有 `.venv`，先用 Python 3.12 或更高版本运行 `python -m venv .venv`。
若本地 API 端口不同，可以设置 `INTEGRATION_API_URL`，例如
`http://127.0.0.1:8000/api/v1`；测试只接受本机 HTTP 地址。

预期结果为 `2 passed`，覆盖以下检查：

- 注册返回 201、登录返回 200，登录令牌能查询对应的当前用户。
- 提交任务返回 202 和 `PENDING`，随后观察到 `RUNNING`；运行中获取结果返回 202。
- Worker 执行后状态为 `SUCCESS`，结果接口返回 200 和正确的 `sum`、`count`。
- 在 API 容器中使用独立连接直接查询 MySQL，核对运行中和完成后的状态、所属用户、
  输入、结果、时间戳、执行次数及 Outbox 记录。15 秒延时任务正常完成且执行次数为 1。
- 两个用户能读取自己的任务，交叉查询状态和结果均返回 404；未登录访问返回 401。

可以再次运行同一测试命令，验证重复执行；无需清理已有记录。每次运行保留 2 个测试
用户和 3 个任务，测试输出包含任务 ID，不删除数据卷、不清空数据库，也不运行第一阶段
的 Worker 崩溃测试。

需要亲自核对数据库时，可以运行下面的只读查询，查看最近的测试记录及其所属用户：

    docker compose exec -T mysql mysql -utask_user -ptask_password task_platform -e "SELECT t.task_uuid, u.username, t.status, t.execution_attempts, t.result, t.started_at, t.finished_at FROM tasks t JOIN users u ON u.id=t.user_id WHERE u.username LIKE 'e2e-%' ORDER BY t.id DESC LIMIT 6;"

这组测试不包含第三阶段 CSV 功能。

## 第三阶段：CSV 异步统计分析

`POST /api/v1/tasks/csv` 接收 multipart 文件字段 `file`，先保存文件并将任务和
Outbox 写入 MySQL，然后返回 HTTP 202、`task_id` 和初始状态。统计在 Celery Worker
中执行，继续使用现有的状态和结果接口：

- `GET /api/v1/tasks/{task_id}`：`PENDING`、`RUNNING`、`SUCCESS` 或 `FAILED`。
- `GET /api/v1/tasks/{task_id}/result`：未结束返回 202，成功或失败返回 200。
  成功时 `result` 包含统计结果，失败时 `error_message` 包含原因。
- 任务仍然仅供创建者查询，其他用户查询状态或结果返回 404。

API 和 Worker 共享新的 `csv_data` 数据卷，Worker 只读挂载。上传文件以生成的 UUID
命名；用户文件名不会成为存储路径。MySQL 的任务参数只保存 `file_id`，Redis/Celery
消息仍只包含 `task_id`，不包含 CSV 内容。现有数据库表足够存储结果，无需新迁移。
上传文件保留在数据卷中，支持 Worker 租约恢复后重新读取；当前没有自动清理策略。

CSV 规则：

- UTF-8（可带 BOM）、逗号分隔，第一条记录必须为非空且不重复的列名。
- 行数不含表头及仅含换行的空物理行；包含空单元格的记录仍计入行数。
- 去除首尾空格后，空字符串和 `NA`、`N/A`、`NULL`、`NaN`（不区分大小写）计为缺失值。
  同时返回总缺失值数和各列缺失值数。
- 非缺失值全部为有限数字的列作为数值列，返回 `count`、`mean`、`min`、`max`。
  混合文本列和全部缺失的列不列入 `numeric_columns`；平均值不计缺失值。
- 支持引号内逗号、换行、负数、小数和科学计数法。字段超过 131,072 个字符、非 UTF-8、
  引号不闭合、列数不一致等格式错误，由 Worker 持久化为 `FAILED`。
- 默认文件不超过 10 MiB、100,000 条数据记录和 200 列，可通过 `CSV_MAX_UPLOAD_BYTES`、
  `CSV_MAX_ROWS`、`CSV_MAX_COLUMNS` 配置。超大上传直接返回 413，不创建任务；其他格式
  错误先返回任务 ID，随后异步失败。执行异常返回安全的错误类别，不暴露文件路径。

### 自动化验收

当前 Compose 已启动时，在 PowerShell 中更新应用镜像和容器：

    Set-Location E:\Projects\distributed-task-platform
    docker compose build api worker dispatcher
    docker compose up -d --no-deps api worker dispatcher
    Invoke-RestMethod http://localhost:8000/api/v1/health/ready

这只更新应用容器，保留 MySQL、Redis 和已有数据卷。随后运行第三阶段和第二阶段回归：

    $env:RUN_DOCKER_CSV = "1"
    $env:RUN_DOCKER_E2E = "1"
    try {
        .\.venv\Scripts\python.exe -m pytest -q -s tests\test_csv_analysis.py tests\integration\test_csv_flow.py tests\integration\test_task_flow.py
    } finally {
        Remove-Item Env:RUN_DOCKER_CSV
        Remove-Item Env:RUN_DOCKER_E2E
    }

预期 `28 passed`：16 项 CSV 解析与临时文件测试（不使用数据库），10 项真实 CSV
集成测试及 2 项真实第二阶段回归。集成测试使用默认 CSV 限额，不使用 SQLite 或内存
仓库；直接核对 MySQL，并检查执行中的真实 Redis 消息。执行异常测试仅新增自己的
测试任务，指向一个不存在的文件，验证真实 Worker 能持久化 `FileNotFoundError`。
测试可重复运行，保留测试账号、任务和上传文件，不清理已有数据，也不杀死 Worker。

### 手工上传验收

也可以打开 `http://localhost:8000/docs`，注册用户并通过 Authorize 登录，在
`POST /api/v1/tasks/csv` 上传 `examples/analysis.csv`，复制返回的任务 ID 查询状态和结果。

或者在项目根目录的 PowerShell 中直接运行：

    $base = "http://localhost:8000/api/v1"
    $username = "csv-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
    $password = [guid]::NewGuid().ToString("N")
    $body = @{username=$username; email="$username@example.com"; password=$password} | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri "$base/auth/register" -ContentType "application/json" -Body $body
    $token = Invoke-RestMethod -Method Post -Uri "$base/auth/login" -Body @{username=$username; password=$password}
    $headers = @{Authorization="Bearer $($token.access_token)"}
    $accepted = curl.exe -sS -H "Authorization: Bearer $($token.access_token)" -F "file=@examples/analysis.csv" "$base/tasks/csv" | ConvertFrom-Json
    $accepted
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        $state = Invoke-RestMethod -Uri "$base/tasks/$($accepted.task_id)" -Headers $headers
        $state.status
        if ($state.status -in @("SUCCESS", "FAILED")) { break }
        Start-Sleep -Milliseconds 500
    }
    Invoke-RestMethod -Uri "$base/tasks/$($accepted.task_id)/result" -Headers $headers | ConvertTo-Json -Depth 8

样例预期 `SUCCESS`，`row_count=3`、`missing_values=2`；`amount` 列平均值 15、最小值
10、最大值 20；`score` 列平均值 2、最小值 1、最大值 3。小文件处理很快，轮询时可能
直接看到完成状态；自动化测试另用大文件验证 `RUNNING`。

将上传文件改为 `examples/invalid.csv`，预期先返回任务 ID，最终 `FAILED`，原因包含
`line 2 has 1 fields; expected 2`。重新注册另一个用户，用其令牌查询原任务，预期 404。

## 认证模块设计

- `app/api/v1/endpoints/auth.py`：请求和 HTTP 状态码转换，不写 SQL。
- `app/api/dependencies.py`：FastAPI 依赖注入，从 Bearer Token 获取当前用户。
- `app/services/auth.py`：注册、登录和激活状态校验。
- `app/repositories/users.py`：封装用户表查询、写入、提交与冲突回滚。
- `app/core/security.py`：Argon2 密码哈希以及 JWT 生成、签名和验证。
- `app/schemas/`：隔离请求和响应字段，响应不包含密码哈希。

用户名和邮箱以小写形式保存。数据库唯一约束处理并发注册冲突。
Access Token 包含 `sub`、`type`、`iat` 和 `exp`；验证时要求全部字段存在，
并在访问受保护接口时重新查询用户，以便立即阻止已停用的账号。

## 本地开发

复制环境变量模板，并按上述方式设置 SECRET_KEY：

    Copy-Item .env.example .env

安装依赖并运行迁移：

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -e ".[dev]"
    alembic upgrade head
    uvicorn app.main:app --reload

另开终端启动 Worker：

    celery -A app.celery_app:celery_app worker --loglevel=INFO --queues=default --pool=solo

上面的 `--pool=solo` 适用于 Windows 本地开发；Docker Compose 在 Linux 容器中使用默认 Worker 池。

再开一个终端启动派发器：

    python -m app.dispatcher.main

运行测试：

    pytest -q

## 当前边界

- MySQL 是持久化数据源，SQLAlchemy 使用异步驱动 asyncmy。
- Redis DB 0 用于缓存，DB 1 用作 Celery Broker，DB 2 保存临时执行结果。
- JWT 当前只实现短期 Access Token，Refresh Token 和主动撤销属于下一阶段。
- Compose 中的 MySQL 密码仅供本地开发，生产环境必须通过安全配置注入。
- 不会在 API 启动时自动建表，所有结构变更统一交给 Alembic。
- Worker 异常退出的租约恢复已实现；真实 Compose 崩溃测试需在安装 Docker 的机器上运行。

## 下一阶段建议

1. 扩展任务类型注册、任务执行历史和客户端提交幂等键。
2. 增加任务取消和人工重试接口。
3. 加入进度上报、限流和可观测性。
