#!/usr/bin/env python3
"""
JTC Brokers — Full Stack Server
Serves dashboard + proxies Ticketmaster + proxies Anthropic API
Deploy to Render — one URL, everything works
"""
import json, os, urllib.request, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

TM_API_KEY  = os.environ.get("TM_API_KEY",  "oUaXugHvRHLEjK2NAtudEudIzG7hyLGH")
CLAUDE_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY  = os.environ.get("GEMINI_API_KEY", "AIzaSyCJ6o_rwCUp1QgRZiFnQDrGGTRc08k6DA0")
GROK_KEY    = os.environ.get("XAI_API_KEY", "xai-9Zv6ckEH4qJ6gjKXf5uxpUH8rUJaIgsJTccxJid9q4k6vhq40DWcya1yk0lE8Cer30TKhmCSazrbzVXq")
TAVILY_KEY  = os.environ.get("TAVILY_API_KEY", "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")
PORT        = int(os.environ.get("PORT", 8080))
SKIP_GENRES = {"hip-hop/rap","hip-hop","rap","urban","hip hop"}

# ── HTML dashboard is embedded below ──────────────────────────────────────────
DASHBOARD_HTML = open("dashboard.html", "rb").read() if os.path.exists("dashboard.html") else b"<h1>dashboard.html not found</h1>"

