import json
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

F1DB_LATEST_ZIP_URL = (
    "https://github.com/f1db/f1db/releases/latest/download/f1db-json-splitted.zip"
)
F1DB_CACHE_PATH = Path(tempfile.gettempdir()) / "f1db-json-splitted.zip"
F1DB_CACHE_TTL_SEC = 7200  # 2 horas en ejecuciones locales

HEADERS = {"User-Agent": "f1-telemetry-repo/2.0"}

# Cuando crees f1-src, solo tendrás que cambiar esta URL
ASSETS_BASE_URL = "https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main"

# Mapeo de circuitId de F1DB -> slug exacto en f1-fantasy/src/circuits/
CIRCUIT_SLUG_ALIASES = {
    "albert-park": "melbourne",
    "americas": "austin",
    "circuit-of-the-americas": "austin",
    "hermanos-rodriguez": "mexico-city",
    "rodriguez": "mexico-city",
    "losail": "lusail",
    "madrid": "madring",
    "red-bull-ring": "spielberg",
    "sakhir": "bahrain",
    "villeneuve": "montreal",
}

# Normaliza nombres/IDs de equipo de F1DB y OpenF1 al estándar de f1-fantasy
TEAM_NORMALIZATION = {
    "red-bull":      ("Red Bull Racing", "RED"),
    "red bull":      ("Red Bull Racing", "RED"),
    "mclaren":       ("McLaren", "MCL"),
    "ferrari":       ("Ferrari", "FER"),
    "mercedes":      ("Mercedes", "MER"),
    "aston-martin":  ("Aston Martin", "AST"),
    "aston martin":  ("Aston Martin", "AST"),
    "alpine":        ("Alpine", "ALP"),
    "williams":      ("Williams", "WIL"),
    "haas":          ("Haas F1 Team", "HAA"),
    "racing-bulls":  ("Racing Bulls", "VRB"),
    "racing bulls":  ("Racing Bulls", "VRB"),
    "rb":            ("Racing Bulls", "VRB"),
    "rb f1":         ("Racing Bulls", "VRB"),
    "vcarb":         ("Racing Bulls", "VRB"),
    "alphatauri":    ("AlphaTauri", "ALT"),
    "alpha tauri":   ("AlphaTauri", "ALT"),
    "alfa-romeo":    ("Alfa Romeo", "ALF"),
    "alfa romeo":    ("Alfa Romeo", "ALF"),
    "kick-sauber":   ("Kick Sauber", "KCK"),
    "kick sauber":   ("Kick Sauber", "KCK"),
    "sauber":        ("Kick Sauber", "KCK"),
    "audi":          ("Audi", "AUD"),
    "cadillac":      ("Cadillac", "CAD"),
}

_F1DB_STORE = None


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


def to_iso_utc(date_str, time_str=None):
    """Normaliza fecha/hora de F1DB ('2026-11-22', '04:00') a ISO-8601 UTC."""
    if not date_str:
        return None
    if not time_str:
        return f"{date_str}T12:00:00Z"
    t = str(time_str).strip()
    if len(t) == 5:  # HH:MM
        t = f"{t}:00"
    if not t.endswith("Z"):
        t = f"{t}Z"
    return f"{date_str}T{t}"


def parse_points(raw_val):
    if raw_val is None:
        return None
    try:
        val = float(raw_val)
        return int(val) if val.is_integer() else round(val, 2)
    except (ValueError, TypeError):
        return None


def _ensure_f1db_zip(force_refresh=False):
    """Descarga f1db-json-splitted.zip en /tmp una sola vez y reutiliza caché breve."""
    if (
        not force_refresh
        and F1DB_CACHE_PATH.exists()
        and (time.time() - F1DB_CACHE_PATH.stat().st_mtime) < F1DB_CACHE_TTL_SEC
    ):
        return F1DB_CACHE_PATH

    print("📦 Descargando última release de F1DB (f1db-json-splitted.zip)...")
    for attempt in range(3):
        try:
            req = Request(F1DB_LATEST_ZIP_URL, headers=HEADERS)
            with urlopen(req, timeout=60) as resp:
                data = resp.read()
            F1DB_CACHE_PATH.write_bytes(data)
            print(f"✅ F1DB descargado ({len(data) / 1_048_576:.2f} MB).")
            return F1DB_CACHE_PATH
        except (HTTPError, URLError, TimeoutError) as e:
            if attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"⏳ Error descargando F1DB ({e}). Reintentando en {wait}s...")
                time.sleep(wait)
            else:
                print(f"❌ No se pudo descargar F1DB: {e}")
    return F1DB_CACHE_PATH if F1DB_CACHE_PATH.exists() else None


