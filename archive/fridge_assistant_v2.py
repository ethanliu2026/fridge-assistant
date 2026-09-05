import gi
gi.require_version("Gst", "1.0")
import cv2
import hailo
import subprocess
import time
import threading
import tkinter as tk
from gi.repository import Gst
from gpiozero import Button
from adafruit_servokit import ServoKit
from hailo_apps.python.pipeline_apps.detection.detection_pipeline import GStreamerDetectionApp
from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer
from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class

hailo_logger = get_logger(__name__)

# ─── FOOD CLASSES ─────────────────────────────────────────────────
FOOD_CLASSES = {
    "banana", "apple", "sandwich", "orange", "broccoli",
    "carrot", "hot dog", "pizza", "donut", "cake",
    "bottle", "cup", "bowl",
    # Custom trained classes
    "egg", "potato", "tomato", "onion", "garlic", "lettuce", "pasta"
}

# ─── SERVO SETUP ──────────────────────────────────────────────────
kit = ServoKit(channels=16)
for ch in range(4):
    kit.servo[ch].set_pulse_width_range(600, 2400)
    time.sleep(0.5)

# Calibrated positions
PARKED   = [80, 90, 100, 90]
DEPLOYED = [150, 70, 30, 110]

pos = PARKED[:]

def set_servo(ch, angle):
    angle = max(0, min(180, int(angle)))
    for attempt in range(3):
        try:
            kit.servo[ch].angle = angle
            return
        except Exception as e:
            print(f"CH{ch} retry {attempt+1}: {e}")
            time.sleep(0.3)

def move_joint(channels, targets, steps=15, delay=0.08):
    start = pos[:]
    for i in range(1, steps + 1):
        for ch in channels:
            new = int(start[ch] + (targets[ch] - start[ch]) * i / steps)
            set_servo(ch, new)
            time.sleep(0.04)
        time.sleep(delay)
    for ch in channels:
        pos[ch] = targets[ch]

def deploy_arm():
    print("Deploying arm...")
    display("Deploying arm...", "")
    # Middle joint first
    mid_targets = pos[:]
    mid_targets[1] = DEPLOYED[1]
    mid_targets[3] = DEPLOYED[3]
    move_joint([1, 3], mid_targets)
    time.sleep(0.5)
    # Then base
    base_targets = pos[:]
    base_targets[0] = DEPLOYED[0]
    base_targets[2] = DEPLOYED[2]
    move_joint([0, 2], base_targets)

def retract_arm():
    print("Retracting arm...")
    display("Retracting...", "")
    # Base first
    base_targets = pos[:]
    base_targets[0] = PARKED[0]
    base_targets[2] = PARKED[2]
    move_joint([0, 2], base_targets)
    time.sleep(0.5)
    # Then middle
    mid_targets = pos[:]
    mid_targets[1] = PARKED[1]
    mid_targets[3] = PARKED[3]
    move_joint([1, 3], mid_targets)

# ─── DISPLAY SETUP ────────────────────────────────────────────────
root = tk.Tk()
root.title("Fridge Assistant")
root.configure(bg="black")
root.attributes('-fullscreen', True)

title_label = tk.Label(root, text="🍳 Fridge Assistant", font=("Arial", 40, "bold"),
                        bg="black", fg="white")
title_label.pack(pady=30)

cuisine_label = tk.Label(root, text="Cuisine: Chinese", font=("Arial", 26),
                          bg="black", fg="yellow")
cuisine_label.pack(pady=10)

status_label = tk.Label(root, text="Ready! Select cuisine then press Deploy",
                         font=("Arial", 22), bg="black", fg="green")
status_label.pack(pady=10)

recipe_label = tk.Label(root, text="", font=("Arial", 20),
                         bg="black", fg="white", wraplength=1100, justify="left")
recipe_label.pack(pady=30, padx=40)

def display(line1, line2=""):
    status_label.config(text=line1)
    recipe_label.config(text=line2)
    root.update()

