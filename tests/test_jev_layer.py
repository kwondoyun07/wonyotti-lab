from src.jev_layer import macro_event, news_questions, tilt_lock, tilt_questions


def test_questions_build_offline():
    assert set(news_questions()) == {"us_macro_event", "event_type"}
    assert set(tilt_questions()) == {"revenge", "fomo"}


def test_gates():
    assert tilt_lock({"revenge": {"noul": 0.82}, "fomo": {"noul": 0.1}})
    assert not tilt_lock({"revenge": {"noul": 0.2}, "fomo": {"noul": 0.1}})
    assert macro_event({"us_macro_event": {"noul": 0.9}})
    assert not macro_event({})
