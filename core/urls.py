from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import DashboardLoginView, home, map_data

urlpatterns = [
    path('login/', DashboardLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('', home, name='home'),
    path('api/map-data/', map_data, name='map-data'),
]
