from django.urls import path

from . import debate_views, views

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
    path(T + "<int:pk>/prorrogas/", views.activity_extension, name="activity_extension"),
    path(T + "<int:pk>/codigo/", views.evaluation_code, name="evaluation_code"),
    path(T + "<int:pk>/codigo/proyectar/", views.evaluation_projector, name="evaluation_projector"),
    path(T + "<int:pk>/publicar/", views.evaluation_publish, name="evaluation_publish"),
    path(T + "<int:pk>/presentar/", views.activity_present, name="activity_present"),
    path(T + "<int:pk>/presentar/accion/", views.activity_present_action, name="activity_present_action"),
    path(T + "<int:pk>/intentos/<int:attempt_pk>/", views.attempt_review, name="attempt_review"),
    path(T + "<int:pk>/intentos/<int:attempt_pk>/comentario/", views.attempt_feedback, name="attempt_feedback"),
    path(T + "<int:pk>/revisar/<str:key>/", views.question_review, name="question_review"),
    path(T + "<int:pk>/respuestas/<int:answer_pk>/nota/", views.answer_grade, name="activity_answer_grade"),
    path(T + "<int:pk>/debate/", debate_views.debate_teacher, name="debate_teacher"),
    path(T + "<int:pk>/debate/accion/", debate_views.debate_teacher_action, name="debate_teacher_action"),
    # estudiante
    path(S, views.activity_student, name="activity_student"),
    path(S + "comenzar/", views.attempt_start, name="attempt_start"),
    path(S + "intento/", views.attempt_form, name="attempt_form"),
    path(S + "intento/imagenes/", views.attempt_upload, name="attempt_upload"),
    path(S + "intento/imagenes/<int:upload_pk>/quitar/", views.attempt_upload_remove, name="attempt_upload_remove"),
    path(S + "intento/descartar/", views.attempt_discard, name="attempt_discard"),
    path(S + "intento/presentacion/", views.attempt_pointer, name="attempt_pointer"),
    path(S + "intento/tiempo/<str:key>/<str:action>/", views.attempt_timer, name="attempt_timer"),
    path(S + "intento/comprobar/<str:key>/", views.attempt_check, name="attempt_check"),
    path(S + "debate/revisar/", debate_views.debate_revise, name="debate_revise"),
    path(S + "debate/comentar/", debate_views.debate_reply, name="debate_reply"),
    path(S + "debate/comentarios/<int:reply_pk>/eliminar/", debate_views.debate_reply_delete, name="debate_reply_delete"),
    path(S + "debate/me-gusta/", debate_views.debate_like, name="debate_like"),
    # ambos
    path("entregas/<int:upload_pk>/", views.upload_file, name="upload_file"),
    path("vista-previa/", views.student_preview, name="student_preview"),
]
