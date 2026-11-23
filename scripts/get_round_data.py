import argparse
import bisect
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from meta_utils import (
    HEADERS,
    build_meta,
    fetch_driver_of_the_day,
    fetch_season_schedule,
    fetch_session_results,
    format_driver_name,
    normalize_team,
    to_iso_utc,
)

OPENF1_BASE = "https://api.openf1.org/v1"
SESSION_FILES = {
    "Qualifying": "qualifying.json",
    "Sprint": "sprint.json",
    "Race": "race.json",
}
SESSION_ORDER = [
    ("Qualifying", "qualifying.json"),
    ("Sprint", "sprint.json"),
    ("Race", "race.json"),
]
DNS_STATUS_ALIASES = {"did not start", "dns", "withdrew", "wd", "injured", "illness"}

RACE_POINTS_TABLE = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
SPRINT_POINTS_TABLE = {1: 8, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}

TRACK_STATUS_SEVERITY = {"GREEN": 0, "YELLOW": 1, "VSC": 2, "SC": 3, "RED": 4}
RC_KEYWORDS = (
    "PENALTY",
    "INVESTIGATION",
    "NOTED",
    "INCIDENT",
    "BLACK AND WHITE",
    "SAFETY CAR",
    "RED FLAG",
    "ABORTED",
    "SUSPENDED",
    "RESUMED",
    "CHEQUERED",
    "DRS",
    "WET TRACK",
    "RAIN",
)


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


def is_dns_status(status):
    return (status or "").strip().lower() in DNS_STATUS_ALIASES


def is_dnf_status(status):
    s = (status or "").strip().lower()
    if not s or s in ("finished", "lapped", "unknown") or s.startswith("+"):
        return False
    if s in DNS_STATUS_ALIASES or s in ("disqualified", "dsq", "did not qualify", "dnq"):
        return False
    return True


def load_json_safe(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def extract_results_from_payload(payload):
    """Extrae clasificación oficial indexada por Driver_Code desde un JSON ya guardado."""
    if not payload:
        return {}
    results = {}
    for d in payload.get("Drivers", []):
        code = (d.get("Driver_Code") or "").upper()
        if not code or code == "N/A":
            continue
        results[code] = {
            "Driver_Name": d.get("Driver_Name"),
            "Driver_Number": d.get("Driver_Number"),
            "Nationality": d.get("Nationality"),
            "Team_Name": d.get("Team_Name", "N/A"),
            "Team_Code": d.get("Team_Code", "N/A"),
            "Grid_Position": d.get("Grid_Position"),
            "Finish_Position": d.get("Finish_Position"),
            "Status": d.get("Status"),
            "Points": d.get("Points"),
            "Laps_Completed": d.get("Laps_Completed"),
            "Race_Time_Text": d.get("Race_Time_Text"),
            "Race_Time_Ms": d.get("Race_Time_Ms"),
            "Gap_To_Leader_Sec": d.get("Gap_To_Leader_Sec"),
            "Fastest_Lap_Rank": d.get("Fastest_Lap_Rank"),
            "Qualifying_Times": d.get("Qualifying_Times"),
        }
    return results


def build_empty_driver_entry(code, info, status, grid_pos=None, finish_pos=None, laps=0, session_type="Race"):
    """Construye una entrada sin telemetría para pilotos DNS o retirados tras Qualifying."""
    return {
        "Driver_Name": info.get("Driver_Name") or code,
        "Driver_Code": code,
        "Driver_Number": info.get("Driver_Number"),
        "Nationality": info.get("Nationality"),
        "Team_Name": info.get("Team_Name", "N/A"),
        "Team_Code": info.get("Team_Code", "N/A"),
        "Grid_Position": grid_pos,
        "Finish_Position": finish_pos,
        "Status": normalize_status(status),
        "Points": 0,
        "Laps_Completed": laps,
        "Race_Time_Text": None,
        "Race_Time_Ms": None,
        "Gap_To_Leader_Sec": None,
        "Fastest_Lap_Rank": None,
        "Qualifying_Times": info.get("Qualifying_Times") if session_type == "Qualifying" else None,
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
        "Pit_Stop_Details": [],
        "Stints": [],
        "Laps": [],
        "Fastest_Lap_Trace": None,
    }


def match_openf1_session(openf1_sessions, session_type, scheduled_iso):
    """Empareja la sesión oficial con su session_key en OpenF1."""
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
            "Air_Temp_Min": None,
            "Air_Temp_Max": None,
            "Track_Temp_Avg": None,
            "Track_Temp_Min": None,
            "Track_Temp_Max": None,
            "Humidity_Avg": None,
            "Pressure_Avg": None,
            "Wind_Speed_Avg": None,
            "Rainfall": None,
        }

    def stats_field(key):
        vals = [float(r[key]) for r in weather_rows if r.get(key) is not None]
        if not vals:
            return None, None, None
        return round(statistics.mean(vals), 2), round(min(vals), 2), round(max(vals), 2)

    air_avg, air_min, air_max = stats_field("air_temperature")
    trk_avg, trk_min, trk_max = stats_field("track_temperature")
    hum_avg, _, _ = stats_field("humidity")
    prs_avg, _, _ = stats_field("pressure")
    wnd_avg, _, _ = stats_field("wind_speed")
    rainfall = any(bool(r.get("rainfall")) for r in weather_rows)

    return {
        "Air_Temp_Avg": air_avg,
        "Air_Temp_Min": air_min,
        "Air_Temp_Max": air_max,
        "Track_Temp_Avg": trk_avg,
        "Track_Temp_Min": trk_min,
        "Track_Temp_Max": trk_max,
        "Humidity_Avg": hum_avg,
        "Pressure_Avg": prs_avg,
        "Wind_Speed_Avg": wnd_avg,
        "Rainfall": rainfall,
    }


