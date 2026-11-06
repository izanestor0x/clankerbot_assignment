import datetime as dt
import html
import json
import os
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

ICS_URL = os.environ["ICS_URL"]
TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
TZ = ZoneInfo("Europe/Madrid")
MOODLE = "https://www.vidalibarraquer.cat/moodle"
API = f"https://api.telegram.org/bot{TOKEN}"
ESTADO = "enviados.json"
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
ETIQUETAS = {
    "7d": "Queda 1 semana",
    "3d": "Quedan 3 días",
    "2d": "Quedan 2 días",
    "1d": "Queda 1 día (mañana)",
    "d0800": "HOY vence",
    "d1430": "HOY vence (tarde)",
    "d2200": "HOY vence, últimas horas",
    "1h30": "Queda 1h30",
    "1h": "Queda 1 hora",
}


def cargar_entregas():
    cal = Calendar.from_ical(requests.get(ICS_URL, timeout=30).content)
    entregas = []
    for ev in cal.walk("VEVENT"):
        ini = ev.get("DTSTART").dt
        if not isinstance(ini, dt.datetime):
            ini = dt.datetime.combine(ini, dt.time(23, 59))
        if ini.tzinfo is None:
            ini = ini.replace(tzinfo=TZ)
        ini = ini.astimezone(TZ)
        cats = ev.get("CATEGORIES")
        asig = ", ".join(str(c) for c in cats.cats) if hasattr(cats, "cats") else ""
        entregas.append({
            "uid": str(ev.get("UID")),
            "titulo": str(ev.get("SUMMARY", "")).replace("Venciment de ", "", 1),
            "asig": asig,
            "ini": ini,
        })
    return sorted(entregas, key=lambda e: e["ini"])


def recordatorios(ini):
    d = ini.date()
    r = []
    for n in (7, 3, 2, 1):
        r.append((f"{n}d", dt.datetime.combine(d - dt.timedelta(days=n), dt.time(8, 0), TZ)))
    for h, m in ((8, 0), (14, 30), (22, 0)):
        r.append((f"d{h:02d}{m:02d}", dt.datetime.combine(d, dt.time(h, m), TZ)))
    r.append(("1h30", ini - dt.timedelta(minutes=90)))
    r.append(("1h", ini - dt.timedelta(hours=1)))
    return [(k, t) for k, t in r if t < ini]


def fecha(d):
    return f"{DIAS[d.weekday()]} {d:%d/%m %H:%M}"


def enlace(e):
    ts = int(e["ini"].timestamp())
    return f"{MOODLE}/calendar/view.php?view=day&time={ts}#event_{e['uid'].split('@')[0]}"


def falta(td):
    d, h, m = td.days, td.seconds // 3600, td.seconds % 3600 // 60
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


def tarjeta(e):
    return (
        f"📌 <b>{html.escape(e['titulo'])}</b>\n"
        f"📚 {html.escape(e['asig'])}\n"
        f"🗓 {fecha(e['ini'])}\n"
        f"🔗 <a href=\"{html.escape(enlace(e))}\">Abrir en Moodle</a>"
    )


def enviar(chat_id, texto):
    try:
        r = requests.post(
            f"{API}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not r.ok:
            print("Telegram:", r.text)
    except requests.RequestException as e:
        print("Error enviando:", e)


def lista(entregas):
    ahora = dt.datetime.now(TZ)
    futuras = [e for e in entregas if e["ini"] >= ahora]
    if not futuras:
        return "🎉 No tienes entregas pendientes"
    texto = f"📚 <b>Entregas pendientes ({len(futuras)})</b>\n"
    for e in futuras:
        bloque = (
            f"\n📌 <b>{html.escape(e['titulo'])}</b>\n"
            f"   {html.escape(e['asig'])}\n"
            f"   🗓 {fecha(e['ini'])} · ⏳ {falta(e['ini'] - ahora)}\n"
            f"   🔗 <a href=\"{html.escape(enlace(e))}\">Moodle</a>"
        )
        if len(texto) + len(bloque) > 3900:
            texto += "\n…"
            break
        texto += bloque
    return texto


def comprobar(entregas, enviados):
    ahora = dt.datetime.now(TZ)
    for e in entregas:
        if e["ini"] < ahora:
            continue
        for k, t in recordatorios(e["ini"]):
            clave = f"{e['uid']}|{e['ini'].isoformat()}|{k}"
            if t > ahora or clave in enviados:
                continue
            enviados[clave] = True
            if ahora - t <= dt.timedelta(minutes=60):
                enviar(CHAT_ID, f"⏰ <b>{ETIQUETAS[k]}</b>\n\n{tarjeta(e)}")


def main():
    estado = {"enviados": {}, "offset": None}
    if os.path.exists(ESTADO):
        with open(ESTADO) as f:
            estado = json.load(f)

    entregas = cargar_entregas()

    params = {"timeout": 0}
    if estado["offset"]:
        params["offset"] = estado["offset"]
    r = requests.get(f"{API}/getUpdates", params=params, timeout=30).json()
    for u in r.get("result", []):
        estado["offset"] = u["update_id"] + 1
        m = u.get("message", {})
        if m.get("text", "").startswith("/entregas"):
            enviar(m["chat"]["id"], lista(entregas))

    comprobar(entregas, estado["enviados"])

    with open(ESTADO, "w") as f:
        json.dump(estado, f)


main()
