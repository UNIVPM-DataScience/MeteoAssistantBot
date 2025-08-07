from typing import Any, Text, Dict, List, Optional
import os
import logging
import requests
from datetime import datetime, timezone, timedelta
from rasa_sdk import Action, Tracker, FormValidationAction
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict
from dotenv import load_dotenv
from rasa_sdk.events import SlotSet
from collections import Counter
from typing import List
import pandas as pd
from requests.exceptions import RequestException, Timeout,HTTPError

#url dataset https://www.kaggle.com/datasets/faizadani/european-tour-destinations-dataset?resource=download
load_dotenv()
API_KEY = os.getenv("OPENWEATHER_API_KEY") or os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("Missing OPENWEATHER_API_KEY environment variable")

logger = logging.getLogger(__name__)

_DAYS_IT = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
_WEEKDAY_LOOKUP = {d.lower(): i for i, d in enumerate(_DAYS_IT)}

ATTRACTIONS_DF = pd.read_csv(
    os.path.join(os.path.dirname(__file__), "data", "attractions_europe_ita.csv"),
    usecols=[
        "Destinazione",       # nome città
        "Regione",            # Regione
        "Paese",              # Nazione
        "Turisti Annui Stimati",  # Popolazione turistica
        "Latitudine",         # lat
        "Longitudine"         # lon
    ],
    dtype={
        "Destinazione": str,
        "Regione": str,
        "Paese": str,
        "Turisti Annui Stimati": str,
        "Latitudine": float,
        "Longitudine": float
    }
)

ATTRACTIONS_DF = ATTRACTIONS_DF.rename(columns={
    "Destinazione": "city",
    "Regione": "region",
    "Paese": "country",
    "Turisti Annui Stimati": "annual_tourists",
    "Latitudine": "lat",
    "Longitudine": "lng"
})

ATTRACTIONS_DF["city_key"] = ATTRACTIONS_DF["city"].str.lower()

