# Collection validation — 2026-09-16

Scope: single PiPER, one D435, independent XRoboToolkit/PiperXR, RealSense and EVA environments. No physical robot motion was executed during implementation.

Passed:

- Imports in the existing Python 3.10 Piper runtime and RealSense environment; EVA single-arm config parsing in Python 3.11.
- Fifteen collection/diagnostic tests: the original five for array IPC, motion leases, timestamp matching, RGB/depth correspondence and recording state; two for subprocess error persistence; eight for feedback units, stationary feedback, missing/stale/future/invalid timestamps, and reading timestamps before a shared SDK object can change during the wall-clock sample.
- Installed PiPER SDK startup regression: reproduced the reported `can_auto_init=False` / `__arm_can is None` error with only CAN I/O and XR mocked. The corrected adapter calls `CreateCanBus` before `ConnectPort`, validates the configured SocketCAN interface at 1 Mbps, and does not enable motors or send motion/gripper targets at connection. The real SDK lifecycle now passes this isolated check. The older whole-SDK mock did not enforce bus creation; it now does.
- Mock CAN adapter validation: no enable on connect, installed SDK's `grippers_angle` feedback field, gripper widths in meters, enable code `0x01`, quantized/limited sent joint targets, stale-feedback rejection.
- Installed SDK parser validation with synthetic CAN frames `0x2A5` through `0x2A8`: radians/meters conversion and Unix-second timestamp freshness. Passive diagnostic CLI validated with a receive-only synthetic bus and actual SDK decoder, including JSON output, no SDK CAN connection, empty reception, missing gripper and partial joint groups. No physical CAN frames were sent or received in these checks.
- Optional firmware query diagnostic (`diagnose_collection_can.sh --query-firmware`): emitted request checked against installed SDK `SearchPiperFirmwareVersion` (`0x4AF`, `01 00 00 00 00 00 00 00`). Synthetic tests cover a maximum of three read requests, actual SDK response decoding, no-response and send-error cases, and exclusion of local TX echoes. This option sends no enable, mode, position or parameter-write command. After restoring UP / 1 Mbps, the user received 33 firmware response frames to three requests, decoding to `S-V1.8-8`; the controller query path is now verified locally.
- Interface preflight regression: DOWN/STOPPED, wrong bitrate, BUS-OFF and unavailable interface status are rejected before opening CAN; a synthetic DOWN CLI run saves a report and recovery commands without constructing the SDK or sending queries. Normal UP/1 Mbps status proceeds. Static Python and shell checks passed.
- Eleven pre-existing simulation and real-controller test functions invoked directly in the Piper runtime (that environment has no pytest package).
- Actual EVA offline capture → FK → LeRobot Parquet/metadata → RGB MP4 → RGB-D HDF5: save/cancel/save produced two green episodes with 36 and 35 frames. Seven-dimensional qpos/action and eight-dimensional EEF values were finite. Video/data/depth counts agreed; uint16 depth and capture times were preserved. Artificially skipped images did not reappear in depth associations.
- RealSense/Open3D point-cloud exporter: 719 colored points saved to PLY; RGB and original/aligned uint16 PNGs saved from a synthetic recorded frame.
- Initial mock process startup reached the EVA web console with a single-arm model and ZMQ stream. The service was then stopped.
- Python compilation, shell syntax, Ruff undefined-name/unused-import checks.

Artifacts from offline validation:

- `/tmp/piperxr_collection_verified/validation.json`
- `/tmp/piperxr_collection_verified/pick_place/raw/`
- `/tmp/piperxr_pointcloud_verified/`

Not yet verified:

