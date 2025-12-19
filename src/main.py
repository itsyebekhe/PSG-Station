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
import proxy_processor  # Your backend file

# --- Configuration (MERGED FIX: Persistence + User Data) ---
INTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))

def get_work_dir():
    # 1. Android / Linux / MacOS (Persistent User Data)
    if platform.system() != "Windows":
        return os.path.expanduser("~")
    
    # 2. Windows EXE (Portable - Next to EXE)
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    
    # 3. Windows Script (Current Folder)
    return os.getcwd()

# Set Global Working Directory
WORK_DIR = get_work_dir()

# --- PATH DEFINITIONS ---

# 1. Channels Logic (User Editable vs Bundled)
USER_CHANNELS_DIR = os.path.join(WORK_DIR, "channelsData")
USER_ASSETS_FILE = os.path.join(USER_CHANNELS_DIR, "channelsAssets.json")
BUNDLED_ASSETS_FILE = os.path.join(INTERNAL_DIR, "channelsData", "channelsAssets.json")

# 2. General Settings & Output
SETTINGS_FILE = os.path.join(WORK_DIR, "settings.json")
SUMMARY_FILE = os.path.join(WORK_DIR, "subscriptions", "summary.json")
CONFIG_FILE = os.path.join(WORK_DIR, "config.txt")

# 3. Xray Knife
IS_WINDOWS = sys.platform == "win32"
XRAY_KNIFE_EXE = "xray-knife.exe" if IS_WINDOWS else "xray-knife"
XRAY_KNIFE_PATH = os.path.join(WORK_DIR, XRAY_KNIFE_EXE)

# GitHub Repo Info
REPO_OWNER = "lilendian0x00"
REPO_NAME = "xray-knife"

# --- Theme Constants (2025 Dark Mode) ---
COLOR_BG = "#0F1115"        # Deep Slate
COLOR_SURFACE = "#181B21"   # Card Background
COLOR_PRIMARY = "#6C5CE7"   # Purple
COLOR_SECONDARY = "#00CEC9" # Teal
COLOR_DANGER = "#FF7675"    # Red
COLOR_TEXT = "#DFE6E9"
COLOR_TEXT_DIM = "#636E72"
BORDER_RADIUS = 12

# --- Default Settings ---
DEFAULT_SETTINGS = {
    "branding_name": "PSG",
    "max_per_channel": 40,
    "timeout": 10,
    "enable_converters": True,
    "fake_configs": "#همکاری_ملی,#جاویدشاه,#KingRezaPahlavi"
}

# --- Helpers ---
def get_opacity_color(color_hex, opacity):
    if color_hex.startswith("#"): color_hex = color_hex[1:]
    alpha = int(opacity * 255)
    return f"#{alpha:02x}{color_hex}"