def process_race_control(rc_rows):
    """Extrae eventos de dirección de carrera, contadores y línea temporal de estados de pista."""
    if not rc_rows:
        return [], [], {"Safety_Car_Count": 0, "VSC_Count": 0, "Red_Flag_Count": 0}

    sorted_rows = sorted(rc_rows, key=lambda r: r.get("date") or "")
    filtered_events = []
    status_timeline = []
    sc_count = 0
    vsc_count = 0
    red_count = 0
    current_state = "GREEN"

    for r in sorted_rows:
        cat = (r.get("category") or "").strip()
        flag = (r.get("flag") or "").strip().upper() or None
        scope = (r.get("scope") or "").strip() or None
        msg = (r.get("message") or "").strip()
        msg_up = msg.upper()
        dt = parse_iso_utc(r.get("date"))
        ts = dt.timestamp() if dt else None

        new_state = None
        if flag == "RED" or "RED FLAG" in msg_up:
            new_state = "RED"
            if current_state != "RED":
                red_count += 1
        elif "VIRTUAL SAFETY CAR DEPLOYED" in msg_up:
            new_state = "VSC"
            if current_state != "VSC":
                vsc_count += 1
        elif "SAFETY CAR DEPLOYED" in msg_up and "VIRTUAL" not in msg_up:
            new_state = "SC"
            if current_state != "SC":
                sc_count += 1
        elif (
            "VIRTUAL SAFETY CAR ENDING" in msg_up
            or "SAFETY CAR IN THIS LAP" in msg_up
            or "TRACK CLEAR" in msg_up
            or (flag in ("GREEN", "CLEAR") and (scope or "").lower() in ("track", ""))
        ):
            new_state = "GREEN"
        elif flag in ("YELLOW", "DOUBLE YELLOW") and current_state == "GREEN":
            new_state = "YELLOW"

        if new_state and ts is not None:
            current_state = new_state
            status_timeline.append((ts, new_state))

        keep = False
        if cat == "SafetyCar":
            keep = True
        elif cat == "Flag" and flag not in ("BLUE", "CLEAR", None):
            keep = True
        elif any(kw in msg_up for kw in RC_KEYWORDS):
            keep = True

        if keep:
            filtered_events.append({
                "Lap": r.get("lap_number"),
                "Date": r.get("date"),
                "Category": cat or None,
                "Flag": flag,
                "Scope": scope,
                "Message": msg,
            })

    return filtered_events, status_timeline, {
        "Safety_Car_Count": sc_count,
        "VSC_Count": vsc_count,
        "Red_Flag_Count": red_count,
    }


def get_track_status_for_interval(status_timeline, start_ts, end_ts):
    if not status_timeline or start_ts is None:
        return "GREEN"

    ts_list = [item[0] for item in status_timeline]
    idx = bisect.bisect_right(ts_list, start_ts) - 1
    worst = status_timeline[idx][1] if idx >= 0 else "GREEN"

    if end_ts is not None:
        scan_idx = max(idx + 1, 0)
        while scan_idx < len(status_timeline) and status_timeline[scan_idx][0] <= end_ts:
            st = status_timeline[scan_idx][1]
            if TRACK_STATUS_SEVERITY.get(st, 0) > TRACK_STATUS_SEVERITY.get(worst, 0):
                worst = st
            scan_idx += 1

    return worst


