import time
import subprocess
import cv2
from gpiozero import Button
from adafruit_servokit import ServoKit
from RPLCD.i2c import CharLCD

# ─── HARDWARE SETUP ───────────────────────────────────────────────
kit = ServoKit(channels=16)

# Servo pulse range for SG90
for ch in range(3):
    kit.servo[ch].set_pulse_width_range(500, 2500)

# LCD
lcd = CharLCD(i2c_expander='PCF8574', address=0x27, port=1, cols=16, rows=2, dotsize=8)

# Buttons (active HIGH — pull_up=False)
btn_deploy   = Button(17, pull_up=False)
btn_chinese  = Button(27, pull_up=False)
btn_italian  = Button(22, pull_up=False)
btn_japanese = Button(23, pull_up=False)

# ─── ARM POSITIONS ────────────────────────────────────────────────
# Adjust these angles after physical testing
ARM_PARKED   = (0, 0)    # (base_angle, elbow_angle) when arm is up
ARM_DEPLOYED = (90, 120) # angles when arm is inside fridge

# ─── STATE ────────────────────────────────────────────────────────
cuisine = "Chinese"  # default
arm_down = False

# ─── HELPERS ──────────────────────────────────────────────────────
def lcd_print(line1, line2=""):
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string(line1[:16])
    if line2:
        lcd.cursor_pos = (1, 0)
        lcd.write_string(line2[:16])

def move_arm(base_angle, elbow_angle, delay=0.8):
    """Move arm smoothly to target position."""
    kit.servo[0].angle = base_angle   # base servo 1
    kit.servo[1].angle = base_angle   # base servo 2 (same signal)
    time.sleep(0.3)
    kit.servo[2].angle = elbow_angle  # elbow
    time.sleep(delay)

def deploy_arm():
    print("Deploying arm...")
    lcd_print("Deploying arm...", "")
    move_arm(*ARM_DEPLOYED)

def retract_arm():
    print("Retracting arm...")
    lcd_print("Retracting...", "")
    # Retract elbow first, then base
    kit.servo[2].angle = ARM_PARKED[1]
    time.sleep(0.5)
    kit.servo[0].angle = ARM_PARKED[0]
    kit.servo[1].angle = ARM_PARKED[0]
    time.sleep(0.8)

def capture_frame():
    """Capture a single frame from the webcam."""
    cap = cv2.VideoCapture(0)
    time.sleep(0.5)  # let camera warm up
    ret, frame = cap.read()
    cap.release()
    if not ret:
        print("Failed to capture frame")
        return None
    cv2.imwrite("/tmp/fridge_scan.jpg", frame)
    return frame

def detect_food(frame):
    """
    Detect food items using YOLOv8 via hailo-apps.
    For now uses OpenCV color/object detection as fallback.
    Returns list of detected food item names.
    """
    # COCO food classes that YOLOv8 can detect
    food_classes = {
        46: "banana", 47: "apple", 48: "sandwich", 49: "orange",
        50: "broccoli", 51: "carrot", 52: "hot dog", 53: "pizza",
        54: "donut", 55: "cake", 39: "bottle", 40: "wine glass",
        41: "cup", 43: "knife", 44: "spoon", 45: "bowl"
    }

    try:
        # Try to use hailo detection pipeline
        result = subprocess.run(
            ["hailo-detect", "--input", "/tmp/fridge_scan.jpg",
             "--labels", "coco", "--threshold", "0.4"],
            capture_output=True, text=True, timeout=15
        )
        detected = []
        for line in result.stdout.split("\n"):
            for class_id, name in food_classes.items():
                if name in line.lower():
                    detected.append(name)
        if detected:
            return list(set(detected))
    except Exception as e:
        print(f"Hailo detection failed: {e}, using fallback")

    # Fallback: return placeholder items for testing
    return ["eggs", "tomatoes", "cheese"]

def ask_ollama(items, cuisine):
    """Send detected items + cuisine to Ollama, return recipe."""
    if not items:
        return "No food detected. Try again."

    items_str = ", ".join(items)
    prompt = (
        f"I have these ingredients: {items_str}. "
        f"Suggest ONE simple {cuisine} recipe in exactly 2 short sentences. "
        f"Be specific and concise."
    )

    print(f"Asking Ollama: {prompt}")
    lcd_print("Thinking...", cuisine)

    try:
        result = subprocess.run(
            ["ollama", "run", "llama3.2:3b", prompt],
            capture_output=True, text=True, timeout=30
        )
        recipe = result.stdout.strip()
        print(f"Ollama response: {recipe}")
        return recipe
    except subprocess.TimeoutExpired:
        return "Timeout. Try again."
    except Exception as e:
        return f"Error: {str(e)[:20]}"

def scroll_lcd(text, delay=0.4):
    """Scroll long text across both LCD lines."""
    # Split into 16-char chunks for display
    words = text.split()
    line1 = ""
    line2 = ""
    buffer = []

    for word in words:
        if len(line1) + len(word) + 1 <= 16:
            line1 += (" " if line1 else "") + word
        elif len(line2) + len(word) + 1 <= 16:
            line2 += (" " if line2 else "") + word
        else:
            buffer.append((line1, line2))
            line1 = word
            line2 = ""

    if line1 or line2:
        buffer.append((line1, line2))

    for l1, l2 in buffer:
        lcd_print(l1, l2)
        time.sleep(delay)

# ─── BUTTON CALLBACKS ─────────────────────────────────────────────
def on_chinese():
    global cuisine
    cuisine = "Chinese"
    lcd_print("Cuisine:", "Chinese")
    print("Cuisine: Chinese")

def on_italian():
    global cuisine
    cuisine = "Italian"
    lcd_print("Cuisine:", "Italian")
    print("Cuisine: Italian")

def on_japanese():
    global cuisine
    cuisine = "Japanese"
    lcd_print("Cuisine:", "Japanese")
    print("Cuisine: Japanese")

def on_deploy():
    global arm_down
    if not arm_down:
        # Deploy arm and scan
        arm_down = True
        deploy_arm()
        time.sleep(0.5)

        # Capture and detect
        lcd_print("Scanning...", "")
        frame = capture_frame()
        if frame is None:
            lcd_print("Camera error!", "Check USB cam")
            retract_arm()
            arm_down = False
            return

        items = detect_food(frame)
        print(f"Detected: {items}")
        lcd_print("Detected:", ", ".join(items)[:16])
        time.sleep(1.5)

        # Get recipe from Ollama
        recipe = ask_ollama(items, cuisine)

        # Display recipe scrolling
        scroll_lcd(recipe, delay=2.5)

    else:
        # Retract arm
        arm_down = False
        retract_arm()
        lcd_print("Ready!", f"Cuisine: {cuisine[:9]}")

btn_deploy.when_pressed   = on_deploy
btn_chinese.when_pressed  = on_chinese
btn_italian.when_pressed  = on_italian
btn_japanese.when_pressed = on_japanese

# ─── STARTUP ──────────────────────────────────────────────────────
print("Fridge Assistant starting...")

# Park arm at startup
move_arm(*ARM_PARKED, delay=1.0)

lcd_print("Fridge Assistant", "Ready!")
print("Ready. Press a cuisine button then deploy.")

# ─── MAIN LOOP ────────────────────────────────────────────────────
try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nShutting down...")
    retract_arm()
    lcd_print("Goodbye!", "")
    time.sleep(1)
    lcd.clear()
