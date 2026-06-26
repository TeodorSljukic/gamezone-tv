# GameZone Igraonica — TV tajmer

Lokalni softver za igraonicu: kontroliše Smart TV-ove preko mreže (gasi kad istekne
plaćeno vrijeme), naplata po paketima ili „Slobodno" (po cijeni/sat), pazar i anti-daljinac.
Čist Python (stdlib), bez baze. UI je jedan fajl `index.html`. Server na portu **8770**.

## Pokretanje iz koda (najlakše za testiranje)

Treba **Python 3.10+** (Windows).

```bash
python server.py
```

Otvori se browser na `http://127.0.0.1:8770`. Podaci (stanice, pazar) se čuvaju u
`stations.json` (pored `server.py` kad se pokreće iz koda, ili u `%APPDATA%\GameZone`
kad je spakovano u .exe).

## Build u .exe (PyInstaller)

```bash
pip install pyinstaller
python -m PyInstaller --clean --noconfirm GameZone-TV.spec
# rezultat: dist/GameZone-TV.exe  (jedan fajl, windowed)
```

## Build instalacije (Inno Setup) — opciono

Treba Inno Setup 6 (ISCC.exe).

```bash
ISCC.exe GameZone.iss
# rezultat: installer/GameZone-Setup.exe  (instalacija + auto-update + prečice)
```

## Bitno kad se postavi na DRUGI računar

1. **Windows SmartScreen / antivirus** može da blokira nepotpisan .exe →
   „More info → Run anyway".
2. **Firewall**: dozvoli aplikaciju i port 8770 (pokreni `otvori-port.bat` ili
   `dozvoli-firewall.bat` kao admin), inače skeniranje ne nalazi TV-ove.
3. **Druga mreža = drugi IP-jevi**: TV-ovi imaju druge IP adrese → u programu
   uradi **Skeniraj** pa ponovo dodaj/podesi stanice (ili upiši IP ručno).
   Najbolje: na ruteru daj TV-u **fiksnu IP**.
4. Računar i TV-ovi moraju biti na **istoj mreži** (isti ruter/Wi-Fi).

## Kontrola po brendu

- **Sony Bravia** — REST (`/sony/system`), treba IP Control + Pre-Shared Key + Remote Start.
- **Samsung Tizen** — WebSocket (port 8002, KEY_POWER); status preko `:8001/api/v2/`.
- **LG webOS** — SSAP (port 3000, uparivanje).
- **Roku/Hisense/TCL** — port 8060.
- **Univerzalni HTTP** — upišeš on/off URL (npr. Shelly utičnica).
- **Wake-on-LAN** za paljenje gdje je podržano.

> Napomena (WiFi): kad se TV ugasi „do kraja", neki (npr. Samsung) isključe mrežu u
> standby-ju pa ih ništa ne može probuditi preko mreže — pouzdano paljenje traži LAN
> kabl ili da se TV pali daljincem. Gašenje/anti-daljinac radi pouzdano.

## Fajlovi

- `server.py` — server + sva logika (TV kontrola, sesije, pazar, čuvar, auto-update).
- `index.html` — kompletan UI (jedan fajl).
- `GameZone-TV.spec` — PyInstaller build.
- `GameZone.iss` — Inno Setup instalacija.
- `pc-agent/agent.py` — opcioni zaključavač ekrana za gaming PC-jeve.
