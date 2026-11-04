# Contributing

Reproduce the issue and check [compatibility](docs/compatibility.md) before changing code.

- Keep capture USB-only and bound to the selected monitor; avoid firmware or network-debugging changes.
- Preserve no-recording behavior. Test with synthetic samples and temporary named pipes.
- Limit installation and cleanup to project-owned files and the private ADB server.
- The optional Android guard may write only `ADB_ENABLED`; keep network settings, recording and accessibility outside its scope.
- Report software checks separately from hardware, wake/reboot and receiving-app acceptance.

Run `make check` on macOS with Python 3.9+ and Xcode Command Line Tools. Tests simulate dependencies and build the actual Swift sources in isolated directories without starting a LaunchAgent; no connected monitor, scrcpy, ADB or BlackHole is required.

Include steps, versions and redacted errors in bug reports. Do not submit raw configuration, serials, personal paths or recordings. For dependency changes, link upstream documentation and update compatibility notes with actual verification results.

`make guardian` builds the Android source and checksum with JDK 17, SDK Platform 34 and Build Tools 35.0.0. `make bundle` packages the built APK, double-click installer and source into `dist/mi-monitor-mic.zip`; users of that bundle do not need Android build tools. Reuse the same private signing key for updates. See [guardian setup](docs/guardian.md); keep APKs, bundles and signing keys out of source control.
