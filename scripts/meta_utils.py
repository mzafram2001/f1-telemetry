import json
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
HEADERS = {"User-Agent": "f1-telemetry-repo/1.0"}

# Cuando crees f1-src, solo tendrás que cambiar esta URL
ASSETS_BASE_URL = "https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main"

# circuitId de Jolpica -> (slug SVG en f1-fantasy/src/circuits/, código ISO-3)
CIRCUIT_MAP = {
    "albert_park":   ("melbourne", "AUS"),
    "americas":      ("austin", "USA"),
    "bahrain":       ("bahrain", "BHR"),
    "baku":          ("baku", "AZE"),
    "catalunya":     ("catalunya", "ESP"),
    "hungaroring":   ("hungaroring", "HUN"),
    "imola":         ("imola", "ITA"),
    "interlagos":    ("interlagos", "BRA"),
    "istanbul":      ("istanbul", "TUR"),
    "jeddah":        ("jeddah", "SAU"),
    "losail":        ("lusail", "QAT"),
    "madrid":        ("madring", "ESP"),
    "madring":       ("madring", "ESP"),
    "marina_bay":    ("marina-bay", "SGP"),
    "miami":         ("miami", "USA"),
    "monaco":        ("monaco", "MCO"),
    "monza":         ("monza", "ITA"),
    "portimao":      ("portimao", "PRT"),
    "red_bull_ring": ("spielberg", "AUT"),
    "rodriguez":     ("mexico-city", "MEX"),
    "sepang":        ("sepang", "MYS"),
    "shanghai":      ("shanghai", "CHN"),
    "silverstone":   ("silverstone", "GBR"),
    "spa":           ("spa-francorchamps", "BEL"),
    "suzuka":        ("suzuka", "JPN"),
    "vegas":         ("las-vegas", "USA"),
    "villeneuve":    ("montreal", "CAN"),
    "yas_marina":    ("yas-marina", "ARE"),
    "zandvoort":     ("zandvoort", "NLD"),
}

COUNTRY_NAME_OVERRIDES = {
    "US": "United States",
    "USA": "United States",
    "UK": "United Kingdom",
    "UAE": "United Arab Emirates",
}

# Normaliza nombres de equipo de OpenF1/Jolpica al estándar (Team_Name, Team_Code) de f1-fantasy
TEAM_NORMALIZATION = {
    "red bull":      ("Red Bull Racing", "RED"),
    "mclaren":       ("McLaren", "MCL"),
    "ferrari":       ("Ferrari", "FER"),
    "mercedes":      ("Mercedes", "MER"),
    "aston martin":  ("Aston Martin", "AST"),
    "alpine":        ("Alpine", "ALP"),
    "williams":      ("Williams", "WIL"),
    "haas":          ("Haas F1 Team", "HAA"),
    "racing bulls":  ("Racing Bulls", "VRB"),
    "rb f1":         ("Racing Bulls", "VRB"),
    "rb":            ("Racing Bulls", "VRB"),
    "vcarb":         ("Racing Bulls", "VRB"),
    "alphatauri":    ("AlphaTauri", "ALT"),
    "alpha tauri":   ("AlphaTauri", "ALT"),
    "alfa romeo":    ("Alfa Romeo", "ALF"),
    "kick sauber":   ("Kick Sauber", "KCK"),
    "sauber":        ("Kick Sauber", "KCK"),
    "audi":          ("Audi", "AUD"),
    "cadillac":      ("Cadillac", "CAD"),
}


def normalize_team(raw_name):
    """Devuelve (Team_Name, Team_Code) alineado con f1-fantasy."""
    if not raw_name:
        return "N/A", "N/A"
    cleaned = raw_name.strip().lower()
    if cleaned in TEAM_NORMALIZATION:
        return TEAM_NORMALIZATION[cleaned]
    for key, value in TEAM_NORMALIZATION.items():
        if key in cleaned:
            return value
    return raw_name.strip(), "N/A"


def format_driver_name(given_name, family_name):
    """Formatea 'Max', 'Verstappen' -> 'M. Verstappen' como en f1-fantasy."""
    if not given_name or not family_name:
        return (family_name or given_name or "N/A").strip()
    return f"{given_name.strip()[0]}. {family_name.strip()}"


