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

    assert queries == [
        "*ood *orning *anye *est",
        "Good Morning *anye *est",
        "Good Morning Kanye West",
        "Good Morning",
    ]


def test_track_query_has_title_only_fallback_for_busy_artists():
    queries = build_track_search_queries("Prince", "Purple Rain", "Purple Rain")

    assert queries == [
        "*urple *ain *rince",
        "Purple Rain *rince",
        "Purple Rain Prince",
        "Purple Rain",
    ]


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

    assert queries == [
        "*urple *ain *rince",
        "Purple Rain *rince",
        "*urple *ain Prince",
    ]
    assert all(" - " not in query and "-" not in query for query in queries)


def test_album_wildcard_queries_strip_hyphens_and_start_with_full_wildcard():
    queries = build_album_wildcard_queries("Kanye West", "BULLY - DELUXE")

    assert queries == [
        "*ULLY *ELUXE *anye *est",
        "BULLY DELUXE *anye *est",
        "*ULLY *ELUXE Kanye West",
    ]
    assert all("-" not in query for query in queries)


def test_track_queries_strip_italian_con_collaborations_and_comma_artists():
    queries = build_track_search_queries(
        "Vasco Rossi, Marracash",
        "La Pioggia Alla Domenica (con Marracash)",
    )

    assert queries == [
        "La *ioggia *lla *omenica *asco *ossi",
        "La Pioggia Alla Domenica *asco *ossi",
        "La Pioggia Alla Domenica Vasco Rossi",
        "La Pioggia Alla Domenica",
    ]
    assert all("Marracash" not in q and "*arracash" not in q for q in queries)


def test_track_queries_and_slskd_candidates_accept_normal_version_for_live_suffix():
    from backend.app.clients.slskd import SlskdClient

    queries = build_track_search_queries("Vasco Rossi", "Marea - Vasco Live 20|26")

    assert queries[0] == "*area *asco *ossi"
    assert "Marea Vasco Rossi" in queries
    assert "Marea" in queries
    assert all("Live" not in q and "*ive" not in q for q in queries)

    client = SlskdClient("http://localhost:5030", "key")
    responses = [
        {
            "username": "ge1965",
            "hasFreeUploadSlot": True,
            "queueLength": 0,
            "speed": 100000,
            "files": [
                {"filename": r"@@habcx\MUSICA\Vasco Rossi\10. Vasco Rossi - Marea.mp3", "size": 9000000, "bitrate": 320},
                {"filename": r"@@habcx\MUSICA\Vasco Rossi\Marea (Live).mp3", "size": 9000000, "bitrate": 320},
            ],
        }
    ]
    candidates = client._parse_candidates(
        responses,
        artist="Vasco Rossi",
        title="Marea - Vasco Live 20|26",
        audio_quality={"preset": "all"},
    )
    assert len(candidates) == 2


def test_clean_track_title_preserves_title_track_matching_album_name():
    from backend.app.album_sync import clean_track_title

    assert clean_track_title("09 Bully feat. Cee Lo Green.mp3", "Kanye West", "BULLY - DELUXE") == "Bully"
    assert clean_track_title("Ye - BULLY - DELUXE - 09 - BULLY (feat. CeeLo Green).mp3", "Kanye West", "BULLY - DELUXE") == "BULLY"


def test_match_track_titles_matches_library_naming_and_live_or_con_variants():
    from backend.app.sync import match_track_titles

    assert match_track_titles(
        "Purple Rain",
        "Prince and The Revolution_Purple Rain_09_Purple Rain.mp3",
        "Prince and The Revolution",
    )
    assert match_track_titles(
        "La Pioggia Alla Domenica (con Marracash)",
        "Vasco Rossi_Siamo qui_05_La pioggia alla domenica.mp3",
        "Vasco Rossi, Marracash",
    )
    assert match_track_titles(
        "Marea - Vasco Live 20|26",
        "Vasco Rossi_Marea_01_Marea.mp3",
        "Vasco Rossi",
    )

