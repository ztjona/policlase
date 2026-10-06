from django.urls import path

from . import views
from .guests import guest_entry

urlpatterns = [
    # presentaciones
    path("docente/cursos/<int:course_pk>/presentaciones/nueva/", views.deck_edit, name="deck_new"),
    path("docente/cursos/<int:course_pk>/presentaciones/vista-previa/", views.deck_preview, name="deck_preview"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:pk>/", views.deck_view, name="deck_view"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:pk>/editar/", views.deck_edit, name="deck_edit"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:pk>/eliminar/", views.deck_delete, name="deck_delete"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:deck_pk>/iniciar/",
         views.session_start, name="session_start"),
    # docente en clase
    path("docente/en-vivo/<int:pk>/", views.present, name="present"),
    path("docente/en-vivo/<int:pk>/escena/", views.present_fragment, name="present_fragment"),
    path("docente/en-vivo/<int:pk>/accion/", views.present_action, name="present_action"),
    path("docente/en-vivo/<int:pk>/resultados/", views.session_results, name="session_results"),
    # estudiante en clase
    path("en-vivo/", views.join_by_pin, name="live_join"),
    path("en-vivo/abierta/<str:token>/", guest_entry, name="live_guest"),
    path("en-vivo/<int:pk>/", views.live_student, name="live_student"),
    path("en-vivo/<int:pk>/escena/", views.student_fragment, name="student_fragment"),
    path("en-vivo/<int:pk>/responder/", views.student_answer, name="student_answer"),
    # ambos
    path("en-vivo/<int:pk>/eventos/", views.events, name="live_events"),
]
