import sys
import os
# ====== 【修复 OMP 冲突的核心代码】 ======
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# =======================================
import json
import re
import time
import shutil
import ctypes
import subprocess
import webbrowser
# ====== 【新增】：启动前置环境检测 (防闪退机制) ======
def check_windows_dependencies():
    if sys.platform != "win32":
        return
    missing_dlls = []
    # OpenCV(cv2) 等图像识别库强依赖微软 VC++ 2015-2022 运行库
    required_dlls = ["vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll"]
    
    for dll in required_dlls:
        try:
            # 尝试静默加载该运行库，如果系统里没有，就会触发 OSError
            ctypes.WinDLL(dll)
        except OSError:
            missing_dlls.append(dll)
            
    if missing_dlls:
        msg = (
            f"경고: 시스템에 다음 필수 런타임이 없어 프로그램이 종료되거나 이미지 인식이 실패할 가능성이 큽니다.\n\n"
            f"{', '.join(missing_dlls)}\n\n"
            f"Microsoft C++ 런타임이 설치되어 있지 않아 발생하는 문제입니다.\n"
            f"[VC++ 2015-2022] 또는 Microsoft Visual C++ 재배포 패키지를 설치한 뒤 다시 실행하세요.\n\n"
            f"확인을 누르면 강제로 계속 실행합니다. 종료되면 런타임을 먼저 설치하세요."
        )
        # 0x30 = MB_ICONWARNING (黄色警告图标), 0x0 = MB_OK (只有确定按钮)
        ctypes.windll.user32.MessageBoxW(0, msg, "런타임 누락 경고", 0x30 | 0x0)
# 在导入耗性能的大型模块前，第一时间执行拦截检测
check_windows_dependencies()
# ===================================================
# 【极其关键】：必须在任何 UI 库导入之前设置 DPI 感知
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Win 8.1+
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # Win Vista+
    except Exception:
        pass

import customtkinter as ctk
ctk.deactivate_automatic_dpi_awareness()
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)
import cv2
import numpy as np
import pyautogui
import pydirectinput
import requests
from pynput import keyboard
from PIL import Image, ImageGrab
import win32gui
import pickle
import threading



# ==========================================
# --- 路径与资源策略 ---
# assets: 只读内置，禁止本地覆盖
# images: 打包进 exe，启动时若外部无 images 则自动释放；识图优先读外部 images
# ==========================================
def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_internal_dir():
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return get_app_dir()


APP_DIR = get_app_dir()
INTERNAL_DIR = get_internal_dir()
# 【新增 config 目录路径】
CONFIG_DIR = os.path.join(APP_DIR, "config")
USER_CONFIG_FILE = os.path.join(APP_DIR, "config.json")      # <--- 全面替换为 config.json
LOG_FILE = os.path.join(APP_DIR, "bot_log.txt")
USER_IMAGE_CONFIG_FILE = os.path.join(APP_DIR, "userconfig.json")
CACHE_DIR = os.path.join(APP_DIR, "cache")
TEMPLATE_CACHE_FILE = os.path.join(CACHE_DIR, "template_cache.pkl")
TEMPLATE_META_FILE = os.path.join(CACHE_DIR, "template_meta.json")
CURRENT_VERSION = "1.1.6.2"
CURRENT_VERSION_KR = "4.5.1"
def auto_extract_configs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    
    # 向下兼容，自动重命名并迁移老版本 bot_config
    old_configs = [
        os.path.join(APP_DIR, "bot_config.json"),
        os.path.join(APP_DIR, "bot-config.json"),
        os.path.join(CONFIG_DIR, "bot-config.json"),
        os.path.join(CONFIG_DIR, "bot_config.json"),
        os.path.join(CONFIG_DIR, "config.json")
    ]
    for old_path in old_configs:
        if os.path.exists(old_path):
            try:
                if not os.path.exists(USER_CONFIG_FILE):
                    shutil.move(old_path, USER_CONFIG_FILE)
                else:
                    os.remove(old_path)
            except Exception:
                pass
def auto_extract_images(folder_name="images"):
    internal_dir = os.path.join(INTERNAL_DIR, folder_name)
    external_dir = os.path.join(APP_DIR, folder_name)

    if not os.path.isdir(internal_dir):
        print(f"[auto_extract_images] 内置目录不存在: {internal_dir}")
        return

    try:
        os.makedirs(external_dir, exist_ok=True)

        for root, dirs, files in os.walk(internal_dir):
            rel_path = os.path.relpath(root, internal_dir)
            target_root = external_dir if rel_path == "." else os.path.join(external_dir, rel_path)
            os.makedirs(target_root, exist_ok=True)

            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(target_root, file)

                # 只在外部不存在时释放，保留用户自定义替换
                if not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)

    except Exception as e:
        print(f"[auto_extract_images] 释放 images 失败: {e}")


def get_img_path(filename):
    basename = os.path.basename(filename)

    # 优先读取程序目录外部 images（允许用户替换）
    ext_path = os.path.join(APP_DIR, "images", basename)
    if os.path.exists(ext_path):
        return ext_path

    # 外部没有则读取内置 images
    int_path = os.path.join(INTERNAL_DIR, "images", basename)
    if os.path.exists(int_path):
        return int_path

    return filename


def get_asset_path(*parts):
    """
    assets 只允许读取内置资源：
    - 打包后：_MEIPASS/assets
    - 开发环境：项目目录/assets
    """
    asset_path = os.path.join(INTERNAL_DIR, "assets", *parts)
    if os.path.exists(asset_path):
        return asset_path

    dev_asset_path = os.path.join(get_app_dir(), "assets", *parts)
    if os.path.exists(dev_asset_path):
        return dev_asset_path

    return None


def parse_version(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0, 0, 0)

# ==========================================
# --- Ctypes 硬件级键盘模拟结构体定义 ---
# ==========================================
SendInput = ctypes.windll.user32.SendInput
PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class Input_I(ctypes.Union):
    _fields_ = [
        ("ki", KeyBdInput),
        ("mi", MouseInput),
        ("hi", HardwareInput),
    ]


class Input(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", Input_I),
    ]


# --- 硬件扫描码 (Scan Codes) 包含数字 0-9 ---
DIK_CODES = {
    # control
    "esc": (0x01, False),
    "enter": (0x1C, False),
    "space": (0x39, False),
    "backspace": (0x0E, False),
    "tab": (0x0F, False),
    "lshift": (0x2A, False),
    "rshift": (0x36, False),
    "lctrl": (0x1D, False),
    "rctrl": (0x1D, True),
    "lalt": (0x38, False),
    "ralt": (0x38, True),
    "capslock": (0x3A, False),

    # letters
    "a": (0x1E, False),
    "b": (0x30, False),
    "c": (0x2E, False),
    "d": (0x20, False),
    "e": (0x12, False),
    "f": (0x21, False),
    "g": (0x22, False),
    "h": (0x23, False),
    "i": (0x17, False),
    "j": (0x24, False),
    "k": (0x25, False),
    "l": (0x26, False),
    "m": (0x32, False),
    "n": (0x31, False),
    "o": (0x18, False),
    "p": (0x19, False),
    "q": (0x10, False),
    "r": (0x13, False),
    "s": (0x1F, False),
    "t": (0x14, False),
    "u": (0x16, False),
    "v": (0x2F, False),
    "w": (0x11, False),
    "x": (0x2D, False),
    "y": (0x15, False),
    "z": (0x2C, False),

    # number row
    "1": (0x02, False),
    "2": (0x03, False),
    "3": (0x04, False),
    "4": (0x05, False),
    "5": (0x06, False),
    "6": (0x07, False),
    "7": (0x08, False),
    "8": (0x09, False),
    "9": (0x0A, False),
    "0": (0x0B, False),

    # arrows / navigation
    "up": (0xC8, True),
    "down": (0xD0, True),
    "left": (0xCB, True),
    "right": (0xCD, True),
    "pageup": (0xC9, True),
    "pagedown": (0xD1, True),
    "home": (0xC7, True),
    "end": (0xCF, True),
    "insert": (0xD2, True),
    "delete": (0xD3, True),

    # function keys
    "f1": (0x3B, False),
    "f2": (0x3C, False),
    "f3": (0x3D, False),
    "f4": (0x3E, False),
    "f5": (0x3F, False),
    "f6": (0x40, False),
    "f7": (0x41, False),
    "f8": (0x42, False),
    "f9": (0x43, False),
    "f10": (0x44, False),
    "f11": (0x57, False),
    "f12": (0x58, False),
}

# --- 全局配置 ---
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
MATCH_THRESHOLD = 0.8
pyautogui.FAILSAFE = False

LANGUAGE_OPTIONS = {
    "中文": "zh",
    "English": "en",
    "한국어": "ko",
}
LANGUAGE_LABELS = {v: k for k, v in LANGUAGE_OPTIONS.items()}
DEFAULT_UI_LANGUAGE = "ko"

UI_TEXT = {
    "next_step": {"zh": "下一步骤", "en": "Next Step", "ko": "다음 단계"},
    "continue": {"zh": "继续", "en": "Continue", "ko": "계속"},
    "start": {"zh": "开始", "en": "Start", "ko": "시작"},
    "start_danger": {"zh": "！！开始！！", "en": "!! Start !!", "ko": "!! 시작 !!"},
    "progress_exec": {"zh": "执行: {current} / {total}", "en": "Run: {current} / {total}", "ko": "진행: {current} / {total}"},
    "race_title": {"zh": "1. 循环跑图", "en": "1. Repeat Race", "ko": "1. 반복 레이스"},
    "buy_title": {"zh": "2. 批量买车", "en": "2. Bulk Buy Cars", "ko": "2. 차량 일괄 구매"},
    "wheelspin_title": {"zh": "3. 超级抽奖", "en": "3. Super Wheelspin", "ko": "3. 슈퍼 휠스핀"},
    "sell_title": {"zh": "4. 移除车辆", "en": "4. Remove Cars", "ko": "4. 차량 제거"},
    "share_placeholder": {"zh": "蓝图数字代码", "en": "Blueprint code", "ko": "블루프린트 코드"},
    "clear_matrix": {"zh": "清除矩阵", "en": "Clear Matrix", "ko": "경로 초기화"},
    "skill_tree": {"zh": "技能树", "en": "Skill Tree", "ko": "스킬 트리"},
    "sell_mode_1": {"zh": "模式1: 识图移除模式", "en": "Mode 1: Image Remove", "ko": "모드 1: 이미지 인식 제거"},
    "sell_mode_2": {"zh": "模式2: 移除最近添加", "en": "Mode 2: Remove Recent", "ko": "모드 2: 최근 추가 제거"},
    "global_settings": {"zh": "⚙️ 循环与守护设置", "en": "⚙️ Loop & Watchdog", "ko": "⚙️ 반복 및 보호 설정"},
    "global_loops": {"zh": "大循环次数:", "en": "Loop Count:", "ko": "대루프 횟수:"},
    "auto_restart": {"zh": "游戏闪退自动重启（测试）", "en": "Auto restart game crash (test)", "ko": "치명적 오류 발생 시 자동 재시작"},
    "restart_cmd": {"zh": "启动命令(CMD):", "en": "Launch CMD:", "ko": "실행 명령(CMD):"},
    "language_label": {"zh": "语言:", "en": "Language:", "ko": "언어:"},
    "calculator": {"zh": "次数计算器", "en": "Run Calculator", "ko": "횟수 계산기"},
    "empty_no_calc": {"zh": "留空不计算", "en": "Blank to skip", "ko": "비우면 계산 안 함"},
    "cost_per_car": {"zh": "单车成本(CR):", "en": "Cost/Car(CR):", "ko": "차량당 비용(CR):"},
    "sp_per_car": {"zh": "单车技能点:", "en": "SP/Car:", "ko": "차량당 스킬 포인트:"},
    "calculate_apply": {"zh": "计算并应用", "en": "Calculate", "ko": "계산 적용"},
    "current_task_waiting": {"zh": "当前任务: 等待中", "en": "Task: Waiting", "ko": "현재 작업: 대기 중"},
    "task_progress_zero": {"zh": "任务进度: 0 / 0", "en": "Progress: 0 / 0", "ko": "작업 진행: 0 / 0"},
    "loop_zero": {"zh": "大循环: 0 / 0", "en": "Loop: 0 / 0", "ko": "대루프: 0 / 0"},
    "total_time_zero": {"zh": "总耗时: 00:00:00", "en": "Elapsed: 00:00:00", "ko": "총 경과: 00:00:00"},
    "total_time": {"zh": "总耗时: {time}", "en": "Elapsed: {time}", "ko": "총 경과: {time}"},
    "mini_stop": {"zh": "⏸ 停止 (F8)", "en": "⏸ Stop (F8)", "ko": "⏸ 중지 (F8)"},
    "mini_support": {"zh": "❤ 支持", "en": "❤ Support", "ko": "❤ 후원"},
    "waiting_command": {"zh": "⏸ 等待指令 (F8)", "en": "⏸ Waiting (F8)", "ko": "⏸ 명령 대기 (F8)"},
    "waiting_command_plain": {"zh": "等待指令 (F8)", "en": "Waiting (F8)", "ko": "명령 대기 (F8)"},
    "support_update": {"zh": "❤ 支持作者 / 检查更新", "en": "❤ Support / Check Update", "ko": "❤ 후원 / 업데이트 확인"},
    "support_title": {"zh": "感谢支持 & 更新", "en": "Support & Update", "ko": "후원 및 업데이트"},
    "support_header": {"zh": "感谢您的支持与鼓励", "en": "Thanks for your support", "ko": "응원과 후원에 감사드립니다"},
    "support_desc": {"zh": "您的支持是我持续优化的动力！", "en": "Your support helps keep this tool improving.", "ko": "후원은 도구를 계속 개선하는 데 큰 도움이 됩니다."},
    "qr_missing": {"zh": "（未找到内置 qrcode.png）", "en": "(Built-in qrcode.png not found)", "ko": "(내장 qrcode.png를 찾지 못했습니다)"},
    "qr_failed": {"zh": "（二维码加载失败）", "en": "(QR load failed)", "ko": "(QR 코드 로드 실패)"},
    "sponsor_page": {"zh": "前往 爱发电 赞助主页", "en": "Open Sponsor Page", "ko": "후원 페이지 열기"},
    "current_version": {"zh": "当前版本: v{version}", "en": "Current version: v{version}", "ko": "현재 버전: v{version}"},
    "connecting_github": {"zh": "正在连接 Github...", "en": "Connecting to Github...", "ko": "Github에 연결 중..."},
    "new_version": {"zh": "发现新版本 v{version}，已打开浏览器！", "en": "New version v{version}; browser opened.", "ko": "새 버전 v{version} 발견, 브라우저를 열었습니다."},
    "untrusted_update": {"zh": "发现更新，但链接不可信，已拦截", "en": "Update found, but the link was blocked.", "ko": "업데이트를 찾았지만 신뢰할 수 없는 링크라 차단했습니다."},
    "latest_version": {"zh": "当前已是最新版本 (v{version})", "en": "Already up to date (v{version})", "ko": "현재 최신 버전입니다(v{version})"},
    "update_server_failed": {"zh": "检查更新失败 (服务器异常)", "en": "Update check failed (server error)", "ko": "업데이트 확인 실패(서버 오류)"},
    "update_network_failed": {"zh": "检查更新失败 (网络超时或无法访问)", "en": "Update check failed (network timeout/unreachable)", "ko": "업데이트 확인 실패(네트워크 시간 초과 또는 접근 불가)"},
    "check_update": {"zh": "检查更新", "en": "Check Update", "ko": "업데이트 확인"},
    "current_task": {"zh": "当前任务: {task}", "en": "Task: {task}", "ko": "현재 작업: {task}"},
    "run_progress": {"zh": "执行进度: {current} / {total}", "en": "Progress: {current} / {total}", "ko": "진행: {current} / {total}"},
    "loop_progress": {"zh": "大循环: {current} / {total}", "en": "Loop: {current} / {total}", "ko": "대루프: {current} / {total}"},
    "telegram_settings": {"zh": "📨 Telegram 通知", "en": "📨 Telegram Notifications", "ko": "📨 텔레그램 알림"},
    "telegram_enabled": {"zh": "启用", "en": "Enabled", "ko": "활성화"},
    "telegram_bot_token": {"zh": "Bot Token:", "en": "Bot Token:", "ko": "Bot Token:"},
    "telegram_chat_id": {"zh": "Chat ID:", "en": "Chat ID:", "ko": "Chat ID:"},
    "telegram_test": {"zh": "测试", "en": "Test", "ko": "테스트"},
    "telegram_on_fatal": {"zh": "致命错误", "en": "Fatal Error", "ko": "치명적 오류"},
    "telegram_on_step": {"zh": "步骤完成", "en": "Step Done", "ko": "단계 완료"},
    "telegram_on_loop": {"zh": "循环完成", "en": "Loop Done", "ko": "루프 완료"},
    "telegram_on_finish": {"zh": "全部完成", "en": "All Done", "ko": "전체 완료"},
    "telegram_test_success": {"zh": "Telegram 测试消息发送成功！", "en": "Telegram test message sent successfully!", "ko": "텔레그램 테스트 메시지 전송 성공!"},
    "telegram_test_fail": {"zh": "Telegram 发送失败，请检查 Token 和 Chat ID", "en": "Telegram send failed. Check Token and Chat ID.", "ko": "텔레그램 전송 실패. Token과 Chat ID를 확인하세요."},
    "tg_fatal_recovery": {"zh": "🚨 [FH6Auto] 连续 {failures} 次恢复失败，强制终止\n总耗时: {elapsed}", "en": "🚨 [FH6Auto] {failures} consecutive recovery failures, forced stop\nElapsed: {elapsed}", "ko": "🚨 [FH6Auto] 연속 {failures}회 복구 실패, 강제 종료\n총 경과: {elapsed}"},
    "tg_fatal_menu": {"zh": "🚨 [FH6Auto] 致命错误: 菜单复归/重启失败，完全停止\n总耗时: {elapsed}", "en": "🚨 [FH6Auto] Fatal: menu recovery/restart failed, stopped\nElapsed: {elapsed}", "ko": "🚨 [FH6Auto] 치명적 오류: 메뉴 복귀/재시작 실패, 완전 정지\n총 경과: {elapsed}"},
    "tg_step": {"zh": "✅ [FH6Auto] {label} 完成\n耗时: {step_elapsed} | 总耗时: {total_elapsed}\n大循环: {loop_cur}/{loop_total}", "en": "✅ [FH6Auto] {label} done\nStep: {step_elapsed} | Elapsed: {total_elapsed}\nLoop: {loop_cur}/{loop_total}", "ko": "✅ [FH6Auto] {label} 완료\n소요: {step_elapsed} | 총 경과: {total_elapsed}\n대루프: {loop_cur}/{loop_total}"},
    "tg_finish": {"zh": "🎉 [FH6Auto] 全部任务完成!\n总大循环: {loop_total} | 总耗时: {elapsed}", "en": "🎉 [FH6Auto] All tasks completed!\nTotal loops: {loop_total} | Elapsed: {elapsed}", "ko": "🎉 [FH6Auto] 전체 작업 완료!\n총 대루프: {loop_total} | 총 경과: {elapsed}"},
    "tg_loop": {"zh": "🔄 [FH6Auto] 大循环 {loop_cur}/{loop_total} 开始\n总耗时: {elapsed}", "en": "🔄 [FH6Auto] Loop {loop_cur}/{loop_total} started\nElapsed: {elapsed}", "ko": "🔄 [FH6Auto] 대루프 {loop_cur}/{loop_total} 시작\n총 경과: {elapsed}"},
}

TASK_TEXT = {
    "初始化中...": {"zh": "初始化中...", "en": "Initializing...", "ko": "초기화 중..."},
    "循环跑图": {"zh": "循环跑图", "en": "Repeat Race", "ko": "반복 레이스"},
    "批量买车": {"zh": "批量买车", "en": "Bulk Buy Cars", "ko": "차량 일괄 구매"},
    "超级抽奖": {"zh": "超级抽奖", "en": "Super Wheelspin", "ko": "슈퍼 휠스핀"},
    "移除车辆": {"zh": "移除车辆", "en": "Remove Cars", "ko": "차량 제거"},
}

LOG_EXACT = {
    "免责声明：本脚本仅供 Python 自动化技术交流与学习使用。请勿用于商业盈利或破坏游戏平衡，因使用本脚本造成的账号封禁等损失，由使用者自行承担。": {
        "en": "Disclaimer: this script is only for Python automation study and technical exchange. Do not use it commercially or to disrupt game balance. Any account bans or losses are your responsibility.",
        "ko": "면책 안내: 이 스크립트는 Python 자동화 기술 교류와 학습용입니다. 상업적 이익이나 게임 밸런스 훼손 목적으로 사용하지 마세요. 사용으로 인한 계정 제재 등 손실은 사용자 책임입니다.",
    },
    "工具运行目录不要有中文": {
        "en": "Do not place this tool in a path containing Chinese characters.",
        "ko": "도구 실행 경로에는 중국어 문자가 없도록 해 주세요.",
    },
    "默认刷图车辆：【斯巴鲁Impreza 22B-STi Version】【调校S2  900】【保持默认涂装】【收藏车辆】": {
        "en": "Default race car: [Subaru Impreza 22B-STi Version], [Tune S2 900], [default livery], [favorite car].",
        "ko": "기본 레이스 차량: [Subaru Impreza 22B-STi Version], [맵에 맞는 튜닝], [즐겨찾기 차량], [자신의 차량에 맞는 skillcar.png 변경 필수].",
    },
    "启动前先将键盘设置为【英文键盘】": {
        "en": "Set the keyboard to English before starting.",
        "ko": "시작 전에 키보드를 [영문 키보드]로 전환하세요.",
    },
    "游戏设置为【自动转向】【自动挡】，游戏语言设置为【简体中文】": {
        "en": "Set the game to [auto steering], [automatic transmission], and choose the matching game language.",
        "ko": "게임 설정은 [자동 조향], [자동 변속]으로 맞추고, 게임 언어는 OCR/이미지 설정과 맞춰 주세요.",
    },
    "大部分以图像识别作为引导，减少机器盲目操作的风险，但仍无法完全避免，使用前请做好准备": {
        "en": "Most steps are guided by image recognition to reduce blind automation risk, but it cannot be fully eliminated. Prepare before use.",
        "ko": "대부분의 과정은 이미지 인식으로 안내해 무작정 입력하는 위험을 줄였지만 완전히 제거할 수는 없습니다. 사용 전 상태를 확인하세요.",
    },
}

LOG_PATTERNS = [
    (r"目标金额不足\(只够买(\d+)辆车\)，无法产生有效跑图！", {
        "en": r"Target CR is too low (only enough for \1 cars), so no valid race plan can be generated.",
        "ko": r"목표 금액이 부족합니다(\1대만 구매 가능). 유효한 레이스 계획을 만들 수 없습니다.",
    }),
    (r"✅计算完成: 总计需(\d+)车, 共跑图(\d+)次。分配为: (\d+) 个大循环, 每轮跑图 (\d+) 次, 动作 (\d+) 辆。", {
        "en": r"✅ Calculation complete: need \1 cars and \2 races. Allocation: \3 loops, \4 races per loop, \5 cars/actions.",
        "ko": r"✅ 계산 완료: 총 \1대 차량, 레이스 \2회가 필요합니다. 배분: 대루프 \3회, 루프당 레이스 \4회, 작업 차량 \5대.",
    }),
    (r"执行模块 (.+) 时异常: (.+)", {
        "en": r"Exception while running module \1: \2",
        "ko": r"모듈 \1 실행 중 예외 발생: \2",
    }),
    (r"!!! 警告：连续 (\d+) 次触发断点恢复仍未能解决问题！", {
        "en": r"!!! Warning: recovery failed after \1 consecutive attempts!",
        "ko": r"!!! 경고: 중단점 복구를 \1회 연속 시도했지만 해결하지 못했습니다!",
    }),
    (r"正在进行全局恢复 \(第 (\d+)/(\d+) 次允许的重试\)\.\.\.", {
        "en": r"Running global recovery (allowed retry \1/\2)...",
        "ko": r"전체 복구 진행 중(허용 재시도 \1/\2)...",
    }),
    (r"开启新一轮大循环 \((\d+)/(\d+)\)", {
        "en": r"Starting next loop (\1/\2)",
        "ko": r"새 대루프 시작(\1/\2)",
    }),
    (r"重试返回漫游界面\((\d+)/100\)", {
        "en": r"Retrying return to freeroam (\1/100)",
        "ko": r"자유 주행 화면 복귀 재시도(\1/100)",
    }),
    (r"成功定位到菜单锚点！\((\d+)/60\)", {
        "en": r"Menu anchor found! (\1/60)",
        "ko": r"메뉴 기준점을 찾았습니다! (\1/60)",
    }),
    (r"未在主菜单，按下 ESC... \((\d+)/60\)", {
        "en": r"Not in main menu, pressing ESC... (\1/60)",
        "ko": r"아직 메인 메뉴가 아닙니다. ESC 입력 중... (\1/60)",
    }),
    (r"跑图 (\d+)/(\d+): 找赛事起点\.\.\.", {
        "en": r"Race \1/\2: finding event start...",
        "ko": r"레이스 \1/\2: 이벤트 시작 지점 탐색 중...",
    }),
    (r"智能记忆触发：快速跳过前 (\d+) 页\.\.\.", {
        "en": r"Smart memory: quickly skipping the first \1 pages...",
        "ko": r"스마트 기억 사용: 앞 \1페이지를 빠르게 건너뜁니다...",
    }),
    (r"锁定目标车辆！已记录当前页码: (\d+)", {
        "en": r"Target car locked. Current page saved: \1",
        "ko": r"대상 차량을 고정했습니다. 현재 페이지 기록: \1",
    }),
    (r"第 (\d+) 次检测到购买与出售，进入车辆界面", {
        "en": r"Detected Buy & Sell on attempt \1, entering vehicle screen.",
        "ko": r"\1번째 시도에서 구매 및 판매를 감지했습니다. 차량 화면으로 진입합니다.",
    }),
    (r"第 (\d+) 次未检测到购买与出售，等待后重试", {
        "en": r"Buy & Sell not detected on attempt \1; waiting and retrying.",
        "ko": r"\1번째 시도에서 구매 및 판매를 감지하지 못했습니다. 대기 후 재시도합니다.",
    }),
    (r"已尝试删除车辆 (\d+)/(\d+)", {
        "en": r"Attempted car removal \1/\2",
        "ko": r"차량 제거 시도 \1/\2",
    }),
    (r"正在使用 3模式 严格扫描当前页面\.\.\. \(连续未找到: (\d+)/5\)", {
        "en": r"Strictly scanning current page with 3-mode check... (consecutive misses: \1/5)",
        "ko": r"3모드로 현재 페이지를 엄격히 스캔 중... (연속 미발견: \1/5)",
    }),
    (r"当前页面未找到，向右翻页寻找\.\.\. \(第 (\d+) 次翻页\)", {
        "en": r"Not found on current page, paging right... (page turn \1)",
        "ko": r"현재 페이지에서 찾지 못했습니다. 오른쪽으로 넘겨 탐색합니다... (\1번째 넘김)",
    }),
    (r"成功移除车辆！当前进度: (\d+)/(\d+)", {
        "en": r"Car removed successfully. Progress: \1/\2",
        "ko": r"차량 제거 완료. 진행도: \1/\2",
    }),
]

