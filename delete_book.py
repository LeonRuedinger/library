
import csv
import json
import os
import re
import sys
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def norm_key(title, author):
    value = unicodedata.normalize(
        "NFKC", f"{title}|{author}"
    ).casefold()
    return re.sub(r"\s+", " ", value).strip()


def norm(value):
    value = unicodedata.normalize(
        "NFKD", (value or "").casefold().replace("ß", "ss")
    )
    value = "".join(
        c for c in value if not unicodedata.combining(c)
    )
    return re.sub(
        r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)
    ).strip()


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=columns, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def remove_book(book_id):
    ids_path = ROOT / "books_linked.ids.json"
    master_path = ROOT / "books.csv"
    linked_path = ROOT / "books_linked.csv"
    archive_path = ROOT / "books_linked.archive.json"
    state_path = ROOT / "books_linked.csv.state.json"
    review_path = ROOT / "review.csv"

    # Resolve the stable ID to its title/author key.
    with open(ids_path, encoding="utf-8") as f:
        id_data = json.load(f)

    ids = id_data.get("ids", {})
    matches = [key for key, value in ids.items() if value == book_id]

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one ID mapping for {book_id}, "
            f"found {len(matches)}."
        )

    key = matches[0]

    # Load and validate the authoritative master CSV.
    master_rows, master_cols = read_csv(master_path)

    matching_master = [
        row for row in master_rows
        if norm_key(row.get("title", ""), row.get("author", "")) == key
    ]

    if len(matching_master) != 1:
        raise ValueError(
            f"Expected exactly one master CSV entry for {key}, "
            f"found {len(matching_master)}. Nothing deleted."
        )

    deleted_row = matching_master[0]

    title = deleted_row.get("title", "")
    author = deleted_row.get("author", "")
    remaining_master = [
        row for row in master_rows if row is not matching_master[0]
    ]

    # Remove the corresponding derived CSV entry.
    linked_rows, linked_cols = read_csv(linked_path)
    remaining_linked = [
        row for row in linked_rows
        if norm_key(row.get("title", ""), row.get("author", "")) != key
    ]

    # Remove the stable ID.
    del ids[key]

    # Remove the ID from the archive.
    if archive_path.exists():
        with open(archive_path, encoding="utf-8") as f:
            archive = json.load(f)

        archive["archived"] = [
            value for value in archive.get("archived", [])
            if value != book_id
        ]

        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)
            f.write("\n")

    # Remove scraper state belonging to this book.
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)

        state = {
            state_key: value
            for state_key, value in state.items()
            if not (
                state_key.startswith(norm(title) + "|" + norm(author) + "|")
            )
        }

        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
            f.write("\n")

    # Remove review entries belonging to this book.
    if review_path.exists():
        review_rows, review_cols = read_csv(review_path)

        review_rows = [
            row for row in review_rows
            if norm_key(row.get("title", ""), row.get("author", "")) != key
        ]

        write_csv(review_path, review_rows, review_cols)

    # Remove the original cover, unless another book uses it.
    front = deleted_row.get("front", "").strip()

    if front:
        cover_path = Path(front.replace("\\", "/"))
        if not cover_path.is_absolute():
            cover_path = ROOT / cover_path

        cover_path = cover_path.resolve()

        try:
            cover_path.relative_to(ROOT.resolve())
        except ValueError:
            cover_path = None

        if cover_path and cover_path.is_file():
            still_used = any(
                row.get("front", "").strip()
                and (
                    (ROOT / row["front"].replace("\\", "/")).resolve()
                    if not Path(row["front"]).is_absolute()
                    else Path(row["front"]).resolve()
                ) == cover_path
                for row in remaining_master
            )

            if not still_used:
                cover_path.unlink()

    # Write the updated CSVs and ID store.
    write_csv(master_path, remaining_master, master_cols)
    write_csv(linked_path, remaining_linked, linked_cols)

    id_data["ids"] = ids
    with open(ids_path, "w", encoding="utf-8") as f:
        json.dump(id_data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print(f"Deleted book {book_id}: {title} — {author}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z0-9]{5}", sys.argv[1]):
        sys.exit("Usage: python delete_book.py BOOK_ID")

    try:
        remove_book(sys.argv[1])
    except Exception as error:
        sys.exit(f"Deletion failed: {error}")
