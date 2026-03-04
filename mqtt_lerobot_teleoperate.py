# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Teleoperation script with MQTT publishing of follower joint commands.

Adds:
- MQTT publish to a fixed broker/topic
- Mapping from LeRobot action keys -> your frontend JSON schema

Install dependency:
    pip install paho-mqtt
"""

import json
import logging
import queue
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pprint import pformat
from typing import Any

import rerun as rr

# ---- MQTT ----
# pip install paho-mqtt
try:
    import paho.mqtt.client as mqtt  # type: ignore
except Exception as e:  # pragma: no cover
    mqtt = None  # type: ignore
    _mqtt_import_error = e
else:
    _mqtt_import_error = None

MQTT_HOST = "0.0.0.0"
MQTT_PORT = 1883
MQTT_TOPIC = "watchman_robotarm/so-101/leader"

# ---- LeRobot ----
from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig  # noqa: F401
from lerobot.cameras.realsense.configuration_realsense import RealSenseCameraConfig  # noqa: F401
from lerobot.configs import parser
from lerobot.processor import (
    RobotAction,
    RobotObservation,
    RobotProcessorPipeline,
    make_default_processors,
)
from lerobot.robots import (  # noqa: F401
    Robot,
    RobotConfig,
    bi_openarm_follower,
    bi_so_follower,
    earthrover_mini_plus,
    hope_jr,
    koch_follower,
    make_robot_from_config,
    omx_follower,
    openarm_follower,
    reachy2,
    so_follower,
    unitree_g1 as unitree_g1_robot,
)
from lerobot.teleoperators import (  # noqa: F401
    Teleoperator,
    TeleoperatorConfig,
    bi_openarm_leader,
    bi_so_leader,
    gamepad,
    homunculus,
    keyboard,
    koch_leader,
    make_teleoperator_from_config,
    omx_leader,
    openarm_leader,
    reachy2_teleoperator,
    so_leader,
    unitree_g1,
)
from lerobot.utils.import_utils import register_third_party_plugins
from lerobot.utils.robot_utils import precise_sleep
from lerobot.utils.utils import init_logging, move_cursor_up
from lerobot.utils.visualization_utils import init_rerun, log_rerun_data


# --------------------------
# Helpers: timestamps + mapping
# --------------------------
def iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def action_to_frontend_payload(robot_action_to_send: dict[str, float], units: str) -> dict[str, Any]:
    """Convert LeRobot action dict into your frontend JSON schema."""
    joints: dict[str, dict[str, float]] = {}
    for lerobot_joint, value in robot_action_to_send.items():
        # Strip .pos suffix to get frontend joint name
        frontend_joint = lerobot_joint.replace(".pos", "")
        joints[frontend_joint] = value

    return {
        "method": "set_follower_joint_angles",
        "timestamp": iso_utc_now(),
        "params": {
            "units": units,  # "degrees" or "radians"
            "mode": "follower",
            "joints": joints,
        },
    }


# --------------------------
# MQTT publisher (non-blocking)
# --------------------------
class MQTTPublisher:
    """
    Non-blocking-ish publisher: teleop thread enqueues, background thread publishes.

    If the queue fills, messages are dropped to keep the control loop stable.
    """

    def __init__(
        self,
        host: str,
        port: int,
        topic: str,
        *,
        queue_size: int = 5,
        qos: int = 0,
        retain: bool = False,
    ):
        if mqtt is None:  # pragma: no cover
            raise RuntimeError(
                f"paho-mqtt not available. Install with `pip install paho-mqtt`. Import error: {_mqtt_import_error}"
            )

        self.host = host
        self.port = port
        self.topic = topic
        self.qos = qos
        self.retain = retain

        self._queue: queue.Queue[str] = queue.Queue(maxsize=queue_size)
        self._client = mqtt.Client()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

        def _on_connect(client, userdata, flags, rc, properties=None):  # type: ignore
            logging.info(f"MQTT connected to {self.host}:{self.port} rc={rc}")

        def _on_disconnect(client, userdata, rc, properties=None):  # type: ignore
            logging.warning(f"MQTT disconnected rc={rc}")

        self._client.on_connect = _on_connect
        self._client.on_disconnect = _on_disconnect

    def start(self) -> None:
        self._client.connect(self.host, self.port, keepalive=30)
        self._client.loop_start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._client.loop_stop()
        except Exception:
            pass
        try:
            self._client.disconnect()
        except Exception:
            pass

    def publish_json(self, payload: dict[str, Any]) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass  # drop if behind

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._client.publish(self.topic, msg, qos=self.qos, retain=self.retain)
            except Exception as e:
                logging.warning(f"MQTT publish failed: {e}")


@dataclass
class TeleoperateConfig:
    teleop: TeleoperatorConfig
    robot: RobotConfig
    fps: int = 60
    teleop_time_s: float | None = None

    # Display all cameras on screen
    display_data: bool = False
    display_ip: str | None = None
    display_port: int | None = None
    display_compressed_images: bool = False

    # MQTT publish settings
    mqtt_enable: bool = True
    mqtt_host: str = MQTT_HOST
    mqtt_port: int = MQTT_PORT
    mqtt_topic: str = MQTT_TOPIC

    # Units label in outgoing JSON. IMPORTANT: set this to match your actual values.
    # If you run with --robot.use_degrees=true --teleop.use_degrees=true, set this to "degrees".
    mqtt_units: str = "degrees"


def teleop_loop(
    teleop: Teleoperator,
    robot: Robot,
    fps: int,
    teleop_action_processor: RobotProcessorPipeline[tuple[RobotAction, RobotObservation], RobotAction],
    robot_action_processor: RobotProcessorPipeline[tuple[RobotAction, RobotObservation], RobotAction],
    robot_observation_processor: RobotProcessorPipeline[RobotObservation, RobotObservation],
    display_data: bool = False,
    duration: float | None = None,
    display_compressed_images: bool = False,
    mqtt_pub: MQTTPublisher | None = None,
    mqtt_units: str = "degrees",
):
    display_len = max(len(key) for key in robot.action_features)
    start = time.perf_counter()

    while True:
        loop_start = time.perf_counter()

        # Observation (used for visualization + processors)
        obs = robot.get_observation()

        # Raw leader action
        raw_action = teleop.get_action()

        # Process leader action
        teleop_action = teleop_action_processor((raw_action, obs))

        # Produce action to send to follower
        robot_action_to_send = robot_action_processor((teleop_action, obs))

        # Send to robot
        _ = robot.send_action(robot_action_to_send)

        # Publish to MQTT (non-blocking)
        if mqtt_pub is not None:
            action_for_frontend = (
                {k: float(v) for k, v in robot_action_to_send.items()}
            )
            payload = action_to_frontend_payload(action_for_frontend, units=mqtt_units)
            mqtt_pub.publish_json(payload)

        # Display (optional)
        if display_data:
            obs_transition = robot_observation_processor(obs)
            log_rerun_data(
                observation=obs_transition,
                action=teleop_action,
                compress_images=display_compressed_images,
            )

            print("\n" + "-" * (display_len + 10))
            print(f"{'NAME':<{display_len}} | {'NORM':>7}")
            for motor, value in robot_action_to_send.items():
                print(f"{motor:<{display_len}} | {value:>7.2f}")
            move_cursor_up(len(robot_action_to_send) + 3)

        # Rate control
        dt_s = time.perf_counter() - loop_start
        precise_sleep(max(1 / fps - dt_s, 0.0))
        loop_s = time.perf_counter() - loop_start
        print(f"Teleop loop time: {loop_s * 1e3:.2f}ms ({1 / loop_s:.0f} Hz)")
        move_cursor_up(1)

        if duration is not None and time.perf_counter() - start >= duration:
            return


@parser.wrap()
def teleoperate(cfg: TeleoperateConfig):
    init_logging()
    logging.info(pformat(asdict(cfg)))

    if cfg.display_data:
        init_rerun(session_name="teleoperation", ip=cfg.display_ip, port=cfg.display_port)

    display_compressed_images = (
        True
        if (cfg.display_data and cfg.display_ip is not None and cfg.display_port is not None)
        else cfg.display_compressed_images
    )

    mqtt_pub: MQTTPublisher | None = None
    if cfg.mqtt_enable:
        mqtt_pub = MQTTPublisher(cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_topic)
        mqtt_pub.start()
        logging.info(f"MQTT publishing enabled: topic='{cfg.mqtt_topic}' broker={cfg.mqtt_host}:{cfg.mqtt_port}")

    teleop = make_teleoperator_from_config(cfg.teleop)
    robot = make_robot_from_config(cfg.robot)
    teleop_action_processor, robot_action_processor, robot_observation_processor = make_default_processors()

    teleop.connect()
    robot.connect()

    try:
        teleop_loop(
            teleop=teleop,
            robot=robot,
            fps=cfg.fps,
            display_data=cfg.display_data,
            duration=cfg.teleop_time_s,
            teleop_action_processor=teleop_action_processor,
            robot_action_processor=robot_action_processor,
            robot_observation_processor=robot_observation_processor,
            display_compressed_images=display_compressed_images,
            mqtt_pub=mqtt_pub,
            mqtt_units=cfg.mqtt_units,
        )
    except KeyboardInterrupt:
        pass
    finally:
        if cfg.display_data:
            rr.rerun_shutdown()
        try:
            teleop.disconnect()
        finally:
            robot.disconnect()
        if mqtt_pub is not None:
            mqtt_pub.stop()


def main():
    register_third_party_plugins()
    teleoperate()


if __name__ == "__main__":
    main()
