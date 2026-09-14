# -*- coding: utf-8 -*-
"""八条猫桌宠 - PyQt5 透明置顶外壳"""

import sys
import os

# --- 打包修复：WebEngine / ICU / 插件路径（必须在任何 PyQt5 导入之前）---
if getattr(sys, 'frozen', False):
    # PyInstaller 6 + contents_directory='.' 时，核心文件与 exe 同级；
    # 若仍存在 _internal，则一并加入搜索路径。
    base_dir = os.path.dirname(sys.executable)
    internal_dir = os.path.join(base_dir, '_internal')

    path_parts = [base_dir]
    if os.path.isdir(internal_dir):
        path_parts.append(internal_dir)
        path_parts.append(os.path.join(internal_dir, 'PyQt5', 'Qt5', 'bin'))
    path_parts.append(os.path.join(base_dir, 'PyQt5', 'Qt5', 'bin'))
    os.environ['PATH'] = ';'.join(path_parts) + ';' + os.environ.get('PATH', '')

    # 插件目录：优先 exe 同级 / 再 _internal
    for plugin_candidate in [
        os.path.join(base_dir, 'PyQt5', 'Qt5', 'plugins'),
        os.path.join(internal_dir, 'PyQt5', 'Qt5', 'plugins'),
    ]:
        if os.path.isdir(plugin_candidate):
            os.environ['QT_PLUGIN_PATH'] = plugin_candidate
            break

    # Chromium 要求：优先 exe 同级的 resources / icudtl.dat
    for res_candidate in [
        os.path.join(base_dir, 'resources'),
        os.path.join(internal_dir, 'resources'),
        os.path.join(base_dir, 'PyQt5', 'Qt5', 'resources'),
        os.path.join(internal_dir, 'PyQt5', 'Qt5', 'resources'),
    ]:
        if os.path.isdir(res_candidate):
            os.environ['QTWEBENGINE_RESOURCES_PATH'] = res_candidate
            break

    for locale_candidate in [
        os.path.join(base_dir, 'translations', 'qtwebengine_locales'),
        os.path.join(base_dir, 'translations'),
        os.path.join(internal_dir, 'translations', 'qtwebengine_locales'),
        os.path.join(internal_dir, 'translations'),
        os.path.join(base_dir, 'PyQt5', 'Qt5', 'translations', 'qtwebengine_locales'),
        os.path.join(internal_dir, 'PyQt5', 'Qt5', 'translations', 'qtwebengine_locales'),
    ]:
        if os.path.isdir(locale_candidate):
            os.environ['QTWEBENGINE_LOCALES_PATH'] = locale_candidate
            break

    for proc_candidate in [
        os.path.join(base_dir, 'QtWebEngineProcess.exe'),
        os.path.join(internal_dir, 'QtWebEngineProcess.exe'),
        os.path.join(base_dir, 'PyQt5', 'Qt5', 'bin', 'QtWebEngineProcess.exe'),
        os.path.join(internal_dir, 'PyQt5', 'Qt5', 'bin', 'QtWebEngineProcess.exe'),
    ]:
        if os.path.isfile(proc_candidate):
            os.environ['QTWEBENGINEPROCESS_PATH'] = proc_candidate
            break
# ---------------------------------------------

# 必须在导入任何 PyQt / WebEngine 之前设置。
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
# 恢复轻量且丝滑的默认硬件加速配置（勿强制软件光栅化 / CPU 绘制）
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--enable-gpu-rasterization --enable-zero-copy"
)

import math
import json
import time
import random
import ctypes
import socket
import threading
from ctypes import wintypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from PyQt5.QtCore import (
    Qt,
    QCoreApplication,
    QPoint,
    QUrl,
    QObject,
    pyqtSlot,
    pyqtSignal,
    QTimer,
    QRectF,
)

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

from PyQt5.QtGui import QColor, QKeySequence, QCursor, QIcon, QPainter, QPen, QBrush, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QShortcut,
    QVBoxLayout,
    QWidget,
    QSystemTrayIcon,
    QMenu,
    QAction,
)
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import (
    QWebEngineView,
    QWebEngineSettings,
    QWebEngineProfile,
    QWebEnginePage,
)


def apply_webengine_settings(settings):
    """本地资源访问、JS，以及 2D 画布硬件加速（经典 / 动图桌宠）。"""
    if not settings:
        return
    for attr in (
        "LocalContentCanAccessRemoteUrls",
        "LocalContentCanAccessFileUrls",
        "JavascriptEnabled",
        "Accelerated2dCanvasEnabled",
    ):
        flag = getattr(QWebEngineSettings, attr, None)
        if flag is not None:
            try:
                settings.setAttribute(flag, True)
            except Exception:
                pass


def enable_global_webengine_settings():
    """在创建任意 QWebEngineView 之前调用。"""
    try:
        apply_webengine_settings(QWebEngineSettings.globalSettings())
    except Exception:
        pass


# ---------- 本地静态资源 HTTP（规避 file:// 下 XHR/fetch Status 0）----------
_LOCAL_HTTP = None  # type: ThreadingHTTPServer | None
_LOCAL_HTTP_PORT = None  # type: int | None
_LOCAL_HTTP_ROOT = None  # type: str | None
_PREFERRED_HTTP_PORT = 18923


def _pick_free_port(host="127.0.0.1", preferred=_PREFERRED_HTTP_PORT):
    candidates = [preferred] + list(range(preferred + 1, preferred + 48))
    for port in candidates:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def start_local_http_server(root_dir, preferred_port=_PREFERRED_HTTP_PORT):
    """在后台线程托管项目根目录，返回 http://127.0.0.1:<port>/"""
    global _LOCAL_HTTP, _LOCAL_HTTP_PORT, _LOCAL_HTTP_ROOT
    if _LOCAL_HTTP is not None and _LOCAL_HTTP_PORT:
        return "http://127.0.0.1:{}/".format(_LOCAL_HTTP_PORT)

    root_dir = os.path.abspath(root_dir)
    port = _pick_free_port(preferred=preferred_port)
    root_fs = root_dir.replace("\\", "/")

    class AssetHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root_dir, **kwargs)

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            path_only = self.path.split("?", 1)[0]
            if path_only in ("/__pet_runtime.js", "__pet_runtime.js"):
                body = (
                    "window.__PET_FS_ROOT__={root};\n"
                    "window.__PET_HTTP_BASE__={base};\n"
                ).format(
                    root=json.dumps(root_fs),
                    base=json.dumps("http://127.0.0.1:{}/".format(port)),
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return SimpleHTTPRequestHandler.do_GET(self)

        def end_headers(self):
            # 允许同机 WebEngine 读取 moc3/json/贴图
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            SimpleHTTPRequestHandler.end_headers(self)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), AssetHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="pet-http", daemon=True)
    thread.start()
    _LOCAL_HTTP = httpd
    _LOCAL_HTTP_PORT = port
    _LOCAL_HTTP_ROOT = root_dir
    print("[pet-http] serving {} at http://127.0.0.1:{}/".format(root_dir, port))
    return "http://127.0.0.1:{}/".format(port)


