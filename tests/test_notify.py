from __future__ import annotations

from tailcam.config import AppConfig, NotificationsConfig
from tailcam.notify.service import NotificationService


def _svc(**over) -> NotificationService:
    cfg = NotificationsConfig(**{"enabled": True, **over})
    return NotificationService(cfg)


def _spy(svc: NotificationService) -> list:
    sent: list = []
    svc._dispatch = lambda ev: sent.append(ev)  # type: ignore[assignment]
    return sent


# -- filters ---------------------------------------------------------------
def test_disabled_service_sends_nothing():
    svc = _svc(enabled=False)
    sent = _spy(svc)
    svc.notify_motion(camera_id="cam0", label="person", confidence=0.9)
    assert sent == []


def test_motion_off_sends_nothing():
    svc = _svc(notify_motion=False)
    sent = _spy(svc)
    svc.notify_motion(camera_id="cam0", label="person", confidence=0.9)
    assert sent == []


def test_min_confidence_filters_low():
    svc = _svc(min_confidence=0.5, cooldown_seconds=0)
    sent = _spy(svc)
    svc.notify_motion(camera_id="cam0", label="person", confidence=0.3)
    assert sent == []
    svc.notify_motion(camera_id="cam0", label="person", confidence=0.8)
    assert len(sent) == 1


def test_label_allowlist_filters():
    svc = _svc(labels=["person", "vehicle"], cooldown_seconds=0)
    sent = _spy(svc)
    svc.notify_motion(camera_id="cam0", label="animal", confidence=0.9)
    assert sent == []
    svc.notify_motion(camera_id="cam0", label="person", confidence=0.9)
    assert len(sent) == 1
    assert sent[0].kind == "motion" and sent[0].data["label"] == "person"


def test_cooldown_per_camera():
    svc = _svc(cooldown_seconds=999)
    sent = _spy(svc)
    svc.notify_motion(camera_id="cam0", label="person", confidence=0.9)
    svc.notify_motion(camera_id="cam0", label="person", confidence=0.9)  # within cooldown
    svc.notify_motion(camera_id="cam1", label="person", confidence=0.9)  # different camera
    assert len(sent) == 2  # cam0 once, cam1 once


def test_camera_status_transitions():
    svc = _svc()
    sent = _spy(svc)
    svc.notify_camera_status(camera_id="cam0", name="Front", old="online", new="offline")
    svc.notify_camera_status(camera_id="cam0", name="Front", old="offline", new="online")
    svc.notify_camera_status(camera_id="cam0", name="Front", old="online", new="online")  # no-op
    assert [e.severity for e in sent] == ["warning", "success"]


def test_training_only_terminal_states():
    svc = _svc()
    sent = _spy(svc)
    svc.notify_training(run_id=1, dataset_id=2, status="training")  # not terminal
    svc.notify_training(run_id=1, dataset_id=2, status="complete", metrics={"top1": 0.9})
    assert len(sent) == 1 and sent[0].severity == "success"


# -- channels --------------------------------------------------------------
def test_channels_configured():
    svc = _svc(discord_webhook="https://discord/x", webhook_url="https://bot/x")
    assert set(svc.channels_configured()) == {"discord", "webhook"}
    assert "telegram" not in svc.channels_configured()  # needs both token + chat id


def test_send_test_no_channels():
    svc = _svc()  # enabled but nothing configured
    result = svc.send_test()
    assert result["channels"] == []


# -- config + REST ---------------------------------------------------------
def test_config_roundtrip():
    cfg = AppConfig()
    data = cfg.to_dict()
    assert "notifications" in data
    restored = AppConfig.from_dict(data)
    assert restored.notifications.cooldown_seconds == 60.0


def test_rest_get_notifications(client):
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["channels"] == []


def test_rest_update_notifications(client):
    resp = client.post("/api/notifications", json={"enabled": True, "discord_webhook": "https://d/x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert "discord" in body["channels"]


def test_rest_test_no_channels_is_400(client):
    resp = client.post("/api/notifications/test")
    assert resp.status_code == 400
