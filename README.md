# 📡 f1-telemetry

![Status](https://img.shields.io/badge/Status-Available-green)
![License](https://img.shields.io/badge/License-MIT-blue)
![Format](https://img.shields.io/badge/Format-JSON-orange)
![Stack](https://img.shields.io/badge/Stack-Python-yellow)
[![Update F1 Telemetry data](https://github.com/mzafram2001/f1-telemetry/actions/workflows/get_round_data.yml/badge.svg)](https://github.com/mzafram2001/f1-telemetry/actions/workflows/get_round_data.yml)
![Last updated](https://img.shields.io/github/last-commit/mzafram2001/f1-telemetry?label=Last%20update)

An open-source, automated historical dataset of Formula 1 session telemetry, race pace, tyre stints, weather, and fastest-lap traces.

Designed as a companion repository to [`f1-fantasy`](https://github.com/mzafram2001/f1-fantasy), sharing the exact same schema conventions, round IDs, `Driver_Code`, and `Team_Code` keys for seamless 1:1 joins across analytics and machine learning workflows.

## 📂 Repository structure

```text
f1-telemetry/
│
├── .github/
│   ├── workflows/
│   │   └── get_round_data.yml
│   │
│   └── FUNDING.yml
│
├── data/
│   ├── 2023/
│   │   ├── round_01/
│   │   │   ├── qualifying.json
│   │   │   └── race.json
│   │   └── ...
│   │
│   └── 2026/
│       ├── round_16/
│       │   ├── qualifying.json
│       │   └── race.json
│       └── ...
│
├── scripts/
│   ├── get_round_data.py
│   ├── meta_utils.py
│   ├── update_readme.py
│   └── validate_data.py
│
├── .gitignore
├── AUTHORS.md
├── LICENSE.md
└── README.md
```

## 📊 Data schema

Each session file (`data/{season}/round_{id}/{qualifying|sprint|race}.json`) follows a standardized `Pascal_Snake_Case` JSON schema divided into **Meta**, **Weather**, **Drivers**, and **Teams**.

### `Meta` & `Weather`

| Attribute | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `Id` | `String` | Unique round identifier (`{season}_{round}`) | `"2026_16"` |
| `Season` / `Race_Id` | `Integer` | Championship year and official round number | `2026` / `16` |
| `Event_Name` | `String` | Official Grand Prix name | `"Bahrain Grand Prix in Malaysia"` |
| `Session_Type` | `String` | Session category (`Qualifying`, `Sprint`, or `Race`) | `"Race"` |
| `Is_Sprint` | `Boolean` | Whether the weekend includes a Sprint session | `false` |
| `OpenF1_Session_Key` | `Integer` | Upstream session identifier in OpenF1 | `11731` |
| `Circuit` | `Object` | Circuit metadata (`Id`, `City`, `Country_Code`, `Country_Name`, `Svg_Path`) | `{"Id": "sepang", ...}` |
| `Start_Times` | `Object` | Session UTC timestamps (`Qualifying`, `Sprint_Qualifying`, `Sprint`, `Race`) | `{"Race": "2026-10-04T07:00:00Z", ...}` |
| `Weather` | `Object` | Session averages (`Air_Temp_Avg`, `Track_Temp_Avg`, `Humidity_Avg`, `Wind_Speed_Avg`, `Rainfall`) | `{"Track_Temp_Avg": 34.4, "Rainfall": true}` |

### `Drivers`

| Attribute | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `Driver_Name` / `Driver_Code` | `String` | Formatted driver name and three-letter identifier (TLA) | `"M. Verstappen"` / `"VER"` |
| `Driver_Number` | `Integer` | Permanent car number for the season | `3` |
| `Team_Name` / `Team_Code` | `String` | Constructor name and normalized TLA aligned with `f1-fantasy` | `"Red Bull Racing"` / `"RED"` |
| `Grid_Position` / `Finish_Position` | `Integer \| null` | Starting grid (`null` in Qualifying) and final classification | `1` / `1` |
| `Status` / `Laps_Completed` | `String` / `Integer` | Classification status and total laps completed | `"Finished"` / `55` |
| `Fastest_Lap_Sec` / `Fastest_Lap_Number` | `Float` / `Integer` | Personal best lap time in seconds and lap number | `98.22` / `55` |
| `Avg_Pace_Sec` | `Float` | Clean average pace in seconds (excludes pit-out and SC/VSC laps) | `101.6` |
| `Best_Sector_1/2/3_Sec` | `Float` | Best individual sector times in seconds | `25.327` |
| `Top_Speed_Kmh` | `Integer` | Peak speed recorded on the fastest lap (or speed traps) | `325` |
| `Full_Throttle_Pct` / `Brake_Pct` | `Float` | Share (`0.0`–`1.0`) of the fastest lap at $\ge 95\%$ throttle or braking | `0.4917` / `0.1972` |
| `Pit_Stops` | `Integer` | Total pit lane visits (`0` in Qualifying) | `3` |
| `Avg_Pit_Lane_Sec` / `Avg_Pit_Stop_Sec` | `Float \| null` | Mean total pit-lane time vs. stationary tyre-change time in seconds | `27.706` / `2.267` |
| `Stints` | `Array` | Tyre compounds, lap ranges, and tyre age at start of each stint | `[{"Compound": "SOFT", ...}]` |
| `Fastest_Lap_Trace` | `Object \| null` | Fastest lap resampled to `100` equidistant points (`Speed_Kmh`, `Throttle_Pct`, `Brake_Pct` binary `0`/`100`, `Gear`, `Rpm`) | `{"Samples": 100, ...}` |

### `Teams`

| Attribute | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `Team_Name` / `Team_Code` | `String` | Constructor name and normalized TLA | `"Red Bull Racing"` / `"RED"` |
| `Avg_Pace_Sec` | `Float \| null` | Mean clean pace across team cars in seconds | `101.871` |
| `Top_Speed_Kmh` | `Integer \| null` | Highest speed recorded by the constructor | `328` |
| `Avg_Pit_Lane_Sec` / `Avg_Pit_Stop_Sec` | `Float \| null` | Constructor average pit-lane and stationary stop durations in seconds | `27.842` / `2.534` |
| `Total_Laps_Completed` | `Integer` | Combined laps completed by both cars | `110` |

## 💻 Data results

<!-- SEASONS_SUMMARY_START -->
<!-- SEASONS_SUMMARY_END -->

## ⚡ Quick start

```python
import pandas as pd

# Load session telemetry directly from GitHub
url = "[https://raw.githubusercontent.com/mzafram2001/f1-telemetry/main/data/2026/round_16/race.json](https://raw.githubusercontent.com/mzafram2001/f1-telemetry/main/data/2026/round_16/race.json)"
data = pd.read_json(url, typ="series")

# Driver summary table
df_drivers = pd.DataFrame(data["Drivers"])
print(df_drivers[["Finish_Position", "Driver_Code", "Fastest_Lap_Sec", "Avg_Pace_Sec", "Top_Speed_Kmh"]].head())

# Extract fastest-lap speed trace for a specific driver
ver_trace = df_drivers.loc[df_drivers["Driver_Code"] == "VER", "Fastest_Lap_Trace"].iloc[0]
df_trace = pd.DataFrame({k: v for k, v in ver_trace.items() if k != "Samples"})
print(df_trace.head())
```

---

> [!IMPORTANT]
> **Data sources & credits:**
>
> * Session telemetry, lap timings, tyre stints, pit stops, and weather powered by [`OpenF1`](https://openf1.org/).
> * Season schedules, session UTC times, and official classifications powered by [`jolpica-f1`](https://github.com/jolpica/jolpica-f1).
> * Driver, constructor, and circuit SVG assets shared from [`mzafram2001/f1-fantasy`](https://github.com/mzafram2001/f1-fantasy).

> [!CAUTION]
> **Disclaimer:** Data is collected from public sources. This repository is for educational and research purposes only.

> [!TIP]
> **Want to help?**
> ⭐ Do you like this project? If you find this data useful, please give it a star! It helps me keep updating it.
>
> ☕ If this dataset saves you time in your analysis, you can also [buy me a coffee on Ko-fi](https://ko-fi.com/mzm0102).
<br>
<!-- LAST_CHECKED_START -->Last checked: 2026-11-20<!-- LAST_CHECKED_END -->
