from pipeline.state import History


def test_history_round_trips(tmp_path):
    path = tmp_path / "history.json"
    history = History(path)
    history.add("a-slug", "A Title", "an angle", ["vid1"])
    history.save()

    reloaded = History(path)
    assert reloaded.has_slug("a-slug")
    assert reloaded.recent_titles() == ["A Title"]
    assert reloaded.entries[0].video_ids == ["vid1"]


def test_missing_file_is_not_an_error(tmp_path):
    assert History(tmp_path / "nope.json").recent_titles() == []


def test_corrupt_history_is_quarantined_not_fatal(tmp_path):
    # A broken ledger must not stop the day's video.
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    history = History(path)
    assert history.entries == []
    assert (tmp_path / "history.corrupt.json").exists()


def test_dedupe_window_is_bounded(tmp_path):
    history = History(tmp_path / "history.json", keep_last=3)
    for index in range(6):
        history.add(f"slug-{index}", f"Title {index}", "angle")
    assert history.recent_slugs() == {"slug-3", "slug-4", "slug-5"}
    assert not history.has_slug("slug-0")
