import flet as ft
import sys
import threading
import json
import os
import shutil
import zipfile
import stat
import requests
import platform
import pathlib
import time
import proxy_processor

# ==========================================
# ⚙️ CONFIGURATION & PATHS
# ==========================================

INTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))

def get_work_dir():
    # 1. Try to get Flet's official storage path (Android/iOS)
    flet_storage = os.getenv("FLET_APP_STORAGE_DATA")
    
    if flet_storage:
        try:
            # CRITICAL: Resolve symlinks for Android 16 (fix execution permission errors)
            # Converts /data/user/0/... -> /data_mirror/data_ce/...
            real_path = str(pathlib.Path(flet_storage).resolve())
            
            # Use a subfolder to keep files organized
            final_path = os.path.join(real_path, "psg_files")
            
            if not os.path.exists(final_path):
                os.makedirs(final_path, 0o777, exist_ok=True)
                
            return final_path
        except Exception as e:
            print(f"Path Resolution Error: {e}")
            return flet_storage

    # 2. PC Frozen (Exe)
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    
    # 3. PC Script
    return os.getcwd()

WORK_DIR = get_work_dir()
USER_ASSETS_FILE = os.path.join(WORK_DIR, "channelsData", "channelsAssets.json")
BUNDLED_ASSETS_FILE = os.path.join(INTERNAL_DIR, "channelsData", "channelsAssets.json")
SETTINGS_FILE = os.path.join(WORK_DIR, "settings.json")
SUMMARY_FILE = os.path.join(WORK_DIR, "subscriptions", "summary.json")

# Binary Name Logic
XRAY_KNIFE_EXE = "xray-knife.exe" if sys.platform == "win32" else "xray-knife"
XRAY_KNIFE_PATH = os.path.join(WORK_DIR, XRAY_KNIFE_EXE)

# --- THEME COLORS ---
COLOR_BG_TOP = "#050505"
COLOR_BG_BOT = "#121214"
COLOR_SURFACE = "#18181B" 
COLOR_CARD = "#1E1E22"
COLOR_BORDER = "#2A2A30"
COLOR_PRIMARY = "#7C3AED"
COLOR_ACCENT = "#06B6D4"
COLOR_ERROR = "#EF4444"
COLOR_TEXT_DIM = "#A1A1AA"

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

    def write(self, message):
        if message.strip():
            self.log_control.controls.append(
                ft.Container(
                    content=ft.Text(f"> {message.strip()}", font_family="monospace", size=11, color=COLOR_TEXT_DIM),
                    padding=ft.padding.only(left=8),
                    border=ft.Border(left=ft.BorderSide(2, COLOR_PRIMARY))
                )
            )
            try:
                if self.log_control.page:
                    self.log_control.update()
                    self.log_control.scroll_to(offset=-1, duration=200)
            except: pass

    def flush(self): pass

def load_json(p):
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {}
    return {}

