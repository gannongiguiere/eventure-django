from django.conf.urls import include, url
from django.contrib.gis import admin

urlpatterns = [
    # Examples:
    # url(r'^$', 'evtidj.views.home', name='home'),
    # url(r'^blog/', include('blog.urls')),

    url(r'^admin/', include(admin.site.urls)),
    url(r'^api/', include('core.urls')),
    url(r'^api-auth/', include('rest_framework.urls', namespace='rest_framework')),
    url(r'^e/', include('fe.urls', namespace='fe')),
]
