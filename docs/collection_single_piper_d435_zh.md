# 单台 PiPER + D435 + PICO + EVA 数采

包含首次配置、逐段操作、文件检查和故障排查的详细版本，见 [数采完整操作手册](../数采完整操作手册.md)。

代码位于 `piper_xr/collection/`，不依赖 ROS1/ROS2。入口使用本机已经安装的三个环境：

| 进程 | Python 环境 | 职责 |
|---|---|---|
| PiperXR robot worker | `~/anaconda3/envs/pico_teleop` | XR 输入、placo IK、唯一 CAN 控制端、关节/夹爪反馈 |
| D435 camera worker | `~/realsense_src/.venv` | 复用 `scripts/camera.py` 的设备选择、流配置、标定/时间戳；RGB、原始深度、对齐深度 |
| bridge / EVA | `~/EVA-CLIENT/.venv` | ZMQ、运动授权、LeRobot、RGB 视频、质检、RGB-D HDF5 和逐帧关联 |

EVA 与 RealSense 原项目无需源码修改或重新安装。扩展由本项目的 `eva_entry.py` 在进入 EVA 前注册。因此必须用这里的启动脚本；直接用普通 `eva --config ...` 不会注册 `single_piper_d435` / `piperxr`。

## 配置

编辑 `configs/collection_single_d435.json`：

- `robot.can`：实际 CAN 名，默认 `can0`。脚本不会自动修改系统 CAN。
- `robot.hand`：PICO 控制手，默认 `right`。
- `robot.scale`：位移缩放，默认 0.7。
- `robot.max_dq`：每周期最大关节目标增量，默认 0.02 rad；控制循环约 50 Hz，实际频率包含计算耗时。
- `robot.speed`：SDK 速度比例，默认 20。
- `robot.gripper_open_m`：最大开口，默认 0.08 m，按你的夹爪标定。0 表示闭合。
- `robot.gripper_effort`：SDK 力矩数值，默认 1000，单位 0.001 N·m。
- `camera.serial`：一台相机可留空，多台设备时填写 D435 序列号。
- `camera.width` / `camera.height` 与顶层 `fps`：默认 640×480、30 FPS；帧率不是 `camera.fps`。必须是设备支持的 RGB+Z16 组合。
- `tasks`：数据集名到 `[任务描述, 目标段数]` 列表；`-1` 不限制数量。
- `output_dir`：数据根目录，相对路径从 PiperXR 项目根目录解析。
- `max_robot_skew_s`：接收时刻的机器人/相机样本配对上限，默认 0.06 s。录制时超过即报错停止桥接。
- `image_skew_tolerance_s`：EVA 固定时间网格选图的最大时间差，默认 0.05 s。

机器人状态/动作都是 7 维：`[joint1..joint6(rad), gripper_opening(m)]`。动作记录限幅、整数单位转换后实际发出的关节目标；保持期间记录最后下发的目标。夹爪保存开口宽度，不混用原项目的闭合归一化。EVA 从关节分别计算状态/动作的 8 维末端位姿：`[x,y,z,qw,qx,qy,qz,gripper]`，使用 `gripper_base`；PiperXR 遥操作控制的是 `link6`。两者不是相同的末端参考点。

## 1. 软件检查

```bash
cd ~/picoxpiper/PiperXR-master
bash scripts/collect.sh check
```

检查三个环境的必要模块，并生成/加载 EVA 单臂配置。不连接相机、不使能机械臂。配置生成在输出目录中的 `eva_single_piper_d435.py`。

## 2. 无硬件体验

```bash
bash scripts/collect.sh mock
```

浏览器打开 `http://127.0.0.1:8080`。合成彩色/深度和单臂数据走真实 EVA 保存流程。模拟数据写入独立的 `_mock` 目录，HDF5 标为 synthetic。

1. COLLECT 中选择 DATASET NAME 与 TASK/PROMPT。
2. 将 MOTION 从 LOCKED 切换为开启。
3. 点击 START RECORD；模拟机械臂动作仅在 MOTION 开启时产生。
4. 停止录制、等待转换队列完成；回放并标记 PASS/FAIL。
5. 点击 CANCEL 放弃当前段；取消的数据不会进入训练 episode。对应 raw RGB-D 暂存保留用于排错。
6. 下一段再次 START RECORD；停止录制本身不会关闭 MOTION。
7. 结束时先关闭 MOTION，然后在启动终端 Ctrl+C，等待后台保存退出。

## 3. 相机与 CAN 准备

先关闭占用 D435 的 Viewer/其他录制脚本。

```bash
source ~/realsense_src/env.sh
python ~/realsense_src/scripts/camera.py check --require-camera --frames 60
```

若提示 USB 规则缺失，在本地终端按 RealSense 文档执行一次：

```bash
bash ~/realsense_src/scripts/setup-system.sh --with-tools
```

CAN 使用 1 Mbps。先检查：

```bash
ip -details link show can0
```

如果尚未配置，且确定 `can0` 是这台 PiPER 的适配器，可在本地终端执行：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

这些命令改变实际硬件接口状态。不要同时运行另一个 PiperXR 真机实例、Piper ROS 驱动或其他 CAN 控制程序。

## 4. 真机采集

先启动 PC 服务，在 PICO 内打开 XRoboToolkit 并连接电脑：

```bash
cd ~/picoxpiper/PiperXR-master
bash scripts/start_pc_service.sh
```

另一个终端：

```bash
cd ~/picoxpiper/PiperXR-master
bash scripts/collect.sh real
```

