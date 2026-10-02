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


class TestSetupFlow:
    @pytest.fixture
    def fresh_device(self, monkeypatch):
        """A first boot: no keys, no Telegram, still in access-point mode."""
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: False)
        for key in ("gemini_api_key", "telegram_bot_token", "telegram_chat_id"):
            pixelpotion.config[key] = ""

    def test_checklist_guides_a_fresh_device_to_each_form_section(
        self, client, fresh_device
    ):
        # Act
        body = client.get("/").get_data(as_text=True)
        parsed = PageAudit()
        parsed.feed(body)

        # Assert
        assert "Get started" in body
        for anchor in ("#gemini", "#telegram", "#wifi"):
            assert parsed.find("a", href=anchor), anchor
            assert parsed.find("fieldset", id=anchor[1:]) or parsed.find("div", id=anchor[1:])
        assert body.count("To do:") == 3
        assert "/newbot" in body
        assert "mobile data" in body

    def test_checklist_marks_finished_steps(self, client, monkeypatch):
        # Arrange — keys saved, but the camera is still in access-point mode.
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: False)

        # Act
        body = client.get("/").get_data(as_text=True)

        # Assert
        assert body.count("Done:") == 2
        assert body.count("To do:") == 1

    def test_checklist_disappears_once_setup_is_complete(self, client, monkeypatch):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)

        # Act
        body = client.get("/").get_data(as_text=True)

        # Assert
        assert "Get started" not in body

    def test_wifi_settings_come_after_the_keys(self, client):
        # Act
        body = client.get("/").get_data(as_text=True)

        # Assert
        assert body.index('action="/save_config"') < body.index('action="/save_wifi"')

    def test_wifi_form_explains_where_to_reconnect(self, client):
        # Arrange
        pixelpotion.config["ap_ssid"] = "PixelPotion-Kitchen"

        # Act
        body = client.get("/").get_data(as_text=True)
        notice = audit(client, "/").find("div", id="wifiNotice")

        # Assert — shown by script before the first submit, never as alert().
        assert notice and "hidden" in notice[0]
        assert "Your phone will disconnect from" in body
        assert "PixelPotion-Kitchen" in body
        assert "http://pixelpotion.local:8080" in body
        assert "alert(" not in (STATIC_DIR / "index.js").read_text(encoding="utf-8")

    def test_every_secret_field_has_a_show_hide_toggle(self, client):
        # Act
        parsed = audit(client, "/")

        # Assert
        secret_ids = {a["id"] for a in parsed.find("input", type="password")}
        toggles = parsed.find("button", **{"class": "toggle-secret"})
        assert secret_ids == {"geminiKey", "teleToken", "wifiPass"}
        assert {t["data-target"] for t in toggles} == secret_ids
        for toggle in toggles:
            assert toggle["aria-pressed"] == "false"
            assert toggle["aria-label"].startswith("Show ")

    @pytest.mark.parametrize("field", [
        "wifi_ssid", "wifi_password", "gemini_api_key",
        "telegram_bot_token", "telegram_chat_id",
    ])
    def test_credential_fields_disable_phone_autocorrect(self, client, field):
        # Act
        (attrs,) = audit(client, "/").find("input", name=field)

        # Assert
        assert attrs["autocapitalize"] == "none"
        assert attrs["spellcheck"] == "false"
        assert attrs["autocomplete"] in ("off", "new-password")


class A11yAudit(HTMLParser):
    """Form controls, their labels, and the visible text of every button."""

    def __init__(self):
        super().__init__()
        self.controls = []        # attrs of input/select/textarea
        self.label_for = set()    # ids referenced by <label for>
        self.label_depth = 0
        self.wrapped = []         # controls nested inside a <label>
        self.buttons = []         # (attrs, visible text)
        self._button = None
        self.elements = []        # (tag, attrs) of every element

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "label":
            self.label_depth += 1
            if attrs.get("for"):
                self.label_for.add(attrs["for"])
        elif tag in ("input", "select", "textarea") and attrs.get("type") != "hidden":
            self.controls.append(attrs)
            if self.label_depth:
                self.wrapped.append(attrs)
        elif tag == "button":
            self._button = [attrs, ""]
            self.buttons.append(self._button)

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth -= 1
        elif tag == "button":
            self._button = None

    def handle_data(self, data):
        if self._button is not None:
            self._button[1] += data.strip()


