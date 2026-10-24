#!/usr/bin/env python3
"""
Spotify -> YouTube Music playlist importer (local web app).

Everything runs on YOUR computer. Your YouTube login never leaves it:
the server only listens on 127.0.0.1 and the login is held in memory
(plus a temp file that is deleted as soon as the import ends).

Setup:   pip install ytmusicapi
Run:     python spotify_to_ytmusic_app.py
Then a browser tab opens at http://127.0.0.1:8765 and walks you through it.
"""
import csv, io, json, os, re, sys, tempfile, threading, time, webbrowser
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from ytmusicapi import YTMusic
except ImportError:
    sys.exit("Missing dependency. Run:  pip install ytmusicapi")

PORT = int(os.environ.get("PORT", 8765))
PROGRESS_FILE = "ytm_import_progress.json"   # matches only, no credentials
MAX_BODY = 25 * 1024 * 1024

S = {"running": False, "stage": "Waiting to start", "done": 0, "total": 0,
     "log": [], "error": None, "url": None, "missing": [], "added": 0}
LOCK = threading.Lock()


class UserError(Exception):
    pass


def log(msg):
    with LOCK:
        S["log"].append(msg)
        S["log"] = S["log"][-150:]


def stage(msg):
    with LOCK:
        S["stage"] = msg


# ---------- parsing ----------
def parse_curl(text):
    """Extract headers from a 'Copy as cURL (bash)' command."""
    text = text.replace("\\\r\n", " ").replace("\\\n", " ")
    headers = {}
    for m in re.finditer(r"""(?:-H|--header)\s+(?:'([^']*)'|"([^"]*)")""", text):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        name, _, value = raw.partition(":")
        name = name.strip().lower()
        if name and not name.startswith(":"):
            headers[name] = value.strip()
    m = re.search(r"""(?:-b|--cookie)\s+(?:'([^']*)'|"([^"]*)")""", text)
    if m:
        headers["cookie"] = m.group(1) if m.group(1) is not None else m.group(2)
    for need in ("cookie", "authorization"):
        if need not in headers:
            raise UserError(
                f"The pasted cURL command has no '{need}' header. Copy the request again "
                "from music.youtube.com while signed in, using 'Copy as cURL (bash)'.")
    headers.setdefault("x-goog-authuser", "0")
    headers.setdefault("origin", "https://music.youtube.com")
    headers.setdefault("content-type", "application/json")
    headers.pop("accept-encoding", None)
    headers.pop("content-length", None)
    return headers


def parse_csv(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        raise UserError("The CSV is empty.")
    for col in ("Track Name", "Artist Name(s)"):
        if col not in rows[0]:
            raise UserError(f"CSV is missing the '{col}' column. Export it with exportify.net.")
    for i, r in enumerate(rows):
        r["_key"] = r.get("Track URI") or f"row{i}"
    return rows


# ---------- matching ----------
def norm(s):
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s.lower())
    return re.sub(r"[^\w ]", "", s).strip()


def score(track, artists, dur_s, hit):
    t = SequenceMatcher(None, norm(track), norm(hit["title"])).ratio()
    hit_artists = " ".join(a["name"] for a in hit.get("artists") or []).lower()
    a = 1.0 if any(x.lower() in hit_artists for x in artists) else 0.0
    d = 0.0
    if dur_s and hit.get("duration_seconds"):
        diff = abs(hit["duration_seconds"] - dur_s)
        d = 1.0 if diff <= 3 else 0.5 if diff <= 8 else 0.0
    return 0.5 * t + 0.3 * a + 0.2 * d


def retry(fn, tries=6):
    for n in range(tries):
        try:
            return fn()
        except Exception as e:
            wait = min(5 * 2 ** n, 120)
            log(f"   YouTube error ({str(e)[:60]}), retrying in {wait}s")
            time.sleep(wait)
    raise UserError("YouTube Music keeps returning errors. Progress is saved - "
                    "wait a while and press Start again.")


def find(yt, track, artists, dur_s):
    best, best_score = None, 0
    for q in (f"{track} {artists[0]}", f"{track} {' '.join(artists)}"):
        for hit in retry(lambda: yt.search(q, filter="songs", limit=5)):
            if hit.get("videoId"):
                s = score(track, artists, dur_s, hit)
                if s > best_score:
                    best, best_score = hit["videoId"], s
        if best_score >= 0.85:
            break
    return best if best_score >= 0.6 else None


