import flet as ft
import sys
import threading
import json
import os
import subprocess
import platform
import shutil
import zipfile
import stat
import requests
import time
import proxy_processor

# ==========================================
# ⚙️ CONFIGURATION & PATHS
# ==========================================

INTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))

def get_work_dir():
    if "ANDROID_ARGUMENT" in os.environ:
        base = os.environ.get("HOME", "/data/data/com.psgstation/files")
        path = os.path.join(base, "psg_data")
        if not os.path.exists(path): 
            try: os.makedirs(path)
            except: pass
        return path
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()

WORK_DIR = get_work_dir()

USER_CHANNELS_DIR = os.path.join(WORK_DIR, "channelsData")
USER_ASSETS_FILE = os.path.join(USER_CHANNELS_DIR, "channelsAssets.json")
BUNDLED_ASSETS_FILE = os.path.join(INTERNAL_DIR, "channelsData", "channelsAssets.json")

SETTINGS_FILE = os.path.join(WORK_DIR, "settings.json")
SUMMARY_FILE = os.path.join(WORK_DIR, "subscriptions", "summary.json")
CONFIG_FILE = os.path.join(WORK_DIR, "config.txt")

IS_WINDOWS = sys.platform == "win32"
XRAY_KNIFE_EXE = "xray-knife.exe" if IS_WINDOWS else "xray-knife"
XRAY_KNIFE_PATH = os.path.join(WORK_DIR, XRAY_KNIFE_EXE)

REPO_OWNER = "lilendian0x00"
REPO_NAME = "xray-knife"

# --- MODERN THEME PALETTE ---
COLOR_BG = "#09090B"        # Void Black
COLOR_SURFACE = "#18181B"   # Zinc 900
COLOR_SURFACE_L = "#27272A" # Zinc 800
COLOR_PRIMARY = "#8B5CF6"   # Violet 500
COLOR_ACCENT = "#06B6D4"    # Cyan 500
COLOR_SUCCESS = "#10B981"   # Emerald 500
COLOR_ERROR = "#EF4444"     # Red 500
COLOR_TEXT = "#FAFAFA"
COLOR_DIM = "#A1A1AA"

DEFAULT_SETTINGS = {
    "branding_name": "PSG",
    "max_per_channel": 40,
    "timeout": 10,
    "enable_converters": True,
    "fake_configs": "#همکاری_ملی,#جاویدشاه"
}

# ==========================================
# 🛠️ UTILITIES
# ==========================================

def get_opacity_color(color_hex, opacity):
    """Adds alpha channel to a hex color."""
    if color_hex.startswith("#"): color_hex = color_hex[1:]
    alpha = int(opacity * 255)
    return f"#{alpha:02x}{color_hex}"

class Logger:
    def __init__(self, log_control):
        self.log_control = log_control
        self.terminal = sys.stdout

    def write(self, message):
        try: self.terminal.write(message)
        except: pass
        if message.strip():
            self.log_control.controls.append(
                ft.Container(
                    content=ft.Text(f"> {message.strip()}", font_family="monospace", size=11, color=COLOR_DIM),
                    padding=ft.padding.only(left=5),
                    border=ft.Border(left=ft.BorderSide(2, COLOR_SURFACE_L))
                )
            )
            self.log_control.update()
            self.log_control.scroll_to(offset=-1, duration=300, curve=ft.AnimationCurve.EASE_OUT)

    def flush(self):
        try: self.terminal.flush()
        except: pass

def get_target_asset_name():
    system = platform.system().lower()
    machine = platform.machine().lower()
    if "android" in system or "linux" in system: 
        if "aarch64" in machine or "arm64" in machine: return "Xray-knife-linux-arm64-v8a.zip", None
        return "Xray-knife-linux-64.zip", None
    if system == "windows": return "Xray-knife-windows-64.zip", None
    if system == "darwin": return "Xray-knife-macos-64.zip", None
    return None, "Unknown OS"

# ==========================================
# 📱 MAIN APPLICATION
# ==========================================

