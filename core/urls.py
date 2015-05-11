from django.conf.urls import patterns, url
from rest_framework.urlpatterns import format_suffix_patterns
from core import views

urlpatterns = [
    url(r'$^', views.api_root, name='api-root'),
    url(r'^accounts/$', views.AccountList.as_view(), name='account-list'),
    url(r'^accounts/(?P<pk>[0-9]+)/$', views.AccountDetail.as_view(), name='account-detail'),
    url(r'^notifications/$', views.NotificationList.as_view(), name='notification-list'),
    url(r'^albums/$', views.AlbumList.as_view(), name='album-list'),
    url(r'^albums/(?P<pk>[0-9]+)/$', views.AlbumDetail.as_view(), name='album-detail'),
    url(r'^albums/(?P<pk>[0-9]+)/files/$', views.AlbumFilesList.as_view(), name='albumfiles-list'),
    url(r'^albumfile/(?P<pk>[0-9]+)/$', views.AlbumFileDetail.as_view(), name='albumfile-detail'),
    url(r'^events/$', views.EventList.as_view(), name='event-list'),
    url(r'^events/(?P<pk>[0-9]+)/$', views.EventDetail.as_view(), name='event-detail'),
    url(r'^events/(?P<pk>[0-9]+)/guests/$', views.EventGuestList.as_view(), name='eventguest-list'),
    url(r'^events/(?P<event_id>[0-9]+)/guests/(?P<guest_id>[0-9]+)/$', views.EventGuestDetail.as_view(), name='eventguest-detail'),
]

urlpatterns = format_suffix_patterns(urlpatterns)
