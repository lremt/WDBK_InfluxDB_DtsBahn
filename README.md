# Projekt: Bahn- und Wetterdaten-Collector mit InfluxDB

Dieses Repository enthält ein vollständiges Docker-Setup, das für die Dauer einer Woche (06/08/2025 18:00 – 06/15/2025 18:00) automatisiert Fahrplandaten (Timetables + RIS :: Stations) und Wetterdaten (WeatherAPI) für zehn wichtige NRW-Bahnhöfe sammelt und in eine InfluxDB schreibt. Die Daten liegen persistent in einem Host-Verzeichnis, sodass Container oder Images gelöscht werden können, ohne die gespeicherten Zeitreihen zu verlieren. Anschließend können beliebige Clients (z. B. Jupyter Notebooks) per Flux-Query oder InfluxDB-Client auf die Daten zugreifen und Analysen durchführen. Folgend werden die dafür verwendete Ordnerstruktur und die Skripte kurz erläutert.

## Projektziel:

Wichtige Bahnmhöfe NRW sowie naheliegende um Paderbon - 10 in der Gesamtzahl - werden unter die Lupe genommen. Es sollen Verspätungen dargestellt und Wahrscheinlichkeiten präsentiert werden, ob ein Zug je nach Tag und Uhzeit verspätet ist oder gar nicht fährt und ob sich dahingehend Korrelationen zum Wetter aufdecken lassen.

## Verzeichnisse und Ihre Bedeutung:

- **docker-compose.yml**

  - Definiert zwei Services:
    1. **influxdb** – InfluxDB 2.x mit persistentem Host-Verzeichnis (`/mnt/influx_data`).
    2. **data_collector** – Python-Container, der alle 10 Minuten Fahrplandaten (Timetables & RIS) und Wetterdaten abruft und in InfluxDB schreibt.

- **collector/Dockerfile**

  - Legt das Python-Image fest und installiert alle benötigten Bibliotheken:
    - `requests`
    - `influxdb-client`
    - `schedule`
    - `python-dateutil`
    - `holidays`

- **collector/collect_data.py**
  - Enthält die Logik, um:
    1. den **festen Wochen-Zeitraum** (z. B. „2025-06-02T18:00 bis 2025-06-09T18:00“) zu löschen bzw. nur Daten aus diesem Bereich zu speichern.
    2. alle **10 Minuten** Fahrplandaten für 10 EVA-Bahnhöfe in NRW (inkl. Verzögerungen, Zugkategorie etc.) abzurufen.
    3. Wetterdaten über die WeatherAPI (Stadtname oder Geo-Koordinaten) zu holen.
    4. sämtliche Punkte in InfluxDB (Measurements `departure` und `weather`) zu schreiben und vor/nach dem Zeitraum automatisch zu löschen.

## Konfiguration

1. **Klonen**

   ```bash
   cd ~
   git clone https://github.com/lremt/WDBK_InfluxDB_DtsBahn
   ```

   Python-Umgebung einrichten
