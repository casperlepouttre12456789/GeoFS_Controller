import usb_hid

# Tells the RP2040 chip to turn into a Keyboard AND a Gamepad Controller on boot
usb_hid.enable((usb_hid.Device.KEYBOARD, usb_hid.Device.GAMEPAD))