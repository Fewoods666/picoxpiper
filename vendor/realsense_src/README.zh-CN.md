# D435 具身智能数采环境

安装位置：`/home/wtc/realsense_src`。适配本机 Ubuntu 22.04、x86_64、Linux 6.8。

## 已配置组件

| 组件 | 作用 |
| --- | --- |
| librealsense 2.58.4，RSUSB 后端 | C/C++ SDK，直接通过 libusb 访问相机，无须打内核补丁 |
| RealSense Viewer | RGB/深度预览、参数调整、录制回放、点云查看 |
| rs-depth-quality | 深度平面拟合与质量检查 |
| rs-enumerate-devices、rs-record、rs-convert | 设备枚举、录制、文件转换 |
| Python 3.10 独立虚拟环境 | 不修改现有 Anaconda 环境 |
| pyrealsense2 2.58.4.10922 | 官方 Python 相机接口 |
| OpenCV contrib 4.11 | 图像处理、ArUco/ChArUco、相机标定及手眼标定算法 |
| Open3D CPU 0.19 | 点云读取、滤波、配准与几何处理 |
| NumPy、SciPy、h5py、Matplotlib | 数组运算、HDF5 数据集、分析绘图 |
| imageio-ffmpeg | Python 环境内可调用的 FFmpeg 可执行文件 |

此环境按独立 RGB-D 数采配置，未安装 ROS 2、机器人控制栈或训练框架。
SDK 内置 `.db3` 文件支持不等于已安装 ROS 2 或 RealSense ROS 驱动。

## 首次连接前

系统 USB 权限规则需要本机管理员密码。执行一次：

```bash
bash /home/wtc/realsense_src/scripts/setup-system.sh --with-tools
```

该命令安装官方 udev 规则并重载，同时通过 apt 安装 `v4l-utils`、系统 FFmpeg 和 `python3-venv`。
如只需要 USB 规则，省略 `--with-tools`。不需要修改用户组或以 root 运行 Viewer。
完成后拔插相机。密码只在本机终端输入。

使用支持数据传输的 USB 3 线直连电脑 USB 3 端口，尽量避开无源扩展坞。
执行 `lsusb -t`，相机所在分支应为 `5000M` 或更高，`480M` 表示当前连接退化为 USB 2。
接口外观或电脑根控制器标称速度不能证明相机实际工作在 USB 3。

## 启动与检查

每个用于采集的新终端先执行：

```bash
cd /home/wtc/realsense_src
source env.sh
python scripts/camera.py check
rs-enumerate-devices
realsense-viewer
```

也可从应用菜单启动 **RealSense Viewer (D435)**。
Viewer 和采集脚本不要同时占用同一台相机。

关闭 Viewer 后进行 150 帧实机检查：

```bash
python scripts/camera.py check --require-camera --frames 150
```

默认使用 640×480、30 FPS 的 RGB + Z16 深度流，输出设备型号、序列号、固件版本、USB 版本、非零深度比例及观察到的帧号缺口。
深度比例依赖场景，零深度代表无效测量。多台相机时用 `--serial 序列号` 指定设备。
分辨率可用 `--width`、`--height`、`--fps` 修改，但必须是设备支持的组合；可用 `rs-enumerate-devices` 查看。

SDK 2.58.4 官方发布说明列出的 D435 固件版本是 **5.17.3.10 或更新**。
先核对实机固件；本次安装没有刷写相机固件。

## 录制一段 episode

```bash
python scripts/camera.py record --output recordings/episode_0001 --seconds 60
```

`--seconds 0` 表示持续录制，按 Ctrl+C 正常结束。输出目录必须不存在，防止覆盖已有数据。
录制中不要直接断开 USB 或强制结束进程，否则文件可能无法完整关闭。

| 文件 | 内容 |
| --- | --- |
| `recording.db3` | SDK 原生录制，保留原始彩色与深度流、相关元数据，可在 Viewer 中加载 |
| `calibration.json` | 原生深度/彩色内参、畸变参数、深度到彩色外参、深度比例、设备信息和启动时参数 |
| `timestamps.csv` | Python 收到的 RGB-D 帧对的相机时间戳、时间域、帧号、主机接收时间 |
| `summary.json` | 结束状态、观察到的帧对数量、帧号缺口和持续时间 |

