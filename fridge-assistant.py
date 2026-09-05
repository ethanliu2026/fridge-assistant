import cv2
import subprocess
import time
import threading
import tkinter as tk
from PIL import Image, ImageTk
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
DEPLOYED = [115, 45, 47, 100]

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
    display("Deploying arm...")
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
    display("Retracting...")
    # Middle first
    mid_targets = pos[:]
    mid_targets[1] = PARKED[1]
    mid_targets[3] = PARKED[3]
    move_joint([1, 3], mid_targets)
    time.sleep(1)
    # Then base
    base_targets = pos[:]
    base_targets[0] = PARKED[0]
    base_targets[2] = PARKED[2]
    move_joint([0, 2], base_targets)

# ─── DISPLAY SETUP ────────────────────────────────────────────────
root = tk.Tk()
root.title("Fridge Assistant")
root.configure(bg="black")
root.attributes('-fullscreen', True)
root.update()
SW = root.winfo_width()
SH = root.winfo_height()

# Left panel — camera feed
cam_label = tk.Label(root, bg="black")
cam_label.place(x=0, y=0, width=SW//2, height=SH)

# Right panel — info
right = tk.Frame(root, bg="black")
right.place(x=SW//2, y=0, width=SW//2, height=SH)

tk.Label(right, text="Fridge Assistant", font=("Arial", 32, "bold"),
         bg="black", fg="white").pack(pady=20)

cuisine_label = tk.Label(right, text="Cuisine: Chinese",
                          font=("Arial", 22), bg="black", fg="yellow")
cuisine_label.pack(pady=5)

status_label = tk.Label(right, text="Select cuisine → Deploy",
                         font=("Arial", 18), bg="black", fg="green")
status_label.pack(pady=5)

detected_label = tk.Label(right, text="", font=("Arial", 16),
                           bg="black", fg="cyan", wraplength=SW//2 - 40)
detected_label.pack(pady=5)

recipe_label = tk.Label(right, text="", font=("Arial", 15),
                         bg="black", fg="white", wraplength=SW//2 - 40, justify="left")
recipe_label.pack(pady=10, padx=20)

hint_label = tk.Label(right, text="", font=("Arial", 14, "italic"),
                       bg="black", fg="gray")
hint_label.pack(pady=5)

# Quit button
quit_btn = tk.Button(root, text="✕  Quit", font=("Arial", 14),
                      bg="#333333", fg="white", relief="flat",
                      command=lambda: quit_program())
quit_btn.place(x=20, y=SH-60, width=150, height=40)

def display(status="", recipe="", detected="", hint=""):
    status_label.config(text=status)
    recipe_label.config(text=recipe)
    detected_label.config(text=detected)
    hint_label.config(text=hint)
    root.update()

def quit_program():
    print("Quitting...")
    retract_arm()
    root.destroy()

# ─── STATE ────────────────────────────────────────────────────────
STATE_IDLE     = 0
STATE_DEPLOYED = 1
STATE_RECIPE   = 2
state = STATE_IDLE

class UserData:
    def __init__(self):
        self.live_items = []
        self.locked_items = []
        self.cuisine = "Chinese"
        self.latest_frame = None

user_data = UserData()

# ─── CAMERA FEED UPDATE ───────────────────────────────────────────
def update_camera():
    if user_data.latest_frame is not None:
        frame = user_data.latest_frame.copy()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = frame.shape[:2]
        # Scale to fit left panel
        panel_w = SW // 2
        panel_h = SH
        scale = min(panel_w / w, panel_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        frame = cv2.resize(frame, (new_w, new_h))
        img = ImageTk.PhotoImage(Image.fromarray(frame))
        cam_label.config(image=img)
        cam_label.image = img
    root.after(50, update_camera)

# ─── DETECTION LOOP ───────────────────────────────────────────────
def run_camera():
    time.sleep(2)  # wait for camera to initialize
    cap = cv2.VideoCapture(0)
    time.sleep(2)  # wait after opening
    while True:
        ret, frame = cap.read()
        frame = cv2.flip(frame, -1)  # 0 = vertical flip only
        if ret:
            user_data.latest_frame = frame
        time.sleep(0.03)

def run_detection():
    """Runs detection separately at slower rate"""
    model = YOLO('best.onnx', task='detect')
    FOOD_CLASSES = {
        "cabbage", "carrot", "chicken", "cucumber", "egg",
        "garlic", "mushroom", "onion", "potato", "zucchini"
    }
    while True:
        if state == STATE_DEPLOYED and user_data.latest_frame is not None:
            frame = user_data.latest_frame.copy()
            results = model(frame, verbose=False)
            items = []
            for r in results:
                for box in r.boxes:
                    label = model.names[int(box.cls)]
                    conf = float(box.conf)
                    if conf > 0.35 and label in FOOD_CLASSES:
                        items.append(label)
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        cv2.rectangle(user_data.latest_frame, (x1,y1), (x2,y2), (0,255,255), 2)
                        cv2.putText(user_data.latest_frame, f"{label} {conf:.2f}",
                                    (x1, y1-8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,255), 2)
            if items:
                user_data.live_items = list(set(user_data.live_items + items))
                items_str = ", ".join(sorted(user_data.live_items))
                detected_label.config(text=f"Detected: {items_str}")
        time.sleep(1.0)  # run detection once per second

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
        import re
        recipe = re.sub(r'\x1b\[[0-9;]*m', '', recipe).strip()
        display(
            f"{user_data.cuisine} Recipe",
            recipe=recipe,
            detected=f"Ingredients: {items_str}",
            hint="Press Deploy to retract arm"
        )
    except Exception as e:
        display("Ollama error", recipe=str(e)[:80])

# ─── BUTTON CALLBACKS ─────────────────────────────────────────────
def on_deploy():
    global state

    if state == STATE_IDLE:
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
        if not user_data.live_items:
            display("No food detected!", hint="Try adjusting camera angle")
            return
        state = STATE_RECIPE
        user_data.locked_items = user_data.live_items[:]
        threading.Thread(target=get_recipe, daemon=True).start()

    elif state == STATE_RECIPE:
        state = STATE_IDLE
        user_data.live_items = []
        user_data.locked_items = []
        def do_retract():
            retract_arm()
            display("Ready!", hint="Select cuisine then press Deploy")
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

display("Fridge Assistant ready!", hint="Select cuisine then press Deploy")

threading.Thread(target=run_camera, daemon=True).start()
threading.Thread(target=run_detection, daemon=True).start()

root.after(100, update_camera)

# ─── MAIN LOOP ────────────────────────────────────────────────────
try:
    root.mainloop()
except KeyboardInterrupt:
    print("\nShutting down...")
    retract_arm()
    root.destroy()
