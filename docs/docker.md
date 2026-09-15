# Docker 部署 Web 前端

此配置只启动 `claudeai-modern-web-music-player` Flask Web 前端，默认映射到宿主机 5000 端口。

```powershell
docker compose up -d --build
```

打开 `http://127.0.0.1:5000/`。下载目录默认是容器内的 `/Media/musicdl`，对应飞牛的 `/vol2/1000/Media/musicdl`；媒体盘以 `/vol2/1000/Media:/Media` 挂载到容器，容器可访问整个媒体库。下载内容与下载目录设置保存到项目根目录的 `data/`，不会写入镜像；其中可能包含个人路径，不应提交到 Git。

要选择媒体盘内其他下载目录，可在项目根目录创建 `.env`（不提交 Git），例如：

```text
MUSICDL_DOWNLOAD_DIR=/Media/音乐/下载
```

修改后执行 `docker compose up -d`。路径必须以容器内的 `/Media/` 开头；例如 `/Media/电影/原声` 对应飞牛的 `/vol2/1000/Media/电影/原声`。

常用命令：

```powershell
docker compose ps
docker compose logs -f musicdl-web
docker compose down
```

若 5000 端口已被占用，请修改 `compose.yaml` 的左侧端口号，例如 `"8080:5000"`，然后访问 `http://127.0.0.1:8080/`。
