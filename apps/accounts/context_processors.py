from django.conf import settings

THEMES = ("light", "dark")


def theme_of(request) -> str:
    """'light', 'dark' o '' (automático: sigue al sistema). La cuenta manda sobre la cookie."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.theme:
        return user.theme
    value = request.COOKIES.get(settings.THEME_COOKIE, "")
    return value if value in THEMES else ""


def preferences(request) -> dict:
    return {"theme": theme_of(request), "policlase_version": settings.POLICLASE_VERSION}
