#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ch_results.py — robot LOTTO AI CH : écrit ch_results.json (30 derniers tirages Swiss Lotto et EuroMillions avec le
vrai gain suisse en CHF et le nombre de gagnants de chaque rang, + prochain tirage et son jackpot).

Sources :
  1. API officielle de la Loterie Romande  jeux.loro.ch/api/dbg/game/{swissloto|euromillions}/draws   (principale)
  2. Swisslos  www.swisslos.ch/de/{swisslotto|euromillions}/information/gewinnzahlen/gewinnzahlen-quoten.html (recoupement)
Contrôles : numéros valides (Lotto 6/42 + 1/6 ; EuroMillions 5/50 + 2/12), numéros de tirage consécutifs, recoupement
Swisslos des 2 derniers tirages (numéros + numéro chance / étoiles) : sinon le robot échoue et rien n'est publié.
LORO_RESOLVE / SL_RESOLVE = IP forcent l'adresse (réseau à DNS détourné, en local).

Format : {"updated", "swissloto": [{date, draw, numbers[6], bonus[1], payouts{"6+1","6","5+1",…,"3"}, winners{…}}],
          "euromillions": [{date, draw, numbers[5], bonus[2], payouts{"5+2" … "2"}, winners{…}}],
          "next": {"swissloto": {date, jackpot}, "euromillions": {date, jackpot}}}
payouts[k] = null quand personne n'a gagné ce rang ; tirages du plus récent au plus ancien ; montants en CHF.
"""
import json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parent.parent / "ch_results.json"
KEEP = 30
ZH = ZoneInfo("Europe/Zurich")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
GAMES = {"swissloto": dict(n=6, pool=42, b=1, bpool=6, sl="swisslotto"),
         "euromillions": dict(n=5, pool=50, b=2, bpool=12, sl="euromillions")}


def curl(url, data=None, timeout=60):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", UA]
    for host, env in (("jeux.loro.ch", "LORO_RESOLVE"), ("www.swisslos.ch", "SL_RESOLVE")):
        if os.environ.get(env) and host in url:
            cmd += ["--resolve", f"{host}:443:{os.environ[env]}"]
    if data:
        cmd += ["-d", data]
    r = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=timeout + 20)
    return r.stdout if r.returncode == 0 else ""


def key(name):
    """« 6+0 » → « 6 », « 5+1 » → « 5+1 »"""
    m, b = name.split("+")
    return m if b == "0" else f"{m}+{b}"


def build(game):
    g = GAMES[game]
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=150)).strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=8)).strftime("%Y-%m-%dT00:00:00Z")
    body = curl(f"https://jeux.loro.ch/api/dbg/game/{game}/draws?startDate={start}&endDate={end}")
    try:
        rows = json.loads(body)["results"]
    except Exception:
        raise SystemExit(f"{game} : API Loterie Romande illisible ({body[:120]!r})")
    out, nxt = [], None
    for x in rows:
        local = datetime.fromisoformat(x["drawDate"]).astimezone(ZH)
        if x.get("phase") != "PAYABLE":
            if x.get("estimatedJackpot") and (nxt is None or local.date().isoformat() < nxt["date"]):
                nxt = {"date": local.date().isoformat(), "jackpot": x["estimatedJackpot"] / 100}
            continue
        r = x.get("drawResult") or {}
        nums = sorted(int(v) for v in r["matrix1"]["main"])
        bonus = sorted(int(v) for v in r["matrix2"]["main"])
        allm = nums
        if len(nums) != g["n"] or len(set(nums)) != g["n"] or not all(1 <= v <= g["pool"] for v in nums) \
                or len(bonus) != g["b"] or len(set(bonus)) != g["b"] or not all(1 <= v <= g["bpool"] for v in bonus):
            raise SystemExit(f"{game} {x['drawNumber']} : numéros invalides {nums} + {bonus}")
        payouts, winners = {}, {}
        for t in x.get("prizeTierResults") or []:
            if t.get("drawType") != "Regular":
                continue
            k = key(t["name"])
            winners[k] = int(t.get("shareCount") or 0)
            v = float(t.get("shareAmount") or 0)
            payouts[k] = v if winners[k] > 0 and v > 0 else None
        if not winners or sum(winners.values()) == 0:
            payouts, winners = None, None          # gains pas encore publiés
        out.append(dict(date=local.date().isoformat(), draw=int(x["drawNumber"]), numbers=nums, bonus=bonus,
                        payouts=payouts, winners=winners))
    out.sort(key=lambda d: d["date"], reverse=True)
    out = out[:KEEP]
    if len(out) < KEEP:
        raise SystemExit(f"{game} : seulement {len(out)} tirages")
    for a, b in zip(out, out[1:]):
        if a["draw"] != b["draw"] + 1:
            raise SystemExit(f"{game} : suite cassée {b['draw']} → {a['draw']}")
    if (now.date() - datetime.fromisoformat(out[0]["date"]).date()).days > 6:
        raise SystemExit(f"{game} : dernier tirage {out[0]['date']} trop ancien")
    # Recoupement Swisslos (page officielle, tirage par date)
    agreed = 0
    for d in out[:2]:
        day = datetime.fromisoformat(d["date"]).strftime("%d.%m.%Y")
        html = curl(f"https://www.swisslos.ch/de/{g['sl']}/information/gewinnzahlen/gewinnzahlen-quoten.html",
                    data=f"formattedFilterDate={day}&filterDate={day}&currentDate={day}")
        i = html.find("actual-numbers__numbers")
        if i < 0:
            continue
        seg = html[i:html.find("</ul>", i)]
        found = re.findall(r'actual-numbers__number___(\w+)"[^>]*>\s*(?:<img[^>]*>\s*)?<span[^>]*>\s*(\d+)', seg)
        n = sorted(int(v) for c, v in found if c == "normal")
        b = sorted(int(v) for c, v in found if c in ("lucky", "superstar"))
        if (n, b) != (d["numbers"], d["bonus"]):
            raise SystemExit(f"{game} {d['date']} : Loterie Romande {d['numbers']}+{d['bonus']} ≠ Swisslos {n}+{b}")
        agreed += 1
    print(f"{game} : {len(out)} tirages ({out[-1]['draw']}–{out[0]['draw']}), {agreed} recoupés Swisslos", file=sys.stderr)
    return out, nxt


def main():
    data = {"swissloto": None, "euromillions": None, "next": {}}
    for g in GAMES:
        data[g], data["next"][g] = build(g)
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if {k: old.get(k) for k in data} == data:
        print("Aucun changement."); return
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **data},
                              ensure_ascii=False, separators=(",", ":")))
    print(f"ch_results.json : Lotto {data['swissloto'][0]['draw']} ({data['swissloto'][0]['date']}), "
          f"Euro {data['euromillions'][0]['draw']} ({data['euromillions'][0]['date']}), next {data['next']}")


if __name__ == "__main__":
    main()
