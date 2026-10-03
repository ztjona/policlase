from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("unirse/", views.join, name="course_join"),
    path("inscripciones/<int:pk>/ocultar/", views.enrollment_dismiss, name="enrollment_dismiss"),
    path("cursos/<int:pk>/", views.course_detail, name="course_detail"),
    path("docente/cursos/nuevo/", views.course_create, name="course_create"),
    path("docente/cursos/<int:pk>/", views.course_manage, name="course_manage"),
    path("docente/cursos/<int:pk>/configuracion/", views.course_settings, name="course_settings"),
    path("docente/cursos/<int:pk>/inscripciones/", views.enrollment_decide, name="enrollment_decide"),
    path("docente/cursos/<int:pk>/inscripciones/<int:enrollment_pk>/retirar/",
         views.enrollment_remove, name="enrollment_remove"),
    path("docente/cursos/<int:pk>/codigo/", views.join_code_action, name="join_code_action"),
]
