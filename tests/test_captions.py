from pipeline.captions import build_ass, chunk_words, escape_ass, format_timestamp
from pipeline.models import WordTiming


def words(count, gap=0.6, length=0.35):
    return [WordTiming(word=f"w{i}", start=i * gap, end=i * gap + length) for i in range(count)]


def parse_events(ass):
    def seconds(stamp):
        hours, minutes, rest = stamp.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(rest)

    events = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",")
            events.append((seconds(parts[1]), seconds(parts[2])))
    return events


def test_format_timestamp():
    assert format_timestamp(0) == "0:00:00.00"
    assert format_timestamp(3725.46) == "1:02:05.46"
    assert format_timestamp(-5) == "0:00:00.00"


def test_escape_ass_neutralises_braces():
    assert escape_ass("{drop}") == "\\{drop\\}"
    assert escape_ass("a\nb") == "a b"


def test_chunks_stay_readable():
    chunks = chunk_words(words(30))
    assert chunks
    assert max(len(chunk) for chunk in chunks) <= 5
    # Every word lands in exactly one chunk, in order.
    assert [w.word for chunk in chunks for w in chunk] == [w.word for w in words(30)]


def test_events_never_overlap():
    # Overlapping cues make libass draw two lines of captions at once.
    for gap in (0.35, 0.6, 1.2):
        events = parse_events(build_ass(words(24, gap=gap), width=1080, height=1920))
        assert events
        for (_, end), (next_start, _) in zip(events, events[1:]):
            assert end <= next_start + 1e-9


def test_every_word_gets_a_highlight_event():
    ass = build_ass(words(12), width=1080, height=1920)
    assert len(parse_events(ass)) == 12


def test_no_events_without_timings():
    assert parse_events(build_ass([], width=1080, height=1920)) == []
