import re
import json
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

WEBHOOK_URL = "https://discord.com/api/webhooks/1520368125994860624/xpcNpvHK2sKcyiQD61anfWG39Z1ur-x9AQCBNFTwkSN3yB20ZEUvCKzPemiwlpSLgOnG"

_webhook_parts = None
_last_error = None


def _parse_webhook(url):
    m = re.match(r"https://discord\.com/api/webhooks/(\d+)/([^/]+)", url)
    if m:
        return m.group(1), m.group(2)
    return None, None


def notify(status, group="?", script="?", data=None, error=None, webhook=None, message_id=None):
    """Envoie ou édite une notification Discord.

    Retourne le message_id (pour édition ultérieure) ou None.

    status     : "debut" | "ok" | "echec" | "info"
    group      : identifiant du groupe traité
    script     : nom du script
    data       : dict avec infos supplémentaires
    error      : message d'erreur (pour status="echec")
    message_id : si fourni, édite le message existant au lieu d'en créer un
    """
    if requests is None:
        return None

    colors = {
        "debut": 0x3498DB,
        "ok":    0x2ECC71,
        "echec": 0xE74C3C,
        "info":  0xF39C12,
    }

    emojis = {
        "debut": "▶️",
        "ok":    "✅",
        "echec": "❌",
        "info":  "ℹ️",
    }

    desc = {
        "debut": "Traitement démarré",
        "ok":    "Traitement terminé",
        "echec": "Erreur",
        "info":  "Information",
    }

    fields = []
    if data:
        for k, v in data.items():
            fields.append({"name": k, "value": str(v), "inline": True})
    if error:
        fields.append({"name": "Erreur", "value": error[:1000], "inline": False})

    embed = {
        "title": f"{emojis.get(status, '')} {desc.get(status, status)}",
        "color": colors.get(status, 0x95A5A6),
        "fields": [
            {"name": "Script", "value": script, "inline": True},
            {"name": "Groupe", "value": str(group), "inline": True},
            {"name": "Heure", "value": datetime.now().strftime("%H:%M:%S"), "inline": True},
        ] + fields,
        "footer": {"text": "Facebook Media OCR"},
    }

    url = webhook or WEBHOOK_URL

    try:
        if message_id:
            wh_id, wh_token = _parse_webhook(url)
            if wh_id:
                edit_url = f"https://discord.com/api/webhooks/{wh_id}/{wh_token}/messages/{message_id}"
                r = requests.patch(edit_url, json={"embeds": [embed]}, timeout=10)
                if r.status_code not in (200, 204):
                    global _last_error
                    _last_error = f"Discord edit {r.status_code}"
                return message_id
            return None
        else:
            post_url = url + "?wait=true"
            r = requests.post(post_url, json={"embeds": [embed]}, timeout=10)
            if r.status_code == 200:
                return r.json().get("id")
            if r.status_code not in (200, 204):
                _last_error = f"Discord {r.status_code}"
            return None
    except requests.RequestException as e:
        _last_error = str(e)
        return None