def build_position_index(pos_rows):
    by_num = {}
    for r in pos_rows:
        drv = r.get("driver_number")
        pos = r.get("position")
        dt = parse_iso_utc(r.get("date"))
        if drv is not None and pos is not None and dt is not None:
            by_num.setdefault(drv, []).append((dt.timestamp(), int(pos)))

    indexed = {}
    for drv, items in by_num.items():
        items.sort(key=lambda x: x[0])
        indexed[drv] = ([x[0] for x in items], [x[1] for x in items])
    return indexed


def lookup_position_at(pos_index, drv_num, target_ts, fallback=None):
    if drv_num not in pos_index or target_ts is None:
        return fallback
    ts_list, pos_list = pos_index[drv_num]
    idx = bisect.bisect_right(ts_list, target_ts + 2.0) - 1
    if idx >= 0:
        return pos_list[idx]
    return fallback if fallback is not None else pos_list[0]


def lookup_stint_for_lap(formatted_stints, lap_num):
    for st in formatted_stints:
        l_start = st.get("Lap_Start")
        l_end = st.get("Lap_End")
        if l_start is not None and l_end is not None and l_start <= lap_num <= l_end:
            base_age = st.get("Tyre_Age_At_Start") or 0
            return st.get("Compound"), base_age + (lap_num - l_start)
    return None, None


def build_laps_detail(
    driver_laps,
    formatted_stints,
    pit_lap_set,
    pos_index,
    drv_num,
    status_timeline,
    laps_completed,
    grid_pos,
    finish_pos,
    session_type,
):
    """Construye el desglose completo vuelta a vuelta."""
    by_num = {}
    for lap in driver_laps:
        num = lap.get("lap_number")
        if isinstance(num, int) and num > 0:
            by_num[num] = lap

    if not by_num:
        return []

    max_lap = max(by_num.keys())
    if laps_completed is not None and laps_completed > 0 and max_lap > laps_completed:
        last_lap = by_num.get(max_lap)
        if last_lap and last_lap.get("lap_duration") is None:
            max_lap = laps_completed

    detailed = []
    for n in range(1, max_lap + 1):
        lap = by_num.get(n)
        if not lap:
            continue

        start_dt = parse_iso_utc(lap.get("date_start"))
        start_ts = start_dt.timestamp() if start_dt else None

        dur = lap.get("lap_duration")
        dur_sec = round(float(dur), 3) if dur is not None else None

        if dur_sec is None and n == 1 and start_dt and (n + 1) in by_num:
            next_dt = parse_iso_utc(by_num[n + 1].get("date_start"))
            if next_dt:
                diff = (next_dt - start_dt).total_seconds()
                if 40.0 < diff < 300.0:
                    dur_sec = round(diff, 3)

        end_ts = None
        if start_ts is not None and dur_sec is not None:
            end_ts = start_ts + dur_sec
        elif (n + 1) in by_num:
            next_dt = parse_iso_utc(by_num[n + 1].get("date_start"))
            if next_dt:
                end_ts = next_dt.timestamp()

        pos = None
        if session_type in ("Race", "Sprint"):
            pos = lookup_position_at(pos_index, drv_num, end_ts or start_ts, fallback=grid_pos)
            if n == max_lap and finish_pos is not None and laps_completed == max_lap:
                pos = finish_pos

        compound, tyre_age = lookup_stint_for_lap(formatted_stints, n)
        track_status = get_track_status_for_interval(status_timeline, start_ts, end_ts)

        def clean_sec(key):
            v = lap.get(key)
            return round(float(v), 3) if v is not None else None

        speed_trap = lap.get("st_speed")
        speed_trap_int = int(speed_trap) if speed_trap is not None else None

        detailed.append({
            "Lap": n,
            "Time_Sec": dur_sec,
            "S1_Sec": clean_sec("duration_sector_1"),
            "S2_Sec": clean_sec("duration_sector_2"),
            "S3_Sec": clean_sec("duration_sector_3"),
            "Position": pos,
            "Compound": compound,
            "Tyre_Age": tyre_age,
            "Speed_Trap_Kmh": speed_trap_int,
            "Is_Pit_Out": bool(lap.get("is_pit_out_lap")),
            "Is_Pit_In": n in pit_lap_set,
            "Track_Status": track_status,
        })

    return detailed


def compute_avg_pace(driver_laps, session_type):
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


def clamp_pct(val):
    return min(100, max(0, int(val or 0)))


