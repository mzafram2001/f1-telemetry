import argparse
import bisect
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from meta_utils import (
    HEADERS,
    build_meta,
    fetch_season_schedule,
    fetch_session_results,
    format_driver_name,
    normalize_team,
)

OPENF1_BASE = "https://api.openf1.org/v1"
SESSION_FILES = {
    "Qualifying": "qualifying.json",
    "Sprint": "sprint.json",
    "Race": "race.json",
}
DNS_STATUS_ALIASES = {"did not start", "dns", "withdrew", "wd", "injured", "illness"}


class OpenF1LiveSessionError(Exception):
    """Se lanza cuando OpenF1 bloquea el plan gratuito por sesión en directo."""


class OpenF1Client:
    def __init__(self, min_interval=2.15):
        self.min_interval = min_interval
        self._last_call = 0.0

    def get(self, endpoint, params=None, raw_query=None):
        """Petición GET respetando el límite de 30 req/min de OpenF1."""
        url = f"{OPENF1_BASE}/{endpoint.lstrip('/')}"
        query_parts = []
        if params:
            query_parts.append(urlencode(params))
        if raw_query:
            query_parts.append(raw_query)
        if query_parts:
            url = f"{url}?{'&'.join(query_parts)}"

        for attempt in range(3):
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)

            self._last_call = time.monotonic()
            try:
                req = Request(url, headers=HEADERS)
                with urlopen(req, timeout=25) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                if e.code == 404:
                    return []
                if e.code in (401, 403):
                    raise OpenF1LiveSessionError(
                        "OpenF1 restringe temporalmente el acceso gratuito durante sesiones en directo."
                    )
                if e.code == 429:
                    wait = 15 * (attempt + 1)
                    print(f"⏳ Rate limit (429) en OpenF1. Esperando {wait}s...")
                    time.sleep(wait)
                    continue
                print(f"⚠️ Error HTTP {e.code} en OpenF1 ({endpoint}): {e}")
                return []
            except (URLError, TimeoutError, json.JSONDecodeError) as e:
                print(f"⚠️ Error de red en OpenF1 ({endpoint}): {e}")
                if attempt < 2:
                    time.sleep(5)
                    continue
                return []
        return []


