"""The ``mds-server`` command line. Commands are specified in INTERFACES.md section 3."""

import argparse

from mds import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mds-server", description=__doc__)
    parser.add_argument("--version", action="version", version=f"mds-server {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
