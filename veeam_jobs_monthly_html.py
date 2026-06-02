#!/usr/bin/env python3
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURACIÓ ---
SESSIONS_FILE = os.environ.get("SESSIONS_FILE", "/tmp/veeam_sessions_monthly.json")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.yourdomain.local")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 25))
SMTP_USER = os.environ.get("SMTP_USER", "ansible@yourdomain.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "admin1@yourdomain.com,admin2@yourdomain.com").split(",")

VEEAM_API_URL = os.environ.get("VEEAM_API_URL", "https://veeam-backup-server.local:9419")
VEEAM_TOKEN = os.environ.get("VEEAM_TOKEN", "")

# --- FUNCIONS AUXILIARS ---
def parse_veeam_date(date_str):
    if not date_str: return None
    date_str = date_str.split('.')[0].replace('Z', '')
    try:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return datetime.now()

def format_hours(seconds):
    return round(seconds / 3600, 2)

def format_size(bytes_size):
    try:
        b = float(bytes_size)
        tb = b / (1024**4)
        if tb >= 1.0: return f"{round(tb, 2)} TB"
        gb = b / (1024**3)
        if gb >= 1.0: return f"{round(gb, 2)} GB"
        mb = b / (1024**2)
        return f"{round(mb, 2)} MB"
    except (ValueError, TypeError):
        return "0.0 MB"

def fetch_session_task_data(session_id):
    if not VEEAM_TOKEN: return {'tasks': [], 'session': {}}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    tasks = []
    try:
        req = urllib.request.Request(f"{VEEAM_API_URL}/api/v1/sessions/{session_id}/taskSessions?limit=2000")
        req.add_header("x-api-version", "1.3-rev1")
        req.add_header("Authorization", f"Bearer {VEEAM_TOKEN}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            tasks = json.loads(response.read().decode()).get('data', [])
    except Exception:
        pass
        
    main_session = {}
    try:
        req = urllib.request.Request(f"{VEEAM_API_URL}/api/v1/sessions/{session_id}")
        req.add_header("x-api-version", "1.3-rev1")
        req.add_header("Authorization", f"Bearer {VEEAM_TOKEN}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            main_session = json.loads(response.read().decode())
    except Exception:
        pass
            
    return {'tasks': tasks, 'session': main_session}

# --- CARREGAR DADES BASE ---
try:
    with open(SESSIONS_FILE, 'r') as f:
        raw_data = json.load(f)
        sessions = raw_data.get('data', raw_data) if isinstance(raw_data, dict) else raw_data
        if isinstance(sessions, str):
            import ast
            sessions = ast.literal_eval(sessions.replace('true', 'True').replace('false', 'False').replace('null', 'None'))
except Exception as e:
    print(f"Error llegint el JSON: {e}")
    sessions = []

now = datetime.now()
start_of_month = now - timedelta(days=30)
start_of_month = start_of_month.replace(hour=0, minute=0, second=0, microsecond=0)

valid_sessions = []
for s in sessions:
    if not isinstance(s, dict): continue
    s_name = s.get('name', 'Unknown')
    if "Health Check" in s_name: continue

    start_time = parse_veeam_date(s.get('creationTime'))
    end_time = parse_veeam_date(s.get('endTime'))
    
    if not start_time or not end_time or start_time < start_of_month:
        continue

    valid_sessions.append({
        'id': s.get('id'),
        'name': s_name,
        'start_time': start_time,
        'end_time': end_time,
        'duration_sec': max(0, (end_time - start_time).total_seconds())
    })

print(f">>> INFO: Obtenint dades volumètriques de {len(valid_sessions)} sessions en paral·lel...")

session_details_map = {}
with ThreadPoolExecutor(max_workers=15) as executor:
    future_to_id = {executor.submit(fetch_session_task_data, s['id']): s['id'] for s in valid_sessions if s.get('id')}
    for future in as_completed(future_to_id):
        s_id = future_to_id[future]
        try:
            data = future.result()
            if data: session_details_map[s_id] = data
        except Exception:
            pass

print(">>> INFO: Dades obtingudes! Generant l'informe HTML...")

# --- ESTRUCTURES DE DADES ---
job_totals_time = {}
job_totals_bytes = {}
daily_counts = {}

total_bytes_processed = 0
total_seconds_processed = 0

envs = {
    '01_PRO': {'jobs': 0, 'bytes': 0, 'seconds': 0, 'max_job': 'N/A', 'max_bytes': 0},
    '02_PRE': {'jobs': 0, 'bytes': 0, 'seconds': 0, 'max_job': 'N/A', 'max_bytes': 0},
    '03_PRE_ITG': {'jobs': 0, 'bytes': 0, 'seconds': 0, 'max_job': 'N/A', 'max_bytes': 0},
    'ALTRES': {'jobs': 0, 'bytes': 0, 'seconds': 0, 'max_job': 'N/A', 'max_bytes': 0}
}

for vs in valid_sessions:
    s_id = vs['id']
    s_name = vs['name']
    
    data_map = session_details_map.get(s_id, {})
    tasks = data_map.get('tasks', [])
    main_session = data_map.get('session', {})
    
    session_bytes = 0
    if tasks:
        for t in tasks:
            progress = t.get('progress', {})
            if not isinstance(progress, dict): progress = {}
            transferred = progress.get('transferredSize') or t.get('transferredSize') or progress.get('readSize') or 0
            session_bytes += float(transferred)
    else:
        progress = main_session.get('progress', {})
        if not isinstance(progress, dict): progress = {}
        transferred = progress.get('transferredSize') or main_session.get('transferredSize') or progress.get('readSize') or 0
        session_bytes += float(transferred)

    vs['bytes'] = session_bytes
    vs['duration_h'] = format_hours(vs['duration_sec'])
    vs['date_str'] = vs['start_time'].strftime('%d/%m/%Y')
    
    total_bytes_processed += session_bytes
    total_seconds_processed += vs['duration_sec']
    
    job_totals_time[s_name] = job_totals_time.get(s_name, 0) + vs['duration_sec']
    job_totals_bytes[s_name] = job_totals_bytes.get(s_name, 0) + session_bytes
    
    day_str = vs['start_time'].strftime('%Y-%m-%d')
    daily_counts[day_str] = daily_counts.get(day_str, 0) + 1

    # Classificació per Entorn
    env_key = 'ALTRES'
    if s_name.startswith('01 PRO'): env_key = '01_PRO'
    elif s_name.startswith('02 PRE') and not s_name.startswith('02 PRE_ITG'): env_key = '02_PRE'
    elif s_name.startswith('03 PRE_ITG') or s_name.startswith('02 PRE_ITG'): env_key = '03_PRE_ITG'

    envs[env_key]['jobs'] += 1
    envs[env_key]['bytes'] += session_bytes
    envs[env_key]['seconds'] += vs['duration_sec']
        
    if session_bytes > envs[env_key]['max_bytes']:
        envs[env_key]['max_bytes'] = session_bytes
        envs[env_key]['max_job'] = s_name

total_jobs_executed = len(valid_sessions)

# Trobar l'execució individual més gran
largest_single_exec = max(valid_sessions, key=lambda x: x.get('bytes', 0), default={})
largest_exec_name = largest_single_exec.get('name', 'N/A')
largest_exec_tb = format_size(largest_single_exec.get('bytes', 0))

# --- GRÀFIC DE BARRES HORITZONTALS ---
sorted_jobs_time = sorted(job_totals_time.items(), key=lambda x: x[1], reverse=True)[:20]
labels = [x[0][:45] + "..." if len(x[0]) > 45 else x[0] for x in sorted_jobs_time]
chart_data = [format_hours(x[1]) for x in sorted_jobs_time]

chart_config = {
    "type": "horizontalBar",
    "data": {"labels": labels, "datasets": [{"label": "Hores Processament", "data": chart_data, "backgroundColor": "#198754", "borderWidth": 1}]},
    "options": {
        "plugins": {"datalabels": {"anchor": "end", "align": "right", "color": "#333", "font": {"weight": "bold", "size": 11}}},
        "scales": {"xAxes": [{"ticks": {"beginAtZero": True}}], "yAxes": [{"ticks": {"fontSize": 10}}]},
        "legend": {"display": False}, "layout": {"padding": {"right": 40}}
    }
}
chart_url = f"https://quickchart.io/chart?w=800&h=600&c={urllib.parse.quote(json.dumps(chart_config))}"

# --- CALENDARI ---
calendar_html = "<table width='100%' style='border-collapse: collapse; margin-top: 10px; margin-bottom: 20px;'><tr>"
for d in ['Dl', 'Dt', 'Dc', 'Dj', 'Dv', 'Ds', 'Dg']:
    calendar_html += f"<th style='background-color: #e9ecef; padding: 10px 0;'>{d}</th>"
calendar_html += "</tr><tr>"

curr_date = start_of_month
for _ in range(curr_date.weekday()): calendar_html += "<td></td>"
while curr_date <= now:
    day_str = curr_date.strftime('%Y-%m-%d')
    count = daily_counts.get(day_str, 0)
    bg = "#f8f9fa" if count == 0 else "#d1e7dd" if count < 20 else "#a3cfbb" if count < 50 else "#198754"
    text_color = "#fff" if count >= 50 else "#333"
    
    calendar_html += f"<td style='background-color: {bg}; color: {text_color}; border: 2px solid #fff; text-align: center; height: 70px; width: 14%;'><strong style='font-size:14px;'>{curr_date.day}</strong><br><span style='font-size:18px;'>{count}</span></td>"
    
    if curr_date.weekday() == 6: calendar_html += "</tr><tr>"
    curr_date += timedelta(days=1)
calendar_html += "</tr></table>"

# --- GENERAR FILES D'ENTORNS ---
def build_env_html(name, data, color_hex):
    job_name = data['max_job'][:30] + "..." if len(data['max_job']) > 30 else data['max_job']
    return f"""
    <tr>
        <td style='padding: 12px; border: 1px solid #ddd; border-left: 4px solid {color_hex}; font-weight: bold;'>{name}</td>
        <td style='padding: 12px; border: 1px solid #ddd; text-align: center;'>{data['jobs']}</td>
        <td style='padding: 12px; border: 1px solid #ddd; text-align: center; color: #0d6efd; font-weight: bold;'>{format_size(data['bytes'])}</td>
        <td style='padding: 12px; border: 1px solid #ddd; text-align: center;'>{format_hours(data['seconds'])} h</td>
        <td style='padding: 12px; border: 1px solid #ddd; font-size: 11px;'>{job_name}<br><i>({format_size(data['max_bytes'])})</i></td>
    </tr>
    """

env_rows = ""
env_rows += build_env_html("01 PRO", envs['01_PRO'], "#dc3545")
env_rows += build_env_html("02 PRE", envs['02_PRE'], "#fd7e14")
env_rows += build_env_html("03 PRE_ITG", envs['03_PRE_ITG'], "#0dcaf0")
env_rows += build_env_html("Altres (Sense classificar)", envs['ALTRES'], "#6c757d")

# --- HTML I CORREU ---
html = f"""
<html>
<body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #0056b3;">Informe Mensual Veeam - IT Department</h2>
    
    <table width="100%" style="margin-bottom: 20px; border-collapse: separate; border-spacing: 10px 0;">
        <tr>
            <td width="25%" style="background: #f8f9fa; padding: 15px; border-top: 4px solid #198754; text-align: center;">
                <div style="font-size: 12px; color: #555;">Jobs Totals Executats</div>
                <div style="font-size: 22px; font-weight: bold; color: #333;">{total_jobs_executed}</div>
            </td>
            <td width="25%" style="background: #f8f9fa; padding: 15px; border-top: 4px solid #198754; text-align: center;">
                <div style="font-size: 12px; color: #555;">Volum Total Transferit</div>
                <div style="font-size: 22px; font-weight: bold; color: #0d6efd;">{format_size(total_bytes_processed)}</div>
            </td>
            <td width="25%" style="background: #f8f9fa; padding: 15px; border-top: 4px solid #198754; text-align: center;">
                <div style="font-size: 12px; color: #555;">Temps Total Processament</div>
                <div style="font-size: 22px; font-weight: bold; color: #333;">{format_hours(total_seconds_processed)} h</div>
            </td>
            <td width="25%" style="background: #f8f9fa; padding: 15px; border-top: 4px solid #dc3545; text-align: center;">
                <div style="font-size: 11px; color: #555;">Execució Més Pesada</div>
                <div style="font-size: 12px; font-weight: bold; color: #dc3545;">{largest_exec_name[:25]}...<br>({largest_exec_tb})</div>
            </td>
        </tr>
    </table>

    <h3 style="color: #0056b3; margin-top: 30px;">1. Desglossament per Entorn de Treball</h3>
    <table width="100%" style="border-collapse: collapse; font-size: 13px; margin-bottom: 30px;">
        <tr style="background-color: #e9ecef;">
            <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Entorn</th>
            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">Jobs Executats</th>
            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">Volum Transferit</th>
            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">Temps de Procés</th>
            <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Execució Més Pesada (Volum)</th>
        </tr>
        {env_rows}
    </table>

    <h3 style="color: #0056b3;">2. Mapa de Calor d'Activitat Diària</h3>
    {calendar_html}
    
    <h3 style="color: #0056b3;">3. Top 10 Jobs amb Més Volum de Dades (Acumulat Mensual)</h3>
    <table width="100%" style="border-collapse: collapse; font-size: 13px; margin-bottom: 20px;">
        <tr style="background-color: #e9ecef; text-align: left;">
            <th style="padding: 10px; border: 1px solid #ddd;">Nom del Backup Job</th>
            <th style="padding: 10px; border: 1px solid #ddd;">Dades transferides</th>
        </tr>
        {''.join(f"<tr><td style='padding: 10px; border: 1px solid #ddd;'>{name}</td><td style='padding: 10px; border: 1px solid #ddd; color: #0d6efd; font-weight: bold;'>{format_size(size)}</td></tr>" for name, size in sorted(job_totals_bytes.items(), key=lambda x: x[1], reverse=True)[:10])}
    </table>

    <h3 style="color: #0056b3;">4. Top 20 Jobs per Temps de Processament (Acumulat)</h3>
    <div style="text-align: center; border: 1px solid #eee; padding: 10px; background: #fff; margin-bottom: 20px;">
        <img src="{chart_url}" width="750" alt="Gràfic de barres">
    </div>
    
    <h3 style="color: #0056b3;">5. Top 10 Execucions Individuals amb Pitjor Rendiment (Temps)</h3>
    <table width="100%" style="border-collapse: collapse; font-size: 13px;">
        <tr style="background-color: #e9ecef; text-align: left;">
            <th style="padding: 10px; border: 1px solid #ddd;">Job</th>
            <th style="padding: 10px; border: 1px solid #ddd;">Data</th>
            <th style="padding: 10px; border: 1px solid #ddd;">Durada</th>
        </tr>
        {''.join(f"<tr><td style='padding: 10px; border: 1px solid #ddd;'>{s['name']}</td><td style='padding: 10px; border: 1px solid #ddd;'>{s['date_str']}</td><td style='padding: 10px; border: 1px solid #ddd; color: #dc3545; font-weight: bold;'>{s['duration_h']} h</td></tr>" for s in sorted(valid_sessions, key=lambda x: x['duration_sec'], reverse=True)[:10])}
    </table>
    
    <p style="font-size: 10px; color: #777; margin-top: 30px;">IT Infrastructure - Automated Veeam Reporter.</p>
</body>
</html>
"""

msg = MIMEMultipart("alternative")
msg["Subject"] = "📈 [Veeam Reporter] Informe Mensual - Rendiment d'Infraestructura"
msg["From"] = SMTP_USER
msg["To"] = ", ".join(EMAIL_TO)
msg.attach(MIMEText(html, "html"))

try:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH))
        if SMTP_USER and SMTP_PASS:
            s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())
    print(f">>> Informe enviat correctament! Processats {total_jobs_executed} jobs.")
except Exception as e:
    print(f"Error enviant el correu: {e}")
