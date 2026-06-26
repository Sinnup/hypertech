"""
Regression test for /reload-kb staged progress (HT-F0032C + HT-9AF839).

ingest.run() reports progress as ``(stage, detail)`` so the Slack handler can
drive the /viz steps strip (download → load → embed → upsert → done) and stream
per-batch upsert detail.
"""

import ingestion.google_drive.ingest as ing


def test_run_emits_staged_progress(monkeypatch, tmp_path):
    fake = tmp_path / "emv.md"
    fake.write_text("# EMV\n" + "PCI DSS card data encryption requirements. " * 200)

    monkeypatch.setattr(ing, "_download_public_folder", lambda fid, td: [fake])
    monkeypatch.setattr(ing.chroma, "upsert", lambda *a, **k: None)

    stages = []
    res = ing.run(folder_id="FAKE_FOLDER", progress=lambda stage, detail: stages.append(stage))

    assert res["ingested"] > 0
    # the ordered stages fire (upsert may repeat per batch; done is terminal)
    assert "download" in stages
    assert "load" in stages
    assert "embed" in stages
    assert "upsert" in stages
    assert stages[-1] == "done"


def test_run_without_progress_is_safe(monkeypatch, tmp_path):
    fake = tmp_path / "doc.md"
    fake.write_text("# Doc\n" + "content " * 40)
    monkeypatch.setattr(ing, "_download_public_folder", lambda fid, td: [fake])
    monkeypatch.setattr(ing.chroma, "upsert", lambda *a, **k: None)
    res = ing.run(folder_id="FAKE")  # no progress arg → must not raise
    assert res["ingested"] > 0
