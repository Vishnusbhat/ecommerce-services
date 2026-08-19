"""Notification 'delivery' -- real channels are out of scope
(notification-service.md); log the content and, if configured, post to a
Slack webhook, reusing the Dealshare alerting-bot pattern instead of
building a fake email sender."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger("gestalt.notification-service")


def notify(text: str) -> None:
    logger.info("notification: %s", text)
    if settings.slack_webhook_url:
        try:
            httpx.post(settings.slack_webhook_url, json={"text": text}, timeout=5.0)
        except httpx.HTTPError:
            logger.exception("slack_webhook_failed")
