"""Bilingual user-facing messages (es/en).

Language is picked once at import time from ``$LANG`` (``es*`` → Spanish,
otherwise English). Use :func:`t` to look up a key; missing keys fall back
to English then to the key itself.
"""

from __future__ import annotations

import os
from typing import Final

LANG: Final[str] = "es" if os.environ.get("LANG", "").startswith("es") else "en"

_MESSAGES: dict[str, dict[str, str]] = {
    "es": {
        "wizard_title": "Wizard de descarga",
        "ask_url": "URL del video",
        "empty_url": "URL vacía. Abortando.",
        "from_clipboard": "URL detectada en el portapapeles:",
        "ask_use_clip": "Usar URL del portapapeles? [S/n]: ",
        "ask_type": "¿Qué quieres descargar?",
        "type_video": "Video (con audio)",
        "type_audio": "Solo audio",
        "ask_choice": "Selecciona [{}]: ",
        "ask_quality": "Calidad máxima de video",
        "ask_aformat": "Formato de audio",
        "ask_vformat": "Contenedor de video",
        "vformat_desc_mp4": "compatible con WhatsApp, iPhone, TV (H.264, hasta 1080p)",
        "vformat_desc_mkv": "máxima calidad, hasta 4K (VP9/AV1)",
        "vformat_desc_webm": "formato web abierto (VP9/AV1)",
        "mp4_h264_cap": "mp4 usa H.264, que normalmente llega hasta 1080p. Para {}p usa --video-format mkv.",
        "ask_output": "Carpeta destino [{}]: ",
        "ask_cookies": "¿Usar cookies de un navegador? (necesario para YouTube logueado, contenido privado)",
        "no_cookies": "No usar cookies",
        "back": "atrás",
        "cancel": "cancelar",
        "summary": "Selecciones hasta ahora:",
        "invalid_retry": "Opción inválida. Intenta de nuevo.",
        "url_required": "Falta la URL.",
        "cancelled": "Cancelado.",
        "interrupted": (
            "Interrumpido mientras se descargaba {0}. Pueden quedar archivos sin terminar en {1}."
        ),
        "probing": "Inspeccionando contenido...",
        "carousel_hdr": "Carrusel/Playlist con {} items:",
        "item_video": "video",
        "item_image": "imagen",
        "item_other": "otro",
        "ask_selection": "Selecciona items (ej: 1-3; 1,2,4; Enter = todo, c = cancelar): ",
        "invalid_selection": "Selección inválida. Intenta de nuevo.",
        "selected": "Seleccionados: {}",
        "fetching": "Obteniendo información...",
        "preview": "Vista previa",
        "p_title": "Título",
        "p_uploader": "Canal",
        "p_duration": "Duración",
        "p_size": "Tamaño aprox.",
        "ask_confirm": "¿Continuar con la descarga? [S/n]: ",
        "downloading": "Descargando...",
        "done": "Listo.",
        "done_without_cookies": "Guardado sin las cookies de {0}: es lo que el sitio muestra sin iniciar sesión.",
        "untagged": (
            "{}: guardado, pero ffmpeg no pudo escribir sus metadatos, así que al repetir la "
            "descarga no se reconocerá y puede guardarse otra copia."
        ),
        "not_converted": "{}: no se pudo convertir a {}; se conserva tal como se descargó.",
        "no_cover": "{}: guardado sin imagen de portada.",
        "failed": "Falló la descarga: {}",
        "output_unusable": "No se puede guardar en {}: {}. Elige otra carpeta con -o.",
        "unexpected_error": "Error: {}",
        "unexpected_bug": (
            "Error inesperado ({}). Puede ser un fallo: si actualizar yt-dlp no lo arregla, "
            "repórtalo con la salida de arriba en "
            "https://github.com/edicsonabel/download-media/issues"
        ),
        "cookies_retry": "Falló con las cookies de {}. Reintentando sin cookies...",
        "cookies_bad_spec": "valor inválido {!r}: usa NAVEGADOR[+KEYRING] (navegadores: {}; keyrings: {})",
        "cookies_keyring_hint": "  → si {0} guarda sus claves en otro keyring, indícalo: --cookies {0}+gnomekeyring (o +kwallet6, +basictext)",
        "cookies_no_browser": "No se encontraron cookies de {0}: ¿está {0} instalado y se ha abierto al menos una vez? Se continúa sin cookies.",
        "cookies_unreadable": "No se pudieron leer las cookies de {0} ({1}). Se continúa sin cookies.",
        "cookies_none_decrypted": "no se pudo descifrar ninguna",
        "item_no_media": "{}: nada que descargar, se omite",
        "item_image_skipped": "{}: es una imagen, se omite",
        "image_not_saved": "{}: no se pudo descargar la imagen",
        "partial_result": "{} de {} items descargados ({} sin medio descargable, {} con error).",
        "subs_none": "No se pudo guardar ningún subtítulo en {}; el contenido se guardó sin ellos.",
        "subs_failed": '{0}: no se pudieron descargar los subtítulos "{1}" ({2}).',
        "subs_empty": (
            "{0}: el archivo de subtítulos no contiene texto; se conserva, pero no se incrusta."
        ),
        "subs_not_embedded": (
            "{}: uno o más subtítulos no se pudieron incrustar; todos se conservan como archivos aparte."
        ),
        "equiv_cmd": "Comando equivalente para repetir esta descarga:",
        "yes_chars": "ys",
        "detected": "Detectado",
        "site_label": "Sitio",
        "type_label": "Tipo",
        "kind_video": "Video",
        "kind_audio": "Audio",
        "kind_image": "Imagen",
        "kind_playlist": "Carrusel/Playlist",
        "kind_unknown": "Desconocido",
        "audio_may_hold_video": "Marcado como audio, pero el archivo puede tener video también: si eliges video, se guarda tal cual.",
        "items_count": "{} items: {} descargables, {} no descargables",
        "items_count_simple": "{} items",
        "auth_needed_pre": "Este sitio requiere cookies del navegador para funcionar.",
        "auth_needed_retry": "Este contenido requiere iniciar sesión: {}",
        "auth_still_needed": "Sigue sin haber acceso con las cookies de {}: comprueba que hayas iniciado sesión en ese navegador.",
        "image_alone_saved": "{}: yt-dlp no encontró video en esta URL, solo esta imagen, y la guardó.",
        "probe_partial": "Algunos items no se pudieron inspeccionar: {}",
        "probe_failed": "No se pudo inspeccionar la URL: {}",
        "probe_falling_back": "Continuando en modo manual (vas a definir todo a mano).",
        "image_only_skipped": "El contenido es una imagen y no es soportado por yt-dlp.",
        "no_supported_items": "Ningún item es descargable.",
        "selection_warning_unsupported": "Items no soportados en tu selección: {}. Se omitirán.",
        "ask_selection_default": "Selecciona items (Enter = todo lo descargable, 'all' = todo intentable, c = cancelar): ",
        "ask_pl_mode": "URL de playlist detectada. ¿Qué hacer?",
        "pl_single": "Solo este video",
        "pl_all": "Toda la playlist",
        "wizard_skipped": "Omitida, no hay nada que descargar: {}",
        "items_not_found": "No están en esta playlist ({} items), se omiten: {}",
        "ask_selection_default_preset": "Selecciona items (Enter = {}, 'all' = todo intentable, c = cancelar): ",
    },
    "en": {
        "wizard_title": "Download wizard",
        "ask_url": "Video URL",
        "empty_url": "Empty URL. Aborting.",
        "from_clipboard": "URL detected in clipboard:",
        "ask_use_clip": "Use URL from clipboard? [Y/n]: ",
        "ask_type": "What do you want to download?",
        "type_video": "Video (with audio)",
        "type_audio": "Audio only",
        "ask_choice": "Select [{}]: ",
        "ask_quality": "Max video quality",
        "ask_aformat": "Audio format",
        "ask_vformat": "Video container",
        "vformat_desc_mp4": "works on WhatsApp, iPhone, TV (H.264, up to 1080p)",
        "vformat_desc_mkv": "best quality, up to 4K (VP9/AV1)",
        "vformat_desc_webm": "open web format (VP9/AV1)",
        "mp4_h264_cap": "mp4 uses H.264, which usually tops out at 1080p. For {}p use --video-format mkv.",
        "ask_output": "Output folder [{}]: ",
        "ask_cookies": "Use browser cookies? (required for logged-in YouTube, private content)",
        "no_cookies": "No cookies",
        "back": "back",
        "cancel": "cancel",
        "summary": "Selections so far:",
        "invalid_retry": "Invalid option. Try again.",
        "url_required": "Missing URL.",
        "cancelled": "Cancelled.",
        "interrupted": "Interrupted while downloading {0}. Unfinished files may be left in {1}.",
        "probing": "Inspecting content...",
        "carousel_hdr": "Carousel/Playlist with {} items:",
        "item_video": "video",
        "item_image": "image",
        "item_other": "other",
        "ask_selection": "Pick items (e.g. 1-3; 1,2,4; Enter = all, c = cancel): ",
        "invalid_selection": "Invalid selection. Try again.",
        "selected": "Selected: {}",
        "fetching": "Fetching info...",
        "preview": "Preview",
        "p_title": "Title",
        "p_uploader": "Channel",
        "p_duration": "Duration",
        "p_size": "Approx. size",
        "ask_confirm": "Proceed with download? [Y/n]: ",
        "downloading": "Downloading...",
        "done": "Done.",
        "done_without_cookies": "Saved without {0} cookies: this is what the site shows without logging in.",
        "untagged": (
            "{}: saved, but ffmpeg couldn't write its metadata, so a rerun won't recognize it "
            "and may save another copy."
        ),
        "not_converted": "{}: couldn't be converted to {}; it's kept as downloaded.",
        "no_cover": "{}: saved without a cover image.",
        "failed": "Download failed: {}",
        "output_unusable": "Can't save to {}: {}. Choose another folder with -o.",
        "unexpected_error": "Error: {}",
        "unexpected_bug": (
            "Unexpected error ({}). It may be a bug: if updating yt-dlp doesn't fix it, "
            "report it with the output above at "
            "https://github.com/edicsonabel/download-media/issues"
        ),
        "cookies_retry": "Failed with {} cookies. Retrying without cookies...",
        "cookies_bad_spec": "invalid value {!r}: use BROWSER[+KEYRING] (browsers: {}; keyrings: {})",
        "cookies_keyring_hint": "  → if {0} stores its keys in another keyring, name it: --cookies {0}+gnomekeyring (or +kwallet6, +basictext)",
        "cookies_no_browser": "No {0} cookies found: is {0} installed, and has it been opened at least once? Going on without cookies.",
        "cookies_unreadable": "Could not read the {0} cookies ({1}). Going on without cookies.",
        "cookies_none_decrypted": "none of them could be decrypted",
        "item_no_media": "{}: nothing to download, skipping",
        "item_image_skipped": "{}: an image, skipping",
        "image_not_saved": "{}: couldn't download the image",
        "partial_result": "{} of {} items downloaded ({} without downloadable media, {} failed).",
        "subs_none": "No subtitles in {} could be saved; the media was saved without them.",
        "subs_failed": '{0}: couldn\'t download the "{1}" subtitles ({2}).',
        "subs_empty": "{0}: the subtitle file holds no text; it's kept, but not embedded.",
        "subs_not_embedded": (
            "{}: one or more subtitles couldn't be embedded; all of them are kept as separate files."
        ),
        "equiv_cmd": "Equivalent command to repeat this download:",
        "yes_chars": "y",
        "detected": "Detected",
        "site_label": "Site",
        "type_label": "Type",
        "kind_video": "Video",
        "kind_audio": "Audio",
        "kind_image": "Image",
        "kind_playlist": "Carousel/Playlist",
        "kind_unknown": "Unknown",
        "audio_may_hold_video": "Marked as audio, but the file may hold a video too: choosing video saves it as it is.",
        "items_count": "{} items: {} downloadable, {} not downloadable",
        "items_count_simple": "{} items",
        "auth_needed_pre": "This site requires browser cookies to work.",
        "auth_needed_retry": "This content needs a login: {}",
        "auth_still_needed": "Still no access with {} cookies: check that you are logged in on that browser.",
        "image_alone_saved": "{}: yt-dlp found no video at this URL, only this image, and saved it.",
        "probe_partial": "Some items could not be inspected: {}",
        "probe_failed": "Could not inspect URL: {}",
        "probe_falling_back": "Continuing in manual mode (you'll choose everything by hand).",
        "image_only_skipped": "This content is a standalone image, not supported by yt-dlp.",
        "no_supported_items": "No items are downloadable.",
        "selection_warning_unsupported": "Items not supported in your selection: {}. They will be skipped.",
        "ask_selection_default": "Pick items (Enter = everything downloadable, 'all' = try everything, c = cancel): ",
        "ask_pl_mode": "Playlist URL detected. What to do?",
        "pl_single": "Just this video",
        "pl_all": "Whole playlist",
        "wizard_skipped": "Skipped, nothing to download: {}",
        "items_not_found": "Not in this playlist ({} items), skipped: {}",
        "ask_selection_default_preset": "Pick items (Enter = {}, 'all' = try everything, c = cancel): ",
    },
}


def t(key: str, *args: object) -> str:
    """Return localized message for ``key``, formatted with ``args`` if given."""
    msg = _MESSAGES[LANG].get(key) or _MESSAGES["en"].get(key) or key
    return msg.format(*args) if args else msg


def is_yes(reply: str) -> bool:
    """Whether a reply means "yes" in the active language (case-insensitive)."""
    if not reply:
        return False
    return reply[0].lower() in _MESSAGES[LANG]["yes_chars"] + "y"
