from iOpenPod.app.track_repair_matching import (
    match_album_tracks,
    normalise_text,
    parse_filename_number,
)


def test_parse_filename_number_supports_single_and_multi_disc_names() -> None:
    assert parse_filename_number("01 - First Song.mp3") == (1, None)
    assert parse_filename_number("2-03 - Third Song.m4a") == (3, 2)


def test_normalise_text_matches_common_punctuation_variants() -> None:
    assert normalise_text("Beyoncé — Live!") == normalise_text("Beyonce Live")


def test_match_album_tracks_rejects_duplicate_reference_positions() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "One",
            "Album": "Record",
            "Artist": "Band",
            "track_number": 0,
        },
        {
            "db_id": 11,
            "Title": "Two",
            "Album": "Record",
            "Artist": "Band",
            "track_number": 0,
        },
    ]
    result = match_album_tracks(
        tracks,
        [
            {"title": "One", "track_number": 1, "disc_number": 1},
            {"title": "Two", "track_number": 1, "disc_number": 1},
        ],
        source="Test",
    )
    assert not result.suggestions
    assert "not unique" in result.warnings[0]


def test_match_album_tracks_returns_title_matched_repairs() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "Second Song",
            "Album": "Record",
            "Artist": "Band",
            "track_number": 0,
        },
    ]
    result = match_album_tracks(
        tracks,
        [{"title": "Second Song", "track_number": 2, "disc_number": 1}],
        source="Test",
    )
    assert result.suggestions[0].new_track_number == 2


def test_match_album_tracks_can_ignore_version_annotations_when_enabled() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "Burning Down the House (2005 Remastered Edition)",
            "Album": "Speaking in Tongues (Deluxe Edition)",
            "Artist": "Talking Heads",
            "track_number": 0,
        },
    ]
    reference = [
        {
            "title": "Burning Down the House",
            "track_number": 1,
            "disc_number": 1,
        }
    ]

    strict = match_album_tracks(tracks, reference, source="Test")
    loose = match_album_tracks(
        tracks,
        reference,
        source="Test",
        loose_matching=True,
    )

    assert not strict.suggestions
    assert loose.suggestions[0].new_track_number == 1


def test_loose_matching_uses_filename_position_to_disambiguate_versions() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "Burning Down the House (2005 Remastered Edition)",
            "Location": ":iPod_Control:Music:F00:01 Burning Down the House.m4a",
            "Album": "Speaking in Tongues (Deluxe Edition)",
            "Artist": "Talking Heads",
            "track_number": 0,
        }
    ]
    reference = [
        {
            "title": "Burning Down the House (2005 Remastered Edition)",
            "track_number": 1,
            "disc_number": 1,
        },
        {
            "title": "Burning Down the House (Alternate Version)",
            "track_number": 11,
            "disc_number": 1,
        },
    ]

    result = match_album_tracks(
        tracks,
        reference,
        source="Test",
        loose_matching=True,
    )

    assert result.suggestions[0].new_track_number == 1
    assert result.suggestions[0].reason == "Matched by filename track position."


def test_matching_does_not_suggest_punctuation_only_metadata_changes() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "Don't Stop",
            "Artist": "The Band",
            "Album Artist": "The Band",
            "Album": "Record",
            "track_number": 0,
        }
    ]
    reference = [
        {
            "title": "Don’t Stop",  # noqa: RUF001 - exercise curly apostrophe matching
            "artist": "The Band",
            "album_artist": "The Band",
            "track_number": 1,
            "disc_number": 1,
        }
    ]

    result = match_album_tracks(tracks, reference, source="Test")

    # 2.0 distinguishes an unknown disc (0) from an explicitly supplied disc 1.
    assert result.suggestions[0].changes == (
        ("track_number", 0, 1),
        ("disc_number", 0, 1),
    )


def test_match_album_tracks_fills_missing_title_and_artist() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "Song",
            "Artist": "",
            "Album Artist": "",
            "Album": "Record",
            "track_number": 0,
        },
    ]
    result = match_album_tracks(
        tracks,
        [
            {
                "title": "Song",
                "artist": "Band feat. Guest",
                "album_artist": "Band",
                "track_number": 1,
                "disc_number": 1,
                "total_tracks": 1,
                "total_discs": 1,
            }
        ],
        source="Test",
    )
    fields = {field: new for field, _old, new in result.suggestions[0].changes}
    assert fields["Artist"] == "Band feat. Guest"
    assert fields["Album Artist"] == "Band"


def test_match_album_tracks_does_not_overwrite_existing_metadata_by_default() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "Old Title",
            "Artist": "Old Artist",
            "Album": "Record",
            "track_number": 1,
        },
    ]
    result = match_album_tracks(
        tracks,
        [
            {
                "title": "New Title",
                "artist": "New Artist",
                "track_number": 1,
                "disc_number": 1,
            }
        ],
        source="Test",
    )
    assert not result.suggestions


def test_match_album_tracks_can_replace_by_position_when_explicitly_enabled() -> None:
    tracks = [
        {
            "db_id": 10,
            "Title": "Old Title",
            "Artist": "Old Artist",
            "Album": "Record",
            "track_number": 1,
        },
    ]
    result = match_album_tracks(
        tracks,
        [
            {
                "title": "New Title",
                "artist": "New Artist",
                "track_number": 1,
                "disc_number": 1,
            }
        ],
        source="Test",
        overwrite_existing=True,
    )
    fields = {field: new for field, _old, new in result.suggestions[0].changes}
    assert fields["Title"] == "New Title"
    assert fields["Artist"] == "New Artist"
