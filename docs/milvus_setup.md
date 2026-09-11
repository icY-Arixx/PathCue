# Milvus 本地接入

项目默认使用 JSON 本地向量库，确保无需 Docker 也能运行。需要验证真实向量数据库时，可切换到 Milvus Standalone。

## 1. 启动 Milvus

Docker Desktop 正常运行后，按照 Milvus 官方 Standalone Docker Compose 文档下载对应版本的 compose 文件并启动：

Windows 10 家庭版首次安装 Docker 前，需要使用“以管理员身份运行”的 PowerShell 启用 WSL2，并在系统提示后重启：

```powershell
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

```powershell
Invoke-WebRequest `
  -Uri "https://github.com/milvus-io/milvus/releases/download/v3.0.1/milvus-standalone-docker-compose.yml" `
  -OutFile "infra\milvus-standalone.yml"
.\scripts\docker_compose.ps1 -ComposeArgs '-f','infra\milvus-standalone.yml','up','-d'
```

版本更新时，以官方安装页给出的 release 地址为准，不要直接使用来历不明的 compose 文件。

## 2. 安装依赖并配置

```powershell
python -m pip install ".[milvus]" --no-cache-dir
Copy-Item .env.example .env
```

在 `.env` 中设置：

```text
VECTOR_BACKEND=milvus
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN=
MILVUS_COLLECTION=urban_mobility_chunks
```

`.env` 已被 Git 忽略，不应上传密钥或云服务 token。

## 3. 建库与验证

```powershell
python scripts\ingest.py
python scripts\demo.py "地铁换乘需要预留多少时间？"
python scripts\evaluate.py
python scripts\verify_milvus.py
```

当前实现将本地索引用于可复现的 BM25 检索，同时把向量写入 Milvus，便于验证建表、向量入库、删除和增量更新。若用于生产，应进一步使用 Milvus 原生多向量或稀疏向量字段完成服务端混合检索，并通过独立验证集调参。

## 4. 停止服务

```powershell
.\scripts\docker_compose.ps1 -ComposeArgs '-f','infra\milvus-standalone.yml','down'
```

如需删除 Milvus 持久化数据，应先确认数据可恢复，再按官方文档处理；本项目不提供自动清空脚本。

参考：

- https://milvus.io/docs/install_standalone-docker-compose.md
- https://milvus.io/docs/multi-vector-search.md
