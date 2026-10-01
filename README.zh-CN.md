# PyPI Downloader

一个快速的异步 Python CLI 工具，用于从 PyPI 镜像下载软件包，构建离线包集。

## 用途

本工具专为在隔离网络或受限网络环境中搭建内部 PyPI 镜像而设计。

### 使用场景

你的开发环境处于无法直接访问互联网的内网中，团队使用：

- 多个 Python 3 版本（3.8、3.9、3.11 等）
- 不同处理器架构（x86_64、ARM 等）
- 多种操作系统（Linux、Windows、macOS）

痛点：每次需要 PyPI 包时，希望一次性下载所有版本、所有架构及其依赖，然后部署到内部 PyPI 服务器，让所有开发者按需安装。

解决方案：本工具通过 `pip-compile` 自动解析依赖，把所有 Python 3 兼容版本和 wheel 文件下载到一个目录中，任何静态文件服务器或 PyPI 索引服务器都可以直接对外提供该目录。

### 核心优势

- 一次下载：单次运行获取所有版本和平台的包
- 异构支持：适用于混合 Python 版本和架构的团队
- 依赖解析：通过 `pip-compile` 自动包含所有传递依赖
- 生产可用：SHA-256 校验 + 重试逻辑 + 镜像自动切换
- 智能缓存：校验已有文件的哈希值，匹配则跳过下载（重复运行速度提升 100 倍）
- 流式下载：文件以 1 MiB 分块流式写盘并原子重命名（GB 级 wheel 不会占满内存）
- 高性能：异步并发下载（默认 16 路）+ 线程池处理文件 I/O
- 国内友好：内置 5 个国内镜像源（阿里云、腾讯云、华为云、火山引擎、教育网联合镜像站）
- 镜像兼容：使用 pip User-Agent，避免被 PyPI 镜像拦截

---

## 功能亮点

- 全版本下载：使用 `--all-versions` 下载每个包的所有 Python 3 版本
- 最新补丁模式：使用 `--latest-patch` 只下载每个次版本的最新补丁版本（减少 60-70% 文件量）
- 多镜像自动切换：某个镜像失败时自动切换到下一个（5 个国内镜像源 + 官方 PyPI）
- 自定义镜像：通过可重复的 `--mirror` 参数使用自己的镜像（优先于内置列表）
- 异步并发：数百个文件并行下载，不阻塞（默认 16 路，可配置）
- 哈希校验：使用 PyPI API 哈希值对每个文件进行 SHA-256 完整性校验
- 智能跳过：校验已有文件哈希，有效则跳过下载
- 非阻塞 I/O：文件操作使用线程池，不阻塞事件循环
- 自动依赖解析：始终使用 `pip-compile` 解析所有传递依赖
- 平台过滤：只下载指定 Python 版本、ABI 或平台的 wheel 文件
- 预演模式：下载前预览 URL 列表（保存的是 PyPI 官方原始 URL）
- 仅 Python 3：自动忽略 Python 2 专属包

---

## 安装

### 从 PyPI 安装

```bash
pip install pypi-downloader
```

### 从源码安装

```bash
git clone https://github.com/Hektorwang/pypi_downloader.git
cd pypi-downloader
uv build
pip install dist/*.whl
```

---

## 快速开始

下载当前目录 `requirements.txt` 中列出的所有包：

```bash
pypi-downloader
```

指定目录、64 路并发、仅预演（不实际下载）：

```bash
pypi-downloader requirements.txt \
  --download-dir ./my_mirror \
  --concurrency 64 \
  --dry-run
```

---

## 用法

