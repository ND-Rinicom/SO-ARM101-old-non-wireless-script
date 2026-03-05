# Camera Streaming Usage

The `mqtt_lerobot_teleoperate.py` script now supports **ustreamer**-based camera streaming as an alternative to the problematic GStreamer RTSP approach.

## Installation

Install ustreamer on your device:
```bash
sudo dnf install -y ustreamer
```

## Usage

Enable camera streaming when running the teleoperation script:

```bash
python lerobot/src/lerobot/scripts/mqtt_lerobot_teleoperate.py \
    --robot.type=so101_follower \
    --robot.port=/dev/ttyACM1 \
    --robot.id=follower_arm \
    --teleop.type=so101_leader \
    --teleop.port=/dev/ttyACM0 \
    --teleop.id=leader_arm \
    --camera_enable=true \
    --camera_device=/dev/video4 \
    --camera_resolution=640x480 \
    --camera_fps=15 \
    --camera_jpeg_quality=50 \
    --camera_buffers=1 \
    --camera_host=10.185.80.233 
```

## Parameters

- `--camera-enable`: Enable ustreamer camera streaming (default: `false`)
- `--camera-device`: V4L2 device path, e.g., `/dev/video0` (default: `None`)
- `--camera-host`: Host to bind ustreamer to (default: `0.0.0.0`)
- `--camera-port`: Port for ustreamer HTTP stream (default: `8080`)
- `--camera-resolution`: Resolution string, e.g., `640x480` (default: `640x480`)

## Accessing the Stream

Once running, access the camera stream via:
```
http://<device-ip>:8080/stream
```

For example, if the device IP is `192.168.1.100`:
```
http://192.168.1.100:8080/stream
```

## Advantages over GStreamer RTSP

- ✅ Simpler setup (no `x264enc` codec issues)
- ✅ Lower latency
- ✅ Works well on Raspberry Pi
- ✅ Better compatibility with browsers and standard HTTP clients
- ✅ Graceful shutdown on KeyboardInterrupt

## Notes

- Make sure your video device is accessible. Check with: `ls -la /dev/video*`
- The camera stream runs as a background subprocess and is automatically cleaned up on exit
- If ustreamer fails to start, an error will be logged but teleoperation will continue
