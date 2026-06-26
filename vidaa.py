"""Hisense VIDAA (RemoteNOW) kontrola preko MQTT/TLS na portu 36669.

Zahtijeva klijentski sertifikat (mutual TLS): rcm_certchain_pem.cer + rcm_pem_privkey.pkcs8
(javni, izvuceni iz RemoteNOW app-a). KEY_POWER je TOGGLE -> power() je status-aware
(cita ui_service/state: 'fake_sleep*' = ugasen) da cuvar ne upali vec ugasen TV.

Pairing: noviji modeli traze 4-cifreni PIN. Tok: pair_start() (TV pokaze PIN) ->
pair_pin(code). Uparenje TV pamti (vezano za device-topic + cert), pa kasnije
send_key/power rade bez ponovnog PIN-a.
"""
import json
import os
import ssl
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt

PORT = 36669
USER = "hisenseservice"
PASS = "multimqttservice"
# "mobilni" device-topic; uparenje se vezuje za njega (+ cert), ne za client_id
DEVICE = "XX:XX:XX:XX:XX:XY$normal"

T_SENDKEY = f"/remoteapp/tv/remote_service/{DEVICE}/actions/sendkey"
T_UI = f"/remoteapp/tv/ui_service/{DEVICE}/actions/%s"
T_SUB = f"/remoteapp/mobile/{DEVICE}/#"
T_SUB_BCAST = "/remoteapp/mobile/broadcast/#"


def _base_dir():
    # PyInstaller pakuje data fajlove u _MEIPASS; u dev-u su pored ovog modula
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _cert_paths():
    b = _base_dir()
    return (os.path.join(b, "rcm_certchain_pem.cer"),
            os.path.join(b, "rcm_pem_privkey.pkcs8"))


def available():
    """True ako su cert/kljuc prisutni (paho se importuje na vrhu)."""
    cert, key = _cert_paths()
    return os.path.exists(cert) and os.path.exists(key)


def _ssl_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    except ssl.SSLError:
        pass
    cert, key = _cert_paths()
    ctx.load_cert_chain(cert, key)
    return ctx


def _new_client():
    cid = "HisenseTv-" + uuid.uuid4().hex[:8]
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid)
    except (AttributeError, TypeError):
        c = mqtt.Client(client_id=cid)  # paho 1.x
    c.username_pw_set(USER, PASS)
    c.tls_set_context(_ssl_ctx())
    return c


def _is_off(statetype):
    if not statetype:
        return False
    st = str(statetype).lower()
    if st.startswith("fake_sleep") or "standby" in st or "powered_off" in st:
        return True
    # nepoznat statetype -> loguj da naucimo prave vrijednosti (tretiramo kao 'nije off')
    if st not in ("on", "normal", "live", "app"):
        print("VIDAA nepoznat statetype:", statetype)
    return False


class _Session:
    """Jedna MQTT veza ka TV-u; skuplja stanje i odgovore."""

    def __init__(self, ip):
        self.ip = ip
        self.connected = threading.Event()
        self.state = None        # zadnji statetype
        self.auth_result = None  # odgovor na authenticationcode
        self.c = _new_client()
        self.c.on_connect = self._on_connect
        self.c.on_message = self._on_message

    def _on_connect(self, cl, ud, flags, rc):
        if rc == 0:
            cl.subscribe(T_SUB)
            cl.subscribe(T_SUB_BCAST)
            self.connected.set()

    def _on_message(self, cl, ud, msg):
        p = msg.payload.decode("utf-8", "ignore")
        if "ui_service/state" in msg.topic:
            try:
                self.state = json.loads(p).get("statetype", self.state)
            except Exception:
                pass
        elif "authenticationcode" in msg.topic:
            try:
                self.auth_result = json.loads(p)
            except Exception:
                self.auth_result = {"raw": p}

    def open(self, timeout=8):
        self.c.connect(self.ip, PORT, keepalive=30)
        self.c.loop_start()
        return self.connected.wait(timeout)

    def close(self):
        try:
            self.c.loop_stop()
        except Exception:
            pass
        try:
            self.c.disconnect()
        except Exception:
            pass


