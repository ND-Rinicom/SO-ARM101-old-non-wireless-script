# SO-ARM101-old-non-wireless-script
Storing this just incase I ever need to use the lerobot git again

# Set up
Follow https://huggingface.co/docs/lerobot/en/installation to get set up.

When cloned lerobot git repository move mqtt_lerobot_teleoperate.py into src/lerobot/scripts.

Then calibrate arms using the so-11 guide https://huggingface.co/docs/lerobot/en/so101#calibrate.

And then start using teleoperation by using https://huggingface.co/docs/lerobot/en/il_robots but instead of 

```
lerobot-teleoperate \
    --robot.type=so101_follower \
    --robot.port=/dev/tty.usbmodem58760431541 \
    --robot.id=my_awesome_follower_arm \
    --teleop.type=so101_leader \
    --teleop.port=/dev/tty.usbmodem58760431551 \
    --teleop.id=my_awesome_leader_arm
```

For digital twin MQTT teleopratoin use 

```
python src/lerobot/scripts/mqtt_lerobot_teleoperate.py \
    --robot.type=so101_follower \
    --robot.port=/dev/tty.usbmodem58760431541 \
    --robot.id=my_awesome_follower_arm \
    --teleop.type=so101_leader \
    --teleop.port=/dev/tty.usbmodem58760431551 \
    --teleop.id=my_awesome_leader_arm \
    # Optional MQTT configuration (these are the defaults):
    --mqtt_enable=true \
    --mqtt_host=192.168.1.107 \
    --mqtt_port=1883 \
    --mqtt_topic=watchman_robotarm/so-101 \
    --mqtt_units=degrees \
    --capture_zero_offsets_at_start=true \
```
