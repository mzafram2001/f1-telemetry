import json
import re
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data")
DNS_ALIASES = {"did not start", "dns", "withdrew", "wd", "injured", "illness"}


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, payload):
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
        f.write("\n")


def clean_num(val):
    return int(val) if float(val).is_integer() else round(float(val), 2)


def is_dns(status):
    return (status or "").strip().lower() in DNS_ALIASES


def is_dnf(status):
    s = (status or "").strip().lower()
    if not s or s in ("finished", "lapped", "unknown") or s.startswith("+"):
        return False
    if s in DNS_ALIASES or s in ("disqualified", "dsq", "did not qualify", "dnq"):
        return False
    return True


def build_season_standings(season, round_dirs):
    drivers_acc = {}
    teams_acc = {}
    round_entries = []
    race_rounds_done = 0

    for r_dir in round_dirs:
        race_id = int(re.fullmatch(r"round_(\d+)", r_dir.name).group(1))
        quali_path = r_dir / "qualifying.json"
        sprint_path = r_dir / "sprint.json"
        race_path = r_dir / "race.json"

        quali = load_json(quali_path) if quali_path.exists() else None
        sprint = load_json(sprint_path) if sprint_path.exists() else None
        race = load_json(race_path) if race_path.exists() else None

        primary = race or quali or sprint
        if not primary:
            continue

        meta = primary.get("Meta", {})
        sessions_avail = []
        if quali:
            sessions_avail.append("qualifying")
        if sprint:
            sessions_avail.append("sprint")
        if race:
            sessions_avail.append("race")

        pole_code = None
        if quali:
            pole_drv = next((d for d in quali.get("Drivers", []) if d.get("Finish_Position") == 1), None)
            if pole_drv:
                pole_code = pole_drv["Driver_Code"]

        round_driver_pts = {}
        round_team_pts = {}

        if sprint:
            for d in sprint.get("Drivers", []):
                code = d["Driver_Code"]
                t_code = d["Team_Code"]
                pts = float(d.get("Points") or 0)
                round_driver_pts[code] = round_driver_pts.get(code, 0.0) + pts
                round_team_pts[t_code] = round_team_pts.get(t_code, 0.0) + pts

                d_acc = drivers_acc.setdefault(code, {
                    "Driver_Code": code,
                    "Driver_Name": d["Driver_Name"],
                    "Driver_Number": d.get("Driver_Number"),
                    "Nationality": d.get("Nationality"),
                    "Team_Code": t_code,
                    "Team_Name": d["Team_Name"],
                    "Points": 0.0,
                    "Wins": 0,
                    "Sprint_Wins": 0,
                    "Podiums": 0,
                    "Poles": 0,
                    "Fastest_Laps": 0,
                    "DNFs": 0,
                    "DNSs": 0,
                    "Races_Entered": 0,
                    "Total_Laps": 0,
                    "Results_By_Round": [],
                })
                if d.get("Finish_Position") == 1:
                    d_acc["Sprint_Wins"] += 1

                teams_acc.setdefault(t_code, {
                    "Team_Code": t_code,
                    "Team_Name": d["Team_Name"],
                    "Points": 0.0,
                    "Wins": 0,
                    "Sprint_Wins": 0,
                    "Podiums": 0,
                    "Poles": 0,
                    "Fastest_Laps": 0,
                    "Total_Laps": 0,
                    "Results_By_Round": [],
                })
                if d.get("Finish_Position") == 1:
                    teams_acc[t_code]["Sprint_Wins"] += 1

        winner_code = None
        podium_codes = []
        fl_code = None
        dotd = None

        if race:
            race_rounds_done += 1
            dotd = (race.get("Session_Summary") or {}).get("Driver_Of_The_Day")
            r_drivers = race.get("Drivers", [])
            fl_cands = [d for d in r_drivers if d.get("Fastest_Lap_Sec") is not None]
            fl_code = min(fl_cands, key=lambda d: d["Fastest_Lap_Sec"])["Driver_Code"] if fl_cands else None

            round_team_best = {}
            round_team_laps = {}
            round_team_names = {}

            for d in r_drivers:
                code = d["Driver_Code"]
                t_code = d["Team_Code"]
                pos = d.get("Finish_Position")
                status = d.get("Status")
                pts = float(d.get("Points") or 0)
                laps = d.get("Laps_Completed") or 0

                if pos == 1:
                    winner_code = code
                if pos in (1, 2, 3):
                    podium_codes.append(code)

                round_driver_pts[code] = round_driver_pts.get(code, 0.0) + pts
                round_team_pts[t_code] = round_team_pts.get(t_code, 0.0) + pts
                round_team_laps[t_code] = round_team_laps.get(t_code, 0) + laps
                round_team_names[t_code] = d["Team_Name"]
                if pos is not None:
                    prev = round_team_best.get(t_code)
                    round_team_best[t_code] = pos if prev is None else min(prev, pos)

                d_acc = drivers_acc.setdefault(code, {
                    "Driver_Code": code,
                    "Driver_Name": d["Driver_Name"],
                    "Driver_Number": d.get("Driver_Number"),
                    "Nationality": d.get("Nationality"),
                    "Team_Code": t_code,
                    "Team_Name": d["Team_Name"],
                    "Points": 0.0,
                    "Wins": 0,
                    "Sprint_Wins": 0,
                    "Podiums": 0,
                    "Poles": 0,
                    "Fastest_Laps": 0,
                    "DNFs": 0,
                    "DNSs": 0,
                    "Races_Entered": 0,
                    "Total_Laps": 0,
                    "Results_By_Round": [],
                })
                d_acc["Driver_Name"] = d["Driver_Name"]
                d_acc["Team_Code"] = t_code
                d_acc["Team_Name"] = d["Team_Name"]
                if d.get("Driver_Number") is not None:
                    d_acc["Driver_Number"] = d["Driver_Number"]
                if d.get("Nationality"):
                    d_acc["Nationality"] = d["Nationality"]

                d_acc["Points"] += round_driver_pts[code]
                d_acc["Total_Laps"] += laps
                if is_dns(status):
                    d_acc["DNSs"] += 1
                else:
                    d_acc["Races_Entered"] += 1
                    if is_dnf(status):
                        d_acc["DNFs"] += 1
                if pos == 1:
                    d_acc["Wins"] += 1
                if pos in (1, 2, 3):
                    d_acc["Podiums"] += 1
                if code == pole_code:
                    d_acc["Poles"] += 1
                if code == fl_code:
                    d_acc["Fastest_Laps"] += 1

                d_acc["Results_By_Round"].append({
                    "Race_Id": race_id,
                    "Team_Code": t_code,
                    "Grid_Position": d.get("Grid_Position"),
                    "Finish_Position": pos,
                    "Status": status,
                    "Round_Points": clean_num(round_driver_pts[code]),
                    "Cumulative_Points": clean_num(d_acc["Points"]),
                })

                t_acc = teams_acc.setdefault(t_code, {
                    "Team_Code": t_code,
                    "Team_Name": d["Team_Name"],
                    "Points": 0.0,
                    "Wins": 0,
                    "Sprint_Wins": 0,
                    "Podiums": 0,
                    "Poles": 0,
                    "Fastest_Laps": 0,
                    "Total_Laps": 0,
                    "Results_By_Round": [],
                })
                if pos == 1:
                    t_acc["Wins"] += 1
                if pos in (1, 2, 3):
                    t_acc["Podiums"] += 1
                if code == pole_code:
                    t_acc["Poles"] += 1
                if code == fl_code:
                    t_acc["Fastest_Laps"] += 1

            for t_code, t_pts in round_team_pts.items():
                t_acc = teams_acc.setdefault(t_code, {
                    "Team_Code": t_code,
                    "Team_Name": round_team_names.get(t_code, t_code),
                    "Points": 0.0,
                    "Wins": 0,
                    "Sprint_Wins": 0,
                    "Podiums": 0,
                    "Poles": 0,
                    "Fastest_Laps": 0,
                    "Total_Laps": 0,
                    "Results_By_Round": [],
                })
                t_acc["Points"] += t_pts
                t_acc["Total_Laps"] += round_team_laps.get(t_code, 0)
                t_acc["Results_By_Round"].append({
                    "Race_Id": race_id,
                    "Best_Finish": round_team_best.get(t_code),
                    "Round_Points": clean_num(t_pts),
                    "Cumulative_Points": clean_num(t_acc["Points"]),
                })

        round_entries.append({
            "Id": meta.get("Id", f"{season}_{race_id:02d}"),
            "Race_Id": race_id,
            "Event_Name": meta.get("Event_Name"),
            "Is_Sprint": meta.get("Is_Sprint", False),
            "Circuit": meta.get("Circuit"),
            "Start_Times": meta.get("Start_Times"),
            "Sessions": sessions_avail,
            "Winner_Code": winner_code,
            "Podium_Codes": podium_codes,
            "Pole_Code": pole_code,
            "Fastest_Lap_Code": fl_code,
            "Driver_Of_The_Day": dotd,
        })

    drivers_list = []
    for d in drivers_acc.values():
        entered = d["Races_Entered"]
        d["Points"] = clean_num(d["Points"])
        d["Win_Rate"] = round(d["Wins"] / entered, 4) if entered > 0 else 0.0
        d["Podium_Rate"] = round(d["Podiums"] / entered, 4) if entered > 0 else 0.0
        drivers_list.append(d)

    drivers_list.sort(key=lambda x: (-x["Points"], -x["Wins"], -x["Podiums"], x["Driver_Code"]))
    for idx, d in enumerate(drivers_list, start=1):
        d["Position"] = idx

    teams_list = []
    for t in teams_acc.values():
        t["Points"] = clean_num(t["Points"])
        teams_list.append(t)

    teams_list.sort(key=lambda x: (-x["Points"], -x["Wins"], -x["Podiums"], x["Team_Code"]))
    for idx, t in enumerate(teams_list, start=1):
        t["Position"] = idx

    standings_payload = {
        "Season": season,
        "Completed_Rounds": race_rounds_done,
        "Generated_At": datetime.now().isoformat(),
        "Drivers_Championship": drivers_list,
        "Constructors_Championship": teams_list,
    }
    return standings_payload, round_entries