def parse_iso_utc(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def to_openf1_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def normalize_status(raw_status):
    """Unifica retiradas previas a la salida bajo 'Did not start'."""
    if not raw_status:
        return raw_status
    cleaned = raw_status.strip()
    if cleaned.lower() in DNS_STATUS_ALIASES:
        return "Did not start"
    return cleaned


def build_empty_driver_entry(code, info, status, grid_pos=None, finish_pos=None, laps=0):
    """Construye una entrada sin telemetría para pilotos DNS o retirados tras Qualifying."""
    return {
        "Driver_Name": info.get("Driver_Name") or code,
        "Driver_Code": code,
        "Driver_Number": info.get("Driver_Number"),
        "Team_Name": info.get("Team_Name", "N/A"),
        "Team_Code": info.get("Team_Code", "N/A"),
        "Grid_Position": grid_pos,
        "Finish_Position": finish_pos,
        "Status": normalize_status(status),
        "Laps_Completed": laps,
        "Fastest_Lap_Sec": None,
        "Fastest_Lap_Number": None,
        "Avg_Pace_Sec": None,
        "Best_Sector_1_Sec": None,
        "Best_Sector_2_Sec": None,
        "Best_Sector_3_Sec": None,
        "Top_Speed_Kmh": None,
        "Full_Throttle_Pct": None,
        "Brake_Pct": None,
        "Pit_Stops": 0,
        "Avg_Pit_Lane_Sec": None,
        "Avg_Pit_Stop_Sec": None,
        "Lap_Times": [],
        "Stints": [],
        "Fastest_Lap_Trace": None,
    }


def match_openf1_session(openf1_sessions, session_type, scheduled_iso):
    """Empareja la sesión oficial de Jolpica con su session_key en OpenF1."""
    target_dt = parse_iso_utc(scheduled_iso)
    if not target_dt:
        return None

    best_match = None
    best_diff = timedelta(hours=36)

    for s in openf1_sessions:
        if s.get("is_cancelled"):
            continue
        if (s.get("session_name") or "").strip().lower() != session_type.lower():
            continue
        s_dt = parse_iso_utc(s.get("date_start"))
        if not s_dt:
            continue
        diff = abs(s_dt - target_dt)
        if diff < best_diff:
            best_diff = diff
            best_match = s

    return best_match


def summarize_weather(weather_rows):
    if not weather_rows:
        return {
            "Air_Temp_Avg": None,
            "Track_Temp_Avg": None,
            "Humidity_Avg": None,
            "Wind_Speed_Avg": None,
            "Rainfall": None,
        }

    def avg_field(key):
        vals = [float(r[key]) for r in weather_rows if r.get(key) is not None]
        return round(statistics.mean(vals), 2) if vals else None

    rainfall = any(bool(r.get("rainfall")) for r in weather_rows)
    return {
        "Air_Temp_Avg": avg_field("air_temperature"),
        "Track_Temp_Avg": avg_field("track_temperature"),
        "Humidity_Avg": avg_field("humidity"),
        "Wind_Speed_Avg": avg_field("wind_speed"),
        "Rainfall": rainfall,
    }


def extract_lap_times(driver_laps):
    """Devuelve lista ordenada vuelta a vuelta (1..N) con duración en segundos o None."""
    by_num = {}
    for lap in driver_laps:
        num = lap.get("lap_number")
        if isinstance(num, int) and num > 0:
            dur = lap.get("lap_duration")
            by_num[num] = round(float(dur), 3) if dur is not None else None
    if not by_num:
        return []
    max_lap = max(by_num.keys())
    return [by_num.get(n) for n in range(1, max_lap + 1)]


def compute_avg_pace(driver_laps, session_type):
    """Calcula ritmo medio limpio descartando vueltas de salida y coches de seguridad."""
    clean = [
        float(l["lap_duration"])
        for l in driver_laps
        if l.get("lap_duration") is not None and not l.get("is_pit_out_lap")
    ]
    if not clean:
        return None

    if session_type == "Qualifying":
        best = min(clean)
        flying = [t for t in clean if t <= best * 1.07]
        return round(statistics.mean(flying), 3) if flying else round(best, 3)

    if len(clean) >= 5:
        med = statistics.median(clean)
        clean = [t for t in clean if t <= med * 1.07] or clean

    return round(statistics.mean(clean), 3)


def align_coordinates(sampled_car_rows, loc_rows):
    """Empareja cada muestra de car_data con su coordenada (x, y) más cercana en /location."""
    valid_locs = []
    for r in loc_rows:
        dt = parse_iso_utc(r.get("date"))
        if dt and r.get("x") is not None and r.get("y") is not None:
            valid_locs.append((dt.timestamp(), int(round(r["x"])), int(round(r["y"]))))

    if not valid_locs:
        return None, None

    valid_locs.sort(key=lambda item: item[0])
    loc_ts = [item[0] for item in valid_locs]

    xs = []
    ys = []
    for row in sampled_car_rows:
        dt = parse_iso_utc(row.get("date"))
        if not dt:
            xs.append(valid_locs[0][1])
            ys.append(valid_locs[0][2])
            continue
        t = dt.timestamp()
        idx = bisect.bisect_left(loc_ts, t)
        if idx == 0:
            chosen = valid_locs[0]
        elif idx >= len(valid_locs):
            chosen = valid_locs[-1]
        else:
            before = valid_locs[idx - 1]
            after = valid_locs[idx]
            chosen = before if (t - before[0]) <= (after[0] - t) else after
        xs.append(chosen[1])
        ys.append(chosen[2])

    return xs, ys


def compute_distance_pct(xs, ys, speeds):
    """Calcula el porcentaje acumulado de vuelta (0.0 - 100.0) usando coordenadas o velocidad."""
    n = len(speeds)
    if n == 0:
        return []
    if n == 1:
        return [0.0]

    cum = [0.0]
    if xs and ys and len(xs) == n:
        for i in range(1, n):
            step = math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1])
            cum.append(cum[-1] + step)

    if cum[-1] <= 0.0:
        cum = [0.0]
        for i in range(1, n):
            avg_v = max((speeds[i] + speeds[i - 1]) * 0.5, 1.0)
            cum.append(cum[-1] + avg_v)

    total = cum[-1]
    return [round((d / total) * 100.0, 2) for d in cum]