def stop_local_http_server():
    global _LOCAL_HTTP, _LOCAL_HTTP_PORT, _LOCAL_HTTP_ROOT
    httpd = _LOCAL_HTTP
    _LOCAL_HTTP = None
    _LOCAL_HTTP_PORT = None
    _LOCAL_HTTP_ROOT = None
    if not httpd:
        return
    try:
        httpd.shutdown()
    except Exception:
        pass
    try:
        httpd.server_close()
    except Exception:
        pass


def local_page_url(query=None):
    """返回托管后的 index.html 地址（可选 query，如 view=settings）。"""
    base = start_local_http_server(app_base_dir())
    url = base + "index.html"
    if query:
        url += "?" + str(query).lstrip("?")
    return url

# ---------- Win32 ----------
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

AUTOSTART_NAME = "HachikyuCatDesktopPet"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# 常见工作 / 游戏 / 音乐 / 社媒软件：按进程名识别（小写）
# 值：(显示名, 场景句, 台词标签 tag)
KNOWN_WORK_APPS = {
    # —— 办公 / 开发 ——
    "winword.exe": ("Word", "用户刚打开了 Word 文档开始写材料", "work"),
    "excel.exe": ("Excel", "用户刚打开了 Excel，在整理表格或数据", "work"),
    "powerpnt.exe": ("PowerPoint", "用户刚打开了 PowerPoint，在做演示文稿", "work"),
    "code.exe": ("VS Code", "用户刚打开了 VS Code，开始写代码或改项目", "work"),
    "devenv.exe": ("Visual Studio", "用户刚打开了 Visual Studio，在写代码", "work"),
    "idea64.exe": ("IntelliJ IDEA", "用户刚打开了 IDEA，进入开发状态", "work"),
    "pycharm64.exe": ("PyCharm", "用户刚打开了 PyCharm，在写 Python", "work"),
    "webstorm64.exe": ("WebStorm", "用户刚打开了 WebStorm，在写前端", "work"),
    "cursor.exe": ("Cursor", "用户刚打开了 Cursor，在用 AI 写代码或改桌宠", "cursor"),
    "photoshop.exe": ("Photoshop", "用户刚打开了 Photoshop，开始修图或设计", "work"),
    "illustrator.exe": ("Illustrator", "用户刚打开了 Illustrator，在做矢量设计", "work"),
    "afterfx.exe": ("After Effects", "用户刚打开了 After Effects，在做动效", "work"),
    "premiere pro.exe": ("Premiere", "用户刚打开了 Premiere，在剪辑视频", "work"),
    "adobe premiere pro.exe": ("Premiere", "用户刚打开了 Premiere，在剪辑视频", "work"),
    "notepad++.exe": ("Notepad++", "用户刚打开了 Notepad++，在记点什么", "work"),
    "sublime_text.exe": ("Sublime Text", "用户刚打开了 Sublime Text，在编辑文本", "work"),
    "notion.exe": ("Notion", "用户刚打开了 Notion，在整理笔记", "work"),
    "obsidian.exe": ("Obsidian", "用户刚打开了 Obsidian，在写笔记", "work"),
    "figma.exe": ("Figma", "用户刚打开了 Figma，在做设计", "work"),
    "wps.exe": ("WPS", "用户刚打开了 WPS，开始办公文档", "work"),
    "wpp.exe": ("WPS 演示", "用户刚打开了 WPS 演示，在做幻灯片", "work"),
    "et.exe": ("WPS 表格", "用户刚打开了 WPS 表格，在处理数据", "work"),
    # —— 游戏 ——
    "stardewvalley.exe": ("星露谷物语", "用户刚打开了星露谷物语，准备下地或下矿", "stardew_valley"),
    "yuanshen.exe": ("原神", "用户刚打开了原神，准备去提瓦特晃悠", "game"),
    "genshinimpact.exe": ("原神", "用户刚打开了原神，准备去提瓦特晃悠", "game"),
    "starrail.exe": ("崩坏：星穹铁道", "用户刚打开了星穹铁道，准备跃迁或清体力", "game"),
    "minecraft.exe": ("Minecraft", "用户刚打开了 Minecraft，准备挖矿或盖房子", "game"),
    "javaw.exe": ("Minecraft", "用户可能在玩 Minecraft（Java）", "game"),
    "steam.exe": ("Steam", "用户刚打开了 Steam，准备挑游戏或更新库", "game"),
    "steamwebhelper.exe": ("Steam", "用户刚打开了 Steam，在商店或库页面晃悠", "game"),
    "leagueclient.exe": ("英雄联盟", "用户刚打开了英雄联盟客户端，准备开黑", "game"),
    "league of legends.exe": ("英雄联盟", "用户进入了英雄联盟对局", "game"),
    # —— 音乐 ——
    "cloudmusic.exe": ("网易云音乐", "用户刚打开了网易云音乐，准备戴上耳机听歌", "music"),
    "qqmusic.exe": ("QQ音乐", "用户刚打开了 QQ 音乐，准备听歌摸鱼", "music"),
    "spotify.exe": ("Spotify", "用户刚打开了 Spotify，准备沉浸听歌", "music"),
    # —— 社媒 / 通讯 ——
    "discord.exe": ("Discord", "用户刚打开了 Discord，准备跟朋友聊天或开黑", "discord"),
    "wechat.exe": ("微信", "用户刚打开了微信，准备回消息或刷朋友圈", "social"),
    "weixin.exe": ("微信", "用户刚打开了微信，准备回消息或刷朋友圈", "social"),
    "qq.exe": ("QQ", "用户刚打开了 QQ，准备聊天或摸鱼", "social"),
}


def match_work_app(exe_name, title):
    """返回 (显示名, 场景句, tag) 或 None。不记录其它隐私信息。"""
    exe = (exe_name or "").lower()
    title_l = (title or "").lower()
    title_raw = title or ""
    if not exe and not title_l:
        return None

    # javaw 仅在标题像 Minecraft 时认作游戏，避免误伤其它 Java 程序
    if exe == "javaw.exe":
        if "minecraft" not in title_l and "我的世界" not in title_raw:
            return None
        return KNOWN_WORK_APPS["javaw.exe"]

    if exe in KNOWN_WORK_APPS:
        return KNOWN_WORK_APPS[exe]

    # 模糊：vscode 变体、adobe / league 路径名
    for key, val in KNOWN_WORK_APPS.items():
        if key == "javaw.exe":
            continue
        if key in exe:
            return val

    # 标题兜底（进程名异常时）
    title_map = [
        ("visual studio code", ("VS Code", "用户刚打开了 VS Code，开始写代码或改项目", "work")),
        ("microsoft word", ("Word", "用户刚打开了 Word 文档开始写材料", "work")),
        ("microsoft excel", ("Excel", "用户刚打开了 Excel，在整理表格或数据", "work")),
        ("microsoft powerpoint", ("PowerPoint", "用户刚打开了 PowerPoint，在做演示文稿", "work")),
        ("adobe photoshop", ("Photoshop", "用户刚打开了 Photoshop，开始修图或设计", "work")),
        ("stardew valley", ("星露谷物语", "用户刚打开了星露谷物语，准备下地或下矿", "stardew_valley")),
        ("网易云音乐", ("网易云音乐", "用户刚打开了网易云音乐，准备戴上耳机听歌", "music")),
        ("cursor", ("Cursor", "用户刚打开了 Cursor，在用 AI 写代码或改桌宠", "cursor")),
        ("discord", ("Discord", "用户刚打开了 Discord，准备跟朋友聊天或开黑", "discord")),
        ("genshin impact", ("原神", "用户刚打开了原神，准备去提瓦特晃悠", "game")),
        ("honkai: star rail", ("崩坏：星穹铁道", "用户刚打开了星穹铁道，准备跃迁或清体力", "game")),
        ("league of legends", ("英雄联盟", "用户打开了英雄联盟", "game")),
    ]
    for needle, val in title_map:
        if needle in title_l or needle in title_raw:
            return val
    return None


