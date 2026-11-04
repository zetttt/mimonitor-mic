# Mi Monitor Mic

Use a supported Xiaomi / REDMI smart monitor's built-in microphone on macOS over **USB**. The default installer sets up both the **Mac audio bridge and monitor-side ADB guard**.

[简体中文 / complete guide](README.md) · [Compatibility and validation](docs/compatibility.md)

```text
Monitor microphone → USB / ADB → scrcpy → PCM bridge → BlackHole 2ch → receiving app
```

Independent pre-release project. USB capture, ADB recovery and manual stop/start tests have passed; the user reports about one day of stable use. Login, long sleep/reboot and WeChat calls remain unverified.

## Default installation

Requires macOS 13+, Python 3.9+, Xcode Command Line Tools, scrcpy, ADB and BlackHole 2ch. The guard requires Android 14+. Enable USB debugging on the monitor and use a USB data cable; authorize the Mac when prompted. The installer currently accepts ADB model `MiTV_MFFU1` only; see compatibility for tested and candidate monitors.

Install missing dependencies and follow BlackHole's restart instructions if prompted:

```bash
xcode-select --install
brew install python scrcpy
brew install --cask android-platform-tools blackhole-2ch
```

Download `mi-monitor-mic.zip` from [Releases](https://github.com/zetttt/mimonitor-mic/releases), extract it and double-click **`Install.command`**, or run `./install.sh` in that directory. The bundle includes a built guard APK, so users do not need a JDK or Android SDK. Stop any other audio bridge before installation. Select **BlackHole 2ch** in the receiving app, grant that app microphone access and verify speech input.

The installer waits for initial USB authorization, sets up the guard before starting capture, and preserves existing device binding, gain and running components. A source checkout requires building the guard first; see [guard setup](docs/guardian.md).

## Separate installation and options

| Command | Components selected |
| --- | --- |
| `./install.sh` | Bridge + ADB guard (default) |
| `./install.sh --bridge-only` | Mac bridge only; no APK required |
| `./install.sh --guard-only` | Monitor guard only; no scrcpy, BlackHole or Swift build required |

These options add selected components without removing existing ones. `--no-start` starts no new services. New bridges default to gain `1`; `--gain 5` adjusts a new installation. Uninstall before changing the saved monitor, gain or scrcpy/server paths. `--adb` can repair the saved ADB path while preserving the monitor binding and gain; it is also accepted by `./uninstall.sh` when the old tool is unavailable. Use `--help` for all options. `--apk` accepts a guard APK with a matching `.apk.sha256` file and can update a managed guard signed with the same key.

```bash
./mic.sh status    # Both components; reports unknown guard state when USB is unavailable
./mic.sh doctor    # USB, configuration and BlackHole checks for an installed bridge
./mic.sh stop      # Stop capture and disable Mac login autostart
./mic.sh start     # Start capture and enable Mac login autostart
./uninstall.sh    # Remove managed components; retain logs and external dependencies
```

Stopping capture leaves the guard running. Use `./mic.sh guard stop` before manually disabling ADB. Uninstalling the managed guard requires its original authorized USB connection. See the [guard guide](docs/guardian.md) for independent controls.

## Scope and development

Capture is continuous while running. Audio stays in memory and a temporary named pipe; no recording file is saved. Do not publish raw logs or local `config.json` / `guardian.json`. Exact installation paths are in the [complete guide](README.md#安装位置).

Monitor Wi-Fi may stay enabled. This project does not connect over IP, manage network-debugging ports or change default macOS audio devices. Windows and HDMI-only transport are outside the current scope.

`make check` runs isolated tests and Swift builds. Maintainers use `make bundle` with JDK 17 and the Android SDK to produce `dist/mi-monitor-mic.zip`; signing keys are excluded. See [contributing](CONTRIBUTING.md). Our code uses the [MIT license](LICENSE); external dependencies retain their [upstream licenses](THIRD_PARTY.md).