def build_trace_and_metrics(car_rows, loc_rows=None, target_samples=100):
    """Extrae métricas agregadas y traza completa de 100 muestras de la vuelta rápida."""
    if not car_rows:
        return None, None, None, None

    car_rows = sorted(car_rows, key=lambda r: r.get("date") or "")

    speeds = [int(r["speed"]) for r in car_rows if r.get("speed") is not None]
    throttles = [int(r["throttle"]) for r in car_rows if r.get("throttle") is not None]
    brakes = [int(r["brake"]) for r in car_rows if r.get("brake") is not None]

    top_speed = max(speeds) if speeds else None
    full_throttle_pct = (
        round(sum(1 for t in throttles if t >= 95) / len(throttles), 4) if throttles else None
    )
    brake_pct = (
        round(sum(1 for b in brakes if b > 0) / len(brakes), 4) if brakes else None
    )

    n = len(car_rows)
    if n <= target_samples:
        sampled = car_rows
    else:
        indices = [round(i * (n - 1) / (target_samples - 1)) for i in range(target_samples)]
        sampled = [car_rows[idx] for idx in indices]

    sampled_speeds = [int(r.get("speed") or 0) for r in sampled]
    xs, ys = align_coordinates(sampled, loc_rows or [])
    dist_pct = compute_distance_pct(xs, ys, sampled_speeds)

    trace = {
        "Samples": len(sampled),
        "Distance_Pct": dist_pct,
        "Speed_Kmh": sampled_speeds,
        "Throttle_Pct": [int(r.get("throttle") or 0) for r in sampled],
        "Brake_Pct": [100 if (r.get("brake") or 0) > 0 else 0 for r in sampled],
        "Gear": [int(r.get("n_gear") or 0) for r in sampled],
        "Rpm": [int(r.get("rpm") or 0) for r in sampled],
        "Drs": [int(r.get("drs") or 0) for r in sampled],
        "X": xs,
        "Y": ys,
    }
    return trace, top_speed, full_throttle_pct, brake_pct


