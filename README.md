# Wrist Curl Rehab Monitor

Real-time monitor for the seated wrist curl. It counts a rep only when the wrist reaches the target angle **and** the wrist flexors are active, with no compensation or posture fault. Every rejected rep gets a plain-language reason with an icon, glow and beep. A fatigue trend is tracked and a rest is forced when it reaches red.

> **Exercise guidance only, not a diagnosis.** The app shows this disclaimer on its first screen.

```
firmware/   ESP32-WROOM-32 firmware (ESP-IDF >= 5.3): BNO055 + sEMG ADC + buzzer + OLED -> Wi-Fi (or USB)
host/       Python pipeline, PyQt6 GUI, config.yaml, simulator, replay and training tools
tests/      pytest suite: filters, MDF, every fault code, replay determinism, latency, GUI FPS
```

## Quick start (no hardware needed)

```bash
cd host
python -m pip install -r requirements.txt   # Python 3.11+ ("python -m" works even if pip is not on PATH)
python run_gui.py --sim --windowed       # virtual patient; buttons on the right perform each fault
python simulate.py --scenario acceptance # scripted session -> sessions/sim/<stamp>/
python replay.py sessions/sim/<stamp>    # re-runs the pipeline, prints the rep table
cd .. && python -m pytest -q tests       # ~3 min (includes end-to-end sessions)
```

With hardware: connect the PC to the ESP32's Wi-Fi network **WristMonitor** (with the password you set in `firmware/sdkconfig.secrets`), then run `python run_gui.py`. The app finds the ESP32 by itself; `python run_gui.py --wifi 192.168.4.1` sets the address explicitly, and `python run_gui.py --port COM5` uses USB serial instead (see section 1). **Spacebar** (or Esc) is the emergency stop on every screen. F11 toggles full screen.

## 1. Hardware and wiring

Board: **ESP32-WROOM-32** (classic ESP32, 4 MB flash, no PSRAM), on a DevKit with a CP2102/CH340 USB bridge. All GPIOs are the firmware defaults. Change them in `idf.py menuconfig` → *Wrist Curl Monitor*.

### Pinout summary (ESP32 side)

| ESP32 pin | Direction | Connects to | Function | Kconfig option |
|---|---|---|---|---|
| GPIO34 (ADC1_CH6) | In (analog) | sEMG module SIG/OUT | sEMG sampling, 1 kHz | `WM_EMG_ADC_CHANNEL` = 6 |
| GPIO26 (I2C1 SDA) | In/Out | BNO055 **SDA** (hand, and forearm if fitted) | IMU data, 100 kHz, 50 Hz samples | `WM_IMU_SDA` |
| GPIO27 (I2C1 SCL) | Out | BNO055 **SCL** | IMU clock | `WM_IMU_SCL` |
| GPIO25 | Out | Passive buzzer **+** (or S/I/O on a module) | Beeps: a 2.5 kHz PWM tone | `WM_BUZZER_GPIO`, `WM_BUZZER_PASSIVE = y` |
| GPIO21 (I2C SDA) | In/Out | OLED **SDA** | Status display, 400 kHz | `WM_OLED_SDA` |
| GPIO22 (I2C SCL) | Out | OLED **SCL** | Status display clock | `WM_OLED_SCL` |
| GPIO35 (ADC1_CH7) | In (analog) | 9 V battery divider midpoint (220k/100k) | Battery voltage | `WM_BATTERY_ADC_CHANNEL` = 7, `WM_BATTERY_DIVIDER_X100` = 320 |
| GPIO1 / GPIO3 (UART0) | - | On-board USB-UART bridge | Flashing the firmware; data link only if USB is chosen instead of Wi-Fi | `WM_LINK`, `WM_HOST_BAUD` |
| (antenna) | - | Built-in Wi-Fi | Data link to the PC (default): TCP 3333, beacon UDP 3334 | `WM_LINK`, `WM_WIFI_*` |
| 3V3 | Power **in** | Buck converter OUT+ (3.3 V from the 9 V battery), and from there BNO055, buzzer, OLED and sEMG VCC | Main 3.3 V rail for everything | - |
| 5V / VIN | - | Not used on battery (it only carries 5 V while USB is plugged in) | - | - |
| GND | - | Every module's GND | Common ground | - |