# ─── STATE ────────────────────────────────────────────────────────
cuisine = "Chinese"
arm_down = False
scan_ready = False
detected_items = []

# ─── HAILO CALLBACK ───────────────────────────────────────────────
class FridgeCallbackClass(app_callback_class):
    def __init__(self):
        super().__init__()
        self.detected_items = []
        self.scan_requested = False
        self.cuisine = "Chinese"

user_data = FridgeCallbackClass()

def app_callback(element, buffer, user_data):
    if buffer is None:
        return

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    items = []
    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()
        if label in FOOD_CLASSES and confidence > 0.4:
            items.append(label)

    if items:
        user_data.detected_items = list(set(items))

    if user_data.scan_requested and user_data.detected_items:
        user_data.scan_requested = False
        items_str = ", ".join(user_data.detected_items)
        print(f"Detected: {items_str}")

        def get_recipe():
            display(f"Detected: {items_str}", "Asking Ollama...")
            prompt = f"Ingredients: {items_str}. One {user_data.cuisine} recipe, 2 sentences max."
            try:
                result = subprocess.run(
                    ["ollama", "run", "llama3.2:3b", prompt],
                    capture_output=True, text=True, timeout=60
                )
                recipe = result.stdout.strip()
                display(f"Detected: {items_str}", recipe)
                print(f"Recipe: {recipe}")
            except Exception as e:
                display("Ollama error", str(e)[:50])

        threading.Thread(target=get_recipe, daemon=True).start()

# ─── BUTTON CALLBACKS ─────────────────────────────────────────────
def on_chinese():
    global cuisine
    cuisine = "Chinese"
    user_data.cuisine = "Chinese"
    cuisine_label.config(text="Cuisine: Chinese")
    display("Ready!", "Press Deploy to scan")
    root.update()
    print("Cuisine: Chinese")

def on_italian():
    global cuisine
    cuisine = "Italian"
    user_data.cuisine = "Italian"
    cuisine_label.config(text="Cuisine: Italian")
    display("Ready!", "Press Deploy to scan")
    root.update()
    print("Cuisine: Italian")

def on_japanese():
    global cuisine
    cuisine = "Japanese"
    user_data.cuisine = "Japanese"
    cuisine_label.config(text="Cuisine: Japanese")
    display("Ready!", "Press Deploy to scan")
    root.update()
    print("Cuisine: Japanese")

def on_deploy():
    global arm_down
    if not arm_down:
        arm_down = True
        def deploy():
            deploy_arm()
            time.sleep(1)
            display("Scanning fridge...", "Looking for food...")
            user_data.scan_requested = True
        threading.Thread(target=deploy, daemon=True).start()
    else:
        arm_down = False
        threading.Thread(target=retract_arm, daemon=True).start()

btn_deploy   = Button(17, pull_up=False)
btn_chinese  = Button(27, pull_up=False)
btn_italian  = Button(22, pull_up=False)
btn_japanese = Button(23, pull_up=False)

btn_deploy.when_pressed   = on_deploy
btn_chinese.when_pressed  = on_chinese
btn_italian.when_pressed  = on_italian
btn_japanese.when_pressed = on_japanese

# ─── STARTUP ──────────────────────────────────────────────────────
print("Starting Fridge Assistant...")

# Park arm at startup
for ch in range(4):
    set_servo(ch, PARKED[ch])
    time.sleep(0.5)

display("Fridge Assistant", "Select cuisine then press Deploy")

# ─── RUN HAILO PIPELINE IN BACKGROUND ─────────────────────────────
def run_pipeline():
    import sys
    sys.argv = ["fridge_assistant.py", "--input", "/dev/video0"]
    app = GStreamerDetectionApp(app_callback, user_data)
    app.run()

pipeline_thread = threading.Thread(target=run_pipeline, daemon=True)
pipeline_thread.start()

# ─── MAIN LOOP ────────────────────────────────────────────────────
try:
    root.mainloop()
except KeyboardInterrupt:
    print("\nShutting down...")
    retract_arm()
    root.destroy()