- Full localhost UI/control-channel save/cancel/save test. Repeated automatic approval service HTTP 503 errors blocked the final run, including after the user explicitly authorized it; the platform reported no available channel for its approval model. Use `scripts/test_collection_network.py` to run the bounded, mock-only test locally. The previously launched mock service was stopped successfully.
- End-to-end real D435 capture, actual CAN/PICO motion, physical gripper calibration and sustained storage throughput. Firmware queries received `S-V1.8-8` replies, but measured feedback remained absent. The subsequent 2026-09-17 passive report after a controller power cycle contains control targets instead of state feedback; see the update below. Direct agent CAN inspection remains restricted by sandbox/approval infrastructure; hardware observations come from the user's local reports.
- Camera-to-robot extrinsic calibration. Point clouds remain in the native depth camera optical frame.

The installed EVA/imageio/JAX pipeline emitted an `os.fork()`/JAX multithreading warning during MP4 encoding; the tested conversions completed. Long-duration hardware collection still needs its own validation.

The user repeated passive reception with zero new RX and confirmed prior successful control through PiperXR on `can0`. Source comparison confirms the original controller enables motors and sets CAN joint mode immediately after connection, while collection waits for feedback and an operator motion gate. Both connection paths invoke the SDK initialization queries. The available evidence does not establish that enabling is required to obtain feedback; firmware query diagnosis was added to distinguish controller responses from missing periodic feedback without changing motion authorization.

An intermediate firmware-query report had a newly numbered interface (10 instead of 9), counters reset, DOWN/STOPPED and no bitrate; all sends failed with `Network is down [Error Code 100]`. The user has since restored UP / 1 Mbps and obtained firmware replies. The latest report at `work_dirs/collection/can_firmware_diagnostic.json` records RX 0 → 33 and TX 0 → 3 with no increased error counters. All 33 frames are `0x4AF`, with `S-V1.8-8` parsed; no periodic or shifted state IDs appeared. No host CAN configuration or hardware motion was performed by the agent.

The diagnostic now explicitly distinguishes firmware-only responses from missing controller communication. Local SDK inspection found a `0x422` silent-mode enum but no documented payload or callable implementation; `data_feedback_0x48x` applies to speed/acceleration frames, not joint position or gripper feedback. No speculative parameter writes were added. Online official protocol retrieval was blocked by automatic approval review HTTP 503, so no undocumented recovery command was inferred.

Startup diagnostics now retain bridge/camera/robot stdout and stderr in `<output_dir>/logs/bridge_<timestamp>_<pid>.log`, continue showing output in the terminal, and include exit code, log path and a bounded log tail in startup/runtime bridge errors. No real hardware was driven to validate these changes.

## 2026-09-17: Control Frames Without State Feedback

The user's 10-second passive capture received 29 frames at `0x151`, `0x155`-`0x157`, `0x159`, `0x190`, and `0x471`. Interface RX rose from 143 to 172 and TX remained zero. The user confirmed a single PiPER directly connected to USB-CAN, with no other controller and no visible connector label. The installed SDK describes these joint/gripper targets as master-to-follower commands. A teaching-input/master role is now a leading hypothesis, not a confirmed state; no measured or shifted state frames appeared. The unknown `0x190` payload has not been interpreted.

`scripts/restore_collection_can.sh` previews by default. With `--apply`, it checks UP / 1 Mbps and queues exactly one documented role configuration (`0x470`, `FC 00 00 00 00 00 00 00`), equivalent to SDK `MasterSlaveConfig(0xFC, 0, 0, 0)`. It selects motion-output role and default feedback/control/linkage offsets. It does not call SDK ConnectPort, enable/disable motors, send motion/gripper targets, zero joints or factory-reset parameters. The SDK's follower setup example requires a controller reboot when switching from master role. Role configuration changes device behavior and must be done with the stationary arm supported and other controllers stopped.

The script reserves a new report before CAN access, makes no automatic retry, closes its socket, and explicitly leaves `configuration_verified=false` even after queueing. Actual device acceptance and fresh `0x2A5`-`0x2A8` after reboot are still unverified. Software validation uses only simulated CAN and compares the request against the installed SDK's real encoder; it covers preview without CAN access, a single queued frame, DOWN preflight rejection, send failure, cleanup and refusing to overwrite a report. No physical configuration was sent by the agent.
