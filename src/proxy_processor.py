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

# --- Try importing geoip2 ---
try:
    import geoip2.database
    HAS_GEOIP_LIB = True
except ImportError:
    HAS_GEOIP_LIB = False

# --- Global Control Flags ---
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
            print("Killed speedtest process.")
        except: pass

# --- Configuration (MERGED FIX) ---
INTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))

def get_work_dir():
    if platform.system() != "Windows":
        return os.path.expanduser("~")
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()

WORK_DIR = get_work_dir()

# 1. Assets Logic (Priority: User File > Bundled File)
USER_CHANNELS_DIR = os.path.join(WORK_DIR, "channelsData")
USER_ASSETS_FILE = os.path.join(USER_CHANNELS_DIR, "channelsAssets.json")
BUNDLED_ASSETS_FILE = os.path.join(INTERNAL_DIR, "channelsData", "channelsAssets.json")

# Logic: If user has their own file, use it. Otherwise read the internal one.
if os.path.exists(USER_ASSETS_FILE):
    ASSETS_FILE = USER_ASSETS_FILE
else:
    ASSETS_FILE = BUNDLED_ASSETS_FILE

# 2. Other Read-Only Assets
TEMPLATES_DIR = os.path.join(INTERNAL_DIR, "templates")
MMDB_FILE = os.path.join(INTERNAL_DIR, "channelsData", "GeoLite2-Country.mmdb")

# 3. Writeable Paths (Persistent)
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

# --- Dynamic Configuration Class ---
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

# --- Helper Utilities ---
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
            if not s: return False
            Utils.safe_base64_decode(s)
            return True
        except Exception: return False

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
                output = {
                    'protocol': ctype,
                    'username': parsed.username or '',
                    'pass': parsed.password or '',
                    'hostname': parsed.hostname or '',
                    'port': parsed.port or '',
                    'params': params,
                    'hash': urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"{GlobalConfig.BRANDING}_{Utils.get_random_name()}"
                }
                return output
            elif ctype == 'ss':
                parsed = urllib.parse.urlparse(config)
                user_info = urllib.parse.unquote(parsed.username or '')
                if Utils.is_base64(user_info) and ':' not in user_info:
                    try:
                        decoded = Utils.safe_base64_decode(user_info).decode('utf-8')
                        if ':' in decoded: user_info = decoded
                    except: pass
                if ':' not in user_info: return None
                method, password = user_info.split(':', 1)
                return {
                    'protocol': 'ss', 
                    'encryption_method': method,
                    'password': password,
                    'server_address': parsed.hostname or '',
                    'server_port': parsed.port or '',
                    'name': urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"{GlobalConfig.BRANDING}_{Utils.get_random_name()}"
                }
        except Exception: return None
        return None

    @staticmethod
    def rebuild(data: dict, ctype: str) -> str | None:
        try:
            if ctype == 'vmess':
                clean_data = data.copy()
                clean_data.pop('protocol', None)
                js = json.dumps(clean_data, separators=(',', ':'))
                b64 = base64.urlsafe_b64encode(js.encode('utf-8')).decode('utf-8').rstrip('=')
                return f"vmess://{b64}"
            elif ctype in ['vless', 'trojan', 'tuic', 'hy2']:
                scheme = ctype
                user = data.get('username', '')
                password = data.get('pass', '')
                auth = f"{user}:{password}@" if (password and ctype != 'vless') else (f"{user}@" if user else "")
                authority = f"{auth}{data['hostname']}"
                if data.get('port'): authority += f":{data['port']}"
                query = urllib.parse.urlencode(data['params'])
                frag = urllib.parse.quote(data['hash'])
                return f"{scheme}://{authority}?{query}#{frag}"
            elif ctype == 'ss':
                creds = f"{data['encryption_method']}:{data['password']}"
                b64_creds = base64.urlsafe_b64encode(creds.encode('utf-8')).decode('utf-8').rstrip('=')
                authority = f"{b64_creds}@{data['server_address']}:{data['server_port']}"
                frag = urllib.parse.quote(data['name'])
                return f"ss://{authority}#{frag}"
        except Exception: return None
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