LOG_PHRASES = {
    "语言已保存，将在任务结束后刷新界面。": {"en": "Language saved; the interface will refresh after the task ends.", "ko": "언어가 저장되었습니다. 작업이 끝난 뒤 화면을 새로고침합니다."},
    "语言已切换。": {"en": "Language switched.", "ko": "언어가 전환되었습니다."},
    "用户 config.json 损坏，已自动恢复默认配置。": {"en": "User config.json is damaged; restored defaults automatically.", "ko": "사용자 config.json이 손상되어 기본 설정으로 자동 복구했습니다."},
    "保存配置失败": {"en": "Failed to save config", "ko": "설정 저장 실패"},
    "未输入CR，无需计算。": {"en": "CR was not entered; no calculation needed.", "ko": "CR이 입력되지 않아 계산하지 않습니다."},
    "输入格式有误，请确保只输入数字！": {"en": "Invalid input format. Use digits only.", "ko": "입력 형식이 잘못되었습니다. 숫자만 입력하세요."},
    "单车成本或技能点不能为 0！": {"en": "Cost per car or skill points cannot be 0.", "ko": "차량당 비용 또는 스킬 포인트는 0일 수 없습니다."},
    "计算后可用大循环次数为0。": {"en": "Calculated loop count is 0.", "ko": "계산 후 사용 가능한 대루프 횟수가 0입니다."},
    "为防止游戏陷入死循环，强制终止当前所有任务，请人工检查游戏状态。": {"en": "Stopping all tasks to prevent an infinite loop. Check the game state manually.", "ko": "무한 루프를 방지하기 위해 모든 작업을 강제 종료합니다. 게임 상태를 직접 확인하세요."},
    "致命错误：连退回菜单/重启也失败了，彻底停止。": {"en": "Fatal error: returning to menu/restarting also failed. Stopping completely.", "ko": "치명적 오류: 메뉴 복귀/재시작도 실패했습니다. 완전히 중지합니다."},
    "达到设定的总循环次数，任务圆满结束。": {"en": "Configured total loop count reached. Task completed.", "ko": "설정한 총 반복 횟수에 도달했습니다. 작업을 종료합니다."},
    "任务已停止，所有物理按键状态已强制重置": {"en": "Task stopped; all physical key states were reset.", "ko": "작업이 중지되었고 모든 물리 키 상태를 강제로 초기화했습니다."},
    "已自动切换英文键盘/关闭中文输入法状态。": {"en": "Switched to English keyboard / disabled Chinese IME state.", "ko": "영문 키보드로 전환하고 중국어 입력 상태를 해제했습니다."},
    "自动防中文输入设置失败": {"en": "Failed to enforce non-Chinese input mode", "ko": "중국어 입력 방지 설정 실패"},
    "检查游戏进程": {"en": "Checking game process", "ko": "게임 프로세스 확인 중"},
    "未发现 forzahorizon6.exe 进程！(请确保游戏已运行)": {"en": "forzahorizon6.exe was not found. Make sure the game is running.", "ko": "forzahorizon6.exe 프로세스를 찾지 못했습니다. 게임이 실행 중인지 확인하세요."},
    "找到进程但无法解析PID！": {"en": "Process found, but PID could not be parsed.", "ko": "프로세스는 찾았지만 PID를 해석하지 못했습니다."},
    "获取窗口坐标失败": {"en": "Failed to get window coordinates", "ko": "창 좌표 가져오기 실패"},
    "检查进程异常": {"en": "Process check exception", "ko": "프로세스 확인 예외"},
    "未开启自动重启，任务结束。": {"en": "Auto restart is disabled. Task ended.", "ko": "자동 재시작이 꺼져 있어 작업을 종료합니다."},
    "触发自动重启机制！正在拉起游戏...": {"en": "Auto restart triggered. Launching game...", "ko": "자동 재시작을 실행합니다. 게임을 실행 중..."},
    "执行重启命令失败": {"en": "Failed to run restart command", "ko": "재시작 명령 실행 실패"},
    "等待游戏启动加载 (10秒)...": {"en": "Waiting for game startup (10 seconds)...", "ko": "게임 시작 로딩 대기 중(10초)..."},
    "开始持续检测开机界面元素 (限制5分钟)...": {"en": "Monitoring startup screen elements (5-minute limit)...", "ko": "시작 화면 요소를 계속 감지합니다(5분 제한)..."},
    "识别到欢迎界面，按下回车。": {"en": "Welcome screen detected; pressing Enter.", "ko": "환영 화면을 감지해 Enter를 누릅니다."},
    "识别到继续游戏，点击进入！": {"en": "Continue detected; clicking in.", "ko": "계속하기를 감지해 진입합니다."},
    "尝试按 ESC 唤出菜单...": {"en": "Trying ESC to open menu...", "ko": "ESC로 메뉴를 여는 중..."},
    "成功重连并进入菜单，准备恢复执行！": {"en": "Reconnected and entered menu. Ready to resume.", "ko": "재연결 후 메뉴에 진입했습니다. 실행을 복구합니다."},
    "自动重启超时(2分钟未进入漫游)，放弃抢救。": {"en": "Auto restart timed out (not in freeroam within 2 minutes). Giving up recovery.", "ko": "재시작 후 2분 내 게임 진입 실패. 복구를 중단합니다."},
    "任务执行异常中断，准备执行断点恢复流程...": {"en": "Task interrupted by exception. Preparing recovery flow...", "ko": "예외 발생. 자동 복구를 시도합니다..."},
    "环境重置成功！即将从中断处继续剩余任务。": {"en": "Environment reset succeeded. Continuing remaining tasks from interruption point.", "ko": "복구 완료. 중단된 작업을 이어서 진행합니다."},
    "验证漫游状态...": {"en": "Verifying freeroam state...", "ko": "자유주행 상태 확인 중..."},
    "验证成功：已确认处于游戏漫游界面。": {"en": "Verified: game is in freeroam.", "ko": "확인 완료: 자유주행 화면입니다."},
    "多次尝试验证漫游界面失败，尝试进入菜单。": {"en": "Freeroam verification failed repeatedly; trying to enter menu.", "ko": "자유 주행 화면 확인을 여러 번 실패해 메뉴 진입을 시도합니다."},
    "开始尝试退回主菜单 (强制ESC兜底)...": {"en": "Trying to return to main menu (ESC fallback)...", "ko": "메인 메뉴 복귀를 시도합니다(ESC 대체 절차)..."},
    "正在尝试进入主菜单 (按ESC验证)...": {"en": "Trying to enter main menu (ESC verification)...", "ko": "메인 메뉴 진입 시도 중(ESC 확인)..."},
    "60 次 ESC 尝试均未进入菜单，请检查游戏状态。": {"en": "Failed to enter menu after 60 ESC attempts. Check game state.", "ko": "ESC 60회 시도 후에도 메뉴 진입 실패. 게임 상태를 확인하세요."},
    "开始构建模板缓存文件...": {"en": "Building template cache file...", "ko": "템플릿 캐시 파일 생성 중..."},
    "未找到 images 目录，无法构建模板缓存。": {"en": "images directory not found; cannot build template cache.", "ko": "images 디렉터리를 찾지 못해 템플릿 캐시를 만들 수 없습니다."},
    "模板缓存文件构建完成。": {"en": "Template cache file built.", "ko": "템플릿 캐시 파일 생성 완료."},
    "写入模板缓存失败": {"en": "Failed to write template cache", "ko": "템플릿 캐시 쓰기 실패"},
    "模板缓存文件加载成功。": {"en": "Template cache file loaded.", "ko": "템플릿 캐시 파일 로드 완료."},
    "加载模板缓存失败": {"en": "Failed to load template cache", "ko": "템플릿 캐시 로드 실패"},
    "模板缓存不存在或已失效，开始后台重建（这可能需要几秒钟）...": {"en": "Template cache is missing or stale; rebuilding in background (may take a few seconds)...", "ko": "템플릿 캐시가 없거나 만료되어 백그라운드에서 다시 만듭니다(몇 초 걸릴 수 있음)..."},
    "准备验证/进入菜单": {"en": "Preparing to verify/enter menu", "ko": "메뉴 확인/진입 준비 중"},
    "切换到创意中心": {"en": "Switching to Creative Hub", "ko": "크리에이티브 허브로 전환 중"},
    "未找到 eventlab": {"en": "EventLab not found.", "ko": "EventLab을 찾지 못했습니다."},
    "未找到游玩赛事": {"en": "Play Event not found.", "ko": "이벤트 플레이를 찾지 못했습니다."},
    "链接超时": {"en": "Connection timed out.", "ko": "연결 시간이 초과되었습니다."},
    "未找到带 liketag 的目标车辆，重新选品牌...": {"en": "Target car with liketag not found; selecting brand again...", "ko": "liketag가 있는 대상 차량을 찾지 못해 브랜드를 다시 선택합니다..."},
    "三次尝试未找到刷图车辆品牌。": {"en": "Failed to find race car brand after three attempts.", "ko": "세 번 시도했지만 레이스 차량 브랜드를 찾지 못했습니다."},
    "翻页未能找到带有 liketag 的刷图车辆！": {"en": "Could not find race car with liketag after paging.", "ko": "페이지를 넘겨도 liketag가 있는 레이스 차량을 찾지 못했습니다."},
    "前置完成，开始循环跑图！": {"en": "Setup complete. Starting repeat race loop.", "ko": "준비 완료. 반복 레이스를 시작합니다."},
    "找不到赛事起点，退出跑图。": {"en": "Event start not found; exiting race loop.", "ko": "이벤트 시작 지점을 찾지 못했습니다. 레이스를 종료합니다."},
    "跑图超时(已超过120秒)！触发强制重开赛事逻辑...": {"en": "Race timed out (over 120 seconds). Triggering forced restart logic...", "ko": "레이스 시간 초과(120초). 이벤트를 다시 시작합니다..."},
    "识别到点赞作界面，执行回车确认！": {"en": "Like/dislike author screen detected; pressing Enter.", "ko": "제작자 좋아요 화면을 감지해 Enter로 확인합니다."},
    "找到 restarta.png，点击重开赛事...": {"en": "restarta.png found; clicking restart event...", "ko": "restarta.png를 찾았습니다. 이벤트를 다시 시작합니다..."},
    "未找到 restarta.png，尝试直接继续...": {"en": "restarta.png not found; trying to continue directly...", "ko": "restarta.png를 찾지 못해 바로 계속 시도합니다..."},
    "未找到收集簿": {"en": "Collection journal not found.", "ko": "컬렉션을 찾지 못했습니다."},
    "未找到探索": {"en": "Explorer not found.", "ko": "탐색 메뉴를 찾지 못했습니다."},
    "未找到车辆收集": {"en": "Car collection not found.", "ko": "차량 컬렉션을 찾지 못했습니다."},
    "未找到品牌": {"en": "Brand not found.", "ko": "브랜드를 찾지 못했습니다."},
    "未找到消耗品车辆": {"en": "Consumable car not found.", "ko": "소모용 차량을 찾지 못했습니다."},
    "进入车辆与收藏": {"en": "Entering Cars & Collection", "ko": "차량 및 컬렉션으로 진입 중"},
    "未识别到 购买新车与二手车": {"en": "Buy New & Used Cars not detected.", "ko": "신차 및 중고차 구매를 인식하지 못했습니다."},
    "未找到购买与出售": {"en": "Buy & Sell not found.", "ko": "구매 및 판매를 찾지 못했습니다."},
    "进入车辆界面": {"en": "Entering vehicle screen", "ko": "차량 화면 진입 중"},
    "进入我的车辆.": {"en": "Entering My Cars.", "ko": "내 차량으로 진입합니다."},
    "选品牌失败": {"en": "Brand selection failed.", "ko": "브랜드 선택 실패."},
    "列表中未找到目标车辆，重置记忆页码。": {"en": "Target car not found in list; resetting remembered page.", "ko": "목록에서 대상 차량을 찾지 못해 기억한 페이지를 초기화합니다."},
    "尝试寻找'上车'按钮...": {"en": "Trying to find 'Get in car' button...", "ko": "'차량 탑승' 버튼을 찾는 중..."},
    "点击上车": {"en": "Clicking Get in car.", "ko": "차량 탑승을 클릭합니다."},
    "回车上车": {"en": "Pressing Enter to get in car.", "ko": "Enter로 차량에 탑승합니다."},
    "找不到升级页面": {"en": "Upgrade page not found.", "ko": "업그레이드 페이지를 찾지 못했습니다."},
    "未找到车辆熟练度": {"en": "Car mastery not found.", "ko": "차량 숙련도를 찾지 못했습니다."},
    "该车辆技能已点过，跳过计数": {"en": "This car's skills were already selected; skipping count.", "ko": "이 차량의 스킬은 이미 찍혀 있어 카운트를 건너뜁니다."},
    "已无技能点或技能已点完，提前结束抽奖！": {"en": "No skill points left or skills completed; ending wheelspin early.", "ko": "스킬 포인트가 없거나 스킬을 모두 찍어 휠스핀을 조기 종료합니다."},
    "使用前请人工核验到正常移除车辆再进行自动化移除处理": {"en": "Before use, manually verify that car removal works normally, then run automated removal.", "ko": "사용 전 차량 제거가 정상 동작하는지 직접 확인한 뒤 자동 제거를 실행하세요."},
    "找到上车，执行点击": {"en": "Get in car found; clicking.", "ko": "차량 탑승을 찾아 클릭합니다."},
    "该车辆已经驾驶，或未找到图片，执行两次ESC": {"en": "This car is already driven or image was not found; pressing ESC twice.", "ko": "이미 운전 중인 차량이거나 이미지를 찾지 못해 ESC를 두 번 누릅니다."},
    "60次内未找到购买与出售": {"en": "Buy & Sell not found within 60 attempts.", "ko": "60회 안에 구매 및 판매를 찾지 못했습니다."},
    "30次内未找到购买与出售": {"en": "Buy & Sell not found within 30 attempts.", "ko": "30회 안에 구매 및 판매를 찾지 못했습니다."},
    "切换到 最近获得 的排序...": {"en": "Switching sort to Recently Acquired...", "ko": "정렬을 최근 획득으로 전환합니다..."},
    "回到最近获得的前面": {"en": "Returning to the front of Recently Acquired.", "ko": "최근 획득 목록의 앞쪽으로 돌아갑니다."},
    "开始删除最近获得的车辆！！！请人工确认是否移除": {"en": "Starting removal of recently acquired cars. Manually confirm before removing.", "ko": "최근 획득 차량 삭제 시작. 제거 대상이 맞는지 확인하세요."},
    "切换到消耗品品牌...": {"en": "Switching to consumable brand...", "ko": "소모용 차량 브랜드로 전환 중..."},
    "=连续翻找 2 页仍未搜索到目标车辆！视为车辆已全部清理完毕。": {"en": "Target car not found after 2 consecutive pages. Treating cleanup as complete.", "ko": "2페이지 연속 대상 차량을 찾지 못했습니다. 차량 정리가 완료된 것으로 간주합니다."},
    "主动结束清理任务，准备进入下一步骤...": {"en": "Ending cleanup task and preparing for next step...", "ko": "차량 정리 완료. 다음 단계로 이동합니다."},
    "精准锁定目标车辆，执行点击...": {"en": "Target car precisely locked; clicking...", "ko": "대상 차량을 정확히 고정해 클릭합니다..."},
    "寻找 '从车库移除' 按钮...": {"en": "Finding 'Remove from garage' button...", "ko": "'차고에서 제거' 버튼을 찾는 중..."},
    "直接找到移除按钮，点击...": {"en": "Remove button found directly; clicking...", "ko": "제거 버튼을 바로 찾아 클릭합니다..."},
    "未直接找到移除按钮，按下 Enter 呼出菜单...": {"en": "Remove button not found directly; pressing Enter to open menu...", "ko": "제거 버튼을 바로 찾지 못해 Enter로 메뉴를 엽니다..."},
    "呼出菜单后找到移除按钮，点击...": {"en": "Remove button found after opening menu; clicking...", "ko": "메뉴를 연 뒤 제거 버튼을 찾아 클릭합니다..."},
    "仍未找到移除按钮，可能点错了/该车无法移除，按 ESC 放弃该车...": {"en": "Still no remove button; maybe wrong car or not removable. Pressing ESC to skip.", "ko": "여전히 제거 버튼을 찾지 못했습니다. 잘못 선택했거나 제거 불가 차량일 수 있어 ESC로 건너뜁니다..."},
    "确认移除...": {"en": "Confirming removal...", "ko": "제거 확인 중..."},
    "命中": {"en": "hit", "ko": "감지"},
    "得分": {"en": "score", "ko": "점수"},
    "阈值": {"en": "threshold", "ko": "임계값"},
    "缩放比": {"en": "scale", "ko": "스케일"},
    "主图": {"en": "main", "ko": "메인"},
    "元素": {"en": "element", "ko": "요소"},
    "灰度得分": {"en": "gray score", "ko": "그레이스케일 점수"},
    "无视背景": {"en": "ignore background", "ko": "배경 무시"},
    "识别报错": {"en": "recognition error", "ko": "인식 오류"},
    "[排他拦截]": {"en": "[Exclusion Block]", "ko": "[배타 차단]"},
    "[终极安全-通过]": {"en": "[Ultimate Safe-Pass]", "ko": "[최종 안전-통과]"},
    "发现 NEW 标签": {"en": "NEW tag found", "ko": "NEW 태그 발견"},
    "放弃该目标。": {"en": "skipping this target.", "ko": "해당 대상을 건너뜁니다."},
    "锁定目标": {"en": "target locked", "ko": "대상 고정"},
    "综合": {"en": "combined", "ko": "종합"},
    "彩色": {"en": "color", "ko": "컬러"},
    "灰度": {"en": "gray", "ko": "그레이스케일"},
    "边缘": {"en": "edge", "ko": "엣지"},
    "中心": {"en": "center", "ko": "중앙"},
    "标签": {"en": "tag", "ko": "태그"},
    "总分": {"en": "total", "ko": "총점"},
    "顶部车名": {"en": "top car name", "ko": "상단 차량명"},
    "右下调校": {"en": "bottom-right tune", "ko": "오른쪽 하단 튜닝"},
    "需>": {"en": "need>", "ko": "필요>"},
    "异常": {"en": "exception", "ko": "예외"},
    "查找图片时发生异常": {"en": "Exception while finding image", "ko": "이미지 탐색 중 예외 발생"},
}


