import os
import json
import re
import base64
import time
import socket
import urllib.parse
import ipaddress
import requests
import concurrent.futures
import subprocess
import sys
import shutil
import platform
from datetime import datetime, timezone

try:
    import geoip2.database
    HAS_GEOIP_LIB = True
except ImportError:
    HAS_GEOIP_LIB = False

ABORT_FLAG = False
CURRENT_SUBPROCESS = None

def reset_globals():
    global ABORT_FLAG, CURRENT_SUBPROCESS
    ABORT_FLAG = False
    CURRENT_SUBPROCESS = None

def stop_processing():
    global ABORT_FLAG, CURRENT_SUBPROCESS
    print("\n🛑 Stopping process...")
    ABORT_FLAG = True
    if CURRENT_SUBPROCESS:
        try:
            CURRENT_SUBPROCESS.kill()
        except: pass

# --- Configuration ---
INTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))

def get_work_dir():
    if "ANDROID_ARGUMENT" in os.environ:
        path = os.path.join(os.environ.get("HOME", "/data/data/com.psgstation/files"), "psg_data")
        if not os.path.exists(path): 
            try: os.makedirs(path)
            except: pass
        return path
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()

WORK_DIR = get_work_dir()

ASSETS_FILE = os.path.join(INTERNAL_DIR, "channelsData", "channelsAssets.json")
USER_CHANNELS = os.path.join(WORK_DIR, "channelsData", "channelsAssets.json")
if os.path.exists(USER_CHANNELS):
    ASSETS_FILE = USER_CHANNELS

TEMPLATES_DIR = os.path.join(INTERNAL_DIR, "templates")
MMDB_FILE = os.path.join(INTERNAL_DIR, "channelsData", "GeoLite2-Country.mmdb")

OUTPUT_DIR = os.path.join(WORK_DIR, "subscriptions")
LOCATION_DIR = os.path.join(OUTPUT_DIR, "location")
CHANNEL_SUBS_DIR = os.path.join(OUTPUT_DIR, "channel")
SUBS_XRAY_DIR = os.path.join(OUTPUT_DIR, "xray")
SUMMARY_FILE = os.path.join(OUTPUT_DIR, "summary.json")

HTML_CACHE_DIR = os.path.join(WORK_DIR, "cache_html")
IP_CACHE_FILE = os.path.join(WORK_DIR, "ip_info_cache.json")
CLOUDFLARE_IPS_CACHE = os.path.join(WORK_DIR, "cloudflare_ips.json")

FINAL_CONFIG_FILE = os.path.join(WORK_DIR, "config.txt")
API_DIR = os.path.join(WORK_DIR, "api")
API_OUTPUT_FILE = os.path.join(API_DIR, "allConfigs.json")

class GlobalConfig:
    BRANDING = "PSG"
    MAX_CONFIGS_PER_CHANNEL = 40
    TIMEOUT = 10
    FAKE_CONFIGS = ['#همکاری_ملی', '#جاویدشاه', '#KingRezaPahlavi']
    
    @classmethod
    def update(cls, settings: dict):
        if not settings: return
        cls.BRANDING = settings.get("branding_name", "PSG")
        cls.MAX_CONFIGS_PER_CHANNEL = int(settings.get("max_per_channel", 40))
        cls.TIMEOUT = int(settings.get("timeout", 10))
        fakes = settings.get("fake_configs", [])
        if isinstance(fakes, str):
            cls.FAKE_CONFIGS = [x.strip() for x in fakes.split(",") if x.strip()]
        else:
            cls.FAKE_CONFIGS = fakes

