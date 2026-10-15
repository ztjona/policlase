from django.urls import path, re_path

from . import views

T = "docente/cursos/<int:course_pk>/actividades/"
S = "cursos/<int:course_pk>/actividades/<int:pk>/"

urlpatterns = [
    # docente
    # La pestaña Evaluaciones: las fechas clave (pruebas y exámenes) y el vencimiento de las actividades.
    path("docente/cursos/<int:course_pk>/evaluaciones/", views.dates_panel, name="activity_dates"),
    path(T + "<int:pk>/", views.activity_detail, name="activity_detail"),
    path(T + "<int:pk>/mover/", views.activity_move, name="activity_move"),
    path(T + "<int:pk>/eliminar/", views.activity_delete, name="activity_delete"),
    path(T + "<int:pk>/partes/", views.activity_parts, name="activity_parts"),
    path(T + "<int:pk>/presentar/", views.activity_present, name="activity_present"),
    path(T + "<int:pk>/presentar/accion/", views.activity_present_action, name="activity_present_action"),
    path(T + "<int:pk>/intentos/<int:attempt_pk>/", views.attempt_review, name="attempt_review"),
    path(T + "<int:pk>/intentos/<int:attempt_pk>/comentario/", views.attempt_feedback, name="attempt_feedback"),
    path(T + "<int:pk>/revisar/<str:key>/", views.question_review, name="question_review"),
    path(T + "<int:pk>/respuestas/<int:answer_pk>/nota/", views.answer_grade, name="activity_answer_grade"),
    # estudiante
    path(S, views.activity_student, name="activity_student"),
    path(S + "comenzar/", views.attempt_start, name="attempt_start"),
    path(S + "intento/", views.attempt_form, name="attempt_form"),
    path(S + "intento/imagenes/", views.attempt_upload, name="attempt_upload"),
    path(S + "intento/imagenes/<int:upload_pk>/quitar/", views.attempt_upload_remove, name="attempt_upload_remove"),
    path(S + "intento/descartar/", views.attempt_discard, name="attempt_discard"),
    path(S + "intento/presentacion/", views.attempt_pointer, name="attempt_pointer"),
    path(S + "intento/tiempo/<str:key>/<str:action>/", views.attempt_timer, name="attempt_timer"),
    # Hasta 0.13.0, el autoguardado iba aquí por un error de activity.js. Las páginas que siguen
    # abiertas con esa versión guardan de nuevo sin recargar. Retirar después del 2026-11-01.
    re_path(r"^cursos/(?P<course_pk>\d+)/actividades/(?P<pk>\d+)/intento/\[object [A-Za-z]+\]$",
            views.attempt_form),
    # ambos
    path("entregas/<int:upload_pk>/", views.upload_file, name="upload_file"),
]
