"""Our stand-in for the hidden target-size catalogue.

RULES.md says scoring targets come from a fixed catalogue of real platform
formats, but the catalogue itself is not published. These are the common
sizes those platforms use.
"""

FORMATS: dict[str, tuple[int, int]] = {
    "instagram_post": (1080, 1080),
    "instagram_portrait": (1080, 1350),
    "instagram_story": (1080, 1920),
    "youtube_thumbnail": (1280, 720),
    "facebook_link": (1200, 628),
    "x_post": (1600, 900),
    "pinterest_pin": (1000, 1500),
    "leaderboard_ad": (728, 90),
    "skyscraper_ad": (160, 600),
    "medium_rectangle_ad": (300, 250),
    "billboard_ad": (970, 250),
    "web_banner": (1600, 400),
}
