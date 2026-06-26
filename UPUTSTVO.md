# Igraonica TV Tajmer — uputstvo

Lokalni alat za igraonicu: kad istekne plaćeno vrijeme, **TV se sam ugasi preko mreže**.
Doplata = produženje. Radi na Windows pultu, bez interneta, bez naloga.

## Pokretanje
- Dupli klik na **`start.bat`** → otvara se `http://127.0.0.1:8770` u browseru.
- (Ako nema Pythona na tom računaru — javi pa spakujem u `.exe` za flešku.)

## Kako naći TV-ove na mreži (AUTOMATSKI)
- Klikni **🔍 Skeniraj mrežu** → softver sam nađe TV-ove (IP + MAC + brend) → klikni **Dodaj** kod svakog.
- Za Sony još dodaj **PSK** (vidi dolje) pa **Sačuvaj**.
- Bitno: TV mora biti **upaljen** i na **istoj mreži** (isti Wi-Fi/ruter) kao računar.

### Ručno (ako skener ne nađe baš taj TV)
- **Na Sony TV-u:** Postavke → Mreža → **Status mreže** → piše IP adresa (npr. 192.168.1.50).
- **Preko rutera:** otvori ruter (192.168.1.1) → lista uređaja (DHCP klijenti) → nađi TV.
- Daj TV-u **fiksnu IP** (rezervacija po MAC-u na ruteru) da se ne mijenja.

## Kako se koristi
1. **🔍 Skeniraj mrežu** (ili **+ Dodaj stanicu** ručno) za svaki TV/PlayStation.
2. Kad dijete dođe: klikni **30 min** ili **1 sat** → TV se pali, kreće odbrojavanje.
3. Kad istekne → TV se **sam gasi**.
4. Doplati → **+30 min / +1 h / +…** (TV ostaje/pali se nazad).
5. **Stop** ranije → gasi TV odmah.

## Podešavanje Sony Bravia TV-a (mrežno gašenje)
Na svakom TV-u uradi jednom:
1. **Postavke → Mreža → Postavke kućne mreže → IP kontrola**
   - **Autentifikacija** → izaberi **„Normal i Pre-Shared Key"**
   - **Pre-Shared Key (PSK)** → upiši ključ (npr. `0000`) — isti taj uneseš u softver.
   - **Simple IP Control** → **Uključeno**
2. **Remote Start / Daljinsko pokretanje** → **Uključeno** (da paljenje preko mreže radi).
3. Daj TV-u **fiksnu IP adresu** (na ruteru — DHCP rezervacija po MAC-u), da se ne mijenja.
4. U softveru, kod stanice: tip **Sony Bravia**, unesi **IP** + **PSK** (+ **MAC** za paljenje).
5. Klikni **Test** na kartici — treba da vrati status TV-a.

> Napomena: PlayStation ostaje upaljen, ali bez slike (TV ugašen) niko ne može da igra.
> Ako želiš da se i konzola gasi — to ide preko pametne utičnice (HTTP režim).

## 💻 RAČUNARI (gaming PC) — zaključavanje agentom
Za **računare** (ne TV) ide mali program **agent** na svaki PC. Kad istekne vrijeme → **zaključa ekran**; start/doplata sa pulta → otključa.

**Na pultu (ovaj računar):** server već radi i sluša na cijeloj mreži. Adresa pulta je npr. `http://192.168.1.230:8770` (vidiš je u crnom prozoru kad upališ server). Dozvoli u Windows Firewall-u port 8770 ako pita.

**Na svakom gaming računaru (jednom):**
1. Kopiraj folder **`pc-agent`** na taj PC (ili sa fleške).
2. U GameZone-u na pultu dodaj stanicu, tip **„💻 Računar / PC"**, daj joj ime (npr. „PC 1").
3. Na tom PC-u pokreni **`start-agent.bat`** → otvori se podešavanje:
   - upiši adresu pulta (npr. `http://192.168.1.230:8770`)
   - klikni **Učitaj stanice** → izaberi „PC 1" → **Sačuvaj i pokreni**.
4. Gotovo. Agent radi u pozadini. Kad istekne vrijeme → ekran se zaključa („Vrijeme isteklo — javite se na pult"). Start/doplata sa pulta → otključa.

**Da se sam pali pri uključenju PC-a:** napravi prečicu od `start-agent.bat` u Startup folder (`shell:startup`).
> Napomena: agent prekrije ekran (kao internet-café). Ako server (pult) padne, agent NE zaključava (da ne blokira PC zbog mreže). Za 100% čvrsto zaključavanje kasnije mogu dodati i pravo Windows zaključavanje/odjavu.

## Drugi tipovi kontrole
- **Hisense/TCL (Roku)** — samo IP (port 8060).
- **Univerzalni HTTP** — unese se URL za paljenje i gašenje. Radi sa **Shelly/Tasmota pametnom utičnicom** (ugasi struju cijeloj stanici — TV + konzola). Najpouzdanije ako TV pravi problem.
- **Samo WoL** — samo paljenje preko mreže.

## Podaci
- Sve stanice se čuvaju u `stations.json` (pored programa). Backup = kopiraj taj fajl.

## Port
- Radi na `8770`. Ako treba drugi, promijeni `PORT` u `server.py`.
