"""This application accepts the single supported Touch349 V2 target."""
TARGET_HARDWARE = "waveshare-touch-lcd-3.49-v2"

def require_touch349(state):
    if not isinstance(state, dict) or not state.get("ok"):
        raise RuntimeError("设备状态读取失败。")
    if state.get("hardware") != TARGET_HARDWARE:
        raise RuntimeError("当前设备不是 3.49 英寸 V2 触屏，请在设置中指定正确的蓝牙地址。")