def build_trace_and_metrics(car_rows, loc_rows=None, target_samples=100):
    if not car_rows:
        return None, None, None, None

    car_rows = sorted(car_rows, key=lambda r: r.get("date") or "")

    speeds = [int(r["speed"]) for r in car_rows if r.get("speed") is not None]
    throttles = [clamp_pct(r["throttle"]) for r in car_rows if r.get("throttle") is not None]
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
        "Throttle_Pct": [clamp_pct(r.get("throttle")) for r in sampled],
        "Brake_Pct": [100 if (r.get("brake") or 0) > 0 else 0 for r in sampled],
        "Gear": [int(r.get("n_gear") or 0) for r in sampled],
        "Rpm": [int(r.get("rpm") or 0) for r in sampled],
        "Drs": [int(r.get("drs") or 0) for r in sampled],
        "X": xs,
        "Y": ys,
    }
    return trace, top_speed, full_throttle_pct, brake_pct


def assign_fallback_points(drivers, season, session_type):
    if session_type not in ("Race", "Sprint"):
        for d in drivers:
            if d.get("Points") is None:
                d["Points"] = 0
        return

    fl_candidates = [d for d in drivers if d.get("Fastest_Lap_Sec") is not None]
    fl_driver_code = (
        min(fl_candidates, key=lambda d: d["Fastest_Lap_Sec"])["Driver_Code"]
        if fl_candidates
        else None
    )

    table = RACE_POINTS_TABLE if session_type == "Race" else SPRINT_POINTS_TABLE
    for d in drivers:
        if d.get("Points") is not None:
            continue
        pos = d.get("Finish_Position")
        status_low = (d.get("Status") or "").strip().lower()
        if pos is None or "disqualified" in status_low or status_low == "dsq" or is_dns_status(status_low):
            d["Points"] = 0
            continue
        pts = table.get(pos, 0)
        if session_type == "Race" and season <= 2024 and pos <= 10 and d["Driver_Code"] == fl_driver_code:
            pts += 1
        d["Points"] = pts


def aggregate_teams(processed_drivers):
    teams_bucket = {}
    for d in processed_drivers:
        t_code = d["Team_Code"]
        bucket = teams_bucket.setdefault(
            t_code,
            {
                "Team_Name": d["Team_Name"],
                "Team_Code": t_code,
                "points": 0.0,
                "positions": [],
                "paces": [],
                "speeds": [],
                "pit_lanes": [],
                "pit_stops_durs": [],
                "total_stops": 0,
                "laps": 0,
            },
        )
        bucket["points"] += float(d.get("Points") or 0)
        if d.get("Finish_Position") is not None:
            bucket["positions"].append(d["Finish_Position"])
        if d.get("Avg_Pace_Sec") is not None:
            bucket["paces"].append(d["Avg_Pace_Sec"])
        if d.get("Top_Speed_Kmh") is not None:
            bucket["speeds"].append(d["Top_Speed_Kmh"])
        if d.get("Avg_Pit_Lane_Sec") is not None:
            bucket["pit_lanes"].append(d["Avg_Pit_Lane_Sec"])
        for p in d.get("Pit_Stop_Details", []):
            if p.get("Stop_Sec") is not None:
                bucket["pit_stops_durs"].append(p["Stop_Sec"])
        if not d.get("Pit_Stop_Details") and d.get("Avg_Pit_Stop_Sec") is not None:
            bucket["pit_stops_durs"].append(d["Avg_Pit_Stop_Sec"])
        bucket["total_stops"] += d.get("Pit_Stops") or 0
        bucket["laps"] += d.get("Laps_Completed") or 0

    processed_teams = []
    for b in teams_bucket.values():
        pts_val = int(b["points"]) if b["points"].is_integer() else round(b["points"], 2)
        processed_teams.append({
            "Team_Name": b["Team_Name"],
            "Team_Code": b["Team_Code"],
            "Points": pts_val,
            "Best_Finish": min(b["positions"]) if b["positions"] else None,
            "Avg_Pace_Sec": round(statistics.mean(b["paces"]), 3) if b["paces"] else None,
            "Top_Speed_Kmh": max(b["speeds"]) if b["speeds"] else None,
            "Total_Pit_Stops": b["total_stops"],
            "Avg_Pit_Lane_Sec": round(statistics.mean(b["pit_lanes"]), 3) if b["pit_lanes"] else None,
            "Avg_Pit_Stop_Sec": round(statistics.mean(b["pit_stops_durs"]), 3) if b["pit_stops_durs"] else None,
            "Best_Pit_Stop_Sec": round(min(b["pit_stops_durs"]), 3) if b["pit_stops_durs"] else None,
            "Total_Laps_Completed": b["laps"],
        })

    processed_teams.sort(
        key=lambda x: (
            -(x["Points"] or 0),
            x["Best_Finish"] is None,
            x["Best_Finish"] or 99,
            x["Avg_Pace_Sec"] is None,
            x["Avg_Pace_Sec"] or 9999.0,
        )
    )
    return processed_teams


