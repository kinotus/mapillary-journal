"""List unique capture devices, classified by 360/normal image type."""

import json
from journal import capture_devices, parser, run_cli


def main():
    args = parser(__doc__).parse_args()
    print(json.dumps(capture_devices(args.data_dir), indent=2))


if __name__ == "__main__":
    run_cli(main)
