#!/usr/bin/env python3
"""Insert markers into asciinema recordings"""

import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="Insert markers into asciinema recordings")
    parser.add_argument("inp_file", help="Input asciinema file")
    parser.add_argument("marker_file", help="Markers file to insert")
    parser.add_argument("out_file", help="Output asciinema file")
    args = parser.parse_args()

    # Load the markers
    with open(args.marker_file) as fm:
        markers = [json.loads(line) for line in fm]
    markers.sort(key=lambda x: x[0])

    with open(args.inp_file) as fi, open(args.out_file, "w") as fo:
        for line in fi:
            record = json.loads(line)
            if len(markers) > 0 and isinstance(record, list) and record[0] > markers[0][0]:
                print(json.dumps(markers.pop(0)), file=fo)
            print(line, end="", file=fo)


if __name__ == "__main__":
    main()