def build_session_summary(drivers, rc_counts, dotd=None):
    winner = next((d for d in drivers if d.get("Finish_Position") == 1), None)
    fl_candidates = [d for d in drivers if d.get("Fastest_Lap_Sec") is not None]
    fastest = min(fl_candidates, key=lambda d: d["Fastest_Lap_Sec"]) if fl_candidates else None
    total_laps = max((d.get("Laps_Completed") or 0 for d in drivers), default=0)

    dnf_count = sum(1 for d in drivers if is_dnf_status(d.get("Status")))
    dns_count = sum(1 for d in drivers if is_dns_status(d.get("Status")))

    return {
        "Total_Laps": total_laps,
        "Winner_Code": winner["Driver_Code"] if winner else None,
        "Winner_Name": winner["Driver_Name"] if winner else None,
        "Driver_Of_The_Day": dotd,
        "Fastest_Lap_Driver": fastest["Driver_Code"] if fastest else None,
        "Fastest_Lap_Sec": fastest["Fastest_Lap_Sec"] if fastest else None,
        "Fastest_Lap_Number": fastest["Fastest_Lap_Number"] if fastest else None,
        "Safety_Car_Count": rc_counts["Safety_Car_Count"],
        "VSC_Count": rc_counts["VSC_Count"],
        "Red_Flag_Count": rc_counts["Red_Flag_Count"],
        "DNF_Count": dnf_count,
        "DNS_Count": dns_count,
    }


