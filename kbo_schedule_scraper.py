"""
KBO-scraper — programma/uitslagen.
Bron: https://eng.koreabaseball.com/ (de homepage). De homepage bevat een
      "schedule"-widget (<div class="tit_schedule">) met daarin drie
      <div class="selec_day_detail">-blokken: gisteren, vandaag en morgen —
      live geverifieerd via de browser, inclusief een kale fetch() zónder
      cookies vanaf een andere origin: dit is gewoon server-gerenderde HTML,
      dus requests + BeautifulSoup volstaan, geen Playwright nodig (zelfde
      situatie als bij de NPB-scrapers van dit project).

In tegenstelling tot npb_schedule_scraper.py — dat per dag een aparte
pagina moest ophalen voor "vandaag" en "gisteren" (dus twee requests) — zit
hier in ÉÉN homepage-fetch al een venster van drie dagen (gisteren/vandaag/
morgen). Er is dus maar één HTTP-request per scraper-run nodig. (Er bestaat
ook een los AJAX-endpoint voor willekeurige andere data — POST
/Schedule/MainSchedule.aspx met form-data "gameDate=YYYYMMDD&flag=PRE/NEXT",
gevonden door de pagina's eigen setSchedule()-functie live uit te lezen —
maar dat is voor deze dagelijkse scraper niet nodig: de homepage geeft
precies het venster dat we willen.)

robots.txt van eng.koreabaseball.com verbiedt alleen /Common/, /Help/,
/Member/ en /ws/ — geen van alle relevant voor "/" (de enige pagina die we
ophalen).

Elke wedstrijd staat in een <li> met daarin twee <span class="ebl_s_XX">
(weg- resp. thuisteam, in die volgorde — bevestigd via de eigen "Away"/
"Home"-kopjes op de pagina zelf: <span class="p_away">Away</span><span
class="p_home">Home</span>). De teamnaam is de platte tekst ná het geneste
logo (<span class="emb"><img></span>...TEKST) — deze tekst is op deze site
zelf al kort (bv. "KT", "SSG", "HANWHA", "KIWOOM"), dus we hoeven die (in
tegenstelling tot bij NPB) niet zelf nog in te korten voor weergave.

Een <div class="scoreboard"> bevat óf twee scorespans + een ":"-span
(gespeelde wedstrijd, bv. <span class="score_win">9</span><span
class="score_normal">:</span><span class="score_normal lose">2</span>) óf
één enkele <span class="score_vs">VS</span> (nog te spelen) — we
onderscheiden deze twee gevallen puur op het aantal directe <span>-kinderen
(1 versus 3), niet op de win/lose/normal-klassen zelf (die zeggen niets
over of de wedstrijd al gespeeld is — een gelijkspel krijgt bv. op BEIDE
scores de klasse "lose").

De exacte datum van elke wedstrijd lezen we NIET uit de <h4>-kop (die mist
het jaartal, bv. "THU SEP 17" i.p.v. "2026-09-17"), maar uit de
locatielink zelf (<a href="/Schedule/Scoreboard.aspx?searchDate=2026-09-17">)
— betrouwbaarder dan de koptekst zelf parsen.

De locatie (en, bij nog te spelen wedstrijden, de aanvangstijd) staat in
diezelfde link, maar de site nest dat inconsistent: soms platte tekst
("DAEJEON"), soms in een extra <span class="stadium">, en bij nog te spelen
wedstrijden staat de tijd ná een <br> — soms wél, soms niét binnen diezelfde
<span class="stadium"> gewrapt. get_text(separator=" ", strip=True) op de
hele <a> plakt dit in alle vier de varianten hetzelfde aan elkaar (bv.
"CHANGWON 18:30"), dus we splitsen achteraf zelf de tijd (regex "H:MM" aan
het einde) van de locatienaam i.p.v. op een specifieke nesting te vertrouwen.

Lege placeholder-<li>'s (opvulling in de site's eigen scroll-container,
zodat elke dag-kolom evenveel rijen toont) hebben geen teamspans en worden
overgeslagen.

Output: net als npb_schedule.json een los "results"-blok (al gespeelde
wedstrijden, met eindstand) en een los "schedule"-blok (nog te spelen
wedstrijden, met aanvangstijd i.p.v. score) — hier het gevolg van vanzelf
doordat het venster altijd gisteren (resultaten), vandaag (kan van beide
zijn) en morgen (programma) bevat.
"""
import datetime as dt
import json
import re
import time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

HOMEPAGE_URL = "https://eng.koreabaseball.com/"
JSON_FILE = "kbo_schedule.json"
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


