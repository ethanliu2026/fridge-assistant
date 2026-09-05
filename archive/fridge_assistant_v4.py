import cv2
import subprocess
import time
import threading
import tkinter as tk
from gpiozero import Button
from adafruit_servokit import ServoKit
from ultralytics import YOLO

# ─── FOOD CLASSES ─────────────────────────────────────────────────
FOOD_CLASSES = {
    "cabbage", "carrot", "chicken", "cucumber", "egg",
    "garlic", "mushroom", "onion", "potato", "zucchini"
}

# ─── SERVO SETUP ──────────────────────────────────────────────────
kit = ServoKit(channels=16)
for ch in [1, 2, 3, 4]:
    kit.servo[ch].set_pulse_width_range(600, 2400)
    time.sleep(0.5)

CHANNELS = [4, 1, 2, 3]
PARKED   = [65, 125, 97, 20]
DEPLOYED = [115, 75, 47, 70]

pos = PARKED[:]

def set_servo(ch, angle):
    angle = max(0, min(180, int(angle)))
    for attempt in range(4):
        try:
            kit.servo[ch].angle = angle
            return
        except Exception as e:
            print(f"CH{ch} retry {attempt+1}: {e}")
            time.sleep(0.3)

def move_joint(indices, targets, steps=15, delay=0.08):
    start = pos[:]
    for i in range(1, steps + 1):
        for idx in indices:
            new = int(start[idx] + (targets[idx] - start[idx]) * i / steps)
            new = max(0, min(180, new))
            set_servo(CHANNELS[idx], new)
            time.sleep(0.04)
        time.sleep(delay)
    for idx in indices:
        pos[idx] = targets[idx]

def deploy_arm():
    print("Deploying arm...")
    display("Deploying arm...", "")
    mid_targets = pos[:]
    mid_targets[1] = DEPLOYED[1]
    mid_targets[3] = DEPLOYED[3]
    move_joint([1, 3], mid_targets)
    time.sleep(0.5)
    base_targets = pos[:]
    base_targets[0] = DEPLOYED[0]
    base_targets[2] = DEPLOYED[2]
    move_joint([0, 2], base_targets)

def retract_arm():
    print("Retracting arm...")
    display("Retracting...", "")
    base_targets = pos[:]
    base_targets[0] = PARKED[0]
    base_targets[2] = PARKED[2]
    move_joint([0, 2], base_targets)
    time.sleep(0.5)
    mid_targets = pos[:]
    mid_targets[1] = PARKED[1]
    mid_targets[3] = PARKED[3]
    move_joint([1, 3], mid_targets)

# ─── DISPLAY SETUP ────────────────────────────────────────────────
root = tk.Tk()
root.title("Fridge Assistant")
root.configure(bg="black")
root.attributes('-fullscreen', True)

title_label = tk.Label(root, text="Fridge Assistant",
                        font=("Arial", 40, "bold"), bg="black", fg="white")
title_label.pack(pady=30)

cuisine_label = tk.Label(root, text="Cuisine: Chinese",
                          font=("Arial", 26), bg="black", fg="yellow")
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
arm_down = False

class UserData:
    def __init__(self):
        self.detected_items = []
        self.scan_requested = False
        self.cuisine = "Chinese"

user_data = UserData()

# ─── DETECTION + OLLAMA ───────────────────────────────────────────
def run_detection():
    model = YOLO('/home/stussy/Downloads/best.onnx', task='detect')
    cap = cv2.VideoCapture(0)

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        results = model(frame, verbose=False)
        items = []
        for r in results:
            for box in r.boxes:
                label = model.names[int(box.cls)]
                conf = float(box.conf)
                if conf > 0.4 and label in FOOD_CLASSES:
                    items.append(label)

        if items:
            user_data.detected_items = list(set(items))
            items_str = ", ".join(user_data.detected_items)
            status_label.config(text=f"Found: {items_str}")
            root.update()

        if user_data.scan_requested and user_data.detected_items:
            user_data.scan_requested = False
            items_str = ", ".join(user_data.detected_items)

            def get_recipe(items_str=items_str):
                display(f"Found: {items_str}", "Generating recipe...")
                prompt = (
                    f"I found these ingredients in my fridge: {items_str}. "
                    f"Suggest ONE simple {user_data.cuisine} recipe I could make. "
                    f"In 3 sentences max: name the dish, briefly describe it, "
                    f"then list any extra ingredients I would need to complete it."
                )
                try:
                    result = subprocess.run(
                        ["ollama", "run", "llama3.2:3b", prompt],
                        capture_output=True, text=True, timeout=60
                    )
                    recipe = result.stdout.strip()
                    display(f"Found: {items_str}", recipe)
                    print(f"Recipe: {recipe}")
                except Exception as e:
                    display("Ollama error", str(e)[:50])

            threading.Thread(target=get_recipe, daemon=True).start()

        time.sleep(0.05)

# ─── BUTTON CALLBACKS ─────────────────────────────────────────────
def on_chinese():
    user_data.cuisine = "Chinese"
    cuisine_label.config(text="Cuisine: Chinese")
    display("Ready!", "Press Deploy to scan")
    root.update()

def on_italian():
    user_data.cuisine = "Italian"
    cuisine_label.config(text="Cuisine: Italian")
    display("Ready!", "Press Deploy to scan")
    root.update()

def on_japanese():
    user_data.cuisine = "Japanese"
    cuisine_label.config(text="Cuisine: Japanese")
    display("Ready!", "Press Deploy to scan")
    root.update()

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

for i, ch in enumerate(CHANNELS):
    set_servo(ch, PARKED[i])
    time.sleep(1.5)

display("Fridge Assistant", "Select cuisine then press Deploy")

detection_thread = threading.Thread(target=run_detection, daemon=True)
detection_thread.start()

# ─── MAIN LOOP ────────────────────────────────────────────────────
try:
    root.mainloop()
except KeyboardInterrupt:
    print("\nShutting down...")
    retract_arm()
    root.destroy()
