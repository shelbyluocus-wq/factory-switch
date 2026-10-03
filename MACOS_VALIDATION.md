# macOS 适配验证记录

日期：2026-10-02。执行主机：Windows。

## 已确认的源码依据

读取本机 Factory Desktop 0.189.0 的 `resources/app.asar`，仅检查应用代码：

- `YOe` 在 `process.platform === "darwin"` 时优先选择 security CLI 后端。
- `jOe.getPassword` 使用 `/usr/bin/security find-generic-password -s <service> -a <account> -w`。
- 默认 service 为 `Factory CLI`；security CLI 后端 account 为 `auth-encryption-key-security-cli`，文件为 `auth.v2.loginkeychain`。
- keyring 后端 account 为 `auth-encryption-key`，文件为 `auth.v2.keyring`。
- `QN` / `GS` 使用 AES-256-GCM，32 字节密钥，16 字节 IV 和 tag，三个 Base64 字段由冒号分隔。
- 本地 pywebview 6.2.1 的依赖元数据声明了 Darwin 的 PyObjC / Cocoa / WebKit 依赖；Cocoa 后端支持原生 resizable 窗口和关闭事件。

## 自动测试

`python -X utf8 -m unittest -v`：34 项，33 项通过，1 项跳过（Windows 无 POSIX 文件锁）。

新增测试覆盖：钥匙串未找到与拒绝访问的区分、按后端选取密钥、备份密钥不通过 argv 传递、缺失解密密钥时不重新生成、AES-GCM 往返与篡改检测、Droid 进程识别和拒绝切换、AppleScript 参数传递、模拟登录文件的双账号往返、失败回滚、两套认证文件冲突。

所有 macOS 钥匙串与生命周期调用均使用模拟数据；测试未修改真实 Factory 登录。Windows 原有 23 项回归继续通过。

## 未验证

本节记录的是 2026-10-02 在 Windows 上验证时的边界。当时尚未验证实际 macOS `security` / `osascript` / `ps` 行为、系统授权提示、POSIX 锁、WKWebView 显示、PyInstaller `.app` 打包，以及真实 Mac 双账号往返切换。后续已确认的原生构建检查见下节；真实账号与系统授权仍需按 `MACOS.md` 完成实机验收。


## GitHub 原生构建检查

2026-10-03，[v0.2.0 关联构建](https://github.com/shelbyluocus-wq/factory-switch/actions/runs/36987624658)已在 macOS 15 的 Apple Silicon 和 Intel 环境通过 Python 测试、JavaScript 启动检查、PyInstaller 打包、Cocoa 原生依赖与界面资源检查，以及 DMG 创建和 `hdiutil verify`。

这确认了构建环境中的原生依赖、POSIX 锁测试和打包资源可用，但没有使用真实 Factory 账号，也没有人工操作桌面界面或授权提示。不能据此宣称真实 Mac 双账号切换已验证。发布标签与后续主分支构建的区别见 [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md)。
