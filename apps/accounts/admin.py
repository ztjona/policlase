from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class PoliclaseUserAdmin(UserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "student_id", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name", "student_id")
    fieldsets = UserAdmin.fieldsets + (("policlase", {"fields": ("role", "student_id")}),)
