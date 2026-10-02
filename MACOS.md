# Factory Switch — macOS 适配版

可从 [GitHub Releases](https://github.com/shelbyluocus-wq/factory-switch/releases/latest) 下载 Apple Silicon 或 Intel 对应的 DMG，将应用拖入 Applications，无需安装 Python。GitHub 的 macOS 构建环境已完成打包与原生依赖/界面资源检查；真实账号切换和系统授权尚未实机验证。

以下是源码启动和自行打包的方法。

## 首次启动

1. 在 Mac 上安装 Factory Desktop，放到 `/Applications/Factory.app`，先完成一次正常登录。
2. 安装 Python 3.11 或更新版本，可使用 https://www.python.org/downloads/macos/ 的安装包。
3. 解压本文件所在文件夹，双击 `Launch.command`。首次运行会创建独立的 `.venv-mac` 并安装 `requirements.txt` 中的依赖，需要联网。
4. 出现系统钥匙串访问提示时，由你确认。切号器需要读取 Factory 的登录密钥，并为自己的加密备份创建单独的密钥。
5. 保存当前账号，添加并登录第二个账号，然后保存第二个账号。之后可在列表里切换。

如果解压工具未保留脚本执行权限，可在终端进入解压目录，运行：

```bash
bash Launch.command
```

默认使用 Cocoa / WKWebView、macOS 原生标题栏及窗口缩放，保留现有大字体界面。建议 macOS 13 或更新版本。刷新快捷键为 `Command R`。

Factory 安装在其他目录时：

```bash
FACTORY_SWITCHER_APP="$HOME/Applications/Factory.app" bash Launch.command
```

## 生成 .app

先启动一次，依赖安装完成后关闭切号器，双击 `Build-mac.command`。

脚本会运行测试并在当前 Mac 上用 PyInstaller 生成 `dist/Factory Switch.app`，可拷到应用程序目录。生成架构取决于运行脚本的 Python：Apple Silicon 使用原生 arm64 Python，Intel Mac 使用 x86_64 Python；此脚本不生成 Universal 2 应用。

不能在 Windows 上直接生成可验证的 macOS `.app`。本地生成的应用未做开发者签名和公证，不应当作已经完成分发验证的安装包。

## 数据与认证

- Factory 登录目录：`~/.factory`。
- macOS 默认认证文件：`auth.v2.loginkeychain`；同时支持使用钥匙串的 `auth.v2.keyring`。若两者同时存在则停止操作，不猜测 Factory 正在使用哪一个。
- Factory 钥匙串 service：`Factory CLI`，对应 account 分别为 `auth-encryption-key-security-cli` 和 `auth-encryption-key`。
- 切号器备份目录：`~/Library/Application Support/FactoryAccountSwitcher`。
- 备份使用 AES-256-GCM 加密，独立密钥存于系统钥匙串的 `Factory Account Switcher / backup-encryption-key`。
- 账号备份后缀 `.keychain`，恢复点为 `recovery.keychain`。Windows 的 DPAPI 备份不能拷来使用，需要在 Mac 上重新登录保存。
- 不保存明文 token；密钥不会放到命令行参数里，不记录钥匙串输出。
- 切换前请求 Factory 正常退出。首次可能出现 macOS 自动化授权；如拒绝，可手动退出 Factory 后再切换。独立 Droid CLI 也必须先退出。
- 保留项目、会话与设置，只替换认证文件和组织策略缓存；这不是完全隔离的账号环境。

## 实机验收

在没有运行中任务时完成：

1. 启动、窗口缩放、字号、用量显示与 `Command R` 刷新。
2. 钥匙串允许/拒绝两种情况；拒绝时应显示错误，登录文件保持不变。
3. 保存 A、添加并保存 B，执行 B → A → B，在 Factory 确认账号一致。
4. 有独立 Droid CLI 时，应拒绝切换。
5. 退出或重启失败时，检查原登录仍在；必要时从终端运行 `.venv-mac/bin/python switcher.py recover`。
6. `.app` 启动及首次自动化授权；关闭应用期间的操作保护。

认证协议依据本机 Factory Desktop 0.189.0 的跨平台打包代码核对。若 Mac 上的 Factory 版本改变了认证存储，可能需要更新适配。
