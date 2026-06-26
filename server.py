"""
Igraonica TV Tajmer — lokalni server (čist Python, bez instalacije).
Kontroliše Smart TV-ove preko mreže: kad istekne plaćeno vrijeme, gasi TV.
Doplata = produženje. Sony primaran + univerzalni HTTP režim + Wake-on-LAN.

Pokretanje:  python server.py   →  http://127.0.0.1:8770
"""
import base64
import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import time
import uuid
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys
import webbrowser

# VIDAA (Hisense RemoteNOW) — opcioni modul (treba paho-mqtt + cert fajlovi)
try:
    import vidaa
except Exception:
    vidaa = None

# Putanje rade i kad je spakovano u .exe (PyInstaller)
if getattr(sys, "frozen", False):
    RES_DIR = sys._MEIPASS                       # ugrađeni resursi (index.html)
    # Trajni podaci u %APPDATA%\GameZone (uvijek upisivo, i kad je u Program Files)
    APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.dirname(sys.executable), "GameZone")
    try:
        os.makedirs(APP_DIR, exist_ok=True)
    except Exception:
        APP_DIR = os.path.dirname(sys.executable)
else:
    RES_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = RES_DIR

BASE = RES_DIR
DATA_FILE = os.path.join(APP_DIR, "stations.json")
PORT = int(os.environ.get("GAMEZONE_PORT", "8770"))

VERSION = "1.6.0"
UPDATE_REPO = "TeodorSljukic/gamezone-tv"  # GitHub repo za auto-update

_lock = threading.Lock()
_state = {"stations": {}}  # id -> station dict


# ──────────────────────────────────────────────────────────
#  Perzistencija
# ──────────────────────────────────────────────────────────
def load_state():
    global _state
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                _state = json.load(f)
        except Exception:
            _state = {"stations": {}}
    if "stations" not in _state:
        _state["stations"] = {}
    if "packages" not in _state:
        _state["packages"] = list(DEFAULT_PACKAGES)
    if "history" not in _state:
        _state["history"] = []
    if "enforce_remote" not in _state:
        _state["enforce_remote"] = True
    if "price_per_hour" not in _state:
        _state["price_per_hour"] = 0  # globalna cijena/sat za Slobodno (ako stanica nema svoju)


DEFAULT_PACKAGES = [
    {"name": "30 min", "minutes": 30, "price": 2},
    {"name": "1 sat", "minutes": 60, "price": 3},
    {"name": "2 sata", "minutes": 120, "price": 5},
]


def today_str():
    return time.strftime("%Y-%m-%d", time.localtime())


def record_payment(station_id, station_name, minutes, amount, kind):
    """Zabilježi naplatu u istoriju (start ili doplata)."""
    entry = {
        "id": uuid.uuid4().hex[:8],
        "ts": time.time(),
        "date": today_str(),
        "time": time.strftime("%H:%M", time.localtime()),
        "station_id": station_id,
        "station": station_name,
        "minutes": int(minutes),
        "amount": round(float(amount or 0), 2),
        "kind": kind,  # start | extend
    }
    _state["history"].append(entry)
    # drži zadnjih 5000 zapisa
    if len(_state["history"]) > 5000:
        _state["history"] = _state["history"][-5000:]
    return entry


def daily_summary(date=None):
    date = date or today_str()
    rows = [h for h in _state.get("history", []) if h.get("date") == date]
    total = round(sum(h.get("amount", 0) for h in rows), 2)
    return {"date": date, "total": total, "count": len(rows)}


def daily_by_station(date=None):
    """Listing po stanicama za dati dan: koliko je svaki TV radio (min) i zaradio (€)."""
    date = date or today_str()
    agg = {}
    for h in _state.get("history", []):
        if date != "all" and h.get("date") != date:
            continue
        key = h.get("station_id") or h.get("station") or "?"
        a = agg.setdefault(key, {"station": h.get("station", "?"), "minutes": 0, "amount": 0.0, "count": 0})
        a["minutes"] += int(h.get("minutes", 0) or 0)
        a["amount"] = round(a["amount"] + float(h.get("amount", 0) or 0), 2)
        a["count"] += 1
    rows = sorted(agg.values(), key=lambda x: x["amount"], reverse=True)
    total = round(sum(r["amount"] for r in rows), 2)
    total_min = sum(r["minutes"] for r in rows)
    return {"date": date, "stations": rows, "total": total, "total_minutes": total_min}


def save_state():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save error:", e)


