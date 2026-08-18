#!/usr/bin/env python3
"""Surveille la dispo de tailles precises sur thereformation.com et pousse une notif ntfy."""

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
LOG_PATH = os.path.join(HERE, "watch.log")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def log(msg):
    line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (IOError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=45) as resp:
        raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def parse_size_labels(html):
    """sku -> libelle de taille lisible, depuis les boutons de taille du PDP."""
    labels = {}
    for chunk in html.split('data-attr="size"')[1:]:
        window = chunk[:900]
        sku = re.search(r'data-product="([^"]+)"', window)
        title = re.search(r'data-title="Size:\s*([^",]+)', window)
        if sku and title:
            labels[sku.group(1)] = title.group(1).strip()
    return labels


def parse_offers(html):
    """sku -> {'in_stock': bool, 'price': str} depuis le JSON-LD Product."""
    offers = {}
    for block in re.findall(
            r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "Product":
            continue
        raw = data.get("offers")
        if isinstance(raw, dict):
            raw = raw.get("offers", [raw])
        for offer in raw or []:
            sku = offer.get("sku")
            if not sku:
                continue
            offers[sku] = {
                "in_stock": "InStock" in str(offer.get("availability", "")),
                "price": str(offer.get("price", "")),
            }
    return offers


def read_stock(url, color, sizes):
    html = fetch(url)
    labels = parse_size_labels(html)
    offers = parse_offers(html)
    if not offers:
        raise RuntimeError("aucune offre trouvee dans le JSON-LD (page changee ?)")

    wanted = {str(s).strip().lower() for s in sizes}
    result = {}
    for sku, info in offers.items():
        if color and color.upper() not in sku.upper():
            continue
        label = labels.get(sku) or re.sub(r"^0+(?=\d)", "", sku[-3:])
        result[label] = dict(info, sku=sku)
    matched = {lab: inf for lab, inf in result.items() if lab.lower() in wanted}
    return result, matched


def notify(cfg, title, message, priority="default", tags="dress", click=None):
    topic = cfg["ntfy_topic"]
    server = cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
        "Tags": tags,
        "Markdown": "yes",
    }
    if click:
        headers["Click"] = click
        headers["Actions"] = "view, Ouvrir la page, %s" % click
    req = urllib.request.Request("%s/%s" % (server, topic),
                                 data=message.encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    log("notif envoyee -> %s (%s)" % (topic, title))


def run_once(cfg, state, force_notify=False):
    watch = cfg["watch"]
    url, color, sizes = watch["url"], watch.get("color", ""), watch["sizes"]
    name = watch.get("name", "Article")
    repeat_after = float(cfg.get("repeat_hours", 12)) * 3600
    now = time.time()

    try:
        allsizes, matched = read_stock(url, color, sizes)
    except Exception as exc:                                  # reseau, parsing...
        state["fail_count"] = state.get("fail_count", 0) + 1
        log("ERREUR (%d consecutives): %s" % (state["fail_count"], exc))
        if state["fail_count"] in (6, 60):                    # ~1h puis ~10h
            try:
                notify(cfg, "Robot Reformation en panne",
                       "%d verifications de suite ont echoue :\n`%s`" %
                       (state["fail_count"], exc),
                       priority="low", tags="warning")
            except Exception as exc2:
                log("notif d'erreur impossible: %s" % exc2)
        return state

    state["fail_count"] = 0
    state["last_check"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["last_seen"] = {lab: inf["in_stock"] for lab, inf in sorted(allsizes.items())}

    if not matched:
        log("ATTENTION: aucune des tailles %s trouvee (dispo: %s)"
            % (sizes, ", ".join(sorted(allsizes))))
        return state

    log("check: " + " | ".join("%s=%s" % (lab, "EN STOCK" if inf["in_stock"] else "rupture")
                               for lab, inf in sorted(matched.items())))

    known = state.setdefault("sizes", {})
    back = []
    for label, info in sorted(matched.items()):
        prev = known.get(label, {})
        was_in = bool(prev.get("in_stock"))
        last_notified = float(prev.get("notified_at", 0))
        if info["in_stock"] and (not was_in or now - last_notified > repeat_after):
            back.append(label)
            prev["notified_at"] = now
        elif not info["in_stock"]:
            prev["notified_at"] = 0
        prev["in_stock"] = info["in_stock"]
        known[label] = prev

    if back or force_notify:
        avail = [lab for lab, inf in sorted(matched.items()) if inf["in_stock"]]
        price = next((inf["price"] for inf in matched.values() if inf["price"]), "")
        if avail:
            title = "%s — taille %s dispo !" % (name, " et ".join(avail))
            body = "**%s** est de retour en stock%s.\n\nTaille(s) : **%s**\n\nFonce." % (
                name, (" à $" + price) if price else "", ", ".join(avail))
            notify(cfg, title, body, priority="urgent", tags="dress,rotating_light", click=url)
        else:
            body = "\n".join("%s : %s" % (lab, "EN STOCK" if inf["in_stock"] else "rupture")
                             for lab, inf in sorted(matched.items()))
            notify(cfg, "%s — etat actuel" % name, body, priority="default",
                   tags="mag", click=url)
    return state


def main():
    ap = argparse.ArgumentParser(description="Surveillance de stock Reformation")
    ap.add_argument("--status", action="store_true",
                    help="affiche l'etat de toutes les tailles et sort")
    ap.add_argument("--test-notif", action="store_true",
                    help="envoie une notif de test tout de suite")
    ap.add_argument("--notify-now", action="store_true",
                    help="verifie et notifie l'etat courant meme sans changement")
    ap.add_argument("--loop", type=int, default=1, metavar="N",
                    help="enchaine N verifications dans un seul run (defaut 1)")
    ap.add_argument("--interval", type=int, default=100, metavar="S",
                    help="secondes entre deux verifications d'un meme run (defaut 100)")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        sys.exit("config.json introuvable ou invalide")
    # En CI le topic vient d'un secret, jamais du fichier versionne.
    if os.environ.get("NTFY_TOPIC"):
        cfg["ntfy_topic"] = os.environ["NTFY_TOPIC"].strip()
    if not cfg.get("ntfy_topic"):
        sys.exit("aucun topic ntfy: renseigner ntfy_topic ou la variable NTFY_TOPIC")

    if args.test_notif:
        notify(cfg, "Robot Reformation actif",
               "Surveillance de **%s** en tailles **%s** demarree.\n\n"
               "Tu recevras une alerte des qu'une des deux revient en stock."
               % (cfg["watch"]["name"], ", ".join(map(str, cfg["watch"]["sizes"]))),
               priority="default", tags="white_check_mark", click=cfg["watch"]["url"])
        return

    if args.status:
        allsizes, _ = read_stock(cfg["watch"]["url"], cfg["watch"].get("color", ""),
                                 cfg["watch"]["sizes"])
        def sortkey(lab):
            return (0, float(lab)) if lab.replace(".", "").isdigit() else (1, 0)
        print("%s (%s)" % (cfg["watch"]["name"], cfg["watch"].get("color", "")))
        for label in sorted(allsizes, key=sortkey):
            print("  taille %-4s %s" % (label,
                  "EN STOCK" if allsizes[label]["in_stock"] else "rupture"))
        return

    state = load_json(STATE_PATH, {})
    for i in range(max(1, args.loop)):
        state = run_once(cfg, state, force_notify=args.notify_now and i == 0)
        save_json(STATE_PATH, state)
        if i < args.loop - 1:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
