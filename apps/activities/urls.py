from django.urls import path

from . import views

T = "docente/cursos/<int:course_pk>/actividades/"
S = "cursos/<int:course_pk>/actividades/<int:pk>/"

urlpatterns = [
    # docente
    path(T + "fechas/", views.dates_panel, name="activity_dates"),
    path(T + "<int:pk>/", views.activity_detail, name="activity_detail"),
    path(T + "<int:pk>/mover/", views.activity_move, name="activity_move"),
    path(T + "<int:pk>/eliminar/", views.activity_delete, name="activity_delete"),
    path(T + "<int:pk>/intentos/<int:attempt_pk>/", views.attempt_review, name="attempt_review"),
    path(T + "<int:pk>/revisar/<str:key>/", views.question_review, name="question_review"),
    path(T + "<int:pk>/respuestas/<int:answer_pk>/nota/", views.answer_grade, name="activity_answer_grade"),
    # estudiante
    path(S, views.activity_student, name="activity_student"),
    path(S + "comenzar/", views.attempt_start, name="attempt_start"),
    path(S + "intento/", views.attempt_form, name="attempt_form"),
    path(S + "intento/imagenes/", views.attempt_upload, name="attempt_upload"),
    path(S + "intento/imagenes/<int:upload_pk>/quitar/", views.attempt_upload_remove, name="attempt_upload_remove"),
    path(S + "intento/descartar/", views.attempt_discard, name="attempt_discard"),
    # ambos
    path("entregas/<int:upload_pk>/", views.upload_file, name="upload_file"),
]
