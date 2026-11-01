"""Preserve ordered platform routes while honoring owned WebChat bindings."""

from unittest.mock import Mock

import pytest

from astrbot.core.umop_config_router import UmopConfigRouter


@pytest.mark.parametrize(
    "origin",
    [
        "qq:GroupMessage:123456",
        "aiocqhttp:FriendMessage:654321",
        "telegram:FriendMessage:123456",
        "qq:FriendMessage:webchat!account_owner!session_id",
        "webchat:FriendMessage:legacy-session-id",
        "webchat:FriendMessage:webchat!!session_id",
        "webchat:FriendMessage:webchat!account_owner!",
        "webchat:FriendMessage:webchat!*some_owner*!session_id",
        "webchat:OtherMessage:webchat!account_owner!session_id",
    ],
)
@pytest.mark.parametrize("exact_first", [False, True])
def test_existing_platform_routes_keep_their_first_match(origin, exact_first):
    router = UmopConfigRouter(Mock())
    router.umop_to_conf_id = (
        {origin: "exact", "*:*:*": "wildcard"}
        if exact_first
        else {"*:*:*": "wildcard", origin: "exact"}
    )
    assert router.get_conf_id_for_umop(origin) == (
        "exact" if exact_first else "wildcard"
    )


@pytest.mark.parametrize("message_type", ["FriendMessage", "GroupMessage"])
@pytest.mark.parametrize("config_id", ["default", "research"])
@pytest.mark.parametrize("wildcard", ["webchat:*:*", "*:*:*", "::"])
def test_owned_webchat_binding_overrides_broader_routes(
    message_type, config_id, wildcard
):
    router = UmopConfigRouter(Mock())
    origin = f"webchat:{message_type}:webchat!account_owner!session_id"
    router.umop_to_conf_id = {wildcard: "wildcard", origin: config_id}
    assert router.get_conf_id_for_umop(origin) == config_id
    assert router.umop_to_conf_id == {wildcard: "wildcard", origin: config_id}