# ──────────────────────────────────────────────────────────
#  TV kontrola
# ──────────────────────────────────────────────────────────
def _http(url, data=None, headers=None, timeout=5, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def wake_on_lan(mac):
    """Pošalji WoL magic paket (paljenje preko mreže)."""
    if not mac:
        return False
    mac = mac.replace(":", "").replace("-", "").replace(" ", "")
    if len(mac) != 12:
        return False
    try:
        payload = bytes.fromhex("ff" * 6 + mac * 16)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(payload, ("255.255.255.255", 9))
        s.sendto(payload, ("255.255.255.255", 7))
        s.close()
        return True
    except Exception as e:
        print("WoL error:", e)
        return False


def sony_power(ip, psk, on):
    """Sony Bravia setPowerStatus preko REST (treba IP Control + PSK na TV-u)."""
    url = f"http://{ip}/sony/system"
    body = json.dumps({
        "method": "setPowerStatus",
        "params": [{"status": bool(on)}],
        "id": 1, "version": "1.0",
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Auth-PSK": psk or ""}
    status, _ = _http(url, data=body, headers=headers, timeout=5)
    return 200 <= status < 300


def sony_get_power(ip, psk):
    url = f"http://{ip}/sony/system"
    body = json.dumps({
        "method": "getPowerStatus", "params": [], "id": 1, "version": "1.0",
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Auth-PSK": psk or ""}
    status, raw = _http(url, data=body, headers=headers, timeout=5)
    try:
        d = json.loads(raw.decode("utf-8", "ignore"))
        return d.get("result", [{}])[0].get("status", "unknown")
    except Exception:
        return "unknown" if 200 <= status < 300 else "error"


def roku_power_off(ip):
    """Roku/Hisense/TCL — keypress PowerOff (port 8060)."""
    status, _ = _http(f"http://{ip}:8060/keypress/PowerOff", data=b"",
                      timeout=5, method="POST")
    return 200 <= status < 300


# ── Minimalni WebSocket klijent (stdlib) za Samsung/LG ──
def _ws_open(ip, port, path, tls, timeout=8):
    raw = socket.create_connection((ip, port), timeout=timeout)
    if tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(raw, server_hostname=ip)
    else:
        sock = raw
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\nHost: {ip}:{port}\r\n"
           f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    sock.sendall(req.encode())
    buf = b""
    sock.settimeout(timeout)
    while b"\r\n\r\n" not in buf:
        c = sock.recv(1)
        if not c:
            break
        buf += c
    if b" 101 " not in buf.split(b"\r\n")[0]:
        raise RuntimeError("WS handshake nije uspio")
    return sock


def _ws_send(sock, text):
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    n = len(payload)
    header = bytearray([0x81])
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126); header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127); header += struct.pack(">Q", n)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_recv(sock, timeout=6):
    sock.settimeout(timeout)
    try:
        b1 = sock.recv(1)
        if not b1:
            return None
        b2 = sock.recv(1)
        ln = b2[0] & 0x7f
        if ln == 126:
            ln = struct.unpack(">H", sock.recv(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", sock.recv(8))[0]
        data = b""
        while len(data) < ln:
            c = sock.recv(ln - len(data))
            if not c:
                break
            data += c
        return data.decode("utf-8", "ignore")
    except Exception:
        return None


def samsung_send_key(ip, token, key="KEY_POWER"):
    """Samsung Tizen — pošalji bilo koji taster (KEY_POWER, KEY_TV, KEY_HDMI, ...).
    Vraća (ok, novi_token, msg). Ako nema token -> čeka popup uparivanja (prvi put)."""
    name_b64 = base64.b64encode(b"Igraonica TV Tajmer").decode()
    path = f"/api/v2/channels/samsung.remote.control?name={name_b64}"
    if token:
        path += f"&token={token}"
    try:
        sock = _ws_open(ip, 8002, path, tls=True, timeout=8)
    except Exception as e:
        return False, token, f"WS greška: {str(e)[:60]}"
    new_token = token
    # ako vec imamo token -> kratko cekanje; ako ne -> duze (popup uparivanja)
    msg = _ws_recv(sock, timeout=(2 if token else 12))
    if msg:
        try:
            d = json.loads(msg)
            t = d.get("data", {}).get("token")
            if t:
                new_token = str(t)
        except Exception:
            pass
    cmd = json.dumps({"method": "ms.remote.control", "params": {
        "Cmd": "Click", "DataOfCmd": key, "Option": "false",
        "TypeOfRemote": "SendRemoteKey"}})
    try:
        _ws_send(sock, cmd)
        time.sleep(0.4)
        ok = True
    except Exception as e:
        return False, new_token, f"send greška: {str(e)[:60]}"
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return ok, new_token, ("upareno" if not token else "OK")


def samsung_power_toggle(ip, token):
    """Samsung Tizen — KEY_POWER (gasi/pali toggle). Zadržano radi kompatibilnosti."""
    return samsung_send_key(ip, token, "KEY_POWER")


def samsung_get_power(ip):
    """Samsung Tizen — procitaj stanje (on/standby) preko REST device info.
    Vraca 'on' / 'standby' / '' (nepoznato/nedostupno). NIKAD ne puca."""
    try:
        status, raw = _http(f"http://{ip}:8001/api/v2/", timeout=2)
        if not (200 <= status < 300):
            return ""
        d = json.loads(raw.decode("utf-8", "ignore"))
        ps = d.get("device", {}).get("PowerState", "")
        return str(ps).lower()
    except Exception:
        return ""  # nedostupan/timeout (TV ugasen) -> tretiramo kao 'nije on'


def _samsung_wake(sid):
    """Paljenje Samsunga SAMO preko WoL-a (NIKAD KEY_POWER).
    Razlog: ovaj model zna lazno da javi 'standby' i kad je TV UPALJEN. KEY_POWER je
    toggle -> ako ga posaljemo upaljenom TV-u, UGASIMO ga (= blink na startu).
    WoL ne moze da ugasi upaljen TV -> sigurno. Ako WoL ne probudi (WiFi), radnik pritisne daljinac."""
    with _lock:
        s = _state["stations"].get(sid)
        if not s:
            return
        mac = s.get("mac", "")
    if mac:
        wake_on_lan(mac)


def _samsung_off(sid):
    """Cycle-proof gašenje Samsunga (KEY_POWER je TOGGLE -> opasnost od pali/gasi).
    Zaštite: (1) dupla provjera 'on' (ignoriše prelaz), (2) cooldown poslije slanja,
    (3) backoff — ako se TV uporno ne gasi, pauza raste (9s,18s,... do 5min) da ne trešti."""
    with _lock:
        s = _state["stations"].get(sid)
        if not s:
            return
        if s.get("_samsung_cd", 0) > time.time():
            return  # cooldown
        ip = s.get("ip", ""); token = s.get("samsung_token", "")
        fails = int(s.get("_samsung_fails", 0))
    # Dupla provjera: mora biti 'on' DVA puta zaredom (da ne reagujemo na prelaz/lažno)
    if samsung_get_power(ip) != "on":
        if fails:
            with _lock:
                s = _state["stations"].get(sid)
                if s:
                    s["_samsung_fails"] = 0  # TV se ugasio -> resetuj backoff
        return
    time.sleep(1.5)
    if samsung_get_power(ip) != "on":
        return  # bio prelaz, nije stabilno upaljen -> ne diraj
    # Stvarno je upaljen -> ugasi
    ok, ntok, _ = samsung_power_toggle(ip, token)
    fails += 1
    cd = min(9 * (2 ** (fails - 1)), 300)  # 9s,18s,36s,72s... do 5 min
    with _lock:
        s = _state["stations"].get(sid)
        if s:
            if ntok:
                s["samsung_token"] = ntok
            s["_samsung_cd"] = time.time() + cd
            s["_samsung_fails"] = fails


def _samsung_input(sid, want_game):
    """Režim ULAZA (TV ostaje upaljen): prebaci na igru (HDMI) ili blokiraj (TV/tuner).
    want_game=True -> HDMI (igra); False -> blokirano (nema signala)."""
    with _lock:
        s = _state["stations"].get(sid)
        if not s:
            return
        ip = s.get("ip", ""); token = s.get("samsung_token", "")
        if want_game:
            key = s.get("samsung_unblock_key", "KEY_HDMI")
        else:
            key = s.get("samsung_block_key", "KEY_TV")
    ok, ntok, _ = samsung_send_key(ip, token, key)
    if ntok:
        with _lock:
            s = _state["stations"].get(sid)
            if s:
                s["samsung_token"] = ntok


LG_PERMISSIONS = [
    "LAUNCH", "CONTROL_POWER", "READ_TV_CURRENT_CHANNEL",
    "CONTROL_INPUT_TV", "CONTROL_DISPLAY", "TEST_OPEN",
]


def lg_turn_off(ip, client_key):
    """LG webOS SSAP — turnOff. Vraća (ok, novi_key, msg)."""
    try:
        sock = _ws_open(ip, 3000, "/", tls=False, timeout=8)
    except Exception as e:
        return False, client_key, f"WS greška: {str(e)[:60]}"
    payload = {
        "forcePairing": False, "pairingType": "PROMPT",
        "manifest": {
            "manifestVersion": 1, "appId": "com.igraonica.tajmer",
            "vendorId": "com.igraonica", "localizedAppNames": {"": "Igraonica"},
            "permissions": LG_PERMISSIONS,
        },
    }
    if client_key:
        payload["client-key"] = client_key
    new_key = client_key
    try:
        _ws_send(sock, json.dumps({"type": "register", "id": "reg0", "payload": payload}))
        # čeka registered (prvi put traži prihvat na TV-u)
        for _ in range(6):
            msg = _ws_recv(sock, timeout=12)
            if not msg:
                break
            try:
                d = json.loads(msg)
            except Exception:
                continue
            ck = d.get("payload", {}).get("client-key")
            if ck:
                new_key = ck
            if d.get("type") == "registered":
                break
        _ws_send(sock, json.dumps({"type": "request", "id": "off1", "uri": "ssap://system/turnOff"}))
        time.sleep(0.4)
        ok = True
    except Exception as e:
        return False, new_key, f"send greška: {str(e)[:60]}"
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return ok, new_key, ("upareno" if not client_key else "OK")


def control_tv(station, on):
    """Univerzalni: gasi/pali TV po tipu kontrole. Vraća (ok, poruka)."""
    ctype = station.get("control", "sony")
    ip = station.get("ip", "")
    mac = station.get("mac", "")
    psk = station.get("psk", "")
    try:
        if ctype == "http":
            # Univerzalni HTTP režim — korisnik unese on/off URL (Shelly, Tasmota, itd.)
            url = station.get("on_url" if on else "off_url", "")
            if not url:
                return False, "Nema URL-a"
            st, _ = _http(url, timeout=5)
            return (200 <= st < 300), f"HTTP {st}"
        if ctype == "wol":
            if on:
                return (wake_on_lan(mac), "WoL paket poslat")
            return False, "WoL ne može da gasi (samo pali)"
        if ctype == "pc":
            # Računar: zaključavanje radi agent na samom PC-u (čita status preko /api/agent)
            if not on and mac:
                wake_on_lan(mac)  # opciono probudi ako je podešeno
            return (True, "PC agent")
        if ctype == "roku":
            if on:
                return (wake_on_lan(mac), "WoL paket poslat")
            return (roku_power_off(ip), "Roku PowerOff")
        if ctype == "vidaa":
            # Hisense VIDAA (MQTT 36669). KEY_POWER je toggle -> status-aware.
            if vidaa is None:
                return False, "VIDAA modul nedostupan (paho-mqtt?)"
            woke = wake_on_lan(mac) if (on and mac) else False
            ok, msg = vidaa.power(ip, on)
            return (ok or woke), msg
        if ctype == "samsung":
            sidv = station.get("id", "")
            mode = station.get("samsung_mode", "power")
            if mode == "input":
                # Režim ULAZA: TV ostaje upaljen, mijenjamo HDMI<->TV (radi i na WiFi-u)
                if sidv:
                    threading.Thread(target=_samsung_input, args=(sidv, on), daemon=True).start()
                return True, ("Samsung: ulaz → igra" if on else "Samsung: ulaz → blokirano")
            if on:
                # Pouzdano paljenje u pozadini (WoL + KEY_POWER, status-aware -> bez blinka)
                if sidv:
                    threading.Thread(target=_samsung_wake, args=(sidv,), daemon=True).start()
                elif mac:
                    wake_on_lan(mac)
                return True, "Samsung ON (buđenje)"
            # Gašenje u pozadini (samo ako je stvarno upaljen) — ne blokira UI
            if sidv:
                threading.Thread(target=_samsung_off, args=(sidv,), daemon=True).start()
                return True, "Samsung OFF (u toku)"
            ok, newtok, msg = samsung_power_toggle(ip, station.get("samsung_token", ""))
            if newtok:
                station["samsung_token"] = newtok
            return ok, f"Samsung OFF ({msg})"
        if ctype == "lg":
            if on:
                woke = wake_on_lan(mac) if mac else False
                return (woke, "LG ON (WoL)" if woke else "LG: dodaj MAC za paljenje")
            ok, newkey, msg = lg_turn_off(ip, station.get("lg_key", ""))
            if newkey:
                station["lg_key"] = newkey
            return ok, f"LG OFF ({msg})"
        # default: sony
        if on:
            woke = wake_on_lan(mac) if mac else False
            try:
                ok = sony_power(ip, psk, True)
            except Exception:
                ok = woke
            return (ok or woke), "Sony ON" + (" + WoL" if mac else "")
        ok = sony_power(ip, psk, False)
        return ok, "Sony OFF"
    except Exception as e:
        return False, str(e)[:80]


# ──────────────────────────────────────────────────────────
#  Mrežno otkrivanje TV-ova (SSDP/UPnP + ARP za MAC)
# ──────────────────────────────────────────────────────────
def ssdp_discover(timeout=3.0):
    """Pošalji SSDP M-SEARCH i pokupi uređaje koji se jave."""
    targets = [
        "ssdp:all",
        "urn:schemas-sony-com:service:ScalarWebAPI:1",
        "urn:schemas-upnp-org:device:MediaRenderer:1",
        "urn:dial-multiscreen-org:service:dial:1",
    ]
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(1.0)
        for st in targets:
            req = ("M-SEARCH * HTTP/1.1\r\n"
                   "HOST: 239.255.255.250:1900\r\n"
                   'MAN: "ssdp:discover"\r\n'
                   "MX: 2\r\n"
                   f"ST: {st}\r\n\r\n").encode()
            try:
                s.sendto(req, ("239.255.255.250", 1900))
                s.sendto(req, ("239.255.255.250", 1900))
            except Exception:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            ip = addr[0]
            text = data.decode("utf-8", "ignore")
            headers = {}
            for line in text.split("\r\n")[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().upper()] = v.strip()
            if ip not in found:
                found[ip] = {
                    "location": headers.get("LOCATION", ""),
                    "server": headers.get("SERVER", ""),
                    "st": headers.get("ST", ""),
                }
        s.close()
    except Exception as e:
        print("ssdp error:", e)
    return found


def fetch_desc(location):
    """Pročitaj UPnP device description XML (friendlyName/manufacturer/model)."""
    try:
        _, raw = _http(location, timeout=4)
        xml = raw.decode("utf-8", "ignore")

        def tag(t):
            m = re.search(rf"<{t}>(.*?)</{t}>", xml, re.S | re.I)
            return m.group(1).strip() if m else ""

        return {
            "friendlyName": tag("friendlyName"),
            "manufacturer": tag("manufacturer"),
            "modelName": tag("modelName"),
        }
    except Exception:
        return {}


def arp_mac(ip):
    """MAC iz ARP tabele (za Wake-on-LAN)."""
    try:
        out = subprocess.run(["arp", "-a", ip], capture_output=True,
                             text=True, timeout=5).stdout
        m = re.search(r"((?:[0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2})", out)
        return m.group(1).replace("-", ":").upper() if m else ""
    except Exception:
        return ""


def classify_device(info):
    blob = " ".join([
        info.get("manufacturer", ""), info.get("modelName", ""),
        info.get("friendlyName", ""), info.get("server", ""),
    ]).lower()
    if "sony" in blob or "bravia" in blob:
        return "SONY", "sony"
    if "samsung" in blob:
        return "SAMSUNG", "samsung"
    if "lg" in blob or "webos" in blob:
        return "LG", "lg"
    if "vidaa" in blob:
        return "VIDAA", "vidaa"
    if "roku" in blob or "tcl" in blob or "hisense" in blob:
        return "ROKU", "roku"
    return "OTHER", "http"


def _tcp_open(ip, port, timeout=0.35):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.close()
        return True
    except Exception:
        return False


def _sony_probe(ip, timeout=1.2):
    """getInterfaceInformation cesto NE trazi PSK -> potvrda da je Sony + model."""
    try:
        url = f"http://{ip}/sony/system"
        body = json.dumps({"method": "getInterfaceInformation",
                           "id": 1, "version": "1.0", "params": []}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        res = d.get("result", [])
        return res[0] if res else {}
    except Exception:
        return None


# TV-specificni portovi (aktivno skeniranje, prolazi kroz firewall)
_TV_PORTS = {8001: "samsung", 8002: "samsung", 3000: "lg", 3001: "lg",
             8060: "roku", 36669: "vidaa"}


def subnet_scan(found):
    """Aktivno proba svaku IP na /24 mrezi na TV portovima (radi i kad SSDP ne radi)."""
    base = lan_ip()
    if "." not in base or base == "127.0.0.1":
        return
    prefix = base.rsplit(".", 1)[0]
    lock = threading.Lock()
    sem = threading.Semaphore(120)

    def check(host):
        ip = f"{prefix}.{host}"
        brand = None
        control = None
        model = ""
        for port, ctl in _TV_PORTS.items():
            if _tcp_open(ip, port, 0.3):
                control = ctl
                brand = {"samsung": "SAMSUNG", "lg": "LG", "roku": "ROKU",
                         "vidaa": "VIDAA"}[ctl]
                break
        if brand is None and _tcp_open(ip, 80, 0.3):
            si = _sony_probe(ip)
            if si is not None:
                brand, control = "SONY", "sony"
                model = si.get("modelName", "") or si.get("productName", "")
        if brand:
            with lock:
                if ip not in found:
                    found[ip] = {"location": "", "server": "",
                                 "st": "", "brand": brand,
                                 "control": control, "model": model}
                else:
                    found[ip].setdefault("brand", brand)
                    found[ip].setdefault("control", control)

    def worker(h):
        with sem:
            check(h)

    threads = [threading.Thread(target=worker, args=(h,), daemon=True)
               for h in range(1, 255)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=8)


def discover_devices():
    found = ssdp_discover(2.5)        # 1) SSDP (brzo, ako multicast radi)
    try:
        subnet_scan(found)             # 2) aktivno skeniranje (radi kroz firewall)
    except Exception as e:
        print("subnet scan err:", e)
    results = []
    for ip, meta in found.items():
        desc = fetch_desc(meta["location"]) if meta.get("location") else {}
        desc["server"] = meta.get("server", "")
        # brend iz SSDP opisa; ako ga nema, iz aktivnog skena
        brand, control = classify_device(desc)
        # Otkrice po OTVORENOM portu (meta) je mjerodavno za protokol
        # (npr. Hisense VIDAA otvara 36669; ne smije pasti u 'roku').
        if meta.get("control"):
            control = meta["control"]
            brand = meta.get("brand", brand)
        elif brand == "OTHER" and meta.get("brand"):
            brand, control = meta["brand"], meta["control"]
        model = desc.get("modelName", "") or meta.get("model", "")
        name = desc.get("friendlyName") or model or f"{brand} TV" if brand != "OTHER" else (desc.get("friendlyName") or ip)
        text = (desc.get("friendlyName", "") + " " + model + " " + meta.get("st", "")).lower()
        is_tv = brand != "OTHER" or "tv" in text or "bravia" in text or "mediarenderer" in text
        results.append({
            "ip": ip,
            "name": name,
            "manufacturer": desc.get("manufacturer", ""),
            "model": model,
            "brand": brand,
            "control": control,
            "mac": arp_mac(ip),
            "is_tv": is_tv,
        })
    results.sort(key=lambda x: (not x["is_tv"], str(x["name"]).lower()))
    return results


# ──────────────────────────────────────────────────────────
#  Logika sesije / tajmer
# ──────────────────────────────────────────────────────────
def effective_pph(s):
    """Cijena/sat: stanica ima prednost, pa globalni cjenovnik."""
    p = float(s.get("price_per_hour", 0) or 0)
    if p <= 0:
        p = float(_state.get("price_per_hour", 0) or 0)
    return p


def station_public(sid, s):
    now = time.time()
    ends = s.get("ends_at")
    is_open = bool(s.get("open"))
    active = s.get("status") == "ACTIVE"
    remaining = int(ends - now) if (ends and active and not is_open) else 0
    if remaining < 0:
        remaining = 0
    # Slobodno (otvoreno): brojimo proteklo vrijeme i tekuci iznos po cijeni/sat
    started = s.get("started_at")
    pph = effective_pph(s)
    elapsed = int(now - started) if (is_open and active and started) else 0
    if elapsed < 0:
        elapsed = 0
    live_amount = round(elapsed / 3600.0 * pph, 2) if is_open else 0
    return {
        "id": sid,
        "name": s.get("name", ""),
        "control": s.get("control", "sony"),
        "ip": s.get("ip", ""),
        "mac": s.get("mac", ""),
        "psk": s.get("psk", ""),
        "brand": s.get("brand", "SONY"),
        "on_url": s.get("on_url", ""),
        "off_url": s.get("off_url", ""),
        "price_per_hour": s.get("price_per_hour", 0),
        "pph_eff": pph,  # efektivna cijena/sat (stanica ili globalna)
        "status": s.get("status", "IDLE"),  # IDLE, ACTIVE, EXPIRED
        "open": is_open,
        "elapsed": elapsed,
        "live_amount": live_amount,
        "remaining": remaining,
        "ends_at": ends,
        "tv_on": s.get("tv_on", False),
        "last_action": s.get("last_action", ""),
        "paid": s.get("paid", 0) if s.get("status") in ("ACTIVE", "EXPIRED") else 0,
        "session_minutes": s.get("session_minutes", 0) if s.get("status") in ("ACTIVE", "EXPIRED") else 0,
    }


def timer_loop():
    """Pozadinska nit: gasi TV kad istekne vrijeme."""
    while True:
        try:
            now = time.time()
            changed = False
            with _lock:
                for sid, s in _state["stations"].items():
                    if s.get("status") == "ACTIVE" and s.get("ends_at") and now >= s["ends_at"]:
                        ok, msg = control_tv(s, False)
                        s["status"] = "EXPIRED"
                        s["tv_on"] = False
                        s["last_action"] = f"Isteklo → TV OFF ({msg})"
                        changed = True
                        print(f"[{s.get('name')}] vrijeme isteklo → gašenje TV: {ok} {msg}")
                if changed:
                    save_state()
        except Exception as e:
            print("timer error:", e)
        time.sleep(1)


# ──────────────────────────────────────────────────────────
#  ČUVAR (anti-daljinac): drži TV ugašen kad stanica nije plaćena
# ──────────────────────────────────────────────────────────
ENFORCE_INTERVAL = 2.0  # sekundi izmedju provjera po stanici (sto manje = daljinac beskorisniji)


def _enforce_off(sid):
    with _lock:
        s = _state["stations"].get(sid)
        if not s or s.get("status") == "ACTIVE":
            return
        # grace period: tek pokrenuto/produzeno -> ne gasi (da paljenje stigne)
        if s.get("_no_enforce_until", 0) > time.time():
            return
        scopy = dict(s)
    ctype = scopy.get("control", "")
    try:
        if ctype == "sony":
            # BEZUSLOVNO gasi: ako je u standby, komanda nema efekta;
            # ako je neko upalio daljincem -> odmah ugasi. (Bez upita = pouzdanije.)
            ok = sony_power(scopy.get("ip", ""), scopy.get("psk", ""), False)
            if ok:
                with _lock:
                    s = _state["stations"].get(sid)
                    if s:
                        s["last_action"] = "Čuvar: ugašen (nije plaćeno)"
        elif ctype == "samsung":
            if scopy.get("samsung_mode") == "input":
                # Režim ulaza: drži ga na blokiranom ulazu (KEY_TV je apsolutno -> sigurno ponavljanje)
                _samsung_input(sid, False)
            else:
                # status-aware (u _samsung_off): KEY_POWER samo ako je stvarno upaljen
                _samsung_off(sid)
        elif ctype == "http":
            url = scopy.get("off_url", "")
            if url:
                _http(url, timeout=4)
        elif ctype == "roku":
            roku_power_off(scopy.get("ip", ""))
        elif ctype == "vidaa":
            if vidaa is not None:
                vidaa.power(scopy.get("ip", ""), False)  # status-aware: nece upaliti ugasen
        elif ctype == "lg":
            ok, nk, _ = lg_turn_off(scopy.get("ip", ""), scopy.get("lg_key", ""))
            if nk:
                with _lock:
                    s = _state["stations"].get(sid)
                    if s:
                        s["lg_key"] = nk
    except Exception:
        pass


def enforce_loop():
    """Periodicno gasi TV-ove stanica koje NISU aktivne (anti-daljinac)."""
    while True:
        try:
            now = time.time()
            with _lock:
                on = _state.get("enforce_remote", True)
                items = list(_state["stations"].items())
            if on:
                for sid, s in items:
                    if s.get("status") == "ACTIVE":
                        continue
                    if s.get("_no_enforce_until", 0) > now:
                        continue
                    # Samsung sad ukljucen (status-aware u _enforce_off, bez rizika da upali)
                    if s.get("control", "") not in ("sony", "http", "roku", "lg", "samsung"):
                        continue
                    if now - s.get("_last_enforce", 0) < ENFORCE_INTERVAL:
                        continue
                    with _lock:
                        st = _state["stations"].get(sid)
                        if st:
                            st["_last_enforce"] = now
                    threading.Thread(target=_enforce_off, args=(sid,), daemon=True).start()
        except Exception as e:
            print("enforce error:", e)
        time.sleep(1)


# ──────────────────────────────────────────────────────────
#  HTTP handler
# ──────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # tiho

    def _send(self, obj, status=200, ctype="application/json"):
        if ctype == "application/json":
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        else:
            body = obj if isinstance(obj, bytes) else str(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            try:
                with open(os.path.join(BASE, "index.html"), "rb") as f:
                    self._send(f.read(), ctype="text/html")
            except Exception:
                self._send("index.html nije pronađen", status=500, ctype="text/plain")
            return
        if path == "/api/stations":
            with _lock:
                data = [station_public(sid, s) for sid, s in _state["stations"].items()]
            data.sort(key=lambda x: x["name"])
            self._send({"stations": data})
            return
        if path == "/api/discover":
            try:
                devices = discover_devices()
                self._send({"devices": devices})
            except Exception as e:
                self._send({"devices": [], "error": str(e)[:120]})
            return
        if path == "/api/packages":
            with _lock:
                self._send({"packages": _state.get("packages", []),
                            "price_per_hour": _state.get("price_per_hour", 0)})
            return
        if path == "/api/version":
            self._send({"version": VERSION})
            return
        if path == "/api/enforce":
            with _lock:
                self._send({"on": _state.get("enforce_remote", True)})
            return
        if path == "/api/update/check":
            try:
                self._send(check_update())
            except Exception as e:
                self._send({"current": VERSION, "newer": False, "error": str(e)[:120]})
            return
        if path == "/api/agent":
            # PC agent pita za svoje stanje. ?id=<sid>
            q = dict(p.split("=") for p in self.path.split("?")[1].split("&")) if "?" in self.path else {}
            sid = q.get("id", "")
            with _lock:
                s = _state["stations"].get(sid)
                if not s:
                    self._send({"ok": False, "locked": True, "error": "nema stanice"})
                    return
                now = time.time()
                active = (s.get("status") == "ACTIVE" and s.get("ends_at") and now < s["ends_at"])
                remaining = int(s["ends_at"] - now) if active else 0
                # zabilježi heartbeat
                s["agent_seen"] = now
                self._send({
                    "ok": True,
                    "name": s.get("name", ""),
                    "status": s.get("status", "IDLE"),
                    "locked": (not active),
                    "remaining": max(0, remaining),
                })
            return
        if path == "/api/summary":
            q = dict(p.split("=") for p in self.path.split("?")[1].split("&")) if "?" in self.path else {}
            with _lock:
                self._send(daily_summary(q.get("date")))
            return
        if path == "/api/report":
            q = dict(p.split("=") for p in self.path.split("?")[1].split("&")) if "?" in self.path else {}
            with _lock:
                self._send(daily_by_station(q.get("date")))
            return
        if path == "/api/history":
            q = dict(p.split("=") for p in self.path.split("?")[1].split("&")) if "?" in self.path else {}
            date = q.get("date", today_str())
            with _lock:
                rows = [h for h in _state.get("history", []) if (date == "all" or h.get("date") == date)]
            rows = sorted(rows, key=lambda x: x.get("ts", 0), reverse=True)[:500]
            self._send({"history": rows, "summary": daily_summary(None if date == "all" else date)})
            return
        if path == "/api/history.csv":
            q = dict(p.split("=") for p in self.path.split("?")[1].split("&")) if "?" in self.path else {}
            date = q.get("date", today_str())
            with _lock:
                rows = [h for h in _state.get("history", []) if (date == "all" or h.get("date") == date)]
            rows = sorted(rows, key=lambda x: x.get("ts", 0))
            lines = ["Datum,Vrijeme,Stanica,Minuti,Iznos(EUR),Tip"]
            for h in rows:
                nm = str(h.get("station", "")).replace(",", " ")
                lines.append(f"{h.get('date','')},{h.get('time','')},{nm},{h.get('minutes',0)},{h.get('amount',0):.2f},{h.get('kind','')}")
            csv = "\r\n".join(lines)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="pazar-{date}.csv"')
            body = csv.encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send({"error": "not found"}, status=404)

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_json()

        if path == "/api/stations":  # dodaj ili izmijeni
            sid = body.get("id") or uuid.uuid4().hex[:8]
            with _lock:
                s = _state["stations"].get(sid, {"status": "IDLE", "tv_on": False})
                s.update({
                    "name": body.get("name", s.get("name", "Stanica")),
                    "control": body.get("control", s.get("control", "sony")),
                    "ip": body.get("ip", s.get("ip", "")),
                    "mac": body.get("mac", s.get("mac", "")),
                    "psk": body.get("psk", s.get("psk", "")),
                    "brand": body.get("brand", s.get("brand", "SONY")),
                    "on_url": body.get("on_url", s.get("on_url", "")),
                    "off_url": body.get("off_url", s.get("off_url", "")),
                    "price_per_hour": float(body.get("price_per_hour", s.get("price_per_hour", 0)) or 0),
                })
                _state["stations"][sid] = s
                save_state()
                out = station_public(sid, s)
            self._send({"ok": True, "station": out})
            return

        if path == "/api/enforce":
            with _lock:
                _state["enforce_remote"] = bool(body.get("on", True))
                save_state()
                on = _state["enforce_remote"]
            self._send({"ok": True, "on": on})
            return

        if path == "/api/quit":
            self._send({"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return

        if path == "/api/update/apply":
            url = body.get("url", "")
            if not url:
                self._send({"ok": False, "error": "nema URL-a"}); return
            try:
                apply_update(url)
                self._send({"ok": True})
            except Exception as e:
                self._send({"ok": False, "error": str(e)[:160]})
            return

        if path == "/api/packages":
            pkgs = body.get("packages", [])
            clean = []
            for p in pkgs:
                try:
                    clean.append({"name": str(p.get("name", "")).strip() or "Paket",
                                  "minutes": int(float(p.get("minutes", 60))),
                                  "price": round(float(p.get("price", 0) or 0), 2)})
                except Exception:
                    continue
            with _lock:
                _state["packages"] = clean
                if "price_per_hour" in body:
                    try:
                        _state["price_per_hour"] = round(float(body.get("price_per_hour") or 0), 2)
                    except Exception:
                        pass
                save_state()
                pph = _state.get("price_per_hour", 0)
            self._send({"ok": True, "packages": clean, "price_per_hour": pph})
            return

        if path.startswith("/api/stations/") and path.endswith("/open"):
            # SLOBODNO — otvorena sesija (sat kuca prema gore, placa se na kraju)
            sid = path.split("/")[3]
            with _lock:
                s = _state["stations"].get(sid)
                if not s:
                    self._send({"error": "nema stanice"}, status=404); return
                s["status"] = "ACTIVE"
                s["open"] = True
                s["started_at"] = time.time()
                s["ends_at"] = None
                s["paid"] = 0
                s["session_minutes"] = 0
                s["_no_enforce_until"] = time.time() + 5
                ok, msg = control_tv(s, True)
                s["tv_on"] = True
                s["last_action"] = "Slobodno — počelo (kuca)"
                save_state()
                out = station_public(sid, s)
            self._send({"ok": True, "station": out})
            return

        if path.startswith("/api/stations/") and path.endswith("/start"):
            sid = path.split("/")[3]
            minutes = float(body.get("minutes", 60) or 60)
            with _lock:
                s = _state["stations"].get(sid)
                if not s:
                    self._send({"error": "nema stanice"}, status=404); return
                amount = body.get("amount")
                if amount is None:
                    amount = round((minutes / 60.0) * float(s.get("price_per_hour", 0) or 0), 2)
                amount = round(float(amount or 0), 2)
                s["status"] = "ACTIVE"
                s["ends_at"] = time.time() + minutes * 60
                s["paid"] = amount
                s["session_minutes"] = int(minutes)
                s["_no_enforce_until"] = time.time() + 5  # cuvar ne dira dok se pali
                ok, msg = control_tv(s, True)
                s["tv_on"] = True
                s["last_action"] = f"Start {int(minutes)}min · {amount:.2f}€ (TV ON)"
                record_payment(sid, s.get("name", ""), minutes, amount, "start")
                save_state()
                out = station_public(sid, s)
            self._send({"ok": True, "station": out})
            return

        if path.startswith("/api/stations/") and path.endswith("/extend"):
            sid = path.split("/")[3]
            minutes = float(body.get("minutes", 30) or 30)
            with _lock:
                s = _state["stations"].get(sid)
                if not s:
                    self._send({"error": "nema stanice"}, status=404); return
                amount = body.get("amount")
                if amount is None:
                    amount = round((minutes / 60.0) * float(s.get("price_per_hour", 0) or 0), 2)
                amount = round(float(amount or 0), 2)
                base = s.get("ends_at") if (s.get("status") == "ACTIVE" and s.get("ends_at", 0) > time.time()) else time.time()
                s["ends_at"] = base + minutes * 60
                s["status"] = "ACTIVE"
                s["paid"] = round(float(s.get("paid", 0) or 0) + amount, 2)
                s["session_minutes"] = int(s.get("session_minutes", 0) or 0) + int(minutes)
                s["_no_enforce_until"] = time.time() + 5  # cuvar ne dira dok se pali
                if not s.get("tv_on"):
                    control_tv(s, True)
                    s["tv_on"] = True
                s["last_action"] = f"Doplata +{int(minutes)}min · {amount:.2f}€"
                record_payment(sid, s.get("name", ""), minutes, amount, "extend")
                save_state()
                out = station_public(sid, s)
            self._send({"ok": True, "station": out})
            return

        if path.startswith("/api/stations/") and path.endswith("/stop"):
            sid = path.split("/")[3]
            with _lock:
                s = _state["stations"].get(sid)
                if not s:
                    self._send({"error": "nema stanice"}, status=404); return
                charged = 0.0
                charged_min = 0
                if s.get("open") and s.get("started_at"):
                    elapsed = max(0, time.time() - s["started_at"])
                    charged_min = int(round(elapsed / 60.0))
                    pph = effective_pph(s)
                    charged = round(elapsed / 3600.0 * pph, 2)
                    record_payment(sid, s.get("name", ""), charged_min, charged, "open")
                ok, msg = control_tv(s, False)
                s["status"] = "IDLE"
                s["open"] = False
                s["started_at"] = None
                s["ends_at"] = None
                s["tv_on"] = False
                if charged_min:
                    s["last_action"] = f"Slobodno gotovo: {charged_min} min · {charged:.2f}€"
                else:
                    s["last_action"] = f"Stop (TV OFF: {msg})"
                save_state()
                out = station_public(sid, s)
            self._send({"ok": True, "station": out, "charged": charged, "charged_min": charged_min})
            return

        if path.startswith("/api/stations/") and path.endswith("/tv"):
            sid = path.split("/")[3]
            action = body.get("action", "test")  # on, off, test
            with _lock:
                s = _state["stations"].get(sid)
                if not s:
                    self._send({"error": "nema stanice"}, status=404); return
                scopy = dict(s)
            if action == "test":
                if scopy.get("control") == "sony":
                    try:
                        st = sony_get_power(scopy["ip"], scopy.get("psk", ""))
                        self._send({"ok": True, "msg": f"Sony status: {st}"})
                    except Exception as e:
                        self._send({"ok": False, "msg": f"Greška: {str(e)[:80]}"})
                elif scopy.get("control") == "vidaa":
                    if vidaa is None:
                        self._send({"ok": False, "msg": "VIDAA modul nedostupan"})
                    else:
                        st = vidaa.get_state(scopy.get("ip", ""))
                        self._send({"ok": bool(st), "msg": f"VIDAA status: {st or 'nedostupan'}"})
                else:
                    self._send({"ok": True, "msg": "Test dostupan za Sony i VIDAA"})
                return
            if action in ("pair", "pin"):
                # Hisense VIDAA uparivanje: 'pair' izazove PIN, 'pin' ga posalje
                if vidaa is None:
                    self._send({"ok": False, "msg": "VIDAA modul nedostupan"}); return
                ip = scopy.get("ip", "")
                if action == "pair":
                    ok, msg = vidaa.pair_start(ip)
                else:
                    ok, msg = vidaa.pair_pin(ip, str(body.get("code", "")).strip())
                self._send({"ok": ok, "msg": msg})
                return
            on = (action == "on")
            ok, msg = control_tv(scopy, on)  # scopy može dobiti token/key
            with _lock:
                s = _state["stations"].get(sid)
                if s:
                    s["tv_on"] = on
                    # sačuvaj eventualni novi token/key sa uparivanja
                    if scopy.get("samsung_token"):
                        s["samsung_token"] = scopy["samsung_token"]
                    if scopy.get("lg_key"):
                        s["lg_key"] = scopy["lg_key"]
                    save_state()
            self._send({"ok": ok, "msg": msg})
            return

        self._send({"error": "not found"}, status=404)

    # ---- DELETE ----
    def do_DELETE(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/stations/"):
            sid = path.split("/")[3]
            with _lock:
                if sid in _state["stations"]:
                    del _state["stations"][sid]
                    save_state()
            self._send({"ok": True})
            return
        self._send({"error": "not found"}, status=404)


# ──────────────────────────────────────────────────────────
#  Auto-update (GitHub Releases)
# ──────────────────────────────────────────────────────────
def _ver_tuple(v):
    v = str(v).lstrip("vV").strip()
    parts = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def update_channel():
    """Kanal ažuriranja:
      - 'dev'  -> pokupi i PRE-RELEASE (za nas, testiranje)
      - 'stable' (podrazumijevano) -> samo pravi release-ovi (za igraonice u radu)
    Dev se uključi: env GAMEZONE_CHANNEL=dev  ILI fajl 'dev-channel' u APP_DIR."""
    try:
        if os.environ.get("GAMEZONE_CHANNEL", "").lower() in ("dev", "beta", "test"):
            return "dev"
        if os.path.exists(os.path.join(APP_DIR, "dev-channel")):
            return "dev"
    except Exception:
        pass
    return "stable"


def _pick_asset(rel):
    for a in rel.get("assets", []):
        nm = a.get("name", "").lower()
        if nm.endswith(".exe"):
            url = a.get("browser_download_url", "")
            if "setup" in nm:
                return url
    for a in rel.get("assets", []):
        if a.get("name", "").lower().endswith(".exe"):
            return a.get("browser_download_url", "")
    return ""


def check_update():
    """Vrati info o najnovijoj verziji sa GitHub-a (po kanalu)."""
    ch = update_channel()
    if ch == "dev":
        # Sve verzije (uklj. pre-release) -> uzmi najnoviju objavljenu
        url = f"https://api.github.com/repos/{UPDATE_REPO}/releases?per_page=15"
    else:
        # Samo pravi release-ovi (GitHub /latest preskače pre-release)
        url = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": "GameZone-Updater",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))

    if ch == "dev":
        rels = [x for x in data if not x.get("draft")]
        rel = rels[0] if rels else {}
    else:
        rel = data

    latest = rel.get("tag_name", "0.0.0")
    notes = rel.get("body", "") or ""
    asset_url = _pick_asset(rel)
    newer = _ver_tuple(latest) > _ver_tuple(VERSION)
    return {
        "current": VERSION, "latest": latest.lstrip("vV"),
        "newer": newer, "url": asset_url, "notes": notes[:500],
        "channel": ch,
    }


def apply_update(asset_url):
    """Preuzmi installer i pokreni ga preko .bat skripte koja PRVO sigurno ugasi
    aplikaciju (da nema 'fajl zauzet / try again'), pa tiho instalira i ponovo upali."""
    tmp = os.environ.get("TEMP", APP_DIR)
    dest = os.path.join(tmp, "GameZone-Update.exe")
    req = urllib.request.Request(asset_url, headers={"User-Agent": "GameZone-Updater"})
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)

    # Skini "Mark of the Web" (skinuto sa interneta) da SmartScreen ne blokira
    try:
        os.remove(dest + ":Zone.Identifier")
    except Exception:
        pass

    # .bat: sacekaj da se app ugasi -> nasilno ugasi ostatak -> tiho instaliraj
    bat = os.path.join(tmp, "gamezone-update.bat")
    exe_name = os.path.basename(sys.executable) if getattr(sys, "frozen", False) else "GameZone-TV.exe"
    try:
        with open(bat, "w", encoding="ascii", errors="ignore") as f:
            f.write("@echo off\r\n")
            f.write("timeout /t 2 /nobreak >nul\r\n")
            f.write('taskkill /F /IM "%s" >nul 2>&1\r\n' % exe_name)
            f.write("timeout /t 1 /nobreak >nul\r\n")
            f.write('"%s" /SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /NOCANCEL\r\n' % dest)
    except Exception as e:
        print("update bat err:", e)

    def _run_and_exit():
        time.sleep(0.5)
        try:
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(["cmd", "/c", bat], close_fds=True, creationflags=flags)
        except Exception as e:
            print("update launch err:", e)
            try:
                subprocess.Popen([dest, "/SILENT"], close_fds=True)
            except Exception:
                pass
        os._exit(0)

    threading.Thread(target=_run_and_exit, daemon=True).start()
    return dest


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    load_state()
    # Zauzmi port; ako je zauzet -> probaj par sekundi (npr. poslije update-a kad se
    # stara instanca gasi). Tek ako TRAJNO ne uspije -> druga instanca radi, otvori browser.
    server = None
    for _ in range(8):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
            break
        except OSError:
            time.sleep(1.0)
    if server is None:
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        except Exception:
            pass
        return

    threading.Thread(target=timer_loop, daemon=True).start()
    threading.Thread(target=enforce_loop, daemon=True).start()
    ip = lan_ip()
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 56)
    print("  GAMEZONE - IGRAONICA")
    print(f"  Pult (ovaj racunar): http://127.0.0.1:{PORT}")
    print(f"  Sa drugih uredjaja:  http://{ip}:{PORT}")
    print(f"  PC agenti se javljaju na:  {ip}:{PORT}")
    print(f"  Podaci: {DATA_FILE}")
    print("=" * 56)
    # auto-otvori browser na pultu
    try:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    except Exception:
        pass
    # server je već zauzeo port gore (bind 0.0.0.0 — agenti/telefon mogu da se povežu)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
