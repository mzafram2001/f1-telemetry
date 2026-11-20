import json
import re
from datetime import datetime
from pathlib import Path

from meta_utils import ASSETS_BASE_URL

DATA_DIR = Path("data")
README_PATH = Path("README.md")

COMPOUND_SHORT = {
    "SOFT": "S",
    "MEDIUM": "M",
    "HARD": "H",
    "INTERMEDIATE": "INT",
    "WET": "WET",
}


def format_lap_time(seconds):
    if seconds is None:
        return "—"
    mins = int(seconds // 60)
    rem = seconds % 60
    return f"{mins}:{rem:06.3f}" if mins > 0 else f"{rem:.3f}s"


def format_strategy(stints):
    if not stints:
        return "—"
    valid = [
        s for s in stints
        if s.get("Compound")
        and (
            s.get("Lap_Start") is None
            or s.get("Lap_End") is None
            or s["Lap_End"] >= s["Lap_Start"]
        )
    ]
    labels = [COMPOUND_SHORT.get(s["Compound"].upper(), s["Compound"]) for s in valid]
    return " → ".join(labels) if labels else "—"


def get_latest_race_file():
    """Devuelve la ruta al race.json más reciente de todo el repositorio."""
    seasons = sorted(
        [d for d in DATA_DIR.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda p: int(p.name),
        reverse=True,
    )
    for season_dir in seasons:
        rounds = sorted(
            [d for d in season_dir.iterdir() if d.is_dir() and d.name.startswith("round_")],
            reverse=True,
        )
        for r_dir in rounds:
            race_file = r_dir / "race.json"
            if race_file.exists():
                return race_file
    return None


def generate_race_markdown(race_json_path):
    with race_json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("Meta", {})
    weather = data.get("Weather", {})
    season = meta.get("Season", "N/A")
    race_id = meta.get("Race_Id", 0)
    event_name = meta.get("Event_Name") or ""
    circuit = meta.get("Circuit") or {}
    circuit_svg = circuit.get("Svg_Path")

    event_suffix = f" · {event_name}" if event_name else ""
    circuit_img = ""
    if circuit_svg:
        circuit_url = f"{ASSETS_BASE_URL}/{circuit_svg}"
        circuit_img = f" <img src='{circuit_url}' width='28' height='20' align='center'>"

    # Línea de meteorología
    weather_parts = []
    if weather.get("Air_Temp_Avg") is not None:
        weather_parts.append(f"🌡️ Air {weather['Air_Temp_Avg']:.1f}°C")
    if weather.get("Track_Temp_Avg") is not None:
        weather_parts.append(f"🛣️ Track {weather['Track_Temp_Avg']:.1f}°C")
    if weather.get("Humidity_Avg") is not None:
        weather_parts.append(f"💧 {weather['Humidity_Avg']:.0f}%")
    if weather.get("Rainfall") is not None:
        weather_parts.append("🌧️ Wet" if weather["Rainfall"] else "☀️ Dry")
    weather_line = f"> {' · '.join(weather_parts)}" if weather_parts else ""

    md = [
        "<details open>",
        f"<summary><b>🏎️ {season} Season — Round {race_id:02d}{event_suffix} (Race)</b>{circuit_img}</summary>",
        "",
    ]

    if weather_line:
        md.extend([weather_line, ""])

    md.extend([
        "#### 👤 Race Telemetry — Drivers",
        "",
        "| Pos | Driver | Fastest lap | Avg pace | Top speed | Stops | Strategy |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    for d in data.get("Drivers", []):
        pos = d.get("Finish_Position")
        pos_str = f"P{pos}" if pos is not None else "—"
        if d.get("Status") and d["Status"] != "Finished" and not str(d["Status"]).startswith("+"):
            pos_str = f"{pos_str} *(DNF)*"

        d_code = (d.get("Driver_Code") or "unk").lower()
        t_code = (d.get("Team_Code") or "unk").lower()
        driver_url = f"{ASSETS_BASE_URL}/src/drivers/{d_code}-{t_code}-{season}.svg"
        img_driver = f"<img src='{driver_url}' width='44' height='32' align='center'>"

        fl = format_lap_time(d.get("Fastest_Lap_Sec"))
        pace = format_lap_time(d.get("Avg_Pace_Sec"))
        speed = f"{d['Top_Speed_Kmh']} km/h" if d.get("Top_Speed_Kmh") is not None else "—"
        stops = d.get("Pit_Stops", 0)
        strat = format_strategy(d.get("Stints", []))

        md.append(f"| {pos_str} | {img_driver} | {fl} | {pace} | {speed} | {stops} | {strat} |")

    md.extend([
        "",
        "#### 🏢 Race Telemetry — Constructors",
        "",
        "| Team | Avg pace | Top speed | Avg pit lane | Laps |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ])

    for t in data.get("Teams", []):
        t_code = (t.get("Team_Code") or "unk").lower()
        team_url = f"{ASSETS_BASE_URL}/src/constructors/{t_code}-{season}.svg"
        img_team = f"<img src='{team_url}' width='44' height='32' align='center'>"

        pace = format_lap_time(t.get("Avg_Pace_Sec"))
        speed = f"{t['Top_Speed_Kmh']} km/h" if t.get("Top_Speed_Kmh") is not None else "—"
        pit_lane = f"{t['Avg_Pit_Lane_Sec']:.2f}s" if t.get("Avg_Pit_Lane_Sec") is not None else "—"
        laps = t.get("Total_Laps_Completed", 0)

        md.append(f"| {img_team} | {pace} | {speed} | {pit_lane} | {laps} |")

    md.extend(["", "</details>", ""])
    return "\n".join(md)


def update_readme():
    if not DATA_DIR.exists():
        print(f"Directory '{DATA_DIR}' not found.")
        return

    latest_race_file = get_latest_race_file()
    if not latest_race_file:
        print("No race.json files found in data/.")
        return

    if not README_PATH.exists():
        print(f"'{README_PATH}' not found.")
        return

    readme_content = README_PATH.read_text(encoding="utf-8")
    latest_summary_md = generate_race_markdown(latest_race_file)

    summary_pattern = r"(<!-- SEASONS_SUMMARY_START -->)(.*?)(<!-- SEASONS_SUMMARY_END -->)"
    if re.search(summary_pattern, readme_content, flags=re.DOTALL):
        readme_content = re.sub(
            summary_pattern, f"\\1\n{latest_summary_md}\n\\3", readme_content, flags=re.DOTALL
        )

    today_str = datetime.now().strftime("%Y-%m-%d")
    checked_pattern = r"(<!-- LAST_CHECKED_START -->)(.*?)(<!-- LAST_CHECKED_END -->)"
    if re.search(checked_pattern, readme_content, flags=re.DOTALL):
        readme_content = re.sub(
            checked_pattern, f"\\1Last checked: {today_str}\\3", readme_content, flags=re.DOTALL
        )

    README_PATH.write_text(readme_content, encoding="utf-8")
    print(f"README.md updated successfully with {latest_race_file}!")


if __name__ == "__main__":
    update_readme()
