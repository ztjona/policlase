from django.contrib import admin

from .models import Deck, LiveSession, Response


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "updated_at")


@admin.register(LiveSession)
class LiveSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "pin", "status", "index", "phase", "created_at")
    list_filter = ("status",)


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ("session", "slide_index", "student", "correct", "points", "elapsed_ms")