class FH_UltimateBot(ctk.CTk):
    def normalize_language(self, value):
        if value in LANGUAGE_OPTIONS:
            return LANGUAGE_OPTIONS[value]
        value = str(value or "").strip().lower()
        if value in LANGUAGE_LABELS:
            return value
        aliases = {
            "chinese": "zh",
            "中文": "zh",
            "简体中文": "zh",
            "zh-cn": "zh",
            "english": "en",
            "영어": "en",
            "korean": "ko",
            "한국어": "ko",
            "한글": "ko",
        }
        return aliases.get(value, DEFAULT_UI_LANGUAGE)

    def t(self, key, **kwargs):
        lang = getattr(self, "ui_language", DEFAULT_UI_LANGUAGE)
        text = UI_TEXT.get(key, {}).get(lang) or UI_TEXT.get(key, {}).get("zh") or key
        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def task_text(self, task_name):
        lang = getattr(self, "ui_language", DEFAULT_UI_LANGUAGE)
        return TASK_TEXT.get(task_name, {}).get(lang, task_name)

    def localize_log_message(self, message):
        lang = getattr(self, "ui_language", DEFAULT_UI_LANGUAGE)
        text = str(message)
        if lang == "zh":
            return text

        if text in LOG_EXACT and lang in LOG_EXACT[text]:
            return LOG_EXACT[text][lang]

        for pattern, translations in LOG_PATTERNS:
            translated = translations.get(lang)
            if translated and re.search(pattern, text):
                return re.sub(pattern, translated, text)

        for source, translations in sorted(LOG_PHRASES.items(), key=lambda item: len(item[0]), reverse=True):
            translated = translations.get(lang)
            if translated:
                text = text.replace(source, translated)
        return text

    def on_language_change(self, choice):
        self.ui_language = self.normalize_language(choice)
        self.config["ui_language"] = self.ui_language
        self.save_config()

        if self.is_running:
            self.log("语言已保存，将在任务结束后刷新界面。")
            return

        for child in self.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

        self.setup_ui()
        self.update_skill_grid()
        self.center_window()
        self.log("语言已切换。")

    def __init__(self):
        super().__init__()
        #窗口相关
        self.title(f"FH6Auto KR Edition v{CURRENT_VERSION_KR} / Original by YSTO v{CURRENT_VERSION}")
        self.geometry("1800x880")
        #self.minsize(980, 560)
        self.attributes("-topmost", False)
        self.attributes("-alpha", 0.98)
        self.resizable(False, False)

        try:
            icon_path = get_asset_path("icon.ico")
            if icon_path:
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self.is_running = False
        self.current_thread = None

        # 일시정지 / 재개 상태
        self.is_paused = False 
        self.pause_requested = False

        self.use_win32_input = True

        self.race_counter = 0
        self.car_counter = 0
        self.cj_counter = 0
        self.sc_count = 0
        self.global_loop_current = 0

        self.template_cache = {}
        self.scaled_template_cache = {}
        self.file_template_cache = {}
        self.last_positions = {}
        self.support_win = None
        self.edge_template_cache = {}
        self.scaled_edge_template_cache = {}

        self.init_regions()
        
        #加载配置文件
        auto_extract_configs()
        self.load_config()

        # 고해상도/4K 이미지 보정 설정 변경 감지용
        self._last_high_res_fix = self.config.get("high_res_image_fix", False)

        self.user_image_config = self.load_user_image_config()
        self.ui_language = self.normalize_language(self.config.get("ui_language", DEFAULT_UI_LANGUAGE))
        self.config["ui_language"] = self.ui_language

        # 【优化加载速度】：将IO提取与图像缓存的加载/生成放到后台线程，避免阻塞主界面启动
        # 增加模型释放步骤
        def background_init():
            auto_extract_images()
            
            self.prepare_template_cache()
            #self.use_ocr = self.config.get("use_ocr", True)
            #if self.use_ocr:
            #    self.init_ocr_engine()
        threading.Thread(target=background_init, daemon=True).start()

        self.setup_ui()
        self.start_hotkey_listener()
        self.update_skill_grid()
        self.center_window()
        
        self.log("免责声明：本脚本仅供 Python 自动化技术交流与学习使用。请勿用于商业盈利或破坏游戏平衡，因使用本脚本造成的账号封禁等损失，由使用者自行承担。")
        self.log("工具运行目录不要有中文")
        self.log("默认刷图车辆：【斯巴鲁Impreza 22B-STi Version】【调校S2  900】【保持默认涂装】【收藏车辆】")
        self.log("启动前先将键盘设置为【英文键盘】")
        self.log("游戏设置为【自动转向】【自动挡】，游戏语言设置为【简体中文】")
        self.log("大部分以图像识别作为引导，减少机器盲目操作的风险，但仍无法完全避免，使用前请做好准备")

    # ==========================================
    # --- UI 安全调度 ---
    # ==========================================
    def ui_call(self, func, *args, **kwargs):
        try:
            self.after(0, lambda: func(*args, **kwargs))
        except Exception:
            pass

    def center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        gx, gy, gw, gh = self.regions["全界面"]
        x = gx + (gw - w) // 2
        y = gy + (gh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
    def sync_buy_to_sell(self, event=None):
        try:
            val = "".join(c for c in self.entry_car.get() if c.isdigit())
            if val == "":
                val = "0"
            self.entry_sc.delete(0, "end")
            self.entry_sc.insert(0, val)
        except Exception:
            pass

    def normalize_step_entry(self, entry_widget, default_value):
        try:
            v = "".join(c for c in entry_widget.get() if c.isdigit())
            if v == "":
                v = str(default_value)
            iv = int(v)
            if iv < 1:
                iv = 1
            if iv > 4:
                iv = 4
            entry_widget.delete(0, "end")
            entry_widget.insert(0, str(iv))
        except Exception:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, str(default_value))
    # ==========================================
    # --- 初始化全局 Region ---
    # ==========================================
    def init_regions(self):
        sw, sh = pyautogui.size()
        self.update_regions_by_window(0, 0, sw, sh)

    def update_regions_by_window(self, x, y, w, h):
        self.regions = {
            "全界面": (x, y, w, h),
            "左上": (x, y, w // 2, h // 2),
            "右上": (x + w // 2, y, w // 2, h // 2),
            "左下": (x, y + h // 2, w // 2, h // 2),
            "右下": (x + w // 2, y + h // 2, w // 2, h // 2),
            "上": (x, y, w, h // 2),
            "下": (x, y + h // 2, w, h // 2),
            "左": (x, y, w // 2, h),
            "右": (x + w // 2, y, w // 2, h),
            "中间": (x + w // 4, y + h // 4, w // 2, h // 2),
        }

    # ==========================================
    # --- 配置管理 ---
    # ==========================================
    def load_config(self):
        # 1. 直接使用内置字典作为“绝对底本”（最安全，无视打包丢文件问题）
        self.config = {
            "race_count": 99,
            "buy_count": 30, 
            "cj_count": 30, 
            "sc_count": 30,
            "chk_1": True, 
            "chk_2": True, 
            "chk_3": True, 
            "chk_4": True,
            "next_1": 2, 
            "next_2": 3, 
            "next_3": 4, 
            "next_4": 1,
            "global_loops": 10, 
            "skill_dirs": ["right", "up", "up", "up", "left"],
            "share_code": "726355095", 
            "auto_restart": False,
            "restart_cmd": "start steam://run/2483190", 
            "race_mode": 1,
            "race_car_mode": 1,
            "sell_mode": 1,
            "ui_language": DEFAULT_UI_LANGUAGE,
            "ocr_lang": "한국어",
            "telegram_enabled": False,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "telegram_on_fatal": True,
            "telegram_on_step": True,
            "telegram_on_loop": True,
            "telegram_on_finish": True,
            "high_res_image_fix": False,
            "image_debug_log": False,
            "always_on_top": True,
            "race_sp_per_run": 10,
            "finish_detect_start_sec": 0,
            "finish_detect_max_sec": 120,
            "race_count_bonus": 1,
            "loop_race_limit": 100,
            "max_sp_per_loop": 990,
            "loop_count_bonus": 0,
        }
        ext_path = USER_CONFIG_FILE
        # 2. 读取用户的 config.json，并与底本合并（自动补全缺失项）
        if os.path.exists(ext_path):
            try:
                with open(ext_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                    self.config.update(user_config) 
            except Exception as e:
                self.log(f"用户 config.json 损坏，已自动恢复默认配置。")
                
        # 3. 将最新、最完整的配置重新写回外置文件
        try:
            with open(ext_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception:
            pass
    
    def load_user_image_config(self):
        default_config = {
            "_comment_race_car_search_range": 
                "레이스 차량 탐색 범위, 값이 클수록 liketag.png로부터 skillcar.png까지의 간격을 더 넓게 탐색합니다(기본값 80)",
            "race_car_search_range": 80,

            "_comment_race_car_search_settings":
                "레이스 차량(skillcar.png + liketag.png) 탐색 설정입니다. "
                "main_threshold=차량 이미지 후보 점수, like_threshold=liketag 점수, final_threshold=최종 판정 점수입니다. "
                "threshold는 낮출수록 인식이 쉬워지지만 오인식 가능성이 증가합니다. "
                "fast_mode=true는 빠른 탐색, false는 느리지만 더 넓게 탐색합니다. "
                "(기본값: 0.75 / 0.70 / 0.70 / True)",

            "race_car_search_settings": {
                "main_threshold": 0.75,
                "like_threshold": 0.70,
                "final_threshold": 0.70,
                "fast_mode": True
            },

            "_comment_new_car_search_range": 
                "신규 차량 탐색 범위, 값이 클수록 newcartag.png로부터 newCC.png까지의 간격을 더 넓게 탐색합니다(기본값 5)",
            "new_car_search_range": 5,

            "_comment_new_car_start_right_count": 
                "신규 차량 탐색 시작 전 오른쪽 방향키를 몇 번 누를지 설정합니다. 0이면 처음부터 탐색합니다. 예: 3 = 오른쪽 3번 누른뒤 화면 탐색(기본값 0)",
            "new_car_start_right_count": 0,

            "_comment_new_car_page_buffer":
                "신규 차량 자동 페이지 보정 여유값입니다. 차량 12대마다 다음 페이지로 이동한 것으로 판단하며, 값이 클수록 다음 페이지로 넘어가는 시점이 늦어집니다. (기본값 3)",

            "new_car_page_buffer": 3,

            "_comment_car_enter_wait":
                "차량 탑승 후 ESC 메뉴 진입 전 대기시간(초) (기본값 6)",
            "car_enter_wait": 6,

            "_comment_new_car_max_search_count":
                "신규 차량 탐색 최대 횟수입니다. 너무 크면 차량이 없을 때 오래 탐색합니다.(85)",
            "new_car_max_search_count": 85,

            "_comment_brand_search_settings":
                "Subaru 제조사를 찾지 못하는 경우에만 조정하세요. "
                "threshold=낮출수록 인식이 쉬워집니다(기본 0.75). "
                "fast_mode=true는 빠른 탐색, false는 느리지만 더 넓게 탐색합니다. [반드시 소문자 true/false 사용] "
                "up_wait=제조사를 찾지 못했을 때 다음 제조사로 이동하기 전 대기시간(초)입니다. "
                "(기본값: 0.75 / True / 0.25)",

            "brand_search_settings": {
                "threshold": 0.75,
                "fast_mode": True,
                "up_wait": 0.25
            },

            "_comment_upgrade_search_settings": "업그레이드 및 튜닝 메뉴(UandT) 탐색 설정입니다. threshold=낮출수록 인식이 쉬워지지만 오인식 가능성이 증가합니다. fast_mode=false는 느리지만 더 넓게 탐색합니다. (기본값: 0.70 / True)",

            "upgrade_search_settings": {
                "threshold": 0.70,
                "fast_mode": True
            }
        }

        try:
            if not os.path.exists(USER_IMAGE_CONFIG_FILE):
                json_text = json.dumps(
                    default_config,
                    indent=4,
                    ensure_ascii=False
                )

                json_text = json_text.replace(
                    ',\n    "_comment_',
                    ',\n\n    "_comment_'
                )

                with open(USER_IMAGE_CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(json_text)

                return default_config

            with open(USER_IMAGE_CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = json.load(f)

            changed = False
            for key, value in default_config.items():
                if key not in user_config:
                    user_config[key] = value
                    changed = True

            if changed:
                json_text = json.dumps(
                    user_config,
                    indent=4,
                    ensure_ascii=False
                )
            
                json_text = json_text.replace(
                    ',\n    "_comment_',
                    ',\n\n    "_comment_'
                )
            
                with open(USER_IMAGE_CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(json_text)

            return user_config

        except Exception:
            return default_config
    def get_race_car_search_settings(self):
        car_cfg = self.user_image_config.get("race_car_search_settings", {})

        try:
            main_threshold = float(car_cfg.get("main_threshold", 0.75))
        except Exception:
            main_threshold = 0.75

        try:
            like_threshold = float(car_cfg.get("like_threshold", 0.70))
        except Exception:
            like_threshold = 0.70

        try:
            final_threshold = float(car_cfg.get("final_threshold", 0.70))
        except Exception:
            final_threshold = 0.70

        try:
            fast_mode = bool(car_cfg.get("fast_mode", True))
        except Exception:
            fast_mode = True

        main_threshold = max(0.45, min(main_threshold, 0.90))
        like_threshold = max(0.45, min(like_threshold, 0.90))
        final_threshold = max(0.45, min(final_threshold, 0.90))

        return main_threshold, like_threshold, final_threshold, fast_mode

    def get_upgrade_search_settings(self):
        cfg = self.user_image_config.get("upgrade_search_settings", {})

        try:
            threshold = float(cfg.get("threshold", 0.70))
        except Exception:
            threshold = 0.70

        try:
            fast_mode = cfg.get("fast_mode", True)
            if isinstance(fast_mode, str):
                fast_mode = fast_mode.strip().lower() == "true"
            else:
                fast_mode = bool(fast_mode)
        except Exception:
            fast_mode = True

        threshold = max(0.40, min(threshold, 0.90))

        return threshold, fast_mode

    def get_brand_search_settings(self):
        brand_cfg = self.user_image_config.get("brand_search_settings", {})
    
        try:
            threshold = float(brand_cfg.get("threshold", 0.75))
        except Exception:
            threshold = 0.75
        
        try:
            fast_mode = bool(brand_cfg.get("fast_mode", True))
        except Exception:
            fast_mode = True
        
        try:
            up_wait = float(brand_cfg.get("up_wait", 0.25))
        except Exception:
            up_wait = 0.25
    
        threshold = max(0.50, min(threshold, 0.90))
        up_wait = max(0.10, min(up_wait, 1.50))
    
        return threshold, fast_mode, up_wait
    
    def save_config(self):
        try:
            self.config["race_count"] = int(self.entry_race.get())
            self.config["buy_count"] = int(self.entry_car.get())
            self.config["cj_count"] = int(self.entry_cj.get())
            self.config["sc_count"] = int(self.entry_sc.get())
            self.config["global_loops"] = int(self.entry_global_loop.get())
            self.config["share_code"] = "".join(c for c in self.entry_share.get() if c.isdigit())
            #self.config["base_width"] = int(self.entry_base_w.get())
            self.config["next_1"] = int(self.entry_next1.get())
            self.config["next_2"] = int(self.entry_next2.get())
            self.config["next_3"] = int(self.entry_next3.get())
            self.config["next_4"] = int(self.entry_next4.get())
            if hasattr(self, "opt_race_mode"):
                self.config["race_mode"] = self.race_mode_values.get(self.opt_race_mode.get(), 1)
            if hasattr(self, "opt_race_car_mode"):
                self.config["race_car_mode"] = self.race_car_mode_values.get(self.opt_race_car_mode.get(), 1)
            if hasattr(self, "opt_sell_mode"):
                val = self.opt_sell_mode.get()
                mode_values = getattr(self, "sell_mode_values", {})
                if val in mode_values:
                    self.config["sell_mode"] = mode_values[val]
                elif "模式1" in val or "Mode 1" in val or "모드 1" in val:
                    self.config["sell_mode"] = 1
                else:
                    self.config["sell_mode"] = 2
        except Exception:
            pass

        self.config["ui_language"] = getattr(self, "ui_language", DEFAULT_UI_LANGUAGE)
        self.config["chk_1"] = self.var_chk1.get()
        self.config["chk_2"] = self.var_chk2.get()
        self.config["chk_3"] = self.var_chk3.get()
        self.config["chk_4"] = self.var_chk4.get()
        self.config["auto_restart"] = self.var_auto_restart.get()
        new_high_res_fix = self.var_high_res_image_fix.get()
        
        self.config["high_res_image_fix"] = new_high_res_fix
        self.config["image_debug_log"] = self.var_image_debug_log.get()
        if hasattr(self, "var_always_on_top"):
            self.config["always_on_top"] = self.var_always_on_top.get()

        self._last_high_res_fix = new_high_res_fix
        self.config["restart_cmd"] = self.le_restart_cmd.get().strip()
        try:
            if hasattr(self, "entry_calc_a"):
                self.config["calc_a"] = self.entry_calc_a.get().strip()
                self.config["calc_b"] = self.entry_calc_b.get().strip()
                self.config["calc_c"] = self.entry_calc_c.get().strip()
            if hasattr(self, "entry_race_sp_per_run"):
                val = self.entry_race_sp_per_run.get().strip()
                self.config["race_sp_per_run"] = int(val) if val else 10

                val = self.entry_finish_detect_start_sec.get().strip()
                self.config["finish_detect_start_sec"] = int(val) if val else 0

                val = self.entry_finish_detect_max_sec.get().strip()
                self.config["finish_detect_max_sec"] = int(val) if val else 120

                val = self.entry_race_count_bonus.get().strip()
                self.config["race_count_bonus"] = int(val) if val else 1

                val = self.entry_loop_race_limit.get().strip()
                self.config["loop_race_limit"] = int(val) if val else 100

                val = self.entry_max_sp_per_loop.get().strip()
                self.config["max_sp_per_loop"] = int(val) if val else 990

                val = self.entry_loop_count_bonus.get().strip()
                self.config["loop_count_bonus"] = int(val) if val else 0
        except Exception:
            pass
        try:
            if hasattr(self, "var_telegram_enabled"):
                self.config["telegram_enabled"] = self.var_telegram_enabled.get()
            if hasattr(self, "entry_telegram_token"):
                self.config["telegram_bot_token"] = self.entry_telegram_token.get().strip()
            if hasattr(self, "entry_telegram_chat_id"):
                self.config["telegram_chat_id"] = self.entry_telegram_chat_id.get().strip()
            if hasattr(self, "var_telegram_fatal"):
                self.config["telegram_on_fatal"] = self.var_telegram_fatal.get()
            if hasattr(self, "var_telegram_step"):
                self.config["telegram_on_step"] = self.var_telegram_step.get()
            if hasattr(self, "var_telegram_loop"):
                self.config["telegram_on_loop"] = self.var_telegram_loop.get()
            if hasattr(self, "var_telegram_finish"):
                self.config["telegram_on_finish"] = self.var_telegram_finish.get()
        except Exception:
            pass
        try:
            with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.log(f"保存配置失败: {e}")

    def auto_calculate_pipeline(self):
        val_a = self.entry_calc_a.get().strip()
        if not val_a:
            self.log("未输入CR，无需计算。")
            return
    
        try:
            target_cr = int(val_a)
    
            val_b = self.entry_calc_b.get().strip()
            cost_per_car = int(val_b) if val_b else 81700
    
            val_c = self.entry_calc_c.get().strip()
            sp_per_car = int(val_c) if val_c else 30
    
            val_race_sp = self.entry_race_sp_per_run.get().strip()
            race_sp_per_run = int(val_race_sp) if val_race_sp else 10
    
            val_race_bonus = self.entry_race_count_bonus.get().strip()
            race_count_bonus = int(val_race_bonus) if val_race_bonus else 1
    
            val_loop_limit = self.entry_loop_race_limit.get().strip()
            loop_race_limit = int(val_loop_limit) if val_loop_limit else 100
            
            val_max_sp = self.entry_max_sp_per_loop.get().strip()
            max_sp_per_loop = int(val_max_sp) if val_max_sp else 990
    
            val_loop_bonus = self.entry_loop_count_bonus.get().strip()
            loop_count_bonus = int(val_loop_bonus) if val_loop_bonus else 0
    
        except Exception:
            self.log("输入格式有误，请确保只输入数字！")
            return
    
        if cost_per_car <= 0 or sp_per_car <= 0 or race_sp_per_run <= 0 or loop_race_limit <= 0 or max_sp_per_loop <= 0:
            self.log("单车成本或技能点不能为 0！")
            return
    
        race_count_bonus = max(0, race_count_bonus)
        loop_count_bonus = max(0, loop_count_bonus)

        # 루프당 최대 보유 SP 기준으로 1루프 레이스 상한 계산
        max_races_by_sp = max_sp_per_loop // race_sp_per_run

        if max_races_by_sp <= 0:
            self.log("루프당 최대 SP가 1판 획득 SP보다 낮아 계산할 수 없습니다.")
            return

        # 기존 루프 기준 레이스와 SP 상한 중 더 작은 값을 실제 루프 상한으로 사용
        effective_loop_limit = min(loop_race_limit, max_races_by_sp)
    
        # 1. 기본 변환
        total_cars = target_cr // cost_per_car
        total_required_sp = total_cars * sp_per_car
    
        # 기존 계산 방식 유지:
        # 기존: ((필요 SP) // 10) + 1
        # 변경: ((필요 SP) // 1판 획득 SP) + 계산 여유 레이스
        total_races = (total_required_sp // race_sp_per_run) + race_count_bonus
    
        if total_races <= 0:
            self.log(f"目标金额不足(只够买{total_cars}辆车)，无法产生有效跑图！")
            return
    
        # 2. 대루프 분배
        if total_races <= effective_loop_limit:
            final_loops = 1
            final_races_per_loop = total_races
        else:
            import math
    
            loops = math.ceil(total_races / effective_loop_limit)
            avg_races = total_races // loops
    
            if avg_races >= int(effective_loop_limit * 0.70):
                final_loops = loops
                final_races_per_loop = avg_races
            else:
                final_races_per_loop = effective_loop_limit
                final_loops = total_races // effective_loop_limit
    
        # 3. 루프 여유 보정
        # 예: 계산 결과 99회 → 보정치 1이면 100회
        final_races_per_loop += loop_count_bonus

        # 루프 여유 보정 후에도 최대 SP를 넘지 않도록 제한
        if final_races_per_loop > max_races_by_sp:
            final_races_per_loop = max_races_by_sp
    
        if final_loops <= 0:
            self.log("计算后可用大循环次数为0。")
            return
    
        # 4. 레이스당 획득 SP 기준으로 루프당 차량/스킬 작업 수 계산
        cars_per_loop = (final_races_per_loop * race_sp_per_run) // sp_per_car
    
        if cars_per_loop <= 0:
            self.log("计算后车辆数量为0。")
            return
    
        # 5. 자동 입력
        self.entry_race.delete(0, "end")
        self.entry_race.insert(0, str(final_races_per_loop))
    
        self.entry_car.delete(0, "end")
        self.entry_car.insert(0, str(cars_per_loop))
    
        self.entry_cj.delete(0, "end")
        self.entry_cj.insert(0, str(cars_per_loop))
    
        self.entry_sc.delete(0, "end")
        self.entry_sc.insert(0, str(cars_per_loop))
    
        self.entry_global_loop.delete(0, "end")
        self.entry_global_loop.insert(0, str(final_loops))
    
        self.log(
            f"✅计算完成: 总计需{total_cars}车, 共跑图{total_races}次。"
            f"分配为: {final_loops} 个大循环, 每轮跑图 {final_races_per_loop} 次, "
            f"动作 {cars_per_loop} 辆。"
        )
    
        self.save_config()

    # ==========================================
    # --- UI 布局设计 ---
    # ==========================================
    def setup_ui(self):
        self.top_container = ctk.CTkFrame(self, fg_color="transparent")
        self.top_container.pack(fill="x", padx=18, pady=(18, 10))

        self.config_frame = ctk.CTkFrame(self.top_container, fg_color="transparent")
        self.config_frame.pack(fill="x")

        def create_box(parent, title, btn_text, btn_cmd, btn_color, def_val):
            frame = ctk.CTkFrame(
                parent,
                width=210,
                height=300,
                corner_radius=12,
                border_width=1,
                border_color="#2B2B2B",
            )
            frame.pack_propagate(False)
            frame.pack(side="left", padx=8)

            ctk.CTkLabel(
                frame,
                text=title,
                font=ctk.CTkFont(weight="bold", size=20),
            ).pack(pady=(14, 10))

            btn = ctk.CTkButton(
                frame,
                text=btn_text,
                fg_color=btn_color,
                hover_color=btn_color,
                command=btn_cmd,
                width=140,
                height=38,
                corner_radius=10,
            )
            btn.pack(pady=8, padx=10)

            entry = ctk.CTkEntry(frame, width=95, height=34, justify="center", corner_radius=8)
            entry.insert(0, str(def_val))
            entry.pack(pady=8)

            lbl = ctk.CTkLabel(
                frame,
                text=self.t("progress_exec", current=0, total=def_val),
                text_color="#A0A0A0",
                font=ctk.CTkFont(size=16),
            )
            lbl.pack(pady=8)
            return frame, btn, entry, lbl

        def create_next_step(parent, var_checked, def_step, box_h=300):
            frame = ctk.CTkFrame(parent, width=120, height=box_h, corner_radius=12, border_width=1, border_color="#2B2B2B")
            frame.pack(side="left", padx=4)
            frame.pack_propagate(False)

            ctk.CTkLabel(
                frame,
                text=self.t("next_step"),
                font=ctk.CTkFont(size=18, weight="bold"),
                text_color="#5DADE2",
            ).pack(pady=(55, 10))

            entry = ctk.CTkEntry(frame, width=60, height=34, justify="center", corner_radius=8)
            entry.insert(0, str(def_step))
            entry.pack(pady=6)

            chk = ctk.CTkCheckBox(frame, text=self.t("continue"), variable=var_checked, width=60)
            chk.pack(pady=8)

            return frame, entry, chk

        self.var_chk1 = ctk.BooleanVar(value=self.config["chk_1"])
        self.var_chk2 = ctk.BooleanVar(value=self.config["chk_2"])
        self.var_chk3 = ctk.BooleanVar(value=self.config["chk_3"])
        self.var_chk4 = ctk.BooleanVar(value=self.config.get("chk_4", True))

        # 고해상도/4K 이미지 인식 보정 옵션
        self.var_high_res_image_fix = ctk.BooleanVar(
            value=self.config.get("high_res_image_fix", False)
        )
        # 이미지 실패 로그 옵션
        self.var_image_debug_log = ctk.BooleanVar(
            value=self.config.get("image_debug_log", False)
        )

        self.var_always_on_top = ctk.BooleanVar(
            value=self.config.get("always_on_top", True)
        )

        box_race, self.btn_race, self.entry_race, self.lbl_race = create_box(
            self.config_frame,
            self.t("race_title"),
            self.t("start"),
            lambda: self.start_pipeline("race"),
            "#1F6AA5",
            self.config.get("race_count", 99),
        )
        self.entry_share = ctk.CTkEntry(box_race, width=130, justify="center", placeholder_text=self.t("share_placeholder"))
        self.entry_share.insert(0, self.config.get("share_code", "890169683"))
        self.entry_share.pack(pady=4)

        self.race_mode_values = {
            "맵 선택 모드 1: 공유코드 입력": 1,
            "맵 선택 모드 2: 첫번째 즐겨찾기 맵 사용": 2,
            "맵 선택 모드 3: 마지막 플레이 맵 사용": 3,
        }

        self.opt_race_mode = ctk.CTkOptionMenu(
            box_race,
            values=list(self.race_mode_values.keys()),
            width=170,
            height=20,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color="#2563EB",
            button_color="#1D4ED8",
            button_hover_color="#1E40AF",
        )
        
        saved_race_mode = self.config.get("race_mode", 1)

        if saved_race_mode == 2:
            self.opt_race_mode.set("맵 선택 모드 2: 첫번째 즐겨찾기 맵 사용")
        elif saved_race_mode == 3:
            self.opt_race_mode.set("맵 선택 모드 3: 마지막 플레이 맵 사용")
        else:
            self.opt_race_mode.set("맵 선택 모드 1: 공유코드 입력")

        self.opt_race_mode.pack(pady=4)

        self.race_car_mode_values = {
            "차량 모드 1: 이미지 탐색 차량 탑승": 1,
            "차량 모드 2: 즐겨찾기 차량 바로 탑승": 2,
        }

        self.opt_race_car_mode = ctk.CTkOptionMenu(
            box_race,
            values=list(self.race_car_mode_values.keys()),
            width=170,
            height=20,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color="#7C3AED",
            button_color="#6D28D9",
            button_hover_color="#5B21B6",
        )

        saved_race_car_mode = self.config.get("race_car_mode", 1)

        if saved_race_car_mode == 2:
            self.opt_race_car_mode.set("차량 모드 2: 즐겨찾기 차량 바로 탑승")
        else:
            self.opt_race_car_mode.set("차량 모드 1: 이미지 탐색 차량 탑승")

        self.opt_race_car_mode.pack(pady=4)

        self.next_frame1, self.entry_next1, self.chk1 = create_next_step(
            self.config_frame, self.var_chk1, self.config.get("next_1", 2)
        )

        box_car, self.btn_car, self.entry_car, self.lbl_car = create_box(
            self.config_frame,
            self.t("buy_title"),
            self.t("start"),
            lambda: self.start_pipeline("buy"),
            "#2EA043",
            self.config.get("buy_count", 30),
        )
        self.entry_car.bind("<KeyRelease>", self.sync_buy_to_sell)

        self.next_frame2, self.entry_next2, self.chk2 = create_next_step(
            self.config_frame, self.var_chk2, self.config.get("next_2", 3)
        )

        self.box_cj = ctk.CTkFrame(
            self.config_frame,
            width=360,
            height=300,
            corner_radius=12,
            border_width=1,
            border_color="#2B2B2B",
        )
        self.box_cj.pack_propagate(False)
        self.box_cj.pack(side="left", padx=8)

        top_cj = ctk.CTkFrame(self.box_cj, fg_color="transparent")
        top_cj.pack(fill="x", pady=10)

        left_cj = ctk.CTkFrame(top_cj, fg_color="transparent")
        left_cj.pack(side="left", padx=10)

        ctk.CTkLabel(left_cj, text=self.t("wheelspin_title"), font=ctk.CTkFont(weight="bold", size=20)).pack(pady=(0, 8))

        self.btn_cj = ctk.CTkButton(
            left_cj,
            text=self.t("start"),
            width=120,
            height=38,
            corner_radius=10,
            fg_color="#8E44AD",
            hover_color="#8E44AD",
            command=lambda: self.start_pipeline("cj"),
        )
        self.btn_cj.pack(pady=5)

        self.entry_cj = ctk.CTkEntry(left_cj, width=95, height=34, justify="center", corner_radius=8)
        self.entry_cj.insert(0, str(self.config.get("cj_count", 30)))
        self.entry_cj.pack(pady=5)

        self.lbl_cj = ctk.CTkLabel(
            left_cj,
            text=self.t("progress_exec", current=0, total=self.config.get("cj_count", 30)),
            text_color="#A0A0A0",
            font=ctk.CTkFont(size=14),
        )
        self.lbl_cj.pack(pady=(2, 8))

        dir_frame = ctk.CTkFrame(left_cj, fg_color="transparent")
        dir_frame.pack(pady=4)

        for text, val in [("↑", "up"), ("↓", "down"), ("←", "left"), ("→", "right")]:
            ctk.CTkButton(
                dir_frame,
                text=text,
                width=30,
                height=28,
                corner_radius=8,
                command=lambda x=val: self.add_skill_dir(x),
            ).pack(side="left", padx=2)

        ctk.CTkButton(
            left_cj,
            text=self.t("clear_matrix"),
            width=90,
            height=28,
            corner_radius=8,
            fg_color="#C0392B",
            hover_color="#A93226",
            command=self.clear_skill_dir,
        ).pack(pady=8)

        self.grid_frame = ctk.CTkFrame(top_cj, fg_color="transparent")
        self.grid_frame.pack(side="right", padx=12)

        self.grid_labels = [[None] * 4 for _ in range(4)]
        for r in range(4):
            for c in range(4):
                lbl = ctk.CTkLabel(
                    self.grid_frame,
                    text="",
                    width=28,
                    height=28,
                    corner_radius=5,
                    fg_color="#444444",
                )
                lbl.grid(row=r, column=c, padx=4, pady=4)
                self.grid_labels[r][c] = lbl
        ctk.CTkLabel(
            self.grid_frame,
            text=self.t("skill_tree"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#A0A0A0",
        ).grid(row=4, column=0, columnspan=4, pady=(8, 0))

        self.next_frame3, self.entry_next3, self.chk3 = create_next_step(
            self.config_frame, self.var_chk3, self.config.get("next_3", 4)
        )

        box_sc, self.btn_sc, self.entry_sc, self.lbl_sc = create_box(
            self.config_frame,
            self.t("sell_title"),
            self.t("start_danger"),
            lambda: self.start_pipeline("sell"),
            "#D97706",
            self.config.get("sc_count", 30),
        )
        # ====== 【新增】：移除车辆模式下拉选择 ======
        self.sell_mode_values = {
            self.t("sell_mode_1"): 1,
            self.t("sell_mode_2"): 2,
        }
        self.opt_sell_mode = ctk.CTkOptionMenu(
            box_sc,
            values=list(self.sell_mode_values.keys()),
            width=180,
            height=28,
            corner_radius=6,
            font=ctk.CTkFont(size=12),
            fg_color="#D97706",
            button_color="#B96705",
            button_hover_color="#995704"
        )
        # 读取配置，默认选模式1
        saved_mode = self.config.get("sell_mode", 1)
        if str(saved_mode) == "1" or "模式1" in str(saved_mode) or "Mode 1" in str(saved_mode) or "모드 1" in str(saved_mode):
            self.opt_sell_mode.set(self.t("sell_mode_1"))
        else:
            self.opt_sell_mode.set(self.t("sell_mode_2"))
            
        self.opt_sell_mode.pack(pady=4)
        # ==========================================
        self.next_frame4, self.entry_next4, self.chk4 = create_next_step(
        self.config_frame, self.var_chk4, self.config.get("next_4", 1)
        )
        # ====== 抽离到底部的全局设置栏 (放在上方) ======
        # 【修改1】把 self.top_container 改成了 self
        self.global_settings_frame = ctk.CTkFrame(self, fg_color="#2B2B2B", height=45, corner_radius=10)
        # 【修改2】加上了 padx=18，让它和上下边缘对齐
        self.global_settings_frame.pack(fill="x", padx=18, pady=(15, 0))
        self.global_settings_frame.pack_propagate(False)
        ctk.CTkLabel(
            self.global_settings_frame, 
            text=self.t("global_settings"), 
            font=ctk.CTkFont(weight="bold", size=15), 
            text_color="#F1C40F"
        ).pack(side="left", padx=(15, 20))
        ctk.CTkLabel(self.global_settings_frame, text=self.t("global_loops")).pack(side="left", padx=(10, 5))
        self.entry_global_loop = ctk.CTkEntry(self.global_settings_frame, width=70, height=28, justify="center")
        self.entry_global_loop.insert(0, str(self.config.get("global_loops", 10)))
        self.entry_global_loop.pack(side="left", padx=(0, 20))
        self.var_auto_restart = ctk.BooleanVar(value=self.config.get("auto_restart", True))
        self.cb_high_res_image_fix = ctk.CTkCheckBox(
            self.global_settings_frame,
            text="고해상도 이미지 보정(UWQHD, 4K 등)",
            variable=self.var_high_res_image_fix,
            command=self.save_config
        )
        self.cb_image_debug_log = ctk.CTkCheckBox(
            self.global_settings_frame,
            text="이미지 인식 실패 디버그 로그",
            variable=self.var_image_debug_log,
            command=self.save_config
        )
        self.cb_image_debug_log.pack(side="left", padx=(0, 20))
        self.cb_high_res_image_fix.pack(side="left", padx=(0, 20))
        self.cb_auto_restart = ctk.CTkCheckBox(self.global_settings_frame, text=self.t("auto_restart"), variable=self.var_auto_restart)
        self.cb_auto_restart.pack(side="left", padx=(10, 20))
        ctk.CTkLabel(self.global_settings_frame, text=self.t("restart_cmd")).pack(side="left", padx=(10, 5))
        self.le_restart_cmd = ctk.CTkEntry(self.global_settings_frame, width=250, height=28)
        self.le_restart_cmd.insert(0, self.config.get("restart_cmd", "start steam://run/2483190"))
        self.le_restart_cmd.pack(side="left", padx=(0, 20))
        self.btn_test_boot = ctk.CTkButton(
            self.global_settings_frame,
            text="시작 테스트",
            width=90,
            height=28,
            fg_color="#444444",
            hover_color="#555555",
            command=self.start_test_boot
        )
        self.btn_test_boot.pack(side="left", padx=(0, 15))
        ctk.CTkLabel(self.global_settings_frame, text=self.t("language_label")).pack(side="left", padx=(0, 5))
        self.var_ui_language = ctk.StringVar(value=LANGUAGE_LABELS.get(self.ui_language, LANGUAGE_LABELS[DEFAULT_UI_LANGUAGE]))
        self.cmb_ui_language = ctk.CTkOptionMenu(
            self.global_settings_frame,
            values=list(LANGUAGE_OPTIONS.keys()),
            variable=self.var_ui_language,
            width=95,
            command=self.on_language_change,
        )
        self.cmb_ui_language.pack(side="left", padx=(0, 10))
        
        # =================================

        # ====== 레이스 세부설정 ======
        self.race_detail_frame = ctk.CTkFrame(self, fg_color="#2B2B2B", height=45, corner_radius=10)
        self.race_detail_frame.pack(fill="x", padx=18, pady=(10, 0))
        self.race_detail_frame.pack_propagate(False)

        ctk.CTkLabel(
            self.race_detail_frame,
            text="레이스 세부설정",
            font=ctk.CTkFont(weight="bold", size=15),
            text_color="#F59E0B"
        ).pack(side="left", padx=(15, 20))

        ctk.CTkLabel(self.race_detail_frame, text="1판 획득 SP:").pack(side="left", padx=(0, 5))
        self.entry_race_sp_per_run = ctk.CTkEntry(self.race_detail_frame, width=55, height=28, justify="center")
        self.entry_race_sp_per_run.insert(0, str(self.config.get("race_sp_per_run", 10)))
        self.entry_race_sp_per_run.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(self.race_detail_frame, text="완주 감지 시작(초):").pack(side="left", padx=(0, 5))
        self.entry_finish_detect_start_sec = ctk.CTkEntry(self.race_detail_frame, width=60, height=28, justify="center")
        self.entry_finish_detect_start_sec.insert(0, str(self.config.get("finish_detect_start_sec", 0)))
        self.entry_finish_detect_start_sec.pack(side="left", padx=(0, 15))
        
        ctk.CTkLabel(self.race_detail_frame, text="완주 감지 최대(초):").pack(side="left", padx=(0, 5))
        self.entry_finish_detect_max_sec = ctk.CTkEntry(self.race_detail_frame, width=60, height=28, justify="center")
        self.entry_finish_detect_max_sec.insert(0, str(self.config.get("finish_detect_max_sec", 120)))
        self.entry_finish_detect_max_sec.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(self.race_detail_frame, text="계산 여유 레이스:").pack(side="left", padx=(0, 5))
        self.entry_race_count_bonus = ctk.CTkEntry(self.race_detail_frame, width=50, height=28, justify="center")
        self.entry_race_count_bonus.insert(0, str(self.config.get("race_count_bonus", 1)))
        self.entry_race_count_bonus.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(self.race_detail_frame, text="루프 기준 레이스:").pack(side="left", padx=(0, 5))
        self.entry_loop_race_limit = ctk.CTkEntry(self.race_detail_frame, width=55, height=28, justify="center")
        self.entry_loop_race_limit.insert(0, str(self.config.get("loop_race_limit", 100)))
        self.entry_loop_race_limit.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(self.race_detail_frame, text="루프당 최대 SP:").pack(side="left", padx=(0, 5))
        self.entry_max_sp_per_loop = ctk.CTkEntry(self.race_detail_frame, width=55, height=28, justify="center")
        self.entry_max_sp_per_loop.insert(0, str(self.config.get("max_sp_per_loop", 990)))
        self.entry_max_sp_per_loop.pack(side="left", padx=(0, 15))
        
        ctk.CTkLabel(self.race_detail_frame, text="루프 여유 보정:").pack(side="left", padx=(0, 5))
        self.entry_loop_count_bonus = ctk.CTkEntry(self.race_detail_frame, width=50, height=28, justify="center")
        self.entry_loop_count_bonus.insert(0, str(self.config.get("loop_count_bonus", 0)))
        self.entry_loop_count_bonus.pack(side="left", padx=(0, 15))

        # ====== 新增：智能计算分配工具栏 (放在下方) ======
        # 【修改1】把 self.top_container 改成了 self
        self.calc_frame = ctk.CTkFrame(self, fg_color="#2B2B2B", height=45, corner_radius=10)
        # 【修改2】加上了 padx=18，让它和上下边缘对齐
        self.calc_frame.pack(fill="x", padx=18, pady=(10, 0))
        self.calc_frame.pack_propagate(False)
        ctk.CTkLabel(
            self.calc_frame, 
            text=self.t("calculator"), 
            font=ctk.CTkFont(weight="bold", size=15), 
            text_color="#2EA043"
        ).pack(side="left", padx=(15, 20))
        ctk.CTkLabel(self.calc_frame, text="CR:").pack(side="left", padx=(0, 5))
        self.entry_calc_a = ctk.CTkEntry(self.calc_frame, width=110, height=28, placeholder_text=self.t("empty_no_calc"))
        self.entry_calc_a.insert(0, self.config.get("calc_a", ""))
        self.entry_calc_a.pack(side="left", padx=(0, 15))
        ctk.CTkLabel(self.calc_frame, text=self.t("cost_per_car")).pack(side="left", padx=(0, 5))
        self.entry_calc_b = ctk.CTkEntry(self.calc_frame, width=70, height=28)
        self.entry_calc_b.insert(0, self.config.get("calc_b", "81700"))
        self.entry_calc_b.pack(side="left", padx=(0, 15))
        ctk.CTkLabel(self.calc_frame, text=self.t("sp_per_car")).pack(side="left", padx=(0, 5))
        self.entry_calc_c = ctk.CTkEntry(self.calc_frame, width=50, height=28)
        self.entry_calc_c.insert(0, self.config.get("calc_c", "30"))
        self.entry_calc_c.pack(side="left", padx=(0, 15))
        ctk.CTkButton(
            self.calc_frame,
            text=self.t("calculate_apply"),
            width=90,
            height=28,
            fg_color="#D35400",
            hover_color="#A04000",
            command=self.auto_calculate_pipeline
        ).pack(side="left", padx=(0, 15))
        
        # 动态限制输入框长度（只允许数字并截断）
        def limit_len(evt, widget, max_l):
            val = "".join(c for c in widget.get() if c.isdigit())
            if len(val) > max_l:
                val = val[:max_l]
            if widget.get() != val:
                widget.delete(0, "end")
                widget.insert(0, val)
        self.entry_calc_a.bind("<KeyRelease>", lambda e: limit_len(e, self.entry_calc_a, 10))
        self.entry_calc_b.bind("<KeyRelease>", lambda e: limit_len(e, self.entry_calc_b, 7))
        self.entry_calc_c.bind("<KeyRelease>", lambda e: limit_len(e, self.entry_calc_c, 2))
        # ==========================================
        #ctk.CTkLabel(self.global_settings_frame, text="图片原宽（不要修改）:").pack(side="left", padx=(10, 5))
        #self.entry_base_w = ctk.CTkEntry(self.global_settings_frame, width=70, height=28, justify="center")
        #self.entry_base_w.insert(0, str(self.config.get("base_width", 2560)))
        #self.entry_base_w.pack(side="left", padx=(0, 20))

        self.entry_next1.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next1, 2))
        self.entry_next2.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next2, 3))
        self.entry_next3.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next3, 4))
        self.entry_next4.bind("<FocusOut>", lambda e: self.normalize_step_entry(self.entry_next4, 1))

        if not self.entry_sc.get().strip():
            self.entry_sc.insert(0, "30")

        # ====== Telegram 通知设置栏 ======
        self.telegram_frame = ctk.CTkFrame(self, fg_color="#2B2B2B", height=45, corner_radius=10)
        self.telegram_frame.pack(fill="x", padx=18, pady=(10, 0))
        self.telegram_frame.pack_propagate(False)
        ctk.CTkLabel(
            self.telegram_frame,
            text=self.t("telegram_settings"),
            font=ctk.CTkFont(weight="bold", size=15),
            text_color="#229ED9"
        ).pack(side="left", padx=(15, 10))
        self.var_telegram_enabled = ctk.BooleanVar(value=self.config.get("telegram_enabled", False))
        ctk.CTkCheckBox(self.telegram_frame, text=self.t("telegram_enabled"), variable=self.var_telegram_enabled).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(self.telegram_frame, text=self.t("telegram_bot_token")).pack(side="left", padx=(0, 3))
        self.entry_telegram_token = ctk.CTkEntry(self.telegram_frame, width=200, height=28, show="*")
        self.entry_telegram_token.insert(0, self.config.get("telegram_bot_token", ""))
        self.entry_telegram_token.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(self.telegram_frame, text=self.t("telegram_chat_id")).pack(side="left", padx=(0, 3))
        self.entry_telegram_chat_id = ctk.CTkEntry(self.telegram_frame, width=140, height=28)
        self.entry_telegram_chat_id.insert(0, self.config.get("telegram_chat_id", ""))
        self.entry_telegram_chat_id.pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            self.telegram_frame,
            text=self.t("telegram_test"),
            width=50,
            height=28,
            fg_color="#229ED9",
            hover_color="#1A8BC4",
            command=self.test_telegram
        ).pack(side="left", padx=(0, 15))
        # 알림 타입 체크박스
        self.var_telegram_fatal = ctk.BooleanVar(value=self.config.get("telegram_on_fatal", True))
        self.var_telegram_step = ctk.BooleanVar(value=self.config.get("telegram_on_step", True))
        self.var_telegram_loop = ctk.BooleanVar(value=self.config.get("telegram_on_loop", True))
        self.var_telegram_finish = ctk.BooleanVar(value=self.config.get("telegram_on_finish", True))
        ctk.CTkCheckBox(self.telegram_frame, text=self.t("telegram_on_fatal"), variable=self.var_telegram_fatal).pack(side="left", padx=(5, 2))
        ctk.CTkCheckBox(self.telegram_frame, text=self.t("telegram_on_step"), variable=self.var_telegram_step).pack(side="left", padx=(5, 2))
        ctk.CTkCheckBox(self.telegram_frame, text=self.t("telegram_on_loop"), variable=self.var_telegram_loop).pack(side="left", padx=(5, 2))
        ctk.CTkCheckBox(self.telegram_frame, text=self.t("telegram_on_finish"), variable=self.var_telegram_finish).pack(side="left", padx=(5, 2))
        # ==========================================

        # === 全新的横向迷你UI设计 ===
        self.mini_frame = ctk.CTkFrame(self, fg_color="#1E1E1E", corner_radius=10)

        # 1. 日志区 (最左侧，占据主要伸缩空间)
        self.mini_log_box = ctk.CTkTextbox(self.mini_frame, state="disabled", wrap="word", font=ctk.CTkFont(size=13), fg_color="#2B2B2B")
        self.mini_log_box.pack(side="left", fill="both", expand=True, padx=(10, 5), pady=10)

        # 2. 信息区 (垂直排列任务状态和耗时)
        self.mini_info_frame = ctk.CTkFrame(self.mini_frame, fg_color="transparent")
        self.mini_info_frame.pack(side="left", fill="y", padx=5, pady=10)

        self.lbl_mini_task = ctk.CTkLabel(self.mini_info_frame, text=self.t("current_task_waiting"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498DB")
        self.lbl_mini_task.pack(pady=(5, 2), anchor="w")

        self.lbl_mini_prog = ctk.CTkLabel(self.mini_info_frame, text=self.t("task_progress_zero"), font=ctk.CTkFont(size=13))
        self.lbl_mini_prog.pack(pady=2, anchor="w")

        self.lbl_mini_loop = ctk.CTkLabel(self.mini_info_frame, text=self.t("loop_zero"), font=ctk.CTkFont(size=13))
        self.lbl_mini_loop.pack(pady=2, anchor="w")

        self.lbl_mini_time = ctk.CTkLabel(self.mini_info_frame, text=self.t("total_time_zero"), font=ctk.CTkFont(size=13))
        self.lbl_mini_time.pack(pady=2, anchor="w")
        # 3. 按钮区 (靠右排列)
        self.btn_mini_stop = ctk.CTkButton(self.mini_frame, text=self.t("mini_stop"), fg_color="#DA3633", hover_color="#B02A37", width=90, font=ctk.CTkFont(weight="bold"), command=self.stop_all)
        self.btn_mini_stop.pack(side="left", fill="y", padx=5, pady=10)

        self.btn_mini_pause = ctk.CTkButton(
            self.mini_frame,
            text="⏸ 일시정지 (F9)",
            fg_color="#B8860B",
            hover_color="#DAA520",
            width=110,
            font=ctk.CTkFont(weight="bold"),
            command=self.toggle_pause,
        )
        self.btn_mini_pause.pack(side="left", fill="y", padx=5, pady=10)

        self.btn_mini_support = ctk.CTkButton(self.mini_frame, text=self.t("mini_support"), fg_color="#F97316", hover_color="#EA580C", width=60, font=ctk.CTkFont(weight="bold"), command=self.open_support_window)
        self.btn_mini_support.pack(side="left", fill="y", padx=(5, 10), pady=10)


        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent", height=200)
        self.bottom_frame.pack(fill="both", expand=True, padx=18, pady=(6, 12))

        self.hotkey_button_frame = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        self.hotkey_button_frame.pack(side="left", padx=6)

        self.btn_stop = ctk.CTkButton(
            self.hotkey_button_frame,
            text="⏹ 중지 (F8)",
            fg_color="#3A3A3A",
            hover_color="#4A4A4A",
            width=180,
            height=50,
            corner_radius=12,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self.stop_all,
        )
        self.btn_stop.pack(pady=(0, 6))

        self.btn_pause_hint = ctk.CTkButton(
            self.hotkey_button_frame,
            text="⏸ 일시정지/재개 (F9)",
            fg_color="#3A3A3A",
            hover_color="#4A4A4A",
            width=180,
            height=50,
            corner_radius=12,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self.toggle_pause,
        )
        self.btn_pause_hint.pack()

        self.btn_topmost = ctk.CTkButton(
            self.hotkey_button_frame,
            text="📌 항상 위 OFF",
            fg_color="#3A3A3A",
            hover_color="#4A4A4A",
            width=180,
            height=45,
            corner_radius=12,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.toggle_always_on_top
        )
        self.btn_topmost.pack(pady=(6, 0))

        self.log_box = ctk.CTkTextbox(
            self.bottom_frame,
            state="disabled",
            wrap="word",
            corner_radius=12,
            height=120,
            font=ctk.CTkFont(size=18),
        )
        self.log_box.pack(side="left", fill="both", expand=True, padx=8)

        self.btn_support = ctk.CTkButton(
            self,
            text=self.t("support_update"),
            fg_color="#F97316",
            hover_color="#EA580C",
            height=42,
            corner_radius=12,
            font=ctk.CTkFont(weight="bold", size=15),
            command=self.open_support_window,
        )
        self.btn_support.pack(fill="x", padx=18, pady=(6, 12))
        self.sync_buy_to_sell()

        self.apply_always_on_top_state(log_message=True)

    def apply_always_on_top_state(self, log_message=True):
        enabled = bool(self.config.get("always_on_top", False))

        self.attributes("-topmost", enabled)

        if hasattr(self, "var_always_on_top"):
            self.var_always_on_top.set(enabled)

        if hasattr(self, "btn_topmost"):
            if enabled:
                self.btn_topmost.configure(
                    text="📌 항상 위 ON",
                    fg_color="#2563EB",
                    hover_color="#1D4ED8"
                )
            else:
                self.btn_topmost.configure(
                    text="📌 항상 위 OFF",
                    fg_color="#3A3A3A",
                    hover_color="#4A4A4A"
                )

        if log_message:
            self.log("매크로 창 항상 위에 표시: ON" if enabled else "매크로 창 항상 위에 표시: OFF")


    def toggle_always_on_top(self):
        current = bool(self.config.get("always_on_top", True))
        new_value = not current
    
        self.config["always_on_top"] = new_value
    
        if hasattr(self, "var_always_on_top"):
            self.var_always_on_top.set(new_value)
    
        self.apply_always_on_top_state(log_message=True)
        self.save_config()

            
        #ocr加载 
    
    def open_support_window(self):
        if self.support_win is not None and self.support_win.winfo_exists():
            self.support_win.focus()
            return

        self.support_win = ctk.CTkToplevel(self)
        self.support_win.title(self.t("support_title"))
        self.support_win.geometry("340x520")
        self.support_win.attributes("-topmost", True)
        self.support_win.resizable(False, False)

        try:
            icon_path = get_asset_path("icon.ico")
            if icon_path:
                self.support_win.iconbitmap(icon_path)
        except Exception:
            pass

        self.support_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 340) // 2
        y = self.winfo_y() + (self.winfo_height() - 520) // 2
        self.support_win.geometry(f"+{x}+{y}")

        ctk.CTkLabel(
            self.support_win,
            text=self.t("support_header"),
            font=ctk.CTkFont(weight="bold", size=18),
            text_color="#F97316",
        ).pack(pady=(20, 6))

        ctk.CTkLabel(
            self.support_win,
            text=self.t("support_desc"),
            font=ctk.CTkFont(size=12),
        ).pack(pady=4)

        qr_path = get_asset_path("qrcode.png")
        try:
            if qr_path and os.path.exists(qr_path):
                img = Image.open(qr_path)
                qr_img = ctk.CTkImage(light_image=img, size=(210, 210))
                qr_label = ctk.CTkLabel(self.support_win, text="", image=qr_img)
                qr_label.image = qr_img
                qr_label.pack(pady=10)
            else:
                ctk.CTkLabel(self.support_win, text=self.t("qr_missing"), text_color="gray").pack(pady=40)
        except Exception:
            ctk.CTkLabel(self.support_win, text=self.t("qr_failed"), text_color="gray").pack(pady=40)

        ctk.CTkButton(
            self.support_win,
            text=self.t("sponsor_page"),
            fg_color="#8E44AD",
            hover_color="#7D3C98",
            command=lambda: webbrowser.open("https://ifdian.net/a/yousto"),
        ).pack(pady=5)

        ctk.CTkFrame(self.support_win, height=2, fg_color="#333333").pack(fill="x", padx=20, pady=10)

        self.lbl_version = ctk.CTkLabel(
            self.support_win,
            text=self.t("current_version", version=CURRENT_VERSION),
            text_color="gray",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_version.pack()

        def check_update_logic():
            self.ui_call(self.lbl_version.configure, text=self.t("connecting_github"), text_color="#3498DB")
            try:
                url = "https://raw.githubusercontent.com/YOUSTHEONE/FH6Auto/refs/heads/main/version.json"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    remote_ver = data.get("version", "0.0.0")
                    remote_url = data.get("url", "")

                    if parse_version(remote_ver) > parse_version(CURRENT_VERSION):
                        if remote_url.startswith("https://github.com/YOUSTHEONE/") or remote_url.startswith("https://ifdian.net/"):
                            self.ui_call(
                                self.lbl_version.configure,
                                text=self.t("new_version", version=remote_ver),
                                text_color="#2EA043",
                            )
                            webbrowser.open(remote_url)
                        else:
                            self.ui_call(
                                self.lbl_version.configure,
                                text=self.t("untrusted_update"),
                                text_color="#DA3633",
                            )
                    else:
                        self.ui_call(
                            self.lbl_version.configure,
                            text=self.t("latest_version", version=CURRENT_VERSION),
                            text_color="gray",
                        )
                else:
                    self.ui_call(
                        self.lbl_version.configure,
                        text=self.t("update_server_failed"),
                        text_color="#DA3633",
                    )
            except Exception:
                self.ui_call(
                    self.lbl_version.configure,
                    text=self.t("update_network_failed"),
                    text_color="#DA3633",
                )

        btn_frame = ctk.CTkFrame(self.support_win, fg_color="transparent")
        btn_frame.pack(pady=6)

        ctk.CTkButton(
            btn_frame,
            text=self.t("check_update"),
            width=100,
            height=30,
            fg_color="#444444",
            hover_color="#555555",
            command=lambda: threading.Thread(target=check_update_logic, daemon=True).start(),
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="원본 GitHub",
            width=120,
            height=30,
            fg_color="#2EA043",
            hover_color="#238636",
            command=lambda: webbrowser.open("https://github.com/YOUSTHEONE/FH6Auto"),
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="한국어 GitHub",
            width=120,
            height=30,
            fg_color="#0969DA",
            hover_color="#0A58CA",
            command=lambda: webbrowser.open("https://github.com/ekarose0/FH6-WheelSpin-Auto"),
        ).pack(side="left", padx=5)
        
    def update_timer(self):
        if not self.is_running:
            return
        elapsed = int(time.time() - self.start_time)
        hrs = elapsed // 3600
        mins = (elapsed % 3600) // 60
        secs = elapsed % 60
        time_str = self.t("total_time", time=f"{hrs:02d}:{mins:02d}:{secs:02d}")
        try:
            self.lbl_mini_time.configure(text=time_str)
        except Exception: pass
        
        if self.is_running:
            self.after(1000, self.update_timer)

    def update_running_ui(self, task_name="", current_val=0, max_val=0):
        try:
            if task_name:
                self.ui_call(self.lbl_mini_task.configure, text=self.t("current_task", task=self.task_text(task_name)))
            if max_val > 0:
                self.ui_call(self.lbl_mini_prog.configure, text=self.t("run_progress", current=current_val, total=max_val))
        except Exception:
            pass

    def set_pause_button_state(self, paused=False, requested=False):
        # 미니 UI의 일시정지 버튼 상태를 변경
        if not hasattr(self, "btn_mini_pause"):
            return

        if requested:
            self.ui_call(
                self.btn_mini_pause.configure,
                text="⏳ 정지 요청 중...\n현재 작업 완료 후 정지",
                fg_color="#D97706",
                hover_color="#B96705",
                state="normal"
            )
        elif paused:
            self.ui_call(
                self.btn_mini_pause.configure,
                text="▶ 재개 (F9)",
                fg_color="#2EA043",
                hover_color="#238636",
                state="normal"
            )
        else:
            self.ui_call(
                self.btn_mini_pause.configure,
                text="⏸ 일시정지 (F9)",
                fg_color="#B8860B",
                hover_color="#DAA520",
                state="normal"
            )

    def toggle_pause(self):
        # F9 또는 버튼으로 일시정지/재개 전환
        if not self.is_running:
            return

        # 정지 예약 상태에서 취소
        if self.pause_requested and not self.is_paused:
        
            self.pause_requested = False
        
            self.set_pause_button_state(paused=False)
        
            self.log("▶ 일시정지 요청 취소")
        
            return
        
        if self.is_paused:
            self.is_paused = False
            self.pause_requested = False
            self.set_pause_button_state(paused=False)
            self.log("▶ 재개합니다.")
        else:
            self.pause_requested = True
            self.set_pause_button_state(requested=True)
            self.log("⏸ 일시정지 요청됨. 현재 1회 작업 완료 후 메뉴로 복귀합니다.")

    def check_safe_pause(self):
        # 각 1회 작업이 끝난 뒤 호출됨
        if not self.pause_requested:
            return False

        self.pause_requested = False
        self.is_paused = True

        self.log("⏸ 현재 작업 1회 완료. 메인 메뉴로 복귀 후 일시정지합니다.")

        # 현재 메뉴/차량 화면/업그레이드 화면 등 어디든 최대한 메인 메뉴로 복귀
        self.enter_menu()

        self.set_pause_button_state(paused=True)
        return True

    # ==========================================
    # --- 核心操作与流程控制 ---
    # ==========================================
    def hw_key_down(self, key):
        if getattr(self, "use_win32_input", False):
            if self.win32_key_down(key):
                return
        if key not in DIK_CODES:
            return
        scan_code, extended = DIK_CODES[key]
        flags = 0x0008 | (0x0001 if extended else 0)
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
        x = Input(ctypes.c_ulong(1), ii_)
        SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    def hw_key_up(self, key):
        if getattr(self, "use_win32_input", False):
            if self.win32_key_up(key):
                return
        if key not in DIK_CODES:
            return
        scan_code, extended = DIK_CODES[key]
        flags = 0x000A | (0x0001 if extended else 0)
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
        x = Input(ctypes.c_ulong(1), ii_)
        SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    def get_forza_hwnd(self):
        try:
            import win32gui
            return win32gui.FindWindow(None, "Forza Horizon 6")
        except Exception:
            return None
    
    def win32_vk(self, key):
        import win32con

        vk_map = {
            "enter": win32con.VK_RETURN,
            "esc": win32con.VK_ESCAPE,
            "up": win32con.VK_UP,
            "down": win32con.VK_DOWN,
            "left": win32con.VK_LEFT,
            "right": win32con.VK_RIGHT,
            "space": win32con.VK_SPACE,
            "backspace": win32con.VK_BACK,
            "tab": win32con.VK_TAB,
            "pagedown": win32con.VK_NEXT,
            "pageup": win32con.VK_PRIOR,
            "home": win32con.VK_HOME,
            "end": win32con.VK_END,
            "delete": win32con.VK_DELETE,

            "w": ord("W"),
            "a": ord("A"),
            "s": ord("S"),
            "d": ord("D"),
            "x": ord("X"),
            "y": ord("Y"),

            "0": ord("0"),
            "1": ord("1"),
            "2": ord("2"),
            "3": ord("3"),
            "4": ord("4"),
            "5": ord("5"),
            "6": ord("6"),
            "7": ord("7"),
            "8": ord("8"),
            "9": ord("9"),
        }

        return vk_map.get(str(key).lower())

    def win32_key_down(self, key):
        try:
            import win32gui
            import win32con

            hwnd = self.get_forza_hwnd()
            if not hwnd:
                self.log("포르자 창을 찾지 못함")
                return False

            vk = self.win32_vk(key)
            if not vk:
                return False

            win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
            return True

        except Exception as e:
            self.log(f"Win32 KeyDown 실패: {e}")
            return False

    def win32_key_up(self, key):
        try:
            import win32gui
            import win32con

            hwnd = self.get_forza_hwnd()
            if not hwnd:
                self.log("포르자 창을 찾지 못함")
                return False

            vk = self.win32_vk(key)
            if not vk:
                return False

            win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)
            return True

        except Exception as e:
            self.log(f"Win32 KeyUp 실패: {e}")
            return False
        
    def win32_press(self, key, hold=0.12):
        try:
            import time

            if not self.win32_key_down(key):
                return False

            time.sleep(hold)

            if not self.win32_key_up(key):
                return False

            return True

        except Exception as e:
            self.log(f"Win32 입력 실패: {e}")
            return False

    def win32_press_digit_precise(self, digit, hold=0.08):
        try:
            import win32gui
            import win32con
            import win32api
    
            hwnd = self.get_forza_hwnd()
            if not hwnd:
                self.log("포르자 창을 찾지 못함")
                return False
    
            digit = str(digit)
            if digit not in "0123456789":
                return False
    
            vk = ord(digit)
            scan = win32api.MapVirtualKey(vk, 0)
    
            lparam_down = 1 | (scan << 16)
            lparam_up = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    
            win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, lparam_down)
            time.sleep(hold)
            win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, lparam_up)
    
            return True
    
        except Exception as e:
            self.log(f"Win32 숫자 입력 실패: {e}")
            return False
        
    def hw_press(self, key, delay=0.08):
        if not self.is_running:
            return

        if getattr(self, "use_win32_input", False):
            self.win32_press(key, hold=0.12)
            time.sleep(max(delay, 0.18))
            return

        self.hw_key_down(key)
        time.sleep(delay)
        self.hw_key_up(key)
    #副屏支持
    def hw_mouse_move(self, x, y):
        # 获取多显示器组成的整个“虚拟桌面”坐标和尺寸
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if width == 0 or height == 0:
            return
        # 映射到 0~65535 的绝对虚拟坐标系统
        calc_x = int((x - left) * 65535 / width)
        calc_y = int((y - top) * 65535 / height)
        # MOUSEEVENTF_MOVE = 0x0001, MOUSEEVENTF_ABSOLUTE = 0x8000, MOUSEEVENTF_VIRTUALDESK = 0x4000
        flags = 0x0001 | 0x8000 | 0x4000 
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.mi = MouseInput(calc_x, calc_y, 0, flags, 0, ctypes.pointer(extra))
        cmd = Input(ctypes.c_ulong(0), ii_)
        SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))
    
    def win32_click(self, pos, double=False):
        if not self.is_running or not pos:
            return False

        try:
            import win32gui
            import win32con
            import win32api

            hwnd = win32gui.FindWindow(None, "Forza Horizon 6")
            if not hwnd:
                self.log("포르자 창을 찾지 못함")
                return False
            
            try:
                client_rect = win32gui.GetClientRect(hwnd)
                client_origin = win32gui.ClientToScreen(hwnd, (0, 0))

                gx, gy = client_origin
                gw = client_rect[2]
                gh = client_rect[3]

                if gw > 1000 and gh > 600:
                    self.update_regions_by_window(gx, gy, gw, gh)
            except Exception:
                pass

            # 화면 절대좌표 → 포르자 클라이언트 내부 좌표
            x, y = int(pos[0]), int(pos[1])
            cx, cy = win32gui.ScreenToClient(hwnd, (x, y))

            lparam = win32api.MAKELONG(cx, cy)

            for _ in range(2 if double else 1):
                win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
                time.sleep(0.05)
                win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
                time.sleep(0.08)
                win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
                time.sleep(0.12)

            return True

        except Exception as e:
            self.log(f"Win32 클릭 실패: {e}")
            return False

    def game_click(self, pos, double=False):
        if not self.is_running or not pos:
            return

        # 백그라운드 모드: 실제 마우스 사용 금지
        if getattr(self, "use_win32_input", False):
            if not self.win32_click(pos, double=double):
                self.log("Win32 클릭 실패: 백그라운드 모드에서는 실제 마우스 클릭으로 대체하지 않습니다.")
            time.sleep(0.2)
            return

        # 기존 모드: 실제 마우스 사용
        x, y = int(pos[0]), int(pos[1])

        self.hw_mouse_move(x, y)
        time.sleep(0.2)

        for _ in range(2 if double else 1):
            pydirectinput.mouseDown()
            time.sleep(0.1)
            pydirectinput.mouseUp()
            time.sleep(0.1)

        time.sleep(0.3)

    def move_to_game_coord(self, x, y):
        """
        기존 물리 입력 모드에서만 마우스를 치우는 함수입니다.
        Win32 백그라운드 입력 모드에서는 실제 마우스를 움직이지 않습니다.
        """
        if getattr(self, "use_win32_input", False):
            return
    
        try:
            gx, gy, gw, gh = self.regions["全界面"]
            abs_x = gx + x
            abs_y = gy + y
            self.hw_mouse_move(abs_x, abs_y)
        except Exception:
            self.hw_mouse_move(x, y)

    def add_skill_dir(self, direction):
        self.config["skill_dirs"].append(direction)
        self.update_skill_grid()
        self.save_config()

    def clear_skill_dir(self):
        self.config["skill_dirs"].clear()
        self.update_skill_grid()
        self.save_config()

    def update_skill_grid(self):
        for r in range(4):
            for c in range(4):
                self.grid_labels[r][c].configure(fg_color="#333333")

        curr_r, curr_c = 3, 0
        self.grid_labels[curr_r][curr_c].configure(fg_color="#3498DB")
        valid_dirs = []

        for d in self.config["skill_dirs"]:
            if d == "up":
                curr_r -= 1
            elif d == "down":
                curr_r += 1
            elif d == "left":
                curr_c -= 1
            elif d == "right":
                curr_c += 1

            if 0 <= curr_r < 4 and 0 <= curr_c < 4:
                self.grid_labels[curr_r][curr_c].configure(fg_color="#3498DB")
                valid_dirs.append(d)
            else:
                break

        self.config["skill_dirs"] = valid_dirs

    # ==========================================
    # --- Telegram 通知系统 ---
    # ==========================================
    def _format_elapsed(self, seconds):
        """将秒数格式化为 HH:MM:SS"""
        hrs, rem = divmod(int(seconds), 3600)
        mins, secs = divmod(rem, 60)
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"

    def send_telegram(self, message):
        """异步发送 Telegram 消息，不阻塞主线程"""
        token = self.config.get("telegram_bot_token", "").strip()
        chat_id = self.config.get("telegram_chat_id", "").strip()
        if not token or not chat_id:
            return

        def _post():
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
            except Exception:
                pass

        threading.Thread(target=_post, daemon=True).start()

    def test_telegram(self):
        """测试 Telegram 通知是否配置正确"""
        token = self.entry_telegram_token.get().strip()
        chat_id = self.entry_telegram_chat_id.get().strip()
        if not token or not chat_id:
            self.log(self.t("telegram_test_fail"))
            return

        lang = getattr(self, "ui_language", DEFAULT_UI_LANGUAGE)
        test_msg = {
            "zh": "✅ FH6Auto Telegram 测试通知",
            "en": "✅ FH6Auto Telegram test notification",
            "ko": "✅ FH6Auto 텔레그램 테스트 알림",
        }.get(lang, "✅ FH6Auto Telegram test notification")

        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(url, json={"chat_id": chat_id, "text": test_msg}, timeout=5)
            if resp.status_code == 200:
                self.log(self.t("telegram_test_success"))
            else:
                self.log(f"Telegram HTTP {resp.status_code}")
        except Exception as e:
            self.log(f"Telegram error: {e}")

    def _tg_notify(self, notify_type, message):
        """根据 config 决定是否发送指定类型的 Telegram 通知"""
        if not self.config.get("telegram_enabled", False):
            return
        key_map = {
            "fatal": "telegram_on_fatal",
            "step": "telegram_on_step",
            "loop": "telegram_on_loop",
            "finish": "telegram_on_finish",
        }
        config_key = key_map.get(notify_type)
        if config_key and not self.config.get(config_key, True):
            return
        self.send_telegram(message)

    def log(self, message):
        message = self.localize_log_message(message)
        curr_time = time.strftime("%H:%M:%S")
        full_msg = f"[{curr_time}] {message}"

        def write_ui():
            try:
                # 写入下方大界面的日志
                self.log_box.configure(state="normal")
                self.log_box.insert("end", full_msg + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
                # 同时写入迷你界面的横向日志
                if hasattr(self, "mini_log_box"):
                    self.mini_log_box.configure(state="normal")
                    self.mini_log_box.insert("end", full_msg + "\n")
                    self.mini_log_box.see("end")
                    self.mini_log_box.configure(state="disabled")
            except Exception:
                pass
        self.ui_call(write_ui)
    def start_pipeline(self, start_step):
        if self.is_running:
            return

        self.is_running = True

        # 새 실행 시작 시 일시정지 상태 초기화
        self.is_paused = False
        self.pause_requested = False
        self.set_pause_button_state(paused=False)
        
        self.save_config()

        # 隐藏大窗的所有元素
        self.config_frame.pack_forget()
        self.global_settings_frame.pack_forget()
        if hasattr(self, "race_detail_frame"):
            self.race_detail_frame.pack_forget()
        self.calc_frame.pack_forget()
        if hasattr(self, "telegram_frame"):
            self.telegram_frame.pack_forget()
        self.top_container.pack_forget()
        if hasattr(self, "bottom_frame"):
            self.bottom_frame.pack_forget()
        self.btn_support.pack_forget()

        # 显示新的迷你横向 UI
        self.mini_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # ====== 计算 15% 高度 40% 宽度 ======
        last_x, last_y, last_w, last_h = self.regions["全界面"]
        if last_w <= 0: last_w = self.winfo_screenwidth()
        if last_h <= 0: last_h = self.winfo_screenheight()

        calc_w = int(last_w * 0.40)
        calc_h = int(last_h * 0.15)
        # 设置一个兜底最小值，防止分辨率过低时文字挤压导致崩溃
        calc_w = max(calc_w, 650)
        calc_h = max(calc_h, 150)

        pos_x = last_x + last_w - calc_w - 20
        pos_y = last_y + 20

        self.attributes("-topmost", self.config.get("always_on_top", False))
        self.geometry(f"{calc_w}x{calc_h}+{pos_x}+{pos_y}")
        
        # 启动计时器
        self.start_time = time.time()
        self.update_timer()

        
        self.update_running_ui("初始化中...")
        self.race_counter = 0
        self.car_counter = 0
        self.cj_counter = 0
        self.sc_count = 0
        self.global_loop_current = 0

        def runner():
            if not self.check_and_focus_game():
                self.stop_all()
                return

            steps = ["race", "buy", "cj", "sell"]
            curr_idx = steps.index(start_step)

            try:
                total_loops = int(self.entry_global_loop.get())
            except Exception:
                total_loops = self.config.get("global_loops", 10)
            self.global_loop_current = 1
            if hasattr(self, "lbl_mini_loop"):
                self.ui_call(self.lbl_mini_loop.configure, text=self.t("loop_progress", current=self.global_loop_current, total=total_loops))

            # 【新增】：全局连续失败计数器
            continuous_failures = 0 
            # 【你可以修改这里】：设置全局允许的最大连续恢复次数（比如 3 次）
            MAX_RECOVERIES = 10 

            while self.is_running:
                step_name = steps[curr_idx]
                success = False
                step_start_time = time.time()

                try:
                    if step_name == "race":
                        success = self.logic_race(int(self.entry_race.get()))
                    elif step_name == "buy":
                        success = self.logic_buy_car(int(self.entry_car.get()))
                    elif step_name == "cj":
                        success = self.logic_super_wheelspin(int(self.entry_cj.get()))
                    elif step_name == "sell":
                        # ====== 【新增】：判断下拉框的模式 ======
                        sell_mode = self.opt_sell_mode.get()
                        mode_value = getattr(self, "sell_mode_values", {}).get(sell_mode)
                        if mode_value == 1 or "模式1" in sell_mode or "Mode 1" in sell_mode or "모드 1" in sell_mode:
                            success = self.find_and_remove_consumable_car(int(self.entry_sc.get()))
                        else:
                            success = self.sell_consumable_car(int(self.entry_sc.get()))
                        # =========================================
                except Exception as e:
                    self.log(f"执行模块 {step_name} 时异常: {e}")
                    success = False

                if not self.is_running:
                    break

                if success == "PAUSED":
                    while self.is_running and self.is_paused:
                        time.sleep(0.2)

                    if not self.is_running:
                        break

                    continue

                if not success:
                    continuous_failures += 1
                    
                    # 检查是否超过最大容忍次数
                    if continuous_failures > MAX_RECOVERIES:
                        self.log(f"!!! 警告：连续 {continuous_failures} 次触发断点恢复仍未能解决问题！")
                        self.log("为防止游戏陷入死循环，强制终止当前所有任务，请人工检查游戏状态。")
                        self._tg_notify("fatal", self.t("tg_fatal_recovery", failures=continuous_failures, elapsed=self._format_elapsed(time.time() - self.start_time)))
                        break # 直接跳出 while，停止脚本
                        
                    self.log(f"正在进行全局恢复 (第 {continuous_failures}/{MAX_RECOVERIES} 次允许的重试)...")
                    
                    if self.attempt_recovery():
                        continue # 恢复成功，回到 while 顶部再次尝试这个任务
                    else:
                        self.log("致命错误：连退回菜单/重启也失败了，彻底停止。")
                        self._tg_notify("fatal", self.t("tg_fatal_menu", elapsed=self._format_elapsed(time.time() - self.start_time)))
                        break
                else:
                    # 只要这一个大步骤成功跑完了，就把连续失败次数清零，奖励它继续跑！
                    continuous_failures = 0
                    # ====== Telegram: 단계 완료 알림 ======
                    step_elapsed = self._format_elapsed(time.time() - step_start_time)
                    step_label = self.task_text({"race": "循环跑图", "buy": "批量买车", "cj": "超级抽奖", "sell": "移除车辆"}.get(step_name, step_name))
                    total_elapsed = self._format_elapsed(time.time() - self.start_time)
                    self._tg_notify("step", self.t("tg_step", label=step_label, step_elapsed=step_elapsed, total_elapsed=total_elapsed, loop_cur=self.global_loop_current, loop_total=total_loops))
                    # ==========================================
                #v1.0.1
                # ====== 核心流转与无限循环逻辑 ======
                next_idx = curr_idx + 1 # 默认前往下一步
                if curr_idx == 0:
                    if self.var_chk1.get():
                        try: next_idx = max(0, min(3, int(self.entry_next1.get()) - 1))
                        except Exception: next_idx = 1
                    else: break
                elif curr_idx == 1:
                    if self.var_chk2.get():
                        try: next_idx = max(0, min(3, int(self.entry_next2.get()) - 1))
                        except Exception: next_idx = 2
                    else: break
                elif curr_idx == 2:
                    if self.var_chk3.get():
                        try: next_idx = max(0, min(3, int(self.entry_next3.get()) - 1))
                        except Exception: next_idx = 3
                    else: break
                elif curr_idx == 3:
                    if self.var_chk4.get():
                        try: next_idx = max(0, min(3, int(self.entry_next4.get()) - 1))
                        except Exception: next_idx = 0
                    else: break

                if next_idx <= curr_idx:
                    self.global_loop_current += 1
                    
                    if self.global_loop_current > total_loops:
                        self.log("达到设定的总循环次数，任务圆满结束。")
                        self._tg_notify("finish", self.t("tg_finish", loop_total=total_loops, elapsed=self._format_elapsed(time.time() - self.start_time)))
                        break
                        
                    self.log(f"开启新一轮大循环 ({self.global_loop_current}/{total_loops})")
                    self._tg_notify("loop", self.t("tg_loop", loop_cur=self.global_loop_current, loop_total=total_loops, elapsed=self._format_elapsed(time.time() - self.start_time)))
                    
                    if hasattr(self, "lbl_mini_loop"):
                        self.ui_call(self.lbl_mini_loop.configure, text=self.t("loop_progress", current=self.global_loop_current, total=total_loops))

                    self.race_counter = 0
                    self.car_counter = 0
                    self.cj_counter = 0
                    self.sc_count = 0
                
                curr_idx = next_idx

            self.stop_all()

        self.current_thread = threading.Thread(target=runner, daemon=True)
        self.current_thread.start()

    def stop_all(self):
        if not self.is_running:
            return

        self.is_running = False
        self.is_paused = False
        self.pause_requested = False

        for key in DIK_CODES.keys():
            self.hw_key_up(key)

        for key in ["w", "e", "y", "enter", "esc", "up", "down", "left", "right", "space", "backspace"]:
            self.hw_key_up(key)

        try:
            pydirectinput.mouseUp()
        except Exception:
            pass

        def restore_ui():
            if hasattr(self, "mini_frame"):
                self.mini_frame.pack_forget()
                
            # 【核心修复】：先让大容器里的东西全部解绑，洗牌重来
            self.config_frame.pack_forget()
            self.global_settings_frame.pack_forget()
            if hasattr(self, "race_detail_frame"):
                self.race_detail_frame.pack_forget()
            self.calc_frame.pack_forget()
            
            # 1. 铺设最外层大容器
            self.top_container.pack(fill="x", padx=18, pady=(18, 10))
            
            # 2. 依次按顺序塞入三个模块，完美保证从上到下的顺序！
            self.config_frame.pack(fill="x")
            self.global_settings_frame.pack(fill="x", pady=(15, 0))
            if hasattr(self, "race_detail_frame"):
                self.race_detail_frame.pack(fill="x", pady=(10, 0))
            self.calc_frame.pack(fill="x", pady=(10, 0))
            if hasattr(self, "telegram_frame"):
                self.telegram_frame.pack(fill="x", pady=(10, 0))
            
            # 3. 铺设底部的日志和按钮
            if hasattr(self, "bottom_frame"):
                self.bottom_frame.pack(fill="both", expand=True, padx=18, pady=(6, 12))
            self.btn_support.pack(fill="x", padx=18, pady=(6, 12))
            
            # 恢复窗口原本的状态
            self.btn_stop.configure(text=self.t("waiting_command_plain"), fg_color="#3A3A3A", hover_color="#4A4A4A")
            self.attributes("-topmost", self.config.get("always_on_top", False))
            self.apply_always_on_top_state(log_message=False)
            self.geometry("1800x880")
            self.center_window()

        self.ui_call(restore_ui)
        self.log("!!! 任务已停止，所有物理按键状态已强制重置")

    def start_test_boot(self):
        """자동 시작/부팅 복구 흐름만 독립 테스트"""
        if self.is_running:
            self.log("이미 작업이 실행 중입니다. 먼저 중지 후 테스트하세요.")
            return

        self.is_running = True
        self.is_paused = False
        self.pause_requested = False
        self.set_pause_button_state(paused=False)
        self.save_config()

        self.config_frame.pack_forget()
        self.global_settings_frame.pack_forget()
        if hasattr(self, "race_detail_frame"):
            self.race_detail_frame.pack_forget()
        self.calc_frame.pack_forget()

        if hasattr(self, "telegram_frame"):
            self.telegram_frame.pack_forget()

        self.top_container.pack_forget()

        if hasattr(self, "bottom_frame"):
            self.bottom_frame.pack_forget()

        self.btn_support.pack_forget()

        self.mini_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.update_running_ui("테스트 시작 흐름...")
        self.start_time = time.time()
        self.update_timer()

        self.log("====== 자동 시작/부팅 복구 테스트를 시작합니다 ======")

        def test_runner():
            success = self.restart_game_and_boot(force_test=True)

            if success:
                self.log("테스트 완료: 자동 시작, 화면 인식, 메뉴 진입까지 성공했습니다.")
            else:
                self.log("테스트 실패: 자동 시작 흐름을 확인하세요.")

            self.stop_all()

        self.current_thread = threading.Thread(target=test_runner, daemon=True)
        self.current_thread.start()

    def start_hotkey_listener(self):
        def hotkey_thread():
            def on_press(k):
                if k == keyboard.Key.f8:
                    self.stop_all()
                elif k == keyboard.Key.f9:
                    self.toggle_pause()

            with keyboard.Listener(on_press=on_press) as listener:
                listener.join()

        threading.Thread(target=hotkey_thread, daemon=True).start()

   
    # ==========================================
    # --- 逻辑保障 ---
    # ==========================================
    # 【新增】：强制切换英文键盘与关闭中文状态
    # 【수정본】: 기존 중국어 방어 로직을 유지하면서, 한국어 설정 시 IME 영문 전환 추가
    def set_english_input(self):
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return

            if getattr(self, "ui_language", "ko") == "ko":
                # IME_CMODE_ALPHANUMERIC(영문 모드) 설정
                WM_IME_CONTROL = 0x0283
                IMC_SETCONVERSIONMODE = 0x0002
                IME_CMODE_ALPHANUMERIC = 0x0000
                
                # IME 기본 윈도우 핸들을 가져와서 메시지를 보내야 한국어 입력기에 정확히 전달됩니다.
                ime_hwnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
                if ime_hwnd:
                    ctypes.windll.user32.SendMessageW(ime_hwnd, WM_IME_CONTROL, IMC_SETCONVERSIONMODE, IME_CMODE_ALPHANUMERIC)
            else:
                # 策略1：尝试切美式键盘
                hkl = ctypes.windll.user32.LoadKeyboardLayoutW("00000409", 1)
                ctypes.windll.user32.PostMessageW(hwnd, 0x0050, 0, hkl) 
                # 策略2：底层强制关闭当前中文输入法的中文状态(绝杀)
                WM_IME_CONTROL = 0x0283
                IMC_SETOPENSTATUS = 0x0006
                ctypes.windll.user32.SendMessageW(hwnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, 0)
                
                self.log("已自动切换英文键盘/关闭中文输入法状态。")
        except Exception as e:
            self.log(f"自动防中文输入设置失败: {e}")

    def check_and_focus_game(self):
        self.log("检查游戏进程 (forzahorizon6.exe)...")
        try:
            CREATE_NO_WINDOW = 0x08000000
            cmd = 'tasklist /FI "IMAGENAME eq forzahorizon6.exe" /NH /FO CSV'
            output = subprocess.check_output(cmd, shell=True, text=True, creationflags=CREATE_NO_WINDOW)

            if "forzahorizon6.exe" not in output.lower():
                self.log("未发现 forzahorizon6.exe 进程！(请确保游戏已运行)")
                return False

            target_pid = None
            for line in output.strip().split("\n"):
                parts = line.split('","')
                if len(parts) >= 2 and "forzahorizon6.exe" in parts[0].lower():
                    target_pid = int(parts[1].replace('"', ""))
                    break

            if not target_pid:
                self.log("找到进程但无法解析PID！")
                return False

            hwnds = []

            def foreach_window(hwnd, lParam):
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        window_pid = ctypes.c_ulong()
                        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                        if window_pid.value == target_pid:
                            hwnds.append(hwnd)
                return True

            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            ctypes.windll.user32.EnumWindows(EnumWindowsProc(foreach_window), 0)

            if hwnds:
                hwnd = hwnds[0]
                if ctypes.windll.user32.IsIconic(hwnd):
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                else:
                    ctypes.windll.user32.ShowWindow(hwnd, 5)
                    
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.5)
                # ====== 【新增】：强制关闭中文输入法 ======
                self.set_english_input()
                # ==========================================
                try:
                    # 1. 更新识图区域为游戏实际窗口区域（识图必须在游戏窗口内）
                    client_rect = win32gui.GetClientRect(hwnd)
                    pt = win32gui.ClientToScreen(hwnd, (0, 0))
                    gx, gy = pt[0], pt[1]
                    gw, gh = client_rect[2], client_rect[3]

                    if gw < 1000 or gh < 600:
                        self.log(f"拦截到过小窗口 ({gw}x{gh})，判定为启动闪屏，等待主窗口加载...")
                        return False

                    self.update_regions_by_window(gx, gy, gw, gh)

                    # 2. 获取该窗口所在的物理显示器边界
                    MONITOR_DEFAULTTONEAREST = 2
                    hMonitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                    class RECT(ctypes.Structure):
                        _fields_ = [
                            ("left", ctypes.c_long), 
                            ("top", ctypes.c_long), 
                            ("right", ctypes.c_long), 
                            ("bottom", ctypes.c_long)
                        ]
                    class MONITORINFO(ctypes.Structure):
                        _fields_ = [
                            ("cbSize", ctypes.c_ulong), 
                            ("rcMonitor", RECT), 
                            ("rcWork", RECT), 
                            ("dwFlags", ctypes.c_ulong)
                        ]
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(MONITORINFO)
                    
                    if ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                        mx = mi.rcMonitor.left
                        my = mi.rcMonitor.top
                        mw = mi.rcMonitor.right - mi.rcMonitor.left
                        mh = mi.rcMonitor.bottom - mi.rcMonitor.top
                    else:
                        # 兜底：如果获取不到屏幕边界，就用游戏窗口边界
                        mx, my, mw, mh = gx, gy, gw, gh

                    # ====== 【修改】：小窗口精准吸附所在显示器的右上角 ======
                    def snap_to_game():
                        if self.is_running:
                            calc_w = int(mw * 0.40)
                            calc_h = int(mh * 0.15)
                            calc_w = max(calc_w, 650)
                            calc_h = max(calc_h, 150)
                            
                            # 放置在当前显示器的右上角（预留20像素边距）
                            pos_x = mx + mw - calc_w - 20
                            pos_y = my + 20
                            self.geometry(f"{calc_w}x{calc_h}+{pos_x}+{pos_y}")
                    self.ui_call(snap_to_game)
                    # ==========================================
                except Exception as e:
                    self.log(f"获取窗口坐标失败: {e}")

                time.sleep(1.0)
                return True

        except Exception as e:
            self.log(f"检查进程异常: {e}")
            return False

        return False

    def restart_game_and_boot(self, force_test=False):
        # 테스트 실행일 때는 자동 재시작 체크를 우회
        if not force_test:
            auto_restart = getattr(self, "var_auto_restart", None)
            if auto_restart is None or not auto_restart.get():
                self.log("未开启自动重启，任务结束。")
                return False

        self.log("触发启动机制！正在拉起游戏...")
        try:
            cmd_widget = getattr(self, "le_restart_cmd", None)
            cmd_str = cmd_widget.get() if cmd_widget else self.config.get("restart_cmd", "start steam://run/2483190")
            os.system(cmd_str)
        except Exception as e:
            self.log(f"执行启动命令失败: {e}")
            return False

        self.log("等待游戏进程出现 (最多60秒)...")
        process_found = False

        for _ in range(120):
            if not self.is_running:
                return False

            if self.check_and_focus_game():
                process_found = True
                break

            time.sleep(1)

        if not process_found:
            self.log("未检测到游戏进程，启动失败。")
            return False

        self.log("游戏进程已启动，进入动态识别阶段 (限制5分钟)...")

        start_time = time.time()
        passed_screen_1 = False
        last_continue_time = 0

        while self.is_running and time.time() - start_time < 300:
            # 화면 1: horizon6.png 감지 후 Enter
            if not passed_screen_1:
                pos_h6 = self.find_image_transparent(
                    "horizon6.png",
                    region=self.regions["全界面"],
                    threshold=0.60,
                    fast_mode=False
                )

                # 투명 이미지 인식 실패 시 엣지 매칭으로 보조
                if not pos_h6:
                    try:
                        screen_bgr = self.capture_region(self.regions["全界面"])
                        tpl_bgr, _ = self.load_template("horizon6.png")

                        if tpl_bgr is not None:
                            screen_edge = self.to_edge_image(screen_bgr)
                            tpl_edge = self.to_edge_image(tpl_bgr)

                            for scale in self.get_scales_to_try(fast_mode=False):
                                t_e = tpl_edge if scale == 1.0 else cv2.resize(
                                    tpl_edge,
                                    None,
                                    fx=scale,
                                    fy=scale,
                                    interpolation=cv2.INTER_AREA
                                )

                                h, w = t_e.shape[:2]
                                if h > screen_edge.shape[0] or w > screen_edge.shape[1] or h < 5 or w < 5:
                                    continue

                                res = cv2.matchTemplate(screen_edge, t_e, cv2.TM_CCOEFF_NORMED)
                                _, max_val, _, max_loc = cv2.minMaxLoc(res)

                                if max_val >= 0.40:
                                    self.log(f"[轮廓黑科技] 无视背景命中！得分: {max_val:.2f} 缩放: {scale:.2f}")
                                    pos_h6 = (
                                        max_loc[0] + w // 2 + self.regions["全界面"][0],
                                        max_loc[1] + h // 2 + self.regions["全界面"][1]
                                    )
                                    break
                    except Exception:
                        pass

                if pos_h6:
                    self.log("✅ 成功识别到 画面1 (horizon6.png)，按下【回车键】...")
                    time.sleep(1)

                    for _ in range(2):
                        self.hw_press("enter")
                        time.sleep(1)

                    passed_screen_1 = True
                    last_continue_time = time.time()

                    self.log("已确认画面1，强制等待 10 秒等待画面2加载...")
                    time.sleep(10)
                    continue
                else:
                    self.log("未找到画面1。正在使用全比例深度扫描...")

            # 화면 2: continue 버튼 감지 시 계속 클릭
            if passed_screen_1:
                pos_continue = self.find_any_image_gray(
                    ["continue-b.png", "continue-w.png"],
                    region=self.regions["全界面"],
                    threshold=0.75,
                    fast_mode=True
                )

                if pos_continue:
                    self.log("识别到 画面2 (继续按钮)，进行点击...")
                    self.game_click(pos_continue)
                    last_continue_time = time.time()
                    time.sleep(3.0)
                    continue

                # 마지막 continue 감지/클릭 후 30초 동안 안 보이면 로딩 완료로 판단
                time_since_last_seen = time.time() - last_continue_time
                if time_since_last_seen >= 30.0:
                    self.log("✅ 已经连续 30 秒未再发现继续按钮，判定为漫游载入完毕！开始尝试进入菜单...")

                    if self.enter_menu():
                        self.log("🎉 验证成功：已成功进入游戏主菜单！启动流程完美结束。")
                        return True
                    else:
                        self.log("普通进入菜单失败(可能还在黑屏或有新弹窗)，重置 30秒倒计时，继续观察...")
                        last_continue_time = time.time()

            time.sleep(1.0)

        self.log("自动启动超时(5分钟)，放弃抢救。")
        return False

    def handle_vramne_restart(self):
        auto_restart = getattr(self, "var_auto_restart", None)
        if auto_restart is None or not auto_restart.get():
            self.log("VRAM 오류 감지: 자동 재시작이 꺼져 있어 게임을 강제 종료하지 않습니다.")
            return False

        self.log("!!! 检测到 VRAMNE.png，2秒后强杀游戏，等待10分钟再重启...")
        time.sleep(2.0)

        if not self.is_running:
            return False

        try:
            os.system('taskkill /F /IM forzahorizon6.exe /T')
            self.log("已强杀 forzahorizon6.exe")
        except Exception as e:
            self.log(f"强杀游戏失败: {e}")
            return False

        self.log("开始等待 10 分钟释放显存...")
        for _ in range(600):
            if not self.is_running:
                return False
            time.sleep(1)

        self.log("10分钟等待结束，准备自动重启游戏...")
        return self.restart_game_and_boot()
    
    def check_vramne_during_race(self):
        try:
            pos_vram = self.find_image_gray(
                "VRAMNE.png",
                region=self.regions["全界面"],
                threshold=0.70,
                fast_mode=True
            )
            if pos_vram:
                return self.handle_vramne_restart()
            return None
        except Exception as e:
            self.log(f"检测到显存不足: {e}")
            return None
        
    def attempt_recovery(self):
        self.log("任务执行异常中断，准备执行断点恢复流程...")
        if not self.check_and_focus_game():
            if not self.restart_game_and_boot():
                return False
        else:
            if not self.advanced_enter_menu():
                auto_restart = getattr(self, "var_auto_restart", None)
            
                if auto_restart is None or not auto_restart.get():
                    self.log("고급 복구 실패: 자동 재시작이 꺼져 있어 게임을 강제 종료하지 않고 작업만 중지합니다.")
                    return False
            
                self.log("고급 복구 실패: 자동 재시작이 켜져 있어 게임을 강제 종료 후 재시작합니다.")
            
                try:
                    os.system('taskkill /F /IM forzahorizon6.exe /T')
                    time.sleep(4)
                except Exception:
                    pass
                
                if not self.restart_game_and_boot():
                    return False

        self.log("环境重置成功！即将从中断处继续剩余任务。")
        return True

    def wait_for_freeroam(self):
        self.log("验证漫游状态...")
        for i in range(100):
            if not self.is_running:
                return False

            if self.find_image("anna.png", region=self.regions["左下"], threshold=0.5):
                self.log("验证成功：已确认处于游戏漫游界面。")
                return True

            self.log(f"重试返回漫游界面({i + 1}/100)")
            self.hw_press("esc")

            for _ in range(20):
                if not self.is_running:
                    return False
                time.sleep(0.1)

        self.log("多次尝试验证漫游界面失败，尝试进入菜单。")
        return True

    def recover_to_menu(self):
        self.log("开始尝试退回主菜单 (强制ESC兜底)...")
        return self.enter_menu()

    def is_in_menu(self):    
        return self.find_image_gray(
            "collectionjournal.png",
            region=self.regions["左"],
            threshold=0.70,
            fast_mode=True
        )
    def enter_menu(self):
        self.log("正在尝试进入主菜单 (按ESC验证)...")

        # 连续尝试 60 次，大概花费 40~60 秒
        for i in range(60):
            if not self.is_running:
                return False
                

            pos_menu = self.find_image_gray("collectionjournal.png", region=self.regions["左"], threshold=0.70, fast_mode=True)
            
            if pos_menu:
                self.log(f"成功定位到菜单锚点！({i + 1}/60)")
                time.sleep(0.5)
                return True
                
            self.log(f"未在主菜单，按下 ESC... ({i + 1}/60)")
            self.hw_press("esc")
            # 给游戏一点动画加载时间
            time.sleep(1.0)
            
        self.log("60 次 ESC 尝试均未进入菜单，请检查游戏状态。")
        return False
    
    def advanced_enter_menu(self):
        """
        高级状态机退回：专门用于故障恢复。
        能够识别中途的特定弹窗、中间过渡画面，并执行点击，没找到目标才按 ESC。
        """
        self.log("正在使用【高级恢复模式】尝试退回主菜单...")
        
        # ==========================================
        # 动态读取 images/obstacles/ 里的所有图片
        # ==========================================
        obstacles_dir = os.path.join("images", "obstacles")
        dynamic_obstacles = []
        
        # 检查文件夹是否存在
        if os.path.exists(obstacles_dir):
            for file in os.listdir(obstacles_dir):
                # 只要是 png 或 jpg 格式的图片，统统加进来
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    # 拼成 "obstacles/文件名.png"，这样 find_any_image_gray 就能正确找到路径
                    dynamic_obstacles.append(f"obstacles/{file}")
        
        if not dynamic_obstacles:
            self.log("提示：images/obstacles/ 文件夹为空或不存在，将只使用 ESC 退回。")
        # 连续尝试 80 次，处理较长的随机过程
        for i in range(80):
            if hasattr(self, "check_pause"): self.check_pause() # 兼容暂停功能
            if not self.is_running:
                return False
                
            # 1. 终极判断：是不是已经在菜单了？
            if self.is_in_menu():
                self.log(f"成功定位到菜单锚点！(尝试次数: {i + 1})")
                time.sleep(0.5)
                return True

            # 2. 致命错误排查 (检测到显存不足，强制休息 10 分钟)
            if self.find_image_gray("VRAMNE.png", region=self.regions["全界面"], threshold=0.75, fast_mode=True):
                self.log("!!! 严重警告: 检测到显存不足 (VRAMNE.png) 报错！")
            
                auto_restart = getattr(self, "var_auto_restart", None)
                if auto_restart is None or not auto_restart.get():
                    self.log("VRAM 오류 감지: 자동 재시작이 꺼져 있어 게임을 강제 종료하지 않습니다.")
                    return False
            
                self.log("자동 재시작이 켜져 있어 2초 후 게임을 강제 종료하고 10분 대기합니다.")
                time.sleep(2.0)
            
                try:
                    os.system('taskkill /F /IM forzahorizon6.exe /T')
                    self.log("已强杀 forzahorizon6.exe")
                except Exception as e:
                    self.log(f"强杀游戏失败: {e}")
                    return False
            
                for _ in range(600):
                    if hasattr(self, "check_pause"):
                        self.check_pause()
                    if not self.is_running:
                        return False
                    time.sleep(1)
            
                self.log("10 分钟冷却完毕，交给外层执行重启流程。")
                return False

            # 3. 动态扫描所有可能的弹窗 / 需要点击的中间图片
            pos_obs = self.find_any_image_gray(dynamic_obstacles, region=self.regions["全界面"], threshold=0.75, fast_mode=True)
            if pos_obs:
                self.log(f"退回途中检测到已知图片/弹窗，点击推进... ({i+1}/80)")
                self.game_click(pos_obs)
                time.sleep(1.5) # 给画面跳转留出动画时间
                continue # 点击后，跳过本轮，不要按 ESC
                
            # 4. 如果既没进菜单，也没看到特定的图片，说明处于常规界面，按 ESC 退回
            self.log(f"未在主菜单且无已知特定图片，按下 ESC... ({i + 1}/80)")
            self.hw_press("esc")
            time.sleep(1.2) # 给游戏一点动画加载时间
            
        self.log("80 次动态尝试均未进入菜单，高级退回失败。")
        return False
    
    # ==========================================
    # --- 图像寻找 ---
    # ==========================================
    def load_template(self, template_path):
        actual_path = get_img_path(template_path)
        cache_key = actual_path

        if cache_key in self.template_cache:
            return self.template_cache[cache_key], actual_path

        tpl = cv2.imread(actual_path, cv2.IMREAD_COLOR)
        if tpl is not None:
            self.template_cache[cache_key] = tpl
        return tpl, actual_path
    def load_template_gray(self, template_path):
        actual_path = get_img_path(template_path)
        cache_key = ("gray", actual_path)
        if not hasattr(self, "template_gray_cache"):
            self.template_gray_cache = {}
        if cache_key in self.template_gray_cache:
            return self.template_gray_cache[cache_key]
        tpl = cv2.imread(actual_path, cv2.IMREAD_GRAYSCALE)
        if tpl is not None:
            self.template_gray_cache[cache_key] = tpl
        return tpl
    def get_images_root_dir(self):
        ext_dir = os.path.join(APP_DIR, "images")
        if os.path.isdir(ext_dir):
            return ext_dir

        int_dir = os.path.join(INTERNAL_DIR, "images")
        if os.path.isdir(int_dir):
            return int_dir

        return None

    def get_template_meta(self):
        images_dir = self.get_images_root_dir()
        meta_data = {}
        if not images_dir:
            return meta_data

        for root, _, files in os.walk(images_dir):
            for file in files:
                if not file.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    continue

                path = os.path.join(root, file)
                rel_path = os.path.relpath(path, images_dir).replace("\\", "/")

                try:
                    stat = os.stat(path)
                    meta_data[rel_path] = {
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    }
                except Exception:
                    pass

        return meta_data

    def is_template_cache_valid(self):
        if not os.path.exists(TEMPLATE_CACHE_FILE) or not os.path.exists(TEMPLATE_META_FILE):
            return False

        try:
            with open(TEMPLATE_META_FILE, "r", encoding="utf-8") as f:
                old_meta = json.load(f)
        except Exception:
            return False

        new_meta = self.get_template_meta()
        return old_meta == new_meta

    def build_template_file_cache(self):
        self.log("开始构建模板缓存文件...")
        os.makedirs(CACHE_DIR, exist_ok=True)

        images_dir = self.get_images_root_dir()
        if not images_dir:
            self.log("未找到 images 目录，无法构建模板缓存。")
            return False

        cache_data = {}
        meta_data = self.get_template_meta()

        scales = self.get_scales_to_try(fast_mode=False)

        for rel_path in meta_data.keys():
            img_path = os.path.join(images_dir, rel_path)
            tpl = cv2.imread(img_path, cv2.IMREAD_COLOR)
            if tpl is None:
                continue

            cache_data[rel_path] = {}
            for scale in scales:
                try:
                    if scale == 1.0:
                        scaled = tpl.copy()
                    else:
                        scaled = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

                    cache_data[rel_path][str(round(scale, 3))] = scaled
                except Exception:
                    continue

        try:
            with open(TEMPLATE_CACHE_FILE, "wb") as f:
                pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)

            with open(TEMPLATE_META_FILE, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, ensure_ascii=False, indent=2)

            self.log("模板缓存文件构建完成。")
            return True
        except Exception as e:
            self.log(f"写入模板缓存失败: {e}")
            return False

    def load_template_file_cache(self):
        try:
            with open(TEMPLATE_CACHE_FILE, "rb") as f:
                self.file_template_cache = pickle.load(f)
            self.log("模板缓存文件加载成功。")
            return True
        except Exception as e:
            self.log(f"加载模板缓存失败: {e}")
            self.file_template_cache = {}
            return False

    def prepare_template_cache(self):
        os.makedirs(CACHE_DIR, exist_ok=True)

        if self.is_template_cache_valid():
            if self.load_template_file_cache():
                return

        self.log("模板缓存不存在或已失效，开始后台重建（这可能需要几秒钟）...")
        if self.build_template_file_cache():
            self.template_cache.clear()
            self.scaled_template_cache.clear()
            self.load_template_file_cache()

    def clear_template_cache(self):
        try:
            if os.path.exists(CACHE_DIR):
                shutil.rmtree(CACHE_DIR)
    
            self.template_cache.clear()
            self.scaled_template_cache.clear()
            self.file_template_cache.clear()
            self.edge_template_cache.clear()
            self.scaled_edge_template_cache.clear()
    
            if hasattr(self, "template_gray_cache"):
                self.template_gray_cache.clear()
    
            if hasattr(self, "template_transparent_cache"):
                self.template_transparent_cache.clear()
    
            self.log("고해상도/4K 이미지 보정 설정 변경: 템플릿 캐시를 초기화했습니다.")
    
        except Exception as e:
            self.log(f"템플릿 캐시 초기화 실패: {e}")

    def capture_forza_window_printwindow(self):
        try:
            import win32ui
            import win32con

            hwnd = self.get_forza_hwnd()
            if not hwnd:
                self.log("PrintWindow 캡처 실패: 포르자 창을 찾지 못함")
                return None

            # 최소화 상태면 캡처 실패 가능성이 높음
            if win32gui.IsIconic(hwnd):
                self.log("PrintWindow 캡처 실패: 포르자 창이 최소화 상태입니다.")
                return None

            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            width = right - left
            height = bottom - top

            if width <= 0 or height <= 0:
                return None

            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)

            # 3 = PW_RENDERFULLCONTENT, 일부 앱에서 더 잘 됨
            result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)

            bmpinfo = bitmap.GetInfo()
            bmpstr = bitmap.GetBitmapBits(True)

            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1
            )

            win32gui.DeleteObject(bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

            if result != 1:
                return None

            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

        except Exception as e:
            self.log(f"PrintWindow 캡처 예외: {e}")
            return None

    def capture_region(self, region=None, mask_areas=None):
        # Win32 입력 모드일 때는 포르자 창 자체 캡처를 먼저 시도
        if getattr(self, "use_win32_input", False):
            screen_bgr = self.capture_forza_window_printwindow()

            if screen_bgr is not None:
                # region이 있으면 전체 포르자 클라이언트 화면 기준으로 잘라냄
                if region:
                    gx, gy, gw, gh = self.regions["全界面"]
                    rx, ry, rw, rh = region

                    x1 = max(0, int(rx - gx))
                    y1 = max(0, int(ry - gy))
                    x2 = min(screen_bgr.shape[1], x1 + int(rw))
                    y2 = min(screen_bgr.shape[0], y1 + int(rh))

                    if x2 > x1 and y2 > y1:
                        screen_bgr = screen_bgr[y1:y2, x1:x2]

                if mask_areas:
                    for rect in mask_areas:
                        try:
                            mx1, my1, mx2, my2 = rect
                            mx1 = max(0, int(mx1))
                            my1 = max(0, int(my1))
                            mx2 = min(screen_bgr.shape[1], int(mx2))
                            my2 = min(screen_bgr.shape[0], int(my2))
                            if mx2 > mx1 and my2 > my1:
                                screen_bgr[my1:my2, mx1:mx2] = 0
                        except Exception:
                            pass

                return screen_bgr
        try:
            if region:
                x, y, w, h = region
                bbox = (int(x), int(y), int(x + w), int(y + h))
                screen = ImageGrab.grab(bbox=bbox, all_screens=True)
            else:
                screen = ImageGrab.grab(all_screens=True)
        except Exception:
            screen = pyautogui.screenshot(region=region)

        screen_bgr = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2BGR)

        # 对指定区域打黑块，避免重复识别同一个目标
        if mask_areas:
            for rect in mask_areas:
                try:
                    mx1, my1, mx2, my2 = rect
                    mx1 = max(0, int(mx1))
                    my1 = max(0, int(my1))
                    mx2 = min(screen_bgr.shape[1], int(mx2))
                    my2 = min(screen_bgr.shape[0], int(my2))
                    if mx2 > mx1 and my2 > my1:
                        screen_bgr[my1:my2, mx1:mx2] = 0
                except Exception:
                    pass

        return screen_bgr

    def get_scales_to_try(self, fast_mode=True):
        full_region = self.regions.get("全界面")
        curr_w = full_region[2] if full_region else pyautogui.size()[0]

        high_res_fix = bool(self.config.get("high_res_image_fix", False))

        # 4.3.1 기본 방식 유지
        primary_base = 2560
        primary_scale = curr_w / primary_base

        scales = []

        def add_scale(s):
            s = round(float(s), 3)
            if 0.45 <= s <= 1.80 and s not in scales:
                scales.append(s)

        # 기본 QHD 기준 스케일
        add_scale(primary_scale)
        add_scale(primary_scale * 0.98)
        add_scale(primary_scale * 1.02)
        add_scale(primary_scale * 0.95)
        add_scale(primary_scale * 1.05)
        add_scale(primary_scale * 0.92)
        add_scale(primary_scale * 1.08)

        # 4.3.1 기존 호환 스케일
        for bw in [1920, 1600]:
            s = curr_w / bw
            add_scale(s)
            add_scale(s * 0.98)
            add_scale(s * 1.02)

        # 4.3.1 기존 fallback
        for s in [1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15, 0.8, 0.75, 0.7]:
            add_scale(s)

        # 고해상도 보정 ON일 때만 추가
        if high_res_fix:
            for bw in [3840, 3440, 3200, 2560]:
                s = curr_w / bw
                add_scale(s)
                add_scale(s * 0.98)
                add_scale(s * 1.02)
                add_scale(s * 0.95)
                add_scale(s * 1.05)

            for s in [
                1.50, 1.48, 1.52,
                1.45, 1.55,
                1.40, 1.60,
                1.35, 1.65,
                1.30, 1.70,
            ]:
                add_scale(s)

        if fast_mode:
            if high_res_fix:
                return scales[:24]
            return scales[:8]

        return scales

    def get_scaled_template(self, template_path, scale):
        actual_path = get_img_path(template_path)
        images_dir = self.get_images_root_dir()

        if images_dir and os.path.exists(actual_path):
            try:
                rel_key = os.path.relpath(actual_path, images_dir).replace("\\", "/")
            except Exception:
                rel_key = os.path.basename(actual_path)
        else:
            rel_key = os.path.basename(actual_path)

        mem_key = (actual_path, round(scale, 3))
        if mem_key in self.scaled_template_cache:
            return self.scaled_template_cache[mem_key], actual_path

        scale_key = str(round(scale, 3))
        if rel_key in self.file_template_cache:
            tpl = self.file_template_cache[rel_key].get(scale_key)
            if tpl is not None:
                self.scaled_template_cache[mem_key] = tpl
                return tpl, actual_path

        template_orig, actual_path = self.load_template(template_path)
        if template_orig is None:
            return None, actual_path

        try:
            if scale == 1.0:
                tpl = template_orig.copy()
            else:
                tpl = cv2.resize(template_orig, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

            self.scaled_template_cache[mem_key] = tpl
            return tpl, actual_path
        except Exception:
            return None, actual_path

    def find_image_in_screen(self, screen_bgr, template_path, region=None, threshold=0.75, fast_mode=True):
        try:
            scales_to_try = self.get_scales_to_try(fast_mode=fast_mode)

            best_val = -1.0
            best_scale = None

            for scale in scales_to_try:
                tpl_c, actual_path = self.get_scaled_template(template_path, scale)
                if tpl_c is None:
                    continue

                h, w = tpl_c.shape[:2]
                if h < 5 or w < 5:
                    continue
                if h > screen_bgr.shape[0] or w > screen_bgr.shape[1]:
                    continue

                res = cv2.matchTemplate(screen_bgr, tpl_c, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)

                if max_val > best_val:
                    best_val = max_val
                    best_scale = scale

                if max_val >= threshold:
                    pos = (
                        max_loc[0] + w // 2 + (region[0] if region else 0),
                        max_loc[1] + h // 2 + (region[1] if region else 0),
                    )
                    self.last_positions[template_path] = pos
                    self.log(f"[ImageMatch] 命中: {template_path} | 得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                    return pos

            if self.config.get("image_debug_log", False):
                self.log(
                    f"[ImageFail] {template_path} | "
                    f"최고점수: {best_val:.3f} | "
                    f"최고스케일: {best_scale} | "
                    f"임계값: {threshold} | "
                    f"fast_mode: {fast_mode}"
                )
            return None

        except Exception as e:
            self.log(f"find_image_in_screen 异常: {e}")
            return None

    def find_image(self, template_path, region=None, threshold=0.75, fast_mode=True):
        if not self.is_running:
            return None

        try:
            screen_bgr = self.capture_region(region)
            return self.find_image_in_screen(
                screen_bgr,
                template_path,
                region=region,
                threshold=threshold,
                fast_mode=fast_mode
            )
        except Exception as e:
            self.log(f"查找图片时发生异常: {e}")
            return None

    def find_any_image(self, image_list, region=None, threshold=MATCH_THRESHOLD, fast_mode=True):
        if not self.is_running:
            return None

        try:
            screen_bgr = self.capture_region(region)
            for img_path in image_list:
                pos = self.find_image_in_screen(
                    screen_bgr,
                    img_path,
                    region=region,
                    threshold=threshold,
                    fast_mode=fast_mode
                )
                if pos:
                    return pos
            return None
        except Exception as e:
            self.log(f"find_any_image 异常: {e}")
            return None

    def find_image_with_element(self, main_path, sub_path, region=None, threshold=0.85, fast_mode=True):
        if not self.is_running:
            return None
        try:
            screen_bgr = self.capture_region(region)
            scales_to_try = self.get_scales_to_try(fast_mode=fast_mode)
            for scale in scales_to_try:
                # 1. 结合新架构缓存直接读取缩放好的图像
                main_tpl_c, _ = self.get_scaled_template(main_path, scale)
                sub_tpl_c, _ = self.get_scaled_template(sub_path, scale)
                if main_tpl_c is None or sub_tpl_c is None:
                    continue
                h_m, w_m = main_tpl_c.shape[:2]
                if h_m < 5 or w_m < 5 or h_m > screen_bgr.shape[0] or w_m > screen_bgr.shape[1]:
                    continue
                # 2. 一阶匹配：寻找全屏符合的主目标
                res_main = cv2.matchTemplate(screen_bgr, main_tpl_c, cv2.TM_CCOEFF_NORMED)
                loc = np.where(res_main >= threshold)
                checked = set() # 【关键优化】：坐标去重，解决几十万次无效循环造成的卡顿
                for pt in zip(*loc[::-1]):
                    x, y = pt
                    # 过滤相邻 10 个像素内的重复识别点
                    key = (x // 10, y // 10)
                    if key in checked:
                        continue
                    checked.add(key)
                    # 3. 旧代码的核心精髓：在主图区域四周略微扩大 5 像素的范围内找元素
                    sub_roi = screen_bgr[
                        max(0, y - 5):min(screen_bgr.shape[0], y + h_m + 5),
                        max(0, x - 5):min(screen_bgr.shape[1], x + w_m + 5),
                    ]
                    if sub_tpl_c.shape[0] > sub_roi.shape[0] or sub_tpl_c.shape[1] > sub_roi.shape[1]:
                        continue
                                        # 4. 二阶匹配：验证提取范围内是否包含子元素
                    res_sub = cv2.matchTemplate(sub_roi, sub_tpl_c, cv2.TM_CCOEFF_NORMED)
                    sub_score = cv2.minMaxLoc(res_sub)[1]
                    if sub_score >= threshold:
                        # 【新增】：在组合图像查找中增加详细日志返回
                        main_score = res_main[y, x]
                        self.log(f"[ComboMatch] 命中: {main_path}+{sub_path} | 主图得分: {main_score:.3f} | 元素得分: {sub_score:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                        return (
                            x + w_m // 2 + (region[0] if region else 0),
                            y + h_m // 2 + (region[1] if region else 0),
                        )
            return None
        except Exception as e:
            self.log(f"find_image_with_element 异常: {e}")
            return None
    def find_image_with_element_stable(
        self,
        main_path,
        sub_path,
        region=None,
        main_threshold=0.60,
        verify_threshold=0.72,
        sub_threshold=0.70,
        max_candidates=15
    ):
        if not self.is_running:
            return None

        try:
            screen_bgr = self.capture_region(region)
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

            main_tpl = self.load_template_gray(main_path)
            sub_tpl = self.load_template_gray(sub_path)

            if main_tpl is None or sub_tpl is None:
                return None

            h_m, w_m = main_tpl.shape[:2]
            h_s, w_s = sub_tpl.shape[:2]

            if h_m > screen_gray.shape[0] or w_m > screen_gray.shape[1]:
                return None

            res_main = cv2.matchTemplate(screen_gray, main_tpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res_main >= main_threshold)

            if len(xs) == 0:
                return None

            candidates = [(float(res_main[y, x]), x, y) for x, y in zip(xs, ys)]
            candidates.sort(key=lambda t: t[0], reverse=True)

            checked = set()
            checked_count = 0

            for main_score, x, y in candidates:
                key = (x // 8, y // 8)
                if key in checked:
                    continue
                checked.add(key)

                checked_count += 1
                if checked_count > max_candidates:
                    break

                pad = 8
                x1 = max(0, x - pad)
                y1 = max(0, y - pad)
                x2 = min(screen_gray.shape[1], x + w_m + pad)
                y2 = min(screen_gray.shape[0], y + h_m + pad)

                sub_roi = screen_gray[y1:y2, x1:x2]
                if sub_roi.shape[0] < h_s or sub_roi.shape[1] < w_s:
                    continue

                res_sub = cv2.matchTemplate(sub_roi, sub_tpl, cv2.TM_CCOEFF_NORMED)
                sub_score = cv2.minMaxLoc(res_sub)[1]

                if main_score >= verify_threshold and sub_score >= sub_threshold:
                    cx = x + w_m // 2
                    cy = y + h_m // 2
                    if region:
                        cx += region[0]
                        cy += region[1]
                    # 【新增】：打印稳定版组合匹配的详细得分
                    self.log(f"[StableMatch] 命中: {main_path}+{sub_path} | 主图: {main_score:.3f} (需>{verify_threshold}) | 元素: {sub_score:.3f} (需>{sub_threshold})")
                    return (cx, cy)

            return None

        except Exception as e:
            self.log(f"find_image_with_element_stable 识别报错: {e}")
            return None
    def find_image_with_element_multi(self, main_path, sub_path, region=None, fast_mode=True,
        main_threshold=0.60, like_threshold=0.75, final_threshold=0.72, mask_areas=None):
        if not self.is_running:
            return None

        try:
            screen_bgr = self.capture_region(region, mask_areas=mask_areas)
            screen_gray = self.to_gray_image(screen_bgr)
            screen_edge = self.to_edge_image(screen_bgr)

            scales_to_try = self.get_scales_to_try(fast_mode=fast_mode)

            for scale in scales_to_try:
                main_tpl_c, _ = self.get_scaled_template(main_path, scale)
                sub_tpl_c, _ = self.get_scaled_template(sub_path, scale)

                if main_tpl_c is None or sub_tpl_c is None:
                    continue

                main_tpl_gray = self.to_gray_image(main_tpl_c)
                main_tpl_edge = self.to_edge_image(main_tpl_c)

                h_m, w_m = main_tpl_c.shape[:2]
                if h_m < 5 or w_m < 5:
                    continue
                if h_m > screen_bgr.shape[0] or w_m > screen_bgr.shape[1]:
                    continue

                # 用彩色主模板先找候选，门槛放低
                res_main = cv2.matchTemplate(screen_bgr, main_tpl_c, cv2.TM_CCOEFF_NORMED)
                loc = np.where(res_main >= main_threshold)

                # ==========================================
                # 【核心魔法】：强制从左到右、从上到下排序！
                # 保证在有多个相同目标时，绝对按顺序点击！
                # ==========================================
                points = list(zip(*loc[::-1]))
                points.sort(key=lambda p: (p[1] // 50, p[0])) 

                checked_points = set()

                for pt in points:
                    x, y = pt

                    # 去重，避免同一辆车计算多次
                    key = (x // 10, y // 10)
                    if key in checked_points:
                        continue
                    checked_points.add(key)

                    roi_bgr = screen_bgr[y:y + h_m, x:x + w_m]
                    roi_gray = screen_gray[y:y + h_m, x:x + w_m]
                    roi_edge = screen_edge[y:y + h_m, x:x + w_m]

                    if roi_bgr.shape[:2] != main_tpl_c.shape[:2]:
                        continue

                    # 四维打分系统 (抗 HDR 核心)
                    color_score = self.match_template_score(roi_bgr, main_tpl_c)
                    gray_score = self.match_template_score(roi_gray, main_tpl_gray)
                    edge_score = self.match_template_score(roi_edge, main_tpl_edge)

                    roi_center = self.crop_center_ratio(roi_bgr, ratio=0.6)
                    tpl_center = self.crop_center_ratio(main_tpl_c, ratio=0.6)
                    center_score = self.match_template_score(roi_center, tpl_center)

                    # 标签匹配 (NEW 标签或作者点赞标签)
                    pad = 80

                    try:
                        user_image_config = getattr(self, "user_image_config", {}) or {}

                        if main_path == "skillcar.png" and sub_path == "liketag.png":
                            pad = int(user_image_config.get("race_car_search_range", 80))
                        
                        elif main_path == "newCC.png" and sub_path == "newcartag.png":
                            pad = int(user_image_config.get("new_car_search_range", 5))
                        
                    except Exception:
                        pad = 80

                    sub_roi = screen_bgr[
                        max(0, y - pad):min(screen_bgr.shape[0], y + h_m + pad),
                        max(0, x - pad):min(screen_bgr.shape[1], x + w_m + pad),
                    ]
                    like_score = self.match_template_score(sub_roi, sub_tpl_c)

                    if like_score < like_threshold:
                        continue

                    # 综合计算总分
                    final_score = (
                        color_score * 0.30 +
                        gray_score * 0.20 +
                        edge_score * 0.20 +
                        center_score * 0.15 +
                        like_score * 0.15
                    )

                    curr_pos = (
                        x + w_m // 2 + (region[0] if region else 0),
                        y + h_m // 2 + (region[1] if region else 0),
                    )

                    # 只要及格，立刻返回（因为已经排过序了，第一个及格的一定是左上角的第一个目标）
                    if final_score >= final_threshold:
                        self.log(
                            f"[MultiMatch] 锁定目标: {main_path}+{sub_path} | "
                            f"Pos: ({curr_pos[0]},{curr_pos[1]}) | "
                            f"Range: {pad} | "
                            f"综合: {final_score:.3f} | 彩色: {color_score:.3f} | "
                            f"灰度: {gray_score:.3f} | 边缘: {edge_score:.3f} | "
                            f"中心: {center_score:.3f} | 标签: {like_score:.3f}"
                        )
                        return curr_pos

            return None

        except Exception as e:
            self.log(f"find_image_with_element_multi 异常: {e}")
            return None
    
    def find_image_with_element_fast(self, main_path, sub_path, region=None, threshold=0.70, sub_threshold=0.70):
        if not self.is_running:
            return None

        try:
            screen_bgr = self.capture_region(region)
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

            main_tpl = self.load_template_gray(main_path)
            sub_tpl = self.load_template_gray(sub_path)

            if main_tpl is None or sub_tpl is None:
                return None

            h_m, w_m = main_tpl.shape[:2]
            h_s, w_s = sub_tpl.shape[:2]

            if h_m > screen_gray.shape[0] or w_m > screen_gray.shape[1]:
                return None

            res_main = cv2.matchTemplate(screen_gray, main_tpl, cv2.TM_CCOEFF_NORMED)
            loc = np.where(res_main >= threshold)

            checked = set()

            for pt in zip(*loc[::-1]):
                x, y = pt

                # 去重，避免相邻重复点太多
                key = (x // 10, y // 10)
                if key in checked:
                    continue
                checked.add(key)

                x1 = max(0, x - 5)
                y1 = max(0, y - 5)
                x2 = min(screen_gray.shape[1], x + w_m + 5)
                y2 = min(screen_gray.shape[0], y + h_m + 5)

                sub_roi = screen_gray[y1:y2, x1:x2]

                if sub_roi.shape[0] < h_s or sub_roi.shape[1] < w_s:
                    continue

                res_sub = cv2.matchTemplate(sub_roi, sub_tpl, cv2.TM_CCOEFF_NORMED)
                _, max_val_sub, _, _ = cv2.minMaxLoc(res_sub)

                if max_val_sub >= sub_threshold:
                    cx = x + w_m // 2
                    cy = y + h_m // 2
                    if region:
                        cx += region[0]
                        cy += region[1]
                    # 【新增】：打印快速匹配模式得分
                    main_score = res_main[y, x]
                    self.log(f"[FastMatch] 命中: {main_path}+{sub_path} | 主图: {main_score:.3f} (需>{threshold}) | 元素: {max_val_sub:.3f} (需>{sub_threshold})")
                    return (cx, cy)

            return None

        except Exception as e:
            self.log(f"find_image_with_element_fast 异常: {e}")
            return None

    def wait_for_image_with_element_multi(self, main_path, sub_path, region=None, fast_mode=True,
        main_threshold=0.60, like_threshold=0.75,
        final_threshold=0.72, timeout=30, interval=0.4):
        start = time.time()

        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_with_element_multi(
                main_path=main_path,
                sub_path=sub_path,
                region=region,
                fast_mode=fast_mode,
                main_threshold=main_threshold,
                like_threshold=like_threshold,
                final_threshold=final_threshold
            )
            if pos:
                return pos

            time.sleep(0.15)
            pos = self.find_image_with_element_multi(
                main_path=main_path,
                sub_path=sub_path,
                region=region,
                fast_mode=fast_mode,
                main_threshold=main_threshold,
                like_threshold=like_threshold,
                final_threshold=final_threshold
            )
            if pos:
                self.log(f"[RetryMatch] 재탐색 성공: {main_path}+{sub_path}")
                return pos

            sleep_end = time.time() + interval

            while self.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None

    def load_template_transparent(self, template_path):
        """专门加载带有 Alpha 透明通道的图片"""
        actual_path = get_img_path(template_path)
        cache_key = ("transparent", actual_path)
        if not hasattr(self, "template_transparent_cache"):
            self.template_transparent_cache = {}
        if cache_key in self.template_transparent_cache:
            return self.template_transparent_cache[cache_key]
            
        # 注意这里的 cv2.IMREAD_UNCHANGED，它会保留透明通道 (BGRA)
        tpl = cv2.imread(actual_path, cv2.IMREAD_UNCHANGED)
        if tpl is not None:
            self.template_transparent_cache[cache_key] = tpl
        return tpl
    def find_image_transparent(self, template_path, region=None, threshold=0.70, fast_mode=True):
        """带透明通道的匹配：彻底无视透明背景，只匹配图像主体"""
        if not self.is_running:
            return None
        try:
            screen_bgr = self.capture_region(region)
            tpl_bgra = self.load_template_transparent(template_path)
            
            if tpl_bgra is None:
                return None
            # 如果图片没有透明通道(不是4通道)，降级为普通匹配
            if tpl_bgra.shape[2] != 4:
                return self.find_image_in_screen(screen_bgr, template_path, region, threshold, fast_mode)
            scales_to_try = self.get_scales_to_try(fast_mode=fast_mode)
            for scale in scales_to_try:
                # 对带有透明通道的原图进行缩放
                if scale == 1.0:
                    tpl_scaled = tpl_bgra.copy()
                else:
                    tpl_scaled = cv2.resize(tpl_bgra, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                h, w = tpl_scaled.shape[:2]
                if h < 5 or w < 5 or h > screen_bgr.shape[0] or w > screen_bgr.shape[1]:
                    continue
                # 分离出 BGR 色彩层 和 Alpha 透明遮罩层
                tpl_bgr = tpl_scaled[:, :, :3]
                alpha_mask = tpl_scaled[:, :, 3]
                                # 核心魔法：带 mask 的匹配！透明区域不参与算分！
                res = cv2.matchTemplate(screen_bgr, tpl_bgr, cv2.TM_CCOEFF_NORMED, mask=alpha_mask)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                if max_val >= threshold:
                    # 【新增】：带透明通道的匹配日志
                    self.log(f"[AlphaMatch] 命中(无视背景): {template_path} | 得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                    return (
                        max_loc[0] + w // 2 + (region[0] if region else 0),
                        max_loc[1] + h // 2 + (region[1] if region else 0),
                    )
            return None
        except Exception as e:
            self.log(f"find_image_transparent 异常: {e}")
            return None
    def wait_for_image_transparent(self, template_path, region=None, threshold=0.70, timeout=30, interval=0.4, fast_mode=True):
        """等待带有透明背景的图片"""
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_transparent(template_path, region, threshold, fast_mode)
            if pos:
                return pos
            time.sleep(interval)
        return None
    def wait_for_image_with_element_stable(
        self,
        main_path,
        sub_path,
        region=None,
        main_threshold=0.60,
        verify_threshold=0.72,
        sub_threshold=0.70,
        max_candidates=15,
        timeout=3,
        interval=0.2
    ):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_with_element_stable(
                main_path=main_path,
                sub_path=sub_path,
                region=region,
                main_threshold=main_threshold,
                verify_threshold=verify_threshold,
                sub_threshold=sub_threshold,
                max_candidates=max_candidates
            )
            if pos:
                return pos
            time.sleep(interval)
        return None
    def wait_for_image_with_element_fast(
        self,
        main_path,
        sub_path,
        region=None,
        threshold=0.70,
        sub_threshold=0.70,
        timeout=4,
        interval=0.25
    ):
        start = time.time()

        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_with_element_fast(
                main_path=main_path,
                sub_path=sub_path,
                region=region,
                threshold=threshold,
                sub_threshold=sub_threshold
            )
            if pos:
                return pos

            time.sleep(interval)

        return None

    # ==========================================
    # --- 【终极安全锁 V5.1】：排他 + 右下角调校精准狙击 + 强制从左到右 ---
    # ==========================================
    def find_image_ultimate_safe(self, main_path, anti_path, region=None, main_threshold=0.80, anti_threshold=0.65):
        if not self.is_running: return None
        try:
            screen_bgr = self.capture_region(region)
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

            scales_to_try = self.get_scales_to_try(fast_mode=True)

            for scale in scales_to_try:
                main_tpl_bgr, _ = self.get_scaled_template(main_path, scale)
                anti_tpl_bgr, _ = self.get_scaled_template(anti_path, scale)

                if main_tpl_bgr is None or anti_tpl_bgr is None: continue
                
                main_tpl_gray = cv2.cvtColor(main_tpl_bgr, cv2.COLOR_BGR2GRAY)
                h_m, w_m = main_tpl_bgr.shape[:2]
                h_a, w_a = anti_tpl_bgr.shape[:2]

                if h_m < 10 or w_m < 10 or h_m > screen_bgr.shape[0] or w_m > screen_bgr.shape[1]:
                    continue

                # 1. 基础彩色初筛
                res_main = cv2.matchTemplate(screen_bgr, main_tpl_bgr, cv2.TM_CCOEFF_NORMED)
                loc = np.where(res_main >= main_threshold)

                
                points = list(zip(*loc[::-1]))
                # 强制按 X 坐标（从左到右）优先排序，无视上下排
                points.sort(key=lambda p: (p[0] // 50, p[1]))

                checked = set()
                for pt in points:
                    x, y = pt
                    if (x // 10, y // 10) in checked: continue
                    checked.add((x // 10, y // 10))

                    base_score = res_main[y, x]
                    
                    roi_bgr = screen_bgr[y:y+h_m, x:x+w_m]
                    roi_gray = screen_gray[y:y+h_m, x:x+w_m]
                    if roi_bgr.shape[:2] != main_tpl_bgr.shape[:2]: continue

                    # ==================================
                    # 防线 1: 排他校验
                    # ==================================
                    pad_anti = 10
                    roi_y1, roi_y2 = max(0, y - pad_anti), min(screen_bgr.shape[0], y + h_m + pad_anti)
                    roi_x1, roi_x2 = max(0, x - pad_anti), min(screen_bgr.shape[1], x + w_m + pad_anti)
                    anti_roi = screen_bgr[roi_y1:roi_y2, roi_x1:roi_x2]

                    if anti_roi.shape[0] >= h_a and anti_roi.shape[1] >= w_a:
                        res_anti = cv2.matchTemplate(anti_roi, anti_tpl_bgr, cv2.TM_CCOEFF_NORMED)
                        _, anti_score, _, _ = cv2.minMaxLoc(res_anti)
                        if anti_score >= anti_threshold:
                            self.log(f"[排他拦截]: 发现 NEW 标签 ({anti_score:.2f})，放弃该目标。")
                            continue

                    # ==================================
                    # 防线 2: 顶部文字
                    # ==================================
                    top_h = int(h_m * 0.25)
                    tpl_top = main_tpl_gray[:top_h, :]
                    
                    score_top = 0.0
                    pad_slide = 5 
                    if top_h > pad_slide*2 and w_m > pad_slide*2:
                        tpl_top_core = tpl_top[pad_slide:-pad_slide, pad_slide:-pad_slide]
                        search_top = roi_gray[:int(h_m * 0.35), :]
                        if search_top.shape[0] >= tpl_top_core.shape[0] and search_top.shape[1] >= tpl_top_core.shape[1]:
                            res_top = cv2.matchTemplate(search_top, tpl_top_core, cv2.TM_CCOEFF_NORMED)
                            _, score_top, _, _ = cv2.minMaxLoc(res_top)

                    # ==================================
                    # 防线 3: 【右下角】
                    # ==================================
                    bottom_h = int(h_m * 0.25)
                    right_w = int(w_m * 0.35)
                    tpl_pi_box = main_tpl_bgr[h_m - bottom_h:, w_m - right_w:]

                    score_bot = 0.0
                    if bottom_h > pad_slide*2 and right_w > pad_slide*2:
                        tpl_pi_core = tpl_pi_box[pad_slide:-pad_slide, pad_slide:-pad_slide]
                        search_y1 = h_m - int(h_m * 0.35)
                        search_x1 = w_m - int(w_m * 0.45)
                        search_bot = roi_bgr[search_y1:, search_x1:]
                        
                        if search_bot.shape[0] >= tpl_pi_core.shape[0] and search_bot.shape[1] >= tpl_pi_core.shape[1]:
                            res_bot = cv2.matchTemplate(search_bot, tpl_pi_core, cv2.TM_CCOEFF_NORMED)
                            _, score_bot, _, _ = cv2.minMaxLoc(res_bot)

                    if base_score >= 0.76 and score_top >= 0.75 and score_bot >= 0.85:
                        self.log(f"[终极安全-通过]: 锁定目标！总分:{base_score:.3f} | 顶部车名:{score_top:.2f} | 右下调校:{score_bot:.2f}")
                        return (x + w_m // 2 + (region[0] if region else 0), y + h_m // 2 + (region[1] if region else 0))
                    else:
                        pass # 静默拦截，继续寻找下一个坐标

            return None
        except Exception as e:
            self.log(f"ultimate_safe 异常: {e}")
            return None
    def wait_for_image_ultimate_safe(self, main_path, anti_path, region=None, main_threshold=0.80, anti_threshold=0.65, timeout=3, interval=0.2):
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_ultimate_safe(main_path, anti_path, region, main_threshold, anti_threshold)
            if pos: return pos
            time.sleep(interval)
        return None
    def find_image_smart(self, template_path, primary_region=None, fallback_region=None, threshold=0.75, fast_mode=True):
        if primary_region:
            pos = self.find_image(template_path, region=primary_region, threshold=threshold, fast_mode=fast_mode)
            if pos:
                return pos

        if fallback_region:
            return self.find_image(template_path, region=fallback_region, threshold=threshold, fast_mode=fast_mode)

        return None
    def to_gray_image(self, img):
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    def to_edge_image(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        edge = cv2.Canny(blur, 50, 150)
        return edge
    def crop_center_ratio(self, img, ratio=0.6):
        h, w = img.shape[:2]
        ch = int(h * ratio)
        cw = int(w * ratio)
        y1 = max(0, (h - ch) // 2)
        x1 = max(0, (w - cw) // 2)
        return img[y1:y1 + ch, x1:x1 + cw]
    def find_image_gray(self, template_path, region=None, threshold=0.75, fast_mode=True, invert_mode=False):
        if not self.is_running:
            return None

        try:
            screen_bgr = self.capture_region(region)
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            scales_to_try = self.get_scales_to_try(fast_mode=fast_mode)

            best_val = -1.0
            best_scale = None
            best_mode = "원본"

            tpl_gray_raw = self.load_template_gray(template_path)
            if tpl_gray_raw is None:
                return None

            for scale in scales_to_try:
                tpl_gray = tpl_gray_raw
                if scale != 1.0:
                    tpl_gray = cv2.resize(tpl_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

                h, w = tpl_gray.shape[:2]
                if h < 5 or w < 5 or h > screen_gray.shape[0] or w > screen_gray.shape[1]:
                    continue

                res = cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)

                if max_val > best_val:
                    best_val = max_val
                    best_scale = scale
                    best_mode = "원본"

                if max_val >= threshold:
                    self.log(f"[GrayMatch] 命中: {template_path} | 模式: 原图 | 灰度得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                    return (
                        max_loc[0] + w // 2 + (region[0] if region else 0),
                        max_loc[1] + h // 2 + (region[1] if region else 0),
                    )

                if invert_mode:
                    tpl_inv = 255 - tpl_gray
                    res_inv = cv2.matchTemplate(screen_gray, tpl_inv, cv2.TM_CCOEFF_NORMED)
                    _, max_val_inv, _, max_loc_inv = cv2.minMaxLoc(res_inv)

                    if max_val_inv > best_val:
                        best_val = max_val_inv
                        best_scale = scale
                        best_mode = "반전"

                    if max_val_inv >= threshold:
                        self.log(f"[GrayMatch] 命中: {template_path} | 模式: 反相 | 灰度得分: {max_val_inv:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                        return (
                            max_loc_inv[0] + w // 2 + (region[0] if region else 0),
                            max_loc_inv[1] + h // 2 + (region[1] if region else 0),
                        )

            if self.config.get("image_debug_log", False):
                self.log(
                    f"[GrayFail] {template_path} | "
                    f"최고점수: {best_val:.3f} | "
                    f"최고스케일: {best_scale} | "
                    f"모드: {best_mode} | "
                    f"임계값: {threshold} | "
                    f"fast_mode: {fast_mode}"
                )
            
            return None

        except Exception as e:
            self.log(f"find_image_gray 异常: {e}")
            return None
    
    def find_any_image_gray(self, image_list, region=None, threshold=0.75, fast_mode=True, invert_mode=False):
        if not self.is_running:
            return None

        try:
            screen_bgr = self.capture_region(region)
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            scales_to_try = self.get_scales_to_try(fast_mode=fast_mode)

            best_val = -1.0
            best_scale = None
            best_img = None
            best_mode = "원본"

            for img_path in image_list:
                tpl_gray_raw = self.load_template_gray(img_path)
                if tpl_gray_raw is None:
                    continue

                for scale in scales_to_try:
                    tpl_gray = tpl_gray_raw
                    if scale != 1.0:
                        tpl_gray = cv2.resize(tpl_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

                    h, w = tpl_gray.shape[:2]
                    if h < 5 or w < 5 or h > screen_gray.shape[0] or w > screen_gray.shape[1]:
                        continue

                    res = cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)

                    if max_val > best_val:
                        best_val = max_val
                        best_scale = scale
                        best_img = img_path
                        best_mode = "원본"

                    if max_val >= threshold:
                        self.log(f"[GrayMatchAny] 命中: {img_path} | 模式: 原图 | 灰度得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                        return (
                            max_loc[0] + w // 2 + (region[0] if region else 0),
                            max_loc[1] + h // 2 + (region[1] if region else 0),
                        )

                    if invert_mode:
                        tpl_inv = 255 - tpl_gray
                        res_inv = cv2.matchTemplate(screen_gray, tpl_inv, cv2.TM_CCOEFF_NORMED)
                        _, max_val_inv, _, max_loc_inv = cv2.minMaxLoc(res_inv)

                        if max_val_inv > best_val:
                            best_val = max_val_inv
                            best_scale = scale
                            best_img = img_path
                            best_mode = "반전"

                        if max_val_inv >= threshold:
                            self.log(f"[GrayMatchAny] 命中: {img_path} | 模式: 反相 | 灰度得分: {max_val_inv:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                            return (
                                max_loc_inv[0] + w // 2 + (region[0] if region else 0),
                                max_loc_inv[1] + h // 2 + (region[1] if region else 0),
                            )

            if self.config.get("image_debug_log", False):
                self.log(
                    f"[GrayFailAny] {image_list} | "
                    f"최고이미지: {best_img} | "
                    f"최고점수: {best_val:.3f} | "
                    f"최고스케일: {best_scale} | "
                    f"모드: {best_mode} | "
                    f"임계값: {threshold} | "
                    f"fast_mode: {fast_mode}"
                )
            return None

        except Exception as e:
            self.log(f"find_any_image_gray 异常: {e}")
            return None

    def wait_for_any_image_gray(self, image_list, region=None, threshold=0.75, timeout=30, interval=0.3, fast_mode=True, invert_mode=False):
        """等待多张灰度图中的任意一张出现（已补全 fast_mode 参数）"""
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_any_image_gray(image_list, region=region, threshold=threshold, fast_mode=fast_mode, invert_mode=invert_mode)
            if pos:
                return pos
            
            time.sleep(0.15)
            pos = self.find_any_image_gray(image_list, region=region, threshold=threshold, fast_mode=fast_mode, invert_mode=invert_mode)
            if pos:
                self.log(f"[RetryMatch] 재탐색 성공: {image_list}")
                return pos

            # 安全等待机制，防止卡死
            sleep_end = time.time() + interval
            while self.is_running and time.time() < sleep_end:
                time.sleep(0.05)
        return None
    
    def wait_for_image_gray(self, template_path, region=None, threshold=0.75, timeout=30, interval=0.3, fast_mode=True, invert_mode=False):
        start = time.time()

        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_gray(template_path, region=region, threshold=threshold, fast_mode=fast_mode, invert_mode=invert_mode)
            if pos:
                return pos

            time.sleep(0.15)
            pos = self.find_image_gray(template_path, region=region, threshold=threshold, fast_mode=fast_mode, invert_mode=invert_mode)
            if pos:
                self.log(f"[RetryMatch] 재탐색 성공: {template_path}")
                return pos

            sleep_end = time.time() + interval
            while self.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None
    
    def click_and_confirm_disappear(
        self,
        pos,
        old_image,
        region=None,
        threshold=0.75,
        timeout=3,
        retry_click=True,
        double=False
    ):
        if not pos:
            return False

        # 1차 클릭
        self.game_click(pos, double=double)
        time.sleep(0.4)

        # 이전 이미지가 사라졌는지 확인
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            still_pos = self.find_image_gray(
                old_image,
                region=region,
                threshold=threshold,
                fast_mode=True
            )

            if not still_pos:
                self.log(f"[ClickConfirm] 화면 전환 확인: {old_image} 사라짐")
                return True

            time.sleep(0.2)

        # 아직 이전 이미지가 보이면 클릭 실패 가능성
        if retry_click:
            self.log(f"[ClickConfirm] {old_image} 아직 보임. 같은 위치 재클릭")
            self.game_click(pos, double=double)
            time.sleep(0.5)

            # 재클릭 후 한 번 더 확인
            start = time.time()
            while self.is_running and time.time() - start < timeout:
                still_pos = self.find_image_gray(
                    old_image,
                    region=region,
                    threshold=threshold,
                    fast_mode=True
                )

                if not still_pos:
                    self.log(f"[ClickConfirm] 재클릭 후 화면 전환 확인: {old_image}")
                    return True

                time.sleep(0.2)

        self.log(f"[ClickConfirm] 클릭 후에도 {old_image}가 남아 있음")
        return False
    
    def click_and_wait_next(
        self,
        pos,
        next_images,
        next_region=None,
        next_threshold=0.75,
        timeout=5,
        retry_times=2,
        double=False,
        gray=True
    ):
        if not pos:
            return None

        if isinstance(next_images, str):
            next_images = [next_images]

        for attempt in range(retry_times + 1):
            self.game_click(pos, double=double)
            time.sleep(0.5)

            if gray:
                next_pos = self.wait_for_any_image_gray(
                    next_images,
                    region=next_region,
                    threshold=next_threshold,
                    timeout=timeout,
                    interval=0.25,
                    fast_mode=True
                )
            else:
                next_pos = self.wait_for_any_image(
                    next_images,
                    region=next_region,
                    threshold=next_threshold,
                    timeout=timeout,
                    interval=0.25,
                    fast_mode=True
                )

            if next_pos:
                self.log(f"[NextConfirm] 다음 화면 확인 성공: {next_images}")
                return next_pos

            self.log(f"[NextConfirm] 다음 화면 미확인. 클릭 재시도 {attempt + 1}/{retry_times}")

        return None

    def find_any_image_transparent(self, image_list, region=None, threshold=0.70, fast_mode=True):
        """查找多张带透明通道的图片中的任意一张"""
        if not self.is_running:
            return None
        try:
            screen_bgr = self.capture_region(region)
            scales_to_try = self.get_scales_to_try(fast_mode=fast_mode)

            for template_path in image_list:
                tpl_bgra = self.load_template_transparent(template_path)
                if tpl_bgra is None:
                    continue
                
                # 如果图片没有透明通道，降级为普通匹配
                if tpl_bgra.shape[2] != 4:
                    pos = self.find_image_in_screen(screen_bgr, template_path, region, threshold, fast_mode)
                    if pos: return pos
                    continue

                for scale in scales_to_try:
                    if scale == 1.0:
                        tpl_scaled = tpl_bgra.copy()
                    else:
                        tpl_scaled = cv2.resize(tpl_bgra, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

                    h, w = tpl_scaled.shape[:2]
                    if h < 5 or w < 5 or h > screen_bgr.shape[0] or w > screen_bgr.shape[1]:
                        continue

                    tpl_bgr = tpl_scaled[:, :, :3]
                    alpha_mask = tpl_scaled[:, :, 3]

                    res = cv2.matchTemplate(screen_bgr, tpl_bgr, cv2.TM_CCOEFF_NORMED, mask=alpha_mask)
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)

                    if max_val >= threshold:
                        # 【新增】：多张带透明通道的匹配日志
                        self.log(f"[AlphaMatchAny] 命中(无视背景): {template_path} | 得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}")
                        return (
                            max_loc[0] + w // 2 + (region[0] if region else 0),
                            max_loc[1] + h // 2 + (region[1] if region else 0),
                        )
            return None
        except Exception as e:
            self.log(f"find_any_image_transparent 异常: {e}")
            return None

    def wait_for_any_image_transparent(self, image_list, region=None, threshold=0.70, timeout=30, interval=0.4, fast_mode=True):
        """等待带有透明背景的多张图片中的任意一张出现"""
        start = time.time()
        while self.is_running and time.time() - start < timeout:
            pos = self.find_any_image_transparent(image_list, region, threshold, fast_mode)
            if pos:
                return pos
            
            sleep_end = time.time() + interval
            while self.is_running and time.time() < sleep_end:
                time.sleep(0.05)
        return None
    def wait_for_any_image(self, image_list, region=None, threshold=0.75, timeout=30, interval=0.4, fast_mode=True, log_text=None):
        start = time.time()

        while self.is_running and time.time() - start < timeout:
            try:
                # 1차 탐색
                screen_bgr = self.capture_region(region)

                for img_path in image_list:
                    pos = self.find_image_in_screen(
                        screen_bgr,
                        img_path,
                        region=region,
                        threshold=threshold,
                        fast_mode=fast_mode
                    )
                    if pos:
                        return pos

                # 2차 재탐색: 화면을 새로 캡처해서 다시 검사
                time.sleep(0.15)
                screen_bgr = self.capture_region(region)

                for img_path in image_list:
                    pos = self.find_image_in_screen(
                        screen_bgr,
                        img_path,
                        region=region,
                        threshold=threshold,
                        fast_mode=fast_mode
                    )
                    if pos:
                        self.log(f"[RetryMatch] 재탐색 성공: {img_path}")
                        return pos

            except Exception as e:
                self.log(f"wait_for_any_image 异常: {e}")

            if log_text:
                self.log(log_text)

            sleep_end = time.time() + interval
            while self.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None

    def wait_for_image(self, template_path, region=None, threshold=0.75, timeout=30, interval=0.4, fast_mode=True, log_text=None):
        return self.wait_for_any_image(
            [template_path],
            region=region,
            threshold=threshold,
            timeout=timeout,
            interval=interval,
            fast_mode=fast_mode,
            log_text=log_text
        )

    def wait_for_image_with_element(self, main_path, sub_path, region=None, threshold=0.85, timeout=30, interval=0.4, fast_mode=True):
        start = time.time()

        while self.is_running and time.time() - start < timeout:
            pos = self.find_image_with_element(
                main_path,
                sub_path,
                region=region,
                threshold=threshold,
                fast_mode=fast_mode
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None

    def match_template_score(self, src, tpl):
        try:
            if tpl is None or src is None:
                return 0.0
            th, tw = tpl.shape[:2]
            sh, sw = src.shape[:2]
            if th < 5 or tw < 5 or th > sh or tw > sw:
                return 0.0
            res = cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED)
            return cv2.minMaxLoc(res)[1]
        except Exception:
            return 0.0
    # ==========================================
    # --- 模块：跑图前置与循环跑图 ---
    # ==========================================
    def logic_race(self, target_count):
        if self.race_counter >= target_count:
            return True

        self.update_running_ui("循环跑图", self.race_counter, target_count)

        self.log("准备验证/进入菜单...")
        if not self.enter_menu():
            return False

        self.log("切换到创意中心...")
        for _ in range(4):
            self.hw_press("pagedown", delay=0.15)
            time.sleep(0.3)

        time.sleep(0.8)


        pos_el = self.wait_for_image_gray(
            "eventlab.png",
            region=self.regions["全界面"],
            threshold=0.7,
            timeout=5,
            interval=0.25,
            fast_mode=True
        )
    
        if not pos_el:
            self.log("未找到 eventlab")
            return False

        pos_yg = self.click_and_wait_next(
            pos_el,
            "playenent.png",
            next_region=self.regions["中间"],
            next_threshold=0.75,
            timeout=8,
            retry_times=2,
            double=False,
            gray=True
        )
        
        if not pos_yg:
            self.log("未找到游玩赛事")
            return False

        pos_ck = self.click_and_wait_next(
            pos_yg,
            "VEI.png",
            next_region=self.regions["下"],
            next_threshold=0.75,
            timeout=20,
            retry_times=2,
            double=False,
            gray=True
        )

        if not pos_ck:
            self.log("链接超时")
            return False

        race_mode = 1
        if hasattr(self, "opt_race_mode"):
            race_mode = self.race_mode_values.get(self.opt_race_mode.get(), 1)

        if race_mode == 1:
            # 기존 공유코드 입력 방식
            self.hw_press("backspace")
            time.sleep(0.8)
            self.hw_press("up")
            time.sleep(0.4)
            self.hw_press("enter")
            time.sleep(0.8)

            code_text = "".join(c for c in self.entry_share.get() if c.isdigit())

            self.log(f"공유코드 입력 준비: {code_text}")

            if not code_text:
                self.log("공유코드가 비어 있습니다. 입력을 중단합니다.")
                return False

            time.sleep(1.0)

            if getattr(self, "use_win32_input", False):
                self.log("공유코드 입력 방식: Win32 정밀 숫자 입력")

                for char in code_text:
                    if not self.is_running:
                        return False

                    self.log(f"공유코드 숫자 입력: {char}")

                    if not self.win32_press_digit_precise(char, hold=0.08):
                        self.log(f"공유코드 숫자 입력 실패: {char}")
                        return False

                    time.sleep(0.12)

            else:
                self.log("공유코드 입력 방식: 기존 키 입력")

                for char in code_text:
                    if not self.is_running:
                        return False

                    if char in DIK_CODES:
                        self.hw_press(char, delay=0.08)
                        time.sleep(0.12)

            time.sleep(0.6)
            self.log("공유코드 입력 단계 종료")

            self.hw_press("enter")
            time.sleep(0.8)
            self.hw_press("down")
            time.sleep(0.3)
            self.hw_press("enter")
            time.sleep(1.5)

        elif race_mode == 2:
            # 첫번째 즐겨찾기 맵 사용 방식
            self.log("첫번째 즐겨찾기 맵 사용 모드: PageDown 7회로 이동합니다.")

            for _ in range(7):
                if not self.is_running:
                    return False
                self.hw_press("pagedown", delay=0.12)
                time.sleep(0.25)
            time.sleep(1.5)

        elif race_mode == 3:
            # 마지막 플레이 맵 사용 방식
            self.log("마지막 플레이 맵 사용 모드: PageDown 8회로 이동합니다.")

            for _ in range(8):
                if not self.is_running:
                    return False
                self.hw_press("pagedown", delay=0.12)
                time.sleep(0.25)
            time.sleep(1.5)
        
        self.hw_press("enter")
        time.sleep(2.0)
        self.hw_press("enter")
        time.sleep(2.0)

        race_car_mode = 1
        if hasattr(self, "opt_race_car_mode"):
            race_car_mode = self.race_car_mode_values.get(self.opt_race_car_mode.get(), 1)
        
        if race_car_mode == 2:
            self.log("레이스 차량 모드 2: 즐겨찾기 차량 바로 탑승을 시도합니다.")
        
            self.hw_press("y")
            time.sleep(0.6)
        
            self.hw_press("enter")
            time.sleep(1.2)

            self.hw_press("esc")
            time.sleep(1.2)
        
            self.log("즐겨찾기 차량 선택 완료. 차량 탑승 Enter를 입력합니다.")
        
            self.hw_press("enter", delay=0.2)
            time.sleep(4.0)
        
        else:
            race_car_main_threshold, race_car_like_threshold, race_car_final_threshold, race_car_fast_mode = self.get_race_car_search_settings()

            pos_target = self.wait_for_image_with_element_multi(
                "skillcar.png",
                "liketag.png",
                region=self.regions["全界面"],
                fast_mode=race_car_fast_mode,
                main_threshold=race_car_main_threshold,
                like_threshold=race_car_like_threshold,
                final_threshold=race_car_final_threshold,
                timeout=2,
                interval=0.25
            )

            if not pos_target:
                self.log("목표 차량을 찾지 못했습니다. 즐겨찾기 차량 목록으로 진입 후 재탐색합니다.")
        
                self.hw_press("y")
                time.sleep(0.5)
                self.hw_press("enter")
                time.sleep(0.8)
                self.hw_press("esc")
                time.sleep(1.0)
        
                race_car_main_threshold, race_car_like_threshold, race_car_final_threshold, race_car_fast_mode = self.get_race_car_search_settings()

                pos_target = self.wait_for_image_with_element_multi(
                    "skillcar.png",
                    "liketag.png",
                    region=self.regions["全界面"],
                    fast_mode=race_car_fast_mode,
                    main_threshold=race_car_main_threshold,
                    like_threshold=race_car_like_threshold,
                    final_threshold=race_car_final_threshold,
                    timeout=2,
                    interval=0.25
                )
                if not pos_target:
                    self.log("즐겨찾기에서도 목표 차량을 찾지 못했습니다. 브랜드 목록으로 이동합니다.")
        
                    self.hw_press("backspace")
                    time.sleep(1.2)
        
                    brand_threshold, brand_fast_mode, brand_up_wait = self.get_brand_search_settings()

                    brand_pos = self.wait_for_any_image_gray(
                        ["CCbrand.png", "CCbrand-b.png"],
                        region=self.regions["全界面"],
                        threshold=brand_threshold,
                        timeout=3,
                        interval=0.25,
                        fast_mode=brand_fast_mode
                    )
        
                    if not brand_pos:
                        self.log("CCbrand.png / CCbrand-b.png를 찾지 못했습니다.")
                        return False
        
                    self.game_click(brand_pos)
                    time.sleep(1.0)
        
                    race_car_main_threshold, race_car_like_threshold, race_car_final_threshold, race_car_fast_mode = self.get_race_car_search_settings()

                    pos_target = self.wait_for_image_with_element_multi(
                        "skillcar.png",
                        "liketag.png",
                        region=self.regions["全界面"],
                        fast_mode=race_car_fast_mode,
                        main_threshold=race_car_main_threshold,
                        like_threshold=race_car_like_threshold,
                        final_threshold=race_car_final_threshold,
                        timeout=2,
                        interval=0.25
                    )
        
                if not pos_target:
                    for _ in range(20):
                        if not self.is_running:
                            return False
        
                        race_car_main_threshold, race_car_like_threshold, race_car_final_threshold, race_car_fast_mode = self.get_race_car_search_settings()

                        pos_target = self.wait_for_image_with_element_multi(
                            "skillcar.png",
                            "liketag.png",
                            region=self.regions["全界面"],
                            fast_mode=race_car_fast_mode,
                            main_threshold=race_car_main_threshold,
                            like_threshold=race_car_like_threshold,
                            final_threshold=race_car_final_threshold,
                            timeout=2,
                            interval=0.25
                        )
        
                        if pos_target:
                            break
                        
                        for _ in range(4):
                            self.hw_press("right", delay=0.08)
                            time.sleep(0.08)
                        time.sleep(0.4)
        
            if not pos_target:
                self.log("翻页未能找到带有 liketag 的刷图车辆！")
                return False
        
            self.game_click(pos_target, double=False)
            time.sleep(1.2)
        
            self.hw_press("enter", delay=0.2)
            time.sleep(3.0)
        
            still_car = self.find_image_gray(
                "skillcar.png",
                region=self.regions["全界面"],
                threshold=0.65,
                fast_mode=True
            )
        
            if still_car:
                self.log("Enter 후에도 차량 선택 화면이 남아 있습니다. 차량 탑승 실패로 판단합니다.")
                return False
        
            time.sleep(4.0)
        
        self.log("前置完成，开始循环跑图！")

        while self.race_counter < target_count:
            if not self.is_running:
                return False

            self.log(f"跑图 {self.race_counter + 1}/{target_count}: 找赛事起点...")

            pos = None
            for _ in range(120):
                if not self.is_running:
                    return False

                pos = self.wait_for_any_image_gray(
                    ["start.png", "startw.png"],
                    region=self.regions["左下"],
                    threshold=0.75,
                    timeout=0.7,
                    interval=0.2,
                    fast_mode=True
                )
                if pos:
                    break

                self.hw_press("down")
                time.sleep(0.25)

            if not pos:
                self.log("找不到赛事起点，退出跑图。")
                return False

            self.log("이벤트 시작 버튼을 클릭합니다.")
            self.game_click(pos)
            time.sleep(0.8)

            still_start = self.wait_for_any_image_gray(
                ["start.png", "startw.png"],
                region=self.regions["左下"],
                threshold=0.75,
                timeout=2.0,
                interval=0.25,
                fast_mode=True
            )

            if still_start:
                self.log("이벤트 시작 버튼이 아직 남아 있습니다. 클릭 실패로 보고 재클릭합니다.")
                self.game_click(still_start)
                time.sleep(1.0)

                still_start = self.wait_for_any_image_gray(
                    ["start.png", "startw.png"],
                    region=self.regions["左下"],
                    threshold=0.75,
                    timeout=2.0,
                    interval=0.25,
                    fast_mode=True
                )

                if still_start:
                    self.log(
                        f"레이스 {self.race_counter + 1}/{target_count}: "
                        f"재클릭 후에도 이벤트 시작 버튼이 남아 있어 다시 탐색합니다."
                    )
                    continue
                
            self.log("이벤트 시작 버튼이 사라졌습니다. 레이스 진입으로 판단합니다.")
            time.sleep(0.2)

            self.hw_key_down("w")
            self.hw_key_down("up")
            
            # 初始化各类计时器
            race_start_time = time.time()
            last_like_chk = time.time()
            last_chk = 0
            finished = False
            timeout_triggered = False
            
            try:
                finish_detect_start_sec = int(self.config.get("finish_detect_start_sec", 0))
            except Exception:
                finish_detect_start_sec = 0
            
            try:
                finish_detect_max_sec = int(self.config.get("finish_detect_max_sec", 120))
            except Exception:
                finish_detect_max_sec = 120
            
            finish_detect_start_sec = max(0, finish_detect_start_sec)
            finish_detect_max_sec = max(30, finish_detect_max_sec)
            
            if finish_detect_start_sec >= finish_detect_max_sec:
                finish_detect_start_sec = max(0, finish_detect_max_sec - 10)
            
            while self.is_running:
                now = time.time()
                elapsed = now - race_start_time

                # 완주 감지 최대시간 초과 방지
                if elapsed > finish_detect_max_sec:
                    self.log(f"跑图超时(已超过{finish_detect_max_sec}秒)！触发强制重开赛事逻辑...")
                    timeout_triggered = True
                    break

                # 【原生逻辑】：每隔3秒识别一次 likeauthor.png
                if now - last_like_chk >= 3.0:
                    vram_result = self.check_vramne_during_race()
                    if vram_result is True:
                        self.log("VRAM恢复完成，结束当前跑图流程，交给外层重新恢复。")
                        return False
                    elif vram_result is False:
                        self.log("VRAM恢复失败。")
                        return False
                    
                    pos_like = self.find_any_image_gray(["likeauthor.png", "dislikeauthor.png"], region=self.regions["中间"], threshold=0.70)
                    if pos_like:
                        self.log("识别到点赞作界面，执行回车确认！")
                        self.hw_press("enter")
                    last_like_chk = now
                    
                # 완주 감지 시작시간 이후부터 restart.png 감지
                if elapsed >= finish_detect_start_sec and now - last_chk >= 1.0:
                    found_restart = self.find_image_gray("restart.png", region=self.regions["下"], threshold=0.75, fast_mode=True)
                    if found_restart:
                        finished = True
                        break
                    last_chk = now
                
                time.sleep(0.3)
                
            # 无论正常结束还是超时，都必须先松开油门和方向
            self.hw_key_up("w")
            self.hw_key_up("up")

            if not self.is_running:
                return False

            # ====== 【新增】：执行超时重置操作 ======
            if timeout_triggered:
                time.sleep(0.5)
                self.hw_press("esc")
                time.sleep(1.5)  # 等待菜单动画加载
                
                # 寻找并点击 restarta.png
                pos_restarta = self.wait_for_image_gray("restarta.png", region=self.regions["全界面"], threshold=0.70, timeout=4.0, interval=0.3, fast_mode=True)
                if pos_restarta:
                    self.log("找到 restarta.png，点击重开赛事...")
                    self.game_click(pos_restarta)
                    time.sleep(1.0)
                    self.hw_press("enter")  # 地平线重开赛事通常有确认弹窗，按一次回车确认
                    time.sleep(4.0)         # 等待黑屏重加载动画
                else:
                    self.log("未找到 restarta.png，尝试直接继续...")
                    
                # 【关键】：直接跳过下方的结算流程，回到最外层 while 重新找 start.png（并且本次不计入 race_counter）
                continue
            # ========================================

            if not finished:
                return False

            if self.pause_requested or self.race_counter == target_count - 1:
                self.hw_press("enter")
                time.sleep(2.0)
            else:
                self.hw_press("x")
                time.sleep(0.8)
                self.hw_press("enter")
                time.sleep(2.0)

            self.race_counter += 1
            self.update_running_ui("循环跑图", self.race_counter, target_count)

            if self.check_safe_pause():
                return "PAUSED"

        return True

    # ==========================================
    # --- 模块：买车 ---
    # ==========================================
    def logic_buy_car(self, target_count):
        if self.car_counter >= target_count:
            return True

        self.update_running_ui("批量买车", self.car_counter, target_count)

        self.log("准备验证/进入菜单...")
        if not self.enter_menu():
            return False

        pos_collectionjournal = self.wait_for_image_transparent(
            "collectionjournal.png",
            region=self.regions["左"],
            threshold=0.7,
            timeout=30,
            interval=0.4,
            fast_mode=True
        )
        if not pos_collectionjournal:
            self.log("未找到收集簿")
            return False

        self.game_click(pos_collectionjournal, double=True)
        time.sleep(1.0)

        pos_masterexplorer = self.wait_for_image(
            "masterexplorer.png",
            region=self.regions["全界面"],
            threshold=0.75,
            timeout=30,
            interval=0.4,
            fast_mode=True
        )
        if not pos_masterexplorer:
            self.log("未找到探索")
            return False

        self.game_click(pos_masterexplorer, double=True)
        time.sleep(0.6)

        pos_carcollection = self.wait_for_image_transparent(
            "carcollection.png",
            region=self.regions["全界面"],
            threshold=0.75,
            timeout=30,
            interval=0.3,
            fast_mode=True
        )
        if not pos_carcollection:
            self.log("未找到车辆收集")
            return False

        self.game_click(pos_carcollection, double=True)
        time.sleep(1.0)

        self.hw_press("backspace")
        time.sleep(0.5)

        brand_threshold, brand_fast_mode, brand_up_wait = self.get_brand_search_settings()

        brand_pos = None
        for _ in range(5):
            if not self.is_running:
                return False
                

            brand_pos = self.wait_for_any_image_gray(
                ["CCbrand.png", "CCbrand-b.png"],
                region=self.regions["全界面"],
                threshold=brand_threshold,
                timeout=0.8,
                interval=0.2,
                fast_mode=brand_fast_mode
            )

            if brand_pos:
                break

            self.hw_press("up")
            time.sleep(brand_up_wait)

        if not brand_pos:
            self.log("未找到品牌")
            return False

        self.game_click(brand_pos)
        time.sleep(0.8)
        self.hw_press("down")
        time.sleep(0.4)

        pos_22b = self.wait_for_image(
            "consumablecar.png",
            region=self.regions["全界面"],
            threshold=0.90,
            timeout=8,
            interval=0.3,
            fast_mode=False
        )
        if not pos_22b:
            self.log("未找到消耗品车辆")
            return False

        self.game_click(pos_22b, double=True)
        time.sleep(1.0)

        while self.car_counter < target_count:
            if not self.is_running:
                return False
            
            self.hw_press("space")
            time.sleep(0.6)
            self.move_to_game_coord(5, 5)
            self.hw_press("down")
            time.sleep(0.2)
            self.move_to_game_coord(5, 5)
            self.hw_press("enter")
            time.sleep(0.6)
            self.move_to_game_coord(5, 5)
            self.hw_press("enter")
            time.sleep(0.6)
            self.move_to_game_coord(5, 5)
            self.hw_press("enter")
            time.sleep(0.7)

            self.car_counter += 1
            self.update_running_ui("批量买车", self.car_counter, target_count)
            if self.check_safe_pause():
                return "PAUSED"

        for _ in range(5):
            if not self.is_running:
                return False
            self.hw_press("esc")
            time.sleep(0.8)

        return True
    # ==========================================
    # --- 模块：抽奖 ---
    # ==========================================
    def logic_super_wheelspin(self, target_count):
        if self.cj_counter >= target_count:
            return True

        self.update_running_ui("超级抽奖", self.cj_counter, target_count)
        self.log("准备验证/进入菜单...")
        if not self.enter_menu():
            return False

        self.log("进入车辆与收藏...")
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        pos_buycar = self.wait_for_image(
            "BNandUC.png",
            region=self.regions["左"],
            threshold=0.70,
            timeout=15,
            interval=0.3,
            fast_mode=True
        )
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车")
            return False

        self.game_click(pos_buycar)
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(5)


        pos_bs = self.wait_for_any_image_gray(
            ["buyandsell-w.png", "buyandsell-b.png"],
            region=self.regions["左"],
            threshold=0.75,
            timeout=60,
            interval=0.5,
            fast_mode=True
        )
        if not pos_bs:
            self.log("未找到购买与出售")
            return False

        self.game_click(pos_bs)
        time.sleep(1.0)
        self.hw_press("pagedown", delay=0.15)
        self.log("进入车辆界面...")
        time.sleep(0.5)

        while self.cj_counter < target_count:
            if not self.is_running:
                return False
            self.log("进入我的车辆.")
            self.hw_press("enter")
            time.sleep(2.0)
            self.hw_press("backspace")
            time.sleep(1.0)

            brand_threshold, brand_fast_mode, brand_up_wait = self.get_brand_search_settings()

            brand_pos = None
            for _ in range(30):
                if not self.is_running:
                    return False

                brand_pos = self.wait_for_any_image_gray(
                    ["CCbrand.png", "CCbrand-b.png"],
                    region=self.regions["全界面"],
                    threshold=brand_threshold,
                    timeout=0.8,
                    interval=0.2,
                    fast_mode=brand_fast_mode
                )

                if brand_pos:
                    break

                self.hw_press("up")
                time.sleep(brand_up_wait)

            if not brand_pos:
                self.log("选品牌失败")
                return False

            self.game_click(brand_pos)
            time.sleep(1.0)

            start_right_count = 0

            try:
                start_right_count = int(
                    self.user_image_config.get(
                        "new_car_start_right_count",
                        0
                    )
                )
            except Exception:
                start_right_count = 0

            start_right_count = max(0, min(start_right_count, 80))

            # 현재 휠스핀 진행 수 기준 자동 보정
            # 12대마다 한 화면이 소비되었다고 보되,
            # 이미지 오탐/실패 여유를 위해 +3대 이후부터 다음 페이지 보정을 적용합니다.
            page_buffer = int(
                self.user_image_config.get(
                    "new_car_page_buffer",
                    3
                )
            )

            auto_right_count = max(
                0,
                ((self.cj_counter - page_buffer) // 12) * 4
            )
            
            total_right_count = start_right_count + auto_right_count
            
            if total_right_count > 0:
                self.log(
                    f"신규 차량 시작 위치 보정: "
                    f"user {start_right_count}칸 + "
                    f"진행도 {auto_right_count}칸 "
                    f"(현재 차량 {self.cj_counter}대, "
                    f"여유값 {page_buffer}) "
                    f"= 총 {total_right_count}칸 이동"
                )
            
                for _ in range(total_right_count):
                    if not self.is_running:
                        return False
            
                    self.hw_press("right", delay=0.08)
                    time.sleep(0.12)
            
                time.sleep(0.5)
            
            pos_target = None
            found_car = False

            current_page = 0

            # 항상 첫 페이지부터 순서대로 탐색
            max_search_count = max(
                1,
                int(
                    self.user_image_config.get(
                        "new_car_max_search_count",
                        85
                    )
                )
            )

            for _ in range(max_search_count):
                if not self.is_running:
                    return False
                pos_target = self.wait_for_image_with_element_multi(
                    "newCC.png",
                    "newcartag.png",
                    region=self.regions["全界面"],
                    main_threshold=0.75,   # 防HDR核心：第一道门槛放低
                    like_threshold=0.75,
                    final_threshold=0.70,
                    timeout=1.5,
                    interval=0.2,
                    fast_mode=True
                )
                
                if pos_target:
                    self.game_click(pos_target)
                    found_car = True
                    self.log(f"대상 차량을 고정했습니다. 현재 페이지: {current_page}")
                    break
                    
                # 翻下一页
                for _ in range(4):
                    self.hw_press("right", delay=0.06)
                    time.sleep(0.1)
                time.sleep(0.4)
                current_page += 1
            if not found_car:
                self.log("목록에서 신규 차량을 찾지 못했습니다. 작업을 중단합니다.")
                return False
            time.sleep(1.2)

            self.log("'차량 탑승' 버튼을 확인합니다.")

            pos_rc = self.wait_for_any_image_gray(
                ["rc.png", "rc-b.png"],
                region=self.regions["全界面"],
                threshold=0.70,
                timeout=0.5,
                interval=0.1,
                fast_mode=True
            )

            # 첫 번째 차량처럼 이미 선택된 상태라면 rc.png가 바로 보일 수 있음
            # 그 외 차량은 Enter를 한 번 눌러야 차량 선택 메뉴가 열림
            if not pos_rc:
                self.log("탑승 버튼이 아직 없어 Enter로 차량 선택 메뉴를 엽니다.")
                self.hw_press("enter")
                time.sleep(1.2)

                pos_rc = self.wait_for_any_image_gray(
                    ["rc.png", "rc-b.png"],
                    region=self.regions["全界面"],
                    threshold=0.70,
                    timeout=2.0,
                    interval=0.1,
                    fast_mode=True
                )

            if pos_rc:
                self.log("탑승 버튼을 찾았습니다. 자동차 보기 방지를 위해 선택 위치를 보정합니다.")
                time.sleep(0.3)

                self.hw_press("up", delay=0.08)
                time.sleep(0.1)
                self.hw_press("up", delay=0.08)
                time.sleep(0.1)

                self.log("차량 탑승 버튼을 클릭합니다.")
                self.game_click(pos_rc)
                time.sleep(6.0)

            else:
                self.log("Enter 후에도 탑승 버튼을 찾지 못했습니다. 선택 실패로 보고 다시 신규 차량을 탐색합니다.")
                self.hw_press("esc")
                time.sleep(1.0)
                continue
            
            # 차량 탑승 컷씬/로딩 대기
            car_enter_wait = float(
                self.user_image_config.get(
                    "car_enter_wait",
                    6
                )
            )

            # 차량 탑승 컷씬/로딩 대기
            time.sleep(car_enter_wait)
            
            pos_sjy = None
            self.log("차량 탑승 후 메뉴 진입을 시도합니다.")
            
            for esc_try in range(3):
                if not self.is_running:
                    return False
            
                self.log(f"차량 탑승 후 ESC 메뉴 열기 시도 {esc_try + 1}/3")
                self.hw_press("esc", delay=0.2)
                time.sleep(2.0)
            
                upgrade_threshold, upgrade_fast_mode = self.get_upgrade_search_settings()

                pos_sjy = self.wait_for_any_image_gray(
                    ["UandT-w.png", "UandT-b.png"],
                    region=self.regions["左下"],
                    threshold=upgrade_threshold,
                    timeout=6,
                    interval=0.5,
                    fast_mode=upgrade_fast_mode
                )
            
                if pos_sjy:
                    break

            if not pos_sjy:
                self.log("找不到升级页面")
                return False

            pos_cls = self.click_and_wait_next(
                pos_sjy,
                ["clsldcnw.png", "clsldcnb.png"],
                next_region=self.regions["左下"],
                next_threshold=0.70,
                timeout=10,
                retry_times=2,
                double=False,
                gray=True
            )
            
            if not pos_cls:
                self.log("未找到车辆熟练度")
                return False
            self.game_click(pos_cls)
            time.sleep(0.8)
            
            pos_exp = self.wait_for_any_image(
                ["EXPwU.png"],
                region=self.regions["左"],
                threshold=0.75,
                timeout=0.9,
                interval=0.25,
                fast_mode=True
            )
            
            if pos_exp:
                self.log("该车辆技能已点过，跳过计数")
            else:
                time.sleep(0.5)
                self.hw_press("enter", delay=0.25)
                time.sleep(1.2)

                for dk in self.config["skill_dirs"]:
                    if not self.is_running:
                        return False

                    self.hw_press(dk, delay=0.25)
                    time.sleep(0.45)

                    self.hw_press("enter", delay=0.25)
                    time.sleep(1.35)

                spne_found = self.find_image_gray("SPNE.png", region=self.regions["全界面"], threshold=0.70)
                
                if spne_found:
                    self.log("已无技能点或技能已点完，提前结束抽奖！")
                    time.sleep(1.0)
                    self.hw_press("enter")
                    time.sleep(0.8)
                    self.hw_press("esc")
                    time.sleep(1.0)
                    self.hw_press("esc")
                    time.sleep(1.0)
                    self.hw_press("esc")
                    time.sleep(1.0)
                    return True
                self.cj_counter += 1
                self.update_running_ui("超级抽奖", self.cj_counter, target_count)
                if self.check_safe_pause():
                    return "PAUSED"

            self.hw_press("esc")
            time.sleep(1.2)
            self.hw_press("esc")
            time.sleep(0.8)
            self.hw_press("up", delay=0.15)
            time.sleep(0.8)
        self.hw_press("esc")
        time.sleep(1.2)
        self.hw_press("esc")
        time.sleep(1.2)
        return True
    # ==========================================
    # --- 模块：移除车辆 ---
    # ==========================================
    def sell_consumable_car(self, target_count):
        if self.sc_count >= target_count:
            return True

        self.update_running_ui("移除车辆", self.sc_count, target_count)

        self.log("准备验证/进入菜单！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        if not self.enter_menu():
            return False

        self.log("进入车辆与收藏！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        pos_buycar = self.wait_for_image("BNandUC.png", region=self.regions["左"], threshold=0.70, timeout=12, interval=0.3, fast_mode=True)
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车")
            return False

        self.game_click(pos_buycar)
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(5)

        pos_bs = self.wait_for_any_image(["buyandsell-w.png", "buyandsell-b.png"], region=self.regions["上"], threshold=0.75, timeout=40, interval=0.5, fast_mode=True)
        if not pos_bs:
            self.log("未找到购买与出售")
            return False

        self.game_click(pos_bs)
        time.sleep(1.0)

        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        self.hw_press("enter")  # 进入我的车辆
        time.sleep(2.0)
        #选择一辆收藏
        self.hw_press("y") 
        time.sleep(1.0)
        self.hw_press("enter")
        time.sleep(0.8)
        self.hw_press("esc") 
        time.sleep(1.5)
        #驾驶收藏的车
        self.hw_press("enter")
        time.sleep(0.8)
        self.move_to_game_coord(5, 5)
        time.sleep(0.2)

        pos = self.wait_for_any_image(["rc.png", "rc-b.png"], region=self.regions["全界面"], threshold=0.65, timeout=5, interval=0.2, fast_mode=True)
        if pos:
            self.log("找到上车，执行点击")
            self.game_click(pos) # 【重要修复】：之前写的是 self.safe_click 导致直接报错崩溃，现已修正
            time.sleep(2.0)
        else:
            self.log("该车辆已经驾驶，或未找到图片，执行两次ESC")
            self.hw_press("esc")
            time.sleep(1.5)
            self.hw_press("esc")
        time.sleep(2.0)

        found = False
        for i in range(60):
            if not self.is_running:
                return False

            pos = self.wait_for_any_image(["buyandsell-b.png", "buyandsell-w.png"], region=self.regions["上"], threshold=0.70, timeout=0.8, interval=0.2, fast_mode=True)
            if pos:
                self.log(f"第 {i + 1} 次检测到购买与出售，进入车辆界面")
                self.hw_press("enter")
                found = True
                break
            self.log(f"第 {i + 1} 次未检测到购买与出售，等待后重试")
            time.sleep(1.0)
        if not found:
            self.log("60次内未找到购买与出售")
            return False
        
        time.sleep(1.5)
        # 切换排序：最近获得
        self.hw_press("x")
        time.sleep(0.5)
        #鼠标复位
        self.move_to_game_coord(5, 5)
        #选择最近获得
        self.log("切换到 最近获得 的排序...")
        for _ in range(6):
            if not self.is_running:
                return False
            self.hw_press("down")
            time.sleep(0.25)
        time.sleep(0.2)
        self.hw_press("enter")
        time.sleep(1.2)
        self.log("回到最近获得的前面")
        # 回到列表首项
        self.hw_press("backspace")
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(1.5)

        self.log("开始删除最近获得的车辆！！！请人工确认是否移除")

        while self.sc_count < target_count:
            self.log(f"is_running = {self.is_running}")
            if not self.is_running:
                return False
            # 进入当前车辆
            self.hw_press("enter")
            time.sleep(1.2)
            #跳到从车库移除
            for _ in range(6):
                if not self.is_running:
                    return False
                self.hw_press("down")
                time.sleep(0.2)
            self.hw_press("enter")
            time.sleep(0.5)
            #向下选择“嗯”
            self.hw_press("down")
            time.sleep(0.3)
            #确认“嗯”
            self.hw_press("enter")
            time.sleep(0.8)
            self.sc_count += 1
            self.log(f"已尝试删除车辆 {self.sc_count}/{target_count}")
            self.update_running_ui("移除车辆", self.sc_count, target_count)

            if self.check_safe_pause():
                return "PAUSED"
            

        for _ in range(3):
            if not self.is_running:
                return False
            self.hw_press("esc")
            time.sleep(1.0)

        return True
    
    def find_and_remove_consumable_car(self, target_count):
        if self.sc_count >= target_count:
            return True
        
        self.update_running_ui("移除车辆", self.sc_count, target_count)

        self.log("准备验证/进入菜单！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        if not self.enter_menu():
            return False

        self.log("进入车辆与收藏！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        pos_buycar = self.wait_for_image("BNandUC.png", region=self.regions["左"], threshold=0.70, timeout=12, interval=0.3, fast_mode=True)
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车")
            return False

        self.game_click(pos_buycar)
        time.sleep(0.8)
        self.hw_press("enter")
        time.sleep(5)

        pos_bs = self.wait_for_any_image(["buyandsell-w.png", "buyandsell-b.png"], region=self.regions["上"], threshold=0.75, timeout=40, interval=0.5, fast_mode=True)
        if not pos_bs:
            self.log("未找到购买与出售")
            return False

        self.game_click(pos_bs)
        time.sleep(1.0)

        self.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        self.hw_press("enter")  # 进入我的车辆
        time.sleep(2.0)
        #选择一辆收藏
        self.hw_press("y") 
        time.sleep(1.0)
        self.hw_press("enter")
        time.sleep(0.8)
        self.hw_press("esc") 
        time.sleep(1.5)
        #驾驶收藏的车
        self.hw_press("enter")
        time.sleep(0.8)
        self.move_to_game_coord(5, 5)
        time.sleep(0.2)

        pos = self.wait_for_any_image(["rc.png", "rc-b.png"], region=self.regions["全界面"], threshold=0.65, timeout=5, interval=0.2, fast_mode=True)
        if pos:
            self.log("找到上车，执行点击")
            self.game_click(pos) # 【重要修复】：之前写的是 self.safe_click 导致直接报错崩溃，现已修正
            time.sleep(2.0)
        else:
            self.log("该车辆已经驾驶，或未找到图片，执行两次ESC")
            self.hw_press("esc")
            time.sleep(1.5)
            self.hw_press("esc")
        time.sleep(2.0)

        found = False
        for i in range(30):
            if not self.is_running:
                return False

            pos = self.wait_for_any_image(["buyandsell-b.png", "buyandsell-w.png"], region=self.regions["上"], threshold=0.70, timeout=0.8, interval=0.2, fast_mode=True)
            if pos:
                self.log(f"第 {i + 1} 次检测到购买与出售，进入车辆界面")
                self.hw_press("enter")  #进入我的车辆
                time.sleep(1.5)
                found = True
                break
            self.log(f"第 {i + 1} 次未检测到购买与出售，等待后重试")
            time.sleep(1.0)
        if not found:
            self.log("30次内未找到购买与出售")
            return False
        #筛选
        self.hw_press("y")
        time.sleep(1.0)
        
        pos_repitem = self.wait_for_image_gray(
            "repitem.png",
            region=self.regions["中间"],
            threshold=0.70,
            timeout=1,
            interval=0.3,
            fast_mode=True
        )
        
        if not pos_repitem:
            self.log("未识别到 repitem.png")
            return False
        
        self.game_click(pos_repitem)
        time.sleep(0.8)
        
        self.hw_press("esc")
        time.sleep(1.0)


        #切换到消耗品品牌
        self.log("切换到消耗品品牌...")
        self.hw_press("backspace")
        brand_pos = None
        for _ in range(5):
            if not self.is_running:
                return False
                

            brand_pos = self.wait_for_any_image_gray(
                ["CCbrand.png"],
                region=self.regions["全界面"],
                threshold=0.75,
                timeout=0.8,
                interval=0.2,
                fast_mode=True
            )

            if not brand_pos:
                brand_pos = self.wait_for_any_image_gray(
                    ["CCbrand-b.png"],
                    region=self.regions["全界面"],
                    threshold=0.75,
                    timeout=0.8,
                    interval=0.2,
                    fast_mode=True
                )

            if brand_pos:
                break

            self.hw_press("up")
            time.sleep(0.25)

        if not brand_pos:
            self.log("未找到品牌")
            return False

        self.game_click(brand_pos)
        time.sleep(0.8)
        
        self.log("开始删除最近获得的车辆！！！请人工确认是否移除")
        
        not_found_pages = 0  
        while self.sc_count < target_count:
            if not self.is_running:
                return False
            self.log(f"正在使用 3模式 严格扫描当前页面... (连续未找到: {not_found_pages}/5)")
            
            # 【使用终极安全锁】：2张图，4道防线，绝不乱删
            pos_target = self.wait_for_image_ultimate_safe(
                main_path="removecarobject.png",  # 你要删的车的截图
                anti_path="newcartag.png",        # NEW标签截图
                region=self.regions["全界面"],
                main_threshold=0.77,              # 极高的基础相似度要求
                anti_threshold=0.65,              # 极度敏感的 NEW 标签排斥
                timeout=3.0,
                interval=0.2
            )
            
            if not pos_target:
                not_found_pages += 1
                if not_found_pages >= 2:
                    self.log("=连续翻找 2 页仍未搜索到目标车辆！视为车辆已全部清理完毕。")
                    self.log("主动结束清理任务，准备进入下一步骤...")
                    break  # 直接跳出循环，结束当前任务
                    
                self.log(f"当前页面未找到，向右翻页寻找... (第 {not_found_pages} 次翻页)")
                for _ in range(4):
                    self.hw_press("right", delay=0.06)
                    time.sleep(0.1)
                time.sleep(0.4)
                continue
            # ====== 找到了目标车辆，重置翻页计数器 ======
            not_found_pages = 0
            
            self.log("精准锁定目标车辆，执行点击...")
            self.game_click(pos_target)
            time.sleep(1.2) # 等待点击后的反应
            
            # ==========================================
            # 核心逻辑：寻找 removecar.png (从车库移除)
            # ==========================================
            self.log("寻找 '从车库移除' 按钮...")
            pos_remove = self.find_image_gray("removecar.png", region=self.regions["全界面"], threshold=0.75, fast_mode=True)
            
            if pos_remove:
                self.log("直接找到移除按钮，点击...")
                self.game_click(pos_remove)
            else:
                self.log("未直接找到移除按钮，按下 Enter 呼出菜单...")
                self.hw_press("enter")
                time.sleep(0.8) # 等待菜单弹出动画
                
                # 再次寻找
                pos_remove = self.find_image_gray("removecar.png", region=self.regions["全界面"], threshold=0.75, fast_mode=True)
                if pos_remove:
                    self.log("呼出菜单后找到移除按钮，点击...")
                    self.game_click(pos_remove)
                else:
                    self.log("仍未找到移除按钮，可能点错了/该车无法移除，按 ESC 放弃该车...")
                    self.hw_press("esc")
                    time.sleep(1.0)
                    self.hw_press("right") # 往右挪一格，防止死循环一直点这辆假车
                    time.sleep(1.2)
                    continue
                    
            time.sleep(0.8) # 等待“你确定要移除吗”的确认弹窗
            
            # 确认移除操作 (按向下选"嗯"，然后回车)
            self.log("确认移除...")
            self.hw_press("down")
            time.sleep(0.3)
            self.hw_press("enter")
            time.sleep(1.2)

            
            self.sc_count += 1
            self.update_running_ui("移除车辆", self.sc_count, target_count)
            self.log(f"成功移除车辆！当前进度: {self.sc_count}/{target_count}")
            if self.check_safe_pause():
                return "PAUSED"

        # 循环结束，退回上一级
        for _ in range(3):
            if not self.is_running:
                return False
            self.hw_press("esc")
            time.sleep(1.0)

        return True

    #===============================
    #---自动超级抽奖-----
    #===============================
if __name__ == "__main__":

    app = FH_UltimateBot()
    app.mainloop()
