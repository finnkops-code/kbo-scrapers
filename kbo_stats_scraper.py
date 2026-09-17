"""
KBO-scraper — spelersstatistieken (batting + pitching), voor alle teams.
Bron: https://eng.koreabaseball.com/Stats/BattingByTeams.aspx?codeTeam=<code>
      en https://eng.koreabaseball.com/Stats/PitchingByTeams.aspx?codeTeam=<code>
      — per team een eigen pagina (in tegenstelling tot kbo_schedule_scraper.py
      en kbo_standings_scraper.py, die allebei van dezelfde homepage lezen).
      Live geverifieerd dat dit gewone server-gerenderde HTML is (zelfde
      situatie als de andere twee scrapers): geen Playwright, geen cookies.

Net als kbo_standings_scraper.py wordt de tabel generiek uitgelezen i.p.v.
met hardgecodeerde kolomnamen, maar hier op een nóg robuustere manier: elke
<td> heeft zijn EIGEN kolomnaam als title-attribuut (bv. title="AVG"), dus
die gebruiken we rechtstreeks als JSON-sleutel i.p.v. af te gaan op de
volgorde van de <thead>-kolommen (die blijft alleen als terugvaloptie voor
het geval een cel toch geen title zou hebben). Batting en pitching hebben
compleet verschillende kolomsets (AVG/HR/RBI/... vs ERA/W/L/SV/IP/...) maar
worden hierdoor door dezelfde parse-functie afgehandeld: er is geen aparte
"batting-parser" en "pitching-parser" nodig.

Verdere bewuste verschillen met kbo_standings_scraper.py:
1) 10 teams x 2 statsoorten = 20 losse paginaverzoeken per run (in
   tegenstelling tot de homepage-scrapers, die maar 1 request doen), dus
   krijgt ELK verzoek zijn eigen kleine retry (haal_pagina_op_met_retry)
   i.p.v. dat main() de hele run in zijn geheel opnieuw probeert bij een
   mislukking — anders zou 1 haperende teampagina de andere 19 ook laten
   herhalen. Tussen verzoeken zit een korte pauze (beleefdheid richting de
   server bij 20 verzoeken achter elkaar).
2) De PLAYER-cel bevat een link naar de spelerspagina
   (/teams/playerinfohitter/summary.aspx?pcode=56626) — dat pcode-nummer is
   de eigen stabiele speler-ID van de site, en wordt er apart uitgehaald
   als "pcode" (int), zodat rijen browsbaar/koppelbaar zijn per speler
   (bv. dezelfde speler terugvinden in een volgend seizoen). Bij standings
   en schedule bestaat zoiets niet, want daar gaat het niet om individuele
   spelers.
3) Sommige waarden in de pitching-tabel (IP, "innings pitched") bevatten
   een breuk plus spatie, bv. "154 1/3" — dat is geen zuiver numerieke
   string, dus blijft die vanzelf een string volgens dezelfde
   "alleen puur-numerieke strings worden int"-regel als in de andere
   scrapers (isdigit() na lstrip("-")); er is geen aparte breuk-parsing
   toegevoegd, om geen precisie/afronding te hoeven kiezen die de site zelf
   niet aanbiedt.
4) Elke teampagina toont maar "Page1" van de stats (er staat een
   Page1/Page2-pager op de site, zie de HTML die je stuurde: bv.
   BattingByTeams.aspx vs BattingByTeams02.aspx). Page2 bevat vermoedelijk
   extra sabermetrische kolommen (denk aan OBP/SLG/OPS bij batting, WHIP/K/BB
   bij pitching) die niet in de door jou geplakte HTML zaten — die pagina
   wordt daarom (nog) niet gescraped. Makkelijk uit te breiden zodra
   duidelijk is welke kolommen daar precies staan.
5) Geen apart season/league-veld per rij nodig zoals bij standings (KBO is
   1 league); de TEAM-kolom komt gewoon rechtstreeks uit de site mee per
   rij (bv. "KIA"), dus is er geen eigen team-code-naar-naam-vertaling
   nodig om de output leesbaar te maken — de KBO_TEAM_CODES-lijst hieronder
   dient alleen om de 10 teampagina's te kunnen aanroepen (codeTeam=...),
   niet om iets te vertalen in de output zelf.
"""
import datetime as dt
import json
import re
import time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BATTING_URL = "https://eng.koreabaseball.com/Stats/BattingByTeams.aspx?codeTeam={code}"
PITCHING_URL = "https://eng.koreabaseball.com/Stats/PitchingByTeams.aspx?codeTeam={code}"
JSON_FILE = "kbo_stats.json"
KBO_TZ = "Asia/Seoul"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# De 10 KBO-teamcodes zoals de site ze zelf gebruikt in "codeTeam="
# (dezelfde interne 2-letter codes als de logo-bestandsnamen in
# kbo_schedule_shortcode.php / kbo_standings_shortcode.php — bv. KIA is
# "HT", precies zoals in de door jou geplakte HTML te zien is).
KBO_TEAM_CODES = ["KT", "HH", "LG", "NC", "SK", "LT", "SS", "OB", "WO", "HT"]


