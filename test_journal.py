"""Offline regression tests using Python's standard-library unittest runner."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generate_map import generate_map
from journal import (
    IMAGE_FIELDS, JournalError, MapillaryAPI, capture_devices, image_record,
    read_json, safe_id, setup_user, sync_sequences, write_json,
)


def image(image_id="1", sequence="seq_a", captured_at=1609459200000, pano=False):
    return {
        "id": image_id, "sequence": sequence, "captured_at": captured_at,
        "creator": {"id": "42", "username": "contributor"},
        "geometry": {"type": "Point", "coordinates": [24.9, 60.2]},
        "is_pano": pano, "make": "Camera", "model": "One",
    }


class FakeAPI:
    def __init__(self, images):
        self.images = images
        self.calls = []

    def pages(self, path, **params):
        self.calls.append((path, params))
        for item in self.images:
            if params["fields"] == "id,sequence":
                yield {key: item[key] for key in ("id", "sequence")}
            else:
                yield copy.deepcopy(item)

    def get(self, path, **params):
        self.calls.append((path, params))
        return copy.deepcopy(next(item for item in self.images if item["id"] == path))


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        write_json(self.data_dir / "config.json", {"user_id": "42", "username": "contributor"})

    def test_setup_is_only_performed_once(self):
        with patch("journal.MapillaryAPI", side_effect=AssertionError("API must not be called")):
            self.assertEqual(setup_user(self.data_dir)["user_id"], "42")

    def test_setup_resolves_creator_and_handles_empty_account(self):
        (self.data_dir / "config.json").unlink()
        result = setup_user(self.data_dir, username="contributor", api=FakeAPI([image()]))
        self.assertEqual(result, {"user_id": "42", "username": "contributor"})
        (self.data_dir / "config.json").unlink()
        with self.assertRaises(JournalError):
            setup_user(self.data_dir, username="contributor", api=FakeAPI([]))
        self.assertFalse((self.data_dir / "config.json").exists())

    def test_first_sync_groups_and_sorts_images(self):
        api = FakeAPI([image("2", captured_at=1609545600000, pano=True), image(), image("3", "seq_b")])
        self.assertEqual(sync_sequences(self.data_dir, api), {"updated_sequences": 2, "new_images": 3})
        feature = read_json(self.data_dir / "sequences" / "seq_a.geojson")["features"][0]
        self.assertEqual(feature["geometry"]["type"], "LineString")
        self.assertEqual([item["id"] for item in feature["properties"]["images"]], ["1", "2"])
        self.assertEqual(feature["properties"]["image_types"], ["360", "normal"])
        singleton = read_json(self.data_dir / "sequences" / "seq_b.geojson")["features"][0]
        self.assertEqual(singleton["geometry"]["type"], "Point")
        self.assertEqual(api.calls[0][1]["fields"], IMAGE_FIELDS)

    def test_repeat_sync_skips_detail_and_leaves_geojson_untouched(self):
        sync_sequences(self.data_dir, FakeAPI([image()]))
        path = self.data_dir / "sequences" / "seq_a.geojson"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        api = FakeAPI([image()])
        self.assertEqual(sync_sequences(self.data_dir, api)["new_images"], 0)
        self.assertEqual(api.calls, [("images", {
            "creator_username": "contributor", "fields": "id,sequence", "limit": 2000,
        })])
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_sync_appends_and_discovers_old_capture_dates(self):
        sync_sequences(self.data_dir, FakeAPI([image()]))
        api = FakeAPI([image(), image("2"), image("3", "old_sequence", 1500000000000)])
        self.assertEqual(sync_sequences(self.data_dir, api), {"updated_sequences": 2, "new_images": 2})
        self.assertEqual([call[0] for call in api.calls], ["images", "2", "3"])
        feature = read_json(self.data_dir / "sequences" / "seq_a.geojson")["features"][0]
        self.assertEqual(feature["properties"]["image_count"], 2)
        self.assertTrue((self.data_dir / "sequences" / "old_sequence.geojson").exists())

    def test_duplicate_images_are_not_appended_twice(self):
        self.assertEqual(sync_sequences(self.data_dir, FakeAPI([image(), image()]))["new_images"], 1)

    def test_failure_does_not_write_partial_discovery(self):
        class FailingAPI(FakeAPI):
            def pages(self, path, **params):
                yield image()
                raise JournalError("Failed page")
        with self.assertRaises(JournalError):
            sync_sequences(self.data_dir, FailingAPI([]))
        self.assertFalse((self.data_dir / "sequences").exists())
        self.assertFalse((self.data_dir / "sync.json").exists())
        self.assertFalse((self.data_dir / ".sync.lock").exists())
        self.assertEqual(sync_sequences(self.data_dir, FakeAPI([image()]))["new_images"], 1)

    def test_overlapping_sync_is_rejected_without_losing_images(self):
        owner = self

        class OverlappingAPI(FakeAPI):
            def pages(self, path, **params):
                with owner.assertRaises(JournalError):
                    sync_sequences(owner.data_dir, FakeAPI([image("2")]))
                yield from super().pages(path, **params)

        self.assertEqual(sync_sequences(self.data_dir, OverlappingAPI([image()]))["new_images"], 1)
        self.assertFalse((self.data_dir / ".sync.lock").exists())
        feature = read_json(self.data_dir / "sequences" / "seq_a.geojson")["features"][0]
        self.assertEqual([item["id"] for item in feature["properties"]["images"]], ["1"])

    def test_existing_lock_is_not_removed_by_a_rejected_sync(self):
        path = self.data_dir / ".sync.lock"
        path.write_text("another process\n", encoding="utf-8")
        with self.assertRaises(JournalError):
            sync_sequences(self.data_dir, FakeAPI([image()]))
        self.assertEqual(path.read_text(encoding="utf-8"), "another process\n")

    def test_creator_mismatch_does_not_write(self):
        wrong = image()
        wrong["creator"]["id"] = "999"
        with self.assertRaises(JournalError):
            sync_sequences(self.data_dir, FakeAPI([wrong]))
        self.assertFalse((self.data_dir / "sync.json").exists())

    def test_devices_classified_by_type_with_unknown_fallback(self):
        unknown = image("3", "seq_b")
        unknown.pop("make")
        unknown.pop("model")
        sync_sequences(self.data_dir, FakeAPI([image(), image("2", pano=True), unknown]))
        devices = capture_devices(self.data_dir)
        self.assertEqual(len(devices), 3)
        self.assertEqual({item["image_type"] for item in devices}, {"normal", "360"})
        self.assertEqual(next(item for item in devices if not item["make"])["device"], "Unknown")
        self.assertTrue(all(item["sequence_count"] == 1 for item in devices))

    def test_missing_and_invalid_metadata_remain_retryable(self):
        for key in ("geometry", "captured_at", "is_pano", "sequence"):
            invalid = image()
            invalid.pop(key)
            with self.subTest(key=key), self.assertRaises(JournalError):
                sync_sequences(self.data_dir, FakeAPI([invalid]))
        invalid = image()
        invalid["geometry"]["coordinates"] = [float("nan"), 0]
        with self.assertRaises(JournalError):
            image_record(invalid)

    def test_identifiers_cannot_escape_sequence_directory(self):
        for value in ("../outside", "/tmp/file", "", "id?access_token=value"):
            with self.subTest(value=value), self.assertRaises(JournalError):
                safe_id(value)

    def test_html_embeds_data_without_script_injection(self):
        malicious = image()
        malicious["model"] = "</script><script>alert(1)</script>"
        sync_sequences(self.data_dir, FakeAPI([malicious]))
        output = generate_map(self.data_dir)
        html = output.read_text(encoding="utf-8")
        self.assertNotIn("__JOURNAL_DATA__", html)
        self.assertNotIn(malicious["model"], html)
        embedded = html.split('<script id="journal-data" type="application/json">')[1].split("</script>")[0]
        payload = json.loads(embedded)
        self.assertEqual(payload["sequences"][0]["properties"]["images"][0]["model"], malicious["model"])
        self.assertEqual(generate_map(self.data_dir), output)

    def test_empty_journal_generates_map_and_device_catalog(self):
        self.assertEqual(capture_devices(self.data_dir), [])
        self.assertTrue(generate_map(self.data_dir).exists())

    def test_offline_manager_commands(self):
        for command in ("devices", "map"):
            result = subprocess.run([
                sys.executable, str(Path(__file__).with_name("manager.py")),
                command, "--data-dir", str(self.data_dir),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class APITests(unittest.TestCase):
    def test_pagination_follows_next_until_exhausted(self):
        api = MapillaryAPI(token="test-placeholder")
        with patch.object(api, "get", side_effect=[
            {"data": [{"id": "1"}], "paging": {"next": "https://graph.mapillary.com/images?after=next"}},
            {"data": [{"id": "2"}]},
        ]) as get:
            self.assertEqual(list(api.pages("images", limit=2000)), [{"id": "1"}, {"id": "2"}])
            self.assertEqual(get.call_args_list[1].args, ("https://graph.mapillary.com/images?after=next",))
            self.assertEqual(get.call_args_list[1].kwargs, {})

    def test_untrusted_pagination_urls_are_rejected_before_network(self):
        api = MapillaryAPI(token="test-placeholder")
        for url in ("https://evil.example/images", "http://graph.mapillary.com/images",
                    "https://graph.mapillary.com@evil.example/images"):
            with self.subTest(url=url), patch.object(api.opener, "open") as request:
                with self.assertRaises(JournalError):
                    api.get(url)
                request.assert_not_called()

    def test_repeated_cursor_and_missing_data_raise(self):
        api = MapillaryAPI(token="test-placeholder")
        with patch.object(api, "get", return_value={"data": [], "paging": {"next": "images?after=same"}}):
            with self.assertRaises(JournalError):
                list(api.pages("images"))
        with patch.object(api, "get", return_value={}):
            with self.assertRaises(JournalError):
                list(api.pages("images"))


if __name__ == "__main__":
    unittest.main()
