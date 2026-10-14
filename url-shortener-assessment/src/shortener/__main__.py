"""Local entry point. Bind loopback; production TLS and identity are out of scope."""
import logging

import uvicorn

from shortener.api import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, access_log=False, server_header=False,
                proxy_headers=False, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