def haal_pagina_op(url, timeout=20):
    """Haalt de pagina op als platte HTML-tekst. Geen Playwright nodig (zie
    de moduledocstring): dit is gewone, server-gerenderde HTML."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


def haal_pagina_op_met_retry(url, pogingen=3, pauze=5):
    """Eigen kleine retry per los paginaverzoek (zie punt 1 in de
    moduledocstring: bij 20 verzoeken per run moet 1 hapering niet de hele
    run laten herhalen)."""
    laatste_fout = None
    for poging in range(1, pogingen + 1):
        try:
            return haal_pagina_op(url)
        except Exception as e:
            laatste_fout = e
            print(f"  poging {poging}/{pogingen} mislukt voor {url}: {e}")
            if poging < pogingen:
                time.sleep(pauze)
    raise RuntimeError(f"Alle {pogingen} pogingen mislukt voor {url}: {laatste_fout}")


def slugify(header: str) -> str:
    """Zet een kolomkop uit de site ("AVG", "2B") om naar een nette,
    stabiele JSON-sleutel ("avg", "2b")."""
    return re.sub(r"\s+", "_", header.strip().lower())


def parse_stats_tabel(tabel):
    """Zet een batting- of pitching-<table> om naar een lijst van
    speler-dicts. Werkt voor beide statsoorten ongeacht de kolomset, omdat
    elke kolomnaam rechtstreeks van de cel zelf (title-attribuut) komt i.p.v.
    hardgecodeerd te zijn (zie moduledocstring)."""
    header_ths = tabel.select("thead th")
    terugval_sleutels = [slugify(th.get_text(strip=True)) for th in header_ths]

    rijen = []
    for tr in tabel.select("tbody tr"):
        cellen = tr.find_all("td")
        if not cellen:
            continue
        if len(cellen) != len(terugval_sleutels):
            continue  # onverwachte rij-vorm: overslaan i.p.v. crashen

        rij = {}
        pcode = None
        for i, cel in enumerate(cellen):
            titel = cel.get("title")
            sleutel = slugify(titel) if titel else terugval_sleutels[i]

            if sleutel == "player":
                link = cel.select_one("a.stats_player")
                waarde = link.get_text(strip=True) if link else cel.get_text(strip=True)
                if link and link.get("href"):
                    match = re.search(r"pcode=(\d+)", link["href"])
                    if match:
                        pcode = int(match.group(1))
            else:
                waarde = cel.get_text(strip=True)
                if waarde.lstrip("-").isdigit():
                    waarde = int(waarde)

            rij[sleutel] = waarde

        if pcode is not None:
            rij["pcode"] = pcode
        rijen.append(rij)
    return rijen


def haal_team_stats_op(url_sjabloon, code):
    """Haalt en ontleedt de statstabel van 1 team-pagina (batting of
    pitching, afhankelijk van welke url_sjabloon wordt meegegeven)."""
    url = url_sjabloon.format(code=code)
    html = haal_pagina_op_met_retry(url)
    soup = BeautifulSoup(html, "html.parser")

    tabel = soup.select_one("div.tbl_common.tbl_stats table")
    if tabel is None:
        raise RuntimeError(f"geen stats-tabel gevonden op {url}")

    return parse_stats_tabel(tabel)


def haal_alle_stats_op(pauze_tussen_verzoeken=0.5):
    """Loopt over alle 10 KBO-teams heen en verzamelt zowel batting- als
    pitching-stats van elk team in twee platte lijsten (alle teams door
    elkaar; elke rij heeft z'n eigen "team"-veld al vanuit de site zelf)."""
    batting = []
    pitching = []

    for code in KBO_TEAM_CODES:
        print(f"Batting-stats ophalen voor team {code}...")
        batting.extend(haal_team_stats_op(BATTING_URL, code))
        time.sleep(pauze_tussen_verzoeken)

        print(f"Pitching-stats ophalen voor team {code}...")
        pitching.extend(haal_team_stats_op(PITCHING_URL, code))
        time.sleep(pauze_tussen_verzoeken)

    if not batting:
        raise RuntimeError("geen enkele batting-rij gevonden over alle teams heen")
    if not pitching:
        raise RuntimeError("geen enkele pitching-rij gevonden over alle teams heen")

    return batting, pitching


def main():
    batting, pitching = haal_alle_stats_op()

    output = {
        "bijgewerkt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": "https://eng.koreabaseball.com/Stats/BattingByTeams.aspx",
        "season": dt.datetime.now(ZoneInfo(KBO_TZ)).year,
        "batting": batting,
        "pitching": pitching,
    }
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n{JSON_FILE} geschreven. {len(batting)} batting-rijen, {len(pitching)} pitching-rijen.")


if __name__ == "__main__":
    main()
