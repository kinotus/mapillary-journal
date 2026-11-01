"""Shared storage and metadata-only Mapillary API operations."""

import argparse
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_ROOT = "https://graph.mapillary.com"
IMAGE_FIELDS = "id,creator,sequence,captured_at,geometry,is_pano,make,model"


class JournalError(Exception):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MapillaryAPI:
    def __init__(self, token=None):
        self.token = token or os.environ.get("MAPILLARY_ACCESS_TOKEN")
        if not self.token:
            raise JournalError("Set MAPILLARY_ACCESS_TOKEN to a Mapillary access token.")
        self.opener = build_opener(NoRedirects())

    def get(self, path, **params):
        url = urljoin(API_ROOT + "/", path)
        if params:
            url += "?" + urlencode(params)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "graph.mapillary.com"
                or parsed.fragment):
            raise JournalError("Refusing an untrusted API pagination URL.")
        request = Request(url, headers={"Authorization": "OAuth " + self.token})
        for attempt in range(4):
            try:
                with self.opener.open(request, timeout=60) as response:
                    result = json.load(response)
                if not isinstance(result, dict) or "error" in result:
                    raise JournalError("Mapillary returned an invalid API response.")
                return result
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise JournalError(
                    f"Mapillary HTTP {exc.code}; check token permissions and user configuration."
                ) from None
            except (URLError, ValueError):
                raise JournalError("Unable to read Mapillary API response; check connectivity.") from None

    def pages(self, path, **params):
        seen = set()
        while path:
            page = self.get(path, **params)
            rows = page.get("data")
            if not isinstance(rows, list):
                raise JournalError("Mapillary response is missing its data list.")
            yield from rows
            path = page.get("paging", {}).get("next")
            params = {}
            if path:
                if path in seen:
                    raise JournalError("Mapillary returned a repeated pagination cursor.")
                seen.add(path)


