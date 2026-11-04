# Mi Monitor Mic

把小米 / REDMI 智能显示器的内置麦克风，经 **USB** 送到 Mac 的 BlackHole 2ch。默认一键安装 **Mac 收音桥 + 显示器 ADB 开关守护**，也可分别安装。

[English](README.en.md) · [兼容性与验证记录](docs/compatibility.md) · [排错](docs/troubleshooting.md)

```text
显示器麦克风 → USB / ADB → scrcpy → PCM 桥接 → BlackHole 2ch → 语音应用
```

独立社区项目，非小米或 BlackHole 官方产品。当前为首个试用版本：USB 送音、ADB 自动恢复和手动启停已验证，使用者反馈连续约一天稳定；登录、长期待机、重启及微信通话仍待验收。

## 使用条件

- macOS 13+、Python 3.9+、Xcode Command Line Tools，以及 scrcpy、ADB、BlackHole 2ch。
- 完整安装及独立守护要求 Android 14+。当前按 ADB 型号 `MiTV_MFFU1` 筛选：REDMI G Pro 32U 已短时实测，27 寸候选见[兼容性](docs/compatibility.md#机型)。
- 显示器开启 USB/ADB 调试，使用支持 USB 数据的连接线，并允许此 Mac 的调试授权。仅有画面或充电不代表 USB 已连通。

只授权可信电脑。显示器 Wi-Fi 可以保持开启，音频始终通过 USB；本项目不连接 IP、不管理网络调试端口、不回退 Wi-Fi。

## 默认一键安装

1. 安装缺少的依赖；已有的可跳过。BlackHole 如提示重启，请按提示操作。

   ```bash
   xcode-select --install
   brew install python scrcpy
   brew install --cask android-platform-tools blackhole-2ch
   ```

2. 从[版本下载页](https://github.com/zetttt/mimonitor-mic/releases)下载 `mi-monitor-mic.zip` 并解压，打开显示器 ADB 调试并接入 USB。若已有其他向 BlackHole 送音的程序，先停止它。
3. 双击 **`Install.command`**，或在解压目录运行：

   ```bash
   ./install.sh
   ```

   安装器会校验随包 APK、配置守护所需权限与待机白名单，再启动 USB 收音。首次授权时会等待显示器确认，最长约 60 秒。已有合法安装只补缺失部分，保留设备绑定、音量与正在运行的组件。
4. 在目标应用中选择 **BlackHole 2ch**，授予该应用麦克风权限，再实际说话验证。

完整包自带已编译的守护 APK，普通用户不需要 JDK 或 Android SDK。源码 checkout 需先[构建守护](docs/guardian.md#源码构建)；维护者通过 `make bundle` 生成 `dist/mi-monitor-mic.zip`，APK 与签名私钥不进入 Git。

## 可选分别安装

| 命令 | 本次安装内容 |
| --- | --- |
| `./install.sh` | USB 收音桥 + ADB 开关守护（默认） |
| `./install.sh --bridge-only` | 仅 Mac USB 收音桥，无需守护 APK |
| `./install.sh --guard-only` | 仅显示器 ADB 守护，无需 scrcpy、BlackHole 或 Swift 编译 |

模式只决定本次安装内容，不移除已有组件。守护会维持显示器的 ADB 开关；授权范围和独立控制见[守护说明](docs/guardian.md)。

| 其他参数 | 用途 |
| --- | --- |
| `--serial YOUR_USB_SERIAL` | 多台显示器时指定设备，标识可由 `./mic.sh devices` 查看 |
| `--gain 5` | 新桥接的音量倍数，默认 `1`；过高会失真，应逐步调整 |
| `--no-start` | 安装但不启动新服务，已有运行状态保持不变 |
| `--adb`、`--scrcpy`、`--server` | 指定现有工具路径；scrcpy 与 server 须版本匹配 |
| `--apk` | 指定守护 APK，需同时提供同目录的 `.apk.sha256` 校验文件 |

更改已有设备绑定、增益或 scrcpy/server 路径需先卸载；重复安装不会用默认值覆盖它们。`--adb` 可直接修复已保存的 ADB 路径，设备绑定与增益保持不变。显式指定 `--apk` 可更新已登记的同签名守护，其余重复安装保留已有版本。所有参数见 `./install.sh --help`。

## 日常操作

```bash
./mic.sh status    # 同时显示桥接与守护状态；USB 不可用时守护显示无法查询
./mic.sh doctor    # 检查已安装桥接的 USB、配置和 BlackHole
./mic.sh stop      # 停止采音并关闭 Mac 登录自启动
./mic.sh start     # 恢复采音并启用 Mac 登录自启动
./uninstall.sh    # 移除已登记的两端组件，保留日志和第三方依赖
```

采音运行时会持续读取显示器麦克风，断流时约 20 秒后重试。停止采音不会停止 ADB 守护；需要手动关闭 ADB 时，先运行 `./mic.sh guard stop`。完整卸载须连接、授权原 USB 设备。

音频只经过内存与临时命名管道，不保存录音。日志和本机配置可能包含 USB 标识，请勿公开原始日志、`config.json` 或 `guardian.json`。

## 安装位置

| 内容 | 当前用户目录下的位置 |
| --- | --- |
| Mac 后台应用 | `~/Applications/Mi Monitor Mic.app` |
| 程序与本机连接配置 | `~/Library/Application Support/Mi Monitor Mic/` |
| Mac 登录任务 | `~/Library/LaunchAgents/local.mimonitor-mic.plist` |
| 状态日志 | `~/Library/Logs/Mi Monitor Mic/service.log` |

安装器在本机编译、临时签名 Mac 应用，安装后可移动源码目录。独立守护只创建连接配置与显示器端应用。安装失败时撤销本轮新增组件；若设备断连导致守护无法移除，保留连接记录供重新接通后清理。

本项目不切换 Mac 默认音频设备。Windows、仅接 HDMI 的 PC 和双电脑自动切换不在当前范围。

## 开发与许可

`make check` 运行隔离测试并构建 Swift 程序；`make guardian` 构建 APK；`make bundle` 生成完整安装包。贡献约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

自写代码采用 [MIT License](LICENSE)。第三方依赖不随安装包分发，许可说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。