class OpenWeatherClient:
    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.params = {"appid": api_key, "units": "metric", "lang": "it"}

    def _get(self, endpoint: str, **params) -> Optional[Dict]:
        try:
            r = self.session.get(f"{self.BASE_URL}/{endpoint}", params=params, timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logger.error("OpenWeather API error [%s]: %s", endpoint, e)
            return None

    def get_current(self, city: str) -> Optional[Dict]:
        return self._get("weather", q=city)

    def get_forecast(self, city: str) -> Optional[Dict]:
        return self._get("forecast", q=city)

    def get_air_pollution(self, lat: float, lon: float) -> Optional[Dict]:
        return self._get("air_pollution", lat=lat, lon=lon)

class ActionGetWeather(Action):

    def __init__(self) -> None:
        self.client = OpenWeatherClient(API_KEY)

    def name(self) -> Text:
        return "action_get_weather"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        city      = tracker.get_slot("city")

        match = ATTRACTIONS_DF[ATTRACTIONS_DF["city_key"] == city.lower()]
        if not match.empty:
            info       = match.iloc[0]
            region     = info["region"]
            country    = info["country"]

            intro = (
                f"{city}({region}, {country})"
            )
        else:
            intro = f"{city}:"

        date_slot = tracker.get_slot("date") or "oggi"
        if not city:
            dispatcher.utter_message(text="Per favore, indicami una città.")
            return []
        slot_l = date_slot.lower()

        if slot_l in ["oggi","ora","adesso"]:
            data = self.client.get_current(city)
            if not data:
                dispatcher.utter_message(text="Servizio meteo non disponibile.")
                return []
            return self._handle_current(dispatcher, city, data, intro)
        else:
            data = self.client.get_forecast(city)
            if not data or not data.get("list"):
                dispatcher.utter_message(text="Non sono disponibili previsioni per quella data.")
                return []
            return self._handle_forecast(dispatcher, city, date_slot, data, intro)

    def _handle_current(self, dispatcher: CollectingDispatcher, city: str, data: Dict, intro) -> List[Dict[Text, Any]]:
        if str(data.get("cod")) == "404":
            dispatcher.utter_message(text=f"Città '{city}' non trovata.")
            return []
        if str(data.get("cod")) != "200":
            dispatcher.utter_message(text=f"Errore meteo: {data.get('message','Errore')}")
            return []

        main     = data.get("main", {})
        desc   = data['weather'][0].get('description', '')
        wind     = data.get("wind", {})
        visibility_km = round(data.get("visibility", 0) / 1000)

        temp        = round(main.get("temp", 0))
        feels_like  = round(main.get("feels_like", 0))
        humidity    = main.get("humidity", "N/D")
        pressure    = main.get("pressure", "N/D")
        wind_speed  = round(wind.get("speed", 0), 1)

        header = intro

        message = (
            f"Oggi a {header}, {desc} {self.emoji(desc)},la temperatura è di {temp} °C "
            f"(percepiti {feels_like} °C). "
            f"L’umidità è al {humidity}%, la pressione a {pressure} hPa e "
            f"il vento soffia leggermente a {wind_speed} m/s. "
            f"Si gode di ottima visibilità (circa {visibility_km} km) e copertura nuvolosa pari al {data.get('clouds',{}).get('all','N/D')}%. \n"
        )

        dispatcher.utter_message(text=message)
        return []

    def _handle_forecast(
        self,
        dispatcher: CollectingDispatcher,
        city: str,
        slot: str,
        data: Dict[Text, Any],
        intro
    ) -> List[Dict[Text, Any]]:
        today = datetime.now().date()
        slot_l = slot.lower()
        tz     = data["city"].get("timezone", 0)

        if slot_l in _WEEKDAY_LOOKUP:
            wd_today  = today.weekday()
            wd_target = _WEEKDAY_LOOKUP[slot_l]
            delta_days = (wd_target - wd_today + 7) % 7 or 7
            target = today + timedelta(days=delta_days)
        else:
            offset_map = {"domani": 1, "dopodomani": 2}
            target     = today + timedelta(days=offset_map.get(slot_l, 0))
            
        entries = []
        for e in data.get("list", []):
            dt_utc   = datetime.fromtimestamp(e["dt"], timezone.utc)
            dt_local = dt_utc + timedelta(seconds=tz)
            if dt_local.date() == target:
                entries.append((dt_local, e))
        entries.sort(key=lambda x: x[0])

        if not entries:
            dispatcher.utter_message(text=f"Non ho trovato previsioni per {slot.capitalize()}.")
            return []

        morning   = [e for e in entries if e[0].hour < 12]
        afternoon = [e for e in entries if 12 <= e[0].hour < 18]
        evening   = [e for e in entries if e[0].hour >= 18]

        def summarize(group):
            dt_local, entry = group[0]
            w      = entry.get("weather", [{}])[0]
            desc   = w.get("description", "N/D")
            raw = entry.get("main", {}).get("temp")
            temp = f"{raw:.0f}" if isinstance(raw, (int, float)) else "N/D"
            hum    = entry.get("main", {}).get("humidity", "N/D")
            wind_v = entry.get("wind", {}).get("speed", "N/D")
            emoji  = self.emoji(desc)
            return desc, temp, hum, wind_v, emoji

        day_name       = _DAYS_IT[target.weekday()]
        formatted_date = target.strftime("%d/%m/%Y")
        parts = [f"{day_name} {formatted_date} - {intro} \n "]

        
        if morning:
            desc, temp, hum, wind_v, emoji = summarize(morning)
            parts.append(
                f"In mattinata avremo {desc} {emoji}, con temperature attorno ai {temp}°C, "
                f"umidità al {hum}% e vento debole a {wind_v} m/s."
            )
        if afternoon:
            desc, temp, hum, wind_v, emoji = summarize(afternoon)
            parts.append(
                f" Durante il pomeriggio il cielo tenderà a essere {desc} {emoji}, "
                f"con punte di {temp}°C, umidità al {hum}% e brezze a {wind_v} m/s."
            )
        if evening:
            desc, temp, hum, wind_v, emoji = summarize(evening)
            parts.append(
                f" In serata ci aspettiamo {desc} {emoji}, temperature in calo verso i {temp}°C, "
                f"umidità al {hum}% e vento a {wind_v} m/s. \n"
                
            )

        message = "".join(parts)
        dispatcher.utter_message(text=message)
        return []


    def _format_time(self, ts: Any, tz_offset: int) -> Text:
        return 'N/D' if not ts else datetime.fromtimestamp(ts+tz_offset,timezone.utc).strftime('%H:%M')

    @staticmethod
    def emoji(description: str) -> Text:
        d=description.lower()
        if 'sole' in d or 'sereno' in d: return '☀️'
        if 'nuvol' in d: return '☁️'
        if 'pioggia' in d or 'rain' in d: return '🌧️'
        if 'neve' in d: return '❄️'
        if 'temporale' in d or 'thunder' in d: return '⛈️'
        return '🌥️'


class ValidateWeatherForm(FormValidationAction):

    def name(self) -> Text:
        return "validate_weather_form"

    async def validate_city(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:


        try:
            resp = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": slot_value,
                    "appid": API_KEY,
                    "units": "metric",
                    "lang": "it"
                },
                timeout=5
            )
        except (RequestException, Timeout):
            # Problema di rete o timeout
            dispatcher.utter_message(response="utter_weather_unavailable")
            return {"city": None}

        # Gestione dei codici HTTP
        if resp.status_code == 200:
            # Città trovata con successo
            return {"city": slot_value}

        if resp.status_code == 404:
            # Città non esistente
            dispatcher.utter_message(response="utter_invalid_city", city=slot_value)
            return {"city": None}

        dispatcher.utter_message(response="utter_weather_unavailable")
        return {"city": None}



