# 项目规范

给编码 agent 的约定，请严格遵守。

## 技术栈

- Python 3.12，只用标准库。**不要引入任何第三方运行时依赖**，沙箱没有网络，装不上。
- 测试用 pytest，静态检查用 ruff，两者都已预装。

## 目录

| 路径 | 用途 | 能否修改 |
|---|---|---|
| `src/app/` | 源码 | 可以 |
| `src/app/cli.py` | 入口，必须有 `main(argv) -> int` | 可以，但要保留入口签名 |
| `tests/unit/` | 单元测试，你自己写 | 可以 |
| `tests/acceptance/` | 验收测试，由需求生成 | **不可以** |
| `run.sh` | 命令契约 | **不可以** |
| `pyproject.toml` | lint 和 pytest 配置 | **不可以** |
| `.gitea/` | CI 配置 | **不可以** |

## 命令

```sh
./run.sh lint        # ruff 静态检查
./run.sh test        # 单元测试
./run.sh acceptance  # 验收测试
./run.sh build       # 打包成 dist/app.pyz
./run.sh run -- ...  # 运行 CLI
```

## 完成标准

`./run.sh lint`、`./run.sh test`、`./run.sh acceptance` 三项全部通过。
