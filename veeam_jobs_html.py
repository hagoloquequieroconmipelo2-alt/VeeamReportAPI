#!/usr/bin/env python3
import json
import os
import re
import smtplib
import ssl
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

VALID_SESSION_TYPES = {
    "BackupJob",
    "BackupCopyJob",
    "AgentBackup",
    "ConfigurationBackup"
}

DAY_NAMES_CA = {
    0: "Dl",
    1: "Dt",
    2: "Dc",
    3: "Dj",
    4: "Dv",
    5: "Ds",
    6: "Dg"
}


def parse_dt(value):
    if not value:
        return None
    s = str(value).strip()

    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S"
    ):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue

    return None


def naive_dt(dt):
    if not dt:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def get_status(raw):
    result = raw.get("result")
    if isinstance(result, dict):
        return result.get("result") or result.get("message") or raw.get("message") or "Unknown"
    if isinstance(result, str) and result.strip():
        return result
    return raw.get("message") or "Unknown"


def clean_recipients(recipients_str):
    if not recipients_str:
        return []

    text = recipients_str.replace("\n", ",")
    parts = [x.strip() for x in text.split(",") if x.strip()]
    cleaned = []

    for item in parts:
        mailto_match = re.search(r"mailto:([^)\]]+)", item, re.IGNORECASE)
        if mailto_match:
            cleaned.append(mailto_match.group(1).strip())
            continue

        bracket_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", item)
        if bracket_match:
            cleaned.append(bracket_match.group(1).strip())

    unique = []
    for mail in cleaned:
        if mail not in unique:
            unique.append(mail)

    return unique


def html_escape(text):
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def short_name(name, max_len=70):
    if name is None:
        return ""
    if len(name) <= max_len:
        return name
    return name[:max_len - 3] + "..."


def render_job_name(name, max_len=70):
    full = html_escape(name)
    short = html_escape(short_name(name, max_len))
    return f'<span title="{full}">{short}</span>'


def is_healthcheck_name(name):
    if not name:
        return False
    n = str(name).strip().lower()
    return (
        "backup health check" in n
        or "health check" in n
    )


def normalized_session_type(raw):
    session_type = raw.get("sessionType", "Unknown")
    if is_healthcheck_name(raw.get("name", "")):
        return "HealthCheck"
    return session_type


def normalize_session(raw):
    start_dt = parse_dt(raw.get("creationTime"))
    end_dt = parse_dt(raw.get("endTime"))

    if not start_dt:
        return None
    if not end_dt:
        end_dt = start_dt

    start_dt = naive_dt(start_dt)
    end_dt = naive_dt(end_dt)

    duration_h = round((end_dt - start_dt).total_seconds() / 3600.0, 2)
    if duration_h < 0:
        return None

    return {
        "id": raw.get("id", ""),
        "job_name": raw.get("name", "Unknown"),
        "status": get_status(raw),
        "session_type": normalized_session_type(raw),
        "raw_session_type": raw.get("sessionType", "Unknown"),
        "platform_name": raw.get("platformName", "Unknown"),
        "job_id": raw.get("jobId", ""),
        "start_dt": start_dt,
        "end_dt": end_dt,
        "duration_h": duration_h
    }


def load_json_data(path):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        return payload.get("data", [])
    return []