class GeoIP:
    def __init__(self):
        self.cache = {}
        if os.path.exists(IP_CACHE_FILE):
            try:
                with open(IP_CACHE_FILE, 'r', encoding='utf-8') as f: self.cache = json.load(f)
            except: self.cache = {}
        self.cf_ranges = self._load_cf_ranges()
        self.reader = None
        if HAS_GEOIP_LIB and os.path.exists(MMDB_FILE):
            try:
                self.reader = geoip2.database.Reader(MMDB_FILE)
            except Exception: pass

    def _load_cf_ranges(self) -> list:
        ranges = []
        try:
            if not os.path.exists(CLOUDFLARE_IPS_CACHE) or (time.time() - os.path.getmtime(CLOUDFLARE_IPS_CACHE) > 86400):
                v4 = requests.get('https://www.cloudflare.com/ips-v4', timeout=5).text.splitlines()
                v6 = requests.get('https://www.cloudflare.com/ips-v6', timeout=5).text.splitlines()
                all_ranges = v4 + v6
                with open(CLOUDFLARE_IPS_CACHE, 'w') as f: json.dump(all_ranges, f)
            else:
                with open(CLOUDFLARE_IPS_CACHE, 'r') as f: all_ranges = json.load(f)
            for r in all_ranges:
                try: ranges.append(ipaddress.ip_network(r.strip()))
                except: pass
        except: pass
        return ranges

    def is_cloudflare(self, ip: str) -> bool:
        try:
            ipa = ipaddress.ip_address(ip)
            for network in self.cf_ranges:
                if ipa in network: return True
        except: return False
        return False

    def get_country(self, host: str) -> str:
        if host in self.cache: return self.cache[host]
        try: ip = socket.gethostbyname(host)
        except: self.cache[host] = 'XX'; return 'XX'
        if self.is_cloudflare(ip): self.cache[host] = 'CF'; return 'CF'
        if self.reader:
            try:
                response = self.reader.country(ip)
                cc = response.country.iso_code
                if cc:
                    self.cache[host] = cc
                    return cc
            except: pass
        apis = [f"http://ip-api.com/json/{ip}", f"https://ipwho.is/{ip}"]
        for url in apis:
            try:
                resp = requests.get(url, timeout=3).json()
                cc = resp.get('countryCode') or resp.get('country_code')
                if cc: self.cache[host] = cc; return cc
            except: continue
        self.cache[host] = 'XX'; return 'XX'

    def save_cache(self):
        with open(IP_CACHE_FILE, 'w', encoding='utf-8') as f: json.dump(self.cache, f, indent=2)

# --- STAGES ---