def get_state(ip, timeout=6):
    """Vrati 'on' / 'off' / '' (nepoznato)."""
    s = _Session(ip)
    try:
        if not s.open(timeout):
            return ""
        # state broadcast obicno stigne odmah po konekciji
        t0 = time.time()
        while s.state is None and time.time() - t0 < 3:
            time.sleep(0.1)
        if s.state is None:
            s.c.publish(T_UI % "gettvstate", "")
            t0 = time.time()
            while s.state is None and time.time() - t0 < 3:
                time.sleep(0.1)
        if s.state is None:
            return ""
        return "off" if _is_off(s.state) else "on"
    except Exception:
        return ""
    finally:
        s.close()


def send_key(ip, key="KEY_POWER", timeout=8):
    s = _Session(ip)
    try:
        if not s.open(timeout):
            return False
        s.c.publish(T_SENDKEY, key)
        time.sleep(0.6)
        return True
    except Exception:
        return False
    finally:
        s.close()


def power(ip, on, timeout=8):
    """Status-aware paljenje/gasenje. KEY_POWER (toggle) salje SAMO ako treba.
    Vraca (ok, poruka)."""
    st = get_state(ip, timeout)
    if st == "":
        if not on:
            # OFF + nepoznato stanje: NE diraj (KEY_POWER je toggle -> mogli bismo upaliti ugasen TV)
            return True, "VIDAA: nedostupan/ugašen - ne diram"
        ok = send_key(ip, "KEY_POWER", timeout)
        return ok, "VIDAA: stanje nepoznato, poslat KEY_POWER"
    cur_on = (st == "on")
    if on and cur_on:
        return True, "VIDAA: vec upaljen"
    if (not on) and (not cur_on):
        return True, "VIDAA: vec ugasen"
    ok = send_key(ip, "KEY_POWER", timeout)
    return ok, ("VIDAA ON" if on else "VIDAA OFF")


# ── Pairing: drzi otvorenu sesiju izmedju 'pair_start' i 'pair_pin' ──
_pending = {}            # ip -> (_Session, ts)  (ts = kad je sesija otvorena)
_pending_lock = threading.Lock()


def reap_pending(max_age=90):
    """Zatvori i izbaci pending sesije starije od max_age sekundi (ako pair_pin nikad
    ne stigne). Sprjecava curenje niti/socketa. Zove se periodicno iz servera."""
    now = time.time()
    stale = []
    with _pending_lock:
        for ip, (s, ts) in list(_pending.items()):
            if now - ts > max_age:
                stale.append(_pending.pop(ip)[0])
    for s in stale:
        try:
            s.close()
        except Exception:
            pass


def pair_start(ip, timeout=8):
    """Otvori sesiju i izazovi PIN na TV-u. Vrati (ok, poruka)."""
    with _pending_lock:
        old = _pending.pop(ip, None)
    if old:
        old[0].close()
    s = _Session(ip)
    if not s.open(timeout):
        s.close()
        return False, "Nema MQTT konekcije (cert/mreza/port 36669?)"
    s.c.publish(T_UI % "gettvstate", "")
    time.sleep(0.4)
    s.c.publish(T_UI % "authentication", "")
    with _pending_lock:
        _pending[ip] = (s, time.time())
    return True, "PIN bi trebao da se pojavi na TV-u — unesi ga"


def pair_pin(ip, code, timeout=8):
    """Posalji PIN u VEC otvorenoj sesiji (od pair_start). Vrati (ok, poruka)."""
    with _pending_lock:
        entry = _pending.get(ip)
    if not entry:
        return False, "Nema aktivnog uparivanja — prvo klikni 'Upari'"
    s = entry[0]
    s.auth_result = None
    s.c.publish(T_UI % "authenticationcode", json.dumps({"authNum": str(code)}))
    t0 = time.time()
    while s.auth_result is None and time.time() - t0 < timeout:
        time.sleep(0.1)
    res = s.auth_result
    with _pending_lock:
        _pending.pop(ip, None)
    s.close()
    if res and str(res.get("result")) == "1":
        return True, "Upareno!"
    return False, f"Uparivanje neuspjelo: {res}"
