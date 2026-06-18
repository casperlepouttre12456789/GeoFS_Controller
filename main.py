import board
import analogio
import usb_hid
import digitalio
from kmk.kmk_keyboard import KMKKeyboard
from kmk.keys import KC
from kmk.scanners.digital import DirectPinScanner
from kmk.modules import Module

# Initialize core KMK engine
keyboard = KMKKeyboard()

# ==========================================
# 1. PHYSICAL HARDWARE PIN MAPPING
# ==========================================
# Directly mapped from your schematic. Buttons switch directly to GND.
keyboard.matrix = DirectPinScanner(
    pins=(
        board.D0,  # SW1 (Landing Gear Toggle)
        board.D1,  # SW2 (Flaps Toggle)
        board.D2,  # SW3 (Airbrake Toggle)
        board.D3,  # SW4 (Wheel Brake - Momentary Spacebar)
        board.D4,  # SW5 (Trim Mode Activation Toggle)
    )
)
keyboard.matrix.value_when_pressed = False 

# Transparent mapping; button execution is intercepted and handled below
keyboard.keymap = [[KC.TRNS, KC.TRNS, KC.TRNS, KC.TRNS, KC.TRNS]]

# ==========================================
# 2. GEOFS ADVANCED INPUT MODULE
# ==========================================
class GeoFSFlightManagement(Module):
    def __init__(self):
        # Analog Joystick Pins
        self.joy_right_x = analogio.AnalogIn(board.A2) # Roll / Ailerons
        self.joy_right_y = analogio.AnalogIn(board.A3) # Pitch / Elevator
        self.joy_left_x  = analogio.AnalogIn(board.A0) # Rudder
        self.joy_left_y  = analogio.AnalogIn(board.A1) # Throttle
        
        # LED Pins
        self.led_gear = digitalio.DigitalInOut(board.D6)   # D1
        self.led_gear.direction = digitalio.Direction.OUTPUT
        self.led_brake = digitalio.DigitalInOut(board.D7)  # D2
        self.led_brake.direction = digitalio.Direction.OUTPUT

        # Simulation States
        self.gear_down = False
        self.airbrake_deployed = False
        self.trim_mode_active = False
        
        # Trim Offset Coordinates
        self.pitch_trim = 0
        self.roll_trim = 0
        
        # Edge-detection logic states
        self.last_throttle_zone = -1
        self.last_rudder_zone = 0  # -1 = Left, 0 = Centered, 1 = Right
        self.last_buttons = [False, False, False, False, False]

        # Grab core HID devices from system descriptor list
        self.keyboard_device = None
        self.mouse_device = None
        for dev in usb_hid.devices:
            if dev.usage == 0x06 and dev.usage_page == 0x01: # Keyboard
                self.keyboard_device = dev
            if dev.usage == 0x02 and dev.usage_page == 0x01: # Mouse
                self.mouse_device = dev

    def send_key(self, key_code):
        """Types a keyboard key once cleanly"""
        if self.keyboard_device:
            try:
                self.keyboard_device.send_report(bytearray([0, 0, key_code, 0, 0, 0, 0, 0]))
                self.keyboard_device.send_report(bytearray(8))
            except OSError:
                pass

    def set_key_hold(self, key_code, hold):
        """Holds down or releases a keyboard key"""
        if self.keyboard_device:
            try:
                if hold:
                    self.keyboard_device.send_report(bytearray([0, 0, key_code, 0, 0, 0, 0, 0]))
                else:
                    self.keyboard_device.send_report(bytearray(8))
            except OSError:
                pass

    def send_mouse_move(self, x, y):
        """Sends relative mouse movement bytes to the PC (-127 to 127)"""
        if self.mouse_device:
            try:
                self.mouse_device.send_report(bytearray([0, x & 0xFF, y & 0xFF, 0]))
            except OSError:
                pass

    def before_matrix_scan(self, keyboard):
        current_buttons = [not state for state in keyboard.matrix.scan()]
        if len(current_buttons) < 5:
            return

        # ---- BUTTON ACTIONS ----
        if current_buttons[0] and not self.last_buttons[0]:
            self.gear_down = not self.gear_down
            self.send_key(0x0A) # 'g' key
            self.led_gear.value = self.gear_down

        if current_buttons[1] and not self.last_buttons[1]:
            self.send_key(0x09) # 'f' key

        if current_buttons[2] and not self.last_buttons[2]:
            self.airbrake_deployed = not self.airbrake_deployed
            self.send_key(0x0B) # 'h' key
            self.led_brake.value = self.airbrake_deployed

        if current_buttons[3] != self.last_buttons[3]:
            self.set_key_hold(0x2C, current_buttons[3]) # Spacebar

        if current_buttons[4] and not self.last_buttons[4]:
            self.trim_mode_active = not self.trim_mode_active

        self.last_buttons = current_buttons

        # ---- THROTTLE LOGIC (Keys 0-9) ----
        # Invert axis so pushing stick forward increases power
        raw_throttle = 65535 - self.joy_left_y.value
        current_throttle_zone = int((raw_throttle / 65536) * 10)
        current_throttle_zone = max(0, min(9, current_throttle_zone))

        if current_throttle_zone != self.last_throttle_zone:
            if current_throttle_zone == 0:
                self.send_key(0x27) # 0 Key
            else:
                self.send_key(0x1E + (current_throttle_zone - 1)) # 1-9 Keys
            self.last_throttle_zone = current_throttle_zone

        # ---- RUDDER LOGIC (Left Stick X) ----
        j_left_x_val = int((self.joy_left_x.value / 65535) * 200) - 100
        
        if j_left_x_val < -40:   # Left Deflection
            if self.last_rudder_zone != -1:
                self.send_key(0x62) # Numpad 0 
                self.last_rudder_zone = -1
        elif j_left_x_val > 40:  # Right Deflection
            if self.last_rudder_zone != 1:
                self.send_key(0x58) # Numpad Enter / Enter
                self.last_rudder_zone = 1
        else:                     # Neutral Center
            if self.last_rudder_zone != 0:
                self.send_key(0x67) # Numpad 5
                self.last_rudder_zone = 0

        # ---- FLIGHT STICK MOUSE EMULATION (Right Stick) ----
        rx = int((self.joy_right_x.value / 65535) * 255) - 128
        ry = int((self.joy_right_y.value / 65535) * 255) - 128

        # Mechanical deadzone calculation
        if abs(rx) < 15: rx = 0
        if abs(ry) < 15: ry = 0

        if self.trim_mode_active:
            # Shift Layer: Inputs permanently creep the trim configuration offset parameters
            if ry != 0: self.pitch_trim += 1 if ry > 0 else -1
            if rx != 0: self.roll_trim += 1 if rx > 0 else -1
            self.pitch_trim = max(-50, min(50, self.pitch_trim))
            self.roll_trim = max(-50, min(50, self.roll_trim))
        else:
            # Normal Flying Mode: Translate deflection rate + trim into a relative vector
            if rx != 0 or ry != 0:
                move_x = int((rx / 12)) + self.roll_trim
                move_y = int((ry / 12)) + self.pitch_trim
                self.send_mouse_move(move_x, move_y)
            else:
                # Active software alignment stabilization returns center
                self.send_mouse_move(0, 0) 

    def after_matrix_scan(self, keyboard): return
    def before_hid_send(self, keyboard): return
    def after_hid_send(self, keyboard): return

keyboard.modules.append(GeoFSFlightManagement())

if __name__ == "__main__":
    keyboard.go()