class ActionClothingAdvice(Action):

    def __init__(self) -> None:
        self.client = OpenWeatherClient(API_KEY)


    def name(self) -> Text:
        return "action_clothing_advice"

    def run(
        self, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:

        city      = tracker.get_slot("city")
        date_slot = (tracker.get_slot("date") or "oggi").lower()

        if not city:
            dispatcher.utter_message(text="Per favore, dimmi per quale città.")
            return []

        fdata = self.client.get_forecast(city)
        if not fdata or not fdata.get("list"):
            dispatcher.utter_message(text="Servizio meteo non disponibile.")
            return []

        today = datetime.now().date()
        if date_slot in _WEEKDAY_LOOKUP:
            wd_today  = today.weekday()
            wd_target = _WEEKDAY_LOOKUP[date_slot]
            delta     = (wd_target - wd_today + 7) % 7 or 7
            target    = today + timedelta(days=delta)
        else:
            offset_map = {"oggi":0, "ora":0, "adesso":0, "domani":1, "dopodomani":2}
            target     = today + timedelta(days=offset_map.get(date_slot, 0))

        tz = fdata["city"].get("timezone", 0)
        entries = [
            e for e in fdata["list"]
            if (datetime.fromtimestamp(e["dt"], timezone.utc)
                + timedelta(seconds=tz)).date() == target
        ]
        if not entries:
            dispatcher.utter_message(text=f"Non ho previsioni utili per «{date_slot}».")
            return []

        fasce = {
            "Mattino":    (6, 12),
            "Pomeriggio": (12, 18),
            "Sera":       (18, 24),
        }
        
        segmenti: Dict[str, Dict[str, Any]] = {}
        for nome, (h1, h2) in fasce.items():
            seg = [
                e for e in entries
                if h1 <= (datetime.fromtimestamp(e["dt"], timezone.utc)
                          + timedelta(seconds=tz)).hour < h2
            ]
            if not seg:
                continue
            temps = [e["main"]["temp"] for e in seg]
            winds = [e["wind"]["speed"] for e in seg]
            descs = [w["description"] for e in seg for w in e.get("weather", [])]

            avg_t = sum(temps) / len(temps)
            avg_w = sum(winds) / len(winds)
            main_desc = Counter(descs).most_common(1)[0][0]

            segmenti[nome] = {"temp": avg_t, "vento": avg_w, "desc": main_desc}

        paragrafi: List[str] = []
        for periodo, s in segmenti.items():
            paragrafi.append(
                self._narrative_paragraph(
                    periodo=periodo,
                    desc=s["desc"],
                    temp=s["temp"],
                    vento=s["vento"]
                )
            )

        testo = f"Per la giornata di {date_slot} a {city}:\n\n" + "\n\n".join(paragrafi)
        dispatcher.utter_message(text=testo)
        return []
        
    def _narrative_paragraph(
        self,
        periodo: str,
        desc: str,
        temp: float,
        vento: float
    ) -> str:
        # --- 1. Descrizione del vento
        if vento > 8:
            vento_str = f"vento sostenuto a {vento:.1f} m/s"
        elif vento > 4:
            vento_str = f"brezza leggera a {vento:.1f} m/s"
        else:
            vento_str = "aria calma"

        # --- 2. Intestazione in base al periodo
        period_map = {
            "Mattino": "Al mattino",
            "Pomeriggio": "A metà pomeriggio",
            "Sera": "Verso sera"
        }
        intro = period_map.get(periodo, "Durante la giornata")

        # --- 3. Outfit di base per fascia termica e periodo
        # Struttura: {periodo: [(max_temp, testo), ...]}
        outfit_rules = {
            "Mattino": [
                (10,   "indossa un cappotto caldo, un maglione in lana e pantaloni lunghi; non dimenticare guanti e sciarpa"),
                (15,   "scegli un cardigan o una giacca in pile con pantaloni lunghi e scarpe chiuse"),
                (20,   "una maglia a maniche lunghe e pantaloni lunghi, accompagnati da sneakers, sono perfetti"),
                (float("inf"), "una t-shirt in cotone fresco e pantaloni corti, accompagnati da sneakers traspiranti; non dimenticare occhiali da sole e un cappellino")
            ],
            "Pomeriggio": [
                (10,   "indossa un piumino leggero o una giacca imbottita, pantaloni lunghi e scarpe chiuse"),
                (15,   "optare per un giubbotto in pile e pantaloni lunghi è ideale"),
                (20,   "una felpa leggera e pantaloni lunghi o jeans sono sufficienti; tieni a portata di mano una borraccia d’acqua"),
                (float("inf"), "optare per un top in lino o tessuto tecnico e shorts leggeri è ideale; tieni a portata di mano una borraccia d’acqua e cerca qualche momento d’ombra")
            ],
            "Sera": [
                (10,   "indossa un cappotto o un piumino leggero, maglione in lana e pantaloni lunghi"),
                (15,   "porta un coprispalle o una giacca in pile insieme a pantaloni lunghi"),
                (20,   "una camicia in lino o un maglioncino leggero con pantaloni lunghi va benissimo"),
                (float("inf"), "le temperature rimarranno miti ma porta con te un coprispalle leggero o una camicia in lino da indossare al tramonto")
            ],
        }

        # Se il periodo non è riconosciuto, usa una regola di fallback
        rules = outfit_rules.get(periodo, outfit_rules["Mattino"])
        outfit_text = ""
        for max_t, text in rules:
            if temp <= max_t:
                outfit_text = text
                break

        # Aggiustamenti extra per vento o caldo estremo
        extras = []
        if periodo == "Pomeriggio" and vento > 4 and temp > 15:
            extras.append("se senti un refolo, una bandana leggera può fare la differenza")
        if periodo == "Mattino" and temp >= 28:
            extras.append("porta con te una bottiglia d’acqua")
        if periodo == "Pomeriggio" and temp >= 30:
            extras.append("ricorda di fare pause all’ombra e mantenerti idratato")
        if periodo == "Sera" and temp >= 25:
            extras.append("non dimenticare di restare idratato con un po’ d’acqua")

        # --- 4. Gestione precipitazioni
        desc_lower = desc.lower()
        precip_umbrella = ["pioggia", "rovesci", "temporale", "acquazzone"]
        precip_boots    = ["neve", "grandine"]
        precip_suggs = []
        if any(k in desc_lower for k in precip_umbrella):
            precip_suggs.append("un ombrello e un impermeabile leggero")
        if any(k in desc_lower for k in precip_boots):
            precip_suggs.append("stivali o scarpe impermeabili")

        # --- 5. Composizione finale
        sentence = (
            f"{intro}, con {desc} e circa {temp:.0f}°C e {vento_str}, {outfit_text}"
        )
        if extras:
            sentence += "; " + "; ".join(extras)
        if precip_suggs:
            sentence += f". Non dimenticare di portare {' e '.join(precip_suggs)}."
        else:
            sentence += "."

        return sentence


class ActionActivityAdvice(Action):

    def __init__(self) -> None:
        self.client = OpenWeatherClient(API_KEY)

    def name(self) -> Text:
        return "action_activity_advice"

    def run(
        self, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:

        city     = tracker.get_slot("city")
        date_raw = tracker.get_slot("date") or "oggi"
        activity = (tracker.get_slot("activity")
                    or next(tracker.get_latest_entity_values("activity"), None))

        if not city:
            dispatcher.utter_message(text="❓ Per favore, indicami una città.")
            return []
        if not activity:
            dispatcher.utter_message(text="❓ Quale attività ti piacerebbe fare?")
            return []

        events: List[SlotSet] = [SlotSet("activity", activity)]

        data, label = self._fetch_weather(city, date_raw)
        if data is None:
            dispatcher.utter_message(text=f"😕 Scusami, non ho previsioni per “{date_raw}” a {city}.")
            return events
        
        msg = self._build_message(label, data, activity)
        dispatcher.utter_message(text=msg)
        return events

    def _fetch_weather(self, city: str, date_raw: str):
        slot = date_raw.lower()
        if slot in ["oggi", "adesso", "ora"]:
            current = self.client.get_current(city)
            if not current:
                return None, None
            return current, f"Oggi a {city}"
        forecast = self.client.get_forecast(city)
        if not forecast or not forecast.get("list"):
            return None, None

        today = datetime.now().date()
        if slot in _WEEKDAY_LOOKUP:
            target_wd = _WEEKDAY_LOOKUP[slot]
            delta = (target_wd - today.weekday() + 7) % 7 or 7
            target = today + timedelta(days=delta)
        else:
            mapping = {"domani": 1, "dopodomani": 2}
            target = today + timedelta(days=mapping.get(slot, 0))

        tz_offset = forecast["city"].get("timezone", 0)
        entry = next(
            (
                e for e in forecast["list"]
                if (datetime.fromtimestamp(e["dt"], timezone.utc)
                    + timedelta(seconds=tz_offset)).date() == target
            ),
            None
        )
        if not entry:
            return None, None

        simplified = {
            "main":    entry["main"],
            "wind":    entry["wind"],
            "weather": entry["weather"][0]
        }
        label = f"Previsioni per {date_raw} a {city}"
        return simplified, label

    def _build_message(self, label: str, data: Dict[str, Any], activity: str) -> str:
        temp       = data["main"].get("temp", 0.0)
        desc       = data["weather"].get("description", "").capitalize()
        rain       = "pioggia" in desc.lower() or "rain" in desc.lower()
        wind_speed = data["wind"].get("speed", 0.0)

        if rain:
            alternative = " leggere un libro 📖 o guardare un film 🍿"
            return (
                f"{label}: {desc.lower()} 🌧️, non è il massimo per {activity}. "
                f"Potresti considerare di{alternative}."
            )

        if 10 <= temp <= 25 and wind_speed < 5:
            return (
                f"{label}: ottime condizioni per {activity}! ✅ {desc.lower()}, "
                f"{temp:.1f}°C e vento lieve ({wind_speed:.1f} m/s). "
                "Divertiti"
            )

        reasons = []
        if temp < 10:
            reasons.append("fa piuttosto freddo 🥶")
        elif temp > 30:
            reasons.append("fa molto caldo ☀️")
        if wind_speed >= 5:
            reasons.append("c'è un bel po' di vento 🌬️")

        reason_text = " e ".join(reasons) if reasons else desc.lower()
        return (
            f"{label}: {reason_text}, non è l’ideale per {activity}. "
        )



class ActionGetAirQuality(Action):

    def name(self) -> Text:
        return "action_get_air_quality"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:

        city = tracker.get_slot("city")
        if not city:
            dispatcher.utter_message(text="Per favore, dimmi prima una città.")
            return []

        # 1) Get coords from current weather
        try:
            resp = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"q": city, "appid": API_KEY},
                timeout=5
            )
            resp.raise_for_status()
        except HTTPError:
            if resp.status_code == 404:
                dispatcher.utter_message(response="utter_invalid_city", city=city)
            else:
                dispatcher.utter_message(response="utter_weather_unavailable")
            return []
        except (RequestException, Timeout):
            dispatcher.utter_message(response="utter_weather_unavailable")
            return []

        coord = resp.json().get("coord", {})
        lat, lon = coord.get("lat"), coord.get("lon")
        if lat is None or lon is None:
            dispatcher.utter_message(text=f"Non sono riuscito a ottenere le coordinate per {city}.")
            return []

        # 2) Call air_pollution
        try:
            ap = requests.get(
                "https://api.openweathermap.org/data/2.5/air_pollution",
                params={"lat": lat, "lon": lon, "appid": API_KEY},
                timeout=5
            )
            ap.raise_for_status()
        except (HTTPError, RequestException, Timeout):
            dispatcher.utter_message(text="⚠️ Servizio qualità dell'aria non disponibile al momento.")
            return []

        data = ap.json().get("list", [])
        if not data:
            dispatcher.utter_message(text="Non ci sono dati di qualità dell'aria per questa località.")
            return []

        item = data[0]
        # Map AQI
        aqi_map = {1: "Buona", 2: "Moderata", 3: "Scadente", 4: "Povera", 5: "Molto povera"}
        aqi = item["main"].get("aqi")
        aqi_text = aqi_map.get(aqi, "N/D")

        # —— begin integration of your snippet ——
        # Qualitative thresholds (µg/m³)
        thresholds = {
            "pm2_5": [(25, "buono"), (50, "moderato"), (float("inf"), "scadente")],
            "pm10":  [(50, "buono"), (100, "moderato"), (float("inf"), "scadente")],
            "no2":   [(40, "buono"), (90, "moderato"), (float("inf"), "scadente")],
            "o3":    [(60, "buono"), (120, "moderato"), (float("inf"), "scadente")],
            "so2":   [(20, "buono"), (80, "moderato"), (float("inf"), "scadente")],
            "co":    [(10000, "buono"), (float("inf"), "moderato")],
            "nh3":   [(200, "buono"), (float("inf"), "moderato")],
        }

        # Descriptions of pollutants
        descriptions = {
            "co":    "Monossido di Carbonio – gas incolore/inodore prodotto da combustione incompleta",
            "no":    "Monossido di Azoto – emesso da traffico e riscaldamento",
            "no2":   "Diossido di Azoto – irritante per le vie respiratorie, da veicoli diesel",
            "o3":    "Ozono – ossidante secondario, può causare irritazioni",
            "so2":   "Diossido di Zolfo – da combustione di carbone e petrolio",
            "nh3":   "Ammoniaca – da attività agricole, contribuisce al particolato",
            "pm2_5":"Particolato fine – penetra in profondità nei polmoni",
            "pm10": "Particolato grosso – irrita le vie aeree"
        }

        def qualifica(pollutant, value):
            if value is None:
                return "N/D"
            for thr, label in thresholds.get(pollutant, []):
                if value <= thr:
                    return label
            return "N/D"

        comps = item["components"]
        lines = [f"Qualità dell'aria a {city}:"]
        lines.append(f"• AQI: {aqi_text}")
        for key in ["co","no","no2","o3","so2","nh3","pm2_5","pm10"]:
            val = comps.get(key)
            if isinstance(val, (int, float)):
                q = qualifica(key, val)
                lines.append(f"• {key.upper()}: {round(val,1)} µg/m³ ({q}) – {descriptions[key]}")
            else:
                lines.append(f"• {key.upper()}: N/D – {descriptions[key]}")

        message = "\n".join(lines)
        # —— end integration ——

        dispatcher.utter_message(text=message)
        return []

