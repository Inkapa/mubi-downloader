#!/usr/bin/env python3
"""Widevine key retrieval for Mubi using the cdmpool.xyz extraction API."""
import os
import json
import base64
import struct
import logging
import requests

logger = logging.getLogger('DRM')

CDMPOOL_API = 'https://cdmpool.xyz/api/extract'
DEFAULT_LICENSE_URL = 'https://lic.drmtoday.com/license-proxy-widevine/cenc/'
DEFAULT_PLAYREADY_LICENSE_URL = 'https://lic.drmtoday.com/license-proxy-headerauth/drmtoday/RightsManager.asmx'

WIDEVINE_SYSTEM_ID = bytes.fromhex('edef8ba979d64acea3c827dcd51d21ed')


def build_pssh(key_id: str) -> str:
    """Build a standard Widevine PSSH box from a key ID.

    Args:
        key_id (str): Key ID, either with or without dashes.

    Returns:
        str: base64-encoded PSSH box.
    """
    kid = bytes.fromhex(key_id.replace('-', ''))
    payload = struct.pack('>I', 0) + struct.pack('>I', 1) + kid
    box = b'pssh' + struct.pack('>I', 0) + WIDEVINE_SYSTEM_ID + struct.pack('>I', len(payload)) + payload
    return base64.b64encode(struct.pack('>I', len(box)) + box).decode('utf-8')


def fetch_decryption_key(pssh: str, dt_custom_data: str, license_url: str = None, api_token: str = None,
                         playready_pssh: str = None) -> str:
    """Request content keys from the cdmpool.xyz extraction API.

    Widevine is attempted first; if the license server rejects the Widevine
    CDM device (as Mubi's DRMtoday setup does), it falls back to PlayReady
    using the PlayReady PSSH from the content's init segment.

    Args:
        pssh (str): base64-encoded Widevine PSSH box.
        dt_custom_data (str): base64-encoded DRMtoday custom data (userId/sessionId/merchant).
        license_url (str, optional): DRMtoday Widevine license URL.
        api_token (str, optional): cdmpool.xyz API token. Falls back to the CDMPOOL_TOKEN env var.
        playready_pssh (str, optional): base64-encoded PlayReady PSSH box for fallback.

    Returns:
        str: Decryption key in shaka-packager "key_id=...:key=..." format.

    Raises:
        ValueError: If no keys could be obtained.
    """
    token = api_token or os.environ.get('CDMPOOL_TOKEN')
    if not token:
        raise ValueError("No cdmpool.xyz API token provided (set CDMPOOL_TOKEN)")

    url = license_url or os.environ.get('MUBI_LICENSE_URL') or DEFAULT_LICENSE_URL

    widevine_payload = {
        'token': token,
        'drm': 'widevine',
        'pssh': pssh,
        'license_url': url,
        'headers': {'dt-custom-data': dt_custom_data},
    }

    logger.debug(f"Requesting Widevine keys from cdmpool using license URL: {url}")
    response = requests.post(CDMPOOL_API, json=widevine_payload, timeout=90)
    logger.debug(f"cdmpool response status: {response.status_code}")

    keys = _extract_keys(response)
    if keys:
        return keys[0]

    # Fall back to PlayReady using the PSSH from the content init segment
    if playready_pssh:
        playready_url = os.environ.get('MUBI_PLAYREADY_LICENSE_URL') or DEFAULT_PLAYREADY_LICENSE_URL
        playready_payload = {
            'token': token,
            'drm': 'playready',
            'pssh': playready_pssh,
            'license_url': playready_url,
            'headers': {'dt-custom-data': dt_custom_data},
        }
        logger.debug(f"Widevine rejected, trying PlayReady keys from: {playready_url}")
        response = requests.post(CDMPOOL_API, json=playready_payload, timeout=90)
        logger.debug(f"cdmpool PlayReady response status: {response.status_code}")
        keys = _extract_keys(response)
        if keys:
            return keys[0]

    data = _safe_json(response)
    hint = (data or {}).get('hint') or (data or {}).get('error_code') or 'unknown'
    raise ValueError(f"Key extraction failed: {hint}")


def _extract_keys(response) -> list:
    """Parse a cdmpool response into shaka-packager key strings."""
    try:
        data = response.json()
    except Exception:
        logger.debug(f"Non-JSON cdmpool response: {response.text[:300]}")
        return []
    if not data.get('ok') or not data.get('keys'):
        logger.debug(f"cdmpool response: {response.text[:500]}")
        return []
    keys = []
    for key in data['keys']:
        if 'kid' in key and 'key' in key:
            keys.append(f"key_id={key['kid']}:key={key['key']}")
    return keys


def _safe_json(response) -> dict:
    try:
        return response.json()
    except Exception:
        return {'hint': f"HTTP {response.status_code}"}
