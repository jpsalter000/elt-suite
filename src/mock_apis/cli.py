"""``elt-mock <api>``: serve a mock API locally for demos and CI."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

import uvicorn
from fastapi import FastAPI

from mock_apis import cartwheel, helpline, netsuite, shopfront, ticketdesk

APIS: dict[str, tuple[Callable[[], FastAPI], int]] = {
    "shopfront": (shopfront.create_app, 8001),
    "ticketdesk": (ticketdesk.create_app, 8002),
    "cartwheel": (cartwheel.create_app, 8003),
    "helpline": (helpline.create_app, 8004),
    "netsuite": (netsuite.create_app, 8005),
}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="elt-mock", description=__doc__)
    parser.add_argument("api", choices=sorted(APIS), help="which mock API to serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        help="default: 8001 shopfront, 8002 ticketdesk, 8003 cartwheel, 8004 helpline, "
        "8005 netsuite",
    )
    args = parser.parse_args(argv)

    create_app, default_port = APIS[args.api]
    uvicorn.run(create_app(), host=args.host, port=args.port or default_port, log_level="info")


if __name__ == "__main__":
    main()
