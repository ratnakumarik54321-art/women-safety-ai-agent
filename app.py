import os
import json
import sqlite3
from datetime import datetime
from functools import wraps

import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv()

# Configuration defaults
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY', '')
DEMO_MODE = os.getenv('DEMO_MODE', '1') == '1'
VOICE_PHRASE_REPEAT = int(os.getenv('VOICE_PHRASE_REPEAT', '2'))
VOICE_TIME_WINDOW = int(os.getenv('VOICE_TIME_WINDOW', '15'))  # seconds
VOICE_COOLDOWN = int(os.getenv('VOICE_COOLDOWN', '60'))  # seconds

# ------------------------------------------------------------
# Flask app configuration
# ------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.urandom(24)  # For session security – can be replaced by a fixed secret in production
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'swai.db')

# ------------------------------------------------------------
# Database helper functions
# ------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Users table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            phone TEXT,
            emergency1_name TEXT,
            emergency1_relationship TEXT,
            emergency1_phone TEXT,
            emergency2_name TEXT,
            emergency2_relationship TEXT,
            emergency2_phone TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    # Trips table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            start_location TEXT,
            destination TEXT,
            distance REAL,
            duration REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    # Emergency events
    cur.execute('''
        CREATE TABLE IF NOT EXISTS emergencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trigger_type TEXT,
            latitude REAL,
            longitude REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    # Travel (vehicle) details linked to a trip (optional)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS travel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            vehicle_type TEXT,
            vehicle_number TEXT,
            driver_name TEXT,
            driver_id TEXT,
            destination TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

# Initialise DB at start-up
init_db()

# ------------------------------------------------------------
# Authentication helpers
# ------------------------------------------------------------

# Inject configuration into templates
@app.context_processor
def inject_config():
    return {
        'GOOGLE_MAPS_API_KEY': GOOGLE_MAPS_API_KEY,
        'DEMO_MODE': DEMO_MODE,
        'VOICE_PHRASE_REPEAT': VOICE_PHRASE_REPEAT,
        'VOICE_TIME_WINDOW': VOICE_TIME_WINDOW,
        'VOICE_COOLDOWN': VOICE_COOLDOWN,
        'VOICE_CONFIDENCE_THRESHOLD': float(os.getenv('VOICE_CONFIDENCE_THRESHOLD', '0.8')),
        'ROUTE_SAFETY_RADIUS': float(os.getenv('ROUTE_SAFETY_RADIUS', '12')),
        'ROUTE_DEVIATION_READINGS': int(os.getenv('ROUTE_DEVIATION_READINGS', '3')),
        'SMS_API_KEY': os.getenv('SMS_API_KEY', ''),
    }


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user

# ------------------------------------------------------------
# Routes – Web Pages
# ------------------------------------------------------------

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

# ---- Login -------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            flash('Logged in successfully.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'danger')
    return render_template('login.html')

# ---- Register ----------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['full_name'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        phone = request.form.get('phone')
        # Emergency contacts
        e1_name = request.form.get('e1_name')
        e1_rel = request.form.get('e1_relationship')
        e1_phone = request.form.get('e1_phone')
        e2_name = request.form.get('e2_name')
        e2_rel = request.form.get('e2_relationship')
        e2_phone = request.form.get('e2_phone')
        hashed_pw = generate_password_hash(password)
        created_at = datetime.utcnow().isoformat()
        conn = get_db()
        try:
            conn.execute('''
                INSERT INTO users (name, email, password, phone,
                    emergency1_name, emergency1_relationship, emergency1_phone,
                    emergency2_name, emergency2_relationship, emergency2_phone, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ''', (name, email, hashed_pw, phone, e1_name, e1_rel, e1_phone, e2_name, e2_rel, e2_phone, created_at))
            conn.commit()
            flash('Registration successful. Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already registered.', 'danger')
        finally:
            conn.close()
    return render_template('register.html')

# ---- Dashboard ----------------------------------------------
@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    return render_template('dashboard.html', user=user)

# ---- Profile ------------------------------------------------
@app.route('/profile')
@login_required
def profile():
    user = get_current_user()
    return render_template('profile.html', user=user)

# ---- Safety Roadmap ----------------------------------------
@app.route('/roadmap')
@login_required
def roadmap():
    return render_template('roadmap.html')

# ---- Voice Safety ------------------------------------------
@app.route('/voice')
@login_required
def voice():
    return render_template('voice.html')

# ---- Travel Safety ------------------------------------------
@app.route('/travel', methods=['GET', 'POST'])
@login_required
def travel():
    if request.method == 'POST':
        vehicle_type = request.form['vehicle_type']
        vehicle_number = request.form['vehicle_number']
        driver_name = request.form['driver_name']
        driver_id = request.form['driver_id']
        destination = request.form['destination']
        user = get_current_user()
        created_at = datetime.utcnow().isoformat()
        conn = get_db()
        conn.execute('''
            INSERT INTO travel (user_id, vehicle_type, vehicle_number, driver_name, driver_id, destination, created_at)
            VALUES (?,?,?,?,?,?,?)
        ''', (user['id'], vehicle_type, vehicle_number, driver_name, driver_id, destination, created_at))
        conn.commit()
        conn.close()
        flash('Travel trip recorded.', 'success')
        return redirect(url_for('history'))
    return render_template('travel.html')

# ---- Emergency (manual SOS) --------------------------------
@app.route('/emergency')
@login_required
def emergency():
    return render_template('emergency.html')

# ---- History ------------------------------------------------
@app.route('/history')
@login_required
def history():
    user = get_current_user()
    conn = get_db()
    trips = conn.execute('SELECT * FROM trips WHERE user_id = ? ORDER BY created_at DESC', (user['id'],)).fetchall()
    emergencies = conn.execute('SELECT * FROM emergencies WHERE user_id = ? ORDER BY created_at DESC', (user['id'],)).fetchall()
    conn.close()
    return render_template('history.html', trips=trips, emergencies=emergencies)

# ---- Logout -------------------------------------------------
@app.route('/logout')
@login_required
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

# ------------------------------------------------------------
# API Endpoints (JSON)
# ------------------------------------------------------------

@app.route('/api/geocode', methods=['POST'])
@login_required
def api_geocode():
    data = request.get_json()
    address = data.get('address')
    if not address:
        return jsonify({'error': 'Missing address'}), 400
    resp = requests.get('https://nominatim.openstreetmap.org/search', params={
        'q': address,
        'format': 'json',
        'limit': 1
    }, headers={'User-Agent': 'SWAI-App'})
    if resp.status_code != 200 or not resp.json():
        return jsonify({'error': 'Geocoding failed'}), 500
    result = resp.json()[0]
    return jsonify({'lat': result['lat'], 'lon': result['lon']})

@app.route('/api/route', methods=['POST'])
@login_required
def api_route():
    data = request.get_json()
    start = data.get('start')  # expects dict with lat, lon
    dest = data.get('dest')
    if not start or not dest:
        return jsonify({'error': 'Missing start or destination'}), 400
    # OSRM public demo server
    url = f"https://router.project-osrm.org/route/v1/driving/{start['lon']},{start['lat']};{dest['lon']},{dest['lat']}"
    params = {
        'overview': 'full',
        'geometries': 'geojson'
    }
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        return jsonify({'error': 'Routing service unavailable'}), 500
    data = resp.json()
    if not data.get('routes'):
        return jsonify({'error': 'No route found'}), 404
    route = data['routes'][0]
    return jsonify({
        'geojson': route['geometry'],
        'distance': route['distance'],  # meters
        'duration': route['duration']   # seconds
    })

@app.route('/api/emergency', methods=['POST'])
@login_required
def api_emergency():
    data = request.get_json()
    trigger_type = data.get('trigger_type')
    lat = data.get('latitude')
    lon = data.get('longitude')
    user = get_current_user()
    created_at = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute('''
        INSERT INTO emergencies (user_id, trigger_type, latitude, longitude, created_at)
        VALUES (?,?,?,?,?)
    ''', (user['id'], trigger_type, lat, lon, created_at))
    conn.commit()
    conn.close()
    # Demo mode: do not actually send SMS/Email
    if DEMO_MODE:
        return jsonify({'status': 'recorded', 'demo': True, 'message': 'Demo alert – not sent'}), 201
    # Placeholder for real SMS/Email integration using env credentials
    # e.g., call Twilio or SendGrid here
    return jsonify({'status': 'recorded', 'demo': False, 'message': 'Alert dispatched'}), 201

# ------------------------------------------------------------
# Helper routes for nearby facilities (Overpass API)
# ------------------------------------------------------------

def overpass_query(lat, lon, amenity, radius=2000):
    query = f"[out:json];node[amenity={amenity}](around:{radius},{lat},{lon});out;"
    resp = requests.get('https://overpass-api.de/api/interpreter', params={'data': query})
    if resp.status_code != 200:
        return []
    elements = resp.json().get('elements', [])
    results = []
    for el in elements:
        name = el.get('tags', {}).get('name', 'Unnamed')
        el_lat = el.get('lat')
        el_lon = el.get('lon')
        dist = ((lat - el_lat) ** 2 + (lon - el_lon) ** 2) ** 0.5 * 111139  # rough meters
        results.append({'name': name, 'lat': el_lat, 'lon': el_lon, 'distance': round(dist)})
    return results

@app.route('/api/nearby', methods=['POST'])
@login_required
def api_nearby():
    data = request.get_json()
    lat = data.get('lat')
    lon = data.get('lon')
    if lat is None or lon is None:
        return jsonify({'error': 'Missing coordinates'}), 400
    # Existing Overpass data for hospitals (optional)
    hospitals = overpass_query(lat, lon, 'hospital')
    return jsonify({'hospitals': hospitals})

@app.route('/api/nearby_police', methods=['POST'])
@login_required
def api_nearby_police():
    data = request.get_json()
    lat = data.get('lat')
    lon = data.get('lon')
    if lat is None or lon is None:
        return jsonify({'error': 'Missing coordinates'}), 400
    # Google Places Nearby Search for police stations
    places_url = 'https://maps.googleapis.com/maps/api/place/nearbysearch/json'
    params = {
        'key': GOOGLE_MAPS_API_KEY,
        'location': f"{lat},{lon}",
        'radius': 2000,
        'type': 'police',
    }
    resp = requests.get(places_url, params=params)
    if resp.status_code != 200:
        return jsonify({'error': 'Places API error'}), 500
    results_json = resp.json()
    police = []
    for place in results_json.get('results', []):
        name = place.get('name')
        geom = place.get('geometry', {}).get('location', {})
        p_lat = geom.get('lat')
        p_lng = geom.get('lng')
        address = place.get('vicinity')
        phone = place.get('formatted_phone_number')
        dist = ((lat - p_lat) ** 2 + (lon - p_lng) ** 2) ** 0.5 * 111139
        police.append({
            'name': name,
            'lat': p_lat,
            'lon': p_lng,
            'address': address,
            'phone': phone,
            'distance': round(dist)
        })
    return jsonify({'police': police})
# ------------------------------------------------------------
# Simple chat endpoint (demo)
@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    data = request.get_json()
    msg = data.get('message', '')
    # Placeholder response – replace with actual AI integration
    response_text = f"AI response to: {msg}"
    return jsonify({'response': response_text})



# ------------------------------------------------------------
# Run the app

# ------------------------------------------------------------
if __name__ == '__main__':
    # Enable debug mode for development; remove in production
    app.run(host='127.0.0.1', port=5000, debug=True)
