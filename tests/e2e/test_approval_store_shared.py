"""
Regression test for HT-08AFDC: the approval store must be a single shared dict.

The Slack server runs as `python -m core.notifications.slack_commands`, loading
that file as `__main__`. If the approval dicts lived there, a `from
core.notifications.slack_commands import get_approval_result` (done by the
validation gate) would see a *different* module object with a *different* dict —
so Slack approvals never reached the pipeline. Keeping the store in its own
module fixes that. These tests assert the store is shared across every consumer.
"""

import core.notifications.approval_store as store
import core.notifications.slack_commands as sc
import core.agent_registry.validation_gate as vg


def test_slack_commands_reexports_same_store():
    assert sc._approval_results is store._approval_results
    assert sc._approval_events is store._approval_events
    assert sc.get_approval_result is store.get_approval_result


def test_gate_reads_what_slack_handler_stored():
    store._approval_results.clear()
    # Simulate the Slack interactive handler storing an approval.
    store.store_approval("HT-SHARE1", "escalation_ba_compliance",
                         {"status": "approved", "approved_by": "u"})
    # The gate's lookup helper must see it.
    got = vg._get_stored_approval("HT-SHARE1", "escalation_ba_compliance")
    assert got is not None and got["status"] == "approved"
    store._approval_results.clear()
