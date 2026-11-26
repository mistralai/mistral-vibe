"""A spent Vibe Code allowance fills the gauge and says pay-as-you-go now bills it; the plan title stays plain."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

handshake = {
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "chat", "name": "TEAM", "title": "Team"},
            "planOffer": None,
            "rateLimitAction": None,
            "budget": {"usagePercentage": 100.0, "paygEnabled": True},
        }
    }
}

screen_contains = ("usage ██████ 100% · PAYG", "mistral-medium-3.5[medium] · Team")
screen_excludes = ("PAYG Enabled",)
timeline: Timeline = [take_snapshot()]
