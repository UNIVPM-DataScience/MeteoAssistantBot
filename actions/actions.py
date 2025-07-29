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

# Load environment variables
load_dotenv()
API_KEY = os.getenv("OPENWEATHER_API_KEY") or os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("Missing OPENWEATHER_API_KEY environment variable")

# Configure logging
logger = logging.getLogger(__name__)

# Italian weekdays mapping
_DAYS_IT = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
# Reverse lookup: name -> index
_WEEKDAY_LOOKUP = {d.lower(): i for i, d in enumerate(_DAYS_IT)}

class OpenWeatherClient:
    """Client for OpenWeather APIs."""
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
    """Provides current weather, air quality, UV, clothing/activity advice and hourly forecast."""

    def __init__(self) -> None:
        self.client = OpenWeatherClient(API_KEY)

    def name(self) -> Text:
        return "action_get_weather"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        city      = tracker.get_slot("city")
        date_slot = tracker.get_slot("date") or "oggi"
        if not city:
            dispatcher.utter_message(text="Per favore, indicami una città.")
            return []
        slot_l = date_slot.lower()
        # current weather
        if slot_l in ["oggi","ora","adesso"]:
            data = self.client.get_current(city)
            if not data:
                dispatcher.utter_message(text="Servizio meteo non disponibile.")
                return []
            return self._handle_current(dispatcher, city, data)
        else:
            data = self.client.get_forecast(city)
            if not data or not data.get("list"):
                dispatcher.utter_message(text="Non sono disponibili previsioni per quella data.")
                return []
            return self._handle_forecast(dispatcher, city, date_slot, data)

    def _handle_current(self, dispatcher: CollectingDispatcher, city: str, data: Dict) -> List[Dict[Text, Any]]:
        if str(data.get("cod")) == "404":
            dispatcher.utter_message(text=f"Città '{city}' non trovata.")
            return []
        if str(data.get("cod")) != "200":
            dispatcher.utter_message(text=f"Errore meteo: {data.get('message','Errore')}")
            return []
        main   = data.get("main",{})
        wind   = data.get("wind",{})
        sys    = data.get("sys",{})
        coord  = data.get("coord",{})
        desc   = data['weather'][0].get('description','')
        now    = datetime.now()
        day_nm = _DAYS_IT[now.weekday()]
        dt_str = now.strftime("%d/%m/%Y %H:%M")
        lines  = [f"🌦️ Meteo per {city} – {day_nm} {dt_str}"]
        lines += [
            f"• 🌡️ Temp: {main.get('temp','N/D')}°C (perc. {main.get('feels_like','N/D')}°C)",
            f"• ☔ Condizioni: {desc} {self.emoji(desc)}",
            f"• 💧 Umidità: {main.get('humidity','N/D')}%",
            f"• 🧭 Pressione: {main.get('pressure','N/D')} hPa",
            f"• 🌬️ Vento: {wind.get('speed','N/D')} m/s ({wind.get('deg','—')}°)",
            f"• 👁️ Visibilità: {round(data.get('visibility',0)/1000,1)} km",
            f"• ☁️ Nuvolosità: {data.get('clouds',{}).get('all','N/D')}%",
            f"• 🌅 Alba: {self._format_time(sys.get('sunrise'), data.get('timezone',0))}  |  🌇 Tramonto: {self._format_time(sys.get('sunset'), data.get('timezone',0))}"
        ]
        # air pollution
        lat=coord.get('lat'); lon=coord.get('lon')
        if lat and lon:
            air = self.client.get_air_pollution(lat, lon)
            if air and air.get('list'):
                aqi=air['list'][0]['main'].get('aqi'); comps=air['list'][0].get('components',{})
                aqi_map={1:'Buona',2:'Moderata',3:'Scadente',4:'Povera',5:'Molto povera'}
                lines.append(f"• 🌫️ Qualità aria (AQI): {aqi_map.get(aqi,'N/D')}")
                #lines.append(f"  - PM2.5: {comps.get('pm2_5','N/A')} µg/m³ | PM10: {comps.get('pm10','N/A')} µg/m³")


        dispatcher.utter_message(text="\n".join(lines))
        return []
    
    def _handle_forecast(
        self,
        dispatcher: CollectingDispatcher,
        city: str,
        slot: str,
        data: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        today = datetime.now().date()
        slot_l = slot.lower()
        tz     = data["city"].get("timezone", 0)

        # 1) Calcola la data target:
        if slot_l in _WEEKDAY_LOOKUP:
            wd_today  = today.weekday()
            wd_target = _WEEKDAY_LOOKUP[slot_l]
            # giorni da aggiungere fino al prossimo slot_l
            delta_days = (wd_target - wd_today + 7) % 7 or 7
            target = today + timedelta(days=delta_days)
        else:
            # domani / dopodomani
            offset_map = {"domani": 1, "dopodomani": 2}
            target     = today + timedelta(days=offset_map.get(slot_l, 0))

        # 2) Filtro delle entry
        daily_entries = []
        for e in data.get("list", []):
            dt_utc   = datetime.fromtimestamp(e["dt"], timezone.utc)
            dt_local = dt_utc + timedelta(seconds=tz)
            if dt_local.date() == target:
                daily_entries.append((dt_local, e))
        daily_entries.sort(key=lambda x: x[0])

        if not daily_entries:
            dispatcher.utter_message(text=f"Non ho trovato previsioni per {slot.capitalize()}.")
            return []

        # 3) Header con giorno della settimana e data target
        day_name       = _DAYS_IT[target.weekday()]
        formatted_date = target.strftime("%d/%m/%Y")
        header = f"⛅ Previsioni per - {day_name} {formatted_date} a {city}:"
        dispatcher.utter_message(text=header)

        # 4) Mini‑card per ogni orario
        for dt_local, entry in daily_entries:
            t     = dt_local.strftime("%H:%M")
            w     = entry.get("weather",[{}])[0]
            desc  = w.get("description","N/D")
            emoji = self.emoji(desc)
            main  = entry.get("main",{})
            wind  = entry.get("wind",{})
            clouds  = entry.get("clouds", {}).get("all", "N/D")
            lines = [
                f"{t} — {emoji} {desc}",
                f"Temperatura: {main.get('temp','N/D')}°C | Umidità: {main.get('humidity','N/D')}% | "
                f"Vento: {wind.get('speed','N/D')} m/s ({wind.get('deg','—')}°) | "
                f"Nuvolosità: {clouds}%"
            ]
            dispatcher.utter_message(text="\n".join(lines))

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
    def name(self) -> Text: return 'validate_weather_form'
    async def validate_city(self,slot_value:Any,dispatcher:CollectingDispatcher,tracker:Tracker,domain:DomainDict)->Dict[Text,Any]:
        resp=requests.get(f"http://api.openweathermap.org/geo/1.0/direct?q={slot_value}&limit=1&appid={API_KEY}",timeout=5)
        if resp.status_code!=200 or not resp.json(): dispatcher.utter_message(response='utter_invalid_city',city=slot_value); return {'city':None}
        return {'city':slot_value}

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
        date_slot = tracker.get_slot("date") or "oggi"

        if not city:
            dispatcher.utter_message(text="Per favore, dimmi per quale città.")
            return []

        slot_l = date_slot.lower()

        # Caso “oggi”: uso il meteo corrente
        if slot_l in ["oggi", "ora", "adesso"]:
            data = self.client.get_current(city)
            if not data:
                dispatcher.utter_message(text="Servizio meteo non disponibile.")
                return []
            avg_temp   = data["main"].get("temp", 0.0)
            avg_wind   = data["wind"].get("speed", 0.0)
            rec_desc   = data["weather"][0].get("description", "")
            label      = f"Oggi a {city}"
        else:
            # Previsioni per giorno futuro
            fdata = self.client.get_forecast(city)
            if not fdata or not fdata.get("list"):
                dispatcher.utter_message(text=f"Non ho previsioni per {date_slot}.")
                return []

            # Calcola data target
            today = datetime.now().date()
            if slot_l in _WEEKDAY_LOOKUP:
                wd_today  = today.weekday()
                wd_target = _WEEKDAY_LOOKUP[slot_l]
                delta     = (wd_target - wd_today + 7) % 7 or 7
                target    = today + timedelta(days=delta)
            else:
                offset_map = {"domani":1, "dopodomani":2}
                target     = today + timedelta(days=offset_map.get(slot_l, 0))

            tz = fdata["city"].get("timezone", 0)
            entries = [
                e for e in fdata["list"]
                if (datetime.fromtimestamp(e["dt"], timezone.utc) + timedelta(seconds=tz)).date() == target
            ]
            if not entries:
                dispatcher.utter_message(text=f"Non ho previsioni utili per {date_slot}.")
                return []

            # Calcola medie
            temps    = [e["main"].get("temp", 0.0) for e in entries]
            winds    = [e["wind"].get("speed", 0.0) for e in entries]
            avg_temp = sum(temps) / len(temps)
            avg_wind = sum(winds) / len(winds)

            # Descrizione più frequente
            from collections import Counter
            descs    = [w["description"] for e in entries for w in e.get("weather", [])]
            rec_desc = Counter(descs).most_common(1)[0][0]

            label = f"Previsioni per {date_slot} a {city}"

        # Genera consiglio
        rec = self._recommendation(avg_temp, "pioggia" in rec_desc.lower(), avg_wind)

        # Messaggio finale
        msg = (
            f"{label}: {rec_desc}, temperatura media {avg_temp:.1f}°C, vento medio {avg_wind:.1f} m/s \n"
            f"{rec}."
        )
        dispatcher.utter_message(text=msg)
        return []

    def _recommendation(self, temp: float, pioggia: bool, vento: float) -> str:

        parti: List[str] = []

        # Abbigliamento principale con dettagli
        if temp < 0:
            parti.append("🥶 Cappotto pesante + maglione e sciarpa")
            parti.append("🧤 Guanti caldi")
        elif temp < 5:
            parti.append("🧥 Giaccone pesante + felpa sotto")
            parti.append("🧣 Sciarpa")
        elif temp < 10:
            parti.append("🧥 Giacca imbottita")
            parti.append("🧤 Guanti leggeri, se necessario")
        elif temp < 15:
            parti.append("🧥 Giacca primaverile o cardigan")
            parti.append("👕 Maglietta a maniche lunghe")
        elif temp < 20:
            parti.append("👕 Felpa leggera o maglia a maniche lunghe")
        else:
            parti.append("👕 T‑shirt comoda")
            parti.append("🩳 Pantaloni corti o gonna, se gradito")

        # Protezione dalla pioggia
        if pioggia:
            parti.append("🌂 Ombrello resistente")
            parti.append("🧥 Impermeabile o k-way")

        # Protezione dal vento
        if vento > 8:
            parti.append("🌬️ Giacca antivento + sciarpa")
        elif vento > 6:
            parti.append("🧥 Giubbotto antivento")

        # Accessori per il sole se non piove
        if not pioggia and vento <= 6:
            parti.append("😎 Occhiali da sole")
            parti.append("👒 Cappello o visiera")

        # Costruzione dell'elenco puntato
        elenco = "\n".join(f"• {item}" for item in parti)

        # Frase introduttiva + elenco
        return f"Ti consiglio di indossare: \n{elenco}"

class ActionActivityAdvice(Action):

    def __init__(self) -> None:
        self.client = OpenWeatherClient(API_KEY)

    def name(self) -> Text:
        return "action_activity_advice"

    def run(
        self, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        # Estrai city e date come prima
        city      = tracker.get_slot("city")
        date_slot = tracker.get_slot("date") or "oggi"

        # Proviamo a estrarre l'entità activity anche se slot non è mappato
        activity  = tracker.get_slot("activity")
        if not activity:
            activity = next(tracker.get_latest_entity_values("activity"), None)

        # Se ancora manca activity, chiedi all'utente
        if not city:
            dispatcher.utter_message(text="Per favore, indicami una città.")
            return []
        if not activity:
            dispatcher.utter_message(text="Quale attività vorresti fare?")
            return []

        # Se l'entity è stata trovata, possiamo settare lo slot per eventuali futuri turni
        events: List[SlotSet] = [SlotSet("activity", activity)]

        slot_l = date_slot.lower()
        # Prepara i dati meteorologici
        if slot_l in ["oggi", "adesso", "ora"]:
            data  = self.client.get_current(city)
            label = f"Oggi a {city}"
        else:
            # Ottieni forecast come in precedenza...
            fdata = self.client.get_forecast(city)
            if not fdata or not fdata.get("list"):
                dispatcher.utter_message(text=f"Non ho previsioni per {date_slot}.")
                return events
            today = datetime.now().date()
            if slot_l in _WEEKDAY_LOOKUP:
                wd_today  = today.weekday()
                wd_target = _WEEKDAY_LOOKUP[slot_l]
                delta     = (wd_target - wd_today + 7) % 7 or 7
                target    = today + timedelta(days=delta)
            else:
                target = today + timedelta(days={"domani":1,"dopodomani":2}.get(slot_l,0))
            tz = fdata["city"].get("timezone", 0)
            candidate = None
            for e in fdata["list"]:
                dt_loc = datetime.fromtimestamp(e["dt"], timezone.utc) + timedelta(seconds=tz)
                if dt_loc.date() == target:
                    candidate = e
                    break
            if not candidate:
                dispatcher.utter_message(text=f"Non ho previsioni utili per {date_slot}.")
                return events
            data  = {
                "weather": candidate.get("weather", []),
                "main":    candidate.get("main", {}),
                "wind":    candidate.get("wind", {})
            }
            label = f"Previsioni per {date_slot} a {city}"

        # Estrai parametri
        temp       = data["main"].get("temp", 0.0)
        desc       = data["weather"][0].get("description", "")
        rain       = "pioggia" in desc.lower() or "rain" in desc.lower()
        wind_speed = data["wind"].get("speed", 0.0)

        # Logica di yes/no sull'attività
        if rain:
            msg = (
                f"{label}: sembra piovere ({desc}), quindi non è consigliato fare "
                f"{activity}. Meglio un’attività al coperto."
            )
        elif 10 <= temp <= 25 and wind_speed < 5:
            msg = (
                f"{label}: condizioni ottimali per {activity}! "
                f"{desc}, {temp:.1f}°C e vento leggero ({wind_speed:.1f} m/s)."
            )
        else:
            reasons = []
            if temp < 10:
                reasons.append("fa freddo")
            elif temp > 30:
                reasons.append("fa molto caldo")
            if wind_speed >= 5:
                reasons.append("c'è vento")
            reason_text = " e ".join(reasons) if reasons else desc
            msg = (
                f"{label}: {reason_text}, non è l’ideale per {activity}. "
                f"Potresti considerare un’alternativa."
            )

        dispatcher.utter_message(text=msg)
        return events
