"""Check the two named cocotb test cases for the 8x4 adder baseline."""

import argparse
import sys
import xml.etree.ElementTree as ET

EXPECTED = [
    ("baseline_extra", "adder_vectors_and_pin_controls"),
    ("test", "test_project"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_xml")
    path = parser.parse_args().results_xml
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        sys.exit(f"Cannot read {path}: {error}")

    cases = sorted(
        (case.get("classname", ""), case.get("name", ""))
        for case in root.iter("testcase")
    )
    if cases != EXPECTED:
        sys.exit(f"Expected {EXPECTED} exactly once each. Found {cases}.")
    for tag in ("failure", "error", "skipped"):
        if next(root.iter(tag), None) is not None:
            sys.exit(f"Baseline test results contain <{tag}>.")

    print("Both baseline tests passed.")


if __name__ == "__main__":
    main()
