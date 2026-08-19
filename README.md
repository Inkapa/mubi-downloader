# Mubi Downloader

Downloads movies from [Mubi](https://mubi.com) by extracting your authenticated
session from a local browser, retrieving the DRM-protected stream, and decrypting
it with content keys obtained through a Widevine/PlayReady CDM key service.

> **Disclaimer.** This tool is for personal, private use only. Downloading
> DRM-protected content may violate Mubi's Terms of Service and the laws of your
> jurisdiction. You are responsible for how you use it.

## Features

- **Modern Mubi auth** — works with the current `lt` session cookie (the legacy
  `authToken`/`dtCustomData` cookies no longer exist on Mubi).
- **Multi-browser** — Chrome, Firefox and Edge; works on Windows, Linux and WSL2.
- **Geo-aware** — honors the detected country; films restricted to other regions
  can be fetched through a VPN (see [Geo-restricted films](#geo-restricted-films)).
- **DRM key extraction** — obtains Widevine/PlayReady content keys via the
  [cdmpool.xyz](https://cdmpool.xyz) API, with automatic **PlayReady fallback**
  for providers that reject Widevine devices (as Mubi's DRMtoday setup does).
- **Full muxing** — video + audio are decrypted and merged with subtitles.
- **Manual entry fallback** — if the film search backend is unreachable, the film
  ID/title/year can be entered manually.
- **Interactive and CLI-driven** — pick a browser at runtime or pass it as an
  argument.

## Requirements

- **Python 3.8+**
- **`N_m3u8DL-RE`** — downloads the encrypted HLS/DASH stream
  ([releases](https://github.com/nilaoda/N_m3u8DL-RE/releases))
- **`shaka-packager`** — decrypts the stream with the content keys
  ([releases](https://github.com/shaka-project/shaka-packager/releases))
- **`ffmpeg`** — merges video/audio/subtitles into a final container
- A valid **Mubi subscription**, logged in from one of the supported browsers
- A **cdmpool.xyz account + API token** (free tier allows 5 key extractions/day)

> The `N_m3u8DL-RE`, `shaka-packager` and `ffmpeg` binaries must be on your
> `PATH`.

## Installation

```bash
git clone https://github.com/Inkapa/mubi-downloader3.git
cd mubi-downloader3
chmod +x mubi-downloader.sh
./mubi-downloader.sh
```

The wrapper script creates a virtual environment, installs the Python
dependencies and runs the tool.

### Configuration

Set your key-service token and, optionally, override defaults via environment
variables:

| Variable                     | Purpose                                             | Default                          |
| ---------------------------- | --------------------------------------------------- | -------------------------------- |
| `CDMPOOL_TOKEN`              | cdmpool.xyz API token (required for key extraction) | —                                |
| `MUBI_DT_CUSTOM_DATA`        | Override the DRMtoday `dt-custom-data` header       | auto-built from your session     |
| `MUBI_LICENSE_URL`           | Widevine license URL                                | `https://lic.drmtoday.com/license-proxy-widevine/cenc/` |
| `MUBI_PLAYREADY_LICENSE_URL` | PlayReady license URL (fallback)                    | `https://lic.drmtoday.com/license-proxy-headerauth/drmtoday/RightsManager.asmx` |

Export the token in your shell before running:

```bash
export CDMPOOL_TOKEN="your-cdmpool-api-token"
```

## Usage

```bash
./mubi-downloader.sh [options]
```

### Options

| Option                | Description                                        |
| --------------------- | -------------------------------------------------- |
| `-b, --browser NAME`  | Browser to read the Mubi session from (`chrome`, `firefox`, `edge`) |
| `-o, --output DIR`    | Output directory (default: `download`)             |
| `--debug`             | Enable verbose/debug logging                       |

If no browser is given, an interactive menu is shown.

### Examples

```bash
# Interactive browser selection
./mubi-downloader.sh

# Use Firefox and a custom output directory
./mubi-downloader.sh --browser firefox --output my_movies

# Debug logging
./mubi-downloader.sh --browser chrome --debug
```

### Flow

1. You are asked for the movie title. If the search backend
   (`whatsonmubi.com`) is unavailable, you can enter the film ID, title and year
   manually (the film ID is the numeric part of the Mubi URL,
   e.g. `https://mubi.com/films/445143` → `445143`).
2. The tool checks the film's availability in your location.
3. Your Mubi session is read from the selected browser and used to obtain the
   DRM-protected stream URL.
4. The content key is requested from cdmpool.xyz (Widevine first, then
   PlayReady).
5. The stream is downloaded, decrypted and muxed into
   `download/<Title> (<Year>)/`.

## How authentication works

Mubi's current web app authenticates API calls with the `lt` cookie, used as a
`Bearer` token. This tool reads your browser's cookie database (Chrome/Edge use
an encrypted SQLite store; Firefox stores cookies in plaintext SQLite) and:

1. Locates the Mubi session (`lt`) cookie.
2. Builds the `dt-custom-data` header as `base64({userId, sessionId, merchant})`
   where `sessionId` is the `lt` token and `merchant` is `mubi`.
3. Calls the Mubi API (`/v3/films/{id}/viewing/secure_url`) to obtain the signed
   stream URL.

If cookie extraction fails, the tool falls back to an interactive manual flow
(cookies.txt or manual token entry).

## How DRM key extraction works

The stream is Widevine/PlayReady protected (DRMtoday). Keys are obtained through
the cdmpool.xyz extraction API:

1. The Widevine KID is read from the manifest (`default_KID`).
2. A standard Widevine PSSH box is built from the KID.
3. A license request is sent to cdmpool.xyz.
4. Mubi's DRMtoday setup rejects cdmpool's Widevine device, so the tool
   automatically retries via **PlayReady**, using the PlayReady PSSH embedded in
   the video init segment. This yields the same content keys.
5. `shaka-packager` decrypts the downloaded segments with
   `key_id=<kid>:key=<key>`.

## WSL2 support

When run inside WSL2 the tool detects the environment and reads browser cookies
from the Windows side (`/mnt/c/Users/<user>/AppData/...`), so the browser
session on Windows is used automatically.

## Geo-restricted films

Mubi decides film availability from your **IP address**. If a film is not
available in your country (the tool reports *"This film is not currently
authorized in your location"*), connect to a VPN in a country where the film is
streaming and re-run the tool.

Note that some key-service providers block requests originating from VPN/datacenter
IPs (cdmpool's free tier does). If you hit that, disable the VPN for the key
extraction step (the signed stream URL and decryption key are not
IP-locked), then re-enable it for the Mubi API calls.

## Troubleshooting

| Problem                                   | Fix                                                                  |
| ----------------------------------------- | -------------------------------------------------------------------- |
| `'EnvironmentDetector' object has no attribute ...` | Update the repo; this was fixed in the current version.     |
| `No cookies found`                        | Log in to Mubi in the selected browser, then try again.              |
| `Film not authorized in your location`    | The film is not available from your IP; use a VPN in a supported country. |
| `Key extraction failed` / `E_QUOTA_REACHED` | Set a valid `CDMPOOL_TOKEN`; upgrade or wait for the daily quota reset. |
| `Only VIP members are allowed to use a VPN` | Disable your VPN for the key-extraction step.                       |
| `ffmpeg not found`                        | Install `ffmpeg` and add it to `PATH`.                               |
| `N_m3u8DL-RE` / `shaka-packager` not found | Install both binaries and add them to `PATH`.                        |
| Search fails / `Enter movie name` loops   | `whatsonmubi.com` may be unreachable; use manual entry with the film ID. |

## Project layout

```
src/mubi_downloader/
├── __main__.py          CLI entry point
├── mubi_downloader.py   Movie search + download/decrypt pipeline
├── auth_manager.py      Browser cookie extraction + header generation
├── environment.py       OS/WSL/browser-path detection
└── drm.py               Widevine/PlayReady key retrieval via cdmpool.xyz
```

## Contributing

Issues and pull requests are welcome.