def main(page: ft.Page):
    # Init
    if not os.path.exists(SETTINGS_FILE):
        src = os.path.join(INTERNAL_DIR, "settings.json")
        if os.path.exists(src): 
            try: shutil.copy(src, SETTINGS_FILE)
            except: pass

    if not os.path.exists(USER_ASSETS_FILE):
        if not os.path.exists(USER_CHANNELS_DIR): os.makedirs(USER_CHANNELS_DIR)
        if os.path.exists(BUNDLED_ASSETS_FILE): 
            try: shutil.copy(BUNDLED_ASSETS_FILE, USER_ASSETS_FILE)
            except: pass

    # Page Config
    page.title = "PSG Station"
    page.bgcolor = COLOR_BG
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.window.width = 450
    page.window.height = 900
    
    # --- Data Helpers ---
    def load_json(path, default={}):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except: pass
        return default

    def save_json(path, data):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

    # --- Shared State ---
    log_lv = ft.ListView(expand=True, spacing=2, auto_scroll=False)
    logger = Logger(log_lv)
    
    # --- Modern Status Indicator ---
    status_ring = ft.ProgressRing(width=160, height=160, stroke_width=6, color=COLOR_PRIMARY, value=0, bgcolor=COLOR_SURFACE)
    status_icon = ft.Icon(ft.Icons.BOLT_ROUNDED, size=60, color=COLOR_PRIMARY)
    status_text = ft.Text("Ready to Start", size=18, weight="bold", color="white")
    status_sub = ft.Text("Waiting for action...", size=12, color=COLOR_DIM)

    def stat_card(icon, label, value, color):
        # FIX: Use helper function for opacity
        bg_color_dim = get_opacity_color(color, 0.15)
        
        return ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Icon(icon, color=color, size=20),
                    padding=8, bgcolor=bg_color_dim, border_radius=8
                ),
                ft.Text(value, size=24, weight="bold"),
                ft.Text(label, size=11, color=COLOR_DIM)
            ], spacing=5),
            bgcolor=COLOR_SURFACE, 
            border_radius=16, 
            padding=15, 
            expand=True,
            border=ft.border.all(1, COLOR_SURFACE_L)
        )

    st_raw = stat_card(ft.Icons.DATA_USAGE_ROUNDED, "Configs", "-", COLOR_PRIMARY)
    st_loc = stat_card(ft.Icons.PUBLIC_ROUNDED, "Countries", "-", COLOR_ACCENT)
    st_src = stat_card(ft.Icons.SOURCE_ROUNDED, "Sources", "-", COLOR_SUCCESS)

    # --- File Export ---
    export_ref = ft.Ref()
    
    def on_save_result(e: ft.FilePickerResultEvent):
        if e.path:
            try:
                shutil.copy(export_ref.current, e.path)
                page.open(ft.SnackBar(ft.Text(f"Saved to: {e.path}")))
            except Exception as ex:
                page.open(ft.SnackBar(ft.Text(f"Error: {ex}")))

    file_picker = ft.FilePicker(on_result=on_save_result)
    page.overlay.append(file_picker)

    def trigger_export(path):
        export_ref.current = path
        fname = os.path.basename(path)
        file_picker.save_file(file_name=fname)

    # --- Logic ---
    def load_stats():
        d = load_json(SUMMARY_FILE)
        if d:
            st_raw.content.controls[1].value = str(d.get('configs', {}).get('total_raw', 0))
            st_loc.content.controls[1].value = str(len(d.get('outputs', {}).get('country_distribution', {})))
            st_src.content.controls[1].value = str(d.get('sources', {}).get('valid', 0))
            page.update()

    def download_tool(on_done=None):
        try:
            target, err = get_target_asset_name()
            if err: return logger.write(f"Error: {err}")
            
            logger.write(f"Downloading {target}...")
            url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
            assets = requests.get(url).json().get("assets", [])
            dl_url = next((x["browser_download_url"] for x in assets if x["name"] == target), None)
            
            if not dl_url: return logger.write("Asset not found.")
            
            zip_path = os.path.join(WORK_DIR, "tool.zip")
            with requests.get(dl_url, stream=True) as r:
                with open(zip_path, 'wb') as f: shutil.copyfileobj(r.raw, f)
            
            with zipfile.ZipFile(zip_path, 'r') as z: z.extractall(WORK_DIR)
            
            if not IS_WINDOWS:
                os.chmod(XRAY_KNIFE_PATH, 0o777)
            try: os.remove(zip_path)
            except: pass
            
            logger.write("✅ Tool Ready.")
            if on_done: on_done()
        except Exception as e:
            logger.write(f"Download Error: {e}")

    def run_process(e):
        if not os.path.exists(XRAY_KNIFE_PATH):
            bs_logs.open = True
            bs_logs.update()
            threading.Thread(target=download_tool, args=(run_vpn_check,), daemon=True).start()
        else:
            run_vpn_check()

    def run_vpn_check():
        def start(e):
            page.close(dlg_vpn)
            status_ring.value = None 
            status_icon.name = ft.Icons.SYNC_ROUNDED
            status_text.value = "Fetching..."
            status_sub.value = "Downloading from Telegram..."
            page.update()
            
            proxy_processor.reset_globals()
            settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
            proxy_processor.init_globals(settings)
            
            sys.stdout = logger
            threading.Thread(target=stage_1_thread, daemon=True).start()

        dlg_vpn = ft.AlertDialog(
            title=ft.Text("Enable VPN", weight="bold"), 
            content=ft.Text("VPN is required to fetch Telegram channels."),
            actions=[ft.ElevatedButton("I'm Connected", on_click=start, bgcolor=COLOR_PRIMARY, color="white")],
            bgcolor=COLOR_SURFACE
        )
        page.open(dlg_vpn)

    def stage_1_thread():
        try:
            proxy_processor.run_stage_1()
            if proxy_processor.ABORT_FLAG: finish_ui(False)
            else: show_stage_2_confirm()
        except: finish_ui(False)

    def show_stage_2_confirm():
        def go(e):
            page.close(dlg_s2)
            status_text.value = "Processing..."
            status_sub.value = "Filtering & Sorting..."
            page.update()
            s = load_json(SETTINGS_FILE)
            threading.Thread(target=stage_2_thread, args=(s.get('enable_converters', True),), daemon=True).start()

        dlg_s2 = ft.AlertDialog(
            title=ft.Text("Fetch Done", weight="bold"), 
            content=ft.Text("Disable VPN now for accurate speedtest?"),
            actions=[ft.TextButton("Continue", on_click=go)],
            bgcolor=COLOR_SURFACE
        )
        page.open(dlg_s2)

    def stage_2_thread(conv):
        try:
            proxy_processor.run_stage_2_5(convert=conv)
            finish_ui(True)
        except: finish_ui(False)

    def finish_ui(success):
        status_ring.value = 0
        status_icon.name = ft.Icons.CHECK_CIRCLE_ROUNDED if success else ft.Icons.ERROR_ROUNDED
        status_icon.color = COLOR_SUCCESS if success else COLOR_ERROR
        status_text.value = "Completed" if success else "Stopped"
        status_sub.value = "Results ready in Files tab" if success else "Process interrupted"
        
        btn_action.disabled = False
        btn_action.text = "START"
        btn_action.icon = ft.Icons.PLAY_ARROW_ROUNDED
        btn_action.style.bgcolor = COLOR_PRIMARY
        
        if success: 
            load_stats()
            refresh_files()
        page.update()

    def handle_action_click(e):
        if btn_action.text == "START":
            btn_action.text = "STOP"
            btn_action.icon = ft.Icons.STOP_ROUNDED
            btn_action.style.bgcolor = COLOR_ERROR
            page.update()
            run_process(e)
        else:
            btn_action.disabled = True
            btn_action.text = "STOPPING..."
            page.update()
            proxy_processor.stop_processing()

    # ==========================
    # 📱 VIEWS
    # ==========================

    # --- 1. DASHBOARD ---
    bs_logs = ft.BottomSheet(
        ft.Container(
            ft.Column([
                ft.Container(width=40, height=4, bgcolor=COLOR_SURFACE_L, border_radius=2, margin=ft.margin.only(bottom=10)),
                ft.Text("System Logs", weight="bold", size=16),
                ft.Container(log_lv, height=300, bgcolor="black", border_radius=12, padding=10),
            ], spacing=10, horizontal_alignment="center"),
            padding=20, bgcolor=COLOR_SURFACE, border_radius=ft.border_radius.vertical(top=20)
        )
    )

    btn_action = ft.ElevatedButton(
        "START", icon=ft.Icons.PLAY_ARROW_ROUNDED, on_click=handle_action_click,
        style=ft.ButtonStyle(bgcolor=COLOR_PRIMARY, color="white", shape=ft.RoundedRectangleBorder(radius=16), padding=20),
        width=180, height=55
    )

    view_dashboard = ft.Container(
        content=ft.Column([
            ft.Container(height=20),
            ft.Stack([
                ft.Container(content=status_ring, alignment=ft.alignment.center),
                ft.Container(content=status_icon, alignment=ft.alignment.center, padding=0),
            ], height=160, width=160),
            ft.Text(status_text.value, ref=status_text, size=22, weight="bold"),
            ft.Text(status_sub.value, ref=status_sub, size=13, color=COLOR_DIM),
            ft.Container(height=30),
            ft.Row([st_raw, st_loc], spacing=15),
            ft.Container(height=5),
            ft.Row([st_src], spacing=15),
            ft.Container(expand=True),
            ft.Row([
                ft.IconButton(ft.Icons.TERMINAL_ROUNDED, icon_color=COLOR_DIM, on_click=lambda e: page.open(bs_logs), tooltip="Logs"),
                btn_action,
                ft.IconButton(ft.Icons.REFRESH_ROUNDED, icon_color=COLOR_DIM, on_click=lambda e: load_stats(), tooltip="Refresh"),
            ], alignment=ft.MainAxisAlignment.SPACE_EVENLY, width=380),
            ft.Container(height=10),
        ], horizontal_alignment="center"),
        padding=25, expand=True,
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_center,
            end=ft.alignment.bottom_center,
            colors=[COLOR_BG, "#0F0F12"]
        )
    )

    # --- 2. CHANNELS ---
    lv_chan = ft.ListView(expand=True, spacing=8)
    tf_chan_add = ft.TextField(hint_text="Add Channel...", border_radius=12, bgcolor=COLOR_SURFACE, border_color="transparent", expand=True, content_padding=15)
    tf_chan_search = ft.TextField(hint_text="Search...", prefix_icon=ft.Icons.SEARCH, border_radius=12, bgcolor=COLOR_SURFACE, border_color="transparent", content_padding=10, height=45)
    
    def refresh_channels(filter_text=""):
        data = load_json(USER_ASSETS_FILE)
        lv_chan.controls.clear()
        for u in sorted(data.keys()):
            if filter_text.lower() in u.lower():
                is_enabled = data[u].get("enabled", True)
                cb = ft.Checkbox(value=is_enabled, on_change=lambda e, x=u: toggle_chan(x, e.control.value), fill_color=COLOR_PRIMARY)
                
                lv_chan.controls.append(
                    ft.Container(
                        content=ft.Row([
                            cb,
                            ft.Text(u, expand=True, weight="w500"),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE_ROUNDED, icon_color=COLOR_ERROR, icon_size=20, on_click=lambda e,x=u: del_chan(x))
                        ]),
                        bgcolor=COLOR_SURFACE, padding=ft.padding.symmetric(horizontal=15, vertical=10), border_radius=12,
                        border=ft.border.all(1, COLOR_SURFACE_L)
                    )
                )
        page.update()

    def toggle_chan(x, val):
        data = load_json(USER_ASSETS_FILE)
        if x in data: data[x]['enabled'] = val; save_json(USER_ASSETS_FILE, data)

    def add_chan(e):
        if not tf_chan_add.value: return
        data = load_json(USER_ASSETS_FILE)
        data[tf_chan_add.value] = {"slug": tf_chan_add.value, "enabled": True}
        save_json(USER_ASSETS_FILE, data)
        tf_chan_add.value = ""; refresh_channels(tf_chan_search.value)

    def del_chan(x):
        data = load_json(USER_ASSETS_FILE)
        if x in data: del data[x]; save_json(USER_ASSETS_FILE, data)
        refresh_channels(tf_chan_search.value)

    tf_chan_search.on_change = lambda e: refresh_channels(e.control.value)

    view_channels = ft.Container(
        content=ft.Column([
            ft.Text("Channel Manager", size=24, weight="bold"),
            ft.Container(height=10),
            ft.Row([tf_chan_add, ft.IconButton(ft.Icons.ADD_CIRCLE_ROUNDED, icon_color=COLOR_PRIMARY, icon_size=45, on_click=add_chan)]),
            tf_chan_search,
            lv_chan
        ]), padding=25, expand=True
    )

    # --- 3. FILES ---
    lv_files = ft.ListView(expand=True, spacing=8)
    
    def refresh_files():
        lv_files.controls.clear()
        subs_dir = os.path.join(WORK_DIR, "subscriptions", "xray", "normal")
        if not os.path.exists(subs_dir):
            lv_files.controls.append(ft.Text("No generated files found.", color=COLOR_DIM, italic=True))
        else:
            for f in os.listdir(subs_dir):
                path = os.path.join(subs_dir, f)
                lv_files.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Container(content=ft.Icon(ft.Icons.DESCRIPTION_ROUNDED, color=COLOR_ACCENT), bgcolor=get_opacity_color(COLOR_ACCENT, 0.1), padding=10, border_radius=10),
                            ft.Column([ft.Text(f, weight="bold"), ft.Text(f"{os.path.getsize(path)/1024:.1f} KB", size=11, color=COLOR_DIM)], spacing=2, expand=True),
                            ft.IconButton(ft.Icons.DOWNLOAD_ROUNDED, icon_color=COLOR_TEXT, tooltip="Save", on_click=lambda e,p=path: trigger_export(p))
                        ]),
                        bgcolor=COLOR_SURFACE, padding=12, border_radius=12, border=ft.border.all(1, COLOR_SURFACE_L)
                    )
                )
        page.update()

    view_files = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Text("Output Files", size=24, weight="bold", expand=True),
                ft.IconButton(ft.Icons.REFRESH_ROUNDED, on_click=lambda e: refresh_files())
            ]),
            ft.Text("Tap download icon to save to device storage.", size=12, color=COLOR_DIM),
            ft.Container(height=10),
            lv_files
        ]), padding=25, expand=True
    )

    # --- 4. SETTINGS ---
    tf_brand = ft.TextField(label="Branding Name", border_radius=10, bgcolor=COLOR_SURFACE, border_color=COLOR_SURFACE_L)
    tf_max = ft.TextField(label="Max per Channel", keyboard_type="number", border_radius=10, bgcolor=COLOR_SURFACE, border_color=COLOR_SURFACE_L)
    sw_conv = ft.Switch(label="Enable Converters", active_color=COLOR_PRIMARY)

    def load_settings_ui():
        d = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
        tf_brand.value = d['branding_name']
        tf_max.value = str(d['max_per_channel'])
        sw_conv.value = d['enable_converters']
        page.update()

    def save_settings_ui(e):
        d = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
        d['branding_name'] = tf_brand.value
        d['max_per_channel'] = int(tf_max.value)
        d['enable_converters'] = sw_conv.value
        save_json(SETTINGS_FILE, d)
        page.open(ft.SnackBar(ft.Text("Settings Saved"), bgcolor=COLOR_SUCCESS))

    view_settings = ft.Container(
        content=ft.Column([
            ft.Text("Configuration", size=24, weight="bold"),
            ft.Container(height=20),
            tf_brand,
            tf_max,
            ft.Container(content=sw_conv, bgcolor=COLOR_SURFACE, padding=15, border_radius=12, border=ft.border.all(1, COLOR_SURFACE_L)),
            ft.Container(expand=True),
            ft.ElevatedButton("Save Changes", on_click=save_settings_ui, bgcolor=COLOR_PRIMARY, color="white", height=50, width=400)
        ]), padding=25, expand=True
    )

    # ==========================
    # 🧭 NAVIGATION
    # ==========================
    
    def on_nav_change(e):
        idx = e.control.selected_index
        main_container = page.controls[0].controls[0]
        
        if idx == 0: main_container.content = view_dashboard
        elif idx == 1: refresh_channels(); main_container.content = view_channels
        elif idx == 2: refresh_files(); main_container.content = view_files
        elif idx == 3: load_settings_ui(); main_container.content = view_settings
        page.update()

    # FIX: Use NavigationBarDestination
    nav_bar = ft.NavigationBar(
        selected_index=0,
        on_change=on_nav_change,
        bgcolor="#0F0F12",
        indicator_color=COLOR_PRIMARY,
        surface_tint_color=COLOR_BG,
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.DASHBOARD_OUTLINED, selected_icon=ft.Icons.DASHBOARD_ROUNDED, label="Home"),
            ft.NavigationBarDestination(icon=ft.Icons.LIST_ALT_OUTLINED, selected_icon=ft.Icons.LIST_ALT_ROUNDED, label="Channels"),
            ft.NavigationBarDestination(icon=ft.Icons.FOLDER_OUTLINED, selected_icon=ft.Icons.FOLDER_ROUNDED, label="Files"),
            ft.NavigationBarDestination(icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS_ROUNDED, label="Config"),
        ],
        border=ft.Border(top=ft.BorderSide(1, COLOR_SURFACE_L))
    )

    page.add(ft.Column([
        ft.Container(content=view_dashboard, expand=True), 
        nav_bar
    ], expand=True, spacing=0))
    
    load_stats()

if __name__ == "__main__":
    ft.app(target=main)