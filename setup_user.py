"""Resolve and save a Mapillary account once."""

import json
from journal import parser, run_cli, setup_user


def main():
    args_parser = parser(__doc__)
    args_parser.add_argument("--username", help="Public Mapillary username")
    args_parser.add_argument("--user-id", help="Numeric Mapillary user ID")
    args = args_parser.parse_args()
    print(json.dumps(setup_user(args.data_dir, args.username, args.user_id), indent=2))


if __name__ == "__main__":
    run_cli(main)