def _read_zip_json(zf, member_map, filename):
    member = member_map.get(filename)
    if not member:
        return []
    with zf.open(member) as f:
        return json.loads(f.read().decode("utf-8"))


def get_f1db_store(force_refresh=False):
    """Carga e indexa en memoria las tablas necesarias de F1DB."""
    global _F1DB_STORE
    if _F1DB_STORE is not None and not force_refresh:
        return _F1DB_STORE

    zip_path = _ensure_f1db_zip(force_refresh=force_refresh)
    if not zip_path:
        return None

    with zipfile.ZipFile(zip_path, "r") as zf:
        member_map = {Path(name).name: name for name in zf.namelist()}

        drivers = {d["id"]: d for d in _read_zip_json(zf, member_map, "f1db-drivers.json")}
        constructors = {c["id"]: c for c in _read_zip_json(zf, member_map, "f1db-constructors.json")}
        circuits = {c["id"]: c for c in _read_zip_json(zf, member_map, "f1db-circuits.json")}
        grands_prix = {g["id"]: g for g in _read_zip_json(zf, member_map, "f1db-grands-prix.json")}
        countries = {c["id"]: c for c in _read_zip_json(zf, member_map, "f1db-countries.json")}
        races_list = _read_zip_json(zf, member_map, "f1db-races.json")

        races_by_yr = {}
        races_by_season = {}
        for r in races_list:
            yr = int(r["year"])
            rd = int(r["round"])
            races_by_yr[(yr, rd)] = r
            races_by_season.setdefault(yr, {})[rd] = r

        def group_by_race_id(filename):
            grouped = {}
            for row in _read_zip_json(zf, member_map, filename):
                grouped.setdefault(row["raceId"], []).append(row)
            return grouped

        _F1DB_STORE = {
            "drivers": drivers,
            "constructors": constructors,
            "circuits": circuits,
            "grands_prix": grands_prix,
            "countries": countries,
            "races_by_yr": races_by_yr,
            "races_by_season": races_by_season,
            "race_results": group_by_race_id("f1db-races-race-results.json"),
            "sprint_results": group_by_race_id("f1db-races-sprint-race-results.json"),
            "qualifying_results": group_by_race_id("f1db-races-qualifying-results.json"),
            "fastest_laps": group_by_race_id("f1db-races-fastest-laps.json"),
            "dotd": group_by_race_id("f1db-races-driver-of-the-day-results.json"),
        }

    return _F1DB_STORE


def _format_event_name(store, race_obj):
    gp = store["grands_prix"].get(race_obj.get("grandPrixId"), {})
    if gp.get("fullName"):
        return gp["fullName"]
    if gp.get("name"):
        return f"{gp['name']} Grand Prix"
    return race_obj.get("officialName") or f"Round {race_obj.get('round')}"


def fetch_season_schedule(season):
    """Devuelve {race_id: schedule_dict} compatible con get_round_data.py desde F1DB."""
    store = get_f1db_store()
    if not store:
        return {}

    season_races = store["races_by_season"].get(int(season), {})
    schedule = {}
    for rd, r in sorted(season_races.items()):
        has_sprint = bool(r.get("sprintRaceDate"))
        schedule[rd] = {
            "f1db_race_id": r["id"],
            "round": rd,
            "raceName": _format_event_name(store, r),
            "date": r.get("date"),
            "time": r.get("time"),
            "Qualifying": (
                {"date": r.get("qualifyingDate"), "time": r.get("qualifyingTime")}
                if r.get("qualifyingDate")
                else None
            ),
            "SprintQualifying": (
                {"date": r.get("sprintQualifyingDate"), "time": r.get("sprintQualifyingTime")}
                if r.get("sprintQualifyingDate")
                else None
            ),
            "Sprint": (
                {"date": r.get("sprintRaceDate"), "time": r.get("sprintRaceTime")}
                if has_sprint
                else None
            ),
            "_raw": r,
        }
    return schedule


