# Linux 平台映射

本附件只说明 Linux 实现如何映射 [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) 的产品行为，不新增或降低产品规则。当前目标为 Ubuntu 20.04 及以上、`amd64`。

## 运行形态

| 项目 | 实现 |
| --- | --- |
| 语言与运行时 | Python 3.8+，使用 Ubuntu 20.04 自带的 GTK 3 和 D-Bus 绑定 |
| 界面 | GTK 3 窗口；可选 Ayatana/AppIndicator 状态栏 |
| 安装 | `.deb`，入口 `scripts` 对应的 `linux/packaging/build-deb.sh` |
| 自启动 | `/etc/xdg/autostart/sayall-linux.desktop` 调用 `sayall-linux --minimized` |
| 单实例 | 当前不提供跨进程锁；重复启动会重复初始化音频和输入接管，测试时必须只运行一个实例 |

## 蓝牙与协议

| 项目 | 实现 |
| --- | --- |
| 传输 | BlueZ System D-Bus：`org.bluez` ObjectManager、Adapter1、Device1、GattCharacteristic1 |
| 配对 | 注册 `NoInputNoOutput` Agent；优先使用系统已完成配对的设备 |
| 设备筛选 | 已知名称或 ATVV Service UUID `ab5e0001-5a21-4f05-bc7d-af01f617b664` |
| 语音协议 | ATVV v1.0/兼容能力；16 kHz IMA ADPCM；高半字节优先，ARN9 型号使用低半字节优先 |
| 适配器 | 使用第一个 BlueZ Adapter；不提供适配器选择 |
| 连接范围 | 单只选中遥控器；只保留一条语音 generation 链路 |

## 音频

| 项目 | 实现 |
| --- | --- |
| 音源 | `pactl load-module module-pipe-source source_name=SayAll` |
| 格式 | 16 kHz、单声道、`s16le` |
| FIFO | `$XDG_DATA_HOME/sayall-linux/sayall-mic.fifo`，默认 `~/.local/share/sayall-linux/sayall-mic.fifo` |
| 后端 | PulseAudio，或提供 Pulse 兼容 `pactl`/module 接口的 PipeWire 环境 |
| 静音保活 | 无 PCM 时按 16 kHz 写入静音块，避免虚拟音源停止 |
| 正常结束 | 停止 generation 后排空写入队列；不 flush 在途数据 |
| 异常结束 | 断连、退出或强制停止标记 `completion=forced`，并记录未写样本 |

依赖包同时安装 `pulseaudio-utils`。如果用户的 PipeWire 环境没有提供兼容 `pactl` 或 `module-pipe-source`，虚拟麦克风必须显示未就绪，不得回退电脑麦克风。

## 输入和按键

| 项目 | 实现 |
| --- | --- |
| 抓取 | 通过 `/sys/class/input/event*` 发现已知名称或 Vendor/Product `0x2717:0x32B8` 的设备 |
| 独占 | `EVIOCGRAB` 独占遥控器事件接口 |
| 注入 | `/dev/uinput` 创建 `SayAll Linux Remote` 键盘 |
| 权限 | 包内 udev 规则为 `uinput` 和匹配遥控器设置 `uaccess`；用户需重新登录或重新插拔/重连 |
| 语音键 | 物理 F5 边沿映射为按住/释放用户配置的触发键，默认 F5 |
| 普通键 | 12 个基础按键映射；方向、返回和音量键支持按住连发 |
| 无权限 | 不抓取、不注入，保留系统原生直通，并显示权限不足 |

Wayland 和 X11 都通过 Linux 输入子系统注入；这不代表所有合成器、输入法或目标 App 都会接受 uinput 按键，仍需实机验证。

## 配置与日志

| 项目 | 路径 |
| --- | --- |
| 配置 | `$XDG_CONFIG_HOME/sayall-linux/config.json`，默认 `~/.config/sayall-linux/config.json` |
| 日志 | `$XDG_STATE_HOME/sayall-linux/sayall.log`，默认 `~/.local/state/sayall-linux/sayall.log` |
| 日志轮转 | 单文件 1 MiB，超出后重命名为 `sayall.log.1` |

配置损坏时，原文件最多保留一份 `.corrupt` 备份，随后使用默认配置。配置和日志权限必须保持仅当前用户可读写。

## 已知平台边界

- 未实现 `MIC_EXTEND`；RC003 约 60 秒会话租期未在 Linux 真机验证。
- 不读取或显示遥控器电量，不提供多设备卡片、排序或独立路由。
- 不提供 macOS 的 `MIC_OPEN` 主动预热、输入法切换、辅助功能聚焦、滚动和触摸能力。
- 状态栏依赖 Ayatana/AppIndicator；缺失时只提供普通窗口。
- 当前依赖用户自行在目标语音工具中配置相同触发键；不会自动修改第三方工具设置。
