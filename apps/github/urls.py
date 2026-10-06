from django.urls import path

from . import views

urlpatterns = [
    path("cuenta/github/", views.account, name="github_account"),
    path("docente/cursos/<int:pk>/github/", views.course_link, name="github_course_link"),
    path("github/webhook/<int:pk>/", views.webhook, name="github_webhook"),
]
