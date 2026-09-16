# musicdl 二次开发版

一个面向个人音乐管理流程的 Python 音乐搜索与下载工具，包含命令行能力和本地 Web 界面。

> 本项目仅供个人学习、研究和已获授权内容的管理使用。请遵守音乐平台服务条款、版权法律及授权限制；不得绕过 DRM、付费墙或其他访问控制。

## 项目来源

本仓库 Fork 自 [CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl)，原始作者为 Zhenchao Jin（CharlesPikachu）。核心的音乐源客户端与下载能力仍基于上游项目；本 Fork 在此基础上维护本地 Web 使用体验、音质策略、元数据处理和后续 Docker 部署能力。

- 上游仓库：<https://github.com/CharlesPikachu/musicdl>
- 本 Fork：<https://github.com/RedAnanas/musicdl>
- 上游许可证：[PolyForm Noncommercial License 1.0.0](LICENSE)

保留上游版权和许可证声明。二次开发内容同样受仓库内许可证约束。

## 当前能力

- Python CLI：搜索、解析和下载由 `musicdl` 核心提供。
- 本地 Web 界面：流式显示搜索结果、播放、下载进度和下载记录。
- Web 界面音乐源：网易云、QQ、酷我、酷狗、咪咕、哔哩哔哩、Apple Music、YouTube、Spotify；默认启用网易云、QQ、酷我和酷狗。
- 音质策略：网易云、QQ、酷我、酷狗优先尝试可用的无损资源，失败时由各平台逻辑降级。
- 下载处理：保存时写入可获得的音乐元数据、封面与内嵌歌词；不额外生成独立歌词文件。

平台接口和资源可用性会随平台策略、网络环境及账号授权而变化。界面展示的格式与音质以实际返回结果为准。

## 快速开始

### 环境

- Python 3.10+
- 建议使用虚拟环境

```powershell
git clone https://github.com/RedAnanas/musicdl.git
Set-Location .\musicdl
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe -m pip install -r .\examples\claudeai-modern-web-music-player\requirements.txt
```

### 命令行

```powershell
& .\.venv\Scripts\musicdl.exe --help
```

具体参数和平台能力以 CLI 帮助及源代码实现为准。

### Web 界面

```powershell
Set-Location .\examples\claudeai-modern-web-music-player
& ..\..\.venv\Scripts\python.exe -X utf8 app.py
```

浏览器打开 <http://127.0.0.1:5000>。

下载文件默认保存在 `examples/claudeai-modern-web-music-player/downloads/`。可在网页“设置”中指定本机绝对保存路径；该设置保存于 `settings.json`，已被 Git 忽略。

## Docker

本仓库已提供 `Dockerfile`、`compose.yaml` 与 Docker Hub 镜像部署配置。默认拉取 `redananas/musicdl:latest`，映射宿主机 5000 端口，并将下载数据持久化；具体步骤见 [Docker 部署文档](docs/docker.md)。

## 目录结构

```text
musicdl/
  modules/sources/                 各音乐平台客户端
  modules/utils/                   平台解析与通用工具
  musicdl.py                       CLI 入口

examples/claudeai-modern-web-music-player/
  app.py                           Web API、下载任务与元数据处理
  static/                          Web 前端资源
  downloads/                       本地下载目录（不提交）
  settings.json                    本机设置（不提交）

mcp/                               MCP 服务
docs/                              项目文档
AGENTS.md                          二次开发与协作规范
```

## 开发与上游同步

项目的开发约定见 [AGENTS.md](AGENTS.md)。远程仓库约定如下：

- `origin`：本 Fork，用于推送本项目分支。
- `upstream`：原项目，仅用于拉取更新。
- `master`：上游稳定基线。
- `develop`：本项目集成分支。
- `feature/*`、`fix/*`：具体功能或修复分支。

同步上游前，请先保证工作区干净：

```powershell
git fetch upstream --prune
git switch master
git merge --ff-only upstream/master
git switch develop
git merge master
```

上游更新可能与本项目的音乐源、Web 接口或音质策略发生冲突；解决冲突后应重新验证搜索、下载和 Web 服务，再合并部署。

## 验证

```powershell
& .\.venv\Scripts\python.exe -m py_compile .\musicdl\musicdl.py .\examples\claudeai-modern-web-music-player\app.py
& .\.venv\Scripts\python.exe -m pip check
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/
```

真实下载测试不得将音乐文件、Cookie、账号信息、令牌或个人设置提交到 Git。