class Stage1_Fetcher:
    def fetch_url(self, item) -> tuple[str, str, bool]:
        if ABORT_FLAG: return item[0], "", False
        source_name, source_data = item
        if "subscription_url" in source_data:
            print(f"  Downloading subscription: {source_name}")
            try:
                resp = requests.get(source_data["subscription_url"], timeout=GlobalConfig.TIMEOUT)
                if resp.status_code == 200: return source_name, resp.text, True
            except Exception: pass
            return source_name, "", False
        
        url = f"https://t.me/s/{source_name}"
        try:
            resp = requests.get(url, timeout=GlobalConfig.TIMEOUT)
            if resp.status_code == 200: return source_name, resp.text, True
        except Exception: pass
        return source_name, "", False

    def run(self):
        print("--- STAGE 1: CHANNEL FETCHER ---")
        if not os.path.exists(ASSETS_FILE): return
        with open(ASSETS_FILE, 'r', encoding='utf-8') as f: assets = json.load(f)
        if not os.path.exists(HTML_CACHE_DIR): os.makedirs(HTML_CACHE_DIR)
        print(f"Fetching data from {len(assets)} sources (Timeout: {GlobalConfig.TIMEOUT}s)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(self.fetch_url, list(assets.items())))
        
        if ABORT_FLAG: return
        success_count = 0
        for source, content, success in results:
            if success and content:
                with open(os.path.join(HTML_CACHE_DIR, f"{source}.html"), 'w', encoding='utf-8') as f: f.write(content)
                success_count += 1
        print(f"Fetched {success_count}/{len(assets)} sources successfully.")

class Stage2_Extractor:
    def __init__(self):
        self.geoip = GeoIP()
        self.stats = {'total_raw': 0, 'protocol_counts': {}}
    
    def process_and_enrich(self, config_str: str, source: str, key: int) -> dict | None:
        config_str = config_str.split('<')[0]
        if not Utils.is_valid_config(config_str): return None
        ctype = Utils.detect_type(config_str)
        if not ctype: return None
        data = ConfigParser.parse(config_str)
        if not data: return None

        fields = {"vmess": ("add", "ps"), "vless": ("hostname", "hash"), "trojan": ("hostname", "hash"), "tuic": ("hostname", "hash"), "hy2": ("hostname", "hash"), "ss": ("server_address", "name")}
        if ctype not in fields: return None
        ip_field, name_field = fields[ctype]
        host = data.get(ip_field)
        if not host: return None

        country = self.geoip.get_country(host)
        flag = '❔' if country == 'XX' else ('🚩' if country == 'CF' else Utils.get_flag_emoji(country))
        is_encrypted = ctype in ['ss', 'tuic', 'hy2'] or (ctype == 'vmess' and (data.get('tls') or data.get('scy') != 'none')) or 'security=tls' in config_str or 'security=reality' in config_str
        security_emoji = '🔒' if is_encrypted else '🔓'
        
        new_name = f"{flag} {country} | {security_emoji} {ctype.upper()} | @{source} [{key+1}]"
        
        data[name_field] = new_name
        final_config = ConfigParser.rebuild(data, ctype)
        if not final_config: return None
        return {'config': final_config.replace("amp%3B", ""), 'country': country, 'source': source, 'type': ctype}

    def run(self, progress_callback=None):
        print("\n--- STAGE 2: CONFIG EXTRACTOR ---")
        if not os.path.exists(ASSETS_FILE): return
        with open(ASSETS_FILE, 'r', encoding='utf-8') as f: assets = json.load(f)
        configs_list = {}
        for source, data in assets.items():
            if ABORT_FLAG: return
            html_path = os.path.join(HTML_CACHE_DIR, f"{source}.html")
            if os.path.exists(html_path):
                with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
                    links = Utils.extract_links(f.read())
                    if links: configs_list[source] = list(set(links))

        all_processed = []
        sources_valid = {}
        total_sources = len(configs_list)
        print(f"Processing configs from {total_sources} sources...")
        
        for idx, (source, configs) in enumerate(configs_list.items()):
            if ABORT_FLAG: return
            if progress_callback: progress_callback((idx + 1) / total_sources)
            to_process = configs[-GlobalConfig.MAX_CONFIGS_PER_CHANNEL:]
            offset = len(configs) - len(to_process)
            for i, conf in enumerate(to_process):
                res = self.process_and_enrich(conf, source, i + offset)
                if res:
                    all_processed.append(res)
                    sources_valid[source] = True
                    self.stats['total_raw'] += 1
                    self.stats['protocol_counts'][res['type']] = self.stats['protocol_counts'].get(res['type'], 0) + 1
        self.geoip.save_cache()

        print("Writing extracted files...")
        if os.path.exists(OUTPUT_DIR):
            try: shutil.rmtree(OUTPUT_DIR)
            except: pass
        for p in [os.path.join(LOCATION_DIR, "normal"), os.path.join(LOCATION_DIR, "base64"), os.path.join(CHANNEL_SUBS_DIR, "normal"), os.path.join(CHANNEL_SUBS_DIR, "base64")]: os.makedirs(p, exist_ok=True)

        main_configs = [x['config'] for x in all_processed]
        grouped = {}
        for x in all_processed: grouped.setdefault(x['source'], []).append(x['config'])
        
        channel_files_count = 0
        for src, confs in grouped.items():
            plain = "\n".join(confs)
            fname = re.sub(r'[^a-zA-Z0-9_-]', '', src)
            with open(os.path.join(CHANNEL_SUBS_DIR, "normal", fname), 'w', encoding='utf-8') as f: f.write(plain)
            with open(os.path.join(CHANNEL_SUBS_DIR, "base64", fname), 'w', encoding='utf-8') as f: f.write(base64.b64encode(plain.encode()).decode())
            channel_files_count += 1

        loc_grouped = {}
        for x in all_processed:
            loc_grouped.setdefault(x['country'], []).append(x['config'])
        
        for loc, confs in loc_grouped.items():
            if not loc: continue
            plain = "\n".join(confs)
            with open(os.path.join(LOCATION_DIR, "normal", loc), 'w', encoding='utf-8') as f: f.write(plain)
            with open(os.path.join(LOCATION_DIR, "base64", loc), 'w', encoding='utf-8') as f: f.write(base64.b64encode(plain.encode()).decode())

        with open(FINAL_CONFIG_FILE, 'w', encoding='utf-8') as f: f.write("\n".join(main_configs))

        summary = {"meta": {"last_updated": datetime.now(timezone.utc).isoformat(), "author": f"{GlobalConfig.BRANDING}_Python"}, "sources": {"total": len(assets), "valid": len(sources_valid)}, "configs": self.stats, "outputs": {"channel_files": channel_files_count, "country_distribution": {k: len(v) for k,v in loc_grouped.items()}}}
        with open(SUMMARY_FILE, 'w', encoding='utf-8') as f: json.dump(summary, f, indent=2)

class Stage3_Deduplicator:
    def run(self):
        print("\n--- STAGE 3: DEDUPLICATION ---")
        if ABORT_FLAG: return
        if not os.path.exists(FINAL_CONFIG_FILE): return
        with open(FINAL_CONFIG_FILE, 'r', encoding='utf-8') as f: lines = [l.strip() for l in f if l.strip()]
        
        assets = {}
        if os.path.exists(ASSETS_FILE):
            with open(ASSETS_FILE, 'r', encoding='utf-8') as f: assets = json.load(f)

        seen = {}
        unique = []
        for l in lines:
            if l not in seen:
                seen[l] = True
                unique.append(l)

        final_output = unique
        
        api_data = []
        for conf in final_output:
            dt = ConfigParser.parse(conf)
            if not dt: continue
            ctype = dt['protocol']
            name_field = 'ps' if ctype == 'vmess' else ('name' if ctype == 'ss' else 'hash')
            name = dt.get(name_field, "")
            src_username = 'unknown'
            parts = name.split('|')
            if len(parts) >= 4: src_username = parts[3].strip().lstrip('@')
            chan_info = assets.get(src_username, {'title': 'Unknown', 'logo': ''})
            effective_type = 'reality' if ctype == 'vless' and 'security=reality' in conf else ctype
            api_data.append({'channel': {'username': src_username, 'title': chan_info.get('title'), 'logo': chan_info.get('logo')}, 'type': effective_type, 'config': conf})

        with open(FINAL_CONFIG_FILE, 'w', encoding='utf-8') as f: f.write("\n".join(final_output))
        
        mix_dir = os.path.join(SUBS_XRAY_DIR, 'normal'); mix_dir_b64 = os.path.join(SUBS_XRAY_DIR, 'base64')
        os.makedirs(mix_dir, exist_ok=True); os.makedirs(mix_dir_b64, exist_ok=True)
        header = self._hiddify_header(f"{GlobalConfig.BRANDING} | MIX")
        content = header + "\n".join(final_output)
        with open(os.path.join(mix_dir, "mix"), 'w', encoding='utf-8') as f: f.write(content)
        with open(os.path.join(mix_dir_b64, "mix"), 'w', encoding='utf-8') as f: f.write(base64.b64encode(content.encode()).decode())
        
        os.makedirs(API_DIR, exist_ok=True)
        with open(API_OUTPUT_FILE, 'w', encoding='utf-8') as f: json.dump(api_data, f, indent=2, ensure_ascii=False)
        print(f"Unique configs: {len(final_output)}")

    def _hiddify_header(self, title):
        b64_title = base64.b64encode(title.encode()).decode()
        return f"#profile-title: base64:{b64_title}\n#profile-update-interval: 1\n#support-url: https://t.me/yebekhe\n\n"

class Stage3_5_Speedtest:
    def run(self, progress_callback=None):
        print("\n--- STAGE 3.5: SPEEDTEST FILTERING ---")
        if ABORT_FLAG: return None
        
        global CURRENT_SUBPROCESS
        is_windows = sys.platform == "win32"
        exe_name = "xray-knife.exe" if is_windows else "./xray-knife"
        exe_path = os.path.join(WORK_DIR, exe_name)

        if not os.path.exists(exe_path):
            print(f"⚠️ {exe_name} not found. Skipping speedtest.")
            return None

        if not os.path.exists(FINAL_CONFIG_FILE):
            print("No config file to test.")
            return None

        if progress_callback: progress_callback(None)

        mdelay_ms = str(GlobalConfig.TIMEOUT * 1000)
        print(f"Running Xray-Knife (Max Delay: {mdelay_ms}ms)...")
        
        valid_output = os.path.join(WORK_DIR, "valid_configs.txt")
        if os.path.exists(valid_output): os.remove(valid_output)

        cmd = [
            exe_path, 
            "http", 
            "-f", FINAL_CONFIG_FILE,
            "-o", valid_output,
            "-d", mdelay_ms,
            "--thread", "20"
        ]

        valid_configs = []

        try:
            CURRENT_SUBPROCESS = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            while True:
                line = CURRENT_SUBPROCESS.stdout.readline()
                if not line and CURRENT_SUBPROCESS.poll() is not None:
                    break
                if line:
                    sys.stdout.write(line)
            
            CURRENT_SUBPROCESS = None

            if ABORT_FLAG:
                print("Speedtest Aborted.")
                return None

            if os.path.exists(valid_output) and os.path.getsize(valid_output) > 0:
                with open(valid_output, 'r', encoding='utf-8') as f:
                    valid_data = f.read()
                    valid_configs = [l.strip() for l in valid_data.splitlines() if l.strip()]
                
                print(f"✅ Speedtest Complete. Active Configs: {len(valid_configs)}")
                
                # Overwrite the main file so API sync works
                with open(FINAL_CONFIG_FILE, 'w', encoding='utf-8') as f:
                    f.write(valid_data)
                
                self._sync_api_file()
                
                # RETURN THE LIST IN MEMORY
                return valid_configs
            else:
                print("⚠️ Speedtest found NO valid configs or failed. Keeping original list.")
                return None
                
        except Exception as e:
            if not ABORT_FLAG:
                print(f"❌ Error executing speedtest: {e}")
            return None

    def _sync_api_file(self):
        if not os.path.exists(API_OUTPUT_FILE) or not os.path.exists(FINAL_CONFIG_FILE): return
        try:
            with open(FINAL_CONFIG_FILE, 'r', encoding='utf-8') as f:
                valid_lines = set([l.strip() for l in f if l.strip()])
            with open(API_OUTPUT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            new_data = [item for item in data if item.get('config') in valid_lines]
            with open(API_OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, indent=2, ensure_ascii=False)
        except: pass

class Stage4_Sorter:
    def create_fake_config(self, name):
        encoded = urllib.parse.quote(name.lstrip('#'))
        return f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443?security=none&type=ws&path=/#{encoded}"

    def get_addr_type(self, config):
        try:
            parsed = urllib.parse.urlparse(config)
            host = parsed.hostname
            if not host: 
                data = ConfigParser.parse(config)
                if data: host = data.get('add') or data.get('server_address') or data.get('hostname')
            if not host: return 'domain'
            ip = ipaddress.ip_address(host.strip('[]'))
            return 'ipv4' if ip.version == 4 else 'ipv6'
        except: return 'domain'

    # MODIFIED: Accepts config_list explicitly
    def run(self, config_list=None):
        print("\n--- STAGE 4: SORTING & FAKES ---")
        if ABORT_FLAG: return
        
        # If list passed from Speedtest, use it. Otherwise read file.
        if config_list and len(config_list) > 0:
            lines = config_list
        elif os.path.exists(FINAL_CONFIG_FILE):
            with open(FINAL_CONFIG_FILE, 'r', encoding='utf-8') as f: lines = [l.strip() for l in f if l.strip()]
        else:
            return

        sorted_confs = {}
        for conf in lines:
            ctype = Utils.detect_type(conf)
            addr = self.get_addr_type(conf)
            if not ctype: continue
            sorted_confs.setdefault(ctype, {}).setdefault(addr, []).append(conf)
            if ctype == 'vless' and 'security=reality' in conf: sorted_confs.setdefault('reality', {}).setdefault(addr, []).append(conf)
            if 'type=xhttp' in conf: sorted_confs.setdefault('xhttp', {}).setdefault(addr, []).append(conf)

        fakes = [self.create_fake_config(n) for n in GlobalConfig.FAKE_CONFIGS]
        
        for p_type, addr_groups in sorted_confs.items():
            all_for_type = []
            for addr, confs in addr_groups.items():
                fname = f"{p_type}_{addr}"
                content_list = fakes + confs
                self._write_sub(fname, content_list, f"{GlobalConfig.BRANDING} | {p_type.upper()} {addr.upper()}")
                all_for_type.extend(confs)
            if all_for_type:
                content_list = fakes + all_for_type
                self._write_sub(p_type, content_list, f"{GlobalConfig.BRANDING} | {p_type.upper()}")

    def _write_sub(self, name, configs, title):
        header = self._hiddify_header(title)
        plain = header + "\n".join(configs)
        b64 = base64.b64encode(plain.encode()).decode()
        p_norm = os.path.join(SUBS_XRAY_DIR, 'normal', name)
        p_b64 = os.path.join(SUBS_XRAY_DIR, 'base64', name)
        with open(p_norm, 'w', encoding='utf-8') as f: f.write(plain)
        with open(p_b64, 'w', encoding='utf-8') as f: f.write(b64)

    def _hiddify_header(self, title):
        b64_title = base64.b64encode(title.encode()).decode()
        return f"#profile-title: base64:{b64_title}\n#profile-update-interval: 1\n\n"

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
def init_globals(settings):
    GlobalConfig.update(settings)

def run_stage_1():
    Stage1_Fetcher().run()

def run_stage_2_5(cb=None, convert=True):
    Stage2_Extractor().run(progress_callback=cb)
    Stage3_Deduplicator().run()
    
    # 3.5: Returns the filtered list
    valid_list = Stage3_5_Speedtest().run(progress_callback=cb)
    
    # 4: Sorts ONLY the filtered list (or falls back if none)
    Stage4_Sorter().run(config_list=valid_list)
    
    if convert:
        Stage5_Converters().run(progress_callback=cb)
    else:
        print("Skipping Converters.")

if __name__ == "__main__":
    init_globals({})
    run_stage_1()
    run_stage_2_5()