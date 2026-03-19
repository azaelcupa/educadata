from django.urls import path

from .views import home, map_data

urlpatterns = [
    path('', home, name='home'),
    path('api/map-data/', map_data, name='map-data'),
]
