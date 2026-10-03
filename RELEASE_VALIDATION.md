# v0.2.0 发布验证记录

核对日期：2026-10-03（Asia/Shanghai）。

## 发布来源

- 仓库：[shelbyluocus-wq/factory-switch](https://github.com/shelbyluocus-wq/factory-switch)。
- 发布页：[Factory Switch v0.2.0](https://github.com/shelbyluocus-wq/factory-switch/releases/tag/v0.2.0)。
- 发布标签：`v0.2.0`，指向 `7915851c7eb3d273392513b4e6c6135f7cfae7ab`。
- 关联打包构建：[Actions 36987624658](https://github.com/shelbyluocus-wq/factory-switch/actions/runs/36987624658)，代码提交为 `3a78e8225c1f8cc0760b27ce2e9f6d66c1f30e3b`。
- 从构建提交到发布标签，仅更新了 README 和平台验证文档，没有修改程序源码。

## 自动构建结果

三个平台的构建任务均已通过，工作流包含 Python 单元测试、JavaScript 启动检查、PyInstaller 打包及冻结程序的原生依赖和界面资源检查。

| 平台 | 构建状态 | 产物 | 额外检查 |
| --- | --- | --- | --- |
| Windows Server 2022 x64 | 通过 | Inno Setup EXE | 原生依赖与资源检查 |
| macOS 15 arm64 | 通过 | DMG | `hdiutil verify` |
| macOS 15 Intel x64 | 通过 | DMG | `hdiutil verify` |

Intel Mac 构建曾遇到 OpenSSL 缺失符号 `SSL_get0_group_name`。打包配置已改为保留 cryptography 实际链接的 `libssl` / `libcrypto`，避免同名库被 Python 自带的其他版本覆盖。修复后的三个平台构建通过。

Release 当前包含 Windows x64 安装包、macOS arm64 与 x64 的 DMG，以及 `SHA256SUMS.txt`。本文确认了资产存在，不代表在本次核对中重新下载并核验了全部安装包。

## 主分支与发布版的区别

发布标签之后，`2ba6809d48081cc7ae00a7d75d7216df65795cc3` 修复了 Factory 进程在关闭前已退出时的处理，并统一了窗口、应用和安装程序图标。

该提交的[三个平台构建](https://github.com/shelbyluocus-wq/factory-switch/actions/runs/37096984181)同样通过，产物保存在 Actions 中；这些改动没有包含在 `v0.2.0` 标签内。下载 v0.2.0 安装包不能视为已经获得主分支的全部修复。

## 验证边界

- Windows 真实账号往返切换的历史记录见 [VALIDATION.md](VALIDATION.md)。
- macOS 构建通过不代表真实账号、钥匙串授权、自动化授权和桌面交互已完成实机验收。
- 本次没有重新执行真实账号切换，也没有逐一安装三个平台的发布包。
- 安装包未使用 Windows 发布者证书或 Apple Developer ID 进行分发签名，macOS 未完成公证。
- `SHA256SUMS.txt` 可用于核对文件完整性，不替代发布者签名。
- 虚拟环境、构建目录、登录文件和加密账号备份不纳入 Git；安装包通过 Release 或 Actions 分发。
