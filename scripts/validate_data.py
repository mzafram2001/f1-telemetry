import argparse
import json
import sys
from pathlib import Path

DATA_DIR = Path("data")
REQUIRED_META_KEYS = {
    "Id",
    "Season",
    "Race_Id",
    "Event_Name",
    "Session_Type",
    "Is_Sprint",
    "OpenF1_Session_Key",
    "Circuit",
    "Start_Times",
    "Generated_At",
}
BASE_TRACE_KEYS = ("Speed_Kmh", "Throttle_Pct", "Brake_Pct", "Gear", "Rpm")
EXTENDED_TRACE_KEYS = ("Distance_Pct", "Drs")
OPTIONAL_TRACE_KEYS = ("X", "Y")


def validate_all(season=None):
    if not DATA_DIR.exists():
        print("❌ No existe el directorio 'data/'.")
        sys.exit(1)

    pattern = f"{season}/*/*.json" if season else "*/*/*.json"
    files = sorted(DATA_DIR.glob(pattern))
    if not files:
        print(f"⚠️ No hay archivos JSON en 'data/{pattern}'.")
        return

    errors = []
    warnings = []

    for path in files:
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            errors.append(f"{path}: JSON inválido ({e})")
            continue

        meta = payload.get("Meta")
        if not isinstance(meta, dict):
            errors.append(f"{path}: falta objeto 'Meta'")
            continue

        missing_meta = REQUIRED_META_KEYS - meta.keys()
        if missing_meta:
            errors.append(f"{path}: faltan claves en Meta {sorted(missing_meta)}")

        drivers = payload.get("Drivers", [])
        teams = payload.get("Teams", [])
        if not drivers or not teams:
            errors.append(f"{path}: listas 'Drivers' o 'Teams' vacías")

        for d in drivers:
            code = d.get("Driver_Code")
            t_code = d.get("Team_Code")
            if not code or code == "N/A":
                errors.append(f"{path}: piloto sin Driver_Code ({d.get('Driver_Name')})")
            if not t_code or t_code == "N/A":
                errors.append(f"{path}: piloto {code} con Team_Code inválido ({d.get('Team_Name')})")

            lap_times = d.get("Lap_Times")
            if lap_times is not None and not isinstance(lap_times, list):
                errors.append(f"{path} [{code}]: 'Lap_Times' debe ser una lista")

            trace = d.get("Fastest_Lap_Trace")
            if trace is not None:
                n = trace.get("Samples", 0)
                for tk in BASE_TRACE_KEYS:
                    if len(trace.get(tk, [])) != n:
                        errors.append(f"{path} [{code}]: longitud inconsistente en {tk}")
                for etk in EXTENDED_TRACE_KEYS:
                    if etk not in trace:
                        if season:
                            errors.append(f"{path} [{code}]: falta {etk} en traza")
                        else:
                            warnings.append(f"{path} [{code}]: pendiente de migrar ({etk})")
                    elif len(trace.get(etk, [])) != n:
                        errors.append(f"{path} [{code}]: longitud inconsistente en {etk}")
                for otk in OPTIONAL_TRACE_KEYS:
                    arr = trace.get(otk)
                    if arr is not None and len(arr) != n:
                        errors.append(f"{path} [{code}]: longitud inconsistente en {otk}")
            elif d.get("Fastest_Lap_Sec") is not None:
                warnings.append(f"{path} [{code}]: tiene vuelta rápida pero sin Fastest_Lap_Trace")

    if warnings:
        print(f"⚠️ Avisos ({len(warnings)}):")
        for w in warnings[:20]:
            print(f"  - {w}")
        if len(warnings) > 20:
            print(f"  ... y {len(warnings) - 20} más.")

    if errors:
        print(f"\n❌ Validación fallida con {len(errors)} errores:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print(f"✅ Validación superada: {len(files)} sesiones verificadas correctamente.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=None)
    args = parser.parse_args()
    validate_all(season=args.season)