# ---------- the job ----------
def load_progress():
    try:
        return json.load(open(PROGRESS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_progress(d):
    json.dump(d, open(PROGRESS_FILE, "w", encoding="utf-8"))


def job(csv_text, curl_text, name):
    tmp, cache = None, load_progress()
    try:
        rows = parse_csv(csv_text)
        headers = parse_curl(curl_text)
        fd, tmp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(headers, f)
        yt = YTMusic(tmp)

        stage("Checking your YouTube Music login")
        try:
            yt.get_library_playlists(limit=1)
        except Exception:
            raise UserError("YouTube Music did not accept the login. Open music.youtube.com, "
                            "make sure you are signed in, refresh, and copy a fresh cURL command.")

        with LOCK:
            S.update(total=len(rows), done=0)
        stage("Matching songs")
        for i, r in enumerate(rows, 1):
            key = r["_key"]
            if key not in cache:
                artists = [a.strip() for a in r["Artist Name(s)"].split(";") if a.strip()] or [""]
                try:
                    dur = int(r.get("Duration (ms)") or 0) / 1000
                except ValueError:
                    dur = 0
                cache[key] = find(yt, r["Track Name"], artists, dur)
                log(f"{'OK  ' if cache[key] else 'MISS'} {r['Track Name']} - {artists[0]}")
                time.sleep(1.0)
                if i % 25 == 0:
                    save_progress(cache)
            with LOCK:
                S["done"] = i
        save_progress(cache)

        ids, seen = [], set()
        for r in rows:
            v = cache.get(r["_key"])
            if v and v not in seen:
                ids.append(v)
                seen.add(v)
        if not ids:
            raise UserError("No songs could be matched.")

        stage("Creating playlist")
        pid = retry(lambda: yt.create_playlist(name or "Spotify import", "Imported from Spotify", "PRIVATE"))
        for i in range(0, len(ids), 100):
            stage(f"Adding songs to the playlist ({min(i + 100, len(ids))}/{len(ids)})")
            retry(lambda: yt.add_playlist_items(pid, ids[i:i + 100], duplicates=False))
            time.sleep(1)

        with LOCK:
            S["added"] = len(ids)
            S["missing"] = [{"track": r["Track Name"], "artists": r["Artist Name(s)"],
                             "album": r.get("Album Name", "")} for r in rows if not cache.get(r["_key"])]
            S["url"] = f"https://music.youtube.com/playlist?list={pid}"
        try:
            os.remove(PROGRESS_FILE)
        except OSError:
            pass
        stage("Done")
    except UserError as e:
        with LOCK:
            S["error"] = str(e)
        stage("Stopped")
    except Exception as e:
        with LOCK:
            S["error"] = f"Unexpected error: {e}"
        stage("Stopped")
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        with LOCK:
            S["running"] = False


# ---------- web ui ----------
PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Spotify to YouTube Music</title>
<style>
:root{--bg:#fff;--fg:#1b1b1f;--mut:#666;--card:#f4f4f7;--acc:#d6204b;--bd:#d9d9e0}
@media(prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#ececf1;--mut:#a0a0ab;--card:#222228;--bd:#383840}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:.2em 0}.sub{color:var(--mut);margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin:14px 0}
.card h2{font-size:1rem;margin:0 0 6px}ol,ul{margin:6px 0;padding-left:20px}
textarea,input[type=text]{width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--bd);border-radius:8px;background:var(--bg);color:var(--fg);font:13px monospace}
input[type=text]{font:15px system-ui}textarea{height:90px}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 20px;font-size:1rem;cursor:pointer}
button:disabled{opacity:.5;cursor:default}a{color:var(--acc)}
.bar{height:10px;background:var(--bd);border-radius:6px;overflow:hidden;margin:8px 0}
.bar>div{height:100%;width:0;background:var(--acc);transition:width .3s}
pre{background:var(--bg);border:1px solid var(--bd);border-radius:8px;padding:8px;max-height:220px;overflow:auto;font-size:12px;margin:8px 0 0}
.err{color:#d6204b;font-weight:600}.mut{color:var(--mut);font-size:.9rem}
</style></head><body><main>
<h1>Spotify &rarr; YouTube Music</h1>
<div class="sub">Runs entirely on your computer. Nothing is uploaded anywhere except to YouTube Music itself.</div>

<div class="card"><h2>1. Your Spotify playlist</h2>
<ol><li>Go to <a href="https://exportify.net" target="_blank" rel="noopener">exportify.net</a>, log in with Spotify and export the playlist as CSV.</li>
<li>Choose that CSV file here:</li></ol><input type="file" id="csv" accept=".csv"></div>

<div class="card"><h2>2. Your YouTube Music login</h2>
<ol><li>Open <b>music.youtube.com</b> while signed in, press <b>F12</b> &rarr; <b>Network</b> tab, type <code>browse</code> in the filter, click around the page.</li>
<li>Right-click a <code>browse?prettyPrint=false</code> request &rarr; <b>Copy</b> &rarr; <b>Copy as cURL (bash)</b>.</li>
<li>Paste it here:</li></ol><textarea id="curl" placeholder="curl 'https://music.youtube.com/youtubei/v1/browse?...' -H '...'"></textarea>
<div class="mut">This contains your login session. It stays on this computer and is forgotten when the import ends.</div></div>

<div class="card"><h2>3. Playlist name</h2><input type="text" id="name" value="Spotify import"></div>
<button id="go">Start import</button>

<div class="card" id="status" style="display:none"><h2 id="stage"></h2>
<div class="bar"><div id="fill"></div></div><div class="mut" id="count"></div>
<div class="err" id="err"></div><div id="result"></div><pre id="log"></pre></div>

<script>
const $=id=>document.getElementById(id);let timer=null;
$('go').onclick=async()=>{
 const f=$('csv').files[0];
 if(!f){alert('Choose your CSV file first');return}
 if(!$('curl').value.trim()){alert('Paste the cURL command first');return}
 $('go').disabled=true;$('status').style.display='block';$('err').textContent='';$('result').innerHTML='';
 const r=await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({csv:await f.text(),curl:$('curl').value,name:$('name').value})});
 const j=await r.json();
 if(!r.ok){$('err').textContent=j.error;$('go').disabled=false;return}
 timer=setInterval(poll,1500);poll();
};
async function poll(){
 const s=await (await fetch('/status')).json();
 $('stage').textContent=s.stage;
 $('fill').style.width=(s.total?100*s.done/s.total:0)+'%';
 $('count').textContent=s.total?`${s.done} / ${s.total} songs checked`:'';
 $('log').textContent=s.log.join('\n');$('log').scrollTop=1e9;
 if(!s.running){clearInterval(timer);$('go').disabled=false;
  if(s.error)$('err').textContent=s.error+' (Your matches are saved, so pressing Start again will not repeat the searching.)';
  if(s.url){$('result').innerHTML=`<p><b>Done.</b> Added ${s.added} songs. <a href="${s.url}" target="_blank" rel="noopener">Open the playlist</a></p>`+
   (s.missing.length?`<p>${s.missing.length} songs could not be matched. <a href="/missing.csv">Download the list</a>.</p>`:'');}}
}
</script></main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/status":
            with LOCK:
                self._send(200, json.dumps(S))
        elif self.path == "/missing.csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["Track", "Artists", "Album"])
            with LOCK:
                for m in S["missing"]:
                    w.writerow([m["track"], m["artists"], m["album"]])
            self._send(200, buf.getvalue(), "text/csv; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="not_found.csv"'})
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path != "/start":
            return self._send(404, "{}")
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._send(413, json.dumps({"error": "File too large."}))
        try:
            d = json.loads(self.rfile.read(n))
            parse_csv(d["csv"])
            parse_curl(d["curl"])
        except UserError as e:
            return self._send(400, json.dumps({"error": str(e)}))
        except Exception:
            return self._send(400, json.dumps({"error": "Bad request."}))
        with LOCK:
            if S["running"]:
                return self._send(409, json.dumps({"error": "An import is already running."}))
            S.update(running=True, stage="Starting", done=0, total=0, log=[], error=None,
                     url=None, missing=[], added=0)
        threading.Thread(target=job, args=(d["csv"], d["curl"], d.get("name", "")), daemon=True).start()
        self._send(200, "{}")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Open {url} in your browser (Ctrl+C to quit)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