# --- Utilities (Compact) ---
class Utils:
    @staticmethod
    def safe_base64_decode(s: str) -> bytes:
        s = s.strip()
        missing_padding = len(s) % 4
        if missing_padding: s += '=' * (4 - missing_padding)
        return base64.urlsafe_b64decode(s)
    @staticmethod
    def is_ip(s: str) -> bool:
        try:
            ipaddress.ip_address(s)
            return True
        except ValueError: return False
    @staticmethod
    def get_flag_emoji(country_code: str) -> str:
        country_code = country_code.upper()
        if len(country_code) != 2 or country_code == "XX": return '🏳️'
        return chr(ord(country_code[0]) + 127397) + chr(ord(country_code[1]) + 127397)
    @staticmethod
    def detect_type(config: str) -> str | None:
        if config.startswith('vmess://'): return 'vmess'
        if config.startswith('vless://'): return 'vless'
        if config.startswith('trojan://'): return 'trojan'
        if config.startswith('ss://'): return 'ss'
        if config.startswith('tuic://'): return 'tuic'
        if config.startswith('hy2://') or config.startswith('hysteria2://'): return 'hy2'
        return None
    @staticmethod
    def extract_links(text: str) -> list[str]:
        pattern = re.compile(r'(?:vmess|vless|trojan|ss|tuic|hy2|hysteria2)://[^\s"\']*(?=\s|<|>|$)', re.IGNORECASE)
        return pattern.findall(text)
    @staticmethod
    def is_valid_config(config: str) -> bool:
        return "..." not in config and "…" not in config
    @staticmethod
    def get_random_name(length=10) -> str:
        import random, string
        return ''.join(random.choices(string.ascii_lowercase, k=length))
    @staticmethod
    def is_base64(s: str) -> bool:
        try:
            Utils.safe_base64_decode(s)
            return True
        except: return False

class ConfigParser:
    @staticmethod
    def parse(config: str) -> dict | None:
        ctype = Utils.detect_type(config)
        if not ctype: return None
        try:
            if ctype == 'vmess':
                b64 = config[8:]
                data = json.loads(Utils.safe_base64_decode(b64).decode('utf-8'))
                data['protocol'] = 'vmess'
                return data
            elif ctype in ['vless', 'trojan', 'tuic', 'hy2']:
                parsed = urllib.parse.urlparse(config)
                params = urllib.parse.parse_qs(parsed.query)
                params = {k: v[0] for k, v in params.items()}
                return {
                    'protocol': ctype, 'username': parsed.username or '', 'pass': parsed.password or '',
                    'hostname': parsed.hostname or '', 'port': parsed.port or '', 'params': params,
                    'hash': urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"{GlobalConfig.BRANDING}_{Utils.get_random_name()}"
                }
            elif ctype == 'ss':
                parsed = urllib.parse.urlparse(config)
                user_info = urllib.parse.unquote(parsed.username or '')
                if Utils.is_base64(user_info) and ':' not in user_info:
                    try: user_info = Utils.safe_base64_decode(user_info).decode('utf-8')
                    except: pass
                if ':' not in user_info: return None
                method, password = user_info.split(':', 1)
                return {
                    'protocol': 'ss', 'encryption_method': method, 'password': password,
                    'server_address': parsed.hostname or '', 'server_port': parsed.port or '',
                    'name': urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"{GlobalConfig.BRANDING}_{Utils.get_random_name()}"
                }
        except: return None
        return None

    @staticmethod
    def rebuild(data: dict, ctype: str) -> str | None:
        try:
            if ctype == 'vmess':
                clean = data.copy(); clean.pop('protocol', None)
                return f"vmess://{base64.urlsafe_b64encode(json.dumps(clean, separators=(',', ':')).encode('utf-8')).decode('utf-8').rstrip('=')}"
            elif ctype in ['vless', 'trojan', 'tuic', 'hy2']:
                auth = f"{data['username']}:{data['pass']}@" if (data['pass'] and ctype != 'vless') else (f"{data['username']}@" if data['username'] else "")
                return f"{ctype}://{auth}{data['hostname']}{':' + str(data['port']) if data['port'] else ''}?{urllib.parse.urlencode(data['params'])}#{urllib.parse.quote(data['hash'])}"
            elif ctype == 'ss':
                creds = f"{data['encryption_method']}:{data['password']}"
                return f"ss://{base64.urlsafe_b64encode(creds.encode('utf-8')).decode('utf-8').rstrip('=')}@{data['server_address']}:{data['server_port']}#{urllib.parse.quote(data['name'])}"
        except: return None
        return None

