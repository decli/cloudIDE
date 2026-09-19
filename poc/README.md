# cloudIDE POC

一句话需求 → 隔离沙箱里让大模型写代码、跑测试、修 bug → 推到本地 Gitea → Gitea Actions 跑 CI → 发布制品。

目的是验证[技术方案](../docs/03-architecture.md)里那条链路，所以刻意做薄：没有 Web UI、数据库、消息队列、工作流引擎，状态就是文件和 git 提交。

## 组成

| 组件 | 是什么 |
|---|---|
| `agent/` | 编排和 agent 循环，几百行 Python，只依赖 openai + rich |
| `templates/python-cli/` | 黄金路径模板：Python CLI 工具，含命令契约、测试目录、CI 配置 |
| `docker/sandbox-py.Dockerfile` | 任务沙箱镜像：Python 3.12 + pytest + ruff，预装好，运行时断网 |
| `infra/` | 本地 Gitea + Actions runner 的 compose 和一键脚本 |

## 依赖

Docker Desktop、Python 3.12、一个 DeepSeek API key。

## 上手

```sh
cp .env.example .env       # 填入 DEEPSEEK_API_KEY
make setup                 # 建虚拟环境装依赖
make image                 # 构建沙箱镜像
make run REQ="做一个统计文本词频的命令行工具，支持 --top N 和 --ignore-case"
```

接上 CI（可选）：

```sh
sh infra/setup.sh          # 起 Gitea 和 runner，凭证自动写回 .env
.venv/bin/python -m agent.cli "需求…" --push
```

Gitea 在 <http://localhost:3000>，账号 `cloudide`，密码 `cloudide-poc-2026`。

常用参数：

```sh
.venv/bin/python -m agent.cli "需求" \
  --name mytool        # 项目名，同时是工作区目录名和 Gitea 仓库名
  --max-usd 0.2        # 这次任务最多花多少钱
  --keep-sandbox       # 结束后保留容器，方便进去排查
  -v                   # 打印完整的工具输出
```

## 一次任务发生了什么

1. 用模板初始化工作区，`git init` 并提交。
2. 测试 agent 把需求翻译成 pytest 验收测试，写进 `tests/acceptance/` 并锁定（记下 sha256）。
3. 起一个断网容器，工作区 bind mount 进去。
4. 编码 agent 用四个工具干活：`list_files`、`read_file`、`write_file`、`run`，自己跑 lint、单测、验收测试。
5. 每轮结束后，编排侧独立做三件事：还原被改动的锁定文件、删除新增的配置文件、自己再跑一遍三项检查。
6. 全绿就提交；不绿就把失败输出回灌给 agent，最多修 3 轮。
7. `--push` 时推到 Gitea，等 Actions 跑完：CI 里独立再跑一遍同样的检查，打包 `dist/app.pyz` 发到 Release。

工作区在 `workspaces/<项目名>-<时间戳>/`，每一轮都有 commit，`.cloudide/task.json` 记录了轮次、检查结果、用量和成本。

## 边界与上限

任何一层循环都有上限，超了就停下来报阻塞原因，不会无限重试烧钱。

| 项 | 默认值 | 改哪里 |
|---|---|---|
| 单任务花费 | $0.50 | `.env` 的 `MAX_USD` |
| 对话轮数 | 40 | `.env` 的 `MAX_TURNS` |
| 修复轮数 | 3 | `agent/config.py` |
| 单条命令超时 | 120 秒 | `agent/config.py` |
| 沙箱资源 | 2 CPU、2G 内存、无网络 | `agent/sandbox.py` |

## 安全边界

- 沙箱 `--network none`，agent 执行的命令碰不到网络，也拿不到任何凭证。
- git 推送和 Gitea API 都在宿主机侧执行，token 只出现在一次性的推送 URL 里，不写进仓库的 remote 配置。
- 锁定路径（`tests/acceptance/`、`run.sh`、`pyproject.toml`、`.gitea/`）被改会自动还原；新增的 `ruff.toml`、`conftest.py` 这类能覆盖配置的文件会被删除。
- 工作区是 bind mount，不是完整隔离。这套只够本地验证，多租户要换成 microVM 级隔离，见[技术方案](../docs/03-architecture.md)第 5.5 节。

## 已知问题

- 验收测试由模型生成，可能本身就写错或写得过严，这时 agent 会一直修不过去——这正是要观察的失败模式，`task.json` 里会记录。
- 沙箱断网意味着生成的工具只能用标准库；要支持第三方依赖，得给沙箱开一个带白名单的包管理代理。
- CI 里的检出用的是 `git clone` 默认分支，不是精确 commit；单人本地验证够用，多人并发要改成按 SHA 检出。
- 没有需求澄清环节，需求直接进了测试生成。澄清 agent 是下一步。