def _format_f1db_status(row, session_type):
    pos_text = str(row.get("positionText") or "").strip().upper()
    reason = (row.get("reasonRetired") or "").strip()
    gap_laps = row.get("gapLaps")

    if session_type == "Qualifying":
        if pos_text in ("DNS", "WD", "W", "DNP"):
            return "Did not start"
        if pos_text in ("DNQ", "DNPQ"):
            return "Did not qualify"
        if pos_text in ("DSQ", "EX"):
            return "Disqualified"
        return "Finished"

    if pos_text in ("DNS", "WD", "W", "DNP"):
        return "Did not start"
    if pos_text in ("DSQ", "EX"):
        return "Disqualified"
    if pos_text in ("DNQ", "DNPQ"):
        return "Did not qualify"
    if pos_text == "NC":
        return "Not classified"
    if pos_text == "DNF" or reason:
        return reason or "Retired"
    if gap_laps is not None and int(gap_laps) > 0:
        n = int(gap_laps)
        return f"+{n} Lap" if n == 1 else f"+{n} Laps"
    return "Finished"


def fetch_session_results(season, race_id, session_type):
    """
    Obtiene clasificación oficial completa desde F1DB indexada por Driver_Code.
    session_type: 'Qualifying' | 'Sprint' | 'Race'
    """
    store = get_f1db_store()
    if not store:
        return {}

    race_obj = store["races_by_yr"].get((int(season), int(race_id)))
    if not race_obj:
        return {}

    f1db_rid = race_obj["id"]
    key_map = {
        "Qualifying": "qualifying_results",
        "Sprint": "sprint_results",
        "Race": "race_results",
    }
    rows = store[key_map[session_type]].get(f1db_rid, [])
    if not rows:
        return {}

    # Ranking oficial de vuelta rápida en carrera
    fl_rank_by_driver = {}
    if session_type == "Race":
        for fl in store["fastest_laps"].get(f1db_rid, []):
            if fl.get("positionNumber") is not None:
                fl_rank_by_driver[fl["driverId"]] = int(fl["positionNumber"])

    results_by_code = {}
    for row in rows:
        drv_id = row.get("driverId")
        con_id = row.get("constructorId")
        drv = store["drivers"].get(drv_id, {})
        con = store["constructors"].get(con_id, {})

        code = (drv.get("abbreviation") or "").upper()
        if not code:
            continue

        nat_country = store["countries"].get(drv.get("nationalityCountryId"), {})
        nationality = nat_country.get("demonym") or nat_country.get("name")

        team_name, team_code = normalize_team(con_id)
        if team_code == "N/A":
            team_name, team_code = normalize_team(con.get("name") or con.get("fullName"))

        pos_num = row.get("positionNumber")
        pos_disp = row.get("positionDisplayOrder")
        finish_pos = (
            int(pos_num)
            if pos_num is not None
            else (int(pos_disp) if pos_disp is not None else None)
        )

        grid_num = row.get("gridPositionNumber")
        grid_pos = int(grid_num) if grid_num is not None and int(grid_num) > 0 else None

        drv_num_raw = row.get("driverNumber") or drv.get("permanentNumber")
        drv_num = int(drv_num_raw) if str(drv_num_raw or "").isdigit() else None

        time_ms = row.get("timeMillis")
        gap_ms = row.get("gapMillis")
        gap_sec = None
        if finish_pos == 1 and time_ms is not None:
            gap_sec = 0.0
        elif gap_ms is not None:
            gap_sec = round(int(gap_ms) / 1000.0, 3)

        race_time_text = row.get("time") or row.get("gap")
        quali_times = (
            {"Q1": row.get("q1"), "Q2": row.get("q2"), "Q3": row.get("q3")}
            if session_type == "Qualifying"
            else None
        )

        results_by_code[code] = {
            "Driver_Name": format_driver_name(drv.get("firstName"), drv.get("lastName")),
            "Driver_Number": drv_num,
            "Nationality": nationality,
            "Team_Name": team_name,
            "Team_Code": team_code,
            "Grid_Position": grid_pos,
            "Finish_Position": finish_pos,
            "Status": _format_f1db_status(row, session_type),
            "Points": parse_points(row.get("points")) if session_type in ("Race", "Sprint") else 0,
            "Laps_Completed": int(row["laps"]) if row.get("laps") is not None else None,
            "Race_Time_Text": race_time_text,
            "Race_Time_Ms": int(time_ms) if time_ms is not None else None,
            "Gap_To_Leader_Sec": gap_sec,
            "Fastest_Lap_Rank": fl_rank_by_driver.get(drv_id),
            "Qualifying_Times": quali_times,
        }

    return results_by_code