# --- Proxy Converter ---
class ProxyConverter:
    @staticmethod
    def to_singbox(data: dict) -> dict | None:
        ctype = data.get('protocol')
        if not ctype: return None
        def get_tls(d, is_reality=False):
            sni = d['params'].get('sni') or d['hostname']
            tls = {"enabled": True, "server_name": sni, "insecure": True, "utls": {"enabled": True, "fingerprint": d['params'].get('fp', 'chrome')}}
            if is_reality: tls['reality'] = {"enabled": True, "public_key": d['params'].get('pbk', ''), "short_id": d['params'].get('sid', '')}
            return tls
        def get_transport(d):
            ttype = d['params'].get('type', 'tcp')
            if ttype == 'ws': return {"type": "ws", "path": d['params'].get('path', '/'), "headers": {"Host": d['params'].get('host', d['hostname'])}}
            elif ttype == 'grpc': return {"type": "grpc", "service_name": d['params'].get('serviceName', '')}
            elif ttype == 'http': return {"type": "http", "host": [d['params'].get('host', d['hostname'])], "path": d['params'].get('path', '/')}
            return None
        if ctype == 'vmess':
            out = {"tag": data.get('ps', 'VMess'), "type": "vmess", "server": data.get('add'), "server_port": int(data.get('port')), "uuid": data.get('id'), "security": "auto", "alter_id": int(data.get('aid', 0))}
            if data.get('port') == 443 or data.get('tls') == 'tls': out['tls'] = {"enabled": True, "server_name": data.get('sni') or data.get('host') or data.get('add'), "insecure": True, "utls": {"enabled": True, "fingerprint": "chrome"}}
            net = data.get('net')
            if net == 'ws': out['transport'] = {"type": "ws", "path": data.get('path', '/'), "headers": {"Host": data.get('host') or data.get('add')}}
            elif net == 'grpc': out['transport'] = {"type": "grpc", "service_name": data.get('path', '')}
            return out
        elif ctype == 'vless':
            out = {"tag": data['hash'], "type": "vless", "server": data['hostname'], "server_port": int(data['port']), "uuid": data['username'], "packet_encoding": "xudp"}
            if data['params'].get('flow'): out['flow'] = "xtls-rprx-vision"
            security = data['params'].get('security')
            if int(data['port']) == 443 or security in ['tls', 'reality']:
                is_reality = (security == 'reality')
                out['tls'] = get_tls(data, is_reality)
                if is_reality and not out['tls']['reality']['public_key']: return None
                if is_reality or data['params'].get('pbk'): out['flow'] = "xtls-rprx-vision"
            trans = get_transport(data)
            if trans: out['transport'] = trans
            return out
        elif ctype == 'trojan':
            out = {"tag": data['hash'], "type": "trojan", "server": data['hostname'], "server_port": int(data['port']), "password": data['username']}
            if int(data['port']) == 443 or data['params'].get('security') == 'tls': out['tls'] = get_tls(data)
            trans = get_transport(data)
            if trans: out['transport'] = trans
            return out
        elif ctype == 'ss':
            allowed = ["chacha20-ietf-poly1305", "aes-256-gcm", "2022-blake3-aes-256-gcm"]
            if data['encryption_method'] not in allowed: return None
            return {"tag": data['name'], "type": "shadowsocks", "server": data['server_address'], "server_port": int(data['server_port']), "method": data['encryption_method'], "password": data['password']}
        elif ctype == 'tuic':
            return {"tag": data['hash'], "type": "tuic", "server": data['hostname'], "server_port": int(data['port']), "uuid": data['username'], "password": data['pass'], "congestion_control": data['params'].get("congestion_control", "bbr"), "udp_relay_mode": data['params'].get("udp_relay_mode", "native"), "tls": {"enabled": True, "server_name": data['params'].get('sni', data['hostname']), "insecure": bool(data['params'].get("allow_insecure", 0)), "alpn": data['params'].get('alpn', '').split(',') if data['params'].get('alpn') else None}}
        return None

    @staticmethod
    def to_clash(data: dict) -> dict | None:
        ctype = data.get('protocol')
        if not ctype: return None
        if ctype == 'vmess':
            out = {"name": data.get('ps'), "type": "vmess", "server": data.get('add'), "port": int(data.get('port')), "uuid": data.get('id'), "alterId": int(data.get('aid', 0)), "cipher": data.get('scy', 'auto'), "tls": True if (data.get('tls') == 'tls') else False, "skip-cert-verify": True, "network": data.get('net', 'tcp')}
            if out['network'] == 'ws': out['ws-opts'] = {"path": data.get('path', '/'), "headers": {"Host": data.get('host') or data.get('add')}}
            elif out['network'] == 'grpc': out['grpc-opts'] = {"grpc-service-name": data.get('path', ''), "grpc-mode": "gun"}; out['tls'] = True
            return out
        elif ctype == 'vless':
            security = data['params'].get('security', '')
            out = {"name": data['hash'], "type": "vless", "server": data['hostname'], "port": int(data['port']), "uuid": data['username'], "tls": True if security in ['tls', 'reality'] else False, "network": data['params'].get('type', 'tcp'), "client-fingerprint": "chrome", "udp": True, "skip-cert-verify": True}
            if data['params'].get('sni'): out['servername'] = data['params'].get('sni')
            if data['params'].get('flow'): out['flow'] = 'xtls-rprx-vision'
            if out['network'] == 'ws': out['ws-opts'] = {"path": data['params'].get('path', '/'), "headers": {"Host": data['params'].get('host', data['hostname'])}}
            elif out['network'] == 'grpc': out['grpc-opts'] = {"grpc-service-name": data['params'].get('serviceName', '')}
            if security == 'reality': out['client-fingerprint'] = data['params'].get('fp', 'chrome'); out['reality-opts'] = {"public-key": data['params'].get('pbk'), "short-id": data['params'].get('sid', '')}
            return out
        elif ctype == 'trojan': return {"name": data['hash'], "type": "trojan", "server": data['hostname'], "port": int(data['port']), "password": data['username'], "skip-cert-verify": True, "sni": data['params'].get('sni', data['hostname'])}
        elif ctype == 'ss': return {"name": data['name'], "type": "ss", "server": data['server_address'], "port": int(data['server_port']), "cipher": data['encryption_method'], "password": data['password']}
        return None

    @staticmethod
    def to_surfboard(data: dict) -> str | None:
        ctype = data.get('protocol')
        if not ctype or ctype == 'vless': return None
        if ctype == 'vmess':
            parts = [f"{data.get('ps', 'vmess').replace(',', ' ')} = vmess", data.get('add'), str(data.get('port')), f"username={data.get('id')}", f"ws={'true' if data.get('net') == 'ws' else 'false'}", f"tls={'true' if data.get('tls') == 'tls' else 'false'}"]
            if data.get('net') == 'ws': parts.append(f"ws-path={data.get('path', '/')}"); parts.append(f"ws-headers=Host:\"{data.get('host') or data.get('add')}\"")
            return ", ".join(parts)
        elif ctype == 'trojan':
            parts = [f"{data['hash'].replace(',', ' ')} = trojan", data['hostname'], str(data['port']), f"password={data['username']}", "skip-cert-verify=true"]
            if data['params'].get('sni'): parts.append(f"sni={data['params'].get('sni')}")
            return ", ".join(parts)
        elif ctype == 'ss':
            if '2022' in data['encryption_method']: return None
            return f"{data['name'].replace(',', ' ')} = ss, {data['server_address']}, {str(data['server_port'])}, encrypt-method={data['encryption_method']}, password={data['password']}"
        return None

