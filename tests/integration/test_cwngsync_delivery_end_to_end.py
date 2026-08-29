# Calibre-Web-NextGen — fork of Calibre-Web-Automated
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The whole send-to-device cycle, against a running server and a real library.

Every other test of this feature stops at a seam. The service tests drive
``device_delivery`` with a hand-built session; the protocol tests drive the Flask
routes with a stubbed user and an in-memory database; the Lua contract tests drive
the plugin with the network mocked. Each half can be green while the two disagree
about the thing between them -- the exact failure the ``api.json`` two-list
invariant already exists to catch on the client side.

So this joins them: a real device identity registers itself, a real book is queued
through the same web API a person clicks, and the device claims, downloads and
confirms it, with the bytes checked against the size the server promised.

The negative control is not optional here. A download that succeeds proves nothing
unless the same request with a wrong claim token fails, because "the server hands
out the file" and "the server hands out the file to whoever asks" look identical
from the happy path.

Requires a running container (``CWA_TEST_PORT``, default 8085); skips otherwise.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

pytestmark = [pytest.mark.docker_integration, pytest.mark.slow]

KOREADER_READABLE = {"EPUB", "PDF", "MOBI", "FB2", "DJVU", "CBZ", "CBR", "TXT", "HTML", "RTF"}
KOSYNC_ACCEPT = "application/vnd.koreader.v1+json"


@pytest.fixture
def cwng_server():
    """An authenticated session against whatever server is already serving.

    Deliberately not ``cwa_api_client``: that fixture also OWNS a container's
    lifecycle under a fixed name, so it collides with any other session already
    running one and it cannot be pointed at a server started some other way.
    This one only asks whether something is listening, which lets the same test
    run against a CI container, a local dev stack, or a worktree instance.
    """
    import requests

    port = os.getenv("CWA_TEST_PORT", "8085")
    base_url = f"http://localhost:{port}"
    try:
        requests.get(base_url, timeout=3)
    except requests.exceptions.RequestException:
        pytest.skip(f"no CWNG server is listening on port {port}")

    session = requests.Session()
    # The login form is CSRF-protected, so the token has to come off the rendered
    # page first. Posting credentials alone returns 400, not 401 -- which reads
    # like a bad request rather than a missing token and is easy to misdiagnose.
    form = session.get(f"{base_url}/login", timeout=10)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', form.text)
    if token is None:
        pytest.skip("could not read a CSRF token from the login page")
    login = session.post(
        f"{base_url}/login",
        data={"username": "admin", "password": "admin123",
              "csrf_token": token.group(1)},
        allow_redirects=False, timeout=10,
    )
    if login.status_code not in (200, 302):
        pytest.skip(
            f"could not authenticate against the CWNG server (HTTP {login.status_code})"
        )
    return {"base_url": base_url, "session": session}


@pytest.fixture
def device(cwng_server):
    """A device identity unique to this run, so reruns never collide."""
    return {
        "device": "IntegrationReader",
        # A fresh id each run: the registry keys deliveries by device, and a
        # reused id would inherit the previous run's queue state.
        "device_id": f"integration-{uuid.uuid4().hex}",
    }


def _kosync(client, method, path, **kwargs):
    """kosync speaks Basic auth and its own accept header, not the web session."""
    import requests

    headers = kwargs.pop("headers", {})
    headers.setdefault("accept", KOSYNC_ACCEPT)
    return requests.request(
        method, f"{client['base_url']}/kosync{path}",
        auth=("admin", "admin123"), headers=headers, timeout=30, **kwargs,
    )


def _csrf(client):
    response = client["session"].get(f"{client['base_url']}/api/v1/auth/csrf", timeout=10)
    response.raise_for_status()
    return response.json()["csrf_token"]


def _first_deliverable_book(client):
    response = client["session"].get(f"{client['base_url']}/api/v1/books?limit=50", timeout=15)
    response.raise_for_status()
    for item in response.json().get("items", []):
        if KOREADER_READABLE.intersection(item.get("formats") or []):
            return item
    pytest.skip("the library holds no book in a format KOReader can read")


def test_a_device_can_be_sent_a_book_and_confirm_it(cwng_server, device):
    client = cwng_server

    # Registering happens as a side effect of the device's first report, which is
    # also the only way a device gets an id the web UI can address.
    registered = _kosync(client, "PUT", "/syncs/inventory",
                         json={**device, "inventory": []})
    assert registered.status_code == 200, registered.text
    public_id = registered.json()["device"]

    book = _first_deliverable_book(client)
    queued = client["session"].post(
        f"{client['base_url']}/api/v1/books/{book['id']}/device-deliveries",
        json={"device": public_id},
        headers={"X-CSRFToken": _csrf(client)},
        timeout=30,
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["queued"] is True
    # Sending an unreadable format would look like success and fail on the device.
    assert queued.json()["format"] in KOREADER_READABLE

    claimed = _kosync(client, "POST", "/syncs/deliveries/claim", json=device)
    assert claimed.status_code == 200, claimed.text
    delivery = claimed.json()["delivery"]
    assert delivery is not None, "the queued book was not offered to its own device"
    assert delivery["book_id"] == book["id"]
    token = delivery["claim_token"]

    device_headers = {
        "X-CWNG-Device-ID": device["device_id"],
        "X-CWNG-Device-Name": device["device"],
    }

    # The negative control runs BEFORE the real download: if a wrong token were
    # accepted, a later success would prove nothing, and running it first also
    # shows the refusal is not merely "already downloaded".
    refused = _kosync(client, "GET", f"/syncs/deliveries/{delivery['id']}/download",
                      headers={**device_headers, "X-CWNG-Claim-Token": "not-the-token"})
    assert refused.status_code == 409, (
        f"a wrong claim token was not refused (got {refused.status_code})"
    )

    downloaded = _kosync(client, "GET", f"/syncs/deliveries/{delivery['id']}/download",
                         headers={**device_headers, "X-CWNG-Claim-Token": token})
    assert downloaded.status_code == 200, downloaded.text
    assert len(downloaded.content) == delivery["size"], (
        "the server promised a size it did not deliver"
    )
    assert len(downloaded.content) > 0

    import hashlib

    confirmed = _kosync(client, "PUT", "/syncs/deliveries/complete", json={
        **device,
        "delivery_id": delivery["id"],
        "claim_token": token,
        "lpath": f"books/{delivery['id']}-integration.{delivery['format'].lower()}",
        "checksum": hashlib.md5(downloaded.content).hexdigest(),
        "size": len(downloaded.content),
        "mtime": 1700000000,
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["completed"] is True

    # A confirmed delivery must leave the queue. If it did not, the device would
    # re-download the same book on every sync for the rest of its life.
    drained = _kosync(client, "POST", "/syncs/deliveries/claim", json=device)
    assert drained.status_code == 200, drained.text
    assert drained.json()["delivery"] is None, (
        "the completed delivery was offered again; this device would loop forever"
    )