def parse_team(span):
    """Haalt de (al korte) teamnaam uit een <span class="ebl_s_XX">-cel."""
    return span.get_text(strip=True)


def parse_scoreboard(div):
    """Geeft (weg_score, thuis_score) terug, of (None, None) als de
    wedstrijd nog gespeeld moet worden (<span class="score_vs">VS</span>,
    zie moduledocstring)."""
    spans = div.find_all("span", recursive=False)
    if len(spans) == 1 and "score_vs" in (spans[0].get("class") or []):
        return None, None
    if len(spans) >= 3:
        try:
            return int(spans[0].get_text(strip=True)), int(spans[2].get_text(strip=True))
        except ValueError:
            return None, None  # onverwachte inhoud: geen score aannemen i.p.v. crashen
    return None, None


def parse_locatie(a_tag):
    """Splitst de locatielink op in (locatie, tijd) — tijd is None bij een
    al gespeelde wedstrijd. Zie moduledocstring voor waarom dit via
    get_text(separator=" ") gebeurt i.p.v. op een specifieke nesting te
    vertrouwen."""
    tekst = a_tag.get_text(" ", strip=True)
    match = re.search(r"(\d{1,2}:\d{2})\s*$", tekst)
    if match:
        return tekst[: match.start()].strip(), match.group(1)
    return tekst, None


def parse_wedstrijd(li):
    """Zet één <li> om naar een wedstrijd-dict, of None voor een lege
    placeholder-rij (zie moduledocstring)."""
    team_spans = li.select("span[class^='ebl_s_']")
    if len(team_spans) < 2:
        return None

    link = li.find("a", href=re.compile(r"searchDate="))
    if link is None:
        return None  # onverwacht: geen locatielink, dus ook geen betrouwbare datum
    datum_match = re.search(r"searchDate=(\d{4}-\d{2}-\d{2})", link["href"])
    if not datum_match:
        return None

    scoreboard = li.find("div", class_="scoreboard")
    weg_score, thuis_score = parse_scoreboard(scoreboard) if scoreboard else (None, None)
    locatie, tijd = parse_locatie(link)

    return {
        "date": datum_match.group(1),
        "away_team": parse_team(team_spans[0]),
        "home_team": parse_team(team_spans[1]),
        "away_score": weg_score,
        "home_score": thuis_score,
        "venue": locatie,
        "time": tijd,
    }


def haal_schedule_op():
    """Haalt de homepage op en ontleedt de schedule-widget (gisteren/
    vandaag/morgen) naar een platte lijst van wedstrijd-dicts."""
    html = haal_pagina_op(HOMEPAGE_URL)
    soup = BeautifulSoup(html, "html.parser")

    widget = soup.select_one("div.tit_schedule")
    if widget is None:
        raise RuntimeError("schedule-widget (div.tit_schedule) niet gevonden op de homepage")

    dagen = widget.select("div.selec_day_detail")
    if not dagen:
        raise RuntimeError("geen 'selec_day_detail'-dagblokken gevonden in de schedule-widget")

    wedstrijden = []
    for dag in dagen:
        for li in dag.find_all("li"):
            wedstrijd = parse_wedstrijd(li)
            if wedstrijd is not None:
                wedstrijden.append(wedstrijd)
    return wedstrijden


def main():
    pogingen = 3
    laatste_fout = None
    wedstrijden = []

    for poging in range(1, pogingen + 1):
        try:
            wedstrijden = haal_schedule_op()
            print(f"{len(wedstrijden)} wedstrijden gevonden (poging {poging}/{pogingen})")
            break
        except Exception as e:
            laatste_fout = e
            print(f"Poging {poging}/{pogingen} mislukt: {e}")
            if poging < pogingen:
                time.sleep(5)
    else:
        raise RuntimeError(f"Alle {pogingen} pogingen mislukt: {laatste_fout}")

    # Een wedstrijd is "gespeeld" zodra er een eindstand bekend is; anders
    # staat hij nog op het programma (zie moduledocstring: dit venster
    # bevat vanzelf gisteren/vandaag/morgen, dus dit onderscheid volgt puur
    # uit of parse_scoreboard() al een score kon lezen).
    results = [w for w in wedstrijden if w["away_score"] is not None and w["home_score"] is not None]
    schedule = [w for w in wedstrijden if w["away_score"] is None or w["home_score"] is None]

    output = {
        "bijgewerkt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": HOMEPAGE_URL,
        "season": dt.datetime.now(ZoneInfo(KBO_TZ)).year,
        "results": results,
        "schedule": schedule,
    }
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n{JSON_FILE} geschreven. {len(results)} resultaten, {len(schedule)} programma-wedstrijden.")


if __name__ == "__main__":
    main()
