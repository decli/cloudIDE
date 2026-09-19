# 项目规范

给编码 agent 的约定，请严格遵守。

## 产物

一个纯静态网站。构建后 `dist/` 目录能被任何静态服务器直接托管，`dist/index.html` 必须存在。

## 技术栈

- HTML + CSS + 原生 JavaScript。
- 构建脚本用 Python 3.12 标准库。
- **不能引用任何外网资源**：没有 CDN 上的字体、图标、框架、图片。沙箱断网，用户访问时也可能没网。所有样式和脚本都放在站点里。
- 不要引入任何第三方依赖，装不上。

## 目录

| 路径 | 用途 | 能否修改 |
|---|---|---|
| `src/` | 站点源文件 | 可以 |
| `scripts/build.py` | 构建脚本，把 `src/` 产出到 `dist/` | 可以，可以改成从数据渲染页面 |
| `tests/unit/` | 单元测试，你自己写 | 可以 |
| `tests/acceptance/` | 验收测试，由需求生成 | **不可以** |
| `run.sh` | 命令契约 | **不可以** |
| `pyproject.toml` | lint 和 pytest 配置 | **不可以** |
| `.gitea/` | CI 配置 | **不可以** |
| `dist/` | 构建产物，由 `./run.sh build` 生成 | 不要手写 |

## 命令

```sh
./run.sh build       # 构建到 dist/
./run.sh lint        # ruff 检查 scripts 和 tests/unit
./run.sh test        # 单元测试
./run.sh acceptance  # 验收测试（会先自动构建）
./run.sh serve 8000  # 本地预览
```

## 质量要求

- 页面要能在手机上正常显示，必须有 viewport meta。
- 中文内容用 `<html lang="zh-CN">`，字符集 UTF-8。
- 站内链接必须都能打开，不要出现指向不存在文件的链接。
- 样式自己写，干净清爽即可，不要堆砌效果。

## 完成标准

`./run.sh lint`、`./run.sh test`、`./run.sh acceptance` 三项全部通过。
