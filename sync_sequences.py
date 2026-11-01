"""Save user sequences as GeoJSON, fetching only unseen image metadata on later runs."""

import json
from journal import parser, run_cli, sync_sequences


def main():
    args = parser(__doc__).parse_args()
    print(json.dumps(sync_sequences(args.data_dir), indent=2))


if __name__ == "__main__":
    run_cli(main)