class ActionGetSunTimes(Action):

    def name(self) -> Text:
        return "action_get_sun_times"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:

        city = tracker.get_slot("city")
        if not city:
            dispatcher.utter_message(text="Per favore, indicami prima una città.")
            return []

        # Call the current weather endpoint to get sys.sunrise, sys.sunset, timezone
        try:
            resp = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": city,
                    "appid": API_KEY,
                    "lang": "it"
                },
                timeout=5,
            )
            resp.raise_for_status()
        except HTTPError:
            if resp.status_code == 404:
                dispatcher.utter_message(response="utter_invalid_city", city=city)
            else:
                dispatcher.utter_message(response="utter_weather_unavailable")
            return []
        except (RequestException, Timeout):
            dispatcher.utter_message(response="utter_weather_unavailable")
            return []

        data = resp.json()
        sys = data.get("sys", {})
        tz_offset = data.get("timezone", 0)  # offset in seconds from UTC

        sunrise_ts = sys.get("sunrise")
        sunset_ts  = sys.get("sunset")
        if sunrise_ts is None or sunset_ts is None:
            dispatcher.utter_message(text="Non sono riuscito a recuperare gli orari di alba e tramonto.")
            return []

        # Convert timestamps + offset to local datetime
        tz = timezone(timedelta(seconds=tz_offset))
        sunrise = datetime.fromtimestamp(sunrise_ts, tz).strftime("%H:%M")
        sunset  = datetime.fromtimestamp(sunset_ts,  tz).strftime("%H:%M")

        message = (
            f"A {city}, l'alba è avvenuta alle {sunrise} e il tramonto avverrà alle {sunset} (orario locale)."
        )
        dispatcher.utter_message(text=message)
        return []