```text
usage: pypi-downloader [-h] [-r REQUIREMENT_FILE] [--dry-run]
                       [--concurrency CONCURRENCY] [--download-dir DOWNLOAD_DIR]
                       [--cn] [--mirror URL] [--python-version PYTHON_VERSION]
                       [--abi ABI] [--platform PLATFORM] [--all-versions]
                       [--latest-patch] [--url-list-path URL_LIST_PATH]
                       [--version]
                       [requirements]

PyPI Package Downloader v0.9.0 - Async downloader for building offline PyPI mirrors. Dependencies are always resolved automatically via pip-compile (pip-tools required).

位置参数:
  requirements          requirements.txt 文件路径

选项:
  -h, --help            显示帮助信息并退出
  -r, --requirement REQUIREMENT_FILE
                        requirements.txt 路径（pip 格式）
  --dry-run             仅收集 URL 并保存到文件，不实际下载
  --concurrency CONCURRENCY
                        最大并发下载数（默认：16）
  --download-dir DOWNLOAD_DIR
                        包保存目录（默认：./pypi）
  --cn                  使用国内 PyPI 镜像，自动切换备用镜像
  --mirror URL          自定义镜像基础 URL，优先于内置列表（可重复使用，
                        例如 https://mirror.example.com/pypi）
  --python-version PYTHON_VERSION
                        按 Python 版本标签过滤（如 cp311、py3、py2.py3）
  --abi ABI             按 ABI 标签过滤（如 cp311、abi3、none）
  --platform PLATFORM   按平台标签过滤（如 manylinux_2_17_x86_64、win_amd64、any）
  --all-versions        下载每个包所有可用的 Python 3 版本
  --latest-patch        只下载每个次版本的最新补丁版本，与 --all-versions 互斥
  --url-list-path PATH  URL 列表文件的自定义路径（默认：./url_list.txt，仅在预演模式下使用）
  --version             显示版本号并退出

示例:
  pypi-downloader                                  # 使用 ./requirements.txt
  pypi-downloader -r reqs.txt --cn                 # 使用国内镜像
  pypi-downloader -r reqs.txt --all-versions --cn  # 下载所有 Python 3 版本
  pypi-downloader -r reqs.txt --latest-patch --cn  # 每个次版本只保留最新补丁
  pypi-downloader -r reqs.txt --mirror https://mirror.example.com/pypi
  pypi-downloader -r reqs.txt --dry-run            # 仅预览 URL
```

说明：依赖始终通过 `pip-compile` 自动解析（需要安装 `pip-tools`）。

---

## 进阶示例

### 下载全部版本（构建内部 PyPI 镜像）

适合构建包含所有 Python 3 版本的内部 PyPI 镜像：

```bash
# 解析所有依赖，下载所有 Python 3 版本
pypi-downloader -r requirements.txt --all-versions --cn

# 执行流程：
# 1. pip-compile 解析所有传递依赖
# 2. 下载所有 Python 3 兼容版本，例如：
#    numpy: 1.19.0, 1.19.1, ..., 1.26.4（全部版本）
#    pandas: 1.0.0, 1.0.1, ..., 2.2.2（全部版本）
# 3. 包落在 ./pypi 目录，可直接供内部索引使用
```

适用场景：内网中有不同 Python 3 版本（3.8、3.9、3.11）和架构（x86_64、ARM）的机器，此命令下载所有 wheel 文件，任意机器均可按需安装。

### 最新补丁模式（精简镜像）

只下载每个次版本的最新补丁版本，减少 60-70% 的文件量：

```bash
# 只保留 2.1.9（跳过 2.1.3、2.1.5），只保留 2.2.8（跳过 2.2.2）
pypi-downloader -r requirements.txt --latest-patch --cn

# 文件量对比示例：
# --all-versions：numpy 1.19.0, 1.19.1, 1.19.2, ..., 1.26.4（100+ 个版本）
# --latest-patch：numpy 1.19.5, 1.20.3, 1.21.6, 1.22.4, ..., 1.26.4（约 20 个版本）
```

优势：
- 减少 60-70% 的下载文件数
- 下载更快，占用存储更少
- 保持兼容性（补丁版本应向后兼容）

注意：`--latest-patch` 与 `--all-versions` 互斥，不能同时使用。

### 预演模式（预览 URL）

预览将要下载的内容并保存 URL 列表，不实际下载：

```bash
# 预演模式自动将 URL 保存到 ./url_list.txt
pypi-downloader -r requirements.txt --dry-run --cn

# 保存到自定义路径
pypi-downloader -r requirements.txt --dry-run --url-list-path /path/to/urls.txt
```

保存的是 `files.pythonhosted.org` 官方原始 URL——每次运行结果一致，可直接配合其他下载工具（wget、aria2c 等）使用。

使用场景：
- 下载前审查将要获取的内容
- 配合其他下载工具使用（wget、aria2c 等）
- 保留包 URL 记录备查

### 平台专属下载

只下载与特定平台兼容的 wheel 文件：