def fetch_driver_of_the_day(season, race_id):
    """Devuelve el ganador de Driver of the Day de F1DB si está disponible."""
    store = get_f1db_store()
    if not store:
        return None

    race_obj = store["races_by_yr"].get((int(season), int(race_id)))
    if not race_obj:
        return None

    rows = store["dotd"].get(race_obj["id"], [])
    if not rows:
        return None

    top = min(rows, key=lambda r: r.get("positionNumber") or 99)
    drv = store["drivers"].get(top.get("driverId"), {})
    code = (drv.get("abbreviation") or "").upper()
    if not code:
        return None

    pct = top.get("percentage")
    return {
        "Driver_Code": code,
        "Driver_Name": format_driver_name(drv.get("firstName"), drv.get("lastName")),
        "Percentage": round(float(pct), 2) if pct is not None else None,
    }


def build_meta(season, race_id, session_type, race_info, openf1_session_key=None):
    """Construye el bloque Meta alineado con f1-fantasy y enriquecido con metadatos de F1DB."""
    store = get_f1db_store()
    raw_race = (race_info or {}).get("_raw") or (
        store["races_by_yr"].get((int(season), int(race_id)), {}) if store else {}
    )
    circuit_id = raw_race.get("circuitId") or ""
    circuit = store["circuits"].get(circuit_id, {}) if store else {}
    country = store["countries"].get(circuit.get("countryId"), {}) if store else {}

    svg_slug = CIRCUIT_SLUG_ALIASES.get(circuit_id, circuit_id)
    course_len = raw_race.get("courseLength") or circuit.get("length")
    turns = raw_race.get("turns") or circuit.get("turns")
    direction = raw_race.get("direction") or circuit.get("direction")
    circuit_type = raw_race.get("circuitType") or circuit.get("type")
    scheduled_laps = raw_race.get("laps")
    distance_km = raw_race.get("distance")

    return {
        "Id": f"{season}_{race_id:02d}",
        "Season": int(season),
        "Race_Id": int(race_id),
        "Event_Name": race_info["raceName"] if race_info else _format_event_name(store, raw_race),
        "Session_Type": session_type,
        "Is_Sprint": bool(raw_race.get("sprintRaceDate")),
        "OpenF1_Session_Key": openf1_session_key,
        "Circuit": {
            "Id": svg_slug,
            "Full_Name": circuit.get("fullName") or circuit.get("name"),
            "City": circuit.get("placeName"),
            "Country_Code": country.get("alpha3Code"),
            "Country_Name": country.get("name"),
            "Type": circuit_type,
            "Direction": direction,
            "Length_Km": round(float(course_len), 3) if course_len is not None else None,
            "Turns": int(turns) if turns is not None else None,
            "Scheduled_Laps": int(scheduled_laps) if scheduled_laps is not None else None,
            "Race_Distance_Km": round(float(distance_km), 3) if distance_km is not None else None,
            "Svg_Path": f"src/circuits/{svg_slug}.svg",
        },
        "Start_Times": {
            "Qualifying": to_iso_utc(raw_race.get("qualifyingDate"), raw_race.get("qualifyingTime")),
            "Sprint_Qualifying": to_iso_utc(
                raw_race.get("sprintQualifyingDate"), raw_race.get("sprintQualifyingTime")
            ),
            "Sprint": to_iso_utc(raw_race.get("sprintRaceDate"), raw_race.get("sprintRaceTime")),
            "Race": to_iso_utc(raw_race.get("date"), raw_race.get("time")),
        },
        "Generated_At": datetime.now().isoformat(),
    }