def read_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            name = handle.name
            handle.write(text)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def safe_id(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise JournalError("Mapillary returned an invalid identifier.")
    return value


def config(data_dir):
    path = Path(data_dir) / "config.json"
    if not path.exists():
        raise JournalError("Run setup_user.py (or manager.py setup) first.")
    return read_json(path)


def setup_user(data_dir, username=None, user_id=None, api=None):
    path = Path(data_dir) / "config.json"
    if path.exists():
        return read_json(path)
    api = api or MapillaryAPI()
    if user_id:
        user = api.get(safe_id(user_id), fields="id,username")
    elif username:
        rows = api.pages("images", creator_username=username, fields="id,creator", limit=1)
        image = next(rows, None)
        if not image or not image.get("creator"):
            raise JournalError("No public images found. Supply --user-id for an empty account.")
        user = image["creator"]
        if user.get("username", "").casefold() != username.casefold():
            raise JournalError("The API returned a different creator.")
    else:
        user = api.get("me", fields="id,username")
    if not user.get("id") or not user.get("username"):
        raise JournalError("User lookup did not return an ID and username.")
    if username and user["username"].casefold() != username.casefold():
        raise JournalError("The supplied user ID does not match the username.")
    result = {"user_id": safe_id(user["id"]), "username": user["username"]}
    write_json(path, result)
    return result


def image_record(image):
    geometry = image.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    captured_at = image.get("captured_at")
    if (geometry.get("type") != "Point" or not isinstance(coordinates, list)
            or len(coordinates) < 2
            or any(isinstance(n, bool) or not isinstance(n, (int, float))
                   or not math.isfinite(n) for n in coordinates[:2])
            or not -180 <= coordinates[0] <= 180
            or not -90 <= coordinates[1] <= 90
            or isinstance(captured_at, bool)
            or not isinstance(captured_at, (int, float))
            or not math.isfinite(captured_at) or captured_at < 0):
        raise JournalError("Image metadata is missing valid location or capture time; retry sync.")
    if not isinstance(image.get("is_pano"), bool):
        raise JournalError("Image metadata is missing its panorama classification; retry sync.")
    make = str(image.get("make") or "").strip()
    model = str(image.get("model") or "").strip()
    return {
        "id": safe_id(image["id"]),
        "coordinates": coordinates[:2],
        "captured_at": captured_at,
        "image_type": "360" if image["is_pano"] else "normal",
        "device": " ".join(part for part in (make, model) if part) or "Unknown",
        "make": make,
        "model": model,
    }


def sequence_feature(sequence_id, images):
    images = sorted(images, key=lambda item: (item["captured_at"], item["id"]))
    coordinates = [item["coordinates"] for item in images]
    geometry = ({"type": "LineString", "coordinates": coordinates} if len(images) > 1
                else {"type": "Point", "coordinates": coordinates[0]})
    return {
        "type": "Feature", "id": sequence_id, "geometry": geometry,
        "properties": {
            "sequence_id": sequence_id,
            "start_captured_at": images[0]["captured_at"],
            "end_captured_at": images[-1]["captured_at"],
            "image_count": len(images),
            "devices": sorted({item["device"] for item in images}),
            "image_types": sorted({item["image_type"] for item in images}),
            "mapillary_url": "https://www.mapillary.com/app/?pKey=" + images[0]["id"] + "&focus=photo",
            "images": images,
        },
    }


def load_sequences(data_dir):
    directory = Path(data_dir) / "sequences"
    return [read_json(path)["features"][0] for path in sorted(directory.glob("*.geojson"))]


def sync_sequences(data_dir, api=None):
    user = config(data_dir)
    api = api or MapillaryAPI()
    existing = load_sequences(data_dir)
    sequences = {
        feature["id"]: {image["id"]: image for image in feature["properties"]["images"]}
        for feature in existing
    }
    known = {image_id for images in sequences.values() for image_id in images}
    changed = set()
    added = 0
    fields = "id,sequence" if known else IMAGE_FIELDS
    for image in api.pages("images", creator_username=user["username"], fields=fields, limit=2000):
        image_id = safe_id(image["id"])
        if image_id in known:
            continue
        if not image.get("sequence"):
            raise JournalError("Image has no sequence yet; retry after Mapillary processing completes.")
        if known and fields != IMAGE_FIELDS:
            image = api.get(image_id, fields=IMAGE_FIELDS)
        creator = image.get("creator") or {}
        if str(creator.get("id")) != user["user_id"]:
            raise JournalError("Image creator does not match the configured user.")
        sequence_id = safe_id(image["sequence"])
        record = image_record(image)
        sequences.setdefault(sequence_id, {})[record["id"]] = record
        known.add(record["id"])
        changed.add(sequence_id)
        added += 1
    # Persist only after the entire discovery succeeds, so failed pages cannot lose images.
    for sequence_id in sorted(changed):
        write_json(Path(data_dir) / "sequences" / (sequence_id + ".geojson"), {
            "type": "FeatureCollection",
            "features": [sequence_feature(sequence_id, list(sequences[sequence_id].values()))],
        })
    write_json(Path(data_dir) / "sync.json", {
        "last_successful_sync": datetime.now(timezone.utc).isoformat(),
        "sequence_count": len(sequences), "image_count": len(known),
    })
    return {"updated_sequences": len(changed), "new_images": added}


def capture_devices(data_dir):
    devices = {}
    for feature in load_sequences(data_dir):
        for image in feature["properties"]["images"]:
            key = (image["make"], image["model"], image["image_type"])
            item = devices.setdefault(key, {
                "device": image["device"], "make": key[0], "model": key[1],
                "image_type": key[2], "image_count": 0, "sequence_ids": set(),
            })
            item["image_count"] += 1
            item["sequence_ids"].add(feature["id"])
    result = []
    for key in sorted(devices):
        item = devices[key]
        item["sequence_ids"] = sorted(item["sequence_ids"])
        item["sequence_count"] = len(item["sequence_ids"])
        result.append(item)
    write_json(Path(data_dir) / "devices.json", result)
    return result


def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--data-dir", type=Path, default=Path("data"),
                        help="Local journal directory (default: data)")
    return result


def run_cli(function):
    try:
        function()
    except (JournalError, OSError, ValueError, KeyError, TypeError) as exc:
        # Never expose request URLs, which can include access tokens in pagination.
        if isinstance(exc, JournalError):
            message = str(exc)
        else:
            message = "Invalid or inaccessible local/API data. Check configuration and retry."
        raise SystemExit(message) from None