def dedupe_sessions(raw_sessions):
    seen = set()
    result = []

    for raw in raw_sessions:
        sid = raw.get("id")
        key = sid if sid else (
            raw.get("name"),
            raw.get("creationTime"),
            raw.get("endTime"),
            raw.get("sessionType")
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(raw)

    return result


def load_sessions(path):
    raw_items = dedupe_sessions(load_json_data(path))
    items = []
    for raw in raw_items:
        item = normalize_session(raw)
        if item:
            items.append(item)
    return items


def previous_week_range():
    now = datetime.now()
    today = datetime(now.year, now.month, now.day)
    start_this_week = today - timedelta(days=today.weekday())
    start_prev_week = start_this_week - timedelta(days=7)
    end_prev_week = start_this_week - timedelta(seconds=1)
    return start_prev_week, end_prev_week


def is_useful_session(session):
    if session["session_type"] in VALID_SESSION_TYPES:
        return True
    if session["session_type"] == "HealthCheck":
        return True
    return False


def filter_useful_sessions(sessions):
    return [s for s in sessions if is_useful_session(s)]


def filter_previous_week_by_overlap(sessions):
    week_start, week_end = previous_week_range()
    filtered = []

    for s in sessions:
        st = s["start_dt"]
        en = s["end_dt"]
        if en >= week_start and st <= week_end:
            filtered.append(s)

    return filtered, week_start, week_end


def overlap_hours(start_dt, end_dt, week_start, week_end):
    start = max(start_dt, week_start)
    end = min(end_dt, week_end)
    if end < start:
        return 0.0
    return round((end - start).total_seconds() / 3600.0, 2)


def week_days_from_start(week_start):
    base = week_start.date()
    return [base + timedelta(days=i) for i in range(7)]


def build_metrics(sessions, week_start, week_end):
    per_job = defaultdict(lambda: {
        "runs": 0,
        "total_h": 0.0,
        "types": set(),
        "platforms": set()
    })

    enriched = []
    for s in sessions:
        week_hours = overlap_hours(s["start_dt"], s["end_dt"], week_start, week_end)
        if week_hours <= 0:
            continue

        item = dict(s)
        item["week_hours"] = week_hours
        enriched.append(item)

        job = s["job_name"]
        per_job[job]["runs"] += 1
        per_job[job]["total_h"] += week_hours
        per_job[job]["types"].add(s["session_type"])
        per_job[job]["platforms"].add(s["platform_name"])

    for job in per_job:
        per_job[job]["total_h"] = round(per_job[job]["total_h"], 2)
        per_job[job]["types"] = ", ".join(sorted(per_job[job]["types"]))
        per_job[job]["platforms"] = ", ".join(sorted(per_job[job]["platforms"]))

    top_single = sorted(enriched, key=lambda x: x["week_hours"], reverse=True)[:15]
    top_weekly = sorted(
        [{"job_name": k, **v} for k, v in per_job.items()],
        key=lambda x: x["total_h"],
        reverse=True
    )[:15]

    return top_single, top_weekly


def floor_to_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def build_week_grid(sessions, days, week_start, week_end):
    grid = {}
    max_jobs = 0

    for day in days:
        for hour in range(24):
            grid[(day, hour)] = []

    for s in sessions:
        start = s["start_dt"]
        end = s["end_dt"]

        if end < week_start or start > week_end:
            continue

        if start < week_start:
            start = week_start
        if end > week_end:
            end = week_end

        start = floor_to_hour(start)
        end = floor_to_hour(end)

        cur = start
        while cur <= end:
            key = (cur.date(), cur.hour)
            if key in grid:
                grid[key].append(s)
                if len(grid[key]) > max_jobs:
                    max_jobs = len(grid[key])
            cur += timedelta(hours=1)

    return grid, max_jobs


def cell_color(count, max_count):
    if count == 0:
        return "#f4f6f8"
    if max_count <= 1:
        return "#d9f0d3"
    ratio = count / max_count
    if ratio <= 0.25:
        return "#d9f0d3"
    if ratio <= 0.50:
        return "#f7e6a6"
    if ratio <= 0.75:
        return "#f5b971"
    return "#e77b72"


def build_html(all_sessions, useful_sessions, healthcheck_count, top_single, top_weekly, days, grid, max_jobs, week_start, week_end):
    total_sessions = len(useful_sessions)
    total_all = len(all_sessions)
    date_ini = week_start.strftime("%d/%m/%Y")
    date_fi = week_end.strftime("%d/%m/%Y")

    rows_single = []
    for s in top_single:
        rows_single.append(
            f"<tr><td>{render_job_name(s['job_name'])}</td><td>{html_escape(s['session_type'])}</td><td>{html_escape(s['platform_name'])}</td><td class='num'>{s['week_hours']:.2f}</td></tr>"
        )

    rows_weekly = []
    for s in top_weekly:
        rows_weekly.append(
            f"<tr><td>{render_job_name(s['job_name'])}</td><td>{html_escape(s['types'])}</td><td>{html_escape(s['platforms'])}</td><td class='num'>{s['runs']}</td><td class='num'>{s['total_h']:.2f}</td></tr>"
        )

    grid_header = []
    for d in days:
        grid_header.append(f"<th>{DAY_NAMES_CA[d.weekday()]}<br>{d.strftime('%d/%m')}</th>")

    grid_rows = []
    for hour in range(24):
        cols = [f"<td class='hour-label'>{hour:02d}:00</td>"]
        for d in days:
            jobs = grid[(d, hour)]
            count = len(jobs)
            bg = cell_color(count, max_jobs)

            if count == 0:
                content = "<span class='cell-zero'>·</span>"
                title = "Sense jobs actius"
            else:
                shown = jobs[:3]
                lines = "<br>".join([f"- {html_escape(short_name(j['job_name'], 42))}" for j in shown])
                extra = f"<br>... i {count - 3} més" if count > 3 else ""
                content = f"<div class='cell-count'>{count}</div><div class='cell-mini'>{lines}{extra}</div>"
                title = f"{count} jobs actius en aquesta franja"

            cols.append(f"<td class='heat-cell' style='background:{bg};' title='{html_escape(title)}'>{content}</td>")

        grid_rows.append(f"<tr>{''.join(cols)}</tr>")

    html = f"""<!DOCTYPE html>
<html lang="ca">
<head>
<meta charset="utf-8">
<title>Informe setmanal Veeam</title>
<style>
body {{
  font-family: Arial, sans-serif;
  background: #f5f7fa;
  color: #222;
  margin: 0;
  padding: 20px;
}}
.container {{
  max-width: 1650px;
  margin: auto;
}}
.card {{
  background: white;
  border-radius: 10px;
  padding: 18px;
  margin-bottom: 18px;
  box-shadow: 0 2px 10px rgba(0,0,0,0.08);
}}
h1 {{
  margin: 0 0 10px 0;
  color: #123b63;
}}
h2 {{
  margin-top: 0;
  color: #234d77;
}}
p {{
  margin: 6px 0 0 0;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  margin-top: 12px;
  table-layout: fixed;
}}
th, td {{
  border: 1px solid #d9e1ea;
  padding: 8px;
  font-size: 13px;
  vertical-align: top;
  word-wrap: break-word;
}}
th {{
  background: #eaf2fb;
  text-align: left;
}}
tr:nth-child(even) {{
  background: #fafcff;
}}
.num {{
  text-align: right;
  white-space: nowrap;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}}
.kpi {{
  background: #f8fbff;
  border: 1px solid #dbe9f6;
  border-radius: 8px;
  padding: 12px;
}}
.kpi .label {{
  font-size: 12px;
  color: #5d7288;
}}
.kpi .value {{
  font-size: 24px;
  font-weight: bold;
  margin-top: 5px;
  color: #123b63;
}}
.small {{
  font-size: 12px;
  color: #6b7785;
}}
.heatmap {{
  table-layout: fixed;
}}
.heatmap th, .heatmap td {{
  font-size: 11px;
  padding: 6px;
}}
.hour-label {{
  width: 70px;
  font-weight: bold;
  background: #f0f4f8;
}}
.heat-cell {{
  width: calc((100% - 70px) / 7);
}}
.cell-count {{
  font-weight: bold;
  font-size: 13px;
  margin-bottom: 4px;
}}
.cell-mini {{
  font-size: 10px;
  line-height: 1.2;
  color: #2f3b45;
}}
.cell-zero {{
  color: #9aa6b2;
  font-size: 16px;
}}
</style>
</head>
<body>
<div class="container">

  <div class="card">
    <h1>Informe setmanal Veeam ({date_ini} - {date_fi})</h1>
    <p>Setmana natural anterior completa. S'analitzen sessions BackupJob, BackupCopyJob, AgentBackup, ConfigurationBackup i HealthCheck detectats per nom.</p>
    <p class="small">Font: API Veeam /api/v1/sessions.</p>
    <p class="small">La graella mostra ocupació real per franja horària, incloent el tram de sessió que solapa amb la setmana informada.</p>
  </div>

  <div class="card">
    <div class="grid">
      <div class="kpi"><div class="label">Sessions totals API</div><div class="value">{total_all}</div></div>
      <div class="kpi"><div class="label">Sessions útils informe</div><div class="value">{total_sessions}</div></div>
      <div class="kpi"><div class="label">Health checks</div><div class="value">{healthcheck_count}</div></div>
      <div class="kpi"><div class="label">Data inici</div><div class="value">{date_ini}</div></div>
      <div class="kpi"><div class="label">Data fi</div><div class="value">{date_fi}</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Top sessions per temps dins setmana</h2>
    <table>
      <tr><th style="width:45%;">Job</th><th style="width:18%;">Tipus</th><th style="width:18%;">Plataforma</th><th style="width:10%;">Hores</th></tr>
      {''.join(rows_single)}
    </table>
  </div>

  <div class="card">
    <h2>Top jobs per temps total setmanal</h2>
    <table>
      <tr><th style="width:42%;">Job</th><th style="width:18%;">Tipus</th><th style="width:18%;">Plataforma</th><th style="width:8%;">Exec.</th><th style="width:10%;">Total hores</th></tr>
      {''.join(rows_weekly)}
    </table>
  </div>

  <div class="card">
    <h2>Graella setmanal per ocupació</h2>
    <table class="heatmap">
      <tr><th>Hora</th>{''.join(grid_header)}</tr>
      {''.join(grid_rows)}
    </table>
  </div>

  <div class="card">
    <p class="small">IT Infrastructure - Automated Veeam Reporter</p>
  </div>

</div>
</body>
</html>"""
    return html


def send_email(html):
    smtp_server = os.environ.get("SMTP_HOST", "")
    try:
        smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    except Exception:
        smtp_port = 25

    user_raw = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    recipients_str = os.environ.get("EMAIL_TO", "")
    recipients = clean_recipients(recipients_str)

    if not smtp_server:
        raise Exception("SMTP_HOST no definit")
    if not recipients:
        raise Exception("No hi ha destinataris vàlids a EMAIL_TO")

    sender = user_raw if user_raw else "veeam-reporter@localhost"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = str(Header("📊 [Veeam Reporter] Informe setmanal", "utf-8"))
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    s = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
    s.ehlo()

    try:
        s.starttls(context=context)
        s.ehlo()
    except Exception:
        pass

    if user_raw and password:
        s.login(user_raw, password)

    s.sendmail(sender, recipients, msg.as_string())
    s.quit()


def debug_health_sessions(all_sessions, week_start, week_end):
    print("DEBUG health sessions detectades:")
    found = 0
    for s in all_sessions:
        if is_healthcheck_name(s["job_name"]) or s["session_type"] == "HealthCheck":
            found += 1
            overlaps = s["end_dt"] >= week_start and s["start_dt"] <= week_end
            print(
                f"DEBUG HC | name={s['job_name']} | raw_type={s['raw_session_type']} | "
                f"norm_type={s['session_type']} | start={s['start_dt']} | end={s['end_dt']} | "
                f"overlap={overlaps}"
            )
    print(f"DEBUG total health detectades: {found}")


def main():
    sessions_file = os.environ.get("SESSIONS_FILE", "")
    if not sessions_file or not os.path.exists(sessions_file):
        raise Exception("No existeix SESSIONS_FILE")

    all_sessions = load_sessions(sessions_file)
    useful_api_sessions = filter_useful_sessions(all_sessions)
    week_sessions, week_start, week_end = filter_previous_week_by_overlap(useful_api_sessions)

    debug_health_sessions(all_sessions, week_start, week_end)

    if not week_sessions:
        raise Exception("No hi ha sessions útils amb solape a la setmana anterior")

    healthcheck_count = len([s for s in week_sessions if s["session_type"] == "HealthCheck"])

    top_single, top_weekly = build_metrics(week_sessions, week_start, week_end)
    days = week_days_from_start(week_start)
    grid, max_jobs = build_week_grid(week_sessions, days, week_start, week_end)

    html = build_html(
        all_sessions,
        week_sessions,
        healthcheck_count,
        top_single,
        top_weekly,
        days,
        grid,
        max_jobs,
        week_start,
        week_end
    )

    send_email(html)

    print("Correu enviat correctament")
    print(f"Setmana analitzada: {week_start.strftime('%Y-%m-%d')} -> {week_end.strftime('%Y-%m-%d')}")
    print(f"Sessions totals API combinades: {len(all_sessions)}")
    print(f"Sessions útils amb solape setmanal: {len(week_sessions)}")
    print(f"Health checks dins setmana: {healthcheck_count}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error crític: {e}")
        sys.exit(1)