class ActionGetAttractions(Action):

    def __init__(self) -> None:
        path = os.path.join(os.path.dirname(__file__), "data", "attractions_europe_ita.csv")
        df = pd.read_csv(path, dtype=str)

        # Rinomina colonne chiave
        df = df.rename(columns={
            "Destinazione": "city",
            "Regione": "region",
            "Paese": "country",
            "Categoria": "category",
            "Turisti Annui Stimati": "annual_tourists",
            "Valuta": "currency",
            "Religione Principale": "religion",
            "Piatti Tipici": "foods",
            "Lingua": "language",
            "Periodo Consigliato": "best_time",
            "Costo della Vita": "cost_of_living",
            "Sicurezza": "safety",
            "Significato Culturale": "cultural_significance",
            "Descrizione": "description"
        })
        cols = [
            "city", "region", "country", "category", "description",
            "annual_tourists", "currency", "religion", "foods", "language",
            "best_time", "cost_of_living", "safety", "cultural_significance"
        ]
        df = df[cols]
        df["city_key"] = df["city"].str.lower()
        self.df = df

    def name(self) -> Text:
        return "action_get_attractions"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:

        raw_city = tracker.get_slot("city") or ""
        key = raw_city.strip().lower()
        matches = self.df[self.df["city_key"] == key]

        if matches.empty:
            dispatcher.utter_message(
                text=f"Mi dispiace, non ho informazioni turistiche per {raw_city}."
            )
            return []

        info = matches.iloc[0]

        message = (
            f"{info['city']} è una {info['category'].lower()} della regione {info['region']} in {info['country']}. "
            f"È famosa per {info['description']} Ogni anno accoglie circa {info['annual_tourists']} turisti. "
            f"La moneta locale è {info['currency'].lower()} e si parla principalmente {info['language'].lower()}, con tradizioni legate al {info['religion'].lower()}. "
            f"Non perdere i piatti tipici come {info['foods'].lower()}. "
            f"Il momento migliore per visitarla è {info['best_time'].lower()}, il costo della vita è {info['cost_of_living'].lower()} "
            f"e la sicurezza viene descritta come {info['safety'].lower()}. "
            f"Spicca come {info['cultural_significance']} \n"
        )

        dispatcher.utter_message(text=message)

        return []
