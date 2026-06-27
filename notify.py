"""
Notification Discord pour les scripts Facebook Media OCR.

Usage :
    from notify import notify

    notify("debut", group="emploi34", script="fb_graphql")
    notify("ok", group="emploi34", script="fb_graphql", data={"posts": 15, "emails": 3})
    notify("echec", group="emploi34", script="fb_graphql", error="timeout")
"""

import json
import os
import sys
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

WEBHOOK_URL = "https://discord.com/api/webhooks/1520368125994860624/xpcNpvHK2sKcyiQD61anfWG39Z1ur-x9AQCBNFTwkSN3yB20ZEUvCKzPemiwlpSLgOnG"

# Cache pour eviter les spam en cas d'erreur reseau
_last_error = None


def notify(status, group="?", script="?", data=None, error=None, webhook=None):
    """Envoie une notification Discord.

    status : "debut" | "ok" | "echec" | "info"
    group  : identifiant du groupe traite
    script : nom du script
    data   : dict avec des infos supplementaires (ex: {"posts": 15, "emails": 3})
    error  : message d'erreur (pour status="echec")
    """
    if requests is None:
        return

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

    payload = {"embeds": [embed]}
    url = webhook or WEBHOOK_URL

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            global _last_error
            _last_error = f"Discord {r.status_code}"
    except requests.RequestException as e:
        _last_error = str(e)