def build_all():
    if not DATA_DIR.exists():
        print("❌ No existe el directorio 'data/'.")
        return

    season_dirs = sorted(
        [d for d in DATA_DIR.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda p: int(p.name),
        reverse=True,
    )

    manifest_seasons = []
    for s_dir in season_dirs:
        season = int(s_dir.name)
        round_dirs = sorted(
            [d for d in s_dir.iterdir() if d.is_dir() and re.fullmatch(r"round_\d+", d.name)],
            key=lambda p: int(p.name.split("_")[1]),
        )
        if not round_dirs:
            continue

        standings_payload, round_entries = build_season_standings(season, round_dirs)
        standings_path = s_dir / "standings.json"
        write_json(standings_path, standings_payload)

        manifest_seasons.append({
            "Season": season,
            "Completed_Rounds": standings_payload["Completed_Rounds"],
            "Standings_Path": f"data/{season}/standings.json",
            "Rounds": round_entries,
        })
        print(f"📊 Índice generado: {standings_path} ({len(round_entries)} rondas)")

    manifest_payload = {
        "Generated_At": datetime.now().isoformat(),
        "Seasons": manifest_seasons,
    }
    manifest_path = DATA_DIR / "manifest.json"
    write_json(manifest_path, manifest_payload)
    print(f"✅ Manifest maestro actualizado: {manifest_path} ({len(manifest_seasons)} temporadas)")


if __name__ == "__main__":
    build_all()
