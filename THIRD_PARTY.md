# Third-party components

This repository contains source for the bridge, background app, optional Android guard and setup tools. Dependencies are separately installed programs/devices. Their source, official binaries, installers, logos and signing material are not included.

| Component | Purpose | Upstream and license |
| --- | --- | --- |
| scrcpy | Capture monitor microphone audio through ADB | [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy), [Apache-2.0](https://github.com/Genymobile/scrcpy/blob/master/LICENSE) |
| Android Debug Bridge | Authorized USB transport | [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools); see the notices in the official distribution |
| BlackHole 2ch | Expose the received audio as a virtual input | [ExistentialAudio/BlackHole](https://github.com/ExistentialAudio/BlackHole), [upstream license](https://github.com/ExistentialAudio/BlackHole/blob/master/LICENSE) |
| Python | Run the bridge and installer | [Python](https://www.python.org/), [license](https://docs.python.org/3/license.html) |

BlackHole's source license is GPL-3.0. Its upstream license file separately describes rights for official compiled binaries, installers and branding. Obtain BlackHole through its official channels; this project neither includes nor rebrands it.

The MIT license in this repository applies to our own files, not to the components above. Xiaomi, REDMI and BlackHole names identify compatibility and dependencies; no affiliation or endorsement is implied.

Tested versions are recorded in [compatibility](docs/compatibility.md). The installer uses existing local tools rather than downloading a fixed binary bundle.

Building the optional guard requires the [Android SDK](https://developer.android.com/studio#command-tools) and a JDK 17 distribution, under their respective licenses. These are build tools only and are not bundled in the guard APK.
