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

COLOR_BG = "#121212"
COLOR_CARD = "#1E1E1E"
COLOR_PRIMARY = "#BB86FC"
COLOR_ACCENT = "#03DAC6"
COLOR_ERROR = "#CF6679"
COLOR_TEXT = "#E0E0E0"
COLOR_DIM = "#A0A0A0"

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

class Logger:
    def __init__(self, log_control):
        self.log_control = log_control
        self.terminal = sys.stdout

    def write(self, message):
        try: self.terminal.write(message)
        except: pass
        if message.strip():
            self.log_control.controls.append(
                ft.Text(f"> {message.strip()}", font_family="monospace", size=11, color=COLOR_DIM)
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

    page.title = "PSG Station"
    page.bgcolor = COLOR_BG
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.window.width = 450
    page.window.height = 800
    
    # --- Data Helpers ---
    def load_json(path, default={}):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except: pass
        return default

    def save_json(path, data):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

    # --- Shared State & Controls ---
    log_lv = ft.ListView(expand=True, spacing=2, auto_scroll=False)
    logger = Logger(log_lv)
    
    status_ring = ft.ProgressRing(width=120, height=120, stroke_width=8, color=COLOR_PRIMARY, value=0)
    status_icon = ft.Icon(ft.Icons.POWER_SETTINGS_NEW_ROUNDED, size=50, color=COLOR_PRIMARY)
    status_text = ft.Text("Ready", size=16, weight="bold")

    def stat_card(icon, label, value, color):
        return ft.Container(
            content=ft.Column([
                ft.Icon(icon, color=color, size=24),
                ft.Text(value, size=20, weight="bold"),
                ft.Text(label, size=10, color=COLOR_DIM)
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=COLOR_CARD, border_radius=12, padding=15, expand=True
        )

    st_raw = stat_card(ft.Icons.DATA_USAGE, "Configs", "-", COLOR_PRIMARY)
    st_loc = stat_card(ft.Icons.PUBLIC, "Countries", "-", COLOR_ACCENT)
    st_src = stat_card(ft.Icons.SOURCE, "Sources", "-", COLOR_ERROR)

    # --- File Picker for Export ---
    export_ref = ft.Ref() # Holds the path of the file user wants to save
    
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
        # Automatically suggest the same filename
        fname = os.path.basename(path)
        file_picker.save_file(file_name=fname)

    # --- Background Tasks ---
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
            page.update()
            
            proxy_processor.reset_globals()
            settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
            proxy_processor.init_globals(settings)
            
            sys.stdout = logger
            threading.Thread(target=stage_1_thread, daemon=True).start()

        dlg_vpn = ft.AlertDialog(
            title=ft.Text("Enable VPN"), 
            content=ft.Text("VPN is required to fetch Telegram channels."),
            actions=[ft.ElevatedButton("I'm Connected", on_click=start)]
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
            page.update()
            s = load_json(SETTINGS_FILE)
            threading.Thread(target=stage_2_thread, args=(s.get('enable_converters', True),), daemon=True).start()

        dlg_s2 = ft.AlertDialog(
            title=ft.Text("Fetch Done"), 
            content=ft.Text("Disable VPN now for accurate speedtest?"),
            actions=[ft.TextButton("Continue", on_click=go)]
        )
        page.open(dlg_s2)

    def stage_2_thread(conv):
        try:
            proxy_processor.run_stage_2_5(convert=conv)
            finish_ui(True)
        except: finish_ui(False)

    def finish_ui(success):
        status_ring.value = 0
        status_icon.name = ft.Icons.CHECK_CIRCLE if success else ft.Icons.ERROR
        status_icon.color = "green" if success else "red"
        status_text.value = "Done" if success else "Stopped"
        if success: load_stats()
        page.update()

    def stop_click(e):
        proxy_processor.stop_processing()
        status_text.value = "Stopping..."
        page.update()

    # ==========================
    # 📱 PAGES (VIEWS)
    # ==========================

    # --- 1. DASHBOARD ---
    bs_logs = ft.BottomSheet(
        ft.Container(
            ft.Column([
                ft.Text("System Logs", weight="bold"),
                ft.Container(log_lv, height=300, bgcolor="black", border_radius=8, padding=10),
                ft.Button("Close", on_click=lambda e: page.close(bs_logs))
            ], spacing=10),
            padding=20, bgcolor=COLOR_CARD
        )
    )

    view_dashboard = ft.Container(
        content=ft.Column([
            ft.Container(height=20),
            ft.Stack([
                ft.Container(content=status_ring, alignment=ft.alignment.center),
                ft.Container(content=status_icon, alignment=ft.alignment.center, padding=35),
            ], height=130),
            ft.Container(content=status_text, alignment=ft.alignment.center),
            ft.Container(height=20),
            ft.Row([st_raw, st_loc, st_src], spacing=10),
            ft.Container(height=20),
            ft.Row([
                ft.ElevatedButton("Show Logs", icon=ft.Icons.TERMINAL, on_click=lambda e: page.open(bs_logs),
                                  style=ft.ButtonStyle(bgcolor=COLOR_CARD, color="white", padding=20), expand=True),
                ft.ElevatedButton("Stop", icon=ft.Icons.STOP, on_click=stop_click,
                                  style=ft.ButtonStyle(bgcolor=COLOR_ERROR, color="white", padding=20), expand=True),
            ]),
            ft.Container(expand=True),
            ft.ElevatedButton("START", on_click=run_process, 
                              style=ft.ButtonStyle(bgcolor=COLOR_PRIMARY, color="black", shape=ft.RoundedRectangleBorder(radius=12)),
                              width=200, height=50)
        ], expand=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        padding=20,
        expand=True
    )

    # --- 2. CHANNELS (Fixed: Search & Checkboxes) ---
    lv_chan = ft.ListView(expand=True, spacing=5)
    tf_chan_add = ft.TextField(hint_text="Add (no @)", expand=True, border_color=COLOR_PRIMARY)
    tf_chan_search = ft.TextField(hint_text="Search...", prefix_icon=ft.Icons.SEARCH, expand=True)
    
    def refresh_channels(filter_text=""):
        data = load_json(USER_ASSETS_FILE)
        lv_chan.controls.clear()
        
        sorted_keys = sorted(data.keys())
        
        for u in sorted_keys:
            if filter_text.lower() in u.lower():
                is_enabled = data[u].get("enabled", True)
                
                # Checkbox toggles 'enabled'
                cb = ft.Checkbox(
                    label=u, 
                    value=is_enabled, 
                    on_change=lambda e, x=u: toggle_chan(x, e.control.value)
                )
                
                lv_chan.controls.append(
                    ft.Container(
                        content=ft.Row([
                            cb,
                            ft.Container(expand=True),
                            # Delete entirely
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=COLOR_DIM, on_click=lambda e,x=u: del_chan(x))
                        ]),
                        bgcolor=COLOR_CARD, padding=ft.padding.symmetric(horizontal=10), border_radius=8
                    )
                )
        page.update()

    def toggle_chan(x, val):
        data = load_json(USER_ASSETS_FILE)
        if x in data:
            data[x]['enabled'] = val
            save_json(USER_ASSETS_FILE, data)

    def add_chan(e):
        if not tf_chan_add.value: return
        data = load_json(USER_ASSETS_FILE)
        # Default enabled = True
        data[tf_chan_add.value] = {"slug": tf_chan_add.value, "enabled": True}
        save_json(USER_ASSETS_FILE, data)
        tf_chan_add.value = ""
        refresh_channels(tf_chan_search.value)

    def del_chan(x):
        data = load_json(USER_ASSETS_FILE)
        if x in data: del data[x]
        save_json(USER_ASSETS_FILE, data)
        refresh_channels(tf_chan_search.value)

    # Search Listener
    tf_chan_search.on_change = lambda e: refresh_channels(e.control.value)

    view_channels = ft.Container(
        content=ft.Column([
            ft.Text("Manage Channels", size=20, weight="bold"),
            ft.Row([tf_chan_add, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=COLOR_PRIMARY, icon_size=40, on_click=add_chan)]),
            tf_chan_search,
            lv_chan
        ], expand=True),
        padding=20,
        expand=True
    )

    # --- 3. FILES (Fixed: Export/Save) ---
    lv_files = ft.ListView(expand=True)
    
    def refresh_files():
        lv_files.controls.clear()
        subs_dir = os.path.join(WORK_DIR, "subscriptions", "xray", "normal")
        
        if not os.path.exists(subs_dir):
            lv_files.controls.append(ft.Text("No files generated yet.", color=COLOR_DIM))
        else:
            for f in os.listdir(subs_dir):
                path = os.path.join(subs_dir, f)
                lv_files.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.INSERT_DRIVE_FILE, color=COLOR_PRIMARY),
                            ft.Column([
                                ft.Text(f, weight="bold"),
                                ft.Text(f"{os.path.getsize(path)/1024:.1f} KB", size=10, color=COLOR_DIM)
                            ], expand=True),
                            # Export Button
                            ft.IconButton(ft.Icons.DOWNLOAD_ROUNDED, tooltip="Save to Device", on_click=lambda e,p=path: trigger_export(p))
                        ]),
                        bgcolor=COLOR_CARD, padding=10, border_radius=8
                    )
                )
        lv_files.controls.insert(0, ft.Container(
            content=ft.Text(f"Internal Storage: {WORK_DIR}", size=10, color="grey"),
            padding=10
        ))
        page.update()

    view_files = ft.Container(
        content=ft.Column([
            ft.Text("Output Files", size=20, weight="bold"),
            ft.Text("Click download icon to save files", size=12, color=COLOR_DIM),
            lv_files,
            ft.ElevatedButton("Refresh", on_click=lambda e: refresh_files())
        ], expand=True),
        padding=20,
        expand=True
    )

    # --- 4. SETTINGS ---
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
        page.open(ft.SnackBar(ft.Text("Settings Saved")))

    tf_brand = ft.TextField(label="Branding Name")
    tf_max = ft.TextField(label="Max per Channel", keyboard_type="number")
    sw_conv = ft.Switch(label="Enable Converters (Singbox/Clash)")
    
    view_settings = ft.Container(
        content=ft.Column([
            ft.Text("Configuration", size=20, weight="bold"),
            tf_brand, tf_max, sw_conv,
            ft.ElevatedButton("Save Changes", on_click=save_settings_ui, bgcolor=COLOR_PRIMARY, color="black")
        ], expand=True),
        padding=20,
        expand=True
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

    nav_bar = ft.NavigationBar(
        selected_index=0,
        on_change=on_nav_change,
        bgcolor=COLOR_CARD,
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.DASHBOARD_ROUNDED, label="Home"),
            ft.NavigationBarDestination(icon=ft.Icons.LIST_ALT_ROUNDED, label="Channels"),
            ft.NavigationBarDestination(icon=ft.Icons.FOLDER_ROUNDED, label="Files"),
            ft.NavigationBarDestination(icon=ft.Icons.SETTINGS_ROUNDED, label="Settings"),
        ]
    )

    page.add(ft.Column([
        ft.Container(content=view_dashboard, expand=True), 
        nav_bar
    ], expand=True))
    
    load_stats()

if __name__ == "__main__":
    ft.app(target=main)