# --- GeoIP ---
class GeoIP:
    def __init__(self):
        self.cache = {}
        if os.path.exists(IP_CACHE_FILE):
            try:
                with open(IP_CACHE_FILE, 'r', encoding='utf-8') as f: self.cache = json.load(f)
            except: self.cache = {}
        self.reader = None
        if HAS_GEOIP_LIB and os.path.exists(MMDB_FILE):
            try: self.reader = geoip2.database.Reader(MMDB_FILE)
            except: pass

    def get_country(self, host: str) -> str:
        if host in self.cache: return self.cache[host]
        try: ip = socket.gethostbyname(host)
        except: self.cache[host] = 'XX'; return 'XX'
        
        # Simple Cloudflare check
        if ip.startswith("104.") or ip.startswith("172."):
             self.cache[host] = 'CF'; return 'CF'

        if self.reader:
            try:
                cc = self.reader.country(ip).country.iso_code
                if cc: self.cache[host] = cc; return cc
            except: pass
        
        # Fallback API (Slow, use sparingly)
        try:
            resp = requests.get(f"http://ip-api.com/json/{ip}", timeout=2).json()
            cc = resp.get('countryCode')
            if cc: self.cache[host] = cc; return cc
        except: pass
        
        self.cache[host] = 'XX'; return 'XX'

    def save_cache(self):
        with open(IP_CACHE_FILE, 'w', encoding='utf-8') as f: json.dump(self.cache, f, indent=2)

