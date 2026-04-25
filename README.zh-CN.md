# PyPI Downloader

一个快速的异步 Python CLI 工具，用于**从 PyPI 镜像下载软件包**。

## 🎯 用途

本工具专为**在隔离网络或受限网络环境中搭建内部 PyPI 镜像**而设计。

### 使用场景

你的开发环境处于无法直接访问互联网的内网中，团队使用：

- 多个 Python 3 版本（3.8、3.9、3.11 等）
- 不同处理器架构（x86_64、ARM 等）
- 多种操作系统（Linux、Windows、macOS）

**痛点**：每次需要 PyPI 包时，希望一次性下载所有版本、所有架构及其依赖，然后部署到内部 PyPI 服务器，让所有开发者按需安装。

**解决方案**：本工具自动解析依赖、下载指定包的所有 Python 3 兼容版本和 wheel 文件，构建可供 pip 使用的离线镜像，随时可部署到内网。

### 核心优势

- ✅ **一次下载**：单次运行获取所有版本和平台的包
- ✅ **异构支持**：适用于混合 Python 版本和架构的团队
- ✅ **依赖解析**：自动包含所有传递依赖
- ✅ **生产可用**：SHA-256 校验 + 重试逻辑 + 镜像自动切换
- ✅ **智能缓存**：校验已有文件的哈希值，匹配则跳过下载（重复运行速度提升 100 倍）
- ✅ **高性能**：异步并发下载（默认 16 路）+ 线程池处理 I/O（不阻塞事件循环）
- ✅ **内存优化**：分块哈希计算、非阻塞文件 I/O、高效内存使用
- ✅ **国内友好**：内置 14 个国内镜像源支持
- ✅ **镜像兼容**：使用 pip User-Agent，避免被 PyPI 镜像拦截

---

## ✨ 功能亮点

- **全版本下载** — 使用 `--all-versions` 下载每个包的所有 Python 3 版本
- **最新补丁模式** — 使用 `--latest-patch` 只下载每个次版本的最新补丁版本（减少 60-70% 文件量）
- **多镜像自动切换** — 某个镜像失败时自动切换到下一个（14 个国内镜像 + 官方 PyPI）
- **异步并发** — 数百个文件并行下载，不阻塞（默认 16 路，可配置）
- **哈希校验** — 使用 PyPI API 哈希值对每个文件进行 SHA-256 完整性校验
- **智能跳过** — 校验已有文件哈希，有效则跳过下载（重复运行速度提升 100 倍）
- **非阻塞 I/O** — 文件操作使用线程池，不阻塞事件循环
- **自动依赖解析** — 始终使用 `pip-compile` 解析所有传递依赖（无需额外参数）
- **平台过滤** — 只下载指定 Python 版本、ABI 或平台的 wheel 文件
- **预演模式** — 下载前预览 URL 列表（自动保存到文件）
- **私有 PyPI 服务器** — 下载完成后使用 `--serve` 启动离线 `pypiserver` 实例
- **仅 Python 3** — 自动忽略 Python 2 专属包
- **镜像友好** — 使用 pip User-Agent，避免被镜像拦截

---

## 📦 安装

### 从 PyPI 安装（即将上线）

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

## 🚀 快速开始

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

## 🛠 用法

```text
usage: pypi-downloader [-h] [-r REQUIREMENT_FILE] [--dry-run] [--concurrency N]
                       [--download-dir DIR] [--cn] [--serve] [--serve-port PORT]
                       [--python-version PYTHON_VERSION] [--abi ABI]
                       [--platform PLATFORM] [--all-versions] [--latest-patch]
                       [--url-list-path PATH]
                       [requirements]

异步 PyPI 镜像下载器

位置参数:
  requirements          requirements.txt 文件路径

选项:
  -h, --help            显示帮助信息并退出
  -r, --requirement REQUIREMENT_FILE
                        requirements.txt 路径（pip 格式）
  --dry-run             仅收集 URL 并保存到文件，不实际下载
  --concurrency N       最大并发下载数（默认：16）
  --download-dir DIR    包保存目录（默认：./pypi）
  --cn                  使用国内 PyPI 镜像，自动切换备用镜像
  --serve               下载完成后从下载目录启动 pypiserver 私有 PyPI 服务器
  --serve-port PORT     pypiserver 端口（默认：8080，仅与 --serve 配合使用）
  --python-version PYTHON_VERSION
                        按 Python 版本标签过滤（如 cp311、py3、py2.py3）
  --abi ABI             按 ABI 标签过滤（如 cp311、abi3、none）
  --platform PLATFORM   按平台标签过滤（如 manylinux_2_17_x86_64、win_amd64、any）
  --all-versions        下载每个包所有可用的 Python 3 版本
  --latest-patch        只下载每个次版本的最新补丁版本，与 --all-versions 互斥
  --url-list-path PATH  URL 列表文件的自定义路径（默认：./url_list.txt，仅在预演模式下使用）
```