def process_session(
    openf1,
    season,
    race_id,
    session_type,
    samples,
    race_info=None,
    openf1_session=None,
    existing_payload=None,
):
    session_key = (
        openf1_session["session_key"]
        if openf1_session
        else (existing_payload or {}).get("Meta", {}).get("OpenF1_Session_Key")
    )
    if not session_key:
        print(f"  ⚠️ Sin OpenF1_Session_Key para {season} R{race_id:02d} ({session_type}).")
        return None

    f1db_results = fetch_session_results(season, race_id, session_type)
    if not f1db_results and existing_payload:
        f1db_results = extract_results_from_payload(existing_payload)
        print(f"  ℹ️ F1DB aún sin resultados para R{race_id:02d} ({session_type}); usando caché local.")

    print(f"  📡 Descargando {session_type} (session_key={session_key}, F1DB + OpenF1)...")

    drivers_raw = openf1.get("drivers", {"session_key": session_key})
    laps_raw = openf1.get("laps", {"session_key": session_key})
    stints_raw = openf1.get("stints", {"session_key": session_key})
    weather_raw = openf1.get("weather", {"session_key": session_key})
    rc_raw = openf1.get("race_control", {"session_key": session_key})
    pos_raw = (
        openf1.get("position", {"session_key": session_key})
        if session_type in ("Race", "Sprint")
        else []
    )
    pits_raw = (
        openf1.get("pit", {"session_key": session_key})
        if session_type in ("Race", "Sprint")
        else []
    )

    if not drivers_raw:
        print(f"  ⚠️ Sin pilotos en OpenF1 para session_key={session_key}")
        return None

    rc_events, status_timeline, rc_counts = process_race_control(rc_raw)
    pos_index = build_position_index(pos_raw)

    laps_by_num = {}
    for lap in laps_raw:
        laps_by_num.setdefault(lap["driver_number"], []).append(lap)

    stints_by_num = {}
    for st in stints_raw:
        stints_by_num.setdefault(st["driver_number"], []).append(st)

    pits_by_num = {}
    for p in pits_raw:
        pits_by_num.setdefault(p["driver_number"], []).append(p)

    unique_drivers = {}
    for d in drivers_raw:
        unique_drivers[d["driver_number"]] = d

    processed_drivers = []
    processed_codes = set()

    for drv_num, d in unique_drivers.items():
        code = (d.get("name_acronym") or "").upper()
        if code:
            processed_codes.add(code)

        f_res = f1db_results.get(code, {})
        norm_status = normalize_status(f_res.get("Status"))
        dns_flag = is_dns_status(norm_status)

        if f_res.get("Driver_Name"):
            driver_name = f_res["Driver_Name"]
        else:
            driver_name = format_driver_name(d.get("first_name"), d.get("last_name"))

        if f_res.get("Team_Code") and f_res["Team_Code"] != "N/A":
            team_name, team_code = f_res["Team_Name"], f_res["Team_Code"]
        else:
            team_name, team_code = normalize_team(d.get("team_name"))

        if dns_flag:
            processed_drivers.append(
                build_empty_driver_entry(
                    code=code or "N/A",
                    info={
                        "Driver_Name": driver_name,
                        "Driver_Number": drv_num,
                        "Nationality": f_res.get("Nationality"),
                        "Team_Name": team_name,
                        "Team_Code": team_code,
                        "Qualifying_Times": f_res.get("Qualifying_Times"),
                    },
                    status="Did not start",
                    grid_pos=f_res.get("Grid_Position"),
                    finish_pos=f_res.get("Finish_Position"),
                    laps=0,
                    session_type=session_type,
                )
            )
            continue

        d_laps = laps_by_num.get(drv_num, [])
        timed_laps = [l for l in d_laps if l.get("lap_duration") is not None]
        fastest_lap = min(timed_laps, key=lambda l: l["lap_duration"]) if timed_laps else None

        def best_sector(field):
            vals = [float(l[field]) for l in d_laps if l.get(field) is not None]
            return round(min(vals), 3) if vals else None

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

        if top_speed is None:
            trap_speeds = [
                int(l[k])
                for l in d_laps
                for k in ("st_speed", "i1_speed", "i2_speed")
                if l.get(k) is not None
            ]
            top_speed = max(trap_speeds) if trap_speeds else None

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

        d_pits = sorted(pits_by_num.get(drv_num, []), key=lambda p: p.get("lap_number") or 0)
        pit_details = []
        lane_durs = []
        stop_durs = []
        pit_lap_set = set()
        for p in d_pits:
            p_lap = p.get("lap_number")
            lane_v = p.get("lane_duration") if p.get("lane_duration") is not None else p.get("pit_duration")
            stop_v = p.get("stop_duration")
            lane_f = round(float(lane_v), 3) if lane_v is not None else None
            stop_f = round(float(stop_v), 3) if stop_v is not None else None
            if lane_f is not None:
                lane_durs.append(lane_f)
            if stop_f is not None:
                stop_durs.append(stop_f)
            if isinstance(p_lap, int):
                pit_lap_set.add(p_lap)
            pit_details.append({
                "Lap": p_lap,
                "Lane_Sec": lane_f,
                "Stop_Sec": stop_f,
            })

        laps_completed = f_res.get("Laps_Completed")
        if laps_completed is None and d_laps:
            laps_completed = max(l.get("lap_number") or 0 for l in d_laps)

        detailed_laps = build_laps_detail(
            driver_laps=d_laps,
            formatted_stints=formatted_stints,
            pit_lap_set=pit_lap_set,
            pos_index=pos_index,
            drv_num=drv_num,
            status_timeline=status_timeline,
            laps_completed=laps_completed,
            grid_pos=f_res.get("Grid_Position"),
            finish_pos=f_res.get("Finish_Position"),
            session_type=session_type,
        )

        driver_entry = {
            "Driver_Name": driver_name,
            "Driver_Code": code or "N/A",
            "Driver_Number": drv_num,
            "Nationality": f_res.get("Nationality"),
            "Team_Name": team_name,
            "Team_Code": team_code,
            "Grid_Position": f_res.get("Grid_Position"),
            "Finish_Position": f_res.get("Finish_Position"),
            "Status": norm_status,
            "Points": f_res.get("Points"),
            "Laps_Completed": laps_completed or 0,
            "Race_Time_Text": f_res.get("Race_Time_Text"),
            "Race_Time_Ms": f_res.get("Race_Time_Ms"),
            "Gap_To_Leader_Sec": f_res.get("Gap_To_Leader_Sec"),
            "Fastest_Lap_Rank": f_res.get("Fastest_Lap_Rank"),
            "Qualifying_Times": f_res.get("Qualifying_Times") if session_type == "Qualifying" else None,
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
            "Pit_Stop_Details": pit_details,
            "Stints": formatted_stints,
            "Laps": detailed_laps,
            "Fastest_Lap_Trace": trace,
        }
        processed_drivers.append(driver_entry)

    # 1. Pilotos presentes en F1DB que no emitieron en OpenF1 (DNS / WD)
    for code, f_res in f1db_results.items():
        if code in processed_codes:
            continue
        processed_codes.add(code)
        processed_drivers.append(
            build_empty_driver_entry(
                code=code,
                info=f_res,
                status=f_res.get("Status") or "Did not start",
                grid_pos=f_res.get("Grid_Position"),
                finish_pos=f_res.get("Finish_Position"),
                laps=f_res.get("Laps_Completed") or 0,
                session_type=session_type,
            )
        )

    # 2. Doble red de seguridad: pilotos en Qualifying que faltan en Race -> DNS
    if session_type == "Race":
        quali_results = fetch_session_results(season, race_id, "Qualifying")
        if not quali_results:
            local_quali = load_json_safe(f"data/{season}/round_{race_id:02d}/qualifying.json")
            quali_results = extract_results_from_payload(local_quali)

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
                    session_type=session_type,
                )
            )

    assign_fallback_points(processed_drivers, season, session_type)

    processed_drivers.sort(
        key=lambda x: (
            x["Finish_Position"] is None,
            x["Finish_Position"] or 99,
            x["Fastest_Lap_Sec"] is None,
            x["Fastest_Lap_Sec"] or 9999.0,
        )
    )

    dotd = fetch_driver_of_the_day(season, race_id) if session_type == "Race" else None

    if race_info is not None:
        meta_block = build_meta(
            season=season,
            race_id=race_id,
            session_type=session_type,
            race_info=race_info,
            openf1_session_key=session_key,
        )
    else:
        meta_block = dict((existing_payload or {}).get("Meta", {}))
        meta_block["OpenF1_Session_Key"] = session_key
        meta_block["Generated_At"] = datetime.now().isoformat()

    return {
        "Meta": meta_block,
        "Session_Summary": build_session_summary(processed_drivers, rc_counts, dotd=dotd),
        "Weather": summarize_weather(weather_raw),
        "Race_Control": rc_events,
        "Drivers": processed_drivers,
        "Teams": aggregate_teams(processed_drivers),
    }