```bash
# Linux x86_64 + CPython 3.11
pypi-downloader -r requirements.txt \
  --python-version cp311 \
  --abi cp311 \
  --platform manylinux_2_17_x86_64

# Windows AMD64 + CPython 3.11
pypi-downloader -r requirements.txt \
  --python-version cp311 \
  --platform win_amd64

# 纯 Python wheel（任意平台）
pypi-downloader -r requirements.txt \
  --abi none \
  --platform any
```

### 自定义下载目录与镜像

下载到指定目录，可选地经由自己的镜像：

```bash
# 下载包到 /var/www/pypi（依赖由 pip-compile 自动解析）
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn

# 优先使用公司内部镜像，失败后再回落到内置列表
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --mirror https://mirror.example.com/pypi
```

将任意静态文件服务器或 PyPI 索引服务器指向下载目录即可安装包：

```bash
pip install --index-url http://localhost:8080/simple/ numpy
```

### 国内镜像加速

使用国内镜像加速下载：

```bash
pypi-downloader -r requirements.txt --cn
```

支持的镜像源（共 5 个；官方 PyPI 始终作为最后的备用镜像）：
- 华为云、阿里云、腾讯云、教育网联合镜像站（CERNET）、火山引擎
- 华为云是指定的首选下载镜像：包文件始终最先从华为云下载（各商业源中同步最及时）。它不代理 PyPI JSON API，元数据由其余镜像或官方兜底提供。
- 其余镜像在启动时随机排序以分散负载
- 教育网联合镜像站是 MirrorZ 聚合入口，会自动跳转到离你网络最近的参与高校镜像（清华 TUNA、中科大、上交大等）
- 各镜像的文件路径布局（是否带 `web/` 前缀）由工具自动适配
- 通过 `--mirror URL` 添加自己的镜像（可重复，优先于内置列表）

### 已知局限

- **跨平台依赖解析**：`pip-compile` 只按运行本工具的解释器与平台解析依赖。受环境标记保护的特定平台依赖（如 `colorama; sys_platform == "win32"`）只有在本工具运行于该平台时才会被固定下来。如果镜像库需要服务多种操作系统，请在每个目标平台上各运行一次本工具（已下载并校验通过的文件会自动跳过）。`--all-versions` 覆盖版本维度，但覆盖不了平台维度。
- **元数据来源**：使用 `--cn` 时，包元数据（版本、哈希）优先从国内镜像获取——多数镜像代理了 PyPI JSON API（端点路径与该镜像的文件布局一致；实测阿里云、腾讯云、火山引擎、教育网联合镜像站及其背后高校源均支持）。不支持该端点的镜像（华为云）会被自动跳过。官方 PyPI JSON API 仍作为最终裁决与兜底，因此"包不存在"依然能被准确判定，仅镜像可达的内网环境也能正常工作。

---

## 环境要求

- Python 3.11+
- `aiohttp`、`loguru`、`rich`、`pip-tools`、`packaging`（随包自动安装）
- 如需把下载目录作为索引对外服务，可自备 `pypiserver` 或任意静态文件服务器（外部工具，本工具不依赖）

---

## 架构说明

工具采用两阶段执行模型：

1. 元数据阶段：并发获取包元数据（镜像优先——代理了 PyPI JSON API 的镜像直接提供服务，官方 API 作为最终裁决与兜底；带缓存；官方源 404 直接判定为"包不存在"）并统计待下载文件总数
2. 下载阶段：并发下载所有文件；每次尝试都会把官方 URL 实时改写到当前正在使用的镜像，因此切换镜像必然切换实际下载的 URL

下载链路天然健壮：

- 内容以 1 MiB 分块流式写入 `.part` 临时文件（同时计算哈希），SHA-256 校验通过后原子重命名——中断的下载绝不会留下写了一半的文件
- 已存在且哈希匹配的文件直接跳过（重复运行幂等）
- 镜像切换只会单向推进共享的"首选镜像"指针，后续文件会从失败镜像之后开始尝试

内部使用混合异步/线程架构：
- asyncio 处理网络 I/O（默认 16 路并发下载）
- ThreadPoolExecutor 处理文件 I/O 和哈希计算（最多 CPU_COUNT * 4 个线程，上限 32）

这种组合在不阻塞事件循环的前提下最大化 I/O 密集型工作负载的吞吐量。

---

## 贡献

欢迎提交 Pull Request。重大变更请先开 Issue 讨论你的想法。

---

## 许可证

MIT (c) Hektorwang
