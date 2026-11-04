import argparse
import sqlite3
from pathlib import Path

from dress_knowledge import import_segments


def main():
    parser = argparse.ArgumentParser(description="Import local dress-design JSONL into Show Gown Studio SQLite")
    parser.add_argument("jsonl", type=Path, help="Path to normalized segments.jsonl")
    parser.add_argument("--db", type=Path, default=Path("show_gown_studio.db"))
    parser.add_argument("--replace", action="store_true", help="Replace existing dress knowledge records")
    args = parser.parse_args()
    with sqlite3.connect(args.db) as connection:
        count = import_segments(connection, args.jsonl, replace=args.replace)
    print(f"DRESS_DATA_IMPORTED records={count} db={args.db}")


if __name__ == "__main__":
    main()
