from backend.app.sync import build_track_search_queries


def test_track_queries_do_not_turn_artist_into_negative_search_term():
    queries = build_track_search_queries("Kanye West", "Good Morning", "Graduation")

    assert queries[0] == "Good Morning Kanye West Graduation"
    assert all(" - " not in query for query in queries)


def test_track_query_preserves_wildcard_strategy_without_hyphen_separator():
    queries = build_track_search_queries("Kanye West", "Good Morning")

    assert queries == ["Good Morning Kanye West", "Good Morning *anye West"]
