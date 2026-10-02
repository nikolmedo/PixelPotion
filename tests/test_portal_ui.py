"""Markup contracts of the web portal: shared layout, static assets, offline use.

The portal is opened from a phone joined to the Pi's own access point, which
has no internet, so every asset must be served by the Pi itself.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import app as pixelpotion
from conftest import REPO_ROOT, make_jpeg_bytes

PAGES = {"/": "Capture", "/styles": "Styles", "/gallery": "Gallery"}
STATIC_DIR = REPO_ROOT / "static"


class PageAudit(HTMLParser):
    """Collect the structural facts the layout tests assert on."""

    def __init__(self):
        super().__init__()
        self.tags = []            # (tag, attrs) in document order
        self.current_nav = []     # hrefs of nav links marked aria-current
        self._in_nav = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "nav":
            self._in_nav = True
        elif tag == "a" and self._in_nav and attrs.get("aria-current") == "page":
            self.current_nav.append(attrs.get("href"))

    def handle_endtag(self, tag):
        if tag == "nav":
            self._in_nav = False

    def find(self, tag, **wanted):
        return [a for t, a in self.tags
                if t == tag and all(a.get(k) == v for k, v in wanted.items())]


def audit(client, page) -> PageAudit:
    parser = PageAudit()
    parser.feed(client.get(page).get_data(as_text=True))
    return parser


@pytest.fixture
def full_pages(monkeypatch, isolated_state):
    """Render every conditional block: WiFi up and one pending photo."""
    monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
    (isolated_state.pending / "photo_20260609_201500.jpg").write_bytes(make_jpeg_bytes())


class TestSharedLayout:
    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_renders_the_shared_layout(self, client, page):
        # Act
        response = client.get(page)
        parsed = PageAudit()
        parsed.feed(response.get_data(as_text=True))

        # Assert
        assert response.status_code == 200
        assert parsed.find("html", lang="en")
        assert parsed.find("meta", name="viewport")
        assert parsed.find("meta", name="color-scheme")
        for landmark in ("header", "nav", "main", "footer"):
            assert parsed.find(landmark), f"{page} has no <{landmark}>"
        assert parsed.find("link", rel="stylesheet", href="/static/app.css")
        assert parsed.find("script", src="/static/app.js")

    @pytest.mark.parametrize("page", PAGES)
    def test_only_the_current_page_is_marked_in_the_navigation(self, client, page):
        # Act / Assert
        assert audit(client, page).current_nav == [page]

    @pytest.mark.parametrize("page", PAGES)
    def test_pending_badge_is_rendered_on_every_page(
        self, client, isolated_state, page
    ):
        # Arrange
        (isolated_state.pending / "photo_20260609_201500.jpg").write_bytes(
            make_jpeg_bytes()
        )

        # Act
        body = client.get(page).get_data(as_text=True)

        # Assert
        assert re.search(r'id="pendingBadge"[^>]*>1</span>', body)

    def test_empty_queue_keeps_the_badge_hidden(self, client):
        # Act
        badge = audit(client, "/").find("span", id="pendingBadge")

        # Assert — kept in the DOM so live updates can reveal it.
        assert len(badge) == 1
        assert "hidden" in badge[0]

    def test_footer_shows_the_installed_version(self, client):
        # Arrange
        version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

        # Act
        body = client.get("/").get_data(as_text=True)

        # Assert
        assert re.search(rf"<footer.*{re.escape(version)}.*</footer>", body, re.S)


class TestStaticAssets:
    def test_flask_serves_the_repository_static_directory(self):
        # Act / Assert
        assert Path(pixelpotion.app.static_folder) == STATIC_DIR

    @pytest.mark.parametrize(
        "name", sorted(p.name for p in STATIC_DIR.iterdir() if p.is_file())
    )
    def test_every_static_file_is_served(self, client, name):
        # Act
        response = client.get(f"/static/{name}")

        # Assert
        assert response.status_code == 200
        assert response.data == (STATIC_DIR / name).read_bytes()

    def test_static_assets_load_nothing_from_the_internet(self):
        # Act / Assert — the access point has no internet connection.
        for path in STATIC_DIR.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", text, re.I), \
                    path.name

    @pytest.mark.parametrize("page", PAGES)
    def test_pages_reference_only_local_scripts_and_styles(
        self, client, full_pages, page
    ):
        # Act
        parsed = audit(client, page)

        # Assert
        sources = [a.get("src") for a in parsed.find("script")]
        sources += [a.get("href") for a in parsed.find("link")]
        sources += [a["src"] for a in parsed.find("img") if "src" in a]
        assert all(s and s.startswith("/") and not s.startswith("//") for s in sources)

    @pytest.mark.parametrize("page", PAGES)
    def test_pages_have_no_inline_event_handlers(self, client, full_pages, page):
        # Act
        parsed = audit(client, page)

        # Assert — handlers live in static/*.js and read data- attributes.
        for tag, attrs in parsed.tags:
            assert not [k for k in attrs if k.startswith("on")], (tag, attrs)
