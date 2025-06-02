import os
import time
import requests
import holidays
from influxdb_client import InfluxDBClient, Point, WritePrecision
from datetime import datetime, date, time as dtime, timedelta
import schedule
from dateutil import parser, tz


# Konfiguration der Bahnhöfe (EVA‐Nummern)

BAHNHOEFE = {
    "Bielefeld Hbf":    "8000036",
    "Altenbeken":       "8000004",
    "Lippstadt":        "8000571",
    "Herford":          "8000152",
    "Köln Hbf":         "8000207",
    "Düsseldorf Hbf":   "8000085",
    "Dortmund Hbf":     "8000080",   
    "Münster Hbf":      "8000263",
    "Bonn Hbf":         "8000044",
    "Bochum Hbf":       "8000041"
}

STADTNAME_MAPPING = {                # für Wetter-API
    "Bielefeld Hbf":   "Bielefeld",
    "Altenbeken":      "Altenbeken",
    "Lippstadt":       "Lippstadt",
    "Herford":         "Herford",
    "Köln Hbf":        "Cologne",
    "Düsseldorf Hbf":  "Dusseldorf",
    "Dortmund Hbf":    "Dortmund",
    "Münster Hbf":     "Muenster",
    "Bonn Hbf":        "Bonn",
    "Bochum Hbf":      "Bochum"
}


# Zeitraum für die Daten (lokal in Europe/Berlin, ~1 Woche 02.-10.06.2025)
WEEK_START_LOCAL = "2025-06-02T18:00:00+02:00" # ISO-Strings
WEEK_END_LOCAL   = "2025-06-10T18:00:00+02:00"

# Strings zu UTC-Objekte
berlin_tz = tz.gettz("Europe/Berlin")
start_local = parser.isoparse(WEEK_START_LOCAL)       # datetime mit tzinfo=Europe/Berlin
end_local   = parser.isoparse(WEEK_END_LOCAL)         # datetime mit tzinfo=Europe/Berlin
START_UTC   = start_local.astimezone(tz.UTC)
END_UTC     = end_local.astimezone(tz.UTC)


# Feiertags­prüfung: Deutsche Feiertage (NRW)
DE_HOLIDAYS = holidays.Germany(prov="NW")  # NW = Nordrhein-Westfalen

def is_holiday(dt: date) -> bool: # Prüfung ob Datum Feiertag
    return dt in DE_HOLIDAYS


# Verspätung berechnen (ISO‐Strings -> Minuten)
def parse_delay(scheduled_iso: str, actual_iso: str) -> float:
    try:
        s = parser.isoparse(scheduled_iso)
        a = parser.isoparse(actual_iso)
        return round((a - s).total_seconds() / 60.0, 2)
    except Exception:
        return None


