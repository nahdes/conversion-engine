# agent/calendar_handler.py
import os, requests
from dotenv import load_dotenv
load_dotenv()

BASE = 'https://api.cal.com/v1'

def get_available_slots(date: str) -> list:
    """Return available slots for a given date (YYYY-MM-DD)."""
    r = requests.get(f'{BASE}/slots', params={
        'apiKey': os.environ['CALCOM_API_KEY'],
        'eventTypeId': os.environ['CALCOM_EVENT_TYPE_ID'],
        'startTime': f'{date}T00:00:00Z',
        'endTime': f'{date}T23:59:59Z'
    })
    r.raise_for_status()
    return r.json().get('slots', {})

def book_slot(name: str, email: str, start: str) -> str:
    """Book a slot. Returns booking ID."""
    r = requests.post(f'{BASE}/bookings', params={
        'apiKey': os.environ['CALCOM_API_KEY']
    }, json={
        'eventTypeId': int(os.environ['CALCOM_EVENT_TYPE_ID']),
        'start': start,
        'responses': {'name': name, 'email': email,
                      'location': {'optionValue': '', 'value': 'zoom'}},
        'timeZone': 'America/New_York',
        'language': 'en',
        'metadata': {}
    })
    r.raise_for_status()
    return r.json()['id']