Do **not** use GPIO6–11 (the WROOM-32's internal flash) or the strapping pins GPIO0, 2, 12 and 15 for the peripherals. GPIO34 and GPIO35 are input-only, which is fine for the ADC. GPIO26/27 are free on both the WROOM-32 and WROVER modules. The OLED (GPIO21/22, I2C0) and the BNO055 (GPIO26/27, I2C1) are on separate buses, so a slow BNO055 transfer never delays the display.

### Per-device wiring

**Bosch BNO055 breakout, hand IMU (back of the hand), I2C address 0x29**

| BNO055 pin | → | ESP32 | Note |
|---|---|---|---|
| VIN / VCC | → | 3V3 rail | Breakouts accept 3.3–5 V |
| GND | → | GND | |
| SDA | → | GPIO26 | |
| SCL | → | GPIO27 | |
| ADR / ADD | → | 3V3 | **High = 0x29** (the firmware default `WM_IMU_ADDR`). Left open or low it is 0x28 |
| PS0 / PS1 | → | GND / open | I2C mode (the breakout default). Do not set them for UART |
| RST / INT | - | not connected | |

* The firmware runs the chip in **IMU fusion mode** (accelerometer + gyro, no magnetometer), so an iron dumbbell does not make the heading jump. `WM_IMU_FUSION_NDOF` adds the magnetometer.
* It reads orientation (quaternion), gyro and acceleration every 20 ms (`WM_IMU_PERIOD_MS`), converts them to the same units the protocol used before, and sends them to the PC. `imu.rate_hz` in `config.yaml` must match (50 Hz).
* The BNO055 calibrates itself while it runs. Move the hand slowly through a few positions after power-on until the OLED shows G3 (gyro) and A3 (accelerometer).
* The BNO055 stretches the I2C clock; 100 kHz (`WM_IMU_I2C_HZ`) is the most reliable speed. Most breakouts have their own pull-ups.

**Second BNO055 on the forearm (optional, needs `WM_IMU2_ENABLE` and `imu.forearm_imu: true`)**

Wire it to the **same** SDA/SCL/3V3/GND, with **ADR to GND (0x28)** so the two addresses differ (`WM_IMU2_ADDR`).

**Schiray dry-electrode sEMG module (1 channel)**

| Module pin | → | ESP32 / body | Note |
|---|---|---|---|
| VCC (+) | → | 3V3 rail | **Confirm in the datasheet that it runs on 3.3 V**; if not, see the Power section |
| GND (−) | → | GND | |
| SIG / OUT | → | GPIO34 | **Must stay within 0–3.3 V.** If the output can exceed 3.3 V, add a divider (for example 10 kΩ over 20 kΩ to GND) |
| Electrode + / − | → | Anterior forearm, about 1/3 of the way from elbow to wrist | Over flexor carpi radialis / flexor digitorum superficialis, in line with the muscle |
| Reference electrode | → | Elbow bone | Bony site, away from the muscle |

**Passive buzzer (`WM_BUZZER_PASSIVE = y`, the default)**

| Buzzer pin | → | ESP32 | Note |
|---|---|---|---|
| + (or S / I/O on a module) | → | GPIO25 | The firmware drives a 2.5 kHz square wave (`WM_BUZZER_FREQ_HZ`) |
| − (or GND) | → | GND | |
| VCC (modules only) | → | 3V3 | |

* A passive buzzer only clicks on DC, so it needs the tone. Try 2000–4000 Hz in menuconfig to find its loudest pitch.
* `WM_BUZZER_ACTIVE_HIGH = y` (the default with a passive buzzer) keeps the pin low while silent. For a low-level-trigger module, set it to `n`; the pin then idles high.
* **Active buzzer instead** (beeps by itself on DC, for example the FC-07): set `WM_BUZZER_PASSIVE = n`. For the FC-07 also set `WM_BUZZER_ACTIVE_HIGH = n` (low = beep) and power it from 3V3.
* **Standalone electrode test (off by default):** `sdkconfig.defaults` sets `WM_EMG_BEEP_THRESHOLD = 0`, so the buzzer is silent while idle. Set it to e.g. 150 and, while the PC app is not streaming, the ESP32 beeps once each time the EMG RMS rises above it. The threshold is the tick mark on the OLED bar. During a session only the app controls the buzzer.

**0.96" OLED display module, 128×64, I2C (SSD1306)**

| OLED pin | → | ESP32 | Note |
|---|---|---|---|
| VCC | → | 3V3 | Most modules accept 3.3–5 V; 3.3 V keeps the I2C lines at 3.3 V |
| GND | → | GND | |
| SCL | → | GPIO22 | |
| SDA | → | GPIO21 | |

* I2C address `0x3C` (the usual default). If the board's address jumper or resistor is set to `0x3D` (printed as 0x7A on some boards), change `WM_OLED_ADDR`.
* The module has its own pull-up resistors; the ESP32's internal pull-ups are also enabled.
* The firmware expects an **SSD1306** controller, which almost all 0.96" boards use. A 1.3" board is usually an SH1106 and needs a different driver.
* If no display is connected, the firmware simply runs without it.

What the OLED shows (refreshed 10 times per second; these are raw sensor values, not the host's calibrated wrist angle):

```
BAT 9.1V      PC:ON      <- battery; PC:ON once the app is connected and streaming
Roll    177.0  S0G3      <- BNO055 angles, degrees; S/G/A/M = calibration (0-3) of
Pitch    -3.2  A3M0         system, gyro, accelerometer, magnetometer (M stays 0 in IMU mode)
Yaw      40.1               (forearm IMU pitch appears as "F" on the Yaw row)
Gyr    1  -30    0       <- gyro x y z, deg/s
EMG raw 2048             <- last ADC sample, 0-4095
EMG rms  123             <- AC RMS over ~100 ms, ADC counts
[########   |      ]    <- bar: RMS / WM_OLED_EMG_FULL_SCALE (default 400); | = beep threshold
                            (shows "IP 192.168.4.1" instead until the PC app connects over Wi-Fi)
```

"IMU: no data / check BNO055 wiring" means no BNO055 sample has arrived for 200 ms; check SDA/SCL (GPIO26/27), 3V3, and that ADR is high (address 0x29). Use the display while placing the electrodes: the EMG RMS should be small and steady with the hand relaxed, and should jump clearly when the wrist flexes against the weight.

**Power: 9 V battery → buck converter (3.3 V) → ESP32 3V3 pin**

| From | → | To | Note |
|---|---|---|---|
| 9 V battery (+) | → | Power switch | Optional but recommended |
| Switch | → | 1N5819 Schottky diode (anode → cathode) | Optional reverse-polarity protection (9 V snaps are easy to touch on backwards); costs about 0.3 V on the 9 V side, which is harmless |
| Diode cathode (or switch) | → | Buck converter **IN+** | |
| 9 V battery (−) | → | Buck converter **IN−** | |
| Buck **OUT+ (3.3 V)** | → | ESP32 **3V3** pin, plus VCC of the BNO055, OLED, buzzer and sEMG module | Feeds the ESP32 chip directly, bypassing the DevKit's own regulator |
| Buck **OUT−** | → | ESP32 **GND** | This is the common ground for everything |
| 10 µF + 100 nF capacitors | across Buck OUT+ / OUT−, close to the ESP32 | Smooths switching ripple, which otherwise shows up as noise on the EMG ADC |

* **Set the buck output to 3.3 V with a multimeter *before* connecting anything.** The 3V3 pin goes straight to the ESP32 chip with no regulator in between. The ESP32 tolerates 3.0–3.6 V; above 3.6 V it can be destroyed. Adjustable modules (LM2596, MP1584, and similar) often ship set to 5 V or more. A fixed 3.3 V module needs no adjustment.
* **Wi-Fi draws current in short bursts of 300–500 mA.** Use a buck rated for at least **800 mA**, and add a **470 µF electrolytic capacitor** across 3V3/GND close to the ESP32 (in addition to the 10 µF + 100 nF). An undersized converter, or a weak 9 V battery, causes random resets ("brown-out"), typically just as the PC connects.
* **Never** connect the 9 V to the 3V3 pin. Do not use the 5V/VIN pin in this setup.
* **Everything runs from the one 3.3 V rail.** The BNO055 breakout, OLED and buzzer all run on 3.3 V. **Check that the Schiray sEMG module runs on 3.3 V.** If it needs 5 V or more, give it its own supply (a second buck, or the battery side if its datasheet allows), keeping its output signal within 0–3.3 V at GPIO34.
* **With Wi-Fi, no cable to the PC is needed during sessions.** The device runs purely on the 9 V battery, so the patient has no electrical connection to the PC or the mains. That is safer, and it also removes mains hum picked up through the PC's ground. Plug USB in only to flash firmware. If you do plug it in while the buck is powering the board, the DevKit's own regulator also drives the 3V3 rail; two 3.3 V supplies in parallel normally works.

**Battery sense (enabled by default; read on the OLED and in the app's status bar)**

| Part | Connection |
|---|---|
| Battery (+), after the switch, before the buck | → 220 kΩ → **GPIO35** → 100 kΩ → GND (ratio 3.2, so a fresh 9.6 V reads about 3.0 V at the pin) |
| 100 nF capacitor | From GPIO35 to GND, to steady the reading |
| Kconfig | `WM_BATTERY_ADC_CHANNEL = 7`, `WM_BATTERY_DIVIDER_X100 = 320` (both now the defaults) |

* The reading uses the ESP32's factory ADC calibration and averages 16 samples, so expect about ±0.2 V accuracy. The divider draws about 28 µA.
* Below **7.0 V** the OLED shows `LOW` and the app's status bar shows "⚠ Battery LOW … Replace the 9 V battery" (`safety.battery_low_v` in `config.yaml`). A nearly flat 9 V battery sags hard under load. If the board then browns out, the app pauses the session ("Sensor disconnected") rather than miscounting.
* **If you do not fit the divider**, set `WM_BATTERY_ADC_CHANNEL = -1`. An unconnected pin reads random voltages.

**Battery life (estimate)**

| Load | Current |
|---|---|
| ESP32 with Wi-Fi on (power saving off for low latency) | about 110–130 mA average, 300–500 mA peaks |
| BNO055 (fusion running) | about 12 mA |
| OLED (text screen) | about 15–20 mA |
| sEMG module | about 10–30 mA (check the datasheet) |
| Buzzer | about 30 mA, only while beeping |
| **Total at 3.3 V** | **about 170–210 mA**, which is about 85 mA from the 9 V battery through an 85 %-efficient buck |

* A 9 V alkaline battery lasts roughly **3–4 hours** with Wi-Fi on. Alkaline 9 V cells also sag under the Wi-Fi current bursts, so a fresh battery matters.
* A rechargeable 9 V Li-ion pack (600 mAh or more) is strongly recommended: it lasts longer (about 6 hours) and holds its voltage under the bursts. NiMH 9 V (about 200 mAh) lasts only about 2 hours.

**Wi-Fi link to the PC (default)**

| Setting (`idf.py menuconfig` → *Wrist Curl Monitor*) | Default | Meaning |
|---|---|---|
| Data link to the PC | Wi-Fi (TCP) | Or USB serial, or both |
| Wi-Fi mode | Access point | The ESP32 creates its own network; no router needed |
| Network name / password | `WristMonitor` / *(none)* | Set the password (8+ characters) in `firmware/sdkconfig.secrets`, never in the repository; an access-point build without one stops with an error |
| AP channel | 6 | Change it if another network on channel 6 interferes |
| TCP port / beacon port | 3333 / 3334 | Must match `link.port` / `link.discovery_port` in `config.yaml` |

* **Access-point mode (default):** connect the PC's Wi-Fi to **WristMonitor**. The ESP32 is always at `192.168.4.1`, and its OLED shows that address until the app connects. The PC has no internet while it is on this network.
* **Station mode:** the ESP32 joins an existing network (set its name and password in menuconfig) and gets its address from the router. The app finds it through the UDP beacon. If Windows Firewall blocks the beacon, allow Python on private networks, or start the app with `--wifi <address shown on the OLED>`.
* **How it behaves:** the stream is the same binary protocol as over USB, on one TCP connection; beep commands come back on the same connection. Every sample keeps the ESP32's own timestamp, so Wi-Fi jitter does not affect the angle, EMG or fault timing. Only lost packets (shown as sequence gaps, giving SIGNAL_POOR) or a silence longer than `link.disconnect_timeout_s` (1 s, which pauses the session) matter. If the PC app restarts, the new connection simply replaces the old one.
* **Latency:** Wi-Fi power saving is disabled in the firmware, so the beep after a rep still arrives well inside the 150 ms target. Typical extra delay is 5–30 ms.
* **Data buffering:** the ESP32 queues about 1.5 s of data. A longer Wi-Fi stall drops packets instead of delaying everything; those packets are then missing from the session log.
* Keep the ESP32's antenna (the end of the module with the zig-zag track) away from the sEMG leads and pads. Wi-Fi transmit bursts can couple into the high-impedance EMG input.
* The sEMG and battery inputs use ADC1 (GPIO34/35). This matters: ADC2 cannot be used while Wi-Fi is on.

**Host PC**

| Link | How to connect |
|---|---|
| Wi-Fi (default) | Join the **WristMonitor** network, then run `python run_gui.py` (or `--wifi 192.168.4.1`) |
| USB serial | Set `link.transport: serial` in `config.yaml` and choose "USB serial" in menuconfig, or run `python run_gui.py --port COMx` |

### Wiring diagram

```
 9V (+)--switch--+--[1N5819]--> BUCK IN+      BUCK OUT+ (3.3 V) --> 3V3 rail (470uF + 10uF + 100nF)
                 |                                BUCK OUT- --------> GND
                 +--220k--+--100k-- GND     9V (-) --> BUCK IN-
                          |
                          +--> GPIO35 (battery sense, 100 nF to GND)

                   ESP32-WROOM-32 DevKit
                   +--------------------+
     BNO055 SDA ---|GPIO26          3V3 |<- buck 3.3 V; also VCC of BNO055(s), buzzer, OLED, sEMG
     BNO055 SCL ---|GPIO27       5V/VIN |   (not used on battery)
 (hand ADR=3V3 -> 0x29,             GND |-- buck OUT-, battery -, all module grounds
  forearm ADR=GND -> 0x28)              |
    sEMG SIG/OUT --|GPIO34       GPIO25 |-- passive buzzer + (2.5 kHz tone)
 battery divider --|GPIO35       GPIO21 |-- OLED SDA
                   |             GPIO22 |-- OLED SCL
                   |   Wi-Fi antenna  ))) PC, "WristMonitor" network, TCP 3333 (data)
                   |   USB (UART0) =====|==== only for flashing (or USB data link)
                   +--------------------+
```

* All grounds must be common: ESP32, both IMUs, sEMG module, buzzer, OLED and battery.
* Keep the sEMG leads short, and away from the buzzer wires and the ESP32 antenna.
* On boot the firmware waits for the BNO055 (up to 1 s), sets IMU fusion mode, and then reads it every 20 ms. If it stops answering (a loose wire), the firmware resets the bus and sets it up again by itself.

### Build and flash (ESP-IDF)

```bash
cd firmware
copy sdkconfig.secrets.example sdkconfig.secrets   # then put your Wi-Fi password in it (git-ignored)
idf.py set-target esp32
idf.py menuconfig        # link type, pins, EMG output hint, buzzer polarity
idf.py build flash
```

Flash over USB. After that, the USB cable can be unplugged. The console and logs are disabled (`sdkconfig.defaults`) so UART0 stays clean if you use the USB data link; in that case the host keeps DTR/RTS low when opening the port, so the board is not reset.

### Stream protocol

The same byte stream is used over Wi-Fi (TCP port 3333) and USB serial: `A5 5A | type | len | seq u16 | payload | CRC-16/CCITT-FALSE` (little-endian). The sequence counter is global, so the host counts any gap as lost packets. All timestamps come from the ESP32 `esp_timer` µs clock.

| type | payload |
|---|---|
| 0x01 EMG | `t0_us u32, dt_us u16, n u8, n × adc u16` (10 samples / 10 ms; hardware gptimer at 1 kHz) |
| 0x02 IMU | `t_us u32, imu_id u8, roll pitch yaw gx gy gz ax ay az` (i16, JY901 raw units) |
| 0x03 STATUS | `t_us u32, battery_mv u16, flags u8, emg_hint u8, fw u8` (1 Hz) |

Host → device commands are ASCII lines: `START`, `STOP`, `BEEP_OK` (1 × 80 ms), `BEEP_BAD` (3 × 80 ms), and `BEEP_LONG` (1.2 s, fatigue red; **added** to the four required commands). The firmware queues buzzer patterns, so they never overlap.

## 2. Electrode and IMU placement

* **sEMG (one channel):** two dry pads on the anterior forearm, about **1/3 of the way from elbow to wrist**, over flexor carpi radialis / flexor digitorum superficialis, along the muscle. **Reference pad on the elbow bone.** Clean the skin and press the dry pads firmly. The wizard shows a diagram.
* **IMU:** BNO055 on the **back of the hand**, **X axis along the hand toward the fingers**. With the palm up, pitch is wrist flexion, roll is pronation/supination and yaw is radial/ulnar deviation. The sign of flexion and of the hinge gyro axis is detected during calibration, so mounting the box upside down still works.
* **Optional forearm IMU:** mount it with the same axis orientation as the hand IMU and set `imu.forearm_imu: true`. Then θ = hand − forearm, and forearm lift is measured directly (> 10°).

## 3. Calibration (GUI wizard)

The first visit runs all 6 steps. Later sessions run the quick recalibration (steps 1–3), which keeps RVC, ROM, target and tempo. Each step shows one instruction, an animation, a countdown and ✔/✖, and offers **Try again** on failure. Results are saved to `host/profiles/<user>.json`, and a copy goes into every session folder.

1. **Sensor check:** both streams live at ≥ 90 % of nominal rate, packet loss ≤ 2 %, and EMG output type detected.
2. **Rest (5 s):** no weight, hand relaxed. Records the EMG noise floor (mean, SD) and checks the IMU is still.
3. **Start position (3 s):** holding the weight, palm up, wrist neutral. Sets θ = 0 and reference roll/yaw. Set `calibration.start_position: extension` to start from about 15° of extension instead.
4. **Top (3 s move + 3 s hold):** personal ROM. Target = min(45°, 0.9 × ROM). Also detects the flexion/gyro signs and the accelerometer sign convention.
5. **Reference contraction (4 s):** hold at half the target. The mean envelope becomes the **RVC**; activation is reported in %RVC. A true MVC is not used (unsafe for elderly users).
6. **Practice (3 reps):** follow the pacer. Learns the tempo and activation level.

A **load-confirmation** screen comes before calibration; it warns above 3 kg.

## 4. How a rep is judged

States: `IDLE → READY → LIFTING → TOP_HOLD → LOWERING → BOTTOM → evaluate → READY`. Hysteresis: lifting starts above 13°, the bottom is below 10°, and the top is exited below target − 6°. Movement onset is the start of the run with ω > 8°/s.

A rep counts only if the target is reached and held ≥ 0.5 s, the EMG onset is ≤ 100 ms after movement onset, mean activation over lift + hold is ≥ 30 % RVC, and no fault is raised. All faults are logged; the one shown is chosen by `faults.priority`.

| Fault | Default rule | Message |
|---|---|---|
| SIGNAL_POOR | EMG quality failed or IMU dropout/packet loss during the rep | Check sensor contact |
| FOREARM_LIFT | off-plane gyro > 60°/s or unexplained accel > 0.15 g for ≥ 40 ms (2nd IMU: > 10°) | Keep your arm on the table |
| NO_MUSCLE | mean < 30 % RVC, or onset > 100 ms late / absent | Use your wrist muscle |
| OVER_FLEX | peak > min(target + 20°, ROM + 5°) | Not so far |
| WRIST_TWIST | roll > ±20° from calibration | Keep your palm facing up |
| SIDE_BEND | yaw > ±15° (from rep start) | Keep your hand straight |
| INCOMPLETE_ROM | peak < target − 2° | Lift a little higher |
| NOT_RETURNED | lifts again before getting within 10° of start | Lower all the way down |
| TIMEOUT | rep > 15 s | Try again |
| EARLY_DOWN | hold < 0.5 s, or lowering before the target | Pause at the top |
| SWING_FAST | lift < 0.8 s or peak ω > 150°/s | Lift smoothly |
| DROP_FAST | lowering < 1.5 s or peak ω > 90°/s | Lower slowly |
| JERKY | SPARC < −2.6 (lift or lower phase) | Move steadily |

**Safety:** caps on reps, sets and attempts per set; a forced, unskippable rest when fatigue is red; a pause on SIGNAL_POOR lasting ≥ 3 s or on sensor disconnect (never miscounts); and the emergency stop.

## 5. Signal processing and fatigue

* **Raw sEMG:** running-mean DC removal, Butterworth band-pass 20–450 Hz (order 4 per edge), 50 Hz notch plus harmonics (causal live; `filter_emg_offline` is zero-phase), 100 ms RMS envelope. Onset is envelope > rest mean + 3 SD for ≥ 50 ms.
* **Quality checks** (continuous, held 1 s): flatline, clipping, 50 Hz power ratio (only if mains RMS is also > 15 counts), jumps > 1000 counts (dry-pad lift-off), packet dropouts, and rest noise > 3 × baseline (checked only during rest, when the muscle is known to be relaxed).
* **IMU:** fused pitch low-passed at 10 Hz gives θ. ω comes from the hinge gyro axis, and off-plane motion from the other two axes.
* **Fatigue** (valid reps, fixed 10–35° lifting window): Welch MDF/MNF, RMS/ARV (%RVC), Dimitrov FInsm5. The baseline is the mean of the first 3 valid reps. Score = 100 × (0.5·MDF drop + 0.2·FInsm5 rise + 0.2·RMS rise + 0.1·lift-time rise), each term clipped to 0–1 at `full_scale_pct`, then EWMA α = 0.3. Zones: green < 40, amber 40–70 ("Getting tired"), red > 70 ("Please rest" + long tone + rest timer). JASA: if RMS rises together with MDF, that means more effort, not fatigue, so the RMS term is ignored for that rep.
* **ML hook:** any object with `predict(features) -> score`. Set `fatigue.model` to `json:<path>` (linear/logistic) or `pickle:<path>` (sklearn-style). `python train_fatigue.py <sessions...>` fits a ridge model with label = rep_index / total_reps, on **healthy volunteers' sets to near-failure only**.

## 6. Envelope-only mode

If the Schiray module outputs only a rectified/smoothed envelope, MDF/MNF/FInsm5 are **disabled**. The status bar shows "EMG envelope-only (MDF off)". In this mode the envelope is low-passed (5 Hz), and %RVC is measured from the resting DC level. The fatigue score uses the RMS trend (0.4), tempo slowdown (0.3), peak-angle decline (0.15) and smoothness decline (0.15).

The mode comes from `emg.mode` (`auto | raw | envelope`), else the firmware's Kconfig hint, else auto-detection during the sensor check (raw = mid-rail bias with most AC power above 20 Hz). **Check the module's datasheet and set `emg.mode` explicitly once known.**

## 7. Logging, replay and threshold tuning

Each session writes `sessions/<user>/<stamp>/`: `emg.csv` and `imu.csv` (raw synchronised streams, device clock), `events.csv` (session actions and calibration captures, with device time), `reps.csv`, `calibration.json`, `config.yaml` and `meta.json`.

Replay feeds these files through the same pipeline, and decisions are deterministic. EMG is read 20 ms behind each IMU sample, so results do not depend on packet arrival order. To tune:

```bash
python replay.py <session>                         # check: "matches recorded reps.csv: yes"
python replay.py <session> --config tuned.yaml     # a yaml with only the keys you change
python replay.py <session> --recalibrate           # also recompute calibration from the data
python replay.py <session> --gui                   # watch it in real time
```

Tune one threshold at a time in `host/config.yaml` (all thresholds live there; per-user values live in the profile), replay the same recordings, and compare the fault columns.

## 8. Known limitations

* **Single EMG channel.** Activation is one number; the arm drawing colours one region, with no spatial distribution. **Finger flexion (gripping harder) also activates this group**, so squeezing the dumbbell can look like wrist effort. The angle, tempo and IMU checks only partly cover this.
* **Single-IMU forearm-lift inference.** Without a forearm IMU, forearm/elbow lift is inferred from off-plane gyro and from acceleration not explained by gravity plus wrist rotation. A slow, pure forearm tilt about an axis parallel to the wrist can pass as wrist flexion; NO_MUSCLE then usually catches it, but not always. Fit the second IMU for certainty.
* **Envelope-only mode** cannot measure spectral fatigue (MDF/FInsm5); its score relies on amplitude and movement changes.
* **With the weight held at neutral, the flexors are already active** before each lift, so the onset-lag check mostly matters for lifts that start from a supported/relaxed hand. The %RVC check catches passive lifts.
* BNO055 yaw drifts slowly in IMU fusion mode (no magnetometer), so SIDE_BEND is measured from the start of each rep (`faults.yaw_reference`).
* The defaults are engineering starting points, not clinically validated. Tune them with a therapist using recorded sessions.

## 9. Defaults chosen where the spec was open (all configurable)

* Pins above; 1 kHz EMG in 10-sample packets; IMU 50 Hz (BNO055, `WM_IMU_PERIOD_MS = 20`; 10 ms gives 100 Hz, the chip's fusion maximum); a 2nd BNO055 at 0x28 on the same bus.
* The **buzzer has a fixed loudness**, so the volume slider controls the matching PC sound; `feedback.buzzer` turns the buzzer on or off. TTS is optional (`feedback.tts`, needs `pyttsx3`).
* Top hold = the longest still period (smoothed |ω| < 3°/s) within 3° of the peak. A slow touch-and-go reversal is not a pause.
* EARLY_DOWN is also logged whenever the target is missed (spec), but INCOMPLETE_ROM is shown first.
* Priority puts posture and structure (NOT_RETURNED, TIMEOUT) ahead of tempo and smoothness faults.
* Excursions under 20° are ignored, not judged (no bad beep for twitches).
* Onset lag is clamped at −1 s ("already active").
* Caps: 12 reps × 3 sets, max 5 sets, 60 reps per session, 25 attempts per set. Rest 60 s; forced rest 90 s.
* Pacer: Lift 2 s, Hold 1 s, Lower 3 s, Rest 1 s. The idle demo starts after 5 s of no movement or before the first rep.
