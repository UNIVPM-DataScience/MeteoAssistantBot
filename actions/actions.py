from typing import Any, Text, Dict, List
import requests
from datetime import datetime, timezone, timedelta
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk import FormValidationAction
from rasa_sdk.types import DomainDict
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENWEATHER_API_KEY")
if not API_KEY:
    raise RuntimeError("Missing OPENWEATHER_API_KEY environment variable")


class ActionGetWeather(Action):

    def name(self) -> Text:
        return "action_get_weather"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        city = tracker.get_slot("city")
        date_slot = tracker.get_slot("date") or "oggi"

        if not city:
            dispatcher.utter_message(text="Per favore, indicami una città.")
            return []

        if date_slot.lower() in ["oggi", "adesso", "ora"]:
            return self._send_current_weather(dispatcher, city)
        else:
            return self._send_forecast(dispatcher, city, date_slot)

    def _send_current_weather(
        self, dispatcher: CollectingDispatcher, city: str
    ) -> List[Dict[Text, Any]]:
        weather_data = self.get_weather(city)
        if not weather_data:
            dispatcher.utter_message(text="Problema di connessione con il servizio meteo. Riprova più tardi.")
            return []
        cod = weather_data.get("cod")
        if str(cod) == "404":
            dispatcher.utter_message(text=f"Mi dispiace, non ho trovato la città '{city}'.")
            return []
        if str(cod) != "200":
            msg = weather_data.get("message", "Errore sconosciuto").capitalize()
            dispatcher.utter_message(text=f"Errore meteo: {msg}.")
            return []

        desc, temp, feels_like, humidity, pressure, wind_speed, _ = self._extract_current_core(weather_data)
        visibility = self._extract_visibility(weather_data)
        clouds = weather_data.get("clouds", {}).get("all", "N/D")
        sunrise, sunset = self._extract_sun_times(weather_data)
        uvi_text = self._extract_uv(weather_data)

        today_str = datetime.now().strftime("%d/%m/%Y")
        lines = [
            f"Dati meteo per {city} ({today_str}):",
            f"• {self.emoji_per_desc(desc)}Descrizione: {desc}",
            f"• 🌡️ Temperatura: {temp}°C (percepita {feels_like}°C)",
            f"• 💧 Umidità: {humidity}%",
            f"• 🧭 Pressione: {pressure} hPa",
            f"• 👁️ Visibilità: {visibility}",
            f"• ☁️ Nuvolosità: {clouds}%",
            f"• 🌅 Alba: {sunrise}",
            f"• 🌇 Tramonto: {sunset}"
        ]
        dispatcher.utter_message(text="\n".join(lines))
        return []
    
    def _send_forecast(
        self, dispatcher: CollectingDispatcher, city: str, date_slot: str
    ) -> List[Dict[Text, Any]]:
        now_local = datetime.now()
        today = now_local.date()
        slot = date_slot.lower()
        offset = {"domani": 1, "dopodomani": 2}.get(slot, 0)
        target_date = today + timedelta(days=offset)

        forecast_data = self.get_forecast(city)
        entries = forecast_data.get("list", [])
        if not entries:
            dispatcher.utter_message(text="Non ci sono dati di previsione disponibili.")
            return []

        tz_offset = forecast_data["city"].get("timezone", 0) 

        daily_entries = []
        for entry in entries:
            ts = entry.get("dt")
            if ts is None:
                continue
            dt_utc = datetime.fromtimestamp(ts, timezone.utc)
            dt_local = dt_utc + timedelta(seconds=tz_offset)
            if dt_local.date() == target_date:
                daily_entries.append((dt_local, entry))

        if not daily_entries:
            dispatcher.utter_message(text=f"Non ho trovato previsioni per {slot}.")
            return []

        daily_entries.sort(key=lambda x: x[0])

        lines = [f"Previsioni per {slot.capitalize()} ({target_date.strftime('%d/%m/%Y')}) a {city}:"]
        for dt_local, entry in daily_entries:
            time_str = dt_local.strftime("%H:%M")
            weather = entry.get("weather", [{}])[0]
            desc = weather.get("description", "N/D")
            main = entry.get("main", {})
            temp = main.get("temp", "N/D")
            humidity = main.get("humidity", "N/D")
            wind_speed = entry.get("wind", {}).get("speed", "N/D")
            clouds = entry.get("clouds", {}).get("all", "N/D")
            lines.append(
                f"• {time_str} — {self.emoji_per_desc(desc)} {desc}, "
                f"{temp}°C, umidità {humidity}%, vento {wind_speed} m/s, nuvolosità {clouds}%"
            )

        dispatcher.utter_message(text="\n".join(lines))
        return []


    @staticmethod
    def get_weather(city: str) -> Dict[Text, Any]:
        url = "https://api.openweathermap.org/data/2.5/weather"
        try:
            r = requests.get(
                url,
                params={"q": city, "units": "metric", "lang": "it", "appid": API_KEY},
                timeout=5,
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException:
            return {}

    @staticmethod
    def get_forecast(city: str) -> Dict[Text, Any]:
        url = "https://api.openweathermap.org/data/2.5/forecast"
        try:
            r = requests.get(
                url,
                params={"q": city, "units": "metric", "lang": "it", "appid": API_KEY},
                timeout=5,
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException:
            return {}

    @staticmethod
    def _extract_current_core(data: Dict) -> Any:
        desc = data["weather"][0].get("description", "N/D")
        main = data.get("main", {})
        wind = data.get("wind", {})
        return (
            desc,
            main.get("temp", "N/D"),
            main.get("feels_like", "N/D"),
            main.get("humidity", "N/D"),
            main.get("pressure", "N/D"),
            wind.get("speed", "N/D"),
            wind.get("deg", "—"),
        )

    @staticmethod
    def _extract_visibility(data: Dict) -> Text:
        vis = data.get("visibility")
        return f"{vis/1000:.1f} km" if isinstance(vis, (int, float)) else "N/D"

    @staticmethod
    def _extract_sun_times(data: Dict) -> Any:
        sys = data.get("sys", {})
        tz = data.get("timezone", 0)
        fmt = lambda ts: datetime.fromtimestamp(ts + tz, timezone.utc).strftime("%H:%M") if ts else "N/D"
        return fmt(sys.get("sunrise")), fmt(sys.get("sunset"))

    @staticmethod
    def emoji_per_desc(desc: str) -> Text:
        d = desc.lower()
        if "sole" in d or "sereno" in d:
            return "☀️"
        if "nuvol" in d:
            return "☁️"
        if "pioggia" in d or "rain" in d:
            return "🌧️"
        if "neve" in d:
            return "❄️"
        if "temporale" in d or "thunderstorm" in d:
            return "⛈️"
        return "🌥️"

class ValidateWeatherForm(FormValidationAction):

    def name(self) -> Text:
        return "validate_weather_form"

    async def validate_city(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        city = slot_value.strip()
        url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={API_KEY}"
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException:
            dispatcher.utter_message(text="Servizio meteo non disponibile. Riprova più tardi.")
            return {"city": None}

        if not data:
            dispatcher.utter_message(response="utter_invalid_city", city=city)
            return {"city": None}

        return {"city": city}
