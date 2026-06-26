"""
Regression test for HT-F0032C: /reload-kb streams progress and reports results.

The handler used to run the ingest silently and only post at the very end — so a
slow or hung ingestion (e.g. a wedged ChromaDB upsert) produced no feedback at
all. ingest.run() now accepts a progress callback invoked at each stage.
"""

import ingestion.google_drive.ingest as ing


def test_run_emits_progress_and_completes(monkeypatch, tmp_path):
    fake = tmp_path / "emv.md"
    fake.write_text("# EMV\n" + "PCI DSS card data encryption requirements. " * 60)

    # Avoid real Google Drive and a real ChromaDB.
    monkeypatch.setattr(ing, "_download_public_folder", lambda fid, td: [fake])
    monkeypatch.setattr(ing.chroma, "upsert", lambda *a, **k: None)

    msgs = []
    res = ing.run(folder_id="FAKE_FOLDER", progress=msgs.append)

    assert res["ingested"] > 0
    # progress fired at the key stages
    assert any("Downloading" in m for m in msgs)
    assert any("Downloaded" in m for m in msgs)
    assert any("Loaded" in m for m in msgs)


def test_run_without_progress_callback_is_safe(monkeypatch, tmp_path):
    fake = tmp_path / "doc.md"
    fake.write_text("# Doc\n" + "content " * 40)
    monkeypatch.setattr(ing, "_download_public_folder", lambda fid, td: [fake])
    monkeypatch.setattr(ing.chroma, "upsert", lambda *a, **k: None)
    # No progress arg → must not raise.
    res = ing.run(folder_id="FAKE")
    assert res["ingested"] > 0
