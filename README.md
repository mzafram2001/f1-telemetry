# 📡 f1-telemetry

![Status](https://img.shields.io/badge/Status-Available-green)
![License](https://img.shields.io/badge/License-MIT-blue)
![Format](https://img.shields.io/badge/Format-JSON-orange)
![Stack](https://img.shields.io/badge/Stack-Python-yellow)
![Last updated](https://img.shields.io/github/last-commit/mzafram2001/f1-telemetry?label=Last%20update)
[![Update F1 Telemetry data](https://github.com/mzafram2001/f1-telemetry/actions/workflows/get_round_data.yml/badge.svg)](https://github.com/mzafram2001/f1-telemetry/actions/workflows/get_round_data.yml)

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
| `Lap_Times` | `Array` | Lap-by-lap durations in seconds (`1..N`, `null` if unrecorded) | `[108.432, 102.11, ...]` |
| `Stints` | `Array` | Tyre compounds, lap ranges, and tyre age at start of each stint | `[{"Compound": "SOFT", ...}]` |
| `Fastest_Lap_Trace` | `Object \| null` | `100` synchronized points (`Distance_Pct`, `Speed_Kmh`, `Throttle_Pct`, `Brake_Pct`, `Gear`, `Rpm`, `Drs`, `X`, `Y`) | `{"Samples": 100, ...}` |


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
<details open>
<summary><b>🏎️ 2026 Season — Round 21 · Las Vegas Grand Prix (Race)</b> <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/circuits/las-vegas.svg' width='28' height='20' align='center'></summary>

> 🌡️ Air 11.9°C · 🛣️ Track 14.0°C · 💧 32% · ☀️ Dry

#### 👤 Race Telemetry — Drivers

| Pos | Driver | Fastest lap | Avg pace | Top speed | Stops | Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| P1 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/ver-red-2026.svg' width='44' height='32' align='center'> | 1:36.450 | 1:38.043 | 327 km/h | 1 | S → M |
| P2 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/nor-mcl-2026.svg' width='44' height='32' align='center'> | 1:36.182 | 1:38.127 | 342 km/h | 1 | S → M |
| P3 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/pia-mcl-2026.svg' width='44' height='32' align='center'> | 1:36.334 | 1:37.827 | 349 km/h | 1 | S → M |
| P4 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/ant-mer-2026.svg' width='44' height='32' align='center'> | 1:36.235 | 1:37.893 | 348 km/h | 1 | M → S |
| P5 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/gas-alp-2026.svg' width='44' height='32' align='center'> | 1:36.057 | 1:37.985 | 342 km/h | 1 | S → M |
| P6 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/col-alp-2026.svg' width='44' height='32' align='center'> | 1:36.207 | 1:38.159 | 352 km/h | 1 | S → M |
| P7 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/ham-fer-2026.svg' width='44' height='32' align='center'> | 1:36.452 | 1:37.910 | 335 km/h | 1 | S → M |
| P8 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/sai-wil-2026.svg' width='44' height='32' align='center'> | 1:36.716 | 1:38.274 | 351 km/h | 1 | S → M |
| P9 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/rus-mer-2026.svg' width='44' height='32' align='center'> | 1:36.331 | 1:38.107 | 350 km/h | 1 | S → M |
| P10 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/lec-fer-2026.svg' width='44' height='32' align='center'> | 1:36.601 | 1:38.071 | 331 km/h | 1 | S → M |
| P11 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/hul-aud-2026.svg' width='44' height='32' align='center'> | 1:37.038 | 1:38.602 | 329 km/h | 1 | S → M |
| P12 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/bor-aud-2026.svg' width='44' height='32' align='center'> | 1:37.087 | 1:38.632 | 340 km/h | 1 | S → M |
| P13 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/bea-haa-2026.svg' width='44' height='32' align='center'> | 1:37.312 | 1:38.805 | 335 km/h | 1 | M → S |
| P14 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/law-vrb-2026.svg' width='44' height='32' align='center'> | 1:35.591 | 1:38.316 | 349 km/h | 1 | M → S |
| P15 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/had-red-2026.svg' width='44' height='32' align='center'> | 1:35.944 | 1:37.798 | 337 km/h | 2 | S → M → S |
| P16 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/cam-haa-2026.svg' width='44' height='32' align='center'> | 1:37.900 | 1:39.272 | 330 km/h | 2 | S → M → S |
| P17 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/str-ast-2026.svg' width='44' height='32' align='center'> | 1:38.805 | 1:40.333 | 341 km/h | 1 | S → M |
| P18 | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/bot-cad-2026.svg' width='44' height='32' align='center'> | 1:38.443 | 1:40.347 | 349 km/h | 2 | S → M → S |
| P19 *(DNF)* | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/alo-ast-2026.svg' width='44' height='32' align='center'> | 1:38.560 | 1:39.958 | 329 km/h | 1 | S → M |
| P20 *(DNF)* | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/lin-vrb-2026.svg' width='44' height='32' align='center'> | 1:37.883 | 1:39.119 | 339 km/h | 1 | S → M |
| P21 *(DNF)* | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/per-cad-2026.svg' width='44' height='32' align='center'> | 1:39.296 | 1:39.991 | 346 km/h | 0 | S |
| P22 *(DNS)* | <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/drivers/alb-wil-2026.svg' width='44' height='32' align='center'> | — | — | — | 0 | — |

#### 🏢 Race Telemetry — Constructors

| Team | Avg pace | Top speed | Avg pit lane | Laps |
| :--- | :--- | :--- | :--- | :--- |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/mcl-2026.svg' width='44' height='32' align='center'> | 1:37.977 | 349 km/h | 26.14s | 100 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/red-2026.svg' width='44' height='32' align='center'> | 1:37.921 | 337 km/h | 24.73s | 100 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/alp-2026.svg' width='44' height='32' align='center'> | 1:38.072 | 352 km/h | 22.99s | 100 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/mer-2026.svg' width='44' height='32' align='center'> | 1:38.000 | 350 km/h | 24.19s | 100 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/fer-2026.svg' width='44' height='32' align='center'> | 1:37.990 | 335 km/h | 27.32s | 100 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/wil-2026.svg' width='44' height='32' align='center'> | 1:38.274 | 351 km/h | 22.16s | 50 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/aud-2026.svg' width='44' height='32' align='center'> | 1:38.617 | 340 km/h | 21.84s | 100 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/haa-2026.svg' width='44' height='32' align='center'> | 1:39.038 | 335 km/h | 24.02s | 100 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/vrb-2026.svg' width='44' height='32' align='center'> | 1:38.718 | 349 km/h | 23.05s | 69 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/ast-2026.svg' width='44' height='32' align='center'> | 1:40.145 | 341 km/h | 21.90s | 99 |
| <img src='https://raw.githubusercontent.com/mzafram2001/f1-fantasy/main/src/constructors/cad-2026.svg' width='44' height='32' align='center'> | 1:40.169 | 349 km/h | 21.91s | 67 |

</details>

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
<!-- LAST_CHECKED_START -->Last checked: 2026-11-24<!-- LAST_CHECKED_END -->
