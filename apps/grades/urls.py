from django.urls import path

from . import views

urlpatterns = [
    path("docente/cursos/<int:pk>/ponderaciones/", views.grading_settings, name="grading_settings"),
    path("docente/cursos/<int:pk>/ponderaciones/parciales/nuevo/", views.period_add, name="period_add"),
    path("docente/cursos/<int:pk>/ponderaciones/parciales/<int:period_pk>/eliminar/", views.period_delete,
         name="period_delete"),
    path("docente/cursos/<int:pk>/calificaciones/", views.course_gradebook, name="course_gradebook"),
    path("cursos/<int:pk>/calificaciones/", views.my_grades, name="my_grades"),
]
