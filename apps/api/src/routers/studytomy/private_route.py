"""Cache policy for authenticated Studytomy routes."""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

PRIVATE_HEADERS = {"Cache-Control": "private, no-store"}


class PrivateStudyTomyRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except HTTPException as error:
                error.headers = {
                    key: value
                    for key, value in (error.headers or {}).items()
                    if key.lower() != "cache-control"
                } | PRIVATE_HEADERS
                raise

        return handle
