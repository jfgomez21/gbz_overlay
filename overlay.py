#!/usr/bin/python3
# @ made by d-rez / dark_skeleton
# Requires:
# - ADS1015 with Vbat on A0
# - pngview
# - a symbolic link to ic_battery_alert_red_white_36dp.png under
#   material_design_icons_master/device/drawable-mdpi/
# - an entry in crontab
# - material_design_icons_master github clone
# - some calibration, there's a lot of jitter
# - code comments. someday...

import time
import Adafruit_ADS1x15
import subprocess
import os
import re
import logging
import logging.handlers
import math
import time

from datetime import datetime
from statistics import median
from collections import deque
from enum import Enum

pngview_path="/usr/local/bin/pngview"
pngview_call=[pngview_path, "-d", "0", "-b", "0x0000", "-n", "-l", "15000", "-y", "0", "-x"]

iconpath = os.path.dirname(os.path.realpath(__file__)) + "/overlay_icons"
logfile = os.path.dirname(os.path.realpath(__file__)) + "/overlay.log"
dpi=36

env_icons = {
	"under-voltage": iconpath + "/flash.png",
	"freq-capped":   iconpath + "/thermometer.png",
	"throttled":     iconpath + "/thermometer-lines.png"
}
wifi_icons = {
	"connected": iconpath + "/ic_network_wifi_white_"      + str(dpi) + "dp.png",
	"disabled":  iconpath + "/ic_signal_wifi_off_white_"   + str(dpi) + "dp.png",
	"enabled":   iconpath + "/ic_signal_wifi_0_bar_white_" + str(dpi) + "dp.png"
}
bt_icons = {
	"enabled":   iconpath + "/ic_bluetooth_white_"           + str(dpi) + "dp.png",
	"connected": iconpath + "/ic_bluetooth_connected_white_" + str(dpi) + "dp.png",
	"disabled":  iconpath + "/ic_bluetooth_disabled_white_"  + str(dpi) + "dp.png"
}

#icon positions starting from the right
icon_indexes = {
	"battery" : 0,
	"wifi" : 1,
	"bluetooth" : 2,
	"under_voltage" : 3,
	"freq_capped" : 4,
	"throttled" : 5
}

wifi_carrier = "/sys/class/net/wlan0/carrier" # 1 when wifi connected, 0 when disconnected and/or ifdown
wifi_linkmode = "/sys/class/net/wlan0/link_mode" # 1 when ifup, 0 when ifdown
bt_devices_dir="/sys/class/bluetooth"
env_cmd="vcgencmd get_throttled"

fbfile="tvservice -s"

ADC_RESOLUTION = pow(2, 15) - 1
ADC_MAX_VOLTAGE = 6.144
R1 = 10000
R2 = 10000

BATTERY_MAX_VOLTAGE = 4.2
BATTERY_INPUT_PIN = 0
CHARGER_INPUT_PIN = 3

CHARGER_LOW_MIN_VOLTAGE = 0.4
CHARGER_LOW_MAX_VOLTAGE = 1

LOW_VOLTAGE_THRESHOLD = 3.3

class ChargerState(Enum) :
	STANDBY = 0
	CHARGING = 1
	CHARGE_COMPLETE = 2

class InterfaceState(Enum):
	DISABLED = 0
	ENABLED = 1
	CONNECTED = 2

adc = Adafruit_ADS1x15.ADS1115()
overlay_processes = {}
wifi_state = None
wifi_timestamp = 0
bt_state = None
bt_timestamp = 0
battery_level = None
env = None
battery_history = deque(maxlen=5)
battery_timestamp = 0
battery_visible = False
charger_state = None

# Set up logging
my_logger = logging.getLogger('gbzOverlay')
my_logger.setLevel(logging.INFO)

my_logger.addHandler(logging.handlers.RotatingFileHandler(logfile, maxBytes=102400, backupCount=1))
my_logger.addHandler(logging.StreamHandler())

# Get Framebuffer resolution
resolution = re.search("(\d{3,}x\d{3,})", subprocess.check_output(fbfile.split()).decode().rstrip()).group().split('x')
my_logger.info("resolution - %sx%s" % (resolution[0], resolution[1]))

def start_process(name, path):
    global overlay_processes

    index = icon_indexes[name] + 1

    overlay_processes[name] = subprocess.Popen(pngview_call + [str(int(resolution[0]) - (dpi * index)), path])

def end_process(name):
    global overlay_processes

    if name in overlay_processes:
        overlay_processes[name].kill()
        del overlay_processes[name]

def contains_process(name):
    return name in overlay_processes

def translate_bat(voltage, state):
    if state == ChargerState.CHARGE_COMPLETE:
	    return "charging_full"

    if voltage < LOW_VOLTAGE_THRESHOLD:
        return "alert_red"

    value = (voltage - LOW_VOLTAGE_THRESHOLD) / (BATTERY_MAX_VOLTAGE - LOW_VOLTAGE_THRESHOLD)
    value = math.floor(int(value * 100) / 10) * 10

    if state == ChargerState.STANDBY:
        if value < 20:
            return "alert"

        return value;

    if value < 20:
        value = 20

    return "charging_%d" % value

