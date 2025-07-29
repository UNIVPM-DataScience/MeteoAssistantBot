from typing import Any, Text, Dict, List, Optional
import os
import logging
import requests
import pandas as pd
import matplotlib.pyplot as plt
import tempfile
from datetime import datetime, timezone, timedelta
from rasa_sdk import Action, Tracker, FormValidationAction
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict
from dotenv import load_dotenv

load_dotenv()
API_KEY: str = os.getenv("OPENWEATHER_API_KEY", "")
if not API_KEY:
    raise RuntimeError("Missing OPENWEATHER_API_KEY environment variable")

logger = logging.getLogger(__name__)

_DAYS_IT = [
    "Lunedì", "Martedì", "Mercoledì", 
    "Giovedì", "Venerdì", "Sabato", "Domenica"
]

_WEEKDAY_LOOKUP = {d.lower(): idx for idx, d in enumerate(_DAYS_IT)}

def render_forecast_table(hourly_data: List[Dict[str, Any]]) -> str:
    
    df = pd.DataFrame(hourly_data)
    fig, ax = plt.subplots(figsize=(10, len(df) * 0.5 + 1))
    ax.axis("off")

    tbl = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        cellLoc="center",
        loc="center"
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1, 1.5)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png", prefix="forecast_")
    fig.tight_layout()
    fig.savefig(tmp.name, dpi=150)
    plt.close(fig)
    return tmp.name

class OpenWeatherClient:

    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.params = {"appid": api_key, "units": "metric", "lang": "it"}

    def _get(self, endpoint: str, **params) -> Optional[Dict]:
        try:
            resp = self.session.get(f"{self.BASE_URL}/{endpoint}", params=params, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error("OpenWeather API error: %s", e)
            return None

    def get_current(self, city: str) -> Optional[Dict]:
        return self._get("weather", q=city)

    def get_forecast(self, city: str) -> Optional[Dict]:
        return self._get("forecast", q=city)

    def get_uv_index(self, lat: float, lon: float) -> Optional[Dict]:
        return self._get("onecall", lat=lat, lon=lon, exclude="minutely,hourly,daily,alerts")

class ActionGetWeather(Action):

    def __init__(self) -> None:
        self.client = OpenWeatherClient(API_KEY)

    def name(self) -> Text:
        return "action_get_weather"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        city = tracker.get_slot("city")
        date_slot = tracker.get_slot("date") or "oggi"

        if not city:
            dispatcher.utter_message(text="Per favore, indicami una città.")
            return []

        if date_slot.lower() in ["oggi", "adesso", "ora"]:
            data = self.client.get_current(city)
            if not data:
                dispatcher.utter_message(text="Servizio meteo non disponibile. Riprova più tardi.")
                return []
            return self._handle_current(dispatcher, city, data)
        else:
            data = self.client.get_forecast(city)
            if not data or not data.get("list"):
                dispatcher.utter_message(text="Non sono disponibili previsioni per quella data.")
                return []
            return self._handle_forecast(dispatcher, city, date_slot, data)

    def _handle_current(
        self, dispatcher: CollectingDispatcher, city: str, data: Dict
    ) -> List[Dict[Text, Any]]:
        cod = data.get("cod")
        if str(cod) == "404":
            dispatcher.utter_message(text=f"Città '{city}' non trovata.")
            return []
        if str(cod) != "200":
            msg = data.get("message", "Errore").capitalize()
            dispatcher.utter_message(text=f"Errore meteo: {msg}.")
            return []

        main = data.get("main", {})
        wind = data.get("wind", {})
        sys = data.get("sys", {})
        coord = data.get("coord", {})

        now = datetime.now()
        day_name = _DAYS_IT[now.weekday()]
        date_time = now.strftime("%d/%m/%Y %H:%M")
        lines = [f"Meteo per {city} – {day_name} {date_time}"]
        lines.append(f"• 🌡️ Temperatura: {main.get('temp', 'N/D')} °C (percepita {main.get('feels_like', 'N/D')} °C)")
        lines.append(f"• ☔ Condizioni: {data['weather'][0].get('description', 'N/D')} {self.emoji(data['weather'][0]['description'])}")
        lines.append(f"• 💧 Umidità: {main.get('humidity', 'N/D')} %")
        lines.append(f"• 🧭 Pressione: {main.get('pressure', 'N/D')} hPa")
        lines.append(f"• 🌬️ Vento: {wind.get('speed', 'N/D')} m/s, {wind.get('deg', '—')}°")
        lines.append(f"• 👁️ Visibilità: {round(data.get('visibility', 0)/1000, 1)} km")
        lines.append(f"• ☁️ Nuvolosità: {data.get('clouds', {}).get('all', 'N/D')} %")
        lines.append(f"• 🌅 Alba: {self._format_time(sys.get('sunrise'), data.get('timezone', 0))}")
        lines.append(f"• 🌇 Tramonto: {self._format_time(sys.get('sunset'), data.get('timezone', 0))}")

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
        header = f"⛅ **Previsioni per {slot.capitalize()} – {day_name} {formatted_date}** a {city}:"
        dispatcher.utter_message(text=header)

        # 4) Mini‑card per ogni orario
        for dt_local, entry in daily_entries:
            t     = dt_local.strftime("%H:%M")
            w     = entry.get("weather",[{}])[0]
            desc  = w.get("description","N/D")
            emoji = self.emoji(desc)
            main  = entry.get("main",{})
            wind  = entry.get("wind",{})
            lines = [
                f"**{t}** — {emoji} {desc}",
                f"_Temp:_ {main.get('temp','N/D')}°C | _Umid:_ {main.get('humidity','N/D')}% | "
                f"_Vento:_ {wind.get('speed','N/D')} m/s ({wind.get('deg','—')}°) | "
            ]
            dispatcher.utter_message(text="\n".join(lines))

        return []

    def _format_time(self, ts: Any, tz_offset: int) -> Text:
        if not ts:
            return "N/D"
        return datetime.fromtimestamp(ts + tz_offset, timezone.utc).strftime("%H:%M")

    @staticmethod
    def emoji(description: str) -> Text:
        d = description.lower()
        return ("☀️" if "sole" in d or "sereno" in d else
                "🌧️" if "pioggia" in d else
                "⛈️" if "temporale" in d else
                "❄️" if "neve" in d else
                "☁️")

class ValidateWeatherForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_weather_form"

    async def validate_city(
        self, slot_value: Any, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: DomainDict
    ) -> Dict[Text, Any]:
        url = f"http://api.openweathermap.org/geo/1.0/direct?q={slot_value}&limit=1&appid={API_KEY}"
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200 or not resp.json():
            dispatcher.utter_message(response="utter_invalid_city", city=slot_value)
            return {"city": None}
        return {"city": slot_value}
