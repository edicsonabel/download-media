"""Tests for the bilingual message lookup."""

from __future__ import annotations

from download_media.i18n import _MESSAGES, t


def test_es_and_en_have_same_keys() -> None:
    """Catch translation drift early — every key must exist in both languages."""
    es_keys = set(_MESSAGES["es"].keys())
    en_keys = set(_MESSAGES["en"].keys())
    assert es_keys == en_keys, (
        f"Keys missing from EN: {es_keys - en_keys}. Keys missing from ES: {en_keys - es_keys}."
    )


def test_t_returns_string() -> None:
    assert isinstance(t("wizard_title"), str)
    assert t("wizard_title")  # non-empty


def test_t_substitutes_args() -> None:
    msg = t("carousel_hdr", 5)
    assert "5" in msg


def test_t_unknown_key_returns_key() -> None:
    assert t("this-key-does-not-exist") == "this-key-does-not-exist"


def test_carousel_image_messages() -> None:
    for lang in ("es", "en"):
        assert {"item_image_skipped", "image_not_saved", "ask_selection_default"} <= set(
            _MESSAGES[lang]
        )
        assert not {"ig_image_warning", "ask_selection_default_videos"} & set(_MESSAGES[lang])


def test_refusals_do_not_say_cancelling() -> None:
    # Nothing is cancelled: the run goes on, the picker asks again, or direct mode fails.
    for lang in ("es", "en"):
        for key in ("image_only_skipped", "no_supported_items"):
            assert "Cancel" not in _MESSAGES[lang][key]
            assert "Cancelando" not in _MESSAGES[lang][key]
