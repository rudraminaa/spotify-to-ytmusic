# Spotify → YouTube Music

Move a Spotify playlist to YouTube Music. It's a small local web app that matches
each song by title, artist and duration, then builds a private playlist in your
YouTube Music account.

**Everything runs on your own computer.** Nothing is uploaded to any server except
your requests to YouTube Music itself.

## Features

- Simple browser UI with a progress bar and live log
- Matches songs using title similarity, artist and track length, which avoids most
  live, remix and cover versions
- Handles YouTube rate limits (HTTP 503) with automatic retries
- Checks your login before searching, so you don't find out about a bad login
  after 900 songs
- Saves progress, so a failed run can be restarted without repeating the searches
- Gives you a list of songs it couldn't find
- Creates a private playlist, keeping your original order

## Requirements

- Python 3.9 or newer
- A YouTube Music account

## Setup

```
pip install -r requirements.txt
python spotify_to_ytmusic_app.py
```

A browser tab opens at `http://127.0.0.1:8765`.

## How to use

### 1. Export your Spotify playlist
Go to [exportify.net](https://exportify.net), log in with Spotify, and export the
playlist as a CSV. Liked Songs works too.

### 2. Copy your YouTube Music login
1. Open [music.youtube.com](https://music.youtube.com) while signed in.
2. Press **F12** to open DevTools and go to the **Network** tab.
3. Type `browse` in the filter box and click around the page until a request named
   `browse?prettyPrint=false` appears.
4. Right-click it, choose **Copy → Copy as cURL (bash)**.
5. Paste it into the app's text box.

### 3. Start
Pick the CSV, name the playlist, and press **Start import**. When it finishes you get
a link to the playlist and a download of any songs that weren't matched.

## Troubleshooting

| Problem | What to do |
|---|---|
| "YouTube Music did not accept the login" | Refresh music.youtube.com, make sure you're signed in, and copy a fresh cURL command. |
| "no 'cookie' header" | Use **Copy as cURL (bash)**, not "cmd" or "PowerShell", and copy from music.youtube.com, not youtube.com. |
| Repeated 503 errors | YouTube is rate limiting you. Wait 30 to 60 minutes and press Start again. Progress is saved. |
| Several Google accounts | Use a private/incognito window signed in with only the account you want. |

## Privacy and security

- The server only listens on `127.0.0.1`, so other devices can't reach it.
- Your login is held in memory and in a temporary file that is deleted when the import
  ends. It is never logged or stored by this app.
- The progress file (`ytm_import_progress.json`) contains only song matches, not
  credentials. It's deleted after a successful run.
- Your copied cURL command is effectively a key to your YouTube Music session. Don't
  share it. Signing out of YouTube Music invalidates it.

## Limitations

- YouTube Music has no official public API. This uses the unofficial
  [`ytmusicapi`](https://github.com/sigma67/ytmusicapi) library, so it can break
  whenever YouTube changes its internal endpoints.
- Matching isn't perfect. Expect a handful of missing or wrong versions, so skim the
  playlist afterwards.
- Heavy automated use may be rate limited, and automation may go against YouTube's
  Terms of Service. Use it for moving your own playlists, not as a service.

## Disclaimer

This project isn't affiliated with, endorsed by, or connected to Spotify, Google or
YouTube. Use at your own risk.

## License

MIT