def process_session(openf1, season, race_id, session_type, race_info, openf1_session, samples):
    session_key = openf1_session["session_key"]
    print(f"  📡 Descargando {session_type} (session_key={session_key})...")

    jolpica_results = fetch_session_results(season, race_id, session_type)
    drivers_raw = openf1.get("drivers", {"session_key": session_key})
    laps_raw = openf1.get("laps", {"session_key": session_key})
    stints_raw = openf1.get("stints", {"session_key": session_key})
    weather_raw = openf1.get("weather", {"session_key": session_key})
    pits_raw = (
        openf1.get("pit", {"session_key": session_key})
        if session_type in ("Race", "Sprint")
        else []
    )

    if not drivers_raw:
        print(f"  ⚠️ Sin pilotos en OpenF1 para session_key={session_key}")
        return None

    # Agrupación por driver_number
    laps_by_num = {}
    for lap in laps_raw:
        laps_by_num.setdefault(lap["driver_number"], []).append(lap)

    stints_by_num = {}
    for st in stints_raw:
        stints_by_num.setdefault(st["driver_number"], []).append(st)

    pits_by_num = {}
    for p in pits_raw:
        pits_by_num.setdefault(p["driver_number"], []).append(p)

    # Deduplicar lista de pilotos por número
    unique_drivers = {}
    for d in drivers_raw:
        unique_drivers[d["driver_number"]] = d

    processed_drivers = []
    processed_codes = set()

    for drv_num, d in unique_drivers.items():
        code = (d.get("name_acronym") or "").upper()
        if code:
            processed_codes.add(code)

        j_res = jolpica_results.get(code, {})

        if j_res.get("Driver_Name"):
            driver_name = j_res["Driver_Name"]
        else:
            driver_name = format_driver_name(d.get("first_name"), d.get("last_name"))

        if j_res.get("Team_Code") and j_res["Team_Code"] != "N/A":
            team_name, team_code = j_res["Team_Name"], j_res["Team_Code"]
        else:
            team_name, team_code = normalize_team(d.get("team_name"))

        d_laps = laps_by_num.get(drv_num, [])
        timed_laps = [l for l in d_laps if l.get("lap_duration") is not None]
        fastest_lap = min(timed_laps, key=lambda l: l["lap_duration"]) if timed_laps else None

        def best_sector(field):
            vals = [float(l[field]) for l in d_laps if l.get(field) is not None]
            return round(min(vals), 3) if vals else None

        # Telemetría + coordenadas de la vuelta rápida
        trace = None
        top_speed = None
        full_throttle_pct = None
        brake_pct = None

        if fastest_lap and fastest_lap.get("date_start"):
            lap_start = parse_iso_utc(fastest_lap["date_start"])
            lap_dur = float(fastest_lap["lap_duration"])
            if lap_start:
                lap_end = lap_start + timedelta(seconds=lap_dur + 0.5)
                raw_q = f"date>={to_openf1_iso(lap_start)}&date<={to_openf1_iso(lap_end)}"
                car_rows = openf1.get(
                    "car_data",
                    params={"session_key": session_key, "driver_number": drv_num},
                    raw_query=raw_q,
                )
                loc_rows = (
                    openf1.get(
                        "location",
                        params={"session_key": session_key, "driver_number": drv_num},
                        raw_query=raw_q,
                    )
                    if car_rows
                    else []
                )
                trace, top_speed, full_throttle_pct, brake_pct = build_trace_and_metrics(
                    car_rows, loc_rows=loc_rows, target_samples=samples
                )

        # Fallback de velocidad punta desde speed traps si no hubo car_data
        if top_speed is None:
            trap_speeds = [
                int(l[k])
                for l in d_laps
                for k in ("st_speed", "i1_speed", "i2_speed")
                if l.get(k) is not None
            ]
            top_speed = max(trap_speeds) if trap_speeds else None

        # Stints ordenados y filtrando stints fantasma invertidos (lap_end < lap_start)
        d_stints = sorted(
            [
                s for s in stints_by_num.get(drv_num, [])
                if s.get("lap_start") is None
                or s.get("lap_end") is None
                or s["lap_end"] >= s["lap_start"]
            ],
            key=lambda s: s.get("stint_number") or 0,
        )
        formatted_stints = [
            {
                "Stint": s.get("stint_number"),
                "Compound": s.get("compound"),
                "Lap_Start": s.get("lap_start"),
                "Lap_End": s.get("lap_end"),
                "Tyre_Age_At_Start": s.get("tyre_age_at_start"),
            }
            for s in d_stints
        ]

        # Paradas en boxes
        d_pits = pits_by_num.get(drv_num, [])
        lane_durs = [
            float(p.get("lane_duration") or p.get("pit_duration"))
            for p in d_pits
            if (p.get("lane_duration") or p.get("pit_duration")) is not None
        ]
        stop_durs = [
            float(p["stop_duration"])
            for p in d_pits
            if p.get("stop_duration") is not None
        ]

        laps_completed = j_res.get("Laps_Completed")
        if laps_completed is None and d_laps:
            laps_completed = max(l.get("lap_number") or 0 for l in d_laps)

        driver_entry = {
            "Driver_Name": driver_name,
            "Driver_Code": code or "N/A",
            "Driver_Number": drv_num,
            "Team_Name": team_name,
            "Team_Code": team_code,
            "Grid_Position": j_res.get("Grid_Position"),
            "Finish_Position": j_res.get("Finish_Position"),
            "Status": normalize_status(j_res.get("Status")),
            "Laps_Completed": laps_completed or 0,
            "Fastest_Lap_Sec": round(float(fastest_lap["lap_duration"]), 3) if fastest_lap else None,
            "Fastest_Lap_Number": fastest_lap.get("lap_number") if fastest_lap else None,
            "Avg_Pace_Sec": compute_avg_pace(d_laps, session_type),
            "Best_Sector_1_Sec": best_sector("duration_sector_1"),
            "Best_Sector_2_Sec": best_sector("duration_sector_2"),
            "Best_Sector_3_Sec": best_sector("duration_sector_3"),
            "Top_Speed_Kmh": top_speed,
            "Full_Throttle_Pct": full_throttle_pct,
            "Brake_Pct": brake_pct,
            "Pit_Stops": len(d_pits),
            "Avg_Pit_Lane_Sec": round(statistics.mean(lane_durs), 3) if lane_durs else None,
            "Avg_Pit_Stop_Sec": round(statistics.mean(stop_durs), 3) if stop_durs else None,
            "Lap_Times": extract_lap_times(d_laps),
            "Stints": formatted_stints,
            "Fastest_Lap_Trace": trace,
        }
        processed_drivers.append(driver_entry)

    # 1. Pilotos presentes en Jolpica-F1 que no emitieron en OpenF1 (DNS en parrilla)
    for code, j_res in jolpica_results.items():
        if code in processed_codes:
            continue
        processed_codes.add(code)
        processed_drivers.append(
            build_empty_driver_entry(
                code=code,
                info=j_res,
                status=j_res.get("Status") or "Did not start",
                grid_pos=j_res.get("Grid_Position"),
                finish_pos=j_res.get("Finish_Position"),
                laps=j_res.get("Laps_Completed") or 0,
            )
        )

    # 2. Pilotos que corrieron Qualifying pero se retiraron antes de la carrera -> DNS
    if session_type == "Race":
        quali_results = fetch_session_results(season, race_id, "Qualifying")
        for code, q_res in quali_results.items():
            if code in processed_codes:
                continue
            processed_codes.add(code)
            processed_drivers.append(
                build_empty_driver_entry(
                    code=code,
                    info=q_res,
                    status="Did not start",
                    grid_pos=None,
                    finish_pos=None,
                    laps=0,
                )
            )

    processed_drivers.sort(
        key=lambda x: (
            x["Finish_Position"] is None,
            x["Finish_Position"] or 99,
            x["Fastest_Lap_Sec"] is None,
            x["Fastest_Lap_Sec"] or 9999.0,
        )
    )

    # Agregado por constructores
    teams_bucket = {}
    for d in processed_drivers:
        t_code = d["Team_Code"]
        bucket = teams_bucket.setdefault(
            t_code,
            {
                "Team_Name": d["Team_Name"],
                "Team_Code": t_code,
                "paces": [],
                "speeds": [],
                "pit_lanes": [],
                "pit_stops": [],
                "laps": 0,
            },
        )
        if d["Avg_Pace_Sec"] is not None:
            bucket["paces"].append(d["Avg_Pace_Sec"])
        if d["Top_Speed_Kmh"] is not None:
            bucket["speeds"].append(d["Top_Speed_Kmh"])
        if d["Avg_Pit_Lane_Sec"] is not None:
            bucket["pit_lanes"].append(d["Avg_Pit_Lane_Sec"])
        if d["Avg_Pit_Stop_Sec"] is not None:
            bucket["pit_stops"].append(d["Avg_Pit_Stop_Sec"])
        bucket["laps"] += d["Laps_Completed"] or 0

    processed_teams = [
        {
            "Team_Name": b["Team_Name"],
            "Team_Code": b["Team_Code"],
            "Avg_Pace_Sec": round(statistics.mean(b["paces"]), 3) if b["paces"] else None,
            "Top_Speed_Kmh": max(b["speeds"]) if b["speeds"] else None,
            "Avg_Pit_Lane_Sec": round(statistics.mean(b["pit_lanes"]), 3) if b["pit_lanes"] else None,
            "Avg_Pit_Stop_Sec": round(statistics.mean(b["pit_stops"]), 3) if b["pit_stops"] else None,
            "Total_Laps_Completed": b["laps"],
        }
        for b in teams_bucket.values()
    ]
    processed_teams.sort(key=lambda x: (x["Avg_Pace_Sec"] is None, x["Avg_Pace_Sec"] or 9999.0))

    return {
        "Meta": build_meta(
            season=season,
            race_id=race_id,
            session_type=session_type,
            race_info=race_info,
            openf1_session_key=session_key,
        ),
        "Weather": summarize_weather(weather_raw),
        "Drivers": processed_drivers,
        "Teams": processed_teams,
    }


