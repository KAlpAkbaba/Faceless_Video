from pipeline.writer import clean_narration, slugify, target_words


def test_markdown_and_stage_directions_are_stripped():
    raw = "## Heading\nNARRATOR: In **nineteen o two** a fight began. [CUT TO WIDE]\n- a bullet"
    cleaned = clean_narration(raw)
    assert "#" not in cleaned
    assert "NARRATOR" not in cleaned
    assert "*" not in cleaned
    assert "CUT TO" not in cleaned
    assert cleaned.startswith("Heading")


def test_emoji_are_removed():
    assert "🚀" not in clean_narration("Liftoff 🚀 happened.")


def test_em_dashes_become_spoken_pauses():
    assert "—" not in clean_narration("A thing — a pause — an end.")


def test_paragraph_breaks_survive():
    assert "\n\n" in clean_narration("First para.\n\n\n\nSecond para.")


def test_slugify_is_ascii_and_bounded():
    assert slugify("The 1902 Patent Fight — AC vs DC!") == "the-1902-patent-fight-ac-vs-dc"
    assert slugify("Şehir Efsanesi") == "sehir-efsanesi"
    assert len(slugify("word " * 60)) <= 60
    assert slugify("!!!") == "untitled"


def test_target_words_scales_with_runtime():
    assert target_words(360) == 900
    assert target_words(50) == 125
    assert target_words(1) >= 40
