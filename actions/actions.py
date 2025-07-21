from typing import Any, Text, Dict, List
import requests
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk import FormValidationAction
from rasa_sdk.types import DomainDict
from rasa_sdk.events import SessionStarted, ActionExecuted


class ActionGetWeather(Action):

    def name(self) -> Text:
        return "action_get_weather"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:

        city_slot = next(tracker.get_latest_entity_values("city"), None)
        city = tracker.get_slot("city") or city_slot

        if not city:
            dispatcher.utter_message(text="Per favore, indicami una città.")
            return []
        
        weather_data = self.get_weather(city)

        if weather_data is None:
            dispatcher.utter_message(text="Mi dispiace, c'è stato un problema di connessione con il servizio meteo. Riprova più tardi.")
            return []

        cod = weather_data.get("cod")
        if str(cod) != "200":
            error_msg = weather_data.get("message", "Città non trovata.")
            dispatcher.utter_message(text=f"Errore: {error_msg.capitalize()}.")
            return []

        weather_list = weather_data.get("weather", [])
        if len(weather_list) == 0:
            dispatcher.utter_message(text="Non sono riuscito a recuperare la descrizione del meteo.")
            return []

        desc = weather_list[0].get("description", "N/D")
        main_data = weather_data.get("main", {})
        temp = main_data.get("temp", "N/D")
        feels_like = main_data.get("feels_like", "N/D")
        humidity = main_data.get("humidity", "N/D")
        pressure = main_data.get("pressure", "N/D")

        wind_data = weather_data.get("wind", {})
        wind_speed = wind_data.get("speed", "N/D")
        wind_deg = wind_data.get("deg", "—") 
        
        emoji_desc = self.emoji_per_desc(desc)
        emoji_temp = "🌡️"
        emoji_humidity = "💧"
        emoji_pressure = "🧭"
        emoji_wind = "🌬️ "

        weather_text = (
            f"Qui di seguito i dati meteo correnti per {city}:\n"
            f"  • Descrizione: {emoji_desc} {desc}\n"
            f"  • Temperatura: {emoji_temp} {temp} °C (percepita: {feels_like} °C)\n"
            f"  • Umidità: {emoji_humidity} {humidity} %\n"
            f"  • Pressione: {emoji_pressure} {pressure} hPa\n"
            f"  • Vento: {emoji_wind} {wind_speed} m/s (direzione {wind_deg}°)\n"
            f"Vuoi sapere il meteo per un’altra città?"
        )

        dispatcher.utter_message(text=weather_text)
        return []

    @staticmethod
    def get_weather(city: str) -> Dict[Text, Any]:
        api_key = "47a19881a511d20f3a7d25483a7774a6"
        base_url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "units": "metric",
            "lang": "it",
            "appid": api_key
        }

        try:
            response = requests.get(base_url, params=params, timeout=5)
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Errore durante la richiesta API: {e}")
            return None

    @staticmethod
    def emoji_per_desc(desc: str) -> Text:
        desc_lower = desc.lower()
        if "sole" in desc_lower or "sereno" in desc_lower:
            return "☀️ "
        if "nuvol" in desc_lower:
            return "☁️ "
        if "pioggia" in desc_lower or "rain" in desc_lower:
            return "🌧️ "
        if "neve" in desc_lower:
            return "❄️ "
        if "temporale" in desc_lower or "thunderstorm" in desc_lower:
            return "⛈️ "
        return "🌥️ "


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
        API_KEY = "47a19881a511d20f3a7d25483a7774a6"
        city_name = slot_value.strip()
        url_geo = f"http://api.openweathermap.org/geo/1.0/direct?q={city_name}&limit=1&appid={API_KEY}"

        try:
            response = requests.get(url_geo, timeout=5)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            dispatcher.utter_message(text="Non riesco a contattare il servizio meteo al momento, riprovi più tardi.")
            return {"city": None}

        if not data:
            dispatcher.utter_message(response="utter_invalid_city", city=city_name)
            return {"city": None}

        return {"city": city_name}
    