def a11y(client, page) -> A11yAudit:
    parser = A11yAudit()
    parser.feed(client.get(page).get_data(as_text=True))
    return parser


class TestAccessibility:
    @pytest.mark.parametrize("page", PAGES)
    def test_every_form_control_has_an_accessible_name(self, client, full_pages, page):
        # Act
        parsed = a11y(client, page)

        # Assert
        assert parsed.controls
        for attrs in parsed.controls:
            named = (attrs.get("id") in parsed.label_for or attrs.get("aria-label"))
            assert named, attrs

    @pytest.mark.parametrize("page", PAGES)
    def test_icon_only_buttons_have_an_aria_label(self, client, full_pages, page):
        # Act
        parsed = a11y(client, page)

        # Assert
        for attrs, text in parsed.buttons:
            if not text:
                assert attrs.get("aria-label"), attrs

    @pytest.mark.parametrize("page", PAGES)
    def test_status_messages_are_announced(self, client, page):
        # Act
        parsed = a11y(client, page)
        live = {a.get("id") for _, a in parsed.elements if a.get("aria-live") == "polite"}

        # Assert
        assert {"flashRegion", "toast"} <= live

    def test_capture_status_line_is_announced(self, client):
        # Act
        parsed = a11y(client, "/")
        (status,) = [a for _, a in parsed.elements if a.get("id") == "statusText"]

        # Assert
        assert status["aria-live"] == "polite"

    def test_style_choices_are_toggle_buttons(self, client):
        # Act
        parsed = a11y(client, "/")
        pills = [a for a, _ in parsed.buttons if "style-pill" in a.get("class", "")]

        # Assert — exactly the active style is pressed.
        assert [p["data-id"] for p in pills] == ["pixar", "anime", "watercolor"]
        assert [p["aria-pressed"] for p in pills] == ["true", "false", "false"]
        assert not [a for t, a in parsed.elements
                    if t == "div" and "style-pill" in a.get("class", "")]

    def test_style_cards_expand_from_a_button_that_reports_its_state(self, client):
        # Act
        parsed = a11y(client, "/styles")
        headers = [a for a, _ in parsed.buttons if "style-header" in a.get("class", "")]
        ids = {a.get("id") for _, a in parsed.elements}

        # Assert
        assert len(headers) == 3
        for header in headers:
            assert header["aria-expanded"] == "false"
            assert header["aria-controls"] in ids

    def test_gallery_preview_is_a_dialog_opened_by_buttons(
        self, client, full_pages
    ):
        # Act
        parsed = a11y(client, "/gallery")

        # Assert
        assert [t for t, _ in parsed.elements].count("dialog") == 1
        openers = [a for a, _ in parsed.buttons if "photo-open" in a.get("class", "")]
        assert [o["aria-label"] for o in openers] == ["Preview photo_20260609_201500.jpg"]

    def test_focus_and_touch_target_rules_exist(self):
        # Arrange
        css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")

        # Act / Assert — computed layout is checked in a real browser.
        assert ":focus-visible" in css
        assert re.search(r"--tap:\s*44px", css)
        for selector in (".btn {", ".style-pill {", ".nav a {", ".wifi-item {", ".style-header {"):
            rule = css[css.index(selector):].split("}", 1)[0]
            assert "min-height: var(--tap)" in rule, selector


def css_tokens() -> dict:
    """Hex colour tokens declared in the first :root block of app.css."""
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\}", css, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", root))


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(hex_colour):
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# Every text colour paired with each background it is drawn on.
TEXT_PAIRS = [
    ("text", "bg"), ("text", "surface"), ("text", "surface2"),
    ("text-dim", "bg"), ("text-dim", "surface"), ("text-dim", "surface2"),
    ("accent2", "surface"),
    ("on-accent", "accent"), ("on-success", "green"),
    ("on-danger", "red"), ("on-warning", "yellow"),
]


class TestColourContrast:
    @pytest.mark.parametrize("foreground, background", TEXT_PAIRS)
    def test_text_meets_wcag_aa(self, foreground, background):
        # Arrange
        tokens = css_tokens()

        # Act
        ratio = contrast_ratio(tokens[foreground], tokens[background])

        # Assert
        assert ratio >= 4.5, f"--{foreground} on --{background}: {ratio:.2f}"

    def test_contrast_maths_matches_the_wcag_reference(self):
        # Act / Assert — black on white is the 21:1 maximum.
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
