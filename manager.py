"""Manage setup, sync, device discovery, and static-map generation."""

import argparse
import json
from pathlib import Path
from journal import capture_devices, run_cli, setup_user, sync_sequences
from generate_map import generate_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "sync", "devices", "map", "update"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS)
        if name in ("setup", "update"):
            command.add_argument("--username")
            command.add_argument("--user-id")
        if name in ("map", "update"):
            command.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command in ("setup", "update"):
        print(json.dumps(setup_user(args.data_dir, args.username, args.user_id), indent=2))
    if args.command in ("sync", "update"):
        print(json.dumps(sync_sequences(args.data_dir), indent=2))
    if args.command in ("devices", "update"):
        print(json.dumps(capture_devices(args.data_dir), indent=2))
    if args.command in ("map", "update"):
        print(generate_map(args.data_dir, args.output))


if __name__ == "__main__":
    run_cli(main)
