"""
Authenticated HMRC HTTP client — attaches OAuth bearer + fraud headers
to every MTD call.

Stub implementation for the first scaffold PR. The full version will:
  - look up the user's encrypted access_token, decrypt
  - call the target endpoint with the bearer + the 13 fraud headers
  - on 401, refresh-and-retry once
  - persist updated tokens (HMRC rotates refresh tokens)
  - log every request + response into `hmrc_submissions` for audit
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("bankparse.hmrc.client")


def call(
    *,
    user_id: int,
    method: str,
    path: str,
    request,
    fraud_context: dict,
    json_body: Any | None = None,
    accept_version: str = "application/vnd.hmrc.1.0+json",
) -> dict:
    """STUB. To be implemented in the next PR.

    Should compose:
        url = HMRC_BASE_URL + path
        Authorization: Bearer <decrypted access token>
        Accept: <accept_version>
        Content-Type: application/json
        + all 13 fraud prevention headers from fraud_headers.build_headers(...)
    """
    raise NotImplementedError(
        "hmrc.services.client.call is a stub — implement in the next PR "
        "alongside the obligations endpoint."
    )