def wifi():
    global wifi_state, overlay_processes, wifi_timestamp

    new_wifi_state = InterfaceState.DISABLED

    try:
        f = open(wifi_carrier, "r")
        carrier_state = int(f.read().rstrip())
        f.close()

        if carrier_state == 1:
	    # ifup and connected to AP
            new_wifi_state = InterfaceState.CONNECTED
        elif carrier_state == 0:
            f = open(wifi_linkmode, "r")
            linkmode_state = int(f.read().rstrip())
            f.close()

            if linkmode_state == 1:
                # ifup but not connected to any network
                new_wifi_state = InterfaceState.ENABLED
                # else - must be ifdown
    except IOError:
        pass

    if new_wifi_state != wifi_state:
        end_process("wifi")
        start_process("wifi", wifi_icons[new_wifi_state.name.lower()])

        wifi_timestamp = time.time()
    else:
        if time.time() - wifi_timestamp > 10:
            end_process("wifi")

    return new_wifi_state

def bluetooth():
    global bt_state, overlay_processes, bluetooth_timestamp

    new_bt_state = InterfaceState.DISABLED

    try:
        p1 = subprocess.Popen('hciconfig', stdout = subprocess.PIPE)
        p2 = subprocess.Popen(['awk', 'FNR == 3 {print tolower($1)}'], stdin = p1.stdout, stdout=subprocess.PIPE)
        state = p2.communicate()[0].decode().rstrip()

        if state == "up":
            new_bt_state = InterfaceState.ENABLED
    except IOError:
        pass

    try:
        devices=os.listdir(bt_devices_dir)

        if len(devices) > 1:
            new_bt_state = InterfaceState.CONNECTED
    except OSError:
        pass

    if new_bt_state != bt_state:
        end_process("bluetooth")

        start_process("bluetooth", bt_icons[new_bt_state.name.lower()])
        bluetooth_timestamp = time.time()
    else:
        if time.time() - bluetooth_timestamp > 10:
            end_process("bluetooth")

    return new_bt_state

def environment():
    val = int(re.search("throttled=(0x\d+)", subprocess.check_output(env_cmd.split()).decode().rstrip()).groups()[0], 16)
    env = {
          "under-voltage": bool(val & 0x01),
          "freq-capped": bool(val & 0x02),
          "throttled": bool(val & 0x04)
    }

    for k,v in env.items():
        if v and not contains_process(k):
            start_process(k, env_icons[k]) 
        elif not v and k in overlay_processes:
            end_process(k)

    return val

def read_voltage(pin):
    value = adc.read_adc(pin, gain=2/3)
    value = (value * ADC_MAX_VOLTAGE) / ADC_RESOLUTION

    return (value * (R1 + R2)) / R2

def read_charger():
    state = ChargerState.CHARGE_COMPLETE
    value = read_voltage(CHARGER_INPUT_PIN)

    if value < CHARGER_LOW_MIN_VOLTAGE:
        state = ChargerState.STANDBY
    elif value >= CHARGER_LOW_MIN_VOLTAGE and value <= CHARGER_LOW_MAX_VOLTAGE:
        state = ChargerState.CHARGING

    return (state, value)

def battery():
    global battery_history, battery_level, charger_state, battery_visible, battery_timestamp

    value_v = read_voltage(BATTERY_INPUT_PIN)
    (charger_s, charger_v) = read_charger();

    battery_history.append(value_v)

    level_icon = translate_bat(median(battery_history), charger_s)
    path = "%s/ic_battery_%s_white_%ddp.png" % (iconpath, level_icon, dpi)

    #display the battery if the charger state has changed
    #display the battery if the battery level is alert or alert_red
    #hide the battery after X seconds
    if charger_s != charger_state:
        end_process("battery")

        start_process("battery", path)

        battery_level = level_icon
        charger_state = charger_s
        battery_visible = True
        battery_timestamp = time.time()
    elif level_icon in ["alert", "alert_red"]:
        if level_icon != battery_level:
            end_process("battery")

            start_process("battery", path);

            battery_level = level_icon
            battery_visible = True
            battery_timestamp = time.time()

        if level_icon == "alert_red":
            if time.time() - battery_timestamp > 5:
                if battery_visible:
                    end_process("battery")
                else:
                    start_process("battery", path)

                battery_visible = not battery_visible
                battery_timestamp = time.time()
    elif battery_visible:
        if time.time() - battery_timestamp > 10:
            end_process("battery")
            battery_visible = False

    return (level_icon, value_v, charger_s, charger_v)

while True:
    (battery_level, value_v, charger_s, charger_v) = battery()
    wifi_state = wifi()
    bt_state = bluetooth()
    env = environment()

    """
    my_logger.info("%s,battery: %.2f,charger_state: %s, charger: %.2f,icon: %s,wifi: %s,bt: %s, throttle: %#0x" % (
        datetime.now(),
        value_v,
        charger_s,
        charger_v,
        battery_level,
        wifi_state.name,
        bt_state.name,
        env
    ))
    """
    time.sleep(2)