## Sammelfunktion: Bahn‐ und Wetterdaten
def collect_data():
    # Umgebungs-Variablen einlesen
    influx_url    = os.getenv("INFLUXDB_URL")
    influx_token  = os.getenv("INFLUXDB_TOKEN")
    influx_org    = os.getenv("INFLUXDB_ORG")
    influx_bucket = os.getenv("INFLUXDB_BUCKET")

    client_id     = os.getenv("DB_CLIENT_ID")
    api_key       = os.getenv("DB_API_KEY")
    api_weather   = os.getenv("WEATHER_API_KEY")

    # InfluxDB-Client initialisieren
    client    = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    write_api = client.write_api()
    delete_api = client.delete_api()
    
    # Jetzt in UTC
    now_utc = datetime.utcnow().replace(tzinfo=tz.UTC)

    # Löschen aller Punkte, die NICHT im gewünschten Wochen-Zeitraum liegen (Vor START_UTC oder nach END_UTC)
    def _delete_out_of_range(measurement: str):
        start_str = "1970-01-01T00:00:00Z"
        stop_before = START_UTC.isoformat()  # alles vor Start
        stop_after  = END_UTC.isoformat()    # alles nach Ende

        # Delete vor Start
        delete_api.delete(bucket=influx_bucket, org=influx_org,
                          start=start_str, stop=stop_before,
                          predicate=f'(r["_measurement"] == "{measurement}")')
        # Delete nach Ende
        delete_api.delete(bucket=influx_bucket, org=influx_org,
                          start=stop_after, stop="9999-12-31T23:59:59Z",
                          predicate=f'(r["_measurement"] == "{measurement}")')

    try:
        _delete_out_of_range("departure")
        _delete_out_of_range("weather")
        print(f"[Info] Daten außerhalb {START_UTC}–{END_UTC} gelöscht.")
    except Exception as e:
        print(f"[Warning] Löschen außerhalb Bereich schlug fehl: {e}")
    
    # Nur weitermachen, wenn jetzt im Zeitraum liegt
    if not (START_UTC <= now_utc <= END_UTC):
        print(f"[Info] Aktuelle Zeit {now_utc} liegt nicht im Bereich; speichere nichts.")
        return
    

    # Für jeden Bahnhof alle Abfahrten (Timetables & RIS) holen
    for station_name, eva_id in BAHNHOEFE.items():

        # Geplante Abfahrten (Timetables API)
        tt_url = (
            f"https://api.deutschebahn.com/timetables/v1/arrivalBoard/"
            f"{eva_id}?direction=&date={now_utc.isoformat()}"
        )
        headers_db = {
            "DB-Client-Id": client_id,
            "DB-Api-Key": api_key,
        }
        try:
            r_tt = requests.get(tt_url, headers=headers_db, timeout=15)
            r_tt.raise_for_status()     # .json zu Python parsen -> departures_tt wie eine Liste/Dict
            departures_tt = r_tt.json()
        except Exception as e:
            print(f"[Error] Timetables für {station_name}: {e}")
            departures_tt = []

        # Reale Abfahrten (RIS::Stations API)
        ris_url = (
            f"https://api.deutschebahn.com/ris/v1/arrivalBoard/"
            f"{eva_id}?date={now_utc.isoformat()}"
        )
        try:
            r_ris = requests.get(ris_url, headers=headers_db, timeout=15)
            r_ris.raise_for_status()
            departures_ris = r_ris.json()
        except Exception as e:
            print(f"[Error] RIS für {station_name}: {e}")
            departures_ris = []

        # Alle Züge verarbeiten (ohne Begrenzung)
        # Nur nach Index, weil sowohl Timetables als auch RIS in Listen nach Abfahrtszeit sortiert zurückkommen
        # Wenn RIS-Länge < TT-Länge, bleiben "real" und "cancelled" leer/default
        for i, dep_tt in enumerate(departures_tt):
            scheduled_time = dep_tt.get("scheduledDateTime") or ""
            train_name     = dep_tt.get("name") or dep_tt.get("train") or ""
            train_type     = dep_tt.get("type") or ""       # z.B. "ICE", "RE", "RB"
            operator       = dep_tt.get("operator") or ""   # z.B. "DB"
            direction      = dep_tt.get("direction") or ""
            platform       = dep_tt.get("platform") or ""

            # Standard-Fallbacks für RIS: Default-Werte, falls keine RIS-Daten
            actual_time = ""
            cancelled   = False
            delay_min   = None

            if i < len(departures_ris):
                ris_e = departures_ris[i]
                actual_time = ris_e.get("realDateTime") or ""
                cancelled   = bool(ris_e.get("cancelled", False))
                # Wenn wir eine realDateTime haben, berechnen wir die Verspätung:
                if scheduled_time and actual_time:
                    delay_min = parse_delay(scheduled_time, actual_time)

            # Falls direkt ein "delay"-Feld da ist (Timetables liefert es manchmal): override, falls parse_delay nichts ergibt
            if delay_min is None and dep_tt.get("delay") is not None:
                # z.B. dep_tt["delay"] liefert int Minuten
                try:
                    delay_min = float(dep_tt.get("delay"))
                except Exception:
                    pass

            # Wenn beides fehlt, setzen wir -1 als Platzhalter
            if delay_min is None:
                delay_min = -1.0
                
            # Date und Time als Felder; date_time = ISO-String der aktuellen UCT-Zeit, z.B. "2025-06-02T17:45:00Z"
            date_time  = now_utc.isoformat()
            weekday_num = now_local_berlin.weekday()  # Wochentag (0=Montag … 6=Sonntag)

            #  Punkt bauen und in InfluxDB schreiben (Measurement "departure")
            point = (
                Point("departure")
                .tag("station",       station_name)
                .tag("train_name",    train_name)
                .tag("train_type",    train_type)
                .tag("operator",      operator)
                .tag("direction",     direction)
                .field("scheduled_time", scheduled_time)
                .field("actual_time",     actual_time)
                .field("platform",        platform)
                .field("cancelled",       cancelled)
                .field("delay_minutes",   delay_min)
                .field("weekday",      weekday_num)
                .time(now_utc, WritePrecision.NS)
            )
            try:
                write_api.write(bucket=influx_bucket, org=influx_org, record=point)
            except Exception as e:
                print(f"[Error] InfluxDB (departure) für {station_name}: {e}")

        # Wetterdaten pro Station
        city = STADTNAME_MAPPING.get(station_name, station_name)
        weather_url = (
            f"http://api.weatherapi.com/v1/current.json?key={api_weather}"
            f"&q={city}&aqi=no"
        )
        try:
            r_w = requests.get(weather_url, timeout=15)
            r_w.raise_for_status()
            current = r_w.json().get("current", {})

            temp_c   = current.get("temp_c", 0.0)
            humidity = current.get("humidity", 0)
            wind_kph = current.get("wind_kph", 0.0)
            cond_txt = current.get("condition", {}).get("text", "")

            point_w = (
                Point("weather")
                .tag("station",       station_name)
                .field("temperature_c", temp_c)
                .field("humidity_pct",  humidity)
                .field("wind_kph",      wind_kph)
                .field("weekday",      weekday_num)
                .time(now_utc, WritePrecision.NS)
            )
            write_api.write(bucket=influx_bucket, org=influx_org, record=point_w)

        except Exception as e:
            print(f"[Error] WeatherAPI für {station_name}: {e}")


# Scheduler: alle 10 Minuten (für 7 Tage)

if __name__ == "__main__":
    collect_data()   # Einmal beim Start

    schedule.every(10).minutes.do(collect_data)  # Alle 10 Minuten

    while True:                    
        schedule.run_pending()
        time.sleep(1)