class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML)
        elif path == "/health":
            self.send_json({"status":"ok","source":"jtc-fullstack"})
        elif path == "/events":
            self.handle_events()
        elif path == "/presales":
            self.handle_presales()
        else:
            self.send_json({"error":"not found"},404)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length",0))
        body   = self.rfile.read(length)
        if path == "/api/claude":
            self.handle_claude(body)
        elif path == "/api/gemini":
            self.handle_gemini(body)
        elif path == "/api/grok":
            self.handle_grok(body)
        elif path == "/api/search":
            self.handle_search(body)
        else:
            self.send_json({"error":"not found"},404)

    # ── Anthropic proxy ──────────────────────────────────────────────────────
    def handle_claude(self, body):
        if not CLAUDE_KEY:
            self.send_json({"error":"ANTHROPIC_API_KEY not set on server"},500); return
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=body,
                headers={
                    "Content-Type":  "application/json",
                    "x-api-key":     CLAUDE_KEY,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "web-search-2025-03-05"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err = e.read()
            print(f"Anthropic API error {e.code}: {err.decode()[:500]}")
            self.send_response(e.code)
            self.send_header("Content-Type","application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(err)
        except Exception as ex:
            self.send_json({"error":str(ex)},500)

    # ── xAI Grok proxy (live web + X search for fan club / tour lookup) ─────
    def handle_grok(self, body):
        if not GROK_KEY:
            self.send_json({"error":"XAI_API_KEY not set on server"},500); return
        try:
            payload = json.loads(body)
            prompt  = payload.get("prompt","")
            max_tok = payload.get("max_tokens", 1000)
            # xAI Agent Tools API — /v1/responses with built-in web_search + x_search
            grok_body = json.dumps({
                "model": "grok-3",
                "input": [{"role":"user","content":prompt}],
                "max_output_tokens": max_tok,
                "tools": [{"type":"web_search"},{"type":"x_search"}]
            }).encode()
            req = urllib.request.Request(
                "https://api.x.ai/v1/responses",
                data=grok_body,
                headers={
                    "Content-Type":  "application/json",
                    "Authorization": f"Bearer {GROK_KEY}"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
            # Extract text from output messages
            text = ""
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") == "output_text":
                            text += c.get("text", "")
            if not text:
                text = str(data)  # fallback: log raw response
            self.send_json({"text": text})
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            print(f"Grok API error {e.code}: {err[:500]}")
            self.send_json({"error":f"Grok {e.code}: {err[:300]}"}, e.code)
        except Exception as ex:
            self.send_json({"error":str(ex)},500)

    # ── Tavily search proxy (web + X search for artist presale research) ─────
    def handle_search(self, body):
        if not TAVILY_KEY:
            self.send_json({"error":"TAVILY_API_KEY not set"},500); return
        try:
            payload  = json.loads(body)
            artist   = payload.get("artist","")
            results  = {}

            def tavily_search(query, domains=None):
                req_body = {"api_key":TAVILY_KEY,"query":query,"max_results":5,"search_depth":"basic"}
                if domains: req_body["include_domains"] = domains
                req = urllib.request.Request(
                    "https://api.tavily.com/search",
                    data=json.dumps(req_body).encode(),
                    headers={"Content-Type":"application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read().decode()).get("results",[])

            # Web search: fan clubs, label sites, official artist pages
            web = tavily_search(f"{artist} fan club mailing list presale signup official site newsletter")
            # X search: tour announcements, presale drops, fan club posts
            x   = tavily_search(f"{artist} presale fan club signup tour 2025 2026", ["x.com","twitter.com"])

            self.send_json({"web": web, "x": x})
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            print(f"Tavily error {e.code}: {err[:300]}")
            self.send_json({"error":f"Tavily {e.code}: {err[:200]}"},e.code)
        except Exception as ex:
            self.send_json({"error":str(ex)},500)

    # ── Gemini proxy (Google Search grounding for fan club link lookup) ──────
    def handle_gemini(self, body):
        if not GEMINI_KEY:
            self.send_json({"error":"GEMINI_API_KEY not set on server"},500); return
        try:
            payload = json.loads(body)
            prompt  = payload.get("prompt","")
            max_tok = payload.get("max_tokens", 1000)
            gemini_body = json.dumps({
                "contents": [{"role":"user","parts":[{"text":prompt}]}],
                "tools": [{"google_search":{}}],
                "generationConfig": {"maxOutputTokens":max_tok,"temperature":0.1}
            }).encode()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
            req = urllib.request.Request(url, data=gemini_body,
                headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            text = data.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","")
            self.send_json({"text": text})
        except urllib.error.HTTPError as e:
            err = e.read()
            print(f"Gemini API error {e.code}: {err.decode()[:500]}")
            self.send_json({"error":f"Gemini {e.code}: {err.decode()[:200]}"},e.code)
        except Exception as ex:
            self.send_json({"error":str(ex)},500)

    # ── Ticketmaster: all events ─────────────────────────────────────────────
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
            if "?" in self.path:
                qs = urllib.parse.parse_qs(self.path.split("?")[1])
                if "keyword" in qs: params["keyword"] = qs["keyword"][0]
                if "city"    in qs: params["city"]    = qs["city"][0]
            url  = "https://app.ticketmaster.com/discovery/v2/events.json?" + urllib.parse.urlencode(params)
            data = self.fetch(url)
            events = data.get("_embedded",{}).get("events",[])
            results = [r for e in events for r in [self.parse_event(e,now)] if r]
            self.send_json({"events":results,"total":len(results),"source":"ticketmaster_live"})
        except Exception as ex:
            self.send_json({"error":str(ex)},500)

    # ── Ticketmaster: presales window ────────────────────────────────────────
    def handle_presales(self):
        try:
            days = 7
            if "?" in self.path:
                qs = urllib.parse.parse_qs(self.path.split("?")[1])
                if "days" in qs: days = int(qs["days"][0])
            now    = datetime.utcnow()
            cutoff = now + timedelta(days=days)
            params = {
                "apikey": TM_API_KEY,
                "countryCode": "US",
                "classificationName": "Music",
                "size": 200,
                "sort": "date,asc",
                "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": (now + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            url  = "https://app.ticketmaster.com/discovery/v2/events.json?" + urllib.parse.urlencode(params)
            data = self.fetch(url)
            events  = data.get("_embedded",{}).get("events",[])
            results = [r for e in events for r in [self.parse_event(e,now,cutoff,True)] if r]
            results.sort(key=lambda x: x.get("days_until_presale",99))
            self.send_json({"events":results,"total":len(results),"source":"ticketmaster_live"})
        except Exception as ex:
            self.send_json({"error":str(ex)},500)

    # ── Parse TM event ───────────────────────────────────────────────────────
    def parse_event(self, e, now, cutoff=None, presale_filter=False):
        clf   = e.get("classifications",[{}])
        genre = clf[0].get("genre",{}).get("name","") if clf else ""
        seg   = clf[0].get("segment",{}).get("name","") if clf else ""
        if seg != "Music" or genre.lower() in SKIP_GENRES: return None
        venues = e.get("_embedded",{}).get("venues",[{}])
        venue  = venues[0] if venues else {}
        vname  = venue.get("name","")
        city   = venue.get("city",{}).get("name","")
        if any(x in vname.lower() for x in ["stadium"," field","speedway"]): return None
        dates  = e.get("dates",{})
        start  = dates.get("start",{})
        edate  = start.get("dateTime", start.get("localDate",""))
        dout   = 30
        if edate:
            try:
                ed   = datetime.fromisoformat(edate.replace("Z","+00:00")).replace(tzinfo=None)
                dout = max(0,(ed-now).days)
            except: pass
        sales    = e.get("sales",{})
        presales = sales.get("presales",[])
        pub      = sales.get("public",{})
        pub_start= pub.get("startDateTime","")
        info     = ((e.get("info","") or "") + " " + (e.get("pleaseNote","") or "")).lower()
        non_tf   = any(x in info for x in ["face value exchange","non-transferable","nontransferable","safetix"])
        ps_list  = []
        first_pd = first_pe = first_code = ""
        d_until  = 999
        for ps in presales:
            ps_start = ps.get("startDateTime","")
            ps_end   = ps.get("endDateTime","")
            ps_name  = ps.get("name","Presale")
            ps_list.append({"name":ps_name,"startDateTime":ps_start,"endDateTime":ps_end})
            if ps_start and not first_pd:
                try:
                    psd = datetime.fromisoformat(ps_start.replace("Z","+00:00")).replace(tzinfo=None)
                    d   = max(0,(psd-now).days)
                    if presale_filter and cutoff and not (now <= psd <= cutoff): continue
                    first_pd, first_pe, first_code, d_until = ps_start, ps_end, ps_name.upper().replace(" ","")[:12], d
                except: pass
        if presale_filter and not first_pd: return None
        pr      = e.get("priceRanges",[])
        ws      = (pr[0].get("min",0) if pr else 0) or 75
        rs      = round(ws*(1.0 if non_tf else 1.8),2)
        ml      = round(ws*(1.0 if non_tf else 1.6),2)
        atype   = "general"
        cl      = first_code.lower()
        if "amex" in cl:                                  atype = "amex_presale"
        elif any(x in cl for x in ["fan","club","vip"]):  atype = "fan_presale"
        elif first_pd:                                     atype = "fan_presale"
        return {
            "id":e.get("id",""),"artist":e.get("name","Unknown"),
            "venue":vname,"city":city,"category":genre.lower() or "pop",
            "event_date":edate,"days_out":dout,"venue_size":5000,
            "wholesale_price":ws,"resale_price":rs,"lowest_market_price":ml,
            "quantity":4,"sell_through_rate":0.78,"risk_score":0.25,"artist_score":75,
            "ticket_limit":4,"access_type":atype,
            "presale_date":first_pd,"presale_end":first_pe,"presale_code":first_code,
            "general_onsale":pub_start,"days_until_presale":d_until,
            "non_transferable":non_tf,"presales":ps_list,
            "url":e.get("url",""),
            "notes":"⛔ Face Value Exchange" if non_tf else (f"{len(ps_list)} presale windows" if ps_list else ""),
        }

    # ── Helpers ──────────────────────────────────────────────────────────────
    def fetch(self, url):
        req = urllib.request.Request(url, headers={"User-Agent":"JTCBrokers/2.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self._cors()
        self.send_header("Content-Length",len(body))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type,x-api-key,anthropic-version")

    def log_message(self,fmt,*args):
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {fmt%args}")

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"JTC Brokers Full Stack — port {PORT}")
    try:    server.serve_forever()
    except KeyboardInterrupt: print("Stopped.")
