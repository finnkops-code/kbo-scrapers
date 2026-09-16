"""
KBO-scraper — team standings (klassement).
Bron: https://eng.koreabaseball.com/ (dezelfde homepage als
      kbo_schedule_scraper.py — de standings-tabel staat in een tweede,
      onafhankelijke widget op diezelfde pagina: <div class="tit_standing">
      met daarin één <table> met kolommen RK/TEAM/W/L/D/PCT). Live
      geverifieerd via de browser: deze standings-tabel stond woordelijk
      hetzelfde in de pagina als hier beschreven, dus zie
      kbo_schedule_scraper.py voor de bevestiging dat dit gewone
      server-gerenderde HTML is (geen Playwright nodig, geen cookies
      vereist) — dezelfde situatie geldt hier vanzelfsprekend ook, want het
      is letterlijk dezelfde pagina.

Elke rij bestaat, in tegenstelling tot een "gewone" tabel, uit TWEE
<th>-cellen gevolgd door vier <td>-cellen: de rang (RK) én de teamnaam
(TEAM) staan allebei in een <th> (de teamnaam-cel heeft zelfs
scope="row"), i.p.v. dat alleen de rang een th is en de rest td's zijn.
We lezen daarom voor elke rij gewoon ALLE th+td-cellen samen op, in
DOM-volgorde, i.p.v. ervan uit te gaan dat de teamnaam per se in een <td>
zit.

De kolomkoppen zelf (RK, TEAM, W, L, D, PCT) lezen we dynamisch uit de
<thead> uit (net als bij de andere scrapers in dit project) i.p.v. hard te
coderen, zodat de scraper blijft werken als de site ooit een kolom
toevoegt of hernoemt (bv. een GB- of STRK-kolom, die deze compacte
homepage-widget nu niet toont — dat is dus bewust niet in deze data
aanwezig).

De class "light_g" op de helft van de rijen is puur zebra-striping voor de
opmaak (geen won/lost-indicatie of iets dergelijks) en heeft geen invloed
op het parsen.

Net als bij kbo_schedule_scraper.py blijft een celwaarde met een
"."-teken (hier alleen "PCT", bv. "0.623") bewust een string; puur
numerieke kolommen (RK, W, L, D) worden naar int omgezet. TEAM blijft
vanzelf een string (geen cijfers). In tegenstelling tot de NPB-
standenscraper is er hier geen league-veld nodig: de KBO kent, in
tegenstelling tot NPB (Central/Pacific), maar één enkele league van
10 teams.
"""
import datetime as dt
import json
import re
import time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

HOMEPAGE_URL = "https://eng.koreabaseball.com/"
JSON_FILE = "kbo_standings.json"
KBO_TZ = "Asia/Seoul"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def haal_pagina_op(url, timeout=20):
    """Haalt de pagina op als platte HTML-tekst. Geen Playwright nodig (zie
    de moduledocstring): dit is gewone, server-gerenderde HTML."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


def slugify(header: str) -> str:
    """Zet een kolomkop uit de site ("RK", "PCT") om naar een nette,
    stabiele JSON-sleutel ("rk", "pct")."""
    return re.sub(r"\s+", "_", header.strip().lower())


def parse_standen_tabel(tabel):
    """Zet de standings-<table> om naar een lijst van team-dicts, in de
    volgorde waarin ze op de site staan (dus al gesorteerd op klassement)."""
    header_ths = tabel.select("thead th")
    sleutels = [slugify(th.get_text(strip=True)) for th in header_ths]

    rijen = []
    for tr in tabel.select("tbody tr"):
        # Rang én teamnaam zitten allebei in een <th> (zie moduledocstring),
        # dus lezen we th+td gezamenlijk uit i.p.v. alleen td's.
        cellen = tr.find_all( [ "th", "td" ] )
        if len(cellen) != len(sleutels):
            continue  # onverwachte rij-vorm: overslaan i.p.v. crashen
        rij = {}
        for sleutel, cel in zip(sleutels, cellen):
            waarde = cel.get_text(strip=True)
            if waarde.lstrip("-").isdigit():
                waarde = int(waarde)
            rij[sleutel] = waarde
        rijen.append(rij)
    return rijen


def haal_standen_op():
    """Haalt de homepage op en ontleedt de standings-widget naar een lijst
    van team-dicts."""
    html = haal_pagina_op(HOMEPAGE_URL)
    soup = BeautifulSoup(html, "html.parser")

    widget = soup.select_one("div.tit_standing")
    if widget is None:
        raise RuntimeError("standings-widget (div.tit_standing) niet gevonden op de homepage")

    tabel = widget.select_one("table")
    if tabel is None:
        raise RuntimeError("geen <table> gevonden in de standings-widget")

    standen = parse_standen_tabel(tabel)
    if not standen:
        raise RuntimeError("standings-tabel gevonden maar geen rijen ontleed")
    return standen


def main():
    pogingen = 3
    laatste_fout = None
    standen = []

    for poging in range(1, pogingen + 1):
        try:
            standen = haal_standen_op()
            print(f"{len(standen)} teams gevonden (poging {poging}/{pogingen})")
            break
        except Exception as e:
            laatste_fout = e
            print(f"Poging {poging}/{pogingen} mislukt: {e}")
            if poging < pogingen:
                time.sleep(5)
    else:
        raise RuntimeError(f"Alle {pogingen} pogingen mislukt: {laatste_fout}")

    output = {
        "bijgewerkt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": HOMEPAGE_URL,
        "season": dt.datetime.now(ZoneInfo(KBO_TZ)).year,
        "standings": standen,
    }
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n{JSON_FILE} geschreven. {len(standen)} teams.")


if __name__ == "__main__":
    main()
