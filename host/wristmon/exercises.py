"""Exercise catalogue: the arm routine (bicep curls, shoulder blade squeezes, pendulum swings),
the leg routine, and the original wrist curls and knee exercises (kept for old profiles,
replays and as the base config, but no longer offered in the menu).

Every exercise uses the same pipeline: one IMU measures a single hinge angle and one sEMG
channel measures the agonist muscle. An exercise is therefore just
  * config overrides (target, tempo, thresholds, fault messages, calibration wording),
  * GUI wording (wizard steps, demo captions, sensor placement) and which drawing to show,
  * its own calibration profile per user, because sensors sit on a different limb.

config.yaml describes the wrist curl with a weight, so that exercise has no overrides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .calibration import Profile, safe_name
from .config import deep_merge

DEFAULT = "wrist_weight"


@dataclass
class Exercise:
    key: str
    limb: str                    # arms | legs | wrist, legacy (old exercises, not in the menu)
    title: str                   # shown on the choice cards and the exercise page
    description: str
    view: str                    # drawing: arm | knee | hip | scapula | curl | pendulum |
                                 #          quad_set | leg_raise | knee_emg | calf_raise
    placement: str               # sensor diagram (when no photo): arm | shin | thigh
    uses_weight: bool
    assisted: bool = False       # a weight is optional: 0 kg = assisted by the other hand
    config: dict = field(default_factory=dict)
    steps: dict = field(default_factory=dict)       # wizard step -> (title, instruction)
    captions: dict = field(default_factory=dict)    # demo captions (pacer phases and wizard poses)
    texts: dict = field(default_factory=dict)       # other GUI wording


# Wording for the wrist curl with a weight; other exercises override what differs.
STEPS = {
    "sensor_check": ("Check the sensors",
                     "Clean the skin on your lower arm (forearm). Strap the sensor box onto the palm side of "
                     "your lower arm as shown, so its dry EMG sensors press flat on the skin. Pull the strap snug."),
    "rest": ("Relax", "Put the weight down. Rest your arm and hand on the table. Stay relaxed and still."),
    "zero": ("Start position", "Hold the weight, palm up. Keep your hand straight, in line with your arm. Hold still."),
    "top": ("Top position", "Slowly curl your wrist up as far as is comfortable. Then hold it there."),
    "rvc": ("Hold halfway", "Lift the weight halfway up and hold it still."),
    "practice": ("Practice", "Do 3 slow curls. Follow the moving guide."),
}
CAPTIONS = {
    "pause": "Arm on table…", "lift": "Lift your hand…", "hold": "Hold…", "lower": "Lower slowly…",
    "rest": "Relax your hand", "zero": "Hand straight, palm up", "top": "Curl up and hold",
    "rvc": "Hold halfway", "top_cue": "Curl up now",
}
TEXTS = {
    "angle_label": "Wrist angle",
    "muscle_label": "Wrist flexors",
    "set_done": "Well done. Put the weight down and relax your hand.",
    "stopped": "You pressed STOP. Put the weight down. Continue when you are ready.",
    "ready_title": "Confirm the weight",
    "ready_text": "Is this the weight in your hand?",
    "ready_button": "Yes, this weight is right",
}

LEG_TEXTS = {
    "set_done": "Well done. Put your foot down flat and relax your leg.",
    "stopped": "You pressed STOP. Put your foot down. Continue when you are ready.",
    "ready_title": "Get ready",
    "ready_button": "I am ready",
    "muscle_label": "Thigh muscle",
}

# Leg-sensor wording for calibration messages (calibration.py reads cfg["texts"]).
LEG_CAL_TEXTS = {
    "sensor": "leg sensor",
    "rest_still": "Please keep your leg still and relaxed.",
    "rvc_small": "Muscle signal too small. Press the pads firmly and tighten your thigh.",
}

LEG_SENSOR_CHECK = ("Clean the skin on your upper leg (thigh). Strap the sensor box on top of your thigh as "
                    "shown, so its dry EMG sensors press flat on the skin. Pull the strap snug.")


def _muscle_driven(target_pct: float, min_hold_s: float, messages: dict, still_msg: str) -> dict:
    """Config for an exercise counted from the muscle signal: theta is the effort in percent of
    the strongest squeeze, so the angle thresholds become percentages and the angle-only
    checks (speed, twist, over-flexion, smoothness) are switched off."""
    return {
        "exercise": {"signal": "emg", "target_deg": target_pct, "load_kg": 0.0},
        "state_machine": {"ready_deg": 12, "still_omega_dps": 40, "lift_start_deg": 25, "bottom_deg": 15,
                          "target_tol_deg": 5, "top_hyst_deg": 10, "reversal_deg": 15, "min_attempt_deg": 30,
                          "omega_onset_dps": 20, "max_rep_s": 20, "hold_band_deg": 15, "hold_omega_dps": 40,
                          "hold_smooth_s": 0.3, "hold_rule": "above_target"},
        "faults": {"min_hold_s": min_hold_s, "min_lift_s": 0.1, "max_lift_omega_dps": 1e5, "min_lower_s": 0.0,
                   "max_lower_omega_dps": 1e5, "roll_max_deg": 1000, "yaw_max_deg": 1000,
                   "over_flex_margin_deg": 1000, "rom_margin_deg": 1000, "sparc_min": -1000,
                   "forearm_acc_g": 0.6, "forearm_offplane_dps": 150,
                   "messages": {"FOREARM_LIFT": still_msg, "TIMEOUT": "Relax, then try again", **messages}},
        "fatigue": {"window_deg": [20, 50]},
        "texts": {**LEG_CAL_TEXTS, "zero_still": "Please keep your leg still and relaxed.",
                  "rvc_small": "Muscle signal too small. Press the sensor firmly and tighten the muscle harder."},
    }

EXERCISES: dict[str, Exercise] = {
    "wrist_weight": Exercise(
        key="wrist_weight", limb="wrist", title="With a weight",
        description="Wrist curls holding a small dumbbell or a water bottle.",
        view="arm", placement="arm", uses_weight=True),

    "wrist_free": Exercise(
        key="wrist_free", limb="wrist", title="No weight",
        description="Wrist curls with a gentle fist. Nothing to hold.",
        view="arm", placement="arm", uses_weight=False,
        config={
            "exercise": {"load_kg": 0.0},
            "texts": {"rvc_small": "Muscle signal too small. Press the pads firmly and squeeze your fist a little.",
                      "zero_still": "Please hold your hand still, in line with your arm."},
            "faults": {"messages": {"NO_MUSCLE": "Keep a gentle fist"}},
        },
        steps={
            "rest": ("Relax", "Rest your arm and hand on the table, palm up. Stay relaxed and still."),
            "zero": ("Start position", "Make a gentle fist, palm up. Keep your hand in line with your arm. Hold still."),
            "rvc": ("Hold halfway", "Keep the gentle fist. Lift your hand halfway up and hold it still."),
        },
        captions={"lift": "Fist, lift your hand…", "zero": "Gentle fist, palm up"},
        texts={"set_done": "Well done. Open your hand and relax.",
               "stopped": "You pressed STOP. Rest your hand. Continue when you are ready.",
               "ready_title": "Get ready",
               "ready_text": "Sit at a table. Rest your forearm on it, palm up, with your hand over the edge.",
               "ready_button": "I am ready"}),

    "knee_extension": Exercise(
        key="knee_extension", limb="legacy", title="Knee straightening",
        description="Sitting on a chair, slowly straighten one knee, then lower the foot.",
        view="knee", placement="shin", uses_weight=False,
        config={
            "exercise": {"target_deg": 60, "load_kg": 0.0, "reps_per_set": 10,
                         "tempo": {"lift_s": 2.0, "hold_s": 2.0, "lower_s": 3.0, "pause_s": 1.5}},
            # Shin sensor: about 30 cm below the knee.
            "imu": {"hand_lever_m": 0.30},
            # The shin hangs vertically at the start, where the sensor's roll and yaw are
            # undefined (pitch is near -90 degrees), so those two checks are switched off.
            "faults": {"roll_max_deg": 1000, "yaw_max_deg": 1000, "forearm_acc_g": 0.3,
                       "forearm_offplane_dps": 90, "max_lift_omega_dps": 120, "max_lower_omega_dps": 70,
                       "messages": {
                           "INCOMPLETE_ROM": "Straighten your knee a little more",
                           "EARLY_DOWN": "Hold your leg up for a moment",
                           "NO_MUSCLE": "Tighten your thigh muscle",
                           "FOREARM_LIFT": "Keep your thigh on the chair",
                           "DROP_FAST": "Lower your foot slowly",
                           "SWING_FAST": "Lift smoothly, do not kick",
                           "NOT_RETURNED": "Bring your foot all the way down"}},
            "fatigue": {"window_deg": [10, 45]},
            "texts": {**LEG_CAL_TEXTS,
                      "zero_still": "Please keep your foot flat and your leg still.",
                      "top_more": "Straighten your knee a little more, then hold.",
                      "rvc_angle": "Hold your foot halfway up (about {want:.0f}°)."},
        },
        steps={
            "sensor_check": ("Check the sensors", LEG_SENSOR_CHECK),
            "rest": ("Relax", "Sit back in the chair. Put your foot flat on the floor. Stay relaxed and still."),
            "zero": ("Start position", "Sit up straight, knee bent, foot flat on the floor. Hold still."),
            "top": ("Top position", "Slowly straighten your knee as far as is comfortable. Then hold it there."),
            "rvc": ("Hold halfway", "Lift your foot halfway up and hold it still."),
            "practice": ("Practice", "Do 3 slow knee straightenings. Follow the moving guide."),
        },
        captions={"pause": "Foot on the floor…", "lift": "Straighten your knee…", "lower": "Lower slowly…",
                  "rest": "Relax your leg", "zero": "Knee bent, foot flat", "top": "Straighten and hold",
                  "rvc": "Hold halfway", "top_cue": "Straighten now"},
        texts={**LEG_TEXTS, "angle_label": "Knee angle",
               "ready_text": "Sit on a firm chair with a backrest, feet flat on the floor. "
                             "Hold the sides of the chair if you need to."}),

    "knee_lift": Exercise(
        key="knee_lift", limb="legacy", title="Knee lift",
        description="Sitting on a chair, slowly lift one knee up, like marching, then lower it.",
        view="hip", placement="thigh", uses_weight=False,
        config={
            "exercise": {"target_deg": 30, "load_kg": 0.0, "reps_per_set": 10,
                         "tempo": {"lift_s": 2.0, "hold_s": 2.0, "lower_s": 3.0, "pause_s": 1.5}},
            "imu": {"hand_lever_m": 0.30},
            # A smaller movement than the wrist curl, so the angle thresholds are scaled down.
            "state_machine": {"lift_start_deg": 8, "bottom_deg": 6, "min_attempt_deg": 12,
                              "ready_deg": 5, "reversal_deg": 4},
            "calibration": {"min_rom_deg": 10, "rvc_angle_tol_deg": 8},
            "faults": {"over_flex_margin_deg": 15, "forearm_acc_g": 0.3, "forearm_offplane_dps": 90,
                       "max_lift_omega_dps": 100, "max_lower_omega_dps": 60,
                       "messages": {
                           "INCOMPLETE_ROM": "Lift your knee a little higher",
                           "EARLY_DOWN": "Hold your knee up for a moment",
                           "NO_MUSCLE": "Tighten your thigh muscle",
                           "FOREARM_LIFT": "Keep your body still",
                           "DROP_FAST": "Lower your foot slowly",
                           "SWING_FAST": "Lift smoothly",
                           "WRIST_TWIST": "Keep your knee pointing forward",
                           "SIDE_BEND": "Lift straight up, not to the side",
                           "NOT_RETURNED": "Put your foot all the way down"}},
            "fatigue": {"window_deg": [5, 22]},
            "texts": {**LEG_CAL_TEXTS,
                      "zero_still": "Please keep both feet flat and your leg still.",
                      "top_more": "Lift your knee a little higher, then hold.",
                      "rvc_angle": "Hold your knee halfway up (about {want:.0f}°)."},
        },
        steps={
            "sensor_check": ("Check the sensors", LEG_SENSOR_CHECK),
            "rest": ("Relax", "Sit back in the chair. Put both feet flat on the floor. Stay relaxed and still."),
            "zero": ("Start position", "Sit up straight, both feet flat on the floor. Hold still."),
            "top": ("Top position", "Slowly lift your knee up as high as is comfortable. Then hold it there."),
            "rvc": ("Hold halfway", "Lift your knee halfway up and hold it still."),
            "practice": ("Practice", "Do 3 slow knee lifts. Follow the moving guide."),
        },
        captions={"pause": "Foot on the floor…", "lift": "Lift your knee…", "lower": "Lower slowly…",
                  "rest": "Relax your leg", "zero": "Both feet flat", "top": "Lift and hold",
                  "rvc": "Hold halfway", "top_cue": "Lift now"},
        texts={**LEG_TEXTS, "angle_label": "Hip angle",
               "ready_text": "Sit on a firm chair with a backrest, feet flat on the floor. "
                             "Hold the sides of the chair if you need to."}),

    # ---- leg routine. The sensor box sits on the thigh (on the calf for calf raises). Only the
    # straight leg raise moves the thigh, so the other three are counted from the muscle signal.
    "quad_set": Exercise(
        key="quad_set", limb="legs", title="Quad sets",
        description="Lie or sit with your leg straight. Tighten your front thigh muscle and hold 5 to 10 s.",
        view="quad_set", placement="thigh", uses_weight=False,
        config=deep_merge(_muscle_driven(60, 5.0, {
            "INCOMPLETE_ROM": "Tighten your thigh harder",
            "EARLY_DOWN": "Hold the squeeze for at least 5 seconds",
            "NOT_RETURNED": "Relax your thigh fully between squeezes"}, "Keep your leg still"),
            {"exercise": {"reps_per_set": 8, "sets": 2, "rest_s": 30,
                          "tempo": {"lift_s": 1.0, "hold_s": 6.0, "lower_s": 1.0, "pause_s": 3.0}}}),
        steps={
            "sensor_check": ("Check the sensors", LEG_SENSOR_CHECK),
            "rest": ("Relax", "Lie on your back or sit with your leg straight out. Let your thigh relax."),
            "zero": ("Start position", "Leg straight and relaxed, toes pointing up. Hold still."),
            "top": ("Strongest squeeze", "Tighten your front thigh as hard as you can, pressing the back of "
                                         "your knee down. Hold it."),
            "practice": ("Practice", "Do 3 squeezes. Hold each one for 5 seconds, then relax."),
        },
        captions={"pause": "Thigh relaxed…", "lift": "Tighten your thigh…", "hold": "Hold the squeeze…",
                  "lower": "Relax…", "rest": "Relax your thigh", "zero": "Leg straight, relaxed",
                  "top": "Squeeze hard and hold", "top_cue": "Squeeze now"},
        texts={**LEG_TEXTS, "angle_label": "Muscle effort",
               "set_done": "Well done. Relax your leg.",
               "ready_text": "Lie on your back (or sit) with the leg straight. A small rolled towel under "
                             "the knee helps."}),

    "straight_leg_raise": Exercise(
        key="straight_leg_raise", limb="legs", title="Straight leg raises",
        description="Lie on your back, one knee bent. Lift the straight leg about 15 cm and lower slowly.",
        view="leg_raise", placement="thigh", uses_weight=False,
        config={
            "exercise": {"target_deg": 12, "load_kg": 0.0, "reps_per_set": 8, "sets": 2, "rest_s": 30,
                         "tempo": {"lift_s": 2.0, "hold_s": 2.0, "lower_s": 3.0, "pause_s": 1.5}},
            "imu": {"hand_lever_m": 0.3},           # hip to the sensor on the thigh
            # 15 cm at the heel is only about 10 degrees at the hip, so thresholds are small.
            "state_machine": {"lift_start_deg": 4, "bottom_deg": 2.5, "min_attempt_deg": 7, "ready_deg": 2.5,
                              "reversal_deg": 2.5, "top_hyst_deg": 2, "omega_onset_dps": 4},
            "calibration": {"min_rom_deg": 7, "rvc_angle_tol_deg": 4},
            "faults": {"min_hold_s": 1.0, "min_lift_s": 0.5, "min_lower_s": 1.2, "min_activation_pct": 20,
                       "forearm_acc_g": 0.3, "forearm_offplane_dps": 90, "over_flex_margin_deg": 10,
                       "messages": {
                           "INCOMPLETE_ROM": "Lift your leg a little higher",
                           "EARLY_DOWN": "Hold your leg up for a moment",
                           "NO_MUSCLE": "Tighten your thigh and keep the knee straight",
                           "FOREARM_LIFT": "Keep your body still",
                           "DROP_FAST": "Lower your leg slowly",
                           "SWING_FAST": "Lift smoothly, do not kick",
                           "WRIST_TWIST": "Keep your toes pointing up",
                           "SIDE_BEND": "Lift straight up, not to the side",
                           "OVER_FLEX": "Not so high, about 15 cm is enough",
                           "NOT_RETURNED": "Lower your leg all the way down"}},
            "fatigue": {"window_deg": [3, 10]},
            "texts": {**LEG_CAL_TEXTS,
                      "zero_still": "Please keep your leg flat and still.",
                      "top_more": "Lift your straight leg a little higher, then hold.",
                      "rvc_angle": "Hold your leg a little up (about {want:.0f}°)."},
        },
        steps={
            "sensor_check": ("Check the sensors", LEG_SENSOR_CHECK),
            "rest": ("Relax", "Lie on your back. Bend the other knee, foot flat. Let the straight leg relax."),
            "zero": ("Start position", "Straight leg flat on the floor, toes up. Hold still."),
            "top": ("Top position", "Tighten your thigh and lift the straight leg about 15 cm. Hold it there."),
            "rvc": ("Hold halfway", "Lift the straight leg halfway (a few cm) and hold it still."),
            "practice": ("Practice", "Do 3 slow leg raises. Follow the moving guide."),
        },
        captions={"pause": "Leg on the floor…", "lift": "Lift the straight leg…", "hold": "Hold…",
                  "lower": "Lower slowly…", "rest": "Relax your leg", "zero": "Leg flat, toes up",
                  "top": "Lift and hold", "rvc": "Hold halfway", "top_cue": "Lift now"},
        texts={**LEG_TEXTS, "angle_label": "Leg angle",
               "set_done": "Well done. Rest your leg on the floor.",
               "ready_text": "Lie on your back on a firm surface. Bend the other knee with that foot flat."}),

    "seated_knee_extension": Exercise(
        key="seated_knee_extension", limb="legs", title="Seated knee extensions",
        description="Sit upright. Straighten one leg level with the floor, hold 3 to 5 s, lower gently.",
        view="knee_emg", placement="thigh", uses_weight=False,
        config=deep_merge(_muscle_driven(60, 3.0, {
            "INCOMPLETE_ROM": "Straighten your leg fully, level with the floor",
            "EARLY_DOWN": "Hold your leg straight for 3 to 5 seconds",
            "NOT_RETURNED": "Lower your foot all the way and relax"}, "Keep your thigh on the chair"),
            {"exercise": {"reps_per_set": 8, "sets": 2, "rest_s": 30,
                          "tempo": {"lift_s": 2.0, "hold_s": 4.0, "lower_s": 2.0, "pause_s": 1.5}}}),
        steps={
            "sensor_check": ("Check the sensors", LEG_SENSOR_CHECK),
            "rest": ("Relax", "Sit back in the chair, foot flat on the floor. Stay relaxed and still."),
            "zero": ("Start position", "Sit upright, knee bent, foot flat on the floor. Hold still."),
            "top": ("Leg straight", "Straighten your leg level with the floor and tighten the thigh. Hold it."),
            "practice": ("Practice", "Do 3 knee extensions. Hold each one for 3 seconds."),
        },
        captions={"pause": "Foot on the floor…", "lift": "Straighten your leg…", "hold": "Hold it level…",
                  "lower": "Lower gently…", "rest": "Relax your leg", "zero": "Knee bent, foot flat",
                  "top": "Straighten and hold", "top_cue": "Straighten now"},
        texts={**LEG_TEXTS, "angle_label": "Muscle effort",
               "ready_text": "Sit upright on a firm chair with a backrest, feet flat on the floor."}),

    "calf_raise": Exercise(
        key="calf_raise", limb="legs", title="Standing calf raises",
        description="Hold a sturdy chair. Rise onto your tiptoes, then slowly lower your heels.",
        view="calf_raise", placement="calf", uses_weight=False,
        config=deep_merge(_muscle_driven(60, 0.3, {
            "INCOMPLETE_ROM": "Rise a little higher onto your toes",
            "EARLY_DOWN": "Pause at the top for a moment",
            "NOT_RETURNED": "Lower your heels all the way to the floor"}, "Keep steady, hold the chair"),
            {"exercise": {"reps_per_set": 10, "sets": 3, "rest_s": 30,
                          "tempo": {"lift_s": 1.5, "hold_s": 0.5, "lower_s": 2.0, "pause_s": 1.0}},
             "texts": {"sensor": "calf sensor", "rest_still": "Please stand still with your weight on both feet."}}),
        steps={
            "sensor_check": ("Check the sensors",
                             "Move the sensor box to the back of your lower leg (the calf). Strap it on so its "
                             "dry EMG sensors press flat on the skin. Pull the strap snug."),
            "rest": ("Relax", "Stand tall holding a sturdy chair or counter, weight on both feet. Stay still."),
            "zero": ("Start position", "Stand tall, heels on the floor. Hold still."),
            "top": ("Up on your toes", "Rise up onto your tiptoes as high as is comfortable. Hold it there."),
            "practice": ("Practice", "Do 3 slow calf raises. Follow the moving guide."),
        },
        captions={"pause": "Heels down…", "lift": "Rise onto your toes…", "hold": "Hold…",
                  "lower": "Lower your heels slowly…", "rest": "Stand relaxed", "zero": "Heels on the floor",
                  "top": "Up on your toes", "top_cue": "Rise now"},
        texts={**LEG_TEXTS, "angle_label": "Muscle effort", "muscle_label": "Calf muscle",
               "set_done": "Well done. Stand still and relax your legs.",
               "stopped": "You pressed STOP. Hold the chair and put your heels down. Continue when ready.",
               "ready_text": "Stand behind a sturdy chair or at a counter and hold it with both hands."}),

    # ---- arm routine. The sensor box sits on the lower arm for all three; the arm starts
    # hanging straight down, where the sensor's roll and yaw are undefined (pitch near -90),
    # so those two checks are switched off, as for knee straightening.
    "scapular_retraction": Exercise(
        key="scapular_retraction", limb="arms", title="Shoulder blade squeezes",
        description="Arms by your sides. Squeeze your shoulder blades together and hold.",
        view="scapula", placement="arm", uses_weight=False,
        config={
            "exercise": {"target_deg": 15, "load_kg": 0.0, "reps_per_set": 8, "sets": 2, "rest_s": 40,
                         "tempo": {"lift_s": 2.0, "hold_s": 5.0, "lower_s": 2.0, "pause_s": 1.5}},
            "imu": {"hand_lever_m": 0.55},          # shoulder to the sensor on the lower arm
            # A small movement (the arms only move back a little), so angle thresholds are small.
            "state_machine": {"lift_start_deg": 5, "bottom_deg": 3, "min_attempt_deg": 8, "ready_deg": 3,
                              "reversal_deg": 3, "top_hyst_deg": 2, "max_rep_s": 20, "omega_onset_dps": 4},
            "calibration": {"min_rom_deg": 8, "rvc_angle_tol_deg": 5},
            # The forearm sensor cannot see the shoulder-blade muscles: no muscle check. Time
            # limits are shorter because only a few degrees are covered.
            "faults": {"check_muscle": False, "min_hold_s": 3.0, "min_lift_s": 0.5, "min_lower_s": 0.8,
                       "roll_max_deg": 1000, "yaw_max_deg": 1000, "forearm_acc_g": 0.3,
                       "forearm_offplane_dps": 90, "over_flex_margin_deg": 10,
                       "messages": {
                           "INCOMPLETE_ROM": "Squeeze your shoulder blades a little more",
                           "EARLY_DOWN": "Hold the squeeze for 5 seconds",
                           "FOREARM_LIFT": "Keep your body still",
                           "DROP_FAST": "Relax slowly",
                           "SWING_FAST": "Squeeze slowly, do not jerk",
                           "OVER_FLEX": "Not so far back",
                           "NOT_RETURNED": "Relax fully between squeezes"}},
            "fatigue": {"window_deg": [3, 12]},
            "texts": {"sensor": "arm sensor",
                      "rest_still": "Please let your arms hang still and relaxed.",
                      "zero_still": "Please stand or sit tall with your arms still by your sides.",
                      "top_more": "Squeeze your shoulder blades a little more, then hold.",
                      "rvc_angle": "Hold your arms a little back (about {want:.0f}°).",
                      "rvc_small": "Muscle signal too small. Press the sensor firmly and make a fist."},
        },
        steps={
            "rest": ("Relax", "Sit or stand tall. Let your arms hang loosely by your sides. Stay still."),
            "zero": ("Start position", "Arms straight by your sides, palms facing forward. Hold still."),
            "top": ("Squeeze", "Squeeze your shoulder blades together. Your arms move back a little. Hold it."),
            "rvc": ("Hold halfway", "Squeeze gently, halfway, make a fist and hold it still."),
            "practice": ("Practice", "Do 3 slow squeezes. Hold each one for 5 seconds."),
        },
        captions={"pause": "Arms by your sides…", "lift": "Squeeze your shoulder blades…",
                  "hold": "Hold the squeeze…", "lower": "Relax slowly…", "rest": "Arms relaxed",
                  "zero": "Arms by your sides", "top": "Squeeze and hold", "rvc": "Squeeze halfway",
                  "top_cue": "Squeeze now"},
        texts={"angle_label": "Arm angle", "muscle_label": "Shoulder blades",
               "set_done": "Well done. Let your arms relax.",
               "stopped": "You pressed STOP. Relax your arms. Continue when you are ready.",
               "ready_title": "Get ready",
               "ready_text": "Sit or stand tall with your arms by your sides. Keep your neck relaxed.",
               "ready_button": "I am ready"}),

    "bicep_curl": Exercise(
        key="bicep_curl", limb="arms", title="Bicep curls",
        description="Bend your elbow to lift a light weight, or help the lift with your other hand.",
        view="curl", placement="arm", uses_weight=True, assisted=True,
        config={
            "exercise": {"target_deg": 90, "load_kg": 1.0, "load_warning_kg": 4.0, "reps_per_set": 10,
                         "sets": 2, "rest_s": 60,          # 20 curls per session
                         "tempo": {"lift_s": 2.0, "hold_s": 1.0, "lower_s": 3.0, "pause_s": 1.0}},
            "imu": {"hand_lever_m": 0.25},          # elbow to the sensor
            "calibration": {"rom_fraction": 0.95},
            "faults": {"roll_max_deg": 1000, "yaw_max_deg": 1000, "forearm_acc_g": 0.3,
                       "forearm_offplane_dps": 90,
                       "messages": {
                           "INCOMPLETE_ROM": "Bend your elbow a little more",
                           "EARLY_DOWN": "Hold at the top for a moment",
                           "NO_MUSCLE": "Use your arm muscle to lift",
                           "FOREARM_LIFT": "Keep your elbow by your side",
                           "DROP_FAST": "Lower your arm slowly",
                           "SWING_FAST": "Lift smoothly, do not swing",
                           "OVER_FLEX": "Not so high",
                           "NOT_RETURNED": "Lower your arm all the way down"}},
            "fatigue": {"window_deg": [20, 70]},
            "texts": {"sensor": "arm sensor",
                      "rest_still": "Please let your arm hang still and relaxed.",
                      "zero_still": "Please let your arm hang straight and still.",
                      "top_more": "Bend your elbow a little more, then hold.",
                      "rvc_angle": "Hold your forearm halfway up (about {want:.0f}°).",
                      "rvc_small": "Muscle signal too small. Press the sensor firmly and hold your arm up."},
        },
        steps={
            "rest": ("Relax", "Put the weight down. Let your arm hang loosely by your side. Stay still."),
            "zero": ("Start position", "Hold the weight (or nothing), palm forward, arm straight down "
                                       "by your side. Hold still."),
            "top": ("Top position", "Keep your elbow by your side. Slowly bend it as far as is "
                                    "comfortable. Then hold it there."),
            "rvc": ("Hold halfway", "Bend your elbow halfway up and hold it still."),
            "practice": ("Practice", "Do 3 slow curls. Follow the moving guide."),
        },
        captions={"pause": "Arm straight down…", "lift": "Bend your elbow…", "hold": "Hold…",
                  "lower": "Lower slowly…", "rest": "Relax your arm", "zero": "Arm straight, palm forward",
                  "top": "Curl up and hold", "rvc": "Hold halfway", "top_cue": "Curl up now"},
        texts={"angle_label": "Elbow angle", "muscle_label": "Elbow flexors",
               "set_done": "Well done. Put the weight down and relax your arm.",
               "stopped": "You pressed STOP. Put the weight down. Continue when you are ready.",
               "ready_title": "Choose the weight",
               "ready_text": "Set the weight you will hold. Choose 0 kg for an assisted curl: "
                             "hold your wrist with your other hand and help the lift.",
               "ready_button": "This is right, start"}),

    "pendulum": Exercise(
        key="pendulum", limb="arms", title="Pendulum swings",
        description="Lean on a table and let your arm swing gently forward and back.",
        view="pendulum", placement="arm", uses_weight=False,
        config={
            "exercise": {"target_deg": 20, "load_kg": 0.0, "reps_per_set": 15, "sets": 3, "rest_s": 30,
                         "tempo": {"lift_s": 1.5, "hold_s": 0.5, "lower_s": 1.5, "pause_s": 0.5}},
            "imu": {"hand_lever_m": 0.6},           # shoulder to the sensor
            "state_machine": {"lift_start_deg": 6, "bottom_deg": 4, "min_attempt_deg": 10, "ready_deg": 4,
                              "reversal_deg": 3, "top_hyst_deg": 2},
            "calibration": {"min_rom_deg": 10, "rvc_angle_tol_deg": 6},
            # The arm should hang relaxed and swing by itself, so there is no muscle check.
            "faults": {"check_muscle": False, "min_hold_s": 0.2, "min_lift_s": 0.5, "min_lower_s": 0.8,
                       "max_lift_omega_dps": 100, "max_lower_omega_dps": 100,
                       "roll_max_deg": 1000, "yaw_max_deg": 1000, "forearm_acc_g": 0.4,
                       "forearm_offplane_dps": 90, "over_flex_margin_deg": 12,
                       "messages": {
                           "INCOMPLETE_ROM": "Let your arm swing a little further",
                           "EARLY_DOWN": "Let the swing pause at the front",
                           "FOREARM_LIFT": "Keep your body still, let the arm swing",
                           "DROP_FAST": "Swing back gently",
                           "SWING_FAST": "Swing gently, not fast",
                           "OVER_FLEX": "Smaller swings",
                           "NOT_RETURNED": "Let your arm hang straight down between swings"}},
            "fatigue": {"window_deg": [4, 16]},
            "texts": {"sensor": "arm sensor",
                      "rest_still": "Please let your arm hang still and relaxed.",
                      "zero_still": "Please let your arm hang straight down and still.",
                      "top_more": "Swing your arm a little further forward, then hold.",
                      "rvc_angle": "Hold your arm a little forward (about {want:.0f}°).",
                      "rvc_small": "Muscle signal too small. Press the sensor firmly and make a fist."},
        },
        steps={
            "rest": ("Relax", "Lean forward with your other hand on a table. Let your arm hang loosely. "
                              "Stay still."),
            "zero": ("Start position", "Keep leaning on the table. Let your arm hang straight down. Hold still."),
            "top": ("Front of the swing", "Move your arm forward as far as is comfortable. Then hold it there."),
            "rvc": ("Hold halfway", "Hold your arm halfway forward, make a fist and hold it still."),
            "practice": ("Practice", "Do 3 gentle swings forward and back. Follow the moving guide."),
        },
        captions={"pause": "Arm hanging down…", "lift": "Swing forward…", "hold": "Pause…",
                  "lower": "Swing back…", "rest": "Let your arm hang", "zero": "Arm hanging straight",
                  "top": "Forward and hold", "rvc": "Hold halfway", "top_cue": "Swing forward now"},
        texts={"angle_label": "Swing angle", "muscle_label": "Shoulder",
               "set_done": "Well done. Stand up slowly and relax your arm.",
               "stopped": "You pressed STOP. Hold the table and stand up slowly. Continue when you are ready.",
               "ready_title": "Get ready",
               "ready_text": "Stand next to a sturdy table. Put your other hand on it, bend forward a little "
                             "at the hips and let your arm hang down.",
               "ready_button": "I am ready"}),
}


@dataclass
class Routine:
    """Exercises done one after another. Each exercise is done for a number of good reps
    (its session count, see session_reps); the routine has no time limit."""
    key: str
    title: str
    keys: list                   # exercise keys, in order


def session_reps(key: str) -> tuple[int, int, int]:
    """(good reps per session, sets, reps per set) of an exercise: its sets x reps per set."""
    e = EXERCISES[key].config["exercise"]
    return e["sets"] * e["reps_per_set"], e["sets"], e["reps_per_set"]


ROUTINES: dict[str, Routine] = {
    "arms": Routine("arms", "Arm routine", ["bicep_curl", "scapular_retraction", "pendulum"]),
    "legs": Routine("legs", "Leg routine", ["quad_set", "straight_leg_raise", "seated_knee_extension",
                                            "calf_raise"]),
}


def get(key: str | None) -> Exercise:
    return EXERCISES.get(key or DEFAULT, EXERCISES[DEFAULT])


def for_limb(limb: str) -> list[Exercise]:
    return [e for e in EXERCISES.values() if e.limb == limb]


def exercise_config(base_cfg: dict, key: str) -> dict:
    """Base config with this exercise's overrides; the key is recorded for logs and replay."""
    ex = get(key)
    return deep_merge(base_cfg, deep_merge(ex.config, {"exercise": {"key": ex.key}}))


