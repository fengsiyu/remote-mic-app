# Linux amd64 桌面版

- 状态：候选实现。31 项自动化测试、编译检查和 `.deb` 构建验证通过；真实 Ubuntu、BlueZ、PulseAudio/PipeWire、遥控器、输入注入和目标语音工具尚未验收。
- 产品规范：[`PRODUCT_SPEC.md`](PRODUCT_SPEC.md)。
- 平台映射：[`platform-linux.md`](platform-linux.md)。
- 测试手册：[`Testing/LinuxDesktop.md`](../../Testing/LinuxDesktop.md)。

## 用户问题

Ubuntu 桌面用户无法把现有兼容蓝牙语音遥控器接入无线麦 SayAll 的语音和基础按键链路。

## 本次提供

- Ubuntu 20.04 及以上 `amd64` 的 `.deb` 安装包。
- BlueZ D-Bus 发现、配对、连接和 ATVV 语音能力协商。
- 16 kHz 单声道 IMA ADPCM 解码和自然排空。
- 名为 `SayAll` 的 PulseAudio/PipeWire Pulse 兼容虚拟音源。
- 语音键按住/释放映射、基础按键映射、按住连发和输出音量。
- GTK 3 设置窗口、可选状态栏、中英文静态文案和脱敏日志；实时状态本地化仍待完善。

## 明确不做

- 不修改 Swift/macOS 目标，也不创建 iOS、Web 或服务端子项目。
- 不提供 macOS/Windows 的完整设置、会员、方案、Onboarding、自动更新或第三方输入法自动切换。
- 不实现 RC003 的 `MIC_EXTEND` 长会话续租。
- 不宣称当前候选版本已兼容真实硬件或目标语音工具。

## 构建与测试

```bash
PYTHONPATH=linux python3 -m unittest discover -s linux/tests -t linux -v
bash linux/packaging/build-deb.sh
```

产物为 `dist/sayall-linux_0.1.0_amd64.deb`。安装后需要重新登录，使 udev `uaccess` 和 uinput 权限生效。目标语音工具必须选择 `SayAll` 麦克风，并把按住说话快捷键配置为同一个触发键，默认 `F5`。