def get_target_asset_name():
    """Determines the correct zip filename for the current OS/Arch."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows": os_str = "windows"
    elif system == "linux": os_str = "linux"
    elif system == "darwin": os_str = "macos"
    else: return None, "Unsupported OS"

    if "aarch64" in machine or "arm64" in machine: arch_str = "arm64-v8a"
    elif "64" in machine: arch_str = "64"
    else: return None, f"Unsupported Architecture: {machine}"

    return f"Xray-knife-{os_str}-{arch_str}.zip", None

class Logger:
    """Redirects stdout to the Flet ListView."""
    def __init__(self, log_control):
        self.log_control = log_control
        self.terminal = sys.stdout

    def write(self, message):
        # 1. Try printing to the real terminal (for debugging)
        # If it fails due to encoding (e.g. Windows Console), fallback to ASCII
        try:
            self.terminal.write(message)
        except UnicodeEncodeError:
            # Replace unknown chars (emojis) with '?' for the console
            try:
                self.terminal.write(message.encode('ascii', 'replace').decode('ascii'))
            except: pass
        except Exception: 
            pass

        # 2. Update the Flet UI (This always supports Emojis/UTF-8)
        if message.strip():
            self.log_control.controls.append(
                ft.Text(f"> {message.strip()}", font_family="Consolas", size=12, color=COLOR_TEXT_DIM)
            )
            self.log_control.update()
            self.log_control.scroll_to(offset=-1, duration=300, curve=ft.AnimationCurve.EASE_OUT)

    def flush(self):
        try:
            self.terminal.flush()
        except: pass

def main(page: ft.Page):
    # 1. Initialize Settings
    default_settings_src = os.path.join(INTERNAL_DIR, "settings.json")
    if not os.path.exists(SETTINGS_FILE) and os.path.exists(default_settings_src):
        try: shutil.copy(default_settings_src, SETTINGS_FILE)
        except: pass

    # 2. Initialize Channels (Extract bundled file to user folder)
    # This enables the "User Editable" feature
    if not os.path.exists(USER_ASSETS_FILE):
        try:
            if not os.path.exists(USER_CHANNELS_DIR):
                os.makedirs(USER_CHANNELS_DIR)
            if os.path.exists(BUNDLED_ASSETS_FILE):
                shutil.copy(BUNDLED_ASSETS_FILE, USER_ASSETS_FILE)
        except: pass

    # --- Page Configuration ---
    page.title = "PSG Station"
    page.bgcolor = COLOR_BG
    page.padding = 0
    # Resized to 1/3 of previous width (850 -> 360)
    page.window.width = 450
    page.window.height = 700
    page.theme_mode = ft.ThemeMode.DARK
    page.window.icon = "icon.png" # Mapped automatically in Flet Build
    
    is_processing = False

    # --- Data Management ---
    def load_settings():
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    return {**DEFAULT_SETTINGS, **json.load(f)}
            except: pass
        return DEFAULT_SETTINGS.copy()

    def save_settings(data):
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    def load_channels():
        # Always try to read the external user file first
        if os.path.exists(USER_ASSETS_FILE):
            try:
                with open(USER_ASSETS_FILE, 'r', encoding='utf-8') as f: 
                    return json.load(f)
            except: pass
        
        # Fallback to bundled if external doesn't exist
        if os.path.exists(BUNDLED_ASSETS_FILE):
            try:
                with open(BUNDLED_ASSETS_FILE, 'r', encoding='utf-8') as f: 
                    return json.load(f)
            except: pass
        return {}

    def save_channels(data):
        # Ensure the directory exists
        if not os.path.exists(USER_CHANNELS_DIR):
            os.makedirs(USER_CHANNELS_DIR)
            
        # Write to the user-editable file
        try:
            with open(USER_ASSETS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as ex:
            print(f"Error saving channels: {ex}")

    # --- Logic: Download Xray Knife ---
    def download_xray_task(on_complete=None):
        try:
            target_file, error = get_target_asset_name()
            if error:
                sys.stdout.write(f"\n[Error] System Detection: {error}\n")
                return

            logger.write(f"Detected System Target: {target_file}")
            logger.write("Fetching latest release info from GitHub...")

            # 1. Get Release Info
            api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
            resp = requests.get(api_url, timeout=10)
            if resp.status_code != 200:
                raise Exception(f"GitHub API Error: {resp.status_code}")
            
            data = resp.json()
            download_url = None
            
            for asset in data.get("assets", []):
                if asset["name"] == target_file:
                    download_url = asset["browser_download_url"]
                    break
            
            if not download_url:
                raise Exception(f"Asset {target_file} not found in latest release.")

            # 2. Download to WORK_DIR
            logger.write(f"Downloading {target_file}...")
            zip_path = os.path.join(WORK_DIR, "xray_temp.zip")
            
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(zip_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            # 3. Extract to WORK_DIR
            logger.write("Extracting archive...")
            extract_folder = os.path.join(WORK_DIR, "xray_temp_ext")
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_folder)

            # 4. Find and Move Binary
            found = False
            for root, dirs, files in os.walk(extract_folder):
                for file in files:
                    if file == XRAY_KNIFE_EXE or (not IS_WINDOWS and file == "xray-knife"):
                        source = os.path.join(root, file)
                        if os.path.exists(XRAY_KNIFE_PATH):
                            os.remove(XRAY_KNIFE_PATH)
                        shutil.move(source, XRAY_KNIFE_PATH)
                        found = True
                        break
                if found: break

            # 5. Cleanup
            if os.path.exists(zip_path): os.remove(zip_path)
            if os.path.exists(extract_folder): shutil.rmtree(extract_folder)

            if not found:
                raise Exception("Binary not found inside the downloaded archive.")

            # 6. Permissions (Linux/Mac)
            if not IS_WINDOWS:
                st = os.stat(XRAY_KNIFE_PATH)
                os.chmod(XRAY_KNIFE_PATH, st.st_mode | stat.S_IEXEC)

            logger.write("\n✅ Xray-Knife installed successfully!")
            
        except Exception as ex:
            logger.write(f"\n[Error] Download Failed: {ex}\n")
        finally:
            if on_complete: on_complete()

    # --- Dialogs & Modals ---

    def open_settings(e):
        current = load_settings()
        
        tf_brand = ft.TextField(label="Branding Name", value=current['branding_name'], border_color=COLOR_PRIMARY)
        tf_max = ft.TextField(label="Max Configs/Channel", value=str(current['max_per_channel']), keyboard_type=ft.KeyboardType.NUMBER)
        tf_fake = ft.TextField(label="Fake Configs (comma separated)", value=current['fake_configs'], multiline=True)
        sw_conv = ft.Switch(label="Enable Converters (Singbox/Clash)", value=current['enable_converters'], active_color=COLOR_PRIMARY)

        def save(e):
            new_s = {
                **current,
                "branding_name": tf_brand.value,
                "max_per_channel": int(tf_max.value) if tf_max.value.isdigit() else 40,
                "fake_configs": tf_fake.value,
                "enable_converters": sw_conv.value
            }
            save_settings(new_s)
            page.close(dlg)
            page.open(ft.SnackBar(ft.Text("Settings Saved!"), bgcolor=COLOR_PRIMARY))

        dlg = ft.AlertDialog(
            title=ft.Text("Settings", weight="bold"),
            content=ft.Column([
                ft.Text("Branding", color=COLOR_SECONDARY, size=12), tf_brand, tf_fake,
                ft.Divider(height=20, color="transparent"),
                ft.Text("Core", color=COLOR_SECONDARY, size=12), tf_max, sw_conv
            ], tight=True, width=300),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.close(dlg)),
                ft.ElevatedButton("Save", on_click=save, bgcolor=COLOR_PRIMARY, color="white")
            ],
            bgcolor=COLOR_SURFACE
        )
        page.open(dlg)

    def open_channel_manager(e):
        channels = load_channels()
        lv_channels = ft.Column(scroll=ft.ScrollMode.AUTO, height=300, spacing=5)
        tf_add = ft.TextField(hint_text="Channel (no @)", expand=True, height=45, content_padding=10, border_radius=8)

        def refresh_list():
            lv_channels.controls.clear()
            for u in channels:
                lv_channels.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.TELEGRAM, size=16, color=COLOR_PRIMARY),
                            ft.Text(f"@{u}", size=14, expand=True, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.IconButton(ft.Icons.DELETE_ROUNDED, icon_color=COLOR_DANGER, icon_size=20, 
                                          on_click=lambda e, x=u: remove_ch(x))
                        ]),
                        padding=ft.padding.symmetric(horizontal=10, vertical=5),
                        bgcolor=get_opacity_color(COLOR_BG, 0.5),
                        border_radius=8
                    )
                )
            page.update()

        def add_ch(e):
            val = tf_add.value.strip().replace("@", "")
            if val and val not in channels:
                # Add new channel structure
                channels[val] = {"slug": val} 
                # SAVE TO FILE
                save_channels(channels)
                
                tf_add.value = ""
                refresh_list()

        def remove_ch(u):
            if u in channels:
                # Remove channel
                del channels[u]
                # SAVE TO FILE
                save_channels(channels)
                
                refresh_list()

        refresh_list()
        
        dlg = ft.AlertDialog(
            title=ft.Text("Manage Channels", weight="bold"),
            content=ft.Container(
                content=ft.Column([
                    ft.Row([tf_add, ft.IconButton(ft.Icons.ADD_CIRCLE_ROUNDED, icon_color=COLOR_SECONDARY, icon_size=30, on_click=add_ch)]),
                    ft.Divider(),
                    lv_channels
                ], tight=True, width=300),
                padding=0
            ),
            actions=[ft.TextButton("Close", on_click=lambda e: page.close(dlg))],
            bgcolor=COLOR_SURFACE
        )
        page.open(dlg)

    def open_tools_dialog(e):
        """Dialog to manually trigger tool download."""
        
        status_text = "Installed" if os.path.exists(XRAY_KNIFE_PATH) else "Missing"
        status_color = COLOR_SECONDARY if os.path.exists(XRAY_KNIFE_PATH) else COLOR_DANGER
        
        def start_download(e):
            page.close(dlg)
            btn_run.disabled = True
            btn_run.text = "Downloading Tools..."
            page.update()
            
            def on_finish():
                btn_run.disabled = False
                btn_run.text = "Run Extraction"
                page.update()
                page.open(ft.SnackBar(ft.Text("Tool Downloaded!"), bgcolor=COLOR_SECONDARY))

            threading.Thread(target=download_xray_task, args=(on_finish,), daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text("Tools Manager", weight="bold"),
            content=ft.Column([
                ft.Row([ft.Text("Xray-Knife Status:"), ft.Text(status_text, color=status_color, weight="bold")]),
                ft.Text("Required for speed testing and filtering configs.", size=12, color=COLOR_TEXT_DIM),
            ], tight=True, width=300),
            actions=[
                ft.TextButton("Close", on_click=lambda e: page.close(dlg)),
                ft.ElevatedButton("Download / Update", on_click=start_download, bgcolor=COLOR_SURFACE)
            ],
            bgcolor=COLOR_SURFACE
        )
        page.open(dlg)

    # --- Main Logic & Threads ---

    def update_progress(val):
        prog_bar.value = val
        page.update()

    def start_process_flow(e):
        if is_processing: return
        
        # Check Tool Existence first
        if not os.path.exists(XRAY_KNIFE_PATH):
            def dl_and_run(e):
                page.close(dlg_missing)
                btn_run.disabled = True
                btn_run.text = "Downloading Tool..."
                page.update()
                
                def proceed():
                    prompt_vpn_check()

                threading.Thread(target=download_xray_task, args=(proceed,), daemon=True).start()

            def skip_and_run(e):
                page.close(dlg_missing)
                prompt_vpn_check()

            dlg_missing = ft.AlertDialog(
                title=ft.Text("⚠️ Tool Missing"),
                content=ft.Text("Xray-Knife is missing. Configs will NOT be tested for speed/validity without it."),
                actions=[
                    ft.TextButton("Skip Speedtest", on_click=skip_and_run),
                    ft.ElevatedButton("Download & Run", on_click=dl_and_run, bgcolor=COLOR_PRIMARY, color="white")
                ],
                bgcolor=COLOR_SURFACE
            )
            page.open(dlg_missing)
            return

        prompt_vpn_check()

    def stop_process(e):
        proxy_processor.stop_processing()
        btn_stop.disabled = True
        btn_stop.text = "Stopping..."
        page.update()

    def prompt_vpn_check():
        def start_stage_1(e):
            page.close(dlg_vpn1)
            
            # Reset Backend Flags
            proxy_processor.reset_globals()

            # UI State for Running
            btn_run.visible = False
            btn_stop.visible = True
            btn_stop.disabled = False
            btn_stop.text = "Stop"
            
            prog_bar.value = None
            page.update()
            
            settings = load_settings()
            proxy_processor.init_globals(settings)
            
            sys.stdout = logger
            threading.Thread(target=run_stage_1_thread, daemon=True).start()

        dlg_vpn1 = ft.AlertDialog(
            title=ft.Text("⚠️ VPN Required"),
            content=ft.Text("Telegram is blocked in your region.\nPlease ENABLE your VPN to fetch channel data."),
            actions=[ft.ElevatedButton("I have Enabled VPN", on_click=start_stage_1, bgcolor=COLOR_PRIMARY, color="white")],
            bgcolor=COLOR_SURFACE
        )
        page.open(dlg_vpn1)

    def run_stage_1_thread():
        try:
            proxy_processor.run_stage_1()
            if proxy_processor.ABORT_FLAG:
                sys.stdout.write("\n⚠️ Process stopped by user.\n")
                reset_ui()
            else:
                show_stage_2_prompt()
        except Exception as ex:
            sys.stdout.write(f"Critical Error Stage 1: {ex}\n")
            reset_ui()

    def show_stage_2_prompt():
        def start_stage_2(e):
            page.close(dlg_vpn2)
            prog_bar.value = 0
            page.update()
            
            settings = load_settings()
            threading.Thread(
                target=run_stage_2_thread, 
                args=(settings['enable_converters'],), 
                daemon=True
            ).start()

        dlg_vpn2 = ft.AlertDialog(
            title=ft.Text("✅ Fetch Complete"),
            content=ft.Text("Data cached successfully.\nYou may now DISABLE your VPN for faster local processing (Optional)."),
            actions=[ft.ElevatedButton("Start Processing", on_click=start_stage_2, bgcolor=COLOR_SECONDARY, color=COLOR_BG)],
            bgcolor=COLOR_SURFACE
        )
        page.open(dlg_vpn2)

    def run_stage_2_thread(do_convert):
        try:
            proxy_processor.run_stage_2_5(cb=update_progress, convert=do_convert)
            if proxy_processor.ABORT_FLAG:
                sys.stdout.write("\n⚠️ Process stopped by user.\n")
            else:
                sys.stdout.write("\nProcess Finished Successfully.\n")
                refresh_stats_ui()
            
        except Exception as ex:
            sys.stdout.write(f"Critical Error Stage 2: {ex}\n")
        finally:
            reset_ui()

    def refresh_stats_ui():
        load_stats()

    def reset_ui():
        btn_run.visible = True
        btn_run.disabled = False
        btn_run.text = "Run Extraction"
        
        btn_stop.visible = False
        
        prog_bar.value = 0
        page.update()

    # --- UI Layout Components ---

    header = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.SECURITY, size=24, color=COLOR_PRIMARY),
            ft.Text("PSG Station", size=20, weight="bold", color=COLOR_TEXT),
            ft.Container(expand=True),
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.CIRCLE, size=8, color="#55E6C1"), ft.Text("Ready", size=10)]),
                bgcolor=COLOR_SURFACE, padding=ft.padding.symmetric(horizontal=8, vertical=4), border_radius=20
            )
        ]),
        padding=15, bgcolor=COLOR_BG
    )

    sidebar = ft.Container(
        content=ft.Column([
            ft.IconButton(ft.Icons.DASHBOARD_ROUNDED, icon_color=COLOR_PRIMARY, tooltip="Dashboard"),
            ft.IconButton(ft.Icons.LIST_ROUNDED, icon_color=COLOR_TEXT_DIM, tooltip="Channels", on_click=open_channel_manager),
            ft.IconButton(ft.Icons.SETTINGS_ROUNDED, icon_color=COLOR_TEXT_DIM, tooltip="Settings", on_click=open_settings),
            ft.IconButton(ft.Icons.BUILD_CIRCLE_ROUNDED, icon_color=COLOR_TEXT_DIM, tooltip="Tools", on_click=open_tools_dialog),
            ft.IconButton(ft.Icons.FOLDER_OPEN_ROUNDED, icon_color=COLOR_TEXT_DIM, tooltip="Open Output", 
                          on_click=lambda e: os.startfile(os.getcwd()) if IS_WINDOWS else None),
        ], spacing=10),
        width=50, bgcolor=COLOR_SURFACE, padding=ft.padding.only(top=20), alignment=ft.alignment.top_center,
        border_radius=ft.border_radius.only(top_right=BORDER_RADIUS, bottom_right=BORDER_RADIUS)
    )

    def make_card(label, icon, color):
        return ft.Container(
            content=ft.Column([
                ft.Container(content=ft.Icon(icon, color=color, size=20), bgcolor=get_opacity_color(color, 0.15), padding=6, border_radius=8),
                ft.Text("-", size=18, weight="bold", color="white"),
                ft.Text(label, size=10, color=COLOR_TEXT_DIM)
            ], spacing=2),
            bgcolor=COLOR_SURFACE, padding=10, border_radius=BORDER_RADIUS, 
            width=100, height=100
        )

    st_configs = make_card("Raw", ft.Icons.DATA_USAGE_ROUNDED, COLOR_PRIMARY)
    st_countries = make_card("Locs", ft.Icons.PUBLIC_ROUNDED, COLOR_SECONDARY)
    st_sources = make_card("Src", ft.Icons.SOURCE_ROUNDED, "#FAB1A0")

    def load_stats():
        if os.path.exists(SUMMARY_FILE):
            try:
                with open(SUMMARY_FILE, 'r', encoding='utf-8') as f: d = json.load(f)
                
                total_raw = d.get('configs', {}).get('total_raw', 0)
                total_countries = len(d.get('outputs', {}).get('country_distribution', {}))
                valid_sources = d.get('sources', {}).get('valid', 0)

                st_configs.content.controls[1].value = str(total_raw)
                st_countries.content.controls[1].value = str(total_countries)
                st_sources.content.controls[1].value = str(valid_sources)
                page.update()
            except Exception as e:
                print(f"Stats Load Error: {e}")

    # Stats Row - Scrollable for narrow view
    stats_row = ft.Row([st_configs, st_countries, st_sources], spacing=10, scroll=ft.ScrollMode.HIDDEN)

    log_list = ft.ListView(expand=True, spacing=2, auto_scroll=False)
    logger = Logger(log_list)
    log_container = ft.Container(
        content=log_list,
        bgcolor="#000000",
        border_radius=BORDER_RADIUS,
        padding=10,
        expand=True,
        border=ft.Border(top=ft.BorderSide(1,"#333"), bottom=ft.BorderSide(1,"#333"), left=ft.BorderSide(1,"#333"), right=ft.BorderSide(1,"#333"))
    )

    prog_bar = ft.ProgressBar(value=0, color=COLOR_PRIMARY, bgcolor=COLOR_SURFACE, height=4)
    
    # --- Action Buttons ---
    btn_run = ft.ElevatedButton(
        "Start", 
        icon=ft.Icons.PLAY_ARROW_ROUNDED, 
        on_click=start_process_flow,
        expand=True,
        style=ft.ButtonStyle(bgcolor=COLOR_PRIMARY, color="white", shape=ft.RoundedRectangleBorder(radius=10), padding=15)
    )

    btn_stop = ft.ElevatedButton(
        "Stop", 
        icon=ft.Icons.STOP_ROUNDED, 
        visible=False,
        on_click=stop_process,
        expand=True,
        style=ft.ButtonStyle(bgcolor=COLOR_DANGER, color="white", shape=ft.RoundedRectangleBorder(radius=10), padding=15)
    )
    
    main_layout = ft.Container(
        content=ft.Column([
            header,
            stats_row,
            ft.Text("Logs", weight="bold", size=12, color=COLOR_TEXT_DIM),
            log_container,
            ft.Column([
                prog_bar,
                ft.Row([btn_stop, btn_run], alignment=ft.MainAxisAlignment.CENTER)
            ], spacing=10)
        ], spacing=10, expand=True),
        padding=15, expand=True
    )

    page.add(ft.Row([sidebar, main_layout], expand=True, spacing=0))
    load_stats()

if __name__ == "__main__":
    ft.app(target=main)