def steps(ex: Exercise) -> dict:
    return {**STEPS, **ex.steps}


def captions(ex: Exercise) -> dict:
    return {**CAPTIONS, **ex.captions}


def texts(ex: Exercise) -> dict:
    return {**TEXTS, **ex.texts}


# ------------------------------------------------------------------ per-exercise profiles

SEP = "@"     # never produced by safe_name, so it cannot clash with a user name


def profile_path(directory: str | Path, user: str, key: str) -> Path:
    """Wrist curl with a weight keeps the original <user>.json; others get <user>@<key>.json."""
    stem = safe_name(user) if key == DEFAULT else f"{safe_name(user)}{SEP}{key}"
    return Path(directory) / f"{stem}.json"


def load_profile(directory: str | Path, user: str, key: str, default_load_kg: float) -> Profile:
    path = profile_path(directory, user, key)
    p = Profile.load(path) if path.exists() else Profile(user=user, load_kg=default_load_kg)
    p.user = user
    return p


def save_profile(directory: str | Path, profile: Profile, key: str) -> Path:
    path = profile_path(directory, profile.user, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.to_json(), encoding="utf-8")
    return path


def list_users(directory: str | Path) -> list[str]:
    d = Path(directory)
    return sorted({p.stem.split(SEP)[0] for p in d.glob("*.json")}) if d.exists() else []
