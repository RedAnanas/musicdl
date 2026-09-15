# AGENTS.md - musicdl 二次开发规范

## 项目信息

- 项目：musicdl（本地二次开发版本）
- 核心代码：Python，包目录为 `musicdl/`，命令行入口为 `musicdl`
- Web 前端：`examples/claudeai-modern-web-music-player/`，Flask 后端配合原生静态页面
- MCP 服务：`mcp/`
- 本地运行环境：项目根目录 `.venv/`；当前 Web 服务默认监听 `127.0.0.1:5000`
- 当前稳定基线分支：`master`

## 工作原则

1. 先确认改动属于核心下载能力、Web 前端、MCP 服务或项目配置，再修改对应最小范围的文件。
2. 保持 Python 源码与 Web 前端的请求参数、响应字段、下载设置一致；修改接口时同步检查两端。
3. 音乐源的搜索、可用音质、登录/会员限制和下载结果必须如实展示，不伪造“无损”或“可下载”状态。
4. 变更音质优先级时，以各平台实际返回的可用资源为准；保留降级路径，并在前端显示最终格式/音质。
5. 元数据、封面和歌词处理应在下载任务完成后验证真实文件；用户未要求时，不额外生成独立歌词文件。
6. 不将 Cookie、账号、令牌、授权链接、下载目录中的音乐、个人 `settings.json` 或运行日志提交到 Git。
7. 不改动与当前需求无关的音乐源逻辑、网页样式或依赖版本。

## 分支与 Git 规则

`master` 是当前稳定基线，禁止直接进行日常开发。首次进入正式二次开发流程时，从 `master` 创建 `develop` 作为集成分支；功能开发统一从 `develop` 创建分支：

- 新功能：`feature/<简短英文描述>`
- 缺陷修复：`fix/<简短英文描述>`
- 文档或工程维护：`chore/<简短英文描述>`

标准流程：

1. 开始前执行 `git status --short`，确认并保留已有未提交改动。
2. 从 `develop` 创建功能分支；若 `develop` 尚未建立，先由项目负责人确认并创建。
3. 在功能分支完成开发、验证和必要文档更新。
4. 由用户确认后再提交、合并至 `develop`；`master` 的合并与推送需再次明确确认。

提交信息格式：`<type>: <中文描述>`，例如 `feat: 增加下载音质选择`。

禁止：

- `git reset --hard`、强制推送，或未确认前覆盖/清理现有工作区改动；
- 未经确认自动提交、合并或推送；
- 提交下载歌曲、个人配置、调试日志、凭据、数据库或备份文件；
- 为绕过平台限制、DRM 或付费/授权限制而修改下载逻辑。

## 目录职责

```text
musicdl/
  modules/sources/                 各音乐平台客户端与下载实现
  modules/utils/                   平台解析、音质与通用工具
  musicdl.py                       CLI 入口

examples/claudeai-modern-web-music-player/
  app.py                           Web API、下载任务和元数据处理
  static/                          前端页面、样式和交互脚本
  settings.json                    本机个人设置，不提交
  downloads/                       Web 下载输出，不提交

mcp/                               MCP 服务
scripts/                           项目辅助脚本
docs/                              项目文档
```

## 本地开发与验证

在项目根目录执行；优先使用项目虚拟环境，不使用全局 Python：

```powershell
# 检查核心 CLI
& .\.venv\Scripts\musicdl.exe --help

# 启动 Web 前端（在独立终端运行）
Set-Location .\examples\claudeai-modern-web-music-player
& ..\..\.venv\Scripts\python.exe -X utf8 app.py

# 验证 Web 首页可访问
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/

# 基础语法与依赖一致性检查
Set-Location ..\..
& .\.venv\Scripts\python.exe -m py_compile .\musicdl\musicdl.py .\examples\claudeai-modern-web-music-player\app.py
& .\.venv\Scripts\python.exe -m pip check
```

修改下载或音质相关代码时，至少验证一次实际搜索、一个可公开下载的测试资源，以及生成文件的格式和元数据。不要把测试下载内容加入 Git。

## 完成标准

交付前逐项确认：

1. 改动仅覆盖需求所需文件，`git diff --check` 通过。
2. 修改过的 Python 文件可编译，依赖检查通过；若新增测试，测试必须通过。
3. Web 改动完成首页访问及对应接口/交互验证；核心下载改动完成真实文件验证。
4. 前端展示的音乐源顺序、默认选择、音质和保存行为与后端实现一致。
5. `git status --short` 中不存在意外文件，尤其是歌曲、日志、凭据和个人设置。
6. 向用户说明代码改动、服务是否重启，以及已完成的实际运行验证。