也可以在 PC 服务未运行时用 `bash scripts/collect.sh real --start-pc-service` 一起启动；不要重复启动服务。

确认画面、反馈及环境后开启 EVA 的 MOTION。按住 PICO grip 才使能并控制机器人，trigger 控制夹爪。释放 grip 时将目标保持到当前反馈位置；再次握住重新捕获参考。为避免采集开始时姿态跳变，新入口保留激活瞬间实际朝向，不使用自动朝下基准。

首次试采 10–20 秒：少量平移、少量转腕、夹爪开合，然后保存/回放。核对开合方向、数据中的米/弧度、目标与反馈差异。硬件紧急停止仍使用机械臂自身的急停方案；软件 MOTION/断线保持不等同于硬件急停。

本配置只接受 PiperXR 手柄控制。EVA 的 HOME、手动关节按钮和策略动作不会被转发到 CAN，会明确报错。回到起始位请在下一段录制前用 PICO 遥操作完成。PICO 上的 WebXR A/B 录制快捷键不适用于 XRoboToolkit；使用 EVA 页面按钮/键盘提示。

## 5. 数据文件

本机 EVA 实际保存路径含 `raw/`：

```text
work_dirs/collection/single_piper_d435/
├── eva_single_piper_d435.py
├── raw_rgbd/capture_<uuid>.h5              # 每次录制的完整收到帧及 EVA 消费帧清单
└── pick_place/raw/
    ├── data/chunk-000/episode_000000.parquet
    ├── videos/chunk-000/observation.images.cam_high/episode_000000.mp4
    ├── rgbd/chunk-000/episode_000000.h5     # 与 Parquet/RGB 视频逐行对应
    ├── rgbd/chunk-000/episode_000000.json   # 同步与来源说明
    └── meta/{info.json,episodes.jsonl,episodes_stats.jsonl,tasks.jsonl,stats.json}
```

`rgbd/episode_*.h5` 保存：RGB 原图、原生 `depth_raw`、对齐彩色像素的 `depth_aligned`、状态/动作配对样本、相机帧号/时间戳、主机时间、源帧索引。`capture_time` 与 Parquet 一致，`source_capture_time` 是被选中相机帧接收时刻，`image_skew_s` 是两者之差。

HDF5 的 `calibration` 属性包含内参、畸变、深度比例和深度→彩色外参；`settings` 保存采集配置。深度为无损 uint16，`depth_m = depth * depth_scale_meters`，零值无效。RGB-D 副本由后台任务在 EVA 保存结束后生成，可能稍晚于界面队列完成。

桥接采用本机单调接收时钟；还原保留相机原始时间域。不是硬件曝光同步，真实传输延迟/漂移需要硬件试采测量。夹爪和关节通过 SDK 反馈读取，CAN 反馈本身也不是原子同步帧。

EVA 的普通 QUALITY EXPORT 不会自动复制自定义 `rgbd/` 目录。需要深度/点云的训练请保留整个 `<任务>/raw`，或者单独携带 `rgbd/` 与标准数据集；不要以为 LeRobot 导出自动带上深度。

如果进程退出前转换尚未完成，重新关联：

```bash
bash scripts/collect.sh link
# 模拟数据：
bash scripts/collect.sh link --mock-data
```

不会覆盖已关联的完整 episode。保存中断会留下 `.partial.h5`，不将其视为完整数据。原始暂存不会自动清除，应在核验最终数据后自行管理磁盘空间。

## 6. 深度图与点云

```bash
bash scripts/export_pointcloud.sh \
  work_dirs/collection/single_piper_d435/pick_place/raw/rgbd/chunk-000/episode_000000.h5 \
  --frame 30 --output work_dirs/pointcloud_episode0_frame30 --denoise
```

输出 `color.png`、原始/对齐深度的 16 位 PNG、`points.ply`、标定 JSON。默认每两像素取一点，截取 2 m 内、5 mm 体素滤波，可调整 `--stride`、`--max-depth`、`--voxel`。点云通过 RealSense 的去投影/外参/投影 API 着色，坐标是原生深度光学系（右、下、前），单位米。若要转换到机械臂基座系，需要另外测得实际相机外参，代码不会假造这个标定。

## 7. 验证命令

```bash
~/EVA-CLIENT/.venv/bin/python -m pytest -q tests/collection
~/anaconda3/envs/pico_teleop/bin/python scripts/validate_collection_robot.py
~/EVA-CLIENT/.venv/bin/python scripts/validate_collection_offline.py \
  --output /tmp/piperxr_offline_check_new
~/EVA-CLIENT/.venv/bin/python scripts/test_collection_network.py \
  --output /tmp/piperxr_network_check_new
```

输出目录必须不存在。离线验证实际调用 EVA 录制器/运动学/视频编码，并检查 7D 状态/动作、8D 末端、帧数、深度位深和取消操作。网络测试强制 mock，使用 15555–15557/15757/18080 独立端口，不连接真实硬件。

已验证软件路径不代表 CAN、D435 USB、PICO、机械臂运动和长时磁盘吞吐已在实机验证；首次实机必须执行上面的短段试采。

启动时 bridge 和两个 worker 的完整输出会保存到本次输出目录的 `logs/bridge_*.log`。若显示 `Collection bridge failed during startup`，查看其附带的日志路径、退出码和原始异常摘要。真机曾发现的 `can_auto_init=False` 缺少 `CreateCanBus()` 问题已修复，并以真实 SDK 加模拟底层 CAN I/O 验证初始化顺序。
