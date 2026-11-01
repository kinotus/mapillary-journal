"""Generate a static, metadata-only HTML map."""

import json
from pathlib import Path
from journal import config, load_sequences, parser, run_cli, write_text


def generate_map(data_dir, output=None):
    payload = json.dumps({
        "user": config(data_dir), "sequences": load_sequences(data_dir),
    }, ensure_ascii=False, allow_nan=False)
    # Data lives inside a script element; prevent metadata from closing that element.
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = Path(__file__).with_name("map.html").read_text(encoding="utf-8")
    output = Path(output) if output else Path(data_dir) / "index.html"
    write_text(output, template.replace("__JOURNAL_DATA__", payload))
    return output


def main():
    args_parser = parser(__doc__)
    args_parser.add_argument("--output", type=Path, help="HTML output (default: DATA_DIR/index.html)")
    args = args_parser.parse_args()
    print(generate_map(args.data_dir, args.output))


if __name__ == "__main__":
    run_cli(main)