def to_iso_utc(session_obj):
    if not session_obj or not session_obj.get("date"):
        return None
    date_str = session_obj["date"]
    time_str = session_obj.get("time")
    return f"{date_str}T{time_str}" if time_str else date_str


def fetch_jolpica(endpoint, retries=4):
    url = f"{JOLPICA_BASE}/{endpoint.lstrip('/')}"
    for attempt in range(retries):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"⏳ Timeout/error en Jolpica ({endpoint}): {e}. Reintentando en {wait}s...")
                time.sleep(wait)
            else:
                print(f"⚠️ Error consultando Jolpica ({endpoint}): {e}")
    return None


def fetch_season_schedule(season):
    """Devuelve {race_id: race_dict} para la temporada."""
    data = fetch_jolpica(f"{season}.json?limit=100")
    if not data:
        return {}
    races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    return {int(r["round"]): r for r in races}


def fetch_session_results(season, race_id, session_type):
    """
    Obtiene clasificación oficial desde Jolpica-F1 indexada por Driver_Code.
    session_type: 'Qualifying' | 'Sprint' | 'Race'
    """
    endpoints = {
        "Qualifying": (f"{season}/{race_id}/qualifying.json?limit=100", "QualifyingResults"),
        "Sprint":     (f"{season}/{race_id}/sprint.json?limit=100", "SprintResults"),
        "Race":       (f"{season}/{race_id}/results.json?limit=100", "Results"),
    }
    endpoint, list_key = endpoints[session_type]
    data = fetch_jolpica(endpoint)
    if not data:
        return {}

    races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return {}

    results_by_code = {}
    for row in races[0].get(list_key, []):
        driver = row.get("Driver", {})
        constructor = row.get("Constructor", {})
        code = driver.get("code")
        if not code:
            continue

        team_name, team_code = normalize_team(constructor.get("name"))
        grid_raw = row.get("grid")
        pos_raw = row.get("position")

        results_by_code[code] = {
            "Driver_Name": format_driver_name(driver.get("givenName"), driver.get("familyName")),
            "Driver_Number": int(row["number"]) if str(row.get("number", "")).isdigit() else None,
            "Team_Name": team_name,
            "Team_Code": team_code,
            "Grid_Position": int(grid_raw) if str(grid_raw).isdigit() and int(grid_raw) > 0 else None,
            "Finish_Position": int(pos_raw) if str(pos_raw).isdigit() else None,
            "Status": row.get("status", "Finished" if session_type == "Qualifying" else "Unknown"),
            "Laps_Completed": int(row["laps"]) if str(row.get("laps", "")).isdigit() else None,
            "Q1": row.get("Q1"),
            "Q2": row.get("Q2"),
            "Q3": row.get("Q3"),
        }

    return results_by_code


def build_meta(season, race_id, session_type, race_info, openf1_session_key=None):
    """Construye el bloque Meta alineado con f1-fantasy en Pascal_Snake_Case."""
    circuit = race_info["Circuit"]
    loc = circuit["Location"]
    jolpica_cid = circuit["circuitId"]

    svg_slug, country_code = CIRCUIT_MAP.get(jolpica_cid, (jolpica_cid.replace("_", "-"), None))
    country_name = COUNTRY_NAME_OVERRIDES.get(loc["country"], loc["country"])
    sprint_obj = race_info.get("Sprint")
    sprint_quali_obj = race_info.get("SprintQualifying") or race_info.get("SprintShootout")

    return {
        "Id": f"{season}_{race_id:02d}",
        "Season": season,
        "Race_Id": race_id,
        "Event_Name": race_info["raceName"],
        "Session_Type": session_type,
        "Is_Sprint": sprint_obj is not None,
        "OpenF1_Session_Key": openf1_session_key,
        "Circuit": {
            "Id": svg_slug,
            "City": loc["locality"],
            "Country_Code": country_code,
            "Country_Name": country_name,
            "Svg_Path": f"src/circuits/{svg_slug}.svg",
        },
        "Start_Times": {
            "Qualifying": to_iso_utc(race_info.get("Qualifying")),
            "Sprint_Qualifying": to_iso_utc(sprint_quali_obj),
            "Sprint": to_iso_utc(sprint_obj),
            "Race": to_iso_utc({"date": race_info.get("date"), "time": race_info.get("time")}),
        },
        "Generated_At": datetime.now().isoformat(),
    }
