import cv2
import subprocess
import time
import threading
import tkinter as tk
from gpiozero import Button
from adafruit_servokit import ServoKit
from ultralytics import YOLO

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
title_label.pack(pady=20)

cuisine_label = tk.Label(root, text="Cuisine: Chinese",
                          font=("Arial", 26), bg="black", fg="yellow")
cuisine_label.pack(pady=5)

status_label = tk.Label(root, text="Ready! Select cuisine then press Deploy",
                         font=("Arial", 22), bg="black", fg="green")
status_label.pack(pady=5)

detected_label = tk.Label(root, text="", font=("Arial", 20),
                           bg="black", fg="cyan")
detected_label.pack(pady=5)

recipe_label = tk.Label(root, text="", font=("Arial", 18),
                         bg="black", fg="white", wraplength=1100, justify="left")
recipe_label.pack(pady=20, padx=40)

hint_label = tk.Label(root, text="", font=("Arial", 16, "italic"),
                       bg="black", fg="gray")
hint_label.pack(pady=5)

def display(status, recipe="", detected="", hint=""):
    status_label.config(text=status)
    recipe_label.config(text=recipe)
    detected_label.config(text=detected)
    hint_label.config(text=hint)
    root.update()

# ─── STATE ────────────────────────────────────────────────────────
# States: IDLE → DEPLOYED → RECIPE → IDLE
# IDLE: arm up, waiting
# DEPLOYED: arm down, scanning, showing detections live
# RECIPE: locked items, showing recipe

STATE_IDLE     = 0
STATE_DEPLOYED = 1
STATE_RECIPE   = 2

state = STATE_IDLE

class UserData:
    def __init__(self):
        self.live_items = []       # what camera sees right now
        self.locked_items = []     # items locked when user confirms
        self.cuisine = "Chinese"

user_data = UserData()

# ─── DETECTION LOOP ───────────────────────────────────────────────
def run_detection():
    model = YOLO('/home/stussy/Downloads/best.onnx', task='detect')
    cap = cv2.VideoCapture(0)
    FOOD_CLASSES = {
        "cabbage", "carrot", "chicken", "cucumber", "egg",
        "garlic", "mushroom", "onion", "potato", "zucchini"
    }

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        if state == STATE_DEPLOYED:
            results = model(frame, verbose=False)
            items = []
            for r in results:
                for box in r.boxes:
                    label = model.names[int(box.cls)]
                    conf = float(box.conf)
                    if conf > 0.35 and label in FOOD_CLASSES:
                        items.append(label)

            if items:
                user_data.live_items = list(set(items))
                items_str = ", ".join(sorted(user_data.live_items))
                detected_label.config(text=f"Detected: {items_str}")
                root.update()

        time.sleep(0.05)

# ─── OLLAMA ───────────────────────────────────────────────────────
def get_recipe():
    items_str = ", ".join(sorted(user_data.locked_items))
    display(
        "Generating recipe...",
        detected=f"Locked: {items_str}",
        hint="Please wait..."
    )
    prompt = (
        f"I found these ingredients in my fridge: {items_str}. "
        f"Suggest ONE simple {user_data.cuisine} recipe I could make. "
        f"In 3 sentences: name the dish, briefly describe how to make it, "
        f"then list any extra ingredients I would need."
    )
    try:
        result = subprocess.run(
            ["ollama", "run", "llama3.2:3b", prompt],
            capture_output=True, text=True, timeout=120
        )
        recipe = result.stdout.strip()
        display(
            f"{user_data.cuisine} Recipe",
            recipe=recipe,
            detected=f"Ingredients: {items_str}",
            hint="Press Deploy to retract arm"
        )
        print(f"Recipe: {recipe}")
    except Exception as e:
        display("Ollama error", recipe=str(e)[:80])

# ─── BUTTON CALLBACKS ─────────────────────────────────────────────
def on_deploy():
    global state

    if state == STATE_IDLE:
        # Deploy arm, start scanning
        state = STATE_DEPLOYED
        user_data.live_items = []
        def do_deploy():
            deploy_arm()
            display(
                "Scanning fridge...",
                detected="Looking for food...",
                hint="Press Deploy again to lock items & get recipe"
            )
        threading.Thread(target=do_deploy, daemon=True).start()

    elif state == STATE_DEPLOYED:
        # Lock items and generate recipe
        if not user_data.live_items:
            display("No food detected!", hint="Try adjusting camera angle")
            return
        state = STATE_RECIPE
        user_data.locked_items = user_data.live_items[:]
        threading.Thread(target=get_recipe, daemon=True).start()

    elif state == STATE_RECIPE:
        # Retract arm, go back to idle
        state = STATE_IDLE
        user_data.live_items = []
        user_data.locked_items = []
        def do_retract():
            retract_arm()
            display(
                "Ready!",
                detected="",
                hint="Select cuisine then press Deploy"
            )
        threading.Thread(target=do_retract, daemon=True).start()

def on_chinese():
    user_data.cuisine = "Chinese"
    cuisine_label.config(text="Cuisine: Chinese")
    root.update()

def on_italian():
    user_data.cuisine = "Italian"
    cuisine_label.config(text="Cuisine: Italian")
    root.update()

def on_japanese():
    user_data.cuisine = "Japanese"
    cuisine_label.config(text="Cuisine: Japanese")
    root.update()

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

display("Fridge Assistant", hint="Select cuisine then press Deploy")

detection_thread = threading.Thread(target=run_detection, daemon=True)
detection_thread.start()

# ─── MAIN LOOP ────────────────────────────────────────────────────
try:
    root.mainloop()
except KeyboardInterrupt:
    print("\nShutting down...")
    retract_arm()
    root.destroy()