def patch_local_file_with_f1db(season, race_id, session_type, out_path, race_info):
    """Inyecta datos oficiales de F1DB en un JSON ya descargado sin llamar a OpenF1."""
    payload = load_json_safe(out_path)
    if not payload:
        return False

    f1db_results = fetch_session_results(season, race_id, session_type)
    if not f1db_results:
        return False

    old_meta = payload.get("Meta", {})
    payload["Meta"] = build_meta(
        season=season,
        race_id=race_id,
        session_type=session_type,
        race_info=race_info,
        openf1_session_key=old_meta.get("OpenF1_Session_Key"),
    )

    drivers = payload.get("Drivers", [])
    existing_codes = set()
    for d in drivers:
        code = (d.get("Driver_Code") or "").upper()
        existing_codes.add(code)
        f_res = f1db_results.get(code)
        if not f_res:
            continue
        d["Driver_Name"] = f_res.get("Driver_Name") or d.get("Driver_Name")
        if f_res.get("Driver_Number") is not None:
            d["Driver_Number"] = f_res["Driver_Number"]
        d["Nationality"] = f_res.get("Nationality")
        if f_res.get("Team_Code") and f_res["Team_Code"] != "N/A":
            d["Team_Name"] = f_res["Team_Name"]
            d["Team_Code"] = f_res["Team_Code"]
        d["Grid_Position"] = f_res.get("Grid_Position")
        d["Finish_Position"] = f_res.get("Finish_Position")
        d["Status"] = normalize_status(f_res.get("Status"))
        d["Points"] = f_res.get("Points")
        if f_res.get("Laps_Completed") is not None:
            d["Laps_Completed"] = f_res["Laps_Completed"]
        d["Race_Time_Text"] = f_res.get("Race_Time_Text")
        d["Race_Time_Ms"] = f_res.get("Race_Time_Ms")
        d["Gap_To_Leader_Sec"] = f_res.get("Gap_To_Leader_Sec")
        d["Fastest_Lap_Rank"] = f_res.get("Fastest_Lap_Rank")
        if session_type == "Qualifying":
            d["Qualifying_Times"] = f_res.get("Qualifying_Times")

        if is_dns_status(d.get("Status")):
            d["Stints"] = []
            if "Laps" in d:
                d["Laps"] = []

    for code, f_res in f1db_results.items():
        if code in existing_codes:
            continue
        existing_codes.add(code)
        drivers.append(
            build_empty_driver_entry(
                code=code,
                info=f_res,
                status=f_res.get("Status") or "Did not start",
                grid_pos=f_res.get("Grid_Position"),
                finish_pos=f_res.get("Finish_Position"),
                laps=f_res.get("Laps_Completed") or 0,
                session_type=session_type,
            )
        )

    assign_fallback_points(drivers, season, session_type)
    drivers.sort(
        key=lambda x: (
            x.get("Finish_Position") is None,
            x.get("Finish_Position") or 99,
            x.get("Fastest_Lap_Sec") is None,
            x.get("Fastest_Lap_Sec") or 9999.0,
        )
    )
    payload["Drivers"] = drivers
    payload["Teams"] = aggregate_teams(drivers)

    if "Session_Summary" in payload:
        prev_sum = payload["Session_Summary"]
        rc_counts = {
            "Safety_Car_Count": prev_sum.get("Safety_Car_Count", 0),
            "VSC_Count": prev_sum.get("VSC_Count", 0),
            "Red_Flag_Count": prev_sum.get("Red_Flag_Count", 0),
        }
        dotd = fetch_driver_of_the_day(season, race_id) if session_type == "Race" else None
        payload["Session_Summary"] = build_session_summary(drivers, rc_counts, dotd=dotd)

    save_payload(out_path, payload)
    return True


