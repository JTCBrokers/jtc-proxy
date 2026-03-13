#!/usr/bin/env python3
"""
JTC Brokers — Ticketmaster Proxy Server (Cloud Version)
Deploys to Railway.app — always on, real Ticketmaster data 24/7
"""

import json
import os
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

TM_API_KEY = os.environ.get("TM_API_KEY", "oUaXugHvRHLEjK2NAtudEudIzG7hyLGH")
PORT = int(os.environ.get("PORT", 8765))
SKIP_GENRES = ["hip-hop/rap", "hip-hop", "rap", "urban", "hip hop"]

class ProxyHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self.send_json({"status": "ok", "message": "JTC Proxy running", "source": "cloud"})
        elif path == "/presales":
            self.handle_presales()
        elif path == "/events":
            self.handle_events()
        else:
            self.send_json({"error": "Unknown endpoint"}, 404)

    def handle_presales(self):
        try:
            days_ahead = 7
            if "?" in self.path:
                qs = urllib.parse.parse_qs(self.path.split("?")[1])
                if "days" in qs:
                    days_ahead = int(qs["days"][0])

            now = datetime.utcnow()
            cutoff = now + timedelta(days=days_ahead)

            params = {
                "apikey": TM_API_KEY,
                "countryCode": "US",
                "classificationName": "Music",
                "size": 200,
                "sort": "date,asc",
                "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": (now + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            url = "https://app.ticketmaster.com/discovery/v2/events.json?" + urllib.parse.urlencode(params)
            data = self.fetch_url(url)
            events = data.get("_embedded", {}).get("events", [])

            results = []
            for e in events:
                try:
                    parsed = self.parse_event(e, now, cutoff=cutoff, presale_filter=True)
                    if parsed:
                        results.append(parsed)
                except:
                    continue

            results.sort(key=lambda x: x.get("days_until_presale", 99))
            self.send_json({"events": results, "total": len(results), "source": "ticketmaster_live"})

        except Exception as ex:
            self.send_json({"error": str(ex)}, 500)

    def handle_events(self):
        try:
            now = datetime.utcnow()
            params = {
                "apikey": TM_API_KEY,
                "countryCode": "US",
                "classificationName": "Music",
                "size": 100,
                "sort": "date,asc",
                "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            url = "https://app.ticketmaster.com/discovery/v2/events.json?" + urllib.parse.urlencode(params)
            data = self.fetch_url(url)
            events = data.get("_embedded", {}).get("events", [])
            results = []
            for e in events:
                try:
                    parsed = self.parse_event(e, now)
                    if parsed:
                        results.append(parsed)
                except:
                    continue
            self.send_json({"events": results, "total": len(results), "source": "ticketmaster_live"})
        except Exception as ex:
            self.send_json({"error": str(ex)}, 500)

    def parse_event(self, e, now, cutoff=None, presale_filter=False):
        classifications = e.get("classifications", [{}])
        genre = classifications[0].get("genre", {}).get("name", "") if classifications else ""
        segment = classifications[0].get("segment", {}).get("name", "") if classifications else ""
        if segment != "Music":
            return None
        if genre.lower() in SKIP_GENRES:
            return None

        venues = e.get("_embedded", {}).get("venues", [{}])
        venue = venues[0] if venues else {}
        venue_name = venue.get("name", "")
        city = venue.get("city", {}).get("name", "")

        venue_lower = venue_name.lower()
        if any(x in venue_lower for x in ["stadium", " field", "motor speedway"]):
            return None

        dates = e.get("dates", {})
        start = dates.get("start", {})
        event_date = start.get("dateTime", start.get("localDate", ""))
        days_out = 30
        if event_date:
            try:
                ed = datetime.fromisoformat(event_date.replace("Z", "+00:00")).replace(tzinfo=None)
                days_out = max(0, (ed - now).days)
            except:
                pass

        sales = e.get("sales", {})
        presales = sales.get("presales", [])
        public_sale = sales.get("public", {})
        public_start = public_sale.get("startDateTime", "")

        info = (e.get("info", "") or "").lower()
        please_note = (e.get("pleaseNote", "") or "").lower()
        restrictions_text = info + " " + please_note
        non_transferable = any(x in restrictions_text for x in [
            "no resale over face value", "face value exchange",
            "non-transferable", "nontransferable", "safetix"
        ])

        presale_info = []
        first_presale_date = ""
        first_presale_end = ""
        first_presale_code = ""
        days_until_presale = 999

        for ps in presales:
            ps_start = ps.get("startDateTime", "")
            ps_end = ps.get("endDateTime", "")
            ps_name = ps.get("name", "Presale")
            presale_info.append({"name": ps_name, "startDateTime": ps_start, "endDateTime": ps_end})

            if ps_start and not first_presale_date:
                try:
                    psd = datetime.fromisoformat(ps_start.replace("Z", "+00:00")).replace(tzinfo=None)
                    d = max(0, (psd - now).days)
                    if presale_filter and cutoff and not (now <= psd <= cutoff):
                        continue
                    first_presale_date = ps_start
                    first_presale_end = ps_end
                    first_presale_code = ps_name.upper().replace(" ", "")[:12]
                    days_until_presale = d
                except:
                    pass

        if presale_filter and not first_presale_date:
            return None

        price_ranges = e.get("priceRanges", [])
        wholesale = (price_ranges[0].get("min", 0) if price_ranges else 0) or 75
        resale = round(wholesale * (1.0 if non_transferable else 1.8), 2)
        market_low = round(wholesale * (1.0 if non_transferable else 1.6), 2)

        code_lower = first_presale_code.lower()
        if "amex" in code_lower:
            access_type = "amex_presale"
        elif any(x in code_lower for x in ["fan", "club", "vip"]):
            access_type = "fan_presale"
        elif first_presale_date:
            access_type = "fan_presale"
        else:
            access_type = "general"

        notes = "Face Value Exchange — no resale profit" if non_transferable else (
            f"{len(presale_info)} presale window{'s' if len(presale_info)>1 else ''}" if presale_info else ""
        )

        return {
            "id": e.get("id", ""),
            "artist": e.get("name", "Unknown"),
            "venue": venue_name,
            "city": city,
            "category": genre.lower() or "pop",
            "event_date": event_date,
            "days_out": days_out,
            "venue_size": 5000,
            "wholesale_price": wholesale,
            "resale_price": resale,
            "lowest_market_price": market_low,
            "quantity": 4,
            "sell_through_rate": 0.78,
            "risk_score": 0.25,
            "artist_score": 75,
            "ticket_limit": 4,
            "access_type": access_type,
            "presale_date": first_presale_date,
            "presale_end": first_presale_end,
            "presale_code": first_presale_code,
            "general_onsale": public_start,
            "days_until_presale": days_until_presale,
            "non_transferable": non_transferable,
            "presales": presale_info,
            "url": e.get("url", ""),
            "notes": notes,
        }

    def fetch_url(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "JTCBrokers/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {format % args}")

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"JTC Brokers Proxy — Cloud — Port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
