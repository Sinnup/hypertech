"""
Regression test for HT-728BAE: predictable coder routing (whole-word match).

Naive substring matching mis-routed prompts — "pos" matched inside "POST", so a
FastAPI backend prompt went to coder_mobile. Routing now uses word boundaries.
"""

import pytest

from agents.orchestrator.agent import _pick_coder


@pytest.mark.parametrize("prompt,expected", [
    ("Build a tiny FastAPI backend with one POST /pay endpoint and a health route", "coder_backend"),
    ("Build an Android TPV app, button press simulates card insertion, deliver an APK", "coder_mobile"),
    ("Build a single-page web dashboard with Tailwind and a button", "coder_web"),
    ("Build a REST api microservice with a database", "coder_backend"),
    ("Write a quick script to summarise a file", "coder"),
])
def test_pick_coder(prompt, expected):
    assert _pick_coder(prompt) == expected


def test_post_does_not_route_to_mobile():
    # The specific false positive: "pos" inside "POST".
    assert _pick_coder("expose a POST endpoint in fastapi") != "coder_mobile"
