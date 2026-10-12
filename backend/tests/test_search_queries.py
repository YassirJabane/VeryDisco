from backend.app.sync import (
    build_album_wildcard_queries,
    build_track_search_queries,
    wildcard_artist_variants,
    wildcard_text_variants,
)


def test_track_queries_do_not_turn_artist_into_negative_search_term():
    queries = build_track_search_queries("Kanye West", "Good Morning", "Graduation")

    assert queries[0] == "*ood *orning *anye *est"
    assert "Good Morning Kanye West Graduation" in queries
    assert all(" - " not in query for query in queries)


def test_track_query_preserves_wildcard_strategy_without_hyphen_separator():
    queries = build_track_search_queries("Kanye West", "Good Morning")

    assert queries[:4] == [
        "*ood *orning *anye *est",
        "Good Morning *anye West",
        "Good Morning Kanye *est",
        "Good Morning *anye *est",
    ]
    assert "Good Morning Kanye West" in queries
    assert "Good Morning" in queries
    assert "*ood Morning Kanye West" in queries
    assert queries.index("Good Morning *anye West") < queries.index("*ood Morning Kanye West")
    assert queries.index("Good Morning *anye West") < queries.index("Good Morning")


def test_track_query_has_title_only_fallback_for_busy_artists():
    queries = build_track_search_queries("Prince", "Purple Rain", "Purple Rain")

    assert queries[0] == "*urple *ain *rince"
    assert "Purple Rain *rince" in queries
    assert "Purple Rain Prince" in queries
    assert queries.index("Purple Rain *rince") < queries.index("*urple Rain Prince")
    assert queries.index("Purple Rain *rince") < queries.index("Purple Rain")


def test_track_query_generates_lead_artist_for_backing_band():
    queries = build_track_search_queries("Prince and The Revolution", "Purple Rain", "Purple Rain")

    assert queries[0] == "*urple *ain *rince"
    assert "Purple Rain Prince and The Revolution" in queries
    assert "Purple Rain Prince" in queries
    assert queries.index("Purple Rain Prince") < queries.index("Purple Rain")


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