def get_foreground_process_name(hwnd):
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)
    return ""


def any_key_activity_pulse():
    """
    仅检测是否有按键活动（按下沿），不记录、不保存具体键值。
    使用 GetAsyncKeyState 的最低位“自上次调用以来是否按下”。
    """
    # 跳过鼠标键 1-6；扫描常用键区，不把 vk 写入任何变量之外的存储
    hit = False
    for vk in range(8, 256):
        state = user32.GetAsyncKeyState(vk)
        if state & 0x0001:
            hit = True
            # 故意不 break 前保存 vk；读完即丢弃
            break
    return hit


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def get_window_text(hwnd, max_len=256):
    buf = ctypes.create_unicode_buffer(max_len)
    user32.GetWindowTextW(hwnd, buf, max_len)
    return buf.value


def get_class_name(hwnd, max_len=256):
    buf = ctypes.create_unicode_buffer(max_len)
    user32.GetClassNameW(hwnd, buf, max_len)
    return buf.value


def rect_area(r):
    return max(0, r.right - r.left) * max(0, r.bottom - r.top)


def is_foreground_overlay_target(self_hwnd):
    """轻量检测：前台窗口是否全屏或最大化（排除自身与桌面外壳）。"""
    fg = user32.GetForegroundWindow()
    if not fg or fg == self_hwnd:
        return False

    # 跳过不可见 / 最小化
    if not user32.IsWindowVisible(fg):
        return False
    if user32.IsIconic(fg):
        return False

    cls = get_class_name(fg)
    if cls in (
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "DV2ControlHost",
        "Windows.UI.Core.CoreWindow",
    ):
        return False

    # 最大化
    if user32.IsZoomed(fg):
        return True

    win = RECT()
    if not user32.GetWindowRect(fg, ctypes.byref(win)):
        return False

    monitor = user32.MonitorFromWindow(fg, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return False
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False

    mon = info.rcMonitor
    w_area = float(rect_area(win))
    m_area = float(rect_area(mon)) or 1.0
    # 覆盖显示器约 92% 以上视为全屏/大窗
    if w_area / m_area >= 0.92:
        return True

    # 四边几乎贴齐显示器
    tol = 8
    if (
        abs(win.left - mon.left) <= tol
        and abs(win.top - mon.top) <= tol
        and abs(win.right - mon.right) <= tol
        and abs(win.bottom - mon.bottom) <= tol
    ):
        return True

    return False


def app_base_dir():
    """定位资源目录：兼容源码运行 / PyInstaller onedir / onefile。

    开发调试（非 frozen）一律使用 main.py 所在目录，即仓库根下的 index.html，
    绝不读取 dist/ 打包输出。
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        meipass = getattr(sys, "_MEIPASS", None)
        for candidate in (exe_dir, meipass):
            if candidate and os.path.isfile(os.path.join(candidate, "index.html")):
                return candidate
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


def resolve_html_path():
    """网页入口：始终为 {app_base_dir}/index.html（源码运行 = 仓库根目录）。"""
    return os.path.abspath(os.path.join(app_base_dir(), "index.html"))


def autostart_command():
    if getattr(sys, "frozen", False):
        return '"{}"'.format(os.path.abspath(sys.argv[0]))
    script = os.path.abspath(__file__)
    pythonw = sys.executable
    if pythonw.lower().endswith("python.exe"):
        candidate = pythonw[:-10] + "pythonw.exe"
        if os.path.isfile(candidate):
            pythonw = candidate
    return '"{}" "{}"'.format(pythonw, script)


def set_autostart(enabled):
    """写入/删除 HKCU\\...\\Run 自启动项。"""
    import winreg

    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)
    try:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def is_autostart_enabled():
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def set_click_through(hwnd, enabled):
    """WS_EX_TRANSPARENT：透明区域点击穿透到下层桌面。"""
    if not hwnd:
        return
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        new_style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
    else:
        new_style = (style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
    if new_style == style:
        return
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
    user32.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
    )


def get_cursor_pos():
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_cursor_pos(x, y):
    user32.SetCursorPos(int(x), int(y))


# ---- 挂机连点器（独立子线程 + 全局热键 F8/F9 + 多点巡检）----
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_F8 = 0x77
VK_F9 = 0x78

_CIRCLED_NUMS = (
    "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
    "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
)


def _mouse_left_down():
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def _mouse_left_up():
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _jitter_ms(base_ms, enabled, pct_lo=0.10, pct_hi=0.20):
    """在基础毫秒上叠加 ±10%~20% 微差，模拟人工节奏。"""
    base = max(1, int(base_ms or 1))
    if not enabled:
        return base
    pct = random.uniform(pct_lo, pct_hi)
    noise = (random.random() + random.random() - 1.0)
    delta = int(base * pct * noise)
    return max(1, base + delta)


def _normalize_clicker_points(raw):
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            x = int(round(float(item.get("x"))))
            y = int(round(float(item.get("y"))))
        except Exception:
            continue
        out.append({"x": x, "y": y})
        if len(out) >= 30:
            break
    return out


class ClickerPointMarker(QWidget):
    """屏幕坐标处的穿透序号浮标。"""

    def __init__(self, index, x, y, parent=None):
        super().__init__(parent)
        self._index = max(1, int(index))
        self._label = (
            _CIRCLED_NUMS[self._index - 1]
            if self._index <= len(_CIRCLED_NUMS)
            else str(self._index)
        )
        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        transparent_input = getattr(Qt, "WindowTransparentForInput", None)
        if transparent_input is not None:
            flags |= transparent_input
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(30, 30)
        self.move(int(x) - 15, int(y) - 15)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 金黄外圈 + 半透明芯
        painter.setBrush(QBrush(QColor(20, 24, 32, 170)))
        painter.setPen(QPen(QColor(255, 214, 64, 230), 2))
        painter.drawEllipse(2, 2, 26, 26)
        painter.setPen(QPen(QColor(0, 245, 212, 240)))
        font = QFont("Segoe UI", 10, QFont.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, self._label)
        painter.end()


class AutoClickerService(QObject):
    """独立线程执行连点；主线程轮询热键。支持多点按序巡检。"""

    statusChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._running = False
        self._done = 0
        self._last_cfg = {
            "mode": "tap",
            "count": 100,
            "infinite": False,
            "holdMs": 80,
            "intervalMs": 120,
            "jitter": True,
            "points": [],
        }
        self._f8_down = False
        self._f9_down = False

    def is_running(self):
        with self._lock:
            return bool(self._running)

    def _emit_status(self, payload):
        try:
            self.statusChanged.emit(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    def update_config(self, cfg):
        if not isinstance(cfg, dict):
            return
        merged = dict(self._last_cfg)
        merged.update(cfg)
        mode = "hold" if str(merged.get("mode") or "") == "hold" else "tap"
        self._last_cfg = {
            "mode": mode,
            "count": max(1, min(99999, int(merged.get("count") or 100))),
            "infinite": bool(merged.get("infinite")),
            "holdMs": max(50, min(1000, int(merged.get("holdMs") or 80))),
            "intervalMs": max(30, min(2000, int(merged.get("intervalMs") or 120))),
            "jitter": merged.get("jitter", True) is not False,
            "points": _normalize_clicker_points(merged.get("points")),
        }

    def start(self, cfg=None):
        if cfg is not None:
            self.update_config(cfg)
        self.stop(silent=True)
        self._stop.clear()
        with self._lock:
            self._running = True
            self._done = 0
        cfg_snapshot = dict(self._last_cfg)
        cfg_snapshot["points"] = list(self._last_cfg.get("points") or [])
        t = threading.Thread(
            target=self._loop,
            args=(cfg_snapshot,),
            name="auto-clicker",
            daemon=True,
        )
        self._thread = t
        t.start()
        pts = cfg_snapshot.get("points") or []
        self._emit_status(
            {
                "state": "running",
                "done": 0,
                "total": cfg_snapshot.get("count"),
                "infinite": bool(cfg_snapshot.get("infinite")),
                "pointTotal": len(pts),
                "pointIndex": 1 if pts else 0,
            }
        )

    def stop(self, silent=False):
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)
        with self._lock:
            was = self._running
            done = self._done
            self._running = False
        self._thread = None
        try:
            _mouse_left_up()
        except Exception:
            pass
        if not silent and was:
            self._emit_status({"state": "stopped", "done": done})

    def _sleep_ms(self, ms):
        end_at = time.time() + (max(0, int(ms)) / 1000.0)
        while time.time() < end_at:
            if self._stop.is_set():
                return False
            time.sleep(0.01)
        return True

    def _click_once(self, hold_ms, jitter):
        press_ms = _jitter_ms(hold_ms, jitter)
        _mouse_left_down()
        ok = self._sleep_ms(press_ms)
        try:
            _mouse_left_up()
        except Exception:
            pass
        return ok and (not self._stop.is_set())

    def _move_to_point(self, point, jitter):
        x = int(point.get("x") or 0)
        y = int(point.get("y") or 0)
        if jitter:
            x += random.randint(-3, 3)
            y += random.randint(-3, 3)
        try:
            set_cursor_pos(x, y)
        except Exception as exc:
            self._emit_status({"state": "error", "message": str(exc) or "move_failed"})
            return False
        # 落点后短暂停顿，避免过快点击
        return self._sleep_ms(_jitter_ms(35, jitter, 0.08, 0.18) if jitter else 25)

    def _loop(self, cfg):
        mode = cfg.get("mode") or "tap"
        infinite = bool(cfg.get("infinite"))
        total = int(cfg.get("count") or 100)
        hold_ms = int(cfg.get("holdMs") or 80)
        interval_ms = int(cfg.get("intervalMs") or 120)
        jitter = cfg.get("jitter", True) is not False
        points = list(cfg.get("points") or [])
        if mode == "tap":
            hold_ms = min(hold_ms, max(50, hold_ms))
        done = 0
        point_i = 0
        try:
            while not self._stop.is_set():
                if not infinite and done >= total:
                    break

                point_index = 0
                if points:
                    point = points[point_i % len(points)]
                    point_index = (point_i % len(points)) + 1
                    if not self._move_to_point(point, jitter):
                        break
                    point_i += 1

                try:
                    if not self._click_once(hold_ms, jitter):
                        break
                except Exception as exc:
                    self._emit_status({"state": "error", "message": str(exc) or "click_failed"})
                    break

                done += 1
                with self._lock:
                    self._done = done
                if done == 1 or done % 5 == 0 or points:
                    self._emit_status(
                        {
                            "state": "running",
                            "done": done,
                            "total": total,
                            "infinite": infinite,
                            "mode": mode,
                            "pointIndex": point_index,
                            "pointTotal": len(points),
                        }
                    )

                if self._stop.is_set():
                    break
                gap_ms = _jitter_ms(interval_ms, jitter)
                if not self._sleep_ms(gap_ms):
                    break

            with self._lock:
                self._running = False
                self._done = done
            if self._stop.is_set():
                self._emit_status({"state": "stopped", "done": done})
            else:
                self._emit_status({"state": "done", "done": done, "total": total})
        except Exception as exc:
            with self._lock:
                self._running = False
            self._emit_status({"state": "error", "message": str(exc) or "loop_error"})
        finally:
            try:
                _mouse_left_up()
            except Exception:
                pass

    def poll_hotkeys(self):
        """主线程定时：F8 启/停切换，F9 紧急中止。"""
        try:
            f8 = bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)
            f9 = bool(user32.GetAsyncKeyState(VK_F9) & 0x8000)
        except Exception:
            return
        if f8 and not self._f8_down:
            if self.is_running():
                self.stop()
            else:
                self.start()
        if f9 and not self._f9_down:
            if self.is_running():
                self.stop()
        self._f8_down = f8
        self._f9_down = f9


class WindowBridge(QObject):
    """供页面 JS 调用。"""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._offset = None

    @pyqtSlot(int, int)
    def beginDrag(self, screen_x, screen_y):
        self._window.set_interactable(True)
        top_left = self._window.frameGeometry().topLeft()
        self._offset = QPoint(screen_x, screen_y) - top_left

    @pyqtSlot(int, int)
    def dragTo(self, screen_x, screen_y):
        if self._offset is None:
            return
        self._window.move(QPoint(screen_x, screen_y) - self._offset)

    @pyqtSlot()
    def endDrag(self):
        self._offset = None

    @pyqtSlot(int, int)
    def followCursor(self, screen_x, screen_y):
        self._window.set_interactable(True)
        geo = self._window.frameGeometry()
        self._window.move(
            screen_x - geo.width() // 2,
            screen_y - geo.height() // 2,
        )

    @pyqtSlot(float, float, float, float)
    def updateHitRect(self, left, top, width, height):
        """网页内猫的 getBoundingClientRect（相对视口）。"""
        self._window.update_hit_rect(left, top, width, height)

    @pyqtSlot(bool)
    def setPetBusy(self, busy):
        """咬住/甩飞等互动中强制捕获鼠标。"""
        self._window.set_pet_busy(bool(busy))

    @pyqtSlot()
    def flingSystemCursor(self):
        """激怒甩飞：物理移动系统光标到右下角。"""
        self._window.fling_system_cursor()

    @pyqtSlot(bool)
    def setAutostart(self, enabled):
        set_autostart(bool(enabled))

    @pyqtSlot(result=bool)
    def getAutostart(self):
        return is_autostart_enabled()

    @pyqtSlot(bool)
    def setAutoHideFullscreen(self, enabled):
        """前端开关：全屏/大窗口自动避让。"""
        self._window.set_auto_hide_fullscreen(bool(enabled))

    @pyqtSlot(result=bool)
    def getAutoHideFullscreen(self):
        return bool(self._window._auto_hide_enabled)

    @pyqtSlot(bool)
    def setAppAwareEnabled(self, enabled):
        self._window.set_app_aware_enabled(bool(enabled))

    @pyqtSlot(bool)
    def setTypingAwareEnabled(self, enabled):
        self._window.set_typing_aware_enabled(bool(enabled))

    @pyqtSlot()
    def openSettings(self):
        """打开独立设置子窗口（非主窗内 DOM）。"""
        self._window.open_settings_window()

    @pyqtSlot(bool)
    def setOverlayExpanded(self, expanded):
        """记忆胶囊等浮层打开时临时扩大透明画布，避免按钮被窗边裁切。"""
        self._window.set_overlay_expanded(bool(expanded))

    @pyqtSlot()
    def closeSettings(self):
        """隐藏设置子窗口，不退出桌宠进程。"""
        self._window.close_settings_window()

    @pyqtSlot()
    def notifySettingsChanged(self):
        """设置页保存后通知猫咪主窗刷新配置。"""
        self._window.broadcast_settings_changed()

    @pyqtSlot()
    def closePet(self):
        """前端退出按钮：优雅退出整个桌宠进程。"""
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            try:
                self._window.close()
            except Exception:
                pass

    @pyqtSlot(result=str)
    def getClipboardText(self):
        """供前端读取剪贴板（酒馆对话兜底）。"""
        try:
            clip = QApplication.clipboard()
            if clip is None:
                return ""
            return str(clip.text() or "")
        except Exception:
            return ""

    @pyqtSlot(str)
    def startAutoClicker(self, cfg_json):
        """启动挂机连点器（独立子线程）。"""
        try:
            cfg = json.loads(cfg_json) if cfg_json else {}
        except Exception:
            cfg = {}
        try:
            self._window.start_auto_clicker(cfg if isinstance(cfg, dict) else {})
        except Exception:
            pass

    @pyqtSlot(str)
    def updateAutoClickerConfig(self, cfg_json):
        """仅更新连点参数（供 F8 使用），不立即启动。"""
        try:
            cfg = json.loads(cfg_json) if cfg_json else {}
        except Exception:
            cfg = {}
        try:
            svc = getattr(self._window, "_auto_clicker", None)
            if svc is not None and isinstance(cfg, dict):
                svc.update_config(cfg)
        except Exception:
            pass

    @pyqtSlot()
    def stopAutoClicker(self):
        """紧急中止连点。"""
        try:
            self._window.stop_auto_clicker()
        except Exception:
            pass

    @pyqtSlot(result=bool)
    def isAutoClickerRunning(self):
        try:
            return bool(self._window.is_auto_clicker_running())
        except Exception:
            return False

    @pyqtSlot(result=str)
    def getCursorScreenPos(self):
        """返回当前屏幕鼠标坐标 JSON。"""
        try:
            x, y = get_cursor_pos()
            return json.dumps({"x": int(x), "y": int(y)})
        except Exception:
            return '{"x":0,"y":0}'

    @pyqtSlot(str)
    def syncClickerPointMarkers(self, points_json):
        """在屏幕坐标处显示序号浮标。"""
        try:
            raw = json.loads(points_json) if points_json else []
        except Exception:
            raw = []
        try:
            self._window.sync_clicker_point_markers(raw if isinstance(raw, list) else [])
        except Exception:
            pass

    @pyqtSlot()
    def clearClickerPointMarkers(self):
        try:
            self._window.clear_clicker_point_markers()
        except Exception:
            pass


class SettingsWindowBridge(QObject):
    """设置子窗专用桥：关闭与无边框拖拽（不移动桌宠主窗）。"""

    def __init__(self, settings_widget):
        super().__init__()
        self._win = settings_widget
        self._offset = None

    @pyqtSlot()
    def closeSettings(self):
        try:
            self._win.hide()
        except Exception:
            pass

    @pyqtSlot()
    def close_window(self):
        """前端自定义标题栏关闭入口。"""
        self.closeSettings()

    @pyqtSlot(int, int)
    def beginDrag(self, screen_x, screen_y):
        try:
            top_left = self._win.frameGeometry().topLeft()
            self._offset = QPoint(screen_x, screen_y) - top_left
        except Exception:
            self._offset = None

    @pyqtSlot(int, int)
    def dragTo(self, screen_x, screen_y):
        if self._offset is None:
            return
        try:
            self._win.move(QPoint(screen_x, screen_y) - self._offset)
        except Exception:
            pass

    @pyqtSlot()
    def endDrag(self):
        self._offset = None

    @pyqtSlot()
    def notifySettingsChanged(self):
        try:
            pet = getattr(self._win, "_pet", None)
            if pet is not None:
                pet.broadcast_settings_changed()
        except Exception:
            pass


class SettingsWidget(QWidget):
    """独立设置窗口：无边框 + 前端自定义标题栏拖拽/关闭。"""

    def __init__(self, profile, pet_window):
        super().__init__(None)
        self._pet = pet_window
        self.setWindowTitle("八条猫 · 设置")
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.resize(540, 720)

        self.view = QWebEngineView(self)
        self.view.setAttribute(Qt.WA_TranslucentBackground, True)
        self.view.setStyleSheet("background: transparent;")
        page = QWebEnginePage(profile, self.view)
        self.view.setPage(page)
        page.setBackgroundColor(QColor(Qt.transparent))

        settings = page.settings()
        apply_webengine_settings(settings)

        self.bridge = SettingsWindowBridge(self)
        channel = QWebChannel(page)
        channel.registerObject("bridge", self.bridge)
        page.setWebChannel(channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        if not os.path.isfile(resolve_html_path()):
            raise FileNotFoundError("找不到 index.html: " + resolve_html_path())
        self.view.loadFinished.connect(self._on_load_finished)
        self.view.load(QUrl(local_page_url("view=settings")))

        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WindowShortcut)
        esc.activated.connect(self._on_escape)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2,
        )

    def _on_escape(self):
        # 优先交给页面关闭 cfg-modal；若无弹窗再隐藏设置窗
        self.view.page().runJavaScript(
            r"""
            (function () {
              var m = document.getElementById('cfg-modal');
              if (m && m.getAttribute('aria-hidden') === 'false') {
                if (typeof closeCfgModal === 'function') { try { closeCfgModal(null); } catch (e) {} }
                else {
                  m.hidden = true;
                  m.style.display = 'none';
                  m.setAttribute('aria-hidden', 'true');
                }
                return true;
              }
              return false;
            })();
            """,
            self._after_escape_js,
        )

    def _after_escape_js(self, closed_modal):
        if not closed_modal:
            self.hide()

    def _on_load_finished(self, ok):
        if not ok:
            return
        self.view.page().runJavaScript(
            r"""
            (function () {
              function connectBridge() {
                if (typeof qt === 'undefined' || !qt.webChannelTransport) return;
                new QWebChannel(qt.webChannelTransport, function (channel) {
                  window.bridge = channel.objects.bridge;
                  try {
                    if (typeof window.__bindSettingsWindowChrome === 'function') {
                      window.__bindSettingsWindowChrome();
                    }
                  } catch (e) {}
                });
              }
              if (typeof QWebChannel === 'undefined') {
                var s = document.createElement('script');
                s.src = 'qrc:///qtwebchannel/qwebchannel.js';
                s.onload = connectBridge;
                document.head.appendChild(s);
              } else {
                connectBridge();
              }
            })();
            """
        )

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def show_and_focus(self):
        self.show()
        self.raise_()
        self.activateWindow()


class PetWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("八条猫")
        # 标准 32 位 Alpha 透明混合（半透明 / 抗锯齿），勿再用 Win32 色键抠像
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.SubWindow
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")

        # 桌宠主窗：为猫咪、头顶气泡与居中浮层（记忆胶囊等）留足安全画布
        self.resize(520, 680)
        self._base_size = (520, 680)
        self._overlay_expanded = False
        self._size_before_overlay = None

        self._hit_rect = QRectF(40, 60, 440, 560)
        self._click_through = False
        self._pet_busy = False
        self._flinging = False
        self._hwnd = None
        self._auto_hide_enabled = False
        self._avoiding = False
        self._app_aware_enabled = False
        self._typing_aware_enabled = False
        # 前台软件感知状态（仅内存，不落盘敏感信息）
        self._fg_candidate_key = None
        self._fg_candidate_since = 0.0
        self._fg_active_key = None
        self._fg_fired_key = None
        self._fg_last_fire_at = {}
        # 打字活动：只计次数与时间戳
        self._typing_dense = False
        self._typing_hits_window = []
        self._typing_session_start = 0.0
        self._typing_last_hit = 0.0
        self._typing_encouraged = False
        self.setWindowOpacity(1.0)

        self._settings_win = None

        # 与设置窗共享 Profile，才能共用 localStorage
        self._profile = QWebEngineProfile("hachikyu_pet_shared", self)
        storage = os.path.join(app_base_dir(), ".webengine_storage")
        try:
            os.makedirs(storage, exist_ok=True)
            self._profile.setPersistentStoragePath(storage)
            self._profile.setCachePath(os.path.join(storage, "cache"))
        except OSError:
            pass

        self.view = QWebEngineView(self)
        self.view.setAttribute(Qt.WA_TranslucentBackground, True)
        self.view.setStyleSheet("background: transparent;")
        page = QWebEnginePage(self._profile, self.view)
        self.view.setPage(page)
        # 透明页背景（勿设洋红色等色键底色）
        page.setBackgroundColor(QColor(Qt.transparent))

        settings = page.settings()
        apply_webengine_settings(settings)

        self.bridge = WindowBridge(self)
        channel = QWebChannel(page)
        channel.registerObject("bridge", self.bridge)
        page.setWebChannel(channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        html_path = resolve_html_path()
        if not os.path.isfile(html_path):
            raise FileNotFoundError(
                "找不到 index.html，请放在 exe 同目录。\n当前查找路径: " + html_path
            )

        self.view.loadFinished.connect(self._on_load_finished)
        # 走本地 HTTP，避免 file:// 下部分资源加载受限
        self.view.load(QUrl(local_page_url()))

        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.ApplicationShortcut)
        esc.activated.connect(QApplication.instance().quit)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 3,
        )

        self._hit_timer = QTimer(self)
        self._hit_timer.setInterval(30)
        self._hit_timer.timeout.connect(self._poll_click_through)
        self._hit_timer.start()

        self._fling_timer = QTimer(self)
        self._fling_timer.setInterval(16)
        self._fling_timer.timeout.connect(self._fling_tick)
        self._fling_step = 0
        self._fling_steps = 0
        self._fling_start = (0, 0)
        self._fling_end = (0, 0)

        # 全屏/大窗避让：约 1.5s 检查一次，极轻量
        self._avoid_timer = QTimer(self)
        self._avoid_timer.setInterval(1500)
        self._avoid_timer.timeout.connect(self._poll_foreground_avoid)
        self._avoid_timer.start()

        # 前台软件感知
        self._app_timer = QTimer(self)
        self._app_timer.setInterval(800)
        self._app_timer.timeout.connect(self._poll_foreground_app)
        self._app_timer.start()

        # 打字活动感知（不记录键值）
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(80)
        self._typing_timer.timeout.connect(self._poll_typing_activity)
        self._typing_timer.start()

        # 挂机连点器：子线程执行 + F8/F9 热键轮询
        self._auto_clicker = AutoClickerService(self)
        self._auto_clicker.statusChanged.connect(self._on_auto_clicker_status)
        self._clicker_hotkey_timer = QTimer(self)
        self._clicker_hotkey_timer.setInterval(50)
        self._clicker_hotkey_timer.timeout.connect(self._auto_clicker.poll_hotkeys)
        self._clicker_hotkey_timer.start()
        self._clicker_markers = []

        self._setup_tray()

    def _setup_tray(self):
        """托盘常驻：窗口 hide() 后进程不退出。"""
        icon_path = os.path.join(app_base_dir(), "app.ico")
        if os.path.isfile(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = self.style().standardIcon(self.style().SP_ComputerIcon)

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("八条猫桌宠")

        menu = QMenu()
        act_show = QAction("显示八条猫", self)
        act_show.triggered.connect(self._force_show_from_tray)
        act_settings = QAction("设置", self)
        act_settings.triggered.connect(self.open_settings_window)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_show)
        menu.addAction(act_settings)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def open_settings_window(self):
        if self._settings_win is None:
            self._settings_win = SettingsWidget(self._profile, self)
        self._settings_win.show_and_focus()

    def close_settings_window(self):
        if self._settings_win is not None:
            self._settings_win.hide()

    def broadcast_settings_changed(self):
        """设置子窗口写完 localStorage 后，让猫窗立刻重载配置。"""
        try:
            self.view.page().runJavaScript(
                "try{if(window.__reloadSettingsFromPeer)window.__reloadSettingsFromPeer();}catch(e){}"
            )
        except Exception:
            pass

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._force_show_from_tray()

    def _force_show_from_tray(self):
        self._avoiding = False
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        self._hwnd = int(self.winId())
        # 启动时先穿透，避免挡桌面
        self.set_click_through(True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            QApplication.instance().quit()
            return
        super().keyPressEvent(event)

    def update_hit_rect(self, left, top, width, height):
        # 略向外扩，保证气泡 / ✨ / 收藏星可点；勿向内收缩导致穿透
        expand = 8.0
        self._hit_rect = QRectF(
            float(left) - expand,
            float(top) - expand,
            max(1.0, float(width) + expand * 2),
            max(1.0, float(height) + expand * 2),
        )

    def set_overlay_expanded(self, expanded):
        """记忆胶囊等全屏居中浮层：临时放大透明窗，关闭后恢复。"""
        expanded = bool(expanded)
        if expanded == self._overlay_expanded:
            return
        geo = self.frameGeometry()
        cx, cy = geo.center().x(), geo.center().y()
        if expanded:
            self._size_before_overlay = (self.width(), self.height())
            tw = max(self.width(), 560)
            th = max(self.height(), 720)
            self.resize(tw, th)
            self._overlay_expanded = True
        else:
            if self._size_before_overlay:
                self.resize(int(self._size_before_overlay[0]), int(self._size_before_overlay[1]))
            else:
                self.resize(int(self._base_size[0]), int(self._base_size[1]))
            self._size_before_overlay = None
            self._overlay_expanded = False
        self.move(cx - self.width() // 2, cy - self.height() // 2)

    def start_auto_clicker(self, cfg):
        if not hasattr(self, "_auto_clicker") or self._auto_clicker is None:
            return
        self._auto_clicker.start(cfg if isinstance(cfg, dict) else {})

    def stop_auto_clicker(self):
        if not hasattr(self, "_auto_clicker") or self._auto_clicker is None:
            return
        self._auto_clicker.stop()

    def is_auto_clicker_running(self):
        if not hasattr(self, "_auto_clicker") or self._auto_clicker is None:
            return False
        return bool(self._auto_clicker.is_running())

    def clear_clicker_point_markers(self):
        markers = getattr(self, "_clicker_markers", None) or []
        self._clicker_markers = []
        for m in markers:
            try:
                m.hide()
                m.close()
                m.deleteLater()
            except Exception:
                pass

    def sync_clicker_point_markers(self, points):
        """根据点位列表重建屏幕序号浮标。"""
        self.clear_clicker_point_markers()
        pts = _normalize_clicker_points(points)
        for i, p in enumerate(pts):
            try:
                marker = ClickerPointMarker(i + 1, p["x"], p["y"], None)
                marker.show()
                marker.raise_()
                self._clicker_markers.append(marker)
            except Exception:
                pass

    def _on_auto_clicker_status(self, payload_json):
        """把连点状态推回前端。"""
        try:
            safe = json.dumps(str(payload_json or ""), ensure_ascii=False)
            js = (
                "try{if(typeof window.__onClickerStatus==='function')"
                "window.__onClickerStatus(JSON.parse(%s))}catch(e){}"
            ) % safe
            self.view.page().runJavaScript(js)
        except Exception:
            pass

    def set_pet_busy(self, busy):
        self._pet_busy = busy
        if busy:
            self.set_interactable(True)

    def set_interactable(self, interactable):
        self.set_click_through(not interactable)

    def set_click_through(self, enabled):
        if self._flinging:
            enabled = True
        if self._click_through == enabled and self._hwnd:
            return
        self._click_through = enabled
        if self._hwnd is None:
            try:
                self._hwnd = int(self.winId())
            except Exception:
                return
        set_click_through(self._hwnd, enabled)

    def _poll_click_through(self):
        # 避让淡出时保持穿透
        if self._avoiding:
            self.set_click_through(True)
            return
        # 甩飞系统光标时保持穿透，避免窗口吞掉位移中的点击
        if self._flinging:
            self.set_click_through(True)
            return
        # 咬住/动画互动中强制捕获
        if self._pet_busy or self._offset_dragging():
            self.set_click_through(False)
            return

        pos = QCursor.pos()
        local = self.mapFromGlobal(pos)
        over_pet = self._hit_rect.contains(float(local.x()), float(local.y()))
        # 在猫身上：捕获；透明区：穿透
        self.set_click_through(not over_pet)

    def set_auto_hide_fullscreen(self, enabled):
        self._auto_hide_enabled = bool(enabled)
        if not self._auto_hide_enabled and self._avoiding:
            self._set_avoiding(False)
        elif self._auto_hide_enabled:
            self._poll_foreground_avoid()

    def set_app_aware_enabled(self, enabled):
        self._app_aware_enabled = bool(enabled)
        if not self._app_aware_enabled:
            self._fg_candidate_key = None
            self._fg_active_key = None
            self._fg_fired_key = None

    def set_typing_aware_enabled(self, enabled):
        self._typing_aware_enabled = bool(enabled)
        if not self._typing_aware_enabled:
            self._typing_dense = False
            self._typing_hits_window = []
            self._typing_session_start = 0.0
            self._typing_encouraged = False
            self._notify_js("window.__onTypingPulse && window.__onTypingPulse(false);")

    def _notify_js(self, code):
        try:
            self.view.page().runJavaScript(code)
        except Exception:
            pass

    def _poll_foreground_app(self):
        if not self._app_aware_enabled:
            return
        if self._hwnd is None and self.isVisible():
            try:
                self._hwnd = int(self.winId())
            except Exception:
                pass

        fg = user32.GetForegroundWindow()
        if not fg or (self._hwnd and fg == self._hwnd):
            self._fg_candidate_key = None
            self._fg_active_key = None
            return

        title = get_window_text(fg)
        exe = get_foreground_process_name(fg)
        # 忽略自身进程
        exe_l = (exe or "").lower()
        if exe_l in ("main.exe", "python.exe", "pythonw.exe") and "八条猫" in (title or ""):
            return
        if "hachikyu" in exe_l:
            return

        matched = match_work_app(exe, title)
        now = time.time()
        if not matched:
            self._fg_candidate_key = None
            self._fg_active_key = None
            self._fg_fired_key = None
            return

        app_name, scene, tag = matched[0], matched[1], (matched[2] if len(matched) > 2 else "work")
        key = "%s|%s" % (app_name, exe_l)

        if self._fg_candidate_key != key:
            self._fg_candidate_key = key
            self._fg_candidate_since = now
            self._fg_active_key = None
            return

        # 停留满 3 秒才算“刚切换并稳住”
        if now - self._fg_candidate_since < 3.0:
            return

        self._fg_active_key = key
        if self._fg_fired_key == key:
            return

        last = self._fg_last_fire_at.get(app_name, 0)
        if now - last < 90:  # 同软件 90 秒内不重复
            self._fg_fired_key = key
            return

        self._fg_fired_key = key
        self._fg_last_fire_at[app_name] = now
        payload = json.dumps(
            {"name": app_name, "scene": scene, "tag": tag},
            ensure_ascii=False,
        )
        self._notify_js(
            "window.__onForegroundApp && window.__onForegroundApp(%s);" % payload
        )

    def _poll_typing_activity(self):
        if not self._typing_aware_enabled:
            return
        now = time.time()
        # 仅布尔活动，不保留键码
        if any_key_activity_pulse():
            self._typing_hits_window.append(now)
            self._typing_last_hit = now
            if not self._typing_session_start:
                self._typing_session_start = now
                self._typing_encouraged = False

        # 保留近 2 秒内的击键次数
        cutoff = now - 2.0
        self._typing_hits_window = [t for t in self._typing_hits_window if t >= cutoff]
        dense = len(self._typing_hits_window) >= 6

        if dense != self._typing_dense:
            self._typing_dense = dense
            self._notify_js(
                "window.__onTypingPulse && window.__onTypingPulse(%s);"
                % ("true" if dense else "false")
            )

        # 空闲超过 8 秒：结束本次连续敲字会话
        if self._typing_session_start and (now - self._typing_last_hit) > 8.0:
            self._typing_session_start = 0.0
            self._typing_encouraged = False
            if self._typing_dense:
                self._typing_dense = False
                self._notify_js("window.__onTypingPulse && window.__onTypingPulse(false);")
            return

        # 连续敲字满 5 分钟 → 鼓励一次
        if (
            self._typing_session_start
            and not self._typing_encouraged
            and (now - self._typing_session_start) >= 300
            and (now - self._typing_last_hit) <= 8.0
        ):
            self._typing_encouraged = True
            self._notify_js(
                "window.__onTypingEncourage && window.__onTypingEncourage();"
            )

    def _set_avoiding(self, avoid):
        """WebEngine 透明窗下 opacity 不可靠，改用 hide()/show()。"""
        avoid = bool(avoid)
        if self._avoiding == avoid:
            return
        self._avoiding = avoid

        if avoid:
            self.set_click_through(True)
            self.hide()
        else:
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            # 恢复后重新取 hwnd（hide 后再 show 可能变化）
            try:
                self._hwnd = int(self.winId())
            except Exception:
                pass
            self.set_click_through(True)

    def _poll_foreground_avoid(self):
        if not self._auto_hide_enabled:
            if self._avoiding:
                self._set_avoiding(False)
            return
        # 互动中不躲，避免咬住光标时突然消失
        if self._pet_busy or self._flinging or self._offset_dragging():
            return
        if self._hwnd is None and self.isVisible():
            self._hwnd = int(self.winId())
        try:
            should_hide = is_foreground_overlay_target(self._hwnd or 0)
        except Exception:
            should_hide = False
        self._set_avoiding(should_hide)

    def _offset_dragging(self):
        return self.bridge._offset is not None

    def fling_system_cursor(self):
        if self._flinging:
            return
        self._flinging = True
        self.set_click_through(True)

        sx, sy = get_cursor_pos()
        screen = QApplication.primaryScreen().geometry()
        ex = screen.x() + screen.width() - 8
        ey = screen.y() + screen.height() - 8
        self._fling_start = (sx, sy)
        self._fling_end = (ex, ey)
        self._fling_step = 0
        self._fling_steps = 42  # ~0.67s @ 16ms
        self._fling_timer.start()

    def _fling_tick(self):
        self._fling_step += 1
        t = min(1.0, self._fling_step / float(self._fling_steps))
        # 缓出 + 抛物线抬升再落向右下角
        ease = 1.0 - (1.0 - t) ** 3
        x0, y0 = self._fling_start
        x1, y1 = self._fling_end
        x = x0 + (x1 - x0) * ease
        arc = -math.sin(math.pi * t) * min(220.0, abs(x1 - x0) * 0.35)
        y = y0 + (y1 - y0) * ease + arc
        set_cursor_pos(x, y)

        if t >= 1.0:
            self._fling_timer.stop()
            set_cursor_pos(x1, y1)
            self._flinging = False
            # _pet_busy 由网页 resetIdle / setPetBusy(false) 负责清除

    def _on_load_finished(self, ok):
        if not ok:
            return
        self.view.page().runJavaScript(
            r"""
            (function () {
              if (window.__petShellInjected) return;
              window.__petShellInjected = true;

              var style = document.createElement('style');
              style.textContent = [
                'html, body {',
                '  background: transparent !important;',
                '  background-image: none !important;',
                '}',
                '#stage { background: transparent !important; }',
                '#pet {',
                '  left: 50% !important;',
                '  top: 52% !important;',
                '}',
                '#pet.biting {',
                '  left: 50% !important;',
                '  top: 55% !important;',
                '}'
              ].join(String.fromCharCode(10));
              document.head.appendChild(style);

              function bootChannel() {
                if (typeof QWebChannel === 'undefined') {
                  var s = document.createElement('script');
                  s.src = 'qrc:///qtwebchannel/qwebchannel.js';
                  s.onload = connectBridge;
                  document.head.appendChild(s);
                } else {
                  connectBridge();
                }
              }

              function connectBridge() {
                new QWebChannel(qt.webChannelTransport, function (channel) {
                  window.bridge = channel.objects.bridge;
                  bindWindowDrag();
                  if (typeof window.__onPetBridgeReady === 'function') {
                    window.__onPetBridgeReady(window.bridge);
                  }
                });
              }

              function bindWindowDrag() {
                var pet = document.getElementById('pet');
                if (!pet || !window.bridge) return;

                var pressing = false;

                function isUiChrome(el) {
                  if (!el || !el.closest) return false;
                  return !!(
                    el.closest('#fav-star') ||
                    el.closest('#chat-spark') ||
                    el.closest('#memo-btn') ||
                    el.closest('#food-btn') ||
                    el.closest('#game-btn') ||
                    el.closest('#btn-pet-quit') ||
                    el.closest('#food-reel') ||
                    el.closest('#memo-panel') ||
                    el.closest('#chat-panel') ||
                    el.closest('#game-modal') ||
                    el.closest('#bubble') ||
                    el.closest('#settings')
                  );
                }

                document.addEventListener('pointerdown', function (e) {
                  if (isUiChrome(e.target)) {
                    pressing = false;
                    if (window.bridge && window.bridge.endDrag) {
                      try { window.bridge.endDrag(); } catch (err) {}
                    }
                    return;
                  }
                  if (!e.target.closest || !e.target.closest('#pet')) return;
                  pressing = true;
                  window.bridge.beginDrag(e.screenX, e.screenY);
                }, true);

                document.addEventListener('pointermove', function (e) {
                  if (!window.bridge) return;
                  if (pet.classList.contains('biting')) {
                    window.bridge.followCursor(e.screenX, e.screenY);
                    return;
                  }
                  if (pressing) {
                    window.bridge.dragTo(e.screenX, e.screenY);
                  }
                }, true);

                document.addEventListener('pointerup', function () {
                  pressing = false;
                  if (window.bridge) window.bridge.endDrag();
                }, true);

                document.addEventListener('pointercancel', function () {
                  pressing = false;
                  if (window.bridge) window.bridge.endDrag();
                }, true);
              }

              bootChannel();
            })();
            """
        )


def main():
    # 开机自启动：写入当前用户 Run 项（静默 pythonw / exe）
    try:
        set_autostart(True)
    except OSError:
        pass

    app = QApplication(sys.argv)
    # 避让 hide() 后仍靠托盘存活，勿随最后窗口关闭而退出
    app.setQuitOnLastWindowClosed(False)

    enable_global_webengine_settings()

    # 先拉起本地静态服务，再创建 WebEngine 窗口
    start_local_http_server(app_base_dir())
    app.aboutToQuit.connect(stop_local_http_server)

    widget = PetWidget()
    app.aboutToQuit.connect(widget.stop_auto_clicker)
    app.aboutToQuit.connect(widget.clear_clicker_point_markers)
    widget.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
