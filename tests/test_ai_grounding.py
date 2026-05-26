from src.ai import grounding


def test_numbers_in_extracts_ints_and_decimals():
    assert grounding.numbers_in("xP 7.2 at home, gap 1.8, confidence 82") == {"7.2", "1.8", "82"}


def test_numbers_in_empty_string():
    assert grounding.numbers_in("") == set()


def test_numbers_in_no_numbers():
    assert grounding.numbers_in("just words here") == set()


def test_is_grounded_when_prose_numbers_subset_of_input():
    ok, ungrounded = grounding.is_grounded(
        prose="Haaland at 7.2 xP",
        input_payload_text='{"xp": 7.2, "confidence": 82}',
    )
    assert ok is True
    assert ungrounded == set()


def test_is_grounded_false_with_invented_number():
    ok, ungrounded = grounding.is_grounded(
        prose="Haaland at 7.2 xP, confidence 99",
        input_payload_text='{"xp": 7.2, "confidence": 82}',
    )
    assert ok is False
    assert ungrounded == {"99"}


def test_is_grounded_with_no_numbers_in_prose():
    ok, ungrounded = grounding.is_grounded(
        prose="Haaland is the captain this week",
        input_payload_text='{"xp": 7.2}',
    )
    assert ok is True
    assert ungrounded == set()