def is_file_up_to_date(out_path):
    """Comprueba si el JSON ya cumple el esquema completo v2 (F1DB + OpenF1)."""
    payload = load_json_safe(out_path)
    if not payload:
        return False
    if "Session_Summary" not in payload or "Race_Control" not in payload:
        return False
    circuit = payload.get("Meta", {}).get("Circuit", {})
    if "Length_Km" not in circuit:
        return False
    drivers = payload.get("Drivers", [])
    if not drivers:
        return False
    for d in drivers:
        if "Laps" not in d or "Pit_Stop_Details" not in d or "Points" not in d:
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


def collect_local_sessions(season, target_round=None):
    season_dir = Path("data") / str(season)
    if not season_dir.exists():
        return []

    items = []
    for r_dir in sorted(season_dir.iterdir()):
        if not r_dir.is_dir():
            continue
        match = re.fullmatch(r"round_(\d+)", r_dir.name)
        if not match:
            continue
        race_id = int(match.group(1))
        if target_round is not None and race_id != target_round:
            continue
        for session_type, filename in SESSION_ORDER:
            path = r_dir / filename
            if path.exists():
                items.append((race_id, session_type, str(path)))
    return items


def save_payload(out_path, payload):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
        f.write("\n")
    print(f"  📁 Guardado: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Descarga telemetría F1 desde F1DB + OpenF1.")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--round", type=int, default=None, help="Procesar una sola ronda")
    parser.add_argument("--force", action="store_true", help="Sobreescribir archivos existentes")
    parser.add_argument("--samples", type=int, default=100, help="Puntos en Fastest_Lap_Trace")
    parser.add_argument(
        "--f1db-only",
        action="store_true",
        help="Parchear JSONs locales únicamente con F1DB (sin llamadas a OpenF1)",
    )
    args = parser.parse_args()

    season = args.season
    print(f"🚀 Comprobando rondas de telemetría para la temporada {season} (fuente: F1DB + OpenF1)...")

    schedule = fetch_season_schedule(season)
    if not schedule:
        print(f"❌ No se pudo obtener el calendario de F1DB para {season}.")
        sys.exit(1)

    if args.f1db_only:
        local_sessions = collect_local_sessions(season, target_round=args.round)
        updated = 0
        for race_id, session_type, out_path in local_sessions:
            race_info = schedule.get(race_id)
            if patch_local_file_with_f1db(season, race_id, session_type, out_path, race_info):
                updated += 1
        print(f"\n✅ Parcheo F1DB completado: {updated}/{len(local_sessions)} sesiones actualizadas.")
        return

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
            print(f"⚠️ Ronda {race_id} no encontrada en calendario F1DB {season}.")
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

            sched_iso = to_iso_utc(sched_obj["date"], sched_obj.get("time"))
            sched_dt = parse_iso_utc(sched_iso)

            if not sched_dt or (now_utc - sched_dt) < timedelta(hours=3):
                continue

            # Si es una ronda nueva y F1DB aún no ha publicado la release de ese GP, esperamos
            existing_payload = load_json_safe(out_path)
            if not existing_payload and not fetch_session_results(season, race_id, session_type):
                print(
                    f"⏳ {season} R{race_id:02d} ({session_type}): "
                    "esperando a la release oficial de F1DB."
                )
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
                    samples=args.samples,
                    race_info=race_info,
                    openf1_session=openf1_session,
                    existing_payload=existing_payload,
                )
            except OpenF1LiveSessionError as e:
                print(f"⏸️ {e} Deteniendo descarga limpia.")
                return

            if payload:
                save_payload(out_path, payload)

    print("\n✅ Proceso finalizado.")


if __name__ == "__main__":
    main()
