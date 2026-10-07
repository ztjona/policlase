from django.urls import path

from . import sections, views
from .guests import guest_entry

urlpatterns = [
    # presentaciones
    path("docente/cursos/<int:course_pk>/presentaciones/nueva/", views.deck_edit, name="deck_new"),
    path("docente/cursos/<int:course_pk>/presentaciones/vista-previa/", views.deck_preview, name="deck_preview"),
    path("docente/cursos/<int:course_pk>/presentaciones/panel/", views.deck_ops, name="deck_ops"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:pk>/", views.deck_view, name="deck_view"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:pk>/editar/", views.deck_edit, name="deck_edit"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:pk>/eliminar/", views.deck_delete, name="deck_delete"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:pk>/mover/", views.deck_move, name="deck_move"),
    path("docente/cursos/<int:course_pk>/presentaciones/<int:deck_pk>/iniciar/",
         views.session_start, name="session_start"),
    # secciones (unidades)
    path("docente/cursos/<int:course_pk>/secciones/nueva/", sections.section_create, name="section_create"),
    path("docente/cursos/<int:course_pk>/secciones/<int:pk>/renombrar/", sections.section_rename,
         name="section_rename"),
    path("docente/cursos/<int:course_pk>/secciones/<int:pk>/eliminar/", sections.section_delete,
         name="section_delete"),
    path("docente/cursos/<int:course_pk>/secciones/<int:pk>/minimizar/", sections.section_toggle,
         name="section_toggle"),
    # docente en clase
    path("docente/en-vivo/<int:pk>/", views.present, name="present"),
    path("docente/en-vivo/<int:pk>/escena/", views.present_fragment, name="present_fragment"),
    path("docente/en-vivo/<int:pk>/accion/", views.present_action, name="present_action"),
    path("docente/en-vivo/<int:pk>/resultados/", views.session_results, name="session_results"),
    path("docente/en-vivo/<int:pk>/eliminar/", views.session_delete, name="session_delete"),
    path("docente/en-vivo/<int:pk>/respuestas/<int:response_pk>/nota/", views.response_grade, name="response_grade"),
    path("docente/en-vivo/<int:pk>/preguntas/<int:index>/calificar/", views.answer_grade, name="answer_grade"),
    # estudiante en clase
    path("en-vivo/", views.join_by_pin, name="live_join"),
    path("en-vivo/abierta/<str:token>/", guest_entry, name="live_guest"),
    path("en-vivo/<int:pk>/", views.live_student, name="live_student"),
    path("en-vivo/<int:pk>/escena/", views.student_fragment, name="student_fragment"),
    path("en-vivo/<int:pk>/responder/", views.student_answer, name="student_answer"),
    path("en-vivo/<int:pk>/retroalimentacion/", views.student_feedback, name="student_feedback"),
    # ambos
    path("en-vivo/<int:pk>/eventos/", views.events, name="live_events"),
]