def save_json(p, data):
    try:
        with open(p, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
    except: pass

def main(page: ft.Page):
    # --- Setup ---
    page.title = "PSG Station"
    page.bgcolor = COLOR_BG_TOP
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    
    if page.platform in [ft.PagePlatform.WINDOWS, ft.PagePlatform.LINUX, ft.PagePlatform.MACOS]:
        page.window.width = 450
        page.window.height = 800
        page.window.resizable = True
        page.window.min_width = 350
    
    # Init Files
    if not os.path.exists(SETTINGS_FILE):
        src = os.path.join(INTERNAL_DIR, "settings.json")
        if os.path.exists(src): shutil.copy(src, SETTINGS_FILE)

    if not os.path.exists(USER_ASSETS_FILE):
        os.makedirs(os.path.dirname(USER_ASSETS_FILE), exist_ok=True)
        if os.path.exists(BUNDLED_ASSETS_FILE): shutil.copy(BUNDLED_ASSETS_FILE, USER_ASSETS_FILE)

    # --- UI Helpers ---
    def ModernCard(content, padding=15):
        return ft.Container(
            content=content,
            bgcolor=COLOR_CARD,
            border_radius=16,
            padding=padding,
            border=ft.border.all(1, COLOR_BORDER),
            shadow=ft.BoxShadow(spread_radius=0, blur_radius=10, color=ft.Colors.with_opacity(0.1, "black"), offset=ft.Offset(0, 4))
        )

    # ==========================
    # 🧩 CONTROLS INITIALIZATION
    # ==========================
    
    # 1. LOGS
    log_lv = ft.ListView(expand=True, spacing=5, auto_scroll=True)
    logger = Logger(log_lv)
    
    bs_logs = ft.BottomSheet(
        ft.Container(
            ft.Column([
                ft.Container(width=40, height=4, bgcolor=COLOR_BORDER, border_radius=2, margin=ft.margin.only(bottom=10)),
                ft.Text("Live Logs", weight="bold", size=16),
                ft.Container(log_lv, height=300, bgcolor="#000000", border_radius=12, padding=10, border=ft.border.all(1, COLOR_BORDER)),
            ], horizontal_alignment="center", tight=True),
            padding=20, bgcolor=COLOR_CARD, border_radius=ft.border_radius.vertical(top=20)
        )
    )

    # 2. STATUS & STATS
    status_ring = ft.ProgressRing(width=140, height=140, stroke_width=8, color=COLOR_PRIMARY, value=0, bgcolor=COLOR_BORDER)
    status_text = ft.Text("System Ready", size=22, weight="bold", color="white")
    status_sub = ft.Text("Waiting for command...", size=13, color=COLOR_TEXT_DIM)
    
    st_raw = ft.Text("-", size=24, weight="bold")
    st_loc = ft.Text("-", size=24, weight="bold")
    st_src = ft.Text("-", size=24, weight="bold")

    # 3. CHANNELS LIST
    chan_lv = ft.ListView(expand=True, spacing=8)
    tf_add = ft.TextField(hint_text="Add Channel", expand=True, bgcolor=COLOR_CARD, border_color="transparent", height=45, border_radius=10, content_padding=10)
    tf_search = ft.TextField(hint_text="Search...", prefix_icon=ft.Icons.SEARCH, bgcolor=COLOR_CARD, border_color="transparent", border_radius=10, height=40, content_padding=10)
    txt_chan_count = ft.Text("0 channels", size=12, color=COLOR_TEXT_DIM)

    # 4. FILES LIST
    file_lv = ft.ListView(expand=True, spacing=8)
    file_picker = ft.FilePicker(on_result=lambda e: shutil.copy(page.session_data, e.path) if e.path else None)
    page.overlay.append(file_picker)

    # 5. SETTINGS INPUTS
    tf_brand = ft.TextField(label="Branding", bgcolor=COLOR_CARD, border_color=COLOR_BORDER, border_radius=10)
    tf_max = ft.TextField(label="Max/Channel", keyboard_type="number", bgcolor=COLOR_CARD, border_color=COLOR_BORDER, border_radius=10)
    tf_fake = ft.TextField(label="Fake Configs", bgcolor=COLOR_CARD, border_color=COLOR_BORDER, border_radius=10)
    sw_conv = ft.Switch(label="Enable Converters", active_color=COLOR_PRIMARY)

    # ==========================
    # ⚙️ LOGIC FUNCTIONS
    # ==========================

    def update_stats():
        try:
            # 1. Manual File Count (Fixes "40" bug)
            mix_file = os.path.join(WORK_DIR, "subscriptions", "xray", "normal", "mix")
            actual_count = 0
            if os.path.exists(mix_file):
                try:
                    with open(mix_file, 'r', encoding='utf-8') as f:
                        # Count lines with config protocols
                        actual_count = sum(1 for line in f if "://" in line)
                except: pass

            # 2. Load JSON and Sync
            d = load_json(SUMMARY_FILE)
            if "configs" not in d: d["configs"] = {}
            if "sources" not in d: d["sources"] = {"valid": 0}
            if "outputs" not in d: d["outputs"] = {"country_distribution": {}}
            
            # Force update JSON
            d["configs"]["total_raw"] = actual_count
            save_json(SUMMARY_FILE, d)

            # 3. Update UI
            st_raw.value = str(actual_count)
            st_src.value = str(d["sources"].get("valid", "0"))
            countries = d["outputs"].get("country_distribution", {})
            st_loc.value = str(len(countries))
            
        except Exception as e:
            logger.write(f"Stats Error: {e}")
        
        page.update()

    def finish_ui(success=True):
        status_ring.value = 0
        status_text.value = "Completed" if success else "Stopped"
        status_sub.value = "Check Output Files" if success else "Process Aborted"
        
        # Reset Button State
        btn_content.text = "START"
        btn_content.icon = ft.Icons.PLAY_ARROW_ROUNDED
        btn_content.style.bgcolor = COLOR_PRIMARY
        btn_content.disabled = False
        
        if success:
            time.sleep(0.5) # Wait for IO
            update_stats()
        page.update()

    def process_thread():
        try:
            # Use WORK_DIR for the processor
            os.chdir(WORK_DIR) 
            
            proxy_processor.run_stage_1()
            if proxy_processor.ABORT_FLAG:
                finish_ui(False)
                return

            def start_s2(e):
                page.close(dlg_s2)
                status_text.value = "Processing..."
                status_sub.value = "Speedtest & Sorting..."
                page.update()
                threading.Thread(target=stage_2_logic, daemon=True).start()

            dlg_s2 = ft.AlertDialog(
                title=ft.Text("Step 1 Done", weight="bold"),
                content=ft.Text("Disable VPN now for accurate speedtest."),
                actions=[ft.ElevatedButton("Continue", on_click=start_s2, bgcolor=COLOR_PRIMARY, color="white")],
                bgcolor=COLOR_CARD
            )
            page.open(dlg_s2)
        except Exception as e:
            logger.write(f"Error: {e}")
            finish_ui(False)

    def stage_2_logic():
        try:
            s = load_json(SETTINGS_FILE)
            proxy_processor.run_stage_2_5(cb=None, convert=s.get('enable_converters', True))
            finish_ui(True)
        except Exception as e:
            logger.write(f"Error: {e}")
            finish_ui(False)

    # --- Tool Downloader (Android Fixed) ---
    def download_tool(on_done=None):
        try:
            logger.write(f"📂 Storage: {WORK_DIR}")
            
            # 1. Determine Asset
            target_asset = None
            is_android = "ANDROID_ARGUMENT" in os.environ or hasattr(sys, 'getandroidapilevel')
            
            if is_android:
                target_asset = "Xray-knife-android-arm64-v8a.zip" # Force Arm64
            else:
                target_asset, _ = proxy_processor.get_target_asset_name()

            logger.write(f"⬇️ Target: {target_asset}")
            
            # 2. Download (with Headers)
            headers = {"User-Agent": "Mozilla/5.0"} 
            url = "https://api.github.com/repos/lilendian0x00/xray-knife/releases/latest"
            
            resp = requests.get(url, headers=headers, timeout=10)
            assets = resp.json().get("assets", [])
            dl_url = next((x["browser_download_url"] for x in assets if x["name"] == target_asset), None)
            
            if not dl_url:
                logger.write("❌ Asset not found.")
                finish_ui(False)
                return

            zip_path = os.path.join(WORK_DIR, "temp_tool.zip")
            with requests.get(dl_url, stream=True, headers=headers) as r:
                r.raise_for_status()
                with open(zip_path, 'wb') as f: shutil.copyfileobj(r.raw, f)

            # 3. Extract & Move
            with zipfile.ZipFile(zip_path, 'r') as z: z.extractall(WORK_DIR)
            os.remove(zip_path)

            # Find binary recursively and move to root
            found = False
            for root, dirs, files in os.walk(WORK_DIR):
                for file in files:
                    if file in ["xray-knife", "xray-knife.exe"]:
                        full_p = os.path.join(root, file)
                        if full_p != XRAY_KNIFE_PATH:
                            if os.path.exists(XRAY_KNIFE_PATH): os.remove(XRAY_KNIFE_PATH)
                            shutil.move(full_p, XRAY_KNIFE_PATH)
                        found = True
                        break
                if found: break

            # 4. EXECUTION PERMISSIONS (Critical for Android)
            if sys.platform != "win32":
                logger.write("🔑 Setting Permissions...")
                os.chmod(XRAY_KNIFE_PATH, 0o777)

            logger.write("✅ Tool Ready.")
            if on_done: on_done()

        except Exception as e:
            logger.write(f"Download Error: {e}")
            finish_ui(False)

    # --- Actions ---
    def on_action_click(e):
        if btn_content.text in ["START", "Downloading..."]:
            
            # Check Tool
            if not os.path.exists(XRAY_KNIFE_PATH):
                def start_dl(e):
                    page.close(dlg_missing)
                    btn_content.disabled = True
                    btn_content.text = "Downloading..."
                    page.update()
                    
                    def after_dl():
                        btn_content.text = "START" 
                        btn_content.disabled = False
                        on_action_click(None) # Auto-click

                    threading.Thread(target=download_tool, args=(after_dl,), daemon=True).start()

                dlg_missing = ft.AlertDialog(
                    title=ft.Text("Tool Missing"),
                    content=ft.Text("Downloading Xray-Knife core..."),
                    actions=[
                        ft.TextButton("Cancel", on_click=lambda e: page.close(dlg_missing)),
                        ft.ElevatedButton("Download", on_click=start_dl)
                    ], bgcolor=COLOR_SURFACE
                )
                page.open(dlg_missing)
                return

            # Start Process
            btn_content.text = "STOP"
            btn_content.icon = ft.Icons.STOP_ROUNDED
            btn_content.style.bgcolor = COLOR_ERROR
            status_text.value = "Working..."
            status_ring.value = None
            page.update()
            
            sys.stdout = logger
            proxy_processor.reset_globals()
            settings = load_json(SETTINGS_FILE)
            proxy_processor.init_globals({**DEFAULT_SETTINGS, **settings})
            threading.Thread(target=process_thread, daemon=True).start()
        else:
            # Stop Process
            btn_content.disabled = True
            btn_content.text = "STOPPING..."
            page.update()
            proxy_processor.stop_processing()

    # --- Animated Button ---
    # --- Animated Button (Fixed) ---
    def animate_button_click():
        # Just use simple float values for scaling
        btn_container.scale = 0.95
        btn_container.update()
        time.sleep(0.1)
        btn_container.scale = 1.0
        btn_container.update()

    btn_content = ft.ElevatedButton(
        "START", 
        icon=ft.Icons.PLAY_ARROW_ROUNDED, 
        on_click=lambda e: [animate_button_click(), on_action_click(e)],
        style=ft.ButtonStyle(
            bgcolor=COLOR_PRIMARY, 
            color="white", 
            shape=ft.RoundedRectangleBorder(radius=12)
        ),
        height=55, 
        width=220
    )

    btn_container = ft.Container(
        content=btn_content,
        # FIX: Use simple number '1' or 'ft.Scale(1)' instead of 'ft.transform.Scale'
        scale=1, 
        animate_scale=ft.Animation(300, ft.AnimationCurve.BOUNCE_OUT),
    )

    # --- Channel Logic ---
    def refresh_chan(search=""):
        data = load_json(USER_ASSETS_FILE)
        chan_lv.controls.clear()
        keys = sorted(data.keys())
        visible_count = 0
        
        for u in keys:
            if search.lower() in u.lower():
                visible_count += 1
                is_on = data[u].get("enabled", True)
                def toggle(e, name=u):
                    d = load_json(USER_ASSETS_FILE); d[name]['enabled'] = e.control.value
                    save_json(USER_ASSETS_FILE, d)
                
                chan_lv.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Checkbox(value=is_on, on_change=toggle, fill_color=COLOR_PRIMARY),
                            ft.Text(u, expand=True, weight="bold"),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE_ROUNDED, icon_color=COLOR_ERROR, on_click=lambda e, n=u: del_c(n))
                        ]),
                        bgcolor=COLOR_CARD, padding=ft.padding.symmetric(horizontal=10, vertical=5), border_radius=10, border=ft.border.all(1, COLOR_BORDER)
                    )
                )
        txt_chan_count.value = f"{visible_count} channels"
        page.update()

    def toggle_all_channels(state: bool):
        d = load_json(USER_ASSETS_FILE)
        for k in d: d[k]['enabled'] = state
        save_json(USER_ASSETS_FILE, d); refresh_chan(tf_search.value)

    def del_c(name):
        d = load_json(USER_ASSETS_FILE); del d[name]
        save_json(USER_ASSETS_FILE, d); refresh_chan(tf_search.value)

    def add_c(e):
        if not tf_add.value: return
        d = load_json(USER_ASSETS_FILE); d[tf_add.value] = {"slug": tf_add.value, "enabled": True}
        save_json(USER_ASSETS_FILE, d); tf_add.value = ""; refresh_chan(tf_search.value)

    tf_search.on_change = lambda e: refresh_chan(e.control.value)

    # --- File Logic ---
    def refresh_files():
        file_lv.controls.clear()
        path = os.path.join(WORK_DIR, "subscriptions", "xray", "normal")
        if os.path.exists(path):
            for f in os.listdir(path):
                f_path = os.path.join(path, f)
                def save_f(e, p=f_path):
                    page.session_data = p
                    file_picker.save_file(file_name=os.path.basename(p))
                
                file_lv.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.INSERT_DRIVE_FILE_ROUNDED, color=COLOR_ACCENT),
                            ft.Column([ft.Text(f, weight="bold"), ft.Text(f"{os.path.getsize(f_path)//1024} KB", size=11, color=COLOR_TEXT_DIM)], spacing=2, expand=True),
                            ft.IconButton(ft.Icons.DOWNLOAD_ROUNDED, icon_color="white", on_click=save_f)
                        ]),
                        bgcolor=COLOR_CARD, padding=12, border_radius=12, border=ft.border.all(1, COLOR_BORDER)
                    )
                )
        page.update()

    # --- Settings Logic (Client Storage) ---
    def load_settings_ui():
        if page.client_storage.contains_key("branding_name"):
            tf_brand.value = page.client_storage.get("branding_name")
            tf_max.value = str(page.client_storage.get("max_per_channel"))
            tf_fake.value = page.client_storage.get("fake_configs")
            sw_conv.value = page.client_storage.get("enable_converters")
        else:
            d = load_json(SETTINGS_FILE)
            d = {**DEFAULT_SETTINGS, **d}
            tf_brand.value = d['branding_name']
            tf_max.value = str(d['max_per_channel'])
            tf_fake.value = d['fake_configs']
            sw_conv.value = d['enable_converters']
        page.update()

    def save_settings_ui(e):
        d = {
            "branding_name": tf_brand.value,
            "max_per_channel": int(tf_max.value) if tf_max.value.isdigit() else 40,
            "fake_configs": tf_fake.value,
            "enable_converters": sw_conv.value,
            "timeout": 10
        }
        # Save to File (for script)
        save_json(SETTINGS_FILE, d)
        # Save to Storage (for UI persistence)
        page.client_storage.set("branding_name", d["branding_name"])
        page.client_storage.set("max_per_channel", d["max_per_channel"])
        page.client_storage.set("fake_configs", d["fake_configs"])
        page.client_storage.set("enable_converters", d["enable_converters"])
        
        page.open(ft.SnackBar(ft.Text("Settings Saved!"), bgcolor=COLOR_PRIMARY))

    # ==========================
    # 📱 VIEWS
    # ==========================
    
    def stat_item(icon, val, label, col):
        return ft.Container(
            content=ft.Column([ft.Icon(icon, color=col), val, ft.Text(label, size=11, color=COLOR_TEXT_DIM)], horizontal_alignment="center"),
            expand=True
        )

    view_dashboard = ft.Container(
        content=ft.Column([
            ft.Container(height=10),
            ft.Stack([status_ring, ft.Container(ft.Icon(ft.Icons.BOLT_ROUNDED, size=50, color=COLOR_PRIMARY), alignment=ft.alignment.center, width=140, height=140)], width=140, height=140),
            status_text, status_sub,
            ft.Container(height=20),
            ModernCard(ft.Row([
                stat_item(ft.Icons.DATA_USAGE_ROUNDED, st_raw, "Configs", COLOR_PRIMARY),
                ft.VerticalDivider(width=1, color=COLOR_BORDER),
                stat_item(ft.Icons.PUBLIC_ROUNDED, st_loc, "Countries", COLOR_ACCENT),
                ft.VerticalDivider(width=1, color=COLOR_BORDER),
                stat_item(ft.Icons.SOURCE_ROUNDED, st_src, "Sources", COLOR_ERROR),
            ], alignment="center")),
            ft.Container(expand=True),
            ft.Row([
                ft.IconButton(ft.Icons.TERMINAL_ROUNDED, icon_color=COLOR_TEXT_DIM, on_click=lambda e: page.open(bs_logs), tooltip="Logs"),
                btn_container,
                ft.IconButton(ft.Icons.REFRESH_ROUNDED, icon_color=COLOR_TEXT_DIM, on_click=lambda e: update_stats(), tooltip="Refresh"),
            ], alignment=ft.MainAxisAlignment.SPACE_EVENLY),
            ft.Container(height=10),
        ], horizontal_alignment="center"),
        padding=25, expand=True
    )

    view_channels = ft.Container(
        content=ft.Column([
            ft.Text("Channels", size=24, weight="bold"),
            ft.Row([tf_add, ft.IconButton(ft.Icons.ADD_CIRCLE_ROUNDED, icon_color=COLOR_PRIMARY, icon_size=40, on_click=add_c)]),
            tf_search,
            ft.Row([
                txt_chan_count,
                ft.Container(expand=True),
                ft.TextButton("All", on_click=lambda e: toggle_all_channels(True)),
                ft.TextButton("None", on_click=lambda e: toggle_all_channels(False)),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            chan_lv
        ]), padding=20, expand=True
    )

    view_files = ft.Container(
        content=ft.Column([
            ft.Row([ft.Text("Output Files", size=24, weight="bold"), ft.Container(expand=True), ft.IconButton(ft.Icons.REFRESH_ROUNDED, on_click=lambda e: refresh_files())]),
            file_lv
        ]), padding=20, expand=True
    )

    view_settings = ft.Container(
        content=ft.Column([
            ft.Text("Settings", size=24, weight="bold"),
            ft.Container(height=10),
            tf_brand, tf_max, tf_fake,
            ModernCard(sw_conv, padding=10),
            ft.Container(expand=True),
            ft.ElevatedButton("Save Changes", on_click=save_settings_ui, bgcolor=COLOR_PRIMARY, color="white", height=50, width=400)
        ]), padding=20, expand=True
    )

    # --- Nav ---
    content_area = ft.Container(expand=True)
    content_area.content = view_dashboard 

    def nav_change(e):
        idx = e.control.selected_index
        if idx == 0: 
            update_stats()
            content_area.content = view_dashboard
        elif idx == 1: 
            refresh_chan()
            content_area.content = view_channels
        elif idx == 2: 
            refresh_files()
            content_area.content = view_files
        elif idx == 3: 
            load_settings_ui()
            content_area.content = view_settings
        page.update()

    nav_bar = ft.NavigationBar(
        selected_index=0,
        on_change=nav_change,
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.DASHBOARD_OUTLINED, selected_icon=ft.Icons.DASHBOARD_ROUNDED, label="Home"),
            ft.NavigationBarDestination(icon=ft.Icons.LIST_ALT_OUTLINED, selected_icon=ft.Icons.LIST_ALT_ROUNDED, label="Channels"),
            ft.NavigationBarDestination(icon=ft.Icons.FOLDER_OUTLINED, selected_icon=ft.Icons.FOLDER_ROUNDED, label="Files"),
            ft.NavigationBarDestination(icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS_ROUNDED, label="Settings"),
        ],
        bgcolor=COLOR_BG_BOT,
        indicator_color=COLOR_PRIMARY,
        height=70,
        label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW
    )

    layout = ft.SafeArea(
        content=ft.Column([content_area, nav_bar], spacing=0, expand=True),
        expand=True, bottom=True, maintain_bottom_view_padding=True
    )

    main_bg = ft.Container(
        expand=True,
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_center,
            end=ft.alignment.bottom_center,
            colors=[COLOR_BG_TOP, COLOR_BG_BOT]
        ),
        content=layout
    )

    page.add(main_bg)
    update_stats()

if __name__ == "__main__":
    ft.app(target=main)