def is_file_up_to_date(out_path):
    """Comprueba si el JSON existente ya tiene el esquema completo (Lap_Times, Distance_Pct, Drs, X, Y)."""
    if not os.path.exists(out_path):
        return False
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        drivers = payload.get("Drivers", [])
        if not drivers:
            return False
        for d in drivers:
            if "Lap_Times" not in d:
                return False
            trace = d.get("Fastest_Lap_Trace")
            if trace is not None and (
                "Distance_Pct" not in trace
                or "Drs" not in trace
                or "X" not in trace
                or "Y" not in trace
            ):
                return False
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Descarga telemetría F1 desde OpenF1 + Jolpica.")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--round", type=int, default=None, help="Procesar una sola ronda")
    parser.add_argument("--force", action="store_true", help="Sobreescribir archivos existentes")
    parser.add_argument("--samples", type=int, default=100, help="Puntos en Fastest_Lap_Trace")
    args = parser.parse_args()

    season = args.season
    print(f"🚀 Comprobando rondas de telemetría para la temporada {season}...")

    schedule = fetch_season_schedule(season)
    if not schedule:
        print(f"❌ No se pudo obtener el calendario de Jolpica para {season}.")
        sys.exit(1)

    openf1 = OpenF1Client()
    try:
        openf1_sessions = openf1.get("sessions", {"year": season})
    except OpenF1LiveSessionError as e:
        print(f"⏸️ {e} Saliendo sin error para reintentar en la próxima ejecución.")
        return

    now_utc = datetime.now(timezone.utc)
    rounds_to_run = [args.round] if args.round else sorted(schedule.keys())

    for race_id in rounds_to_run:
        race_info = schedule.get(race_id)
        if not race_info:
            print(f"⚠️ Ronda {race_id} no encontrada en calendario {season}.")
            continue

        target_sessions = ["Qualifying", "Race"]
        if race_info.get("Sprint"):
            target_sessions.insert(1, "Sprint")

        start_map = {
            "Qualifying": race_info.get("Qualifying"),
            "Sprint": race_info.get("Sprint"),
            "Race": {"date": race_info.get("date"), "time": race_info.get("time")},
        }

        for session_type in target_sessions:
            out_dir = f"data/{season}/round_{race_id:02d}"
            out_path = f"{out_dir}/{SESSION_FILES[session_type]}"

            if is_file_up_to_date(out_path) and not args.force:
                continue

            sched_obj = start_map.get(session_type)
            if not sched_obj or not sched_obj.get("date"):
                continue

            sched_iso = f"{sched_obj['date']}T{sched_obj.get('time', '00:00:00Z')}"
            sched_dt = parse_iso_utc(sched_iso)

            # Esperamos al menos 3h desde el inicio programado para asegurar datos cerrados
            if not sched_dt or (now_utc - sched_dt) < timedelta(hours=3):
                continue

            openf1_session = match_openf1_session(openf1_sessions, session_type, sched_iso)
            if not openf1_session:
                print(f"⚠️ Ronda {race_id:02d} ({session_type}): aún sin sesión en OpenF1.")
                continue

            print(f"\n🏁 {season} Ronda {race_id:02d} — {race_info['raceName']} [{session_type}]")
            try:
                payload = process_session(
                    openf1=openf1,
                    season=season,
                    race_id=race_id,
                    session_type=session_type,
                    race_info=race_info,
                    openf1_session=openf1_session,
                    samples=args.samples,
                )
            except OpenF1LiveSessionError as e:
                print(f"⏸️ {e} Deteniendo descarga limpia.")
                return

            if not payload:
                continue

            os.makedirs(out_dir, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
                f.write("\n")

            print(f"  📁 Guardado: {out_path}")

    print("\n✅ Proceso finalizado.")


if __name__ == "__main__":
    main()
