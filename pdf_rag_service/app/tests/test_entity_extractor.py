from services.entity_extractor import select_subject


def test_most_frequent_person_wins():
    stats = {
        "<PERSON_1>": {"type": "PERSON", "count": 1, "first_page": 1},
        "<PERSON_2>": {"type": "PERSON", "count": 5, "first_page": 2},
        "<ADDRESS_1>": {"type": "ADDRESS", "count": 1, "first_page": 1},
    }
    subject = select_subject(stats)
    assert subject.name_token == "<PERSON_2>"
    assert subject.name_candidates == ["<PERSON_2>", "<PERSON_1>"]


def test_tie_broken_by_first_page_then_token_number():
    stats = {
        "<PERSON_3>": {"type": "PERSON", "count": 2, "first_page": 1},
        "<PERSON_1>": {"type": "PERSON", "count": 2, "first_page": 2},
        "<PERSON_2>": {"type": "PERSON", "count": 2, "first_page": 1},
    }
    assert select_subject(stats).name_candidates == ["<PERSON_2>", "<PERSON_3>", "<PERSON_1>"]


def test_first_address_is_primary():
    stats = {
        "<ADDRESS_2>": {"type": "ADDRESS", "count": 9, "first_page": 1},
        "<ADDRESS_1>": {"type": "ADDRESS", "count": 1, "first_page": 1},
    }
    subject = select_subject(stats)
    assert subject.address_token == "<ADDRESS_1>"
    assert subject.address_candidates == ["<ADDRESS_1>", "<ADDRESS_2>"]


def test_ignores_other_token_types():
    stats = {"<PHONE_1>": {"type": "PHONE_NUMBER", "count": 3, "first_page": 1}}
    subject = select_subject(stats)
    assert subject.is_empty
    assert subject.name_candidates == [] and subject.address_candidates == []


def test_empty_stats_gives_empty_subject():
    assert select_subject({}).is_empty


def test_as_dict_has_only_token_fields():
    stats = {"<PERSON_1>": {"type": "PERSON", "count": 1, "first_page": 1}}
    d = select_subject(stats).as_dict()
    assert set(d) == {"name_token", "address_token", "name_candidates", "address_candidates"}
    assert d["name_token"] == "<PERSON_1>" and d["address_token"] is None
