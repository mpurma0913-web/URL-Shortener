"""Create local secrets once without printing them; never overwrite an existing .env."""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def initialize(path: Path) -> None:
    content = ("# Local credentials: do not commit or share this file.\n"
               f"API_KEY={secrets.token_urlsafe(32)}\nREVIEWER_KEY={secrets.token_urlsafe(32)}\nAUDIT_KEY={secrets.token_urlsafe(32)}\n"
               "DATABASE_PATH=data/shortener.db\nBASE_URL=http://127.0.0.1:8000\n"
               "ALLOWED_HOSTS=127.0.0.1,localhost\nRATE_LIMIT=120\nREDIRECT_LIMIT=300\n")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        initialize(args.path)
    except FileExistsError:
        parser.exit(1, "Existing environment file left unchanged.\n")
    print("Created local credential file. Inspect it locally; do not paste it into AI tools or commit it.")
