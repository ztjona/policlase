from django.urls import path

from . import views

T = "docente/cursos/<int:course_pk>/actividades/<int:pk>/ia/"

urlpatterns = [
    path("cuenta/ia/", views.ai_account, name="ai_account"),
    path(T + "pendientes/", views.ai_pending, name="ai_pending"),
    path(T + "proponer/<int:answer_pk>/", views.ai_propose, name="ai_propose"),
]
