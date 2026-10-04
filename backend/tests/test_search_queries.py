from backend.app.sync import (
    build_album_wildcard_queries,
    build_track_search_queries,
    wildcard_artist_variants,
    wildcard_text_variants,
)


def test_track_queries_do_not_turn_artist_into_negative_search_term():
    queries = build_track_search_queries("Kanye West", "Good Morning", "Graduation")

    assert queries[0] == "Good Morning Kanye West Graduation"
    assert all(" - " not in query for query in queries)


def test_track_query_preserves_wildcard_strategy_without_hyphen_separator():
    queries = build_track_search_queries("Kanye West", "Good Morning")

    assert queries[:4] == [
        "Good Morning Kanye West",
        "*ood Morning Kanye West",
        "Good *orning Kanye West",
        "*ood *orning Kanye West",
    ]
    assert "Good Morning" in queries
    assert "Good Morning *anye West" in queries


def test_track_query_has_title_only_fallback_for_busy_artists():
    queries = build_track_search_queries("Prince", "Purple Rain", "Purple Rain")

    assert queries[:2] == ["Purple Rain Prince", "*urple Rain Prince"]
    assert "Purple *ain Prince" in queries
    assert "*urple *ain Prince" in queries
    assert "Purple Rain" in queries
    assert "Purple Rain *rince" in queries


def test_wildcard_artist_variants_expand_each_multiword_artist():
    assert wildcard_artist_variants("Kanye West") == [
        "Kanye West",
        "*anye West",
        "Kanye *est",
        "*anye *est",
    ]


def test_wildcard_title_variants_expand_each_title_word():
    assert wildcard_text_variants("Purple Rain") == [
        "Purple Rain",
        "*urple Rain",
        "Purple *ain",
        "*urple *ain",
    ]


def test_album_wildcard_queries_cover_blocked_title_and_artist_terms():
    queries = build_album_wildcard_queries("Prince", "Purple Rain")

    assert "*urple *ain *rince" in queries
    assert all(" - " not in query for query in queries)
