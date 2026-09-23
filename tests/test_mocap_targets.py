import meshcat.transformations as tf
import mujoco
import numpy as np
import pytest

from piper_xr.config import build_dual_piper_config, build_piper_config
from piper_xr.paths import (
    PIPER_DUAL_SCENE_XML, PIPER_DUAL_URDF, PIPER_SCENE_XML, PIPER_URDF,
)
from piper_xr.simulation.piper_mujoco_controller import LoggingMujocoTeleopController


@pytest.mark.parametrize("dual", [False, True])
@pytest.mark.parametrize("mode", ["position", "pose"])
def test_mocap_targets(dual, mode):
    config = (build_dual_piper_config if dual else build_piper_config)(control_mode=mode)
    controller = LoggingMujocoTeleopController(
        xml_path=PIPER_DUAL_SCENE_XML if dual else PIPER_SCENE_XML,
        robot_urdf_path=PIPER_DUAL_URDF if dual else PIPER_URDF,
        manipulator_config=config,
        visualize_placo=False,
    )
    for _ in range(3):
        controller._update_robot_state()
        controller._update_ik()
        controller._update_gripper_target()
        controller._update_mocap_target()
        controller._send_command()
        mujoco.mj_step(controller.mj_model, controller.mj_data)

    for name, task in controller.effector_task.items():
        index = controller.target_mocap_idx[name]
        assert index >= 0
        position = np.array([0.31, 0.12, 0.42])
        quaternion = tf.quaternion_from_euler(0.2, -0.3, 0.4)
        if mode == "position":
            task.target_world = position
            controller.mj_data.mocap_quat[index] = quaternion
        else:
            target = tf.quaternion_matrix(quaternion)
            target[:3, 3] = position
            task.T_world_frame = target
        controller._update_mocap_target()
        np.testing.assert_allclose(controller.mj_data.mocap_pos[index], position)
        np.testing.assert_allclose(controller.mj_data.mocap_quat[index], quaternion)

    assert np.all(np.isfinite(controller.mj_data.qpos))
    assert np.all(np.isfinite(controller.mj_data.ctrl))