> **说明：** 依赖始终通过 `pip-compile` 自动解析（需要安装 `pip-tools`），无需额外参数。

### 运行输出示例

```
2026-04-26 12:34:56 | INFO | Packages will be downloaded to: /home/user/pypi
2026-04-26 12:34:57 | INFO | Downloaded: numpy-1.26.4-cp311-cp311-manylinux_2_17_x86_64.whl
...
```

**下载汇总表：**

| 包名   | 版本   | 状态         | 详情                    |
|--------|--------|--------------|-------------------------|
| numpy  | 1.26.4 | Synchronized | All 1 file(s) processed |
| pandas | 2.2.2  | Synchronized | All 1 file(s) processed |
| torch  | 2.3.0  | Failed       | All mirrors failed: 404 |

---

## 📋 进阶示例

### 下载全部版本（构建内部 PyPI 镜像）

适合构建包含所有 Python 3 版本的内部 PyPI 镜像：

```bash
# 下载 requirements.txt 中所有包的全部版本
# 依赖由 pip-compile 自动解析
pypi-downloader -r requirements.txt --all-versions --cn --serve

# 执行流程：
# 1. 使用 pip-compile 解析所有依赖
# 2. 下载所有 Python 3 兼容版本，例如：
#    numpy: 1.19.0, 1.19.1, ..., 1.26.4（全部版本）
#    pandas: 1.0.0, 1.0.1, ..., 2.2.2（全部版本）
# 3. 下载完成后在 8080 端口启动 pypiserver
```

**适用场景**：内网中有不同 Python 3 版本（3.8、3.9、3.11）和架构（x86_64、ARM）的机器，此命令下载所有 wheel 文件，任意机器均可按需安装。

### 最新补丁模式（精简镜像）

只下载每个次版本的最新补丁版本，减少 60-70% 的文件量：

```bash
# 只下载最新补丁：保留 2.1.9（跳过 2.1.3、2.1.5），保留 2.2.8（跳过 2.2.2）
pypi-downloader -r requirements.txt --latest-patch --cn --serve

# 文件量对比示例：
# 使用 --all-versions：numpy 1.19.0, 1.19.1, 1.19.2, ..., 1.26.4（100+ 个版本）
# 使用 --latest-patch：numpy 1.19.5, 1.20.3, 1.21.6, 1.22.4, ..., 1.26.4（约 20 个版本）
```

**优势：**
- 减少 60-70% 的下载文件数
- 下载更快，占用存储更少
- 保持兼容性（补丁版本应向后兼容）

**适合使用的场景：**
- 存储或带宽有限时构建内部镜像
- 信任语义化版本规范（补丁版本 = 仅修复 bug）
- 大多数生产环境场景

**不适合使用的场景：**
- 需要特定补丁版本来规避某个 bug
- 包不严格遵循语义化版本规范

> **注意：** `--latest-patch` 与 `--all-versions` 互斥，不能同时使用。

### 预演模式（预览 URL）

预览将要下载的内容并保存 URL 列表，不实际下载：

```bash
# 预演模式自动将 URL 保存到 ./url_list.txt
pypi-downloader -r requirements.txt --dry-run --cn

# 保存到自定义路径
pypi-downloader -r requirements.txt --dry-run --url-list-path /path/to/urls.txt
```

**使用场景：**
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

### 搭建自托管 PyPI 私有源

下载包并启动私有 PyPI 服务器：

```bash
# 下载包（依赖由 pip-compile 自动解析）
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn

# 下载完成后立即在 8080 端口（默认）启动私有 PyPI 服务器
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn \
  --serve

# 使用自定义端口
pypi-downloader -r requirements.txt \
  --download-dir /var/www/pypi \
  --cn \
  --serve \
  --serve-port 9090
```

从私有服务器安装包：

```bash
pip install --index-url http://localhost:8080/simple/ numpy
```

### 国内镜像加速

使用国内镜像加速下载：

```bash
# 依赖解析和下载均使用国内镜像
pypi-downloader -r requirements.txt --cn
```

支持的镜像源：
- 阿里云、腾讯云、清华大学、中科大、北京外国语大学、上海交通大学、南京大学等
- 某个镜像失败时自动切换备用镜像

---

## 🔧 环境要求

- Python 3.11+
- `aiohttp`、`loguru`、`rich`（随包自动安装）
- `pip-tools`（自动依赖解析所必需）

### 可选依赖

- **pypiserver** — 用于 `--serve`（离线私有 PyPI 服务器）

  ```bash
  pip install pypiserver
  # 或通过 full extras 安装：
  pip install pypi-downloader[full]
  ```

---

## 🤝 贡献

欢迎提交 Pull Request！重大变更请先开 Issue 讨论你的想法。

---

## 📄 许可证

MIT © [Hektorwang]
