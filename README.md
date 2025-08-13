# 🌦️ MeteoAssistantBot

A conversational assistant built with **Rasa** to provide **real-time weather forecasts** and **personalized suggestions** via **Telegram**.  
This chatbot uses **Natural Language Processing (NLP)** to understand user requests and integrates with the **OpenWeather API** for accurate weather data, enriched with cultural and tourist information from the **European Tour Destinations dataset**.

---

## 📌 1. Introduction

### ❓ Definition and challenges of automatic weather forecasts
Access to accurate, real-time weather data is essential for planning activities, travel, events, agriculture, and logistics.  
Traditional weather services require manual searches, lack personalization, and rarely support natural, conversational interaction.

### 🎯 Project goal
MeteoAssistantBot aims to:
- Understand natural language requests (even informal).
- Retrieve real-time weather data from external APIs.
- Present personalized and clear weather information.
- Integrate seamlessly with platforms like Telegram, Slack, or web chat.

### 🛠️ Proposed solution (Rasa)
The system is built with **Rasa**, leveraging:
- **Rasa NLU** – intent recognition and entity extraction.
- **Rasa Core** – conversation flow management.
- **Custom Actions** – API calls and tailored responses.

---

## 📊 2. Dataset

### 🌍 Data sources
1. **Dynamic data** – OpenWeather API (`Current Weather`, `5-day Forecast`, `One Call API`).
2. **Static data** – *European Tour Destinations* dataset from Kaggle.

### 🗂 Dataset role
- Provide coordinates for direct API queries.
- Add tourist and cultural context (best travel time, attractions, local cuisine).

---

## ⚙️ 3. Methodology

### 🔎 Rasa framework
Two main components:
- **Rasa NLU** – Intent classification & Entity extraction.
- **Rasa Core** – Policy-based dialogue management.

### 🧩 Bot architecture
- `domain.yml` – intents, entities, slots, actions, responses.
- `config.yml` – NLU and dialogue pipeline configuration.
- `data/` – training data (`nlu.yml`, `stories.yml`, `rules.yml`).
- `actions/` – Python custom actions (weather retrieval, tourist info, advice).
- `credentials.yml` – channel integration (Telegram).
- `endpoints.yml` – API and service connections.

### 💬 Main functionalities
- **Weather info** – current & forecast.
- **Clothing suggestions** – based on forecast.
- **Activity recommendations** – outdoor suitability.
- **Air quality** – AQI and pollutants.
- **Sunrise/Sunset times**.
- **City overview** – cultural & tourist facts.

---

## 📲 4. Telegram Integration & Testing

### 📡 Telegram setup
- Created via **BotFather**.
- Configured in `credentials.yml` with `access_token`, `verify`, and `webhook_url`.
- Uses **ngrok** for local server tunneling.

### 🧪 Testing
Tested interactions include:
- Weather forecasts (current & future dates).
- Tourist info requests.
- Clothing and activity suggestions.
- Air quality and sun times queries.

---

## 📈 5. Results & Future Work

### ✅ Achievements
- Fully functional Rasa chatbot for weather-based assistance.
- Integration with OpenWeather API and tourist dataset.
- Deployed on Telegram for mobile-friendly access.

### 🚀 Future improvements
- Expand supported scenarios and responses.
- Save user preferences for personalized experience.
- Add more data sources (UV index, local events).
- Enhance contextual understanding.

---
