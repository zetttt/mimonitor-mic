# USB ADB 开关守护

当显示器关闭 ADB 调试时，Mac 会失去设备，无法从已经断开的连接重新打开开关。默认完整安装包含 `Mi Monitor Mic Guard`，在显示器本地维持该开关，再由 Mac 接续 USB 收音。

守护仅写入 `Settings.Global.ADB_ENABLED`，不连接 Wi-Fi、不管理 TCP 端口、不采集声音，也不申请网络、录音、辅助功能或 Root 权限。它不替代 USB 授权、不修复线材或端口模式。

## 默认安装与独立安装

完整安装包自带守护 APK 与 SHA-256 校验文件。双击 `Install.command` 或运行 `./install.sh` 即可同时安装桥接和守护，普通用户无需 JDK / Android SDK。

只安装守护时使用 `./install.sh --guard-only`：要求 Android 14+、ADB 手动开启并授权，只需要已有 ADB 工具，不依赖 Mac 收音桥。只安装桥接使用 `./install.sh --bridge-only`。

已有桥接时也可运行 `./mic.sh guard install`。安装器只针对绑定的 USB 设备，启用守护时授予设置、通知权限并加入待机白名单；`--no-start` 不启动新守护，设置与通知权限在首次启用时授予。记录保存在运行目录的 `guardian.json`，不要公开该文件。

## 源码构建

维护者需要 JDK 17，以及 Android SDK 的 `platforms;android-34`、`build-tools;35.0.0`。通过官方 SDK Manager 安装，设置 `JAVA_HOME` 和 `ANDROID_HOME` 后运行：

```bash
make guardian   # .build/mi-monitor-mic-guard.apk 与 .apk.sha256
make bundle     # dist/mi-monitor-mic.zip，内含 APK 和可双击的安装入口
```

APK 校验不符时在安装前退出。源码 checkout 可直接使用本地构建产物；完整包优先使用随包的 `assets/` 产物。

`WRITE_SECURE_SETTINGS` 是高权限授权；本项目源码只用它维护上述一个开关。应用以可见前台服务运行，观察设置变化，并每 10 秒补查一次；开机或应用更新后，仅在此前已启用时恢复守护。实现采用 Android 的 [specialUse 前台服务](https://developer.android.com/develop/background-work/services/fgs/service-types#special-use)。

## 启停与移除

```bash
./mic.sh guard status      # 查询守护与 ADB 状态、当前进程恢复次数
./mic.sh guard stop        # 停用守护，保持 ADB 当前状态
./mic.sh guard start       # 重新启用守护
./mic.sh guard uninstall   # 移除本项目守护及本次添加的待机白名单
```

停止 Mac 采音不会停止显示器守护。若要手动关闭 ADB，先停用守护；也可在显示器打开 `Mi Monitor Mic Guard`，使用“停用守护”按钮。若 USB 已失联，Mac 命令无法控制显示器，需要在显示器操作。

Mac 的 `./uninstall.sh` 会先移除已登记的守护，因此此时须接通、授权原 USB 设备。既有待机白名单不删除，ADB 开关、网络设置与第三方应用保持原状态。

## 签名与验证范围

构建使用本机开发签名，密钥保存在 `~/.local/share/mimonitor-mic/guardian.p12`，不进入仓库；更新同一设备的 APK 时复用该密钥。更换签名需要先卸载原 APK。仓库不分发 SDK、JDK、私钥或旧 AdbGuardian 补丁包。

实测结果见[兼容性记录](compatibility.md)。短时自动恢复测试不能替代长期待机、强杀、重新登录和真实断电重启验收；固件拒绝权限或终止守护时，仍可能需要人工开启 ADB。