本版本录制扩展名为 `.db3`，旧版教程中的 `.bag` 不适用于新录制。SDK 仍支持旧文件读取/转换。
SDK 录制在后台独立进行，CSV 记录的是应用收到的帧对；二者帧数不保证相同。
采集完成后应回放检查实际帧数、时长、深度质量和丢帧情况，再用于训练。
640×480、30 FPS、RGB8+Z16 未压缩图像量约 **46 MB/s，即 2.8 GB/min**，实际录制体积随压缩与元数据变化。

## RGB-D 与点云快照

```bash
python scripts/camera.py snapshot --output recordings/snapshot_0001
```

预热 30 帧后保存：

- `color.png`：彩色图像。
- `depth_raw.png`：原生深度相机坐标系下的 16 位无损深度图。
- `depth_aligned_to_color.png`：映射到彩色像素网格的 16 位深度图，适合关联 RGB 像素。
- `points.ply`：带颜色的点云，坐标属于原生深度相机光学坐标系。
- `calibration.json`：标定信息、深度比例、对齐后的内参和此次快照时间戳。

读取深度必须保留位深：

```python
import cv2
import json

with open("recordings/snapshot_0001/calibration.json") as f:
    calibration = json.load(f)
depth = cv2.imread("recordings/snapshot_0001/depth_raw.png", cv2.IMREAD_UNCHANGED)
depth_meters = depth.astype("float32") * calibration["depth_scale_meters"]
valid = depth > 0
```

不能将伪彩色深度预览当作真实深度。不要假定每台相机固定为每单位 1 毫米，按保存的比例换算。
光学坐标约定为 X 向右、Y 向下、Z 向前，单位米。外参旋转列表以列优先存储，NumPy 恢复矩阵时使用 `reshape(3, 3, order="F")`。

## 与机器人数据结合

主机 Unix/单调时间戳是接收时刻，不是精确曝光时刻；相机硬件时钟与机器人时钟不能直接等同。
做动作与图像配对前需确定共用时间基准，测量传输延迟并检查漂移。
深度与彩色帧对由 SDK 匹配，不能据此假定两路曝光绝对同时发生。
多相机或精确控制实验还需要独立设计同步与带宽配置。

D435 没有 D435i 的 IMU，也不会自行输出相机全局位姿。
相机到机械臂末端/基座的变换需进行实际手眼或外参标定；安装 OpenCV 仅提供算法，不能代替采集标定数据。
保存训练数据时保留原始深度，并记录后续滤波、补洞和对齐操作，避免不可逆地丢失测量信息。

## 软件自检与重建

```bash
source /home/wtc/realsense_src/env.sh
python -m pip check
python /home/wtc/realsense_src/scripts/self_test.py
```

自检使用合成 RGB-D 数据验证 `.db3` 录制/回放、像素与时间戳、16 位 PNG、HDF5、PLY 点云和 FFmpeg。
它不替代 D435 的硬件连接、真实画面或长时间采集测试。

源码在 `librealsense/`，构建在 `build/`，SDK 安装在 `sdk/`，Python 环境在 `.venv/`。
`requirements.txt` 是主要依赖；`requirements.lock.txt` 记录实际安装的完整版本。
重新构建使用：

```bash
cmake -S librealsense -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/home/wtc/realsense_src/sdk \
  -DCMAKE_INSTALL_RPATH=/home/wtc/realsense_src/sdk/lib \
  -DFORCE_RSUSB_BACKEND=ON -DBUILD_WITH_DDS=OFF \
  -DBUILD_GRAPHICAL_EXAMPLES=ON -DBUILD_EXAMPLES=ON -DBUILD_TOOLS=ON
cmake --build build --parallel 6
cmake --install build
```

官方安装器可能提示无法更新系统 `ldconfig` 缓存；本地工具已嵌入 SDK 库路径，并由 `env.sh` 提供库路径，无需为此以 root 重新安装。

参考：[官方 SDK 2.58.4 发布说明](https://github.com/realsenseai/librealsense/releases/tag/v2.58.4)、[官方 Python 示例](https://github.com/realsenseai/librealsense/tree/v2.58.4/wrappers/python/examples)。