# ============================
#       PROCESSING STAGES
# ============================

class Stage1_Fetcher:
    def fetch_url(self, item) -> tuple[str, str, bool]:
        if ABORT_FLAG: return item[0], "", False
        source_name, source_data = item
        if not source_data.get("enabled", True): return source_name, "", False
        
        url = source_data.get("subscription_url") or f"https://t.me/s/{source_name}"
        try:
            resp = requests.get(url, timeout=GlobalConfig.TIMEOUT)
            if resp.status_code == 200: return source_name, resp.text, True
        except: pass
        return source_name, "", False

    def run(self):
        print("--- STAGE 1: FETCHER ---")
        if not os.path.exists(ASSETS_FILE): return
        with open(ASSETS_FILE, 'r', encoding='utf-8') as f: assets = json.load(f)
        if not os.path.exists(HTML_CACHE_DIR): os.makedirs(HTML_CACHE_DIR)
        
        # Threaded Fetch
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(self.fetch_url, list(assets.items())))
        
        count = 0
        for src, txt, ok in results:
            if ok:
                with open(os.path.join(HTML_CACHE_DIR, f"{src}.html"), 'w', encoding='utf-8') as f: f.write(txt)
                count += 1
        print(f"Fetched {count} sources.")

class Stage2_Extractor:
    def __init__(self):
        self.geoip = GeoIP()
        self.stats = {'total_raw': 0}
    
    def run(self, progress_callback=None):
        print("\n--- STAGE 2: EXTRACTOR ---")
        if not os.path.exists(ASSETS_FILE): return
        with open(ASSETS_FILE, 'r', encoding='utf-8') as f: assets = json.load(f)
        
        # 1. Gather Links
        raw_configs = []
        sources = [s for s, d in assets.items() if d.get("enabled", True)]
        total = len(sources)
        
        for idx, src in enumerate(sources):
            if ABORT_FLAG: return
            # UPDATE PROGRESS LESS FREQUENTLY TO PREVENT UI FREEZE
            if progress_callback and idx % 5 == 0: 
                progress_callback(idx / total)
                
            fpath = os.path.join(HTML_CACHE_DIR, f"{src}.html")
            if os.path.exists(fpath):
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    links = Utils.extract_links(f.read())
                    # Limit configs per channel
                    links = links[-GlobalConfig.MAX_CONFIGS_PER_CHANNEL:]
                    for l in links: raw_configs.append((src, l))

        # 2. Process & Enrich
        print(f"Processing {len(raw_configs)} raw configs...")
        processed = []
        
        for i, (src, conf) in enumerate(raw_configs):
            if ABORT_FLAG: return
            
            # Parse
            data = ConfigParser.parse(conf)
            if not data: continue
            
            # Enrich
            ctype = data['protocol']
            fields = {"vmess": "add", "vless": "hostname", "trojan": "hostname", "tuic": "hostname", "hy2": "hostname", "ss": "server_address"}
            host = data.get(fields.get(ctype, ""))
            if not host: continue
            
            cc = self.geoip.get_country(host)
            flag = Utils.get_flag_emoji(cc)
            name_field = 'ps' if ctype == 'vmess' else ('name' if ctype == 'ss' else 'hash')
            
            # Rename
            data[name_field] = f"{flag} {cc} | {ctype.upper()} | @{src}"
            
            # Rebuild
            final = ConfigParser.rebuild(data, ctype)
            if final: processed.append(final)

        self.geoip.save_cache()
        self.stats['total_raw'] = len(processed)
        
        # Save Raw List
        with open(FINAL_CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.write("\n".join(processed))
            
        # Clean previous output
        if os.path.exists(OUTPUT_DIR):
            try: shutil.rmtree(OUTPUT_DIR)
            except: pass
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Write Summary
        with open(SUMMARY_FILE, 'w', encoding='utf-8') as f: 
            json.dump({"configs": self.stats, "sources": {"valid": len(sources)}, "outputs": {"country_distribution": {}}}, f)

class Stage3_Deduplicator:
    def run(self):
        print("\n--- STAGE 3: DEDUPLICATION ---")
        if not os.path.exists(FINAL_CONFIG_FILE): return
        with open(FINAL_CONFIG_FILE, 'r', encoding='utf-8') as f:
            unique = list(set([l.strip() for l in f if l.strip()]))
        
        with open(FINAL_CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.write("\n".join(unique))
        print(f"Unique configs: {len(unique)}")

class Stage3_5_Speedtest:
    def run(self, progress_callback=None):
        print("\n--- STAGE 3.5: SPEEDTEST ---")
        if ABORT_FLAG: return None
        
        # CHECK EXECUTABLE
        is_windows = sys.platform == "win32"
        exe_name = "xray-knife.exe" if is_windows else "./xray-knife"
        exe_path = os.path.join(WORK_DIR, exe_name)
        if not os.path.exists(exe_path) and not is_windows:
             exe_path = os.path.join(WORK_DIR, "xray-knife") # Try without ./

        # ANDROID EXECUTION CHECK
        if "ANDROID_ARGUMENT" in os.environ:
            print("⚠️ Android OS restricts executing binaries in user data.")
            print("⚠️ Skipping Speedtest. Using RAW configs.")
            return None # Fallback to raw list

        if not os.path.exists(exe_path):
            print("⚠️ Xray-Knife not found. Skipping.")
            return None

        if progress_callback: progress_callback(None)
        
        valid_out = os.path.join(WORK_DIR, "valid.txt")
        if os.path.exists(valid_out): os.remove(valid_out)
        
        cmd = [exe_path, "http", "-f", FINAL_CONFIG_FILE, "-o", valid_out, "-d", str(GlobalConfig.TIMEOUT*1000), "--thread", "20"]
        
        try:
            global CURRENT_SUBPROCESS
            CURRENT_SUBPROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW if is_windows else 0)
            
            while True:
                line = CURRENT_SUBPROCESS.stdout.readline()
                if not line and CURRENT_SUBPROCESS.poll() is not None: break
                if line: sys.stdout.write(line)
            
            CURRENT_SUBPROCESS = None
            
            if ABORT_FLAG: return None
            
            if os.path.exists(valid_out) and os.path.getsize(valid_out) > 0:
                with open(valid_out, 'r', encoding='utf-8') as f: lines = [l.strip() for l in f if l.strip()]
                print(f"✅ Active Configs: {len(lines)}")
                
                # OVERWRITE MAIN FILE WITH VALID ONES
                with open(FINAL_CONFIG_FILE, 'w', encoding='utf-8') as f: f.write("\n".join(lines))
                return lines
            else:
                print("⚠️ No valid configs found. Using original list.")
                return None
        except Exception as e:
            print(f"Speedtest Error: {e}")
            return None

class Stage4_Sorter:
    def run(self, config_list=None):
        print("\n--- STAGE 4: SORTING ---")
        if ABORT_FLAG: return
        
        # 1. Clean Output Dirs
        for d in [LOCATION_DIR, CHANNEL_SUBS_DIR, SUBS_XRAY_DIR]:
            if os.path.exists(d): shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
            os.makedirs(os.path.join(d, "normal"), exist_ok=True)
            os.makedirs(os.path.join(d, "base64"), exist_ok=True)

        # 2. Get Configs (Prefer Filtered List)
        lines = []
        if config_list: lines = config_list
        elif os.path.exists(FINAL_CONFIG_FILE):
            with open(FINAL_CONFIG_FILE, 'r', encoding='utf-8') as f: lines = [l.strip() for l in f if l.strip()]
        
        if not lines: return

        # 3. Sort
        sorted_data = {}
        for c in lines:
            ctype = Utils.detect_type(c)
            if not ctype: continue
            sorted_data.setdefault(ctype, []).append(c)
        
        fakes = [f"vless://000@127.0.0.1:443?type=ws&path=/#{urllib.parse.quote(n)}" for n in GlobalConfig.FAKE_CONFIGS]
        
        # 4. Write Files
        # A. MIX File (Everything)
        self.write_sub("mix", fakes + lines)
        
        # B. Protocol Files
        for p, confs in sorted_data.items():
            self.write_sub(p, fakes + confs)

    def write_sub(self, name, configs):
        plain = "\n".join(configs)
        b64 = base64.b64encode(plain.encode()).decode()
        
        # Write to SUBS_XRAY_DIR (which main.py looks at)
        p_norm = os.path.join(SUBS_XRAY_DIR, 'normal', name)
        p_b64 = os.path.join(SUBS_XRAY_DIR, 'base64', name)
        
        with open(p_norm, 'w', encoding='utf-8') as f: f.write(plain)
        with open(p_b64, 'w', encoding='utf-8') as f: f.write(b64)

class Stage5_Converters:
    def run(self, progress_callback=None):
        print("\n--- STAGE 5: CONVERTERS ---")
        input_dir = os.path.join(SUBS_XRAY_DIR, 'base64')
        if not os.path.exists(input_dir): return
        files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
        
        total_files = len(files)
        print(f"Converting {total_files} files...")
        
        for idx, fname in enumerate(files):
            if ABORT_FLAG: return
            if progress_callback: progress_callback( (idx + 1) / total_files )
            path = os.path.join(input_dir, fname)
            try:
                with open(path, 'r', encoding='utf-8') as f: b64_content = f.read()
                decoded = base64.b64decode(b64_content).decode('utf-8', errors='ignore')
                raw_configs = [l.strip() for l in decoded.splitlines() if Utils.detect_type(l.strip())]
            except Exception: continue
            
            if not raw_configs: continue
            parsed_configs = []
            for raw in raw_configs:
                p = ConfigParser.parse(raw)
                if p: parsed_configs.append(p)

            profile_name = f"{GlobalConfig.BRANDING} | {fname.upper()}"
            self.process_singbox(fname, parsed_configs, profile_name)
            self.process_clash(fname, parsed_configs, profile_name)
            self.process_surfboard(fname, parsed_configs, profile_name)

    def process_singbox(self, filename, configs, title):
        sb_dir = os.path.join(OUTPUT_DIR, 'singbox')
        os.makedirs(sb_dir, exist_ok=True)
        template_path = os.path.join(TEMPLATES_DIR, "structure.json")
        if not os.path.exists(template_path): return
        try:
            with open(template_path, 'r', encoding='utf-8') as f: structure = json.load(f)
        except: return
        valid_outbounds = []
        tags = []
        for data in configs:
            sb_conf = ProxyConverter.to_singbox(data)
            if sb_conf: valid_outbounds.append(sb_conf); tags.append(sb_conf['tag'])
        if not valid_outbounds: return
        structure['outbounds'].extend(valid_outbounds)
        for i in range(min(2, len(structure['outbounds']))):
            if 'outbounds' in structure['outbounds'][i] and isinstance(structure['outbounds'][i]['outbounds'], list):
                structure['outbounds'][i]['outbounds'].extend(tags)
        b64_title = base64.b64encode(title.encode()).decode()
        header = f"//profile-title: base64:{b64_title}\n//profile-update-interval: 1\n//profile-web-page-url: https://github.com/itsyebekhe/PSG\n\n"
        with open(os.path.join(sb_dir, f"{filename}.json"), 'w', encoding='utf-8') as f: f.write(header + json.dumps(structure, indent=2, ensure_ascii=False))

    def process_clash(self, filename, configs, title):
        clash_dir = os.path.join(OUTPUT_DIR, 'clash')
        os.makedirs(clash_dir, exist_ok=True)
        template_path = os.path.join(TEMPLATES_DIR, "clash.yaml")
        if not os.path.exists(template_path): return
        with open(template_path, 'r', encoding='utf-8') as f: template = f.read()
        proxies = []; proxy_names = []
        for data in configs:
            clash_conf = ProxyConverter.to_clash(data)
            if clash_conf: proxies.append(clash_conf); proxy_names.append(clash_conf['name'])
        if not proxies: return
        proxies_yaml = "".join([f"  - {json.dumps(p, ensure_ascii=False)}\n" for p in proxies])
        names_yaml = "".join([f"      - '{n.replace("'", "''")}'\n" for n in proxy_names])
        with open(os.path.join(clash_dir, f"{filename}.yaml"), 'w', encoding='utf-8') as f: f.write(template.replace('##PROXIES##', proxies_yaml.strip()).replace('##PROXY_NAMES##', names_yaml.strip()))

    def process_surfboard(self, filename, configs, title):
        if not any(filename.startswith(p) for p in ['mix', 'vmess', 'trojan', 'ss']): return
        surf_dir = os.path.join(OUTPUT_DIR, 'surfboard')
        os.makedirs(surf_dir, exist_ok=True)
        template_path = os.path.join(TEMPLATES_DIR, "surfboard.ini")
        if not os.path.exists(template_path): return
        with open(template_path, 'r', encoding='utf-8') as f: template = f.read()
        proxy_lines = []; proxy_names = []
        for data in configs:
            line = ProxyConverter.to_surfboard(data)
            if line:
                proxy_lines.append(line)
                name_key = 'ps' if data['protocol'] == 'vmess' else ('name' if data['protocol'] == 'ss' else 'hash')
                proxy_names.append(data[name_key].replace(',', ' '))
        if not proxy_lines: return
        config_url = f"https://raw.githubusercontent.com/itsyebekhe/PSG/main/subscriptions/surfboard/{filename}"
        final_ini = template.replace('##CONFIG_URL##', config_url).replace('##PROXIES##', "\n".join(proxy_lines)).replace('##PROXY_NAMES##', ", ".join(proxy_names))
        with open(os.path.join(surf_dir, filename), 'w', encoding='utf-8') as f: f.write(final_ini)

# --- Expose Functions for GUI ---
def init_globals(settings): GlobalConfig.update(settings)
def run_stage_1(): Stage1_Fetcher().run()
def run_stage_2_5(cb=None, convert=True):
    Stage2_Extractor().run(progress_callback=cb)
    Stage3_Deduplicator().run()
    valid = Stage3_5_Speedtest().run(progress_callback=cb)
    Stage4_Sorter().run(config_list=valid)

if __name__ == "__main__":
    init_globals({})
    run_stage_1()
    run_stage_2_5()